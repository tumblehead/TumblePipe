//! The window `--frames` opens (#75): rendered frames played as the editor
//! plays a cut, through the project's OCIO display (`previews` says which
//! frames, `reel` how they lie on the timeline).
//!
//! It is the editor's window cut down: the menu bar (File, Playback, View),
//! the top bar with the frame number in the accent, the picture with the
//! editor's display bar under it (display, view, exposure), and a one-row
//! timeline: the editor's tracks (`tracks`, not editable) whose clips are
//! the runs of frames on disk and whose hatched gaps are frames not
//! rendered yet, with the ruler's buffered strip and the playhead. Press or
//! drag on it to scrub. The keys are the editor's Playback keys: Space or Up
//! plays and pauses, Left/Right step a frame, Alt+Left/Right a second,
//! Ctrl+Left/Right go to the previous or next run's edge, Home/End to the
//! first and last frame; L turns looping on and off (on to start with).
//!
//! The frames are decoded by the editor's engine (`engine`): each group of
//! frames is one source, read up to six EXRs at a time ahead of the
//! playhead and kept around it. Playback runs at `--fps` (the shot's,
//! which the Farm Manager passes; 24 without) and waits for a frame not
//! decoded yet rather than skip it: the top bar then says it is waiting
//! for frames and the rate it plays at, and the buffered strip shows how
//! far ahead it has read. A gap plays as black, saying the frame is not
//! rendered yet.
//!
//! A render still going: the folder (or batch) is read again every two
//! seconds off the UI thread, and frames that landed fill their gaps (a
//! frame that failed to read because it was still being written is read
//! again once its file changes). It can open before the first frame lands
//! (Remy, 2026-11-20): a folder with no frames yet, there or not, given the
//! frame list (`--expect`) opens with every frame a gap, the picture saying
//! it waits for the first frame ([`Source::read_or_wait`]); the first frames
//! to land take their place, and the version's AOV folders are listed again.
//!
//! Two choosers sit before the display's, each only when there is something
//! to choose. **AOV**: the layers the EXR's header lists (`th_media`'s
//! `aov_rows`) and, for a render in the pipeline's folders, the other AOV
//! folders of its version (`th_pipe::preview_place`); a layer of the file
//! shows at once, an AOV folder swaps the frames in place. An AOV that is
//! not colour (depth, ids, a cryptomatte) says so and is shown as written,
//! through no display transform ([`transform_of`]). **Version**: the other
//! versions of what is shown, newest first, saying which are partial or
//! have no frames; Page Up and Page Down step to the newer and older one,
//! as in the editor. A swap keeps the frame number and goes on playing; a
//! finished playblast's version is its movie.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use futures::channel::mpsc::Sender;
use primer::keyboard::{Key, Modifiers, Named};
use primer::menu::{Accelerator, Menu, MenuItem};
use primer::prelude::*;
use primer::subscription::{self, time};
use th_edit::document::TrackRef;
use th_media::{AovRow, AovTarget, ExrSequence, ExrView, FrameKey, JpgSequence, LayerKind, Media};
use th_pipe::preview_place::{self, Place, VersionRow, VersionState};

use crate::colour::{Colour, Lut};
use crate::display::{Display, display_bar, theme};
use crate::engine::{Clip, Content, Engine, Timeline};
use crate::glyph::{self, Glyph};
use crate::layer_view;
use crate::look;
use crate::menu_words::{self, Line, Roomy};
use crate::previews::open_frames;
use crate::reel::{Fill, Group, Reel, Run, Tick, Transport};
use crate::take_menu::marked_row;
use crate::tracks::{self, ClipView, RowView, Tracks};
use crate::video::{Picture, Video};

/// Decoded frames kept in memory.
const BUFFER_BYTES: usize = 2 << 30;
/// How often the frames are looked for again.
const RESCAN_EVERY: Duration = Duration::from_secs(2);

/// What was asked to be shown, read again on every rescan.
#[derive(Debug, Clone)]
pub(crate) struct Source {
    pub(crate) path: PathBuf,
    pub(crate) fill: Fill,
}

impl Source {
    pub(crate) fn read(&self) -> Result<Reel, String> {
        open_frames(&self.path).map(|stills| Reel::of(&stills, if stills.exact { &Fill::Exact } else { &self.fill }))
    }

    /// [`Self::read`], or before the first frame lands, the frame list as
    /// gaps ([`Reel::waiting`]): for a folder (there or not yet) with no
    /// frames in it, given the frames it is to have (`--expect`). Anything
    /// else (a batch, a frame, a movie, no frame list) is read as it is,
    /// its error kept.
    pub(crate) fn read_or_wait(&self) -> Result<Reel, String> {
        self.read().or_else(|error| self.waiting().ok_or(error))
    }

    fn waiting(&self) -> Option<Reel> {
        let Fill::List(numbers) = &self.fill else { return None };
        let name = self.path.file_name()?.to_string_lossy();
        let named_as_file = th_pipe::preview_place::is_frame_file(&name) || crate::reel::is_movie(&self.path);
        if numbers.is_empty() || self.path.is_file() || named_as_file || th_pipe::previews::is_batch(&self.path) {
            return None;
        }
        Some(Reel::waiting(&self.path, numbers))
    }
}

/// What is beside the frames shown, in the pipeline's folders: where they
/// sit, their version's AOV folders and the versions of the same thing.
/// All empty for frames that sit nowhere (a batch, any other folder).
#[derive(Debug, Clone, Default)]
pub(crate) struct Beside {
    place: Option<Place>,
    aovs: Vec<(String, PathBuf)>,
    versions: Vec<VersionRow>,
}

impl Beside {
    /// Read for the frames at `path`: a folder listing per version, so off
    /// the UI thread.
    fn of(path: &Path) -> Self {
        let Some(place) = preview_place::place(path) else { return Self::default() };
        Beside { aovs: preview_place::aovs(&place), versions: preview_place::versions(&place), place: Some(place) }
    }

    fn aov(&self) -> Option<&str> {
        self.place.as_ref().and_then(Place::aov)
    }

    fn version(&self) -> Option<&str> {
        self.place.as_ref().map(Place::version)
    }
}

/// What a float frame is shown through: the colour space a colour layer's
/// display transform starts from, or None for data, shown as written. A
/// colour layer of frames that do not say their colour space cannot be
/// shown right, and is refused.
pub(crate) fn transform_of(kind: LayerKind, colour_space: Option<&str>) -> Result<Option<&str>, String> {
    match (kind, colour_space) {
        (LayerKind::Colour, Some(space)) => Ok(Some(space)),
        (LayerKind::Colour, None) => Err("the frames do not say their colour space (oiio:ColorSpace)".into()),
        (LayerKind::Data | LayerKind::Cryptomatte, _) => Ok(None),
    }
}

/// The Version menu's rows for `versions`, the one shown ticked as the
/// take menu ticks its own.
pub(crate) fn version_menu_rows(versions: &[VersionRow], shown: Option<&str>) -> Vec<String> {
    versions.iter().map(|row| marked_row(&row.label(), Some(row.version.as_str()) == shown)).collect()
}

#[derive(Debug, Clone)]
pub(crate) enum Message {
    Start,
    Wake,
    Tick,
    TogglePlay,
    Step(i64),
    Seconds(i64),
    Edge(i64),
    Seek(usize),
    Last,
    Loop,
    /// View > Flip Horizontally (Shift+H): the picture mirrored, or back.
    Flip,
    Display(usize),
    View(usize),
    Exposure(f32),
    Rescan,
    /// View > Refresh: the frames, and what is beside them, read again.
    Refresh,
    /// The AOV chooser's row.
    Aov(usize),
    /// The Version chooser's row.
    Version(usize),
    /// Page Up (+1, newer) and Page Down.
    VersionStep(i8),
    /// What is beside the frames, for the listing asked for.
    Beside(u64, Beside),
    /// Another version's or AOV's frames read, for the swap asked for.
    Swapped(u64, Source, Result<Reel, String>, Beside),
    /// A group's frames opened, for the generation of the group asked for.
    Opened(usize, u64, Result<Arc<Media>, String>),
    /// The frames read again (of the source of that epoch), with the
    /// modification times of the files of frames that failed to read.
    Scanned(u64, Result<Reel, String>, Vec<(FrameKey, Option<SystemTime>)>),
    Ignore,
    Quit,
}

