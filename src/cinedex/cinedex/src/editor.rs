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
use serde_json::{Value, json};
use th_edit::document::{ClipRef, Frame as EditFrame, Source, TakeKind, TrackKind, TrackRef, WorkingEdit};
use th_edit::ops::{Op, Refused};
use th_media::{ExrView, Media, Proxy};
use th_pipe::Project;
use th_pipe::desktop::{Desktop, Opened, Session};
use th_pipe::edits::EditSources;
use th_pipe::houdini_link::HoudiniLink;
use th_playlist::Take;

use crate::audio::Speaker;
use crate::board::{self, Board};
use crate::burnin::{self, Burnin};
use crate::colour::Colour;
use crate::display::{Display, theme};
use crate::editing::{self, BinShot, Document, Origin};
use crate::engine::{Content, Engine, Timeline};
use crate::flip;
use crate::layer_view;
use crate::layout_prefs;
use crate::look;
use crate::menu_words::Roomy;
use crate::rpc::{self, Refusal};
use crate::take_menu;
use crate::tracks::{self, ClipView, RowView, Tracks};
use crate::video::Video;
use document_io::{master_refusal, open_arguments};
use entries::{Entry, EntryState, Resolved, show_word};
use file_dialogs::{Dialog, Field, OpenDialog, PublishReport};
use playback::Shown;
use watch::BinData;

mod agent;
mod bin_files;
mod bin_menu;
mod bin_panel;
#[cfg(test)]
mod bin_wiring_tests;
mod clip_fields;
mod clip_menu;
pub(crate) mod document_io;
pub(crate) mod entries;
mod export_dialog;
mod file_dialogs;
mod gestures;
mod menu_bar;
mod menu_row;
mod panels;
mod playback;
mod range_shot;
mod rendering_watch;
mod shot_sync;
mod timeline_dialog;
mod timeline_menu;
mod timeline_view;
#[cfg(test)]
mod timeline_window_tests;
mod track_order;
mod track_rows;
mod transport;
mod watch;
mod waves;
#[cfg(test)]
mod wiring_tests;

/// The status line's height under everything: a 13 px line and its padding,
/// as it was laid out when it came and went.
const STATUS_HEIGHT: f32 = 29.0;
/// The player's strip over the picture, where Live and Burn-ins are.
const PLAYER_HEADER_HEIGHT: f32 = 28.0;
/// The timer's size: the window's one large word.
const TIMER_SIZE: f32 = 20.0;

/// Decoded frames kept in memory (see `th_media::FrameBuffer`).
const BUFFER_BYTES: usize = 4 << 30;
/// How often a changed, named edit is autosaved.
const AUTOSAVE: Duration = Duration::from_secs(60);
/// How often the board asks Desktop which workfiles a Houdini has open.
const SESSIONS_EVERY: Duration = Duration::from_secs(3);
/// How often the live playblasts the shots play are looked at: a file
/// stat each, on this machine's disk.
const LIVE_EVERY: Duration = Duration::from_millis(500);
/// How often the change journal is looked at: one local file stat. A new
/// playblast, render or live run of a shot, or a config write, shows
/// within this.
const JOURNAL_EVERY: Duration = Duration::from_millis(500);
/// How often the local farm's batches are read for ready preview frames
/// (#75, `previews`): a few small files on this machine's disk.
const PREVIEWS_EVERY: Duration = Duration::from_secs(5);
/// Every how many of those polls a render still saying "rendering" is
/// asked whether its husk is alive (its Houdini may have been killed).
const LIVE_HUSK_POLLS: u64 = 4;
/// Why a workfile cannot be opened from here.
const NO_DESKTOP: &str = "Opening workfiles needs a newer TumbleTrove Desktop, with its Remote Control API on (Settings > General), and this editor started from it";

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

/// How wide the display row's strip of a stack's layers is when they
/// scroll (more than `layer_view::LAYERS_INLINE`).
const LAYER_STRIP_WIDTH: f32 = 320.0;

