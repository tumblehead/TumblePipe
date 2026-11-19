//! A shot's render that is on its way: the newest version of a render
//! layer when it records its frame range and is published (not a check
//! render) but lacks frames, and which of its frames are on disk.
//!
//! Such a version is not a take ([`crate::discovery`]: a take has every
//! frame), so nothing plays it; the editor says how far it is on the shot's
//! clips (`cinedex::rendering`). Of several unfinished layers the one
//! waited for is the one best available would pick once done
//! ([`waited_for`]): the most downstream department, its slapcomp, else
//! `default`. An older unfinished version under a newer one is an abandoned
//! render and is never it.
//!
//! A playblast the farm (or the local queue) is rendering is said the same
//! way. Its worker renders on its own disk and writes only the finished
//! movie to the project; meanwhile it copies each finished frame into
//! `<movie>.mp4.frames` beside the movie to come, with the range in
//! `frames.json`, and removes the folder when it ends
//! (`farm.workers.playblast.FrameMirror`). [`find_playblast`] reads those:
//! a shot waits for the playblast of its most downstream department that
//! has one newer than every movie there. A render on its way comes first.
//!
//! [`find`] reads a shot's render folders and lists the frames of only the
//! newest version of each layer; [`Unfinished::relisted`] is one listing of
//! one folder, for following a render already found.

use crate::Project;
use crate::departments::{SLAPCOMP, department_names};
use crate::discovery::{Aov, RenderVersion, get_render, movie_versions, playblast_dir, playblast_stem, version_of_movie};
use crate::naming::version_key;
use crate::timeline::BlockRange;
use crate::uri::Uri;

/// A render version of a shot that lacks frames.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unfinished {
    pub department: String,
    pub layer: String,
    pub version: String,
    /// The frames it shows (its beauty), where they are listed.
    pub aov: Aov,
    /// The frames it is to have, ascending.
    pub expected: Vec<i64>,
    /// Those of them on disk, ascending.
    pub there: Vec<i64>,
    /// A playblast being rendered, not a render: `layer` is empty, and
    /// `aov` its frames folder, which goes when it ends.
    pub playblast: bool,
}

/// What a playblast's frames folder adds to its movie's name
/// (`farm.frames.PLAYBLAST_FRAMES_SUFFIX`).
pub const PLAYBLAST_FRAMES_SUFFIX: &str = ".frames";
/// The file in it that says what is rendered (`PLAYBLAST_FRAMES_INFO`).
pub const PLAYBLAST_FRAMES_INFO: &str = "frames.json";
/// Its frames are husk's own: `playblast.NNNN.jpg`.
const PLAYBLAST_FRAME: (&str, &str) = ("playblast", "jpg");

/// `found` as the frames of `expected` among them, ascending.
pub fn present(expected: &[i64], found: &[i64]) -> Vec<i64> {
    expected.iter().copied().filter(|frame| found.binary_search(frame).is_ok()).collect()
}

impl Unfinished {
    /// How many of its frames are there, and how many it is to have.
    pub fn counts(&self) -> (usize, usize) {
        (self.there.len(), self.expected.len())
    }

    pub fn complete(&self) -> bool {
        self.there.len() >= self.expected.len()
    }

    /// What a clip says of it: `lighting v0007: 34/120 rendered`.
    pub fn label(&self) -> String {
        let (done, total) = self.counts();
        let what = if self.playblast { " playblast" } else { "" };
        format!("{} {}{what}: {done}/{total} rendered", self.department, self.version)
    }

    /// The same with its frames folder listed again (one listing). A
    /// folder that no longer reads has no frames.
    ///
    /// A playblast's folder is removed when it ends, so one whose folder
    /// is gone has nothing more to wait for: it reads as complete.
    pub fn relisted(&self) -> Self {
        if self.playblast && !self.aov.path.is_dir() {
            return Self { there: self.expected.clone(), ..self.clone() };
        }
        let found = self.aov.frames().unwrap_or_default();
        Self { there: present(&self.expected, &found), ..self.clone() }
    }
}

/// How a render version stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// Every frame is there.
    Complete,
    /// It records its range and is published, and lacks frames.
    Unfinished,
    /// A check render, or one with no range or frames that do not read:
    /// never the shot's render.
    Other,
}

