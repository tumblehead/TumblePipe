//! The menu a right-click on the timeline opens where there is no clip:
//! on empty track, the ruler, or a track's name.
//!
//! Its rows are what the editor already does, at the place clicked: a
//! marker, a new shot or the bin's pick at that frame ([`Message::At`]
//! parks the playhead there first, since each of them acts at the
//! playhead), a track added, and the view's switches; and on a track's row
//! what is done to that track: moved a row up or down, renamed (a small
//! dialog), given a colour ([`colours`], a submenu), or taken away with
//! its clips, the row saying how many. It is read from [`State`], the little of the editor it depends
//! on, so the tests hold it to its rules: nothing that changes the cut on an
//! edit that cannot change, nothing at a frame where none was clicked.
//!
//! No Paste: the editor has no copy or clipboard to paste from.

use th_edit::document::{ClipRef, TrackKind, TrackRef};
use th_edit::ops::Op;

use primer::prelude::*;

use super::{Dialog, Editor, Message, OpenDialog, track_rows};
use crate::{look, tracks};

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
    /// Their names, bottom first: a new track gets one none of them has.
    pub video_names: Vec<String>,
    pub audio_names: Vec<String>,
    pub audio_hidden: bool,
    pub solo: Option<TrackRef>,
    /// The gap under the click, named by the clip after it (`gestures`).
    pub gap: Option<ClipRef>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Row {
    pub track: TrackRef,
    pub name: String,
    /// How many clips it holds.
    pub clips: usize,
    /// The colour name it is marked with.
    pub color: Option<String>,
    pub default_height: bool,
}

/// The index `track` goes to when moved one row up (or down) the timeline,
/// of `video` and `audio` tracks; None at the end of its kind. The rows
/// show video tracks top track first and audio tracks first track first,
/// so up is a higher index for video and a lower one for audio.
pub(super) fn step(track: TrackRef, up: bool, video: usize, audio: usize) -> Option<usize> {
    let (count, higher) = match track.kind {
        TrackKind::Video => (video, up),
        TrackKind::Audio => (audio, !up),
    };
    let to = if higher { track.index.checked_add(1)? } else { track.index.checked_sub(1)? };
    (track.index < count && to < count).then_some(to)
}

/// What the row that takes a track away says: an empty track is removed;
/// one with clips is deleted with them, and says how many go.
pub(super) fn delete_label(name: &str, clips: usize) -> String {
    match clips {
        0 => format!("Remove Track {name}"),
        1 => format!("Delete Track {name} and 1 Clip"),
        n => format!("Delete Track {name} and {n} Clips"),
    }
}

/// The Track Colour submenu of the row clicked: its title names the track
/// and the colour it has, and its rows are the colours it can be given
/// instead, None first and then the palette's by name. The colour it has
/// is not a row: choosing it would be a step of undo that changes
/// nothing. None on an edit that cannot change, or off a track.
pub(super) fn colours(s: &State) -> Option<(String, Vec<(String, Message)>)> {
    let Row { track, name, color, .. } = s.track.as_ref().filter(|_| s.writable)?;
    let label = |palette: &str| format!("{}{}", palette[..1].to_uppercase(), &palette[1..]);
    let choices = std::iter::once(None).chain(look::TRACK_COLORS.iter().map(|palette| Some((*palette).to_owned())));
    let rows = choices
        .filter(|choice| choice != color)
        .map(|choice| {
            let says = choice.as_deref().map_or_else(|| "None".to_owned(), label);
            (says, Message::Edit(Op::SetTrackColor { track: *track, color: choice }))
        })
        .collect();
    let has = color.as_deref().filter(|c| !c.is_empty()).map_or_else(|| "None".to_owned(), label);
    Some((format!("Track Colour of {name} ({has})"), rows))
}

