//! Reviewing: drawing on the frames of the takes the cut plays, and
//! writing notes on them, for everyone with access to the project to see,
//! add to and change (`th_pipe::review` keeps them, one file per mark, in
//! the take's version's folder on the project share).
//!
//! This is the model, with no window: the brush ([`Brush`]), where a take's
//! picture is on screen ([`Geometry`]), a stroke turned into the lines the
//! screen draws ([`runs`]) and what the eraser touches ([`touches`]), each
//! take's marks as last read and as changed here ([`Folder`]), the session's
//! undo steps, and the thread that reads and writes the files ([`Io`]), so
//! a slow share never holds the window up. `review_view.rs` draws the marks
//! over the picture and takes the brush's strokes; `editor/review.rs` wires
//! them to the editor.
//!
//! Everyone may change anyone's marks: the eraser takes out any stroke it
//! touches, Clear takes out every stroke of the frame, and any note can be
//! changed or deleted. A note changed says who changed it last.
//!
//! Another machine's marks come in as the editor polls the folders
//! ([`Folder::sync_job`]): the take on screen every few seconds, the
//! cut's others in turn, and all of them on Refresh. A read is queued
//! behind the writes before it on the one thread, so it sees them; and a
//! read started before a change made here is dropped, so it can never take
//! back a stroke just drawn.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use primer::{Point, Size};
use th_media::composite::{Placement, fit_into};
use th_pipe::review::{self as files, Body, Changes, Mark, Note, Rgb, Stamp, Stroke};

/// The brush's colours, in the order the strip shows them: bright enough to
/// read on most frames, with white and black for the rest.
pub const COLOURS: [(&str, Rgb); 9] = [
    ("Red", Rgb([255, 59, 48])),
    ("Orange", Rgb([255, 149, 0])),
    ("Yellow", Rgb([255, 214, 10])),
    ("Green", Rgb([52, 199, 89])),
    ("Cyan", Rgb([50, 173, 230])),
    ("Blue", Rgb([10, 132, 255])),
    ("Purple", Rgb([191, 90, 242])),
    ("White", Rgb([255, 255, 255])),
    ("Black", Rgb([0, 0, 0])),
];

/// The brush's sizes are pixels of a picture this tall: a size reads the
/// same on a proxy and on the full frame.
pub const SIZE_LINES: f32 = 1080.0;
/// The smallest and largest brush, in pixels at [`SIZE_LINES`].
pub const SIZE_MIN: f32 = 1.0;
pub const SIZE_MAX: f32 = 60.0;
/// How near (screen pixels) the eraser must come to a stroke to take it.
pub const ERASER_REACH: f32 = 6.0;
/// Points closer than this (screen pixels) to the last one kept are not
/// kept: a still pen adds nothing.
pub const POINT_SPACING: f32 = 0.75;
/// How often the folder of the take on screen is read for other machines'
/// changes.
pub const POLL_EVERY: Duration = Duration::from_secs(3);

/// What the brush draws with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tool {
    Pen,
    Eraser,
}

/// The brush: its tool, colour (of [`COLOURS`]), size and whether a pen's
/// pressure sets the line's width.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Brush {
    pub tool: Tool,
    pub colour: usize,
    /// Pixels at [`SIZE_LINES`].
    pub size: f32,
    pub pressure: bool,
}

impl Default for Brush {
    fn default() -> Self {
        Brush { tool: Tool::Pen, colour: 0, size: 6.0, pressure: true }
    }
}

impl Brush {
    pub fn rgb(&self) -> Rgb {
        COLOURS[self.colour.min(COLOURS.len() - 1)].1
    }

    /// Its size as a stroke keeps it: a share of the picture's height.
    pub fn share(&self) -> f32 {
        (self.size.clamp(SIZE_MIN, SIZE_MAX) / SIZE_LINES).clamp(files::MIN_SIZE, files::MAX_SIZE)
    }

    /// The pressure a point is kept with: the pen's, while pressure is on
    /// and the pen gives one; else full.
    pub fn pressure_of(&self, pen: Option<f32>) -> f32 {
        match pen {
            Some(p) if self.pressure => p.clamp(0.0, 1.0),
            _ => 1.0,
        }
    }
}

/// Where a take's picture is on screen: the picture the viewer shows (the
/// take's own frame, or, with a clip transformed, the edit-shaped picture
/// it is blended into), the take's frame inside it (fitted whole and
/// centred, then placed by the clip's transform) and whether the view is
/// flipped. A mark is kept in the take's frame, so it stays on its picture
/// whatever is done to the clip or the view.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Geometry {
    /// The picture shown, in its pixels.
    pub picture: (u32, u32),
    /// The take's frame, in its pixels.
    pub source: (u32, u32),
    pub placement: Placement,
    pub flipped: bool,
}

impl Geometry {
    /// Where the picture is in a view `bounds` big: fitted whole and
    /// centred, as `video.wgsl` (and `burnin::fitted`) put it. Its left,
    /// top and how many screen pixels a picture pixel is.
    fn shown(&self, bounds: Size) -> (f32, f32, f32) {
        let (w, h) = (self.picture.0.max(1) as f32, self.picture.1.max(1) as f32);
        let scale = (bounds.width / w).min(bounds.height / h);
        ((bounds.width - w * scale) * 0.5, (bounds.height - h * scale) * 0.5, scale)
    }

