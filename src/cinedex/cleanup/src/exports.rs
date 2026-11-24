//! Old export versions (`tumblepipe.prune_exports`, and
//! `tumblepipe.cleanup.exports` around it).
//!
//! Every export writes a new `vNNNN` folder and nothing removes the old
//! ones. A version folder goes only when it is finished (its
//! `context.json` is there), at least `keep` newer finished versions of
//! the same export exist, no current staged build names it (followed
//! through the `.usda` layers of what it names), and no latest workfile
//! pins it. A `.hiplc`/`.hipnc` is read as a `.hip` is, but one in a form
//! `hip::readable` does not know is not; the listing says how many there
//! were and that their pins are not protected.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use th_pipe::naming::is_valid_version_name;

use crate::core::{
    Category, CategoryPlan, Entry, Meter, Outcome, Payload, Settings, Steer, Watch, Workfiles, each_item, log_line, open_log, stamp_now,
    user,
};
use crate::fsx;
use crate::hip::{self, ROOT, STAGED};
use crate::inventory::{Inventory, Kind};
use crate::py;
use crate::roots::ProjectRoots;

pub const CATEGORY: Category = Category {
    key: "exports",
    title: "Old export versions",
    about: "export versions older than the newest few, that no staged build or latest workfile loads",
    purposes: &["export"],
};

const CONTEXTS: [&str; 2] = ["shots", "assets"];
const SKIP_TOP: &str = "other";

pub fn reason_prune(keep: i64) -> String {
    format!("older than the newest {keep} finished versions")
}
pub fn reason_keep_newest(keep: i64) -> String {
    format!("one of the newest {keep} finished versions")
}
pub const KEEP_UNFINISHED: &str = "unfinished (no context.json): may still be being written";
pub const KEEP_STAGED: &str = "a current staged build uses it";
pub const KEEP_WORKFILE: &str = "a latest workfile pins it";

/// One export version folder: `rel` under the export root, `series` the
/// folder holding its siblings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Version {
    pub rel: String,
    pub series: String,
    pub code: i128,
    pub finished: bool,
    pub size: u64,
}

impl Version {
    fn parts(&self) -> Vec<String> {
        self.rel.split('/').map(String::from).collect()
    }

    fn context(&self) -> &str {
        self.rel.split('/').next().unwrap_or("")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub version: Version,
    pub prune: bool,
    pub reason: String,
}

/// `prune_exports.Plan`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ExportPlan {
    pub export_path: PathBuf,
    pub keep: i64,
    pub verdicts: Vec<Verdict>,
    pub hips_read: usize,
    pub hips_unreadable: Vec<String>,
    pub errors: Vec<String>,
}

/// Every version folder under the export root, its exports and its staged
/// builds apart (`list_versions`); `other/` left out.
fn list_versions(export_path: &Path, inventory: &Inventory) -> (Vec<Version>, Vec<Version>) {
    let mut sizes: HashMap<String, u64> = HashMap::new();
    let mut info: HashMap<String, (Kind, String, i128, bool)> = HashMap::new();
    for item in &inventory.items {
        if item.root != "export" || !matches!(item.kind, Kind::Export | Kind::Staged) {
            continue;
        }
        let Some(unit) = &item.unit else { continue };
        if unit.split('/').next() == Some(SKIP_TOP) {
            continue;
        }
        *sizes.entry(unit.clone()).or_default() += item.size;
        info.insert(unit.clone(), (item.kind, item.series.clone().unwrap_or_default(), item.version.unwrap_or(0), item.complete));
    }
    let mut units: Vec<&String> = info.keys().collect();
    units.sort();
    let (mut exports, mut staged) = (Vec::new(), Vec::new());
    for unit in units {
        let (kind, series, code, complete) = info[unit].clone();
        if kind == Kind::Staged {
            let mut path = export_path.to_path_buf();
            path.extend(unit.split('/'));
            let finished = path.join("context.json").is_file();
            staged.push(Version { rel: unit.clone(), series, code, finished, size: sizes[unit] });
        } else {
            exports.push(Version { rel: unit.clone(), series, code, finished: complete, size: sizes[unit] });
        }
    }
    (exports, staged)
}

