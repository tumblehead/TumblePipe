//! The rasteriser: triangles in, a render buffer out ([`Samples`]: colour,
//! normal and depth per sample), which [`crate::composite`] lights and
//! filters into the image.
//!
//! Work is split two ways. Instances are processed in batches whose
//! transformed vertices fit [`VERTEX_BUDGET`], so a shot with ten thousand
//! instanced trees never holds all of them in memory at once. Within a batch
//! the image is cut into horizontal bands and each thread owns whole bands,
//! so the depth test needs no locking.

use crate::blur::{self, Blur};
use crate::composite::{self, Samples, Shading, View};
use crate::math::{self, Mat4, Vec3};
use crate::pool::{self, threads};
use crate::texture::Texture;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

/// Transformed vertices held at once, across a batch of instances.
pub const VERTEX_BUDGET: usize = 4_000_000;

/// Albedo of geometry with no colour of its own (display colour default).
pub const DEFAULT_ALBEDO: Vec3 = [0.5, 0.5, 0.5];

pub struct Mesh<'a> {
    pub points: &'a [Vec3],
    /// Vertex indices into `points`, three per triangle.
    pub triangles: &'a [[u32; 3]],
    /// Per triangle corner (3 per triangle); computed smooth when absent.
    pub corner_normals: Option<&'a [Vec3]>,
    pub corner_colors: Option<&'a [Vec3]>,
    pub corner_uvs: Option<&'a [[f32; 2]]>,
    pub base_color: Vec3,
    /// (UDIM tile, texture); a single tile 0 is a plain texture.
    pub tiles: Vec<(i32, &'a Texture)>,
    /// One per instance; object to world.
    pub transforms: &'a [Mat4],
    /// Who this geometry belongs to, for the GPU path to keep it on the
    /// card between frames; `None`, drawn from scratch each frame.
    pub key: Option<MeshKey>,
    /// With motion blur ([`crate::blur`]): the points at the shutter's
    /// closing, one per point (`points` are then at its opening); points in
    /// between are interpolated linearly. `None`: the points hold still.
    pub points_close: Option<&'a [Vec3]>,
    /// With motion blur: each instance's transform at two to
    /// [`crate::blur::MAX_KEYS`] moments evenly spread over the shutter,
    /// key-major (key k of instance i at `k * transforms.len() + i`);
    /// empty, the instances hold still at `transforms`.
    pub transform_keys: &'a [Mat4],
}

/// A mesh's identity across frames, from the shim: `id` is the Hydra mesh's
/// (never reused in the process), and `version` changes whenever its points,
/// topology or primvars are rebuilt.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MeshKey {
    pub id: u64,
    pub version: u64,
}

#[derive(Clone, Copy)]
pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub world_to_clip: Mat4,
    /// Camera to world: its rows are the camera's right, up and back axes
    /// and its position (the camera looks down -z).
    pub view_to_world: Mat4,
    /// Samples per pixel ([`composite::sample_offsets`]); 1 = no anti-aliasing.
    pub samples: usize,
    pub shading: Shading,
    /// Screen-space ambient occlusion.
    pub ao: bool,
    /// Motion blur and depth of field ([`crate::blur`]); [`Blur::OFF`] for
    /// neither.
    pub blur: Blur,
    /// A transparent background ([`crate::composite`]): no backdrop, colour
    /// premultiplied by coverage, alpha the coverage. Off: the backdrop
    /// gradient behind everything and alpha 1.
    pub alpha: bool,
}

pub struct Image {
    /// Linear RGBA, row 0 at the top.
    pub color: Vec<[f32; 4]>,
    /// Normalised device depth in [0, 1], 1 = far / nothing drawn.
    pub depth: Vec<f32>,
    /// Per pixel, the mesh and instance (indices into the meshes rendered
    /// and their transforms) whose surface the pixel's first sample sees,
    /// [-1, -1] where it sees none: what a viewport picks and highlights
    /// by. Empty when the path does not record them (the GPU path).
    pub ids: Vec<[i32; 2]>,
}

pub(crate) fn srgb_to_linear(v: f32) -> f32 {
    if v <= 0.040_45 { v / 12.92 } else { ((v + 0.055) / 1.055).powf(2.4) }
}

/// Smooth vertex normals for a mesh that authored none: each triangle's
/// area-weighted normal added to its three vertices.
pub fn smooth_normals(points: &[Vec3], triangles: &[[u32; 3]]) -> Vec<Vec3> {
    let mut normals = vec![[0.0f32; 3]; points.len()];
    for tri in triangles {
        let [a, b, c] = tri.map(|i| i as usize);
        if a >= points.len() || b >= points.len() || c >= points.len() {
            continue;
        }
        let n = math::cross(math::sub(points[b], points[a]), math::sub(points[c], points[a]));
        for i in [a, b, c] {
            normals[i] = math::add(normals[i], n);
        }
    }
    normals.into_iter().map(math::normalize).collect()
}

/// Houdini's cusp angle, the Normal SOP's default: a polygonal mesh with no
/// normals is smoothed across edges that bend less than this and kept hard
/// across sharper ones, as the viewport draws it.
pub const CUSP_DEGREES: f32 = 60.0;

/// Per-corner normals for a polygonal mesh that authored none: at each
/// corner, the area-weighted normals of the triangles around its point that
/// face within `cusp_degrees` of this triangle. Curved surfaces come out
/// smooth (where every bend is under the cusp, exactly `smooth_normals`), a
/// box keeps its hard edges. A triangle indexing past `points` gets +z.
pub fn cusp_normals(points: &[Vec3], triangles: &[[u32; 3]], cusp_degrees: f32) -> Vec<Vec3> {
    let in_range = |tri: &[u32; 3]| tri.iter().all(|&i| (i as usize) < points.len());
    let face: Vec<Option<Vec3>> = triangles
        .iter()
        .map(|tri| {
            in_range(tri).then(|| {
                let [a, b, c] = tri.map(|i| points[i as usize]);
                math::cross(math::sub(b, a), math::sub(c, a))
            })
        })
        .collect();
    // Unit face normals; None for a degenerate or out-of-range triangle.
    let unit: Vec<Option<Vec3>> = face
        .iter()
        .map(|f| f.and_then(|n| {
            let len = math::dot(n, n).sqrt();
            (len > 1e-20).then(|| math::scale(n, 1.0 / len))
        }))
        .collect();

    // The triangles around each point, in triangle order (compressed rows).
    let mut start = vec![0usize; points.len() + 1];
    for tri in triangles.iter().filter(|tri| in_range(tri)) {
        for &i in tri {
            start[i as usize + 1] += 1;
        }
    }
    for i in 0..points.len() {
        start[i + 1] += start[i];
    }
    let mut fill = start.clone();
    let mut around = vec![0u32; start[points.len()]];
    for (t, tri) in triangles.iter().enumerate().filter(|(_, tri)| in_range(tri)) {
        for &i in tri {
            around[fill[i as usize]] = t as u32;
            fill[i as usize] += 1;
        }
    }

    let cos_cusp = cusp_degrees.to_radians().cos();
    let mut normals = Vec::with_capacity(triangles.len() * 3);
    for (t, tri) in triangles.iter().enumerate() {
        if face[t].is_none() {
            normals.extend([[0.0, 0.0, 1.0]; 3]);
            continue;
        }
        let own = unit[t];
        for &i in tri {
            let mut sum = [0.0f32; 3];
            for &s in &around[start[i as usize]..start[i as usize + 1]] {
                let s = s as usize;
                // A degenerate triangle's corner takes every neighbour.
                let within = match (own, unit[s]) {
                    (Some(o), Some(u)) => math::dot(o, u) >= cos_cusp,
                    (None, _) => true,
                    (Some(_), None) => false,
                };
                if within {
                    sum = math::add(sum, face[s].unwrap_or([0.0; 3]));
                }
            }
            normals.push(math::normalize(sum));
        }
    }
    normals
}

/// One instance's vertices, transformed.
struct InstanceVerts {
    mesh: usize,
    clip: Vec<[f32; 4]>,
    world: Vec<Vec3>,
    /// World normals: per vertex when smooth, per corner when authored.
    normals: Vec<Vec3>,
}

/// One instance's vertices as a pass draws them: its own, transformed for
/// the pass, or a still instance's shared by every pass of the frame
/// ([`Shared`]), with a clip of its own when the passes' cameras differ.
#[derive(Clone, Copy)]
struct Inst<'v> {
    mesh: usize,
    clip: &'v [[f32; 4]],
    world: &'v [Vec3],
    normals: &'v [Vec3],
}

impl InstanceVerts {
    fn view(&self) -> Inst<'_> {
        Inst { mesh: self.mesh, clip: &self.clip, world: &self.world, normals: &self.normals }
    }
}

/// A clip-space vertex with everything interpolated across a triangle.
#[derive(Clone, Copy)]
struct Corner {
    clip: [f32; 4],
    world: Vec3,
    normal: Vec3,
    color: Vec3,
    uv: [f32; 2],
}

fn lerp_corner(a: &Corner, b: &Corner, t: f32) -> Corner {
    let l3 = |x: Vec3, y: Vec3| math::add(x, math::scale(math::sub(y, x), t));
    let mut clip = [0.0; 4];
    for i in 0..4 {
        clip[i] = a.clip[i] + (b.clip[i] - a.clip[i]) * t;
    }
    Corner {
        clip,
        world: l3(a.world, b.world),
        normal: l3(a.normal, b.normal),
        color: l3(a.color, b.color),
        uv: [a.uv[0] + (b.uv[0] - a.uv[0]) * t, a.uv[1] + (b.uv[1] - a.uv[1]) * t],
    }
}

