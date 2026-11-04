//! What a stack of render layers is made from: movies with alpha decode to
//! straight RGBA code values, alpha kept; EXR layers decode with their own
//! alpha; and a stack opens only of layers that can be composited together.
//!
//! The movie fixture is ProRes 4444 with alpha, the alpha codec the
//! player's pinned FFmpeg decodes (its PNG decoder needs zlib, which the
//! build leaves out). 16x8, 3 frames, made with FFmpeg 8.1:
//!
//! ```text
//! ffmpeg -f lavfi -i "color=c=black:s=16x8:r=24,format=rgba,geq=r='255':g='N*100':b='X*16':a='if(lt(X\,8)\,0\,128)'" \
//!        -frames:v 3 -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le \
//!        -color_primaries bt709 -color_trc bt709 -colorspace bt709 rgba_prores4444.mov
//! ```
//!
//! so pixel (x, y) of frame n is R 255, G 100n, B 16x (to ProRes's
//! precision), A 0 left of x = 8 and 128 from there.

use std::path::PathBuf;
use std::sync::Arc;

use exr::prelude::*;
use th_media::{ExrSequence, ExrView, Media, MovieDecoder, MovieIndex, Pixels};

fn fixture(name: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name).to_string_lossy().into_owned()
}

fn decode_all(name: &str) -> Vec<Vec<u8>> {
    let index = Arc::new(MovieIndex::read(&fixture(name)).unwrap());
    assert_eq!((index.frame_count(), index.rate()), (3, (24, 1)));
    assert_eq!(index.keyframes().len(), 3, "every ProRes frame is a keyframe");
    let mut decoder = MovieDecoder::open(index, 2).unwrap();
    // Out of order, as a reviewer steps.
    let mut frames = vec![Vec::new(); 3];
    for n in [2, 0, 1, 2, 0] {
        let frame = decoder.decode(n, |_, _| {}, || true).unwrap();
        assert_eq!((frame.width, frame.height), (16, 8));
        let Pixels::Rgba8(data) = &*frame.pixels else { panic!("{name}: a movie with alpha decodes to RGBA") };
        frames[n] = data.clone();
    }
    frames
}

#[test]
fn a_movie_with_alpha_decodes_to_straight_rgba() {
    for (n, data) in decode_all("rgba_prores4444.mov").iter().enumerate() {
        for y in 0..8 {
            for x in 0..16 {
                let at = (y * 16 + x) * 4;
                let alpha = if x < 8 { 0 } else { 128 };
                assert_eq!(data[at + 3], alpha, "frame {n} alpha at ({x}, {y})");
                // ProRes is near-lossless and subsamples nothing here, but a
                // BT.601 decode of its BT.709 would be off by far more.
                let colour = [255, 100 * n, 16 * x];
                for c in 0..3 {
                    let off = (i32::from(data[at + c]) - colour[c] as i32).abs();
                    assert!(off <= 4, "frame {n} at ({x}, {y}): {:?} for {colour:?}", &data[at..at + 4]);
                }
            }
        }
    }
}

/// What a layered playblast actually is (tumblepipe.apps.mp4.from_png_alpha):
/// QuickTime Animation, argb, every frame a keyframe, written by Houdini's
/// hffmpeg from raw RGBA frames of the same pattern as the ProRes fixture:
///
/// ```text
/// hffmpeg -f rawvideo -pix_fmt rgba -s 16x8 -r 24 -i frames.rgba \
///         -frames:v 3 -c:v qtrle -g 1 -pix_fmt argb rgba_qtrle.mov
/// ```
///
/// Lossless, so every value is exact.
#[test]
fn a_layered_playblast_movie_decodes_exactly() {
    for (n, data) in decode_all("rgba_qtrle.mov").iter().enumerate() {
        for y in 0..8 {
            for x in 0..16 {
                let at = (y * 16 + x) * 4;
                let expected = [255, (100 * n) as u8, (16 * x) as u8, if x < 8 { 0 } else { 128 }];
                assert_eq!(&data[at..at + 4], &expected, "frame {n} at ({x}, {y})");
            }
        }
    }
}

