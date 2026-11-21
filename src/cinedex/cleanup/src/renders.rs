//! Old render versions and check renders (`tumblepipe.cleanup.renders`).
//!
//! A render version folder goes only when at least `keep_renders` newer
//! complete published versions of its layer exist, it is not the highest
//! of its layer, no latest workfile, edit or kept composite version names
//! it, nothing was written to it in the last two days and it holds no
//! `.keep` file. A check render (`published: false`) goes by age alone,
//! under the same guards, and its review movie with it. Only `render/` and
//! the check renders' movies under `playblast/` are read.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde_json::Value;
use th_pipe::naming::is_valid_version_name;

use crate::core::{
    Category, CategoryPlan, Entry, KEEP_FILE, Outcome, Payload, RECENT_DAYS, Settings, Steer, Watch, Workfiles, age_days, days,
    delete_entries, under_context,
};
use crate::fsx;
use crate::hip;
use crate::inventory::{Inventory, Item, Kind, file_version};
use crate::py;
use crate::roots::ProjectRoots;

pub const CATEGORY: Category = Category {
    key: "renders",
    title: "Old render versions",
    about: "render versions older than the newest few complete ones, that no workfile, edit or kept composite names; and check renders past their age",
    purposes: &["render"],
};

const AREA: &str = "render";
const MOVIES: &str = "playblast";

pub fn reason_prune_old(keep: i64) -> String {
    format!("older than the newest {keep} complete versions of its layer")
}
pub fn reason_prune_check(check_days: f64) -> String {
    format!("a check render older than {} days", days(check_days))
}
pub const PRUNE_CHECK_MOVIE: &str = "a check render's review movie, deleted with it";
pub fn reason_keep_newest(keep: i64) -> String {
    format!("one of the newest {keep} complete versions of its layer, or newer")
}
pub const KEEP_HIGHEST: &str = "the highest version of its layer: its number would be used again";
pub const KEEP_WORKFILE: &str = "a latest workfile names it";
pub const KEEP_EDIT: &str = "an edit names it";
pub const KEEP_READ: &str = "a kept composite version read it";
pub fn reason_keep_recent() -> String {
    format!("written to in the last {} days: it may be rendering", days(RECENT_DAYS))
}
pub fn reason_keep_marked() -> String {
    format!("marked to keep ({KEEP_FILE})")
}
pub fn reason_keep_check(check_days: f64) -> String {
    format!("a check render newer than {} days", days(check_days))
}
pub const KEEP_CHECK_MOVIE: &str = "a check render's review movie: its render is kept";

/// One render version folder: `rel` under the render root.
#[derive(Debug, Clone)]
pub struct Unit {
    pub rel: String,
    pub code: i128,
    pub size: u64,
    pub published: bool,
    pub has_context: bool,
    pub marked: bool,
    /// `.exr` files per folder inside it (`''`: directly in it), in the
    /// order first met.
    pub frames: Vec<(String, usize)>,
}

impl Unit {
    pub fn parts(&self) -> Vec<String> {
        self.rel.split('/').map(String::from).collect()
    }
    fn series(&self) -> &str {
        self.rel.rsplit_once('/').map_or("", |(s, _)| s)
    }
    fn name(&self) -> &str {
        self.rel.rsplit('/').next().unwrap_or("")
    }
    /// The frames it has in every AOV: the fewest any of them holds.
    fn frame_count(&self) -> usize {
        self.frames.iter().map(|(_, n)| *n).filter(|n| *n > 0).min().unwrap_or(0)
    }
}

/// Render version folders, kept in the order first met (Python's dict).
#[derive(Debug, Default)]
pub struct Units {
    order: Vec<String>,
    map: HashMap<String, Unit>,
}

impl Units {
    fn get(&self, rel: &str) -> Option<&Unit> {
        self.map.get(rel)
    }
    fn contains(&self, rel: &str) -> bool {
        self.map.contains_key(rel)
    }
    fn values(&self) -> impl Iterator<Item = &Unit> {
        self.order.iter().map(|rel| &self.map[rel])
    }
}

