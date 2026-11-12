//! The player's endpoint: how programs of this user reach a running player
//! (Live view: Houdini asks it to show the shot it renders live). The seed
//! of the agent RPC (`designs/shot-dashboard-live-playblast.md` §8).
//!
//! A player listens on `127.0.0.1` (a port the system picks) and writes
//! where, with a token, to `players/<project key>/<pid>.json` under the live
//! root (`crate::live::players_dir`), removed when it closes. A call is
//! one line of JSON-RPC 2.0, its params carrying the token; the answer is
//! one line back:
//!
//! - `ping` → `{"pid", "protocol"}`;
//! - `view_shot` `{"shot": "entity:/shots/...", "frame": 1040}` → the view
//!   (`{"start", "end", "playhead"}`), or an error: -32002 when this player
//!   is not the one to ask (it is not on the project's master edit; ask
//!   another, or start one), -32000 when it is but cannot (the master edit
//!   has no clip of the shot).
//!
//! - `events` `{"after": 12, "wait_ms": 10000}` → the editor's events
//!   since number `after` (`{"events": [{"seq", "kind", ...}], "next",
//!   "lost"}`), waiting up to `wait_ms` (at most 25 s) for one when there
//!   is none yet: the event stream, as a long poll. The last 256 are kept;
//!   `lost` says some after `after` are gone.
//! - any other method, the agent commands (the player's `agent` module,
//!   `tools/list` among them): handed to the editor as
//!   [`Request::Call`], its params without the token; the editor answers
//!   -32601 for a method it does not know.
//!
//! A wrong or missing token is refused (-32001) before anything is read
//! into the editor.

use std::collections::VecDeque;
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::{BufRead, BufReader, Write};
use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::time::{Duration, Instant};

use crate::live::{PLAYER_PROTOCOL, PlayerEndpoint};
use serde_json::{Value, json};

/// How long a call waits for the editor to answer it (a save on a file
/// server takes seconds).
const ANSWER_WITHIN: Duration = Duration::from_secs(60);
/// The longest an `events` call waits for one.
pub const LONGEST_WAIT: Duration = Duration::from_secs(25);
/// How many events are kept for `events` calls.
pub const EVENTS_KEPT: usize = 256;
/// How long a caller may take to send its line.
const READ_WITHIN: Duration = Duration::from_secs(2);
/// The longest call line read.
const MAX_LINE: u64 = 64 * 1024;

/// The call did not carry the endpoint's token.
pub const BAD_TOKEN: i64 = -32001;
/// The player asked cannot do it.
pub const REFUSED: i64 = -32000;
/// The player asked is not the one to ask (another, or a new one, is).
pub const NOT_HERE: i64 = -32002;
/// No such method.
pub const NO_METHOD: i64 = -32601;
/// The params are not what the method wants.
pub const BAD_PARAMS: i64 = -32602;
const PARSE: i64 = -32700;

/// What the editor is asked to do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Request {
    /// Show `shot` on the master edit, at shot frame `frame`.
    ViewShot { shot: String, frame: Option<i64> },
    /// Any other method, with its params (the token taken out).
    Call { method: String, params: Value },
}

/// The editor's events, numbered from 1, the last [`EVENTS_KEPT`] kept, for
/// `events` calls to read and wait on. Clones share them.
#[derive(Debug, Clone, Default)]
pub struct Events {
    shared: Arc<(Mutex<EventLog>, Condvar)>,
}

#[derive(Debug, Default)]
struct EventLog {
    /// The number the next event gets, less one: the last given.
    last: u64,
    kept: VecDeque<Value>,
}

/// What an `events` call reads: the events after the one asked, the
/// number of the last, and whether some after it are no longer kept.
#[derive(Debug, Clone, PartialEq)]
pub struct EventsSince {
    pub events: Vec<Value>,
    pub next: u64,
    pub lost: bool,
}

impl Events {
    pub fn new() -> Self {
        Self::default()
    }

