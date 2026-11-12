use std::fs::Metadata;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde_json::{Value, json};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum DbError {
    #[error("failed to read {file}: {source}")]
    Io {
        file: String,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to parse {file}: {source}")]
    Parse {
        file: String,
        #[source]
        source: serde_json::Error,
    },
    #[error("failed to write {file}: {source}")]
    Write {
        file: String,
        #[source]
        source: std::io::Error,
    },
    #[error("{file} mtime changed since read (expected {expected}, actual {actual}) — re-read before writing")]
    MtimeMismatch { file: String, expected: String, actual: String },
    #[error("unexpected shape in {file}: {context}")]
    Shape { file: String, context: String },
    #[error("name {name:?} {reason}", reason = crate::naming::entity_name_reason(.name))]
    BadEntityName { name: String },
    #[error("system clock is before UNIX epoch: {0}")]
    Clock(String),
}

fn purpose_path(db_dir: &Path, purpose: &str) -> PathBuf {
    db_dir.join(format!("{purpose}.json"))
}

/// Format a file's mtime as `<secs>.<nanos:09>` so the model can pass it back
/// verbatim as `expected_mtime` on a subsequent write. The value is opaque to
/// the model — only string equality is meaningful.
pub fn mtime_str(meta: &Metadata) -> Result<String, DbError> {
    let modified = meta.modified().map_err(|source| DbError::Io { file: "<metadata>".into(), source })?;
    let dur = modified.duration_since(UNIX_EPOCH).map_err(|e| DbError::Clock(e.to_string()))?;
    Ok(format!("{}.{:09}", dur.as_secs(), dur.subsec_nanos()))
}

/// Read and parse a `_config/db/<name>` JSON file. Returns just the parsed
/// `Value`; use [`read_with_mtime`] when the caller needs the mtime as well
/// (which any code path that may later write does).
pub fn read(db_dir: &Path, name: &str) -> Result<Value, DbError> {
    let path = db_dir.join(name);
    let bytes = std::fs::read(&path).map_err(|source| DbError::Io { file: name.to_string(), source })?;
    serde_json::from_slice(&bytes).map_err(|source| DbError::Parse { file: name.to_string(), source })
}

/// Read a `<purpose>.json` file and return its parsed value alongside the
/// current mtime, formatted via [`mtime_str`].
pub fn read_with_mtime(db_dir: &Path, purpose: &str) -> Result<(Value, String), DbError> {
    let path = purpose_path(db_dir, purpose);
    let file_name = format!("{purpose}.json");
    let bytes = std::fs::read(&path).map_err(|source| DbError::Io { file: file_name.clone(), source })?;
    let value = serde_json::from_slice(&bytes).map_err(|source| DbError::Parse { file: file_name.clone(), source })?;
    let meta = std::fs::metadata(&path).map_err(|source| DbError::Io { file: file_name, source })?;
    Ok((value, mtime_str(&meta)?))
}

/// Atomically write `data` to `<db_dir>/<purpose>.json` using a temp file in
/// the same directory plus a rename, byte for byte as TumblePipe's store
/// writes it ([`crate::io::store_json`]: `json.dump(indent=4)` with Python's
/// ASCII escapes, `\r\n` lines on Windows), so a file both write never
/// churns. Returns the new mtime string.
pub fn write_atomic(db_dir: &Path, purpose: &str, data: &Value) -> Result<String, DbError> {
    let file_name = format!("{purpose}.json");
    let target = purpose_path(db_dir, purpose);

    let mut tmp = tempfile::Builder::new()
        .prefix(&format!(".{purpose}."))
        .suffix(".json.tmp")
        .tempfile_in(db_dir)
        .map_err(|source| DbError::Write { file: file_name.clone(), source })?;

    tmp.as_file_mut()
        .write_all(crate::io::python_text(data).as_bytes())
        .map_err(|source| DbError::Write { file: file_name.clone(), source })?;
    tmp.as_file_mut().sync_all().map_err(|source| DbError::Write { file: file_name.clone(), source })?;

    // Renamed with the retry a reader holding the file open needs on
    // Windows (`util.io.replace_file`); the TempPath removes a leftover.
    let tmp = tmp.into_temp_path();
    crate::io::replace_file(&tmp, &target).map_err(|source| DbError::Write { file: file_name.clone(), source })?;
    let _ = tmp.keep();

    let meta = std::fs::metadata(&target).map_err(|source| DbError::Io { file: file_name, source })?;
    mtime_str(&meta)
}

/// Read, mutate in memory, write back atomically. No mtime guard — use for
/// internal high-level operations that own their entire read-modify-write
/// cycle within a single call. For tools that hand the read mtime back to
/// the model and trust it to pass it back on write, use [`apply_mut`].
pub fn apply_mut_atomic<F>(db_dir: &Path, purpose: &str, mutate: F) -> Result<String, DbError>
where
    F: FnOnce(&mut Value) -> Result<(), DbError>,
{
    let (mut root, _) = read_with_mtime(db_dir, purpose)?;
    mutate(&mut root)?;
    write_atomic(db_dir, purpose, &root)
}

/// Read, check mtime, mutate, write back atomically. Returns the new mtime.
/// Use for tools that expose the read mtime to the model and accept it back
/// on write (the standard contract for the low-level `db_*` tools).
pub fn apply_mut<F>(db_dir: &Path, purpose: &str, expected_mtime: &str, mutate: F) -> Result<String, DbError>
where
    F: FnOnce(&mut Value) -> Result<(), DbError>,
{
    let file_name = format!("{purpose}.json");
    let (mut root, actual) = read_with_mtime(db_dir, purpose)?;
    if actual != expected_mtime {
        return Err(DbError::MtimeMismatch { file: file_name, expected: expected_mtime.to_string(), actual });
    }
    mutate(&mut root)?;
    write_atomic(db_dir, purpose, &root)
}

/// Walk a `properties`/`children` tree to the node at `segments`. Returns
/// `None` if any segment is missing along the way.
pub fn lookup<'a>(root: &'a Value, segments: &[String]) -> Option<&'a Value> {
    let mut node = root;
    for seg in segments {
        node = node.get("children")?.get(seg)?;
    }
    Some(node)
}

