//! Properties of the rendering core (proptest). The example tests next to
//! each module pin single pictures; these pin what has to hold for any
//! mesh Hydra can hand over, including broken ones (indices past the
//! points, NaN points from a bad deformer).

use crate::ffi::{th_render, ThFrame, ThMeshView};
use crate::math::{self, Mat4, Vec3};
use crate::composite::{self, Shading};
use crate::blur::{self, Blur, Lens};
use crate::raster::{self, Frame, Mesh};
use crate::TH_OK;
use crate::triangulate;
use proptest::prelude::*;

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

fn frame(width: usize, height: usize, samples: usize) -> Frame {
    let (world_to_clip, view_to_world) = camera();
    Frame { width, height, world_to_clip, view_to_world, samples, shading: Shading::Lit, ao: true, blur: Blur::OFF,
            alpha: false }
}

/// A sample count the rasteriser has a pattern for.
fn sample_count() -> impl Strategy<Value = usize> {
    prop::sample::select(vec![1usize, 2, 4, 8, 16])
}

fn point() -> impl Strategy<Value = Vec3> {
    [-4.0f32..4.0, -4.0f32..4.0, -4.0f32..4.0]
}

/// Points, and triangles that may index past them (a topology/points
/// mismatch mid-edit).
fn mesh_data() -> impl Strategy<Value = (Vec<Vec3>, Vec<[u32; 3]>)> {
    prop::collection::vec(point(), 1..24).prop_flat_map(|points| {
        let n = points.len() as u32 + 2;
        (Just(points), prop::collection::vec([0..n, 0..n, 0..n], 0..32))
    })
}

/// Instance transforms: a translation and a uniform scale, zero included.
fn transforms() -> impl Strategy<Value = Vec<Mat4>> {
    prop::collection::vec(([-3.0f64..3.0, -3.0f64..3.0, -3.0f64..3.0], 0.0f64..2.0), 1..4).prop_map(|ts| {
        ts.into_iter()
            .map(|([x, y, z], s)| {
                let mut m = math::IDENTITY;
                m[0] = s;
                m[5] = s;
                m[10] = s;
                m[12] = x;
                m[13] = y;
                m[14] = z;
                m
            })
            .collect()
    })
}

fn plain<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], transforms: &'a [Mat4]) -> Mesh<'a> {
    Mesh {
        points, triangles, corner_normals: None, corner_colors: None, corner_uvs: None,
        base_color: [0.6, 0.5, 0.4], tiles: Vec::new(), transforms, key: None,
        points_close: None, transform_keys: &[],
    }
}

/// x, y and z over w, and w: a clip-space corner in front of the near
/// plane once multiplied out.
fn clip_corner() -> impl Strategy<Value = ([f64; 3], f64)> {
    ([-1.5f64..1.5, -1.5f64..1.5, -1.0f64..1.0], 0.2f64..4.0)
}

fn length(a: Vec3) -> f32 {
    math::dot(a, a).sqrt()
}

proptest! {
    #[test]
    fn cusp_normals_stay_within_the_cusp_of_their_face((points, triangles) in mesh_data()) {
        let normals = raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES);
        prop_assert_eq!(normals.len(), triangles.len() * 3);
        let cos_cusp = raster::CUSP_DEGREES.to_radians().cos();
        for (t, tri) in triangles.iter().enumerate() {
            let corners = &normals[t * 3..t * 3 + 3];
            for n in corners {
                prop_assert!((length(*n) - 1.0).abs() < 1e-4);
            }
            let Some([a, b, c]) = tri.iter().map(|&i| points.get(i as usize).copied())
                .collect::<Option<Vec<_>>>().map(|v| [v[0], v[1], v[2]]) else { continue };
            let (e1, e2) = (math::sub(b, a), math::sub(c, a));
            let cross = math::cross(e1, e2);
            // Skip slivers: their f32 normal has no stable direction.
            if length(e1) < 0.1 || length(e2) < 0.1 || length(cross) < 0.05 * length(e1) * length(e2) {
                continue;
            }
            // A sum of normals each within the cusp of this face's is too:
            // no corner turns further from its face than the cusp angle.
            let face = math::normalize(cross);
            for n in corners {
                prop_assert!(math::dot(*n, face) >= cos_cusp - 1e-3, "{:?} vs face {:?}", n, face);
            }
        }
    }

    #[test]
    fn a_gentle_surface_is_smoothed_exactly_as_a_subdivision_surface(
        cols in 2usize..7,
        rows in 2usize..7,
        heights in prop::collection::vec(-0.2f32..0.2, 49),
    ) {
        // A height field whose slopes stay under 22 degrees, so no two
        // faces around a point are a cusp apart.
        let points: Vec<Vec3> = (0..rows * cols)
            .map(|i| [(i % cols) as f32, (i / cols) as f32, heights[i]])
            .collect();
        let mut triangles = Vec::new();
        for y in 0..rows - 1 {
            for x in 0..cols - 1 {
                let i = (y * cols + x) as u32;
                let (r, d) = (i + 1, i + cols as u32);
                triangles.push([i, r, d + 1]);
                triangles.push([i, d + 1, d]);
            }
        }
        let cusp = raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES);
        let smooth = raster::smooth_normals(&points, &triangles);
        for (t, tri) in triangles.iter().enumerate() {
            for k in 0..3 {
                let (c, s) = (cusp[t * 3 + k], smooth[tri[k] as usize]);
                prop_assert!((0..3).all(|j| (c[j] - s[j]).abs() < 1e-5), "{:?} != {:?}", c, s);
            }
        }
    }

    #[test]
    fn a_box_keeps_its_hard_edges(
        size in [0.1f32..5.0, 0.1f32..5.0, 0.1f32..5.0],
        at in point(),
    ) {
        // Eight shared points, two outward-wound triangles per side.
        let points: Vec<Vec3> = (0..8)
            .map(|i| [0, 1, 2].map(|j| at[j] + if i >> j & 1 == 1 { size[j] } else { 0.0 }))
            .collect();
        let sides: [([u32; 4], Vec3); 6] = [
            ([0, 4, 6, 2], [-1.0, 0.0, 0.0]), ([1, 3, 7, 5], [1.0, 0.0, 0.0]),
            ([0, 1, 5, 4], [0.0, -1.0, 0.0]), ([2, 6, 7, 3], [0.0, 1.0, 0.0]),
            ([0, 2, 3, 1], [0.0, 0.0, -1.0]), ([4, 5, 7, 6], [0.0, 0.0, 1.0]),
        ];
        let triangles: Vec<[u32; 3]> = sides.iter()
            .flat_map(|([a, b, c, d], _)| [[*a, *b, *c], [*a, *c, *d]])
            .collect();
        let normals = raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES);
        for (t, n) in normals.chunks(3).enumerate() {
            let want = sides[t / 2].1;
            for c in n {
                prop_assert!((0..3).all(|j| (c[j] - want[j]).abs() < 1e-5), "side {}: {:?} != {:?}", t / 2, c, want);
            }
        }
    }

    #[test]
    fn smooth_normals_of_a_plane_are_the_planes(
        z in -4.0f32..4.0,
        (points, triangles) in mesh_data(),
    ) {
        // Flatten onto z and wind every usable triangle to face +z.
        let points: Vec<Vec3> = points.iter().map(|p| [p[0], p[1], z]).collect();
        let triangles: Vec<[u32; 3]> = triangles.iter().filter_map(|&[a, b, c]| {
            let p = |i: u32| points.get(i as usize).copied();
            let (pa, pb, pc) = (p(a)?, p(b)?, p(c)?);
            let nz = math::cross(math::sub(pb, pa), math::sub(pc, pa))[2];
            if nz.abs() < 1e-3 { None } else if nz > 0.0 { Some([a, b, c]) } else { Some([a, c, b]) }
        }).collect();
        let normals = raster::smooth_normals(&points, &triangles);
        prop_assert_eq!(normals.len(), points.len());
        for n in normals {
            prop_assert!((n[0].abs() < 1e-4) && (n[1].abs() < 1e-4) && ((n[2] - 1.0).abs() < 1e-4), "{:?}", n);
        }
    }

    #[test]
    fn binning_keeps_every_sample_a_triangle_covers(
        corners in [clip_corner(), clip_corner(), clip_corner()],
        width in 1usize..64,
        height in 1usize..64,
        band_rows in 1usize..16,
        samples in sample_count(),
    ) {
        // Clip-space corners in front of the near plane (z >= -w).
        let clip = corners.map(|([x, y, z], w)| [(x * w) as f32, (y * w) as f32, (z * w) as f32, w as f32]);
        let offsets = composite::sample_offsets(samples);
        let bands = raster::triangle_bands(clip, width, height, band_rows, &raster::SampleGrid::new(&offsets));
        let screen = clip.map(|c| {
            let iw = 1.0 / c[3] as f64;
            ((c[0] as f64 * iw * 0.5 + 0.5) * width as f64, (0.5 - c[1] as f64 * iw * 0.5) * height as f64)
        });
        let edge = |(ax, ay): (f64, f64), (bx, by): (f64, f64), (px, py): (f64, f64)| {
            (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        };
        let area = edge(screen[0], screen[1], screen[2]);
        for py in 0..height {
            for px in 0..width {
                for &(ox, oy) in &offsets {
                    let p = (px as f64 + 0.5 + ox as f64, py as f64 + 0.5 + oy as f64);
                    let e = [edge(screen[1], screen[2], p), edge(screen[2], screen[0], p), edge(screen[0], screen[1], p)];
                    // Strictly inside, by a margin: the edges themselves are
                    // the rasteriser's call, not the binner's.
                    let inside = area.abs() > 1e-9 && e.iter().all(|&v| v * area.signum() > 1e-6 * area.abs());
                    if inside {
                        let (b0, b1) = bands.ok_or_else(|| TestCaseError::fail(
                            format!("a sample of pixel ({px}, {py}) covered but no bands")))?;
                        prop_assert!(b0 <= py / band_rows && py / band_rows <= b1,
                            "pixel row {} (band {}) outside bands {}..={}", py, py / band_rows, b0, b1);
                    }
                }
            }
        }
    }

    #[test]
    fn rendering_any_mesh_gives_a_whole_frame(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
        width in 1usize..40,
        height in 1usize..40,
        samples in sample_count(),
        nan_at in prop::option::of(0usize..24),
    ) {
        let mut points = points;
        if let Some(i) = nan_at.filter(|&i| i < points.len()) {
            points[i][1] = f32::NAN;
        }
        let image = raster::render(&frame(width, height, samples), &[plain(&points, &triangles, &transforms)]);
        prop_assert_eq!(image.color.len(), width * height);
        prop_assert_eq!(image.depth.len(), width * height);
        prop_assert!(image.depth.iter().all(|d| (0.0..=1.0).contains(d)), "depth outside [0, 1]");
        prop_assert!(image.color.iter().all(|c| c.iter().all(|v| v.is_finite())), "non-finite colour");
    }

    #[test]
    fn depth_does_not_depend_on_mesh_order(
        (pa, ta) in mesh_data(),
        (pb, tb) in mesh_data(),
        transforms in transforms(),
    ) {
        let f = frame(32, 24, 1);
        let (a, b) = (plain(&pa, &ta, &transforms), plain(&pb, &tb, &transforms));
        let ab = raster::render(&f, &[a, b]);
        let (a, b) = (plain(&pa, &ta, &transforms), plain(&pb, &tb, &transforms));
        let ba = raster::render(&f, &[b, a]);
        prop_assert!(ab.depth == ba.depth);
    }

    #[test]
    fn a_polygonal_mesh_draws_as_its_cusp_normals(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
    ) {
        let flat = raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES);
        let (world_to_clip, view_to_world) = camera();
        let frame = ThFrame { width: 24, height: 16, world_to_clip, view_to_world, samples: 1, shading: 0, ao: 1,
                             blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
                             focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0 };
        let view = |polygonal: u32, normals: *const f32| ThMeshView {
            points: points.as_ptr() as *const f32,
            point_count: points.len() as u32,
            triangles: triangles.as_ptr() as *const u32,
            triangle_count: triangles.len() as u32,
            corner_normals: normals,
            corner_colors: std::ptr::null(),
            corner_uvs: std::ptr::null(),
            base_color: [0.6, 0.5, 0.4],
            tiles: std::ptr::null(),
            tile_count: 0,
            transforms: transforms.as_ptr() as *const f64,
            transform_count: transforms.len() as u32,
            polygonal,
            key: 0,
            version: 0,
            points_close: std::ptr::null(),
            transform_keys: std::ptr::null(),
            transform_key_count: 0,
        };
        let draw = |v: ThMeshView| {
            let mut color = vec![0.0f32; 24 * 16 * 4];
            let status = unsafe { th_render(&frame, &v, 1, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null()) };
            (status, color)
        };
        let flat_ptr = if flat.is_empty() { std::ptr::null() } else { flat.as_ptr() as *const f32 };
        let polygonal = draw(view(1, std::ptr::null()));
        let explicit = draw(view(0, flat_ptr));
        prop_assert_eq!(polygonal.0, TH_OK);
        prop_assert!(polygonal.1 == explicit.1, "polygonal differs from its cusp normals");
        // Authored normals always win over the flag.
        let smooth: Vec<Vec3> = vec![[0.0, 1.0, 0.0]; triangles.len() * 3];
        let smooth_ptr = if smooth.is_empty() { std::ptr::null() } else { smooth.as_ptr() as *const f32 };
        prop_assert!(draw(view(1, smooth_ptr)).1 == draw(view(0, smooth_ptr)).1);
    }
}