    /// Say `kind` happened, with `fields` (an object; anything else is
    /// kept under `"value"`), and wake the calls waiting.
    pub fn push(&self, kind: &str, fields: Value) {
        let (log, woken) = &*self.shared;
        let mut log = log.lock().unwrap();
        log.last += 1;
        let mut event = match fields {
            Value::Object(map) => Value::Object(map),
            Value::Null => json!({}),
            other => json!({"value": other}),
        };
        event["seq"] = json!(log.last);
        event["kind"] = json!(kind);
        log.kept.push_back(event);
        while log.kept.len() > EVENTS_KEPT {
            log.kept.pop_front();
        }
        woken.notify_all();
    }

    /// The events after number `after`, waiting up to `wait` for one when
    /// there is none yet.
    pub fn since(&self, after: u64, wait: Duration) -> EventsSince {
        let (log, woken) = &*self.shared;
        let until = Instant::now() + wait.min(LONGEST_WAIT);
        let mut log = log.lock().unwrap();
        while log.last <= after {
            let now = Instant::now();
            if now >= until {
                break;
            }
            log = woken.wait_timeout(log, until - now).unwrap().0;
        }
        let first_kept = log.last + 1 - log.kept.len() as u64;
        let events = log.kept.iter().filter(|e| e["seq"].as_u64().is_some_and(|s| s > after)).cloned().collect();
        EventsSince { events, next: log.last, lost: after + 1 < first_kept && log.last > after }
    }
}

/// Where a call's answer goes.
type Answer = mpsc::Sender<Result<Value, Refusal>>;

/// Why a call was not done: a JSON-RPC error code and words.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refusal {
    pub code: i64,
    pub message: String,
}

impl Refusal {
    pub fn new(code: i64, message: impl Into<String>) -> Self {
        Self { code, message: message.into() }
    }
}

/// A call for the editor to answer, once.
#[derive(Debug, Clone)]
pub struct Call {
    pub request: Request,
    reply: Arc<Mutex<Option<Answer>>>,
}

impl Call {
    pub fn answer(&self, result: Result<Value, Refusal>) {
        if let Some(reply) = self.reply.lock().unwrap().take() {
            let _ = reply.send(result);
        }
    }
}

/// A running endpoint; its file goes when it is dropped.
pub struct Server {
    file: PathBuf,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.file);
    }
}

/// Where an editor keeps its endpoint while it runs. The endpoint is
/// served from a subscription that never returns, so the editor's quit,
/// not the end of that task, is what must take the file away: `release`
/// drops the endpoint held (its file goes), and one handed over after the
/// release (the editor quit before its endpoint was up) goes at once.
#[derive(Clone, Default)]
pub struct EndpointSlot(Arc<Mutex<SlotState>>);

#[derive(Default)]
struct SlotState {
    server: Option<Server>,
    released: bool,
}

impl EndpointSlot {
    /// Keep `server` until `release`; after it, drop `server` now. A server
    /// held before is replaced; its file goes unless it is the new one's
    /// (the same process writes the same file).
    pub fn hold(&self, server: Server) {
        let mut state = self.0.lock().unwrap();
        if state.released {
            drop(state);
            drop(server);
            return;
        }
        let new_file = server.file.clone();
        if let Some(mut old) = state.server.replace(server)
            && old.file == new_file
        {
            // Its file is the new server's now: dropping it must not
            // remove it.
            old.file = PathBuf::new();
        }
    }

    /// The editor quits: the endpoint held goes, and so will any handed over later.
    pub fn release(&self) {
        let mut state = self.0.lock().unwrap();
        state.released = true;
        let server = state.server.take();
        drop(state);
        drop(server);
    }
}

/// The programs a running editor is: `cinedex`, and `th_player`, its name
/// up to TumblePipe 1.103 (an editor started from an older install), as
/// Houdini's Live view client counts them (`pipe.live_view.PLAYER_PROGRAMS`,
/// held alike by test_live_parity).
pub const PLAYER_PROGRAMS: [&str; 2] = ["cinedex", "th_player"];

