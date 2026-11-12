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

/// The text [`store_json`] writes for `value`: [`to_python_json`], in
/// `\r\n` lines on Windows, as Python writes a file in text mode.
pub fn python_text(value: &Value) -> String {
    let text = to_python_json(value);
    if cfg!(windows) { text.replace('\n', "\r\n") } else { text }
}

/// Write `value` to `path` atomically, as `util.io.store_json`: into a
/// temporary file beside it, then renamed over it ([`replace_file`]). The
/// file is either whole or untouched.
pub fn store_json(path: &Path, value: &Value) -> std::io::Result<()> {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    std::fs::create_dir_all(parent)?;
    let temp = tempfile::Builder::new().prefix(".tmp").suffix(".json").tempfile_in(parent)?;
    let temp_path = temp.into_temp_path();
    std::fs::write(&temp_path, python_text(value))?;
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
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }
}
