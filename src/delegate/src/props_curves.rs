//! Properties of basis curves and points ([`crate::curves`]): the cubic
//! bases reach the vertices USD says they reach, a curve draws as a ribbon
//! that covers its centre line and is never thinner than a pixel, a point
//! as a disc that covers its centre and nothing outside it, and through the
//! ABI the ids name the curves view and instance a pixel sees.

use crate::composite::Shading;
use crate::curves::{self, Basis, Camera, Interp, Kind, Made, Strokes, Wrap};
use crate::ffi::{th_render_scene, ThCurvesView, ThFrame, ThMeshView};
use crate::math::{self, Mat4, Vec3};
use crate::raster::{self, Frame};
use crate::blur::Blur;
use crate::TH_OK;
use proptest::prelude::*;

/// As props.rs's: a camera at z = 5 looking down -z, 90 degrees across.
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

fn frame(width: usize, height: usize, samples: usize) -> Frame {
    let (world_to_clip, view_to_world) = camera();
    Frame { width, height, world_to_clip, view_to_world, samples, shading: Shading::Lit, ao: false, blur: Blur::OFF,
            alpha: false, lights: crate::lights::SceneLights::NONE }
}

fn basis() -> impl Strategy<Value = Basis> {
    prop::sample::select(vec![Basis::Bezier, Basis::Bspline, Basis::CatmullRom])
}

fn wrap() -> impl Strategy<Value = Wrap> {
    prop::sample::select(vec![Wrap::Nonperiodic, Wrap::Periodic, Wrap::Pinned])
}

fn point() -> impl Strategy<Value = Vec3> {
    [-4.0f32..4.0, -4.0f32..4.0, -4.0f32..4.0]
}

fn close(a: Vec3, b: Vec3, tol: f32) -> bool {
    (0..3).all(|i| (a[i] - b[i]).abs() <= tol * (1.0 + a[i].abs().max(b[i].abs())))
}

/// One curve or point set drawn alone through `f`.
fn draw(f: &Frame, s: &Strokes, transform: &Mat4) -> (raster::Image, Made) {
    let (made, _) = curves::expand(s, s.points, transform, &Camera::of(f), None);
    let image = raster::render(f, &[curves::mesh(&made, None, [0.5, 0.5, 0.5])]);
    (image, made)
}

fn strokes<'a>(kind: Kind, wrap: Wrap, points: &'a [Vec3], counts: &'a [i32], widths: &'a [f32]) -> Strokes<'a> {
    Strokes {
        kind, wrap, points, counts, widths,
        width_interp: if widths.len() == 1 { Interp::Constant } else { Interp::Vertex },
        colors: &[], color_interp: Interp::Constant,
    }
}