/// Versions grouped by series, in the order the series first come, each
/// sorted by number (stable).
fn by_series(versions: Vec<Version>) -> Vec<Vec<Version>> {
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, Vec<Version>> = HashMap::new();
    for version in versions {
        if !groups.contains_key(&version.series) {
            order.push(version.series.clone());
        }
        groups.entry(version.series.clone()).or_default().push(version);
    }
    order
        .into_iter()
        .map(|series| {
            let mut group = groups.remove(&series).unwrap_or_default();
            group.sort_by_key(|v| v.code);
            group
        })
        .collect()
}

/// The newest finished version of every staged series, else its newest.
fn current_staged(staged: Vec<Version>) -> Vec<Version> {
    by_series(staged)
        .into_iter()
        .filter_map(|group| {
            let done: Vec<&Version> = group.iter().filter(|v| v.finished).collect();
            done.last().copied().or(group.last()).cloned()
        })
        .collect()
}

type Unit = Vec<String>;

/// Every version folder reached from `start` through the `.usda` files in
/// it, followed transitively; only folders in `known` (`staged_closure`).
fn staged_closure(
    export_path: &Path,
    start: &HashSet<Unit>,
    known: &HashSet<Unit>,
    meter: &Meter<'_>,
    watch: &Watch<'_>,
) -> Result<HashSet<Unit>, String> {
    let mut seen: HashSet<Unit> = HashSet::new();
    let mut todo: Vec<Unit> = start.iter().filter(|u| known.contains(*u)).cloned().collect();
    while let Some(unit) = todo.pop() {
        if seen.contains(&unit) {
            continue;
        }
        watch.check()?;
        seen.insert(unit.clone());
        let mut folder = export_path.to_path_buf();
        folder.extend(&unit);
        let mut failure = None;
        fsx::os_walk(&folder, |dir| {
            if failure.is_some() {
                return;
            }
            let mut rel_dir = unit.clone();
            if let Ok(rest) = dir.path.strip_prefix(&folder) {
                rel_dir.extend(rest.components().map(|c| c.as_os_str().to_string_lossy().into_owned()));
            }
            for name in dir.files {
                if !name.to_lowercase().ends_with(".usda") {
                    continue;
                }
                let Ok(data) = std::fs::read(dir.path.join(name)) else { continue };
                meter.add(1, data.len() as u64);
                for reference in hip::usda_refs(&data) {
                    match hip::ref_units(&reference, &rel_dir) {
                        Ok(targets) => {
                            for target in targets {
                                if known.contains(&target) && !seen.contains(&target) {
                                    todo.push(target);
                                }
                            }
                        }
                        Err(error) => {
                            failure = Some(error);
                            return;
                        }
                    }
                }
            }
        });
        if let Some(error) = failure {
            return Err(error);
        }
    }
    Ok(seen)
}

/// The last run of digits in a parm name: the row of a multi-row parm
/// (`entity2`, `layer_3_version`); None for a plain one (`_ROW`).
fn row(name: &str) -> Option<i128> {
    let chars: Vec<char> = name.chars().collect();
    let end = chars.iter().rposition(|c| py::is_digit(*c))? + 1;
    let start = chars[..end].iter().rposition(|c| !py::is_digit(*c)).map_or(0, |at| at + 1);
    py::int_digits(&chars[start..end].iter().collect::<String>())
}

/// `_LABEL`: `_label\d*$|^layer_\d+_version$`, searched: parms that only
/// show a value.
fn is_label(name: &str) -> bool {
    if let Some(rest) = name.strip_prefix("layer_").and_then(|r| r.strip_suffix("_version"))
        && !rest.is_empty()
        && rest.chars().all(py::is_digit)
    {
        return true;
    }
    let chars: Vec<char> = name.chars().collect();
    let digits = chars.iter().rev().take_while(|c| py::is_digit(**c)).count();
    let head: String = chars[..chars.len() - digits].iter().collect();
    head.ends_with("_label")
}

/// `(entity parts, version name, department or None)`.
pub type Pin = (Vec<String>, String, Option<String>);

