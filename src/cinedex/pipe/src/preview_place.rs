//! Where the frames a preview window shows sit in the pipeline's folders,
//! read from their path alone: Cinedex's `--frames` is handed a folder (the
//! Farm Manager's *Preview in Cinedex*), not an entity, and needs no project.
//!
//! A render's frames are in an AOV folder of a version folder,
//! `<department>/<layer>/vNNNN/<aov>/` (a slapcomp's and a merged comp's in
//! the version folder itself; see `discovery`), so the AOVs beside the one
//! shown are the version folder's other folders ([`aovs`]). A playblast
//! still rendering has its frames beside the movie it will write,
//! `<stem>_vNNNN.mp4.frames` (`tumblepipe.farm.frames.playblast_frames_dir`).
//! Anything else (a batch folder, a folder of frames somewhere) has no
//! place: [`place`] is None, and the window offers nothing beside it.

use std::path::{Path, PathBuf};

use crate::discovery::{BEAUTY, RESERVED_SUFFIX, check_step, file_name, list_dir, parallel_map, same_name, split_suffix};
use crate::naming::{is_valid_version_name, version_key};
use crate::versions::list_version_paths;

/// The file kinds Cinedex shows as frames
/// (`farm.manager.frames_preview.FRAME_EXTENSIONS`).
pub const FRAME_EXTENSIONS: [&str; 3] = ["exr", "jpg", "jpeg"];
/// What a playblast's frames folder adds to its movie's name while it
/// renders (`farm.frames.PLAYBLAST_FRAMES_SUFFIX`).
pub const PLAYBLAST_FRAMES_SUFFIX: &str = ".frames";
/// A playblast's movie.
const MOVIE_SUFFIX: &str = ".mp4";

/// Whether Cinedex shows the file `name` as a frame.
pub fn is_frame_file(name: &str) -> bool {
    name.rsplit_once('.').is_some_and(|(_, ext)| FRAME_EXTENSIONS.iter().any(|e| e.eq_ignore_ascii_case(ext)))
}

/// Where a preview's frames sit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Place {
    /// A render's (or comp's): `stream/version/aov`, or `stream/version`
    /// for frames written into the version folder itself.
    Render { stream: PathBuf, version: String, aov: Option<String> },
    /// A playblast's: `directory/<stem>_<version>.mp4`, its frames beside
    /// it while it renders. `stem` is empty for a bare `<version>.mp4`.
    Playblast { directory: PathBuf, stem: String, version: String },
}

/// The stem and version of a playblast's movie, or of its frames folder,
/// called `name`.
fn playblast_name(name: &str) -> Option<(String, String)> {
    let cut = |name: &str, suffix: &str| {
        let at = name.len().checked_sub(suffix.len())?;
        (name.is_char_boundary(at) && same_name(&name[at..], suffix)).then(|| name[..at].to_owned())
    };
    let movie = cut(name, PLAYBLAST_FRAMES_SUFFIX).unwrap_or_else(|| name.to_owned());
    let stem = cut(&movie, MOVIE_SUFFIX)?;
    if is_valid_version_name(&stem) {
        return Some((String::new(), stem));
    }
    let (prefix, version) = stem.rsplit_once('_')?;
    (!prefix.is_empty() && is_valid_version_name(version)).then(|| (prefix.to_owned(), version.to_owned()))
}

/// The place of the frames at `path` (a folder of frames, or a playblast's
/// movie), by its name and its folders' names alone; None when it is not
/// laid out as a version of something.
pub fn place(path: &Path) -> Option<Place> {
    let name = path.file_name()?.to_str()?;
    let parent = path.parent()?;
    if let Some((stem, version)) = playblast_name(name) {
        return Some(Place::Playblast { directory: parent.to_path_buf(), stem, version });
    }
    if is_valid_version_name(name) {
        return Some(Place::Render { stream: parent.to_path_buf(), version: name.to_owned(), aov: None });
    }
    let version = parent.file_name()?.to_str()?;
    // An AOV folder's name has no suffix (`discovery::get_render`).
    if !is_valid_version_name(version) || !split_suffix(name).1.is_empty() {
        return None;
    }
    Some(Place::Render { stream: parent.parent()?.to_path_buf(), version: version.to_owned(), aov: Some(name.to_owned()) })
}