/// Whether a process running `program` (`live::program_name`'s form; None:
/// no such process) is a player, to a process running `own`: an editor by
/// either name, or the same program as the one asking (a test's stand-in
/// players count each other).
pub fn is_player_program(program: Option<&str>, own: Option<&str>) -> bool {
    program.is_some_and(|program| PLAYER_PROGRAMS.contains(&program) || own == Some(program))
}

/// Whether process `pid` is a running player. A pid Windows has since given
/// to another program is not one, though it is alive.
pub fn is_player(pid: u32) -> bool {
    let own = std::env::current_exe().ok().map(|exe| crate::live::program_name(&exe.to_string_lossy()));
    is_player_program(crate::live::process_program(pid).as_deref(), own.as_deref())
}

/// A token nobody else can guess: 128 bits from the system's random keys.
fn token() -> String {
    (0..2)
        .map(|i| {
            let mut hasher = RandomState::new().build_hasher();
            hasher.write_u32(std::process::id());
            hasher.write_u8(i);
            format!("{:016x}", hasher.finish())
        })
        .collect()
}

/// Listen, say where in `dir`, and hand each call to `deliver` (false: the
/// editor is gone). Endpoint files in `dir` of players no longer running
/// are cleared on the way.
pub fn serve(dir: &Path, events: Events, deliver: impl Fn(Call) -> bool + Send + Sync + 'static) -> std::io::Result<Server> {
    clear_stale(dir);
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
    let endpoint =
        PlayerEndpoint { pid: std::process::id(), port: listener.local_addr()?.port(), token: token(), protocol: PLAYER_PROTOCOL };
    let file = crate::live::write_endpoint(dir, &endpoint)?;
    let deliver = Arc::new(deliver);
    std::thread::Builder::new().name("cinedex rpc".into()).spawn(move || {
        for stream in listener.incoming().flatten() {
            let (deliver, token, events) = (Arc::clone(&deliver), endpoint.token.clone(), events.clone());
            let _ = std::thread::Builder::new().name("cinedex rpc call".into()).spawn(move || {
                let _ = answer_one(stream, &token, &events, &*deliver);
            });
        }
    })?;
    Ok(Server { file })
}

/// Endpoint files whose player is gone: no process has its pid, or the
/// one that has it is no player (`is_player`).
fn clear_stale(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        let pid = path.file_stem().and_then(|s| s.to_str()).and_then(|s| s.parse::<u32>().ok());
        if pid.is_some_and(|pid| !is_player(pid)) {
            let _ = std::fs::remove_file(path);
        }
    }
}

/// Read one call from `stream`, answer it, and close.
fn answer_one(stream: TcpStream, token: &str, events: &Events, deliver: &dyn Fn(Call) -> bool) -> std::io::Result<()> {
    stream.set_read_timeout(Some(READ_WITHIN))?;
    let mut line = String::new();
    BufReader::new(std::io::Read::take(&stream, MAX_LINE)).read_line(&mut line)?;
    let reply = respond(&line, token, events, deliver);
    let mut stream = stream;
    stream.write_all(format!("{reply}\n").as_bytes())?;
    stream.flush()
}

