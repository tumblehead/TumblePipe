//! th_player: plays shots back to back, frame-accurately.
//!
//! `th_player <playlist.otio>` plays a TumblePipe playlist (see
//! the `th_playlist` crate); `th_player <media> [<media> ...]` plays movies and EXR
//! sequences (any frame of one, or its folder) in argument order.
//! `--ocio <config>` names the OCIO config for EXRs (else the playlist's,
//! else `$OCIO`).
//!
//! Under the video the track shows the shots as clips (see `track`). A change
//! of take applies to the shots selected there, else to the playhead's shot,
//! and replaces only their clips: every other shot keeps its buffered frames.
//! A take not opened yet opens off the UI thread (see `opener`), the shot
//! showing its old take until it has.
//!
//! Keys: Left/Right step a frame, Alt+Left/Right a second (at the rate of
//! the shot under the playhead), Ctrl+Left/Right jump to the previous/next
//! shot, Home/End the first/last frame, Space play/pause. Up/Down show the
//! next newer/older version, Ctrl+Up/Down (Cmd on macOS) the department
//! upstream/downstream, Shift+Up/Down the playblast/the render; Ctrl+A
//! selects every shot and Escape clears the selection; B shows or hides the
//! burn-ins (see `burnin`). The controls never take the keyboard from these.
//! Playback shows every frame: when the next one is not decoded yet it waits
//! rather than skips.

mod burnin;
mod colour;
mod engine;
mod opener;
mod take_menu;
mod thumbnails;
mod track;
mod video;

use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use futures::channel::mpsc::Sender;

use primer::keyboard::{Key, Modifiers, Named};
use primer::prelude::*;
use primer::subscription::{self, time};
use th_media::{ExrView, FrameKey, Source};

use burnin::Burnin;
use colour::Lut;
use engine::Engine;
use opener::{Aims, Ask, Opener};
use take_menu::kind_word;
use th_playlist::{Kind, Playlist, Shot};
use thumbnails::Thumbnails;
use track::{Pick, Segment, Track};
use video::{DisplaySettings, Video};

/// Decoded frames kept in memory (see `th_media::FrameBuffer`).
const BUFFER_BYTES: usize = 4 << 30;

/// The platform's name for the modifier the keys above call Ctrl.

#[derive(Debug, Clone)]
enum Message {
    Step(i64),
    /// Seconds to move, a second being the playhead shot's rate in frames.
    Seconds(i64),
    Clip(i64),
    Seek(usize),
    /// A click on a shot's clip: the clip, the frame under the click, and
    /// how it changes the selection.
    Pick(usize, usize, Pick),
    SelectAll,
    ClearSelection,
    TogglePlay,
    Tick,
    /// A frame decoded, a thumbnail made or a take opened (see `waker`).
    Wake,
    /// A shot's take, by index, chosen from its clip's menu.
    Take(usize, usize),
    /// A right press on a clip, just before its take menu opens.
    TakeMenu(usize),
    /// The changed shots' next newer (positive) or older version.
    Version(i64),
    /// The changed shots' department downstream (positive) or upstream.
    Department(i64),
    /// Show the changed shots' playblast or render.
    ShowKind(Kind),
    Layer(usize),
    Channel(usize),
    Display(usize),
    View(usize),
    Exposure(f32),
    /// Show or hide the burn-ins.
    Burnins(bool),
}

/// The display side: the OCIO config, if one opened, and what it shows.
struct Display {
    #[cfg(feature = "ocio")]
    colour: Option<colour::Colour>,
    display: String,
    view: String,
    exposure: f32,
    error: Option<String>,
}

impl Display {
    fn displays(&self) -> Vec<String> {
        #[cfg(feature = "ocio")]
        if let Some(c) = &self.colour {
            return c.displays();
        }
        Vec::new()
    }

    fn views(&self) -> Vec<String> {
        #[cfg(feature = "ocio")]
        if let Some(c) = &self.colour {
            return c.views(&self.display);
        }
        Vec::new()
    }

    /// The LUT for scene-linear frames in `input` (None: the config's
    /// scene-linear space); None without a config.
    fn lut(&mut self, input: Option<&str>) -> Option<Arc<Lut>> {
        #[cfg(feature = "ocio")]
        if let Some(c) = &mut self.colour {
            let input = match input {
                Some(cs) if c.has_colour_space(cs) => cs.to_owned(),
                _ => c.scene_linear(),
            };
            return match c.lut(&input, &self.display, &self.view) {
                Ok(lut) => Some(lut),
                Err(e) => {
                    self.error = Some(e);
                    None
                }
            };
        }
        let _ = input;
        None
    }
}

