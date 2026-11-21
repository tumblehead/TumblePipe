//! Editorial media an edit imports, beyond playblasts: audio-only files give
//! their sound, movies in older and web codecs open frame-accurately, and a
//! codec the player has no decoder for is named in the refusal.
//!
//! The fixtures come from a full FFmpeg 8.1 build (libmp3lame, libvorbis,
//! libopus, libvpx, libaom): `tone*` a second of a 1 kHz sine (16 kHz mono
//! for FLAC and MS ADPCM WAV, 48 kHz stereo otherwise; the mp3 carries a JPEG
//! cover picture), `grey_ramp_*` twelve 64x64 frames at 24 fps where frame
//! `k` is a flat grey of luma `5k + 8`, one keyframe each.

use std::path::PathBuf;
use std::sync::Arc;

use proptest::prelude::*;
use th_media::sound::Sound;
use th_media::{Frame, Media, MovieDecoder, MovieIndex, Pixels};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

/// The sound is the fixture tone: `rate`, about a second (an encoder's
/// delay and padding aside), 1 kHz on the left across its middle half.
fn assert_tone(name: &str, rate: u32) {
    let sound = Sound::read(&fixture(name)).unwrap_or_else(|e| panic!("{name}: {e}")).unwrap_or_else(|| panic!("{name} has no sound"));
    assert_eq!(sound.rate, rate, "{name}");
    assert!((sound.seconds() - 1.0).abs() < 0.08, "{name}: {} s", sound.seconds());
    let left: Vec<f32> = sound.samples.chunks(2).map(|f| f[0]).collect();
    let quarter = rate as usize / 4;
    let middle = &left[quarter..3 * quarter];
    let rising = middle.windows(2).filter(|w| w[0] < 0.0 && w[1] >= 0.0).count();
    assert!((495..=505).contains(&rising), "{name}: {rising} cycles in half a second");
    assert!(middle.iter().any(|s| s.abs() > 0.05), "{name} is not silent");
}

#[test]
fn audio_only_files_give_their_sound() {
    assert_tone("tone_cover.mp3", 48000);
    assert_tone("tone.flac", 16000);
    assert_tone("tone_vorbis.ogg", 48000);
    assert_tone("tone_opus.ogg", 48000);
    // A WAV the Rust reader does not read (MS ADPCM) goes through FFmpeg.
    assert_tone("tone_adpcm.wav", 16000);
}

/// An mp3's cover picture is no movie: the file is sound only.
#[test]
fn a_cover_picture_is_no_movie() {
    let err = Media::movie(&fixture("tone_cover.mp3")).unwrap_err().to_string();
    assert!(err.contains("no decodable video stream (only a cover picture)"), "{err}");
    assert_eq!(th_media::undecodable(&fixture("tone_cover.mp3")), None);
}

/// Which ramp frame `frame` is, from its mean luma.
fn ramp_index(frame: &Frame) -> usize {
    let Pixels::Yuv420 { y, .. } = &*frame.pixels else { panic!("expected a Y'CbCr frame") };
    let mean = y.iter().map(|&v| f64::from(v)).sum::<f64>() / y.len() as f64;
    ((mean - 8.0) / 5.0).round() as usize
}

#[test]
fn older_and_web_codecs_are_frame_accurate() {
    // (name, keyframes): the MPEG-2 one a keyframe every 4 frames, in a
    // transport stream, whose seeks are guesses by bytes.
    for (name, keyframes) in [("grey_ramp_mpeg4.mp4", 1), ("grey_ramp_vp8.webm", 1), ("grey_ramp_mpeg2.ts", 3)] {
        let index = Arc::new(MovieIndex::read(&fixture(name)).unwrap_or_else(|e| panic!("{name}: {e}")));
        assert_eq!(index.frame_count(), 12, "{name}");
        assert_eq!(index.keyframes().len(), keyframes, "{name}");
        assert_eq!(index.rate(), (24, 1), "{name}");
        let mut source = MovieDecoder::open(index, 2).unwrap();
        // Backwards, forwards, then jumps across keyframes both ways.
        let jumps = [9, 2, 11, 5, 4, 0, 7, 3, 10, 1, 8, 6];
        for want in (0..12).rev().chain(0..12).chain(jumps) {
            let frame = source.decode(want, |_, _| {}, || true).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(ramp_index(&frame), want, "{name}: decode({want})");
        }
    }
}

