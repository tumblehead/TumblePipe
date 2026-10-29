//! Frame accuracy: whatever order frames are asked for in, `decode(i)` is
//! frame `i`.
//!
//! The fixtures are 64x64 clips from Houdini's hffmpeg with the encoder
//! TumblePipe's playblasts use (libopenh264), where frame `k` is a flat grey of
//! luma `5k + 8`, so a decoded frame names itself. `grey_ramp_single` has one
//! keyframe, like every playblast to date; `grey_ramp_gop5` one every 5
//! frames.

use std::path::PathBuf;

use th_media::{Frame, MovieSource, Pixels};

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
    vec![
        (0..count).collect(),
        (0..count).rev().collect(),
        vec![40, 39, 38, 12, 13, 11, 47, 0, 47, 46, 3, 3, 4, 2, 25, 24, 26, 23],
        jumpy,
    ]
}

fn check(name: &str, expected_keyframes: usize) {
    let mut source = MovieSource::open(&fixture(name)).unwrap();
    assert_eq!(source.frame_count(), 48);
    assert_eq!(source.keyframes().len(), expected_keyframes);
    assert_eq!(source.rate(), (24, 1));
    for order in orders(48) {
        for &want in &order {
            let mut passed = Vec::new();
            let frame = source.decode(want, |at, f| passed.push((at, ramp_index(f)))).unwrap();
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
    let mut source = MovieSource::open(&fixture("grey_ramp_gop5.mp4")).unwrap();
    assert!(source.decode(48, |_, _| {}).is_err());
}

/// Against a real playblast (set `TH_PLAYER_TEST_MOVIE`): random access gives
/// byte-identical frames to a straight decode.
#[test]
fn real_movie_random_access_matches_sequential() {
    let Ok(path) = std::env::var("TH_PLAYER_TEST_MOVIE") else { return };
    let mut source = MovieSource::open(&path).unwrap();
    let count = source.frame_count();
    let mut sequential = Vec::with_capacity(count);
    for i in 0..count {
        sequential.push(source.decode(i, |_, _| {}).unwrap());
    }
    let mut i = count / 3;
    for _ in 0..60 {
        let frame = source.decode(i, |_, _| {}).unwrap();
        let (Pixels::Yuv420 { y: a, u: au, v: av, .. }, Pixels::Yuv420 { y: b, u: bu, v: bv, .. }) =
            (&*frame.pixels, &*sequential[i].pixels)
        else {
            panic!("expected Y'CbCr frames")
        };
        assert!(a == b && au == bu && av == bv, "frame {i} differs from the sequential decode");
        i = (i * 37 + 11) % count;
    }
}
