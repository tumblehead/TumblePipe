//! The project's config database, `_config/db/<purpose>.json`
//! (`tumblepipe.config.store.JsonConfigStore`).
//!
//! Each file is one tree of `{properties, children}` nodes. A node stores
//! only what was pinned on it; [`Config::get_properties`] resolves an
//! entity's properties the way Python does: its schema's defaults (the
//! schema found by the entity's position, [`Config::get_entity_schema_uri`]),
//! then every node's own properties from the root down, deep-merged, the
//! child winning.
//!
//! A `Config` reads each file once, when first asked, and keeps what it
//! read: it is a snapshot. Make a new one to see later writes. Its own
//! writes re-read the file first and update the snapshot.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{Map, Value};
use thiserror::Error;

use crate::io::{load_json, store_json};
use crate::naming::is_valid_entity_name;
use crate::uri::Uri;

pub type Properties = Map<String, Value>;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("{0}")]
    Read(String),
    #[error("could not write {file}: {reason}")]
    Write { file: String, reason: String },
    #[error("Entity already exists")]
    Exists,
    #[error("Entity does not exist")]
    Missing,
    #[error(
        "Entity name '{wanted}' collides with existing '{existing}' (names may not differ only by case — case-variant hierarchies split exports and sidecars)"
    )]
    CaseCollision { wanted: String, existing: String },
    #[error("Name '{0}' may only use letters and digits")]
    BadName(String),
    #[error("A purpose root cannot be renamed")]
    RootRename,
    #[error("Entity '{0}' already exists")]
    NameTaken(String),
    #[error("Entity name '{wanted}' collides with existing '{existing}' (names may not differ only by case)")]
    RenameCaseCollision { wanted: String, existing: String },
}

/// The entity kinds whose names follow [`is_valid_entity_name`]
/// (`store._NAMED_KINDS`).
pub const NAMED_KINDS: [&str; 3] = ["assets", "shots", "edits"];

/// Python's `util.data.deep_merge`: `over` onto `base`, nested objects
/// merged, anything else replaced.
pub fn deep_merge(base: &mut Properties, over: &Properties) {
    for (key, value) in over {
        match (base.get_mut(key), value) {
            (Some(Value::Object(mine)), Value::Object(theirs)) => deep_merge(mine, theirs),
            _ => {
                base.insert(key.clone(), value.clone());
            }
        }
    }
}

fn children(node: &Value) -> Option<&Map<String, Value>> {
    node.get("children").and_then(Value::as_object)
}

fn own_properties(node: &Value) -> Properties {
    node.get("properties").and_then(Value::as_object).cloned().unwrap_or_default()
}

fn lookup<'a>(mut node: &'a Value, segments: &[String]) -> Option<&'a Value> {
    for step in segments {
        node = children(node)?.get(step)?;
    }
    Some(node)
}

/// The first (wanted, existing) pair along `path` that differ only by case
/// (`store._find_case_collision`).
pub fn find_case_collision(mut node: &Value, path: &[String]) -> Option<(String, String)> {
    for step in path {
        let children = children(node)?;
        match children.get(step) {
            Some(child) => node = child,
            None => {
                let folded = step.to_lowercase();
                return children.keys().find(|existing| existing.to_lowercase() == folded).map(|existing| (step.clone(), existing.clone()));
            }
        }
    }
    None
}

/// Why a node a write to `path` creates is misnamed (`store._new_name_problem`):
/// only the categories, sequences, assets and shots it creates are checked.
fn new_name_problem(purpose: &str, mut node: &Value, path: &[String]) -> Option<ConfigError> {
    if purpose != "entity" || path.len() < 2 || !NAMED_KINDS.contains(&path[0].as_str()) {
        return None;
    }
    let mut depth = 0;
    for step in path {
        match children(node).and_then(|c| c.get(step)) {
            Some(child) => {
                node = child;
                depth += 1;
            }
            None => break,
        }
    }
    path[depth.max(1)..].iter().find(|name| !is_valid_entity_name(name)).map(|name| ConfigError::BadName(name.clone()))
}

