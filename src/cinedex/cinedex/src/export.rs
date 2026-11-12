//! File > Export movie… (#61): the edit's cut (its clips with the cut's
//! takes, fades and sound) made into one movie on this machine, where
//! Render Edit makes it on the farm. Like Render Edit it reads only the
//! edit document, so a view-only choice (View > Show) is not exported.
//!
//! The pictures and the mix are Render Edit's own (`render`: the editor's
//! picture of each frame, fitted into the edit's resolution, shown through
//! the display and view; the editor's mix). Only where they go differs:
//! each frame is streamed as raw RGB into Houdini's hffmpeg, which encodes
//! them as the farm does (`th_pipe::movie::edit_export_args`), instead of
//! being written as a PNG to encode afterwards. The editor's own FFmpeg
//! decodes only.
//!
//! What it exports is the edit as it is in the window, saved or not, at the
//! edit's rate and resolution, its start timecode on the timecode track,
//! through the display and view the viewer shows (the project's OCIO
//! default until another is picked). Live playblasts are never exported:
//! a shot shows its take. The movie is written beside its final name and
//! renamed over it when done, so a cancelled or failed export leaves
//! nothing behind and does not touch a file already there.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use th_edit::document::WorkingEdit;
use th_pipe::movie;

use crate::colour::Colour;
use crate::editing::Origin;
use crate::editor::ProjectPaths;
use crate::render;

/// How often the window hears of progress, at most.
const WAKE_EVERY: Duration = Duration::from_millis(100);

/// Where an export stands: written by the export's thread, read by the
/// window, and how the window asks it to stop.
#[derive(Debug, Default)]
pub struct Progress {
    phase: AtomicUsize,
    done: AtomicUsize,
    total: AtomicUsize,
    cancel: AtomicBool,
}

/// What an export is doing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// Resolving the takes and mixing the sound.
    Preparing,
    /// Rendering and encoding frames.
    Frames,
    /// The encoder finishing the movie.
    Finishing,
}

impl Progress {
    pub fn phase(&self) -> Phase {
        match self.phase.load(Ordering::Relaxed) {
            0 => Phase::Preparing,
            1 => Phase::Frames,
            _ => Phase::Finishing,
        }
    }

    fn set_phase(&self, phase: Phase) {
        self.phase.store(phase as usize, Ordering::Relaxed);
    }

    /// Frames encoded so far, and how many there are.
    pub fn frames(&self) -> (usize, usize) {
        (self.done.load(Ordering::Relaxed), self.total.load(Ordering::Relaxed))
    }

    /// How far along, 0 to 1.
    pub fn fraction(&self) -> f32 {
        let (done, total) = self.frames();
        if total == 0 { 0.0 } else { (done.min(total) as f32 / total as f32).clamp(0.0, 1.0) }
    }

    /// Ask the export to stop; it does at the next frame.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }

    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }
}

/// What to export, and how.
#[derive(Debug, Clone)]
pub struct Request {
    pub paths: ProjectPaths,
    /// The OCIO config the editor opened.
    pub ocio: String,
    pub edit: WorkingEdit,
    pub display: String,
    pub view: String,
    pub hffmpeg: PathBuf,
    pub output: PathBuf,
}

/// What an export wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Exported {
    pub path: PathBuf,
    pub frames: usize,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub audio: bool,
}

/// hffmpeg encoding into the partial movie. Dropped before it is
/// finished (an error, a cancel), it is stopped and the partial movie
/// deleted.
struct Encoder {
    child: Child,
    stdin: Option<ChildStdin>,
    stderr: Option<std::thread::JoinHandle<Vec<u8>>>,
    partial: PathBuf,
    finished: bool,
}

impl Encoder {
    fn start(hffmpeg: &Path, args: &[String], partial: PathBuf) -> Result<Self, String> {
        let mut command = Command::new(hffmpeg);
        command.args(args).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = command.spawn().map_err(|e| format!("{}: {e}", hffmpeg.display()))?;
        let stdin = child.stdin.take();
        // Read its words as it goes, or it blocks once the pipe is full.
        let stderr = child.stderr.take().map(|mut pipe| {
            std::thread::spawn(move || {
                let mut words = Vec::new();
                let _ = pipe.read_to_end(&mut words);
                words
            })
        });
        Ok(Self { child, stdin, stderr, partial, finished: false })
    }

