//! Files into the media library: the ones picked in the system's Open
//! dialog (Import…) and the ones dropped on the window.
//!
//! Both go the same way ([`Editor::files_import`]): `import_files::plan`
//! sorts them, each movie and sound is measured and copied in by
//! `th_pipe::media::import` under a name made from the file's, and the
//! status line names everything that went in and everything that did not,
//! with why. Files dropped on a track of the timeline are then put in the
//! cut there, as a row dragged out of the Media bin is (`bin_drag`).
//!
//! Primer does not hand the app files dropped on its window yet, so
//! nothing sends [`FilesMessage::Dropped`] today: it is where that arrives
//! once Primer does. The Open dialog is never shown in tests.

use std::path::PathBuf;

use primer::prelude::*;
use th_edit::document::{Frame as EditFrame, TrackRef};
use th_pipe::edits::EditSources;
use th_pipe::media;

use super::{Dialog, Editor, Message, OpenDialog};
use crate::bins::Target;
use crate::editing::{self, BinItem};
use crate::import_files::{self, Report};
use crate::{bin_drag, open_dialog};

/// Where dropped files go in the cut: the track and frame under the
/// pointer, and whether the key that inserts was held.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Place {
    pub track: TrackRef,
    pub frame: EditFrame,
    pub insert: bool,
}

#[derive(Debug, Clone)]
pub enum FilesMessage {
    /// What the Open dialog answered: the files picked (none: cancelled),
    /// or why there is no dialog.
    Picked(Result<Vec<PathBuf>, String>),
    /// Files were dropped on the window; over a track of the timeline, or
    /// anywhere else.
    #[cfg_attr(not(test), allow(dead_code))] // sent once Primer hands over dropped files
    Dropped(Vec<PathBuf>, Option<Place>),
    /// The import is done: what became of each file, the items made, and
    /// where they go in the cut.
    Imported(Report, Vec<BinItem>, Option<Place>),
}

impl Editor {
    /// Import…: ask the system which files, off the UI thread.
    pub(super) fn import_browse(&mut self) -> Command<Message> {
        let folder = self.bin_state.more.folder.clone();
        Command::perform_blocking(
            move || open_dialog::open(folder.as_deref(), &open_dialog::MEDIA),
            |picked| Message::Files(FilesMessage::Picked(picked)),
        )
    }

    /// Import `files` into the library, then put what they made at `place`.
    fn files_import(&mut self, files: Vec<PathBuf>, place: Option<Place>) -> Command<Message> {
        if files.is_empty() {
            return Command::none();
        }
        if let Some(folder) = files[0].parent() {
            self.bin_state.more.folder = Some(folder.to_path_buf());
        }
        self.dialog = Some(Dialog::Busy(format!("Importing {}\u{2026}", crate::bins::count_words(files.len(), "file"))));
        let (paths, fps) = (self.paths.clone(), self.fps());
        Command::perform_blocking(
            move || {
                let plan = import_files::plan_on_disk(&files);
                let mut made = Vec::new();
                let project = paths.open();
                let report = import_files::import_all(&plan, |file, item| {
                    let project = project.as_ref().map_err(Clone::clone)?;
                    let measure = editing::measure(file)?;
                    let version = media::import(project, file, item, measure, "")?;
                    made.push(BinItem::of(item, &version, fps));
                    Ok(version.version)
                });
                (report, made)
            },
            move |(report, made)| Message::Files(FilesMessage::Imported(report, made, place)),
        )
    }

    pub(super) fn files_message(&mut self, message: FilesMessage) -> Command<Message> {
        match message {
            FilesMessage::Picked(Ok(files)) => self.files_import(files, None),
            FilesMessage::Picked(Err(e)) => {
                // No dialog on this system: the path is typed instead.
                self.open_dialog(OpenDialog::Import);
                self.status = Some((format!("{e}: type the file's path"), true));
                Command::none()
            }
            FilesMessage::Dropped(files, place) => self.files_import(files, place),
            FilesMessage::Imported(report, made, place) => {
                self.dialog = None;
                let (mut words, mut error) = import_files::words(&report);
                // The new items are in the Media bin at once, in its order;
                // the read that follows confirms them.
                for item in &made {
                    self.bins.media.retain(|m| m.item != item.item);
                    self.bins.media.push(item.clone());
                }
                self.bins.media.sort_by(|a, b| a.item.cmp(&b.item));
                if !made.is_empty() {
                    if self.bin_tab != 1 {
                        self.bin_tab = 1;
                        self.bin_state.tab_changed();
                    }
                    self.bin_pick = self.bins.media.iter().position(|m| m.item == made[0].item);
                }
                let mut put = Command::none();
                if let (Some(place), false) = (place, made.is_empty()) {
                    let indices: Vec<usize> =
                        made.iter().filter_map(|item| self.bins.media.iter().position(|m| m.item == item.item)).collect();
                    match self.files_put(&Target::Items(indices), place) {
                        Ok(command) => put = command,
                        Err(e) => {
                            words = format!("{words}. Not put in the cut: {e}");
                            error = true;
                        }
                    }
                }
                self.status = Some((words, error));
                Command::batch([put, self.refresh_bins()])
            }
        }
    }

    /// Put the Media bin's `target` in the cut at `place`.
    fn files_put(&mut self, target: &Target, place: Place) -> Result<Command<Message>, String> {
        let makers = self.bin_makers(target);
        let landing = self.paths.open().map_err(th_edit::ops::Refused).and_then(|project| {
            let sources = EditSources::new(&project, self.fps());
            bin_drag::landing(&self.doc.edit, !self.doc.read_only(), place.track, place.frame, place.insert, &makers, &sources)
        });
        drop(makers);
        let landing = landing.map_err(|e| e.0)?;
        // The edit says in the status when it takes no part of it.
        self.status = None;
        let command = self.edit_then_park(&landing.ops, landing.end);
        match self.status.take() {
            Some((why, true)) => Err(why),
            _ => Ok(command),
        }
    }
}
