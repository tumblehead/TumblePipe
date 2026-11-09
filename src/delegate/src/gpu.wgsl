// The GPU path's shader: fills the same render buffer as src/raster.rs,
// which is the reference (colour, camera-space normal and distance, depth);
// post.wgsl makes the picture from it as src/composite.rs does. Keep the
// two in step.

struct Globals {
    // world_to_clip, one row per vec4 (USD row-vector layout).
    clip0: vec4<f32>,
    clip1: vec4<f32>,
    clip2: vec4<f32>,
    clip3: vec4<f32>,
    eye: vec4<f32>,
    // The camera's unit right, up and back axes, world space.
    right: vec4<f32>,
    up: vec4<f32>,
    back: vec4<f32>,
    // xy: this pass's shift of the picture, in clip units per unit w, so
    // that its sample of every pixel lands on the pixel centre. z: a blurred
    // layer's moment of the shutter (0 opening, 1 closing), which moving
    // points (vs_moving) are drawn at.
    jitter: vec4<f32>,
};
@group(0) @binding(0) var<uniform> g: Globals;

struct MeshParams {
    base: vec4<f32>,
    // x: 1 = textured (has uvs and tiles); y: 1 = a plain texture in layer 0;
    // z: the mesh's index in the frame's meshes (fs_ids).
    mode: vec4<i32>,
    // Layer of UDIM tile 1001 + i at [i / 4][i % 4], -1 for none.
    layers: array<vec4<i32>, 25>,
};
@group(1) @binding(0) var<uniform> m: MeshParams;
@group(1) @binding(1) var tex: texture_2d_array<f32>;
@group(1) @binding(2) var samp: sampler;

struct VsIn {
    @location(0) pos: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec3<f32>,
    @location(3) uv: vec2<f32>,
    // Object to world, one row per vec4, then the normal matrix's rows.
    @location(4) m0: vec4<f32>,
    @location(5) m1: vec4<f32>,
    @location(6) m2: vec4<f32>,
    @location(7) m3: vec4<f32>,
    @location(8) n0: vec4<f32>,
    @location(9) n1: vec4<f32>,
    @location(10) n2: vec4<f32>,
    // Object to clip, composed in f64 on the CPU (see gpu.rs Instance).
    @location(11) c0: vec4<f32>,
    @location(12) c1: vec4<f32>,
    @location(13) c2: vec4<f32>,
    @location(14) c3: vec4<f32>,
};

struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) world: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec3<f32>,
    @location(3) uv: vec2<f32>,
    // The draw's instance: in the first layer, where fs_ids reads it, the
    // instance's index among the mesh's transforms.
    @location(4) @interpolate(flat) instance: u32,
};

@vertex
fn vs(v: VsIn, @builtin(instance_index) instance: u32) -> VsOut {
    return vertex(v, v.pos, instance);
}

// A moving mesh's corner at the layer's moment, between its position at the
// shutter's opening (pos) and closing, as blur::point_at moves it.
@vertex
fn vs_moving(v: VsIn, @location(15) closing: vec3<f32>, @builtin(instance_index) instance: u32) -> VsOut {
    let s = g.jitter.z;
    return vertex(v, v.pos + (closing - v.pos) * s, instance);
}

fn vertex(v: VsIn, pos: vec3<f32>, instance: u32) -> VsOut {
    let w = pos.x * v.m0 + pos.y * v.m1 + pos.z * v.m2 + v.m3;
    var c = pos.x * v.c0 + pos.y * v.c1 + pos.z * v.c2 + v.c3;
    c = vec4<f32>(c.x + g.jitter.x * c.w, c.y + g.jitter.y * c.w, c.z, c.w);
    // GL clip z in [-w, w] to wgpu's [0, w]: the same depth the CPU path
    // stores (z / w * 0.5 + 0.5), and the same near plane.
    c.z = (c.z + c.w) * 0.5;
    var out: VsOut;
    out.clip = c;
    out.world = w.xyz;
    out.normal = v.normal.x * v.n0.xyz + v.normal.y * v.n1.xyz + v.normal.z * v.n2.xyz;
    out.color = v.color;
    out.uv = v.uv;
    out.instance = instance;
    return out;
}

fn safe_normalize(a: vec3<f32>) -> vec3<f32> {
    let len = length(a);
    if (len > 1e-20) {
        return a / len;
    }
    return vec3<f32>(0.0, 0.0, 1.0);
}

fn albedo(in: VsOut) -> vec3<f32> {
    if (m.mode.x == 1) {
        var layer = -1;
        if (m.mode.y == 1) {
            layer = 0;
        } else {
            let t = i32(floor(in.uv.x)) + 10 * i32(floor(in.uv.y));
            if (t >= 0 && t < 100) {
                layer = m.layers[t / 4][t % 4];
            }
        }
        if (layer >= 0) {
            // v = 0 is the bottom of the image; row 0 of the texture is its top.
            let st = vec2<f32>(in.uv.x, 1.0 - in.uv.y);
            return textureSampleLevel(tex, samp, st, layer, 0.0).rgb;
        }
    }
    return in.color;
}

struct Buffer {
    // rgb: the surface colour; a: the depth the depth test keeps, again,
    // for the post pass to read (it cannot read the depth attachment).
    @location(0) albedo: vec4<f32>,
    @location(1) normal: vec4<f32>,
};

// composite::View::buffer_normal: the normal in camera space turned towards
// the camera, and the distance in front of it.
@fragment
fn fs(in: VsOut) -> Buffer {
    let to_eye = g.eye.xyz - in.world;
    var n = safe_normalize(in.normal);
    if (dot(n, to_eye) < 0.0) {
        n = -n;
    }
    let v = safe_normalize(vec3<f32>(dot(n, g.right.xyz), dot(n, g.up.xyz), dot(n, g.back.xyz)));
    var out: Buffer;
    out.albedo = vec4<f32>(albedo(in), in.clip.z);
    out.normal = vec4<f32>(v, dot(to_eye, g.back.xyz));
    return out;
}

// The picking ids (raster.rs Samples::ids): the mesh and instance a pixel's
// first sample sees, drawn into their own target with their own depth test
// (gpu.rs Targets::id_pass), as the CPU path records them.
@fragment
fn fs_ids(in: VsOut) -> @location(0) vec2<i32> {
    return vec2<i32>(m.mode.z, i32(in.instance));
}
