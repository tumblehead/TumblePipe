//! Old `th::cache` versions (`tumblepipe.cleanup.caches`).
//!
//! `th::cache` writes `<department>/cache/<name>/vNNNN` (SOP) or
//! `.../lops_cache/<name>/vNNNN` (LOP) in a workfile folder or under the
//! proxy root. A cache is the one derived thing other files point straight
//! into, so a version goes only when it finished and says what made it, that
//! workfile is still there, at least `keep_caches` newer finished versions
//! exist, it is not the highest, no latest workfile names it, no export
//! layer on disk references it, nothing was written to it lately and it is
//! not marked `.keep`.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde_json::Value;
use th_pipe::naming::is_valid_version_name;

use crate::core::{
    Category, CategoryPlan, Entry, KEEP_FILE, Outcome, Payload, RECENT_DAYS, Settings, Steer, Watch, Workfiles, days, delete_entries,
    under_context,
};
use crate::fsx;
use crate::hip;
use crate::inventory::{Inventory, load_cache_context, version_code};
use crate::py;
use crate::roots::ProjectRoots;
use crate::usd;

pub const PURPOSES: [&str; 4] = ["assets", "shots", "groups", "proxy"];

pub const CATEGORY: Category = Category {
    key: "caches",
    title: "Old cache versions",
    about: "th::cache versions older than the newest few, that their workfile can make again and that no latest workfile or export still loads",
    purposes: &PURPOSES,
};

const CACHE_DIRS: [&str; 2] = ["cache", "lops_cache"];
const LAYERS: [&str; 3] = [".usd", ".usda", ".usdc"];

pub fn reason_prune(keep: i64) -> String {
    format!("older than the newest {keep} finished versions, and its workfile can make it again")
}
pub fn reason_keep_newest(keep: i64) -> String {
    format!("one of the newest {keep} finished versions")
}
pub const KEEP_HIGHEST: &str = "the highest version of its cache: its number would be used again";
pub const KEEP_NO_RECORD: &str = "no record of what made it (or unfinished): it could not be made again";
pub const KEEP_NO_WORKFILE: &str = "the workfile that made it is gone: it could not be made again";
pub const KEEP_WORKFILE: &str = "a latest workfile names it";
pub const KEEP_EXPORT: &str = "an export references it";
pub const KEEP_NO_USD: &str = "the exports could not be read here (no USD): nothing is deleted";
pub fn reason_keep_recent() -> String {
    format!("written to in the last {} days", days(RECENT_DAYS))
}
pub fn reason_keep_marked() -> String {
    format!("marked to keep ({KEEP_FILE})")
}

/// `(purpose, rel)`.
type Key = (String, String);

/// One cache version folder: `rel` under the root of `purpose`.
#[derive(Debug, Clone)]
struct Unit {
    purpose: String,
    rel: String,
    code: i128,
    size: u64,
    marked: bool,
}

impl Unit {
    fn parts(&self) -> Vec<String> {
        self.rel.split('/').map(String::from).collect()
    }
    fn key(&self) -> Key {
        (self.purpose.clone(), self.rel.clone())
    }
    fn series(&self) -> Key {
        (self.purpose.clone(), self.rel.rsplit_once('/').map_or(String::new(), |(s, _)| s.to_string()))
    }
    fn cache_name(&self) -> String {
        let parts = self.parts();
        parts[parts.len() - 2].clone()
    }
    /// `cache/<name>/vNNNN`: how a path into it ends.
    fn tail(&self) -> String {
        let parts = self.parts();
        parts[parts.len() - 3..].join("/")
    }
}

/// The cache version folder a file's path parts are in
/// (`.../<cache|lops_cache>/<name>/vNNNN/...`), or None (`cache_unit`).
pub fn cache_unit(parts: &[String]) -> Option<Vec<String>> {
    for at in 0..parts.len().saturating_sub(3) {
        if CACHE_DIRS.contains(&parts[at].as_str()) && is_valid_version_name(&parts[at + 2]) {
            return Some(parts[..at + 3].to_vec());
        }
    }
    None
}

