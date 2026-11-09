//! Cusp and smooth normals kept from one frame to the next.
//!
//! A polygonal mesh without its own normals is drawn with Houdini's cusp
//! normals ([`raster::cusp_normals`]), worked out from its points and
//! triangles. That is a pass over every triangle and, on a heavy mesh, most
//! of what a one-sample draft costs (274 ms of a 355 ms frame on 4.5 million
//! triangles). A subdivision surface without its own is drawn with smooth
//! normals ([`raster::smooth_normals`]), another pass over its triangles (15
//! ms of a 106 ms draft with 3.3M of them). So a delegate keeps both (in
//! [`crate::ffi::ThCache`]) for the
//! meshes that have a key, while the key's version and the arrays they were
//! made from stay the same. The version changes whenever the shim rebuilds
//! the mesh or moves its points; the arrays' addresses and lengths are
//! checked as well, as the GPU cache's signature does. Meshes a call does not
//! draw are let go, as the GPU cache lets go of theirs.
//!
//! The normals that are not kept are worked out a mesh at a time on the
//! pool's threads, so a scene of many animated meshes (new versions every
//! frame) does not pay for them one after another.

use crate::math::Vec3;
use crate::pool;
use crate::raster::{self, MeshKey};
use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};

/// One mesh that needs normals: its cusp normals, one per corner (a
/// polygonal mesh), or with `smooth` its smooth normals, one per point (a
/// subdivision surface, [`raster::smooth_normals`]).
pub(crate) struct Request<'a> {
    pub key: Option<MeshKey>,
    pub points: &'a [Vec3],
    pub triangles: &'a [[u32; 3]],
    pub smooth: bool,
}

/// What a mesh's normals were made from.
#[derive(Clone, Copy, PartialEq, Eq)]
struct Source {
    version: u64,
    points: (usize, usize),
    triangles: (usize, usize),
    smooth: bool,
}

impl Source {
    fn of(r: &Request) -> Source {
        Source {
            version: r.key.map_or(0, |k| k.version),
            points: (r.points.as_ptr() as usize, r.points.len()),
            triangles: (r.triangles.as_ptr() as usize, r.triangles.len()),
            smooth: r.smooth,
        }
    }
}

struct Kept {
    source: Source,
    normals: Arc<[Vec3]>,
}

/// A delegate's kept normals, by mesh key id.
#[derive(Default)]
pub(crate) struct Cache {
    meshes: HashMap<u64, Kept>,
}

/// What a call did, for TH_PLAYBLAST_TIMING and the tests.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Stats {
    /// Meshes whose normals were worked out, and those taken from the cache.
    pub computed: usize,
    pub kept: usize,
}

impl Cache {
    /// The cusp normals of each request, in order (`None` stays `None`):
    /// kept ones where a keyed mesh's source is unchanged, the rest worked
    /// out now. Afterwards the cache holds exactly the keyed meshes asked
    /// for.
    pub fn normals(&mut self, requests: &[Option<Request>]) -> (Vec<Option<Arc<[Vec3]>>>, Stats) {
        let mut out: Vec<Option<Arc<[Vec3]>>> = Vec::with_capacity(requests.len());
        let mut missing = Vec::new();
        for (i, r) in requests.iter().enumerate() {
            let kept = r.as_ref().and_then(|r| {
                let id = r.key?.id;
                self.meshes.get(&id).filter(|k| k.source == Source::of(r)).map(|k| k.normals.clone())
            });
            if kept.is_none() && r.is_some() {
                missing.push(i);
            }
            out.push(kept);
        }
        let stats = Stats { computed: missing.len(), kept: out.iter().filter(|n| n.is_some()).count() };
        for (i, normals) in compute(requests, &missing) {
            out[i] = Some(normals);
        }
        // Keep what this call drew, nothing else.
        let mut meshes = HashMap::with_capacity(self.meshes.len());
        for (r, n) in requests.iter().zip(&out) {
            if let (Some(r), Some(n)) = (r, n) {
                if let Some(key) = r.key {
                    meshes.insert(key.id, Kept { source: Source::of(r), normals: n.clone() });
                }
            }
        }
        self.meshes = meshes;
        (out, stats)
    }

    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.meshes.len()
    }
}

