//! Where a project's URIs live on disk (`tumblepipe.storage`).
//!
//! `project:/`, `pipeline:/` and `config:/` are the directories TumblePipe
//! is launched with. Every other purpose comes from the project's
//! `_config/storage.json`, a table of path templates over [`VARIABLES`],
//! which must list exactly [`PURPOSES`].

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::Value;
use thiserror::Error;

use crate::io::load_json;
use crate::uri::Uri;

pub const STORAGE_FILE: &str = "storage.json";

/// The purposes `storage.json` maps (`storage.PURPOSES`, and the wizard's
/// `conventions::PURPOSES`).
pub const PURPOSES: [&str; 12] =
    ["assets", "shots", "groups", "export", "cache", "render", "turntable", "temp", "proxy", "preset", "edit", "edits"];

/// The purpose layout v23 added. A project before it has no `edits` in its
/// table (tumblepipe-mcp reads layouts back to v13); resolving `edits:` there
/// is an unknown purpose.
pub const EDITS: &str = "edits";

/// The placeholders a template may use (`storage.VARIABLES`).
pub const VARIABLES: [&str; 7] = ["project_path", "project_parent", "project_name", "pipeline_path", "config_path", "home", "local_temp"];

#[derive(Debug, Error, PartialEq, Eq)]
pub enum StorageError {
    #[error("{0}")]
    Table(String),
    #[error("Cannot resolve a wildcard URI to a path: {0}")]
    Wildcard(String),
    #[error("'{purpose}' is not a storage purpose: {uri}")]
    UnknownPurpose { purpose: String, uri: String },
}

/// `path` as text with `/` separators, as Python's `str(path).replace('\\', '/')`.
pub fn slashed(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

/// The machine's home directory, as Python's `Path.home()`.
fn home() -> PathBuf {
    let var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    std::env::var_os(var).map(PathBuf::from).unwrap_or_default()
}

/// `$TH_TEMP`, else the OS temp directory (`tempfile.gettempdir()`).
fn local_temp() -> PathBuf {
    match std::env::var_os("TH_TEMP") {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        _ => std::env::temp_dir(),
    }
}

/// Python's `str.format_map` over `variables`, for the subset templates
/// use: `{name}` placeholders and `{{`/`}}` escapes.
fn format_map(template: &str, variables: &BTreeMap<&str, String>) -> Result<String, String> {
    let mut out = String::new();
    let mut chars = template.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '{' if chars.peek() == Some(&'{') => {
                chars.next();
                out.push('{');
            }
            '}' if chars.peek() == Some(&'}') => {
                chars.next();
                out.push('}');
            }
            '{' => {
                let mut name = String::new();
                loop {
                    match chars.next() {
                        Some('}') => break,
                        Some(c) => name.push(c),
                        None => return Err("unmatched '{'".into()),
                    }
                }
                let value = variables.get(name.as_str()).ok_or_else(|| format!("'{name}'"))?;
                out.push_str(value);
            }
            '}' => return Err("single '}'".into()),
            c => out.push(c),
        }
    }
    Ok(out)
}

/// `text` as a path, normalized as Python's `Path()` does: separators
/// collapsed, `.` parts dropped (`{project_parent}` of a drive root ends in
/// a separator, so a template gives `P:/` + `/Show_cache`). Only a leading
/// drive or share is one; a `C:` further in is a name, as in Python.
fn normalize(text: &str) -> PathBuf {
    let is_sep = |c: char| c == '/' || (cfg!(windows) && c == '\\');
    let separator = std::path::MAIN_SEPARATOR_STR;
    let mut head = String::new();
    let mut rest = text;
    if let Some(std::path::Component::Prefix(prefix)) = Path::new(text).components().next() {
        let len = prefix.as_os_str().len().min(text.len());
        head.push_str(&text[..len]);
        rest = &text[len..];
    }
    if rest.starts_with(is_sep) {
        head.push_str(separator);
    }
    let parts: Vec<&str> = rest.split(is_sep).filter(|part| !part.is_empty() && *part != ".").collect();
    PathBuf::from(head + &parts.join(separator))
}

/// Resolves storage URIs to paths for one project.
#[derive(Debug, Clone)]
pub struct Storage {
    roots: BTreeMap<String, PathBuf>,
}

impl Storage {
    /// The storage of the project at `project_path`, launched with
    /// `pipeline_path`, its config at `config_path`.
    pub fn load(project_path: &Path, pipeline_path: &Path, config_path: &Path) -> Result<Self, StorageError> {
        let table_path = config_path.join(STORAGE_FILE);
        if !table_path.is_file() {
            return Err(StorageError::Table(format!(
                "{} does not exist. The project config predates layout v11; run the migration (tt_prepare --migrate, or scripts/migrate_config.py) to create it.",
                table_path.display()
            )));
        }
        let table = load_json(&table_path).map_err(StorageError::Table)?;
        Self::from_table(&table, project_path, pipeline_path, config_path)
    }

