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
    // What fs_glass lights a see-through surface with, as post.wgsl's light
    // does an opaque one (lights::Lighting, gpu.rs Lights): the sky ambient
    // and in w the lamp count; the ground ambient and in w the lamp the
    // highlight is of; the highlight's strength, SHININESS, 1 = tinted by
    // that lamp's light; CLAY_ALBEDO; x: the shading (0 lit, 1 clay, 2
    // albedo, 3 occlusion); the lamps.
    sky: vec4<f32>,
    ground: vec4<f32>,
    spec: vec4<f32>,
    clay: vec4<f32>,
    shade: vec4<u32>,
    lamps: array<Lamp, 16>,
};

// lights::Lamp: xyz the unit direction towards it (w 0, directional) or its
// position (w 1 a point, 2 a point lighting the side it faces), camera
// space; rgb its light (a point's at unit distance), w the least squared
// distance; xyz the unit direction a facing lamp emits towards.
struct Lamp {
    at: vec4<f32>,
    color: vec4<f32>,
    axis: vec4<f32>,
};
@group(0) @binding(0) var<uniform> g: Globals;

struct MeshParams {
    // rgb: the base colour; a: the opacity (raster::Mesh::opacity).
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

// lights::Lamp::toward: the unit direction towards lamp `i` from camera-space
// point `p`, and the light it gives there before the surface's cosine.
struct Toward {
    l: vec3<f32>,
    e: vec3<f32>,
};

fn toward(i: u32, p: vec3<f32>) -> Toward {
    let lamp = g.lamps[i];
    var t: Toward;
    if (lamp.at.w == 0.0) {
        t.l = lamp.at.xyz;
        t.e = lamp.color.xyz;
        return t;
    }
    let d = lamp.at.xyz - p;
    let dd = dot(d, d);
    t.l = vec3<f32>(0.0, 0.0, 1.0);
    if (dd > 0.0) {
        t.l = d * (1.0 / sqrt(dd));
    }
    var f = 1.0 / max(dd, lamp.color.w);
    if (lamp.at.w == 2.0) {
        f = f * max(-dot(lamp.axis.xyz, t.l), 0.0);
    }
    t.e = lamp.color.xyz * f;
    return t;
}

// composite::light (post.wgsl's light), with the lighting in `g`.
fn light(shading: u32, surface: vec3<f32>, n: vec3<f32>, v: vec3<f32>, p: vec3<f32>) -> vec3<f32> {
    if (shading == 2u) {
        return surface;
    }
    if (shading == 3u) {
        return vec3<f32>(1.0);
    }
    var base = surface;
    if (shading == 1u) {
        base = g.clay.xyz;
    }
    var k = g.ground.xyz + (g.sky.xyz - g.ground.xyz) * (0.5 + 0.5 * n.y);
    let count = u32(g.sky.w);
    for (var i = 0u; i < count; i = i + 1u) {
        let t = toward(i, p);
        k = k + t.e * max(dot(n, t.l), 0.0);
    }
    var spec = vec3<f32>(0.0);
    if (count > 0u && g.spec.x > 0.0) {
        let t = toward(u32(g.ground.w), p);
        let half_vector = safe_normalize(t.l + v);
        let nh = max(dot(n, half_vector), 0.0);
        if (nh > 0.0) {
            let s = g.spec.x * pow(nh, g.spec.y);
            if (g.spec.z != 0.0) {
                spec = t.e * s;
            } else {
                spec = vec3<f32>(s);
            }
        }
    }
    return base * k + spec;
}

struct Glass {
    // The weighted sums (raster.rs Band::accum): lit colour times opacity,
    // and opacity, added up by the target's blend.
    @location(0) accum: vec4<f32>,
    // The opacity, one minus which the target's blend multiplies into the
    // revealage.
    @location(1) reveal: vec4<f32>,
};

// A see-through surface (raster::Surface::Glass) in front of the opaque
// depth, lit where it is drawn, as raster.rs Raster::draw does with `glass`:
// weighted blended order-independent transparency, every weight 1.
@fragment
fn fs_glass(in: VsOut) -> Glass {
    let to_eye = g.eye.xyz - in.world;
    var n = safe_normalize(in.normal);
    if (dot(n, to_eye) < 0.0) {
        n = -n;
    }
    let nc = safe_normalize(vec3<f32>(dot(n, g.right.xyz), dot(n, g.up.xyz), dot(n, g.back.xyz)));
    let towards = vec3<f32>(dot(to_eye, g.right.xyz), dot(to_eye, g.up.xyz), dot(to_eye, g.back.xyz));
    let v = safe_normalize(towards);
    let lit = light(g.shade.x, albedo(in), nc, v, -towards);
    let a = m.base.w;
    var out: Glass;
    out.accum = vec4<f32>(lit * a, a);
    out.reveal = vec4<f32>(a, a, a, a);
    return out;
}
