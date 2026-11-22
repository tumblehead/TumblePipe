//! The bins beside the viewer: the project's shots under their sequences'
//! names, and its media library, with what puts them in the cut.
//!
//! A double-click (or Enter) on a row inserts it at the playhead: a shot or
//! a media item itself, a sequence's name every shot of the sequence in
//! natural name order, back to back, as one undo step (`bins::put_run`).
//! With Shift held it is laid over what is there instead ([`with_keys`]).
//! Insert and Overlay put the picked item the same two ways.
//!
//! Each shot and media item has a thumbnail (`bin_thumbs`), read off the UI
//! thread for the rows on screen only. **Grid** shows the bin as tiles, so
//! many a line as the panel's width holds (`bins::grid_rows`), else it is a
//! list; the choice is this user's (`layout_prefs`). Both are the same
//! windowed list underneath, and both put things in the cut the same way:
//! the list reports its double-click itself, a tile's is two presses on it
//! (`bins::completes_double_click`), and either goes through
//! [`with_keys`] to [`BinMessage::Activate`]'s handler.
//!
//! A row is a line: its thumbnail and its name, and why it has no picture
//! when it has none. What else it is (a shot's length and range, a media
//! item's version) is said when the pointer rests on it (`bins::row_tip`;
//! Primer's list shows no tooltip of a row, so `sense` reports the row the
//! pointer is on and floats the words). The chevron before a sequence's
//! name folds its shots away and brings them back, remembered for this
//! user and project (`layout_prefs::save_folded`); a folded sequence's
//! name still stands for all its shots.
//!
//! Ctrl-click (Cmd on macOS) adds a row to the picked ones or takes it
//! out; Insert and Overlay put all of them, in the bin's order.

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use primer::keyboard::Modifiers;
use primer::prelude::*;
use primer::widget::custom::{Cursor, Event, Sizing, mouse};
use th_edit::document::{Frame as EditFrame, TrackKind};
use th_pipe::edits::EditSources;

use super::file_dialogs::OpenDialog;
use super::{Editor, Message};
use crate::bin_drag::{self, Drag, Landing, Lanes, Over};
use crate::bin_thumbs::{self, Loaded, Shown, ThumbKey, Thumbs};
use crate::bins::{self, BinRow, Clips, GridRow, Target};
use crate::by_width::by_width;
use crate::display::Display;
use crate::layout_prefs;
use crate::look;
use crate::modified::modified;
use crate::sense::{Float, Mark, sense};

const INSERT_TIP: &str = "Put the pick at the playhead; what follows moves on. Double-click a shot or media item does the same, and double-click a sequence's name inserts all its shots in order. Ctrl-click picks several";
const OVERLAY_TIP: &str = "Lay the pick over what is at the playhead, on a track with room. Shift+double-click a shot or media item does the same, and Shift+double-click a sequence's name overlays all its shots";

/// A bin's messages with the keys held at them: Shift lays what a
/// double-click (or Enter) names over the cut, where a plain one inserts
/// it; Ctrl (Cmd on macOS) makes a click add its row to the picked ones, or
/// take it out, where a plain one picks it alone.
fn with_keys(message: Message, modifiers: Modifiers) -> Message {
    match message {
        Message::Bin(BinMessage::Activate(target, _)) => Message::Bin(BinMessage::Activate(target, modifiers.shift)),
        Message::Bin(BinMessage::Press(Target::Item(index), _)) if modifiers.command() => Message::Bin(BinMessage::Toggle(index)),
        Message::Bin(BinMessage::Press(target, _)) => Message::Bin(BinMessage::Press(target, modifiers.shift)),
        Message::BinPick(index) if modifiers.command() => Message::Bin(BinMessage::Toggle(index)),
        other => other,
    }
}

const GRID_TIP: &str = "Show the bin as a grid of thumbnails, or as a list. Remembered for next time";

/// A list row's height, and its thumbnail's size (16:9).
pub(super) const LIST_ROW: f32 = 26.0;
const LIST_THUMB: (f32, f32) = (39.0, 22.0);
/// A sequence's name on its line of the grid.
pub(super) const GRID_HEADING: f32 = 30.0;
/// How long the pointer rests on a row before its tip shows.
pub(super) const TIP_DELAY: Duration = Duration::from_millis(500);

/// The row the pointer is on: the item, where its row is in the window,
/// and since when.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct Hover {
    pub(super) index: usize,
    pub(super) rect: Rectangle,
    pub(super) since: Instant,
}

/// What the bins panel keeps between frames.
#[derive(Debug, Default)]
pub(super) struct BinState {
    pub(super) thumbs: Thumbs,
    /// The grid tile (or sequence's name) last pressed, and when: a second
    /// press on it in time is a double-click.
    pub(super) last_press: Option<(Target, Instant)>,
    /// The sequences whose shots are folded away in the Shots bin.
    pub(super) folded: BTreeSet<String>,
    /// The items picked together (`bins::picked`: they count while the
    /// editor's pick is one of them).
    pub(super) picks: BTreeSet<usize>,
    pub(super) hover: Option<Hover>,
    /// A row pressed, or carried out of the bin (`bin_drag`).
    pub(super) drag: Drag,
    /// Where what is carried would land, worked out when the pointer or
    /// the key held changed: what it is over, how long the run is, and the
    /// ops or why not. None while it is over no row of the tracks.
    pub(super) landing: Option<(Over, EditFrame, Result<Landing, String>)>,
    /// The row's menu, the clipboard, Import's folder (`bin_menu.rs`).
    pub(super) more: super::bin_menu::More,
}

impl BinState {
    /// The state a session starts with: the sequences this user left
    /// folded in `project` (its root), by the file beside `layout`.
    pub(super) fn remembered(layout: Option<&Path>, project: &str) -> Self {
        let folded = layout.map(|path| layout_prefs::load_folded(&layout_prefs::folded_path(path), project)).unwrap_or_default();
        Self { folded, ..Self::default() }
    }

