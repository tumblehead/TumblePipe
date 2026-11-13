//! Properties of the rendering core (proptest). The example tests next to
//! each module pin single pictures; these pin what has to hold for any
//! mesh Hydra can hand over, including broken ones (indices past the
//! points, NaN points from a bad deformer).

use crate::ffi::{linear_depth, th_render, ThFrame, ThLight, ThMeshView};
use crate::lights::{LightShape, Lighting, SceneLight, SceneLights};
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
            alpha: false, lights: crate::lights::SceneLights::NONE }
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
        points_close: None, transform_keys: &[], opacity: 1.0, cull: crate::raster::Cull::Nothing,
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
                             focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0 };
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
            transparency: 0.0, cull: 0,
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
        prop_assert!(image.ids == reference.ids, "ids differ");
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
        prop_assert!(image.ids == reference.ids, "layered ids differ");
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
            focal_length: 0.5, f_stop, focus_distance: 20.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0,
        };
        let view = ThMeshView {
            points: points.as_ptr() as *const f32, point_count: 4,
            triangles: QUAD.as_ptr() as *const u32, triangle_count: 2,
            corner_normals: std::ptr::null(), corner_colors: std::ptr::null(), corner_uvs: std::ptr::null(),
            base_color: [0.6, 0.5, 0.4], tiles: std::ptr::null(), tile_count: 0,
            transforms: one.as_ptr() as *const f64, transform_count: 1, polygonal: 0, key: 0, version: 0,
            points_close: if with_close { close.as_ptr() as *const f32 } else { std::ptr::null() },
            transform_keys: if with_keys { keys.as_ptr() as *const f64 } else { std::ptr::null() },
            transform_key_count: if with_keys { 2 } else { 0 }, transparency: 0.0, cull: 0,
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

    /// `flip` (a left-handed mesh) gives exactly the triangles it gives
    /// without, each wound the other way: the same faces, corners
    /// `[a, c, b]` for `[a, b, c]`, so the same coverage with every signed
    /// area negated (a face's triangles sum to its area unflipped, minus it
    /// flipped). The shim leans on this: it flips an unrefined left-handed
    /// mesh only, a refined one being right-handed already (8cdb3434).
    #[test]
    fn flip_reverses_every_triangle_and_nothing_else(
        faces in prop::collection::vec(prop_oneof![star(3..20, 0.3, 2.0), star(3..20, 0.999, 1.0)], 1..6),
        holes in prop::collection::vec(0i32..6, 0..3),
        at in plane(),
    ) {
        let mut points = Vec::new();
        let mut counts = Vec::new();
        for q in &faces {
            counts.push(q.len() as i32);
            points.extend(lay(q, at).into_iter().map(|p| p.unwrap()));
        }
        let indices: Vec<i32> = (0..points.len() as i32).collect();
        let (plain, plain_faces) = triangulate::triangulate(&points, &counts, &indices, &holes, false).unwrap();
        let (flipped, flipped_faces) = triangulate::triangulate(&points, &counts, &indices, &holes, true).unwrap();
        prop_assert_eq!(&plain_faces, &flipped_faces);
        let wound: Vec<[u32; 3]> = plain.iter().map(|&[a, b, c]| [a, c, b]).collect();
        prop_assert_eq!(&flipped, &wound);
        // In each face's own 2D coordinates: corner k of the mesh is
        // corner k - start of its face.
        let flat: Vec<[f64; 2]> = faces.iter().flatten().copied().collect();
        let twice = |t: &[u32; 3]| {
            let [a, b, c] = t.map(|i| flat[i as usize]);
            (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        };
        for (f, q) in faces.iter().enumerate() {
            if holes.contains(&(f as i32)) {
                prop_assert!(!plain_faces.contains(&(f as u32)));
                continue;
            }
            let area = signed_area(q);
            let sum = |tris: &[[u32; 3]]| -> f64 {
                tris.iter().zip(&plain_faces).filter(|(_, &g)| g == f as u32).map(|(t, _)| twice(t) / 2.0).sum()
            };
            let eps = 1e-6 * q.len() as f64 + area.abs() * 1e-6;
            prop_assert!((sum(&plain) - area).abs() <= eps, "face {}: {} unflipped vs area {}", f, sum(&plain), area);
            prop_assert!((sum(&flipped) + area).abs() <= eps, "face {}: {} flipped vs area {}", f, sum(&flipped), area);
        }
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
                              focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 1, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0 };
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

// Picking ids (th_render_ids): what the viewport selects by.

fn view_of(points: &[Vec3], triangles: &[[u32; 3]], transforms: &[Mat4]) -> ThMeshView {
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
        polygonal: 0,
        key: 0,
        version: 0,
        points_close: std::ptr::null(),
        transform_keys: std::ptr::null(),
        transform_key_count: 0,
        transparency: 0.0, cull: 0,
    }
}

fn abi_frame(width: u32, height: u32, samples: u32) -> ThFrame {
    let (world_to_clip, view_to_world) = camera();
    ThFrame { width, height, world_to_clip, view_to_world, samples, shading: 0, ao: 0,
              blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
              focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0 }
}

proptest! {
    // One sample a pixel: a pixel names a surface exactly where it is
    // covered, and the surface it names, drawn alone, is what covers it
    // (the same depth), as an instance that mesh has.
    #[test]
    fn ids_name_the_surface_a_pixel_sees(
        (pa, ta) in mesh_data(),
        (pb, tb) in mesh_data(),
        transforms in transforms(),
    ) {
        let f = Frame { ao: false, ..frame(24, 18, 1) };
        let meshes = [plain(&pa, &ta, &transforms), plain(&pb, &tb, &transforms)];
        let both = raster::render(&f, &meshes);
        prop_assert_eq!(both.ids.len(), 24 * 18);
        for (p, (&[m, i], &z)) in both.ids.iter().zip(&both.depth).enumerate() {
            prop_assert_eq!(m >= 0, z < 1.0, "pixel {} id {:?} depth {}", p, [m, i], z);
            if m < 0 {
                prop_assert_eq!(i, -1);
                continue;
            }
            prop_assert!((m as usize) < meshes.len() && (i as usize) < transforms.len());
            let alone = raster::render(&f, std::slice::from_ref(&meshes[m as usize]));
            prop_assert_eq!(alone.depth[p], z, "pixel {} names mesh {}", p, m);
        }
    }

    // Through the ABI: the ids are the core's for the views it draws, each
    // mesh index mapped back to the caller's, wherever among the views the
    // ones it skips (a missing points, triangles or transforms pointer) sit.
    #[test]
    fn abi_ids_index_the_callers_meshes(
        views in prop::collection::vec((mesh_data(), transforms(), 0usize..4), 1..6),
        samples in sample_count(),
    ) {
        let (w, h) = (20usize, 14usize);
        let mut abi = Vec::new();
        let mut kept = Vec::new();
        for (i, ((points, triangles), transforms, skip)) in views.iter().enumerate() {
            let mut view = view_of(points, triangles, transforms);
            match skip {
                1 => view.points = std::ptr::null(),
                2 => view.triangles = std::ptr::null(),
                3 => view.transforms = std::ptr::null(),
                _ => kept.push(i),
            }
            abi.push(view);
        }
        let abi_f = abi_frame(w as u32, h as u32, samples as u32);
        let mut color = vec![0.0f32; w * h * 4];
        let mut depth = vec![0.0f32; w * h];
        let mut ids = vec![7i32; w * h * 2];
        let status = unsafe {
            crate::ffi::th_render_ids(&abi_f, abi.as_ptr(), abi.len() as u32, color.as_mut_ptr(), depth.as_mut_ptr(),
                                      ids.as_mut_ptr(), std::ptr::null(), std::ptr::null())
        };
        prop_assert_eq!(status, TH_OK);
        let meshes: Vec<Mesh> = kept.iter()
            .map(|&i| { let ((p, t), m, _) = &views[i]; plain(p, t, m) })
            .collect();
        let core = raster::render(&Frame { ao: false, ..frame(w, h, samples) }, &meshes);
        for (p, (got, want)) in ids.chunks(2).zip(&core.ids).enumerate() {
            let want = if want[0] < 0 { [-1, -1] } else { [kept[want[0] as usize] as i32, want[1]] };
            prop_assert_eq!(got, &want[..], "pixel {}", p);
        }
    }

    // What a pixel names does not depend on the look: shading, occlusion
    // and a transparent background change the colours only.
    #[test]
    fn ids_ignore_the_look(
        (pa, ta) in mesh_data(), (pb, tb) in mesh_data(), transforms in transforms(),
        samples in sample_count(), shading in shading_mode(), ao in any::<bool>(), alpha in any::<bool>(),
    ) {
        let meshes = [plain(&pa, &ta, &transforms), plain(&pb, &tb, &transforms)];
        let reference = raster::render(&Frame { ao: false, ..frame(24, 18, samples) }, &meshes);
        let looked = raster::render(&Frame { shading, ao, alpha, ..frame(24, 18, samples) }, &meshes);
        prop_assert!(looked.ids == reference.ids);
        prop_assert!(looked.depth == reference.depth);
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 24, ..ProptestConfig::default() })]

    // Blurred, a pixel names what the first layer (the shutter's opening,
    // the lens's first point) sees there: an instance of a mesh that, drawn
    // alone through the same blur, covers the pixel; and a pixel that names
    // something is covered in the picture too. The meshes' order is the
    // caller's even though moving and still instances are drawn apart.
    #[test]
    fn blurred_ids_name_a_surface_that_covers_the_pixel(
        quads in quads(), moves in prop::collection::vec(any::<bool>(), 4),
        shift in [-1.0f32..1.0, -1.0f32..1.0, -0.5f32..0.5],
        samples in prop::sample::select(vec![1usize, 4]), chunks in prop::sample::select(vec![1usize, 2, 4]),
    ) {
        let (w, h) = (32usize, 24usize);
        let geometry: Vec<(Vec<Vec3>, [Mat4; 1])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
            .collect();
        let closing: Vec<Vec<Vec3>> = geometry.iter()
            .map(|(p, _)| p.iter().map(|&q| math::add(q, shift)).collect())
            .collect();
        let meshes: Vec<Mesh> = geometry.iter().zip(&closing).zip(&moves)
            .map(|(((p, t), c), &moving)| Mesh { points_close: moving.then_some(&c[..]), ..white(p, t) })
            .collect();
        let f = Frame { alpha: true, ..blurred(flat_frame(w, h, samples), samples * chunks, true, None) };
        let image = raster::render(&f, &meshes);
        prop_assert_eq!(image.ids.len(), w * h);
        let alone: Vec<raster::Image> = meshes.iter()
            .map(|m| raster::render(&f, std::slice::from_ref(m)))
            .collect();
        for (p, &[m, i]) in image.ids.iter().enumerate() {
            if m < 0 {
                prop_assert_eq!(i, -1);
                continue;
            }
            prop_assert!((m as usize) < meshes.len() && i == 0, "pixel {} names {:?}", p, [m, i]);
            prop_assert!(image.color[p][3] > 0.0, "pixel {} names mesh {} but is uncovered", p, m);
            prop_assert!(alone[m as usize].color[p][3] > 0.0, "pixel {} names mesh {}, which alone misses it", p, m);
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 16, ..ProptestConfig::default() })]

    // A cancel at any moment, from another thread as the viewport's render
    // thread is stopped, is all or nothing: either the call finishes and
    // writes exactly the uncancelled frame, or it reports TH_CANCELLED and
    // the caller's buffers are untouched. Afterwards the thread has no
    // cancel token left, so the next call draws in full. With the
    // delegate's cache (the viewer's case), a cancelled call leaves nothing
    // stale in it: the next call, drawn from what it kept, is the frame
    // drawn without a cache.
    #[test]
    fn a_cancel_at_any_moment_is_all_or_nothing(
        quads in quads(), samples in prop::sample::select(vec![4usize, 8]), motion in any::<bool>(),
        delay_us in 0u64..4000, cached in any::<bool>(),
    ) {
        let (w, h) = (96usize, 72usize);
        let geometry: Vec<(Vec<Vec3>, [Mat4; 1])> = quads.iter()
            .map(|&(s, yaw, pitch, at)| (turned_quad(s, yaw, pitch), [translation(at[0], at[1], at[2])]))
            .collect();
        let views: Vec<ThMeshView> = geometry.iter().enumerate()
            .map(|(i, (p, t))| ThMeshView { polygonal: 1, key: i as u64 + 1, version: 1, ..view_of(p, &QUAD, t) })
            .collect();
        let mut frame = abi_frame(w as u32, h as u32, samples as u32);
        frame.ao = 1;
        let cache = if cached { crate::ffi::th_cache_create() } else { std::ptr::null_mut() };
        if motion {
            // The camera moves over the shutter: every frame is blurred.
            frame.motion = 1;
            frame.blur_samples = 16;
            frame.camera_keys = 2;
            let (_, view_to_world) = camera();
            frame.cameras[..16].copy_from_slice(&view_to_world);
            frame.cameras[16..32].copy_from_slice(&math::mul(&view_to_world, &translation(0.3, 0.1, 0.0)));
        }
        let call_with = |cache: *const crate::ffi::ThCache, cancel: *const std::sync::atomic::AtomicU32,
                         color: &mut Vec<f32>, depth: &mut Vec<f32>, ids: &mut Vec<i32>| unsafe {
            crate::ffi::th_render_ids(&frame, views.as_ptr(), views.len() as u32, color.as_mut_ptr(),
                                      depth.as_mut_ptr(), ids.as_mut_ptr(), cancel, cache)
        };
        let call = |cancel: *const std::sync::atomic::AtomicU32, color: &mut Vec<f32>, depth: &mut Vec<f32>, ids: &mut Vec<i32>|
            call_with(cache, cancel, color, depth, ids);
        // The reference: no cache, never cancelled.
        let (mut rc, mut rd, mut ri) = (vec![0.0f32; w * h * 4], vec![0.0f32; w * h], vec![0i32; w * h * 2]);
        prop_assert_eq!(call_with(std::ptr::null(), std::ptr::null(), &mut rc, &mut rd, &mut ri), TH_OK);

        let cancel = std::sync::atomic::AtomicU32::new(0);
        let (mut color, mut depth, mut ids) = (vec![-5.0f32; w * h * 4], vec![-5.0f32; w * h], vec![-5i32; w * h * 2]);
        let status = std::thread::scope(|s| {
            s.spawn(|| {
                std::thread::sleep(std::time::Duration::from_micros(delay_us));
                cancel.store(1, std::sync::atomic::Ordering::Relaxed);
            });
            call(&cancel, &mut color, &mut depth, &mut ids)
        });
        prop_assert!(!crate::cancel::Token::current().is_set(), "the call left its cancel token behind");
        if status == TH_OK {
            prop_assert!(color == rc && depth == rd && ids == ri, "a finished call wrote a different frame");
        } else {
            prop_assert_eq!(status, crate::TH_CANCELLED);
            prop_assert!(color.iter().all(|&c| c == -5.0) && depth.iter().all(|&d| d == -5.0)
                         && ids.iter().all(|&i| i == -5), "a cancelled call wrote to the caller's buffers");
        }
        // The same thread, no cancel: the full frame.
        let (mut ac, mut ad, mut ai) = (vec![0.0f32; w * h * 4], vec![0.0f32; w * h], vec![0i32; w * h * 2]);
        prop_assert_eq!(call(std::ptr::null(), &mut ac, &mut ad, &mut ai), TH_OK);
        prop_assert!(ac == rc && ad == rd && ai == ri);
        unsafe { crate::ffi::th_cache_free(cache) };
    }
}

