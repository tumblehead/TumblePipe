//! The C ABI the shim calls. `cpp/th_delegate_core.h` declares the same
//! structs and functions; keep the two in step.

use crate::math::{Mat4, Vec3};
use crate::raster::{self, Frame, Mesh};
use crate::texture::Texture;
use crate::{ffi_guard, TH_ERR_ARGS, TH_OK};
use std::slice;

/// One image of a texture set: `tile` is the UDIM number, 0 for a plain
/// texture.
#[repr(C)]
pub struct ThTextureTile {
    pub tile: i32,
    pub texture: *const Texture,
}

/// Borrowed geometry of one mesh and every instance of it.
#[repr(C)]
pub struct ThMeshView {
    /// `point_count` xyz triples, object space.
    pub points: *const f32,
    pub point_count: u32,
    /// `triangle_count` index triples into `points`.
    pub triangles: *const u32,
    pub triangle_count: u32,
    /// Optional, 9 floats per triangle (one xyz per corner).
    pub corner_normals: *const f32,
    /// Optional, 9 floats per triangle (one linear rgb per corner).
    pub corner_colors: *const f32,
    /// Optional, 6 floats per triangle (one st per corner).
    pub corner_uvs: *const f32,
    /// Albedo where neither corner colours nor a texture apply.
    pub base_color: [f32; 3],
    pub tiles: *const ThTextureTile,
    pub tile_count: u32,
    /// `transform_count` object-to-world matrices, 16 doubles each in USD's
    /// row-vector layout; one per instance.
    pub transforms: *const f64,
    pub transform_count: u32,
    /// Nonzero for a polygonal mesh (subdivision scheme none or bilinear):
    /// without `corner_normals` it is drawn with per-face normals, as Storm
    /// draws it, instead of smoothed ones.
    pub faceted: u32,
}

#[repr(C)]
pub struct ThFrame {
    pub width: u32,
    pub height: u32,
    /// World to clip space (GL clip, row-vector layout).
    pub world_to_clip: [f64; 16],
    /// Camera to world.
    pub view_to_world: [f64; 16],
    /// Samples per pixel along each axis (1 = no anti-aliasing).
    pub supersample: u32,
}

/// Whether this process asked for the GPU path: `TH_PLAYBLAST_GPU` set to
/// 1, true or on.
#[cfg(feature = "gpu")]
fn gpu_requested() -> bool {
    std::env::var("TH_PLAYBLAST_GPU")
        .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "on"))
        .unwrap_or(false)
}

/// The GPU path when it was asked for and works, else the CPU one. A GPU
/// failure is reported (once) and the frame drawn on the CPU instead, so a
/// worker without a usable GPU still finishes the playblast.
fn render(frame: &Frame, meshes: &[Mesh]) -> raster::Image {
    // A check for a new GPU or driver: TH_PLAYBLAST_COMPARE=1 draws every
    // mesh alone, small, both ways and reports the meshes whose pictures
    // differ. On HideAndReek/010 (RTX 3070) the only one is a set mesh whose
    // coplanar faces z-fight, which the two rasterisers resolve differently.
    #[cfg(feature = "gpu")]
    if std::env::var("TH_PLAYBLAST_COMPARE").is_ok() {
        let small = Frame { width: 320, height: 180, supersample: 1, ..*frame };
        for (i, m) in meshes.iter().enumerate() {
            let one = std::slice::from_ref(m);
            let (cpu, gpu) = (raster::render(&small, one), crate::gpu::render(&small, one));
            let gpu = match gpu { Ok(g) => g, Err(e) => { eprintln!("mesh {i}: GPU error {e}"); continue } };
            let cov = |d: &[f32]| d.iter().filter(|&&z| z < 1.0).count();
            let diff = cpu.color.iter().zip(&gpu.color).filter(|(a, b)| (0..3).any(|c| (a[c] - b[c]).abs() > 0.02)).count();
            if diff > 20 {
                eprintln!("mesh {i}: {diff} px differ; cpu covers {}, gpu {}; {} pts {} tris, normals {} colors {} uvs {}, tiles {:?}, {} instances",
                    cov(&cpu.depth), cov(&gpu.depth), m.points.len(), m.triangles.len(),
                    m.corner_normals.is_some(), m.corner_colors.is_some(), m.corner_uvs.is_some(),
                    m.tiles.iter().map(|(t, x)| (*t, x.width, x.height)).collect::<Vec<_>>(), m.transforms.len());
            }
        }
    }
    #[cfg(feature = "gpu")]
    if gpu_requested() {
        use std::sync::atomic::{AtomicBool, Ordering};
        static REPORTED: AtomicBool = AtomicBool::new(false);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| crate::gpu::render(frame, meshes)));
        let error = match result {
            Ok(Ok(image)) => return image,
            Ok(Err(error)) => error,
            Err(_) => "the GPU path panicked".to_string(),
        };
        if !REPORTED.swap(true, Ordering::Relaxed) {
            eprintln!("hdThPlayblast: GPU unavailable, drawing on the CPU: {error}");
        }
    }
    raster::render(frame, meshes)
}

unsafe fn opt_slice<'a, T>(ptr: *const f32, count: usize) -> Option<&'a [T]> {
    (!ptr.is_null() && count > 0).then(|| slice::from_raw_parts(ptr as *const T, count))
}