    /// The other bin is on screen: what was picked and pointed at was of
    /// the one before.
    pub(super) fn tab_changed(&mut self) {
        self.picks.clear();
        self.hover = None;
        self.last_press = None;
        self.drag = Drag::Idle;
        self.landing = None;
    }
}

/// What a row shows for its thumbnail, owned by the view.
#[derive(Debug, Clone)]
pub(super) enum Picture {
    Pending,
    Image(primer::ImageHandle),
    Nothing(String),
}

impl Picture {
    /// Why there is no picture, when the row should say so.
    fn note(&self) -> Option<&str> {
        match self {
            Picture::Nothing(why) if !why.is_empty() => Some(why),
            _ => None,
        }
    }
}

/// A thumbnail's box, `width` by `height`: its picture fitted inside, on
/// the surround's grey (so is one still being read, or with nothing).
/// `says` writes why there is no picture inside the box (a grid tile, which
/// has no line for it).
fn thumb_box(picture: &Picture, width: Length, height: f32, says: bool) -> Element<Message> {
    let inside: Element<Message> = match picture {
        Picture::Image(handle) => image(handle.clone()).width(Length::Fill).height(Length::Fill).content_fit(ContentFit::Contain).into(),
        Picture::Nothing(why) if says && !why.is_empty() => container(text(why.clone()).font_size(10.0).color(look::caution()))
            .padding(4.0)
            .width(Length::Fill)
            .height(Length::Fill)
            .align_y(Vertical::Center)
            .into(),
        Picture::Pending | Picture::Nothing(_) => space(Length::Fill, Length::Fill).into(),
    };
    container(inside).width(width).height(Length::fixed(height)).background(look::surround()).into()
}

/// The chevron before a sequence's name: it folds the sequence's shots
/// away, or brings them back.
fn chevron(tab: usize, at: usize, name: &str, folded: bool) -> Element<Message> {
    let name = name.to_owned();
    button(Id::new(format!("bin fold {tab} {at}")), text(if folded { "\u{25b8}" } else { "\u{25be}" }).font_size(12.0).color(look::quiet()))
        .background(Color::TRANSPARENT)
        .padding(Padding::from([0.0, 4.0]))
        .focusable(false)
        .on_press(move || Message::Bin(BinMessage::Fold(name.clone())))
        .into()
}

/// What a row's watcher sends as the pointer moves: onto an item's row
/// (not the one it is on already), that row; onto a sequence's name, or
/// with nothing under it, no row.
fn row_hover(item: Option<usize>, hovered: Option<usize>, event: &Event, bounds: Rectangle, cursor: Cursor) -> Option<Message> {
    if !matches!(event, Event::Mouse(mouse::Event::CursorMoved { .. })) || !cursor.is_over(bounds) || item == hovered {
        return None;
    }
    Some(Message::Bin(BinMessage::Hover(item.map(|index| (index, bounds)))))
}

/// What a row knows of the panel when it is built, to answer the pointer.
#[derive(Debug, Clone, Copy)]
struct Watch {
    /// The item whose row the pointer is on.
    hovered: Option<usize>,
    /// Something is carried: rows say nothing.
    carrying: bool,
}

/// What the watcher of the row that stands for `target` sends: a left
/// press on it takes hold of it (not with Ctrl or Cmd held, which picks),
/// and the pointer coming onto it says so (`row_hover`).
fn row_event(target: &Target, watch: Watch, event: &Event, bounds: Rectangle, cursor: Cursor, modifiers: Modifiers) -> Option<Message> {
    if watch.carrying {
        return None;
    }
    let item = match target {
        Target::Item(index) => Some(*index),
        _ => None,
    };
    match event {
        Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) if !modifiers.command() => {
            cursor.position_in(bounds).map(|at| Message::Bin(BinMessage::Hold(target.clone(), at)))
        }
        Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) => {
            cursor.position_in(bounds).and(cursor.position()).map(|at| Message::Bin(BinMessage::Menu(target.clone(), at)))
        }
        _ if item.is_none() && watch.hovered.is_none() => None,
        _ => row_hover(item, watch.hovered, event, bounds, cursor),
    }
}

/// What the panel's watcher sends while a row is held or carried: where
/// the pointer goes and whether the key that inserts is held, and the let
/// go. `insert` is what the carry last knew of the key, so pressing or
/// letting go of it without moving is seen too.
fn panel_event(drag: &Drag, event: &Event, cursor: Cursor, modifiers: Modifiers) -> Option<Message> {
    let now = bin_drag::inserts(modifiers);
    match (drag, event) {
        (Drag::Idle, _) => None,
        (_, Event::Mouse(mouse::Event::CursorMoved { .. })) => cursor.position().map(|at| Message::Bin(BinMessage::Moved(at, now))),
        (_, Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left))) => Some(Message::Bin(BinMessage::Dropped(now))),
        (Drag::Carried { at, insert, .. }, _) if *insert != now => Some(Message::Bin(BinMessage::Moved(*at, now))),
        _ => None,
    }
}

/// Where what is carried would land, as it was last worked out.
type Tried = (Over, EditFrame, Result<Landing, String>);

/// The boxes over tracks `size` big for `landing`: where each clip would
/// go, named, or the run's length on the row under the pointer, marked as
/// refused with why.
fn marks_of(landing: Option<&Tried>, lanes: &Lanes, size: Size) -> Vec<Mark> {
    let Some((over, length, tried)) = landing else { return Vec::new() };
    match tried {
        Ok(landing) => bin_drag::spans(&landing.ops)
            .into_iter()
            .filter_map(|(track, at, duration, source)| {
                Some(Mark { rect: lanes.rect(track, at, duration, size.width)?, words: super::source_name(&source), refused: false })
            })
            .collect(),
        Err(why) => lanes
            .rows
            .get(over.row)
            .and_then(|&(track, _)| lanes.rect(track, over.frame, (*length).max(1), size.width))
            .map(|rect| Mark { rect, words: why.clone(), refused: true })
            .into_iter()
            .collect(),
    }
}

