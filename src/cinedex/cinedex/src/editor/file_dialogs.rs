//! The dialogs over the window and the file work behind them: open, save,
//! save as, publish, import, the master edit, and the autosave.

use std::path::PathBuf;

use primer::prelude::*;
use serde_json::json;
use th_edit::document::{Frame as EditFrame, FrozenSource, Source, TrackRef};
use th_pipe::edits::EditSources;
use th_pipe::master::Change;
use th_pipe::{Project, edit_files, master, media};

use crate::editing::Origin;
use crate::rpc::{self, Refusal};
use crate::{editing, look};

use super::document_io::edit_listings;
use super::{Editor, Message, bin_menu, export_dialog, timeline_dialog, timeline_menu};

/// A field of a dialog.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Field {
    Name,
    Note,
    Path,
    Item,
    Sequence,
    Frames,
}

/// One shot change of a master publish, ticked to be written or not, with
/// what the user should know about it.
#[derive(Debug, Clone)]
pub struct ChangeRow {
    pub(super) change: Change,
    pub(super) ticked: bool,
    pub(super) flag: Option<String>,
}

/// What a publish did.
#[derive(Debug, Clone)]
pub struct PublishReport {
    pub(super) name: String,
    pub(super) version: String,
    /// Shot changes written.
    pub(super) written: usize,
    /// Shot changes not written, and why.
    pub(super) failed: Vec<String>,
}

/// An edit the Open dialog lists, with its newest working and published
/// versions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditListing {
    pub(super) name: String,
    pub(super) working: Option<String>,
    pub(super) published: Option<String>,
}

/// A dialog over the window.
#[derive(Debug, Clone)]
pub(super) enum Dialog {
    /// The project's edits, one to open; `problem` is why they are not
    /// listed.
    OpenEdit {
        edits: Vec<EditListing>,
        problem: Option<String>,
    },
    /// A new working version of the named edit.
    Save {
        note: String,
        warning: Option<String>,
    },
    /// A new edit (or a new one from a published version).
    SaveAs {
        name: String,
        note: String,
        /// Save was pressed: an empty name says so under the field too.
        tried: bool,
    },
    /// For the master edit, `changes` are the shot changes it offers;
    /// `problem` is why it cannot be published.
    Publish {
        note: String,
        changes: Vec<ChangeRow>,
        problem: Option<String>,
    },
    /// Another name for a track; `others` are the names the other tracks
    /// of its kind have.
    RenameTrack {
        track: TrackRef,
        name: String,
        others: Vec<String>,
        tried: bool,
    },
    /// A planned shot put at the playhead, or (`place`) over the frames an
    /// Alt+drag swept (`range_shot`), the shot made at once when `now`.
    NewShot {
        sequence: String,
        name: editing::Offered,
        frames: String,
        place: Option<super::range_shot::Place>,
        now: bool,
        /// The project lists its shots flat: `sequence` is its implicit
        /// sequence, and the dialog shows no Sequence field.
        flat: bool,
    },
    Import {
        path: String,
        item: editing::Offered,
        note: String,
    },
    /// An autosave newer than what opened.
    Recover {
        name: String,
    },
    /// Closing with changes not saved.
    Unsaved,
    /// Something running; the window waits.
    Busy(String),
    /// A media item's name and note (`editor/bin_menu.rs`).
    MediaEdit(bin_menu::MediaEdit),
    /// File > Export movie… (`editor/export_dialog.rs`).
    Export(export_dialog::ExportDialog),
    /// File > Export Timeline… (`editor/timeline_dialog.rs`).
    Timeline(timeline_dialog::TimelineDialog),
}

/// The dialogs a button or key opens.
#[derive(Debug, Clone, Copy)]
pub enum OpenDialog {
    Open,
    Save,
    SaveAs,
    Publish,
    Import,
    NewShot,
    /// Another name for this track.
    RenameTrack(TrackRef),
}

/// What the autosave tick does with an edit's autosave.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AutosaveStep {
    Write,
    Drop,
    Nothing,
}

