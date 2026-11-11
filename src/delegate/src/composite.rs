//! The post pass: a render buffer in, the finished image out, the way a game
//! engine composites its G-buffer. Both rasterisers ([`crate::raster`] and
//! the GPU path) only fill [`Samples`]; everything that makes the picture
//! (ambient occlusion, lighting, the backdrop, the anti-aliasing filter)
//! happens here. The GPU path runs the same pass on the card (`post.wgsl`),
//! reading its constants and tap table from this module; this one is the
//! reference, and the GPU tests hold the two to the same picture.
//!
//! Ambient occlusion is screen-space and comes in three tiers, small, medium
//! and large ([`AO_TIERS`]): creases and contacts, then folds and limbs, then
//! whole objects against walls and floors. Their product darkens the surface.
//! It is worked out per sample of the render buffer, so averaging a pixel's
//! samples anti-aliases it along with the shading: a few taps per tier in a
//! spiral turned per pixel by a small ordered pattern (4x4, 2x2 or none, the
//! more samples the smaller) and per sample by a share of a turn, then a
//! separable depth-aware blur that spans the pattern. A tier fades in with
//! its size on screen.
//!
//! The lights are the scene's own UsdLux lights ([`crate::lights`]), as
//! Houdini's GL viewport lights a scene with them; a scene without any, or
//! a frame asking for the headlight (`th:lighting`), is lit by lights fixed
//! to the camera ([`LIGHTS`]), so it is never dark and every such shot is
//! lit the same way.
//!
//! Transparent background ([`Frame::alpha`], `TH_PLAYBLAST_ALPHA`): the
//! backdrop is left out. A sample that hit nothing adds colour 0 and
//! coverage 0, one that hit a surface its lit colour and coverage 1, and the
//! pixel is the average of both, so its colour is premultiplied by its
//! alpha (an edge pixel half covered by white is 0.5, 0.5, 0.5, 0.5) and
//! alpha goes through the same samples, motion blur and depth of field as
//! the colour. The surfaces' own shading and occlusion are unchanged.
//! Without it every pixel is opaque (alpha 1) over the backdrop gradient,
//! bit for bit as before the option existed. What the delegate hands husk
//! is this premultiplied linear RGBA. husk writes it to an EXR unchanged
//! (premultiplied, linear); to a PNG it converts the premultiplied colour
//! to sRGB and OIIO then divides by alpha, clamped: `min(1, srgb(c) / a)`
//! with `a` linear, straight alpha in display space (docs/development.md,
//! "hdThPlayblast: transparent background").
//!
//! See-through surfaces ([`crate::raster::Surface::Glass`], a mesh of
//! opacity under 1) are not in the render buffer the post pass lights: the
//! rasteriser lights each where it draws it, after the opaque surfaces and
//! in front of their depth, and adds it to the sample's weighted blended
//! order-independent sums, every weight 1 ([`Samples::accum`],
//! [`Samples::reveal`]). Here each sample then becomes `behind * reveal +
//! accum.rgb / accum.a * (1 - reveal)`, behind being the opaque surface's
//! lit, occluded colour or the backdrop; a sample nothing opaque covers
//! counts `1 - reveal` towards the pixel's coverage. A frame without them
//! has no sums and is resolved bit for bit as before (docs/development.md,
//! "hdThPlayblast: see-through surfaces").

use crate::lights::Lighting;
use crate::math::{self, Mat4, Vec3};
use crate::pool;
use crate::raster::{srgb_to_linear, Frame, Image};

/// What the post pass makes of the render buffer.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Shading {
    /// The scene's lights (or the fixed key, fill and rim lights plus a
    /// sky/ground ambient) on the surface's own colour.
    #[default]
    Lit,
    /// The same lights on a uniform grey.
    Clay,
    /// The surface colour alone, unlit.
    Albedo,
    /// White, darkened only by the ambient occlusion.
    Occlusion,
}

impl Shading {
    /// The ABI's number for it ([`Shading::from_abi`]'s inverse).
    pub fn abi(self) -> u32 {
        match self {
            Shading::Lit => 0,
            Shading::Clay => 1,
            Shading::Albedo => 2,
            Shading::Occlusion => 3,
        }
    }

    /// The ABI's number for it: 0 lit, 1 clay, 2 albedo, 3 occlusion;
    /// anything else lit.
    pub fn from_abi(v: u32) -> Shading {
        match v {
            1 => Shading::Clay,
            2 => Shading::Albedo,
            3 => Shading::Occlusion,
            _ => Shading::Lit,
        }
    }
}