#[test]
fn a_cancelled_frame_writes_nothing() {
    let points: Vec<Vec3> = vec![[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, 1.0, 0.0]];
    let triangles = vec![[0u32, 1, 2]];
    let transforms = vec![math::IDENTITY];
    let view = view_of(&points, &triangles, &transforms);
    let frame = abi_frame(16, 12, 8);
    let mut color = vec![-5.0f32; 16 * 12 * 4];
    let mut ids = vec![-5i32; 16 * 12 * 2];
    let cancel = std::sync::atomic::AtomicU32::new(1);
    let status = unsafe {
        crate::ffi::th_render_ids(&frame, &view, 1, color.as_mut_ptr(), std::ptr::null_mut(),
                                  ids.as_mut_ptr(), &cancel, std::ptr::null())
    };
    assert_eq!(status, crate::TH_CANCELLED);
    assert!(color.iter().all(|&c| c == -5.0) && ids.iter().all(|&i| i == -5));
    // Not cancelled, the same call draws the triangle and names it.
    cancel.store(0, std::sync::atomic::Ordering::Relaxed);
    let status = unsafe {
        crate::ffi::th_render_ids(&frame, &view, 1, color.as_mut_ptr(), std::ptr::null_mut(),
                                  ids.as_mut_ptr(), &cancel, std::ptr::null())
    };
    assert_eq!(status, TH_OK);
    assert_eq!(&ids[(6 * 16 + 8) * 2..(6 * 16 + 8) * 2 + 2], &[0, 0]);
}

// Kept cusp normals (crate::normals): a delegate's cache never changes a
// picture.

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    // Over any run of frames in which polygonal meshes hold still, move
    // their points in place (saying so with a new version, as the shim
    // does) or are left out, every frame drawn with the delegate's cache is
    // the frame drawn without one, to the bit.
    #[test]
    fn frames_with_kept_normals_are_frames_worked_out_anew(
        mut scene in prop::collection::vec(mesh_data(), 1..4),
        steps in prop::collection::vec(prop::collection::vec((0u8..3, 0.5f32..1.5), 3), 1..6),
        transforms in transforms(),
        samples in sample_count(),
    ) {
        let (w, h) = (24usize, 18usize);
        let mut frame = abi_frame(w as u32, h as u32, samples as u32);
        frame.ao = 1;
        let mut versions = vec![1u64; scene.len()];
        let cache = crate::ffi::th_cache_create();
        let draw = |views: &[ThMeshView], cache: *const crate::ffi::ThCache| {
            let (mut color, mut depth) = (vec![0.0f32; w * h * 4], vec![0.0f32; w * h]);
            let status = unsafe {
                th_render(&frame, views.as_ptr(), views.len() as u32, color.as_mut_ptr(), depth.as_mut_ptr(), cache)
            };
            (status, color.iter().map(|c| c.to_bits()).collect::<Vec<_>>(), depth.iter().map(|d| d.to_bits()).collect::<Vec<_>>())
        };
        for step in steps {
            let mut drawn = Vec::new();
            for (m, (points, _)) in scene.iter_mut().enumerate() {
                let (edit, scale) = step[m % step.len()];
                match edit {
                    1 => {
                        for p in points.iter_mut() {
                            p[1] *= scale;
                            p[2] += scale * p[0];
                        }
                        versions[m] += 1;
                        drawn.push(m);
                    }
                    2 => {}
                    _ => drawn.push(m),
                }
            }
            let views: Vec<ThMeshView> = drawn.iter().map(|&m| ThMeshView {
                polygonal: 1,
                key: m as u64 + 1,
                version: versions[m],
                ..view_of(&scene[m].0, &scene[m].1, &transforms)
            }).collect();
            let kept = draw(&views, cache);
            let fresh = draw(&views, std::ptr::null());
            prop_assert_eq!(kept.0, TH_OK);
            prop_assert!(kept == fresh, "a frame drawn with kept normals differs");
        }
        unsafe { crate::ffi::th_cache_free(cache) };
    }
}