/// A clip's name on the tracks: the shot's, the item's, a slug's text.
pub(crate) fn source_name(source: &Source) -> String {
    match source {
        Source::Shot { shot, .. } => shot.rsplit('/').next().unwrap_or(shot).to_owned(),
        Source::Media { item, .. } => item.clone(),
        Source::Slug { text } => format!("Slug: {text}"),
    }
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
    /// Several ops of one gesture, one undo step (a clip dragged in
    /// between clips of its track, `tracks::reorder`).
    Edits(Vec<Op<Source>>),
    /// Text typed into a clip field (#60).
    ClipField(clip_fields::ClipField, String),
    /// Enter in a clip field: set what was typed.
    ClipFieldSet(clip_fields::ClipField),
    Undo,
    Redo,
    Split,
    Lift,
    RippleDelete,
    Marker,
    Resolved(usize, Result<Resolved, String>),
    /// A right press on a clip, just before its menu opens: the clip and
    /// the edit frame under the press (`clip_menu`).
    ClipMenu(ClipRef, usize),
    /// A right press on the timeline off the clips, just before its menu
    /// opens (`timeline_menu`): the frame under it (None on a track's
    /// name) and the track of its row.
    TimelineMenu(Option<usize>, Option<TrackRef>),
    /// A range, a box or a gap picked on the tracks (`gestures`).
    Tracks(tracks::Gesture),
    /// Close the gap before a clip: it and what follows on its track move
    /// earlier (`gestures`).
    CloseGap(ClipRef),
    /// The wheel over the tracks: zoom or scroll the timeline (`timeline_view`).
    TimelineView(tracks::ViewChange),
    /// The strip under the tracks was dragged or paged: the first frame
    /// to show.
    ScrollTo(f64),
    /// An end of the strip's thumb was dragged: the first frame to show,
    /// and how many (`tracks::overview`).
    ShowFrames(f64, usize),
    /// View > Fit to Clips (Shift+F): the tracks show the first clip's
    /// start to the last clip's end.
    FitClips,
    /// Park the playhead on a frame, then do what acts at the playhead
    /// (the timeline menu's "here" rows).
    At(usize, Box<Message>),
    /// A take for the menu's clips: by index of the menu clip's takes, or
    /// None for the best available.
    Take(ClipRef, Option<usize>, bool),
    /// A take key (#47): step the selected clips (else the clip under the
    /// playhead) along an axis of the take menu.
    TakeStep(take_menu::TakeAxis, i8),
    /// The take menu's Layer row (#58): the render layer for the menu's
    /// clips' rules, None for best available.
    TakeLayer(ClipRef, Option<String>),
    /// The viewer's AOV dropdown: an EXR layer of the take on screen.
    Aov(usize),
    /// The viewer's layer toggles: show a stack layer of the take on
    /// screen, or hide it.
    ShowLayer(usize, bool),
    Bins(Result<BinData, String>),
    /// The middle of the window: 0 the cut (bins and viewer), 1 the board.
    Mode(usize),
    Board(Result<Board, String>),
    PollSessions,
    Sessions(Result<Vec<Session>, String>),
    /// Open a shot's (URI) department workfile in Houdini.
    OpenWorkfile(String, String),
    /// Show a take's folder in the file manager (#59).
    Reveal(PathBuf),
    /// The workfile's name, and what opening it did.
    Opened(Result<(String, Opened), String>),
    /// A call through the player's endpoint (Live view, the agent
    /// commands), to answer.
    Rpc(crate::rpc::Call),
    /// Work an agent command started is done: answer its call.
    Answer(crate::rpc::Call, Result<Value, Refusal>),
    /// Stop looping the Live view's range.
    ClearLoop,
    /// Keep the live playblast an entry (by index) plays as the shot's
    /// next playblast version.
    KeepLive(usize),
    /// The entry, and the playblast kept (or why not).
    Kept(usize, Result<PathBuf, String>),
    BinTab(usize),
    BinPick(usize),
    /// Put the bin's pick at the playhead: inserted (rippling), or over what
    /// is there.
    BinPut(bool),
    /// The bins panel's own (`editor/bin_panel.rs`).
    Bin(bin_panel::BinMessage),
    /// Files on their way into the media library (`editor/bin_files.rs`).
    Files(bin_files::FilesMessage),
    Open(OpenDialog),
    /// Open an edit (`entity:/edits/<name>[@vNNNN]`) in a window of its own.
    OpenEdit(String),
    /// A clip's Convert to Shot… (`range_shot`, `convert_shot`).
    ConvertToShot(ClipRef),
    /// A clip swapped with the clip it replaced (`convert_shot`).
    SwapOriginal(ClipRef),
    Text(Field, String),
    Confirm,
    /// The second choice of a dialog that has one (Discard; New shot's
    /// Placeholder).
    Alternate,
    /// New shot's "Create the shot now" ticked or not (`range_shot`).
    NewShotNow(bool),
    /// Edit > Sync Shot Ranges: trims of the master edit's shot clips set
    /// the shots' frames at once, or not (`shot_sync`).
    SyncRanges(bool),
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
    /// File > Export movie… and its dialog.
    Export(export_dialog::ExportMessage),
    /// File > Export Timeline… and its dialog.
    Timeline(timeline_dialog::TimelineMessage),
    Display(usize),
    View(usize),
    Exposure(f32),
    Burnins(bool),
    /// View > Flip Horizontally (Shift+H): the picture mirrored, or not
    /// (`flip`). The view's only, never saved.
    Flip(bool),
    /// View > Fit Timeline (F): the tracks span the cut and a second past
    /// it again (`timeline_view`).
    FitTimeline,
    /// The bottom edge of a track's header was dragged: its row is this
    /// tall now (remembered for this user, `track_rows`).
    RowHeight(TrackRef, f32),
    /// A track's name was double-clicked: show only its row, or, when it
    /// is the one shown, every track's again. The view's only.
    Solo(TrackRef),
    /// View > Hide Audio Tracks: the audio tracks have no rows (remembered
    /// for this user). The view's only: they still play.
    HideAudio(bool),
    /// View > Show All Tracks: no solo, and the audio tracks' rows back.
    ShowAllTracks,
    /// The bins panel or the timeline was resized, folded or restored
    /// (remembered for this user, `layout_prefs`).
    BinsPanel(SplitChange),
    TimelinePanel(SplitChange),
    /// Play live playblasts over the shots' takes, or not.
    ShowLive(bool),
    /// View > Show (#57): every shot as its render or playblast, or (None)
    /// as the cut. The view's only: the edit keeps its takes.
    Show(Option<TakeKind>),
    /// Look at the shots' live playblasts.
    PollLive,
    /// Look at the change journal.
    PollJournal,
    /// Read the local farm's ready preview frames (#75), and what they were.
    PollPreviews,
    Previews(crate::previews::Ready),
    /// Ask the farm which shots have jobs (`farm_jobs`), and what it said.
    PollFarm,
    FarmJobs(th_pipe::farm_jobs::ShotJobs),
    /// A clip's farm job row: the Farm Manager on it, or its id copied.
    FarmJob(crate::farm_jobs::Act),
    /// A look at the cut's unfinished renders is back (`rendering`).
    Rendering(crate::rendering::Found),
    /// The project's shared journal's new changes (#78).
    SharedJournal(Vec<th_pipe::events::Change>),
    /// Read every shot's takes and the bins again (changes made on another
    /// machine that no journal here announces).
    Refresh,
    Autosave,
    CloseRequested,
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
    /// The clip clicked last: where a Shift+click ranges from (`gestures`).
    anchor: Option<ClipRef>,
    /// The gap picked, named by the clip after it (`gestures`).
    gap: Option<ClipRef>,
    /// The cut as of its last change: what the picks are followed from.
    seen: WorkingEdit,
    /// How many times the cut's clips have changed (`Tracks::cut`).
    clip_changes: u64,
    /// Text typed into a clip field (#60) and not set yet: the clip, the
    /// field, the text.
    clip_draft: Option<(ClipRef, clip_fields::ClipField, String)>,
    /// The clip right-clicked the clip menu is for, and the edit frame
    /// under the click (`clip_menu`).
    menu_clip: Option<(ClipRef, usize)>,
    /// The right-click off the clips the timeline's menu is for: the frame
    /// under it (None on a track's name) and the track of its row.
    menu_empty: Option<(Option<usize>, Option<TrackRef>)>,
    bins: BinData,
    bin_tab: usize,
    bin_pick: Option<usize>,
    /// The bins' thumbnails and grid presses (`editor/bin_panel.rs`).
    bin_state: bin_panel::BinState,
    /// 0 the cut, 1 the board.
    mode: usize,
    board: Option<Board>,
    board_loading: bool,
    /// The project's Houdini sessions, as Desktop last listed them.
    sessions: Vec<Session>,
    sessions_polling: bool,
    /// The Desktop that started this editor; None when nothing links to one.
    desktop: Option<Desktop>,
    /// The Houdini that started this editor (Open Timeline, Play in
    /// Cinedex, Live view); None when nothing did, or it is too old to say.
    houdini: Option<HoudiniLink>,
    dialog: Option<Dialog>,
    /// The last thing said (a refused op, a save), and whether it is an error.
    status: Option<(String, bool)>,
    burnins: bool,
    /// Edit > Sync Shot Ranges (`shot_sync`): on unless turned off, for
    /// this window's session.
    sync_ranges: bool,
    /// The picture is mirrored left to right (`flip`): for this window's
    /// session only, never in the edit, an export or a burn-in.
    flipped: bool,
    /// Play the shots' live playblasts from this machine over their takes.
    show_live: bool,
    /// View > Show (#57): the kind every shot plays as, None for the cut's
    /// takes. Never saved: the edit's rules stay as they are.
    show: Option<TakeKind>,
    /// Live polls so far (every few asks after the renders' husks).
    live_polls: u64,
    /// This machine's change journal, followed from when the editor started.
    journal: th_pipe::events::JournalReader,
    /// How the journal names this project.
    journal_key: String,
    /// The project's shared journal: playblasts and renders from any
    /// machine, the farm's included (#78).
    shared_journal: crate::journal::Shared,
    /// The picture shown was written anew (a live frame): show it again even
    /// though the clips under the playhead are the same.
    reshow: bool,
    /// The clips playback loops over, set by a Live view: followed as the
    /// cut changes (`live_view::Loop`).
    live_loop: Option<crate::live_view::Loop>,
    /// A Live view asked for on the command line (`--view`, `--at`), shown
    /// once the editor starts.
    pending_view: Option<(String, Option<i64>)>,
    started: bool,
    quit: bool,
    /// Quit once the save under way lands.
    quit_after_save: bool,
    wake_slot: Arc<Mutex<Option<Sender<Message>>>>,
    clock: (Instant, usize),
    speaker: Option<Speaker>,
    /// The autosave found at start, offered back.
    recovered: Option<WorkingEdit>,
    /// The edit whose autosave this editor wrote and has not dropped yet:
    /// dropped once the edit is back to what is saved (an undo), so the
    /// next open offers no change that was undone ([`autosave_step`]).
    autosaved: Option<String>,
    /// What the editor did, for the endpoint's `events` calls.
    events: rpc::Events,
    /// The endpoint Live view and agents reach this editor through,
    /// released (its file gone) when the editor quits.
    endpoint: rpc::EndpointSlot,
    /// An agent's `save`, answered when the save is done.
    agent_save: Option<rpc::Call>,
    /// The shots with preview frames ready on this machine (#75).
    previews: crate::previews::Ready,
    /// The shots with jobs on the farm, and whether the farm is being
    /// asked now (one read at a time: a slow `deadlinecommand` must not
    /// pile reads up).
    farm_jobs: th_pipe::farm_jobs::ShotJobs,
    farm_asking: bool,
    /// The unfinished render each of the cut's shots waits for.
    rendering: crate::rendering::Watch,
    /// The panel sizes, remembered for this user between sessions.
    layout: layout_prefs::Layout,
    /// Where they are saved; None keeps them for this session only.
    layout_path: Option<PathBuf>,
    /// The edit frames the tracks' width spans: fitted to the cut when it
    /// opens and on View > Fit Timeline, never by an edit (`timeline_view`).
    timeline_view: timeline_view::TimelineView,
    /// How tall each track's row is, remembered for this user beside the
    /// panel sizes. The view's only: never in the edit.
    track_layout: track_rows::TrackLayout,
    /// The one track the timeline shows (its name was double-clicked),
    /// this session. The view's only: every track still plays.
    solo: Option<TrackRef>,
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
        let speaker = Speaker::open().map_err(|e| eprintln!("cinedex: no sound: {e}")).ok();
        Self::with(paths, ocio, doc, colour, recovered, speaker, Some(layout_prefs::path()))
    }

    /// [`Self::new`] with its sound out and the file its panel sizes are
    /// kept in given: the tests' editor has no speaker, and its prefs are
    /// not this user's. No `layout_path` keeps them for the session only.
    fn with(
        paths: ProjectPaths,
        ocio: String,
        doc: Document,
        colour: Colour,
        recovered: Option<WorkingEdit>,
        speaker: Option<Speaker>,
        layout_path: Option<PathBuf>,
    ) -> Self {
        let (display, view) = colour.default();
        let journal_key = th_pipe::events::project_key(&paths.config);
        let bin_state = bin_panel::BinState::remembered(layout_path.as_deref(), &th_pipe::storage::slashed(&paths.root));
        let shared_journal = crate::journal::Shared::new(&paths.config, journal_key.clone());
        let engine = Engine::start(vec![Content::none()], Timeline::new(Vec::new()), BUFFER_BYTES);
        let wake_slot: Arc<Mutex<Option<Sender<Message>>>> = Arc::new(Mutex::new(None));
        engine.set_notifier(waker(&wake_slot));
        let timeline = engine.timeline();
        let timeline_view = timeline_view::TimelineView::fitted(doc.edit.length(), doc.edit.fps.max(1) as usize);
        let seen = doc.edit.clone();
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
            anchor: None,
            gap: None,
            seen,
            clip_changes: 0,
            clip_draft: None,
            menu_clip: None,
            menu_empty: None,
            bins: BinData::default(),
            bin_tab: 0,
            bin_pick: None,
            bin_state,
            mode: 0,
            board: None,
            board_loading: false,
            sessions: Vec::new(),
            sessions_polling: false,
            desktop: Desktop::from_env(),
            houdini: HoudiniLink::from_env(),
            dialog,
            status: None,
            burnins: true,
            sync_ranges: true,
            flipped: false,
            show_live: true,
            show: None,
            live_polls: 0,
            journal: {
                let mut reader = th_pipe::events::JournalReader::this_machine();
                reader.poll(); // begin at its end
                reader
            },
            shared_journal,
            journal_key,
            reshow: false,
            live_loop: None,
            pending_view: None,
            started: false,
            quit: false,
            quit_after_save: false,
            wake_slot,
            clock: (Instant::now(), 0),
            speaker,
            recovered,
            autosaved: None,
            events: rpc::Events::new(),
            endpoint: rpc::EndpointSlot::default(),
            agent_save: None,
            previews: Default::default(),
            farm_jobs: Default::default(),
            farm_asking: false,
            rendering: Default::default(),
            layout: layout_path.as_deref().map(layout_prefs::load).unwrap_or_default(),
            track_layout: layout_path.as_deref().map(|path| track_rows::load(&track_rows::path(path))).unwrap_or_default(),
            solo: None,
            layout_path,
            timeline_view,
        }
    }

    /// Remember a panel's new size for this user. A file that cannot be
    /// written only loses the size for the next session, so it is said on
    /// stderr, not in the window.
    fn set_layout(&mut self, layout: layout_prefs::Layout) {
        self.layout = layout;
        if let Some(path) = &self.layout_path
            && let Err(e) = layout_prefs::save(path, layout)
        {
            eprintln!("cinedex: the panel sizes were not saved to {}: {e}", path.display());
        }
    }

    /// The timeline's height: the user's, else enough for the clip fields
    /// and every track.
    fn timeline_height(&self) -> f32 {
        self.layout.timeline_height.unwrap_or_else(|| {
            let rows: f32 = self.spare_track().iter().chain(&self.shown_tracks()).map(|&t| self.track_layout.height(t)).sum();
            panels::fitted_to_rows(rows + if self.tracks_note().is_some() { tracks::NOTE } else { 0.0 })
        })
    }

    /// Which tracks have a row: all, the soloed one, or all but the audio.
    fn focus(&self) -> track_rows::Focus {
        track_rows::Focus { solo: self.solo, audio_hidden: self.track_layout.audio_hidden }
    }

    /// The tracks the timeline shows a row for, top first.
    fn shown_tracks(&self) -> Vec<TrackRef> {
        track_rows::shown(self.doc.edit.video.len(), self.doc.edit.audio.len(), self.focus())
    }

    /// The spare layer's track: one past the top video track, shown as an
    /// empty row while the top one is used (`track_rows::spare`). Never a
    /// track of the edit.
    fn spare_track(&self) -> Option<TrackRef> {
        let edit = &self.doc.edit;
        track_rows::spare(edit.video.len(), track_rows::top_used(edit), self.focus(), !self.doc.read_only(), edit.audio.len())
    }

    /// What the rows leave out, for the line under them.
    fn tracks_note(&self) -> Option<String> {
        track_rows::note(self.doc.edit.video.len(), self.doc.edit.audio.len(), self.focus())
    }

    /// The rows changed (a solo, the audio hidden): a clip without a row is
    /// no longer selected, so Delete never takes out what is not seen.
    fn refocused(&mut self) {
        let shown = self.shown_tracks();
        self.selection.retain(|c| shown.contains(&c.track));
    }

    /// Remember a track's row height for this user, as [`Self::set_layout`]
    /// does the panels'.
    fn set_row_height(&mut self, track: TrackRef, height: f32) {
        if (self.track_layout.height(track) - tracks::row_height(height)).abs() < 0.5 {
            return;
        }
        self.track_layout.set_height(track, height);
        self.save_track_layout();
    }

    fn save_track_layout(&self) {
        if let Some(path) = self.layout_path.as_deref().map(track_rows::path)
            && let Err(e) = track_rows::save(&path, &self.track_layout)
        {
            eprintln!("cinedex: the tracks' layout was not saved to {}: {e}", path.display());
        }
    }

    fn fps(&self) -> u32 {
        self.doc.edit.fps.max(1)
    }

    /// The tracks span the cut and a second past it again.
    fn fit_timeline(&mut self) {
        self.timeline_view = timeline_view::TimelineView::fitted(self.doc.edit.length(), self.fps() as usize);
    }

    /// Positions the playhead can stand on: the edit, and a second past it.
    fn length(&self) -> usize {
        self.doc.edit.length().max(0) as usize + self.fps() as usize
    }

    /// The cut's end: one past its last frame, where an Insert appends.
    /// Go to End and a click past the last clip park here (#25: they landed
    /// on the last frame, so an Insert split the last clip).
    fn end(&self) -> usize {
        self.doc.edit.length().max(0) as usize
    }

    /// The AOV and stack layers entry `index` shows once resolved to
    /// `media`: what it showed before, where the new media still has it
    /// (`layer_view`), else the media's own view and every layer.
    fn kept_choices(&self, index: usize, media: Option<&Media>) -> (ExrView, u64) {
        let Some(media) = media else { return (ExrView::default(), 0) };
        let (before, view, hidden) = match &self.entries[index].state {
            EntryState::Ready { resolved, view, hidden, .. } => (resolved.media.as_deref(), Some(view), *hidden),
            _ => (None, None, 0),
        };
        let names: Vec<String> = before.map(|m| m.stack_layers().into_iter().map(str::to_owned).collect()).unwrap_or_default();
        (layer_view::kept_view(view, media.layers(), media.default_view()), layer_view::kept_hidden(&names, hidden, &media.stack_layers()))
    }

    /// What the engine plays for entry `index`.
    fn content_of(&self, index: usize) -> Content {
        match &self.entries[index].state {
            EntryState::Ready { resolved: Resolved { media: Some(media), .. }, view, hidden, .. } => {
                Content { media: Some(Arc::clone(media)), view: view.clone(), hidden: *hidden, wants_proxy: true }
            }
            _ => Content::none(),
        }
    }

    /// The entry whose take is on screen: the top clip's under the playhead.
    fn viewed_entry(&self) -> Option<usize> {
        self.engine.layers(self.position).last()?.key.source.checked_sub(1)
    }

    /// Change what the viewer shows of the take on screen (the AOV dropdown,
    /// the layer toggles), and play it so. View only: the edit is untouched.
    fn change_view(&mut self, change: impl FnOnce(&Media, &mut ExrView, &mut u64)) {
        let Some(index) = self.viewed_entry() else { return };
        let EntryState::Ready { resolved: Resolved { media: Some(media), .. }, view, hidden, .. } = &mut self.entries[index].state else {
            return;
        };
        change(media, view, hidden);
        let content = self.content_of(index);
        let timeline = self.engine_timeline();
        self.timeline = self.engine.replace_source(index + 1, content, timeline);
        self.engine.set_playhead(self.position);
        self.shown = None;
        self.refresh_shown();
    }

    /// The edit changed: resolve its new sources, place its clips and sound.
    fn changed(&mut self) -> Command<Message> {
        self.events.push(
            "edit_changed",
            json!({"length": self.doc.edit.length(), "dirty": self.doc.dirty(), "can_undo": self.doc.can_undo(), "can_redo": self.doc.can_redo()}),
        );
        if self.live_loop.as_mut().is_some_and(|l| !l.follow(&self.doc.edit)) {
            self.live_loop = None;
        }
        // The tracks keep their scale: wider only if the cut outgrew it.
        self.timeline_view = self.timeline_view.changed(self.doc.edit.length(), self.fps() as usize);
        let command = self.resolve_new();
        self.timeline = self.engine.set_timeline(self.engine_timeline());
        self.engine.set_playhead(self.position);
        self.follow_picks();
        self.solo = track_rows::kept_solo(self.solo, self.doc.edit.video.len(), self.doc.edit.audio.len());
        self.place_sounds();
        self.shown = None;
        self.refresh_shown();
        command
    }

    /// The edit changed by an edit, an undo or a redo: the shots' frames
    /// follow the master's trims (`shot_sync`), then [`Self::changed`].
    fn changed_by_edit(&mut self) -> Command<Message> {
        let synced = self.sync_shot_ranges();
        Command::batch([self.changed(), synced])
    }

    /// Apply `ops`, one user action, as one undo step: all of them or,
    /// when one is refused, none (the status says why).
    fn edit(&mut self, ops: &[Op<Source>]) -> Command<Message> {
        if ops.is_empty() {
            return Command::none();
        }
        let shots = self.syncing();
        let result = self.paths.open().map_err(Refused).and_then(|project| {
            let sources = EditSources::new(&project, self.fps());
            self.doc.apply(ops, &shot_sync::Open { inner: &sources, shots })
        });
        match result {
            Ok(()) => {
                self.status = None;
                self.changed_by_edit()
            }
            Err(e) => {
                self.status = Some((format!("Not done: {e}"), true));
                Command::none()
            }
        }
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
    fn take_step(&mut self, axis: take_menu::TakeAxis, delta: i8) -> Command<Message> {
        let Some(clip) = self.targets().into_iter().find(|&c| matches!(clip_source(&self.doc.edit, c), Some(Source::Shot { .. }))) else {
            self.status = Some(("Select a shot's clip, or put the playhead over one".into(), true));
            return Command::none();
        };
        let step = clip_source(&self.doc.edit, clip).and_then(|source| self.entry_of(source)).and_then(|i| {
            let resolved = self.entries[i].resolved()?;
            let takes = &resolved.takes;
            let of_kind = |kind| takes.iter().rposition(|t| t.kind() == kind);
            take_menu::step(takes, resolved.take?, axis, delta, of_kind)
        });
        match step {
            Some((take, pin)) => self.change_take(clip, Some(take), pin),
            None => Command::none(),
        }
    }

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

    /// The take menu's Layer row for `menu`'s clip, applied to it and the
    /// other selected shot clips: each rule's layer set (None, best
    /// available), the rest of it kept.
    fn change_layer(&mut self, menu: ClipRef, layer: Option<String>) -> Command<Message> {
        let mut targets: Vec<ClipRef> = if self.selection.contains(&menu) { self.selection.iter().copied().collect() } else { vec![menu] };
        targets.retain(|c| matches!(clip_source(&self.doc.edit, *c), Some(Source::Shot { .. })));
        let ops: Vec<Op<Source>> = targets
            .into_iter()
            .filter_map(|clip| {
                let Some(Source::Shot { shot, take }) = clip_source(&self.doc.edit, clip).cloned() else { return None };
                let rule = take_menu::with_layer(&take, layer.clone());
                (rule != take).then_some(Op::SetSource { clip, source: Source::Shot { shot, take: rule } })
            })
            .collect();
        self.edit(&ops)
    }

    /// [`Self::edit`], then, when it was done, the playhead to `end` (the
    /// end of what an Insert or Overlay put, so the next one appends).
    fn edit_then_park(&mut self, ops: &[Op<Source>], end: Option<EditFrame>) -> Command<Message> {
        let command = self.edit(ops);
        let done = !ops.is_empty() && self.status.is_none();
        if let Some(end) = end.filter(|_| done) {
            self.move_to(end.max(0) as usize);
        }
        command
    }

    fn rows(&self) -> Vec<RowView> {
        let edit = &self.doc.edit;
        // The spare layer first: an empty row over the top video track.
        let spare = self.spare_track().map(|track| RowView {
            track,
            name: track_rows::fresh_name('V', &edit.video.iter().map(|t| t.name.as_str()).collect::<Vec<_>>()),
            clips: Vec::new(),
            height: self.track_layout.height(track),
            solo: false,
            spare: true,
            color: None,
        });
        let rows = self.shown_tracks().into_iter().map(|track| {
            let views = |source: &Source, at, duration, source_in, fade_in, fade_out, index| {
                let entry = self.entry_of(source).map(|i| &self.entries[i]);
                // While the master's trims set its shots' frames, a shot
                // clip trims past them (`shot_sync`).
                let open = self.syncing() && matches!(source, Source::Shot { .. });
                let available = entry.and_then(Entry::resolved).and_then(|r| r.available).filter(|_| !open);
                ClipView {
                    at,
                    duration,
                    fade_in,
                    fade_out,
                    name: match entry.and_then(Entry::layer_note) {
                        Some(note) => format!("{} · {note}", source_name(source)),
                        None => source_name(source),
                    },
                    detail: crate::rendering::detail(&entry.map(Entry::detail).unwrap_or_default(), self.rendering.of_source(source)),
                    thumbnail: self
                        .entry_of(source)
                        .and_then(|i| self.proxies.get(&(i + 1)))
                        .and_then(|p| p.1.clone())
                        .filter(|_| track.kind == TrackKind::Video),
                    selected: self.selection.contains(&ClipRef { track, index }),
                    missing: entry.is_some_and(Entry::missing),
                    department: entry.and_then(Entry::department),
                    preview: self.previews.clip_badge(source, entry.and_then(Entry::resolved).map(|r| r.takes.as_slice())),
                    farm: self.farm_job_of(source).map(|job| job.status),
                    room: available.and_then(|a| tracks::room(a, source_in, duration)),
                    wave: self.clip_wave(track, index),
                }
            };
            let (name, clips) = match track.kind {
                TrackKind::Video => {
                    let t = &edit.video[track.index];
                    (
                        t.name.clone(),
                        t.clips
                            .iter()
                            .enumerate()
                            .map(|(i, c)| views(&c.source, c.at, c.duration, c.source_in, c.fade_in, c.fade_out, i))
                            .collect(),
                    )
                }
                TrackKind::Audio => {
                    let t = &edit.audio[track.index];
                    (
                        t.name.clone(),
                        t.clips
                            .iter()
                            .enumerate()
                            .map(|(i, c)| views(&c.source, c.at, c.duration, c.source_in, c.fade_in, c.fade_out, i))
                            .collect(),
                    )
                }
            };
            let color = edit.track_color(track).and_then(look::track);
            RowView { track, name, clips, height: self.track_layout.height(track), solo: self.solo == Some(track), spare: false, color }
        });
        spare.into_iter().chain(rows).collect()
    }

    /// The tracks as the timeline shows them, each gesture on them wired
    /// to the message it sends.
    fn tracks_program(&self) -> Tracks<Message> {
        Tracks {
            rows: self.rows(),
            markers: self.doc.edit.markers.iter().map(|m| m.at).collect(),
            // Not the cut's length: the span only an explicit fit sets, so
            // a drag, trim or retime never rescales the clips under it.
            total: self.timeline_view.shown(),
            playhead: self.position,
            buffered: tracks::ranges(&self.engine.buffered_positions()),
            rendering: self.render_strips(),
            on_seek: Message::Seek,
            on_select: Message::Select,
            on_drag: || Message::Drag,
            on_op: Message::Edit,
            on_ops: Message::Edits,
            on_menu: Message::ClipMenu,
            on_row_height: Message::RowHeight,
            on_solo: Message::Solo,
            on_context: Message::TimelineMenu,
            on_gesture: Message::Tracks,
            first: self.timeline_view.first(),
            on_view: Message::TimelineView,
            cut: self.clip_changes,
            gap: self.gap,
            note: self.tracks_note(),
            editable: true,
        }
    }

    /// The strip under the tracks (`tracks::overview`).
    fn overview(&self) -> tracks::overview::Overview<Message> {
        let edit = &self.doc.edit;
        tracks::overview::Overview {
            span: self.timeline_view.span(),
            first: self.timeline_view.first(),
            shown: self.timeline_view.shown(),
            clips: edit.tracks().flat_map(|track| edit.spans(track)).collect(),
            playhead: self.position,
            least: timeline_view::SHOWN_MIN,
            on_scroll: Message::ScrollTo,
            on_frames: Message::ShowFrames,
        }
    }

    fn tracks_view(&self) -> Element<Message> {
        let program = self.tracks_program();
        let height = program.height().max(tracks::RULER + tracks::ROW);
        let view = tracks::track_view(Id::new("edit tracks"), program, height);
        // While something is carried from the bins, the tracks say what
        // the pointer is over and show where it would land (`bin_panel`).
        let view = self.bin_drop_zone(view);
        // A timeline dragged lower than its tracks scrolls them.
        let view = scrollable(Id::new("tracks scroll"), view).width(Length::Fill).height(Length::Fill);
        let mut menu = context_menu(Id::new("clip menu"), view);
        if let Some((clip, _)) = self.menu_clip
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
                if let Source::Shot { take: rule, .. } = source
                    && let Some((label, rows)) = take_menu::layer_submenu(&resolved.takes, shown, rule)
                {
                    let rows = rows
                        .into_iter()
                        .fold(MenuList::new(), |rows, (layer, label)| rows.item(label, move || Message::TakeLayer(clip, layer.clone())));
                    menu = menu.submenu(label, rows);
                }
            }
            if resolved.live.playing().is_some()
                && let Some(index) = self.entry_of(source)
            {
                menu = menu.item("Keep as playblast", move || Message::KeepLive(index));
            }
            if let Some(folder) = resolved.take.and_then(|t| resolved.takes.get(t)).and_then(|t| crate::reveal::media_folder(&t.media)) {
                menu = menu.item("Reveal folder", move || Message::Reveal(folder.clone()));
            }
            if let Source::Shot { shot, .. } = source
                && let Some(board) = &self.board
            {
                let workfiles = board.workfiles_of(shot);
                if !workfiles.is_empty() {
                    let rows = workfiles.into_iter().fold(MenuList::new(), |rows, (department, workfile)| {
                        let (uri, department) = (shot.clone(), department.to_owned());
                        rows.item(format!("{department}  {}", workfile.version), move || {
                            Message::OpenWorkfile(uri.clone(), department.clone())
                        })
                    });
                    menu = menu.submenu("Open in Houdini", rows);
                }
            }
            if let Source::Shot { shot, .. } = source {
                for (label, message) in Self::clip_menu_more(shot) {
                    menu = menu.item(label, move || message.clone());
                }
            }
            for (label, message) in self.farm_job_rows(source) {
                menu = menu.item(label, move || message.clone());
            }
            // The view's, not the clip's: the picture mirrored (`flip`).
            let (label, flip) = self.flip_row();
            menu = menu.item(label, move || flip.clone());
        }
        // Any clip's: Convert to Shot… and the swap back (`convert_shot`),
        // then what the edit does to it (`clip_menu`).
        if let Some(state) = self.menu_clip.and_then(|(clip, frame)| self.clip_menu_state(clip, frame)) {
            for (label, message) in clip_menu::clip_rows(&self.doc.edit, &state) {
                menu = menu.item(label, move || message.clone());
            }
        }
        // A right-click off the clips: what the timeline does there.
        if let Some((frame, track)) = self.menu_empty {
            let state = self.timeline_menu_state(frame, track);
            for (label, message) in timeline_menu::entries(&state) {
                menu = menu.item(label, move || message.clone());
            }
            if let Some((label, rows)) = timeline_menu::colours(&state) {
                let rows = rows.into_iter().fold(MenuList::new(), |rows, (label, message)| rows.item(label, move || message.clone()));
                menu = menu.submenu(label, rows);
            }
        }
        // Under the tracks: the whole timeline in little, to scroll by.
        column()
            .push(Roomy::menus(menu))
            .push(tracks::overview::overview_view(Id::new("tracks overview"), self.overview()))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    /// The picture as the video view draws it: the frame shown, at the
    /// exposure, mirrored while flipped (`flip`).
    fn video(&self) -> Video {
        Video { picture: self.shown.as_ref().map(|s| s.picture.clone()), exposure: self.display.exposure, flipped: self.flipped }
    }

    /// The right-click menus' Flip Horizontally row (on the picture and on
    /// a shot's clip): its words, ticked while flipped, and what it sends.
    fn flip_row(&self) -> (&'static str, Message) {
        (flip::context_label(self.flipped), Message::Flip(!self.flipped))
    }

    /// The window's one row above the panels: on Windows and Linux the
    /// menus first (`menu_row`; on macOS they are the global menu bar),
    /// then Cut|Board, then, at the right, the master edit tick and the
    /// chips of what the view is doing. Live and Burn-ins are on the player
    /// (`player_header`) and the timer on the timeline (`timeline_header`),
    /// beside what they affect, so checking a timing is not a look across
    /// the window to its corner (Remy, 2026-11-21). The edit's name (its
    /// version, `*` while it has unsaved changes) is the window's title
    /// (`windows`), not said here again.
    fn toolbar(&self) -> Element<Message> {
        let modes = segmented(Id::new("modes"), self.mode).push("Cut").push("Board").on_select(Message::Mode).focusable(false);
        let mut tools = row().spacing(24.0).align_y(Vertical::Center);
        let padding = if menu_row::in_window() {
            tools = tools.push(self.menu_row());
            // The bar's titles have room of their own around their words.
            Padding { top: 4.0, right: 14.0, bottom: 4.0, left: 4.0 }
        } else {
            Padding::from([8.0, 14.0])
        };
        let mut tools = tools.push(modes).push(space(Length::Fill, Length::Shrink));
        if let Origin::Working { name, .. } = &self.doc.origin {
            let is_master = self.bins.master.as_deref() == Some(name.as_str());
            let master = row()
                .spacing(6.0)
                .align_y(Vertical::Center)
                .push(look::tick_box(Id::new("master"), is_master, Message::SetMaster).focusable(false))
                .push(text("Master edit").font_size(13.0).color(look::quiet()));
            tools = tools.push(tooltip(Id::new("master tip"), master, "Publishing the master edit sets the shots' frame ranges"));
        }
        if let Some((start, end)) = self.loop_range() {
            let span = format!(
                "Loop {}–{}  ✕",
                editing::timecode(start as EditFrame, &self.doc.edit),
                editing::timecode(end.saturating_sub(1) as EditFrame, &self.doc.edit)
            );
            let chip = look::button(Id::new("loop"), text(span).font_size(12.0))
                .padding(Padding::from([3.0, 8.0]))
                .focusable(false)
                .on_press(|| Message::ClearLoop);
            tools =
                tools.push(tooltip(Id::new("loop tip"), chip, "Live view loops the shots around the one rendering; click to stop looping"));
        }
        if let Some(kind) = self.show {
            let chip = look::button(Id::new("show"), text(format!("Showing {}  \u{2715}", show_word(Some(kind)))).font_size(12.0))
                .padding(Padding::from([3.0, 8.0]))
                .focusable(false)
                .on_press(|| Message::Show(None));
            tools = tools.push(tooltip(
                Id::new("show tip"),
                chip,
                "View > Show plays every shot as this, for viewing only: saving and publishing keep the cut's takes. Click to show the cut (V steps through)",
            ));
        }
        if self.flipped {
            tools = tools.push(flip::chip(Message::Flip(false)));
        }
        // With the timeline folded away the timer has no panel of its own to
        // sit on: it comes back here, at the row's end, where it was.
        if self.layout.timeline_collapsed {
            tools = tools.push(self.timer());
        }
        container(tools).padding(padding).width(Length::Fill).background(look::panel()).into()
    }

    /// The timer: the playhead's timecode, the one large thing, in the
    /// accent, because it is where the playhead is now. It heads the
    /// timeline, left of the clip fields and over the tracks' names, beside
    /// the playhead it reads (`timeline_header`); in the toolbar only while
    /// the timeline is folded away.
    fn timer(&self) -> Element<Message> {
        text(editing::timecode(self.position as EditFrame, &self.doc.edit)).font_size(TIMER_SIZE).color(look::now()).into()
    }

    /// The timeline's top row: the timer, then the clip fields, one
    /// [`CLIP_FIELDS_HEIGHT`](panels::CLIP_FIELDS_HEIGHT) tall. The timer
    /// comes first so that a narrow window cuts off the end of the fields,
    /// never the timer.
    fn timeline_header(&self) -> Element<Message> {
        let timer = container(self.timer())
            .padding(Padding { top: 0.0, right: 0.0, bottom: 0.0, left: 14.0 })
            .height(Length::fixed(panels::CLIP_FIELDS_HEIGHT))
            .align_y(Vertical::Center)
            .background(look::panel());
        row().push(timer).push(self.clip_fields()).width(Length::Fill).into()
    }

    /// The player's top strip, over the picture: Live and Burn-ins, the two
    /// switches of what the picture shows, at its right. Only the two, so
    /// they fit the narrowest the viewer is let be (`panels::VIEWER_MIN`).
    fn player_header(&self) -> Element<Message> {
        let switch = |id: &'static str, on: bool, send: fn(bool) -> Message, words: &'static str| {
            row()
                .spacing(6.0)
                .align_y(Vertical::Center)
                .push(look::tick_box(Id::new(id), on, send).focusable(false))
                .push(text(words).font_size(13.0).color(look::quiet()))
        };
        let live = switch("live", self.show_live, Message::ShowLive, "Live");
        let burnins = switch("burnins", self.burnins, Message::Burnins, "Burn-ins");
        let switches = row()
            .spacing(24.0)
            .align_y(Vertical::Center)
            .push(space(Length::Fill, Length::Shrink))
            .push(tooltip(Id::new("live tip"), live, "Live playblasts from this machine play over the shots' takes as they render"))
            .push(tooltip(Id::new("burnins tip"), burnins, "B"));
        container(switches)
            .padding(Padding::from([0.0, 14.0]))
            .width(Length::Fill)
            .height(Length::fixed(PLAYER_HEADER_HEIGHT))
            .align_y(Vertical::Center)
            .background(look::panel())
            .into()
    }

    /// The board: a row per shot under its sequence's name, a column per
    /// shot department, each cell the department's latest workfile. A cell
    /// opens its workfile in Houdini.
    fn board_view(&self) -> Element<Message> {
        const NAME: f32 = 150.0;
        const CELL: f32 = 138.0;
        let quiet = |w: String| text(w).font_size(12.0).color(look::quiet());
        let Some(board) = &self.board else {
            let words = if self.board_loading { "Reading the shots' workfiles…" } else { "The board has not been read" };
            return container(quiet(words.into()))
                .padding(Padding::from([14.0, 14.0]))
                .width(Length::Fill)
                .height(Length::Fill)
                .background(look::panel())
                .into();
        };
        let now = chrono::Local::now().naive_local();
        let mut header = row().spacing(4.0).push(space(Length::fixed(NAME), Length::Shrink));
        for department in &board.departments {
            header = header.push(container(quiet(department.clone())).width(Length::fixed(CELL)).padding(Padding::from([0.0, 8.0])));
        }
        let mut grid = column().spacing(4.0);
        for (index, shot) in board.shots.iter().enumerate() {
            if index == 0 || board.shots[index - 1].sequence != shot.sequence {
                grid =
                    grid.push(container(quiet(shot.sequence.clone())).padding(Padding { top: 12.0, right: 0.0, bottom: 2.0, left: 0.0 }));
            }
            let mut line = row()
                .spacing(4.0)
                .align_y(Vertical::Center)
                .push(container(text(shot.name.clone()).font_size(13.0).color(look::text())).width(Length::fixed(NAME)));
            for (department, cell) in board.departments.iter().zip(&shot.cells) {
                let id = Id::new(format!("board {} {department}", shot.uri));
                let cell: Element<Message> = match cell {
                    Some(workfile) => {
                        let (detail, open) = board::detail(workfile, &self.sessions, now);
                        let words = column()
                            .spacing(1.0)
                            .push(text(workfile.version.clone()).font_size(13.0).color(look::text()))
                            .push(text(detail).font_size(11.0).color(if open { look::now() } else { look::quiet() }));
                        let (uri, department) = (shot.uri.clone(), department.clone());
                        look::button(id, words)
                            .padding(Padding::from([4.0, 8.0]))
                            .width(Length::fixed(CELL))
                            .on_press(move || Message::OpenWorkfile(uri.clone(), department.clone()))
                            .focusable(false)
                            .into()
                    }
                    None => container(text("–").font_size(13.0).color(look::quiet()))
                        .padding(Padding::from([4.0, 8.0]))
                        .width(Length::fixed(CELL))
                        .into(),
                };
                line = line.push(cell);
            }
            grid = grid.push(line);
        }
        let mut page = column().spacing(8.0).padding(Padding::from([10.0, 14.0]));
        if self.desktop.is_none() && self.houdini.is_none() {
            page = page.push(text(NO_DESKTOP).font_size(12.0).color(look::caution()));
        }
        if board.shots.is_empty() {
            page = page.push(quiet("The project has no shots yet".into()));
        }
        let refresh = look::button(Id::new("board refresh"), text("Refresh").font_size(12.0))
            .padding(Padding::from([3.0, 8.0]))
            .on_press(|| Message::Mode(1))
            .focusable(false);
        let foot = row().spacing(12.0).align_y(Vertical::Center).push(refresh).push(quiet(if self.board_loading {
            "Reading…".into()
        } else {
            "A cell opens the workfile in Houdini, or brings the Houdini that has it open to the front".into()
        }));
        page = page.push(header).push(scrollable(Id::new("board"), grid).width(Length::Fill).height(Length::Fill)).push(foot);
        container(page.height(Length::Fill)).background(look::panel()).width(Length::Fill).height(Length::Fill).into()
    }

    /// The bar under the picture (`display::display_bar`), led by what of
    /// the take on screen shows.
    fn display_controls(&self) -> Element<Message> {
        let label = |w: String| text(w).font_size(13.0).color(look::quiet());
        let mut controls = row().spacing(8.0).align_y(Vertical::Center);
        // What of the take on screen shows (#58), view only: a render's AOV,
        // a stack's layers.
        if let Some(index) = self.viewed_entry()
            && let Some(Entry { state: EntryState::Ready { resolved: Resolved { media: Some(media), .. }, view, hidden, .. }, .. }) =
                self.entries.get(index)
        {
            let aovs = media.layers();
            if !aovs.is_empty() {
                let selected = layer_view::aov_index(aovs, view).unwrap_or(0);
                controls = controls.push(label("AOV".into())).push(
                    combo_box(Id::new("aov"), layer_view::aov_names(aovs))
                        .selected(selected)
                        .on_select(Message::Aov)
                        .width(Length::fixed(150.0))
                        .focusable(false),
                );
            }
            let stack = media.stack_layers();
            if !stack.is_empty() {
                controls = controls.push(label(layer_view::layers_label(stack.len(), *hidden)));
                let many = stack.len() > layer_view::LAYERS_INLINE;
                let mut boxes = row().spacing(8.0).align_y(Vertical::Center);
                for (i, name) in stack.into_iter().enumerate().take(64) {
                    let shown = !crate::engine::is_hidden(*hidden, i);
                    boxes = boxes
                        .push(look::tick_box(Id::new(format!("layer {i}")), shown, move |on| Message::ShowLayer(i, on)).focusable(false))
                        .push(text(name.to_owned()).font_size(13.0).color(look::quiet()));
                }
                // Many layers scroll sideways in a strip of their own, so the
                // display and view stay where they are (#58).
                controls = if many {
                    controls.push(
                        scrollable(Id::new("layers"), boxes)
                            .direction(ScrollDirection::Horizontal)
                            .width(Length::fixed(LAYER_STRIP_WIDTH))
                            .height(Length::fixed(30.0)),
                    )
                } else {
                    controls.push(boxes)
                };
            }
            if !aovs.is_empty() || !media.stack_layers().is_empty() {
                controls = controls.push(space(Length::fixed(16.0), Length::Shrink));
            }
        }
        crate::display::display_bar(&self.display, controls, Message::Display, Message::View, Message::Exposure)
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

    /// Put the planned shot `sequence/name` of `frames` frames at the
    /// playhead, inserted; or, with `place` (an Alt+drag's frames), laid
    /// there (`range_shot`). With `now` the shot is made first.
    fn new_shot(&mut self, sequence: &str, name: &str, frames: &str, place: Option<range_shot::Place>, now: bool) -> Command<Message> {
        // A flat project's every new shot goes into its implicit sequence,
        // whatever the dialog was given (`flat_entities`).
        let flat = self.bins.flat_shots.clone();
        let (sequence, name) = (flat.as_deref().unwrap_or(sequence).trim(), name.trim());
        for (what, value) in [("sequence", sequence), ("shot", name)] {
            if let Some(problem) = th_pipe::naming::entity_name_problem(value) {
                self.status = Some((format!("{value:?}: a {what}'s name {problem}"), true));
                return Command::none();
            }
        }
        let Some(frames) = crate::number_field::whole(frames, None).ok().filter(|&f| f > 0) else {
            self.status = Some((format!("{frames:?}: the frames are a whole number above 0"), true));
            return Command::none();
        };
        let uri = format!("entity:/shots/{sequence}/{name}");
        if self.bins.shots.iter().any(|s| s.uri == uri) {
            self.status = Some((format!("{sequence}/{name} exists: pick it in the Shots bin"), true));
            return Command::none();
        }
        // A flat list shows every shot by name alone: no two may share one
        // in any case, whichever sequence holds them, as the Asset
        // Browser's New Shot refuses (`creation_check`). The planned shots
        // the edit names count too: the master's publish makes them.
        if flat.is_some() {
            let paths: Vec<Vec<String>> = self.taken_shot_names().into_iter().map(|(s, n)| vec!["shots".to_owned(), s, n]).collect();
            if let Ok(Some(holder)) = th_pipe::flat_entities::name_taken("shots", name, &paths) {
                self.status = Some((format!("A shot {name:?} exists (in {holder}, by another case or the same): pick another name"), true));
                return Command::none();
            }
        }
        let first = self.bins.shot_start(sequence);
        let Some((first, last)) = th_pipe::shot_sync::new_shot_frames(first, frames) else { return Command::none() };
        let planned = BinShot { uri, sequence: sequence.into(), name: name.into(), first, last };
        if now && let Err(e) = self.create_shot_now(&planned) {
            // The dialog stays, to change the name or untick it.
            self.status = Some((format!("{sequence}/{name} was not created: {e}"), true));
            return Command::none();
        }
        self.dialog = None;
        if let Some(place) = place {
            let refresh = if now { self.refresh_bins() } else { Command::none() };
            return Command::batch([self.put_over(place, planned.clip(place.at)), refresh]);
        }
        let track = self.targets().first().map(|c| c.track).filter(|t| t.kind == TrackKind::Video).map_or(0, |t| t.index);
        let clip = planned.clip(self.position as EditFrame);
        let end = editing::put_end(Some(&clip), None);
        let ops = editing::put(&self.doc.edit, Some(clip), None, track, false);
        let refresh = if now { self.refresh_bins() } else { Command::none() };
        Command::batch([self.edit_then_park(&ops, end), refresh])
    }

    /// Every shot's (sequence, name): the project's (with a frame range or
    /// not: one without is in no bin but holds its name), and the planned
    /// ones the edit names, for the name New shot… offers.
    fn taken_shot_names(&self) -> Vec<(String, String)> {
        let mut taken: Vec<(String, String)> = self.bins.shots.iter().map(|s| (s.sequence.clone(), s.name.clone())).collect();
        taken.extend(self.bins.shot_paths.iter().filter_map(|p| match p.as_slice() {
            [_, sequence, name] => Some((sequence.clone(), name.clone())),
            _ => None,
        }));
        for clip in self.doc.edit.video.iter().flat_map(|t| &t.clips) {
            if let Source::Shot { shot, .. } = &clip.source
                && let Some(planned) = editing::shot_of_uri(shot)
            {
                taken.push(planned);
            }
        }
        taken
    }

    /// Show `shot` as Houdini's Live view asks (see `live_view`): its first
    /// clip, looping from the clip before to the clip after, the playhead on
    /// shot frame `frame`, live playblasts on, playing, and the window in
    /// front without the keyboard. With `master`, refused unless this window
    /// is on the project's master edit (another player, or a new one, is).
    fn view_shot(&mut self, shot: &str, frame: Option<i64>, master: bool) -> (Result<Value, Refusal>, Command<Message>) {
        if master {
            let name = match &self.doc.origin {
                Origin::Working { name, .. } | Origin::Published { name, .. } => Some(name.as_str()),
                Origin::Untitled => None,
            };
            if let Some(refusal) = master_refusal(name, self.paths.open().and_then(|p| th_pipe::master::master_edit(&p))) {
                return (Err(refusal), Command::none());
            }
        }
        let Some(view) = crate::live_view::shot_view(&self.doc.edit, shot, frame) else {
            return (Err(Refusal::new(rpc::REFUSED, format!("the edit has no clip of {shot}"))), Command::none());
        };
        self.mode = 0;
        self.live_loop = Some(view.looping);
        self.move_to(view.playhead);
        self.set_playing(true);
        // On Wayland only the compositor can bring the window forward.
        let attention = if crate::live_view::raise_without_focus() {
            Command::request_attention(window::Id::MAIN, primer::Attention::Informational)
        } else {
            Command::none()
        };
        let command = if self.show_live {
            attention
        } else {
            self.show_live = true;
            Command::batch([self.re_resolve_shots(), attention])
        };
        self.status = Some((format!("Live view: {shot}"), false));
        self.events.push("view", json!({"shot": shot, "start": view.start, "end": view.end, "playhead": view.playhead}));
        (Ok(json!({"start": view.start, "end": view.end, "playhead": view.playhead})), command)
    }

    /// A Live view asked for on the command line, shown when it starts.
    pub fn with_view(mut self, shot: String, frame: Option<i64>) -> Self {
        self.pending_view = Some((shot, frame));
        self
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

impl Editor {
    /// Quit: the window closes, and the endpoint goes with it now (its
    /// file too), not whenever the process happens to end.
    fn leave(&mut self) {
        self.quit = true;
        self.endpoint.release();
        // Undone back to what is saved since the last autosave: drop it.
        if !self.doc.dirty() {
            self.autosave();
        }
    }
}

impl Application for Editor {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        if self.quit {
            return Vec::new();
        }
        let title = format!("{} - Cinedex", self.doc.title());
        vec![window::Window::new(window::Id::MAIN).title(title).app_id(crate::APP_ID).size(Size::new(1440.0, 940.0)).theme(theme())]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        let command = self.handle(message);
        // Primer ends a run only on Command::quit (since 0.16): emptying
        // windows() closed the window and left the process running.
        if self.quit { Command::batch([command, Command::quit()]) } else { command }
    }

    fn on_key(&self, _window: window::Id, key: Key, modifiers: Modifiers) -> Option<Message> {
        if let Some(cancel) = self.bin_escape(&key) {
            return Some(cancel);
        }
        menu_bar::key_message(&self.menu_state(), key, modifiers)
    }

    /// The platform's menu bar (macOS's), or none where the toolbar's row
    /// draws the menus itself (`menu_row`): given one there, Primer would
    /// stack a bar of its own above the view.
    fn menu(&self) -> Option<primer::menu::Menu<Message>> {
        (!menu_row::in_window()).then(|| self.menu_bar())
    }

    fn on_close_requested(&self, _window: window::Id) -> Option<Message> {
        Some(Message::CloseRequested)
    }

    fn subscription(&self) -> Subscription<Message> {
        let slot = Arc::clone(&self.wake_slot);
        let engine_events = subscription::channel(subscription::hash_id(&"cinedex editor engine"), 4, move |sender| async move {
            let mut sender = sender;
            let _ = sender.try_send(Message::Start);
            *slot.lock().unwrap() = Some(sender);
            futures::future::pending::<()>().await;
        });
        // The endpoint Houdini's Live view reaches this player through,
        // for as long as the editor runs: the slot holds it, and quitting
        // (`leave`) releases it, its file with it. This task never ends,
        // so dropping the server here would never happen.
        let players = th_pipe::live::players_dir(&th_pipe::live::this_live_root(), &th_pipe::storage::slashed(&self.paths.root));
        let events = self.events.clone();
        let slot = self.endpoint.clone();
        let rpc = subscription::channel(subscription::hash_id(&"cinedex rpc"), 8, move |sender| async move {
            let sender = Mutex::new(sender);
            let server = crate::rpc::serve(&players, events, move |call| sender.lock().unwrap().try_send(Message::Rpc(call)).is_ok());
            match server {
                Ok(server) => slot.hold(server),
                Err(e) => eprintln!("cinedex: no Live view endpoint: {e}"),
            }
            futures::future::pending::<()>().await;
        });
        let mut autosave = Subscription::batch([
            time::every(AUTOSAVE, || Message::Autosave),
            rpc,
            time::every(JOURNAL_EVERY, || Message::PollJournal),
            time::every(PREVIEWS_EVERY, || Message::PollPreviews),
            time::every(crate::farm_jobs::EVERY, || Message::PollFarm),
        ]);
        if self.show_live && self.entries.iter().any(|e| e.resolved().is_some_and(|r| r.live.playing().is_some())) {
            autosave = Subscription::batch([autosave, time::every(LIVE_EVERY, || Message::PollLive)]);
        }
        // The board shows which workfiles a Houdini has open: ask while it
        // is on screen (a local call), not while cutting.
        if self.mode == 1 && self.desktop.is_some() {
            return Subscription::batch([engine_events, autosave, time::every(SESSIONS_EVERY, || Message::PollSessions)]);
        }
        if !self.playing {
            return Subscription::batch([engine_events, autosave]);
        }
        let half_frame = Duration::from_secs_f64(0.5 / f64::from(self.fps()));
        Subscription::batch([engine_events, autosave, time::every(half_frame, || Message::Tick)])
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let video = shader(self.video()).width(Length::Fill).height(Length::Fill).redraw(false);
        let mut layers = stack().push(video).width(Length::Fill).height(Length::Fill);
        // The burn-ins go over the picture unmirrored, so they still read
        // when it is flipped (`flip`).
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
        // A right-click on the picture: what the view of it can do.
        let (label, flip) = self.flip_row();
        let layers = Roomy::menus(context_menu(Id::new("viewer menu"), layers).item(label, move || flip.clone()));
        // The panels resize (`editor/panels.rs`): drag a handle, double-
        // click it to fold the panel to a strip, click the strip to bring it
        // back. The handles stay out of keyboard focus, so the player's keys
        // keep working after a drag. The sizes are remembered for this user.
        let middle: Element<Message> = if self.mode == 1 {
            self.board_view()
        } else {
            let viewer = column()
                .push(self.player_header())
                .push(layers)
                .push(self.transport_bar())
                .push(self.display_controls())
                .width(Length::Fill)
                .height(Length::Fill);
            panels::bins_split(self.bins_view(), viewer, &self.layout, Message::BinsPanel).into()
        };
        let timeline = column().push(self.timeline_header()).push(self.tracks_view()).width(Length::Fill).height(Length::Fill);
        let body = panels::timeline_split(middle, timeline, self.timeline_height(), self.layout.timeline_collapsed, Message::TimelinePanel);
        // The status line is always there, empty when there is nothing to
        // say: a refused drag putting it up (and the next edit taking it
        // down) used to move the whole timeline up and down.
        let (words, error) = self.status.clone().unwrap_or_default();
        let colour = if error { look::error() } else { look::text() };
        let status = container(text(words).font_size(13.0).color(colour))
            .padding(Padding::from([0.0, 14.0]))
            .width(Length::Fill)
            .height(Length::fixed(STATUS_HEIGHT))
            .align_y(Vertical::Center)
            .background(look::panel());
        let page = column().push(self.toolbar()).push(body).push(status).width(Length::Fill).height(Length::Fill);
        let mut root = modal(page);
        if let Some(view) = self.dialog_view() {
            root = root.dialog(Id::new("dialog"), view).on_dismiss(|| Message::Dismiss);
        }
        root.into()
    }
}

impl Editor {
    /// The model's side of [`Application::update`], which quits the run
    /// once this left (see [`Self::leave`]).
    fn handle(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Start if !self.started => {
                self.started = true;
                crate::app_icon::apply();
                let resolve = self.changed();
                let mut commands = vec![resolve, self.refresh_bins()];
                if let Some((shot, frame)) = self.pending_view.take() {
                    // Houdini started this player on the master edit for it.
                    let (result, command) = self.view_shot(&shot, frame, false);
                    if let Err(e) = result {
                        self.status = Some((format!("Live view: {}", e.message), true));
                    }
                    commands.push(command);
                }
                return Command::batch(commands);
            }
            Message::Start => {}
            Message::Wake => {
                crate::app_icon::apply();
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
                self.leave_loop_outside();
            }
            Message::Seconds(delta) => {
                self.set_playing(false);
                self.move_to(self.position.saturating_add_signed(delta as isize * self.fps() as isize));
                self.leave_loop_outside();
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
                // A click past the last clip, or Go to End: the cut's end.
                self.set_playing(false);
                self.move_to(position.min(self.end()));
                self.leave_loop_outside();
            }
            Message::TogglePlay => self.set_playing(!self.playing),
            Message::Select(clip, additive, position) => {
                self.gap = None;
                self.anchor = clip;
                match clip {
                    None => {
                        self.selection.clear();
                        self.set_playing(false);
                        self.move_to(position.min(self.end()));
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
                }
            }
            Message::Tracks(gesture) => self.gesture(gesture),
            Message::CloseGap(clip) => return self.close_gap(clip),
            Message::Drag => {}
            Message::SelectAll => {
                // Every clip that has a row: a hidden track's are not seen,
                // so not selected (Delete would take them out unseen).
                let edit = &self.doc.edit;
                self.selection = self
                    .shown_tracks()
                    .into_iter()
                    .flat_map(|track| (0..edit.spans(track).len()).map(move |index| ClipRef { track, index }))
                    .collect();
                self.gap = None;
            }
            Message::ClearSelection => {
                self.selection.clear();
                self.gap = None;
            }
            // What is selected stays selected through an edit: it follows
            // its clips (`gestures`).
            Message::Edit(op) => {
                // A move of a selected clip with others selected moves them all.
                if let Op::Move { clip, to, at } = &op
                    && self.selection.len() > 1
                    && self.selection.contains(clip)
                    && to == &clip.track
                {
                    let from = self.doc.edit.spans(clip.track)[clip.index].0;
                    let ops = editing::move_all(&self.doc.edit, &self.selection, at - from);
                    return self.edit(&ops);
                }
                // A clip let go on the spare layer makes its track first.
                return self.edit(&track_rows::landing(&self.doc.edit, op));
            }
            Message::Edits(ops) => return self.edit(&ops),
            Message::ClipField(field, text) => self.type_into(field, text),
            Message::ClipFieldSet(field) => return self.set_field(field),
            // What the status said was of the cut before: a refusal left
            // there would read as being of what the undo just did.
            Message::Undo => {
                if self.doc.undo() {
                    self.status = None;
                    return self.changed_by_edit();
                }
            }
            Message::Redo => {
                if self.doc.redo() {
                    self.status = None;
                    return self.changed_by_edit();
                }
            }
            Message::Split => {
                let at = self.position as EditFrame;
                let ops: Vec<Op<Source>> = Self::latest_first(self.targets()).into_iter().map(|clip| Op::Split { clip, at }).collect();
                return self.edit(&ops);
            }
            // Delete with a gap picked (and so no clip): the gap closes.
            Message::Lift | Message::RippleDelete if self.gap.is_some() && self.selection.is_empty() => {
                let gap = self.gap.take().expect("checked");
                return self.close_gap(gap);
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
            Message::Resolved(index, result) => {
                self.resolved(index, result);
                if std::mem::take(&mut self.entries[index].again) {
                    return self.refresh_entry(index);
                }
            }
            Message::ClipMenu(clip, frame) => {
                // Picked as a click picks it, so the menu's Delete rows
                // act on it (and on the others picked with it).
                if !self.selection.contains(&clip) {
                    self.selection = BTreeSet::from([clip]);
                    self.anchor = Some(clip);
                }
                self.gap = None;
                self.menu_clip = Some((clip, frame));
                self.menu_empty = None;
            }
            Message::TimelineMenu(frame, track) => {
                self.menu_clip = None;
                self.menu_empty = Some((frame, track));
            }
            Message::At(frame, then) => {
                // As a click there: park the playhead, then act at it.
                self.set_playing(false);
                self.move_to(frame.min(self.end()));
                self.leave_loop_outside();
                return self.update(*then);
            }
            Message::Take(clip, take, pin) => return self.change_take(clip, take, pin),
            Message::TakeStep(axis, delta) => return self.take_step(axis, delta),
            Message::TakeLayer(clip, layer) => return self.change_layer(clip, layer),
            Message::Aov(row) => self.change_view(|media, view, _| {
                if let Some(v) = layer_view::aov_view(media.layers(), row) {
                    *view = v;
                }
            }),
            Message::ShowLayer(layer, shown) => self.change_view(|_, _, hidden| *hidden = layer_view::set_shown(*hidden, layer, shown)),
            Message::Bins(result) => match result {
                Ok(bins) => {
                    self.bins = bins;
                    return self.refresh_board();
                }
                Err(e) => self.status = Some((format!("The bins: {e}"), true)),
            },
            Message::Mode(mode) => {
                self.mode = mode;
                if mode == 1 {
                    self.set_playing(false);
                    return Command::batch([self.refresh_bins(), self.poll_sessions()]);
                }
            }
            Message::Board(result) => {
                self.board_loading = false;
                match result {
                    Ok(board) => self.board = Some(board),
                    Err(e) => self.status = Some((format!("The board: {e}"), true)),
                }
            }
            Message::PollSessions => return self.poll_sessions(),
            Message::Sessions(result) => {
                self.sessions_polling = false;
                // A Desktop that stops answering is said once, by the Open
                // that needs it, not every few seconds here.
                if let Ok(sessions) = result {
                    self.sessions = sessions;
                }
            }
            Message::OpenWorkfile(shot, department) => return self.open_workfile(&shot, &department),
            Message::Reveal(folder) => {
                if let Err(e) = crate::reveal::reveal(&folder) {
                    self.status = Some((format!("Not revealed: {e}"), true));
                }
            }
            Message::Opened(result) => {
                self.status = Some(match result {
                    Ok((name, Opened::Raised(_))) => (format!("{name} is open in Houdini: brought to the front"), false),
                    Ok((name, Opened::Launched(_))) => (format!("Houdini is starting on {name}"), false),
                    Ok((name, Opened::Loaded)) => (format!("Opened {name} in your Houdini"), false),
                    Ok((name, Opened::AlreadyHere)) => (format!("{name} is open in your Houdini: brought to the front"), false),
                    Ok((name, Opened::Cancelled)) => (format!("{name} not opened: you cancelled in Houdini"), false),
                    Err(e) => (format!("Not opened: {e}"), true),
                });
                return self.poll_sessions();
            }
            Message::Rpc(call) => match call.request.clone() {
                crate::rpc::Request::ViewShot { shot, frame } => {
                    let (result, command) = self.view_shot(&shot, frame, true);
                    call.answer(result);
                    return command;
                }
                crate::rpc::Request::Call { method, params } => return self.agent_call(call, &method, &params),
            },
            Message::Answer(call, result) => call.answer(result),
            Message::ClearLoop => self.live_loop = None,
            Message::KeepLive(index) => return self.keep_live(index),
            Message::Kept(index, result) => {
                self.status = Some(match result {
                    Ok(path) => {
                        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                        (format!("Kept as {name}"), false)
                    }
                    Err(e) => (format!("Not kept: {e}"), true),
                });
                // The new version is a take of the shot now (and the take
                // under the live frames).
                return self.refresh_entry(index);
            }
            Message::BinTab(tab) => {
                self.bin_tab = tab;
                self.bin_pick = None;
                self.bin_state.tab_changed();
            }
            Message::BinPick(pick) => self.bin_pick = Some(pick),
            Message::BinPut(overlay) => return self.bin_put_picked(overlay),
            Message::Bin(message) => return self.bin_message(message),
            Message::Files(message) => return self.files_message(message),
            Message::Open(OpenDialog::Import) => return self.import_browse(),
            Message::Open(which) => self.open_dialog(which),
            Message::Export(m) => return self.export_update(m),
            Message::Timeline(m) => return self.timeline_update(m),
            message @ (Message::Text(..)
            | Message::Confirm
            | Message::Alternate
            | Message::Dismiss
            | Message::Saved(..)
            | Message::Published(..)
            | Message::TickChange(..)
            | Message::SetMaster(..)
            | Message::MasterSet(..)
            | Message::Imported(..)
            | Message::NewShotNow(..)) => return self.file_update(message),
            Message::SyncRanges(on) => self.sync_ranges = on,
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
            Message::Burnins(on) => self.burnins = on,
            Message::Flip(on) => self.flipped = on,
            Message::FitTimeline => self.fit_timeline(),
            Message::TimelineView(change) => self.timeline_view = timeline_view::viewed(self.timeline_view, change),
            Message::ScrollTo(first) => self.timeline_view = self.timeline_view.scrolled_to(first),
            Message::ShowFrames(first, shown) => self.timeline_view = self.timeline_view.showing(first, shown),
            Message::FitClips => {
                let (from, to) = timeline_view::clips_span(&self.doc.edit);
                self.timeline_view = self.timeline_view.fitted_to(from, to);
            }
            Message::RowHeight(track, height) => self.set_row_height(track, height),
            Message::Solo(track) => {
                self.solo = track_rows::toggle_solo(self.solo, track);
                self.refocused();
            }
            Message::HideAudio(hidden) => {
                self.track_layout.audio_hidden = hidden;
                self.save_track_layout();
                self.refocused();
            }
            Message::ShowAllTracks => {
                self.solo = None;
                if self.track_layout.audio_hidden {
                    self.track_layout.audio_hidden = false;
                    self.save_track_layout();
                }
            }
            Message::BinsPanel(change) => {
                let mut layout = self.layout;
                if let SplitSize::First(width) = change.size {
                    layout.bins_width = width;
                }
                layout.bins_collapsed = change.collapsed;
                self.set_layout(layout);
            }
            Message::TimelinePanel(change) => {
                let mut layout = self.layout;
                // A fold leaves an unset height unset, so it still fits the
                // tracks.
                if let SplitSize::Second(height) = change.size
                    && height != self.timeline_height()
                {
                    layout.timeline_height = Some(height);
                }
                layout.timeline_collapsed = change.collapsed;
                self.set_layout(layout);
            }
            Message::ShowLive(on) => {
                self.show_live = on;
                return self.re_resolve_shots();
            }
            Message::Show(show) => return self.set_show(show),
            Message::PollLive => return self.poll_live(),
            Message::PollJournal => return Command::batch([self.poll_journal(), self.bin_thumbs_pump()]),
            Message::PollPreviews => return Command::batch([crate::previews::poll(Message::Previews), self.poll_rendering()]),
            Message::Rendering(found) => self.rendering.apply(found),
            Message::Previews(ready) => self.previews = ready,
            Message::PollFarm => {
                if !self.farm_asking {
                    self.farm_asking = true;
                    return crate::farm_jobs::poll(self.project_name(), Message::FarmJobs);
                }
            }
            Message::FarmJobs(jobs) => {
                self.farm_asking = false;
                self.farm_jobs = jobs;
            }
            Message::FarmJob(act) => self.farm_job_act(act),
            Message::SharedJournal(changes) => {
                let changes = self.shared_journal.arrived(changes);
                return self.apply_journal(Some(changes));
            }
            Message::Refresh => {
                self.bin_thumbs_changed(None);
                let shots = self.shot_entries().into_iter().map(|(i, _)| i).collect();
                return Command::batch([self.refresh_entries(shots), self.refresh_bins()]);
            }
            Message::Autosave => self.autosave(),
            Message::ConvertToShot(clip) => self.convert_to_shot(clip),
            Message::SwapOriginal(clip) => return self.swap_original(clip),
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
                    Ok(_) if self.doc.replaceable() => self.leave(),
                    Ok(_) => self.status = Some((format!("Opening {edit} in a new window"), false)),
                    Err(e) => self.status = Some((format!("{edit} did not open: {e}"), true)),
                }
            }
            Message::CloseRequested => {
                if export_dialog::cancel(&self.dialog) {
                    self.status = Some(("Cancelling the export: close again once it has stopped".into(), false));
                } else if self.doc.dirty() {
                    self.dialog = Some(Dialog::Unsaved);
                } else {
                    self.leave();
                }
            }
        }
        Command::none()
    }
}