/// A triangle clipped to the near plane: up to four corners, on the stack
/// (a Vec per triangle, and another for its screen positions, were 13M
/// allocations a 6.6M triangle frame).
struct Poly {
    corners: [Corner; 4],
    len: usize,
}

/// Clip a triangle to the near plane (z >= -w, GL clip space).
fn clip_near(tri: [Corner; 3]) -> Poly {
    let inside = |c: &Corner| c.clip[2] + c.clip[3] >= 0.0;
    let mut out = Poly { corners: [tri[0]; 4], len: 0 };
    if tri.iter().all(inside) {
        out.corners[..3].copy_from_slice(&tri);
        out.len = 3;
        return out;
    }
    for i in 0..3 {
        let (a, b) = (&tri[i], &tri[(i + 1) % 3]);
        let (da, db) = (a.clip[2] + a.clip[3], b.clip[2] + b.clip[3]);
        if da >= 0.0 {
            out.corners[out.len] = *a;
            out.len += 1;
        }
        if (da >= 0.0) != (db >= 0.0) {
            out.corners[out.len] = lerp_corner(a, b, da / (da - db));
            out.len += 1;
        }
    }
    out
}

/// The most samples a pixel can have ([`composite::sample_offsets`]).
const MAX_SAMPLES: usize = 16;

/// Rows `y0..` of the render buffer, one slice per layer ([`Samples`]).
struct Band<'a> {
    y0: usize,
    albedo: Vec<&'a mut [[f32; 4]]>,
    normal: Vec<&'a mut [[f32; 4]]>,
    depth: Vec<&'a mut [f32]>,
    /// The first layer's ids ([`Samples::ids`]), in the pass that draws it.
    ids: Option<&'a mut [[i32; 2]]>,
}

struct Raster<'a> {
    width: usize,
    height: usize,
    meshes: &'a [Mesh<'a>],
    view: View,
    /// Each layer's sample offset from the pixel centre, and their least
    /// and greatest x and y.
    offsets: &'a [(f32, f32)],
    spread: [f32; 4],
}

impl Raster<'_> {
    fn albedo(&self, mesh: &Mesh, c: &Corner) -> Vec3 {
        if let (Some(_), false) = (mesh.corner_uvs, mesh.tiles.is_empty()) {
            let (u, v) = (c.uv[0], c.uv[1]);
            let texture = if mesh.tiles.len() == 1 && mesh.tiles[0].0 == 0 {
                Some(mesh.tiles[0].1)
            } else {
                let tile = 1001 + u.floor() as i32 + 10 * v.floor() as i32;
                mesh.tiles.iter().find(|(t, _)| *t == tile).map(|(_, tex)| *tex)
            };
            if let Some(texture) = texture {
                return texture.sample(u, v);
            }
        }
        c.color
    }

    /// Draw a polygon (3 or 4 corners, as a fan) at clip positions `clip`,
    /// corner k's attributes being `corner(k)`: fetched only once one of a
    /// triangle's samples passes the depth test, as most of a dense mesh's
    /// triangles that reach a sample's box cover none or lie behind.
    fn draw(&self, band: &mut Band, mesh: &Mesh, clip: &[[f32; 4]], corner: impl Fn(usize) -> Corner,
            id: [i32; 2]) {
        let rows = band.depth[0].len() / self.width;
        let (w, h) = (self.width as f32, self.height as f32);
        let [ox_lo, ox_hi, oy_lo, oy_hi] = self.spread;
        // Screen-space vertices: x right, y down, z in [0, 1], 1/w.
        let mut screen = [[0.0f32; 4]; 4];
        for (s, c) in screen.iter_mut().zip(clip) {
            let iw = 1.0 / c[3];
            *s = [
                (c[0] * iw * 0.5 + 0.5) * w,
                (0.5 - c[1] * iw * 0.5) * h,
                c[2] * iw * 0.5 + 0.5,
                iw,
            ];
        }
        for i in 1..clip.len().saturating_sub(1) {
            let idx = [0, i, i + 1];
            let s = idx.map(|k| screen[k]);
            let area = (s[1][0] - s[0][0]) * (s[2][1] - s[0][1])
                - (s[2][0] - s[0][0]) * (s[1][1] - s[0][1]);
            if area.abs() < 1e-12 || !area.is_finite() {
                continue;
            }
            // The pixels that have a sample inside the triangle's bounds:
            // sample k of pixel x sits at x + 0.5 + its offset.
            let lo = |a: usize| s.iter().map(|p| p[a]).fold(f32::MAX, f32::min);
            let hi = |a: usize| s.iter().map(|p| p[a]).fold(f32::MIN, f32::max);
            let min_x = (lo(0) - 0.5 - ox_hi).ceil().max(0.0) as usize;
            let max_x = ((hi(0) - 0.5 - ox_lo).floor() as i64).min(self.width as i64 - 1);
            let min_y = ((lo(1) - 0.5 - oy_hi).ceil() as i64).max(band.y0 as i64);
            let max_y = ((hi(1) - 0.5 - oy_lo).floor() as i64).min((band.y0 + rows) as i64 - 1);
            if max_x < min_x as i64 || max_y < min_y {
                continue;
            }
            let mut fetched: Option<[Corner; 3]> = None;
            // Each barycentric weight is an edge function over the area, so
            // a sample's weights are its pixel centre's plus a fixed step.
            let edges = [(s[1], s[2]), (s[2], s[0]), (s[0], s[1])];
            let mut steps = [[0.0f32; 3]; MAX_SAMPLES];
            for (step, &(ox, oy)) in steps.iter_mut().zip(self.offsets) {
                *step = edges.map(|(a, b)| ((b[0] - a[0]) * oy - (b[1] - a[1]) * ox) / area);
            }
            for y in min_y as usize..=max_y as usize {
                let py = y as f32 + 0.5;
                for x in min_x..=max_x as usize {
                    let px = x as f32 + 0.5;
                    let centre = edges.map(|(a, b)| ((b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])) / area);
                    let at = (y - band.y0) * self.width + x;
                    for (k, step) in steps.iter().take(self.offsets.len()).enumerate() {
                        let (b0, b1, b2) = (centre[0] + step[0], centre[1] + step[1], centre[2] + step[2]);
                        if b0 < 0.0 || b1 < 0.0 || b2 < 0.0 {
                            continue;
                        }
                        let z = b0 * s[0][2] + b1 * s[1][2] + b2 * s[2][2];
                        if !(z >= 0.0 && z < band.depth[k][at]) {
                            continue;
                        }
                        let corners = fetched.get_or_insert_with(|| idx.map(&corner));
                        let corners = [&corners[0], &corners[1], &corners[2]];
                        // Perspective-correct weights.
                        let (p0, p1, p2) = (b0 * s[0][3], b1 * s[1][3], b2 * s[2][3]);
                        let sum = p0 + p1 + p2;
                        let (p0, p1, p2) = (p0 / sum, p1 / sum, p2 / sum);
                        let mix3 = |f: fn(&Corner) -> Vec3| {
                            let (a, b, c) = (f(corners[0]), f(corners[1]), f(corners[2]));
                            [
                                a[0] * p0 + b[0] * p1 + c[0] * p2,
                                a[1] * p0 + b[1] * p1 + c[1] * p2,
                                a[2] * p0 + b[2] * p1 + c[2] * p2,
                            ]
                        };
                        let (u0, u1, u2) = (corners[0].uv, corners[1].uv, corners[2].uv);
                        let c = Corner {
                            clip: [0.0; 4],
                            world: mix3(|c| c.world),
                            normal: mix3(|c| c.normal),
                            color: mix3(|c| c.color),
                            uv: [
                                u0[0] * p0 + u1[0] * p1 + u2[0] * p2,
                                u0[1] * p0 + u1[1] * p1 + u2[1] * p2,
                            ],
                        };
                        let rgb = self.albedo(mesh, &c);
                        band.depth[k][at] = z;
                        band.albedo[k][at] = [rgb[0], rgb[1], rgb[2], 1.0];
                        band.normal[k][at] = self.view.buffer_normal(c.normal, c.world);
                        if k == 0 {
                            if let Some(ids) = band.ids.as_deref_mut() {
                                ids[at] = id;
                            }
                        }
                    }
                }
            }
        }
    }

    fn draw_triangle(&self, band: &mut Band, inst: &Inst, t: usize, id: [i32; 2]) {
        let mesh = &self.meshes[inst.mesh];
        let vi = mesh.triangles[t].map(|i| i as usize);
        let per_corner_normals = mesh.corner_normals.is_some();
        let corner = |k: usize| Corner {
            clip: inst.clip[vi[k]],
            world: inst.world[vi[k]],
            normal: if per_corner_normals { inst.normals[t * 3 + k] } else { inst.normals[vi[k]] },
            color: mesh.corner_colors.map_or(mesh.base_color, |c| c[t * 3 + k]),
            uv: mesh.corner_uvs.map_or([0.0, 0.0], |u| u[t * 3 + k]),
        };
        let clip = vi.map(|v| inst.clip[v]);
        if clip.iter().all(|c| c[2] + c[3] >= 0.0) {
            self.draw(band, mesh, &clip, corner, id);
            return;
        }
        let poly = clip_near([corner(0), corner(1), corner(2)]);
        if poly.len >= 3 {
            let clip: [[f32; 4]; 4] = std::array::from_fn(|k| poly.corners[k].clip);
            self.draw(band, mesh, &clip[..poly.len], |k| poly.corners[k], id);
        }
    }
}

