//! What a right-click on a clip offers to do to it, any clip: a shot's, a
//! media item's, a slug, a sound. Up to 1.120 only a shot's clip had rows
//! (its takes), so a right-click on an audio clip opened an empty popup
//! (Remy, 2026-11-22).
//!
//! The rows are the edit's own, at the clip: split it where it was clicked
//! (or at the playhead, when that is over it), delete it with or without
//! closing up after it, join it with the clip after it when that continues
//! it, close the gap before it, put a sound's level back to 0 dB; and,
//! whatever the edit, take the playhead to its start or its last frame.
//! The right-click picks the clip first, as a click does (one not already
//! picked becomes the only one), so Delete and Ripple Delete act on what
//! is picked, as the keys do, and the clip fields show the clip's.
//!
//! No mute or disable row: a clip has no such switch in the edit.

use th_edit::document::{ClipRef, Frame as EditFrame, TrackKind, WorkingEdit};
use th_edit::ops::Op;

use super::{Editor, Message, gestures};

/// What the menu depends on.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct State {
    /// The edit can change (not a published version).
    pub writable: bool,
    pub clip: ClipRef,
    /// The clip's frames, `(at, end)`.
    pub span: (EditFrame, EditFrame),
    /// The edit frame right-clicked.
    pub frame: usize,
    pub playhead: usize,
    /// How many clips are picked (the clip among them).
    pub picked: usize,
    /// The clip after it continues it, so a join is taken.
    pub joinable: bool,
    /// Empty frames before it on its track (after the clip before it, or
    /// from frame 0).
    pub gap_before: bool,
    /// A sound's level, in dB; None on a video clip.
    pub gain_db: Option<f64>,
}

/// "Delete", or "Delete 3 Clips" when more than the one are picked.
fn counted(word: &str, picked: usize) -> String {
    if picked > 1 { format!("{word} {picked} Clips") } else { word.to_owned() }
}

/// Whether edit frame `frame` is a place to split a clip over `span`: a
/// frame of it other than its first.
fn splits(span: (EditFrame, EditFrame), frame: usize) -> bool {
    let frame = frame as EditFrame;
    span.0 < frame && frame < span.1
}

/// The menu's rows, in order: each its label and what it sends.
pub(super) fn entries(s: &State) -> Vec<(String, Message)> {
    let mut rows: Vec<(String, Message)> = Vec::new();
    let clip = s.clip;
    if s.writable {
        if splits(s.span, s.frame) {
            rows.push(("Split Here".into(), Message::Edit(Op::Split { clip, at: s.frame as EditFrame })));
        }
        if splits(s.span, s.playhead) && s.playhead != s.frame {
            rows.push(("Split at Playhead".into(), Message::Edit(Op::Split { clip, at: s.playhead as EditFrame })));
        }
        rows.push((counted("Delete", s.picked), Message::Lift));
        rows.push((counted("Ripple Delete", s.picked), Message::RippleDelete));
        if s.joinable {
            rows.push(("Join with Next Clip".into(), Message::Edit(Op::Join { clip })));
        }
        if s.gap_before {
            rows.push(("Close Gap Before".into(), Message::CloseGap(clip)));
        }
        if let Some(gain) = s.gain_db.filter(|g| *g != 0.0) {
            rows.push((format!("Reset Level to 0 dB (now {gain:+.1} dB)"), Message::Edit(Op::SetGain { clip, gain_db: 0.0 })));
        }
    }
    rows.push(("Go to Clip Start".into(), Message::Seek(s.span.0.max(0) as usize)));
    rows.push(("Go to Clip's Last Frame".into(), Message::Seek((s.span.1 - 1).max(0) as usize)));
    rows
}

/// Every clip's rows of its right-click popup, as the popup lists them
/// after a shot's takes and views: Convert to Shot… and the swap with the
/// clip it replaced (`convert_shot::rows`), then what the edit does to it
/// ([`entries`]). One list, so the two merged features are tested as the
/// popup has them.
pub(super) fn clip_rows(edit: &WorkingEdit, s: &State) -> Vec<(String, Message)> {
    use crate::convert_shot::{self, Row};
    let clip = s.clip;
    let mut rows: Vec<(String, Message)> = convert_shot::rows(edit, clip, s.writable)
        .into_iter()
        .map(|(label, row)| {
            let message = match row {
                Row::Convert => Message::ConvertToShot(clip),
                Row::Swap => Message::SwapOriginal(clip),
            };
            (label, message)
        })
        .collect();
    rows.extend(entries(s));
    rows
}