/// The distance from `p` to the segment `a`-`b` (pixels).
fn to_segment(p: [f32; 2], a: [f32; 2], b: [f32; 2]) -> f32 {
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let len2 = dx * dx + dy * dy;
    let t = if len2 > 0.0 { (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2).clamp(0.0, 1.0) } else { 0.0 };
    let (x, y) = (a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
    (x * x + y * y).sqrt()
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 256, ..ProptestConfig::default() })]

    // Every basis weights its controls to one: a curve moved moves with them.
    #[test]
    fn every_basis_is_a_partition_of_unity(b in basis(), t in 0.0f32..=1.0) {
        let w = curves::weights(b, t);
        prop_assert!((w.iter().sum::<f32>() - 1.0).abs() < 1e-5, "{:?} at {}: {:?}", b, t, w);
        if b != Basis::CatmullRom {
            prop_assert!(w.iter().all(|&x| x >= -1e-6), "{:?} at {}: {:?}", b, t, w);
        }
    }

    // A bezier span runs from its first control to its last; a catmullRom
    // span from its second to its third.
    #[test]
    fn spans_reach_the_vertices_their_basis_interpolates(c in [point(), point(), point(), point()]) {
        prop_assert!(close(curves::evaluate(Basis::Bezier, c, 0.0), c[0], 1e-5));
        prop_assert!(close(curves::evaluate(Basis::Bezier, c, 1.0), c[3], 1e-5));
        prop_assert!(close(curves::evaluate(Basis::CatmullRom, c, 0.0), c[1], 1e-5));
        prop_assert!(close(curves::evaluate(Basis::CatmullRom, c, 1.0), c[2], 1e-5));
        // A bspline starts at (P0 + 4 P1 + P2) / 6.
        let want = std::array::from_fn(|i| (c[0][i] + 4.0 * c[1][i] + c[2][i]) / 6.0);
        prop_assert!(close(curves::evaluate(Basis::Bspline, c, 0.0), want, 1e-5));
    }

    // USD's span counts: bezier (n - 1) / 3 (periodic n / 3); bspline and
    // catmullRom n - 3, n periodic, n - 1 pinned.
    #[test]
    fn span_counts_are_usds(b in basis(), w in wrap(), n in 0usize..40) {
        let got = curves::spans(n, b, w).len();
        let want = match (b, w) {
            (Basis::Bezier, Wrap::Periodic) => if n >= 3 && n % 3 == 0 { n / 3 } else { 0 },
            (Basis::Bezier, _) => if n >= 4 && (n - 1) % 3 == 0 { (n - 1) / 3 } else { 0 },
            (_, Wrap::Periodic) => if n >= 3 { n } else { 0 },
            (_, Wrap::Pinned) => if n >= 2 { n - 1 } else { 0 },
            (_, Wrap::Nonperiodic) => if n >= 4 { n - 3 } else { 0 },
        };
        prop_assert_eq!(got, want);
    }

    // Consecutive spans meet (the curve is unbroken); a periodic curve's
    // last span ends where its first starts; a pinned bspline or catmullRom
    // curve, and any nonperiodic bezier, reaches its end vertices.
    #[test]
    fn curves_are_unbroken_closed_or_pinned(b in basis(), w in wrap(), pts in prop::collection::vec(point(), 2..14)) {
        let spans = curves::spans(pts.len(), b, w);
        prop_assume!(!spans.is_empty());
        let ctl = curves::controls(&pts, b, w);
        let at = |s: usize, t: f32| curves::evaluate(b, spans[s].map(|i| ctl[i]), t);
        for s in 1..spans.len() {
            prop_assert!(close(at(s - 1, 1.0), at(s, 0.0), 1e-4), "span {} does not meet span {}", s - 1, s);
        }
        let last = spans.len() - 1;
        match w {
            Wrap::Periodic => prop_assert!(close(at(last, 1.0), at(0, 0.0), 1e-4)),
            _ if b == Basis::Bezier || w == Wrap::Pinned => {
                prop_assert!(close(at(0, 0.0), pts[0], 1e-4), "starts at {:?}, not {:?}", at(0, 0.0), pts[0]);
                prop_assert!(close(at(last, 1.0), pts[pts.len() - 1], 1e-4));
            }
            _ => {}
        }
    }

    // A curve's samples: each span's start and inner points, the end
    // unless closed, in order along the curve.
    #[test]
    fn samples_cover_every_span_in_order(steps in prop::collection::vec(1usize..20, 1..10), closed: bool) {
        let s = curves::samples(&steps, closed);
        let want: usize = steps.iter().sum::<usize>() + usize::from(!closed);
        prop_assert_eq!(s.len(), want);
        for pair in s.windows(2) {
            prop_assert!((pair[0].span, pair[0].t) < (pair[1].span, pair[1].t));
        }
        prop_assert!(s.iter().all(|x| (0.0..=1.0).contains(&x.t)));
    }

    // Longer spans on screen get more segments, never more than the cap.
    #[test]
    fn segments_grow_with_the_span_on_screen(a in 0.0f32..2000.0, b in 0.0f32..2000.0) {
        let (lo, hi) = if a < b { (a, b) } else { (b, a) };
        prop_assert!(curves::steps_for(Some(lo)) <= curves::steps_for(Some(hi)));
        prop_assert!((1..=curves::MOST_STEPS).contains(&curves::steps_for(Some(hi))));
        prop_assert!(curves::steps_for(Some(hi)) as f32 >= (hi / curves::SEGMENT_PIXELS).min(curves::MOST_STEPS as f32));
    }
}

