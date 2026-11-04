//! The decode engine: a pool of workers filling the frame buffer around the
//! playhead, frames nearest it first.
//!
//! The clips are laid end to end on a [`Timeline`]. A clip is a shot: its
//! take's media once open, else (opening, or failed) only its length, which
//! nothing decodes. The workers take jobs from shared state:
//!
//! - a frame of the window (`AHEAD` frames from the playhead, `BEHIND`
//!   behind), nearest first. A movie decodes forward from a keyframe, so a
//!   clip's movie has one decode at a time, through a decoder the engine
//!   keeps open (the last `DECODERS` used); an EXR frame is one file read, so
//!   up to `EXR_READS` run side by side, except that while the playhead's own
//!   frame is being read the others wait, leaving the file server to it.
//! - a clip's proxy (its first frame, small) for the track, `PROXY_JOBS` at a
//!   time, before the window's other frames but never while the playhead's
//!   own frame waits: a take's thumbnail costs one decode, and behind a
//!   window of render reads it would wait minutes.
//!
//! A job is claimed with the clip's generation (bumped when its view or media
//! changes) and the window it was claimed in (bumped when the playhead moves
//! or a clip changes). A decode asks between pieces whether it is still
//! wanted: not once its clip changed, nor, for a window frame, once the frame
//! left the window. Frames of a changed clip never reach the buffer.

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

/// A clip's place on the timeline.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Clip {
    pub frames: usize,
    /// Frames per second as a ratio.
    pub rate: (i32, i32),
}

impl Clip {
    /// Frames in one second of the clip, at least one.
    pub fn second(&self) -> usize {
        second_of(self.rate)
    }
}

fn second_of((num, den): (i32, i32)) -> usize {
    (f64::from(num) / f64::from(den)).round().max(1.0) as usize
}

/// What a clip plays.
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
    /// A clip with nothing to decode.
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

/// The clips laid end to end: a timeline position is a frame of one clip.
#[derive(Debug, Clone)]
pub struct Timeline {
    pub clips: Vec<Clip>,
    offsets: Vec<usize>,
}

impl Timeline {
    pub fn new(clips: Vec<Clip>) -> Self {
        let mut offsets = Vec::with_capacity(clips.len());
        let mut at = 0;
        for clip in &clips {
            offsets.push(at);
            at += clip.frames;
        }
        Self { clips, offsets }
    }

    pub fn len(&self) -> usize {
        self.clips.iter().map(|c| c.frames).sum()
    }

    pub fn position(&self, key: FrameKey) -> usize {
        self.offsets[key.clip] + key.index
    }

    pub fn key(&self, position: usize) -> FrameKey {
        let clip = self.offsets.partition_point(|&o| o <= position).saturating_sub(1);
        FrameKey { clip, index: position - self.offsets[clip] }
    }

    /// The first frame of `clip`.
    pub fn clip_start(&self, clip: usize) -> usize {
        self.offsets[clip]
    }

    /// Whether `key` is in the window the workers fill around `playhead`.
    fn in_window(&self, key: FrameKey, playhead: FrameKey) -> bool {
        let (at, here) = (self.position(key), self.position(playhead));
        at <= here + AHEAD && at + BEHIND >= here
    }

    /// How far `key` is from `playhead` in the workers' terms, for eviction:
    /// a frame behind counts as AHEAD/BEHIND times as far as the same gap
    /// ahead, so the frames the workers fill (their window) always outrank
    /// the ones they only passed on the way, and playback's frames go last.
    fn eviction_distance(&self, key: FrameKey, playhead: FrameKey) -> usize {
        let (at, here) = (self.position(key), self.position(playhead));
        if at >= here { at - here } else { (here - at) * AHEAD / BEHIND }
    }
}

/// Something a worker does.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Job {
    /// Decode a frame of the window.
    Frame(FrameKey),
    /// Make a clip's proxy.
    Proxy(usize),
}