/// `rel` (`render/shots/sq010/...`) is under `context`.
fn in_context(rel: &str, context: Option<&str>) -> bool {
    let tail = rel.split_once('/').map_or(rel, |(_, t)| t);
    under_context(tail, context)
}

/// The render version folders and the check renders' movies a scan found
/// (`list_units`). An error where Python's raises: a file named as a bare
/// version (`render/.../v0001`).
fn list_units(inventory: &Inventory) -> Result<(Units, Vec<&Item>), String> {
    let mut units = Units::default();
    let mut movies = Vec::new();
    for item in &inventory.items {
        if item.root != "render" || item.kind != Kind::Render {
            continue;
        }
        let Some(unit_rel) = &item.unit else { continue };
        let parts: Vec<&str> = item.rel.split('/').collect();
        if parts[0] == MOVIES && *unit_rel == item.rel && !item.published {
            movies.push(item);
            continue;
        }
        if parts[0] != AREA || !is_valid_version_name(unit_rel.rsplit('/').next().unwrap_or("")) {
            continue;
        }
        if !units.map.contains_key(unit_rel) {
            units.order.push(unit_rel.clone());
            units.map.insert(
                unit_rel.clone(),
                Unit {
                    rel: unit_rel.clone(),
                    code: item.version.unwrap_or(0),
                    size: 0,
                    published: true,
                    has_context: false,
                    marked: false,
                    frames: Vec::new(),
                },
            );
        }
        let unit = units.map.get_mut(unit_rel).expect("just added");
        unit.size += item.size;
        unit.published = unit.published && item.published;
        let rest = &parts[unit_rel.split('/').count()..];
        if rest == ["context.json"] {
            unit.has_context = true;
        } else if rest == [KEEP_FILE] {
            unit.marked = true;
        } else {
            let last = rest.last().ok_or_else(|| format!("IndexError: list index out of range ({})", item.rel))?;
            if last.to_lowercase().ends_with(".exr") {
                let folder = if rest.len() > 1 { rest[0].to_string() } else { String::new() };
                match unit.frames.iter_mut().find(|(f, _)| *f == folder) {
                    Some((_, n)) => *n += 1,
                    None => unit.frames.push((folder, 1)),
                }
            }
        }
    }
    Ok((units, movies))
}

/// `(entity parts, version name, department or None, layer or None)`.
pub type Pin = (Vec<String>, String, Option<String>, Option<String>);

/// `_VERSION_TOKEN.findall`: `(?<![A-Za-z0-9])v\d{4,}(?![0-9])`.
fn version_tokens(value: &str) -> Vec<String> {
    let chars: Vec<char> = value.chars().collect();
    let mut out = Vec::new();
    let mut at = 0;
    while at < chars.len() {
        if chars[at] == 'v' && (at == 0 || !chars[at - 1].is_ascii_alphanumeric()) {
            let digits = chars[at + 1..].iter().take_while(|c| py::is_digit(**c)).count();
            if digits >= 4 {
                out.push(chars[at..at + 1 + digits].iter().collect());
                at += 1 + digits;
                continue;
            }
        }
        at += 1;
    }
    out
}

/// `_VERSION_PARM`: `^(?:version\d*|pins)$`.
fn is_version_parm(name: &str) -> bool {
    name == "pins" || name.strip_prefix("version").is_some_and(|rest| rest.chars().all(py::is_digit))
}

/// The render versions a workfile's text names (`renders.hip_pins`): every
/// exact version in a node's `version` or `pins` parm, of every entity that
/// node names. Any department and layer of it.
pub fn hip_pins(text: &str, own: Option<&[String]>) -> HashSet<Pin> {
    let mut pins = HashSet::new();
    let mut entities: Vec<Vec<String>> = Vec::new();
    let mut versions: Vec<String> = Vec::new();
    let flush = |pins: &mut HashSet<Pin>, entities: &[Vec<String>], versions: &[String]| {
        for entity in entities {
            for version in versions {
                pins.insert((entity.clone(), version.clone(), None, None));
            }
        }
    };
    for line in py::splitlines(text) {
        if line.starts_with('}') || line.starts_with('{') {
            flush(&mut pins, &entities, &versions);
            entities.clear();
            versions.clear();
            continue;
        }
        let Some((name, value)) = hip::parm(line) else { continue };
        for entity in hip::entities(value) {
            let entity: Vec<String> = entity.trim_matches('/').split('/').map(String::from).collect();
            if !entities.contains(&entity) {
                entities.push(entity);
            }
        }
        if name.to_lowercase().contains("entity")
            && value.starts_with("from_context")
            && let Some(own) = own
            && !entities.iter().any(|e| e == own)
        {
            entities.push(own.to_vec());
        }
        if is_version_parm(name) {
            for token in version_tokens(value) {
                if !versions.contains(&token) {
                    versions.push(token);
                }
            }
        }
    }
    flush(&mut pins, &entities, &versions);
    pins
}

