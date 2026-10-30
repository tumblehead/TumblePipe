//! The little vector maths the rasteriser needs.
//!
//! Matrices follow USD's convention: row-major, row vectors, a point
//! transforms as `p * M` and `M1 * M2` applies M1 first.

pub type Vec3 = [f32; 3];
pub type Mat4 = [f64; 16];

pub fn add(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

pub fn sub(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

pub fn scale(a: Vec3, s: f32) -> Vec3 {
    [a[0] * s, a[1] * s, a[2] * s]
}

pub fn dot(a: Vec3, b: Vec3) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub fn cross(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

pub fn normalize(a: Vec3) -> Vec3 {
    let len = dot(a, a).sqrt();
    if len > 1e-20 {
        scale(a, 1.0 / len)
    } else {
        [0.0, 0.0, 1.0]
    }
}

/// `a * b` in USD's row-vector convention (a applies first).
pub fn mul(a: &Mat4, b: &Mat4) -> Mat4 {
    let mut out = [0.0; 16];
    for r in 0..4 {
        for c in 0..4 {
            out[r * 4 + c] = (0..4).map(|k| a[r * 4 + k] * b[k * 4 + c]).sum();
        }
    }
    out
}

/// `[p, 1] * m` as homogeneous coordinates.
pub fn transform_point4(p: Vec3, m: &Mat4) -> [f64; 4] {
    let (x, y, z) = (p[0] as f64, p[1] as f64, p[2] as f64);
    let mut out = [0.0; 4];
    for (c, slot) in out.iter_mut().enumerate() {
        *slot = x * m[c] + y * m[4 + c] + z * m[8 + c] + m[12 + c];
    }
    out
}

/// `[p, 1] * m`, divided through.
pub fn transform_point(p: Vec3, m: &Mat4) -> Vec3 {
    let h = transform_point4(p, m);
    let w = if h[3].abs() > 1e-30 { h[3] } else { 1.0 };
    [(h[0] / w) as f32, (h[1] / w) as f32, (h[2] / w) as f32]
}

/// The 3x3 that carries normals through `m`: the inverse transpose of its
/// upper-left block, laid out so a normal transforms as `n * N` like a point.
pub fn normal_matrix(m: &Mat4) -> [f32; 9] {
    let a = |r: usize, c: usize| m[r * 4 + c];
    let det = a(0, 0) * (a(1, 1) * a(2, 2) - a(1, 2) * a(2, 1))
        - a(0, 1) * (a(1, 0) * a(2, 2) - a(1, 2) * a(2, 0))
        + a(0, 2) * (a(1, 0) * a(2, 1) - a(1, 1) * a(2, 0));
    let inv_det = if det.abs() > 1e-30 { 1.0 / det } else { 0.0 };
    // cofactor(r, c) / det is inverse(c, r); the transpose of the inverse is
    // therefore cofactor(r, c) / det, which is what a row vector needs.
    let cof = |r: usize, c: usize| {
        let rows: Vec<usize> = (0..3).filter(|&i| i != r).collect();
        let cols: Vec<usize> = (0..3).filter(|&i| i != c).collect();
        let minor = a(rows[0], cols[0]) * a(rows[1], cols[1])
            - a(rows[0], cols[1]) * a(rows[1], cols[0]);
        if (r + c) % 2 == 0 { minor } else { -minor }
    };
    let mut out = [0.0f32; 9];
    for r in 0..3 {
        for c in 0..3 {
            out[r * 3 + c] = (cof(r, c) * inv_det) as f32;
        }
    }
    out
}

pub fn transform_normal(n: Vec3, m: &[f32; 9]) -> Vec3 {
    [
        n[0] * m[0] + n[1] * m[3] + n[2] * m[6],
        n[0] * m[1] + n[1] * m[4] + n[2] * m[7],
        n[0] * m[2] + n[1] * m[5] + n[2] * m[8],
    ]
}

pub const IDENTITY: Mat4 = [
    1.0, 0.0, 0.0, 0.0, //
    0.0, 1.0, 0.0, 0.0, //
    0.0, 0.0, 1.0, 0.0, //
    0.0, 0.0, 0.0, 1.0,
];

#[cfg(test)]
mod tests {
    use super::*;

    fn translate(x: f64, y: f64, z: f64) -> Mat4 {
        let mut m = IDENTITY;
        m[12] = x;
        m[13] = y;
        m[14] = z;
        m
    }

    fn scale_m(x: f64, y: f64, z: f64) -> Mat4 {
        let mut m = IDENTITY;
        m[0] = x;
        m[5] = y;
        m[10] = z;
        m
    }

    #[test]
    fn row_vector_order_applies_left_first() {
        let m = mul(&scale_m(2.0, 2.0, 2.0), &translate(1.0, 0.0, 0.0));
        assert_eq!(transform_point([1.0, 0.0, 0.0], &m), [3.0, 0.0, 0.0]);
    }

    #[test]
    fn normals_survive_non_uniform_scale() {
        // A 45 degree normal on a plane stretched 4x along x leans towards y.
        let n = normalize(transform_normal(
            normalize([1.0, 1.0, 0.0]),
            &normal_matrix(&scale_m(4.0, 1.0, 1.0)),
        ));
        assert!(n[1] > n[0] * 3.9, "{n:?}");
    }
}
