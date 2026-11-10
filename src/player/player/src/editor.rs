//! The editor window (`designs/edit-entity.md` §5): an edit's tracks under
//! the video, bins of the project's shots and media library beside it, and
//! its actions (save, publish, the cut's edits) in the menu bar over it
//! (`editor/menu_bar.rs`).
//!
//! The document is `editing::Document`: every change is a `th_edit` op,
//! undone and redone through its history. Each distinct source the edit
//! names (a shot with its take rule, a media item, a slug) is an *entry*:
//! resolved off the UI thread (the take its rule picks today, opened, with
//! its sound) and played by the engine as one source; the engine's clips are
//! the edit's (`editing::engine_clips`), rebuilt on every change. Engine
//! source 0 plays nothing: it keeps the engine whole while the edit is
//! empty.
//!
//! Open… lists the project's edits; the one chosen opens in a window of its
//! own (`open_arguments`), and an empty untitled window it was chosen from
//! closes.
//!
//! The master edit (`th_pipe::master`): its Publish lists the shot changes
//! its cut makes (planned shots made, ranges set, shots it dropped retired),
//! each ticked or not, flagged where the take it shows no longer covers the
//! new range; they are written after the publish. "New shot…" in the Shots
//! bin puts a planned shot in the cut.
//!
//! Sound: a shot clip's playblast sound plays under it on a video track;
//! audio clips play their source's sound at their level. While there is any,
//! playback keeps to the speaker's clock.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures::channel::mpsc::Sender;
use primer::ImageHandle;
use primer::keyboard::{Key, Modifiers};
use primer::prelude::*;
use primer::subscription::{self, time};
use th_edit::document::{ClipRef, Frame as EditFrame, FrozenSource, Source, TrackKind, TrackRef, WorkingEdit};
use th_edit::ops::{Op, Refused};
use th_media::sound::Sound;
use th_media::{ExrView, Frame, FrameKey, Media, Proxy};
use th_pipe::Project;
use th_pipe::edits::EditSources;
use th_pipe::master::{self, Change};
use th_pipe::{edit_files, media};
use th_playlist::Take;

use crate::audio::{Placed, Speaker};
use crate::burnin::{self, Burnin};
use crate::colour::{Colour, Lut};
use crate::display::{Display, open_media, theme};
use crate::editing::{self, BinShot, Bound, Document, Origin};
use crate::engine::{Content, Engine, Layer, Timeline};
use crate::look;
use crate::take_menu;
use crate::tracks::{self, ClipView, RowView, Tracks};
use crate::video::{Picture, Video};

mod menu_bar;

/// Decoded frames kept in memory (see `th_media::FrameBuffer`).
const BUFFER_BYTES: usize = 4 << 30;
/// How often a changed, named edit is autosaved.
const AUTOSAVE: Duration = Duration::from_secs(60);
/// The bins' width.
const BINS: f32 = 260.0;

/// Where the project is: what a worker thread opens it from.
#[derive(Debug, Clone)]
pub struct ProjectPaths {
    pub root: PathBuf,
    pub pipeline: PathBuf,
    pub config: PathBuf,
}

impl ProjectPaths {
    /// The project at `root`; the pipeline and config as TumblePipe's
    /// environment names them, else the project's own.
    pub fn at(root: &Path) -> Self {
        let var = |key: &str| std::env::var_os(key).filter(|v| !v.is_empty()).map(PathBuf::from);
        ProjectPaths {
            root: root.to_path_buf(),
            pipeline: var("TH_PIPELINE_PATH").unwrap_or_else(|| root.to_path_buf()),
            config: var("TH_CONFIG_PATH")
                .filter(|_| var("TH_PROJECT_PATH").as_deref() == Some(root))
                .unwrap_or_else(|| root.join("_config")),
        }
    }

    pub fn open(&self) -> Result<Project, String> {
        Project::open_paths(&self.root, &self.pipeline, &self.config)
    }
}

/// A source resolved: the takes it has, the one its rule picks, opened.
#[derive(Debug, Clone)]
pub struct Resolved {
    takes: Vec<Take>,
    take: Option<usize>,
    /// What plays; None for sound alone, or nothing (see `nothing`).
    pub(crate) media: Option<Arc<Media>>,
    pub(crate) sound: Option<Arc<Sound>>,
    /// The frame number of the media's index 0.
    pub(crate) first: EditFrame,
    /// Why nothing plays, when nothing does (a slug, a planned shot, no take).
    nothing: Option<String>,
}

impl Resolved {
    fn nothing(why: impl Into<String>) -> Self {
        Resolved { takes: Vec::new(), take: None, media: None, sound: None, first: 0, nothing: Some(why.into()) }
    }
}

/// What a source's rule picks, opened. Runs off the UI thread: listing takes
/// and opening media take seconds on a file server.
pub(crate) fn resolve(paths: &ProjectPaths, source: &Source, fps: u32) -> Result<Resolved, String> {
    match source {
        Source::Slug { text } => Ok(Resolved::nothing(text.clone())),
        Source::Shot { shot, take: rule } => {
            let project = paths.open()?;
            let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
            if !project.config.exists(&uri).map_err(|e| e.to_string())? {
                return Ok(Resolved::nothing("Planned: made when the edit is published"));
            }
            let takes = EditSources::new(&project, fps).takes(shot)?;
            let Some(take) = th_edit::takes::pick(&takes, rule) else {
                return Ok(Resolved { takes, ..Resolved::nothing("No take fits its rule") });
            };
            let first = th_pipe::timeline::get_frame_range(&project.config, &uri)?.map_or(1001, |r| r.full_range().first_frame);
            let opened = open_media(&takes[take].media, (fps as i32, 1))?;
            let sound = match &takes[take].media {
                th_playlist::Media::Movie { path } => Sound::read(path).unwrap_or_else(|e| {
                    eprintln!("th_player: {e}");
                    None
                }),
                _ => None,
            };
            let first = opened.frame_number(0).unwrap_or(first);
            Ok(Resolved { takes, take: Some(take), media: Some(opened), sound: sound.map(Arc::new), first, nothing: None })
        }
        Source::Media { item, version } => {
            let project = paths.open()?;
            let found = EditSources::new(&project, fps).item(item, version)?;
            let path = th_pipe::storage::slashed(&found.path);
            let take = Take {
                label: format!("{item} {}", found.version),
                department: "media".into(),
                version: found.version.clone(),
                media: th_playlist::Media::Movie { path: path.clone() },
            };
            let opened = match found.measure.frames {
                Some(_) => Some(Media::movie(&path).map(Arc::new).map_err(|e| e.to_string())?),
                None => None,
            };
            let sound = if found.measure.sound { Sound::read(&path).map_err(|e| e.to_string())?.map(Arc::new) } else { None };
            Ok(Resolved { takes: vec![take], take: Some(0), media: opened, sound, first: 0, nothing: None })
        }
    }
}

/// A source of the edit, as far as the editor has it.
struct Entry {
    source: Source,
    state: EntryState,
}

enum EntryState {
    Resolving,
    Ready { resolved: Resolved, view: ExrView, lut: Result<Option<Arc<Lut>>, String> },
    Failed(String),
}

impl Entry {
    fn resolved(&self) -> Option<&Resolved> {
        match &self.state {
            EntryState::Ready { resolved, .. } => Some(resolved),
            _ => None,
        }
    }

    /// What its clips say under their name: the take, or why nothing plays.
    fn detail(&self) -> String {
        match &self.state {
            EntryState::Resolving => "Opening…".into(),
            EntryState::Failed(e) => format!("Cannot play: {e}"),
            EntryState::Ready { resolved, .. } => match (&resolved.nothing, resolved.take) {
                (Some(why), _) => why.clone(),
                (None, Some(t)) => resolved.takes[t].label.clone(),
                (None, None) => String::new(),
            },
        }
    }

    /// The department of the take it shows, if it shows a shot's take.
    fn department(&self) -> Option<String> {
        let resolved = self.resolved()?;
        matches!(self.source, Source::Shot { .. }).then(|| resolved.takes.get(resolved.take?).map(|t| t.department.clone()))?
    }

    fn missing(&self) -> bool {
        match &self.state {
            EntryState::Failed(_) => true,
            EntryState::Ready { resolved, .. } => resolved.nothing.is_some() && !matches!(self.source, Source::Slug { .. }),
            EntryState::Resolving => false,
        }
    }
}