/// Every string in a JSON value, depth first.
fn strings(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::String(s) => out.push(s.clone()),
        Value::Object(map) => map.values().for_each(|v| strings(v, out)),
        Value::Array(list) => list.iter().for_each(|v| strings(v, out)),
        _ => {}
    }
}

/// The takes an edit document pins (`renders.edit_pins`).
pub fn edit_pins(data: &Value) -> HashSet<Pin> {
    fn walk(value: &Value, pins: &mut HashSet<Pin>) {
        match value {
            Value::Object(map) => {
                if let (Some(Value::String(shot)), Some(take @ Value::Object(fields))) = (map.get("shot"), map.get("take"))
                    && let Some(rest) = shot.strip_prefix("entity:/")
                {
                    let entity: Vec<String> = rest.split('/').filter(|p| !p.is_empty()).map(String::from).collect();
                    let text = |key: &str| fields.get(key).and_then(Value::as_str).map(String::from);
                    let (department, layer) = (text("department"), text("layer"));
                    let mut found = Vec::new();
                    if let Some(version) = take.get("version") {
                        strings(version, &mut found);
                    }
                    for version in found {
                        if is_valid_version_name(&version) {
                            pins.insert((entity.clone(), version, department.clone(), layer.clone()));
                        }
                    }
                }
                map.values().for_each(|v| walk(v, pins));
            }
            Value::Array(list) => list.iter().for_each(|v| walk(v, pins)),
            _ => {}
        }
    }
    let mut pins = HashSet::new();
    walk(data, &mut pins);
    pins
}

/// `_PATH.finditer`: `(?<![A-Za-z0-9_])render/[^"'\s<>|*?]+?/v\d{4,}(?![0-9])`.
fn render_paths(flat: &str) -> Vec<String> {
    let chars: Vec<char> = flat.chars().collect();
    let middle_ok = |c: char| !(py::is_space(c) || matches!(c, '"' | '\'' | '<' | '>' | '|' | '*' | '?'));
    let head: Vec<char> = "render/".chars().collect();
    let mut out = Vec::new();
    let mut at = 0;
    'scan: while at < chars.len() {
        let behind_ok = at == 0 || !(chars[at - 1].is_ascii_alphanumeric() || chars[at - 1] == '_');
        if behind_ok && chars[at..].starts_with(&head) {
            let middle_start = at + head.len();
            // The shortest middle that `/v` and four or more digits follow.
            let mut end = middle_start;
            while end < chars.len() && middle_ok(chars[end]) {
                end += 1;
                if end + 1 < chars.len() && chars[end] == '/' && chars[end + 1] == 'v' {
                    let digits = chars[end + 2..].iter().take_while(|c| py::is_digit(**c)).count();
                    if digits >= 4 {
                        let stop = end + 2 + digits;
                        out.push(chars[at..stop].iter().collect());
                        at = stop;
                        continue 'scan;
                    }
                }
            }
        }
        at += 1;
    }
    out
}

/// The render versions the paths in `text` lead into (`path_units`).
fn path_units(text: &str, units: &Units) -> HashSet<String> {
    let mut flat = String::with_capacity(text.len());
    for c in text.chars() {
        let c = if c == '\\' { '/' } else { c };
        if c == '/' && flat.ends_with('/') {
            continue;
        }
        flat.push(c);
    }
    let mut found = HashSet::new();
    for mut candidate in render_paths(&flat) {
        loop {
            if units.contains(&candidate) {
                found.insert(candidate);
                break;
            }
            let Some(at) = candidate.find("/render/") else { break };
            candidate = candidate[at + 1..].to_string();
        }
    }
    found
}