/// The autosave tick (and leaving) for an edit `dirty` or not, whose
/// autosave this editor `wrote` and has not dropped: a changed edit is
/// written; one back to what is saved (an undo) drops the autosave it
/// wrote, which would otherwise offer the undone change back on the next
/// open. An autosave it did not write (another session's) is left alone.
fn autosave_step(dirty: bool, wrote: bool) -> AutosaveStep {
    match (dirty, wrote) {
        (true, _) => AutosaveStep::Write,
        (false, true) => AutosaveStep::Drop,
        (false, false) => AutosaveStep::Nothing,
    }
}

fn dialog_element(dialog: &Dialog, title: &str) -> Element<Message> {
    let heading = |w: &str| text(w.to_owned()).font_size(16.0).color(look::text());
    let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
    let field = |id: &'static str, value: &str, f: Field, hint: &str| {
        text_input(Id::new(id), value.to_owned())
            .placeholder(hint.to_owned())
            .on_input(move |s| Message::Text(f, s))
            .on_submit(|| Message::Confirm)
            .width(Length::fixed(420.0))
    };
    let choice = |id: &'static str, w: &str, m: Message| {
        look::button(Id::new(id), text(w.to_owned()).font_size(13.0)).padding(Padding::from([5.0, 14.0])).on_press(move || m.clone())
    };
    let mut page = column().spacing(8.0).padding(16.0);
    let mut buttons = row().spacing(8.0);
    match dialog {
        Dialog::OpenEdit { edits, problem } => {
            page = page.push(heading("Open an edit"));
            if let Some(problem) = problem {
                page = page.push(text(problem.clone()).font_size(12.0).color(look::error()));
            } else if edits.is_empty() {
                page = page.push(label("The project has no edits yet: Save As names one."));
            } else {
                page = page.push(label("It opens in a window of its own."));
                let mut rows = column().spacing(4.0);
                for (i, e) in edits.iter().enumerate() {
                    let uri = format!("entity:/edits/{}", e.name);
                    let working = e.working.as_deref().unwrap_or("not saved yet");
                    let mut line = row()
                        .spacing(8.0)
                        .align_y(Vertical::Center)
                        .push(text(e.name.clone()).font_size(13.0).color(look::text()).width(Length::fixed(160.0)))
                        .push(
                            look::button(Id::new(format!("open {i}")), text(format!("Open ({working})")).font_size(12.0))
                                .padding(Padding::from([3.0, 10.0]))
                                .on_press({
                                    let uri = uri.clone();
                                    move || Message::OpenEdit(uri.clone())
                                }),
                        );
                    if let Some(version) = &e.published {
                        let published = format!("{uri}@{version}");
                        line = line.push(
                            look::button(Id::new(format!("play {i}")), text(format!("Published {version}")).font_size(12.0))
                                .padding(Padding::from([3.0, 10.0]))
                                .on_press(move || Message::OpenEdit(published.clone())),
                        );
                    }
                    rows = rows.push(line);
                }
                page = page.push(rows);
            }
            buttons = buttons.push(choice("cancel", "Close", Message::Dismiss));
        }
        Dialog::Save { note, warning } => {
            page = page.push(heading(&format!("Save {title}")));
            if let Some(w) = warning {
                page = page.push(text(w.clone()).font_size(12.0).color(look::caution()));
            }
            page = page.push(label("Note (what changed)")).push(field("note", note, Field::Note, "optional"));
            buttons = buttons.push(choice("ok", "Save", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::SaveAs { name, note, tried } => {
            page = page.push(heading("Save as a new edit")).push(label("Name (letters and digits)")).push(field(
                "name",
                name,
                Field::Name,
                "Main",
            ));
            // Under the field as it is typed, not in the status bar (#25).
            if let Some(problem) = editing::edit_name_problem(name).filter(|_| *tried || !name.trim().is_empty()) {
                page = page.push(text(problem).font_size(12.0).color(look::error()));
            }
            page = page.push(label("Note")).push(field("note", note, Field::Note, "optional"));
            buttons = buttons.push(choice("ok", "Save", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Publish { note, changes, problem } => {
            page = page
                .push(heading(&format!("Publish {title}")))
                .push(label("Every take resolves to the version it shows now; the publish holds the edit and its OTIO."));
            if let Some(problem) = problem {
                page = page.push(text(format!("Cannot publish: {problem}")).font_size(12.0).color(look::error()));
                buttons = buttons.push(choice("cancel", "Close", Message::Dismiss));
            } else {
                if !changes.is_empty() {
                    page = page.push(label("The master edit: these shot changes are written after the publish (untick to skip)."));
                    let mut rows = column().spacing(4.0);
                    for (i, row_) in changes.iter().enumerate() {
                        let mut line = row().spacing(8.0).align_y(Vertical::Center).push(look::tick_box(
                            Id::new(format!("change {i}")),
                            row_.ticked,
                            move |on| Message::TickChange(i, on),
                        ));
                        line = line.push(text(row_.change.describe()).font_size(12.0).color(look::text()));
                        if let Some(flag) = &row_.flag {
                            line = line.push(text(format!("({flag})")).font_size(11.0).color(look::caution()));
                        }
                        rows = rows.push(line);
                    }
                    page = page.push(rows);
                }
                page = page.push(label("Note")).push(field("note", note, Field::Note, "optional"));
                buttons = buttons.push(choice("ok", "Publish", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
            }
        }
        Dialog::NewShot { sequence, name, frames, place, now, flat } => {
            page = page.push(heading("New shot"));
            if let Some(place) = place {
                page = page.push(label(&super::range_shot::place_words(place)));
            }
            page = page.push(
                row().spacing(8.0).align_y(Vertical::Center).push(look::tick_box(Id::new("new shot now"), *now, Message::NewShotNow)).push(
                    text("Create the shot now (else it is planned: made when the master edit is published)")
                        .font_size(12.0)
                        .color(look::text()),
                ),
            );
            // A flat project's shots have no sequence to pick: every new
            // one goes into its implicit sequence (`flat_entities`).
            if !*flat {
                page = page.push(label("Sequence")).push(field("sequence", sequence, Field::Sequence, "S"));
            }
            page = page.push(label("Shot")).push(field("name", &name.text, Field::Name, "SH010")).push(label("Frames")).push(
                crate::ladder::ladder(
                    Id::new("frames ladder"),
                    field("frames", frames, Field::Frames, "48"),
                    crate::ladder::Number {
                        value: crate::number_field::number(frames, &[], None).unwrap_or(48.0),
                        min: 1.0,
                        max: 1_000_000.0,
                        decimals: 0,
                        default: 48.0,
                    },
                    |v| Message::Text(Field::Frames, format!("{}", v.round() as i64)),
                    None,
                ),
            );
            buttons = match place {
                // Convert to Shot: the clip there is a placeholder already.
                Some(p) if p.replaces => buttons
                    .push(choice("ok", if *now { "Create Shot" } else { "Plan Shot" }, Message::Confirm))
                    .push(choice("cancel", "Cancel", Message::Dismiss)),
                // Over the swept frames: a shot, or a placeholder of them.
                Some(_) => buttons
                    .push(choice("ok", if *now { "Create Shot" } else { "Plan Shot" }, Message::Confirm))
                    .push(choice("placeholder", "Placeholder", Message::Alternate))
                    .push(choice("cancel", "Cancel", Message::Dismiss)),
                None => buttons.push(choice("ok", "Insert", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss)),
            };
        }
        Dialog::RenameTrack { name, others, tried, .. } => {
            page = page.push(heading("Rename track")).push(label("Name")).push(field("name", name, Field::Name, "V1"));
            if let Some(problem) = timeline_menu::rename_problem(name, others).filter(|_| *tried || !name.is_empty()) {
                page = page.push(text(problem).font_size(12.0).color(look::error()));
            }
            buttons = buttons.push(choice("ok", "Rename", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Import { path, item, note } => {
            page = page
                .push(heading("Import media"))
                .push(label("File (a movie or a sound; it is copied into the project)"))
                .push(field("path", path, Field::Path, "C:/path/to/animatic.mov"))
                .push(label("Item name (letters and digits; an existing name gets a new version)"))
                .push(field("item", &item.text, Field::Item, "animatic"))
                .push(label("Note"))
                .push(field("note", note, Field::Note, "optional"));
            buttons = buttons.push(choice("ok", "Import", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Recover { name } => {
            page = page
                .push(heading(&format!("{name} has unsaved changes")))
                .push(label("An autosave newer than the saved version is there."));
            buttons = buttons.push(choice("ok", "Recover", Message::Confirm)).push(choice("discard", "Discard it", Message::Alternate));
        }
        Dialog::Unsaved => {
            page = page.push(heading(&format!("{title}: save the changes?")));
            buttons = buttons
                .push(choice("ok", "Save", Message::Confirm))
                .push(choice("discard", "Discard", Message::Alternate))
                .push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Busy(what) => page = page.push(heading(what)),
        Dialog::MediaEdit(edit) => {
            page = page
                .push(heading(&format!("{} {}", edit.item, edit.version)))
                .push(label("Item name (letters and digits)"))
                .push(field("item", &edit.name, Field::Item, "animatic"))
                .push(label("Note"))
                .push(field("note", &edit.note, Field::Note, "optional"));
            buttons = buttons.push(choice("ok", "OK", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Export(export) => return export_dialog::element(export),
        Dialog::Timeline(timeline) => return timeline_dialog::element(timeline),
    }
    container(page.push(buttons)).surface().border_radius(6.0).into()
}

impl Editor {
    pub(super) fn open_dialog(&mut self, which: OpenDialog) {
        self.dialog = Some(match which {
            OpenDialog::Open => match self.paths.open().and_then(|project| edit_listings(&project)) {
                Ok(edits) => Dialog::OpenEdit { edits, problem: None },
                Err(e) => Dialog::OpenEdit { edits: Vec::new(), problem: Some(e) },
            },
            OpenDialog::Save => match &self.doc.origin {
                Origin::Untitled | Origin::Published { .. } => Dialog::SaveAs { name: String::new(), note: String::new(), tried: false },
                Origin::Working { name, version } => {
                    Dialog::Save { note: String::new(), warning: self.newer_version(name, version.as_deref()) }
                }
            },
            OpenDialog::SaveAs => Dialog::SaveAs { name: String::new(), note: String::new(), tried: false },
            OpenDialog::Publish => self.publish_dialog(),
            OpenDialog::Import => Dialog::Import { path: String::new(), item: editing::Offered::default(), note: String::new() },
            OpenDialog::RenameTrack(track) => match self.rename_track_dialog(track) {
                Some(dialog) => dialog,
                None => return,
            },
            OpenDialog::NewShot => {
                let picked = self.bin_pick.and_then(|p| self.bins.shots.get(p)).or(self.bins.shots.first()).map(|s| s.sequence.clone());
                let flat = self.bins.flat_shots.clone();
                let (sequence, name) = editing::offered_shot(&self.taken_shot_names(), &picked.unwrap_or_default(), flat.as_deref());
                Dialog::NewShot {
                    sequence,
                    name: editing::Offered::new(name),
                    frames: "48".into(),
                    place: None,
                    now: false,
                    flat: flat.is_some(),
                }
            }
        });
    }

    /// The Publish dialog: for the master edit, with the shot changes its
    /// cut makes.
    fn publish_dialog(&self) -> Dialog {
        let plain = |problem| Dialog::Publish { note: String::new(), changes: Vec::new(), problem };
        let Origin::Working { name, .. } = &self.doc.origin else { return plain(None) };
        let project = match self.paths.open() {
            Ok(p) => p,
            Err(e) => return plain(Some(e)),
        };
        match master::master_edit(&project) {
            Ok(Some(m)) if m == *name => {}
            Ok(_) => return plain(None),
            Err(e) => return plain(Some(e)),
        }
        match self.master_changes(&project, name) {
            Ok(changes) => Dialog::Publish { note: String::new(), changes, problem: None },
            Err(e) => plain(Some(e)),
        }
    }

    /// The shot changes publishing this master makes, against its last
    /// published version.
    pub(super) fn master_changes(&self, project: &Project, name: &str) -> Result<Vec<ChangeRow>, String> {
        let uses = master::shot_uses(&self.doc.edit, |s| match s {
            Source::Shot { shot, .. } => Some(shot.as_str()),
            _ => None,
        })?;
        let previous = match edit_files::published_versions(project, name)?.pop() {
            Some(version) => master::shot_uses(&edit_files::load_published(project, name, &version)?, |s| match s {
                FrozenSource::Shot { shot, .. } => Some(shot.as_str()),
                _ => None,
            })?,
            None => Vec::new(),
        };
        let changes = master::changes(project, &uses, &previous, i64::from(self.fps()))?;
        Ok(changes
            .into_iter()
            .map(|change| ChangeRow { ticked: change.ticked_by_default(), flag: self.coverage(project, &change), change })
            .collect())
    }

    /// Whether the take a changed shot shows covers its new range with roll
    /// (when not, it needs a new playblast or render).
    fn coverage(&self, project: &Project, change: &Change) -> Option<String> {
        let (shot, first, last) = match change {
            Change::Create { .. } => return Some("new: nothing made for it yet".into()),
            Change::Range { shot, first, last, .. } => (shot, *first, *last),
            Change::Fps { .. } | Change::Retire { .. } => return None,
        };
        let roll = th_pipe::timeline::get_frame_range(&project.config, shot).ok().flatten().map_or((0, 0), |r| (r.start_roll, r.end_roll));
        let (want_first, want_last) = (first - roll.0, last + roll.1);
        let source = self.doc.edit.video.iter().flat_map(|t| &t.clips).map(|c| &c.source).find(|s| match s {
            Source::Shot { shot: s, .. } => th_pipe::Uri::parse_address(s).ok().as_ref() == Some(shot),
            _ => false,
        })?;
        let resolved = self.entry_of(source).and_then(|i| self.entries[i].resolved())?;
        let Some(media) = &resolved.media else { return Some("no take yet".into()) };
        let (have_first, have_last) = (resolved.first, resolved.first + media.frame_count() as EditFrame - 1);
        (have_first > want_first || have_last < want_last)
            .then(|| format!("its take covers {have_first}-{have_last}: {want_first}-{want_last} needs a new one"))
    }

    /// Make this edit the master (or, `on` false, leave the project without
    /// one).
    pub(super) fn set_master(&mut self, on: bool) -> Command<Message> {
        let Origin::Working { name, .. } = self.doc.origin.clone() else { return Command::none() };
        let paths = self.paths.clone();
        Command::perform_blocking(
            move || {
                let project = paths.open()?;
                master::set_master_edit(&project, on.then_some(name.as_str()))?;
                master::master_edit(&paths.open()?)
            },
            Message::MasterSet,
        )
    }

    /// A warning when someone saved a newer working version since this one.
    fn newer_version(&self, name: &str, from: Option<&str>) -> Option<String> {
        let project = self.paths.open().ok()?;
        let last = edit_files::working_versions(&project, name).ok()?.pop()?;
        if Some(last.as_str()) == from {
            return None;
        }
        let who = edit_files::working_context(&project, name, &last).map(|s| s.user).unwrap_or_default();
        let by = if who.is_empty() { String::new() } else { format!(" by {who}") };
        Some(format!("{last} was saved{by} since you opened {}: saving makes the next version from yours.", from.unwrap_or("this edit")))
    }

    pub(super) fn save(&mut self, name: String, note: String, create: bool) -> Command<Message> {
        let paths = self.paths.clone();
        let edit = self.doc.edit.clone();
        let from = match &self.doc.origin {
            Origin::Working { name: n, version } if *n == name => version.clone(),
            _ => None,
        };
        self.dialog = Some(Dialog::Busy(format!("Saving {name}…")));
        Command::perform_blocking(
            move || {
                let project = paths.open()?;
                if create {
                    if edit_files::list_edits(&project)?.contains(&name) {
                        return Err(format!("There is an edit named {name} already"));
                    }
                    edit_files::create_edit(&project, &name)?;
                }
                let version = edit_files::save_working(&project, &name, &edit, from.as_deref(), &note)?;
                edit_files::discard_autosave(&project, &name)?;
                Ok((name, version))
            },
            Message::Saved,
        )
    }

    /// Publish, then write `changes` (a master's ticked shot changes) in
    /// order; one that fails does not stop the rest.
    pub(super) fn publish(&mut self, note: String, changes: Vec<Change>) -> Command<Message> {
        let Origin::Working { name, .. } = self.doc.origin.clone() else { return Command::none() };
        let paths = self.paths.clone();
        let edit = self.doc.edit.clone();
        let fps = self.fps();
        self.dialog = Some(Dialog::Busy(format!("Publishing {name}…")));
        Command::perform_blocking(
            move || {
                let project = paths.open()?;
                let frozen = th_edit::freeze::freeze(&edit, &EditSources::new(&project, fps))?;
                let version = edit_files::publish(&project, &name, &frozen, &note)?;
                let stamp = th_pipe::entities::archive_stamp();
                let mut failed = Vec::new();
                for change in &changes {
                    if let Err(e) = paths.open().and_then(|p| master::apply(&p, change, &stamp)) {
                        failed.push(format!("{}: {e}", change.describe()));
                    }
                }
                Ok(PublishReport { name, version, written: changes.len() - failed.len(), failed })
            },
            Message::Published,
        )
    }

    pub(super) fn import(&mut self, path: String, item: String, note: String) -> Command<Message> {
        let paths = self.paths.clone();
        self.dialog = Some(Dialog::Busy(format!("Importing {item}…")));
        Command::perform_blocking(
            move || {
                let file = PathBuf::from(path.trim().trim_matches('"'));
                let measure = editing::measure(&file)?;
                let project = paths.open()?;
                let version = media::import(&project, &file, &item, measure, &note)?;
                Ok(format!("{item} {}", version.version))
            },
            Message::Imported,
        )
    }

    pub(super) fn autosave(&mut self) {
        let Origin::Working { name, .. } = &self.doc.origin else { return };
        let name = name.clone();
        let result = match autosave_step(self.doc.dirty(), self.autosaved.as_deref() == Some(name.as_str())) {
            AutosaveStep::Write => {
                self.autosaved = Some(name.clone());
                self.paths.open().and_then(|p| edit_files::autosave(&p, &name, &self.doc.edit))
            }
            AutosaveStep::Drop => {
                self.autosaved = None;
                self.paths.open().and_then(|p| edit_files::discard_autosave(&p, &name))
            }
            AutosaveStep::Nothing => return,
        };
        if let Err(e) = result {
            self.status = Some((format!("Autosave failed: {e}"), true));
        }
    }

    pub(super) fn dialog_view(&self) -> Option<impl Fn() -> Element<Message> + 'static> {
        let dialog = self.dialog.clone()?;
        let title = self.doc.title();
        Some(move || dialog_element(&dialog, &title))
    }
}

impl Editor {
    /// The file dialogs' messages (typing into a field, the buttons, and
    /// what their work answers): `update` hands each of them here.
    pub(super) fn file_update(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Text(f, value) => {
                let taken = if matches!(self.dialog, Some(Dialog::NewShot { .. })) { self.taken_shot_names() } else { Vec::new() };
                match (&mut self.dialog, f) {
                    (
                        Some(
                            Dialog::Save { note, .. }
                            | Dialog::Publish { note, .. }
                            | Dialog::SaveAs { note, .. }
                            | Dialog::Import { note, .. },
                        ),
                        Field::Note,
                    ) => *note = value,
                    (Some(Dialog::SaveAs { name, .. }), Field::Name) => *name = value,
                    (Some(Dialog::RenameTrack { name, .. }), Field::Name) => *name = value,
                    (Some(Dialog::NewShot { name, .. }), Field::Name) => name.type_text(value),
                    // A flat project's has no Sequence field: nothing to type.
                    (Some(Dialog::NewShot { flat: true, .. }), Field::Sequence) => {}
                    (Some(Dialog::NewShot { sequence, name, .. }), Field::Sequence) => {
                        name.follow(editing::next_shot_name(&taken, &value));
                        *sequence = value;
                    }
                    (Some(Dialog::NewShot { frames, .. }), Field::Frames) => *frames = value,
                    (Some(Dialog::Import { path, item, .. }), Field::Path) => {
                        // Name the item after the file until a name is typed.
                        item.follow(editing::item_name_of(&value));
                        *path = value;
                    }
                    (Some(Dialog::Import { item, .. }), Field::Item) => item.type_text(value),
                    (Some(Dialog::MediaEdit(edit)), Field::Item) => edit.name = value,
                    (Some(Dialog::MediaEdit(edit)), Field::Note) => edit.note = value,
                    _ => {}
                }
            }
            Message::Confirm => match self.dialog.clone() {
                Some(Dialog::Save { note, .. }) => {
                    if let Origin::Working { name, .. } = self.doc.origin.clone() {
                        return self.save(name, note, false);
                    }
                }
                Some(Dialog::SaveAs { name, note, .. }) => {
                    if editing::edit_name_problem(&name).is_some() {
                        // The dialog stays and says why, under the field.
                        if let Some(Dialog::SaveAs { tried, .. }) = &mut self.dialog {
                            *tried = true;
                        }
                        return Command::none();
                    }
                    return self.save(name.trim().to_owned(), note, true);
                }
                Some(Dialog::Publish { problem: Some(_), .. }) => {}
                Some(Dialog::Publish { note, changes, problem: None }) => {
                    let ticked = changes.into_iter().filter(|r| r.ticked).map(|r| r.change).collect();
                    return self.publish(note, ticked);
                }
                Some(Dialog::RenameTrack { track, name, others, .. }) => return self.rename_track(track, name, &others),
                Some(Dialog::NewShot { sequence, name, frames, place, now, .. }) => {
                    return self.new_shot(&sequence, &name.text, &frames, place, now);
                }
                Some(Dialog::Import { path, item, note }) => {
                    let item = item.text.trim().to_owned();
                    if let Some(problem) = th_pipe::naming::entity_name_problem(&item) {
                        self.status = Some((format!("{item:?}: an item's name {problem}"), true));
                        return Command::none();
                    }
                    return self.import(path, item, note);
                }
                Some(Dialog::MediaEdit(edit)) => return self.media_edit(edit),
                Some(Dialog::Recover { .. }) => {
                    if let Some(edit) = self.recovered.take() {
                        self.doc.edit = edit;
                        self.dialog = None;
                        // Another cut opened in this window: fit it.
                        self.fit_timeline();
                        return self.changed();
                    }
                    self.dialog = None;
                }
                Some(Dialog::Unsaved) => {
                    self.quit_after_save = true;
                    self.open_dialog(OpenDialog::Save);
                }
                Some(Dialog::Export(_)) => return self.export_update(export_dialog::ExportMessage::Start),
                Some(Dialog::Timeline(_)) => return self.timeline_update(timeline_dialog::TimelineMessage::Start),
                // Open: each edit has its own buttons; Enter picks none.
                Some(Dialog::OpenEdit { .. } | Dialog::Busy(_)) | None => {}
            },
            Message::Alternate => match &self.dialog {
                Some(Dialog::Recover { name }) => {
                    let discarded = self.paths.open().and_then(|project| edit_files::discard_autosave(&project, name));
                    self.recovered = None;
                    self.dialog = None;
                    if let Err(e) = discarded {
                        self.status = Some((format!("The autosave was not removed, so it will be offered again: {e}"), true));
                    }
                }
                Some(Dialog::Unsaved) => {
                    if let Origin::Working { name, .. } = &self.doc.origin
                        && let Err(e) = self.paths.open().and_then(|project| edit_files::discard_autosave(&project, name))
                    {
                        self.status = Some((format!("The autosave was not removed, so it will be offered again: {e}"), true));
                    }
                    self.leave();
                }
                Some(Dialog::NewShot { place: Some(place), frames, .. }) => {
                    let (place, frames) = (*place, frames.clone());
                    return self.placeholder(place, &frames);
                }
                _ => {}
            },
            Message::NewShotNow(on) => {
                if let Some(Dialog::NewShot { now, .. }) = &mut self.dialog {
                    *now = on;
                }
            }
            Message::Dismiss => {
                if !matches!(self.dialog, Some(Dialog::Busy(_)))
                    && !export_dialog::holds(&self.dialog)
                    && !timeline_dialog::holds(&self.dialog)
                {
                    self.dialog = None;
                    self.quit_after_save = false;
                }
            }
            Message::Saved(result) => {
                self.dialog = None;
                if let Some(call) = self.agent_save.take() {
                    call.answer(match &result {
                        Ok((name, version)) => Ok(json!({"edit": name, "version": version})),
                        Err(e) => Err(Refusal::new(rpc::REFUSED, e.clone())),
                    });
                }
                match result {
                    Ok((name, version)) => {
                        // The save dropped the autosave.
                        self.autosaved = None;
                        self.events.push("saved", json!({"edit": name, "version": version}));
                        self.status = Some((format!("Saved {name} {version}"), false));
                        self.doc.origin = Origin::Working { name, version: Some(version) };
                        self.doc.mark_saved();
                        if self.quit_after_save {
                            self.leave();
                        }
                    }
                    Err(e) => {
                        self.quit_after_save = false;
                        self.status = Some((format!("Not saved: {e}"), true));
                    }
                }
            }
            Message::Published(result) => {
                self.dialog = None;
                match result {
                    Ok(report) => {
                        self.events.push(
                            "published",
                            json!({"edit": report.name, "version": report.version, "written": report.written, "failed": report.failed}),
                        );
                        let mut words = format!("Published {} {}", report.name, report.version);
                        if report.written > 0 {
                            words += &format!("; {} shot change{} written", report.written, if report.written == 1 { "" } else { "s" });
                        }
                        if !report.failed.is_empty() {
                            words += &format!("; not written: {}", report.failed.join("; "));
                        }
                        self.status = Some((words, !report.failed.is_empty()));
                        if report.written > 0 {
                            return Command::batch([self.refresh_bins(), self.re_resolve_shots()]);
                        }
                    }
                    Err(e) => self.status = Some((format!("Not published: {e}"), true)),
                }
            }
            Message::TickChange(index, on) => {
                if let Some(Dialog::Publish { changes, .. }) = &mut self.dialog
                    && let Some(row) = changes.get_mut(index)
                {
                    row.ticked = on;
                }
            }
            Message::SetMaster(on) => return self.set_master(on),
            Message::MasterSet(result) => match result {
                Ok(master) => {
                    self.status = Some((
                        match &master {
                            Some(m) => format!("{m} is the master edit"),
                            None => "The project has no master edit".into(),
                        },
                        false,
                    ));
                    self.bins.master = master;
                }
                Err(e) => self.status = Some((format!("The master edit: {e}"), true)),
            },
            Message::Imported(result) => {
                self.dialog = None;
                match result {
                    Ok(what) => {
                        self.status = Some((format!("Imported {what}"), false));
                        self.bin_tab = 1;
                        return self.refresh_bins();
                    }
                    Err(e) => self.status = Some((format!("Not imported: {e}"), true)),
                }
            }
            other => unreachable!("{other:?} is not a file dialog's message"),
        }
        Command::none()
    }
}

#[cfg(test)]
mod autosave_tests {
    use super::*;
    use proptest::prelude::*;

    #[derive(Debug, Clone, Copy)]
    enum Act {
        Change,
        UndoToSaved,
        Save,
        Tick,
        Leave,
    }

    proptest! {
        /// The autosave file, through any run of changes, undos back to
        /// what is saved, saves, ticks and leaving: after a tick it is
        /// there exactly while the edit has unsaved changes (or it was
        /// another session's, never written here); after leaving a clean
        /// edit no autosave this editor wrote is left to offer an undone
        /// change back.
        #[test]
        fn an_autosave_is_there_only_while_the_edit_is_changed(
            other in any::<bool>(),
            acts in prop::collection::vec(prop_oneof![
                Just(Act::Change), Just(Act::UndoToSaved), Just(Act::Save), Just(Act::Tick), Just(Act::Leave)
            ], 0..30),
        ) {
            // `file`: whose autosave is on disk (Some(true): this editor's).
            let (mut dirty, mut wrote, mut file) = (false, false, other.then_some(false));
            for act in acts {
                let tick = |dirty: bool, wrote: &mut bool, file: &mut Option<bool>| match autosave_step(dirty, *wrote) {
                    AutosaveStep::Write => { *wrote = true; *file = Some(true); }
                    AutosaveStep::Drop => { *wrote = false; *file = None; }
                    AutosaveStep::Nothing => {}
                };
                match act {
                    Act::Change => dirty = true,
                    Act::UndoToSaved => dirty = false,
                    Act::Save => { dirty = false; wrote = false; file = None; }
                    Act::Tick => {
                        let (before, ours) = (file, wrote);
                        tick(dirty, &mut wrote, &mut file);
                        prop_assert_eq!(file == Some(true), dirty, "after a tick");
                        if !dirty && !ours {
                            prop_assert_eq!(file, before, "a clean edit's tick touched an autosave it did not write");
                        }
                    }
                    Act::Leave => {
                        if !dirty {
                            let (before, ours) = (file, wrote);
                            tick(dirty, &mut wrote, &mut file);
                            prop_assert_ne!(file, Some(true), "left a clean edit with its autosave");
                            if !ours {
                                prop_assert_eq!(file, before, "leaving dropped an autosave it did not write");
                            }
                        }
                    }
                }
                // Another session's autosave goes only by a save (or a
                // write of ours over it), never by a drop.
                prop_assert!(wrote == (file == Some(true)));
            }
        }
    }
}
