//! Motion blur and depth of field, by accumulation: the frame is drawn as
//! many layers as it has blur samples, each through its own camera at its
//! own moment of the shutter and its own point on the lens, and the layers
//! are averaged. Each layer is an ordinary render buffer layer, so both
//! rasterisers draw it as they draw any frame and the post pass
//! ([`crate::composite`]) shades it unchanged; the blur is in the geometry,
//! so what a moving or defocused surface uncovers is really there.
//!
//! The layers come in chunks of the frame's anti-aliasing samples: chunk c
//! holds layers `c * S .. (c + 1) * S`, keeps the S sub-pixel offsets of
//! [`composite::sample_offsets`], and is resolved into a picture of its own;
//! the chunks' pictures are averaged ([`average`]). The render buffer so
//! never holds more than S layers.
//!
//! Every layer's time and lens position come from fixed tables
//! ([`Plan::new`]), never a seed, so a frame is the same picture every time
//! it is drawn and a still scene does not shimmer from frame to frame. A
//! layer's time is its shutter slot in bit-reversed order, so every chunk
//! spans the whole shutter; its lens position is a point of a Vogel spiral
//! over the aperture, in an order that spreads every chunk across it.
//!
//! The camera's projection (its lens and film back) is the frame's; its
//! transform moves over the shutter ([`Blur::cameras`]). A lens position
//! moves the eye across the aperture and shears the frustum so the focus
//! plane stays put: a point at the focus distance lands on the same pixel
//! from every point of the lens, a point nearer or further off it spreads
//! over a circle of confusion `radius * |1 - focus / distance|` (camera
//! space units, at the focus plane).

use crate::composite::{self, View};
use crate::math::{self, Mat4, Vec3};
use crate::raster::{Frame, Image, Mesh};

/// Most camera and transform keys over the shutter.
pub const MAX_KEYS: usize = 4;

/// Most blur samples (layers) a frame can have.
pub const MAX_SAMPLES: usize = 64;

/// A thin lens: the aperture's radius and the focus distance, both in camera
/// space units (the scene's).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Lens {
    pub radius: f32,
    pub focus: f32,
}

impl Lens {
    /// The lens of a camera with this focal length (scene units, as Hydra's
    /// HdCamera has it), f-stop and focus distance; None for a pinhole: an
    /// f-stop of 0 (the USD default, depth of field off), a focus that is
    /// not a positive distance, or anything not finite.
    pub fn of_camera(focal_length: f32, f_stop: f32, focus: f32) -> Option<Lens> {
        let radius = focal_length / (2.0 * f_stop);
        (f_stop > 0.0 && radius > 0.0 && radius.is_finite() && focus > 0.0 && focus.is_finite())
            .then_some(Lens { radius, focus })
    }
}

/// What a frame blurs, and with how many layers.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Blur {
    /// Layers to spread over the shutter and the lens: the largest power of
    /// two not over it, at least the frame's samples, at most [`MAX_SAMPLES`].
    pub samples: usize,
    /// Motion blur: meshes take their [`Mesh::points_close`] and
    /// [`Mesh::transform_keys`], and the camera its [`Blur::cameras`].
    pub motion: bool,
    /// The camera's transform (camera to world) at `camera_keys` moments
    /// evenly spread over the shutter, the first at its opening. Fewer than
    /// two: the camera holds still at the frame's.
    pub cameras: [Mat4; MAX_KEYS],
    pub camera_keys: usize,
    /// Depth of field; None for a pinhole camera.
    pub lens: Option<Lens>,
}

impl Blur {
    pub const OFF: Blur = Blur {
        samples: 1,
        motion: false,
        cameras: [math::IDENTITY; MAX_KEYS],
        camera_keys: 0,
        lens: None,
    };

    /// The camera keys in use, when the camera moves.
    fn camera_keys(&self) -> &[Mat4] {
        if self.motion && self.camera_keys >= 2 { &self.cameras[..self.camera_keys.min(MAX_KEYS)] } else { &[] }
    }
}

