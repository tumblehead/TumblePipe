//! Triangles of a polygon mesh, for the shim (`th_triangulate`).
//!
//! Hydra's `HdMeshUtil::ComputeTriangleIndices` fans every face from its
//! first corner. That is right for a convex face and wrong for a concave
//! one: a fan from a corner that cannot see the whole face lays triangles
//! outside it, so an L-shaped face fills its notch and a keyhole face (a
//! wall with a window cut in, the outer loop bridged to the opening) fills
//! the opening. This keeps the fan for every face it is right for, so the
//! picture of a convex mesh is exactly what it was, and ear-clips the rest
//! in the face's own plane.
//!
//! The output is per triangle: its three *corners* (positions in
//! `faceVertexIndices`, so a face-varying primvar is read straight from
//! them) and its face (for a uniform primvar). Every face of `n >= 3`
//! corners that is not a hole gives `n - 2` triangles, the same count and
//! order of faces as the fan, wound as the face is (reversed for a
//! left-handed mesh, as `HdMeshUtil` does).

use crate::math::Vec3;

/// Above this many corners a concave face is fanned anyway: ear clipping is
/// quadratic in the corners and worse in the reflex ones, and a face this
/// big is a cap or a ground plane, not a wall with a window.
pub const EAR_CLIP_LIMIT: usize = 1024;

/// The triangles of a polygon mesh: `(corners, faces)`, `corners[t]` the
/// three positions in `indices` of triangle `t` and `faces[t]` the face it
/// came from. `holes` lists faces to leave out (USD `holeIndices`); `flip`
/// reverses the winding (a left-handed mesh). None when the topology does
/// not add up: a negative count, or counts that run past `indices`.
pub fn triangulate(
    points: &[Vec3],
    counts: &[i32],
    indices: &[i32],
    holes: &[i32],
    flip: bool,
) -> Option<(Vec<[u32; 3]>, Vec<u32>)> {
    let mut total = 0usize;
    let mut triangles = 0usize;
    for &n in counts {
        let n = usize::try_from(n).ok()?;
        total = total.checked_add(n)?;
        triangles += n.saturating_sub(2);
    }
    if total > indices.len() {
        return None;
    }
    let mut hole = vec![false; counts.len()];
    for &h in holes {
        if let Some(slot) = usize::try_from(h).ok().and_then(|h| hole.get_mut(h)) {
            *slot = true;
        }
    }
    let mut corners = Vec::with_capacity(triangles);
    let mut faces = Vec::with_capacity(triangles);
    let mut start = 0usize;
    let mut face_points = Vec::new();
    for (face, &n) in counts.iter().enumerate() {
        let n = n as usize;
        if n >= 3 && !hole[face] {
            face_points.clear();
            face_points.extend(indices[start..start + n].iter().map(|&i| {
                usize::try_from(i).ok().and_then(|i| points.get(i)).copied()
            }));
            for [a, b, c] in polygon(&face_points) {
                let (b, c) = if flip { (c, b) } else { (b, c) };
                corners.push([(start + a) as u32, (start + b) as u32, (start + c) as u32]);
                faces.push(face as u32);
            }
        }
        start += n;
    }
    Some((corners, faces))
}

/// One face's triangles, as corner numbers within the face, wound as the
/// face is. A triangle, a convex face, a face with a corner that has no
/// point (or a non-finite one), a face too flat to have a plane and a face
/// over [`EAR_CLIP_LIMIT`] corners are fanned from corner 0, as Hydra does;
/// a concave face is ear-clipped. Always `n - 2` triangles for `n >= 3`.
pub fn polygon(points: &[Option<Vec3>]) -> Vec<[usize; 3]> {
    let n = points.len();
    if n < 3 {
        return Vec::new();
    }
    let fan = || (1..n - 1).map(|i| [0, i, i + 1]).collect::<Vec<_>>();
    if n == 3 || n > EAR_CLIP_LIMIT {
        return fan();
    }
    let Some(q) = project(points) else {
        return fan();
    };
    // A scale for "zero": twice-areas below this are degenerate.
    let (mut lo, mut hi) = ([f64::MAX; 2], [f64::MIN; 2]);
    for p in &q {
        for k in 0..2 {
            lo[k] = lo[k].min(p[k]);
            hi[k] = hi[k].max(p[k]);
        }
    }
    let extent = (hi[0] - lo[0]).max(hi[1] - lo[1]);
    let eps = extent * extent * 1e-12;
    let turn = |a: usize, b: usize, c: usize| cross(q[a], q[b], q[c]);
    if (0..n).all(|i| turn((i + n - 1) % n, i, (i + 1) % n) >= -eps) {
        return fan();
    }
    ear_clip(&q, eps)
}

