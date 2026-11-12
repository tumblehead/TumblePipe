//! Sound: a file's audio decoded whole into memory, and the mix of an edit's
//! audio clips.
//!
//! A [`Sound`] is stereo f32 at the file's own rate, read once: a review's
//! audio is minutes long (a five minute stereo track is 58 MB as f32), and
//! holding it whole makes every seek and scrub exact and free. Mono plays on
//! both sides; more channels than two keep the first two. A WAV file is read
//! here (the vendored FFmpeg has no WAV demuxer); a movie's audio stream goes
//! through FFmpeg, its samples converted from whatever format the decoder
//! gives.
//!
//! [`mix`] renders the voices at an output rate: each clip's sound from its
//! in point, resampled linearly, at its gain, through its fades. Pure: the
//! player's audio output calls it from the device's callback.

use std::path::Path;
use std::ptr;

use th_ffmpeg_sys as ff;

use crate::movie::{Input, ff_error};
use crate::{Error, Result};

/// A file's audio: stereo, interleaved (left, right), at `rate`.
#[derive(Debug, Clone, PartialEq)]
pub struct Sound {
    pub rate: u32,
    pub samples: Vec<f32>,
}

impl Sound {
    /// Sample frames (one left and one right sample each).
    pub fn frames(&self) -> usize {
        self.samples.len() / 2
    }

    /// Its length in seconds.
    pub fn seconds(&self) -> f64 {
        self.frames() as f64 / f64::from(self.rate.max(1))
    }

    /// The sound of the file at `path`: None when it has no audio.
    pub fn read(path: &str) -> Result<Option<Sound>> {
        let fail = |message: String| Error::Open { path: path.to_owned(), message };
        let is_wav = Path::new(path).extension().is_some_and(|e| e.eq_ignore_ascii_case("wav"));
        if is_wav {
            let bytes = std::fs::read(path).map_err(|e| fail(e.to_string()))?;
            return parse_wav(&bytes).map(Some).map_err(fail);
        }
        decode(path).map_err(fail)
    }

    /// Its stereo sample frame at fractional frame `at`, linearly between
    /// its neighbours; silence outside it.
    fn at(&self, at: f64) -> [f32; 2] {
        if at < 0.0 {
            return [0.0; 2];
        }
        let i = at.floor() as usize;
        let frac = (at - at.floor()) as f32;
        if i >= self.frames() {
            return [0.0; 2];
        }
        let frame = |i: usize| -> [f32; 2] { if i < self.frames() { [self.samples[i * 2], self.samples[i * 2 + 1]] } else { [0.0; 2] } };
        let (a, b) = (frame(i), frame(i + 1));
        [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac]
    }
}

/// Stereo from `channels` interleaved samples of one frame.
fn stereo(frame: &[f32]) -> [f32; 2] {
    match frame.len() {
        0 => [0.0; 2],
        1 => [frame[0]; 2],
        _ => [frame[0], frame[1]],
    }
}