/// `(shot key, layer, render department, version)` for every entry of the
/// edit root's `manifest.json` (`manifest_pins`).
fn manifest_pins(data: Option<&Value>) -> Vec<(String, String, String, String)> {
    let mut out = Vec::new();
    let Some(Value::Object(shots)) = data else { return out };
    for (shot, layers) in shots {
        let Value::Object(layers) = layers else { continue };
        for (layer, aovs) in layers {
            let Value::Object(aovs) = aovs else { continue };
            for entry in aovs.values() {
                if let Value::Array(entry) = entry
                    && entry.len() == 3
                    && let (Some(_), Some(department), Some(version)) = (entry[0].as_str(), entry[1].as_str(), entry[2].as_str())
                {
                    out.push((shot.clone(), layer.clone(), department.to_string(), version.to_string()));
                }
            }
        }
    }
    out
}

/// A JSON file, None when it cannot be read (`_read_json`).
pub fn read_json(path: &Path) -> Option<Value> {
    let bytes = std::fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// Every edit document: the working versions (the `edits` root) and the
/// published ones (`export/edits`), with their `.otio`.
fn edit_files(roots: &ProjectRoots) -> Vec<PathBuf> {
    let mut folders = Vec::new();
    if let Some(edits) = roots.path("edits") {
        folders.push(edits.to_path_buf());
    }
    if let Some(export) = roots.path("export") {
        folders.push(export.join("edits"));
    }
    let mut files = Vec::new();
    for folder in folders {
        fsx::os_walk(&folder, |dir| {
            dir.dirs.retain(|d| !matches!(d.as_str(), "_deleted" | "_context" | "media"));
            for name in dir.files {
                let lower = name.to_lowercase();
                if lower.ends_with(".json") || lower.ends_with(".otio") {
                    files.push(dir.path.join(name));
                }
            }
        });
    }
    files
}

struct Index<'a> {
    units: &'a Units,
    by_version: HashMap<String, Vec<&'a Unit>>,
}

impl<'a> Index<'a> {
    fn new(units: &'a Units) -> Self {
        let mut by_version: HashMap<String, Vec<&Unit>> = HashMap::new();
        for unit in units.values() {
            by_version.entry(unit.name().to_string()).or_default().push(unit);
        }
        Index { units, by_version }
    }

    fn matching(&self, pin: &Pin) -> Vec<String> {
        let (entity, version, department, layer) = pin;
        let mut out = Vec::new();
        for unit in self.by_version.get(version).into_iter().flatten() {
            let parts = unit.parts();
            let n = parts.len();
            let lo = (1 + entity.len()).min(n);
            let hi = n.saturating_sub(1).max(lo);
            let middle = &parts[lo..hi];
            let head = &parts[1.min(n)..lo];
            if head != entity.as_slice() {
                continue;
            }
            if let Some(department) = department
                && !middle.contains(department)
            {
                continue;
            }
            if let Some(layer) = layer
                && !middle.contains(layer)
            {
                continue;
            }
            out.push(unit.rel.clone());
        }
        let _ = self.units;
        out
    }
}

