//! The window before its edit is in (Magnus, cinedex__rfe 2026-11-24: "UI
//! appears instantly and then the panels and timeline show loading").
//!
//! The window opens at once. The edit and the OCIO config's text are read
//! off the UI thread ([`read`]), the bins beside them; meanwhile the viewer
//! and the timeline say what is opening, the bins say they are being read,
//! and the status line's right end names every read under way
//! ([`activity_words`]). Editing waits for the edit ([`waits`]): a key or a
//! button pressed meanwhile is told so, and nothing is changed under the
//! edit that is coming.

use primer::prelude::*;
use th_edit::document::WorkingEdit;
use th_pipe::edit_files;

use super::bin_panel::BinMessage;
use super::entries::EntryState;
use super::review::ReviewMessage;
use super::{Dialog, Editor, Message, Origin, ProjectPaths, document_io, panels, timeline_view};
use crate::colour::Colour;
use crate::display::Display;
use crate::editing::Document;
use crate::{layout_prefs, look, recent_edits};

/// What the window waits for before it can edit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum Opening {
    /// Being read: what, as the window says it ("Main", "3 shots").
    Reading(String),
    /// It did not open, and why. Nothing to edit: the window says so.
    Failed(String),
}

impl Opening {
    /// The window's title meanwhile.
    pub(super) fn title(&self) -> String {
        match self {
            Opening::Reading(what) => format!("Opening {what}\u{2026}"),
            Opening::Failed(_) => "Nothing to open".into(),
        }
    }
}

/// What to open, as the command line asked: an edit (`--edit`, its URI and
/// maybe `@version`), shots (`--shots`), or neither (every shot).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Request {
    pub edit: Option<String>,
    pub shots: Vec<String>,
}

impl Request {
    /// What is being opened, in the window's words.
    pub(super) fn words(&self) -> String {
        if let Some(edit) = &self.edit {
            let (uri, version) = match edit.rsplit_once('@') {
                Some((uri, version)) => (uri, Some(version)),
                None => (edit.as_str(), None),
            };
            let name = th_pipe::Uri::parse_address(uri).ok().and_then(|u| edit_files::edit_name(&u).ok()).unwrap_or_else(|| uri.to_owned());
            // A blank `--edit` still opens something the window can name.
            let name = if name.trim().is_empty() { "the edit".to_owned() } else { name.trim().to_owned() };
            return match version.map(str::trim).filter(|v| !v.is_empty()) {
                Some(v) => format!("{name} {v}"),
                None => name,
            };
        }
        match self.shots.len() {
            0 => "the project's shots".into(),
            1 => "1 shot".into(),
            n => format!("{n} shots"),
        }
    }
}

/// The OCIO config as read off the UI thread: its text, which the window
/// parses ([`Colour::parse`]), or None where OCIO reads it itself: a
/// built-in `ocio://` config, an `.ocioz` archive (a zip, not text).
#[derive(Debug, Clone)]
pub struct ConfigText(Option<String>);

impl ConfigText {
    pub fn read(ocio: &str) -> Result<Self, String> {
        if ocio.starts_with("ocio://") {
            return Ok(Self(None));
        }
        let bytes = std::fs::read(ocio).map_err(|e| format!("the OCIO config {ocio}: {e}"))?;
        if bytes.starts_with(b"PK\x03\x04") {
            return Ok(Self(None));
        }
        String::from_utf8(bytes).map(|text| Self(Some(text))).or(Ok(Self(None)))
    }

    /// The config, on the UI thread.
    pub fn colour(&self, ocio: &str) -> Result<Colour, String> {
        match &self.0 {
            Some(text) => Colour::parse(ocio, text),
            None => Colour::open(ocio),
        }
        .map_err(|e| format!("the OCIO config: {e}"))
    }
}

/// What the background read hands the window.
#[derive(Debug, Clone)]
pub struct Loaded {
    pub(super) doc: Document,
    /// An autosave newer than the edit, offered back.
    pub(super) recovered: Option<WorkingEdit>,
    pub(super) config: ConfigText,
}

