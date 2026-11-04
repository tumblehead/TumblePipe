//! The player window: the picture, the controls over and under it, and the
//! track of shots (see `track`).
//!
//! Every shot is a clip on the track from the start, as long as its cut
//! while its take opens or when the take cannot play, and as long as the
//! take once it has opened. Takes open off the UI thread. A change of take
//! applies to the shots selected on the track, else to the playhead's shot,
//! and replaces only their clips: every other shot keeps its buffered
//! frames. What is wrong is said where it shows: a take that did not open,
//! a frame that did not decode, a display transform that does not apply,
//! each while the playhead is on it.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures::channel::mpsc::Sender;
use primer::ImageHandle;
use primer::keyboard::{Key, Modifiers, Named};
use primer::prelude::*;
use primer::subscription::{self, time};
use th_media::{ExrSequence, ExrView, FrameKey, Media, Proxy};
use th_playlist::{Kind, Playlist};

use crate::burnin::{self, Burnin};
use crate::colour::{Colour, Lut};
use crate::engine::{Clip, Content, Engine, Timeline};
use crate::session::{Ask, Session, TakeKey, TakeState};
use crate::take_menu;
use crate::track::{self, Pick, Segment, Track};
use crate::video::{Picture, Video};

/// Decoded frames kept in memory (see `th_media::FrameBuffer`).
const BUFFER_BYTES: usize = 4 << 30;

#[derive(Debug, Clone)]
pub enum Message {
    /// The window is up: open the shots' takes.
    Start,
    Step(i64),
    /// Seconds to move, a second being the playhead shot's rate in frames.
    Seconds(i64),
    Clip(i64),
    Seek(usize),
    /// A click on a shot's clip: the shot, the frame under the click, and
    /// how it changes the selection.
    Pick(usize, usize, Pick),
    SelectAll,
    ClearSelection,
    TogglePlay,
    Tick,
    /// A frame decoded or a proxy made (see `waker`).
    Wake,
    /// A take opened, or failed to.
    Opened(TakeKey, Result<Arc<Media>, String>),
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
    /// Show or hide a layer (bottom first) of the stack the playhead's shot
    /// shows.
    ToggleLayer(usize),
}

/// An open take.
pub struct Opened {
    media: Arc<Media>,
    /// The EXR view shown (meaningless for a movie).
    view: ExrView,
    /// A stack's hidden layers (see `engine::is_hidden`), kept for the
    /// session: back on the take, the same layers show.
    hidden: u64,
    /// The display transform of a render's frames; None for a playblast.
    /// An error when the display and view chosen cannot show its colour
    /// space.
    lut: Result<Option<Arc<Lut>>, String>,
}

/// The display side: the OCIO config and what it shows.
struct Display {
    colour: Colour,
    display: String,
    view: String,
    exposure: f32,
}

impl Display {
    /// The display transform for `media` as the display and view show it.
    fn lut(&mut self, media: &Media) -> Result<Option<Arc<Lut>>, String> {
        media.colour_space().map(|cs| self.colour.lut(cs, &self.display, &self.view)).transpose()
    }
}

/// The frame on screen and what it is a frame of.
struct Shown {
    key: FrameKey,
    /// The take and view it was decoded from: after a switch of either it
    /// stays up until the new one's frame lands.
    take: TakeKey,
    view: ExrView,
    hidden: u64,
    picture: Picture,
}

