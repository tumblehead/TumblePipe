//! Reading and writing JSON as `tumblepipe.util.io` does.

use std::path::Path;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;

/// How long a rename over a file another process is reading keeps
/// retrying (`util.io.REPLACE_TIMEOUT`).
pub const REPLACE_TIMEOUT: Duration = Duration::from_secs(5);

/// The sidecar a published version folder is finished by.
pub const VERSION_MARKER: &str = "context.json";

/// Rename `src` over `dst`, waiting out a reader that has `dst` open.
///
/// On Windows a file opened without FILE_SHARE_DELETE (every plain Python
/// `open()`, most readers over SMB) cannot be renamed over until the reader
/// closes it; those reads take milliseconds. Retries with a growing
/// back-off, then gives up with the last error (`util.io.replace_file`).
pub fn replace_file(src: &Path, dst: &Path) -> std::io::Result<()> {
    replace_file_within(src, dst, REPLACE_TIMEOUT)
}

/// [`replace_file`] with its own timeout.
pub fn replace_file_within(src: &Path, dst: &Path, timeout: Duration) -> std::io::Result<()> {
    let deadline = Instant::now() + timeout;
    let mut delay = Duration::from_millis(10);
    loop {
        match std::fs::rename(src, dst) {
            Ok(()) => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied && Instant::now() < deadline => {
                std::thread::sleep(delay);
                delay = (delay * 2).min(Duration::from_millis(250));
            }
            Err(error) => return Err(error),
        }
    }
}

/// `value` as Python's `json.dump(value, indent=4)` writes it: four-space
/// indent, `": "` after a key, and every character outside printable ASCII
/// escaped as `\uXXXX` (`ensure_ascii`). No trailing newline.
pub fn to_python_json(value: &Value) -> String {
    let mut buf = Vec::new();
    let formatter = PythonFormatter(serde_json::ser::PrettyFormatter::with_indent(b"    "));
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    value.serialize(&mut ser).expect("a JSON value serializes");
    String::from_utf8(buf).expect("ensure_ascii output is ASCII")
}

/// Read the JSON document at `path`.
pub fn load_json(path: &Path) -> Result<Value, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("could not read {}: {e}", path.display()))?;
    serde_json::from_slice(&bytes).map_err(|e| format!("invalid JSON in {}: {e}", path.display()))
}

/// The text [`store_json`] writes for `value`: [`to_python_json`] and a
/// final newline, LF on every platform. The bytes `util.io.store_json` and
/// the config migrator (`th_project_core::to_json_string`) write.
pub fn python_text(value: &Value) -> String {
    to_python_json(value) + "\n"
}

/// Write `value` to `path` atomically, as `util.io.store_json`: into a
/// temporary file beside it, then renamed over it ([`replace_file`]). The
/// file is either whole or untouched.
pub fn store_json(path: &Path, value: &Value) -> std::io::Result<()> {
    store_whole(path, python_text(value).as_bytes())
}

/// Write `value` to `path` as serde_json's pretty print (two-space indent,
/// no final newline), atomically as [`store_json`]: a user's preferences
/// file, which another Cinedex of theirs may be reading.
pub fn store_pretty_json(path: &Path, value: &Value) -> std::io::Result<()> {
    store_whole(path, &serde_json::to_vec_pretty(value).map_err(std::io::Error::other)?)
}

/// What `from_json` makes of the JSON at `path`; the default when there is
/// no file, or it is not JSON (a preferences file: whatever is in it, the
/// program starts).
pub fn load_or_default<T: Default>(path: &Path, from_json: impl FnOnce(&Value) -> T) -> T {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .map_or_else(T::default, |value| from_json(&value))
}

/// Write `bytes` to `path`, making its folder: into a temporary file beside
/// it, then renamed over it ([`replace_file`]), so the file is either whole
/// or untouched and no temporary file is left.
fn store_whole(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    std::fs::create_dir_all(parent)?;
    let temp = tempfile::Builder::new().prefix(".tmp").suffix(".json").tempfile_in(parent)?;
    let temp_path = temp.into_temp_path();
    std::fs::write(&temp_path, bytes)?;
    // Renamed by hand, with the retry; the TempPath deletes a leftover.
    replace_file(&temp_path, path)?;
    // The file is gone from under the TempPath now; do not let it try again.
    let _ = temp_path.keep();
    Ok(())
}

/// `PrettyFormatter` with Python's `ensure_ascii` string escapes. The
/// escapes JSON itself requires are left to serde_json, whose spellings
/// match Python's.
struct PythonFormatter<'a>(serde_json::ser::PrettyFormatter<'a>);

