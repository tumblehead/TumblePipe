//! An audio clip's waveform on the timeline (2026-11-22 RFE: "the audio
//! tracks don't read as audio tracks without seeing the waveform").
//!
//! [`Peaks`] is a sound's loudest and quietest sample per bucket, at a few
//! sizes of bucket (levels): the finest [`BASE`] sample frames, each next
//! [`FACTOR`] times coarser. It is made once per sound, off the UI thread,
//! where the sound itself is read (`editor::entries::resolve`, beside the
//! decoded `Sound` the speaker plays), so a clip of it on any track and at
//! any zoom draws from the same peaks. [`Peaks::range`] answers a span of
//! seconds from the coarsest level that still has a bucket in it, so a
//! pixel column costs a few buckets whatever the zoom.
//!
//! [`Wave`] is what a clip's row draws: nothing yet (the sound is being
//! read: a placeholder line), or the peaks placed under the clip through
//! its in point, gain and fades; [`bars`] makes the column bars for the
//! pixels of the clip on screen, and only those.

use std::sync::Arc;

use primer::prelude::Rectangle;
use th_edit::document::Frame as EditFrame;
use th_media::sound::Sound;

/// The finest level's bucket, in sample frames (2.7 ms at 48 kHz).
pub const BASE: usize = 128;
/// Each level's buckets are this many of the level below.
pub const FACTOR: usize = 8;

/// One level: per bucket of `bucket` sample frames, its lowest and
/// highest sample (either channel).
#[derive(Debug, Clone, PartialEq)]
struct Level {
    bucket: usize,
    low: Vec<f32>,
    high: Vec<f32>,
}

/// A sound's peaks at every level, finest first.
#[derive(Debug, Clone, PartialEq)]
pub struct Peaks {
    rate: u32,
    frames: usize,
    levels: Vec<Level>,
}

impl Peaks {
    /// The peaks of `sound`.
    pub fn of(sound: &Sound) -> Self {
        let frames = sound.frames();
        let buckets = frames.div_ceil(BASE);
        let (mut low, mut high) = (Vec::with_capacity(buckets), Vec::with_capacity(buckets));
        for chunk in sound.samples[..frames * 2].chunks(BASE * 2) {
            let (lo, hi) = chunk.iter().fold((f32::INFINITY, f32::NEG_INFINITY), |(lo, hi), &s| (lo.min(s), hi.max(s)));
            low.push(lo);
            high.push(hi);
        }
        let mut levels = vec![Level { bucket: BASE, low, high }];
        while levels.last().is_some_and(|l| l.low.len() > 1) {
            let below = levels.last().expect("a level");
            let fold = |values: &[f32], pick: fn(f32, f32) -> f32| {
                values.chunks(FACTOR).map(|c| c.iter().copied().reduce(pick).expect("a chunk")).collect()
            };
            let level = Level { bucket: below.bucket * FACTOR, low: fold(&below.low, f32::min), high: fold(&below.high, f32::max) };
            levels.push(level);
        }
        Peaks { rate: sound.rate.max(1), frames, levels }
    }

    /// Its length in seconds.
    #[cfg(test)]
    fn seconds(&self) -> f64 {
        self.frames as f64 / f64::from(self.rate)
    }

    /// The level a span of `frames` sample frames reads from: the coarsest
    /// whose bucket is no longer than the span (the finest for a span
    /// shorter than a bucket).
    fn level_for(&self, frames: usize) -> &Level {
        self.levels.iter().rev().find(|l| l.bucket <= frames).unwrap_or(&self.levels[0])
    }

    /// The lowest and highest sample from second `from` to second `to`:
    /// every sample in it counted, and none further out than one bucket of
    /// the level read (a bucket no longer than the span). None where the
    /// span has no sample of the sound (before it, after it, or empty).
    pub fn range(&self, from: f64, to: f64) -> Option<(f32, f32)> {
        if !(from.is_finite() && to.is_finite()) {
            return None;
        }
        let rate = f64::from(self.rate);
        let a = (from * rate).floor().max(0.0);
        let b = (to * rate).ceil().min(self.frames as f64);
        if b <= a {
            return None;
        }
        let (a, b) = (a as usize, b as usize);
        let level = self.level_for(b - a);
        let (i0, i1) = (a / level.bucket, b.div_ceil(level.bucket).min(level.low.len()));
        let low = level.low[i0..i1].iter().copied().fold(f32::INFINITY, f32::min);
        let high = level.high[i0..i1].iter().copied().fold(f32::NEG_INFINITY, f32::max);
        Some((low, high))
    }
}