/// AV1 has no decoder in the player's FFmpeg (its software decoders are
/// libraries the build leaves out): the refusal says so.
#[test]
fn a_codec_without_a_decoder_is_named() {
    let path = fixture("grey_ramp_av1.mp4");
    assert_eq!(th_media::undecodable(&path).as_deref(), Some("video codec av1 is not supported"));
    let err = Media::movie(&path).unwrap_err().to_string();
    assert!(err.ends_with("no decodable video stream (video codec av1 is not supported)"), "{err}");
    assert_eq!(Sound::read(&path).unwrap(), None, "it has no sound");
}

/// A codec two streams share is named once: `grey_ramp_av1_twice.mkv` is
/// the AV1 ramp's stream twice over (stream copied into Matroska).
#[test]
fn a_codec_of_two_streams_is_named_once() {
    let path = fixture("grey_ramp_av1_twice.mkv");
    assert_eq!(th_media::undecodable(&path).as_deref(), Some("video codec av1 is not supported"));
}

/// Both a picture and a sound the player cannot decode are named, the
/// picture's first: `grey_ramp_av1_dts.mkv` is the AV1 ramp with a DTS
/// tone (no DTS decoder in the player's FFmpeg).
#[test]
fn a_picture_and_a_sound_without_decoders_are_both_named() {
    let path = fixture("grey_ramp_av1_dts.mkv");
    assert_eq!(th_media::undecodable(&path).as_deref(), Some("video codec av1 is not supported, audio codec dts is not supported"));
}

/// A cover picture is no picture, even in a codec the player cannot
/// decode: `tone_png_cover.mp3` carries PNG album art (the build leaves
/// PNG out), and is still the tone, with nothing undecodable in it.
#[test]
fn an_undecodable_cover_is_not_named() {
    let path = fixture("tone_png_cover.mp3");
    assert_eq!(th_media::undecodable(&path), None);
    assert!(Sound::read(&path).unwrap().is_some(), "the tone");
}

/// A file FFmpeg cannot open says that, not only "Invalid data found".
#[test]
fn a_file_of_no_known_kind_says_so() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("notes.txt");
    std::fs::write(&path, b"these are not the sounds you are looking for").unwrap();
    let err = Sound::read(&path.to_string_lossy()).unwrap_err().to_string();
    assert!(err.contains("not a kind of file the player reads, or a damaged one"), "{err}");
}

proptest! {
    /// The refusal names every codec once, in order, singular for one and
    /// plural for more; with none there is nothing to say.
    #[test]
    fn the_refusal_names_each_codec(what in prop::sample::select(vec!["video", "audio"]), codecs in prop::collection::vec("[a-z][a-z0-9_]{1,8}", 0..4)) {
        let said = th_media::unsupported(what, &codecs);
        prop_assert_eq!(said.is_none(), codecs.is_empty());
        let Some(said) = said else { return Ok(()) };
        let (singular, plural) = (format!("{what} codec "), format!("{what} codecs "));
        let one = codecs.len() == 1;
        let ending = if one { " is not supported" } else { " are not supported" };
        prop_assert!(said.starts_with(&singular) || said.starts_with(&plural), "{}", said);
        prop_assert_eq!(said.starts_with(&plural), !one);
        prop_assert!(said.ends_with(ending), "{}", said);
        let listed = said.trim_start_matches(&plural).trim_start_matches(&singular);
        let listed = listed.trim_end_matches(" is not supported").trim_end_matches(" are not supported");
        let words: Vec<&str> = listed.split(", ").flat_map(|part| part.split(" and ")).collect();
        prop_assert_eq!(words, codecs.iter().map(String::as_str).collect::<Vec<_>>());
    }
}