/// Read what `request` asks for and the OCIO config at `ocio`: the work
/// the window does not wait for. Notes the edit as the one used last
/// (`recent_edits`: Play in Houdini opens it).
pub fn read(paths: &ProjectPaths, ocio: &str, request: &Request) -> Result<Loaded, String> {
    let config = ConfigText::read(ocio)?;
    let (doc, recovered) = document_io::open_document(paths, request.edit.as_deref(), &request.shots)?;
    if let Origin::Working { name, .. } | Origin::Published { name, .. } = &doc.origin {
        let file = recent_edits::path(&layout_prefs::path());
        if let Err(e) = recent_edits::note(&file, &th_pipe::storage::slashed(&paths.root), name) {
            eprintln!("cinedex: the edit opened was not noted in {}: {e}", file.display());
        }
    }
    Ok(Loaded { doc, recovered, config })
}

/// Whether `message` waits for the edit while it opens: every edit, key,
/// button and dialog does. What goes on meanwhile is reading the project
/// (the bins, the board, the journals, the farm), the panels' sizes, and
/// looking over the bins without putting anything in the cut.
pub(super) fn waits(message: &Message) -> bool {
    !matches!(
        message,
        Message::Start
            | Message::Wake
            | Message::Tick
            | Message::Loaded(_)
            | Message::Bins(_)
            | Message::Board(_)
            | Message::Mode(_)
            | Message::BinTab(_)
            | Message::PollSessions
            | Message::Sessions(_)
            | Message::PollJournal
            | Message::SharedJournal(_)
            | Message::PollLive
            | Message::PollPreviews
            | Message::Previews(_)
            | Message::PollFarm
            | Message::FarmJobs(_)
            | Message::Rendering(_)
            | Message::Refresh
            | Message::Autosave
            | Message::CloseRequested
            | Message::Dismiss
            | Message::Answer(..)
            | Message::BinsPanel(_)
            | Message::TimelinePanel(_)
            | Message::Review(ReviewMessage::Poll)
            | Message::Bin(
                BinMessage::Grid(_)
                    | BinMessage::Thumb(..)
                    | BinMessage::Fold(_)
                    | BinMessage::Hover(_)
                    | BinMessage::Moved(..)
                    | BinMessage::Over(_)
                    | BinMessage::Cancel
                    | BinMessage::Found(_)
                    | BinMessage::Copied
            )
    )
}

/// The reads under way, in the status line's words: what is opening, the
/// bins, the shots' takes, the board. None when nothing is being read.
pub(super) fn activity_words(opening: Option<&str>, bins: usize, shots: usize, board: bool) -> Option<String> {
    let mut parts = Vec::new();
    if let Some(what) = opening {
        parts.push(format!("Opening {what}"));
    }
    if bins > 0 {
        parts.push("Reading the bins".to_owned());
    }
    match shots {
        0 => {}
        1 => parts.push("Reading 1 shot's takes".to_owned()),
        n => parts.push(format!("Reading {n} shots' takes")),
    }
    if board {
        parts.push("Reading the board".to_owned());
    }
    (!parts.is_empty()).then(|| format!("{}\u{2026}", parts.join(" \u{b7} ")))
}

/// The edit the window holds until its own is in: empty, never saved.
pub(super) fn placeholder() -> Document {
    Document::new(WorkingEdit::new(24, [1920, 1080], "01:00:00:00"), Origin::Untitled)
}

impl Editor {
    /// This editor (on [`placeholder`]) waits for what `request` asks
    /// for: its first `Start` reads it.
    pub(super) fn waiting_for(mut self, request: Request) -> Self {
        self.opening = Some(Opening::Reading(request.words()));
        self.open_request = Some(request);
        self
    }

    /// The read the window starts with, once it shows: the edit and the
    /// OCIO config ([`read`]), and the bins beside them.
    pub(super) fn start_opening(&mut self, request: Request) -> Command<Message> {
        let (paths, ocio) = (self.paths.clone(), self.ocio.clone());
        let read = Command::perform_blocking(move || read(&paths, &ocio, &request).map(Box::new), Message::Loaded);
        Command::batch([read, self.refresh_bins()])
    }