impl serde_json::ser::Formatter for PythonFormatter<'_> {
    fn write_string_fragment<W: ?Sized + std::io::Write>(&mut self, writer: &mut W, fragment: &str) -> std::io::Result<()> {
        let mut units = [0u16; 2];
        for c in fragment.chars() {
            if c.is_ascii() && c != '\x7f' {
                writer.write_all(&[c as u8])?;
            } else {
                for unit in c.encode_utf16(&mut units) {
                    write!(writer, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }
    fn begin_array<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_array(w)
    }
    fn end_array<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_array(w)
    }
    fn begin_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        self.0.begin_array_value(w, first)
    }
    fn end_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_array_value(w)
    }
    fn begin_object<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_object(w)
    }
    fn end_object<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_object(w)
    }
    fn begin_object_key<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        self.0.begin_object_key(w, first)
    }
    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.begin_object_value(w)
    }
    fn end_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        self.0.end_object_value(w)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    #[test]
    fn writes_like_python() {
        let value = json!({"name": "Søren", "list": [1, 2], "empty": {}, "none": [], "f": 1.5});
        assert_eq!(
            to_python_json(&value),
            "{\n    \"name\": \"S\\u00f8ren\",\n    \"list\": [\n        1,\n        2\n    ],\n    \"empty\": {},\n    \"none\": [],\n    \"f\": 1.5\n}"
        );
    }

    #[test]
    fn store_replaces_whole() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a").join("x.json");
        store_json(&path, &json!({"a": 1})).unwrap();
        store_json(&path, &json!({"b": 2})).unwrap();
        assert_eq!(load_json(&path).unwrap(), json!({"b": 2}));
        assert_eq!(std::fs::read(&path).unwrap(), b"{\n    \"b\": 2\n}\n");
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }

    /// Any JSON value, a few levels deep, unicode in its strings.
    fn any_json() -> impl Strategy<Value = Value> {
        let leaf = prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::from),
            any::<i64>().prop_map(Value::from),
            (-1e6f64..1e6).prop_map(Value::from),
            "\\PC{0,8}".prop_map(Value::from),
        ];
        leaf.prop_recursive(3, 24, 4, |inner| {
            prop_oneof![
                prop::collection::vec(inner.clone(), 0..4).prop_map(Value::from),
                prop::collection::btree_map("\\PC{0,6}", inner, 0..4).prop_map(|m| Value::Object(m.into_iter().collect())),
            ]
        })
    }

    proptest! {
        /// A stored value loads back the same, written as serde_json's
        /// pretty print with nothing left beside it; stored again over it,
        /// the same.
        #[test]
        fn pretty_json_stores_whole_and_loads_back(value in any_json(), again in any_json()) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("prefs").join("layout.json");
            for value in [&value, &again] {
                store_pretty_json(&path, value).unwrap();
                prop_assert_eq!(std::fs::read(&path).unwrap(), serde_json::to_vec_pretty(value).unwrap());
                prop_assert_eq!(&load_or_default(&path, Value::clone), value);
                prop_assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
            }
        }

        /// No file, or one that is not JSON, loads as the default; JSON is
        /// handed to `from_json` as it is.
        #[test]
        fn a_missing_or_broken_file_loads_the_default(bytes in prop::collection::vec(any::<u8>(), 0..32)) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("x.json");
            prop_assert_eq!(load_or_default(&path, |_| 7u8), 0);
            std::fs::write(&path, &bytes).unwrap();
            let expected = serde_json::from_slice::<Value>(&bytes).ok();
            prop_assert_eq!(load_or_default(&path, |v| Some(v.clone())), expected);
        }
    }

    /// Where a folder is in the way, a store fails and leaves nothing
    /// behind. (Not a property: on Windows the failed rename is retried
    /// for REPLACE_TIMEOUT.)
    #[test]
    fn a_blocked_store_leaves_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let blocked = dir.path().join("blocked.json");
        std::fs::create_dir(&blocked).unwrap();
        assert!(store_pretty_json(&blocked, &json!({"a": 1})).is_err());
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
        assert_eq!(std::fs::read_dir(&blocked).unwrap().count(), 0);
    }

    /// A reader holding the file open without letting it be deleted (as
    /// another Cinedex reading its prefs can, and every plain Python
    /// `open()`) only delays a store: it waits the reader out.
    #[cfg(windows)]
    #[test]
    fn a_store_waits_out_a_reader_holding_the_file() {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 1;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("layout.json");
        store_pretty_json(&path, &json!({"a": 1})).unwrap();
        let reader = std::fs::OpenOptions::new().read(true).share_mode(FILE_SHARE_READ).open(&path).unwrap();
        let released = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(300));
            drop(reader);
        });
        store_pretty_json(&path, &json!({"b": 2})).unwrap();
        released.join().unwrap();
        assert_eq!(load_or_default(&path, Value::clone), json!({"b": 2}));
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    }
}
