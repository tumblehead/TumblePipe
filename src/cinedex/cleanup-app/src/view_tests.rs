//! The real window, laid out and drawn without one: [`Window::view`] at the
//! window's own sizes, measured with Primer's own fonts, its drawing lowered
//! as the renderer lowers it, the rules scrolled by the wheel as a person
//! scrolls them, and the Confirm sheet taken from the modal's overlay and
//! sent keys. What is checked is what lands on screen.

use std::collections::BTreeSet;

use primer::core::clipboard::Clipboard;
use primer::core::measure::{TextMeasurer, line_height};
use primer::core::render::{Painted, Primitive, Quad, Text};
use primer::keyboard::{Key, Modifiers, Named};
use primer::renderer::{Fonts, TextMeasure};
use primer::widget::custom::{Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Shell, StateStore, Widget, keyboard, mouse};
use th_cleanup::core::Entry;

use super::tests::{NoFarm, project, run_scan, scan};
use super::*;

/// The window's size as it opens.
const WIDE: Size = Size { width: 1180.0, height: 780.0 };
/// A narrow window.
const NARROW: Size = Size { width: 760.0, height: 560.0 };

/// A clipboard nothing here uses.
struct NoClipboard;

impl Clipboard for NoClipboard {
    fn read(&mut self) -> Option<String> {
        None
    }
    fn write(&mut self, _: String) {}
}

/// What one frame puts on screen: each primitive the renderer would paint
/// with some part of it inside the window and inside the clip in force.
struct Frame {
    texts: Vec<Text>,
    quads: Vec<Quad>,
    /// Where the meshes (the icons) are.
    meshes: Vec<Rectangle>,
    /// Every primitive's bounds are finite numbers.
    finite: bool,
}

/// A window on screen, its widget state kept from frame to frame.
struct Screen {
    measure: TextMeasure,
    store: StateStore,
    theme: Theme,
    size: Size,
}

impl Screen {
    fn new(size: Size) -> Self {
        Screen { measure: TextMeasure::new(Fonts::load()), store: StateStore::new(), theme: theme(), size }
    }

    fn ctx(&self) -> LayoutCtx<'_> {
        LayoutCtx { measurer: &self.measure, store: &self.store, theme: &self.theme }
    }

    /// `window`'s view drawn at the screen's size.
    fn frame(&self, window: &Window) -> Frame {
        self.draw(&window.view(window::Id::MAIN))
    }

    /// `view` laid out and drawn.
    fn draw(&self, view: &Element<Message>) -> Frame {
        let node = view.layout(&Limits::new(Size::ZERO, self.size), self.ctx());
        let screen = Rectangle::new(Point::ORIGIN, self.size);
        let draw = Ctx { cursor: Cursor::Unavailable, theme: &self.theme, store: &self.store, measurer: &self.measure, visible: screen };
        let drawn = view.draw(Layout::new(&node), draw);
        let mut frame = Frame { texts: Vec::new(), quads: Vec::new(), meshes: Vec::new(), finite: true };
        drawn.lower(&mut |painted: Painted<'_>| {
            let bounds = painted.primitive.bounds();
            let finite = [bounds.x, bounds.y, bounds.width, bounds.height].iter().all(|v| v.is_finite() && v.abs() < 1.0e7);
            frame.finite &= finite;
            let shown = painted.clip.map_or(Some(screen), |clip| clip.intersection(screen));
            if !finite || !shown.is_some_and(|area| area.intersection(bounds).is_some()) {
                return;
            }
            match &*painted.primitive {
                Primitive::Text(text) => frame.texts.push(text.clone()),
                Primitive::Quad(quad) => frame.quads.push(*quad),
                Primitive::Mesh(_) => frame.meshes.push(bounds),
                _ => {}
            }
        });
        frame
    }

    /// `event` sent to `view` laid out at the screen's size, the pointer at
    /// `at`: the messages it publishes.
    fn send(&mut self, view: &mut Element<Message>, event: &Event, at: Point) -> Vec<Message> {
        let mut messages = Vec::new();
        let node = view.layout(&Limits::new(Size::ZERO, self.size), self.ctx());
        let mut clipboard = NoClipboard;
        let mut shell =
            Shell::new(&mut messages, &mut self.store, &self.measure, &mut clipboard, &self.theme, Cursor::Available(at), Modifiers::NONE);
        view.on_event(event, Layout::new(&node), &mut shell);
        messages
    }

    /// The wheel turned `by` pixels (negative: down) with the pointer at
    /// `at`; the window is given what the view published.
    fn wheel(&mut self, window: &mut Window, at: Point, by: f32) {
        let mut view = window.view(window::Id::MAIN);
        let event = Event::Mouse(mouse::Event::WheelScrolled { delta: Vector::new(0.0, by) });
        for message in self.send(&mut view, &event, at) {
            drop(window.update(message));
        }
    }

    /// The Confirm sheet the modal floats over the window, as an element.
    fn sheet(&self, window: &Window) -> Element<Message> {
        let view = window.view(window::Id::MAIN);
        let node = view.layout(&Limits::new(Size::ZERO, self.size), self.ctx());
        let screen = Rectangle::new(Point::ORIGIN, self.size);
        let draw = Ctx { cursor: Cursor::Unavailable, theme: &self.theme, store: &self.store, measurer: &self.measure, visible: screen };
        let overlay = view.overlay(Layout::new(&node), draw).expect("the Confirm is up");
        assert!(overlay.modal.is_some(), "the Confirm blocks the window");
        overlay.content
    }

    /// The ink of a drawn text: where its glyphs are, not its box.
    fn ink(&self, text: &Text) -> Rectangle {
        let width = self.measure.measure_styled(&text.content, &text.spans, text.size, None).width.min(text.bounds.width.max(0.0));
        let x = match text.align {
            Horizontal::Left => text.bounds.x,
            Horizontal::Center => text.bounds.x + (text.bounds.width - width) / 2.0,
            Horizontal::Right => text.bounds.x + text.bounds.width - width,
        };
        Rectangle::new(Point::new(x, text.bounds.y), Size::new(width, text.bounds.height))
    }
}

fn key(named: Named) -> Event {
    Event::Keyboard(keyboard::Event::KeyPressed { key: Key::Named(named), modifiers: Modifiers::NONE })
}

fn titles() -> Vec<&'static str> {
    core::categories().iter().map(|c| c.title).collect()
}

/// The painted words `content`, if on screen.
fn find<'a>(frame: &'a Frame, content: &str) -> Option<&'a Text> {
    frame.texts.iter().find(|t| t.content == content)
}

fn find_start<'a>(frame: &'a Frame, start: &str) -> Option<&'a Text> {
    frame.texts.iter().find(|t| t.content.starts_with(start))
}

fn inside(outer: Rectangle, inner: Rectangle) -> bool {
    inner.x >= outer.x - 0.5
        && inner.y >= outer.y - 0.5
        && inner.x + inner.width <= outer.x + outer.width + 0.5
        && inner.y + inner.height <= outer.y + outer.height + 0.5
}

/// The same colour, whatever its opacity: a notice fades in from its first
/// frame.
fn rgb(a: Color, b: Color) -> bool {
    (a.r, a.g, a.b) == (b.r, b.g, b.b)
}

/// The number fields on screen: the boxes as wide as a field.
fn fields(frame: &Frame) -> Vec<Rectangle> {
    frame.quads.iter().filter(|q| (q.bounds.width - FIELD_WIDTH).abs() < 0.5 && q.style.border.width > 0.0).map(|q| q.bounds).collect()
}

/// The Primary buttons on screen: an `accent_deep` fill with words in it (a
/// ticked box is that fill too, with none).
fn primaries(frame: &Frame, theme: &Theme) -> usize {
    frame
        .quads
        .iter()
        .filter(|q| q.style.background == theme.accent_deep)
        .filter(|q| frame.texts.iter().any(|t| inside(q.bounds, t.bounds)))
        .count()
}

/// The box of the button whose label is `label` (the smallest outlined or
/// filled quad around it, at least a button tall), right of the rules.
fn button_of(frame: &Frame, window: &Window, label: &str) -> Rectangle {
    let words = frame
        .texts
        .iter()
        .filter(|t| t.content == label && t.bounds.x > window.left)
        .max_by(|a, b| a.bounds.y.total_cmp(&b.bounds.y))
        .unwrap_or_else(|| panic!("{label:?} drawn"));
    frame
        .quads
        .iter()
        .filter(|q| q.bounds.height >= 20.0 && q.bounds.width < 400.0 && inside(q.bounds, words.bounds))
        .filter(|q| q.style.border.width > 0.0 || q.style.background.a > 0.0)
        .map(|q| q.bounds)
        .min_by(|a, b| (a.width * a.height).total_cmp(&(b.width * b.height)))
        .unwrap_or_else(|| panic!("{label:?}'s button"))
}

