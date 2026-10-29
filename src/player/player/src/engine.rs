//! The decode engine: worker threads that share the clips' sources and fill
//! the shared frame buffer around the playhead.
//!
//! The UI only ever reads the buffer. It tells the engine where the playhead
//! is; a worker decodes the nearest frame not yet buffered or being decoded
//! (the playhead itself first, then ahead, then behind), and every frame a
//! decode passes on the way goes into the buffer too. When the window around
//! the playhead is full, or holds all the budget allows, the workers sleep
//! until the playhead moves. Eviction ranks frames by the same lopsided
//! window (see `Timeline::eviction_distance`), so a full buffer drops what
//! the workers would decode last.
//!
//! One worker decodes the movies: a movie decodes forward from a keyframe on
//! its one decoder. The others decode the EXR frames, which are independent
//! files and slow to read from the NAS, so reading several at once is what
//! makes them play. Neither takes the other's frames, so a movie never waits
//! behind an EXR read. While the playhead's own EXR frame is being read, no
//! other EXR read starts: reads side by side share the file server, and the
//! frame on screen should not wait for the ones after it.
//!
//! An EXR clip shows one view (layer, channel) at a time. Changing it drops
//! the clip's frames, and bumps the clip's generation so a decode of the old
//! view that is still running cannot put its frame back; an EXR read of the
//! old view stops at its next piece. Replacing a clip's source (another take
//! of the shot) works the same way, and leaves every other clip's frames
//! where they are.

use std::collections::HashSet;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;

use th_media::{ExrLayer, ExrView, Frame, FrameBuffer, FrameKey, Source};

/// How far ahead of the playhead the workers decode before they rest.
const AHEAD: usize = 96;
/// How far behind the playhead they fill in.
const BEHIND: usize = 48;
/// Workers for EXR frames (besides the one for movies).
const EXR_WORKERS: usize = 6;

/// What the timeline knows about a clip.
#[derive(Debug, Clone)]
pub struct Clip {
    pub frames: usize,
    pub rate: (i32, i32),
    /// The EXR layers it can show; empty for a movie.
    pub layers: Vec<ExrLayer>,
    /// Its frames decode independently (EXR), so any worker may take them.
    pub parallel: bool,
}

impl Clip {
    fn of(source: &Source) -> Self {
        Self {
            frames: source.frame_count(),
            rate: source.rate(),
            layers: source.layers().to_vec(),
            parallel: source.is_parallel(),
        }
    }

    /// Frames in a second of it: its rate, rounded (24 at 23.976), at least 1.
    pub fn second(&self) -> usize {
        second_of(self.rate)
    }
}

/// Frames in a second at `rate`, rounded, at least 1.
fn second_of((num, den): (i32, i32)) -> usize {
    if den <= 0 || num <= 0 {
        return 1;
    }
    ((f64::from(num) / f64::from(den)).round() as usize).max(1)
}

/// What the workers decode: the timeline and each clip's source. Replaced
/// whole when a clip is, under the same lock as the `State` it goes with.
struct Clips {
    timeline: Arc<Timeline>,
    sources: Vec<Arc<Source>>,
}

struct Locked {
    state: State,
    clips: Clips,
}

struct State {
    buffer: FrameBuffer,
    playhead: FrameKey,
    /// Each clip's view, and how many times it changed.
    views: Vec<ExrView>,
    generations: Vec<u64>,
    /// Targets a worker is decoding right now.
    in_flight: HashSet<FrameKey>,
    failed: HashSet<FrameKey>,
    errors: Vec<String>,
    /// The last target was evicted as soon as it landed: the budget holds
    /// less than the window, and everything nearer is already in. Decoding
    /// on would only evict and re-decode the same frame, so rest until the
    /// playhead moves.
    saturated: bool,
    quit: bool,
}

struct Shared {
    locked: Mutex<Locked>,
    wake: Condvar,
    notify: Mutex<Option<Box<dyn Fn() + Send>>>,
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

    /// How far `key` is from `playhead` in the workers' terms, for eviction:
    /// a frame behind counts as AHEAD/BEHIND times as far as the same gap
    /// ahead, so the frames the workers fill (their window) always outrank
    /// the ones they only passed on the way, and playback's frames go last.
    fn eviction_distance(&self, key: FrameKey, playhead: FrameKey) -> usize {
        let (at, here) = (self.position(key), self.position(playhead));
        if at >= here { at - here } else { (here - at) * AHEAD / BEHIND }
    }
}