pub struct Player {
    session: Session<Opened>,
    engine: Engine,
    /// The engine's timeline as the UI last made it: one clip per shot.
    timeline: Arc<Timeline>,
    display: Display,
    /// Each take's proxy once made, and as the track shows it.
    proxies: HashMap<TakeKey, (Proxy, Option<ImageHandle>)>,
    /// Timeline position of the playhead.
    position: usize,
    playing: bool,
    /// The playhead's frame once it is decoded, until then the last frame
    /// shown; None on a shot with nothing to show.
    shown: Option<Shown>,
    frames_shown: u64,
    /// The shots selected on the track. None selected: a change applies to
    /// the playhead's shot.
    selection: BTreeSet<usize>,
    /// The shot a Shift-click's run starts from.
    anchor: Option<usize>,
    /// Whether the burn-ins show.
    burnins: bool,
    /// The shot whose clip was last right-clicked: its takes fill the menu.
    menu_shot: Option<usize>,
    started: bool,
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

/// The media of `take` of `shot`, opened: a movie's index, or an EXR
/// sequence's first header. Runs off the UI thread; on a file server it
/// takes seconds.
fn open_media(media: &th_playlist::Media, rate: (i32, i32)) -> Result<Arc<Media>, String> {
    let media = match media {
        th_playlist::Media::Movie { path } => Media::movie(path),
        th_playlist::Media::Exr { frames, .. } => {
            let paths = (0..frames.count()).map(|index| frames.path(index)).collect();
            Media::exr(ExrSequence::new(frames.first, frames.step, paths), rate)
        }
        // Every layer at once: each is a round trip or more to the file server.
        th_playlist::Media::Stack { layers } => {
            let opened: Vec<Result<Arc<Media>, String>> = std::thread::scope(|scope| {
                let opening: Vec<_> = layers.iter().map(|layer| scope.spawn(move || open_media(&layer.media, rate))).collect();
                opening.into_iter().map(|t| t.join().unwrap_or_else(|_| Err("opening the layer panicked".into()))).collect()
            });
            let mut stack = Vec::with_capacity(layers.len());
            for (layer, media) in layers.iter().zip(opened) {
                stack.push((layer.name.clone(), media.map_err(|e| format!("layer {}: {e}", layer.name))?));
            }
            Media::stack(stack)
        }
    };
    media.map(Arc::new).map_err(|e| e.to_string())
}

impl Player {
    pub fn new(playlist: Playlist, colour: Colour) -> Self {
        let (display, view) = colour.default();
        let session = Session::new(playlist);
        let clips = (0..session.shots()).map(|shot| (cut(&session, shot), Content::none())).collect();
        let engine = Engine::start(clips, BUFFER_BYTES);
        let wake_slot: Arc<Mutex<Option<Sender<Message>>>> = Arc::new(Mutex::new(None));
        engine.set_notifier(waker(&wake_slot));
        let timeline = engine.timeline();
        let mut player = Self {
            session,
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
            burnins: true,
            menu_shot: None,
            started: false,
            wake_slot,
            clock: (Instant::now(), 0),
        };
        player.move_to(0);
        player
    }

    /// Open `key` off the UI thread.
    fn open(&self, key: TakeKey) -> Command<Message> {
        let shot = self.session.shot(key.shot);
        let (take, rate) = (shot.takes[key.take].clone(), shot.rate());
        Command::perform_blocking(move || open_media(&take.media, rate), move |result| Message::Opened(key, result))
    }

    fn playhead(&self) -> FrameKey {
        self.timeline.key(self.position)
    }

    /// The take the playhead's shot shows.
    fn current(&self) -> TakeKey {
        self.session.shown(self.playhead().clip)
    }

    fn move_to(&mut self, position: usize) {
        self.position = position.min(self.timeline.len() - 1);
        self.engine.set_playhead(self.playhead());
        self.refresh_shown();
    }

    /// Whether the playhead's frame can show: decoded, failed (its error
    /// shows), or of a shot with nothing to decode (its card shows).
    fn settled(&self, key: FrameKey) -> bool {
        self.session.open(self.session.shown(key.clip)).is_none() || self.engine.is_buffered(key) || self.engine.failure(key).is_some()
    }