/// The answer line to call `line`.
pub fn respond(line: &str, token: &str, events: &Events, deliver: &dyn Fn(Call) -> bool) -> Value {
    let error = |id: &Value, code: i64, message: &str| json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}});
    let Ok(call) = serde_json::from_str::<Value>(line.trim()) else { return error(&Value::Null, PARSE, "not JSON") };
    let id = call.get("id").cloned().unwrap_or(Value::Null);
    let params = call.get("params").cloned().unwrap_or(Value::Null);
    if params.get("token").and_then(Value::as_str) != Some(token) {
        return error(&id, BAD_TOKEN, "wrong token");
    }
    let request = match call.get("method").and_then(Value::as_str) {
        Some("ping") => return json!({"jsonrpc": "2.0", "id": id, "result": {"pid": std::process::id(), "protocol": PLAYER_PROTOCOL}}),
        Some("view_shot") => {
            let Some(shot) = params.get("shot").and_then(Value::as_str).filter(|s| s.starts_with("entity:/shots/")) else {
                return error(&id, BAD_PARAMS, "view_shot wants a shot (entity:/shots/...)");
            };
            let frame = match params.get("frame") {
                None | Some(Value::Null) => None,
                Some(f) => match f.as_i64() {
                    Some(f) => Some(f),
                    None => return error(&id, BAD_PARAMS, "frame is a whole number"),
                },
            };
            Request::ViewShot { shot: shot.to_owned(), frame }
        }
        Some("events") => {
            let number = |key: &str| match params.get(key) {
                None | Some(Value::Null) => Some(0),
                Some(v) => v.as_u64(),
            };
            let (Some(after), Some(wait)) = (number("after"), number("wait_ms")) else {
                return error(&id, BAD_PARAMS, "events wants after and wait_ms as whole numbers from 0");
            };
            let since = events.since(after, Duration::from_millis(wait));
            return json!({"jsonrpc": "2.0", "id": id, "result": {"events": since.events, "next": since.next, "lost": since.lost}});
        }
        Some(method) => {
            let mut params = match params {
                Value::Object(map) => map,
                _ => return error(&id, BAD_PARAMS, "params is an object"),
            };
            params.remove("token");
            Request::Call { method: method.to_owned(), params: Value::Object(params) }
        }
        None => return error(&id, NO_METHOD, "no method named"),
    };
    let (sender, receiver) = mpsc::channel();
    if !deliver(Call { request, reply: Arc::new(Mutex::new(Some(sender))) }) {
        return error(&id, REFUSED, "the player is closing");
    }
    match receiver.recv_timeout(ANSWER_WITHIN) {
        Ok(Ok(result)) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Ok(Err(refusal)) => error(&id, refusal.code, &refusal.message),
        Err(_) => error(&id, REFUSED, "the player did not answer"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A stand-in editor: answers every view with `answer`, recording it.
    fn editor(answer: Result<Value, Refusal>) -> (impl Fn(Call) -> bool, Arc<Mutex<Vec<Request>>>) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let log = Arc::clone(&seen);
        (
            move |call: Call| {
                log.lock().unwrap().push(call.request.clone());
                call.answer(answer.clone());
                true
            },
            seen,
        )
    }

    proptest! {
        /// Only a call with the token reaches the editor; a view_shot with
        /// one is handed over as asked and answered with what the editor
        /// said, its id kept.
        #[test]
        fn only_the_token_holder_reaches_the_editor(
            given in prop::option::of("[a-f0-9]{32}"),
            frame in prop::option::of(-5i64..5000),
            id in 0u32..1000,
            refused in prop::option::of(prop::sample::select(vec![REFUSED, NOT_HERE])),
        ) {
            let token = "0123456789abcdef0123456789abcdef";
            let answer = match refused {
                Some(code) => Err(Refusal::new(code, "not the master edit")),
                None => Ok(json!({"playhead": 7})),
            };
            let (deliver, seen) = editor(answer.clone());
            let mut params = json!({"shot": "entity:/shots/S/A", "frame": frame});
            if let Some(given) = &given {
                params["token"] = json!(given);
            }
            let line = json!({"jsonrpc": "2.0", "id": id, "method": "view_shot", "params": params}).to_string();
            let reply = respond(&line, token, &Events::new(), &deliver);
            prop_assert_eq!(&reply["id"], &json!(id));
            if given.as_deref() != Some(token) {
                prop_assert_eq!(&reply["error"]["code"], &json!(BAD_TOKEN));
                prop_assert!(seen.lock().unwrap().is_empty());
                return Ok(());
            }
            prop_assert_eq!(seen.lock().unwrap().clone(), vec![Request::ViewShot { shot: "entity:/shots/S/A".into(), frame }]);
            match answer {
                Ok(v) => prop_assert_eq!(&reply["result"], &v),
                Err(r) => {
                    prop_assert_eq!(&reply["error"]["code"], &json!(r.code));
                    prop_assert_eq!(&reply["error"]["message"], &json!(r.message));
                }
            }
        }
    }

    #[test]
    fn malformed_calls_are_refused_without_reaching_the_editor() {
        let token = "t";
        let (deliver, seen) = editor(Ok(json!({})));
        let events = Events::new();
        let code = |line: &str| respond(line, token, &events, &deliver)["error"]["code"].clone();
        assert_eq!(code("{nope"), json!(PARSE));
        assert_eq!(code(r#"{"id":1,"params":{"token":"t"}}"#), json!(NO_METHOD));
        assert_eq!(code(r#"{"id":1,"method":"events","params":{"token":"t","after":-1}}"#), json!(BAD_PARAMS));
        assert_eq!(code(r#"{"id":1,"method":"view_shot","params":{"token":"t"}}"#), json!(BAD_PARAMS));
        assert_eq!(code(r#"{"id":1,"method":"view_shot","params":{"token":"t","shot":"entity:/assets/x"}}"#), json!(BAD_PARAMS));
        assert_eq!(
            code(r#"{"id":1,"method":"view_shot","params":{"token":"t","shot":"entity:/shots/S/A","frame":"x"}}"#),
            json!(BAD_PARAMS)
        );
        assert!(seen.lock().unwrap().is_empty());
        let ping = respond(r#"{"id":2,"method":"ping","params":{"token":"t"}}"#, token, &events, &deliver);
        assert_eq!(ping["result"]["protocol"], json!(PLAYER_PROTOCOL));
        // Any other method goes to the editor, without the token.
        let reply = respond(r#"{"id":3,"method":"get_state","params":{"token":"t","x":1}}"#, token, &events, &deliver);
        assert_eq!(reply["result"], json!({}));
        assert_eq!(seen.lock().unwrap().clone(), vec![Request::Call { method: "get_state".into(), params: json!({"x": 1}) }]);
    }

    proptest! {
        /// However many events are pushed and from wherever a reader asks,
        /// it gets each kept event after the one it names once, in order,
        /// numbered on from it, `next` the last; `lost` exactly when some
        /// after it were dropped (only the last EVENTS_KEPT are kept).
        #[test]
        fn a_reader_gets_each_kept_event_once_in_order(pushed in 0usize..600, after in 0u64..700) {
            let events = Events::new();
            for i in 0..pushed {
                events.push(if i % 2 == 0 { "edit_changed" } else { "saved" }, json!({"i": i}));
            }
            let since = events.since(after, Duration::ZERO);
            let last = pushed as u64;
            prop_assert_eq!(since.next, last);
            let first_kept = last.saturating_sub(EVENTS_KEPT as u64) + 1;
            let want: Vec<u64> = (after.max(first_kept - 1) + 1..=last).collect();
            let got: Vec<u64> = since.events.iter().map(|e| e["seq"].as_u64().unwrap()).collect();
            prop_assert_eq!(got, want.clone());
            for e in &since.events {
                let seq = e["seq"].as_u64().unwrap();
                prop_assert_eq!(&e["i"], &json!(seq - 1));
            }
            prop_assert_eq!(since.lost, after + 1 < first_kept && last > after);
        }
    }

    /// A reader with nothing new waits, and an event pushed meanwhile
    /// wakes it at once; with nothing pushed it comes back empty.
    #[test]
    fn a_waiting_reader_is_woken_by_an_event() {
        let events = Events::new();
        let pusher = events.clone();
        let started = Instant::now();
        let thread = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(100));
            pusher.push("saved", json!({"version": "v0002"}));
        });
        let since = events.since(0, Duration::from_secs(10));
        thread.join().unwrap();
        assert_eq!(since.events.len(), 1);
        assert_eq!(since.events[0]["kind"], json!("saved"));
        assert!(started.elapsed() < Duration::from_secs(5), "woken, not timed out");
        let empty = events.since(1, Duration::from_millis(50));
        assert!(empty.events.is_empty() && !empty.lost && empty.next == 1);
    }

    /// Over a real socket: the endpoint file says where, a call there is
    /// answered, and the file goes with the server.
    #[test]
    fn a_served_endpoint_answers_and_goes_with_the_server() {
        let dir = tempfile::tempdir().unwrap();
        let (deliver, seen) = editor(Ok(json!({"playhead": 3})));
        let server = serve(dir.path(), Events::new(), deliver).unwrap();
        let file = dir.path().join(format!("{}.json", std::process::id()));
        let endpoint: PlayerEndpoint = serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
        let mut stream = TcpStream::connect((Ipv4Addr::LOCALHOST, endpoint.port)).unwrap();
        let call = json!({"jsonrpc": "2.0", "id": 9, "method": "view_shot", "params": {"token": endpoint.token, "shot": "entity:/shots/S/B", "frame": 1040}});
        stream.write_all(format!("{call}\n").as_bytes()).unwrap();
        let mut line = String::new();
        BufReader::new(&stream).read_line(&mut line).unwrap();
        let reply: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(reply["result"]["playhead"], json!(3));
        assert_eq!(seen.lock().unwrap().clone(), vec![Request::ViewShot { shot: "entity:/shots/S/B".into(), frame: Some(1040) }]);
        drop(server);
        assert!(!file.exists());
    }

    /// One step an editor's endpoint goes through: served (and handed to
    /// the slot) or the editor quitting.
    #[derive(Clone, Debug)]
    enum Step {
        Serve,
        Quit,
    }

    proptest! {
        /// Whatever the order the endpoint comes up and the editor quits
        /// in (a quit before the endpoint is up included), the endpoint
        /// file is there exactly while one is held and the editor has not
        /// quit; after a quit nothing brings it back.
        #[test]
        fn the_endpoint_file_lives_until_the_editor_quits(
            steps in prop::collection::vec(prop_oneof![3 => Just(Step::Serve), 1 => Just(Step::Quit)], 0..6),
        ) {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join(format!("{}.json", std::process::id()));
            let slot = EndpointSlot::default();
            let (mut quit, mut served) = (false, false);
            for step in &steps {
                match step {
                    Step::Serve => {
                        let (deliver, _) = editor(Ok(json!({})));
                        slot.hold(serve(dir.path(), Events::new(), deliver).unwrap());
                        served = true;
                    }
                    Step::Quit => {
                        slot.release();
                        quit = true;
                    }
                }
                prop_assert_eq!(file.exists(), served && !quit, "after {:?} of {:?}", step, steps);
            }
            // A clone (the subscription's) releases the same endpoint.
            slot.clone().release();
            prop_assert!(!file.exists());
        }

        /// A process is a player when it runs either of the editor's names,
        /// or the asking process's own program; no process, and any other
        /// program, is not.
        #[test]
        fn players_are_known_by_program(
            program in prop::option::of(prop::sample::select(vec!["cinedex", "th_player", "husk", "houdini", "rpc_stand_in", "cinedex_old", "th_player2", ""])),
            own in prop::option::of(prop::sample::select(vec!["cinedex", "rpc_stand_in", "th_pipe-0123abcd", "husk"])),
        ) {
            let expected = match program {
                None => false,
                Some(p) => p == "cinedex" || p == "th_player" || Some(p) == own,
            };
            prop_assert_eq!(is_player_program(program, own), expected);
        }
    }

    /// An endpoint file whose pid is alive but is no player (here: a
    /// process that is not this test's own program, nor an editor) is
    /// cleared when a player starts serving; this process's own file stays.
    #[test]
    fn a_pid_given_to_another_program_is_no_player() {
        let dir = tempfile::tempdir().unwrap();
        let mut other = std::process::Command::new(if cfg!(windows) { "cmd" } else { "sleep" })
            .args(if cfg!(windows) { vec!["/c", "ping", "-n", "30", "127.0.0.1"] } else { vec!["30"] })
            .stdout(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let theirs = dir.path().join(format!("{}.json", other.id()));
        std::fs::write(&theirs, b"{}").unwrap();
        assert!(crate::live::process_program(other.id()).is_some(), "the stand-in process runs");
        let (deliver, _) = editor(Ok(json!({})));
        let server = serve(dir.path(), Events::new(), deliver).unwrap();
        let _ = other.kill();
        let _ = other.wait();
        assert!(!theirs.exists(), "a live pid of another program is cleared");
        assert!(dir.path().join(format!("{}.json", std::process::id())).exists());
        drop(server);
    }
}
