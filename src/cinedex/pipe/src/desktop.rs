//! TumbleTrove Desktop, as a project's own programs reach it: launch the
//! project's Houdini on a workfile, list its Houdini sessions, raise one.
//!
//! Desktop hands the programs it starts (a project's Houdini sessions and
//! its Scripts-panel apps, and through them the editor) three variables:
//! `TT_DESKTOP_URL` (its loopback Remote Control API), `TT_DESKTOP_PROJECT_ID`
//! and `TT_APP_TOKEN`, a token that reaches `launch_project`,
//! `sessions_list` and `sessions_focus` for that project and nothing else.
//! Without them (an older Desktop, the API switched off, a program started
//! by hand) there is no link: [`Desktop::from_env`] is None.
//!
//! The API is plain HTTP/1.1 on 127.0.0.1, one JSON request per connection,
//! so this speaks it over `std::net` and links no HTTP client.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use serde_json::{Value, json};

/// How long a listing or a focus may take.
const QUICK: Duration = Duration::from_secs(10);
/// How long a launch may take: Desktop answers once Houdini is spawned,
/// after it has prepared the project's packages.
const LAUNCH: Duration = Duration::from_secs(600);

/// The link to the Desktop that started this program.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Desktop {
    addr: SocketAddr,
    host: String,
    project_id: String,
    token: String,
}

/// One of the project's Houdini sessions (`sessions_list`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Session {
    pub id: String,
    pub pid: Option<u32>,
    /// `starting`, `running`, `unresponsive`, `bridge_unavailable`,
    /// `exited` or `crashed`.
    pub state: String,
    /// The scene it has open, as Houdini reports it; None until its bridge
    /// has checked in.
    pub hip_file: Option<String>,
}

impl Session {
    /// Still a Houdini on screen (not exited or crashed).
    pub fn is_live(&self) -> bool {
        !matches!(self.state.as_str(), "exited" | "crashed")
    }
}

/// What opening a workfile did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Opened {
    /// A session already had it open, and was raised.
    Raised(String),
    /// A new Houdini was launched on it.
    Launched(String),
}

/// A path as two programs on one machine may spell it: forward slashes,
/// no trailing slash, case folded on Windows.
pub fn same_file_key(path: &str) -> String {
    let slashed = path.replace('\\', "/");
    let trimmed = slashed.trim_end_matches('/');
    if cfg!(windows) { trimmed.to_lowercase() } else { trimmed.to_string() }
}

/// The live session that has `hip` open, if any: the one to raise instead
/// of opening the workfile a second time.
pub fn session_with<'a>(sessions: &'a [Session], hip: &Path) -> Option<&'a Session> {
    let want = same_file_key(&hip.to_string_lossy());
    sessions.iter().find(|s| s.is_live() && s.hip_file.as_deref().is_some_and(|h| same_file_key(h) == want))
}

fn parse_url(url: &str) -> Option<(SocketAddr, String)> {
    let host = url.trim().strip_prefix("http://")?.trim_end_matches('/');
    let addr = host.to_socket_addrs().ok()?.find(|a| a.ip().is_loopback())?;
    Some((addr, host.to_string()))
}

/// The body of an HTTP/1.1 reply read to its end, and its status.
fn parse_reply(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n").ok_or("Desktop sent no reply")?;
    let head = String::from_utf8_lossy(&raw[..split]);
    let status = head.split_whitespace().nth(1).and_then(|s| s.parse::<u16>().ok()).ok_or("Desktop sent no status")?;
    let body = &raw[split + 4..];
    let chunked = head.lines().any(|l| {
        let l = l.to_ascii_lowercase();
        l.starts_with("transfer-encoding:") && l.contains("chunked")
    });
    if !chunked {
        return Ok((status, body.to_vec()));
    }
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let line_end = rest.windows(2).position(|w| w == b"\r\n").ok_or("Desktop sent a broken reply")?;
        let size_text = String::from_utf8_lossy(&rest[..line_end]);
        let size =
            usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16).map_err(|_| "Desktop sent a broken reply")?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            return Ok((status, out));
        }
        if rest.len() < size + 2 {
            return Err("Desktop's reply was cut short".into());
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
}

impl Desktop {
    /// The link the environment names; None when any part is missing.
    pub fn from_env() -> Option<Self> {
        let var = |key: &str| std::env::var(key).ok().filter(|v| !v.trim().is_empty());
        Self::new(&var("TT_DESKTOP_URL")?, &var("TT_DESKTOP_PROJECT_ID")?, &var("TT_APP_TOKEN")?)
    }

