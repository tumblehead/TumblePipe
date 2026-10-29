//! The video view as a Primer shader. A movie frame is three single-channel
//! textures (Y, U, V) the shader converts; an EXR frame is one half-float
//! RGBA texture of scene-linear values, shown through the display transform
//! (an OCIO view baked into a 3D LUT, see `colour.rs`) or, with none, as
//! linear sRGB. Textures are re-uploaded only when the frame changes.

use std::sync::Arc;

use bytemuck::{Pod, Zeroable};
use half::f16;
use primer::prelude::*;
use primer::wgpu;
use primer::{Rectangle, Viewport};
use th_media::{Frame, Pixels, YuvMatrix};

use crate::colour::Lut;

/// Must match `Uniforms` in `video.wgsl`.
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Uniforms {
    resolution: [f32; 2],
    frame_size: [f32; 2],
    bounds: [f32; 4],
    params: [f32; 4],
    display: [f32; 4],
}

/// Which texture set the bind group samples.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Yuv,
    Linear,
}

struct Textures {
    size: (u32, u32),
    kind: Kind,
    /// Y, U, V for a movie; the RGBA texture for an EXR. The other kind's
    /// bindings get 1x1 stand-ins.
    planes: Vec<wgpu::Texture>,
    bind_group: wgpu::BindGroup,
}

pub struct VideoPipeline {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    uniforms: wgpu::Buffer,
    sampler: wgpu::Sampler,
    textures: Option<Textures>,
    /// The display LUT as a 3D texture, and which LUT it holds.
    lut: (wgpu::Texture, Option<u64>),
    /// Which frame the textures hold, so an unchanged frame is not re-uploaded.
    uploaded: Option<u64>,
    params: [f32; 4],
}

fn texture_entry(binding: u32, dimension: wgpu::TextureViewDimension) -> wgpu::BindGroupLayoutEntry {
    wgpu::BindGroupLayoutEntry {
        binding,
        visibility: wgpu::ShaderStages::FRAGMENT,
        ty: wgpu::BindingType::Texture {
            sample_type: wgpu::TextureSampleType::Float { filterable: true },
            view_dimension: dimension,
            multisampled: false,
        },
        count: None,
    }
}

fn make_texture(
    device: &wgpu::Device,
    label: &str,
    size: (u32, u32, u32),
    format: wgpu::TextureFormat,
    dimension: wgpu::TextureDimension,
) -> wgpu::Texture {
    device.create_texture(&wgpu::TextureDescriptor {
        label: Some(label),
        size: wgpu::Extent3d { width: size.0, height: size.1, depth_or_array_layers: size.2 },
        mip_level_count: 1,
        sample_count: 1,
        dimension,
        format,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    })
}