/// Display-referred backdrop, top and bottom (Houdini's light viewport
/// gradient); linearised when drawn, since the image husk receives is linear.
pub(crate) const BACKDROP_TOP: f32 = 0.70;
pub(crate) const BACKDROP_BOTTOM: f32 = 0.52;
/// The fixed rig ([`crate::lights::Lighting::rig`]): camera space (x right,
/// y up, z towards the camera), each pointing from the surface towards the
/// light, with its intensity.
pub(crate) const LIGHTS: [(Vec3, f32); 3] = [
    ([-0.35, 0.35, 1.0], 0.62), // key: above and left of the lens
    ([0.7, -0.15, 0.5], 0.18),  // fill: low on the right
    ([0.0, 0.7, -0.7], 0.30),   // rim: above and behind, edges the tops
];
/// Ambient from above (camera up) and below.
pub(crate) const SKY: f32 = 0.24;
pub(crate) const GROUND: f32 = 0.08;
/// A faint highlight of the key light.
pub(crate) const SPECULAR: f32 = 0.05;
pub(crate) const SHININESS: i32 = 32;
/// The clay material's colour, linear.
pub const CLAY_ALBEDO: Vec3 = [0.45, 0.45, 0.45];

/// Ambient occlusion tiers: radius in scene units and strength. The radius
/// is held under [`AO_MAX_SCREEN`] of the image height on screen, so a
/// surface near the lens is not darkened by half the frame.
pub const AO_TIERS: [(f32, f32); 3] = [(0.04, 0.9), (0.25, 0.9), (1.2, 0.8)];
pub(crate) const AO_MAX_SCREEN: f32 = 0.12;
/// Taps per tier per pixel, shared among its samples ([`ao_taps`]); every
/// sample of a pixel taps in another direction.
const AO_PIXEL_TAPS: usize = 24;
/// A tier fades in as its radius grows from the first to the second of
/// these, in pixels, instead of popping on at one pixel.
pub(crate) const AO_FADE_PX: (f32, f32) = (0.5, 1.5);
/// The blur's half-width, in pixels.
pub(crate) const AO_BLUR_PX: usize = 2;
/// Taps see a crease from one side only, so their average is small; this
/// brings a deep crease near full strength.
pub(crate) const AO_GAIN: f32 = 3.0;
/// Ignore occluders within this angle of the tangent plane: tessellation
/// of a curved surface would otherwise darken it.
pub(crate) const AO_BIAS: f32 = 0.15;

/// Where a pixel's samples sit, in sixteenths of a pixel from its centre (x
/// right, y down): the standard multisample positions of Vulkan and
/// Direct3D. From two samples up no two share a column or a row of the
/// pixel (N-rooks), so an edge near vertical or horizontal steps through
/// all N + 1 coverages as it crosses a pixel, where a 2x2 grid's four
/// samples give it three.
const PATTERNS: [&[(i8, i8)]; 5] = [
    &[(0, 0)],
    &[(4, 4), (-4, -4)],
    &[(-2, -6), (6, -2), (-6, 2), (2, 6)],
    &[(1, -3), (-1, 3), (5, 1), (-3, -5), (-5, 5), (-7, -1), (3, 7), (7, -7)],
    &[(1, 1), (-1, -3), (-3, 2), (4, -1), (-5, -2), (2, 5), (5, 3), (3, -5),
      (-2, 6), (0, -7), (-4, -6), (-6, 4), (-8, 0), (7, -4), (6, 7), (-7, -8)],
];

/// The sample offsets, in pixels from the pixel centre (y down), of the
/// largest pattern with at most `requested` samples (1, 2, 4, 8 or 16).
pub fn sample_offsets(requested: usize) -> Vec<(f32, f32)> {
    let pattern = PATTERNS.iter().rev().find(|p| p.len() <= requested.max(1)).unwrap_or(&PATTERNS[0]);
    pattern.iter().map(|&(x, y)| (x as f32 / 16.0, y as f32 / 16.0)).collect()
}

