//! Frame accuracy: whatever order frames are asked for in, `decode(i)` is
//! frame `i`.
//!
//! The fixtures are 64x64 clips from Houdini's hffmpeg with the encoder
//! TumblePipe's playblasts use (libopenh264), where frame `k` is a flat grey of
//! luma `5k + 8`, so a decoded frame names itself. `grey_ramp_single` has one
//! keyframe, like every playblast to date; `grey_ramp_gop5` one every 5
//! frames.

use std::path::PathBuf;
use std::sync::Arc;

use th_media::{Frame, MovieDecoder, MovieIndex, Pixels};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

/// Which ramp frame `frame` is, from its mean luma.
fn ramp_index(frame: &Frame) -> usize {
    let Pixels::Yuv420 { y, .. } = &*frame.pixels else { panic!("expected a Y'CbCr frame") };
    let mean = y.iter().map(|&v| f64::from(v)).sum::<f64>() / y.len() as f64;
    ((mean - 8.0) / 5.0).round() as usize
}

/// Frame orders a reviewer produces: forward, backward from the end, jumps
/// across keyframes both ways, repeats.
fn orders(count: usize) -> Vec<Vec<usize>> {
    let mut jumpy = Vec::new();
    let mut i = 7usize;
    for _ in 0..200 {
        jumpy.push(i);
        i = (i * 31 + 17) % count;
    }
    vec![(0..count).collect(), (0..count).rev().collect(), vec![40, 39, 38, 12, 13, 11, 47, 0, 47, 46, 3, 3, 4, 2, 25, 24, 26, 23], jumpy]
}

/// A decoder of the movie at `path`, on two threads.
fn open(path: &str) -> MovieDecoder {
    MovieDecoder::open(Arc::new(MovieIndex::read(path).unwrap()), 2).unwrap()
}

fn check(name: &str, expected_keyframes: usize) {
    let mut source = open(&fixture(name));
    let index = Arc::clone(source.index());
    assert_eq!(index.frame_count(), 48);
    assert_eq!(index.keyframes().len(), expected_keyframes);
    assert_eq!(index.rate(), (24, 1));
    for order in orders(48) {
        for &want in &order {
            let mut passed = Vec::new();
            let frame = source.decode(want, |at, f| passed.push((at, ramp_index(f))), || true).unwrap();
            assert_eq!(ramp_index(&frame), want, "{name}: decode({want}) in {order:?}");
            // Every frame handed over on the way is the frame it claims to be,
            // so buffering them is safe.
            for (at, seen) in passed {
                assert_eq!(at, seen, "{name}: frame passed as {at} on the way to {want}");
            }
        }
    }
}

#[test]
fn single_keyframe_movie_is_frame_accurate() {
    check("grey_ramp_single.mp4", 1);
}

#[test]
fn short_gop_movie_is_frame_accurate() {
    check("grey_ramp_gop5.mp4", 10);
}

#[test]
fn out_of_range_is_an_error() {
    let mut source = open(&fixture("grey_ramp_gop5.mp4"));
    assert!(source.decode(48, |_, _| {}, || true).is_err());
}

/// A decode no longer wanted stops before its next frame, and the decoder
/// decodes right after.
#[test]
fn a_decode_no_longer_wanted_stops() {
    let mut source = open(&fixture("grey_ramp_single.mp4"));
    let mut passed = 0;
    let asked = std::cell::Cell::new(0);
    let ten_frames = || {
        asked.set(asked.get() + 1);
        asked.get() <= 10
    };
    assert!(matches!(source.decode(40, |_, _| passed += 1, ten_frames), Err(th_media::Error::Cancelled { index: 40 })));
    assert_eq!(passed, 10);
    let frame = source.decode(40, |_, _| {}, || true).unwrap();
    assert_eq!(ramp_index(&frame), 40);
}

/// Against a real playblast: random access gives byte-identical frames to a
/// straight decode. Run by hand with `TH_PLAYER_TEST_MOVIE` set:
/// `cargo test -p th_media -- --ignored`.
#[test]
#[ignore = "needs TH_PLAYER_TEST_MOVIE, a real playblast"]
fn real_movie_random_access_matches_sequential() {
    let path = std::env::var("TH_PLAYER_TEST_MOVIE").expect("TH_PLAYER_TEST_MOVIE names a movie");
    let mut source = open(&path);
    let count = source.index().frame_count();
    let mut sequential = Vec::with_capacity(count);
    for i in 0..count {
        sequential.push(source.decode(i, |_, _| {}, || true).unwrap());
    }
    let mut i = count / 3;
    for _ in 0..60 {
        let frame = source.decode(i, |_, _| {}, || true).unwrap();
        let (Pixels::Yuv420 { y: a, u: au, v: av, .. }, Pixels::Yuv420 { y: b, u: bu, v: bv, .. }) =
            (&*frame.pixels, &*sequential[i].pixels)
        else {
            panic!("expected Y'CbCr frames")
        };
        assert!(a == b && au == bu && av == bv, "frame {i} differs from the sequential decode");
        i = (i * 37 + 11) % count;
    }
}
