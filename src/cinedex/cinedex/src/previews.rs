//! Preview frames rendered on this machine (#75): what `--frames` opens,
//! and which shots' clips say "Preview ready".
//!
//! The local farm's batches, and the previews their runner records, are
//! read by `th_pipe::previews`, held to Python's
//! `tumblepipe.farm.local.previews` by `tests/test_previews_parity.py`.
//! This is what the editor makes of them.
//!
//! `--frames <path>` opens, by what `path` is:
//! - a local farm batch folder (it holds `batch.json`): its ready previews'
//!   frames, job by job;
//! - any other folder: the EXR and JPG frames in it;
//! - a frame file: that frame.
//!
//! A clip's badge: its shot has a ready preview whose version is not yet a
//! take it can play (a render take is listed only once every frame is
//! there), so the badge goes once the full range has rendered.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use primer::prelude::Command;
use th_edit::document::Source;
use th_pipe::previews::{Preview, batch_previews, is_batch, this_farm_root};
use th_playlist::Take;

/// What a clip whose shot has ready preview frames says.
pub(crate) const BADGE: &str = "Preview ready";

/// Frame files `--frames` shows from a folder.
const FRAME_EXTENSIONS: [&str; 3] = ["exr", "jpg", "jpeg"];

/// One frame `--frames` shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Still {
    /// What it belongs to: the preview's summary, or the folder's name.
    pub(crate) group: String,
    /// Its frame number, when its name has one.
    pub(crate) number: Option<i64>,
    pub(crate) path: PathBuf,
}

/// What `--frames` opens: a title and the frames, in showing order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Stills {
    pub(crate) title: String,
    pub(crate) frames: Vec<Still>,
}

/// The stills of a batch's `previews`, job by job.
pub(crate) fn preview_stills(previews: &[Preview]) -> Vec<Still> {
    previews
        .iter()
        .flat_map(|p| {
            let group = p.summary();
            p.frame_paths().into_iter().map(move |(number, path)| Still { group: group.clone(), number: Some(number), path })
        })
        .collect()
}

/// The frame number at the end of a file stem (`beauty.1001` is 1001).
pub(crate) fn trailing_number(stem: &str) -> Option<i64> {
    let digits = stem.len() - stem.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    (digits > 0).then(|| stem[stem.len() - digits..].parse().ok()).flatten()
}

fn is_frame_file(name: &str) -> bool {
    name.rsplit_once('.').is_some_and(|(_, ext)| FRAME_EXTENSIONS.iter().any(|e| e.eq_ignore_ascii_case(ext)))
}

/// The frames among `names` (files of one folder), by sequence then frame
/// number: each name's part before its number, then the number.
pub(crate) fn folder_frames(names: &[String]) -> Vec<(Option<i64>, String)> {
    let mut frames: Vec<(String, Option<i64>, String)> = names
        .iter()
        .filter(|name| is_frame_file(name))
        .map(|name| {
            let stem = name.rsplit_once('.').map_or(name.as_str(), |(stem, _)| stem);
            let number = trailing_number(stem);
            let prefix = stem.trim_end_matches(|c: char| c.is_ascii_digit()).to_owned();
            (prefix, number, name.clone())
        })
        .collect();
    frames.sort();
    frames.into_iter().map(|(_, number, name)| (number, name)).collect()
}

fn file_name(path: &Path) -> String {
    path.file_name().map_or_else(|| path.display().to_string(), |n| n.to_string_lossy().into_owned())
}

/// What `--frames path` shows; an error saying why there is nothing.
pub(crate) fn open_frames(path: &Path) -> Result<Stills, String> {
    if path.is_dir() && is_batch(path) {
        let previews = batch_previews(path)?;
        let frames = preview_stills(&previews);
        if frames.is_empty() {
            return Err(format!("no preview frames of the batch in {} are ready yet", path.display()));
        }
        let title = match previews.as_slice() {
            [one] => one.summary(),
            _ => format!("{} previews of {}", previews.len(), file_name(path)),
        };
        return Ok(Stills { title, frames });
    }
    if path.is_dir() {
        let names: Vec<String> = std::fs::read_dir(path)
            .map_err(|e| format!("{}: {e}", path.display()))?
            .filter_map(Result::ok)
            .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        let group = file_name(path);
        let frames: Vec<Still> =
            folder_frames(&names).into_iter().map(|(number, name)| Still { group: group.clone(), number, path: path.join(name) }).collect();
        if frames.is_empty() {
            return Err(format!("no EXR or JPG frames in {}", path.display()));
        }
        return Ok(Stills { title: group, frames });
    }
    if path.is_file() && is_frame_file(&file_name(path)) {
        let name = file_name(path);
        let number = trailing_number(name.rsplit_once('.').map_or(name.as_str(), |(stem, _)| stem));
        return Ok(Stills { title: name.clone(), frames: vec![Still { group: name, number, path: path.to_path_buf() }] });
    }
    Err(format!("{} is not a batch folder, a folder of frames or an EXR or JPG frame", path.display()))
}