/// A group's source in the engine.
#[derive(Debug, Clone, Default)]
struct Opened {
    /// Bumped whenever its files change; an open of an older one is dropped.
    generation: u64,
    /// Its display transform (None for JPGs, display-referred).
    lut: Option<Arc<Lut>>,
    media: Option<Arc<Media>>,
    /// Why it cannot play.
    error: Option<String>,
}

pub(crate) struct FramesView {
    source: Source,
    /// What the window opened on: its frame list (`--expect`) is that
    /// version's, and no other's.
    home: Source,
    beside: Beside,
    /// The layer chosen in the AOV chooser; None, each file's default.
    view: Option<ExrView>,
    /// Counts listings and swaps asked for: an answer to an older one is dropped.
    asked: u64,
    /// Counts sources shown: a rescan of an earlier one is dropped.
    epoch: u64,
    reel: Reel,
    display: Display,
    engine: Engine,
    opened: Vec<Opened>,
    transport: Transport,
    /// The picture is mirrored left to right (`flip`), for this window's
    /// session only.
    flipped: bool,
    /// The picture on screen and the frame it is.
    shown: Option<(FrameKey, Picture)>,
    frames_shown: u64,
    /// The modification time of each frame's file when it failed to read.
    failed_at: HashMap<FrameKey, Option<SystemTime>>,
    scanning: bool,
    status: Option<String>,
    wake_slot: Arc<Mutex<Option<Sender<Message>>>>,
    started: bool,
    quit: bool,
}

/// The engine clips of `reel`'s frames on disk, at `fps`.
pub(crate) fn clips(reel: &Reel, fps: u32) -> Vec<Clip> {
    reel.runs()
        .into_iter()
        .filter(|r| r.present)
        .map(|r| Clip {
            source: r.group,
            track: 0,
            at: r.at,
            frames: r.len,
            first: r.first,
            fade_in: 0,
            fade_out: 0,
            lead: 0,
            tail: 0,
            opacity: 1.0,
            transform: Default::default(),
            rate: (fps as i32, 1),
        })
        .collect()
}

/// Open `group`'s frames at `rate`: an EXR sequence's header read from its
/// first frame on disk, or JPGs (nothing read), or a movie's index. Off the
/// UI thread. EXRs need not say their colour space here: a data AOV shows
/// without one ([`transform_of`] refuses a colour layer that does not).
fn open_group(group: &Group, rate: (i32, i32)) -> Result<Arc<Media>, String> {
    if let Some(movie) = group.movie() {
        return Media::movie(&movie.to_string_lossy()).map(Arc::new).map_err(|e| e.to_string());
    }
    let index = group.first_present().ok_or("none of its frames is rendered yet")?;
    let first = group.slots.first().and_then(|s| s.number).unwrap_or(0);
    let media = if group.is_jpg() {
        Media::jpg(JpgSequence::new(first, 1, group.paths()), rate)
    } else {
        Media::exr_any_from(ExrSequence::new(first, 1, group.paths()), rate, index).map_err(|e| e.to_string())?
    };
    Ok(Arc::new(media))
}

/// The frame numbers `run` spans, as a label: `1001–1050`, `1023`.
fn span_label(reel: &Reel, run: &Run) -> String {
    let group = &reel.groups[run.group];
    let number = |i: usize| group.slots[i].number.map_or_else(|| (i + 1).to_string(), |n| n.to_string());
    if run.len == 1 { number(run.first) } else { format!("{}\u{2013}{}", number(run.first), number(run.first + run.len - 1)) }
}

/// What the play button says under the pointer: what it does now, and both
/// keys that do the same.
pub(crate) fn play_tip(playing: bool) -> &'static str {
    if playing { "Pause (Space or Up)" } else { "Play (Space or Up)" }
}

/// What a key does in the window (the menu shows the same keys; Up is a
/// second play and pause, as Space).
pub(crate) fn key_message(key: &Key, modifiers: Modifiers) -> Option<Message> {
    if crate::flip::is_key(key, modifiers) {
        return Some(Message::Flip);
    }
    let command = modifiers.command();
    match key {
        Key::Named(Named::ArrowRight) if command => Some(Message::Edge(1)),
        Key::Named(Named::ArrowLeft) if command => Some(Message::Edge(-1)),
        Key::Named(Named::ArrowRight) if modifiers.alt => Some(Message::Seconds(1)),
        Key::Named(Named::ArrowLeft) if modifiers.alt => Some(Message::Seconds(-1)),
        Key::Named(Named::ArrowRight) => Some(Message::Step(1)),
        Key::Named(Named::ArrowLeft) => Some(Message::Step(-1)),
        Key::Named(Named::Home) => Some(Message::Seek(0)),
        Key::Named(Named::End) => Some(Message::Last),
        Key::Named(Named::Space) => Some(Message::TogglePlay),
        // Up plays and pauses too, alone: with a modifier it is left free.
        Key::Named(Named::ArrowUp) if modifiers == Modifiers::NONE => Some(Message::TogglePlay),
        // The editor's version keys; with Ctrl or Shift (its department
        // and source keys) they are left free.
        Key::Named(Named::PageUp) if modifiers == Modifiers::NONE => Some(Message::VersionStep(1)),
        Key::Named(Named::PageDown) if modifiers == Modifiers::NONE => Some(Message::VersionStep(-1)),
        Key::Character('r' | 'R') if command => Some(Message::Refresh),
        Key::Character('l' | 'L') if !command && !modifiers.alt => Some(Message::Loop),
        _ => None,
    }
}

/// The menu the versions are listed in, under its two step rows.
const VERSION_MENU: &str = "&Version";

const NONE: Modifiers = Modifiers::NONE;
const ALT: Modifiers = Modifiers { alt: true, ..Modifiers::NONE };

/// One menu row: its label, message and key.
type Row3 = (&'static str, Message, Option<(Key, Modifiers)>);

/// The menus: (title, rows), None rows being separators.
fn headings(playing: bool, looping: bool, flipped: bool) -> Vec<(&'static str, Vec<Option<Row3>>)> {
    let named = |n: Named, m: Modifiers| Some((Key::Named(n), m));
    let mut file = Vec::new();
    if !cfg!(target_os = "macos") {
        file.push(Some(("E&xit", Message::Quit, None)));
    }
    vec![
        ("&File", file),
        (
            "&Playback",
            vec![
                Some((if playing { "&Pause" } else { "&Play" }, Message::TogglePlay, named(Named::Space, NONE))),
                None,
                Some(("Previous Frame", Message::Step(-1), named(Named::ArrowLeft, NONE))),
                Some(("Next Frame", Message::Step(1), named(Named::ArrowRight, NONE))),
                Some(("Back One Second", Message::Seconds(-1), named(Named::ArrowLeft, ALT))),
                Some(("Forward One Second", Message::Seconds(1), named(Named::ArrowRight, ALT))),
                Some(("Previous Gap Edge", Message::Edge(-1), named(Named::ArrowLeft, Modifiers::COMMAND))),
                Some(("Next Gap Edge", Message::Edge(1), named(Named::ArrowRight, Modifiers::COMMAND))),
                None,
                Some(("Go to &Start", Message::Seek(0), named(Named::Home, NONE))),
                Some(("Go to &End", Message::Last, named(Named::End, NONE))),
                None,
                Some((if looping { "\u{2713} &Loop" } else { "&Loop" }, Message::Loop, Some((Key::Character('l'), NONE)))),
            ],
        ),
        (
            "&View",
            vec![
                Some((crate::flip::menu_label(flipped), Message::Flip, Some((Key::Character(crate::flip::KEY.0), crate::flip::KEY.1)))),
                None,
                Some(("&Refresh", Message::Refresh, Some((Key::Character('r'), Modifiers::COMMAND)))),
            ],
        ),
        (
            VERSION_MENU,
            vec![
                Some(("&Newer Version", Message::VersionStep(1), named(Named::PageUp, NONE))),
                Some(("Older &Version", Message::VersionStep(-1), named(Named::PageDown, NONE))),
            ],
        ),
    ]
}

fn waker(slot: &Arc<Mutex<Option<Sender<Message>>>>) -> impl Fn() + Send + 'static {
    let slot = Arc::clone(slot);
    move || {
        if let Some(sender) = slot.lock().unwrap().as_mut() {
            let _ = sender.try_send(Message::Wake);
        }
    }
}