fn open_display(path: Option<&str>) -> Display {
    #[cfg(feature = "ocio")]
    {
        match colour::Colour::open(path) {
            Ok(c) => {
                let display = c.default_display().unwrap_or_default();
                let view = c.default_view(&display).unwrap_or_default();
                Display { colour: Some(c), display, view, exposure: 0.0, error: None }
            }
            Err(e) => Display { colour: None, display: String::new(), view: String::new(), exposure: 0.0, error: Some(e) },
        }
    }
    #[cfg(not(feature = "ocio"))]
    {
        let _ = path;
        Display { display: String::new(), view: String::new(), exposure: 0.0, error: None }
    }
}

/// The frame on screen and what it is a frame of.
struct Shown {
    key: FrameKey,
    /// The (shot, take) and view it was decoded from: after a switch of
    /// either it stays up until the new one's frame lands.
    take: (usize, usize),
    view: ExrView,
    frame: th_media::Frame,
    /// New for every frame shown, so the renderer uploads it.
    id: u64,
}

struct Player {
    playlist: Playlist,
    /// Opened takes, by (shot, take), kept so switching back is instant.
    sources: HashMap<(usize, usize), Arc<Source>>,
    /// Each opened take's EXR view.
    views: HashMap<(usize, usize), ExrView>,
    /// Each opened take's display LUT (None: a movie, or no config).
    luts: HashMap<(usize, usize), Option<Arc<Lut>>>,
    /// The shot each engine clip is (shots whose take failed are left out).
    clip_shots: Vec<usize>,
    engine: Engine,
    opener: Opener,
    /// The take each shot is switching to while it opens.
    aims: Aims,
    thumbnails: Thumbnails,
    display: Display,
    /// Takes that failed to open.
    open_errors: Vec<String>,
    /// Timeline position of the playhead.
    position: usize,
    playing: bool,
    /// The playhead's frame once it is decoded, until then the last frame
    /// shown. Its key is what the status line reports.
    shown: Option<Shown>,
    frames_shown: u64,
    /// The shots selected on the track (playlist indices). None selected: a
    /// change applies to the playhead's shot.
    selection: BTreeSet<usize>,
    /// The shot a Shift-click's run starts from.
    anchor: Option<usize>,
    /// The take each shot last showed of each kind, so a switch from the
    /// render back to the playblast finds the one left.
    remembered: HashMap<(usize, Kind), usize>,
    /// Whether the burn-ins show.
    burnins: bool,
    /// The shot whose clip was last right-clicked: its takes fill the menu.
    menu_shot: Option<usize>,
    wake_slot: Arc<Mutex<Option<Sender<Message>>>>,
    /// Playback clock: when play started (or last stalled) and the timeline
    /// position at that moment.
    clock: (Instant, usize),
}

/// A notifier that wakes the UI (see `Player::subscription`). Every
/// notifier sends the same `Wake`, which looks at everything that can have
/// finished, so a wake-up dropped on a full channel loses nothing.
fn waker(slot: &Arc<Mutex<Option<Sender<Message>>>>) -> impl Fn() + Send + 'static {
    let slot = Arc::clone(slot);
    move || {
        if let Some(sender) = slot.lock().unwrap().as_mut() {
            // A full channel already has a wake-up queued; dropping this one is fine.
            let _ = sender.try_send(Message::Wake);
        }
    }
}

impl Player {
    fn new(playlist: Playlist, display: Display) -> Self {
        let wake_slot: Arc<Mutex<Option<Sender<Message>>>> = Arc::new(Mutex::new(None));
        let thumbnails = Thumbnails::start();
        thumbnails.set_notifier(waker(&wake_slot));
        let opener = Opener::start();
        opener.set_notifier(waker(&wake_slot));
        let mut player = Self {
            playlist,
            sources: HashMap::new(),
            views: HashMap::new(),
            luts: HashMap::new(),
            clip_shots: Vec::new(),
            engine: Engine::start(Vec::new(), Vec::new(), 0),
            opener,
            aims: Aims::default(),
            thumbnails,
            display,
            open_errors: Vec::new(),
            position: 0,
            playing: false,
            shown: None,
            frames_shown: 0,
            selection: BTreeSet::new(),
            anchor: None,
            remembered: HashMap::new(),
            burnins: true,
            menu_shot: None,
            wake_slot,
            clock: (Instant::now(), 0),
        };
        player.open_all();
        player.rebuild(None);
        player
    }

    /// Open every shot's take at once: each mostly waits on the file
    /// server, so the playlist opens in about the time of its slowest take.
    fn open_all(&mut self) {
        let results: Vec<Option<Result<Source, String>>> = thread::scope(|scope| {
            let opening: Vec<_> = self
                .playlist
                .shots
                .iter()
                .map(|s| {
                    let take = s.takes.get(s.take);
                    scope.spawn(move || take.map(|t| Source::open(&t.path, t.rate).map_err(|e| e.to_string())))
                })
                .collect();
            opening.into_iter().map(|o| o.join().expect("opening a take panicked")).collect()
        });
        for (shot, result) in results.into_iter().enumerate() {
            let key = (shot, self.playlist.shots[shot].take);
            match result {
                Some(Ok(source)) => self.add_source(key, source),
                Some(Err(e)) => self.open_failed(key, &e),
                None => {}
            }
        }
    }

