//! Properties of `JpgSequence`, a live playblast's frames read while husk
//! writes them: every frame on disk decodes to the picture written, at its
//! own frame number; a frame not written yet is pending, never a fault; a
//! frame husk is halfway through writing (a cut-off file) is a fault the
//! player retries once the frame is done, never a picture.
//!
//! The frames are real JPGs written with the `image` crate: one flat colour
//! each, which JPG keeps to within a few code values, so a decoded frame
//! says which one it is.

use std::path::{Path, PathBuf};

use image::codecs::jpeg::JpegEncoder;
use proptest::prelude::*;
use th_media::{Error, JpgSequence, Media, Pixels};

fn write_jpg(path: &Path, width: u32, height: u32, rgb: [u8; 3]) {
    let pixels: Vec<u8> = (0..width * height).flat_map(|_| rgb).collect();
    let mut bytes = Vec::new();
    JpegEncoder::new_with_quality(&mut bytes, 100).encode(&pixels, width, height, image::ExtendedColorType::Rgb8).expect("encoding a JPG");
    std::fs::write(path, bytes).unwrap();
}

/// Frame `number`'s colour: distinct for each of a sequence's frames.
fn colour(number: i64) -> [u8; 3] {
    let n = number.rem_euclid(40) as u8;
    [n * 6, 255 - n * 6, 128]
}

fn frame_path(dir: &Path, number: i64) -> PathBuf {
    dir.join(format!("frame.{number:04}.jpg"))
}

fn sequence(dir: &Path, first: i64, step: i64, count: usize) -> JpgSequence {
    JpgSequence::new(first, step, (0..count).map(|i| frame_path(dir, first + i as i64 * step)).collect())
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(24))]

    #[test]
    fn written_frames_decode_as_written_and_the_rest_are_pending(
        first in 990i64..1010,
        step in 1i64..3,
        count in 1usize..10,
        written in prop::collection::vec(any::<bool>(), 10),
        width in 8u32..40,
        height in 8u32..30,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let seq = sequence(dir.path(), first, step, count);
        for (i, _) in written.iter().enumerate().take(count).filter(|(_, w)| **w) {
            write_jpg(&frame_path(dir.path(), seq.frame_number(i)), width, height, colour(seq.frame_number(i)));
        }
        let media = Media::jpg(seq.clone(), (24, 1));
        prop_assert_eq!(media.frame_count(), count);
        prop_assert_eq!(media.colour_space(), None);
        prop_assert!(!media.is_exr());
        for (i, &was_written) in written.iter().enumerate().take(count) {
            prop_assert_eq!(media.frame_number(i), Some(first + i as i64 * step));
            match seq.decode(i) {
                Ok(frame) => {
                    prop_assert!(was_written, "frame {} decoded but was never written", i);
                    prop_assert_eq!((frame.width, frame.height), (width, height));
                    let Pixels::Rgba8(px) = &*frame.pixels else { panic!("not RGBA8") };
                    let want = colour(seq.frame_number(i));
                    for pixel in px.as_chunks::<4>().0 {
                        for (got, want) in pixel.iter().zip(want) {
                            prop_assert!((i16::from(*got) - i16::from(want)).abs() <= 3, "{:?} for {:?}", pixel, want);
                        }
                        prop_assert_eq!(pixel[3], 255);
                    }
                }
                Err(Error::Pending { index }) => prop_assert!(!was_written && index == i),
                Err(e) => prop_assert!(false, "frame {}: {}", i, e),
            }
        }
        let past = matches!(seq.decode(count), Err(Error::OutOfRange { index, count: c }) if index == count && c == count);
        prop_assert!(past, "a frame past the end is out of range");
    }

    #[test]
    fn a_frame_cut_off_mid_write_is_a_fault_not_a_picture(keep in 0.0f64..0.9) {
        let dir = tempfile::tempdir().unwrap();
        let path = frame_path(dir.path(), 1001);
        write_jpg(&path, 64, 48, colour(1001));
        let bytes = std::fs::read(&path).unwrap();
        std::fs::write(&path, &bytes[..(bytes.len() as f64 * keep) as usize]).unwrap();
        let result = sequence(dir.path(), 1001, 1, 1).decode(0);
        prop_assert!(matches!(result, Err(Error::Decode { index: 0, .. })), "{:?}", result.map(|f| (f.width, f.height)));
    }
}

