//! An edit's files (`designs/edit-entity.md` §2.1, §2.2, §4).
//!
//! - **Working versions**: `edits:/<name>/edit/<name>_edit_vNNNN.json`, the
//!   working document, each with the workfile sidecar
//!   `_context/vNNNN.json` (user, timestamp, from and to version, note), as
//!   a hip save writes (`pipe.context.save_context`). A save is always the
//!   next version: one past the highest file there, never written over.
//! - **Autosave**: `<name>_edit_autosave.json` beside them, the document as
//!   it stood, for recovery after a crash.
//! - **Published versions**: `export:/edits/<name>/default/edit/vNNNN/`, the
//!   frozen document `<name>_edit_vNNNN.json`, its OpenTimelineIO
//!   `<name>_edit_vNNNN.otio`, and `context.json` last (the export context
//!   `pipe.context.save_export_context` writes, with the note): a version
//!   without it is unfinished and not listed.
//!
//! The edit's entity, `entity:/edits/<name>`, is created on its first save
//! ([`create_edit`]).

use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};
use th_edit::document::{FrozenEdit, WorkingEdit};

use crate::Project;
use crate::io::{VERSION_MARKER, store_json};
use crate::naming::{is_valid_entity_name, is_valid_version_name, version_key, version_name};
use crate::uri::Uri;
use crate::versions::{is_finished_version, list_version_paths, next_version_path};

/// The edits' one department.
pub const DEPARTMENT: &str = "edit";
/// The channel published edits go to.
pub const CHANNEL: &str = "default";
/// The working versions' sidecar folder.
pub const CONTEXT_DIR: &str = "_context";

/// Who saves: TumblePipe's `TH_USER` (never the OS account), else blank.
pub fn user_name() -> String {
    std::env::var("TH_USER").unwrap_or_default()
}

/// The edit `entity:/edits/<name>`.
pub fn edit_uri(name: &str) -> Uri {
    Uri::new("entity", ["edits", name])
}

/// The name of edit `uri`, or why it is not an edit.
pub fn edit_name(uri: &Uri) -> Result<String, String> {
    match uri.segments.as_slice() {
        [kind, name] if uri.purpose == "entity" && kind == "edits" => Ok(name.clone()),
        _ => Err(format!("{uri} is not an edit")),
    }
}

/// Create the edit `name`, refusing a name not of letters and digits, one
/// that exists and a case variant of one that does.
pub fn create_edit(project: &Project, name: &str) -> Result<Uri, String> {
    if !is_valid_entity_name(name) {
        return Err(format!("{name:?}: an edit's name is letters and digits"));
    }
    let uri = edit_uri(name);
    project.config.add_entity(&uri, Map::new()).map_err(|e| e.to_string())?;
    Ok(uri)
}

/// The edits the project has, by name.
pub fn list_edits(project: &Project) -> Result<Vec<String>, String> {
    project.config.child_names(&Uri::new("entity", ["edits"])).map_err(|e| e.to_string())
}

/// Where edit `name`'s working versions are.
pub fn working_dir(project: &Project, name: &str) -> Result<PathBuf, String> {
    project.storage.path("edits", &[name, DEPARTMENT]).map_err(|e| e.to_string())
}

fn working_file(name: &str, version: &str) -> String {
    format!("{name}_{DEPARTMENT}_{version}.json")
}

/// Edit `name`'s working version `version`.
pub fn working_path(project: &Project, name: &str, version: &str) -> Result<PathBuf, String> {
    Ok(working_dir(project, name)?.join(working_file(name, version)))
}

/// Edit `name`'s working versions, oldest first.
pub fn working_versions(project: &Project, name: &str) -> Result<Vec<String>, String> {
    let dir = working_dir(project, name)?;
    let prefix = format!("{name}_{DEPARTMENT}_");
    let Ok(entries) = std::fs::read_dir(&dir) else { return Ok(Vec::new()) };
    let mut versions: Vec<String> = entries
        .filter_map(Result::ok)
        .filter_map(|e| {
            let file = e.file_name().to_string_lossy().into_owned();
            let version = file.strip_prefix(&prefix)?.strip_suffix(".json")?.to_owned();
            is_valid_version_name(&version).then_some(version)
        })
        .collect();
    versions.sort_by_key(|v| version_key(v));
    Ok(versions)
}