// A delegate's kept memory (raster::Scratch: sample buffers reused, a vertex
// arena that only grows; kept smooth and cusp normals) never changes a
// picture, whatever ran before.

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    // Over any run of frames of any size, sample count, look, alpha and lens
    // -- the viewer resizing, a draft (1 spp, no AO) then a full frame -- in
    // which a mesh may switch between polygonal (cusp normals) and smooth
    // under the same key, every frame drawn with the delegate's cache is the
    // frame drawn without one, to the bit, ids included: nothing of an
    // earlier frame's buffers or normals shows through.
    #[test]
    fn a_kept_cache_draws_any_run_of_frames_as_drawn_anew(
        scene in prop::collection::vec(mesh_data(), 1..4),
        steps in prop::collection::vec((
            prop::sample::select(vec![(24u32, 18u32), (24, 18), (17, 31), (40, 9)]),
            sample_count(), 0u32..3, any::<bool>(), any::<bool>(),
            prop::option::of((prop::sample::select(vec![4u32, 16]), 0.5f32..4.0)),
            prop::collection::vec(any::<bool>(), 4), [-1.5f64..1.5, -1.5f64..1.5],
        ), 1..6),
        transforms in transforms(),
    ) {
        let cache = crate::ffi::th_cache_create();
        for (step, (size, samples, shading, ao, alpha, lens, polygonal, [dx, dy])) in steps.into_iter().enumerate() {
            let (w, h) = (size.0 as usize, size.1 as usize);
            let mut frame = abi_frame(size.0, size.1, samples as u32);
            frame.shading = shading;
            frame.ao = ao as u32;
            frame.alpha = alpha as u32;
            if let Some((layers, f_stop)) = lens {
                frame.blur_samples = layers;
                frame.focal_length = 0.05;
                frame.f_stop = f_stop;
                frame.focus_distance = 5.0;
            }
            // The instances move from frame to frame (as an orbit moves the
            // view): what a pixel covered last frame it may not cover now.
            let moved: Vec<Mat4> = transforms.iter().map(|&t| math::mul(&t, &translation(dx, dy, 0.0))).collect();
            let views: Vec<ThMeshView> = scene.iter().enumerate().map(|(m, (points, triangles))| ThMeshView {
                polygonal: polygonal[m] as u32,
                key: m as u64 + 1,
                version: 1,
                ..view_of(points, triangles, &moved)
            }).collect();
            // Ids too: a pixel left uncovered must not keep an earlier
            // frame's surface for the viewer to pick.
            let draw = |cache: *const crate::ffi::ThCache| {
                let (mut color, mut depth, mut ids) = (vec![0.0f32; w * h * 4], vec![0.0f32; w * h], vec![7i32; w * h * 2]);
                let cancel = std::sync::atomic::AtomicU32::new(0);
                let status = unsafe {
                    crate::ffi::th_render_ids(&frame, views.as_ptr(), views.len() as u32, color.as_mut_ptr(),
                                              depth.as_mut_ptr(), ids.as_mut_ptr(), &cancel, cache)
                };
                (status, color.iter().map(|c| c.to_bits()).collect::<Vec<_>>(),
                 depth.iter().map(|d| d.to_bits()).collect::<Vec<_>>(), ids)
            };
            let kept = draw(cache);
            let fresh = draw(std::ptr::null());
            prop_assert_eq!(kept.0, TH_OK);
            prop_assert!(kept == fresh, "frame {} drawn with the kept cache differs", step);
        }
        unsafe { crate::ffi::th_cache_free(cache) };
    }

    // Instances wholly out of view -- beside it, behind the camera, past the
    // far plane -- are skipped by an unblurred frame (raster::outside) but
    // drawn by a still scene's layers, which never skip: the two pictures
    // agree to the byte however the instances are strewn, so skipping drops
    // nothing that reaches a sample.
    #[test]
    fn instances_out_of_view_change_nothing(
        size in 0.3f32..4.0, yaw in -0.8f32..0.8, pitch in -0.8f32..0.8,
        // Across the view and its edges (the camera at z = 5 sees x and y
        // out to +-distance), behind the camera and past the far plane.
        spots in prop::collection::vec([-1.6f64..1.6, -1.6f64..1.6, -6.0f64..130.0], 1..12),
        samples in prop::sample::select(vec![1usize, 4]),
    ) {
        let points = turned_quad(size, yaw, pitch);
        // Each instance at its own depth: no two surfaces tie for a sample.
        let mut placed: Vec<Mat4> = spots.iter().enumerate()
            .map(|(i, &[u, v, distance])| translation(u * distance.abs(), v * distance.abs(), 5.0 - distance + 0.013 * i as f64))
            .collect();
        placed.dedup();
        let still = [plain(&points, &QUAD, &placed)];
        let keys: Vec<Mat4> = placed.repeat(2);
        // A zero shutter: closing points and transforms the opening's.
        let layers = [Mesh { points_close: Some(&points), transform_keys: &keys, ..plain(&points, &QUAD, &placed) }];
        let f = frame(32, 24, samples);
        let reference = raster::render(&f, &still);
        let mut layered = blurred(f, samples * 2, true, None);
        layered.blur.cameras[0] = f.view_to_world;
        layered.blur.cameras[1] = f.view_to_world;
        layered.blur.camera_keys = 2;
        let image = raster::render_blurred(&layered, &layers);
        prop_assert!(image.color == reference.color, "skipping out-of-view instances changed the picture");
        prop_assert!(image.depth == reference.depth, "skipping out-of-view instances changed depth");
        prop_assert!(image.ids == reference.ids, "skipping out-of-view instances changed ids");
    }
}

// Linear depth (ThFrame::linear_depth, Houdini's viewer): what Houdini reads
// as each pixel's distance from the camera, the point its Set Pivot tumble
// orbits.