    /// The point of the take's frame (0 to 1 across and down) under screen
    /// point `p` of a view `bounds` big.
    pub fn to_source(self, bounds: Size, p: Point) -> (f32, f32) {
        let (left, top, scale) = self.shown(bounds);
        let (w, h) = (self.picture.0.max(1) as f32, self.picture.1.max(1) as f32);
        let mut x = (p.x - left) / scale;
        let y = (p.y - top) / scale;
        if self.flipped {
            x = w - x;
        }
        let (x, y) = if self.placement.is_identity() { (x, y) } else { self.placement.invert(w, h, (x, y)) };
        let (fl, ft, fs) = fit_into(self.source, self.picture.0.max(1), self.picture.1.max(1));
        let (sw, sh) = (self.source.0.max(1) as f32, self.source.1.max(1) as f32);
        ((x - fl) / fs / sw, (y - ft) / fs / sh)
    }

    /// The screen point of a view `bounds` big that shows point `at` of
    /// the take's frame: [`Self::to_source`] undone.
    pub fn to_screen(self, bounds: Size, at: (f32, f32)) -> Point {
        let (left, top, scale) = self.shown(bounds);
        let (w, h) = (self.picture.0.max(1) as f32, self.picture.1.max(1) as f32);
        let (fl, ft, fs) = fit_into(self.source, self.picture.0.max(1), self.picture.1.max(1));
        let (sw, sh) = (self.source.0.max(1) as f32, self.source.1.max(1) as f32);
        let (x, y) = (fl + at.0 * sw * fs, ft + at.1 * sh * fs);
        let (mut x, y) = if self.placement.is_identity() { (x, y) } else { self.placement.apply(w, h, (x, y)) };
        if self.flipped {
            x = w - x;
        }
        Point::new(left + x * scale, top + y * scale)
    }

    /// How tall the take's frame is on screen, in screen pixels: what a
    /// stroke's width (a share of that height) is multiplied by.
    pub fn height_on_screen(&self, bounds: Size) -> f32 {
        let (_, _, scale) = self.shown(bounds);
        let (_, _, fs) = fit_into(self.source, self.picture.0.max(1), self.picture.1.max(1));
        self.source.1.max(1) as f32 * fs * self.placement.scale.abs() * scale
    }
}

/// A stroke as the screen draws it: polylines, each of one width (screen
/// pixels), with round joins and ends. A run ends where the width has
/// changed by more than a little, and the next starts at its last point,
/// so the line is unbroken and opaque colour never doubles up. A change at
/// the last point starts no run: the point ends the run before.
pub fn runs(points: &[(Point, f32)]) -> Vec<(Vec<Point>, f32)> {
    let mut out: Vec<(Vec<Point>, f32)> = Vec::new();
    let Some(&(first, w)) = points.first() else { return out };
    let mut run = vec![first];
    let mut width = w;
    for &(p, w) in &points[1..] {
        run.push(p);
        if (w - width).abs() > (width * 0.08).max(0.35) {
            out.push((std::mem::replace(&mut run, vec![p]), width));
            width = w;
        }
    }
    if run.len() > 1 || out.is_empty() {
        out.push((run, width));
    }
    out
}

/// The screen distance from `p` to the segment `a`–`b`.
fn to_segment(p: Point, a: Point, b: Point) -> f32 {
    let (dx, dy) = (b.x - a.x, b.y - a.y);
    let length = dx * dx + dy * dy;
    let t = if length <= f32::EPSILON { 0.0 } else { (((p.x - a.x) * dx + (p.y - a.y) * dy) / length).clamp(0.0, 1.0) };
    let (cx, cy) = (a.x + t * dx, a.y + t * dy);
    ((p.x - cx).powi(2) + (p.y - cy).powi(2)).sqrt()
}

/// Whether the eraser at `p` touches the stroke drawn through `points`
/// (screen points and widths): within [`ERASER_REACH`] of its edge.
pub fn touches(points: &[(Point, f32)], p: Point) -> bool {
    match points {
        [] => false,
        [(only, w)] => to_segment(p, *only, *only) <= ERASER_REACH + w / 2.0,
        _ => points.windows(2).any(|pair| to_segment(p, pair[0].0, pair[1].0) <= ERASER_REACH + pair[0].1.max(pair[1].1) / 2.0),
    }
}

/// Whether the eraser moving from `from` to `to` touches a stroke at
/// `points`: anywhere along the way, not only where it was seen (a fast
/// swipe jumps across a stroke between two events). Tried every
/// [`ERASER_REACH`] along it, so a stroke crossing the path is never more
/// than half that from a try.
pub fn swept(points: &[(Point, f32)], from: Point, to: Point) -> bool {
    let length = ((to.x - from.x).powi(2) + (to.y - from.y).powi(2)).sqrt();
    let steps = (length / ERASER_REACH).ceil().max(1.0) as usize;
    (0..=steps).any(|i| {
        let t = i as f32 / steps as f32;
        touches(points, Point::new(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t))
    })
}

/// A stroke's points on screen, each with its width there.
pub fn on_screen(stroke: &Stroke, geometry: &Geometry, bounds: Size) -> Vec<(Point, f32)> {
    let tall = geometry.height_on_screen(bounds);
    stroke
        .points
        .iter()
        .map(|p| (geometry.to_screen(bounds, (p.x, p.y)), (files::width_at(stroke.size, p.pressure) * tall).max(0.5)))
        .collect()
}

/// A stamp no file has: a mark written here and not read back yet. Kept
/// with what a sync is told is held, so it is read again once listed and
/// gone once not.
const UNREAD: Stamp = Stamp { modified: None, len: u64::MAX };

