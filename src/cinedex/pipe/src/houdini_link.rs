//! The Houdini that started the editor, and opening a workfile in Houdini.
//!
//! When a Houdini session starts the editor (the Asset Browser's Open
//! Timeline, Play in Cinedex, Live view), it says how to reach it in the
//! editor's environment (`python/tumblepipe/pipe/houdini_link.py`):
//! [`ENDPOINT_ENV`], a port on 127.0.0.1, and [`TOKEN_ENV`]. Started any
//! other way (Desktop's Open Cinedex, by hand, or from a TumblePipe older
//! than this) there is no link: [`HoudiniLink::from_env`] is None.
//!
//! A call is one line of JSON-RPC 2.0, its params carrying the token; the
//! answer is one line back. `open_workfile` `{"path", "entity",
//! "department"}` loads the workfile in that Houdini the way the Asset
//! Browser's Open does (the unsaved-changes prompt, the project, the
//! shot's frame range), on Houdini's main thread, and raises its window;
//! it answers `{"outcome": "opened" | "already_open" | "cancelled"}`.
//!
//! [`open_workfile`] decides where a workfile opens ([`next_step`]):
//!
//! 1. a Houdini of the project that has it open is raised (Desktop);
//! 2. else the Houdini that started the editor loads it, if it is still
//!    there;
//! 3. else Desktop launches a new Houdini on it.

use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use serde_json::{Value, json};

use crate::desktop::{Desktop, Opened, session_with};

/// Where the Houdini that started the editor listens (`127.0.0.1:<port>`).
pub const ENDPOINT_ENV: &str = "TH_HOUDINI_ENDPOINT";
/// The token its calls carry.
pub const TOKEN_ENV: &str = "TH_HOUDINI_TOKEN";

/// How long connecting may take before that Houdini is taken for gone.
const CONNECT: Duration = Duration::from_secs(2);
/// How long it may take to answer: the artist may sit on the save prompt.
const ANSWER: Duration = Duration::from_secs(30 * 60);

/// The call's token was wrong: another program has the port now.
pub const BAD_TOKEN: i64 = -32001;
/// That Houdini is not the one to open it (another project's workfile).
pub const NOT_HERE: i64 = -32002;

/// The link to the Houdini that started this program.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HoudiniLink {
    addr: SocketAddr,
    token: String,
}

/// What that Houdini did with the workfile.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Answer {
    /// It loaded it, and came to the front.
    Opened,
    /// It had it open already, and came to the front.
    AlreadyOpen,
    /// The artist cancelled its unsaved-changes prompt.
    Cancelled,
}

/// Why that Houdini did not open the workfile.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AskError {
    /// It is not there (closed), or not the one to ask: open it elsewhere.
    Gone,
    /// It tried and failed: the reason, said to the artist.
    Failed(String),
}

impl HoudiniLink {
    /// The link the environment names; None when either part is missing.
    pub fn from_env() -> Option<Self> {
        let var = |key: &str| std::env::var(key).ok().filter(|v| !v.trim().is_empty());
        Self::new(&var(ENDPOINT_ENV)?, &var(TOKEN_ENV)?)
    }

    /// The link to `endpoint` (`host:port`, loopback only).
    pub fn new(endpoint: &str, token: &str) -> Option<Self> {
        let addr = endpoint.trim().to_socket_addrs().ok()?.find(|a| a.ip().is_loopback())?;
        Some(HoudiniLink { addr, token: token.to_string() })
    }

    /// Ask that Houdini to open `hip`, `entity`'s `department` workfile.
    pub fn open_workfile(&self, hip: &Path, entity: &str, department: &str) -> Result<Answer, AskError> {
        let mut stream = TcpStream::connect_timeout(&self.addr, CONNECT).map_err(|_| AskError::Gone)?;
        let lost = |e: std::io::Error| AskError::Failed(format!("your Houdini stopped answering ({e})"));
        stream.set_read_timeout(Some(ANSWER)).map_err(lost)?;
        let call = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "open_workfile",
            "params": {
                "token": self.token,
                "path": hip.to_string_lossy().replace('\\', "/"),
                "entity": entity,
                "department": department,
            },
        });
        stream.write_all(format!("{call}\n").as_bytes()).map_err(lost)?;
        let mut line = String::new();
        BufReader::new(stream).read_line(&mut line).map_err(lost)?;
        read_answer(&line)
    }
}