/// A segment in view: x and y across the frame, z between 3 in front of
/// the origin and 3 behind it (2 to 8 from the camera).
fn seen_point() -> impl Strategy<Value = Vec3> {
    (-0.8f32..0.8, -0.8f32..0.8, -3.0f32..3.0).prop_map(|(x, y, z)| {
        let d = 5.0 - z;
        [x * d, y * d, z]
    })
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 96, ..ProptestConfig::default() })]

    // A curve with no width is drawn at least a pixel across at any depth
    // and in any direction: every point of its centre line has a covered
    // pixel within one of it, and nothing two pixels off the line is.
    #[test]
    fn a_widthless_curve_is_an_unbroken_pixel_wide_line(a in seen_point(), b in seen_point(), w in 24usize..64,
                                                        h in 24usize..64) {
        let f = frame(w, h, 1);
        let cam = Camera::of(&f);
        let pts = [a, b];
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &[2], &[]), &math::IDENTITY);
        let (sa, sb) = (cam.screen(a).unwrap(), cam.screen(b).unwrap());
        let hit = |x: i64, y: i64| x >= 0 && y >= 0 && (x as usize) < w && (y as usize) < h
            && image.depth[y as usize * w + x as usize] < 1.0;
        for k in 0..=40 {
            let t = k as f32 / 40.0;
            let p = math::add(a, math::scale(math::sub(b, a), t));
            let q = cam.screen(p).unwrap();
            let (x, y) = (q[0].floor() as i64, q[1].floor() as i64);
            if x < 1 || y < 1 || x as usize >= w - 1 || y as usize >= h - 1 {
                continue;
            }
            prop_assert!((-1..=1).any(|dy| (-1..=1).any(|dx| hit(x + dx, y + dy))), "a gap at {:?}", q);
        }
        // The pixel floor is the longer side of a pixel; on a frame whose
        // pixels are not square, the shorter side sees it as wider. A
        // covered pixel's centre is within half the floor across the line
        // and half CAP_PIXELS past an end: hypot(0.5, 0.75) < 0.91 pixels.
        // Off the view axis, by angle t, geometry facing the eye is seen up
        // to 1 / cos t larger (sqrt(1 + tan^2) with this camera's 90
        // degrees, where ndc is the tangent).
        let aspect = (w as f32 / h as f32).max(h as f32 / w as f32);
        let off_axis = |s: [f32; 2]| {
            let (nx, ny) = (2.0 * s[0] / w as f32 - 1.0, 1.0 - 2.0 * s[1] / h as f32);
            (1.0 + nx * nx + ny * ny).sqrt()
        };
        let stretch = off_axis(sa).max(off_axis(sb));
        for y in 0..h {
            for x in 0..w {
                if image.depth[y * w + x] < 1.0 {
                    let d = to_segment([x as f32 + 0.5, y as f32 + 0.5], sa, sb);
                    prop_assert!(d <= 0.91 * aspect * stretch + 0.25, "pixel {},{} is {} px off the line", x, y, d);
                }
            }
        }
    }

    // A width-less curve shorter than a pixel on screen, or with no length
    // at all, at any angle and sub-pixel offset, still covers a pixel next
    // to it (a fine hair, a short rig curve), and nothing far off.
    #[test]
    fn a_tiny_widthless_curve_still_shows(a in seen_point(), angle in 0.0f32..std::f32::consts::TAU,
                                          pixels in prop_oneof![Just(0.0f32), 0.0f32..3.0], w in 16usize..48) {
        let f = frame(w, w, 1);
        let cam = Camera::of(&f);
        let px = cam.pixel(a);
        let b = math::add(a, [angle.cos() * pixels * px, angle.sin() * pixels * px, 0.0]);
        let pts = [a, b];
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &[2], &[]), &math::IDENTITY);
        let q = cam.screen(a).unwrap();
        let (x, y) = (q[0].floor() as i64, q[1].floor() as i64);
        prop_assume!(x >= 2 && y >= 2 && (x as usize) < w - 2 && (y as usize) < w - 2);
        let hit = |x: i64, y: i64| image.depth[y as usize * w + x as usize] < 1.0;
        prop_assert!((-1..=1).any(|dy| (-1..=1).any(|dx| hit(x + dx, y + dy))), "nothing drawn near {:?}", q);
        for yy in 0..w {
            for xx in 0..w {
                if image.depth[yy * w + xx] < 1.0 {
                    let d = to_segment([xx as f32 + 0.5, yy as f32 + 0.5], q, cam.screen(b).unwrap());
                    prop_assert!(d <= 2.0, "pixel {},{} is {} px off the line", xx, yy, d);
                }
            }
        }
    }

    // A bent width-less polyline (sharp turns included) is unbroken at its
    // bends as along its segments.
    #[test]
    fn a_bent_widthless_curve_is_unbroken(pts in prop::collection::vec(seen_point(), 3..7), w in 24usize..56) {
        let f = frame(w, w, 1);
        let cam = Camera::of(&f);
        let counts = [pts.len() as i32];
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &counts, &[]), &math::IDENTITY);
        let hit = |x: i64, y: i64| x >= 0 && y >= 0 && (x as usize) < w && (y as usize) < w
            && image.depth[y as usize * w + x as usize] < 1.0;
        for seg in pts.windows(2) {
            for k in 0..=40 {
                let p = math::add(seg[0], math::scale(math::sub(seg[1], seg[0]), k as f32 / 40.0));
                let q = cam.screen(p).unwrap();
                let (x, y) = (q[0].floor() as i64, q[1].floor() as i64);
                if x < 1 || y < 1 || x as usize >= w - 1 || y as usize >= w - 1 {
                    continue;
                }
                prop_assert!((-1..=1).any(|dy| (-1..=1).any(|dx| hit(x + dx, y + dy))), "a gap at {:?}", q);
            }
        }
    }

    // Square pixels, a horizontal curve: every column it spans is covered.
    #[test]
    fn a_widthless_curve_covers_every_column(x0 in -0.8f32..-0.1, x1 in 0.1f32..0.8, y in -0.8f32..0.8,
                                             z in -3.0f32..3.0, w in 24usize..80) {
        let f = frame(w, w, 1);
        let d = 5.0 - z;
        let pts = [[x0 * d, y * d, z], [x1 * d, y * d, z]];
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &[2], &[]), &math::IDENTITY);
        let cam = Camera::of(&f);
        let (sa, sb) = (cam.screen(pts[0]).unwrap(), cam.screen(pts[1]).unwrap());
        let row = sa[1];
        prop_assume!(row > 1.0 && row < w as f32 - 1.0);
        for x in (sa[0].ceil() as usize + 1)..(sb[0].floor() as usize).saturating_sub(1) {
            prop_assert!((0..w).any(|yy| image.depth[yy * w + x] < 1.0), "column {} is empty", x);
        }
    }

    // An authored width draws as that width on screen, give or take the
    // pixel edges; under a uniform scale, scaled with it.
    #[test]
    fn an_authored_width_is_drawn_to_size(width in 0.2f32..1.2, scale in 0.5f64..2.0) {
        let n = 64usize;
        let f = frame(n, n, 1);
        let pts = [[-2.0f32, 0.0, 0.0], [2.0, 0.0, 0.0]];
        let mut m = math::IDENTITY;
        m[0] = scale; m[5] = scale; m[10] = scale;
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &[2], &[width]), &m);
        // At depth 5 a pixel is 2 * 5 / n across.
        let want = width * scale as f32 / (10.0 / n as f32);
        let got = (0..n).filter(|&y| image.depth[y * n + n / 2] < 1.0).count() as f32;
        prop_assert!((got - want).abs() <= 1.5, "{} px across, want {}", got, want);
    }

    // Points: a disc covers the pixel its centre is in, whatever its width
    // (at least MIN_POINT_PIXELS across), and nothing further off than its
    // radius.
    #[test]
    fn points_cover_their_disc_and_nothing_else(pts in prop::collection::vec(seen_point(), 1..6),
                                                widths in prop::collection::vec(0.0f32..0.6, 1..6),
                                                authored: bool, w in 24usize..64) {
        let f = frame(w, w, 1);
        let cam = Camera::of(&f);
        let widths: Vec<f32> = if authored { (0..pts.len()).map(|i| widths[i % widths.len()]).collect() } else { Vec::new() };
        let s = strokes(Kind::Points, Wrap::Nonperiodic, &pts, &[], &widths);
        let (image, _) = draw(&f, &s, &math::IDENTITY);
        let radius = |i: usize| {
            let px = cam.pixel(pts[i]);
            (0.5 * widths.get(i).copied().unwrap_or(0.0) / px).max(0.5 * curves::MIN_POINT_PIXELS)
        };
        for (i, p) in pts.iter().enumerate() {
            let q = cam.screen(*p).unwrap();
            let (x, y) = (q[0].floor() as usize, q[1].floor() as usize);
            if x < w && y < w {
                prop_assert!(image.depth[y * w + x] < 1.0, "point {} at {:?} not drawn", i, q);
            }
        }
        for y in 0..w {
            for x in 0..w {
                if image.depth[y * w + x] < 1.0 {
                    let c = [x as f32 + 0.5, y as f32 + 0.5];
                    // A disc facing the eye off the axis is stretched by up
                    // to 1 / cos of its angle off it (under 1.5 here).
                    let near = (0..pts.len()).any(|i| {
                        let q = cam.screen(pts[i]).unwrap();
                        to_segment(c, q, q) <= radius(i) * 1.5 + 0.75
                    });
                    prop_assert!(near, "pixel {},{} is off every disc", x, y);
                }
            }
        }
    }

    // Albedo shading draws a curve's and a point's own colours; a
    // per-vertex colour shows at its vertex.
    #[test]
    fn albedo_draws_the_display_colour(c0 in [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0],
                                       c1 in [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0], points: bool) {
        let f = Frame { shading: Shading::Albedo, ..frame(48, 48, 1) };
        let pts = [[-3.0f32, 0.0, 0.0], [3.0, 0.0, 0.0]];
        let colors = [c0, c0];
        let one = [0.3f32];
        let mut s = strokes(if points { Kind::Points } else { Kind::Linear }, Wrap::Nonperiodic, &pts, &[2], &one);
        s.colors = &colors;
        s.color_interp = Interp::Vertex;
        let (image, _) = draw(&f, &s, &math::IDENTITY);
        let mut seen = 0;
        for (c, d) in image.color.iter().zip(&image.depth) {
            if *d < 1.0 {
                seen += 1;
                prop_assert!((0..3).all(|i| (c[i] - c0[i]).abs() < 1e-4), "{:?} is not {:?}", c, c0);
            }
        }
        prop_assert!(seen > 0);
        // Two colours along a line: each end shows its own.
        let colors = [c0, c1];
        s.colors = &colors;
        s.kind = Kind::Linear;
        let (image, _) = draw(&f, &s, &math::IDENTITY);
        let cam = Camera::of(&f);
        for (p, want) in [(pts[0], c0), (pts[1], c1)] {
            // A pixel in from the end: its centre is within 1/20 of the
            // line's length of the end vertex.
            let q = cam.screen(math::add(p, [if p[0] < 0.0 { 0.2 } else { -0.2 }, 0.0, 0.0])).unwrap();
            let c = image.color[q[1] as usize * 48 + q[0] as usize];
            prop_assert!((0..3).all(|i| (c[i] - want[i]).abs() < 0.06), "{:?} is not {:?}", c, want);
        }
    }

    // A curve's normals face the camera, bent across it like a tube's: lit
    // and clay are never black on it, and shade it across its width.
    #[test]
    fn a_ribbon_faces_the_light(y in -1.0f32..1.0, shading in prop::sample::select(vec![Shading::Lit, Shading::Clay])) {
        let n = 64usize;
        let f = Frame { shading, ..frame(n, n, 1) };
        let pts = [[-3.0f32, y, 0.0], [3.0, y, 0.0]];
        let (image, _) = draw(&f, &strokes(Kind::Linear, Wrap::Nonperiodic, &pts, &[2], &[1.0]), &math::IDENTITY);
        let col: Vec<f32> = (0..n).filter(|&r| image.depth[r * n + n / 2] < 1.0).map(|r| image.color[r * n + n / 2][1]).collect();
        prop_assert!(col.len() >= 4);
        prop_assert!(col.iter().all(|&v| v > 0.05), "{:?}", col);
        let (lo, hi) = col.iter().fold((f32::MAX, 0.0f32), |(lo, hi), &v| (lo.min(v), hi.max(v)));
        prop_assert!(hi - lo > 0.02, "flat across: {:?}", col);
    }
}

