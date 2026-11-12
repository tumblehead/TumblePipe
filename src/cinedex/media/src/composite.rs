//! Render layers composited into one picture, bottom layer first, each
//! "over" what is below it.
//!
//! - Movie layers (a layered playblast's QuickTime Animation movies) are display-referred
//!   code values with straight alpha: each is laid over the layers below in
//!   display space, `c * a + below * (1 - a)`, starting from opaque black, so
//!   the picture is opaque and shows like any playblast. A Y'CbCr layer (a
//!   movie without alpha) is opaque.
//! - EXR layers (a render's) are scene-linear and premultiplied: `top +
//!   (1 - top.a) * below`, starting from nothing, and the picture goes
//!   through the display transform like any render.
//!
//! The arithmetic is f32 throughout; a movie picture is rounded to code
//! values once, at the end.
//!
//! [`dissolve`] is the other blend: an edit's clips through each other's
//! fades. Their frames may be any size and kind, so each is fitted into the
//! picture (letterboxed) and taken to display code values first (a render
//! through its display transform), then laid over the ones below at its
//! opacity.

use std::sync::Arc;

use half::f16;

use crate::frame::{Frame, Pixels};
use crate::thumbnail::yuv_pixel;

/// Premultiplied `top` over `below`.
pub fn over_premultiplied(top: [f32; 4], below: [f32; 4]) -> [f32; 4] {
    let keep = 1.0 - top[3];
    [top[0] + keep * below[0], top[1] + keep * below[1], top[2] + keep * below[2], top[3] + keep * below[3]]
}

/// Straight-alpha `top` (colour and alpha in 0..1) over opaque `below`: the
/// opaque colour that shows.
pub fn over_straight(top: [f32; 4], below: [f32; 3]) -> [f32; 3] {
    let a = top[3];
    [top[0] * a + below[0] * (1.0 - a), top[1] * a + below[1] * (1.0 - a), top[2] * a + below[2] * (1.0 - a)]
}

fn code(value: f32) -> u8 {
    (value.clamp(0.0, 1.0) * 255.0).round() as u8
}

/// Pixel `at` of a movie layer as straight RGBA in 0..1.
fn display_pixel(frame: &Frame, at: usize) -> [f32; 4] {
    match &*frame.pixels {
        Pixels::Rgba8(data) => {
            let p = &data[at * 4..at * 4 + 4];
            [p[0], p[1], p[2], p[3]].map(|c| f32::from(c) / 255.0)
        }
        Pixels::Yuv420 { y, u, v, matrix, full_range } => {
            let w = frame.width as usize;
            let (row, col) = (at / w, at % w);
            let chroma = (row / 2) * w.div_ceil(2) + col / 2;
            let [r, g, b] = yuv_pixel(y[at], u[chroma], v[chroma], *matrix, *full_range);
            [r, g, b, 1.0]
        }
        Pixels::Rgba16f(_) => unreachable!("a movie stack holds no scene-linear layer"),
    }
}

/// `layers` (bottom first) composited into one `width` x `height` frame:
/// movie layers over opaque black, as display code values; EXR layers
/// premultiplied over nothing, scene-linear. No layers is black. A layer of
/// another size, or EXRs mixed with movies, is an error.
pub fn composite(width: u32, height: u32, layers: &[Frame]) -> Result<Frame, String> {
    if let Some(other) = layers.iter().find(|f| (f.width, f.height) != (width, height)) {
        return Err(format!("a layer is {}x{}, the stack {width}x{height}", other.width, other.height));
    }
    let linear = layers.iter().filter(|f| matches!(&*f.pixels, Pixels::Rgba16f(_))).count();
    if linear != 0 && linear != layers.len() {
        return Err("a stack mixes render and movie layers".into());
    }
    let pixels = width as usize * height as usize;
    let out = if linear > 0 {
        let data: Vec<&[f16]> = layers
            .iter()
            .map(|f| match &*f.pixels {
                Pixels::Rgba16f(d) => &d[..],
                _ => unreachable!(),
            })
            .collect();
        let mut out = vec![f16::ZERO; pixels * 4];
        for at in 0..pixels {
            let mut below = [0.0f32; 4];
            for layer in &data {
                let p = &layer[at * 4..at * 4 + 4];
                below = over_premultiplied([p[0], p[1], p[2], p[3]].map(f16::to_f32), below);
            }
            for (slot, value) in out[at * 4..at * 4 + 4].iter_mut().zip(below) {
                *slot = f16::from_f32(value);
            }
        }
        Pixels::Rgba16f(out)
    } else {
        let mut out = vec![255u8; pixels * 4];
        for at in 0..pixels {
            let mut below = [0.0f32; 3];
            for layer in layers {
                below = over_straight(display_pixel(layer, at), below);
            }
            for (slot, value) in out[at * 4..at * 4 + 3].iter_mut().zip(below) {
                *slot = code(value);
            }
        }
        Pixels::Rgba8(out)
    };
    Ok(Frame { width, height, pixels: Arc::new(out) })
}