/// Mutable counterpart of [`lookup`]; returns `None` if any segment is
/// missing instead of creating it (use [`lookup_or_create_mut`] for that).
pub fn lookup_mut<'a>(root: &'a mut Value, segments: &[String]) -> Option<&'a mut Value> {
    let mut node = root;
    for seg in segments {
        node = node.get_mut("children")?.get_mut(seg)?;
    }
    Some(node)
}

/// The `entity.json` branches whose nodes are named categories, assets,
/// sequences, shots and edits. Mirrors `_NAMED_KINDS` in TumblePipe's
/// `tumblepipe/config/store.py`.
const NAMED_KINDS: &[&str] = &["assets", "shots", "edits"];

/// TumblePipe's entity name rule (`tumblepipe.naming.is_valid_entity_name`):
/// [`crate::naming::is_valid_entity_name`], kept here for callers of `db`.
pub fn is_valid_entity_name(name: &str) -> bool {
    crate::naming::is_valid_entity_name(name)
}

/// Whether `segments[depth]` in `file` names a category, asset, sequence or
/// shot, so a new one must follow [`is_valid_entity_name`].
fn is_named_entity(file: &str, segments: &[String], depth: usize) -> bool {
    file == "entity.json" && depth >= 1 && NAMED_KINDS.contains(&segments[0].as_str())
}

/// How many leading `segments` already exist under `root`.
fn existing_depth(root: &Value, segments: &[String]) -> usize {
    let mut node = root;
    for (depth, seg) in segments.iter().enumerate() {
        match node.get("children").and_then(|c| c.get(seg)) {
            Some(child) => node = child,
            None => return depth,
        }
    }
    segments.len()
}