/// What a working version's sidecar says.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Saved {
    pub user: String,
    pub timestamp: String,
    pub note: String,
}

/// The sidecar of working version `version` (blank when it has none).
pub fn working_context(project: &Project, name: &str, version: &str) -> Result<Saved, String> {
    let path = working_dir(project, name)?.join(CONTEXT_DIR).join(format!("{version}.json"));
    let Ok(text) = std::fs::read_to_string(&path) else { return Ok(Saved::default()) };
    let value: Value = serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let field = |key: &str| value.get(key).and_then(Value::as_str).unwrap_or_default().to_owned();
    Ok(Saved { user: field("user"), timestamp: field("timestamp"), note: field("note") })
}

/// Read working version `version` of edit `name`.
pub fn load_working(project: &Project, name: &str, version: &str) -> Result<WorkingEdit, String> {
    let path = working_path(project, name, version)?;
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    WorkingEdit::parse(&text).map_err(|e| format!("{}: {e}", path.display()))
}

/// Write `contents` to `path`, which must not exist yet.
fn write_new(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    file.write_all(contents).map_err(|e| format!("{}: {e}", path.display()))
}

/// Save `edit` as edit `name`'s next working version, opened from `from`
/// (None for a new edit), with `note`: the new version.
pub fn save_working(project: &Project, name: &str, edit: &WorkingEdit, from: Option<&str>, note: &str) -> Result<String, String> {
    let dir = working_dir(project, name)?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    // One past the highest, retried if another saver takes it first.
    for _ in 0..16 {
        let next = working_versions(project, name)?.last().map_or(1, |v| version_key(v) + 1);
        let version = version_name(next);
        match write_new(&dir.join(working_file(name, &version)), edit.to_json().as_bytes()) {
            Ok(()) => {
                let context = json!({
                    "user": user_name(),
                    "timestamp": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
                    "from_version": from,
                    "to_version": version,
                    "producer": producer(),
                    "extension": "json",
                    "note": note,
                });
                store_json(&dir.join(CONTEXT_DIR).join(format!("{version}.json")), &context).map_err(|e| e.to_string())?;
                return Ok(version);
            }
            Err(_) if dir.join(working_file(name, &version)).exists() => continue,
            Err(e) => return Err(e),
        }
    }
    Err(format!("{}: could not find a free version to save to", dir.display()))
}

/// Where edit `name`'s autosave is.
pub fn autosave_path(project: &Project, name: &str) -> Result<PathBuf, String> {
    Ok(working_dir(project, name)?.join(format!("{name}_{DEPARTMENT}_autosave.json")))
}

/// Keep `edit` as edit `name`'s autosave.
pub fn autosave(project: &Project, name: &str, edit: &WorkingEdit) -> Result<(), String> {
    let path = autosave_path(project, name)?;
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let partial = path.with_extension("json.partial");
    std::fs::write(&partial, edit.to_json()).map_err(|e| format!("{}: {e}", partial.display()))?;
    crate::io::replace_file(&partial, &path).map_err(|e| format!("{}: {e}", path.display()))
}

/// Edit `name`'s autosave, when there is one newer than its newest working
/// version (a session that ended without saving).
pub fn recoverable(project: &Project, name: &str) -> Result<Option<WorkingEdit>, String> {
    let path = autosave_path(project, name)?;
    let Ok(saved) = std::fs::metadata(&path).and_then(|m| m.modified()) else { return Ok(None) };
    if let Some(latest) = working_versions(project, name)?.last() {
        let newest = std::fs::metadata(working_path(project, name, latest)?).and_then(|m| m.modified()).ok();
        if newest.is_some_and(|n| n >= saved) {
            return Ok(None);
        }
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    WorkingEdit::parse(&text).map(Some).map_err(|e| format!("{}: {e}", path.display()))
}

/// Drop edit `name`'s autosave (after a save, or a recovery declined).
pub fn discard_autosave(project: &Project, name: &str) -> Result<(), String> {
    match std::fs::remove_file(autosave_path(project, name)?) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e.to_string()),
        _ => Ok(()),
    }
}

