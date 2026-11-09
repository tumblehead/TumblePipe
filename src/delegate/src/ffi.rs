//! The C ABI the shim calls. `cpp/th_delegate_core.h` declares the same
//! structs and functions; keep the two in step.

use crate::math::{Mat4, Vec3};
use crate::composite::Shading;
use crate::blur::{self, Blur, Lens};
use crate::raster::{self, Frame, Mesh, MeshKey};
use crate::texture::Texture;
use crate::{ffi_guard, TH_CANCELLED, TH_ERR_ARGS, TH_OK};
use std::sync::atomic::{AtomicU32, Ordering};
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
    /// without `corner_normals` it gets Houdini's cusp normals (smooth under
    /// `raster::CUSP_DEGREES`, hard edges above) instead of smoothing across
    /// every edge, which rounds a box off.
    pub polygonal: u32,
    /// The Hydra mesh this is, unique in the process; 0 for none, and the
    /// GPU path then uploads it every frame.
    pub key: u64,
    /// Changes whenever the mesh's points, topology or primvars are
    /// rebuilt: the GPU path keeps a mesh's geometry on the card while its
    /// key and version stay the same.
    pub version: u64,
    /// With motion blur, optional: `point_count` xyz triples, the points at
    /// the shutter's closing (`points` are then at its opening).
    pub points_close: *const f32,
    /// With motion blur, optional: `transform_key_count` (2 to
    /// `blur::MAX_KEYS`) transforms per instance at moments evenly spread
    /// over the shutter, key-major, 16 doubles each.
    pub transform_keys: *const f64,
    pub transform_key_count: u32,
}

/// What a render delegate keeps between frames: its meshes' cusp normals
/// ([`crate::normals`]) and, on the GPU path, its meshes' geometry and its
/// textures on the card ([`crate::gpu::Cache`]). One per
/// delegate, freed with it, so a closed render session gives its memory
/// back.
#[derive(Default)]
pub struct ThCache {
    /// Cusp normals of keyed polygonal meshes ([`crate::normals`]).
    pub(crate) normals: std::sync::Mutex<crate::normals::Cache>,
    #[cfg(feature = "gpu")]
    pub(crate) gpu: crate::gpu::Cache,
}

#[repr(C)]
pub struct ThFrame {
    pub width: u32,
    pub height: u32,
    /// World to clip space (GL clip, row-vector layout).
    pub world_to_clip: [f64; 16],
    /// Camera to world.
    pub view_to_world: [f64; 16],
    /// Samples per pixel: 1, 2, 4, 8 or 16, others the next fewer (1 = no
    /// anti-aliasing; see `composite::sample_offsets`).
    pub samples: u32,
    /// 0 lit, 1 clay, 2 albedo, 3 occlusion (`composite::Shading`).
    pub shading: u32,
    /// Nonzero: screen-space ambient occlusion.
    pub ao: u32,
    /// Layers to spread over the shutter and the lens (`crate::blur`); 0
    /// or 1, neither blur.
    pub blur_samples: u32,
    /// Nonzero: motion blur over the shutter.
    pub motion: u32,
    /// The camera's transform (camera to world) at `camera_keys` moments
    /// evenly spread over the shutter (0, or 2 to 4), 16 doubles each.
    pub camera_keys: u32,
    pub cameras: [f64; 64],
    /// Depth of field from the camera's lens (HdCamera, scene units): an
    /// f-stop of 0 is a pinhole, no depth of field.
    pub focal_length: f32,
    pub f_stop: f32,
    pub focus_distance: f32,
    /// Nonzero: a transparent background (`raster::Frame::alpha`), colour
    /// premultiplied by coverage and alpha the coverage; 0, the backdrop
    /// and alpha 1.
    pub alpha: u32,
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
/// `ids`: the caller wants the picking ids (the CPU path always makes
/// them; the GPU path only when asked, with an extra pass).
fn render(frame: &Frame, meshes: &[Mesh], cache: Option<&ThCache>, ids: bool) -> raster::Image {
    // A check for a new GPU or driver: TH_PLAYBLAST_COMPARE=1 draws every
    // mesh alone, small, both ways and reports the meshes whose pictures
    // differ. On HideAndReek/010 (RTX 3070) the only one is a set mesh whose
    // coplanar faces z-fight, which the two rasterisers resolve differently.
    #[cfg(feature = "gpu")]
    if std::env::var("TH_PLAYBLAST_COMPARE").is_ok() {
        let small = Frame { width: 320, height: 180, samples: 1, blur: Blur::OFF, ..*frame };
        for (i, m) in meshes.iter().enumerate() {
            let one = std::slice::from_ref(m);
            let (cpu, gpu) = (raster::render(&small, one), crate::gpu::render(&small, one, None, false));
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
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            crate::gpu::render(frame, meshes, cache.map(|c| &c.gpu), ids)
        }));
        let error = match result {
            Ok(Ok(image)) => return image,
            Ok(Err(error)) => error,
            Err(_) => "the GPU path panicked".to_string(),
        };
        if !REPORTED.swap(true, Ordering::Relaxed) {
            eprintln!("hdThPlayblast: GPU unavailable, drawing on the CPU: {error}");
        }
    }
    #[cfg(not(feature = "gpu"))]
    let _ = (cache, ids);
    raster::render(frame, meshes)
}