/// What the tracks' watcher sends while something is carried: what the
/// pointer is over now, when that is not what it was over.
fn tracks_event(lanes: &Lanes, was: Option<Over>, event: &Event, bounds: Rectangle, cursor: Cursor) -> Option<Message> {
    if !matches!(event, Event::Mouse(mouse::Event::CursorMoved { .. })) {
        return None;
    }
    let now = cursor.position_relative_to(bounds).and_then(|point| lanes.over(point, bounds.width));
    (now != was).then_some(Message::Bin(BinMessage::Over(now)))
}

/// What the list's watcher sends when the pointer leaves the row pointed
/// at (`hovered`, where that row is): no row. It is asked before the rows,
/// so a row the pointer moved onto says so after.
fn list_left(hovered: Option<Rectangle>, event: &Event, bounds: Rectangle, cursor: Cursor) -> Option<Message> {
    let row = hovered?;
    let gone = match event {
        Event::Mouse(mouse::Event::CursorMoved { .. }) => !cursor.is_over(row) || !cursor.is_over(bounds),
        Event::Mouse(mouse::Event::CursorLeft) => true,
        _ => false,
    };
    gone.then_some(Message::Bin(BinMessage::Hover(None)))
}

/// The display transform for scene-linear pictures in `space`.
fn display_transform(display: &mut Display, space: &str) -> bin_thumbs::Shows {
    let lut = display.colour.lut(space, &display.display, &display.view)?;
    Ok(Box::new(move |rgb| lut.sample(rgb)))
}

/// What the bins panel sends.
#[derive(Debug, Clone)]
pub enum BinMessage {
    /// A double-click (or Enter) on a row: put what it names at the
    /// playhead, inserted, or (true) over what is there.
    Activate(Target, bool),
    /// A press on a grid tile or a sequence's name in the grid, and whether
    /// Shift was held: it picks, and a second in time is a double-click.
    Press(Target, bool),
    /// Show the bins as a grid of thumbnails, or as a list.
    Grid(bool),
    /// A thumbnail was read (or why not).
    Thumb(ThumbKey, Result<Loaded, String>),
    /// The chevron before a sequence's name: fold its shots away, or bring
    /// them back.
    Fold(String),
    /// A click with Ctrl (Cmd) held: the item joins the picked ones, or
    /// leaves them.
    Toggle(usize),
    /// The pointer is on an item's row (the item, the row in the window),
    /// or on none.
    Hover(Option<(usize, Rectangle)>),
    /// A left press on the row that stands for this, at this point of the
    /// window: it is held, and carried if the pointer moves on.
    Hold(Target, Point),
    /// The pointer moved with a row held or carried, and whether the key
    /// that inserts is held.
    Moved(Point, bool),
    /// What the pointer is over on the tracks while something is carried.
    Over(Option<Over>),
    /// The button was let go, and whether the key that inserts was held.
    Dropped(bool),
    /// Escape: what is carried goes nowhere.
    Cancel,
    /// A right press on the row that stands for this, at this point: its
    /// menu opens (`bin_menu.rs`).
    Menu(Target, Point),
    /// A right press on the list at this point, on a row or off them.
    MenuAt(Point),
    /// A row of the menu.
    Act(super::bin_menu::Act, Target),
    /// What a menu row looked up off the UI thread.
    Found(Result<super::bin_menu::Found, String>),
    /// The words sent to the clipboard are on it.
    Copied,
    /// Put these words on the clipboard.
    Copy(String),
    /// Open Farm Submit ticked on these shots (never a submission).
    Farm(Vec<String>),
}

impl Editor {
    /// The items picked in the bin on screen, in its order.
    pub(super) fn bin_picked(&self) -> Vec<usize> {
        bins::picked(self.bin_pick, &self.bin_state.picks)
    }

    /// Put what is picked in the bin at the playhead: all of it, in the
    /// bin's order.
    pub(super) fn bin_put_picked(&mut self, overlay: bool) -> Command<Message> {
        match bins::picked_target(&self.bin_picked()) {
            Some(target) => self.bin_put_target(&target, overlay),
            None => {
                self.status = Some(("Pick a shot or media item in the bins first".into(), true));
                Command::none()
            }
        }
    }

