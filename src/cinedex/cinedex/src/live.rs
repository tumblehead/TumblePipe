//! A shot's live playblasts as the editor follows them (`th_pipe::live`).
//!
//! When the animator renders a shot live, the editor plays that render over
//! the shot's take, as its frames land. A [`LiveWatch`] belongs to a shot's
//! resolved source: the folders a live playblast of the shot would be in
//! (one per department, on this machine's disk) and the live render it
//! plays, if any. Polling it is one file stat of the folder it plays: it
//! reads that live.json only when its time changed. What a poll comes to:
//!
//! - new frames of the run it plays: those frames are read again (a frame
//!   the render had not reached showed the previous picture);
//! - a new run there, or the render gone: the shot is resolved again;
//! - the render ended or its husk died (its Houdini was killed): only what
//!   the clip says changes.
//!
//! A live run starting where the shot plays none (another department, or a
//! shot with none yet) is not looked for here: Houdini announces it on the
//! change journal, and the editor resolves the shot (`crate::journal`).

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::time::SystemTime;

use th_pipe::live::{self, CANCELLED, DONE, FAILED, LiveState, RENDERING};
use th_pipe::{Project, Uri};
use th_playlist::Take;

/// What a poll of a [`LiveWatch`] came to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Change {
    None,
    /// Only what the clip says changed (the render ended, or its husk died).
    Label,
    /// These media indices were written anew: read them again.
    Frames(Vec<usize>),
    /// Resolve the shot again.
    Resolve,
}

/// The live render a shot plays.
#[derive(Debug, Clone)]
pub(crate) struct Playing {
    /// Which of the watch's folders it is in.
    folder: usize,
    department: String,
    run: u64,
    first: i64,
    step: i64,
    done: BTreeSet<i64>,
    frames: usize,
    state: String,
}

impl Playing {
    fn of(folder: usize, state: &LiveState) -> Self {
        Self {
            folder,
            department: state.department.clone(),
            run: state.run,
            first: state.range[0],
            step: state.step.max(1),
            done: state.done.iter().copied().collect(),
            frames: state.order.len(),
            state: state.state.clone(),
        }
    }

    /// What the clip says: the department's live render and how far it is.
    pub(crate) fn label(&self) -> String {
        let (done, of) = (self.done.len(), self.frames);
        match self.state.as_str() {
            RENDERING => format!("{} live, rendering {done}/{of}", self.department),
            DONE => format!("{} live", self.department),
            FAILED => format!("{} live, failed at {done}/{of}", self.department),
            CANCELLED => format!("{} live, stopped at {done}/{of}", self.department),
            other => format!("{} live, {other}", self.department),
        }
    }

    /// [`Self::label`], and while frames are still unrendered, the take
    /// `under` that shows in their place.
    pub(crate) fn label_over(&self, under: Option<&str>) -> String {
        match under {
            Some(under) if self.done.len() < self.frames => format!("{} over {under}", self.label()),
            _ => self.label(),
        }
    }

    fn index(&self, frame: i64) -> Option<usize> {
        let offset = frame - self.first;
        (offset >= 0 && offset % self.step == 0).then(|| (offset / self.step) as usize)
    }
}

/// A shot's live folders, and the render it plays.
#[derive(Debug, Clone, Default)]
pub(crate) struct LiveWatch {
    folders: Vec<PathBuf>,
    /// When the live.json of the folder it plays was last written.
    seen: Option<SystemTime>,
    playing: Option<Playing>,
}

impl LiveWatch {
    /// The render it plays, if any.
    pub(crate) fn playing(&self) -> Option<&Playing> {
        self.playing.as_ref()
    }

    /// The folder of the live render it plays, for Keep as playblast
    /// (which reads its live.json afresh and refuses one not done).
    pub(crate) fn playing_folder(&self) -> Option<PathBuf> {
        self.playing.as_ref().map(|p| self.folders[p.folder].clone())
    }

