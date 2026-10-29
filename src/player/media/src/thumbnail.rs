//! A small, display-ready picture of a frame, for the player's timeline: the
//! frame scaled to fit a box (aspect kept, never enlarged) as 8-bit code
//! values, the way the video view shows it. A movie frame's Y'CbCr converts
//! as `video.wgsl` converts it; an EXR frame's scene-linear RGB goes through
//! the display transform the caller passes.

use crate::frame::{Frame, Pixels, YuvMatrix};

/// RGBA8 code values, row-major, `width * height * 4` bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Thumbnail {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// Samples per axis each thumbnail pixel averages over its patch of the
/// frame: enough to keep fine detail from aliasing, few enough to stay cheap
/// on a 2K frame.
const SAMPLES: u32 = 4;

/// The size a `width` x `height` frame takes inside `max_width` x
/// `max_height`: aspect kept to the nearest pixel, never enlarged, at least
/// one pixel each way.
pub fn fit(width: u32, height: u32, max_width: u32, max_height: u32) -> (u32, u32) {
    if width == 0 || height == 0 {
        return (1, 1);
    }
    let scale = (f64::from(max_width) / f64::from(width)).min(f64::from(max_height) / f64::from(height)).min(1.0);
    let size = |side: u32, max: u32| ((f64::from(side) * scale).round() as u32).clamp(1, max.max(1));
    (size(width, max_width), size(height, max_height))
}

/// sRGB-encode a linear value: what the video view's sRGB surface does to a
/// scene-linear frame shown without a display transform.
pub fn srgb_encode(linear: f32) -> f32 {
    let c = linear.clamp(0.0, 1.0);
    if c <= 0.003_130_8 { c * 12.92 } else { 1.055 * c.powf(1.0 / 2.4) - 0.055 }
}

fn code(value: f32) -> u8 {
    (value.clamp(0.0, 1.0) * 255.0).round() as u8
}

/// One movie pixel as R'G'B' in 0..1. Must match `yuv` in `video.wgsl`.
fn yuv_pixel(y: u8, cb: u8, cr: u8, matrix: YuvMatrix, full_range: bool) -> [f32; 3] {
    let (y, cb, cr) = (f32::from(y), f32::from(cb), f32::from(cr));
    let (luma, cb, cr) = if full_range {
        (y / 255.0, (cb - 128.0) / 255.0, (cr - 128.0) / 255.0)
    } else {
        ((y - 16.0) / 219.0, (cb - 128.0) / 224.0, (cr - 128.0) / 224.0)
    };
    let rgb = match matrix {
        YuvMatrix::Bt709 => [luma + 1.5748 * cr, luma - 0.187_324 * cb - 0.468_124 * cr, luma + 1.8556 * cb],
        YuvMatrix::Bt601 => [luma + 1.402 * cr, luma - 0.344_136 * cb - 0.714_136 * cr, luma + 1.772 * cb],
    };
    rgb.map(|c| c.clamp(0.0, 1.0))
}