pub struct Engine {
    shared: Arc<Shared>,
    /// The workers' timeline, for the UI to read without the lock.
    timeline: Arc<Timeline>,
    /// Whether the EXR-only workers have started (with the first EXR clip).
    exr_workers: bool,
}

impl std::fmt::Debug for Engine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Engine").field("timeline", &self.timeline).finish_non_exhaustive()
    }
}

impl Engine {
    /// Start the workers on `sources` showing `views`, buffering up to
    /// `budget_bytes`.
    pub fn start(sources: Vec<Arc<Source>>, views: Vec<ExrView>, budget_bytes: usize) -> Self {
        let timeline = Arc::new(Timeline::new(sources.iter().map(|s| Clip::of(s)).collect()));
        let shared = Arc::new(Shared {
            locked: Mutex::new(Locked {
                state: new_state(budget_bytes, views),
                clips: Clips { timeline: Arc::clone(&timeline), sources },
            }),
            wake: Condvar::new(),
            notify: Mutex::new(None),
        });
        let mut engine = Self { shared, timeline, exr_workers: false };
        engine.spawn(0, false);
        engine.start_exr_workers();
        engine
    }

    /// Start a worker for EXR frames (`exr`) or for movie frames.
    fn spawn(&self, worker: usize, exr: bool) {
        let shared = Arc::clone(&self.shared);
        thread::Builder::new()
            .name(format!("th_player decode {worker}"))
            .spawn(move || work(&shared, exr))
            .expect("spawning a decode thread");
    }

    /// Start the EXR-only workers once there is an EXR clip to read.
    fn start_exr_workers(&mut self) {
        if self.exr_workers || !self.timeline.clips.iter().any(|c| c.parallel) {
            return;
        }
        self.exr_workers = true;
        for worker in 1..=EXR_WORKERS {
            self.spawn(worker, true);
        }
    }

    pub fn timeline(&self) -> &Timeline {
        &self.timeline
    }

    /// Called (from a worker thread) whenever a frame lands in the buffer.
    pub fn set_notifier(&self, notify: impl Fn() + Send + 'static) {
        *self.shared.notify.lock().unwrap() = Some(Box::new(notify));
    }

    pub fn set_playhead(&self, key: FrameKey) {
        move_playhead(&mut self.shared.locked.lock().unwrap().state, key);
        self.shared.wake.notify_all();
    }

    /// Show `view` of `clip` from now on.
    pub fn set_view(&self, clip: usize, view: ExrView) {
        change_view(&mut self.shared.locked.lock().unwrap().state, clip, view);
        self.shared.wake.notify_all();
    }

    /// Play `source` showing `view` as `clip` from now on: its frames, and
    /// its failures, go; every other clip's stay. A playhead on the clip keeps
    /// its index, or moves to the new last frame when the clip got shorter.
    pub fn replace_clip(&mut self, clip: usize, source: Arc<Source>, view: ExrView) {
        let mut clips = self.timeline.clips.clone();
        clips[clip] = Clip::of(&source);
        let frames = clips[clip].frames;
        self.timeline = Arc::new(Timeline::new(clips));
        {
            let mut locked = self.shared.locked.lock().unwrap();
            replace_clip(&mut locked.state, clip, view, frames);
            locked.clips.sources[clip] = source;
            locked.clips.timeline = Arc::clone(&self.timeline);
        }
        self.start_exr_workers();
        self.shared.wake.notify_all();
    }

    pub fn frame(&self, key: FrameKey) -> Option<Frame> {
        self.shared.locked.lock().unwrap().state.buffer.get(key).cloned()
    }

    pub fn is_buffered(&self, key: FrameKey) -> bool {
        self.shared.locked.lock().unwrap().state.buffer.contains(key)
    }

    /// Which timeline positions are buffered, for the timeline bar.
    pub fn buffered_positions(&self) -> Vec<usize> {
        let locked = self.shared.locked.lock().unwrap();
        let mut positions: Vec<usize> = locked.state.buffer.keys().map(|k| self.timeline.position(k)).collect();
        positions.sort_unstable();
        positions
    }

