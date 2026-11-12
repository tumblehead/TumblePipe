//! A stand-in player for the Python side's tests: the real endpoint
//! (`th_pipe::player_rpc`, as cinedex serves it) in front of an editor
//! that answers every view one way.
//!
//! `rpc_stand_in <players dir> <shows|not_here|refuses> <calls file>` serves,
//! prints `ready <endpoint file>`, appends each view it is asked for to the
//! calls file as a JSON line, and serves until its stdin closes; its
//! endpoint file goes with it, as a player's does.

use std::io::{BufRead, Write};
use std::path::PathBuf;

use serde_json::json;
use th_pipe::player_rpc::{self, Call, NOT_HERE, REFUSED, Refusal, Request};

fn main() {
    let mut args = std::env::args().skip(1);
    let (Some(dir), Some(mode), Some(calls)) = (args.next(), args.next(), args.next()) else {
        eprintln!("usage: rpc_stand_in <players dir> <shows|not_here|refuses> <calls file>");
        std::process::exit(2);
    };
    let dir = PathBuf::from(dir);
    std::fs::create_dir_all(&dir).expect("players dir");
    let calls = PathBuf::from(calls);
    let server = player_rpc::serve(&dir, player_rpc::Events::new(), move |call: Call| {
        let Request::ViewShot { shot, frame } = call.request.clone() else {
            call.answer(Err(Refusal::new(player_rpc::NO_METHOD, "the stand-in only shows shots")));
            return true;
        };
        if let Ok(mut log) = std::fs::OpenOptions::new().create(true).append(true).open(&calls) {
            let _ = writeln!(log, "{}", json!({"shot": shot, "frame": frame}));
        }
        call.answer(match mode.as_str() {
            "shows" => Ok(json!({"start": 0, "end": 10, "playhead": 4})),
            "not_here" => Err(Refusal::new(NOT_HERE, "this window is not on the master edit")),
            _ => Err(Refusal::new(REFUSED, format!("the edit has no clip of {shot}"))),
        });
        true
    })
    .expect("serve");
    println!("ready {}", dir.join(format!("{}.json", std::process::id())).display());
    let _ = std::io::stdout().flush();
    for _ in std::io::stdin().lock().lines() {}
    drop(server);
}
