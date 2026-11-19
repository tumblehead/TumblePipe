//! The menu a right-click on the timeline opens where there is no clip:
//! on empty track, the ruler, or a track's name.
//!
//! Its rows are what the editor already does, at the place clicked: a
//! marker, a new shot or the bin's pick at that frame ([`Message::At`]
//! parks the playhead there first, since each of them acts at the
//! playhead), a track added or an empty one removed, and the view's
//! switches. It is read from [`State`], the little of the editor it depends
//! on, so the tests hold it to its rules: nothing that changes the cut on an
//! edit that cannot change, nothing at a frame where none was clicked.
//!
//! No Paste: the editor has no copy or clipboard to paste from.

use th_edit::document::{TrackKind, TrackRef};
use th_edit::ops::Op;

use super::{Editor, Message, OpenDialog, track_rows};
use crate::tracks;

/// What the menu depends on.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct State {
    /// The edit can change (not a published version).
    pub writable: bool,
    /// The edit frame clicked; None on a track's name.
    pub frame: Option<usize>,
    /// A marker is on that frame.
    pub marker_here: bool,
    /// The bin has a pick to put in the cut.
    pub picked: bool,
    /// The track whose row was clicked (not the ruler, not the spare
    /// layer): its name, whether it holds no clip, and whether its row is
    /// at the default height.
    pub track: Option<Row>,
    /// How many video and audio tracks the edit has.
    pub video: usize,
    pub audio: usize,
    pub audio_hidden: bool,
    pub solo: Option<TrackRef>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Row {
    pub track: TrackRef,
    pub name: String,
    pub empty: bool,
    pub default_height: bool,
}

/// The menu's rows, in order: each its label and what it sends.
pub(super) fn entries(s: &State) -> Vec<(String, Message)> {
    let mut rows: Vec<(String, Message)> = Vec::new();
    let mut row = |label: &str, message: Message| rows.push((label.to_owned(), message));
    if let (true, Some(frame)) = (s.writable, s.frame) {
        let at = |message: Message| Message::At(frame, Box::new(message));
        row(if s.marker_here { "Remove Marker Here" } else { "Add Marker Here" }, at(Message::Marker));
        row("New Shot Here\u{2026}", at(Message::Open(OpenDialog::NewShot)));
        if s.picked {
            row("Insert Bin Pick Here", at(Message::BinPut(false)));
            row("Overlay Bin Pick Here", at(Message::BinPut(true)));
        }
    }
    if s.writable {
        row("Add Video Track", Message::Edit(Op::AddTrack { kind: TrackKind::Video, name: track_rows::video_name(s.video) }));
        row("Add Audio Track", Message::Edit(Op::AddTrack { kind: TrackKind::Audio, name: format!("A{}", s.audio + 1) }));
        // An empty track that is not the only one of its kind.
        if let Some(Row { track, name, empty: true, .. }) = &s.track {
            let others = match track.kind {
                TrackKind::Video => s.video,
                TrackKind::Audio => s.audio,
            };
            if others > 1 {
                row(&format!("Remove Track {name}"), Message::Edit(Op::RemoveTrack { track: *track }));
            }
        }
    }
    if let Some(Row { track, name, default_height, .. }) = &s.track {
        let label = if s.solo == Some(*track) { format!("Show All Tracks (Unsolo {name})") } else { format!("Solo {name}") };
        row(&label, Message::Solo(*track));
        if !default_height {
            row("Reset Track Height", Message::RowHeight(*track, tracks::ROW));
        }
    }
    row(if s.audio_hidden { "Show Audio Tracks" } else { "Hide Audio Tracks" }, Message::HideAudio(!s.audio_hidden));
    if s.solo.is_some() && s.track.as_ref().map(|r| r.track) != s.solo {
        row("Show All Tracks", Message::ShowAllTracks);
    }
    row("Select All", Message::SelectAll);
    row("Fit Timeline", Message::FitTimeline);
    rows
}

