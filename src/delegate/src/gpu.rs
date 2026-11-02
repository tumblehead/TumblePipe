//! The GPU path: the same picture as [`crate::raster`] and
//! [`crate::composite`], made with wgpu (Vulkan, DX12 or Metal, headless: no
//! window, GL context or display).
//!
//! Opt-in (`TH_PLAYBLAST_GPU=1`, see [`crate::ffi`]); any failure falls back
//! to the CPU path, which stays the reference: `gpu.wgsl` fills the same
//! render buffer as `raster.rs` does, `post.wgsl` makes the picture from it
//! as `composite.rs` does, and the tests below compare the two.
//!
//! Work reaches the GPU in bounded pieces. Each mesh's corners go up in
//! chunks of [`CHUNK_TRIANGLES`], and a submission carries at most
//! [`SUBMIT_TRIANGLES`] drawn triangles (instances included) and
//! [`SUBMIT_BYTES`] of new vertex data, then waits for the GPU. One huge
//! submission is what a driver watchdog (Windows TDR) resets the device for,
//! and that takes the display down with it.
//!
//! Each sample of a pixel ([`composite::sample_offsets`]) is a layer of the
//! render targets, drawn by its own pass with the picture shifted so that
//! the sample lands on the pixel centre. The geometry goes up once and is
//! drawn by every pass. The post pass then works through the layers on the
//! card, and only the finished image and its depth are read back.
//!
//! With a [`Cache`] (the render delegate's), a mesh's corners stay on the
//! card from frame to frame while its [`MeshKey`](crate::raster::MeshKey)
//! and data stay the same, so a shot uploads only the meshes Hydra rebuilt.
//!
//! A blurred frame ([`crate::blur`]) is drawn chunk by chunk, each chunk's
//! layers through their own cameras: every layer then has its own instance
//! transforms (at its moment of the shutter, through its camera), and a mesh
//! whose points move is drawn by a second pipeline that also reads its
//! closing positions and moves each corner to the layer's moment. Only those
//! closing positions go up each frame; a mesh's corners stay cached as
//! before. The chunks' pictures stay on the card and are averaged there
//! (`sum.wgsl`) in the order and with the operations the CPU path uses
//! ([`blur::average`]), so the average is the CPU's bit for bit; only it is
//! read back.

use crate::blur::{self, LayerCamera};
use crate::composite::{self, View};
#[cfg(test)]
use crate::composite::{Samples, Shading};
use crate::math::{self, Mat4, Vec3};
use crate::raster::{self, Frame, Image, Mesh};
use crate::texture::Texture;
use std::collections::HashMap;
use std::sync::{mpsc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use wgpu::util::DeviceExt;

const CHUNK_TRIANGLES: usize = 1 << 20;
const SUBMIT_TRIANGLES: u64 = 32_000_000;
const SUBMIT_BYTES: usize = 256 << 20;
/// Corners a [`Cache`] keeps on the card at most; a mesh past it goes up
/// each frame, as it would without a cache.
const CACHE_BYTES: usize = 3 << 30;
/// The render buffer's targets: albedo with the sample's depth again in its
/// alpha, which the post pass can read (a depth attachment can be neither
/// read by a compute pass nor written by a test), and normal plus distance.
/// Each is 16 bytes a sample, the most two targets may share (32).
const COLOR_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba32Float;
const DEPTH_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Depth32Float;
/// `post.wgsl`'s workgroups are 8 x 8 pixels.
const GROUP: u32 = 8;

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Vertex {
    pos: [f32; 3],
    normal: [f32; 3],
    color: [f32; 3],
    uv: [f32; 2],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Instance {
    rows: [[f32; 4]; 4],
    normal: [[f32; 4]; 3],
    /// Object to clip, combined in f64 as the CPU path does: composing it
    /// in the shader in f32 decides near-coplanar surfaces (a curtain on a
    /// wall) differently from the CPU.
    clip: [[f32; 4]; 4],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Globals {
    clip: [[f32; 4]; 4],
    eye: [f32; 4],
    right: [f32; 4],
    up: [f32; 4],
    back: [f32; 4],
    jitter: [f32; 4],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct MeshParams {
    base: [f32; 4],
    mode: [i32; 4],
    layers: [[i32; 4]; 25],
}

/// `post.wgsl`'s `Globals`, field for field.
#[repr(C)]
#[derive(Clone, Copy)]
struct PostGlobals {
    size: [u32; 4],
    mode: [u32; 4],
    scale: [f32; 4],
    proj: [f32; 4],
    proj_w: [f32; 4],
    tiers01: [f32; 4],
    tier2: [f32; 4],
    ao: [f32; 4],
    lights: [[f32; 4]; 3],
    ambient: [f32; 4],
    clay: [f32; 4],
    backdrop: [f32; 4],
    pattern: [[u32; 4]; 4],
}

/// `post.wgsl`'s `Layer`.
#[repr(C)]
#[derive(Clone, Copy)]
struct PostLayer {
    offset: [f32; 4],
    index: [u32; 4],
}

/// The bytes of plain-old-data `repr(C)` values (f32 / i32 / u32 fields
/// only, so no padding).
fn bytes<T: Copy>(values: &[T]) -> &[u8] {
    unsafe { std::slice::from_raw_parts(values.as_ptr() as *const u8, std::mem::size_of_val(values)) }
}

fn align(n: usize, to: usize) -> usize {
    n.div_ceil(to) * to
}

/// What a render delegate keeps on the card between frames: its meshes'
/// corners and its texture arrays. Whatever a frame did not draw is let go
/// at its end.
#[derive(Default)]
pub struct Cache {
    inner: Mutex<CacheInner>,
}

#[derive(Default)]
struct CacheInner {
    /// By the mesh's [`crate::raster::MeshKey::id`].
    meshes: HashMap<u64, CachedMesh>,
    /// By (tile, [`Texture::id`]) of each layer.
    textures: HashMap<Vec<(i32, u64)>, CachedTexture>,
    /// Bytes of corners in `meshes`.
    bytes: usize,
    /// Frames drawn with this cache.
    frame: u64,
}

struct CachedMesh {
    signature: Signature,
    chunks: Vec<Chunk>,
    bytes: usize,
    frame: u64,
}

struct CachedTexture {
    view: wgpu::TextureView,
    frame: u64,
}

/// A vertex buffer of a mesh's corners.
struct Chunk {
    buffer: wgpu::Buffer,
    vertices: u32,
    /// The mesh triangle it starts at.
    start: usize,
}

/// Everything a mesh's corners are made from. Its version changes when the
/// shim rebuilds it; the data's addresses, sizes and base colour change when
/// its material switches between colours and textures without a rebuild.
/// Not the normals' address: the cusp normals are computed anew each frame
/// (from points and triangles, which the version covers).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Signature {
    version: u64,
    points: (usize, usize),
    triangles: (usize, usize),
    normals: bool,
    colors: usize,
    uvs: usize,
    base: [u32; 3],
}

impl Signature {
    fn of(mesh: &Mesh) -> Signature {
        Signature {
            version: mesh.key.map_or(0, |k| k.version),
            points: (mesh.points.as_ptr() as usize, mesh.points.len()),
            triangles: (mesh.triangles.as_ptr() as usize, mesh.triangles.len()),
            normals: mesh.corner_normals.is_some(),
            colors: mesh.corner_colors.map_or(0, |c| c.as_ptr() as usize),
            uvs: mesh.corner_uvs.map_or(0, |u| u.as_ptr() as usize),
            base: mesh.base_color.map(f32::to_bits),
        }
    }
}

/// What a frame did, for TH_PLAYBLAST_TIMING and the tests.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Stats {
    /// Meshes whose corners went up this frame, and their bytes.
    pub uploaded_meshes: usize,
    pub uploaded_bytes: usize,
    /// Meshes drawn from corners already on the card.
    pub cached_meshes: usize,
    /// Bytes of corners the cache holds after the frame.
    pub resident_bytes: usize,
    /// Building and queueing corners; drawing (submit and wait); the post
    /// pass; reading the image back.
    pub upload: Duration,
    pub raster: Duration,
    pub post: Duration,
    pub read_back: Duration,
    /// Chunks of a blurred frame (0 unblurred).
    pub chunks: usize,
}

impl Stats {
    /// A blurred frame's chunks summed (the cache's size is the last's).
    fn add(&mut self, o: &Stats) {
        self.uploaded_meshes += o.uploaded_meshes;
        self.uploaded_bytes += o.uploaded_bytes;
        self.cached_meshes += o.cached_meshes;
        self.resident_bytes = o.resident_bytes;
        self.upload += o.upload;
        self.raster += o.raster;
        self.post += o.post;
        self.read_back += o.read_back;
    }
}

struct PostPipelines {
    distances: wgpu::ComputePipeline,
    occlude: wgpu::ComputePipeline,
    blur_across: wgpu::ComputePipeline,
    blur_down: wgpu::ComputePipeline,
    accumulate: wgpu::ComputePipeline,
    finish: wgpu::ComputePipeline,
}

/// `sum.wgsl`: a blurred frame's chunk pictures added pairwise, and the sum
/// made the average.
struct SumPipelines {
    add: wgpu::ComputePipeline,
    scale: wgpu::ComputePipeline,
}

struct Gpu {
    device: wgpu::Device,
    queue: wgpu::Queue,
    scene: wgpu::RenderPipeline,
    /// The same, for a blurred frame's meshes whose points move: a third
    /// vertex buffer holds each corner's closing position.
    scene_moving: wgpu::RenderPipeline,
    globals_layout: wgpu::BindGroupLayout,
    mesh_layout: wgpu::BindGroupLayout,
    post_layout: wgpu::BindGroupLayout,
    post: PostPipelines,
    sum_layout: wgpu::BindGroupLayout,
    sum: SumPipelines,
    sampler: wgpu::Sampler,
    limits: wgpu::Limits,
    /// The last frame's targets, kept while the size and sample count do.
    targets: Option<Targets>,
}

static GPU: OnceLock<Result<Mutex<Gpu>, String>> = OnceLock::new();

fn gpu() -> Result<std::sync::MutexGuard<'static, Gpu>, String> {
    let gpu = GPU.get_or_init(|| Gpu::new().map(Mutex::new)).as_ref().map_err(Clone::clone)?;
    gpu.lock().map_err(|_| "an earlier GPU frame panicked".to_string())
}

/// Render `meshes` on the GPU, keeping their geometry in `cache` for the
/// next frame. The device is opened on first use and kept for the process
/// (husk renders every frame through the same delegate).
pub fn render(frame: &Frame, meshes: &[Mesh], cache: Option<&Cache>) -> Result<Image, String> {
    let (image, stats) = render_with_stats(frame, meshes, cache)?;
    if std::env::var_os("TH_PLAYBLAST_TIMING").is_some() {
        let mb = |b: usize| b as f64 / (1 << 20) as f64;
        eprintln!(
            "hdThPlayblast: GPU upload {} ms ({} meshes, {:.0} MB; {} kept on the card, {:.0} MB there), \
             raster {} ms at {} samples a pixel{}, post pass {} ms, read back {} ms",
            stats.upload.as_millis(), stats.uploaded_meshes, mb(stats.uploaded_bytes), stats.cached_meshes,
            mb(stats.resident_bytes), stats.raster.as_millis(), composite::sample_offsets(frame.samples).len(),
            if stats.chunks > 0 { format!(" in {} chunks", stats.chunks) } else { String::new() },
            stats.post.as_millis(), stats.read_back.as_millis()
        );
    }
    Ok(image)
}

pub(crate) fn render_with_stats(frame: &Frame, meshes: &[Mesh], cache: Option<&Cache>)
                                -> Result<(Image, Stats), String> {
    gpu()?.scoped(frame, |gpu| {
        if !blur::active(frame, meshes) {
            let mut stats = gpu.draw(frame, meshes, cache, None)?;
            let image = gpu.read_back(&mut stats)?;
            return Ok((image, stats));
        }
        let plan = blur::Plan::new(frame, composite::sample_offsets(frame.samples).len());
        let mut sum = Vec::new();
        let mut total = Stats::default();
        for chunk in &plan.chunks {
            let stats = gpu.draw(frame, meshes, cache, Some(chunk))?;
            gpu.sum_push(&mut sum);
            total.add(&stats);
        }
        total.chunks = plan.chunks.len();
        let image = gpu.sum_finish(sum, plan.chunks.len(), &mut total)?;
        Ok((image, total))
    })
}

/// The GPU post pass alone, on a render buffer made elsewhere: what the
/// tests hold against [`composite::resolve`].
#[cfg(test)]
pub(crate) fn resolve(frame: &Frame, samples: &Samples) -> Result<Image, String> {
    gpu()?.scoped(frame, |gpu| {
        let mut stats = Stats::default();
        gpu.upload_samples(samples)?;
        gpu.post(frame, &mut stats)
    })
}

struct MeshResources {
    instances: wgpu::Buffer,
    instance_count: u32,
    /// Instances between one layer's and the next's in `instances`: 0 when
    /// every layer draws the same (unblurred), else `instance_count`.
    layer_stride: u32,
    bind: wgpu::BindGroup,
}

/// Where a draw's corners are: a cached mesh's chunk, or a chunk drawn this
/// frame only.
#[derive(Clone, Copy)]
enum Corners {
    Cached { id: u64, chunk: usize },
    Transient(usize),
}

/// Drawn work waiting for a submission.
struct Draw {
    mesh: usize,
    corners: Corners,
    vertices: u32,
    instances: std::ops::Range<u32>,
    /// The corners' closing positions in the batch's `motion`, when the
    /// mesh's points move: drawn by the moving pipeline.
    motion: Option<usize>,
}

/// The render targets for one size and sample count, and the post pass's
/// buffers.
struct Targets {
    size: (usize, usize, usize),
    /// Per layer: albedo, normal and depth attachment views.
    layer_views: Vec<[wgpu::TextureView; 3]>,
    #[cfg(test)]
    albedo: wgpu::Texture,
    #[cfg(test)]
    normal: wgpu::Texture,
    globals: wgpu::Buffer,
    /// Bytes between layers in the layers' uniform buffer (the dynamic
    /// offset alignment).
    layer_stride: usize,
    color: wgpu::Buffer,
    depth: wgpu::Buffer,
    read_color: wgpu::Buffer,
    read_depth: wgpu::Buffer,
    bind: wgpu::BindGroup,
    /// `sum.wgsl`'s pixel count and one over the chunk count.
    sum_params: wgpu::Buffer,
    /// A blurred frame's partial sums of chunk pictures, colour and depth
    /// (one more than the chunk count's bits, at most), made as needed.
    slots: Vec<[wgpu::Buffer; 2]>,
}

impl Targets {
    /// A pass drawing into layer `k`, clearing it first or not.
    fn pass<'e>(&self, encoder: &'e mut wgpu::CommandEncoder, clear: bool, k: usize) -> wgpu::RenderPass<'e> {
        let attach = |view, clear_to: wgpu::Color| Some(wgpu::RenderPassColorAttachment {
            view,
            resolve_target: None,
            ops: wgpu::Operations {
                load: if clear { wgpu::LoadOp::Clear(clear_to) } else { wgpu::LoadOp::Load },
                store: wgpu::StoreOp::Store,
            },
        });
        let [albedo, normal, depth] = &self.layer_views[k];
        // Nothing drawn: no colour at depth 1, no normal at distance 0.
        let far = wgpu::Color { r: 0.0, g: 0.0, b: 0.0, a: 1.0 };
        encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("frame"),
            color_attachments: &[attach(albedo, far), attach(normal, wgpu::Color::TRANSPARENT)],
            depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                view: depth,
                depth_ops: Some(wgpu::Operations {
                    load: if clear { wgpu::LoadOp::Clear(1.0) } else { wgpu::LoadOp::Load },
                    store: wgpu::StoreOp::Store,
                }),
                stencil_ops: None,
            }),
            timestamp_writes: None,
            occlusion_query_set: None,
        })
    }
}

