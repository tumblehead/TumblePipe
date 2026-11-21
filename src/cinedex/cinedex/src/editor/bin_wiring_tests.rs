//! The bins panel and what comes out of it, held to the cut without a
//! window: an [`Editor`] on the project template, its real bins view (and
//! its real tracks view) laid out at a size and sent mouse events at
//! pixels, each message they publish given to the editor as the runtime
//! would. Where a row is, is found as a person finds it: by moving the
//! pointer over the panel and seeing which row answers.
//!
//! Text is measured by a fixed rule (no font is loaded), so what is checked
//! is which row, tile, chevron or frame an event reaches and what it does,
//! not how anything looks.

#![allow(clippy::float_cmp)]

use std::collections::BTreeMap;

use primer::core::clipboard::Clipboard;
use primer::core::measure::{TextLayout, TextMeasurer};
use primer::core::render::TextSpan;
use primer::widget::custom::{Cursor, Event, Layout as Placed, LayoutCtx, Limits, Shell, StateStore, Widget, mouse};
use proptest::prelude::*;

use super::bin_panel::{BinMessage, GRID_HEADING, LIST_ROW, TIP_DELAY};
use super::*;
use crate::bin_drag::Drag;

/// Each editor is an engine and an OCIO config read: few cases.
const CASES: u32 = 24;

/// Every character half its size wide, a line 1.3 of it tall.
struct FixedText;

impl TextMeasurer for FixedText {
    fn measure_text(&self, content: &str, size: f32, _: Option<f32>) -> Size {
        Size::new(content.chars().count() as f32 * size * 0.5, size * 1.3)
    }
    fn measure_styled(&self, content: &str, _: &[TextSpan], size: f32, max: Option<f32>) -> Size {
        self.measure_text(content, size, max)
    }
    fn layout_text(&self, _: &str, _: f32, _: Option<f32>) -> TextLayout {
        unreachable!("nothing here edits text")
    }
}

/// A clipboard that keeps what was written.
#[derive(Default)]
struct Kept(Vec<String>);

impl Clipboard for Kept {
    fn read(&mut self) -> Option<String> {
        self.0.last().cloned()
    }
    fn write(&mut self, contents: String) {
        self.0.push(contents);
    }
}

/// An editor on the project template, its prefs in a folder of its own.
struct Bench {
    editor: Editor,
    prefs: tempfile::TempDir,
}

impl Bench {
    fn new(edit: &WorkingEdit, read_only: bool) -> Self {
        Self::at(edit, read_only, tempfile::tempdir().expect("a folder for the prefs"))
    }

    fn at(edit: &WorkingEdit, read_only: bool, prefs: tempfile::TempDir) -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
        let colour = Colour::open(&ocio).expect("the template's OCIO config");
        let origin = if read_only { Origin::Published { name: "cut".into(), version: "v0001".into() } } else { Origin::Untitled };
        let layout = prefs.path().join("layout.json");
        let editor = Editor::with(paths, ocio, Document::new(edit.clone(), origin), colour, None, None, Some(layout));
        Bench { editor, prefs }
    }

    fn send(&mut self, message: Message) {
        drop(self.editor.update(message));
    }

    fn refusal(&self) -> Option<&str> {
        self.editor.status.as_ref().filter(|s| s.1).map(|s| s.0.as_str())
    }

    fn reopened(self, edit: &WorkingEdit) -> Self {
        let Bench { editor, prefs } = self;
        drop(editor);
        Self::at(edit, false, prefs)
    }
}

/// The window's two parts under test, side by side as they never are, each
/// with a place of its own: the bins panel at the top-left, the tracks to
/// its right.
struct Screen {
    store: StateStore,
    clipboard: Kept,
    modifiers: Modifiers,
    /// The bins' width on screen, when it is not the saved one: a drag of
    /// the panel's handle in progress, which the split shows itself and
    /// reports only on release.
    dragged: Option<f32>,
}

/// The bins panel's height here: every row of these tests on screen.
const PANEL_TALL: f32 = 1400.0;

impl Screen {
    fn new() -> Self {
        Screen { store: StateStore::new(), clipboard: Kept::default(), modifiers: Modifiers::NONE, dragged: None }
    }

    /// `view` laid out `size` big takes `event` with the pointer at `at`;
    /// the editor is given what it published, in order. The messages, as
    /// they print.
    fn deliver(&mut self, editor: &mut Editor, tracks: bool, size: Size, event: &Event, at: Point) -> Vec<String> {
        let theme = Theme::dark();
        let mut messages = Vec::new();
        {
            let mut view = if tracks { editor.tracks_view() } else { editor.bins_view() };
            let node = view.layout(&Limits::new(Size::ZERO, size), LayoutCtx { measurer: &FixedText, store: &self.store, theme: &theme });
            let mut shell =
                Shell::new(&mut messages, &mut self.store, &FixedText, &mut self.clipboard, &theme, Cursor::Available(at), self.modifiers);
            view.on_event(event, Placed::new(&node), &mut shell);
        }
        let said = messages.iter().map(|m| format!("{m:?}")).collect();
        for message in messages {
            drop(editor.update(message));
        }
        said
    }

    /// The bins' width on screen.
    fn wide(&self, editor: &Editor) -> f32 {
        self.dragged.unwrap_or(editor.layout.bins_width)
    }

    fn bins(&mut self, editor: &mut Editor, event: Event, at: Point) -> Vec<String> {
        let size = Size::new(self.wide(editor), PANEL_TALL);
        self.deliver(editor, false, size, &event, at)
    }

    fn move_to(&mut self, editor: &mut Editor, at: Point) -> Vec<String> {
        self.bins(editor, Event::Mouse(mouse::Event::CursorMoved { position: at }), at)
    }

    fn press(&mut self, editor: &mut Editor, at: Point) -> Vec<String> {
        self.bins(editor, Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), at)
    }

    fn release(&mut self, editor: &mut Editor, at: Point) -> Vec<String> {
        self.bins(editor, Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), at)
    }

    fn click(&mut self, editor: &mut Editor, at: Point) -> Vec<String> {
        let mut said = self.press(editor, at);
        said.extend(self.release(editor, at));
        said
    }

    /// Where each item's row (or tile) is: the pointer is moved over the
    /// whole panel and each row says when it is on it.
    fn item_rects(&mut self, editor: &mut Editor) -> BTreeMap<usize, Rectangle> {
        let mut rects = BTreeMap::new();
        let wide = self.wide(editor);
        let mut y = 1.0;
        while y < PANEL_TALL {
            let mut x = 14.0;
            while x < wide {
                self.move_to(editor, Point::new(x, y));
                if let Some(hover) = editor.bin_state.hover {
                    assert!(hover.rect.contains(Point::new(x, y)), "a row answered for a point outside it");
                    rects.insert(hover.index, hover.rect);
                }
                x += 30.0;
            }
            y += 5.0;
        }
        // Leave the panel: nothing is pointed at.
        self.move_to(editor, Point::new(wide + 500.0, 5.0));
        assert!(editor.bin_state.hover.is_none(), "the pointer left the bins and a row is still pointed at");
        rects
    }
}