/// One render version among a shot's, for [`waited_for`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    /// Its department's place in pipeline order (later is further down).
    pub department: usize,
    pub layer: String,
    /// Its version's number.
    pub version: u64,
    pub state: State,
}

/// A layer's standing among a department's: the slapcomp first, then
/// `default`, then the others by name.
fn layer_rank(layer: &str) -> (u8, &str) {
    if layer == SLAPCOMP {
        (0, "")
    } else if layer == "default" {
        (1, "")
    } else {
        (2, layer)
    }
}

/// Which of `candidates` a shot is waiting for, if any: of the layers
/// whose newest version is unfinished, the one of the most downstream
/// department, its slapcomp before `default` before the rest.
pub fn waited_for(candidates: &[Candidate]) -> Option<usize> {
    let newest =
        |c: &Candidate| candidates.iter().filter(|o| o.department == c.department && o.layer == c.layer).all(|o| o.version <= c.version);
    candidates
        .iter()
        .enumerate()
        .filter(|(_, c)| c.state == State::Unfinished && newest(c))
        .min_by(|(_, a), (_, b)| b.department.cmp(&a.department).then_with(|| layer_rank(&a.layer).cmp(&layer_rank(&b.layer))))
        .map(|(i, _)| i)
}

/// How `version` stands, and the frames it is to have and has (one listing
/// of its frames folder, its `context.json` read).
fn standing(version: &RenderVersion) -> (State, Vec<i64>, Vec<i64>) {
    let (Some(range), true) = (version.frame_range(), version.published()) else { return (State::Other, Vec::new(), Vec::new()) };
    let Some(found) = version.shown().0.frames() else { return (State::Other, Vec::new(), Vec::new()) };
    let expected: Vec<i64> = range.frames().collect();
    let state = if found == expected { State::Complete } else { State::Unfinished };
    let there = present(&expected, &found);
    (state, expected, there)
}

/// The version of the playblast a department waits for: the newest of
/// those `rendering` (with a frames folder) when it is newer than every
/// one of its `movies`. A frames folder at or under a movie's version is a
/// leftover of a playblast that was killed, not one on its way.
pub fn playblast_waited_for(movies: &[u64], rendering: &[u64]) -> Option<u64> {
    let newest = rendering.iter().copied().max()?;
    movies.iter().all(|movie| *movie < newest).then_some(newest)
}

/// The playblast being rendered that `shot` waits for, if any: its most
/// downstream department's ([`playblast_waited_for`]). One listing of each
/// department's playblast folder, and one of the frames folder found.
pub fn find_playblast(project: &Project, shot: &Uri) -> Option<Unfinished> {
    let departments = department_names(&project.config, "shots").ok()?;
    for department in departments.iter().rev() {
        let Ok(directory) = playblast_dir(project, shot, department, None) else { continue };
        let stem = playblast_stem(shot, department, None);
        let Ok(entries) = std::fs::read_dir(&directory) else { continue };
        let mut rendering: Vec<(u64, String, std::path::PathBuf)> = Vec::new();
        for path in entries.flatten().map(|entry| entry.path()) {
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let Some(movie) = name.strip_suffix(PLAYBLAST_FRAMES_SUFFIX) else { continue };
            if let Some(version) = version_of_movie(&path.with_file_name(movie), &stem) {
                rendering.push((version_key(&version), version, path));
            }
        }
        let movies: Vec<u64> = movie_versions(&directory, &stem).keys().map(|version| version_key(version)).collect();
        let keys: Vec<u64> = rendering.iter().map(|r| r.0).collect();
        let Some(newest) = playblast_waited_for(&movies, &keys) else { continue };
        let Some((_, version, path)) = rendering.into_iter().find(|r| r.0 == newest) else { continue };
        let Ok(info) = crate::io::load_json(&path.join(PLAYBLAST_FRAMES_INFO)) else { continue };
        let number = |key: &str| info.get(key).and_then(serde_json::Value::as_i64);
        let (Some(first), Some(last), Some(step)) = (number("first_frame"), number("last_frame"), number("step_size")) else { continue };
        let Ok(range) = BlockRange::new(first, last, step) else { continue };
        let aov = Aov { path, name: PLAYBLAST_FRAME.0.into(), suffix: PLAYBLAST_FRAME.1.into() };
        let expected: Vec<i64> = range.frames().collect();
        let there = present(&expected, &aov.frames().unwrap_or_default());
        return Some(Unfinished { department: department.clone(), layer: String::new(), version, aov, expected, there, playblast: true });
    }
    None
}