/// What an answer line says.
fn read_answer(line: &str) -> Result<Answer, AskError> {
    let Ok(value) = serde_json::from_str::<Value>(line.trim()) else {
        // Closed without a word: it went away while we asked.
        return Err(if line.trim().is_empty() { AskError::Gone } else { AskError::Failed("your Houdini sent a broken answer".into()) });
    };
    if let Some(error) = value.get("error") {
        let code = error.get("code").and_then(Value::as_i64).unwrap_or(0);
        if code == BAD_TOKEN || code == NOT_HERE {
            return Err(AskError::Gone);
        }
        let message = error.get("message").and_then(Value::as_str).unwrap_or("no reason given");
        return Err(AskError::Failed(message.to_string()));
    }
    match value.pointer("/result/outcome").and_then(Value::as_str) {
        Some("opened") => Ok(Answer::Opened),
        Some("already_open") => Ok(Answer::AlreadyOpen),
        Some("cancelled") => Ok(Answer::Cancelled),
        _ => Err(AskError::Failed("your Houdini sent an answer this editor does not know".into())),
    }
}

/// Where the Houdini that started the editor stands, as far as opening a
/// workfile has found out.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Caller {
    /// There is none (no link).
    None,
    /// There is one, not asked yet.
    Untried,
    /// It was asked, and is gone (or not the one to ask).
    Gone,
}

/// What opening a workfile does next.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// Raise this session, which has the workfile open.
    Raise(String),
    /// Ask the Houdini that started the editor to load it.
    AskCaller,
    /// Have Desktop launch a new Houdini on it.
    Launch,
    /// There is nowhere to open it.
    Nowhere,
}

/// The next step of opening a workfile: `open_in`, the live session that
/// has it open (if Desktop listed one); `caller`, the Houdini that started
/// the editor; `can_launch`, whether Desktop can start a new Houdini.
pub fn next_step(open_in: Option<&str>, caller: Caller, can_launch: bool) -> Step {
    if let Some(session) = open_in {
        return Step::Raise(session.to_string());
    }
    if caller == Caller::Untried {
        return Step::AskCaller;
    }
    if can_launch { Step::Launch } else { Step::Nowhere }
}

