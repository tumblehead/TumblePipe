//! The C ABI the shim calls. `cpp/th_delegate_core.h` declares the same
//! structs and functions; keep the two in step.

use crate::math::{Mat4, Vec3};
use crate::composite::Shading;
use crate::lights::{LightShape, SceneLight, SceneLights};
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
    /// One minus the surface's opacity (`raster::Mesh::opacity`), so that
    /// a zeroed view is opaque: 0 opaque, drawn as before; between 0 and 1
    /// see-through, blended over the opaque surfaces; 1 or more not drawn.
    pub transparency: f32,
    /// The side of the triangles not drawn (`raster::Cull`): 0 neither, 1
    /// the back, 2 the front. The shim resolves Hydra's cull style (the
    /// prim's, else the pass's; the "unless double-sided" styles by the
    /// prim's doubleSided) to one of these.
    pub cull: u32,
}

/// A view's opacity from its transparency, in [0, 1]; NaN opaque. With
/// `opaque` (the frame's [`ThFrame::opaque`], th:transparency "off") every
/// view is opaque, whatever its transparency.
pub(crate) fn opacity_of(transparency: f32, opaque: bool) -> f32 {
    if opaque || transparency.is_nan() { 1.0 } else { (1.0 - transparency).clamp(0.0, 1.0) }
}

/// Borrowed geometry of one basis curves or points prim and every instance
/// of it ([`crate::curves`]). Drawn as camera-facing ribbons and discs made
/// for each frame, so nothing of it is kept on the card.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct ThCurvesView {
    /// `point_count` xyz triples, object space: the curves' vertices, or
    /// the points.
    pub points: *const f32,
    pub point_count: u32,
    /// Vertices per curve, `curve_count` of them (ignored for points).
    pub curve_vertex_counts: *const i32,
    pub curve_count: u32,
    /// 0 points, 1 linear curves, 2 cubic curves.
    pub kind: u32,
    /// Of cubic curves: 0 bezier, 1 bspline, 2 catmullRom.
    pub basis: u32,
    /// 0 nonperiodic, 1 periodic, 2 pinned.
    pub wrap: u32,
    /// Optional, `width_count` widths (object space), spread by
    /// `width_interp`: 0 constant, 1 uniform (per curve), 2 varying, 3
    /// vertex. None, or a count that does not fit: as thin as
    /// `curves::MIN_CURVE_PIXELS` / `curves::MIN_POINT_PIXELS`.
    pub widths: *const f32,
    pub width_count: u32,
    pub width_interp: u32,
    /// Optional, `color_count` linear rgb triples, spread as the widths.
    pub colors: *const f32,
    pub color_count: u32,
    pub color_interp: u32,
    /// Albedo where there are no colours.
    pub base_color: [f32; 3],
    /// As [`ThMeshView`]'s: one object-to-world matrix per instance.
    pub transforms: *const f64,
    pub transform_count: u32,
    /// With motion blur, optional: the points at the shutter's closing.
    pub points_close: *const f32,
    /// With motion blur, optional: as [`ThMeshView::transform_keys`].
    pub transform_keys: *const f64,
    pub transform_key_count: u32,
}

/// What a render delegate keeps between frames: its meshes' cusp and
/// smooth normals ([`crate::normals`]), the CPU path's render buffers and
/// transformed vertices ([`raster::Scratch`]) and, on the GPU path, its
/// meshes' geometry and its textures on the card ([`crate::gpu::Cache`]).
/// One per delegate, freed with it, so a closed render session gives its
/// memory back.
#[derive(Default)]
pub struct ThCache {
    /// Normals of keyed meshes that have none of their own ([`crate::normals`]).
    pub(crate) normals: std::sync::Mutex<crate::normals::Cache>,
    pub(crate) cpu: std::sync::Mutex<raster::Scratch>,
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
    /// Nonzero: the GPU path where it works (built with the `gpu` feature,
    /// a usable adapter), else the CPU one. The delegate decides (th:gpu,
    /// TH_PLAYBLAST_GPU, else on in Houdini's viewer).
    pub gpu: u32,
    /// Nonzero: `depth` out as distance along the camera's view axis
    /// ([`linear_depth`]), `f32::MAX` where nothing is drawn, as Houdini's
    /// viewer reads it (UsdRenderers.json `"depthstyle": "linear"`); 0,
    /// window depth in [0, 1], 1 for nothing (husk, the farm).
    pub linear_depth: u32,
    /// The scene's lights (`light_count`, may be null when 0): the frame
    /// is lit by them (`crate::lights`) unless `lighting` is 1 or none is
    /// a light this core knows, then by the fixed camera rig.
    pub lights: *const ThLight,
    pub light_count: u32,
    /// 0: the scene's lights when it has any; 1: the headlight (the fixed
    /// camera rig) always (`th:lighting`).
    pub lighting: u32,
    /// Nonzero: every mesh drawn opaque, its transparency (opacity,
    /// transmission, display opacity) ignored (`th:transparency` "off");
    /// 0: see-through meshes blended over the opaque ones, as before.
    pub opaque: u32,
}