    /// The link to the API at `url` (loopback only) for `project_id`.
    pub fn new(url: &str, project_id: &str, token: &str) -> Option<Self> {
        let (addr, host) = parse_url(url)?;
        Some(Desktop { addr, host, project_id: project_id.to_string(), token: token.to_string() })
    }

    pub fn project_id(&self) -> &str {
        &self.project_id
    }

    fn invoke(&self, command: &str, args: Value, patience: Duration) -> Result<Value, String> {
        let unreachable = |e: std::io::Error| format!("TumbleTrove Desktop is not answering ({e}); is it running?");
        let mut stream = TcpStream::connect_timeout(&self.addr, Duration::from_secs(3)).map_err(unreachable)?;
        stream.set_read_timeout(Some(patience)).map_err(unreachable)?;
        let body = json!({ "command": command, "args": args }).to_string();
        let request = format!(
            "POST /v1/invoke HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nX-TT-Project: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            self.host,
            self.token,
            self.project_id,
            body.len(),
            body
        );
        stream.write_all(request.as_bytes()).map_err(unreachable)?;
        let mut raw = Vec::new();
        stream.read_to_end(&mut raw).map_err(unreachable)?;
        let (status, body) = parse_reply(&raw)?;
        let value: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
        if (200..300).contains(&status) {
            return Ok(value);
        }
        let message = value.pointer("/error/message").and_then(Value::as_str).unwrap_or("no reason given");
        Err(format!("TumbleTrove Desktop refused {command} ({status}): {message}"))
    }

    /// The project's Houdini sessions, live and recently ended.
    pub fn sessions(&self) -> Result<Vec<Session>, String> {
        let rows = self.invoke("sessions_list", json!({}), QUICK)?;
        let text = |row: &Value, key: &str| row.get(key).and_then(Value::as_str).map(str::to_string);
        Ok(rows
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .filter_map(|row| {
                Some(Session {
                    id: text(row, "sessionId")?,
                    pid: row.get("pid").and_then(Value::as_u64).and_then(|p| u32::try_from(p).ok()),
                    state: text(row, "state").unwrap_or_default(),
                    hip_file: text(row, "hipFile"),
                })
            })
            .collect())
    }

    /// Launch the project's Houdini on `scene`; the new session's id.
    pub fn launch(&self, scene: &Path) -> Result<String, String> {
        let scene = scene.to_string_lossy().replace('\\', "/");
        let result = self.invoke("launch_project", json!({ "project_id": self.project_id, "scene": scene }), LAUNCH)?;
        result
            .get("sessionId")
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| "this TumbleTrove Desktop is too old to open a workfile: update it".to_string())
    }

    /// Raise a session's Houdini window.
    pub fn focus(&self, session_id: &str) -> Result<(), String> {
        allow_foreground();
        self.invoke("sessions_focus", json!({ "session_id": session_id }), QUICK).map(|_| ())
    }

    /// Open `hip` in Houdini: raise the session that has it open, else
    /// launch a new one on it.
    pub fn open_workfile(&self, hip: &Path) -> Result<Opened, String> {
        let sessions = self.sessions()?;
        if let Some(session) = session_with(&sessions, hip) {
            self.focus(&session.id)?;
            return Ok(Opened::Raised(session.id.clone()));
        }
        self.launch(hip).map(Opened::Launched)
    }
}

/// Let another process take the foreground. Windows only lets the
/// foreground process (this one, the user having just clicked in it) hand
/// the foreground on; without this Desktop's raise of Houdini only flashes
/// its taskbar button.
#[cfg(windows)]
fn allow_foreground() {
    #[link(name = "user32")]
    unsafe extern "system" {
        fn AllowSetForegroundWindow(process_id: u32) -> i32;
    }
    const ASFW_ANY: u32 = u32::MAX;
    // SAFETY: a plain Win32 call taking one integer; failure is a zero
    // return and leaves nothing to clean up.
    unsafe {
        AllowSetForegroundWindow(ASFW_ANY);
    }
}

