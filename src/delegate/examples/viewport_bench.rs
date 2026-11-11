//! How long TumbleView's core takes over a viewer frame, measured through
//! the C ABI the shim calls (`th_render_ids`, with a `ThCache`, ids and a
//! transparent background, as Solaris's viewer asks for them), while the
//! camera orbits a generated scene.
//!
//! ```text
//! cargo run --release --example viewport_bench [--features gpu] -- \
//!     [--size 1920x1080] [--frames 12] [--scene medium] [--gpu] [--only draft|full] [--no-ids]
//! ```
//!
//! Scenes: `small` (~0.3M triangles), `medium` (~4.5M, 6.5M drawn with
//! instances), `large` (~13M, 20M drawn). Each line is one setting: the
//! draft (1 sample, no occlusion) the viewer shows while the camera moves,
//! and the full frame (8 samples and occlusion) it settles on. Set
//! TH_PLAYBLAST_TIMING=1 for the core's own stage timings; --no-ids leaves the
//! picking ids out (a viewer with no id buffer bound).

use std::time::Instant;
use th_delegate_core::ffi::{
    th_cache_create, th_cache_free, th_render_ids, ThFrame, ThMeshView,
};
use th_delegate_core::math::{self, Mat4};

struct MeshData {
    points: Vec<f32>,
    triangles: Vec<u32>,
    colors: Option<Vec<f32>>,
    transforms: Vec<f64>,
    polygonal: bool,
    base: [f32; 3],
}

fn sphere(segments: u32, rings: u32, radius: f32) -> (Vec<f32>, Vec<u32>) {
    let mut points = Vec::new();
    for r in 0..=rings {
        let v = r as f32 / rings as f32 * std::f32::consts::PI;
        for s in 0..segments {
            let u = s as f32 / segments as f32 * std::f32::consts::TAU;
            points.extend([radius * v.sin() * u.cos(), radius * v.cos(), radius * v.sin() * u.sin()]);
        }
    }
    let mut tris = Vec::new();
    for r in 0..rings {
        for s in 0..segments {
            let a = r * segments + s;
            let b = r * segments + (s + 1) % segments;
            let c = a + segments;
            let d = b + segments;
            tris.extend([a, c, b, b, c, d]);
        }
    }
    (points, tris)
}

/// A box of `n x n` quads a face, corners shared within a face only.
fn boxed(n: u32, size: f32) -> (Vec<f32>, Vec<u32>) {
    let mut points = Vec::new();
    let mut tris = Vec::new();
    let axes: [([f32; 3], [f32; 3], [f32; 3]); 6] = [
        ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]),
        ([-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]),
        ([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]),
        ([0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]),
        ([1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]),
        ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]),
    ];
    for (u, v, w) in axes {
        let base = (points.len() / 3) as u32;
        for j in 0..=n {
            for i in 0..=n {
                let (a, b) = (i as f32 / n as f32 - 0.5, j as f32 / n as f32 - 0.5);
                for k in 0..3 {
                    points.push(size * (u[k] * a + v[k] * b + w[k] * 0.5));
                }
            }
        }
        for j in 0..n {
            for i in 0..n {
                let p = base + j * (n + 1) + i;
                tris.extend([p, p + 1, p + n + 1, p + 1, p + n + 2, p + n + 1]);
            }
        }
    }
    (points, tris)
}

fn grid(n: u32, size: f32) -> (Vec<f32>, Vec<u32>) {
    let mut points = Vec::new();
    for j in 0..=n {
        for i in 0..=n {
            let (x, z) = (i as f32 / n as f32 - 0.5, j as f32 / n as f32 - 0.5);
            points.extend([x * size, 0.15 * ((x * 23.0).sin() + (z * 17.0).cos()), z * size]);
        }
    }
    let mut tris = Vec::new();
    for j in 0..n {
        for i in 0..n {
            let p = j * (n + 1) + i;
            tris.extend([p, p + n + 1, p + 1, p + 1, p + n + 1, p + n + 2]);
        }
    }
    (points, tris)
}

fn translate_scale(x: f64, y: f64, z: f64, s: f64, yaw: f64) -> [f64; 16] {
    let (c, si) = (yaw.cos() * s, yaw.sin() * s);
    [c, 0.0, -si, 0.0, 0.0, s, 0.0, 0.0, si, 0.0, c, 0.0, x, y, z, 1.0]
}

