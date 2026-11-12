//! A minimal project on disk, for tests here and in the crates that use
//! this one (feature `testing`).

use std::path::PathBuf;

use tempfile::TempDir;

use crate::project;

/// The storage table of the project template.
pub const STORAGE_JSON: &str = r#"{
    "assets": "{project_path}/assets",
    "shots": "{project_path}/shots",
    "groups": "{project_path}/groups",
    "export": "{project_path}/export",
    "cache": "{project_parent}/{project_name}_cache",
    "render": "{project_parent}/{project_name}_render",
    "turntable": "{project_parent}/{project_name}_turntable",
    "temp": "{local_temp}/th_temp/{project_name}",
    "proxy": "{project_parent}/{project_name}_proxy",
    "preset": "{config_path}/presets",
    "edit": "{project_path}/edit",
    "edits": "{project_path}/edits"
}
"#;

/// A minimal valid project (layout [`project::CONFIG_LAYOUT_VERSION`]) in a
/// temporary folder: the folder, and the project root inside it.
pub fn make_project() -> (TempDir, PathBuf) {
    let dir = TempDir::new().unwrap();
    let root = dir.path().join("Show");
    let config = root.join("_config");
    std::fs::create_dir_all(config.join("db")).unwrap();
    std::fs::write(config.join(project::VERSION_FILE), format!("{{\n    \"version\": {}\n}}\n", project::CONFIG_LAYOUT_VERSION)).unwrap();
    std::fs::write(config.join(project::STORAGE_FILE), STORAGE_JSON).unwrap();
    let db = config.join("db");
    let writes = [
        ("config.json", r#"{"properties":{},"children":{"project":{"properties":{"fps":24},"children":{}}}}"#),
        ("departments.json", r#"{"properties":{},"children":{}}"#),
        (
            "entity.json",
            r#"{"properties":{},"children":{"shots":{"properties":{},"children":{}},"assets":{"properties":{},"children":{}}}}"#,
        ),
        ("groups.json", r#"{"properties":{},"children":{}}"#),
        ("procedurals.json", r#"{"properties":{},"children":{}}"#),
        ("scenes.json", r#"{"children":{}}"#),
        ("schemas.json", r#"{"properties":{},"children":{}}"#),
    ];
    for (name, body) in writes {
        std::fs::write(db.join(name), body).unwrap();
    }
    (dir, root)
}