/// Where edit `name`'s published versions are.
pub fn export_dir(project: &Project, name: &str) -> Result<PathBuf, String> {
    project.storage.path("export", &["edits", name, CHANNEL, DEPARTMENT]).map_err(|e| e.to_string())
}

/// Edit `name`'s finished published versions, oldest first.
pub fn published_versions(project: &Project, name: &str) -> Result<Vec<String>, String> {
    Ok(list_version_paths(&export_dir(project, name)?)
        .into_iter()
        .filter(|p| is_finished_version(p))
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
        .collect())
}

/// Published version `version` of edit `name`: its document's path.
pub fn published_path(project: &Project, name: &str, version: &str) -> Result<PathBuf, String> {
    Ok(export_dir(project, name)?.join(version).join(working_file(name, version)))
}

/// Read published version `version` of edit `name`.
pub fn load_published(project: &Project, name: &str, version: &str) -> Result<FrozenEdit, String> {
    let path = published_path(project, name, version)?;
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    FrozenEdit::parse(&text).map_err(|e| format!("{}: {e}", path.display()))
}

/// `to` as a hard link to `from`, or a copy where a link cannot be made
/// (another drive, a share without links).
fn link_or_copy(from: &Path, to: &Path) -> Result<(), String> {
    if std::fs::hard_link(from, to).is_ok() {
        return Ok(());
    }
    std::fs::copy(from, to).map(|_| ()).map_err(|e| format!("linking {} into the publish: {e}", from.display()))
}

/// The `producer` block the edit's files record.
fn producer() -> Value {
    json!({"app": "th_player", "version": env!("CARGO_PKG_VERSION")})
}

