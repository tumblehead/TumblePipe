//! A Y'CbCr movie decodes to the colours it was made from, so the chroma
//! planes are the right way round and read with the right matrix. The grey
//! ramps cannot show this: their chroma is neutral, so swapping Cb and Cr
//! changes nothing in them.
//!
//! `colour_cards_*` are seven 64x48 frames at 24 fps, frame `k` a flat card
//! of [`CARDS`]`[k]`, video range:
//! - `colour_cards_mpeg4.mp4`: 8-bit 4:2:0, untagged (an SD picture, so
//!   BT.601), copied plane by plane.
//! - `colour_cards_prores422.mov`: 10-bit 4:2:2 tagged BT.709, converted to
//!   4:2:0 through swscale.
//!
//! Made with ffmpeg from raw RGB frames (`scale=out_color_matrix=<m>:
//! out_range=tv`); MPEG-4 at `-q:v 1 -g 1`, ProRes `prores_ks -profile:v 2`.

use std::path::PathBuf;
use std::sync::Arc;

use proptest::prelude::*;
use th_media::composite::composite;
use th_media::{MovieDecoder, MovieIndex, Pixels, YuvMatrix};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join(name).to_string_lossy().into_owned()
}

/// The cards' colours, frame by frame: saturated enough that any mix-up of
/// Cb and Cr turns one into another colour, and a neutral grey last.
const CARDS: [[u8; 3]; 7] = [
    [0xC8, 0x3C, 0x28],
    [0x28, 0x50, 0xC8],
    [0x3C, 0xB4, 0x50],
    [0xB4, 0xB4, 0x28],
    [0x8C, 0x3C, 0xB4],
    [0x28, 0xB4, 0xB4],
    [0x80, 0x80, 0x80],
];

/// Each fixture, and the matrix its frames are tagged with.
const MOVIES: [(&str, YuvMatrix); 2] = [("colour_cards_mpeg4.mp4", YuvMatrix::Bt601), ("colour_cards_prores422.mov", YuvMatrix::Bt709)];

/// How far a code value may drift: 8-bit Y'CbCr rounding and the codecs'
/// loss on a flat card. A swapped chroma pair is off by more than 100.
const TOLERANCE: i32 = 6;

proptest! {
    #![proptest_config(ProptestConfig::with_cases(16))]

    /// Whichever frame is asked for, in whatever order, every pixel of it
    /// shows its card's colour, and the frame says the matrix it was made
    /// with.
    #[test]
    fn every_frame_shows_its_cards_colour(
        movie in prop::sample::select(MOVIES.to_vec()),
        picks in prop::collection::vec(0usize..CARDS.len(), 1..10),
    ) {
        let (name, matrix) = movie;
        let mut source = MovieDecoder::open(Arc::new(MovieIndex::read(&fixture(name)).unwrap()), 1).unwrap();
        prop_assert_eq!(source.index().frame_count(), CARDS.len());
        for k in picks {
            let frame = source.decode(k, |_, _| {}, || true).unwrap();
            let Pixels::Yuv420 { matrix: tagged, full_range, .. } = &*frame.pixels else {
                panic!("{name}: frame {k} is not Y'CbCr");
            };
            prop_assert_eq!(*tagged, matrix, "{}: frame {}", name, k);
            prop_assert!(!full_range, "{}: frame {} read as full range", name, k);
            let shown = composite(frame.width, frame.height, std::slice::from_ref(&frame)).unwrap();
            let Pixels::Rgba8(rgba) = &*shown.pixels else { unreachable!("a movie composite is RGBA8") };
            for (at, pixel) in rgba.as_chunks::<4>().0.iter().enumerate() {
                for c in 0..3 {
                    let off = (i32::from(pixel[c]) - i32::from(CARDS[k][c])).abs();
                    prop_assert!(
                        off <= TOLERANCE,
                        "{}: frame {} pixel {} is {:?}, the card {:?}", name, k, at, &pixel[..3], CARDS[k]
                    );
                }
            }
        }
    }
}