    pub(super) fn bin_message(&mut self, message: BinMessage) -> Command<Message> {
        match message {
            BinMessage::Activate(target, overlay) => self.bin_put_target(&target, overlay),
            BinMessage::Press(target, shift) => {
                let now = Instant::now();
                if bins::completes_double_click(self.bin_state.last_press.as_ref(), &target, now) {
                    self.bin_state.last_press = None;
                    return self.bin_put_target(&target, shift);
                }
                // As in the list: a sequence's name picks its first shot.
                let pick = bins::target_items(&target, Some(&self.bins.shots), usize::MAX).first().copied();
                self.bin_pick = pick.or(self.bin_pick);
                self.bin_state.last_press = Some((target, now));
                Command::none()
            }
            BinMessage::Fold(sequence) => {
                if !self.bin_state.folded.remove(&sequence) {
                    self.bin_state.folded.insert(sequence);
                }
                self.bin_state.hover = None;
                let project = th_pipe::storage::slashed(&self.paths.root);
                if let Some(path) = self.layout_path.as_deref().map(layout_prefs::folded_path)
                    && let Err(e) = layout_prefs::save_folded(&path, &project, &self.bin_state.folded)
                {
                    eprintln!("cinedex: the folded sequences were not saved to {}: {e}", path.display());
                }
                Command::none()
            }
            BinMessage::Toggle(index) => {
                let (picks, pick) = bins::toggled(&self.bin_picked(), index);
                self.bin_state.picks = picks;
                self.bin_pick = pick;
                Command::none()
            }
            BinMessage::Hover(row) => {
                self.bin_state.hover = row.map(|(index, rect)| Hover { index, rect, since: Instant::now() });
                Command::none()
            }
            BinMessage::Hold(row, from) => {
                let target = bins::held_target(&row, &self.bin_picked());
                self.bin_state.drag = Drag::Held { target, from };
                self.bin_state.landing = None;
                Command::none()
            }
            BinMessage::Moved(to, insert) => {
                self.bin_state.drag = std::mem::take(&mut self.bin_state.drag).moved(to, insert);
                if self.bin_state.drag.carried().is_some() {
                    self.bin_state.hover = None;
                    self.bin_state.last_press = None;
                }
                self.bin_try_landing();
                Command::none()
            }
            BinMessage::Over(over) => {
                self.bin_state.drag = std::mem::take(&mut self.bin_state.drag).over(over);
                self.bin_try_landing();
                Command::none()
            }
            BinMessage::Dropped(insert) => {
                self.bin_state.landing = None;
                match std::mem::take(&mut self.bin_state.drag) {
                    Drag::Carried { target, over: Some(over), .. } => self.bin_drop(&target, over, insert),
                    // Let go where it was pressed: a click. On one of
                    // several picked rows it picks that row alone.
                    Drag::Held { .. } => {
                        self.bin_state.picks.clear();
                        Command::none()
                    }
                    // Let go off the tracks: it goes nowhere.
                    _ => Command::none(),
                }
            }
            BinMessage::Menu(row, at) => {
                self.bin_menu_open(&row, at);
                Command::none()
            }
            BinMessage::MenuAt(at) => {
                self.bin_menu_at(at);
                Command::none()
            }
            BinMessage::Act(act, target) => self.bin_act(act, &target),
            BinMessage::Found(found) => self.bin_found(found),
            BinMessage::Copy(words) => {
                self.bin_state.more.copy = Some(words);
                Command::none()
            }
            BinMessage::Farm(shots) => {
                self.farm_submit(&shots);
                Command::none()
            }
            BinMessage::Copied => {
                self.bin_copied();
                Command::none()
            }
            BinMessage::Cancel => {
                self.bin_state.drag = Drag::Idle;
                self.bin_state.landing = None;
                Command::none()
            }
            BinMessage::Grid(grid) => {
                let mut layout = self.layout;
                layout.bins_grid = grid;
                self.set_layout(layout);
                Command::none()
            }
            BinMessage::Thumb(key, result) => {
                let Self { bin_state, display, .. } = self;
                bin_state.thumbs.arrived(&key, result, |space| display_transform(display, space));
                self.bin_thumbs_pump()
            }
        }
    }

    /// What each item of the bin on screen has its thumbnail by, in the
    /// bin's order.
    fn bin_keys(&self) -> Vec<ThumbKey> {
        match self.bin_tab {
            0 => self.bins.shots.iter().map(|s| ThumbKey::Shot(s.uri.clone())).collect(),
            _ => self.bins.media.iter().map(|m| ThumbKey::Item { item: m.item.clone(), version: m.version.clone() }).collect(),
        }
    }

    /// Start reading the thumbnails of the rows on screen that have none
    /// yet, a few at a time (`bin_thumbs`). Called on the journal's tick
    /// and whenever one comes back.
    pub(super) fn bin_thumbs_pump(&mut self) -> Command<Message> {
        if self.mode != 0 || self.layout.bins_collapsed {
            return Command::none();
        }
        let order = self.bin_keys();
        let start = self.bin_state.thumbs.start(&order, Instant::now());
        let fps = self.fps();
        Command::batch(start.into_iter().map(|key| {
            let (paths, wanted) = (self.paths.clone(), key.clone());
            Command::perform_blocking(
                move || bin_thumbs::load(&paths, &wanted, fps),
                move |result| Message::Bin(BinMessage::Thumb(key.clone(), result)),
            )
        }))
    }

    /// The display or view changed: the scene-linear thumbnails follow.
    pub(super) fn bin_thumbs_redisplay(&mut self) {
        let Self { bin_state, display, .. } = self;
        bin_state.thumbs.redisplay(|space| display_transform(display, space));
    }

    /// The journal's `changes` (None: anything may have changed, as on
    /// Refresh): a shot with a new playblast or render gets its thumbnail
    /// read again when it is next on screen.
    pub(super) fn bin_thumbs_changed(&mut self, changes: Option<&[th_pipe::events::Change]>) {
        use th_pipe::events;
        let Some(changes) = changes else { return self.bin_state.thumbs.clear() };
        for change in changes.iter().filter(|c| c.project == self.journal_key) {
            if matches!(change.kind.as_str(), events::PLAYBLAST | events::RENDER) {
                self.bin_state.thumbs.forget_shot(&change.uri);
            }
        }
    }

    /// Put `target` of the bin on screen at the playhead: inserted
    /// (rippling), or over what is there. A sequence is all its shots, in
    /// natural name order, back to back; one undo step either way.
    pub(super) fn bin_put_target(&mut self, target: &Target, overlay: bool) -> Command<Message> {
        let at = self.position as EditFrame;
        let makers = self.bin_makers(target);
        if makers.is_empty() {
            return Command::none();
        }
        let track = self.targets().first().map(|c| c.track).filter(|t| t.kind == TrackKind::Video).map_or(0, |t| t.index);
        let run = self.paths.open().map_err(th_edit::ops::Refused).and_then(|project| {
            let sources = EditSources::new(&project, self.fps());
            bins::put_run(&self.doc.edit, at, track, overlay, makers, &sources)
        });
        match run {
            Ok((ops, end)) => self.edit_then_park(&ops, end),
            Err(e) => {
                self.status = Some((format!("Not done: {e}"), true));
                Command::none()
            }
        }
    }

    /// Where the tracks' rows are, as the timeline draws them.
    pub(super) fn bin_lanes(&self) -> Lanes {
        Lanes { rows: self.rows().iter().map(|row| (row.track, row.height)).collect(), total: self.timeline_view.span() }
    }

