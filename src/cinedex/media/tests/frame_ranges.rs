//! A multi-range render's mp4 (#84) numbered by the frame list it records
//! (#86): its pictures are the listed frames back to back.
//!
//! The `grey_ramp_ranges*` fixtures are `grey_ramp_start1001.mp4`'s video
//! remuxed by Houdini's hffmpeg with `-map 0:v -c copy -movflags
//! use_metadata_tags -metadata frame_ranges=...`: `1001-1020 1040-1066 1100`
//! (its 48 frames), `_short` listing 16, `_bad` carrying `1001-1020 later`.

use std::path::PathBuf;
use std::sync::Arc;

use th_media::{Media, MovieDecoder, MovieIndex, Pixels, Under};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

/// The list numbers the pictures over the start frame it also records: the
/// jump from 1020 to 1040 is between indexes 19 and 20.
#[test]
fn a_recorded_frame_list_numbers_the_frames() {
    let media = Media::movie(&fixture("grey_ramp_ranges.mp4")).unwrap();
    assert_eq!(media.frame_count(), 48);
    let numbers: Vec<_> = [0, 19, 20, 46, 47].map(|i| media.frame_number(i)).into();
    assert_eq!(numbers, [Some(1001), Some(1020), Some(1040), Some(1066), Some(1100)]);
    assert_eq!(media.frame_ranges().map(|l| l.ranges().to_vec()), Some(vec![(1001, 1020), (1040, 1066), (1100, 1100)]));
}

/// Under a live playblast it shows a listed frame by its index, and no
/// picture in a gap (the live frame shows alone there).
#[test]
fn under_a_live_playblast_a_gap_shows_nothing() {
    let under = Under { media: Arc::new(Media::movie(&fixture("grey_ramp_ranges.mp4")).unwrap()), first: 1001 };
    assert_eq!(under.index(1001), Some(0));
    assert_eq!(under.index(1021), None);
    assert_eq!(under.index(1039), None);
    assert_eq!(under.index(1040), Some(20));
    assert_eq!(under.index(1100), Some(47));
    assert_eq!(under.index(1067), None);
}

/// The pictures decode as themselves: index `k` is a flat grey of luma
/// `5k + 8`, whatever frame number it carries.
#[test]
fn the_frames_decode_by_index() {
    let index = Arc::new(MovieIndex::read(&fixture("grey_ramp_ranges.mp4")).unwrap());
    let mut decoder = MovieDecoder::open(Arc::clone(&index), 2).unwrap();
    for want in [0usize, 19, 20, 47] {
        let frame = decoder.decode(want, |_, _| {}, || true).unwrap();
        let Pixels::Yuv420 { y, .. } = &*frame.pixels else { panic!("expected a Y'CbCr frame") };
        let mean = y.iter().map(|&v| f64::from(v)).sum::<f64>() / y.len() as f64;
        assert_eq!(((mean - 8.0) / 5.0).round() as usize, want);
    }
}

/// A list that is not the movie's frames (too few), or not a list, is an
/// error naming the tag, never a guessed numbering.
#[test]
fn a_frame_list_that_does_not_fit_is_an_error() {
    let short = MovieIndex::read(&fixture("grey_ramp_ranges_short.mp4")).unwrap_err().to_string();
    assert!(short.contains("lists 16 frames; it has 48"), "{short}");
    let bad = MovieIndex::read(&fixture("grey_ramp_ranges_bad.mp4")).unwrap_err().to_string();
    assert!(bad.contains("frame_ranges") && bad.contains("later"), "{bad}");
}
