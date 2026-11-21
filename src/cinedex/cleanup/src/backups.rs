//! Houdini's numbered save backups (`<department>/backup/*_bakN.hip`,
//! `tumblepipe.cleanup.backups`).
//!
//! Houdini copies the file it is about to overwrite into `backup/` on every
//! save and never removes one. A backup goes when it is older than
//! `backup_days` and either it is of a workfile version that has a newer
//! saved version in its department folder, or it is of the latest version
//! and not one of the newest `keep_backups` of that department. Anything in
//! a `backup` folder not named like a backup of a workfile version
//! (`<stem>_vNNNN_bakN.hip`) is left alone.

use std::collections::HashMap;
use std::path::Path;

use crate::core::{Category, CategoryPlan, Entry, Outcome, Payload, Settings, Steer, age_days, days, delete_entries, under_context};
use crate::fsx;
use crate::inventory::{Inventory, Item, Kind};
use crate::py;
use crate::roots::{ProjectRoots, WORKFILE_PURPOSES};

pub const CATEGORY: Category = Category {
    key: "backups",
    title: "Houdini backups",
    about: "Houdini's save backups of workfile versions that have since been saved again",
    purposes: &["assets", "shots", "groups"],
};

pub const BACKUP_DIR: &str = "backup";

pub const PRUNE_SUPERSEDED: &str = "a backup of a workfile version that has a newer saved version";
pub fn reason_prune_extra(keep: i64) -> String {
    format!("beyond the newest {keep} backups of its department's latest workfile version")
}
pub fn reason_keep_newest(keep: i64) -> String {
    format!("one of the newest {keep} backups of its department's latest workfile version")
}
pub fn reason_keep_recent(backup_days: f64) -> String {
    format!("newer than {} days", days(backup_days))
}
pub const KEEP_OTHER: &str = "not named like a backup of a workfile version";

/// `(workfile version, backup number)` a backup file's name says
/// (`_BACKUP`: `^.+_v(\d{4,})_bak(\d+)\.(?:hip|hiplc|hipnc)$`, case-blind).
pub fn backup_of(name: &str) -> Option<(i128, i128)> {
    let name = name.strip_suffix('\n').unwrap_or(name);
    if name.contains('\n') {
        return None;
    }
    let chars: Vec<char> = name.chars().collect();
    let i = |c: char| matches!(c, 'i' | 'I' | '\u{130}' | '\u{131}');
    let h = |c: char| matches!(c, 'h' | 'H');
    let p = |c: char| matches!(c, 'p' | 'P');
    let lower = |c: char, l: char| c.is_ascii() && c.to_ascii_lowercase() == l;
    let mut end = chars.len();
    // `.hip`, `.hiplc` or `.hipnc` at the end.
    let ext = |len: usize| -> bool {
        if end < len + 1 || chars[end - len - 1] != '.' {
            return false;
        }
        let e = &chars[end - len..end];
        let hip = h(e[0]) && i(e[1]) && p(e[2]);
        hip && (len == 3 || ((lower(e[3], 'l') || lower(e[3], 'n')) && lower(e[4], 'c')))
    };
    let ext_len = [5, 3].into_iter().find(|len| ext(*len))?;
    end -= ext_len + 1;
    let number_digits = chars[..end].iter().rev().take_while(|c| py::is_digit(**c)).count();
    if number_digits == 0 {
        return None;
    }
    let number: String = chars[end - number_digits..end].iter().collect();
    end -= number_digits;
    let k = |c: char| matches!(c, 'k' | 'K' | '\u{212A}');
    if end < 4 || chars[end - 4] != '_' || !lower(chars[end - 3], 'b') || !lower(chars[end - 2], 'a') || !k(chars[end - 1]) {
        return None;
    }
    end -= 4;
    let version_digits = chars[..end].iter().rev().take_while(|c| py::is_digit(**c)).count();
    if version_digits < 4 {
        return None;
    }
    let version: String = chars[end - version_digits..end].iter().collect();
    end -= version_digits;
    // `_v` with at least one character before it.
    if end < 3 || chars[end - 2] != '_' || !matches!(chars[end - 1], 'v' | 'V') {
        return None;
    }
    Some((py::int_digits(&version)?, py::int_digits(&number)?))
}

