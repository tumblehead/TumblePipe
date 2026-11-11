//! Basis curves and points, drawn as the GL viewport draws them: curves as
//! ribbons that face the camera, points as discs that face it, each of its
//! authored width but never thinner than [`MIN_CURVE_PIXELS`] /
//! [`MIN_POINT_PIXELS`] at its depth, so a curve or a point with no width
//! still shows as a line or a dot.
//!
//! Facing the camera, and the pixel floor, need the frame's camera and size,
//! so the geometry is made per frame ([`expand`]), in world space, as
//! ordinary triangles: both rasterisers, the ids, alpha, depth and the blur
//! layers then draw it as they draw a mesh. Its normals face the camera,
//! bent across a ribbon (and out to a disc's rim) like a tube's (a
//! sphere's), so lit and clay shading round them off.
//!
//! Cubic curves (USD's bezier, bspline and catmullRom bases, wrapped
//! nonperiodic, periodic or pinned) are evaluated to polylines here, each
//! span cut into as many segments as its control polygon spans pixels over
//! [`SEGMENT_PIXELS`] ([`steps_for`]), so a curve is smooth at any size.

use crate::math::{self, Mat4, Vec3};
use crate::raster::{Frame, Mesh};

/// Thinnest a curve is drawn, in pixels across, at its depth.
pub const MIN_CURVE_PIXELS: f32 = 1.0;
/// Least an open curve is lengthened by, in pixels, half past each end: a
/// line a pixel wide and about a pixel long can fall between pixel centres
/// whatever its angle; 1.5 px longer it always crosses one. An authored
/// width caps each end by half of it when that is more.
pub const CAP_PIXELS: f32 = 1.5;
/// Smallest a point is drawn, in pixels across, at its depth.
pub const MIN_POINT_PIXELS: f32 = 2.0;
/// About how long a segment of a cubic span is on screen, in pixels.
pub const SEGMENT_PIXELS: f32 = 4.0;
/// Most segments a cubic span is cut into.
pub const MOST_STEPS: usize = 64;
/// Segments of a cubic span not wholly in front of the camera.
pub const UNSEEN_STEPS: usize = 16;

/// A cubic curve's basis (USD's `basis`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Basis {
    Bezier,
    Bspline,
    CatmullRom,
}

/// How a curve's ends are treated (USD's `wrap`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Wrap {
    Nonperiodic,
    /// Closed: the last vertex runs back to the first.
    Periodic,
    /// A bspline or catmullRom curve that reaches its end vertices (USD
    /// adds a phantom vertex at each end, `2 P0 - P1`); bezier and linear
    /// curves as nonperiodic.
    Pinned,
}

/// What a [`Strokes`] draws.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Points,
    Linear,
    Cubic(Basis),
}

/// How a width or colour primvar spreads (Hydra's interpolation; for
/// points, varying and vertex are per point).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Interp {
    Constant,
    /// One per curve.
    Uniform,
    /// One per segment end of a cubic curve (per vertex of a linear one),
    /// interpolated linearly.
    Varying,
    /// One per vertex, interpolated by the curve's basis.
    Vertex,
}

impl Kind {
    pub fn from_abi(kind: u32, basis: u32) -> Kind {
        match kind {
            0 => Kind::Points,
            1 => Kind::Linear,
            _ => Kind::Cubic(match basis {
                1 => Basis::Bspline,
                2 => Basis::CatmullRom,
                _ => Basis::Bezier,
            }),
        }
    }
}

impl Wrap {
    pub fn from_abi(v: u32) -> Wrap {
        match v {
            1 => Wrap::Periodic,
            2 => Wrap::Pinned,
            _ => Wrap::Nonperiodic,
        }
    }
}

impl Interp {
    pub fn from_abi(v: u32) -> Interp {
        match v {
            1 => Interp::Uniform,
            2 => Interp::Varying,
            3 => Interp::Vertex,
            _ => Interp::Constant,
        }
    }
}