impl ShaderPipeline for VideoPipeline {
    fn new(device: &wgpu::Device, _queue: &wgpu::Queue, format: wgpu::TextureFormat, sample_count: u32) -> Self {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("video.shader"),
            source: wgpu::ShaderSource::Wgsl(include_str!("video.wgsl").into()),
        });
        let uniforms = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("video.uniforms"),
            size: std::mem::size_of::<Uniforms>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let d2 = wgpu::TextureViewDimension::D2;
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("video.bind_group.layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
                texture_entry(1, d2),
                texture_entry(2, d2),
                texture_entry(3, d2),
                wgpu::BindGroupLayoutEntry {
                    binding: 4,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                texture_entry(5, d2),
                texture_entry(6, wgpu::TextureViewDimension::D3),
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("video.pipeline.layout"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("video.pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                buffers: &[],
                compilation_options: wgpu::PipelineCompilationOptions::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
                targets: &[Some(wgpu::ColorTargetState { format, blend: None, write_mask: wgpu::ColorWrites::ALL })],
                compilation_options: wgpu::PipelineCompilationOptions::default(),
            }),
            primitive: wgpu::PrimitiveState { topology: wgpu::PrimitiveTopology::TriangleStrip, ..Default::default() },
            depth_stencil: None,
            multisample: wgpu::MultisampleState { count: sample_count, mask: !0, alpha_to_coverage_enabled: false },
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("video.sampler"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let lut = make_texture(device, "video.lut", (1, 1, 1), wgpu::TextureFormat::Rgba16Float, wgpu::TextureDimension::D3);
        Self { pipeline, layout, uniforms, sampler, textures: None, lut: (lut, None), uploaded: None, params: [0.0; 4] }
    }
}

impl VideoPipeline {
    fn textures_for(&mut self, device: &wgpu::Device, kind: Kind, width: u32, height: u32) -> &Textures {
        if self.textures.as_ref().is_none_or(|t| t.size != (width, height) || t.kind != kind) {
            self.textures = Some(self.make_textures(device, kind, width, height));
        }
        self.textures.as_ref().unwrap()
    }

    fn make_textures(&self, device: &wgpu::Device, kind: Kind, width: u32, height: u32) -> Textures {
        let d2 = wgpu::TextureDimension::D2;
        let r8 = |label, w, h| make_texture(device, label, (w, h, 1), wgpu::TextureFormat::R8Unorm, d2);
        let rgba = |w, h| make_texture(device, "video.rgba", (w, h, 1), wgpu::TextureFormat::Rgba16Float, d2);
        let planes = match kind {
            Kind::Yuv => {
                let (cw, ch) = (width.div_ceil(2), height.div_ceil(2));
                vec![r8("video.y", width, height), r8("video.u", cw, ch), r8("video.v", cw, ch), rgba(1, 1)]
            }
            Kind::Linear => vec![r8("video.y", 1, 1), r8("video.u", 1, 1), r8("video.v", 1, 1), rgba(width, height)],
        };
        let views: Vec<wgpu::TextureView> = planes.iter().map(|t| t.create_view(&Default::default())).collect();
        let lut_view = self.lut.0.create_view(&Default::default());
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("video.bind_group"),
            layout: &self.layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: self.uniforms.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::TextureView(&views[0]) },
                wgpu::BindGroupEntry { binding: 2, resource: wgpu::BindingResource::TextureView(&views[1]) },
                wgpu::BindGroupEntry { binding: 3, resource: wgpu::BindingResource::TextureView(&views[2]) },
                wgpu::BindGroupEntry { binding: 4, resource: wgpu::BindingResource::Sampler(&self.sampler) },
                wgpu::BindGroupEntry { binding: 5, resource: wgpu::BindingResource::TextureView(&views[3]) },
                wgpu::BindGroupEntry { binding: 6, resource: wgpu::BindingResource::TextureView(&lut_view) },
            ],
        });
        Textures { size: (width, height), kind, planes, bind_group }
    }

    /// Upload `lut` (or drop to a 1x1 stand-in for none) when it changed;
    /// the bind group samples the LUT texture, so it is rebuilt with it.
    fn set_lut(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, lut: Option<&Lut>) {
        let id = lut.map(|l| l.id);
        if self.lut.1 == id {
            return;
        }
        let n = lut.map_or(1, |l| l.size as u32);
        let texture = make_texture(device, "video.lut", (n, n, n), wgpu::TextureFormat::Rgba16Float, wgpu::TextureDimension::D3);
        if let Some(lut) = lut {
            queue.write_texture(
                wgpu::TexelCopyTextureInfo { texture: &texture, mip_level: 0, origin: wgpu::Origin3d::ZERO, aspect: wgpu::TextureAspect::All },
                bytemuck::cast_slice(&lut.rgba),
                wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(n * 8), rows_per_image: Some(n) },
                wgpu::Extent3d { width: n, height: n, depth_or_array_layers: n },
            );
        }
        self.lut = (texture, id);
        if let Some(t) = &self.textures {
            let (kind, (w, h)) = (t.kind, t.size);
            self.textures = Some(self.make_textures(device, kind, w, h));
            // New, empty textures: the frame must go up again.
            self.uploaded = None;
        }
    }
}

fn write_texture(queue: &wgpu::Queue, texture: &wgpu::Texture, data: &[u8], width: u32, height: u32, texel_bytes: u32) {
    queue.write_texture(
        wgpu::TexelCopyTextureInfo { texture, mip_level: 0, origin: wgpu::Origin3d::ZERO, aspect: wgpu::TextureAspect::All },
        data,
        wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(width * texel_bytes), rows_per_image: Some(height) },
        wgpu::Extent3d { width, height, depth_or_array_layers: 1 },
    );
}