/// Walk to the node at `segments`, creating intermediate
/// `{properties: {}, children: {}}` nodes along the way.
///
/// A category, asset, sequence or shot this creates must follow the entity
/// name rule ([`is_valid_entity_name`]); existing nodes are walked whatever
/// their name, so a sequence named before the rule (`Seq_0010`) still takes
/// new shots. As in TumblePipe's store, the path is checked before anything
/// is created, so a refused call leaves the tree as it was.
pub fn lookup_or_create_mut<'a>(root: &'a mut Value, segments: &[String], file: &str) -> Result<&'a mut Value, DbError> {
    for depth in existing_depth(root, segments)..segments.len() {
        if is_named_entity(file, segments, depth) && !is_valid_entity_name(&segments[depth]) {
            return Err(DbError::BadEntityName { name: segments[depth].clone() });
        }
    }
    let mut node = root;
    for seg in segments {
        let obj = node.as_object_mut().ok_or_else(|| DbError::Shape {
            file: file.to_string(),
            context: format!("expected an object node while walking to {seg:?}"),
        })?;
        let children_entry = obj.entry("children".to_string()).or_insert_with(|| json!({}));
        let children = children_entry
            .as_object_mut()
            .ok_or_else(|| DbError::Shape { file: file.to_string(), context: "\"children\" must be an object".to_string() })?;
        node = children.entry(seg.clone()).or_insert_with(|| json!({"properties": {}, "children": {}}));
    }
    Ok(node)
}

/// Remove the child at `segments` from its parent, returning the removed
/// value. Errors if the URI is a root (no parent) or the segment is missing.
pub fn remove_child(root: &mut Value, segments: &[String], file: &str) -> Result<Value, DbError> {
    let (last, parent_segments) =
        segments.split_last().ok_or_else(|| DbError::Shape { file: file.to_string(), context: "cannot remove a root node".to_string() })?;
    let parent = lookup_mut(root, parent_segments)
        .ok_or_else(|| DbError::Shape { file: file.to_string(), context: "parent path does not exist".to_string() })?;
    let children = parent
        .get_mut("children")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| DbError::Shape { file: file.to_string(), context: "parent has no \"children\" object".to_string() })?;
    children.shift_remove(last).ok_or_else(|| DbError::Shape { file: file.to_string(), context: format!("no child named {last:?}") })
}

/// Rename the child at `segments` within its parent's children map, keeping
/// its place among its siblings.
pub fn rename_child(root: &mut Value, segments: &[String], new_name: &str, file: &str) -> Result<(), DbError> {
    let (last, parent_segments) =
        segments.split_last().ok_or_else(|| DbError::Shape { file: file.to_string(), context: "cannot rename a root node".to_string() })?;
    if is_named_entity(file, segments, segments.len() - 1) && !is_valid_entity_name(new_name) {
        return Err(DbError::BadEntityName { name: new_name.to_string() });
    }
    let parent = lookup_mut(root, parent_segments)
        .ok_or_else(|| DbError::Shape { file: file.to_string(), context: "parent path does not exist".to_string() })?;
    let children = parent
        .get_mut("children")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| DbError::Shape { file: file.to_string(), context: "parent has no \"children\" object".to_string() })?;
    if children.contains_key(new_name) {
        return Err(DbError::Shape { file: file.to_string(), context: format!("destination name {new_name:?} already exists") });
    }
    if !children.contains_key(last) {
        return Err(DbError::Shape { file: file.to_string(), context: format!("no child named {last:?}") });
    }
    // In place, as `store.rename_entity`: child order is pipeline order for
    // departments.
    *children = std::mem::take(children)
        .into_iter()
        .map(|(name, child)| (if name == *last { new_name.to_string() } else { name }, child))
        .collect();
    Ok(())
}

/// Walk a `properties`/`children` tree starting at `start_segments`, returning
/// the absolute segment paths of every leaf (i.e. nodes whose `children` is
/// empty or absent). Returns `None` if `start_segments` does not exist.
///
/// If the start node itself is a leaf, the returned vec contains exactly
/// `start_segments`. Output order matches the JSON object key order at each
/// level, depth-first.
pub fn list_leaves(root: &Value, start_segments: &[String]) -> Option<Vec<Vec<String>>> {
    let start = lookup(root, start_segments)?;
    let mut out = Vec::new();
    let mut path: Vec<String> = start_segments.to_vec();
    walk_leaves(start, &mut path, &mut out);
    Some(out)
}

fn walk_leaves(node: &Value, path: &mut Vec<String>, out: &mut Vec<Vec<String>>) {
    let children = node.get("children").and_then(|c| c.as_object());
    match children {
        Some(map) if !map.is_empty() => {
            for (name, child) in map {
                path.push(name.clone());
                walk_leaves(child, path, out);
                path.pop();
            }
        }
        _ => out.push(path.clone()),
    }
}

