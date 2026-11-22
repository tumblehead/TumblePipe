//! The menu a right-click on a bin's row opens: what can be done with the
//! shot, the picked rows, the sequence or the media item under the pointer.
//!
//! A right press on a row says which ([`BinMessage::Menu`]; a row that is
//! one of several picked stands for them all, as when it is dragged), and
//! the panel's menu is built from it at the next view, as the timeline's
//! clip menu is. [`entries`] gives the rows from [`State`], the little of
//! the editor they depend on, so the tests hold them to their rules: only
//! rows that do something, nothing that changes the cut on an edit that
//! cannot change, the farm only through its own window.
//!
//! **Farm Submit…** opens the pipeline's Farm Submit window ticked on the
//! shots (`farm_submit.rs`). Cinedex never submits by itself.

use std::path::PathBuf;

use primer::prelude::*;
use th_edit::document::{Rule, Source, VersionRule};
use th_pipe::edits::EditSources;
use th_pipe::media;

use super::bin_panel::BinMessage;
use super::file_dialogs::Dialog;
use super::{Editor, Message};
use crate::bin_thumbs::ThumbKey;
use crate::bins::{self, Target};
use crate::farm_submit;

/// What the bins keep besides their rows: the row the menu is for, words
/// on their way to the clipboard, where Import… last looked.
#[derive(Debug, Clone, Default)]
pub(in crate::editor) struct More {
    /// What the menu is for, and the point pressed.
    pub menu: Option<(Target, Point)>,
    pub copy: Option<String>,
    pub folder: Option<PathBuf>,
    /// What Farm Submit… would have started, in tests (which start nothing).
    #[cfg(test)]
    pub launched: Option<farm_submit::Launch>,
}

/// A row of the menu that is not a message the editor already has.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Act {
    Reveal,
    CopyNames,
    CopyUris,
    RefreshThumbs,
    FarmSubmit,
    /// Rename a media item, or change its note.
    Edit,
}

/// What a lookup off the UI thread found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Found {
    Folder(PathBuf),
}

/// A media item's name and note, being changed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaEdit {
    pub item: String,
    pub version: String,
    pub name: String,
    pub note: String,
    /// The note as it was.
    pub noted: String,
}

/// What the menu depends on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::editor) struct State {
    /// The edit can change (not a published version).
    pub writable: bool,
    /// The row is of the Shots bin; else of Media.
    pub shots: bool,
    /// How many items the row stands for.
    pub count: usize,
    /// One shot's URI and its workfiles: (department, version).
    pub workfiles: Option<(String, Vec<(String, String)>)>,
    /// Farm Submit can be opened here.
    pub farm: bool,
}

#[derive(Debug, Clone)]
pub(in crate::editor) enum Row {
    Item(String, Message),
    Sub(String, Vec<(String, Message)>),
}

impl Row {
    #[cfg(test)]
    pub fn label(&self) -> &str {
        match self {
            Row::Item(label, _) | Row::Sub(label, _) => label,
        }
    }
}

pub const INSERT: &str = "Insert at Playhead";
pub const OVERLAY: &str = "Overlay at Playhead";
pub const OPEN: &str = "Open in Houdini";
pub const FARM: &str = "Farm Submit\u{2026}";
pub const REVEAL: &str = "Reveal in File Browser";
pub const REFRESH: &str = "Refresh Thumbnail";
pub const EDIT: &str = "Rename or Edit Note\u{2026}";

/// "Copy Name", or "Copy 3 Names".
pub fn copy_label(what: &str, count: usize) -> String {
    if count == 1 { format!("Copy {what}") } else { format!("Copy {count} {what}s") }
}