/// A quad of half-size `size` facing the camera, turned `yaw` about y and
/// `pitch` about x.
fn turned_quad(size: f32, yaw: f32, pitch: f32) -> Vec<Vec3> {
    let (sy, cy) = yaw.sin_cos();
    let (sp, cp) = pitch.sin_cos();
    [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]
        .map(|[x, y]: [f32; 2]| {
            let (x, y) = (x * size, y * size);
            // Pitch (about x), then yaw (about y).
            let (y, z) = (y * cp, y * sp);
            [x * cy + z * sy, y, -x * sy + z * cy]
        })
        .to_vec()
}

const QUAD: [[u32; 3]; 2] = [[0, 1, 2], [0, 2, 3]];

fn covered(image: &raster::Image) -> impl Iterator<Item = &[f32; 4]> {
    image.color.iter().zip(&image.depth).filter(|(_, d)| **d < 1.0).map(|(c, _)| c)
}

/// Covered pixels whose eight neighbours are covered too: away from an
/// edge, where anti-aliasing mixes in the backdrop.
fn interior(image: &raster::Image, w: usize, h: usize) -> Vec<[f32; 4]> {
    let hit = |x: usize, y: usize| image.depth[y * w + x] < 1.0;
    (1..h.saturating_sub(1))
        .flat_map(|y| (1..w.saturating_sub(1)).map(move |x| (x, y)))
        .filter(|&(x, y)| (y - 1..=y + 1).all(|yy| (x - 1..=x + 1).all(|xx| hit(xx, yy))))
        .map(|(x, y)| image.color[y * w + x])
        .collect()
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 64, ..ProptestConfig::default() })]

    #[test]
    fn shading_and_occlusion_never_move_depth(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
        samples in sample_count(),
    ) {
        let mut images = Vec::new();
        for shading in [Shading::Lit, Shading::Clay, Shading::Albedo, Shading::Occlusion] {
            for ao in [false, true] {
                let f = Frame { shading, ao, ..frame(20, 14, samples) };
                let image = raster::render(&f, &[plain(&points, &triangles, &transforms)]);
                prop_assert!(image.color.iter().all(|c| c.iter().all(|v| v.is_finite() && *v >= 0.0)),
                             "{:?} ao {}: colour negative or not finite", shading, ao);
                images.push(image);
            }
        }
        prop_assert!(images.iter().all(|i| i.depth == images[0].depth));
    }

    #[test]
    fn a_lone_plane_is_never_occluded(
        size in 0.3f32..3.0,
        yaw in -1.0f32..1.0,
        pitch in -1.0f32..1.0,
        samples in sample_count(),
    ) {
        let points = turned_quad(size, yaw, pitch);
        let one = [math::IDENTITY];
        let f = Frame { shading: Shading::Occlusion, ..frame(48, 32, samples) };
        let image = raster::render(&f, &[plain(&points, &QUAD, &one)]);
        for c in interior(&image, 48, 32) {
            prop_assert!(c[0] > 0.999, "a plane occluded itself: {:?}", c);
        }
    }

    #[test]
    fn albedo_shading_is_the_surface_colour(
        color in [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0],
        yaw in -1.0f32..1.0,
    ) {
        let points = turned_quad(1.5, yaw, 0.3);
        let one = [math::IDENTITY];
        let mut mesh = plain(&points, &QUAD, &one);
        mesh.base_color = color;
        let f = Frame { shading: Shading::Albedo, ..frame(40, 30, 1) };
        let image = raster::render(&f, &[mesh]);
        prop_assert!(covered(&image).count() > 0);
        for c in covered(&image) {
            prop_assert!((0..3).all(|i| (c[i] - color[i]).abs() < 1e-4), "{:?} is not {:?}", c, color);
        }
    }

    #[test]
    fn without_ao_the_occlusion_pass_is_white(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
    ) {
        let f = Frame { shading: Shading::Occlusion, ao: false, ..frame(20, 14, 1) };
        let image = raster::render(&f, &[plain(&points, &triangles, &transforms)]);
        prop_assert!(covered(&image).all(|c| c[..3] == [1.0, 1.0, 1.0]));
    }
}

/// A wall standing on a floor: the corner between them is darker than the
/// open wall above it. (Much further off, the floor seen edge-on is less
/// than a pixel deep at the corner, and screen-space occlusion cannot see it.)
#[test]
fn a_corner_is_darker_than_the_open_wall() {
    for back in [0.0f32, -2.0, -4.0] {
        let wall = vec![[-3.0, -1.0, back], [3.0, -1.0, back], [3.0, 9.0, back], [-3.0, 9.0, back]];
        let floor = vec![[-3.0, -1.0, 4.0], [3.0, -1.0, 4.0], [3.0, -1.0, back], [-3.0, -1.0, back]];
        let one = [math::IDENTITY];
        let f = Frame { shading: Shading::Occlusion, ..frame(96, 96, 1) };
        let image = raster::render(&f, &[plain(&wall, &QUAD, &one), plain(&floor, &[[0, 2, 1], [0, 3, 2]], &one)]);
        // The corner's row: y = -1 at distance 5 - back, 90 degree field of view.
        let corner = ((0.5 + 0.5 / (5.0 - back)) * 96.0) as usize;
        let at = |row: usize| image.color[row * 96 + 48][0];
        let (near, open) = (at(corner - 2), at(corner - 30));
        assert!(near < open - 0.05, "distance {}: corner {near}, open wall {open}", 5.0 - back);
        assert!(open > 0.95, "open wall occluded: {open}");
    }
}

