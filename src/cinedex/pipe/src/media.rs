//! The media library (`designs/edit-entity.md` §3): files copied into the
//! project as named items with versions, for edits to use (an animatic, a
//! reference, music).
//!
//! An item version is `export:/media/<item>/vNNNN/<item>_vNNNN.<ext>` with
//! `context.json` last: where the file came from, who imported it and when,
//! the note, and what it is ([`Measure`]: a movie's frames and rate, any
//! file's seconds), which the importer measures (this crate decodes
//! nothing). Re-importing to a name makes its next version. Items are found
//! by listing the folders; there is no config node.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::Project;
use crate::io::{VERSION_MARKER, store_json};
use crate::naming::{entity_name_problem, is_name_alphabet};
use crate::versions::{is_finished_version, list_version_paths, next_version_path};

/// What an imported file is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Measure {
    /// A movie's frames and rate; None for sound alone.
    pub frames: Option<(u64, (i32, i32))>,
    /// How long it plays.
    pub seconds: f64,
    /// Whether it has sound.
    pub sound: bool,
}

/// One version of an item, as its `context.json` says.
#[derive(Debug, Clone, PartialEq)]
pub struct ItemVersion {
    pub version: String,
    pub path: PathBuf,
    pub measure: Measure,
    pub note: String,
    pub user: String,
}

/// Where the library is.
pub fn library_dir(project: &Project) -> Result<PathBuf, String> {
    project.storage.path("export", &["media"]).map_err(|e| e.to_string())
}

/// The items, by name.
pub fn list_items(project: &Project) -> Result<Vec<String>, String> {
    let Ok(entries) = std::fs::read_dir(library_dir(project)?) else { return Ok(Vec::new()) };
    let mut items: Vec<String> = entries
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|name| is_name_alphabet(name))
        .collect();
    items.sort();
    Ok(items)
}

/// Item `item`'s finished versions, oldest first.
pub fn item_versions(project: &Project, item: &str) -> Result<Vec<ItemVersion>, String> {
    list_version_paths(&library_dir(project)?.join(item))
        .into_iter()
        .filter(|dir| is_finished_version(dir))
        .map(|dir| read_version(item, &dir))
        .collect()
}

fn read_version(item: &str, dir: &Path) -> Result<ItemVersion, String> {
    let version = dir.file_name().unwrap().to_string_lossy().into_owned();
    let marker = dir.join(VERSION_MARKER);
    let context: Value = serde_json::from_str(&std::fs::read_to_string(&marker).map_err(|e| format!("{}: {e}", marker.display()))?)
        .map_err(|e| format!("{}: {e}", marker.display()))?;
    let file = context.get("file").and_then(Value::as_str).ok_or_else(|| format!("{}: no file", marker.display()))?;
    let frames = match (context.get("frames").and_then(Value::as_u64), context.get("rate").and_then(Value::as_array)) {
        (Some(n), Some(rate)) if rate.len() == 2 => Some((n, (rate[0].as_i64().unwrap_or(0) as i32, rate[1].as_i64().unwrap_or(1) as i32))),
        _ => None,
    };
    let text = |key: &str| context.get(key).and_then(Value::as_str).unwrap_or_default().to_owned();
    Ok(ItemVersion {
        version,
        path: dir.join(file),
        measure: Measure {
            frames,
            seconds: context.get("seconds").and_then(Value::as_f64).unwrap_or(0.0),
            sound: context.get("sound").and_then(Value::as_bool).unwrap_or(false),
        },
        note: text("note"),
        user: text("user"),
    })
    .and_then(|v| if v.version.is_empty() { Err(format!("{item}: a version without a name")) } else { Ok(v) })
}