/// What `shot` waits for, if anything: its unfinished render
/// ([`find_render`]), else the playblast being rendered of it
/// ([`find_playblast`]).
pub fn find(project: &Project, shot: &Uri) -> Option<Unfinished> {
    find_render(project, shot).or_else(|| find_playblast(project, shot))
}

/// The unfinished render `shot` waits for, if any. Reads the shot's render
/// folders; only the newest version of each layer has its frames listed.
pub fn find_render(project: &Project, shot: &Uri) -> Option<Unfinished> {
    let departments = department_names(&project.config, "render").ok()?;
    // Each layer's newest version: how it stands, and what it would be
    // said as were it the one waited for.
    let mut candidates: Vec<Candidate> = Vec::new();
    let mut newest_of: Vec<Unfinished> = Vec::new();
    for (place, department) in departments.iter().enumerate() {
        let Ok(Some(layers)) = get_render(project, shot, department) else { continue };
        for (layer, versions) in layers {
            let Some(newest) = versions.into_iter().max_by_key(|v| version_key(&v.version)) else { continue };
            let (state, expected, there) = standing(&newest);
            candidates.push(Candidate { department: place, layer: layer.clone(), version: version_key(&newest.version), state });
            newest_of.push(Unfinished {
                department: department.clone(),
                layer,
                version: newest.version.clone(),
                aov: newest.shown().0,
                expected,
                there,
                playblast: false,
            });
        }
    }
    Some(newest_of.swap_remove(waited_for(&candidates)?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::naming::entity_stem;
    use crate::testing::make_project;
    use proptest::prelude::*;
    use serde_json::json;
    use std::path::{Path, PathBuf};

    fn unfinished(expected: Vec<i64>, there: Vec<i64>) -> Unfinished {
        Unfinished {
            department: "lighting".into(),
            layer: "default".into(),
            version: "v0007".into(),
            aov: Aov { path: PathBuf::from("nowhere"), name: "sh010_default_beauty_v0007".into(), suffix: "exr".into() },
            expected,
            there,
            playblast: false,
        }
    }

    proptest! {
        /// The count and the label say how many of its frames are there;
        /// frames on disk that are not its own are not counted.
        #[test]
        fn the_label_counts_its_own_frames(
            first in 990i64..1010, count in 1usize..60, step in 1i64..4,
            picks in prop::collection::vec(any::<bool>(), 60), stray in prop::collection::vec(0i64..2000, 0..5),
        ) {
            let expected: Vec<i64> = (0..count as i64).map(|i| first + i * step).collect();
            let mut found: Vec<i64> = expected.iter().zip(&picks).filter(|(_, on)| **on).map(|(f, _)| *f).chain(stray.iter().copied()).collect();
            found.sort();
            found.dedup();
            let own = present(&expected, &found);
            let want: Vec<i64> = expected.iter().copied().filter(|f| found.contains(f)).collect();
            prop_assert_eq!(&own, &want);
            let u = unfinished(expected.clone(), own.clone());
            prop_assert_eq!(u.counts(), (want.len(), expected.len()));
            prop_assert_eq!(u.label(), format!("lighting v0007: {}/{} rendered", want.len(), expected.len()));
            prop_assert_eq!(u.complete(), want.len() == expected.len());
        }

        /// The render a shot waits for is the newest version of a layer
        /// when that is unfinished: of several, the most downstream
        /// department's, its slapcomp before default before any other. An
        /// unfinished version under a newer one is never it.
        #[test]
        fn the_newest_unfinished_render_is_waited_for(
            raw in prop::collection::vec((0usize..3, prop::sample::select(vec!["default", "chars", "slapcomp", "env"]), 1u64..6, 0usize..3), 0..10),
        ) {
            let candidates: Vec<Candidate> = raw.iter().map(|(department, layer, version, state)| Candidate {
                department: *department,
                layer: (*layer).to_owned(),
                version: *version,
                state: [State::Complete, State::Unfinished, State::Other][*state],
            }).collect();
            let got = waited_for(&candidates);
            // By hand: every (department, layer) whose highest version is
            // unfinished (in each of its copies), ranked.
            let rank = |c: &Candidate| (std::cmp::Reverse(c.department), match c.layer.as_str() { "slapcomp" => 0, "default" => 1, _ => 2 }, c.layer.clone());
            let mut waiting: Vec<&Candidate> = candidates.iter().filter(|c| {
                let top = candidates.iter().filter(|o| o.department == c.department && o.layer == c.layer).map(|o| o.version).max().unwrap();
                c.version == top && c.state == State::Unfinished
            }).collect();
            waiting.sort_by_key(|c| rank(c));
            match (got, waiting.first()) {
                (None, None) => {}
                (Some(i), Some(best)) => prop_assert_eq!(rank(&candidates[i]), rank(best)),
                (got, want) => prop_assert!(false, "{:?} for {:?}", got, want),
            }
        }
    }

    /// On disk: a shot's newest render version with frames missing is
    /// found with the frames it has; a frame landing shows at the next
    /// listing; the last one completes it.
    #[test]
    fn a_render_on_disk_is_found_and_followed() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("beauty");
        std::fs::create_dir_all(&folder).unwrap();
        let aov = Aov { path: folder.clone(), name: "sh010_default_beauty_v0007".into(), suffix: "exr".into() };
        let frame = |n: i64| folder.join(format!("sh010_default_beauty_v0007.{n:04}.exr"));
        for n in [1001, 1003] {
            std::fs::write(frame(n), b"").unwrap();
        }
        std::fs::write(folder.join("notes.txt"), b"").unwrap();
        let mut watched = Unfinished { aov, ..unfinished((1001..=1004).collect(), Vec::new()) }.relisted();
        assert_eq!((watched.there.clone(), watched.label()), (vec![1001, 1003], "lighting v0007: 2/4 rendered".to_owned()));
        std::fs::write(frame(1002), b"").unwrap();
        // A frame outside its range is not its own.
        std::fs::write(frame(1009), b"").unwrap();
        watched = watched.relisted();
        assert_eq!((watched.there.clone(), watched.complete()), (vec![1001, 1002, 1003], false));
        std::fs::write(frame(1004), b"").unwrap();
        assert!(watched.relisted().complete());
        // Its folder gone: no frames, not an error.
        drop(dir);
        assert_eq!(watched.relisted().counts(), (0, 4));
    }

    proptest! {
        /// A department waits for its newest playblast with frames when no
        /// movie is as new; never for one a movie has reached or passed.
        #[test]
        fn the_newest_playblast_past_every_movie_is_waited_for(
            movies in prop::collection::vec(1u64..12, 0..5),
            rendering in prop::collection::vec(1u64..12, 0..5),
        ) {
            let got = playblast_waited_for(&movies, &rendering);
            let mut want = None;
            for version in 1..12 {
                let newer_or_same_movie = movies.iter().any(|m| *m >= version);
                let newer_rendering = rendering.iter().any(|r| *r > version);
                if rendering.contains(&version) && !newer_rendering && !newer_or_same_movie {
                    want = Some(version);
                }
            }
            prop_assert_eq!(got, want);
        }
    }

    /// In a project: a playblast the farm is rendering (its frames folder
    /// beside the movie to come) is found with the frames copied so far,
    /// and followed; a leftover folder under a written movie is not; a
    /// render on its way comes first; the folder gone, it has ended.
    #[test]
    fn a_playblast_being_rendered_is_found_and_followed() {
        let (_dir, root, project) = project();
        let entity = Uri::parse_address("entity:/shots/S/010").unwrap();
        let folder = root.parent().unwrap().join("Show_render/playblast/shots/S/010/animation");
        let stem = entity_stem(&entity.segments, &["animation"]);
        let frames_of = |code: u32| folder.join(format!("{stem}_v{code:04}.mp4{PLAYBLAST_FRAMES_SUFFIX}"));
        let begin = |code: u32, info: serde_json::Value| {
            std::fs::create_dir_all(frames_of(code)).unwrap();
            std::fs::write(frames_of(code).join(PLAYBLAST_FRAMES_INFO), info.to_string()).unwrap();
        };
        let land = |code: u32, frame: i64| std::fs::write(frames_of(code).join(format!("playblast.{frame:04}.jpg")), b"").unwrap();
        let movie = |code: u32| std::fs::write(folder.join(format!("{stem}_v{code:04}.mp4")), b"").unwrap();
        let info = json!({"first_frame": 1001, "last_frame": 1009, "step_size": 2, "fps": 24});
        assert_eq!(find(&project, &entity), None);
        // v0001's movie is written; a folder left from it is not waited for.
        begin(1, info.clone());
        land(1, 1001);
        assert_eq!(find(&project, &entity).map(|u| u.version), Some("v0001".to_owned()));
        movie(1);
        assert_eq!(find(&project, &entity), None);
        // v0002 is rendering: two of its five frames are copied, a third on its way.
        begin(2, info.clone());
        land(2, 1001);
        land(2, 1003);
        // A frame between its steps is not its own.
        land(2, 1004);
        std::fs::write(frames_of(2).join("playblast.1005.jpg.part"), b"").unwrap();
        // Layout, upstream of animation, is rendering one too: the shot
        // waits for the one further down.
        let layout = root.parent().unwrap().join("Show_render/playblast/shots/S/010/layout");
        let layout_frames = layout.join(format!("{}_v0001.mp4{PLAYBLAST_FRAMES_SUFFIX}", entity_stem(&entity.segments, &["layout"])));
        std::fs::create_dir_all(&layout_frames).unwrap();
        std::fs::write(layout_frames.join(PLAYBLAST_FRAMES_INFO), info.to_string()).unwrap();
        let found = find(&project, &entity).expect("v0002 is being rendered");
        assert!(found.playblast);
        assert_eq!((found.department.as_str(), found.version.as_str()), ("animation", "v0002"));
        assert_eq!((found.expected.clone(), found.there.clone()), (vec![1001, 1003, 1005, 1007, 1009], vec![1001, 1003]));
        assert_eq!(found.label(), "animation v0002 playblast: 2/5 rendered");
        // A folder that does not say its range is not read: animation
        // then waits for none, and layout's is the shot's.
        begin(3, json!({"first_frame": 1001}));
        assert_eq!(find(&project, &entity).map(|u| u.department), Some("layout".to_owned()));
        std::fs::remove_dir_all(frames_of(3)).unwrap();
        // Following it.
        land(2, 1005);
        let followed = found.relisted();
        assert_eq!((followed.there.clone(), followed.complete()), (vec![1001, 1003, 1005], false));
        // A render on its way comes before it.
        let range = Some(json!({"first_frame": 1001, "last_frame": 1006, "step_size": 1}));
        write_version(&root, &entity, "lighting", "default", 1, &[1001], range);
        assert_eq!(find(&project, &entity).map(|u| (u.playblast, u.department)), Some((false, "lighting".to_owned())));
        assert_eq!(find_playblast(&project, &entity).map(|u| u.version), Some("v0002".to_owned()));
        // It ends: the movie is written and the folder goes.
        movie(2);
        assert_eq!(find_playblast(&project, &entity).map(|u| u.department), Some("layout".to_owned()));
        std::fs::remove_dir_all(frames_of(2)).unwrap();
        assert!(followed.relisted().complete());
        // Animation's has ended; layout's is still on its way.
        assert_eq!(find_playblast(&project, &entity).map(|u| (u.department, u.there)), Some(("layout".to_owned(), Vec::new())));
        std::fs::remove_dir_all(&layout_frames).unwrap();
        assert_eq!(find_playblast(&project, &entity), None);
    }

    /// A project with two render departments, `lighting` then `comp`.
    fn project() -> (tempfile::TempDir, PathBuf, Project) {
        let (dir, root) = make_project();
        let department = json!({"properties": {"independent": false, "publishable": true, "renderable": true}, "children": {}});
        let departments = json!({"properties": {}, "children": {
            "shots": {"properties": {}, "children": {"layout": department, "animation": department}},
            "render": {"properties": {}, "children": {"lighting": department, "comp": department}},
        }});
        std::fs::write(root.join("_config/db/departments.json"), departments.to_string()).unwrap();
        let project = Project::open(&root, &root).unwrap();
        (dir, root, project)
    }

    /// A render version of `layer` with `frames` of 1001 to 1006 on disk.
    fn write_version(
        root: &Path,
        entity: &Uri,
        department: &str,
        layer: &str,
        code: u32,
        frames: &[i64],
        context: Option<serde_json::Value>,
    ) {
        let version = format!("v{code:04}");
        let folder = root.parent().unwrap().join("Show_render/render/shots/S/010").join(department).join(layer).join(&version);
        let aov = folder.join("beauty");
        std::fs::create_dir_all(&aov).unwrap();
        let name = entity_stem(&entity.segments, &[layer, "beauty", &version]);
        for frame in frames {
            std::fs::write(aov.join(format!("{name}.{frame:04}.exr")), b"").unwrap();
        }
        if let Some(context) = context {
            std::fs::write(folder.join("context.json"), context.to_string()).unwrap();
        }
    }

    /// In a project: nothing rendered, nothing waited for; a newest
    /// version short of frames is found with the frames it has; a check
    /// render, a version with no context, and an unfinished version under
    /// a finished one are not; the downstream department's wins; and the
    /// last frame landing ends the wait.
    #[test]
    fn a_shots_unfinished_render_is_found() {
        let (_dir, root, project) = project();
        let entity = Uri::parse_address("entity:/shots/S/010").unwrap();
        let range = || Some(json!({"first_frame": 1001, "last_frame": 1006, "step_size": 1}));
        assert_eq!(find(&project, &entity), None);
        // A version short of frames that records no range is not waited
        // for, nor is a check render.
        write_version(&root, &entity, "lighting", "default", 1, &[1001], None);
        assert_eq!(find(&project, &entity), None);
        let check = json!({"first_frame": 1001, "last_frame": 1006, "step_size": 1, "published": false});
        write_version(&root, &entity, "lighting", "default", 1, &[1001], Some(check));
        assert_eq!(find(&project, &entity), None);
        // v0001 complete, v0002 abandoned short, v0003 complete: nothing.
        write_version(&root, &entity, "lighting", "default", 1, &[1001, 1002, 1003, 1004, 1005, 1006], range());
        write_version(&root, &entity, "lighting", "default", 2, &[1001, 1002], range());
        write_version(&root, &entity, "lighting", "default", 3, &[1001, 1002, 1003, 1004, 1005, 1006], range());
        assert_eq!(find(&project, &entity), None);
        // v0004 on its way.
        write_version(&root, &entity, "lighting", "default", 4, &[1001, 1002, 1005], range());
        let found = find(&project, &entity).expect("v0004 is on its way");
        assert_eq!((found.department.as_str(), found.layer.as_str(), found.version.as_str()), ("lighting", "default", "v0004"));
        assert_eq!((found.expected.clone(), found.there.clone()), ((1001..=1006).collect::<Vec<_>>(), vec![1001, 1002, 1005]));
        assert_eq!(found.label(), "lighting v0004: 3/6 rendered");
        // A check render and a version with no context are never waited for.
        write_version(
            &root,
            &entity,
            "lighting",
            "chars",
            1,
            &[1001],
            Some(json!({"first_frame": 1001, "last_frame": 1006, "step_size": 1, "published": false})),
        );
        write_version(&root, &entity, "lighting", "env", 1, &[1001], None);
        assert_eq!(find(&project, &entity).map(|u| u.layer), Some("default".to_owned()));
        // The department further down the pipeline wins.
        write_version(&root, &entity, "comp", "default", 1, &[1006], range());
        let comp = find(&project, &entity).expect("comp v0001 is on its way");
        assert_eq!((comp.department.as_str(), comp.there.clone()), ("comp", vec![1006]));
        // Following it: a frame lands, then the rest.
        write_version(&root, &entity, "comp", "default", 1, &[1001], range());
        assert_eq!(comp.relisted().there, vec![1001, 1006]);
        write_version(&root, &entity, "comp", "default", 1, &[1002, 1003, 1004, 1005], range());
        assert!(comp.relisted().complete());
        // Complete, comp no longer waits; lighting v0004 still does.
        assert_eq!(find(&project, &entity).map(|u| u.department), Some("lighting".to_owned()));
    }
}