/// Publish `edit` as edit `name`'s next version, with `note`: the document,
/// the movies it plays linked under names of their own in `media/`
/// (`th_edit::otio::movie_links`: Resolve takes same-named movies for one),
/// its OTIO referring to them, and `context.json` last. The new version.
pub fn publish(project: &Project, name: &str, edit: &FrozenEdit, note: &str) -> Result<String, String> {
    let root = export_dir(project, name)?;
    std::fs::create_dir_all(&root).map_err(|e| format!("{}: {e}", root.display()))?;
    let dir = next_version_path(&root);
    std::fs::create_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let version = dir.file_name().unwrap().to_string_lossy().into_owned();
    let stem = format!("{name}_{DEPARTMENT}_{version}");
    write_new(&dir.join(format!("{stem}.json")), edit.to_json().as_bytes())?;
    let mut otio = th_edit::otio::timeline(edit, &format!("{name} {version}"));
    let links = th_edit::otio::movie_links(edit);
    if !links.is_empty() {
        let media = dir.join("media");
        std::fs::create_dir(&media).map_err(|e| format!("{}: {e}", media.display()))?;
        let mut to = std::collections::BTreeMap::new();
        for (path, file) in &links {
            let link = media.join(file);
            link_or_copy(Path::new(path), &link)?;
            to.insert(path.clone(), link.to_string_lossy().into_owned());
        }
        th_edit::otio::relink(&mut otio, &to);
    }
    write_new(&dir.join(format!("{stem}.otio")), serde_json::to_string_pretty(&otio).expect("OTIO serializes").as_bytes())?;
    let context = json!({
        "uri": edit_uri(name).to_string(),
        "department": DEPARTMENT,
        "channel": CHANNEL,
        "version": version,
        "timestamp": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "user": user_name(),
        "producer": producer(),
        "note": note,
    });
    store_json(&dir.join(VERSION_MARKER), &context).map_err(|e| e.to_string())?;
    Ok(version)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::make_project;
    use th_edit::document::{FrozenSource, Source, Track, VideoClip};

    fn working() -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let slug = Source::Slug { text: "title".into() };
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![VideoClip { at: 0, duration: 24, source_in: 0, source: slug, fade_in: 0, fade_out: 0 }],
        });
        edit
    }

    #[test]
    fn saves_are_new_versions_with_their_note() {
        let (_dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        assert_eq!(create_edit(&project, "Main").unwrap().to_string(), "entity:/edits/Main");
        assert!(create_edit(&project, "Main").is_err(), "it exists");
        assert!(create_edit(&project, "main").is_err(), "a case variant");
        assert!(create_edit(&project, "Bad name").is_err());
        let project = Project::open(&root, &root).unwrap();
        assert_eq!(list_edits(&project).unwrap(), ["Main"]);

        assert_eq!(working_versions(&project, "Main").unwrap(), Vec::<String>::new());
        let mut edit = working();
        assert_eq!(save_working(&project, "Main", &edit, None, "first cut").unwrap(), "v0001");
        edit.video[0].clips[0].duration = 48;
        assert_eq!(save_working(&project, "Main", &edit, Some("v0001"), "").unwrap(), "v0002");
        assert_eq!(working_versions(&project, "Main").unwrap(), ["v0001", "v0002"]);
        assert_eq!(load_working(&project, "Main", "v0002").unwrap(), edit);
        assert_eq!(working_context(&project, "Main", "v0001").unwrap().note, "first cut");
        let path = working_path(&project, "Main", "v0001").unwrap();
        assert!(path.ends_with("edits/Main/edit/Main_edit_v0001.json"), "{}", path.display());
    }

    #[test]
    fn an_autosave_newer_than_the_last_save_is_recoverable() {
        let (_dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        let edit = working();
        assert_eq!(recoverable(&project, "Main").unwrap(), None);
        autosave(&project, "Main", &edit).unwrap();
        assert_eq!(recoverable(&project, "Main").unwrap(), Some(edit.clone()));
        std::thread::sleep(std::time::Duration::from_millis(20));
        save_working(&project, "Main", &edit, None, "").unwrap();
        assert_eq!(recoverable(&project, "Main").unwrap(), None, "saved since");
        discard_autosave(&project, "Main").unwrap();
        discard_autosave(&project, "Main").unwrap();
        assert!(!autosave_path(&project, "Main").unwrap().exists());
    }

    #[test]
    fn a_publish_is_finished_by_its_context() {
        let (_dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let slug = FrozenSource::Slug { text: "title".into() };
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![VideoClip { at: 0, duration: 24, source_in: 0, source: slug, fade_in: 0, fade_out: 0 }],
        });
        assert_eq!(publish(&project, "Main", &edit, "for review").unwrap(), "v0001");
        // An unfinished version (no context.json) is skipped, and the next
        // publish goes past it.
        std::fs::create_dir(export_dir(&project, "Main").unwrap().join("v0002")).unwrap();
        assert_eq!(published_versions(&project, "Main").unwrap(), ["v0001"]);
        assert_eq!(publish(&project, "Main", &edit, "").unwrap(), "v0003");
        assert_eq!(load_published(&project, "Main", "v0003").unwrap(), edit);
        let dir = export_dir(&project, "Main").unwrap().join("v0001");
        assert!(dir.join("Main_edit_v0001.otio").is_file());
        let context: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("context.json")).unwrap()).unwrap();
        assert_eq!(context["uri"], "entity:/edits/Main");
        assert_eq!(context["note"], "for review");
        assert!(dir.ends_with("edits/Main/default/edit/v0001"), "{}", dir.display());
        assert!(!dir.join("media").exists(), "no movies, no media folder");
    }

    /// Two shots' playblasts, both `v0001.mp4`: the publish links each
    /// under its own name, and its OTIO refers to the links.
    #[test]
    fn a_publish_links_its_movies_under_names_of_their_own() {
        use th_edit::document::{FrozenTake, TakeKind, Version};
        let (dir, root) = make_project();
        let project = Project::open(&root, &root).unwrap();
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let mut clips = Vec::new();
        for (i, shot) in ["010", "020"].iter().enumerate() {
            let movie = dir.path().join("render").join(shot).join("v0001.mp4");
            std::fs::create_dir_all(movie.parent().unwrap()).unwrap();
            std::fs::write(&movie, format!("movie of {shot}")).unwrap();
            let take = FrozenTake {
                department: "animation".into(),
                kind: TakeKind::Playblast,
                layer: None,
                version: Version::try_from("v0001".to_string()).unwrap(),
                media: th_playlist::Media::Movie { path: movie.to_string_lossy().into_owned() },
            };
            let source = FrozenSource::Shot { shot: format!("entity:/shots/S/{shot}"), take: Some(take) };
            clips.push(VideoClip { at: i as i64 * 10, duration: 10, source_in: 1001, source, fade_in: 0, fade_out: 0 });
        }
        edit.video.push(Track { name: "V1".into(), clips });
        publish(&project, "Main", &edit, "").unwrap();
        let out = export_dir(&project, "Main").unwrap().join("v0001");
        let otio = std::fs::read_to_string(out.join("Main_edit_v0001.otio")).unwrap();
        for shot in ["010", "020"] {
            let link = out.join("media").join(format!("S_{shot}_animation_playblast_v0001.mp4"));
            assert_eq!(std::fs::read_to_string(&link).unwrap(), format!("movie of {shot}"));
            assert!(otio.contains(&th_edit::otio::file_url(&link.to_string_lossy())), "{otio}");
        }
        // The references, not the clips' metadata (which keeps the take as
        // the edit has it, paths included).
        let mut urls = Vec::new();
        fn targets(v: &Value, out: &mut Vec<String>) {
            match v {
                Value::Object(m) => {
                    if let Some(url) = m.get("target_url").and_then(Value::as_str) {
                        out.push(url.to_owned());
                    }
                    m.values().for_each(|c| targets(c, out));
                }
                Value::Array(a) => a.iter().for_each(|c| targets(c, out)),
                _ => {}
            }
        }
        targets(&serde_json::from_str(&otio).unwrap(), &mut urls);
        assert_eq!(urls.len(), 2, "{otio}");
        assert!(urls.iter().all(|u| u.contains("/media/S_")), "no movie referred to by its own name: {urls:?}");
        // The edit document keeps the movies' own paths.
        assert_eq!(load_published(&project, "Main", "v0001").unwrap(), edit);
    }

    /// The path a `file:` URL (`th_edit::otio::file_url`) names.
    fn url_path(url: &str) -> String {
        let rest = url.strip_prefix("file://").unwrap();
        let rest = if rest.as_bytes().get(2) == Some(&b':') { &rest[1..] } else { rest };
        rest.replace("%20", " ").replace("%23", "#").replace("%25", "%")
    }

    /// An edit of shot clips playing `movies` (indices into the files
    /// `make_movies` wrote), each a playblast of shot `S/0<n>0`.
    fn edit_of(clips: &[(usize, usize)], movies: &[PathBuf]) -> FrozenEdit {
        use th_edit::document::{FrozenTake, TakeKind, Version};
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let clips = clips
            .iter()
            .enumerate()
            .map(|(i, &(shot, movie))| {
                let take = FrozenTake {
                    department: "animation".into(),
                    kind: TakeKind::Playblast,
                    layer: None,
                    version: Version::try_from("v0001".to_string()).unwrap(),
                    media: th_playlist::Media::Movie { path: movies[movie].to_string_lossy().into_owned() },
                };
                let source = FrozenSource::Shot { shot: format!("entity:/shots/S/0{shot}0"), take: Some(take) };
                VideoClip { at: i as i64 * 10, duration: 10, source_in: 1001, source, fade_in: 0, fade_out: 0 }
            })
            .collect();
        edit.video.push(Track { name: "V1".into(), clips });
        edit
    }

    /// Movies in folders of their own, every one named `v0001.mp4` or
    /// `v0002.mp4`, each holding its own path (or `" "` in its folder name).
    fn make_movies(root: &Path, count: usize) -> Vec<PathBuf> {
        (0..count)
            .map(|i| {
                let movie = root.join("render").join(format!("shot {i}#")).join(format!("v000{}.mp4", 1 + i % 2));
                std::fs::create_dir_all(movie.parent().unwrap()).unwrap();
                std::fs::write(&movie, movie.to_string_lossy().as_bytes()).unwrap();
                movie
            })
            .collect()
    }

    use proptest::prelude::*;

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(24))]

        /// Whatever shots and movies a cut plays, every movie its OTIO refers
        /// to is a file in the version's `media/` holding that clip's own
        /// movie, and `media/` holds nothing the OTIO does not refer to.
        #[test]
        fn the_otio_opens_each_clips_own_movie(count in 1usize..5, clips in prop::collection::vec((0usize..3, 0usize..5), 1..8)) {
            let (dir, root) = make_project();
            let project = Project::open(&root, &root).unwrap();
            let movies = make_movies(dir.path(), count);
            let clips: Vec<(usize, usize)> = clips.into_iter().map(|(s, m)| (s, m % count)).collect();
            publish(&project, "Main", &edit_of(&clips, &movies), "").unwrap();
            let out = export_dir(&project, "Main").unwrap().join("v0001");
            let otio: Value = serde_json::from_str(&std::fs::read_to_string(out.join("Main_edit_v0001.otio")).unwrap()).unwrap();
            let mut referred = std::collections::BTreeSet::new();
            let items = otio["tracks"]["children"][0]["children"].as_array().unwrap().clone();
            let shown: Vec<&Value> = items.iter().filter(|c| c["OTIO_SCHEMA"] == "Clip.2").collect();
            prop_assert_eq!(shown.len(), clips.len());
            for clip in shown {
                let own = clip["metadata"]["tumblepipe"]["media"]["path"].as_str().unwrap();
                let url = clip["media_references"]["DEFAULT_MEDIA"]["target_url"].as_str().unwrap();
                let link = PathBuf::from(url_path(url));
                let media = out.join("media");
                prop_assert_eq!(link.parent(), Some(media.as_path()), "{} is not in media/", url);
                prop_assert_eq!(std::fs::read(&link).unwrap(), std::fs::read(own).unwrap(), "{} is not {}", url, own);
                referred.insert(link.file_name().unwrap().to_owned());
            }
            let held: std::collections::BTreeSet<_> = std::fs::read_dir(out.join("media")).unwrap().map(|e| e.unwrap().file_name()).collect();
            prop_assert_eq!(held, referred);
        }

        /// A publish whose movie is gone fails, and leaves no finished
        /// version behind; the next publish takes the version after it.
        #[test]
        fn a_publish_that_cannot_link_a_movie_is_not_finished(count in 1usize..4, gone in 0usize..4) {
            let (dir, root) = make_project();
            let project = Project::open(&root, &root).unwrap();
            let movies = make_movies(dir.path(), count);
            let clips: Vec<(usize, usize)> = (0..count).map(|m| (m % 3, m)).collect();
            let edit = edit_of(&clips, &movies);
            std::fs::remove_file(&movies[gone % count]).unwrap();
            prop_assert!(publish(&project, "Main", &edit, "").is_err());
            prop_assert!(published_versions(&project, "Main").unwrap().is_empty());
            std::fs::write(&movies[gone % count], b"back").unwrap();
            prop_assert_eq!(publish(&project, "Main", &edit, "").unwrap(), "v0002");
            prop_assert_eq!(published_versions(&project, "Main").unwrap(), ["v0002"]);
        }
    }
}
