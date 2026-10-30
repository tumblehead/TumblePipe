//! Textures as the rasteriser samples them.
//!
//! The shim decodes image files (Houdini's Hio readers know the formats) and
//! hands over linear RGBA floats; [`Texture::from_linear`] shrinks them to
//! [`MAX_SIZE`] and keeps them as sRGB-encoded bytes. A playblast never needs
//! more than that, and bytes keep a set of 4K textures to a few megabytes
//! each instead of hundreds.

/// Longest side kept, in texels.
pub const MAX_SIZE: usize = 1024;

pub struct Texture {
    pub width: usize,
    pub height: usize,
    /// sRGB-encoded RGBA, row 0 = v of 1 (the top of the image).
    texels: Vec<[u8; 4]>,
}

fn linear_to_srgb(v: f32) -> f32 {
    let v = v.clamp(0.0, 1.0);
    if v <= 0.003_130_8 {
        v * 12.92
    } else {
        1.055 * v.powf(1.0 / 2.4) - 0.055
    }
}

fn srgb_to_linear(v: f32) -> f32 {
    if v <= 0.040_45 {
        v / 12.92
    } else {
        ((v + 0.055) / 1.055).powf(2.4)
    }
}

fn decode_lut() -> &'static [f32; 256] {
    use std::sync::OnceLock;
    static LUT: OnceLock<[f32; 256]> = OnceLock::new();
    LUT.get_or_init(|| {
        let mut lut = [0.0; 256];
        for (i, slot) in lut.iter_mut().enumerate() {
            *slot = srgb_to_linear(i as f32 / 255.0);
        }
        lut
    })
}

impl Texture {
    /// `pixels` is `width * height` linear RGBA texels, row 0 at the top.
    pub fn from_linear(pixels: &[[f32; 4]], width: usize, height: usize) -> Texture {
        assert_eq!(pixels.len(), width * height);
        let step = width.max(height).div_ceil(MAX_SIZE).max(1);
        let (w, h) = (width.div_ceil(step), height.div_ceil(step));
        let mut texels = Vec::with_capacity(w * h);
        for ty in 0..h {
            for tx in 0..w {
                // Box filter over the step x step block this texel covers.
                let mut sum = [0.0f32; 4];
                let mut n = 0.0f32;
                for y in (ty * step)..((ty + 1) * step).min(height) {
                    for x in (tx * step)..((tx + 1) * step).min(width) {
                        let p = pixels[y * width + x];
                        for c in 0..4 {
                            sum[c] += p[c];
                        }
                        n += 1.0;
                    }
                }
                let enc = |c: usize, srgb: bool| {
                    let v = sum[c] / n;
                    let v = if srgb { linear_to_srgb(v) } else { v.clamp(0.0, 1.0) };
                    (v * 255.0 + 0.5) as u8
                };
                texels.push([enc(0, true), enc(1, true), enc(2, true), enc(3, false)]);
            }
        }
        Texture { width: w, height: h, texels }
    }

    /// The sRGB-encoded RGBA texels, row 0 at the top.
    pub fn texels(&self) -> &[[u8; 4]] {
        &self.texels
    }

    /// Bilinear, repeating, linear RGB at texture coordinate (u, v) with
    /// v = 0 at the bottom as USD's st has it.
    pub fn sample(&self, u: f32, v: f32) -> [f32; 3] {
        let lut = decode_lut();
        let fx = (u - u.floor()) * self.width as f32 - 0.5;
        let fy = (1.0 - (v - v.floor())) * self.height as f32 - 0.5;
        let (x0, y0) = (fx.floor(), fy.floor());
        let (ax, ay) = (fx - x0, fy - y0);
        let wrap = |i: f32, n: usize| (i as i64).rem_euclid(n as i64) as usize;
        let at = |x: f32, y: f32| {
            let t = self.texels[wrap(y, self.height) * self.width + wrap(x, self.width)];
            [lut[t[0] as usize], lut[t[1] as usize], lut[t[2] as usize]]
        };
        let (a, b, c, d) = (at(x0, y0), at(x0 + 1.0, y0), at(x0, y0 + 1.0), at(x0 + 1.0, y0 + 1.0));
        let mut out = [0.0; 3];
        for i in 0..3 {
            let top = a[i] + (b[i] - a[i]) * ax;
            let bottom = c[i] + (d[i] - c[i]) * ax;
            out[i] = top + (bottom - top) * ay;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn large_textures_shrink_and_keep_their_colour() {
        let (w, h) = (4096, 2048);
        let px = vec![[0.5f32, 0.25, 0.0, 1.0]; w * h];
        let t = Texture::from_linear(&px, w, h);
        assert_eq!((t.width, t.height), (1024, 512));
        let s = t.sample(0.3, 0.7);
        assert!((s[0] - 0.5).abs() < 0.01 && (s[1] - 0.25).abs() < 0.01, "{s:?}");
    }

    #[test]
    fn v_zero_is_the_bottom_row() {
        // Top half red, bottom half blue (tall enough that bilinear
        // filtering near v = 0.25 / 0.75 reads one colour only).
        let (w, h) = (2, 8);
        let px: Vec<[f32; 4]> = (0..w * h)
            .map(|i| if i < w * h / 2 { [1.0, 0.0, 0.0, 1.0] } else { [0.0, 0.0, 1.0, 1.0] })
            .collect();
        let t = Texture::from_linear(&px, w, h);
        assert!(t.sample(0.5, 0.75)[0] > 0.9);
        assert!(t.sample(0.5, 0.25)[2] > 0.9);
    }
}