/// A RIFF WAVE file's audio: PCM 8, 16, 24 or 32 bit, or 32/64-bit float
/// (format 1, 3, or the extensible format naming either).
pub fn parse_wav(bytes: &[u8]) -> std::result::Result<Sound, String> {
    if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("not a WAV file".into());
    }
    let u16_at = |at: usize| u16::from_le_bytes([bytes[at], bytes[at + 1]]);
    let u32_at = |at: usize| u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]);
    let mut format = None;
    let mut data = None;
    let mut at = 12;
    while at + 8 <= bytes.len() {
        let (id, size) = (&bytes[at..at + 4], u32_at(at + 4) as usize);
        let body = at + 8;
        let end = body.saturating_add(size).min(bytes.len());
        match id {
            b"fmt " if size >= 16 && end - body >= 16 => {
                let mut tag = u16_at(body);
                if tag == 0xFFFE && size >= 26 {
                    // WAVE_FORMAT_EXTENSIBLE: the real format leads its GUID.
                    tag = u16_at(body + 24);
                }
                format = Some((tag, u16_at(body + 2), u32_at(body + 4), u16_at(body + 14)));
            }
            b"data" => data = Some(&bytes[body..end]),
            _ => {}
        }
        // Chunks are padded to an even size.
        at = body + size + (size & 1);
    }
    let (tag, channels, rate, bits) = format.ok_or("no fmt chunk")?;
    let data = data.ok_or("no data chunk")?;
    let channels = usize::from(channels);
    if channels == 0 || rate == 0 {
        return Err("no channels or no rate".into());
    }
    let sample = |s: &[u8]| -> Option<f32> {
        Some(match (tag, bits) {
            (1, 8) => (f32::from(s[0]) - 128.0) / 128.0,
            (1, 16) => f32::from(i16::from_le_bytes([s[0], s[1]])) / 32768.0,
            (1, 24) => (i32::from_le_bytes([0, s[0], s[1], s[2]]) >> 8) as f32 / 8_388_608.0,
            (1, 32) => i32::from_le_bytes([s[0], s[1], s[2], s[3]]) as f32 / 2_147_483_648.0,
            (3, 32) => f32::from_le_bytes([s[0], s[1], s[2], s[3]]),
            (3, 64) => f64::from_le_bytes([s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]]) as f32,
            _ => return None,
        })
    };
    let width = usize::from(bits / 8);
    if width == 0 || sample(&vec![0; width]).is_none() {
        return Err(format!("unsupported WAV sample format {tag} at {bits} bits"));
    }
    let mut samples = Vec::with_capacity(data.len() / width / channels * 2);
    let mut frame = vec![0f32; channels];
    for chunk in data.chunks_exact(width * channels) {
        for (c, s) in chunk.chunks_exact(width).enumerate() {
            frame[c] = sample(s).unwrap_or(0.0);
        }
        samples.extend(stereo(&frame));
    }
    Ok(Sound { rate, samples })
}

/// One decoded FFmpeg audio frame appended to `out` as stereo.
///
/// # Safety
/// `frame` is a frame the audio decoder just produced.
unsafe fn append(frame: &ff::AVFrame, out: &mut Vec<f32>) -> std::result::Result<(), String> {
    let channels = frame.ch_layout.nb_channels.max(0) as usize;
    let count = frame.nb_samples.max(0) as usize;
    if channels == 0 || count == 0 {
        return Ok(());
    }
    // The packed form of the format, and whether it is planar.
    let (base, planar) = match frame.format {
        ff::AV_SAMPLE_FMT_U8P => (ff::AV_SAMPLE_FMT_U8, true),
        ff::AV_SAMPLE_FMT_S16P => (ff::AV_SAMPLE_FMT_S16, true),
        ff::AV_SAMPLE_FMT_S32P => (ff::AV_SAMPLE_FMT_S32, true),
        ff::AV_SAMPLE_FMT_FLTP => (ff::AV_SAMPLE_FMT_FLT, true),
        ff::AV_SAMPLE_FMT_DBLP => (ff::AV_SAMPLE_FMT_DBL, true),
        ff::AV_SAMPLE_FMT_S64P => (ff::AV_SAMPLE_FMT_S64, true),
        packed @ (ff::AV_SAMPLE_FMT_U8
        | ff::AV_SAMPLE_FMT_S16
        | ff::AV_SAMPLE_FMT_S32
        | ff::AV_SAMPLE_FMT_FLT
        | ff::AV_SAMPLE_FMT_DBL
        | ff::AV_SAMPLE_FMT_S64) => (packed, false),
        other => return Err(format!("unsupported audio sample format {other}")),
    };
    let width = match base {
        ff::AV_SAMPLE_FMT_U8 => 1,
        ff::AV_SAMPLE_FMT_S16 => 2,
        ff::AV_SAMPLE_FMT_S32 | ff::AV_SAMPLE_FMT_FLT => 4,
        _ => 8,
    };
    let value = |s: &[u8]| -> f32 {
        match base {
            ff::AV_SAMPLE_FMT_U8 => (f32::from(s[0]) - 128.0) / 128.0,
            ff::AV_SAMPLE_FMT_S16 => f32::from(i16::from_ne_bytes([s[0], s[1]])) / 32768.0,
            ff::AV_SAMPLE_FMT_S32 => i32::from_ne_bytes([s[0], s[1], s[2], s[3]]) as f32 / 2_147_483_648.0,
            ff::AV_SAMPLE_FMT_FLT => f32::from_ne_bytes([s[0], s[1], s[2], s[3]]),
            ff::AV_SAMPLE_FMT_DBL => f64::from_ne_bytes(s[..8].try_into().unwrap()) as f32,
            _ => i64::from_ne_bytes(s[..8].try_into().unwrap()) as f32 / 9.223_372e18,
        }
    };
    let used = channels.min(2);
    let planes: Vec<&[u8]> = if planar {
        (0..used).map(|c| unsafe { std::slice::from_raw_parts(*frame.extended_data.add(c), count * width) }).collect()
    } else {
        vec![unsafe { std::slice::from_raw_parts(*frame.extended_data, count * width * channels) }]
    };
    let mut one = [0f32; 2];
    for i in 0..count {
        for (c, slot) in one.iter_mut().enumerate().take(used) {
            let at = if planar { i * width } else { (i * channels + c) * width };
            let plane = if planar { planes[c] } else { planes[0] };
            *slot = value(&plane[at..at + width]);
        }
        out.extend(stereo(&one[..used]));
    }
    Ok(())
}