/// Scan (labelled `scan`) sits directly left of Purge, `spacing.sm` apart,
/// on one row at the right of the results side, and nothing is drawn under
/// that row (there is no status line).
fn scan_beside_purge(frame: &Frame, window: &Window, scan: &str, size: Size) {
    let theme = theme();
    let (scan_box, purge) = (button_of(frame, window, scan), button_of(frame, window, PURGE));
    assert!((scan_box.center().y - purge.center().y).abs() < 1.0, "{scan} and Purge on one row at {size:?}");
    let gap = purge.x - (scan_box.x + scan_box.width);
    assert!((gap - theme.spacing.sm).abs() < 1.5, "{scan} directly left of Purge at {size:?}: gap {gap}");
    assert!(purge.x + purge.width > size.width - 40.0, "at the right at {size:?}");
    let bottom = purge.y + purge.height;
    assert!(!frame.texts.iter().any(|t| t.bounds.y > bottom + 2.0 && !t.content.trim().is_empty()), "nothing under the row at {size:?}");
}

/// None of the reasons the window says elsewhere is repeated beside Purge
/// (on the bottom row: a scanning table row may say "Scanning…").
fn no_reason(frame: &Frame) -> bool {
    let repeated = ["Scan first.", "Rules changed: scan again.", "Scanning\u{2026}", "Purging\u{2026}", "Nothing to purge."];
    let Some(purge) = frame.texts.iter().filter(|t| t.content == PURGE).max_by(|a, b| a.bounds.y.total_cmp(&b.bounds.y)) else {
        return true;
    };
    let line = purge.bounds.center().y;
    !frame.texts.iter().any(|t| repeated.contains(&t.content.as_str()) && (t.bounds.center().y - line).abs() < 6.0)
}

/// The live Danger buttons with `words` on them.
fn dangers<'a>(frame: &'a Frame, theme: &Theme, words: &str) -> Vec<&'a Quad> {
    frame
        .quads
        .iter()
        .filter(|q| q.style.border.width > 0.0 && q.style.border.color == theme.danger.base)
        .filter(|q| frame.texts.iter().any(|t| t.content.starts_with(words) && inside(q.bounds, t.bounds)))
        .collect()
}

/// The window's rules from the top to the bottom, a wheel notch at a time:
/// each frame drawn.
fn scrolled_through(window: &mut Window, size: Size) -> Vec<Frame> {
    let mut screen = Screen::new(size);
    let first = screen.frame(window);
    let at = {
        let title = find(&first, titles()[0]).expect("the first category at the top");
        Point::new(title.bounds.x + 4.0, title.bounds.y + 4.0)
    };
    let mut frames = vec![first];
    for _ in 0..100 {
        screen.wheel(window, at, -60.0);
        let frame = screen.frame(window);
        let same = frames.last().is_some_and(|last: &Frame| texts_of(last) == texts_of(&frame));
        frames.push(frame);
        if same {
            break;
        }
    }
    frames
}

fn texts_of(frame: &Frame) -> Vec<(String, u32)> {
    frame.texts.iter().map(|t| (t.content.clone(), t.bounds.y.to_bits())).collect()
}

/// A window on `project`, scanned with no farm.
fn scanned(project: &Path) -> Window {
    let mut window = Window::new(project.display().to_string());
    scan(&mut window);
    assert!(window.scan_error.is_none(), "{:?}", window.scan_error);
    window
}

/// A scan that ends with `plan`, as if the project held it.
fn deliver(window: &mut Window, plan: Plan) {
    let _ = window.update(Message::Scan);
    let generation = window.busy.as_ref().expect("scanning").generation;
    let _ = window.update(Message::Scanned(generation, Ok(Arc::new(plan))));
}

/// The scan shown, its categories given one entry to purge of each size in
/// `sizes` (none for a zero).
fn spread(window: &mut Window, sizes: [u64; 6]) {
    let mut plan = (**window.plan.as_ref().unwrap()).clone();
    let template: Entry = plan.categories.iter().flat_map(|c| c.entries.iter()).find(|e| e.prune).cloned().expect("the old stamp");
    for (category, size) in plan.categories.iter_mut().zip(sizes) {
        category.entries = if size > 0 { vec![Entry { size, ..template.clone() }] } else { Vec::new() };
        category.notes.clear();
    }
    deliver(window, plan);
}

/// Where each category's row is drawn in the table: its name's words.
fn row_of<'a>(frame: &'a Frame, window: &Window, title: &str) -> &'a Text {
    frame.texts.iter().find(|t| t.content == title && t.bounds.x > window.left).unwrap_or_else(|| panic!("{title}'s row"))
}

/// The quads `height` tall on `row`'s line, right of the rules.
fn on_line<'a>(frame: &'a Frame, window: &Window, row: &Text, height: f32) -> Vec<&'a Quad> {
    frame
        .quads
        .iter()
        .filter(|q| q.bounds.x > window.left && (q.bounds.height - height).abs() < 0.5)
        .filter(|q| (q.bounds.center().y - row.bounds.center().y).abs() < 6.0)
        .collect()
}

/// The stacked bar's segments: the quads as tall as it, right of the rules.
fn segments<'a>(frame: &'a Frame, window: &Window) -> Vec<&'a Quad> {
    frame
        .quads
        .iter()
        .filter(|q| q.bounds.x > window.left && (q.bounds.height - bars::HEIGHT).abs() < 0.5 && q.bounds.width > 0.0)
        .collect()
}

/// At the size the window opens and at a narrow one, every category's
/// rules are drawn with its name whole on one line, every number field
/// starts at one left edge, and no words on the rules side overlap.
#[test]
fn every_category_row_lines_up_at_both_sizes() {
    for size in [WIDE, NARROW] {
        let mut window = Window::new(String::new());
        let frames = scrolled_through(&mut window, size);
        let screen = Screen::new(size);
        assert!(frames.iter().all(|f| f.finite), "something is laid out at an unbounded size at {size:?}");
        let seen: BTreeSet<&str> = frames.iter().flat_map(|f| titles().into_iter().filter(|t| find(f, t).is_some())).collect();
        assert_eq!(seen, titles().into_iter().collect::<BTreeSet<_>>(), "a category never drawn at {size:?}");
        let mut edges = Vec::new();
        for frame in &frames {
            for title in titles() {
                if let Some(drawn) = find(frame, title) {
                    let words = screen.measure.measure_styled(&drawn.content, &drawn.spans, drawn.size, None);
                    assert!(drawn.bounds.width + 0.5 >= words.width, "{title} cut at {size:?}: {:?}", drawn.bounds);
                    assert!(drawn.bounds.height < 1.5 * line_height(drawn.size), "{title} on more than a line at {size:?}");
                }
            }
            edges.extend(fields(frame).iter().map(|f| f.x));
            let left: Vec<Rectangle> = frame
                .texts
                .iter()
                .filter(|t| t.bounds.x < window.left && !t.content.trim().is_empty())
                .map(|t| screen.ink(t))
                .filter(|r| r.width > 0.0)
                .collect();
            for (i, a) in left.iter().enumerate() {
                for b in &left[i + 1..] {
                    if let Some(both) = a.intersection(*b) {
                        assert!(both.width < 1.0 || both.height < 1.0, "words overlap at {size:?}: {a:?} {b:?}");
                    }
                }
            }
            // The rules have no ticks: no box is ticked or tickable there.
            assert!(!frame.quads.iter().any(|q| q.bounds.x < window.left && q.style.background == screen.theme.accent_deep));
        }
        let distinct: BTreeSet<u32> = edges.iter().map(|x| x.round() as u32).collect();
        assert_eq!(distinct.len(), 1, "number fields start at {distinct:?} at {size:?}");
        let all: usize = form::all_fields().count();
        assert!(frames.iter().map(|f| fields(f).len()).max().unwrap_or(0) >= all.min(4), "the fields are drawn");
        for field in form::all_fields() {
            let width = screen.measure.measure_text(field.label, screen.theme.typography.body, None).width;
            assert!(width <= LABEL_WIDTH, "{:?} is {width} wide", field.label);
        }
        assert!(frames.iter().any(|f| find(f, "Rules").is_some()), "the rules side's section");
    }
}