/// The menu's rows for `target`, in order.
pub(in crate::editor) fn entries(s: &State, target: &Target) -> Vec<Row> {
    let mut rows = Vec::new();
    if s.count == 0 {
        return rows;
    }
    let act = |act: Act| Message::Bin(BinMessage::Act(act, target.clone()));
    if s.writable {
        rows.push(Row::Item(INSERT.into(), Message::Bin(BinMessage::Activate(target.clone(), false))));
        rows.push(Row::Item(OVERLAY.into(), Message::Bin(BinMessage::Activate(target.clone(), true))));
    }
    if let Some((uri, workfiles)) = s.workfiles.as_ref().filter(|w| s.shots && s.count == 1 && !w.1.is_empty()) {
        let files = workfiles
            .iter()
            .map(|(department, version)| (format!("{department}  {version}"), Message::OpenWorkfile(uri.clone(), department.clone())))
            .collect();
        rows.push(Row::Sub(OPEN.into(), files));
    }
    if s.shots && s.farm {
        rows.push(Row::Item(FARM.into(), act(Act::FarmSubmit)));
    }
    if s.count == 1 {
        rows.push(Row::Item(REVEAL.into(), act(Act::Reveal)));
    }
    rows.push(Row::Item(copy_label("Name", s.count), act(Act::CopyNames)));
    if s.shots {
        rows.push(Row::Item(copy_label("URI", s.count), act(Act::CopyUris)));
        rows.push(Row::Item(REFRESH.into(), act(Act::RefreshThumbs)));
    } else if s.count == 1 {
        rows.push(Row::Item(EDIT.into(), act(Act::Edit)));
    }
    rows
}

/// Why item `item` cannot be renamed while `edit` is open, if it cannot:
/// the edit's clips name it.
pub fn rename_problem(edit: &th_edit::document::WorkingEdit, item: &str) -> Option<String> {
    let of = |source: &Source| matches!(source, Source::Media { item: used, .. } if used == item);
    let uses = edit.video.iter().flat_map(|t| &t.clips).filter(|c| of(&c.source)).count()
        + edit.audio.iter().flat_map(|t| &t.clips).filter(|c| of(&c.source)).count();
    (uses > 0).then(|| {
        format!("{item} is in this edit ({uses} clip{}): take it out of the cut before renaming it", if uses == 1 { "" } else { "s" })
    })
}

impl Editor {
    /// The bin's indices `target` stands for, in the cut's order.
    fn bin_menu_items(&self, target: &Target) -> Vec<usize> {
        let shots = (self.bin_tab == 0).then_some(self.bins.shots.as_slice());
        let count = if self.bin_tab == 0 { self.bins.shots.len() } else { self.bins.media.len() };
        bins::target_items(target, shots, count).into_iter().filter(|&i| i < count).collect()
    }

    /// The names, and for shots the URIs, of what `target` stands for.
    fn bin_menu_names(&self, target: &Target) -> (Vec<String>, Vec<String>) {
        let items = self.bin_menu_items(target);
        if self.bin_tab == 0 {
            items.iter().map(|&i| (self.bins.shots[i].name.clone(), self.bins.shots[i].uri.clone())).unzip()
        } else {
            (items.iter().map(|&i| self.bins.media[i].item.clone()).collect(), Vec::new())
        }
    }

    pub(in crate::editor) fn bin_menu_state(&self, target: &Target) -> State {
        let items = self.bin_menu_items(target);
        let shots = self.bin_tab == 0;
        let workfiles = match (shots, items.as_slice()) {
            (true, [one]) => {
                let uri = self.bins.shots[*one].uri.clone();
                let files = self
                    .board
                    .as_ref()
                    .map(|b| b.workfiles_of(&uri).into_iter().map(|(d, w)| (d.to_owned(), w.version.to_string())).collect());
                Some((uri, files.unwrap_or_default()))
            }
            _ => None,
        };
        State { writable: !self.doc.read_only(), shots, count: items.len(), workfiles, farm: farm_here() }
    }

    /// `panel` with the menu of the row last right-pressed.
    pub(in crate::editor) fn bin_menu(&self, panel: impl Into<Element<Message>>) -> Element<Message> {
        let mut menu = context_menu(Id::new("bin menu"), panel);
        if let Some((target, _)) = &self.bin_state.more.menu {
            for row in entries(&self.bin_menu_state(target), target) {
                menu = match row {
                    Row::Item(label, message) => menu.item(label, move || message.clone()),
                    Row::Sub(label, rows) => {
                        let rows =
                            rows.into_iter().fold(MenuList::new(), |rows, (label, message)| rows.item(label, move || message.clone()));
                        menu.submenu(label, rows)
                    }
                };
            }
        }
        menu.into()
    }

    /// A right press on the row that stands for `row`, at `at`.
    pub(in crate::editor) fn bin_menu_open(&mut self, row: &Target, at: Point) {
        let target = bins::held_target(row, &self.bin_picked());
        self.bin_state.more.menu = Some((target, at));
    }

