//! Preview frames rendered on this machine, as the local farm's runner
//! records them (`tumblepipe.farm.local.previews`, #75).
//!
//! A render or comp submitted with Preview Frames to the local farm renders
//! a few frames first; once they are on disk the runner records them in
//! the batch's `state.json` under `previews`, one entry per partial notify
//! job (its index as the key): entity, department, version, frame pattern
//! and frames. Python writes it; this reads it as Python's
//! `batch_previews` does, and `tests/test_previews_parity.py` holds the two
//! to each other through `examples/pipe_lines.rs`.
//!
//! The reading rules, the same on both sides:
//! - a job key is ASCII digits with an optional leading `-` that fit an
//!   i64 (`3`, `03`, `-1`); any other key (`--3`, `+3`, `²`, a number past
//!   i64) is passed over;
//! - an entry is a preview when it is an object with a text `frame_path`;
//!   its `entity`, `department` and `version` are their text, '' for
//!   anything else (a number is not a name); its `frames` are kept when
//!   every one is a whole number within i64, else read as none;
//! - two keys naming the same job (`3` and `03`): the one later in the
//!   object wins, a key written twice counting at its first place with its
//!   last value, as both JSON readers keep it (Python's dict, serde's map
//!   in file order through th_pipe's `preserve_order`); an entry that is
//!   not a preview displaces nothing;
//! - a batch's `version` must be the whole number 1 (not `true`, `1.0` or
//!   `"1"`), else the batch is refused.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::Value;

/// The `state.json` key the runner records ready previews under.
pub const STATE_KEY: &str = "previews";
/// The batch's own description, written once at submit.
pub const BATCH_NAME: &str = "batch.json";
/// The runner's record of how far a batch got.
pub const STATE_NAME: &str = "state.json";
/// The batch layout this reads (`queue.BATCH_VERSION`).
pub const BATCH_VERSION: i64 = 1;
/// A frame pattern's frame number.
pub const FRAME_TOKEN: &str = "####";
/// Names the local farm's folder when set (`queue.ROOT_ENV`).
pub const ROOT_ENV: &str = "TH_LOCAL_FARM_DIR";

/// One partial notify job's preview frames, as the runner recorded them.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Preview {
    /// The notify job's index in its batch.
    pub job: i64,
    /// The entity URI; '' when the job's message did not name it.
    pub entity: String,
    pub department: String,
    pub version: String,
    /// The frame pattern, `####` for the frame number.
    pub frame_path: String,
    pub frames: Vec<i64>,
}

/// A text field of an entry: '' for anything but text.
fn text_of(entry: &serde_json::Map<String, Value>, key: &str) -> String {
    entry.get(key).and_then(Value::as_str).unwrap_or_default().to_owned()
}

/// A recorded entry as a [`Preview`]; None when it is not one.
pub fn entry_preview(job: i64, entry: &Value) -> Option<Preview> {
    let entry = entry.as_object()?;
    let frame_path = entry.get("frame_path")?.as_str()?.to_owned();
    let frames = entry
        .get("frames")
        .and_then(Value::as_array)
        .and_then(|frames| frames.iter().map(Value::as_i64).collect::<Option<Vec<i64>>>())
        .unwrap_or_default();
    Some(Preview {
        job,
        entity: text_of(entry, "entity"),
        department: text_of(entry, "department"),
        version: text_of(entry, "version"),
        frame_path,
        frames,
    })
}

/// A job index key: ASCII digits after at most one `-`, within i64.
pub fn job_key(key: &str) -> Option<i64> {
    let digits = key.strip_prefix('-').unwrap_or(key);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    key.parse().ok()
}

/// The previews recorded in a batch's `state`, in job order.
pub fn recorded(state: &Value) -> Vec<Preview> {
    let Some(entries) = state.get(STATE_KEY).and_then(Value::as_object) else { return Vec::new() };
    let mut by_job: BTreeMap<i64, Preview> = BTreeMap::new();
    for (key, entry) in entries {
        if let Some(preview) = job_key(key).and_then(|job| entry_preview(job, entry)) {
            by_job.insert(preview.job, preview);
        }
    }
    by_job.into_values().collect()
}