/// `frame`'s camera, moved by `offset`.
fn moved_frame(width: usize, height: usize, offset: [f64; 3]) -> Frame {
    let mut f = frame(width, height, 1);
    let (_, view_to_world) = camera();
    let mut moved_to_world = view_to_world;
    let mut world_to_view = math::IDENTITY;
    for k in 0..3 {
        moved_to_world[12 + k] += offset[k];
        world_to_view[12 + k] = -moved_to_world[12 + k];
    }
    let (world_to_clip, _) = camera();
    // camera() is world_to_view(z = -5) * proj; rebuild with the moved view.
    let mut back = math::IDENTITY;
    back[14] = 5.0;
    let proj = math::mul(&back, &world_to_clip);
    f.world_to_clip = math::mul(&world_to_view, &proj);
    f.view_to_world = moved_to_world;
    f
}

/// `transforms`, each moved by `offset`.
fn moved(transforms: &[Mat4], offset: [f64; 3]) -> Vec<Mat4> {
    transforms
        .iter()
        .map(|m| {
            let mut m = *m;
            for k in 0..3 {
                m[12 + k] += offset[k];
            }
            m
        })
        .collect()
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 64, ..ProptestConfig::default() })]

    /// Ambient occlusion only ever takes light away: with it, no pixel is
    /// brighter than without it, whatever the mesh.
    #[test]
    fn occlusion_only_ever_darkens(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
        samples in sample_count(),
        shading in prop::sample::select(vec![Shading::Lit, Shading::Clay, Shading::Albedo]),
    ) {
        let draw = |ao| {
            let f = Frame { shading, ao, ..frame(24, 18, samples) };
            raster::render(&f, &[plain(&points, &triangles, &transforms)])
        };
        let (with, without) = (draw(true), draw(false));
        for (a, b) in with.color.iter().zip(&without.color) {
            prop_assert!((0..3).all(|c| a[c] <= b[c] + 1e-5), "occlusion brightened {:?} to {:?}", b, a);
        }
        let f = Frame { shading: Shading::Occlusion, ..frame(24, 18, samples) };
        let occlusion = raster::render(&f, &[plain(&points, &triangles, &transforms)]);
        prop_assert!(occlusion.color.iter().all(|c| (0..3).all(|i| (0.0..=1.0).contains(&c[i]))));
    }

    /// The post pass works in the camera's space: moving the camera and the
    /// scene together leaves the picture as it was (bar a few edge pixels a
    /// rounding difference can tip either way).
    #[test]
    fn the_picture_follows_the_camera_not_the_world(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
        offset in [-40.0f64..40.0, -40.0f64..40.0, -40.0f64..40.0],
        shading in prop::sample::select(vec![Shading::Lit, Shading::Clay, Shading::Occlusion]),
    ) {
        let (w, h) = (32, 24);
        let here = raster::render(&Frame { shading, ..frame(w, h, 1) }, &[plain(&points, &triangles, &transforms)]);
        let shifted = moved(&transforms, offset);
        let there = raster::render(&Frame { shading, ..moved_frame(w, h, offset) },
                                   &[plain(&points, &triangles, &shifted)]);
        let differ = here.color.iter().zip(&there.color)
            .filter(|(a, b)| (0..3).any(|c| (a[c] - b[c]).abs() > 0.02))
            .count();
        prop_assert!(differ <= (w * h) / 50, "{} of {} pixels changed when everything moved by {:?}", differ, w * h, offset);
    }

    /// Clay is one grey: the surface's own colour never shows through.
    #[test]
    fn clay_ignores_the_surface_colour(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
        a in [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0],
        b in [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0],
    ) {
        let draw = |color: Vec3| {
            let mut mesh = plain(&points, &triangles, &transforms);
            mesh.base_color = color;
            raster::render(&Frame { shading: Shading::Clay, ..frame(20, 14, 1) }, &[mesh])
        };
        prop_assert!(draw(a).color == draw(b).color);
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    /// Occlusion is the surface's own, sample by sample: where the edge of a
    /// darkened surface cuts a pixel, the samples of the open surface behind
    /// it stay open, so the pixel is lighter than the surface beside it by
    /// about the share of it the far surface covers. (Worked out per pixel,
    /// the whole pixel took the near surface's occlusion: a dark,
    /// stair-stepped halo along every silhouette.)
    #[test]
    fn occlusion_does_not_spill_across_a_silhouette(
        back in -3.0f32..-0.5,
        // From eight. With four, the two samples an upright edge leaves on
        // the wall can sit where a crease seen at a grazing angle darkens
        // them most: the floor's samples a row below, a fraction of a pixel
        // apart on screen, are far apart in depth. Their pixel then gained
        // as little as a tenth of its open share (600 cases; eight: 0.46,
        // sixteen: 0.67).
        samples in prop::sample::select(vec![8usize, 16]),
        col in 52usize..70,
    ) {
        let size = 96;
        let distance = 5.0 - back;
        // A wall standing on a floor, both ending 0.4 of the way across
        // pixel column `col`, in front of an open plane 12 further back.
        let edge = (((col as f32 + 0.4) / size as f32) * 2.0 - 1.0) * distance;
        let wall = vec![[-3.0, -1.0, back], [edge, -1.0, back], [edge, 9.0, back], [-3.0, 9.0, back]];
        let floor = vec![[-3.0, -1.0, 4.0], [edge, -1.0, 4.0], [edge, -1.0, back], [-3.0, -1.0, back]];
        let far = back - 12.0;
        let open = vec![[-40.0, -40.0, far], [40.0, -40.0, far], [40.0, 40.0, far], [-40.0, 40.0, far]];
        let one = [math::IDENTITY];
        let f = Frame { shading: Shading::Occlusion, ..frame(size, size, samples) };
        let image = raster::render(&f, &[
            plain(&wall, &QUAD, &one), plain(&floor, &[[0, 2, 1], [0, 3, 2]], &one), plain(&open, &QUAD, &one),
        ]);
        let at = |x: usize, y: usize| image.color[y * size + x][0];
        // The wall's rows just above the corner, where the floor darkens it.
        let corner = ((0.5 + 0.5 / distance) * size as f32) as usize;
        for row in corner - 3..corner {
            let (inside, cut, beyond) = (at(col - 2, row), at(col, row), at(col + 2, row));
            prop_assert!(beyond > 0.999, "row {}: the open plane is occluded: {}", row, beyond);
            if inside < 0.97 {
                // 3 of the cut pixel's 8 samples are the wall's, 7 of 16;
                // allow for the wall's samples there being darker.
                prop_assert!(cut - inside >= 0.35 * (1.0 - inside),
                             "row {}: wall {}, the pixel its edge cuts {}, open plane {}", row, inside, cut, beyond);
            }
        }
    }
}