/// What a workfile's text pins (`hip::readable`), and the exact version folders its URIs name
/// (`prune_exports.hip_pins`). More is kept than strictly needed (the
/// version of that entity in every channel), never less.
pub fn hip_pins(text: &str, own: Option<&[String]>) -> Result<(HashSet<Pin>, HashSet<Unit>), String> {
    let mut pins: HashSet<Pin> = HashSet::new();
    let mut exact: HashSet<Unit> = HashSet::new();
    // Keyed by row; insertion order kept so the flush is Python's.
    let mut entities: Vec<(Option<i128>, Vec<Vec<String>>)> = Vec::new();
    let mut versions: Vec<(Option<i128>, Vec<String>)> = Vec::new();
    let mut departments: HashMap<Option<i128>, String> = HashMap::new();

    fn add<T: PartialEq>(map: &mut Vec<(Option<i128>, Vec<T>)>, row: Option<i128>, value: T) {
        match map.iter_mut().find(|(r, _)| *r == row) {
            Some((_, values)) => {
                if !values.contains(&value) {
                    values.push(value);
                }
            }
            None => map.push((row, vec![value])),
        }
    }

    let flush = |pins: &mut HashSet<Pin>,
                 entities: &Vec<(Option<i128>, Vec<Vec<String>>)>,
                 versions: &Vec<(Option<i128>, Vec<String>)>,
                 departments: &HashMap<Option<i128>, String>| {
        let everyone: Vec<Vec<String>> = entities.iter().flat_map(|(_, e)| e.iter().cloned()).collect();
        let get = |row: Option<i128>| entities.iter().find(|(r, _)| *r == row).map(|(_, e)| e.clone());
        for (row, names) in versions {
            let targets = get(*row).or_else(|| get(None)).unwrap_or_else(|| everyone.clone());
            let department = departments.get(row).cloned();
            for entity in &targets {
                for version in names {
                    pins.insert((entity.clone(), version.clone(), department.clone()));
                }
            }
        }
    };

    for line in py::splitlines(text) {
        if line.starts_with('}') || line.starts_with('{') {
            flush(&mut pins, &entities, &versions, &departments);
            entities.clear();
            versions.clear();
            departments.clear();
            continue;
        }
        let Some((name, value)) = hip::parm(line) else { continue };
        if is_label(name) {
            continue;
        }
        let row = row(name);
        for uri in hip::pinned_uris(value) {
            if uri.contains("version=") {
                exact.extend(hip::ref_units(uri, &[])?);
            }
        }
        for entity in hip::entities(value) {
            add(&mut entities, row, entity.trim_matches('/').split('/').map(String::from).collect());
        }
        let lowered = name.to_lowercase();
        if lowered.contains("entity")
            && value.starts_with("from_context")
            && let Some(own) = own
        {
            add(&mut entities, row, own.to_vec());
        }
        if (lowered.contains("department") || lowered.starts_with("dept"))
            && !value.is_empty()
            && value.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && value != "from_context"
        {
            departments.insert(row, value.to_string());
        }
        if is_valid_version_name(value) {
            add(&mut versions, row, value.to_string());
        }
    }
    flush(&mut pins, &entities, &versions, &departments);
    Ok((pins, exact))
}

fn in_context(version: &Version, context: Option<&str>) -> bool {
    if !CONTEXTS.contains(&version.context()) {
        return false;
    }
    let Some(context) = context else { return true };
    let prefix = context.trim_matches('/');
    version.rel == prefix || version.rel.starts_with(&format!("{prefix}/"))
}