/// A take's marks: as last read from its folder, and as changed here since.
#[derive(Debug, Default)]
pub struct Folder {
    /// By file name, so in the order they were made, each with the stamp
    /// it was read at.
    marks: BTreeMap<String, (Mark, Stamp)>,
    /// Files there that do not read as marks (a later kind), kept so they
    /// are not read again until they change.
    unread: HashMap<String, Stamp>,
    /// Changes made here: a read queued before the latest is dropped.
    edits: u64,
    /// A read is queued and not back.
    reading: bool,
    /// When it was last asked to be read.
    asked: Option<Instant>,
    /// Read once at least.
    pub read: bool,
}

impl Folder {
    /// The frames with marks.
    pub fn frames(&self) -> BTreeSet<i64> {
        self.marks.values().map(|(m, _)| m.frame).collect()
    }

    /// Frame `frame`'s marks, in the order they were made.
    pub fn on(&self, frame: i64) -> impl Iterator<Item = &Mark> {
        self.marks.values().map(|(m, _)| m).filter(move |m| m.frame == frame)
    }

    pub fn get(&self, name: &str) -> Option<&Mark> {
        self.marks.get(name).map(|(m, _)| m)
    }

    /// What a read of it is told is held.
    fn held(&self) -> HashMap<String, Stamp> {
        let mut held: HashMap<String, Stamp> = self.marks.iter().map(|(name, (_, stamp))| (name.clone(), *stamp)).collect();
        held.extend(self.unread.iter().map(|(n, s)| (n.clone(), *s)));
        held
    }

    /// Whether it is due a read at `now`, read every `every`.
    pub fn due(&self, now: Instant, every: Duration) -> bool {
        !self.reading && self.asked.is_none_or(|asked| now.duration_since(asked) >= every)
    }

    /// A read of `dir`, from what is held now; marks it reading.
    pub fn sync_job(&mut self, dir: &Path, now: Instant) -> Job {
        self.reading = true;
        self.asked = Some(now);
        Job::Sync { dir: dir.to_path_buf(), held: self.held(), edits: self.edits }
    }

    /// A read's result, queued when `edits` changes had been made here:
    /// taken in unless a change was made since (it may predate a write),
    /// in which case the next poll reads again.
    pub fn synced(&mut self, edits: u64, result: Result<Changes, String>) -> Result<bool, String> {
        self.reading = false;
        let changes = result?;
        if edits != self.edits {
            self.asked = None;
            return Ok(false);
        }
        self.read = true;
        let mut changed = !changes.gone.is_empty();
        for name in changes.gone {
            self.marks.remove(&name);
            self.unread.remove(&name);
        }
        for (name, stamp) in changes.unread {
            self.unread.insert(name, stamp);
        }
        for (name, stamp, mark) in changes.changed {
            changed |= self.marks.get(&name).is_none_or(|(held, _)| held != &mark);
            self.unread.remove(&name);
            self.marks.insert(name, (mark, stamp));
        }
        Ok(changed)
    }

    fn put(&mut self, mark: Mark) {
        self.edits += 1;
        self.marks.insert(mark.file_name(), (mark, UNREAD));
    }

    fn take(&mut self, name: &str) {
        self.edits += 1;
        self.marks.remove(name);
    }
}

/// One change to a take's marks, made here.
#[derive(Debug, Clone, PartialEq)]
pub enum Change {
    Add(PathBuf, Mark),
    Remove(PathBuf, Mark),
    /// A mark changed: as it was, and as it is.
    Replace(PathBuf, Mark, Mark),
}

impl Change {
    fn inverse(&self) -> Change {
        match self {
            Change::Add(dir, mark) => Change::Remove(dir.clone(), mark.clone()),
            Change::Remove(dir, mark) => Change::Add(dir.clone(), mark.clone()),
            Change::Replace(dir, before, after) => Change::Replace(dir.clone(), after.clone(), before.clone()),
        }
    }
}

/// What the file thread is asked to do, in order.
#[derive(Debug)]
pub enum Job {
    Write(PathBuf, Mark),
    Remove(PathBuf, String),
    /// Read what changed in a folder since `held` was read, `edits` being
    /// the changes made here when it was asked.
    Sync {
        dir: PathBuf,
        held: HashMap<String, Stamp>,
        edits: u64,
    },
    /// Say so once every job before it is done.
    Flush(mpsc::Sender<()>),
}

/// What the file thread did that the editor must hear of.
#[derive(Debug)]
pub enum Done {
    /// A write or removal failed: what was not saved.
    Failed(String),
    Synced {
        dir: PathBuf,
        edits: u64,
        result: Result<Changes, String>,
    },
}

/// The thread that reads and writes the marks' files, one job at a time
/// in the order asked; what it did waits in [`Io::take`], and `wake` is
/// called after each so the editor looks.
pub struct Io {
    jobs: mpsc::Sender<Job>,
    done: Arc<Mutex<Vec<Done>>>,
}

impl Io {
    pub fn start(wake: impl Fn() + Send + 'static) -> Io {
        let (jobs, inbox) = mpsc::channel::<Job>();
        let done = Arc::new(Mutex::new(Vec::new()));
        let out = Arc::clone(&done);
        let spawned = std::thread::Builder::new().name("cinedex review files".into()).spawn(move || {
            for job in inbox {
                let report = match job {
                    Job::Write(dir, mark) => files::write(&dir, &mark)
                        .err()
                        .map(|e| Done::Failed(format!("A review mark was not saved to {}: {e}", dir.display()))),
                    Job::Remove(dir, name) => files::remove(&dir, &name)
                        .err()
                        .map(|e| Done::Failed(format!("A review mark was not erased from {}: {e}", dir.display()))),
                    Job::Sync { dir, held, edits } => {
                        let result =
                            files::sync(&dir, &held).map_err(|e| format!("The review marks in {} were not read: {e}", dir.display()));
                        Some(Done::Synced { dir, edits, result })
                    }
                    Job::Flush(done) => {
                        let _ = done.send(());
                        None
                    }
                };
                if let Some(report) = report {
                    out.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(report);
                    wake();
                }
            }
        });
        if let Err(e) = spawned {
            eprintln!("cinedex: no thread for the review marks: {e}");
        }
        Io { jobs, done }
    }

