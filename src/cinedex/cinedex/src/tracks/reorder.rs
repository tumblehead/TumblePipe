//! A clip dragged along its own track to a place that is taken: it goes in
//! between the clips there, and the clips it passed make room (Remy,
//! 2026-11-22: a clip dropped between two clips with too small a gap
//! between them refused, then jumped to the nearest free place).
//!
//! The rule, for clip `D` (`L` frames long) dropped with its start asked
//! at `a` (after snapping) among the other clips of its track `S`, in
//! order:
//!
//! 1. **Where in the order.** `D` goes after every clip of `S` whose middle
//!    is before `D`'s middle there: past half of a clip, it is past that
//!    clip. That is slot `k` (between `S[k-1]` and `S[k]`).
//! 2. **Its own slot** (`k` is where it was): nothing else moves; `D` goes
//!    as near `a` as fits between its neighbours (it always fits: it was
//!    there).
//! 3. **Earlier** (`k` before where it was, slot `o`): `D` starts at `a`,
//!    but not before `S[k-1]`'s end and not after `S[k]`'s start; the
//!    clips `S[k..o]` (between there and where `D` was) all move later by
//!    as much as `D` overlaps `S[k]`. That is never more than `L`, so they
//!    move into the room `D` left and nothing past where it was moves.
//! 4. **Later**: the same, mirrored: `D` ends at `a + L`, but not after
//!    `S[k]`'s start nor before `S[k-1]`'s end, and `S[o..k]` all move
//!    earlier by as much as `D` overlaps `S[k-1]`.
//!
//! So a clip dragged among clips that touch swaps into the order with
//! them touching still, the track as long as it was; the gaps between the
//! clips that move stay as they were; and no clip of another track moves.
//! A place that is free is left to [`super::landing`]: this rule is only
//! for a drop on a clip.
//!
//! The editor does it as [`Op::Move`]s ([`rearrange_ops`]), one undo step.

use th_edit::document::{ClipRef, Frame, Source, TrackRef};
use th_edit::ops::Op;

/// Where each clip of a track (`spans`, each `(at, end)`, in order) starts
/// once clip `dragged`, asked to start at `asked`, goes in among the others
/// (see the module's rule); None when `asked` is free for it (nothing else
/// need move: the caller's plain move), or `dragged` is not a clip of it.
pub fn reorder(spans: &[(Frame, Frame)], dragged: usize, asked: Frame) -> Option<Vec<Frame>> {
    let &(at, end) = spans.get(dragged)?;
    let long = end - at;
    let asked = asked.max(0);
    let others: Vec<(usize, Frame, Frame)> =
        spans.iter().enumerate().filter(|&(i, _)| i != dragged).map(|(i, &(a, e))| (i, a, e)).collect();
    if others.iter().all(|&(_, a, e)| asked + long <= a || e <= asked) {
        return None;
    }
    // Doubled, so a middle is a whole number.
    let middle = 2 * asked + long;
    let k = others.iter().filter(|&&(_, a, e)| a + e < middle).count();
    let o = dragged;
    let before = |slot: usize| slot.checked_sub(1).map_or(0, |s| others[s].2);
    let after = |slot: usize| others.get(slot).map(|s| s.1);
    let mut placed: Vec<Frame> = spans.iter().map(|s| s.0).collect();
    if k == o {
        let latest = after(k).map_or(Frame::MAX, |next| next - long);
        placed[dragged] = asked.min(latest).max(before(k));
    } else if k < o {
        let next = others[k].1;
        let start = asked.max(before(k)).min(next);
        let push = (start + long - next).max(0);
        placed[dragged] = start;
        for &(i, a, _) in &others[k..o] {
            placed[i] = a + push;
        }
    } else {
        let last = others[k - 1].2;
        let finish = (asked + long).min(after(k).unwrap_or(Frame::MAX)).max(last);
        let pull = (last - (finish - long)).max(0);
        placed[dragged] = finish - long;
        for &(i, a, _) in &others[o..k] {
            placed[i] = a - pull;
        }
    }
    Some(placed)
}

