//! The rasteriser: triangles in, a shaded colour image and a depth image out.
//!
//! Work is split two ways. Instances are processed in batches whose
//! transformed vertices fit [`VERTEX_BUDGET`], so a shot with ten thousand
//! instanced trees never holds all of them in memory at once. Within a batch
//! the image is cut into horizontal bands and each thread owns whole bands,
//! so the depth test needs no locking.
//!
//! Shading is the viewport's smooth headlight: a key light from just above
//! and left of the camera, a soft ambient floor and a faint specular, lit
//! from both sides (a playblast shows back faces like the viewport does).

use crate::math::{self, Mat4, Vec3};
use crate::texture::Texture;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

/// Transformed vertices held at once, across a batch of instances.
pub const VERTEX_BUDGET: usize = 4_000_000;

/// Display-referred backdrop, top and bottom (Houdini's light viewport
/// gradient); linearised when drawn, since the image husk receives is linear.
pub(crate) const BACKDROP_TOP: f32 = 0.70;
pub(crate) const BACKDROP_BOTTOM: f32 = 0.52;
/// The light, in camera space, pointing from the surface towards the light:
/// up and to the left of the lens, as the viewport's headlight sits.
pub(crate) const KEY_DIRECTION: Vec3 = [-0.35, 0.35, 1.0];
pub(crate) const AMBIENT: f32 = 0.18;
pub(crate) const SPECULAR: f32 = 0.08;
pub(crate) const SHININESS: i32 = 32;
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
}

#[derive(Clone, Copy)]
pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub world_to_clip: Mat4,
    /// Camera to world: its rows are the camera's right, up and back axes
    /// and its position (the camera looks down -z).
    pub view_to_world: Mat4,
    pub supersample: usize,
}