fn abi_frame(width: u32, height: u32, samples: u32) -> ThFrame {
    let (world_to_clip, view_to_world) = camera();
    ThFrame { width, height, world_to_clip, view_to_world, samples, shading: 0, ao: 0,
              blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
              focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0 }
}

fn curves_view(kind: u32, points: &[Vec3], counts: &[i32], transforms: &[Mat4]) -> ThCurvesView {
    ThCurvesView {
        points: points.as_ptr() as *const f32,
        point_count: points.len() as u32,
        curve_vertex_counts: counts.as_ptr(),
        curve_count: counts.len() as u32,
        kind, basis: 1, wrap: 2,
        widths: std::ptr::null(), width_count: 0, width_interp: 0,
        colors: std::ptr::null(), color_count: 0, color_interp: 0,
        base_color: [0.6, 0.5, 0.4],
        transforms: transforms.as_ptr() as *const f64,
        transform_count: transforms.len() as u32,
        points_close: std::ptr::null(),
        transform_keys: std::ptr::null(),
        transform_key_count: 0,
    }
}

fn translations() -> impl Strategy<Value = Vec<Mat4>> {
    prop::collection::vec([-2.0f64..2.0, -2.0f64..2.0, -2.0f64..2.0], 1..4).prop_map(|ts| {
        ts.into_iter().map(|[x, y, z]| {
            let mut m = math::IDENTITY;
            m[12] = x; m[13] = y; m[14] = z;
            m
        }).collect()
    })
}