/// A clip's name on the tracks: the shot's, the item's, a slug's text.
fn source_name(source: &Source) -> String {
    match source {
        Source::Shot { shot, .. } => shot.rsplit('/').next().unwrap_or(shot).to_owned(),
        Source::Media { item, .. } => item.clone(),
        Source::Slug { text } => format!("Slug: {text}"),
    }
}

/// The project's shots and media library, for the bins.
#[derive(Debug, Clone, Default)]
pub struct BinData {
    shots: Vec<BinShot>,
    media: Vec<editing::BinItem>,
    /// The project's master edit.
    master: Option<String>,
}

fn load_bins(paths: &ProjectPaths, fps: u32) -> Result<BinData, String> {
    let project = paths.open()?;
    let shots = editing::project_shots(&project)?;
    let mut items = Vec::new();
    for item in media::list_items(&project)? {
        if let Some(last) = media::item_versions(&project, &item)?.pop() {
            items.push(editing::BinItem::of(&item, &last, fps));
        }
    }
    Ok(BinData { shots, media: items, master: master::master_edit(&project)? })
}

/// A row of a bin: a sequence's name over its shots, or something to put
/// in the cut (`index` in the bin's shots or media).
#[derive(Debug, Clone, PartialEq, Eq)]
enum BinRow {
    Sequence(String),
    Item { index: usize, name: String, detail: String },
}

impl BinRow {
    fn item(&self) -> Option<usize> {
        match self {
            BinRow::Item { index, .. } => Some(*index),
            BinRow::Sequence(_) => None,
        }
    }
}

/// `shots` (sorted by sequence, as [`editing::project_shots`] lists them)
/// as bin rows: each sequence's name, then its shots.
fn shot_rows(shots: &[BinShot]) -> Vec<BinRow> {
    let mut out = Vec::new();
    for (index, shot) in shots.iter().enumerate() {
        if index == 0 || shots[index - 1].sequence != shot.sequence {
            out.push(BinRow::Sequence(shot.sequence.clone()));
        }
        let frames = shot.last - shot.first + 1;
        let detail = format!("{frames} frames, {}–{}", shot.first, shot.last);
        out.push(BinRow::Item { index, name: shot.name.clone(), detail });
    }
    out
}

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
    change: Change,
    ticked: bool,
    flag: Option<String>,
}

/// What a publish did.
#[derive(Debug, Clone)]
pub struct PublishReport {
    name: String,
    version: String,
    /// Shot changes written.
    written: usize,
    /// Shot changes not written, and why.
    failed: Vec<String>,
}

/// An edit the Open dialog lists, with its newest working and published
/// versions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditListing {
    name: String,
    working: Option<String>,
    published: Option<String>,
}

/// A dialog over the window.
#[derive(Debug, Clone)]
enum Dialog {
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
    },
    /// For the master edit, `changes` are the shot changes it offers;
    /// `problem` is why it cannot be published.
    Publish {
        note: String,
        changes: Vec<ChangeRow>,
        problem: Option<String>,
    },
    /// A planned shot put at the playhead.
    NewShot {
        sequence: String,
        name: String,
        frames: String,
    },
    Import {
        path: String,
        item: String,
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
}

#[derive(Debug, Clone)]
pub enum Message {
    Start,
    Wake,
    Tick,
    Step(i64),
    Seconds(i64),
    /// To the previous (negative) or next edit point.
    Cut(i64),
    Seek(usize),
    TogglePlay,
    /// A click on the tracks: the clip (None: empty track), whether it adds
    /// to the selection, and the frame under it.
    Select(Option<ClipRef>, bool, usize),
    Drag,
    SelectAll,
    ClearSelection,
    Edit(Op<Source>),
    Undo,
    Redo,
    Split,
    Lift,
    RippleDelete,
    Marker,
    Resolved(usize, Result<Resolved, String>),
    ClipMenu(ClipRef),
    /// A take for the menu's clips: by index of the menu clip's takes, or
    /// None for the best available.
    Take(ClipRef, Option<usize>, bool),
    Bins(Result<BinData, String>),
    BinTab(usize),
    BinPick(usize),
    /// Put the bin's pick at the playhead: inserted (rippling), or over what
    /// is there.
    BinPut(bool),
    Open(OpenDialog),
    /// Open an edit (`entity:/edits/<name>[@vNNNN]`) in a window of its own.
    OpenEdit(String),
    Text(Field, String),
    Confirm,
    /// The second choice of a dialog that has one (Discard).
    Alternate,
    Dismiss,
    Saved(Result<(String, String), String>),
    Published(Result<PublishReport, String>),
    /// Tick or untick a master publish's change.
    TickChange(usize, bool),
    /// Make this edit the master, or no longer.
    SetMaster(bool),
    /// The master edit now.
    MasterSet(Result<Option<String>, String>),
    Imported(Result<String, String>),
    Display(usize),
    View(usize),
    Exposure(f32),
    Burnins(bool),
    Autosave,
    CloseRequested,
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
}

/// The frame on screen and the layers it was made of.
struct Shown {
    layers: Vec<(FrameKey, u32)>,
    picture: Picture,
    source: usize,
}

pub struct Editor {
    paths: ProjectPaths,
    /// The OCIO config it opened, for the windows Open starts.
    ocio: String,
    doc: Document,
    entries: Vec<Entry>,
    engine: Engine,
    timeline: Arc<Timeline>,
    display: Display,
    /// Each engine source's proxy, as the tracks show it.
    proxies: HashMap<usize, (Proxy, Option<ImageHandle>)>,
    position: usize,
    playing: bool,
    shown: Option<Shown>,
    frames_shown: u64,
    selection: BTreeSet<ClipRef>,
    menu_clip: Option<ClipRef>,
    bins: BinData,
    bin_tab: usize,
    bin_pick: Option<usize>,
    dialog: Option<Dialog>,
    /// The last thing said (a refused op, a save), and whether it is an error.
    status: Option<(String, bool)>,
    burnins: bool,
    started: bool,
    quit: bool,
    /// Quit once the save under way lands.
    quit_after_save: bool,
    wake_slot: Arc<Mutex<Option<Sender<Message>>>>,
    clock: (Instant, usize),
    speaker: Option<Speaker>,
    /// The autosave found at start, offered back.
    recovered: Option<WorkingEdit>,
}

fn waker(slot: &Arc<Mutex<Option<Sender<Message>>>>) -> impl Fn() + Send + 'static {
    let slot = Arc::clone(slot);
    move || {
        if let Some(sender) = slot.lock().unwrap().as_mut() {
            let _ = sender.try_send(Message::Wake);
        }
    }
}

impl Editor {
    /// Edit `doc` of the project at `paths`, shown through `colour`;
    /// `recovered` is an autosave newer than it, offered back at start.
    pub fn new(paths: ProjectPaths, ocio: String, doc: Document, colour: Colour, recovered: Option<WorkingEdit>) -> Self {
        let (display, view) = colour.default();
        let engine = Engine::start(vec![Content::none()], Timeline::new(Vec::new()), BUFFER_BYTES);
        let wake_slot: Arc<Mutex<Option<Sender<Message>>>> = Arc::new(Mutex::new(None));
        engine.set_notifier(waker(&wake_slot));
        let timeline = engine.timeline();
        let dialog = recovered.as_ref().and_then(|_| match &doc.origin {
            Origin::Working { name, .. } => Some(Dialog::Recover { name: name.clone() }),
            _ => None,
        });
        Self {
            paths,
            ocio,
            doc,
            entries: Vec::new(),
            engine,
            timeline,
            display: Display { colour, display, view, exposure: 0.0 },
            proxies: HashMap::new(),
            position: 0,
            playing: false,
            shown: None,
            frames_shown: 0,
            selection: BTreeSet::new(),
            menu_clip: None,
            bins: BinData::default(),
            bin_tab: 0,
            bin_pick: None,
            dialog,
            status: None,
            burnins: true,
            started: false,
            quit: false,
            quit_after_save: false,
            wake_slot,
            clock: (Instant::now(), 0),
            speaker: Speaker::open().map_err(|e| eprintln!("th_player: no sound: {e}")).ok(),
            recovered,
        }
    }

    fn fps(&self) -> u32 {
        self.doc.edit.fps.max(1)
    }

    /// Positions the playhead can stand on: the edit, and a second past it.
    fn length(&self) -> usize {
        self.doc.edit.length().max(0) as usize + self.fps() as usize
    }

    fn entry_of(&self, source: &Source) -> Option<usize> {
        self.entries.iter().position(|e| &e.source == source)
    }