/// The render buffer: [`Samples::count`] samples for each pixel of a
/// `width x height` image, as that many layers, each a whole image whose
/// samples all sit at the same offset from their pixel centres: sample k
/// of pixel (x, y) is at `k * width * height + y * width + x`. Row 0 is
/// the top.
pub struct Samples {
    pub width: usize,
    pub height: usize,
    /// Per layer, its samples' offset from the pixel centre ([`sample_offsets`]).
    pub offsets: Vec<(f32, f32)>,
    /// Linear surface colour (texture or display colour); w unused.
    pub albedo: Vec<[f32; 4]>,
    /// Camera-space unit normal turned towards the camera, and in w the
    /// distance in front of the camera along its axis.
    pub normal: Vec<[f32; 4]>,
    /// Normalised device depth in [0, 1], 1 = nothing drawn.
    pub depth: Vec<f32>,
    /// One per pixel (not per sample): the mesh and instance the first
    /// layer's sample sees, [-1, -1] for none ([`crate::raster::Image::ids`]).
    pub ids: Vec<[i32; 2]>,
    /// See-through surfaces ([`crate::raster::Surface::Glass`]), per sample
    /// like `depth`, and empty in a frame with none: the sum of each
    /// surface's lit colour times its opacity (rgb) and of its opacities
    /// (w), and the product of one minus its opacities (the revealage, how
    /// much of what is behind shows; 1 for none).
    pub accum: Vec<[f32; 4]>,
    pub reveal: Vec<f32>,
    /// Per pixel like `ids`: the depth of the nearest see-through surface
    /// the first layer's sample saw (1 for none), which a nearer one must
    /// beat to be named in `ids`.
    pub near: Vec<f32>,
}

impl Samples {
    pub fn empty(width: usize, height: usize, offsets: Vec<(f32, f32)>) -> Samples {
        let n = width * height * offsets.len();
        Samples {
            width,
            height,
            offsets,
            albedo: vec![[0.0; 4]; n],
            normal: vec![[0.0; 4]; n],
            depth: vec![1.0; n],
            ids: vec![[-1, -1]; width * height],
            accum: Vec::new(),
            reveal: Vec::new(),
            near: Vec::new(),
        }
    }

    /// Ready the see-through sums for a frame that has see-through
    /// surfaces (cleared: nothing added, everything revealed), or empty
    /// them for one that has none.
    pub(crate) fn glass(&mut self, on: bool) {
        let w = self.width.max(1);
        if !on {
            self.accum.clear();
            self.reveal.clear();
            self.near.clear();
            return;
        }
        let (n, plane) = (self.depth.len(), self.ids.len());
        self.accum.resize(n, [0.0; 4]);
        self.reveal.resize(n, 1.0);
        self.near.resize(plane, 1.0);
        par_rows(&mut self.accum, w, |_, row| row.fill([0.0; 4]));
        par_rows(&mut self.reveal, w, |_, row| row.fill(1.0));
        par_rows(&mut self.near, w, |_, row| row.fill(1.0));
    }

    /// Samples per pixel.
    pub fn count(&self) -> usize {
        self.offsets.len()
    }

    /// This buffer emptied for another frame of its shape, its memory kept
    /// (a new 1080p buffer of 8 samples is 300 MB of page faults, and as
    /// much again to free). Only depth and ids are cleared: depth says
    /// what was drawn, and nothing reads a sample's albedo or normal where
    /// nothing was.
    pub(crate) fn clear(&mut self) {
        let w = self.width.max(1);
        par_rows(&mut self.depth, w, |_, row| row.fill(1.0));
        par_rows(&mut self.ids, w, |_, row| row.fill([-1, -1]));
    }

    /// Whether this buffer is of `width x height` with these sample offsets.
    pub(crate) fn fits(&self, width: usize, height: usize, offsets: &[(f32, f32)]) -> bool {
        self.width == width && self.height == height && self.offsets == offsets
    }
}

/// The camera as the rasterisers need it: its position and unit right, up
/// and back axes in world space (a rigid camera; the camera looks down -z).
#[derive(Clone, Copy)]
pub(crate) struct View {
    pub eye: Vec3,
    pub axes: [Vec3; 3],
}

impl View {
    pub fn new(view_to_world: &Mat4) -> View {
        let vw = view_to_world;
        let axis = |r: usize| math::normalize([vw[r * 4] as f32, vw[r * 4 + 1] as f32, vw[r * 4 + 2] as f32]);
        View { eye: [vw[12] as f32, vw[13] as f32, vw[14] as f32], axes: [axis(0), axis(1), axis(2)] }
    }

    /// A world normal and position as the render buffer keeps them: the
    /// normal in camera space turned towards the camera, and the distance.
    pub fn buffer_normal(&self, normal: Vec3, world: Vec3) -> [f32; 4] {
        let to_eye = math::sub(self.eye, world);
        let mut n = math::normalize(normal);
        if math::dot(n, to_eye) < 0.0 {
            n = math::scale(n, -1.0);
        }
        let [r, u, b] = self.axes;
        let v = math::normalize([math::dot(n, r), math::dot(n, u), math::dot(n, b)]);
        [v[0], v[1], v[2], math::dot(to_eye, b)]
    }