    /// A right press on the list at `at`: off every row when no row said so.
    pub(in crate::editor) fn bin_menu_at(&mut self, at: Point) {
        if self.bin_state.more.menu.as_ref().is_some_and(|m| m.1 != at) {
            self.bin_state.more.menu = None;
        }
    }

    /// What a right-click on a shot's clip in the timeline offers besides
    /// its takes and workfiles: the shot's URI, and the farm's window.
    pub(in crate::editor) fn clip_menu_more(shot: &str) -> Vec<(String, Message)> {
        let mut rows = vec![(copy_label("URI", 1), Message::Bin(BinMessage::Copy(shot.to_owned())))];
        if farm_here() {
            rows.push((FARM.to_owned(), Message::Bin(BinMessage::Farm(vec![shot.to_owned()]))));
        }
        rows
    }

    /// Start the window; in tests, which start nothing, keep what it was.
    #[cfg(test)]
    fn farm_start(&mut self, launch: farm_submit::Launch) -> Result<(), String> {
        self.bin_state.more.launched = Some(launch);
        Ok(())
    }

    #[cfg(not(test))]
    fn farm_start(&mut self, launch: farm_submit::Launch) -> Result<(), String> {
        farm_submit::start(&launch)
    }

    /// Open Farm Submit ticked on `shots`.
    pub(in crate::editor) fn farm_submit(&mut self, shots: &[String]) {
        let started = farm_launch(shots).and_then(|launch| self.farm_start(launch));
        self.status = Some(match started {
            Ok(()) => (
                format!(
                    "Farm Submit is opening on {}: nothing is submitted until you press Submit there",
                    bins::count_words(shots.len(), "shot")
                ),
                false,
            ),
            Err(e) => (format!("Farm Submit not opened: {e}"), true),
        });
    }

    /// The project the farm's jobs are stamped with: its folder's name
    /// (Python's `get_project_name`).
    pub(in crate::editor) fn project_name(&self) -> String {
        self.paths.root.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
    }

    /// The farm job a clip of `source` shows (`farm_jobs`): only a shot's.
    pub(in crate::editor) fn farm_job_of(&self, source: &Source) -> Option<&th_pipe::farm_jobs::ShotJob> {
        let Source::Shot { shot, .. } = source else { return None };
        self.farm_jobs.get(shot)
    }

    /// The right-click rows for a clip of `source` whose shot has a farm
    /// job; none for any other clip.
    pub(in crate::editor) fn farm_job_rows(&self, source: &Source) -> Vec<(String, Message)> {
        crate::farm_jobs::menu_rows(self.farm_job_of(source), manager_here())
            .into_iter()
            .map(|(label, act)| (label, Message::FarmJob(act)))
            .collect()
    }

    /// Do a farm job row: open the Farm Manager on the job, or copy its id.
    pub(in crate::editor) fn farm_job_act(&mut self, act: crate::farm_jobs::Act) {
        match act {
            crate::farm_jobs::Act::Copy(id) => {
                self.status = Some((format!("Copied farm job ID {id}"), false));
                self.bin_state.more.copy = Some(id);
            }
            crate::farm_jobs::Act::Show(id) => {
                let started = manager_launch(&id).and_then(|launch| self.farm_start(launch));
                self.status = Some(match started {
                    Ok(()) => (format!("The Farm Manager is opening on job {id}"), false),
                    Err(e) => (format!("Farm Manager not opened: {e}"), true),
                });
            }
        }
    }

    pub(in crate::editor) fn bin_act(&mut self, act: Act, target: &Target) -> Command<Message> {
        let (names, uris) = self.bin_menu_names(target);
        if names.is_empty() {
            return Command::none();
        }
        match act {
            Act::CopyNames => self.bin_state.more.copy = Some(names.join("\n")),
            Act::CopyUris => self.bin_state.more.copy = (!uris.is_empty()).then(|| uris.join("\n")),
            Act::RefreshThumbs => {
                for uri in &uris {
                    self.bin_state.thumbs.forget_shot(uri);
                }
            }
            Act::FarmSubmit => self.farm_submit(&uris),
            Act::Reveal => {
                let (paths, fps) = (self.paths.clone(), self.fps());
                let key = match uris.first() {
                    Some(uri) => ThumbKey::Shot(uri.clone()),
                    None => {
                        let item = &self.bins.media[self.bin_menu_items(target)[0]];
                        ThumbKey::Item { item: item.item.clone(), version: item.version.clone() }
                    }
                };
                return Command::perform_blocking(
                    move || folder_of(&paths, &key, fps).map(Found::Folder),
                    |found| Message::Bin(BinMessage::Found(found)),
                );
            }
            Act::Edit => {
                let item = &self.bins.media[self.bin_menu_items(target)[0]];
                let note = self
                    .paths
                    .open()
                    .and_then(|project| media::item_versions(&project, &item.item))
                    .ok()
                    .and_then(|versions| versions.into_iter().find(|v| v.version == item.version))
                    .map(|v| v.note)
                    .unwrap_or_default();
                self.dialog = Some(Dialog::MediaEdit(MediaEdit {
                    item: item.item.clone(),
                    version: item.version.clone(),
                    name: item.item.clone(),
                    noted: note.clone(),
                    note,
                }));
            }
        }
        Command::none()
    }