/// A camera's projection, USD's row-vector layout: perspective (`s` =
/// focal length over half the aperture, a finite far plane or none, as
/// Houdini's viewer hands it) or orthographic (`s` = 1 over the half width).
fn projection(s: f64, near: f64, far: Option<f64>, orthographic: bool) -> Mat4 {
    if orthographic {
        let f = far.unwrap_or(near * 1000.0);
        return [s, 0.0, 0.0, 0.0, 0.0, s, 0.0, 0.0, 0.0, 0.0, -2.0 / (f - near), 0.0,
                0.0, 0.0, -(f + near) / (f - near), 1.0];
    }
    let (p22, p32) = match far {
        Some(f) => (-(f + near) / (f - near), -2.0 * f * near / (f - near)),
        None => (-1.0, -2.0 * near),
    };
    [s, 0.0, 0.0, 0.0, 0.0, s, 0.0, 0.0, 0.0, 0.0, p22, -1.0, 0.0, 0.0, p32, 0.0]
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 64, ..ProptestConfig::default() })]

    // A wall square to the camera at view distance `distance`, over the
    // left half of the frame, through any camera placement and projection
    // (near anywhere, far finite or at infinity, perspective or
    // orthographic): with linear depth every pixel it covers reads that
    // distance, every pixel it leaves reads f32::MAX, and nothing else in
    // the frame changes (colour to the bit; window depth as before).
    #[test]
    fn linear_depth_is_the_distance_along_the_view_axis(
        eye in [-5.0f64..5.0, -5.0f64..5.0, -5.0f64..5.0], yaw in -3.1f64..3.1, pitch in -1.2f64..1.2,
        near in 0.05f64..2.0, far_times in prop::option::of(20.0f64..2000.0), orthographic: bool,
        s in 0.3f64..3.0, at in 0.05f64..0.9,
    ) {
        let far = far_times.map(|t| near * t);
        // Between the planes, not on them: near * 1.5 to most of the far
        // plane (or 400 near planes on, at infinity).
        let reach = far.map_or(near * 400.0, |f| f * 0.9);
        let distance = near * 1.5 + at * (reach - near * 1.5);
        let (sy, cy) = yaw.sin_cos();
        let (sp, cp) = pitch.sin_cos();
        // View-to-world: pitch about x, then yaw about y, then the eye.
        let view_to_world: Mat4 = [cy, 0.0, -sy, 0.0, sy * sp, cp, cy * sp, 0.0, sy * cp, -sp, cy * cp, 0.0,
                                   eye[0], eye[1], eye[2], 1.0];
        // Its inverse (a rigid transform: the rotation transposed, the eye
        // turned back through it).
        let r = |i: usize, j: usize| view_to_world[i * 4 + j];
        let back = |j: usize| -(0..3).map(|k| eye[k] * r(j, k)).sum::<f64>();
        let world_to_view: Mat4 = [r(0, 0), r(1, 0), r(2, 0), 0.0, r(0, 1), r(1, 1), r(2, 1), 0.0,
                                   r(0, 2), r(1, 2), r(2, 2), 0.0, back(0), back(1), back(2), 1.0];
        let proj = projection(s, near, far, orthographic);
        // Out past the frame's top, bottom and left; its right edge down
        // the middle.
        let half = if orthographic { 2.0 / s } else { 2.0 * distance / s };
        let wall_view = [[-half, -half, -distance], [0.0, -half, -distance], [0.0, half, -distance], [-half, half, -distance]];
        let points: Vec<Vec3> = wall_view.iter()
            .map(|&p| math::transform_point(p.map(|c| c as f32), &view_to_world))
            .collect();
        let triangles = [[0u32, 1, 2], [0, 2, 3]];
        let transforms = [math::IDENTITY];
        let (w, h) = (16usize, 16usize);
        let mut frame = abi_frame(w as u32, h as u32, 1);
        frame.world_to_clip = math::mul(&world_to_view, &proj);
        frame.view_to_world = view_to_world;
        frame.shading = 1;
        let views = [view_of(&points, &triangles, &transforms)];
        let draw = |frame: &ThFrame| {
            let (mut color, mut depth) = (vec![0.0f32; w * h * 4], vec![0.0f32; w * h]);
            let status = unsafe {
                th_render(frame, views.as_ptr(), 1, color.as_mut_ptr(), depth.as_mut_ptr(), std::ptr::null())
            };
            (status, color, depth)
        };
        // Both rasterisers: the viewer draws on the GPU by default (with its
        // own clip-depth mapping, wgpu's [0, w]), on the CPU without one.
        let gpus: &[u32] = if cfg!(feature = "gpu") { &[0, 1] } else { &[0] };
        for &gpu in gpus {
            frame.gpu = gpu;
            frame.linear_depth = 0;
            let (status, window_color, window_depth) = draw(&frame);
            prop_assert_eq!(status, TH_OK);
            frame.linear_depth = 1;
            let (status, color, depth) = draw(&frame);
            prop_assert_eq!(status, TH_OK);
            prop_assert!(color.iter().zip(&window_color).all(|(a, b)| a.to_bits() == b.to_bits()),
                         "colour changed (gpu {})", gpu);
            for y in 0..h {
                for x in 0..w {
                    let (z, d) = (depth[y * w + x], window_depth[y * w + x]);
                    prop_assert!((0.0..=1.0).contains(&d), "window depth {} at {:?} (gpu {})", d, (x, y), gpu);
                    if x + 2 <= w / 2 {
                        prop_assert!(((z as f64) - distance).abs() <= 1e-3 * distance,
                                     "covered pixel {:?} reads {}, the wall is {} away (window depth {}, gpu {})",
                                     (x, y), z, distance, d, gpu);
                    } else if x > w / 2 {
                        prop_assert_eq!(z, f32::MAX, "uncovered pixel {:?} (window depth {}, gpu {})", (x, y), d, gpu);
                    }
                }
            }
        }
    }

    // The conversion alone, over the whole depth range: a point anywhere
    // between the planes (spread evenly in log distance, out to 99.9% of
    // the far plane, or a million near planes on at infinity), put through
    // the projection to window depth as both rasterisers store it
    // ((z / w) * 0.5 + 0.5, in f32), comes back as its view distance, to
    // what f32 window depth can hold there; any two points keep their
    // order, so what Houdini composites (grid, guides, handles) stays in
    // front of or behind the scene; and nothing (1, or past) is f32::MAX.
    #[test]
    fn linear_depth_undoes_the_projection_and_keeps_the_order(
        near in 0.01f64..5.0, far_times in prop::option::of(2.0f64..1e5), orthographic: bool,
        s in 0.3f64..3.0, ats in prop::collection::vec(0.0f64..1.0, 2..16),
    ) {
        let far = far_times.map(|t| near * t);
        let proj = projection(s, near, far, orthographic);
        // The far plane the projection has (orthographic always has one).
        let far = if orthographic { Some(far.unwrap_or(near * 1000.0)) } else { far };
        let mut frame = abi_frame(1, 1, 1);
        frame.view_to_world = math::IDENTITY;
        frame.world_to_clip = proj;
        let linear = linear_depth(&frame);
        let reach = far.map_or(near * 1e6, |f| f * 0.999);
        let window = |z: f64| {
            let (clip_z, clip_w) = (-z * proj[10] + proj[14], -z * proj[11] + proj[15]);
            ((clip_z / clip_w) * 0.5 + 0.5) as f32
        };
        let mut seen: Vec<(f64, f32, f32)> = ats.iter()
            .map(|&at| {
                let z = near * (reach / near).powf(at);
                let d = window(z);
                (z, d, linear(d))
            })
            .collect();
        for &(z, d, back) in &seen {
            if d >= 1.0 {
                prop_assert_eq!(back, f32::MAX, "z {} rounds to window depth 1", z);
                continue;
            }
            let next = f32::from_bits(d.to_bits() + 1);
            if next >= 1.0 {
                continue; // the last step before 1: only the order to check
            }
            // One f32 step of window depth, in distance, either side.
            let step = (linear(next) as f64 - back as f64).abs()
                .max((back as f64 - linear(f32::from_bits(d.to_bits().saturating_sub(1))) as f64).abs());
            prop_assert!(((back as f64) - z).abs() <= 1e-5 * z + step,
                         "z {} -> window {} -> {} (one step {})", z, d, back, step);
        }
        seen.sort_by(|a, b| a.0.total_cmp(&b.0));
        for pair in seen.windows(2) {
            prop_assert!(pair[0].2 <= pair[1].2, "order lost: {:?} then {:?}", pair[0], pair[1]);
        }
        prop_assert_eq!(linear(1.0), f32::MAX);
        prop_assert_eq!(linear(1.5), f32::MAX);
    }
}

// ---------------------------------------------------------------------------
// See-through surfaces (raster::Surface::Glass): a mesh of opacity under 1
// blended over the opaque picture.
// ---------------------------------------------------------------------------

/// A square filling the whole view (the camera sees +-d at distance d) at
/// world depth `z`, of `opacity` and `color`.
fn pane<'a>(points: &'a [Vec3], transforms: &'a [Mat4], color: Vec3, opacity: f32) -> Mesh<'a> {
    Mesh { base_color: color, opacity, ..plain(points, &QUAD, transforms) }
}

/// The corners and transform of a square at depth `z` that fills the view.
fn pane_at(z: f64) -> (Vec<Vec3>, [Mat4; 1]) {
    let size = (5.0 - z) as f32 * 2.0 + 1.0;
    (turned_quad(size, 0.0, 0.0), [translation(0.0, 0.0, z)])
}

const ONE_TRIANGLE: [[u32; 3]; 1] = [[0, 1, 2]];

fn see_through() -> impl Strategy<Value = f32> {
    0.02f32..0.98
}

