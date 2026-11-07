use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::Value;
use thiserror::Error;

use crate::db;

/// The newest `_config/` layout TumblePipe here knows. Mirrors
/// `tumblepipe.config.layout.CONFIG_LAYOUT_VERSION`: the layout the newest
/// TumblePipe config migration (`th_project_core::migration`) ends at, and
/// the number `scripts/project_template/_config/version.json` carries.
///
/// A newer project is refused: a future migration may change the shape of
/// the files TumblePipe here writes, and it cannot tell which ones did.
pub const CONFIG_LAYOUT_VERSION: u32 = 23;

/// The oldest `_config/` layout this reads and writes: the last one
/// that changed the shape of `_config/db/` or `storage.json`.
///
/// An older project would not fail on its own -- it would be read and
/// written wrongly (before v13 the publish channels were stored as
/// `variants`, so a channel added here would be invisible to the pipeline)
/// -- so it is refused up front with the fix. v14 to v23 refreshed
/// department templates, moved WSL paths out of `storage.json` values
/// (v17), added the `groom` department to the pool (v21), moved the
/// group schema under `schemas:/groups/<context>` (v22) and added the
/// `edits` context (v23: a storage purpose, a department context, schemas
/// and the `entity:/edits` root, all new keys); none changed a shape
/// TumblePipe here touches. Edits themselves need v23. When TumblePipe adds a migration, bump
/// [`CONFIG_LAYOUT_VERSION`], and this too if the new step changes a shape.
pub const OLDEST_LAYOUT_VERSION: u32 = 13;

/// `_config/version.json`: `{ "version": <n> }`, the last migration taken.
pub const VERSION_FILE: &str = "version.json";

/// `_config/storage.json`: storage purpose -> path template (layout v11+).
pub const STORAGE_FILE: &str = "storage.json";

const REQUIRED_DB_FILES: &[&str] =
    &["config.json", "departments.json", "entity.json", "groups.json", "procedurals.json", "scenes.json", "schemas.json"];

#[derive(Debug, Error)]
pub enum ProjectError {
    #[error("path does not exist: {0}")]
    Missing(PathBuf),
    #[error("not a directory: {0}")]
    NotADirectory(PathBuf),
    #[error("no _config directory at {0}")]
    NoConfigDir(PathBuf),
    #[error(
        "the project config at {config_path} is on layout v{version}; this reads \
         v{oldest} to v{newest}. Run the migration (launch the project from TumbleTrove \
         Desktop and accept the migration, or run `tt_prepare --migrate`) and try again",
        oldest = OLDEST_LAYOUT_VERSION,
        newest = CONFIG_LAYOUT_VERSION
    )]
    LayoutBehind { config_path: PathBuf, version: u32 },
    #[error(
        "the project config at {config_path} is on layout v{version}, newer than this \
         server reads (v{oldest} to v{newest}); it was migrated by a newer TumblePipe -- \
         update tumblepipe-mcp",
        oldest = OLDEST_LAYOUT_VERSION,
        newest = CONFIG_LAYOUT_VERSION
    )]
    LayoutAhead { config_path: PathBuf, version: u32 },
    #[error("unreadable {path}: {reason}")]
    BadVersionFile { path: PathBuf, reason: String },
    #[error("missing _config/storage.json (the storage purpose -> path table, layout v11+)")]
    MissingStorage,
    #[error("unreadable _config/storage.json: {0}")]
    BadStorage(String),
    #[error("missing db file: _config/db/{0}")]
    MissingDbFile(String),
    #[error("db error: {0}")]
    Db(#[from] db::DbError),
}

#[derive(Debug, Serialize)]
pub struct ProjectSummary {
    pub root: PathBuf,
    pub name: String,
    pub config_path: PathBuf,
    pub fps: Option<u32>,
    /// `_config/version.json`'s version; between [`OLDEST_LAYOUT_VERSION`]
    /// and [`CONFIG_LAYOUT_VERSION`] once detection succeeds.
    pub layout_version: u32,
    /// The raw `_config/storage.json` table (purpose -> path template with
    /// `{project_path}`-style placeholders), unresolved.
    pub storage: Value,
    pub db_files: Vec<String>,
}

