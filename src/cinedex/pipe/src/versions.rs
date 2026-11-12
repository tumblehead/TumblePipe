//! Version folders (`tumblepipe.pipe.paths.version`).
//!
//! Everything versioned is laid out `.../vNNNN/...`. A version folder is
//! finished when its `context.json` is there: every writer puts it in last.

use std::path::{Path, PathBuf};

use crate::io::VERSION_MARKER;
use crate::naming::{is_valid_version_name, version_key, version_name};

/// The version folders under `path`, oldest first; none when it is missing.
pub fn list_version_paths(path: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(path) else { return Vec::new() };
    let mut versions: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.file_name().to_str().is_some_and(is_valid_version_name) && entry.file_type().is_ok_and(|t| t.is_dir()))
        .map(|entry| entry.path())
        .collect();
    versions.sort_by_key(|path| version_key(&file_name(path)));
    versions
}

fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Whether a version folder is finished: its `context.json` is there.
pub fn is_finished_version(version: &Path) -> bool {
    version.join(VERSION_MARKER).is_file()
}

/// The newest finished version under `path`; when none is finished (a
/// layout that never wrote sidecars), the newest; None when there is none.
pub fn latest_version_path(path: &Path) -> Option<PathBuf> {
    let versions = list_version_paths(path);
    versions.iter().rev().find(|version| is_finished_version(version)).cloned().or_else(|| versions.last().cloned())
}

/// One past the highest version folder, finished or not, so a new version
/// never lands in a folder another writer is filling.
pub fn next_version_path(path: &Path) -> PathBuf {
    let next = list_version_paths(path).last().map(|last| version_key(&file_name(last)) + 1).unwrap_or(1);
    path.join(version_name(next))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn latest_skips_unfinished() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(latest_version_path(dir.path()), None);
        assert_eq!(next_version_path(dir.path()), dir.path().join("v0001"));
        for name in ["v0002", "v0010", "v0009", "notes"] {
            std::fs::create_dir(dir.path().join(name)).unwrap();
        }
        assert_eq!(latest_version_path(dir.path()), Some(dir.path().join("v0010")));
        std::fs::write(dir.path().join("v0009").join("context.json"), "{}").unwrap();
        assert_eq!(latest_version_path(dir.path()), Some(dir.path().join("v0009")));
        assert_eq!(next_version_path(dir.path()), dir.path().join("v0011"));
        let names: Vec<_> = list_version_paths(dir.path()).iter().map(|p| file_name(p)).collect();
        assert_eq!(names, ["v0002", "v0009", "v0010"]);
    }
}
