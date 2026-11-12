//! Creating shots and retiring entities (`tumblepipe.config.shot_batch.create`,
//! `tumblepipe.pipe.entity_files`).
//!
//! An entity is a row in the config; its work is folders named after it in
//! several storage roots. Retiring one archives those folders under
//! `<root>/_deleted/<stamp>/` (each with one rename, all or nothing), takes
//! it out of every Multi, then removes the row; when a step fails the ones
//! before are undone, so a new entity of the same name never picks the old
//! work back up and nothing is left half done.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use thiserror::Error;

use crate::Project;
use crate::groups::{drop_member_everywhere, restore_members};
use crate::uri::Uri;

/// Where retired folders go, in each storage root.
pub const ARCHIVE_DIR: &str = "_deleted";

/// The storage roots an entity's folders sit in, and the sub-path below
/// each its segments go under (`ENTITY_STORAGE`).
pub const ENTITY_STORAGE: [(&str, &[&str]); 6] =
    [("project", &[]), ("proxy", &[]), ("export", &[]), ("render", &["render"]), ("render", &["playblast"]), ("render", &["daily"])];

#[derive(Debug, Error)]
pub enum RetireError {
    /// A folder could not be moved; none were.
    #[error("Could not move {folder} to the {ARCHIVE_DIR} folder: {reason}. Close any scene or viewer using its files and try again.")]
    Archive { folder: PathBuf, reason: String },
    #[error("{0}")]
    Other(String),
}

/// `(storage root, folder)` for every existing folder holding `entity`'s
/// files, each once.
pub fn entity_folders(project: &Project, entity: &Uri) -> Result<Vec<(PathBuf, PathBuf)>, String> {
    if entity.purpose != "entity" || entity.segments.is_empty() {
        return Ok(Vec::new());
    }
    let mut folders = Vec::new();
    let mut seen = Vec::new();
    for (storage, prefix) in ENTITY_STORAGE {
        let root = project.storage.resolve(&Uri::root(storage)).map_err(|e| e.to_string())?;
        let folder = prefix.iter().copied().chain(entity.segments.iter().map(String::as_str)).fold(root.clone(), |p, s| p.join(s));
        let key = crate::storage::slashed(&folder).to_lowercase();
        if seen.contains(&key) || !folder.is_dir() {
            continue;
        }
        seen.push(key);
        folders.push((root, folder));
    }
    Ok(folders)
}

/// Whether any folder named after `entity` holds a file.
pub fn has_entity_files(project: &Project, entity: &Uri) -> Result<bool, String> {
    fn any_file(dir: &Path) -> bool {
        std::fs::read_dir(dir).into_iter().flatten().filter_map(Result::ok).any(|entry| match entry.file_type() {
            Ok(t) if t.is_dir() => any_file(&entry.path()),
            Ok(t) => t.is_file() || entry.path().is_file(),
            Err(_) => false,
        })
    }
    Ok(entity_folders(project, entity)?.iter().any(|(_, folder)| any_file(folder)))
}

/// Now, as the archive stamp: `YYYYmmdd-HHMMSS` local time.
pub fn archive_stamp() -> String {
    chrono::Local::now().format("%Y%m%d-%H%M%S").to_string()
}

/// Move `entity`'s folders to `<root>/_deleted/<stamp>/<same path>`, one
/// rename each. All or nothing: when one cannot move, the ones moved are
/// put back. The `(from, to)` pairs moved.
pub fn archive_entity_folders(project: &Project, entity: &Uri, stamp: &str) -> Result<Vec<(PathBuf, PathBuf)>, RetireError> {
    let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
    for (root, folder) in entity_folders(project, entity).map_err(RetireError::Other)? {
        let relative = folder.strip_prefix(&root).expect("a folder lies under its root").to_path_buf();
        let mut target = root.join(ARCHIVE_DIR).join(stamp).join(&relative);
        let name = folder.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let mut n = 1;
        while target.exists() {
            target = target.with_file_name(format!("{name}_{n}"));
            n += 1;
        }
        let result = target.parent().map_or(Ok(()), std::fs::create_dir_all).and_then(|()| std::fs::rename(&folder, &target));
        if let Err(error) = result {
            restore_entity_folders(&moved);
            return Err(RetireError::Archive { folder, reason: error.to_string() });
        }
        moved.push((folder, target));
    }
    Ok(moved)
}

/// Put back what [`archive_entity_folders`] moved; best effort, last first.
pub fn restore_entity_folders(moved: &[(PathBuf, PathBuf)]) {
    for (source, dest) in moved.iter().rev() {
        if let Err(error) = std::fs::rename(dest, source) {
            eprintln!("Could not move {} back to {}: {error}", dest.display(), source.display());
        }
    }
}

/// Retire `entity`: archive its folders, take it out of every Multi, remove
/// it from the config (`delete_entity`). All or nothing. The folders moved.
pub fn delete_entity(project: &Project, entity: &Uri, stamp: &str) -> Result<Vec<(PathBuf, PathBuf)>, RetireError> {
    let moved = archive_entity_folders(project, entity, stamp)?;
    let mut memberships = Vec::new();
    let result = (|| {
        memberships = drop_member_everywhere(&project.config, entity)?;
        project.config.remove_entity(entity).map_err(|e| e.to_string())
    })();
    if let Err(error) = result {
        if let Err(restore) = restore_members(&project.config, &memberships) {
            eprintln!("Could not restore the Multis {entity} was in: {restore}");
        }
        restore_entity_folders(&moved);
        return Err(RetireError::Other(error));
    }
    Ok(moved)
}

/// Rename `entity` to `new_name` (`entity_files.rename_entity`): its config
/// row, keeping its place, and every Multi listing it (or what is under it)
/// lists the new name. Refuses an entity with files on disk: they sit in
/// folders named after it and would stay under the old name. The new URI.
pub fn rename_entity(project: &Project, entity: &Uri, new_name: &str) -> Result<Uri, String> {
    if has_entity_files(project, entity)? {
        return Err(format!(
            "'{}' has workfiles or exports on disk, so it can't be renamed: they would stay under the old name and the \
             renamed entity would come up empty. Use Duplicate\u{2026} on its card in the Asset Browser to make a copy \
             under the new name, then delete the old one.",
            entity.last().unwrap_or_default()
        ));
    }
    let mut segments = entity.segments.clone();
    segments.pop();
    segments.push(new_name.to_string());
    let new_uri = Uri::new(&entity.purpose, segments);
    project.config.rename_entity(entity, new_name).map_err(|e| e.to_string())?;
    crate::groups::rename_member_everywhere(&project.config, entity, &new_uri)?;
    Ok(new_uri)
}

/// Create the shot `entity:/shots/<sequence>/<shot>` with its frames,
/// creating the sequence when it is new (`shot_batch.create`). Refuses a
/// shot that exists, a case variant of an existing name, and a name not of
/// letters and digits.
pub fn create_shot(project: &Project, sequence: &str, shot: &str, frame_start: i64, frame_end: i64) -> Result<Uri, String> {
    let sequence_uri = Uri::new("entity", ["shots", sequence]);
    if !project.config.exists(&sequence_uri).map_err(|e| e.to_string())? {
        project.config.add_entity(&sequence_uri, Map::new()).map_err(|e| e.to_string())?;
    }
    let uri = sequence_uri.join(shot);
    let mut properties = Map::new();
    properties.insert("frame_start".into(), Value::from(frame_start));
    properties.insert("frame_end".into(), Value::from(frame_end));
    project.config.add_entity(&uri, properties).map_err(|e| e.to_string())?;
    Ok(uri)
}