/// A UsdLux light as authored (`crate::lights::SceneLight`), the shim's
/// enabled, visible light sprims.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct ThLight {
    /// Light to world, USD row-vector layout.
    pub transform: [f64; 16],
    /// 0 distant, 1 sphere, 2 rect, 3 disk, 4 cylinder, 5 dome; others
    /// are left out.
    pub kind: u32,
    /// Nonzero: inputs:normalize.
    pub normalize: u32,
    /// Nonzero: a sphere light's treatAsPoint.
    pub point: u32,
    /// inputs:color, linear.
    pub color: [f32; 3],
    /// inputs:intensity x 2^inputs:exposure x inputs:diffuse.
    pub intensity: f32,
    /// inputs:colorTemperature in kelvin when enableColorTemperature is
    /// on, else 0.
    pub temperature: f32,
    /// inputs:angle (distant, degrees), radius, width, height, length.
    pub angle: f32,
    pub radius: f32,
    pub width: f32,
    pub height: f32,
    pub length: f32,
}

/// The frame's scene lights (`crate::lights::SceneLights`): none (so the
/// fixed rig) when `f` asks for the headlight or has no light of a known
/// kind.
pub(crate) unsafe fn scene_lights(f: &ThFrame) -> SceneLights {
    if f.lighting == 1 || f.lights.is_null() || f.light_count == 0 {
        return SceneLights::NONE;
    }
    let lights: Vec<SceneLight> = slice::from_raw_parts(f.lights, f.light_count as usize)
        .iter()
        .filter_map(|l| {
            let shape = LightShape::from_abi(l.kind)?;
            let tint = if l.temperature > 0.0 { crate::lights::blackbody(l.temperature) } else { [1.0; 3] };
            let radiance = [0, 1, 2].map(|c| l.color[c] * tint[c] * l.intensity);
            Some(SceneLight::placed(shape, radiance, l.normalize != 0, l.point != 0, l.angle,
                                    [l.radius, l.width, l.height, l.length], &l.transform))
        })
        .collect();
    SceneLights::new(&lights)
}

/// Window depth (0 at the near plane, 1 at the far one or where nothing is
/// drawn) to distance along the camera's view axis, through `f`'s
/// projection (view-to-world times world-to-clip, row vectors): a view-space
/// point (0, 0, z) lands at clip z = z p22 + p32, w = z p23 + p33, and
/// ndc = clip z / w solved for z. Nothing drawn is `f32::MAX`.
///
/// Houdini 22's viewer decodes `"depthstyle": "opengl"` depth as near / d,
/// not through the projection it hands the delegate (near 1, far at
/// infinity): every surface read as about one unit away, and its Set Pivot
/// tumble orbited the camera. `"linear"` (Karma's) leaves nothing to decode.
pub(crate) fn linear_depth(f: &ThFrame) -> impl Fn(f32) -> f32 {
    let p = |r: usize, c: usize| (0..4).map(|k| f.view_to_world[r * 4 + k] * f.world_to_clip[k * 4 + c]).sum::<f64>();
    let (p22, p32, p23, p33) = (p(2, 2), p(3, 2), p(2, 3), p(3, 3));
    move |d: f32| {
        if d >= 1.0 || d.is_nan() {
            return f32::MAX;
        }
        let ndc = 2.0 * d as f64 - 1.0;
        (-(p32 - ndc * p33) / (ndc * p23 - p22)) as f32
    }
}