impl FramesView {
    pub(crate) fn new(source: Source, reel: Reel, colour: Colour, fps: u32) -> Self {
        let (display, view) = colour.default();
        let groups = reel.groups.len();
        let engine = Engine::start(vec![Content::none(); groups.max(1)], Timeline::new(clips(&reel, fps)), BUFFER_BYTES);
        let wake_slot: Arc<Mutex<Option<Sender<Message>>>> = Arc::new(Mutex::new(None));
        engine.set_notifier(waker(&wake_slot));
        let transport = Transport::new(reel.len(), fps, Instant::now());
        // Where the frames sit is read from the path alone; what is beside
        // them is listed once the window is up.
        let beside = Beside { place: preview_place::place(&source.path), ..Beside::default() };
        FramesView {
            home: source.clone(),
            source,
            beside,
            view: None,
            asked: 0,
            epoch: 0,
            reel,
            display: Display { colour, display, view, exposure: 0.0 },
            engine,
            opened: vec![Opened::default(); groups],
            transport,
            flipped: false,
            shown: None,
            frames_shown: 0,
            failed_at: HashMap::new(),
            scanning: false,
            status: None,
            wake_slot,
            started: false,
            quit: false,
        }
    }

    fn rate(&self) -> (i32, i32) {
        (self.transport.fps as i32, 1)
    }

    fn timeline(&self) -> Timeline {
        Timeline::new(clips(&self.reel, self.transport.fps))
    }

    /// Open group `g` again (its files changed, or it is new): it plays
    /// nothing until it is open.
    fn open(&mut self, g: usize) -> Command<Message> {
        if self.opened.len() <= g {
            self.opened.resize(g + 1, Opened::default());
        }
        let opened = &mut self.opened[g];
        opened.generation += 1;
        opened.media = None;
        opened.lut = None;
        opened.error = None;
        let generation = opened.generation;
        self.engine.replace_source(g, Content::none(), self.timeline());
        let group = self.reel.groups[g].clone();
        let rate = self.rate();
        Command::perform_blocking(move || open_group(&group, rate), move |r| Message::Opened(g, generation, r))
    }

    /// What `media` shows: the layer chosen when it has it, else its default.
    fn view_of(&self, media: &Media) -> ExrView {
        layer_view::kept_view(self.view.as_ref(), media.layers(), media.default_view())
    }

    fn content(&self, g: usize) -> Content {
        match &self.opened.get(g).and_then(|o| o.media.clone()) {
            Some(media) => Content { media: Some(Arc::clone(media)), view: self.view_of(media), hidden: 0, wants_proxy: false },
            None => Content::none(),
        }
    }

    /// The media at the playhead (the first group's in a gap between groups).
    fn media_here(&self) -> Option<&Arc<Media>> {
        let g = self.reel.slot_at(self.transport.position).map_or(0, |(g, _)| g);
        self.opened.get(g).and_then(|o| o.media.as_ref())
    }

    /// The AOV chooser's rows: the layers of the file at the playhead and
    /// its version's other AOV folders.
    fn aov_rows(&self) -> Vec<AovRow> {
        let layers = self.media_here().map_or(&[][..], |m| m.layers());
        let folders: Vec<String> = self.beside.aovs.iter().map(|(name, _)| name.clone()).collect();
        th_media::aov_rows(layers, self.beside.aov(), &folders)
    }

    /// The display transform `media` is shown through as the display and
    /// view have it; None for display-referred media, and for a layer of
    /// data, shown as written.
    fn lut_of(&mut self, media: &Media) -> Result<Option<Arc<Lut>>, String> {
        if !media.is_exr() {
            return Ok(None);
        }
        let view = self.view_of(media);
        let kind = media.layers().iter().find(|l| l.name == view.layer).map_or(LayerKind::Colour, |l| l.kind(self.beside.aov()));
        let display = &mut self.display;
        transform_of(kind, media.colour_space())?.map(|space| display.colour.lut(space, &display.display, &display.view)).transpose()
    }

    /// List what is beside the frames shown, off the UI thread.
    fn list_beside(&mut self) -> Command<Message> {
        self.asked += 1;
        let (asked, path) = (self.asked, self.source.path.clone());
        Command::perform_blocking(move || Beside::of(&path), move |beside| Message::Beside(asked, beside))
    }

    /// Read `source`'s frames to show them in place of these, off the UI
    /// thread; these stay until they are read.
    fn swap(&mut self, source: Source) -> Command<Message> {
        self.asked += 1;
        let asked = self.asked;
        Command::perform_blocking(
            move || {
                let (reel, beside) = (source.read(), Beside::of(&source.path));
                (source, reel, beside)
            },
            move |(source, reel, beside)| Message::Swapped(asked, source, reel, beside),
        )
    }

    /// Show `reel` (of `source`) in place of what is shown, at the frame
    /// number the playhead is on, playing on if it was.
    fn swapped(&mut self, source: Source, reel: Reel, beside: Beside, now: Instant) -> Command<Message> {
        let number = self.reel.number_at(self.transport.position);
        self.epoch += 1;
        self.scanning = false;
        self.failed_at.clear();
        self.status = None;
        self.source = source;
        self.beside = beside;
        self.reel = reel;
        self.transport.set_len(self.reel.len());
        self.transport.move_to(self.reel.position_of(number), now);
        // Sources past the new groups play nothing.
        for g in self.reel.groups.len()..self.opened.len() {
            let generation = self.opened[g].generation + 1;
            self.opened[g] = Opened { generation, ..Opened::default() };
            self.engine.replace_source(g, Content::none(), self.timeline());
        }
        let commands: Vec<Command<Message>> = (0..self.reel.groups.len()).map(|g| self.open(g)).collect();
        self.shown = None;
        self.refresh_shown();
        Command::batch(commands)
    }

    /// Show another layer of the files shown: every group plays its frames
    /// as the view chosen has them, from where the playhead is.
    fn show_view(&mut self) {
        for g in 0..self.opened.len() {
            if self.opened[g].media.is_some() {
                let content = self.content(g);
                self.engine.replace_source(g, content, self.timeline());
            }
        }
        self.set_display();
    }

    fn pick_aov(&mut self, row: usize) -> Command<Message> {
        match self.aov_rows().get(row).map(|r| r.target.clone()) {
            Some(AovTarget::Layer(layer)) => {
                self.view = Some(ExrView { layer, channel: None });
                self.show_view();
            }
            Some(AovTarget::Folder(folder)) => {
                if let Some((_, path)) = self.beside.aovs.get(folder) {
                    // The same version's frames: its frame list holds.
                    let source = Source { path: path.clone(), fill: self.source.fill.clone() };
                    self.view = None;
                    return self.swap(source);
                }
            }
            None => {}
        }
        Command::none()
    }

    fn pick_version(&mut self, row: usize) -> Command<Message> {
        let Some(row) = self.beside.versions.get(row) else { return Command::none() };
        if Some(row.version.as_str()) == self.beside.version() {
            return Command::none();
        }
        if row.state == VersionState::Missing {
            let what = self.beside.aov().map_or_else(String::new, |aov| format!("{aov} "));
            self.status = Some(format!("{} has no {what}frames to show", row.version));
            return Command::none();
        }
        let home = preview_place::place(&self.home.path);
        let fill = if home.as_ref().map(Place::version) == Some(row.version.as_str()) { self.home.fill.clone() } else { Fill::Span };
        let source = Source { path: row.path.clone(), fill };
        self.swap(source)
    }

    /// The frame at the playhead, when it has one to decode.
    fn key(&self) -> Option<FrameKey> {
        self.engine.layers(self.transport.position).last().map(|l| l.key)
    }

