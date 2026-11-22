//! Where a clip dragged by its body lands.
//!
//! A drag asks for a place: the clip's own plus the frames the pointer
//! went. Two rules then decide where it lands, in this order:
//!
//! 1. **Snap.** When the dragged clip's first frame, or the frame after its
//!    last, would be within [`SNAP_PIXELS`] of a snap point, it goes on that
//!    point. The snap points are the cut's start (frame 0), the playhead,
//!    every marker, and both ends of every other clip on the rows shown
//!    (not of a clip moving with it). The nearest point wins; at the same
//!    distance, the clip's start snaps rather than its end, then the
//!    earlier point.
//! 2. **Fit.** A clip never lands on another: when the place asked for
//!    (after snapping) would put a moving clip over a clip that stays on
//!    its track, or before frame 0, the move lands at the nearest place
//!    where every moving clip is on free frames. That place always has a
//!    moving clip butted against a staying clip's end, or against the
//!    cut's start. At the same distance either way, the place nearer where
//!    the clip came from wins, then the earlier. There is always such a
//!    place (after the track's last clip, at the latest), so a move is
//!    never refused for want of room, and nothing is ever overwritten.
//!
//! Several clips moved together (a selection) keep their distances: one
//! shift is found for all of them.

use th_edit::document::Frame;

/// How near (pixels) a dragged clip's end comes to a snap point to go on it.
pub const SNAP_PIXELS: f32 = 8.0;

/// A clip that moves: the row it lands on (an index into the staying
/// clips' rows), where it starts now and its length.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Moving {
    pub row: usize,
    pub at: Frame,
    pub duration: Frame,
}

/// Where a move lands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Landing {
    /// The frames every moving clip goes (positive: later).
    pub delta: Frame,
    /// The snap point the dragged clip went on.
    pub snapped: Option<Frame>,
    /// The place asked for was taken: it landed beside it.
    pub bumped: bool,
}

/// Whether every clip of `moving`, moved `delta` frames, is on frames none
/// of `staying` (per row, each `(at, end)`) is on, and not before frame 0.
pub fn fits(moving: &[Moving], staying: &[Vec<(Frame, Frame)>], delta: Frame) -> bool {
    moving.iter().all(|m| {
        let (at, end) = (m.at + delta, m.at + m.duration + delta);
        at >= 0 && staying.get(m.row).is_none_or(|row| row.iter().all(|&(a, e)| end <= a || e <= at))
    })
}

/// `delta` with the dragged clip (`moving[0]`) snapped: the shift that
/// puts its start or its end on the nearest of `points` within `reach`
/// frames, and that point; else `delta` as asked.
pub fn snapped(dragged: Moving, points: &[Frame], reach: Frame, delta: Frame) -> (Frame, Option<Frame>) {
    let (start, end) = (dragged.at + delta, dragged.at + dragged.duration + delta);
    // (how far, the end snapping rather than the start, the point).
    let best = points.iter().flat_map(|&p| [((p - start).abs(), false, p), ((p - end).abs(), true, p)]).filter(|c| c.0 <= reach).min();
    match best {
        Some((_, false, point)) => (delta + point - start, Some(point)),
        Some((_, true, point)) => (delta + point - end, Some(point)),
        None => (delta, None),
    }
}

/// `delta` for a dragged edge (a trim's or a roll's) that is at frame
/// `edge` before the drag, snapped: the shift that puts it on the nearest
/// of `points` within `reach` frames of where `delta` takes it (the
/// earlier point when two are as near), and that point; else `delta` as
/// asked. The edge then still stops where the footage ends
/// ([`super::reach`]), which can take it off the point.
pub fn snapped_edge(edge: Frame, points: &[Frame], reach: Frame, delta: Frame) -> (Frame, Option<Frame>) {
    let to = edge + delta;
    match points.iter().map(|&p| ((p - to).abs(), p)).filter(|c| c.0 <= reach).min() {
        Some((_, point)) => (point - edge, Some(point)),
        None => (delta, None),
    }
}