/// Copy `file` into the library as item `item`'s next version, measured as
/// `measure`, with `note`: the new version.
pub fn import(project: &Project, file: &Path, item: &str, measure: Measure, note: &str) -> Result<ItemVersion, String> {
    if let Some(problem) = entity_name_problem(item) {
        return Err(format!("{item:?}: an item's name {problem}"));
    }
    if !file.is_file() {
        return Err(format!("{}: not a file", file.display()));
    }
    let root = library_dir(project)?.join(item);
    std::fs::create_dir_all(&root).map_err(|e| format!("{}: {e}", root.display()))?;
    let dir = next_version_path(&root);
    std::fs::create_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let version = dir.file_name().unwrap().to_string_lossy().into_owned();
    let ext = file.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    let name = if ext.is_empty() { format!("{item}_{version}") } else { format!("{item}_{version}.{ext}") };
    std::fs::copy(file, dir.join(&name)).map_err(|e| format!("copying {}: {e}", file.display()))?;
    let mut context = json!({
        "item": item,
        "version": version,
        "file": name,
        "original": crate::storage::slashed(file),
        "seconds": measure.seconds,
        "sound": measure.sound,
        "timestamp": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "user": crate::edit_files::user_name(),
        "note": note,
    });
    if let Some((frames, rate)) = measure.frames {
        context["frames"] = json!(frames);
        context["rate"] = json!([rate.0, rate.1]);
    }
    store_json(&dir.join(VERSION_MARKER), &context).map_err(|e| e.to_string())?;
    read_version(item, &dir)
}

/// Write `note` as the note of `item`'s version `version`; everything else
/// its `context.json` says stays.
pub fn set_note(project: &Project, item: &str, version: &str, note: &str) -> Result<(), String> {
    let marker = library_dir(project)?.join(item).join(version).join(VERSION_MARKER);
    let mut context: Value = serde_json::from_str(&std::fs::read_to_string(&marker).map_err(|e| format!("{}: {e}", marker.display()))?)
        .map_err(|e| format!("{}: {e}", marker.display()))?;
    let fields = context.as_object_mut().ok_or_else(|| format!("{}: not an object", marker.display()))?;
    fields.insert("note".into(), json!(note));
    store_json(&marker, &context).map_err(|e| e.to_string())
}