    /// One frame's pixels; false when hffmpeg stopped taking them.
    fn frame(&mut self, rgb: &[u8]) -> bool {
        self.stdin.as_mut().is_some_and(|s| s.write_all(rgb).is_ok())
    }

    /// The last of what hffmpeg said.
    fn last_words(&mut self) -> String {
        let words = self.stderr.take().and_then(|t| t.join().ok()).unwrap_or_default();
        let words = String::from_utf8_lossy(&words);
        let tail: Vec<&str> = words.lines().filter(|l| !l.trim().is_empty()).rev().take(3).collect();
        tail.into_iter().rev().collect::<Vec<_>>().join(" / ")
    }

    /// Close its input and wait for the movie.
    fn finish(&mut self) -> Result<(), String> {
        drop(self.stdin.take());
        let status = self.child.wait().map_err(|e| format!("hffmpeg: {e}"))?;
        if !status.success() {
            return Err(format!("hffmpeg failed ({status}): {}", self.last_words()));
        }
        self.finished = true;
        Ok(())
    }
}

impl Drop for Encoder {
    fn drop(&mut self) {
        if !self.finished {
            drop(self.stdin.take());
            let _ = self.child.kill();
            let _ = self.child.wait();
            let _ = std::fs::remove_file(&self.partial);
        }
    }
}

/// Export `request`, reporting into `progress` and calling `wake` (at
/// most every [`WAKE_EVERY`]) when it moved. None when it was cancelled.
pub fn export(request: &Request, progress: &Progress, mut wake: impl FnMut()) -> Result<Option<Exported>, String> {
    let edit = &request.edit;
    let frames = edit.length().max(0) as usize;
    if frames == 0 {
        return Err("the edit is empty: there is nothing to export".into());
    }
    let fps = edit.fps.max(1);
    let [width, height] = edit.resolution;
    progress.total.store(frames, Ordering::Relaxed);
    progress.set_phase(Phase::Preparing);
    wake();

    let mut colour = Colour::open(&request.ocio).map_err(|e| format!("the OCIO config: {e}"))?;
    let mut sources = render::open_sources(&request.paths, &mut colour, &request.display, &request.view, edit)?;
    let work = tempfile::Builder::new().prefix("cinedex-export-").tempdir().map_err(|e| format!("a temporary folder: {e}"))?;
    let wav = work.path().join(format!("{}.wav", render::STEM));
    let audio = render::write_mix(edit, &sources, fps, frames, &wav)?;
    if progress.cancelled() {
        return Ok(None);
    }

    let partial = movie::partial_path(&request.output);
    let wav_arg = audio.then(|| wav.to_string_lossy().into_owned());
    let args = movie::edit_export_args(width, height, fps, frames, wav_arg.as_deref(), &edit.start_timecode, &partial.to_string_lossy())?;
    let mut encoder = Encoder::start(&request.hffmpeg, &args, partial.clone())?;
    progress.set_phase(Phase::Frames);
    let mut woke = Instant::now();
    for at in 0..frames {
        if progress.cancelled() {
            return Ok(None);
        }
        let rgb = render::rgb(&render::picture(edit, &mut sources, at)?)?;
        if !encoder.frame(&rgb) {
            // It stopped reading: it says why when it ends.
            return Err(encoder.finish().err().unwrap_or_else(|| format!("hffmpeg stopped at frame {at}")));
        }
        progress.done.store(at + 1, Ordering::Relaxed);
        // The first frame says the frames have begun; then now and then.
        if at == 0 || woke.elapsed() >= WAKE_EVERY {
            woke = Instant::now();
            wake();
        }
    }
    progress.set_phase(Phase::Finishing);
    wake();
    if progress.cancelled() {
        return Ok(None);
    }
    encoder.finish()?;
    if let Err(e) = std::fs::rename(&partial, &request.output) {
        let _ = std::fs::remove_file(&partial);
        return Err(format!("{}: {e}", request.output.display()));
    }
    Ok(Some(Exported { path: request.output.clone(), frames, width, height, fps, audio }))
}