fn middle(rect: Rectangle) -> Point {
    Point::new(rect.x + rect.width / 2.0, rect.y + rect.height / 2.0)
}

fn shot(sequence: &str, name: &str, frames: i64) -> BinShot {
    BinShot {
        uri: format!("entity:/shots/{sequence}/{name}"),
        sequence: sequence.into(),
        name: name.into(),
        first: 1001,
        last: 1000 + frames,
    }
}

/// A bin of sequences S0, S1, ... of `sizes` shots each (sh1, sh2, ...,
/// each `10 + its number` frames long), as the bin lists them.
fn bin_of(sizes: &[usize]) -> Vec<BinShot> {
    sizes.iter().enumerate().flat_map(|(s, &n)| (1..=n).map(move |i| shot(&format!("S{s}"), &format!("sh{i}"), 10 + i as i64))).collect()
}

fn empty_cut() -> WorkingEdit {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
    edit.video.push(th_edit::document::Track { name: "V1".into(), clips: Vec::new(), color: None });
    edit.audio.push(th_edit::document::Track { name: "A1".into(), clips: Vec::new(), color: None });
    edit
}

/// The shot clips of the cut: (uri, video track, at, length), by where
/// they are.
fn shot_clips(edit: &WorkingEdit) -> Vec<(String, usize, EditFrame, EditFrame)> {
    let mut out = Vec::new();
    for (track, clips) in edit.video.iter().enumerate() {
        for clip in &clips.clips {
            if let Source::Shot { shot, .. } = &clip.source {
                out.push((shot.clone(), track, clip.at, clip.duration));
            }
        }
    }
    out.sort_by_key(|c| (c.2, c.1));
    out
}

/// Where the chevron of the sequence whose first shown shot's row is
/// `first` is: on the name's line, just above.
fn chevron_at(first: Rectangle, grid: bool) -> Point {
    Point::new(first.x + 8.0, first.y - if grid { GRID_HEADING / 2.0 } else { LIST_ROW / 2.0 })
}