impl Place {
    /// Its version's name (`v0003`).
    pub fn version(&self) -> &str {
        match self {
            Self::Render { version, .. } | Self::Playblast { version, .. } => version,
        }
    }

    /// The AOV its frames are of, for a render in an AOV folder.
    pub fn aov(&self) -> Option<&str> {
        match self {
            Self::Render { aov, .. } => aov.as_deref(),
            Self::Playblast { .. } => None,
        }
    }
}

/// AOV names in the order a chooser lists them: the beauty first, then by
/// name (case ignored, then by case so the order is total).
pub fn aov_order(names: &mut [String]) {
    names.sort_by(|a, b| (a != BEAUTY, a.to_lowercase(), a).cmp(&(b != BEAUTY, b.to_lowercase(), b)));
}

/// The AOVs of the version `place` is in, the one shown among them, each
/// with its frames folder, in [`aov_order`]: the version folder's folders
/// (those named as AOVs are, without a suffix). Empty for frames that are
/// in no AOV folder (a slapcomp, a playblast). One listing of the version
/// folder; whether each holds frames yet is not looked up.
pub fn aovs(place: &Place) -> Vec<(String, PathBuf)> {
    let Place::Render { stream, version, aov: Some(_) } = place else { return Vec::new() };
    let folder = stream.join(version);
    let mut names: Vec<String> =
        list_dir(&folder).iter().filter(|p| p.is_dir()).map(|p| file_name(p)).filter(|n| split_suffix(n).1.is_empty()).collect();
    aov_order(&mut names);
    names.into_iter().map(|name| (name.clone(), folder.join(name))).collect()
}

/// How much of a version is there to show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VersionState {
    /// Every frame it should have (or a playblast's movie).
    Complete,
    /// `frames` on disk, of `of` when its `context.json` records a range;
    /// not finished (still rendering, or stopped part-way).
    Partial { frames: usize, of: Option<usize> },
    /// Nothing to show: no frames of this AOV, or a playblast only reserved.
    Missing,
}

/// One version of what a preview shows, as its dropdown lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VersionRow {
    pub version: String,
    /// What shows it: its frames folder, or a playblast's movie.
    pub path: PathBuf,
    pub state: VersionState,
    /// A check render's (its `context.json` says `published: false`).
    pub check: bool,
}

/// The state of a render version with `frames` frame files, whose
/// `context.json` is there or not (`finished`) and records `expected`
/// frames when it records a range: none on disk is missing; all of the
/// range, or any of a finished version that records none, is complete;
/// else partial.
pub fn render_state(frames: usize, finished: bool, expected: Option<usize>) -> VersionState {
    match (frames, expected) {
        (0, _) => VersionState::Missing,
        (n, Some(of)) if n >= of => VersionState::Complete,
        (_, None) if finished => VersionState::Complete,
        (n, of) => VersionState::Partial { frames: n, of },
    }
}

impl VersionRow {
    /// The row's words: the version, then what is missing of it, then
    /// whether it is a check render (as the take menu's Version rows say).
    pub fn label(&self) -> String {
        let mut label = self.version.clone();
        match self.state {
            VersionState::Complete => {}
            VersionState::Partial { frames, of: Some(of) } => label.push_str(&format!(" \u{b7} {frames} of {of} frames")),
            VersionState::Partial { frames, of: None } => {
                label.push_str(&format!(" \u{b7} {frames} frame{}, not finished", if frames == 1 { "" } else { "s" }))
            }
            VersionState::Missing => label.push_str(" \u{b7} no frames"),
        }
        if self.check {
            label.push_str(" \u{b7} check");
        }
        label
    }
}

fn frame_count(folder: &Path) -> usize {
    list_dir(folder).iter().filter(|p| is_frame_file(&file_name(p))).count()
}