/// The movie's file name until another is chosen: the edit's name and
/// version (`Main_v0003.mp4`), `edit.mp4` for an untitled edit.
pub fn default_file_name(origin: &Origin) -> String {
    let stem = match origin {
        Origin::Untitled => "edit".to_owned(),
        Origin::Working { name, version: Some(v) } | Origin::Published { name, version: v } => format!("{name}_{v}"),
        Origin::Working { name, version: None } => name.clone(),
    };
    format!("{stem}.{}", movie::MOVIE_EXTENSIONS[0])
}

/// The folder last exported to in this session.
static LAST_FOLDER: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Remember `movie`'s folder for the next export.
pub fn remember_folder(movie: &Path) {
    if let Some(folder) = movie.parent() {
        *LAST_FOLDER.lock().unwrap() = Some(folder.to_path_buf());
    }
}

/// The folder a movie goes in until another is chosen: the last one
/// exported to, else the user's videos folder (Videos, macOS's Movies),
/// else their home. Never the project: a movie made here is the user's.
pub fn default_folder(env: impl Fn(&str) -> Option<String>, is_dir: impl Fn(&Path) -> bool) -> Option<PathBuf> {
    if let Some(last) = LAST_FOLDER.lock().unwrap().clone().filter(|f| is_dir(f)) {
        return Some(last);
    }
    let home = ["USERPROFILE", "HOME"].iter().find_map(|v| env(v).filter(|h| !h.trim().is_empty())).map(PathBuf::from)?;
    let videos = home.join(if cfg!(target_os = "macos") { "Movies" } else { "Videos" });
    Some(if is_dir(&videos) { videos } else { home })
}

/// The movie a fresh Export movie dialog offers.
pub fn default_movie(origin: &Origin) -> PathBuf {
    let folder = default_folder(|v| std::env::var(v).ok(), Path::is_dir).unwrap_or_default();
    folder.join(default_file_name(origin))
}