/// Along a straight crease the occlusion is even: the blur spans the
/// rotation pattern exactly, so no grain or dashes are left on the wall
/// above a floor. (Interleaved gradient noise left three to five times this.)
#[test]
fn a_straight_crease_is_evenly_dark() {
    let wall = vec![[-4.0, -1.0, -2.0], [4.0, -1.0, -2.0], [4.0, 6.0, -2.0], [-4.0, 6.0, -2.0]];
    let floor = vec![[-4.0, -1.0, 4.0], [4.0, -1.0, 4.0], [4.0, -1.0, -2.0], [-4.0, -1.0, -2.0]];
    let one = [math::IDENTITY];
    for (size, samples) in [(160usize, 1usize), (160, 4), (320, 4), (120, 8), (160, 8), (160, 16)] {
        let f = Frame { shading: Shading::Occlusion, ..frame(size, size, samples) };
        let image = raster::render(&f, &[plain(&wall, &QUAD, &one), plain(&floor, &[[0, 2, 1], [0, 3, 2]], &one)]);
        // The corner's row: y = -1 at distance 7, 90 degree field of view.
        let corner = ((0.5 + 0.5 / 7.0) * size as f64) as usize;
        for row in corner - 4..corner {
            let v: Vec<f32> = (size * 3 / 8..size * 5 / 8).map(|x| image.color[row * size + x][0]).collect();
            let mean = v.iter().sum::<f32>() / v.len() as f32;
            let spread = (v.iter().map(|a| (a - mean) * (a - mean)).sum::<f32>() / v.len() as f32).sqrt();
            assert!(mean < 0.97, "{size} px at {samples} samples: row {row} not darkened ({mean})");
            assert!(spread < 0.008, "{size} px at {samples} samples: row {row} varies by {spread} around {mean}");
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    /// The samples are N-rooks: an edge close to vertical (or, turned,
    /// horizontal) passes each of a pixel's samples at a different place,
    /// so as it slides across a pixel the pixel's coverage takes every step
    /// from 0 to N of N. (A 2x2 grid's four samples sit in two columns, so a
    /// near-vertical edge gave three coverages: the stair-stepped shelf legs
    /// and lamp stripes.)
    #[test]
    fn a_near_vertical_edge_takes_every_coverage(
        samples in prop::sample::select(vec![2usize, 4, 8, 16]),
        start in 0.0f32..1.0,
        lean in prop::sample::select(vec![-1.0f32, 1.0]),
        turned: bool,
    ) {
        // White over the backdrop, unlit: a pixel's colour is the backdrop
        // of its row plus its coverage times (1 - backdrop). The edge slides
        // a pixel and a half over 96 pixels (a pixel per 64), finer than the
        // samples' spacing.
        let (along, across) = (96usize, 24usize);
        let (w, h) = if turned { (along, across) } else { (across, along) };
        let edge = |t: f32| 8.0 + start + lean * t / 64.0;
        // Screen (u along the edge, v across it) to a point on z = 0, which
        // the camera sees at distance 5 (90 degrees: x and y over 5).
        let at = |u: f32, v: f32| {
            let (sx, sy) = if turned { (u, v) } else { (v, u) };
            [(2.0 * sx / w as f32 - 1.0) * 5.0, (1.0 - 2.0 * sy / h as f32) * 5.0, 0.0]
        };
        let points = vec![at(-8.0, -8.0), at(-8.0, edge(-8.0)), at(along as f32 + 8.0, edge(along as f32 + 8.0)),
                          at(along as f32 + 8.0, -8.0)];
        let one = [math::IDENTITY];
        let mut mesh = plain(&points, &QUAD, &one);
        mesh.base_color = [1.0, 1.0, 1.0];
        let f = Frame { shading: Shading::Albedo, ao: false, ..frame(w, h, samples) };
        let image = raster::render(&f, &[mesh]);
        // The backdrop's gradient, as the post pass draws it.
        let backdrop = |y: usize| raster::srgb_to_linear(
            composite::BACKDROP_TOP + (composite::BACKDROP_BOTTOM - composite::BACKDROP_TOP) * y as f32 / (h - 1) as f32);
        let mut levels = std::collections::BTreeSet::new();
        for u in 4..along - 4 {
            for v in 0..across {
                let (x, y) = if turned { (u, v) } else { (v, u) };
                let (c, g) = (image.color[y * w + x][0], backdrop(y));
                let coverage = (c - g) / (1.0 - g);
                if coverage > 0.02 && coverage < 0.98 {
                    let k = (coverage * samples as f32).round();
                    prop_assert!((coverage * samples as f32 - k).abs() < 0.05,
                                 "coverage {} is not a whole number of {} samples", coverage, samples);
                    levels.insert(k as usize);
                }
            }
        }
        prop_assert_eq!(levels.len(), samples - 1, "partial coverages {:?} of {} samples", levels, samples);
    }
}

// ---------------------------------------------------------------------------
// Motion blur and depth of field (crate::blur)
// ---------------------------------------------------------------------------

/// `frame` with blur: `samples` layers, motion blur on or off, a lens or a
/// pinhole.
fn blurred(f: Frame, samples: usize, motion: bool, lens: Option<Lens>) -> Frame {
    Frame { blur: Blur { samples, motion, lens, ..Blur::OFF }, ..f }
}

/// White, unlit, no occlusion: a pixel's colour tells its coverage
/// ([`coverage`]).
fn flat_frame(w: usize, h: usize, samples: usize) -> Frame {
    Frame { shading: Shading::Albedo, ao: false, ..frame(w, h, samples) }
}

/// How much of pixel (x, y) a white surface covers, from its colour over
/// the backdrop's.
fn coverage(image: &raster::Image, w: usize, h: usize, x: usize, y: usize) -> f32 {
    let g = raster::srgb_to_linear(
        composite::BACKDROP_TOP + (composite::BACKDROP_BOTTOM - composite::BACKDROP_TOP) * y as f32 / (h - 1) as f32);
    (image.color[y * w + x][0] - g) / (1.0 - g)
}

fn white<'a>(points: &'a [Vec3], transforms: &'a [Mat4]) -> Mesh<'a> {
    Mesh { base_color: [1.0, 1.0, 1.0], ..plain(points, &QUAD, transforms) }
}

fn translation(x: f64, y: f64, z: f64) -> Mat4 {
    let mut m = math::IDENTITY;
    m[12] = x;
    m[13] = y;
    m[14] = z;
    m
}

/// A few quads at their own depths (no two surfaces tie for a sample).
fn quads() -> impl Strategy<Value = Vec<(f32, f32, f32, [f64; 3])>> {
    prop::collection::vec((0.3f32..1.5, -0.8f32..0.8, -0.8f32..0.8,
                           [-1.5f64..1.5, -1.5f64..1.5, -3.0f64..2.0]), 1..4)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 24, ..ProptestConfig::default() })]

    /// Off is off: with motion blur and depth of field switched off, motion
    /// data on the meshes and a lens on the frame change nothing, to the
    /// byte.
    #[test]
    fn blur_switched_off_is_the_unblurred_picture(
        quads in quads(), samples in sample_count(), shift in [-1.0f32..1.0, -1.0f32..1.0, -1.0f32..1.0],
        blur_samples in prop::sample::select(vec![0usize, 1, 16, 64]),
    ) {
        let geometry: Vec<(Vec<Vec3>, [Mat4; 1])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
            .collect();
        let closing: Vec<Vec<Vec3>> = geometry.iter().map(|(p, _)| p.iter().map(|&q| math::add(q, shift)).collect()).collect();
        let keys: Vec<[Mat4; 2]> = geometry.iter().map(|(_, t)| [t[0], translation(1.0, 0.0, 0.0)]).collect();
        let still: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        let moving: Vec<Mesh> = geometry.iter().zip(&closing).zip(&keys)
            .map(|(((p, t), c), k)| Mesh { points_close: Some(c), transform_keys: k, ..plain(p, &QUAD, t) })
            .collect();
        let f = frame(32, 24, samples);
        let reference = raster::render(&f, &still);
        // Neither blur asked for: the meshes' and the camera's motion are
        // ignored.
        let mut off = blurred(f, blur_samples, false, None);
        off.blur.cameras[0] = f.view_to_world;
        off.blur.cameras[1] = math::mul(&f.view_to_world, &translation(0.5, 0.2, 0.0));
        off.blur.camera_keys = 2;
        let image = raster::render(&off, &moving);
        prop_assert!(image.color == reference.color && image.depth == reference.depth);
        prop_assert!(!blur::active(&off, &moving));
    }

    /// A still scene drawn in layers -- motion blur on but nothing moving,
    /// a pinhole lens, any number of chunks -- is the unblurred picture to
    /// the byte: a zero shutter (closing points and keys equal to the
    /// opening's) and an f-stop of 0 both equal off.
    #[test]
    fn a_still_scene_drawn_in_layers_is_the_unblurred_picture(
        quads in quads(), samples in prop::sample::select(vec![1usize, 4, 8]),
        chunks in prop::sample::select(vec![1usize, 2, 4]), focal in 0.01f32..0.2, focus in 0.5f32..50.0,
    ) {
        let geometry: Vec<(Vec<Vec3>, [Mat4; 2])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| {
                let t = translation(at[0], at[1], at[2]);
                (turned_quad(s, yaw, pitch), [t, t])
            })
            .collect();
        let still: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, &t[..1])).collect();
        let zero_shutter: Vec<Mesh> = geometry.iter()
            .map(|(p, t)| Mesh { points_close: Some(p), transform_keys: t, ..plain(p, &QUAD, &t[..1]) })
            .collect();
        let f = frame(32, 24, samples);
        let reference = raster::render(&f, &still);
        prop_assert!(Lens::of_camera(focal, 0.0, focus).is_none(), "an f-stop of 0 is a pinhole");
        let mut layered = blurred(f, samples * chunks, true, Lens::of_camera(focal, 0.0, focus));
        // The camera holding still over the shutter, as keys.
        layered.blur.cameras[0] = f.view_to_world;
        layered.blur.cameras[1] = f.view_to_world;
        layered.blur.camera_keys = 2;
        let image = raster::render_blurred(&layered, &zero_shutter);
        prop_assert!(image.color == reference.color, "layered picture differs");
        prop_assert!(image.depth == reference.depth, "layered depth differs");
    }

    /// A small upright bar moving across the shutter smears along its path,
    /// whether its points, its transform or the camera move: covered from
    /// where it opens to where it closes, each pixel of the path only partly
    /// (the share of the shutter the square spends over it), and no taller
    /// than the square. The three ways of moving draw the same smear.
    #[test]
    fn a_square_moving_across_the_shutter_smears_along_its_path(
        from in -3.0f64..-1.0, length in 1.5f64..4.0, y in -1.5f64..1.5,
        samples in prop::sample::select(vec![1usize, 4, 8]),
    ) {
        let (w, h) = (96usize, 48usize);
        let (half, tall) = (0.15f32, 0.4f32);
        let square = vec![[-half, -tall, 0.0], [half, -tall, 0.0], [half, tall, 0.0], [-half, tall, 0.0]];
        let open = [translation(from, y, 0.0)];
        let keys = [open[0], translation(from + length, y, 0.0)];
        let shifted: Vec<Vec3> = square.iter().map(|p| [p[0] + length as f32, p[1], p[2]]).collect();
        let f = blurred(flat_frame(w, h, samples), 32, true, None);
        // Moving by its transform, by its points, or held still while the
        // camera moves the other way.
        let by_transform = raster::render(&f, &[Mesh { transform_keys: &keys, ..white(&square, &open) }]);
        let by_points = raster::render(&f, &[Mesh { points_close: Some(&shifted), ..white(&square, &open) }]);
        let mut by_camera = f;
        by_camera.blur.cameras[0] = f.view_to_world;
        by_camera.blur.cameras[1] = math::mul(&f.view_to_world, &translation(-length, 0.0, 0.0));
        by_camera.blur.camera_keys = 2;
        let by_camera = raster::render(&by_camera, &[white(&square, &open)]);
        let still = raster::render(&Frame { blur: Blur::OFF, ..f }, &[white(&square, &open)]);

        // Screen x of world x on z = 0 (distance 5, 90 degrees), and row of y.
        let px = |x: f64| ((x / 5.0 + 1.0) * 0.5 * w as f64) as f32;
        let row = ((0.5 - y / 10.0) * h as f64) as usize;
        let (start, end) = (px(from - half as f64), px(from + length + half as f64));
        let still_cols = (0..w).filter(|&x| coverage(&still, w, h, x, row) > 0.02).count() as f32;
        for (name, image) in [("transform", &by_transform), ("points", &by_points), ("camera", &by_camera)] {
            let cols: Vec<usize> = (0..w).filter(|&x| coverage(image, w, h, x, row) > 0.01).collect();
            let (lo, hi) = (*cols.first().unwrap_or(&0) as f32, *cols.last().unwrap_or(&0) as f32);
            // The first and last layers sit half a slot inside the shutter.
            prop_assert!((lo - start).abs() <= 3.0 && (hi - end).abs() <= 3.0,
                         "{}: smear covers {}..{}, path {}..{}", name, lo, hi, start, end);
            prop_assert!(cols.len() as f32 > still_cols * 2.0, "{}: no longer than the square", name);
            // Depth is the nearest of the layers: any pixel the smear
            // touches has it.
            for &x in &cols {
                prop_assert!(image.depth[row * w + x] < 1.0, "{}: column {} covered but no depth", name, x);
            }
            // The middle of the path: partly covered, about the square's
            // width over the path's length.
            let mid = coverage(image, w, h, ((start + end) * 0.5) as usize, row);
            let share = (2.0 * half as f64 / length) as f32;
            prop_assert!(mid > 0.02 && mid < 0.9 && (mid - share).abs() < 0.2,
                         "{}: middle of the path covered {} (share {})", name, mid, share);
            // Not taller than the square.
            let rows = (0..h).filter(|&yy| coverage(image, w, h, ((start + end) * 0.5) as usize, yy) > 0.01).count();
            let square_rows = (tall as f64 * 2.0 / 10.0 * h as f64).ceil() as usize + 2;
            prop_assert!(rows <= square_rows, "{}: smear {} rows tall, square {}", name, rows, square_rows);
        }
        let same = |a: &raster::Image, b: &raster::Image| a.color.iter().zip(&b.color)
            .filter(|(p, q)| (p[0] - q[0]).abs() > 0.02).count();
        prop_assert!(same(&by_transform, &by_points) <= 4, "points and transform smear differently");
        prop_assert!(same(&by_transform, &by_camera) <= 4, "camera and transform smear differently");
    }

    /// Depth of field: an edge at the focus distance stays as sharp as
    /// without a lens; off it, the edge spreads over the circle of
    /// confusion, wider the further the edge is from the focus plane (in
    /// 1 / distance), on either side of it. The picture is the same every
    /// time it is drawn.
    #[test]
    fn blur_grows_with_distance_from_the_focus_plane(
        focus in 3.0f32..8.0, near in 0.3f32..0.9, far in 1.2f32..3.0, f_stop in 1.4f32..4.0,
        samples in prop::sample::select(vec![1usize, 8]),
    ) {
        let (w, h) = (96usize, 16usize);
        let focal = 0.6f32;
        let lens = Lens::of_camera(focal, f_stop, focus);
        prop_assert!(lens.is_some());
        let f = blurred(flat_frame(w, h, samples), 64, false, lens);
        // A white half plane, its edge at screen x = 0 seen from `distance`.
        let width_at = |distance: f32| -> (usize, raster::Image) {
            let z = 5.0 - distance;
            let big = 20.0 * distance;
            let points = vec![[-big, -big, z], [0.0, -big, z], [0.0, big, z], [-big, big, z]];
            let one = [math::IDENTITY];
            let image = raster::render(&f, &[white(&points, &one)]);
            let row = h / 2;
            let partial = (0..w).filter(|&x| {
                let c = coverage(&image, w, h, x, row);
                c > 0.02 && c < 0.98
            }).count();
            (partial, image)
        };
        let (at_focus, sharp) = width_at(focus);
        let (nearer, _) = width_at(focus * near);
        let (further, _) = width_at(focus * far);
        let unblurred = raster::render(&Frame { blur: Blur::OFF, ..f }, &[white(
            &[[-100.0, -100.0, 5.0 - focus], [0.0, -100.0, 5.0 - focus], [0.0, 100.0, 5.0 - focus], [-100.0, 100.0, 5.0 - focus]],
            &[math::IDENTITY])]);
        let row = h / 2;
        for x in 0..w {
            prop_assert!((coverage(&sharp, w, h, x, row) - coverage(&unblurred, w, h, x, row)).abs() < 0.02,
                         "the focus plane is not sharp at x {}", x);
        }
        prop_assert!(at_focus <= 1, "{} partial pixels at the focus distance", at_focus);
        // The circle of confusion on screen: radius * |1/focus - 1/d| *
        // h... for this 90 degree camera, w/2 pixels per unit at distance 1.
        let coc = |d: f32| lens.unwrap().radius * (1.0 / focus - 1.0 / d).abs() * w as f32;
        for (d, measured) in [(focus * near, nearer), (focus * far, further)] {
            let expected = coc(d);
            prop_assert!((measured as f32 - expected).abs() <= 2.0 + 0.25 * expected,
                         "at {} the edge spreads {} px, circle of confusion {} px", d, measured, expected);
        }
        // Wider the further from the focus plane, on either side.
        let (d1, d2) = (focus * (1.0 + (far - 1.0) * 0.5), focus * far);
        prop_assert!(width_at(d1).0 <= width_at(d2).0 + 1);
        let (n1, n2) = (focus * (1.0 - (1.0 - near) * 0.5), focus * near);
        prop_assert!(width_at(n1).0 <= width_at(n2).0 + 1);
        // Drawn again, the same picture: the lens pattern is fixed.
        let again = width_at(focus * far).1;
        prop_assert!(again.color == width_at(focus * far).1.color);
    }
}

