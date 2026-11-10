//! The same-machine change journal, written as `tumblepipe.util.events`
//! writes it: an open Asset Browser follows it and refreshes for what other
//! sessions changed.
//!
//! QA 2026-11-10 (qabeta, master edit publish): the editor's config writes
//! (a master publish creating a shot or changing its range, an edit saved
//! from the editor) did not reach the journal, so the Houdini session's
//! browser went on showing the shots as they were until Refresh. Every
//! [`Config`](crate::config::Config) write now announces itself here, as
//! the Python config store's `write_root` does.
//!
//! Format (the Python module's docstring is the contract): UTF-8 JSON
//! lines in `changes.jsonl` of [`journal_dir`]; the first line of every
//! file is a header `{"journal": "<id>"}`, new whenever the file is started
//! afresh; writers hold `changes.lock` while they append or rotate; a file
//! that is missing, past [`MAX_BYTES`] or has no readable header is started
//! afresh, truncated in place.

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};

pub const JOURNAL_NAME: &str = "changes.jsonl";
pub const LOCK_NAME: &str = "changes.lock";
/// A journal past this size is started afresh by the next write.
pub const MAX_BYTES: u64 = 256 * 1024;
/// Overrides the journal folder (tests point it into their temp project).
pub const DIR_ENV: &str = "TH_EVENTS_DIR";
/// A config db file was written: `uri` is empty, `detail` the purpose.
pub const CONFIG: &str = "config";

/// The per-user folder the journal lives in, on local disk.
pub fn journal_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os(DIR_ENV).filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    let home = home_dir();
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").filter(|d| !d.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join("AppData").join("Local"))
    } else if cfg!(target_os = "macos") {
        home.join("Library").join("Application Support")
    } else {
        std::env::var_os("XDG_STATE_HOME").filter(|d| !d.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".local").join("state"))
    };
    base.join("tumblepipe").join("events")
}

fn home_dir() -> PathBuf {
    let var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    std::env::var_os(var).map(PathBuf::from).unwrap_or_default()
}

pub fn journal_path() -> PathBuf {
    journal_dir().join(JOURNAL_NAME)
}

/// How the journal names a project: its config folder spelled as Python's
/// `os.path.normcase(os.path.abspath(...))` spells it, so the browser
/// matches the line to the project it shows. No canonicalising: on a
/// mapped drive that can trade the letter for a UNC path.
pub fn project_key(config_path: &Path) -> String {
    let absolute = std::path::absolute(config_path).unwrap_or_else(|_| config_path.to_path_buf());
    if cfg!(windows) {
        // GetFullPathNameW folds `.` and `..` but keeps a trailing
        // separator, which Python's abspath then normalises away (a drive
        // root keeps its own); normcase is `/` -> `\` and lower case.
        let mut key = absolute.to_string_lossy().replace('/', "\\").to_lowercase();
        while key.ends_with('\\') && !key.ends_with(":\\") && key.len() > 1 {
            key.pop();
        }
        key
    } else {
        // posixpath.abspath is normpath(join(cwd, p)): `.` and `..` folded
        // lexically, no trailing separator.
        let mut parts: Vec<String> = Vec::new();
        for component in absolute.components() {
            match component {
                Component::ParentDir => {
                    parts.pop();
                }
                Component::Normal(part) => parts.push(part.to_string_lossy().into_owned()),
                _ => {}
            }
        }
        format!("/{}", parts.join("/"))
    }
}

/// One journal line.
#[derive(Debug, Clone, PartialEq)]
pub struct Change {
    pub project: String,
    pub kind: String,
    pub uri: String,
    pub detail: String,
    pub pid: u32,
    pub time: f64,
}

impl Change {
    fn line(&self) -> Vec<u8> {
        let mut object = Map::new();
        object.insert("project".into(), Value::from(self.project.as_str()));
        object.insert("kind".into(), Value::from(self.kind.as_str()));
        object.insert("uri".into(), Value::from(self.uri.as_str()));
        object.insert("detail".into(), Value::from(self.detail.as_str()));
        object.insert("pid".into(), Value::from(self.pid));
        object.insert("time".into(), Value::from(self.time));
        let mut line = Value::Object(object).to_string().into_bytes();
        line.push(b'\n');
        line
    }
}

