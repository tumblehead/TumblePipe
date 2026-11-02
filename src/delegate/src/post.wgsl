// The GPU path's post pass: src/composite.rs (ambient_occlusion, blur_pass,
// light, resolve) as compute passes over the render buffer gpu.wgsl drew,
// so only the finished image and its depth leave the card. composite.rs is
// the reference and the tests in gpu.rs hold the two together; the constants
// come from it through `g` rather than being copied here. Keep the two in
// step.
//
// Per layer (sample) of the render buffer: `distances`, `occlude`,
// `blur_across`, `blur_down`, `accumulate`; then `finish` once.

struct Globals {
    // width, height, layers, taps per tier per sample
    size: vec4<u32>,
    // rotation period P, 1 = ambient occlusion on, shading (0 lit, 1 clay,
    // 2 albedo, 3 occlusion), blur radius in pixels
    mode: vec4<u32>,
    // composite::Unproject: scale.0, scale.1, inv.0, inv.1
    scale: vec4<f32>,
    // the projection's terms x8, x12, y9, y13
    proj: vec4<f32>,
    // w11, w15, |proj[5]| * height / 2 (pixels_per_unit), AO_MAX_SCREEN * height
    proj_w: vec4<f32>,
    // AO_TIERS: radius, strength (xy, zw)
    tiers01: vec4<f32>,
    tier2: vec4<f32>,
    // AO_FADE_PX.0, AO_FADE_PX.1, AO_GAIN, AO_BIAS
    ao: vec4<f32>,
    // per light: direction from the surface (unit, camera space), intensity
    lights: array<vec4<f32>, 3>,
    // SKY, GROUND, SPECULAR, SHININESS
    ambient: vec4<f32>,
    // CLAY_ALBEDO, 1 / layers
    clay: vec4<f32>,
    // BACKDROP_TOP, BACKDROP_BOTTOM (display-referred)
    backdrop: vec4<f32>,
    // composite::rotation_pattern, row by row, 4 a vec4
    pattern: array<vec4<u32>, 4>,
};

struct Layer {
    // this layer's sample offset from the pixel centre (x, y down)
    offset: vec4<f32>,
    // layer index, first of its taps in `spirals`
    index: vec4<u32>,
};

@group(0) @binding(0) var<uniform> g: Globals;
@group(0) @binding(1) var<uniform> layer: Layer;
// the render buffer: surface colour and, in a, depth (1 = nothing drawn);
// camera-space normal and distance
@group(0) @binding(2) var albedo_tex: texture_2d_array<f32>;
@group(0) @binding(3) var normal_tex: texture_2d_array<f32>;
// composite::spirals of every layer: cos, sin, distance along the radius
@group(0) @binding(4) var<storage, read> spirals: array<vec4<f32>>;
// this layer's distances, -1 where nothing was drawn (the CPU's NaN)
@group(0) @binding(5) var<storage, read_write> distance: array<f32>;
@group(0) @binding(6) var<storage, read_write> occlusion: array<f32>;
@group(0) @binding(7) var<storage, read_write> across: array<f32>;
@group(0) @binding(8) var<storage, read_write> color: array<vec4<f32>>;
@group(0) @binding(9) var<storage, read_write> depth: array<f32>;

fn unproject(sx: f32, sy: f32, d: f32) -> vec3<f32> {
    let ndc_x = sx * g.scale.x - 1.0;
    let ndc_y = 1.0 - sy * g.scale.y;
    let cw = g.proj_w.y - d * g.proj_w.x;
    return vec3<f32>((ndc_x * cw + d * g.proj.x - g.proj.y) * g.scale.z,
                     (ndc_y * cw + d * g.proj.z - g.proj.w) * g.scale.w,
                     -d);
}

fn pixels_per_unit(d: f32) -> f32 {
    let cw = max(abs(-d * g.proj_w.x + g.proj_w.y), 1e-12);
    return g.proj_w.z / cw;
}

fn safe_normalize(a: vec3<f32>) -> vec3<f32> {
    let len = sqrt(dot(a, a));
    if (len > 1e-20) {
        return a * (1.0 / len);
    }
    return vec3<f32>(0.0, 0.0, 1.0);
}

fn texel(at: vec2<u32>) -> vec2<i32> {
    return vec2<i32>(i32(at.x), i32(at.y));
}

fn inside(id: vec3<u32>) -> bool {
    return id.x < g.size.x && id.y < g.size.y;
}