    /// Keep an opened take, with its display LUT and the view it opens on.
    fn add_source(&mut self, key: (usize, usize), source: Source) {
        let source = Arc::new(source);
        let lut = if source.is_scene_linear() { self.display.lut(source.colour_space()) } else { None };
        self.luts.insert(key, lut);
        self.views.insert(key, source.default_view());
        self.sources.insert(key, source);
    }

    fn open_failed(&mut self, (shot, take): (usize, usize), error: &str) {
        let s = &self.playlist.shots[shot];
        self.open_errors.push(format!("{} ({}): {error}", s.name, s.takes[take].label));
    }

    /// Start a new engine on the shots' current takes that opened, the
    /// playhead on `keep` (a shot and frame) if it is still there.
    fn rebuild(&mut self, keep: Option<(usize, usize)>) {
        let mut sources = Vec::new();
        let mut views = Vec::new();
        self.clip_shots.clear();
        for shot in 0..self.playlist.shots.len() {
            let key = (shot, self.playlist.shots[shot].take);
            if let Some(s) = self.sources.get(&key) {
                views.push(self.views[&key].clone());
                sources.push(Arc::clone(s));
                self.clip_shots.push(shot);
            }
        }
        self.engine = Engine::start(sources, views, BUFFER_BYTES);
        self.engine.set_notifier(waker(&self.wake_slot));
        self.shown = None;
        let timeline = self.engine.timeline();
        self.position = match keep.and_then(|(shot, index)| Some((self.clip_shots.iter().position(|&s| s == shot)?, index))) {
            Some((clip, index)) if timeline.clips[clip].frames > 0 => {
                timeline.clip_start(clip) + index.min(timeline.clips[clip].frames - 1)
            }
            _ => 0,
        };
        if timeline.len() > 0 {
            self.move_to(self.position);
        }
        for shot in self.clip_shots.clone() {
            self.request_thumbnail(shot);
        }
    }

    /// Ask for the thumbnail of `shot`'s current take, if it opened. An EXR
    /// take's waits until the frame on screen is the playhead's (see
    /// `Message::Wake`): its read is as big as a frame's, and would share
    /// the file server with the one being waited for.
    fn request_thumbnail(&self, shot: usize) {
        let take = self.playlist.shots[shot].take;
        let Some(lut) = self.luts.get(&(shot, take)) else {
            return;
        };
        if self.sources[&(shot, take)].is_scene_linear() && !self.shown_is_current() {
            return;
        }
        let t = &self.playlist.shots[shot].takes[take];
        self.thumbnails.request(thumbnails::Request { key: (shot, take), path: t.path.clone(), rate: t.rate, lut: lut.clone() });
    }

    fn is_empty(&self) -> bool {
        self.engine.timeline().len() == 0
    }

    fn playhead(&self) -> FrameKey {
        self.engine.timeline().key(self.position)
    }

    /// The (shot, take) the playhead is on.
    fn current(&self) -> (usize, usize) {
        let shot = self.clip_shots[self.playhead().clip];
        (shot, self.playlist.shots[shot].take)
    }

    fn move_to(&mut self, position: usize) {
        if self.is_empty() {
            return;
        }
        let last = self.engine.timeline().len() - 1;
        self.position = position.min(last);
        self.engine.set_playhead(self.playhead());
        self.refresh_shown();
    }

    /// One playback tick: step to the next frame when the clock says it is
    /// due. Ticks come at twice the frame rate so timer slop never drags the
    /// average below the clip's rate; a frame not decoded yet stalls playback
    /// (every frame is shown) and restarts the clock from where it stalled.
    fn advance(&mut self) {
        let next = self.position + 1;
        if next >= self.engine.timeline().len() {
            self.playing = false;
            return;
        }
        let (rate_num, rate_den) = self.engine.timeline().clips[self.playhead().clip].rate;
        let (started, from) = self.clock;
        let due = from + (started.elapsed().as_secs_f64() * f64::from(rate_num) / f64::from(rate_den)) as usize;
        if self.position >= due {
            return;
        }
        if !self.engine.is_buffered(self.engine.timeline().key(next)) {
            self.clock = (Instant::now(), self.position);
            return;
        }
        self.move_to(next);
        // A cut can change the rate: restart the clock on every clip change.
        if self.playhead().index == 0 {
            self.clock = (Instant::now(), self.position);
        }
    }