/// A tiny deterministic random number generator.
struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> f64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (self.0 >> 11) as f64 / (1u64 << 53) as f64
    }
}

fn corner_colors(tris: usize, rng: &mut Lcg) -> Vec<f32> {
    let c = [rng.next() as f32, rng.next() as f32, rng.next() as f32];
    (0..tris * 3).flat_map(|k| [c[0], c[1] * (k % 7) as f32 / 7.0, c[2]]).collect()
}

fn scene(kind: &str) -> Vec<MeshData> {
    let (spheres, boxes, rocks, ground, sphere_res) = match kind {
        "small" => (20, 10, 200, 100, (64, 32)),
        "large" => (400, 150, 6000, 800, (160, 80)),
        _ => (200, 50, 2000, 400, (128, 64)),
    };
    let mut rng = Lcg(7);
    let mut out = Vec::new();
    let (p, t) = grid(ground, 120.0);
    out.push(MeshData { points: p, triangles: t, colors: None, transforms: math::IDENTITY.to_vec(),
                        polygonal: true, base: [0.4, 0.45, 0.35] });
    let (sp, st) = sphere(sphere_res.0, sphere_res.1, 1.0);
    for _ in 0..spheres {
        let colors = (rng.next() < 0.5).then(|| corner_colors(st.len() / 3, &mut rng));
        let xf = translate_scale(rng.next() * 80.0 - 40.0, 1.0 + rng.next() * 3.0, rng.next() * 80.0 - 40.0,
                                 0.5 + rng.next() * 2.0, rng.next() * 6.0);
        out.push(MeshData { points: sp.clone(), triangles: st.clone(), colors, transforms: xf.to_vec(),
                            polygonal: false, base: [0.6, 0.5, 0.4] });
    }
    let (bp, bt) = boxed(40, 2.0);
    for _ in 0..boxes {
        let xf = translate_scale(rng.next() * 80.0 - 40.0, 1.0, rng.next() * 80.0 - 40.0, 0.5 + rng.next() * 3.0,
                                 rng.next() * 6.0);
        out.push(MeshData { points: bp.clone(), triangles: bt.clone(), colors: None, transforms: xf.to_vec(),
                            polygonal: true, base: [0.7, 0.7, 0.75] });
    }
    // One instanced rock.
    let (rp, rt) = sphere(32, 16, 0.4);
    let mut xfs = Vec::new();
    for _ in 0..rocks {
        xfs.extend(translate_scale(rng.next() * 110.0 - 55.0, 0.2, rng.next() * 110.0 - 55.0,
                                   0.3 + rng.next(), rng.next() * 6.0));
    }
    out.push(MeshData { points: rp, triangles: rt, colors: None, transforms: xfs, polygonal: true,
                        base: [0.5, 0.48, 0.45] });
    out
}

