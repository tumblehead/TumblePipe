//! The decode engine: a pool of workers filling the frame buffer around the
//! playhead, frames nearest it first.
//!
//! What plays is a set of **sources** (each a take's media once open, else
//! nothing to decode while it opens or after it failed) and a [`Timeline`]
//! of **clips** placed on tracks, each showing a run of one source's frames
//! from its in point. A playlist is one track of clips laid end to end, one
//! source each; an edit stacks tracks, and where clips overlap the top-most
//! one with media shows, the ones below showing through its fades
//! ([`Timeline::layers`], as `th_edit::picture` has it). Frames are buffered
//! by source, so clips of one take share them and moving a clip decodes
//! nothing again. The workers take jobs from shared state:
//!
//! - a frame of the window (the layers of the positions `AHEAD` of the
//!   playhead and `BEHIND` it), nearest first. A movie decodes forward from a
//!   keyframe, so a source's movie has one decode at a time, through a
//!   decoder the engine keeps open (the last `DECODERS` used); an EXR frame
//!   is one file read, so up to `EXR_READS` run side by side, except that
//!   while one of the playhead's own frames is being read the others wait,
//!   leaving the file server to it.
//! - a source's proxy (its first frame, small) for the track, `PROXY_JOBS`
//!   at a time, before the window's other frames but never while one of the
//!   playhead's own frames waits: a take's thumbnail costs one decode, and
//!   behind a window of render reads it would wait minutes.
//!
//! A job is claimed with the source's generation (bumped when its view or
//! media changes) and the window it was claimed in (bumped when the
//! playhead or the clips move, or a source changes). A decode asks between
//! pieces whether it is still wanted: not once its source changed, nor, for
//! a window frame, once the frame left the window. Frames of a changed
//! source never reach the buffer.

use std::collections::{HashMap, HashSet};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};

use th_media::{Error, ExrView, Frame, FrameBuffer, FrameKey, Media, MovieDecoder, Proxy};

/// Frames ahead of the playhead the workers fill.
const AHEAD: usize = 96;
/// Frames behind the playhead they fill.
const BEHIND: usize = 48;
/// EXR frames read at once: a file server serves a few reads side by side
/// faster than one, and many slower.
const EXR_READS: usize = 6;
/// Workers: every EXR read, and a movie decode beside them.
const WORKERS: usize = EXR_READS + 1;
/// Proxies made at once.
const PROXY_JOBS: usize = 2;
/// Movie decoders kept open, most recently used first: enough for a few
/// stacks of layer movies, each layer its own decoder.
const DECODERS: usize = 16;
/// Slice threads a movie decoder runs on, at most: FFmpeg warns past 16,
/// and a review movie's slices give no more than that.
const MOVIE_THREADS: usize = 16;
/// The most pixels a proxy has each way: twice what the track shows at most,
/// for high-DPI screens.
const PROXY_SIZE: (u32, u32) = (240, 120);

/// A clip: a run of one source's frames placed on a track.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Clip {
    /// The source it shows.
    pub source: usize,
    /// Its track: a higher one is over a lower.
    pub track: usize,
    /// The timeline position of its first frame.
    pub at: usize,
    pub frames: usize,
    /// The source frame (media index) its first frame shows: its in point.
    pub first: usize,
    /// Frames it fades in and out over: inside a fade the tracks below show
    /// through.
    pub fade_in: usize,
    pub fade_out: usize,
    /// Frames per second as a ratio.
    pub rate: (i32, i32),
}

impl Clip {
    /// A clip of `source` from its first frame, `frames` long, no fades, at
    /// the start of the bottom track (`Timeline::end_to_end` places it).
    #[cfg(test)]
    pub fn cut(source: usize, frames: usize, rate: (i32, i32)) -> Self {
        Self { source, track: 0, at: 0, frames, first: 0, fade_in: 0, fade_out: 0, rate }
    }

    /// The position after its last frame.
    pub fn end(&self) -> usize {
        self.at + self.frames
    }

    pub fn covers(&self, position: usize) -> bool {
        self.at <= position && position < self.end()
    }

    /// The frame it shows at `position` (which it covers).
    pub fn key(&self, position: usize) -> FrameKey {
        FrameKey { source: self.source, index: self.first + (position - self.at) }
    }

    /// Whether it shows source frame `index`.
    fn shows(&self, index: usize) -> bool {
        self.first <= index && index < self.first + self.frames
    }

    /// Where source frame `index` falls on the timeline by this clip, before
    /// its start (negative) or past its end when it does not show it.
    fn place(&self, index: usize) -> i64 {
        self.at as i64 + index as i64 - self.first as i64
    }

    /// How much of it shows over the tracks below at `position` (which it
    /// covers): 1 outside its fades; frame `k` of an `n`-frame fade is
    /// `(k + 0.5) / n`, as `th_edit::picture::fade` has it.
    pub fn opacity(&self, position: usize) -> f32 {
        let into = position - self.at;
        let left = self.end() - 1 - position;
        let mut opacity = 1.0f32;
        if into < self.fade_in {
            opacity = opacity.min((into as f32 + 0.5) / self.fade_in as f32);
        }
        if left < self.fade_out {
            opacity = opacity.min((left as f32 + 0.5) / self.fade_out as f32);
        }
        opacity
    }
}

/// What a source plays.
#[derive(Debug, Clone)]
pub struct Content {
    /// None while its take opens, or when it failed: nothing to decode.
    pub media: Option<Arc<Media>>,
    /// The EXR view shown (meaningless for a movie).
    pub view: ExrView,
    /// A stack's hidden layers: bit `i` hides layer `i`, bottom first
    /// (meaningless for anything else).
    pub hidden: u64,
    /// Make a proxy of its first frame (the UI has none of this media yet).
    pub wants_proxy: bool,
}

impl Content {
    /// A source with nothing to decode.
    pub fn none() -> Self {
        Self { media: None, view: ExrView::default(), hidden: 0, wants_proxy: false }
    }

    /// Whether its frames are EXR reads (an EXR sequence, or a stack of
    /// them), which run side by side.
    fn is_exr(&self) -> bool {
        self.media.as_deref().is_some_and(Media::is_exr)
    }
}

/// Whether layer `layer` of a stack is hidden by `hidden`.
pub fn is_hidden(hidden: u64, layer: usize) -> bool {
    layer < 64 && hidden & (1 << layer) != 0
}

/// One picture layer of a timeline position.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Layer {
    /// The clip, by index.
    pub clip: usize,
    pub key: FrameKey,
    /// How much of it shows over the layers below.
    pub opacity: f32,
}

/// The clips placed on their tracks. Clips of one track never overlap.
#[derive(Debug, Clone)]
pub struct Timeline {
    pub clips: Vec<Clip>,
    len: usize,
    /// Each source's clips, by index.
    by_source: HashMap<usize, Vec<usize>>,
    /// The clips by track, top first, each track's by position.
    order: Vec<usize>,
}

impl Timeline {
    pub fn new(clips: Vec<Clip>) -> Self {
        let len = clips.iter().map(Clip::end).max().unwrap_or(0);
        let mut by_source: HashMap<usize, Vec<usize>> = HashMap::new();
        for (i, c) in clips.iter().enumerate() {
            by_source.entry(c.source).or_default().push(i);
        }
        let mut order: Vec<usize> = (0..clips.len()).collect();
        order.sort_by_key(|&i| (std::cmp::Reverse(clips[i].track), clips[i].at));
        Self { clips, len, by_source, order }
    }

    /// `clips` laid end to end on the bottom track, in order: a playlist.
    #[cfg(test)]
    pub fn end_to_end(clips: Vec<Clip>) -> Self {
        let mut at = 0;
        let clips = clips
            .into_iter()
            .map(|c| {
                let placed = Clip { track: 0, at, ..c };
                at += c.frames;
                placed
            })
            .collect();
        Self::new(clips)
    }

    /// The position after the last clip's last frame.
    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// The first frame of `clip`.
    #[cfg(test)]
    fn clip_start(&self, clip: usize) -> usize {
        self.clips[clip].at
    }

