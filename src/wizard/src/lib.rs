//! Pure (GUI-free) core of the tt_setup wizard: project validation and the
//! template scaffold + config-DB customization. Kept separate from `main.rs`
//! so the byte-for-byte JSON parity with the old Python wizard can be pinned
//! by unit tests without spinning up a window.

use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::{json, Value};

/// Standard top-level project directories, created after the template copy.
/// Mirrors `TOP_LEVEL_DIRS` in the old scripts/tt_setup.py.
pub const TOP_LEVEL_DIRS: [&str; 5] = ["assets", "shots", "groups", "kits", "export"];

// ---------- validation ------------------------------------------------------

/// Alphanumeric, non-empty — same rule as the Python `str.isalnum()` gate.
pub fn is_valid_project_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(char::is_alphanumeric)
}

/// A directory looks like a project when it has `_config/db/entity.json`.
pub fn looks_like_project(path: &Path) -> bool {
    path.join("_config").join("db").join("entity.json").is_file()
}

/// Expand a leading `~` to the user's home directory (mirrors
/// `Path.expanduser()` for the common cases the wizard sees).
pub fn expanduser(text: &str) -> PathBuf {
    if let Some(rest) = text.strip_prefix('~') {
        if rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\') {
            if let Some(home) = home_dir() {
                let rest = rest.trim_start_matches(['/', '\\']);
                return if rest.is_empty() { home } else { home.join(rest) };
            }
        }
    }
    PathBuf::from(text)
}

pub fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

// ---------- scaffolding -----------------------------------------------------

/// Copy the template, create the top-level dirs, and customize the config DBs.
/// On any failure the partially-created `target` is removed so a retry starts
/// clean (mirrors the Python validatePage rmtree-on-error).
pub fn scaffold_project(
    template_dir: &Path,
    target: &Path,
    project_name: &str,
    fps: i64,
) -> Result<(), String> {
    if !template_dir.is_dir() {
        return Err(format!(
            "Bundled project template not found at {}. The TumblePipe package is incomplete.",
            template_dir.display()
        ));
    }

    let result = (|| -> Result<(), String> {
        copy_template_tree(template_dir, target)
            .map_err(|e| format!("Failed to copy template: {e}"))?;
        for sub in TOP_LEVEL_DIRS {
            std::fs::create_dir_all(target.join(sub))
                .map_err(|e| format!("Failed to create '{sub}' directory: {e}"))?;
        }
        customize_template(target, project_name, fps)
    })();

    if result.is_err() {
        let _ = std::fs::remove_dir_all(target);
    }
    result
}

/// Copy `template_dir` -> `target`, skipping `__pycache__/`, `*.pyc`, `*.bak`
/// (the same carve-outs as the Python `shutil.ignore_patterns`). `target` must
/// not already exist.
pub fn copy_template_tree(template_dir: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(target)?;
    for entry in std::fs::read_dir(template_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str == "__pycache__" || name_str.ends_with(".pyc") || name_str.ends_with(".bak") {
            continue;
        }
        let src = entry.path();
        let dst = target.join(&name);
        if entry.file_type()?.is_dir() {
            copy_template_tree(&src, &dst)?;
        } else {
            std::fs::copy(&src, &dst)?;
        }
    }
    Ok(())
}

/// Patch the three config databases in-place with the new project's name and
/// fps, preserving key order and formatting.
pub fn customize_template(project_path: &Path, project_name: &str, fps: i64) -> Result<(), String> {
    let db = project_path.join("_config").join("db");

    let entity_path = db.join("entity.json");
    let mut entity = load_json(&entity_path)?;
    patch_entity(&mut entity, project_name);
    store_json(&entity_path, &entity)?;

    let config_path = db.join("config.json");
    let mut config = load_json(&config_path)?;
    patch_config(&mut config, fps);
    store_json(&config_path, &config)?;

    let schemas_path = db.join("schemas.json");
    let mut schemas = load_json(&schemas_path)?;
    patch_schemas(&mut schemas, fps);
    store_json(&schemas_path, &schemas)?;

    Ok(())
}

/// entity.json: the project's own farm pools default to its name.
pub fn patch_entity(entity: &mut Value, project_name: &str) {
    let farm = ensure_object(entity, &["properties", "farm"]);
    farm.insert("pools".to_string(), json!([project_name]));
    farm.insert("default_pool".to_string(), json!(project_name));
}

/// config.json: project fps.
pub fn patch_config(config: &mut Value, fps: i64) {
    let props = ensure_object(config, &["children", "project", "properties"]);
    props.insert("fps".to_string(), json!(fps));
}

/// schemas.json: the fps default appears in two places.
pub fn patch_schemas(schemas: &mut Value, fps: i64) {
    ensure_object(schemas, &["children", "entity", "properties"])
        .insert("fps".to_string(), json!(fps));
    ensure_object(
        schemas,
        &["children", "config", "children", "project", "properties"],
    )
    .insert("fps".to_string(), json!(fps));
}

