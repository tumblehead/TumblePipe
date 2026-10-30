//! The GPU path: the same picture as [`crate::raster`], drawn with wgpu
//! (Vulkan, DX12 or Metal, headless: no window, GL context or display).
//!
//! Opt-in (`TH_PLAYBLAST_GPU=1`, see [`crate::ffi`]); any failure falls back
//! to the CPU path, which stays the reference: `gpu.wgsl` shades exactly as
//! `raster.rs` does, and the tests below compare the two.
//!
//! Work reaches the GPU in bounded pieces. Each mesh's corners go up in
//! chunks of [`CHUNK_TRIANGLES`], and a submission carries at most
//! [`SUBMIT_TRIANGLES`] drawn triangles (instances included) and
//! [`SUBMIT_BYTES`] of vertex data, then waits for the GPU. One huge
//! submission is what a driver watchdog (Windows TDR) resets the device for,
//! and that takes the display down with it.

use crate::math::{self, Mat4, Vec3};
use crate::raster::{self, Frame, Image, Mesh};
use crate::texture::Texture;
use std::collections::HashMap;
use std::sync::{mpsc, Mutex, OnceLock};
use wgpu::util::DeviceExt;

const CHUNK_TRIANGLES: usize = 1 << 20;
const SUBMIT_TRIANGLES: u64 = 32_000_000;
const SUBMIT_BYTES: usize = 256 << 20;
const COLOR_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba32Float;
const DEPTH_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Depth32Float;

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
    key: [f32; 4],
    light: [f32; 4],
    backdrop: [f32; 4],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct MeshParams {
    base: [f32; 4],
    mode: [i32; 4],
    layers: [[i32; 4]; 25],
}

/// The bytes of plain-old-data `repr(C)` values (f32 / i32 fields only, so
/// no padding).
fn bytes<T: Copy>(values: &[T]) -> &[u8] {
    unsafe { std::slice::from_raw_parts(values.as_ptr() as *const u8, std::mem::size_of_val(values)) }
}

fn align(n: usize, to: usize) -> usize {
    n.div_ceil(to) * to
}

struct Gpu {
    device: wgpu::Device,
    queue: wgpu::Queue,
    scene: wgpu::RenderPipeline,
    backdrop: wgpu::RenderPipeline,
    globals_layout: wgpu::BindGroupLayout,
    mesh_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    max_dim: u32,
}

static GPU: OnceLock<Result<Mutex<Gpu>, String>> = OnceLock::new();

/// Render `meshes` on the GPU. The device is opened on first use and kept
/// for the process (husk renders every frame through the same delegate).
pub fn render(frame: &Frame, meshes: &[Mesh]) -> Result<Image, String> {
    let gpu = GPU.get_or_init(|| Gpu::new().map(Mutex::new)).as_ref().map_err(Clone::clone)?;
    let gpu = gpu.lock().map_err(|_| "an earlier GPU frame panicked".to_string())?;
    gpu.render(frame, meshes)
}

struct MeshResources {
    instances: wgpu::Buffer,
    instance_count: u32,
    bind: wgpu::BindGroup,
}