    fn send(&self, job: Job) {
        // The thread ends only with the editor.
        let _ = self.jobs.send(job);
    }

    /// Wait, at most `within`, for every job asked so far to be done:
    /// whether they were.
    pub fn flush(&self, within: Duration) -> bool {
        let (done, wait) = mpsc::channel();
        self.send(Job::Flush(done));
        wait.recv_timeout(within).is_ok()
    }

    /// What it did since the last call.
    pub fn take(&self) -> Vec<Done> {
        std::mem::take(&mut *self.done.lock().unwrap_or_else(std::sync::PoisonError::into_inner))
    }
}

/// A note being written: a new one, or a change to the note named `name`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Draft {
    pub text: String,
    /// The note changed, by file name (and its folder); None for a new one.
    pub editing: Option<(PathBuf, String)>,
}

/// The editor's reviewing: every take's marks it has read, the brush,
/// whether the picture takes strokes, the note being written, and the
/// session's undo steps (each one gesture: a stroke, an erase, a Clear, a
/// note).
pub struct Review {
    folders: HashMap<PathBuf, Folder>,
    undo: Vec<Vec<Change>>,
    redo: Vec<Vec<Change>>,
    io: Io,
    /// Who makes marks here (`TH_USER`).
    pub me: String,
    pub brush: Brush,
    /// The picture takes the brush's strokes (D).
    pub drawing: bool,
    /// The marks are drawn over the picture.
    pub shown: bool,
    pub draft: Draft,
    /// An eraser drag is under way and has made a step: what it takes
    /// next joins that step ([`Self::join`]).
    pub joining: bool,
    /// The next of the cut's folders the poll reads, in turn.
    turn: usize,
}

impl Review {
    pub fn new(io: Io) -> Self {
        Review {
            folders: HashMap::new(),
            undo: Vec::new(),
            redo: Vec::new(),
            io,
            me: files::author(),
            brush: Brush::default(),
            drawing: false,
            shown: true,
            draft: Draft::default(),
            joining: false,
            turn: 0,
        }
    }

    pub fn folder(&self, dir: &Path) -> Option<&Folder> {
        self.folders.get(dir)
    }

    /// Read the folders not read yet (a take that came into the cut).
    pub fn want(&mut self, dirs: impl IntoIterator<Item = PathBuf>) {
        let now = Instant::now();
        for dir in dirs {
            let folder = self.folders.entry(dir.clone()).or_default();
            if !folder.read && !folder.reading {
                let job = folder.sync_job(&dir, now);
                self.io.send(job);
            }
        }
    }

    /// Read `dir` again unless a read of it is under way or it was asked
    /// within `every`.
    pub fn poll(&mut self, dir: &Path, every: Duration) {
        let now = Instant::now();
        let folder = self.folders.entry(dir.to_path_buf()).or_default();
        if folder.due(now, every) {
            let job = folder.sync_job(dir, now);
            self.io.send(job);
        }
    }

    /// Read one more of `dirs` (the cut's), in turn: the poll's slow sweep.
    pub fn poll_turn(&mut self, dirs: &[PathBuf], every: Duration) {
        if dirs.is_empty() {
            return;
        }
        self.turn = (self.turn + 1) % dirs.len();
        self.poll(&dirs[self.turn], every);
    }

    /// Take in what the file thread did: whether any marks changed, and
    /// what failed.
    pub fn collect(&mut self) -> (bool, Vec<String>) {
        let mut changed = false;
        let mut failed = Vec::new();
        for done in self.io.take() {
            match done {
                Done::Failed(e) => failed.push(e),
                Done::Synced { dir, edits, result } => match self.folders.entry(dir).or_default().synced(edits, result) {
                    Ok(c) => changed |= c,
                    Err(e) => failed.push(e),
                },
            }
        }
        (changed, failed)
    }

    /// Make `step` (one gesture's changes) and keep it to undo.
    pub fn apply(&mut self, step: Vec<Change>) {
        if step.is_empty() {
            return;
        }
        for change in &step {
            self.perform(change);
        }
        self.undo.push(step);
        self.redo.clear();
    }

    /// Make `step` part of the last step made (one drag of the eraser is
    /// one step to undo); the first step when there is none.
    pub fn join(&mut self, step: Vec<Change>) {
        if step.is_empty() {
            return;
        }
        for change in &step {
            self.perform(change);
        }
        match self.undo.last_mut() {
            Some(last) => last.extend(step),
            None => self.undo.push(step),
        }
        self.redo.clear();
    }

    /// Wait, at most `within`, for the marks made so far to be written
    /// (before the editor closes): whether they were.
    pub fn flush(&self, within: Duration) -> bool {
        self.io.flush(within)
    }

    pub fn can_undo(&self) -> bool {
        !self.undo.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.redo.is_empty()
    }

    /// Undo the last step made here; false when there is none.
    pub fn undo(&mut self) -> bool {
        let Some(step) = self.undo.pop() else { return false };
        for change in step.iter().rev() {
            self.perform(&change.inverse());
        }
        self.redo.push(step);
        true
    }