    /// Whether `position` can show: decoded, failed, or nothing to decode
    /// (a gap, a group not open).
    fn ready(&self, position: usize) -> bool {
        self.engine.layers(position).iter().all(|l| self.engine.is_buffered(l.key) || self.engine.failure(l.key).is_some())
    }

    /// Show the playhead's frame once it is decoded; until then the last
    /// picture stays (a gap shows black at once).
    fn refresh_shown(&mut self) {
        self.engine.set_playhead(self.transport.position);
        let Some(key) = self.key() else {
            self.shown = None;
            return;
        };
        if self.shown.as_ref().is_some_and(|(k, _)| *k == key) {
            return;
        }
        // A group that cannot show (no display transform for it) says why.
        if self.opened.get(key.source).is_some_and(|o| o.error.is_some()) {
            self.shown = None;
            return;
        }
        if let Some(frame) = self.engine.frame(key) {
            self.frames_shown += 1;
            let lut = self.opened.get(key.source).and_then(|o| o.lut.clone());
            self.shown = Some((key, Picture { id: self.frames_shown, frame, lut }));
        }
    }

    fn set_display(&mut self) {
        for g in 0..self.opened.len() {
            if let Some(media) = self.opened[g].media.clone() {
                let lut = self.lut_of(&media);
                let o = &mut self.opened[g];
                (o.lut, o.error) = match lut {
                    Ok(lut) => (lut, None),
                    Err(e) => (None, Some(e)),
                };
            }
        }
        self.shown = None;
        self.refresh_shown();
    }

    fn rescan(&mut self) -> Command<Message> {
        // A movie is written once: nothing lands in it.
        if self.scanning || self.reel.groups.iter().all(|g| g.movie().is_some()) {
            return Command::none();
        }
        self.scanning = true;
        let epoch = self.epoch;
        let source = self.source.clone();
        // Still waiting for the first frame: no frames is still waiting. A
        // folder that empties once frames were shown keeps them instead.
        let waiting = self.reel.is_waiting();
        let failed: Vec<(FrameKey, PathBuf)> = self
            .failed_at
            .keys()
            .filter_map(|k| self.reel.groups.get(k.source).and_then(|g| g.slots.get(k.index)).map(|s| (*k, s.path.clone())))
            .collect();
        Command::perform_blocking(
            move || {
                let times = failed.into_iter().map(|(k, p)| (k, std::fs::metadata(p).and_then(|m| m.modified()).ok())).collect();
                (if waiting { source.read_or_wait() } else { source.read() }, times)
            },
            move |(reel, times)| Message::Scanned(epoch, reel, times),
        )
    }

    /// The frames read again: what landed fills its gap, a group whose
    /// files changed opens again, a frame that failed and whose file has
    /// changed since is read again.
    fn scanned(&mut self, reel: Result<Reel, String>, times: Vec<(FrameKey, Option<SystemTime>)>) -> Command<Message> {
        self.scanning = false;
        let forget: Vec<FrameKey> =
            times.into_iter().filter(|(k, t)| self.failed_at.get(k).is_some_and(|was| was != t)).map(|(k, _)| k).collect();
        for key in &forget {
            self.failed_at.remove(key);
            self.engine.forget_frames(key.source, &[key.index]);
        }
        let reel = match reel {
            Ok(reel) => reel,
            Err(e) => {
                // A share that went away, a folder emptied: keep what is shown.
                self.status = Some(format!("Cannot read the frames again: {e}"));
                return Command::none();
            }
        };
        self.status = None;
        if reel == self.reel {
            return Command::none();
        }
        let changed: Vec<usize> = (0..reel.groups.len())
            .filter(|&g| {
                self.reel
                    .groups
                    .get(g)
                    .is_none_or(|old| old.paths() != reel.groups[g].paths() || self.opened.get(g).is_none_or(|o| o.media.is_none()))
            })
            .collect();
        // The first frames landed: what is beside them (the version's AOV
        // folders, made as the render writes them) is listed again.
        let first_frames = self.reel.is_waiting() && !reel.is_waiting();
        self.reel = reel;
        self.transport.set_len(self.reel.len());
        self.engine.set_timeline(self.timeline());
        let mut commands: Vec<Command<Message>> = changed.into_iter().map(|g| self.open(g)).collect();
        if first_frames {
            commands.push(self.list_beside());
        }
        self.refresh_shown();
        Command::batch(commands)
    }

    /// Note frames that failed to read, with their file's time then (asked
    /// on the next rescan), so a frame still being written is read again.
    fn note_failures(&mut self) {
        let Some(key) = self.key() else { return };
        if self.engine.failure(key).is_some() && !self.failed_at.contains_key(&key) {
            let path = self.reel.groups.get(key.source).and_then(|g| g.slots.get(key.index)).map(|s| s.path.clone());
            let time = path.and_then(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok());
            self.failed_at.insert(key, time);
        }
    }

    fn after_move(&mut self) {
        self.refresh_shown();
    }

    /// The frame number at the playhead.
    fn frame_text(&self) -> String {
        match self.reel.slot(self.transport.position) {
            Some(slot) => {
                slot.number.map_or_else(|| slot.path.file_name().unwrap_or_default().to_string_lossy().into_owned(), |n| n.to_string())
            }
            None => "\u{2014}".into(),
        }
    }

    /// What the picture says when it has nothing to show: why.
    fn card(&self) -> Option<(String, Color)> {
        let position = self.transport.position;
        let (g, i) = self.reel.slot_at(position)?;
        let slot = &self.reel.groups[g].slots[i];
        let name = self.frame_text();
        if self.reel.is_waiting() {
            let of = self.reel.len();
            return Some((format!("Waiting for the first frame: none of the {of} frames is rendered yet"), look::quiet()));
        }
        if !slot.present {
            return Some((format!("Frame {name} is not rendered yet"), look::quiet()));
        }
        if let Some(e) = self.opened.get(g).and_then(|o| o.error.as_ref()) {
            return Some((format!("Cannot show {}: {e}", self.reel.groups[g].name), look::error()));
        }
        if let Some(key) = self.key()
            && let Some(e) = self.engine.failure(key)
        {
            return Some((format!("Cannot read frame {name}: {e}"), look::error()));
        }
        self.shown.is_none().then(|| (format!("Reading frame {name}\u{2026}"), look::quiet()))
    }

    /// The playback state, honestly: waiting for frames, the rate it plays
    /// at when slower than asked, else the rate asked.
    fn rate_text(&self) -> (String, Color) {
        let fps = self.transport.fps;
        if self.transport.playing {
            if self.transport.stalled {
                return (format!("{fps} fps \u{00b7} waiting for frames"), look::caution());
            }
            if let Some(rate) = self.transport.achieved_fps()
                && rate < f64::from(fps) * 0.9
            {
                return (format!("playing {rate:.1} of {fps} fps"), look::caution());
            }
        } else if let Some(key) = self.key()
            && !self.engine.is_buffered(key)
            && self.engine.failure(key).is_none()
        {
            return (format!("{fps} fps \u{00b7} reading\u{2026}"), look::quiet());
        }
        (format!("{fps} fps"), look::quiet())
    }

    fn rows(&self) -> Vec<RowView> {
        let many = self.reel.groups.len() > 1;
        let clips = self
            .reel
            .runs()
            .iter()
            .map(|run| {
                let span = span_label(&self.reel, run);
                let group = &self.reel.groups[run.group];
                let error = self.opened.get(run.group).and_then(|o| o.error.clone());
                let (name, detail) = match (run.present, error) {
                    (true, Some(e)) => (if many { group.name.clone() } else { span }, e),
                    (true, None) if many => (group.name.clone(), span),
                    (true, None) => (span, format!("{} frame{}", run.len, if run.len == 1 { "" } else { "s" })),
                    (false, _) => (span, "not rendered yet".to_owned()),
                };
                ClipView {
                    at: run.at as i64,
                    duration: run.len as i64,
                    fade_in: 0,
                    fade_out: 0,
                    name,
                    detail,
                    thumbnail: None,
                    selected: false,
                    missing: !run.present,
                    department: None,
                    preview: None,
                    farm: None,
                    room: None,
                    wave: None,
                }
            })
            .collect();
        vec![RowView {
            track: TrackRef::video(0),
            name: "Frames".into(),
            clips,
            height: tracks::ROW,
            solo: false,
            spare: false,
            color: None,
        }]
    }

