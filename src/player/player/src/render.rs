//! A published edit rendered without a window, for the farm's Render Edit
//! job (`designs/edit-entity.md` §6): every frame as a PNG, the sound mixed
//! into a WAV, and `render.json` saying what was written. The worker encodes
//! the movie from them with Houdini's FFmpeg; this build has no encoder.
//!
//! What a frame shows is what the editor shows (`th_edit::picture`): the
//! top-most playable clip, blended through its fades over the tracks below,
//! each picture fitted into the edit's resolution and a render taken through
//! the config's default display and view. A slug, a shot without a take or a
//! frame its media has not is black. What it hears is the editor's mix: a
//! shot clip's playblast sound under it, audio clips at their level.
//!
//! ```text
//! <out>/edit.0000.png ... edit.<n-1>.png   the edit's frames, from 0
//! <out>/edit.wav                            16-bit stereo, when there is sound
//! <out>/render.json                         {"fps", "frames", "width", "height",
//!                                            "start_timecode", "audio"}
//! ```

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use th_edit::document::{Frame as EditFrame, Source};
use th_edit::picture::{Playable, layers_at};
use th_media::sound::{Voice, mix};
use th_media::{ExrView, Frame, Media, MovieDecoder, Pixels};

use crate::colour::{Colour, Lut};
use crate::editor::{ProjectPaths, Resolved, open_document, resolve};

/// The rate the mix is written at.
pub const AUDIO_RATE: u32 = 48_000;
/// The frames' file name stem.
pub const STEM: &str = "edit";

/// What a render wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rendered {
    pub frames: usize,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub audio: bool,
}

/// The file of edit frame `at`.
pub fn frame_path(out: &Path, at: usize) -> PathBuf {
    out.join(format!("{STEM}.{at:04}.png"))
}

/// A source, opened, with what decodes it.
struct Opened {
    resolved: Resolved,
    lut: Option<Arc<Lut>>,
    /// The movie decoder, opened on first use (frames go in order, so one
    /// decoder reads each movie straight through).
    decoder: Option<MovieDecoder>,
}

struct Sources<'a> {
    /// The edit's distinct sources; `opened` holds each at its index.
    sources: Vec<&'a Source>,
    opened: Vec<Opened>,
}

impl Sources<'_> {
    fn index(&self, source: &Source) -> Option<usize> {
        self.sources.iter().position(|s| *s == source)
    }
}

impl Playable<Source> for Sources<'_> {
    fn playable(&self, source: &Source, frame: EditFrame) -> bool {
        let Some(i) = self.index(source) else { return false };
        let resolved = &self.opened[i].resolved;
        resolved.media.as_ref().is_some_and(|m| frame >= resolved.first && ((frame - resolved.first) as usize) < m.frame_count())
    }
}

/// Render the published edit `edit` (`entity:/edits/<name>@vNNNN`) of the
/// project at `paths` into `out`, through the OCIO config `ocio`. `progress`
/// hears each frame written.
pub fn render(
    paths: &ProjectPaths,
    ocio: &str,
    edit: &str,
    out: &Path,
    mut progress: impl FnMut(usize, usize),
) -> Result<Rendered, String> {
    if !edit.contains('@') {
        return Err(format!("{edit}: Render Edit renders a published version (entity:/edits/<name>@vNNNN)"));
    }
    let (doc, _) = open_document(paths, Some(edit), &[])?;
    let edit = doc.edit;
    let fps = edit.fps.max(1);
    let [width, height] = edit.resolution;
    std::fs::create_dir_all(out).map_err(|e| format!("{}: {e}", out.display()))?;
    let mut colour = Colour::open(ocio).map_err(|e| format!("the OCIO config: {e}"))?;
    let (display, view) = colour.default();

    // Every distinct source, resolved once, side by side: on a file server
    // each is a few round trips.
    let mut distinct: Vec<&Source> = Vec::new();
    for source in edit
        .video
        .iter()
        .flat_map(|t| t.clips.iter().map(|c| &c.source))
        .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)))
    {
        if !distinct.contains(&source) {
            distinct.push(source);
        }
    }
    let resolved: Vec<Result<Resolved, String>> = std::thread::scope(|scope| {
        let running: Vec<_> = distinct.iter().map(|&s| scope.spawn(move || resolve(paths, s, fps))).collect();
        running.into_iter().map(|t| t.join().unwrap_or_else(|_| Err("resolving panicked".into()))).collect()
    });
    let mut sources = Sources { sources: Vec::new(), opened: Vec::new() };
    for (source, resolved) in distinct.iter().zip(resolved) {
        let resolved = resolved.map_err(|e| format!("{}: {e}", describe(source)))?;
        let lut = match resolved.media.as_ref().and_then(|m| m.colour_space()) {
            Some(cs) => Some(colour.lut(cs, &display, &view)?),
            None => None,
        };
        sources.sources.push(source);
        sources.opened.push(Opened { resolved, lut, decoder: None });
    }

    let frames = edit.length().max(0) as usize;
    for at in 0..frames {
        let layers = layers_at(&edit, at as EditFrame, &sources);
        let mut pictures: Vec<(Frame, f32, Option<Arc<Lut>>)> = Vec::new();
        for layer in &layers {
            let clip = &edit.video[layer.track].clips[layer.clip];
            let i = sources.index(&clip.source).expect("every clip's source is opened");
            let opened = &mut sources.opened[i];
            let index = (layer.frame - opened.resolved.first) as usize;
            let media = Arc::clone(opened.resolved.media.as_ref().expect("a playable source has media"));
            let frame = decode(&media, index, &mut opened.decoder).map_err(|e| format!("{} frame {index}: {e}", describe(&clip.source)))?;
            pictures.push((frame, layer.opacity, opened.lut.clone()));
        }
        let input: Vec<(&Frame, f32)> = pictures.iter().map(|(f, o, _)| (f, *o)).collect();
        let picture = th_media::composite::dissolve(width, height, &input, |layer, rgb| match &pictures[layer].2 {
            Some(lut) => lut.sample(rgb),
            None => rgb,
        });
        write_png(&frame_path(out, at), &picture)?;
        progress(at, frames);
    }

    let audio = write_mix(&edit, &sources, fps, frames, &out.join(format!("{STEM}.wav")))?;
    let summary = serde_json::json!({
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "start_timecode": edit.start_timecode,
        "audio": audio.then(|| format!("{STEM}.wav")),
    });
    std::fs::write(out.join("render.json"), serde_json::to_string_pretty(&summary).unwrap()).map_err(|e| e.to_string())?;
    Ok(Rendered { frames, width, height, fps, audio })
}

