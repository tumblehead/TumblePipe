//! The project's shared journal, written as `tumblepipe.util.shared_journal`
//! writes it: review media (playblasts, renders) written on any machine,
//! the farm's included (#78).
//!
//! The change journal ([`crate::events`]) is per machine, so a playblast or
//! render a farm worker wrote reached the editor only on Refresh. Review
//! media also announce themselves in `<config>/journal/` on the project
//! share, one file per change (`<ms>-<id>.json`, renamed into place whole,
//! so a reader never sees half of one), pruned by the writers to the last
//! hour and at most a thousand changes. Ages go by the pruning writer's
//! clock: one running more than [`MAX_AGE_MS`] ahead prunes everyone's
//! fresh changes on each of its writes; readers poll every 3 s, so they
//! read most first, and Refresh covers the rest. The Python module's
//! docstring is the contract.
//!
//! [`SharedReader`] lists the folder and reads the names it has not seen.
//! Nothing it meets stops it: a missing or unreachable folder is no
//! changes (the next poll looks again, and finds what was written
//! meanwhile, pruning permitting), a file that is not a change is passed
//! over, one that cannot be read yet is read next time. The share may be
//! slow, so the editor polls it off the UI thread.
//!
//! The same change reaches this machine's journal too when it was written
//! here, under the same id: [`RecentIds`] lets each through once.

use std::collections::{BTreeSet, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::events::{self, Change};

/// The folder under the project's config folder (`_config`).
pub const DIR_NAME: &str = "journal";
/// The kinds of change the shared journal carries.
pub const KINDS: [&str; 2] = [events::PLAYBLAST, events::RENDER];
const TIME_DIGITS: usize = 13;
const ID_DIGITS: usize = 32;

/// The shared journal's folder of the project whose config folder is `config`.
pub fn shared_dir(config: &Path) -> PathBuf {
    config.join(DIR_NAME)
}

/// The milliseconds a change's file name (`<13 digits>-<32 hex>.json`)
/// holds; None for any other name (a file being written, anything else).
pub fn name_time(name: &str) -> Option<u64> {
    let stem = name.strip_suffix(".json")?;
    let (time, id) = stem.split_once('-')?;
    let digits = time.len() == TIME_DIGITS && time.bytes().all(|b| b.is_ascii_digit());
    let hex = id.len() == ID_DIGITS && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    if digits && hex { time.parse().ok() } else { None }
}

/// One change on the shared journal.
#[derive(Debug, Clone, PartialEq)]
pub struct Event {
    pub id: String,
    pub kind: String,
    pub uri: String,
    pub detail: String,
    pub host: String,
    pub time: f64,
}

impl Event {
    /// The change as this machine's journal would hold it, of the project
    /// keyed `project` (the shared journal is the project's own, so its
    /// lines name none).
    pub fn change(&self, project: &str) -> Change {
        Change {
            project: project.to_owned(),
            kind: self.kind.clone(),
            uri: self.uri.clone(),
            detail: self.detail.clone(),
            pid: 0,
            time: self.time,
            id: self.id.clone(),
        }
    }
}

/// The change a file holds; None for anything else (garbage, a torn
/// write by something other than a writer, another kind of JSON).
pub fn parse(bytes: &[u8]) -> Option<Event> {
    let value: Value = serde_json::from_slice(bytes).ok()?;
    let text = |key: &str| value.get(key)?.as_str().map(str::to_owned);
    Some(Event {
        id: text("id")?,
        kind: text("kind")?,
        uri: text("uri")?,
        detail: text("detail")?,
        host: text("host").unwrap_or_default(),
        time: value.get("time")?.as_f64()?,
    })
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)
}

/// Changes older than this (ms, by their name) are pruned.
pub const MAX_AGE_MS: u64 = 60 * 60 * 1000;
/// At most this many changes are kept; the oldest go first.
pub const MAX_FILES: usize = 1000;

/// A change's file name: `<13 digits of ms>-<id>.json`.
pub fn event_name(ms: u64, id: &str) -> String {
    format!("{ms:0TIME_DIGITS$}-{id}.json")
}