/// On open nothing scans: the results side says what a scan does (an empty
/// state with no button of its own, which is also why Purge is disabled:
/// no reason beside it), and the bottom row reads Scan, the window's one
/// Primary, directly left of a disabled "Purge".
/// The title names the project; no path, no status line. There is no field
/// for the project and no scope to choose.
#[test]
fn nothing_scans_on_open_and_scan_is_the_one_primary() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let window = Window::new(project.display().to_string());
    let theme = theme();
    let frame = Screen::new(WIDE).frame(&window);
    assert!(window.busy.is_none() && window.plan.is_none(), "nothing scans on open");
    assert_eq!(window.title(), "Clean Up Project \u{2014} Demo");
    let heading = find(&frame, "Scan the project").expect("the empty state");
    let body = find(&frame, SCAN_BODY).expect("what a scan does");
    let scans: Vec<&Text> = frame.texts.iter().filter(|t| t.content == "Scan").collect();
    assert_eq!(scans.len(), 1, "one Scan button, none in the empty state");
    assert!(scans[0].bounds.y > body.bounds.y + body.bounds.height + 40.0 && heading.bounds.y < body.bounds.y, "Scan is in the bottom row");
    assert_eq!(primaries(&frame, &theme), 1, "exactly one Primary before a scan");
    assert!(frame.quads.iter().any(|q| q.style.background == theme.accent_deep && inside(q.bounds, scans[0].bounds)), "and it is Scan");
    let purge = find(&frame, PURGE).expect("Purge, disabled, its label just the verb");
    assert_eq!(purge.color, theme.text_disabled);
    assert!(no_reason(&frame), "the empty state says why; nothing beside Purge repeats it");
    assert!(dangers(&frame, &theme, "Purge").is_empty());
    assert!(find(&frame, "Not scanned yet. Nothing is purged until you confirm.").is_none(), "no status line");
    assert!(!frame.texts.iter().any(|t| t.content.contains(&project.display().to_string())), "no path anywhere");
    assert!(find(&frame, "Scan Again").is_none(), "Scan Again only after a scan");
    for size in [WIDE, NARROW] {
        scan_beside_purge(&Screen::new(size).frame(&window), &window, "Scan", size);
    }
    // No toolbar: no project field, no scope.
    for gone in ["Project", "Look in", "Shots and assets"] {
        assert!(find(&frame, gone).is_none(), "{gone:?} is drawn");
    }
    assert!(fields(&frame).iter().all(|f| f.x < window.left), "the only fields are the rules'");
}

/// Started without a project: an empty state says so, and there is no Scan
/// and no Purge, nor anywhere to type one in.
#[test]
fn no_project_shows_an_empty_state_without_scan() {
    let window = Window::new(String::new());
    let frame = Screen::new(WIDE).frame(&window);
    assert_eq!(window.title(), "Clean Up Project");
    assert!(find(&frame, NO_PROJECT).is_some());
    assert!(find(&frame, NO_PROJECT_BODY).is_some());
    for gone in ["Scan", "Scan Again", "Scan the project"] {
        assert!(find(&frame, gone).is_none(), "{gone:?} is drawn");
    }
    assert!(find_start(&frame, "Purge").is_none());
    assert_eq!(primaries(&frame, &theme()), 0);
    assert!(fields(&frame).iter().all(|f| f.x < window.left));
}

/// After a scan of every category: all six rows, every one ticked that has
/// something to purge; Purge (Danger) is live and nothing is Primary; Scan
/// Again sits directly left of Purge.
#[test]
fn a_scan_covers_every_category_and_enables_purge() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let window = scanned(&project);
    let theme = theme();
    let keys: Vec<&str> = window.plan.as_ref().unwrap().categories.iter().map(|c| c.key.as_str()).collect();
    assert_eq!(keys, CATEGORY_KEYS.to_vec(), "a scan covers every category");
    assert!(window.can_purge());
    let frame = Screen::new(WIDE).frame(&window);
    for title in titles() {
        row_of(&frame, &window, title);
    }
    assert_eq!(primaries(&frame, &theme), 0, "no Primary after a scan");
    let purge = dangers(&frame, &theme, PURGE);
    assert_eq!(purge.len(), 1, "Purge, live, the one Danger");
    assert!(frame.texts.iter().any(|t| t.content == PURGE && inside(purge[0].bounds, t.bounds)), "labelled just \"Purge\"");
    assert!(no_reason(&frame));
    for size in [WIDE, NARROW] {
        scan_beside_purge(&Screen::new(size).frame(&window), &window, "Scan Again", size);
    }
    assert!(find_start(&frame, "Scanned at ").is_none(), "no status line");
    assert!(find_start(&frame, "4 B can be freed").is_some_and(|t| t.color == theme.text));
    assert!(find(&frame, "Purge \u{00b7} Category").is_some(), "the tick column's header");
    assert_eq!(segments(&frame, &window).len(), 1, "one segment: the one category that frees anything");
}

/// Unticking a category changes the totals, the stacked bar and its row's
/// bar at once, with no scan; its row stays, dimmed. Ticking it back
/// restores them. Neither makes the scan stale.
#[test]
fn unticking_changes_the_totals_without_a_scan() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    spread(&mut window, [0, 2048, 0, 1024, 0, 4]);
    let theme = theme();
    let generation = window.generation;
    assert_eq!((window.count(), window.size()), (3, 3076));
    let renders = core::categories()[1].title;
    let before = Screen::new(WIDE).frame(&window);
    assert!(find(&before, &format!("{} can be freed", human(3076))).is_some(), "the headline");
    assert_eq!(segments(&before, &window).len(), 3);

    let _ = window.update(Message::Tick("renders".into(), false));
    assert_eq!(window.generation, generation, "no scan");
    assert!(window.busy.is_none() && !window.stale, "unticking does not make the scan stale");
    assert_eq!((window.count(), window.size()), (2, 1028));
    let after = Screen::new(WIDE).frame(&window);
    assert!(find(&after, &format!("{} can be freed", human(1028))).is_some(), "the headline follows");
    assert!(find_start(&after, "2 of ").is_some(), "the count follows");
    assert!(find(&after, PURGE).is_some(), "Purge stays just the verb");
    assert!(window.confirm_data().unwrap().body.starts_with("2 old items "), "the Confirm's count follows");
    let row = row_of(&after, &window, renders);
    assert_eq!(row.color, theme.text_muted, "the unticked row is dimmed, not hidden");
    let grey: Vec<_> = segments(&after, &window).into_iter().filter(|q| rgb(q.style.background, theme.border_strong)).collect();
    assert_eq!(grey.len(), 1, "its segment greys");
    assert!(on_line(&after, &window, row, ROW_BAR).iter().any(|q| rgb(q.style.background, theme.border_strong)), "its row bar greys");

    // Nothing ticked: nothing to purge, and it says why.
    for key in ["backups", "deleted"] {
        let _ = window.update(Message::Tick(key.into(), false));
    }
    assert_eq!(window.purge_reason(), Some(NOTHING_TICKED));
    let frame = Screen::new(WIDE).frame(&window);
    // The one reason nothing else says, beside a disabled Purge.
    let reason = find(&frame, NOTHING_TICKED).expect("the reason beside Purge");
    let purge = find(&frame, PURGE).expect("Purge");
    assert!(dangers(&frame, &theme, PURGE).is_empty(), "Purge disabled");
    assert!(reason.bounds.x < purge.bounds.x && (reason.bounds.center().y - purge.bounds.center().y).abs() < 6.0);
    for key in ["renders", "backups", "deleted"] {
        let _ = window.update(Message::Tick(key.into(), true));
    }
    assert_eq!((window.count(), window.size()), (3, 3076));
    assert!(window.can_purge());
}

/// A category has one colour, the same in the stacked bar, its row's bar
/// and its swatch; the six colours differ. A category that frees nothing
/// has no segment and a blank, disabled tick.
#[test]
fn each_category_keeps_one_colour() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    spread(&mut window, [5 << 20, 3 << 20, 2 << 20, 1 << 20, 0, 4]);
    let theme = theme();
    let colours: Vec<Color> = (0..6).map(|i| colour_of(&theme, i)).collect();
    for (i, a) in colours.iter().enumerate() {
        for b in &colours[i + 1..] {
            assert!(!rgb(*a, *b), "two categories share a colour");
        }
    }
    let frame = Screen::new(WIDE).frame(&window);
    let segs = segments(&frame, &window);
    assert_eq!(segs.len(), 5, "no segment for the category that frees nothing");
    for (index, title) in titles().into_iter().enumerate() {
        let row = row_of(&frame, &window, title);
        let swatch = on_line(&frame, &window, row, SWATCH);
        assert!(swatch.iter().any(|q| rgb(q.style.background, colours[index])), "{title}'s swatch");
        let frees = index != 4;
        assert_eq!(segs.iter().any(|q| rgb(q.style.background, colours[index])), frees, "{title}'s segment");
        assert_eq!(
            on_line(&frame, &window, row, ROW_BAR).iter().any(|q| rgb(q.style.background, colours[index])),
            frees,
            "{title}'s row bar"
        );
    }
    // The bar spans the results side, each segment as wide as its share
    // (the tiny one held at the minimum).
    let total: f32 = segs.iter().map(|q| q.bounds.width).sum();
    let widest = segs.iter().map(|q| q.bounds.width).fold(0.0, f32::max);
    assert!(total > 500.0, "full width: {total}");
    assert!(segs.iter().all(|q| q.bounds.width >= bars::MIN_SEGMENT - 0.01));
    let exports = segs.iter().find(|q| rgb(q.style.background, colours[0])).unwrap();
    assert_eq!(exports.bounds.width, widest);
    // The unit comes from the largest category: MB.
    assert!(find(&frame, "Frees (MB)").is_some());
    assert!(find(&frame, "5.00").is_some());
}

