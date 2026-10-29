//! The display transform for scene-linear frames: an OCIO display/view,
//! baked into a 3D LUT the shader samples.
//!
//! A scene-linear value is far from uniform in [0, 1], so the LUT is indexed
//! in log2 space (a "shaper"): a channel value `c` sits at
//! `(log2(c) - lo) / (hi - lo)` along its axis, clamped to the cube, which
//! spends the lattice evenly over the stops from `2^lo` to `2^hi`. Each
//! lattice point holds the transform of the linear value it stands for.
//! Values below `2^lo` (black, negatives) show as `2^lo` does.
//!
//! OCIO only runs when a LUT is baked, on the CPU; drawing is one texture
//! lookup. Movies are display-referred already and never go through it.

use std::sync::atomic::{AtomicU64, Ordering};

use half::f16;

/// The shaper's range, in stops: 2^-12 (~0.00024) to 2^8 (256).
pub const SHAPER: (f32, f32) = (-12.0, 8.0);
/// Lattice points per axis.
pub const LUT_SIZE: usize = 65;

/// A baked display LUT. `rgba` is `size`^3 texels, red varying fastest,
/// then green, then blue: the layout of a 3D texture with width = red.
#[derive(Debug, Clone)]
pub struct Lut {
    /// Distinguishes LUTs, so the renderer re-uploads only on change.
    pub id: u64,
    pub size: usize,
    pub shaper: (f32, f32),
    pub rgba: Vec<f16>,
}

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

impl Lut {
    /// Where linear `c` sits along an axis, in [0, 1] (the shader's
    /// `display_transform` shapes the same way).
    pub fn shape(shaper: (f32, f32), c: f32) -> f32 {
        let (lo, hi) = shaper;
        ((c.max(lo.exp2()).log2() - lo) / (hi - lo)).clamp(0.0, 1.0)
    }

    /// The linear value lattice point `i` of `size` stands for.
    pub fn lattice_value(shaper: (f32, f32), size: usize, i: usize) -> f32 {
        let (lo, hi) = shaper;
        (lo + (hi - lo) * i as f32 / (size - 1) as f32).exp2()
    }

    /// Bake `transform`, which maps packed linear RGBA f32 pixels to display
    /// values in place (OCIO's `apply_rgba_pixels`), one blue slice at a time.
    pub fn bake(size: usize, shaper: (f32, f32), mut transform: impl FnMut(&mut [f32])) -> Self {
        let value = |i| Self::lattice_value(shaper, size, i);
        let mut rgba = Vec::with_capacity(size * size * size * 4);
        let mut slice = vec![0f32; size * size * 4];
        for b in 0..size {
            for g in 0..size {
                for r in 0..size {
                    let at = (g * size + r) * 4;
                    slice[at..at + 4].copy_from_slice(&[value(r), value(g), value(b), 1.0]);
                }
            }
            transform(&mut slice);
            rgba.extend(slice.iter().map(|&v| f16::from_f32(v)));
        }
        Self { id: NEXT_ID.fetch_add(1, Ordering::Relaxed), size, shaper, rgba }
    }

    fn texel(&self, r: usize, g: usize, b: usize) -> [f32; 3] {
        let at = ((b * self.size + g) * self.size + r) * 4;
        [self.rgba[at].to_f32(), self.rgba[at + 1].to_f32(), self.rgba[at + 2].to_f32()]
    }

    /// What the shader shows for linear `rgb`: the shaped coordinates,
    /// trilinearly interpolated between lattice points. (The shader's
    /// texture sampling does the same at half precision.) Thumbnails use it
    /// to show an EXR frame as the view would.
    pub fn sample(&self, rgb: [f32; 3]) -> [f32; 3] {
        let last = (self.size - 1) as f32;
        let axis = |c: f32| {
            let x = Self::shape(self.shaper, c) * last;
            let i = (x.floor() as usize).min(self.size - 2);
            (i, x - i as f32)
        };
        let ((r, fr), (g, fg), (b, fb)) = (axis(rgb[0]), axis(rgb[1]), axis(rgb[2]));
        let mut out = [0f32; 3];
        for (dr, wr) in [(0, 1.0 - fr), (1, fr)] {
            for (dg, wg) in [(0, 1.0 - fg), (1, fg)] {
                for (db, wb) in [(0, 1.0 - fb), (1, fb)] {
                    let t = self.texel(r + dr, g + dg, b + db);
                    for c in 0..3 {
                        out[c] += t[c] * wr * wg * wb;
                    }
                }
            }
        }
        out
    }
}

#[cfg(feature = "ocio")]
pub use ocio::Colour;

#[cfg(feature = "ocio")]
mod ocio {
    use std::collections::HashMap;
    use std::sync::Arc;

    use ocio_rs::{Config, TransformDirection};

    use super::{LUT_SIZE, Lut, SHAPER};

    /// An OCIO config and the display LUTs baked from it so far.
    pub struct Colour {
        config: Config,
        cache: HashMap<(String, String, String), Arc<Lut>>,
    }

