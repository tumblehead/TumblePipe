//! Builders the property tests share: the test camera, a frame through it
//! and a mesh view, everything else zeroed, so a new ABI field touches only
//! the tests that set it.

use crate::ffi::{ThFrame, ThMeshView};
use crate::math::{self, Mat4, Vec3};

/// Looking down -z from z = 5, a 90 degree perspective.
pub(crate) fn camera() -> (Mat4, Mat4) {
    let mut view_to_world = math::IDENTITY;
    view_to_world[14] = 5.0;
    let mut world_to_view = math::IDENTITY;
    world_to_view[14] = -5.0;
    let (n, f) = (0.1f64, 100.0f64);
    let proj: Mat4 = [
        1.0,
        0.0,
        0.0,
        0.0, //
        0.0,
        1.0,
        0.0,
        0.0, //
        0.0,
        0.0,
        -(f + n) / (f - n),
        -1.0, //
        0.0,
        0.0,
        -2.0 * f * n / (f - n),
        0.0,
    ];
    (math::mul(&world_to_view, &proj), view_to_world)
}

/// `width` x `height` through [`camera`], `samples` a pixel.
pub(crate) fn abi_frame(width: u32, height: u32, samples: u32) -> ThFrame {
    let (world_to_clip, view_to_world) = camera();
    ThFrame { samples, ..ThFrame::new(width, height, world_to_clip, view_to_world) }
}

/// A mesh's view: its points, triangles and instance transforms, the test
/// colour, smooth normals made by the core.
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
        polygonal: 0,
        key: 0,
        version: 0,
        points_close: std::ptr::null(),
        transform_keys: std::ptr::null(),
        transform_key_count: 0,
        transparency: 0.0,
        cull: 0,
    }
}