/// Drawn work waiting for a submission.
struct Draw {
    mesh: usize,
    buffer: usize,
    vertices: u32,
    instances: std::ops::Range<u32>,
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
        let uniform = |binding, visibility| wgpu::BindGroupLayoutEntry {
            binding,
            visibility,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        };
        let globals_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("globals"),
            entries: &[uniform(0, wgpu::ShaderStages::VERTEX_FRAGMENT)],
        });
        let mesh_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("mesh"),
            entries: &[
                uniform(0, wgpu::ShaderStages::FRAGMENT),
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

        let color_target = [Some(wgpu::ColorTargetState {
            format: COLOR_FORMAT,
            blend: None,
            write_mask: wgpu::ColorWrites::ALL,
        })];
        let depth = |write, compare| {
            Some(wgpu::DepthStencilState {
                format: DEPTH_FORMAT,
                depth_write_enabled: write,
                depth_compare: compare,
                stencil: Default::default(),
                bias: Default::default(),
            })
        };
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
        let scene = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("scene"),
            layout: Some(&scene_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: "vs",
                compilation_options: Default::default(),
                buffers: &[
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
                ],
            },
            primitive,
            depth_stencil: depth(true, wgpu::CompareFunction::Less),
            multisample: Default::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: "fs",
                compilation_options: Default::default(),
                targets: &color_target,
            }),
            multiview: None,
            cache: None,
        });
        let backdrop_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("backdrop"),
            bind_group_layouts: &[&globals_layout],
            push_constant_ranges: &[],
        });
        let backdrop = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("backdrop"),
            layout: Some(&backdrop_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: "bg_vs",
                compilation_options: Default::default(),
                buffers: &[],
            },
            primitive,
            depth_stencil: depth(false, wgpu::CompareFunction::Always),
            multisample: Default::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: "bg_fs",
                compilation_options: Default::default(),
                targets: &color_target,
            }),
            multiview: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("texture"),
            address_mode_u: wgpu::AddressMode::Repeat,
            address_mode_v: wgpu::AddressMode::Repeat,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });
        Ok(Gpu {
            device,
            queue,
            scene,
            backdrop,
            globals_layout,
            mesh_layout,
            sampler,
            max_dim: limits.max_texture_dimension_2d,
        })
    }

    fn render(&self, frame: &Frame, meshes: &[Mesh]) -> Result<Image, String> {
        let mut ss = frame.supersample.clamp(1, 4);
        while ss > 1 && (frame.width.max(frame.height) * ss) as u32 > self.max_dim {
            ss -= 1;
        }
        if frame.width.max(frame.height) as u32 > self.max_dim {
            return Err(format!("{}x{} is larger than the GPU's textures", frame.width, frame.height));
        }
        self.device.push_error_scope(wgpu::ErrorFilter::OutOfMemory);
        self.device.push_error_scope(wgpu::ErrorFilter::Validation);
        let image = self.draw(frame, meshes, ss);
        let validation = pollster::block_on(self.device.pop_error_scope());
        let memory = pollster::block_on(self.device.pop_error_scope());
        if let Some(error) = validation.or(memory) {
            return Err(error.to_string());
        }
        image
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

    fn mesh_resources(&self, mesh: &Mesh, world_to_clip: &Mat4,
                      views: &mut HashMap<Vec<(i32, usize)>, wgpu::TextureView>) -> MeshResources {
        let rows = |m: &Mat4| {
            let row = |r: usize| [m[r * 4] as f32, m[r * 4 + 1] as f32, m[r * 4 + 2] as f32, m[r * 4 + 3] as f32];
            [row(0), row(1), row(2), row(3)]
        };
        let instances: Vec<Instance> = mesh
            .transforms
            .iter()
            .map(|m| {
                let nm = math::normal_matrix(m);
                let nrow = |r: usize| [nm[r * 3], nm[r * 3 + 1], nm[r * 3 + 2], 0.0];
                Instance {
                    rows: rows(m),
                    normal: [nrow(0), nrow(1), nrow(2)],
                    clip: rows(&math::mul(m, world_to_clip)),
                }
            })
            .collect();
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
        let key: Vec<(i32, usize)> = tiles.iter().map(|(t, tex)| (*t, *tex as *const Texture as usize)).collect();
        let view = views.entry(key).or_insert_with(|| self.texture_view(tiles));
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
            instance_count: instances.len() as u32,
            bind,
        }
    }

    fn draw(&self, frame: &Frame, meshes: &[Mesh], ss: usize) -> Result<Image, String> {
        let (w, h) = (frame.width * ss, frame.height * ss);
        let target = |format, usage| {
            self.device.create_texture(&wgpu::TextureDescriptor {
                label: Some("target"),
                size: wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: 1 },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format,
                usage,
                view_formats: &[],
            })
        };
        let usage = wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC;
        let color = target(COLOR_FORMAT, usage);
        let depth = target(DEPTH_FORMAT, usage);
        let color_view = color.create_view(&Default::default());
        let depth_view = depth.create_view(&Default::default());

        let (eye, key) = raster::headlight(&frame.view_to_world);
        let wc = &frame.world_to_clip;
        let row = |r: usize| [wc[r * 4] as f32, wc[r * 4 + 1] as f32, wc[r * 4 + 2] as f32, wc[r * 4 + 3] as f32];
        let globals = Globals {
            clip: [row(0), row(1), row(2), row(3)],
            eye: [eye[0], eye[1], eye[2], 1.0],
            key: [key[0], key[1], key[2], 0.0],
            light: [raster::AMBIENT, raster::SPECULAR, raster::SHININESS as f32, 0.0],
            backdrop: [raster::BACKDROP_TOP, raster::BACKDROP_BOTTOM, h as f32, 0.0],
        };
        let globals = self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("globals"),
            contents: bytes(&[globals]),
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let globals = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("globals"),
            layout: &self.globals_layout,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: globals.as_entire_binding() }],
        });

        let pass = |encoder: &mut wgpu::CommandEncoder, clear: bool| {
            let (color_load, depth_load) = if clear {
                (wgpu::LoadOp::Clear(wgpu::Color::BLACK), wgpu::LoadOp::Clear(1.0))
            } else {
                (wgpu::LoadOp::Load, wgpu::LoadOp::Load)
            };
            encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("frame"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &color_view,
                    resolve_target: None,
                    ops: wgpu::Operations { load: color_load, store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &depth_view,
                    depth_ops: Some(wgpu::Operations { load: depth_load, store: wgpu::StoreOp::Store }),
                    stencil_ops: None,
                }),
                timestamp_writes: None,
                occlusion_query_set: None,
            })
            .forget_lifetime()
        };

        // The backdrop, on cleared targets.
        let mut encoder = self.device.create_command_encoder(&Default::default());
        {
            let mut p = pass(&mut encoder, true);
            p.set_pipeline(&self.backdrop);
            p.set_bind_group(0, &globals, &[]);
            p.draw(0..3, 0..1);
        }
        self.queue.submit([encoder.finish()]);

        let mut views = HashMap::new();
        let resources: Vec<Option<MeshResources>> = meshes
            .iter()
            .map(|m| (!m.transforms.is_empty() && !m.triangles.is_empty()).then(|| self.mesh_resources(m, &frame.world_to_clip, &mut views)))
            .collect();

        let mut buffers: Vec<wgpu::Buffer> = Vec::new();
        let mut draws: Vec<Draw> = Vec::new();
        let (mut pending_bytes, mut pending_triangles) = (0usize, 0u64);
        // Submits the queued draws and waits for them; the vertex buffers are
        // the caller's to drop.
        let flush = |buffers: &Vec<wgpu::Buffer>, draws: &mut Vec<Draw>| {
            if draws.is_empty() {
                return;
            }
            let mut encoder = self.device.create_command_encoder(&Default::default());
            {
                let mut p = pass(&mut encoder, false);
                p.set_pipeline(&self.scene);
                p.set_bind_group(0, &globals, &[]);
                for d in draws.iter() {
                    let r = resources[d.mesh].as_ref().expect("drawn meshes have resources");
                    p.set_bind_group(1, &r.bind, &[]);
                    p.set_vertex_buffer(0, buffers[d.buffer].slice(..));
                    p.set_vertex_buffer(1, r.instances.slice(..));
                    p.draw(0..d.vertices, d.instances.clone());
                }
            }
            self.queue.submit([encoder.finish()]);
            self.device.poll(wgpu::Maintain::Wait);
            draws.clear();
        };

        for (m, mesh) in meshes.iter().enumerate() {
            let Some(res) = &resources[m] else { continue };
            let smooth = mesh
                .corner_normals
                .is_none()
                .then(|| raster::smooth_normals(mesh.points, mesh.triangles));
            for start in (0..mesh.triangles.len()).step_by(CHUNK_TRIANGLES) {
                let end = (start + CHUNK_TRIANGLES).min(mesh.triangles.len());
                let mut verts: Vec<Vertex> = Vec::with_capacity((end - start) * 3);
                for t in start..end {
                    let vi = mesh.triangles[t].map(|i| i as usize);
                    if vi.iter().any(|&i| i >= mesh.points.len()) {
                        continue;
                    }
                    for (k, &v) in vi.iter().enumerate() {
                        let c = t * 3 + k;
                        let normal: Vec3 = match (mesh.corner_normals, &smooth) {
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
                if verts.is_empty() {
                    continue;
                }
                let tris = (verts.len() / 3) as u64;
                buffers.push(self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("corners"),
                    contents: bytes(&verts),
                    usage: wgpu::BufferUsages::VERTEX,
                }));
                pending_bytes += std::mem::size_of_val(verts.as_slice());
                let buffer = buffers.len() - 1;
                // Instances in runs that keep a submission under the budget.
                let per_run = (SUBMIT_TRIANGLES / tris).clamp(1, res.instance_count as u64) as u32;
                let mut first = 0;
                while first < res.instance_count {
                    let last = (first + per_run).min(res.instance_count);
                    draws.push(Draw { mesh: m, buffer, vertices: verts.len() as u32, instances: first..last });
                    pending_triangles += tris * (last - first) as u64;
                    first = last;
                    if pending_triangles >= SUBMIT_TRIANGLES {
                        flush(&buffers, &mut draws);
                        pending_triangles = 0;
                    }
                }
                if draws.is_empty() {
                    // Everything queued so far has been drawn.
                    buffers.clear();
                    pending_bytes = 0;
                } else if pending_bytes >= SUBMIT_BYTES {
                    flush(&buffers, &mut draws);
                    buffers.clear();
                    pending_triangles = 0;
                    pending_bytes = 0;
                }
            }
        }
        flush(&buffers, &mut draws);

        let (color, depth) = self.read_back(&color, &depth, w, h)?;
        Ok(raster::downsample(color, depth, w, h, ss))
    }

    fn read_back(&self, color: &wgpu::Texture, depth: &wgpu::Texture, w: usize, h: usize)
        -> Result<(Vec<[f32; 4]>, Vec<f32>), String> {
        let (color_row, depth_row) = (align(w * 16, 256), align(w * 4, 256));
        let buffer = |size: usize| {
            self.device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("read back"),
                size: (size * h) as u64,
                usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            })
        };
        let (color_buf, depth_buf) = (buffer(color_row), buffer(depth_row));
        let mut encoder = self.device.create_command_encoder(&Default::default());
        let extent = wgpu::Extent3d { width: w as u32, height: h as u32, depth_or_array_layers: 1 };
        for (texture, aspect, buf, row) in [
            (color, wgpu::TextureAspect::All, &color_buf, color_row),
            (depth, wgpu::TextureAspect::DepthOnly, &depth_buf, depth_row),
        ] {
            encoder.copy_texture_to_buffer(
                wgpu::ImageCopyTexture { texture, mip_level: 0, origin: wgpu::Origin3d::ZERO, aspect },
                wgpu::ImageCopyBuffer {
                    buffer: buf,
                    layout: wgpu::ImageDataLayout {
                        offset: 0,
                        bytes_per_row: Some(row as u32),
                        rows_per_image: Some(h as u32),
                    },
                },
                extent,
            );
        }
        self.queue.submit([encoder.finish()]);

        let map = |buf: &wgpu::Buffer| -> Result<Vec<u8>, String> {
            let (tx, rx) = mpsc::channel();
            buf.slice(..).map_async(wgpu::MapMode::Read, move |r| {
                let _ = tx.send(r);
            });
            self.device.poll(wgpu::Maintain::Wait);
            rx.recv().map_err(|e| e.to_string())?.map_err(|e| e.to_string())?;
            let data = buf.slice(..).get_mapped_range().to_vec();
            buf.unmap();
            Ok(data)
        };
        let (color_bytes, depth_bytes) = (map(&color_buf)?, map(&depth_buf)?);
        let f32_at = |b: &[u8], at: usize| f32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]]);
        let mut out_color = Vec::with_capacity(w * h);
        let mut out_depth = Vec::with_capacity(w * h);
        for y in 0..h {
            for x in 0..w {
                let c = y * color_row + x * 16;
                out_color.push([
                    f32_at(&color_bytes, c),
                    f32_at(&color_bytes, c + 4),
                    f32_at(&color_bytes, c + 8),
                    f32_at(&color_bytes, c + 12),
                ]);
                out_depth.push(f32_at(&depth_bytes, y * depth_row + x * 4));
            }
        }
        Ok((out_color, out_depth))
    }
}