/// The category's plan (`backups.plan`).
pub fn plan(roots: &ProjectRoots, inventory: Option<&Inventory>, settings: &Settings, now: f64) -> CategoryPlan {
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "file");
    out.payload = Payload::Roots(roots.roots.iter().map(|r| (r.purpose.clone(), r.path.clone())).collect());
    let Some(inventory) = inventory else { return out };
    let workfile_root = |item: &Item| WORKFILE_PURPOSES.contains(&item.root.as_str());
    let mut latest: HashMap<(String, String), i128> = HashMap::new();
    for item in &inventory.items {
        if item.kind == Kind::Workfile && workfile_root(item) {
            let key = (item.root.clone(), item.series.clone().unwrap_or_default());
            let version = item.version.unwrap_or(0);
            let slot = latest.entry(key).or_insert(-1);
            *slot = (*slot).max(version);
        }
    }

    // (root, department folder) -> [(version, backup number, item)] of its latest version
    type Department<'a> = ((String, String), Vec<(i128, i128, &'a Item)>);
    let mut current: Vec<Department> = Vec::new();
    let mut verdicts: HashMap<(String, String), (bool, String)> = HashMap::new();
    let items: Vec<&Item> = inventory.items.iter().filter(|i| i.kind == Kind::Backup && workfile_root(i)).collect();
    for item in &items {
        let parts: Vec<&str> = item.rel.split('/').collect();
        let named = backup_of(parts[parts.len() - 1]);
        let key = (item.root.clone(), item.rel.clone());
        let Some((version, number)) = named.filter(|_| parts[parts.len() - 2] == BACKUP_DIR) else {
            verdicts.insert(key, (false, KEEP_OTHER.to_string()));
            continue;
        };
        let department = parts[..parts.len() - 2].join("/");
        let group_key = (item.root.clone(), department);
        if version < latest.get(&group_key).copied().unwrap_or(-1) {
            verdicts.insert(key, (true, PRUNE_SUPERSEDED.to_string()));
        } else {
            match current.iter_mut().find(|(k, _)| *k == group_key) {
                Some((_, group)) => group.push((version, number, item)),
                None => current.push((group_key, vec![(version, number, item)])),
            }
        }
    }
    let keep = settings.keep_backups;
    for (_, mut group) in current {
        group.sort_by_key(|(version, number, _)| (*version, *number));
        let take = usize::try_from(keep).unwrap_or(0).min(group.len());
        let first_kept = group.len() - take;
        for (at, (_, _, item)) in group.iter().enumerate() {
            let verdict = if at >= first_kept { (false, reason_keep_newest(keep)) } else { (true, reason_prune_extra(keep)) };
            verdicts.insert((item.root.clone(), item.rel.clone()), verdict);
        }
    }

    let mut sorted = items.clone();
    sorted.sort_by(|a, b| (&a.root, &a.rel).cmp(&(&b.root, &b.rel)));
    for item in sorted {
        let label = format!("{}/{}", item.root, item.rel);
        if !under_context(&label, settings.context()) {
            continue;
        }
        let (mut prune, mut reason) = verdicts[&(item.root.clone(), item.rel.clone())].clone();
        let path = item.path(roots);
        if prune {
            match std::fs::metadata(&path).and_then(|m| m.modified()) {
                Ok(modified) => {
                    if age_days(fsx::seconds(modified), now) < settings.backup_days {
                        prune = false;
                        reason = reason_keep_recent(settings.backup_days);
                    }
                }
                Err(error) => {
                    out.errors.push(format!("{}: {error}", path.display()));
                    continue;
                }
            }
        }
        let parts: Vec<&str> = item.rel.split('/').collect();
        out.entries.push(Entry {
            path,
            label,
            group: format!("{}/{}", item.root, parts[..parts.len() - 2].join("/")),
            size: item.size,
            prune,
            reason,
        });
    }
    out
}

/// Delete the backups marked to go, each still a backup file in a workfile
/// folder's `backup` folder (`backups.apply`).
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let root_paths = match &category.payload {
        Payload::Roots(roots) => roots.clone(),
        _ => Vec::new(),
    };
    let still_ok = |entry: &Entry| -> Option<String> {
        let (purpose, rel) = entry.label.split_once('/').unwrap_or((entry.label.as_str(), ""));
        let root = root_paths.iter().find(|(p, _)| p == purpose).map(|(_, path)| path);
        let expected = root.map(|root| {
            let mut path = root.clone();
            path.extend(rel.split('/'));
            path
        });
        let Some(root) =
            root.filter(|_| WORKFILE_PURPOSES.contains(&purpose) && expected.as_ref().is_some_and(|e| fsx::same_path(e, &entry.path)))
        else {
            return Some("not in a workfile folder".into());
        };
        let parent = entry.path.parent().unwrap_or(Path::new(""));
        let parent_name = parent.file_name().map(fsx::name_of).unwrap_or_default();
        let name = entry.path.file_name().map(fsx::name_of).unwrap_or_default();
        if parent_name != BACKUP_DIR || backup_of(&name).is_none() {
            return Some("not a workfile backup".into());
        }
        if !fsx::within(parent, root) {
            return Some("outside its storage root".into());
        }
        let link = std::fs::symlink_metadata(&entry.path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if !entry.path.is_file() || link {
            return Some("not a file".into());
        }
        None
    };
    delete_entries(category, log_path, steer, still_ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backup_names() {
        assert_eq!(backup_of("a_v0012_bak3.hip"), Some((12, 3)));
        assert_eq!(backup_of("x_y_V00012_BAK10.HIPLC"), Some((12, 10)));
        assert_eq!(backup_of("a_v0012_ba\u{212A}3.h\u{131}pnc"), Some((12, 3)));
        assert_eq!(backup_of("a_v0012_bak3.hip\n"), Some((12, 3)));
        assert_eq!(backup_of("_v0012_bak3.hip"), None);
        assert_eq!(backup_of("a_v012_bak3.hip"), None);
        assert_eq!(backup_of("a_v0012_bak.hip"), None);
        assert_eq!(backup_of("a_v0012_bak3.hipx"), None);
        assert_eq!(backup_of("a\n_v0012_bak3.hip"), None);
        assert_eq!(backup_of("a_v0012_bak3.lc"), None);
        assert_eq!(backup_of("a_v0012_bak3.hiplc"), Some((12, 3)));
    }
}