fn render_row(stream: &Path, version: &str, aov: Option<&str>) -> VersionRow {
    let folder = stream.join(version);
    let path = aov.map_or_else(|| folder.clone(), |aov| folder.join(aov));
    let context = crate::io::load_json(&folder.join(crate::io::VERSION_MARKER)).ok();
    let number = |key: &str| context.as_ref().and_then(|c| c.get(key)).and_then(serde_json::Value::as_i64);
    let expected = match (number("first_frame"), number("last_frame"), number("step_size")) {
        (Some(first), Some(last), Some(step)) => crate::timeline::BlockRange::new(first, last, step).ok().map(|r| r.len()),
        _ => None,
    };
    let check = context.as_ref().is_some_and(|c| check_step(c).is_some());
    VersionRow { version: version.to_owned(), state: render_state(frame_count(&path), context.is_some(), expected), path, check }
}

/// The playblast versions among `names` (the files and folders of one
/// playblast folder) of the stream `stem`, by version: each one's entry
/// name and state. A movie is complete; frames beside a movie still to
/// come are partial (`frames_in` counts a frames folder's frames); a
/// reservation alone is missing. A movie wins over frames, either over a
/// reservation.
pub fn playblast_rows(names: &[String], stem: &str, frames_in: impl Fn(&str) -> usize) -> Vec<(String, String, VersionState)> {
    let rank = |state: &VersionState| match state {
        VersionState::Complete => 2,
        VersionState::Partial { .. } => 1,
        VersionState::Missing => 0,
    };
    let mut found: Vec<(String, String, VersionState)> = Vec::new();
    for name in names {
        let (movie, state) = if let Some(movie) = name.strip_suffix(RESERVED_SUFFIX) {
            (movie, VersionState::Missing)
        } else if let Some(movie) = name.strip_suffix(PLAYBLAST_FRAMES_SUFFIX) {
            (movie, render_state(frames_in(name), false, None))
        } else {
            (name.as_str(), VersionState::Complete)
        };
        let Some((its_stem, version)) = playblast_name(movie) else { continue };
        if !same_name(&its_stem, stem) {
            continue;
        }
        match found.iter_mut().find(|(v, _, _)| *v == version) {
            Some(row) if rank(&state) > rank(&row.2) => *row = (version, name.clone(), state),
            Some(_) => {}
            None => found.push((version, name.clone(), state)),
        }
    }
    found.sort_by_key(|(version, _, _)| std::cmp::Reverse(version_key(version)));
    found
}

/// The versions of what `place` is a version of, newest first: a render's
/// are the version folders beside its own, each as the same AOV's frames; a
/// playblast's the movies (and frames still rendering) of its stream. Each
/// version's folder is listed once, [`CHECKS`] at a time.
pub fn versions(place: &Place) -> Vec<VersionRow> {
    match place {
        Place::Render { stream, aov, .. } => {
            let mut names: Vec<String> = list_version_paths(stream).iter().map(|p| file_name(p)).collect();
            names.reverse();
            parallel_map(&names, |version| render_row(stream, version, aov.as_deref()))
        }
        Place::Playblast { directory, stem, .. } => {
            let names: Vec<String> = list_dir(directory).iter().map(|p| file_name(p)).collect();
            playblast_rows(&names, stem, |folder| frame_count(&directory.join(folder)))
                .into_iter()
                .map(|(version, name, state)| VersionRow { version, path: directory.join(name), state, check: false })
                .collect()
        }
    }
}