/// Where a pixel's samples fall along each axis, for the binner to hold a
/// triangle's bounds to. Every pattern's samples sit on sixteenths of a
/// pixel ([`composite::sample_offsets`]), so per axis two tables answer
/// it: for each sixteenth s of a pixel (0 to 16), the first sample at or
/// after it and the last at or before it, in sixteenths, past the pixel's
/// ends when there is none inside.
pub(crate) struct SampleGrid {
    axes: [([i32; 17], [i32; 17]); 2],
}

impl SampleGrid {
    pub fn new(offsets: &[(f32, f32)]) -> SampleGrid {
        let axis = |of: fn(&(f32, f32)) -> f32| {
            let at: Vec<i32> = offsets.iter().map(|o| ((0.5 + of(o)) * 16.0).round() as i32).collect();
            let (lowest, highest) = (*at.iter().min().unwrap_or(&8), *at.iter().max().unwrap_or(&8));
            let first = std::array::from_fn(|s| at.iter().copied().filter(|&v| v >= s as i32).min().unwrap_or(16 + lowest));
            let last = std::array::from_fn(|s| at.iter().copied().filter(|&v| v <= s as i32).max().unwrap_or(highest - 16));
            (first, last)
        };
        SampleGrid { axes: [axis(|o| o.0), axis(|o| o.1)] }
    }

    /// The first sample position at or after `lo` and the last at or
    /// before `hi` along `axis` (0 x, 1 y); None when there is none between
    /// them.
    fn span(&self, axis: usize, lo: f32, hi: f32) -> Option<(f32, f32)> {
        let (first, last) = &self.axes[axis];
        let (lb, hb) = (lo.floor(), hi.floor());
        // Sixteenths into the pixel, rounded towards the inside of the span.
        let ls = (((lo - lb) * 16.0).ceil() as usize).min(16);
        let hs = (((hi - hb) * 16.0).floor() as usize).min(16);
        let a = lb + first[ls] as f32 / 16.0;
        let b = hb + last[hs] as f32 / 16.0;
        (a <= b).then_some((a, b))
    }
}

/// The bands a triangle can touch, or None when it cannot cover a sample
/// at all: behind the camera, off screen, or so small it falls between
/// samples (most of a dense shot's triangles at playblast size).
#[cfg(test)]
pub(crate) fn triangle_bands(clip: [[f32; 4]; 3], width: usize, height: usize, band_rows: usize,
                             grid: &SampleGrid) -> Option<(usize, usize)> {
    screen_bounds(clip, width, height).and_then(|b| bounds_bands(b, width, height, band_rows, grid))
}

/// A triangle's bounds on screen, `[x0, x1, y0, y1]` in pixels (of its part
/// in front of the near plane), or None when none of it is in front or the
/// bounds are not finite. What [`triangle_bands`] needs of the clip
/// positions; the rest ([`bounds_bands`]) depends only on the sample grid.
fn screen_bounds(clip: [[f32; 4]; 3], width: usize, height: usize) -> Option<[f32; 4]> {
    let inside = |c: &[f32; 4]| c[2] + c[3] >= 0.0;
    let (w, h) = (width as f32, height as f32);
    let (mut x0, mut x1, mut y0, mut y1) = (f32::MAX, f32::MIN, f32::MAX, f32::MIN);
    let mut extend = |c: [f32; 4]| {
        let iw = 1.0 / c[3];
        let x = (c[0] * iw * 0.5 + 0.5) * w;
        let y = (0.5 - c[1] * iw * 0.5) * h;
        x0 = x0.min(x);
        x1 = x1.max(x);
        y0 = y0.min(y);
        y1 = y1.max(y);
    };
    match clip.iter().filter(|c| inside(c)).count() {
        0 => return None,
        3 => clip.iter().for_each(|&c| extend(c)),
        _ => {
            // Straddles the near plane: bound the clipped polygon.
            for i in 0..3 {
                let (a, b) = (clip[i], clip[(i + 1) % 3]);
                let (da, db) = (a[2] + a[3], b[2] + b[3]);
                if da >= 0.0 {
                    extend(a);
                }
                if (da >= 0.0) != (db >= 0.0) {
                    let t = da / (da - db);
                    let mut c = [0.0; 4];
                    for k in 0..4 {
                        c[k] = a[k] + (b[k] - a[k]) * t;
                    }
                    if c[3] > 1e-12 {
                        extend(c);
                    }
                }
            }
        }
    }
    if !(x0.is_finite() && x1.is_finite() && y0.is_finite() && y1.is_finite()) {
        return None;
    }
    Some([x0, x1, y0, y1])
}

/// The bands a triangle with screen `bounds` can touch through `grid`, or
/// None when no sample of the grid falls inside them.
fn bounds_bands([x0, x1, y0, y1]: [f32; 4], width: usize, height: usize, band_rows: usize,
                grid: &SampleGrid) -> Option<(usize, usize)> {
    let (w, h) = (width as f32, height as f32);
    // The samples inside the bounds, then their pixels, clamped to the image.
    let (Some((sx0, sx1)), Some((sy0, sy1))) = (grid.span(0, x0, x1), grid.span(1, y0, y1)) else {
        return None;
    };
    let px0 = sx0.floor().max(0.0);
    let px1 = sx1.floor().min(w - 1.0);
    let py0 = sy0.floor().max(0.0);
    let py1 = sy1.floor().min(h - 1.0);
    if px0 > px1 || py0 > py1 {
        return None;
    }
    Some((py0 as usize / band_rows, py1 as usize / band_rows))
}

/// Triangles a binning thread takes at a time: whole instances would leave
/// one thread with a set's million-triangle mesh while the rest wait.
const BIN_CHUNK: usize = 1 << 15;

/// Per band, its (instance, triangle) pairs.
type Bands = Vec<Vec<(u32, u32)>>;

/// Per band, the (instance, triangle) pairs that can touch it, in instance
/// and triangle order whichever thread binned them: the order a band fills
/// in decides which of two surfaces at the same depth (coplanar faces) a
/// sample keeps, so any other order made those pixels change from one
/// render of a frame to the next. Of the instances `which` (indices into
/// `instances`, ascending), once per grid: each triangle's screen bounds
/// are found once and looked up in every grid, so the bands of grid g are
/// exactly those binning through g alone gives.
#[allow(clippy::too_many_arguments)]
fn bin(instances: &[Inst], which: &[usize], meshes: &[Mesh], width: usize, height: usize,
       band_rows: usize, band_count: usize, grids: &[SampleGrid]) -> Vec<Bands> {
    let chunks: Vec<(usize, usize)> = which
        .iter()
        .flat_map(|&i| (0..meshes[instances[i].mesh].triangles.len()).step_by(BIN_CHUNK).map(move |t| (i, t)))
        .collect();
    let next = AtomicUsize::new(0);
    // Per chunk (by its index), per grid, its bands' triangles.
    let parts: Mutex<Vec<(usize, Vec<Bands>)>> = Mutex::new(Vec::new());
    pool::each_thread(&|| {
        loop {
            let c = next.fetch_add(1, Ordering::Relaxed);
            if c >= chunks.len() {
                break;
            }
            let mut local: Vec<Bands> = vec![vec![Vec::new(); band_count]; grids.len()];
            let (i, start) = chunks[c];
            let inst = &instances[i];
            let triangles = &meshes[inst.mesh].triangles;
            let end = (start + BIN_CHUNK).min(triangles.len());
            for (t, tri) in triangles.iter().enumerate().take(end).skip(start) {
                let vi = tri.map(|v| v as usize);
                if vi.iter().any(|&v| v >= inst.clip.len()) {
                    continue;
                }
                let Some(bounds) = screen_bounds(vi.map(|v| inst.clip[v]), width, height) else { continue };
                for (grid, bands) in grids.iter().zip(&mut local) {
                    if let Some((b0, b1)) = bounds_bands(bounds, width, height, band_rows, grid) {
                        for band in bands.iter_mut().take(b1.min(band_count - 1) + 1).skip(b0) {
                            band.push((i as u32, t as u32));
                        }
                    }
                }
            }
            if local.iter().flatten().any(|b| !b.is_empty()) {
                parts.lock().unwrap().push((c, local));
            }
        }
    });
    let mut parts = parts.into_inner().unwrap();
    parts.sort_unstable_by_key(|(c, _)| *c);
    // Each band of each grid is its chunks' pairs in chunk order, the
    // bands joined in parallel, each sized once (appended one after the
    // other on this thread, growing as they went, they were a third of
    // binning).
    let cells: Vec<(usize, usize)> = (0..grids.len()).flat_map(|g| (0..band_count).map(move |b| (g, b))).collect();
    let mut joined = par_map(&cells, |&(g, b)| {
        let mut band = Vec::with_capacity(parts.iter().map(|(_, p)| p[g][b].len()).sum());
        for (_, part) in &parts {
            band.extend_from_slice(&part[g][b]);
        }
        band
    })
    .into_iter();
    (0..grids.len()).map(|_| joined.by_ref().take(band_count).collect()).collect()
}

/// Two bands' (instance, triangle) pairs, each in ascending order, as one
/// in ascending order: the order binning all of them together gives.
fn merge(a: &[(u32, u32)], b: &[(u32, u32)]) -> Vec<(u32, u32)> {
    let mut out = Vec::with_capacity(a.len() + b.len());
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        if a[i] <= b[j] {
            out.push(a[i]);
            i += 1;
        } else {
            out.push(b[j]);
            j += 1;
        }
    }
    out.extend_from_slice(&a[i..]);
    out.extend_from_slice(&b[j..]);
    out
}

