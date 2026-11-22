//! An Alt+drag along an empty stretch of a video track sweeps frames for a
//! new clip (Remy's RFE, 2026-11-22): let go, and the editor offers a new
//! shot of that length there, or a placeholder (`editor/range_shot.rs`).
//!
//! Alt, because a plain drag from empty track is the box that picks clips
//! and Alt on a clip is its slip or roll: Alt on empty track did nothing.
//! The sweep runs from the frame pressed to the frame under the pointer,
//! both whole, within the gap pressed in: it never covers a clip and never
//! starts before frame 0, so what it offers always fits where it was drawn.

use th_edit::document::Frame;

/// The frames an Alt+drag sweeps, pressed over edit frame `from` and now
/// over `to` (the fractional frames under the pointer), among the track's
/// clips `spans` (each `(at, end)`, end exclusive): `(at, end)`, end
/// exclusive, from the frame pressed to the frame under the pointer, held
/// within the empty frames around the press. None when the press is on a
/// clip or before frame 0.
pub fn swept(spans: &[(Frame, Frame)], from: f64, to: f64) -> Option<(Frame, Frame)> {
    if !from.is_finite() || !to.is_finite() || from < 0.0 {
        return None;
    }
    let press = from.floor() as Frame;
    if spans.iter().any(|&(a, e)| a <= press && press < e) {
        return None;
    }
    let low = spans.iter().map(|&(_, e)| e).filter(|&e| e <= press).max().unwrap_or(0).max(0);
    let high = spans.iter().map(|&(a, _)| a).filter(|&a| a > press).min();
    let mut now = (to.floor().clamp(-1e15, 1e15) as Frame).max(low);
    if let Some(high) = high {
        now = now.min(high - 1);
    }
    Some((press.min(now), press.max(now) + 1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A track's clips, in order, from (gap before, length) pairs.
    fn spans() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((0i64..20, 1i64..30), 0..5).prop_map(|parts| {
            let mut at = 0;
            parts
                .into_iter()
                .map(|(gap, len)| {
                    at += gap;
                    let span = (at, at + len);
                    at += len;
                    span
                })
                .collect()
        })
    }

    proptest! {
        /// A sweep holds the frame pressed, covers no clip, starts at frame
        /// 0 or later, and is exactly the frames between the press and the
        /// pointer whenever the pointer is in the same gap; a press on a
        /// clip sweeps nothing.
        #[test]
        fn a_sweep_stays_in_its_gap(spans in spans(), from in -5f64..200.0, to in -50f64..300.0) {
            let got = swept(&spans, from, to);
            let press = from.floor() as Frame;
            let on_clip = spans.iter().any(|&(a, e)| a <= press && press < e);
            if from < 0.0 || on_clip {
                prop_assert_eq!(got, None);
                return Ok(());
            }
            let (at, end) = got.expect("a sweep off the clips");
            prop_assert!(at >= 0 && at <= press && press < end);
            for &(a, e) in &spans {
                prop_assert!(end <= a || at >= e, "{:?} covers the clip {:?}", (at, end), (a, e));
            }
            let point = to.floor() as Frame;
            let same_gap = point >= 0 && spans.iter().all(|&(a, e)| a > press.max(point) || e <= press.min(point));
            if same_gap {
                prop_assert_eq!((at, end), (press.min(point), press.max(point) + 1));
            }
        }

        /// Dragging further from the press never sweeps fewer frames.
        #[test]
        fn a_longer_drag_sweeps_no_less(spans in spans(), from in 0f64..200.0, to in 0f64..300.0, more in 0f64..50.0) {
            let further = if to >= from { to + more } else { to - more };
            if let (Some(a), Some(b)) = (swept(&spans, from, to), swept(&spans, from, further)) {
                prop_assert!(b.1 - b.0 >= a.1 - a.0);
            }
        }
    }

    #[test]
    fn a_sweep_after_the_last_clip_runs_on() {
        assert_eq!(swept(&[(0, 10)], 12.5, 60.2), Some((12, 61)));
        assert_eq!(swept(&[(0, 10)], 12.5, 3.0), Some((10, 13)));
        assert_eq!(swept(&[(0, 10), (20, 30)], 15.0, 99.0), Some((15, 20)));
        assert_eq!(swept(&[(0, 10)], 5.0, 20.0), None);
    }
}
