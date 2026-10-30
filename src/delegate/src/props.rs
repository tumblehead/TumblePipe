//! Properties of the rendering core (proptest). The example tests next to
//! each module pin single pictures; these pin what has to hold for any
//! mesh Hydra can hand over, including broken ones (indices past the
//! points, NaN points from a bad deformer).

use crate::ffi::{th_render, ThFrame, ThMeshView};
use crate::math::{self, Mat4, Vec3};
use crate::raster::{self, Frame, Mesh};
use crate::TH_OK;
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

fn frame(width: usize, height: usize, supersample: usize) -> Frame {
    let (world_to_clip, view_to_world) = camera();
    Frame { width, height, world_to_clip, view_to_world, supersample }
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
        base_color: [0.6, 0.5, 0.4], tiles: Vec::new(), transforms,
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
    fn flat_normals_are_each_faces_own((points, triangles) in mesh_data()) {
        let normals = raster::flat_normals(&points, &triangles);
        prop_assert_eq!(normals.len(), triangles.len() * 3);
        for (t, tri) in triangles.iter().enumerate() {
            let corners = &normals[t * 3..t * 3 + 3];
            prop_assert!(corners[0] == corners[1] && corners[1] == corners[2]);
            prop_assert!((length(corners[0]) - 1.0).abs() < 1e-4);
            let Some([a, b, c]) = tri.iter().map(|&i| points.get(i as usize).copied())
                .collect::<Option<Vec<_>>>().map(|v| [v[0], v[1], v[2]]) else { continue };
            let (e1, e2) = (math::sub(b, a), math::sub(c, a));
            let cross = math::cross(e1, e2);
            // Skip slivers: their f32 normal has no stable direction.
            if length(e1) < 0.1 || length(e2) < 0.1 || length(cross) < 0.05 * length(e1) * length(e2) {
                continue;
            }
            let n = corners[0];
            prop_assert!(math::dot(n, e1).abs() / length(e1) < 1e-3);
            prop_assert!(math::dot(n, e2).abs() / length(e2) < 1e-3);
            // Facing follows the winding, as the smooth normals' does.
            prop_assert!(math::dot(n, cross) > 0.0);
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
    fn binning_keeps_every_pixel_centre_a_triangle_covers(
        corners in [clip_corner(), clip_corner(), clip_corner()],
        width in 1usize..64,
        height in 1usize..64,
        band_rows in 1usize..16,
    ) {
        // Clip-space corners in front of the near plane (z >= -w).
        let clip = corners.map(|([x, y, z], w)| [(x * w) as f32, (y * w) as f32, (z * w) as f32, w as f32]);
        let bands = raster::triangle_bands(clip, width, height, band_rows);
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
                let p = (px as f64 + 0.5, py as f64 + 0.5);
                let e = [edge(screen[1], screen[2], p), edge(screen[2], screen[0], p), edge(screen[0], screen[1], p)];
                // Strictly inside, by a margin: the edges themselves are the
                // rasteriser's call, not the binner's.
                let inside = area.abs() > 1e-9 && e.iter().all(|&v| v * area.signum() > 1e-6 * area.abs());
                if inside {
                    let (b0, b1) = bands.ok_or_else(|| TestCaseError::fail(format!("pixel ({px}, {py}) covered but no bands")))?;
                    prop_assert!(b0 <= py / band_rows && py / band_rows <= b1,
                        "pixel row {} (band {}) outside bands {}..={}", py, py / band_rows, b0, b1);
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
        supersample in 1usize..4,
        nan_at in prop::option::of(0usize..24),
    ) {
        let mut points = points;
        if let Some(i) = nan_at.filter(|&i| i < points.len()) {
            points[i][1] = f32::NAN;
        }
        let image = raster::render(&frame(width, height, supersample), &[plain(&points, &triangles, &transforms)]);
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
    fn a_faceted_mesh_draws_as_its_flat_normals(
        (points, triangles) in mesh_data(),
        transforms in transforms(),
    ) {
        let flat = raster::flat_normals(&points, &triangles);
        let (world_to_clip, view_to_world) = camera();
        let frame = ThFrame { width: 24, height: 16, world_to_clip, view_to_world, supersample: 1 };
        let view = |faceted: u32, normals: *const f32| ThMeshView {
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
            faceted,
        };
        let draw = |v: ThMeshView| {
            let mut color = vec![0.0f32; 24 * 16 * 4];
            let status = unsafe { th_render(&frame, &v, 1, color.as_mut_ptr(), std::ptr::null_mut()) };
            (status, color)
        };
        let flat_ptr = if flat.is_empty() { std::ptr::null() } else { flat.as_ptr() as *const f32 };
        let faceted = draw(view(1, std::ptr::null()));
        let explicit = draw(view(0, flat_ptr));
        prop_assert_eq!(faceted.0, TH_OK);
        prop_assert!(faceted.1 == explicit.1, "faceted differs from its flat normals");
        // Authored normals always win over the flag.
        let smooth: Vec<Vec3> = vec![[0.0, 1.0, 0.0]; triangles.len() * 3];
        let smooth_ptr = if smooth.is_empty() { std::ptr::null() } else { smooth.as_ptr() as *const f32 };
        prop_assert!(draw(view(1, smooth_ptr)).1 == draw(view(0, smooth_ptr)).1);
    }
}