    /// Whether the frame on screen is the playhead's, of the take and view
    /// chosen now.
    fn shown_is_current(&self) -> bool {
        self.shown.as_ref().is_some_and(|s| s.key == self.playhead() && s.take == self.current() && self.views.get(&s.take) == Some(&s.view))
    }

    fn refresh_shown(&mut self) {
        if self.is_empty() || self.shown_is_current() {
            return;
        }
        let key = self.playhead();
        // The engine drops a clip's frames when its take or view changes, so
        // what it has for the playhead is of the take and view chosen now.
        if let Some(frame) = self.engine.frame(key) {
            let take = self.current();
            self.frames_shown += 1;
            self.shown = Some(Shown { key, take, view: self.views[&take].clone(), frame, id: self.frames_shown });
        }
    }

    /// The shots a change of take applies to: those selected, else the
    /// playhead's.
    fn targets(&self) -> Vec<usize> {
        if self.selection.is_empty() { vec![self.current().0] } else { self.selection.iter().copied().collect() }
    }

    /// The take `shot` is switching to, else the one it shows.
    fn aimed(&self, shot: usize) -> usize {
        self.aims.aimed(shot, self.playlist.shots[shot].take)
    }

    /// `shot` as it is once the take it is switching to shows: what the
    /// keys step from, so presses made while a take opens add up.
    fn aimed_shot(&self, shot: usize) -> Shot {
        let mut s = self.playlist.shots[shot].clone();
        s.take = self.aimed(shot);
        s
    }

    /// Switch `shot` to `take`. A take opened before shows at once; any
    /// other opens off the UI thread and shows once it has (see
    /// `Message::Wake`), the shot showing its old take until then. A take
    /// that does not open leaves the shot as it was.
    fn set_take_of(&mut self, shot: usize, take: usize) {
        if take >= self.playlist.shots[shot].takes.len() {
            return;
        }
        let from = self.aimed(shot);
        let shown = self.playlist.shots[shot].take;
        let ask = self.aims.ask(shot, shown, take, self.sources.contains_key(&(shot, take)));
        if ask == Ask::Nothing {
            return;
        }
        let kind = self.playlist.shots[shot].takes[from].kind;
        self.remembered.insert((shot, kind), from);
        if ask == Ask::Show {
            self.show_take(shot, take);
        } else {
            let t = &self.playlist.shots[shot].takes[take];
            self.opener.request((shot, take), t.path.clone(), t.rate);
        }
    }

    /// Show `take` of `shot`, opened already. Only its clip changes: every
    /// other shot keeps its buffered frames, and the playhead stays on its
    /// frame (the new take's last, when that is shorter).
    fn show_take(&mut self, shot: usize, take: usize) {
        if self.playlist.shots[shot].take == take {
            return;
        }
        self.playlist.shots[shot].take = take;
        let source = Arc::clone(&self.sources[&(shot, take)]);
        let keep = self.playhead();
        match self.clip_shots.iter().position(|&s| s == shot) {
            Some(clip) => {
                let view = self.views[&(shot, take)].clone();
                self.engine.replace_clip(clip, source, view);
                let timeline = self.engine.timeline();
                let index = if keep.clip == clip { keep.index.min(timeline.clips[clip].frames.saturating_sub(1)) } else { keep.index };
                let position = timeline.clip_start(keep.clip) + index;
                // Positions after the clip moved with its length.
                self.clock = (Instant::now(), position);
                self.move_to(position);
            }
            // Its earlier take did not open, so it has no clip yet.
            None => self.rebuild(Some((self.clip_shots[keep.clip], keep.index))),
        }
        self.request_thumbnail(shot);
    }

    /// Keep the takes that finished opening, and show each that its shot is
    /// still switching to.
    fn collect_opened(&mut self) {
        for opened in self.opener.collect() {
            let (shot, take) = opened.key;
            let wanted = self.aims.opened(shot, take);
            match opened.result {
                Ok(source) => self.add_source(opened.key, source),
                Err(e) if wanted => self.open_failed(opened.key, &e),
                // Stepped past while it opened: no one is waiting for it.
                Err(_) => {}
            }
            if wanted && self.sources.contains_key(&opened.key) {
                self.show_take(shot, take);
            }
        }
    }

    /// Show, for each target shot, the take `choose` picks (None: leave it).
    fn change_takes(&mut self, choose: impl Fn(&Self, usize) -> Option<usize>) {
        let picks: Vec<(usize, usize)> = self
            .targets()
            .into_iter()
            .filter(|&shot| !self.playlist.shots[shot].takes.is_empty())
            .filter_map(|shot| Some((shot, choose(self, shot)?)))
            .collect();
        for (shot, take) in picks {
            self.set_take_of(shot, take);
        }
    }