proptest! {
    #![proptest_config(ProptestConfig { cases: CASES, failure_persistence: None, ..ProptestConfig::default() })]

    /// The chevron before a sequence's name, clicked, folds that sequence's
    /// shots away and no other's; the name, double-clicked while folded,
    /// still puts every shot of the sequence in the cut, in order (Shift:
    /// laid over); the fold is there next time for this user; and a second
    /// click brings the shots back. In the list and in the grid.
    #[test]
    fn a_chevron_folds_its_sequence_and_the_name_still_stands_for_its_shots(
        sizes in prop::collection::vec(1usize..4, 1..4), which in any::<prop::sample::Index>(), grid in any::<bool>(), overlay in any::<bool>(),
    ) {
        let shots = bin_of(&sizes);
        let which = which.index(sizes.len());
        let name = format!("S{which}");
        let own: Vec<usize> = (0..shots.len()).filter(|&i| shots[i].sequence == name).collect();
        let edit = empty_cut();
        let mut bench = Bench::new(&edit, false);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let all = screen.item_rects(&mut bench.editor);
        prop_assert_eq!(all.keys().copied().collect::<Vec<_>>(), (0..shots.len()).collect::<Vec<_>>(), "every shot has a row");
        let chevron = chevron_at(all[&own[0]], grid);
        let said = screen.click(&mut bench.editor, chevron);
        prop_assert!(said.iter().any(|m| m.contains("Fold")), "the chevron was not where it is drawn: {:?}", said);
        prop_assert_eq!(bench.editor.bin_state.folded.iter().cloned().collect::<Vec<_>>(), vec![name.clone()]);
        let folded = screen.item_rects(&mut bench.editor);
        let others: Vec<usize> = (0..shots.len()).filter(|i| !own.contains(i)).collect();
        prop_assert_eq!(folded.keys().copied().collect::<Vec<_>>(), others, "only the folded sequence's shots are gone");
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit.clone(), "folding changed the cut");

        // The name still takes a double-click, for all its shots.
        screen.modifiers = Modifiers { shift: overlay, ..Modifiers::NONE };
        let on_name = Point::new(chevron.x + 110.0, chevron.y);
        screen.click(&mut bench.editor, on_name);
        screen.click(&mut bench.editor, on_name);
        screen.modifiers = Modifiers::NONE;
        prop_assert_eq!(bench.refusal(), None);
        let put = shot_clips(&bench.editor.doc.edit);
        let mut next = 0;
        prop_assert_eq!(put.len(), own.len(), "every shot of the folded sequence, once: {:?}", put);
        for (clip, &index) in put.iter().zip(&own) {
            prop_assert_eq!((&clip.0, clip.2, clip.3), (&shots[index].uri, next, shots[index].last - shots[index].first + 1));
            next += clip.3;
        }
        bench.send(Message::Undo);
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit.clone(), "not one undo step");

        // Next time, for this user, it is still folded.
        let mut bench = bench.reopened(&edit);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        prop_assert_eq!(bench.editor.bin_state.folded.iter().cloned().collect::<Vec<_>>(), vec![name]);
        // And the chevron brings the shots back.
        let mut screen = Screen::new();
        let said = screen.click(&mut bench.editor, chevron);
        prop_assert!(said.iter().any(|m| m.contains("Fold")), "{:?}", said);
        prop_assert!(bench.editor.bin_state.folded.is_empty());
        let back = screen.item_rects(&mut bench.editor);
        prop_assert_eq!(back, all, "unfolded, the rows are where they were");
        let bench = bench.reopened(&edit);
        prop_assert!(bench.editor.bin_state.folded.is_empty(), "unfolding was not remembered");
    }

    /// The grid's tiles follow the width the bins are shown at, not the
    /// one they were saved at: while the panel's handle is dragged (the
    /// split shows the drag and reports it only on release) the tiles are
    /// already where the release leaves them, as many a line as that width
    /// holds, so nothing snaps when the handle is let go.
    #[test]
    fn the_grid_follows_the_bins_as_they_are_dragged(
        sizes in prop::collection::vec(1usize..5, 1..3), saved in 180f32..900.0, dragged in 240f32..900.0,
    ) {
        let shots = bin_of(&sizes);
        let edit = empty_cut();
        let mut bench = Bench::new(&edit, false);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(true)));
        bench.editor.layout.bins_width = saved;
        let mut screen = Screen::new();
        screen.dragged = Some(dragged);
        let during = screen.item_rects(&mut bench.editor);
        prop_assert_eq!(during.keys().copied().collect::<Vec<_>>(), (0..shots.len()).collect::<Vec<_>>(), "every shot has a tile");
        // The first sequence's first line: as many tiles as the width holds.
        let columns = crate::bins::grid_geometry(dragged - 20.0).columns;
        let first_line = during.values().filter(|r| (r.y - during[&0].y).abs() < 0.5).count();
        prop_assert_eq!(first_line, columns.min(sizes[0]), "{} wide shows {} a line", dragged, first_line);
        prop_assert!(during.values().all(|r| r.x + r.width <= dragged), "a tile is past the bins' edge");
        // Released: the width is saved, and the tiles stay where they were.
        bench.editor.layout.bins_width = dragged;
        screen.dragged = None;
        let after = screen.item_rects(&mut bench.editor);
        prop_assert_eq!(after, during, "the tiles moved when the drag was let go");
    }

    /// A row shows its name; what else it is comes when the pointer has
    /// rested on it: the tip names the shot the pointer is on, with its
    /// length and range, under that row, and goes when the pointer moves
    /// to a sequence's name or off the bins.
    #[test]
    fn the_pointer_resting_on_a_row_says_what_the_row_no_longer_shows(
        sizes in prop::collection::vec(1usize..4, 1..4), which in any::<prop::sample::Index>(), grid in any::<bool>(),
    ) {
        let shots = bin_of(&sizes);
        let which = which.index(shots.len());
        let mut bench = Bench::new(&empty_cut(), false);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let rects = screen.item_rects(&mut bench.editor);
        prop_assert!(bench.editor.bin_tip(&[]).is_none());
        let on = middle(rects[&which]);
        screen.move_to(&mut bench.editor, on);
        let hover = bench.editor.bin_state.hover.expect("the row under the pointer");
        prop_assert_eq!(hover.index, which);
        prop_assert!(bench.editor.bin_tip(&[]).is_none(), "the tip showed before the pointer rested");
        // More moves on the same row do not start the wait again.
        screen.move_to(&mut bench.editor, Point::new(on.x + 2.0, on.y + 1.0));
        prop_assert_eq!(bench.editor.bin_state.hover.map(|h| h.since), Some(hover.since));
        bench.editor.bin_state.hover.as_mut().unwrap().since -= TIP_DELAY;
        let tip = bench.editor.bin_tip(&[]).expect("the tip of a row rested on");
        let frames = shots[which].last - shots[which].first + 1;
        prop_assert!(tip.words.starts_with(&shots[which].name), "{}", tip.words);
        prop_assert!(tip.words.contains(&format!("{frames} frames")) && tip.words.contains("1001"), "{}", tip.words);
        prop_assert_eq!(tip.anchor, Some(rects[&which]));
        prop_assert!(tip.at.y >= rects[&which].y + rects[&which].height, "the tip covers its row");
        // A sequence's name has no tip, nor has anywhere off the bins.
        screen.move_to(&mut bench.editor, chevron_at(rects[&0], grid));
        prop_assert!(bench.editor.bin_state.hover.is_none(), "a sequence's name kept a shot's tip");
        screen.move_to(&mut bench.editor, on);
        prop_assert!(bench.editor.bin_state.hover.is_some());
        let outside = Point::new(bench.editor.layout.bins_width + 300.0, on.y);
        screen.move_to(&mut bench.editor, outside);
        prop_assert!(bench.editor.bin_state.hover.is_none());
        prop_assert!(bench.editor.bin_tip(&[]).is_none());
    }

    /// Ctrl-click adds a row to the picked ones and takes it out again; a
    /// plain click picks one alone; Insert puts all that is picked, in the
    /// bin's order, as one undo step. In the list and in the grid.
    #[test]
    fn ctrl_click_picks_several_and_insert_puts_them_all(
        sizes in prop::collection::vec(1usize..4, 1..4), clicks in prop::collection::vec(any::<prop::sample::Index>(), 1..7),
        grid in any::<bool>(), overlay in any::<bool>(),
    ) {
        let shots = bin_of(&sizes);
        let edit = empty_cut();
        let mut bench = Bench::new(&edit, false);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let rects = screen.item_rects(&mut bench.editor);
        screen.click(&mut bench.editor, middle(rects[&0]));
        prop_assert_eq!(bench.editor.bin_picked(), vec![0]);
        // The oracle: a set, toggled.
        let mut oracle = std::collections::BTreeSet::from([0usize]);
        screen.modifiers = if cfg!(target_os = "macos") { Modifiers { logo: true, ..Modifiers::NONE } } else { Modifiers { control: true, ..Modifiers::NONE } };
        for click in &clicks {
            let index = click.index(shots.len());
            // Apart in time: two clicks on one row are not a double-click.
            bench.editor.bin_state.last_press = None;
            screen.store = StateStore::new();
            screen.click(&mut bench.editor, middle(rects[&index]));
            if !oracle.remove(&index) {
                oracle.insert(index);
            }
            prop_assert_eq!(bench.editor.bin_picked(), oracle.iter().copied().collect::<Vec<_>>());
        }
        screen.modifiers = Modifiers::NONE;
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit.clone(), "picking changed the cut");
        bench.send(Message::BinPut(overlay));
        let put = shot_clips(&bench.editor.doc.edit);
        if oracle.is_empty() {
            prop_assert!(put.is_empty());
            prop_assert!(bench.refusal().is_some(), "nothing picked, and nothing said");
        } else {
            prop_assert_eq!(bench.refusal(), None);
            let mut next = 0;
            prop_assert_eq!(put.len(), oracle.len());
            for (clip, &index) in put.iter().zip(&oracle) {
                prop_assert_eq!((&clip.0, clip.2), (&shots[index].uri, next), "in the bin's order, back to back");
                next += clip.3;
            }
            bench.send(Message::Undo);
            prop_assert_eq!(bench.editor.doc.edit.clone(), edit.clone(), "not one undo step");
            // A plain click picks one alone again.
            bench.editor.bin_state.last_press = None;
            screen.store = StateStore::new();
            let last = *oracle.iter().next_back().unwrap();
            screen.click(&mut bench.editor, middle(rects[&last]));
            screen.release(&mut bench.editor, middle(rects[&last]));
            prop_assert_eq!(bench.editor.bin_picked(), vec![last], "a plain click picks its row alone");
        }
    }
}

// ---- Dragging out of the bins onto the timeline ----

/// The tracks' size here: wide enough that a frame is some pixels.
const TRACKS: Size = Size { width: 1100.0, height: 900.0 };

/// Where the bins' own events put the pointer when it is over the tracks:
/// far from every row of the panel.
const AWAY: f32 = 5000.0;

impl Screen {
    /// The pointer moves to `at` of the tracks (their own pixels): the
    /// tracks and the bins panel both hear of it, as every widget does.
    fn carry_to(&mut self, editor: &mut Editor, at: Point) {
        self.bins(editor, Event::Mouse(mouse::Event::CursorMoved { position: at }), Point::new(at.x + AWAY, at.y + AWAY));
        self.deliver(editor, true, TRACKS, &Event::Mouse(mouse::Event::CursorMoved { position: at }), at);
    }

    /// The button is let go over the tracks.
    fn let_go(&mut self, editor: &mut Editor) {
        let at = Point::new(AWAY, AWAY);
        self.deliver(editor, true, TRACKS, &Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), at);
        self.release(editor, at);
    }
}

/// The tracks' rows as the test reads them off the editor: (track, top,
/// height), under the ruler.
fn row_bands(editor: &Editor) -> Vec<(TrackRef, f32, f32)> {
    let mut top = crate::tracks::RULER;
    editor
        .rows()
        .iter()
        .map(|row| {
            let band = (row.track, top, row.height);
            top += row.height;
            band
        })
        .collect()
}