/// A rule changed after a scan: a warning notice says the scan is stale,
/// with a Scan Again link, and Purge is disabled with the reason beside it.
/// Scanning again clears it.
#[test]
fn a_rule_change_marks_the_scan_stale() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    let theme = theme();
    let _ = window.update(Message::Field("deleted_days", "5000".into()));
    assert!(window.stale && window.busy.is_none(), "stale, and nothing scans on its own");
    let frame = Screen::new(WIDE).frame(&window);
    let title = find_start(&frame, STALE_TITLE).expect("the stale notice");
    assert!(
        frame.quads.iter().any(|q| rgb(q.style.border.color, theme.warning.line) && inside(q.bounds, title.bounds)),
        "a warning notice"
    );
    assert!(frame.texts.iter().filter(|t| t.content == "Scan Again").count() >= 2, "its link, and the button");
    assert!(dangers(&frame, &theme, "Purge").is_empty(), "Purge disabled");
    assert_eq!(find(&frame, PURGE).map(|t| t.color), Some(theme.text_disabled));
    assert!(no_reason(&frame), "the notice says why; nothing beside Purge repeats it");
    for size in [WIDE, NARROW] {
        scan_beside_purge(&Screen::new(size).frame(&window), &window, "Scan Again", size);
    }
    scan(&mut window);
    assert!(!window.stale);
    let frame = Screen::new(WIDE).frame(&window);
    assert!(find_start(&frame, STALE_TITLE).is_none());
}

/// A fine progress report, as `th_cleanup` sends one.
fn report(category: Option<&'static str>, doing: &'static str, done: usize, total: Option<usize>, bytes: u64) -> Progress {
    Progress { category, doing, done, total, bytes, total_bytes: None, found: 0, found_bytes: 0 }
}

/// A category's report of what it has found to purge so far.
fn found(category: &'static str, items: usize, bytes: u64) -> Progress {
    Progress { found: items, found_bytes: bytes, ..report(Some(category), "Scanning", 0, None, 0) }
}

/// Hand `step` to the window as the scan's thread would, and poll.
fn feed(window: &mut Window, step: &Progress) {
    window.live.lock().unwrap().report(step);
    let _ = window.update(Message::Poll);
}

/// Category `at` finished with `plan`, as the scan's thread says it; polled.
fn finish(window: &mut Window, at: usize, plan: &CategoryPlan) {
    window.live.lock().unwrap().finished(at, plan);
    let _ = window.update(Message::Poll);
}

/// The texts of the results side (right of the split).
fn right_texts(frame: &Frame, window: &Window) -> Vec<String> {
    frame.texts.iter().filter(|t| t.bounds.x > window.left).map(|t| t.content.clone()).collect()
}

/// A window on a project with a scan running and on screen.
fn scanning(project: &Path) -> Window {
    let mut window = Window::new(project.display().to_string());
    let _ = window.update(Message::Scan);
    window.scan_started = Instant::now().checked_sub(SHOW_DELAY);
    assert!(window.showing_scan());
    window
}

/// A bar in the scan block: the track `height` tall nearest under `label`
/// (beside it to the left at most a swatch's way), and how far its fill in
/// `colour` reaches across it, `0..=1` (the target layer's reach where the
/// solid fill is still easing after it).
struct Bar {
    track: Rectangle,
    share: f32,
}

fn bar_under(frame: &Frame, theme: &Theme, label: &Text, height: f32, colour: Color) -> Bar {
    let track = frame
        .quads
        .iter()
        .filter(|q| (q.bounds.height - height).abs() < 0.5 && q.style.background == theme.bg_field)
        .filter(|q| q.bounds.y > label.bounds.y && q.bounds.x <= label.bounds.x + 1.0 && label.bounds.x - q.bounds.x < 40.0)
        .min_by(|a, b| a.bounds.y.total_cmp(&b.bounds.y))
        .unwrap_or_else(|| panic!("{:?}'s bar", label.content))
        .bounds;
    let reach = frame
        .quads
        .iter()
        .filter(|q| (q.bounds.y - track.y).abs() < 0.5 && (q.bounds.x - track.x).abs() < 0.5 && (q.bounds.height - height).abs() < 0.5)
        .filter(|q| rgb(q.style.background, colour) && q.style.background != theme.bg_field)
        .map(|q| q.bounds.width)
        .fold(0.0, f32::max);
    Bar { track, share: reach / track.width }
}

/// Whether a check icon is drawn on `label`'s line, right of it.
fn checked(frame: &Frame, label: &Text) -> bool {
    frame.meshes.iter().any(|m| m.x > label.bounds.x && (m.center().y - label.bounds.center().y).abs() < 6.0)
}

/// The scan's two phase bars on screen: the tracks a phase bar tall.
fn phase_tracks(frame: &Frame, window: &Window, theme: &Theme) -> usize {
    frame
        .quads
        .iter()
        .filter(|q| q.bounds.x > window.left && (q.bounds.height - PHASE_BAR).abs() < 0.5 && q.style.background == theme.bg_field)
        .count()
}

/// The label `content` right of the rules.
fn label<'a>(frame: &'a Frame, window: &Window, content: &str) -> &'a Text {
    frame.texts.iter().find(|t| t.content == content && t.bounds.x > window.left).unwrap_or_else(|| panic!("{content:?} drawn"))
}

/// A real scan, its reports replayed one by one: nothing for its first
/// 300 ms; then only the scan block: exactly two bars, Indexing and Sizing,
/// and no table. Indexing runs (live, Sizing still and empty), then is
/// full with a check while Sizing runs; neither goes back nor is full
/// before its phase is done; what each category has found only grows. On
/// completion the results replace the block.
#[test]
fn a_scan_indexes_then_sizes_and_then_shows_its_results() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = Window::new(project.display().to_string());
    let _ = window.update(Message::Scan);
    let job = window.busy.clone().expect("scanning");
    let screen = Screen::new(WIDE);
    let theme = theme();

    // A quick scan flashes nothing. Its start is put a minute ahead so a
    // slow, loaded test machine never reaches the delay before the frame.
    window.scan_started = Instant::now().checked_add(Duration::from_secs(60));
    let frame = screen.frame(&window);
    assert!(find(&frame, INDEXING).is_none() && find(&frame, SIZING).is_none(), "nothing under 300 ms");
    window.scan_started = Instant::now().checked_sub(SHOW_DELAY);

    enum Step {
        Report(Progress),
        Done(usize, Box<CategoryPlan>),
    }
    let steps = Mutex::new(Vec::new());
    let plan = job
        .run(&NoFarm, &|p| steps.lock().unwrap().push(Step::Report(p.clone())), &|at, c| {
            steps.lock().unwrap().push(Step::Done(at, Box::new(c.clone())))
        })
        .expect("the scan");
    let steps = steps.into_inner().unwrap();

    let frame = screen.frame(&window);
    assert_eq!(phase_tracks(&frame, &window, &theme), 2, "exactly two bars");
    assert_eq!(window.phase_states(), [(true, false), (false, false)], "indexing runs, sizing waits");
    assert!(find(&frame, CANCEL_SCAN).is_some(), "Scan is Cancel while scanning");

    let (mut last, mut last_found) = (window.phases, window.found);
    for step in steps {
        match &step {
            Step::Report(p) => feed(&mut window, p),
            Step::Done(at, category) => finish(&mut window, *at, category),
        }
        let now = window.phases;
        assert!(now.0 >= last.0 && now.1 >= last.1, "a bar went back: {last:?} to {now:?}");
        let [(indexing, indexed), (sizing, sized)] = window.phase_states();
        assert_eq!((indexing, sizing), (!indexed, indexed && !sized), "the running phase alone is live");
        if !indexed {
            assert!(now.0 < 1.0 && now.1 == 0.0, "indexing below full, sizing still at 0: {now:?}");
        }
        if !sized {
            assert!(now.1 < 1.0, "sizing never full before it is done: {now:?}");
        }
        for (at, (items, bytes)) in window.found.iter().enumerate() {
            assert!(*items >= last_found[at].0 && *bytes >= last_found[at].1, "{at}'s finds went back");
        }
        (last, last_found) = (now, window.found);
        assert!(!window.can_purge() && window.purge_reason() == Some("Scanning\u{2026}"));

        let frame = screen.frame(&window);
        assert_eq!(phase_tracks(&frame, &window, &theme), 2, "exactly two bars throughout");
        assert!(find(&frame, "Purge \u{00b7} Category").is_none(), "no table while scanning");
        assert!(no_reason(&frame));
        assert!(dangers(&frame, &theme, "Purge").is_empty());
        let (index_label, size_label) = (label(&frame, &window, INDEXING), label(&frame, &window, SIZING));
        let (index_bar, size_bar) = (
            bar_under(&frame, &theme, index_label, PHASE_BAR, theme.accent),
            bar_under(&frame, &theme, size_label, PHASE_BAR, theme.accent),
        );
        assert!(index_label.bounds.y < size_label.bounds.y, "Indexing above Sizing");
        assert_eq!(checked(&frame, index_label), indexed, "Indexing's check");
        assert_eq!(checked(&frame, size_label), sized, "Sizing's check");
        if indexed {
            assert!(index_bar.share > 0.999, "indexing done: full ({})", index_bar.share);
        } else {
            assert!(size_bar.share == 0.0, "sizing empty until indexing ends");
        }
    }
    let frame = screen.frame(&window);
    assert!(checked(&frame, label(&frame, &window, SIZING)), "every category done: sizing full with a check");

    let _ = window.update(Message::Scanned(job.generation, Ok(Arc::new(plan))));
    assert!(window.busy.is_none() && window.can_purge(), "Purge once the scan is complete");
    let frame = screen.frame(&window);
    assert!(find(&frame, INDEXING).is_none() && find(&frame, SIZING).is_none(), "the block is gone");
    assert!(find(&frame, "Purge \u{00b7} Category").is_some(), "the results table");
    assert!(find_start(&frame, "4 B can be freed").is_some(), "and its headline");
}