/// A shot URI as the edit names it, so a preview's and a clip's compare.
fn shot_key(uri: &str) -> String {
    th_pipe::Uri::parse_address(uri).map_or_else(|_| uri.trim_end_matches('/').to_owned(), |u| u.to_string())
}

/// The ready previews on this machine, by shot.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Ready {
    by_shot: BTreeMap<String, Vec<Preview>>,
}

impl Ready {
    /// `previews` sorted by shot; those naming no entity are left out.
    pub(crate) fn of(previews: impl IntoIterator<Item = Preview>) -> Self {
        let mut by_shot: BTreeMap<String, Vec<Preview>> = BTreeMap::new();
        for preview in previews.into_iter().filter(|p| !p.entity.is_empty()) {
            by_shot.entry(shot_key(&preview.entity)).or_default().push(preview);
        }
        Ready { by_shot }
    }

    /// Every batch's ready previews under the local farm's folder `root`;
    /// a batch that cannot be read is passed over.
    pub(crate) fn scan(root: &Path) -> Self {
        let Ok(batches) = std::fs::read_dir(root.join("batches")) else { return Ready::default() };
        Ready::of(
            batches.filter_map(Result::ok).map(|e| e.path()).filter(|p| is_batch(p)).flat_map(|p| batch_previews(&p).unwrap_or_default()),
        )
    }

    /// What the clip of `shot` says when its preview frames are ready: the
    /// badge, while one of its previews is of a version not among the
    /// `takes` it can play (once the full range is rendered, it is).
    pub(crate) fn badge(&self, shot: &str, takes: &[Take]) -> Option<&'static str> {
        let previews = self.by_shot.get(&shot_key(shot))?;
        previews.iter().any(|p| !takes.iter().any(|t| t.department == p.department && t.version == p.version)).then_some(BADGE)
    }

    /// [`Self::badge`] of a clip of `source` whose takes are `takes`: only
    /// a shot's clip, and only once its takes are known (None while it
    /// resolves), so a badge never flashes up before them.
    pub(crate) fn clip_badge(&self, source: &Source, takes: Option<&[Take]>) -> Option<&'static str> {
        let Source::Shot { shot, .. } = source else { return None };
        self.badge(shot, takes?)
    }
}