    /// The clips covering `position`, top track first.
    fn covering(&self, position: usize) -> impl Iterator<Item = usize> + '_ {
        self.order.iter().copied().filter(move |&i| self.clips[i].covers(position))
    }

    /// The top-most clip at `position`, whatever it plays.
    #[cfg(test)]
    fn top(&self, position: usize) -> Option<usize> {
        self.covering(position).next()
    }

    /// The frame the playhead stands on at `position`: the top clip's there;
    /// in a gap, or past the end, that of the last clip starting before it
    /// (its index past the clip's frames).
    #[cfg(test)]
    fn key(&self, position: usize) -> FrameKey {
        let clip = self.top(position).or_else(|| {
            self.order.iter().copied().filter(|&i| self.clips[i].at <= position).max_by_key(|&i| (self.clips[i].at, self.clips[i].track))
        });
        match clip {
            Some(i) => FrameKey { source: self.clips[i].source, index: self.clips[i].first + (position - self.clips[i].at) },
            None => FrameKey { source: 0, index: position },
        }
    }

    /// The picture layers of `position`, bottom first: the top-most clip
    /// whose source `plays`, and under it, while it fades, what the same
    /// rule gives for the tracks below. Empty: black.
    pub fn layers(&self, position: usize, plays: impl Fn(usize) -> bool) -> Vec<Layer> {
        let mut out = Vec::new();
        for clip in self.covering(position) {
            let c = &self.clips[clip];
            if !plays(c.source) {
                continue;
            }
            let opacity = c.opacity(position);
            out.push(Layer { clip, key: c.key(position), opacity });
            if opacity >= 1.0 {
                break;
            }
        }
        out.reverse();
        out
    }

    /// The timeline positions showing `key` (whether or not a clip above
    /// hides it).
    pub fn positions(&self, key: FrameKey) -> impl Iterator<Item = usize> + '_ {
        let clips = self.by_source.get(&key.source).map_or(&[][..], Vec::as_slice);
        clips.iter().map(|&i| &self.clips[i]).filter(move |c| c.shows(key.index)).map(move |c| c.place(key.index) as usize)
    }

    /// Whether `key` is in the window the workers fill around `here`: a clip
    /// shows it there.
    fn in_window(&self, key: FrameKey, here: usize) -> bool {
        self.positions(key).any(|at| at <= here + AHEAD && at + BEHIND >= here)
    }

    /// How far `key` is from `here` in the workers' terms, for eviction: a
    /// frame behind counts as AHEAD/BEHIND times as far as the same gap
    /// ahead, so the frames the workers fill (their window) always outrank
    /// the ones they only passed on the way, and playback's frames go last.
    /// A frame no clip shows (passed on the way to a clip's in point) ranks
    /// past every window frame, by where it would fall; one of a source no
    /// clip shows is furthest.
    fn eviction_distance(&self, key: FrameKey, here: usize) -> usize {
        let clips = self.by_source.get(&key.source).map_or(&[][..], Vec::as_slice);
        let here = here as i64;
        clips
            .iter()
            .map(|&i| {
                let at = self.clips[i].place(key.index);
                let d = if at >= here { (at - here) as usize } else { (here - at) as usize * AHEAD / BEHIND };
                // A window frame is at most AHEAD away.
                if self.clips[i].shows(key.index) { d } else { AHEAD + 1 + d }
            })
            .min()
            .unwrap_or(usize::MAX)
    }
}

/// Something a worker does.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Job {
    /// Decode a frame of the window.
    Frame(FrameKey),
    /// Make a source's proxy.
    Proxy(usize),
}

impl Job {
    fn source(self) -> usize {
        match self {
            Self::Frame(key) => key.source,
            Self::Proxy(source) => source,
        }
    }
}

/// What a worker does a job with, and what its results are checked against
/// when they come back.
struct Claim {
    job: Job,
    media: Arc<Media>,
    view: ExrView,
    hidden: u64,
    /// The source's generation: results of an older one are dropped.
    generation: u64,
    /// The window it was claimed in: only a frame of the current window says
    /// whether the current window fits.
    window: u64,
}

/// Everything the workers and the UI share.
struct State {
    buffer: FrameBuffer,
    timeline: Arc<Timeline>,
    /// Each source's content.
    contents: Vec<Content>,
    /// The playhead's timeline position.
    playhead: usize,
    /// Each source's generation: how many times its view or media changed.
    generations: Vec<u64>,
    /// How many times the window changed: the playhead or the clips moved,
    /// or a source changed.
    window: u64,
    /// Jobs a worker is doing right now.
    running: HashSet<Job>,
    /// Frames that failed to decode, and why.
    failed: HashMap<FrameKey, String>,
    /// Proxies made and not collected yet, with the generation they are of.
    proxies: Vec<(usize, u64, Proxy)>,
    /// A frame claimed in this window was evicted as soon as it landed: the
    /// budget holds less than the window, and everything nearer is already
    /// in. Decoding on would only evict and re-decode the same frame, so
    /// frames rest until the window changes.
    saturated: bool,
    quit: bool,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    notify: Mutex<Option<Box<dyn Fn() + Send>>>,
    /// Movie decoders not in use, most recently used first.
    decoders: Mutex<Vec<MovieDecoder>>,
}

pub struct Engine {
    shared: Arc<Shared>,
    workers: Vec<JoinHandle<()>>,
}

impl std::fmt::Debug for Engine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Engine").field("timeline", &self.timeline()).finish_non_exhaustive()
    }
}

impl Engine {
    /// Start the workers on `sources` placed as `timeline` says, buffering
    /// up to `budget_bytes`. Every clip's source is one of `sources`.
    pub fn start(sources: Vec<Content>, timeline: Timeline, budget_bytes: usize) -> Self {
        let shared = Arc::new(Shared {
            state: Mutex::new(new_state(budget_bytes, timeline, sources)),
            wake: Condvar::new(),
            notify: Mutex::new(None),
            decoders: Mutex::new(Vec::new()),
        });
        let workers = (0..WORKERS)
            .map(|worker| {
                let shared = Arc::clone(&shared);
                thread::Builder::new()
                    .name(format!("th_player decode {worker}"))
                    .spawn(move || work(&shared))
                    .expect("spawning a decode thread")
            })
            .collect();
        Self { shared, workers }
    }