pub struct Image {
    /// Linear RGBA, row 0 at the top.
    pub color: Vec<[f32; 4]>,
    /// Normalised device depth in [0, 1], 1 = far / nothing drawn.
    pub depth: Vec<f32>,
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

/// Per-face normals for a polygonal mesh that authored none, one per
/// triangle corner: each triangle's own normal on all three corners. A
/// triangle indexing past `points` gets +z, as a degenerate one does.
pub fn flat_normals(points: &[Vec3], triangles: &[[u32; 3]]) -> Vec<Vec3> {
    let mut normals = Vec::with_capacity(triangles.len() * 3);
    for tri in triangles {
        let [a, b, c] = tri.map(|i| i as usize);
        let n = if a < points.len() && b < points.len() && c < points.len() {
            math::normalize(math::cross(math::sub(points[b], points[a]), math::sub(points[c], points[a])))
        } else {
            [0.0, 0.0, 1.0]
        };
        normals.extend([n, n, n]);
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

struct Light {
    eye: Vec3,
    key: Vec3,
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

/// Clip a triangle to the near plane (z >= -w, GL clip space).
fn clip_near(tri: [Corner; 3]) -> Vec<Corner> {
    let inside = |c: &Corner| c.clip[2] + c.clip[3] >= 0.0;
    if tri.iter().all(inside) {
        return tri.to_vec();
    }
    let mut out = Vec::with_capacity(4);
    for i in 0..3 {
        let (a, b) = (&tri[i], &tri[(i + 1) % 3]);
        let (da, db) = (a.clip[2] + a.clip[3], b.clip[2] + b.clip[3]);
        if da >= 0.0 {
            out.push(*a);
        }
        if (da >= 0.0) != (db >= 0.0) {
            out.push(lerp_corner(a, b, da / (da - db)));
        }
    }
    out
}

struct Band<'a> {
    y0: usize,
    color: &'a mut [[f32; 4]],
    depth: &'a mut [f32],
}

struct Raster<'a> {
    width: usize,
    height: usize,
    meshes: &'a [Mesh<'a>],
    light: &'a Light,
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

    fn shade(&self, mesh: &Mesh, c: &Corner) -> Vec3 {
        let mut n = math::normalize(c.normal);
        let v = math::normalize(math::sub(self.light.eye, c.world));
        if math::dot(n, v) < 0.0 {
            n = math::scale(n, -1.0);
        }
        let l = self.light.key;
        let diffuse = math::dot(n, l).max(0.0);
        let h = math::normalize(math::add(l, v));
        let spec = SPECULAR * math::dot(n, h).max(0.0).powi(SHININESS);
        let albedo = self.albedo(mesh, c);
        let k = AMBIENT + (1.0 - AMBIENT) * diffuse;
        [albedo[0] * k + spec, albedo[1] * k + spec, albedo[2] * k + spec]
    }

    fn draw(&self, band: &mut Band, mesh: &Mesh, poly: &[Corner]) {
        let rows = band.depth.len() / self.width;
        let (w, h) = (self.width as f32, self.height as f32);
        // Screen-space vertices: x right, y down, z in [0, 1], 1/w.
        let screen: Vec<[f32; 4]> = poly
            .iter()
            .map(|c| {
                let iw = 1.0 / c.clip[3];
                [
                    (c.clip[0] * iw * 0.5 + 0.5) * w,
                    (0.5 - c.clip[1] * iw * 0.5) * h,
                    c.clip[2] * iw * 0.5 + 0.5,
                    iw,
                ]
            })
            .collect();
        for i in 1..poly.len().saturating_sub(1) {
            let idx = [0, i, i + 1];
            let s = idx.map(|k| screen[k]);
            let area = (s[1][0] - s[0][0]) * (s[2][1] - s[0][1])
                - (s[2][0] - s[0][0]) * (s[1][1] - s[0][1]);
            if area.abs() < 1e-12 || !area.is_finite() {
                continue;
            }
            let min_x = s.iter().map(|p| p[0]).fold(f32::MAX, f32::min).floor().max(0.0) as usize;
            let max_x = (s.iter().map(|p| p[0]).fold(f32::MIN, f32::max).ceil() as i64)
                .min(self.width as i64);
            let min_y = (s.iter().map(|p| p[1]).fold(f32::MAX, f32::min).floor() as i64)
                .max(band.y0 as i64);
            let max_y = (s.iter().map(|p| p[1]).fold(f32::MIN, f32::max).ceil() as i64)
                .min((band.y0 + rows) as i64);
            if max_x <= min_x as i64 || max_y <= min_y {
                continue;
            }
            let corners = idx.map(|k| &poly[k]);
            for y in min_y as usize..max_y as usize {
                let py = y as f32 + 0.5;
                for x in min_x..max_x as usize {
                    let px = x as f32 + 0.5;
                    let e = |a: [f32; 4], b: [f32; 4]| {
                        (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])
                    };
                    let b0 = e(s[1], s[2]) / area;
                    let b1 = e(s[2], s[0]) / area;
                    let b2 = e(s[0], s[1]) / area;
                    if b0 < 0.0 || b1 < 0.0 || b2 < 0.0 {
                        continue;
                    }
                    let z = b0 * s[0][2] + b1 * s[1][2] + b2 * s[2][2];
                    let at = (y - band.y0) * self.width + x;
                    if !(z >= 0.0 && z < band.depth[at]) {
                        continue;
                    }
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
                    let rgb = self.shade(mesh, &c);
                    band.depth[at] = z;
                    band.color[at] = [rgb[0], rgb[1], rgb[2], 1.0];
                }
            }
        }
    }

    fn draw_triangle(&self, band: &mut Band, inst: &InstanceVerts, t: usize) {
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
        let poly = clip_near([corner(0), corner(1), corner(2)]);
        if poly.len() >= 3 {
            self.draw(band, mesh, &poly);
        }
    }
}