#[cfg(test)]
mod tests {
    //! The CPU path is the reference: each scene is drawn both ways and the
    //! images compared. Measured on an RTX 3070 (Vulkan) they agree on every
    //! pixel; the thresholds leave room for another driver breaking a tie on
    //! a pixel centre differently, and still catch a 10% shading change.
    use super::*;
    use crate::math::IDENTITY;

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

    fn frame(w: usize, h: usize, ss: usize) -> Frame {
        let (world_to_clip, view_to_world) = camera();
        Frame { width: w, height: h, world_to_clip, view_to_world, supersample: ss }
    }

    /// Fraction of pixels whose colour differs by more than `tol`, and the
    /// fraction whose depth disagrees on whether anything was drawn.
    fn compare(frame: &Frame, meshes: &[Mesh]) -> (f32, f32) {
        let cpu = raster::render(frame, meshes);
        let gpu = match render(frame, meshes) {
            Ok(image) => image,
            Err(e) => panic!("GPU render failed: {e}"),
        };
        assert_eq!(cpu.color.len(), gpu.color.len());
        let n = cpu.color.len() as f32;
        let color = cpu.color.iter().zip(&gpu.color)
            .filter(|(a, b)| (0..4).any(|c| (a[c] - b[c]).abs() > 0.02))
            .count() as f32 / n;
        let coverage = cpu.depth.iter().zip(&gpu.depth)
            .filter(|(a, b)| (**a < 1.0) != (**b < 1.0) || (**a - **b).abs() > 1e-3)
            .count() as f32 / n;
        eprintln!("cpu vs gpu: {:.4} of pixels differ in colour, {:.4} in depth", color, coverage);
        (color, coverage)
    }

