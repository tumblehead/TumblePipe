//! The `_deleted/<stamp>/` folders deleting an entity or an edit leaves
//! (`tumblepipe.cleanup.deleted`).
//!
//! Deleting an asset, a shot or an edit moves its folders to
//! `<storage root>/_deleted/<YYYYMMDD-HHMMSS>/` in every storage root, so a
//! mistake can be put back by hand. A stamp folder goes once the delete it
//! records is older than `deleted_days`; anything else in a `_deleted`
//! folder is left alone.

use std::path::{Path, PathBuf};

use chrono::{Local, NaiveDate, TimeZone};

use crate::core::{Category, CategoryPlan, Entry, Outcome, Payload, Settings, Steer, Watch, age_days, days, delete_entries};
use crate::fsx;
use crate::py;
use crate::roots::ProjectRoots;

pub const CATEGORY: Category = Category {
    key: "deleted",
    title: "Deleted entities",
    about: "the folders of assets, shots and edits deleted a while ago (kept in _deleted so a mistake can be undone)",
    purposes: &[],
};

pub const ARCHIVE_DIR: &str = "_deleted";

pub fn reason_prune(deleted_days: f64) -> String {
    format!("deleted more than {} days ago", days(deleted_days))
}
pub fn reason_keep_recent(deleted_days: f64) -> String {
    format!("deleted less than {} days ago", days(deleted_days))
}
pub const KEEP_OTHER: &str = "not a folder a delete made";

/// Two characters `strptime` reads as one field: the first an ASCII
/// character `first` allows, the second a digit `second` allows.
fn field(chars: &[char], first: impl Fn(char) -> bool, second: impl Fn(char) -> bool) -> Option<u32> {
    let (a, b) = (chars[0], chars[1]);
    if !first(a) || !second(b) {
        return None;
    }
    Some(py::digit(a)? * 10 + py::digit(b)?)
}

/// When the delete that made the folder `name` happened, in local time
/// (`deleted.stamp_time`: `^\d{8}-\d{6}$`, then `strptime('%Y%m%d-%H%M%S')`,
/// whose month, day and time fields start with an ASCII digit).
pub fn stamp_time(name: &str) -> Option<f64> {
    let chars: Vec<char> = name.chars().collect();
    if chars.len() != 15 || chars[8] != '-' || !chars.iter().enumerate().all(|(at, c)| at == 8 || py::is_digit(*c)) {
        return None;
    }
    let digit = py::is_digit;
    let year = chars[..4].iter().try_fold(0i32, |n, c| Some(n * 10 + py::digit(*c)? as i32))?;
    let month = field(&chars[4..6], |c| c == '1', |c| matches!(c, '0'..='2'))
        .or_else(|| field(&chars[4..6], |c| c == '0', |c| matches!(c, '1'..='9')))?;
    let day = field(&chars[6..8], |c| c == '3', |c| matches!(c, '0' | '1'))
        .or_else(|| field(&chars[6..8], |c| matches!(c, '1' | '2'), digit))
        .or_else(|| field(&chars[6..8], |c| c == '0', |c| matches!(c, '1'..='9')))?;
    let hour = field(&chars[9..11], |c| c == '2', |c| matches!(c, '0'..='3'))
        .or_else(|| field(&chars[9..11], |c| matches!(c, '0' | '1'), digit))?;
    let minute = field(&chars[11..13], |c| matches!(c, '0'..='5'), digit)?;
    let second = field(&chars[13..15], |c| c == '6', |c| matches!(c, '0' | '1'))
        .or_else(|| field(&chars[13..15], |c| matches!(c, '0'..='5'), digit))?;
    // `datetime(...)`: year 1 and up, a real day, seconds below 60.
    if year < 1 || second > 59 {
        return None;
    }
    let naive = NaiveDate::from_ymd_opt(year, month, day)?.and_hms_opt(hour, minute, second)?;
    // A naive datetime's `.timestamp()`: the earlier of two readings of a
    // time the clocks went back over; a time they skipped reads as before.
    let stamp = match Local.from_local_datetime(&naive) {
        chrono::LocalResult::Single(at) | chrono::LocalResult::Ambiguous(at, _) => at.timestamp(),
        chrono::LocalResult::None => {
            let offset = Local.offset_from_utc_datetime(&naive);
            naive.and_utc().timestamp() - i64::from(offset.local_minus_utc())
        }
    };
    // Windows' C runtime reads no local time before 1970 or after 3000:
    // `.timestamp()` raises there, and the folder is not a stamp.
    if cfg!(windows) && !(86_400..=32_535_215_999 - 86_400).contains(&stamp) {
        return None;
    }
    Some(stamp as f64)
}

