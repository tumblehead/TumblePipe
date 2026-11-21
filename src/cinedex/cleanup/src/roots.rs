//! Where a project's files live (`tumblepipe.archive.roots`).
//!
//! `storage.json` puts some purposes inside the project folder and others
//! beside it (`{project_parent}/{project_name}_render`, `_cache`, ...), so
//! the roots come from the table, each resolved, each walked once.

use std::path::{Path, PathBuf};

use th_pipe::{Storage, Uri};

use crate::fsx::{resolve, same_path};

/// The purposes whose roots are walked, besides the project folder and its
/// config (`roots.WALKED_PURPOSES`).
pub const WALKED_PURPOSES: [&str; 10] = ["assets", "shots", "groups", "edits", "export", "edit", "render", "cache", "proxy", "turntable"];

/// Purposes holding workfile folders (`roots.WORKFILE_PURPOSES`).
pub const WORKFILE_PURPOSES: [&str; 4] = ["assets", "shots", "groups", "edits"];

/// One directory a scan walks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Root {
    /// `project`, `config`, or a storage purpose.
    pub purpose: String,
    pub path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectRoots {
    pub project_path: PathBuf,
    pub config_path: PathBuf,
    pub roots: Vec<Root>,
}

impl ProjectRoots {
    /// The roots of the project at `project_path`, from its `storage.json`
    /// (`roots.project_roots`): an error for a project without a valid
    /// table, which is the right answer, since where renders live is not
    /// known without it.
    pub fn open(project_path: &Path) -> Result<Self, String> {
        let project_path = resolve(project_path);
        let config_path = project_path.join("_config");
        let storage = Storage::load(&project_path, &project_path, &config_path).map_err(|e| e.to_string())?;
        let mut roots = vec![
            Root { purpose: "project".into(), path: project_path.clone() },
            Root { purpose: "config".into(), path: config_path.clone() },
        ];
        let mut seen = vec![project_path.clone(), config_path.clone()];
        for purpose in WALKED_PURPOSES {
            let path = storage.resolve(&Uri::new(purpose, std::iter::empty::<&str>())).map_err(|e| e.to_string())?;
            let path = resolve(&path);
            if seen.iter().any(|s| same_path(s, &path)) {
                continue;
            }
            seen.push(path.clone());
            roots.push(Root { purpose: purpose.into(), path });
        }
        Ok(ProjectRoots { project_path, config_path, roots })
    }

    pub fn get(&self, purpose: &str) -> Option<&Root> {
        self.roots.iter().find(|r| r.purpose == purpose)
    }

    /// The folder of `purpose`, if the project has that root.
    pub fn path(&self, purpose: &str) -> Option<&Path> {
        self.get(purpose).map(|r| r.path.as_path())
    }

    /// The export root, or `<project>/export` without one
    /// (`cleanup.core.export_path`).
    pub fn export_path(&self) -> PathBuf {
        self.path("export").map(Path::to_path_buf).unwrap_or_else(|| self.project_path.join("export"))
    }
}
