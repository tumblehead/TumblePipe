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

/// A project's roots. `roots` are the directories to walk, each once: a
/// purpose `storage.json` puts on a path another one already has (say
/// `export` on the project folder itself) is walked as that one. `purposes`
/// has every purpose, so [`ProjectRoots::get`] finds each.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectRoots {
    pub project_path: PathBuf,
    pub config_path: PathBuf,
    pub roots: Vec<Root>,
    pub purposes: Vec<Root>,
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
        let mut purposes = roots.clone();
        for purpose in WALKED_PURPOSES {
            let path = storage.resolve(&Uri::new(purpose, std::iter::empty::<&str>())).map_err(|e| e.to_string())?;
            let root = Root { purpose: purpose.into(), path: resolve(&path) };
            if !roots.iter().any(|r| same_path(&r.path, &root.path)) {
                roots.push(root.clone());
            }
            purposes.push(root);
        }
        Ok(ProjectRoots { project_path, config_path, roots, purposes })
    }

    /// The root of `purpose`: `project`, `config` or one of
    /// [`WALKED_PURPOSES`]. Panics for any other, a caller's mistake
    /// (Python's `ProjectRoots.get` raises KeyError).
    pub fn get(&self, purpose: &str) -> &Root {
        self.purposes.iter().find(|r| r.purpose == purpose).unwrap_or_else(|| panic!("no storage purpose {purpose:?}"))
    }

    /// The folder of `purpose` ([`ProjectRoots::get`]).
    pub fn path(&self, purpose: &str) -> &Path {
        &self.get(purpose).path
    }

    /// The export root (`cleanup.core.export_path`).
    pub fn export_path(&self) -> PathBuf {
        self.path("export").to_path_buf()
    }
}