    /// The edit and the config are in: edit them, or say why not.
    pub(super) fn loaded(&mut self, result: Result<Box<Loaded>, String>) -> Command<Message> {
        crate::startup_mark("edit open");
        let opened = result.and_then(|loaded| Ok((loaded.config.colour(&self.ocio)?, *loaded)));
        let (colour, loaded) = match opened {
            Ok(opened) => opened,
            Err(why) => {
                eprintln!("cinedex: {why}");
                self.status = Some((format!("Nothing to open: {why}"), true));
                self.opening = Some(Opening::Failed(why));
                return Command::none();
            }
        };
        let Loaded { doc, recovered, .. } = loaded;
        let (display, view) = colour.default();
        self.display = Display { colour, display, view, exposure: self.display.exposure };
        self.timeline_view = timeline_view::TimelineView::fitted(doc.edit.length(), doc.edit.fps.max(1) as usize);
        self.seen = doc.edit.clone();
        if let (Some(_), Origin::Working { name, .. }) = (&recovered, &doc.origin) {
            self.dialog = Some(Dialog::Recover { name: name.clone() });
        }
        self.recovered = recovered;
        self.doc = doc;
        self.opening = None;
        if matches!(&self.status, Some((s, false)) if s.starts_with(WAIT)) {
            self.status = None;
        }
        Command::batch([self.begin_editing(), self.bin_thumbs_pump()])
    }

    /// A message that waits for the edit came while it opens: say so.
    pub(super) fn wait_for_opening(&mut self) {
        match &self.opening {
            Some(Opening::Reading(what)) => self.status = Some((format!("{WAIT}still opening {what}\u{2026}"), false)),
            Some(Opening::Failed(why)) => self.status = Some((format!("Nothing to open: {why}"), true)),
            None => {}
        }
    }

    /// The reads under way, for the status line ([`activity_words`]).
    pub(super) fn activity(&self) -> Option<String> {
        let opening = match &self.opening {
            Some(Opening::Reading(what)) => Some(what.as_str()),
            _ => None,
        };
        let shots = self.entries.iter().filter(|e| e.refreshing || matches!(e.state, EntryState::Resolving)).count();
        activity_words(opening, self.bins_reading, shots, self.board_loading)
    }

    /// The window while its edit opens: the bins (or the board) as they
    /// are read, what is opening in the viewer and the timeline, the reads
    /// under way in the status line.
    pub(super) fn view_while_opening(&self, opening: &Opening) -> Element<Message> {
        let middle = if self.mode == 1 {
            self.board_view()
        } else {
            panels::bins_split(self.bins_view(), self.opening_view(opening, false), &self.layout, Message::BinsPanel).into()
        };
        let timeline = self.opening_view(opening, true);
        let body = panels::timeline_split(middle, timeline, self.timeline_height(), self.layout.timeline_collapsed, Message::TimelinePanel);
        column().push(self.toolbar()).push(body).push(self.status_line()).width(Length::Fill).height(Length::Fill).into()
    }

    /// What the viewer and the timeline show while the edit opens: what is
    /// opening, turning, or why nothing opened.
    pub(super) fn opening_view(&self, opening: &Opening, timeline: bool) -> Element<Message> {
        let mut words = column().spacing(10.0).align_x(Horizontal::Center);
        match opening {
            Opening::Reading(what) => {
                words = words.push(spinner().diameter(if timeline { 16.0 } else { 24.0 }).color(look::quiet()));
                let line = if timeline { "Reading the tracks\u{2026}".to_owned() } else { format!("Opening {what}\u{2026}") };
                words = words.push(text(line).font_size(14.0).color(look::quiet()));
            }
            Opening::Failed(why) => {
                words = words.push(text(format!("Nothing to open: {why}")).font_size(14.0).color(look::error()));
                if !timeline {
                    words = words.push(text("Close this window, or start Cinedex on another edit").font_size(13.0).color(look::quiet()));
                }
            }
        }
        container(words).padding(16.0).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center).into()
    }
}