/// The least and greatest corner of `points`' bounding box, none for no
/// points.
fn bounds_of(points: &[Vec3]) -> Option<(Vec3, Vec3)> {
    let first = *points.first()?;
    Some(points.iter().fold((first, first), |(lo, hi), p| {
        (std::array::from_fn(|k| lo[k].min(p[k])), std::array::from_fn(|k| hi[k].max(p[k])))
    }))
}

/// Whether a box (`bounds_of`, object space) through `xform` then
/// `world_to_clip` lies wholly beyond one plane of the view (its sides,
/// near and far): nothing of it can reach a sample, as the near clip and
/// the depth test ([`Raster::draw`]) keep what is beyond the other two out.
fn outside((lo, hi): (Vec3, Vec3), xform: &Mat4, world_to_clip: &Mat4) -> bool {
    let to_clip = math::mul(xform, world_to_clip);
    let mut beyond = [true; 6];
    for k in 0..8 {
        let corner = std::array::from_fn(|a| if k >> a & 1 == 0 { lo[a] } else { hi[a] });
        let [x, y, z, w] = math::transform_point4(corner, &to_clip);
        for (b, out) in beyond.iter_mut().zip([x < -w, x > w, y < -w, y > w, z < -w, z > w]) {
            *b &= out;
        }
    }
    beyond.contains(&true)
}

/// Clip positions of `points` through `xform` then `world_to_clip`, into
/// `out` (one per point).
fn clip_into(points: &[Vec3], xform: &Mat4, world_to_clip: &Mat4, out: &mut [[f32; 4]]) {
    let to_clip = math::mul(xform, world_to_clip);
    for (o, &p) in out.iter_mut().zip(points) {
        let c = math::transform_point4(p, &to_clip);
        *o = [c[0] as f32, c[1] as f32, c[2] as f32, c[3] as f32];
    }
}

fn clip_of(points: &[Vec3], xform: &Mat4, world_to_clip: &Mat4) -> Vec<[f32; 4]> {
    let mut out = vec![[0.0; 4]; points.len()];
    clip_into(points, xform, world_to_clip, &mut out);
    out
}

/// How many normals an instance of `mesh` draws with: one per corner when
/// it has its own, else one per point (`smooth`), else none.
fn normal_count(mesh: &Mesh, smooth: Option<&[Vec3]>) -> usize {
    mesh.corner_normals.or(smooth).map_or(0, <[Vec3]>::len)
}

/// World positions of `points` and the mesh's normals through `xform`,
/// into `world` and `normals` ([`normal_count`] of them): what a pass
/// draws that does not depend on its camera.
fn world_into(mesh: &Mesh, smooth: Option<&[Vec3]>, points: &[Vec3], xform: &Mat4, world: &mut [Vec3],
              normals: &mut [Vec3]) {
    for (o, &p) in world.iter_mut().zip(points) {
        *o = math::transform_point(p, xform);
    }
    if let Some(n) = mesh.corner_normals.or(smooth) {
        let nm = math::normal_matrix(xform);
        for (o, &n) in normals.iter_mut().zip(n) {
            *o = math::transform_normal(n, &nm);
        }
    }
}

fn world_of(mesh: &Mesh, smooth: Option<&[Vec3]>, points: &[Vec3], xform: &Mat4) -> (Vec<Vec3>, Vec<Vec3>) {
    let (mut world, mut normals) = (vec![[0.0; 3]; points.len()], vec![[0.0; 3]; normal_count(mesh, smooth)]);
    world_into(mesh, smooth, points, xform, &mut world, &mut normals);
    (world, normals)
}

/// An instance's vertices for a pass of its own, into `clip`, `world` and
/// `normals`: at `close`'s moment of the shutter when its points move.
#[allow(clippy::too_many_arguments)]
fn transform_into(mesh: &Mesh, smooth: Option<&[Vec3]>, xform: &Mat4, world_to_clip: &Mat4,
                  close: Option<(&[Vec3], f32)>, clip: &mut [[f32; 4]], world: &mut [Vec3], normals: &mut [Vec3]) {
    let moved: Option<Vec<Vec3>> = close.map(|(close, s)| {
        mesh.points.iter().zip(close).map(|(&a, &b)| blur::point_at(a, b, s)).collect()
    });
    let points = moved.as_deref().unwrap_or(mesh.points);
    clip_into(points, xform, world_to_clip, clip);
    world_into(mesh, smooth, points, xform, world, normals);
}

/// [`transform_into`] into vectors of its own: what a pass makes of an
/// instance, for the tests.
#[cfg(test)]
fn transform_instance(mesh_index: usize, mesh: &Mesh, smooth: Option<&[Vec3]>, xform: &Mat4, world_to_clip: &Mat4,
                      close: Option<(&[Vec3], f32)>) -> InstanceVerts {
    let n = mesh.points.len();
    let mut v = InstanceVerts {
        mesh: mesh_index,
        clip: vec![[0.0; 4]; n],
        world: vec![[0.0; 3]; n],
        normals: vec![[0.0; 3]; normal_count(mesh, smooth)],
    };
    transform_into(mesh, smooth, xform, world_to_clip, close, &mut v.clip, &mut v.world, &mut v.normals);
    v
}

/// The transformed vertices of a pass's instances, end to end, kept from
/// frame to frame ([`Scratch`]): grown when a frame needs more, never
/// shrunk or cleared, as every value a pass reads it wrote first. Made per
/// instance, they were up to 180 MB of allocations a frame, their page
/// faults and as long again to free.
#[derive(Default)]
pub(crate) struct Arena {
    clip: Vec<[f32; 4]>,
    world: Vec<Vec3>,
    normals: Vec<Vec3>,
}

impl Arena {
    /// Room for at least `[clip, world, normals]` values.
    fn reserve(&mut self, [c, w, n]: [usize; 3]) {
        if self.clip.len() < c {
            self.clip.resize(c, [0.0; 4]);
        }
        if self.world.len() < w {
            self.world.resize(w, [0.0; 3]);
        }
        if self.normals.len() < n {
            self.normals.resize(n, [0.0; 3]);
        }
    }
}

/// The largest render buffer a delegate keeps between frames (a viewport
/// at 1080p and eight samples is 600 MB): a larger one is made per frame
/// rather than held while the viewer sits still.
const KEEP_BYTES: usize = 1 << 30;

/// What the CPU path draws into and through, kept by a delegate from one
/// frame to the next ([`crate::ffi::ThCache`]) so a viewport's frames
/// allocate nothing large: the render buffers of the last two frame shapes
/// (a viewer alternates a one-sample draft with the full frame) and the
/// instances' transformed vertices.
#[derive(Default)]
pub(crate) struct Scratch {
    samples: Vec<Samples>,
    arena: Arena,
}

impl Scratch {
    /// An empty render buffer of `width x height` with `offsets`, a kept
    /// one when it fits.
    fn samples(&mut self, width: usize, height: usize, offsets: &[(f32, f32)]) -> Samples {
        match self.samples.iter().position(|s| s.fits(width, height, offsets)) {
            Some(i) => {
                let mut samples = self.samples.remove(i);
                samples.clear();
                samples
            }
            None => Samples::empty(width, height, offsets.to_vec()),
        }
    }

    /// Keep `samples` for a later frame, in place of the least recent of
    /// two; not a buffer over [`KEEP_BYTES`].
    fn keep(&mut self, samples: Samples) {
        let bytes = samples.depth.len() * (16 + 16 + 4) + samples.ids.len() * 8;
        if bytes > KEEP_BYTES {
            return;
        }
        self.samples.push(samples);
        if self.samples.len() > 2 {
            self.samples.remove(0);
        }
    }
}

/// `f` of every item, in parallel on the pool, in the items' order.
fn par_map<T: Sync, R: Send>(items: &[T], f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let next = AtomicUsize::new(0);
    let done: Mutex<Vec<(usize, R)>> = Mutex::new(Vec::with_capacity(items.len()));
    pool::each_thread(&|| loop {
        let i = next.fetch_add(1, Ordering::Relaxed);
        if i >= items.len() {
            break;
        }
        let r = f(&items[i]);
        done.lock().unwrap().push((i, r));
    });
    let mut done = done.into_inner().unwrap();
    done.sort_unstable_by_key(|(i, _)| *i);
    done.into_iter().map(|(_, r)| r).collect()
}

/// Every (mesh, instance) pair, in the order the rasteriser draws them.
fn jobs(meshes: &[Mesh]) -> Vec<(usize, usize)> {
    meshes
        .iter()
        .enumerate()
        .flat_map(|(m, mesh)| (0..mesh.transforms.len()).map(move |i| (m, i)))
        .collect()
}

/// The vertices of a blurred frame's still instances -- those that do not
/// move over the shutter -- transformed once and drawn by every layer. Their
/// world positions and normals never depend on a layer's camera; their clip
/// positions do only when the layers' cameras differ (a lens, a moving
/// camera), and are then made per layer. Each is made by the same code from
/// the same inputs as a layer's own would be, so the picture is the same to
/// the byte. Kept across the frame's chunks while all its instances fit one
/// batch ([`VERTEX_BUDGET`]); otherwise only for the passes of one batch.
pub(crate) struct Shared {
    /// Sharing at all: off, every pass transforms every instance itself
    /// (the tests hold the two to the same picture).
    on: bool,
    /// Every layer of the frame has the same `world_to_clip`.
    same_clip: bool,
    /// Keep a batch's entries for the next chunk.
    keep: bool,
    /// Per job ([`jobs`]): a still instance's vertices, once made; `clip`
    /// is empty unless `same_clip`.
    verts: Vec<Option<InstanceVerts>>,
    /// With `same_clip` and `keep`: per pass of a chunk, the bands of the
    /// still instances, which every chunk's pass of the same sample offset
    /// shares.
    bins: Option<Vec<Bands>>,
}