/// What a clip's row draws of its sound.
#[derive(Debug, Clone)]
pub enum Wave {
    /// Its sound is still being read: a placeholder.
    Pending,
    Ready(Placed),
}

/// A sound's peaks placed under a clip, as `place_sounds` places its sound
/// for the speaker.
#[derive(Debug, Clone)]
pub struct Placed {
    pub peaks: Arc<Peaks>,
    /// The clip's first edit frame and its length in frames.
    pub at: EditFrame,
    pub duration: EditFrame,
    /// The second of the sound at the clip's first frame (negative: the
    /// sound starts later in the clip).
    pub offset: f64,
    pub fps: f64,
    /// Its linear gain.
    pub gain: f32,
    /// Frames it fades in and out over.
    pub fade_in: EditFrame,
    pub fade_out: EditFrame,
}

impl Placed {
    /// How loud the clip plays at fractional edit frame `frame`: its gain
    /// through its fades, as `Voice::level` hears it.
    fn level(&self, frame: f64) -> f32 {
        let t = frame - self.at as f64;
        if t < 0.0 || t > self.duration as f64 {
            return 0.0;
        }
        let mut envelope = 1.0f64;
        if self.fade_in > 0 {
            envelope = envelope.min(t / self.fade_in as f64);
        }
        if self.fade_out > 0 {
            envelope = envelope.min((self.duration as f64 - t) / self.fade_out as f64);
        }
        self.gain * envelope.clamp(0.0, 1.0) as f32
    }
}