fn colour() -> impl Strategy<Value = Vec3> {
    [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0]
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 24, ..ProptestConfig::default() })]

    /// What cannot be seen changes nothing, to the byte (colour, alpha,
    /// depth and ids), in any shading, with occlusion or depth of field or
    /// not: a mesh of opacity 0 or less in front of everything, and a
    /// see-through one behind an opaque wall (the opaque depth hides it).
    /// An opacity of 1 or more, or NaN, is opaque.
    #[test]
    fn what_cannot_be_seen_through_changes_nothing(
        quads in quads(), samples in sample_count(), shading in shading_mode(), ao: bool, alpha: bool,
        dof: bool, invisible in prop::sample::select(vec![0.0f32, -1.0]), hidden in see_through(),
        opaque in prop::sample::select(vec![1.0f32, 2.0, f32::INFINITY, f32::NAN]),
    ) {
        let geometry = quad_geometry(&quads);
        // One triangle: the opaque rasteriser's test can leave a sample
        // uncovered along an edge two triangles share, where glass behind
        // would show.
        let (wall, wall_at) = (vec![[-40.0f32, -40.0, 0.0], [80.0, -40.0, 0.0], [-40.0, 80.0, 0.0]], [translation(0.0, 0.0, -6.0)]);
        let (front, front_at) = pane_at(4.0);
        let (back, back_at) = pane_at(-8.0);
        let mut scene: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        scene.push(Mesh { base_color: [0.3, 0.6, 0.2], ..plain(&wall, &ONE_TRIANGLE, &wall_at) });
        let mut f = Frame { shading, ao, alpha, ..frame(32, 24, samples) };
        if dof {
            f = blurred(f, 16, false, Lens::of_camera(0.5, 0.05, 3.0));
        }
        let reference = raster::render(&f, &scene);
        let same = |image: &raster::Image| image.color.iter().zip(&reference.color)
            .all(|(a, b)| a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits()))
            && image.depth == reference.depth && image.ids == reference.ids;

        let mut with_invisible: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        with_invisible.push(Mesh { base_color: [0.3, 0.6, 0.2], ..plain(&wall, &ONE_TRIANGLE, &wall_at) });
        with_invisible.push(pane(&front, &front_at, [1.0, 0.0, 1.0], invisible));
        prop_assert!(same(&raster::render(&f, &with_invisible)), "an invisible mesh changed the picture");

        let mut with_hidden: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        with_hidden.push(Mesh { base_color: [0.3, 0.6, 0.2], ..plain(&wall, &ONE_TRIANGLE, &wall_at) });
        with_hidden.push(pane(&back, &back_at, [1.0, 0.0, 1.0], hidden));
        prop_assert!(same(&raster::render(&f, &with_hidden)), "glass behind the wall changed the picture");

        let solid: Vec<Mesh> = scene.iter().map(|m| Mesh { opacity: opaque, tiles: Vec::new(), ..*m }).collect();
        prop_assert!(same(&raster::render(&f, &solid)), "opacity {} is not opaque", opaque);
    }

    /// A see-through surface in front of everything blends over the
    /// picture behind it: unlit, every pixel is `a * colour + (1 - a) *`
    /// the pixel without it, alpha `a + (1 - a) *` its alpha (a transparent
    /// background: see-through surfaces add partial coverage), depth as
    /// without it, and every pixel picks it (the nearest surface).
    #[test]
    fn a_see_through_surface_blends_over_the_picture_behind(
        quads in quads(), samples in sample_count(), alpha: bool, a in see_through(), c in colour(),
    ) {
        let geometry = quad_geometry(&quads);
        let (front, front_at) = pane_at(4.0);
        let behind: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        let mut meshes: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        meshes.push(pane(&front, &front_at, c, a));
        let f = Frame { alpha, ..flat_frame(32, 24, samples) };
        let without = raster::render(&f, &behind);
        let with = raster::render(&f, &meshes);
        prop_assert!(with.depth == without.depth, "a see-through surface wrote depth");
        for (p, (o, t)) in without.color.iter().zip(&with.color).enumerate() {
            for k in 0..3 {
                let want = a * c[k] + (1.0 - a) * o[k];
                prop_assert!((t[k] - want).abs() < 1e-5, "pixel {} channel {}: {} is not {} (a {}, behind {:?})",
                             p, k, t[k], want, a, o);
            }
            let want = if alpha { a + (1.0 - a) * o[3] } else { 1.0 };
            prop_assert!((t[3] - want).abs() < 1e-5, "pixel {}: alpha {} is not {}", p, t[3], want);
        }
        let glass = meshes.len() as i32 - 1;
        prop_assert!(with.ids.iter().all(|&id| id == [glass, 0]), "the pane in front is not what every pixel picks");
    }

    /// Several see-through surfaces blend in any order alike (weighted
    /// blended order-independent transparency): drawn front to back or
    /// back to front, the picture is the same within rounding, and it is
    /// the blend's: what is behind times the product of one minus each
    /// opacity, plus their opacity-weighted mean colour times the rest.
    /// Picking names the nearest whichever order they came in.
    #[test]
    fn see_through_surfaces_blend_in_any_order(
        quads in quads(), samples in sample_count(), alpha: bool,
        panes in prop::collection::vec((see_through(), colour()), 2..5),
    ) {
        let geometry = quad_geometry(&quads);
        let built: Vec<(Vec<Vec3>, [Mat4; 1])> = (0..panes.len()).map(|i| pane_at(3.7 + 0.25 * i as f64)).collect();
        let glass: Vec<Mesh> = built.iter().zip(&panes).map(|((p, t), &(a, c))| pane(p, t, c, a)).collect();
        let behind: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
        let f = Frame { alpha, ..flat_frame(32, 24, samples) };
        let without = raster::render(&f, &behind);
        let order = |reversed: bool| -> Vec<Mesh> {
            let mut m: Vec<Mesh> = geometry.iter().map(|(p, t)| plain(p, &QUAD, t)).collect();
            let mut g: Vec<Mesh> = glass.iter().map(|m| Mesh { tiles: Vec::new(), ..*m }).collect();
            if reversed {
                g.reverse();
            }
            m.extend(g);
            m
        };
        let (forward, backward) = (raster::render(&f, &order(false)), raster::render(&f, &order(true)));
        let reveal: f32 = panes.iter().map(|(a, _)| 1.0 - a).product();
        let weight: f32 = panes.iter().map(|(a, _)| a).sum();
        let mean: Vec3 = std::array::from_fn(|k| panes.iter().map(|(a, c)| a * c[k]).sum::<f32>() / weight);
        for (p, o) in without.color.iter().enumerate() {
            let (x, y) = (forward.color[p], backward.color[p]);
            for k in 0..3 {
                prop_assert!((x[k] - y[k]).abs() < 1e-5, "pixel {}: the order changed {} to {}", p, x[k], y[k]);
                let want = o[k] * reveal + mean[k] * (1.0 - reveal);
                prop_assert!((x[k] - want).abs() < 1e-5, "pixel {} channel {}: {} is not the blend {}", p, k, x[k], want);
            }
            let want = if alpha { o[3] + (1.0 - o[3]) * (1.0 - reveal) } else { 1.0 };
            prop_assert!((x[3] - want).abs() < 1e-5 && (y[3] - want).abs() < 1e-5, "pixel {}: alpha {} / {} is not {}",
                         p, x[3], y[3], want);
        }
        // The nearest pane is the last built: last in the forward order,
        // first of the panes in the backward one.
        let n = geometry.len() as i32;
        let last = n + panes.len() as i32 - 1;
        prop_assert!(forward.ids.iter().all(|&id| id == [last, 0]), "forward: not the nearest pane");
        prop_assert!(backward.ids.iter().all(|&id| id == [n, 0]), "backward: not the nearest pane");
    }

    /// A still scene with see-through surfaces, drawn in layers (motion
    /// blur on, nothing moving, a pinhole), is the unblurred picture to the
    /// byte: each layer blends its own samples as the single pass does.
    #[test]
    fn see_through_surfaces_drawn_in_layers_are_the_unblurred_picture(
        quads in quads(), samples in prop::sample::select(vec![1usize, 4, 8]),
        chunks in prop::sample::select(vec![1usize, 2, 4]), a in see_through(), which in 0usize..4,
    ) {
        let geometry = quad_geometry(&quads);
        let pick = which % geometry.len();
        let meshes: Vec<Mesh> = geometry.iter().enumerate()
            .map(|(i, (p, t))| Mesh { opacity: if i == pick { a } else { 1.0 }, ..plain(p, &QUAD, t) })
            .collect();
        let f = frame(32, 24, samples);
        let reference = raster::render(&f, &meshes);
        let mut layered = blurred(f, samples * chunks, true, None);
        layered.blur.cameras[0] = f.view_to_world;
        layered.blur.cameras[1] = f.view_to_world;
        layered.blur.camera_keys = 2;
        let image = raster::render_blurred(&layered, &meshes);
        prop_assert!(image.color == reference.color, "layered picture differs");
        prop_assert!(image.depth == reference.depth && image.ids == reference.ids);
    }
}

/// Through the C ABI: `transparency` is one minus the opacity, so a zeroed
/// view is opaque; NaN is opaque and the rest is clamped. A fully
/// transparent view draws nothing; a half one blends over the backdrop.
#[test]
fn the_abi_transparency_is_one_minus_the_opacity() {
    use crate::ffi::opacity_of;
    assert_eq!(opacity_of(0.0, false), 1.0);
    assert_eq!(opacity_of(1.0, false), 0.0);
    assert_eq!(opacity_of(0.25, false), 0.75);
    assert_eq!(opacity_of(f32::NAN, false), 1.0);
    assert_eq!(opacity_of(-3.0, false), 1.0);
    assert_eq!(opacity_of(5.0, false), 0.0);

    let (points, at) = pane_at(4.0);
    let mut frame = abi_frame(8, 6, 4);
    frame.shading = Shading::Albedo.abi();
    let draw = |transparency: f32| {
        let view = ThMeshView { base_color: [1.0, 1.0, 1.0], transparency, ..view_of(&points, &QUAD, &at) };
        let mut color = vec![0.0f32; 8 * 6 * 4];
        assert_eq!(unsafe { th_render(&frame, &view, 1, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null()) },
                   TH_OK);
        color
    };
    let mut empty = vec![0.0f32; 8 * 6 * 4];
    assert_eq!(unsafe { th_render(&frame, std::ptr::null(), 0, empty.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null()) },
               TH_OK);
    assert_eq!(draw(1.0), empty, "fully transparent draws nothing");
    let half = draw(0.5);
    for y in 0..6 {
        let g = backdrop_at(y, 6);
        for p in half[y * 8 * 4..(y + 1) * 8 * 4].chunks(4) {
            assert!((p[0] - (0.5 + 0.5 * g)).abs() < 1e-5, "row {y}: {p:?} is not half white over {g}");
        }
    }
    assert!(draw(0.0).chunks(4).all(|p| p[0] == 1.0), "opaque white");
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(64))]

    /// th:transparency "off" (`ThFrame::opaque`): any transparency at all,
    /// NaN and out of range included, is drawn opaque, the picture of a
    /// zeroed one, in front of an opaque mesh it would otherwise show; and
    /// "on" (opaque 0, a frame zeroed before the switch existed) is the old
    /// one minus the transparency, clamped, NaN opaque.
    #[test]
    fn the_abi_opaque_switch_draws_every_view_opaque(
        transparency in prop_oneof![
            -2.0f32..3.0, Just(0.5f32), Just(1.0f32), Just(f32::NAN), Just(f32::INFINITY),
            proptest::num::f32::ANY,
        ],
    ) {
        use crate::ffi::opacity_of;
        prop_assert_eq!(opacity_of(transparency, true), 1.0);
        let on = opacity_of(transparency, false);
        let old = if transparency.is_nan() { 1.0 } else { (1.0 - transparency).clamp(0.0, 1.0) };
        prop_assert!(on == old, "on: {} for {}, not {}", on, transparency, old);

        let (front, at) = pane_at(4.0);
        let (back, behind) = pane_at(1.0);
        let draw = |opaque: u32, transparency: f32| {
            let mut frame = abi_frame(8, 6, 4);
            frame.shading = Shading::Albedo.abi();
            frame.opaque = opaque;
            let views = [
                ThMeshView { base_color: [0.1, 0.8, 0.2], ..view_of(&back, &QUAD, &behind) },
                ThMeshView { base_color: [1.0, 1.0, 1.0], transparency, ..view_of(&front, &QUAD, &at) },
            ];
            let mut color = vec![0.0f32; 8 * 6 * 4];
            assert_eq!(unsafe { th_render(&frame, views.as_ptr(), 2, color.as_mut_ptr(), std::ptr::null_mut(),
                                          std::ptr::null()) }, TH_OK);
            color
        };
        let solid = draw(0, 0.0);
        prop_assert!(draw(1, transparency) == solid, "opaque frame: transparency {} shows through", transparency);
        if on < 1.0 {
            prop_assert!(draw(0, transparency) != solid, "transparency {} no longer shows through", transparency);
        }
    }
}

