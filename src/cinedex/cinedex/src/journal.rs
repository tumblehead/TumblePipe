//! What the change journal (`th_pipe::events`) asks of the editor.
//!
//! Every write of review media on this machine (a playblast from Houdini or
//! a farm job run here, render frames, Keep as playblast, a live run
//! starting) appends a line naming the shot and department, and every
//! config write a line naming its purpose. The editor follows the journal
//! (one local `stat()` a poll) instead of looking at the shots' folders on
//! the share: a line about a shot it shows resolves that shot again, a
//! config write reads the bins again (the master edit box, the shots), and
//! a workfile line reads the board again. Phase 6 of
//! designs/shot-dashboard-live-playblast.md.
//!
//! Playblasts and renders made on other machines (the farm) reach no
//! journal here; they announce themselves on the project's shared journal
//! as well, which the editor follows alongside ([`Shared`], #78). Other
//! kinds of change made elsewhere show after Refresh, as before.

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::{Arc, Mutex};

use th_pipe::events::{self, Change};
use th_pipe::shared_journal::{RecentIds, SharedReader};

/// What the editor does for a poll's changes.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Plan {
    /// Resolve every shot again (lines may have been lost, or the shots'
    /// config changed: their ranges, which exist).
    pub all: bool,
    /// Resolve these entries again (indices into the editor's entries).
    pub entries: BTreeSet<usize>,
    /// Read the bins again (the master edit, the shots, the media).
    pub bins: bool,
    /// Read the board again (a workfile was saved).
    pub board: bool,
}

/// The config purpose that holds the shots (their ranges, which exist).
const ENTITY_PURPOSE: &str = "entity";

/// What `changes` (a [`events::JournalReader::poll`]) ask of an editor
/// showing the project keyed `project` (`events::project_key`), whose
/// shot entries are `shots` (entry index, shot URI as written). `own_pid`
/// is the editor's: its own config writes it has applied already. With
/// `live` off, a live run changes nothing shown. None (lines may be lost)
/// asks for everything.
pub(crate) fn plan(changes: Option<&[Change]>, project: &str, own_pid: u32, shots: &[(usize, String)], live: bool) -> Plan {
    let Some(changes) = changes else {
        return Plan { all: true, entries: BTreeSet::new(), bins: true, board: true };
    };
    let mut plan = Plan::default();
    for change in changes.iter().filter(|c| c.project == project) {
        match change.kind.as_str() {
            events::CONFIG if change.pid != own_pid => {
                plan.bins = true;
                plan.board = true;
                plan.all |= change.detail == ENTITY_PURPOSE;
            }
            events::WORKFILE => plan.board = true,
            events::PLAYBLAST | events::RENDER => plan.entries.extend(entries_of(shots, &change.uri)),
            events::LIVE if live => plan.entries.extend(entries_of(shots, &change.uri)),
            _ => {}
        }
    }
    if plan.all {
        plan.entries.clear();
    }
    plan
}

/// Local journal polls between two looks at the shared journal: a listing
/// of a folder on the share every few seconds, not every poll.
pub(crate) const SHARED_EVERY: u64 = 6;

/// The project's shared journal (`th_pipe::shared_journal`), followed
/// alongside this machine's: playblasts and renders written on any machine,
/// the farm's included (#78). The share may be slow or gone, so it is
/// polled in the background, one poll at a time; a poll that hangs only
/// holds back the next. A change that reaches both journals (written on
/// this machine) is acted on once, whichever brings it first.
pub(crate) struct Shared {
    reader: Arc<Mutex<SharedReader>>,
    /// How the journal names this project, for the changes it brings.
    project: String,
    busy: bool,
    polls: u64,
    ids: RecentIds,
}

impl Shared {
    pub(crate) fn new(config: &Path, project: String) -> Self {
        Self { reader: Arc::new(Mutex::new(SharedReader::starting_now(config))), project, busy: false, polls: 0, ids: RecentIds::default() }
    }