/// Where frame `frame` starts on tracks `TRACKS` wide, in the test's own
/// arithmetic.
fn frame_x(editor: &Editor, frame: f32) -> f32 {
    span_x(editor.timeline_view.span(), frame)
}

/// [`frame_x`] on tracks that span `span` frames.
fn span_x(span: usize, frame: f32) -> f32 {
    let header = crate::tracks::HEADER;
    header + frame / span.max(1) as f32 * (TRACKS.width - header)
}

/// A cut of video tracks, each a list of (gap before, length) of slugs,
/// and one empty audio track.
fn cut_of(video: &[Vec<(EditFrame, EditFrame)>]) -> WorkingEdit {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
    for (index, spans) in video.iter().enumerate() {
        let mut at = 0;
        let clips = spans
            .iter()
            .map(|&(gap, length)| {
                at += gap;
                let clip = th_edit::document::VideoClip::new(at, length, 0, Source::Slug { text: format!("old {index} {at}") });
                at += length;
                clip
            })
            .collect();
        edit.video.push(th_edit::document::Track { name: format!("V{}", index + 1), clips, color: None });
    }
    edit.audio.push(th_edit::document::Track { name: "A1".into(), clips: Vec::new(), color: None });
    edit
}

fn slugs(edit: &WorkingEdit) -> Vec<(usize, EditFrame, EditFrame, String)> {
    let mut out = Vec::new();
    for (track, clips) in edit.video.iter().enumerate() {
        for clip in &clips.clips {
            if let Source::Slug { text } = &clip.source {
                out.push((track, clip.at, clip.duration, text.clone()));
            }
        }
    }
    out
}

fn command() -> Modifiers {
    if cfg!(target_os = "macos") { Modifiers { logo: true, ..Modifiers::NONE } } else { Modifiers { control: true, ..Modifiers::NONE } }
}

/// What is dragged in a test.
#[derive(Debug, Clone, Copy)]
enum Carry {
    One,
    /// Two rows picked together, dragged by one of them.
    Picked,
    Sequence,
    /// The sequence's name with its shots folded away.
    Folded,
}

fn carry() -> impl Strategy<Value = Carry> {
    prop::sample::select(vec![Carry::One, Carry::Picked, Carry::Sequence, Carry::Folded])
}

/// Press what `carry` says in the bins and carry it off the panel. The
/// shots that go with it, in the order they go into the cut.
fn pick_up(screen: &mut Screen, editor: &mut Editor, shots: &[BinShot], carry: Carry, which: usize, grid: bool) -> Vec<usize> {
    let rects = screen.item_rects(editor);
    let sequence: Vec<usize> = (0..shots.len()).filter(|&i| shots[i].sequence == shots[which].sequence).collect();
    let (press, goes) = match carry {
        Carry::One => (middle(rects[&which]), vec![which]),
        Carry::Picked => {
            let other = (which + 1) % shots.len();
            screen.click(editor, middle(rects[&which]));
            screen.modifiers = command();
            editor.bin_state.last_press = None;
            screen.store = StateStore::new();
            if other != which {
                screen.click(editor, middle(rects[&other]));
            }
            screen.modifiers = Modifiers::NONE;
            editor.bin_state.last_press = None;
            screen.store = StateStore::new();
            let mut goes = vec![which, other];
            goes.sort_unstable();
            goes.dedup();
            assert_eq!(editor.bin_picked(), goes);
            (middle(rects[&which]), goes)
        }
        Carry::Sequence | Carry::Folded => {
            let chevron = chevron_at(rects[&sequence[0]], grid);
            if matches!(carry, Carry::Folded) {
                screen.click(editor, chevron);
                assert!(editor.bin_state.folded.contains(&shots[which].sequence));
            }
            (Point::new(chevron.x + 110.0, chevron.y), sequence)
        }
    };
    screen.press(editor, press);
    assert!(matches!(editor.bin_state.drag, Drag::Held { .. }), "a press on a row holds it: {:?}", editor.bin_state.drag);
    // A few pixels is still a click: nothing is carried, nothing follows.
    screen.move_to(editor, Point::new(press.x + 2.0, press.y + 1.0));
    assert!(editor.bin_state.drag.carried().is_none() && editor.bin_ghost().is_none());
    screen.move_to(editor, Point::new(press.x + 30.0, press.y + 9.0));
    assert!(editor.bin_state.drag.carried().is_some(), "moved on, it is carried");
    assert!(editor.bin_state.hover.is_none(), "a row's tip while carrying");
    goes
}