    /// Work out where what is carried would land, with every frame taken
    /// as there (`bin_drag::AnyFrames`): what the placeholder shows.
    fn bin_try_landing(&mut self) {
        let landing = match &self.bin_state.drag {
            Drag::Carried { target, over: Some(over), insert, .. } => self.bin_lanes().rows.get(over.row).map(|&(track, _)| {
                let makers = self.bin_makers(target);
                let length = makers
                    .iter()
                    .filter_map(|make| {
                        let (v, a) = make(over.frame);
                        crate::editing::put_end(v.as_ref(), a.as_ref())
                    })
                    .max()
                    .map_or(0, |end| end - over.frame);
                let tried =
                    bin_drag::landing(&self.doc.edit, !self.doc.read_only(), track, over.frame, *insert, &makers, &bin_drag::AnyFrames);
                (*over, length, tried.map_err(|e| e.to_string()))
            }),
            _ => None,
        };
        self.bin_state.landing = landing;
    }

    /// Drop `target` at `over`: the same ops the placeholder showed, asked
    /// of the project's own frames this time; one undo step, the playhead
    /// to the end of what was put.
    fn bin_drop(&mut self, target: &Target, over: Over, insert: bool) -> Command<Message> {
        let Some(&(track, _)) = self.bin_lanes().rows.get(over.row) else { return Command::none() };
        let makers = self.bin_makers(target);
        let landing = self.paths.open().map_err(th_edit::ops::Refused).and_then(|project| {
            let sources = EditSources::new(&project, self.fps());
            bin_drag::landing(&self.doc.edit, !self.doc.read_only(), track, over.frame, insert, &makers, &sources)
        });
        drop(makers);
        match landing {
            Ok(landing) => self.edit_then_park(&landing.ops, landing.end),
            Err(e) => {
                self.status = Some((format!("Not dropped: {e}"), true));
                Command::none()
            }
        }
    }

    /// Escape while a row is held or carried lets it go nowhere.
    pub(super) fn bin_escape(&self, key: &primer::keyboard::Key) -> Option<Message> {
        let escape = matches!(key, primer::keyboard::Key::Named(primer::keyboard::Named::Escape));
        (escape && self.bin_state.drag != Drag::Idle).then_some(Message::Bin(BinMessage::Cancel))
    }

    /// The boxes over the tracks `size` big while something is carried
    /// over them: where each clip would go, named, or the run's length on
    /// the row under the pointer, marked as refused.
    #[cfg(test)]
    pub(super) fn bin_marks(&self, size: Size) -> Vec<Mark> {
        marks_of(self.bin_state.landing.as_ref(), &self.bin_lanes(), size)
    }

    /// What follows the pointer while something is carried: what it is,
    /// and what letting go here does (or why it cannot land here).
    pub(super) fn bin_ghost(&self) -> Option<Float> {
        let Drag::Carried { target, at, insert, .. } = &self.bin_state.drag else { return None };
        let shots = (self.bin_tab == 0).then_some(self.bins.shots.as_slice());
        let count = if self.bin_tab == 0 { self.bins.shots.len() } else { self.bins.media.len() };
        let names: Vec<String> =
            bins::target_items(target, shots, count).into_iter().filter_map(|i| self.bin_item_words(i)).map(|w| w.0).collect();
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let what = bins::carried_words(target, &names, self.bin_tab == 0);
        let key = bin_drag::insert_key();
        let (does, caution) = match &self.bin_state.landing {
            Some((_, _, Err(why))) => (why.clone(), true),
            _ if *insert => ("insert, what follows moves on".to_owned(), false),
            _ => (format!("lay over ({key}: insert)"), false),
        };
        Some(Float { words: format!("{what} \u{b7} {does}"), at: Point::new(at.x + 14.0, at.y + 18.0), anchor: None, caution })
    }

    /// The tracks, watched while something is carried from the bins: they
    /// say what the pointer is over, and show where it would land.
    pub(super) fn bin_drop_zone(&self, tracks: impl Into<Element<Message>>) -> Element<Message> {
        let Drag::Carried { over, .. } = &self.bin_state.drag else { return tracks.into() };
        let (lanes, was) = (self.bin_lanes(), *over);
        let (landing, drawn) = (self.bin_state.landing.clone(), lanes.clone());
        sense(tracks)
            .on(move |event, bounds, cursor, _| tracks_event(&lanes, was, event, bounds, cursor))
            .marks(move |size| marks_of(landing.as_ref(), &drawn, size))
            .into()
    }