/// The bars of `wave` over the clip drawn at `rect` (its part on screen),
/// one per pixel column, `frame_at` naming the edit frame at a local x:
/// each bar from its column's highest sample to its lowest, through the
/// clip's gain and fades, about the rect's middle and never outside it.
/// Columns with no sound under them (before or after it, or silent) have
/// no bar.
pub fn bars(wave: &Placed, rect: Rectangle, frame_at: impl Fn(f32) -> f64) -> Vec<Rectangle> {
    let half = (rect.height / 2.0 - 1.0).max(0.0);
    let middle = rect.y + rect.height / 2.0;
    let seconds = |frame: f64| wave.offset + (frame - wave.at as f64) / wave.fps.max(1e-9);
    let mut out = Vec::new();
    let mut x = rect.x.floor();
    while x < rect.x + rect.width {
        let (left, right) = (x.max(rect.x), (x + 1.0).min(rect.x + rect.width));
        let (f0, f1) = (frame_at(left), frame_at(right));
        let level = wave.level((f0 + f1) / 2.0);
        if level > 0.0
            && let Some((low, high)) = wave.peaks.range(seconds(f0), seconds(f1))
        {
            let top = (high * level).clamp(-1.0, 1.0);
            let bottom = (low * level).clamp(-1.0, 1.0);
            let (y0, y1) = (middle - top * half, middle - bottom * half);
            // At least a pixel tall, so quiet passages still read.
            let tall = (y1 - y0).max(1.0);
            let y0 = y0.min(middle + half - tall).max(middle - half - 0.5);
            out.push(Rectangle { x: left, y: y0, width: right - left, height: tall });
        }
        x += 1.0;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn sound() -> impl Strategy<Value = Sound> {
        (1u32..50_000, prop::collection::vec(-1f32..1.0, 0..6000)).prop_map(|(rate, mut samples)| {
            samples.truncate(samples.len() / 2 * 2);
            Sound { rate, samples }
        })
    }

    /// The true lowest and highest sample of frames `a..b`.
    fn exact(sound: &Sound, a: usize, b: usize) -> Option<(f32, f32)> {
        let s = &sound.samples[a * 2..b * 2];
        (!s.is_empty()).then(|| (s.iter().copied().fold(f32::INFINITY, f32::min), s.iter().copied().fold(f32::NEG_INFINITY, f32::max)))
    }

    proptest! {
        /// Every level covers the whole sound, each bucket FACTOR of the
        /// one below, up to a single bucket.
        #[test]
        fn the_levels_cover_the_sound(sound in sound()) {
            let peaks = Peaks::of(&sound);
            prop_assert_eq!(peaks.levels[0].bucket, BASE);
            for pair in peaks.levels.windows(2) {
                prop_assert_eq!(pair[1].bucket, pair[0].bucket * FACTOR);
                prop_assert_eq!(pair[1].low.len(), pair[0].low.len().div_ceil(FACTOR));
            }
            prop_assert!(peaks.levels.last().unwrap().low.len() <= 1);
            // The coarsest level's one bucket is the whole sound's range.
            if let Some(whole) = exact(&sound, 0, sound.frames()) {
                let top = peaks.levels.last().unwrap();
                prop_assert_eq!((top.low[0], top.high[0]), whole);
            }
        }

        /// A span's range holds every sample in it, and reaches no further
        /// than one bucket of the level read past either end, a bucket no
        /// longer than the span.
        #[test]
        fn a_range_holds_the_span_and_little_more(sound in sound(), from in -0.2f64..1.2, long in 0f64..1.2) {
            let peaks = Peaks::of(&sound);
            let seconds = peaks.seconds();
            let (from, to) = (from * seconds, (from + long) * seconds);
            let rate = f64::from(sound.rate);
            let a = (from * rate).floor().max(0.0) as usize;
            let b = ((to * rate).ceil().min(sound.frames() as f64).max(0.0)) as usize;
            let got = peaks.range(from, to);
            match exact(&sound, a.min(b), b) {
                None => prop_assert_eq!(got, None),
                Some((low, high)) => {
                    let (glow, ghigh) = got.expect("a span with samples has a range");
                    prop_assert!(glow <= low && ghigh >= high, "{:?} does not hold {:?}", got, (low, high));
                    let bucket = peaks.level_for(b - a).bucket;
                    prop_assert!(bucket <= (b - a).max(BASE));
                    let wider = exact(&sound, (a / bucket) * bucket, (b.div_ceil(bucket) * bucket).min(sound.frames())).unwrap();
                    prop_assert!(glow >= wider.0 && ghigh <= wider.1, "{:?} reaches past {:?}", got, wider);
                }
            }
        }

        /// The bars are inside the clip on screen, one per pixel column at
        /// most, and none where the clip is silent (no gain).
        #[test]
        fn the_bars_stay_inside_the_clip(
            sound in sound(), x in -50f32..1500.0, wide in 0f32..1500.0, tall in 4f32..120.0, gain in 0f32..4.0,
            offset in -2f64..2.0, per_px in 0.05f64..20.0, fade_in in 0i64..50, fade_out in 0i64..50,
        ) {
            let peaks = Arc::new(Peaks::of(&sound));
            let rect = Rectangle { x, y: 30.0, width: wide, height: tall };
            let wave = Placed { peaks, at: 10, duration: 400, offset, fps: 24.0, gain, fade_in, fade_out };
            let got = bars(&wave, rect, |px| 10.0 + f64::from(px - x) * per_px);
            prop_assert!(got.len() as f32 <= wide.ceil() + 1.0);
            for bar in &got {
                prop_assert!(bar.x >= rect.x - 1e-3 && bar.x + bar.width <= rect.x + rect.width + 1e-3, "{:?} outside {:?}", bar, rect);
                prop_assert!(bar.y >= rect.y - 1e-3 && bar.y + bar.height <= rect.y + rect.height + 1e-3, "{:?} outside {:?}", bar, rect);
                prop_assert!(bar.height >= 1.0);
            }
            if gain == 0.0 {
                prop_assert!(got.is_empty());
            }
        }
    }

    /// A full-scale square wave fills the clip; at half gain, half of it.
    #[test]
    fn gain_scales_the_bars() {
        let samples = (0..48_000).flat_map(|i| if i % 2 == 0 { [1.0, 1.0] } else { [-1.0, -1.0] }).collect();
        let peaks = Arc::new(Peaks::of(&Sound { rate: 48_000, samples }));
        let rect = Rectangle { x: 0.0, y: 0.0, width: 10.0, height: 42.0 };
        let wave = |gain| Placed { peaks: peaks.clone(), at: 0, duration: 24, offset: 0.0, fps: 24.0, gain, fade_in: 0, fade_out: 0 };
        let full = bars(&wave(1.0), rect, f64::from);
        assert_eq!(full.len(), 10);
        assert!(full.iter().all(|b| (b.height - 40.0).abs() < 1e-3), "{full:?}");
        let half = bars(&wave(0.5), rect, f64::from);
        assert!(half.iter().all(|b| (b.height - 20.0).abs() < 1e-3), "{half:?}");
    }
}