/// The name a change is written under before it is renamed into place.
pub fn temp_name(ms: u64, id: &str) -> String {
    format!(".{ms:0TIME_DIGITS$}-{id}.tmp")
}

/// The milliseconds a temporary file's name holds; None for any other name.
fn temp_time(name: &str) -> Option<u64> {
    name.strip_prefix('.')?.strip_suffix(".tmp").and_then(|stem| name_time(&format!("{stem}.json")))
}

/// The file content of `event`, as Python's `encode` writes it: one JSON
/// object and a newline.
pub fn encode(event: &Event) -> Vec<u8> {
    let mut object = serde_json::Map::new();
    object.insert("id".into(), Value::from(event.id.as_str()));
    object.insert("kind".into(), Value::from(event.kind.as_str()));
    object.insert("uri".into(), Value::from(event.uri.as_str()));
    object.insert("detail".into(), Value::from(event.detail.as_str()));
    object.insert("host".into(), Value::from(event.host.as_str()));
    object.insert("time".into(), Value::from(event.time));
    let mut bytes = Value::Object(object).to_string().into_bytes();
    bytes.push(b'\n');
    bytes
}

/// A new change id: 32 lower-case hex digits, as Python's `uuid4().hex`.
pub fn new_id() -> String {
    use std::hash::{BuildHasher, Hasher};
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNT: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos());
    let count = COUNT.fetch_add(1, Ordering::Relaxed);
    // RandomState is seeded per process from the OS; two halves, two hashers.
    let half = |salt: u64| {
        let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
        hasher.write_u128(nanos);
        hasher.write_u32(std::process::id());
        hasher.write_u64(count);
        hasher.write_u64(salt);
        hasher.finish()
    };
    format!("{:016x}{:016x}", half(1), half(2))
}

/// This machine's name, for the `host` of the changes it writes ('' unknown).
pub fn host() -> String {
    ["COMPUTERNAME", "HOSTNAME"].iter().find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty())).unwrap_or_default()
}

/// Write `event` into `dir` (made if missing) at `now` (ms) and prune it.
/// Written under [`temp_name`] and renamed into place: a reader on any
/// machine sees no file, or all of it. Errors are the caller's;
/// [`announce`] is the one that must not fail.
pub fn write(dir: &Path, event: &Event, now: u64) -> std::io::Result<PathBuf> {
    use std::io::Write;
    std::fs::create_dir_all(dir)?;
    let temp = dir.join(temp_name(now, &event.id));
    let path = dir.join(event_name(now, &event.id));
    let written = (|| {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temp)?;
        file.write_all(&encode(event))?;
        drop(file);
        std::fs::rename(&temp, &path)
    })();
    if let Err(error) = written {
        let _ = std::fs::remove_file(&temp);
        return Err(error);
    }
    prune(dir, now, MAX_AGE_MS, MAX_FILES);
    Ok(path)
}

/// Remove changes and temporary files older than `max_age` ms before
/// `now`, then the oldest changes past `max_files`. How many went. Never
/// fails: another machine may be pruning the same files.
pub fn prune(dir: &Path, now: u64, max_age: u64, max_files: usize) -> usize {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return 0;
    };
    let cutoff = now.saturating_sub(max_age);
    let mut doomed = Vec::new();
    let mut kept = Vec::new();
    for name in entries.filter_map(|e| e.ok()?.file_name().into_string().ok()) {
        let (event, time) = match (name_time(&name), temp_time(&name)) {
            (Some(t), _) => (true, t),
            (None, Some(t)) => (false, t),
            (None, None) => continue,
        };
        if time < cutoff {
            doomed.push(name);
        } else if event {
            kept.push(name);
        }
    }
    kept.sort();
    let over = kept.len().saturating_sub(max_files);
    doomed.extend(kept.into_iter().take(over));
    doomed.iter().filter(|name| std::fs::remove_file(dir.join(name)).is_ok()).count()
}