    fn state(&self) -> std::sync::MutexGuard<'_, State> {
        self.shared.state.lock().unwrap()
    }

    /// Called (from a worker thread) whenever a frame or a proxy lands.
    pub fn set_notifier(&self, notify: impl Fn() + Send + 'static) {
        *self.shared.notify.lock().unwrap() = Some(Box::new(notify));
    }

    pub fn timeline(&self) -> Arc<Timeline> {
        Arc::clone(&self.state().timeline)
    }

    /// Put the playhead on timeline position `position`.
    pub fn set_playhead(&self, position: usize) {
        move_playhead(&mut self.state(), position);
        self.shared.wake.notify_all();
    }

    /// Place the clips as `timeline` says from now on: every source keeps
    /// its frames. A playhead past the new end moves to its last frame. The
    /// new timeline.
    pub fn set_timeline(&self, timeline: Timeline) -> Arc<Timeline> {
        let timeline = set_timeline(&mut self.state(), timeline);
        self.shared.wake.notify_all();
        timeline
    }

    /// Play `content` as `source` (a new one when it is one past the last),
    /// and place the clips as `timeline` says, at once: the source's frames
    /// and failures go; every other source's stay. The new timeline.
    pub fn replace_source(&self, source: usize, content: Content, timeline: Timeline) -> Arc<Timeline> {
        let mut state = self.state();
        replace_source(&mut state, source, content);
        let timeline = set_timeline(&mut state, timeline);
        drop(state);
        self.shared.wake.notify_all();
        timeline
    }

    pub fn frame(&self, key: FrameKey) -> Option<Frame> {
        self.state().buffer.get(key).cloned()
    }

    pub fn is_buffered(&self, key: FrameKey) -> bool {
        self.state().buffer.contains(key)
    }

    /// Why `key` failed to decode, if it did.
    pub fn failure(&self, key: FrameKey) -> Option<String> {
        self.state().failed.get(&key).cloned()
    }

    /// The picture layers of `position`, bottom first (see
    /// [`Timeline::layers`]): only sources with media play.
    pub fn layers(&self, position: usize) -> Vec<Layer> {
        let state = self.state();
        layers(&state, position)
    }

    /// Which timeline positions show a buffered frame, for the track.
    pub fn buffered_positions(&self) -> Vec<usize> {
        let state = self.state();
        let mut positions: Vec<usize> = state.buffer.keys().flat_map(|k| state.timeline.positions(k).collect::<Vec<_>>()).collect();
        positions.sort_unstable();
        positions.dedup();
        positions
    }

    /// The proxies made since the last call, by source, each of the media
    /// the source plays now.
    pub fn take_proxies(&self) -> Vec<(usize, Proxy)> {
        let mut state = self.state();
        let made = std::mem::take(&mut state.proxies);
        made.into_iter().filter(|(source, generation, _)| state.generations[*source] == *generation).map(|(s, _, p)| (s, p)).collect()
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.state().quit = true;
        self.shared.wake.notify_all();
        // A running decode stops at its next piece or frame.
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

fn new_state(budget_bytes: usize, timeline: Timeline, contents: Vec<Content>) -> State {
    State {
        buffer: FrameBuffer::new(budget_bytes),
        playhead: 0,
        generations: vec![0; contents.len()],
        timeline: Arc::new(timeline),
        contents,
        window: 0,
        running: HashSet::new(),
        failed: HashMap::new(),
        proxies: Vec::new(),
        saturated: false,
        quit: false,
    }
}

/// The picture layers of `position`: sources with media play.
fn layers(state: &State, position: usize) -> Vec<Layer> {
    state.timeline.layers(position, |source| state.contents.get(source).is_some_and(|c| c.media.is_some()))
}

/// The frames the playhead shows.
fn playhead_keys(state: &State) -> Vec<FrameKey> {
    layers(state, state.playhead).into_iter().map(|l| l.key).collect()
}

/// Whether a job of `source` is running.
fn source_busy(state: &State, source: usize) -> bool {
    state.running.iter().any(|job| job.source() == source)
}

/// EXR reads running.
fn exr_reads(state: &State) -> usize {
    state.running.iter().filter(|job| state.contents[job.source()].is_exr()).count()
}

/// Whether one of the playhead's own EXR frames is being read and not in
/// yet: the other EXR reads wait for it.
fn playhead_read_alone(state: &State) -> bool {
    playhead_keys(state)
        .into_iter()
        .any(|key| state.contents[key.source].is_exr() && state.running.contains(&Job::Frame(key)) && !state.buffer.contains(key))
}

/// Whether `key` still needs decoding: of a source with media, not
/// buffered, failed or being decoded. (A clip past its media's end fails
/// there, and says so.)
fn waits(state: &State, key: FrameKey) -> bool {
    state.contents[key.source].media.is_some()
        && !state.buffer.contains(key)
        && !state.failed.contains_key(&key)
        && !state.running.contains(&Job::Frame(key))
}

/// The window's frames that still need decoding, nearest the playhead
/// first, each position's top layer first.
fn waiting_frames(state: &State) -> impl Iterator<Item = FrameKey> + '_ {
    let (here, len) = (state.playhead, state.timeline.len());
    let ahead = (0..=AHEAD).map(move |d| here + d).filter(move |&p| p < len);
    let behind = (1..=BEHIND).filter_map(move |d| here.checked_sub(d));
    ahead.chain(behind).flat_map(move |p| layers(state, p).into_iter().rev().map(|l| l.key)).filter(move |&k| waits(state, k))
}

/// Whether a job of `source` may start beside the running ones: one decode
/// at a time of a movie; EXR reads up to their limit, and none beside the
/// playhead's own.
fn may_start(state: &State, source: usize) -> bool {
    if state.contents[source].is_exr() { exr_reads(state) < EXR_READS && !playhead_read_alone(state) } else { !source_busy(state, source) }
}

/// Whether one of the playhead's own frames waits to be decoded: they are
/// what the window shows, so they go before everything.
fn playhead_waits(state: &State) -> bool {
    !state.saturated && playhead_keys(state).into_iter().any(|k| waits(state, k))
}

/// The next job: the playhead's frames while they wait; else a proxy, so a
/// take's thumbnail never waits behind a whole window of render reads from
/// the file server (one small decode per take); else the nearest window
/// frame that may start.
fn next_job(state: &State) -> Option<Job> {
    if state.timeline.is_empty() {
        return None;
    }
    if !playhead_waits(state)
        && let Some(proxy) = next_proxy(state)
    {
        return Some(proxy);
    }
    if state.saturated {
        return None;
    }
    waiting_frames(state).find(|k| may_start(state, k.source)).map(Job::Frame)
}

/// A proxy wanted and allowed to start, `PROXY_JOBS` at a time.
fn next_proxy(state: &State) -> Option<Job> {
    let proxies = state.running.iter().filter(|job| matches!(job, Job::Proxy(_))).count();
    if proxies >= PROXY_JOBS {
        return None;
    }
    (0..state.contents.len())
        .find(|&source| {
            let content = &state.contents[source];
            content.media.is_some() && content.wants_proxy && !state.running.contains(&Job::Proxy(source)) && may_start(state, source)
        })
        .map(Job::Proxy)
}

/// The window changed: whatever saturated the old one says nothing of it.
fn new_window(state: &mut State) {
    state.window += 1;
    state.saturated = false;
}

fn move_playhead(state: &mut State, position: usize) {
    state.playhead = position;
    new_window(state);
}

/// What a source showed is gone: its frames, failures and proxies, and any
/// running job's results.
fn forget_source(state: &mut State, source: usize) {
    state.generations[source] += 1;
    state.buffer.remove_source(source);
    state.failed.retain(|k, _| k.source != source);
    new_window(state);
}

/// New content for `source` (a new source when it is one past the last): its
/// frames show the old content, so they go, and so do its failures (a
/// missing channel of the old view says nothing of the new). A new view or
/// other hidden layers come as new content.
fn replace_source(state: &mut State, source: usize, content: Content) {
    if source == state.contents.len() {
        state.contents.push(content);
        state.generations.push(0);
        new_window(state);
        return;
    }
    state.contents[source] = content;
    forget_source(state, source);
}

/// The clips placed anew: frames stay with their sources; a playhead past
/// the end moves to the last frame.
fn set_timeline(state: &mut State, timeline: Timeline) -> Arc<Timeline> {
    debug_assert!(timeline.clips.iter().all(|c| c.source < state.contents.len()), "a clip of a source the engine has not");
    state.timeline = Arc::new(timeline);
    state.playhead = state.playhead.min(state.timeline.len().saturating_sub(1));
    new_window(state);
    Arc::clone(&state.timeline)
}

/// Claim `job`, whose source has media.
fn claim(state: &mut State, job: Job) -> Claim {
    state.running.insert(job);
    let content = &state.contents[job.source()];
    Claim {
        job,
        media: Arc::clone(content.media.as_ref().expect("a job's source has media")),
        view: content.view.clone(),
        hidden: content.hidden,
        generation: state.generations[job.source()],
        window: state.window,
    }
}

/// Whether the job of `claim` is still wanted: its source unchanged and,
/// for a window frame, the frame still in the window.
fn wanted(state: &State, claim: &Claim) -> bool {
    !state.quit
        && state.generations[claim.job.source()] == claim.generation
        && match claim.job {
            Job::Frame(key) => state.timeline.in_window(key, state.playhead),
            Job::Proxy(_) => true,
        }
}