    /// What gives the clips of each item `target` stands for in the bin on
    /// screen, in the order they go into the cut (`bins::target_items`).
    pub(super) fn bin_makers(&self, target: &Target) -> Vec<Box<dyn Fn(EditFrame) -> Clips + '_>> {
        let shots = (self.bin_tab == 0).then_some(self.bins.shots.as_slice());
        let count = if self.bin_tab == 0 { self.bins.shots.len() } else { self.bins.media.len() };
        bins::target_items(target, shots, count)
            .into_iter()
            .map(|index| -> Box<dyn Fn(EditFrame) -> Clips + '_> {
                if self.bin_tab == 0 {
                    let shot = &self.bins.shots[index];
                    Box::new(move |at| (Some(shot.clip(at)), None))
                } else {
                    let item = &self.bins.media[index];
                    Box::new(move |at| item.clips(at))
                }
            })
            .collect()
    }

    /// The rows of the bin on screen.
    fn bin_rows(&self) -> Vec<BinRow> {
        match self.bin_tab {
            0 => bins::shot_rows(&self.bins.shots, &self.bin_state.folded),
            _ => self
                .bins
                .media
                .iter()
                .enumerate()
                .map(|(i, m)| BinRow::Item { index: i, name: m.item.clone(), detail: m.describe() })
                .collect(),
        }
    }

    /// The Shots bin lists each sequence's shots under its name; the Media
    /// bin its items. Each row is a name and, quieter, what it is.
    pub(super) fn bins_view(&self) -> Element<Message> {
        // Out of keyboard focus, as are the panel's buttons: a click on them
        // must leave the arrows, Home/End and Space to the timeline (on_key).
        let tabs = segmented(Id::new("bin tabs"), self.bin_tab).push("Shots").push("Media").on_select(Message::BinTab).focusable(false);
        let rows = Arc::new(self.bin_rows());
        let keys = Arc::new(self.bin_keys());
        let count = keys.len();
        let pictures: Arc<Vec<Picture>> = Arc::new(
            keys.iter()
                .map(|key| match self.bin_state.thumbs.shown(key) {
                    Shown::Pending => Picture::Pending,
                    Shown::Picture(handle) => Picture::Image(handle.clone()),
                    Shown::Nothing(why) => Picture::Nothing(why.to_owned()),
                })
                .collect(),
        );
        let grid = self.layout.bins_grid;
        let list = if grid { self.bin_grid(&rows, &keys, &pictures) } else { self.bin_list(&rows, &keys, &pictures) };
        let hovered = self.bin_state.hover.map(|h| h.rect);
        let list = sense(list)
            .on(move |event, bounds, cursor, _| match event {
                Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) => {
                    cursor.position_in(bounds).and(cursor.position()).map(|at| Message::Bin(BinMessage::MenuAt(at)))
                }
                _ => list_left(hovered, event, bounds, cursor),
            })
            .float(self.bin_tip(&pictures));
        let list = self.bin_menu(list);
        let grid_box = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(look::tick_box(Id::new("bin grid"), grid, |on| Message::Bin(BinMessage::Grid(on))).focusable(false))
            .push(text("Grid").font_size(13.0).color(look::quiet()));
        let top = row().align_y(Vertical::Center).push(tabs).push(space(Length::Fill, Length::Shrink)).push(tooltip(
            Id::new("bin grid tip"),
            grid_box,
            GRID_TIP,
        ));
        let writable = !self.doc.read_only();
        let put = |id: &'static str, label: &str, overlay: bool| {
            let b = look::button(Id::new(id), text(label.to_owned()).font_size(12.0)).padding(Padding::from([3.0, 8.0])).focusable(false);
            // The editor's own handler puts every picked item.
            if writable { b.on_press(move || Message::BinPut(overlay)) } else { b }
        };
        let mut actions = row()
            .spacing(6.0)
            .push(tooltip(Id::new("insert tip"), put("insert", "Insert", false), INSERT_TIP))
            .push(tooltip(Id::new("overlay tip"), put("overlay", "Overlay", true), OVERLAY_TIP));
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
        let mut page = column().spacing(8.0).padding(Padding::from([10.0, 10.0])).push(top);
        if count == 0 {
            let empty = if self.bin_tab == 0 {
                "The project has no shots yet: New shot… plans one in the cut"
            } else {
                "No media yet: Import… copies a file in"
            };
            page = page.push(text(empty).font_size(12.0).color(look::quiet()));
        }
        let panel = container(page.push(list).push(actions).height(Length::Fill))
            .background(look::panel())
            .width(Length::Fill)
            .height(Length::Fill);
        // The panel follows a row held or carried wherever the pointer
        // goes: every widget is given every event.
        let drag = self.bin_state.drag.clone();
        let copy = self.bin_state.more.copy.clone().map(|words| (words, Message::Bin(BinMessage::Copied)));
        sense(panel)
            .on(move |event, _, cursor, modifiers| panel_event(&drag, event, cursor, modifiers))
            .float(self.bin_ghost())
            .copy(copy)
            .into()
    }

    fn bin_watch(&self) -> Watch {
        Watch { hovered: self.bin_state.hover.map(|h| h.index), carrying: self.bin_state.drag.carried().is_some() }
    }

    /// The name of item `index` of the bin on screen, and what else it is.
    fn bin_item_words(&self, index: usize) -> Option<(String, String)> {
        match self.bin_tab {
            0 => self.bins.shots.get(index).map(|s| (s.name.clone(), bins::shot_detail(s))),
            _ => self.bins.media.get(index).map(|m| (m.item.clone(), m.describe())),
        }
    }

    /// The tip of the row the pointer has rested on: under the row, a
    /// little in from its left.
    pub(super) fn bin_tip(&self, pictures: &[Picture]) -> Option<Float> {
        let hover = self.bin_state.hover.filter(|h| h.since.elapsed() >= TIP_DELAY)?;
        let (name, detail) = self.bin_item_words(hover.index)?;
        let note = pictures.get(hover.index).and_then(Picture::note);
        Some(Float {
            words: bins::row_tip(&name, &detail, note),
            at: Point::new(hover.rect.x + 24.0, hover.rect.y + hover.rect.height + 2.0),
            anchor: Some(hover.rect),
            caution: false,
        })
    }

    /// The bin on screen as a list: a row a line, its thumbnail before its
    /// name. A row the list builds is on screen: it says so (`Seen`).
    fn bin_list(&self, rows: &Arc<Vec<BinRow>>, keys: &Arc<Vec<ThumbKey>>, pictures: &Arc<Vec<Picture>>) -> Element<Message> {
        let picked = self.bin_picked();
        let shown = self.bin_pick.and_then(|p| rows.iter().position(|r| r.item() == Some(p)));
        let marked = bins::selected_rows(rows, &picked);
        // A sequence's name picks its first shot, folded or not.
        let pick: Vec<usize> = rows
            .iter()
            .map(|row| bins::target_items(&row.target(), Some(&self.bins.shots), usize::MAX).first().copied().unwrap_or(0))
            .collect();
        let activate = Arc::clone(rows);
        let rows = Arc::clone(rows);
        let (keys, pictures, seen) = (Arc::clone(keys), Arc::clone(pictures), self.bin_state.thumbs.seen.clone());
        let (tab, folded) = (self.bin_tab, self.bin_state.folded.clone());
        // The list builds its rows as it lays them out, so this borrows
        // nothing: each row is made from what it needs, owned.
        let watch = self.bin_watch();
        let list = indexed_list(Id::new(if tab == 0 { "shot bin" } else { "media bin" }), rows.len(), LIST_ROW, move |i| {
            let content: Element<Message> = match &rows[i] {
                BinRow::Sequence(name) => {
                    let words = row()
                        .align_y(Vertical::Center)
                        .push(chevron(tab, i, name, folded.contains(name)))
                        .push(text(name.clone()).font_size(12.0).color(look::quiet()));
                    container(words).padding(Padding { top: 6.0, right: 8.0, bottom: 0.0, left: 2.0 }).width(Length::Fill).into()
                }
                BinRow::Item { index, name, .. } => {
                    seen.mark(&keys[*index], Instant::now());
                    let picture = &pictures[*index];
                    let mut line = row()
                        .spacing(8.0)
                        .align_y(Vertical::Center)
                        .width(Length::Fill)
                        .padding(Padding { top: 2.0, right: 8.0, bottom: 2.0, left: 10.0 })
                        .push(thumb_box(picture, Length::fixed(LIST_THUMB.0), LIST_THUMB.1, false))
                        .push(text(name.clone()).font_size(13.0).color(look::text()));
                    // Why there is no picture stays on the row.
                    if let Some(why) = picture.note() {
                        line = line.push(text(why.to_owned()).font_size(11.0).color(look::caution()));
                    }
                    let line: Element<Message> = if marked.contains(&i) && Some(*index) != shown_item(&rows, shown) {
                        container(line).background(look::rule()).into()
                    } else {
                        line.into()
                    };
                    line
                }
            };
            let target = rows[i].target();
            sense(content).on(move |event, bounds, cursor, modifiers| row_event(&target, watch, event, bounds, cursor, modifiers)).into()
        })
        .selected(shown)
        .on_select(move |i| Message::BinPick(pick[i]))
        .on_activate(move |i| Message::Bin(BinMessage::Activate(activate[i].target(), false)))
        .width(Length::Fill)
        .height(Length::Fill);
        modified(list, with_keys).into()
    }

    /// The bin on screen as a grid: each sequence's name on a line, then
    /// its shots as tiles (a thumbnail over a name), as many a line as the
    /// grid's width holds. The same windowed list as [`Self::bin_list`], a
    /// line of tiles a row, so only the lines on screen are built and only
    /// their thumbnails read. A press on a tile picks it, two put it in the
    /// cut (Shift: over it), as a double-click in the list does.
    ///
    /// The lines are built for the width the grid is laid out at
    /// ([`by_width`]), not the bins' saved width: while the panel's handle
    /// is dragged the split shows the dragged width and reports it only on
    /// release, so a grid built from the saved width kept its columns all
    /// through the drag and jumped to new ones when the handle was let go.
    fn bin_grid(&self, rows: &Arc<Vec<BinRow>>, keys: &Arc<Vec<ThumbKey>>, pictures: &Arc<Vec<Picture>>) -> Element<Message> {
        let (rows, keys, pictures, seen) = (Arc::clone(rows), Arc::clone(keys), Arc::clone(pictures), self.bin_state.thumbs.seen.clone());
        let (tab, picked, folded) = (self.bin_tab, Arc::new(self.bin_picked()), Arc::new(self.bin_state.folded.clone()));
        let watch = self.bin_watch();
        let grid = by_width(Sizing::FILL, move |width| {
            let bins::GridGeometry { columns, picture_tall, line_tall, .. } = bins::grid_geometry(width);
            let lines = Arc::new(bins::grid_rows(&rows, columns));
            let heights = Arc::clone(&lines);
            let (rows, keys, pictures, seen) = (Arc::clone(&rows), Arc::clone(&keys), Arc::clone(&pictures), seen.clone());
            let (picked, folded) = (Arc::clone(&picked), Arc::clone(&folded));
            let watched = move |target: Target, content: Element<Message>| -> Element<Message> {
                sense(content)
                    .on(move |event, bounds, cursor, modifiers| row_event(&target, watch, event, bounds, cursor, modifiers))
                    .into()
            };
            let press = |target: Target| move || Message::Bin(BinMessage::Press(target.clone(), false));
            indexed_list(Id::new(if tab == 0 { "shot grid" } else { "media grid" }), lines.len(), line_tall, move |i| match &lines[i] {
                GridRow::Sequence(at) => {
                    let BinRow::Sequence(name) = &rows[*at] else { return space(Length::Fill, Length::Fill).into() };
                    let words = container(text(name.clone()).font_size(12.0).color(look::quiet())).width(Length::Fill);
                    let heading = button(Id::new(format!("bin heading {tab} {at}")), words)
                        .background(Color::TRANSPARENT)
                        .padding(Padding { top: 10.0, right: 4.0, bottom: 2.0, left: 0.0 })
                        .width(Length::Fill)
                        .focusable(false)
                        .on_press(press(Target::Sequence(name.clone())));
                    let fold = container(chevron(tab, *at, name, folded.contains(name))).padding(Padding {
                        top: 8.0,
                        right: 0.0,
                        bottom: 0.0,
                        left: 0.0,
                    });
                    watched(Target::Sequence(name.clone()), row().push(fold).push(heading).width(Length::Fill).into())
                }
                GridRow::Tiles(tiles) => {
                    let mut line = row().spacing(bins::TILE_GAP);
                    for at in tiles {
                        let BinRow::Item { index, name, .. } = &rows[*at] else { continue };
                        seen.mark(&keys[*index], Instant::now());
                        // A tile is its picture (or why it has none) over
                        // its name; the rest is in its tip.
                        let tile = column()
                            .spacing(2.0)
                            .width(Length::Fill)
                            .push(thumb_box(&pictures[*index], Length::Fill, picture_tall, true))
                            .push(text(name.clone()).font_size(12.0).color(look::text()));
                        let tile = button(Id::new(format!("bin tile {tab} {index}")), tile)
                            .background(if picked.contains(index) { look::rule() } else { Color::TRANSPARENT })
                            .padding(bins::TILE_PADDING)
                            .width(Length::Fill)
                            .height(Length::fixed(line_tall - bins::TILE_GAP))
                            .focusable(false)
                            .on_press(press(Target::Item(*index)));
                        line = line.push(watched(Target::Item(*index), tile.into()));
                    }
                    // A short last line keeps its tiles the others' width.
                    for _ in tiles.len()..columns {
                        line = line.push(space(Length::Fill, Length::Shrink));
                    }
                    line.into()
                }
            })
            .row_heights(move |i| match heights[i] {
                GridRow::Sequence(_) => GRID_HEADING,
                GridRow::Tiles(_) => line_tall,
            })
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
        });
        modified(grid, with_keys).into()
    }
}