    /// The take a switch of `shot` to `kind` shows: the one it showed last
    /// of that kind, else the newest of the most downstream department. None
    /// when it shows (or is switching to) that kind already, or has none.
    fn take_of_kind(&self, shot: usize, kind: Kind) -> Option<usize> {
        let s = self.aimed_shot(shot);
        if s.takes[s.take].kind == kind {
            return None;
        }
        let remembered = self.remembered.get(&(shot, kind)).copied();
        remembered.filter(|&t| s.takes.get(t).is_some_and(|t| t.kind == kind)).or_else(|| s.last_of_kind(kind))
    }

    fn set_view(&mut self, view: ExrView) {
        let key = self.current();
        let clip = self.playhead().clip;
        self.views.insert(key, view.clone());
        self.engine.set_view(clip, view);
        // Keep the old frame up until the new view's lands.
        self.refresh_shown();
    }

    fn set_display(&mut self) {
        self.display.error = None;
        let inputs: Vec<((usize, usize), Option<String>)> =
            self.sources.iter().filter(|(_, s)| s.is_scene_linear()).map(|(&k, s)| (k, s.colour_space().map(str::to_owned))).collect();
        for (key, input) in inputs {
            let lut = self.display.lut(input.as_deref());
            self.luts.insert(key, lut);
        }
        // EXR thumbnails went through the old display transform.
        self.thumbnails.forget(|key| self.sources.get(&key).is_some_and(|s| s.is_scene_linear()));
        for &shot in &self.clip_shots {
            self.request_thumbnail(shot);
        }
    }

    /// The latest thing that went wrong, if anything did.
    fn error(&self) -> Option<String> {
        let (_, _, errors) = self.engine.stats();
        if let Some(e) = errors.last() {
            return Some(format!("Could not decode a frame: {e}"));
        }
        if let Some(e) = self.open_errors.last() {
            return Some(format!("Could not open {e}"));
        }
        self.display.error.as_ref().map(|e| format!("Colour config: {e}; EXRs are shown without a display transform."))
    }

    fn track(&self) -> Track<Message> {
        let timeline = self.engine.timeline();
        let targets: BTreeSet<usize> = self.targets().into_iter().collect();
        let segments = (0..timeline.clips.len())
            .map(|c| {
                let index = self.clip_shots[c];
                let shot = &self.playlist.shots[index];
                // A take being opened is named at once; the clip is the old
                // take's until it has.
                let take = &shot.takes[self.aimed(index)];
                Segment {
                    start: timeline.clip_start(c),
                    len: timeline.clips[c].frames,
                    name: shot.name.clone(),
                    take: take.label.clone(),
                    kind: if self.aims.is_opening(index) { "Opening" } else { kind_word(take.kind) },
                    thumbnail: self.thumbnails.get((index, shot.take)),
                    target: targets.contains(&index),
                }
            })
            .collect();
        Track {
            segments,
            total: timeline.len(),
            buffered: track::ranges(&self.engine.buffered_positions()),
            playhead: self.position,
            on_seek: Message::Seek,
            on_pick: Message::Pick,
            on_menu: Message::TakeMenu,
        }
    }

    /// The track, with a right-click menu on each clip offering its shot's
    /// takes in Source, Department and Version submenus (see `take_menu`);
    /// choosing one switches that shot only.
    fn track_with_menu(&self) -> Element<Message> {
        let track = interactive_canvas(Id::new("track"), self.track()).width(Length::Fill).height(Length::fixed(track::HEIGHT));
        let mut menu = context_menu(Id::new("take menu"), track);
        if let Some(shot) = self.menu_shot.filter(|&s| s < self.playlist.shots.len()) {
            for submenu in take_menu::submenus(&self.playlist.shots[shot].takes, self.aimed(shot), |kind| self.take_of_kind(shot, kind)) {
                let rows = submenu.rows.into_iter().fold(MenuList::new(), |rows, (take, label)| rows.item(label, move || Message::Take(shot, take)));
                menu = menu.submenu(submenu.label, rows);
            }
        }
        menu.into()
    }

    /// The toolbar over the picture: what part of a render shows (its layer
    /// and channel) and whether the burn-ins do. Centred, like the display
    /// row under the picture.
    fn toolbar(&self) -> Element<Message> {
        let mut tools = row().spacing(8.0).align_y(Vertical::Center);
        if let Some(layers) = self.layer_controls() {
            tools = tools.push(layers).push(space(Length::fixed(12.0), Length::Shrink));
        }
        let burnins = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(checkbox(Id::new("burnins"), self.burnins).on_toggle(Message::Burnins).focusable(false))
            .push(text("Burn-ins").font_size(13.0));
        tools = tools.push(tooltip(Id::new("burnins tip"), burnins, "B"));
        container(tools).padding(6.0).width(Length::Fill).align_x(Horizontal::Center).into()
    }