    /// The unit direction from a world position towards the camera, in
    /// camera space: what [`light`] takes as `v` for a surface lit where
    /// it is drawn (a see-through one) rather than in the post pass.
    pub fn towards_eye(&self, world: Vec3) -> Vec3 {
        let to_eye = math::sub(self.eye, world);
        let [r, u, b] = self.axes;
        math::normalize([math::dot(to_eye, r), math::dot(to_eye, u), math::dot(to_eye, b)])
    }

    /// A world position in camera space: what [`light`] takes as `p`.
    pub fn camera_point(&self, world: Vec3) -> Vec3 {
        let v = math::sub(world, self.eye);
        let [r, u, b] = self.axes;
        [math::dot(v, r), math::dot(v, u), math::dot(v, b)]
    }
}

/// Camera-space points from their place on a `w x h` screen (pixels, y
/// down; a pixel centre is at x + 0.5) and distance in front of the camera,
/// through a projection (camera to clip). Its terms are worked out once: the
/// occlusion taps unproject tens of millions of samples a frame, from a
/// plane of distances a third the size of one of positions.
pub(crate) struct Unproject {
    // Screen to normalised device coordinates: x * scale.0 - 1, 1 - y * scale.1.
    pub scale: (f32, f32),
    // The projection's terms in x (8, 12), y (9, 13) and w (11, 15).
    pub p: [f32; 6],
    // 1 / its x and y scales; 0 for a degenerate one.
    pub inv: (f32, f32),
}

impl Unproject {
    pub fn new(proj: &Mat4, w: usize, h: usize) -> Unproject {
        let inv = |v: f64| if v.abs() > 1e-30 { (1.0 / v) as f32 } else { 0.0 };
        Unproject {
            scale: (2.0 / w.max(1) as f32, 2.0 / h.max(1) as f32),
            p: [8, 12, 9, 13, 11, 15].map(|i| proj[i] as f32),
            inv: (inv(proj[0]), inv(proj[5])),
        }
    }

    /// The point at (`sx`, `sy`) on screen, `distance` in front of the camera.
    #[inline]
    fn at(&self, sx: f32, sy: f32, distance: f32) -> Vec3 {
        let [x8, x12, y9, y13, w11, w15] = self.p;
        let (ndc_x, ndc_y) = (sx * self.scale.0 - 1.0, 1.0 - sy * self.scale.1);
        // Clip w of the point at camera z = -distance.
        let cw = w15 - distance * w11;
        [(ndc_x * cw + distance * x8 - x12) * self.inv.0, (ndc_y * cw + distance * y9 - y13) * self.inv.1, -distance]
    }
}

/// Pixels on screen per scene unit at `distance` in front of the camera.
fn pixels_per_unit(proj: &Mat4, height: usize, distance: f32) -> f32 {
    let cw = (-distance as f64 * proj[11] + proj[15]).abs().max(1e-12);
    (proj[5].abs() * height as f64 * 0.5 / cw) as f32
}

/// `f(y, row)` for every row of `out` (`width` long), rows shared out across
/// threads a few at a time as each thread frees up: a thread the OS sets
/// aside, or a band of costly rows beside a band of empty sky, then holds no
/// one else up.
fn par_rows<T: Send>(out: &mut [T], width: usize, f: impl Fn(usize, &mut [T]) + Sync) {
    if width == 0 || out.is_empty() {
        return;
    }
    const BLOCK: usize = 8;
    let blocks = std::sync::Mutex::new(out.chunks_mut(BLOCK * width).enumerate());
    pool::each_thread(&|| loop {
        let next = blocks.lock().unwrap_or_else(|e| e.into_inner()).next();
        let Some((b, block)) = next else { break };
        for (i, row) in block.chunks_mut(width).enumerate() {
            f(b * BLOCK + i, row);
        }
    });
}

/// Ordered patterns of tap rotations, one per pixel of a layer, row by row:
/// P x P of them, P = 4, 2 or 1 ([`rotation_pattern`]). Any P x P window
/// holds each once, so the blur ([`blur_pass`]), four pixels wide, averages
/// them exactly and leaves no noise on an open surface. (Interleaved
/// gradient noise measured three to five times noisier along a crease.)
const ROTATION_4: [u8; 16] = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
const ROTATION_2: [u8; 4] = [0, 2, 3, 1];