impl Editor {
    /// The menu's state for a right-click at `frame` on `track`'s row.
    pub(super) fn timeline_menu_state(&self, frame: Option<usize>, track: Option<TrackRef>) -> State {
        let edit = &self.doc.edit;
        let row = track.filter(|t| edit.tracks().any(|have| have == *t)).map(|track| Row {
            track,
            name: match track.kind {
                TrackKind::Video => edit.video[track.index].name.clone(),
                TrackKind::Audio => edit.audio[track.index].name.clone(),
            },
            empty: edit.spans(track).is_empty(),
            default_height: (self.track_layout.height(track) - tracks::ROW).abs() < 0.5,
        });
        // A row that acts "here" acts where a click there parks the
        // playhead: on the cut's end, for a click past it.
        let here = frame.map(|f| f.min(self.end()));
        State {
            writable: !self.doc.read_only(),
            frame,
            marker_here: here.is_some_and(|f| edit.markers.iter().any(|m| m.at == f as i64)),
            picked: self.bin_pick.is_some(),
            track: row,
            video: edit.video.len(),
            audio: edit.audio.len(),
            audio_hidden: self.track_layout.audio_hidden,
            solo: self.solo,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn state() -> impl Strategy<Value = State> {
        let row = (track_rows::tests::track(), "[VA][1-9]", any::<[bool; 2]>()).prop_map(|(track, name, b)| Row {
            track,
            name,
            empty: b[0],
            default_height: b[1],
        });
        (
            any::<[bool; 4]>(),
            proptest::option::of(0usize..5000),
            proptest::option::of(row),
            1usize..6,
            0usize..6,
            proptest::option::of(track_rows::tests::track()),
        )
            .prop_map(|(b, frame, track, video, audio, solo)| State {
                writable: b[0],
                frame,
                marker_here: b[1],
                picked: b[2],
                track,
                video,
                audio,
                audio_hidden: b[3],
                solo,
            })
    }

    fn sent(s: &State) -> Vec<(String, String)> {
        entries(s).into_iter().map(|(label, message)| (label, format!("{message:?}"))).collect()
    }

    /// Whether `message` changes the cut.
    fn writes(message: &str) -> bool {
        message.starts_with("Edit(") || message.starts_with("At(")
    }

    proptest! {
        /// On an edit that cannot change (a published version) the menu
        /// offers nothing that changes the cut, and is still there: the
        /// view's rows.
        #[test]
        fn a_read_only_edit_is_offered_no_change(state in state()) {
            let state = State { writable: false, ..state };
            let rows = sent(&state);
            prop_assert!(!rows.is_empty());
            for (label, message) in rows {
                prop_assert!(!writes(&message), "{} ({}) is offered read-only", label, message);
            }
        }

        /// What acts "here" acts at the frame clicked, every time: each
        /// such row parks the playhead on that frame first. A click with no
        /// frame under it (a track's name) offers none of them.
        #[test]
        fn here_is_the_frame_clicked(state in state()) {
            let here: Vec<(String, String)> = sent(&state).into_iter().filter(|(label, _)| label.contains("Here")).collect();
            match (state.writable, state.frame) {
                (true, Some(frame)) => {
                    prop_assert_eq!(here.len(), if state.picked { 4 } else { 2 });
                    for (label, message) in here {
                        prop_assert!(message.starts_with(&format!("At({frame}, ")), "{}: {}", label, message);
                    }
                }
                _ => prop_assert!(here.is_empty()),
            }
            let at = sent(&state).into_iter().filter(|(_, message)| message.starts_with("At(")).count();
            prop_assert_eq!(at, if state.writable && state.frame.is_some() { if state.picked { 4 } else { 2 } } else { 0 });
        }

        /// The marker row takes away a marker that is there and adds one
        /// where there is none: the same toggle as M at the playhead.
        #[test]
        fn the_marker_row_says_what_it_does(state in state(), frame in 0usize..5000) {
            let state = State { writable: true, frame: Some(frame), ..state };
            let rows = sent(&state);
            let marker: Vec<&(String, String)> = rows.iter().filter(|(label, _)| label.contains("Marker")).collect();
            prop_assert_eq!(marker.len(), 1);
            prop_assert_eq!(marker[0].0.starts_with("Remove"), state.marker_here);
            prop_assert_eq!(&marker[0].1, &format!("{:?}", Message::At(frame, Box::new(Message::Marker))));
        }

        /// A track is added on top of those of its kind under the next
        /// name; only an empty track's row, and never the last track of
        /// its kind, offers to remove it, and it removes that track.
        #[test]
        fn tracks_are_added_on_top_and_only_empty_ones_removed(state in state()) {
            let state = State { writable: true, ..state };
            let rows = sent(&state);
            let adds: Vec<&String> = rows.iter().filter(|(_, m)| m.contains("AddTrack")).map(|(_, m)| m).collect();
            prop_assert_eq!(adds.len(), 2);
            let (video, audio) = (format!("\"V{}\"", state.video + 1), format!("\"A{}\"", state.audio + 1));
            prop_assert!(adds[0].contains(&video) && adds[1].contains(&audio));
            let removes: Vec<&(String, String)> = rows.iter().filter(|(_, m)| m.contains("RemoveTrack")).collect();
            let removable = state.track.as_ref().filter(|r| {
                r.empty && match r.track.kind { TrackKind::Video => state.video > 1, TrackKind::Audio => state.audio > 1 }
            });
            match removable {
                Some(row) => {
                    prop_assert_eq!(removes.len(), 1);
                    prop_assert_eq!(&removes[0].1, &format!("{:?}", Message::Edit(Op::RemoveTrack { track: row.track })));
                    prop_assert!(removes[0].0.ends_with(&row.name));
                }
                None => prop_assert!(removes.is_empty()),
            }
        }

        /// No two rows read the same, and the view's rows are always there:
        /// the audio rows (sending the other of what is), Select All and
        /// Fit Timeline.
        #[test]
        fn the_rows_are_distinct_and_the_views_are_always_there(state in state()) {
            let rows = sent(&state);
            for (i, (a, _)) in rows.iter().enumerate() {
                prop_assert!(rows[i + 1..].iter().all(|(b, _)| a != b), "{} is there twice", a);
            }
            let has = |message: &Message| rows.iter().any(|(_, m)| *m == format!("{message:?}"));
            prop_assert!(has(&Message::HideAudio(!state.audio_hidden)));
            prop_assert!(has(&Message::SelectAll) && has(&Message::FitTimeline));
            // With a track soloed there is a way back to all of them.
            if let Some(solo) = state.solo {
                prop_assert!(has(&Message::ShowAllTracks) || has(&Message::Solo(solo)));
            }
        }
    }
}