/// The bands a triangle can touch, or None when it cannot cover a pixel
/// centre at all: behind the camera, off screen, or smaller than a pixel
/// between centres (most of a dense shot's triangles at playblast size).
pub(crate) fn triangle_bands(clip: [[f32; 4]; 3], width: usize, height: usize, band_rows: usize)
    -> Option<(usize, usize)> {
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
    // Pixel centres sit at i + 0.5: the covered range is ceil(lo - 0.5) ..=
    // floor(hi - 0.5), clamped to the image.
    let px0 = (x0 - 0.5).ceil().max(0.0);
    let px1 = (x1 - 0.5).floor().min(w - 1.0);
    let py0 = (y0 - 0.5).ceil().max(0.0);
    let py1 = (y1 - 0.5).floor().min(h - 1.0);
    if px0 > px1 || py0 > py1 {
        return None;
    }
    Some((py0 as usize / band_rows, py1 as usize / band_rows))
}

/// Per band, the (instance, triangle) pairs that can touch it.
fn bin(instances: &[InstanceVerts], meshes: &[Mesh], width: usize, height: usize,
       band_rows: usize, band_count: usize) -> Vec<Vec<(u32, u32)>> {
    let next = AtomicUsize::new(0);
    let parts: Mutex<Vec<Vec<Vec<(u32, u32)>>>> = Mutex::new(Vec::new());
    std::thread::scope(|scope| {
        for _ in 0..threads() {
            scope.spawn(|| {
                let mut local: Vec<Vec<(u32, u32)>> = vec![Vec::new(); band_count];
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= instances.len() {
                        break;
                    }
                    let inst = &instances[i];
                    for (t, tri) in meshes[inst.mesh].triangles.iter().enumerate() {
                        let vi = tri.map(|v| v as usize);
                        if vi.iter().any(|&v| v >= inst.clip.len()) {
                            continue;
                        }
                        let clip = vi.map(|v| inst.clip[v]);
                        if let Some((b0, b1)) = triangle_bands(clip, width, height, band_rows) {
                            for band in local.iter_mut().take(b1.min(band_count - 1) + 1).skip(b0) {
                                band.push((i as u32, t as u32));
                            }
                        }
                    }
                }
                parts.lock().unwrap().push(local);
            });
        }
    });
    let mut bins: Vec<Vec<(u32, u32)>> = vec![Vec::new(); band_count];
    for part in parts.into_inner().unwrap() {
        for (bin, mut items) in bins.iter_mut().zip(part) {
            bin.append(&mut items);
        }
    }
    bins
}

fn transform_instance(mesh_index: usize, mesh: &Mesh, smooth: Option<&Vec<Vec3>>,
                      xform: &Mat4, world_to_clip: &Mat4) -> InstanceVerts {
    let to_clip = math::mul(xform, world_to_clip);
    let nm = math::normal_matrix(xform);
    let clip = mesh
        .points
        .iter()
        .map(|&p| {
            let c = math::transform_point4(p, &to_clip);
            [c[0] as f32, c[1] as f32, c[2] as f32, c[3] as f32]
        })
        .collect();
    let world = mesh.points.iter().map(|&p| math::transform_point(p, xform)).collect();
    let normals = match (mesh.corner_normals, smooth) {
        (Some(n), _) => n.iter().map(|&n| math::transform_normal(n, &nm)).collect(),
        (None, Some(n)) => n.iter().map(|&n| math::transform_normal(n, &nm)).collect(),
        (None, None) => Vec::new(),
    };
    InstanceVerts { mesh: mesh_index, clip, world, normals }
}

/// The eye position and the world-space direction towards the key light.
pub(crate) fn headlight(view_to_world: &Mat4) -> (Vec3, Vec3) {
    let vw = view_to_world;
    let eye = [vw[12] as f32, vw[13] as f32, vw[14] as f32];
    let axis = |r: usize| [vw[r * 4] as f32, vw[r * 4 + 1] as f32, vw[r * 4 + 2] as f32];
    let k = math::normalize(KEY_DIRECTION);
    let key = math::normalize(math::add(
        math::add(math::scale(axis(0), k[0]), math::scale(axis(1), k[1])),
        math::scale(axis(2), k[2]),
    ));
    (eye, key)
}