fn header() -> Vec<u8> {
    static COUNT: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos() as u64);
    let id = format!("{nanos:016x}{:08x}{:08x}", std::process::id(), COUNT.fetch_add(1, Ordering::Relaxed) as u32);
    let mut object = Map::new();
    object.insert("journal".into(), Value::from(id));
    let mut line = Value::Object(object).to_string().into_bytes();
    line.push(b'\n');
    line
}

/// The header id of a journal's first line (with its newline), if it has one.
pub fn header_id(line: &[u8]) -> Option<String> {
    if !line.ends_with(b"\n") {
        return None;
    }
    let value: Value = serde_json::from_slice(line).ok()?;
    value.get("journal")?.as_str().map(str::to_owned)
}

/// `(size, header readable, last line unfinished)`; `(0, false, false)`
/// when there is no journal.
fn inspect(path: &Path) -> std::io::Result<(u64, bool, bool)> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok((0, false, false)),
        Err(e) => return Err(e),
    };
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    if bytes.is_empty() {
        return Ok((0, false, false));
    }
    let first = bytes.iter().position(|&b| b == b'\n').map_or(&bytes[..], |end| &bytes[..=end]);
    Ok((bytes.len() as u64, header_id(first).is_some(), bytes.last() != Some(&b'\n')))
}

/// Append `change` to the journal at `path`, starting it afresh when it is
/// missing, full or headerless. Errors are the caller's; [`announce`] is
/// the one that must not fail.
pub fn append(change: &Change, path: &Path) -> std::io::Result<()> {
    let folder = path.parent().unwrap_or(Path::new("."));
    std::fs::create_dir_all(folder)?;
    let line = change.line();
    // Read and write: LockFileEx refuses a handle opened only to append.
    let lock = OpenOptions::new().create(true).truncate(false).read(true).write(true).open(folder.join(LOCK_NAME))?;
    lock.lock()?;
    let result = (|| {
        let (size, whole, open_line) = inspect(path)?;
        if !whole || size + line.len() as u64 > MAX_BYTES {
            // Truncated in place, not replaced: on Windows a rename fails
            // while a reader has the file open.
            let mut file = OpenOptions::new().create(true).write(true).truncate(true).open(path)?;
            let mut bytes = header();
            bytes.extend_from_slice(&line);
            file.write_all(&bytes)
        } else {
            let mut file = OpenOptions::new().append(true).open(path)?;
            file.seek(SeekFrom::End(0))?;
            let mut bytes = if open_line { b"\n".to_vec() } else { Vec::new() };
            bytes.extend_from_slice(&line);
            file.write_all(&bytes)
        }
    })();
    let _ = lock.unlock();
    result
}

#[cfg(test)]
thread_local! {
    /// The journal this thread's announces go to in tests; none unless a
    /// test names one, so the suite never writes the user's journal.
    pub(crate) static TEST_JOURNAL: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) };
}

fn target() -> Option<PathBuf> {
    #[cfg(test)]
    return TEST_JOURNAL.with(|j| j.borrow().clone());
    #[cfg(not(test))]
    Some(journal_path())
}