/// A frame husk wrote with TumbleView (a 2048x858 playblast frame) reads at
/// its size, and every prefix of it is refused, as a frame husk is still
/// writing would be.
#[test]
fn a_real_husk_frame_reads_and_its_prefixes_do_not() {
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/tumbleview_husk.jpg");
    let bytes = std::fs::read(&fixture).unwrap();
    let frame = th_media::decode_jpg(&bytes).expect("husk's frame decodes");
    assert_eq!((frame.width, frame.height), (2048, 858));
    let Pixels::Rgba8(px) = &*frame.pixels else { panic!("not RGBA8") };
    assert!(px.as_chunks::<4>().0.iter().all(|p| p[3] == 255), "opaque");
    for cut in (0..bytes.len()).step_by(997) {
        assert!(th_media::decode_jpg(&bytes[..cut]).is_err(), "a frame cut at {cut} of {} bytes decoded", bytes.len());
    }
}

fn fixture_movie() -> std::sync::Arc<Media> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/grey_ramp_gop5.mp4");
    std::sync::Arc::new(Media::movie(&path.to_string_lossy()).expect("the fixture movie opens"))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(64))]

    /// Under a live playblast its take shows frame for frame by frame
    /// number: live frame n is the movie's index n - first wherever the
    /// movie has one, and nothing where it has none (before its first frame
    /// or past its last); live media without a take under shows nothing.
    #[test]
    fn the_take_under_a_live_playblast_shows_by_frame_number(
        first in 990i64..1010,
        step in 1i64..3,
        count in 1usize..40,
        under_first in 980i64..1020,
    ) {
        let movie = fixture_movie();
        let frames = movie.frame_count();
        let seq = sequence(Path::new("nowhere"), first, step, count);
        prop_assert!(Media::jpg(seq.clone(), (24, 1)).under(0).is_none());
        let under = th_media::Under { media: std::sync::Arc::clone(&movie), first: under_first };
        let media = Media::jpg_over(seq.clone(), (24, 1), under).unwrap();
        for i in 0..count {
            let n = seq.frame_number(i);
            let want = (n >= under_first && ((n - under_first) as usize) < frames).then(|| (n - under_first) as usize);
            let got = media.under(i);
            prop_assert_eq!(got.map(|(_, at)| at), want, "live frame {}", n);
            if let Some((m, _)) = got {
                prop_assert!(std::sync::Arc::ptr_eq(m, &movie));
            }
        }
    }
}

/// Only a movie shows under a live playblast: an EXR is scene-linear, a live
/// frame is not, and a live playblast under another is no take.
#[test]
fn only_a_movie_goes_under_a_live_playblast() {
    let seq = sequence(Path::new("nowhere"), 1001, 1, 3);
    let live = std::sync::Arc::new(Media::jpg(seq.clone(), (24, 1)));
    assert!(Media::jpg_over(seq.clone(), (24, 1), th_media::Under { media: live, first: 1001 }).is_err());
    let exr = th_media::ExrSequence::new(1001, 1, Vec::new());
    let header = th_media::ExrHeader { layers: Vec::new(), colour_space: Some("ACEScg".into()) };
    let exr = std::sync::Arc::new(Media::Exr { sequence: exr, header, colour_space: "ACEScg".into(), rate: (24, 1) });
    assert!(Media::jpg_over(seq.clone(), (24, 1), th_media::Under { media: exr, first: 1001 }).is_err());
    assert!(Media::jpg_over(seq, (24, 1), th_media::Under { media: fixture_movie(), first: 1001 }).is_ok());
}
