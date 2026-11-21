//! What the editor does with the picks the tracks send
//! ([`tracks::Gesture`]): a range of clips, a box of clips, a gap; and how
//! what is picked follows the clips through an edit.
//!
//! **Selection.** A click picks one clip (and makes it the anchor);
//! Ctrl/Cmd+click adds it or takes it out (and makes it the anchor);
//! Shift+click picks the range from the anchor to it, in place of what was
//! picked ([`tracks::picks::range`]); a drag from empty track picks what
//! its box touches. A click on empty track picks nothing, or the gap under
//! it.
//!
//! **After an edit, an undo or a redo** each picked clip is found again
//! ([`th_edit::follow`]): a clip that moved stays picked where it went,
//! one that cannot be told from another is let go. The selection never
//! goes to another clip. A picked gap is let go by any change of the cut.

use std::collections::BTreeSet;

use th_edit::document::{ClipRef, Frame as EditFrame, TrackRef, WorkingEdit};
use th_edit::follow::followed;
use th_edit::ops::Op;

use super::{Command, Editor, Message, Source};
use crate::tracks::{Gesture, picks};

/// The clips a Shift+click on `to` picks, the anchor being `anchor`, among
/// the tracks shown (`rows`: each its track and its clips as `(at, end)`,
/// top first): the range between them, or `to` alone when there is no
/// anchor on a row shown.
pub(super) fn ranged(rows: &[(TrackRef, Vec<(EditFrame, EditFrame)>)], anchor: Option<ClipRef>, to: ClipRef) -> BTreeSet<ClipRef> {
    let spans: Vec<Vec<(EditFrame, EditFrame)>> = rows.iter().map(|(_, spans)| spans.clone()).collect();
    let place = |clip: ClipRef| rows.iter().position(|(track, _)| *track == clip.track).map(|r| (r, clip.index));
    let picked = match (anchor.and_then(place), place(to)) {
        (Some(from), Some(to)) => picks::range(&spans, from, to),
        _ => Vec::new(),
    };
    let mut picked: BTreeSet<ClipRef> = picked.into_iter().map(|(r, index)| ClipRef { track: rows[r].0, index }).collect();
    picked.insert(to);
    picked
}

/// The op that closes the gap before `clip`: the clip and all after it on
/// its track, earlier by the gap. None when there is no gap there.
pub(super) fn closing(edit: &WorkingEdit, clip: ClipRef) -> Option<Op<Source>> {
    let spans = edit.tracks().any(|t| t == clip.track).then(|| edit.spans(clip.track))?;
    let (from, to) = picks::gap_before(&spans, clip.index)?;
    Some(Op::Shift { clip, delta: from - to })
}

/// The gap of `track` that edit frame `frame` is in, named by the clip
/// after it.
pub(super) fn gap_under(edit: &WorkingEdit, track: TrackRef, frame: usize) -> Option<ClipRef> {
    let spans = edit.tracks().any(|t| t == track).then(|| edit.spans(track))?;
    picks::gap_at(&spans, frame as EditFrame).map(|(index, _, _)| ClipRef { track, index })
}

/// `picked`, clips of `before`, as they are in `after`.
pub(super) fn kept(before: &WorkingEdit, after: &WorkingEdit, picked: &BTreeSet<ClipRef>) -> BTreeSet<ClipRef> {
    picked.iter().filter_map(|clip| followed(before, after, *clip)).collect()
}

impl Editor {
    pub(super) fn gesture(&mut self, gesture: Gesture) {
        self.gap = None;
        match gesture {
            Gesture::Range(clip) => {
                let edit = &self.doc.edit;
                let rows: Vec<_> = self.shown_tracks().into_iter().map(|track| (track, edit.spans(track))).collect();
                let anchor = self.anchor.filter(|a| super::clip_exists(edit, *a));
                self.selection = ranged(&rows, anchor, clip);
                self.anchor = anchor.or(Some(clip));
            }
            Gesture::Box(clips) => {
                self.selection = clips.into_iter().collect();
                self.anchor = None;
            }
            Gesture::Gap(clip, position) => {
                self.selection.clear();
                self.anchor = None;
                self.gap = closing(&self.doc.edit, clip).map(|_| clip);
                self.set_playing(false);
                self.move_to(position.min(self.end()));
            }
        }
    }