/// The units in the order first met, as Python's dict.
fn list_units(inventory: &Inventory) -> (Vec<Key>, HashMap<Key, Unit>) {
    let mut order = Vec::new();
    let mut units: HashMap<Key, Unit> = HashMap::new();
    for item in &inventory.items {
        if !PURPOSES.contains(&item.root.as_str()) {
            continue;
        }
        let parts: Vec<String> = item.rel.split('/').map(String::from).collect();
        let Some(unit_parts) = cache_unit(&parts) else { continue };
        if parts.iter().any(|p| p == "_deleted") {
            continue;
        }
        let key = (item.root.clone(), unit_parts.join("/"));
        let unit = units.entry(key.clone()).or_insert_with(|| {
            order.push(key.clone());
            Unit {
                purpose: item.root.clone(),
                rel: key.1.clone(),
                code: version_code(&unit_parts[unit_parts.len() - 1]),
                size: 0,
                marked: false,
            }
        });
        unit.size += item.size;
        if parts[unit_parts.len()..] == [KEEP_FILE.to_string()] {
            unit.marked = true;
        }
    }
    (order, units)
}

/// What a workfile's text names (`hip::readable`): `(cache name, version)` of every node with a
/// `name` and an exact `version` parm, and every `cache/<name>/vNNNN` a
/// path in it runs into (`hip_names`).
pub fn hip_names(text: &str) -> (HashSet<(String, String)>, HashSet<String>) {
    let mut pairs = HashSet::new();
    let mut names: Vec<String> = Vec::new();
    let mut versions: Vec<String> = Vec::new();
    let flush = |pairs: &mut HashSet<(String, String)>, names: &[String], versions: &[String]| {
        for name in names {
            for version in versions {
                pairs.insert((name.clone(), version.clone()));
            }
        }
    };
    for line in py::splitlines(text) {
        if line.starts_with('}') || line.starts_with('{') {
            flush(&mut pairs, &names, &versions);
            names.clear();
            versions.clear();
            continue;
        }
        let Some((name, value)) = hip::parm(line) else { continue };
        if name == "name" && !value.is_empty() {
            names.push(value.to_string());
        } else if name == "version" && is_valid_version_name(value) {
            versions.push(value.to_string());
        }
    }
    flush(&mut pairs, &names, &versions);
    (pairs, path_tails(text))
}

/// Every `<cache|lops_cache>/<name>/vNNNN` a path in `text` runs into,
/// with what leads up to it (`path_tails`, `_INTO`:
/// `(?:^|/)((?:cache|lops_cache)/[^/"'\s]+/v\d{4,})(?![0-9])`).
pub fn path_tails(text: &str) -> HashSet<String> {
    let mut flat = String::with_capacity(text.len());
    for c in text.chars() {
        let c = if c == '\\' { '/' } else { c };
        if c == '/' && flat.ends_with('/') {
            continue;
        }
        flat.push(c);
    }
    let chars: Vec<char> = flat.chars().collect();
    // Byte offsets of each char, for slicing `flat`.
    let mut offsets: Vec<usize> = flat.char_indices().map(|(at, _)| at).collect();
    offsets.push(flat.len());
    let name_ok = |c: char| !(c == '/' || c == '"' || c == '\'' || py::is_space(c));
    // The body at char `at`: its end (in chars), if it matches there.
    let body = |at: usize| -> Option<usize> {
        let rest = &chars[at..];
        let head = ["cache/", "lops_cache/"].into_iter().map(|h| h.chars().collect::<Vec<_>>()).find(|h| rest.starts_with(h))?;
        let name_start = at + head.len();
        let name_len = chars[name_start..].iter().take_while(|c| name_ok(**c)).count();
        if name_len == 0 {
            return None;
        }
        let after = name_start + name_len;
        if chars.get(after) != Some(&'/') || chars.get(after + 1) != Some(&'v') {
            return None;
        }
        let digits = chars[after + 2..].iter().take_while(|c| py::is_digit(**c)).count();
        (digits >= 4).then_some(after + 2 + digits)
    };
    let mut found = HashSet::new();
    let mut at = 0;
    while at <= chars.len() {
        let hit = if at == 0 { body(0).map(|end| (0, end)) } else { None }
            .or_else(|| (chars.get(at) == Some(&'/')).then(|| body(at + 1).map(|end| (at + 1, end))).flatten());
        let Some((group_start, end)) = hit else {
            at += 1;
            continue;
        };
        // What leads up to it: from just after the last stop before it.
        let before = &flat[..offsets[group_start]];
        let start = ['"', '\'', ' ', '\t', '\n', '@'].iter().filter_map(|stop| before.rfind(*stop)).max().map_or(0, |s| s + 1);
        found.insert(flat[start..offsets[end]].to_string());
        at = end;
    }
    found
}

