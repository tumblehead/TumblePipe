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
//! drag on it to scrub. The keys are the editor's Playback keys: Space
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
//! again once its file changes).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use futures::channel::mpsc::Sender;
use primer::keyboard::{Key, Modifiers, Named};
use primer::menu::{Accelerator, Menu, MenuItem};
use primer::prelude::*;
use primer::subscription::{self, time};
use th_edit::document::TrackRef;
use th_media::{ExrSequence, FrameKey, JpgSequence, Media};

use crate::colour::{Colour, Lut};
use crate::display::{Display, display_bar, theme};
use crate::engine::{Clip, Content, Engine, Timeline};
use crate::glyph::{self, Glyph};
use crate::look;
use crate::previews::open_frames;
use crate::reel::{Fill, Group, Reel, Run, Tick, Transport};
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
    Display(usize),
    View(usize),
    Exposure(f32),
    Rescan,
    /// A group's frames opened, for the generation of the group asked for.
    Opened(usize, u64, Result<Arc<Media>, String>),
    /// The frames read again, with the modification times of the files of
    /// frames that failed to read.
    Scanned(Result<Reel, String>, Vec<(FrameKey, Option<SystemTime>)>),
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
    reel: Reel,
    display: Display,
    engine: Engine,
    opened: Vec<Opened>,
    transport: Transport,
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
/// first frame on disk, or JPGs (nothing read). Off the UI thread.
fn open_group(group: &Group, rate: (i32, i32)) -> Result<Arc<Media>, String> {
    let index = group.first_present().ok_or("none of its frames is rendered yet")?;
    let first = group.slots.first().and_then(|s| s.number).unwrap_or(0);
    let media = if group.is_jpg() {
        Media::jpg(JpgSequence::new(first, 1, group.paths()), rate)
    } else {
        Media::exr_from(ExrSequence::new(first, 1, group.paths()), rate, index).map_err(|e| e.to_string())?
    };
    Ok(Arc::new(media))
}

/// The frame numbers `run` spans, as a label: `1001–1050`, `1023`.
fn span_label(reel: &Reel, run: &Run) -> String {
    let group = &reel.groups[run.group];
    let number = |i: usize| group.slots[i].number.map_or_else(|| (i + 1).to_string(), |n| n.to_string());
    if run.len == 1 { number(run.first) } else { format!("{}\u{2013}{}", number(run.first), number(run.first + run.len - 1)) }
}

/// What a key does in the window (the menu shows the same keys).
pub(crate) fn key_message(key: &Key, modifiers: Modifiers) -> Option<Message> {
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
        Key::Character('r' | 'R') if command => Some(Message::Rescan),
        Key::Character('l' | 'L') if !command && !modifiers.alt => Some(Message::Loop),
        _ => None,
    }
}

const NONE: Modifiers = Modifiers::NONE;
const ALT: Modifiers = Modifiers { alt: true, ..Modifiers::NONE };