/// Confirm `root` is a TumblePipe project and return a high-level summary.
///
/// A project is recognised by:
///   - the directory exists
///   - it contains `_config/`, whose `db/` holds the standard JSON tables
///   - `_config/version.json` records a layout from [`OLDEST_LAYOUT_VERSION`]
///     to [`CONFIG_LAYOUT_VERSION`] (any other is refused with the fix, see
///     [`ProjectError`])
///   - `_config/storage.json` is a JSON object (the storage table)
pub fn detect(root: &Path) -> Result<ProjectSummary, ProjectError> {
    if !root.exists() {
        return Err(ProjectError::Missing(root.to_path_buf()));
    }
    if !root.is_dir() {
        return Err(ProjectError::NotADirectory(root.to_path_buf()));
    }

    let config_path = root.join("_config");
    if !config_path.is_dir() {
        return Err(ProjectError::NoConfigDir(root.to_path_buf()));
    }

    let db_dir = config_path.join("db");
    let mut db_files = Vec::with_capacity(REQUIRED_DB_FILES.len());
    for name in REQUIRED_DB_FILES {
        let p = db_dir.join(name);
        if !p.is_file() {
            return Err(ProjectError::MissingDbFile((*name).to_string()));
        }
        db_files.push((*name).to_string());
    }

    let layout_version = require_supported_layout(&config_path)?;
    let storage = read_storage(&config_path)?;

    let fps = db::read(&db_dir, "config.json").ok().and_then(|v| {
        v.get("children")
            .and_then(|c| c.get("project"))
            .and_then(|p| p.get("properties"))
            .and_then(|p| p.get("fps"))
            .and_then(|f| f.as_u64())
            .map(|n| n as u32)
    });

    let name = root.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| "unnamed".to_string());

    Ok(ProjectSummary { root: root.to_path_buf(), name, config_path, fps, layout_version, storage, db_files })
}

/// The layout version `config_path` records; 0 when it has no
/// `version.json` (a project no migration has run on). Mirrors
/// `tumblepipe.config.layout.project_layout_version`.
pub fn layout_version(config_path: &Path) -> Result<u32, ProjectError> {
    let path = config_path.join(VERSION_FILE);
    if !path.is_file() {
        return Ok(0);
    }
    let bad = |reason: String| ProjectError::BadVersionFile { path: path.clone(), reason };
    let text = std::fs::read_to_string(&path).map_err(|e| bad(e.to_string()))?;
    let value: Value = serde_json::from_str(&text).map_err(|e| bad(e.to_string()))?;
    value
        .get("version")
        .and_then(Value::as_u64)
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| bad("expected {\"version\": <non-negative integer>}".into()))
}

/// Refuse `config_path` unless its layout is one this reads
/// ([`OLDEST_LAYOUT_VERSION`] to [`CONFIG_LAYOUT_VERSION`]); return the
/// version on success. The range form of
/// `tumblepipe.config.layout.require_current_layout`, which accepts exactly
/// its own layout.
pub fn require_supported_layout(config_path: &Path) -> Result<u32, ProjectError> {
    let version = layout_version(config_path)?;
    let config_path = config_path.to_path_buf();
    if version < OLDEST_LAYOUT_VERSION {
        Err(ProjectError::LayoutBehind { config_path, version })
    } else if version > CONFIG_LAYOUT_VERSION {
        Err(ProjectError::LayoutAhead { config_path, version })
    } else {
        Ok(version)
    }
}

/// Read `_config/storage.json` as-is. The server never resolves storage
/// paths (it only touches `_config/`), so the placeholders are left alone.
fn read_storage(config_path: &Path) -> Result<Value, ProjectError> {
    let path = config_path.join(STORAGE_FILE);
    if !path.is_file() {
        return Err(ProjectError::MissingStorage);
    }
    let text = std::fs::read_to_string(&path).map_err(|e| ProjectError::BadStorage(e.to_string()))?;
    let value: Value = serde_json::from_str(&text).map_err(|e| ProjectError::BadStorage(e.to_string()))?;
    if !value.is_object() {
        return Err(ProjectError::BadStorage("expected a JSON object mapping purpose -> path template".into()));
    }
    Ok(value)
}

