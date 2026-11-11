//! Where the hook apps find the configured TumblePipe project.
//!
//! `TH_PROJECT_PATH` is a required `[runtime]` var, but the desktop defers
//! package-declared `[runtime]` keys to Houdini's package file and hands a
//! hook only the `TT_*` context. The value it holds is in the project's own
//! manifest at `$TT_PROJECT_DIR/hpm.toml`, so that is read when the variable
//! is absent. The variable wins when present (the `--migrate` CLI and a
//! hand-set environment rely on it).
//!
//! Shared by `tt_prepare` (which project to migrate) and `tt_setup` (the
//! folder field's starting value, #35: it started empty even when the
//! desktop project already named the folder).

use std::path::{Path, PathBuf};

/// The configured project folder: `TH_PROJECT_PATH`, else the
/// `TH_PROJECT_PATH` recorded in `$TT_PROJECT_DIR/hpm.toml`.
pub fn configured_project_path() -> Result<PathBuf, String> {
    project_path_from(
        std::env::var("TH_PROJECT_PATH").ok().as_deref(),
        std::env::var("TT_PROJECT_DIR").ok().as_deref().map(Path::new),
    )
}

/// [`configured_project_path`] with the two variables passed in.
pub fn project_path_from(
    th_project_path: Option<&str>,
    tt_project_dir: Option<&Path>,
) -> Result<PathBuf, String> {
    if let Some(value) = th_project_path {
        if !value.trim().is_empty() {
            return Ok(PathBuf::from(value));
        }
    }
    if let Some(dir) = tt_project_dir {
        if !dir.as_os_str().is_empty() {
            return project_path_from_manifest(&dir.join("hpm.toml"));
        }
    }
    Err("TH_PROJECT_PATH is unset and TT_PROJECT_DIR names no project".to_string())
}

/// `TH_PROJECT_PATH` as the project's manifest records it, in the
/// `[runtime]` table the desktop writes: `TH_PROJECT_PATH = { method = "set",
/// value = "..." }`. Only a literal path is usable here — a value that refers
/// to another variable would need an expansion the hooks cannot do.
pub fn project_path_from_manifest(manifest: &Path) -> Result<PathBuf, String> {
    let text = std::fs::read_to_string(manifest)
        .map_err(|e| format!("cannot read {}: {e}", manifest.display()))?;
    let doc: toml::Value = toml::from_str(&text)
        .map_err(|e| format!("cannot parse {}: {e}", manifest.display()))?;
    let entry = doc.get("runtime").and_then(|runtime| runtime.get("TH_PROJECT_PATH"));
    let value = match entry {
        Some(toml::Value::Table(table)) => table.get("value").and_then(|v| v.as_str()),
        Some(toml::Value::String(s)) => Some(s.as_str()),
        _ => None,
    };
    match value {
        Some(v) if !v.trim().is_empty() && !v.contains('$') => Ok(PathBuf::from(v)),
        Some(v) => Err(format!(
            "TH_PROJECT_PATH in {} is {v:?}, which cannot be expanded here",
            manifest.display()
        )),
        None => Err(format!(
            "{} sets no TH_PROJECT_PATH — the project has not been configured",
            manifest.display()
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn manifest_with(dir: &Path, value: &str) {
        std::fs::write(
            dir.join("hpm.toml"),
            format!("[package]\nname = \"p\"\n\n[runtime]\nTH_PROJECT_PATH = {{ method = \"set\", value = {value:?} }}\n"),
        )
        .unwrap();
    }

    proptest! {
        /// A set TH_PROJECT_PATH wins over whatever the manifest says.
        #[test]
        fn the_variable_wins(path in "[A-Za-z]:/[a-z]{1,8}/[a-z]{1,8}", other in "/[a-z]{1,8}") {
            let dir = tempfile::tempdir().unwrap();
            manifest_with(dir.path(), &other);
            prop_assert_eq!(
                project_path_from(Some(&path), Some(dir.path())),
                Ok(PathBuf::from(&path))
            );
        }

        /// Unset or blank, the manifest's literal path is used.
        #[test]
        fn the_manifest_fills_in(path in "[A-Za-z]:/[a-z]{1,8}/[a-z]{1,8}", blank in "[ \t]{0,3}") {
            let dir = tempfile::tempdir().unwrap();
            manifest_with(dir.path(), &path);
            prop_assert_eq!(
                project_path_from(Some(&blank), Some(dir.path())),
                Ok(PathBuf::from(&path))
            );
            prop_assert_eq!(project_path_from(None, Some(dir.path())), Ok(PathBuf::from(&path)));
        }
    }

    #[test]
    fn nothing_set_is_an_error() {
        assert!(project_path_from(None, None).is_err());
        assert!(project_path_from(Some(""), None).is_err());
    }

    #[test]
    fn an_unexpandable_manifest_value_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        manifest_with(dir.path(), "$SHOWS/film");
        assert!(project_path_from(None, Some(dir.path())).is_err());
    }
}
