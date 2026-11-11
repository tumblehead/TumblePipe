//! Department workfiles: where an entity's department hips are saved and
//! which one is the latest (`tumblepipe.pipe.paths.workspace`,
//! `tumblepipe.config.groups.find_group`).
//!
//! A department workspace is `project:/<entity segments>/<department>`,
//! unless a Multi lists the entity and covers the department: then it is
//! the Multi's, `groups:/<context>/<multi>/<department>`, and the hips are
//! named after the Multi. A hip is `<stem>_<department>_vNNNN.<ext>` with
//! `ext` one of [`HIP_EXTENSIONS`]; the workspace's `context.json` points
//! at the version last saved, and `_context/vNNNN.json` holds each
//! version's lineage entry (or a `{"reserved": ...}` claim while a save is
//! still writing it).

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::groups::{Group, groups_of};
use crate::io::load_json;
use crate::naming::{entity_stem, is_valid_version_name, version_code};
use crate::{Config, Project, Uri};

/// Houdini's hip extensions: commercial, limited commercial, non-commercial.
pub const HIP_EXTENSIONS: [&str; 3] = ["hip", "hiplc", "hipnc"];

/// The Multi of `context` that lists `member` and covers `department`
/// (`find_group`): the first such, in the groups database's order.
pub fn find_group(config: &Config, context: &str, member: &Uri, department: &str) -> Result<Option<Group>, String> {
    Ok(groups_of(config, context)?.into_iter().find(|g| g.members.contains(member) && g.departments.iter().any(|d| d == department)))
}

/// An entity's department workspace (`department_workspace`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Workspace {
    /// Whose workfiles they are: the entity, or the Multi covering it.
    pub workfile_uri: Uri,
    pub department: String,
    /// The folder the hips are saved in.
    pub path: PathBuf,
}

impl Workspace {
    /// `<stem>_<department>_<version>`, the file name of a version without
    /// its extension.
    pub fn base_name(&self, version: &str) -> String {
        entity_stem(&self.workfile_uri.segments, &[self.department.as_str(), version])
    }
}

/// The workspace `entity`'s `department` workfiles are saved in.
pub fn department_workspace(project: &Project, entity: &Uri, department: &str) -> Result<Workspace, String> {
    let (workfile_uri, purpose) = if entity.purpose == "groups" {
        (entity.clone(), "groups")
    } else {
        let context = entity.first().ok_or_else(|| format!("{entity} names no entity"))?;
        match find_group(&project.config, context, entity, department)? {
            Some(group) => (group.uri, "groups"),
            None => (entity.clone(), "project"),
        }
    };
    let uri = Uri::new(purpose, workfile_uri.segments.iter().cloned().chain([department.to_string()]));
    let path = project.storage.resolve(&uri).map_err(|e| e.to_string())?;
    Ok(Workspace { workfile_uri, department: department.to_string(), path })
}

/// The version a file name ends in (`_vNNNN` before the last extension),
/// as `version_of` reads it; None when it does not end in one.
pub fn version_of(path: &Path) -> Option<String> {
    let stem = path.file_stem()?.to_str()?;
    let (_, version) = stem.rsplit_once('_')?;
    is_valid_version_name(version).then(|| version.to_string())
}

/// Python's `glob` folds case on Windows (fnmatch normcases both sides).
fn fold(s: &str) -> String {
    if cfg!(windows) { s.to_lowercase() } else { s.to_string() }
}

/// The workspace's hips named `<stem>_<department>_*.<ext>` that end in a
/// version, oldest first (`_list_valid_hip_files`): extension by extension
/// in [`HIP_EXTENSIONS`] order, then a stable sort by version number, so of
/// two files of one version the later extension sorts last.
pub fn list_hips(workspace: &Workspace) -> Vec<PathBuf> {
    let prefix = fold(&format!("{}_", entity_stem(&workspace.workfile_uri.segments, &[workspace.department.as_str()])));
    let names: Vec<(String, PathBuf)> = match std::fs::read_dir(&workspace.path) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .filter_map(|e| {
                let name = e.file_name().into_string().ok()?;
                Some((fold(&name), e.path()))
            })
            .collect(),
        Err(_) => Vec::new(),
    };
    let mut hips = Vec::new();
    for ext in HIP_EXTENSIONS {
        let suffix = format!(".{ext}");
        for (name, path) in &names {
            if name.len() >= prefix.len() + suffix.len()
                && name.starts_with(&prefix)
                && name.ends_with(&suffix)
                && version_of(path).is_some()
            {
                hips.push(path.clone());
            }
        }
    }
    hips.sort_by_key(|p| version_of(p).and_then(|v| version_code(&v)).unwrap_or(0));
    hips
}