/// Tell every machine's editor that `uri`'s `detail` department has new
/// review media, under `id` (the id of the same change on this machine's
/// journal). Blocks on the share: call it off the UI thread. Never fails:
/// a change that cannot be announced only shows after Refresh.
pub fn announce(config: &Path, kind: &str, uri: &str, detail: &str, id: &str) {
    let event = Event {
        id: id.to_owned(),
        kind: kind.into(),
        uri: uri.into(),
        detail: detail.into(),
        host: host(),
        time: SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64()),
    };
    if let Err(error) = write(&shared_dir(config), &event, now_ms()) {
        eprintln!("Could not announce a {kind} change of {uri:?} on the shared journal: {error}");
    }
}

/// Follows a project's shared journal.
///
/// [`poll`](Self::poll) returns the changes that appeared since the last
/// poll, oldest first. The first poll that reaches the folder passes over
/// the changes named before the reader was made (history is not replayed)
/// and returns the rest.
#[derive(Debug, Clone)]
pub struct SharedReader {
    dir: PathBuf,
    /// Changes named before this (ms) were there before the reader.
    since: u64,
    /// The change files read (or gone), of those last listed.
    seen: HashSet<String>,
    started: bool,
}

impl SharedReader {
    pub fn new(dir: PathBuf, since: u64) -> Self {
        Self { dir, since, seen: HashSet::new(), started: false }
    }

    /// A reader of the shared journal of the project whose config folder is
    /// `config`, from now on.
    pub fn starting_now(config: &Path) -> Self {
        Self::new(shared_dir(config), now_ms())
    }

    /// The changes that appeared since the last poll. Blocks on the share;
    /// never fails.
    pub fn poll(&mut self) -> Vec<Event> {
        let listing = std::fs::read_dir(&self.dir)
            .map(|entries| entries.filter_map(|e| e.ok()?.file_name().into_string().ok()).collect::<Vec<_>>())
            .ok();
        let dir = self.dir.clone();
        self.step(listing, |name| std::fs::read(dir.join(name)))
    }

    /// [`poll`](Self::poll) given the folder's `listing` (None: it could not
    /// be listed) and how to `read` a file of it.
    pub fn step(&mut self, listing: Option<Vec<String>>, mut read: impl FnMut(&str) -> std::io::Result<Vec<u8>>) -> Vec<Event> {
        let Some(listing) = listing else {
            return Vec::new();
        };
        // Names sort in time order: 13 digits of milliseconds first.
        let names: BTreeSet<String> = listing.into_iter().filter(|n| name_time(n).is_some()).collect();
        if !self.started {
            self.started = true;
            let since = self.since;
            self.seen.extend(names.iter().filter(|n| name_time(n).is_some_and(|t| t < since)).cloned());
        }
        let mut found = Vec::new();
        for name in &names {
            if self.seen.contains(name) {
                continue;
            }
            match read(name) {
                Ok(bytes) => {
                    self.seen.insert(name.clone());
                    found.extend(parse(&bytes).filter(|e| KINDS.contains(&e.kind.as_str())));
                }
                // Pruned between the listing and now.
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                    self.seen.insert(name.clone());
                }
                Err(_) => {} // held or slow: read it next time
            }
        }
        // What is gone was pruned and cannot come back under its name.
        self.seen.retain(|n| names.contains(n));
        found
    }
}

/// The change ids let through lately, to let each through once whichever
/// journal brings it first. An empty id (a change on one journal only)
/// always passes.
#[derive(Debug, Clone)]
pub struct RecentIds {
    order: VecDeque<String>,
    set: HashSet<String>,
    cap: usize,
}

impl Default for RecentIds {
    fn default() -> Self {
        Self::new(4096)
    }
}

impl RecentIds {
    pub fn new(cap: usize) -> Self {
        Self { order: VecDeque::new(), set: HashSet::new(), cap: cap.max(1) }
    }