#[cfg(not(windows))]
fn allow_foreground() {}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::net::TcpListener;
    use std::sync::mpsc;

    /// A one-shot stand-in for Desktop: answers `replies` in order, and
    /// sends back each request it read.
    fn stub(replies: Vec<(u16, Value, bool)>) -> (Desktop, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for (status, body, chunked) in replies {
                let (mut stream, _) = listener.accept().unwrap();
                let mut seen = Vec::new();
                let mut buffer = [0u8; 4096];
                loop {
                    let n = stream.read(&mut buffer).unwrap();
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
                tx.send(String::from_utf8_lossy(&seen).to_string()).unwrap();
                let body = body.to_string();
                let reply = if chunked {
                    let (a, b) = body.split_at(body.len() / 2);
                    format!(
                        "HTTP/1.1 {status} X\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{a}\r\n{:x}\r\n{b}\r\n0\r\n\r\n",
                        a.len(),
                        b.len()
                    )
                } else {
                    format!("HTTP/1.1 {status} X\r\nContent-Length: {}\r\n\r\n{body}", body.len())
                };
                stream.write_all(reply.as_bytes()).unwrap();
            }
        });
        (Desktop::new(&format!("http://127.0.0.1:{port}"), "proj-1", "tok").unwrap(), rx)
    }

    fn row(id: &str, state: &str, hip: Option<&str>) -> Value {
        json!({"sessionId": id, "pid": 42, "state": state, "projectId": "proj-1", "hipFile": hip})
    }

    #[test]
    fn only_loopback_urls() {
        assert!(Desktop::new("http://127.0.0.1:18925", "p", "t").is_some());
        assert!(Desktop::new("http://127.0.0.1:18925/", "p", "t").is_some());
        assert!(Desktop::new("https://127.0.0.1:18925", "p", "t").is_none());
        assert!(Desktop::new("http://8.8.8.8:80", "p", "t").is_none());
    }

    #[test]
    fn a_request_carries_the_token_and_project() {
        let (desktop, seen) = stub(vec![(200, json!([row("s1", "running", Some("C:/a/b_v0001.hip"))]), false)]);
        let sessions = desktop.sessions().unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].pid, Some(42));
        let request = seen.recv().unwrap();
        assert!(request.starts_with("POST /v1/invoke HTTP/1.1\r\n"));
        assert!(request.contains("Authorization: Bearer tok\r\n"));
        assert!(request.contains("X-TT-Project: proj-1\r\n"));
        assert!(!request.to_ascii_lowercase().contains("origin:"));
        assert!(
            request.ends_with(r#"{"args":{},"command":"sessions_list"}"#) || request.ends_with(r#"{"command":"sessions_list","args":{}}"#)
        );
    }

    #[test]
    fn a_refusal_reads_as_its_message() {
        let (desktop, _seen) = stub(vec![(403, json!({"error": {"message": "an app token launches only its own project"}}), true)]);
        let error = desktop.launch(Path::new("C:/a/b_v0001.hip")).unwrap_err();
        assert!(error.contains("403") && error.contains("only its own project"), "{error}");
    }

    #[test]
    fn an_old_desktop_is_named() {
        let (desktop, _seen) = stub(vec![(200, json!({"processId": 7}), false)]);
        assert!(desktop.launch(Path::new("C:/a/b_v0001.hip")).unwrap_err().contains("too old"));
    }

    #[test]
    fn nothing_listening_is_an_error_not_a_hang() {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        let desktop = Desktop::new(&format!("http://127.0.0.1:{port}"), "p", "t").unwrap();
        assert!(desktop.sessions().unwrap_err().contains("not answering"));
    }

    proptest! {
        /// Opening a workfile raises the live session that has it (however
        /// the path is spelled) and otherwise launches; never both.
        #[test]
        fn open_raises_or_launches(open_at in proptest::option::of(0usize..3), ended in any::<bool>(), backslashes in any::<bool>(), chunked in any::<bool>()) {
            let hip = "C:/show/shots/A/010/animation/A_010_animation_v0003.hip";
            let rows: Vec<Value> = (0..3).map(|i| {
                let has = open_at == Some(i);
                let spelled = if backslashes { hip.replace('/', "\\") } else { hip.to_string() };
                row(&format!("s{i}"), if has && ended { "exited" } else { "running" }, Some(if has { spelled.as_str() } else { "C:/other.hip" }))
            }).collect();
            let raise = open_at.is_some() && !ended;
            let second = if raise { (200, Value::Null, chunked) } else { (200, json!({"processId": 9, "sessionId": "new"}), chunked) };
            let (desktop, seen) = stub(vec![(200, Value::Array(rows), chunked), second]);
            let opened = desktop.open_workfile(Path::new(hip)).unwrap();
            let _ = seen.recv().unwrap();
            let request = seen.recv().unwrap();
            if raise {
                prop_assert_eq!(opened, Opened::Raised(format!("s{}", open_at.unwrap())));
                prop_assert!(request.contains("sessions_focus"));
            } else {
                prop_assert_eq!(opened, Opened::Launched("new".into()));
                prop_assert!(request.contains("launch_project") && request.contains(hip));
            }
        }
    }
}