/// Tell this machine's other sessions that `uri` in the project at
/// `config_path` changed. Never fails: a change that cannot be announced
/// only means the other browsers refresh when asked, as before.
pub fn announce(config_path: &Path, kind: &str, uri: &str, detail: &str) {
    let Some(journal) = target() else { return };
    let change = Change {
        project: project_key(config_path),
        kind: kind.into(),
        uri: uri.into(),
        detail: detail.into(),
        pid: std::process::id(),
        time: SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64()),
    };
    if let Err(error) = append(&change, &journal) {
        eprintln!("Could not announce a {kind} change of {uri:?}: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn change(i: usize, detail: &str) -> Change {
        Change {
            project: "p:\\show\\_config".into(),
            kind: CONFIG.into(),
            uri: String::new(),
            detail: format!("{detail}{i}"),
            pid: 7,
            time: 1.5 + i as f64,
        }
    }

    /// A line as the Python reader takes it: None for one it skips (a torn
    /// line a later append ended).
    fn parse(line: &[u8]) -> Option<Change> {
        let v: Value = serde_json::from_slice(line).ok()?;
        Some(Change {
            project: v["project"].as_str().unwrap().into(),
            kind: v["kind"].as_str().unwrap().into(),
            uri: v["uri"].as_str().unwrap().into(),
            detail: v["detail"].as_str().unwrap().into(),
            pid: v["pid"].as_u64().unwrap() as u32,
            time: v["time"].as_f64()?,
        })
    }

    /// The journal's lines after its header, as a reader takes them.
    fn lines(path: &Path) -> (Option<String>, Vec<Change>) {
        let bytes = std::fs::read(path).unwrap();
        let mut split = bytes.split_inclusive(|&b| b == b'\n');
        let id = split.next().and_then(header_id);
        (id, split.filter(|l| l.ends_with(b"\n") && l.len() > 1).filter_map(parse).collect())
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(32))]

        /// Whatever state the file is in (none, empty, headerless, a torn
        /// last line, full), an append leaves a header first and the new
        /// change whole as the last line; a file that was fine keeps its
        /// header and every change it held.
        #[test]
        fn an_append_leaves_a_readable_journal(
            state in 0usize..5,
            before in 0usize..6,
            detail in "[a-z\u{e6}\u{f8} \"\\\\]{0,12}",
        ) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("events").join(JOURNAL_NAME);
            for i in 0..before {
                append(&change(i, &detail), &path).unwrap();
            }
            let (id_before, held) = if path.exists() { lines(&path) } else { (None, vec![]) };
            match state {
                0 => {}
                1 => { let _ = std::fs::remove_file(&path); }
                2 => { std::fs::create_dir_all(path.parent().unwrap()).unwrap(); std::fs::write(&path, b"").unwrap(); }
                3 => { std::fs::create_dir_all(path.parent().unwrap()).unwrap(); std::fs::write(&path, b"{\"project\": \"x\"").unwrap(); }
                _ => {
                    if path.exists() {
                        let mut f = OpenOptions::new().append(true).open(&path).unwrap();
                        f.write_all(b"{\"project\": \"torn").unwrap();
                    }
                }
            }
            let new = change(99, &detail);
            append(&new, &path).unwrap();
            let (id, got) = lines(&path);
            prop_assert!(id.is_some(), "a header first");
            prop_assert_eq!(got.last(), Some(&new));
            if before > 0 && (state == 0 || state == 4) {
                prop_assert_eq!(id, id_before);
                prop_assert_eq!(&got[..got.len() - 1], &held[..]);
            } else {
                prop_assert_eq!(got.len(), 1, "started afresh");
            }
        }
    }

    #[test]
    fn a_full_journal_is_started_afresh_with_a_new_header() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(JOURNAL_NAME);
        append(&change(0, "x"), &path).unwrap();
        let (first, _) = lines(&path);
        let mut i = 1;
        while std::fs::metadata(&path).unwrap().len() < MAX_BYTES - 400 {
            append(&change(i, &"y".repeat(200)), &path).unwrap();
            i += 1;
        }
        for _ in 0..4 {
            append(&change(i, &"y".repeat(200)), &path).unwrap();
            i += 1;
        }
        let (id, got) = lines(&path);
        assert!(std::fs::metadata(&path).unwrap().len() <= MAX_BYTES);
        assert_ne!(id, first, "a new header");
        assert!((1..=4).contains(&got.len()), "only what came after the rotation: {}", got.len());
    }

    #[test]
    fn the_key_is_pythons_normcase_abspath() {
        if cfg!(windows) {
            assert_eq!(project_key(Path::new("P:/QaBeta/_config")), "p:\\qabeta\\_config");
            assert_eq!(project_key(Path::new("P:/qabeta/x/../_config/")), "p:\\qabeta\\_config");
            assert_eq!(project_key(Path::new("P:/")), "p:\\");
        } else {
            assert_eq!(project_key(Path::new("/mnt/Show/x/../_config/")), "/mnt/Show/_config");
        }
    }
}