/// `(named by a latest workfile, named by an edit)`: version rels.
fn pinned(
    roots: &ProjectRoots,
    units: &Units,
    workfiles: &Workfiles,
    notes: &mut Vec<String>,
    errors: &mut Vec<String>,
) -> (HashSet<String>, HashSet<String>) {
    let index = Index::new(units);
    let mut by_workfile = HashSet::new();
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
        for pin in facts.renders.iter().flatten() {
            by_workfile.extend(index.matching(pin));
        }
    }
    notes.push(format!("Read {read} latest workfile(s) for the render versions they name."));
    if unreadable > 0 {
        notes.push(format!(
            "{unreadable} latest workfile(s) are .hiplc/.hipnc in a form that cannot be read: render versions they name are NOT protected."
        ));
    }

    let mut by_edit = HashSet::new();
    let documents = edit_files(roots);
    for path in &documents {
        let text = match hip::read_text(path) {
            Ok(text) => text,
            Err(error) => {
                errors.push(format!("{}: {error}", path.display()));
                continue;
            }
        };
        by_edit.extend(path_units(&text, units));
        let Ok(data) = serde_json::from_str::<Value>(&text) else { continue };
        for pin in edit_pins(&data) {
            by_edit.extend(index.matching(&pin));
        }
    }
    let manifest = roots.path("edit").and_then(|edit| read_json(&edit.join("manifest.json")));
    for (shot, layer, department, version) in manifest_pins(manifest.as_ref()) {
        // The shot's URI (`entity:/shots/<sequence>/<shot>`), as its folders.
        let tail = shot.split_once(':').map_or(shot.as_str(), |(_, t)| t);
        let segments: Vec<&str> = tail.split(['\\', '/']).filter(|p| !p.is_empty()).collect();
        let matched: Vec<String> = index
            .by_version
            .get(&version)
            .into_iter()
            .flatten()
            .filter(|unit| {
                let parts = unit.parts();
                parts.len() >= 3 && parts[parts.len() - 3] == department && parts[parts.len() - 2] == layer
            })
            .map(|unit| unit.rel.clone())
            .collect();
        let of_shot: Vec<String> = matched
            .iter()
            .filter(|rel| {
                let parts: Vec<&str> = rel.split('/').collect();
                let (lo, hi) = (1.min(parts.len()), parts.len().saturating_sub(3).max(1.min(parts.len())));
                parts[lo..hi] == segments[..]
            })
            .cloned()
            .collect();
        by_edit.extend(if of_shot.is_empty() { matched } else { of_shot });
    }
    if !documents.is_empty() || manifest.as_ref().is_some_and(py::truthy) {
        notes.push(format!("Read {} edit document(s) for the render versions they name.", documents.len()));
    }
    (by_workfile, by_edit)
}

/// The frames a version's `context.json` says it holds; None without a
/// record, 0 for a multi-range render (never complete). An error where
/// Python's `int()` raises past its `except` (an infinite number).
fn expected_frames(context: Option<&Value>) -> Result<Option<i128>, String> {
    let Some(Value::Object(context)) = context else { return Ok(None) };
    if context.get("frame_ranges").is_some_and(py::truthy) {
        return Ok(Some(0));
    }
    // In Python's order: the first that raises decides.
    let Some(first) = context.get("first_frame") else { return Ok(None) };
    let Some(first) = py_int(first)? else { return Ok(None) };
    let Some(last) = context.get("last_frame") else { return Ok(None) };
    let Some(last) = py_int(last)? else { return Ok(None) };
    let step = match context.get("step_size") {
        Some(value) if py::truthy(value) => py_int(value)?,
        _ => Some(1),
    };
    let Some(step) = step else { return Ok(None) };
    let step = step.max(1);
    let span = last + 1 - first;
    Ok(Some(if span <= 0 { 0 } else { (span + step - 1) / step }))
}

/// Python's `int(value)` on a JSON value: None where it raises
/// `TypeError`/`ValueError`; an error where it raises `OverflowError`.
fn py_int(value: &Value) -> Result<Option<i128>, String> {
    Ok(match value {
        Value::Bool(b) => Some(i128::from(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i128::from(i))
            } else if let Some(u) = n.as_u64() {
                Some(i128::from(u))
            } else {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f.is_nan() {
                    None
                } else if f.is_infinite() {
                    return Err("OverflowError: cannot convert float infinity to integer".into());
                } else {
                    Some(f.trunc() as i128)
                }
            }
        }
        Value::String(s) => py::int_str(s),
        _ => None,
    })
}

