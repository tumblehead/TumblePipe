// The GPU path's shader: the same picture as src/raster.rs, which is the
// reference. Keep the two in step.

struct Globals {
    // world_to_clip, one row per vec4 (USD row-vector layout).
    clip0: vec4<f32>,
    clip1: vec4<f32>,
    clip2: vec4<f32>,
    clip3: vec4<f32>,
    eye: vec4<f32>,
    // Towards the key light, world space.
    key: vec4<f32>,
    // ambient, specular, shininess, unused
    light: vec4<f32>,
    // Display-referred backdrop top, bottom; target height; unused.
    backdrop: vec4<f32>,
};
@group(0) @binding(0) var<uniform> g: Globals;

struct MeshParams {
    base: vec4<f32>,
    // x: 1 = textured (has uvs and tiles); y: 1 = a plain texture in layer 0
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
};

@vertex
fn vs(v: VsIn) -> VsOut {
    let w = v.pos.x * v.m0 + v.pos.y * v.m1 + v.pos.z * v.m2 + v.m3;
    var c = v.pos.x * v.c0 + v.pos.y * v.c1 + v.pos.z * v.c2 + v.c3;
    // GL clip z in [-w, w] to wgpu's [0, w]: the same depth the CPU path
    // stores (z / w * 0.5 + 0.5), and the same near plane.
    c.z = (c.z + c.w) * 0.5;
    var out: VsOut;
    out.clip = c;
    out.world = w.xyz;
    out.normal = v.normal.x * v.n0.xyz + v.normal.y * v.n1.xyz + v.normal.z * v.n2.xyz;
    out.color = v.color;
    out.uv = v.uv;
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

@fragment
fn fs(in: VsOut) -> @location(0) vec4<f32> {
    var n = safe_normalize(in.normal);
    let v = safe_normalize(g.eye.xyz - in.world);
    if (dot(n, v) < 0.0) {
        n = -n;
    }
    let l = g.key.xyz;
    let diffuse = max(dot(n, l), 0.0);
    let h = safe_normalize(l + v);
    let spec = g.light.y * pow(max(dot(n, h), 0.0), g.light.z);
    let k = g.light.x + (1.0 - g.light.x) * diffuse;
    return vec4<f32>(albedo(in) * k + vec3<f32>(spec), 1.0);
}

fn srgb_to_linear(v: f32) -> f32 {
    if (v <= 0.04045) {
        return v / 12.92;
    }
    return pow((v + 0.055) / 1.055, 2.4);
}

// The backdrop: one triangle over the whole target, drawn first.
@vertex
fn bg_vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let x = f32((i << 1u) & 2u) * 2.0 - 1.0;
    let y = f32(i & 2u) * 2.0 - 1.0;
    return vec4<f32>(x, y, 1.0, 1.0);
}

@fragment
fn bg_fs(@builtin(position) p: vec4<f32>) -> @location(0) vec4<f32> {
    let h = g.backdrop.z;
    var t = 0.0;
    if (h > 1.0) {
        t = (p.y - 0.5) / (h - 1.0);
    }
    let v = srgb_to_linear(g.backdrop.x + (g.backdrop.y - g.backdrop.x) * t);
    return vec4<f32>(v, v, v, 1.0);
}