/// The basis matrix: a span's point at `t` is `[t^3 t^2 t 1] M [P0 P1 P2 P3]`.
pub fn basis_matrix(basis: Basis) -> [[f32; 4]; 4] {
    match basis {
        Basis::Bezier => [[-1.0, 3.0, -3.0, 1.0], [3.0, -6.0, 3.0, 0.0], [-3.0, 3.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],
        Basis::Bspline => {
            let s = 1.0 / 6.0;
            [[-s, 3.0 * s, -3.0 * s, s], [3.0 * s, -6.0 * s, 3.0 * s, 0.0], [-3.0 * s, 0.0, 3.0 * s, 0.0],
             [s, 4.0 * s, s, 0.0]]
        }
        Basis::CatmullRom => [[-0.5, 1.5, -1.5, 0.5], [1.0, -2.5, 2.0, -0.5], [-0.5, 0.0, 0.5, 0.0], [0.0, 1.0, 0.0, 0.0]],
    }
}

/// The weights of a span's four control values at `t` in [0, 1].
pub fn weights(basis: Basis, t: f32) -> [f32; 4] {
    let m = basis_matrix(basis);
    let tt = [t * t * t, t * t, t, 1.0];
    std::array::from_fn(|j| (0..4).map(|i| tt[i] * m[i][j]).sum())
}

/// A span's value at `t`: its four control values weighted by the basis.
pub fn evaluate<const D: usize>(basis: Basis, controls: [[f32; D]; 4], t: f32) -> [f32; D] {
    let w = weights(basis, t);
    std::array::from_fn(|d| (0..4).map(|j| w[j] * controls[j][d]).sum())
}

/// The spans of a cubic curve of `n` vertices: each the indices of its four
/// control values into the curve's values as [`controls`] extends them.
/// Empty when `n` is too few for the basis and wrap.
pub fn spans(n: usize, basis: Basis, wrap: Wrap) -> Vec<[usize; 4]> {
    match (basis, wrap) {
        (Basis::Bezier, Wrap::Periodic) => {
            if n < 3 || n % 3 != 0 {
                return Vec::new();
            }
            (0..n / 3).map(|s| std::array::from_fn(|k| (3 * s + k) % n)).collect()
        }
        (Basis::Bezier, _) => {
            if n < 4 || (n - 1) % 3 != 0 {
                return Vec::new();
            }
            (0..(n - 1) / 3).map(|s| std::array::from_fn(|k| 3 * s + k)).collect()
        }
        (_, Wrap::Periodic) => {
            if n < 3 {
                return Vec::new();
            }
            (0..n).map(|s| std::array::from_fn(|k| (s + k) % n)).collect()
        }
        // Over the values with a phantom at each end (n + 2 of them).
        (_, Wrap::Pinned) => {
            if n < 2 {
                return Vec::new();
            }
            (0..n - 1).map(|s| std::array::from_fn(|k| s + k)).collect()
        }
        (_, Wrap::Nonperiodic) => {
            if n < 4 {
                return Vec::new();
            }
            (0..n - 3).map(|s| std::array::from_fn(|k| s + k)).collect()
        }
    }
}

/// A curve's control values as its [`spans`] index them: as authored, or
/// for a pinned bspline or catmullRom curve with a phantom value at each
/// end (`2 V0 - V1`, `2 Vn-1 - Vn-2`), which makes the curve reach its end
/// vertices.
pub fn controls<const D: usize>(values: &[[f32; D]], basis: Basis, wrap: Wrap) -> Vec<[f32; D]> {
    if wrap != Wrap::Pinned || basis == Basis::Bezier || values.len() < 2 {
        return values.to_vec();
    }
    let n = values.len();
    let phantom = |a: [f32; D], b: [f32; D]| std::array::from_fn(|d| 2.0 * a[d] - b[d]);
    let mut out = Vec::with_capacity(n + 2);
    out.push(phantom(values[0], values[1]));
    out.extend_from_slice(values);
    out.push(phantom(values[n - 1], values[n - 2]));
    out
}

/// Where along a curve each polyline vertex lies: the span and the
/// parameter in it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Sample {
    pub span: usize,
    pub t: f32,
}