    /// Close the gap before `clip`, as one undo step.
    pub(super) fn close_gap(&mut self, clip: ClipRef) -> Command<Message> {
        self.gap = None;
        match closing(&self.doc.edit, clip) {
            Some(op) => self.edit(&[op]),
            None => Command::none(),
        }
    }

    /// The cut changed (an edit, an undo, a redo): what is picked follows
    /// its clips from the cut as it was.
    pub(super) fn follow_picks(&mut self) {
        // A clip held on the tracks is let go of once any clip changed: a
        // Shift+Delete or an undo can leave another clip at its place and
        // over its frames, which the drag must never land on. A marker or
        // a track's name changes no clip, and the drag goes on.
        if self.seen.video.iter().map(|t| &t.clips).ne(self.doc.edit.video.iter().map(|t| &t.clips))
            || self.seen.audio.iter().map(|t| &t.clips).ne(self.doc.edit.audio.iter().map(|t| &t.clips))
        {
            self.clip_changes += 1;
        }
        self.follow_tracks();
        self.selection = kept(&self.seen, &self.doc.edit, &self.selection);
        self.anchor = self.anchor.and_then(|a| followed(&self.seen, &self.doc.edit, a));
        self.gap = None;
        self.seen = self.doc.edit.clone();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn rows() -> impl Strategy<Value = Vec<(TrackRef, Vec<(EditFrame, EditFrame)>)>> {
        let row = prop::collection::vec((0i64..8, 1i64..10), 0..5).prop_map(|clips| {
            let mut at = 0;
            clips
                .into_iter()
                .map(|(gap, long)| {
                    at += gap;
                    let span = (at, at + long);
                    at += long;
                    span
                })
                .collect::<Vec<_>>()
        });
        // Shown top first: video tracks from the highest, then audio.
        prop::collection::vec(row, 1..5).prop_map(|rows| {
            let video = rows.len().div_ceil(2);
            let tracks = (0..video).rev().map(TrackRef::video).chain((0..rows.len() - video).map(TrackRef::audio));
            tracks.zip(rows).collect()
        })
    }

    proptest! {
        /// A Shift+click always picks the clip clicked; with no anchor (or
        /// one on a track not shown) only it; on the anchor's track, the
        /// clips from the anchor to it and no other; and every clip it
        /// picks is a clip of a row shown.
        #[test]
        fn a_shift_click_picks_from_the_anchor(rows in rows(), a in any::<prop::sample::Index>(), b in any::<prop::sample::Index>(), anchored in 0u8..3) {
            let all: Vec<ClipRef> = rows.iter().flat_map(|(track, spans)| (0..spans.len()).map(|index| ClipRef { track: *track, index })).collect();
            prop_assume!(!all.is_empty());
            let to = *b.get(&all);
            let anchor = match anchored {
                0 => None,
                1 => Some(ClipRef { track: TrackRef::audio(9), index: 0 }),
                _ => Some(*a.get(&all)),
            };
            let picked = ranged(&rows, anchor, to);
            prop_assert!(picked.contains(&to));
            prop_assert!(picked.iter().all(|c| all.contains(c)));
            match anchor.filter(|_| anchored == 2) {
                None => prop_assert_eq!(picked, BTreeSet::from([to])),
                Some(anchor) => {
                    prop_assert!(picked.contains(&anchor));
                    if anchor.track == to.track {
                        let between: BTreeSet<ClipRef> =
                            (anchor.index.min(to.index)..=anchor.index.max(to.index)).map(|index| ClipRef { track: to.track, index }).collect();
                        prop_assert_eq!(picked, between);
                    } else {
                        // Only rows from the anchor's to the clicked one.
                        let row = |c: &ClipRef| rows.iter().position(|(t, _)| *t == c.track).unwrap();
                        let (top, bottom) = (row(&anchor).min(row(&to)), row(&anchor).max(row(&to)));
                        prop_assert!(picked.iter().all(|c| (top..=bottom).contains(&row(c))));
                    }
                }
            }
        }
    }
}