// ---------------------------------------------------------------------------
// A mesh in parts (the shim's GeomSubsets): one view per material, each a
// run of the mesh's triangles, with the whole mesh's normals
// (ffi::th_mesh_normals).
// ---------------------------------------------------------------------------

/// A bumpy sheet facing the camera: an `n` x `n` grid of points at
/// `heights` (towards the camera), two triangles a cell.
pub(crate) fn sheet(n: usize, heights: &[f32]) -> (Vec<Vec3>, Vec<[u32; 3]>) {
    let at = |i: usize| (i as f32 / (n - 1) as f32) * 3.0 - 1.5;
    let points = (0..n * n).map(|k| [at(k % n), at(k / n), heights[k]]).collect();
    let mut triangles = Vec::new();
    for y in 0..n - 1 {
        for x in 0..n - 1 {
            let (k, n) = ((y * n + x) as u32, n as u32);
            triangles.push([k, k + 1, k + n + 1]);
            triangles.push([k, k + n + 1, k + n]);
        }
    }
    (points, triangles)
}

/// A sheet's size, heights, and per triangle the part (0..3) it is in.
pub(crate) fn parted_sheet() -> impl Strategy<Value = (usize, Vec<f32>, Vec<usize>)> {
    (3usize..7).prop_flat_map(|n| {
        (Just(n), prop::collection::vec(-0.6f32..0.6, n * n), prop::collection::vec(0usize..3, 2 * (n - 1) * (n - 1)))
    })
}

/// The sheet's triangles sorted by part (as the shim sorts them), and each
/// part's run of them.
pub(crate) fn by_part(triangles: &[[u32; 3]], part: &[usize]) -> (Vec<[u32; 3]>, Vec<(usize, std::ops::Range<usize>)>) {
    let mut sorted = Vec::new();
    let mut runs = Vec::new();
    for p in 0..3 {
        let first = sorted.len();
        sorted.extend(triangles.iter().zip(part).filter(|(_, &q)| q == p).map(|(t, _)| *t));
        if sorted.len() > first {
            runs.push((p, first..sorted.len()));
        }
    }
    (sorted, runs)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

    /// A mesh drawn as one view per part, each painted its own colour and
    /// given the whole mesh's normals, is to the bit the whole mesh drawn
    /// once with each corner coloured by its part: colour, depth and ids
    /// (a part naming the mesh), cusp or smooth, lit and occluded. Normals
    /// worked out per part would crease the shading where parts meet.
    #[test]
    fn a_mesh_drawn_in_parts_is_the_whole_mesh_coloured_by_part(
        (n, heights, part) in parted_sheet(), polygonal: bool, samples in prop::sample::select(vec![1u32, 4]),
        colours in prop::collection::vec(colour(), 3),
    ) {
        use crate::ffi::{mesh_normals, th_render_ids};
        let (points, triangles) = sheet(n, &heights);
        let (sorted, runs) = by_part(&triangles, &part);
        let at = [translation(0.0, 0.0, 1.0)];
        let mut corner_colors = Vec::new();
        for (p, run) in &runs {
            corner_colors.extend(std::iter::repeat_n(colours[*p], run.len() * 3));
        }
        let whole = ThMeshView {
            corner_colors: corner_colors.as_ptr() as *const f32,
            polygonal: polygonal as u32,
            ..view_of(&points, &sorted, &at)
        };
        let normals = mesh_normals(&points, &sorted, polygonal);
        prop_assert_eq!(normals.len(), sorted.len() * 3);
        let parts: Vec<ThMeshView> = runs.iter().map(|(p, run)| ThMeshView {
            triangles: sorted[run.clone()].as_ptr() as *const u32,
            triangle_count: run.len() as u32,
            corner_normals: normals[run.start * 3..].as_ptr() as *const f32,
            base_color: colours[*p],
            polygonal: polygonal as u32,
            ..view_of(&points, &sorted, &at)
        }).collect();
        let mut frame = abi_frame(40, 30, samples);
        frame.ao = 1;
        let draw = |views: &[ThMeshView]| {
            let (mut color, mut depth, mut ids) = (vec![0.0f32; 40 * 30 * 4], vec![0.0f32; 40 * 30], vec![0i32; 40 * 30 * 2]);
            let status = unsafe {
                th_render_ids(&frame, views.as_ptr(), views.len() as u32, color.as_mut_ptr(), depth.as_mut_ptr(),
                              ids.as_mut_ptr(), std::ptr::null(), std::ptr::null())
            };
            assert_eq!(status, TH_OK);
            (color, depth, ids)
        };
        let (wc, wd, wi) = draw(std::slice::from_ref(&whole));
        let (pc, pd, pi) = draw(&parts);
        prop_assert!(wc.iter().zip(&pc).all(|(a, b)| a.to_bits() == b.to_bits()), "the parts are not the whole mesh's colour");
        prop_assert!(wd == pd, "depth differs");
        let named: Vec<i32> = pi.chunks(2).flat_map(|id| if id[0] >= 0 { [0, id[1]] } else { [-1, -1] }).collect();
        prop_assert!(wi == named, "a part does not pick as the mesh");
    }
}

/// How an instance of a quad is placed for culling: turned (yaw, pitch),
/// scaled with each axis possibly mirrored, and moved.
pub(crate) type Placing = (f64, f64, [f64; 3], [f64; 3]);

/// One to four instances' placings, none turned edge-on to the camera.
pub(crate) fn placings() -> impl Strategy<Value = Vec<Placing>> {
    let axis = (any::<bool>(), 0.5f64..1.0).prop_map(|(mirror, s)| if mirror { -s } else { s });
    prop::collection::vec(
        (-2.8f64..2.8, -1.2f64..1.2, [axis.clone(), axis.clone(), axis], [-1.5f64..1.5, -1.5f64..1.5, -2.0f64..1.0]),
        1..5,
    )
}

/// An instance's transform from its placing.
pub(crate) fn placed((yaw, pitch, s, t): &Placing) -> Mat4 {
    let (cy, sy, cp, sp) = (yaw.cos(), yaw.sin(), pitch.cos(), pitch.sin());
    let scale: Mat4 = [s[0], 0.0, 0.0, 0.0, 0.0, s[1], 0.0, 0.0, 0.0, 0.0, s[2], 0.0, 0.0, 0.0, 0.0, 1.0];
    let turn_y: Mat4 = [cy, 0.0, -sy, 0.0, 0.0, 1.0, 0.0, 0.0, sy, 0.0, cy, 0.0, 0.0, 0.0, 0.0, 1.0];
    let turn_x: Mat4 = [1.0, 0.0, 0.0, 0.0, 0.0, cp, sp, 0.0, 0.0, -sp, cp, 0.0, 0.0, 0.0, 0.0, 1.0];
    math::mul(&math::mul(&math::mul(&scale, &turn_y), &turn_x), &translation(t[0], t[1], t[2]))
}

/// A unit quad in z = 0 wound counter-clockwise seen from +z (its front
/// faces +z), or clockwise when `reversed`.
pub(crate) fn facing_quad(reversed: bool) -> (Vec<Vec3>, Vec<[u32; 3]>) {
    let points = vec![[-0.5, -0.5, 0.0], [0.5, -0.5, 0.0], [0.5, 0.5, 0.0], [-0.5, 0.5, 0.0]];
    let triangles = if reversed { vec![[0, 2, 1], [0, 3, 2]] } else { QUAD.to_vec() };
    (points, triangles)
}