    /// What changed since the last poll. `husk` also asks whether a render
    /// still says rendering because its husk died without a word.
    pub(crate) fn poll(&mut self, husk: bool) -> Change {
        let Some(playing) = &mut self.playing else { return Change::None };
        let folder = &self.folders[playing.folder];
        let written = live::state_written(folder);
        let changed = written != self.seen;
        self.seen = written;
        if written.is_none() {
            return Change::Resolve;
        }
        if !changed && !(husk && playing.state == RENDERING) {
            return Change::None;
        }
        let Some(state) = live::read_state(folder) else { return Change::Resolve };
        let state = live::effective_state(folder, state);
        if state.run != playing.run || state.range[0] != playing.first || state.step.max(1) != playing.step {
            return Change::Resolve;
        }
        let done: BTreeSet<i64> = state.done.iter().copied().collect();
        let fresh: Vec<usize> = done.difference(&playing.done).filter_map(|&f| playing.index(f)).collect();
        let relabel = state.state != playing.state || done.len() != playing.done.len();
        playing.done = done;
        playing.state = state.state;
        playing.frames = state.order.len();
        match (fresh.is_empty(), relabel) {
            (false, _) => Change::Frames(fresh),
            (true, true) => Change::Label,
            (true, false) => Change::None,
        }
    }
}