/// `frame` as a thumbnail fitting `max_width` x `max_height`. `display` maps
/// an EXR frame's scene-linear RGB to display code values in 0..1 (a single
/// channel shows as grey); a movie frame is display-referred already and
/// does not go through it.
pub fn thumbnail(frame: &Frame, max_width: u32, max_height: u32, display: impl Fn([f32; 3]) -> [f32; 3]) -> Thumbnail {
    let (width, height) = fit(frame.width, frame.height, max_width, max_height);
    let (fw, fh) = (frame.width.max(1), frame.height.max(1));
    // The frame pixel under sample `s` of thumbnail pixel `at`, along one axis.
    let source = |at: u32, s: u32, side: u32, out: u32| -> usize {
        let x = (f64::from(at) + (f64::from(s) + 0.5) / f64::from(SAMPLES)) * f64::from(side) / f64::from(out);
        (x as u32).min(side - 1) as usize
    };
    let mut rgba = Vec::with_capacity(width as usize * height as usize * 4);
    for ty in 0..height {
        for tx in 0..width {
            let mut sum = [0f32; 3];
            for sy in 0..SAMPLES {
                let y = source(ty, sy, fh, height);
                for sx in 0..SAMPLES {
                    let x = source(tx, sx, fw, width);
                    let rgb = match &*frame.pixels {
                        Pixels::Yuv420 { y: luma, u, v, matrix, full_range } => {
                            let chroma = (y / 2) * fw.div_ceil(2) as usize + x / 2;
                            yuv_pixel(luma[y * fw as usize + x], u[chroma], v[chroma], *matrix, *full_range)
                        }
                        Pixels::Half { data, channels } => {
                            let at = (y * fw as usize + x) * channels;
                            if *channels < 3 {
                                [data[at].to_f32(); 3]
                            } else {
                                [data[at].to_f32(), data[at + 1].to_f32(), data[at + 2].to_f32()]
                            }
                        }
                    };
                    for c in 0..3 {
                        sum[c] += rgb[c];
                    }
                }
            }
            let mean = sum.map(|c| c / (SAMPLES * SAMPLES) as f32);
            // Linear frames average before the display transform, as light does.
            let shown = match &*frame.pixels {
                Pixels::Yuv420 { .. } => mean,
                Pixels::Half { .. } => display(mean),
            };
            rgba.extend(shown.map(code));
            rgba.push(255);
        }
    }
    Thumbnail { width, height, rgba }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use half::f16;
    use proptest::prelude::*;

    use super::*;

    fn movie(width: u32, height: u32, y: u8, chroma: u8, matrix: YuvMatrix) -> Frame {
        let (w, h) = (width as usize, height as usize);
        let c = w.div_ceil(2) * h.div_ceil(2);
        let pixels = Pixels::Yuv420 { y: vec![y; w * h], u: vec![chroma; c], v: vec![chroma; c], matrix, full_range: true };
        Frame { width, height, pixels: Arc::new(pixels) }
    }

    fn exr(width: u32, height: u32, value: [f32; 3], channels: usize) -> Frame {
        let pixel: Vec<f16> = (0..channels).map(|c| f16::from_f32(value[c.min(2)])).collect();
        let data = pixel.iter().copied().cycle().take(width as usize * height as usize * channels).collect();
        Frame { width, height, pixels: Arc::new(Pixels::Half { data, channels }) }
    }

    proptest! {
        /// A fitted size stays inside the box, never grows the frame, fills
        /// the box along one side, and keeps the aspect to a pixel.
        #[test]
        fn fitting_keeps_aspect_inside_the_box(width in 1u32..5000, height in 1u32..5000, max_w in 1u32..400, max_h in 1u32..400) {
            let (w, h) = fit(width, height, max_w, max_h);
            prop_assert!(w >= 1 && h >= 1 && w <= max_w && h <= max_h);
            prop_assert!(w <= width && h <= height);
            if width <= max_w && height <= max_h {
                prop_assert_eq!((w, h), (width, height));
            } else {
                prop_assert!(w + 1 >= max_w || h + 1 >= max_h);
            }
            // One pixel of rounding in either side, measured across.
            let error = (u64::from(w) * u64::from(height)).abs_diff(u64::from(h) * u64::from(width));
            prop_assert!(error <= u64::from(width.max(height)), "{w}x{h} from {width}x{height}");
        }

        /// A flat grey movie frame thumbnails to that grey, whatever its size.
        #[test]
        fn a_flat_movie_frame_stays_flat(width in 1u32..300, height in 1u32..300, y in any::<u8>(), bt709 in any::<bool>()) {
            let matrix = if bt709 { YuvMatrix::Bt709 } else { YuvMatrix::Bt601 };
            let thumb = thumbnail(&movie(width, height, y, 128, matrix), 64, 64, |_| [0.0; 3]);
            prop_assert_eq!((thumb.width, thumb.height), fit(width, height, 64, 64));
            prop_assert_eq!(thumb.rgba.len(), (thumb.width * thumb.height * 4) as usize);
            prop_assert!(thumb.rgba.chunks(4).all(|p| p == [y, y, y, 255]));
        }

        /// A flat EXR frame thumbnails to its value through the display
        /// transform; a single channel shows as grey.
        #[test]
        fn a_flat_exr_frame_goes_through_the_display(width in 1u32..200, height in 1u32..200, value in prop::array::uniform3(0f32..4.0), grey in any::<bool>()) {
            let channels = if grey { 1 } else { 3 };
            let display = |rgb: [f32; 3]| rgb.map(|c| c * 0.25);
            let thumb = thumbnail(&exr(width, height, value, channels), 48, 48, display);
            let stored = value.map(|c| f16::from_f32(c).to_f32());
            let shown = if grey { [stored[0]; 3] } else { stored };
            let expected = display(shown).map(code);
            prop_assert!(thumb.rgba.chunks(4).all(|p| p[..3] == expected && p[3] == 255), "{:?} vs {expected:?}", &thumb.rgba[..4]);
        }
    }

    #[test]
    fn srgb_encoding_matches_the_standard_curve() {
        assert_eq!(srgb_encode(0.0), 0.0);
        assert!((srgb_encode(1.0) - 1.0).abs() < 1e-6);
        assert!((srgb_encode(0.18) - 0.4613).abs() < 1e-3);
        assert_eq!(srgb_encode(2.0), srgb_encode(1.0));
    }
}