/// Return the `_config/db/` path for a project root. Caller is responsible
/// for ensuring the root has been [`detect`]ed first.
pub fn db_dir(root: &Path) -> PathBuf {
    root.join("_config").join("db")
}

#[derive(Debug, Serialize)]
pub struct ProjectInfo {
    pub root: PathBuf,
    pub name: String,
    pub config_path: PathBuf,
    pub fps: Option<u32>,
    pub layout_version: u32,
    /// Raw `_config/storage.json` table (purpose -> path template).
    pub storage: Value,
    pub db_files: Vec<String>,
    /// Raw `entity:/.properties.render` object (e.g. `{ resolution: [...], camera: "..." }`).
    /// `{}` if the project hasn't set any render defaults yet.
    pub render: Value,
    /// Raw `entity:/.properties.farm` object (pools, default_pool, priorities,
    /// default_priority). `{}` if the project hasn't configured a farm.
    pub farm: Value,
    pub schemas: SchemasInfo,
    pub counts: ProjectCounts,
}

#[derive(Debug, Serialize)]
pub struct SchemasInfo {
    /// Top-level keys under `schemas:/` (e.g. `entity`, `groups`).
    pub top_level: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ProjectCounts {
    pub sequences: u32,
    pub shots: u32,
    pub asset_categories: u32,
    pub assets: u32,
    pub departments: DepartmentCounts,
    pub groups: GroupCounts,
}

#[derive(Debug, Serialize)]
pub struct DepartmentCounts {
    pub assets: u32,
    pub shots: u32,
    pub render: u32,
}

#[derive(Debug, Serialize)]
pub struct GroupCounts {
    pub assets: u32,
    pub shots: u32,
}

/// Detect the project and return an enriched summary suitable for an overview
/// dashboard: render defaults, farm config, schemas top-level, and counts of
/// entities/departments/groups.
pub fn inspect(root: &Path) -> Result<ProjectInfo, ProjectError> {
    let summary = detect(root)?;
    let db_dir = db_dir(root);

    let entity = db::read(&db_dir, "entity.json")?;
    let departments = db::read(&db_dir, "departments.json")?;
    let groups = db::read(&db_dir, "groups.json")?;
    let schemas = db::read(&db_dir, "schemas.json")?;

    let render = entity.pointer("/properties/render").cloned().unwrap_or_else(|| serde_json::json!({}));
    let farm = entity.pointer("/properties/farm").cloned().unwrap_or_else(|| serde_json::json!({}));

    let sequences = db::children_at(&entity, ["shots"]);
    let shots_total: usize = sequences.iter().map(|seq| db::children_at(&entity, ["shots", seq.as_str()]).len()).sum();
    let categories = db::children_at(&entity, ["assets"]);
    let assets_total: usize = categories.iter().map(|cat| db::children_at(&entity, ["assets", cat.as_str()]).len()).sum();

    let counts = ProjectCounts {
        sequences: sequences.len() as u32,
        shots: shots_total as u32,
        asset_categories: categories.len() as u32,
        assets: assets_total as u32,
        departments: DepartmentCounts {
            assets: db::children_at(&departments, ["assets"]).len() as u32,
            shots: db::children_at(&departments, ["shots"]).len() as u32,
            render: db::children_at(&departments, ["render"]).len() as u32,
        },
        groups: GroupCounts {
            assets: db::children_at(&groups, ["assets"]).len() as u32,
            shots: db::children_at(&groups, ["shots"]).len() as u32,
        },
    };

    let schemas_info = SchemasInfo { top_level: db::children_at(&schemas, std::iter::empty::<&str>()) };

    Ok(ProjectInfo {
        root: summary.root,
        name: summary.name,
        config_path: summary.config_path,
        fps: summary.fps,
        layout_version: summary.layout_version,
        storage: summary.storage,
        db_files: summary.db_files,
        render,
        farm,
        schemas: schemas_info,
        counts,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::make_project;
    use serde_json::json;
    use std::fs;
    use tempfile::TempDir;

    fn write_entity(root: &Path, v: Value) {
        fs::write(root.join("_config/db/entity.json"), serde_json::to_string(&v).unwrap()).unwrap();
    }

    #[test]
    fn inspect_blank_project_has_zero_counts() {
        let (_dir, root) = make_project();
        let info = inspect(&root).unwrap();
        assert_eq!(info.name, root.file_name().unwrap().to_string_lossy());
        assert_eq!(info.fps, Some(24));
        assert_eq!(info.counts.sequences, 0);
        assert_eq!(info.counts.shots, 0);
        assert_eq!(info.counts.asset_categories, 0);
        assert_eq!(info.counts.assets, 0);
        assert_eq!(info.counts.departments.shots, 0);
        assert_eq!(info.counts.groups.assets, 0);
        assert_eq!(info.render, json!({}));
        assert_eq!(info.farm, json!({}));
        assert!(info.schemas.top_level.is_empty());
    }

    #[test]
    fn inspect_counts_entities_across_seqs_and_categories() {
        let (_dir, root) = make_project();
        write_entity(
            &root,
            json!({
                "properties": {
                    "render": { "resolution": [3840, 2160], "camera": "/cameras/render" },
                    "farm": { "pools": ["default", "highmem"], "default_pool": "default" }
                },
                "children": {
                    "shots": {
                        "properties": {}, "children": {
                            "010": { "properties": {}, "children": {
                                "010_010": { "properties": {}, "children": {} },
                                "010_020": { "properties": {}, "children": {} }
                            }},
                            "020": { "properties": {}, "children": {
                                "020_010": { "properties": {}, "children": {} }
                            }}
                        }
                    },
                    "assets": {
                        "properties": {}, "children": {
                            "characters": { "properties": {}, "children": {
                                "hero": { "properties": {}, "children": {} }
                            }},
                            "props": { "properties": {}, "children": {} }
                        }
                    }
                }
            }),
        );
        let info = inspect(&root).unwrap();
        assert_eq!(info.counts.sequences, 2);
        assert_eq!(info.counts.shots, 3);
        assert_eq!(info.counts.asset_categories, 2);
        assert_eq!(info.counts.assets, 1);
        assert_eq!(info.render["resolution"][0].as_u64(), Some(3840));
        assert_eq!(info.render["camera"].as_str(), Some("/cameras/render"));
        assert_eq!(info.farm["pools"].as_array().unwrap().len(), 2);
        assert_eq!(info.farm["default_pool"].as_str(), Some("default"));
    }

    #[test]
    fn inspect_lists_schemas_top_level() {
        let (_dir, root) = make_project();
        fs::write(
            root.join("_config/db/schemas.json"),
            serde_json::to_string(&json!({
                "properties": {},
                "children": {
                    "entity": { "properties": {}, "children": {} },
                    "groups": { "properties": {}, "children": {} }
                }
            }))
            .unwrap(),
        )
        .unwrap();
        let info = inspect(&root).unwrap();
        let mut got = info.schemas.top_level.clone();
        got.sort();
        assert_eq!(got, vec!["entity".to_string(), "groups".to_string()]);
    }

    #[test]
    fn inspect_rejects_non_project() {
        let dir = TempDir::new().unwrap();
        let err = inspect(dir.path()).unwrap_err();
        // The project lacks _config/, so detect should fail before we read.
        assert!(matches!(err, ProjectError::NoConfigDir(_)));
    }

    fn write_version(root: &Path, body: &str) {
        fs::write(root.join("_config").join(VERSION_FILE), body).unwrap();
    }

    #[test]
    fn detect_reports_layout_and_storage() {
        let (_dir, root) = make_project();
        let summary = detect(&root).unwrap();
        assert_eq!(summary.layout_version, CONFIG_LAYOUT_VERSION);
        assert_eq!(summary.storage["assets"].as_str(), Some("{project_path}/assets"));
    }

    #[test]
    fn detect_needs_no_convention_modules() {
        // Layout v11 retired the three *_convention.py modules.
        let (_dir, root) = make_project();
        for name in ["config_convention.py", "naming_convention.py", "storage_convention.py"] {
            assert!(!root.join("_config").join(name).exists());
        }
        detect(&root).unwrap();
    }

    #[test]
    fn detect_refuses_unstamped_project_as_v0() {
        let (_dir, root) = make_project();
        fs::remove_file(root.join("_config").join(VERSION_FILE)).unwrap();
        let err = detect(&root).unwrap_err();
        assert!(matches!(err, ProjectError::LayoutBehind { version: 0, .. }));
        assert!(err.to_string().contains("tt_prepare --migrate"));
    }

    #[test]
    fn detect_refuses_older_layout_with_the_fix() {
        let (_dir, root) = make_project();
        write_version(&root, r#"{"version": 12}"#);
        let err = detect(&root).unwrap_err();
        assert!(matches!(err, ProjectError::LayoutBehind { version: 12, .. }));
        let msg = err.to_string();
        assert!(msg.contains("v12"), "{msg}");
        assert!(msg.contains("v13"), "{msg}");
        assert!(msg.contains("tt_prepare --migrate"), "{msg}");
    }

    #[test]
    fn detect_refuses_newer_layout() {
        let (_dir, root) = make_project();
        let newer = CONFIG_LAYOUT_VERSION + 1;
        write_version(&root, &format!(r#"{{"version": {newer}}}"#));
        let err = detect(&root).unwrap_err();
        assert!(matches!(err, ProjectError::LayoutAhead { version, .. } if version == newer));
        assert!(err.to_string().contains("update tumblepipe-mcp"));
    }

    /// Every layout from the last shape change to the newest known reads:
    /// the later steps only touched templates and storage values, so a
    /// project one TumblePipe release behind (or ahead of an older server
    /// pin) still works.
    #[test]
    fn detect_accepts_every_supported_layout() {
        let (_dir, root) = make_project();
        for version in OLDEST_LAYOUT_VERSION..=CONFIG_LAYOUT_VERSION {
            write_version(&root, &format!(r#"{{"version": {version}}}"#));
            assert_eq!(detect(&root).unwrap().layout_version, version);
        }
    }

    #[test]
    fn detect_refuses_malformed_version_file() {
        let (_dir, root) = make_project();
        write_version(&root, r#"{"version": "13"}"#);
        assert!(matches!(detect(&root).unwrap_err(), ProjectError::BadVersionFile { .. }));
    }

    proptest::proptest! {
        /// Any recorded layout is accepted exactly inside
        /// OLDEST_LAYOUT_VERSION..=CONFIG_LAYOUT_VERSION, and a refusal says
        /// which side it fell on (the message carries the fix for that
        /// side). 0.6.0 accepted only v13, so every project TumblePipe had
        /// migrated since was refused by every tool.
        #[test]
        fn layout_accepted_exactly_inside_the_range(
            version in proptest::prop_oneof![
                0u32..64,
                proptest::prelude::Just(u32::MAX),
            ]
        ) {
            let (_dir, root) = make_project();
            write_version(&root, &format!(r#"{{"version": {version}}}"#));
            match detect(&root) {
                Ok(summary) => {
                    proptest::prop_assert!(
                        (OLDEST_LAYOUT_VERSION..=CONFIG_LAYOUT_VERSION).contains(&version)
                    );
                    proptest::prop_assert_eq!(summary.layout_version, version);
                }
                Err(ProjectError::LayoutBehind { version: v, .. }) => {
                    proptest::prop_assert_eq!(v, version);
                    proptest::prop_assert!(version < OLDEST_LAYOUT_VERSION);
                }
                Err(ProjectError::LayoutAhead { version: v, .. }) => {
                    proptest::prop_assert_eq!(v, version);
                    proptest::prop_assert!(version > CONFIG_LAYOUT_VERSION);
                }
                Err(e) => proptest::prop_assert!(false, "unexpected error {e}"),
            }
        }
    }

    #[test]
    fn detect_requires_storage_table() {
        let (_dir, root) = make_project();
        fs::remove_file(root.join("_config").join(STORAGE_FILE)).unwrap();
        assert!(matches!(detect(&root).unwrap_err(), ProjectError::MissingStorage));
        fs::write(root.join("_config").join(STORAGE_FILE), "[]").unwrap();
        assert!(matches!(detect(&root).unwrap_err(), ProjectError::BadStorage(_)));
    }
}