    pub(in crate::editor) fn bin_found(&mut self, found: Result<Found, String>) -> Command<Message> {
        match found {
            Ok(Found::Folder(folder)) => return self.update(Message::Reveal(folder)),
            Err(e) => self.status = Some((format!("Not found: {e}"), true)),
        }
        Command::none()
    }

    /// The words on their way to the clipboard are there.
    pub(in crate::editor) fn bin_copied(&mut self) {
        if let Some(words) = self.bin_state.more.copy.take() {
            self.status = Some((format!("Copied {}", bins::count_words(words.lines().count(), "line")), false));
        }
    }

    /// OK in the item's dialog: its note, then its name, each when changed.
    pub(in crate::editor) fn media_edit(&mut self, edit: MediaEdit) -> Command<Message> {
        let name = edit.name.trim().to_owned();
        let renamed = name != edit.item;
        let problem = if !renamed {
            None
        } else if let Some(problem) = th_pipe::naming::entity_name_problem(&name) {
            Some(format!("{name:?}: an item's name {problem}"))
        } else {
            rename_problem(&self.doc.edit, &edit.item)
        };
        if let Some(problem) = problem {
            self.status = Some((problem, true));
            return Command::none();
        }
        let done = self.paths.open().and_then(|project| {
            if edit.note != edit.noted {
                media::set_note(&project, &edit.item, &edit.version, &edit.note)?;
            }
            if renamed {
                media::rename_item(&project, &edit.item, &name)?;
            }
            Ok(())
        });
        match done {
            Ok(()) => {
                self.dialog = None;
                self.status =
                    Some((if renamed { format!("{} is now {name}", edit.item) } else { format!("{}: note saved", edit.item) }, false));
                self.refresh_bins()
            }
            Err(e) => {
                self.status = Some((format!("Not changed: {e}"), true));
                Command::none()
            }
        }
    }
}

#[cfg(test)]
fn farm_launch(shots: &[String]) -> Result<farm_submit::Launch, String> {
    farm_submit::launch(Some(std::path::Path::new("/hfs")), shots, &farm_submit::TestDisk)
}

#[cfg(not(test))]
fn farm_launch(shots: &[String]) -> Result<farm_submit::Launch, String> {
    let hfs = std::env::var_os("HFS").map(PathBuf::from);
    farm_submit::launch(hfs.as_deref(), shots, &farm_submit::RealDisk)
}

#[cfg(test)]
fn manager_launch(job: &str) -> Result<farm_submit::Launch, String> {
    farm_submit::manager_launch(Some(std::path::Path::new("/hfs")), job, &farm_submit::TestDisk)
}

#[cfg(not(test))]
fn manager_launch(job: &str) -> Result<farm_submit::Launch, String> {
    let hfs = std::env::var_os("HFS").map(PathBuf::from);
    farm_submit::manager_launch(hfs.as_deref(), job, &farm_submit::RealDisk)
}

/// Whether the Farm Manager can be opened from here: where Farm Submit can.
fn manager_here() -> bool {
    farm_here()
}

/// Whether Farm Submit can be opened from here: Houdini's Python is found
/// under `$HFS`. Looked for once; neither changes while Cinedex runs.
fn farm_here() -> bool {
    static HERE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *HERE.get_or_init(|| farm_launch(&[String::from("entity:/shots/x/x")]).is_ok())
}