/// Store `properties` at `path`, creating the nodes on the way
/// (`store._insert`): verbatim, no per-node schema.
fn insert(mut node: &mut Value, properties: Properties, path: &[String]) {
    for step in path {
        let object = node.as_object_mut().expect("a config node is an object");
        let children = object.entry("children").or_insert_with(|| Value::Object(Map::new()));
        if !children.is_object() {
            *children = Value::Object(Map::new());
        }
        node =
            children.as_object_mut().unwrap().entry(step.clone()).or_insert_with(|| serde_json::json!({"properties": {}, "children": {}}));
    }
    node.as_object_mut().expect("a config node is an object").insert("properties".into(), Value::Object(properties));
}

/// The project's config database.
#[derive(Debug)]
pub struct Config {
    db_path: PathBuf,
    cache: Mutex<HashMap<String, Option<Arc<Value>>>>,
}

impl Config {
    /// The database of the config folder `config_path` (`<project>/_config`).
    pub fn new(config_path: &Path) -> Self {
        Config { db_path: config_path.join("db"), cache: Mutex::new(HashMap::new()) }
    }

    pub fn db_file(&self, purpose: &str) -> PathBuf {
        self.db_path.join(format!("{purpose}.json"))
    }

    fn read_disk(&self, purpose: &str) -> Result<Option<Value>, ConfigError> {
        let path = self.db_file(purpose);
        if !path.is_file() {
            return Ok(None);
        }
        load_json(&path).map(Some).map_err(ConfigError::Read)
    }

    /// `purpose`'s whole tree, None when it has no file.
    pub fn root(&self, purpose: &str) -> Result<Option<Arc<Value>>, ConfigError> {
        if let Some(hit) = self.cache.lock().unwrap().get(purpose) {
            return Ok(hit.clone());
        }
        let loaded = self.read_disk(purpose)?.map(Arc::new);
        self.cache.lock().unwrap().insert(purpose.to_string(), loaded.clone());
        Ok(loaded)
    }

    /// Persist `data` as `purpose`'s whole tree.
    fn write_root(&self, purpose: &str, data: Value) -> Result<(), ConfigError> {
        let file = self.db_file(purpose);
        store_json(&file, &data).map_err(|e| ConfigError::Write { file: file.display().to_string(), reason: e.to_string() })?;
        self.cache.lock().unwrap().insert(purpose.to_string(), Some(Arc::new(data)));
        // This machine's open Asset Browsers hear of it and refresh, as
        // they do for the Python config store's writes (`crate::events`).
        if let Some(config_path) = self.db_path.parent() {
            crate::events::announce(config_path, crate::events::CONFIG, "", purpose);
        }
        Ok(())
    }

    /// The schema an entity's position gives it (`get_entity_schema_uri`):
    /// walking the schema tree beside the entity's segments, a segment that
    /// names a schema child is taken as it is, else the level's single
    /// (placeholder) child; None when the tree does not cover the path.
    pub fn get_entity_schema_uri(&self, entity: &Uri) -> Result<Option<Uri>, ConfigError> {
        let Some(schemas) = self.root("schemas")? else { return Ok(None) };
        let Some(mut node) = children(&schemas).and_then(|c| c.get(&entity.purpose)) else { return Ok(None) };
        let mut segments = vec![entity.purpose.clone()];
        for segment in &entity.segments {
            let Some(children) = children(node) else { return Ok(None) };
            let chosen = if children.contains_key(segment) {
                segment.clone()
            } else if children.len() == 1 {
                children.keys().next().unwrap().clone()
            } else {
                return Ok(None);
            };
            node = &children[&chosen];
            segments.push(chosen);
        }
        Ok(Some(Uri::new("schemas", segments)))
    }

    /// The children of a schema node (`get_child_schemas`), by name.
    pub fn child_schema_names(&self, schema: &Uri) -> Result<Vec<String>, ConfigError> {
        let Some(schemas) = self.root("schemas")? else { return Ok(Vec::new()) };
        Ok(lookup(&schemas, &schema.segments).and_then(children).map(|c| c.keys().cloned().collect()).unwrap_or_default())
    }

    /// An actual asset or shot, not a category or sequence
    /// (`entities.is_terminal_entity`): its schema node has no children.
    pub fn is_terminal_entity(&self, entity: &Uri) -> Result<bool, ConfigError> {
        Ok(match self.get_entity_schema_uri(entity)? {
            None => false,
            Some(schema) => self.child_schema_names(&schema)?.is_empty(),
        })
    }