/// Give item `from` the name `to`: its folder is renamed, with every
/// version in it (the files keep the names they were copied under, which
/// each version's `context.json` gives). Refused when `to` is no name for
/// an item or is one already. An edit that uses the item names it as
/// `from`, and is not looked at here: the caller answers for that.
pub fn rename_item(project: &Project, from: &str, to: &str) -> Result<(), String> {
    if let Some(problem) = entity_name_problem(to) {
        return Err(format!("{to:?}: an item's name {problem}"));
    }
    let library = library_dir(project)?;
    let (old, new) = (library.join(from), library.join(to));
    if !is_name_alphabet(from) || !old.is_dir() {
        return Err(format!("{from}: no such item"));
    }
    // Names differ by more than case, or Windows would call them one folder.
    if list_items(project)?.iter().any(|item| item.eq_ignore_ascii_case(to)) {
        return Err(format!("{to}: there is an item of that name"));
    }
    std::fs::rename(&old, &new).map_err(|e| format!("renaming {}: {e}", old.display()))?;
    for dir in list_version_paths(&new).into_iter().filter(|dir| is_finished_version(dir)) {
        let marker = dir.join(VERSION_MARKER);
        let Ok(text) = std::fs::read_to_string(&marker) else { continue };
        let Ok(mut context) = serde_json::from_str::<Value>(&text) else { continue };
        if let Some(fields) = context.as_object_mut() {
            fields.insert("item".into(), json!(to));
            store_json(&marker, &context).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::make_project;

    /// A note written is the note read, of that version alone; a renamed
    /// item is found under its new name with every version, file and note
    /// it had, and under the old no more. A name taken (in any case), a
    /// bad name, an item that is not there: refused, and nothing moves.
    #[test]
    fn notes_and_names_change_and_nothing_else_does() {
        let (dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        let source = dir.path().join("cut3.mov");
        std::fs::write(&source, b"movie").unwrap();
        let movie = Measure { frames: Some((240, (24, 1))), seconds: 10.0, sound: true };
        import(&project, &source, "animatic", movie, "first").unwrap();
        import(&project, &source, "animatic", movie, "second").unwrap();
        import(&project, &source, "music", movie, "").unwrap();
        set_note(&project, "animatic", "v0002", "the \"new\" pass").unwrap();
        let before = item_versions(&project, "animatic").unwrap();
        assert_eq!(before.iter().map(|v| v.note.as_str()).collect::<Vec<_>>(), ["first", "the \"new\" pass"]);
        assert_eq!(before[1].measure, movie);
        assert!(set_note(&project, "animatic", "v0009", "x").is_err());

        for (from, to) in [("animatic", "music"), ("animatic", "MUSIC"), ("animatic", "bad name"), ("animatic", "nul"), ("nothere", "fine")]
        {
            assert!(rename_item(&project, from, to).is_err(), "{from} -> {to}");
            assert_eq!(list_items(&project).unwrap(), ["animatic", "music"]);
        }
        rename_item(&project, "animatic", "boards").unwrap();
        assert_eq!(list_items(&project).unwrap(), ["boards", "music"]);
        let after = item_versions(&project, "boards").unwrap();
        assert_eq!(after.len(), 2);
        for (was, is) in before.iter().zip(&after) {
            assert_eq!((&was.version, &was.note, &was.measure, &was.user), (&is.version, &is.note, &is.measure, &is.user));
            assert_eq!(was.path.file_name(), is.path.file_name());
            assert_eq!(std::fs::read(&is.path).unwrap(), b"movie");
        }
        assert!(item_versions(&project, "animatic").unwrap().is_empty());
        assert_eq!(item_versions(&project, "music").unwrap().len(), 1);
    }

    #[test]
    fn imports_are_versions_of_named_items() {
        let (dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        assert_eq!(list_items(&project).unwrap(), Vec::<String>::new());
        let source = dir.path().join("cut3.mov");
        std::fs::write(&source, b"movie").unwrap();
        let movie = Measure { frames: Some((240, (24, 1))), seconds: 10.0, sound: true };
        let first = import(&project, &source, "animatic", movie, "board pass").unwrap();
        assert_eq!(first.version, "v0001");
        assert!(first.path.ends_with("media/animatic/v0001/animatic_v0001.mov"), "{}", first.path.display());
        assert_eq!(std::fs::read(&first.path).unwrap(), b"movie");
        let wav = dir.path().join("music.WAV");
        std::fs::write(&wav, b"riff").unwrap();
        let sound = Measure { frames: None, seconds: 93.5, sound: true };
        import(&project, &wav, "music", sound, "").unwrap();
        let second = import(&project, &source, "animatic", movie, "").unwrap();
        assert_eq!(second.version, "v0002");
        assert_eq!(list_items(&project).unwrap(), ["animatic", "music"]);
        let versions = item_versions(&project, "animatic").unwrap();
        assert_eq!(versions.iter().map(|v| v.version.as_str()).collect::<Vec<_>>(), ["v0001", "v0002"]);
        assert_eq!(versions[0].note, "board pass");
        assert_eq!(versions[0].measure, movie);
        let music = item_versions(&project, "music").unwrap();
        assert_eq!(music[0].measure, sound);
        assert!(music[0].path.ends_with("music_v0001.wav"));
        // An unfinished version is not listed.
        std::fs::create_dir(library_dir(&project).unwrap().join("music").join("v0002")).unwrap();
        assert_eq!(item_versions(&project, "music").unwrap().len(), 1);
        assert!(import(&project, &source, "bad name", movie, "").is_err());
        assert!(import(&project, &dir.path().join("missing.mov"), "x", movie, "").is_err());
    }
}