/// The folder `key`'s media is in: a shot's best take's, an item's version's.
fn folder_of(paths: &super::ProjectPaths, key: &ThumbKey, fps: u32) -> Result<PathBuf, String> {
    let project = paths.open()?;
    let sources = EditSources::new(&project, fps);
    match key {
        ThumbKey::Shot(shot) => {
            let takes = sources.takes(shot)?;
            let take = th_edit::takes::pick(&takes, &Rule::best()).ok_or("nothing is rendered or playblasted for this shot yet")?;
            crate::reveal::media_folder(&takes[take].media).ok_or_else(|| "its media has no folder".to_owned())
        }
        ThumbKey::Item { item, version } => {
            let pinned = VersionRule::Pinned(th_edit::document::Version::try_from(version.clone())?);
            let found = sources.item(item, &pinned)?;
            found.path.parent().map(std::path::Path::to_path_buf).ok_or_else(|| "its file has no folder".to_owned())
        }
    }
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use super::*;

    fn state() -> impl Strategy<Value = State> {
        (
            any::<bool>(),
            any::<bool>(),
            0usize..4,
            proptest::option::of(prop::collection::vec(("[a-z]{3,6}", "v000[1-9]"), 0..3)),
            any::<bool>(),
        )
            .prop_map(|(writable, shots, count, workfiles, farm)| State {
                writable,
                shots,
                count,
                workfiles: workfiles.filter(|_| shots && count == 1).map(|w| ("entity:/shots/S1/sh1".to_owned(), w)),
                farm,
            })
    }

    proptest! {
        /// The rows are what the state allows and no more: nothing for no
        /// item; Insert and Overlay only on an edit that can change, and
        /// they send what a double-click does; workfiles and Reveal for one
        /// item; the farm for shots where its window can open, and only as
        /// that window; a media item's own rows for one media item.
        #[test]
        fn the_menu_offers_what_can_be_done_and_no_more(s in state(), index in 0usize..5) {
            let target = Target::Item(index);
            let rows = entries(&s, &target);
            let labels: Vec<&str> = rows.iter().map(Row::label).collect();
            let has = |label: &str| labels.contains(&label);
            if s.count == 0 {
                prop_assert!(rows.is_empty());
                return Ok(());
            }
            prop_assert_eq!(has(INSERT), s.writable);
            prop_assert_eq!(has(OVERLAY), s.writable);
            prop_assert_eq!(has(OPEN), s.workfiles.as_ref().is_some_and(|w| !w.1.is_empty()));
            prop_assert_eq!(has(FARM), s.shots && s.farm);
            prop_assert_eq!(has(REVEAL), s.count == 1);
            prop_assert_eq!(has(EDIT), !s.shots && s.count == 1);
            prop_assert_eq!(has(REFRESH), s.shots);
            prop_assert!(has(&copy_label("Name", s.count)));
            prop_assert_eq!(has(&copy_label("URI", s.count)), s.shots);
            prop_assert_eq!(labels.iter().collect::<std::collections::BTreeSet<_>>().len(), labels.len(), "a label twice");
            for row in &rows {
                let sent = |message: &Message| format!("{message:?}");
                let expected = match row {
                    Row::Item(label, message) => {
                        let expected = match label.as_str() {
                            INSERT => Message::Bin(BinMessage::Activate(target.clone(), false)),
                            OVERLAY => Message::Bin(BinMessage::Activate(target.clone(), true)),
                            FARM => Message::Bin(BinMessage::Act(Act::FarmSubmit, target.clone())),
                            REVEAL => Message::Bin(BinMessage::Act(Act::Reveal, target.clone())),
                            REFRESH => Message::Bin(BinMessage::Act(Act::RefreshThumbs, target.clone())),
                            EDIT => Message::Bin(BinMessage::Act(Act::Edit, target.clone())),
                            l if l.contains("URI") => Message::Bin(BinMessage::Act(Act::CopyUris, target.clone())),
                            _ => Message::Bin(BinMessage::Act(Act::CopyNames, target.clone())),
                        };
                        (sent(message), sent(&expected))
                    }
                    Row::Sub(_, files) => {
                        let (uri, workfiles) = s.workfiles.as_ref().unwrap();
                        let expected: Vec<(String, String)> =
                            workfiles.iter().map(|(d, v)| (format!("{d}  {v}"), sent(&Message::OpenWorkfile(uri.clone(), d.clone())))).collect();
                        (format!("{:?}", files.iter().map(|(l, m)| (l.clone(), sent(m))).collect::<Vec<_>>()), format!("{expected:?}"))
                    }
                };
                prop_assert_eq!(expected.0, expected.1);
            }
        }
    }
}