impl Job {
    fn clip(self) -> usize {
        match self {
            Self::Frame(key) => key.clip,
            Self::Proxy(clip) => clip,
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
    /// The clip's generation: results of an older one are dropped.
    generation: u64,
    /// The window it was claimed in: only a frame of the current window says
    /// whether the current window fits.
    window: u64,
}

/// Everything the workers and the UI share.
struct State {
    buffer: FrameBuffer,
    timeline: Arc<Timeline>,
    contents: Vec<Content>,
    playhead: FrameKey,
    /// Each clip's generation: how many times its view or media changed.
    generations: Vec<u64>,
    /// How many times the window changed: the playhead moved, or a clip
    /// changed.
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
    /// Start the workers on `clips`, buffering up to `budget_bytes`.
    pub fn start(clips: Vec<(Clip, Content)>, budget_bytes: usize) -> Self {
        let (shapes, contents): (Vec<Clip>, Vec<Content>) = clips.into_iter().unzip();
        let shared = Arc::new(Shared {
            state: Mutex::new(new_state(budget_bytes, Timeline::new(shapes), contents)),
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

    pub fn set_playhead(&self, key: FrameKey) {
        move_playhead(&mut self.state(), key);
        self.shared.wake.notify_all();
    }

    /// Show `view` of `clip` from now on.
    pub fn set_view(&self, clip: usize, view: ExrView) {
        change_view(&mut self.state(), clip, view);
        self.shared.wake.notify_all();
    }

    /// Hide the layers of `clip`'s stack that `hidden` says from now on.
    pub fn set_hidden(&self, clip: usize, hidden: u64) {
        change_hidden(&mut self.state(), clip, hidden);
        self.shared.wake.notify_all();
    }

    /// Play `content` as `clip`, `shape` long, from now on: its frames, and
    /// its failures, go; every other clip's stay. A playhead on the clip
    /// keeps its index, or moves to the new last frame when the clip got
    /// shorter. The new timeline.
    pub fn replace_clip(&self, clip: usize, shape: Clip, content: Content) -> Arc<Timeline> {
        let timeline = replace_clip(&mut self.state(), clip, shape, content);
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

    /// Which timeline positions are buffered, for the track.
    pub fn buffered_positions(&self) -> Vec<usize> {
        let state = self.state();
        let mut positions: Vec<usize> = state.buffer.keys().map(|k| state.timeline.position(k)).collect();
        positions.sort_unstable();
        positions
    }

    /// The proxies made since the last call, by clip, each of the media the
    /// clip plays now.
    pub fn take_proxies(&self) -> Vec<(usize, Proxy)> {
        let mut state = self.state();
        let made = std::mem::take(&mut state.proxies);
        made.into_iter().filter(|(clip, generation, _)| state.generations[*clip] == *generation).map(|(c, _, p)| (c, p)).collect()
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
        playhead: FrameKey { clip: 0, index: 0 },
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

/// Whether a job of `clip` is running.
fn clip_busy(state: &State, clip: usize) -> bool {
    state.running.iter().any(|job| job.clip() == clip)
}

/// EXR reads running.
fn exr_reads(state: &State) -> usize {
    state.running.iter().filter(|job| state.contents[job.clip()].is_exr()).count()
}

/// Whether the playhead's own EXR frame is being read and not in yet: the
/// other EXR reads wait for it.
fn playhead_read_alone(state: &State) -> bool {
    let playhead = state.playhead;
    state.contents[playhead.clip].is_exr() && state.running.contains(&Job::Frame(playhead)) && !state.buffer.contains(playhead)
}

/// The window's frames that still need decoding, nearest the playhead
/// first: in a clip with media, not buffered, failed or being decoded.
fn waiting_frames(state: &State) -> impl Iterator<Item = FrameKey> + '_ {
    let timeline = &state.timeline;
    let (here, len) = (timeline.position(state.playhead), timeline.len());
    let ahead = (0..=AHEAD).map(move |d| here + d).filter(move |&p| p < len);
    let behind = (1..=BEHIND).filter_map(move |d| here.checked_sub(d));
    ahead.chain(behind).map(|p| timeline.key(p)).filter(|&k| {
        state.contents[k.clip].media.is_some()
            && !state.buffer.contains(k)
            && !state.failed.contains_key(&k)
            && !state.running.contains(&Job::Frame(k))
    })
}

/// Whether a job of `clip` may start beside the running ones: one decode at
/// a time of a movie; EXR reads up to their limit, and none beside the
/// playhead's own.
fn may_start(state: &State, clip: usize) -> bool {
    if state.contents[clip].is_exr() { exr_reads(state) < EXR_READS && !playhead_read_alone(state) } else { !clip_busy(state, clip) }
}

/// Whether the playhead's own frame waits to be decoded: it is what the
/// window shows, so it goes before everything.
fn playhead_waits(state: &State) -> bool {
    !state.saturated && waiting_frames(state).next() == Some(state.playhead)
}

/// The next job: the playhead's frame while it waits; else a proxy, so a
/// take's thumbnail never waits behind a whole window of render reads from
/// the file server (one small decode per take); else the nearest window
/// frame that may start.
fn next_job(state: &State) -> Option<Job> {
    if state.timeline.len() == 0 {
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
    waiting_frames(state).find(|k| may_start(state, k.clip)).map(Job::Frame)
}

/// A proxy wanted and allowed to start, `PROXY_JOBS` at a time.
fn next_proxy(state: &State) -> Option<Job> {
    let proxies = state.running.iter().filter(|job| matches!(job, Job::Proxy(_))).count();
    if proxies >= PROXY_JOBS {
        return None;
    }
    (0..state.contents.len())
        .find(|&clip| {
            let content = &state.contents[clip];
            content.media.is_some() && content.wants_proxy && !state.running.contains(&Job::Proxy(clip)) && may_start(state, clip)
        })
        .map(Job::Proxy)
}

/// The window changed: whatever saturated the old one says nothing of it.
fn new_window(state: &mut State) {
    state.window += 1;
    state.saturated = false;
}

fn move_playhead(state: &mut State, key: FrameKey) {
    state.playhead = key;
    new_window(state);
}

/// What a clip showed is gone: its frames, failures and proxies, and any
/// running job's results.
fn forget_clip(state: &mut State, clip: usize) {
    state.generations[clip] += 1;
    state.buffer.remove_clip(clip);
    state.failed.retain(|k, _| k.clip != clip);
    new_window(state);
}

/// A new view: the clip's frames show the old one, so they go, and so do
/// its failures (a missing channel of the old view says nothing of the new).
fn change_view(state: &mut State, clip: usize, view: ExrView) {
    if state.contents[clip].view == view {
        return;
    }
    state.contents[clip].view = view;
    forget_clip(state, clip);
}

/// Other hidden layers: the clip's frames composite the old ones, so they
/// go, as for a new view.
fn change_hidden(state: &mut State, clip: usize, hidden: u64) {
    if state.contents[clip].hidden == hidden {
        return;
    }
    state.contents[clip].hidden = hidden;
    forget_clip(state, clip);
}

/// New content for `clip`, `shape` long: what applies to a new view applies
/// to every frame of it, and a playhead past its end moves to its last frame.
fn replace_clip(state: &mut State, clip: usize, shape: Clip, content: Content) -> Arc<Timeline> {
    let mut clips = state.timeline.clips.clone();
    clips[clip] = shape;
    state.timeline = Arc::new(Timeline::new(clips));
    state.contents[clip] = content;
    forget_clip(state, clip);
    if state.playhead.clip == clip {
        state.playhead.index = state.playhead.index.min(shape.frames.saturating_sub(1));
    }
    Arc::clone(&state.timeline)
}

/// Claim `job`, whose clip has media.
fn claim(state: &mut State, job: Job) -> Claim {
    state.running.insert(job);
    let content = &state.contents[job.clip()];
    Claim {
        job,
        media: Arc::clone(content.media.as_ref().expect("a job's clip has media")),
        view: content.view.clone(),
        hidden: content.hidden,
        generation: state.generations[job.clip()],
        window: state.window,
    }
}

/// Whether the job of `claim` is still wanted: its clip unchanged and, for a
/// window frame, the frame still in the window.
fn wanted(state: &State, claim: &Claim) -> bool {
    !state.quit
        && state.generations[claim.job.clip()] == claim.generation
        && match claim.job {
            Job::Frame(key) => state.timeline.in_window(key, state.playhead),
            Job::Proxy(_) => true,
        }
}

/// A frame a job of `claim` decoded, its target or passed on the way.
fn store(state: &mut State, key: FrameKey, frame: Frame, claim: &Claim) {
    if state.generations[key.clip] != claim.generation {
        return;
    }
    let (playhead, timeline) = (state.playhead, Arc::clone(&state.timeline));
    state.buffer.insert(key, frame, playhead, |a, b| timeline.eviction_distance(a, b));
}

/// What a job came to.
enum Outcome {
    Frame(Result<(), Error>),
    Proxy(Option<Proxy>),
}

/// Bookkeeping once the job of `claim` is over.
fn finish(state: &mut State, claim: &Claim, outcome: Outcome) {
    state.running.remove(&claim.job);
    let clip = claim.job.clip();
    if state.generations[clip] != claim.generation {
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
            state.contents[clip].wants_proxy = false;
            if let Some(proxy) = proxy {
                state.proxies.push((clip, claim.generation, proxy));
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
            let key = FrameKey { clip: claim.job.clip(), index };
            store(&mut shared.state.lock().unwrap(), key, frame.clone(), &claim);
        };
        let outcome = match claim.job {
            Job::Frame(key) => Outcome::Frame(decode(shared, &claim, key.index, keep, still_wanted).map(|_| ())),
            Job::Proxy(clip) => {
                let first = FrameKey { clip, index: 0 };
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
    //! Overlapping jobs, view changes and replaced clips are driven step by
    //! step; the threads themselves are driven against real media at the end.

    use super::*;
    use half::f16;
    use proptest::prelude::*;
    use th_media::{ExrSequence, MovieIndex, Pixels};

    const FRAME_BYTES: usize = 64;
    /// Jobs the fake worker may run before it must have come to rest.
    const MAX_DECODES: usize = 4 * (AHEAD + BEHIND + 1) + 16;

    fn fixture(name: &str) -> String {
        format!("{}/../media/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))
    }

    /// Media standing in for a clip's: a movie or an EXR sequence. The
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

    #[test]
    fn a_second_is_the_rate_rounded() {
        assert_eq!(second_of((24, 1)), 24);
        assert_eq!(second_of((24000, 1001)), 24);
        assert_eq!(second_of((30000, 1001)), 30);
        assert_eq!(second_of((25, 2)), 13);
        assert_eq!(second_of((1, 10)), 1, "never less than a frame");
    }

    /// A frame whose width records the view it was decoded with.
    fn tagged(view: u32) -> Frame {
        Frame { width: view, height: 1, pixels: Arc::new(Pixels::Rgba16f(vec![f16::ZERO; FRAME_BYTES / 2])) }
    }

    fn shapes(frames: &[usize]) -> Vec<Clip> {
        frames.iter().map(|&frames| Clip { frames, rate: (24, 1) }).collect()
    }

    fn timeline(frames: &[usize]) -> Timeline {
        Timeline::new(shapes(frames))
    }

    /// State over clips `frames` long, of movies unless `exr` says, every one
    /// open unless `closed` says.
    fn state_of(frames: &[usize], exr: &[bool], closed: &[bool], budget_frames: usize, at: usize) -> State {
        let contents = (0..frames.len())
            .map(|c| Content {
                media: (!closed.get(c).copied().unwrap_or(false)).then(|| media(exr.get(c).copied().unwrap_or(false))),
                view: ExrView::default(),
                hidden: 0,
                wants_proxy: false,
            })
            .collect();
        let mut state = new_state(budget_frames * FRAME_BYTES, timeline(frames), contents);
        state.playhead = state.timeline.key(at);
        state
    }

    fn state(frames: &[usize], budget_frames: usize, at: usize) -> State {
        state_of(frames, &[], &[], budget_frames, at)
    }

    /// The frames a decode of `target` runs through: its keyframe up to it.
    fn decode_run(target: FrameKey, gop: usize) -> impl Iterator<Item = FrameKey> {
        (target.index / gop * gop..=target.index).map(move |index| FrameKey { clip: target.clip, index })
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

    /// Clip lengths, empty clips included, with at least one frame overall.
    fn clips() -> impl Strategy<Value = Vec<usize>> {
        prop::collection::vec(prop_oneof![1 => Just(0usize), 4 => 1usize..200], 1..6)
            .prop_filter("an empty playlist", |c| c.iter().sum::<usize>() > 0)
    }

    proptest! {
        #[test]
        fn key_and_position_round_trip(frames in clips()) {
            let timeline = timeline(&frames);
            prop_assert_eq!(timeline.len(), frames.iter().sum::<usize>());
            for p in 0..timeline.len() {
                let key = timeline.key(p);
                prop_assert!(key.index < frames[key.clip], "position {} maps into {:?}, past its clip", p, key);
                prop_assert_eq!(timeline.position(key), p);
            }
            for (clip, &n) in frames.iter().enumerate() {
                if n > 0 {
                    prop_assert_eq!(timeline.key(timeline.clip_start(clip)), FrameKey { clip, index: 0 });
                }
            }
        }

        #[test]
        fn window_frames_outrank_the_rest(frames in clips(), here in any::<prop::sample::Index>()) {
            let timeline = timeline(&frames);
            let here = here.index(timeline.len());
            let playhead = timeline.key(here);
            let span = window(&timeline, here);
            let rank = |p: usize| timeline.eviction_distance(timeline.key(p), playhead);
            prop_assert_eq!(rank(here), 0);
            let inside = span.clone().map(rank).max().unwrap();
            if let Some(outside) = (0..timeline.len()).filter(|p| !span.contains(p)).map(rank).min() {
                prop_assert!(inside < outside, "a window frame ranks {} but one outside ranks {}", inside, outside);
            }
            for p in 0..timeline.len() {
                prop_assert_eq!(timeline.in_window(timeline.key(p), playhead), span.contains(&p));
            }
        }

        /// Whatever the budget, the worker comes to rest (no evict-and-redecode
        /// loop when the window does not fit), with the playhead's frame in.
        #[test]
        fn worker_rests_with_playhead_buffered(
            frames in clips(),
            here in any::<prop::sample::Index>(),
            budget_frames in 1usize..400,
            gop in 1usize..250,
        ) {
            let mut state = state(&frames, budget_frames, here.index(frames.iter().sum()));
            let playhead = state.playhead;
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
            prop_assert!(state.buffer.contains(playhead));
            prop_assert!(state.buffer.used_bytes() <= budget_frames * FRAME_BYTES);
        }

        /// A budget that holds the window ends with every window frame in,
        /// except the ones whose decode runs into a broken frame, which are
        /// tried once, and those of clips with nothing to decode.
        #[test]
        fn roomy_budget_fills_the_window(
            frames in clips(),
            closed in prop::collection::vec(any::<bool>(), 6),
            here in any::<prop::sample::Index>(),
            spare in 0usize..50,
            gop in 1usize..250,
            broken in prop::collection::vec(any::<prop::sample::Index>(), 0..5),
        ) {
            let len: usize = frames.iter().sum();
            let here = here.index(len);
            let span = window(&timeline(&frames), here);
            let mut state = state_of(&frames, &[], &closed, span.len() + spare, here);
            let broken: HashSet<FrameKey> = broken.iter().map(|i| state.timeline.key(i.index(len))).collect();
            prop_assert!(run(&mut state, gop, &broken).is_some(), "the worker never rested");
            let decodable = |key: FrameKey| decode_run(key, gop).all(|k| !broken.contains(&k));
            for p in span {
                let key = state.timeline.key(p);
                let open = !closed[key.clip];
                prop_assert_eq!(state.buffer.contains(key), open && decodable(key), "window frame {:?}", key);
            }
            let failures: Vec<&FrameKey> = state.failed.keys().filter(|&&k| decodable(k)).collect();
            prop_assert!(failures.is_empty(), "frames marked failed that decode fine: {:?}", failures);
            prop_assert!(state.failed.keys().all(|k| !closed[k.clip]), "a clip with nothing to decode was decoded");
        }

        /// Moving the playhead wakes a rested worker, however full the
        /// buffer, and it brings in the new playhead's frame.
        #[test]
        fn moving_the_playhead_wakes_the_worker(
            frames in clips(),
            moves in prop::collection::vec(any::<prop::sample::Index>(), 1..8),
            budget_frames in 1usize..200,
            gop in 1usize..250,
        ) {
            let mut state = state(&frames, budget_frames, 0);
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some());
            for to in moves {
                let playhead = state.timeline.key(to.index(state.timeline.len()));
                move_playhead(&mut state, playhead);
                prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
                prop_assert!(state.buffer.contains(playhead), "playhead {:?} not buffered", playhead);
            }
        }

        /// A decode claimed before the playhead moved, finishing after it
        /// with the buffer full, never stops the workers short of the new
        /// playhead's frame: whatever it brings in (or loses to eviction on
        /// arrival) belongs to the old window, so it says nothing about
        /// whether the new one fits.
        #[test]
        fn a_stale_decode_never_stalls_the_new_window(
            frames in clips(),
            from in any::<prop::sample::Index>(),
            to in any::<prop::sample::Index>(),
            stale in any::<prop::sample::Index>(),
            budget_frames in 1usize..60,
            gop in 1usize..20,
        ) {
            let len: usize = frames.iter().sum();
            let mut state = state(&frames, budget_frames, from.index(len));
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some());
            let unbuffered: Vec<FrameKey> = (0..len).map(|p| state.timeline.key(p)).filter(|&k| !state.buffer.contains(k)).collect();
            if unbuffered.is_empty() {
                return Ok(());
            }
            let target = unbuffered[stale.index(unbuffered.len())];
            let claim = claim(&mut state, Job::Frame(target));
            let playhead = state.timeline.key(to.index(len));
            move_playhead(&mut state, playhead);
            for key in decode_run(target, gop) {
                store(&mut state, key, frame(), &claim);
            }
            finish(&mut state, &claim, Outcome::Frame(Ok(())));
            prop_assert!(run(&mut state, gop, &HashSet::new()).is_some(), "the worker never rested");
            prop_assert!(state.buffer.contains(playhead), "playhead {:?} not buffered after a stale decode landed", playhead);
        }

        /// A running frame decode is wanted exactly while its clip is
        /// unchanged and its frame in the window: a seek away stops it.
        #[test]
        fn a_decode_is_wanted_while_its_frame_is_in_the_window(
            frames in clips(),
            from in any::<prop::sample::Index>(),
            to in any::<prop::sample::Index>(),
            change in any::<bool>(),
        ) {
            let len: usize = frames.iter().sum();
            let mut state = state(&frames, 1000, from.index(len));
            let Some(Job::Frame(target)) = next_job(&state) else { panic!("a fresh window has frames to decode") };
            let claim = claim(&mut state, Job::Frame(target));
            prop_assert!(wanted(&state, &claim));
            let playhead = state.timeline.key(to.index(len));
            move_playhead(&mut state, playhead);
            if change {
                change_view(&mut state, target.clip, ExrView { layer: "other".into(), channel: None });
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
    /// move the playhead, change a clip's view, replace a clip's content with
    /// one this many frames long (open or not), or ask for a clip's proxy.
    #[derive(Debug, Clone)]
    enum Op {
        Start,
        Frame(prop::sample::Index),
        Finish(prop::sample::Index),
        Cancel(prop::sample::Index),
        Seek(prop::sample::Index),
        View(usize),
        Hide(usize, u8),
        Replace(usize, usize, bool, bool),
        Proxy(usize),
    }

    fn ops() -> impl Strategy<Value = Vec<Op>> {
        let op = prop_oneof![
            4 => Just(Op::Start),
            4 => any::<prop::sample::Index>().prop_map(Op::Frame),
            2 => any::<prop::sample::Index>().prop_map(Op::Finish),
            1 => any::<prop::sample::Index>().prop_map(Op::Cancel),
            1 => any::<prop::sample::Index>().prop_map(Op::Seek),
            1 => (0usize..6).prop_map(Op::View),
            1 => (0usize..6, any::<u8>()).prop_map(|(c, h)| Op::Hide(c, h)),
            1 => (0usize..6, 0usize..200, any::<bool>(), any::<bool>()).prop_map(|(c, f, open, exr)| Op::Replace(c, f, open, exr)),
            1 => (0usize..6).prop_map(Op::Proxy),
        ];
        prop::collection::vec(op, 1..300)
    }

    proptest! {
        /// However jobs, seeks, view changes, hidden stack layers and
        /// replaced clips interleave: the buffer only ever holds frames of
        /// each clip's current view, hidden layers and content; no job runs twice, nor on a clip with nothing to decode;
        /// a movie has one job at a time; EXR reads stay within their limit
        /// and none starts beside the playhead's own; a proxy never starts
        /// while the playhead's frame waits, and stays within its limit; a
        /// window frame never starts while a wanted proxy could; a stopped
        /// job is no failure.
        #[test]
        fn jobs_keep_to_their_limits_and_never_leave_stale_frames(
            frames in clips(),
            exr in prop::collection::vec(any::<bool>(), 6),
            here in any::<prop::sample::Index>(),
            gop in 1usize..20,
            ops in ops(),
        ) {
            let len: usize = frames.iter().sum();
            let mut state = state_of(&frames, &exr, &[], usize::MAX / FRAME_BYTES / 2, here.index(len));
            // Each clip's view, as a number (its layer name).
            let mut views = vec![0u32; frames.len()];
            // Each clip's hidden layers.
            let mut hidden = vec![0u64; frames.len()];
            // Running jobs: their claim and the frames left to decode.
            let mut running: Vec<(Claim, Vec<FrameKey>)> = Vec::new();
            let tag = |claim: &Claim| tagged(claim.view.layer.parse::<u32>().unwrap_or(0) * 256 + claim.hidden as u32);
            for op in ops {
                match op {
                    Op::Start => {
                        let alone = playhead_read_alone(&state);
                        let playhead_first = playhead_waits(&state) && may_start(&state, state.playhead.clip);
                        let proxy_ready = !playhead_waits(&state) && next_proxy(&state).is_some();
                        if let Some(job) = next_job(&state) {
                            let clip = job.clip();
                            prop_assert!(running.iter().all(|r| r.0.job != job), "{:?} claimed twice", job);
                            prop_assert!(state.contents[clip].media.is_some(), "{:?} of a clip with nothing to decode", job);
                            if state.contents[clip].is_exr() {
                                prop_assert!(!alone, "{:?} started beside the playhead's read", job);
                                prop_assert!(exr_reads(&state) < EXR_READS);
                            } else {
                                prop_assert!(!clip_busy(&state, clip), "a second job on movie clip {}", clip);
                            }
                            match job {
                                Job::Proxy(_) => prop_assert!(!playhead_first, "a proxy started while the playhead's frame waits"),
                                Job::Frame(key) => {
                                    prop_assert!(!proxy_ready, "{:?} started while a proxy waits", key);
                                    if playhead_first {
                                        prop_assert_eq!(key, state.playhead, "the playhead's frame goes first");
                                    }
                                }
                            }
                            let claim = claim(&mut state, job);
                            let rest = match job {
                                Job::Frame(target) => decode_run(target, gop).collect(),
                                Job::Proxy(clip) => vec![FrameKey { clip, index: 0 }],
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
                    Op::Seek(to) if state.timeline.len() > 0 => {
                        let key = state.timeline.key(to.index(state.timeline.len()));
                        move_playhead(&mut state, key);
                    }
                    Op::View(clip) if clip < frames.len() => {
                        views[clip] += 1;
                        change_view(&mut state, clip, ExrView { layer: views[clip].to_string(), channel: None });
                    }
                    Op::Hide(clip, mask) if clip < frames.len() => {
                        let others: HashSet<FrameKey> = state.buffer.keys().filter(|k| k.clip != clip).collect();
                        let changed = hidden[clip] != u64::from(mask);
                        hidden[clip] = u64::from(mask);
                        change_hidden(&mut state, clip, hidden[clip]);
                        if changed {
                            let kept: HashSet<FrameKey> = state.buffer.keys().collect();
                            prop_assert_eq!(kept, others, "hiding layers of clip {} kept its frames or touched another's", clip);
                        }
                    }
                    Op::Replace(clip, n, open, is_exr) if clip < frames.len() => {
                        hidden[clip] = 0;
                        let others: HashSet<FrameKey> = state.buffer.keys().filter(|k| k.clip != clip).collect();
                        // New content decodes as a new view would: tag it so.
                        views[clip] += 1;
                        let content = Content {
                            media: open.then(|| media(is_exr)),
                            view: ExrView { layer: views[clip].to_string(), channel: None },
                            hidden: 0,
                            wants_proxy: open,
                        };
                        replace_clip(&mut state, clip, Clip { frames: n, rate: (24, 1) }, content);
                        let kept: HashSet<FrameKey> = state.buffer.keys().collect();
                        prop_assert_eq!(kept, others, "replacing clip {} touched another clip's frames", clip);
                        if state.playhead.clip == clip {
                            prop_assert!(state.playhead.index < n.max(1), "the playhead is past the new clip's end");
                        }
                    }
                    Op::Proxy(clip) if clip < frames.len() => state.contents[clip].wants_proxy = true,
                    _ => {}
                }
                let running_jobs: HashSet<Job> = running.iter().map(|r| r.0.job).collect();
                prop_assert_eq!(&state.running, &running_jobs);
                prop_assert!(running.iter().filter(|r| matches!(r.0.job, Job::Proxy(_))).count() <= PROXY_JOBS);
                for key in state.buffer.keys() {
                    let shown = state.buffer.get(key).unwrap().width;
                    prop_assert_eq!(shown, views[key.clip] * 256 + hidden[key.clip] as u32, "{:?} holds a frame of an old view", key);
                }
            }
        }
    }

    /// The real thing, threads and all: the playhead's frame of a movie and
    /// of an EXR sequence lands, every open clip gets its proxy once, a clip
    /// with nothing to decode is left alone, and dropping the engine stops
    /// and joins its workers.
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
        let open = |media: &Arc<Media>| {
            let shape = Clip { frames: media.frame_count(), rate: media.rate() };
            (shape, Content { media: Some(Arc::clone(media)), view: media.default_view(), hidden: 0, wants_proxy: true })
        };
        let closed = (Clip { frames: 10, rate: (24, 1) }, Content::none());
        let engine = Engine::start(vec![open(&movie), closed, open(&sequence)], 1 << 30);
        let (woke, woken) = std::sync::mpsc::channel();
        engine.set_notifier(move || {
            let _ = woke.send(());
        });
        let mut proxies = Vec::new();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        for playhead in [FrameKey { clip: 0, index: 30 }, FrameKey { clip: 2, index: 2 }] {
            engine.set_playhead(playhead);
            while !engine.is_buffered(playhead) || proxies.len() < 2 {
                assert!(std::time::Instant::now() < deadline, "playhead {playhead:?} or the proxies never came ({proxies:?})");
                let _ = woken.recv_timeout(std::time::Duration::from_millis(200));
                proxies.extend(engine.take_proxies().into_iter().map(|(clip, _)| clip));
            }
        }
        proxies.sort_unstable();
        assert_eq!(proxies, [0, 2], "each open clip's proxy, once");
        assert!(engine.failure(FrameKey { clip: 2, index: 2 }).is_none());
        assert!((0..10).all(|index| !engine.is_buffered(FrameKey { clip: 1, index })), "the closed clip was decoded");
        drop(engine);
        std::fs::remove_file(exr_path).ok();
    }
}