    /// (frames buffered, bytes they hold, decode errors so far)
    pub fn stats(&self) -> (usize, usize, Vec<String>) {
        let state = &self.shared.locked.lock().unwrap().state;
        (state.buffer.len(), state.buffer.used_bytes(), state.errors.clone())
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.shared.locked.lock().unwrap().state.quit = true;
        self.shared.wake.notify_all();
    }
}

fn new_state(budget_bytes: usize, views: Vec<ExrView>) -> State {
    State {
        buffer: FrameBuffer::new(budget_bytes),
        playhead: FrameKey { clip: 0, index: 0 },
        generations: vec![0; views.len()],
        views,
        in_flight: HashSet::new(),
        failed: HashSet::new(),
        errors: Vec::new(),
        saturated: false,
        quit: false,
    }
}

/// Whether the playhead's own EXR frame is being read and not in yet: the
/// other EXR workers wait for it.
fn playhead_read_alone(state: &State, timeline: &Timeline) -> bool {
    let playhead = state.playhead;
    timeline.clips[playhead.clip].parallel && state.in_flight.contains(&playhead) && !state.buffer.contains(playhead)
}

/// The nearest frame to the playhead that still needs decoding, of the kind
/// this worker takes (`exr`: EXR frames, else movie frames).
fn next_target(state: &State, timeline: &Timeline, exr: bool) -> Option<FrameKey> {
    if state.saturated || timeline.len() == 0 || (exr && playhead_read_alone(state, timeline)) {
        return None;
    }
    let here = timeline.position(state.playhead);
    let len = timeline.len();
    let ahead = (0..=AHEAD).map(|d| here + d).filter(|&p| p < len);
    let behind = (1..=BEHIND).filter_map(|d| here.checked_sub(d));
    ahead.chain(behind).map(|p| timeline.key(p)).find(|&k| {
        timeline.clips[k.clip].parallel == exr
            && !state.buffer.contains(k)
            && !state.failed.contains(&k)
            && !state.in_flight.contains(&k)
    })
}

/// A new playhead is a new window: a saturated worker has work again.
fn move_playhead(state: &mut State, key: FrameKey) {
    state.playhead = key;
    state.saturated = false;
}

/// A new view: the clip's frames show the old one, so they go, and so do
/// its failures (a missing channel of the old view says nothing of the new).
fn change_view(state: &mut State, clip: usize, view: ExrView) {
    if state.views[clip] == view {
        return;
    }
    state.views[clip] = view;
    state.generations[clip] += 1;
    state.buffer.remove_clip(clip);
    state.failed.retain(|k| k.clip != clip);
    state.saturated = false;
}

/// A new source for `clip`, `frames` long, shown in `view`: what applies to a
/// new view applies to every frame of it, and a playhead past its end moves
/// to its last frame.
fn replace_clip(state: &mut State, clip: usize, view: ExrView, frames: usize) {
    state.views[clip] = view;
    state.generations[clip] += 1;
    state.buffer.remove_clip(clip);
    state.failed.retain(|k| k.clip != clip);
    state.saturated = false;
    if state.playhead.clip == clip {
        state.playhead.index = state.playhead.index.min(frames.saturating_sub(1));
    }
}

/// Claim `target` for a decode: the view to decode it with and the clip's
/// generation, which `store` and `finish` check.
fn claim(state: &mut State, target: FrameKey) -> (ExrView, u64) {
    state.in_flight.insert(target);
    (state.views[target.clip].clone(), state.generations[target.clip])
}

/// A frame a decode of `generation` produced, target or passed on the way.
/// A frame of a view that has changed since is dropped.
fn store(state: &mut State, timeline: &Timeline, key: FrameKey, frame: Frame, generation: u64) {
    if state.generations[key.clip] != generation {
        return;
    }
    let playhead = state.playhead;
    state.buffer.insert(key, frame, playhead, |a, b| timeline.eviction_distance(a, b));
}

/// Bookkeeping once the decode of `target` is over.
fn finish(state: &mut State, target: FrameKey, generation: u64, error: Option<String>) {
    state.in_flight.remove(&target);
    if state.generations[target.clip] != generation {
        return;
    }
    match error {
        Some(error) => {
            state.failed.insert(target);
            state.errors.push(error);
        }
        None if !state.buffer.contains(target) => state.saturated = true,
        None => {}
    }
}