/// The units the paths lead into: a path ending in a unit's own path
/// under its root, whatever drive or share the root was spelled as.
fn named_by_paths(units: &HashMap<Key, Unit>, order: &[Key], paths: &HashSet<String>) -> HashSet<Key> {
    let mut by_tail: HashMap<String, Vec<&Unit>> = HashMap::new();
    for key in order {
        let unit = &units[key];
        by_tail.entry(unit.tail()).or_default().push(unit);
    }
    let mut named = HashSet::new();
    for path in paths {
        let pieces: Vec<&str> = path.split('/').collect();
        let tail = pieces[pieces.len().saturating_sub(3)..].join("/");
        for unit in by_tail.get(&tail).into_iter().flatten() {
            if *path == unit.rel || path.ends_with(&format!("/{}", unit.rel)) {
                named.insert(unit.key());
            }
        }
    }
    named
}

fn named_by_workfiles(
    workfiles: &Workfiles,
    units: &HashMap<Key, Unit>,
    order: &[Key],
    notes: &mut Vec<String>,
    errors: &mut Vec<String>,
) -> HashSet<Key> {
    let mut by_pair: HashMap<(String, String), Vec<Key>> = HashMap::new();
    for key in order {
        let unit = &units[key];
        let parts = unit.parts();
        by_pair.entry((unit.cache_name(), parts[parts.len() - 1].clone())).or_default().push(unit.key());
    }
    let mut named = HashSet::new();
    let (mut read, mut unreadable) = (0, 0);
    for workfile in &workfiles.list {
        let facts = match &workfile.read {
            None => {
                unreadable += 1;
                continue;
            }
            Some(Err(error)) => {
                errors.push(error.clone());
                continue;
            }
            Some(Ok(facts)) => facts,
        };
        read += 1;
        let Some((pairs, paths)) = &facts.caches else { continue };
        for pair in pairs {
            named.extend(by_pair.get(pair).into_iter().flatten().cloned());
        }
        named.extend(named_by_paths(units, order, paths));
    }
    notes.push(format!("Read {read} latest workfile(s) for the cache versions they name."));
    if unreadable > 0 {
        notes.push(format!(
            "{unreadable} latest workfile(s) are .hiplc/.hipnc in a form that cannot be read: cache versions they name are NOT protected."
        ));
    }
    named
}

/// The units an export layer on disk references; None when a layer
/// cannot be read (it may reference anything).
fn referenced_by_exports(
    roots: &ProjectRoots,
    units: &HashMap<Key, Unit>,
    order: &[Key],
    notes: &mut Vec<String>,
    errors: &mut Vec<String>,
) -> Option<HashSet<Key>> {
    let Some(export) = roots.path("export") else { return Some(HashSet::new()) };
    if !export.is_dir() {
        return Some(HashSet::new());
    }
    let mut paths = HashSet::new();
    let mut read = 0;
    let mut failed = false;
    fsx::os_walk(export, |dir| {
        if failed {
            dir.dirs.clear();
            return;
        }
        dir.dirs.retain(|d| d != "other" && d != "_deleted");
        for name in dir.files {
            let lower = name.to_lowercase();
            if !LAYERS.iter().any(|l| lower.ends_with(l)) {
                continue;
            }
            let layer_path = dir.path.join(name);
            read += 1;
            match usd::layer_references(&layer_path) {
                Ok(references) => {
                    for reference in references {
                        paths.extend(path_tails(&reference));
                    }
                }
                Err(error) => {
                    let error: String = error.chars().take(200).collect();
                    errors.push(format!("{}: {error}", layer_path.display()));
                    failed = true;
                    dir.dirs.clear();
                    return;
                }
            }
        }
    });
    if failed {
        return None;
    }
    notes.push(format!("Read {read} export layer(s) for the cache files they reference."));
    Some(named_by_paths(units, order, &paths))
}