    fn toolbar(&self) -> Element<Message> {
        let playing = self.transport.playing;
        let (rendered, slots) = self.reel.rendered();
        let mut title = format!("Preview \u{00b7} {}", self.reel.title);
        if let Some(version) = self.beside.version() {
            title.push_str(&format!(" \u{00b7} {version}"));
        }
        if rendered < slots {
            title.push_str(&format!("   {rendered} of {slots} frames rendered"));
        }
        let (rate, rate_colour) = self.rate_text();
        let transport = row()
            .spacing(4.0)
            .align_y(Vertical::Center)
            .push(glyph::button("start", Glyph::ToStart, "Go to start (Home)", Message::Seek(0)))
            .push(glyph::button("back", Glyph::StepBack, "Previous frame (Left)", Message::Step(-1)))
            .push(glyph::button("play", if playing { Glyph::Pause } else { Glyph::Play }, play_tip(playing), Message::TogglePlay))
            .push(glyph::button("forward", Glyph::StepForward, "Next frame (Right)", Message::Step(1)))
            .push(glyph::button("end", Glyph::ToEnd, "Go to end (End)", Message::Last));
        let looping = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(look::tick_box(Id::new("loop"), self.transport.looping, |_| Message::Loop).focusable(false))
            .push(text("Loop").font_size(13.0).color(look::quiet()));
        let mut tools = row()
            .spacing(24.0)
            .align_y(Vertical::Center)
            .push(text(title).font_size(15.0).color(look::text()))
            .push(space(Length::Fill, Length::Shrink));
        if self.flipped {
            tools = tools.push(crate::flip::chip(Message::Flip));
        }
        let tools = tools
            .push(transport)
            .push(tooltip(Id::new("loop tip"), looping, "Play round from the end to the start (L)"))
            .push(text(rate).font_size(13.0).color(rate_colour))
            .push(text(self.frame_text()).font_size(20.0).color(look::now()));
        container(tools).padding(Padding::from([8.0, 14.0])).width(Length::Fill).background(look::panel()).into()
    }

    /// The AOV and Version choosers, each only when it has a choice to
    /// make. Out of keyboard focus, as the display's are.
    fn choosers(&self) -> Row<Message> {
        let label = |words: &str| text(words.to_owned()).font_size(13.0).color(look::quiet());
        let mut choosers = row();
        let rows = self.aov_rows();
        if rows.len() > 1 {
            let view = self.media_here().map(|m| self.view_of(m)).or_else(|| self.view.clone()).unwrap_or_default();
            let mut combo = combo_box(Id::new("preview aov"), rows.iter().map(|r| r.label.clone()).collect())
                .on_select(Message::Aov)
                .width(Length::fixed(230.0))
                .focusable(false);
            if let Some(selected) = th_media::aov_selected(&rows, &view) {
                combo = combo.selected(selected);
            }
            choosers = choosers.push(label("AOV")).push(combo).push(space(Length::fixed(16.0), Length::Shrink));
        }
        let versions = &self.beside.versions;
        if versions.len() > 1 {
            let mut combo = combo_box(Id::new("preview version"), versions.iter().map(VersionRow::label).collect())
                .on_select(Message::Version)
                .width(Length::fixed(230.0))
                .focusable(false);
            if let Some(selected) = versions.iter().position(|r| Some(r.version.as_str()) == self.beside.version()) {
                combo = combo.selected(selected);
            }
            choosers = choosers.push(label("Version")).push(combo).push(space(Length::fixed(16.0), Length::Shrink));
        }
        choosers
    }

    fn timeline_view(&self) -> Element<Message> {
        let program = Tracks {
            rows: self.rows(),
            markers: Vec::new(),
            total: self.reel.len(),
            playhead: self.transport.position,
            buffered: tracks::ranges(&self.engine.buffered_positions()),
            rendering: Vec::new(),
            on_seek: Message::Seek,
            // No selection here: a click on a clip seeks, as one on a gap.
            on_select: |_, _, position| Message::Seek(position),
            on_drag: || Message::Ignore,
            on_op: |_| Message::Ignore,
            on_ops: |_| Message::Ignore,
            on_menu: |_, _| Message::Ignore,
            on_row_height: |_, _| Message::Ignore,
            on_solo: |_| Message::Ignore,
            on_context: |_, _| Message::Ignore,
            on_gesture: |_| Message::Ignore,
            first: 0.0,
            on_view: |_| Message::Ignore,
            cut: 0,
            gap: None,
            note: None,
            editable: false,
        };
        let height = program.height();
        tracks::track_view(Id::new("frames timeline"), program, height).into()
    }

    /// The window's menus ([`menus_of`]).
    fn menus(&self) -> Vec<(&'static str, Vec<Option<Line<Message>>>)> {
        menus_of(self.transport.playing, self.transport.looping, self.flipped, &self.beside.versions, self.beside.version())
    }

    /// The picture as the video view draws it: the frame shown, at the
    /// exposure, mirrored while flipped (`flip`).
    fn video(&self) -> Video {
        Video { picture: self.shown.as_ref().map(|(_, p)| p.clone()), exposure: self.display.exposure, flipped: self.flipped }
    }

    /// The menus as the platform's menu bar (macOS's).
    fn menu_bar(&self) -> Menu<Message> {
        let mut bar = Menu::new();
        if cfg!(target_os = "macos") {
            let quit =
                MenuItem::button("Quit Cinedex", Message::Quit).accelerator(Accelerator::new(Key::Character('q'), Modifiers::COMMAND));
            bar = bar.app_submenu("Cinedex", Menu::new().push(quit));
        }
        for (title, lines) in self.menus() {
            let menu = lines.into_iter().fold(Menu::new(), |menu, line| {
                menu.push(match line {
                    None => MenuItem::separator(),
                    Some(Line { label, message, enabled, keys }) => {
                        let item = MenuItem::button(label, message).enabled(enabled);
                        // On macOS only Command chords go to the menu: a bare
                        // key stays the window's (as the editor's menu has it).
                        match keys.filter(|(_, m)| !cfg!(target_os = "macos") || m.command()) {
                            Some((key, modifiers)) => item.accelerator(Accelerator::new(key, modifiers)),
                            None => item,
                        }
                    }
                })
            });
            bar = bar.submenu(title, menu);
        }
        bar
    }

    /// The menus as a bar across the top of the window, on Windows and
    /// Linux: where Primer would put its own (`menu_words`), drawn so no
    /// title loses its last letter.
    fn menu_row(&self) -> Element<Message> {
        menu_row_of(self.menus())
    }
}

/// The menus, each its title and rows (`None` a separator): the
/// [`headings`], the Version menu's steps greyed with one version or none,
/// and `versions` listed under them, the one `shown` ticked.
#[allow(clippy::type_complexity)]
fn menus_of(
    playing: bool,
    looping: bool,
    flipped: bool,
    versions: &[VersionRow],
    shown: Option<&str>,
) -> Vec<(&'static str, Vec<Option<Line<Message>>>)> {
    headings(playing, looping, flipped)
        .into_iter()
        .map(|(title, rows)| {
            let enabled = title != VERSION_MENU || versions.len() > 1;
            let mut lines: Vec<Option<Line<Message>>> = rows
                .into_iter()
                .map(|row| row.map(|(label, message, keys)| Line { label: label.to_owned(), message, enabled, keys }))
                .collect();
            if title == VERSION_MENU && !versions.is_empty() {
                lines.push(None);
                for (i, label) in version_menu_rows(versions, shown).into_iter().enumerate() {
                    lines.push(Some(Line { label, message: Message::Version(i), enabled: true, keys: None }));
                }
            }
            (title, lines)
        })
        .collect()
}