    /// Whether `id` is new (and remember it).
    pub fn first(&mut self, id: &str) -> bool {
        if id.is_empty() {
            return true;
        }
        if !self.set.insert(id.to_owned()) {
            return false;
        }
        self.order.push_back(id.to_owned());
        while self.order.len() > self.cap {
            if let Some(old) = self.order.pop_front() {
                self.set.remove(&old);
            }
        }
        true
    }

    /// `changes` without those already let through.
    pub fn filter(&mut self, changes: Vec<Change>) -> Vec<Change> {
        changes.into_iter().filter(|c| self.first(&c.id)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::collections::BTreeMap;

    fn id(i: usize) -> String {
        format!("{i:032x}")
    }

    fn name(ms: u64, i: usize) -> String {
        format!("{ms:013}-{}.json", id(i))
    }

    fn event(i: usize, kind: &str) -> Event {
        Event {
            id: id(i),
            kind: kind.into(),
            uri: format!("entity:/shots/SQ010/SH{i:03}"),
            detail: "lighting".into(),
            host: "farm01".into(),
            time: i as f64,
        }
    }

    /// A change file as Python's `encode` writes it.
    fn encode(e: &Event) -> Vec<u8> {
        let mut bytes = serde_json::json!({"id": e.id, "kind": e.kind, "uri": e.uri, "detail": e.detail, "host": e.host, "time": e.time})
            .to_string()
            .into_bytes();
        bytes.push(b'\n');
        bytes
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(256))]

        /// Whatever text a change holds, it reads back as written.
        #[test]
        fn a_change_reads_back(
            i in 0usize..1000,
            uri in "[a-zA-Z0-9:/_ \u{e6}\u{f8}\"\\\\]{0,24}",
            detail in "[a-z\u{2603} ]{0,12}",
            host in "[a-zA-Z0-9.-]{0,12}",
            time in prop_oneof![Just(0.0), 0.0f64..2e9],
            render in any::<bool>(),
        ) {
            let e = Event { uri, detail, host, time, ..event(i, if render { events::RENDER } else { events::PLAYBLAST }) };
            prop_assert_eq!(parse(&encode(&e)), Some(e));
        }

        /// No bytes make the parser panic; a change cut short anywhere
        /// (before its closing brace) is no change.
        #[test]
        fn garbage_and_torn_changes_are_none(bytes in prop::collection::vec(any::<u8>(), 0..64), cut in 0usize..200) {
            let _ = parse(&bytes);
            let whole = encode(&event(3, events::PLAYBLAST));
            let close = whole.iter().rposition(|&b| b == b'}').unwrap();
            prop_assert_eq!(parse(&whole[..cut.min(close)]), None);
        }

        /// Only `<13 digits>-<32 lower hex>.json` names a change, and its
        /// time is the digits.
        #[test]
        fn names_are_exact(ms in 0u64..9_999_999_999_999, i in 0usize..1000, junk in "[.a-zA-Z0-9-]{0,8}") {
            prop_assert_eq!(name_time(&name(ms, i)), Some(ms));
            let temp = format!(".{ms:013}-{}.tmp", id(i));
            prop_assert_eq!(name_time(&temp), None);
            prop_assert_eq!(name_time(&format!("{}{junk}x", name(ms, i))), None);
            prop_assert_eq!(name_time(&name(ms, i).to_uppercase()), None);
            // Upper-case hex: Python writes lower case only.
            prop_assert_eq!(name_time(&format!("{ms:013}-{}.json", id(i | 0xa).to_uppercase())), None);
            prop_assert_eq!(name_time(&format!("{:012}-{}.json", ms % 1_000_000_000_000, id(i))), None);
        }
    }

    /// What happens to the folder between two polls.
    #[derive(Debug, Clone)]
    enum Step {
        /// A writer renames a change into place (a render or playblast, or
        /// another kind no editor takes).
        Write {
            render: bool,
            other_kind: bool,
        },
        /// Something not a change: a file being written, garbage under a
        /// change's name, a stray file.
        Temp,
        Garbage,
        Stray,
        /// A writer prunes the oldest files.
        Prune(usize),
        /// The share does not answer this poll.
        Unreachable,
        /// A change is written that cannot be read this poll (held,
        /// slow), nor can the oldest unread one.
        Held,
    }