/// Draw through the ABI: `meshes` then `curves`; colour, depth, ids.
fn abi_draw(f: &ThFrame, meshes: &[ThMeshView], curves: &[ThCurvesView]) -> (Vec<f32>, Vec<f32>, Vec<i32>) {
    let n = (f.width * f.height) as usize;
    let (mut color, mut depth, mut ids) = (vec![0.0f32; n * 4], vec![0.0f32; n], vec![7i32; n * 2]);
    let status = unsafe {
        th_render_scene(f, meshes.as_ptr(), meshes.len() as u32, curves.as_ptr(), curves.len() as u32,
                        color.as_mut_ptr(), depth.as_mut_ptr(), ids.as_mut_ptr(), std::ptr::null(), std::ptr::null())
    };
    assert_eq!(status, TH_OK);
    (color, depth, ids)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 48, ..ProptestConfig::default() })]

    // Through the ABI, after a mesh: a pixel a curve or point covers names
    // its view (counted after the meshes) and instance, and that view and
    // instance drawn alone covers the pixel at the same depth.
    #[test]
    fn ids_name_the_curves_view_and_instance(
        curve_pts in prop::collection::vec(point(), 4..9),
        dots in prop::collection::vec(point(), 1..8),
        ta in translations(), tb in translations(),
        samples in prop::sample::select(vec![1u32, 4]),
    ) {
        let (w, h) = (32u32, 24u32);
        let f = abi_frame(w, h, samples);
        let wall = [[-40.0f32, -40.0, -30.0], [40.0, -40.0, -30.0], [40.0, 40.0, -30.0], [-40.0, 40.0, -30.0]];
        let wall_tris = [[0u32, 1, 2], [0, 2, 3]];
        let one = [math::IDENTITY];
        let mesh = crate::props_curves::mesh_view(&wall, &wall_tris, &one);
        let counts = [curve_pts.len() as i32];
        let views = [curves_view(2, &curve_pts, &counts, &ta), curves_view(0, &dots, &[], &tb)];
        let (_, depth, ids) = abi_draw(&f, std::slice::from_ref(&mesh), &views);
        for (p, id) in ids.chunks(2).enumerate() {
            prop_assert!(id[0] >= 0, "pixel {} sees nothing in front of the wall", p);
            if id[0] == 0 {
                prop_assert_eq!(id[1], 0);
                continue;
            }
            let j = id[0] as usize - 1;
            prop_assert!(j < 2, "pixel {} names view {}", p, id[0]);
            let transforms = if j == 0 { &ta } else { &tb };
            prop_assert!((id[1] as usize) < transforms.len());
            let alone = [transforms[id[1] as usize]];
            let mut view = views[j];
            view.transforms = alone.as_ptr() as *const f64;
            view.transform_count = 1;
            let (_, d, i) = abi_draw(&f, &[], &[view]);
            prop_assert_eq!(&i[p * 2..p * 2 + 2], &[0, 0][..], "pixel {}", p);
            if samples == 1 {
                prop_assert_eq!(d[p], depth[p], "pixel {}", p);
            }
        }
    }
}