fn read_json(path: &Path) -> Result<Option<Value>, String> {
    match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|e| format!("{}: {e}", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

/// Whether `folder` is a local farm batch: it holds a `batch.json`.
pub fn is_batch(folder: &Path) -> bool {
    folder.join(BATCH_NAME).is_file()
}

/// The ready previews of the local batch in `folder`, in job order: none
/// while the runner has recorded none (or has not written its state yet).
/// Refused: no `batch.json`, or one of another layout (its `version` not
/// the whole number [`BATCH_VERSION`]).
pub fn batch_previews(folder: &Path) -> Result<Vec<Preview>, String> {
    let batch = read_json(&folder.join(BATCH_NAME))?.ok_or_else(|| format!("no {BATCH_NAME} in {}", folder.display()))?;
    let version = batch.get("version").and_then(Value::as_i64);
    if version != Some(BATCH_VERSION) {
        return Err(format!("{} is a version {version:?} batch; this reads version {BATCH_VERSION}", folder.display()));
    }
    Ok(read_json(&folder.join(STATE_NAME))?.map(|state| recorded(&state)).unwrap_or_default())
}

/// `pattern` with the [`FRAME_TOKEN`] in its file name replaced by `frame`,
/// four digits (Python's `frames.frame_file`); None when its name has no
/// token. The name is the path's last component, as Python's `Path.name`.
pub fn frame_file(pattern: &str, frame: i64) -> Option<PathBuf> {
    let path = Path::new(pattern);
    let name = path.file_name()?.to_str()?;
    name.contains(FRAME_TOKEN).then(|| path.with_file_name(name.replace(FRAME_TOKEN, &format!("{frame:04}"))))
}

impl Preview {
    /// The entity as an artist names it: `entity:/shots/010/0100` is `010/0100`.
    pub fn entity_label(&self) -> &str {
        ["shots", "assets", "groups", "edits"]
            .iter()
            .find_map(|kind| self.entity.strip_prefix("entity:/")?.strip_prefix(kind)?.strip_prefix('/').filter(|rest| !rest.is_empty()))
            .unwrap_or(&self.entity)
    }

    /// `010/0100 denoise v0003: 3 frames` (Python's `summary`).
    pub fn summary(&self) -> String {
        let parts: Vec<&str> =
            [self.entity_label(), self.department.as_str(), self.version.as_str()].into_iter().filter(|p| !p.is_empty()).collect();
        let what = if parts.is_empty() { "Preview".to_owned() } else { parts.join(" ") };
        let count = self.frames.len();
        format!("{what}: {count} frame{}", if count == 1 { "" } else { "s" })
    }

    /// Its frames' numbers and files, in the order they were rendered.
    pub fn frame_paths(&self) -> Vec<(i64, PathBuf)> {
        self.frames.iter().filter_map(|&frame| frame_file(&self.frame_path, frame).map(|path| (frame, path))).collect()
    }
}

/// The local farm's folder (Python's `queue.root`): `$TH_LOCAL_FARM_DIR`,
/// else the platform's (`win32`, `darwin`, anything else Linux's) under
/// `home`, this user's home. `var` reads the environment; '' is unset.
pub fn farm_root(var: impl Fn(&str) -> Option<String>, platform: &str, home: &Path) -> PathBuf {
    let set = |name: &str| var(name).filter(|v| !v.is_empty());
    if let Some(root) = set(ROOT_ENV) {
        return PathBuf::from(root);
    }
    if platform == "win32" {
        let base = set("LOCALAPPDATA").map_or_else(|| home.join("AppData").join("Local"), PathBuf::from);
        base.join("TumblePipe").join("local_farm")
    } else if platform == "darwin" {
        home.join("Library").join("Application Support").join("TumblePipe").join("local_farm")
    } else {
        let base = set("XDG_DATA_HOME").map_or_else(|| home.join(".local").join("share"), PathBuf::from);
        base.join("tumblepipe").join("local_farm")
    }
}

/// This process's [`farm_root`].
pub fn this_farm_root() -> PathBuf {
    let platform = if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    };
    farm_root(|name| std::env::var(name).ok(), platform, &std::env::home_dir().unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    fn preview(job: i64, entity: &str) -> Preview {
        Preview {
            job,
            entity: entity.into(),
            department: "lighting".into(),
            version: "v0002".into(),
            frame_path: "P:/r/beauty.####.exr".into(),
            frames: vec![1001, 1050, 1100],
        }
    }

    fn entry_json(p: &Preview) -> Value {
        json!({"ready": "now", "entity": p.entity, "department": p.department, "version": p.version,
               "frame_path": p.frame_path, "frames": p.frames, "notified": true, "error": ""})
    }

    #[test]
    fn job_keys_are_ascii_whole_numbers_within_i64() {
        assert_eq!(job_key("3"), Some(3));
        assert_eq!(job_key("03"), Some(3));
        assert_eq!(job_key("-3"), Some(-3));
        for bad in ["", "-", "--3", "+3", "²", "٣", "3 ", "99999999999999999999"] {
            assert_eq!(job_key(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn frame_files_replace_the_token_in_the_name_only() {
        assert_eq!(frame_file("P:/r/beauty.####.exr", 1001), Some(PathBuf::from("P:/r/beauty.1001.exr")));
        assert_eq!(frame_file("P:/####/beauty.####.exr", 7), Some(PathBuf::from("P:/####/beauty.0007.exr")));
        assert_eq!(frame_file("P:/r/b.####.exr", -5), Some(PathBuf::from("P:/r/b.-005.exr")));
        assert_eq!(frame_file("P:/####/beauty.exr", 7), None);
        assert_eq!(frame_file("", 7), None);
    }

    #[test]
    fn entries_read_as_python_now_reads_them() {
        assert_eq!(entry_preview(1, &json!({"frames": [1]})), None);
        assert_eq!(entry_preview(1, &json!({"frame_path": 3})), None);
        let p = entry_preview(1, &json!({"frame_path": "a.####.exr", "frames": [1, 2.5], "entity": 5, "version": null})).unwrap();
        assert!(p.frames.is_empty(), "frames that are not all whole numbers read as none");
        assert_eq!((p.entity.as_str(), p.version.as_str()), ("", ""), "a number is not a name");
        let p = entry_preview(1, &json!({"frame_path": "a", "frames": [1, 18446744073709551615u64]})).unwrap();
        assert!(p.frames.is_empty(), "a frame past i64");
    }

    #[test]
    fn of_two_keys_for_one_job_the_later_preview_wins() {
        let text = r#"{"previews": {"03": {"frame_path": "a"}, "3": {"frame_path": "b"}, "4": {"frame_path": "c"}, "04": 7}}"#;
        let read = recorded(&serde_json::from_str(text).unwrap());
        assert_eq!(read.iter().map(|p| (p.job, p.frame_path.as_str())).collect::<Vec<_>>(), [(3, "b"), (4, "c")]);
    }

    #[test]
    fn a_batch_folder_reads_its_ready_previews() {
        let dir = tempfile::tempdir().unwrap();
        assert!(batch_previews(dir.path()).is_err(), "no batch.json");
        std::fs::write(dir.path().join(BATCH_NAME), br#"{"version": 1, "jobs": []}"#).unwrap();
        assert_eq!(batch_previews(dir.path()).unwrap(), []);
        let a = preview(4, "entity:/shots/010/0100");
        std::fs::write(dir.path().join(STATE_NAME), serde_json::to_vec(&json!({STATE_KEY: {"4": entry_json(&a)}})).unwrap()).unwrap();
        assert_eq!(batch_previews(dir.path()).unwrap(), [a]);
        for version in ["2", "true", "1.0", "\"1\""] {
            std::fs::write(dir.path().join(BATCH_NAME), format!(r#"{{"version": {version}, "jobs": []}}"#)).unwrap();
            assert!(batch_previews(dir.path()).is_err(), "version {version}");
        }
    }

    #[test]
    fn the_farm_root_is_the_variable_else_the_platform_s() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |key: &str| pairs.iter().find(|(k, _)| *k == key).map(|(_, v)| (*v).to_owned())
        };
        let home = Path::new("H:/me");
        assert_eq!(farm_root(env(&[(ROOT_ENV, "D:/farm")]), "linux", home), PathBuf::from("D:/farm"));
        let all: &'static [(&str, &str)] = &[(ROOT_ENV, ""), ("LOCALAPPDATA", "L:/app"), ("XDG_DATA_HOME", "X:/d")];
        assert_eq!(farm_root(env(all), "win32", home), Path::new("L:/app").join("TumblePipe").join("local_farm"));
        assert_eq!(farm_root(env(all), "linux", home), Path::new("X:/d").join("tumblepipe").join("local_farm"));
        assert_eq!(farm_root(env(&[]), "win32", home), home.join("AppData").join("Local").join("TumblePipe").join("local_farm"));
        assert_eq!(
            farm_root(env(all), "darwin", home),
            home.join("Library").join("Application Support").join("TumblePipe").join("local_farm")
        );
    }

    #[test]
    fn summaries_read_as_python_writes_them() {
        assert_eq!(preview(3, "entity:/shots/010/0100").summary(), "010/0100 lighting v0002: 3 frames");
        let mut p = preview(3, "");
        p.department.clear();
        p.version.clear();
        p.frames = vec![1];
        assert_eq!(p.summary(), "Preview: 1 frame");
    }

    proptest! {
        /// Whatever the runner recorded reads back: every entry that is a
        /// preview, in job order, its fields as written; others left out.
        #[test]
        fn recorded_previews_read_back_in_job_order(
            jobs in prop::collection::btree_map(-3i64..60, ("[A-Za-z0-9]{1,8}", prop::collection::vec(-5i64..3000, 0..5)), 0..6),
            junk in prop::collection::vec("[a-z+]{1,3}", 0..3),
        ) {
            let mut entries = serde_json::Map::new();
            let mut wanted = Vec::new();
            for (job, (shot, frames)) in &jobs {
                let mut p = preview(*job, &format!("entity:/shots/{shot}"));
                p.frames = frames.clone();
                entries.insert(job.to_string(), entry_json(&p));
                wanted.push(p);
            }
            for key in junk {
                entries.insert(key, json!({"frame_path": "x.####.exr", "frames": [1]}));
            }
            prop_assert_eq!(recorded(&json!({STATE_KEY: entries})), wanted);
        }

        /// A preview's frame files number its frames, in its order, in its
        /// pattern's folder.
        #[test]
        fn frame_paths_number_the_frames(folder in "[A-Za-z0-9]{1,8}", stem in "[A-Za-z0-9]{1,8}", frames in prop::collection::vec(0i64..100_000, 0..8)) {
            let mut p = preview(0, "");
            p.frame_path = format!("P:/{folder}/{stem}.####.exr");
            p.frames = frames.clone();
            let paths = p.frame_paths();
            prop_assert_eq!(paths.iter().map(|(n, _)| *n).collect::<Vec<_>>(), frames);
            for (n, path) in paths {
                prop_assert_eq!(path, PathBuf::from(format!("P:/{folder}/{stem}.{n:04}.exr")));
            }
        }
    }
}