impl Default for Blur {
    fn default() -> Blur {
        Blur::OFF
    }
}

/// Whether drawing `meshes` through `frame` blurs at all: a lens, a moving
/// camera, or a mesh that moves. Otherwise the frame is drawn as it always
/// was, in one pass over every layer.
pub fn active(frame: &Frame, meshes: &[Mesh]) -> bool {
    let b = &frame.blur;
    b.lens.is_some()
        || !b.camera_keys().is_empty()
        || (b.motion && meshes.iter().any(|m| moves(m)))
}

/// Whether this mesh's points or transforms change over the shutter (with
/// motion blur on).
pub(crate) fn moves(mesh: &Mesh) -> bool {
    closing_points(mesh).is_some() || !transform_keys(mesh).is_empty()
}

/// The mesh's points at the shutter's closing, when it has them for every
/// point (its `points` are then at the opening).
pub(crate) fn closing_points<'a>(mesh: &Mesh<'a>) -> Option<&'a [Vec3]> {
    mesh.points_close.filter(|c| c.len() == mesh.points.len() && !c.is_empty())
}

/// The mesh's transform keys when there is a whole number of at least two
/// per instance: key k of instance i at `k * instances + i`.
pub(crate) fn transform_keys<'a>(mesh: &Mesh<'a>) -> &'a [Mat4] {
    let n = mesh.transforms.len();
    let keys = mesh.transform_keys;
    if n > 0 && keys.len() >= 2 * n && keys.len().is_multiple_of(n) && keys.len() / n <= MAX_KEYS { keys } else { &[] }
}

/// `a + (b - a) * t`, per element.
fn lerp_matrix(a: &Mat4, b: &Mat4, t: f64) -> Mat4 {
    std::array::from_fn(|i| a[i] + (b[i] - a[i]) * t)
}

/// The key segment `s` (0 at the opening, 1 at the closing) falls in of
/// `keys` evenly spread keys, and how far along it.
fn segment(keys: usize, s: f32) -> (usize, f64) {
    let along = s.clamp(0.0, 1.0) as f64 * (keys - 1) as f64;
    let k = (along.floor() as usize).min(keys - 2);
    (k, along - k as f64)
}

/// Instance `i`'s transform at shutter time `s`, from the mesh's keys (each
/// segment interpolated per element), else its frame transform.
pub(crate) fn transform_at(mesh: &Mesh, i: usize, s: f32) -> Mat4 {
    let keys = transform_keys(mesh);
    if keys.is_empty() {
        return mesh.transforms[i];
    }
    let n = mesh.transforms.len();
    let (k, t) = segment(keys.len() / n, s);
    lerp_matrix(&keys[k * n + i], &keys[(k + 1) * n + i], t)
}

/// A point at shutter time `s` between its opening and closing positions,
/// as both rasterisers move it: `open + (close - open) * s`.
#[inline]
pub(crate) fn point_at(open: Vec3, close: Vec3, s: f32) -> Vec3 {
    [open[0] + (close[0] - open[0]) * s, open[1] + (close[1] - open[1]) * s, open[2] + (close[2] - open[2]) * s]
}

type Quat = [f64; 4];