    /// Called on every local poll: every [`SHARED_EVERY`]th, unless a poll
    /// is under way, the poll of the share to run in the background.
    pub(crate) fn due(&mut self) -> Option<impl FnOnce() -> Vec<Change> + Send + 'static> {
        self.polls += 1;
        if self.busy || !(self.polls - 1).is_multiple_of(SHARED_EVERY) {
            return None;
        }
        self.busy = true;
        let (reader, project) = (Arc::clone(&self.reader), self.project.clone());
        Some(move || {
            let events = reader.lock().map(|mut r| r.poll()).unwrap_or_default();
            events.iter().map(|e| e.change(&project)).collect()
        })
    }

    /// The shared journal's changes are in: those not seen on this
    /// machine's journal already.
    pub(crate) fn arrived(&mut self, changes: Vec<Change>) -> Vec<Change> {
        self.busy = false;
        self.ids.filter(changes)
    }

    /// This machine's journal's changes: those the shared journal did not
    /// bring already. None (lines may be lost) stays None.
    pub(crate) fn local(&mut self, changes: Option<Vec<Change>>) -> Option<Vec<Change>> {
        changes.map(|c| self.ids.filter(c))
    }
}

fn entries_of<'a>(shots: &'a [(usize, String)], uri: &'a str) -> impl Iterator<Item = usize> + 'a {
    let wanted = th_pipe::Uri::parse_address(uri).ok();
    shots.iter().filter(move |(_, shot)| wanted.is_some() && th_pipe::Uri::parse_address(shot).ok() == wanted).map(|(i, _)| *i)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    const PROJECT: &str = "p:\\show\\_config";
    const OWN: u32 = 7;

    fn shot(i: usize) -> String {
        format!("entity:/shots/SQ010/SH{:03}", (i % 4 + 1) * 10)
    }

    fn change() -> impl Strategy<Value = Change> {
        let kinds = prop::sample::select(vec![
            events::CONFIG,
            events::WORKFILE,
            events::EXPORT,
            events::PLAYBLAST,
            events::RENDER,
            events::LIVE,
            "something new",
        ]);
        (kinds, 0usize..5, any::<bool>(), any::<bool>(), prop::sample::select(vec!["entity", "edits", "groups", "animation"])).prop_map(
            |(kind, i, other_project, own, detail)| Change {
                project: if other_project { "q:\\other\\_config".into() } else { PROJECT.into() },
                kind: kind.into(),
                uri: if kind == events::CONFIG { String::new() } else { shot(i) },
                detail: detail.into(),
                pid: if own { OWN } else { OWN + 1 },
                time: 0.0,
                id: String::new(),
            },
        )
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(256))]

        /// A playblast, render or (with live on) live line resolves again
        /// exactly the entries of its shot; another project's lines and
        /// the editor's own config writes change nothing; another
        /// process's config write reads the bins and board again, and of
        /// the shots' purpose every shot.
        #[test]
        fn lines_ask_for_what_they_name(changes in prop::collection::vec(change(), 0..8), live in any::<bool>(), n in 0usize..6) {
            let shots: Vec<(usize, String)> = (0..n).map(|i| (i * 2, shot(i))).collect();
            let got = plan(Some(&changes), PROJECT, OWN, &shots, live);
            let mine: Vec<&Change> = changes.iter().filter(|c| c.project == PROJECT).collect();
            let config: Vec<&&Change> = mine.iter().filter(|c| c.kind == events::CONFIG && c.pid != OWN).collect();
            let all = config.iter().any(|c| c.detail == "entity");
            prop_assert_eq!(got.all, all);
            prop_assert_eq!(got.bins, !config.is_empty());
            prop_assert_eq!(got.board, !config.is_empty() || mine.iter().any(|c| c.kind == events::WORKFILE));
            let named: BTreeSet<&str> = mine
                .iter()
                .filter(|c| c.kind == events::PLAYBLAST || c.kind == events::RENDER || (live && c.kind == events::LIVE))
                .map(|c| c.uri.as_str())
                .collect();
            let expected: BTreeSet<usize> =
                if all { BTreeSet::new() } else { shots.iter().filter(|(_, s)| named.contains(s.as_str())).map(|(i, _)| *i).collect() };
            prop_assert_eq!(got.entries, expected);
        }
    }

    #[test]
    fn lost_lines_ask_for_everything() {
        let got = plan(None, PROJECT, OWN, &[(0, shot(0))], true);
        assert_eq!(got, Plan { all: true, entries: BTreeSet::new(), bins: true, board: true });
    }

    /// The URI is compared as a URI, not as text: a shot written by Python
    /// (`entity:/shots/SQ010/SH010`) is the clip's shot however the edit
    /// spells it.
    #[test]
    fn a_shot_is_found_however_the_edit_spells_it() {
        let line = Change {
            project: PROJECT.into(),
            kind: events::PLAYBLAST.into(),
            uri: "entity:/shots/SQ010/SH010".into(),
            detail: "animation".into(),
            pid: 1,
            time: 0.0,
            id: String::new(),
        };
        let spelled = th_pipe::Uri::parse_address("entity:/shots/SQ010/SH010").unwrap().to_string();
        let got = plan(Some(std::slice::from_ref(&line)), PROJECT, OWN, &[(3, spelled), (4, shot(2))], true);
        assert_eq!(got.entries, BTreeSet::from([3]));
    }

    /// A change file as Python's `shared_journal.write` leaves it.
    fn write_shared(config: &Path, ms: u64, id: &str, kind: &str, uri: &str) {
        let dir = th_pipe::shared_journal::shared_dir(config);
        std::fs::create_dir_all(&dir).unwrap();
        let body = serde_json::json!({"id": id, "kind": kind, "uri": uri, "detail": "lighting", "host": "farm01", "time": 1.0});
        std::fs::write(dir.join(format!("{ms:013}-{id}.json")), format!("{body}\n")).unwrap();
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(64))]

        /// The share is looked at on the first local poll and every
        /// SHARED_EVERY after, never while a look is under way.
        #[test]
        fn the_share_is_polled_now_and_then_one_at_a_time(arrivals in prop::collection::vec(any::<bool>(), 1..40)) {
            let dir = tempfile::tempdir().unwrap();
            let mut shared = Shared::new(&dir.path().join("_config"), PROJECT.into());
            let mut busy = false;
            for (n, arrives) in arrivals.into_iter().enumerate() {
                let due = shared.due();
                let expected = !busy && (n as u64).is_multiple_of(SHARED_EVERY);
                prop_assert_eq!(due.is_some(), expected, "poll {}", n);
                if let Some(poll) = due {
                    busy = true;
                    prop_assert_eq!(poll(), vec![]);
                }
                if busy && arrives {
                    shared.arrived(Vec::new());
                    busy = false;
                }
            }
        }
    }

    /// A render a farm worker wrote reaches the editor through the shared
    /// journal and resolves its shot again; the same change on this
    /// machine's journal (written here) is not acted on twice, whichever
    /// comes first.
    #[test]
    fn a_farm_render_resolves_its_shot_once() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("_config");
        write_shared(&config, 1, &"0".repeat(32), events::RENDER, &shot(0)); // before the editor
        let mut shared = Shared::new(&config, PROJECT.into());
        let shots: Vec<(usize, String)> = (0..4).map(|i| (i, shot(i))).collect();
        let poll = |shared: &mut Shared| {
            let run = shared.due().expect("due");
            let changes = shared.arrived(run());
            for _ in 1..SHARED_EVERY {
                assert!(shared.due().is_none());
            }
            changes
        };
        assert_eq!(poll(&mut shared), vec![], "history is not replayed");

        let farm = "a".repeat(32);
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
        write_shared(&config, now + 1, &farm, events::RENDER, &shot(1));
        let changes = poll(&mut shared);
        let got = plan(Some(&changes), PROJECT, OWN, &shots, true);
        assert_eq!(got.entries, BTreeSet::from([1]), "only the farm render's shot");
        assert!(!got.all && !got.bins);
        let local = Change {
            project: PROJECT.into(),
            kind: events::RENDER.into(),
            uri: shot(1),
            detail: "lighting".into(),
            pid: 1,
            time: 1.0,
            id: farm,
        };
        assert_eq!(shared.local(Some(vec![local.clone()])), Some(vec![]), "the shared journal brought it already");

        let here = "b".repeat(32);
        let local = Change { id: here.clone(), uri: shot(2), ..local };
        assert_eq!(shared.local(Some(vec![local.clone()])), Some(vec![local]));
        write_shared(&config, now + 2, &here, events::PLAYBLAST, &shot(2));
        assert_eq!(poll(&mut shared), vec![], "this machine's journal brought it already");
        assert_eq!(shared.local(None), None, "lost lines stay lost");
    }
}