/// The scan block sits alone, centred both ways in the results pane, at
/// both sizes; its bars are a share of the pane wide within 240..420 px.
#[test]
fn the_scan_block_is_centred_in_the_results_pane() {
    let home = tempfile::tempdir().unwrap();
    let mut window = scanning(&project(home.path()));
    feed(&mut window, &report(None, progress::LISTING, 18_402, None, 0));
    for size in [WIDE, NARROW] {
        let screen = Screen::new(size);
        let frame = screen.frame(&window);
        let theme = &screen.theme;
        let top = label(&frame, &window, INDEXING).bounds.y;
        let elapsed = frame.texts.iter().find(|t| t.content.ends_with(" elapsed")).expect("the time it has run");
        let bottom = elapsed.bounds.y + elapsed.bounds.height;
        let pane_top = theme.spacing.lg;
        let pane_bottom = button_of(&frame, &window, CANCEL_SCAN).y - theme.spacing.sm;
        let (centre, want) = ((top + bottom) / 2.0, (pane_top + pane_bottom) / 2.0);
        assert!((centre - want).abs() < 6.0, "centred down at {size:?}: {centre} vs {want}");
        let bar = bar_under(&frame, theme, label(&frame, &window, INDEXING), PHASE_BAR, theme.accent).track;
        let side = (window.left + size.width) / 2.0;
        assert!((bar.center().x - side).abs() < 12.0, "centred across at {size:?}: {} vs {side}", bar.center().x);
        assert!((236.0..=420.0).contains(&bar.width), "{} wide at {size:?}", bar.width);
        // Alone: no headline, no table, no stacked bar.
        assert!(find_start(&frame, "Scanning\u{2026}").is_none() && find_start(&frame, "Purge \u{00b7}").is_none());
        assert!(segments(&frame, &window).is_empty());
    }
}

/// Under the phase bars the six categories, in their usual order, each a
/// swatch, its name and a bar in its colour (the colour the results then
/// draw it in) as long as what it has found against the largest so far;
/// a category not started is a muted name over an empty track; a finished
/// one has a check; the counts say what each has found.
#[test]
fn categories_fill_up_as_they_find_what_goes() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let real = scanned(&project).plan.clone().unwrap();
    let mut window = scanning(&project);
    let screen = Screen::new(WIDE);
    let theme = theme();
    for step in [
        report(None, progress::LISTING, 18_402, None, 0),
        report(None, progress::SORTING, 18_402, Some(18_402), 0),
        report(None, progress::WORKFILES, 72, Some(72), 0),
    ] {
        feed(&mut window, &step);
    }
    let keys = CATEGORY_KEYS;
    let gb = 1u64 << 30;
    let rounds: [Vec<Progress>; 3] = [
        vec![found(keys[0], 3, gb / 2), found(keys[1], 10, gb)],
        vec![found(keys[0], 5, 2 * gb), found(keys[1], 12, gb + gb / 4), report(Some(keys[3]), "Scanning", 0, None, 0)],
        vec![found(keys[5], 1, 4)],
    ];
    let mut last = [0u64; 6];
    for (round, steps) in rounds.iter().enumerate() {
        for step in steps {
            feed(&mut window, step);
        }
        if round == 2 {
            // The deleted entities' scan ends with what it found.
            let at = keys.iter().position(|k| *k == "deleted").unwrap();
            finish(&mut window, at, &real.categories[at]);
        }
        let frame = screen.frame(&window);
        let largest = window.found.iter().map(|f| f.1).max().unwrap();
        let mut y = 0.0;
        for (at, title) in titles().into_iter().enumerate() {
            let name = label(&frame, &window, title);
            assert!(name.bounds.y > y, "{title} in its place, round {round}");
            y = name.bounds.y;
            let colour = colour_of(&theme, at);
            assert!(
                frame.quads.iter().any(|q| (q.bounds.height - SWATCH).abs() < 0.5
                    && rgb(q.style.background, colour)
                    && (q.bounds.center().y - name.bounds.center().y).abs() < 6.0),
                "{title}'s swatch"
            );
            let (items, bytes) = window.found[at];
            assert!(bytes >= last[at], "{title}'s finds only grow");
            last[at] = bytes;
            let bar = bar_under(&frame, &theme, name, ROW_BAR, colour);
            let want = bytes as f32 / largest as f32;
            assert!((bar.share - want).abs() < 0.02, "{title}'s bar {} vs {want}, round {round}", bar.share);
            let started = window.snapshot.started(at) || window.partial[at].is_some();
            assert_eq!(name.color == theme.text_muted, !started, "{title} muted until it starts");
            if started {
                let words = format!("{} \u{00b7} {}", overview::plural(items, "item"), human(bytes));
                assert!(find(&frame, &words).is_some(), "{title}'s count {words:?}");
            }
            assert_eq!(checked(&frame, name), window.partial[at].is_some(), "{title}'s check, round {round}");
        }
        assert_eq!(
            frame.texts.iter().filter(|t| t.content == WAITING).count(),
            6 - (0..6).filter(|at| window.snapshot.started(*at)).count()
        );
    }
    // The exports grew past the renders (the largest at first), so the
    // renders' bar rescaled to the exports'.
    assert_eq!((window.found[0], window.found[1]), ((5, 2 * gb), (12, gb + gb / 4)));

    // The colours are those the results draw.
    let colours: Vec<Color> = (0..6).map(|at| colour_of(&theme, at)).collect();
    let mut results = scanned(&project);
    spread(&mut results, [1, 1, 1, 1, 1, 1]);
    let frame = Screen::new(WIDE).frame(&results);
    for (at, title) in titles().into_iter().enumerate() {
        let row = row_of(&frame, &results, title);
        assert!(on_line(&frame, &results, row, ROW_BAR).iter().any(|q| rgb(q.style.background, colours[at])), "{title}'s colour");
    }
}

