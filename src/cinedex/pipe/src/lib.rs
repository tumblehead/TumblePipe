//! TumblePipe's pipeline in Rust, for the editor (`cinedex`) and
//! tumblepipe-mcp: URIs, storage, the config database, departments,
//! versions, review media discovery, department workfiles, live
//! playblasts, and creating and retiring entities.
//!
//! The Python package (`python/tumblepipe`) is the specification: each
//! module names what it ports, and `tests/test_pipe_parity.py` drives both
//! over the same projects through `examples/pipe_lines.rs`.

pub mod config;
pub mod db;
pub mod departments;
pub mod desktop;
pub mod discovery;
pub mod edit_files;
pub mod edits;
pub mod entities;
pub mod events;
pub mod groups;
pub mod io;
pub mod live;
pub mod master;
pub mod media;
pub mod movie;
pub mod naming;
pub mod player_rpc;
pub mod previews;
pub mod project;
pub mod shared_journal;
pub mod storage;
#[cfg(any(test, feature = "testing"))]
pub mod testing;
pub mod timeline;
pub mod uri;
pub mod versions;
pub mod workfiles;

use std::path::{Path, PathBuf};

pub use config::Config;
pub use storage::Storage;
pub use uri::Uri;

/// One project as TumblePipe is launched on it: its root, the pipeline
/// (package) it runs with, its config folder, its storage and a snapshot
/// of its config database.
#[derive(Debug)]
pub struct Project {
    pub root: PathBuf,
    pub pipeline_path: PathBuf,
    pub config_path: PathBuf,
    pub storage: Storage,
    pub config: Config,
}

impl Project {
    /// The project at `root` (its config in `root/_config`), with
    /// `pipeline_path` as the pipeline.
    pub fn open(root: &Path, pipeline_path: &Path) -> Result<Self, String> {
        Self::open_paths(root, pipeline_path, &root.join("_config"))
    }

    /// The project at `root` with its config at `config_path`.
    pub fn open_paths(root: &Path, pipeline_path: &Path, config_path: &Path) -> Result<Self, String> {
        let storage = Storage::load(root, pipeline_path, config_path).map_err(|e| e.to_string())?;
        Ok(Project {
            root: root.to_path_buf(),
            pipeline_path: pipeline_path.to_path_buf(),
            config_path: config_path.to_path_buf(),
            storage,
            config: Config::new(config_path),
        })
    }

    /// The project TumblePipe's environment names, as `api.default_client`:
    /// `TH_PROJECT_PATH`, `TH_PIPELINE_PATH` and `TH_CONFIG_PATH`.
    pub fn from_env() -> Result<Self, String> {
        let var = |key: &str| -> Result<PathBuf, String> {
            std::env::var_os(key).filter(|v| !v.is_empty()).map(PathBuf::from).ok_or_else(|| format!("{key} environment variable not set"))
        };
        Self::open_paths(&var("TH_PROJECT_PATH")?, &var("TH_PIPELINE_PATH")?, &var("TH_CONFIG_PATH")?)
    }

    /// A fresh snapshot of the config database, to see writes made since.
    pub fn refresh(&mut self) {
        self.config = Config::new(&self.config_path);
    }
}
