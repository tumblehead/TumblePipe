//! Live playblasts as the player reads them (`tumblepipe.pipe.live`).
//!
//! Houdini renders an animator's shot in the background into a folder on
//! this machine's disk, one per project, shot and department, and keeps a
//! `live.json` beside the frames saying what is rendered, the run, the
//! frames this run has finished and its state. Python writes it; this reads
//! it, with the same names for the folders, and the same reading of a
//! "rendering" whose husk is gone (its Houdini was killed): settled from the
//! frames on disk this run wrote, done if all of them, else cancelled. Only
//! Python writes live.json: [`effective_state`] settles what it returns and
//! leaves the file alone.
//!
//! A live take is never frozen into a publish; the editor plays it over the
//! shot's rule (see `cinedex`).

use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde::{Deserialize, Serialize};
use th_playlist::{Frames, Media, Take};

use crate::Project;
use crate::departments::list_departments;
use crate::uri::Uri;

pub const STATE_FILE: &str = "live.json";
pub const FRAME_PREFIX: &str = "frame.";
pub const FRAME_SUFFIX: &str = ".jpg";
pub const FRAME_PADDING: usize = 4;
/// A live take's version, in place of a published one's `vNNNN`.
pub const LIVE_VERSION: &str = "live";

pub const RENDERING: &str = "rendering";
pub const DONE: &str = "done";
pub const FAILED: &str = "failed";
pub const CANCELLED: &str = "cancelled";

// ── where ───────────────────────────────────────────────────

/// The folder every live playblast of this user goes under:
/// `%LOCALAPPDATA%/tumblepipe/live` on Windows, `$XDG_STATE_HOME` (or
/// `~/.local/state`) on Linux, `~/Library/Application Support` on macOS.
/// `var` reads the environment; `platform` is Python's `sys.platform`.
pub fn live_root(var: impl Fn(&str) -> Option<String>, platform: &str) -> PathBuf {
    let set = |name: &str| var(name).filter(|v| !v.is_empty());
    // Neither set: this user's home, as Python's Path.home().
    let home = set("HOME").or_else(|| set("USERPROFILE")).map(PathBuf::from).or_else(std::env::home_dir).unwrap_or_default();
    let base = if platform.starts_with("win") {
        set("LOCALAPPDATA").map_or_else(|| home.join("AppData").join("Local"), PathBuf::from)
    } else if platform == "darwin" {
        home.join("Library").join("Application Support")
    } else {
        set("XDG_STATE_HOME").map_or_else(|| home.join(".local").join("state"), PathBuf::from)
    };
    base.join("tumblepipe").join("live")
}

/// This process's [`live_root`].
pub fn this_live_root() -> PathBuf {
    let platform = if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    };
    live_root(|name| std::env::var(name).ok(), platform)
}

/// A short name for a project's folder under the live root, the same
/// whichever way its path is spelled (slashes, a trailing one, case).
pub fn project_key(project_path: &str) -> String {
    let spelled = project_path.replace('\\', "/");
    let spelled = spelled.trim_end_matches('/').to_lowercase();
    sha1_hex(spelled.as_bytes())[..12].to_string()
}

/// `SQ010_SH020_animation` for entity:/shots/SQ010/SH020 in animation: the
/// shot's segments after its context, then the department.
pub fn folder_name(uri_segments: &[String], department: &str) -> Result<String, String> {
    let segments = uri_segments.get(1..).unwrap_or_default();
    if segments.is_empty() || department.is_empty() {
        return Err("a live playblast needs a shot and a department".into());
    }
    Ok([segments, &[department.to_string()]].concat().join("_"))
}

pub fn live_folder(root: &Path, project_path: &str, uri_segments: &[String], department: &str) -> Result<PathBuf, String> {
    Ok(root.join(project_key(project_path)).join(folder_name(uri_segments, department)?))
}

pub fn frame_file(folder: &Path, frame: i64) -> PathBuf {
    folder.join(format!("{FRAME_PREFIX}{frame:0FRAME_PADDING$}{FRAME_SUFFIX}"))
}

// ── the players Houdini can reach ───────────────────────────

/// The folder under the live root where each running player of a project
/// says how to reach it (Live view: Houdini asks one to show the shot it
/// renders live). Beside the projects' live folders, not in one: a player
/// running must not make a project look as if it had live renders.
pub const PLAYERS_DIR: &str = "players";