fn work(shared: &Shared, exr: bool) {
    loop {
        let (target, view, generation, source) = {
            let mut locked = shared.locked.lock().unwrap();
            loop {
                if locked.state.quit {
                    return;
                }
                let timeline = Arc::clone(&locked.clips.timeline);
                if let Some(target) = next_target(&locked.state, &timeline, exr) {
                    let (view, generation) = claim(&mut locked.state, target);
                    // The source the generation belongs to: a replacement
                    // after this cannot change what this decode reads.
                    let source = Arc::clone(&locked.clips.sources[target.clip]);
                    break (target, view, generation, source);
                }
                locked = shared.wake.wait(locked).unwrap();
            }
        };
        // A read for a view or source since replaced (or a stopped engine)
        // stops at its next piece, leaving the file server to what is wanted.
        let wanted = || {
            let state = &shared.locked.lock().unwrap().state;
            !state.quit && state.generations[target.clip] == generation
        };
        let keep = |index, frame: &Frame| {
            let key = FrameKey { clip: target.clip, index };
            let mut locked = shared.locked.lock().unwrap();
            let timeline = Arc::clone(&locked.clips.timeline);
            store(&mut locked.state, &timeline, key, frame.clone(), generation);
        };
        let result = source.decode_while(target.index, &view, keep, wanted);
        finish(&mut shared.locked.lock().unwrap().state, target, generation, result.err().map(|e| e.to_string()));
        // Its target is no longer in flight: a waiting worker may want it.
        shared.wake.notify_all();
        if let Some(notify) = &*shared.notify.lock().unwrap() {
            notify();
        }
    }
}

#[cfg(test)]
mod tests {
    //! Properties of the timeline arithmetic and of the worker's scheduling,
    //! the latter driven against a fake source that decodes like
    //! `MovieSource`: from the keyframe at or before the target up to it,
    //! keeping every frame it passes. Overlapping decodes (several workers)
    //! and view changes mid-decode are driven step by step.

    use super::*;
    use half::f16;
    use proptest::prelude::*;
    use th_media::Pixels;