fn camera(angle: f64, w: usize, h: usize) -> ([f64; 16], [f64; 16]) {
    let eye = [55.0 * angle.cos(), 18.0, 55.0 * angle.sin()];
    let target = [0.0, 1.0, 0.0];
    let norm = |v: [f64; 3]| {
        let l = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        [v[0] / l, v[1] / l, v[2] / l]
    };
    let cross = |a: [f64; 3], b: [f64; 3]| [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    let back = norm([eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]);
    let right = norm(cross([0.0, 1.0, 0.0], back));
    let up = cross(back, right);
    let view_to_world: Mat4 = [
        right[0], right[1], right[2], 0.0, up[0], up[1], up[2], 0.0, back[0], back[1], back[2], 0.0, eye[0], eye[1],
        eye[2], 1.0,
    ];
    let dot = |a: [f64; 3], b: [f64; 3]| a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    let world_to_view: Mat4 = [
        right[0], up[0], back[0], 0.0, right[1], up[1], back[1], 0.0, right[2], up[2], back[2], 0.0,
        -dot(right, eye), -dot(up, eye), -dot(back, eye), 1.0,
    ];
    let (n, f) = (0.1f64, 1000.0f64);
    let fy = 1.0 / (25f64.to_radians()).tan();
    let fx = fy * h as f64 / w as f64;
    let proj: Mat4 = [
        fx, 0.0, 0.0, 0.0, 0.0, fy, 0.0, 0.0, 0.0, 0.0, -(f + n) / (f - n), -1.0, 0.0, 0.0, -2.0 * f * n / (f - n), 0.0,
    ];
    (math::mul(&world_to_view, &proj), view_to_world)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let arg = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned();
    let (w, h) = arg("--size")
        .and_then(|s| s.split_once('x').map(|(a, b)| (a.parse().unwrap(), b.parse().unwrap())))
        .unwrap_or((1920usize, 1080usize));
    let frames: usize = arg("--frames").map_or(12, |f| f.parse().unwrap());
    let kind = arg("--scene").unwrap_or_else(|| "medium".into());
    let only = arg("--only");
    let want_ids = !args.iter().any(|a| a == "--no-ids");
    let gpu = args.iter().any(|a| a == "--gpu");

    let built = Instant::now();
    let meshes = scene(&kind);
    let views: Vec<ThMeshView> = meshes
        .iter()
        .enumerate()
        .map(|(i, m)| ThMeshView {
            points: m.points.as_ptr(),
            point_count: (m.points.len() / 3) as u32,
            triangles: m.triangles.as_ptr(),
            triangle_count: (m.triangles.len() / 3) as u32,
            corner_normals: std::ptr::null(),
            corner_colors: m.colors.as_ref().map_or(std::ptr::null(), |c| c.as_ptr()),
            corner_uvs: std::ptr::null(),
            base_color: m.base,
            tiles: std::ptr::null(),
            tile_count: 0,
            transforms: m.transforms.as_ptr(),
            transform_count: (m.transforms.len() / 16) as u32,
            polygonal: m.polygonal as u32,
            key: i as u64 + 1,
            version: 1,
            points_close: std::ptr::null(),
            transform_keys: std::ptr::null(),
            transform_key_count: 0,
            transparency: 0.0, cull: 0,
        })
        .collect();
    let tris: usize = meshes.iter().map(|m| m.triangles.len() / 3).sum();
    let drawn: usize = meshes.iter().map(|m| m.triangles.len() / 3 * m.transforms.len() / 16).sum();
    eprintln!("scene {kind}: {} meshes, {:.2}M triangles ({:.2}M drawn), built in {} ms; {w}x{h}",
              views.len(), tris as f64 / 1e6, drawn as f64 / 1e6, built.elapsed().as_millis());

    let cache = th_cache_create();
    let mut color = vec![0.0f32; w * h * 4];
    let mut depth = vec![0.0f32; w * h];
    let mut ids = vec![0i32; w * h * 2];
    for (label, samples, ao) in [("draft", 1u32, 0u32), ("full", 8, 1)] {
        if only.as_deref().is_some_and(|o| o != label) {
            continue;
        }
        let mut times = Vec::new();
        for k in 0..frames + 1 {
            let (world_to_clip, view_to_world) = camera(k as f64 * 0.05, w, h);
            let frame = ThFrame {
                width: w as u32, height: h as u32, world_to_clip, view_to_world, samples, shading: 0, ao,
                blur_samples: 0, motion: 0, camera_keys: 0, cameras: [0.0; 64], focal_length: 0.0, f_stop: 0.0,
                focus_distance: 0.0, alpha: 1, gpu: gpu as u32, linear_depth: 0,
                lights: std::ptr::null(), light_count: 0, lighting: 0,
            };
            let started = Instant::now();
            let status = unsafe {
                th_render_ids(&frame, views.as_ptr(), views.len() as u32, color.as_mut_ptr(), depth.as_mut_ptr(),
                              if want_ids { ids.as_mut_ptr() } else { std::ptr::null_mut() }, std::ptr::null(), cache)
            };
            assert_eq!(status, 0, "the core failed");
            let ms = started.elapsed().as_secs_f64() * 1000.0;
            if k > 0 {
                times.push(ms); // the first frame fills the caches
            } else {
                eprintln!("{label}: first frame {ms:.1} ms");
            }
        }
        times.sort_by(f64::total_cmp);
        let covered = depth.iter().filter(|&&d| d < 1.0).count();
        eprintln!("{label}: median {:.1} ms, min {:.1}, max {:.1} over {} frames ({:.0}% of pixels covered)",
                  times[times.len() / 2], times[0], times[times.len() - 1], times.len(),
                  100.0 * covered as f64 / (w * h) as f64);
    }
    unsafe { th_cache_free(cache) };
}