#[cfg(test)]
mod tests {
    /// Every way the editor quits goes through `leave`, which releases the
    /// endpoint: a quit that only set the flag left the endpoint file
    /// behind (2026-11-12), since the task serving it never ends.
    #[test]
    fn every_quit_releases_the_endpoint() {
        let source = include_str!("editor.rs");
        let sets = source.matches(concat!("self.quit", " = true")).count();
        assert_eq!(sets, 1, "set the quit flag only in Editor::leave, which releases the endpoint");
        let leave = source.split("fn leave(&mut self) {").nth(1).and_then(|rest| rest.split('}').next()).unwrap_or_default();
        assert!(leave.contains(concat!("self.quit", " = true")) && leave.contains("self.endpoint.release()"));
    }

    use super::*;
    use th_edit::document::{Rule, VersionRule};

    proptest::proptest! {
        /// Exactly a window on the master edit shows a Live view; every
        /// other window says to ask another (NOT_HERE), so Houdini goes on
        /// to the next or starts one; an unreadable master edit refuses.
        #[test]
        fn only_the_master_edit_window_shows_a_live_view(
            open in proptest::option::of("[a-c]{1,2}"),
            master in proptest::option::of("[a-c]{1,2}"),
            broken in proptest::bool::ANY,
        ) {
            let given = if broken { Err("bad config".to_string()) } else { Ok(master.clone()) };
            let refusal = master_refusal(open.as_deref(), given);
            match (broken, &master) {
                (true, _) => proptest::prop_assert_eq!(refusal.map(|r| r.code), Some(rpc::REFUSED)),
                (false, Some(m)) if open.as_deref() == Some(m.as_str()) => proptest::prop_assert_eq!(refusal, None),
                _ => proptest::prop_assert_eq!(refusal.map(|r| r.code), Some(rpc::NOT_HERE)),
            }
        }
    }

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
