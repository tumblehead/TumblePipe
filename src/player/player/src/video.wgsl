// The video view: a decoded frame fitted inside the widget's bounds
// (letterboxed, aspect kept). A movie's 4:2:0 Y'CbCr is converted to R'G'B'
// here; an EXR's scene-linear RGBA goes through the display transform, a 3D
// LUT indexed in log2 (shaper) space, see `colour.rs`.

struct Uniforms {
    resolution: vec2<f32>,   // render target, physical pixels
    frame_size: vec2<f32>,   // the frame, pixels
    bounds: vec4<f32>,       // widget rect: x, y, width, height (physical)
    // x: 1 = full range, 0 = video range; y: 1 = BT.709, 0 = BT.601;
    // z: 1 = there is a frame to show; w: 1 = scene-linear RGBA, 0 = Y'CbCr.
    params: vec4<f32>,
    // x: exposure gain; y: 1 = apply the LUT; z, w: the shaper's log2 range.
    display: vec4<f32>,
};

@group(0) @binding(0) var<uniform> u: Uniforms;
@group(0) @binding(1) var plane_y: texture_2d<f32>;
@group(0) @binding(2) var plane_u: texture_2d<f32>;
@group(0) @binding(3) var plane_v: texture_2d<f32>;
@group(0) @binding(4) var plane_sampler: sampler;
@group(0) @binding(5) var linear_rgba: texture_2d<f32>;
@group(0) @binding(6) var display_lut: texture_3d<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertex: u32) -> VertexOut {
    let corner = vec2<f32>(f32(vertex & 1u), f32(vertex >> 1u));
    let fit = min(u.bounds.z / u.frame_size.x, u.bounds.w / u.frame_size.y);
    let size = u.frame_size * fit;
    let origin = u.bounds.xy + (u.bounds.zw - size) * 0.5;
    let pixel = origin + corner * size;
    var out: VertexOut;
    out.position = vec4<f32>(pixel.x / u.resolution.x * 2.0 - 1.0, 1.0 - pixel.y / u.resolution.y * 2.0, 0.0, 1.0);
    out.uv = corner;
    return out;
}

// The surface is sRGB: the GPU encodes what we return. Returning the decoded
// code value makes the display receive the code values unchanged.
fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
    let low = c / 12.92;
    let high = pow((c + 0.055) / 1.055, vec3<f32>(2.4));
    return select(high, low, c <= vec3<f32>(0.04045));
}

fn yuv(uv: vec2<f32>) -> vec3<f32> {
    let y = textureSample(plane_y, plane_sampler, uv).r;
    let cb_raw = textureSample(plane_u, plane_sampler, uv).r;
    let cr_raw = textureSample(plane_v, plane_sampler, uv).r;
    var luma: f32;
    var cb: f32;
    var cr: f32;
    if (u.params.x > 0.5) {
        luma = y;
        cb = cb_raw - 128.0 / 255.0;
        cr = cr_raw - 128.0 / 255.0;
    } else {
        luma = (y * 255.0 - 16.0) / 219.0;
        cb = (cb_raw * 255.0 - 128.0) / 224.0;
        cr = (cr_raw * 255.0 - 128.0) / 224.0;
    }
    var rgb: vec3<f32>;
    if (u.params.y > 0.5) {
        rgb = vec3<f32>(luma + 1.5748 * cr, luma - 0.187324 * cb - 0.468124 * cr, luma + 1.8556 * cb);
    } else {
        rgb = vec3<f32>(luma + 1.402 * cr, luma - 0.344136 * cb - 0.714136 * cr, luma + 1.772 * cb);
    }
    return clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.0));
}

// Must match `Lut::shape` and `Lut::sample` in colour.rs.
fn display_transform(linear: vec3<f32>) -> vec3<f32> {
    let lo = u.display.z;
    let hi = u.display.w;
    let shaped = clamp((log2(max(linear, vec3<f32>(exp2(lo)))) - lo) / (hi - lo), vec3<f32>(0.0), vec3<f32>(1.0));
    let n = f32(textureDimensions(display_lut).x);
    let coord = shaped * (n - 1.0) / n + 0.5 / n;
    return textureSampleLevel(display_lut, plane_sampler, coord, 0.0).rgb;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    if (u.params.z < 0.5) {
        discard;
    }
    if (u.params.w < 0.5) {
        return vec4<f32>(srgb_to_linear(yuv(in.uv)), 1.0);
    }
    let linear = textureSample(linear_rgba, plane_sampler, in.uv).rgb * u.display.x;
    if (u.display.y > 0.5) {
        // The LUT gives display code values.
        return vec4<f32>(srgb_to_linear(clamp(display_transform(linear), vec3<f32>(0.0), vec3<f32>(1.0))), 1.0);
    }
    // No display transform: linear values, encoded as sRGB by the surface.
    return vec4<f32>(clamp(linear, vec3<f32>(0.0), vec3<f32>(1.0)), 1.0);
}