impl Shared {
    /// Nothing shared: the unblurred frame's single pass.
    fn off() -> Shared {
        Shared { on: false, same_clip: false, keep: false, verts: Vec::new(), bins: None }
    }

    /// For the chunks of `plan`, batched under `budget` vertices.
    fn new(meshes: &[Mesh], plan: &blur::Plan, budget: usize) -> Shared {
        let mut layers = plan.chunks.iter().flatten();
        let first = layers.next().map(|l| l.world_to_clip);
        let same_clip = layers.all(|l| Some(l.world_to_clip) == first);
        let vertices: usize = meshes.iter().map(|m| m.points.len() * m.transforms.len()).sum();
        let jobs: usize = meshes.iter().map(|m| m.transforms.len()).sum();
        Shared { on: true, same_clip, keep: vertices <= budget, verts: (0..jobs).map(|_| None).collect(), bins: None }
    }
}

/// Render `meshes` through `frame`'s camera at `frame.width x frame.height`.
pub fn render(frame: &Frame, meshes: &[Mesh]) -> Image {
    render_kept(frame, meshes, &[], &mut Scratch::default())
}

/// [`render`] with what a delegate keeps between frames: each mesh's
/// smooth point normals where it has them (`smooth`, by mesh; the rest are
/// worked out here) and the buffers of earlier frames (`scratch`).
pub(crate) fn render_kept(frame: &Frame, meshes: &[Mesh], smooth: &[Option<&[Vec3]>], scratch: &mut Scratch)
                          -> Image {
    let made = smooth_missing(meshes, smooth);
    let smooth = smooth_of(meshes, smooth, &made);
    if !blur::active(frame, meshes) {
        let offsets = composite::sample_offsets(frame.samples);
        let mut samples = scratch.samples(frame.width, frame.height, &offsets);
        let pass = Pass {
            layers: 0..offsets.len(),
            world_to_clip: frame.world_to_clip,
            view: View::new(&frame.view_to_world),
            time: None,
        };
        render_passes(frame, meshes, &smooth, &offsets, std::slice::from_ref(&pass), &mut Shared::off(),
                      VERTEX_BUDGET, &mut samples, &mut scratch.arena);
        let image = composite::resolve(frame, &samples);
        scratch.keep(samples);
        return image;
    }
    blurred(frame, meshes, &smooth, true, VERTEX_BUDGET, scratch)
}

/// A blurred frame ([`crate::blur`]): each chunk of layers drawn layer by
/// layer through its own camera and moment, resolved, and the chunks'
/// pictures averaged.
#[cfg(test)]
pub(crate) fn render_blurred(frame: &Frame, meshes: &[Mesh]) -> Image {
    render_blurred_with(frame, meshes, true, VERTEX_BUDGET)
}

/// [`render_blurred`], sharing still instances' vertices across layers or
/// not, in batches of `budget` vertices.
#[cfg(test)]
pub(crate) fn render_blurred_with(frame: &Frame, meshes: &[Mesh], share: bool, budget: usize) -> Image {
    let made = smooth_missing(meshes, &[]);
    blurred(frame, meshes, &smooth_of(meshes, &[], &made), share, budget, &mut Scratch::default())
}

fn blurred(frame: &Frame, meshes: &[Mesh], smooth: &[Option<&[Vec3]>], share: bool, budget: usize,
           scratch: &mut Scratch) -> Image {
    let offsets = composite::sample_offsets(frame.samples);
    let plan = blur::Plan::new(frame, offsets.len());
    let mut shared = if share { Shared::new(meshes, &plan, budget) } else { Shared::off() };
    let images = plan
        .chunks
        .iter()
        .map(|chunk| {
            let passes: Vec<Pass> = chunk
                .iter()
                .enumerate()
                .map(|(k, layer)| Pass {
                    layers: k..k + 1,
                    world_to_clip: layer.world_to_clip,
                    view: layer.view,
                    time: frame.blur.motion.then_some(layer.time),
                })
                .collect();
            let mut samples = scratch.samples(frame.width, frame.height, &offsets);
            render_passes(frame, meshes, smooth, &offsets, &passes, &mut shared, budget, &mut samples,
                          &mut scratch.arena);
            let image = composite::resolve(frame, &samples);
            scratch.keep(samples);
            image
        })
        .collect();
    blur::average(images, frame.alpha)
}

/// The smooth point normals of the meshes that draw with them (no corner
/// normals) and are not in `given`, by mesh, a mesh at a time on the pool.
fn smooth_missing(meshes: &[Mesh], given: &[Option<&[Vec3]>]) -> Vec<Option<Vec<Vec3>>> {
    let missing: Vec<usize> = (0..meshes.len())
        .filter(|&m| meshes[m].corner_normals.is_none() && given.get(m).copied().flatten().is_none())
        .collect();
    let mut out: Vec<Option<Vec<Vec3>>> = (0..meshes.len()).map(|_| None).collect();
    for (m, normals) in missing.iter().zip(par_map(&missing, |&m| smooth_normals(meshes[m].points, meshes[m].triangles))) {
        out[*m] = Some(normals);
    }
    out
}

/// Each mesh's smooth point normals, from `given` or `made`
/// ([`smooth_missing`]); none for a mesh with corner normals.
fn smooth_of<'a>(meshes: &[Mesh], given: &[Option<&'a [Vec3]>], made: &'a [Option<Vec<Vec3>>])
                 -> Vec<Option<&'a [Vec3]>> {
    (0..meshes.len())
        .map(|m| match meshes[m].corner_normals {
            Some(_) => None,
            None => made[m].as_deref().or(given.get(m).copied().flatten()),
        })
        .collect()
}

/// The render buffer [`render`] makes an unblurred picture from.
#[cfg(all(test, feature = "gpu"))]
pub(crate) fn render_buffer(frame: &Frame, meshes: &[Mesh]) -> Samples {
    let offsets = composite::sample_offsets(frame.samples);
    let pass = Pass {
        layers: 0..offsets.len(),
        world_to_clip: frame.world_to_clip,
        view: View::new(&frame.view_to_world),
        time: None,
    };
    let made = smooth_missing(meshes, &[]);
    let mut samples = Samples::empty(frame.width, frame.height, offsets.clone());
    render_passes(frame, meshes, &smooth_of(meshes, &[], &made), &offsets, std::slice::from_ref(&pass),
                  &mut Shared::off(), VERTEX_BUDGET, &mut samples, &mut Arena::default());
    samples
}

/// Some layers of the render buffer drawn through one camera at one moment
/// of the shutter: unblurred, every layer in one pass; blurred, a pass a
/// layer.
pub(crate) struct Pass {
    pub layers: std::ops::Range<usize>,
    pub world_to_clip: Mat4,
    pub view: View,
    /// The moment (0 opening, 1 closing) moving meshes are drawn at; None,
    /// every mesh at its `points` and `transforms`.
    pub time: Option<f32>,
}