/// One menu row: its label, message and key.
type Row3 = (&'static str, Message, Option<(Key, Modifiers)>);

/// The menus: (title, rows), None rows being separators.
fn headings(playing: bool, looping: bool) -> Vec<(&'static str, Vec<Option<Row3>>)> {
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
        ("&View", vec![Some(("&Refresh", Message::Rescan, Some((Key::Character('r'), Modifiers::COMMAND))))]),
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
        FramesView {
            source,
            reel,
            display: Display { colour, display, view, exposure: 0.0 },
            engine,
            opened: vec![Opened::default(); groups],
            transport,
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

    fn content(&self, g: usize) -> Content {
        match &self.opened.get(g).and_then(|o| o.media.clone()) {
            Some(media) => Content { media: Some(Arc::clone(media)), view: media.default_view(), hidden: 0, wants_proxy: false },
            None => Content::none(),
        }
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
        if let Some(frame) = self.engine.frame(key) {
            self.frames_shown += 1;
            let lut = self.opened.get(key.source).and_then(|o| o.lut.clone());
            self.shown = Some((key, Picture { id: self.frames_shown, frame, lut }));
        }
    }

    fn set_display(&mut self) {
        for o in &mut self.opened {
            if let Some(media) = &o.media {
                o.lut = match self.display.lut(media) {
                    Ok(lut) => lut,
                    Err(e) => {
                        o.error = Some(e);
                        None
                    }
                };
            }
        }
        self.shown = None;
        self.refresh_shown();
    }

    fn rescan(&mut self) -> Command<Message> {
        if self.scanning {
            return Command::none();
        }
        self.scanning = true;
        let source = self.source.clone();
        let failed: Vec<(FrameKey, PathBuf)> = self
            .failed_at
            .keys()
            .filter_map(|k| self.reel.groups.get(k.source).and_then(|g| g.slots.get(k.index)).map(|s| (*k, s.path.clone())))
            .collect();
        Command::perform_blocking(
            move || {
                let times = failed.into_iter().map(|(k, p)| (k, std::fs::metadata(p).and_then(|m| m.modified()).ok())).collect();
                (source.read(), times)
            },
            |(reel, times)| Message::Scanned(reel, times),
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
        self.reel = reel;
        self.transport.set_len(self.reel.len());
        self.engine.set_timeline(self.timeline());
        let commands: Vec<Command<Message>> = changed.into_iter().map(|g| self.open(g)).collect();
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
                    room: None,
                }
            })
            .collect();
        vec![RowView { track: TrackRef::video(0), name: "Frames".into(), clips, height: tracks::ROW, solo: false, spare: false }]
    }

    fn toolbar(&self) -> Element<Message> {
        let playing = self.transport.playing;
        let (rendered, slots) = self.reel.rendered();
        let mut title = format!("Preview \u{00b7} {}", self.reel.title);
        if rendered < slots {
            title.push_str(&format!("   {rendered} of {slots} frames rendered"));
        }
        let (rate, rate_colour) = self.rate_text();
        let transport = row()
            .spacing(4.0)
            .align_y(Vertical::Center)
            .push(glyph::button("start", Glyph::ToStart, "Go to start (Home)", Message::Seek(0)))
            .push(glyph::button("back", Glyph::StepBack, "Previous frame (Left)", Message::Step(-1)))
            .push(glyph::button(
                "play",
                if playing { Glyph::Pause } else { Glyph::Play },
                if playing { "Pause (Space)" } else { "Play (Space)" },
                Message::TogglePlay,
            ))
            .push(glyph::button("forward", Glyph::StepForward, "Next frame (Right)", Message::Step(1)))
            .push(glyph::button("end", Glyph::ToEnd, "Go to end (End)", Message::Last));
        let looping = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(look::tick_box(Id::new("loop"), self.transport.looping, |_| Message::Loop).focusable(false))
            .push(text("Loop").font_size(13.0).color(look::quiet()));
        let tools = row()
            .spacing(24.0)
            .align_y(Vertical::Center)
            .push(text(title).font_size(15.0).color(look::text()))
            .push(space(Length::Fill, Length::Shrink))
            .push(transport)
            .push(tooltip(Id::new("loop tip"), looping, "Play round from the end to the start (L)"))
            .push(text(rate).font_size(13.0).color(rate_colour))
            .push(text(self.frame_text()).font_size(20.0).color(look::now()));
        container(tools).padding(Padding::from([8.0, 14.0])).width(Length::Fill).background(look::panel()).into()
    }

    fn timeline_view(&self) -> Element<Message> {
        let program = Tracks {
            rows: self.rows(),
            markers: Vec::new(),
            total: self.reel.len(),
            playhead: self.transport.position,
            buffered: tracks::ranges(&self.engine.buffered_positions()),
            on_seek: Message::Seek,
            // No selection here: a click on a clip seeks, as one on a gap.
            on_select: |_, _, position| Message::Seek(position),
            on_drag: || Message::Ignore,
            on_op: |_| Message::Ignore,
            on_menu: |_| Message::Ignore,
            on_row_height: |_, _| Message::Ignore,
            on_solo: |_| Message::Ignore,
            on_context: |_, _| Message::Ignore,
            note: None,
            editable: false,
        };
        let height = program.height();
        tracks::track_view(Id::new("frames timeline"), program, height).into()
    }

    fn menu_bar(&self) -> Menu<Message> {
        let mut bar = Menu::new();
        if cfg!(target_os = "macos") {
            let quit =
                MenuItem::button("Quit Cinedex", Message::Quit).accelerator(Accelerator::new(Key::Character('q'), Modifiers::COMMAND));
            bar = bar.app_submenu("Cinedex", Menu::new().push(quit));
        }
        for (label, rows) in headings(self.transport.playing, self.transport.looping) {
            let menu = rows.into_iter().fold(Menu::new(), |menu, row| {
                menu.push(match row {
                    None => MenuItem::separator(),
                    Some((label, message, keys)) => {
                        let item = MenuItem::button(label, message);
                        // On macOS only Command chords go to the menu: a bare
                        // key stays the window's (as the editor's menu has it).
                        match keys.filter(|(_, m)| !cfg!(target_os = "macos") || m.command()) {
                            Some((key, modifiers)) => item.accelerator(Accelerator::new(key, modifiers)),
                            None => item,
                        }
                    }
                })
            });
            bar = bar.submenu(label, menu);
        }
        bar
    }
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
                let commands: Vec<Command<Message>> = (0..self.reel.groups.len()).map(|g| self.open(g)).collect();
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
            Message::Scanned(reel, times) => return self.scanned(reel, times),
            Message::Opened(g, generation, result) => {
                if self.opened.get(g).is_none_or(|o| o.generation != generation) {
                    return Command::none();
                }
                match result {
                    Ok(media) => {
                        let lut = self.display.lut(&media);
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
            Message::Quit => self.quit = true,
        }
        Command::none()
    }

    fn on_key(&self, _window: window::Id, key: Key, modifiers: Modifiers) -> Option<Message> {
        key_message(&key, modifiers)
    }

    fn menu(&self) -> Option<Menu<Message>> {
        Some(self.menu_bar())
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
        let picture = self.shown.as_ref().map(|(_, p)| p.clone());
        let video = shader(Video { picture, exposure: self.display.exposure }).width(Length::Fill).height(Length::Fill).redraw(false);
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
        let bar = display_bar(&self.display, row(), Message::Display, Message::View, Message::Exposure);
        let mut page =
            column().push(self.toolbar()).push(layers).push(bar).push(self.timeline_view()).width(Length::Fill).height(Length::Fill);
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

    /// Every menu row's key sends the row's message.
    #[test]
    fn the_menu_shows_the_keys_that_work() {
        for playing in [false, true] {
            for (_, rows) in headings(playing, playing) {
                for (label, message, keys) in rows.into_iter().flatten() {
                    let Some((key, modifiers)) = keys else { continue };
                    let sent = key_message(&key, modifiers);
                    assert_eq!(format!("{sent:?}"), format!("{:?}", Some(message)), "{label}");
                }
            }
        }
    }
}