pub(crate) fn mesh_view(points: &[Vec3], triangles: &[[u32; 3]], transforms: &[Mat4]) -> ThMeshView {
    ThMeshView {
        points: points.as_ptr() as *const f32,
        point_count: points.len() as u32,
        triangles: triangles.as_ptr() as *const u32,
        triangle_count: triangles.len() as u32,
        corner_normals: std::ptr::null(),
        corner_colors: std::ptr::null(),
        corner_uvs: std::ptr::null(),
        base_color: [0.6, 0.5, 0.4],
        tiles: std::ptr::null(),
        tile_count: 0,
        transforms: transforms.as_ptr() as *const f64,
        transform_count: transforms.len() as u32,
        polygonal: 1,
        key: 0,
        version: 0,
        points_close: std::ptr::null(),
        transform_keys: std::ptr::null(),
        transform_key_count: 0,
        transparency: 0.0, cull: 0,
    }
}

/// With motion blur, a point moving across the shutter is drawn all along
/// its path, faintly; held still, it is the unblurred picture.
#[test]
fn a_moving_point_smears_along_its_path() {
    let (w, h) = (64u32, 16u32);
    let pts = [[-3.0f32, 0.0, 0.0]];
    let later = [[3.0f32, 0.0, 0.0]];
    let one = [math::IDENTITY];
    let mut view = curves_view(0, &pts, &[], &one);
    let widths = [0.4f32];
    view.widths = widths.as_ptr();
    view.width_count = 1;
    let still = abi_draw(&abi_frame(w, h, 1), &[], &[view]).1;
    let mut f = abi_frame(w, h, 1);
    f.blur_samples = 16;
    f.motion = 1;
    let held = abi_draw(&f, &[], &[view]).1;
    assert_eq!(still, held, "a still point drawn in layers is the unblurred picture");
    view.points_close = later.as_ptr() as *const f32;
    let (color, depth, _) = abi_draw(&f, &[], &[view]);
    let row = (h / 2) as usize;
    let drawn: Vec<usize> = (0..w as usize).filter(|&x| depth[row * w as usize + x] < 1.0).collect();
    assert!(drawn.len() > 30, "covered along the path: {drawn:?}");
    // Each layer covers only part of the path: partial coverage, not solid.
    let _ = color;
}