/// The highest version's hip on disk (`latest_hip_file_path`).
pub fn latest_hip_on_disk(workspace: &Workspace) -> Option<PathBuf> {
    list_hips(workspace).pop()
}

/// The latest workfile, the higher of the `context.json` pointer (when its
/// hip exists) and the listing (`latest_hip_file_path_with_context`). The
/// pointer catches a listing that misses a just-saved file; the listing
/// catches a pointer that lags.
pub fn latest_hip(workspace: &Workspace) -> Result<Option<PathBuf>, String> {
    let mut tracked: Option<(u64, PathBuf)> = None;
    let context_path = workspace.path.join("context.json");
    if context_path.exists() {
        let context = load_json(&context_path)?;
        if let Some(Value::String(version)) = context.get("version")
            && is_valid_version_name(version)
        {
            let base = workspace.base_name(version);
            tracked = HIP_EXTENSIONS
                .iter()
                .map(|ext| workspace.path.join(format!("{base}.{ext}")))
                .find(|p| p.exists())
                .map(|p| (version_code(version).unwrap_or(0), p));
        }
    }
    let on_disk = latest_hip_on_disk(workspace);
    let disk_code = on_disk.as_deref().and_then(version_of).and_then(|v| version_code(&v));
    Ok(match (on_disk, tracked) {
        (Some(disk), Some((code, _))) if disk_code.is_some_and(|d| d > code) => Some(disk),
        (_, Some((_, pointed))) => Some(pointed),
        (disk, None) => disk,
    })
}

/// A department's latest workfile, as the dashboard shows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Workfile {
    pub path: PathBuf,
    pub version: String,
    /// Who saved it and when (`_context/<version>.json`); None for a
    /// version saved before entries existed.
    pub user: Option<String>,
    pub timestamp: Option<String>,
    /// Still being saved: its entry is a reservation claim.
    pub saving: bool,
}

/// `entity`'s latest `department` workfile, with who saved it and when;
/// None when the department has no workfile yet.
pub fn latest_workfile(project: &Project, entity: &Uri, department: &str) -> Result<Option<Workfile>, String> {
    let workspace = department_workspace(project, entity, department)?;
    let Some(path) = latest_hip(&workspace)? else { return Ok(None) };
    let version = version_of(&path).ok_or_else(|| format!("{} is not a version", path.display()))?;
    let entry_path = workspace.path.join("_context").join(format!("{version}.json"));
    let entry = if entry_path.exists() { load_json(&entry_path).ok() } else { None };
    let field = |key: &str| entry.as_ref().and_then(|e| e.get(key)).and_then(Value::as_str).map(str::to_string);
    let saving = matches!(&entry, Some(Value::Object(map)) if map.len() == 1 && map.contains_key("reserved"));
    Ok(Some(Workfile { user: field("user"), timestamp: field("timestamp"), saving, version, path }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn workspace(dir: &Path) -> Workspace {
        Workspace { workfile_uri: Uri::parse("entity:/shots/A/010").unwrap(), department: "animation".into(), path: dir.to_path_buf() }
    }

    fn touch(dir: &Path, name: &str) {
        std::fs::write(dir.join(name), b"").unwrap();
    }

    #[test]
    fn versions_of_names() {
        assert_eq!(version_of(Path::new("A_010_animation_v0003.hip")).as_deref(), Some("v0003"));
        assert_eq!(version_of(Path::new("A_010_animation_v10000.hipnc")).as_deref(), Some("v10000"));
        assert_eq!(version_of(Path::new("A_010_animation.hip")), None);
        assert_eq!(version_of(Path::new("v0003.hip")), None);
    }

    proptest! {
        /// The latest hip is the highest version on disk, whatever the
        /// pointer says, unless the pointer names a higher version whose
        /// hip exists; foreign and unversioned files never count.
        #[test]
        fn latest_is_the_higher_of_pointer_and_disk(
            versions in proptest::collection::btree_set(1u64..40, 0..6),
            pointer in proptest::option::of(1u64..50),
            junk in any::<bool>(),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let ws = workspace(dir.path());
            for v in &versions {
                touch(dir.path(), &format!("{}.hip", ws.base_name(&crate::naming::version_name(*v))));
            }
            if junk {
                touch(dir.path(), "B_020_animation_v0099.hip");
                touch(dir.path(), "A_010_animation_notes.hip");
                touch(dir.path(), &format!("{}.txt", ws.base_name("v0098")));
            }
            if let Some(p) = pointer {
                std::fs::write(dir.path().join("context.json"), format!("{{\"version\": \"{}\"}}", crate::naming::version_name(p))).unwrap();
            }
            let got = latest_hip(&ws).unwrap().map(|p| version_of(&p).unwrap());
            let want = versions.iter().max().map(|v| crate::naming::version_name(*v));
            prop_assert_eq!(got, want);
        }
    }
}