    /// The burn-ins for the frame on screen: the sequence and shot, the take,
    /// its rate, the frame. None when they are off or no frame is up yet.
    fn burnin(&self) -> Option<Burnin> {
        let s = self.shown.as_ref().filter(|_| self.burnins)?;
        let shot = &self.playlist.shots[s.take.0];
        let source = &self.sources[&s.take];
        Some(Burnin {
            frame_size: (s.frame.width as f32, s.frame.height as f32),
            top_left: match &shot.sequence {
                Some(sequence) => format!("{sequence} / {}", shot.name),
                None => shot.name.clone(),
            },
            top_right: shot.takes[s.take.1].label.clone(),
            bottom_left: burnin::rate_label(source.rate()),
            bottom_right: burnin::frame_text(source.first_frame(), s.key.index, source.frame_count()),
        })
    }

    /// None for a playlist without renders, which gets no render controls.
    /// Else whether they are off: a playblast is shown as it is, so none of
    /// them apply to it. They stay (dimmed) while a playblast plays, so the
    /// video keeps its size across cuts.
    fn render_controls_off(&self) -> Option<bool> {
        if self.is_empty() || !self.playlist.shots.iter().flat_map(|s| &s.takes).any(|t| t.kind == Kind::Exr) {
            return None;
        }
        Some(self.engine.timeline().clips[self.playhead().clip].layers.is_empty())
    }

    /// The render's layer and channel, for the toolbar.
    fn layer_controls(&self) -> Option<Element<Message>> {
        let off = self.render_controls_off()?;
        let key = self.current();
        let clip = &self.engine.timeline().clips[self.playhead().clip];
        let label = |words: String| text(words).font_size(13.0).color(if off { Color::from_rgb8(120, 120, 120) } else { Color::from_rgb8(214, 214, 214) });
        let view = self.views.get(&key).cloned().unwrap_or_default();
        let layer_names: Vec<String> =
            clip.layers.iter().map(|l| if l.name.is_empty() { "(unnamed)".to_owned() } else { l.name.clone() }).collect();
        let layer_index = clip.layers.iter().position(|l| l.name == view.layer).unwrap_or(0);
        let mut channels = vec!["Colour".to_owned()];
        let mut channel_index = 0;
        if let Some(layer) = clip.layers.get(layer_index) {
            channels.extend(layer.channels.iter().cloned());
            channel_index = view.channel.as_ref().and_then(|c| layer.channels.iter().position(|x| x == c)).map_or(0, |i| i + 1);
        }
        // A playblast has no layers: the menu stays, blank and dimmed.
        let mut layers =
            combo_box(Id::new("layer"), layer_names).placeholder("").on_select(Message::Layer).width(Length::fixed(130.0)).disabled(off).focusable(false);
        if !off {
            layers = layers.selected(layer_index);
        }
        let controls = row()
            .spacing(8.0)
            .align_y(Vertical::Center)
            .push(label("Layer".to_owned()))
            .push(layers)
            .push(combo_box(Id::new("channel"), channels).selected(channel_index).on_select(Message::Channel).width(Length::fixed(110.0)).disabled(off).focusable(false));
        Some(controls.into())
    }

    /// How a render is shown: the display and view, and the exposure. On a
    /// row of its own right under the video.
    fn display_controls(&self) -> Option<Element<Message>> {
        let off = self.render_controls_off()?;
        let label = |words: String| text(words).font_size(13.0).color(if off { Color::from_rgb8(120, 120, 120) } else { Color::from_rgb8(214, 214, 214) });
        let mut controls = row().spacing(8.0).align_y(Vertical::Center);
        let displays = self.display.displays();
        if !displays.is_empty() {
            let views = self.display.views();
            let d = displays.iter().position(|d| *d == self.display.display).unwrap_or(0);
            let v = views.iter().position(|v| *v == self.display.view).unwrap_or(0);
            controls = controls
                .push(label("Display".to_owned()))
                .push(combo_box(Id::new("display"), displays).selected(d).on_select(Message::Display).width(Length::fixed(170.0)).disabled(off).focusable(false))
                .push(combo_box(Id::new("view"), views).selected(v).on_select(Message::View).width(Length::fixed(200.0)).disabled(off).focusable(false))
                .push(space(Length::fixed(12.0), Length::Shrink));
        }
        controls = controls.push(label(format!("Exposure {:+.1}", self.display.exposure))).push(
            slider(Id::new("exposure"), -8.0..=8.0, self.display.exposure, Message::Exposure)
                .step(0.5)
                .width(Length::fixed(120.0))
                .height(20.0)
                .disabled(off)
                .focusable(false),
        );
        Some(container(controls).padding(6.0).width(Length::Fill).align_x(Horizontal::Center).into())
    }
}

