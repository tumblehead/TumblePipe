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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::make_project;

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