/// `frames` at `fps` as m:ss (h:mm:ss past the hour).
pub fn duration(frames: usize, fps: u32) -> String {
    let seconds = (frames as f64 / f64::from(fps.max(1))).round() as u64;
    let (h, m, s) = (seconds / 3600, seconds / 60 % 60, seconds % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}

/// What the movie will be, in a line: size, rate, length, how it is shown
/// and encoded.
pub fn summary(edit: &WorkingEdit, display: &str, view: &str) -> String {
    let frames = edit.length().max(0) as usize;
    let fps = edit.fps.max(1);
    let [w, h] = edit.resolution;
    format!(
        "{w}x{h}, {fps} fps, {frames} frames ({}), shown as {display} / {view}. H.264, the sound as AAC; timecode from {}.",
        duration(frames, fps),
        edit.start_timecode
    )
}

/// The progress line of an export under way.
pub fn progress_line(progress: &Progress) -> String {
    let (done, total) = progress.frames();
    match progress.phase() {
        Phase::Preparing => "Reading the takes and mixing the sound…".to_owned(),
        Phase::Frames => format!("Frame {done} of {total} ({:.0}%)", progress.fraction() * 100.0),
        Phase::Finishing => "Finishing the movie…".to_owned(),
    }
}

/// An export under way, as the window holds it: its progress, shared with
/// the thread doing it.
pub type Shared = Arc<Progress>;

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{Source, Track, VideoClip};

    proptest! {
        /// The offered name is the edit's name and version, a .mp4 that
        /// Export movie takes as it is.
        #[test]
        fn the_offered_name_is_the_edits(name in "[A-Za-z][A-Za-z0-9]{0,10}", version in 1u32..10_000, kind in 0usize..4) {
            let v = format!("v{version:04}");
            let origin = match kind {
                0 => Origin::Untitled,
                1 => Origin::Working { name: name.clone(), version: Some(v.clone()) },
                2 => Origin::Working { name: name.clone(), version: None },
                _ => Origin::Published { name: name.clone(), version: v.clone() },
            };
            let file = default_file_name(&origin);
            let want = match kind {
                0 => "edit.mp4".to_owned(),
                2 => format!("{name}.mp4"),
                _ => format!("{name}_{v}.mp4"),
            };
            prop_assert_eq!(&file, &want);
            prop_assert_eq!(movie::movie_path(&format!("C:/out/{file}")).unwrap(), PathBuf::from(format!("C:/out/{want}")));
        }

        /// The offered folder is the videos folder when there is one, else
        /// home; with no home, none.
        #[test]
        fn the_offered_folder_is_videos_else_home(home in prop::option::of("[a-z]{1,8}"), profile in any::<bool>(), videos in any::<bool>()) {
            *LAST_FOLDER.lock().unwrap() = None;
            let home_dir = home.as_ref().map(|h| format!("/users/{h}"));
            let env = |v: &str| match v {
                "USERPROFILE" if profile => home_dir.clone(),
                "HOME" if !profile => home_dir.clone(),
                _ => None,
            };
            let videos_name = if cfg!(target_os = "macos") { "Movies" } else { "Videos" };
            let got = default_folder(env, |p| videos && p.ends_with(videos_name));
            let want = home_dir.map(|h| if videos { Path::new(&h).join(videos_name) } else { PathBuf::from(h) });
            prop_assert_eq!(got, want);
        }

        /// Progress never reads past done, nor before nothing.
        #[test]
        fn progress_stays_within_bounds(done in 0usize..10_000, total in 0usize..10_000) {
            let p = Progress::default();
            p.done.store(done, Ordering::Relaxed);
            p.total.store(total, Ordering::Relaxed);
            let f = p.fraction();
            prop_assert!((0.0..=1.0).contains(&f));
            if total > 0 && done >= total { prop_assert_eq!(f, 1.0); }
            if done == 0 { prop_assert_eq!(f, 0.0); }
        }

        /// A duration reads back as the seconds it stands for.
        #[test]
        fn durations_read_back(frames in 0usize..1_000_000, fps in 1u32..121) {
            let text = duration(frames, fps);
            let parts: Vec<u64> = text.split(':').map(|p| p.parse().unwrap()).collect();
            let seconds = parts.iter().fold(0, |acc, p| acc * 60 + p);
            prop_assert_eq!(seconds, (frames as f64 / f64::from(fps)).round() as u64);
        }
    }

    /// The last folder exported to is offered next.
    #[test]
    fn the_last_folder_is_offered_again() {
        remember_folder(Path::new("/tmp/exports/cut.mp4"));
        let got = default_folder(|_| Some("/users/x".into()), |p| p == Path::new("/tmp/exports"));
        *LAST_FOLDER.lock().unwrap() = None;
        assert_eq!(got, Some(PathBuf::from("/tmp/exports")));
    }

    // ── End to end, with a real hffmpeg ────────────────────────────

    const OCIO: &str = "ocio_profile_version: 2\nroles:\n  default: raw\ndisplays:\n  sRGB:\n    - !<View> {name: Raw, colorspace: raw}\ncolorspaces:\n  - !<ColorSpace>\n    name: raw\n    isdata: true\n";

    /// This machine's hffmpeg, else none (and the test says so).
    fn hffmpeg() -> Option<PathBuf> {
        let found = movie::find_hffmpeg(|v| std::env::var(v).ok(), movie::houdini_bins, Path::is_file);
        if found.is_none() {
            eprintln!("no hffmpeg here: the export is not driven end to end");
        }
        found
    }

    /// An edit of slugs (black, nothing to resolve) `frames` long.
    fn slugs(frames: i64, fps: u32, resolution: [u32; 2]) -> WorkingEdit {
        let mut edit = WorkingEdit::new(fps, resolution, "01:00:00:00");
        let half = frames / 2;
        let clip = |at, duration, text: &str| VideoClip {
            at,
            duration,
            source_in: 0,
            source: Source::Slug { text: text.into() },
            fade_in: 0,
            fade_out: 0,
            opacity: 1.0,
        };
        edit.video.push(Track { name: "V1".into(), clips: vec![clip(0, half, "A"), clip(half, frames - half, "B")] });
        edit
    }

    fn request(dir: &Path, hffmpeg: PathBuf, edit: WorkingEdit, name: &str) -> Request {
        let ocio = dir.join("config.ocio");
        std::fs::write(&ocio, OCIO).unwrap();
        Request {
            paths: ProjectPaths::at(&dir.join("NoProject")),
            ocio: ocio.to_string_lossy().into_owned(),
            edit,
            display: "sRGB".into(),
            view: "Raw".into(),
            hffmpeg,
            output: dir.join(name),
        }
    }

    /// hffprobe's word on `movie`: frames, rate, width, height.
    fn probe(hffmpeg: &Path, movie: &Path) -> (usize, String, u32, u32) {
        let probe = hffmpeg.with_file_name(hffmpeg.file_name().unwrap().to_string_lossy().replace("hffmpeg", "hffprobe"));
        let out = Command::new(probe)
            .args(["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries"])
            .arg("stream=nb_read_frames,r_frame_rate,width,height")
            .args(["-of", "default=noprint_wrappers=1"])
            .arg(movie)
            .output()
            .unwrap();
        let text = String::from_utf8_lossy(&out.stdout).into_owned();
        let get = |k: &str| text.lines().find_map(|l| l.strip_prefix(&format!("{k}="))).unwrap_or_default().to_owned();
        (
            get("nb_read_frames").parse().unwrap_or(0),
            get("r_frame_rate"),
            get("width").parse().unwrap_or(0),
            get("height").parse().unwrap_or(0),
        )
    }

    /// An exported edit is a movie of its every frame at its rate and its
    /// size (an odd size padded even), and nothing is left beside it.
    #[test]
    fn an_export_is_the_edit_at_its_rate_and_size() {
        let Some(hffmpeg) = hffmpeg() else { return };
        let dir = tempfile::tempdir().unwrap();
        for (frames, fps, size, name) in [(30, 24, [64, 48], "cut.mp4"), (17, 25, [33, 21], "odd.mov")] {
            let request = request(dir.path(), hffmpeg.clone(), slugs(frames, fps, size), name);
            let progress = Progress::default();
            let mut wakes = 0;
            let exported = export(&request, &progress, || wakes += 1).unwrap().unwrap();
            assert_eq!((exported.frames, exported.fps, exported.audio), (frames as usize, fps, false));
            assert_eq!(progress.frames(), (frames as usize, frames as usize));
            assert!(wakes >= 2, "the window heard of it");
            let (n, rate, w, h) = probe(&hffmpeg, &request.output);
            assert_eq!(n, frames as usize, "{name}");
            assert_eq!(rate, format!("{fps}/1"));
            assert_eq!((w, h), (size[0].next_multiple_of(2), size[1].next_multiple_of(2)));
            assert!(!movie::partial_path(&request.output).exists());
        }
    }

    /// An export cancelled at any point (preparing, among the frames,
    /// finishing) writes nothing and leaves a file already there as it was.
    #[test]
    fn a_cancelled_export_leaves_nothing() {
        let Some(hffmpeg) = hffmpeg() else { return };
        for phase in [Phase::Preparing, Phase::Frames, Phase::Finishing] {
            let dir = tempfile::tempdir().unwrap();
            let request = request(dir.path(), hffmpeg.clone(), slugs(200, 24, [64, 48]), "cut.mp4");
            std::fs::write(&request.output, b"before").unwrap();
            let progress = Progress::default();
            let got = export(&request, &progress, || {
                if progress.phase() == phase {
                    progress.cancel();
                }
            })
            .unwrap();
            assert_eq!(got, None, "cancelled {phase:?}");
            assert_eq!(std::fs::read(&request.output).unwrap(), b"before");
            assert!(!movie::partial_path(&request.output).exists());
            if phase == Phase::Frames {
                assert!(progress.frames().0 < 200, "it stopped among the frames");
            }
        }
    }

    /// An encoder that cannot run is an error, and nothing is written.
    #[test]
    fn a_missing_encoder_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let request = request(dir.path(), dir.path().join("no-hffmpeg.exe"), slugs(10, 24, [16, 16]), "cut.mp4");
        let error = export(&request, &Progress::default(), || {}).unwrap_err();
        assert!(error.contains("no-hffmpeg"), "{error}");
        assert!(!request.output.exists() && !movie::partial_path(&request.output).exists());
    }

    /// An empty edit is refused before anything runs.
    #[test]
    fn an_empty_edit_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let request = request(dir.path(), PathBuf::from("hffmpeg"), WorkingEdit::new(24, [16, 16], "01:00:00:00"), "cut.mp4");
        assert!(export(&request, &Progress::default(), || {}).unwrap_err().contains("empty"));
    }
}