/// The face's corners in its own plane, wound counter-clockwise: the axis
/// the Newell normal leans on most is dropped. None when a corner has no
/// finite point or the face has no plane (zero area).
fn project(points: &[Option<Vec3>]) -> Option<Vec<[f64; 2]>> {
    let p: Vec<[f64; 3]> = points
        .iter()
        .map(|p| {
            let p = (*p)?;
            p.iter().all(|c| c.is_finite()).then(|| p.map(f64::from))
        })
        .collect::<Option<_>>()?;
    let n = p.len();
    let mut normal = [0.0f64; 3];
    for i in 0..n {
        let (a, b) = (p[i], p[(i + 1) % n]);
        normal[0] += (a[1] - b[1]) * (a[2] + b[2]);
        normal[1] += (a[2] - b[2]) * (a[0] + b[0]);
        normal[2] += (a[0] - b[0]) * (a[1] + b[1]);
    }
    let axis = (0..3).max_by(|&i, &j| normal[i].abs().total_cmp(&normal[j].abs()))?;
    if !(normal[axis].abs() > 0.0) {
        return None;
    }
    // The two kept axes in cyclic order make the area positive where the
    // normal's component is; swapped, it is negative, so swap them back.
    let (u, v) = match axis {
        0 => (1, 2),
        1 => (2, 0),
        _ => (0, 1),
    };
    let (u, v) = if normal[axis] > 0.0 { (u, v) } else { (v, u) };
    Some(p.iter().map(|p| [p[u], p[v]]).collect())
}

/// Twice the signed area of triangle `abc`: positive when it turns left.
fn cross(a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

/// Ear clipping of a counter-clockwise polygon. A corner is an ear when it
/// turns left and no other corner lies in or on its triangle. A corner at
/// the same place as one of the triangle's own does not count: a keyhole
/// face visits each end of its bridge twice. With no ear left (a
/// self-crossing face) the corner that turns left most is cut anyway, so
/// the face still gives `n - 2` triangles.
fn ear_clip(q: &[[f64; 2]], eps: f64) -> Vec<[usize; 3]> {
    let mut ring: Vec<usize> = (0..q.len()).collect();
    let mut out = Vec::with_capacity(q.len() - 2);
    let mut k = 0usize;
    while ring.len() > 3 {
        let m = ring.len();
        let corner = |k: usize| (ring[(k + m - 1) % m], ring[k % m], ring[(k + 1) % m]);
        let reflex: Vec<usize> = (0..m)
            .filter(|&j| {
                let (a, b, c) = corner(j);
                cross(q[a], q[b], q[c]) <= eps
            })
            .map(|j| ring[j])
            .collect();
        let is_ear = |j: usize| {
            let (a, b, c) = corner(j);
            if cross(q[a], q[b], q[c]) <= eps {
                return false;
            }
            !reflex.iter().any(|&r| {
                let p = q[r];
                r != a && r != b && r != c && p != q[a] && p != q[b] && p != q[c]
                    && cross(q[a], q[b], p) >= -eps
                    && cross(q[b], q[c], p) >= -eps
                    && cross(q[c], q[a], p) >= -eps
            })
        };
        let at = (0..m).map(|i| (k + i) % m).find(|&j| is_ear(j)).unwrap_or_else(|| {
            (0..m)
                .max_by(|&i, &j| {
                    let (a, b, c) = corner(i);
                    let (d, e, f) = corner(j);
                    cross(q[a], q[b], q[c]).total_cmp(&cross(q[d], q[e], q[f]))
                })
                .unwrap_or(0)
        });
        let (a, b, c) = corner(at);
        out.push([a, b, c]);
        ring.remove(at);
        // Carry on from the corner before the cut: it is the likeliest ear
        // next, which keeps a mostly convex face near linear.
        k = (at + ring.len() - 1) % ring.len();
    }
    out.push([ring[0], ring[1], ring[2]]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn some(points: &[[f32; 2]]) -> Vec<Option<Vec3>> {
        points.iter().map(|p| Some([p[0], p[1], 0.0])).collect()
    }

    /// A convex face keeps Hydra's fan exactly.
    #[test]
    fn a_convex_face_is_fanned() {
        let quad = some(&[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]);
        assert_eq!(polygon(&quad), vec![[0, 1, 2], [0, 2, 3]]);
    }

    /// A dart fanned from corner 0 (convex) fills its notch; the only right
    /// cut is the diagonal from the notch, corner 3, to corner 1.
    #[test]
    fn a_dart_is_cut_from_its_notch() {
        let dart = some(&[[0.0, 1.0], [0.5, 0.0], [1.0, 1.0], [0.5, 0.4]]);
        let tris = polygon(&dart);
        assert_eq!(tris.len(), 2);
        assert!(tris.iter().all(|t| t.contains(&1) && t.contains(&3)), "{tris:?}");
    }

    /// A bad count or counts past the indices refuse; holes and short faces
    /// give no triangles; a left-handed mesh is wound the other way.
    #[test]
    fn topology_is_checked_and_flip_reverses() {
        let pts = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]];
        assert!(triangulate(&pts, &[-1], &[], &[], false).is_none());
        assert!(triangulate(&pts, &[3, 3], &[0, 1, 2], &[], false).is_none());
        let (c, f) = triangulate(&pts, &[3, 2, 3], &[0, 1, 2, 0, 1, 2, 1, 0], &[2], false).unwrap();
        assert_eq!((c, f), (vec![[0, 1, 2]], vec![0]));
        let (c, _) = triangulate(&pts, &[3], &[0, 1, 2], &[], true).unwrap();
        assert_eq!(c, vec![[0, 2, 1]]);
    }
}
