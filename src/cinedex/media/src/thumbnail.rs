//! A small picture of a frame, for the player's timeline: the frame scaled to
//! fit a box (aspect kept, never enlarged) once, as a [`Proxy`], then shown
//! as 8-bit code values the way the video view shows it. A movie frame's
//! Y'CbCr converts as `video.wgsl` converts it; an EXR frame's proxy stays
//! scene-linear and goes through the display transform each time it is
//! shown, so a change of display recolours it without decoding again.

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

fn code(value: f32) -> u8 {
    (value.clamp(0.0, 1.0) * 255.0).round() as u8
}

/// One movie pixel as R'G'B' in 0..1. Must match `yuv` in `video.wgsl`.
pub(crate) fn yuv_pixel(y: u8, cb: u8, cr: u8, matrix: YuvMatrix, full_range: bool) -> [f32; 3] {
    yuv_pixel_f32(f32::from(y), f32::from(cb), f32::from(cr), matrix, full_range)
}

/// [`yuv_pixel`] of samples on the 8-bit scale, fractions kept (a deeper
/// sample divided down to it).
pub(crate) fn yuv_pixel_f32(y: f32, cb: f32, cr: f32, matrix: YuvMatrix, full_range: bool) -> [f32; 3] {
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

/// A frame scaled down: each pixel the mean of its patch of the frame.
#[derive(Debug, Clone, PartialEq)]
pub struct Proxy {
    pub width: u32,
    pub height: u32,
    pub rgb: Vec<[f32; 3]>,
    /// Scene-linear (an EXR's), shown through the display transform; else
    /// display-referred already (a movie's R'G'B' in 0..1).
    pub linear: bool,
}

impl Proxy {
    /// The proxy as a thumbnail. `display` maps scene-linear RGB to display
    /// code values in 0..1; a display-referred proxy does not go through it.
    pub fn shown(&self, display: impl Fn([f32; 3]) -> [f32; 3]) -> Thumbnail {
        let mut rgba = Vec::with_capacity(self.rgb.len() * 4);
        for &rgb in &self.rgb {
            let shown = if self.linear { display(rgb) } else { rgb };
            rgba.extend(shown.map(code));
            rgba.push(255);
        }
        Thumbnail { width: self.width, height: self.height, rgba }
    }
}

/// `frame` scaled to fit `max_width` x `max_height`. Linear frames average
/// before any display transform, as light does.
pub fn proxy(frame: &Frame, max_width: u32, max_height: u32) -> Proxy {
    let (width, height) = fit(frame.width, frame.height, max_width, max_height);
    let (fw, fh) = (frame.width.max(1), frame.height.max(1));
    // The frame pixel under sample `s` of thumbnail pixel `at`, along one axis.
    let source = |at: u32, s: u32, side: u32, out: u32| -> usize {
        let x = (f64::from(at) + (f64::from(s) + 0.5) / f64::from(SAMPLES)) * f64::from(side) / f64::from(out);
        (x as u32).min(side - 1) as usize
    };
    let mut rgb = Vec::with_capacity(width as usize * height as usize);
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
                        // Straight alpha, shown over black.
                        Pixels::Rgba8(data) => {
                            let at = (y * fw as usize + x) * 4;
                            let a = f32::from(data[at + 3]) / 255.0;
                            [0, 1, 2].map(|c| f32::from(data[at + c]) / 255.0 * a)
                        }
                        Pixels::Rgba16f(data) => {
                            let at = (y * fw as usize + x) * 4;
                            [data[at].to_f32(), data[at + 1].to_f32(), data[at + 2].to_f32()]
                        }
                    };
                    for c in 0..3 {
                        sum[c] += rgb[c];
                    }
                }
            }
            rgb.push(sum.map(|c| c / (SAMPLES * SAMPLES) as f32));
        }
    }
    Proxy { width, height, rgb, linear: matches!(&*frame.pixels, Pixels::Rgba16f(_)) }
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

    fn exr(width: u32, height: u32, value: [f32; 3]) -> Frame {
        let pixel = [value[0], value[1], value[2], 1.0].map(f16::from_f32);
        let data = pixel.iter().copied().cycle().take(width as usize * height as usize * 4).collect();
        Frame { width, height, pixels: Arc::new(Pixels::Rgba16f(data)) }
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
            let thumb = proxy(&movie(width, height, y, 128, matrix), 64, 64).shown(|_| [0.0; 3]);
            prop_assert_eq!((thumb.width, thumb.height), fit(width, height, 64, 64));
            prop_assert_eq!(thumb.rgba.len(), (thumb.width * thumb.height * 4) as usize);
            prop_assert!(thumb.rgba.chunks(4).all(|p| p == [y, y, y, 255]));
        }

        /// A flat EXR frame thumbnails to its value through the display
        /// transform, whichever transform it is shown through.
        #[test]
        fn a_flat_exr_frame_goes_through_the_display(width in 1u32..200, height in 1u32..200, value in prop::array::uniform3(0f32..4.0), gain in 0.1f32..1.0) {
            let display = |rgb: [f32; 3]| rgb.map(|c| c * gain);
            let small = proxy(&exr(width, height, value), 48, 48);
            let thumb = small.shown(display);
            let expected = display(value.map(|c| f16::from_f32(c).to_f32())).map(code);
            prop_assert!(thumb.rgba.chunks(4).all(|p| p[..3] == expected && p[3] == 255), "{:?} vs {expected:?}", &thumb.rgba[..4]);
        }
    }
}