/// A frame a job of `claim` decoded, its target or passed on the way.
fn store(state: &mut State, key: FrameKey, frame: Frame, claim: &Claim) {
    if state.generations[key.source] != claim.generation {
        return;
    }
    let shown: HashSet<FrameKey> = playhead_keys(state).into_iter().collect();
    let (here, timeline) = (state.playhead, Arc::clone(&state.timeline));
    // The playhead's frames are at distance 0, never evicted; any other
    // frame at least 1.
    state.buffer.insert(key, frame, |k| if shown.contains(&k) { 0 } else { timeline.eviction_distance(k, here).max(1) });
}

/// What a job came to.
enum Outcome {
    Frame(Result<(), Error>),
    Proxy(Option<Proxy>),
}

/// Bookkeeping once the job of `claim` is over.
fn finish(state: &mut State, claim: &Claim, outcome: Outcome) {
    state.running.remove(&claim.job);
    let source = claim.job.source();
    if state.generations[source] != claim.generation {
        return;
    }
    match (claim.job, outcome) {
        (Job::Frame(_), Outcome::Frame(Err(Error::Cancelled { .. }))) => {}
        (Job::Frame(key), Outcome::Frame(Err(error))) => {
            state.failed.insert(key, error.to_string());
        }
        (Job::Frame(key), Outcome::Frame(Ok(()))) => {
            if claim.window == state.window && !state.buffer.contains(key) {
                state.saturated = true;
            }
        }
        (Job::Proxy(_), Outcome::Proxy(proxy)) => {
            // Made or not (its frame failed, and says so in the window), it
            // is not asked for again.
            state.contents[source].wants_proxy = false;
            if let Some(proxy) = proxy {
                state.proxies.push((source, claim.generation, proxy));
            }
        }
        (job, _) => unreachable!("{job:?} came to another job's outcome"),
    }
}

/// `work`, or the error its panic was: a decoder bug is reported on the
/// frame it hit, never left as a frame forever in flight.
fn caught<T>(work: impl FnOnce() -> Result<T, Error>, index: usize) -> Result<T, Error> {
    catch_unwind(AssertUnwindSafe(work)).unwrap_or_else(|panic| {
        let message = panic
            .downcast_ref::<&str>()
            .map(|s| (*s).to_owned())
            .or_else(|| panic.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "no message".to_owned());
        Err(Error::Decode { index, message: format!("the decoder panicked: {message}") })
    })
}

/// Decode frame `index` of `claim`'s media, handing every frame decoded on
/// the way to `keep`, while `wanted` says so.
fn decode(
    shared: &Shared,
    claim: &Claim,
    index: usize,
    mut keep: impl FnMut(usize, &Frame),
    wanted: impl Fn() -> bool,
) -> Result<Frame, Error> {
    caught(
        || match &*claim.media {
            Media::Stack(layers) => {
                // Each layer's frame alone (not those passed on the way: a
                // layer movie's frames are all keyframes), then the picture.
                let frame = decode_stack(layers, claim.hidden, index, claim.media.is_exr(), |layer| {
                    decode_media(shared, layer, &layer.default_view(), true, index, |_, _| {}, &wanted)
                })?;
                keep(index, &frame);
                Ok(frame)
            }
            media => decode_media(shared, media, &claim.view, false, index, keep, &wanted),
        },
        index,
    )
}

/// Frame `index` of a stack of `layers` (EXR layers when `exr`): every
/// layer not hidden that has that frame, each from `layer_frame`, composited
/// bottom first. With every layer hidden it is black, as big as the bottom
/// layer's frame.
fn decode_stack(
    layers: &[(String, Arc<Media>)],
    hidden: u64,
    index: usize,
    exr: bool,
    mut layer_frame: impl FnMut(&Media) -> Result<Frame, Error>,
) -> Result<Frame, Error> {
    let mut frames = Vec::new();
    for (at, (_, layer)) in layers.iter().enumerate() {
        if !is_hidden(hidden, at) && index < layer.frame_count() {
            frames.push(layer_frame(layer)?);
        }
    }
    let Some(first) = frames.first() else {
        let size = layers.iter().find(|l| index < l.1.frame_count()).map(|l| layer_frame(&l.1)).transpose()?;
        let (width, height) = size.map_or((1, 1), |f| (f.width, f.height));
        return Ok(th_media::composite::black(width, height, exr));
    };
    th_media::composite::composite(first.width, first.height, &frames).map_err(|message| Error::Decode { index, message })
}

/// Frame `index` of a movie or an EXR sequence (`view` of it, with its own
/// alpha when `alpha`), handing every frame decoded on the way to `keep`.
fn decode_media(
    shared: &Shared,
    media: &Media,
    view: &ExrView,
    alpha: bool,
    index: usize,
    mut keep: impl FnMut(usize, &Frame),
    wanted: &dyn Fn() -> bool,
) -> Result<Frame, Error> {
    match media {
        Media::Exr { sequence, .. } => {
            let frame =
                if alpha { sequence.decode_with_alpha_while(index, view, wanted)? } else { sequence.decode_while(index, view, wanted)? };
            keep(index, &frame);
            Ok(frame)
        }
        Media::Movie(movie) => {
            let held = {
                let mut decoders = shared.decoders.lock().unwrap();
                decoders.iter().position(|d| Arc::ptr_eq(d.index(), movie)).map(|at| decoders.remove(at))
            };
            let threads = thread::available_parallelism().map_or(MOVIE_THREADS, |n| n.get().min(MOVIE_THREADS));
            let mut decoder = match held {
                Some(decoder) => decoder,
                None => MovieDecoder::open(Arc::clone(movie), threads)?,
            };
            let frame = decoder.decode(index, keep, wanted);
            let mut decoders = shared.decoders.lock().unwrap();
            decoders.insert(0, decoder);
            decoders.truncate(DECODERS);
            frame
        }
        Media::Stack(_) => Err(Error::Decode { index, message: "a stack in a stack".into() }),
    }
}

fn work(shared: &Shared) {
    loop {
        let claim = {
            let mut state = shared.state.lock().unwrap();
            loop {
                if state.quit {
                    return;
                }
                if let Some(job) = next_job(&state) {
                    break claim(&mut state, job);
                }
                state = shared.wake.wait(state).unwrap();
            }
        };
        let still_wanted = || wanted(&shared.state.lock().unwrap(), &claim);
        let keep = |index, frame: &Frame| {
            let key = FrameKey { source: claim.job.source(), index };
            store(&mut shared.state.lock().unwrap(), key, frame.clone(), &claim);
        };
        let outcome = match claim.job {
            Job::Frame(key) => Outcome::Frame(decode(shared, &claim, key.index, keep, still_wanted).map(|_| ())),
            Job::Proxy(source) => {
                let first = FrameKey { source, index: 0 };
                let buffered = shared.state.lock().unwrap().buffer.get(first).cloned();
                let frame = buffered.map_or_else(|| decode(shared, &claim, 0, keep, still_wanted).ok(), Some);
                Outcome::Proxy(frame.map(|f| th_media::proxy(&f, PROXY_SIZE.0, PROXY_SIZE.1)))
            }
        };
        finish(&mut shared.state.lock().unwrap(), &claim, outcome);
        // Its job is over: a waiting worker may start one it held back.
        shared.wake.notify_all();
        if let Some(notify) = &*shared.notify.lock().unwrap() {
            notify();
        }
    }
}

#[cfg(test)]
mod tests {
    //! Properties of the timeline arithmetic and of the scheduling, driven
    //! against fake media that decodes like `MovieDecoder`: from the keyframe
    //! at or before the target up to it, keeping every frame it passes.
    //! Timelines are playlists (one track, clips end to end) and stacks of
    //! tracks with gaps, in points, fades and sources shown by several clips.
    //! Overlapping jobs, view changes, replaced sources and moved clips are
    //! driven step by step; the threads themselves are driven against real
    //! media at the end.

    use super::*;
    use half::f16;
    use proptest::prelude::*;
    use th_media::{ExrSequence, MovieIndex, Pixels};

