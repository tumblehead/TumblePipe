//! Sound out, and the clock playback keeps to while it plays.
//!
//! A [`Speaker`] plays the mix of the clips placed on it ([`Placed`],
//! `th_media::sound::mix`) on the default output device from a timeline
//! second, and says how far it has got: samples the device has taken are
//! the clock, so the picture follows the sound and the two never drift. With
//! no device (or no sound support in this build) there is no speaker, and
//! playback keeps to the wall clock as before.

use std::sync::{Arc, Mutex};

use th_media::sound::{Sound, Voice, mix};

#[cfg(test)]
use crate::engine::Timeline;

/// A sound placed on the timeline, in seconds.
#[derive(Debug, Clone)]
#[cfg_attr(not(any(feature = "sound", test)), expect(dead_code, reason = "only the device callback reads it"))]
pub struct Placed {
    pub sound: Arc<Sound>,
    pub at: f64,
    pub length: f64,
    /// The second of the sound it starts at.
    pub offset: f64,
    pub gain: f32,
    pub fade_in: f64,
    pub fade_out: f64,
}

/// What the device callback and the UI share.
#[derive(Debug, Default)]
struct Playback {
    voices: Vec<Placed>,
    playing: bool,
    /// The output frame the next callback starts at: timeline second
    /// `frame / rate`.
    frame: u64,
}

impl Playback {
    /// The next `out.len() / 2` stereo frames: the mix while playing,
    /// silence otherwise.
    #[cfg_attr(not(any(feature = "sound", test)), expect(dead_code, reason = "the device callback renders"))]
    fn render(&mut self, rate: u32, out: &mut [f32]) {
        if !self.playing {
            out.fill(0.0);
            return;
        }
        let voices: Vec<Voice> = self
            .voices
            .iter()
            .map(|p| Voice {
                sound: &p.sound,
                at: p.at,
                length: p.length,
                offset: p.offset,
                gain: p.gain,
                fade_in: p.fade_in,
                fade_out: p.fade_out,
            })
            .collect();
        mix(&voices, rate, self.frame, out);
        self.frame += (out.len() / 2) as u64;
    }
}

/// The default output device, playing the mix.
pub struct Speaker {
    shared: Arc<Mutex<Playback>>,
    rate: u32,
    #[cfg(feature = "sound")]
    _stream: cpal::Stream,
}

impl std::fmt::Debug for Speaker {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Speaker").field("rate", &self.rate).finish_non_exhaustive()
    }
}

impl Speaker {
    /// The default output device, or why there is none.
    #[cfg(feature = "sound")]
    pub fn open() -> Result<Self, String> {
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
        let device = cpal::default_host().default_output_device().ok_or("no audio output device")?;
        let config = device.default_output_config().map_err(|e| e.to_string())?;
        let rate = config.sample_rate();
        let channels = usize::from(config.channels());
        let shared = Arc::new(Mutex::new(Playback::default()));
        let stream_config: cpal::StreamConfig = config.into();
        let error = |e: cpal::Error| eprintln!("cinedex: audio output: {e}");
        macro_rules! stream {
            ($t:ty) => {{
                let shared = Arc::clone(&shared);
                let mut stereo = Vec::new();
                device.build_output_stream(
                    stream_config,
                    move |data: &mut [$t], _| {
                        let frames = data.len() / channels.max(1);
                        stereo.resize(frames * 2, 0.0f32);
                        shared.lock().unwrap().render(rate, &mut stereo);
                        for (i, out) in data.chunks_mut(channels.max(1)).enumerate() {
                            for (c, sample) in out.iter_mut().enumerate() {
                                let s = if c < 2 { stereo[i * 2 + c] } else { 0.0 };
                                *sample = <$t as cpal::FromSample<f32>>::from_sample_(s);
                            }
                        }
                    },
                    error,
                    None,
                )
            }};
        }
        let stream = match config.sample_format() {
            cpal::SampleFormat::F32 => stream!(f32),
            cpal::SampleFormat::I16 => stream!(i16),
            cpal::SampleFormat::U16 => stream!(u16),
            cpal::SampleFormat::I32 => stream!(i32),
            other => return Err(format!("the output device takes {other} samples")),
        }
        .map_err(|e| e.to_string())?;
        stream.play().map_err(|e| e.to_string())?;
        Ok(Self { shared, rate, _stream: stream })
    }

    #[cfg(not(feature = "sound"))]
    pub fn open() -> Result<Self, String> {
        Err("this build has no sound".into())
    }

    /// Play `voices` from now on.
    pub fn set_voices(&self, voices: Vec<Placed>) {
        self.shared.lock().unwrap().voices = voices;
    }

    /// Whether there is anything to hear: playback keeps to the speaker's
    /// clock only then.
    pub fn has_voices(&self) -> bool {
        !self.shared.lock().unwrap().voices.is_empty()
    }

    /// Play from timeline second `seconds`.
    pub fn play_from(&self, seconds: f64) {
        let mut playback = self.shared.lock().unwrap();
        playback.frame = (seconds.max(0.0) * f64::from(self.rate)).round() as u64;
        playback.playing = true;
    }

    pub fn stop(&self) {
        self.shared.lock().unwrap().playing = false;
    }

    pub fn is_playing(&self) -> bool {
        self.shared.lock().unwrap().playing
    }