/// Box-filter a supersampled `w x h` image down by `ss`; depth keeps the
/// nearest sample.
pub(crate) fn downsample(color: Vec<[f32; 4]>, depth: Vec<f32>, w: usize, h: usize, ss: usize) -> Image {
    if ss == 1 {
        return Image { color, depth };
    }
    let (ow, oh) = (w / ss, h / ss);
    let mut out_color = vec![[0.0f32; 4]; ow * oh];
    let mut out_depth = vec![1.0f32; ow * oh];
    let inv = 1.0 / (ss * ss) as f32;
    for y in 0..oh {
        for x in 0..ow {
            let mut sum = [0.0f32; 4];
            let mut near = 1.0f32;
            for sy in 0..ss {
                for sx in 0..ss {
                    let at = (y * ss + sy) * w + x * ss + sx;
                    for c in 0..4 {
                        sum[c] += color[at][c];
                    }
                    near = near.min(depth[at]);
                }
            }
            out_color[y * ow + x] = sum.map(|v| v * inv);
            out_depth[y * ow + x] = near;
        }
    }
    Image { color: out_color, depth: out_depth }
}

fn threads() -> usize {
    std::thread::available_parallelism().map_or(4, |n| n.get())
}

/// Render `meshes` through `frame`'s camera at `frame.width x frame.height`.
pub fn render(frame: &Frame, meshes: &[Mesh]) -> Image {
    let ss = frame.supersample.clamp(1, 4);
    let (w, h) = (frame.width * ss, frame.height * ss);

    let mut color = vec![[0.0f32; 4]; w * h];
    let mut depth = vec![1.0f32; w * h];
    for y in 0..h {
        let t = if h > 1 { y as f32 / (h - 1) as f32 } else { 0.0 };
        let g = srgb_to_linear(BACKDROP_TOP + (BACKDROP_BOTTOM - BACKDROP_TOP) * t);
        color[y * w..(y + 1) * w].fill([g, g, g, 1.0]);
    }

    let (eye, key) = headlight(&frame.view_to_world);
    let light = Light { eye, key };

    let smooth: Vec<Option<Vec<Vec3>>> = meshes
        .iter()
        .map(|m| m.corner_normals.is_none().then(|| smooth_normals(m.points, m.triangles)))
        .collect();

    // Every (mesh, instance) pair, then batches of them under the budget.
    let jobs: Vec<(usize, usize)> = meshes
        .iter()
        .enumerate()
        .flat_map(|(m, mesh)| (0..mesh.transforms.len()).map(move |i| (m, i)))
        .collect();
    let raster = Raster { width: w, height: h, meshes, light: &light };
    let band_rows = 16usize.max(h / (threads() * 8)).min(h.max(1));

    let mut start = 0;
    while start < jobs.len() {
        let mut end = start;
        let mut verts = 0;
        while end < jobs.len() && (end == start || verts + meshes[jobs[end].0].points.len() <= VERTEX_BUDGET) {
            verts += meshes[jobs[end].0].points.len();
            end += 1;
        }
        let batch = &jobs[start..end];
        start = end;

        // Transform the batch in parallel.
        let next = AtomicUsize::new(0);
        let transformed: Mutex<Vec<(usize, InstanceVerts)>> = Mutex::new(Vec::with_capacity(batch.len()));
        std::thread::scope(|scope| {
            for _ in 0..threads() {
                scope.spawn(|| loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= batch.len() {
                        break;
                    }
                    let (m, inst) = batch[i];
                    let v = transform_instance(m, &meshes[m], smooth[m].as_ref(),
                                               &meshes[m].transforms[inst], &frame.world_to_clip);
                    transformed.lock().unwrap().push((i, v));
                });
            }
        });
        let mut transformed = transformed.into_inner().unwrap();
        transformed.sort_by_key(|(i, _)| *i);
        let instances: Vec<InstanceVerts> = transformed.into_iter().map(|(_, v)| v).collect();

        // Sort the batch's triangles into the bands they touch, then
        // rasterise, each thread taking whole bands and only their triangles.
        let band_count = h.div_ceil(band_rows);
        let bins = bin(&instances, meshes, w, h, band_rows, band_count);
        let bands: Vec<(Band, &Vec<(u32, u32)>)> = color
            .chunks_mut(band_rows * w)
            .zip(depth.chunks_mut(band_rows * w))
            .enumerate()
            .map(|(i, (c, d))| (Band { y0: i * band_rows, color: c, depth: d }, &bins[i]))
            .collect();
        let queue = Mutex::new(bands);
        std::thread::scope(|scope| {
            for _ in 0..threads() {
                scope.spawn(|| loop {
                    let item = queue.lock().unwrap().pop();
                    let Some((mut band, triangles)) = item else { break };
                    for &(inst, t) in triangles {
                        raster.draw_triangle(&mut band, &instances[inst as usize], t as usize);
                    }
                });
            }
        });
    }

    downsample(color, depth, w, h, ss)
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
        Frame { width: size, height: size, world_to_clip, view_to_world, supersample: 1 }
    }

    #[test]
    fn a_red_quad_facing_the_camera_covers_the_middle() {
        let (points, triangles) = quad();
        let transforms = [math::IDENTITY];
        let mesh = Mesh {
            points: &points, triangles: &triangles, corner_normals: None,
            corner_colors: None, corner_uvs: None, base_color: [1.0, 0.0, 0.0],
            tiles: Vec::new(), transforms: &transforms,
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
                   tiles: Vec::new(), transforms: &red },
            Mesh { points: &points, triangles: &triangles, corner_normals: None,
                   corner_colors: None, corner_uvs: None, base_color: [0.0, 0.0, 1.0],
                   tiles: Vec::new(), transforms: &other },
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
                          tiles: Vec::new(), transforms: &transforms };
        let img = render(&frame(64), &[mesh]);
        assert!(img.color[60 * 64 + 32][1] > 0.3, "floor visible below the horizon");
        assert_eq!(img.depth[4 * 64 + 32], 1.0, "nothing above it");
    }

    #[test]
    fn binning_skips_what_cannot_cover_a_pixel_centre() {
        // Clip coords with w = 1 map x, y in [-1, 1] onto a 100 x 100 image.
        let at = |x: f32, y: f32| [x, y, 0.0, 1.0];
        // Spans the image: every band.
        assert_eq!(triangle_bands([at(-1.0, 1.0), at(1.0, 1.0), at(0.0, -1.0)], 100, 100, 10), Some((0, 9)));
        // A sliver between two pixel centres: nothing.
        assert_eq!(triangle_bands([at(0.001, 0.0), at(0.009, 0.0), at(0.005, 0.009)], 100, 100, 10), None);
        // Off screen, and behind the camera.
        assert_eq!(triangle_bands([at(2.0, 0.0), at(3.0, 0.0), at(2.5, 0.5)], 100, 100, 10), None);
        let behind = [-1.0, -1.0, -2.0, 1.0];
        assert_eq!(triangle_bands([behind, behind, behind], 100, 100, 10), None);
    }

    #[test]
    fn supersampling_keeps_the_frame_size() {
        let (points, triangles) = quad();
        let transforms = [math::IDENTITY];
        let mesh = Mesh { points: &points, triangles: &triangles, corner_normals: None,
                          corner_colors: None, corner_uvs: None, base_color: DEFAULT_ALBEDO,
                          tiles: Vec::new(), transforms: &transforms };
        let mut f = frame(40);
        f.supersample = 3;
        let img = render(&f, &[mesh]);
        assert_eq!(img.color.len(), 40 * 40);
    }
}
