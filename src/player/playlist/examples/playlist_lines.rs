//! Line protocol for the Python side's parity property
//! (`tests/test_review_playlist.py`): each stdin line is the path of an
//! `.otio` file; each stdout line is the playlist read from it as JSON,
//! `{"name", "ocio", "shots": [{"name", "sequence", "take", "takes":
//! [{"label", "path", "rate", "kind", "department", "layer", "version"}]}]}`,
//! or `{"error": "..."}`.

use std::io::{BufRead, Write};

use serde_json::json;
use th_playlist::Playlist;

fn main() {
    let stdout = std::io::stdout();
    for line in std::io::stdin().lock().lines() {
        let Ok(path) = line else { break };
        let reply = match Playlist::read(path.trim()) {
            Ok(p) => json!({
                "name": p.name,
                "ocio": p.ocio,
                "shots": p.shots.iter().map(|s| json!({
                    "name": s.name,
                    "sequence": s.sequence,
                    "take": s.take,
                    "takes": s.takes.iter().map(|t| json!({
                        "label": t.label,
                        "path": t.path,
                        "rate": t.rate.map(|(n, d)| vec![n, d]),
                        "kind": t.kind.name(),
                        "department": t.department,
                        "layer": t.layer,
                        "version": t.version,
                    })).collect::<Vec<_>>(),
                })).collect::<Vec<_>>(),
            }),
            Err(e) => json!({ "error": e }),
        };
        let mut out = stdout.lock();
        writeln!(out, "{reply}").unwrap();
        out.flush().unwrap();
    }
}