/// The watch of `entity`'s live folders under `root`, and the live take it
/// plays: the shot's live render started last, if any.
pub(crate) fn watch(project: &Project, entity: &Uri, root: &std::path::Path) -> Result<(LiveWatch, Option<Take>), String> {
    let project_path = th_pipe::storage::slashed(&project.root);
    let folders: Vec<PathBuf> = th_pipe::departments::list_departments(&project.config, "shots", true, true)?
        .into_iter()
        .filter_map(|d| live::live_folder(root, &project_path, &entity.segments, &d.name).ok())
        .collect();
    let mut watch = LiveWatch { folders, seen: None, playing: None };
    let takes = live::live_takes(project, entity, root)?;
    let latest = takes.into_iter().max_by(|a, b| a.1.started_at.unwrap_or(0.0).total_cmp(&b.1.started_at.unwrap_or(0.0)));
    let Some((take, state, folder)) = latest else { return Ok((watch, None)) };
    let index = watch.folders.iter().position(|f| *f == folder).ok_or("the live take's folder is not one the shot has")?;
    watch.seen = live::state_written(&folder);
    watch.playing = Some(Playing::of(index, &state));
    Ok((watch, Some(take)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    fn write_state(folder: &std::path::Path, run: u64, done: &[i64], state: &str) {
        std::fs::create_dir_all(folder).unwrap();
        let order: Vec<i64> = (1001..=1010).collect();
        let body = json!({
            "uri": "entity:/shots/SQ010/SH010", "department": "animation", "range": [1001, 1010], "step": 1,
            "fps": 24, "run": run, "started_at": 0.0, "order": order, "done": done, "state": state,
            "pid": std::process::id(), "program": live::program_name(&std::env::current_exe().unwrap().to_string_lossy()),
        });
        // A new modification time each write, as each of husk's writes has.
        let path = folder.join(live::STATE_FILE);
        let before = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
        loop {
            std::fs::write(&path, serde_json::to_vec(&body).unwrap()).unwrap();
            if std::fs::metadata(&path).unwrap().modified().ok() != before {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }

    fn watching(folders: &[PathBuf], playing: Option<(usize, u64, &[i64])>) -> LiveWatch {
        let seen = playing.and_then(|(folder, _, _)| live::state_written(&folders[folder]));
        let playing = playing.map(|(folder, run, done)| Playing {
            folder,
            department: "animation".into(),
            run,
            first: 1001,
            step: 1,
            done: done.iter().copied().collect(),
            frames: 10,
            state: RENDERING.into(),
        });
        LiveWatch { folders: folders.to_vec(), seen, playing }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(16))]

        /// The frames a run finished since the last poll are read again,
        /// once; a poll with nothing new is nothing.
        #[test]
        fn new_frames_of_the_run_are_read_again(before in 0usize..10, after in 0usize..=10) {
            let after = after.max(before);
            let dir = tempfile::tempdir().unwrap();
            let folder = dir.path().join("SQ010_SH010_animation");
            let frames: Vec<i64> = (1001..=1010).collect();
            write_state(&folder, 3, &frames[..before], RENDERING);
            let mut watch = watching(std::slice::from_ref(&folder), Some((0, 3, &frames[..before])));
            prop_assert_eq!(watch.poll(false), Change::None);
            write_state(&folder, 3, &frames[..after], RENDERING);
            let expected = if after > before { Change::Frames((before..after).collect()) } else { Change::None };
            prop_assert_eq!(watch.poll(false), expected);
            prop_assert_eq!(watch.poll(false), Change::None);
            prop_assert_eq!(watch.playing().unwrap().label(), format!("animation live, rendering {after}/10"));
        }

        /// A new run or the render gone: the shot is resolved again. A
        /// render starting in another department, or where there was none,
        /// is the journal's to tell (`crate::journal`): the watch does not
        /// look in folders it does not play.
        #[test]
        fn a_new_render_resolves_the_shot(which in 0usize..4) {
            let dir = tempfile::tempdir().unwrap();
            let folders = [dir.path().join("SQ010_SH010_animation"), dir.path().join("SQ010_SH010_layout")];
            let mut watch = if which == 3 {
                watching(&folders, None)
            } else {
                write_state(&folders[0], 3, &[1001], RENDERING);
                watching(&folders, Some((0, 3, &[1001])))
            };
            match which {
                0 => write_state(&folders[0], 4, &[], RENDERING),
                1 => write_state(&folders[1], 1, &[], RENDERING),
                2 => std::fs::remove_file(folders[0].join(live::STATE_FILE)).unwrap(),
                _ => write_state(&folders[0], 1, &[], RENDERING),
            }
            let expected = if which == 0 || which == 2 { Change::Resolve } else { Change::None };
            prop_assert_eq!(watch.poll(false), expected);
        }
    }

    /// A render that says "rendering" with its husk gone reads as stopped,
    /// once the poll asks after the husk: the clip says so, nothing is read
    /// again.
    #[test]
    fn a_dead_husk_changes_only_the_label() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("SQ010_SH010_animation");
        write_state(&folder, 2, &[1001, 1002], RENDERING);
        let mut body: serde_json::Value = serde_json::from_slice(&std::fs::read(folder.join(live::STATE_FILE)).unwrap()).unwrap();
        body["program"] = json!("husk");
        std::fs::write(folder.join(live::STATE_FILE), serde_json::to_vec(&body).unwrap()).unwrap();
        let mut watch = watching(std::slice::from_ref(&folder), Some((0, 2, &[1001, 1002])));
        assert_eq!(watch.poll(false), Change::None, "without asking after the husk, nothing changed");
        // Its frames were never written: the run stopped with none.
        assert_eq!(watch.poll(true), Change::Label);
        assert_eq!(watch.playing().unwrap().label(), "animation live, stopped at 0/10");
    }

    #[test]
    fn labels_say_how_far_the_render_is() {
        let mut playing = Playing {
            folder: 0,
            department: "animation".into(),
            run: 1,
            first: 1001,
            step: 2,
            done: [1001, 1003].into(),
            frames: 5,
            state: RENDERING.into(),
        };
        assert_eq!(playing.label(), "animation live, rendering 2/5");
        assert_eq!(playing.label_over(Some("animation v0012")), "animation live, rendering 2/5 over animation v0012");
        assert_eq!(playing.label_over(None), "animation live, rendering 2/5");
        assert_eq!((playing.index(1003), playing.index(1004), playing.index(999)), (Some(1), None, None));
        playing.state = DONE.into();
        playing.done = [1001, 1003, 1005, 1007, 1009].into();
        assert_eq!(playing.label(), "animation live");
        assert_eq!(playing.label_over(Some("animation v0012")), "animation live", "every frame rendered: nothing shows under");
    }
}
