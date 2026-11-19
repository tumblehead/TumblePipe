//! What a press or a drag on the tracks picks: a gap, a box of clips, a
//! range of clips.
//!
//! **A gap** is the empty frames of a track before one of its clips: after
//! the clip before it, or from the cut's start for the first clip. The
//! frames after a track's last clip are no gap (nothing follows to close
//! it with). A gap is named by the clip after it. Closing it moves that
//! clip and every clip after it on the track earlier by the gap's length
//! ([`th_edit::ops::Op::Shift`]): no other track moves.
//!
//! **A box** dragged over the tracks picks every clip of the rows it
//! touches that has a frame in the frames it spans.
//!
//! **A range** (Shift+click) runs from the clip clicked last (the anchor)
//! to the clip clicked now. On one track: those two and every clip between
//! them. Across tracks: on the rows of the two and those between them as
//! shown, every clip with a frame in the frames from the start of the
//! earlier of the two to the end of the later (a box around the two).

use th_edit::document::Frame;

/// The gap frame `frame` is in, among a track's clips `spans` (each
/// `(at, end)`, in order): the index of the clip after it, and its frames
/// `from..to`. None on a clip and after the last.
pub fn gap_at(spans: &[(Frame, Frame)], frame: Frame) -> Option<(usize, Frame, Frame)> {
    let after = spans.iter().position(|&(_, end)| frame < end)?;
    let from = after.checked_sub(1).map_or(0, |before| spans[before].1);
    let to = spans[after].0;
    (from <= frame && frame < to).then_some((after, from, to))
}

/// The gap before clip `index` of `spans`: its frames `from..to`; None
/// when the clip touches the one before it (or starts the cut).
pub fn gap_before(spans: &[(Frame, Frame)], index: usize) -> Option<(Frame, Frame)> {
    let to = spans.get(index)?.0;
    let from = index.checked_sub(1).map_or(0, |before| spans[before].1);
    (from < to).then_some((from, to))
}

/// The clips a box picks: on rows `a` to `b` (either way round) of `rows`
/// (each row's clips as `(at, end)`), those with a frame in the frames
/// from `from` to `to` (either way round, both ends in). As (row, index).
pub fn boxed(rows: &[Vec<(Frame, Frame)>], a: usize, b: usize, from: Frame, to: Frame) -> Vec<(usize, usize)> {
    let (top, bottom) = (a.min(b), a.max(b));
    let (first, last) = (from.min(to), from.max(to));
    let mut picked = Vec::new();
    for (r, row) in rows.iter().enumerate().filter(|(r, _)| (top..=bottom).contains(r)) {
        picked.extend(row.iter().enumerate().filter(|(_, (at, end))| *at <= last && first < *end).map(|(i, _)| (r, i)));
    }
    picked
}

