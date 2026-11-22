//! Edit > Sync Shot Ranges (Remy's RFE, 2026-11-22: "increasing the length
//! of a shot clip on the timeline would then update the shot entity's start
//! and end frames").
//!
//! On by default, for the window's session. While it is on and the window
//! edits the master edit's working version, every edit, undo or redo that
//! changes a shot clip's source frames writes that shot's own
//! `frame_start` / `frame_end` straight away (`th_pipe::shot_sync`: the end
//! the clip moved takes the clip's frame; a head trim moves the start, so
//! frame 1001 stays the renders' 1001). An undo moves the clip back and
//! writes the frame back, so the shot follows the cut's history. Clips of
//! anything but a made shot (slugs, media, planned shots) never write, and
//! neither does any other edit than the master, nor a published version.
//!
//! Each write is guarded: the shot's range on disk must still be the one the
//! Shots bin read, and the database file unwritten since that check
//! (`Config::set_own_keys_guarded`). A refused write leaves the shot as it
//! is, says so in the status line and reads the bins again; the master's
//! publish still offers the range then.
//!
//! While it syncs, a made shot's clip can be trimmed past the frames the
//! shot has ([`Open`]): that is how a shot gets longer. The frames no take
//! has yet show as missing until a new playblast or render.

use serde_json::json;
use th_edit::document::Source;
use th_edit::ops::{Available, Sources};
use th_pipe::master;
use th_pipe::shot_sync;

use super::{Command, Editor, Message};
use crate::editing::Origin;

/// The sources as `inner` has them, but with every shot's frames open while
/// `shots` (the sync is on): a shot clip trimmed past the shot's frames
/// makes the shot longer.
pub(super) struct Open<'a, S> {
    pub inner: &'a S,
    pub shots: bool,
}

impl<S: Sources<Source>> Sources<Source> for Open<'_, S> {
    fn available(&self, source: &Source) -> Available {
        match source {
            Source::Shot { .. } if self.shots => Available::Any,
            _ => self.inner.available(source),
        }
    }
}

fn shot_of(source: &Source) -> Option<&str> {
    match source {
        Source::Shot { shot, .. } => Some(shot),
        _ => None,
    }
}

impl Editor {
    /// Whether trims of shot clips set the shots' frames now: Sync Shot
    /// Ranges is on, and the window is on the master edit's working
    /// version, which it can change.
    pub(super) fn syncing(&self) -> bool {
        self.sync_ranges
            && !self.doc.read_only()
            && match &self.doc.origin {
                Origin::Working { name, .. } => self.bins.master.as_deref() == Some(name.as_str()),
                _ => false,
            }
    }

    /// After an edit, undo or redo, before `changed` (the cut it changed
    /// from is still `self.seen`): write the ranges of the shots whose
    /// clips' frames it changed, and say what was written or why not.
    pub(super) fn sync_shot_ranges(&mut self) -> Command<Message> {
        if !self.syncing() {
            return Command::none();
        }
        // A shot shown twice has no one range: the publish refuses it too.
        let (Ok(before), Ok(after)) = (master::shot_uses(&self.seen, shot_of), master::shot_uses(&self.doc.edit, shot_of)) else {
            return Command::none();
        };
        let known = |shot: &th_pipe::Uri| {
            self.bins.shots.iter().find(|s| th_pipe::Uri::parse(&s.uri).ok().as_ref() == Some(shot)).map(|s| (s.first, s.last))
        };
        let plan = shot_sync::plan(&before, &after, known);
        if plan.is_empty() {
            return Command::none();
        }
        let project = match self.paths.open() {
            Ok(project) => project,
            Err(e) => {
                self.status = Some((format!("The shots' frames were not synced: {e}"), true));
                return Command::none();
            }
        };
        let (mut written, mut failed) = (Vec::new(), Vec::new());
        for sync in &plan {
            match shot_sync::write(&project, sync) {
                Ok(()) => {
                    let uri = sync.shot.to_string();
                    if let Some(shot) = self.bins.shots.iter_mut().find(|s| s.uri == uri) {
                        (shot.first, shot.last) = sync.to;
                    }
                    written.push(sync.describe());
                }
                Err(e) => failed.push(e),
            }
        }
        self.events.push("shot_ranges_synced", json!({"written": written, "failed": failed}));
        let mut words = if written.is_empty() { String::new() } else { format!("Shot frames synced: {}", written.join("; ")) };
        if !failed.is_empty() {
            if !words.is_empty() {
                words += "; ";
            }
            words += &failed.join("; ");
        }
        self.status = Some((words, !failed.is_empty()));
        if failed.is_empty() { Command::none() } else { self.refresh_bins() }
    }
}
