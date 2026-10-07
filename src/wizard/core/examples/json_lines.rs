//! Re-serialize JSON documents through `to_json_string`, one per line.
//!
//! Each input line is a JSON *string* holding a whole document's text; each
//! output line is a JSON string holding what `to_json_string` writes for it,
//! or `ERR <message>` when the text does not parse. Wrapping documents in
//! strings keeps one request to one line whatever the document contains.
//!
//! It exists for `tests/test_wizard_json_parity.py`, which pins the
//! migrator's byte-for-byte parity with Python's `json.dump(indent=4)`
//! against the Python interpreter itself rather than a hand-written
//! expectation. Not shipped: examples are dev-only.

use std::io::{self, BufRead, Write};

use serde_json::Value;
use th_project_core::to_json_string;

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let reply = serde_json::from_str::<String>(line.trim_end_matches('\r'))
            .map_err(|e| format!("request is not a JSON string: {e}"))
            .and_then(|text| serde_json::from_str::<Value>(&text).map_err(|e| e.to_string()))
            .map(|value| serde_json::to_string(&to_json_string(&value)).expect("a string serializes"));
        let reply = match reply {
            Ok(encoded) => encoded,
            Err(e) => format!("ERR {}", e.replace(['\r', '\n'], " ")),
        };
        if writeln!(stdout, "{reply}").and_then(|_| stdout.flush()).is_err() {
            break;
        }
    }
}