/// A verdict for every export version of the shots and assets under
/// `context` (`prune_exports.make_plan`).
pub fn make_export_plan(
    roots: &ProjectRoots,
    keep: i64,
    context: Option<&str>,
    inventory: &Inventory,
    workfiles: &Workfiles,
    watch: &Watch<'_>,
) -> Result<ExportPlan, String> {
    let export_path = roots.path("export").ok_or("the project has no export root")?.to_path_buf();
    let (exports, staged) = list_versions(&export_path, inventory);
    let mut plan = ExportPlan { export_path: export_path.clone(), keep, errors: inventory.errors.clone(), ..ExportPlan::default() };
    let known: HashSet<Unit> = exports.iter().chain(staged.iter()).map(Version::parts).collect();
    let start: HashSet<Unit> = current_staged(staged).iter().map(Version::parts).collect();

    let mut pinned_by_workfile: HashSet<Unit> = HashSet::new();
    let mut by_entity_version: HashMap<(Vec<String>, String), Vec<Unit>> = HashMap::new();
    for unit in &known {
        for depth in 1..unit.len() {
            by_entity_version.entry((unit[..depth].to_vec(), unit[unit.len() - 1].clone())).or_default().push(unit.clone());
        }
    }
    for read in &workfiles.list {
        let workfile = &read.workfile;
        let file_name = workfile.path.file_name().map(fsx::name_of).unwrap_or_default();
        let facts = match &read.read {
            None => {
                plan.hips_unreadable.push(format!("{}/{}/{}", workfile.purpose, workfile.parts.join("/"), file_name));
                continue;
            }
            Some(Err(error)) => {
                plan.errors.push(error.clone());
                continue;
            }
            Some(Ok(facts)) => facts,
        };
        plan.hips_read += 1;
        let (pins, exact) = facts.exports.clone().ok_or("the workfiles were read without the exports' pins")??;
        for (entity, version, department) in pins {
            for unit in by_entity_version.get(&(entity.clone(), version)).into_iter().flatten() {
                // A department narrows it to that department's layers
                // (`root`: the shot's root layer), but never away from a
                // staged build: import_shot's version is its staged
                // build's, set beside the department it works in.
                let within = &unit[entity.len()..unit.len() - 1];
                let has = |name: &str| within.iter().any(|p| p == name);
                let keep = match department.as_deref() {
                    None => true,
                    Some(department) => has(department) || has(STAGED) || (department == "root" && has(ROOT)),
                };
                if keep {
                    pinned_by_workfile.insert(unit.clone());
                }
            }
        }
        pinned_by_workfile.extend(exact.into_iter().filter(|u| known.contains(u)));
    }
    let meter = watch.meter(Some(CATEGORY.key), "Following layers", None);
    let staged_used = staged_closure(&export_path, &start, &known, &meter, watch)?;
    let workfile_used = staged_closure(&export_path, &pinned_by_workfile, &known, &meter, watch)?;
    meter.finish();

    let picked: Vec<Version> = exports.into_iter().filter(|v| in_context(v, context)).collect();
    for series in by_series(picked) {
        let finished: Vec<&Version> = series.iter().filter(|v| v.finished).collect();
        let take = usize::try_from(keep).unwrap_or(0).min(finished.len());
        let newest: HashSet<&str> = finished[finished.len() - take..].iter().map(|v| v.rel.as_str()).collect();
        for version in &series {
            let unit = version.parts();
            let (prune, reason) = if !version.finished {
                (false, KEEP_UNFINISHED.to_string())
            } else if newest.contains(version.rel.as_str()) {
                (false, reason_keep_newest(keep))
            } else if staged_used.contains(&unit) {
                (false, KEEP_STAGED.to_string())
            } else if workfile_used.contains(&unit) {
                (false, KEEP_WORKFILE.to_string())
            } else {
                (true, reason_prune(keep))
            };
            if prune {
                // Only known once the layers are followed: found here.
                meter.found(version.size);
            }
            plan.verdicts.push(Verdict { version: version.clone(), prune, reason });
        }
    }
    Ok(plan)
}