    fn step() -> impl Strategy<Value = Step> {
        prop_oneof![
            6 => (any::<bool>(), prop::bool::weighted(0.15)).prop_map(|(render, other_kind)| Step::Write { render, other_kind }),
            1 => Just(Step::Temp),
            1 => Just(Step::Garbage),
            1 => Just(Step::Stray),
            1 => (1usize..4).prop_map(Step::Prune),
            1 => Just(Step::Unreachable),
            1 => Just(Step::Held),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(128))]

        /// A reader returns each render or playblast written after it was
        /// made exactly once, oldest first, and nothing written before;
        /// garbage, files being written, other kinds and stray files never;
        /// a poll the share does not answer, or a file it cannot read yet,
        /// only defers what it would have returned (unless pruned meanwhile).
        #[test]
        fn each_new_change_is_returned_once(before in 0usize..4, steps in prop::collection::vec(step(), 1..14), unreachable_first in any::<bool>()) {
            let since = 1_000_000u64;
            let mut files: BTreeMap<String, Vec<u8>> = BTreeMap::new();
            let mut next = 0usize;
            let mut clock = since - 10;
            for _ in 0..before {
                clock += 1;
                files.insert(name(clock, next), encode(&event(next, events::PLAYBLAST)));
                next += 1;
            }
            clock = since;
            let mut reader = SharedReader::new(PathBuf::new(), since);
            if unreachable_first {
                prop_assert_eq!(reader.step(None, |_| unreachable!()), vec![]);
            }
            let mut pending: Vec<(String, Event)> = Vec::new(); // expected, not yet returned
            let mut got_all = Vec::new();
            for step in steps {
                let mut reachable = true;
                let mut held: Option<String> = None;
                match step {
                    Step::Write { render, other_kind } => {
                        clock += 1;
                        let kind = if other_kind { events::WORKFILE } else if render { events::RENDER } else { events::PLAYBLAST };
                        let e = event(next, kind);
                        let n = name(clock, next);
                        next += 1;
                        files.insert(n.clone(), encode(&e));
                        if !other_kind {
                            pending.push((n, e));
                        }
                    }
                    Step::Temp => {
                        clock += 1;
                        files.insert(format!(".{clock:013}-{}.tmp", id(next)), b"{\"id\": \"half".to_vec());
                        next += 1;
                    }
                    Step::Garbage => {
                        clock += 1;
                        files.insert(name(clock, next), b"\x00not json{".to_vec());
                        next += 1;
                    }
                    Step::Stray => {
                        files.insert(format!("notes{next}.txt"), encode(&event(next, events::RENDER)));
                        next += 1;
                    }
                    Step::Prune(n) => {
                        let doomed: Vec<String> = files.keys().filter(|k| name_time(k).is_some()).take(n).cloned().collect();
                        for d in doomed {
                            files.remove(&d);
                            pending.retain(|(p, _)| *p != d);
                        }
                    }
                    Step::Unreachable => reachable = false,
                    Step::Held => {
                        // A change written now that cannot be read this
                        // poll; the oldest unread one when there is any.
                        clock += 1;
                        let (n, e) = (name(clock, next), event(next, events::RENDER));
                        next += 1;
                        files.insert(n.clone(), encode(&e));
                        pending.push((n, e));
                        held = pending.first().map(|(n, _)| n.clone());
                    }
                }
                let listing = reachable.then(|| files.keys().cloned().collect::<Vec<_>>());
                let got = reader.step(listing.clone(), |n| {
                    if held.as_deref() == Some(n) {
                        return Err(std::io::Error::other("held"));
                    }
                    files.get(n).cloned().ok_or_else(|| std::io::Error::from(std::io::ErrorKind::NotFound))
                });
                let expected: Vec<Event> = if listing.is_none() {
                    Vec::new()
                } else {
                    let (now, later): (Vec<_>, Vec<_>) = pending.drain(..).partition(|(n, _)| held.as_deref() != Some(n.as_str()));
                    pending = later;
                    now.into_iter().map(|(_, e)| e).collect()
                };
                prop_assert_eq!(&got, &expected);
                got_all.extend(got);
            }
            let ids: HashSet<&str> = got_all.iter().map(|e| e.id.as_str()).collect();
            prop_assert_eq!(ids.len(), got_all.len(), "none twice");
            prop_assert!(got_all.iter().all(|e| e.id.as_str() >= id(before).as_str()), "none from before the reader");
        }