/// Why `name` cannot be the track's new name, of those the other tracks
/// of its kind have (`others`); None when it can. As the edit refuses it:
/// an empty name, or another track's. Spaces around it do not count.
pub(super) fn rename_problem(name: &str, others: &[String]) -> Option<String> {
    let name = name.trim();
    if name.is_empty() {
        return Some("A track needs a name".to_owned());
    }
    others.iter().any(|other| other == name).then(|| format!("Another track is named {name}"))
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
    if let (true, Some(gap)) = (s.writable, s.gap) {
        row("Close Gap", Message::CloseGap(gap));
    }
    if s.writable {
        row("Add Video Track", Message::Edit(Op::AddTrack { kind: TrackKind::Video, name: track_rows::fresh_name('V', &s.video_names) }));
        row("Add Audio Track", Message::Edit(Op::AddTrack { kind: TrackKind::Audio, name: track_rows::fresh_name('A', &s.audio_names) }));
        if let Some(Row { track, name, clips, .. }) = &s.track {
            for (up, word) in [(true, "Up"), (false, "Down")] {
                if let Some(to) = step(*track, up, s.video, s.audio) {
                    row(&format!("Move Track {name} {word}"), Message::Edit(Op::MoveTrack { track: *track, to }));
                }
            }
            row(&format!("Rename Track {name}\u{2026}"), Message::Open(OpenDialog::RenameTrack(*track)));
            // Not the only track of its kind.
            let others = match track.kind {
                TrackKind::Video => s.video,
                TrackKind::Audio => s.audio,
            };
            if others > 1 {
                row(&delete_label(name, *clips), Message::Edit(Op::RemoveTrack { track: *track }));
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
    row("Fit to Clips", Message::FitClips);
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
            clips: edit.spans(track).len(),
            color: edit.track_color(track).map(str::to_owned),
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
            video_names: edit.video.iter().map(|t| t.name.clone()).collect(),
            audio_names: edit.audio.iter().map(|t| t.name.clone()).collect(),
            audio_hidden: self.track_layout.audio_hidden,
            solo: self.solo,
            gap: frame.zip(track).and_then(|(frame, track)| super::gestures::gap_under(edit, track, frame)),
        }
    }

    /// The dialog that renames `track`, its field holding the name it
    /// has; None on an edit that cannot change, or for no track of it.
    pub(super) fn rename_track_dialog(&self, track: TrackRef) -> Option<Dialog> {
        let edit = &self.doc.edit;
        if self.doc.read_only() || !edit.tracks().any(|have| have == track) {
            return None;
        }
        let names: Vec<String> = match track.kind {
            TrackKind::Video => edit.video.iter().map(|t| t.name.clone()).collect(),
            TrackKind::Audio => edit.audio.iter().map(|t| t.name.clone()).collect(),
        };
        let others = names.iter().enumerate().filter(|(i, _)| *i != track.index).map(|(_, n)| n.clone()).collect();
        Some(Dialog::RenameTrack { track, name: names[track.index].clone(), others, tried: false })
    }

    /// The rename dialog's Rename: the track gets `name` (without the
    /// spaces around it) as one undo step and the dialog closes; a name
    /// that cannot be keeps the dialog, which says why. The name it has
    /// already closes it and changes nothing.
    pub(super) fn rename_track(&mut self, track: TrackRef, name: String, others: &[String]) -> Command<Message> {
        if rename_problem(&name, others).is_some() {
            if let Some(Dialog::RenameTrack { tried, .. }) = &mut self.dialog {
                *tried = true;
            }
            return Command::none();
        }
        self.dialog = None;
        let name = name.trim().to_owned();
        let edit = &self.doc.edit;
        if !edit.tracks().any(|have| have == track) || edit.track_name(track) == name {
            return Command::none();
        }
        self.edit(&[Op::RenameTrack { track, name }])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn state() -> impl Strategy<Value = State> {
        let color = proptest::option::of(prop::sample::select(look::TRACK_COLORS.to_vec()).prop_map(str::to_owned));
        let row = (track_rows::tests::track(), "[VA][1-9]", any::<bool>(), 0usize..4, color)
            .prop_map(|(track, name, default_height, clips, color)| Row { track, name, clips, color, default_height });
        // Names as tracks have them: no two alike, in any order, some of
        // them the numbers a new track would be given.
        let names = |letter: char| {
            let pool: Vec<String> = (1..=8).map(|n| format!("{letter}{n}")).chain(["Plates".to_owned(), "BG".to_owned()]).collect();
            Just(pool).prop_shuffle()
        };
        (
            any::<[bool; 4]>(),
            proptest::option::of(0usize..5000),
            proptest::option::of(row),
            (1usize..6, names('V')),
            (0usize..6, names('A')),
            proptest::option::of(track_rows::tests::track()),
            proptest::option::of(0usize..4),
        )
            .prop_map(|(b, frame, track, (video, video_names), (audio, audio_names), solo, gap)| State {
                writable: b[0],
                frame,
                marker_here: b[1],
                picked: b[2],
                track,
                video,
                audio,
                video_names: video_names[..video].to_vec(),
                audio_names: audio_names[..audio].to_vec(),
                audio_hidden: b[3],
                solo,
                gap: gap.map(|index| ClipRef { track: TrackRef::video(0), index }),
            })
    }

    fn sent(s: &State) -> Vec<(String, String)> {
        entries(s).into_iter().map(|(label, message)| (label, format!("{message:?}"))).collect()
    }

    /// Whether `message` changes the cut.
    fn writes(message: &str) -> bool {
        message.starts_with("Edit(")
            || message.starts_with("At(")
            || message.starts_with("CloseGap(")
            || message.starts_with("Open(RenameTrack")
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
            prop_assert!(colours(&state).is_none());
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

        /// A track is added on top of those of its kind under a name none
        /// of them has: its letter and a number, the next one when the
        /// tracks are named in order. Never the last track of its kind,
        /// and any other, offers to take it away, and takes that track: an
        /// empty one is removed; one with clips says it deletes them, and
        /// how many.
        #[test]
        fn tracks_are_added_under_a_free_name_and_taken_away_with_a_count(state in state(), in_order in any::<bool>()) {
            let mut state = State { writable: true, ..state };
            if in_order {
                state.video_names = (1..=state.video).map(|n| format!("V{n}")).collect();
                state.audio_names = (1..=state.audio).map(|n| format!("A{n}")).collect();
            }
            let rows = entries(&state);
            let adds: Vec<(TrackKind, String)> = rows
                .iter()
                .filter_map(|(_, m)| match m {
                    Message::Edit(Op::AddTrack { kind, name }) => Some((*kind, name.clone())),
                    _ => None,
                })
                .collect();
            prop_assert_eq!(adds.len(), 2);
            prop_assert_eq!((adds[0].0, adds[1].0), (TrackKind::Video, TrackKind::Audio));
            for ((_, name), (letter, taken)) in adds.iter().zip([('V', &state.video_names), ('A', &state.audio_names)]) {
                prop_assert!(!taken.contains(name), "{} is taken", name);
                let number = name.strip_prefix(letter).and_then(|n| n.parse::<usize>().ok());
                prop_assert!(number.is_some_and(|n| n > taken.len()), "{}", name);
                if in_order {
                    prop_assert_eq!(name, &format!("{letter}{}", taken.len() + 1));
                }
            }
            let rows = sent(&state);
            let removes: Vec<&(String, String)> = rows.iter().filter(|(_, m)| m.contains("RemoveTrack")).collect();
            let removable = state.track.as_ref().filter(|r| match r.track.kind {
                TrackKind::Video => state.video > 1,
                TrackKind::Audio => state.audio > 1,
            });
            match removable {
                Some(row) => {
                    prop_assert_eq!(removes.len(), 1);
                    prop_assert_eq!(&removes[0].1, &format!("{:?}", Message::Edit(Op::RemoveTrack { track: row.track })));
                    let says = match row.clips {
                        0 => format!("Remove Track {}", row.name),
                        1 => format!("Delete Track {} and 1 Clip", row.name),
                        n => format!("Delete Track {} and {n} Clips", row.name),
                    };
                    prop_assert_eq!(&removes[0].0, &says);
                }
                None => prop_assert!(removes.is_empty()),
            }
        }

        /// Move Up and Move Down are offered on a track's row exactly when
        /// there is a row of its kind to pass that way, and send the track
        /// to the place of the row it passes: the rows show video tracks
        /// top first, then audio tracks first track first. Rename is
        /// offered on every track's row of an edit that can change.
        #[test]
        fn a_track_moves_past_the_row_beside_it(state in state()) {
            let state = State { writable: true, ..state };
            let rows = entries(&state);
            let moves: Vec<(&str, TrackRef, usize)> = rows
                .iter()
                .filter_map(|(label, m)| match m {
                    Message::Edit(Op::MoveTrack { track, to }) => Some((label.as_str(), *track, *to)),
                    _ => None,
                })
                .collect();
            let renames = rows.iter().filter(|(_, m)| matches!(m, Message::Open(OpenDialog::RenameTrack(_)))).count();
            let Some(row) = &state.track else {
                prop_assert!(moves.is_empty() && renames == 0);
                return Ok(());
            };
            prop_assert_eq!(renames, 1);
            // The rows of its kind as drawn, top first.
            let order: Vec<usize> = match row.track.kind {
                TrackKind::Video => (0..state.video).rev().collect(),
                TrackKind::Audio => (0..state.audio).collect(),
            };
            let place = order.iter().position(|&i| i == row.track.index);
            let above = place.and_then(|p| p.checked_sub(1)).map(|p| order[p]);
            let below = place.and_then(|p| order.get(p + 1).copied());
            let mut expected = Vec::new();
            if let Some(to) = above {
                expected.push((format!("Move Track {} Up", row.name), row.track, to));
            }
            if let Some(to) = below {
                expected.push((format!("Move Track {} Down", row.name), row.track, to));
            }
            let got: Vec<(String, TrackRef, usize)> = moves.into_iter().map(|(l, t, to)| (l.to_owned(), t, to)).collect();
            prop_assert_eq!(got, expected);
        }

        /// The colour submenu is the track's: its title says the track and
        /// the colour it has; its rows are None and every colour of the
        /// palette, in order, but the one it has, each setting the colour
        /// its label names on that track. So every row changes the cut.
        #[test]
        fn the_colour_rows_are_the_colours_the_track_has_not(state in state(), unknown in any::<bool>()) {
            let mut state = State { writable: true, ..state };
            if let (true, Some(row)) = (unknown, &mut state.track) {
                // A colour of a later Cinedex's palette.
                row.color = Some("pink".to_owned());
            }
            let Some(row) = &state.track else {
                prop_assert!(colours(&state).is_none());
                return Ok(());
            };
            let (title, rows) = colours(&state).unwrap();
            let has = row.color.clone().unwrap_or_else(|| "none".to_owned());
            prop_assert_eq!(title.to_lowercase(), format!("track colour of {} ({has})", row.name.to_lowercase()));
            let mut set = Vec::new();
            for (label, message) in &rows {
                let Message::Edit(Op::SetTrackColor { track, color }) = message else {
                    prop_assert!(false, "{} sends {:?}", label, message);
                    unreachable!()
                };
                prop_assert_eq!(*track, row.track);
                prop_assert!(*color != row.color, "{} is the colour it has", label);
                prop_assert_eq!(color.clone().unwrap_or_else(|| "none".to_owned()), label.to_lowercase());
                prop_assert!(label.chars().next().is_some_and(char::is_uppercase));
                prop_assert!(color.as_deref().is_none_or(|c| look::track(c).is_some()));
                set.push(color.clone());
            }
            let mut all: Vec<Option<String>> = vec![None];
            all.extend(look::TRACK_COLORS.iter().map(|c| Some((*c).to_owned())));
            all.retain(|c| *c != row.color);
            prop_assert_eq!(set, all);
        }

        /// A new name is refused exactly when it is empty or another track
        /// of its kind has it, and says which.
        #[test]
        fn a_tracks_new_name_is_its_own(state in state(), name in prop_oneof![Just(String::new()), Just("  ".to_owned()), "[VA][1-9]", " ?[VA][1-9] ?", Just("Plates".to_owned()), "[a-z]{1,6}"]) {
            let problem = rename_problem(&name, &state.video_names);
            let bare: String = name.chars().filter(|c| *c != ' ').collect();
            if bare.is_empty() {
                prop_assert_eq!(problem, Some("A track needs a name".to_owned()));
            } else if state.video_names.contains(&bare) {
                prop_assert_eq!(problem, Some(format!("Another track is named {bare}")));
            } else {
                prop_assert_eq!(problem, None);
            }
        }

        /// Close Gap is offered exactly on a gap of an edit that can
        /// change, and closes that gap.
        #[test]
        fn close_gap_is_offered_on_a_gap(state in state()) {
            let closes: Vec<(String, String)> = sent(&state).into_iter().filter(|(label, _)| label == "Close Gap").collect();
            match state.gap.filter(|_| state.writable) {
                Some(gap) => prop_assert_eq!(closes, vec![("Close Gap".to_owned(), format!("{:?}", Message::CloseGap(gap)))]),
                None => prop_assert!(closes.is_empty()),
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
            prop_assert!(has(&Message::SelectAll) && has(&Message::FitTimeline) && has(&Message::FitClips));
            // With a track soloed there is a way back to all of them.
            if let Some(solo) = state.solo {
                prop_assert!(has(&Message::ShowAllTracks) || has(&Message::Solo(solo)));
            }
        }
    }
}