    /// One playback tick: step to the next frame when the clock says it is
    /// due. Ticks come at twice the frame rate so timer slop never drags the
    /// average below the clip's rate; a frame not decoded yet stalls playback
    /// (every frame is shown) and restarts the clock from where it stalled.
    fn advance(&mut self) {
        let next = self.position + 1;
        if next >= self.timeline.len() {
            self.playing = false;
            return;
        }
        let (rate_num, rate_den) = self.timeline.clips[self.playhead().clip].rate;
        let (started, from) = self.clock;
        let due = from + (started.elapsed().as_secs_f64() * f64::from(rate_num) / f64::from(rate_den)) as usize;
        if self.position >= due {
            return;
        }
        if !self.settled(self.timeline.key(next)) {
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
        self.shown.as_ref().is_some_and(|s| {
            s.key == self.playhead()
                && s.take == self.current()
                && self.session.open(s.take).is_some_and(|o| o.view == s.view && o.hidden == s.hidden)
        })
    }

    fn refresh_shown(&mut self) {
        if self.shown_is_current() {
            return;
        }
        let (key, take) = (self.playhead(), self.current());
        let Some(opened) = self.session.open(take) else {
            // Nothing to show: its card says why.
            self.shown = None;
            return;
        };
        let Ok(lut) = opened.lut.clone() else {
            self.shown = None;
            return;
        };
        // The engine drops a clip's frames when its take or view changes, so
        // what it has for the playhead is of the take and view chosen now.
        if let Some(frame) = self.engine.frame(key) {
            self.frames_shown += 1;
            let picture = Picture { id: self.frames_shown, frame, lut };
            self.shown = Some(Shown { key, take, view: opened.view.clone(), hidden: opened.hidden, picture });
        }
    }

    /// The shots a change of take applies to: those selected, else the
    /// playhead's.
    fn targets(&self) -> Vec<usize> {
        if self.selection.is_empty() { vec![self.playhead().clip] } else { self.selection.iter().copied().collect() }
    }

    /// Ask for `take` of `shot`: an open take shows at once; any other opens
    /// off the UI thread and shows once it has.
    fn ask(&mut self, shot: usize, take: usize) -> Command<Message> {
        match self.session.ask(shot, take) {
            Ask::Nothing | Ask::Wait => Command::none(),
            Ask::Show => {
                self.show_take(shot);
                Command::none()
            }
            Ask::Open => self.open(TakeKey { shot, take }),
        }
    }

    /// Put the take `shot` shows on its clip. Only its clip changes: every
    /// other shot keeps its buffered frames, and the playhead stays on its
    /// frame (the new take's last, when that is shorter).
    fn show_take(&mut self, shot: usize) {
        let key = self.session.shown(shot);
        let (shape, content) = match self.session.open(key) {
            Some(opened) => {
                let shape = Clip { frames: opened.media.frame_count(), rate: opened.media.rate() };
                let content = Content {
                    media: Some(Arc::clone(&opened.media)),
                    view: opened.view.clone(),
                    hidden: opened.hidden,
                    wants_proxy: !self.proxies.contains_key(&key),
                };
                (shape, content)
            }
            None => (cut(&self.session, shot), Content::none()),
        };
        let keep = self.playhead();
        self.timeline = self.engine.replace_clip(shot, shape, content);
        let index = if keep.clip == shot { keep.index.min(shape.frames - 1) } else { keep.index };
        let position = self.timeline.clip_start(keep.clip) + index;
        // Positions after the clip moved with its length.
        self.clock = (Instant::now(), position);
        self.move_to(position);
    }

    /// Keep a take that opened (or did not), and show it if its shot wants it.
    fn opened(&mut self, key: TakeKey, result: Result<Arc<Media>, String>) {
        let result = result.map(|media| {
            let lut = self.display.lut(&media);
            Opened { view: media.default_view(), hidden: 0, media, lut }
        });
        if self.session.opened(key, result) {
            self.show_take(key.shot);
        }
    }

    /// Ask, for each target shot, for the take `choose` picks (None: leave it).
    fn change_takes(&mut self, choose: impl Fn(&Session<Opened>, usize) -> Option<usize>) -> Command<Message> {
        let picks: Vec<(usize, usize)> = self.targets().into_iter().filter_map(|shot| Some((shot, choose(&self.session, shot)?))).collect();
        Command::batch(picks.into_iter().map(|(shot, take)| self.ask(shot, take)).collect::<Vec<_>>())
    }

    fn set_view(&mut self, view: ExrView) {
        let (take, shot) = (self.current(), self.playhead().clip);
        if let Some(opened) = self.session.open_mut(take) {
            opened.view = view.clone();
            self.engine.set_view(shot, view);
        }
        // Keep the old frame up until the new view's lands.
        self.refresh_shown();
    }

    /// Show or hide `layer` (bottom first) of the stack the playhead's shot
    /// shows; nothing for a take that is no stack, or a layer it has not.
    /// The frame up stays until the new picture lands.
    fn toggle_layer(&mut self, layer: usize) {
        let (take, shot) = (self.current(), self.playhead().clip);
        if let Some(opened) = self.session.open_mut(take)
            && layer < opened.media.stack_layers().len().min(64)
        {
            opened.hidden ^= 1 << layer;
            self.engine.set_hidden(shot, opened.hidden);
        }
        self.refresh_shown();
    }

    /// The layers of the stack the playhead's shot shows, bottom first, and
    /// whether each shows. Empty for a take that is no stack (or not open).
    fn stack_layers(&self) -> Vec<track::LayerRow> {
        self.session.open(self.current()).map_or_else(Vec::new, |o| {
            o.media
                .stack_layers()
                .into_iter()
                .enumerate()
                .map(|(i, name)| track::LayerRow { name: name.to_owned(), shown: !crate::engine::is_hidden(o.hidden, i) })
                .collect()
        })
    }

    /// The rows over the track naming the stack's layers; None for a take
    /// that is no stack.
    fn layer_rows(&self) -> Option<Element<Message>> {
        let layers = self.stack_layers();
        if layers.is_empty() {
            return None;
        }
        let shot = self.playhead().clip;
        let rows = track::LayerRows {
            layers,
            start: self.timeline.clip_start(shot),
            len: self.timeline.clips[shot].frames,
            total: self.timeline.len(),
            on_toggle: Message::ToggleLayer,
        };
        let height = rows.height();
        Some(interactive_canvas(Id::new("layer rows"), rows).width(Length::Fill).height(Length::fixed(height)).into())
    }

    /// What the toolbar says of a stack: which of its layers show, and the
    /// keys that toggle them.
    fn layers_status(&self) -> Option<String> {
        let layers = self.stack_layers();
        if layers.is_empty() {
            return None;
        }
        let shown: Vec<&str> = layers.iter().filter(|l| l.shown).map(|l| l.name.as_str()).collect();
        let keys = match layers.len().min(9) {
            1 => "1".to_owned(),
            n => format!("1-{n}"),
        };
        let list = if shown.is_empty() { "none".to_owned() } else { shown.join(", ") };
        Some(format!("Layers {} of {} shown: {list}   ({keys} toggles)", shown.len(), layers.len()))
    }

    /// The display or view changed: every render's display transform, and
    /// every proxy of one, follow.
    fn set_display(&mut self) {
        let Self { session, display, .. } = self;
        for (_, opened) in session.open_takes_mut() {
            opened.lut = display.lut(&opened.media);
        }
        let keys: Vec<TakeKey> = self.proxies.keys().copied().collect();
        for key in keys {
            let handle = self.proxy_handle(key, &self.proxies[&key].0);
            self.proxies.get_mut(&key).unwrap().1 = handle;
        }
        self.shown = None;
        self.refresh_shown();
    }

    /// `proxy` of `key` as the track shows it: through the take's display
    /// transform when it is a render's (None when that does not apply).
    fn proxy_handle(&self, key: TakeKey, proxy: &Proxy) -> Option<ImageHandle> {
        let thumbnail = match self.session.open(key).map(|o| &o.lut) {
            Some(Ok(Some(lut))) => proxy.shown(|rgb| lut.sample(rgb)),
            Some(Ok(None)) => proxy.shown(|rgb| rgb),
            _ => return None,
        };
        Some(ImageHandle::from_rgba(thumbnail.width, thumbnail.height, thumbnail.rgba))
    }

    fn collect_proxies(&mut self) {
        for (shot, proxy) in self.engine.take_proxies() {
            let key = self.session.shown(shot);
            let handle = self.proxy_handle(key, &proxy);
            self.proxies.insert(key, (proxy, handle));
        }
    }

    /// What is wrong with what the playhead is on, if anything is.
    fn problem(&self) -> Option<String> {
        let take = self.current();
        let label = &self.session.shot(take.shot).takes[take.take].label;
        match self.session.state(take) {
            Some(TakeState::Failed(e)) => Some(format!("{label} cannot play: {e}")),
            Some(TakeState::Open(Opened { lut: Err(e), .. })) => {
                Some(format!("{label} cannot show as {} / {}: {e}", self.display.display, self.display.view))
            }
            _ => self.engine.failure(self.playhead()).map(|e| format!("Could not decode a frame of {label}: {e}")),
        }
    }

    /// What the picture says while it has no frame of the playhead's shot.
    fn card(&self) -> Option<String> {
        let take = self.current();
        let label = &self.session.shot(take.shot).takes[take.take].label;
        match self.session.state(take) {
            Some(TakeState::Opening) => Some(format!("Opening {label}…")),
            Some(TakeState::Open(Opened { lut: Ok(_), .. })) if self.engine.failure(self.playhead()).is_none() => None,
            _ => self.problem(),
        }
    }

    fn track(&self) -> Track<Message> {
        let targets: BTreeSet<usize> = self.targets().into_iter().collect();
        let segments = (0..self.session.shots())
            .map(|shot| {
                let s = self.session.shot(shot);
                // A take being opened is named at once; the clip is the old
                // take's until it has.
                let take = &s.takes[self.session.aimed(shot)];
                let shown = self.session.shown(shot);
                let kind = match self.session.state(shown) {
                    _ if self.session.is_switching(shot) => "Opening",
                    Some(TakeState::Opening) => "Opening",
                    Some(TakeState::Failed(_)) => "Cannot play",
                    _ => take_menu::take_word(take),
                };
                Segment {
                    start: self.timeline.clip_start(shot),
                    len: self.timeline.clips[shot].frames,
                    name: s.name.clone(),
                    take: take.label.clone(),
                    kind,
                    thumbnail: self.proxies.get(&shown).and_then(|p| p.1.clone()),
                    target: targets.contains(&shot),
                }
            })
            .collect();
        Track {
            segments,
            total: self.timeline.len(),
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
        if let Some(shot) = self.menu_shot {
            let takes = &self.session.shot(shot).takes;
            for submenu in take_menu::submenus(takes, self.session.aimed(shot), |kind| self.session.take_of_kind(shot, kind)) {
                let rows = submenu
                    .rows
                    .into_iter()
                    .fold(MenuList::new(), |rows, (take, label)| rows.item(label, move || Message::Take(shot, take)));
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
        if let Some(status) = self.layers_status() {
            tools = tools
                .push(text(status).font_size(13.0).color(Color::from_rgb8(214, 214, 214)))
                .push(space(Length::fixed(12.0), Length::Shrink));
        }
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
    /// its rate, the frame. None when they are off or no frame is up.
    fn burnin(&self) -> Option<Burnin> {
        let s = self.shown.as_ref().filter(|_| self.burnins)?;
        let shot = self.session.shot(s.take.shot);
        let media = &self.session.open(s.take)?.media;
        let frame = &s.picture.frame;
        Some(Burnin {
            frame_size: (frame.width as f32, frame.height as f32),
            top_left: format!("{} / {}", shot.sequence, shot.name),
            top_right: shot.takes[s.take.take].label.clone(),
            bottom_left: burnin::rate_label(media.rate()),
            bottom_right: burnin::frame_text(media.frame_number(s.key.index), s.key.index, media.frame_count()),
        })
    }

    /// None for a playlist without renders, which gets no render controls.
    /// Else whether they are off: a playblast is shown as it is, so none of
    /// them apply to it. They stay (dimmed) while a playblast plays, so the
    /// video keeps its size across cuts.
    fn render_controls_off(&self) -> Option<bool> {
        if !self.session.playlist.shots.iter().flat_map(|s| &s.takes).any(|t| t.kind() == Kind::Exr) {
            return None;
        }
        Some(self.session.open(self.current()).is_none_or(|o| o.media.layers().is_empty()))
    }

    /// The render's layer and channel, for the toolbar.
    fn layer_controls(&self) -> Option<Element<Message>> {
        let off = self.render_controls_off()?;
        let label = |words: String| {
            text(words).font_size(13.0).color(if off { Color::from_rgb8(120, 120, 120) } else { Color::from_rgb8(214, 214, 214) })
        };
        let opened = self.session.open(self.current());
        let layers = opened.map_or(&[][..], |o| o.media.layers());
        let view = opened.map(|o| o.view.clone()).unwrap_or_default();
        let layer_names: Vec<String> =
            layers.iter().map(|l| if l.name.is_empty() { "(unnamed)".to_owned() } else { l.name.clone() }).collect();
        let layer_index = layers.iter().position(|l| l.name == view.layer);
        let mut channels = vec!["Colour".to_owned()];
        let mut channel_index = 0;
        if let Some(layer) = layer_index.map(|i| &layers[i]) {
            channels.extend(layer.channels.iter().cloned());
            channel_index = view.channel.as_ref().and_then(|c| layer.channels.iter().position(|x| x == c)).map_or(0, |i| i + 1);
        }
        // A playblast has no layers: the menu stays, blank and dimmed.
        let mut layer_menu = combo_box(Id::new("layer"), layer_names)
            .placeholder("")
            .on_select(Message::Layer)
            .width(Length::fixed(130.0))
            .disabled(off)
            .focusable(false);
        if let Some(index) = layer_index.filter(|_| !off) {
            layer_menu = layer_menu.selected(index);
        }
        let controls = row().spacing(8.0).align_y(Vertical::Center).push(label("Layer".to_owned())).push(layer_menu).push(
            combo_box(Id::new("channel"), channels)
                .selected(channel_index)
                .on_select(Message::Channel)
                .width(Length::fixed(110.0))
                .disabled(off)
                .focusable(false),
        );
        Some(controls.into())
    }

    /// How a render is shown: the display and view, and the exposure. On a
    /// row of its own right under the video.
    fn display_controls(&self) -> Option<Element<Message>> {
        // On for whatever goes through the display transform: a render, and a
        // stack of render layers, which has no layers of its own to pick.
        self.render_controls_off()?;
        let off = self.session.open(self.current()).is_none_or(|o| o.media.colour_space().is_none());
        let label = |words: String| {
            text(words).font_size(13.0).color(if off { Color::from_rgb8(120, 120, 120) } else { Color::from_rgb8(214, 214, 214) })
        };
        let displays = self.display.colour.displays();
        let views = self.display.colour.views(&self.display.display);
        let d = displays.iter().position(|d| *d == self.display.display).unwrap_or(0);
        let v = views.iter().position(|v| *v == self.display.view).unwrap_or(0);
        let controls = row()
            .spacing(8.0)
            .align_y(Vertical::Center)
            .push(label("Display".to_owned()))
            .push(
                combo_box(Id::new("display"), displays)
                    .selected(d)
                    .on_select(Message::Display)
                    .width(Length::fixed(170.0))
                    .disabled(off)
                    .focusable(false),
            )
            .push(
                combo_box(Id::new("view"), views)
                    .selected(v)
                    .on_select(Message::View)
                    .width(Length::fixed(200.0))
                    .disabled(off)
                    .focusable(false),
            )
            .push(space(Length::fixed(12.0), Length::Shrink))
            .push(label(format!("Exposure {:+.1}", self.display.exposure)))
            .push(
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

/// `shot` as its cut: how long it stands in the sequence while its take
/// cannot play.
fn cut(session: &Session<Opened>, shot: usize) -> Clip {
    let s = session.shot(shot);
    Clip { frames: s.length as usize, rate: s.rate() }
}

/// Neutral greys around the image: a tinted surround shifts how its colours
/// read. `primary` marks what is on (a ticked checkbox, a slider's fill, the
/// highlighted menu row), so it stands well clear of the surface.
pub fn theme() -> Theme {
    Theme {
        background: Color::from_rgb8(30, 30, 30),
        surface: Color::from_rgb8(40, 40, 40),
        primary: Color::from_rgb8(175, 175, 175),
        secondary: Color::from_rgb8(64, 64, 64),
        border: Color::from_rgb8(76, 76, 76),
        focus: Color::from_rgb8(240, 180, 60),
        ..Theme::dark()
    }
}

impl Application for Player {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        let name = &self.session.playlist.name;
        let title = if name.is_empty() { "TumblePipe Player".to_owned() } else { format!("{name} - TumblePipe Player") };
        vec![window::Window::new(window::Id::MAIN).title(title).size(Size::new(1280.0, 900.0)).theme(theme())]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Start if !self.started => {
                self.started = true;
                return Command::batch(self.session.shown_keys().into_iter().map(|key| self.open(key)).collect::<Vec<_>>());
            }
            Message::Start => {}
            Message::Step(delta) => {
                self.playing = false;
                self.move_to(self.position.saturating_add_signed(delta as isize));
            }
            Message::Seconds(delta) => {
                self.playing = false;
                let second = self.timeline.clips[self.playhead().clip].second();
                self.move_to(self.position.saturating_add_signed(delta as isize * second as isize));
            }
            Message::Clip(delta) => {
                self.playing = false;
                let clip = self.playhead().clip as i64;
                let at_start = self.playhead().index == 0;
                // Back from mid-clip goes to this clip's start, like an editor.
                let target = if delta < 0 && !at_start { clip } else { clip + delta };
                let target = target.clamp(0, self.timeline.clips.len() as i64 - 1) as usize;
                self.move_to(self.timeline.clip_start(target));
            }
            Message::Seek(position) => {
                self.playing = false;
                self.move_to(position);
            }
            Message::Pick(shot, position, how) => {
                let (selection, anchor) = track::pick(&self.selection, self.anchor, shot, how, self.playhead().clip);
                self.selection = selection;
                self.anchor = anchor;
                if how == Pick::Only {
                    self.playing = false;
                    self.move_to(position);
                }
            }
            Message::SelectAll => self.selection = (0..self.session.shots()).collect(),
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
            Message::Wake => {
                self.collect_proxies();
                self.refresh_shown();
            }
            Message::Opened(key, result) => self.opened(key, result),
            Message::Take(shot, take) => return self.ask(shot, take),
            Message::TakeMenu(shot) => self.menu_shot = Some(shot),
            Message::Version(delta) => return self.change_takes(|s, shot| Some(s.version_step(shot, delta))),
            Message::Department(delta) => return self.change_takes(|s, shot| Some(s.department_step(shot, delta))),
            Message::ShowKind(kind) => return self.change_takes(|s, shot| s.take_of_kind(shot, kind)),
            Message::Layer(index) => {
                let layer = self.session.open(self.current()).and_then(|o| o.media.layers().get(index)).map(|l| l.name.clone());
                if let Some(layer) = layer {
                    self.set_view(ExrView { layer, channel: None });
                }
            }
            Message::Channel(index) => {
                if let Some(opened) = self.session.open(self.current()) {
                    let mut view = opened.view.clone();
                    let channels =
                        opened.media.layers().iter().find(|l| l.name == view.layer).map(|l| l.channels.clone()).unwrap_or_default();
                    view.channel = if index == 0 { None } else { channels.get(index - 1).cloned() };
                    self.set_view(view);
                }
            }
            Message::Display(index) => {
                if let Some(d) = self.display.colour.displays().get(index).cloned() {
                    self.display.display = d;
                    // Keep the view when the new display has it.
                    let views = self.display.colour.views(&self.display.display);
                    if !views.contains(&self.display.view) {
                        // `displays` lists only displays with a view.
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
            Message::ToggleLayer(layer) => self.toggle_layer(layer),
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
            Key::Character(digit @ '1'..='9') if !modifiers.command() && !modifiers.alt => {
                Some(Message::ToggleLayer(digit as usize - '1' as usize))
            }
            _ => None,
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        // The engine's notifier sends through this channel; the slot gets
        // the sender once the subscription starts (the id keeps it running),
        // which is also when the takes start opening.
        let slot = Arc::clone(&self.wake_slot);
        let engine_events = subscription::channel(subscription::hash_id(&"th_player engine"), 4, move |sender| async move {
            let mut sender = sender;
            let _ = sender.try_send(Message::Start);
            *slot.lock().unwrap() = Some(sender);
            futures::future::pending::<()>().await;
        });
        if !self.playing {
            return engine_events;
        }
        let clip = &self.timeline.clips[self.playhead().clip];
        let half_frame = Duration::from_secs_f64(f64::from(clip.rate.1) / f64::from(clip.rate.0) / 2.0);
        Subscription::batch([engine_events, time::every(half_frame, || Message::Tick)])
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let picture = self.shown.as_ref().map(|s| s.picture.clone());
        let video = shader(Video { picture, exposure: self.display.exposure }).width(Length::Fill).height(Length::Fill).redraw(false);
        let mut layers = stack().push(video).width(Length::Fill).height(Length::Fill);
        if let Some(burnin) = self.burnin() {
            layers = layers.push(canvas(burnin).width(Length::Fill).height(Length::Fill));
        }
        if let Some(card) = self.card().filter(|_| self.shown.is_none()) {
            let words = text(card).font_size(15.0).color(Color::from_rgb8(200, 200, 200));
            layers = layers
                .push(container(words).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center));
        }
        let mut page = column().push(self.toolbar()).push(layers);
        if let Some(display) = self.display_controls() {
            page = page.push(display);
        }
        if let Some(rows) = self.layer_rows() {
            page = page.push(rows);
        }
        page = page.push(self.track_with_menu());
        if let Some(problem) = self.problem() {
            page = page.push(container(text(problem).font_size(13.0).color(Color::from_rgb8(235, 120, 105))).padding(6.0));
        }
        page.into()
    }
}
