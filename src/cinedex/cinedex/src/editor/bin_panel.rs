//! The bins beside the viewer: the project's shots under their sequences'
//! names, and its media library, with what puts them in the cut.
//!
//! A double-click (or Enter) on a row inserts it at the playhead: a shot or
//! a media item itself, a sequence's name every shot of the sequence in
//! natural name order, back to back, as one undo step (`bins::put_run`).
//! With Shift held it is laid over what is there instead ([`with_shift`]).
//! Insert and Overlay put the picked item the same two ways.
//!
//! Each shot and media item has a thumbnail (`bin_thumbs`), read off the UI
//! thread for the rows on screen only. **Grid** shows the bin as tiles, so
//! many a line as the panel's width holds (`bins::grid_rows`), else it is a
//! list; the choice is this user's (`layout_prefs`). Both are the same
//! windowed list underneath, and both put things in the cut the same way:
//! the list reports its double-click itself, a tile's is two presses on it
//! (`bins::completes_double_click`), and either goes through
//! [`with_shift`] to [`BinMessage::Activate`]'s handler. Nothing is dragged
//! out of a bin in either view.

use std::sync::Arc;
use std::time::Instant;

use primer::keyboard::Modifiers;
use primer::prelude::*;
use th_edit::document::{Frame as EditFrame, TrackKind};
use th_pipe::edits::EditSources;

use super::{Editor, Message, OpenDialog};
use crate::bin_thumbs::{self, Loaded, Shown, ThumbKey, Thumbs};
use crate::bins::{self, BinRow, Clips, GridRow, Target};
use crate::display::Display;
use crate::look;
use crate::modified::modified;

const INSERT_TIP: &str = "Put the pick at the playhead; what follows moves on. Double-click a shot or media item does the same, and double-click a sequence's name inserts all its shots in order";
const OVERLAY_TIP: &str = "Lay the pick over what is at the playhead, on a track with room. Shift+double-click a shot or media item does the same, and Shift+double-click a sequence's name overlays all its shots";

/// A bin's double-click (or Enter) with the keys held at it: Shift lays
/// what it names over the cut, where a plain one inserts it.
fn with_shift(message: Message, modifiers: Modifiers) -> Message {
    match message {
        Message::Bin(BinMessage::Activate(target, _)) => Message::Bin(BinMessage::Activate(target, modifiers.shift)),
        Message::Bin(BinMessage::Press(target, _)) => Message::Bin(BinMessage::Press(target, modifiers.shift)),
        other => other,
    }
}

const GRID_TIP: &str = "Show the bin as a grid of thumbnails, or as a list. Remembered for next time";

/// A list row's height, and its thumbnail's size (16:9).
const LIST_ROW: f32 = 36.0;
const LIST_THUMB: (f32, f32) = (57.0, 32.0);
/// What of the bins' width is not the grid's tiles: the panel's padding
/// and the list's scrollbar.
const GRID_CHROME: f32 = 30.0;
/// A tile's two lines of words under its picture, and its padding.
const TILE_WORDS: f32 = 36.0;
const TILE_PADDING: f32 = 3.0;
/// A sequence's name on its line of the grid.
const GRID_HEADING: f32 = 30.0;

/// What the bins panel keeps between frames.
#[derive(Debug, Default)]
pub(super) struct BinState {
    pub(super) thumbs: Thumbs,
    /// The grid tile (or sequence's name) last pressed, and when: a second
    /// press on it in time is a double-click.
    last_press: Option<(Target, Instant)>,
}

/// What a row shows for its thumbnail, owned by the view.
#[derive(Debug, Clone)]
enum Picture {
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
fn thumb_box(picture: &Picture, width: Length, height: f32) -> Element<Message> {
    let inside: Element<Message> = match picture {
        Picture::Image(handle) => image(handle.clone()).width(Length::Fill).height(Length::Fill).content_fit(ContentFit::Contain).into(),
        Picture::Pending | Picture::Nothing(_) => space(Length::Fill, Length::Fill).into(),
    };
    container(inside).width(width).height(Length::fixed(height)).background(look::surround()).into()
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
}

impl Editor {
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
                let pick = match &target {
                    Target::Item(index) => Some(*index),
                    Target::Sequence(sequence) => bins::sequence_shots(&self.bins.shots, sequence).first().copied(),
                };
                self.bin_pick = pick.or(self.bin_pick);
                self.bin_state.last_press = Some((target, now));
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
        let makers: Vec<Box<dyn Fn(EditFrame) -> Clips + '_>> = match (self.bin_tab, target) {
            (0, Target::Item(index)) => match self.bins.shots.get(*index) {
                Some(shot) => vec![Box::new(move |at| (Some(shot.clip(at)), None))],
                None => return Command::none(),
            },
            (0, Target::Sequence(sequence)) => bins::sequence_shots(&self.bins.shots, sequence)
                .into_iter()
                .map(|index| {
                    let shot = &self.bins.shots[index];
                    Box::new(move |at| (Some(shot.clip(at)), None)) as Box<dyn Fn(EditFrame) -> Clips + '_>
                })
                .collect(),
            (_, Target::Item(index)) => match self.bins.media.get(*index) {
                Some(item) => vec![Box::new(move |at| item.clips(at))],
                None => return Command::none(),
            },
            (_, Target::Sequence(_)) => return Command::none(),
        };
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

