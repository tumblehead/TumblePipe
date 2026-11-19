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
    let placed: Vec<Placed> = layers.iter().map(|&(frame, opacity)| Placed { frame, opacity, placement: Placement::IDENTITY }).collect();
    dissolve_placed(width, height, &placed, display)
}

/// Where a layer's picture sits in the picture it is blended into, after
/// it is fitted into it whole and centred ([`fit_into`]): scaled and turned
/// about the picture's centre, then moved. Nothing here is in pixels, so a
/// small picture and a large one of the same shape show the same.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Placement {
    /// How far right it is moved, in picture heights.
    pub x: f32,
    /// How far up it is moved, in picture heights.
    pub y: f32,
    /// Its size, of the fitted picture.
    pub scale: f32,
    /// Degrees it is turned, anticlockwise.
    pub rotation: f32,
}

impl Placement {
    /// As fitted.
    pub const IDENTITY: Placement = Placement { x: 0.0, y: 0.0, scale: 1.0, rotation: 0.0 };

    #[allow(clippy::float_cmp)]
    pub fn is_identity(&self) -> bool {
        *self == Placement::IDENTITY
    }

    /// Where point `p` of the fitted picture (pixels from the top left of a
    /// `width` x `height` picture, y down) is shown.
    pub fn apply(&self, width: f32, height: f32, p: (f32, f32)) -> (f32, f32) {
        let (sin, cos) = self.rotation.to_radians().sin_cos();
        let (dx, dy) = ((p.0 - width / 2.0) * self.scale, (p.1 - height / 2.0) * self.scale);
        // Anticlockwise as seen, with y down.
        (width / 2.0 + self.x * height + dx * cos + dy * sin, height / 2.0 - self.y * height - dx * sin + dy * cos)
    }

    /// The point of the fitted picture shown at `q`: [`Placement::apply`]
    /// undone.
    pub fn invert(&self, width: f32, height: f32, q: (f32, f32)) -> (f32, f32) {
        let (sin, cos) = self.rotation.to_radians().sin_cos();
        let (dx, dy) = (q.0 - width / 2.0 - self.x * height, q.1 - height / 2.0 + self.y * height);
        (width / 2.0 + (dx * cos - dy * sin) / self.scale, height / 2.0 + (dx * sin + dy * cos) / self.scale)
    }
}

/// One layer of a [`dissolve_placed`]: a frame, how much of it shows, and
/// where.
#[derive(Debug, Clone, Copy)]
pub struct Placed<'a> {
    pub frame: &'a Frame,
    pub opacity: f32,
    pub placement: Placement,
}

/// A frame's pixels around the point (`fx`, `fy`) (in its own pixels, from
/// its top left), blended by how near each is: its straight RGBA as
/// [`pixel`] gives it. Points past an edge take the edge's pixels.
fn sample(frame: &Frame, fx: f32, fy: f32) -> [f32; 4] {
    let (w, h) = (frame.width as usize, frame.height as usize);
    // Pixel centres are at half pixels.
    let (x, y) = ((fx - 0.5).clamp(0.0, (w - 1) as f32), (fy - 0.5).clamp(0.0, (h - 1) as f32));
    let (x0, y0) = (x.floor() as usize, y.floor() as usize);
    let (x1, y1) = ((x0 + 1).min(w - 1), (y0 + 1).min(h - 1));
    let (tx, ty) = (x - x0 as f32, y - y0 as f32);
    if tx == 0.0 && ty == 0.0 {
        return pixel(frame, y0 * w + x0);
    }
    let (a, b, c, d) = (pixel(frame, y0 * w + x0), pixel(frame, y0 * w + x1), pixel(frame, y1 * w + x0), pixel(frame, y1 * w + x1));
    std::array::from_fn(|k| (a[k] * (1.0 - tx) + b[k] * tx) * (1.0 - ty) + (c[k] * (1.0 - tx) + d[k] * tx) * ty)
}