/// Pixel `at` of any frame as straight RGBA: a movie's display code values
/// in 0..1, a render's scene-linear premultiplied values as they are.
fn pixel(frame: &Frame, at: usize) -> [f32; 4] {
    match &*frame.pixels {
        Pixels::Rgba16f(data) => {
            let p = &data[at * 4..at * 4 + 4];
            [p[0], p[1], p[2], p[3]].map(f16::to_f32)
        }
        _ => display_pixel(frame, at),
    }
}

/// Where a `frame`-sized picture sits in a `width` x `height` one, fitted
/// whole and centred: its left and top edges and its scale, in pixels.
pub fn fit_into(frame: (u32, u32), width: u32, height: u32) -> (f32, f32, f32) {
    let scale = (width as f32 / frame.0.max(1) as f32).min(height as f32 / frame.1.max(1) as f32);
    let (w, h) = (frame.0 as f32 * scale, frame.1 as f32 * scale);
    ((width as f32 - w) / 2.0, (height as f32 - h) / 2.0, scale)
}

/// `layers` (bottom first, each a frame and how much of it shows) blended
/// into one `width` x `height` picture of display code values: each frame
/// fitted into it whole and centred (black around it), a render's pixels
/// taken to display values by `display(layer, rgb)` (its display transform,
/// with exposure), then laid over the layers below at its opacity, starting
/// from opaque black. A movie's own alpha counts as well (straight); a
/// render's is in its premultiplied colour already. No layers is black.
pub fn dissolve(width: u32, height: u32, layers: &[(&Frame, f32)], display: impl Fn(usize, [f32; 3]) -> [f32; 3]) -> Frame {
    let places: Vec<(f32, f32, f32)> = layers.iter().map(|(f, _)| fit_into((f.width, f.height), width, height)).collect();
    let mut out = vec![255u8; width as usize * height as usize * 4];
    for y in 0..height {
        for x in 0..width {
            let mut below = [0.0f32; 3];
            for (i, (frame, opacity)) in layers.iter().enumerate() {
                let opacity = opacity.clamp(0.0, 1.0);
                let (left, top, scale) = places[i];
                // The frame pixel under this one's centre, if any.
                let fx = ((x as f32 + 0.5 - left) / scale).floor();
                let fy = ((y as f32 + 0.5 - top) / scale).floor();
                if fx < 0.0 || fy < 0.0 || fx >= frame.width as f32 || fy >= frame.height as f32 {
                    // Letterbox: black at this layer's opacity.
                    below = over_straight([0.0, 0.0, 0.0, opacity], below);
                    continue;
                }
                let at = fy as usize * frame.width as usize + fx as usize;
                let p = pixel(frame, at);
                let (rgb, alpha) = match &*frame.pixels {
                    // Premultiplied over black is its colour, whole.
                    Pixels::Rgba16f(_) => (display(i, [p[0], p[1], p[2]]), 1.0),
                    _ => ([p[0], p[1], p[2]], p[3]),
                };
                below = over_straight([rgb[0], rgb[1], rgb[2], alpha * opacity], below);
            }
            let at = (y as usize * width as usize + x as usize) * 4;
            for (slot, value) in out[at..at + 3].iter_mut().zip(below) {
                *slot = code(value);
            }
        }
    }
    Frame { width, height, pixels: Arc::new(Pixels::Rgba8(out)) }
}