/// The moves that put the clips of `track` (`spans`, each `(at, end)`, in
/// order) at `placed` (a start for each, in the same order): every clip
/// that changes place is first moved past the end of all of them, then
/// to its place, so no move is ever onto another clip. In the order the
/// editor applies them, each naming its clip by where it is then.
pub fn rearrange_ops(track: TrackRef, spans: &[(Frame, Frame)], placed: &[Frame]) -> Vec<Op<Source>> {
    // Each clip as it is now: (which, at, length), kept in track order.
    let mut now: Vec<(usize, Frame, Frame)> = spans.iter().enumerate().map(|(i, &(a, e))| (i, a, e - a)).collect();
    let changed: Vec<usize> = (0..spans.len()).filter(|&i| placed.get(i).is_some_and(|&p| p != spans[i].0)).collect();
    let ends = spans.iter().map(|s| s.1).chain(changed.iter().map(|&i| placed[i] + spans[i].1 - spans[i].0));
    let mut park = ends.max().unwrap_or(0);
    let mut ops = Vec::with_capacity(2 * changed.len());
    let mut go = |now: &mut Vec<(usize, Frame, Frame)>, which: usize, to: Frame| {
        let index = now.iter().position(|c| c.0 == which).expect("every clip is on the track");
        ops.push(Op::Move { clip: ClipRef { track, index }, to: track, at: to });
        now[index].1 = to;
        now.sort_by_key(|c| c.1);
    };
    for &i in &changed {
        go(&mut now, i, park);
        park += spans[i].1 - spans[i].0;
    }
    for &i in &changed {
        go(&mut now, i, placed[i]);
    }
    ops
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{Track, VideoClip, WorkingEdit};
    use th_edit::ops::{Available, Sources, apply};

    /// A track's clips, each `(at, end)`, in order: gaps of 0 to 11
    /// frames (often none) between clips 1 to 30 long.
    fn spans() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((prop_oneof![Just(0i64), 0i64..12], 1i64..30), 1..7).prop_map(|clips| {
            let mut at = 0;
            clips
                .into_iter()
                .map(|(gap, long)| {
                    at += gap;
                    let span = (at, at + long);
                    at += long;
                    span
                })
                .collect()
        })
    }

    struct AnyFrames;

    impl Sources<Source> for AnyFrames {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    const V1: TrackRef = TrackRef { kind: th_edit::document::TrackKind::Video, index: 0 };

    /// A video track of slugs over `spans`, each named by its index.
    fn edit_of(spans: &[(Frame, Frame)]) -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let clips =
            spans.iter().enumerate().map(|(i, &(at, end))| VideoClip::new(at, end - at, 0, Source::Slug { text: i.to_string() })).collect();
        edit.video.push(Track { name: "V1".into(), clips, color: None });
        edit
    }

    proptest! {
        /// A drop on a clip puts the dragged clip in the slot its middle
        /// is in, on free frames, every other clip in its order and on
        /// free frames; only the clips between where it was and where it
        /// goes move, all by the same frames, never more than its length,
        /// towards the room it left.
        #[test]
        fn a_clip_dropped_on_clips_goes_in_between_them(spans in spans(), dragged in any::<prop::sample::Index>(), asked in -20i64..250) {
            let dragged = dragged.index(spans.len());
            let (at, end) = spans[dragged];
            let long = end - at;
            let Some(placed) = reorder(&spans, dragged, asked) else {
                // Left alone only when the place asked for is free.
                let asked = asked.max(0);
                prop_assert!(spans.iter().enumerate().all(|(i, &(a, e))| i == dragged || asked + long <= a || e <= asked));
                return Ok(());
            };
            prop_assert_eq!(placed.len(), spans.len());
            let mut after: Vec<(Frame, Frame, usize)> = placed.iter().zip(&spans).enumerate().map(|(i, (&p, &(a, e)))| (p, p + e - a, i)).collect();
            after.sort();
            prop_assert!(after[0].0 >= 0, "a clip before frame 0: {:?}", after);
            for pair in after.windows(2) {
                prop_assert!(pair[0].1 <= pair[1].0, "clips overlap: {:?}", after);
            }
            // The others keep their order; the dragged clip is in its slot.
            let order: Vec<usize> = after.iter().map(|c| c.2).filter(|&i| i != dragged).collect();
            prop_assert_eq!(order, (0..spans.len()).filter(|&i| i != dragged).collect::<Vec<_>>());
            let slot = after.iter().position(|c| c.2 == dragged).unwrap();
            let middle = 2 * asked.max(0) + long;
            let k = spans.iter().enumerate().filter(|&(i, &(a, e))| i != dragged && a + e < middle).count();
            prop_assert_eq!(slot, k);
            // Who moved, and by how much.
            let moved: Vec<(usize, Frame)> = (0..spans.len()).filter(|&i| i != dragged && placed[i] != spans[i].0).map(|i| (i, placed[i] - spans[i].0)).collect();
            for &(i, by) in &moved {
                prop_assert!(by.abs() <= long, "clip {} moved {} for a clip {} long", i, by, long);
                if k < dragged {
                    prop_assert!(by > 0 && i < dragged && i >= k);
                } else {
                    prop_assert!(by < 0 && i > dragged && i <= k);
                }
                prop_assert_eq!(by, moved[0].1, "the clips that move keep their gaps");
            }
            // The track never gets longer.
            let last = spans.iter().map(|s| s.1).max().unwrap();
            prop_assert!(after.iter().all(|c| c.1 <= last.max(asked.max(0) + long)));
        }

        /// The moves the editor makes of a reorder are ones the document
        /// takes, every one of them, and leave each clip where the rule
        /// put it.
        #[test]
        fn the_moves_leave_every_clip_where_the_rule_puts_it(spans in spans(), dragged in any::<prop::sample::Index>(), asked in -20i64..250) {
            let dragged = dragged.index(spans.len());
            let Some(placed) = reorder(&spans, dragged, asked) else { return Ok(()) };
            let mut edit = edit_of(&spans);
            for op in rearrange_ops(V1, &spans, &placed) {
                edit = match apply(&edit, &op, &AnyFrames) {
                    Ok(next) => next,
                    Err(e) => return Err(TestCaseError::fail(format!("{op:?} refused: {e}"))),
                };
            }
            for (i, &p) in placed.iter().enumerate() {
                let clip = edit.video[0].clips.iter().find(|c| c.source == Source::Slug { text: i.to_string() }).expect("no clip is lost");
                prop_assert_eq!((clip.at, clip.duration), (p, spans[i].1 - spans[i].0));
            }
        }
    }

    #[test]
    fn a_clip_swaps_in_among_clips_that_touch() {
        // A B C D, ten frames each, touching.
        let spans = [(0, 10), (10, 20), (20, 30), (30, 40)];
        // D dropped over B: A D B C, still touching.
        assert_eq!(reorder(&spans, 3, 10), Some(vec![0, 20, 30, 10]));
        // A dropped between C and D (its middle past C's): B C A D.
        assert_eq!(reorder(&spans, 0, 25), Some(vec![20, 0, 10, 30]));
        // B nudged a little over C, not past its middle: it stays first.
        assert_eq!(reorder(&spans, 1, 13), Some(vec![0, 10, 20, 30]));
    }

    #[test]
    fn a_gap_too_short_takes_the_clip_the_others_making_room() {
        // A 0-10, B 14-24, C 40-60: C (20 frames) dropped in the 4-frame
        // gap between A and B goes after A, and B moves along by 16.
        let spans = [(0, 10), (14, 24), (40, 60)];
        assert_eq!(reorder(&spans, 2, 9), Some(vec![0, 30, 10]));
        // Into the free frames after B, where it fits: left to the move.
        assert_eq!(reorder(&spans, 2, 25), None);
    }
}
