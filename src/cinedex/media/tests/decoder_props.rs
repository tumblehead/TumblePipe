//! Whatever order a decoder is asked for frames in, each is byte for byte
//! the frame a forward pass decodes, in every pixel path: Y'CbCr 4:2:0
//! copied (H.264, MPEG-4, VP8, MPEG-2 in TS), RGBA through swscale
//! (QuickTime Animation) and Y'CbCrA converted here (ProRes 4444). And a
//! movie with no sound reads as none.

use std::path::PathBuf;
use std::sync::Arc;

use proptest::prelude::*;
use th_media::sound::Sound;
use th_media::{Frame, MovieDecoder, MovieIndex, Pixels};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join(name).to_string_lossy().into_owned()
}

const MOVIES: [&str; 6] =
    ["grey_ramp_gop5.mp4", "grey_ramp_mpeg4.mp4", "grey_ramp_vp8.webm", "grey_ramp_mpeg2.ts", "rgba_qtrle.mov", "rgba_prores4444.mov"];

fn bytes(frame: &Frame) -> (u32, u32, Vec<u8>) {
    let pixels = match &*frame.pixels {
        Pixels::Rgba8(rgba) => rgba.clone(),
        Pixels::Yuv420 { y, u, v, .. } => [y.as_slice(), u, v].concat(),
        other => panic!("a movie decoded to {other:?}"),
    };
    (frame.width, frame.height, pixels)
}

fn decoder(name: &str) -> MovieDecoder {
    MovieDecoder::open(Arc::new(MovieIndex::read(&fixture(name)).unwrap()), 2).unwrap()
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(24))]

    #[test]
    fn any_order_decodes_what_a_forward_pass_does(
        name in prop::sample::select(MOVIES.to_vec()),
        picks in prop::collection::vec(any::<prop::sample::Index>(), 1..30),
        threads in 1usize..4,
    ) {
        let mut forward = decoder(name);
        let count = forward.index().frame_count();
        let frames: Vec<_> = (0..count).map(|i| bytes(&forward.decode(i, |_, _| {}, || true).unwrap())).collect();
        let mut source = MovieDecoder::open(Arc::clone(forward.index()), threads).unwrap();
        for pick in picks {
            let want = pick.index(count);
            let mut passed = Vec::new();
            let frame = source.decode(want, |at, f| passed.push((at, bytes(f))), || true).unwrap();
            prop_assert!(bytes(&frame) == frames[want], "{}: decode({})", name, want);
            for (at, seen) in passed {
                prop_assert!(seen == frames[at], "{}: frame passed as {} on the way to {}", name, at, want);
            }
        }
    }
}

#[test]
fn a_movie_without_sound_has_none() {
    for name in MOVIES {
        assert_eq!(Sound::read(&fixture(name)).unwrap(), None, "{name}");
    }
}