/// Whether the workfile the sidecar names is still in the project.
fn producer_workfile(context: &Value, project_path: &Path) -> bool {
    let producer = context.get("producer").filter(|p| py::truthy(p));
    let workfile = producer.and_then(|p| p.get("workfile")).filter(|w| py::truthy(w)).and_then(|w| w.get("path")).filter(|p| py::truthy(p));
    let node = producer.and_then(|p| p.get("node")).filter(|n| py::truthy(n));
    let (Some(Value::String(workfile)), Some(_)) = (workfile, node) else { return false };
    // A rooted path without a drive is not absolute on Windows: it joins
    // onto the project's drive, as `Path / workfile` does in Python.
    !Path::new(workfile).is_absolute() && project_path.join(workfile).is_file()
}

/// The category's plan (`caches.plan`). An error where Python's raises: a
/// sidecar that cannot be read.
pub fn plan(
    roots: &ProjectRoots,
    inventory: Option<&Inventory>,
    settings: &Settings,
    now: f64,
    workfiles: &Workfiles,
    watch: &Watch<'_>,
) -> Result<CategoryPlan, String> {
    watch.check()?;
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "version");
    let root_paths: Vec<(String, PathBuf)> = roots.roots.iter().map(|r| (r.purpose.clone(), r.path.clone())).collect();
    out.payload = Payload::Roots(root_paths.clone());
    let Some(inventory) = inventory else { return Ok(out) };
    let root_of = |purpose: &str| root_paths.iter().find(|(p, _)| p == purpose).map(|(_, path)| path.clone()).unwrap_or_default();
    let unit_path = |unit: &Unit| {
        let mut path = root_of(&unit.purpose);
        path.extend(unit.parts());
        path
    };
    let (order, units) = list_units(inventory);
    let keep = settings.keep_caches;
    let mut verdicts: HashMap<Key, (bool, String)> = HashMap::new();
    let mut series: Vec<(Key, Vec<&Unit>)> = Vec::new();
    for key in &order {
        let unit = &units[key];
        match series.iter_mut().find(|(s, _)| *s == unit.series()) {
            Some((_, group)) => group.push(unit),
            None => series.push((unit.series(), vec![unit])),
        }
    }
    for (_, mut group) in series {
        group.sort_by_key(|u| u.code);
        let highest = group[group.len() - 1].code;
        let mut contexts: HashMap<Key, Option<Value>> = HashMap::new();
        for unit in &group {
            contexts.insert(unit.key(), load_cache_context(&unit_path(unit))?);
        }
        let finished: Vec<&&Unit> = group.iter().filter(|u| contexts[&u.key()].is_some()).collect();
        let take = usize::try_from(keep).unwrap_or(0).min(finished.len());
        let newest: HashSet<Key> = finished[finished.len() - take..].iter().map(|u| u.key()).collect();
        for unit in &group {
            let context = &contexts[&unit.key()];
            let verdict = if unit.marked {
                (false, reason_keep_marked())
            } else if context.is_none() {
                (false, KEEP_NO_RECORD.to_string())
            } else if newest.contains(&unit.key()) {
                (false, reason_keep_newest(keep))
            } else if unit.code == highest {
                (false, KEEP_HIGHEST.to_string())
            } else if !producer_workfile(context.as_ref().expect("has a record"), &roots.project_path) {
                (false, KEEP_NO_WORKFILE.to_string())
            } else {
                (true, reason_prune(keep))
            };
            verdicts.insert(unit.key(), verdict);
        }
    }

    // Only worth reading the workfiles and the exports when something could go.
    if verdicts.values().any(|(prune, _)| *prune) {
        let by_workfile = named_by_workfiles(workfiles, &units, &order, &mut out.notes, &mut out.errors);
        let by_export = referenced_by_exports(roots, &units, &order, &mut out.notes, &mut out.errors);
        for key in &order {
            if !verdicts[key].0 {
                continue;
            }
            let unit = &units[key];
            let changed = if by_workfile.contains(key) {
                Some(KEEP_WORKFILE.to_string())
            } else if by_export.is_none() {
                Some(KEEP_NO_USD.to_string())
            } else if by_export.as_ref().is_some_and(|b| b.contains(key)) {
                Some(KEEP_EXPORT.to_string())
            } else if fsx::recently_written(&unit_path(unit), now, RECENT_DAYS) {
                Some(reason_keep_recent())
            } else {
                None
            };
            if let Some(reason) = changed {
                verdicts.insert(key.clone(), (false, reason));
            }
        }
    }

    let mut keys: Vec<&Key> = order.iter().collect();
    keys.sort();
    for key in keys {
        let unit = &units[key];
        // The proxy root holds shots/... and assets/... itself.
        let label = if unit.purpose == "proxy" { unit.rel.clone() } else { format!("{}/{}", unit.purpose, unit.rel) };
        if !under_context(&label, settings.context()) {
            continue;
        }
        let (prune, reason) = verdicts[key].clone();
        let parts = unit.parts();
        out.entries.push(Entry {
            path: unit_path(unit),
            label: format!("{}/{}", unit.purpose, unit.rel),
            group: format!("{}/{}", unit.purpose, parts[..parts.len() - 3].join("/")),
            size: unit.size,
            prune,
            reason,
        });
    }
    Ok(out)
}