/// Whether the front of `facing_quad(reversed)` placed by `xform` faces
/// the camera (at z = 5), worked out from the surface's normal carried
/// through the transform as normals are, not from any winding on screen;
/// None when it is too near edge-on to say.
pub(crate) fn front_faces_camera(xform: &Mat4, reversed: bool) -> Option<bool> {
    let normal = math::normalize(math::transform_normal([0.0, 0.0, if reversed { -1.0 } else { 1.0 }],
                                                        &math::normal_matrix(xform)));
    let centre = math::transform_point([0.0; 3], xform);
    let to_eye = math::normalize(math::sub([0.0, 0.0, 5.0], centre));
    let cos = math::dot(normal, to_eye);
    (cos.abs() > 0.25).then_some(cos > 0.0)
}

/// Whether an instance whose front does or does not face the camera is
/// culled by `cull`.
pub(crate) fn culled(cull: raster::Cull, front: bool) -> bool {
    match cull {
        raster::Cull::Nothing => false,
        raster::Cull::Back => !front,
        raster::Cull::Front => front,
    }
}

pub(crate) fn cull_style() -> impl Strategy<Value = raster::Cull> {
    prop::sample::select(vec![raster::Cull::Nothing, raster::Cull::Back, raster::Cull::Front])
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 64, ..ProptestConfig::default() })]

    /// Culling a side drops exactly the instances showing it: the picture
    /// is, to the bit, the instances left drawn with nothing culled, and
    /// they alone are picked. Which side shows comes from the surface's
    /// normal through the instance's transform, so a mirroring transform
    /// keeps its front outside (Storm's rule), and a quad wound the other
    /// way has its front on the other side.
    #[test]
    fn culling_drops_exactly_the_instances_showing_the_culled_side(
        placing in placings(), reversed: bool, cull in cull_style(), samples in prop::sample::select(vec![1usize, 4]),
    ) {
        let (points, triangles) = facing_quad(reversed);
        let transforms: Vec<Mat4> = placing.iter().map(placed).collect();
        let fronts: Option<Vec<bool>> = transforms.iter().map(|m| front_faces_camera(m, reversed)).collect();
        let Some(fronts) = fronts else { return Ok(()) };
        let kept: Vec<usize> = (0..transforms.len()).filter(|&i| !culled(cull, fronts[i])).collect();
        let kept_transforms: Vec<Mat4> = kept.iter().map(|&i| transforms[i]).collect();
        let f = frame(48, 36, samples);
        let image = raster::render(&f, &[Mesh { cull, ..plain(&points, &triangles, &transforms) }]);
        let want = raster::render(&f, &[plain(&points, &triangles, &kept_transforms)]);
        prop_assert!(image.color.iter().zip(&want.color).all(|(a, b)| a.map(f32::to_bits) == b.map(f32::to_bits)),
                     "the picture is not the instances left's ({:?}, fronts {:?})", cull, fronts);
        prop_assert!(image.depth == want.depth, "depth differs");
        let named: Vec<[i32; 2]> = want.ids.iter().map(|&[m, i]| if m < 0 { [m, i] } else { [m, kept[i as usize] as i32] }).collect();
        prop_assert!(image.ids == named, "a culled instance is picked");
    }
}

/// Through the ABI: `cull` 1 drops a quad facing the camera with its back,
/// 2 one facing it with its front, 0 neither; unknown codes cull nothing.
#[test]
fn cull_through_the_abi() {
    use crate::ffi::th_render_ids;
    let at = [translation(0.0, 0.0, 1.0)];
    let draw = |reversed: bool, cull: u32| {
        let (points, triangles) = facing_quad(reversed);
        let view = ThMeshView { cull, ..view_of(&points, &triangles, &at) };
        let frame = abi_frame(20, 16, 1);
        let (mut color, mut depth, mut ids) = (vec![0.0f32; 20 * 16 * 4], vec![0.0f32; 20 * 16], vec![0i32; 20 * 16 * 2]);
        let status = unsafe {
            th_render_ids(&frame, &view, 1, color.as_mut_ptr(), depth.as_mut_ptr(), ids.as_mut_ptr(),
                          std::ptr::null(), std::ptr::null())
        };
        assert_eq!(status, TH_OK);
        ids.chunks(2).filter(|id| id[0] >= 0).count()
    };
    for (reversed, cull, drawn) in [(false, 0, true), (false, 1, true), (false, 2, false), (true, 0, true),
                                    (true, 1, false), (true, 2, true), (true, 7, true)] {
        assert_eq!(draw(reversed, cull) > 0, drawn, "reversed {reversed}, cull {cull}");
    }
}

/// A quad 60 across running away from the camera ([`camera`], far plane
/// 100 away) from 50 to 150 deep as it rises: its lower half on screen in
/// front of the far plane, its upper half beyond it.
pub(crate) fn past_the_far_plane() -> (Vec<Vec3>, Vec<[u32; 3]>) {
    (vec![[-30.0, -30.0, -45.0], [30.0, -30.0, -45.0], [30.0, 30.0, -145.0], [-30.0, 30.0, -145.0]], QUAD.to_vec())
}

/// How far from the camera the ray through screen point (`xn`, `yn`, NDC)
/// meets [`past_the_far_plane`]'s quad, None when it misses it.
pub(crate) fn past_the_far_plane_depth(xn: f64, yn: f64) -> Option<f64> {
    // The quad's plane is z = -95 - 5/3 y; the ray is (0, 0, 5) + t (xn, yn, -1).
    let t = 100.0 / (1.0 - 5.0 / 3.0 * yn);
    (t > 0.0 && (t * xn).abs() <= 30.0 && (t * yn).abs() <= 30.0).then_some(t)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 8, ..ProptestConfig::default() })]

    /// The far plane is kept per sample, not per mesh or triangle: of a
    /// triangle that crosses it, what is in front is drawn and what is
    /// beyond shows the backdrop (depth 1), and nothing beyond is picked.
    /// (The instance cull is only a shortcut; the depth test's `z < 1`
    /// keeps the plane.) Pixels within a few units of the plane or the
    /// quad's edges are left out: their samples may go either way.
    #[test]
    fn what_is_past_the_far_plane_is_not_drawn(samples in sample_count()) {
        let (points, triangles) = past_the_far_plane();
        let one = [math::IDENTITY];
        let (w, h) = (64usize, 48usize);
        let image = raster::render(&frame(w, h, samples), &[plain(&points, &triangles, &one)]);
        let (mut near, mut far) = (0, 0);
        for y in 0..h {
            for x in 0..w {
                let xn = (x as f64 + 0.5) / w as f64 * 2.0 - 1.0;
                let yn = 1.0 - (y as f64 + 0.5) / h as f64 * 2.0;
                let at = y * w + x;
                let Some(t) = past_the_far_plane_depth(xn, yn) else { continue };
                let inside = (t * xn).abs() < 28.0 && (t * yn).abs() < 28.0;
                if inside && t < 92.0 {
                    near += 1;
                    prop_assert!(image.depth[at] < 1.0, "({}, {}) at {:.1} is not drawn", x, y, t);
                } else if t > 108.0 {
                    far += 1;
                    prop_assert!(image.depth[at] == 1.0, "({}, {}) at {:.1} is drawn", x, y, t);
                    prop_assert!(image.ids[at][0] < 0, "({}, {}) at {:.1} is picked", x, y, t);
                }
            }
        }
        prop_assert!(near > 100 && far > 40, "near {}, far {}", near, far);
    }
}

/// th_mesh_normals: the core's normals for a mesh, one per corner; nothing
/// to do for no triangles, an error for a null pointer.
#[test]
fn mesh_normals_through_the_abi() {
    use crate::ffi::{mesh_normals, th_mesh_normals};
    let heights = [0.0, 0.3, -0.2, 0.1, 0.5, 0.0, 0.2, -0.4, 0.1, 0.0, 0.3, 0.2, -0.1, 0.0, 0.4, 0.1];
    let (points, triangles) = sheet(4, &heights);
    for polygonal in [0u32, 1] {
        let mut out = vec![0.0f32; triangles.len() * 9];
        let status = unsafe {
            th_mesh_normals(points.as_ptr() as *const f32, points.len() as u32, triangles.as_ptr() as *const u32,
                            triangles.len() as u32, polygonal, out.as_mut_ptr())
        };
        assert_eq!(status, TH_OK);
        let want: Vec<f32> = mesh_normals(&points, &triangles, polygonal != 0).into_iter().flatten().collect();
        assert_eq!(out, want);
        if polygonal == 1 {
            let cusp: Vec<f32> =
                raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES).into_iter().flatten().collect();
            assert_eq!(out, cusp);
        } else {
            let smooth = raster::smooth_normals(&points, &triangles);
            assert_eq!(&out[..3], &smooth[triangles[0][0] as usize][..]);
        }
    }
    unsafe {
        assert_eq!(th_mesh_normals(std::ptr::null(), 0, std::ptr::null(), 0, 0, std::ptr::null_mut()), TH_OK);
        assert_eq!(th_mesh_normals(points.as_ptr() as *const f32, points.len() as u32, triangles.as_ptr() as *const u32,
                                   triangles.len() as u32, 0, std::ptr::null_mut()), crate::TH_ERR_ARGS);
    }
}