/// `{rel: (prune, reason)}` for every version (`renders.decide`). Always
/// the whole render root: what names a version may be in another shot.
fn decide(
    root: &Path,
    units: &Units,
    settings: &Settings,
    by_workfile: &HashSet<String>,
    by_edit: &HashSet<String>,
    now: f64,
    watch: &Watch<'_>,
) -> Result<HashMap<String, (bool, String)>, String> {
    let keep = settings.keep_renders;
    // Every version's record, read `watch.threads` at a time up front (the
    // same files the verdicts would read one by one, and a few more).
    let recorded: Vec<&Unit> = units.values().filter(|u| u.has_context).collect();
    let meter = watch.meter(Some(CATEGORY.key), "Reading render records", Some(recorded.len()));
    let read = fsx::par_map(&recorded, watch.threads, |unit| {
        if watch.cancelled() {
            return None;
        }
        let mut path = root.to_path_buf();
        path.extend(unit.parts());
        let record = read_json(&path.join("context.json"));
        meter.add(1, 0);
        record
    });
    meter.finish();
    watch.check()?;
    let mut contexts: HashMap<String, Option<Value>> = recorded.iter().map(|u| u.rel.clone()).zip(read).collect();
    let mut context = |unit: &Unit| -> Option<Value> {
        contexts
            .entry(unit.rel.clone())
            .or_insert_with(|| {
                if !unit.has_context {
                    return None;
                }
                let mut path = root.to_path_buf();
                path.extend(unit.parts());
                read_json(&path.join("context.json"))
            })
            .clone()
    };

    let mut verdicts: HashMap<String, (bool, String)> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    let mut series: Vec<(String, Vec<&Unit>)> = Vec::new();
    for unit in units.values() {
        match series.iter_mut().find(|(s, _)| s == unit.series()) {
            Some((_, group)) => group.push(unit),
            None => series.push((unit.series().to_string(), vec![unit])),
        }
    }
    for (_, mut group) in series {
        group.sort_by_key(|u| u.code);
        let highest = group[group.len() - 1].code;
        let fullest = group.iter().filter(|u| u.published).map(|u| u.frame_count()).max().unwrap_or(0) as i128;
        // The newest `keep` complete versions: read from the newest down.
        let mut complete = 0;
        let mut threshold = None;
        for unit in group.iter().rev() {
            if !unit.published {
                continue;
            }
            let expected = expected_frames(context(unit).as_ref())?.unwrap_or(fullest);
            if expected != 0 && unit.frame_count() as i128 >= expected {
                complete += 1;
                if complete == keep {
                    threshold = Some(unit.code);
                    break;
                }
            }
        }
        for unit in &group {
            let verdict = if unit.marked {
                (false, reason_keep_marked())
            } else if by_workfile.contains(&unit.rel) {
                (false, KEEP_WORKFILE.to_string())
            } else if by_edit.contains(&unit.rel) {
                (false, KEEP_EDIT.to_string())
            } else if unit.published && threshold.is_none_or(|t| unit.code >= t) {
                (false, reason_keep_newest(keep))
            } else if unit.code == highest {
                (false, KEEP_HIGHEST.to_string())
            } else if !unit.published {
                let mut path = root.to_path_buf();
                path.extend(unit.parts());
                match fsx::mtime(&path.join("context.json")) {
                    Some(stamp) if age_days(stamp, now) >= settings.check_render_days => {
                        (true, reason_prune_check(settings.check_render_days))
                    }
                    _ => (false, reason_keep_check(settings.check_render_days)),
                }
            } else {
                (true, reason_prune_old(keep))
            };
            if !verdicts.contains_key(&unit.rel) {
                order.push(unit.rel.clone());
            }
            verdicts.insert(unit.rel.clone(), verdict);
        }
    }

    // What the kept composite versions read stays, and what those read.
    let mut todo: Vec<String> = order.iter().filter(|rel| !verdicts[*rel].0).cloned().collect();
    let mut seen: HashSet<String> = todo.iter().cloned().collect();
    while let Some(rel) = todo.pop() {
        let Some(unit) = units.get(&rel) else { continue };
        let record = match context(unit) {
            Some(Value::Object(record)) => record,
            // `(context or {}).get`: a record that is a non-empty list or
            // text has no `.get`, and Python raises.
            Some(other) if py::truthy(&other) => {
                return Err(format!("AttributeError: the context.json of {} is not an object", unit.rel));
            }
            _ => continue,
        };
        let Some(Value::Object(reads)) = record.get("reads") else { continue };
        let parts = unit.parts();
        let entity = &parts[..parts.len().saturating_sub(3)];
        for (channel, read) in reads {
            let Value::Object(read) = read else { continue };
            let field = |key: &str| read.get(key).map_or_else(|| "None".to_string(), py::str_of);
            let mut target: Vec<String> = entity.to_vec();
            target.extend([field("department"), channel.clone(), field("version")]);
            let target = target.join("/");
            if units.contains(&target) && !seen.contains(&target) {
                seen.insert(target.clone());
                todo.push(target.clone());
                if verdicts[&target].0 {
                    verdicts.insert(target, (false, KEEP_READ.to_string()));
                }
            }
        }
    }

    let going: Vec<&String> = order.iter().filter(|rel| verdicts[*rel].0 && in_context(rel, settings.context())).collect();
    let meter = watch.meter(Some(CATEGORY.key), "Checking recent writes", Some(going.len()));
    let recent = fsx::par_map(&going, watch.threads, |rel| {
        let mut path = root.to_path_buf();
        path.extend(rel.split('/'));
        let recent = !watch.cancelled() && fsx::recently_written(&path, now, RECENT_DAYS);
        if !recent {
            // Marked to go and not written to since: found.
            meter.found(units.get(rel).map_or(0, |u| u.size));
        }
        meter.add(1, 0);
        recent
    });
    meter.finish();
    watch.check()?;
    for (rel, recent) in going.into_iter().zip(recent) {
        if recent {
            verdicts.insert(rel.clone(), (false, reason_keep_recent()));
        }
    }
    Ok(verdicts)
}