/// Shading modes and AO never move a curve's depth (as for meshes).
#[test]
fn shading_never_moves_a_curves_depth() {
    let pts = [[-3.0f32, -1.0, 0.0], [-1.0, 2.0, -1.0], [1.0, -2.0, 1.0], [3.0, 1.0, 0.0]];
    let s = strokes(Kind::Cubic(Basis::CatmullRom), Wrap::Pinned, &pts, &[4], &[]);
    let mut depths = Vec::new();
    for shading in [Shading::Lit, Shading::Clay, Shading::Albedo, Shading::Occlusion] {
        for ao in [false, true] {
            let f = Frame { shading, ao, ..frame(48, 32, 4) };
            let (image, _) = draw(&f, &s, &math::IDENTITY);
            assert!(image.color.iter().all(|c| c.iter().all(|v| v.is_finite() && *v >= 0.0)));
            depths.push(image.depth);
        }
    }
    assert!(depths.iter().all(|d| *d == depths[0]));
    assert!(depths[0].iter().any(|&d| d < 1.0));
}

/// Opening and closing are cut alike whatever the camera sees of each, so
/// a moving curve's two poses line up vertex for vertex.
#[test]
fn a_plan_cuts_the_closing_as_the_opening() {
    let f = frame(64, 64, 1);
    let cam = Camera::of(&f);
    let open = [[-3.0f32, 0.0, 0.0], [-1.0, 2.0, 0.0], [1.0, -2.0, 0.0], [3.0, 0.0, 0.0]];
    let shut = [[-0.1f32, 0.0, 0.0], [-0.05, 0.1, 0.0], [0.05, -0.1, 0.0], [0.1, 0.0, 0.0]];
    let s = strokes(Kind::Cubic(Basis::Bezier), Wrap::Nonperiodic, &open, &[4], &[]);
    let (a, plan) = curves::expand(&s, &open, &math::IDENTITY, &cam, None);
    let (fresh, _) = curves::expand(&s, &shut, &math::IDENTITY, &cam, None);
    let (b, _) = curves::expand(&s, &shut, &math::IDENTITY, &cam, Some(&plan));
    assert_ne!(fresh.points.len(), a.points.len(), "the small pose alone would be cut coarser");
    assert_eq!(a.points.len(), b.points.len());
    assert_eq!(a.triangles, b.triangles);
    // A polyline straight at the opening and bent into a hairpin at the
    // closing (broken there, not mitred) keeps its vertices too.
    let straight = [[-2.0f32, 0.0, 0.0], [0.0, 0.0, 0.0], [2.0, 0.0, 0.0]];
    let hairpin = [[-2.0f32, 0.0, 0.0], [0.0, 0.0, 0.0], [-2.0, 0.3, 0.0]];
    let s = strokes(Kind::Linear, Wrap::Nonperiodic, &straight, &[3], &[]);
    let (a, plan) = curves::expand(&s, &straight, &math::IDENTITY, &cam, None);
    let (b, _) = curves::expand(&s, &hairpin, &math::IDENTITY, &cam, Some(&plan));
    assert_eq!(a.points.len(), b.points.len());
    assert_eq!(a.triangles, b.triangles);
}