    impl Colour {
        /// The config at `path`, or `$OCIO` when None.
        pub fn open(path: Option<&str>) -> Result<Self, String> {
            let path = match path {
                Some(p) => p.to_owned(),
                None => std::env::var("OCIO").map_err(|_| "no OCIO config given and $OCIO is not set".to_owned())?,
            };
            let config = Config::from_file(&path).map_err(|e| format!("{path}: {e}"))?;
            Ok(Self { config, cache: HashMap::new() })
        }

        pub fn displays(&self) -> Vec<String> {
            (0..self.config.num_displays()).filter_map(|i| self.config.display(i)).collect()
        }

        pub fn views(&self, display: &str) -> Vec<String> {
            (0..self.config.num_views(display)).filter_map(|i| self.config.view(display, i)).collect()
        }

        pub fn default_display(&self) -> Option<String> {
            self.config.default_display()
        }

        pub fn default_view(&self, display: &str) -> Option<String> {
            self.config.default_view(display)
        }

        /// The colour space scene-linear frames are in when they do not say.
        pub fn scene_linear(&self) -> String {
            self.config.role_color_space("scene_linear").unwrap_or_else(|| "scene_linear".to_owned())
        }

        /// True when `name` is a colour space (or role) of the config.
        pub fn has_colour_space(&self, name: &str) -> bool {
            (0..self.config.num_color_spaces())
                .filter_map(|i| self.config.color_space_name_by_index(i))
                .any(|n| n == name)
                || self.config.has_role(name)
        }

        /// The LUT showing `input` through `display`/`view`.
        pub fn lut(&mut self, input: &str, display: &str, view: &str) -> Result<Arc<Lut>, String> {
            let key = (input.to_owned(), display.to_owned(), view.to_owned());
            if let Some(lut) = self.cache.get(&key) {
                return Ok(Arc::clone(lut));
            }
            let cpu = self
                .config
                .processor_display(input, display, view, TransformDirection::Forward)
                .and_then(|p| p.default_cpu_processor())
                .map_err(|e| format!("{input} -> {display} / {view}: {e}"))?;
            let lut = Arc::new(Lut::bake(LUT_SIZE, SHAPER, |pixels| {
                let n = (pixels.len() / 4) as i64;
                // Stride is in floats per pixel.
                cpu.apply_rgba_pixels(pixels, n, 4);
            }));
            self.cache.insert(key, Arc::clone(&lut));
            Ok(lut)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    const SIZE: usize = 17;

    fn linear() -> impl Strategy<Value = f32> {
        prop_oneof![Just(0.0f32), -1.0f32..0.0, 1e-5f32..1.0, 1.0f32..400.0]
    }

    proptest! {
        /// Shaping is monotonic and lands every lattice value on its point.
        #[test]
        fn shape_is_monotonic_and_hits_the_lattice(a in linear(), b in linear(), i in 0..SIZE) {
            if a <= b {
                prop_assert!(Lut::shape(SHAPER, a) <= Lut::shape(SHAPER, b));
            }
            let at = Lut::shape(SHAPER, Lut::lattice_value(SHAPER, SIZE, i)) * (SIZE - 1) as f32;
            prop_assert!((at - i as f32).abs() < 1e-3, "lattice point {} shaped to {}", i, at);
        }

        /// The texel layout is red fastest, then green, then blue: a
        /// transform that keeps one channel shows only that channel, from the
        /// right axis.
        #[test]
        fn each_axis_is_its_own_channel(rgb in [linear(), linear(), linear()], keep in 0usize..3) {
            let lut = Lut::bake(SIZE, SHAPER, |pixels| {
                for p in pixels.chunks_exact_mut(4) {
                    let v = Lut::shape(SHAPER, p[keep]);
                    p[0] = 0.0;
                    p[1] = 0.0;
                    p[2] = 0.0;
                    p[keep] = v;
                }
            });
            let shown = lut.sample(rgb);
            for c in 0..3 {
                let expected = if c == keep { Lut::shape(SHAPER, rgb[keep]) } else { 0.0 };
                // Shaped coordinates are linear in the lattice, so
                // interpolation is exact up to half precision.
                prop_assert!((shown[c] - expected).abs() < 2e-3, "channel {} shows {} for {}", c, shown[c], expected);
            }
        }

        /// A LUT samples exactly what was baked at its lattice points.
        #[test]
        fn lattice_points_sample_exactly(r in 0..SIZE, g in 0..SIZE, b in 0..SIZE) {
            let lut = Lut::bake(SIZE, SHAPER, |pixels| {
                for p in pixels.chunks_exact_mut(4) {
                    // Any smooth display-like curve.
                    for c in &mut p[..3] {
                        *c = (*c / (*c + 1.0)).powf(1.0 / 2.2);
                    }
                }
            });
            let v = |i| Lut::lattice_value(SHAPER, SIZE, i);
            let curve = |x: f32| (x / (x + 1.0)).powf(1.0 / 2.2);
            let shown = lut.sample([v(r), v(g), v(b)]);
            for (c, i) in [r, g, b].into_iter().enumerate() {
                prop_assert!((shown[c] - curve(v(i))).abs() < 2e-3);
            }
        }
    }

    #[test]
    fn ids_differ() {
        let a = Lut::bake(2, SHAPER, |_| {});
        let b = Lut::bake(2, SHAPER, |_| {});
        assert_ne!(a.id, b.id);
    }
}