/// Delete the cache versions marked to go, each still a recorded cache
/// version folder in its root, unmarked and not written to since.
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let root_paths = match &category.payload {
        Payload::Roots(roots) => roots.clone(),
        _ => Vec::new(),
    };
    let still_ok = |entry: &Entry| -> Option<String> {
        let (purpose, rel) = entry.label.split_once('/').unwrap_or((entry.label.as_str(), ""));
        let root = root_paths.iter().find(|(p, _)| p == purpose).map(|(_, path)| path);
        let parts: Vec<String> = rel.split('/').map(String::from).collect();
        let expected = root.map(|root| {
            let mut path = root.clone();
            path.extend(&parts);
            path
        });
        let Some(root) = root.filter(|_| PURPOSES.contains(&purpose) && expected.as_ref().is_some_and(|e| fsx::same_path(e, &entry.path)))
        else {
            return Some("not in a workfile folder or the proxy root".into());
        };
        let mut probe = parts.clone();
        probe.push("x".into());
        if cache_unit(&probe).as_ref() != Some(&parts) || parts.iter().any(|p| p == "_deleted") {
            return Some("not a cache version folder".into());
        }
        let parent = entry.path.parent().unwrap_or(Path::new(""));
        if !fsx::within(parent, root) {
            return Some("outside its storage root".into());
        }
        let link = std::fs::symlink_metadata(&entry.path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if !entry.path.is_dir() || link {
            return Some("not a folder".into());
        }
        match load_cache_context(&entry.path) {
            Ok(Some(_)) => {}
            Ok(None) => return Some("it has no record of what made it".into()),
            Err(error) => return Some(error),
        }
        if entry.path.join(KEEP_FILE).exists() {
            return Some(format!("it holds a {KEEP_FILE} file"));
        }
        if fsx::recently_written(&entry.path, fsx::now(), RECENT_DAYS) {
            return Some("it was written to since it was listed".into());
        }
        None
    };
    delete_entries(category, log_path, steer, still_ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tails_of_paths() {
        let tails = |t: &str| {
            let mut v: Vec<String> = path_tails(t).into_iter().collect();
            v.sort();
            v
        };
        assert_eq!(tails("P:/proj/shots/s/h/fx/cache/smoke/v0003/x.usd"), vec!["P:/proj/shots/s/h/fx/cache/smoke/v0003"]);
        assert_eq!(tails("cache/a/v0001/f"), vec!["cache/a/v0001"]);
        assert_eq!(tails("x \"//srv\\\\share/a/lops_cache/b/v00012/y\""), vec!["/srv/share/a/lops_cache/b/v00012"]);
        assert_eq!(tails("mycache/a/v0001"), Vec::<String>::new());
        assert_eq!(tails("/cache/a/v001"), Vec::<String>::new());
        assert_eq!(tails("cache/a/v0001/cache/b/v0002"), vec!["cache/a/v0001", "cache/a/v0001/cache/b/v0002"]);
    }

    #[test]
    fn cache_units() {
        let p = |s: &str| s.split('/').map(String::from).collect::<Vec<_>>();
        assert_eq!(cache_unit(&p("s/fx/cache/smoke/v0003/f.bgeo")), Some(p("s/fx/cache/smoke/v0003")));
        assert_eq!(cache_unit(&p("s/fx/cache/smoke/v0003")), None);
        assert_eq!(cache_unit(&p("s/fx/lops_cache/a/v00010/x/y")), Some(p("s/fx/lops_cache/a/v00010")));
    }
}