    /// The storage `table` (`storage.json`'s contents) gives.
    pub fn from_table(table: &Value, project_path: &Path, pipeline_path: &Path, config_path: &Path) -> Result<Self, StorageError> {
        let file = config_path.join(STORAGE_FILE);
        let table = table.as_object().ok_or_else(|| StorageError::Table(format!("{} is not a JSON object", file.display())))?;
        let missing: Vec<_> = PURPOSES.iter().filter(|p| **p != EDITS && !table.contains_key(**p)).collect();
        let unknown: Vec<_> = table.keys().filter(|p| !PURPOSES.contains(&p.as_str())).collect();
        if !missing.is_empty() || !unknown.is_empty() {
            return Err(StorageError::Table(format!(
                "{} must map exactly {}; missing {missing:?}, unknown {unknown:?}",
                file.display(),
                PURPOSES.join(", ")
            )));
        }
        let parent = project_path.parent().unwrap_or(Path::new(""));
        let name = project_path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let variables: BTreeMap<&str, String> = [
            ("project_path", slashed(project_path)),
            ("project_parent", slashed(parent)),
            ("project_name", name),
            ("pipeline_path", slashed(pipeline_path)),
            ("config_path", slashed(config_path)),
            ("home", slashed(&home())),
            ("local_temp", slashed(&local_temp())),
        ]
        .into_iter()
        .collect();
        let mut roots = BTreeMap::new();
        roots.insert("project".to_string(), project_path.to_path_buf());
        roots.insert("pipeline".to_string(), pipeline_path.to_path_buf());
        roots.insert("config".to_string(), config_path.to_path_buf());
        for (purpose, template) in table {
            let template =
                template.as_str().ok_or_else(|| StorageError::Table(format!("{}: '{purpose}' is not a string", file.display())))?;
            let path = format_map(template, &variables).map_err(|error| {
                StorageError::Table(format!(
                    "{}: '{purpose}' is '{template}', which is not a template over {} ({error})",
                    file.display(),
                    VARIABLES.join(", ")
                ))
            })?;
            roots.insert(purpose.clone(), normalize(&path));
        }
        Ok(Storage { roots })
    }

    /// Where `uri` is: its purpose's root joined with its segments.
    pub fn resolve(&self, uri: &Uri) -> Result<PathBuf, StorageError> {
        if uri.is_wild() {
            return Err(StorageError::Wildcard(uri.to_string()));
        }
        let root = self
            .roots
            .get(&uri.purpose)
            .ok_or_else(|| StorageError::UnknownPurpose { purpose: uri.purpose.clone(), uri: uri.to_string() })?;
        Ok(uri.segments.iter().fold(root.clone(), |path, segment| path.join(segment)))
    }

    /// [`Storage::resolve`] of `purpose:/segments...`.
    pub fn path(&self, purpose: &str, segments: &[&str]) -> Result<PathBuf, StorageError> {
        self.resolve(&Uri::new(purpose, segments.iter().copied()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn table() -> Value {
        let mut table = serde_json::Map::new();
        for purpose in PURPOSES {
            table.insert(purpose.to_string(), json!(format!("{{project_path}}/{purpose}")));
        }
        table.insert("cache".into(), json!("{project_parent}/{project_name}_cache"));
        Value::Object(table)
    }

    #[test]
    fn resolves_templates() {
        let storage = Storage::from_table(&table(), Path::new("P:/Show"), Path::new("C:/pipe"), Path::new("P:/Show/_config")).unwrap();
        assert_eq!(slashed(&storage.resolve(&Uri::parse("export:/shots/010").unwrap()).unwrap()), "P:/Show/export/shots/010");
        assert_eq!(slashed(&storage.resolve(&Uri::parse("cache:/x").unwrap()).unwrap()), "P:/Show_cache/x");
        assert_eq!(slashed(&storage.resolve(&Uri::parse("project:/a").unwrap()).unwrap()), "P:/Show/a");
        assert!(storage.resolve(&Uri::parse("entity:/a").unwrap()).is_err());
        assert!(storage.resolve(&Uri::parse("export:/*").unwrap()).is_err());
    }

    #[test]
    fn refuses_a_wrong_table() {
        let mut bad = table();
        bad.as_object_mut().unwrap().remove("edit");
        assert!(Storage::from_table(&bad, Path::new("P:/S"), Path::new("C:/p"), Path::new("P:/S/_config")).is_err());
        let mut bad = table();
        bad.as_object_mut().unwrap().insert("cache".into(), json!("{nope}/x"));
        assert!(Storage::from_table(&bad, Path::new("P:/S"), Path::new("C:/p"), Path::new("P:/S/_config")).is_err());
    }

    /// A table from before layout v23 has no `edits`: it still opens, and
    /// `edits:` is an unknown purpose there.
    #[test]
    fn a_table_before_v23_opens_without_edits() {
        let mut old = table();
        old.as_object_mut().unwrap().remove(EDITS);
        let storage = Storage::from_table(&old, Path::new("P:/S"), Path::new("C:/p"), Path::new("P:/S/_config")).unwrap();
        assert!(storage.resolve(&Uri::parse("export:/a").unwrap()).is_ok());
        assert!(matches!(storage.resolve(&Uri::parse("edits:/a").unwrap()), Err(StorageError::UnknownPurpose { .. })));
    }
}