/// Navigate/create a nested-object path and return the leaf object, creating
/// missing intermediates as empty objects at the end of their parent — the
/// same behavior as chained Python `dict.setdefault({})`. `serde_json::Map`'s
/// insert keeps an existing key's position, so patching a value that already
/// exists never reorders the file.
fn ensure_object<'a>(root: &'a mut Value, path: &[&str]) -> &'a mut serde_json::Map<String, Value> {
    let mut cur = root
        .as_object_mut()
        .expect("config DB root must be a JSON object");
    for key in path {
        cur = cur
            .entry((*key).to_string())
            .or_insert_with(|| Value::Object(serde_json::Map::new()))
            .as_object_mut()
            .expect("config DB path element must be a JSON object");
    }
    cur
}

pub fn load_json(path: &Path) -> Result<Value, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("Could not read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("Invalid JSON in {}: {e}", path.display()))
}

/// Serialize with 4-space indentation and a single trailing newline, matching
/// the Python `json.dump(data, fh, indent=4)` + `fh.write('\n')` output.
pub fn to_json_string(value: &Value) -> String {
    let mut buf = Vec::new();
    let formatter = serde_json::ser::PrettyFormatter::with_indent(b"    ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    value.serialize(&mut ser).expect("Value serialization");
    buf.push(b'\n');
    String::from_utf8(buf).expect("serde_json emits UTF-8")
}

pub fn store_json(path: &Path, value: &Value) -> Result<(), String> {
    std::fs::write(path, to_json_string(value))
        .map_err(|e| format!("Could not write {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_project_names() {
        assert!(is_valid_project_name("myfilm"));
        assert!(is_valid_project_name("Film2"));
        assert!(!is_valid_project_name(""));
        assert!(!is_valid_project_name("my film"));
        assert!(!is_valid_project_name("my-film"));
        assert!(!is_valid_project_name("my_film"));
    }

    #[test]
    fn entity_patch_replaces_pools_in_place_and_keeps_order() {
        let src = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"default\"\n            ],\n            \"default_pool\": \"default\"\n        }\n    },\n    \"children\": {}\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_entity(&mut v, "myfilm");
        let expected = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"myfilm\"\n            ],\n            \"default_pool\": \"myfilm\"\n        }\n    },\n    \"children\": {}\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn config_patch_sets_fps() {
        let src = "{\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 24\n            },\n            \"children\": {}\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_config(&mut v, 30);
        let expected = "{\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 30\n            },\n            \"children\": {}\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn schemas_patch_sets_fps_in_both_places() {
        let src = "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {\n                \"fps\": 24\n            }\n        },\n        \"config\": {\n            \"children\": {\n                \"project\": {\n                    \"properties\": {\n                        \"fps\": 30\n                    }\n                }\n            }\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_schemas(&mut v, 48);
        let expected = "{\n    \"children\": {\n        \"entity\": {\n            \"properties\": {\n                \"fps\": 48\n            }\n        },\n        \"config\": {\n            \"children\": {\n                \"project\": {\n                    \"properties\": {\n                        \"fps\": 48\n                    }\n                }\n            }\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }

    #[test]
    fn untouched_floats_and_ints_round_trip_verbatim() {
        // arbitrary_precision keeps 0.0 / 0.5 / 1920 as their original tokens
        // instead of reformatting them — a naive f64 round-trip risks 0.0 -> 0.
        let src = "{\n    \"properties\": {\n        \"farm\": {\n            \"pools\": [\n                \"default\"\n            ],\n            \"default_pool\": \"default\"\n        },\n        \"render\": {\n            \"resolution\": [\n                1920,\n                1080\n            ],\n            \"overscan\": [\n                0.0,\n                0.0\n            ],\n            \"scale\": 0.5\n        }\n    }\n}\n";
        let mut v: Value = serde_json::from_str(src).unwrap();
        patch_entity(&mut v, "myfilm");
        let out = to_json_string(&v);
        assert!(out.contains("\"overscan\": [\n                0.0,\n                0.0\n            ]"));
        assert!(out.contains("\"scale\": 0.5"));
        assert!(out.contains("1920"));
    }

    #[test]
    fn ensure_object_appends_missing_intermediates() {
        let mut v: Value = serde_json::from_str("{\n    \"a\": 1\n}\n").unwrap();
        patch_config(&mut v, 25);
        // "a" stays first; the created children.project.properties.fps appends.
        let expected = "{\n    \"a\": 1,\n    \"children\": {\n        \"project\": {\n            \"properties\": {\n                \"fps\": 25\n            }\n        }\n    }\n}\n";
        assert_eq!(to_json_string(&v), expected);
    }
}
