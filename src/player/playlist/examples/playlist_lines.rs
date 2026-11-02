//! Line protocol for the Python side's parity property
//! (`tests/test_review_playlist.py`): each stdin line is a playlist as one
//! line of JSON; each stdout line is the playlist the player read from it,
//! written back as JSON (so anything dropped or changed on the way shows),
//! or `{"error": "..."}`.

use std::io::{BufRead, Write};

use serde_json::json;
use th_playlist::Playlist;

fn main() {
    let stdout = std::io::stdout();
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let reply = match Playlist::parse(&line) {
            Ok(playlist) => serde_json::to_value(&playlist).unwrap(),
            Err(e) => json!({ "error": e }),
        };
        let mut out = stdout.lock();
        writeln!(out, "{reply}").unwrap();
        out.flush().unwrap();
    }
}