/// A movie's (or any file FFmpeg opens) audio stream, decoded whole.
fn decode(path: &str) -> std::result::Result<Option<Sound>, String> {
    let input = match Input::open_stream(path, ff::AVMEDIA_TYPE_AUDIO) {
        Ok(input) => input,
        Err(e) if e.starts_with("no decodable") => return Ok(None),
        Err(e) => return Err(e),
    };
    let mut samples = Vec::new();
    let rate;
    unsafe {
        let mut codec = ff::avcodec_alloc_context3(input.decoder);
        let mut packet = ff::av_packet_alloc();
        let mut decoded = ff::av_frame_alloc();
        let result = (|| -> std::result::Result<(), String> {
            ff::avcodec_parameters_to_context(codec, (*input.stream()).codecpar);
            let code = ff::avcodec_open2(codec, input.decoder, ptr::null_mut());
            if code < 0 {
                return Err(ff_error(code));
            }
            let mut ended = false;
            loop {
                let code = ff::avcodec_receive_frame(codec, decoded);
                if code == 0 {
                    let appended = append(&*decoded, &mut samples);
                    ff::av_frame_unref(decoded);
                    appended?;
                    continue;
                }
                if code == ff::AVERROR_EOF {
                    return Ok(());
                }
                if code != ff::AVERROR_EAGAIN {
                    return Err(ff_error(code));
                }
                if ended {
                    return Ok(());
                }
                let read = ff::av_read_frame(input.format, packet);
                if read == ff::AVERROR_EOF {
                    ff::avcodec_send_packet(codec, ptr::null());
                    ended = true;
                    continue;
                }
                if read < 0 {
                    return Err(ff_error(read));
                }
                if (*packet).stream_index == input.stream_index {
                    let sent = ff::avcodec_send_packet(codec, packet);
                    if sent < 0 && sent != ff::AVERROR_EAGAIN {
                        ff::av_packet_unref(packet);
                        return Err(ff_error(sent));
                    }
                }
                ff::av_packet_unref(packet);
            }
        })();
        rate = (*codec).sample_rate.max(0) as u32;
        ff::av_frame_free(&mut decoded);
        ff::av_packet_free(&mut packet);
        ff::avcodec_free_context(&mut codec);
        result?;
    }
    if rate == 0 {
        return Err("the audio stream has no sample rate".into());
    }
    Ok(Some(Sound { rate, samples }))
}

/// One clip in the mix: a sound placed on the edit.
#[derive(Debug, Clone, Copy)]
pub struct Voice<'a> {
    pub sound: &'a Sound,
    /// Where it starts on the edit and how long it plays, in seconds.
    pub at: f64,
    pub length: f64,
    /// The second of its sound it starts at (its in point).
    pub offset: f64,
    /// Its linear gain.
    pub gain: f32,
    /// Seconds it fades in and out over (linear ramps).
    pub fade_in: f64,
    pub fade_out: f64,
}

impl Voice<'_> {
    /// How loud it is at edit second `t`: 0 outside it, its gain through
    /// its fades.
    pub fn level(&self, t: f64) -> f32 {
        if t < self.at || t >= self.at + self.length {
            return 0.0;
        }
        let mut envelope = 1.0f64;
        if self.fade_in > 0.0 {
            envelope = envelope.min((t - self.at) / self.fade_in);
        }
        if self.fade_out > 0.0 {
            envelope = envelope.min((self.at + self.length - t) / self.fade_out);
        }
        self.gain * envelope.clamp(0.0, 1.0) as f32
    }
}