proptest! {
    #![proptest_config(ProptestConfig { cases: CASES, failure_persistence: None, ..ProptestConfig::default() })]

    /// A shot, two picked together, or a sequence's name (folded or not)
    /// dragged from the list or the grid to a frame of a video row (the
    /// spare layer included) lands there: every shot once, in order, back
    /// to back from the frame under the pointer; plain it is laid over and
    /// nothing that was there moves, with Ctrl it is inserted on that row.
    /// The placeholder shown before letting go is where the clips went;
    /// what follows the pointer says what is carried; the playhead ends
    /// after the last; and it is one undo step.
    #[test]
    fn a_row_dragged_to_the_timeline_lands_where_its_placeholder_was(
        video in prop::collection::vec(prop::collection::vec((0i64..8, 1i64..12), 0..3), 1..3),
        sizes in prop::collection::vec(1usize..4, 1..3), which in any::<prop::sample::Index>(), carry in carry(), grid in any::<bool>(),
        row in any::<prop::sample::Index>(), frame in any::<prop::sample::Index>(), insert in any::<bool>(),
    ) {
        let (edit, shots) = (cut_of(&video), bin_of(&sizes));
        let which = which.index(shots.len());
        let mut bench = Bench::new(&edit, false);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let goes = pick_up(&mut screen, &mut bench.editor, &shots, carry, which, grid);
        let ghost = bench.editor.bin_ghost().expect("something follows the pointer");
        match (carry, goes.len()) {
            (Carry::Sequence | Carry::Folded, _) => prop_assert!(ghost.words.starts_with(&shots[which].sequence), "{}", ghost.words),
            (_, 1) => prop_assert!(ghost.words.starts_with(&shots[which].name), "{}", ghost.words),
            (_, n) => prop_assert!(ghost.words.starts_with(&format!("{n} shots")), "{}", ghost.words),
        }
        prop_assert!(bench.editor.bin_marks(TRACKS).is_empty(), "a placeholder before the pointer is over the tracks");

        // Over a video row (the spare layer is the first).
        let bands = row_bands(&bench.editor);
        let video_rows: Vec<&(TrackRef, f32, f32)> = bands.iter().filter(|b| b.0.kind == TrackKind::Video).collect();
        let &&(track, top, tall) = &video_rows[row.index(video_rows.len())];
        let span = bench.editor.timeline_view.span();
        let frame = frame.index(bench.editor.timeline_view.span()) as EditFrame;
        screen.modifiers = if insert { command() } else { Modifiers::NONE };
        let point = Point::new(frame_x(&bench.editor, frame as f32 + 0.5), top + tall / 2.0);
        screen.carry_to(&mut bench.editor, point);
        let ghost = bench.editor.bin_ghost().unwrap();
        prop_assert!(!ghost.caution, "{}", ghost.words);
        prop_assert_eq!(ghost.words.contains("lay over"), !insert, "{}", ghost.words);
        let marks = bench.editor.bin_marks(TRACKS);
        prop_assert_eq!(marks.len(), goes.len(), "a placeholder for each clip");
        prop_assert!(marks.iter().all(|m| !m.refused));
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit.clone(), "carrying changed the cut");

        screen.let_go(&mut bench.editor);
        screen.modifiers = Modifiers::NONE;
        prop_assert_eq!(bench.refusal(), None);
        prop_assert_eq!(&bench.editor.bin_state.drag, &Drag::Idle);
        prop_assert!(bench.editor.bin_ghost().is_none() && bench.editor.bin_marks(TRACKS).is_empty());
        let after = bench.editor.doc.edit.clone();
        prop_assert_eq!(after.problem(), None);
        let put = shot_clips(&after);
        prop_assert_eq!(put.len(), goes.len(), "each shot once: {:?}", put);
        let on = put[0].1;
        let mut next = frame;
        for (clip, &index) in put.iter().zip(&goes) {
            let length = shots[index].last - shots[index].first + 1;
            prop_assert_eq!((&clip.0, clip.1, clip.2, clip.3), (&shots[index].uri, on, next, length), "in order, back to back, from the frame under the pointer");
            next += length;
        }
        prop_assert_eq!(bench.editor.position as EditFrame, next, "the playhead ends after the last");
        if insert {
            prop_assert_eq!(on, track.index, "inserted on the row under the pointer");
        } else {
            prop_assert!(on >= track.index, "laid over below the row under the pointer");
            prop_assert_eq!(slugs(&after), slugs(&edit), "laying over moved what was there");
        }
        // The placeholder was where they went: the same boxes, by the
        // test's own arithmetic on the rows as they were drawn then.
        for (mark, clip) in marks.iter().zip(&put) {
            let band = bands.iter().find(|b| b.0 == TrackRef::video(clip.1)).expect("the clip's track had a row");
            let (x0, x1) = (span_x(span, clip.2 as f32), span_x(span, (clip.2 + clip.3) as f32));
            prop_assert!((mark.rect.x - x0).abs() < 0.6 && (mark.rect.x + mark.rect.width - x1).abs() < 2.1, "{:?} for {:?}", mark.rect, clip);
            prop_assert!((mark.rect.y - band.1).abs() < 0.01 && (mark.rect.height - band.2).abs() < 0.01, "{:?} on {:?}", mark.rect, band);
        }
        bench.send(Message::Undo);
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit, "not one undo step");
        prop_assert!(!bench.editor.doc.can_undo());
    }

    /// What is carried goes nowhere when it cannot or should not: Escape,
    /// a let go off the rows, a picture over an audio row, anything over a
    /// read-only edit. The last two are said while carrying (the box under
    /// the pointer and the words beside it say why), not after. A press let
    /// go where it was is a click, and puts nothing.
    #[test]
    fn a_drag_that_cannot_land_changes_nothing_and_says_so_while_carried(
        video in prop::collection::vec(prop::collection::vec((0i64..8, 1i64..12), 1..3), 1..3),
        sizes in prop::collection::vec(1usize..4, 1..3), which in any::<prop::sample::Index>(), carry in carry(), grid in any::<bool>(),
        frame in any::<prop::sample::Index>(), insert in any::<bool>(), how in 0usize..5,
    ) {
        let (edit, shots) = (cut_of(&video), bin_of(&sizes));
        let which = which.index(shots.len());
        let read_only = how == 4;
        let mut bench = Bench::new(&edit, read_only);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let escape = Key::Named(primer::keyboard::Named::Escape);
        prop_assert!(bench.editor.bin_escape(&escape).is_none(), "Escape with nothing held is not the bins'");
        if how == 0 {
            // A click.
            let rects = screen.item_rects(&mut bench.editor);
            screen.click(&mut bench.editor, middle(rects[&which]));
            prop_assert_eq!(&bench.editor.bin_state.drag, &Drag::Idle);
            prop_assert_eq!(bench.editor.bin_picked(), vec![which]);
            prop_assert_eq!(bench.editor.doc.edit.clone(), edit);
            return Ok(());
        }
        pick_up(&mut screen, &mut bench.editor, &shots, carry, which, grid);
        let bands = row_bands(&bench.editor);
        let frame = frame.index(bench.editor.timeline_view.span()) as EditFrame;
        let x = frame_x(&bench.editor, frame as f32 + 0.5);
        screen.modifiers = if insert { command() } else { Modifiers::NONE };
        let video_row = bands.iter().find(|b| b.0.kind == TrackKind::Video).unwrap();
        let audio_row = bands.iter().find(|b| b.0.kind == TrackKind::Audio).unwrap();
        let refused = match how {
            1 => {
                // Over a row, then Escape.
                screen.carry_to(&mut bench.editor, Point::new(x, video_row.1 + 5.0));
                prop_assert!(!bench.editor.bin_marks(TRACKS).is_empty());
                let cancel = bench.editor.bin_escape(&escape).expect("Escape lets go of what is carried");
                bench.send(cancel);
                prop_assert_eq!(&bench.editor.bin_state.drag, &Drag::Idle);
                None
            }
            2 => {
                // Over a row, then off them: the ruler.
                screen.carry_to(&mut bench.editor, Point::new(x, video_row.1 + 5.0));
                prop_assert!(!bench.editor.bin_marks(TRACKS).is_empty());
                screen.carry_to(&mut bench.editor, Point::new(x, crate::tracks::RULER - 3.0));
                prop_assert!(bench.editor.bin_marks(TRACKS).is_empty(), "a placeholder with the pointer on the ruler");
                None
            }
            3 => {
                screen.carry_to(&mut bench.editor, Point::new(x, audio_row.1 + 5.0));
                Some(crate::bin_drag::PICTURE_ON_AUDIO)
            }
            _ => {
                prop_assert!(bands.iter().all(|b| b.0.kind != TrackKind::Video || b.0.index < edit.video.len()), "a read-only edit has no spare layer");
                screen.carry_to(&mut bench.editor, Point::new(x, video_row.1 + 5.0));
                Some(crate::bin_drag::READ_ONLY)
            }
        };
        if let Some(why) = refused {
            let marks = bench.editor.bin_marks(TRACKS);
            prop_assert_eq!(marks.len(), 1);
            prop_assert!(marks[0].refused && marks[0].words == why, "{:?}", marks);
            let band = if how == 3 { audio_row } else { video_row };
            prop_assert!((marks[0].rect.y - band.1).abs() < 0.01 && marks[0].rect.contains(Point::new(x, band.1 + 5.0)), "{:?}", marks);
            let ghost = bench.editor.bin_ghost().unwrap();
            prop_assert!(ghost.caution && ghost.words.ends_with(why), "{}", ghost.words);
        }
        screen.let_go(&mut bench.editor);
        screen.modifiers = Modifiers::NONE;
        prop_assert_eq!(bench.editor.doc.edit.clone(), edit, "it went somewhere");
        prop_assert!(!bench.editor.doc.can_undo());
        prop_assert_eq!(&bench.editor.bin_state.drag, &Drag::Idle);
        prop_assert!(bench.editor.bin_ghost().is_none() && bench.editor.bin_marks(TRACKS).is_empty());
        prop_assert_eq!(bench.refusal().is_some(), refused.is_some(), "{:?}", bench.refusal());
    }
}