fn describe(source: &Source) -> String {
    match source {
        Source::Shot { shot, .. } => shot.clone(),
        Source::Media { item, .. } => format!("media {item}"),
        Source::Slug { text } => format!("slug {text:?}"),
    }
}

/// Frame `index` of `media`: a movie's through `decoder`, an EXR's default
/// view, a stack's layers composited.
fn decode(media: &Media, index: usize, decoder: &mut Option<MovieDecoder>) -> Result<Frame, String> {
    match media {
        Media::Movie(movie) => {
            if decoder.is_none() {
                *decoder = Some(MovieDecoder::open(Arc::clone(movie), 4).map_err(|e| e.to_string())?);
            }
            decoder.as_mut().unwrap().decode(index, |_, _| {}, || true).map_err(|e| e.to_string())
        }
        Media::Exr { sequence, .. } => sequence.decode(index, &media.default_view()).map_err(|e| e.to_string()),
        Media::Stack(layers) => {
            let mut frames = Vec::new();
            for (_, layer) in layers.iter().filter(|l| index < l.1.frame_count()) {
                frames.push(match &**layer {
                    Media::Exr { sequence, .. } => {
                        let view: ExrView = layer.default_view();
                        sequence.decode_with_alpha_while(index, &view, &|| true).map_err(|e| e.to_string())?
                    }
                    // A layer movie's frames are all keyframes: each alone.
                    other => decode(other, index, &mut None)?,
                });
            }
            let first = frames.first().ok_or("no layer has the frame")?;
            th_media::composite::composite(first.width, first.height, &frames)
        }
    }
}

/// `picture` (display code values, as `dissolve` makes them) as an RGB PNG.
fn write_png(path: &Path, picture: &Frame) -> Result<(), String> {
    let Pixels::Rgba8(rgba) = &*picture.pixels else { return Err("a picture not in code values".into()) };
    let rgb: Vec<u8> = rgba.as_chunks::<4>().0.iter().flat_map(|p| [p[0], p[1], p[2]]).collect();
    let file = File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut encoder = png::Encoder::new(BufWriter::new(file), picture.width, picture.height);
    encoder.set_color(png::ColorType::Rgb);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(png::Compression::Fast);
    let mut writer = encoder.write_header().map_err(|e| format!("{}: {e}", path.display()))?;
    writer.write_image_data(&rgb).map_err(|e| format!("{}: {e}", path.display()))
}