/// The category's plan (`cleanup.exports.plan`).
pub fn plan(
    roots: &ProjectRoots,
    inventory: Option<&Inventory>,
    settings: &Settings,
    workfiles: &Workfiles,
    watch: &Watch<'_>,
) -> Result<CategoryPlan, String> {
    let empty = Inventory::default();
    let export_plan = make_export_plan(roots, settings.keep_exports, settings.context(), inventory.unwrap_or(&empty), workfiles, watch)?;
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "version");
    for verdict in &export_plan.verdicts {
        let version = &verdict.version;
        let mut path = export_plan.export_path.clone();
        path.extend(version.rel.split('/'));
        let group = version.series.split('/').take(3).collect::<Vec<_>>().join("/");
        out.entries.push(Entry {
            path,
            label: version.rel.clone(),
            group,
            size: version.size,
            prune: verdict.prune,
            reason: verdict.reason.clone(),
        });
    }
    out.notes.push(format!("Read {} latest workfile(s) for pinned versions.", export_plan.hips_read));
    if !export_plan.hips_unreadable.is_empty() {
        out.notes.push(format!(
            "{} latest workfile(s) are .hiplc/.hipnc in a form that cannot be read: versions only they load are not kept for them.",
            export_plan.hips_unreadable.len()
        ));
    }
    out.errors = export_plan.errors.clone();
    out.payload = Payload::Exports(export_plan);
    Ok(out)
}

/// Delete the version folders the export plan marks to go, each checked
/// again just before: still a finished export version folder under the
/// export root, outside `_staged` and `other` (`prune_exports.apply`).
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let Payload::Exports(plan) = &category.payload else { return Ok((0, 0, Vec::new())) };
    let export_path = fsx::resolve(&plan.export_path);
    let mut log = open_log(log_path)?;
    log_line(&mut log, &format!("# prune exports {} keep {} by {}", stamp_now(), plan.keep, user()))?;
    each_item(steer, &category.key, plan.verdicts.iter().filter(|v| v.prune), |verdict| {
        let parts = verdict.version.parts();
        let mut path = plan.export_path.clone();
        path.extend(&parts);
        let ok = parts.len() >= 3
            && CONTEXTS.contains(&parts[0].as_str())
            && !parts.iter().any(|p| p == STAGED)
            && parts[0] != SKIP_TOP
            && is_valid_version_name(&parts[parts.len() - 1])
            && hip::unit_of(&parts).as_ref() == Some(&parts)
            && fsx::resolve(&path).parent().is_some_and(|parent| fsx::is_relative_to(parent, &export_path))
            && path.join("context.json").is_file();
        if !ok {
            return Ok(Err(format!("{}: not a finished export version folder, left alone", path.display())));
        }
        if let Err(error) = std::fs::remove_dir_all(&path) {
            return Ok(Err(format!("{}: {error}", path.display())));
        }
        log_line(&mut log, &format!("{}\t{}\t{}", verdict.version.rel, verdict.version.size, verdict.reason))?;
        Ok(Ok(verdict.version.size))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|p| p.to_string()).collect()
    }

    #[test]
    fn rows_and_labels() {
        assert_eq!(row("entity2"), Some(2));
        assert_eq!(row("layer_3_version"), Some(3));
        assert_eq!(row("a12b03"), Some(3));
        assert_eq!(row("version"), None);
        assert!(is_label("version_label"));
        assert!(is_label("version_label12"));
        assert!(is_label("layer_3_version"));
        assert!(!is_label("layer__version"));
        assert!(!is_label("labelx"));
    }

    fn parm(name: &str, value: &str) -> String {
        format!("{name}\t[ 0\tlocks=0 ]\t(\t{value}\t)")
    }

    #[test]
    fn pins_of_a_node() {
        let text = [
            "{".to_string(),
            parm("entity", "\"entity:/shots/s0/h0\""),
            parm("department", "fx"),
            parm("version", "v0003"),
            "}".into(),
            "{".into(),
            parm("entity", "from_context"),
            parm("version2", "v0004"),
            parm("uri", "\"entity:/assets/A?dept=model&version=v0007\""),
            "}".into(),
        ]
        .join("\n");
        let own = s(&["shots", "s0", "h1"]);
        let (pins, exact) = hip_pins(&text, Some(&own)).unwrap();
        assert!(pins.contains(&(s(&["shots", "s0", "h0"]), "v0003".into(), Some("fx".into()))));
        // The second node's entity parm has no row: it covers row 2.
        assert!(pins.contains(&(own.clone(), "v0004".into(), None)));
        assert!(pins.contains(&(s(&["assets", "A"]), "v0004".into(), None)));
        assert!(exact.contains(&s(&["assets", "A", "default", "model", "v0007"])));
    }
}