// ---- The row's menu, Import… and dropped files ----

use super::bin_files::{FilesMessage, Place};
use super::bin_menu::{self, Act, Row};
use crate::bins::Target;
use crate::editing::BinItem;
use crate::import_files::Report;

impl Screen {
    fn right_press(&mut self, editor: &mut Editor, at: Point) -> Vec<String> {
        self.bins(editor, Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)), at)
    }
}

/// The message of the menu row labelled `label`.
fn menu_row(editor: &Editor, label: &str) -> Option<Message> {
    let (target, _) = editor.bin_state.more.menu.clone()?;
    bin_menu::entries(&editor.bin_menu_state(&target), &target).into_iter().find_map(|row| match row {
        Row::Item(l, message) if l == label => Some(message),
        _ => None,
    })
}

proptest! {
    #![proptest_config(ProptestConfig { cases: CASES, failure_persistence: None, ..ProptestConfig::default() })]

    /// A right press on a row opens the menu of what the row stands for (a
    /// shot, every picked row when it is one of them, a sequence), in the
    /// list and the grid, and each row of it does what it says: Insert and
    /// Overlay put those shots at the playhead, in order, as one undo step;
    /// Copy puts their names or URIs on the clipboard, a line each; Farm
    /// Submit changes nothing here and starts at most the Farm Submit
    /// window on exactly those shots. On a read-only edit no row changes
    /// the cut. A right press off the rows has no menu.
    #[test]
    fn a_right_press_opens_the_rows_menu_and_its_rows_do_what_they_say(
        sizes in prop::collection::vec(1usize..4, 1..3), which in any::<prop::sample::Index>(), kind in 0usize..3, grid in any::<bool>(),
        read_only in any::<bool>(), overlay in any::<bool>(),
    ) {
        let (edit, shots) = (empty_cut(), bin_of(&sizes));
        let which = which.index(shots.len());
        let mut bench = Bench::new(&edit, read_only);
        bench.editor.bins.shots = shots.clone();
        bench.send(Message::Bin(BinMessage::Grid(grid)));
        let mut screen = Screen::new();
        let rects = screen.item_rects(&mut bench.editor);
        let sequence: Vec<usize> = (0..shots.len()).filter(|&i| shots[i].sequence == shots[which].sequence).collect();
        let (at, goes, target) = match kind {
            0 => (middle(rects[&which]), vec![which], Target::Item(which)),
            1 => {
                let other = (which + 1) % shots.len();
                screen.click(&mut bench.editor, middle(rects[&which]));
                screen.modifiers = command();
                bench.editor.bin_state.last_press = None;
                screen.store = StateStore::new();
                if other != which {
                    screen.click(&mut bench.editor, middle(rects[&other]));
                }
                screen.modifiers = Modifiers::NONE;
                let mut goes = vec![which, other];
                goes.sort_unstable();
                goes.dedup();
                let target = if goes.len() == 1 { Target::Item(which) } else { Target::Items(goes.clone()) };
                (middle(rects[&which]), goes, target)
            }
            _ => {
                let chevron = chevron_at(rects[&sequence[0]], grid);
                (Point::new(chevron.x + 110.0, chevron.y), sequence.clone(), Target::Sequence(shots[which].sequence.clone()))
            }
        };
        let before = bench.editor.doc.edit.clone();
        screen.right_press(&mut bench.editor, at);
        prop_assert_eq!(bench.editor.bin_state.more.menu.clone().map(|m| m.0), Some(target.clone()));
        prop_assert_eq!(&bench.editor.bin_state.drag, &Drag::Idle, "a right press held the row");
        prop_assert_eq!(bench.editor.doc.edit.clone(), before.clone(), "opening the menu changed the cut");
        let state = bench.editor.bin_menu_state(&target);
        prop_assert_eq!((state.count, state.shots, state.writable), (goes.len(), true, !read_only));

        // Copy: at the next event the words are on the clipboard.
        let names: Vec<String> = goes.iter().map(|&i| shots[i].name.clone()).collect();
        let uris: Vec<String> = goes.iter().map(|&i| shots[i].uri.clone()).collect();
        for (what, lines) in [("Name", &names), ("URI", &uris)] {
            let row = menu_row(&bench.editor, &bin_menu::copy_label(what, goes.len())).expect("a Copy row");
            bench.send(row);
            screen.move_to(&mut bench.editor, Point::new(3.0, 3.0));
            prop_assert_eq!(screen.clipboard.0.last(), Some(&lines.join("\n")));
            prop_assert!(bench.editor.bin_state.more.copy.is_none(), "copied again at every event");
            prop_assert!(bench.editor.status.as_ref().is_some_and(|s| !s.1 && s.0.starts_with("Copied")));
        }
        let copies = screen.clipboard.0.len();
        screen.move_to(&mut bench.editor, Point::new(4.0, 4.0));
        prop_assert_eq!(screen.clipboard.0.len(), copies);

        // Farm Submit: the window on these shots, or why not; never the cut.
        let farm = menu_row(&bench.editor, bin_menu::FARM).expect("a Farm Submit row where Houdini is found");
        prop_assert_eq!(format!("{farm:?}"), format!("{:?}", Message::Bin(BinMessage::Act(Act::FarmSubmit, target.clone()))));
        bench.send(farm);
        let launch = bench.editor.bin_state.more.launched.take().expect("Farm Submit's window");
        let expected: Vec<String> = uris.iter().flat_map(|u| ["--entity".to_owned(), u.clone()]).collect();
        prop_assert_eq!(&launch.args[..4], ["-m", "tumblepipe.farm_app", "--context", "shots"]);
        prop_assert_eq!(&launch.args[4..], &expected[..]);
        prop_assert!(bench.editor.status.as_ref().is_some_and(|s| !s.1 && s.0.contains("nothing is submitted until")));
        prop_assert_eq!(bench.editor.doc.edit.clone(), before.clone());

        // Insert and Overlay.
        let label = if overlay { bin_menu::OVERLAY } else { bin_menu::INSERT };
        match menu_row(&bench.editor, label) {
            None => {
                prop_assert!(read_only, "{} is missing on an edit that can change", label);
                prop_assert!(menu_row(&bench.editor, bin_menu::INSERT).is_none() && menu_row(&bench.editor, bin_menu::OVERLAY).is_none());
                // Whatever the menu does offer, the cut stays.
                let (target, _) = bench.editor.bin_state.more.menu.clone().unwrap();
                for row in bin_menu::entries(&bench.editor.bin_menu_state(&target), &target) {
                    if let Row::Item(label, message) = row
                        && label != bin_menu::REVEAL
                    {
                        bench.send(message);
                    }
                }
                prop_assert_eq!(bench.editor.doc.edit.clone(), before);
                prop_assert!(!bench.editor.doc.can_undo());
            }
            Some(row) => {
                prop_assert!(!read_only);
                bench.send(row);
                prop_assert_eq!(bench.refusal(), None);
                let put = shot_clips(&bench.editor.doc.edit);
                let mut next = 0;
                prop_assert_eq!(put.len(), goes.len());
                for (clip, &index) in put.iter().zip(&goes) {
                    let length = shots[index].last - shots[index].first + 1;
                    prop_assert_eq!((&clip.0, clip.2, clip.3), (&shots[index].uri, next, length));
                    next += length;
                }
                bench.send(Message::Undo);
                prop_assert_eq!(bench.editor.doc.edit.clone(), before);
            }
        }

        // Off the rows: no menu.
        let below = rects.values().map(|r| r.y + r.height).fold(0.0, f32::max) + 60.0;
        screen.right_press(&mut bench.editor, Point::new(at.x, below));
        prop_assert!(bench.editor.bin_state.more.menu.is_none(), "a menu for a press off the rows: {:?}", bench.editor.bin_state.more.menu);
    }

}