/// The check render versions the review movie `item` is of
/// (`playblast/<entity>/<dept>[/<layer>]/<stem>_vNNNN.mp4` beside
/// `render/<entity>/<dept>/<layer>/vNNNN`).
fn movie_units(item: &Item, units: &Units) -> Vec<String> {
    let parts: Vec<&str> = item.rel.split('/').collect();
    let version_name = file_version(parts[parts.len() - 1]);
    let middle: Vec<&str> = parts[1..parts.len() - 1].to_vec();
    let mut out = Vec::new();
    for department in [&middle[..], &middle[..middle.len().saturating_sub(1)]] {
        let mut prefix_parts = vec![AREA];
        prefix_parts.extend(department.iter().copied());
        let prefix = format!("{}/", prefix_parts.join("/"));
        for unit in units.values() {
            if Some(unit.name()) == version_name.as_deref()
                && !unit.published
                && unit.rel.starts_with(&prefix)
                && unit.parts().len() == department.len() + 3
            {
                out.push(unit.rel.clone());
            }
        }
    }
    out
}

/// The category's plan (`renders.plan`).
pub fn plan(
    roots: &ProjectRoots,
    inventory: Option<&Inventory>,
    settings: &Settings,
    now: f64,
    workfiles: &Workfiles,
    watch: &Watch<'_>,
) -> Result<CategoryPlan, String> {
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "version");
    out.payload = Payload::Renders(None);
    let (Some(render_root), Some(inventory)) = (roots.path("render"), inventory) else { return Ok(out) };
    out.payload = Payload::Renders(Some(render_root.to_path_buf()));
    let (units, movies) = list_units(inventory)?;
    let mut notes = Vec::new();
    let mut errors = Vec::new();
    let (by_workfile, by_edit) = pinned(roots, &units, workfiles, &mut notes, &mut errors);
    out.notes = notes;
    out.errors = errors;
    let verdicts = decide(render_root, &units, settings, &by_workfile, &by_edit, now, watch)?;
    let mut rels: Vec<&String> = units.order.iter().collect();
    rels.sort();
    for rel in rels {
        if !in_context(rel, settings.context()) {
            continue;
        }
        let unit = &units.map[rel];
        let (prune, reason) = verdicts[rel].clone();
        let parts = unit.parts();
        let mut path = render_root.to_path_buf();
        path.extend(&parts);
        let group = parts.iter().skip(1).take(3).cloned().collect::<Vec<_>>().join("/");
        out.entries.push(Entry { path, label: rel.clone(), group, size: unit.size, prune, reason });
    }
    let mut movies = movies;
    movies.sort_by(|a, b| a.rel.cmp(&b.rel));
    for item in movies {
        if !in_context(&item.rel, settings.context()) {
            continue;
        }
        let of = movie_units(item, &units);
        let goes = !of.is_empty() && of.iter().all(|rel| verdicts[rel].0);
        let mut path = render_root.to_path_buf();
        path.extend(item.rel.split('/'));
        let group = item.rel.split('/').skip(1).take(3).collect::<Vec<_>>().join("/");
        out.entries.push(Entry {
            path,
            label: item.rel.clone(),
            group,
            size: item.size,
            prune: goes,
            reason: if goes { PRUNE_CHECK_MOVIE } else { KEEP_CHECK_MOVIE }.to_string(),
        });
    }
    let root_text = render_root.display().to_string();
    out.errors.extend(inventory.errors.iter().filter(|e| e.contains(&root_text)).cloned());
    Ok(out)
}