/// Shrink and keep a decoded texture. `pixels` is `width * height` linear
/// RGBA floats, row 0 at the top of the image. Returns null on bad input.
///
/// # Safety
/// `pixels` must be valid for reads of `width * height * 4` floats.
#[no_mangle]
pub unsafe extern "C" fn th_texture_create(pixels: *const f32, width: u32, height: u32) -> *mut Texture {
    let mut out: *mut Texture = std::ptr::null_mut();
    ffi_guard(|| {
        if pixels.is_null() || width == 0 || height == 0 {
            return TH_ERR_ARGS;
        }
        let (w, h) = (width as usize, height as usize);
        let texels = slice::from_raw_parts(pixels as *const [f32; 4], w * h);
        out = Box::into_raw(Box::new(Texture::from_linear(texels, w, h)));
        TH_OK
    });
    out
}

/// # Safety
/// `texture` must come from [`th_texture_create`] and not be freed twice.
#[no_mangle]
pub unsafe extern "C" fn th_texture_free(texture: *mut Texture) {
    if !texture.is_null() {
        drop(Box::from_raw(texture));
    }
}

/// Render `meshes` into `color` (`width * height` linear RGBA floats) and
/// `depth` (`width * height` floats, may be null), both row 0 at the top.
///
/// # Safety
/// Every pointer in `frame` and `meshes` must be valid for the counts it is
/// given with; `color` and `depth` for `width * height` texels.
#[no_mangle]
pub unsafe extern "C" fn th_render(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    color: *mut f32,
    depth: *mut f32,
) -> i32 {
    ffi_guard(|| {
        if frame.is_null() || color.is_null() || (meshes.is_null() && mesh_count > 0) {
            return TH_ERR_ARGS;
        }
        let f = &*frame;
        if f.width == 0 || f.height == 0 {
            return TH_ERR_ARGS;
        }
        let views = if mesh_count > 0 { slice::from_raw_parts(meshes, mesh_count as usize) } else { &[] };
        let views: Vec<&ThMeshView> = views
            .iter()
            .filter(|v| !v.points.is_null() && !v.triangles.is_null() && !v.transforms.is_null())
            .collect();
        let points = |v: &ThMeshView| slice::from_raw_parts(v.points as *const Vec3, v.point_count as usize);
        let triangles = |v: &ThMeshView| slice::from_raw_parts(v.triangles as *const [u32; 3], v.triangle_count as usize);
        // Owned here, borrowed by the meshes below.
        let flat: Vec<Option<Vec<Vec3>>> = views
            .iter()
            .map(|v| (v.faceted != 0 && v.corner_normals.is_null())
                .then(|| raster::flat_normals(points(v), triangles(v))))
            .collect();
        let mut list = Vec::with_capacity(views.len());
        for (v, flat) in views.iter().zip(&flat) {
            let tris = v.triangle_count as usize;
            let tiles = if v.tiles.is_null() { &[][..] } else { slice::from_raw_parts(v.tiles, v.tile_count as usize) };
            list.push(Mesh {
                points: points(v),
                triangles: triangles(v),
                corner_normals: opt_slice(v.corner_normals, tris * 3).or(flat.as_deref().filter(|n| !n.is_empty())),
                corner_colors: opt_slice(v.corner_colors, tris * 3),
                corner_uvs: opt_slice(v.corner_uvs, tris * 3),
                base_color: v.base_color,
                tiles: tiles
                    .iter()
                    .filter(|t| !t.texture.is_null())
                    .map(|t| (t.tile, &*t.texture))
                    .collect(),
                transforms: slice::from_raw_parts(v.transforms as *const Mat4, v.transform_count as usize),
            });
        }
        let image = render(
            &Frame {
                width: f.width as usize,
                height: f.height as usize,
                world_to_clip: f.world_to_clip,
                view_to_world: f.view_to_world,
                supersample: f.supersample.max(1) as usize,
            },
            &list,
        );
        let n = f.width as usize * f.height as usize;
        slice::from_raw_parts_mut(color as *mut [f32; 4], n).copy_from_slice(&image.color);
        if !depth.is_null() {
            slice::from_raw_parts_mut(depth, n).copy_from_slice(&image.depth);
        }
        TH_OK
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_rejects_bad_arguments_and_draws_an_empty_scene() {
        let frame = ThFrame {
            width: 8, height: 4,
            world_to_clip: crate::math::IDENTITY, view_to_world: crate::math::IDENTITY,
            supersample: 1,
        };
        let mut color = vec![0.0f32; 8 * 4 * 4];
        let mut depth = vec![0.0f32; 8 * 4];
        unsafe {
            assert_eq!(th_render(std::ptr::null(), std::ptr::null(), 0, color.as_mut_ptr(), depth.as_mut_ptr()), TH_ERR_ARGS);
            assert_eq!(th_render(&frame, std::ptr::null(), 0, color.as_mut_ptr(), depth.as_mut_ptr()), TH_OK);
        }
        assert!(depth.iter().all(|&d| d == 1.0));
        assert!(color.chunks(4).all(|p| p[3] == 1.0 && p[0] > 0.1));
    }

    #[test]
    fn textures_round_trip_through_the_abi() {
        let px = vec![0.5f32; 16 * 16 * 4];
        unsafe {
            let t = th_texture_create(px.as_ptr(), 16, 16);
            assert!(!t.is_null());
            assert!(th_texture_create(std::ptr::null(), 16, 16).is_null());
            th_texture_free(t);
        }
    }
}