proptest! {
    #![proptest_config(ProptestConfig { cases: 60, failure_persistence: None, ..ProptestConfig::default() })]

    /// When an import ends, the status names every item made and every
    /// file that did not go in, with why; the items are in the Media bin,
    /// which comes to the front with the first picked. Files dropped on a
    /// track are then put in the cut there, or the status says why not;
    /// files that made nothing put nothing.
    #[test]
    fn an_import_says_what_became_of_every_file(
        // Item names the pipeline takes: `aux`, `con`, `nul` and `prn` are
        // Windows device names, refused by the import below.
        made in prop::collection::btree_set("[a-z]{3,6}".prop_filter("a Windows device name", |n| th_pipe::naming::is_valid_entity_name(n)), 0..4),
        not in prop::collection::vec(("[a-z]{2,5}\\.txt", "[a-z ]{3,12}"), 0..3),
        frame in 0i64..12, insert in any::<bool>(), dropped in any::<bool>(), read_only in prop::bool::weighted(0.2),
    ) {
        let edit = cut_of(&[vec![(2, 6)]]);
        // A project of its own, with the items in its library as an import
        // leaves them.
        let home = tempfile::tempdir().unwrap();
        let root = home.path().join("Show");
        copy_folder(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template"), &root);
        let mut bench = Bench::at(&edit, read_only, tempfile::tempdir().unwrap());
        bench.editor.paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let file = home.path().join("in.mov");
        std::fs::write(&file, b"movie").unwrap();
        let project = bench.editor.paths.open().unwrap();
        for name in &made {
            let measure = media::Measure { frames: Some((12, (24, 1))), seconds: 0.5, sound: false };
            media::import(&project, &file, name, measure, "").unwrap();
        }
        bench.editor.bins.shots = bin_of(&[2]);
        bench.editor.bins.media = vec![BinItem { item: "zold".into(), version: "v0003".into(), frames: Some(9), sound: None }];
        let items: Vec<BinItem> = made.iter().map(|name| BinItem { item: name.clone(), version: "v0001".into(), frames: Some(12), sound: None }).collect();
        let report = Report { imported: items.iter().map(|i| (i.item.clone(), i.version.clone())).collect(), not: not.clone() };
        let place = dropped.then_some(Place { track: TrackRef::video(0), frame, insert });
        bench.editor.dialog = Some(Dialog::Busy("Importing".into()));
        bench.send(Message::Files(FilesMessage::Imported(report, items.clone(), place)));
        prop_assert!(bench.editor.dialog.is_none(), "the window still waits");
        let (said, error) = bench.editor.status.clone().expect("a status");
        for item in &items {
            let went = format!("{} v0001", item.item);
            prop_assert!(said.contains(&went), "{:?}", said);
            prop_assert!(bench.editor.bins.media.contains(item));
        }
        for (name, why) in &not {
            let stayed = format!("{name} ({why})");
            prop_assert!(said.contains(&stayed), "{:?}", said);
        }
        let names: Vec<&str> = bench.editor.bins.media.iter().map(|m| m.item.as_str()).collect();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        prop_assert_eq!(&names, &sorted, "the Media bin out of order");
        prop_assert_eq!(names.len(), items.len() + 1, "an item twice, or the old one gone");
        if items.is_empty() {
            prop_assert_eq!(bench.editor.bin_tab, 0);
            prop_assert_eq!(bench.editor.doc.edit.clone(), edit);
            prop_assert_eq!(error, !not.is_empty());
            return Ok(());
        }
        prop_assert_eq!(bench.editor.bin_tab, 1);
        prop_assert_eq!(bench.editor.bin_pick.map(|p| bench.editor.bins.media[p].item.clone()), Some(items[0].item.clone()));
        let after = bench.editor.doc.edit.clone();
        let media: Vec<(String, EditFrame, usize)> = after.video.iter().enumerate().flat_map(|(i, t)| t.clips.iter().map(move |c| (i, c))).filter_map(|(i, c)| match &c.source {
            Source::Media { item, .. } => Some((item.clone(), c.at, i)),
            _ => None,
        }).collect();
        prop_assert_eq!(said.contains("Not put in the cut"), dropped && read_only, "{:?}", said);
        if read_only || !dropped {
            prop_assert!(media.is_empty());
            prop_assert_eq!(bench.editor.doc.edit.clone(), edit);
            prop_assert_eq!(error, dropped || !not.is_empty());
        } else {
            prop_assert_eq!(error, !not.is_empty());
            prop_assert_eq!(media.iter().map(|m| m.0.clone()).collect::<Vec<_>>(), items.iter().map(|i| i.item.clone()).collect::<Vec<_>>());
            prop_assert_eq!(media[0].1, frame);
            if insert {
                prop_assert!(media.iter().all(|m| m.2 == 0), "inserted off the track under the pointer: {:?}", media);
            } else {
                prop_assert_eq!(slugs(&after), slugs(&edit), "laid over, and what was there moved");
            }
            bench.send(Message::Undo);
            prop_assert_eq!(bench.editor.doc.edit.clone(), edit);
        }
        prop_assert!(!read_only || media.is_empty(), "a read-only edit took dropped files");
    }
}

fn copy_folder(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap().map(Result::unwrap) {
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_folder(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// Import… asks the system's dialog and opens nothing of its own; the
/// files picked are imported (the window waits); none picked is a Cancel;
/// where the system has no dialog, the typed one opens and says why.
#[test]
fn import_asks_the_system_for_files() {
    let mut bench = Bench::new(&empty_cut(), false);
    bench.send(Message::Open(OpenDialog::Import));
    assert!(bench.editor.dialog.is_none(), "Import… opened a dialog of its own");
    bench.send(Message::Files(FilesMessage::Picked(Ok(Vec::new()))));
    assert!(bench.editor.dialog.is_none() && bench.editor.status.is_none(), "a Cancel did something");
    bench.send(Message::Files(FilesMessage::Picked(Ok(vec![PathBuf::from("/in/a.mov"), PathBuf::from("/in/b.wav")]))));
    assert!(matches!(&bench.editor.dialog, Some(Dialog::Busy(what)) if what.contains("2 files")), "the window does not wait");
    assert_eq!(bench.editor.bin_state.more.folder, Some(PathBuf::from("/in")), "the next Import… starts elsewhere");
    bench.editor.dialog = None;
    bench.send(Message::Files(FilesMessage::Dropped(vec![PathBuf::from("/in/c.mov")], None)));
    assert!(matches!(&bench.editor.dialog, Some(Dialog::Busy(what)) if what.contains("1 file")), "dropped files are not imported");
    bench.editor.dialog = None;
    bench.send(Message::Files(FilesMessage::Picked(Err(crate::open_dialog::NO_DIALOG.into()))));
    assert!(matches!(bench.editor.dialog, Some(Dialog::Import { .. })), "no typed path where there is no dialog");
    assert!(bench.refusal().is_some_and(|s| s.contains(crate::open_dialog::NO_DIALOG)));
}

/// A media item that clips of the open edit name is not renamed under
/// them, and a bad name is refused: both before anything is written.
#[test]
fn an_item_in_the_cut_is_not_renamed() {
    let mut edit = cut_of(&[vec![(0, 5)]]);
    let source = Source::Media { item: "music".into(), version: th_edit::document::VersionRule::Latest };
    edit.video[0].clips.push(th_edit::document::VideoClip::new(20, 4, 0, source));
    assert!(bin_menu::rename_problem(&edit, "music").is_some_and(|why| why.contains("1 clip")));
    assert_eq!(bin_menu::rename_problem(&edit, "other"), None);
    let mut bench = Bench::new(&edit, false);
    let item = bin_menu::MediaEdit {
        item: "music".into(),
        version: "v0001".into(),
        name: "score".into(),
        note: String::new(),
        noted: String::new(),
    };
    bench.editor.dialog = Some(Dialog::MediaEdit(item.clone()));
    bench.send(Message::Confirm);
    assert!(bench.refusal().is_some_and(|s| s.contains("in this edit")), "{:?}", bench.editor.status);
    assert!(matches!(bench.editor.dialog, Some(Dialog::MediaEdit(_))), "the dialog closed on a refusal");
    bench.editor.dialog = Some(Dialog::MediaEdit(bin_menu::MediaEdit { item: "other".into(), name: "bad name".into(), ..item }));
    bench.send(Message::Confirm);
    assert!(bench.refusal().is_some_and(|s| s.contains("letters and digits")), "{:?}", bench.editor.status);
}

/// What the timeline's menu on a shot's clip gained: the shot's URI on the
/// clipboard, and the farm's window on that shot where Houdini is found.
/// Neither changes the cut, also on a read-only edit.
#[test]
fn a_clips_menu_copies_its_uri_and_opens_the_farms_window() {
    let uri = "entity:/shots/S1/sh7";
    let rows = Editor::clip_menu_more(uri);
    assert_eq!(rows[0].0, "Copy URI");
    assert!(rows.len() <= 2 && rows.iter().skip(1).all(|r| r.0 == bin_menu::FARM));
    let edit = cut_of(&[vec![(0, 5)]]);
    let mut bench = Bench::new(&edit, true);
    let mut screen = Screen::new();
    bench.send(rows[0].1.clone());
    screen.move_to(&mut bench.editor, Point::new(3.0, 3.0));
    assert_eq!(screen.clipboard.0, [uri.to_owned()]);
    assert_eq!(rows.len(), 2, "the window can open and the menu has no row for it");
    bench.send(rows[1].1.clone());
    let launch = bench.editor.bin_state.more.launched.take().expect("Farm Submit's window");
    assert_eq!(launch.args, ["-m", "tumblepipe.farm_app", "--context", "shots", "--entity", uri]);
    assert_eq!(bench.editor.doc.edit, edit);
}

/// The farm's jobs on the timeline (`farm_jobs`): a shot's clip shows the
/// state of its shot's job and offers the job on a right-click; a slug, or
/// a shot with no job (or another project's), shows and offers nothing.
/// Show Farm Job opens the Farm Manager on that job id and nothing else,
/// Copy puts the id on the clipboard; neither changes the cut. One read of
/// the farm at a time.
#[test]
fn clips_show_their_shots_farm_job_and_offer_it() {
    use th_pipe::farm_jobs::{Job, ShotJobs, Source as Farm, Status};
    let mut edit = cut_of(&[vec![(0, 5), (0, 5), (0, 5), (0, 5)]]);
    let shots = ["entity:/shots/S1/sh1", "entity:/shots/S1/sh2", "entity:/shots/S1/sh3"];
    for (clip, shot) in edit.video[0].clips.iter_mut().zip(shots) {
        clip.source = Source::Shot { shot: shot.into(), take: th_edit::document::Rule::best() };
    }
    let mut bench = Bench::new(&edit, true);
    let project = bench.editor.project_name();
    assert_eq!(project, "project_template");
    let job = |id: &str, project: &str, shot: &str, status| Job {
        source: Farm::Deadline,
        id: id.into(),
        batch_key: format!("deadline:{id}"),
        batch: String::new(),
        name: "render".into(),
        project: project.into(),
        entity: shot.into(),
        status,
        submitted: 1,
    };
    let jobs = [job("6720aa", &project, shots[0], Status::Rendering), job("6720bb", "Other", shots[1], Status::Failed)];
    bench.send(Message::FarmJobs(ShotJobs::of(&project, &jobs)));

    let rows = bench.editor.rows();
    let clips = &rows.iter().find(|r| r.track == TrackRef::video(0)).expect("V1").clips;
    assert_eq!(clips.iter().map(|c| c.farm).collect::<Vec<_>>(), [Some(Status::Rendering), None, None, None]);
    let offered: Vec<Vec<(String, Message)>> = edit.video[0].clips.iter().map(|c| bench.editor.farm_job_rows(&c.source)).collect();
    assert_eq!(offered.iter().map(Vec::len).collect::<Vec<_>>(), [2, 0, 0, 0], "rows only on the clip whose shot has a job");
    assert!(offered[0][0].0.starts_with(crate::farm_jobs::SHOW));
    assert_eq!(offered[0][1].0, crate::farm_jobs::COPY);

    bench.send(offered[0][0].1.clone());
    let launch = bench.editor.bin_state.more.launched.take().expect("the Farm Manager");
    assert_eq!(launch.args, ["-m", "tumblepipe.farm.manager", "--job", "6720aa"]);
    bench.send(offered[0][1].1.clone());
    assert_eq!(bench.editor.bin_state.more.copy.as_deref(), Some("6720aa"));
    assert_eq!(bench.editor.doc.edit, edit, "the cut is untouched");

    // One read at a time: a second tick while one is out asks nothing.
    bench.send(Message::PollFarm);
    assert!(bench.editor.farm_asking);
    assert!(bench.editor.update(Message::PollFarm).is_empty(), "no second read while one is out");
    bench.send(Message::FarmJobs(ShotJobs::default()));
    assert!(!bench.editor.farm_asking);
    assert!(edit.video[0].clips.iter().all(|c| bench.editor.farm_job_rows(&c.source).is_empty()), "no farm, no rows");
}