    pub fn redo(&mut self) -> bool {
        let Some(step) = self.redo.pop() else { return false };
        for change in &step {
            self.perform(change);
        }
        self.undo.push(step);
        true
    }

    fn perform(&mut self, change: &Change) {
        match change {
            Change::Add(dir, mark) | Change::Replace(dir, _, mark) => {
                self.folders.entry(dir.clone()).or_default().put(mark.clone());
                self.io.send(Job::Write(dir.clone(), mark.clone()));
            }
            Change::Remove(dir, mark) => {
                let name = mark.file_name();
                self.folders.entry(dir.clone()).or_default().take(&name);
                self.io.send(Job::Remove(dir.clone(), name));
            }
        }
    }

    /// A new mark by me on `frame`, made now.
    pub fn new_mark(&self, frame: i64, body: Body) -> Mark {
        let created = files::now_ms();
        Mark { id: files::new_id(created), frame, author: self.me.clone(), created, body }
    }

    /// The step that adds a stroke through `points` (the take's frame's,
    /// 0 to 1, with pressures) to `frame` of `dir`, drawn with the brush.
    pub fn stroke_step(&self, dir: &Path, frame: i64, points: Vec<files::Point>) -> Vec<Change> {
        if points.is_empty() {
            return Vec::new();
        }
        let stroke = Stroke { color: self.brush.rgb(), size: self.brush.share(), points };
        vec![Change::Add(dir.to_path_buf(), self.new_mark(frame, Body::Stroke(stroke)))]
    }

    /// The step that takes the marks named `names` out of `dir`.
    pub fn remove_step(&self, dir: &Path, names: &[String]) -> Vec<Change> {
        let Some(folder) = self.folders.get(dir) else { return Vec::new() };
        names.iter().filter_map(|n| folder.get(n)).map(|m| Change::Remove(dir.to_path_buf(), m.clone())).collect()
    }

    /// The step that takes every stroke of `frame` of `dir` out (Clear).
    pub fn clear_step(&self, dir: &Path, frame: i64) -> Vec<Change> {
        let Some(folder) = self.folders.get(dir) else { return Vec::new() };
        folder.on(frame).filter(|m| m.stroke().is_some()).map(|m| Change::Remove(dir.to_path_buf(), m.clone())).collect()
    }

    /// The step the draft makes on `frame` of `dir`: a new note, or the
    /// note it edits changed (said to be changed by me, now); taken out
    /// when its text is cleared. None when it changes nothing.
    pub fn note_step(&self, dir: &Path, frame: i64) -> Vec<Change> {
        let text = self.draft.text.trim().to_owned();
        match &self.draft.editing {
            Some((at, name)) => {
                let Some(before) = self.folders.get(at).and_then(|f| f.get(name)).filter(|m| m.note().is_some()) else { return Vec::new() };
                if text.is_empty() {
                    return vec![Change::Remove(at.clone(), before.clone())];
                }
                if before.note().is_some_and(|n| n.text == text) {
                    return Vec::new();
                }
                let mut after = before.clone();
                after.body = Body::Note(Note { text, edited: Some((files::now_ms(), self.me.clone())) });
                vec![Change::Replace(at.clone(), before.clone(), after)]
            }
            None if text.is_empty() => Vec::new(),
            None => vec![Change::Add(dir.to_path_buf(), self.new_mark(frame, Body::Note(Note { text, edited: None })))],
        }
    }
}

/// How long ago `ms` (Unix milliseconds) was, as a note says it: "just
/// now", "5 min ago", "3 h ago", else the day.
pub fn ago(ms: u64, now_ms: u64) -> String {
    let seconds = now_ms.saturating_sub(ms) / 1000;
    match seconds {
        0..60 => "just now".into(),
        60..3600 => format!("{} min ago", seconds / 60),
        3600..86_400 => format!("{} h ago", seconds / 3600),
        _ => chrono::DateTime::from_timestamp_millis(ms as i64)
            .map(|t| t.with_timezone(&chrono::Local).format("%Y-%m-%d").to_string())
            .unwrap_or_default(),
    }
}