/// The time a scan has run ticks on with no report at all: the screen
/// changes while a slow read on the share says nothing.
#[test]
fn elapsed_time_ticks_without_reports() {
    let home = tempfile::tempdir().unwrap();
    let mut window = Window::new(project(home.path()).display().to_string());
    let _ = window.update(Message::Scan);
    feed(&mut window, &report(None, progress::LISTING, 18_402, None, 0));
    let screen = Screen::new(WIDE);
    let started = Instant::now();
    window.scan_started = started.checked_sub(Duration::from_secs(61));
    let _ = window.update(Message::Poll);
    let before = right_texts(&screen.frame(&window), &window);
    window.scan_started = started.checked_sub(Duration::from_secs(63));
    let _ = window.update(Message::Poll);
    let after = right_texts(&screen.frame(&window), &window);
    assert_ne!(before, after, "two seconds on, the screen differs");
    assert!(before.contains(&"1:01 elapsed".to_string()), "{before:?}");
    assert!(after.contains(&"1:03 elapsed".to_string()), "{after:?}");
    assert!(before.contains(&"18,402 files".to_string()), "indexing's count: {before:?}");
}

/// Cancel stops the scan where Scan was: the window is as before it, with
/// no plan and Purge off, and Scan back.
#[test]
fn cancel_stops_a_scan_and_keeps_nothing() {
    let home = tempfile::tempdir().unwrap();
    let mut window = scanning(&project(home.path()));
    feed(&mut window, &report(None, progress::LISTING, 10, None, 0));
    let screen = Screen::new(WIDE);
    let frame = screen.frame(&window);
    scan_beside_purge(&frame, &window, CANCEL_SCAN, WIDE);
    let job = window.busy.clone().unwrap();
    let _ = window.update(Message::CancelScan);
    assert!(job.cancel.load(Ordering::Relaxed));
    let frame = screen.frame(&window);
    assert!(window.plan.is_none() && !window.can_purge());
    assert!(find(&frame, INDEXING).is_none() && find(&frame, SIZING).is_none());
    assert!(find(&frame, "Scan the project").is_some(), "the empty state from before");
    scan_beside_purge(&frame, &window, "Scan", WIDE);
    assert!(dangers(&frame, &theme(), "Purge").is_empty());
}

/// A temp project with the demo stamp and `extra` more old `_deleted`
/// stamps, each a little bigger: the names in the order a purge takes them.
fn stamps_project(home: &Path, extra: usize) -> (PathBuf, PathBuf, Vec<String>) {
    let project = project(home);
    let holder = project.join("shots").join("_deleted");
    let mut names = vec!["20200101-120000".to_string()];
    for at in 1..=extra {
        let name = format!("20200101-1200{at:02}");
        std::fs::create_dir_all(holder.join(&name)).unwrap();
        std::fs::write(holder.join(&name).join("f"), vec![b'x'; 100 * at]).unwrap();
        names.push(name);
    }
    (project, holder, names)
}

/// Scanned, Purge pressed and confirmed: the purge the window then runs.
fn confirmed(project: &Path) -> (Window, PurgeJob) {
    let mut window = scanned(project);
    let _ = window.update(Message::Purge);
    let _ = window.update(Message::Confirm);
    let job = window.purge.clone().expect("purging");
    (window, job)
}

/// What the log names as gone: each item's last path part, in order.
fn logged(log: &Path) -> Vec<String> {
    std::fs::read_to_string(log)
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.starts_with('#') && !l.trim().is_empty())
        .map(|l| l.split('\t').next().unwrap_or("").rsplit('/').next().unwrap_or("").to_string())
        .collect()
}

/// The words of `label`'s button, if drawn, and whether it is enabled.
fn button_state(frame: &Frame, theme: &Theme, label: &str) -> Option<bool> {
    find(frame, label).map(|t| t.color != theme.text_disabled)
}

/// While a purge runs the results side is only its block, centred: one
/// Purging bar with its counts, the chosen categories only (each filling
/// in its colour over its own total), the time run; the bottom row is
/// Pause and Stop, no Scan, no Purge. Paused, it says so, holds its
/// clock and removes nothing however long; resumed it goes on, its counts
/// only rising, to every chosen item. Then the success notice and the
/// scan that follows.
#[test]
fn a_purge_shows_its_progress_and_pauses_and_resumes() {
    let home = tempfile::tempdir().unwrap();
    let (project, holder, names) = stamps_project(home.path(), 6);
    let (mut window, job) = confirmed(&project);
    let theme = theme();
    let screen = Screen::new(WIDE);
    let bytes: u64 = job.chosen.iter().map(|c| c.bytes).sum();
    assert_eq!(job.chosen.len(), 1, "only the deleted entities have anything to purge");

    let frame = screen.frame(&window);
    assert_eq!(phase_tracks(&frame, &window, &theme), 1, "one bar");
    let label_text = label(&frame, &window, PURGING);
    assert!(find(&frame, &format!("0 of 7 items \u{00b7} 0 B of {}", human(bytes))).is_some(), "its counts");
    let right = right_texts(&frame, &window);
    assert!(titles().iter().all(|t| right.contains(&t.to_string()) == (*t == "Deleted entities")), "only the chosen: {right:?}");
    assert!(find_start(&frame, "0 of 7 items \u{00b7} 0 B freed").is_some(), "the category's count");
    assert_eq!(button_state(&frame, &theme, PAUSE), Some(true));
    assert_eq!(button_state(&frame, &theme, STOP), Some(true));
    assert!(find(&frame, PURGE).is_none() && find(&frame, "Scan Again").is_none(), "no Scan, no Purge");
    assert!(find_start(&frame, "Purge \u{00b7}").is_none() && segments(&frame, &window).is_empty(), "nothing else");
    let elapsed = frame.texts.iter().find(|t| t.content.ends_with(" elapsed")).expect("the time run");
    let pane_bottom = button_of(&frame, &window, STOP).y - theme.spacing.sm;
    let centre = (label_text.bounds.y + elapsed.bounds.y + elapsed.bounds.height) / 2.0;
    assert!((centre - (theme.spacing.lg + pane_bottom) / 2.0).abs() < 6.0, "centred");
    assert_eq!(window.purge_states().0, (true, false, false), "live while running");

    let _ = window.update(Message::PausePurge);
    let frame = screen.frame(&window);
    assert!(find(&frame, PAUSED).is_some(), "it says Paused");
    assert_eq!(button_state(&frame, &theme, RESUME), Some(true));
    assert!(find(&frame, PAUSE).is_none());
    assert_eq!(window.purge_states().0, (false, true, false), "still: no sweep");
    let held = window.purge.as_ref().unwrap().elapsed();

    std::thread::scope(|s| {
        let running = job.clone();
        let purge = s.spawn(move || running.run(&|_| {}, Duration::ZERO));
        for _ in 0..4 {
            std::thread::sleep(Duration::from_millis(50));
            let _ = window.update(Message::Poll);
            assert!(names.iter().all(|n| holder.join(n).exists()), "nothing goes while paused");
            assert_eq!(window.purge_counts, vec![(0, 0, 0)]);
        }
        assert_eq!(window.purge.as_ref().unwrap().elapsed(), held, "the clock holds while paused");
        let _ = window.update(Message::ResumePurge);
        let mut last = (0, 0, 0);
        while !purge.is_finished() {
            let _ = window.update(Message::Poll);
            let now = window.purge_counts[0];
            assert!(now.0 >= last.0 && now.1 >= last.1 && now.2 >= last.2, "{last:?} to {now:?}");
            last = now;
        }
        let results = purge.join().unwrap();
        let _ = window.update(Message::Poll);
        assert_eq!(window.purge_counts, vec![(7, 7, bytes)], "every chosen item");
        assert!(window.purge.as_ref().unwrap().elapsed() >= held, "the clock went on");
        let frame = screen.frame(&window);
        assert!(find(&frame, &format!("7 of 7 items \u{00b7} {} of {}", human(bytes), human(bytes))).is_some());
        assert!(checked(&frame, label(&frame, &window, "Deleted entities")), "the category's check");
        let colour = colour_of(&theme, CATEGORY_KEYS.iter().position(|k| *k == "deleted").unwrap());
        let bar = bar_under(&frame, &theme, label(&frame, &window, "Deleted entities"), ROW_BAR, colour);
        assert!(bar.share > 0.999, "filled in its colour: {}", bar.share);
        let _ = window.update(Message::Purged(job.plan.clone(), Arc::new(results), job.log.clone()));
    });
    assert!(names.iter().all(|n| !holder.join(n).exists()));
    assert_eq!(logged(&job.log), names, "the log names everything that went");
    assert!(window.purge.is_none() && window.busy.is_some(), "the scan that follows");
    let done = window.purged.clone().expect("the notice");
    assert_eq!((done.tone, done.text.as_str()), (NoticeTone::Success, format!("Purged 7 items, freed {}.", human(bytes)).as_str()));
}

