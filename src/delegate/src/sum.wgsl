// A blurred frame's chunks averaged on the card, as blur::average does on
// the CPU: pictures summed pairwise (`add`, in the order gpu.rs's Sum keeps),
// then the sum scaled by one over the count (`scale`). Each step is one f32
// operation per channel, the CPU's, so the result is the CPU's bit for bit.

struct Params {
    // x: pixels; y: one over the chunk count, as f32 bits; z: 1 = average
    // alpha too (a transparent background), 0 = alpha 1.
    plane: vec4<u32>,
}

@group(0) @binding(0) var<uniform> p: Params;
@group(0) @binding(1) var<storage, read_write> dst_color: array<vec4<f32>>;
@group(0) @binding(2) var<storage, read_write> dst_depth: array<f32>;
@group(0) @binding(3) var<storage, read> src_color: array<vec4<f32>>;
@group(0) @binding(4) var<storage, read> src_depth: array<f32>;

// dst += src; the nearer depth kept.
@compute @workgroup_size(64)
fn add(@builtin(global_invocation_id) id: vec3<u32>) {
    let i = id.x + id.y * 65535u * 64u;
    if (i >= p.plane.x) {
        return;
    }
    dst_color[i] = dst_color[i] + src_color[i];
    dst_depth[i] = min(dst_depth[i], src_depth[i]);
}

// dst = the average: the sum times one over the count; alpha averaged too
// for a transparent background, else 1.
@compute @workgroup_size(64)
fn scale(@builtin(global_invocation_id) id: vec3<u32>) {
    let i = id.x + id.y * 65535u * 64u;
    if (i >= p.plane.x) {
        return;
    }
    let inv = bitcast<f32>(p.plane.y);
    let s = dst_color[i];
    var a = 1.0;
    if (p.plane.z != 0u) {
        a = s.w * inv;
    }
    dst_color[i] = vec4<f32>(s.x * inv, s.y * inv, s.z * inv, a);
}