    /// The entry an engine source plays (engine source 0 plays none).
    fn entry_of_engine(&self, source: usize) -> Option<&Entry> {
        source.checked_sub(1).and_then(|i| self.entries.get(i))
    }

    fn bind(&self, source: &Source) -> Option<Bound> {
        let i = self.entry_of(source)?;
        match &self.entries[i].state {
            EntryState::Ready { resolved, .. } if resolved.media.is_some() => Some(Bound { source: i + 1, first: resolved.first }),
            _ => None,
        }
    }

    fn engine_timeline(&self) -> Timeline {
        Timeline::new(editing::engine_clips(&self.doc.edit, |s| self.bind(s)))
    }

    /// An entry for every source the edit names that has none, each
    /// resolving off the UI thread.
    fn resolve_new(&mut self) -> Command<Message> {
        let mut sources: Vec<Source> = Vec::new();
        let edit = &self.doc.edit;
        for s in edit
            .video
            .iter()
            .flat_map(|t| t.clips.iter().map(|c| &c.source))
            .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)))
        {
            if self.entry_of(s).is_none() && !sources.contains(s) {
                sources.push(s.clone());
            }
        }
        let fps = self.fps();
        let mut commands = Vec::new();
        for source in sources {
            let index = self.entries.len();
            self.entries.push(Entry { source: source.clone(), state: EntryState::Resolving });
            let timeline = Timeline::new(self.timeline.clips.clone());
            self.timeline = self.engine.replace_source(index + 1, Content::none(), timeline);
            let paths = self.paths.clone();
            commands.push(Command::perform_blocking(move || resolve(&paths, &source, fps), move |r| Message::Resolved(index, r)));
        }
        Command::batch(commands)
    }

    fn resolved(&mut self, index: usize, result: Result<Resolved, String>) {
        let state = match result {
            Ok(resolved) => {
                let view = resolved.media.as_ref().map(|m| m.default_view()).unwrap_or_default();
                let lut = match &resolved.media {
                    Some(m) => self.display.lut(m),
                    None => Ok(None),
                };
                EntryState::Ready { resolved, view, lut }
            }
            Err(e) => EntryState::Failed(e),
        };
        self.entries[index].state = state;
        let content = match &self.entries[index].state {
            EntryState::Ready { resolved: Resolved { media: Some(media), .. }, view, .. } => {
                Content { media: Some(Arc::clone(media)), view: view.clone(), hidden: 0, wants_proxy: true }
            }
            _ => Content::none(),
        };
        self.proxies.remove(&(index + 1));
        let timeline = self.engine_timeline();
        self.timeline = self.engine.replace_source(index + 1, content, timeline);
        self.engine.set_playhead(self.position);
        self.place_sounds();
        self.shown = None;
        self.refresh_shown();
    }

    /// The edit changed: resolve its new sources, place its clips and sound.
    fn changed(&mut self) -> Command<Message> {
        let command = self.resolve_new();
        self.timeline = self.engine.set_timeline(self.engine_timeline());
        self.engine.set_playhead(self.position);
        self.selection.retain(|c| clip_exists(&self.doc.edit, *c));
        self.place_sounds();
        self.shown = None;
        self.refresh_shown();
        command
    }

    /// Apply `ops`, one user action, as one undo step: all of them or,
    /// when one is refused, none (the status says why).
    fn edit(&mut self, ops: &[Op<Source>]) -> Command<Message> {
        if ops.is_empty() {
            return Command::none();
        }
        let result = self.paths.open().map_err(Refused).and_then(|project| {
            let sources = EditSources::new(&project, self.fps());
            self.doc.apply(ops, &sources)
        });
        match result {
            Ok(()) => {
                self.status = None;
                self.changed()
            }
            Err(e) => {
                self.status = Some((format!("Not done: {e}"), true));
                Command::none()
            }
        }
    }

    fn move_to(&mut self, position: usize) {
        self.position = position.min(self.length().saturating_sub(1));
        self.engine.set_playhead(self.position);
        self.refresh_shown();
    }

    fn sound_clock(&self) -> Option<&Speaker> {
        self.speaker.as_ref().filter(|s| s.has_voices())
    }

    fn seconds(&self, position: usize) -> f64 {
        position as f64 / f64::from(self.fps())
    }

    fn position_at(&self, seconds: f64) -> usize {
        (seconds * f64::from(self.fps()) + 1e-6).max(0.0) as usize
    }

    fn set_playing(&mut self, playing: bool) {
        self.playing = playing;
        self.clock = (Instant::now(), self.position);
        if let Some(speaker) = &self.speaker {
            if playing {
                speaker.play_from(self.seconds(self.position));
            } else {
                speaker.stop();
            }
        }
    }

    /// The sound of every clip that has one.
    fn place_sounds(&self) {
        let Some(speaker) = &self.speaker else { return };
        let fps = f64::from(self.fps());
        let mut voices = Vec::new();
        let mut place =
            |source: &Source, at: EditFrame, duration: EditFrame, source_in: EditFrame, gain_db: f64, fades: (EditFrame, EditFrame)| {
                let Some(i) = self.entry_of(source) else { return };
                let Some(resolved) = self.entries[i].resolved() else { return };
                let Some(sound) = resolved.sound.clone() else { return };
                let skip = (resolved.first - source_in).max(0);
                if skip >= duration {
                    return;
                }
                voices.push(Placed {
                    sound,
                    at: (at + skip) as f64 / fps,
                    length: (duration - skip) as f64 / fps,
                    offset: (source_in + skip - resolved.first) as f64 / fps,
                    gain: 10f64.powf(gain_db / 20.0) as f32,
                    fade_in: (fades.0 - skip).max(0) as f64 / fps,
                    fade_out: fades.1 as f64 / fps,
                });
            };
        for c in self.doc.edit.video.iter().flat_map(|t| &t.clips) {
            if matches!(c.source, Source::Shot { .. }) {
                place(&c.source, c.at, c.duration, c.source_in, 0.0, (c.fade_in, c.fade_out));
            }
        }
        for c in self.doc.edit.audio.iter().flat_map(|t| &t.clips) {
            place(&c.source, c.at, c.duration, c.source_in, c.gain_db, (c.fade_in, c.fade_out));
        }
        speaker.set_voices(voices);
    }

    /// Whether `position`'s picture can show: every layer's frame is in or
    /// failed.
    fn settled(&self, position: usize) -> bool {
        self.engine.layers(position).iter().all(|l| self.engine.is_buffered(l.key) || self.engine.failure(l.key).is_some())
    }

    /// One playback tick , at the edit's rate.
    fn advance(&mut self) {
        let next = self.position + 1;
        if next >= self.length() {
            self.set_playing(false);
            return;
        }
        if let Some(speaker) = self.sound_clock()
            && !speaker.is_playing()
        {
            if self.settled(next) {
                speaker.play_from(self.seconds(self.position));
            }
            return;
        }
        let due = match self.sound_clock() {
            Some(speaker) => self.position_at(speaker.seconds()),
            None => {
                let (started, from) = self.clock;
                from + (started.elapsed().as_secs_f64() * f64::from(self.fps())) as usize
            }
        };
        if self.position >= due {
            return;
        }
        let target = if self.sound_clock().is_some() { due.min(self.length() - 1) } else { next };
        let ready = (next..=target).take_while(|&p| self.settled(p)).last();
        if ready != Some(target) {
            self.clock = (Instant::now(), ready.unwrap_or(self.position));
            if let Some(speaker) = self.sound_clock() {
                speaker.stop();
            }
        }
        if let Some(to) = ready {
            self.move_to(to);
        }
    }

    fn refresh_shown(&mut self) {
        let layers = self.engine.layers(self.position);
        let wanted: Vec<(FrameKey, u32)> = layers.iter().map(|l| (l.key, l.opacity.to_bits())).collect();
        if self.shown.as_ref().is_some_and(|s| s.layers == wanted) {
            return;
        }
        if layers.is_empty() {
            self.shown = None;
            return;
        }
        let top = layers.last().unwrap().key.source;
        let blended = layers.len() > 1 || layers[0].opacity < 1.0;
        let picture = if blended {
            self.dissolved(&layers).map(|f| (f, None))
        } else {
            let lut = self.lut_of(top);
            self.engine.frame(layers[0].key).map(|f| (f, lut))
        };
        if let Some((frame, lut)) = picture {
            self.frames_shown += 1;
            self.shown = Some(Shown { layers: wanted, picture: Picture { id: self.frames_shown, frame, lut }, source: top });
        }
    }

    fn lut_of(&self, source: usize) -> Option<Arc<Lut>> {
        match &self.entry_of_engine(source)?.state {
            EntryState::Ready { lut: Ok(lut), .. } => lut.clone(),
            _ => None,
        }
    }

    fn dissolved(&self, layers: &[Layer]) -> Option<Frame> {
        let frames: Vec<Frame> = layers.iter().map(|l| self.engine.frame(l.key)).collect::<Option<_>>()?;
        let luts: Vec<Option<Arc<Lut>>> = layers.iter().map(|l| self.lut_of(l.key.source)).collect();
        let top = frames.last()?;
        let gain = 2f32.powf(self.display.exposure);
        let input: Vec<(&Frame, f32)> = frames.iter().zip(layers).map(|(f, l)| (f, l.opacity)).collect();
        Some(th_media::composite::dissolve(top.width, top.height, &input, |layer, rgb| match &luts[layer] {
            Some(lut) => lut.sample(rgb.map(|c| c * gain)),
            None => rgb,
        }))
    }

    /// What the picture says when it has nothing: why the top clip under
    /// the playhead plays nothing.
    fn card(&self) -> Option<String> {
        let at = self.position as EditFrame;
        let clip = self.doc.edit.video.iter().rev().flat_map(|t| t.clips.iter()).find(|c| c.at <= at && at < c.at + c.duration)?;
        let entry = &self.entries[self.entry_of(&clip.source)?];
        match (&entry.state, &clip.source) {
            (_, Source::Slug { text }) => Some(text.clone()),
            (EntryState::Ready { resolved, .. }, _) if resolved.media.is_some() => None,
            _ => Some(format!("{}: {}", source_name(&clip.source), entry.detail())),
        }
    }

    fn collect_proxies(&mut self) {
        for (source, proxy) in self.engine.take_proxies() {
            let handle = self.proxy_handle(source, &proxy);
            self.proxies.insert(source, (proxy, handle));
        }
    }

    fn proxy_handle(&self, source: usize, proxy: &Proxy) -> Option<ImageHandle> {
        let thumbnail = match self.lut_of(source) {
            Some(lut) => proxy.shown(|rgb| lut.sample(rgb)),
            None => proxy.shown(|rgb| rgb),
        };
        Some(ImageHandle::from_rgba(thumbnail.width, thumbnail.height, thumbnail.rgba))
    }

    fn set_display(&mut self) {
        let Self { entries, display, .. } = self;
        for entry in entries.iter_mut() {
            if let EntryState::Ready { resolved: Resolved { media: Some(media), .. }, lut, .. } = &mut entry.state {
                *lut = display.lut(media);
            }
        }
        let sources: Vec<usize> = self.proxies.keys().copied().collect();
        for source in sources {
            let handle = self.proxy_handle(source, &self.proxies[&source].0);
            self.proxies.get_mut(&source).unwrap().1 = handle;
        }
        self.shown = None;
        self.refresh_shown();
    }

    /// The clips an op on "the clip" applies to: those selected, else the
    /// top video clip under the playhead.
    fn targets(&self) -> Vec<ClipRef> {
        if !self.selection.is_empty() {
            return self.selection.iter().copied().collect();
        }
        let at = self.position as EditFrame;
        (0..self.doc.edit.video.len())
            .rev()
            .find_map(|t| {
                let i = self.doc.edit.video[t].clips.iter().position(|c| c.at <= at && at < c.at + c.duration)?;
                Some(ClipRef { track: TrackRef::video(t), index: i })
            })
            .into_iter()
            .collect()
    }

    /// Ops on several clips, latest first on each track, so the indices of
    /// those still to go hold.
    fn latest_first(mut clips: Vec<ClipRef>) -> Vec<ClipRef> {
        clips.sort_by_key(|c| std::cmp::Reverse((c.track.kind == TrackKind::Audio, c.track.index, c.index)));
        clips
    }

    /// The take menu's choice for `menu`'s clip, applied to it and the other
    /// selected clips: the same take for clips of the same shot, the same
    /// department, kind and layer (newest) for other shots; or the best
    /// available for all. Only a Version row (`pin`) pins, and only on the
    /// clicked clip's shot; a source or department row follows the newest.
    fn change_take(&mut self, menu: ClipRef, take: Option<usize>, pin: bool) -> Command<Message> {
        let Some(Source::Shot { shot: menu_shot, .. }) = clip_source(&self.doc.edit, menu).cloned() else { return Command::none() };
        let picked: Option<Take> = take.and_then(|t| {
            let entry = &self.entries[self.entry_of(clip_source(&self.doc.edit, menu)?)?];
            entry.resolved()?.takes.get(t).cloned()
        });
        let mut targets: Vec<ClipRef> = if self.selection.contains(&menu) { self.selection.iter().copied().collect() } else { vec![menu] };
        targets.retain(|c| matches!(clip_source(&self.doc.edit, *c), Some(Source::Shot { .. })));
        let ops: Vec<Op<Source>> = targets
            .into_iter()
            .filter_map(|clip| {
                let Some(Source::Shot { shot, .. }) = clip_source(&self.doc.edit, clip).cloned() else { return None };
                let rule = take_menu::rule(picked.as_ref(), pin && shot == menu_shot);
                Some(Op::SetSource { clip, source: Source::Shot { shot, take: rule } })
            })
            .collect();
        self.edit(&ops)
    }

    /// Put the bin's pick at the playhead.
    fn bin_put(&mut self, overlay: bool) -> Command<Message> {
        let Some(pick) = self.bin_pick else {
            self.status = Some(("Pick a shot or media item in the bins first".into(), true));
            return Command::none();
        };
        let at = self.position as EditFrame;
        let (video, audio) = match self.bin_tab {
            0 => match self.bins.shots.get(pick) {
                Some(shot) => (Some(shot.clip(at)), None),
                None => return Command::none(),
            },
            _ => match self.bins.media.get(pick) {
                Some(item) => item.clips(at),
                None => return Command::none(),
            },
        };
        let track = self.targets().first().map(|c| c.track).filter(|t| t.kind == TrackKind::Video).map_or(0, |t| t.index);
        let ops = editing::put(&self.doc.edit, video, audio, track, overlay);
        self.edit(&ops)
    }

    fn rows(&self) -> Vec<RowView> {
        let edit = &self.doc.edit;
        tracks::row_order(edit.video.len(), edit.audio.len())
            .into_iter()
            .map(|track| {
                let views = |source: &Source, at, duration, fade_in, fade_out, index| {
                    let entry = self.entry_of(source).map(|i| &self.entries[i]);
                    ClipView {
                        at,
                        duration,
                        fade_in,
                        fade_out,
                        name: source_name(source),
                        detail: entry.map(Entry::detail).unwrap_or_default(),
                        thumbnail: self
                            .entry_of(source)
                            .and_then(|i| self.proxies.get(&(i + 1)))
                            .and_then(|p| p.1.clone())
                            .filter(|_| track.kind == TrackKind::Video),
                        selected: self.selection.contains(&ClipRef { track, index }),
                        missing: entry.is_some_and(Entry::missing),
                        department: entry.and_then(Entry::department),
                    }
                };
                let (name, clips) = match track.kind {
                    TrackKind::Video => {
                        let t = &edit.video[track.index];
                        (
                            t.name.clone(),
                            t.clips.iter().enumerate().map(|(i, c)| views(&c.source, c.at, c.duration, c.fade_in, c.fade_out, i)).collect(),
                        )
                    }
                    TrackKind::Audio => {
                        let t = &edit.audio[track.index];
                        (
                            t.name.clone(),
                            t.clips.iter().enumerate().map(|(i, c)| views(&c.source, c.at, c.duration, c.fade_in, c.fade_out, i)).collect(),
                        )
                    }
                };
                RowView { track, name, clips }
            })
            .collect()
    }

    fn tracks_view(&self) -> Element<Message> {
        let program = Tracks {
            rows: self.rows(),
            markers: self.doc.edit.markers.iter().map(|m| m.at).collect(),
            total: self.length(),
            playhead: self.position,
            buffered: tracks::ranges(&self.engine.buffered_positions()),
            on_seek: Message::Seek,
            on_select: Message::Select,
            on_drag: || Message::Drag,
            on_op: Message::Edit,
            on_menu: Message::ClipMenu,
        };
        let height = program.height().max(tracks::RULER + tracks::ROW);
        let canvas = interactive_canvas(Id::new("edit tracks"), program).width(Length::Fill).height(Length::fixed(height));
        let mut menu = context_menu(Id::new("clip menu"), canvas);
        if let Some(clip) = self.menu_clip
            && let Some(source @ Source::Shot { .. }) = clip_source(&self.doc.edit, clip)
            && let Some(resolved) = self.entry_of(source).and_then(|i| self.entries[i].resolved())
        {
            menu = menu.item("Best available", move || Message::Take(clip, None, false));
            if let Some(shown) = resolved.take {
                let takes = resolved.takes.clone();
                let of_kind = |kind| takes.iter().rposition(|t| t.kind() == kind);
                for submenu in take_menu::submenus(&resolved.takes, shown, of_kind) {
                    let pins = submenu.pins;
                    let rows = submenu
                        .rows
                        .into_iter()
                        .fold(MenuList::new(), |rows, (take, label)| rows.item(label, move || Message::Take(clip, Some(take), pins)));
                    menu = menu.submenu(submenu.label, rows);
                }
            }
        }
        menu.into()
    }

    /// The edit's name, then, at the right, the view's switches and the
    /// timecode: the one large thing, in the accent, because it is where the
    /// playhead is now. The actions are the menu bar's (native on Windows and
    /// macOS, drawn in the window on Linux) and their keys.
    fn toolbar(&self) -> Element<Message> {
        let mut tools = row()
            .spacing(24.0)
            .align_y(Vertical::Center)
            .push(text(self.doc.title()).font_size(15.0).color(look::text()))
            .push(space(Length::Fill, Length::Shrink));
        if let Origin::Working { name, .. } = &self.doc.origin {
            let is_master = self.bins.master.as_deref() == Some(name.as_str());
            let master = row()
                .spacing(6.0)
                .align_y(Vertical::Center)
                .push(checkbox(Id::new("master"), is_master).on_toggle(Message::SetMaster).focusable(false))
                .push(text("Master edit").font_size(13.0).color(look::quiet()));
            tools = tools.push(tooltip(Id::new("master tip"), master, "Publishing the master edit sets the shots' frame ranges"));
        }
        let burnins = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(checkbox(Id::new("burnins"), self.burnins).on_toggle(Message::Burnins).focusable(false))
            .push(text("Burn-ins").font_size(13.0).color(look::quiet()));
        tools = tools
            .push(tooltip(Id::new("burnins tip"), burnins, "B"))
            .push(text(editing::timecode(self.position as EditFrame, &self.doc.edit)).font_size(20.0).color(look::now()));
        container(tools).padding(Padding::from([8.0, 14.0])).width(Length::Fill).background(look::panel()).into()
    }

    /// The Shots bin lists each sequence's shots under its name; the Media
    /// bin its items. Each row is a name and, quieter, what it is.
    fn bins_view(&self) -> Element<Message> {
        // Out of keyboard focus, as are the panel's buttons: a click on them
        // must leave the arrows, Home/End and Space to the timeline (on_key).
        let tabs = segmented(Id::new("bin tabs"), self.bin_tab).push("Shots").push("Media").on_select(Message::BinTab).focusable(false);
        let rows: Vec<BinRow> = match self.bin_tab {
            0 => shot_rows(&self.bins.shots),
            _ => self
                .bins
                .media
                .iter()
                .enumerate()
                .map(|(i, m)| BinRow::Item { index: i, name: m.item.clone(), detail: m.describe() })
                .collect(),
        };
        let count = rows.iter().filter(|r| matches!(r, BinRow::Item { .. })).count();
        let rows = Arc::new(rows);
        let list = {
            let rows = Arc::clone(&rows);
            let pick = Arc::clone(&rows);
            let shown = self.bin_pick.filter(|&p| p < count).and_then(|p| rows.iter().position(|r| r.item() == Some(p)));
            indexed_list(Id::new(if self.bin_tab == 0 { "shot bin" } else { "media bin" }), rows.len(), 36.0, move |i| match &rows[i] {
                BinRow::Sequence(name) => container(text(name.clone()).font_size(12.0).color(look::quiet()))
                    .padding(Padding { top: 14.0, right: 8.0, bottom: 0.0, left: 8.0 })
                    .into(),
                BinRow::Item { name, detail, .. } => column()
                    .push(text(name.clone()).font_size(13.0).color(look::text()))
                    .push(text(detail.clone()).font_size(11.0).color(look::quiet()))
                    .spacing(1.0)
                    .padding(Padding { top: 2.0, right: 8.0, bottom: 2.0, left: 18.0 })
                    .into(),
            })
            .selected(shown)
            // A sequence's name picks its first shot.
            .on_select(move |i| Message::BinPick(pick[i..].iter().find_map(BinRow::item).unwrap_or(0)))
            .on_activate(|_| Message::BinPut(false))
            .width(Length::Fill)
            .height(Length::Fill)
        };
        let writable = !self.doc.read_only();
        let put = |id: &'static str, label: &str, overlay: bool| {
            let b = look::button(Id::new(id), text(label.to_owned()).font_size(12.0)).padding(Padding::from([3.0, 8.0])).focusable(false);
            if writable { b.on_press(move || Message::BinPut(overlay)) } else { b }
        };
        let mut actions = row().spacing(6.0).push(put("insert", "Insert", false)).push(put("overlay", "Overlay", true));
        if self.bin_tab == 0 && writable {
            actions = actions.push(
                look::button(Id::new("new shot"), text("New shot…").font_size(12.0))
                    .padding(Padding::from([3.0, 8.0]))
                    .on_press(|| Message::Open(OpenDialog::NewShot))
                    .focusable(false),
            );
        }
        if self.bin_tab == 1 {
            actions = actions.push(
                look::button(Id::new("import"), text("Import…").font_size(12.0))
                    .padding(Padding::from([3.0, 8.0]))
                    .on_press(|| Message::Open(OpenDialog::Import))
                    .focusable(false),
            );
        }
        let mut page = column().spacing(8.0).padding(Padding::from([10.0, 10.0])).push(tabs);
        if count == 0 {
            let empty = if self.bin_tab == 0 {
                "The project has no shots yet: New shot… plans one in the cut"
            } else {
                "No media yet: Import… copies a file in"
            };
            page = page.push(text(empty).font_size(12.0).color(look::quiet()));
        }
        container(page.push(list).push(actions).height(Length::Fill))
            .background(look::panel())
            .width(Length::fixed(BINS))
            .height(Length::Fill)
            .into()
    }

    fn display_controls(&self) -> Element<Message> {
        let displays = self.display.colour.displays();
        let views = self.display.colour.views(&self.display.display);
        let d = displays.iter().position(|d| *d == self.display.display).unwrap_or(0);
        let v = views.iter().position(|v| *v == self.display.view).unwrap_or(0);
        let label = |w: String| text(w).font_size(13.0).color(look::quiet());
        let controls = row()
            .spacing(8.0)
            .align_y(Vertical::Center)
            .push(label("Display".into()))
            .push(
                combo_box(Id::new("display"), displays)
                    .selected(d)
                    .on_select(Message::Display)
                    .width(Length::fixed(170.0))
                    .focusable(false),
            )
            .push(combo_box(Id::new("view"), views).selected(v).on_select(Message::View).width(Length::fixed(200.0)).focusable(false))
            .push(space(Length::fixed(16.0), Length::Shrink))
            .push(label(format!("Exposure {:+.1}", self.display.exposure)))
            .push(
                slider(Id::new("exposure"), -8.0..=8.0, self.display.exposure, Message::Exposure)
                    .step(0.5)
                    .width(Length::fixed(120.0))
                    .height(20.0)
                    .focusable(false),
            );
        container(controls).padding(Padding::from([6.0, 8.0])).width(Length::Fill).align_x(Horizontal::Center).into()
    }

    fn burnin(&self) -> Option<Burnin> {
        let s = self.shown.as_ref().filter(|_| self.burnins)?;
        let entry = self.entry_of_engine(s.source)?;
        let resolved = entry.resolved()?;
        let key = s.layers.last()?.0;
        let frame = &s.picture.frame;
        let media = resolved.media.as_ref()?;
        Some(Burnin {
            frame_size: (frame.width as f32, frame.height as f32),
            top_left: source_name(&entry.source),
            top_right: entry.detail(),
            bottom_left: editing::timecode(self.position as EditFrame, &self.doc.edit),
            bottom_right: burnin::frame_text(media.frame_number(key.index), key.index, media.frame_count()),
        })
    }

    // -- saving ---------------------------------------------------------------

    fn open_dialog(&mut self, which: OpenDialog) {
        self.dialog = Some(match which {
            OpenDialog::Open => match self.paths.open().and_then(|project| edit_listings(&project)) {
                Ok(edits) => Dialog::OpenEdit { edits, problem: None },
                Err(e) => Dialog::OpenEdit { edits: Vec::new(), problem: Some(e) },
            },
            OpenDialog::Save => match &self.doc.origin {
                Origin::Untitled | Origin::Published { .. } => Dialog::SaveAs { name: String::new(), note: String::new() },
                Origin::Working { name, version } => {
                    Dialog::Save { note: String::new(), warning: self.newer_version(name, version.as_deref()) }
                }
            },
            OpenDialog::SaveAs => Dialog::SaveAs { name: String::new(), note: String::new() },
            OpenDialog::Publish => self.publish_dialog(),
            OpenDialog::Import => Dialog::Import { path: String::new(), item: String::new(), note: String::new() },
            OpenDialog::NewShot => {
                let sequence = self.bin_pick.and_then(|p| self.bins.shots.get(p)).or(self.bins.shots.first()).map(|s| s.sequence.clone());
                Dialog::NewShot { sequence: sequence.unwrap_or_default(), name: String::new(), frames: "48".into() }
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
    fn master_changes(&self, project: &Project, name: &str) -> Result<Vec<ChangeRow>, String> {
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
        let changes = master::changes(project, &uses, &previous)?;
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
            Change::Retire { .. } => return None,
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

    /// Put the planned shot `sequence/name` of `frames` frames at the
    /// playhead, inserted.
    fn new_shot(&mut self, sequence: &str, name: &str, frames: &str) -> Command<Message> {
        let (sequence, name) = (sequence.trim(), name.trim());
        for (what, value) in [("sequence", sequence), ("shot", name)] {
            if !th_pipe::naming::is_valid_entity_name(value) {
                self.status = Some((format!("{value:?}: a {what}'s name is letters and digits"), true));
                return Command::none();
            }
        }
        let Some(frames) = frames.trim().parse::<EditFrame>().ok().filter(|&f| f > 0) else {
            self.status = Some((format!("{frames:?}: the frames are a whole number above 0"), true));
            return Command::none();
        };
        let uri = format!("entity:/shots/{sequence}/{name}");
        if self.bins.shots.iter().any(|s| s.uri == uri) {
            self.status = Some((format!("{sequence}/{name} exists: pick it in the Shots bin"), true));
            return Command::none();
        }
        self.dialog = None;
        let planned = BinShot { uri, sequence: sequence.into(), name: name.into(), first: 1001, last: 1000 + frames };
        let track = self.targets().first().map(|c| c.track).filter(|t| t.kind == TrackKind::Video).map_or(0, |t| t.index);
        let ops = editing::put(&self.doc.edit, Some(planned.clip(self.position as EditFrame)), None, track, false);
        self.edit(&ops)
    }

    /// Make this edit the master (or, `on` false, leave the project without
    /// one).
    fn set_master(&mut self, on: bool) -> Command<Message> {
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

    /// Resolve every shot source again (their takes and ranges may have
    /// changed under them).
    fn re_resolve_shots(&mut self) -> Command<Message> {
        let fps = self.fps();
        let mut commands = Vec::new();
        for (index, entry) in self.entries.iter_mut().enumerate() {
            if matches!(entry.source, Source::Shot { .. }) {
                entry.state = EntryState::Resolving;
                let (paths, source) = (self.paths.clone(), entry.source.clone());
                commands.push(Command::perform_blocking(move || resolve(&paths, &source, fps), move |r| Message::Resolved(index, r)));
            }
        }
        Command::batch(commands)
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

    fn save(&mut self, name: String, note: String, create: bool) -> Command<Message> {
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
    fn publish(&mut self, note: String, changes: Vec<Change>) -> Command<Message> {
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

    fn import(&mut self, path: String, item: String, note: String) -> Command<Message> {
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

    fn refresh_bins(&self) -> Command<Message> {
        let paths = self.paths.clone();
        let fps = self.fps();
        Command::perform_blocking(move || load_bins(&paths, fps), Message::Bins)
    }

    fn autosave(&mut self) {
        let Origin::Working { name, .. } = &self.doc.origin else { return };
        if !self.doc.dirty() {
            return;
        }
        let result = self.paths.open().and_then(|p| edit_files::autosave(&p, name, &self.doc.edit));
        if let Err(e) = result {
            self.status = Some((format!("Autosave failed: {e}"), true));
        }
    }

    fn dialog_view(&self) -> Option<impl Fn() -> Element<Message> + 'static> {
        let dialog = self.dialog.clone()?;
        let title = self.doc.title();
        Some(move || dialog_element(&dialog, &title))
    }
}

/// The clip `clip` of `edit`'s source.
fn clip_source(edit: &WorkingEdit, clip: ClipRef) -> Option<&Source> {
    match clip.track.kind {
        TrackKind::Video => edit.video.get(clip.track.index)?.clips.get(clip.index).map(|c| &c.source),
        TrackKind::Audio => edit.audio.get(clip.track.index)?.clips.get(clip.index).map(|c| &c.source),
    }
}

fn clip_exists(edit: &WorkingEdit, clip: ClipRef) -> bool {
    clip_source(edit, clip).is_some()
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
        Dialog::SaveAs { name, note } => {
            page = page
                .push(heading("Save as a new edit"))
                .push(label("Name (letters and digits)"))
                .push(field("name", name, Field::Name, "Main"))
                .push(label("Note"))
                .push(field("note", note, Field::Note, "optional"));
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
                        let mut line = row()
                            .spacing(8.0)
                            .align_y(Vertical::Center)
                            .push(checkbox(Id::new(format!("change {i}")), row_.ticked).on_toggle(move |on| Message::TickChange(i, on)));
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
        Dialog::NewShot { sequence, name, frames } => {
            page = page
                .push(heading("New shot"))
                .push(label("A planned shot: it is made when the master edit is published."))
                .push(label("Sequence"))
                .push(field("sequence", sequence, Field::Sequence, "S"))
                .push(label("Shot"))
                .push(field("name", name, Field::Name, "030"))
                .push(label("Frames"))
                .push(field("frames", frames, Field::Frames, "48"));
            buttons = buttons.push(choice("ok", "Insert", Message::Confirm)).push(choice("cancel", "Cancel", Message::Dismiss));
        }
        Dialog::Import { path, item, note } => {
            page = page
                .push(heading("Import media"))
                .push(label("File (a movie or a sound; it is copied into the project)"))
                .push(field("path", path, Field::Path, "C:/path/to/animatic.mov"))
                .push(label("Item name (letters and digits; an existing name gets a new version)"))
                .push(field("item", item, Field::Item, "animatic"))
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
    }
    container(page.push(buttons)).surface().border_radius(6.0).into()
}

impl Application for Editor {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        if self.quit {
            return Vec::new();
        }
        let title = format!("{} - TumblePipe Editor", self.doc.title());
        vec![window::Window::new(window::Id::MAIN).title(title).size(Size::new(1440.0, 940.0)).theme(theme())]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Start if !self.started => {
                self.started = true;
                let resolve = self.changed();
                return Command::batch([resolve, self.refresh_bins()]);
            }
            Message::Start => {}
            Message::Wake => {
                self.collect_proxies();
                self.refresh_shown();
            }
            Message::Tick => {
                if self.playing {
                    self.advance();
                }
            }
            Message::Step(delta) => {
                self.set_playing(false);
                self.move_to(self.position.saturating_add_signed(delta as isize));
            }
            Message::Seconds(delta) => {
                self.set_playing(false);
                self.move_to(self.position.saturating_add_signed(delta as isize * self.fps() as isize));
            }
            Message::Cut(delta) => {
                self.set_playing(false);
                let points = editing::edit_points(&self.doc.edit);
                let at = self.position as EditFrame;
                let to = if delta < 0 { points.iter().rev().find(|&&p| p < at) } else { points.iter().find(|&&p| p > at) };
                if let Some(&to) = to {
                    self.move_to(to as usize);
                }
            }
            Message::Seek(position) => {
                self.set_playing(false);
                self.move_to(position);
            }
            Message::TogglePlay => self.set_playing(!self.playing),
            Message::Select(clip, additive, position) => match clip {
                None => {
                    self.selection.clear();
                    self.set_playing(false);
                    self.move_to(position);
                }
                Some(clip) if additive => {
                    if !self.selection.remove(&clip) {
                        self.selection.insert(clip);
                    }
                }
                Some(clip) => {
                    if !self.selection.contains(&clip) {
                        self.selection = BTreeSet::from([clip]);
                    }
                }
            },
            Message::Drag => {}
            Message::SelectAll => {
                let edit = &self.doc.edit;
                self.selection =
                    edit.tracks().flat_map(|track| (0..edit.spans(track).len()).map(move |index| ClipRef { track, index })).collect();
            }
            Message::ClearSelection => self.selection.clear(),
            Message::Edit(op) => {
                // A move of a selected clip with others selected moves them all.
                if let Op::Move { clip, to, at } = &op
                    && self.selection.len() > 1
                    && self.selection.contains(clip)
                    && to == &clip.track
                {
                    let from = self.doc.edit.spans(clip.track)[clip.index].0;
                    let ops = editing::move_all(&self.doc.edit, &self.selection, at - from);
                    let command = self.edit(&ops);
                    self.selection.clear();
                    return command;
                }
                let moved = matches!(op, Op::Move { .. });
                let command = self.edit(&[op]);
                if moved {
                    self.selection.clear();
                }
                return command;
            }
            Message::Undo => {
                if self.doc.undo() {
                    return self.changed();
                }
            }
            Message::Redo => {
                if self.doc.redo() {
                    return self.changed();
                }
            }
            Message::Split => {
                let at = self.position as EditFrame;
                let ops: Vec<Op<Source>> = Self::latest_first(self.targets()).into_iter().map(|clip| Op::Split { clip, at }).collect();
                return self.edit(&ops);
            }
            Message::Lift => {
                let ops: Vec<Op<Source>> =
                    Self::latest_first(self.selection.iter().copied().collect()).into_iter().map(|clip| Op::Lift { clip }).collect();
                self.selection.clear();
                return self.edit(&ops);
            }
            Message::RippleDelete => {
                // One at a time from the latest: each takes its span out of every track.
                let ops: Vec<Op<Source>> = editing::latest_spans_first(&self.doc.edit, self.selection.iter().copied().collect())
                    .into_iter()
                    .map(|clip| Op::RippleDelete { clip })
                    .collect();
                self.selection.clear();
                return self.edit(&ops);
            }
            Message::Marker => {
                let at = self.position as EditFrame;
                let op = match self.doc.edit.markers.iter().position(|m| m.at == at) {
                    Some(index) => Op::RemoveMarker { index },
                    None => Op::AddMarker { at, note: String::new() },
                };
                return self.edit(&[op]);
            }
            Message::Resolved(index, result) => self.resolved(index, result),
            Message::ClipMenu(clip) => self.menu_clip = Some(clip),
            Message::Take(clip, take, pin) => return self.change_take(clip, take, pin),
            Message::Bins(result) => match result {
                Ok(bins) => self.bins = bins,
                Err(e) => self.status = Some((format!("The bins: {e}"), true)),
            },
            Message::BinTab(tab) => {
                self.bin_tab = tab;
                self.bin_pick = None;
            }
            Message::BinPick(pick) => self.bin_pick = Some(pick),
            Message::BinPut(overlay) => return self.bin_put(overlay),
            Message::Open(which) => self.open_dialog(which),
            Message::Text(f, value) => match (&mut self.dialog, f) {
                (
                    Some(
                        Dialog::Save { note, .. }
                        | Dialog::Publish { note, .. }
                        | Dialog::SaveAs { note, .. }
                        | Dialog::Import { note, .. },
                    ),
                    Field::Note,
                ) => *note = value,
                (Some(Dialog::SaveAs { name, .. } | Dialog::NewShot { name, .. }), Field::Name) => *name = value,
                (Some(Dialog::NewShot { sequence, .. }), Field::Sequence) => *sequence = value,
                (Some(Dialog::NewShot { frames, .. }), Field::Frames) => *frames = value,
                (Some(Dialog::Import { path, item, .. }), Field::Path) => {
                    // Name the item after the file until a name is typed.
                    let stem = |p: &str| editing::item_name_of(p);
                    if item.is_empty() || *item == stem(path) {
                        *item = stem(&value);
                    }
                    *path = value;
                }
                (Some(Dialog::Import { item, .. }), Field::Item) => *item = value,
                _ => {}
            },
            Message::Confirm => match self.dialog.clone() {
                Some(Dialog::Save { note, .. }) => {
                    if let Origin::Working { name, .. } = self.doc.origin.clone() {
                        return self.save(name, note, false);
                    }
                }
                Some(Dialog::SaveAs { name, note }) => {
                    let name = name.trim().to_owned();
                    if !th_pipe::naming::is_valid_entity_name(&name) {
                        self.status = Some((format!("{name:?}: an edit's name is letters and digits"), true));
                        return Command::none();
                    }
                    return self.save(name, note, true);
                }
                Some(Dialog::Publish { problem: Some(_), .. }) => {}
                Some(Dialog::Publish { note, changes, problem: None }) => {
                    let ticked = changes.into_iter().filter(|r| r.ticked).map(|r| r.change).collect();
                    return self.publish(note, ticked);
                }
                Some(Dialog::NewShot { sequence, name, frames }) => return self.new_shot(&sequence, &name, &frames),
                Some(Dialog::Import { path, item, note }) => {
                    let item = item.trim().to_owned();
                    if !th_pipe::naming::is_valid_entity_name(&item) {
                        self.status = Some((format!("{item:?}: an item's name is letters and digits"), true));
                        return Command::none();
                    }
                    return self.import(path, item, note);
                }
                Some(Dialog::Recover { .. }) => {
                    if let Some(edit) = self.recovered.take() {
                        self.doc.edit = edit;
                        self.dialog = None;
                        return self.changed();
                    }
                    self.dialog = None;
                }
                Some(Dialog::Unsaved) => {
                    self.quit_after_save = true;
                    self.open_dialog(OpenDialog::Save);
                }
                // Open: each edit has its own buttons; Enter picks none.
                Some(Dialog::OpenEdit { .. } | Dialog::Busy(_)) | None => {}
            },
            Message::Alternate => match &self.dialog {
                Some(Dialog::Recover { name }) => {
                    if let Ok(project) = self.paths.open() {
                        let _ = edit_files::discard_autosave(&project, name);
                    }
                    self.recovered = None;
                    self.dialog = None;
                }
                Some(Dialog::Unsaved) => {
                    if let (Origin::Working { name, .. }, Ok(project)) = (&self.doc.origin, self.paths.open()) {
                        let _ = edit_files::discard_autosave(&project, name);
                    }
                    self.quit = true;
                }
                _ => {}
            },
            Message::Dismiss => {
                if !matches!(self.dialog, Some(Dialog::Busy(_))) {
                    self.dialog = None;
                    self.quit_after_save = false;
                }
            }
            Message::Saved(result) => {
                self.dialog = None;
                match result {
                    Ok((name, version)) => {
                        self.status = Some((format!("Saved {name} {version}"), false));
                        self.doc.origin = Origin::Working { name, version: Some(version) };
                        self.doc.mark_saved();
                        if self.quit_after_save {
                            self.quit = true;
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
            Message::Display(index) => {
                if let Some(d) = self.display.colour.displays().get(index).cloned() {
                    self.display.display = d;
                    let views = self.display.colour.views(&self.display.display);
                    if !views.contains(&self.display.view) {
                        self.display.view = views[0].clone();
                    }
                    self.set_display();
                }
            }
            Message::View(index) => {
                if let Some(v) = self.display.colour.views(&self.display.display).get(index).cloned() {
                    self.display.view = v;
                    self.set_display();
                }
            }
            Message::Exposure(stops) => self.display.exposure = stops,
            Message::Burnins(on) => self.burnins = on,
            Message::Autosave => self.autosave(),
            Message::OpenEdit(edit) => {
                self.dialog = None;
                let started = std::env::current_exe().map_err(|e| e.to_string()).and_then(|exe| {
                    std::process::Command::new(exe)
                        .args(open_arguments(&self.paths.root, &self.ocio, &edit))
                        .spawn()
                        .map_err(|e| e.to_string())
                });
                match started {
                    // Nothing to keep here: the edit takes this window's place.
                    Ok(_) if self.doc.replaceable() => self.quit = true,
                    Ok(_) => self.status = Some((format!("Opening {edit} in a new window"), false)),
                    Err(e) => self.status = Some((format!("{edit} did not open: {e}"), true)),
                }
            }
            Message::CloseRequested => {
                if self.doc.dirty() {
                    self.dialog = Some(Dialog::Unsaved);
                } else {
                    self.quit = true;
                }
            }
        }
        Command::none()
    }

    fn on_key(&self, _window: window::Id, key: Key, modifiers: Modifiers) -> Option<Message> {
        menu_bar::key_message(&self.menu_state(), key, modifiers)
    }

    fn menu(&self) -> Option<primer::menu::Menu<Message>> {
        Some(self.menu_bar())
    }

    fn on_close_requested(&self, _window: window::Id) -> Option<Message> {
        Some(Message::CloseRequested)
    }

    fn subscription(&self) -> Subscription<Message> {
        let slot = Arc::clone(&self.wake_slot);
        let engine_events = subscription::channel(subscription::hash_id(&"th_player editor engine"), 4, move |sender| async move {
            let mut sender = sender;
            let _ = sender.try_send(Message::Start);
            *slot.lock().unwrap() = Some(sender);
            futures::future::pending::<()>().await;
        });
        let autosave = time::every(AUTOSAVE, || Message::Autosave);
        if !self.playing {
            return Subscription::batch([engine_events, autosave]);
        }
        let half_frame = Duration::from_secs_f64(0.5 / f64::from(self.fps()));
        Subscription::batch([engine_events, autosave, time::every(half_frame, || Message::Tick)])
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let picture = self.shown.as_ref().map(|s| s.picture.clone());
        let video = shader(Video { picture, exposure: self.display.exposure }).width(Length::Fill).height(Length::Fill).redraw(false);
        let mut layers = stack().push(video).width(Length::Fill).height(Length::Fill);
        if let Some(burnin) = self.burnin() {
            layers = layers.push(canvas(burnin).width(Length::Fill).height(Length::Fill));
        }
        if self.shown.is_none()
            && let Some(card) = self.card()
        {
            let words = text(card).font_size(15.0).color(look::quiet());
            layers = layers
                .push(container(words).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center));
        }
        let middle = row()
            .push(self.bins_view())
            .push(column().push(layers).push(self.display_controls()).width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill);
        let mut page = column().push(self.toolbar()).push(middle).push(self.tracks_view()).width(Length::Fill).height(Length::Fill);
        if let Some((words, error)) = &self.status {
            let colour = if *error { look::error() } else { look::text() };
            page = page.push(
                container(text(words.clone()).font_size(13.0).color(colour))
                    .padding(Padding::from([6.0, 14.0]))
                    .width(Length::Fill)
                    .background(look::panel()),
            );
        }
        let mut root = modal(page);
        if let Some(view) = self.dialog_view() {
            root = root.dialog(Id::new("dialog"), view).on_dismiss(|| Message::Dismiss);
        }
        root.into()
    }
}

/// The rate and size a new edit takes: `shot`'s, else the project's
/// (24 fps, 1920x1080 when it says none).
fn edit_format(project: &Project, shot: Option<&th_pipe::Uri>) -> Result<(u32, [u32; 2]), String> {
    let root = th_pipe::Uri::parse("entity:/shots").map_err(|e| e.to_string())?;
    let entity = shot.unwrap_or(&root);
    let fps = th_pipe::timeline::get_fps(&project.config, entity)?.and_then(|f| u32::try_from(f).ok()).filter(|&f| f > 0).unwrap_or(24);
    let properties = project.config.get_inherited_properties(entity).map_err(|e| e.to_string())?.unwrap_or_default();
    let resolution = properties
        .get("render")
        .and_then(|r| r.get("resolution"))
        .and_then(|r| r.as_array())
        .and_then(|r| Some([r.first()?.as_u64()? as u32, r.get(1)?.as_u64()? as u32]))
        .filter(|r| r[0] > 0 && r[1] > 0)
        .unwrap_or([1920, 1080]);
    Ok((fps, resolution))
}

/// The document the arguments ask for, and an autosave newer than it:
/// `edit` (`entity:/edits/<name>`, its newest working version; with
/// `@vNNNN` that published version, read-only), else `shots` end to end in
/// an untitled edit. An edit with nothing saved yet, and an untitled edit
/// with no `shots`, open as every shot of the project ([`project_fill`]).
/// The arguments that open `edit` (`entity:/edits/<name>[@vNNNN]`) of the
/// project at `root`, shown through `ocio`.
pub fn open_arguments(root: &Path, ocio: &str, edit: &str) -> Vec<String> {
    vec!["--project".into(), root.to_string_lossy().into_owned(), "--ocio".into(), ocio.into(), "--edit".into(), edit.into()]
}

/// The project's edits by name, each with its newest working and published
/// versions.
fn edit_listings(project: &Project) -> Result<Vec<EditListing>, String> {
    let mut out = Vec::new();
    for name in edit_files::list_edits(project)? {
        let working = edit_files::working_versions(project, &name)?.pop();
        let published = edit_files::published_versions(project, &name)?.pop();
        out.push(EditListing { name, working, published });
    }
    Ok(out)
}

/// [`editing::project_fill`] of `project`, at the project's format.
fn project_fill(project: &Project) -> Result<WorkingEdit, String> {
    let (fps, resolution) = edit_format(project, None)?;
    Ok(editing::project_fill(&editing::project_shots(project)?, fps, resolution))
}

pub fn open_document(paths: &ProjectPaths, edit: Option<&str>, shots: &[String]) -> Result<(Document, Option<WorkingEdit>), String> {
    let project = paths.open()?;
    if let Some(edit) = edit {
        let (uri, published) = match edit.rsplit_once('@') {
            Some((uri, version)) => (uri, Some(version.to_owned())),
            None => (edit, None),
        };
        let name = edit_files::edit_name(&th_pipe::Uri::parse_address(uri).map_err(|e| e.to_string())?)?;
        if !edit_files::list_edits(&project)?.contains(&name) {
            return Err(format!("there is no edit {name}"));
        }
        if let Some(version) = published {
            let frozen = edit_files::load_published(&project, &name, &version)?;
            return Ok((Document::new(th_edit::freeze::thaw(&frozen), Origin::Published { name, version }), None));
        }
        let (working, version) = match edit_files::working_versions(&project, &name)?.pop() {
            Some(last) => (edit_files::load_working(&project, &name, &last)?, Some(last)),
            None => {
                // Never saved: it starts as the whole project.
                let working = project_fill(&project)?;
                let recovered = edit_files::recoverable(&project, &name)?.filter(|r| *r != working);
                return Ok((Document::filled(working, Origin::Working { name, version: None }), recovered));
            }
        };
        let recovered = edit_files::recoverable(&project, &name)?.filter(|r| *r != working);
        return Ok((Document::new(working, Origin::Working { name, version }), recovered));
    }
    if shots.is_empty() {
        return Ok((Document::filled(project_fill(&project)?, Origin::Untitled), None));
    }
    let mut ranges = Vec::new();
    for shot in shots {
        let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
        let range = th_pipe::timeline::get_frame_range(&project.config, &uri)?.ok_or_else(|| format!("{shot} has no frame range"))?;
        let play = range.play_range();
        ranges.push((uri.to_string(), play.first_frame, play.last_frame));
    }
    let first = shots.first().map(|s| th_pipe::Uri::parse_address(s)).transpose().map_err(|e| e.to_string())?;
    let (fps, resolution) = edit_format(&project, first.as_ref())?;
    Ok((Document::new(editing::of_shots(&ranges, fps, resolution), Origin::Untitled), None))
}

#[cfg(test)]
mod tests {
    use super::*;
    use th_edit::document::{Rule, VersionRule};

    #[test]
    fn engine_sources_skip_the_black_one() {
        assert_eq!(source_name(&Source::Shot { shot: "entity:/shots/A/010".into(), take: Rule::best() }), "010");
        assert_eq!(source_name(&Source::Media { item: "music".into(), version: VersionRule::Latest }), "music");
    }

    #[test]
    fn several_clips_go_latest_first() {
        let c = |kind: TrackKind, track: usize, index: usize| ClipRef { track: TrackRef { kind, index: track }, index };
        let order = Editor::latest_first(vec![
            c(TrackKind::Video, 0, 1),
            c(TrackKind::Video, 0, 3),
            c(TrackKind::Audio, 0, 0),
            c(TrackKind::Video, 1, 0),
        ]);
        assert_eq!(order, [c(TrackKind::Audio, 0, 0), c(TrackKind::Video, 1, 0), c(TrackKind::Video, 0, 3), c(TrackKind::Video, 0, 1)]);
    }
}