/// A rotation's unit quaternion (x, y, z, w) from the rows of its matrix,
/// in the row-vector layout (Shepperd's method).
fn quat_of(r: [[f64; 3]; 3]) -> Quat {
    let trace = r[0][0] + r[1][1] + r[2][2];
    // Row-vector layout: r[i][j] is the transpose of the column form's.
    let m = |i: usize, j: usize| r[j][i];
    let q = if trace > 0.0 {
        let s = (trace + 1.0).sqrt() * 2.0;
        [(m(2, 1) - m(1, 2)) / s, (m(0, 2) - m(2, 0)) / s, (m(1, 0) - m(0, 1)) / s, 0.25 * s]
    } else if m(0, 0) > m(1, 1) && m(0, 0) > m(2, 2) {
        let s = (1.0 + m(0, 0) - m(1, 1) - m(2, 2)).sqrt() * 2.0;
        [0.25 * s, (m(0, 1) + m(1, 0)) / s, (m(0, 2) + m(2, 0)) / s, (m(2, 1) - m(1, 2)) / s]
    } else if m(1, 1) > m(2, 2) {
        let s = (1.0 + m(1, 1) - m(0, 0) - m(2, 2)).sqrt() * 2.0;
        [(m(0, 1) + m(1, 0)) / s, 0.25 * s, (m(1, 2) + m(2, 1)) / s, (m(0, 2) - m(2, 0)) / s]
    } else {
        let s = (1.0 + m(2, 2) - m(0, 0) - m(1, 1)).sqrt() * 2.0;
        [(m(0, 2) + m(2, 0)) / s, (m(1, 2) + m(2, 1)) / s, 0.25 * s, (m(1, 0) - m(0, 1)) / s]
    };
    let len = q.iter().map(|v| v * v).sum::<f64>().sqrt().max(1e-300);
    q.map(|v| v / len)
}