/// The normals of `requests[i]` for each `i` in `which`, a mesh at a time
/// on the pool's threads (on this one alone for a single mesh).
fn compute(requests: &[Option<Request>], which: &[usize]) -> Vec<(usize, Arc<[Vec3]>)> {
    let one = |i: usize| -> Arc<[Vec3]> {
        let r = requests[i].as_ref().expect("only requests are computed");
        if r.smooth {
            raster::smooth_normals(r.points, r.triangles).into()
        } else {
            raster::cusp_normals(r.points, r.triangles, raster::CUSP_DEGREES).into()
        }
    };
    if which.len() <= 1 {
        return which.iter().map(|&i| (i, one(i))).collect();
    }
    let next = AtomicUsize::new(0);
    let done = Mutex::new(Vec::with_capacity(which.len()));
    pool::each_thread(&|| loop {
        let n = next.fetch_add(1, Ordering::Relaxed);
        let Some(&i) = which.get(n) else { break };
        let normals = one(i);
        done.lock().unwrap_or_else(PoisonError::into_inner).push((i, normals));
    });
    done.into_inner().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cube() -> (Vec<Vec3>, Vec<[u32; 3]>) {
        let p: Vec<Vec3> = (0..8).map(|i| [(i & 1) as f32, ((i >> 1) & 1) as f32, ((i >> 2) & 1) as f32]).collect();
        let quads = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]];
        let t = quads.iter().flat_map(|q| [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]).collect();
        (p, t)
    }

    fn key(id: u64, version: u64) -> Option<MeshKey> {
        Some(MeshKey { id, version })
    }

    #[test]
    fn an_unchanged_mesh_keeps_its_normals_and_a_new_version_recomputes() {
        let (mut points, triangles) = cube();
        let mut cache = Cache::default();
        fn ask<'a>(points: &'a [Vec3], triangles: &'a [[u32; 3]], version: u64) -> [Option<Request<'a>>; 1] {
            [Some(Request { key: key(1, version), points, triangles, smooth: false })]
        }
        let (first, s) = cache.normals(&ask(&points, &triangles, 1));
        assert_eq!(s, Stats { computed: 1, kept: 0 });
        let (second, s) = cache.normals(&ask(&points, &triangles, 1));
        assert_eq!(s, Stats { computed: 0, kept: 1 });
        assert!(Arc::ptr_eq(first[0].as_ref().unwrap(), second[0].as_ref().unwrap()));
        // Moved in place: only the version tells.
        for p in points.iter_mut() {
            p[1] *= 3.0;
        }
        let (third, s) = cache.normals(&ask(&points, &triangles, 2));
        assert_eq!(s, Stats { computed: 1, kept: 0 });
        assert_eq!(&*third[0].clone().unwrap(), &raster::cusp_normals(&points, &triangles, raster::CUSP_DEGREES)[..]);
    }

    #[test]
    fn unkeyed_meshes_are_never_kept_and_undrawn_ones_are_let_go() {
        let (points, triangles) = cube();
        let mut cache = Cache::default();
        let r = |k| Some(Request { key: k, points: &points, triangles: &triangles, smooth: false });
        let (_, s) = cache.normals(&[r(None), r(key(1, 1)), None, r(key(2, 1))]);
        assert_eq!((s, cache.len()), (Stats { computed: 3, kept: 0 }, 2));
        let (out, s) = cache.normals(&[r(None), None, r(key(2, 1))]);
        assert_eq!((s, cache.len()), (Stats { computed: 1, kept: 1 }, 1));
        assert!(out[1].is_none() && out[0].is_some() && out[2].is_some());
        let (_, s) = cache.normals(&[r(key(1, 1))]);
        assert_eq!(s, Stats { computed: 1, kept: 0 }, "1 was let go");
    }

    use proptest::prelude::*;

    /// A small random mesh: points on a jittered grid, triangles between
    /// them (some sharing edges at sharp angles, some not).
    fn shape() -> impl Strategy<Value = (Vec<Vec3>, Vec<[u32; 3]>)> {
        (4usize..10).prop_flat_map(|n| {
            (
                prop::collection::vec([-1.0f32..1.0, -1.0f32..1.0, -1.0f32..1.0], n),
                prop::collection::vec([0..n as u32, 0..n as u32, 0..n as u32], 1..12),
            )
        })
    }

    /// What one mesh does in a call: not drawn, drawn without a key, drawn
    /// as it was, or moved in place and drawn with a new version.
    #[derive(Clone, Copy, Debug)]
    enum Step {
        Absent,
        Unkeyed,
        Same,
        Moved(f32),
    }

    fn step() -> impl Strategy<Value = Step> {
        prop_oneof![
            Just(Step::Absent),
            Just(Step::Unkeyed),
            Just(Step::Same),
            (-0.7f32..0.7).prop_map(Step::Moved),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 256, ..ProptestConfig::default() })]

        /// Against a model of the contract: over any run of calls, every
        /// mesh gets its normals worked out from its points as they are now,
        /// in its own slot (several misses are worked out at once, on the
        /// pool); exactly the keyed meshes whose last call drew them at the
        /// same version are kept rather than worked out; and the cache then
        /// holds exactly the keyed meshes the call drew.
        #[test]
        fn kept_normals_follow_the_model(
            mut shapes in prop::collection::vec(shape(), 1..6),
            calls in prop::collection::vec(prop::collection::vec(step(), 6), 1..8),
        ) {
            let mut cache = Cache::default();
            let mut versions = vec![1u64; shapes.len()];
            // The model: each key's version as last drawn, by the last call only.
            let mut drawn: HashMap<u64, u64> = HashMap::new();
            for steps in calls {
                let steps = &steps[..shapes.len()];
                for (m, step) in steps.iter().enumerate() {
                    if let Step::Moved(by) = step {
                        for p in shapes[m].0.iter_mut() {
                            p[1] += by * p[0];
                            p[2] -= by;
                        }
                        versions[m] += 1;
                    }
                }
                let requests: Vec<Option<Request>> = shapes.iter().zip(steps).enumerate().map(|(m, ((p, t), step))| {
                    let key = match step {
                        Step::Absent => return None,
                        Step::Unkeyed => None,
                        _ => key(m as u64 + 1, versions[m]),
                    };
                    Some(Request { key, points: p, triangles: t, smooth: false })
                }).collect();
                let kept_expected = requests.iter().flatten()
                    .filter(|r| r.key.is_some_and(|k| drawn.get(&k.id) == Some(&k.version)))
                    .count();
                let asked = requests.iter().flatten().count();

                let (out, stats) = cache.normals(&requests);

                prop_assert_eq!(stats, Stats { computed: asked - kept_expected, kept: kept_expected });
                for (r, n) in requests.iter().zip(&out) {
                    match (r, n) {
                        (None, None) => {}
                        (Some(r), Some(n)) => {
                            let fresh = raster::cusp_normals(r.points, r.triangles, raster::CUSP_DEGREES);
                            prop_assert!(n[..] == fresh[..], "normals not of the mesh's points as they are now");
                        }
                        _ => prop_assert!(false, "a slot came back filled or empty against its request"),
                    }
                }
                drawn = requests.iter().flatten().filter_map(|r| r.key.map(|k| (k.id, k.version))).collect();
                prop_assert_eq!(cache.len(), drawn.len());
            }
        }
    }
}