/// Open `hip` (`entity`'s `department` workfile) in Houdini, as
/// [`next_step`] says: raise the session that has it, else have the
/// Houdini that started the editor load it, else launch a new one.
pub fn open_workfile(
    desktop: Option<&Desktop>,
    caller: Option<&HoudiniLink>,
    hip: &Path,
    entity: &str,
    department: &str,
) -> Result<Opened, String> {
    let mut desktop_error = None;
    let open_in = match desktop.map(Desktop::sessions) {
        Some(Ok(sessions)) => session_with(&sessions, hip).map(|s| s.id.clone()),
        Some(Err(e)) => {
            desktop_error = Some(e);
            None
        }
        None => None,
    };
    let can_launch = desktop.is_some() && desktop_error.is_none();
    let mut state = if caller.is_some() { Caller::Untried } else { Caller::None };
    loop {
        match (next_step(open_in.as_deref(), state, can_launch), desktop, caller) {
            (Step::Raise(session), Some(desktop), _) => {
                desktop.focus(&session)?;
                return Ok(Opened::Raised(session));
            }
            (Step::AskCaller, _, Some(link)) => {
                // Houdini comes to the front itself, before its save
                // prompt: let it take the foreground from this window.
                crate::desktop::allow_foreground();
                match link.open_workfile(hip, entity, department) {
                    Ok(Answer::Opened) => return Ok(Opened::Loaded),
                    Ok(Answer::AlreadyOpen) => return Ok(Opened::AlreadyHere),
                    Ok(Answer::Cancelled) => return Ok(Opened::Cancelled),
                    Err(AskError::Failed(why)) => return Err(why),
                    Err(AskError::Gone) => state = Caller::Gone,
                }
            }
            (Step::Launch, Some(desktop), _) => return desktop.launch(hip).map(Opened::Launched),
            _ => {
                return Err(desktop_error.unwrap_or_else(|| {
                    "your Houdini has closed: open the workfile from the Asset Browser, or start this editor from TumbleTrove Desktop"
                        .into()
                }));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::sync::mpsc;

    /// A one-shot stand-in for the Houdini that started the editor: answers
    /// `reply` (None: closes without a word) and sends back the call line.
    fn houdini(reply: Option<Value>) -> (HoudiniLink, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();
            tx.send(line).unwrap();
            if let Some(reply) = reply {
                let mut stream = stream;
                stream.write_all(format!("{reply}\n").as_bytes()).unwrap();
            }
        });
        (HoudiniLink::new(&format!("127.0.0.1:{port}"), "secret").unwrap(), rx)
    }

    /// A link to a port nobody listens on: a Houdini that has closed.
    fn closed_houdini() -> HoudiniLink {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        HoudiniLink::new(&format!("127.0.0.1:{port}"), "secret").unwrap()
    }

    /// A stand-in for Desktop answering `replies` in order; each request
    /// read goes back on the channel.
    fn desktop(replies: Vec<Value>) -> (Desktop, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for body in replies {
                let Ok((mut stream, _)) = listener.accept() else { return };
                let mut seen = Vec::new();
                let mut buffer = [0u8; 4096];
                loop {
                    let n = stream.read(&mut buffer).unwrap_or(0);
                    seen.extend_from_slice(&buffer[..n]);
                    let text = String::from_utf8_lossy(&seen).to_string();
                    if let Some(split) = text.find("\r\n\r\n") {
                        let length: usize = text
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap()))
                            .unwrap_or(0);
                        if seen.len() >= split + 4 + length {
                            break;
                        }
                    }
                    if n == 0 {
                        break;
                    }
                }
                let _ = tx.send(String::from_utf8_lossy(&seen).to_string());
                let body = body.to_string();
                let _ = stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes());
            }
        });
        (Desktop::new(&format!("http://127.0.0.1:{port}"), "proj-1", "tok").unwrap(), rx)
    }

    const HIP: &str = "C:/show/shots/A/010/animation/A_010_animation_v0003.hip";
    const SHOT: &str = "entity:/shots/A/010";

    #[test]
    fn only_loopback_endpoints() {
        assert!(HoudiniLink::new("127.0.0.1:5000", "t").is_some());
        assert!(HoudiniLink::new("8.8.8.8:53", "t").is_none());
        assert!(HoudiniLink::new("not an endpoint", "t").is_none());
    }

    #[test]
    fn the_call_carries_token_path_entity_and_department() {
        let (link, seen) = houdini(Some(json!({"jsonrpc": "2.0", "id": 1, "result": {"outcome": "opened"}})));
        let hip = HIP.replace('/', "\\");
        assert_eq!(link.open_workfile(Path::new(&hip), SHOT, "animation"), Ok(Answer::Opened));
        let call: Value = serde_json::from_str(seen.recv().unwrap().trim()).unwrap();
        assert_eq!(call["method"], "open_workfile");
        assert_eq!(call["params"]["token"], "secret");
        assert_eq!(call["params"]["path"], HIP);
        assert_eq!(call["params"]["entity"], SHOT);
        assert_eq!(call["params"]["department"], "animation");
    }

    #[test]
    fn answers_read_as_what_they_say() {
        let answer = |v: Value| read_answer(&v.to_string());
        assert_eq!(answer(json!({"result": {"outcome": "already_open"}})), Ok(Answer::AlreadyOpen));
        assert_eq!(answer(json!({"result": {"outcome": "cancelled"}})), Ok(Answer::Cancelled));
        assert_eq!(answer(json!({"error": {"code": BAD_TOKEN, "message": "x"}})), Err(AskError::Gone));
        assert_eq!(answer(json!({"error": {"code": NOT_HERE, "message": "x"}})), Err(AskError::Gone));
        assert_eq!(answer(json!({"error": {"code": -32000, "message": "no disk"}})), Err(AskError::Failed("no disk".into())));
        assert_eq!(read_answer(""), Err(AskError::Gone));
        assert!(matches!(read_answer("garbage"), Err(AskError::Failed(_))));
        assert!(matches!(answer(json!({"result": {"outcome": "teleported"}})), Err(AskError::Failed(_))));
    }

    #[test]
    fn a_closed_houdini_is_gone() {
        assert_eq!(closed_houdini().open_workfile(Path::new(HIP), SHOT, "animation"), Err(AskError::Gone));
    }

    proptest! {
        /// Raise wins; the caller is asked once, before any launch; a
        /// launch needs Desktop; nothing else is ever chosen.
        #[test]
        fn the_next_step(open_in in proptest::option::of("[a-z0-9]{1,8}"), caller in 0u8..3, can_launch in any::<bool>()) {
            let caller = [Caller::None, Caller::Untried, Caller::Gone][caller as usize];
            let step = next_step(open_in.as_deref(), caller, can_launch);
            let expected = match (&open_in, caller, can_launch) {
                (Some(id), _, _) => Step::Raise(id.clone()),
                (None, Caller::Untried, _) => Step::AskCaller,
                (None, _, true) => Step::Launch,
                (None, _, false) => Step::Nowhere,
            };
            prop_assert_eq!(step, expected);
        }
    }

    proptest! {
        // Each case opens real sockets (a refused connect takes a second
        // or two on Windows), and there are 24 combinations.
        #![proptest_config(ProptestConfig::with_cases(32))]

        /// End to end against stand-ins: the session that has the workfile
        /// is raised; else the calling Houdini's answer stands (a cancel or
        /// a failure never launches); only a calling Houdini that is gone,
        /// or none, leads to a launch, and only with Desktop.
        #[test]
        fn open_raises_asks_or_launches(
            open_elsewhere in any::<bool>(),
            caller in 0u8..6,
            with_desktop in any::<bool>(),
        ) {
            // caller: 0 none, 1 closed, 2 opened, 3 already open, 4 cancelled, 5 failed.
            let outcome = |o: &str| Some(json!({"jsonrpc": "2.0", "id": 1, "result": {"outcome": o}}));
            let (link, asked) = match caller {
                0 => (None, None),
                1 => (Some(closed_houdini()), None),
                2 => { let (l, r) = houdini(outcome("opened")); (Some(l), Some(r)) }
                3 => { let (l, r) = houdini(outcome("already_open")); (Some(l), Some(r)) }
                4 => { let (l, r) = houdini(outcome("cancelled")); (Some(l), Some(r)) }
                _ => { let (l, r) = houdini(Some(json!({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "the disk is full"}}))); (Some(l), Some(r)) }
            };
            let rows = json!([{"sessionId": "s1", "pid": 7, "state": "running", "projectId": "proj-1",
                "hipFile": if open_elsewhere { HIP } else { "C:/other.hip" }}]);
            let raise = with_desktop && open_elsewhere;
            let launch = with_desktop && !raise && matches!(caller, 0 | 1);
            let second = if launch { json!({"sessionId": "new"}) } else { Value::Null };
            let (dt, requests) = desktop(vec![rows, second]);
            let result = open_workfile(with_desktop.then_some(&dt), link.as_ref(), Path::new(HIP), SHOT, "animation");
            let expected = if raise {
                Ok(Opened::Raised("s1".into()))
            } else {
                match caller {
                    2 => Ok(Opened::Loaded),
                    3 => Ok(Opened::AlreadyHere),
                    4 => Ok(Opened::Cancelled),
                    5 => Err("the disk is full".to_string()),
                    _ if with_desktop => Ok(Opened::Launched("new".into())),
                    _ => Err("your Houdini has closed: open the workfile from the Asset Browser, or start this editor from TumbleTrove Desktop".into()),
                }
            };
            prop_assert_eq!(result, expected);
            if with_desktop {
                prop_assert!(requests.recv().unwrap().contains("sessions_list"));
                let next = requests.recv_timeout(Duration::from_millis(if raise || launch { 5000 } else { 100 }));
                if raise {
                    prop_assert!(next.unwrap().contains("sessions_focus"));
                } else if launch {
                    prop_assert!(next.unwrap().contains("launch_project"));
                } else {
                    prop_assert!(next.is_err(), "Desktop was called again: nothing may launch");
                }
            }
            if let Some(asked) = asked {
                // The calling Houdini is asked only when no session had it.
                prop_assert_eq!(asked.recv_timeout(Duration::from_millis(if raise { 100 } else { 5000 })).is_ok(), !raise);
            }
        }
    }
}