/// Delete the render versions and movies marked to go, each still one,
/// under the render root, unmarked and not written to since (`renders.apply`).
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let root = match &category.payload {
        Payload::Renders(root) => root.clone(),
        _ => None,
    };
    let still_ok = |entry: &Entry| -> Option<String> {
        let parts: Vec<&str> = entry.label.split('/').collect();
        if parts.contains(&"_deleted") || parts.len() < 4 {
            return Some("not a render version".into());
        }
        let path = &entry.path;
        if parts[0] == AREA {
            let link = std::fs::symlink_metadata(path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
            if !is_valid_version_name(parts[parts.len() - 1]) || !path.is_dir() || link {
                return Some("not a render version folder".into());
            }
            if path.join(KEEP_FILE).exists() {
                return Some(format!("it holds a {KEEP_FILE} file"));
            }
            if fsx::recently_written(path, fsx::now(), RECENT_DAYS) {
                return Some("it was written to since it was listed".into());
            }
            return None;
        }
        let last = parts[parts.len() - 1].to_lowercase();
        if parts[0] == MOVIES && path.is_file() && (last.ends_with(".mp4") || last.ends_with(".mov")) {
            return None;
        }
        Some("not a render version".into())
    };
    let guarded = |entry: &Entry| -> Option<String> {
        let Some(root) = &root else { return Some("outside the render root".into()) };
        let mut expected = root.clone();
        expected.extend(entry.label.split('/'));
        let parent = entry.path.parent().unwrap_or(Path::new(""));
        if !fsx::same_path(&entry.path, &expected) || !fsx::within(parent, root) {
            return Some("outside the render root".into());
        }
        still_ok(entry)
    };
    delete_entries(category, log_path, steer, guarded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_and_paths() {
        assert_eq!(version_tokens("a v0001 xv0002 v00034 v001 :v12345"), vec!["v0001", "v00034", "v12345"]);
        assert!(is_version_parm("version"));
        assert!(is_version_parm("version12"));
        assert!(is_version_parm("pins"));
        assert!(!is_version_parm("version_x"));
        assert_eq!(render_paths("//S/x/render/shots/a/render/default/v0002/b/f.exr"), vec!["render/shots/a/render/default/v0002"]);
        assert_eq!(render_paths("xrender/a/v0001 render/b/v0002"), vec!["render/b/v0002"]);
        assert_eq!(render_paths("render/a/v001/v0003"), vec!["render/a/v001/v0003"]);
    }

    #[test]
    fn expected_frames_as_python() {
        let e = |v: serde_json::Value| expected_frames(Some(&v)).unwrap();
        assert_eq!(e(serde_json::json!({"first_frame": 1001, "last_frame": 1004})), Some(4));
        assert_eq!(e(serde_json::json!({"first_frame": 1001, "last_frame": 1004, "step_size": 2})), Some(2));
        assert_eq!(e(serde_json::json!({"first_frame": "1001", "last_frame": 1004.7, "step_size": 0})), Some(4));
        assert_eq!(e(serde_json::json!({"first_frame": 1001, "last_frame": 1004, "frame_ranges": [[1, 2]]})), Some(0));
        assert_eq!(e(serde_json::json!({"first_frame": 1001})), None);
        assert_eq!(e(serde_json::json!({"first_frame": 5, "last_frame": 1})), Some(0));
        assert_eq!(e(serde_json::json!({"first_frame": 1, "last_frame": 9, "step_size": -3})), Some(9));
    }
}