/// The item of the list's selected row.
fn shown_item(rows: &[BinRow], shown: Option<usize>) -> Option<usize> {
    shown.and_then(|row| rows.get(row)).and_then(BinRow::item)
}

#[cfg(test)]
mod tests {
    use primer::core::clipboard::NoopClipboard;
    use primer::core::measure::{TextLayout, TextMeasurer};
    use primer::core::render::TextSpan;
    use primer::widget::custom::{Cursor, Event, Layout as Placed, LayoutCtx, Limits, Shell, StateStore, Widget, mouse};

    use super::*;

    /// The rows here are spaces: nothing is measured.
    struct NoText;

    impl TextMeasurer for NoText {
        fn measure_text(&self, _: &str, _: f32, _: Option<f32>) -> Size {
            unreachable!("the rows hold no text")
        }
        fn measure_styled(&self, _: &str, _: &[TextSpan], _: f32, _: Option<f32>) -> Size {
            unreachable!("the rows hold no text")
        }
        fn layout_text(&self, _: &str, _: f32, _: Option<f32>) -> TextLayout {
            unreachable!("the rows hold no text")
        }
    }

    /// A bin's list as `bin_list` wires it (rows 36 high, a double-click
    /// names the row's target, through [`with_keys`]), the rows spaces.
    fn list(rows: Vec<BinRow>) -> Element<Message> {
        let rows = Arc::new(rows);
        let (pick, activate) = (Arc::clone(&rows), Arc::clone(&rows));
        let list = indexed_list(Id::new("bin under test"), rows.len(), 36.0, |_| space(Length::Fill, Length::Fill).into())
            .on_select(move |i| Message::BinPick(pick[i..].iter().find_map(BinRow::item).unwrap_or(0)))
            .on_activate(move |i| Message::Bin(BinMessage::Activate(activate[i].target(), false)))
            .width(Length::Fill)
            .height(Length::Fill);
        modified(list, with_keys).into()
    }