        /// Each id passes once however the two journals interleave it;
        /// changes without an id always pass.
        #[test]
        fn each_id_passes_once(batches in prop::collection::vec(prop::collection::vec((0usize..12, any::<bool>()), 0..6), 0..8)) {
            let mut recent = RecentIds::default();
            let mut passed: Vec<String> = Vec::new();
            let mut anonymous = 0usize;
            let mut sent_anonymous = 0usize;
            for batch in batches {
                let changes: Vec<Change> = batch
                    .iter()
                    .map(|(i, named)| Change { id: if *named { id(*i) } else { String::new() }, ..Default::default() })
                    .collect();
                sent_anonymous += changes.iter().filter(|c| c.id.is_empty()).count();
                for c in recent.filter(changes) {
                    if c.id.is_empty() { anonymous += 1 } else { passed.push(c.id) }
                }
            }
            let unique: HashSet<&String> = passed.iter().collect();
            prop_assert_eq!(unique.len(), passed.len());
            prop_assert_eq!(anonymous, sent_anonymous);
        }
    }

    /// Past its cap the oldest ids are forgotten, not the newest.
    #[test]
    fn recent_ids_forget_the_oldest() {
        let mut recent = RecentIds::new(3);
        for i in 0..5 {
            assert!(recent.first(&id(i)));
        }
        assert!(!recent.first(&id(4)));
        assert!(recent.first(&id(0)), "forgotten past the cap");
    }

    /// The real folder: a missing one is no changes, and once made the
    /// reader picks up what is written there.
    #[test]
    fn a_missing_folder_is_no_changes_until_it_exists() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("_config");
        let mut reader = SharedReader::new(shared_dir(&config), 0);
        assert_eq!(reader.poll(), vec![]);
        std::fs::create_dir_all(shared_dir(&config)).unwrap();
        let e = event(1, events::RENDER);
        std::fs::write(shared_dir(&config).join(name(5, 1)), encode(&e)).unwrap();
        assert_eq!(reader.poll(), vec![e]);
        assert_eq!(reader.poll(), vec![]);
    }

    fn listing(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(dir).unwrap().filter_map(|e| e.ok()?.file_name().into_string().ok()).collect();
        names.sort();
        names
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(64))]

        /// What the writer writes the reader reads back, whole, under its
        /// time and id, in the order written, as Python's encoding would
        /// spell it; nothing is left under a temporary name.
        #[test]
        fn written_changes_read_back(
            changes in prop::collection::vec(("[a-zA-Z0-9:/_ \u{e6}\u{2603}\"\\\\]{0,16}", "[a-z ]{0,8}", 0.0f64..2e9, any::<bool>(), 1u64..5000), 1..6),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let folder = dir.path().join("_config").join(DIR_NAME);
            let mut reader = SharedReader::new(folder.clone(), 0);
            let mut now = 1_750_000_000_000u64;
            let mut written = Vec::new();
            for (uri, detail, time, render, step) in changes {
                now += step;
                let e = Event { id: new_id(), kind: (if render { events::RENDER } else { events::PLAYBLAST }).into(), uri, detail, host: host(), time };
                let path = write(&folder, &e, now).unwrap();
                prop_assert_eq!(path.file_name().unwrap().to_string_lossy().into_owned(), event_name(now, &e.id));
                prop_assert_eq!(name_time(&event_name(now, &e.id)), Some(now));
                prop_assert_eq!(parse(&std::fs::read(&path).unwrap()), parse(&super::encode(&e)));
                prop_assert_eq!(super::encode(&e), encode(&e), "as Python spells it");
                written.push(e);
            }
            prop_assert_eq!(reader.poll(), written);
            prop_assert!(listing(&folder).iter().all(|n| name_time(n).is_some()), "no temporary file left");
        }

        /// Every write prunes: nothing older than the age (stale temporary
        /// files included), at most the cap of changes, the newest kept,
        /// files not the journal's untouched.
        #[test]
        fn prune_keeps_the_newest_young_changes(
            ages in prop::collection::vec((0u64..300_000, any::<bool>()), 0..12),
            max_files in 0usize..6,
        ) {
            let dir = tempfile::tempdir().unwrap();
            let now = 1_750_000_000_000u64;
            let max_age = 100_000u64;
            std::fs::write(dir.path().join("notes.txt"), b"not ours").unwrap();
            let mut model: Vec<(u64, String)> = Vec::new();
            for (i, (age, temp)) in ages.into_iter().enumerate() {
                let n = if temp { temp_name(now - age, &id(i)) } else { event_name(now - age, &id(i)) };
                std::fs::write(dir.path().join(&n), b"{}").unwrap();
                model.push((now - age, n));
            }
            prune(dir.path(), now, max_age, max_files);
            model.retain(|(t, _)| *t >= now - max_age);
            let mut changes: Vec<&(u64, String)> = model.iter().filter(|(_, n)| name_time(n).is_some()).collect();
            changes.sort_by(|a, b| a.1.cmp(&b.1));
            let doomed: HashSet<String> = changes.iter().take(changes.len().saturating_sub(max_files)).map(|(_, n)| n.clone()).collect();
            let mut want: Vec<String> = model.iter().filter(|(_, n)| !doomed.contains(n)).map(|(_, n)| n.clone()).collect();
            want.push("notes.txt".into());
            want.sort();
            prop_assert_eq!(listing(dir.path()), want);
        }
    }

    /// A change is written under its temporary name first: when that
    /// cannot be made, no change appears and the write says so.
    #[test]
    fn a_write_goes_through_its_temporary_name() {
        let dir = tempfile::tempdir().unwrap();
        let e = event(1, events::PLAYBLAST);
        std::fs::create_dir(dir.path().join(temp_name(7, &e.id))).unwrap();
        assert!(write(dir.path(), &e, 7).is_err());
        assert!(!dir.path().join(event_name(7, &e.id)).exists(), "no change without its temporary file");
    }

    /// A write prunes: a change older than the age goes with the next write.
    #[test]
    fn a_write_prunes_what_is_older_than_the_age() {
        let dir = tempfile::tempdir().unwrap();
        let (old, new) = (event(1, events::RENDER), event(2, events::RENDER));
        write(dir.path(), &old, 1_000).unwrap();
        write(dir.path(), &new, 1_000 + MAX_AGE_MS + 1).unwrap();
        assert_eq!(listing(dir.path()), vec![event_name(1_000 + MAX_AGE_MS + 1, &new.id)]);
    }

    /// Ids are 32 lower-case hex digits, as Python's, and differ.
    #[test]
    fn ids_are_python_shaped_and_unique() {
        let ids: HashSet<String> = (0..2000).map(|_| new_id()).collect();
        assert_eq!(ids.len(), 2000);
        assert!(ids.iter().all(|i| name_time(&event_name(1, i)).is_some()), "{:?}", ids.iter().next());
    }

    /// A share that cannot be written is no panic, and leaves nothing.
    #[test]
    fn announcing_where_nothing_can_be_written_never_fails() {
        let dir = tempfile::tempdir().unwrap();
        let blocker = dir.path().join("not-a-folder");
        std::fs::write(&blocker, b"x").unwrap();
        announce(&blocker, events::PLAYBLAST, "entity:/shots/SQ010/SH010", "animation", &new_id());
        assert_eq!(prune(&blocker.join("missing"), 0, 0, 0), 0);
        assert_eq!(listing(dir.path()), vec!["not-a-folder".to_string()]);
    }
}