/// Queued draws and this frame's own vertex buffers, submitted in pieces
/// ([`SUBMIT_TRIANGLES`], [`SUBMIT_BYTES`]).
struct Batch<'a> {
    gpu: &'a Gpu,
    targets: &'a Targets,
    globals: &'a [wgpu::BindGroup],
    resources: &'a [Option<MeshResources>],
    transient: Vec<Chunk>,
    /// This submission's closing positions of moving corners.
    motion: Vec<wgpu::Buffer>,
    draws: Vec<Draw>,
    pending_bytes: usize,
    pending_triangles: u64,
    elapsed: Duration,
}

impl Batch<'_> {
    /// Queue every instance of mesh `mesh` drawn from `corners`, in runs that
    /// keep a submission under the budget.
    fn draw(&mut self, cache: &CacheInner, mesh: usize, corners: Corners, vertices: u32, motion: Option<usize>) {
        let res = self.resources[mesh].as_ref().expect("drawn meshes have resources");
        let tris = (vertices / 3).max(1) as u64;
        let per_run = (SUBMIT_TRIANGLES / tris).clamp(1, res.instance_count as u64) as u32;
        let mut first = 0;
        while first < res.instance_count {
            let last = (first + per_run).min(res.instance_count);
            self.draws.push(Draw { mesh, corners, vertices, instances: first..last, motion });
            self.pending_triangles += tris * (last - first) as u64 * self.globals.len() as u64;
            first = last;
            if self.pending_triangles >= SUBMIT_TRIANGLES {
                self.flush(cache);
            }
        }
        if self.draws.is_empty() {
            self.transient.clear(); // all drawn
            self.motion.clear();
        }
    }

    /// Count `bytes` of corners just queued for upload; past the budget,
    /// submit everything so far.
    fn uploaded(&mut self, cache: &CacheInner, bytes: usize) {
        self.pending_bytes += bytes;
        if self.pending_bytes >= SUBMIT_BYTES {
            self.flush(cache);
            self.transient.clear();
            self.motion.clear();
        }
    }

    /// Submit the queued draws (and the uploads before them) and wait.
    fn flush(&mut self, cache: &CacheInner) {
        let started = Instant::now();
        let mut encoder = self.gpu.device.create_command_encoder(&Default::default());
        if !self.draws.is_empty() {
            for (k, globals) in self.globals.iter().enumerate() {
                let mut p = self.targets.pass(&mut encoder, false, k);
                p.set_pipeline(&self.gpu.scene);
                p.set_bind_group(0, globals, &[]);
                let mut moving = false;
                for d in &self.draws {
                    let r = self.resources[d.mesh].as_ref().expect("drawn meshes have resources");
                    let buffer = match d.corners {
                        Corners::Cached { id, chunk } => &cache.meshes[&id].chunks[chunk].buffer,
                        Corners::Transient(i) => &self.transient[i].buffer,
                    };
                    if d.motion.is_some() != moving {
                        moving = d.motion.is_some();
                        p.set_pipeline(if moving { &self.gpu.scene_moving } else { &self.gpu.scene });
                    }
                    p.set_bind_group(1, &r.bind, &[]);
                    p.set_vertex_buffer(0, buffer.slice(..));
                    p.set_vertex_buffer(1, r.instances.slice(..));
                    if let Some(m) = d.motion {
                        p.set_vertex_buffer(2, self.motion[m].slice(..));
                    }
                    let base = k as u32 * r.layer_stride;
                    p.draw(0..d.vertices, base + d.instances.start..base + d.instances.end);
                }
            }
        }
        self.gpu.queue.submit([encoder.finish()]);
        self.gpu.device.poll(wgpu::Maintain::Wait);
        self.draws.clear();
        self.pending_bytes = 0;
        self.pending_triangles = 0;
        self.elapsed += started.elapsed();
    }
}

/// The corners of triangles `start..end` of `mesh` as vertices, skipping
/// triangles that index past its points. `smooth` is its smoothed point
/// normals when it has no corner normals.
fn corner_vertices(mesh: &Mesh, smooth: Option<&[Vec3]>, start: usize, end: usize) -> Vec<Vertex> {
    let mut verts: Vec<Vertex> = Vec::with_capacity((end - start) * 3);
    for t in start..end {
        let vi = mesh.triangles[t].map(|i| i as usize);
        if vi.iter().any(|&i| i >= mesh.points.len()) {
            continue;
        }
        for (k, &v) in vi.iter().enumerate() {
            let c = t * 3 + k;
            let normal: Vec3 = match (mesh.corner_normals, smooth) {
                (Some(n), _) => n[c],
                (None, Some(n)) => n[v],
                (None, None) => [0.0, 0.0, 1.0],
            };
            verts.push(Vertex {
                pos: mesh.points[v],
                normal,
                color: mesh.corner_colors.map_or(mesh.base_color, |cc| cc[c]),
                uv: mesh.corner_uvs.map_or([0.0, 0.0], |u| u[c]),
            });
        }
    }
    verts
}

/// The closing positions of the corners [`corner_vertices`] makes of
/// triangles `start..end`, in the same order.
fn closing_corners(mesh: &Mesh, close: &[Vec3], start: usize, end: usize) -> Vec<Vec3> {
    let mut out = Vec::with_capacity((end - start) * 3);
    for t in start..end {
        let vi = mesh.triangles[t].map(|i| i as usize);
        if vi.iter().any(|&i| i >= mesh.points.len()) {
            continue;
        }
        out.extend(vi.map(|v| close[v]));
    }
    out
}

/// A matrix's rows as the shaders take them.
fn rows(m: &Mat4) -> [[f32; 4]; 4] {
    let row = |r: usize| [m[r * 4] as f32, m[r * 4 + 1] as f32, m[r * 4 + 2] as f32, m[r * 4 + 3] as f32];
    [row(0), row(1), row(2), row(3)]
}