    /// The messages of two left presses on row `row` with `modifiers` held.
    fn double_click(rows: Vec<BinRow>, row: usize, modifiers: Modifiers) -> Vec<Message> {
        let mut list = list(rows);
        let mut store = StateStore::new();
        let theme = Theme::dark();
        let node =
            list.layout(&Limits::new(Size::ZERO, Size::new(200.0, 400.0)), LayoutCtx { measurer: &NoText, store: &store, theme: &theme });
        let cursor = Cursor::Available(Point::new(50.0, row as f32 * 36.0 + 10.0));
        let press = Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left));
        let mut messages = Vec::new();
        let mut clipboard = NoopClipboard;
        for _ in 0..2 {
            let mut shell = Shell::new(&mut messages, &mut store, &NoText, &mut clipboard, &theme, cursor, modifiers);
            list.on_event(&press, Placed::new(&node), &mut shell);
        }
        messages
    }

    fn rows() -> Vec<BinRow> {
        let item = |index| BinRow::Item { index, name: String::new(), detail: String::new() };
        vec![BinRow::Sequence("A".into()), item(0), item(1), BinRow::Sequence("B".into()), item(2)]
    }

    /// What a double-click on each row sends, plain and with Shift: the
    /// row's own target (a sequence's name the sequence, a shot the shot),
    /// inserted plain and overlaid with Shift, and Alt alone changes
    /// nothing. (Up to 1.112.0 every double-click inserted the picked
    /// shot, so a sequence's name its first shot, whatever was held.)
    #[test]
    fn a_double_click_names_its_row_and_shift_overlays() {
        let shift = Modifiers { shift: true, ..Modifiers::NONE };
        let others = Modifiers { alt: true, ..Modifiers::NONE };
        for (row, expected) in rows().into_iter().enumerate() {
            for (modifiers, overlay) in [(Modifiers::NONE, false), (shift, true), (others, false)] {
                let sent = double_click(rows(), row, modifiers);
                let put: Vec<(Target, bool)> = sent
                    .iter()
                    .filter_map(|m| match m {
                        Message::Bin(BinMessage::Activate(target, overlay)) => Some((target.clone(), *overlay)),
                        _ => None,
                    })
                    .collect();
                assert_eq!(put, [(expected.target(), overlay)], "row {row} with {modifiers:?}");
                // The first press picks: a sequence's name its first shot.
                let first = rows()[row..].iter().find_map(BinRow::item).unwrap();
                assert!(matches!(sent.first(), Some(Message::BinPick(pick)) if *pick == first), "row {row}: {sent:?}");
            }
        }
    }

    #[test]
    fn only_a_bin_double_click_takes_the_shift() {
        let shift = Modifiers { shift: true, ..Modifiers::NONE };
        // A grid tile's press carries it as the list's double-click does.
        let pressed = with_keys(Message::Bin(BinMessage::Press(Target::Item(2), false)), shift);
        assert!(matches!(pressed, Message::Bin(BinMessage::Press(Target::Item(2), true))));
        assert!(matches!(with_keys(Message::BinPut(false), shift), Message::BinPut(false)));
        assert!(matches!(with_keys(Message::BinPick(3), shift), Message::BinPick(3)));
        // Ctrl (Cmd on macOS) makes a click add or take out, in the list
        // and on a tile; a sequence's name in the grid is still a press.
        let command = if cfg!(target_os = "macos") {
            Modifiers { logo: true, ..Modifiers::NONE }
        } else {
            Modifiers { control: true, ..Modifiers::NONE }
        };
        assert!(matches!(with_keys(Message::BinPick(3), command), Message::Bin(BinMessage::Toggle(3))));
        assert!(matches!(with_keys(Message::Bin(BinMessage::Press(Target::Item(2), false)), command), Message::Bin(BinMessage::Toggle(2))));
        let heading = with_keys(Message::Bin(BinMessage::Press(Target::Sequence("A".into()), false)), command);
        assert!(matches!(heading, Message::Bin(BinMessage::Press(Target::Sequence(_), false))));
    }
}