/// The clips a range picks, from clip `anchor` to clip `to` (each a row of
/// `rows` and an index in it); see the module's rule. Empty when either is
/// no clip of `rows`.
pub fn range(rows: &[Vec<(Frame, Frame)>], anchor: (usize, usize), to: (usize, usize)) -> Vec<(usize, usize)> {
    let span = |(r, i): (usize, usize)| rows.get(r).and_then(|row| row.get(i)).copied();
    let (Some(one), Some(other)) = (span(anchor), span(to)) else { return Vec::new() };
    if anchor.0 == to.0 {
        return (anchor.1.min(to.1)..=anchor.1.max(to.1)).map(|i| (anchor.0, i)).collect();
    }
    // The last frame of the later clip, not the frame after it.
    boxed(rows, anchor.0, to.0, one.0.min(other.0), one.1.max(other.1) - 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn row() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((0i64..10, 1i64..12), 0..6).prop_map(|clips| {
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

    proptest! {
        /// Frame by frame: a frame is in a gap exactly when no clip is on
        /// it and a clip starts after it; the gap is then every empty
        /// frame around it, and the clip named is the first after it.
        #[test]
        fn a_gap_is_the_empty_frames_before_a_clip(spans in row(), frame in 0i64..90) {
            let on_clip = |f: Frame| spans.iter().any(|&(at, end)| at <= f && f < end);
            let next = spans.iter().position(|&(at, _)| at > frame);
            match gap_at(&spans, frame) {
                None => prop_assert!(on_clip(frame) || next.is_none()),
                Some((after, from, to)) => {
                    prop_assert!(!on_clip(frame));
                    prop_assert_eq!(Some(after), next);
                    prop_assert!(from <= frame && frame < to);
                    prop_assert!((from..to).all(|f| !on_clip(f)), "a clip is in the gap");
                    prop_assert!(from == 0 || on_clip(from - 1), "the gap starts after empty frames");
                    prop_assert!(on_clip(to), "the gap does not end on a clip");
                    prop_assert_eq!(gap_before(&spans, after), Some((from, to)));
                }
            }
        }

        /// Every clip has a gap before it or touches what is before it.
        #[test]
        fn the_gap_before_a_clip_is_up_to_the_clip_before(spans in row(), index in 0usize..7) {
            let on_clip = |f: Frame| spans.iter().any(|&(at, end)| at <= f && f < end);
            match gap_before(&spans, index) {
                None => prop_assert!(index >= spans.len() || spans[index].0 == 0 || on_clip(spans[index].0 - 1)),
                Some((from, to)) => {
                    prop_assert_eq!(to, spans[index].0);
                    prop_assert_eq!(gap_at(&spans, from), Some((index, from, to)));
                }
            }
        }

        /// A box picks a clip exactly when its row is one the box touches
        /// and one of its frames is one the box spans, counted frame by
        /// frame; the same whichever corner the drag began in.
        #[test]
        fn a_box_picks_the_clips_it_touches(
            rows in prop::collection::vec(row(), 1..5), a in 0usize..5, b in 0usize..5, from in 0i64..80, to in 0i64..80,
        ) {
            let picked = boxed(&rows, a, b, from, to);
            for (r, row) in rows.iter().enumerate() {
                for (i, &(at, end)) in row.iter().enumerate() {
                    let in_rows = (a <= r && r <= b) || (b <= r && r <= a);
                    let in_frames = (at..end).any(|f| (from <= f && f <= to) || (to <= f && f <= from));
                    prop_assert_eq!(picked.contains(&(r, i)), in_rows && in_frames, "row {} clip {}", r, i);
                }
            }
            let mut other = boxed(&rows, b, a, to, from);
            other.sort_unstable();
            let mut picked = picked;
            picked.sort_unstable();
            prop_assert_eq!(picked, other);
        }

        /// A range has both its ends in it and is the same from either
        /// end. On one track it is the clips from one to the other and no
        /// clip of another row; across tracks it is every clip, of the
        /// rows from one to the other, with a frame between the start of
        /// the earlier and the end of the later, and no other.
        #[test]
        fn a_range_runs_from_the_anchor_to_the_click(
            rows in prop::collection::vec(row(), 1..5), a in any::<prop::sample::Index>(), b in any::<prop::sample::Index>(),
        ) {
            let all: Vec<(usize, usize)> = rows.iter().enumerate().flat_map(|(r, row)| (0..row.len()).map(move |i| (r, i))).collect();
            prop_assume!(!all.is_empty());
            let (anchor, to) = (*a.get(&all), *b.get(&all));
            let mut picked = range(&rows, anchor, to);
            prop_assert!(picked.contains(&anchor) && picked.contains(&to));
            let (one, other) = (rows[anchor.0][anchor.1], rows[to.0][to.1]);
            for &(r, i) in &all {
                let between_rows = (anchor.0.min(to.0)..=anchor.0.max(to.0)).contains(&r);
                let expected = if anchor.0 == to.0 {
                    r == anchor.0 && (anchor.1.min(to.1)..=anchor.1.max(to.1)).contains(&i)
                } else {
                    let (at, end) = rows[r][i];
                    between_rows && (at..end).any(|f| one.0.min(other.0) <= f && f < one.1.max(other.1))
                };
                prop_assert_eq!(picked.contains(&(r, i)), expected, "row {} clip {}", r, i);
            }
            let mut back = range(&rows, to, anchor);
            back.sort_unstable();
            picked.sort_unstable();
            prop_assert_eq!(picked, back);
            prop_assert_eq!(range(&rows, anchor, (rows.len(), 0)), Vec::new());
        }
    }
}
