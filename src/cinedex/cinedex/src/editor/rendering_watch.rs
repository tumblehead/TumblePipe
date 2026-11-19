//! The editor's side of `crate::rendering`: which shots the cut has, the
//! poll that looks at their unfinished renders (on the previews' tick, off
//! the UI thread), and the strips its video clips show.

use primer::prelude::*;
use th_edit::document::{Source, TrackKind};

use super::{Editor, Message};
use crate::rendering::{self, Strip};

impl Editor {
    /// Start this poll's look at the cut's unfinished renders, if any is
    /// due and none is out.
    pub(super) fn poll_rendering(&mut self) -> Command<Message> {
        let shots = rendering::shots_of(self.doc.edit.video.iter().flat_map(|track| track.clips.iter().map(|clip| &clip.source)));
        let jobs = self.rendering.plan(&shots);
        if jobs.is_empty() {
            return Command::none();
        }
        let paths = self.paths.clone();
        Command::perform_blocking(move || rendering::run(paths.open(), jobs), Message::Rendering)
    }

    /// The strip of every video clip shown whose shot waits for a render.
    pub(super) fn render_strips(&self) -> Vec<Strip> {
        let edit = &self.doc.edit;
        let mut strips = Vec::new();
        for track in self.shown_tracks().into_iter().filter(|t| t.kind == TrackKind::Video) {
            for clip in &edit.video[track.index].clips {
                let Source::Shot { .. } = &clip.source else { continue };
                if let Some(unfinished) = self.rendering.of_source(&clip.source) {
                    strips.extend(rendering::strip(unfinished, track, clip.at, clip.source_in, clip.duration));
                }
            }
        }
        strips
    }
}