    const FRAME_BYTES: usize = 64;
    /// Jobs the fake worker may run before it must have come to rest.
    const MAX_DECODES: usize = 4 * 3 * (AHEAD + BEHIND + 1) + 16;

    fn fixture(name: &str) -> String {
        format!("{}/../media/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))
    }

    /// Media standing in for a source's: a movie or an EXR sequence. The
    /// scheduling only asks which; the files are never read.
    fn media(exr: bool) -> Arc<Media> {
        static MOVIE: std::sync::OnceLock<Arc<MovieIndex>> = std::sync::OnceLock::new();
        if exr {
            let sequence = ExrSequence::new(1, 1, Vec::new());
            let header = th_media::ExrHeader { layers: Vec::new(), colour_space: Some("ACEScg".into()) };
            Arc::new(Media::Exr { sequence, header, colour_space: "ACEScg".into(), rate: (24, 1) })
        } else {
            let index = MOVIE.get_or_init(|| Arc::new(MovieIndex::read(&fixture("grey_ramp_gop5.mp4")).unwrap()));
            Arc::new(Media::Movie(Arc::clone(index)))
        }
    }

    fn frame() -> Frame {
        tagged(1)
    }

    /// A frame whose width records the view it was decoded with.
    fn tagged(view: u32) -> Frame {
        Frame { width: view, height: 1, pixels: Arc::new(Pixels::Rgba16f(vec![f16::ZERO; FRAME_BYTES / 2])) }
    }

    /// A playlist of clips `frames` long, one source each.
    fn playlist(frames: &[usize]) -> Timeline {
        Timeline::end_to_end(frames.iter().enumerate().map(|(s, &n)| Clip::cut(s, n, (24, 1))).collect())
    }

    /// Clip lengths, empty clips included, with at least one frame overall.
    fn clips() -> impl Strategy<Value = Vec<usize>> {
        prop::collection::vec(prop_oneof![1 => Just(0usize), 4 => 1usize..200], 1..6)
            .prop_filter("an empty playlist", |c| c.iter().sum::<usize>() > 0)
    }

    /// A stack of up to three tracks over `sources` sources: clips after
    /// gaps, from in points, with fades, a source shown by any number of
    /// clips; at least one frame overall.
    fn stack(sources: usize) -> impl Strategy<Value = Timeline> {
        let clip = (0usize..30, 1usize..80, 0usize..20, 0..sources, 0usize..8, 0usize..8);
        prop::collection::vec(prop::collection::vec(clip, 0..4), 1..4)
            .prop_map(|tracks| {
                let mut clips = Vec::new();
                for (track, t) in tracks.into_iter().enumerate() {
                    let mut at = 0;
                    for (gap, frames, first, source, fade_in, fade_out) in t {
                        at += gap;
                        let fade_in = fade_in.min(frames);
                        let fade_out = fade_out.min(frames - fade_in);
                        clips.push(Clip { source, track, at, frames, first, fade_in, fade_out, rate: (24, 1) });
                        at += frames;
                    }
                }
                Timeline::new(clips)
            })
            .prop_filter("an empty stack", |t| !t.is_empty())
    }

    /// A playlist or a stack, and how many sources it plays.
    fn timelines() -> impl Strategy<Value = (usize, Timeline)> {
        prop_oneof![
            clips().prop_map(|frames| (frames.len(), playlist(&frames))),
            (1usize..5).prop_flat_map(|sources| stack(sources).prop_map(move |t| (sources, t))),
        ]
    }

    /// State over `timeline`'s `sources`, movies unless `exr` says, every one
    /// open unless `closed` says.
    fn state_of(sources: usize, timeline: Timeline, exr: &[bool], closed: &[bool], budget_frames: usize, at: usize) -> State {
        let contents = (0..sources)
            .map(|s| Content {
                media: (!closed.get(s).copied().unwrap_or(false)).then(|| media(exr.get(s).copied().unwrap_or(false))),
                view: ExrView::default(),
                hidden: 0,
                wants_proxy: false,
            })
            .collect();
        let mut state = new_state(budget_frames * FRAME_BYTES, timeline, contents);
        state.playhead = at;
        state
    }

    fn state(sources: usize, timeline: Timeline, budget_frames: usize, at: usize) -> State {
        state_of(sources, timeline, &[], &[], budget_frames, at)
    }

    /// The frames a decode of `target` runs through: its keyframe up to it.
    fn decode_run(target: FrameKey, gop: usize) -> impl Iterator<Item = FrameKey> {
        (target.index / gop * gop..=target.index).map(move |index| FrameKey { source: target.source, index })
    }

    /// Run one worker's loop until it rests; the number of jobs it did, or
    /// None if it was still busy after `MAX_DECODES`. A decode that runs into
    /// a broken frame fails there, as `MovieDecoder::decode` does.
    fn run(state: &mut State, gop: usize, broken: &HashSet<FrameKey>) -> Option<usize> {
        for jobs in 0..MAX_DECODES {
            let Some(job) = next_job(state) else { return Some(jobs) };
            let claim = claim(state, job);
            let outcome = match job {
                Job::Frame(target) => {
                    let mut result = Ok(());
                    for key in decode_run(target, gop) {
                        if broken.contains(&key) {
                            result = Err(Error::Decode { index: key.index, message: "broken".into() });
                            break;
                        }
                        store(state, key, frame(), &claim);
                    }
                    Outcome::Frame(result)
                }
                Job::Proxy(_) => Outcome::Proxy(None),
            };
            finish(state, &claim, outcome);
        }
        None
    }

    /// The timeline positions the workers fill around `here`.
    fn window(timeline: &Timeline, here: usize) -> std::ops::Range<usize> {
        here.saturating_sub(BEHIND)..(here + AHEAD + 1).min(timeline.len())
    }

    /// Every frame a clip places in the window around `here`, shown or
    /// hidden under a clip above (a decode passes through those too, and
    /// they rank as the window's).
    fn window_keys(timeline: &Timeline, here: usize) -> HashSet<FrameKey> {
        window(timeline, here).flat_map(|p| timeline.clips.iter().filter(move |c| c.covers(p)).map(move |c| c.key(p))).collect()
    }

    proptest! {
        #[test]
        fn a_playlists_keys_and_positions_round_trip(frames in clips()) {
            let timeline = playlist(&frames);
            prop_assert_eq!(timeline.len(), frames.iter().sum::<usize>());
            for p in 0..timeline.len() {
                let key = timeline.key(p);
                prop_assert!(key.index < frames[key.source], "position {} maps into {:?}, past its clip", p, key);
                prop_assert_eq!(timeline.positions(key).collect::<Vec<_>>(), vec![p]);
                prop_assert_eq!(timeline.layers(p, |_| true), vec![Layer { clip: key.source, key, opacity: 1.0 }]);
            }
            for (clip, &n) in frames.iter().enumerate() {
                if n > 0 {
                    prop_assert_eq!(timeline.key(timeline.clip_start(clip)), FrameKey { source: clip, index: 0 });
                }
            }
        }

        /// The layers of a position: the top one is the top-most clip there
        /// whose source plays, showing its in point plus the way in; each
        /// one under it shows through the fade of the one above; outside a
        /// fade there is one layer, whole.
        #[test]
        fn the_top_playing_clip_shows((sources, timeline) in timelines(), at in 0usize..300, plays in prop::collection::vec(any::<bool>(), 5)) {
            let plays = |s: usize| plays[s % sources.max(1)];
            let layers = timeline.layers(at, plays);
            let top = (0..timeline.clips.len())
                .filter(|&i| timeline.clips[i].covers(at) && plays(timeline.clips[i].source))
                .max_by_key(|&i| timeline.clips[i].track);
            prop_assert_eq!(layers.last().map(|l| l.clip), top);
            for l in &layers {
                let c = &timeline.clips[l.clip];
                prop_assert_eq!(l.key, FrameKey { source: c.source, index: c.first + at - c.at });
                prop_assert!(l.opacity > 0.0 && l.opacity <= 1.0);
            }
            for pair in layers.windows(2) {
                prop_assert!(pair[1].opacity < 1.0);
                prop_assert!(timeline.clips[pair[0].clip].track < timeline.clips[pair[1].clip].track);
            }
            if layers.last().is_some_and(|l| l.opacity >= 1.0) {
                prop_assert_eq!(layers.len(), 1);
            }
        }

        #[test]
        fn window_frames_outrank_the_rest((_sources, timeline) in timelines(), here in any::<prop::sample::Index>()) {
            let here = here.index(timeline.len());
            let inside = window_keys(&timeline, here);
            let rank = |k: FrameKey| timeline.eviction_distance(k, here);
            let shown_here: Vec<FrameKey> = timeline.layers(here, |_| true).into_iter().map(|l| l.key).collect();
            for k in &shown_here {
                prop_assert_eq!(rank(*k), 0);
            }
            let all: HashSet<FrameKey> = (0..timeline.len()).flat_map(|p| timeline.layers(p, |_| true)).map(|l| l.key).collect();
            let worst_inside = inside.iter().map(|&k| rank(k)).max().unwrap_or(0);
            for &k in all.difference(&inside) {
                prop_assert!(worst_inside < rank(k), "a window frame ranks {} but {:?} outside ranks {}", worst_inside, k, rank(k));
            }
            // A frame no clip shows (before an in point) ranks past them all.
            for c in &timeline.clips {
                let before = FrameKey { source: c.source, index: c.first.wrapping_sub(1) };
                if c.first > 0 && timeline.positions(before).next().is_none() {
                    prop_assert!(worst_inside < rank(before), "{:?}", before);
                }
            }
            for &k in &inside {
                prop_assert!(timeline.in_window(k, here), "{:?}", k);
            }
        }

        /// Whatever the budget, the worker comes to rest (no evict-and-redecode
        /// loop when the window does not fit), with the playhead's frames in;
        /// over budget only by the playhead's own frames.
        #[test]
        fn worker_rests_with_playhead_buffered(
            (sources, timeline) in timelines(),
            here in any::<prop::sample::Index>(),
            budget_frames in 1usize..400,
            gop in 1usize..250,
        ) {
            let here = here.index(timeline.len());
            let mut state = state(sources, timeline, budget_frames, here);
            let shown = playhead_keys(&state);
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
            for key in &shown {
                prop_assert!(state.buffer.contains(*key), "{:?}", key);
            }
            prop_assert!(state.buffer.used_bytes() <= budget_frames.max(shown.len()) * FRAME_BYTES);
        }

        /// A budget that holds the window ends with every window frame in,
        /// except the ones whose decode runs into a broken frame, which are
        /// tried once, and those of sources with nothing to decode (which
        /// are no layer: what is under them shows).
        #[test]
        fn roomy_budget_fills_the_window(
            (sources, timeline) in timelines(),
            closed in prop::collection::vec(any::<bool>(), 6),
            here in any::<prop::sample::Index>(),
            spare in 0usize..50,
            gop in 1usize..250,
            broken in prop::collection::vec((0usize..6, 0usize..300), 0..5),
        ) {
            let here = here.index(timeline.len());
            let budget = window_keys(&timeline, here).len() + spare;
            let mut state = state_of(sources, timeline, &[], &closed, budget, here);
            let broken: HashSet<FrameKey> = broken.into_iter().map(|(source, index)| FrameKey { source, index }).collect();
            prop_assert!(run(&mut state, gop, &broken).is_some(), "the worker never rested");
            let decodable = |key: FrameKey| decode_run(key, gop).all(|k| !broken.contains(&k));
            let open = |s: usize| !closed[s];
            for p in window(&state.timeline, here) {
                for layer in state.timeline.layers(p, open) {
                    prop_assert_eq!(state.buffer.contains(layer.key), decodable(layer.key), "window frame {:?}", layer.key);
                }
            }
            let failures: Vec<&FrameKey> = state.failed.keys().filter(|&&k| decodable(k)).collect();
            prop_assert!(failures.is_empty(), "frames marked failed that decode fine: {:?}", failures);
            prop_assert!(state.failed.keys().all(|k| open(k.source)), "a source with nothing to decode was decoded");
        }

        /// Moving the playhead wakes a rested worker, however full the
        /// buffer, and it brings in the new playhead's frames.
        #[test]
        fn moving_the_playhead_wakes_the_worker(
            (sources, timeline) in timelines(),
            moves in prop::collection::vec(any::<prop::sample::Index>(), 1..8),
            budget_frames in 1usize..200,
            gop in 1usize..250,
        ) {
            let mut state = state(sources, timeline, budget_frames, 0);
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some());
            for to in moves {
                let to = to.index(state.timeline.len());
                move_playhead(&mut state, to);
                prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
                for key in playhead_keys(&state) {
                    prop_assert!(state.buffer.contains(key), "playhead frame {:?} not buffered", key);
                }
            }
        }

        /// A decode claimed before the playhead moved, finishing after it
        /// with the buffer full, never stops the workers short of the new
        /// playhead's frames: whatever it brings in (or loses to eviction on
        /// arrival) belongs to the old window, so it says nothing about
        /// whether the new one fits.
        #[test]
        fn a_stale_decode_never_stalls_the_new_window(
            (sources, timeline) in timelines(),
            from in any::<prop::sample::Index>(),
            to in any::<prop::sample::Index>(),
            stale in any::<prop::sample::Index>(),
            budget_frames in 1usize..60,
            gop in 1usize..20,
        ) {
            let len = timeline.len();
            let mut state = state(sources, timeline, budget_frames, from.index(len));
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some());
            let unbuffered: Vec<FrameKey> = (0..len)
                .flat_map(|p| layers(&state, p))
                .map(|l| l.key)
                .filter(|&k| !state.buffer.contains(k))
                .collect();
            if unbuffered.is_empty() {
                return Ok(());
            }
            let target = unbuffered[stale.index(unbuffered.len())];
            let claim = claim(&mut state, Job::Frame(target));
            move_playhead(&mut state, to.index(len));
            for key in decode_run(target, gop) {
                store(&mut state, key, frame(), &claim);
            }
            finish(&mut state, &claim, Outcome::Frame(Ok(())));
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
            for key in playhead_keys(&state) {
                prop_assert!(state.buffer.contains(key), "playhead frame {:?} not buffered after a stale decode landed", key);
            }
        }