/// Whether the clip after `clip` continues it, so a join is taken.
fn joinable(edit: &WorkingEdit, clip: ClipRef) -> bool {
    th_edit::ops::apply(edit, &Op::Join { clip }, &crate::bin_drag::AnyFrames).is_ok()
}

impl Editor {
    /// The clip menu's state for a right-click on `clip` at edit frame
    /// `frame`; None when there is no such clip (the cut changed since).
    pub(super) fn clip_menu_state(&self, clip: ClipRef, frame: usize) -> Option<State> {
        let edit = &self.doc.edit;
        let span = *edit.tracks().any(|t| t == clip.track).then(|| edit.spans(clip.track))?.get(clip.index)?;
        let gain_db = match clip.track.kind {
            TrackKind::Video => None,
            TrackKind::Audio => Some(edit.audio[clip.track.index].clips[clip.index].gain_db),
        };
        Some(State {
            writable: !self.doc.read_only(),
            clip,
            span,
            frame,
            playhead: self.position,
            picked: self.selection.len().max(1),
            joinable: joinable(edit, clip),
            gap_before: gestures::closing(edit, clip).is_some(),
            gain_db,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::TrackRef;

    fn state() -> impl Strategy<Value = State> {
        let clip = (any::<bool>(), 0usize..3, 0usize..5).prop_map(|(audio, track, index)| ClipRef {
            track: if audio { TrackRef::audio(track) } else { TrackRef::video(track) },
            index,
        });
        (any::<[bool; 3]>(), clip, (0i64..200, 1i64..100), 0usize..400, 0usize..400, 1usize..5, prop::option::of(-20.0f64..6.0)).prop_map(
            |(b, clip, (at, long), frame, playhead, picked, gain)| State {
                writable: b[0],
                clip,
                span: (at, at + long),
                frame,
                playhead,
                picked,
                joinable: b[1],
                gap_before: b[2],
                gain_db: gain.filter(|_| clip.track.kind == TrackKind::Audio),
            },
        )
    }

    proptest! {
        /// Every clip has a menu: never an empty popup, on any clip, of
        /// an edit that can change or not.
        #[test]
        fn every_clip_has_rows(state in state()) {
            prop_assert!(!entries(&state).is_empty());
        }

        /// On an edit that cannot change, nothing offered changes the cut.
        #[test]
        fn a_read_only_edit_is_offered_no_change(state in state()) {
            let state = State { writable: false, ..state };
            for (label, message) in entries(&state) {
                prop_assert!(matches!(message, Message::Seek(_)), "{} ({:?}) is offered read-only", label, message);
            }
        }

        /// Each row that acts on the clip names that clip, and a split is
        /// at a frame of it that leaves both halves a frame at least.
        #[test]
        fn the_rows_are_of_the_clip_clicked(state in state()) {
            let state = State { writable: true, ..state };
            let rows = entries(&state);
            for (label, message) in &rows {
                match message {
                    Message::Edit(Op::Split { clip, at }) => {
                        prop_assert_eq!(*clip, state.clip);
                        prop_assert!(state.span.0 < *at && *at < state.span.1, "{} at {}", label, at);
                    }
                    Message::Edit(Op::Join { clip } | Op::SetGain { clip, .. }) | Message::CloseGap(clip) => prop_assert_eq!(*clip, state.clip),
                    Message::Seek(frame) => prop_assert!((state.span.0..state.span.1).contains(&(*frame as EditFrame))),
                    Message::Lift | Message::RippleDelete => {}
                    other => prop_assert!(false, "unexpected row {} ({:?})", label, other),
                }
            }
            let labels: Vec<&str> = rows.iter().map(|(l, _)| l.as_str()).collect();
            prop_assert_eq!(labels.contains(&"Split Here"), splits(state.span, state.frame));
            prop_assert_eq!(labels.contains(&"Join with Next Clip"), state.joinable);
            prop_assert_eq!(labels.contains(&"Close Gap Before"), state.gap_before);
            prop_assert_eq!(labels.iter().any(|l| l.starts_with("Reset Level")), state.gain_db.is_some_and(|g| g != 0.0));
            let delete = if state.picked > 1 { format!("Delete {} Clips", state.picked) } else { "Delete".to_owned() };
            prop_assert!(labels.contains(&delete.as_str()));
        }
    }
}