impl Gpu {
    fn new() -> Result<Gpu, String> {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Backends::PRIMARY,
            ..Default::default()
        });
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::HighPerformance,
            compatible_surface: None,
            force_fallback_adapter: false,
        }))
        .ok_or("no GPU adapter")?;
        let info = adapter.get_info();
        if info.device_type == wgpu::DeviceType::Cpu {
            return Err(format!("only a software adapter ({})", info.name));
        }
        eprintln!("hdThPlayblast: GPU {} ({:?})", info.name, info.backend);
        let limits = adapter.limits();
        let (device, queue) = pollster::block_on(adapter.request_device(
            &wgpu::DeviceDescriptor {
                label: Some("hdThPlayblast"),
                required_features: wgpu::Features::empty(),
                required_limits: limits.clone(),
                memory_hints: wgpu::MemoryHints::Performance,
            },
            None,
        ))
        .map_err(|e| e.to_string())?;
        device.on_uncaptured_error(Box::new(|e| eprintln!("hdThPlayblast: GPU error: {e}")));

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("hdThPlayblast"),
            source: wgpu::ShaderSource::Wgsl(include_str!("gpu.wgsl").into()),
        });
        let uniform = |binding, visibility, dynamic| wgpu::BindGroupLayoutEntry {
            binding,
            visibility,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: dynamic,
                min_binding_size: None,
            },
            count: None,
        };
        let globals_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("globals"),
            entries: &[uniform(0, wgpu::ShaderStages::VERTEX_FRAGMENT, false)],
        });
        let mesh_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("mesh"),
            entries: &[
                uniform(0, wgpu::ShaderStages::FRAGMENT, false),
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2Array,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });

        let target = |format| Some(wgpu::ColorTargetState { format, blend: None, write_mask: wgpu::ColorWrites::ALL });
        let color_targets = [target(COLOR_FORMAT), target(COLOR_FORMAT)];
        let primitive = wgpu::PrimitiveState {
            topology: wgpu::PrimitiveTopology::TriangleList,
            cull_mode: None,
            ..Default::default()
        };

        let vertex_attrs = wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3, 2 => Float32x3, 3 => Float32x2];
        let instance_attrs = wgpu::vertex_attr_array![
            4 => Float32x4, 5 => Float32x4, 6 => Float32x4, 7 => Float32x4,
            8 => Float32x4, 9 => Float32x4, 10 => Float32x4,
            11 => Float32x4, 12 => Float32x4, 13 => Float32x4, 14 => Float32x4
        ];
        let scene_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("scene"),
            bind_group_layouts: &[&globals_layout, &mesh_layout],
            push_constant_ranges: &[],
        });
        let closing_attrs = wgpu::vertex_attr_array![15 => Float32x3];
        let buffers = [
            wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Vertex>() as u64,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &vertex_attrs,
            },
            wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Instance>() as u64,
                step_mode: wgpu::VertexStepMode::Instance,
                attributes: &instance_attrs,
            },
            wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Vec3>() as u64,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &closing_attrs,
            },
        ];
        let scene_pipeline = |entry_point: &str, buffers: &[wgpu::VertexBufferLayout]| device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("scene"),
            layout: Some(&scene_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point,
                compilation_options: Default::default(),
                buffers,
            },
            primitive,
            depth_stencil: Some(wgpu::DepthStencilState {
                format: DEPTH_FORMAT,
                depth_write_enabled: true,
                depth_compare: wgpu::CompareFunction::Less,
                stencil: Default::default(),
                bias: Default::default(),
            }),
            multisample: Default::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: "fs",
                compilation_options: Default::default(),
                targets: &color_targets,
            }),
            multiview: None,
            cache: None,
        });
        let scene = scene_pipeline("vs", &buffers[..2]);
        let scene_moving = scene_pipeline("vs_moving", &buffers);

        // The post pass: one bind group for every entry point (post.wgsl).
        let compute = wgpu::ShaderStages::COMPUTE;
        let texture = |binding| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: compute,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: false },
                view_dimension: wgpu::TextureViewDimension::D2Array,
                multisampled: false,
            },
            count: None,
        };
        let storage = |binding, read_only| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: compute,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Storage { read_only },
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        };
        let post_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("post"),
            entries: &[
                uniform(0, compute, false),
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: compute,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: true,
                        min_binding_size: wgpu::BufferSize::new(std::mem::size_of::<PostLayer>() as u64),
                    },
                    count: None,
                },
                texture(2),
                texture(3),
                storage(4, true),
                storage(5, false),
                storage(6, false),
                storage(7, false),
                storage(8, false),
                storage(9, false),
            ],
        });
        let post_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("post"),
            source: wgpu::ShaderSource::Wgsl(include_str!("post.wgsl").into()),
        });
        let post_pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("post"),
            bind_group_layouts: &[&post_layout],
            push_constant_ranges: &[],
        });
        let entry = |name| {
            device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some(name),
                layout: Some(&post_pipeline_layout),
                module: &post_shader,
                entry_point: name,
                compilation_options: Default::default(),
                cache: None,
            })
        };
        let post = PostPipelines {
            distances: entry("distances"),
            occlude: entry("occlude"),
            blur_across: entry("blur_across"),
            blur_down: entry("blur_down"),
            accumulate: entry("accumulate"),
            finish: entry("finish"),
        };

        // Averaging a blurred frame's chunks (sum.wgsl).
        let sum_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("sum"),
            entries: &[uniform(0, compute, false), storage(1, false), storage(2, false), storage(3, true), storage(4, true)],
        });
        let sum_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("sum"),
            source: wgpu::ShaderSource::Wgsl(include_str!("sum.wgsl").into()),
        });
        let sum_pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("sum"),
            bind_group_layouts: &[&sum_layout],
            push_constant_ranges: &[],
        });
        let sum_entry = |name| {
            device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some(name),
                layout: Some(&sum_pipeline_layout),
                module: &sum_shader,
                entry_point: name,
                compilation_options: Default::default(),
                cache: None,
            })
        };
        let sum = SumPipelines { add: sum_entry("add"), scale: sum_entry("scale") };

        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("texture"),
            address_mode_u: wgpu::AddressMode::Repeat,
            address_mode_v: wgpu::AddressMode::Repeat,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });
        Ok(Gpu { device, queue, scene, scene_moving, globals_layout, mesh_layout, post_layout, post, sum_layout, sum, sampler, limits,
                 targets: None })
    }

    /// `body` under error scopes: a validation or out-of-memory error
    /// anywhere in it fails the frame (and the shim draws it on the CPU).
    fn scoped<T>(&mut self, frame: &Frame, body: impl FnOnce(&mut Gpu) -> Result<T, String>) -> Result<T, String> {
        if frame.width.max(frame.height) as u32 > self.limits.max_texture_dimension_2d {
            return Err(format!("{}x{} is larger than the GPU's textures", frame.width, frame.height));
        }
        let plane_bytes = (frame.width * frame.height * 16) as u64;
        if plane_bytes > self.limits.max_storage_buffer_binding_size as u64 || plane_bytes > self.limits.max_buffer_size {
            return Err(format!("{}x{} is larger than the GPU's buffers", frame.width, frame.height));
        }
        self.device.push_error_scope(wgpu::ErrorFilter::OutOfMemory);
        self.device.push_error_scope(wgpu::ErrorFilter::Validation);
        let result = body(self);
        let validation = pollster::block_on(self.device.pop_error_scope());
        let memory = pollster::block_on(self.device.pop_error_scope());
        if let Some(error) = validation.or(memory) {
            self.targets = None; // they may be what failed
            return Err(error.to_string());
        }
        result
    }

    /// The targets for a `w x h` frame of `layers` samples a pixel, made
    /// anew when the last frame's do not fit.
    fn targets(&mut self, w: usize, h: usize, layers: usize) -> &Targets {
        if self.targets.as_ref().is_some_and(|t| t.size == (w, h, layers)) {
            return self.targets.as_ref().expect("checked");
        }
        self.targets = None; // free the old ones first
        let device = &self.device;
        let texture = |format, usage| {
            device.create_texture(&wgpu::TextureDescriptor {
                label: Some("target"),
                size: wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: layers as u32 },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format,
                usage,
                view_formats: &[],
            })
        };
        let read = wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING
            | wgpu::TextureUsages::COPY_DST;
        let albedo = texture(COLOR_FORMAT, read);
        let normal = texture(COLOR_FORMAT, read);
        let depth_target = texture(DEPTH_FORMAT, wgpu::TextureUsages::RENDER_ATTACHMENT);
        let layer_view = |t: &wgpu::Texture, k: u32| t.create_view(&wgpu::TextureViewDescriptor {
            dimension: Some(wgpu::TextureViewDimension::D2),
            base_array_layer: k,
            array_layer_count: Some(1),
            ..Default::default()
        });
        let layer_views = (0..layers as u32)
            .map(|k| [layer_view(&albedo, k), layer_view(&normal, k), layer_view(&depth_target, k)])
            .collect();
        let array_view = |t: &wgpu::Texture| t.create_view(&wgpu::TextureViewDescriptor {
            dimension: Some(wgpu::TextureViewDimension::D2Array),
            ..Default::default()
        });

        let buffer = |label, size: usize, usage| device.create_buffer(&wgpu::BufferDescriptor {
            label: Some(label),
            size: size.max(16) as u64,
            usage,
            mapped_at_creation: false,
        });
        let plane = w * h;
        let storage = wgpu::BufferUsages::STORAGE;
        let uniform = wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST;
        let layer_stride = align(std::mem::size_of::<PostLayer>(), self.limits.min_uniform_buffer_offset_alignment as usize);
        let taps = composite::ao_taps(layers);
        let (period, _) = composite::rotation_pattern(layers);
        let spiral_count = layers * period * period * taps;
        let globals = buffer("post globals", std::mem::size_of::<PostGlobals>(), uniform);
        let layer_buffer = buffer("post layers", layer_stride * layers, uniform);
        let spirals = buffer("spirals", spiral_count * 16, storage | wgpu::BufferUsages::COPY_DST);
        let distance = buffer("distance", plane * 4, storage);
        let occlusion = buffer("occlusion", plane * 4, storage);
        let across = buffer("across", plane * 4, storage);
        let copied = wgpu::BufferUsages::COPY_SRC | wgpu::BufferUsages::COPY_DST;
        let color = buffer("color", plane * 16, storage | copied);
        let depth = buffer("depth", plane * 4, storage | copied);
        let sum_params = buffer("sum params", 16, uniform);
        self.queue.write_buffer(&sum_params, 0, bytes(&[[plane as u32, 0, 0, 0]]));
        let read_back = wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST;
        let read_color = buffer("read color", plane * 16, read_back);
        let read_depth = buffer("read depth", plane * 4, read_back);

        // The taps and each layer's offset: fixed for this sample count.
        let offsets = composite::sample_offsets(layers);
        let mut table: Vec<[f32; 4]> = Vec::with_capacity(spiral_count);
        let mut layer_bytes = vec![0u8; layer_stride * layers];
        for (k, &(ox, oy)) in offsets.iter().enumerate() {
            let first = table.len() as u32;
            table.extend(composite::spirals(k, layers).into_iter().map(|(c, s, along)| [c, s, along, 0.0]));
            let layer = PostLayer { offset: [ox, oy, 0.0, 0.0], index: [k as u32, first, 0, 0] };
            layer_bytes[k * layer_stride..][..std::mem::size_of::<PostLayer>()].copy_from_slice(bytes(&[layer]));
        }
        self.queue.write_buffer(&spirals, 0, bytes(&table));
        self.queue.write_buffer(&layer_buffer, 0, &layer_bytes);

        let (albedo_array, normal_array) = (array_view(&albedo), array_view(&normal));
        fn whole(b: &wgpu::Buffer) -> wgpu::BindingResource<'_> {
            b.as_entire_binding()
        }
        let bind = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("post"),
            layout: &self.post_layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: whole(&globals) },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Buffer(wgpu::BufferBinding {
                        buffer: &layer_buffer,
                        offset: 0,
                        size: wgpu::BufferSize::new(std::mem::size_of::<PostLayer>() as u64),
                    }),
                },
                wgpu::BindGroupEntry { binding: 2, resource: wgpu::BindingResource::TextureView(&albedo_array) },
                wgpu::BindGroupEntry { binding: 3, resource: wgpu::BindingResource::TextureView(&normal_array) },
                wgpu::BindGroupEntry { binding: 4, resource: whole(&spirals) },
                wgpu::BindGroupEntry { binding: 5, resource: whole(&distance) },
                wgpu::BindGroupEntry { binding: 6, resource: whole(&occlusion) },
                wgpu::BindGroupEntry { binding: 7, resource: whole(&across) },
                wgpu::BindGroupEntry { binding: 8, resource: whole(&color) },
                wgpu::BindGroupEntry { binding: 9, resource: whole(&depth) },
            ],
        });
        self.targets.insert(Targets {
            size: (w, h, layers),
            layer_views,
            #[cfg(test)]
            albedo,
            #[cfg(test)]
            normal,
            globals,
            layer_stride,
            color,
            depth,
            read_color,
            read_depth,
            bind,
            sum_params,
            slots: Vec::new(),
        })
    }

    /// A texture array of `tiles` (layer i = tiles[i]), every layer resized
    /// to the largest; a 1x1 white layer when there are none.
    fn texture_view(&self, tiles: &[(i32, &Texture)]) -> wgpu::TextureView {
        let (w, h) = tiles
            .iter()
            .fold((1, 1), |(w, h), (_, t)| (w.max(t.width), h.max(t.height)));
        let layers = tiles.len().max(1);
        let texture = self.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("tiles"),
            size: wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: layers as u32 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8UnormSrgb,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        for layer in 0..layers {
            let texels: Vec<[u8; 4]> = match tiles.get(layer) {
                None => vec![[255; 4]; w * h],
                Some((_, t)) if t.width == w && t.height == h => t.texels().to_vec(),
                Some((_, t)) => (0..w * h)
                    .map(|i| t.texels()[(i / w) * t.height / h * t.width + (i % w) * t.width / w])
                    .collect(),
            };
            self.queue.write_texture(
                wgpu::ImageCopyTexture {
                    texture: &texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d { x: 0, y: 0, z: layer as u32 },
                    aspect: wgpu::TextureAspect::All,
                },
                bytes(&texels),
                wgpu::ImageDataLayout { offset: 0, bytes_per_row: Some(4 * w as u32), rows_per_image: Some(h as u32) },
                wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: 1 },
            );
        }
        texture.create_view(&wgpu::TextureViewDescriptor {
            dimension: Some(wgpu::TextureViewDimension::D2Array),
            ..Default::default()
        })
    }

    /// A mesh's instances and material. Unblurred, one set of instances
    /// every layer draws; blurred (`layers`), one per layer, at its moment
    /// of the shutter (with `motion`) and through its camera.
    fn mesh_resources(&self, mesh: &Mesh, world_to_clip: &Mat4, cache: &mut CacheInner,
                      layers: Option<&[LayerCamera]>, motion: bool) -> MeshResources {
        let instance = |m: &Mat4, world_to_clip: &Mat4| {
            let nm = math::normal_matrix(m);
            let nrow = |r: usize| [nm[r * 3], nm[r * 3 + 1], nm[r * 3 + 2], 0.0];
            Instance { rows: rows(m), normal: [nrow(0), nrow(1), nrow(2)], clip: rows(&math::mul(m, world_to_clip)) }
        };
        let count = mesh.transforms.len();
        let (instances, layer_stride): (Vec<Instance>, u32) = match layers {
            None => (mesh.transforms.iter().map(|m| instance(m, world_to_clip)).collect(), 0),
            Some(layers) => (
                layers
                    .iter()
                    .flat_map(|l| (0..count).map(move |i| {
                        let m = if motion { blur::transform_at(mesh, i, l.time) } else { mesh.transforms[i] };
                        instance(&m, &l.world_to_clip)
                    }))
                    .collect(),
                count as u32,
            ),
        };
        let textured = mesh.corner_uvs.is_some() && !mesh.tiles.is_empty();
        let plain = mesh.tiles.len() == 1 && mesh.tiles[0].0 == 0;
        let mut layers = [[-1i32; 4]; 25];
        for (i, (tile, _)) in mesh.tiles.iter().enumerate() {
            if (1001..=1100).contains(tile) {
                let at = (tile - 1001) as usize;
                layers[at / 4][at % 4] = i as i32;
            }
        }
        let params = MeshParams {
            base: [mesh.base_color[0], mesh.base_color[1], mesh.base_color[2], 1.0],
            mode: [textured as i32, plain as i32, 0, 0],
            layers,
        };
        let tiles: &[(i32, &Texture)] = if textured { &mesh.tiles } else { &[] };
        let key: Vec<(i32, u64)> = tiles.iter().map(|(t, tex)| (*t, tex.id())).collect();
        let frame = cache.frame;
        let view = &cache
            .textures
            .entry(key)
            .and_modify(|t| t.frame = frame)
            .or_insert_with(|| CachedTexture { view: self.texture_view(tiles), frame })
            .view;
        let params = self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("mesh params"),
            contents: bytes(&[params]),
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let bind = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("mesh"),
            layout: &self.mesh_layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: params.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::TextureView(view) },
                wgpu::BindGroupEntry { binding: 2, resource: wgpu::BindingResource::Sampler(&self.sampler) },
            ],
        });
        MeshResources {
            instances: self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("instances"),
                contents: bytes(&instances),
                usage: wgpu::BufferUsages::VERTEX,
            }),
            instance_count: count as u32,
            layer_stride,
            bind,
        }
    }

    /// Draw `meshes` into the targets and run the post pass, which leaves
    /// the picture in the targets' colour and depth. `chunk`: a blurred
    /// frame's chunk, each layer through its own camera.
    fn draw(&mut self, frame: &Frame, meshes: &[Mesh], cache: Option<&Cache>, chunk: Option<&[LayerCamera]>)
            -> Result<Stats, String> {
        let (w, h) = (frame.width, frame.height);
        let offsets = composite::sample_offsets(frame.samples);
        let layers = offsets.len();
        self.targets(w, h, layers);
        let gpu = &*self;
        let targets = gpu.targets.as_ref().expect("made above");

        let motion = chunk.is_some() && frame.blur.motion;
        let axis = |a: Vec3| [a[0], a[1], a[2], 0.0];
        // Per layer: to sample (ox, oy) pixels from each centre (y down),
        // move the picture ox left and oy up (clip y points up); a blurred
        // layer's camera and, in jitter.z, its moment of the shutter.
        let globals: Vec<wgpu::BindGroup> = offsets
            .iter()
            .enumerate()
            .map(|(k, &(ox, oy))| {
                let (wc, view, time) = match chunk {
                    Some(l) => (l[k].world_to_clip, l[k].view, if motion { l[k].time } else { 0.0 }),
                    None => (frame.world_to_clip, View::new(&frame.view_to_world), 0.0),
                };
                let globals = Globals {
                    clip: rows(&wc),
                    eye: [view.eye[0], view.eye[1], view.eye[2], 1.0],
                    right: axis(view.axes[0]),
                    up: axis(view.axes[1]),
                    back: axis(view.axes[2]),
                    jitter: [-2.0 * ox / w as f32, 2.0 * oy / h as f32, time, 0.0],
                };
                let buffer = gpu.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("globals"),
                    contents: bytes(&[globals]),
                    usage: wgpu::BufferUsages::UNIFORM,
                });
                gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
                    label: Some("globals"),
                    layout: &gpu.globals_layout,
                    entries: &[wgpu::BindGroupEntry { binding: 0, resource: buffer.as_entire_binding() }],
                })
            })
            .collect();

        // Clear the targets: no colour, no normal, far depth.
        let mut encoder = gpu.device.create_command_encoder(&Default::default());
        for k in 0..layers {
            drop(targets.pass(&mut encoder, true, k));
        }
        gpu.queue.submit([encoder.finish()]);

        // Without a cache, this frame's own, dropped at its end.
        let mut own = CacheInner::default();
        let mut guard = cache.map(|c| c.inner.lock().unwrap_or_else(|e| e.into_inner()));
        let caching = guard.is_some();
        let inner: &mut CacheInner = guard.as_deref_mut().unwrap_or(&mut own);
        inner.frame += 1;
        let now = inner.frame;
        let mut stats = Stats::default();

        let resources: Vec<Option<MeshResources>> = meshes
            .iter()
            .map(|m| (!m.transforms.is_empty() && !m.triangles.is_empty())
                .then(|| gpu.mesh_resources(m, &frame.world_to_clip, inner, chunk, motion)))
            .collect();

        let mut batch = Batch {
            gpu,
            targets,
            globals: &globals,
            resources: &resources,
            transient: Vec::new(),
            motion: Vec::new(),
            draws: Vec::new(),
            pending_bytes: 0,
            pending_triangles: 0,
            elapsed: Duration::ZERO,
        };
        for (m, mesh) in meshes.iter().enumerate() {
            if resources[m].is_none() {
                continue;
            }
            let signature = Signature::of(mesh);
            let id = mesh.key.filter(|_| caching).map(|k| k.id);
            // A moving mesh's corners' closing positions, triangles
            // `start..` of a chunk, queued for this submission.
            let close = if motion { blur::closing_points(mesh) } else { None };
            let closing = |batch: &mut Batch, stats: &mut Stats, start: usize| -> Option<usize> {
                let close = close?;
                let started = Instant::now();
                let end = (start + CHUNK_TRIANGLES).min(mesh.triangles.len());
                let corners = closing_corners(mesh, close, start, end);
                let buffer = gpu.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("closing corners"),
                    contents: bytes(&corners),
                    usage: wgpu::BufferUsages::VERTEX,
                });
                stats.upload += started.elapsed();
                stats.uploaded_bytes += std::mem::size_of_val(corners.as_slice());
                batch.pending_bytes += std::mem::size_of_val(corners.as_slice());
                batch.motion.push(buffer);
                Some(batch.motion.len() - 1)
            };
            if let Some(id) = id {
                if let Some(cached) = inner.meshes.get_mut(&id).filter(|c| c.signature == signature) {
                    cached.frame = now;
                    stats.cached_meshes += 1;
                    let chunks: Vec<(u32, usize)> = cached.chunks.iter().map(|c| (c.vertices, c.start)).collect();
                    for (chunk, (vertices, start)) in chunks.into_iter().enumerate() {
                        let moving = closing(&mut batch, &mut stats, start);
                        batch.draw(inner, m, Corners::Cached { id, chunk }, vertices, moving);
                    }
                    continue;
                }
                // Rebuilt, or its material switched: the old corners are stale.
                if let Some(stale) = inner.meshes.remove(&id) {
                    inner.bytes -= stale.bytes;
                }
            }
            let size = mesh.triangles.len() * 3 * std::mem::size_of::<Vertex>();
            let keep = id.filter(|_| inner.bytes + size <= CACHE_BYTES);
            if let Some(id) = keep {
                inner.meshes.insert(id, CachedMesh { signature, chunks: Vec::new(), bytes: 0, frame: now });
            }
            stats.uploaded_meshes += 1;
            let started = Instant::now();
            let smooth = mesh.corner_normals.is_none().then(|| raster::smooth_normals(mesh.points, mesh.triangles));
            stats.upload += started.elapsed();
            for start in (0..mesh.triangles.len()).step_by(CHUNK_TRIANGLES) {
                let started = Instant::now();
                let end = (start + CHUNK_TRIANGLES).min(mesh.triangles.len());
                let verts = corner_vertices(mesh, smooth.as_deref(), start, end);
                if verts.is_empty() {
                    continue;
                }
                let buffer = gpu.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("corners"),
                    contents: bytes(&verts),
                    usage: wgpu::BufferUsages::VERTEX,
                });
                stats.upload += started.elapsed();
                let (vertices, n) = (verts.len() as u32, std::mem::size_of_val(verts.as_slice()));
                stats.uploaded_bytes += n;
                let corners = match keep {
                    Some(id) => {
                        let cached = inner.meshes.get_mut(&id).expect("inserted above");
                        cached.chunks.push(Chunk { buffer, vertices, start });
                        cached.bytes += n;
                        inner.bytes += n;
                        Corners::Cached { id, chunk: cached.chunks.len() - 1 }
                    }
                    None => {
                        batch.transient.push(Chunk { buffer, vertices, start });
                        Corners::Transient(batch.transient.len() - 1)
                    }
                };
                let moving = closing(&mut batch, &mut stats, start);
                batch.draw(inner, m, corners, vertices, moving);
                batch.uploaded(inner, n);
            }
        }
        batch.flush(inner);
        stats.raster = batch.elapsed;
        drop(batch);

        // Let go of what this frame did not draw.
        inner.meshes.retain(|_, c| c.frame == now);
        inner.textures.retain(|_, t| t.frame == now);
        inner.bytes = inner.meshes.values().map(|c| c.bytes).sum();
        stats.resident_bytes = if caching { inner.bytes } else { 0 };
        drop(guard);

        self.post_pass(frame, &mut stats)?;
        Ok(stats)
    }

    /// Fill the targets with a render buffer made elsewhere (the tests').
    #[cfg(test)]
    fn upload_samples(&mut self, samples: &Samples) -> Result<(), String> {
        let (w, h, layers) = (samples.width, samples.height, samples.count());
        self.targets(w, h, layers);
        let targets = self.targets.as_ref().expect("made above");
        let plane = w * h;
        let write = |texture: &wgpu::Texture, data: &[u8], k: usize| {
            self.queue.write_texture(
                wgpu::ImageCopyTexture {
                    texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d { x: 0, y: 0, z: k as u32 },
                    aspect: wgpu::TextureAspect::All,
                },
                data,
                wgpu::ImageDataLayout { offset: 0, bytes_per_row: Some((w * 16) as u32), rows_per_image: Some(h as u32) },
                wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: 1 },
            );
        };
        for k in 0..layers {
            let at = k * plane..(k + 1) * plane;
            let albedo: Vec<[f32; 4]> = samples.albedo[at.clone()].iter().zip(&samples.depth[at.clone()])
                .map(|(a, &d)| [a[0], a[1], a[2], d])
                .collect();
            write(&targets.albedo, bytes(&albedo), k);
            write(&targets.normal, bytes(&samples.normal[at]), k);
        }
        Ok(())
    }

    /// The post pass over the targets' render buffer, and the finished
    /// image read back.
    #[cfg(test)]
    fn post(&self, frame: &Frame, stats: &mut Stats) -> Result<Image, String> {
        self.post_pass(frame, stats)?;
        self.read_back(stats)
    }

    /// The post pass over the targets' render buffer: the finished image in
    /// the targets' colour and depth.
    fn post_pass(&self, frame: &Frame, stats: &mut Stats) -> Result<(), String> {
        let targets = self.targets.as_ref().ok_or("no targets")?;
        let (w, h, layers) = targets.size;
        let started = Instant::now();

        let proj = math::mul(&frame.view_to_world, &frame.world_to_clip);
        let unproject = composite::Unproject::new(&proj, w, h);
        let (period, pattern) = composite::rotation_pattern(layers);
        let mut cells = [[0u32; 4]; 4];
        for (i, &turn) in pattern.iter().enumerate() {
            cells[i / 4][i % 4] = turn as u32;
        }
        let lights = composite::LIGHTS.map(|(l, i)| {
            let l = math::normalize(l);
            [l[0], l[1], l[2], i]
        });
        let [t0, t1, t2] = composite::AO_TIERS;
        let clay = composite::CLAY_ALBEDO;
        let globals = PostGlobals {
            size: [w as u32, h as u32, layers as u32, composite::ao_taps(layers) as u32],
            mode: [period as u32, frame.ao as u32, frame.shading.abi(), composite::AO_BLUR_PX as u32],
            scale: [unproject.scale.0, unproject.scale.1, unproject.inv.0, unproject.inv.1],
            proj: [unproject.p[0], unproject.p[1], unproject.p[2], unproject.p[3]],
            proj_w: [unproject.p[4], unproject.p[5], (proj[5].abs() * h as f64 * 0.5) as f32,
                     composite::AO_MAX_SCREEN * h as f32],
            tiers01: [t0.0, t0.1, t1.0, t1.1],
            tier2: [t2.0, t2.1, 0.0, 0.0],
            ao: [composite::AO_FADE_PX.0, composite::AO_FADE_PX.1, composite::AO_GAIN, composite::AO_BIAS],
            lights,
            ambient: [composite::SKY, composite::GROUND, composite::SPECULAR, composite::SHININESS as f32],
            clay: [clay[0], clay[1], clay[2], 1.0 / layers as f32],
            backdrop: [composite::BACKDROP_TOP, composite::BACKDROP_BOTTOM, 0.0, 0.0],
            pattern: cells,
        };
        self.queue.write_buffer(&targets.globals, 0, bytes(&[globals]));

        let groups = ((w as u32).div_ceil(GROUP), (h as u32).div_ceil(GROUP));
        let mut encoder = self.device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor { label: Some("post"), timestamp_writes: None });
            let mut run = |pipeline: &wgpu::ComputePipeline, layer: usize| {
                pass.set_pipeline(pipeline);
                pass.set_bind_group(0, &targets.bind, &[(layer * targets.layer_stride) as u32]);
                pass.dispatch_workgroups(groups.0, groups.1, 1);
            };
            for layer in 0..layers {
                if frame.ao {
                    run(&self.post.distances, layer);
                    run(&self.post.occlude, layer);
                    run(&self.post.blur_across, layer);
                    run(&self.post.blur_down, layer);
                }
                run(&self.post.accumulate, layer);
            }
            run(&self.post.finish, 0);
        }
        self.queue.submit([encoder.finish()]);
        self.device.poll(wgpu::Maintain::Wait);
        stats.post = started.elapsed();
        Ok(())
    }

    /// The targets' finished image read back.
    fn read_back(&self, stats: &mut Stats) -> Result<Image, String> {
        let targets = self.targets.as_ref().ok_or("no targets")?;
        self.read_back_from(&targets.color, &targets.depth, stats)
    }

    /// An image (colour and depth buffers of the targets' size) read back.
    fn read_back_from(&self, color: &wgpu::Buffer, depth: &wgpu::Buffer, stats: &mut Stats) -> Result<Image, String> {
        let targets = self.targets.as_ref().ok_or("no targets")?;
        let plane = targets.size.0 * targets.size.1;
        let started = Instant::now();
        let mut encoder = self.device.create_command_encoder(&Default::default());
        encoder.copy_buffer_to_buffer(color, 0, &targets.read_color, 0, (plane * 16) as u64);
        encoder.copy_buffer_to_buffer(depth, 0, &targets.read_depth, 0, (plane * 4) as u64);
        self.queue.submit([encoder.finish()]);
        let map = |buf: &wgpu::Buffer, size: usize| -> Result<Vec<u8>, String> {
            let slice = buf.slice(..size as u64);
            let (tx, rx) = mpsc::channel();
            slice.map_async(wgpu::MapMode::Read, move |r| {
                let _ = tx.send(r);
            });
            self.device.poll(wgpu::Maintain::Wait);
            rx.recv().map_err(|e| e.to_string())?.map_err(|e| e.to_string())?;
            let data = slice.get_mapped_range().to_vec();
            buf.unmap();
            Ok(data)
        };
        let (color_bytes, depth_bytes) = (map(&targets.read_color, plane * 16)?, map(&targets.read_depth, plane * 4)?);
        let f32_at = |b: &[u8]| f32::from_le_bytes([b[0], b[1], b[2], b[3]]);
        let color: Vec<[f32; 4]> = color_bytes
            .chunks_exact(16)
            .map(|c| [f32_at(&c[0..]), f32_at(&c[4..]), f32_at(&c[8..]), f32_at(&c[12..])])
            .collect();
        let depth: Vec<f32> = depth_bytes.chunks_exact(4).map(f32_at).collect();
        stats.read_back = started.elapsed();
        Ok(Image { color, depth })
    }

    /// One `sum.wgsl` pass over slot-sized buffers: `dst` and `src` are
    /// colour and depth.
    fn sum_pass(&self, encoder: &mut wgpu::CommandEncoder, pipeline: &wgpu::ComputePipeline,
                dst: &[wgpu::Buffer; 2], src: [&wgpu::Buffer; 2]) {
        let targets = self.targets.as_ref().expect("a frame's targets");
        fn entry(binding: u32, buffer: &wgpu::Buffer) -> wgpu::BindGroupEntry<'_> {
            wgpu::BindGroupEntry { binding, resource: buffer.as_entire_binding() }
        }
        let bind = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("sum"),
            layout: &self.sum_layout,
            entries: &[entry(0, &targets.sum_params), entry(1, &dst[0]), entry(2, &dst[1]), entry(3, src[0]),
                       entry(4, src[1])],
        });
        // 64 pixels a group, in rows of at most 65535 groups (sum.wgsl).
        let groups = (targets.size.0 * targets.size.1).div_ceil(64) as u32;
        let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor { label: Some("sum"), timestamp_writes: None });
        pass.set_pipeline(pipeline);
        pass.set_bind_group(0, &bind, &[]);
        pass.dispatch_workgroups(groups.min(65535), groups.div_ceil(65535), 1);
    }

    /// `src` added into `dst`, slots of the sum.
    fn sum_add(&self, encoder: &mut wgpu::CommandEncoder, dst: usize, src: usize) {
        let slots = &self.targets.as_ref().expect("a frame's targets").slots;
        self.sum_pass(encoder, &self.sum.add, &slots[dst], [&slots[src][0], &slots[src][1]]);
    }

    /// The targets' picture (a chunk's, its post pass done) onto `sum`,
    /// where `sum[i]` is how many chunks slot `i` holds. Two neighbours
    /// holding as many merge at once, so slot `i` holds a whole power of
    /// two of chunks summed pairwise, as [`blur::average`] sums them.
    fn sum_push(&mut self, sum: &mut Vec<usize>) {
        let device = &self.device;
        let targets = self.targets.as_mut().expect("a frame's targets");
        let plane = targets.size.0 * targets.size.1;
        if targets.slots.len() == sum.len() {
            let slot = |label, size: usize| device.create_buffer(&wgpu::BufferDescriptor {
                label: Some(label),
                size: size.max(16) as u64,
                usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            });
            targets.slots.push([slot("sum color", plane * 16), slot("sum depth", plane * 4)]);
        }
        let targets = self.targets.as_ref().expect("a frame's targets");
        let mut encoder = self.device.create_command_encoder(&Default::default());
        let [color, depth] = &targets.slots[sum.len()];
        encoder.copy_buffer_to_buffer(&targets.color, 0, color, 0, (plane * 16) as u64);
        encoder.copy_buffer_to_buffer(&targets.depth, 0, depth, 0, (plane * 4) as u64);
        sum.push(1);
        while sum.len() >= 2 && sum[sum.len() - 1] == sum[sum.len() - 2] {
            self.sum_add(&mut encoder, sum.len() - 2, sum.len() - 1);
            sum.pop();
            *sum.last_mut().expect("two") *= 2;
        }
        self.queue.submit([encoder.finish()]);
    }

    /// The average of the `count` chunks on `sum`, read back: what is left
    /// merged from the last slot down (a chunk count's bits, the larger
    /// sum on the left, as [`blur::average`]'s odd one out is), times one
    /// over the count.
    fn sum_finish(&mut self, mut sum: Vec<usize>, count: usize, stats: &mut Stats) -> Result<Image, String> {
        assert_eq!(sum.iter().sum::<usize>(), count, "every chunk on the sum");
        let started = Instant::now();
        let targets = self.targets.as_ref().ok_or("no targets")?;
        let plane = targets.size.0 * targets.size.1;
        let inv = 1.0 / count as f32;
        self.queue.write_buffer(&targets.sum_params, 0, bytes(&[[plane as u32, inv.to_bits(), 0, 0]]));
        let mut encoder = self.device.create_command_encoder(&Default::default());
        while sum.len() >= 2 {
            self.sum_add(&mut encoder, sum.len() - 2, sum.len() - 1);
            let last = sum.pop().expect("two");
            *sum.last_mut().expect("one") += last;
        }
        // `scale` reads no source; a slot may not be bound twice.
        self.sum_pass(&mut encoder, &self.sum.scale, &targets.slots[0], [&targets.color, &targets.depth]);
        self.queue.submit([encoder.finish()]);
        let [color, depth] = &targets.slots[0];
        let image = self.read_back_from(color, depth, stats)?;
        stats.read_back = started.elapsed();
        Ok(image)
    }
}

