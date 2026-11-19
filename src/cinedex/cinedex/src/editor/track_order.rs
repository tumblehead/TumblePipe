//! What is the user's own about a track goes with the track when the
//! tracks are put in another order.
//!
//! A row's height is remembered by the track's place (V1, V2, A1) and a
//! solo names a track by its place, so when a track moves up or down (and
//! when that is undone or redone) both must move with it: the tall row
//! stays the track it was made tall for, and the soloed track stays the
//! one shown. [`moved`] finds where each track went by comparing the edit
//! before and after: only an edit that is the same tracks in another order
//! counts. Anything else (a clip changed, a track added, removed or
//! renamed) moves nothing here.

use th_edit::document::{TrackKind, TrackRef};

use super::{Editor, track_rows::TrackLayout};

/// Where each of `before` is in `after`, when `after` is `before` in
/// another order: `moved(..)[i]` is the new index of what was at `i`. None
/// when they are in the same order, or are not the same things. Of things
/// alike, one that is where it was stays there.
pub(crate) fn moved<T: PartialEq>(before: &[T], after: &[T]) -> Option<Vec<usize>> {
    if before.len() != after.len() || before == after {
        return None;
    }
    let mut to: Vec<Option<usize>> = (0..before.len()).map(|i| (before[i] == after[i]).then_some(i)).collect();
    let mut taken: Vec<bool> = to.iter().map(Option::is_some).collect();
    for i in 0..before.len() {
        if to[i].is_none() {
            let j = (0..after.len()).find(|&j| !taken[j] && after[j] == before[i])?;
            taken[j] = true;
            to[i] = Some(j);
        }
    }
    to.into_iter().collect()
}

/// `layout` with the heights of `kind`'s tracks gone where `to` says each
/// track went.
pub(crate) fn carried_heights(layout: &TrackLayout, kind: TrackKind, to: &[usize]) -> TrackLayout {
    let mut next = layout.clone();
    for (from, &now) in to.iter().enumerate() {
        let height = layout.height(TrackRef { kind, index: from });
        if (next.height(TrackRef { kind, index: now }) - height).abs() > f32::EPSILON {
            next.set_height(TrackRef { kind, index: now }, height);
        }
    }
    next
}

/// The solo, gone where `to` says its track went.
pub(crate) fn carried_solo(solo: Option<TrackRef>, kind: TrackKind, to: &[usize]) -> Option<TrackRef> {
    solo.map(|t| match to.get(t.index) {
        Some(&index) if t.kind == kind => TrackRef { kind, index },
        _ => t,
    })
}

impl Editor {
    /// The cut changed: when its tracks are the ones it had (`seen`) in
    /// another order, their heights and the solo go with them. Called
    /// before `seen` is brought up to date.
    pub(super) fn follow_tracks(&mut self) {
        let (was, now) = (&self.seen, &self.doc.edit);
        let orders = [(TrackKind::Video, moved(&was.video, &now.video)), (TrackKind::Audio, moved(&was.audio, &now.audio))];
        for (kind, to) in orders {
            let Some(to) = to else { continue };
            let layout = carried_heights(&self.track_layout, kind, &to);
            if layout != self.track_layout {
                self.track_layout = layout;
                self.save_track_layout();
            }
            self.solo = carried_solo(self.solo, kind, &to);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tracks;
    use proptest::prelude::*;

    /// A list and the same list shuffled, with where each went.
    fn shuffled(alike: bool) -> impl Strategy<Value = (Vec<u8>, Vec<u8>, Vec<usize>)> {
        (1usize..9).prop_flat_map(move |n| {
            let items = if alike { prop::collection::vec(0u8..3, n).boxed() } else { Just((0..n as u8).collect::<Vec<u8>>()).boxed() };
            (items, Just((0..n).collect::<Vec<usize>>()).prop_shuffle()).prop_map(|(before, to)| {
                let mut after = before.clone();
                for (i, &j) in to.iter().enumerate() {
                    after[j] = before[i];
                }
                (before, after, to)
            })
        })
    }

    proptest! {
        /// Things all different, put in another order: each is found
        /// where it went. In the same order nothing moved.
        #[test]
        fn each_is_found_where_it_went((before, after, to) in shuffled(false)) {
            let same = to.iter().enumerate().all(|(i, &j)| i == j);
            prop_assert_eq!(moved(&before, &after), (!same).then_some(to));
        }

        /// With things alike among them: what is found is each thing once,
        /// at a place that holds a thing like it, and a thing that is
        /// where it was is found there.
        #[test]
        fn alike_or_not_each_goes_to_one_like_it((before, after, _) in shuffled(true)) {
            match moved(&before, &after) {
                None => prop_assert_eq!(&before, &after),
                Some(to) => {
                    let mut places = to.clone();
                    places.sort_unstable();
                    prop_assert_eq!(places, (0..before.len()).collect::<Vec<_>>());
                    for (i, &j) in to.iter().enumerate() {
                        prop_assert_eq!(before[i], after[j]);
                        if before[i] == after[i] {
                            prop_assert_eq!(j, i);
                        }
                    }
                }
            }
        }

        /// One thing changed, added or taken away is not another order.
        #[test]
        fn a_changed_list_is_not_another_order((before, after, _) in shuffled(false), at in any::<prop::sample::Index>(), which in 0u8..3) {
            let mut other = after.clone();
            let i = at.index(other.len());
            match which {
                0 => other[i] = 200,
                1 => other.insert(i, 200),
                _ => drop(other.remove(i)),
            }
            prop_assert_eq!(moved(&before, &other), None);
        }

        /// Heights and the solo go where their tracks went, and those of
        /// the other kind stay.
        #[test]
        fn heights_and_solo_go_with_their_tracks(
            (_, _, to) in shuffled(false),
            heights in prop::collection::vec(24u16..240, 9),
            audio in any::<bool>(),
            solo in proptest::option::of((any::<bool>(), 0usize..9)),
        ) {
            let kind = if audio { TrackKind::Audio } else { TrackKind::Video };
            let other = if audio { TrackKind::Video } else { TrackKind::Audio };
            let mut layout = TrackLayout::default();
            for (index, &h) in heights.iter().enumerate() {
                layout.set_height(TrackRef { kind, index }, f32::from(h));
                layout.set_height(TrackRef { kind: other, index }, f32::from(h) + 1.0);
            }
            let next = carried_heights(&layout, kind, &to);
            for index in 0..9 {
                let came_from = to.iter().position(|&j| j == index);
                let want = came_from.map_or(heights[index], |from| heights[from]);
                prop_assert_eq!(next.height(TrackRef { kind, index }), tracks::row_height(f32::from(want)));
                prop_assert_eq!(next.height(TrackRef { kind: other, index }), layout.height(TrackRef { kind: other, index }));
            }
            let solo = solo.map(|(same, index)| TrackRef { kind: if same { kind } else { other }, index });
            let carried = carried_solo(solo, kind, &to);
            match solo {
                Some(t) if t.kind == kind && t.index < to.len() => prop_assert_eq!(carried, Some(TrackRef { kind, index: to[t.index] })),
                _ => prop_assert_eq!(carried, solo),
            }
        }
    }
}