    /// The schema defaults for `entity` (its schema's resolved properties).
    fn schema_defaults(&self, entity: &Uri) -> Result<Properties, ConfigError> {
        if entity.purpose == "schemas" {
            return Ok(Properties::new());
        }
        Ok(match self.get_entity_schema_uri(entity)? {
            Some(schema) => self.get_properties(&schema)?.unwrap_or_default(),
            None => Properties::new(),
        })
    }

    fn compute(&self, uri: &Uri, include_own: bool) -> Result<Option<Properties>, ConfigError> {
        let Some(root) = self.root(&uri.purpose)? else { return Ok(None) };
        let mut result = self.schema_defaults(uri)?;
        let mut nodes = vec![&*root];
        let mut node = &*root;
        for step in &uri.segments {
            let Some(child) = children(node).and_then(|c| c.get(step)) else { return Ok(None) };
            node = child;
            nodes.push(node);
        }
        if !include_own {
            nodes.pop();
        }
        for node in nodes {
            deep_merge(&mut result, &own_properties(node));
        }
        if include_own && result.is_empty() {
            return Ok(None);
        }
        Ok(Some(result))
    }

    /// `uri`'s resolved properties (`get_properties`); None when it does not
    /// exist, or resolves to nothing at all.
    pub fn get_properties(&self, uri: &Uri) -> Result<Option<Properties>, ConfigError> {
        self.compute(uri, true)
    }

    /// What `uri` would resolve to without its own properties.
    pub fn get_inherited_properties(&self, uri: &Uri) -> Result<Option<Properties>, ConfigError> {
        self.compute(uri, false)
    }

    /// Exactly what is stored on `uri`'s node ({} when nothing); None when
    /// it does not exist.
    pub fn get_own_properties(&self, uri: &Uri) -> Result<Option<Properties>, ConfigError> {
        let Some(root) = self.root(&uri.purpose)? else { return Ok(None) };
        Ok(lookup(&root, &uri.segments).map(own_properties))
    }

    /// Whether `uri`'s node exists.
    pub fn exists(&self, uri: &Uri) -> Result<bool, ConfigError> {
        Ok(match self.root(&uri.purpose)? {
            Some(root) => lookup(&root, &uri.segments).is_some(),
            None => false,
        })
    }

    /// The names of `uri`'s children, in stored order.
    pub fn child_names(&self, uri: &Uri) -> Result<Vec<String>, ConfigError> {
        let Some(root) = self.root(&uri.purpose)? else { return Ok(Vec::new()) };
        Ok(lookup(&root, &uri.segments).and_then(children).map(|c| c.keys().cloned().collect()).unwrap_or_default())
    }

    /// The leaf nodes under `filter` (`list_entity_uris`): its leaf children,
    /// or with `closure` every leaf below it, in Python's order.
    pub fn list_entity_uris(&self, filter: &Uri, closure: bool) -> Result<Vec<Uri>, ConfigError> {
        let Some(root) = self.root(&filter.purpose)? else { return Ok(Vec::new()) };
        let Some(node) = lookup(&root, &filter.segments) else { return Ok(Vec::new()) };
        let empty = Map::new();
        let top = children(node).unwrap_or(&empty);
        let is_leaf = |datum: &Value| children(datum).is_none_or(Map::is_empty);
        if !closure {
            return Ok(top.iter().filter(|(_, datum)| is_leaf(datum)).map(|(name, _)| filter.join(name)).collect());
        }
        // `_list_uri_deep`'s walk: a stack, popped from the end, its
        // result reversed.
        let mut result = Vec::new();
        let mut worklist: Vec<(&String, &Value, Uri)> = top.iter().map(|(name, datum)| (name, datum, filter.clone())).collect();
        while let Some((name, datum, base)) = worklist.pop() {
            let here = base.join(name);
            match children(datum) {
                Some(kids) if !kids.is_empty() => worklist.extend(kids.iter().map(|(n, d)| (n, d, here.clone()))),
                _ => result.push(here),
            }
        }
        result.reverse();
        Ok(result)
    }