/// The rotation pattern and its period P for a pixel of `count` samples:
/// with more samples each pixel's own samples turn more ways, so a smaller
/// pattern still gives sixteen or more turns, and where the blur stops at an
/// edge the few pixels it still spans hold whole periods.
pub(crate) fn rotation_pattern(count: usize) -> (usize, &'static [u8]) {
    match count {
        0..=3 => (4, &ROTATION_4),
        4..=15 => (2, &ROTATION_2),
        _ => (1, &[0]),
    }
}

/// Taps per tier for each sample of a pixel with `count` samples.
pub(crate) fn ao_taps(count: usize) -> usize {
    (AO_PIXEL_TAPS / count.max(1)).clamp(3, 6)
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// The taps of layer `layer` of `layers`: a golden-angle spiral of
/// [`ao_taps`] taps, turned P x P ways ([`rotation_pattern`]) and then by
/// this layer's share of a turn. Per tap, its direction on screen (y up) and
/// distance as a fraction of the radius; turn `t`'s taps are at
/// `t * taps..(t + 1) * taps`. The shares, k/N plus k/(P x P x N), put a
/// pixel's N samples evenly round the circle and every turn of every sample
/// apart. The GPU post pass reads this same table.
pub(crate) fn spirals(layer: usize, layers: usize) -> Vec<(f32, f32, f32)> {
    let layers = layers.max(1);
    let taps = ao_taps(layers);
    let (period, _) = rotation_pattern(layers);
    let cells = period * period;
    let golden = 0.618_034f32;
    let n = layers as f32;
    let share = layer as f32 / n + layer as f32 / (cells as f32 * n);
    (0..cells * taps)
        .map(|i| {
            let (turn, k) = (i / taps, i % taps);
            let (s, c) = (std::f32::consts::TAU * (k as f32 * golden + turn as f32 / cells as f32 + share)).sin_cos();
            (c, s, (k as f32 + 0.5) / taps as f32)
        })
        .collect()
}

/// Ambient occlusion of every sample of a render buffer, laid out as
/// [`Samples`] keeps it: 1 = open, 0 = fully occluded. Per layer, the
/// product of the tiers, blurred. Per sample, so averaging a pixel's samples
/// anti-aliases it as it does the shading, and a surface's occlusion never
/// spills onto the samples of another surface sharing its pixel. `proj`
/// takes camera space to clip.
pub(crate) fn ambient_occlusion(samples: &Samples, proj: &Mat4) -> Vec<f32> {
    let (w, h, layers) = (samples.width, samples.height, samples.count().max(1));
    let plane = w * h;
    let unproject = Unproject::new(proj, w, h);
    let max_px = AO_MAX_SCREEN * h as f32;
    let taps = ao_taps(layers);
    let (period, pattern) = rotation_pattern(layers);
    let mut ao = vec![1.0f32; plane * layers];
    // Each layer's distances (NaN where nothing was drawn), which is all the
    // taps read, and the blur's first pass.
    let (mut distance, mut across) = (vec![f32::NAN; plane], vec![0.0f32; plane]);
    for (layer, out) in ao.chunks_mut(plane).enumerate() {
        let depth = &samples.depth[layer * plane..(layer + 1) * plane];
        let normal = &samples.normal[layer * plane..(layer + 1) * plane];
        let (ox, oy) = samples.offsets.get(layer).copied().unwrap_or((0.0, 0.0));
        par_rows(&mut distance, w, |y, row| {
            for (x, d) in row.iter_mut().enumerate() {
                let at = y * w + x;
                *d = if depth[at] < 1.0 { normal[at][3] } else { f32::NAN };
            }
        });
        let spirals = spirals(layer, layers);
        let distance = &distance;
        par_rows(out, w, |y, row| {
            for (x, out) in row.iter_mut().enumerate() {
                let d = distance[y * w + x];
                if d.is_nan() {
                    continue;
                }
                let (fx, fy) = (x as f32 + 0.5, y as f32 + 0.5);
                let p = unproject.at(fx + ox, fy + oy, d);
                let n = normal[y * w + x];
                let n = [n[0], n[1], n[2]];
                let turn = pattern[(y % period) * period + x % period] as usize;
                let spiral = &spirals[turn * taps..(turn + 1) * taps];
                let ppu = pixels_per_unit(proj, h, d);
                let mut ao = 1.0f32;
                for (radius, strength) in AO_TIERS {
                    let r_px = (radius * ppu).min(max_px);
                    let fade = smoothstep(AO_FADE_PX.0, AO_FADE_PX.1, r_px);
                    if fade <= 0.0 {
                        continue; // finer than a pixel here
                    }
                    let inv_r = ppu / r_px;
                    let mut occlusion = 0.0f32;
                    for &(dx, dy, along) in spiral {
                        let dist = (r_px * along).max(1.0);
                        let (qx, qy) = (fx + dx * dist, fy - dy * dist);
                        if qx < 0.0 || qy < 0.0 || qx >= w as f32 || qy >= h as f32 {
                            continue;
                        }
                        // The nearest sample, not its tangent plane carried
                        // to the tap: on a grazing floor that plane runs on
                        // behind the wall it meets and the crease goes pale.
                        let (qx, qy) = (qx.floor(), qy.floor());
                        let q = unproject.at(qx + 0.5 + ox, qy + 0.5 + oy, distance[qy as usize * w + qx as usize]);
                        let v = math::sub(q, p);
                        let vv = math::dot(v, v);
                        if vv.is_nan() || vv <= 1e-12 {
                            continue;
                        }
                        let inv_len = 1.0 / vv.sqrt();
                        let falloff = smoothstep(0.0, 1.0, 2.0 - 2.0 * inv_r / inv_len);
                        occlusion += (math::dot(v, n) * inv_len - AO_BIAS).max(0.0) * falloff;
                    }
                    let o = (AO_GAIN * occlusion / (taps as f32 * (1.0 - AO_BIAS))).min(1.0);
                    ao *= (1.0 - fade * strength * o).clamp(0.0, 1.0);
                }
                *out = ao;
            }
        });
        blur_pass(out, &mut across, normal, distance, w, h, AO_BLUR_PX, true, period);
        blur_pass(&across, out, normal, distance, w, h, AO_BLUR_PX, false, period);
    }
    ao
}

/// One direction (`across` rows, else down columns) of a separable
/// bilateral blur of `radius` samples, from `ao` into `out`: a neighbour
/// counts as much as its normal agrees and its distance is close, so the
/// blur stays on one surface. The neighbours are averaged per phase of the
/// rotation pattern (its `period`, a divisor of 2 x `radius`), and the
/// phases count the same, each up to its weight: with the two ends at half
/// weight that is the plain average on an open surface, and where the blur
/// stops at an edge it still averages every rotation it reaches, where
/// counting neighbours would favour some and leave the edge darker or
/// lighter. A phase reached only across onto another surface weighs, and
/// counts, next to nothing.
#[allow(clippy::too_many_arguments)]
fn blur_pass(ao: &[f32], out: &mut [f32], normal: &[[f32; 4]], distance: &[f32], w: usize, h: usize,
             radius: usize, across: bool, period: usize) {
    let (stride, len) = if across { (1, w) } else { (w, h) };
    par_rows(out, w, |y, row| {
        for (x, out) in row.iter_mut().enumerate() {
            let at = y * w + x;
            // The layer's distances say what was drawn (NaN where nothing
            // was): a reused render buffer keeps old normals there.
            let (n, d) = (normal[at], distance[at]);
            if d.is_nan() || d <= 0.0 {
                *out = ao[at]; // nothing drawn
                continue;
            }
            let inv_gap = 1.0 / (0.02 * d).max(1e-6);
            let pos = if across { x } else { y };
            let (lo, hi) = (pos.saturating_sub(radius), (pos + radius).min(len - 1));
            let (mut sums, mut weights) = ([0.0f32; 4], [0.0f32; 4]);
            let mut phase = lo % period;
            for q in lo..=hi {
                let at = at + q * stride - pos * stride;
                let e = distance[at];
                if e > 0.0 {
                    // Drawn (NaN where nothing was).
                    let m = normal[at];
                    let agree = (n[0] * m[0] + n[1] * m[1] + n[2] * m[2]).max(0.0).powi(8);
                    let gap = (e - d).abs() * inv_gap;
                    let spatial = if q.abs_diff(pos) == radius { 0.5 } else { 1.0 };
                    let wgt = spatial * agree / (1.0 + gap * gap);
                    sums[phase] += ao[at] * wgt;
                    weights[phase] += wgt;
                }
                phase += 1;
                if phase == period {
                    phase = 0;
                }
            }
            let (mut sum, mut weight) = (0.0f32, 0.0f32);
            for (s, &wt) in sums.iter().zip(&weights).take(period) {
                if wt > 0.0 {
                    let share = wt.min(1.0);
                    sum += s / wt * share;
                    weight += share;
                }
            }
            *out = if weight > 0.0 { sum / weight } else { ao[at] };
        }
    });
}

/// The colour of a surface with `albedo` and camera-space normal `n` at
/// camera-space point `p`, seen along `v` (towards the camera): the
/// ambient, each light's colour by Lambert's cosine, and a highlight of one
/// light ([`Lighting`]). With the fixed rig, per channel exactly the
/// arithmetic of the grey rig before scene lights existed.
pub(crate) fn light(shading: Shading, albedo: Vec3, n: Vec3, v: Vec3, p: Vec3, lighting: &Lighting) -> Vec3 {
    let albedo = match shading {
        Shading::Albedo => return albedo,
        Shading::Occlusion => return [1.0; 3],
        Shading::Clay => CLAY_ALBEDO,
        Shading::Lit => albedo,
    };
    let (sky, ground) = (lighting.sky, lighting.ground);
    let up = 0.5 + 0.5 * n[1];
    let mut k = [0, 1, 2].map(|c| ground[c] + (sky[c] - ground[c]) * up);
    for lamp in &lighting.lamps[..lighting.count] {
        let (l, e) = lamp.toward(p);
        let cos = math::dot(n, l).max(0.0);
        for c in 0..3 {
            k[c] += e[c] * cos;
        }
    }
    let mut spec = [0.0f32; 3];
    if lighting.count > 0 && lighting.specular > 0.0 {
        let (l, e) = lighting.lamps[lighting.shine].toward(p);
        let h = math::normalize(math::add(l, v));
        let s = lighting.specular * math::dot(n, h).max(0.0).powi(SHININESS);
        spec = if lighting.tinted { e.map(|c| s * c) } else { [s; 3] };
    }
    [albedo[0] * k[0] + spec[0], albedo[1] * k[1] + spec[1], albedo[2] * k[2] + spec[2]]
}

fn backdrop(y: usize, h: usize) -> f32 {
    let t = if h > 1 { y as f32 / (h - 1) as f32 } else { 0.0 };
    srgb_to_linear(BACKDROP_TOP + (BACKDROP_BOTTOM - BACKDROP_TOP) * t)
}

/// The finished `frame` from its render buffer: each pixel the average of
/// its samples.
pub fn resolve(frame: &Frame, samples: &Samples) -> Image {
    let (w, h, n) = (samples.width, samples.height, samples.count().max(1));
    let plane = w * h;
    let proj = math::mul(&frame.view_to_world, &frame.world_to_clip);
    let unproject = Unproject::new(&proj, w, h);
    let lighting = Lighting::new(&frame.lights, &frame.view_to_world);
    // Where sample `k` of pixel (x, y) sits on screen.
    let at_screen = |k: usize, x: usize, y: usize| {
        let (ox, oy) = samples.offsets[k];
        (x as f32 + 0.5 + ox, y as f32 + 0.5 + oy)
    };
    let started = std::time::Instant::now();

    // A cancelled frame is thrown away: skip the occlusion, its dearest part.
    let ao = if frame.ao && !crate::cancel::Token::current().is_set() {
        ambient_occlusion(samples, &proj)
    } else {
        vec![1.0; plane * n]
    };
    let occluded = started.elapsed();

    let mut color = vec![[0.0f32; 4]; plane];
    let inv = 1.0 / n as f32;
    let glass = !samples.reveal.is_empty();
    par_rows(&mut color, w, |y, row| {
        // Transparent: nothing behind the surfaces, and alpha the coverage.
        let g = if frame.alpha { 0.0 } else { backdrop(y, h) };
        for (x, out) in row.iter_mut().enumerate() {
            let mut sum = [0.0f32; 3];
            let mut covered = 0usize;
            // What see-through surfaces cover of samples nothing opaque does.
            let mut seen = 0.0f32;
            for k in 0..n {
                let at = k * plane + y * w + x;
                let opaque = samples.depth[at] < 1.0;
                let mut c = if opaque {
                    covered += 1;
                    let nd = samples.normal[at];
                    let n = [nd[0], nd[1], nd[2]];
                    let (sx, sy) = at_screen(k, x, y);
                    let p = unproject.at(sx, sy, nd[3]);
                    let v = math::normalize(math::scale(p, -1.0));
                    let a = samples.albedo[at];
                    let c = light(frame.shading, [a[0], a[1], a[2]], n, v, p, &lighting);
                    math::scale(c, ao[at])
                } else {
                    [g, g, g]
                };
                if glass {
                    let s = samples.accum[at];
                    if s[3] > 0.0 {
                        let (r, over) = (samples.reveal[at], 1.0 / s[3]);
                        let k = 1.0 - r;
                        c = [c[0] * r + s[0] * over * k, c[1] * r + s[1] * over * k, c[2] * r + s[2] * over * k];
                        if !opaque {
                            seen += k;
                        }
                    }
                }
                sum = math::add(sum, c);
            }
            let a = if frame.alpha { (covered as f32 + seen) * inv } else { 1.0 };
            *out = [sum[0] * inv, sum[1] * inv, sum[2] * inv, a];
        }
    });
    let mut depth = vec![1.0f32; plane];
    par_rows(&mut depth, w, |y, row| {
        for (x, out) in row.iter_mut().enumerate() {
            for k in 0..n {
                *out = out.min(samples.depth[k * plane + y * w + x]);
            }
        }
    });
    // The C++ shim's TH_PLAYBLAST_TIMING line counts this inside "render".
    if std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
        eprintln!(
            "hdThPlayblast: post pass {} ms, of which ambient occlusion {} ms",
            started.elapsed().as_millis(),
            occluded.as_millis()
        );
    }
    Image { color, depth, ids: samples.ids.clone() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A camera-to-clip projection (GL, row-vector layout): perspective
    /// with `focal` and an off-centre `shift`, or orthographic.
    fn projection(focal: f64, aspect: f64, shift: [f64; 2], ortho: bool) -> Mat4 {
        let (n, f) = (0.1f64, 100.0f64);
        let (sx, sy) = (focal / aspect, focal);
        if ortho {
            [
                sx, 0.0, 0.0, 0.0, //
                0.0, sy, 0.0, 0.0, //
                0.0, 0.0, -2.0 / (f - n), 0.0, //
                shift[0], shift[1], -(f + n) / (f - n), 1.0,
            ]
        } else {
            [
                sx, 0.0, 0.0, 0.0, //
                0.0, sy, 0.0, 0.0, //
                shift[0], shift[1], -(f + n) / (f - n), -1.0, //
                0.0, 0.0, -2.0 * f * n / (f - n), 0.0,
            ]
        }
    }

    proptest! {
        /// Ambient occlusion rebuilds each sample's camera-space position
        /// from its distance: projecting it again lands on the sample's
        /// place on screen, for any lens, off-centre or orthographic.
        #[test]
        fn unproject_inverts_the_projection(
            focal in 0.3f64..4.0,
            aspect in 0.5f64..2.5,
            shift in [-0.5f64..0.5, -0.5f64..0.5],
            ortho: bool,
            (w, h) in (1usize..300, 1usize..300),
            (fx, fy) in (0.0f64..1.0, 0.0f64..1.0),
            distance in 0.2f32..90.0,
        ) {
            let proj = projection(focal, aspect, shift, ortho);
            let (x, y) = (w as f64 * fx, h as f64 * fy);
            let p = Unproject::new(&proj, w, h).at(x as f32, y as f32, distance);
            prop_assert!((p[2] + distance).abs() < 1e-4 * distance.max(1.0), "z {} at distance {}", p[2], distance);
            let c = math::transform_point4(p, &proj);
            let ndc = [c[0] / c[3], c[1] / c[3]];
            let want = [(x / w as f64) * 2.0 - 1.0, 1.0 - (y / h as f64) * 2.0];
            prop_assert!((ndc[0] - want[0]).abs() < 1e-4 && (ndc[1] - want[1]).abs() < 1e-4,
                         "screen ({}, {}) came back at {:?}, want {:?}", x, y, ndc, want);
        }

        /// The occlusion radius on screen: one scene unit at `distance` spans
        /// as many pixels as projecting its two ends says.
        #[test]
        fn pixels_per_unit_is_the_projected_size(
            focal in 0.3f64..4.0,
            ortho: bool,
            h in 8usize..2000,
            distance in 0.2f32..90.0,
        ) {
            let proj = projection(focal, 1.0, [0.0, 0.0], ortho);
            let at = |y: f32| {
                let c = math::transform_point4([0.0, y, -distance], &proj);
                (c[1] / c[3]) as f32 * h as f32 * 0.5
            };
            let want = at(0.5) - at(-0.5);
            let got = pixels_per_unit(&proj, h, distance);
            prop_assert!((got - want).abs() <= 1e-3 * want.abs().max(1.0), "{} vs {}", got, want);
        }
    }

    #[test]
    fn every_abi_number_is_a_shading_and_the_four_are_distinct() {
        let named: Vec<Shading> = (0..4).map(Shading::from_abi).collect();
        assert_eq!(named, [Shading::Lit, Shading::Clay, Shading::Albedo, Shading::Occlusion]);
        for v in [4, 7, u32::MAX] {
            assert_eq!(Shading::from_abi(v), Shading::Lit, "unknown {v} falls back to lit");
        }
        for s in named {
            assert_eq!(Shading::from_abi(s.abi()), s, "the GPU post pass gets {s:?}'s number");
        }
    }
}