/// An 4x2 EXR of RGBA (premultiplied) plus an `X` channel, tagged ACEScg.
fn write_exr(path: &std::path::Path, alpha: f32) {
    let mut attributes = LayerAttributes::default();
    attributes.other.insert(Text::from("oiio:ColorSpace"), AttributeValue::Text(Text::from("ACEScg")));
    let channels = SpecificChannels::rgba(move |_: Vec2<usize>| (0.5 * alpha, 0.25 * alpha, 0.125 * alpha, alpha));
    let layer = Layer::new((4, 2), attributes, Encoding::UNCOMPRESSED, channels);
    Image::from_layer(layer).write().to_file(path).unwrap();
}

#[test]
fn an_exr_layer_carries_its_alpha_only_when_asked() {
    let path = std::env::temp_dir().join(format!("th_media_stack_alpha_{}.exr", std::process::id()));
    write_exr(&path, 0.25);
    let sequence = ExrSequence::new(1, 1, vec![path.clone()]);
    let view = ExrSequence::default_view(&sequence.header().unwrap().layers);
    let alpha_of = |frame: th_media::Frame| {
        let Pixels::Rgba16f(data) = &*frame.pixels else { panic!("an EXR frame is RGBA halfs") };
        assert!(data.chunks(4).all(|p| p[0].to_f32() == 0.125), "the colour is as written");
        data.chunks(4).map(|p| p[3].to_f32()).collect::<Vec<_>>()
    };
    assert!(alpha_of(sequence.decode_while(0, &view, &|| true).unwrap()).iter().all(|&a| a == 1.0), "a take is opaque");
    assert!(alpha_of(sequence.decode_with_alpha_while(0, &view, &|| true).unwrap()).iter().all(|&a| a == 0.25), "a layer keeps its alpha");
    // One channel on its own stays opaque grey.
    let single = ExrView { layer: view.layer.clone(), channel: Some("A".into()) };
    assert!(alpha_of_grey(sequence.decode_with_alpha_while(0, &single, &|| true).unwrap()));
    std::fs::remove_file(path).ok();
}

fn alpha_of_grey(frame: th_media::Frame) -> bool {
    let Pixels::Rgba16f(data) = &*frame.pixels else { return false };
    data.chunks(4).all(|p| p[3].to_f32() == 1.0 && p[0].to_f32() == 0.25)
}

#[test]
fn a_stack_opens_only_of_layers_that_composite() {
    let movie = || Arc::new(Media::movie(&fixture("rgba_prores4444.mov")).unwrap());
    let path = std::env::temp_dir().join(format!("th_media_stack_open_{}.exr", std::process::id()));
    write_exr(&path, 1.0);
    let exr = Arc::new(Media::exr(ExrSequence::new(1001, 1, vec![path.clone(); 5]), (25, 1)).unwrap());

    let stack = Media::stack(vec![("default".into(), movie()), ("fx".into(), movie())]).unwrap();
    assert_eq!((stack.frame_count(), stack.rate(), stack.colour_space(), stack.is_exr()), (3, (24, 1), None, false));
    assert_eq!(stack.stack_layers(), ["default", "fx"]);
    assert!(stack.layers().is_empty(), "no view to pick");

    let renders = Media::stack(vec![("default".into(), Arc::clone(&exr)), ("fx".into(), Arc::clone(&exr))]).unwrap();
    assert_eq!((renders.frame_count(), renders.rate(), renders.colour_space(), renders.is_exr()), (5, (25, 1), Some("ACEScg"), true));
    assert_eq!(renders.frame_number(2), Some(1003));

    assert!(Media::stack(Vec::new()).is_err(), "no layers");
    assert!(Media::stack(vec![("default".into(), movie()), ("fx".into(), Arc::clone(&exr))]).is_err(), "mixed kinds");
    assert!(Media::stack(vec![("default".into(), Arc::new(stack))]).is_err(), "a stack in a stack");
    std::fs::remove_file(path).ok();
}