/// Stop ends the purge once the item under way is done: exactly the first
/// k items went and are logged, the rest are on disk. While it stops it
/// says so and its buttons are off. The notice says how many of how many
/// went, its log behind Show details, and the scan follows.
#[test]
fn a_stopped_purge_keeps_what_went_and_says_so() {
    let home = tempfile::tempdir().unwrap();
    let (project, holder, names) = stamps_project(home.path(), 6);
    let (mut window, job) = confirmed(&project);
    let theme = theme();
    let screen = Screen::new(WIDE);
    let k = 3;
    let (reached, go) = (std::sync::Barrier::new(2), std::sync::Barrier::new(2));
    // Once: the category's last word repeats the count it stopped at.
    let once = AtomicBool::new(false);
    std::thread::scope(|s| {
        let running = job.clone();
        let hook = |r: &core::Removal<'_>| {
            if r.handled == k && !once.swap(true, Ordering::SeqCst) {
                reached.wait();
                go.wait();
            }
        };
        let purge = s.spawn(move || running.run(&hook, Duration::ZERO));
        reached.wait();
        let _ = window.update(Message::StopPurge);
        let frame = screen.frame(&window);
        assert!(find(&frame, STOPPING).is_some(), "it says it is stopping");
        assert_eq!((button_state(&frame, &theme, PAUSE), button_state(&frame, &theme, STOP)), (Some(false), Some(false)));
        go.wait();
        let results = purge.join().unwrap();
        let _ = window.update(Message::Poll);
        let _ = window.update(Message::Purged(job.plan.clone(), Arc::new(results), job.log.clone()));
    });
    for (at, name) in names.iter().enumerate() {
        assert_eq!(holder.join(name).exists(), at >= k, "{name}");
    }
    assert_eq!(logged(&job.log), names[..k].to_vec(), "the log names exactly what went");
    let freed: u64 = 4 + 100 + 200;
    let done = window.purged.clone().expect("the notice");
    assert_eq!(done.tone, NoticeTone::Success);
    assert_eq!(done.text, format!("Stopped after purging 3 of 7 items, freed {}.", human(freed)));
    assert!(done.log.contains(&job.log.display().to_string()));
    assert!(window.purge.is_none() && window.busy.is_some(), "the scan that follows");
    run_scan(&mut window);
    let frame = screen.frame(&window);
    assert!(find_start(&frame, "Stopped after purging 3 of 7 items").is_some(), "kept across the scan");
    scan_beside_purge(&frame, &window, "Scan Again", WIDE);

    // Stopped before its first item: nothing went, and it says so.
    let (mut window, job) = confirmed(&project);
    let _ = window.update(Message::StopPurge);
    let results = job.run(&|_| {}, Duration::ZERO);
    let _ = window.update(Message::Purged(job.plan.clone(), Arc::new(results), job.log.clone()));
    let done = window.purged.clone().expect("the notice");
    assert_eq!((done.tone, done.text.as_str()), (NoticeTone::Info, STOPPED_EARLY));
    assert!(names[k..].iter().all(|n| holder.join(n).exists()));
}

/// Closing the window mid-purge stops it after the item under way, keeps
/// the window until then, and then quits rather than scanning; a window
/// dropped mid-purge waits for the item under way.
#[test]
fn closing_mid_purge_stops_after_the_item_under_way() {
    let home = tempfile::tempdir().unwrap();
    let (project, holder, names) = stamps_project(home.path(), 4);
    let (mut window, job) = confirmed(&project);
    assert!(window.on_close_requested(window::Id::MAIN).is_some(), "the window stays while purging");
    let (reached, go) = (std::sync::Barrier::new(2), std::sync::Barrier::new(2));
    // Once: the category's last word repeats the count it stopped at.
    let once = AtomicBool::new(false);
    std::thread::scope(|s| {
        let running = job.clone();
        let hook = |r: &core::Removal<'_>| {
            if r.handled == 1 && !once.swap(true, Ordering::SeqCst) {
                reached.wait();
                go.wait();
            }
        };
        let purge = s.spawn(move || running.run(&hook, Duration::ZERO));
        reached.wait();
        let _ = window.update(Message::CloseRequested);
        assert!(window.purge.as_ref().unwrap().closing);
        go.wait();
        let results = purge.join().unwrap();
        let _ = window.update(Message::Purged(job.plan.clone(), Arc::new(results), job.log.clone()));
    });
    assert_eq!(names.iter().filter(|n| !holder.join(n).exists()).count(), 1, "the one under way went");
    assert_eq!(logged(&job.log), names[..1].to_vec(), "and is logged");
    assert!(window.purge.is_none() && window.busy.is_none(), "no scan: the window goes");
    assert!(window.on_close_requested(window::Id::MAIN).is_none(), "now closing quits");

    // Dropped while its purge is held: the drop stops it and waits.
    let (window, job) = confirmed(&project);
    job.control.store(core::PAUSE, Ordering::SeqCst);
    std::thread::scope(|s| {
        let running = job.clone();
        let purge = s.spawn(move || running.run(&|_| {}, Duration::ZERO));
        while *job.done.0.lock().unwrap() != RUNNING {
            std::thread::yield_now();
        }
        drop(window);
        assert_eq!(*job.done.0.lock().unwrap(), DONE, "the drop waited for the purge");
        purge.join().unwrap();
    });
    assert!(names[1..].iter().all(|n| holder.join(n).exists()), "nothing more went");
}

/// The Confirm is short: the size in its title, one sentence with the count
/// and what a workfile can make again, no per-category list (the main screen shows that),
/// Cancel before Purge, and neither Enter nor Esc purges: both cancel.
#[test]
fn the_confirm_is_short_and_never_purges_on_a_key() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    let _ = window.update(Message::Purge);
    assert!(window.confirm.is_some());
    let mut screen = Screen::new(WIDE);
    let sheet = screen.sheet(&window);
    let frame = screen.draw(&sheet);
    assert!(find(&frame, "Free up 4 B?").is_some(), "the title names the size");
    assert!(
        find(&frame, "1 old item will be removed from disk.").is_some(),
        "one sentence: the count, and (a deleted entity) nothing more"
    );
    assert!(find(&frame, results::TO_PURGE.0).is_none(), "no per-category table");
    let cancel = find(&frame, "Cancel").expect("Cancel");
    let purge = find(&frame, "Purge").expect("the verb");
    assert!(cancel.bounds.x < purge.bounds.x, "Cancel, then Purge");
    assert!((cancel.bounds.y - purge.bounds.y).abs() < 1.0, "on one row");

    for named in [Named::Enter, Named::Escape] {
        let mut sheet = screen.sheet(&window);
        let messages = screen.send(&mut sheet, &key(named), Point::ORIGIN);
        assert!(messages.iter().all(|m| matches!(m, Message::Cancel)), "{named:?} only cancels");
        assert!(!messages.is_empty(), "{named:?} closes the Confirm");
        for message in messages {
            let _ = window.update(message);
        }
        assert!(window.confirm.is_none() && !window.purging());
        assert!(project.join("shots").join("_deleted").join("20200101-120000").exists(), "{named:?} purged");
        let _ = window.update(Message::Purge);
    }
    let _ = window.update(Message::Cancel);

    // Three categories to purge, one unticked: the Confirm counts two and
    // names no category.
    spread(&mut window, [0, 2048, 0, 1024, 0, 4]);
    let _ = window.update(Message::Tick("backups".into(), false));
    let _ = window.update(Message::Purge);
    let frame = screen.draw(&screen.sheet(&window));
    assert!(titles().iter().all(|t| find(&frame, t).is_none()), "no category is listed");
    assert!(find(&frame, &format!("Free up {}?", human(2052))).is_some(), "the title counts only the ticked");
    assert!(find_start(&frame, "2 old items ").is_some(), "and so does the sentence");
    let only: Vec<String> = window.purge_plan().unwrap().categories.iter().map(|c| c.key.clone()).collect();
    assert!(!only.contains(&"backups".to_string()), "and Purge leaves it alone: {only:?}");
}