/// Fill `samples` (empty, of the frame's size and `all_offsets`) with
/// `meshes`, by `passes`, each one pass over the triangles. Instances go in batches of
/// at most `budget` vertices, every pass drawing a batch before the next
/// batch is made; each layer so takes the batches, and the triangles in
/// them, in the same order whatever is shared.
#[allow(clippy::too_many_arguments)]
fn render_passes(frame: &Frame, meshes: &[Mesh], smooth: &[Option<&[Vec3]>], all_offsets: &[(f32, f32)],
                 passes: &[Pass], shared: &mut Shared, budget: usize, samples: &mut Samples, arena: &mut Arena) {
    let (w, h) = (frame.width, frame.height);
    if w == 0 || h == 0 {
        return;
    }
    let started = std::time::Instant::now();
    let zero = std::time::Duration::ZERO;
    let (mut transforming, mut binning, mut filling) = (zero, zero, zero);
    let band_rows = 16usize.max(h / (threads() * 8)).min(h.max(1));
    let plane = w * h;
    let jobs = jobs(meshes);
    let every_mesh: Vec<usize> = (0..meshes.len()).collect();
    let bounds = par_map(&every_mesh, |&m| bounds_of(meshes[m].points));
    // A mesh every pass draws at its `points` and `transforms`.
    let motion = passes.iter().any(|p| p.time.is_some());
    let still = |m: usize| shared.on && (!motion || !blur::moves(&meshes[m]));
    let same_clip = shared.same_clip;

    let cancel = crate::cancel::Token::current();
    let mut start = 0;
    while start < jobs.len() && !cancel.is_set() {
        // Batches of instances under the budget.
        let mut end = start;
        let mut verts = 0;
        while end < jobs.len() && (end == start || verts + meshes[jobs[end].0].points.len() <= budget) {
            verts += meshes[jobs[end].0].points.len();
            end += 1;
        }
        let batch = start..end;
        start = end;

        // The batch's still instances not made yet, once for every pass.
        let t0 = std::time::Instant::now();
        let missing: Vec<usize> = batch.clone().filter(|&j| still(jobs[j].0) && shared.verts[j].is_none()).collect();
        let first_clip = passes.first().map(|p| p.world_to_clip);
        let made = par_map(&missing, |&j| {
            let (m, i) = jobs[j];
            let mesh = &meshes[m];
            let xform = &mesh.transforms[i];
            let clip = match (same_clip, &first_clip) {
                (true, Some(wc)) => clip_of(mesh.points, xform, wc),
                _ => Vec::new(),
            };
            let (world, normals) = world_of(mesh, smooth[m], mesh.points, xform);
            InstanceVerts { mesh: m, clip, world, normals }
        });
        for (j, v) in missing.into_iter().zip(made) {
            shared.verts[j] = Some(v);
        }
        transforming += t0.elapsed();

        // With one camera for every layer, the still instances' bands too,
        // once for every pass: each triangle's bounds found once, looked up
        // in each pass's sample grid.
        let band_count = h.div_ceil(band_rows);
        let t0 = std::time::Instant::now();
        let is_still: Vec<bool> = batch.clone().map(|j| still(jobs[j].0)).collect();
        if shared.on && same_clip && shared.bins.is_none() {
            let placeholder = |j: usize| Inst { mesh: jobs[j].0, clip: &[], world: &[], normals: &[] };
            let insts: Vec<Inst> = batch
                .clone()
                .map(|j| shared.verts[j].as_ref().map_or_else(|| placeholder(j), InstanceVerts::view))
                .collect();
            let which: Vec<usize> = (0..batch.len()).filter(|&k| is_still[k]).collect();
            let grids: Vec<SampleGrid> = passes.iter().map(|p| SampleGrid::new(&all_offsets[p.layers.clone()])).collect();
            shared.bins = Some(bin(&insts, &which, meshes, w, h, band_rows, band_count, &grids));
        }
        binning += t0.elapsed();
        let moving: Vec<usize> = (0..batch.len()).filter(|&k| !is_still[k]).collect();
        let kept = &shared.verts;
        let still_bins = shared.bins.as_ref();

        for (pass_index, pass) in passes.iter().enumerate() {
            let offsets = &all_offsets[pass.layers.clone()];
            let grid = SampleGrid::new(offsets);
            let spread = [
                offsets.iter().map(|o| o.0).fold(f32::MAX, f32::min),
                offsets.iter().map(|o| o.0).fold(f32::MIN, f32::max),
                offsets.iter().map(|o| o.1).fold(f32::MAX, f32::min),
                offsets.iter().map(|o| o.1).fold(f32::MIN, f32::max),
            ];
            let raster = Raster { width: w, height: h, meshes, view: pass.view, offsets, spread };

            // Transform what the pass needs of its own, in parallel, into
            // the arena: everything, or only the clip positions of a still
            // instance whose world positions and normals are shared.
            let t0 = std::time::Instant::now();
            // An instance wholly out of an unblurred pass's view is neither
            // transformed nor binned (half the instances of an orbit around
            // a set, a third of a draft's time). Its triangles could reach
            // no sample; the picture is the same.
            let culled: Vec<bool> = batch
                .clone()
                .map(|j| {
                    let (m, inst) = jobs[j];
                    pass.time.is_none()
                        && !(still(m) && same_clip)
                        && bounds[m].is_some_and(|b| outside(b, &meshes[m].transforms[inst], &pass.world_to_clip))
                })
                .collect();
            let todo: Vec<usize> =
                batch.clone().filter(|&j| !(still(jobs[j].0) && same_clip) && !culled[j - batch.start]).collect();
            let lens: Vec<[usize; 3]> = todo
                .iter()
                .map(|&j| {
                    let m = jobs[j].0;
                    let n = meshes[m].points.len();
                    if still(m) { [n, 0, 0] } else { [n, n, normal_count(&meshes[m], smooth[m])] }
                })
                .collect();
            let totals = lens.iter().fold([0; 3], |a, l| [a[0] + l[0], a[1] + l[1], a[2] + l[2]]);
            arena.reserve(totals);
            {
                let (mut clip, mut world, mut normals) =
                    (&mut arena.clip[..totals[0]], &mut arena.world[..totals[1]], &mut arena.normals[..totals[2]]);
                let mut items = Vec::with_capacity(todo.len());
                for (&j, l) in todo.iter().zip(&lens) {
                    let (c, rest) = std::mem::take(&mut clip).split_at_mut(l[0]);
                    clip = rest;
                    let (wd, rest) = std::mem::take(&mut world).split_at_mut(l[1]);
                    world = rest;
                    let (n, rest) = std::mem::take(&mut normals).split_at_mut(l[2]);
                    normals = rest;
                    items.push((j, c, wd, n));
                }
                let items = Mutex::new(items);
                pool::each_thread(&|| loop {
                    let item = items.lock().unwrap().pop();
                    let Some((j, clip, world, normals)) = item else { break };
                    let (m, inst) = jobs[j];
                    let mesh = &meshes[m];
                    if still(m) {
                        clip_into(mesh.points, &mesh.transforms[inst], &pass.world_to_clip, clip);
                        continue;
                    }
                    let (xform, close) = match pass.time {
                        Some(s) => (blur::transform_at(mesh, inst, s), blur::closing_points(mesh).map(|c| (c, s))),
                        None => (mesh.transforms[inst], None),
                    };
                    transform_into(mesh, smooth[m], &xform, &pass.world_to_clip, close, clip, world, normals);
                });
            }
            let mut own: Vec<Option<[std::ops::Range<usize>; 3]>> = vec![None; batch.len()];
            let mut at = [0usize; 3];
            for (&j, l) in todo.iter().zip(&lens) {
                own[j - batch.start] = Some(std::array::from_fn(|k| at[k]..at[k] + l[k]));
                at = std::array::from_fn(|k| at[k] + l[k]);
            }
            let shared_view = |j: usize| kept[j].as_ref().expect("a still instance is made before its passes").view();
            let instances: Vec<Inst> = batch
                .clone()
                .zip(&own)
                .map(|(j, o)| match o {
                    Some([c, _, _]) if still(jobs[j].0) => Inst { clip: &arena.clip[c.clone()], ..shared_view(j) },
                    Some([c, wd, n]) => Inst {
                        mesh: jobs[j].0,
                        clip: &arena.clip[c.clone()],
                        world: &arena.world[wd.clone()],
                        normals: &arena.normals[n.clone()],
                    },
                    None if culled[j - batch.start] => Inst { mesh: jobs[j].0, clip: &[], world: &[], normals: &[] },
                    None => shared_view(j),
                })
                .collect();
            let visible: Vec<usize> = (0..batch.len()).filter(|&k| !culled[k]).collect();

            // Sort the batch's triangles into the bands they touch, then
            // rasterise, each thread taking whole bands (in every layer of
            // the pass) and only their triangles.
            let t1 = std::time::Instant::now();
            let bins: Vec<std::borrow::Cow<[(u32, u32)]>> = match still_bins {
                Some(still) => {
                    let still = &still[pass_index];
                    if moving.is_empty() {
                        still.iter().map(|b| std::borrow::Cow::Borrowed(&b[..])).collect()
                    } else {
                        let own = bin(&instances, &moving, meshes, w, h, band_rows, band_count, std::slice::from_ref(&grid))
                            .pop()
                            .expect("one grid");
                        still.iter().zip(&own).map(|(a, b)| std::borrow::Cow::Owned(merge(a, b))).collect()
                    }
                }
                None => bin(&instances, &visible, meshes, w, h, band_rows, band_count, std::slice::from_ref(&grid))
                    .pop()
                    .expect("one grid")
                    .into_iter()
                    .map(std::borrow::Cow::Owned)
                    .collect(),
            };
            let t2 = std::time::Instant::now();
            let layers = pass.layers.clone();
            let mut albedo: Vec<_> = samples.albedo.chunks_mut(plane).skip(layers.start).take(layers.len())
                .map(|l| l.chunks_mut(band_rows * w)).collect();
            let mut normal: Vec<_> = samples.normal.chunks_mut(plane).skip(layers.start).take(layers.len())
                .map(|l| l.chunks_mut(band_rows * w)).collect();
            let mut depth: Vec<_> = samples.depth.chunks_mut(plane).skip(layers.start).take(layers.len())
                .map(|l| l.chunks_mut(band_rows * w)).collect();
            let mut ids = (layers.start == 0).then(|| samples.ids.chunks_mut(band_rows * w));
            let bands: Vec<(Band, &[(u32, u32)])> = bins
                .iter()
                .enumerate()
                .map(|(i, bin)| {
                    let band = Band {
                        y0: i * band_rows,
                        albedo: albedo.iter_mut().map(|l| l.next().expect("a band in every layer")).collect(),
                        normal: normal.iter_mut().map(|l| l.next().expect("a band in every layer")).collect(),
                        depth: depth.iter_mut().map(|l| l.next().expect("a band in every layer")).collect(),
                        ids: ids.as_mut().and_then(|l| l.next()),
                    };
                    (band, &bin[..])
                })
                .collect();
            let queue = Mutex::new(bands);
            let job_ids: Vec<[i32; 2]> = batch.clone().map(|j| [jobs[j].0 as i32, jobs[j].1 as i32]).collect();
            pool::each_thread(&|| loop {
                if cancel.is_set() {
                    break;
                }
                let item = queue.lock().unwrap().pop();
                let Some((mut band, triangles)) = item else { break };
                for &(inst, t) in triangles {
                    raster.draw_triangle(&mut band, &instances[inst as usize], t as usize, job_ids[inst as usize]);
                }
            });
            transforming += t1 - t0;
            binning += t2 - t1;
            filling += t2.elapsed();
        }
        if shared.on && !shared.keep {
            for v in &mut shared.verts[batch] {
                *v = None;
            }
            shared.bins = None;
        }
    }
    // The C++ shim's TH_PLAYBLAST_TIMING line counts this inside "render".
    if std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
        eprintln!("hdThPlayblast: raster {} ms at {} samples a pixel{}: transform {} ms, bin {} ms, fill {} ms",
                  started.elapsed().as_millis(), all_offsets.len(),
                  if passes.len() > 1 { format!(" in {} passes", passes.len()) } else { String::new() },
                  transforming.as_millis(), binning.as_millis(), filling.as_millis());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A camera at z = 5 looking down -z with a 90 degree field of view,
    /// near 0.1, far 100 (GL projection, USD row-vector layout).
    fn camera() -> (Mat4, Mat4) {
        let mut view_to_world = math::IDENTITY;
        view_to_world[14] = 5.0;
        let mut world_to_view = math::IDENTITY;
        world_to_view[14] = -5.0;
        let (n, f) = (0.1f64, 100.0f64);
        let proj: Mat4 = [
            1.0, 0.0, 0.0, 0.0, //
            0.0, 1.0, 0.0, 0.0, //
            0.0, 0.0, -(f + n) / (f - n), -1.0, //
            0.0, 0.0, -2.0 * f * n / (f - n), 0.0,
        ];
        (math::mul(&world_to_view, &proj), view_to_world)
    }

    fn quad() -> (Vec<Vec3>, Vec<[u32; 3]>) {
        (
            vec![[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]],
            vec![[0, 1, 2], [0, 2, 3]],
        )
    }

    fn frame(size: usize) -> Frame {
        let (world_to_clip, view_to_world) = camera();
        Frame { width: size, height: size, world_to_clip, view_to_world, samples: 1,
                shading: Shading::Lit, ao: true, blur: Blur::OFF, alpha: false }
    }

    #[test]
    fn a_red_quad_facing_the_camera_covers_the_middle() {
        let (points, triangles) = quad();
        let transforms = [math::IDENTITY];
        let mesh = Mesh {
            points: &points, triangles: &triangles, corner_normals: None,
            corner_colors: None, corner_uvs: None, base_color: [1.0, 0.0, 0.0],
            tiles: Vec::new(), transforms: &transforms, key: None, points_close: None, transform_keys: &[],
        };
        let img = render(&frame(64), &[mesh]);
        // Quad half-width 1 at distance 5, 90 degree fov: 1/5 of the half
        // frame either side of the centre.
        let mid = img.color[32 * 64 + 32];
        assert!(mid[0] > 0.5 && mid[1] < 0.05, "{mid:?}");
        assert!(img.depth[32 * 64 + 32] < 1.0);
        let corner = img.color[2 * 64 + 2];
        assert!((corner[0] - corner[1]).abs() < 1e-6, "backdrop is grey: {corner:?}");
        assert_eq!(img.depth[2 * 64 + 2], 1.0);
    }

    #[test]
    fn up_is_up_and_the_nearer_instance_wins() {
        let (points, triangles) = quad();
        // A green quad moved up, and a blue one in front of the red one.
        let mut up = math::IDENTITY;
        up[13] = 2.0;
        let mut near = math::IDENTITY;
        near[14] = 1.0;
        let red = [math::IDENTITY];
        let other = [up, near];
        let meshes = [
            Mesh { points: &points, triangles: &triangles, corner_normals: None,
                   corner_colors: None, corner_uvs: None, base_color: [1.0, 0.0, 0.0],
                   tiles: Vec::new(), transforms: &red, key: None, points_close: None, transform_keys: &[] },
            Mesh { points: &points, triangles: &triangles, corner_normals: None,
                   corner_colors: None, corner_uvs: None, base_color: [0.0, 0.0, 1.0],
                   tiles: Vec::new(), transforms: &other, key: None, points_close: None, transform_keys: &[] },
        ];
        let img = render(&frame(64), &meshes);
        assert!(img.color[32 * 64 + 32][2] > 0.5, "blue in front");
        // y = 2 is above the centre: the top half of the image (row 0 = top).
        assert!(img.color[18 * 64 + 32][2] > 0.5, "moved-up quad drawn in the top half");
        assert!(img.color[46 * 64 + 32][2] < 0.05 || img.depth[46 * 64 + 32] == 1.0);
    }

    #[test]
    fn geometry_through_the_near_plane_is_clipped_not_dropped() {
        // A floor running from behind the camera to far in front of it.
        let points = vec![[-50.0, -1.0, 10.0], [50.0, -1.0, 10.0], [50.0, -1.0, -50.0], [-50.0, -1.0, -50.0]];
        let triangles = vec![[0u32, 1, 2], [0, 2, 3]];
        let transforms = [math::IDENTITY];
        let mesh = Mesh { points: &points, triangles: &triangles, corner_normals: None,
                          corner_colors: None, corner_uvs: None, base_color: [0.0, 1.0, 0.0],
                          tiles: Vec::new(), transforms: &transforms, key: None, points_close: None, transform_keys: &[] };
        let img = render(&frame(64), &[mesh]);
        assert!(img.color[60 * 64 + 32][1] > 0.3, "floor visible below the horizon");
        assert_eq!(img.depth[4 * 64 + 32], 1.0, "nothing above it");
    }

    #[test]
    fn binning_skips_what_cannot_cover_a_sample() {
        // Clip coords with w = 1 map x, y in [-1, 1] onto a 100 x 100 image.
        let at = |x: f32, y: f32| [x, y, 0.0, 1.0];
        let centres = SampleGrid::new(&composite::sample_offsets(1));
        let eight = SampleGrid::new(&composite::sample_offsets(8));
        // Spans the image: every band.
        assert_eq!(triangle_bands([at(-1.0, 1.0), at(1.0, 1.0), at(0.0, -1.0)], 100, 100, 10, &centres), Some((0, 9)));
        // A sliver between pixel centres (screen x 50.05..50.45, y
        // 49.55..50.0): nothing at one sample a pixel; at eight, one sits
        // inside it (x 50.1875, y 49.8125).
        let sliver = [at(0.001, 0.0), at(0.009, 0.0), at(0.005, 0.009)];
        assert_eq!(triangle_bands(sliver, 100, 100, 10, &centres), None);
        assert_eq!(triangle_bands(sliver, 100, 100, 10, &eight), Some((4, 4)));
        // Off screen, and behind the camera.
        assert_eq!(triangle_bands([at(2.0, 0.0), at(3.0, 0.0), at(2.5, 0.5)], 100, 100, 10, &eight), None);
        let behind = [-1.0, -1.0, -2.0, 1.0];
        assert_eq!(triangle_bands([behind, behind, behind], 100, 100, 10, &eight), None);
    }

    #[test]
    fn anti_aliasing_keeps_the_frame_size() {
        let (points, triangles) = quad();
        let transforms = [math::IDENTITY];
        let mesh = Mesh { points: &points, triangles: &triangles, corner_normals: None,
                          corner_colors: None, corner_uvs: None, base_color: DEFAULT_ALBEDO,
                          tiles: Vec::new(), transforms: &transforms, key: None, points_close: None, transform_keys: &[] };
        let mut f = frame(40);
        f.samples = 8;
        let img = render(&f, &[mesh]);
        assert_eq!(img.color.len(), 40 * 40);
    }


    fn quad_mesh<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], transforms: &'a [Mat4]) -> Mesh<'a> {
        Mesh {
            points, triangles, corner_normals: None, corner_colors: None, corner_uvs: None,
            base_color: [1.0; 3], tiles: Vec::new(), transforms, key: None, points_close: None, transform_keys: &[],
        }
    }

    /// What is shared and what is kept: clip positions only when every layer
    /// looks through the same camera, and the vertices kept across chunks
    /// only while every instance fits one batch.
    #[test]
    fn a_frame_shares_clip_only_through_one_camera_and_keeps_only_one_batch() {
        let (points, triangles) = quad();
        let transforms = [math::IDENTITY, math::IDENTITY];
        let meshes = [quad_mesh(&points, &triangles, &transforms)];
        let mut f = frame(16);
        f.samples = 4;
        f.blur = Blur { samples: 16, motion: true, ..Blur::OFF };
        let plan = blur::Plan::new(&f, 4);
        let shared = Shared::new(&meshes, &plan, 8);
        assert!(shared.on && shared.same_clip && shared.keep);
        assert_eq!(shared.verts.len(), 2);
        assert!(!Shared::new(&meshes, &plan, 7).keep, "8 vertices do not fit 7");
        f.blur.lens = blur::Lens::of_camera(0.05, 2.0, 3.0);
        assert!(!Shared::new(&meshes, &blur::Plan::new(&f, 4), 8).same_clip, "a lens moves the eye per layer");
        f.blur.lens = None;
        f.blur.cameras[0] = f.view_to_world;
        f.blur.cameras[1] = math::mul(&f.view_to_world, &{
            let mut m = math::IDENTITY;
            m[12] = 0.5;
            m
        });
        f.blur.camera_keys = 2;
        assert!(!Shared::new(&meshes, &blur::Plan::new(&f, 4), 8).same_clip, "a moving camera");
    }

    /// Not vacuous: a box beside the view, behind the camera or past the
    /// far plane is skipped; one in view or through the near plane is not.
    #[test]
    fn boxes_beside_behind_and_beyond_the_view_are_skipped() {
        let (world_to_clip, _) = camera();
        let unit = ([-1.0f32, -1.0, -1.0], [1.0f32, 1.0, 1.0]);
        let at = |x: f64, y: f64, z: f64| {
            let mut m = math::IDENTITY;
            m[12] = x;
            m[13] = y;
            m[14] = z;
            m
        };
        for (where_, x, y, z, skipped) in [("in view", 0.0, 0.0, 0.0, false), ("through the near plane", 0.0, 0.0, 5.0, false),
                                         ("beside", 40.0, 0.0, 0.0, true), ("above", 0.0, 40.0, 0.0, true),
                                         ("behind", 0.0, 0.0, 9.0, true), ("beyond far", 0.0, 0.0, -200.0, true)] {
            assert_eq!(outside(unit, &at(x, y, z), &world_to_clip), skipped, "{where_}");
        }
    }

    use proptest::prelude::*;

    proptest! {
        /// A still instance's shared vertices -- world positions and normals
        /// made once, clip positions made for each layer's camera -- are, to
        /// the bit, the ones each layer transforming it itself makes.
        #[test]
        fn shared_transforms_equal_per_layer_transforms(
            points in prop::collection::vec([-3.0f32..3.0, -3.0f32..3.0, -3.0f32..3.0], 3..12),
            yaw in -3.0f64..3.0, scale in 0.1f64..3.0, at in [-2.0f64..2.0, -2.0f64..2.0, -2.0f64..2.0],
            eyes in prop::collection::vec([-0.5f64..0.5, -0.5f64..0.5], 1..5), authored: bool,
        ) {
            let triangles: Vec<[u32; 3]> = (1..points.len() as u32 - 1).map(|i| [0, i, i + 1]).collect();
            let normals: Vec<Vec3> = (0..triangles.len() * 3).map(|k| math::normalize([k as f32, 1.0, 2.0])).collect();
            let (c, s) = (yaw.cos() * scale, yaw.sin() * scale);
            let xform: Mat4 = [c, 0.0, -s, 0.0, 0.0, scale, 0.0, 0.0, s, 0.0, c, 0.0, at[0], at[1], at[2], 1.0];
            let transforms = [xform];
            let mesh = Mesh { corner_normals: authored.then_some(&normals[..]), ..quad_mesh(&points, &triangles, &transforms) };
            let smooth = (!authored).then(|| smooth_normals(&points, &triangles));
            let (world, shared_normals) = world_of(&mesh, smooth.as_deref(), mesh.points, &xform);
            // A layer's own transform, written out: what every layer made
            // before still instances were shared.
            let nm = math::normal_matrix(&xform);
            let world_ref: Vec<Vec3> = points.iter().map(|&p| math::transform_point(p, &xform)).collect();
            let normals_ref: Vec<Vec3> = if authored { &normals } else { smooth.as_ref().unwrap() }
                .iter().map(|&n| math::transform_normal(n, &nm)).collect();
            let bits = |v: &[Vec3]| v.iter().flat_map(|p| p.map(f32::to_bits)).collect::<Vec<_>>();
            let cbits = |v: &[[f32; 4]]| v.iter().flat_map(|p| p.map(f32::to_bits)).collect::<Vec<_>>();
            prop_assert_eq!(bits(&world), bits(&world_ref));
            prop_assert_eq!(bits(&shared_normals), bits(&normals_ref));
            let (world_to_clip, _) = camera();
            for [x, y] in eyes {
                let mut shift = math::IDENTITY;
                shift[12] = x;
                shift[13] = y;
                let layer = math::mul(&shift, &world_to_clip);
                let to_clip = math::mul(&xform, &layer);
                let clip_ref: Vec<[f32; 4]> = points.iter()
                    .map(|&p| math::transform_point4(p, &to_clip).map(|c| c as f32))
                    .collect();
                let own = transform_instance(0, &mesh, smooth.as_deref(), &xform, &layer, None);
                prop_assert_eq!(cbits(&clip_of(mesh.points, &xform, &layer)), cbits(&clip_ref));
                prop_assert_eq!(cbits(&own.clip), cbits(&clip_ref));
                prop_assert_eq!(bits(&own.world), bits(&world_ref));
                prop_assert_eq!(bits(&own.normals), bits(&normals_ref));
            }
        }
    }


    proptest! {
        /// Binning once for several sample grids, each triangle's bounds
        /// found once, gives each grid exactly the bands binning through it
        /// alone gives, of any subset of the instances.
        #[test]
        fn binning_for_many_grids_is_binning_for_each(
            meshes in prop::collection::vec((prop::collection::vec([-3.0f32..3.0, -3.0f32..3.0, -3.0f32..3.0], 3..10),
                                             [-1.0f64..1.0, -1.0f64..1.0, -2.0f64..2.0]), 1..5),
            pick in prop::collection::vec(any::<bool>(), 5),
            samples in prop::sample::select(vec![1usize, 2, 4, 8, 16]), band_rows in 1usize..9,
        ) {
            let (w, h) = (37usize, 23usize);
            let built: Vec<(Vec<[u32; 3]>, [Mat4; 1])> = meshes.iter()
                .map(|(pts, at)| {
                    let mut t = math::IDENTITY;
                    t[12] = at[0];
                    t[13] = at[1];
                    t[14] = at[2];
                    ((1..pts.len() as u32 - 1).map(|i| [0, i, i + 1]).collect(), [t])
                })
                .collect();
            let ms: Vec<Mesh> = meshes.iter().zip(&built).map(|((pts, _), (tris, t))| quad_mesh(pts, tris, t)).collect();
            let (world_to_clip, _) = camera();
            let verts: Vec<InstanceVerts> = ms.iter().enumerate()
                .map(|(m, mesh)| transform_instance(m, mesh, None, &mesh.transforms[0], &world_to_clip, None))
                .collect();
            let insts: Vec<Inst> = verts.iter().map(InstanceVerts::view).collect();
            let which: Vec<usize> = (0..insts.len()).filter(|&i| pick[i]).collect();
            let offsets = composite::sample_offsets(samples);
            let grids: Vec<SampleGrid> = offsets.iter().map(|o| SampleGrid::new(std::slice::from_ref(o))).collect();
            let band_count = h.div_ceil(band_rows);
            let together = bin(&insts, &which, &ms, w, h, band_rows, band_count, &grids);
            prop_assert_eq!(together.len(), grids.len());
            for (g, grid) in grids.iter().enumerate() {
                let alone = bin(&insts, &which, &ms, w, h, band_rows, band_count, std::slice::from_ref(grid));
                prop_assert_eq!(&together[g], &alone[0]);
                // And exactly what triangle_bands says of each triangle of
                // the chosen instances, in instance and triangle order.
                let mut expected: Bands = vec![Vec::new(); band_count];
                for &i in &which {
                    let inst = &insts[i];
                    for (t, tri) in ms[inst.mesh].triangles.iter().enumerate() {
                        if let Some((b0, b1)) = triangle_bands(tri.map(|v| inst.clip[v as usize]), w, h, band_rows, grid) {
                            for band in expected.iter_mut().take(b1.min(band_count - 1) + 1).skip(b0) {
                                band.push((i as u32, t as u32));
                            }
                        }
                    }
                }
                prop_assert_eq!(&alone[0], &expected);
            }
        }

        /// A box `outside` turns away holds no point the view can see, for
        /// any instance transform (turned, scaled, moved) and any camera
        /// shift: skipping it can drop nothing drawn.
        #[test]
        fn a_box_outside_the_view_holds_nothing_in_view(
            lo in [-3.0f32..3.0, -3.0f32..3.0, -3.0f32..3.0], extent in [0.0f32..4.0, 0.0f32..4.0, 0.0f32..4.0],
            yaw in -3.2f64..3.2, scale in 0.05f64..4.0,
            at in [-30.0f64..30.0, -30.0f64..30.0, -200.0f64..30.0], shift in [-3.0f64..3.0, -3.0f64..3.0],
            probes in prop::collection::vec([0.0f32..=1.0, 0.0f32..=1.0, 0.0f32..=1.0], 64),
        ) {
            let hi: Vec3 = std::array::from_fn(|k| lo[k] + extent[k]);
            let (c, s) = (yaw.cos() * scale, yaw.sin() * scale);
            let xform: Mat4 = [c, 0.0, -s, 0.0, 0.0, scale, 0.0, 0.0, s, 0.0, c, 0.0, at[0], at[1], at[2], 1.0];
            let (world_to_clip, _) = camera();
            let mut moved = math::IDENTITY;
            moved[12] = shift[0];
            moved[13] = shift[1];
            let world_to_clip = math::mul(&moved, &world_to_clip);
            if outside((lo, hi), &xform, &world_to_clip) {
                let to_clip = math::mul(&xform, &world_to_clip);
                let corners = (0..8).map(|k| std::array::from_fn(|a| if k >> a & 1 == 0 { 0.0 } else { 1.0 }));
                for t in probes.into_iter().chain(corners) {
                    let p: Vec3 = std::array::from_fn(|k| lo[k] + t[k] * extent[k]);
                    let [x, y, z, w] = math::transform_point4(p, &to_clip);
                    let seen = (-w..=w).contains(&x) && (-w..=w).contains(&y) && (-w..=w).contains(&z);
                    prop_assert!(!seen, "{:?} is in view, its box was skipped", p);
                }
            }
        }

        /// Two ascending lists merge into their sorted concatenation.
        #[test]
        fn merging_keeps_instance_and_triangle_order(
            mut a in prop::collection::vec((0u32..6, 0u32..40), 0..30),
            mut b in prop::collection::vec((0u32..6, 0u32..40), 0..30),
        ) {
            a.sort();
            b.sort();
            let mut both = [a.clone(), b.clone()].concat();
            both.sort();
            prop_assert_eq!(merge(&a, &b), both);
        }
    }
}