/// Who a mark is by, as a note says it: the author, or "someone" when it
/// was made without a TumblePipe user.
pub fn who(author: &str) -> &str {
    if author.is_empty() { "someone" } else { author }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn geometry() -> impl Strategy<Value = Geometry> {
        let placement = prop_oneof![
            Just(Placement::IDENTITY),
            (-1f32..1.0, -1f32..1.0, 0.2f32..4.0, -360f32..360.0).prop_map(|(x, y, scale, rotation)| Placement { x, y, scale, rotation }),
        ];
        ((16u32..4000, 16u32..3000), (16u32..4000, 16u32..3000), placement, any::<bool>())
            .prop_map(|(picture, source, placement, flipped)| Geometry { picture, source, placement, flipped })
    }

    fn size() -> impl Strategy<Value = Size> {
        (50f32..3000.0, 50f32..2000.0).prop_map(|(w, h)| Size::new(w, h))
    }

    proptest! {
        /// A point of the take's frame goes to the screen and back to
        /// itself, however the clip is placed and whether the view is
        /// flipped: a mark stays where it was drawn.
        #[test]
        fn marks_stay_on_their_picture(g in geometry(), bounds in size(), u in 0f32..=1.0, v in 0f32..=1.0) {
            let back = g.to_source(bounds, g.to_screen(bounds, (u, v)));
            prop_assert!((back.0 - u).abs() < 1e-3 && (back.1 - v).abs() < 1e-3, "{:?} -> {:?}", (u, v), back);
        }

        /// Untransformed, the frame's corners are the corners of the
        /// picture as the video fits it, mirrored when flipped.
        #[test]
        fn an_untransformed_frame_is_where_the_video_draws_it(w in 16u32..4000, h in 16u32..3000, bounds in size(), flipped in any::<bool>()) {
            let g = Geometry { picture: (w, h), source: (w, h), placement: Placement::IDENTITY, flipped };
            let fit = crate::burnin::fitted((w as f32, h as f32), bounds);
            let top_left = g.to_screen(bounds, (0.0, 0.0));
            let x = if flipped { fit.x + fit.width } else { fit.x };
            prop_assert!((top_left.x - x).abs() < 0.05 && (top_left.y - fit.y).abs() < 0.05);
            prop_assert!((g.height_on_screen(bounds) - fit.height).abs() < 0.05);
        }

        /// Runs cover every point once in order, share only their joins,
        /// and each is at least two points unless the stroke is one.
        #[test]
        fn runs_cover_the_stroke(points in prop::collection::vec((0f32..500.0, 0f32..500.0, 0.5f32..40.0), 1..60)) {
            let points: Vec<(Point, f32)> = points.into_iter().map(|(x, y, w)| (Point::new(x, y), w)).collect();
            let runs = runs(&points);
            let mut joined: Vec<Point> = Vec::new();
            for (i, (run, _)) in runs.iter().enumerate() {
                prop_assert!(run.len() >= 2 || points.len() == 1);
                let skip = usize::from(i > 0);
                if i > 0 { prop_assert_eq!(Some(&run[0]), joined.last()); }
                joined.extend(&run[skip..]);
            }
            prop_assert_eq!(joined, points.iter().map(|p| p.0).collect::<Vec<_>>());
        }

        /// The eraser touches a stroke at any of its points, and none far
        /// from all of them.
        /// A swipe across a stroke touches it however far apart the two
        /// events it was seen at, and one that stays clear of it does not.
        #[test]
        fn a_swipe_touches_what_it_jumps_across(
            y in 100f32..400.0,
            above in 1f32..300.0,
            below in 1f32..300.0,
            x in 50f32..450.0,
            lean in -200f32..200.0,
        ) {
            let stroke = [(Point::new(-1000.0, y), 2.0), (Point::new(1500.0, y), 2.0)];
            let (from, to) = (Point::new(x, y - above - ERASER_REACH - 2.0), Point::new(x + lean, y + below + ERASER_REACH + 2.0));
            prop_assert!(swept(&stroke, from, to));
            prop_assert!(swept(&stroke, to, from));
            let clear = Point::new(x + lean, y - ERASER_REACH - 2.0);
            prop_assert!(!swept(&stroke, from, clear));
        }

        #[test]
        fn the_eraser_touches_what_it_crosses(points in prop::collection::vec((0f32..500.0, 0f32..500.0), 1..20), pick in any::<prop::sample::Index>()) {
            let stroke: Vec<(Point, f32)> = points.iter().map(|&(x, y)| (Point::new(x, y), 4.0)).collect();
            prop_assert!(touches(&stroke, pick.get(&stroke).0));
            prop_assert!(!touches(&stroke, Point::new(2000.0, 2000.0)));
        }

        /// A pen's pressure is kept only while pressure is on; a mouse's
        /// points are full.
        #[test]
        fn pressure_is_kept_only_when_on(p in 0f32..=1.0, on in any::<bool>()) {
            let brush = Brush { pressure: on, ..Brush::default() };
            prop_assert_eq!(brush.pressure_of(Some(p)), if on { p } else { 1.0 });
            prop_assert_eq!(brush.pressure_of(None), 1.0);
        }
    }

    fn wait(review: &mut Review) -> (bool, Vec<String>) {
        let mut changed = false;
        let mut failed = Vec::new();
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(10) {
            let (c, f) = review.collect();
            changed |= c;
            failed.extend(f);
            if review.folders.values().all(|f| !f.reading) {
                return (changed, failed);
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        panic!("the file thread did not answer");
    }

    fn point(x: f32) -> files::Point {
        files::Point { x, y: 0.5, pressure: 1.0 }
    }

    /// Something one reviewer does to a take's marks.
    #[derive(Debug, Clone)]
    enum Act {
        Stroke(usize, i64),
        Note(usize, i64),
        Edit(usize, prop::sample::Index),
        Erase(usize, prop::sample::Index),
        Clear(usize, i64),
        Undo(usize),
        Redo(usize),
        /// Read the folder again (as the poll does) and wait for it.
        Read(usize),
        /// Ask for a read and go on: it lands later, after what is done next.
        ReadLater(usize),
        /// Take in what the file thread has done so far.
        Collect(usize),
    }

    fn act(sides: usize) -> impl Strategy<Value = Act> {
        let side = 0..sides;
        let frame = 1001i64..1004;
        prop_oneof![
            3 => (side.clone(), frame.clone()).prop_map(|(s, f)| Act::Stroke(s, f)),
            2 => (side.clone(), frame.clone()).prop_map(|(s, f)| Act::Note(s, f)),
            2 => (side.clone(), any::<prop::sample::Index>()).prop_map(|(s, i)| Act::Edit(s, i)),
            2 => (side.clone(), any::<prop::sample::Index>()).prop_map(|(s, i)| Act::Erase(s, i)),
            1 => (side.clone(), frame).prop_map(|(s, f)| Act::Clear(s, f)),
            2 => side.clone().prop_map(Act::Undo),
            1 => side.clone().prop_map(Act::Redo),
            2 => side.clone().prop_map(Act::Read),
            2 => side.clone().prop_map(Act::ReadLater),
            1 => side.prop_map(Act::Collect),
        ]
    }

    /// Do `act` on `review`'s marks in `dir`; `n` tells its words apart.
    fn perform_act(review: &mut Review, dir: &Path, act: &Act, n: usize) {
        let names = |review: &Review, notes: bool| -> Vec<String> {
            review
                .folder(dir)
                .map(|f| f.marks.iter().filter(|(_, (m, _))| !notes || m.note().is_some()).map(|(n, _)| n.clone()).collect())
                .unwrap_or_default()
        };
        match act {
            Act::Stroke(_, frame) => {
                let step = review.stroke_step(dir, *frame, vec![point(0.1 + n as f32 / 100.0), point(0.6)]);
                review.apply(step);
            }
            Act::Note(_, frame) => {
                review.draft = Draft { text: format!("note {n}"), editing: None };
                let step = review.note_step(dir, *frame);
                review.apply(step);
                review.draft = Draft::default();
            }
            Act::Edit(_, pick) => {
                let notes = names(review, true);
                if !notes.is_empty() {
                    let name = notes[pick.index(notes.len())].clone();
                    review.draft = Draft { text: format!("edit {n}"), editing: Some((dir.to_path_buf(), name)) };
                    let step = review.note_step(dir, 0);
                    review.apply(step);
                    review.draft = Draft::default();
                }
            }
            Act::Erase(_, pick) => {
                let all = names(review, false);
                if !all.is_empty() {
                    let step = review.remove_step(dir, std::slice::from_ref(&all[pick.index(all.len())]));
                    review.apply(step);
                }
            }
            Act::Clear(_, frame) => {
                let step = review.clear_step(dir, *frame);
                review.apply(step);
            }
            Act::Undo(_) => {
                review.undo();
            }
            Act::Redo(_) => {
                review.redo();
            }
            Act::Read(_) => {
                review.want([dir.to_path_buf()]);
                review.poll(dir, Duration::ZERO);
                wait(review);
            }
            Act::ReadLater(_) => {
                review.want([dir.to_path_buf()]);
                review.poll(dir, Duration::ZERO);
            }
            Act::Collect(_) => {
                review.collect();
            }
        }
    }

    /// A reviewer's marks in `dir` as they would be written.
    fn as_written(review: &Review, dir: &Path) -> BTreeMap<String, Vec<u8>> {
        review.folder(dir).map(|f| f.marks.iter().map(|(n, (m, _))| (n.clone(), files::encode(m))).collect()).unwrap_or_default()
    }

    /// The marks in `dir` as a fresh read finds them.
    fn on_disk(dir: &Path) -> BTreeMap<String, Vec<u8>> {
        files::sync(dir, &HashMap::new()).unwrap().changed.into_iter().map(|(n, _, m)| (n, files::encode(&m))).collect()
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(24))]

        /// Two reviewers taking turns on one take, each looking at the
        /// take as it is (read first) and acting on anyone's marks:
        /// drawing, writing, changing, erasing, clearing, undoing and
        /// redoing their own steps. After each turn the share holds what
        /// the one who acted sees, mark for mark; and at the end both see
        /// what is on the share.
        #[test]
        fn two_reviewers_taking_turns_agree_with_the_share(acts in prop::collection::vec(act(2).prop_filter("turns", |a| !matches!(a, Act::Read(_) | Act::ReadLater(_) | Act::Collect(_))), 1..30)) {
            let share = tempfile::tempdir().unwrap();
            let dir = share.path().join("review").join("v0001");
            let mut sides = [Review::new(Io::start(|| {})), Review::new(Io::start(|| {}))];
            sides[0].me = "remy".into();
            sides[1].me = "soren".into();
            for (n, act) in acts.iter().enumerate() {
                let side = match act {
                    Act::Stroke(s, _) | Act::Note(s, _) | Act::Edit(s, _) | Act::Erase(s, _) | Act::Clear(s, _) => *s,
                    Act::Undo(s) | Act::Redo(s) | Act::Read(s) | Act::ReadLater(s) | Act::Collect(s) => *s,
                };
                perform_act(&mut sides[side], &dir, &Act::Read(side), n);
                perform_act(&mut sides[side], &dir, act, n);
                prop_assert!(sides[side].flush(Duration::from_secs(10)));
                prop_assert_eq!(&as_written(&sides[side], &dir), &on_disk(&dir), "after {:?}", act);
            }
            let disk = on_disk(&dir);
            for side in &mut sides {
                perform_act(side, &dir, &Act::Read(0), 0);
                prop_assert_eq!(&as_written(side, &dir), &disk);
            }
        }

        /// One reviewer whose reads of the folder are still under way while
        /// they draw, write, erase, undo and redo: a read asked before a
        /// change never takes the change back. With every write done and
        /// every read in, what they see is what is on the share, with no
        /// read after to put it right.
        #[test]
        fn reads_under_way_never_undo_a_change(acts in prop::collection::vec(act(1), 1..30)) {
            let share = tempfile::tempdir().unwrap();
            let dir = share.path().join("v0001");
            let mut review = Review::new(Io::start(|| {}));
            for (n, act) in acts.iter().enumerate() {
                perform_act(&mut review, &dir, act, n);
            }
            prop_assert!(review.flush(Duration::from_secs(10)));
            wait(&mut review);
            prop_assert_eq!(as_written(&review, &dir), on_disk(&dir));
        }

        /// Whatever a session does (strokes, notes, changes to them,
        /// erasing, clearing), undoing it all leaves the take as it was,
        /// here and on the share, and redoing it all brings back what it
        /// made, here and on the share.
        #[test]
        fn undoing_all_and_redoing_all(acts in prop::collection::vec(act(1).prop_filter("no undo, redo or read", |a| !matches!(a, Act::Undo(_) | Act::Redo(_) | Act::Read(_))), 1..20)) {
            let share = tempfile::tempdir().unwrap();
            let dir = share.path().join("v0001");
            let mut review = Review::new(Io::start(|| {}));
            for (n, act) in acts.iter().enumerate() {
                perform_act(&mut review, &dir, act, n);
            }
            let made = as_written(&review, &dir);
            while review.undo() {}
            prop_assert!(as_written(&review, &dir).is_empty());
            prop_assert!(review.flush(Duration::from_secs(10)));
            prop_assert!(on_disk(&dir).is_empty());
            while review.redo() {}
            prop_assert_eq!(&as_written(&review, &dir), &made);
            prop_assert!(review.flush(Duration::from_secs(10)));
            prop_assert_eq!(on_disk(&dir), made);
        }
    }

    /// What one machine draws, erases and writes, another reads; undo and
    /// redo put the files back as they were; and a read that went out
    /// before a stroke never takes it back.
    #[test]
    fn two_editors_share_a_take() {
        let share = tempfile::tempdir().unwrap();
        let dir = share.path().join("review").join("v0001");
        let (mut here, mut there) = (Review::new(Io::start(|| {})), Review::new(Io::start(|| {})));
        here.me = "remy".into();
        let step = here.stroke_step(&dir, 1001, vec![point(0.1), point(0.2)]);
        here.apply(step);
        here.draft.text = "the hand pops".into();
        let note = here.note_step(&dir, 1001);
        here.apply(note);
        assert!(here.flush(Duration::from_secs(10)));
        there.want([dir.clone()]);
        wait(&mut there);
        let seen = there.folder(&dir).unwrap();
        assert_eq!(seen.frames(), BTreeSet::from([1001]));
        assert_eq!(seen.on(1001).count(), 2);
        // Everyone may change anyone's: the other machine edits the note.
        let name = seen.on(1001).find(|m| m.note().is_some()).unwrap().file_name();
        there.me = "soren".into();
        there.draft = Draft { text: "fixed in v2?".into(), editing: Some((dir.clone(), name.clone())) };
        let edit = there.note_step(&dir, 1001);
        there.apply(edit);
        assert!(there.flush(Duration::from_secs(10)));
        here.poll(&dir, Duration::ZERO);
        wait(&mut here);
        let note = here.folder(&dir).unwrap().get(&name).unwrap().note().unwrap().clone();
        assert_eq!(note.text, "fixed in v2?");
        assert_eq!(note.edited.map(|e| e.1), Some("soren".into()));
        // Clear takes the strokes, not the note; undo brings them back.
        let clear = there.clear_step(&dir, 1001);
        assert_eq!(clear.len(), 1);
        there.apply(clear);
        assert!(there.flush(Duration::from_secs(10)));
        here.poll(&dir, Duration::ZERO);
        wait(&mut here);
        assert_eq!(here.folder(&dir).unwrap().on(1001).count(), 1);
        assert!(there.undo());
        assert!(there.flush(Duration::from_secs(10)));
        here.poll(&dir, Duration::ZERO);
        wait(&mut here);
        assert_eq!(here.folder(&dir).unwrap().on(1001).count(), 2);
        // A read asked before a stroke is drawn is dropped, not let take
        // the stroke back.
        let job = here.folders.get_mut(&dir).unwrap().sync_job(&dir, Instant::now());
        let step = here.stroke_step(&dir, 1002, vec![point(0.3)]);
        here.apply(step);
        here.io.send(job);
        wait(&mut here);
        assert_eq!(here.folder(&dir).unwrap().frames(), BTreeSet::from([1001, 1002]));
    }

    /// Undo and redo are each other's inverse: any sequence of steps
    /// undone all the way leaves no marks, and redone all the way leaves
    /// the marks it made.
    #[test]
    fn undo_and_redo_are_inverse() {
        let share = tempfile::tempdir().unwrap();
        let dir = share.path().join("v0001");
        let mut review = Review::new(Io::start(|| {}));
        let mut after = Vec::new();
        for i in 0..6 {
            let step = review.stroke_step(&dir, 1001 + i % 2, vec![point(i as f32 / 10.0)]);
            review.apply(step);
            if i == 3 {
                let first: Vec<String> = review.folder(&dir).unwrap().marks.keys().take(2).cloned().collect();
                let step = review.remove_step(&dir, &first);
                review.apply(step);
            }
            after.push(review.folder(&dir).unwrap().marks.keys().cloned().collect::<Vec<_>>());
        }
        while review.undo() {}
        assert!(review.folder(&dir).unwrap().marks.is_empty());
        while review.redo() {}
        assert_eq!(review.folder(&dir).unwrap().marks.keys().cloned().collect::<Vec<_>>(), *after.last().unwrap());
        assert!(review.flush(Duration::from_secs(10)));
        let mut fresh = Review::new(Io::start(|| {}));
        fresh.want([dir.clone()]);
        wait(&mut fresh);
        assert_eq!(fresh.folder(&dir).unwrap().marks.keys().cloned().collect::<Vec<_>>(), *after.last().unwrap());
    }

    #[test]
    fn notes_say_when() {
        let now = 1_763_894_400_000;
        assert_eq!(ago(now - 5_000, now), "just now");
        assert_eq!(ago(now - 5 * 60_000, now), "5 min ago");
        assert_eq!(ago(now - 3 * 3_600_000, now), "3 h ago");
        assert_eq!(ago(now + 5_000, now), "just now");
        assert_eq!(who(""), "someone");
    }
}