/// The edit's sound mixed into `path` as 16-bit stereo at [`AUDIO_RATE`],
/// as long as its `frames`; false, and nothing written, when nothing sounds.
fn write_mix(edit: &th_edit::document::WorkingEdit, sources: &Sources, fps: u32, frames: usize, path: &Path) -> Result<bool, String> {
    let fps = f64::from(fps);
    let mut voices = Vec::new();
    let mut place =
        |source: &Source, at: EditFrame, duration: EditFrame, source_in: EditFrame, gain_db: f64, fades: (EditFrame, EditFrame)| {
            let Some(i) = sources.index(source) else { return };
            let resolved = &sources.opened[i].resolved;
            let Some(sound) = resolved.sound.as_deref() else { return };
            // As the editor places it (`Editor::place_sounds`).
            let skip = (resolved.first - source_in).max(0);
            if skip >= duration {
                return;
            }
            voices.push(Voice {
                sound,
                at: (at + skip) as f64 / fps,
                length: (duration - skip) as f64 / fps,
                offset: (source_in + skip - resolved.first) as f64 / fps,
                gain: 10f64.powf(gain_db / 20.0) as f32,
                fade_in: (fades.0 - skip).max(0) as f64 / fps,
                fade_out: fades.1 as f64 / fps,
            });
        };
    for c in edit.video.iter().flat_map(|t| &t.clips) {
        if matches!(c.source, Source::Shot { .. }) {
            place(&c.source, c.at, c.duration, c.source_in, 0.0, (c.fade_in, c.fade_out));
        }
    }
    for c in edit.audio.iter().flat_map(|t| &t.clips) {
        place(&c.source, c.at, c.duration, c.source_in, c.gain_db, (c.fade_in, c.fade_out));
    }
    if voices.is_empty() {
        return Ok(false);
    }
    let total = (frames as f64 / fps * f64::from(AUDIO_RATE)).round() as u64;
    let file = File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut out = BufWriter::new(file);
    let fail = |e: std::io::Error| format!("{}: {e}", path.display());
    out.write_all(&wav_header(total, AUDIO_RATE)).map_err(fail)?;
    let mut chunk = vec![0f32; 2 * 4096];
    let mut from = 0u64;
    while from < total {
        let n = (total - from).min(4096) as usize;
        let samples = &mut chunk[..n * 2];
        mix(&voices, AUDIO_RATE, from, samples);
        let bytes: Vec<u8> = samples.iter().flat_map(|s| pcm16(*s).to_le_bytes()).collect();
        out.write_all(&bytes).map_err(fail)?;
        from += n as u64;
    }
    out.flush().map_err(fail)?;
    Ok(true)
}

/// A sample as 16-bit PCM, as WAV readers take it back (`/ 32768`).
pub fn pcm16(s: f32) -> i16 {
    (s * 32768.0).round().clamp(-32768.0, 32767.0) as i16
}

/// A 16-bit stereo PCM WAV header for `frames` sample frames at `rate`.
pub fn wav_header(frames: u64, rate: u32) -> Vec<u8> {
    let data = (frames * 4) as u32;
    let mut h = Vec::with_capacity(44);
    h.extend(b"RIFF");
    h.extend((36 + data).to_le_bytes());
    h.extend(b"WAVEfmt ");
    h.extend(16u32.to_le_bytes());
    h.extend(1u16.to_le_bytes()); // PCM
    h.extend(2u16.to_le_bytes()); // stereo
    h.extend(rate.to_le_bytes());
    h.extend((rate * 4).to_le_bytes());
    h.extend(4u16.to_le_bytes());
    h.extend(16u16.to_le_bytes());
    h.extend(b"data");
    h.extend(data.to_le_bytes());
    h
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// The mix's WAV reads back through th_media's own reader as what was
        /// written: its rate, and every sample to 16-bit precision (whose
        /// largest value is 32767/32768: a sample above it reads back as it).
        #[test]
        fn the_mix_reads_back(rate in prop::sample::select(vec![44_100u32, 48_000]), samples in prop::collection::vec(-1.0f32..1.0, 0..400)) {
            let frames = samples.len() / 2;
            let samples = &samples[..frames * 2];
            let mut bytes = wav_header(frames as u64, rate);
            bytes.extend(samples.iter().flat_map(|s| pcm16(*s).to_le_bytes()));
            let sound = th_media::sound::parse_wav(&bytes).unwrap();
            prop_assert_eq!(sound.rate, rate);
            prop_assert_eq!(sound.frames(), frames);
            for (got, want) in sound.samples.iter().zip(samples) {
                let want = want.min(32767.0 / 32768.0);
                prop_assert!((got - want).abs() <= 0.5 / 32768.0 + 1e-6, "{} read back as {}", want, got);
            }
        }
    }

    /// A picture written is the picture read back, as RGB.
    #[test]
    fn frames_are_written_as_rgb_pngs() {
        let dir = std::env::temp_dir().join(format!("th_render_png_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (w, h) = (5u32, 3u32);
        let rgba: Vec<u8> = (0..w * h).flat_map(|i| [i as u8, (i * 7) as u8, 200, 255]).collect();
        let picture = Frame { width: w, height: h, pixels: Arc::new(Pixels::Rgba8(rgba.clone())) };
        let path = frame_path(&dir, 12);
        assert!(path.ends_with("edit.0012.png"));
        write_png(&path, &picture).unwrap();
        let decoder = png::Decoder::new(std::io::BufReader::new(File::open(&path).unwrap()));
        let mut reader = decoder.read_info().unwrap();
        let mut buf = vec![0; reader.output_buffer_size().unwrap()];
        let info = reader.next_frame(&mut buf).unwrap();
        assert_eq!((info.width, info.height, info.color_type), (w, h, png::ColorType::Rgb));
        let want: Vec<u8> = rgba.as_chunks::<4>().0.iter().flat_map(|p| [p[0], p[1], p[2]]).collect();
        assert_eq!(&buf[..info.buffer_size()], &want[..]);
        std::fs::remove_dir_all(dir).ok();
    }
}