/// The samples of a curve whose spans are cut into `steps[s]` segments
/// each (at least 1): every span's start and its inner points, and the
/// last span's end unless the curve is closed (it is then the first
/// span's start).
pub fn samples(steps: &[usize], closed: bool) -> Vec<Sample> {
    let mut out = Vec::new();
    for (span, &n) in steps.iter().enumerate() {
        let n = n.max(1);
        for k in 0..n {
            out.push(Sample { span, t: k as f32 / n as f32 });
        }
    }
    if !closed && !steps.is_empty() {
        out.push(Sample { span: steps.len() - 1, t: 1.0 });
    }
    out
}

/// Segments for a span whose control polygon is `pixels` long on screen
/// (None: not wholly in front of the camera): one per [`SEGMENT_PIXELS`],
/// at least 1, at most [`MOST_STEPS`]; [`UNSEEN_STEPS`] for None.
pub fn steps_for(pixels: Option<f32>) -> usize {
    match pixels {
        Some(p) if p.is_finite() => ((p / SEGMENT_PIXELS).ceil() as usize).clamp(1, MOST_STEPS),
        Some(_) => 1,
        None => UNSEEN_STEPS,
    }
}

/// What the frame's camera tells the geometry made for it.
#[derive(Clone, Copy, Debug)]
pub struct Camera {
    world_to_clip: Mat4,
    eye: Vec3,
    back: Vec3,
    up: Vec3,
    perspective: bool,
    /// Pixels a unit spans across the view at clip w 1, the lesser of x and y.
    pixels_per_unit: f64,
    width: f64,
    height: f64,
}

impl Camera {
    pub fn of(frame: &Frame) -> Camera {
        let v = &frame.view_to_world;
        let p = |r: usize, c: usize| (0..4).map(|k| v[r * 4 + k] * frame.world_to_clip[k * 4 + c]).sum::<f64>();
        let row = |r: usize| math::normalize([v[r * 4] as f32, v[r * 4 + 1] as f32, v[r * 4 + 2] as f32]);
        let (w, h) = (frame.width as f64, frame.height as f64);
        Camera {
            world_to_clip: frame.world_to_clip,
            eye: [v[12] as f32, v[13] as f32, v[14] as f32],
            back: row(2),
            up: row(1),
            perspective: p(2, 3).abs() > 1e-12,
            pixels_per_unit: 0.5 * (p(0, 0).abs() * w).min(p(1, 1).abs() * h),
            width: w,
            height: h,
        }
    }

    /// The world length one pixel spans at `p`'s depth (the longer of a
    /// pixel's width and height there).
    pub fn pixel(&self, p: Vec3) -> f32 {
        let w = if self.perspective { math::transform_point4(p, &self.world_to_clip)[3].abs().max(1e-6) } else { 1.0 };
        (w / self.pixels_per_unit.max(1e-12)) as f32
    }

    /// The unit direction from `p` towards the camera.
    pub fn toward(&self, p: Vec3) -> Vec3 {
        if self.perspective {
            let d = math::sub(self.eye, p);
            if math::dot(d, d) > 1e-20 { math::normalize(d) } else { self.back }
        } else {
            self.back
        }
    }

    /// `p` in pixels, when it is in front of the camera.
    pub fn screen(&self, p: Vec3) -> Option<[f32; 2]> {
        let c = math::transform_point4(p, &self.world_to_clip);
        (c[3] > 1e-6 && c.iter().all(|v| v.is_finite())).then(|| {
            [((c[0] / c[3] + 1.0) * 0.5 * self.width) as f32, ((1.0 - c[1] / c[3]) * 0.5 * self.height) as f32]
        })
    }
}

/// One curves or points prim, borrowed, in object space.
#[derive(Clone, Copy, Debug)]
pub struct Strokes<'a> {
    pub kind: Kind,
    pub wrap: Wrap,
    pub points: &'a [Vec3],
    /// Vertices per curve (ignored for points); curves running past
    /// `points`, and from there on, are not drawn.
    pub counts: &'a [i32],
    pub widths: &'a [f32],
    pub width_interp: Interp,
    pub colors: &'a [Vec3],
    pub color_interp: Interp,
}