impl Application for Player {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        let title = if self.playlist.name.is_empty() { "TumblePipe Player".to_owned() } else { format!("{} - TumblePipe Player", self.playlist.name) };
        // Neutral greys around the image: a tinted surround shifts how its
        // colours read.
        let theme = Theme {
            background: Color::from_rgb8(30, 30, 30),
            surface: Color::from_rgb8(40, 40, 40),
            primary: Color::from_rgb8(90, 90, 90),
            secondary: Color::from_rgb8(64, 64, 64),
            border: Color::from_rgb8(76, 76, 76),
            focus: Color::from_rgb8(240, 180, 60),
            ..Theme::dark()
        };
        vec![window::Window::new(window::Id::MAIN).title(title).size(Size::new(1280.0, 900.0)).theme(theme)]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        if self.is_empty() {
            return Command::none();
        }
        match message {
            Message::Step(delta) => {
                self.playing = false;
                self.move_to(self.position.saturating_add_signed(delta as isize));
            }
            Message::Seconds(delta) => {
                self.playing = false;
                let second = self.engine.timeline().clips[self.playhead().clip].second();
                self.move_to(self.position.saturating_add_signed(delta as isize * second as isize));
            }
            Message::Clip(delta) => {
                self.playing = false;
                let timeline = self.engine.timeline();
                let clip = self.playhead().clip as i64;
                let at_start = self.playhead().index == 0;
                // Back from mid-clip goes to this clip's start, like an editor.
                let target = if delta < 0 && !at_start { clip } else { clip + delta };
                let target = target.clamp(0, timeline.clips.len() as i64 - 1) as usize;
                let start = timeline.clip_start(target);
                self.move_to(start);
            }
            Message::Seek(position) => {
                self.playing = false;
                self.move_to(position);
            }
            Message::Pick(clip, position, how) => {
                let shot = self.clip_shots[clip];
                let (selection, anchor) = track::pick(&self.selection, self.anchor, shot, how, self.current().0);
                self.selection = selection;
                self.anchor = anchor;
                if how == Pick::Only {
                    self.playing = false;
                    self.move_to(position);
                }
            }
            Message::SelectAll => self.selection = self.clip_shots.iter().copied().collect(),
            Message::ClearSelection => self.selection.clear(),
            Message::Burnins(on) => self.burnins = on,
            Message::TogglePlay => {
                self.playing = !self.playing;
                self.clock = (Instant::now(), self.position);
            }
            Message::Tick => {
                if self.playing {
                    self.advance();
                }
            }
            // The view reads the thumbnails itself.
            Message::Wake => {
                self.collect_opened();
                self.refresh_shown();
                // The thumbnails put off for the frame on screen (asking
                // again for one asked for already does nothing).
                if self.shown_is_current() {
                    for &shot in &self.clip_shots {
                        self.request_thumbnail(shot);
                    }
                }
            }
            Message::Take(shot, take) => self.set_take_of(shot, take),
            Message::TakeMenu(clip) => self.menu_shot = self.clip_shots.get(clip).copied(),
            Message::Version(delta) => self.change_takes(|p, shot| Some(p.aimed_shot(shot).version_step(delta))),
            Message::Department(delta) => self.change_takes(|p, shot| Some(p.aimed_shot(shot).department_step(delta))),
            Message::ShowKind(kind) => self.change_takes(|p, shot| p.take_of_kind(shot, kind)),
            Message::Layer(index) => {
                let clip = &self.engine.timeline().clips[self.playhead().clip];
                if let Some(layer) = clip.layers.get(index) {
                    let view = ExrView { layer: layer.name.clone(), channel: None };
                    self.set_view(view);
                }
            }
            Message::Channel(index) => {
                let key = self.current();
                let mut view = self.views.get(&key).cloned().unwrap_or_default();
                let clip = &self.engine.timeline().clips[self.playhead().clip];
                let channels = clip.layers.iter().find(|l| l.name == view.layer).map(|l| l.channels.clone()).unwrap_or_default();
                view.channel = if index == 0 { None } else { channels.get(index - 1).cloned() };
                self.set_view(view);
            }
            Message::Display(index) => {
                if let Some(d) = self.display.displays().get(index).cloned() {
                    self.display.display = d;
                    // Keep the view when the new display has it.
                    if !self.display.views().contains(&self.display.view) {
                        self.display.view = self.display.views().first().cloned().unwrap_or_default();
                    }
                    self.set_display();
                }
            }
            Message::View(index) => {
                if let Some(v) = self.display.views().get(index).cloned() {
                    self.display.view = v;
                    self.set_display();
                }
            }
            Message::Exposure(stops) => self.display.exposure = stops,
        }
        Command::none()
    }

    fn on_key(&self, _window: window::Id, key: Key, modifiers: Modifiers) -> Option<Message> {
        match key {
            Key::Named(Named::ArrowRight) if modifiers.command() => Some(Message::Clip(1)),
            Key::Named(Named::ArrowLeft) if modifiers.command() => Some(Message::Clip(-1)),
            Key::Named(Named::ArrowRight) if modifiers.alt => Some(Message::Seconds(1)),
            Key::Named(Named::ArrowLeft) if modifiers.alt => Some(Message::Seconds(-1)),
            Key::Named(Named::ArrowRight) => Some(Message::Step(1)),
            Key::Named(Named::ArrowLeft) => Some(Message::Step(-1)),
            Key::Named(Named::ArrowUp) if modifiers.shift => Some(Message::ShowKind(Kind::Movie)),
            Key::Named(Named::ArrowDown) if modifiers.shift => Some(Message::ShowKind(Kind::Exr)),
            Key::Named(Named::ArrowUp) if modifiers.command() => Some(Message::Department(-1)),
            Key::Named(Named::ArrowDown) if modifiers.command() => Some(Message::Department(1)),
            Key::Named(Named::ArrowUp) => Some(Message::Version(1)),
            Key::Named(Named::ArrowDown) => Some(Message::Version(-1)),
            Key::Character('a' | 'A') if modifiers.command() => Some(Message::SelectAll),
            Key::Named(Named::Escape) => Some(Message::ClearSelection),
            Key::Character('b' | 'B') if !modifiers.command() => Some(Message::Burnins(!self.burnins)),
            Key::Named(Named::Home) => Some(Message::Seek(0)),
            Key::Named(Named::End) => Some(Message::Seek(usize::MAX)),
            Key::Named(Named::Space) => Some(Message::TogglePlay),
            _ => None,
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        // The engine's, the opener's and the thumbnails' notifiers send
        // through this channel; the slot gets the sender once the
        // subscription starts (the id keeps it running).
        let slot = Arc::clone(&self.wake_slot);
        let engine_events = subscription::channel(subscription::hash_id(&"th_player engine"), 4, move |sender| async move {
            let mut sender = sender;
            // What finished before this subscription started had no one to
            // tell; pick it up now.
            let _ = sender.try_send(Message::Wake);
            *slot.lock().unwrap() = Some(sender);
            futures::future::pending::<()>().await;
        });
        if !self.playing || self.is_empty() {
            return engine_events;
        }
        let clip = &self.engine.timeline().clips[self.playhead().clip];
        let half_frame = Duration::from_secs_f64(f64::from(clip.rate.1) / f64::from(clip.rate.0) / 2.0);
        Subscription::batch([engine_events, time::every(half_frame, || Message::Tick)])
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let frame = self.shown.as_ref().map(|s| (s.id, s.frame.clone()));
        let lut = self.shown.as_ref().and_then(|s| self.luts.get(&s.take).cloned().flatten());
        let display = DisplaySettings { exposure: self.display.exposure, lut };
        let video = shader(Video { frame, display }).width(Length::Fill).height(Length::Fill).redraw(false);
        let picture: Element<Message> = match self.burnin() {
            Some(burnin) => stack().push(video).push(canvas(burnin).width(Length::Fill).height(Length::Fill)).width(Length::Fill).height(Length::Fill).into(),
            None => video.into(),
        };
        let mut page = column().push(self.toolbar()).push(picture);
        if let Some(display) = self.display_controls() {
            page = page.push(display);
        }
        page = page.push(self.track_with_menu());
        if self.is_empty() {
            page = page.push(container(text("Nothing to play: none of these shots has a take that opens.").font_size(13.0)).padding(6.0));
        }
        if let Some(error) = self.error() {
            page = page.push(container(text(error).font_size(13.0).color(Color::from_rgb8(235, 120, 105))).padding(6.0));
        }
        page.into()
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut ocio = None;
    if let Some(at) = args.iter().position(|a| a == "--ocio") {
        if at + 1 >= args.len() {
            eprintln!("--ocio needs a config path");
            std::process::exit(2);
        }
        ocio = Some(args.remove(at + 1));
        args.remove(at);
    }
    if args.is_empty() {
        eprintln!("usage: th_player [--ocio <config.ocio>] <playlist.otio> | <media> [<media> ...]");
        std::process::exit(2);
    }
    let playlist = if args.len() == 1 && args[0].to_ascii_lowercase().ends_with(".otio") {
        Playlist::read(&args[0])?
    } else {
        Playlist::of_paths(&args)
    };
    let display = open_display(ocio.as_deref().or(playlist.ocio.as_deref()));
    run(Player::new(playlist, display), Setup::default())?;
    Ok(())
}