/// `menus` as a bar drawn in the window, its words given room.
fn menu_row_of(menus: Vec<(&'static str, Vec<Option<Line<Message>>>)>) -> Element<Message> {
    let menus = menus.into_iter().map(|(title, lines)| (title.to_owned(), menu_words::rows(lines))).collect();
    Roomy::words(menu_words::bar(Id::new("frames menus"), menus).0).into()
}

impl Application for FramesView {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        if self.quit {
            return Vec::new();
        }
        vec![
            window::Window::new(window::Id::MAIN)
                .title(format!("Preview frames \u{00b7} {} - Cinedex", self.reel.title))
                .app_id(crate::APP_ID)
                .size(Size::new(1280.0, 860.0))
                .theme(theme()),
        ]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        let now = Instant::now();
        match message {
            Message::Start if !self.started => {
                self.started = true;
                crate::app_icon::apply();
                let mut commands: Vec<Command<Message>> = (0..self.reel.groups.len()).map(|g| self.open(g)).collect();
                commands.push(self.list_beside());
                return Command::batch(commands);
            }
            Message::Start => {}
            Message::Wake => {
                self.refresh_shown();
                self.note_failures();
            }
            Message::Tick => {
                // A tick before the first frame shows also gives the window
                // its icon, should it have opened after Start.
                if self.frames_shown == 0 {
                    crate::app_icon::apply();
                }
                let tick = {
                    let ready = |p: usize| self.ready(p);
                    let mut transport = self.transport.clone();
                    let tick = transport.tick(now, ready);
                    self.transport = transport;
                    tick
                };
                if let Tick::Moved(_) | Tick::Ended = tick {
                    self.after_move();
                }
            }
            Message::TogglePlay => self.transport.toggle(now),
            Message::Step(by) => {
                self.transport.step(by, now);
                self.after_move();
            }
            Message::Seconds(by) => {
                self.transport.step(by * i64::from(self.transport.fps), now);
                self.after_move();
            }
            Message::Edge(direction) => {
                let at = self.transport.position;
                let points = self.reel.edit_points();
                let to = if direction < 0 { points.iter().rev().find(|&&p| p < at) } else { points.iter().find(|&&p| p > at) };
                if let Some(&to) = to {
                    self.transport.seek(to, now);
                    self.after_move();
                }
            }
            Message::Seek(position) => {
                self.transport.seek(position, now);
                self.after_move();
            }
            Message::Last => {
                self.transport.seek(usize::MAX, now);
                self.after_move();
            }
            Message::Loop => self.transport.looping = !self.transport.looping,
            Message::Flip => self.flipped = !self.flipped,
            Message::Display(index) => {
                if self.display.pick_display(index) {
                    self.set_display();
                }
            }
            Message::View(index) => {
                if self.display.pick_view(index) {
                    self.set_display();
                }
            }
            Message::Exposure(stops) => self.display.exposure = stops,
            Message::Rescan => return self.rescan(),
            Message::Refresh => {
                let commands = vec![self.rescan(), self.list_beside()];
                return Command::batch(commands);
            }
            Message::Aov(row) => return self.pick_aov(row),
            Message::Version(row) => return self.pick_version(row),
            Message::VersionStep(delta) => {
                let to = self.beside.version().and_then(|current| preview_place::step_version(&self.beside.versions, current, delta));
                if let Some(row) = to {
                    return self.pick_version(row);
                }
            }
            Message::Beside(asked, beside) => {
                if asked == self.asked {
                    self.beside = beside;
                    // Which AOV the frames are of says how they are shown.
                    self.set_display();
                }
            }
            Message::Swapped(asked, source, reel, beside) => {
                if asked != self.asked {
                    return Command::none();
                }
                match reel {
                    Ok(reel) => return self.swapped(source, reel, beside, now),
                    Err(e) => self.status = Some(format!("Cannot show {}: {e}", source.path.display())),
                }
            }
            Message::Scanned(epoch, reel, times) => {
                if epoch == self.epoch {
                    return self.scanned(reel, times);
                }
            }
            Message::Opened(g, generation, result) => {
                if self.opened.get(g).is_none_or(|o| o.generation != generation) {
                    return Command::none();
                }
                match result {
                    Ok(media) => {
                        let lut = self.lut_of(&media);
                        let o = &mut self.opened[g];
                        match lut {
                            Ok(lut) => o.lut = lut,
                            Err(e) => o.error = Some(e),
                        }
                        o.media = Some(media);
                        let content = self.content(g);
                        self.engine.replace_source(g, content, self.timeline());
                    }
                    Err(e) => {
                        eprintln!("cinedex: {}: {e}", self.reel.groups.get(g).map_or("", |g| g.name.as_str()));
                        self.opened[g].error = Some(e);
                    }
                }
                self.shown = None;
                self.refresh_shown();
            }
            Message::Ignore => {}
            // Primer ends a run only on Command::quit (since 0.16): emptying
            // windows() closed the window and left the process running.
            Message::Quit => {
                self.quit = true;
                return Command::quit();
            }
        }
        Command::none()
    }

    fn on_key(&self, _window: window::Id, key: Key, modifiers: Modifiers) -> Option<Message> {
        key_message(&key, modifiers)
    }

    /// The platform's menu bar (macOS's), or none where the window draws
    /// the menus itself (`menu_row`).
    fn menu(&self) -> Option<Menu<Message>> {
        (!menu_words::in_window()).then(|| self.menu_bar())
    }

    fn subscription(&self) -> Subscription<Message> {
        let slot = Arc::clone(&self.wake_slot);
        let engine_events = subscription::channel(subscription::hash_id(&"cinedex frames engine"), 4, move |sender| async move {
            let mut sender = sender;
            let _ = sender.try_send(Message::Start);
            *slot.lock().unwrap() = Some(sender);
            futures::future::pending::<()>().await;
        });
        let rescan = time::every(RESCAN_EVERY, || Message::Rescan);
        if !self.transport.playing {
            return Subscription::batch([engine_events, rescan]);
        }
        Subscription::batch([engine_events, rescan, time::every(self.transport.tick_every(), || Message::Tick)])
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let video = shader(self.video()).width(Length::Fill).height(Length::Fill).redraw(false);
        let mut layers = stack().push(video).width(Length::Fill).height(Length::Fill);
        if let Some((words, colour)) = self.card() {
            layers = layers.push(
                container(text(words).font_size(15.0).color(colour))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .align_x(Horizontal::Center)
                    .align_y(Vertical::Center),
            );
        }
        // A right-click on the picture: what the view of it can do.
        let layers =
            Roomy::menus(context_menu(Id::new("viewer menu"), layers).item(crate::flip::context_label(self.flipped), || Message::Flip));
        let bar = display_bar(&self.display, self.choosers(), Message::Display, Message::View, Message::Exposure);
        let mut page = column();
        if menu_words::in_window() {
            page = page.push(self.menu_row());
        }
        let mut page = page.push(self.toolbar()).push(layers).push(bar).push(self.timeline_view()).width(Length::Fill).height(Length::Fill);
        if let Some(words) = &self.status {
            page = page.push(
                container(text(words.clone()).font_size(13.0).color(look::error()))
                    .padding(Padding::from([6.0, 14.0]))
                    .width(Length::Fill)
                    .background(look::panel()),
            );
        }
        page.into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::previews::{Still, Stills};
    use proptest::prelude::*;

    fn reel(numbers: &[i64], fill: &Fill) -> Reel {
        let frames = numbers
            .iter()
            .map(|n| Still { group: "beauty".into(), number: Some(*n), path: PathBuf::from(format!("R:/b/beauty.{n}.exr")) })
            .collect();
        Reel::of(&Stills { title: "beauty".into(), frames, exact: false }, fill)
    }

    #[test]
    fn clips_play_the_frames_on_disk_and_leave_the_gaps() {
        let r = reel(&[1001, 1002, 1005, 1006, 1007], &Fill::Span);
        let c = clips(&r, 25);
        assert_eq!(c.len(), 2);
        assert_eq!((c[0].at, c[0].frames, c[0].first), (0, 2, 0));
        assert_eq!((c[1].at, c[1].frames, c[1].first), (4, 3, 4));
        assert!(c.iter().all(|c| c.rate == (25, 1) && c.source == 0));
        let t = Timeline::new(c);
        assert!(t.layers(2, |_| true).is_empty(), "a gap plays nothing");
        assert_eq!(t.layers(5, |_| true)[0].key, FrameKey { source: 0, index: 5 });
    }

    #[test]
    fn a_folder_with_no_frames_yet_waits_for_its_frame_list() {
        let dir = tempfile::tempdir().unwrap();
        let beauty = dir.path().join("v0003").join("beauty");
        let list = Fill::List((1001..=1010).collect());
        let source = Source { path: beauty.clone(), fill: list.clone() };
        // Not there yet, then there and empty: the frame list as gaps.
        let waiting = source.read_or_wait().unwrap();
        assert!(waiting.is_waiting());
        assert_eq!(waiting.rendered(), (0, 10));
        assert!(clips(&waiting, 24).is_empty(), "nothing to play yet");
        assert!(source.read().is_err(), "a plain read still says there are no frames");
        std::fs::create_dir_all(&beauty).unwrap();
        assert_eq!(source.read_or_wait().unwrap(), waiting);
        // The first frame lands: the folder's frames, the rest still gaps.
        std::fs::write(beauty.join("shot_beauty.1004.exr"), b"").unwrap();
        let landed = source.read_or_wait().unwrap();
        assert!(!landed.is_waiting());
        assert_eq!(landed.rendered(), (1, 10));
        assert_eq!(landed.groups[0].name, waiting.groups[0].name);
        // Without a frame list, or for a frame or a movie, nothing to wait for.
        let empty = dir.path().join("empty");
        std::fs::create_dir_all(&empty).unwrap();
        assert!(Source { path: empty.clone(), fill: Fill::Span }.read_or_wait().is_err());
        assert!(Source { path: empty, fill: Fill::List(Vec::new()) }.read_or_wait().is_err());
        assert!(Source { path: dir.path().join("gone.1001.exr"), fill: list.clone() }.read_or_wait().is_err());
        assert!(Source { path: dir.path().join("gone_v0001.mp4"), fill: list }.read_or_wait().is_err());
    }

    proptest! {
        /// A clip's frame at every position is the slot there, on disk.
        #[test]
        fn every_clip_frame_is_its_slot(numbers in prop::collection::btree_set(1i64..60, 1..20)) {
            let numbers: Vec<i64> = numbers.into_iter().collect();
            let r = reel(&numbers, &Fill::Span);
            let t = Timeline::new(clips(&r, 24));
            for p in 0..r.len() {
                let layers = t.layers(p, |_| true);
                let (g, i) = r.slot_at(p).unwrap();
                prop_assert_eq!(layers.len(), usize::from(r.groups[g].slots[i].present));
                if let Some(l) = layers.first() {
                    prop_assert_eq!(l.key, FrameKey { source: g, index: i });
                }
            }
        }
    }

    /// Space and Up, alone, play and pause, and no other key does: Up with
    /// a modifier and Down do nothing. The play button's tooltip names what
    /// it does now and both keys.
    #[test]
    fn space_and_up_play_and_pause_and_the_tooltip_names_both() {
        let named = [
            (Named::Space, "Space"),
            (Named::ArrowUp, "Up"),
            (Named::ArrowDown, "Down"),
            (Named::ArrowLeft, "Left"),
            (Named::ArrowRight, "Right"),
            (Named::Home, "Home"),
            (Named::End, "End"),
        ];
        let toggles: Vec<&str> = named
            .iter()
            .filter(|(key, _)| matches!(key_message(&Key::Named(*key), NONE), Some(Message::TogglePlay)))
            .map(|(_, name)| *name)
            .collect();
        assert_eq!(toggles, ["Space", "Up"]);
        for (playing, does, other) in [(false, "Play", "Pause"), (true, "Pause", "Play")] {
            let tip = play_tip(playing);
            assert!(tip.starts_with(does) && !tip.contains(other), "{tip}");
            assert_eq!(tip, format!("{does} ({})", toggles.join(" or ")));
        }
        let shift = Modifiers { shift: true, ..NONE };
        let control = Modifiers { control: true, ..NONE };
        let logo = Modifiers { logo: true, ..NONE };
        for held in [shift, ALT, control, logo, Modifiers::COMMAND] {
            assert!(key_message(&Key::Named(Named::ArrowUp), held).is_none(), "Up with {held:?}");
            assert!(key_message(&Key::Named(Named::ArrowDown), held).is_none(), "Down with {held:?}");
        }
        for c in ('a'..='z').chain('0'..='9') {
            assert!(!matches!(key_message(&Key::Character(c), NONE), Some(Message::TogglePlay)), "{c} plays");
        }
    }

    /// Page Up and Page Down, alone, step versions as in the editor (up is
    /// newer); with the editor's department and source modifiers they do
    /// nothing here.
    #[test]
    fn page_keys_step_versions_as_the_editor_s() {
        assert!(matches!(key_message(&Key::Named(Named::PageUp), NONE), Some(Message::VersionStep(1))));
        assert!(matches!(key_message(&Key::Named(Named::PageDown), NONE), Some(Message::VersionStep(-1))));
        let shift = Modifiers { shift: true, ..NONE };
        for held in [shift, ALT, Modifiers::COMMAND] {
            assert!(key_message(&Key::Named(Named::PageUp), held).is_none(), "{held:?}");
            assert!(key_message(&Key::Named(Named::PageDown), held).is_none(), "{held:?}");
        }
    }

    /// Colour goes through the display transform of its colour space and
    /// is refused without one; data never needs one.
    #[test]
    fn data_is_shown_as_written_and_colour_needs_its_space() {
        assert_eq!(transform_of(LayerKind::Colour, Some("ACEScg")), Ok(Some("ACEScg")));
        assert!(transform_of(LayerKind::Colour, None).unwrap_err().contains("colour space"));
        for kind in [LayerKind::Data, LayerKind::Cryptomatte] {
            assert_eq!(transform_of(kind, Some("ACEScg")), Ok(None));
            assert_eq!(transform_of(kind, None), Ok(None));
        }
        assert_eq!((crate::video::float_mode(true), crate::video::float_mode(false)), (1.0, 3.0));
    }

    proptest! {
        /// The Version menu lists every version, newest first as given,
        /// and ticks exactly the one shown.
        #[test]
        fn the_version_menu_ticks_the_one_shown(count in 0usize..8, shown in prop::option::of(0usize..10)) {
            let versions: Vec<VersionRow> = (0..count)
                .map(|i| VersionRow { version: format!("v{:04}", count - i), path: PathBuf::new(), state: VersionState::Complete, check: false })
                .collect();
            let shown = shown.map(|n| format!("v{n:04}"));
            let rows = version_menu_rows(&versions, shown.as_deref());
            prop_assert_eq!(rows.len(), count);
            for (row, version) in rows.iter().zip(&versions) {
                let (ticked, plain) = crate::take_menu::unmarked(row);
                prop_assert_eq!(plain, version.label());
                prop_assert_eq!(ticked, Some(&version.version) == shown.as_ref());
            }
        }
    }

    /// A place is read from the window's path at once; a folder that sits
    /// nowhere has nothing beside it.
    #[test]
    fn what_is_beside_the_frames_comes_from_their_folders() {
        let dir = tempfile::tempdir().unwrap();
        let stream = dir.path().join("default");
        for (version, aov) in [("v0001", "beauty"), ("v0002", "beauty"), ("v0002", "depth")] {
            let folder = stream.join(version).join(aov);
            std::fs::create_dir_all(&folder).unwrap();
            std::fs::write(folder.join(format!("s_{aov}_{version}.1001.exr")), b"x").unwrap();
        }
        let beside = Beside::of(&stream.join("v0002").join("beauty"));
        assert_eq!((beside.version(), beside.aov()), (Some("v0002"), Some("beauty")));
        assert_eq!(beside.aovs.iter().map(|a| a.0.as_str()).collect::<Vec<_>>(), ["beauty", "depth"]);
        assert_eq!(beside.versions.iter().map(|v| v.version.as_str()).collect::<Vec<_>>(), ["v0002", "v0001"]);
        let nowhere = Beside::of(dir.path());
        assert!(nowhere.place.is_none() && nowhere.aovs.is_empty() && nowhere.versions.is_empty());
    }

    #[test]
    fn keys_are_the_editor_s_playback_keys() {
        let arrow = |n| Key::Named(n);
        assert!(matches!(key_message(&arrow(Named::Space), NONE), Some(Message::TogglePlay)));
        assert!(matches!(key_message(&arrow(Named::ArrowLeft), NONE), Some(Message::Step(-1))));
        assert!(matches!(key_message(&arrow(Named::ArrowRight), ALT), Some(Message::Seconds(1))));
        assert!(matches!(key_message(&arrow(Named::ArrowRight), Modifiers::COMMAND), Some(Message::Edge(1))));
        assert!(matches!(key_message(&arrow(Named::Home), NONE), Some(Message::Seek(0))));
        assert!(matches!(key_message(&arrow(Named::End), NONE), Some(Message::Last)));
        assert!(matches!(key_message(&Key::Character('l'), NONE), Some(Message::Loop)));
        assert!(key_message(&Key::Character('l'), Modifiers::COMMAND).is_none());
        assert!(key_message(&Key::Character('x'), NONE).is_none());
    }

    /// View > Flip Horizontally is there in the preview window too, ticked
    /// exactly while flipped, and Shift+H (not H) sends it.
    #[test]
    fn the_view_menu_flips_the_picture() {
        for flipped in [false, true] {
            let menus = headings(false, true, flipped);
            let (_, view) = menus.iter().find(|(title, _)| *title == "&View").expect("a View menu");
            let flips: Vec<&Row3> = view.iter().flatten().filter(|row| matches!(row.1, Message::Flip)).collect();
            assert_eq!(flips.len(), 1);
            assert_eq!(flips[0].0, crate::flip::menu_label(flipped));
        }
        let shift = Modifiers { shift: true, ..NONE };
        assert!(matches!(key_message(&Key::Character('H'), shift), Some(Message::Flip)));
        assert!(key_message(&Key::Character('h'), NONE).is_none());
    }

    /// A preview window on two frames that sit nowhere in the pipeline,
    /// shown through the project template's colour.
    fn window() -> FramesView {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
        let colour = Colour::open(&ocio).expect("the template's OCIO config");
        let source = Source { path: PathBuf::from("R:/b/beauty.1001.exr"), fill: Fill::Span };
        FramesView::new(source, reel(&[1001, 1002], &Fill::Span), colour, 24)
    }

    /// The words the window's toolbar draws.
    fn toolbar_words(view: &FramesView) -> Vec<String> {
        use primer::widget::custom::{Ctx, Cursor, Layout, LayoutCtx, Limits, StateStore, Widget};
        let toolbar = view.toolbar();
        let (store, theme) = (StateStore::new(), Theme::dark());
        let measurer = crate::menu_words::tests::OddText;
        let node = toolbar
            .layout(&Limits::new(Size::ZERO, Size::new(2400.0, 80.0)), LayoutCtx { measurer: &measurer, store: &store, theme: &theme });
        let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer: &measurer, visible: node.bounds() };
        let mut drawn = Vec::new();
        crate::menu_words::tests::texts(&toolbar.draw(Layout::new(&node), ctx), &mut drawn);
        drawn.into_iter().map(|text| text.content).collect()
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(8))]

        /// Quit (File > Exit, Quit Cinedex) ends the run, after any flips
        /// before it: Primer ends one only on `Command::quit` (since 0.16),
        /// so emptying `windows()` alone closed the window and left the
        /// process running (GUI check of 1.121.0, 2026-11-22).
        #[test]
        fn only_quit_ends_the_run(flips in 0usize..4) {
            let mut view = window();
            for _ in 0..flips {
                prop_assert!(!view.update(Message::Flip).is_quit());
            }
            let command = view.update(Message::Quit);
            prop_assert!(command.is_quit() && view.windows().is_empty());
        }
    }

    /// Flip Horizontally flips the picture and back: the video view is
    /// told, the View menu ticks it, and the toolbar's chip shows exactly
    /// while it is flipped. The window's menus are its own bar off macOS,
    /// Primer given none to stack above it.
    #[test]
    fn flip_mirrors_the_picture_and_says_so() {
        let mut view = window();
        assert_eq!(Application::menu(&view).is_none(), menu_words::in_window());
        for flipped in [true, false, true] {
            drop(view.update(Message::Flip));
            assert_eq!((view.flipped, view.video().flipped), (flipped, flipped));
            let menus = view.menus();
            let (_, rows) = menus.iter().find(|(title, _)| *title == "&View").expect("a View menu");
            assert!(rows.iter().flatten().any(|line| line.label == crate::flip::menu_label(flipped)));
            assert_eq!(toolbar_words(&view).iter().any(|words| words == crate::flip::CHIP), flipped);
        }
    }

    /// Versions as the Version menu lists them, of every state.
    fn versions(count: usize) -> Vec<VersionRow> {
        let states = [
            VersionState::Complete,
            VersionState::Partial { frames: 37, of: Some(240) },
            VersionState::Partial { frames: 1, of: None },
            VersionState::Missing,
        ];
        (0..count)
            .map(|i| VersionRow {
                version: format!("v{:04}", count - i),
                path: PathBuf::new(),
                state: states[i % states.len()],
                check: i % 3 == 1,
            })
            .collect()
    }

    proptest! {
        /// The Version menu's steps can be chosen only with two versions
        /// or more; the versions are listed under them past a line, each
        /// choosable, the one shown ticked; no other menu is greyed.
        #[test]
        fn the_version_menu_lists_the_versions_past_its_steps(playing: bool, looping: bool, flipped: bool, count in 0usize..6, shown in prop::option::of(0usize..6)) {
            let versions = versions(count);
            let shown = shown.map(|n| format!("v{n:04}"));
            for (title, lines) in menus_of(playing, looping, flipped, &versions, shown.as_deref()) {
                let steps = headings(playing, looping, flipped).into_iter().find(|h| h.0 == title).expect("the same menus").1.len();
                prop_assert!(lines[..steps].iter().flatten().all(|line| line.enabled == (title != VERSION_MENU || count > 1)), "{}", title);
                if title == VERSION_MENU && count > 0 {
                    prop_assert!(lines[steps].is_none(), "a line before the versions");
                    let listed: Vec<String> = lines[steps + 1..].iter().flatten().map(|line| line.label.clone()).collect();
                    prop_assert_eq!(listed, version_menu_rows(&versions, shown.as_deref()));
                    prop_assert!(lines[steps + 1..].iter().all(|line| line.as_ref().is_some_and(|line| line.enabled)));
                } else {
                    prop_assert_eq!(lines.len(), steps);
                }
            }
        }

        /// With the fonts the player draws with, at the common window
        /// scales and any between, each title of a preview window's menus
        /// and each row of each menu it opens (its words and its shortcut,
        /// the versions listed included) draws whole on its one line,
        /// inside its title or menu, a row's words ending before its
        /// shortcut.
        #[test]
        fn the_preview_menus_draw_whole_at_any_scale(
            playing: bool, looping: bool, flipped: bool, count in 0usize..5, scale in crate::menu_words::tests::scale(),
        ) {
            use crate::menu_words::tests::{drawn_whole, open, still, with_real};
            let versions = versions(count);
            let shown = versions.first().map(|v| v.version.clone());
            with_real(|real| {
                let theme = still(crate::display::theme());
                let menus = menus_of(playing, looping, flipped, &versions, shown.as_deref());
                let opens: Vec<bool> = menus.iter().map(|(_, lines)| !lines.is_empty()).collect();
                let mut bar = menu_row_of(menus);
                for (index, _) in opens.iter().enumerate().filter(|(_, opens)| **opens) {
                    let opened = open(&mut bar, index, real, &theme);
                    prop_assert!(!opened.words.is_empty());
                    drawn_whole(real, &opened, scale)?;
                }
                Ok(())
            })?;
        }
    }

    /// Every menu row's key sends the row's message.
    #[test]
    fn the_menu_shows_the_keys_that_work() {
        for playing in [false, true] {
            for (_, rows) in headings(playing, playing, playing) {
                for (label, message, keys) in rows.into_iter().flatten() {
                    let Some((key, modifiers)) = keys else { continue };
                    let sent = key_message(&key, modifiers);
                    assert_eq!(format!("{sent:?}"), format!("{:?}", Some(message)), "{label}");
                }
            }
        }
    }
}
