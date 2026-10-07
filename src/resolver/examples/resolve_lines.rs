//! Resolve `entity:` URIs read from stdin, one per line, through the same
//! `resolve_uri` entry point the C ABI calls.
//!
//! Each input line gets exactly one output line, flushed immediately:
//! `OK <path>` or `ERR <message>`. That makes it a long-lived subprocess a
//! test in another language can drive one URI at a time.
//!
//! It exists for `tests/test_resolver_parity.py`, which pins that every path
//! the Python writers (`tumblepipe.pipe.paths`) publish to is the path this
//! resolver hands back for the URI the Python side records. Not shipped:
//! examples are dev-only and never part of the release build.

use std::io::{self, BufRead, Write};

use th_resolver_core::resolve_uri;

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(_) => break,
        };
        let reply = match resolve_uri(line.trim_end_matches('\r')) {
            Ok(path) => format!("OK {path}"),
            // One line per reply, whatever the message holds.
            Err(e) => format!("ERR {}", e.to_string().replace(['\r', '\n'], " ")),
        };
        if writeln!(stdout, "{reply}").and_then(|_| stdout.flush()).is_err() {
            break;
        }
    }
}
