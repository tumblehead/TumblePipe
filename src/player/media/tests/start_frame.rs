//! A movie's start frame: the shot frame its first picture is, as TumblePipe
//! playblasts record it (`tumblepipe.apps.mp4.start_frame_args`: a
//! `start_frame` tag plus a timecode track).
//!
//! The `_start*` fixtures are the plain ones remuxed by Houdini's hffmpeg
//! with `-c copy -movflags use_metadata_tags -metadata start_frame=N
//! -timecode ...`, exactly as scripts/fix_playblast_start_frames.py rewrites
//! an older playblast; `grey_ramp_bad_start` carries `start_frame=soon`.

use std::path::PathBuf;
use std::sync::Arc;

use proptest::prelude::*;
use th_media::{Media, MovieDecoder, MovieIndex, Pixels, parse_start_frame};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

#[test]
fn a_recorded_start_frame_numbers_the_frames() {
    let media = Media::movie(&fixture("grey_ramp_start1001.mp4")).unwrap();
    assert_eq!(media.frame_count(), 48);
    assert_eq!(media.frame_number(0), Some(1001));
    assert_eq!(media.frame_number(47), Some(1048));
    let alpha = Media::movie(&fixture("rgba_qtrle_start995.mov")).unwrap();
    assert_eq!(alpha.frame_number(0), Some(995));
}

#[test]
fn a_movie_without_one_has_no_frame_numbers() {
    for name in ["grey_ramp_single.mp4", "grey_ramp_gop5.mp4", "rgba_qtrle.mov"] {
        let media = Media::movie(&fixture(name)).unwrap();
        assert_eq!(media.frame_number(0), None, "{name}");
    }
}

#[test]
fn a_start_frame_that_is_no_number_is_an_error() {
    let error = MovieIndex::read(&fixture("grey_ramp_bad_start.mp4")).unwrap_err().to_string();
    assert!(error.contains("soon"), "{error}");
}

/// The remux adds a timecode track beside the video; frames still decode
/// as themselves (frame `k` is a flat grey of luma `5k + 8`).
#[test]
fn the_timecode_track_leaves_the_frames_alone() {
    let index = Arc::new(MovieIndex::read(&fixture("grey_ramp_start1001.mp4")).unwrap());
    let mut decoder = MovieDecoder::open(Arc::clone(&index), 2).unwrap();
    for want in [0usize, 47, 12, 13, 3] {
        let frame = decoder.decode(want, |_, _| {}, || true).unwrap();
        let Pixels::Yuv420 { y, .. } = &*frame.pixels else { panic!("expected a Y'CbCr frame") };
        let mean = y.iter().map(|&v| f64::from(v)).sum::<f64>() / y.len() as f64;
        assert_eq!(((mean - 8.0) / 5.0).round() as usize, want);
    }
}

proptest! {
    /// Any frame number reads back as itself, surrounding blanks allowed.
    #[test]
    fn frame_numbers_parse(n in any::<i64>(), pad in "[ \t]{0,2}") {
        prop_assert_eq!(parse_start_frame(&format!("{pad}{n}{pad}")), Ok(n));
    }

    /// Anything with a character a number has not is refused.
    #[test]
    fn non_numbers_are_refused(text in "[0-9]{0,3}[a-z.:_][0-9a-z]{0,3}") {
        prop_assert!(parse_start_frame(&text).is_err());
    }
}