/// The JSON-RPC version the players' endpoint speaks.
pub const PLAYER_PROTOCOL: u32 = 1;

/// Where the players of the project at `project_path` say how to reach
/// them: one `<pid>.json` each.
pub fn players_dir(root: &Path, project_path: &str) -> PathBuf {
    root.join(PLAYERS_DIR).join(project_key(project_path))
}

/// How a running player is reached: a line of JSON-RPC 2.0 to
/// `127.0.0.1:port`, its params carrying `token` (only this user can read
/// the file, so only this user's programs can talk to the player).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlayerEndpoint {
    pub pid: u32,
    pub port: u16,
    pub token: String,
    pub protocol: u32,
}

/// `endpoint`'s file in `dir`, written whole (temp + rename), as live.json
/// is, so a reader never sees half of it.
pub fn write_endpoint(dir: &Path, endpoint: &PlayerEndpoint) -> std::io::Result<PathBuf> {
    std::fs::create_dir_all(dir)?;
    let path = dir.join(format!("{}.json", endpoint.pid));
    let temp = dir.join(format!("{}.json.tmp", endpoint.pid));
    std::fs::write(&temp, serde_json::to_vec(endpoint).map_err(std::io::Error::other)?)?;
    std::fs::rename(&temp, &path)?;
    Ok(path)
}

// ── live.json ───────────────────────────────────────────────

/// What live.json says. Python may add to it; what this does not read it
/// keeps in `rest`, so nothing is lost on the way through.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LiveState {
    pub uri: String,
    pub department: String,
    /// The shot's frames: first and last.
    pub range: [i64; 2],
    #[serde(default = "one")]
    pub step: i64,
    pub fps: f64,
    pub run: u64,
    /// When the run started, in seconds since the epoch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<f64>,
    #[serde(default)]
    pub order: Vec<i64>,
    #[serde(default)]
    pub done: Vec<i64>,
    pub state: String,
    /// The husk rendering it, so a reader can tell whether it still is
    /// (absent from a live.json written before husks were named).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub program: Option<String>,
    /// Houdini's hffmpeg, beside the husk that renders it: what Keep as
    /// playblast encodes the frames with (the player's own FFmpeg has no
    /// encoder). Absent from a live.json written before it was named.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hffmpeg: Option<String>,
    #[serde(flatten)]
    pub rest: serde_json::Map<String, serde_json::Value>,
}

fn one() -> i64 {
    1
}

impl LiveState {
    /// Its frames as files: frame `n` is `folder/frame.NNNN.jpg`, from the
    /// shot's first to its last by its step.
    pub fn frames(&self, folder: &Path) -> Frames {
        Frames {
            directory: crate::storage::slashed(folder),
            prefix: FRAME_PREFIX.into(),
            suffix: FRAME_SUFFIX.into(),
            padding: FRAME_PADDING,
            first: self.range[0],
            last: self.range[1],
            step: self.step.max(1),
        }
    }
}