/// Geometry made for one frame: triangles, with a normal and a colour per
/// corner.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Made {
    pub points: Vec<Vec3>,
    pub triangles: Vec<[u32; 3]>,
    pub normals: Vec<Vec3>,
    /// Per corner; empty when the prim has no colours of its own.
    pub colors: Vec<Vec3>,
}

/// How the geometry is cut: segments per cubic span, sides per point. A
/// shutter's opening and closing are cut alike, so the two line up vertex
/// for vertex.
pub type Plan = Vec<usize>;

/// Normals at a ribbon's edges lean this far (sine) from facing the camera
/// towards the side, as a tube's do part way round.
const EDGE_LEAN: f32 = 0.8;

/// The geometry of `s` through `camera`, its object-to-world `transform`:
/// world-space triangles facing the camera. `plan` cuts it as a previous
/// call did (the shutter's opening, for its closing); None works one out,
/// returned alongside. `points` overrides `s.points` (the closing's).
pub fn expand(s: &Strokes, points: &[Vec3], transform: &Mat4, camera: &Camera, plan: Option<&Plan>) -> (Made, Plan) {
    let world: Vec<Vec3> = points.iter().map(|&p| math::transform_point(p, transform)).collect();
    let scale = mean_scale(transform);
    let mut made = Made::default();
    let mut used = Vec::new();
    let mut cursor = 0usize;
    match s.kind {
        Kind::Points => {
            let width = Lookup::new(s.widths, s.width_interp, 1, points.len(), points.len());
            let color = Lookup::new(s.colors, s.color_interp, 1, points.len(), points.len());
            for (i, &p) in world.iter().enumerate() {
                let r = (0.5 * width.at(0, i, i).unwrap_or(0.0) * scale).max(0.5 * MIN_POINT_PIXELS * camera.pixel(p));
                let sides = match plan {
                    Some(plan) => plan.get(i).copied().unwrap_or(6),
                    None => disc_sides(r / camera.pixel(p)),
                };
                used.push(sides);
                disc(&mut made, p, r, color.at(0, i, i), camera, sides);
            }
        }
        Kind::Linear | Kind::Cubic(_) => {
            let total: usize = s.counts.iter().map(|&c| c.max(0) as usize).sum();
            let curves = s.counts.len();
            let varying_total: usize = s.counts.iter().map(|&c| varying_count(s.kind, s.wrap, c.max(0) as usize)).sum();
            let width = Lookup::new(s.widths, s.width_interp, curves, varying_total, total);
            let color = Lookup::new(s.colors, s.color_interp, curves, varying_total, total);
            let mut varying_at = 0usize;
            for (c, &count) in s.counts.iter().enumerate() {
                let n = count.max(0) as usize;
                if cursor + n > world.len() {
                    break;
                }
                let verts = &world[cursor..cursor + n];
                let line = polyline(s, c, verts, cursor, varying_at, &width, &color, camera, plan, &mut used);
                if let Some((pts, widths, colors, closed)) = line {
                    let halves: Vec<f32> = pts
                        .iter()
                        .zip(&widths)
                        .map(|(&p, &w)| (0.5 * w * scale).max(0.5 * MIN_CURVE_PIXELS * camera.pixel(p)))
                        .collect();
                    let n = pts.len();
                    let cap = |i: usize| halves[i].max(0.5 * CAP_PIXELS * camera.pixel(pts[i]));
                    let caps = if n > 0 { [cap(0), cap(n - 1)] } else { [0.0; 2] };
                    ribbon(&mut made, &pts, &halves, &colors, closed, caps, camera);
                }
                cursor += n;
                varying_at += varying_count(s.kind, s.wrap, n);
            }
        }
    }
    if s.colors.is_empty() {
        made.colors.clear();
    }
    (made, used)
}