        /// A running frame decode is wanted exactly while its source is
        /// unchanged and its frame in the window: a seek away stops it.
        #[test]
        fn a_decode_is_wanted_while_its_frame_is_in_the_window(
            (sources, timeline) in timelines(),
            from in any::<prop::sample::Index>(),
            to in any::<prop::sample::Index>(),
            change in any::<bool>(),
        ) {
            let len = timeline.len();
            let mut state = state(sources, timeline, 1000, from.index(len));
            let Some(Job::Frame(target)) = next_job(&state) else { return Ok(()) };
            let claim = claim(&mut state, Job::Frame(target));
            prop_assert!(wanted(&state, &claim));
            let playhead = to.index(len);
            move_playhead(&mut state, playhead);
            if change {
                let content = Content { view: ExrView { layer: "other".into(), channel: None }, ..state.contents[target.source].clone() };
                replace_source(&mut state, target.source, content);
            }
            prop_assert_eq!(wanted(&state, &claim), !change && state.timeline.in_window(target, playhead));
            // A stopped decode is no failure, and the frame can be claimed again.
            finish(&mut state, &claim, Outcome::Frame(Err(Error::Cancelled { index: target.index })));
            prop_assert!(state.failed.is_empty() && state.running.is_empty());
        }
    }

    /// A render layer `frames` long standing in a stack; its files are never
    /// read.
    fn layer_of(frames: usize) -> Arc<Media> {
        let sequence = ExrSequence::new(1, 1, vec![std::path::PathBuf::new(); frames]);
        let header = th_media::ExrHeader { layers: Vec::new(), colour_space: Some("ACEScg".into()) };
        Arc::new(Media::Exr { sequence, header, colour_space: "ACEScg".into(), rate: (24, 1) })
    }

    proptest! {
        /// A stack's frame is exactly its shown layers that have the frame,
        /// each decoded once and laid over the ones below in order; a hidden
        /// layer, or one that ended, is never decoded; with nothing shown it
        /// is black.
        #[test]
        fn a_stack_composites_its_shown_layers_in_order(
            layers in prop::collection::vec((1usize..6, 0f32..=1.0, 0f32..=1.0), 1..6),
            hidden in any::<u64>(),
            index in 0usize..6,
        ) {
            let stack: Vec<(String, Arc<Media>)> = layers.iter().enumerate().map(|(i, l)| (format!("l{i}"), layer_of(l.0))).collect();
            // Layer i's pixel: premultiplied grey `value` at alpha `alpha`.
            let pixel = |i: usize| [layers[i].1 * layers[i].2, layers[i].1 * layers[i].2, layers[i].1 * layers[i].2, layers[i].2];
            let mut decoded = Vec::new();
            let frame = decode_stack(&stack, hidden, index, true, |media| {
                let i = stack.iter().position(|l| std::ptr::eq(&*l.1, media)).unwrap();
                decoded.push(i);
                Ok(Frame { width: 2, height: 1, pixels: Arc::new(Pixels::Rgba16f(pixel(i).repeat(2).into_iter().map(f16::from_f32).collect())) })
            }).unwrap();
            let shown: Vec<usize> = (0..layers.len()).filter(|&i| !is_hidden(hidden, i) && index < layers[i].0).collect();
            let has_frame = layers.iter().any(|l| index < l.0);
            if shown.is_empty() {
                // Only a size is asked of the bottom layer that has the frame.
                prop_assert!(decoded.len() <= 1);
                prop_assert_eq!(decoded.is_empty(), !has_frame);
            } else {
                prop_assert_eq!(&decoded, &shown, "decoded layers");
            }
            let Pixels::Rgba16f(data) = &*frame.pixels else { panic!("a render stack is RGBA halfs") };
            let expected = shown.iter().fold([0f32; 4], |below, &i| {
                th_media::composite::over_premultiplied(pixel(i).map(|c| f16::from_f32(c).to_f32()), below)
            });
            for (got, want) in data[..4].iter().zip(expected) {
                prop_assert!((got.to_f32() - want).abs() <= 2e-3, "{:?} vs {expected:?}", &data[..4]);
            }
        }
    }

    /// The panic of a decode is its error, naming the panic.
    #[test]
    fn a_panicking_decode_is_an_error() {
        let result: Result<(), Error> = caught(|| panic!("index {} out of bounds", 7), 3);
        let Err(Error::Decode { index: 3, message }) = result else { panic!("not a decode error: {result:?}") };
        assert!(message.contains("index 7 out of bounds"), "{message}");
        let Err(Error::Decode { message, .. }) = caught::<()>(|| panic!("plain"), 0) else { unreachable!() };
        assert!(message.contains("plain"));
    }

    /// One step of several workers sharing the state: start a job, hand over
    /// the next frame of a running decode, end one, stop one no longer wanted,
    /// move the playhead, change a source's view, replace a source's content
    /// (open or not), place the clips anew, or ask for a source's proxy.
    #[derive(Debug, Clone)]
    enum Op {
        Start,
        Frame(prop::sample::Index),
        Finish(prop::sample::Index),
        Cancel(prop::sample::Index),
        Seek(prop::sample::Index),
        View(usize),
        Hide(usize, u8),
        Replace(usize, bool, bool),
        Place(Timeline),
        Proxy(usize),
    }

    fn ops(sources: usize) -> impl Strategy<Value = Vec<Op>> {
        let op = prop_oneof![
            4 => Just(Op::Start),
            4 => any::<prop::sample::Index>().prop_map(Op::Frame),
            2 => any::<prop::sample::Index>().prop_map(Op::Finish),
            1 => any::<prop::sample::Index>().prop_map(Op::Cancel),
            1 => any::<prop::sample::Index>().prop_map(Op::Seek),
            1 => (0usize..6).prop_map(Op::View),
            1 => (0usize..6, any::<u8>()).prop_map(|(c, h)| Op::Hide(c, h)),
            1 => (0usize..6, any::<bool>(), any::<bool>()).prop_map(|(c, open, exr)| Op::Replace(c, open, exr)),
            1 => stack(sources).prop_map(Op::Place),
            1 => (0usize..6).prop_map(Op::Proxy),
        ];
        prop::collection::vec(op, 1..300)
    }

    fn timelines_and_ops() -> impl Strategy<Value = (usize, Timeline, Vec<Op>)> {
        timelines().prop_flat_map(|(sources, timeline)| ops(sources).prop_map(move |ops| (sources, timeline.clone(), ops)))
    }

    proptest! {
        /// However jobs, seeks, view changes, hidden stack layers, replaced
        /// sources and moved clips interleave: the buffer only ever holds
        /// frames of each source's current view, hidden layers and content,
        /// and moving clips keeps every frame; no job runs twice, nor on a
        /// source with nothing to decode; a movie has one job at a time; EXR
        /// reads stay within their limit and none starts beside the
        /// playhead's own; a proxy never starts while a playhead frame
        /// waits, and stays within its limit; a window frame never starts
        /// while a wanted proxy could; a stopped job is no failure.
        #[test]
        fn jobs_keep_to_their_limits_and_never_leave_stale_frames(
            (sources, timeline, ops) in timelines_and_ops(),
            exr in prop::collection::vec(any::<bool>(), 6),
            here in any::<prop::sample::Index>(),
            gop in 1usize..20,
        ) {
            let here = here.index(timeline.len());
            let mut state = state_of(sources, timeline, &exr, &[], usize::MAX / FRAME_BYTES / 2, here);
            // Each source's view, as a number (its layer name).
            let mut views = vec![0u32; sources];
            // Each source's hidden layers.
            let mut hidden = vec![0u64; sources];
            // Running jobs: their claim and the frames left to decode.
            let mut running: Vec<(Claim, Vec<FrameKey>)> = Vec::new();
            let tag = |claim: &Claim| tagged(claim.view.layer.parse::<u32>().unwrap_or(0) * 256 + claim.hidden as u32);
            for op in ops {
                match op {
                    Op::Start => {
                        let alone = playhead_read_alone(&state);
                        let playhead_first = playhead_waits(&state);
                        let proxy_ready = !playhead_waits(&state) && next_proxy(&state).is_some();
                        let shown = playhead_keys(&state);
                        if let Some(job) = next_job(&state) {
                            let source = job.source();
                            prop_assert!(running.iter().all(|r| r.0.job != job), "{:?} claimed twice", job);
                            prop_assert!(state.contents[source].media.is_some(), "{:?} of a source with nothing to decode", job);
                            if state.contents[source].is_exr() {
                                prop_assert!(!alone, "{:?} started beside the playhead's read", job);
                                prop_assert!(exr_reads(&state) < EXR_READS);
                            } else {
                                prop_assert!(!source_busy(&state, source), "a second job on movie source {}", source);
                            }
                            match job {
                                Job::Proxy(_) => prop_assert!(!playhead_first, "a proxy started while the playhead's frame waits"),
                                Job::Frame(key) => {
                                    prop_assert!(!proxy_ready, "{:?} started while a proxy waits", key);
                                    if playhead_first && shown.iter().any(|&k| waits(&state, k) && may_start(&state, k.source)) {
                                        prop_assert!(shown.contains(&key), "the playhead's frames go first, not {:?}", key);
                                    }
                                }
                            }
                            let claim = claim(&mut state, job);
                            let rest = match job {
                                Job::Frame(target) => decode_run(target, gop).collect(),
                                Job::Proxy(source) => vec![FrameKey { source, index: 0 }],
                            };
                            running.push((claim, rest));
                        }
                    }
                    Op::Frame(i) if !running.is_empty() => {
                        let at = i.index(running.len());
                        let r = &mut running[at];
                        if !r.1.is_empty() {
                            let key = r.1.remove(0);
                            store(&mut state, key, tag(&r.0), &r.0);
                        }
                    }
                    Op::Finish(i) if !running.is_empty() => {
                        let (claim, rest) = running.remove(i.index(running.len()));
                        for key in rest {
                            store(&mut state, key, tag(&claim), &claim);
                        }
                        let outcome = match claim.job {
                            Job::Frame(_) => Outcome::Frame(Ok(())),
                            Job::Proxy(_) => Outcome::Proxy(Some(th_media::proxy(&frame(), 4, 4))),
                        };
                        finish(&mut state, &claim, outcome);
                    }
                    Op::Cancel(i) if !running.is_empty() => {
                        let at = i.index(running.len());
                        if !wanted(&state, &running[at].0) {
                            let (claim, _) = running.remove(at);
                            let failed = state.failed.len();
                            let outcome = match claim.job {
                                Job::Frame(k) => Outcome::Frame(Err(Error::Cancelled { index: k.index })),
                                Job::Proxy(_) => Outcome::Proxy(None),
                            };
                            finish(&mut state, &claim, outcome);
                            prop_assert_eq!(state.failed.len(), failed, "a stopped job reported a failure");
                        }
                    }
                    Op::Seek(to) if !state.timeline.is_empty() => {
                        let to = to.index(state.timeline.len());
                        move_playhead(&mut state, to);
                    }
                    Op::View(source) if source < sources => {
                        views[source] += 1;
                        let content = Content { view: ExrView { layer: views[source].to_string(), channel: None }, ..state.contents[source].clone() };
                        replace_source(&mut state, source, content);
                    }
                    Op::Hide(source, mask) if source < sources => {
                        let others: HashSet<FrameKey> = state.buffer.keys().filter(|k| k.source != source).collect();
                        let changed = hidden[source] != u64::from(mask);
                        hidden[source] = u64::from(mask);
                        if changed {
                            let content = Content { hidden: hidden[source], ..state.contents[source].clone() };
                            replace_source(&mut state, source, content);
                            let kept: HashSet<FrameKey> = state.buffer.keys().collect();
                            prop_assert_eq!(kept, others, "hiding layers of source {} kept its frames or touched another's", source);
                        }
                    }
                    Op::Replace(source, open, is_exr) if source < sources => {
                        hidden[source] = 0;
                        let others: HashSet<FrameKey> = state.buffer.keys().filter(|k| k.source != source).collect();
                        // New content decodes as a new view would: tag it so.
                        views[source] += 1;
                        let content = Content {
                            media: open.then(|| media(is_exr)),
                            view: ExrView { layer: views[source].to_string(), channel: None },
                            hidden: 0,
                            wants_proxy: open,
                        };
                        replace_source(&mut state, source, content);
                        let kept: HashSet<FrameKey> = state.buffer.keys().collect();
                        prop_assert_eq!(kept, others, "replacing source {} touched another source's frames", source);
                    }
                    Op::Place(timeline) => {
                        let before: HashSet<FrameKey> = state.buffer.keys().collect();
                        set_timeline(&mut state, timeline);
                        prop_assert_eq!(state.buffer.keys().collect::<HashSet<_>>(), before, "moving clips lost frames");
                        prop_assert!(state.playhead < state.timeline.len().max(1));
                    }
                    Op::Proxy(source) if source < sources => state.contents[source].wants_proxy = true,
                    _ => {}
                }
                let running_jobs: HashSet<Job> = running.iter().map(|r| r.0.job).collect();
                prop_assert_eq!(&state.running, &running_jobs);
                prop_assert!(running.iter().filter(|r| matches!(r.0.job, Job::Proxy(_))).count() <= PROXY_JOBS);
                for key in state.buffer.keys() {
                    let shown = state.buffer.get(key).unwrap().width;
                    prop_assert_eq!(shown, views[key.source] * 256 + hidden[key.source] as u32, "{:?} holds a frame of an old view", key);
                }
            }
        }
    }

    /// The real thing, threads and all: the playhead's frame of a movie and
    /// of an EXR sequence lands, every open source gets its proxy once, a
    /// source with nothing to decode is left alone, a dissolve's two frames
    /// both land, and dropping the engine stops and joins its workers.
    #[test]
    fn the_workers_fill_the_window_and_stop_when_dropped() {
        let exr_path = std::env::temp_dir().join(format!("th_player_engine_{}.exr", std::process::id()));
        {
            use exr::prelude::*;
            let mut attributes = LayerAttributes::default();
            attributes.other.insert(Text::from("oiio:ColorSpace"), AttributeValue::Text(Text::from("ACEScg")));
            let channels = SpecificChannels::rgb(|_: Vec2<usize>| (0.5_f32, 0.25_f32, 0.125_f32));
            let layer = Layer::new((8, 8), attributes, Encoding::UNCOMPRESSED, channels);
            Image::from_layer(layer).write().to_file(&exr_path).unwrap();
        }
        let movie = Arc::new(Media::movie(&fixture("grey_ramp_gop5.mp4")).unwrap());
        let sequence = Arc::new(Media::exr(ExrSequence::new(1001, 1, vec![exr_path.clone(); 3]), (24, 1)).unwrap());
        let open =
            |media: &Arc<Media>| Content { media: Some(Arc::clone(media)), view: media.default_view(), hidden: 0, wants_proxy: true };
        let sources = vec![open(&movie), Content::none(), open(&sequence)];
        let shapes = vec![Clip::cut(0, movie.frame_count(), movie.rate()), Clip::cut(1, 10, (24, 1)), Clip::cut(2, 3, (24, 1))];
        let engine = Engine::start(sources, Timeline::end_to_end(shapes), 1 << 30);
        let (woke, woken) = std::sync::mpsc::channel();
        engine.set_notifier(move || {
            let _ = woke.send(());
        });
        let timeline = engine.timeline();
        let mut proxies = Vec::new();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        let wait_for = |keys: &[FrameKey], proxies: &mut Vec<usize>, want_proxies: usize| {
            while !keys.iter().all(|&k| engine.is_buffered(k)) || proxies.len() < want_proxies {
                assert!(std::time::Instant::now() < deadline, "{keys:?} or the proxies never came ({proxies:?})");
                let _ = woken.recv_timeout(std::time::Duration::from_millis(200));
                proxies.extend(engine.take_proxies().into_iter().map(|(source, _)| source));
            }
        };
        let movie_at = 30;
        engine.set_playhead(movie_at);
        wait_for(&[timeline.key(movie_at)], &mut proxies, 0);
        let exr_at = timeline.clip_start(2) + 2;
        engine.set_playhead(exr_at);
        wait_for(&[FrameKey { source: 2, index: 2 }], &mut proxies, 2);
        proxies.sort_unstable();
        assert_eq!(proxies, [0, 2], "each open source's proxy, once");
        assert!(engine.failure(FrameKey { source: 2, index: 2 }).is_none());
        assert!((0..10).all(|index| !engine.is_buffered(FrameKey { source: 1, index })), "the closed source was decoded");

        // A dissolve: the render over the movie from its in point, fading in.
        let dissolve = vec![
            Clip { source: 0, track: 0, at: 0, frames: 40, first: 5, fade_in: 0, fade_out: 0, rate: (24, 1) },
            Clip { source: 2, track: 1, at: 37, frames: 3, first: 0, fade_in: 3, fade_out: 0, rate: (24, 1) },
        ];
        engine.set_timeline(Timeline::new(dissolve));
        engine.set_playhead(38);
        let layers = engine.layers(38);
        assert_eq!(layers.iter().map(|l| l.key).collect::<Vec<_>>(), [FrameKey { source: 0, index: 43 }, FrameKey { source: 2, index: 1 }]);
        assert!((layers[1].opacity - 0.5).abs() < 1e-6);
        wait_for(&[layers[0].key, layers[1].key], &mut proxies, 2);
        drop(engine);
        std::fs::remove_file(exr_path).ok();
    }
}