/// [`dissolve`] with each layer placed ([`Placement`]). A layer as fitted
/// is blended exactly as `dissolve` blends it, black around it. A layer
/// that is moved, scaled or turned shows only where its own picture lands
/// (its pixels blended by nearness, its edge softened over one pixel): the
/// layers below show around it, and black where there are none.
pub fn dissolve_placed(width: u32, height: u32, layers: &[Placed], display: impl Fn(usize, [f32; 3]) -> [f32; 3]) -> Frame {
    let places: Vec<(f32, f32, f32)> = layers.iter().map(|l| fit_into((l.frame.width, l.frame.height), width, height)).collect();
    let mut out = vec![255u8; width as usize * height as usize * 4];
    for y in 0..height {
        for x in 0..width {
            let mut below = [0.0f32; 3];
            for (i, &Placed { frame, opacity, placement }) in layers.iter().enumerate() {
                let opacity = opacity.clamp(0.0, 1.0);
                let (left, top, scale) = places[i];
                if !placement.is_identity() {
                    if frame.width == 0 || frame.height == 0 {
                        continue;
                    }
                    let p = placement.invert(width as f32, height as f32, (x as f32 + 0.5, y as f32 + 0.5));
                    let (fx, fy) = ((p.0 - left) / scale, (p.1 - top) / scale);
                    // How far inside the frame's edge this pixel's centre
                    // is, in picture pixels: the edge covers half of the
                    // pixel it runs through.
                    let inside = fx.min(frame.width as f32 - fx).min(fy).min(frame.height as f32 - fy) * scale * placement.scale;
                    let cover = (inside + 0.5).clamp(0.0, 1.0);
                    if cover <= 0.0 {
                        continue;
                    }
                    let p = sample(frame, fx, fy);
                    let (rgb, alpha) = match &*frame.pixels {
                        Pixels::Rgba16f(_) => (display(i, [p[0], p[1], p[2]]), 1.0),
                        _ => ([p[0], p[1], p[2]], p[3]),
                    };
                    below = over_straight([rgb[0], rgb[1], rgb[2], alpha * opacity * cover], below);
                    continue;
                }
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

    /// A placement within what an edit allows.
    fn placements() -> impl Strategy<Value = Placement> {
        (-2f32..2.0, -2f32..2.0, 0.05f32..8.0, -720f32..720.0).prop_map(|(x, y, scale, rotation)| Placement { x, y, scale, rotation })
    }

    /// A frame of `pixels`, row by row.
    fn picture_of(width: u32, height: u32, pixels: &[[u8; 3]]) -> Frame {
        let data = pixels.iter().flat_map(|p| [p[0], p[1], p[2], 255]).collect();
        Frame { width, height, pixels: Arc::new(Pixels::Rgba8(data)) }
    }

    fn near(a: (f32, f32), b: (f32, f32), tolerance: f32) -> bool {
        (a.0 - b.0).abs() <= tolerance && (a.1 - b.1).abs() <= tolerance
    }

    proptest! {
        /// A placement scales and turns about the picture's centre, then
        /// moves: against the same sum worked in f64, step by step. Undone
        /// it gives the point back. And it knows no resolution: in a
        /// picture `k` times the size the same point lands `k` times as far.
        #[test]
        fn a_placement_scales_turns_then_moves(
            placement in placements(),
            size in (2u32..4000, 2u32..4000),
            at in (0f32..1.0, 0f32..1.0),
            k in prop::sample::select(vec![0.25f32, 0.5, 2.0, 3.0]),
        ) {
            let (w, h) = (size.0 as f32, size.1 as f32);
            let p = (at.0 * w, at.1 * h);
            let shown = placement.apply(w, h, p);
            let (dx, dy) = (f64::from(p.0 - w / 2.0) * f64::from(placement.scale), f64::from(p.1 - h / 2.0) * f64::from(placement.scale));
            let turn = f64::from(placement.rotation).to_radians();
            // Anticlockwise on a screen whose y runs down.
            let (rx, ry) = (dx * turn.cos() + dy * turn.sin(), dy * turn.cos() - dx * turn.sin());
            let want = (f64::from(w) / 2.0 + rx + f64::from(placement.x) * f64::from(h), f64::from(h) / 2.0 + ry - f64::from(placement.y) * f64::from(h));
            let slack = 1e-3 * (w + h) * placement.scale.max(1.0);
            prop_assert!(near(shown, (want.0 as f32, want.1 as f32), slack), "{shown:?} against {want:?}");
            let back = placement.invert(w, h, shown);
            prop_assert!(near(back, p, slack / placement.scale.min(1.0)), "{back:?} against {p:?}");
            let larger = placement.apply(w * k, h * k, (p.0 * k, p.1 * k));
            prop_assert!(near(larger, (shown.0 * k, shown.1 * k), slack * k.max(1.0)), "{larger:?} against {shown:?} times {k}");
        }

        /// A clip moved by whole pixels shows each of its pixels exactly
        /// that far right and up, and what is below around it (black when
        /// nothing is): the picture is not black behind a moved clip.
        #[test]
        fn a_moved_layer_moves_its_pixels_and_shows_what_is_below(
            size in (1u32..7, 1u32..7),
            seed in prop::collection::vec(prop::array::uniform3(any::<u8>()), 36),
            by in (-6i32..7, -6i32..7),
            below in prop::option::of(prop::array::uniform3(any::<u8>())),
        ) {
            prop_assume!(by != (0, 0));
            let (w, h) = size;
            let top = picture_of(w, h, &seed[..(w * h) as usize]);
            let under = below.map(|c| rgba8(w, h, [c[0], c[1], c[2], 255]));
            let placement = Placement { x: by.0 as f32 / h as f32, y: by.1 as f32 / h as f32, ..Placement::IDENTITY };
            let mut layers = Vec::new();
            if let Some(under) = &under {
                layers.push(Placed { frame: under, opacity: 1.0, placement: Placement::IDENTITY });
            }
            layers.push(Placed { frame: &top, opacity: 1.0, placement });
            let picture = dissolve_placed(w, h, &layers, |_, rgb| rgb);
            for y in 0..h as i32 {
                for x in 0..w as i32 {
                    // Right is +x; up is +y, so the pixel came from below.
                    let (fx, fy) = (x - by.0, y + by.1);
                    let want = if (0..w as i32).contains(&fx) && (0..h as i32).contains(&fy) {
                        code_of(&top, fx as u32, fy as u32)
                    } else {
                        below.map_or([0, 0, 0, 255], |c| [c[0], c[1], c[2], 255])
                    };
                    prop_assert_eq!(code_of(&picture, x as u32, y as u32), want, "at {},{} moved by {:?}", x, y, by);
                }
            }
        }

        /// Half a turn shows the frame upside down and back to front, a
        /// quarter turn anticlockwise puts a square frame's right edge at
        /// the top, pixel for pixel.
        #[test]
        fn a_turned_layer_turns_its_pixels(side in 1u32..6, wide in 1u32..6, seed in prop::collection::vec(prop::array::uniform3(any::<u8>()), 36)) {
            let frame = picture_of(wide, side, &seed[..(wide * side) as usize]);
            let half = Placed { frame: &frame, opacity: 1.0, placement: Placement { rotation: 180.0, ..Placement::IDENTITY } };
            let picture = dissolve_placed(wide, side, &[half], |_, rgb| rgb);
            for y in 0..side {
                for x in 0..wide {
                    prop_assert_eq!(code_of(&picture, x, y), code_of(&frame, wide - 1 - x, side - 1 - y));
                }
            }
            let square = picture_of(side, side, &seed[..(side * side) as usize]);
            let quarter = Placed { frame: &square, opacity: 1.0, placement: Placement { rotation: 90.0, ..Placement::IDENTITY } };
            let picture = dissolve_placed(side, side, &[quarter], |_, rgb| rgb);
            for y in 0..side {
                for x in 0..side {
                    // Anticlockwise: what was at the right is now at the top.
                    prop_assert_eq!(code_of(&picture, x, y), code_of(&square, side - 1 - y, x));
                }
            }
        }

        /// A proxy and the full frame agree: the same picture comes of a
        /// frame and of one twice or three times its size, whatever the
        /// placement. And a clip scaled down shows what is below around it
        /// and itself at the picture's centre.
        #[test]
        fn a_proxy_and_the_full_frame_are_placed_alike(
            placement in placements(),
            colours in (prop::array::uniform3(any::<u8>()), prop::array::uniform3(any::<u8>())),
            frame in (1u32..9, 1u32..9),
            k in 2u32..4,
            opacity in prop::sample::select(vec![1.0f32, 0.5]),
        ) {
            let (top, under) = colours;
            let under = rgba8(16, 9, [under[0], under[1], under[2], 255]);
            let of = |w: u32, h: u32, placement: Placement| {
                let frame = rgba8(w, h, [top[0], top[1], top[2], 255]);
                let layers = [Placed { frame: &under, opacity: 1.0, placement: Placement::IDENTITY }, Placed { frame: &frame, opacity, placement }];
                dissolve_placed(16, 9, &layers, |_, rgb| rgb)
            };
            let (small, large) = (of(frame.0, frame.1, placement), of(frame.0 * k, frame.1 * k, placement));
            for y in 0..9 {
                for x in 0..16 {
                    let (a, b) = (code_of(&small, x, y), code_of(&large, x, y));
                    prop_assert!(a.iter().zip(b).all(|(a, b)| a.abs_diff(b) <= 1), "{a:?} against {b:?} at {x},{y}");
                }
            }
            let shrunk = of(16, 9, Placement { scale: 0.5, ..Placement::IDENTITY });
            prop_assert_eq!(code_of(&shrunk, 0, 0), code_of(&under, 0, 0));
            prop_assert_eq!(code_of(&shrunk, 15, 8), code_of(&under, 0, 0));
            if opacity >= 1.0 {
                prop_assert_eq!(code_of(&shrunk, 8, 4), [top[0], top[1], top[2], 255]);
            }
        }
    }

    /// The dissolve as it was before a layer could be placed (Cinedex up
    /// to 1.112.0), kept as it was written: what layers as fitted must
    /// still blend as, to the code value.
    fn dissolve_before_placements(
        width: u32,
        height: u32,
        layers: &[(&Frame, f32)],
        display: impl Fn(usize, [f32; 3]) -> [f32; 3],
    ) -> Frame {
        let places: Vec<(f32, f32, f32)> = layers.iter().map(|(f, _)| fit_into((f.width, f.height), width, height)).collect();
        let mut out = vec![255u8; width as usize * height as usize * 4];
        for y in 0..height {
            for x in 0..width {
                let mut below = [0.0f32; 3];
                for (i, (frame, opacity)) in layers.iter().enumerate() {
                    let opacity = opacity.clamp(0.0, 1.0);
                    let (left, top, scale) = places[i];
                    let fx = ((x as f32 + 0.5 - left) / scale).floor();
                    let fy = ((y as f32 + 0.5 - top) / scale).floor();
                    if fx < 0.0 || fy < 0.0 || fx >= frame.width as f32 || fy >= frame.height as f32 {
                        below = over_straight([0.0, 0.0, 0.0, opacity], below);
                        continue;
                    }
                    let at = fy as usize * frame.width as usize + fx as usize;
                    let p = pixel(frame, at);
                    let (rgb, alpha) = match &*frame.pixels {
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

    /// A frame of `seed`'s pixels, over and over: a movie's with their
    /// alpha, or (`render`) scene-linear halfs of them.
    fn seeded(width: u32, height: u32, seed: &[[u8; 4]], render: bool) -> Frame {
        let pixels = (0..(width * height) as usize).map(|i| seed[i % seed.len()]);
        let pixels = if render {
            Pixels::Rgba16f(pixels.flat_map(|p| p.map(|c| f16::from_f32(f32::from(c) / 200.0))).collect())
        } else {
            Pixels::Rgba8(pixels.flatten().collect())
        };
        Frame { width, height, pixels: Arc::new(pixels) }
    }

    /// One frame of a blend: its size, whether it is a render, how much of
    /// it shows.
    fn any_layer() -> impl Strategy<Value = ((u32, u32), bool, f32)> {
        ((1u32..12, 1u32..12), any::<bool>(), prop_oneof![2 => Just(1.0f32), 1 => Just(0.0f32), 3 => 0f32..=1.0])
    }

    /// The display transform of the tests: each layer its own.
    fn shown(layer: usize, rgb: [f32; 3]) -> [f32; 3] {
        rgb.map(|c| 1.0 - c / (layer as f32 + 2.0))
    }

    /// A placement anywhere an edit allows: moved up to 10 picture heights,
    /// scaled 0.01 to 100, turned up to ten turns; the limits themselves
    /// now and then.
    fn any_placement() -> impl Strategy<Value = Placement> {
        let moved = || prop_oneof![4 => -0.6f32..0.6, 1 => -10f32..=10.0, 1 => prop::sample::select(vec![-10f32, 10.0, 0.0])];
        let scale = prop_oneof![3 => 0.2f32..3.0, 1 => 0.01f32..=100.0, 1 => prop::sample::select(vec![0.01f32, 100.0, 1.0])];
        let turn = prop_oneof![3 => -180f32..180.0, 1 => -3600f32..=3600.0, 1 => prop::sample::select(vec![-3600f32, 3600.0, 90.0, 0.0])];
        (moved(), moved(), scale, turn).prop_map(|(x, y, scale, rotation)| Placement { x, y, scale, rotation })
    }

    proptest! {
        /// Layers as fitted blend to the very code values they did before
        /// a layer could be placed: frames of any size and kind (movies
        /// with their alpha, renders through their display transform) in a
        /// picture of any size, at any opacities, letterboxed or not. Both
        /// through `dissolve` and through `dissolve_placed` with identity
        /// placements.
        #[test]
        fn layers_as_fitted_blend_as_they_did_before_placements(
            size in (1u32..12, 1u32..12),
            layers in prop::collection::vec(any_layer(), 0..4),
            seed in prop::collection::vec(prop::array::uniform4(any::<u8>()), 1..7),
        ) {
            let frames: Vec<Frame> = layers.iter().map(|&((w, h), render, _)| seeded(w, h, &seed, render)).collect();
            let input: Vec<(&Frame, f32)> = frames.iter().zip(&layers).map(|(f, l)| (f, l.2)).collect();
            let placed: Vec<Placed> = input.iter().map(|&(frame, opacity)| Placed { frame, opacity, placement: Placement::IDENTITY }).collect();
            let before = dissolve_before_placements(size.0, size.1, &input, shown);
            let Pixels::Rgba8(want) = &*before.pixels else { panic!("a dissolve is RGBA8") };
            for now in [dissolve(size.0, size.1, &input, shown), dissolve_placed(size.0, size.1, &placed, shown)] {
                prop_assert_eq!((now.width, now.height), size);
                let Pixels::Rgba8(got) = &*now.pixels else { panic!("a dissolve is RGBA8") };
                prop_assert_eq!(got, want);
            }
        }

        /// A placed layer blends as a fitted one does: at its opacity times
        /// a movie's own alpha, a render through its display transform
        /// (its alpha in its colour already). Moved by whole pixels, each
        /// pixel it covers is that blend over what is below, and the rest
        /// is what is below, untouched.
        #[test]
        fn a_placed_layer_blends_at_its_opacity_through_its_display_transform(
            size in (1u32..7, 1u32..7),
            by in (-3i32..4, -3i32..4),
            top in prop::array::uniform4(any::<u8>()),
            under in prop::array::uniform3(any::<u8>()),
            opacity in prop_oneof![1 => Just(1.0f32), 3 => 0f32..=1.0],
            render in any::<bool>(),
        ) {
            prop_assume!(by != (0, 0));
            let (w, h) = size;
            let frame = seeded(w, h, &[top], render);
            let below = rgba8(w, h, [under[0], under[1], under[2], 255]);
            let placement = Placement { x: by.0 as f32 / h as f32, y: by.1 as f32 / h as f32, ..Placement::IDENTITY };
            let layers = [Placed { frame: &below, opacity: 1.0, placement: Placement::IDENTITY }, Placed { frame: &frame, opacity, placement }];
            let picture = dissolve_placed(w, h, &layers, shown);
            // The blend, worked in f64 from the pixel's own numbers.
            let (colour, alpha): ([f64; 3], f64) = if render {
                let linear = top.map(|c| f64::from(f16::from_f32(f32::from(c) / 200.0).to_f32()));
                ([0, 1, 2].map(|k| 1.0 - linear[k] / 3.0), 1.0)
            } else {
                ([0, 1, 2].map(|k| f64::from(top[k]) / 255.0), f64::from(top[3]) / 255.0)
            };
            let a = alpha * f64::from(opacity);
            for y in 0..h as i32 {
                for x in 0..w as i32 {
                    let covered = (0..w as i32).contains(&(x - by.0)) && (0..h as i32).contains(&(y + by.1));
                    let got = code_of(&picture, x as u32, y as u32);
                    prop_assert_eq!(got[3], 255);
                    for k in 0..3 {
                        let want = if covered { colour[k].clamp(0.0, 1.0) * a + f64::from(under[k]) / 255.0 * (1.0 - a) } else { f64::from(under[k]) / 255.0 };
                        prop_assert!((f64::from(got[k]) - want * 255.0).abs() <= 1.0, "{:?} at {},{}: channel {} is to be {}", got, x, y, k, want * 255.0);
                    }
                }
            }
        }

        /// Wherever an edit may put a clip (10 picture heights either way,
        /// a hundredth to a hundred times its size, ten turns), a placed
        /// frame of any size is blended without reading outside it, and
        /// shows exactly where its picture lands: a pixel whose centre is
        /// most of a pixel inside the fitted picture, scaled and turned
        /// about the centre and then moved, is the clip's colour whole; one
        /// most of a pixel outside is what is below, untouched; the edge
        /// between is a mix of the two. The landing is worked here in f64,
        /// forwards from the words: anticlockwise on a screen whose y runs
        /// down takes the point right of the centre up.
        #[test]
        fn a_placed_layer_shows_exactly_where_its_picture_lands(
            size in (2u32..24, 2u32..24),
            frame in (1u32..16, 1u32..16),
            placement in any_placement(),
            colours in (prop::array::uniform3(any::<u8>()), prop::array::uniform3(any::<u8>())),
        ) {
            let (w, h) = size;
            let (top, under) = colours;
            let clip = rgba8(frame.0, frame.1, [top[0], top[1], top[2], 255]);
            let below = rgba8(w, h, [under[0], under[1], under[2], 255]);
            let layers = [Placed { frame: &below, opacity: 1.0, placement: Placement::IDENTITY }, Placed { frame: &clip, opacity: 1.0, placement }];
            let picture = dissolve_placed(w, h, &layers, |_, rgb| rgb);
            let (wide, tall) = (f64::from(w), f64::from(h));
            let fit = (wide / f64::from(frame.0)).min(tall / f64::from(frame.1));
            let half = (f64::from(frame.0) * fit / 2.0, f64::from(frame.1) * fit / 2.0);
            let (scale, turn) = (f64::from(placement.scale), f64::from(placement.rotation).to_radians());
            // Where the fitted picture's own right and down point once turned.
            let (right, down) = ((turn.cos(), -turn.sin()), (turn.sin(), turn.cos()));
            let centre = (wide / 2.0 + f64::from(placement.x) * tall, tall / 2.0 - f64::from(placement.y) * tall);
            for y in 0..h {
                for x in 0..w {
                    let from = (f64::from(x) + 0.5 - centre.0, f64::from(y) + 0.5 - centre.1);
                    // How far along the picture's own right and down this
                    // pixel is from its centre, and so how far inside it.
                    let along = (from.0 * right.0 + from.1 * right.1, from.0 * down.0 + from.1 * down.1);
                    let inside = (half.0 * scale - along.0.abs()).min(half.1 * scale - along.1.abs());
                    let got = code_of(&picture, x, y);
                    prop_assert_eq!(got[3], 255);
                    if inside >= 0.75 {
                        prop_assert_eq!(got, [top[0], top[1], top[2], 255], "{},{} is {} inside {:?}", x, y, inside, placement);
                    } else if inside <= -0.75 {
                        prop_assert_eq!(got, [under[0], under[1], under[2], 255], "{},{} is {} outside {:?}", x, y, -inside, placement);
                    } else {
                        for k in 0..3 {
                            prop_assert!((top[k].min(under[k])..=top[k].max(under[k])).contains(&got[k]), "{:?} at {},{} is no mix", got, x, y);
                        }
                    }
                }
            }
        }

        /// A frame of no size, placed anywhere, shows nothing: what is
        /// below it is the picture.
        #[test]
        fn a_placed_frame_of_no_size_shows_what_is_below(size in (1u32..8, 1u32..8), placement in any_placement(), under in prop::array::uniform3(any::<u8>())) {
            prop_assume!(!placement.is_identity());
            let below = rgba8(size.0, size.1, [under[0], under[1], under[2], 255]);
            for (w, h) in [(0, 0), (0, 3), (3, 0)] {
                let empty = Frame { width: w, height: h, pixels: Arc::new(Pixels::Rgba8(Vec::new())) };
                let layers = [Placed { frame: &below, opacity: 1.0, placement: Placement::IDENTITY }, Placed { frame: &empty, opacity: 1.0, placement }];
                let picture = dissolve_placed(size.0, size.1, &layers, |_, rgb| rgb);
                let (Pixels::Rgba8(got), Pixels::Rgba8(want)) = (&*picture.pixels, &*below.pixels) else { panic!("a dissolve is RGBA8") };
                prop_assert_eq!(got, want);
            }
        }
    }

    /// A smooth picture of a lit disc at the centre of a `side` square,
    /// black around it, no two quarters alike.
    fn disc(side: u32) -> Frame {
        let mid = side as f32 / 2.0;
        let mut data = Vec::with_capacity((side * side * 4) as usize);
        for y in 0..side {
            for x in 0..side {
                let (dx, dy) = (x as f32 + 0.5 - mid, y as f32 + 0.5 - mid);
                let r = (dx * dx + dy * dy).sqrt() / (0.3 * side as f32);
                let lit = if r < 1.0 { 0.5 * (1.0 + (std::f32::consts::PI * r).cos()) } else { 0.0 };
                let colour = [lit * (0.5 + 0.5 * x as f32 / side as f32), lit * (1.0 - 0.5 * y as f32 / side as f32), lit];
                data.extend(colour.map(code));
                data.push(255);
            }
        }
        Frame { width: side, height: side, pixels: Arc::new(Pixels::Rgba8(data)) }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(48))]

        /// Scaled by `s` and turned by `a`, and the picture that makes then
        /// scaled by `1/s` and turned by `-a`, is the picture it was: a
        /// clip is scaled and turned about the picture's centre and
        /// nowhere else. (Each pass blends neighbouring pixels, so to
        /// within a few code values of a smooth picture.)
        #[test]
        fn scaled_and_turned_and_back_is_the_picture_it_was(
            scale in prop_oneof![1 => Just(1.0f32), 3 => 0.6f32..1.5],
            turn in prop_oneof![1 => Just(0.0f32), 3 => -3600f32..=3600.0],
        ) {
            prop_assume!(scale != 1.0 || turn != 0.0);
            let side = 48;
            let picture = disc(side);
            let there = Placed { frame: &picture, opacity: 1.0, placement: Placement { scale, rotation: turn, ..Placement::IDENTITY } };
            let placed = dissolve_placed(side, side, &[there], |_, rgb| rgb);
            let back = Placed { frame: &placed, opacity: 1.0, placement: Placement { scale: 1.0 / scale, rotation: -turn, ..Placement::IDENTITY } };
            let again = dissolve_placed(side, side, &[back], |_, rgb| rgb);
            let mut moved = false;
            for y in 0..side {
                for x in 0..side {
                    let (want, got, between) = (code_of(&picture, x, y), code_of(&again, x, y), code_of(&placed, x, y));
                    prop_assert!(want.iter().zip(got).all(|(a, b)| a.abs_diff(b) <= 10), "{:?} came back as {:?} at {},{}", want, got, x, y);
                    moved |= want.iter().zip(between).any(|(a, b)| a.abs_diff(b) > 20);
                }
            }
            // The picture between is another one: the first pass did scale it.
            prop_assert!(moved || (scale - 1.0).abs() <= 0.2, "scaled by {} it is the same picture", scale);
        }
    }

    #[test]
    fn mismatched_layers_are_errors() {
        assert!(composite(2, 1, &[rgba8(2, 1, [0; 4]), rgba8(1, 1, [0; 4])]).is_err(), "another size");
        assert!(composite(1, 1, &[rgba8(1, 1, [0; 4]), rgba16f([0.0; 4])]).is_err(), "mixed kinds");
        let Pixels::Rgba8(data) = &*composite(3, 2, &[]).unwrap().pixels else { panic!() };
        assert!(data.chunks(4).all(|p| p == [0, 0, 0, 255]), "no layers is black");
    }
}