/// Varying values of a curve of `n` vertices: one per segment end.
fn varying_count(kind: Kind, wrap: Wrap, n: usize) -> usize {
    match kind {
        Kind::Points | Kind::Linear => n,
        Kind::Cubic(b) => {
            let s = spans(n, b, wrap).len();
            if s == 0 { 0 } else if wrap == Wrap::Periodic { s } else { s + 1 }
        }
    }
}

/// A primvar's values and how to find a curve's (or point's) in them; one
/// whose count does not fit its interpolation reads as its first value.
struct Lookup<'a, T> {
    values: &'a [T],
    interp: Interp,
}

impl<'a, T: Copy> Lookup<'a, T> {
    fn new(values: &'a [T], interp: Interp, uniform: usize, varying: usize, vertex: usize) -> Lookup<'a, T> {
        let want = match interp {
            Interp::Constant => 1,
            Interp::Uniform => uniform,
            Interp::Varying => varying,
            Interp::Vertex => vertex,
        };
        let interp = if values.len() >= want && want > 0 { interp } else { Interp::Constant };
        Lookup { values, interp }
    }

    /// The value of curve `curve` at varying index `varying` and vertex
    /// index `vertex` (all global).
    fn at(&self, curve: usize, varying: usize, vertex: usize) -> Option<T> {
        let i = match self.interp {
            Interp::Constant => 0,
            Interp::Uniform => curve,
            Interp::Varying => varying,
            Interp::Vertex => vertex,
        };
        self.values.get(i).copied()
    }
}

type Line = (Vec<Vec3>, Vec<f32>, Vec<Option<Vec3>>, bool);

/// One curve (`verts`, world space, starting at vertex `first` and varying
/// value `varying_first`) as a polyline: points, widths, colours, closed.
#[allow(clippy::too_many_arguments)]
fn polyline(s: &Strokes, curve: usize, verts: &[Vec3], first: usize, varying_first: usize, width: &Lookup<f32>,
            color: &Lookup<Vec3>, camera: &Camera, plan: Option<&Plan>, used: &mut Plan) -> Option<Line> {
    let n = verts.len();
    let closed = s.wrap == Wrap::Periodic;
    match s.kind {
        Kind::Points => None,
        Kind::Linear => {
            if n < 2 {
                return None;
            }
            let widths = (0..n).map(|i| width.at(curve, varying_first + i, first + i).unwrap_or(0.0)).collect();
            let colors = (0..n).map(|i| color.at(curve, varying_first + i, first + i)).collect();
            Some((verts.to_vec(), widths, colors, closed && n > 2))
        }
        Kind::Cubic(basis) => {
            let spans = spans(n, basis, s.wrap);
            if spans.is_empty() {
                return None;
            }
            let pts = controls(verts, basis, s.wrap);
            let steps: Vec<usize> = match plan {
                Some(plan) => (0..spans.len()).map(|k| plan.get(used.len() + k).copied().unwrap_or(1)).collect(),
                None => spans
                    .iter()
                    .map(|sp| {
                        let screen: Option<Vec<[f32; 2]>> = sp.iter().map(|&i| camera.screen(pts[i])).collect();
                        steps_for(screen.map(|q| q.windows(2).map(|w| ((w[1][0] - w[0][0]).powi(2) + (w[1][1] - w[0][1]).powi(2)).sqrt()).sum()))
                    })
                    .collect(),
            };
            used.extend_from_slice(&steps);
            let vertex_w: Vec<[f32; 1]> = (0..n).map(|i| [width.at(curve, 0, first + i).unwrap_or(0.0)]).collect();
            let vertex_w = controls(&vertex_w, basis, s.wrap);
            let vertex_c: Vec<Vec3> = (0..n).map(|i| color.at(curve, 0, first + i).unwrap_or([0.0; 3])).collect();
            let vertex_c = controls(&vertex_c, basis, s.wrap);
            let varying = varying_count(s.kind, s.wrap, n);
            // A sample's varying values either side, and how far between.
            let along = |sample: &Sample| -> (usize, usize, f32) {
                let a = sample.span;
                let b = if closed { (a + 1) % varying.max(1) } else { a + 1 };
                (varying_first + a, varying_first + b, sample.t)
            };
            let mut points = Vec::new();
            let mut widths = Vec::new();
            let mut colors = Vec::new();
            for sample in samples(&steps, closed) {
                let sp = spans[sample.span];
                points.push(evaluate(basis, sp.map(|i| pts[i]), sample.t));
                widths.push(match width.interp {
                    Interp::Vertex => evaluate(basis, sp.map(|i| vertex_w[i]), sample.t)[0].max(0.0),
                    Interp::Varying => {
                        let (a, b, t) = along(&sample);
                        let (wa, wb) = (width.at(curve, a, 0).unwrap_or(0.0), width.at(curve, b, 0).unwrap_or(0.0));
                        wa + (wb - wa) * t
                    }
                    _ => width.at(curve, 0, 0).unwrap_or(0.0),
                });
                colors.push(match color.interp {
                    Interp::Vertex => Some(evaluate(basis, sp.map(|i| vertex_c[i]), sample.t)),
                    Interp::Varying => {
                        let (a, b, t) = along(&sample);
                        match (color.at(curve, a, 0), color.at(curve, b, 0)) {
                            (Some(ca), Some(cb)) => Some(math::add(ca, math::scale(math::sub(cb, ca), t))),
                            (c, _) => c,
                        }
                    }
                    _ => color.at(curve, 0, 0),
                });
            }
            Some((points, widths, colors, closed))
        }
    }
}

/// Sides of a disc `r` pixels across its radius: enough to look round.
pub fn disc_sides(r_pixels: f32) -> usize {
    if r_pixels.is_finite() { ((std::f32::consts::TAU * r_pixels / 4.0).ceil() as usize).clamp(6, 24) } else { 6 }
}

/// A unit vector across the view at `p` (facing `toward`), any one.
fn across(camera: &Camera, toward: Vec3) -> Vec3 {
    let side = math::cross(camera.up, toward);
    if math::dot(side, side) > 1e-12 {
        math::normalize(side)
    } else {
        math::normalize(math::cross([1.0, 0.0, 0.0], toward))
    }
}

/// A disc of radius `r` at `p` facing the camera: a fan of `sides`
/// triangles round a centre vertex, its normals a sphere's.
fn disc(made: &mut Made, p: Vec3, r: f32, color: Option<Vec3>, camera: &Camera, sides: usize) {
    let v = camera.toward(p);
    let right = across(camera, v);
    let up = math::cross(v, right);
    let base = made.points.len() as u32;
    let mut normals = Vec::with_capacity(sides + 1);
    made.points.push(p);
    normals.push(v);
    for k in 0..sides {
        let a = std::f32::consts::TAU * k as f32 / sides as f32;
        let dir = math::add(math::scale(right, a.cos()), math::scale(up, a.sin()));
        made.points.push(math::add(p, math::scale(dir, r)));
        normals.push(math::normalize(math::add(math::scale(v, 0.6), math::scale(dir, EDGE_LEAN))));
    }
    for k in 0..sides as u32 {
        let (a, b) = (1 + k, 1 + (k + 1) % sides as u32);
        made.triangles.push([base, base + a, base + b]);
        for i in [0, a, b] {
            made.normals.push(normals[i as usize]);
            made.colors.push(color.unwrap_or([0.0; 3]));
        }
    }
}

/// Most a ribbon widens at a bend to keep its width across it (a mitre),
/// as a multiple of its half width; sharper bends than 60 degrees narrow.
const MOST_MITRE: f32 = 2.0;

/// The unit direction of `d`, None for (nearly) none.
fn direction(d: Vec3) -> Option<Vec3> {
    let len2 = math::dot(d, d);
    (len2 > 1e-30 && len2.is_finite()).then(|| math::scale(d, 1.0 / len2.sqrt()))
}

/// The unit vector across a ribbon whose centre line runs along `tangent`
/// at a point seen along `v` (towards the camera); any across one when the
/// line runs along `v` or has no length.
fn side_of(tangent: Vec3, v: Vec3, camera: &Camera) -> Vec3 {
    direction(math::cross(tangent, v)).unwrap_or_else(|| across(camera, v))
}

/// Bends sharper than this (degrees) break a ribbon, each side capped past
/// the bend, rather than mitring it: a mitre that sharp would be long, and
/// clamped it narrows the ribbon to nothing at a hairpin.
const BREAK_DEGREES: f32 = 90.0;

/// A camera-facing ribbon along `pts`, `halves[i]` either side of point i:
/// mitred at gentle bends, broken at sharp ones ([`BREAK_DEGREES`]). Each
/// open end runs on past its point by a cap (`caps` for the curve's first
/// and last ends; at a break, the larger of the half width and half
/// [`CAP_PIXELS`]), so a line shorter than a pixel still crosses one: a
/// pixel-wide square can sit between pixel centres and cover none.
///
/// Every point makes the same four vertices whatever the bends (the end of
/// the segment coming in, the start of the one going out; the same two
/// where the ribbon is not broken), so a shutter's opening and closing line
/// up vertex for vertex even when a bend sharpens between them.
fn ribbon(made: &mut Made, pts: &[Vec3], halves: &[f32], colors: &[Option<Vec3>], closed: bool, caps: [f32; 2],
          camera: &Camera) {
    let n = pts.len();
    if n < 2 {
        return;
    }
    let cos_break = BREAK_DEGREES.to_radians().cos();
    // The way the line leaves its first end and reaches its last (when it
    // has any length), for the end caps.
    let first_way = (1..n).find_map(|k| direction(math::sub(pts[k], pts[0])));
    let last_way = (0..n - 1).rev().find_map(|k| direction(math::sub(pts[n - 1], pts[k])));
    let base = made.points.len() as u32;
    let mut normals = Vec::with_capacity(4 * n);
    let mut vcolors = Vec::with_capacity(4 * n);
    let mut push_pair = |made: &mut Made, (centre, side, h): (Vec3, Vec3, f32), v: Vec3, c: Vec3| {
        // The pixel floor where the pair is, not at its point: a cap runs
        // on deeper into the picture along a line going away.
        let h = h.max(0.5 * MIN_CURVE_PIXELS * camera.pixel(centre));
        made.points.push(math::sub(centre, math::scale(side, h)));
        made.points.push(math::add(centre, math::scale(side, h)));
        for s in [-1.0f32, 1.0] {
            normals.push(math::normalize(math::add(math::scale(v, 0.6), math::scale(side, s * EDGE_LEAN))));
            vcolors.push(c);
        }
    };
    for i in 0..n {
        let has_prev = i > 0 || closed;
        let has_next = i + 1 < n || closed;
        let prev = if i > 0 { pts[i - 1] } else { pts[n - 1] };
        let next = if i + 1 < n { pts[i + 1] } else { pts[0] };
        let v = camera.toward(pts[i]);
        let c = colors.get(i).copied().flatten().unwrap_or([0.0; 3]);
        // The bend as seen: the segments' directions across the view (with
        // what runs towards or away from the camera taken out). A line
        // that turns gently in depth can fold right back on screen.
        let seen = |d: Vec3| direction(math::sub(d, math::scale(v, math::dot(d, v))));
        // A cap runs on along the line itself (`way`, a unit direction in
        // the world), lengthened so that it is `cap` long across the view,
        // up to four times: a line running at the camera is foreshortened.
        // What that leaves short (a line pointing at the camera is a dot on
        // screen however long) runs on across the view instead, in the
        // line's direction as seen (`flat`).
        let run_on = |way: Vec3, flat: Vec3, cap: f32| {
            let across_view = (1.0 - math::dot(way, v).powi(2)).max(0.0).sqrt();
            let along = cap / across_view.max(0.25);
            let short = (cap - along * across_view).max(0.0);
            math::add(math::scale(way, along), math::scale(flat, short))
        };
        let in_way = if has_prev { direction(math::sub(pts[i], prev)) } else { None };
        let out_way = if has_next { direction(math::sub(next, pts[i])) } else { None };
        let d_in = in_way.and_then(seen);
        let d_out = out_way.and_then(seen);
        let h = halves[i];
        let cap = h.max(0.5 * CAP_PIXELS * camera.pixel(pts[i]));
        let (in_pair, out_pair) = match (d_in, d_out, in_way, out_way) {
            (Some(a), Some(b), Some(wa), Some(wb)) if math::dot(a, b) < cos_break => {
                // Broken: the segment coming in runs on past the point, the
                // one going out starts back before it.
                let (sa, sb) = (side_of(a, v, camera), side_of(b, v, camera));
                ((math::add(pts[i], run_on(wa, a, cap)), sa, h), (math::sub(pts[i], run_on(wb, b, cap)), sb, h))
            }
            (Some(a), Some(b), _, _) => {
                // Mitred: across the bisector, widened to keep the width
                // measured across the segment coming in.
                let side = side_of(math::add(a, b), v, camera);
                let mitre = 1.0 / math::dot(side, side_of(a, v, camera)).abs().max(1.0 / MOST_MITRE);
                let pair = (pts[i], side, h * mitre);
                (pair, pair)
            }
            _ if !closed && (i == 0 || i == n - 1) => {
                // A curve's own end: capped along the line; with no length
                // at all, along the line the ribbon would make.
                let way = if i == 0 { first_way } else { last_way };
                let side = match way.and_then(seen) {
                    Some(w) => side_of(w, v, camera),
                    None => across(camera, v),
                };
                let flat = math::cross(v, side);
                let way = way.unwrap_or(flat);
                let (sign, end_cap) = if i == 0 { (-1.0, caps[0]) } else { (1.0, caps[1]) };
                let pair = (math::add(pts[i], math::scale(run_on(way, flat, end_cap), sign)), side, h);
                (pair, pair)
            }
            _ => {
                // A repeated point inside the line: across the line through it.
                let pair = (pts[i], side_of(math::sub(next, prev), v, camera), h);
                (pair, pair)
            }
        };
        push_pair(made, in_pair, v, c);
        push_pair(made, out_pair, v, c);
    }
    let segments = if closed { n } else { n - 1 };
    for i in 0..segments {
        let j = (i + 1) % n;
        // From point i's outgoing pair to point j's incoming one.
        let (l0, r0, l1, r1) = (4 * i as u32 + 2, 4 * i as u32 + 3, 4 * j as u32, 4 * j as u32 + 1);
        for tri in [[l0, r0, r1], [l0, r1, l1]] {
            made.triangles.push(tri.map(|k| base + k));
            for k in tri {
                made.normals.push(normals[k as usize]);
                made.colors.push(vcolors[k as usize]);
            }
        }
    }
}

/// How much `m` scales lengths, on average over its axes.
fn mean_scale(m: &Mat4) -> f32 {
    let len = |r: usize| (m[r * 4] * m[r * 4] + m[r * 4 + 1] * m[r * 4 + 1] + m[r * 4 + 2] * m[r * 4 + 2]).sqrt();
    ((len(0) + len(1) + len(2)) / 3.0) as f32
}

/// The transform of geometry made in world space.
static WORLD: [Mat4; 1] = [math::IDENTITY];

/// `made` as a mesh to draw: world space, its own normals and colours
/// (else `base_color`), moving to `close` over the shutter when given.
/// Made anew each frame, it has no key: nothing of it is kept on the card.
pub fn mesh<'a>(made: &'a Made, close: Option<&'a [Vec3]>, base_color: Vec3) -> Mesh<'a> {
    Mesh {
        points: &made.points,
        triangles: &made.triangles,
        corner_normals: Some(&made.normals),
        corner_colors: (!made.colors.is_empty()).then_some(&made.colors[..]),
        corner_uvs: None,
        base_color,
        tiles: Vec::new(),
        transforms: &WORLD,
        key: None,
        points_close: close,
        transform_keys: &[],
        // Ribbons and dots are drawn opaque: a curve's displayOpacity is
        // not read (see-through surfaces are meshes' for now).
        opacity: 1.0, cull: crate::raster::Cull::Nothing,
    }
}