/// The category's plan (`deleted.plan`).
pub fn plan(roots: &ProjectRoots, settings: &Settings, now: f64, watch: &Watch<'_>) -> Result<CategoryPlan, String> {
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "folder");
    let holders: Vec<(String, PathBuf)> = roots.roots.iter().map(|r| (r.purpose.clone(), r.path.join(ARCHIVE_DIR))).collect();
    out.payload = Payload::Deleted(holders.clone());
    // Every entry first, sized `watch.threads` at a time after.
    let mut found: Vec<(Entry, bool)> = Vec::new();
    for (purpose, holder) in &holders {
        let Ok(listing) = std::fs::read_dir(holder) else { continue };
        let mut entries: Vec<std::fs::DirEntry> = listing.filter_map(Result::ok).collect();
        entries.sort_by_key(|e| fsx::name_of(&e.file_name()));
        for entry in entries {
            let name = fsx::name_of(&entry.file_name());
            let path = holder.join(&name);
            let label = format!("{purpose}/{ARCHIVE_DIR}/{name}");
            let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);
            let when = stamp_time(&name);
            let Some(when) = when.filter(|_| is_dir) else {
                let entry = Entry { path, label, group: purpose.clone(), size: 0, prune: false, reason: KEEP_OTHER.into() };
                found.push((entry, is_dir));
                continue;
            };
            let old = age_days(when, now) >= settings.deleted_days;
            let reason = if old { reason_prune(settings.deleted_days) } else { reason_keep_recent(settings.deleted_days) };
            found.push((Entry { size: 0, path, label, group: purpose.clone(), prune: old, reason }, true));
        }
    }
    let meter = watch.meter(Some(CATEGORY.key), "Measuring deleted folders", Some(found.len()));
    let sizes = fsx::par_map(&found, watch.threads, |(entry, is_dir)| {
        let size = if watch.cancelled() {
            0
        } else if *is_dir {
            fsx::tree_size(&entry.path)
        } else {
            std::fs::symlink_metadata(&entry.path).map(|m| m.len()).unwrap_or(0)
        };
        if entry.prune {
            meter.found(size);
        }
        meter.add(1, size);
        size
    });
    meter.finish();
    watch.check()?;
    for ((mut entry, _), size) in found.into_iter().zip(sizes) {
        entry.size = size;
        out.entries.push(entry);
    }
    Ok(out)
}

/// Delete the stamp folders marked to go, each still one, in a `_deleted`
/// folder this plan read (`deleted.apply`).
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let holders = match &category.payload {
        Payload::Deleted(holders) => holders.clone(),
        _ => Vec::new(),
    };
    let still_ok = |entry: &Entry| -> Option<String> {
        let parent = entry.path.parent().unwrap_or(Path::new(""));
        let parent_name = parent.file_name().map(fsx::name_of).unwrap_or_default();
        if !holders.iter().any(|(_, holder)| fsx::same_path(holder, parent)) || parent_name != ARCHIVE_DIR {
            return Some(format!("not in a {ARCHIVE_DIR} folder"));
        }
        let name = entry.path.file_name().map(fsx::name_of).unwrap_or_default();
        if stamp_time(&name).is_none() {
            return Some("not a folder a delete made".into());
        }
        let link = std::fs::symlink_metadata(&entry.path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if !entry.path.is_dir() || link {
            return Some("not a folder".into());
        }
        None
    };
    delete_entries(category, log_path, steer, still_ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamps() {
        assert!(stamp_time("20240131-235959").is_some());
        assert!(stamp_time("20240229-000000").is_some());
        assert!(stamp_time("20230229-000000").is_none());
        assert!(stamp_time("20241301-000000").is_none());
        assert!(stamp_time("20240100-000000").is_none());
        assert!(stamp_time("20240101-240000").is_none());
        assert!(stamp_time("20240101-000060").is_none());
        assert!(stamp_time("20240101-000000\n").is_none());
        assert!(stamp_time("2024010-1000000").is_none());
        // Day and minute fields take any digit second.
        assert_eq!(stamp_time("20240101-0\u{0663}0000"), stamp_time("20240101-030000"));
        // A month, day or time field's first digit is an ASCII one.
        assert!(stamp_time("202401\u{0661}1-000000").is_none());
        assert!(stamp_time("2024\u{0660}101-000000").is_none());
    }
}
