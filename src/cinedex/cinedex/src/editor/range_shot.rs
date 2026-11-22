//! New clips from frames swept on the timeline (Remy's RFE, 2026-11-22:
//! "drag on the timeline, then be given options including creating a shot
//! with the length of the dragged clip").
//!
//! An Alt+drag along an empty stretch of a video track (`tracks::sweep`)
//! opens the New shot dialog over the frames swept: its Frames are the
//! swept length, its name the sequence's next free one
//! (`editing::next_shot_name`), and "Create the shot now" is ticked.
//! **Create Shot** makes the shot entity there and then, as the master
//! edit's publish makes a planned one (`master::apply` of a
//! `Change::Create`: `entities::create_shot`, then the Multis listing its
//! sequence), its frames from the project's start frame
//! (`master::new_shot_start`, 1001 by default) for the swept length
//! (`shot_sync::new_shot_frames`), and lays its clip over the swept frames.
//! Unticked, it is **Plan Shot**: the clip of a planned shot, made when
//! the master is published, as File > New Shot… has always done.
//! **Placeholder** lays a slug of the frames there instead. Either clip is
//! one undo step; a shot made stays made (retire it from the master's
//! publish or the Entity Editor).
//!
//! The clip goes where it was swept, on the swept track: the sweep never
//! covers a clip, so it fits unless more frames are typed than the gap
//! holds, when it goes on the lowest track above with room (`editing::put`
//! laying over).
//!
//! A right-click on an animatic clip > Convert to Shot… opens the same
//! dialog over that clip's frames (`Place::replaces`): the shot's clip then
//! takes the animatic clip's place and keeps it, to swap back to
//! (`crate::convert_shot`). No Placeholder there: the clip is one already.

use th_edit::document::{ClipRef, Frame as EditFrame, Source, TrackKind, TrackRef, VideoClip};
use th_pipe::master::{self, Change};

use super::file_dialogs::{Dialog, OpenDialog};
use super::{Command, Editor, Message};
use crate::convert_shot;
use crate::editing::{self, BinShot};

/// Where frames were swept: the video track's index and the edit frame
/// they start on, and how many.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Place {
    pub track: usize,
    pub at: EditFrame,
    pub frames: EditFrame,
    /// They are the frames of the clip there, which the new clip replaces
    /// (Convert to Shot); else of empty track.
    pub replaces: bool,
}

/// What the New shot dialog says of the frames it was opened over.
pub(super) fn place_words(place: &Place) -> String {
    let s = if place.frames == 1 { "" } else { "s" };
    if place.replaces {
        return format!("In place of the {} frame{s} clip at edit frame {}: its right-click swaps back to it.", place.frames, place.at);
    }
    format!("Over the {} frame{s} dragged from edit frame {}: a shot, or a placeholder.", place.frames, place.at)
}

impl Editor {
    /// An Alt+drag on the tracks swept `frames` frames of `track` from
    /// `at`: the New shot dialog over them. Nothing on a read-only edit,
    /// under another dialog, or off the video tracks.
    pub(super) fn swept(&mut self, track: TrackRef, at: EditFrame, frames: EditFrame) {
        if self.doc.read_only() || self.dialog.is_some() || track.kind != TrackKind::Video || frames < 1 {
            return;
        }
        self.open_dialog(OpenDialog::NewShot);
        if let Some(Dialog::NewShot { frames: typed, place, now, .. }) = &mut self.dialog {
            *typed = frames.to_string();
            *place = Some(Place { track: track.index, at, frames, replaces: false });
            *now = true;
        }
    }

    /// Convert to Shot… on video clip `clip`: the New shot dialog over its
    /// frames, the shot's clip to take its place. Nothing on a read-only
    /// edit, under another dialog, or on a clip that is a shot.
    pub(super) fn convert_to_shot(&mut self, clip: ClipRef) {
        let Some(c) = convert_shot::video_clip(&self.doc.edit, clip).filter(|c| convert_shot::convertible(c)) else { return };
        if self.doc.read_only() || self.dialog.is_some() {
            return;
        }
        let over = Place { track: clip.track.index, at: c.at, frames: c.duration, replaces: true };
        self.open_dialog(OpenDialog::NewShot);
        if let Some(Dialog::NewShot { frames, place, now, .. }) = &mut self.dialog {
            *frames = over.frames.to_string();
            *place = Some(over);
            *now = true;
        }
    }

    /// Swap video clip `clip` with the clip it replaced
    /// (`convert_shot::swapped`), as one undo step.
    pub(super) fn swap_original(&mut self, clip: ClipRef) -> Command<Message> {
        if self.doc.read_only() {
            return Command::none();
        }
        let edit = &self.doc.edit;
        let ops = convert_shot::video_clip(edit, clip)
            .and_then(convert_shot::swapped)
            .and_then(|with| convert_shot::replace_ops(edit, clip, with));
        match ops {
            Some(ops) => self.edit(&ops),
            None => Command::none(),
        }
    }

    /// Make `shot` now, as the master's publish makes a planned shot, and
    /// put it in the Shots bin until the bins are read again.
    pub(super) fn create_shot_now(&mut self, shot: &BinShot) -> Result<(), String> {
        let project = self.paths.open()?;
        let uri = th_pipe::Uri::parse(&shot.uri).map_err(|e| e.to_string())?;
        master::apply(&project, &Change::Create { shot: uri, first: shot.first, last: shot.last }, "")?;
        self.bins.shots.push(shot.clone());
        self.events.push("shot_created", serde_json::json!({"shot": shot.uri, "first": shot.first, "last": shot.last}));
        Ok(())
    }

    /// Lay `clip` over the frames at `place` (its track, or the lowest
    /// above it with room), as one undo step.
    pub(super) fn put_over(&mut self, place: Place, clip: VideoClip<Source>) -> Command<Message> {
        if place.replaces {
            // Convert to Shot: in place of the clip there, which it keeps.
            let edit = &self.doc.edit;
            let ops = convert_shot::convertible_at(edit, place.track, place.at).and_then(|at| {
                let original = convert_shot::video_clip(edit, at)?;
                convert_shot::replace_ops(edit, at, convert_shot::converted(original, clip))
            });
            let Some(ops) = ops else {
                self.status = Some((format!("The clip at edit frame {} to convert is gone", place.at), true));
                return Command::none();
            };
            return self.edit(&ops);
        }
        let ops = editing::put(&self.doc.edit, Some(clip), None, place.track, true);
        self.edit(&ops)
    }

    /// New shot's Placeholder: a slug of `frames` (as typed) at `place`.
    pub(super) fn placeholder(&mut self, place: Place, frames: &str) -> Command<Message> {
        if place.replaces {
            return Command::none(); // Convert to Shot offers none
        }
        let Some(frames) = crate::number_field::whole(frames, None).ok().filter(|&f| f > 0) else {
            self.status = Some((format!("{frames:?}: the frames are a whole number above 0"), true));
            return Command::none();
        };
        self.dialog = None;
        self.put_over(place, VideoClip::new(place.at, frames, 0, Source::Slug { text: "Placeholder".into() }))
    }
}