/// A scan's warning is a warning notice led by its first sentence; a purge
/// that failed is a danger notice that outlives the scan after it until
/// dismissed.
#[test]
fn warnings_and_failures_are_notices() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    let theme = theme();
    let mut plan = (*window.plan.clone().unwrap()).clone();
    plan.categories[0].notes.push("Deadline could not be asked. No job folder is purged.".into());
    deliver(&mut window, plan);
    let view = window.view.clone().unwrap();
    let warnings = overview::warnings(&view);
    let (category, first) = warnings.first().expect("the warning");
    assert_eq!(results::split_sentence(first).1, "No job folder is purged.");
    let (title, _) = results::split_sentence(first);
    let frame = Screen::new(WIDE).frame(&window);
    let words = find_start(&frame, &format!("{category}: {title}")).expect("the warning notice, its title first");
    assert!(
        frame.quads.iter().any(|q| rgb(q.style.border.color, theme.warning.line) && inside(q.bounds, words.bounds)),
        "in a warning notice"
    );
    assert!(rgb(words.color, theme.text), "the words stay in text");

    // A purge that failed for some.
    let plan = window.plan.clone().unwrap();
    let log = home.path().join("cleanup.log");
    let results = vec![("deleted".to_string(), (0usize, 0u64, vec!["shots/_deleted/x: access denied".to_string()]))];
    let _ = window.update(Message::Purged(plan, Arc::new(results), log));
    run_scan(&mut window);
    assert!(window.purged.is_none(), "a partial result is not a success");
    for round in 0..2 {
        let frame = Screen::new(WIDE).frame(&window);
        let words = find_start(&frame, "1 item could not be purged.").unwrap_or_else(|| panic!("the danger notice, round {round}"));
        assert!(frame.quads.iter().any(|q| rgb(q.style.border.color, theme.danger.line) && inside(q.bounds, words.bounds)));
        // It stays across another scan.
        scan(&mut window);
    }
    let _ = window.update(Message::ShowFailure(true));
    let frame = Screen::new(WIDE).frame(&window);
    assert!(frame.texts.iter().any(|t| t.content.contains("access denied")), "Show details lists what failed");
    let _ = window.update(Message::DismissFailure);
    let frame = Screen::new(WIDE).frame(&window);
    assert!(find_start(&frame, "1 item could not be purged.").is_none());
}

/// A purge that went is a success notice at the top of the results, kept
/// across the scan that follows it, its log behind "Show details", until
/// dismissed or the next Scan.
#[test]
fn a_purge_that_went_is_a_success_notice() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    let theme = theme();
    let _ = window.update(Message::Purge);
    let log = window.confirm.clone().unwrap();
    let _ = window.update(Message::Confirm);
    let plan = window.purge_plan().unwrap();
    let results = core::apply(&plan, &log);
    let _ = window.update(Message::Purged(plan, Arc::new(results), log.clone()));
    run_scan(&mut window);
    let frame = Screen::new(WIDE).frame(&window);
    let words = find_start(&frame, "Purged 1 item, freed 4 B.").expect("the success notice");
    assert!(
        frame.quads.iter().any(|q| rgb(q.style.border.color, theme.success.line) && inside(q.bounds, words.bounds)),
        "in a success notice"
    );
    let nothing = find(&frame, "Nothing to clean up").expect("what is left");
    assert!(words.bounds.y < nothing.bounds.y, "at the top of the results");
    assert!(!frame.texts.iter().any(|t| t.content.contains(&log.display().to_string())), "the log behind Show details");
    let _ = window.update(Message::ShowPurged(true));
    let frame = Screen::new(WIDE).frame(&window);
    assert!(frame.texts.iter().any(|t| t.content.contains(&log.display().to_string())), "Show details names the log");
    let _ = window.update(Message::DismissPurged);
    assert!(find_start(&Screen::new(WIDE).frame(&window), "Purged 1 item").is_none(), "dismissed");
}

/// A project with nothing to purge shows the empty state, which says why
/// Purge is disabled; no reason is repeated beside it.
#[test]
fn an_empty_project_shows_the_empty_state() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    std::fs::remove_dir_all(project.join("shots")).unwrap();
    let window = scanned(&project);
    let theme = theme();
    let frame = Screen::new(WIDE).frame(&window);
    assert!(find(&frame, "Nothing to clean up").is_some(), "the empty state says why");
    assert!(no_reason(&frame));
    let purge = find(&frame, PURGE).expect("Purge, disabled");
    assert_eq!(purge.color, theme.text_disabled);
    assert!(dangers(&frame, &theme, "Purge").is_empty(), "no live Danger button");
    assert_eq!(primaries(&frame, &theme), 0);
}

/// Every empty state sits centred, both ways, in the results pane above
/// the button row (the whole side without a project), at both sizes.
#[test]
fn empty_states_are_centred_in_the_results_pane() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let before = Window::new(project.display().to_string());
    let nobody = Window::new(String::new());
    let empty = {
        let home = tempfile::tempdir().unwrap();
        let project = super::tests::project(home.path());
        std::fs::remove_dir_all(project.join("shots")).unwrap();
        let window = scanned(&project);
        (window, home)
    };
    for (window, title, body) in
        [(&before, "Scan the project", SCAN_BODY), (&nobody, NO_PROJECT, NO_PROJECT_BODY), (&empty.0, "Nothing to clean up", "Of ")]
    {
        for size in [WIDE, NARROW] {
            let screen = Screen::new(size);
            let frame = screen.frame(window);
            let theme = &screen.theme;
            let heading = find(&frame, title).unwrap_or_else(|| panic!("{title} at {size:?}"));
            let words = find_start(&frame, body).unwrap_or_else(|| panic!("{title}'s body at {size:?}"));
            // The block: the icon (22 px, a gap above the title), down to
            // the body.
            let top = heading.bounds.y - 22.0 - theme.spacing.sm;
            let bottom = words.bounds.y + words.bounds.height;
            let pane_top = theme.spacing.lg;
            let pane_bottom =
                if window.has_project { button_of(&frame, window, PURGE).y - theme.spacing.sm } else { size.height - theme.spacing.md };
            let (centre, want) = ((top + bottom) / 2.0, (pane_top + pane_bottom) / 2.0);
            assert!(
                (centre - want).abs() < 6.0,
                "{title} centred at {size:?}: {centre} vs {want} ({top}..{bottom} in {pane_top}..{pane_bottom})"
            );
            let middle = heading.bounds.x + heading.bounds.width / 2.0;
            let side = (window.left + size.width) / 2.0;
            assert!((middle - side).abs() < 12.0, "{title} centred across at {size:?}: {middle} vs {side}");
        }
    }
}

/// The table's numbers sit right in their columns, under headers that
/// carry the unit, chosen from the largest value.
#[test]
fn the_table_reads_at_a_glance() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let window = scanned(&project);
    let frame = Screen::new(WIDE).frame(&window);
    for title in [results::FOUND.0, results::TO_PURGE.0] {
        assert!(find(&frame, title).is_some(), "the {title:?} header");
    }
    let header = find(&frame, "Frees (B)").expect("the size header in the largest value's unit");
    let row = row_of(&frame, &window, "Deleted entities");
    let size = frame
        .texts
        .iter()
        .find(|t| t.content == "4" && t.bounds.x > header.bounds.x - 20.0 && (t.bounds.center().y - row.bounds.center().y).abs() < 4.0)
        .expect("its size, in B");
    let right = |t: &Text| t.bounds.x + t.bounds.width;
    assert!((right(size) - right(header)).abs() < 12.0, "right-aligned under its header: {size:?} {header:?}");
}

/// At both sizes every line of the table is one line: a category's name
/// whole beside its tick and swatch, a long note cut with an ellipsis
/// rather than wrapped over the rows below it.
#[test]
fn every_table_line_is_one_line() {
    let home = tempfile::tempdir().unwrap();
    let project = project(home.path());
    let mut window = scanned(&project);
    spread(&mut window, [1, 1, 1, 1, 1, 1]);
    let mut plan = (*window.plan.clone().unwrap()).clone();
    let long = "Deadline could not be asked, so no job folder is purged until it answers again from this machine";
    plan.categories[4].notes.push(long.into());
    deliver(&mut window, plan);
    let _ = window.update(Message::Toggle("4".into()));
    for size in [WIDE, NARROW] {
        let screen = Screen::new(size);
        let frame = screen.frame(&window);
        let header = find(&frame, "Purge \u{00b7} Category").expect("the table's header");
        let table: Vec<&Text> = frame.texts.iter().filter(|t| t.bounds.x > window.left && t.bounds.y >= header.bounds.y).collect();
        for title in titles() {
            let drawn = table.iter().find(|t| t.content == title).unwrap_or_else(|| panic!("{title}'s row at {size:?}"));
            let words = screen.measure.measure_styled(&drawn.content, &drawn.spans, drawn.size, None);
            assert!(drawn.bounds.width + 0.5 >= words.width, "{title} squeezed at {size:?}: {:?}", drawn.bounds);
        }
        for drawn in &table {
            assert!(drawn.bounds.height < 1.5 * line_height(drawn.size), "{:?} wraps at {size:?}", drawn.content);
        }
        if let Some(note) = table.iter().find(|t| long.starts_with(t.content.trim_end_matches('\u{2026}')) && t.content.len() > 10)
            && size == NARROW
        {
            assert!(note.content.ends_with('\u{2026}'), "cut at {size:?}: {:?}", note.content);
        }
    }
}