/// Half floats with `channels` per pixel as RGBA: one channel is grey,
/// three get an opaque alpha.
pub fn to_rgba(data: &[f16], channels: usize) -> Vec<f16> {
    match channels {
        4 => data.to_vec(),
        3 => data.chunks_exact(3).flat_map(|p| [p[0], p[1], p[2], f16::ONE]).collect(),
        1 => data.iter().flat_map(|&v| [v, v, v, f16::ONE]).collect(),
        n => data.chunks_exact(n.max(1)).flat_map(|p| [p[0], p[0], p[0], f16::ONE]).collect(),
    }
}

/// How scene-linear frames are shown.
#[derive(Clone, Default)]
pub struct DisplaySettings {
    /// Exposure in stops, applied before the display transform.
    pub exposure: f32,
    /// The display transform; None shows linear values as sRGB.
    pub lut: Option<Arc<Lut>>,
}

/// This frame's inputs: the frame to show and an id that changes with it.
#[derive(Clone)]
pub struct Video {
    pub frame: Option<(u64, Frame)>,
    pub display: DisplaySettings,
}

impl ShaderProgram for Video {
    type Pipeline = VideoPipeline;

    fn prepare(
        &self,
        pipeline: &mut VideoPipeline,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        bounds: Rectangle,
        viewport: Viewport,
    ) {
        pipeline.set_lut(device, queue, self.display.lut.as_deref());
        let mut frame_size = [1.0, 1.0];
        match &self.frame {
            Some((id, frame)) => {
                frame_size = [frame.width as f32, frame.height as f32];
                if pipeline.uploaded != Some(*id) {
                    let (w, h) = (frame.width, frame.height);
                    match &*frame.pixels {
                        Pixels::Yuv420 { y, u, v, matrix, full_range } => {
                            let (cw, ch) = (w.div_ceil(2), h.div_ceil(2));
                            let textures = pipeline.textures_for(device, Kind::Yuv, w, h);
                            write_texture(queue, &textures.planes[0], y, w, h, 1);
                            write_texture(queue, &textures.planes[1], u, cw, ch, 1);
                            write_texture(queue, &textures.planes[2], v, cw, ch, 1);
                            pipeline.params = [
                                if *full_range { 1.0 } else { 0.0 },
                                if *matrix == YuvMatrix::Bt709 { 1.0 } else { 0.0 },
                                1.0,
                                0.0,
                            ];
                        }
                        Pixels::Half { data, channels } => {
                            let rgba = to_rgba(data, *channels);
                            let textures = pipeline.textures_for(device, Kind::Linear, w, h);
                            write_texture(queue, &textures.planes[3], bytemuck::cast_slice(&rgba), w, h, 8);
                            pipeline.params = [0.0, 0.0, 1.0, 1.0];
                        }
                    }
                    pipeline.uploaded = Some(*id);
                }
            }
            None => pipeline.params[2] = 0.0,
        }
        let resolution = viewport.physical_size();
        let (lo, hi) = self.display.lut.as_ref().map_or((0.0, 1.0), |l| l.shaper);
        let uniforms = Uniforms {
            resolution: [resolution.width as f32, resolution.height as f32],
            frame_size,
            bounds: [bounds.x, bounds.y, bounds.width, bounds.height],
            params: pipeline.params,
            display: [
                self.display.exposure.exp2(),
                if self.display.lut.is_some() { 1.0 } else { 0.0 },
                lo,
                hi,
            ],
        };
        queue.write_buffer(&pipeline.uniforms, 0, bytemuck::bytes_of(&uniforms));
    }

    fn draw(&self, pipeline: &VideoPipeline, pass: &mut wgpu::RenderPass<'_>) {
        let Some(textures) = &pipeline.textures else { return };
        pass.set_pipeline(&pipeline.pipeline);
        pass.set_bind_group(0, &textures.bind_group, &[]);
        pass.draw(0..4, 0..1);
    }
}