/// The rows of a unit quaternion's rotation, row-vector layout.
fn rows_of(q: Quat) -> [[f64; 3]; 3] {
    let [x, y, z, w] = q;
    let column = [
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
        [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
        [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
    ];
    std::array::from_fn(|i| std::array::from_fn(|j| column[j][i]))
}

/// The shorter turn from `a` to `b`, a share `t` of the way.
fn slerp(a: Quat, mut b: Quat, t: f64) -> Quat {
    let mut cos = (0..4).map(|i| a[i] * b[i]).sum::<f64>();
    if cos < 0.0 {
        b = b.map(|v| -v);
        cos = -cos;
    }
    let (wa, wb) = if cos > 0.9995 {
        (1.0 - t, t)
    } else {
        let angle = cos.acos();
        let sin = angle.sin();
        (((1.0 - t) * angle).sin() / sin, (t * angle).sin() / sin)
    };
    let q: Quat = std::array::from_fn(|i| a[i] * wa + b[i] * wb);
    let len = q.iter().map(|v| v * v).sum::<f64>().sqrt().max(1e-300);
    q.map(|v| v / len)
}

/// A rigid camera between two of its transforms: the eye along the line,
/// the axes turned the shorter way, each axis's length (a scaled camera)
/// interpolated too. Identical keys give the key back exactly.
fn camera_between(a: &Mat4, b: &Mat4, t: f64) -> Mat4 {
    if a == b || t == 0.0 {
        return *a;
    }
    if t == 1.0 {
        return *b;
    }
    let axes = |m: &Mat4| -> ([[f64; 3]; 3], [f64; 3]) {
        let mut rows = [[0.0; 3]; 3];
        let mut lengths = [1.0; 3];
        for r in 0..3 {
            let v = [m[r * 4], m[r * 4 + 1], m[r * 4 + 2]];
            let len = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt().max(1e-300);
            lengths[r] = len;
            rows[r] = v.map(|x| x / len);
        }
        (rows, lengths)
    };
    let ((ra, la), (rb, lb)) = (axes(a), axes(b));
    let rows = rows_of(slerp(quat_of(ra), quat_of(rb), t));
    let mut out = math::IDENTITY;
    for r in 0..3 {
        let len = la[r] + (lb[r] - la[r]) * t;
        for c in 0..3 {
            out[r * 4 + c] = rows[r][c] * len;
        }
        out[12 + r] = a[12 + r] + (b[12 + r] - a[12 + r]) * t;
    }
    out
}

/// Inverse of an affine transform (the camera's: rotation, scale and a
/// translation, no projection).
pub(crate) fn affine_inverse(m: &Mat4) -> Mat4 {
    let a = |r: usize, c: usize| m[r * 4 + c];
    let det = a(0, 0) * (a(1, 1) * a(2, 2) - a(1, 2) * a(2, 1))
        - a(0, 1) * (a(1, 0) * a(2, 2) - a(1, 2) * a(2, 0))
        + a(0, 2) * (a(1, 0) * a(2, 1) - a(1, 1) * a(2, 0));
    let inv_det = if det.abs() > 1e-300 { 1.0 / det } else { 0.0 };
    let mut out = math::IDENTITY;
    for r in 0..3 {
        for c in 0..3 {
            // inverse(r, c) = cofactor(c, r) / det.
            let rows: Vec<usize> = (0..3).filter(|&i| i != c).collect();
            let cols: Vec<usize> = (0..3).filter(|&i| i != r).collect();
            let minor = a(rows[0], cols[0]) * a(rows[1], cols[1]) - a(rows[0], cols[1]) * a(rows[1], cols[0]);
            out[r * 4 + c] = if (r + c) % 2 == 0 { minor } else { -minor } * inv_det;
        }
    }
    for c in 0..3 {
        out[12 + c] = -(0..3).map(|k| m[12 + k] * out[k * 4 + c]).sum::<f64>();
    }
    out
}

/// One layer's camera.
#[derive(Clone, Copy)]
pub(crate) struct LayerCamera {
    pub world_to_clip: Mat4,
    /// Its eye and axes, which the render buffer's normals and distances
    /// are measured from.
    pub view: View,
    /// Its moment of the shutter: 0 at the opening, 1 at the closing.
    pub time: f32,
}

/// The layers of a blurred frame, chunk by chunk.
pub(crate) struct Plan {
    pub chunks: Vec<Vec<LayerCamera>>,
}

/// Blur layers for `requested` samples and `per_chunk` anti-aliasing
/// samples (a power of two): a power of two from `per_chunk` to
/// [`MAX_SAMPLES`].
pub(crate) fn layer_count(requested: usize, per_chunk: usize) -> usize {
    let mut n = 1;
    while n * 2 <= requested.min(MAX_SAMPLES) {
        n *= 2;
    }
    n.max(per_chunk.max(1))
}

/// `g`'s bits reversed over `bits` bits.
fn bit_reverse(g: usize, bits: u32) -> usize {
    if bits == 0 { 0 } else { g.reverse_bits() >> (usize::BITS - bits) }
}

/// Layer `g`'s shutter time of `n` (a power of two): its slot, bit
/// reversed, at the middle of the slot. Every run of 2^k layers from a
/// multiple of 2^k takes one slot of each of 2^k equal parts of the shutter.
pub(crate) fn layer_time(g: usize, n: usize) -> f32 {
    (bit_reverse(g, n.trailing_zeros()) as f32 + 0.5) / n as f32
}

/// Which of a chunk's S shutter slots each of its sub-pixel offsets
/// ([`composite::sample_offsets`]) takes, chunk 0's; chunk c turns it c
/// places, so over S chunks every offset takes every slot. Searched for
/// (offline) so that over 1, 2, 4... chunks an offset's place in its pixel
/// says as little as can be about its moment: paired in order, the offsets
/// of a column or row of the pixel took the same part of the shutter every
/// chunk, and a thin thing moving across a pixel row smeared as dashes in
/// alternate rows.
const PAIRINGS: [&[usize]; 5] = [
    &[0],
    &[0, 1],
    &[0, 1, 3, 2],
    &[2, 0, 5, 7, 3, 4, 1, 6],
    &[5, 3, 11, 8, 4, 0, 13, 1, 2, 7, 12, 15, 10, 14, 9, 6],
];

/// The layer whose shutter slot (in [`layer_time`]'s order) sub-pixel offset
/// `k` of chunk `c` takes, of chunks of `per_chunk`.
pub(crate) fn time_layer(c: usize, k: usize, per_chunk: usize) -> usize {
    let pairing = PAIRINGS.iter().find(|p| p.len() == per_chunk).copied();
    let j = pairing.map_or(k, |p| p[(k + c) % per_chunk]);
    c * per_chunk + j
}

/// Layer `g`'s point on the unit disk of `n` (a power of two): point
/// `(5g + 3) mod n` of a Vogel spiral, which spreads every chunk over the
/// whole aperture (rings from the middle out, each a golden angle round).
pub(crate) fn lens_point(g: usize, n: usize) -> (f32, f32) {
    let j = (g * 5 + 3) % n;
    let r = ((j as f32 + 0.5) / n as f32).sqrt();
    let angle = j as f32 * 2.399_963_2;
    (r * angle.cos(), r * angle.sin())
}

impl Plan {
    /// Every layer's camera for `frame`, in chunks of `per_chunk` (the
    /// frame's sample offsets).
    pub fn new(frame: &Frame, per_chunk: usize) -> Plan {
        let b = &frame.blur;
        let per_chunk = per_chunk.max(1);
        let n = layer_count(b.samples, per_chunk);
        let proj = math::mul(&frame.view_to_world, &frame.world_to_clip);
        let keys = b.camera_keys();
        let layer = |c: usize, k: usize| -> LayerCamera {
            let g = c * per_chunk + k;
            let time = layer_time(time_layer(c, k, per_chunk), n);
            let camera = if keys.is_empty() {
                None
            } else {
                let (k, t) = segment(keys.len(), time);
                Some(camera_between(&keys[k], &keys[k + 1], t))
            };
            let base = camera.unwrap_or(frame.view_to_world);
            let Some(lens) = b.lens else {
                return LayerCamera {
                    world_to_clip: camera.map_or(frame.world_to_clip, |c| math::mul(&affine_inverse(&c), &proj)),
                    view: View::new(&base),
                    time,
                };
            };
            let (dx, dy) = lens_point(g, n);
            let (lx, ly) = ((dx * lens.radius) as f64, (dy * lens.radius) as f64);
            let f = lens.focus as f64;
            // Camera space from the centre of the lens to the layer's eye,
            // sheared so the focus plane (z = -f) does not move: x' = x -
            // lx - lx z / f.
            let mut shear = math::IDENTITY;
            shear[8] = -lx / f;
            shear[9] = -ly / f;
            shear[12] = -lx;
            shear[13] = -ly;
            let mut offset = math::IDENTITY;
            offset[12] = lx;
            offset[13] = ly;
            let world_to_view = affine_inverse(&base);
            LayerCamera {
                world_to_clip: math::mul(&math::mul(&world_to_view, &shear), &proj),
                view: View::new(&math::mul(&offset, &base)),
                time,
            }
        };
        let chunks = (0..n / per_chunk).map(|c| (0..per_chunk).map(|k| layer(c, k)).collect()).collect();
        Plan { chunks }
    }
}

/// The chunks' pictures averaged, pairwise so that equal pictures average
/// to themselves exactly; depth is the nearest of them. With `alpha` (a
/// transparent background) alpha is averaged as the colour is, so it is the
/// coverage over the shutter and the lens; without, it is 1.
pub fn average(mut images: Vec<Image>, alpha: bool) -> Image {
    assert!(!images.is_empty(), "a frame has at least one chunk");
    let count = images.len();
    while images.len() > 1 {
        let mut next = Vec::with_capacity(images.len().div_ceil(2));
        let mut it = images.into_iter();
        while let Some(mut a) = it.next() {
            if let Some(b) = it.next() {
                for (x, y) in a.color.iter_mut().zip(&b.color) {
                    for c in 0..4 {
                        x[c] += y[c];
                    }
                }
                for (x, y) in a.depth.iter_mut().zip(&b.depth) {
                    *x = x.min(*y);
                }
            }
            next.push(a);
        }
        images = next;
    }
    let mut out = images.pop().expect("one left");
    let inv = 1.0 / count as f32;
    for p in &mut out.color {
        *p = [p[0] * inv, p[1] * inv, p[2] * inv, if alpha { p[3] * inv } else { 1.0 }];
    }
    out
}

/// How many layers a frame with `offsets` sample offsets draws: one chunk
/// unblurred.
pub fn layers(frame: &Frame, meshes: &[Mesh]) -> usize {
    let s = composite::sample_offsets(frame.samples).len();
    if active(frame, meshes) { layer_count(frame.blur.samples, s) } else { s }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn rotation(yaw: f64, pitch: f64) -> Mat4 {
        let (cy, sy, cp, sp) = (yaw.cos(), yaw.sin(), pitch.cos(), pitch.sin());
        let y: Mat4 = [cy, 0.0, -sy, 0.0, 0.0, 1.0, 0.0, 0.0, sy, 0.0, cy, 0.0, 0.0, 0.0, 0.0, 1.0];
        let p: Mat4 = [1.0, 0.0, 0.0, 0.0, 0.0, cp, sp, 0.0, 0.0, -sp, cp, 0.0, 0.0, 0.0, 0.0, 1.0];
        math::mul(&p, &y)
    }

    /// Keys are evenly spread over the shutter and each segment between
    /// two is interpolated on its own: three keys along x at 0, 1 and 5
    /// (a move that speeds up) put the instance at 0.5 a quarter in and at
    /// 3 three quarters in, and exactly on the keys at 0, 1/2 and 1.
    #[test]
    fn transform_keys_are_interpolated_segment_by_segment() {
        let at = |x: f64| {
            let mut m = math::IDENTITY;
            m[12] = x;
            m
        };
        let points = [[0.0f32; 3]];
        let triangles = [[0u32, 0, 0]];
        let transforms = [at(9.0), at(-9.0)];
        // Two instances, key-major: instance 1 moves the other way.
        let keys = [at(0.0), at(0.0), at(1.0), at(-1.0), at(5.0), at(-5.0)];
        let mesh = Mesh {
            points: &points, triangles: &triangles, corner_normals: None, corner_colors: None, corner_uvs: None,
            base_color: [1.0; 3], tiles: Vec::new(), transforms: &transforms, key: None,
            points_close: None, transform_keys: &keys,
        };
        for (s, x) in [(0.0f32, 0.0), (0.25, 0.5), (0.5, 1.0), (0.75, 3.0), (1.0, 5.0)] {
            assert!((transform_at(&mesh, 0, s)[12] - x).abs() < 1e-9, "at {s}: {}", transform_at(&mesh, 0, s)[12]);
            assert!((transform_at(&mesh, 1, s)[12] + x).abs() < 1e-9);
        }
        // Without keys, or with a key count that is not whole, the frame's.
        let still = Mesh { transform_keys: &keys[..5], ..mesh };
        assert_eq!(transform_at(&still, 1, 0.5)[12], -9.0);
    }

    #[test]
    fn layer_count_is_a_power_of_two_between_the_chunk_and_the_cap() {
        assert_eq!(layer_count(16, 8), 16);
        assert_eq!(layer_count(20, 8), 16);
        assert_eq!(layer_count(4, 8), 8);
        assert_eq!(layer_count(1000, 1), MAX_SAMPLES);
        assert_eq!(layer_count(0, 1), 1);
    }

    proptest! {
        /// Every chunk of 2^k layers takes one slot of each 2^k-th of the
        /// shutter: the times are stratified at every chunk size.
        #[test]
        fn every_chunk_spans_the_whole_shutter(bits in 0u32..7, chunk_bits in 0u32..7) {
            let n = 1usize << bits;
            let per = 1usize << chunk_bits.min(bits);
            for c in 0..n / per {
                let mut slots: Vec<usize> = (0..per)
                    .map(|k| (layer_time(c * per + k, n) * per as f32).floor() as usize)
                    .collect();
                slots.sort();
                prop_assert_eq!(slots, (0..per).collect::<Vec<_>>());
            }
        }

        /// Through the pairings too, every chunk's layers take one slot of
        /// each S-th of the shutter, and over S chunks each sub-pixel offset
        /// takes every slot.
        #[test]
        fn every_offset_takes_every_slot(chunk_bits in 0u32..5, more in 0u32..3) {
            let per = 1usize << chunk_bits;
            let n = (per << more).min(MAX_SAMPLES).max(per);
            let chunks = n / per;
            // seen[k][s]: offset k has drawn in the s-th S-th of the shutter.
            let mut seen = vec![vec![false; per]; per];
            for c in 0..chunks {
                let mut slots: Vec<usize> = (0..per)
                    .map(|k| (layer_time(time_layer(c, k, per), n) * per as f32).floor() as usize)
                    .collect();
                for (k, &s) in slots.iter().enumerate() {
                    seen[k][s] = true;
                }
                slots.sort();
                prop_assert_eq!(slots, (0..per).collect::<Vec<_>>());
            }
            if chunks >= per {
                // Not one offset held to one part of the shutter (thin
                // movers then smear as dashes, row by row).
                for (k, s) in seen.iter().enumerate() {
                    let taken = s.iter().filter(|&&v| v).count();
                    prop_assert_eq!(taken, per, "offset {} takes {} of the {} slots over {} chunks", k, taken, per, chunks);
                }
            }
        }

        /// The lens points are distinct, inside the unit disk, and every
        /// layer's point is its own.
        #[test]
        fn lens_points_cover_the_disk_once(bits in 0u32..7) {
            let n = 1usize << bits;
            let points: Vec<(f32, f32)> = (0..n).map(|g| lens_point(g, n)).collect();
            for (i, p) in points.iter().enumerate() {
                prop_assert!(p.0 * p.0 + p.1 * p.1 <= 1.0 + 1e-6);
                for q in &points[i + 1..] {
                    prop_assert!((p.0 - q.0).abs() + (p.1 - q.1).abs() > 1e-6);
                }
            }
            if n >= 4 {
                // Centred: the mean is near the middle of the disk.
                let (sx, sy) = points.iter().fold((0.0, 0.0), |(a, b), p| (a + p.0, b + p.1));
                prop_assert!((sx / n as f32).hypot(sy / n as f32) < 0.35);
            }
        }

        /// Between two camera transforms the camera stays rigid, starts and
        /// ends on the keys, and its eye runs along the line between them.
        #[test]
        fn a_moving_camera_stays_rigid(
            yaw in -3.0f64..3.0, pitch in -1.4f64..1.4, yaw2 in -3.0f64..3.0, pitch2 in -1.4f64..1.4,
            eye in [-5.0f64..5.0, -5.0f64..5.0, -5.0f64..5.0], eye2 in [-5.0f64..5.0, -5.0f64..5.0, -5.0f64..5.0],
            t in 0.0f64..1.0,
        ) {
            let mut a = rotation(yaw, pitch);
            a[12..15].copy_from_slice(&eye);
            let mut b = rotation(yaw2, pitch2);
            b[12..15].copy_from_slice(&eye2);
            prop_assert_eq!(camera_between(&a, &b, 0.0), a);
            prop_assert_eq!(camera_between(&a, &a, t), a);
            let m = camera_between(&a, &b, t);
            for r in 0..3 {
                for s in 0..3 {
                    let d: f64 = (0..3).map(|c| m[r * 4 + c] * m[s * 4 + c]).sum();
                    prop_assert!((d - if r == s { 1.0 } else { 0.0 }).abs() < 1e-9, "axes {r} {s}: {d}");
                }
                prop_assert!((m[12 + r] - (eye[r] + (eye2[r] - eye[r]) * t)).abs() < 1e-9);
            }
            let end = camera_between(&a, &b, 1.0);
            prop_assert_eq!(end, b);
            let inv = affine_inverse(&m);
            let id = math::mul(&m, &inv);
            for i in 0..16 {
                prop_assert!((id[i] - math::IDENTITY[i]).abs() < 1e-9);
            }
        }
    }
}