/// Where `moving` lands when dragged `delta` frames: snapped to `points`
/// within `reach` frames (the dragged clip is `moving[0]`), then fitted
/// among the clips that stay (see the module's rules).
pub fn land(moving: &[Moving], staying: &[Vec<(Frame, Frame)>], points: &[Frame], reach: Frame, delta: Frame) -> Landing {
    let Some(&dragged) = moving.first() else { return Landing { delta, snapped: None, bumped: false } };
    let (asked, point) = snapped(dragged, points, reach, delta);
    if fits(moving, staying, asked) {
        return Landing { delta: asked, snapped: point, bumped: false };
    }
    // Every place where a moving clip is butted against a staying one, or
    // against the cut's start: the nearest free place is one of them.
    let start = moving.iter().map(|m| -m.at);
    let beside =
        moving.iter().flat_map(|m| staying.get(m.row).into_iter().flatten().flat_map(move |&(a, e)| [e - m.at, a - m.at - m.duration]));
    let nearest = start.chain(beside).filter(|&d| fits(moving, staying, d)).min_by_key(|&d| ((d - asked).abs(), d.abs(), d));
    match nearest {
        Some(delta) => Landing { delta, snapped: None, bumped: true },
        // No row to land on has an end: nothing is in the way after all.
        None => Landing { delta: asked, snapped: point, bumped: false },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Rows of clips that stay, each `(at, end)`, in order.
    fn rows() -> impl Strategy<Value = Vec<Vec<(Frame, Frame)>>> {
        let row = prop::collection::vec((0i64..12, 1i64..15), 0..5).prop_map(|gaps| {
            let mut at = 0;
            gaps.into_iter()
                .map(|(gap, long)| {
                    at += gap;
                    let span = (at, at + long);
                    at += long;
                    span
                })
                .collect::<Vec<_>>()
        });
        prop::collection::vec(row, 1..4)
    }

    fn movers() -> impl Strategy<Value = Vec<(prop::sample::Index, Frame, Frame)>> {
        prop::collection::vec((any::<prop::sample::Index>(), 0i64..80, 1i64..15), 1..4)
    }

    proptest! {
        /// A move always lands on free frames, never before frame 0, and
        /// nowhere nearer the place asked for is free: every shift nearer
        /// it, tried one by one, has a moving clip on a staying one. When
        /// the place asked for is free it lands exactly there.
        #[test]
        fn a_move_lands_on_the_nearest_free_place(staying in rows(), movers in movers(), delta in -90i64..90) {
            let moving: Vec<Moving> = movers.iter().map(|(row, at, duration)| Moving { row: row.index(staying.len()), at: *at, duration: *duration }).collect();
            let landing = land(&moving, &staying, &[], 0, delta);
            let free = |d: Frame| moving.iter().all(|m| {
                m.at + d >= 0 && (m.at + d..m.at + d + m.duration).all(|f| !staying[m.row].iter().any(|&(a, e)| a <= f && f < e))
            });
            prop_assert!(free(landing.delta), "landed on a clip: {:?}", landing);
            prop_assert_eq!(landing.snapped, None);
            if free(delta) {
                prop_assert_eq!(landing, Landing { delta, snapped: None, bumped: false });
            } else {
                prop_assert!(landing.bumped);
                let far = (landing.delta - delta).abs();
                for d in delta - far + 1..delta + far {
                    prop_assert!(!free(d), "{} is free and nearer {} than {}", d, delta, landing.delta);
                }
                // The other place as near, if free, is further from where
                // the clip came from (or as far, and later).
                let other = 2 * delta - landing.delta;
                if other != landing.delta && free(other) {
                    prop_assert!((other.abs(), other) > (landing.delta.abs(), landing.delta));
                }
            }
        }

        /// A snap puts the dragged clip's start or end exactly on a point
        /// that was within reach of it, the nearest there is; with no
        /// point in reach of either end, the drag is as asked.
        #[test]
        fn a_snap_goes_on_the_nearest_point_in_reach(
            at in 0i64..100, duration in 1i64..40, points in prop::collection::vec(0i64..200, 0..6), reach in 0i64..8, delta in -60i64..60,
        ) {
            let dragged = Moving { row: 0, at, duration };
            let (to, point) = snapped(dragged, &points, reach, delta);
            let (start, end) = (at + delta, at + duration + delta);
            let far = |p: Frame| (p - start).abs().min((p - end).abs());
            let nearest = points.iter().map(|&p| far(p)).min();
            match point {
                None => {
                    prop_assert_eq!(to, delta);
                    prop_assert!(nearest.is_none_or(|n| n > reach));
                }
                Some(p) => {
                    prop_assert!(points.contains(&p));
                    prop_assert_eq!(Some(far(p)), nearest, "a nearer point was passed over");
                    prop_assert!(far(p) <= reach);
                    prop_assert_eq!((to - delta).abs(), far(p), "it moved further than to the point");
                    prop_assert!(at + to == p || at + duration + to == p);
                    // The start rather than the end, when both are as near.
                    if (p - start).abs() == (p - end).abs() || points.iter().any(|&q| (q - start).abs() == far(p)) {
                        prop_assert!(points.contains(&(at + to)), "the end snapped though the start was as near");
                    }
                }
            }
        }

        /// A snap onto frames that are taken still lands free: the fit
        /// comes after the snap.
        #[test]
        fn a_snap_never_lands_on_a_clip(staying in rows(), at in 0i64..80, duration in 1i64..15, delta in -60i64..60, reach in 0i64..6) {
            let moving = [Moving { row: 0, at, duration }];
            let points: Vec<Frame> = staying.iter().flatten().flat_map(|&(a, e)| [a, e]).chain([0]).collect();
            let landing = land(&moving, &staying, &points, reach, delta);
            prop_assert!(fits(&moving, &staying, landing.delta));
            let (asked, point) = snapped(moving[0], &points, reach, delta);
            if fits(&moving, &staying, asked) {
                prop_assert_eq!(landing, Landing { delta: asked, snapped: point, bumped: false });
            }
        }
    }

    proptest! {
        /// A dragged edge goes on the nearest point within reach of where
        /// the pointer takes it, the earlier of two as near; with none in
        /// reach it goes as far as the pointer.
        #[test]
        fn an_edge_snaps_to_the_nearest_point_in_reach(
            edge in 0i64..200, points in prop::collection::vec(0i64..300, 0..6), reach in 0i64..8, delta in -100i64..100,
        ) {
            let (to, point) = snapped_edge(edge, &points, reach, delta);
            let asked = edge + delta;
            let nearest = points.iter().map(|&p| (p - asked).abs()).min();
            match point {
                None => {
                    prop_assert_eq!(to, delta);
                    prop_assert!(nearest.is_none_or(|n| n > reach));
                }
                Some(p) => {
                    prop_assert_eq!(edge + to, p);
                    prop_assert!(points.contains(&p));
                    prop_assert_eq!(Some((p - asked).abs()), nearest);
                    prop_assert!((p - asked).abs() <= reach);
                    prop_assert!(!points.iter().any(|&q| q < p && (q - asked).abs() == (p - asked).abs()), "an earlier point was as near");
                }
            }
        }
    }

    #[test]
    fn the_gif_s_drop_lands_against_the_clip_it_was_refused_on() {
        // Remy's clip, 100 frames at 400, dropped 46 frames earlier over
        // the clip at 200 to 399 ("frames 354 to 453 are taken"): it lands
        // butted against that clip's end.
        let moving = [Moving { row: 0, at: 400, duration: 100 }];
        let staying = vec![vec![(0, 200), (200, 400)]];
        assert_eq!(land(&moving, &staying, &[], 0, -46), Landing { delta: 0, snapped: None, bumped: true });
        // Dropped in a gap too short for it, nearer its far side.
        let staying = vec![vec![(0, 100), (150, 300)]];
        let moving = [Moving { row: 0, at: 400, duration: 100 }];
        assert_eq!(land(&moving, &staying, &[], 0, -290).delta, -100);
    }

    #[test]
    fn as_near_either_way_it_lands_nearer_where_it_came_from() {
        // Four frames dropped at 13, over the clip at 10 to 19: frame 6
        // before it and frame 20 after it are as near.
        let staying = vec![vec![(10, 20)]];
        let from_after = [Moving { row: 0, at: 30, duration: 4 }];
        assert_eq!(land(&from_after, &staying, &[], 0, -17), Landing { delta: -10, snapped: None, bumped: true });
        let from_before = [Moving { row: 0, at: 0, duration: 4 }];
        assert_eq!(land(&from_before, &staying, &[], 0, 13), Landing { delta: 6, snapped: None, bumped: true });
    }
}
