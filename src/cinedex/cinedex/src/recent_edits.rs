//! The edits this user opened last, per project: what Play on shot cards
//! in Houdini reads to open the edit the shots are in, not an untitled one
//! (Auguu, 2026-11-22; `tumblepipe.pipe.edits.edit_for_shots`, which takes
//! the master edit first, then the one opened or saved last).
//!
//! One small JSON file beside the layout file (`layout_prefs`):
//! `{"<project root>": {"<edit>": <seconds since 1970>}}`, the root
//! spelled with forward slashes (`th_pipe::storage::slashed`), as the
//! folded sequences are kept. Every window on an edit notes its edit when
//! it opens. A file that cannot be written only loses that note.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};

/// The file beside the layout file at `layout`.
pub fn path(layout: &Path) -> PathBuf {
    layout.with_file_name("recent_edits.json")
}

/// `value` (the file's JSON) with edit `name` of `project` opened at `at`
/// seconds, every other edit and project kept. A file that was not an
/// object starts again, and so does a project's entry that was not one.
pub fn noted(value: Value, project: &str, name: &str, at: u64) -> Value {
    let mut all = match value {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    let mut edits = match all.remove(project) {
        Some(Value::Object(map)) => map,
        _ => serde_json::Map::new(),
    };
    edits.insert(name.to_owned(), json!(at));
    all.insert(project.to_owned(), Value::Object(edits));
    Value::Object(all)
}

/// When edit `name` of `project` was last opened, as `value` says.
#[cfg(test)]
fn opened_at(value: &Value, project: &str, name: &str) -> Option<u64> {
    value.get(project)?.get(name)?.as_u64()
}

/// Note in the file at `path` that edit `name` of `project` opened now.
pub fn note(path: &Path, project: &str, name: &str) -> std::io::Result<()> {
    let at = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs());
    let all = th_pipe::io::load_or_default(path, Value::clone);
    th_pipe::io::store_pretty_json(path, &noted(all, project, name, at))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn name() -> impl Strategy<Value = String> {
        "[A-Za-z][A-Za-z0-9]{0,8}"
    }

    proptest! {
        /// Noting an edit sets its time and leaves every other edit, of
        /// its project and of the others, as it was; whatever the file
        /// held before, it is an object of objects after.
        #[test]
        fn noting_an_edit_keeps_the_others(
            before in prop::collection::btree_map(name(), prop::collection::btree_map(name(), 0u64..u64::MAX / 2, 0..4), 0..4),
            project in name(),
            edit in name(),
            at in 0u64..u64::MAX / 2,
            junk in any::<bool>(),
        ) {
            let value = if junk { json!([1, "x"]) } else { json!(before) };
            let after = noted(value, &project, &edit, at);
            prop_assert_eq!(opened_at(&after, &project, &edit), Some(at));
            if !junk {
                for (p, edits) in &before {
                    for (e, t) in edits {
                        if (p, e) != (&project, &edit) {
                            prop_assert_eq!(opened_at(&after, p, e), Some(*t));
                        }
                    }
                }
            }
            prop_assert!(after.as_object().unwrap().values().all(Value::is_object));
        }
    }

    #[test]
    fn a_project_entry_that_is_not_an_object_starts_again() {
        let after = noted(json!({"P:/show": 3, "P:/other": {"cut": 7}}), "P:/show", "cut", 9);
        assert_eq!(after, json!({"P:/show": {"cut": 9}, "P:/other": {"cut": 7}}));
    }

    #[test]
    fn the_file_is_written_whole_beside_the_layout() {
        let dir = tempfile::tempdir().unwrap();
        let file = path(&dir.path().join("cinedex").join("layout.json"));
        assert_eq!(file.file_name().unwrap(), "recent_edits.json");
        note(&file, "P:/show", "cut").unwrap();
        note(&file, "P:/show", "other").unwrap();
        let value: Value = serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
        assert!(opened_at(&value, "P:/show", "cut").is_some() && opened_at(&value, "P:/show", "other").is_some());
    }
}