/// live.json in `folder`; None when there is none, or it does not read
/// (it is written by a rename, so a reader never sees half of it).
pub fn read_state(folder: &Path) -> Option<LiveState> {
    let bytes = std::fs::read(folder.join(STATE_FILE)).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// When live.json in `folder` was last written; None when there is none.
pub fn state_written(folder: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(folder.join(STATE_FILE)).and_then(|m| m.modified()).ok()
}

/// The frames of `order` a run that started at `since` has written: their
/// file exists and is no older than the run's start.
pub fn done_frames(folder: &Path, order: &[i64], since: f64) -> Vec<i64> {
    order
        .iter()
        .copied()
        .filter(|&frame| {
            std::fs::metadata(frame_file(folder, frame))
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .is_some_and(|d| d.as_secs_f64() >= since)
        })
        .collect()
}

/// `state` as it stands: a "rendering" whose husk is gone settled from the
/// frames on disk (as Python's `settle`, without writing it back).
pub fn effective_state(folder: &Path, state: LiveState) -> LiveState {
    if state.state != RENDERING || husk_alive(&state) {
        return state;
    }
    let done = done_frames(folder, &state.order, state.started_at.unwrap_or(f64::INFINITY));
    let outcome = if !state.order.is_empty() && done.len() == state.order.len() { DONE } else { CANCELLED };
    LiveState { done, state: outcome.into(), ..state }
}

// ── the husk ────────────────────────────────────────────────

/// How live.json names a program: `husk` for .../bin/husk.exe.
pub fn program_name(path: &str) -> String {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
    let stem = match name.rfind('.') {
        Some(i) if i > 0 => &name[..i],
        _ => name,
    };
    stem.to_lowercase()
}

/// Whether the husk live.json names is still running: a process with its
/// pid running its program (a pid since given to something else does not
/// count).
pub fn husk_alive(state: &LiveState) -> bool {
    match (state.pid, &state.program) {
        (Some(pid), Some(program)) if !program.is_empty() => {
            u32::try_from(pid).ok().filter(|&p| p > 0).and_then(process_program).as_deref() == Some(program.as_str())
        }
        _ => false,
    }
}

/// The program process `pid` runs, as [`program_name`] names it; None when
/// no process has that pid, or it has ended.
#[cfg(windows)]
pub fn process_program(pid: u32) -> Option<String> {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn GetExitCodeProcess(process: *mut c_void, code: *mut u32) -> i32;
        fn QueryFullProcessImageNameW(process: *mut c_void, flags: u32, name: *mut u16, size: *mut u32) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    const QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    // SAFETY: plain Win32 calls on a handle this function opens and closes;
    // the name buffer and its size are this function's own.
    unsafe {
        let handle = OpenProcess(QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut code = 0u32;
        let mut name = vec![0u16; 32768];
        let mut size = name.len() as u32;
        let alive = GetExitCodeProcess(handle, &mut code) != 0 && code == STILL_ACTIVE;
        let named = alive && QueryFullProcessImageNameW(handle, 0, name.as_mut_ptr(), &mut size) != 0;
        CloseHandle(handle);
        named.then(|| program_name(&String::from_utf16_lossy(&name[..size as usize])))
    }
}

#[cfg(not(windows))]
pub fn process_program(pid: u32) -> Option<String> {
    let proc = PathBuf::from(format!("/proc/{pid}"));
    if proc.is_dir() {
        let stat = std::fs::read_to_string(proc.join("stat")).ok()?;
        if stat.rsplit_once(')')?.1.split_whitespace().next()? == "Z" {
            return None; // ended, not yet reaped
        }
        return std::fs::read_link(proc.join("exe")).ok().map(|exe| program_name(&exe.to_string_lossy()));
    }
    let out = std::process::Command::new("ps").args(["-p", &pid.to_string(), "-o", "stat=,comm="]).output().ok()?;
    let out = String::from_utf8_lossy(&out.stdout);
    let (stat, command) = out.trim().split_once(char::is_whitespace)?;
    (!stat.starts_with('Z')).then(|| program_name(command.trim()))
}

// ── a shot's live takes ─────────────────────────────────────

/// A shot's live takes on this machine under `root`: one per department
/// whose folder has a live.json for this shot, as it stands, with the
/// folder. A live take's frames may still be on their way.
pub fn live_takes(project: &Project, entity: &Uri, root: &Path) -> Result<Vec<(Take, LiveState, PathBuf)>, String> {
    let project_path = crate::storage::slashed(&project.root);
    let mut takes = Vec::new();
    for department in list_departments(&project.config, "shots", true, true)?.into_iter().map(|d| d.name) {
        let Ok(folder) = live_folder(root, &project_path, &entity.segments, &department) else { continue };
        let Some(state) = read_state(&folder) else { continue };
        if state.uri != entity.to_string() || state.department != department || state.range[1] < state.range[0] {
            continue;
        }
        let state = effective_state(&folder, state);
        let take = Take {
            label: format!("{department} live"),
            department: department.clone(),
            version: LIVE_VERSION.into(),
            media: Media::Live { frames: state.frames(&folder) },
            multi_range: false,
        };
        takes.push((take, state, folder));
    }
    Ok(takes)
}

// ── Keep as playblast ───────────────────────────────────────

/// What a playblast movie's frames are, as `apps.mp4._encode_sequence`
/// fills a stepped or gappy render: for each frame of `first..=last`, the
/// frame of `available` it shows, the nearest at or before it, else the
/// first there is. Empty when nothing is available.
pub fn fill_plan(first: i64, last: i64, available: &[i64]) -> Vec<(i64, i64)> {
    let mut sorted = available.to_vec();
    sorted.sort_unstable();
    sorted.dedup();
    let Some(&lowest) = sorted.first() else { return Vec::new() };
    (first..=last)
        .map(|frame| {
            let at = sorted.partition_point(|&a| a <= frame);
            (frame, if at == 0 { lowest } else { sorted[at - 1] })
        })
        .collect()
}

/// `frame` as a non-drop SMPTE timecode at `framerate` (`apps.mp4.frame_timecode`).
pub fn frame_timecode(frame: i64, framerate: i64) -> Result<String, String> {
    if frame < 0 || framerate <= 0 {
        return Err(format!("No timecode for frame {frame} at {framerate} fps"));
    }
    let (seconds, frames) = (frame / framerate, frame % framerate);
    let (minutes, seconds) = (seconds / 60, seconds % 60);
    let (hours, minutes) = (minutes / 60, minutes % 60);
    Ok(format!("{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"))
}

/// hffmpeg's arguments that encode the JPG frames `pattern` names
/// (`frame.%04d.jpg`), `first..=last`, at `framerate` into `output`: the
/// playblast movie every playblast is (`apps.mp4.from_jpg`: H.264 through
/// openh264, sizes padded even, the first frame recorded as `start_frame`
/// and a timecode track).
pub fn keep_args(pattern: &str, first: i64, last: i64, framerate: i64, output: &str) -> Result<Vec<String>, String> {
    let timecode = frame_timecode(first, framerate)?;
    let args = [
        "-y",
        "-framerate",
        &framerate.to_string(),
        "-pattern_type",
        "sequence",
        "-start_number",
        &first.to_string(),
        "-i",
        pattern,
        "-frames:v",
        &(last - first + 1).to_string(),
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v",
        "libopenh264",
        "-rc_mode",
        "quality",
        "-q:v",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "use_metadata_tags",
        "-metadata",
        &format!("start_frame={first}"),
        "-timecode",
        &timecode,
        output,
    ];
    Ok(args.into_iter().map(str::to_owned).collect())
}

/// Why the live render in `state` cannot be kept as a playblast; None when
/// it can (rendered to the end, with Houdini's hffmpeg named).
pub fn keep_refusal(state: &LiveState) -> Option<String> {
    if state.state != DONE {
        return Some(format!("the live render is {}, not done", state.state));
    }
    if state.hffmpeg.as_deref().is_none_or(str::is_empty) {
        return Some("this live render does not name Houdini's hffmpeg (render it again with this TumblePipe)".into());
    }
    None
}

/// Keep the live render in `folder` as the next playblast version of its
/// shot and department, and as its daily, as a playblast made in Houdini
/// is: its frames encoded with Houdini's hffmpeg, no render again. The
/// version is reserved while the movie is made (a farm playblast submitted
/// meanwhile takes the one after), and the new playblast is announced on
/// the change journal, as every playblast is. Returns the playblast written.
pub fn keep(project: &Project, folder: &Path) -> Result<PathBuf, String> {
    let state = read_state(folder).ok_or("no live render here")?;
    let state = effective_state(folder, state);
    if let Some(why) = keep_refusal(&state) {
        return Err(why);
    }
    let entity = Uri::parse(&state.uri).map_err(|e| e.to_string())?;
    let (first, last) = (state.range[0], state.range[1]);
    let plan = fill_plan(first, last, &state.done);
    if plan.is_empty() {
        return Err("the live render has no frames".into());
    }
    let framerate = state.fps.round() as i64;
    let work = tempfile::Builder::new().prefix("keep-").tempdir_in(folder).map_err(|e| format!("{}: {e}", folder.display()))?;
    for &(frame, source) in &plan {
        let to = work.path().join(format!("frame.{frame:04}.jpg"));
        std::fs::copy(frame_file(folder, source), &to).map_err(|e| format!("frame {source}: {e}"))?;
    }
    let movie = work.path().join("output.mp4");
    let pattern = work.path().join("frame.%04d.jpg");
    let args = keep_args(&pattern.to_string_lossy(), first, last, framerate, &movie.to_string_lossy())?;

    let playblast = crate::discovery::next_playblast_path(project, &entity, &state.department)?;
    let daily = crate::discovery::daily_path(project, &entity, &state.department)?;
    let marker = crate::discovery::reservation_path(&playblast);
    let parent = playblast.parent().ok_or("a playblast path with no folder")?;
    std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    std::fs::write(&marker, b"").map_err(|e| format!("{}: {e}", marker.display()))?;
    let written = (|| {
        encode(state.hffmpeg.as_deref().unwrap_or_default(), &args)?;
        std::fs::copy(&movie, &playblast).map_err(|e| format!("{}: {e}", playblast.display()))?;
        if let Some(parent) = daily.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        std::fs::copy(&movie, &daily).map_err(|e| format!("{}: {e}", daily.display()))?;
        Ok(playblast.clone())
    })();
    let _ = std::fs::remove_file(&marker);
    if written.is_ok() {
        // This machine's editors (and this one) resolve the shot again, and
        // through the shared journal every other machine's (#78), under one
        // id. Off the UI thread: the editor keeps in the background.
        let id = crate::shared_journal::new_id();
        crate::events::announce_as(&project.config_path, crate::events::PLAYBLAST, &state.uri, &state.department, &id);
        crate::shared_journal::announce(&project.config_path, crate::events::PLAYBLAST, &state.uri, &state.department, &id);
    }
    written
}

/// Run `hffmpeg` with `args`, no console window; its last words when it fails.
fn encode(hffmpeg: &str, args: &[String]) -> Result<(), String> {
    let mut command = std::process::Command::new(hffmpeg);
    command.args(args).stdin(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let output = command.output().map_err(|e| format!("{hffmpeg}: {e}"))?;
    if output.status.success() {
        return Ok(());
    }
    let words = String::from_utf8_lossy(&output.stderr);
    let tail: Vec<&str> = words.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect();
    Err(format!("hffmpeg failed ({}): {}", output.status, tail.join(" / ")))
}

// ── SHA-1, for the folder name Python's hashlib gives ───────

fn sha1_hex(data: &[u8]) -> String {
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let mut message = data.to_vec();
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    for block in message.as_chunks::<64>().0 {
        let mut w = [0u32; 80];
        for (i, word) in block.as_chunks::<4>().0.iter().enumerate() {
            w[i] = u32::from_be_bytes(*word);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, &word) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A827999),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let t = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(word);
            (e, d, c, b, a) = (d, c, b.rotate_left(30), a, t);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e]) {
            *x = x.wrapping_add(y);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn sha1_known_answers() {
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(sha1_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"), "84983e441c3bd26ebaae4aa1f95129e5e54670f1");
        assert_eq!(sha1_hex(&[b'a'; 1000]), "291e9a6c66994949b57ba5e650361e98fc36b1ba");
    }

    #[test]
    fn program_names() {
        assert_eq!(program_name("C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\bin\\husk.exe"), "husk");
        assert_eq!(program_name("/opt/hfs22.0/bin/husk"), "husk");
        assert_eq!(program_name("Python.EXE"), "python");
        assert_eq!(program_name(".hidden"), ".hidden");
    }

    #[test]
    fn this_process_is_alive_and_named() {
        let me = std::env::current_exe().unwrap();
        let state = |program: &str| LiveState {
            uri: String::new(),
            department: String::new(),
            range: [1, 1],
            step: 1,
            fps: 24.0,
            run: 1,
            started_at: None,
            order: vec![],
            done: vec![],
            state: RENDERING.into(),
            pid: Some(i64::from(std::process::id())),
            program: Some(program.into()),
            hffmpeg: None,
            rest: Default::default(),
        };
        assert!(husk_alive(&state(&program_name(&me.to_string_lossy()))));
        assert!(!husk_alive(&state("husk")), "a pid running another program counts as gone");
        assert!(!husk_alive(&LiveState { pid: None, ..state("husk") }));
    }

    proptest! {
        #[test]
        fn one_project_one_key_however_spelled(backslashes: bool, trailing: bool, upper: bool) {
            let path = "C:/Users/ann/projects/HideAndReek";
            let mut spelled = if backslashes { path.replace('/', "\\") } else { path.to_string() };
            if trailing {
                spelled.push(if backslashes { '\\' } else { '/' });
            }
            if upper {
                spelled = spelled.to_uppercase();
            }
            prop_assert_eq!(project_key(&spelled), project_key(path));
            prop_assert_ne!(project_key(path), project_key("C:/Users/ann/projects/HideAndReek2"));
        }
    }

    /// `apps.mp4._encode_sequence`'s filling, as Python writes it: frames
    /// before the first rendered one copy it, every later gap copies the
    /// frame before.
    fn python_fill(first: i64, last: i64, available: &[i64]) -> Vec<(i64, i64)> {
        let set: std::collections::BTreeSet<i64> = available.iter().copied().collect();
        let Some(&first_available) = set.first() else { return Vec::new() };
        let mut plan = std::collections::BTreeMap::new();
        for frame in first..first_available {
            plan.insert(frame, first_available);
        }
        for &frame in &set {
            plan.insert(frame, frame);
        }
        let mut previous = first_available;
        for frame in first_available + 1..=last {
            if set.contains(&frame) {
                previous = frame;
            } else {
                plan.insert(frame, previous);
            }
        }
        plan.into_iter().filter(|&(f, _)| f >= first && f <= last).collect()
    }

    proptest! {
        /// The kept movie's frames are the ones Python's encoder would put
        /// there, whatever frames are rendered (a step, gaps, frames off the
        /// range), one per frame of the range.
        #[test]
        fn a_kept_movies_frames_are_filled_as_python_fills_them(
            first in 990i64..1010,
            length in 0i64..30,
            available in prop::collection::vec(980i64..1050, 1..20),
        ) {
            let last = first + length;
            let plan = fill_plan(first, last, &available);
            prop_assert_eq!(plan.len() as i64, length + 1);
            prop_assert_eq!(&plan, &python_fill(first, last, &available));
            prop_assert!(plan.iter().all(|(_, source)| available.contains(source)));
        }
    }

    fn done_state(folder: &Path, hffmpeg: Option<String>, frames: std::ops::RangeInclusive<i64>) -> LiveState {
        let order: Vec<i64> = frames.clone().collect();
        let state = LiveState {
            uri: "entity:/shots/SQ010/SH020".into(),
            department: "animation".into(),
            range: [*frames.start(), *frames.end()],
            step: 1,
            fps: 24.0,
            run: 1,
            started_at: Some(0.0),
            order: order.clone(),
            done: order,
            state: DONE.into(),
            pid: None,
            program: None,
            hffmpeg,
            rest: Default::default(),
        };
        std::fs::create_dir_all(folder).unwrap();
        std::fs::write(folder.join(STATE_FILE), serde_json::to_vec(&state).unwrap()).unwrap();
        state
    }

    /// Only a render that is done, made by a Houdini that named its
    /// hffmpeg, can be kept.
    #[test]
    fn only_a_finished_render_naming_its_hffmpeg_is_kept() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = done_state(dir.path(), Some("hffmpeg".into()), 1001..=1003);
        assert_eq!(keep_refusal(&state), None);
        state.state = CANCELLED.into();
        assert!(keep_refusal(&state).unwrap().contains("cancelled"));
        state.state = DONE.into();
        state.hffmpeg = None;
        assert!(keep_refusal(&state).unwrap().contains("hffmpeg"));
    }

    /// The journal lines this thread's announces wrote while `f` ran, as
    /// `(kind, uri, detail)`.
    fn announced<T>(f: impl FnOnce() -> T) -> (T, Vec<(String, String, String)>) {
        let dir = tempfile::tempdir().unwrap();
        let journal = dir.path().join(crate::events::JOURNAL_NAME);
        crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = Some(journal.clone()));
        let result = f();
        crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = None);
        let lines = std::fs::read(&journal).unwrap_or_default();
        let got = lines.split(|&b| b == 10).skip(1).filter_map(crate::events::parse).map(|c| (c.kind, c.uri, c.detail)).collect();
        (result, got)
    }

    /// A shared journal change as `(kind, uri, detail, id)`.
    type SharedLine = (String, String, String, String);

    /// The ids of this thread's local journal lines and the shared
    /// journal's changes a keep wrote.
    fn announced_both<T>(config: &Path, f: impl FnOnce() -> T) -> (T, Vec<String>, Vec<SharedLine>) {
        let dir = tempfile::tempdir().unwrap();
        let journal = dir.path().join(crate::events::JOURNAL_NAME);
        crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = Some(journal.clone()));
        let result = f();
        crate::events::TEST_JOURNAL.with(|j| *j.borrow_mut() = None);
        let lines = std::fs::read(&journal).unwrap_or_default();
        let local = lines.split(|&b| b == 10).skip(1).filter_map(crate::events::parse).map(|c| c.id).collect();
        let shared_dir = crate::shared_journal::shared_dir(config);
        let mut reader = crate::shared_journal::SharedReader::new(shared_dir, 0);
        let shared = reader.poll().into_iter().map(|e| (e.kind, e.uri, e.detail, e.id)).collect();
        (result, local, shared)
    }

    /// An encoder that fails leaves no playblast, daily or reservation
    /// behind, says why, and announces nothing.
    #[test]
    fn a_failed_keep_leaves_nothing_behind() {
        let (_dir, root) = crate::testing::make_project();
        let project = Project::open(&root, &root).unwrap();
        let folder = root.join("live");
        done_state(&folder, Some(root.join("no-hffmpeg.exe").to_string_lossy().into_owned()), 1001..=1002);
        for frame in 1001..=1002 {
            std::fs::write(frame_file(&folder, frame), b"jpg").unwrap();
        }
        let entity = Uri::parse("entity:/shots/SQ010/SH020").unwrap();
        let next = crate::discovery::next_playblast_path(&project, &entity, "animation").unwrap();
        let (result, local, shared) = announced_both(&project.config_path, || keep(&project, &folder));
        let error = result.unwrap_err();
        assert!(error.contains("no-hffmpeg"), "{error}");
        assert_eq!(local, Vec::<String>::new(), "a failed keep announces nothing");
        assert_eq!(shared, vec![], "nor on the shared journal");
        assert!(!next.exists() && !crate::discovery::reservation_path(&next).exists());
        assert!(!crate::discovery::daily_path(&project, &entity, "animation").unwrap().exists());
        assert!(
            std::fs::read_dir(&folder).unwrap().flatten().all(|e| !e.file_name().to_string_lossy().starts_with("keep-")),
            "the work folder is gone"
        );
    }

    /// With a real hffmpeg (an installed Houdini's, or TH_TEST_HFFMPEG), a
    /// kept render is the next playblast version and the daily, a movie
    /// recording its first frame; the next keep takes the version after.
    #[test]
    fn a_kept_render_is_the_next_playblast_and_the_daily() {
        let found = std::env::var("TH_TEST_HFFMPEG").ok().map(PathBuf::from).or_else(|| {
            let sidefx = Path::new("C:/Program Files/Side Effects Software");
            let mut installs: Vec<PathBuf> = std::fs::read_dir(sidefx)
                .ok()?
                .flatten()
                .map(|e| e.path().join("bin").join("hffmpeg.exe"))
                .filter(|p| p.is_file())
                .collect();
            installs.sort();
            installs.pop()
        });
        let Some(hffmpeg) = found else {
            eprintln!("no hffmpeg here: the keep is not driven end to end");
            return;
        };
        let (_dir, root) = crate::testing::make_project();
        let project = Project::open(&root, &root).unwrap();
        let folder = root.join("live");
        done_state(&folder, Some(hffmpeg.to_string_lossy().into_owned()), 1001..=1004);
        let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("../media/tests/fixtures/tumbleview_husk.jpg");
        for frame in 1001..=1004 {
            std::fs::copy(&fixture, frame_file(&folder, frame)).unwrap();
        }
        let entity = Uri::parse("entity:/shots/SQ010/SH020").unwrap();
        let (first, lines) = announced(|| keep(&project, &folder));
        let first = first.unwrap();
        let line = (crate::events::PLAYBLAST.to_string(), entity.to_string(), "animation".to_string());
        assert_eq!(lines, vec![line], "a kept render announces its playblast");
        assert!(first.to_string_lossy().ends_with("SQ010_SH020_animation_v0001.mp4"), "{}", first.display());
        let daily = crate::discovery::daily_path(&project, &entity, "animation").unwrap();
        assert_eq!(std::fs::read(&first).unwrap(), std::fs::read(&daily).unwrap());
        assert!(!crate::discovery::reservation_path(&first).exists());
        assert_eq!(crate::discovery::list_playblast_versions(&project, &entity, "animation").unwrap(), ["v0001"]);
        // Every other machine hears of it on the shared journal, under the
        // local line's id (#78).
        let (second, local, shared) = announced_both(&project.config_path, || keep(&project, &folder));
        let second = second.unwrap();
        assert_eq!(local.len(), 1);
        assert!(local[0].len() == 32, "a change id on the local line: {local:?}");
        let mut lines = shared.into_iter().filter(|s| s.3 == local[0]);
        let shared = lines.next().expect("the shared journal has the keep");
        assert_eq!((shared.0, shared.1, shared.2), (crate::events::PLAYBLAST.to_string(), entity.to_string(), "animation".to_string()));
        assert!(second.to_string_lossy().ends_with("SQ010_SH020_animation_v0002.mp4"), "{}", second.display());
    }
}