/// Walk a `properties`/`children` tree, returning the names of children at the
/// given dotted path. Returns an empty vec if any segment is missing.
///
/// Example: with `entity.json` and path `["shots"]` this returns the sequence
/// names; with `["shots", "<seq>"]` it returns shot names under that sequence.
pub fn children_at<'a, I>(root: &Value, path: I) -> Vec<String>
where
    I: IntoIterator<Item = &'a str>,
{
    let mut node = root;
    for segment in path {
        let Some(children) = node.get("children") else {
            return Vec::new();
        };
        let Some(next) = children.get(segment) else {
            return Vec::new();
        };
        node = next;
    }
    let Some(children) = node.get("children").and_then(|c| c.as_object()) else {
        return Vec::new();
    };
    children.keys().cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;
    use std::collections::BTreeSet;

    /// Build a `{properties, children}` node with no own properties and the
    /// given children map.
    fn node(children: serde_json::Map<String, Value>) -> Value {
        json!({ "properties": {}, "children": children })
    }

    /// Names follow the identifier alphabet TumblePipe DBs actually use.
    fn name() -> impl Strategy<Value = String> {
        "[a-zA-Z][a-zA-Z0-9_]{0,7}"
    }

    /// A bounded properties/children tree (depth <= 3, fanout <= 4).
    fn tree() -> impl Strategy<Value = Value> {
        let leaf = Just(node(serde_json::Map::new()));
        leaf.prop_recursive(3, 16, 4, |inner| {
            proptest::collection::hash_map(name(), inner, 0..4).prop_map(|m| node(m.into_iter().collect()))
        })
    }

    proptest! {
        /// `children_at` must never panic, regardless of the input tree shape
        /// or path segments. The function returning at all is the assertion.
        #[test]
        fn never_panics_on_arbitrary_tree(tree in tree(), seg in name()) {
            let _ = children_at(&tree, [seg.as_str()]);
        }

        /// Walking through a segment that is guaranteed not to exist returns
        /// the empty vec.
        #[test]
        fn missing_segment_returns_empty(tree in tree(), garbage in name()) {
            let absent = format!("__missing_{garbage}");
            prop_assert!(children_at(&tree, [absent.as_str()]).is_empty());
        }

        /// Empty path returns exactly the top-level children keys, as a set.
        #[test]
        fn empty_path_returns_top_children(
            names in proptest::collection::hash_set(name(), 0..5)
        ) {
            let mut children = serde_json::Map::new();
            for n in &names {
                children.insert(n.clone(), node(serde_json::Map::new()));
            }
            let tree = node(children);
            let got: BTreeSet<String> =
                children_at(&tree, std::iter::empty::<&str>()).into_iter().collect();
            let want: BTreeSet<String> = names.into_iter().collect();
            prop_assert_eq!(got, want);
        }

        /// Round-trip: given a known sub-children set planted under one path
        /// segment, `children_at(tree, [parent])` returns exactly that set.
        #[test]
        fn one_level_round_trip(
            parent in name(),
            kids in proptest::collection::hash_set(name(), 0..5),
        ) {
            let mut sub = serde_json::Map::new();
            for k in &kids {
                sub.insert(k.clone(), node(serde_json::Map::new()));
            }
            let mut top = serde_json::Map::new();
            top.insert(parent.clone(), node(sub));
            let tree = node(top);
            let got: BTreeSet<String> =
                children_at(&tree, [parent.as_str()]).into_iter().collect();
            let want: BTreeSet<String> = kids.into_iter().collect();
            prop_assert_eq!(got, want);
        }
    }

    // ---- the entity name rule ------------------------------------------

    /// A small name pool that mixes valid names with every character the
    /// URI alphabet allows but the entity rule refuses (`_`, `-`, `.`), so
    /// generated paths collide with each other and with planted nodes.
    fn pool() -> impl Strategy<Value = Vec<String>> {
        proptest::collection::vec("[A-Za-z0-9_.-]{1,4}", 1..6)
    }

    /// An `entity.json`-shaped tree with `assets`/`shots` branches and the
    /// given extra paths planted *without* the guard (pre-rule names such
    /// as `Seq_0010` exist in live projects), plus a target path.
    #[allow(clippy::type_complexity)]
    fn entity_case() -> impl Strategy<Value = (Value, Vec<String>)> {
        pool()
            .prop_flat_map(|names| {
                let seg = proptest::sample::select(names);
                let kind = proptest::sample::select(vec!["assets", "shots"]);
                let path = (kind, proptest::collection::vec(seg, 1..4)).prop_map(|(k, rest)| {
                    let mut p = vec![k.to_string()];
                    p.extend(rest);
                    p
                });
                (proptest::collection::vec(path.clone(), 0..4), path)
            })
            .prop_map(|(planted, target)| {
                let mut tree = json!({"properties": {}, "children": {
                    "assets": {"properties": {}, "children": {}},
                    "shots": {"properties": {}, "children": {}}
                }});
                for p in planted {
                    // Plant with a file name the guard ignores.
                    lookup_or_create_mut(&mut tree, &p, "planted.json").unwrap();
                }
                (tree, target)
            })
    }

    /// Python's `str.isascii() and str.isalnum()` and not a Windows device
    /// name, the rule TumblePipe's `naming.is_valid_entity_name` applies,
    /// written independently.
    fn python_rule(name: &str) -> bool {
        let ascii = name.is_ascii() && !name.is_empty() && name.chars().all(char::is_alphanumeric);
        let lower = name.to_lowercase();
        let device = ["con", "prn", "aux", "nul"].contains(&lower.as_str())
            || (lower.len() == 4 && (lower.starts_with("com") || lower.starts_with("lpt")) && lower.as_bytes()[3].is_ascii_digit());
        ascii && !device
    }

    proptest! {
        /// The Rust rule agrees with TumblePipe's on any string, Unicode
        /// letters and digits included (Python's `isalnum` alone would
        /// accept them; the `isascii` half refuses them).
        #[test]
        fn name_rule_matches_tumblepipe(name in any::<String>()) {
            prop_assert_eq!(is_valid_entity_name(&name), python_rule(&name));
        }

        /// A create under `entity:/assets` or `entity:/shots` succeeds
        /// exactly when every node it *creates* follows the rule (existing
        /// nodes are walked whatever their name), and a refused one leaves
        /// the tree exactly as it was. On success every node that existed
        /// is still there and the target exists.
        #[test]
        fn create_checks_exactly_the_new_nodes((tree, target) in entity_case()) {
            let before = tree.clone();
            let depth = existing_depth(&tree, &target);
            let expect_ok = target[depth.max(1)..].iter().all(|n| is_valid_entity_name(n));
            let mut after = tree;
            let result = lookup_or_create_mut(&mut after, &target, "entity.json").map(|_| ());
            match result {
                Ok(()) => {
                    prop_assert!(expect_ok, "accepted {:?}", target);
                    prop_assert!(lookup(&after, &target).is_some());
                    for leaf in list_leaves(&before, &[] as &[String]).unwrap_or_default() {
                        prop_assert!(lookup(&after, &leaf).is_some());
                    }
                }
                Err(DbError::BadEntityName { name }) => {
                    prop_assert!(!expect_ok, "refused {:?}", target);
                    prop_assert!(!is_valid_entity_name(&name));
                    prop_assert_eq!(&after, &before);
                }
                Err(e) => prop_assert!(false, "unexpected error {e}"),
            }
        }

        /// Other databases (groups, departments, ...) are not entity names:
        /// the same paths always create there.
        #[test]
        fn other_files_are_never_checked((tree, target) in entity_case()) {
            let mut tree = tree;
            prop_assert!(lookup_or_create_mut(&mut tree, &target, "groups.json").is_ok());
        }

        /// A rename of a named entity is refused for a new name that breaks
        /// the rule, before anything else is checked, and any refusal leaves
        /// the tree as it was.
        #[test]
        fn rename_checks_the_new_name(
            (tree, target) in entity_case(),
            new_name in "[A-Za-z0-9_.-]{1,4}",
        ) {
            let before = tree.clone();
            let mut after = tree;
            let result = rename_child(&mut after, &target, &new_name, "entity.json");
            let named = target.len() >= 2;
            if named && !is_valid_entity_name(&new_name) {
                prop_assert!(
                    matches!(result, Err(DbError::BadEntityName { .. })),
                    "{:?}",
                    result
                );
            }
            if result.is_err() {
                prop_assert_eq!(&after, &before);
            } else {
                prop_assert!(!named || is_valid_entity_name(&new_name));
            }
        }

        /// A renamed node keeps its place among its siblings (department
        /// order is pipeline order), as `store.rename_entity` keeps it.
        #[test]
        fn rename_keeps_the_place(
            names in proptest::collection::vec("[a-z]{1,4}", 1..6),
            pick in any::<proptest::sample::Index>(),
        ) {
            let mut unique: Vec<String> = Vec::new();
            for n in names { if !unique.contains(&n) { unique.push(n); } }
            let old = pick.get(&unique).clone();
            let mut tree = node(unique.iter().map(|n| (n.clone(), node(serde_json::Map::new()))).collect());
            rename_child(&mut tree, std::slice::from_ref(&old), "NEW", "departments.json").unwrap();
            let expected: Vec<String> = unique.iter().map(|n| if *n == old { "NEW".to_string() } else { n.clone() }).collect();
            prop_assert_eq!(children_at(&tree, std::iter::empty::<&str>()), expected);
        }

        /// What `write_atomic` writes is byte for byte what TumblePipe's
        /// store writes (`io::store_json`, parity-tested against Python):
        /// `\uXXXX` escapes, Windows lines, no trailing newline.
        #[test]
        fn writes_what_the_store_writes(value in json_value()) {
            let dir = tempfile::tempdir().unwrap();
            write_atomic(dir.path(), "x", &value).unwrap();
            crate::io::store_json(&dir.path().join("y.json"), &value).unwrap();
            prop_assert_eq!(std::fs::read(dir.path().join("x.json")).unwrap(), std::fs::read(dir.path().join("y.json")).unwrap());
            prop_assert_eq!(read(dir.path(), "x.json").unwrap(), value);
        }
    }

    fn json_value() -> impl Strategy<Value = Value> {
        let leaf = prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::Bool),
            any::<i64>().prop_map(Value::from),
            "[a-zA-Z0-9 \"\\\\/éøß💡\u{7f}\n\t]{0,6}".prop_map(Value::String),
        ];
        leaf.prop_recursive(3, 24, 4, |inner| {
            prop_oneof![
                proptest::collection::vec(inner.clone(), 0..4).prop_map(Value::Array),
                proptest::collection::vec(("[a-zø]{1,3}", inner), 0..4).prop_map(|kv| Value::Object(kv.into_iter().collect())),
            ]
        })
    }

    #[test]
    fn empty_tree_has_no_children() {
        let tree = node(serde_json::Map::new());
        assert!(children_at(&tree, std::iter::empty::<&str>()).is_empty());
    }

    fn segs(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn list_leaves_returns_none_for_missing_start() {
        let tree = node(serde_json::Map::new());
        assert!(list_leaves(&tree, &segs(&["ghost"])).is_none());
    }

    #[test]
    fn list_leaves_includes_start_when_it_is_a_leaf() {
        let tree = node(serde_json::Map::new());
        let got = list_leaves(&tree, &[]).unwrap();
        assert_eq!(got, vec![Vec::<String>::new()]);
    }

    #[test]
    fn list_leaves_walks_nested_tree() {
        let tree = json!({
            "properties": {}, "children": {
                "shots": { "properties": {}, "children": {
                    "010": { "properties": {}, "children": {
                        "010_010": { "properties": {}, "children": {} },
                        "010_020": { "properties": {}, "children": {} }
                    }},
                    "020": { "properties": {}, "children": {
                        "020_010": { "properties": {}, "children": {} }
                    }}
                }},
                "assets": { "properties": {}, "children": {} }
            }
        });
        let mut got = list_leaves(&tree, &[]).unwrap();
        got.sort();
        let mut want = vec![
            segs(&["assets"]),
            segs(&["shots", "010", "010_010"]),
            segs(&["shots", "010", "010_020"]),
            segs(&["shots", "020", "020_010"]),
        ];
        want.sort();
        assert_eq!(got, want);
    }

    #[test]
    fn list_leaves_from_subtree_returns_absolute_paths() {
        let tree = json!({
            "properties": {}, "children": {
                "shots": { "properties": {}, "children": {
                    "010": { "properties": {}, "children": {
                        "010_010": { "properties": {}, "children": {} }
                    }}
                }}
            }
        });
        let got = list_leaves(&tree, &segs(&["shots", "010"])).unwrap();
        assert_eq!(got, vec![segs(&["shots", "010", "010_010"])]);
    }
}