#[cfg(test)]
mod tests {
    //! The CPU path is the reference: each scene is drawn both ways and the
    //! images compared, as 8-bit sRGB (what a playblast ends up as): a pixel
    //! agrees when no channel is more than two steps off ([`STEPS`]), and at
    //! most [`DIFFERING`] of the pixels may not, the few where a tap or an
    //! edge lands on a pixel boundary and the two round it differently.
    //! Measured on an RTX 3070 (Vulkan) far fewer differ; a 10% shading
    //! change fails every one.
    use super::*;
    use crate::math::IDENTITY;
    use crate::raster::MeshKey;
    use proptest::prelude::*;

    const STEPS: i32 = 2;
    const DIFFERING: f32 = 0.005;

    fn camera() -> (Mat4, Mat4) {
        let mut view_to_world = IDENTITY;
        view_to_world[14] = 5.0;
        let mut world_to_view = IDENTITY;
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

    fn frame(w: usize, h: usize, samples: usize) -> Frame {
        let (world_to_clip, view_to_world) = camera();
        Frame { width: w, height: h, world_to_clip, view_to_world, samples,
                shading: composite::Shading::Lit, ao: true, blur: crate::blur::Blur::OFF }
    }

    fn encode(v: f32) -> i32 {
        let v = v.clamp(0.0, 1.0);
        let s = if v <= 0.003_130_8 { v * 12.92 } else { 1.055 * v.powf(1.0 / 2.4) - 0.055 };
        (s * 255.0 + 0.5) as i32
    }

    /// The fraction of pixels more than [`STEPS`] off in 8-bit sRGB, and the
    /// fraction whose depth disagrees.
    fn differences(cpu: &Image, gpu: &Image) -> (f32, f32) {
        assert_eq!(cpu.color.len(), gpu.color.len());
        let n = cpu.color.len() as f32;
        let color = cpu.color.iter().zip(&gpu.color)
            .filter(|(a, b)| (0..4).any(|c| (encode(a[c]) - encode(b[c])).abs() > STEPS))
            .count() as f32 / n;
        let depth = cpu.depth.iter().zip(&gpu.depth)
            .filter(|(a, b)| (**a < 1.0) != (**b < 1.0) || (**a - **b).abs() > 1e-3)
            .count() as f32 / n;
        (color, depth)
    }

    fn compare(frame: &Frame, meshes: &[Mesh]) -> (f32, f32) {
        let cpu = raster::render(frame, meshes);
        let gpu = render(frame, meshes, None).unwrap_or_else(|e| panic!("GPU render failed: {e}"));
        let (color, depth) = differences(&cpu, &gpu);
        eprintln!("cpu vs gpu: {color:.4} of pixels differ in colour, {depth:.4} in depth");
        (color, depth)
    }

    fn quad() -> (Vec<Vec3>, Vec<[u32; 3]>) {
        (
            vec![[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]],
            vec![[0, 1, 2], [0, 2, 3]],
        )
    }

    fn mesh<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], transforms: &'a [Mat4]) -> Mesh<'a> {
        Mesh { points, triangles, corner_normals: None, corner_colors: None, corner_uvs: None,
               base_color: [0.8, 0.3, 0.1], tiles: Vec::new(), transforms, key: None,
               points_close: None, transform_keys: &[] }
    }