/// The GPU path when `gpu` asks for it and it works, else the CPU one. A GPU
/// failure is reported (once) and the frame drawn on the CPU instead, so a
/// worker without a usable GPU still finishes the playblast.
/// `ids`: the caller wants the picking ids (the CPU path always makes
/// them; the GPU path only when asked, with an extra pass). `smooth`: the
/// meshes' kept smooth normals, by mesh, for the CPU path.
fn render(frame: &Frame, meshes: &[Mesh], smooth: &[Option<&[Vec3]>], cache: Option<&ThCache>, ids: bool,
          gpu: bool) -> raster::Image {
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
    if gpu {
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
    let _ = (ids, gpu);
    match cache {
        Some(c) => raster::render_kept(frame, meshes, smooth, &mut c.cpu.lock().unwrap_or_else(|e| e.into_inner())),
        None => raster::render_kept(frame, meshes, smooth, &mut raster::Scratch::default()),
    }
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
    render_into(frame, meshes, mesh_count, std::ptr::null(), 0, color, depth, std::ptr::null_mut(), std::ptr::null(), cache)
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
    render_into(frame, meshes, mesh_count, std::ptr::null(), 0, color, depth, ids, cancel, cache)
}

/// [`th_render_ids`] with basis curves and points as well ([`ThCurvesView`],
/// [`crate::curves`]): camera-facing ribbons and discs made for this frame.
/// In `ids`, curves view `j` is `mesh_count + j`, with the instance as for a
/// mesh.
///
/// # Safety
/// As [`th_render_ids`]; every pointer in `curves` valid for the counts it
/// is given with.
#[no_mangle]
pub unsafe extern "C" fn th_render_scene(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    curves: *const ThCurvesView,
    curves_count: u32,
    color: *mut f32,
    depth: *mut f32,
    ids: *mut i32,
    cancel: *const AtomicU32,
    cache: *const ThCache,
) -> i32 {
    render_into(frame, meshes, mesh_count, curves, curves_count, color, depth, ids, cancel, cache)
}

/// One instance of a curves view, made for a frame: the geometry, its
/// closing (with motion blur), and the view and instance it came from.
struct MadeStrokes {
    view: usize,
    instance: usize,
    base_color: Vec3,
    made: crate::curves::Made,
    close: Option<Vec<Vec3>>,
}

/// The geometry of `views` through `frame` ([`crate::curves::expand`]),
/// one per instance, in world space. With motion blur, each instance's
/// geometry at the shutter's opening (its points through its first
/// transform key) and closing (its closing points through its last), cut
/// alike so the two line up.
unsafe fn make_strokes(frame: &Frame, views: &[ThCurvesView]) -> Vec<MadeStrokes> {
    use crate::curves::{expand, Camera, Interp, Kind, Strokes, Wrap};
    let camera = Camera::of(frame);
    let moving = frame.blur.motion && frame.blur.samples > 1;
    let mut out = Vec::new();
    for (j, v) in views.iter().enumerate() {
        if v.points.is_null() || v.transforms.is_null() || v.point_count == 0 || v.transform_count == 0 {
            continue;
        }
        let kind = Kind::from_abi(v.kind, v.basis);
        let n = v.point_count as usize;
        let points = slice::from_raw_parts(v.points as *const Vec3, n);
        let counts = int_slice(v.curve_vertex_counts, v.curve_count);
        if kind != Kind::Points && counts.is_empty() {
            continue;
        }
        let strokes = Strokes {
            kind,
            wrap: Wrap::from_abi(v.wrap),
            points,
            counts,
            widths: if v.widths.is_null() { &[] } else { slice::from_raw_parts(v.widths, v.width_count as usize) },
            width_interp: Interp::from_abi(v.width_interp),
            colors: opt_slice(v.colors, v.color_count as usize).unwrap_or(&[]),
            color_interp: Interp::from_abi(v.color_interp),
        };
        let tc = v.transform_count as usize;
        let transforms = slice::from_raw_parts(v.transforms as *const Mat4, tc);
        let kc = v.transform_key_count as usize;
        let keys: &[Mat4] = if moving && !v.transform_keys.is_null() && (2..=blur::MAX_KEYS).contains(&kc) {
            slice::from_raw_parts(v.transform_keys as *const Mat4, kc * tc)
        } else {
            &[]
        };
        let close_points = if moving { opt_slice::<Vec3>(v.points_close, n) } else { None };
        for (i, transform) in transforms.iter().enumerate() {
            let (open_m, close_m) = if keys.is_empty() { (transform, transform) } else { (&keys[i], &keys[(kc - 1) * tc + i]) };
            let (made, plan) = expand(&strokes, points, open_m, &camera, None);
            if made.triangles.is_empty() {
                continue;
            }
            let close = (close_points.is_some() || !keys.is_empty()).then(|| {
                expand(&strokes, close_points.unwrap_or(points), close_m, &camera, Some(&plan)).0.points
            }).filter(|c| c.len() == made.points.len());
            out.push(MadeStrokes { view: j, instance: i, base_color: v.base_color, made, close });
        }
    }
    out
}

#[allow(clippy::too_many_arguments)]
unsafe fn render_into(
    frame: *const ThFrame,
    meshes: *const ThMeshView,
    mesh_count: u32,
    curves: *const ThCurvesView,
    curves_count: u32,
    color: *mut f32,
    depth: *mut f32,
    ids: *mut i32,
    cancel: *const AtomicU32,
    cache: *const ThCache,
) -> i32 {
    ffi_guard(|| {
        let _scope = crate::cancel::Scope::new(cancel);
        let cancelled = || !cancel.is_null() && (*cancel).load(Ordering::Relaxed) != 0;
        if frame.is_null() || color.is_null() || (meshes.is_null() && mesh_count > 0)
            || (curves.is_null() && curves_count > 0) {
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
            .map(|v| v.corner_normals.is_null().then(|| crate::normals::Request {
                key: (v.key != 0).then_some(MeshKey { id: v.key, version: v.version }),
                points: points(v),
                triangles: triangles(v),
                smooth: v.polygonal == 0,
            }))
            .collect();
        let (normals, made) = {
            let mut own = crate::normals::Cache::default();
            let mut kept = cache.as_ref().map(|c| c.normals.lock().unwrap_or_else(|e| e.into_inner()));
            kept.as_deref_mut().unwrap_or(&mut own).normals(&requests)
        };
        if std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
            eprintln!("hdThPlayblast: cusp and smooth normals {} ms ({} meshes worked out, {} kept)",
                      started.elapsed().as_millis(), made.computed, made.kept);
        }
        // Cusp normals go in as the mesh's corner normals; smooth ones, per
        // point, alongside.
        let mut smooth: Vec<Option<&[Vec3]>> = views
            .iter()
            .zip(&normals)
            .map(|(v, n)| n.as_deref().filter(|n| v.polygonal == 0 && n.len() == v.point_count as usize))
            .collect();
        let render_frame = Frame {
            width: f.width as usize,
            height: f.height as usize,
            world_to_clip: f.world_to_clip,
            view_to_world: f.view_to_world,
            samples: f.samples.max(1) as usize,
            shading: Shading::from_abi(f.shading),
            ao: f.ao != 0,
            blur: blur_of(f),
            alpha: f.alpha != 0,
            lights: scene_lights(f),
        };
        // Curves and points, made for this frame: after the meshes, each
        // instance a mesh of its own in world space.
        let curve_views = if curves_count > 0 { slice::from_raw_parts(curves, curves_count as usize) } else { &[] };
        let started = std::time::Instant::now();
        let strokes = make_strokes(&render_frame, curve_views);
        if !curve_views.is_empty() && std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
            eprintln!("hdThPlayblast: curves and points {} ms ({} views, {} triangles)", started.elapsed().as_millis(),
                      curve_views.len(), strokes.iter().map(|s| s.made.triangles.len()).sum::<usize>());
        }
        let mut list = Vec::with_capacity(views.len() + strokes.len());
        for (v, n) in views.iter().zip(&normals) {
            let tris = v.triangle_count as usize;
            let tiles = if v.tiles.is_null() { &[][..] } else { slice::from_raw_parts(v.tiles, v.tile_count as usize) };
            list.push(Mesh {
                points: points(v),
                triangles: triangles(v),
                corner_normals: opt_slice(v.corner_normals, tris * 3)
                    .or(n.as_deref().filter(|n| v.polygonal != 0 && !n.is_empty())),
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
                opacity: opacity_of(v.transparency, f.opaque != 0),
                cull: raster::Cull::from_abi(v.cull),
            });
        }
        for s in &strokes {
            list.push(crate::curves::mesh(&s.made, s.close.as_deref(), s.base_color));
            smooth.push(None);
        }
        let image = render(
            &render_frame,
            &list,
            &smooth,
            cache.as_ref(),
            !ids.is_null(),
            f.gpu != 0,
        );
        if cancelled() {
            return TH_CANCELLED;
        }
        let n = f.width as usize * f.height as usize;
        slice::from_raw_parts_mut(color as *mut [f32; 4], n).copy_from_slice(&image.color);
        if !depth.is_null() {
            let out = slice::from_raw_parts_mut(depth, n);
            if f.linear_depth != 0 {
                let linear = linear_depth(f);
                for (o, &d) in out.iter_mut().zip(&image.depth) {
                    *o = linear(d);
                }
            } else {
                out.copy_from_slice(&image.depth);
            }
        }
        if !ids.is_null() {
            let out = slice::from_raw_parts_mut(ids as *mut [i32; 2], n);
            if image.ids.len() == n {
                let meshes_drawn = kept.len();
                for (o, &[m, i]) in out.iter_mut().zip(&image.ids) {
                    *o = if m < 0 {
                        [-1, -1]
                    } else if (m as usize) < meshes_drawn {
                        [kept[m as usize], i]
                    } else {
                        let s = &strokes[m as usize - meshes_drawn];
                        [(mesh_count as usize + s.view) as i32, s.instance as i32]
                    };
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
            focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0,
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
            focal_length: 0.0, f_stop: 0.0, focus_distance: 0.0, alpha: 0, gpu: 0, linear_depth: 0,
            lights: std::ptr::null(), light_count: 0, lighting: 0, opaque: 0,
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

/// The normals the core draws a mesh with when it has none of its own, one
/// per triangle corner into `corner_normals` (`9 * triangle_count` floats):
/// cusp normals ([`raster::cusp_normals`]) for a `polygonal` one, else its
/// smooth point normals ([`raster::smooth_normals`]) at each corner; +z at
/// a corner past the points. The shim draws a mesh whose faces have
/// materials of their own (GeomSubsets) as one view per material, and hands
/// each the whole mesh's normals from here, so its parts meet without a
/// crease: worked out per part, each would be smoothed only over its own
/// faces. Returns 0, or 1 for a null pointer.
///
/// # Safety
/// `points` valid for `point_count` xyz triples, `triangles` for
/// `3 * triangle_count` indices, `corner_normals` for `9 * triangle_count`
/// writes.
#[no_mangle]
pub unsafe extern "C" fn th_mesh_normals(
    points: *const f32,
    point_count: u32,
    triangles: *const u32,
    triangle_count: u32,
    polygonal: u32,
    corner_normals: *mut f32,
) -> i32 {
    ffi_guard(|| {
        if (points.is_null() && point_count > 0) || (triangles.is_null() && triangle_count > 0)
            || (corner_normals.is_null() && triangle_count > 0) {
            return TH_ERR_ARGS;
        }
        if triangle_count == 0 {
            return TH_OK;
        }
        let pts: &[Vec3] = if point_count == 0 { &[] } else { slice::from_raw_parts(points as *const Vec3, point_count as usize) };
        let tris = slice::from_raw_parts(triangles as *const [u32; 3], triangle_count as usize);
        let out = slice::from_raw_parts_mut(corner_normals as *mut Vec3, tris.len() * 3);
        out.copy_from_slice(&mesh_normals(pts, tris, polygonal != 0));
        TH_OK
    })
}

/// [`th_mesh_normals`]'s normals, one per corner.
pub(crate) fn mesh_normals(points: &[Vec3], triangles: &[[u32; 3]], polygonal: bool) -> Vec<Vec3> {
    if polygonal {
        return raster::cusp_normals(points, triangles, raster::CUSP_DEGREES);
    }
    let smooth = raster::smooth_normals(points, triangles);
    triangles.iter().flat_map(|t| t.map(|i| smooth.get(i as usize).copied().unwrap_or([0.0, 0.0, 1.0]))).collect()
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