#[test]
fn a_pinhole_has_no_lens() {
    assert_eq!(Lens::of_camera(0.05, 0.0, 10.0), None, "f-stop 0: depth of field off");
    assert_eq!(Lens::of_camera(0.05, f32::INFINITY, 10.0), None, "infinite f-stop: a pinhole");
    assert_eq!(Lens::of_camera(0.05, 2.0, 0.0), None);
    assert_eq!(Lens::of_camera(0.05, 2.0, f32::NAN), None);
    assert_eq!(Lens::of_camera(0.05, 2.0, 10.0), Some(Lens { radius: 0.0125, focus: 10.0 }));
}


/// A frame is the same picture every time it is drawn, even where surfaces
/// tie for a sample: many coplanar quads of different colours, one instance
/// each (each binned by whichever thread is free), keep the same winner.
#[test]
fn coplanar_ties_resolve_the_same_every_time() {
    let points = turned_quad(1.5, 0.0, 0.0);
    let transforms: Vec<Mat4> = (0..64).map(|i| translation(0.002 * (i % 8) as f64, 0.002 * (i / 8) as f64, 0.0)).collect();
    let meshes: Vec<Mesh> = transforms
        .chunks(1)
        .enumerate()
        .map(|(i, t)| Mesh { base_color: [(i % 4) as f32 / 3.0, (i / 4 % 4) as f32 / 3.0, (i / 16) as f32 / 3.0], ..plain(&points, &QUAD, t) })
        .collect();
    let f = Frame { shading: Shading::Albedo, ao: false, ..frame(64, 48, 8) };
    let first = raster::render(&f, &meshes);
    for _ in 0..12 {
        assert!(raster::render(&f, &meshes).color == first.color, "a tie resolved differently");
    }
}

/// A turn about y by `angle` about the point `at`.
fn turn_about(angle: f64, at: [f64; 3]) -> Mat4 {
    let (s, c) = angle.sin_cos();
    let rot: Mat4 = [c, 0.0, -s, 0.0, 0.0, 1.0, 0.0, 0.0, s, 0.0, c, 0.0, 0.0, 0.0, 0.0, 1.0];
    math::mul(&math::mul(&translation(-at[0], -at[1], -at[2]), &rot), &translation(at[0], at[1], at[2]))
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 16, ..ProptestConfig::default() })]

    /// The camera's own motion: a camera turning and sliding over the
    /// shutter draws what a still camera sees of the world moving the other
    /// way, lit and occluded the same (the lights and the render buffer's
    /// normals follow each layer's camera).
    #[test]
    fn a_moving_camera_sees_the_world_move_the_other_way(
        quads in quads(), angle in -0.12f64..0.12, slide in [-0.4f64..0.4, -0.2f64..0.2, -0.4f64..0.4],
        shading in prop::sample::select(vec![Shading::Lit, Shading::Clay]), ao: bool,
    ) {
        let geometry: Vec<(Vec<Vec3>, [Mat4; 1])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
            .collect();
        let f = blurred(Frame { shading, ao, ..frame(48, 32, 4) }, 16, true, None);
        let eye = [f.view_to_world[12], f.view_to_world[13], f.view_to_world[14]];
        let close = math::mul(&math::mul(&f.view_to_world, &turn_about(angle, eye)),
                              &translation(slide[0], slide[1], slide[2]));
        // The camera moving from the frame's to `close`.
        let mut moving_camera = f;
        moving_camera.blur.cameras[0] = f.view_to_world;
        moving_camera.blur.cameras[1] = close;
        moving_camera.blur.camera_keys = 2;
        let still: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        let by_camera = raster::render(&moving_camera, &still);
        // The world moving by close^-1 * frame camera instead.
        let world = math::mul(&blur::affine_inverse(&close), &f.view_to_world);
        let keys: Vec<[Mat4; 2]> = geometry.iter().map(|(_, t)| [t[0], math::mul(&t[0], &world)]).collect();
        let moving: Vec<Mesh> = geometry.iter().zip(&keys)
            .map(|((p, t), k)| Mesh { transform_keys: k, ..plain(p, &QUAD, t) })
            .collect();
        let by_world = raster::render(&f, &moving);
        let off = by_camera.color.iter().zip(&by_world.color)
            .filter(|(a, b)| (0..3).any(|c| (a[c] - b[c]).abs() > 0.03))
            .count();
        prop_assert!(off <= by_camera.color.len() / 50, "{} of {} pixels differ", off, by_camera.color.len());
    }
}