// Scene lights (crate::lights): the scene's UsdLux lights instead of the
// fixed camera rig.

/// Light to world for a light at `at` facing `facing` (its -Z), unscaled.
fn facing_transform(facing: Vec3, at: Vec3) -> Mat4 {
    let z = math::normalize(math::scale(facing, -1.0));
    let up = if z[1].abs() < 0.9 { [0.0, 1.0, 0.0] } else { [1.0, 0.0, 0.0] };
    let x = math::normalize(math::cross(up, z));
    let y = math::cross(z, x);
    let mut m = math::IDENTITY;
    for (r, axis) in [x, y, z, at].iter().enumerate() {
        for c in 0..3 {
            m[r * 4 + c] = axis[c] as f64;
        }
    }
    m
}

fn unit() -> impl Strategy<Value = Vec3> {
    [-1.0f32..1.0, -1.0f32..1.0, -1.0f32..1.0]
        .prop_filter("not near zero", |v| math::dot(*v, *v) > 0.05)
        .prop_map(math::normalize)
}

/// A few lights of any kind about the origin, as the shim hands them over.
fn some_lights() -> impl Strategy<Value = Vec<SceneLight>> {
    let one = (0u32..6, colour(), 0.1f32..20.0, any::<bool>(), any::<bool>(), point(), unit(),
               [0.05f32..1.0, 0.1f32..2.0, 0.1f32..2.0, 0.1f32..2.0]);
    prop::collection::vec(one, 1..6).prop_map(|lights| {
        lights.into_iter()
            .map(|(kind, color, intensity, normalize, point, at, facing, sizes)| {
                let shape = LightShape::from_abi(kind).unwrap();
                SceneLight::placed(shape, math::scale(color, intensity), normalize, point, 0.53, sizes,
                                   &facing_transform(facing, at))
            })
            .collect()
    })
}

/// The fixed rig's lighting as it was before scene lights existed: a grey
/// k, one scalar highlight of the key light.
fn rig_as_it_was(shading: Shading, albedo: Vec3, n: Vec3, v: Vec3) -> Vec3 {
    let albedo = if shading == Shading::Clay { composite::CLAY_ALBEDO } else { albedo };
    let lights = composite::LIGHTS.map(|(l, i)| (math::normalize(l), i));
    let mut k = composite::GROUND + (composite::SKY - composite::GROUND) * (0.5 + 0.5 * n[1]);
    for &(l, intensity) in &lights {
        k += intensity * math::dot(n, l).max(0.0);
    }
    let h = math::normalize(math::add(lights[0].0, v));
    let spec = composite::SPECULAR * math::dot(n, h).max(0.0).powi(composite::SHININESS);
    [albedo[0] * k + spec, albedo[1] * k + spec, albedo[2] * k + spec]
}

proptest! {
    /// No scene lights (or the headlight asked for) is the fixed rig to the
    /// bit, lit or clay, wherever the surface is.
    #[test]
    fn no_scene_lights_is_the_fixed_rig(
        shading in prop::sample::select(vec![Shading::Lit, Shading::Clay]),
        albedo in colour(), n in unit(), v in unit(), p in point(),
    ) {
        let lighting = Lighting::new(&SceneLights::NONE, &camera().1);
        prop_assert_eq!(composite::light(shading, albedo, n, v, p, &lighting), rig_as_it_was(shading, albedo, n, v));
    }

    /// One normalized distant light shining straight onto a surface seen
    /// head on lights it to albedo x intensity x colour / pi (Karma's
    /// brightness), plus the highlight of that light.
    #[test]
    fn a_distant_light_along_the_normal_lights_albedo_times_its_colour(
        albedo in colour(), color in colour(), intensity in 0.0f32..50.0, n in unit(),
    ) {
        let light = SceneLight::placed(LightShape::Distant, math::scale(color, intensity), true, false, 0.53,
                                       [0.0; 4], &facing_transform(math::scale(n, -1.0), [0.0; 3]));
        let lighting = Lighting::new(&SceneLights::new(&[light]), &math::IDENTITY);
        let got = composite::light(Shading::Lit, albedo, n, n, [0.0, 0.0, -3.0], &lighting);
        for c in 0..3 {
            let e = color[c] * intensity / std::f32::consts::PI;
            let want = albedo[c] * e + crate::lights::SCENE_SPECULAR * e;
            prop_assert!((got[c] - want).abs() <= 1e-4 * want.max(1.0), "channel {}: {} vs {}", c, got[c], want);
        }
    }

    /// Clay is lit by the scene's lights as a lit surface of the clay
    /// colour; albedo and occlusion are not lit at all.
    #[test]
    fn clay_takes_the_same_lights_and_flat_shading_none(
        lights in some_lights(), albedo in colour(), n in unit(), v in unit(), p in point(),
    ) {
        let lighting = Lighting::new(&SceneLights::new(&lights), &camera().1);
        prop_assert_eq!(composite::light(Shading::Clay, albedo, n, v, p, &lighting),
                        composite::light(Shading::Lit, composite::CLAY_ALBEDO, n, v, p, &lighting));
        prop_assert_eq!(composite::light(Shading::Albedo, albedo, n, v, p, &lighting), albedo);
        prop_assert_eq!(composite::light(Shading::Occlusion, albedo, n, v, p, &lighting), [1.0; 3]);
    }

    /// Lighting never goes negative or non-finite, and more light (every
    /// light brighter) never darkens a surface.
    #[test]
    fn scene_lighting_is_finite_and_grows_with_the_lights(
        lights in some_lights(), albedo in colour(), n in unit(), v in unit(), p in point(), more in 1.0f32..4.0,
    ) {
        let view = camera().1;
        let lit = composite::light(Shading::Lit, albedo, n, v, p, &Lighting::new(&SceneLights::new(&lights), &view));
        let brighter: Vec<SceneLight> = lights.iter()
            .map(|l| SceneLight { radiance: math::scale(l.radiance, more), ..*l })
            .collect();
        let lit_more = composite::light(Shading::Lit, albedo, n, v, p,
                                        &Lighting::new(&SceneLights::new(&brighter), &view));
        for c in 0..3 {
            prop_assert!(lit[c].is_finite() && lit[c] >= 0.0, "{:?}", lit);
            prop_assert!(lit_more[c] >= lit[c] * (1.0 - 1e-5), "{:?} brighter is {:?}", lit, lit_more);
        }
    }
}

/// A light the shim hands over: at `at`, facing `facing`, normalized.
fn abi_light(kind: u32, intensity: f32, at: Vec3, facing: Vec3) -> ThLight {
    ThLight {
        transform: facing_transform(facing, at),
        kind, normalize: 1, point: 0,
        color: [1.0, 0.9, 0.8], intensity, temperature: 0.0,
        angle: 0.53, radius: 0.2, width: 1.0, height: 1.0, length: 1.0,
    }
}

/// Through the ABI: the headlight setting is the fixed rig whatever the
/// scene's lights; lights of no known kind are left out (only such lights
/// is the rig); a light of intensity 0 adds nothing; and a scene light
/// changes the picture.
#[test]
fn the_abi_lighting_switch_and_lights_that_add_nothing() {
    let points = vec![[-1.5f32, -1.5, 0.0], [1.5, -1.5, 0.0], [1.5, 1.5, 0.0], [-1.5, 1.5, 0.0]];
    let one = [math::IDENTITY];
    let view = view_of(&points, &QUAD, &one);
    let draw = |lights: &[ThLight], lighting: u32| {
        let mut f = abi_frame(16, 12, 4);
        f.lights = if lights.is_empty() { std::ptr::null() } else { lights.as_ptr() };
        f.light_count = lights.len() as u32;
        f.lighting = lighting;
        let mut color = vec![0.0f32; 16 * 12 * 4];
        let status = unsafe {
            th_render(&f, &view, 1, color.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null())
        };
        assert_eq!(status, TH_OK);
        color
    };
    let rig = draw(&[], 0);
    let key = abi_light(1, 40.0, [0.5, 1.0, 2.0], [0.0, 0.0, -1.0]);
    let sun = abi_light(0, 2.0, [0.0; 3], [0.3, -0.5, -1.0]);
    let lit = draw(&[key, sun], 0);
    assert_ne!(lit, rig, "scene lights light the frame");
    assert_eq!(draw(&[key, sun], 1), rig, "the headlight is the rig");
    assert_eq!(draw(&[abi_light(9, 5.0, [0.0; 3], [0.0, 0.0, -1.0])], 0), rig, "an unknown light is no light");
    let dark = abi_light(2, 0.0, [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]);
    let unknown = abi_light(7, 3.0, [0.0; 3], [0.0, 0.0, -1.0]);
    assert_eq!(draw(&[key, sun, dark, unknown], 0), lit, "a dark light and an unknown one add nothing");
    // The light falls on the quad's side facing the camera: brighter in the
    // corner nearer the sphere light (up and right) than the far one.
    let at = |x: usize, y: usize| lit[(y * 16 + x) * 4];
    assert!(at(10, 4) > at(3, 9), "nearer the light is brighter: {} vs {}", at(10, 4), at(3, 9));
}