@compute @workgroup_size(8, 8)
fn distances(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    let l = i32(layer.index.x);
    let z = textureLoad(albedo_tex, texel(id.xy), l, 0).w;
    var d = -1.0;
    if (z < 1.0) {
        d = textureLoad(normal_tex, texel(id.xy), l, 0).w;
    }
    distance[id.y * g.size.x + id.x] = d;
}

fn tier(t: u32) -> vec2<f32> {
    if (t == 0u) {
        return g.tiers01.xy;
    }
    if (t == 1u) {
        return g.tiers01.zw;
    }
    return g.tier2.xy;
}

// composite::ambient_occlusion, one sample.
@compute @workgroup_size(8, 8)
fn occlude(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    let w = g.size.x;
    let h = g.size.y;
    let at = id.y * w + id.x;
    let d = distance[at];
    if (d < 0.0) {
        occlusion[at] = 1.0;
        return;
    }
    let ox = layer.offset.x;
    let oy = layer.offset.y;
    let fx = f32(id.x) + 0.5;
    let fy = f32(id.y) + 0.5;
    let p = unproject(fx + ox, fy + oy, d);
    let n = textureLoad(normal_tex, texel(id.xy), i32(layer.index.x), 0).xyz;
    let period = g.mode.x;
    let cell = (id.y % period) * period + id.x % period;
    let turn = g.pattern[cell / 4u][cell % 4u];
    let taps = g.size.w;
    let first = layer.index.y + turn * taps;
    let ppu = pixels_per_unit(d);
    let bias = g.ao.w;
    var ao = 1.0;
    for (var t = 0u; t < 3u; t = t + 1u) {
        let rs = tier(t);
        let r_px = min(rs.x * ppu, g.proj_w.w);
        let fade = smoothstep(g.ao.x, g.ao.y, r_px);
        if (fade <= 0.0) {
            continue; // finer than a pixel here
        }
        let inv_r = ppu / r_px;
        var occluded = 0.0;
        for (var k = 0u; k < taps; k = k + 1u) {
            let tap = spirals[first + k];
            let dist = max(r_px * tap.z, 1.0);
            var qx = fx + tap.x * dist;
            var qy = fy - tap.y * dist;
            if (qx < 0.0 || qy < 0.0 || qx >= f32(w) || qy >= f32(h)) {
                continue;
            }
            qx = floor(qx);
            qy = floor(qy);
            let dq = distance[u32(qy) * w + u32(qx)];
            if (dq < 0.0) {
                continue; // nothing drawn there
            }
            let q = unproject(qx + 0.5 + ox, qy + 0.5 + oy, dq);
            let v = q - p;
            let vv = dot(v, v);
            if (vv <= 1e-12) {
                continue;
            }
            let inv_len = 1.0 / sqrt(vv);
            let falloff = smoothstep(0.0, 1.0, 2.0 - 2.0 * inv_r / inv_len);
            occluded = occluded + max(dot(v, n) * inv_len - bias, 0.0) * falloff;
        }
        let o = min(g.ao.z * occluded / (f32(taps) * (1.0 - bias)), 1.0);
        ao = ao * clamp(1.0 - fade * rs.y * o, 0.0, 1.0);
    }
    occlusion[at] = ao;
}

fn blur_source(at: u32, from_occlusion: bool) -> f32 {
    if (from_occlusion) {
        return occlusion[at];
    }
    return across[at];
}

fn pow8(x: f32) -> f32 {
    let x2 = x * x;
    let x4 = x2 * x2;
    return x4 * x4;
}