/// The ABI's blur fields reach the core: no blur samples or no motion flag
/// draw the still picture whatever motion the meshes carry, an f-stop of 0
/// is no lens; with motion on the meshes' closing points and keys blur it,
/// and a lens defocuses it.
#[test]
fn the_abi_carries_motion_and_the_lens() {
    let (world_to_clip, view_to_world) = camera();
    let points = turned_quad(0.8, 0.3, 0.2);
    let close: Vec<Vec3> = points.iter().map(|p| math::add(*p, [1.0, 0.0, 0.0])).collect();
    let one = [translation(0.0, 0.0, -2.0)];
    let keys = [one[0], translation(0.0, 0.6, -2.0)];
    let draw = |blur_samples: u32, motion: u32, f_stop: f32, with_close: bool, with_keys: bool| {
        let frame = ThFrame {
            width: 32, height: 24, world_to_clip, view_to_world, samples: 4, shading: 0, ao: 1,
            blur_samples, motion, camera_keys: 0, cameras: [0.0; 64],
            focal_length: 0.5, f_stop, focus_distance: 20.0, alpha: 0,
        };
        let view = ThMeshView {
            points: points.as_ptr() as *const f32, point_count: 4,
            triangles: QUAD.as_ptr() as *const u32, triangle_count: 2,
            corner_normals: std::ptr::null(), corner_colors: std::ptr::null(), corner_uvs: std::ptr::null(),
            base_color: [0.6, 0.5, 0.4], tiles: std::ptr::null(), tile_count: 0,
            transforms: one.as_ptr() as *const f64, transform_count: 1, polygonal: 0, key: 0, version: 0,
            points_close: if with_close { close.as_ptr() as *const f32 } else { std::ptr::null() },
            transform_keys: if with_keys { keys.as_ptr() as *const f64 } else { std::ptr::null() },
            transform_key_count: if with_keys { 2 } else { 0 },
        };
        let mut color = vec![0.0f32; 32 * 24 * 4];
        assert_eq!(unsafe { th_render(&frame, &view, 1, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null()) }, TH_OK);
        color
    };
    let still = draw(0, 0, 0.0, false, false);
    assert!(draw(0, 1, 2.0, true, true) == still, "no blur samples: no blur");
    assert!(draw(16, 0, 0.0, true, true) == still, "motion off, f-stop 0: no blur");
    assert!(draw(16, 1, 0.0, false, false) == still, "nothing moves: no blur");
    assert!(draw(16, 1, 0.0, true, false) != still, "closing points blur");
    assert!(draw(16, 1, 0.0, false, true) != still, "transform keys blur");
    assert!(draw(16, 0, 1.0, false, false) != still, "a lens defocuses");
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 16, ..ProptestConfig::default() })]

    /// Each layer is drawn, lit and occluded from its own camera: a camera
    /// held still over the shutter somewhere other than the frame's draws,
    /// to the byte, the unblurred picture through that camera.
    #[test]
    fn a_held_camera_draws_as_the_frame_camera_there(
        quads in quads(), angle in -0.8f64..0.8, slide in [-0.4f64..0.4, -0.2f64..0.2, -0.4f64..0.4],
        shading in prop::sample::select(vec![Shading::Lit, Shading::Clay]), ao: bool,
    ) {
        let geometry: Vec<(Vec<Vec3>, [Mat4; 1])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
            .collect();
        let meshes: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        let f = blurred(Frame { shading, ao, ..frame(48, 32, 4) }, 16, true, None);
        let eye = [f.view_to_world[12], f.view_to_world[13], f.view_to_world[14]];
        let held = math::mul(&math::mul(&f.view_to_world, &turn_about(angle, eye)),
                             &translation(slide[0], slide[1], slide[2]));
        let mut by_keys = f;
        by_keys.blur.cameras[0] = held;
        by_keys.blur.cameras[1] = held;
        by_keys.blur.camera_keys = 2;
        let proj = math::mul(&f.view_to_world, &f.world_to_clip);
        let there = Frame {
            view_to_world: held, world_to_clip: math::mul(&blur::affine_inverse(&held), &proj), blur: Blur::OFF, ..f
        };
        let a = raster::render(&by_keys, &meshes);
        let b = raster::render(&there, &meshes);
        prop_assert!(a.color == b.color && a.depth == b.depth);
    }
}

// ---------------------------------------------------------------------------
// Still instances transformed once a frame (raster::Shared)
// ---------------------------------------------------------------------------

/// A blurred scene: quads at their own places, each still or moving by its
/// points, its transform keys or both, with one or two instances, smooth or
/// with authored corner normals.
fn blur_scene() -> impl Strategy<Value = Vec<(f32, f32, f32, [f64; 3], u8, bool, bool)>> {
    prop::collection::vec((0.3f32..1.5, -0.8f32..0.8, -0.8f32..0.8, [-1.5f64..1.5, -1.5f64..1.5, -3.0f64..2.0],
                           0u8..4, any::<bool>(), any::<bool>()), 1..6)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    /// Sharing a still instance's vertices across a frame's layers draws
    /// the picture every layer transforming everything itself draws, to
    /// the byte: with motion blur or without, through a lens or a moving
    /// camera (clip made per layer) or neither (clip shared), in one batch
    /// or many (kept across chunks or per batch).
    #[test]
    fn shared_still_vertices_draw_the_unshared_picture(
        scene in blur_scene(), samples in prop::sample::select(vec![1usize, 2, 4, 8]),
        blur_samples in prop::sample::select(vec![8usize, 16, 32]), motion: bool,
        lens in prop::option::of((0.02f32..0.2, 0.5f32..8.0, 1.0f32..10.0)),
        camera_moves: bool, budget in prop::sample::select(vec![4usize, 9, 1 << 20]),
        shading in prop::sample::select(vec![Shading::Lit, Shading::Albedo]), ao: bool,
    ) {
        // Per quad: points, closing points, transforms (1 or 2) and keys
        // (key-major, 2 keys), corner normals.
        let built: Vec<(Vec<Vec3>, Vec<Vec3>, Vec<Mat4>, Vec<Mat4>, Vec<Vec3>)> = scene.iter()
            .map(|&(s, yaw, pitch, at, moves, twice, authored)| {
                let points = turned_quad(s, yaw, pitch);
                let close: Vec<Vec3> = points.iter().map(|&p| math::add(p, [0.3, -0.1, 0.05])).collect();
                let mut transforms = vec![translation(at[0], at[1], at[2])];
                if twice {
                    transforms.push(translation(at[0] + 0.4, at[1] - 0.3, at[2] - 0.5));
                }
                let keys: Vec<Mat4> = if moves & 2 != 0 {
                    transforms.iter().copied()
                        .chain(transforms.iter().map(|t| math::mul(t, &translation(-0.2, 0.25, 0.1))))
                        .collect()
                } else {
                    Vec::new()
                };
                let normals = if authored {
                    QUAD.iter().flatten().map(|&v| math::normalize(math::add(points[v as usize], [0.0, 0.0, 2.0]))).collect()
                } else {
                    Vec::new()
                };
                (points, if moves & 1 != 0 { close } else { Vec::new() }, transforms, keys, normals)
            })
            .collect();
        let meshes: Vec<Mesh> = built.iter()
            .map(|(p, c, t, k, n)| Mesh {
                points_close: (!c.is_empty()).then_some(&c[..]),
                transform_keys: k,
                corner_normals: (!n.is_empty()).then_some(&n[..]),
                ..plain(p, &QUAD, t)
            })
            .collect();
        let mut f = blurred(Frame { shading, ao, ..frame(40, 28, samples) }, blur_samples, motion,
                            lens.and_then(|(focal, stop, focus)| Lens::of_camera(focal, stop, focus)));
        if camera_moves {
            f.blur.cameras[0] = f.view_to_world;
            f.blur.cameras[1] = math::mul(&f.view_to_world, &translation(0.3, 0.1, 0.2));
            f.blur.camera_keys = 2;
        }
        let reference = raster::render_blurred_with(&f, &meshes, false, raster::VERTEX_BUDGET);
        let unshared = raster::render_blurred_with(&f, &meshes, false, budget);
        let shared = raster::render_blurred_with(&f, &meshes, true, budget);
        prop_assert!(unshared.color == reference.color && unshared.depth == reference.depth, "batching changed the picture");
        prop_assert!(shared.color == reference.color, "sharing changed the colour");
        prop_assert!(shared.depth == reference.depth, "sharing changed the depth");
    }
}