/// How a waiting note starts: the edit's arrival clears it.
const WAIT: &str = "Wait: ";

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// The activity line names each read under way once, counts the
        /// shots right, and is there exactly when something is read.
        #[test]
        fn the_activity_line_names_every_read(
            opening in proptest::option::of("[A-Za-z][A-Za-z0-9]{0,8}"),
            bins in 0usize..3,
            shots in 0usize..40,
            board in any::<bool>(),
        ) {
            let words = activity_words(opening.as_deref(), bins, shots, board);
            let busy = opening.is_some() || bins > 0 || shots > 0 || board;
            prop_assert_eq!(words.is_some(), busy);
            let words = words.unwrap_or_default();
            if let Some(what) = &opening {
                let named = format!("Opening {what}");
                prop_assert!(words.contains(&named), "{}", words);
            }
            prop_assert_eq!(words.contains("Reading the bins"), bins > 0);
            prop_assert_eq!(words.contains("Reading the board"), board);
            match shots {
                0 => prop_assert!(!words.contains("takes")),
                1 => prop_assert!(words.contains("Reading 1 shot's takes")),
                n => {
                    let counted = format!("Reading {n} shots' takes");
                    prop_assert!(words.contains(&counted), "{}", words);
                }
            }
        }

        /// An edit's URI names it by its name, and its version when one is
        /// asked; shots by their count.
        #[test]
        fn what_is_opening_is_named(name in "[A-Z][a-z]{0,6}[0-9]{0,2}", version in proptest::option::of(1u32..100), shots in 0usize..5) {
            let edit = format!("entity:/edits/{name}{}", version.map(|v| format!("@v{v:04}")).unwrap_or_default());
            let words = Request { edit: Some(edit), shots: Vec::new() }.words();
            match version {
                Some(v) => prop_assert_eq!(words, format!("{name} v{v:04}")),
                None => prop_assert_eq!(words, name),
            }
            let shot_list: Vec<String> = (0..shots).map(|i| format!("entity:/shots/010/{i:03}")).collect();
            let words = Request { edit: None, shots: shot_list }.words();
            let expected = match shots {
                0 => "the project's shots".to_owned(),
                1 => "1 shot".to_owned(),
                n => format!("{n} shots"),
            };
            prop_assert_eq!(words, expected);
        }
    }

    proptest! {
        /// Whatever bytes are at the config's path, reading them never
        /// panics: a zip (an `.ocioz` under any name) is left to OCIO, text
        /// comes back exactly, anything else is left to OCIO to say why.
        #[test]
        fn any_config_file_is_read_or_left_to_ocio(bytes in prop::collection::vec(any::<u8>(), 0..64), zip in any::<bool>()) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("studio.ocio");
            let bytes = if zip { [b"PK\x03\x04".as_slice(), &bytes].concat() } else { bytes };
            std::fs::write(&path, &bytes).unwrap();
            let read = ConfigText::read(&path.to_string_lossy()).unwrap();
            match String::from_utf8(bytes) {
                Ok(text) if !zip => prop_assert_eq!(read.0, Some(text)),
                _ => prop_assert_eq!(read.0, None),
            }
        }

        /// Whatever the command line names (any `--edit`, any shots), the
        /// window says what it opens in words: never blank, never a panic.
        #[test]
        fn anything_asked_is_named(edit in proptest::option::of(".{0,40}"), shots in prop::collection::vec(".{0,20}", 0..4)) {
            let words = Request { edit, shots }.words();
            prop_assert!(!words.trim().is_empty(), "{:?}", words);
            prop_assert_eq!(words.trim(), words.as_str());
        }
    }

    #[test]
    fn a_built_in_or_archived_config_is_read_by_ocio() {
        assert!(ConfigText::read("ocio://default").unwrap().0.is_none());
        let dir = tempfile::tempdir().unwrap();
        let zip = dir.path().join("studio.ocioz");
        std::fs::write(&zip, b"PK\x03\x04rest").unwrap();
        assert!(ConfigText::read(&zip.to_string_lossy()).unwrap().0.is_none());
        let text = dir.path().join("studio.ocio");
        std::fs::write(&text, "ocio_profile_version: 2\n").unwrap();
        assert_eq!(ConfigText::read(&text.to_string_lossy()).unwrap().0.as_deref(), Some("ocio_profile_version: 2\n"));
        assert!(ConfigText::read(&dir.path().join("missing.ocio").to_string_lossy()).is_err());
    }
}