/// The mix of `voices` at `rate` from output sample frame `from`, into
/// `out` (stereo, interleaved; its length says how many frames), each
/// sample clamped to -1..1.
pub fn mix(voices: &[Voice], rate: u32, from: u64, out: &mut [f32]) {
    out.fill(0.0);
    let rate = f64::from(rate.max(1));
    for voice in voices {
        let source_rate = f64::from(voice.sound.rate);
        for (i, frame) in out.as_chunks_mut::<2>().0.iter_mut().enumerate() {
            let t = (from + i as u64) as f64 / rate;
            let level = voice.level(t);
            if level == 0.0 {
                continue;
            }
            let [l, r] = voice.sound.at((voice.offset + t - voice.at) * source_rate);
            frame[0] += l * level;
            frame[1] += r * level;
        }
    }
    for s in out {
        *s = s.clamp(-1.0, 1.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A WAV file of `frames` (each `channels` samples in -1..1) as `bits`
    /// PCM (or 32-bit float when `float`).
    pub fn wav(frames: &[Vec<f32>], channels: u16, rate: u32, bits: u16, float: bool) -> Vec<u8> {
        let mut data = Vec::new();
        for frame in frames {
            for &s in frame {
                match (float, bits) {
                    (true, _) => data.extend(s.to_le_bytes()),
                    (false, 16) => data.extend(((s * 32767.0).round() as i16).to_le_bytes()),
                    (false, 24) => data.extend(&((s * 8_388_607.0).round() as i32).to_le_bytes()[..3]),
                    (false, 8) => data.push((s * 127.0 + 128.0).round() as u8),
                    _ => unreachable!(),
                }
            }
        }
        let block = channels * bits / 8;
        let mut out = Vec::new();
        out.extend(b"RIFF");
        out.extend((36 + data.len() as u32).to_le_bytes());
        out.extend(b"WAVE");
        out.extend(b"fmt ");
        out.extend(16u32.to_le_bytes());
        out.extend((if float { 3u16 } else { 1u16 }).to_le_bytes());
        out.extend(channels.to_le_bytes());
        out.extend(rate.to_le_bytes());
        out.extend((rate * u32::from(block)).to_le_bytes());
        out.extend(block.to_le_bytes());
        out.extend(bits.to_le_bytes());
        // An odd-sized chunk a reader must step over, padding and all.
        out.extend(b"LIST");
        out.extend(3u32.to_le_bytes());
        out.extend([1, 2, 3, 0]);
        out.extend(b"data");
        out.extend((data.len() as u32).to_le_bytes());
        out.extend(data);
        out
    }

    proptest! {
        /// A WAV reads back as written, to its bit depth, mono on both
        /// sides and only the first two of more channels.
        #[test]
        fn a_wav_reads_back(
            channels in 1u16..4,
            rate in prop::sample::select(vec![44100u32, 48000, 96000]),
            format in prop::sample::select(vec![(8u16, false), (16, false), (24, false), (32, true)]),
            frames in prop::collection::vec(prop::collection::vec(-1f32..1.0, 3), 0..50),
        ) {
            let frames: Vec<Vec<f32>> = frames.into_iter().map(|f| f[..usize::from(channels)].to_vec()).collect();
            let sound = parse_wav(&wav(&frames, channels, rate, format.0, format.1)).unwrap();
            prop_assert_eq!(sound.rate, rate);
            prop_assert_eq!(sound.frames(), frames.len());
            let tolerance = if format.1 { 1e-7 } else { 2.0 / f32::from(1u16 << (format.0.min(16) - 1)) };
            for (i, f) in frames.iter().enumerate() {
                let want = stereo(f);
                for (c, want) in want.iter().enumerate() {
                    prop_assert!((sound.samples[i * 2 + c] - want).abs() <= tolerance, "{} vs {}", sound.samples[i * 2 + c], want);
                }
            }
        }

        /// The mix is the sum of its voices (below clipping), silent where
        /// none plays, and a voice plays its sound from its in point at its
        /// level: a ramp resampled to any rate is the ramp.
        #[test]
        fn the_mix_is_the_sum_of_its_voices(
            out_rate in prop::sample::select(vec![44100u32, 48000]),
            voices in prop::collection::vec((prop::sample::select(vec![22050u32, 44100, 48000]), 0f64..0.05, 0.001f64..0.05, 0f64..0.02, 0f32..0.4, 0f64..0.01, 0f64..0.01), 1..4),
            from in 0u64..2000,
        ) {
            // Each sound a ramp: frame n is n / 1e5 on the left, minus that on the right.
            let sounds: Vec<Sound> = voices.iter().map(|v| {
                let n = (v.0 as f64 * 0.2) as usize;
                Sound { rate: v.0, samples: (0..n).flat_map(|i| [i as f32 / 1e5, -(i as f32) / 1e5]).collect() }
            }).collect();
            let voices: Vec<Voice> = voices.iter().zip(&sounds).map(|(v, sound)| Voice {
                sound, at: v.1, length: v.2, offset: v.3, gain: v.4, fade_in: v.5.min(v.2 / 2.0), fade_out: v.6.min(v.2 / 2.0),
            }).collect();
            let mut out = vec![0f32; 2 * 1000];
            mix(&voices, out_rate, from, &mut out);
            let mut parts = vec![0f32; 2 * 1000];
            let mut sum = vec![0f32; 2 * 1000];
            for v in &voices {
                mix(std::slice::from_ref(v), out_rate, from, &mut parts);
                for (s, p) in sum.iter_mut().zip(&parts) {
                    *s += p;
                }
            }
            for (i, (got, want)) in out.iter().zip(&sum).enumerate() {
                prop_assert!((got - want).abs() <= 1e-5, "sample {}: {} vs {}", i, got, want);
            }
            for i in 0..1000 {
                let t = (from + i as u64) as f64 / f64::from(out_rate);
                let ramp: f64 = voices.iter().map(|v| {
                    let position = (v.offset + t - v.at) * f64::from(v.sound.rate);
                    let inside = position >= 0.0 && position < (v.sound.frames() - 1) as f64;
                    if inside { f64::from(v.level(t)) * position / 1e5 } else { 0.0 }
                }).sum();
                prop_assert!((f64::from(out[i * 2]) - ramp).abs() <= 1e-4, "frame {}: {} vs {}", i, out[i * 2], ramp);
                prop_assert!((f64::from(out[i * 2 + 1]) + ramp).abs() <= 1e-4);
            }
        }
    }

    #[test]
    fn a_voice_fades_and_clips_stay_in_range() {
        let loud = Sound { rate: 100, samples: vec![0.9; 2 * 1000] };
        let voice = Voice { sound: &loud, at: 1.0, length: 4.0, offset: 0.0, gain: 1.0, fade_in: 1.0, fade_out: 2.0 };
        assert_eq!(voice.level(0.99), 0.0);
        assert!((voice.level(1.5) - 0.5).abs() < 1e-6);
        assert_eq!(voice.level(2.5), 1.0);
        assert!((voice.level(4.0) - 0.5).abs() < 1e-6);
        assert_eq!(voice.level(5.0), 0.0, "its end is outside it");
        let mut out = vec![0f32; 2 * 50];
        mix(&[voice, voice], 100, 250, &mut out);
        assert!(out.iter().all(|&s| s == 1.0), "two loud voices clip at 1");
    }

    fn fixture(name: &str) -> String {
        format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))
    }

    /// A movie's AAC track decodes whole: a second of a 1 kHz tone at
    /// 48 kHz, stereo.
    #[test]
    fn a_movies_audio_decodes() {
        let sound = Sound::read(&fixture("tone_aac.mov")).unwrap().expect("the fixture has audio");
        assert_eq!(sound.rate, 48000);
        assert!((sound.seconds() - 1.0).abs() < 0.05, "{} s", sound.seconds());
        // Count rising zero crossings on the left over the middle half second.
        let left: Vec<f32> = sound.samples.chunks(2).map(|f| f[0]).collect();
        let middle = &left[12000..36000];
        let rising = middle.windows(2).filter(|w| w[0] < 0.0 && w[1] >= 0.0).count();
        assert!((495..=505).contains(&rising), "{rising} cycles in half a second");
        assert!(middle.iter().any(|s| s.abs() > 0.05), "it is not silent");
        assert_eq!(Sound::read(&fixture("grey_ramp_gop5.mp4")).unwrap(), None, "a movie without audio has no sound");
    }
}