/// Where a still and a moving surface tie for a sample, the one earlier in
/// instance order wins, as without sharing: the still instances' bands,
/// binned once, merge with each pass's moving ones in instance order.
#[test]
fn ties_between_still_and_moving_instances_keep_instance_order() {
    let points = turned_quad(1.5, 0.0, 0.0);
    let one = [translation(0.0, 0.0, 0.0)];
    // A moving mesh whose closing points are its opening ones: it moves by
    // the rules (and is drawn per pass), sitting exactly on the still one.
    let still = Mesh { base_color: [1.0, 0.0, 0.0], ..plain(&points, &QUAD, &one) };
    let moving = Mesh { base_color: [0.0, 0.0, 1.0], points_close: Some(&points), ..plain(&points, &QUAD, &one) };
    let f = blurred(Frame { shading: Shading::Albedo, ao: false, ..frame(32, 24, 4) }, 16, true, None);
    for order in [[&still, &moving], [&moving, &still]] {
        let meshes: Vec<Mesh> = order.iter().map(|m| Mesh { tiles: Vec::new(), ..**m }).collect();
        let unshared = raster::render_blurred_with(&f, &meshes, false, raster::VERTEX_BUDGET);
        let shared = raster::render_blurred_with(&f, &meshes, true, raster::VERTEX_BUDGET);
        assert!(shared.color == unshared.color, "a tie resolved differently with sharing");
        // The first in instance order is the one seen.
        let first = meshes[0].base_color;
        let middle = unshared.color[12 * 32 + 16];
        assert!((middle[0] - first[0]).abs() < 1e-6 && (middle[2] - first[2]).abs() < 1e-6, "{middle:?}");
    }
}

// ---------------------------------------------------------------------------
// Triangulation (triangulate.rs): concave faces, keyhole faces, the fan kept
// ---------------------------------------------------------------------------

/// A star-shaped polygon about the origin: `corners` corners at increasing
/// angles (the first at angle 0), every angular gap under half a turn, and
/// radii in `lo..hi`, so it is simple, holds the origin and is as concave
/// as the radii make it; counter-clockwise. With 8 corners or more no gap
/// is over 0.27 of a turn, so no edge passes nearer the origin than
/// `0.67 * lo`.
fn star(corners: std::ops::Range<usize>, lo: f64, hi: f64) -> impl Strategy<Value = Vec<[f64; 2]>> {
    corners.prop_flat_map(move |n| {
        (prop::collection::vec(1.0f64..2.5, n), prop::collection::vec(lo..hi, n)).prop_map(move |(gaps, radii)| {
            let total: f64 = gaps.iter().sum();
            let mut angle = 0.0f64;
            let mut out = Vec::with_capacity(n);
            for i in 0..n {
                out.push([radii[i] * angle.cos(), radii[i] * angle.sin()]);
                angle += gaps[i] / total * std::f64::consts::TAU;
            }
            out
        })
    })
}

fn signed_area(p: &[[f64; 2]]) -> f64 {
    (0..p.len())
        .map(|i| {
            let (a, b) = (p[i], p[(i + 1) % p.len()]);
            a[0] * b[1] - a[1] * b[0]
        })
        .sum::<f64>()
        / 2.0
}

fn inside(p: [f64; 2], poly: &[[f64; 2]]) -> bool {
    let mut odd = false;
    for i in 0..poly.len() {
        let (a, b) = (poly[i], poly[(i + 1) % poly.len()]);
        if (a[1] > p[1]) != (b[1] > p[1]) && p[0] < a[0] + (p[1] - a[1]) / (b[1] - a[1]) * (b[0] - a[0]) {
            odd = !odd;
        }
    }
    odd
}

type Plane = ([f64; 3], [f64; 3], [f64; 3]);

/// A plane in space to lay a 2D face in: an orthonormal pair and an origin.
fn plane() -> impl Strategy<Value = Plane> {
    let v = || [-1.0f64..1.0, -1.0f64..1.0, -1.0f64..1.0];
    (v(), v(), [-5.0f64..5.0, -5.0f64..5.0, -5.0f64..5.0]).prop_filter_map("a plane needs two directions", |(a, b, o)| {
        let norm = |v: [f64; 3]| {
            let l = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
            (l > 0.1).then(|| v.map(|c| c / l))
        };
        let u = norm(a)?;
        let d = b[0] * u[0] + b[1] * u[1] + b[2] * u[2];
        let v = norm([b[0] - d * u[0], b[1] - d * u[1], b[2] - d * u[2]])?;
        Some((u, v, o))
    })
}

fn lay(p: &[[f64; 2]], (u, v, o): Plane) -> Vec<Option<Vec3>> {
    p.iter().map(|q| Some([0, 1, 2].map(|k| (o[k] + q[0] * u[k] + q[1] * v[k]) as f32))).collect()
}