/// The blur a frame asks for: none at 0 or 1 blur samples; motion blur when
/// `motion` is set; depth of field when the lens is not a pinhole.
fn blur_of(f: &ThFrame) -> Blur {
    if f.blur_samples <= 1 {
        return Blur::OFF;
    }
    let keys = (f.camera_keys as usize).min(blur::MAX_KEYS);
    let mut cameras = [crate::math::IDENTITY; blur::MAX_KEYS];
    for (k, camera) in cameras.iter_mut().enumerate().take(keys) {
        camera.copy_from_slice(&f.cameras[k * 16..(k + 1) * 16]);
    }
    Blur {
        samples: f.blur_samples as usize,
        motion: f.motion != 0,
        cameras,
        camera_keys: if keys >= 2 { keys } else { 0 },
        lens: Lens::of_camera(f.focal_length, f.f_stop, f.focus_distance),
    }
}

/// A new, empty cache for one render delegate ([`ThCache`]).
#[no_mangle]
pub extern "C" fn th_cache_create() -> *mut ThCache {
    let mut out: *mut ThCache = std::ptr::null_mut();
    ffi_guard(|| {
        out = Box::into_raw(Box::default());
        TH_OK
    });
    out
}

/// # Safety
/// `cache` must come from [`th_cache_create`], not be freed twice, and not
/// be in use by a [`th_render`] call.
#[no_mangle]
pub unsafe extern "C" fn th_cache_free(cache: *mut ThCache) {
    if !cache.is_null() {
        ffi_guard(|| {
            drop(Box::from_raw(cache));
            TH_OK
        });
    }
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
/// `cache` (may be null) is the delegate's [`ThCache`]: with it the GPU path
/// keeps the meshes that have a key on the card from one frame to the next.
///
/// # Safety
/// Every pointer in `frame` and `meshes` must be valid for the counts it is
/// given with; `color` and `depth` for `width * height` texels; `cache` null
/// or from [`th_cache_create`], used by one call at a time.
#[no_mangle]
pub unsafe extern "C" fn th_render(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    color: *mut f32,
    depth: *mut f32,
    cache: *const ThCache,
) -> i32 {
    render_into(frame, meshes, mesh_count, color, depth, std::ptr::null_mut(), std::ptr::null(), cache)
}

/// [`th_render`] for a viewport. Also writes `ids` (may be null): per
/// texel, row 0 at the top, two ints, the index into `meshes` and the
/// instance (into its transforms) the texel's first sample sees, -1 -1
/// for none, on the CPU and the GPU path alike.
/// `cancel` (may be null) is checked while drawing: set it non-zero from
/// another thread to give the frame up, and the call returns
/// [`TH_CANCELLED`] having written nothing.
///
/// # Safety
/// As [`th_render`]; `ids` valid for `width * height * 2` ints, `cancel`
/// for the whole call.
#[no_mangle]
pub unsafe extern "C" fn th_render_ids(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    color: *mut f32,
    depth: *mut f32,
    ids: *mut i32,
    cancel: *const AtomicU32,
    cache: *const ThCache,
) -> i32 {
    render_into(frame, meshes, mesh_count, color, depth, ids, cancel, cache)
}

#[allow(clippy::too_many_arguments)]
unsafe fn render_into(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    color: *mut f32,
    depth: *mut f32,
    ids: *mut i32,
    cancel: *const AtomicU32,
    cache: *const ThCache,
) -> i32 {
    ffi_guard(|| {
        let _scope = crate::cancel::Scope::new(cancel);
        let cancelled = || !cancel.is_null() && (*cancel).load(Ordering::Relaxed) != 0;
        if frame.is_null() || color.is_null() || (meshes.is_null() && mesh_count > 0) {
            return TH_ERR_ARGS;
        }
        let f = &*frame;
        if f.width == 0 || f.height == 0 {
            return TH_ERR_ARGS;
        }
        let views = if mesh_count > 0 { slice::from_raw_parts(meshes, mesh_count as usize) } else { &[] };
        // The views drawn, and where each sits in `meshes` (for `ids`).
        let (kept, views): (Vec<i32>, Vec<&ThMeshView>) = views
            .iter()
            .enumerate()
            .filter(|(_, v)| !v.points.is_null() && !v.triangles.is_null() && !v.transforms.is_null())
            .map(|(i, v)| (i as i32, v))
            .unzip();
        let points = |v: &ThMeshView| slice::from_raw_parts(v.points as *const Vec3, v.point_count as usize);
        let triangles = |v: &ThMeshView| slice::from_raw_parts(v.triangles as *const [u32; 3], v.triangle_count as usize);
        // Owned here (or by the cache), borrowed by the meshes below.
        let started = std::time::Instant::now();
        let requests: Vec<Option<crate::normals::Request>> = views
            .iter()
            .map(|v| (v.polygonal != 0 && v.corner_normals.is_null()).then(|| crate::normals::Request {
                key: (v.key != 0).then_some(MeshKey { id: v.key, version: v.version }),
                points: points(v),
                triangles: triangles(v),
            }))
            .collect();
        let (cusp, made) = {
            let mut own = crate::normals::Cache::default();
            let mut kept = cache.as_ref().map(|c| c.normals.lock().unwrap_or_else(|e| e.into_inner()));
            kept.as_deref_mut().unwrap_or(&mut own).normals(&requests)
        };
        if std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
            eprintln!("hdThPlayblast: cusp normals {} ms ({} meshes worked out, {} kept)",
                      started.elapsed().as_millis(), made.computed, made.kept);
        }
        let mut list = Vec::with_capacity(views.len());
        for (v, cusp) in views.iter().zip(&cusp) {
            let tris = v.triangle_count as usize;
            let tiles = if v.tiles.is_null() { &[][..] } else { slice::from_raw_parts(v.tiles, v.tile_count as usize) };
            list.push(Mesh {
                points: points(v),
                triangles: triangles(v),
                corner_normals: opt_slice(v.corner_normals, tris * 3).or(cusp.as_deref().filter(|n| !n.is_empty())),
                corner_colors: opt_slice(v.corner_colors, tris * 3),
                corner_uvs: opt_slice(v.corner_uvs, tris * 3),
                base_color: v.base_color,
                tiles: tiles
                    .iter()
                    .filter(|t| !t.texture.is_null())
                    .map(|t| (t.tile, &*t.texture))
                    .collect(),
                transforms: slice::from_raw_parts(v.transforms as *const Mat4, v.transform_count as usize),
                key: (v.key != 0).then_some(MeshKey { id: v.key, version: v.version }),
                points_close: opt_slice(v.points_close, v.point_count as usize),
                transform_keys: if v.transform_keys.is_null() || v.transform_key_count < 2 {
                    &[]
                } else {
                    slice::from_raw_parts(v.transform_keys as *const Mat4,
                                          v.transform_key_count as usize * v.transform_count as usize)
                },
            });
        }
        let image = render(
            &Frame {
                width: f.width as usize,
                height: f.height as usize,
                world_to_clip: f.world_to_clip,
                view_to_world: f.view_to_world,
                samples: f.samples.max(1) as usize,
                shading: Shading::from_abi(f.shading),
                ao: f.ao != 0,
                blur: blur_of(f),
                alpha: f.alpha != 0,
            },
            &list,
            cache.as_ref(),
            !ids.is_null(),
        );
        if cancelled() {
            return TH_CANCELLED;
        }
        let n = f.width as usize * f.height as usize;
        slice::from_raw_parts_mut(color as *mut [f32; 4], n).copy_from_slice(&image.color);
        if !depth.is_null() {
            slice::from_raw_parts_mut(depth, n).copy_from_slice(&image.depth);
        }
        if !ids.is_null() {
            let out = slice::from_raw_parts_mut(ids as *mut [i32; 2], n);
            if image.ids.len() == n {
                for (o, &[m, i]) in out.iter_mut().zip(&image.ids) {
                    *o = if m >= 0 { [kept[m as usize], i] } else { [-1, -1] };
                }
            } else {
                out.fill([-1, -1]);
            }
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
            samples: 1, shading: 0, ao: 1,
            blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
            focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0,
        };
        let mut color = vec![0.0f32; 8 * 4 * 4];
        let mut depth = vec![0.0f32; 8 * 4];
        unsafe {
            assert_eq!(th_render(std::ptr::null(), std::ptr::null(), 0, color.as_mut_ptr(), depth.as_mut_ptr(), std::ptr::null()), TH_ERR_ARGS);
            assert_eq!(th_render(&frame, std::ptr::null(), 0, color.as_mut_ptr(), depth.as_mut_ptr(), std::ptr::null()), TH_OK);
        }
        assert!(depth.iter().all(|&d| d == 1.0));
        assert!(color.chunks(4).all(|p| p[3] == 1.0 && p[0] > 0.1));
    }

    #[test]
    fn caches_are_created_and_freed_and_null_is_no_cache() {
        let frame = ThFrame {
            width: 8, height: 4,
            world_to_clip: crate::math::IDENTITY, view_to_world: crate::math::IDENTITY,
            samples: 1, shading: 0, ao: 1,
            blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64],
            focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0,
        };
        let mut color = vec![0.0f32; 8 * 4 * 4];
        unsafe {
            let cache = th_cache_create();
            assert!(!cache.is_null());
            assert_eq!(th_render(&frame, std::ptr::null(), 0, color.as_mut_ptr(), std::ptr::null_mut(), cache), TH_OK);
            th_cache_free(cache);
            th_cache_free(std::ptr::null_mut());
        }
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

unsafe fn int_slice<'a>(ptr: *const i32, count: u32) -> &'a [i32] {
    if ptr.is_null() || count == 0 {
        &[]
    } else {
        slice::from_raw_parts(ptr, count as usize)
    }
}

/// Triangulate a polygon mesh ([`crate::triangulate`]): Hydra's fan for a
/// convex face, ear clipping for a concave one, so a face with a notch or a
/// window keeps it. Writes up to `capacity` triangles: three `corners` each
/// (positions in `indices`, which is also where a face-varying primvar's
/// values are) and one `faces` entry (the face, for a uniform primvar).
/// `left_handed` nonzero reverses the winding. Returns the number of
/// triangles, or `u32::MAX` when the topology does not add up, `capacity`
/// is short, or an argument is null.
///
/// # Safety
/// Every pointer must be valid for the count it is given with: `points`
/// for `point_count` xyz triples, `corners` for `3 * capacity` and `faces`
/// for `capacity` writes.
#[no_mangle]
pub unsafe extern "C" fn th_triangulate(
    points: *const f32,
    point_count: u32,
    counts: *const i32,
    count_n: u32,
    indices: *const i32,
    index_n: u32,
    holes: *const i32,
    hole_n: u32,
    left_handed: u32,
    corners: *mut u32,
    faces: *mut u32,
    capacity: u32,
) -> u32 {
    let mut written = u32::MAX;
    ffi_guard(|| {
        if corners.is_null() || faces.is_null() || (points.is_null() && point_count > 0) {
            return TH_ERR_ARGS;
        }
        let pts: &[Vec3] = if point_count == 0 {
            &[]
        } else {
            slice::from_raw_parts(points as *const Vec3, point_count as usize)
        };
        let Some((tris, fs)) = crate::triangulate::triangulate(
            pts,
            int_slice(counts, count_n),
            int_slice(indices, index_n),
            int_slice(holes, hole_n),
            left_handed != 0,
        ) else {
            return TH_ERR_ARGS;
        };
        if tris.len() > capacity as usize {
            return TH_ERR_ARGS;
        }
        let out_c = slice::from_raw_parts_mut(corners, tris.len() * 3);
        for (dst, t) in out_c.as_chunks_mut::<3>().0.iter_mut().zip(&tris) {
            dst.copy_from_slice(t);
        }
        slice::from_raw_parts_mut(faces, fs.len()).copy_from_slice(&fs);
        written = tris.len() as u32;
        TH_OK
    });
    written
}