    /// The timeline second it has played to.
    pub fn seconds(&self) -> f64 {
        self.shared.lock().unwrap().frame as f64 / f64::from(self.rate)
    }
}

/// Seconds within which two times are the same.
#[cfg(test)]
const EPSILON: f64 = 1e-9;

/// The timeline second position `position` starts at, for clips laid end to
/// end (a playlist), each at its own rate: the tests' reference clock.
#[cfg(test)]
fn seconds_at(timeline: &Timeline, position: usize) -> f64 {
    timeline
        .clips
        .iter()
        .map(|c| {
            let inside = position.saturating_sub(c.at).min(c.frames);
            inside as f64 * f64::from(c.rate.1) / f64::from(c.rate.0)
        })
        .sum()
}

/// The timeline position playing at second `seconds`: the inverse of
/// `seconds_at`, the frame whose time span holds it (the last frame past
/// the end).
#[cfg(test)]
fn position_at(timeline: &Timeline, seconds: f64) -> usize {
    let mut start = 0.0;
    for c in &timeline.clips {
        let frame = f64::from(c.rate.1) / f64::from(c.rate.0);
        let length = c.frames as f64 * frame;
        // Within a nanosecond of a frame's start is that frame: the clock's
        // samples, and `seconds_at`, land on frame starts only to rounding.
        if seconds + EPSILON < start + length {
            let into = ((seconds - start + EPSILON) / frame).floor().max(0.0) as usize;
            return c.at + into.min(c.frames.saturating_sub(1));
        }
        start += length;
    }
    timeline.len().saturating_sub(1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Clip;
    use proptest::prelude::*;

    fn playlist(clips: &[(usize, (i32, i32))]) -> Timeline {
        Timeline::end_to_end(clips.iter().enumerate().map(|(s, &(n, rate))| Clip::cut(s, n, rate)).collect())
    }

    fn rates() -> impl Strategy<Value = (i32, i32)> {
        prop::sample::select(vec![(24, 1), (25, 1), (30, 1), (24000, 1001), (30000, 1001)])
    }

    proptest! {
        /// Every position's start second maps back to it, and seconds grow
        /// with positions.
        #[test]
        fn seconds_and_positions_round_trip(clips in prop::collection::vec((1usize..60, rates()), 1..5)) {
            let timeline = playlist(&clips);
            let mut last = -1.0;
            for p in 0..timeline.len() {
                let s = seconds_at(&timeline, p);
                prop_assert!(s > last);
                last = s;
                prop_assert_eq!(position_at(&timeline, s), p);
            }
            prop_assert_eq!(position_at(&timeline, 1e9), timeline.len() - 1);
        }

        /// Audio and picture stay in sync: playing a sound placed at a
        /// clip's start from any position, after `n` samples the clock's
        /// position is the frame whose span holds the `n`-th sample, and the
        /// samples rendered are the sound's own from that frame on.
        #[test]
        fn the_clock_follows_the_samples(
            frames in 10usize..200,
            rate in rates(),
            out_rate in prop::sample::select(vec![44100u32, 48000]),
            from in any::<prop::sample::Index>(),
            chunks in prop::collection::vec(1usize..2000, 1..6),
        ) {
            let timeline = playlist(&[(frames, rate)]);
            // A ramp sound at the output rate, as long as the clip: sample n is n.
            let n = (seconds_at(&timeline, frames) * f64::from(out_rate)) as usize;
            let sound = Arc::new(Sound { rate: out_rate, samples: (0..n).flat_map(|i| [i as f32 * 1e-6; 2]).collect() });
            let placed = Placed { sound, at: 0.0, length: seconds_at(&timeline, frames), offset: 0.0, gain: 1.0, fade_in: 0.0, fade_out: 0.0 };
            let start = from.index(frames);
            let mut playback = Playback { voices: vec![placed], playing: true, frame: 0 };
            playback.frame = (seconds_at(&timeline, start) * f64::from(out_rate)).round() as u64;
            let first = playback.frame;
            let mut played = 0u64;
            for chunk in chunks {
                let mut out = vec![0f32; chunk * 2];
                playback.render(out_rate, &mut out);
                for (i, f) in out.chunks(2).enumerate() {
                    let sample = first + played + i as u64;
                    if (sample as usize) + 1 < n {
                        prop_assert!((f[0] - sample as f32 * 1e-6).abs() < 1e-6, "sample {} plays {}", sample, f[0]);
                    }
                }
                played += chunk as u64;
                let seconds = playback.frame as f64 / f64::from(out_rate);
                let expected = ((seconds + EPSILON) * f64::from(rate.0) / f64::from(rate.1)).floor() as usize;
                prop_assert_eq!(position_at(&timeline, seconds), expected.min(frames - 1));
            }
        }
    }

    #[test]
    fn a_stopped_speaker_is_silent() {
        let sound = Arc::new(Sound { rate: 100, samples: vec![0.5; 200] });
        let placed = Placed { sound, at: 0.0, length: 1.0, offset: 0.0, gain: 1.0, fade_in: 0.0, fade_out: 0.0 };
        let mut playback = Playback { voices: vec![placed], playing: false, frame: 10 };
        let mut out = vec![1f32; 20];
        playback.render(100, &mut out);
        assert!(out.iter().all(|&s| s == 0.0));
        assert_eq!(playback.frame, 10, "the clock stands while stopped");
    }
}