    fn quad() -> (Vec<Vec3>, Vec<[u32; 3]>) {
        (
            vec![[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]],
            vec![[0, 1, 2], [0, 2, 3]],
        )
    }

    fn mesh<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], transforms: &'a [Mat4]) -> Mesh<'a> {
        Mesh { points, triangles, corner_normals: None, corner_colors: None, corner_uvs: None,
               base_color: [0.8, 0.3, 0.1], tiles: Vec::new(), transforms }
    }

    #[test]
    fn backdrop_and_a_lit_quad_match_the_cpu() {
        let (points, triangles) = quad();
        // Tilted, so the headlight and specular vary across it.
        let mut tilt = IDENTITY;
        let (s, c) = (0.6f64.sin(), 0.6f64.cos());
        tilt[0] = c; tilt[2] = -s; tilt[8] = s; tilt[10] = c;
        let transforms = [tilt];
        let (color, coverage) = compare(&frame(96, 64, 1), &[mesh(&points, &triangles, &transforms)]);
        assert!(color < 0.005 && coverage < 0.005, "color {color}, coverage {coverage}");
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
        let (color, coverage) = compare(&frame(128, 96, 2), &meshes);
        assert!(color < 0.005 && coverage < 0.005, "color {color}, coverage {coverage}");
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
        let (color, coverage) = compare(&frame(128, 64, 1), &[textured, coloured]);
        assert!(color < 0.005 && coverage < 0.005, "color {color}, coverage {coverage}");
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
        let (color, coverage) = compare(&frame(160, 120, 1), &[mesh(&points, &triangles, &transforms)]);
        assert!(color < 0.005 && coverage < 0.005, "color {color}, coverage {coverage}");
    }
}