/// What a face's triangles must do, checked in the face's own 2D
/// coordinates (counter-clockwise): `n - 2` of them, none turned over,
/// their areas summing to the face's, and every sample point of the face in
/// one of them and no point outside it (a keyhole's opening included) in
/// any.
fn covers(tris: &[[usize; 3]], q: &[[f64; 2]], area: f64, is_in: impl Fn([f64; 2]) -> bool) -> Result<(), TestCaseError> {
    prop_assert_eq!(tris.len(), q.len() - 2);
    let scale = q.iter().map(|p| p[0].abs().max(p[1].abs())).fold(0.0, f64::max);
    let eps = scale * scale * 1e-4;
    let twice = |t: &[usize; 3]| {
        (q[t[1]][0] - q[t[0]][0]) * (q[t[2]][1] - q[t[0]][1]) - (q[t[1]][1] - q[t[0]][1]) * (q[t[2]][0] - q[t[0]][0])
    };
    let mut sum = 0.0;
    for t in tris {
        prop_assert!(twice(t) >= -eps, "a triangle is turned over: {:?} {}", t, twice(t));
        sum += twice(t) / 2.0;
    }
    prop_assert!((sum - area).abs() <= eps * q.len() as f64 + area.abs() * 1e-4, "areas {} vs {}", sum, area);
    let in_tri = |p: [f64; 2], t: &[usize; 3]| {
        let c = |a: [f64; 2], b: [f64; 2]| (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
        let (a, b, d) = (c(q[t[0]], q[t[1]]), c(q[t[1]], q[t[2]]), c(q[t[2]], q[t[0]]));
        (a > 0.0 && b > 0.0 && d > 0.0) || (a < 0.0 && b < 0.0 && d < 0.0)
    };
    for i in 0..24 {
        for j in 0..24 {
            let p = [(i as f64 + 0.37) / 12.0 * scale - scale, (j as f64 + 0.61) / 12.0 * scale - scale];
            let hits = tris.iter().filter(|t| in_tri(p, t)).count();
            if is_in(p) {
                prop_assert!(hits >= 1, "{:?} inside the face is in no triangle", p);
            } else {
                prop_assert_eq!(hits, 0, "{:?} outside the face is in a triangle", p);
            }
        }
    }
    Ok(())
}

fn reversed(p: &[[f64; 2]]) -> Vec<[f64; 2]> {
    p.iter().rev().copied().collect()
}

proptest! {
    /// A concave (star-shaped) face, laid in any plane and wound either
    /// way, is cut into triangles that tile it exactly.
    #[test]
    fn a_concave_face_is_tiled(poly in star(4..40, 0.3, 2.0), at in plane(), backwards in any::<bool>()) {
        let q = if backwards { reversed(&poly) } else { poly };
        let tris = triangulate::polygon(&lay(&q, at));
        // Check in the counter-clockwise order: reversing the corners maps
        // corner i to n-1-i and turns each triangle's winding over.
        let n = q.len();
        let (q, tris): (Vec<[f64; 2]>, Vec<[usize; 3]>) = if backwards {
            (reversed(&q), tris.iter().map(|t| [n - 1 - t[0], n - 1 - t[2], n - 1 - t[1]]).collect())
        } else {
            (q, tris)
        };
        let area = signed_area(&q);
        covers(&tris, &q, area, |p| inside(p, &q))?;
    }

    /// A wall with a window: an outer loop bridged at angle 0 to an inner
    /// one walked the other way, each bridge end visited twice. The opening
    /// stays open.
    #[test]
    fn a_keyhole_face_keeps_its_opening(outer in star(8..40, 1.5, 2.0), inner in star(4..40, 0.3, 0.9), at in plane()) {
        let mut q = outer.clone();
        q.push(outer[0]);
        q.push(inner[0]);
        q.extend(inner[1..].iter().rev());
        q.push(inner[0]);
        let tris = triangulate::polygon(&lay(&q, at));
        let area = signed_area(&outer) - signed_area(&inner);
        covers(&tris, &q, area, |p| inside(p, &outer) && !inside(p, &inner))?;
    }

    /// A convex face (and a triangle) is fanned from corner 0, exactly as
    /// Hydra fans it, so a convex mesh draws as it did.
    #[test]
    fn a_convex_face_keeps_the_fan(n in 3usize..40, r in 0.1f64..3.0, at in plane()) {
        let q: Vec<[f64; 2]> = (0..n)
            .map(|i| {
                let a = i as f64 / n as f64 * std::f64::consts::TAU;
                [r * a.cos(), r * a.sin()]
            })
            .collect();
        let fan: Vec<[usize; 3]> = (1..n - 1).map(|i| [0, i, i + 1]).collect();
        prop_assert_eq!(triangulate::polygon(&lay(&q, at)), fan);
    }

    /// Any topology, broken ones included (indices past the points, NaN
    /// points, holes out of range): no panic, `n - 2` triangles per face
    /// that is not a hole, each from its own face's corners, faces in order;
    /// counts that run past the indices refuse.
    #[test]
    fn any_topology_triangulates_face_by_face(
        points in prop::collection::vec(prop_oneof![point(), Just([f32::NAN, 0.0, 0.0])], 0..12),
        counts in prop::collection::vec(0i32..9, 0..8),
        holes in prop::collection::vec(-1i32..9, 0..3),
        flip in any::<bool>(),
        seed in any::<u64>(),
    ) {
        let total: usize = counts.iter().map(|&c| c as usize).sum();
        let mut x = seed;
        let indices: Vec<i32> = (0..total)
            .map(|_| {
                x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                ((x >> 33) % 16) as i32 - 1
            })
            .collect();
        let (corners, faces) = triangulate::triangulate(&points, &counts, &indices, &holes, flip).unwrap();
        let mut expected = Vec::new();
        let mut start = 0usize;
        for (f, &n) in counts.iter().enumerate() {
            if n >= 3 && !holes.contains(&(f as i32)) {
                expected.extend(std::iter::repeat(f as u32).take(n as usize - 2));
            }
            let range = start as u32..(start + n as usize) as u32;
            for (t, c) in corners.iter().enumerate() {
                if faces[t] == f as u32 {
                    prop_assert!(c.iter().all(|i| range.contains(i)), "{:?} not in face {}", c, f);
                }
            }
            start += n as usize;
        }
        prop_assert_eq!(faces, expected);
        if total > 0 {
            prop_assert!(triangulate::triangulate(&points, &counts, &indices[..total - 1], &holes, flip).is_none());
        }
    }
}

// ---------------------------------------------------------------------------
// Transparent background (raster::Frame::alpha, TH_PLAYBLAST_ALPHA)
// ---------------------------------------------------------------------------

/// The backdrop of row `y` of `h`, linear, as the post pass draws it.
fn backdrop_at(y: usize, h: usize) -> f32 {
    raster::srgb_to_linear(
        composite::BACKDROP_TOP + (composite::BACKDROP_BOTTOM - composite::BACKDROP_TOP) * y as f32 / (h - 1) as f32)
}

/// Each quad's corners and its one transform.
fn quad_geometry(quads: &[(f32, f32, f32, [f64; 3])]) -> Vec<(Vec<Vec3>, [Mat4; 1])> {
    quads.iter()
        .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
        .collect()
}

fn shading_mode() -> impl Strategy<Value = Shading> {
    prop::sample::select(vec![Shading::Lit, Shading::Clay, Shading::Albedo, Shading::Occlusion])
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    /// Off is the opaque picture, on its premultiplied cut-out: with the
    /// option off every pixel has alpha 1; on, the same scene (any shading,
    /// occlusion, samples, motion blur or not) gives the off picture once
    /// put over the backdrop, `on + (1 - alpha) * backdrop`, with the same
    /// depth. So alpha is the share of the samples (over the shutter too)
    /// that hit a surface, and nothing of the surfaces' shading changed.
    #[test]
    fn the_transparent_picture_over_the_backdrop_is_the_opaque_one(
        quads in quads(), samples in sample_count(), shading in shading_mode(),
        ao: bool, motion: bool, shift in [-1.0f32..1.0, -1.0f32..1.0, -1.0f32..1.0],
    ) {
        let geometry = quad_geometry(&quads);
        let closing: Vec<Vec<Vec3>> = geometry.iter().map(|(p, _)| p.iter().map(|&q| math::add(q, shift)).collect()).collect();
        let meshes: Vec<Mesh> = geometry.iter().zip(&closing)
            .map(|((p, t), c)| Mesh { points_close: motion.then_some(&c[..]), ..plain(p, &QUAD, t) })
            .collect();
        let (w, h) = (32usize, 24usize);
        let mut f = Frame { shading, ao, ..frame(w, h, samples) };
        if motion {
            f = blurred(f, 16, true, None);
        }
        let off = raster::render(&f, &meshes);
        let on = raster::render(&Frame { alpha: true, ..f }, &meshes);
        prop_assert!(off.color.iter().all(|p| p[3] == 1.0), "alpha off is opaque");
        prop_assert!(off.depth == on.depth);
        for y in 0..h {
            let g = backdrop_at(y, h);
            for x in 0..w {
                let (o, t) = (off.color[y * w + x], on.color[y * w + x]);
                prop_assert!((0.0..=1.0).contains(&t[3]), "alpha {} at ({}, {})", t[3], x, y);
                for c in 0..3 {
                    let over = t[c] + (1.0 - t[3]) * g;
                    prop_assert!((over - o[c]).abs() < 1e-5, "({}, {}) channel {}: {:?} over {} is not {:?}",
                                 x, y, c, t, g, o);
                }
            }
        }
    }

    /// Transparent white, unlit: every pixel's colour is its alpha (colour
    /// premultiplied by coverage) and a pixel nothing reached is 0, 0, 0, 0,
    /// with motion blur and depth of field as without. (A fully covered
    /// pixel is opaque: alpha_falls_monotonically_across_an_edge.)
    #[test]
    fn transparent_white_is_its_own_alpha(
        quads in quads(), samples in sample_count(), motion: bool, dof: bool,
        shift in [-1.0f32..1.0, -1.0f32..1.0, -1.0f32..1.0],
    ) {
        let geometry = quad_geometry(&quads);
        let closing: Vec<Vec<Vec3>> = geometry.iter().map(|(p, _)| p.iter().map(|&q| math::add(q, shift)).collect()).collect();
        let meshes: Vec<Mesh> = geometry.iter().zip(&closing)
            .map(|((p, t), c)| Mesh { points_close: motion.then_some(&c[..]), ..white(p, t) })
            .collect();
        let (w, h) = (32usize, 24usize);
        let mut f = Frame { alpha: true, ..flat_frame(w, h, samples) };
        if motion || dof {
            f = blurred(f, 16, motion, if dof { Lens::of_camera(0.5, 0.05, 3.0) } else { None });
        }
        let image = raster::render(&f, &meshes);
        for (p, &d) in image.color.iter().zip(&image.depth) {
            prop_assert!((0.0..=1.0).contains(&p[3]));
            for c in 0..3 {
                prop_assert!((p[c] - p[3]).abs() < 1e-6, "colour {:?} is not premultiplied white", p);
            }
            if d >= 1.0 {
                prop_assert!(*p == [0.0; 4], "a pixel nothing reached is {:?}", p);
            }
        }
    }

    /// Alpha follows coverage: across a straight edge it falls from 1 to 0
    /// without ever rising, in whole steps of the samples, and the colour
    /// (white, unlit) is the alpha at each.
    #[test]
    fn alpha_falls_monotonically_across_an_edge(
        samples in sample_count(),
        start in 0.0f32..1.0,
        lean in prop::sample::select(vec![-1.0f32, 1.0]),
    ) {
        // A white half-plane x < 12 + start + lean * y / 64 on a 32 x 64
        // transparent frame.
        let (w, h) = (32usize, 64usize);
        let edge = |y: f32| 12.0 + start + lean * y / 64.0;
        let at = |sx: f32, sy: f32| [(2.0 * sx / w as f32 - 1.0) * 5.0, (1.0 - 2.0 * sy / h as f32) * 5.0, 0.0];
        let points = vec![at(-8.0, -8.0), at(edge(-8.0), -8.0), at(edge(h as f32 + 8.0), h as f32 + 8.0),
                          at(-8.0, h as f32 + 8.0)];
        let one = [math::IDENTITY];
        let f = Frame { alpha: true, ..flat_frame(w, h, samples) };
        let image = raster::render(&f, &[white(&points, &one)]);
        let n = composite::sample_offsets(samples).len() as f32;
        for y in 0..h {
            let row = &image.color[y * w..(y + 1) * w];
            prop_assert_eq!(row[0][3], 1.0, "row {} starts covered", y);
            prop_assert_eq!(row[w - 1], [0.0; 4], "row {} ends empty", y);
            for x in 0..w - 1 {
                prop_assert!(row[x + 1][3] <= row[x][3], "row {}: alpha rises from {} to {} at {}",
                             y, row[x][3], row[x + 1][3], x + 1);
            }
            for p in row {
                let k = p[3] * n;
                prop_assert!((k - k.round()).abs() < 1e-4, "alpha {} is not a whole number of {} samples", p[3], n);
                prop_assert!((p[0] - p[3]).abs() < 1e-6);
            }
        }
    }
}

/// Through the C ABI: `alpha` nonzero is the transparent background (an
/// empty scene is 0, 0, 0, 0), 0 the opaque backdrop.
#[test]
fn the_abi_alpha_switch_reaches_the_post_pass() {
    let (world_to_clip, view_to_world) = camera();
    let mut frame = ThFrame { width: 8, height: 6, world_to_clip, view_to_world, samples: 4, shading: 0, ao: 1,
                              blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
                              focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 1 };
    let mut color = vec![0.5f32; 8 * 6 * 4];
    let status = unsafe {
        th_render(&frame, std::ptr::null(), 0, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null())
    };
    assert_eq!(status, TH_OK);
    assert!(color.iter().all(|&v| v == 0.0), "transparent and empty");
    frame.alpha = 0;
    let status = unsafe {
        th_render(&frame, std::ptr::null(), 0, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null())
    };
    assert_eq!(status, TH_OK);
    assert!(color.chunks(4).all(|p| p[3] == 1.0 && p[0] > 0.1), "the backdrop, opaque");
}