    /// The rows of the bin on screen.
    fn bin_rows(&self) -> Vec<BinRow> {
        match self.bin_tab {
            0 => bins::shot_rows(&self.bins.shots),
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
        let rows = self.bin_rows();
        let count = rows.iter().filter(|r| matches!(r, BinRow::Item { .. })).count();
        let rows = Arc::new(rows);
        let keys = Arc::new(self.bin_keys());
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
        let list = if grid { self.bin_grid(&rows, &keys, &pictures) } else { self.bin_list(&rows, count, &keys, &pictures) };
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
        container(page.push(list).push(actions).height(Length::Fill))
            .background(look::panel())
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    /// The bin on screen as a list: a row a line, its thumbnail before its
    /// name. A row the list builds is on screen: it says so (`Seen`).
    fn bin_list(&self, rows: &Arc<Vec<BinRow>>, count: usize, keys: &Arc<Vec<ThumbKey>>, pictures: &Arc<Vec<Picture>>) -> Element<Message> {
        let shown = self.bin_pick.filter(|&p| p < count).and_then(|p| rows.iter().position(|r| r.item() == Some(p)));
        let (pick, activate) = (Arc::clone(rows), Arc::clone(rows));
        let rows = Arc::clone(rows);
        let (keys, pictures, seen) = (Arc::clone(keys), Arc::clone(pictures), self.bin_state.thumbs.seen.clone());
        let list = indexed_list(Id::new(if self.bin_tab == 0 { "shot bin" } else { "media bin" }), rows.len(), LIST_ROW, move |i| {
            match &rows[i] {
                BinRow::Sequence(name) => container(text(name.clone()).font_size(12.0).color(look::quiet()))
                    .padding(Padding { top: 14.0, right: 8.0, bottom: 0.0, left: 8.0 })
                    .into(),
                BinRow::Item { index, name, detail } => {
                    seen.mark(&keys[*index], Instant::now());
                    let picture = &pictures[*index];
                    let (words, colour) = match picture.note() {
                        Some(why) => (why.to_owned(), look::caution()),
                        None => (detail.clone(), look::quiet()),
                    };
                    let words = column()
                        .push(text(name.clone()).font_size(13.0).color(look::text()))
                        .push(text(words).font_size(11.0).color(colour))
                        .spacing(1.0);
                    row()
                        .spacing(8.0)
                        .align_y(Vertical::Center)
                        .padding(Padding { top: 2.0, right: 8.0, bottom: 2.0, left: 10.0 })
                        .push(thumb_box(picture, Length::fixed(LIST_THUMB.0), LIST_THUMB.1))
                        .push(words)
                        .into()
                }
            }
        })
        .selected(shown)
        // A sequence's name picks its first shot.
        .on_select(move |i| Message::BinPick(pick[i..].iter().find_map(BinRow::item).unwrap_or(0)))
        .on_activate(move |i| Message::Bin(BinMessage::Activate(activate[i].target(), false)))
        .width(Length::Fill)
        .height(Length::Fill);
        modified(list, with_shift).into()
    }

    /// The bin on screen as a grid: each sequence's name on a line, then
    /// its shots as tiles (a thumbnail over a name), as many a line as the
    /// bins' width holds. The same windowed list as [`Self::bin_list`], a
    /// line of tiles a row, so only the lines on screen are built and only
    /// their thumbnails read. A press on a tile picks it, two put it in the
    /// cut (Shift: over it), as a double-click in the list does.
    fn bin_grid(&self, rows: &Arc<Vec<BinRow>>, keys: &Arc<Vec<ThumbKey>>, pictures: &Arc<Vec<Picture>>) -> Element<Message> {
        let wide = (self.layout.bins_width - GRID_CHROME).max(bins::TILE_MIN);
        let columns = bins::grid_columns(wide);
        let tile_wide = (wide - (columns - 1) as f32 * bins::TILE_GAP) / columns as f32;
        let picture_tall = ((tile_wide - 2.0 * TILE_PADDING) * 9.0 / 16.0).round();
        let line_tall = picture_tall + TILE_WORDS + 2.0 * TILE_PADDING + bins::TILE_GAP;
        let lines = Arc::new(bins::grid_rows(rows, columns));
        let heights = Arc::clone(&lines);
        let (rows, keys, pictures, seen) = (Arc::clone(rows), Arc::clone(keys), Arc::clone(pictures), self.bin_state.thumbs.seen.clone());
        let (tab, picked) = (self.bin_tab, self.bin_pick);
        let press = |target: Target| move || Message::Bin(BinMessage::Press(target.clone(), false));
        let list =
            indexed_list(Id::new(if tab == 0 { "shot grid" } else { "media grid" }), lines.len(), line_tall, move |i| match &lines[i] {
                GridRow::Sequence(at) => {
                    let BinRow::Sequence(name) = &rows[*at] else { return space(Length::Fill, Length::Fill).into() };
                    let words = container(text(name.clone()).font_size(12.0).color(look::quiet())).width(Length::Fill);
                    button(Id::new(format!("bin heading {tab} {at}")), words)
                        .background(Color::TRANSPARENT)
                        .padding(Padding { top: 10.0, right: 4.0, bottom: 2.0, left: 4.0 })
                        .width(Length::Fill)
                        .focusable(false)
                        .on_press(press(Target::Sequence(name.clone())))
                        .into()
                }
                GridRow::Tiles(tiles) => {
                    let mut line = row().spacing(bins::TILE_GAP);
                    for at in tiles {
                        let BinRow::Item { index, name, detail } = &rows[*at] else { continue };
                        seen.mark(&keys[*index], Instant::now());
                        let picture = &pictures[*index];
                        // A tile has room for the first of the row's details.
                        let (words, colour) = match picture.note() {
                            Some(why) => (why.to_owned(), look::caution()),
                            None => (detail.split(',').next().unwrap_or_default().to_owned(), look::quiet()),
                        };
                        let tile = column()
                            .spacing(2.0)
                            .width(Length::Fill)
                            .push(thumb_box(picture, Length::Fill, picture_tall))
                            .push(text(name.clone()).font_size(12.0).color(look::text()))
                            .push(text(words).font_size(10.0).color(colour));
                        line = line.push(
                            button(Id::new(format!("bin tile {tab} {index}")), tile)
                                .background(if picked == Some(*index) { look::rule() } else { Color::TRANSPARENT })
                                .padding(TILE_PADDING)
                                .width(Length::Fill)
                                .height(Length::fixed(line_tall - bins::TILE_GAP))
                                .focusable(false)
                                .on_press(press(Target::Item(*index))),
                        );
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
            .height(Length::Fill);
        modified(list, with_shift).into()
    }
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
    /// names the row's target, through [`with_shift`]), the rows spaces.
    fn list(rows: Vec<BinRow>) -> Element<Message> {
        let rows = Arc::new(rows);
        let (pick, activate) = (Arc::clone(&rows), Arc::clone(&rows));
        let list = indexed_list(Id::new("bin under test"), rows.len(), 36.0, |_| space(Length::Fill, Length::Fill).into())
            .on_select(move |i| Message::BinPick(pick[i..].iter().find_map(BinRow::item).unwrap_or(0)))
            .on_activate(move |i| Message::Bin(BinMessage::Activate(activate[i].target(), false)))
            .width(Length::Fill)
            .height(Length::Fill);
        modified(list, with_shift).into()
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
    /// inserted plain and overlaid with Shift, and Ctrl or Alt alone change
    /// nothing. (Up to 1.112.0 every double-click inserted the picked
    /// shot, so a sequence's name its first shot, whatever was held.)
    #[test]
    fn a_double_click_names_its_row_and_shift_overlays() {
        let shift = Modifiers { shift: true, ..Modifiers::NONE };
        let others = Modifiers { control: true, alt: true, ..Modifiers::NONE };
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
        let pressed = with_shift(Message::Bin(BinMessage::Press(Target::Item(2), false)), shift);
        assert!(matches!(pressed, Message::Bin(BinMessage::Press(Target::Item(2), true))));
        assert!(matches!(with_shift(Message::BinPut(false), shift), Message::BinPut(false)));
        assert!(matches!(with_shift(Message::BinPick(3), shift), Message::BinPick(3)));
    }
}