    /// Create `uri` with `properties` pinned on it (`add_entity`). Refuses
    /// one that exists, one whose name differs from an existing sibling's
    /// only by case, and a new category, sequence, asset or shot not named
    /// with letters and digits.
    pub fn add_entity(&self, uri: &Uri, properties: Properties) -> Result<(), ConfigError> {
        let mut data = self.read_disk(&uri.purpose)?.unwrap_or_else(|| serde_json::json!({"properties": {}, "children": {}}));
        if lookup(&data, &uri.segments).is_some() {
            return Err(ConfigError::Exists);
        }
        if let Some((wanted, existing)) = find_case_collision(&data, &uri.segments) {
            return Err(ConfigError::CaseCollision { wanted, existing });
        }
        if let Some(problem) = new_name_problem(&uri.purpose, &data, &uri.segments) {
            return Err(problem);
        }
        insert(&mut data, properties, &uri.segments);
        self.write_root(&uri.purpose, data)
    }

    /// Rename `uri` to `new_name`, keeping its place among its siblings
    /// (`rename_entity`): child order is pipeline order for departments.
    /// Refuses a root, a node that does not exist, a name taken or one that
    /// differs from a sibling's only by case, and a new category, sequence,
    /// asset or shot name not of letters and digits. Only the config row:
    /// files named after it are the caller's.
    pub fn rename_entity(&self, uri: &Uri, new_name: &str) -> Result<(), ConfigError> {
        let Some((old_name, parents)) = uri.segments.split_last() else { return Err(ConfigError::RootRename) };
        let mut data = self.read_disk(&uri.purpose)?.ok_or(ConfigError::Missing)?;
        if lookup(&data, &uri.segments).is_none() {
            return Err(ConfigError::Missing);
        }
        let mut new_path = parents.to_vec();
        new_path.push(new_name.to_string());
        if lookup(&data, &new_path).is_some() {
            return Err(ConfigError::NameTaken(new_name.to_string()));
        }
        if let Some((wanted, existing)) = find_case_collision(&data, &new_path) {
            return Err(ConfigError::RenameCaseCollision { wanted, existing });
        }
        if let Some(problem) = new_name_problem(&uri.purpose, &data, &new_path) {
            return Err(problem);
        }
        let mut node = &mut data;
        for step in parents {
            node = node.get_mut("children").and_then(|c| c.get_mut(step)).ok_or(ConfigError::Missing)?;
        }
        let children = node.get_mut("children").and_then(Value::as_object_mut).ok_or(ConfigError::Missing)?;
        *children = std::mem::take(children)
            .into_iter()
            .map(|(name, child)| (if name == *old_name { new_name.to_string() } else { name }, child))
            .collect();
        self.write_root(&uri.purpose, data)
    }

    /// Remove `uri` and everything under it (`remove_entity`).
    pub fn remove_entity(&self, uri: &Uri) -> Result<(), ConfigError> {
        let mut data = self.read_disk(&uri.purpose)?.ok_or(ConfigError::Missing)?;
        let Some((last, parents)) = uri.segments.split_last() else { return Err(ConfigError::Missing) };
        if lookup(&data, &uri.segments).is_none() {
            return Err(ConfigError::Missing);
        }
        let mut node = &mut data;
        for step in parents {
            node = node.get_mut("children").and_then(|c| c.get_mut(step)).ok_or(ConfigError::Missing)?;
        }
        node.get_mut("children").and_then(Value::as_object_mut).and_then(|c| c.shift_remove(last)).ok_or(ConfigError::Missing)?;
        self.write_root(&uri.purpose, data)
    }

    /// Pin `keys` on `uri`'s own properties as they are on disk now, its
    /// other own keys left as they are (a key already there keeps its
    /// place). Refuses a node that does not exist.
    pub fn set_own_keys(&self, uri: &Uri, keys: Properties) -> Result<(), ConfigError> {
        let mut data = self.read_disk(&uri.purpose)?.ok_or(ConfigError::Missing)?;
        let node = lookup(&data, &uri.segments).ok_or(ConfigError::Missing)?;
        let mut own = node.get("properties").and_then(Value::as_object).cloned().unwrap_or_default();
        own.extend(keys);
        insert(&mut data, own, &uri.segments);
        self.write_root(&uri.purpose, data)
    }