/// The row a version key steps to from the version `current`, `delta`
/// steps newer (the editor's Page Up is +1) among `rows` (newest first),
/// passing over versions with nothing to show. None at either end, or
/// when `current` is not listed.
pub fn step_version(rows: &[VersionRow], current: &str, delta: i8) -> Option<usize> {
    let at = rows.iter().position(|r| r.version == current)?;
    let shows = |i: &usize| rows[*i].state != VersionState::Missing;
    match delta {
        d if d > 0 => (0..at).rev().find(shows),
        d if d < 0 => (at + 1..rows.len()).find(shows),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn render_versions_list_newest_first_and_say_what_is_there() {
        let dir = tempfile::tempdir().unwrap();
        let stream = dir.path().join("default");
        let frames = |version: &str, aov: &str, count: usize| {
            let folder = stream.join(version).join(aov);
            std::fs::create_dir_all(&folder).unwrap();
            for n in 0..count {
                std::fs::write(folder.join(format!("s_{aov}_{version}.{:04}.exr", 1001 + n)), b"x").unwrap();
            }
        };
        let context = |version: &str, text: &str| std::fs::write(stream.join(version).join("context.json"), text).unwrap();
        frames("v0001", "beauty", 3);
        context("v0001", r#"{"first_frame": 1001, "last_frame": 1003, "step_size": 1}"#);
        frames("v0002", "depth", 3);
        context("v0002", r#"{"first_frame": 1001, "last_frame": 1003, "step_size": 1}"#);
        frames("v0003", "beauty", 2);
        context("v0003", r#"{"first_frame": 1001, "last_frame": 1009, "step_size": 2, "published": false}"#);
        frames("v0010", "beauty", 1);
        let rows = versions(&place(&stream.join("v0003").join("beauty")).unwrap());
        let labels: Vec<String> = rows.iter().map(VersionRow::label).collect();
        assert_eq!(
            labels,
            ["v0010 \u{b7} 1 frame, not finished", "v0003 \u{b7} 2 of 5 frames \u{b7} check", "v0002 \u{b7} no frames", "v0001"]
        );
        assert_eq!(rows[3].path, stream.join("v0001").join("beauty"));
        assert_eq!(step_version(&rows, "v0003", -1), Some(3), "past the version with no beauty");
        assert_eq!(step_version(&rows, "v0003", 1), Some(0));
        assert_eq!(step_version(&rows, "v0010", 1), None);
        assert_eq!(step_version(&rows, "v0099", 1), None);
        // Frames in the version folder itself (a slapcomp).
        std::fs::write(stream.join("v0002").join("s.1001.exr"), b"x").unwrap();
        let rows = versions(&place(&stream.join("v0002")).unwrap());
        assert_eq!(rows[2].label(), "v0002 \u{b7} 1 of 3 frames");
        assert_eq!(rows[0].state, VersionState::Missing, "v0010 has frames only in its AOV folder");
    }

    #[test]
    fn playblast_versions_are_movies_frames_and_reservations() {
        let dir = tempfile::tempdir().unwrap();
        for name in ["010_anim_v0001.mp4", "v0002.mp4", "010_anim_v0003.mp4.reserved", "010_layout_v0009.mp4", "notes.txt"] {
            std::fs::write(dir.path().join(name), b"x").unwrap();
        }
        let live = dir.path().join("010_anim_v0004.mp4.frames");
        std::fs::create_dir_all(&live).unwrap();
        std::fs::write(live.join("frames.json"), b"{}").unwrap();
        std::fs::write(live.join("p.1001.jpg"), b"x").unwrap();
        let rows = versions(&place(&live).unwrap());
        let labels: Vec<String> = rows.iter().map(VersionRow::label).collect();
        assert_eq!(labels, ["v0004 \u{b7} 1 frame, not finished", "v0003 \u{b7} no frames", "v0001"]);
        assert_eq!(rows[0].path, live);
        assert_eq!(rows[2].path, dir.path().join("010_anim_v0001.mp4"));
        // Once its movie is written, the movie is the version.
        std::fs::write(dir.path().join("010_anim_v0004.mp4"), b"x").unwrap();
        let rows = versions(&place(&live).unwrap());
        assert_eq!((rows[0].label().as_str(), &rows[0].path), ("v0004", &dir.path().join("010_anim_v0004.mp4")));
    }

    proptest! {
        /// A version's state says what is there: missing exactly without
        /// frames, complete exactly with all of a recorded range (or any,
        /// finished, without one), and its label names the version first
        /// and says anything short of complete.
        #[test]
        fn a_state_says_what_is_there(frames in 0usize..40, finished in any::<bool>(), expected in prop::option::of(1usize..40), check in any::<bool>(), v in version()) {
            let state = render_state(frames, finished, expected);
            prop_assert_eq!(state == VersionState::Missing, frames == 0);
            let whole = frames > 0 && expected.map_or(finished, |of| frames >= of);
            prop_assert_eq!(state == VersionState::Complete, whole);
            let row = VersionRow { version: v.clone(), path: PathBuf::new(), state, check };
            let label = row.label();
            prop_assert!(label.starts_with(&v));
            prop_assert_eq!(label.ends_with("check"), check);
            prop_assert_eq!(label == v, whole && !check);
        }

        /// Playblast rows are newest first, one per version of the stream,
        /// a movie over frames over a reservation, whatever the listing
        /// order; another stream's are left out.
        #[test]
        fn playblast_rows_are_one_per_version(
            found in prop::collection::btree_map(1u32..30, (any::<bool>(), any::<bool>(), any::<bool>(), 0usize..3), 0..8),
            stem in word(), other in word(), flip in any::<bool>(),
        ) {
            prop_assume!(!stem.eq_ignore_ascii_case(&other));
            let mut names = Vec::new();
            let mut wanted = Vec::new();
            for (n, (movie, frames, reserved, count)) in &found {
                let base = format!("{stem}_v{n:04}.mp4");
                if *movie { names.push(base.clone()); }
                if *frames { names.push(format!("{base}{PLAYBLAST_FRAMES_SUFFIX}")); }
                if *reserved { names.push(format!("{base}{RESERVED_SUFFIX}")); }
                names.push(format!("{other}_v{n:04}.mp4"));
                let state = match (movie, frames, reserved) {
                    (true, _, _) => VersionState::Complete,
                    (false, true, _) if *count > 0 => VersionState::Partial { frames: *count, of: None },
                    (false, true, _) | (false, false, true) => VersionState::Missing,
                    (false, false, false) => continue,
                };
                wanted.push((format!("v{n:04}"), state));
            }
            wanted.reverse();
            if flip { names.reverse(); }
            let counts: std::collections::HashMap<String, usize> =
                found.iter().map(|(n, f)| (format!("{stem}_v{n:04}.mp4{PLAYBLAST_FRAMES_SUFFIX}"), f.3)).collect();
            let rows = playblast_rows(&names, &stem, |folder| counts[folder]);
            prop_assert_eq!(rows.iter().map(|(v, _, s)| (v.clone(), *s)).collect::<Vec<_>>(), wanted);
        }

        /// A step lands on the nearest version in its direction that has
        /// something to show, and nowhere else.
        #[test]
        fn a_step_goes_to_the_nearest_version_that_shows(shows in prop::collection::vec(any::<bool>(), 1..10), at in any::<prop::sample::Index>(), up in any::<bool>()) {
            let rows: Vec<VersionRow> = shows.iter().enumerate().map(|(i, s)| VersionRow {
                version: format!("v{:04}", shows.len() - i),
                path: PathBuf::new(),
                state: if *s { VersionState::Complete } else { VersionState::Missing },
                check: false,
            }).collect();
            let at = at.index(rows.len());
            let to = step_version(&rows, &rows[at].version, if up { 1 } else { -1 });
            let between: Vec<usize> = if up { (0..at).rev().collect() } else { (at + 1..rows.len()).collect() };
            prop_assert_eq!(to, between.iter().copied().find(|&i| shows[i]));
            prop_assert_eq!(step_version(&rows, &rows[at].version, 0), None);
        }
    }

    fn word() -> impl Strategy<Value = String> {
        "[A-Za-z][A-Za-z0-9]{0,7}"
    }

    fn version() -> impl Strategy<Value = String> {
        (1u32..12000).prop_map(|n| format!("v{n:04}"))
    }

    #[test]
    fn places_read_from_the_folders_names() {
        let render = |stream: &str, version: &str, aov: Option<&str>| {
            Some(Place::Render { stream: PathBuf::from(stream), version: version.into(), aov: aov.map(str::to_owned) })
        };
        assert_eq!(place(Path::new("P:/r/010/light/default/v0003/beauty")), render("P:/r/010/light/default", "v0003", Some("beauty")));
        assert_eq!(place(Path::new("P:/r/010/light/slapcomp/v0003")), render("P:/r/010/light/slapcomp", "v0003", None));
        assert_eq!(place(Path::new("P:/r/010/light/default/v0003/beauty/")), render("P:/r/010/light/default", "v0003", Some("beauty")));
        let playblast = |stem: &str, version: &str| {
            Some(Place::Playblast { directory: PathBuf::from("P:/pb/010/anim"), stem: stem.into(), version: version.into() })
        };
        assert_eq!(place(Path::new("P:/pb/010/anim/010_anim_v0007.mp4.frames")), playblast("010_anim", "v0007"));
        assert_eq!(place(Path::new("P:/pb/010/anim/010_anim_v0007.mp4")), playblast("010_anim", "v0007"));
        assert_eq!(place(Path::new("P:/pb/010/anim/v0007.mp4")), playblast("", "v0007"));
        for none in [
            "P:/farm/batches/b1",
            "P:/r/v0003/notes.txt",
            "P:/r/v003/beauty",
            "P:/pb/_v0007.mp4",
            "P:/pb/010_anim_v0007.mov",
            "P:/pb/010_anim_final.mp4",
            "beauty",
            "",
        ] {
            assert_eq!(place(Path::new(none)), None, "{none}");
        }
    }

    #[test]
    fn a_version_s_folders_are_its_aovs_beauty_first() {
        let dir = tempfile::tempdir().unwrap();
        let version = dir.path().join("default").join("v0002");
        for aov in ["depth", "beauty", "CryptoObject00", "albedo"] {
            std::fs::create_dir_all(version.join(aov)).unwrap();
        }
        std::fs::create_dir_all(version.join("logs.d")).unwrap();
        std::fs::write(version.join("context.json"), "{}").unwrap();
        let shown = place(&version.join("depth")).unwrap();
        assert_eq!((shown.version(), shown.aov()), ("v0002", Some("depth")));
        let found = aovs(&shown);
        assert_eq!(found.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["beauty", "albedo", "CryptoObject00", "depth"]);
        assert!(found.iter().all(|(name, path)| *path == version.join(name)));
        // Frames in the version folder itself, and a playblast's, have none.
        assert_eq!(aovs(&place(&version).unwrap()), []);
        assert_eq!(aovs(&place(&dir.path().join("a_v0001.mp4.frames")).unwrap()), []);
        assert_eq!(aovs(&place(&dir.path().join("gone").join("v0001").join("beauty")).unwrap()), [], "a version that is not there");
    }

    proptest! {
        /// A render's AOV folder, its version folder and a playblast's
        /// movie and frames folder each read back as what they were built
        /// from, and an AOV's own folder is always among its version's.
        #[test]
        fn a_place_reads_back(stream in word(), layer in word(), v in version(), aov in word(), stem in word()) {
            // An AOV named as a version is would read as one.
            prop_assume!(!is_valid_version_name(&aov));
            let root = Path::new("P:/render").join(&stream).join(&layer);
            let aov_place = place(&root.join(&v).join(&aov));
            prop_assert_eq!(aov_place, Some(Place::Render { stream: root.clone(), version: v.clone(), aov: Some(aov.clone()) }));
            prop_assert_eq!(place(&root.join(&v)), Some(Place::Render { stream: root.clone(), version: v.clone(), aov: None }));
            for suffix in ["", PLAYBLAST_FRAMES_SUFFIX] {
                let found = place(&root.join(format!("{stem}_{v}.mp4{suffix}")));
                prop_assert_eq!(found, Some(Place::Playblast { directory: root.clone(), stem: stem.clone(), version: v.clone() }));
            }
        }

        /// The chooser's order is the same whatever order the folder lists
        /// its AOVs in, with the beauty first when there is one.
        #[test]
        fn aovs_are_ordered_whatever_the_listing(names in prop::collection::btree_set(word(), 0..8), beauty in any::<bool>(), seed in any::<u64>()) {
            let mut names: Vec<String> = names.into_iter().filter(|n| n != BEAUTY).collect();
            if beauty {
                names.push(BEAUTY.to_owned());
            }
            let mut sorted = names.clone();
            aov_order(&mut sorted);
            // Another listing order: rotated by the seed.
            if !names.is_empty() {
                let by = (seed % names.len() as u64) as usize;
                names.rotate_left(by);
            }
            names.reverse();
            aov_order(&mut names);
            prop_assert_eq!(&names, &sorted);
            prop_assert_eq!(sorted.first().is_some_and(|n| n == BEAUTY), beauty);
        }

        /// No name panics, and only frame files are frames.
        #[test]
        fn no_name_panics(name in "\\PC{0,24}") {
            let _ = place(Path::new(&name));
            let _ = playblast_name(&name);
            prop_assert_eq!(is_frame_file(&format!("{name}.exr")), true);
            prop_assert_eq!(is_frame_file(&format!("{name}.mp4")), false);
        }
    }
}