/// Black in the form a stack of `linear` (EXR) or movie layers shows, for a
/// stack with every layer hidden.
pub fn black(width: u32, height: u32, linear: bool) -> Frame {
    let pixels = width as usize * height as usize;
    let pixels = if linear { Pixels::Rgba16f(vec![f16::ZERO; pixels * 4]) } else { Pixels::Rgba8([0, 0, 0, 255].repeat(pixels)) };
    Frame { width, height, pixels: Arc::new(pixels) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A premultiplied pixel: colour no brighter than its alpha allows.
    fn premultiplied() -> impl Strategy<Value = [f32; 4]> {
        (0f32..=1.0, prop::array::uniform3(0f32..=1.0)).prop_map(|(a, c)| [c[0] * a, c[1] * a, c[2] * a, a])
    }

    fn rgba8(width: u32, height: u32, pixel: [u8; 4]) -> Frame {
        Frame { width, height, pixels: Arc::new(Pixels::Rgba8(pixel.repeat(width as usize * height as usize))) }
    }

    fn rgba16f(pixel: [f32; 4]) -> Frame {
        Frame { width: 1, height: 1, pixels: Arc::new(Pixels::Rgba16f(pixel.map(f16::from_f32).to_vec())) }
    }

    fn close(a: [f32; 4], b: [f32; 4], tolerance: f32) -> bool {
        a.iter().zip(b).all(|(x, y)| (x - y).abs() <= tolerance)
    }

    proptest! {
        /// Premultiplied over is associative: a stack composites the same
        /// however its layers are grouped.
        #[test]
        fn premultiplied_over_is_associative(a in premultiplied(), b in premultiplied(), c in premultiplied()) {
            let left = over_premultiplied(a, over_premultiplied(b, c));
            let right = over_premultiplied(over_premultiplied(a, b), c);
            prop_assert!(close(left, right, 1e-5), "{left:?} vs {right:?}");
        }

        /// A fully transparent top leaves what is below; an opaque one
        /// hides it. In both spaces.
        #[test]
        fn transparent_tops_vanish_and_opaque_tops_hide(below in premultiplied(), colour in prop::array::uniform3(0f32..=1.0)) {
            prop_assert_eq!(over_premultiplied([0.0; 4], below), below);
            let opaque = [colour[0], colour[1], colour[2], 1.0];
            prop_assert_eq!(over_premultiplied(opaque, below), opaque);
            let shown = [below[0], below[1], below[2]];
            prop_assert_eq!(over_straight([colour[0], colour[1], colour[2], 0.0], shown), shown);
            prop_assert_eq!(over_straight(opaque, shown), colour);
        }

        /// Composited movie layers match the float reference (straight
        /// alpha over black, bottom first) within one code value, and come
        /// out opaque.
        #[test]
        fn movie_layers_match_a_float_reference(layers in prop::collection::vec(prop::array::uniform4(any::<u8>()), 0..5)) {
            let frames: Vec<Frame> = layers.iter().map(|&p| rgba8(2, 1, p)).collect();
            let picture = composite(2, 1, &frames).unwrap();
            let Pixels::Rgba8(data) = &*picture.pixels else { panic!("a movie stack is RGBA8") };
            let mut reference = [0f64; 3];
            for p in &layers {
                let a = f64::from(p[3]) / 255.0;
                for c in 0..3 {
                    reference[c] = f64::from(p[c]) / 255.0 * a + reference[c] * (1.0 - a);
                }
            }
            for pixel in data.chunks(4) {
                prop_assert_eq!(pixel[3], 255);
                for c in 0..3 {
                    prop_assert!((f64::from(pixel[c]) / 255.0 - reference[c]).abs() <= 1.0 / 255.0, "{pixel:?} vs {reference:?}");
                }
            }
        }

        /// Composited render layers are the premultiplied over of their
        /// pixels, to half precision.
        #[test]
        fn render_layers_compose_premultiplied(layers in prop::collection::vec(premultiplied(), 1..5)) {
            let frames: Vec<Frame> = layers.iter().map(|&p| rgba16f(p)).collect();
            let picture = composite(1, 1, &frames).unwrap();
            let Pixels::Rgba16f(data) = &*picture.pixels else { panic!("a render stack is RGBA halfs") };
            let halfs: Vec<[f32; 4]> = layers.iter().map(|p| p.map(|c| f16::from_f32(c).to_f32())).collect();
            let expected = halfs.iter().fold([0.0; 4], |below, &top| over_premultiplied(top, below));
            let got = [data[0], data[1], data[2], data[3]].map(f16::to_f32);
            prop_assert!(close(got, expected, 2e-3), "{got:?} vs {expected:?}");
        }
    }

    fn code_of(frame: &Frame, x: u32, y: u32) -> [u8; 4] {
        let Pixels::Rgba8(data) = &*frame.pixels else { panic!("a dissolve is RGBA8") };
        let at = ((y * frame.width + x) * 4) as usize;
        [data[at], data[at + 1], data[at + 2], data[at + 3]]
    }

    proptest! {
        /// A dissolve of solid frames is their straight blend, bottom first
        /// over black, within one code value; a whole top layer shows alone;
        /// it is opaque.
        #[test]
        fn a_dissolve_blends_at_each_layers_opacity(layers in prop::collection::vec((prop::array::uniform3(any::<u8>()), 0f32..=1.0), 0..4)) {
            let frames: Vec<Frame> = layers.iter().map(|(c, _)| rgba8(3, 2, [c[0], c[1], c[2], 255])).collect();
            let input: Vec<(&Frame, f32)> = frames.iter().zip(&layers).map(|(f, l)| (f, l.1)).collect();
            let picture = dissolve(3, 2, &input, |_, rgb| rgb);
            let mut reference = [0f64; 3];
            for (c, opacity) in &layers {
                let o = f64::from(*opacity);
                for k in 0..3 {
                    reference[k] = f64::from(c[k]) / 255.0 * o + reference[k] * (1.0 - o);
                }
            }
            for y in 0..2 {
                for x in 0..3 {
                    let p = code_of(&picture, x, y);
                    prop_assert_eq!(p[3], 255);
                    for k in 0..3 {
                        prop_assert!((f64::from(p[k]) / 255.0 - reference[k]).abs() <= 1.0 / 255.0 + 1e-6, "{p:?} vs {reference:?}");
                    }
                }
            }
            if let Some((c, _)) = layers.last().filter(|l| l.1 >= 1.0) {
                prop_assert_eq!(code_of(&picture, 1, 1), [c[0], c[1], c[2], 255]);
            }
        }
    }

    /// A frame of another shape is fitted whole and centred, black around
    /// it; a render goes through its display transform.
    #[test]
    fn a_dissolve_letterboxes_and_shows_renders_through_their_transform() {
        let wide = rgba8(4, 1, [200, 100, 50, 255]);
        let picture = dissolve(4, 3, &[(&wide, 1.0)], |_, rgb| rgb);
        for x in 0..4 {
            assert_eq!(code_of(&picture, x, 0), [0, 0, 0, 255], "above the frame");
            assert_eq!(code_of(&picture, x, 1), [200, 100, 50, 255], "the frame, centred");
            assert_eq!(code_of(&picture, x, 2), [0, 0, 0, 255], "below it");
        }
        let tall = rgba8(1, 2, [10, 20, 30, 255]);
        let picture = dissolve(3, 2, &[(&tall, 1.0)], |_, rgb| rgb);
        assert_eq!(code_of(&picture, 0, 0), [0, 0, 0, 255], "pillarbox");
        assert_eq!(code_of(&picture, 1, 0), [10, 20, 30, 255]);
        assert_eq!(code_of(&picture, 2, 1), [0, 0, 0, 255]);
        let render = rgba16f([0.25, 0.5, 1.0, 1.0]);
        let picture = dissolve(1, 1, &[(&render, 1.0)], |layer, rgb| {
            assert_eq!(layer, 0);
            rgb.map(|c| c / 2.0)
        });
        assert_eq!(code_of(&picture, 0, 0), [32, 64, 128, 255]);
    }

    #[test]
    fn mismatched_layers_are_errors() {
        assert!(composite(2, 1, &[rgba8(2, 1, [0; 4]), rgba8(1, 1, [0; 4])]).is_err(), "another size");
        assert!(composite(1, 1, &[rgba8(1, 1, [0; 4]), rgba16f([0.0; 4])]).is_err(), "mixed kinds");
        let Pixels::Rgba8(data) = &*composite(3, 2, &[]).unwrap().pixels else { panic!() };
        assert!(data.chunks(4).all(|p| p == [0, 0, 0, 255]), "no layers is black");
    }
}