    const FRAME_BYTES: usize = 64;
    /// Decodes the fake worker may run before it must have come to rest.
    const MAX_DECODES: usize = 4 * (AHEAD + BEHIND + 1);

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
        assert_eq!(second_of((24, 0)), 1, "a broken rate still moves");
    }

    /// A frame whose width records the view it was decoded with.
    fn tagged(view: u32) -> Frame {
        Frame { width: view, height: 1, pixels: Arc::new(Pixels::Half { data: vec![f16::ZERO; FRAME_BYTES / 2], channels: 1 }) }
    }

    fn timeline(frames: &[usize]) -> Timeline {
        Timeline::new(
            frames
                .iter()
                .map(|&frames| Clip { frames, rate: (24, 1), layers: Vec::new(), parallel: false })
                .collect(),
        )
    }

    fn state(budget_frames: usize, playhead: FrameKey) -> State {
        // Enough views for any generated playlist.
        let mut state = new_state(budget_frames * FRAME_BYTES, vec![ExrView::default(); 8]);
        state.playhead = playhead;
        state
    }

    /// The frames a decode of `target` runs through: its keyframe up to it.
    fn decode_run(target: FrameKey, gop: usize) -> impl Iterator<Item = FrameKey> {
        (target.index / gop * gop..=target.index).map(move |index| FrameKey { clip: target.clip, index })
    }

    /// Run the movie worker's loop until it rests; the number of decodes it
    /// took, or None if it was still decoding after `MAX_DECODES`. A decode
    /// that runs into a broken frame fails there, as `MovieSource::decode`
    /// does.
    fn run(state: &mut State, timeline: &Timeline, gop: usize, broken: &HashSet<FrameKey>) -> Option<usize> {
        for decodes in 0..MAX_DECODES {
            let Some(target) = next_target(state, timeline, false) else { return Some(decodes) };
            let (_, generation) = claim(state, target);
            let mut error = None;
            for key in decode_run(target, gop) {
                if broken.contains(&key) {
                    error = Some(format!("broken frame {key:?}"));
                    break;
                }
                store(state, timeline, key, frame(), generation);
            }
            finish(state, target, generation, error);
        }
        None
    }

    /// The timeline positions the worker fills around `here`.
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
            let timeline = timeline(&frames);
            let playhead = timeline.key(here.index(timeline.len()));
            let mut state = state(budget_frames, playhead);
            prop_assert!(run(&mut state, &timeline, gop, &HashSet::new()).is_some(), "the worker never rested");
            prop_assert!(state.buffer.contains(playhead));
            prop_assert!(state.buffer.used_bytes() <= budget_frames * FRAME_BYTES);
        }

        /// A budget that holds the window ends with every window frame in,
        /// except the ones whose decode runs into a broken frame, which are
        /// tried once.
        #[test]
        fn roomy_budget_fills_the_window(
            frames in clips(),
            here in any::<prop::sample::Index>(),
            spare in 0usize..50,
            gop in 1usize..250,
            broken in prop::collection::vec(any::<prop::sample::Index>(), 0..5),
        ) {
            let timeline = timeline(&frames);
            let here = here.index(timeline.len());
            let span = window(&timeline, here);
            let broken: HashSet<FrameKey> = broken.iter().map(|i| timeline.key(i.index(timeline.len()))).collect();
            let mut state = state(span.len() + spare, timeline.key(here));
            prop_assert!(run(&mut state, &timeline, gop, &broken).is_some(), "the worker never rested");
            let decodable = |key: FrameKey| decode_run(key, gop).all(|k| !broken.contains(&k));
            for p in span {
                let key = timeline.key(p);
                prop_assert_eq!(state.buffer.contains(key), decodable(key), "window frame {:?}", key);
            }
            let failures: Vec<&FrameKey> = state.failed.iter().filter(|&&k| decodable(k)).collect();
            prop_assert!(failures.is_empty(), "frames marked failed that decode fine: {:?}", failures);
            prop_assert_eq!(state.errors.len(), state.failed.len(), "a broken frame was retried");
        }

        /// Moving the playhead (as `Engine::set_playhead` does) wakes a
        /// rested worker, however full the buffer, and it brings in the new
        /// playhead's frame.
        #[test]
        fn moving_the_playhead_wakes_the_worker(
            frames in clips(),
            moves in prop::collection::vec(any::<prop::sample::Index>(), 1..8),
            budget_frames in 1usize..200,
            gop in 1usize..250,
        ) {
            let timeline = timeline(&frames);
            let mut state = state(budget_frames, timeline.key(0));
            prop_assert!(run(&mut state, &timeline, gop, &HashSet::new()).is_some());
            for to in moves {
                let playhead = timeline.key(to.index(timeline.len()));
                move_playhead(&mut state, playhead);
                prop_assert!(run(&mut state, &timeline, gop, &HashSet::new()).is_some(), "the worker never rested");
                prop_assert!(state.buffer.contains(playhead), "playhead {:?} not buffered", playhead);
            }
        }
    }

    /// One step of several workers sharing the state: start a decode, hand
    /// over the next frame of a running one, end one, stop one no longer
    /// wanted (as an EXR read of a replaced view does), change a clip's view,
    /// or replace a clip's source with one this many frames long.
    #[derive(Debug, Clone)]
    enum Op {
        Start,
        Frame(prop::sample::Index),
        Finish(prop::sample::Index),
        Cancel(prop::sample::Index),
        View(usize),
        Replace(usize, usize),
    }

    fn ops() -> impl Strategy<Value = Vec<Op>> {
        let op = prop_oneof![
            3 => Just(Op::Start),
            4 => any::<prop::sample::Index>().prop_map(Op::Frame),
            2 => any::<prop::sample::Index>().prop_map(Op::Finish),
            1 => any::<prop::sample::Index>().prop_map(Op::Cancel),
            1 => (0usize..6).prop_map(Op::View),
            1 => (0usize..6, 0usize..200).prop_map(|(clip, frames)| Op::Replace(clip, frames)),
        ];
        prop::collection::vec(op, 1..300)
    }

    proptest! {
        /// However decodes, view changes and replaced clips interleave, no two
        /// decodes run on one target, the buffer only ever holds frames of
        /// each clip's current view and source, and a decode stopped because
        /// its view or source went is no failure. Movie frames go to the movie
        /// worker only, EXR frames to the EXR workers only, and no EXR read
        /// starts beside the playhead's own.
        #[test]
        fn view_changes_and_replacements_never_leave_stale_frames(
            frames in clips(),
            parallel in prop::collection::vec(any::<bool>(), 6),
            here in any::<prop::sample::Index>(),
            gop in 1usize..20,
            ops in ops(),
        ) {
            let mut timeline = timeline(&frames);
            for (clip, p) in timeline.clips.iter_mut().zip(&parallel) {
                clip.parallel = *p;
            }
            let mut state = state(usize::MAX / FRAME_BYTES / 2, timeline.key(here.index(timeline.len())));
            // Each clip's view, as a number (its layer name).
            let mut views = vec![0u32; timeline.clips.len()];
            // Running decodes: target, generation, the view it decodes, frames left.
            let mut running: Vec<(FrameKey, u64, u32, Vec<FrameKey>)> = Vec::new();
            for op in ops {
                match op {
                    Op::Start => {
                        // The movie worker is busy whenever a movie decode
                        // runs; an EXR worker is always free.
                        let movie_free = !running.iter().any(|r| !timeline.clips[r.0.clip].parallel);
                        let alone = playhead_read_alone(&state, &timeline);
                        let picked = movie_free
                            .then(|| next_target(&state, &timeline, false).map(|t| (t, false)))
                            .flatten()
                            .or_else(|| next_target(&state, &timeline, true).map(|t| (t, true)));
                        if let Some((target, exr)) = picked {
                            prop_assert_eq!(timeline.clips[target.clip].parallel, exr, "{:?} went to the wrong worker", target);
                            prop_assert!(!(exr && alone), "{:?} started beside the playhead's read", target);
                            prop_assert!(running.iter().all(|r| r.0 != target), "{:?} claimed twice", target);
                            let (view, generation) = claim(&mut state, target);
                            let view: u32 = view.layer.parse().unwrap_or(0);
                            running.push((target, generation, view, decode_run(target, gop).collect()));
                        }
                    }
                    Op::Frame(i) if !running.is_empty() => {
                        let at = i.index(running.len());
                        let r = &mut running[at];
                        if !r.3.is_empty() {
                            let key = r.3.remove(0);
                            store(&mut state, &timeline, key, tagged(r.2), r.1);
                        }
                    }
                    Op::Finish(i) if !running.is_empty() => {
                        let at = i.index(running.len());
                        let (target, generation, view, rest) = running.remove(at);
                        for key in rest {
                            store(&mut state, &timeline, key, tagged(view), generation);
                        }
                        finish(&mut state, target, generation, None);
                    }
                    Op::Cancel(i) if !running.is_empty() => {
                        let at = i.index(running.len());
                        let (target, generation, ..) = running[at];
                        // Only a decode whose view or source went is stopped.
                        if state.generations[target.clip] != generation {
                            running.remove(at);
                            let errors = state.errors.len();
                            finish(&mut state, target, generation, Some("no longer wanted".into()));
                            prop_assert!(!state.failed.contains(&target), "a stopped decode marked {:?} failed", target);
                            prop_assert_eq!(state.errors.len(), errors, "a stopped decode reported an error");
                        }
                    }
                    Op::View(clip) if clip < timeline.clips.len() => {
                        views[clip] += 1;
                        change_view(&mut state, clip, ExrView { layer: views[clip].to_string(), channel: None });
                    }
                    Op::Replace(clip, frames) if clip < timeline.clips.len() => {
                        let others: HashSet<FrameKey> = state.buffer.keys().filter(|k| k.clip != clip).collect();
                        // A new source decodes as a new view would: tag it so.
                        views[clip] += 1;
                        let mut clips = timeline.clips.clone();
                        clips[clip].frames = frames;
                        timeline = Timeline::new(clips);
                        replace_clip(&mut state, clip, ExrView { layer: views[clip].to_string(), channel: None }, frames);
                        let kept: HashSet<FrameKey> = state.buffer.keys().collect();
                        prop_assert_eq!(kept, others, "replacing clip {} touched another clip's frames", clip);
                        if state.playhead.clip == clip {
                            prop_assert!(state.playhead.index < frames.max(1), "the playhead is past the new clip's end");
                        }
                    }
                    _ => {}
                }
                let running_targets: HashSet<FrameKey> = running.iter().map(|r| r.0).collect();
                prop_assert_eq!(&state.in_flight, &running_targets);
                for key in state.buffer.keys() {
                    let shown = state.buffer.get(key).unwrap().width;
                    prop_assert_eq!(shown, views[key.clip], "{:?} holds a frame of an old view", key);
                }
            }
        }
    }
}