    /// `images` (colour and depth, `pixels` each) averaged on the card
    /// through the sum a blurred frame uses.
    fn average_on_card(pixels: usize, images: &[(Vec<[f32; 4]>, Vec<f32>)]) -> Image {
        gpu().expect("a GPU").scoped(&frame(pixels, 1, 1), |gpu| {
            gpu.targets(pixels, 1, 1);
            let mut sum = Vec::new();
            for (color, depth) in images {
                let targets = gpu.targets.as_ref().expect("made above");
                gpu.queue.write_buffer(&targets.color, 0, bytes(color));
                gpu.queue.write_buffer(&targets.depth, 0, bytes(depth));
                gpu.sum_push(&mut sum);
            }
            gpu.sum_finish(sum, images.len(), &mut Stats::default())
        }).unwrap_or_else(|e| panic!("GPU average failed: {e}"))
    }

    fn agrees((color, depth): (f32, f32)) {
        assert!(color <= DIFFERING && depth <= DIFFERING, "color {color}, depth {depth}");
    }

    fn chunk_pictures() -> impl Strategy<Value = (usize, Vec<(Vec<[f32; 4]>, Vec<f32>)>)> {
        let value = prop_oneof![Just(0.0f32), 0.0f32..4.0];
        let pixel = ([value.clone(), value.clone(), value.clone(), value], prop_oneof![Just(1.0f32), 0.0f32..1.0]);
        // Past one group of 64 pixels, and up to 9 chunks (not only powers
        // of two, though a frame's count is one).
        (1usize..200, 1usize..10).prop_flat_map(move |(pixels, count)| {
            let image = prop::collection::vec(pixel.clone(), pixels)
                .prop_map(|px| px.into_iter().unzip::<_, _, Vec<[f32; 4]>, Vec<f32>>());
            (Just(pixels), prop::collection::vec(image, count))
        })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(64))]

        /// A blurred frame's chunk pictures averaged on the card are
        /// blur::average's picture, bit for bit.
        #[test]
        fn chunks_averaged_on_the_card_are_the_cpus_average_bit_for_bit((pixels, images) in chunk_pictures()) {
            let got = average_on_card(pixels, &images);
            let want = blur::average(images.into_iter().map(|(color, depth)| Image { color, depth }).collect());
            let bits = |i: &Image| (i.color.iter().flatten().map(|v| v.to_bits()).collect::<Vec<_>>(),
                                    i.depth.iter().map(|v| v.to_bits()).collect::<Vec<_>>());
            prop_assert_eq!(bits(&got), bits(&want));
        }
    }

    #[test]
    fn backdrop_and_a_lit_quad_match_the_cpu() {
        let (points, triangles) = quad();
        // Tilted, so the lights and specular vary across it.
        let mut tilt = IDENTITY;
        let (s, c) = (0.6f64.sin(), 0.6f64.cos());
        tilt[0] = c; tilt[2] = -s; tilt[8] = s; tilt[10] = c;
        let transforms = [tilt];
        agrees(compare(&frame(96, 64, 1), &[mesh(&points, &triangles, &transforms)]));
    }

    #[test]
    fn instances_depth_and_the_near_plane_match_the_cpu() {
        let (points, triangles) = quad();
        let mut up = IDENTITY;
        up[13] = 1.5;
        let mut near = IDENTITY;
        near[14] = 1.0;
        near[12] = 0.5;
        let quads = [IDENTITY, up, near];
        let floor = vec![[-50.0, -1.0, 10.0], [50.0, -1.0, 10.0], [50.0, -1.0, -50.0], [-50.0, -1.0, -50.0]];
        let floor_tris = vec![[0u32, 1, 2], [0, 2, 3]];
        let one = [IDENTITY];
        let meshes = [mesh(&points, &triangles, &quads), mesh(&floor, &floor_tris, &one)];
        agrees(compare(&frame(128, 96, 8), &meshes));
    }

    #[test]
    fn corner_colours_and_udim_textures_match_the_cpu() {
        let (points, triangles) = quad();
        // Two UDIM tiles side by side: 1001 red/green checker, 1002 blue.
        let checker: Vec<[f32; 4]> = (0..64 * 64)
            .map(|i| if ((i % 64) / 8 + (i / 64) / 8) % 2 == 0 { [1.0, 0.0, 0.0, 1.0] } else { [0.0, 1.0, 0.0, 1.0] })
            .collect();
        let t1001 = Texture::from_linear(&checker, 64, 64);
        let t1002 = Texture::from_linear(&vec![[0.0, 0.0, 1.0, 1.0]; 16 * 16], 16, 16);
        let uvs: Vec<[f32; 2]> = [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 0.0], [2.0, 1.0], [0.0, 1.0]].to_vec();
        let transforms = [IDENTITY];
        let mut textured = mesh(&points, &triangles, &transforms);
        textured.corner_uvs = Some(&uvs);
        textured.tiles = vec![(1001, &t1001), (1002, &t1002)];
        let colors: Vec<Vec3> = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
                                 [1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [1.0, 1.0, 0.0]].to_vec();
        let mut right = IDENTITY;
        right[12] = 2.2;
        let shifted = [right];
        let mut coloured = mesh(&points, &triangles, &shifted);
        coloured.corner_colors = Some(&colors);
        // Tile 1002 is a different size: the GPU resizes it into the array.
        agrees(compare(&frame(128, 64, 1), &[textured, coloured]));
    }

    #[test]
    fn many_instances_split_across_submissions() {
        // 40k instances of a 2-triangle quad would be one draw; with a tiny
        // per-submission budget it is not. Here the real budget applies and
        // the picture must still match.
        let (points, triangles) = quad();
        let transforms: Vec<Mat4> = (0..40_000)
            .map(|i| {
                let mut m = IDENTITY;
                m[0] = 0.01; m[5] = 0.01;
                m[12] = (i % 200) as f64 * 0.02 - 2.0;
                m[13] = (i / 200) as f64 * 0.02 - 2.0;
                m
            })
            .collect();
        agrees(compare(&frame(160, 120, 1), &[mesh(&points, &triangles, &transforms)]));
    }

    // -- the post pass alone ------------------------------------------------

    /// A box of six quads, open or not, turned and placed: creases, contact
    /// with a floor and silhouettes for the occlusion and its blur.
    fn boxed(centre: [f32; 3], size: [f32; 3], yaw: f32) -> (Vec<Vec3>, Vec<[u32; 3]>) {
        let (s, c) = yaw.sin_cos();
        let points: Vec<Vec3> = (0..8)
            .map(|i| {
                let local = [
                    if i & 1 == 0 { -size[0] } else { size[0] },
                    if i & 2 == 0 { -size[1] } else { size[1] },
                    if i & 4 == 0 { -size[2] } else { size[2] },
                ];
                [centre[0] + c * local[0] + s * local[2], centre[1] + local[1], centre[2] - s * local[0] + c * local[2]]
            })
            .collect();
        let faces = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]];
        let triangles = faces.iter().flat_map(|f| [[f[0], f[1], f[2]], [f[0], f[2], f[3]]]).collect();
        (points, triangles)
    }

    fn a_box() -> impl Strategy<Value = ([f32; 3], [f32; 3], f32, [f32; 3])> {
        (
            [-2.5f32..2.5, -1.0f32..1.5, -3.0f32..2.0],
            [0.05f32..1.2, 0.05f32..1.2, 0.05f32..1.2],
            -1.5f32..1.5,
            [0.0f32..1.0, 0.0f32..1.0, 0.0f32..1.0],
        )
    }

    fn shading() -> impl Strategy<Value = Shading> {
        prop::sample::select(vec![Shading::Lit, Shading::Clay, Shading::Albedo, Shading::Occlusion])
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 48, ..ProptestConfig::default() })]

        /// The GPU post pass makes the CPU's picture from the same render
        /// buffer, for any scene, sample count, shading and occlusion
        /// setting: the occlusion, its blur, the lights, the backdrop and the
        /// average of the samples all agree, and so does depth.
        #[test]
        fn the_gpu_post_pass_makes_the_cpus_picture(
            boxes in prop::collection::vec(a_box(), 1..5),
            samples in prop::sample::select(vec![1usize, 2, 4, 8, 16]),
            shading in shading(),
            ao: bool,
            (w, h) in (8usize..90, 8usize..70),
        ) {
            let built: Vec<_> = boxes.iter().map(|(c, s, yaw, _)| boxed(*c, *s, *yaw)).collect();
            let floor = vec![[-6.0, -1.0, 4.0], [6.0, -1.0, 4.0], [6.0, -1.0, -8.0], [-6.0, -1.0, -8.0]];
            let floor_tris = vec![[0u32, 1, 2], [0, 2, 3]];
            let one = [IDENTITY];
            let mut meshes: Vec<Mesh> = built.iter().zip(&boxes)
                .map(|((p, t), (_, _, _, color))| Mesh { base_color: *color, ..mesh(p, t, &one) })
                .collect();
            meshes.push(mesh(&floor, &floor_tris, &one));
            let f = Frame { shading, ao, ..frame(w, h, samples) };
            let buffer = raster::render_buffer(&f, &meshes);
            let cpu = composite::resolve(&f, &buffer);
            let gpu = resolve(&f, &buffer).map_err(|e| TestCaseError::fail(format!("GPU post pass failed: {e}")))?;
            let (color, depth) = differences(&cpu, &gpu);
            prop_assert!(depth == 0.0, "depth differs on {} of the pixels", depth);
            // Small frames: a single pixel is already a large fraction.
            let allowed = DIFFERING.max(1.5 / (w * h) as f32);
            prop_assert!(color <= allowed, "{} of the pixels differ ({}x{}, {} samples, {:?}, ao {})",
                         color, w, h, samples, shading, ao);
        }
    }

    /// The largest colour difference, in 8-bit steps, on a scene where every
    /// part of the post pass is busy: it is a tolerance for rounding, not
    /// for a different picture.
    #[test]
    fn the_post_pass_is_off_by_rounding_only() {
        let a = boxed([-0.6, -0.4, 0.0], [0.6, 0.6, 0.6], 0.4);
        let b = boxed([0.9, -0.7, -0.6], [0.3, 0.3, 0.3], -0.3);
        let floor = vec![[-6.0, -1.0, 4.0], [6.0, -1.0, 4.0], [6.0, -1.0, -8.0], [-6.0, -1.0, -8.0]];
        let floor_tris = vec![[0u32, 1, 2], [0, 2, 3]];
        let one = [IDENTITY];
        let meshes = [mesh(&a.0, &a.1, &one), mesh(&b.0, &b.1, &one), mesh(&floor, &floor_tris, &one)];
        for samples in [1, 8] {
            let f = frame(160, 120, samples);
            let buffer = raster::render_buffer(&f, &meshes);
            let cpu = composite::resolve(&f, &buffer);
            let gpu = resolve(&f, &buffer).expect("GPU post pass");
            let worst = cpu.color.iter().zip(&gpu.color)
                .map(|(a, b)| (0..3).map(|c| (encode(a[c]) - encode(b[c])).abs()).max().unwrap_or(0))
                .max()
                .unwrap_or(0);
            let rms = (cpu.color.iter().zip(&gpu.color)
                .map(|(a, b)| (0..3).map(|c| (a[c] - b[c]).powi(2)).sum::<f32>())
                .sum::<f32>() / (cpu.color.len() * 3) as f32).sqrt();
            eprintln!("{samples} samples: worst {worst} steps, rms {rms:e}");
            assert!(worst <= 1, "{samples} samples: a pixel is {worst} steps off");
            assert!(rms < 1e-5, "{samples} samples: rms difference {rms}");
        }
    }

    // -- keeping meshes on the card -----------------------------------------

    /// Process-unique mesh ids for these tests (each test has its own cache,
    /// but the ids say what they are).
    fn new_id() -> u64 {
        use std::sync::atomic::{AtomicU64, Ordering};
        static NEXT: AtomicU64 = AtomicU64::new(1);
        NEXT.fetch_add(1, Ordering::Relaxed)
    }

    fn keyed<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], transforms: &'a [Mat4], id: u64, version: u64) -> Mesh<'a> {
        Mesh { key: Some(MeshKey { id, version }), ..mesh(points, triangles, transforms) }
    }

    #[test]
    fn an_unchanged_mesh_stays_on_the_card_and_draws_the_same() {
        let cache = Cache::default();
        let (points, triangles) = quad();
        let one = [IDENTITY];
        let id = new_id();
        let f = frame(64, 48, 4);
        let first = render_with_stats(&f, &[keyed(&points, &triangles, &one, id, 1)], Some(&cache)).expect("GPU");
        assert_eq!((first.1.uploaded_meshes, first.1.cached_meshes), (1, 0));
        assert!(first.1.uploaded_bytes > 0 && first.1.resident_bytes == first.1.uploaded_bytes);
        // The same mesh again, the camera's instance moved: nothing goes up.
        let mut moved = IDENTITY;
        moved[12] = 0.4;
        let moved = [moved];
        let second = render_with_stats(&f, &[keyed(&points, &triangles, &moved, id, 1)], Some(&cache)).expect("GPU");
        assert_eq!((second.1.uploaded_meshes, second.1.uploaded_bytes, second.1.cached_meshes), (0, 0, 1));
        agrees(differences(&raster::render(&f, &[mesh(&points, &triangles, &moved)]), &second.0));
    }

    #[test]
    fn a_rebuilt_mesh_goes_up_again() {
        let cache = Cache::default();
        let (mut points, triangles) = quad();
        let one = [IDENTITY];
        let id = new_id();
        let f = frame(64, 48, 1);
        render_with_stats(&f, &[keyed(&points, &triangles, &one, id, 1)], Some(&cache)).expect("GPU");
        // Rebuilt in place (same addresses, as a cleared vector refilled):
        // the version is all that tells.
        for p in points.iter_mut() {
            p[0] *= 0.5;
        }
        let (image, stats) = render_with_stats(&f, &[keyed(&points, &triangles, &one, id, 2)], Some(&cache)).expect("GPU");
        assert_eq!((stats.uploaded_meshes, stats.cached_meshes), (1, 0));
        agrees(differences(&raster::render(&f, &[mesh(&points, &triangles, &one)]), &image));
    }

    #[test]
    fn a_material_switch_without_a_rebuild_goes_up_again() {
        let cache = Cache::default();
        let (points, triangles) = quad();
        let one = [IDENTITY];
        let id = new_id();
        let f = frame(64, 48, 1);
        render_with_stats(&f, &[keyed(&points, &triangles, &one, id, 1)], Some(&cache)).expect("GPU");
        let colors: Vec<Vec3> = vec![[0.1, 0.9, 0.2]; 6];
        let mut coloured = keyed(&points, &triangles, &one, id, 1);
        coloured.corner_colors = Some(&colors);
        let (image, stats) = render_with_stats(&f, &[coloured], Some(&cache)).expect("GPU");
        assert_eq!(stats.uploaded_meshes, 1, "display colours appeared");
        let mut expected = mesh(&points, &triangles, &one);
        expected.corner_colors = Some(&colors);
        agrees(differences(&raster::render(&f, &[expected]), &image));
        // Back to the base colour, then only the base colour changes.
        render_with_stats(&f, &[keyed(&points, &triangles, &one, id, 1)], Some(&cache)).expect("GPU");
        let mut recoloured = keyed(&points, &triangles, &one, id, 1);
        recoloured.base_color = [0.2, 0.2, 0.9];
        let (image, stats) = render_with_stats(&f, &[recoloured], Some(&cache)).expect("GPU");
        assert_eq!(stats.uploaded_meshes, 1, "the base colour changed");
        let mut expected = mesh(&points, &triangles, &one);
        expected.base_color = [0.2, 0.2, 0.9];
        agrees(differences(&raster::render(&f, &[expected]), &image));
    }

    #[test]
    fn a_mesh_not_drawn_is_let_go() {
        let cache = Cache::default();
        let (points, triangles) = quad();
        let one = [IDENTITY];
        let (a, b) = (new_id(), new_id());
        let f = frame(32, 24, 1);
        let (_, stats) = render_with_stats(&f, &[keyed(&points, &triangles, &one, a, 1), keyed(&points, &triangles, &one, b, 1)],
                                           Some(&cache)).expect("GPU");
        let both = stats.resident_bytes;
        let (_, stats) = render_with_stats(&f, &[keyed(&points, &triangles, &one, a, 1)], Some(&cache)).expect("GPU");
        assert_eq!((stats.cached_meshes, stats.resident_bytes), (1, both / 2));
        let (_, stats) = render_with_stats(&f, &[keyed(&points, &triangles, &one, b, 1)], Some(&cache)).expect("GPU");
        assert_eq!((stats.uploaded_meshes, stats.cached_meshes), (1, 0), "b was let go and goes up again");
        // Without a cache nothing is kept, keys or not.
        let (_, stats) = render_with_stats(&f, &[keyed(&points, &triangles, &one, a, 1)], None).expect("GPU");
        assert_eq!((stats.uploaded_meshes, stats.resident_bytes), (1, 0));
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 24, ..ProptestConfig::default() })]

        /// Over any run of frames in which meshes change (and say so with a
        /// new version), appear and disappear, every frame drawn with the
        /// cache is the CPU's picture of that frame: nothing stale is ever
        /// drawn, and a mesh that did not change is never sent again.
        #[test]
        fn frames_drawn_with_the_cache_are_always_the_current_scene(
            edits in prop::collection::vec(prop::collection::vec((0u8..4, -0.6f32..0.6), 3), 2..6),
        ) {
            let cache = Cache::default();
            let ids = [new_id(), new_id(), new_id()];
            let mut versions = [1u64; 3];
            let mut shapes: Vec<(Vec<Vec3>, Vec<[u32; 3]>)> = (0..3)
                .map(|i| boxed([i as f32 * 1.6 - 1.6, -0.3, 0.0], [0.5, 0.5, 0.5], 0.3))
                .collect();
            let mut was_drawn = [false; 3];
            let one = [IDENTITY];
            let f = frame(72, 54, 4);
            for frame_edits in &edits {
                // Per mesh: 0 unchanged, 1 rebuilt (moved), 2 hidden, 3 unchanged.
                let mut expect_uploads = 0;
                let mut drawn = [false; 3];
                for (i, &(what, by)) in frame_edits.iter().enumerate() {
                    if what == 1 {
                        for p in shapes[i].0.iter_mut() {
                            p[1] += by;
                        }
                        versions[i] += 1;
                    }
                    drawn[i] = what != 2;
                    if drawn[i] && (what == 1 || !was_drawn[i]) {
                        expect_uploads += 1;
                    }
                }
                let meshes: Vec<Mesh> = (0..3)
                    .filter(|&i| drawn[i])
                    .map(|i| keyed(&shapes[i].0, &shapes[i].1, &one, ids[i], versions[i]))
                    .collect();
                let (image, stats) = render_with_stats(&f, &meshes, Some(&cache))
                    .map_err(|e| TestCaseError::fail(format!("GPU: {e}")))?;
                prop_assert_eq!(stats.uploaded_meshes, expect_uploads);
                prop_assert_eq!(stats.cached_meshes, meshes.len() - expect_uploads);
                let plain: Vec<Mesh> = meshes.iter().map(|m| Mesh { key: None, ..mesh(m.points, m.triangles, &one) }).collect();
                let (color, depth) = differences(&raster::render(&f, &plain), &image);
                prop_assert!(color <= DIFFERING && depth <= DIFFERING, "color {}, depth {}", color, depth);
                was_drawn = drawn;
            }
        }
    }

    // -- motion blur and depth of field -------------------------------------

    fn translation(x: f64, y: f64, z: f64) -> Mat4 {
        let mut m = IDENTITY;
        m[12] = x;
        m[13] = y;
        m[14] = z;
        m
    }

    /// How a box moves over the shutter: not at all, by its points, by its
    /// transform (rotating a little too), or by both.
    fn motion() -> impl Strategy<Value = (u8, [f32; 3], f32)> {
        (0u8..4, [-1.0f32..1.0, -0.5f32..0.5, -0.5f32..0.5], -0.4f32..0.4)
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 32, ..ProptestConfig::default() })]

        /// The GPU draws the CPU's blurred picture: boxes moving by their
        /// points or their transforms, a moving camera, a lens, any sample
        /// counts and shading, with and without the cache.
        #[test]
        fn the_gpu_blurs_as_the_cpu_does(
            boxes in prop::collection::vec((a_box(), motion()), 1..4),
            samples in prop::sample::select(vec![1usize, 4, 8]),
            blur_samples in prop::sample::select(vec![8usize, 16, 32]),
            camera_move in prop::option::of([-0.6f64..0.6, -0.3f64..0.3, -0.3f64..0.3]),
            lens in prop::option::of((0.05f32..0.4, 1.4f32..4.0, 2.0f32..9.0)),
            shading in shading(), ao: bool, cached: bool,
        ) {
            let (w, h) = (64usize, 40usize);
            let made: Vec<_> = boxes.iter().map(|((centre, size, yaw, color), (how, shift, turn))| {
                let (points, triangles) = boxed(*centre, *size, *yaw);
                let close: Vec<Vec3> = points.iter().map(|p| math::add(*p, *shift)).collect();
                let (s, c) = (turn.sin() as f64, turn.cos() as f64);
                let mut end = translation(shift[0] as f64 * 0.5, 0.0, shift[2] as f64);
                end[0] = c; end[2] = -s; end[8] = s; end[10] = c;
                (points, triangles, close, [IDENTITY, end], *how, *color)
            }).collect();
            let one = [IDENTITY];
            let meshes: Vec<Mesh> = made.iter().map(|(points, triangles, close, keys, how, color)| {
                let base = Mesh { base_color: *color, key: cached.then(|| MeshKey { id: new_id(), version: 1 }),
                                  ..mesh(points, triangles, &one) };
                match how {
                    1 => Mesh { points_close: Some(close), ..base },
                    2 => Mesh { transform_keys: keys, ..base },
                    3 => Mesh { points_close: Some(close), transform_keys: keys, ..base },
                    _ => base,
                }
            }).collect();
            let mut f = Frame { shading, ao, ..frame(w, h, samples) };
            f.blur = crate::blur::Blur {
                samples: blur_samples,
                motion: true,
                lens: lens.and_then(|(focal, stop, focus)| crate::blur::Lens::of_camera(focal, stop, focus)),
                ..crate::blur::Blur::OFF
            };
            if let Some([x, y, z]) = camera_move {
                f.blur.cameras[0] = f.view_to_world;
                f.blur.cameras[1] = math::mul(&f.view_to_world, &translation(x, y, z));
                f.blur.camera_keys = 2;
            }
            let cpu = raster::render(&f, &meshes);
            let cache = Cache::default();
            let gpu = render(&f, &meshes, cached.then_some(&cache)).unwrap_or_else(|e| panic!("GPU render failed: {e}"));
            let (color, depth) = differences(&cpu, &gpu);
            prop_assert!(color <= DIFFERING && depth <= DIFFERING, "color {color}, depth {depth}");
            if cached {
                // Drawn again from the card: the same picture.
                let again = render(&f, &meshes, Some(&cache)).unwrap();
                prop_assert!(again.color == gpu.color && again.depth == gpu.depth);
            }
        }
    }

    /// Off is off on the GPU too: with both blurs switched off, motion data
    /// on the meshes changes nothing, to the byte.
    #[test]
    fn the_gpu_ignores_motion_with_blur_off() {
        let (points, triangles) = boxed([0.2, 0.0, 0.0], [0.8, 0.6, 0.5], 0.4);
        let close: Vec<Vec3> = points.iter().map(|p| math::add(*p, [0.7, 0.1, 0.0])).collect();
        let one = [IDENTITY];
        let keys = [IDENTITY, translation(1.0, 0.0, 0.0)];
        let still = mesh(&points, &triangles, &one);
        let moving = Mesh { points_close: Some(&close), transform_keys: &keys, ..mesh(&points, &triangles, &one) };
        let mut f = frame(64, 40, 8);
        let reference = render(&f, &[still], None).unwrap();
        f.blur.samples = 32;
        let (image, stats) = render_with_stats(&f, &[moving], None).unwrap();
        assert!(image.color == reference.color && image.depth == reference.depth);
        assert_eq!(stats.chunks, 0, "drawn once, not in chunks");
    }

    /// A moving mesh keeps its corners on the card while its closing
    /// positions go up each frame: a second frame whose points close
    /// elsewhere (same version) draws as an uncached render of it does.
    #[test]
    fn a_cached_moving_mesh_moves_as_drawn_this_frame() {
        let (points, triangles) = boxed([0.0, 0.0, 0.0], [0.6, 0.6, 0.6], 0.3);
        let one = [IDENTITY];
        let id = new_id();
        let mut f = frame(64, 40, 4);
        f.blur = crate::blur::Blur { samples: 16, motion: true, ..crate::blur::Blur::OFF };
        let cache = Cache::default();
        for shift in [[0.8f32, 0.0, 0.0], [-0.5, 0.4, 0.0]] {
            let close: Vec<Vec3> = points.iter().map(|p| math::add(*p, shift)).collect();
            let m = Mesh { points_close: Some(&close), ..keyed(&points, &triangles, &one, id, 1) };
            let (cached, stats) = render_with_stats(&f, std::slice::from_ref(&m), Some(&cache)).unwrap();
            let fresh = render(&f, std::slice::from_ref(&Mesh { key: None, ..m }), None).unwrap();
            assert!(cached.color == fresh.color, "shift {shift:?}: cached corners moved wrong");
            assert!(stats.cached_meshes > 0 || shift[0] > 0.0, "the second frame reuses the corners");
        }
    }
}