/// Read this machine's ready previews off the UI thread, then `done` with them.
pub(crate) fn poll<M: Send + 'static>(done: impl Fn(Ready) -> M + Send + 'static) -> Command<M> {
    Command::perform_blocking(|| Ready::scan(&this_farm_root()), done)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::{Value, json};
    use th_pipe::previews::{BATCH_NAME, STATE_KEY, STATE_NAME};

    fn preview(job: i64, entity: &str, department: &str, version: &str) -> Preview {
        Preview {
            job,
            entity: entity.into(),
            department: department.into(),
            version: version.into(),
            frame_path: "P:/r/beauty.####.exr".into(),
            frames: vec![1001, 1050, 1100],
        }
    }

    fn take(department: &str, version: &str) -> Take {
        Take {
            label: format!("{department} {version}"),
            department: department.into(),
            version: version.into(),
            media: th_playlist::Media::Movie { path: "P:/m.mp4".into() },
            multi_range: false,
        }
    }

    fn entry_json(p: &Preview) -> Value {
        json!({"ready": "now", "entity": p.entity, "department": p.department, "version": p.version,
               "frame_path": p.frame_path, "frames": p.frames, "notified": true, "error": ""})
    }

    fn word() -> impl Strategy<Value = String> {
        "[A-Za-z][A-Za-z0-9]{0,7}"
    }

    #[test]
    fn a_batch_folder_opens_its_ready_previews() {
        let dir = tempfile::tempdir().unwrap();
        let batch = dir.path().join("b1");
        std::fs::create_dir_all(&batch).unwrap();
        assert!(open_frames(&batch).unwrap_err().contains("no EXR or JPG"), "no batch.json: a plain folder");
        std::fs::write(batch.join(BATCH_NAME), br#"{"version": 1, "jobs": []}"#).unwrap();
        assert!(open_frames(&batch).unwrap_err().contains("ready yet"), "no state.json yet");
        let a = preview(4, "entity:/shots/010/0100", "denoise", "v0003");
        let b = preview(9, "entity:/shots/010/0100", "composite", "v0001");
        let state = json!({"jobs": {}, STATE_KEY: {"9": entry_json(&b), "4": entry_json(&a)}});
        std::fs::write(batch.join(STATE_NAME), serde_json::to_vec(&state).unwrap()).unwrap();
        let stills = open_frames(&batch).unwrap();
        assert_eq!(stills.frames.len(), 6);
        assert_eq!(stills.frames[0].group, a.summary(), "job order");
        assert_eq!(stills.frames[3].group, b.summary());
        assert_eq!(stills.frames[1].path, PathBuf::from("P:/r/beauty.1050.exr"));
        assert_eq!(stills.title, "2 previews of b1");
        std::fs::write(batch.join(BATCH_NAME), br#"{"version": 2, "jobs": []}"#).unwrap();
        assert!(open_frames(&batch).is_err(), "a batch layout it does not read");
    }

    #[test]
    fn a_folder_opens_its_frames_and_a_file_itself() {
        let dir = tempfile::tempdir().unwrap();
        for name in ["beauty.1100.exr", "beauty.1001.exr", "notes.txt", "beauty.1050.EXR"] {
            std::fs::write(dir.path().join(name), b"x").unwrap();
        }
        let stills = open_frames(dir.path()).unwrap();
        assert_eq!(stills.frames.iter().map(|s| s.number).collect::<Vec<_>>(), [Some(1001), Some(1050), Some(1100)]);
        let one = open_frames(&dir.path().join("beauty.1050.EXR")).unwrap();
        assert_eq!(one.frames[0].number, Some(1050));
        assert!(open_frames(&dir.path().join("notes.txt")).is_err());
        assert!(open_frames(&dir.path().join("gone")).is_err());
    }

    proptest! {
        /// A folder's frames come in sequence then frame order, and only
        /// frame files.
        #[test]
        fn folder_frames_are_in_frame_order(numbers in prop::collection::btree_set(0i64..9999, 0..12), other in word()) {
            let mut names: Vec<String> = numbers.iter().rev().map(|n| format!("beauty.{n:04}.exr")).collect();
            names.push(format!("{other}.txt"));
            let frames = folder_frames(&names);
            prop_assert_eq!(frames.iter().map(|(n, _)| n.unwrap()).collect::<Vec<_>>(), numbers.into_iter().collect::<Vec<_>>());
        }

        /// The badge shows exactly while a preview of the shot is of a
        /// version it has no take of, and never on another shot's clip.
        #[test]
        fn the_badge_shows_until_the_version_is_a_take(
            shot in word(), other in word(), department in word(), version in 1u32..20, takes in prop::collection::vec((word(), 1u32..20), 0..5),
        ) {
            let uri = format!("entity:/shots/A/{shot}");
            let v = format!("v{version:04}");
            let ready = Ready::of([preview(1, &uri, &department, &v)]);
            let takes: Vec<Take> = takes.iter().map(|(d, n)| take(d, &format!("v{n:04}"))).collect();
            let covered = takes.iter().any(|t| t.department == department && t.version == v);
            prop_assert_eq!(ready.badge(&uri, &takes), (!covered).then_some(BADGE));
            prop_assume!(other != shot);
            prop_assert_eq!(ready.badge(&format!("entity:/shots/A/{other}"), &[]), None);
            let mut done = takes.clone();
            done.push(take(&department, &v));
            prop_assert_eq!(ready.badge(&uri, &done), None, "the full range rendered");
        }
    }

    /// Only a shot's clip wears the badge, and only once its takes are known.
    #[test]
    fn only_a_resolved_shot_clip_has_a_badge() {
        use th_edit::document::{Rule, VersionRule};
        let uri = "entity:/shots/A/010";
        let ready = Ready::of([preview(1, uri, "lighting", "v0002"), preview(2, "", "lighting", "v0002")]);
        let shot = Source::Shot { shot: uri.into(), take: Rule::best() };
        assert_eq!(ready.clip_badge(&shot, Some(&[])), Some(BADGE));
        assert_eq!(ready.clip_badge(&shot, None), None, "still resolving");
        assert_eq!(ready.clip_badge(&Source::Slug { text: uri.into() }, Some(&[])), None);
        assert_eq!(ready.clip_badge(&Source::Media { item: uri.into(), version: VersionRule::Latest }, Some(&[])), None);
        assert_eq!(Ready::of([preview(2, "", "lighting", "v0002")]), Ready::default(), "a preview naming no shot");
    }

    #[test]
    fn a_scan_finds_every_batch_s_previews() {
        let dir = tempfile::tempdir().unwrap();
        for (id, shot) in [("b1", "010"), ("b2", "020")] {
            let batch = dir.path().join("batches").join(id);
            std::fs::create_dir_all(&batch).unwrap();
            std::fs::write(batch.join(BATCH_NAME), br#"{"version": 1, "jobs": []}"#).unwrap();
            let p = preview(2, &format!("entity:/shots/A/{shot}"), "lighting", "v0002");
            std::fs::write(batch.join(STATE_NAME), serde_json::to_vec(&json!({STATE_KEY: {"2": entry_json(&p)}})).unwrap()).unwrap();
        }
        std::fs::create_dir_all(dir.path().join("batches").join("broken")).unwrap();
        std::fs::write(dir.path().join("batches").join("broken").join(BATCH_NAME), b"{").unwrap();
        let ready = Ready::scan(dir.path());
        assert_eq!(ready.badge("entity:/shots/A/010", &[]), Some(BADGE));
        assert_eq!(ready.badge("entity:/shots/A/020/", &[]), Some(BADGE), "the same shot however it is written");
        assert_eq!(ready.badge("entity:/shots/A/030", &[]), None);
        assert_eq!(Ready::scan(&dir.path().join("none")), Ready::default());
    }
}