    /// Store `properties` as `uri`'s own, verbatim (`set_own_properties`):
    /// a key present is pinned, a key absent cleared. Pass the node's own
    /// properties with only the changed keys changed, never resolved ones.
    pub fn set_own_properties(&self, uri: &Uri, properties: Properties) -> Result<(), ConfigError> {
        let mut data = self.read_disk(&uri.purpose)?.unwrap_or_else(|| serde_json::json!({"children": {}}));
        insert(&mut data, properties, &uri.segments);
        self.write_root(&uri.purpose, data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn project(files: &[(&str, Value)]) -> (tempfile::TempDir, Config) {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("db")).unwrap();
        for (purpose, value) in files {
            std::fs::write(dir.path().join("db").join(format!("{purpose}.json")), value.to_string()).unwrap();
        }
        let config = Config::new(dir.path());
        (dir, config)
    }

    fn uri(text: &str) -> Uri {
        Uri::parse(text).unwrap()
    }

    proptest::proptest! {
        #![proptest_config(proptest::prelude::ProptestConfig::with_cases(32))]

        /// QA 2026-11-10: the editor's writes (a master publish's created
        /// shot and range change) never reached the change journal, so an
        /// open browser kept the old shots. Every write, whichever kind,
        /// announces one config change of its purpose for this project, as
        /// the Python store's `write_root` does; a read announces nothing.
        #[test]
        fn every_write_announces_its_purpose(
            writes in proptest::collection::vec((0usize..4, proptest::sample::select(vec!["entity", "config", "groups"]), "[a-z]{1,6}"), 0..8),
        ) {
            let (dir, config) = project(&[]);
            let journal = dir.path().join("events").join(crate::events::JOURNAL_NAME);
            crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = Some(journal.clone()));
            let mut want = Vec::new();
            for (kind, purpose, name) in &writes {
                let node = uri(&format!("{purpose}:/{name}"));
                let props: Properties = serde_json::from_value(json!({"k": name})).unwrap();
                let wrote = match kind {
                    0 => config.set_own_properties(&node, props).is_ok(),
                    1 => config.set_own_keys(&node, props).is_ok(),
                    2 => config.add_entity(&node, props).is_ok(),
                    _ => { let _ = config.get_properties(&node); false }
                };
                if wrote {
                    want.push(purpose.to_string());
                }
            }
            crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = None);
            let key = crate::events::project_key(dir.path());
            let got: Vec<String> = match std::fs::read(&journal) {
                Err(_) => vec![],
                Ok(bytes) => bytes
                    .split(|&b| b == b'\n')
                    .skip(1)
                    .filter(|l| !l.is_empty())
                    .map(|l| {
                        let v: Value = serde_json::from_slice(l).unwrap();
                        assert_eq!((v["project"].as_str(), v["kind"].as_str(), v["uri"].as_str()), (Some(key.as_str()), Some("config"), Some("")));
                        v["detail"].as_str().unwrap().to_owned()
                    })
                    .collect(),
            };
            proptest::prop_assert_eq!(got, want);
        }
    }

    #[test]
    fn properties_merge_schema_then_nodes() {
        let schemas = json!({"properties": {}, "children": {"entity": {"properties": {}, "children": {
            "shots": {"properties": {"fps": 24, "render": {"a": 1, "b": 2}}, "children": {
                "sequence": {"properties": {}, "children": {
                    "shot": {"properties": {"frame_start": 1001, "render": {"a": 1, "b": 2}}, "children": {}}}}}}}}}});
        let entity = json!({"properties": {}, "children": {"shots": {"properties": {"fps": 25}, "children": {
            "010": {"properties": {"render": {"b": 3}}, "children": {
                "sh020": {"properties": {"frame_end": 1100}, "children": {}}}}}}}});
        let (_dir, config) = project(&[("schemas", schemas), ("entity", entity)]);
        let shot = uri("entity:/shots/010/sh020");
        assert_eq!(config.get_entity_schema_uri(&shot).unwrap().unwrap().to_string(), "schemas:/entity/shots/sequence/shot");
        let props = config.get_properties(&shot).unwrap().unwrap();
        assert_eq!(Value::Object(props), json!({"frame_start": 1001, "render": {"a": 1, "b": 3}, "fps": 25, "frame_end": 1100}));
        assert!(config.is_terminal_entity(&shot).unwrap());
        assert!(!config.is_terminal_entity(&uri("entity:/shots/010")).unwrap());
        assert_eq!(config.get_properties(&uri("entity:/shots/020")).unwrap(), None);
        assert_eq!(config.list_entity_uris(&uri("entity:/shots"), true).unwrap(), vec![shot.clone()]);
    }

    #[test]
    fn writes_refuse_and_store_verbatim() {
        let (_dir, config) = project(&[(
            "entity",
            json!({"properties": {}, "children": {"shots": {"properties": {}, "children": {
            "Seq_01": {"properties": {}, "children": {}}}}}}),
        )]);
        assert_eq!(
            config.add_entity(&uri("entity:/shots/seq_01/a"), Properties::new()),
            Err(ConfigError::CaseCollision { wanted: "seq_01".into(), existing: "Seq_01".into() })
        );
        assert_eq!(config.add_entity(&uri("entity:/shots/010/sh_1"), Properties::new()), Err(ConfigError::BadName("sh_1".into())));
        // An old sequence name still takes new shots.
        config.add_entity(&uri("entity:/shots/Seq_01/sh010"), json!({"fps": 24}).as_object().unwrap().clone()).unwrap();
        assert_eq!(config.add_entity(&uri("entity:/shots/Seq_01/sh010"), Properties::new()), Err(ConfigError::Exists));
        let fresh = Config::new(_dir.path());
        assert_eq!(fresh.get_own_properties(&uri("entity:/shots/Seq_01/sh010")).unwrap().unwrap()["fps"], json!(24));
        fresh.remove_entity(&uri("entity:/shots/Seq_01/sh010")).unwrap();
        assert!(!Config::new(_dir.path()).exists(&uri("entity:/shots/Seq_01/sh010")).unwrap());
    }

    proptest::proptest! {
        /// A rename keeps the node's place among its siblings and its
        /// subtree, and is refused (nothing written) for a name taken, one
        /// differing from a sibling's only by case, or a bad entity name.
        #[test]
        fn rename_keeps_place_and_refuses_as_python(
            names in proptest::collection::vec("[A-Ca-c][0-9]", 1..6),
            pick in proptest::prelude::any::<proptest::sample::Index>(),
            new_name in "[A-Ca-c_][0-9]",
        ) {
            let mut unique: Vec<String> = Vec::new();
            for n in names { if !unique.iter().any(|u: &String| u.to_lowercase() == n.to_lowercase()) { unique.push(n); } }
            let old = pick.get(&unique).clone();
            let children: Map<String, Value> = unique.iter().map(|n| (n.clone(), json!({"properties": {"n": n}, "children": {}}))).collect();
            let entity = json!({"properties": {}, "children": {"shots": {"properties": {}, "children": children}}});
            let (dir, config) = project(&[("entity", entity.clone())]);
            let result = config.rename_entity(&Uri::new("entity", ["shots", old.as_str()]), &new_name);
            let on_disk = load_json(&dir.path().join("db").join("entity.json")).unwrap();
            if unique.contains(&new_name) {
                proptest::prop_assert_eq!(result, Err(ConfigError::NameTaken(new_name.clone())));
            } else if let Some(existing) = unique.iter().find(|u| u.to_lowercase() == new_name.to_lowercase()) {
                proptest::prop_assert_eq!(result, Err(ConfigError::RenameCaseCollision { wanted: new_name.clone(), existing: existing.clone() }));
            } else if new_name.contains('_') {
                proptest::prop_assert_eq!(result, Err(ConfigError::BadName(new_name.clone())));
            } else {
                proptest::prop_assert_eq!(result, Ok(()));
                let names: Vec<String> = on_disk["children"]["shots"]["children"].as_object().unwrap().keys().cloned().collect();
                let expected: Vec<String> = unique.iter().map(|n| if *n == old { new_name.clone() } else { n.clone() }).collect();
                proptest::prop_assert_eq!(names, expected);
                proptest::prop_assert_eq!(&on_disk["children"]["shots"]["children"][&new_name]["properties"]["n"], &json!(old));
                return Ok(());
            }
            proptest::prop_assert_eq!(on_disk, entity);
        }
    }
}