// composite::blur_pass, one sample: across rows from `occlusion` into
// `across`, or down columns from `across` back into `occlusion`.
fn blur(id: vec3<u32>, horizontal: bool) -> f32 {
    let w = g.size.x;
    let h = g.size.y;
    let at = id.y * w + id.x;
    let l = i32(layer.index.x);
    let n = textureLoad(normal_tex, texel(id.xy), l, 0);
    if (n.w <= 0.0) {
        return blur_source(at, horizontal); // nothing drawn
    }
    let radius = g.mode.w;
    let period = g.mode.x;
    let inv_gap = 1.0 / max(0.02 * n.w, 1e-6);
    var pos = id.y;
    var len = h;
    if (horizontal) {
        pos = id.x;
        len = w;
    }
    var lo = 0u;
    if (pos > radius) {
        lo = pos - radius;
    }
    let hi = min(pos + radius, len - 1u);
    var sums = array<f32, 4>(0.0, 0.0, 0.0, 0.0);
    var weights = array<f32, 4>(0.0, 0.0, 0.0, 0.0);
    var phase = lo % period;
    for (var q = lo; q <= hi; q = q + 1u) {
        var xy = vec2<u32>(id.x, q);
        if (horizontal) {
            xy = vec2<u32>(q, id.y);
        }
        let m = textureLoad(normal_tex, texel(xy), l, 0);
        if (m.w > 0.0) {
            let agree = pow8(max(n.x * m.x + n.y * m.y + n.z * m.z, 0.0));
            let gap = abs(m.w - n.w) * inv_gap;
            var spatial = 1.0;
            if (max(q, pos) - min(q, pos) == radius) {
                spatial = 0.5;
            }
            let wgt = spatial * agree / (1.0 + gap * gap);
            sums[phase] = sums[phase] + blur_source(xy.y * w + xy.x, horizontal) * wgt;
            weights[phase] = weights[phase] + wgt;
        }
        phase = phase + 1u;
        if (phase == period) {
            phase = 0u;
        }
    }
    var sum = 0.0;
    var weight = 0.0;
    for (var i = 0u; i < period; i = i + 1u) {
        let wt = weights[i];
        if (wt > 0.0) {
            let share = min(wt, 1.0);
            sum = sum + sums[i] / wt * share;
            weight = weight + share;
        }
    }
    if (weight > 0.0) {
        return sum / weight;
    }
    return blur_source(at, horizontal);
}

@compute @workgroup_size(8, 8)
fn blur_across(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    across[id.y * g.size.x + id.x] = blur(id, true);
}

@compute @workgroup_size(8, 8)
fn blur_down(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    occlusion[id.y * g.size.x + id.x] = blur(id, false);
}

fn srgb_to_linear(v: f32) -> f32 {
    if (v <= 0.04045) {
        return v / 12.92;
    }
    return pow((v + 0.055) / 1.055, 2.4);
}

// composite::light.
fn light(shading: u32, surface: vec3<f32>, n: vec3<f32>, v: vec3<f32>) -> vec3<f32> {
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
    var k = g.ambient.y + (g.ambient.x - g.ambient.y) * (0.5 + 0.5 * n.y);
    for (var i = 0u; i < 3u; i = i + 1u) {
        k = k + g.lights[i].w * max(dot(n, g.lights[i].xyz), 0.0);
    }
    let half_vector = safe_normalize(g.lights[0].xyz + v);
    let nh = max(dot(n, half_vector), 0.0);
    var spec = 0.0;
    if (nh > 0.0) {
        spec = g.ambient.z * pow(nh, g.ambient.w);
    }
    return base * k + vec3<f32>(spec);
}

// composite::resolve: this layer's sample of each pixel, lit and occluded
// (or the backdrop), added to the pixel's sum; the nearest depth kept.
@compute @workgroup_size(8, 8)
fn accumulate(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    let w = g.size.x;
    let h = g.size.y;
    let at = id.y * w + id.x;
    let l = i32(layer.index.x);
    let a = textureLoad(albedo_tex, texel(id.xy), l, 0);
    let z = a.w;
    var c: vec3<f32>;
    if (z < 1.0) {
        let nd = textureLoad(normal_tex, texel(id.xy), l, 0);
        let sx = f32(id.x) + 0.5 + layer.offset.x;
        let sy = f32(id.y) + 0.5 + layer.offset.y;
        let v = safe_normalize(-unproject(sx, sy, nd.w));
        var ao = 1.0;
        if (g.mode.y == 1u) {
            ao = occlusion[at];
        }
        c = light(g.mode.z, a.xyz, nd.xyz, v) * ao;
    } else {
        var t = 0.0;
        if (h > 1u) {
            t = f32(id.y) / f32(h - 1u);
        }
        let b = srgb_to_linear(g.backdrop.x + (g.backdrop.y - g.backdrop.x) * t);
        c = vec3<f32>(b, b, b);
    }
    if (layer.index.x == 0u) {
        color[at] = vec4<f32>(c, 0.0);
        depth[at] = min(1.0, z);
    } else {
        color[at] = vec4<f32>(color[at].xyz + c, 0.0);
        depth[at] = min(depth[at], z);
    }
}

// Each pixel the average of its samples.
@compute @workgroup_size(8, 8)
fn finish(@builtin(global_invocation_id) id: vec3<u32>) {
    if (!inside(id)) {
        return;
    }
    let at = id.y * g.size.x + id.x;
    let s = color[at].xyz;
    color[at] = vec4<f32>(s.x * g.clay.w, s.y * g.clay.w, s.z * g.clay.w, 1.0);
}
