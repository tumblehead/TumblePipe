//! The menus the player draws in its windows, on Windows and Linux: built as
//! Primer's own [`MenuBar`] (its menus, Alt and a title's access letter,
//! F10) from the same rows as the native macOS bar, and drawn so no title or
//! row loses its last letter ([`widen_words`]).
//!
//! The editor puts its bar at the start of the toolbar's row
//! (`editor/menu_row`); a preview window (`frames_view`) stacks it above its
//! toolbar, where Primer would put its own.

use primer::core::alignment::Horizontal;
use primer::core::measure::TextMeasurer;
use primer::core::render::Primitive;
use primer::keyboard::{Key, Modifiers, Named};
use primer::prelude::*;
use primer::widget::custom::{
    Ctx, Event, Layout, LayoutCtx, Limits, Node, Overlay, RenderNode, Semantics, Shell, Sizing, Status, Widget, mouse,
};
use primer::widgets::{MenuBar, MenuHeading, MenuRow};

/// Whether the menus are drawn in the window: on every platform but macOS,
/// whose menu bar is the screen's.
pub(crate) fn in_window() -> bool {
    !cfg!(target_os = "macos")
}

/// `label` with its `&` taken out, and where (in characters) the letter the
/// first `&` marks is: the menu's access letter. `&&` is a literal `&`, and a
/// trailing `&` is dropped, as Primer reads a label for its own bar.
pub(crate) fn access_label(label: &str) -> (String, Option<usize>) {
    let mut text = String::with_capacity(label.len());
    let mut access = None;
    let mut chars = label.chars();
    while let Some(c) = chars.next() {
        let c = if c == '&' {
            match chars.next() {
                Some('&') => '&',
                Some(next) => {
                    access.get_or_insert(text.chars().count());
                    next
                }
                None => break,
            }
        } else {
            c
        };
        text.push(c);
    }
    (text, access)
}

/// How a shortcut is written at a menu row's right end: the modifiers, then
/// the key ("Ctrl+Shift+S"), as Primer writes a native item's accelerator.
/// `None` for a key with no name to show.
pub(crate) fn hint(key: &Key, modifiers: Modifiers) -> Option<String> {
    let key = match key {
        Key::Character(' ') => "Space".to_owned(),
        Key::Character(c) => c.to_uppercase().collect(),
        Key::Named(named) => match named {
            Named::Delete => "Del".to_owned(),
            Named::Escape => "Esc".to_owned(),
            Named::ArrowUp => "Up".to_owned(),
            Named::ArrowDown => "Down".to_owned(),
            Named::ArrowLeft => "Left".to_owned(),
            Named::ArrowRight => "Right".to_owned(),
            Named::PageUp => "PgUp".to_owned(),
            Named::PageDown => "PgDn".to_owned(),
            other => format!("{other:?}"),
        },
        Key::Unidentified => return None,
    };
    let logo = if cfg!(target_os = "macos") { "Cmd" } else { "Super" };
    let held = [(modifiers.control, "Ctrl"), (modifiers.alt, "Alt"), (modifiers.shift, "Shift"), (modifiers.logo, logo)];
    let mut parts: Vec<&str> = held.into_iter().filter(|(on, _)| *on).map(|(_, name)| name).collect();
    parts.push(&key);
    Some(parts.join("+"))
}

/// One row of a menu drawn in the window: its label (`&` marking its access
/// letter), what it sends, whether it can be chosen, and its shortcut.
pub(crate) struct Line<M> {
    pub label: String,
    pub message: M,
    pub enabled: bool,
    pub keys: Option<(Key, Modifiers)>,
}

/// The rows of a menu, `None` a separator: each item with its access letter
/// and shortcut, and a separator only between two items.
pub(crate) fn rows<M: Clone + 'static>(lines: impl IntoIterator<Item = Option<Line<M>>>) -> MenuList<M> {
    let mut rows = MenuList::new();
    let (mut any, mut parted) = (false, false);
    for line in lines {
        let Some(Line { label, message, enabled, keys }) = line else {
            parted = any;
            continue;
        };
        let (text, access) = access_label(&label);
        let mut row = MenuRow::new(text, move || message.clone()).enabled(enabled);
        if let Some(at) = access {
            row = row.access_at(at);
        }
        if let Some(words) = keys.and_then(|(key, modifiers)| hint(&key, modifiers)) {
            row = row.shortcut(words);
        }
        if parted {
            rows = rows.separator();
            parted = false;
        }
        rows = rows.row(row);
        any = true;
    }
    rows
}

/// A bar of `menus`, each a title (`&` marking its access letter) and its
/// rows, kept under `id`; and the titles as the bar shows them.
pub(crate) fn bar<M: Clone + 'static>(id: Id, menus: Vec<(String, MenuList<M>)>) -> (MenuBar<M>, Vec<String>) {
    let mut titles = Vec::new();
    let bar = menus.into_iter().fold(MenuBar::new(id), |bar, (label, rows)| {
        let (text, access) = access_label(&label);
        titles.push(text.clone());
        let heading = MenuHeading::new(text, rows);
        bar.heading(match access {
            Some(at) => heading.access_at(at),
            None => heading,
        })
    });
    (bar, titles)
}

/// Room a title's or a menu row's words get past what they measure: less
/// than the padding either side of them, so the words' box stays inside
/// their title or row.
pub(crate) const WORD_ROOM: f32 = 4.0;

/// `node` with every left-aligned text's box at least `room` wider to the
/// right than its words measure (`measurer`), a box already that wide left
/// as it is.
///
/// Primer (0.19) draws a menu title's words in a box exactly as wide as
/// they measured, got back by adding the title's padding and taking it off
/// again (`(w + 2p) - 2p`), which can come out a few ulps short of the
/// words. The text is shaped to wrap at its box, so the last letter went to
/// a second line the one-line box hides: "Edit" showed as "Edi". Text drawn
/// from the left is placed the same in a wider box, so widening it changes
/// nothing else; right-aligned or centred text would move, and is left be.
/// Primer 0.20.1 runs a title's box on to the title's right edge itself: a
/// box widened by `room` regardless would then end past its title.
pub(crate) fn widen_words(node: RenderNode, room: f32, measurer: &dyn TextMeasurer) -> RenderNode {
    let widen = |child: Box<RenderNode>| Box::new(widen_words(*child, room, measurer));
    match node {
        RenderNode::Leaf(Primitive::Text(mut text)) if text.align == Horizontal::Left => {
            let words = if text.spans.is_empty() {
                measurer.measure_text(&text.content, text.size, None)
            } else {
                measurer.measure_styled(&text.content, &text.spans, text.size, None)
            };
            text.bounds.width = text.bounds.width.max(words.width + room);
            RenderNode::Leaf(Primitive::Text(text))
        }
        RenderNode::Group { bounds, children } => {
            RenderNode::Group { bounds, children: children.into_iter().map(|child| widen_words(child, room, measurer)).collect() }
        }
        RenderNode::Clip { bounds, child } => RenderNode::Clip { bounds, child: widen(child) },
        RenderNode::Overlay { child } => RenderNode::Overlay { child: widen(child) },
        RenderNode::Opacity { alpha, child } => RenderNode::Opacity { alpha, child: widen(child) },
        RenderNode::Transform { translate, scale, child } => RenderNode::Transform { translate, scale, child: widen(child) },
        RenderNode::Layer { child } => RenderNode::Layer { child: widen(child) },
        leaf @ RenderNode::Leaf(_) => leaf,
    }
}

/// A widget whose open menu, and with [`words`](Self::words) its own
/// words too, is drawn with the words given [`WORD_ROOM`] ([`widen_words`]).
pub(crate) struct Roomy<M> {
    element: Element<M>,
    /// The widget's own words get room too, not only its open menu's.
    own: bool,
}

impl<M: 'static> Roomy<M> {
    /// A menu bar, or a menu: its words and its open menu's get room.
    pub(crate) fn words(element: impl Into<Element<M>>) -> Self {
        Self { element: element.into(), own: true }
    }

    /// What a right-click menu is on: only the menu's words get room, so
    /// the content's (a clip's name, say) stay inside what they label.
    pub(crate) fn menus(element: impl Into<Element<M>>) -> Self {
        Self { element: element.into(), own: false }
    }
}

impl<M: 'static> Widget<M> for Roomy<M> {
    fn sizing(&self) -> Sizing {
        self.element.sizing()
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        self.element.layout(limits, ctx)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let drawn = self.element.draw(layout, ctx);
        if self.own { widen_words(drawn, WORD_ROOM, ctx.measurer) } else { drawn }
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, M>) -> Status {
        self.element.on_event(event, layout, shell)
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.element.mouse_interaction(layout, ctx)
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        self.element.hover(layout, ctx, regions);
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<M>> {
        let overlay = self.element.overlay(layout, ctx)?;
        Some(Overlay { content: Element::new(Roomy::words(overlay.content)), ..overlay })
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Semantics {
        self.element.semantics(layout, ctx)
    }
}

impl<M: 'static> From<Roomy<M>> for Element<M> {
    fn from(roomy: Roomy<M>) -> Self {
        Element::new(roomy)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use primer::core::geometry::Vector;
    use primer::core::measure::line_height;
    use primer::core::measure::{TextLayout, TextMeasurer};
    use primer::core::render::{Quad, Text, TextSpan};
    use primer::renderer::{Fonts, TextMeasure};
    use primer::widget::custom::{Cursor, StateStore};
    use proptest::prelude::*;
    use proptest::test_runner::TestCaseError;

    use super::*;

    /// Each character as wide as its code point says, in odd fractions of a
    /// pixel, as a real font's advances are: widths that do not come back
    /// exactly from padding added and taken off.
    pub(crate) struct OddText;

    impl TextMeasurer for OddText {
        fn measure_text(&self, content: &str, size: f32, _: Option<f32>) -> Size {
            let width = content.chars().map(|c| size * (0.3 + (u32::from(c) % 17) as f32 * 0.0371)).sum();
            Size::new(width, size * 1.3)
        }
        fn measure_styled(&self, content: &str, _: &[TextSpan], size: f32, max: Option<f32>) -> Size {
            self.measure_text(content, size, max)
        }
        fn layout_text(&self, _: &str, _: f32, _: Option<f32>) -> TextLayout {
            unreachable!("nothing here edits text")
        }
    }

    /// The texts a drawing paints, anywhere in it.
    pub(crate) fn texts(node: &RenderNode, out: &mut Vec<Text>) {
        match node {
            RenderNode::Leaf(Primitive::Text(text)) => out.push(text.clone()),
            RenderNode::Group { children, .. } => children.iter().for_each(|child| texts(child, out)),
            RenderNode::Clip { child, .. }
            | RenderNode::Overlay { child }
            | RenderNode::Opacity { child, .. }
            | RenderNode::Transform { child, .. }
            | RenderNode::Layer { child } => texts(child, out),
            RenderNode::Leaf(_) => {}
        }
    }

    /// A menu label with its `&` anywhere: before the first letter, inside,
    /// on the last, a literal `&&`, or none.
    pub(crate) fn label() -> impl Strategy<Value = String> {
        ("[A-Za-z][A-Za-z ]{0,10}", 0usize..14, 0usize..4).prop_map(|(words, at, how)| {
            let at = words.char_indices().map(|(i, _)| i).chain([words.len()]).nth(at.min(words.chars().count())).unwrap();
            let mark = ["&", "&&", "", "&"][how];
            format!("{}{mark}{}", &words[..at], &words[at..])
        })
    }

    /// The quads a drawing paints, anywhere in it.
    pub(crate) fn quads(node: &RenderNode, out: &mut Vec<Quad>) {
        match node {
            RenderNode::Leaf(Primitive::Quad(quad)) => out.push(*quad),
            RenderNode::Leaf(_) => {}
            RenderNode::Group { children, .. } => children.iter().for_each(|child| quads(child, out)),
            RenderNode::Clip { child, .. }
            | RenderNode::Overlay { child }
            | RenderNode::Opacity { child, .. }
            | RenderNode::Transform { child, .. }
            | RenderNode::Layer { child } => quads(child, out),
        }
    }

    /// What a drawn menu shows a person, top to bottom.
    #[derive(Debug, Clone, PartialEq)]
    pub(crate) enum Shown {
        /// A line between rows.
        Line,
        /// A row: its words, whether it can be chosen, the letter (in
        /// characters) its access underline is under, and its shortcut.
        Row { label: String, enabled: bool, access: Option<usize>, hint: Option<String> },
    }

    /// `list` laid out and drawn with `measurer`, read back as it shows: its
    /// rows from what it says of them and the words drawn on them, its lines
    /// from the hairlines drawn between them.
    pub(crate) fn shown<M: 'static>(list: MenuList<M>, measurer: &dyn TextMeasurer) -> Vec<Shown> {
        let list: Element<M> = list.into();
        let (store, theme) = (StateStore::new(), Theme::dark());
        let node = list.layout(&Limits::new(Size::ZERO, Size::new(4000.0, 4000.0)), LayoutCtx { measurer, store: &store, theme: &theme });
        let layout = Layout::new(&node);
        let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer, visible: node.bounds() };
        let rows = list.semantics(layout, ctx).children;
        let drawn = list.draw(layout, ctx);
        let (mut words, mut painted) = (Vec::new(), Vec::new());
        texts(&drawn, &mut words);
        quads(&drawn, &mut painted);
        let on = |row: &Rectangle, y: f32| y >= row.y && y < row.y + row.height;
        let hairlines: Vec<&Quad> = painted.iter().filter(|q| q.bounds.height == 1.0).collect();
        let lines_in = |from: f32, to: f32| {
            hairlines.iter().filter(|q| q.bounds.y >= from && q.bounds.y < to && !rows.iter().any(|r| on(&r.bounds, q.bounds.y))).count()
        };
        let mut out = Vec::new();
        let mut above = f32::NEG_INFINITY;
        for row in &rows {
            let band = row.bounds;
            out.extend(std::iter::repeat_n(Shown::Line, lines_in(above, band.y)));
            above = band.y + band.height;
            let label = words.iter().find(|t| t.align == Horizontal::Left && on(&band, t.bounds.y)).expect("a row's words");
            let hint = words.iter().find(|t| t.align == Horizontal::Right && on(&band, t.bounds.y)).map(|t| t.content.clone());
            let access = hairlines.iter().filter(|q| on(&band, q.bounds.y)).find_map(|q| {
                (0..label.content.chars().count()).find(|&at| {
                    let before: String = label.content.chars().take(at).collect();
                    (label.bounds.x + measurer.measure_text(&before, label.size, None).width - q.bounds.x).abs() < 1e-3
                })
            });
            out.push(Shown::Row { label: label.content.clone(), enabled: !row.disabled, access, hint });
        }
        out.extend(std::iter::repeat_n(Shown::Line, lines_in(above, f32::INFINITY)));
        out
    }

    /// `theme` with every animation done at once, so a menu opened is
    /// there at the next draw.
    pub(crate) fn still(theme: Theme) -> Theme {
        Theme { motion: primer::core::theme::Motion::NONE, ..theme }
    }

    /// What a bar shows with its title `index` pressed: where its titles
    /// are, the words drawn on them, and the menu that opens, where it is
    /// and the words drawn on it.
    pub(crate) struct Opened {
        pub titles: Vec<Rectangle>,
        pub title_words: Vec<Text>,
        pub menu: Rectangle,
        pub words: Vec<Text>,
    }

    /// `bar` laid out in a row 4000 wide, its title `index` pressed.
    pub(crate) fn open<M: 'static>(bar: &mut Element<M>, index: usize, measurer: &dyn TextMeasurer, theme: &Theme) -> Opened {
        let mut store = StateStore::new();
        let room = Limits::new(Size::ZERO, Size::new(4000.0, 200.0));
        let node = bar.layout(&room, LayoutCtx { measurer, store: &store, theme });
        let layout = Layout::new(&node);
        let ctx = Ctx { cursor: Cursor::Unavailable, theme, store: &store, measurer, visible: node.bounds() };
        let titles: Vec<Rectangle> = bar.semantics(layout, ctx).children.iter().map(|title| title.bounds).collect();
        let title = titles[index];
        let at = Point::new(title.x + title.width / 2.0, title.y + title.height / 2.0);
        let mut said: Vec<M> = Vec::new();
        let mut clipboard = primer::core::clipboard::NoopClipboard;
        {
            let mut shell = Shell::new(&mut said, &mut store, measurer, &mut clipboard, theme, Cursor::Available(at), Modifiers::NONE);
            bar.on_event(&Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), layout, &mut shell);
        }
        let ctx = Ctx { cursor: Cursor::Unavailable, theme, store: &store, measurer, visible: node.bounds() };
        let mut title_words = Vec::new();
        texts(&bar.draw(layout, ctx), &mut title_words);
        let overlay = bar.overlay(layout, ctx).expect("the pressed title's menu is open");
        let menu_node =
            overlay.content.layout(&Limits::new(Size::ZERO, Size::new(4000.0, 4000.0)), LayoutCtx { measurer, store: &store, theme });
        let menu_layout = Layout::new(&menu_node);
        let mut words = Vec::new();
        texts(&overlay.content.draw(menu_layout, Ctx { visible: menu_node.bounds(), ..ctx }), &mut words);
        Opened { titles, title_words, menu: menu_node.bounds(), words }
    }

    thread_local! {
        /// The fonts the player draws with (Primer's bundled Source Sans
        /// Pro, the system's for anything it lacks), loaded once a thread.
        static REAL: TextMeasure = TextMeasure::new(Fonts::load());
    }

    /// `f` given a measurer of the fonts the player draws with.
    pub(crate) fn with_real<R>(f: impl FnOnce(&TextMeasure) -> R) -> R {
        REAL.with(f)
    }

    /// The window scales a menu is drawn at: the common ones, and any
    /// between.
    pub(crate) fn scale() -> impl Strategy<Value = f32> {
        prop_oneof![Just(1.0f32), Just(1.25), Just(1.5), Just(1.75), Just(2.0), 1.0f32..3.0]
    }

    /// How wide `text` draws at window scale `scale`, in logical pixels,
    /// when all of it is on its one line: shaped as the renderer shapes it,
    /// its size and its box both in physical pixels; an error naming what
    /// was cut off when a letter wraps out of its one-line box, or the box
    /// is too short for the line.
    pub(crate) fn drawn_width(real: &TextMeasure, text: &Text, scale: f32) -> Result<f32, TestCaseError> {
        let laid = real.layout_text(&text.content, text.size * scale, Some(text.bounds.width * scale));
        let count = text.content.chars().count();
        prop_assert!(
            laid.lines.len() == 1 && laid.lines[0].start == 0 && laid.lines[0].end == count,
            "{:?} in {:?} at scale {} draws as {:?}",
            text.content,
            text.bounds,
            scale,
            laid.lines.iter().map(|l| text.content.chars().skip(l.start).take(l.end - l.start).collect::<String>()).collect::<Vec<_>>()
        );
        prop_assert!(
            text.bounds.height * scale + 1e-3 >= line_height(text.size * scale),
            "{:?} in {:?} at scale {}: the box is shorter than its line",
            text.content,
            text.bounds,
            scale
        );
        Ok(laid.lines[0].width / scale)
    }

    /// Every title of `opened` and every row of its menu draws whole at
    /// `scale` with the fonts the player draws with: each title's words
    /// inside the title, each row's inside the menu, and a row's words
    /// ending before its shortcut starts.
    pub(crate) fn drawn_whole(real: &TextMeasure, opened: &Opened, scale: f32) -> Result<(), TestCaseError> {
        let slack = 1e-3;
        for text in &opened.title_words {
            let wide = drawn_width(real, text, scale)?;
            let inside = opened.titles.iter().any(|t| text.bounds.x >= t.x && text.bounds.x + wide <= t.x + t.width + slack);
            prop_assert!(inside, "{:?} ({} wide at {:?}) is past its title: {:?}", text.content, wide, text.bounds, opened.titles);
        }
        let menu = opened.menu;
        let mut drawn = Vec::new();
        for text in &opened.words {
            let wide = drawn_width(real, text, scale)?;
            let (from, to) = match text.align {
                Horizontal::Right => (text.bounds.x + text.bounds.width - wide, text.bounds.x + text.bounds.width),
                _ => (text.bounds.x, text.bounds.x + wide),
            };
            prop_assert!(
                from >= menu.x - slack && to <= menu.x + menu.width + slack,
                "{:?} ({}..{}) is past its menu {:?}",
                text.content,
                from,
                to,
                menu
            );
            drawn.push((text, from, to));
        }
        for (label, _, end) in drawn.iter().filter(|d| d.0.align == Horizontal::Left) {
            let middle = label.bounds.y + label.bounds.height / 2.0;
            for (hint, start, _) in drawn.iter().filter(|d| d.0.align == Horizontal::Right) {
                if middle >= hint.bounds.y - 4.0 && middle <= hint.bounds.y + hint.bounds.height + 4.0 {
                    prop_assert!(end <= start, "{:?} runs into its shortcut {:?} ({} past {})", label.content, hint.content, end, start);
                }
            }
        }
        Ok(())
    }

    /// A row as `rows` is given it: its label (`&` marking its access
    /// letter), whether it can be chosen, and its keys; or a separator.
    #[allow(clippy::type_complexity)]
    fn line() -> impl Strategy<Value = Option<(String, bool, Option<(Key, Modifiers)>)>> {
        let named = [
            Named::Space,
            Named::Delete,
            Named::Escape,
            Named::ArrowUp,
            Named::ArrowLeft,
            Named::PageUp,
            Named::PageDown,
            Named::Home,
            Named::F10,
        ];
        let key = prop_oneof![
            proptest::char::range('a', 'z').prop_map(Key::Character),
            proptest::sample::select(named.to_vec()).prop_map(Key::Named)
        ];
        let keys = (key, any::<[bool; 4]>()).prop_map(|(key, [shift, control, alt, logo])| (key, Modifiers { shift, control, alt, logo }));
        prop_oneof![1 => Just(None), 3 => (label(), any::<bool>(), proptest::option::of(keys)).prop_map(Some)]
    }

    proptest! {
        /// Only a label's first `&` marks its access letter; a trailing
        /// `&` marks nothing and is dropped, as Primer reads a label.
        #[test]
        fn only_the_first_ampersand_marks(a in "[a-zA-Z ]{0,6}", x in "[a-zA-Z]", b in "[a-zA-Z ]{0,6}", y in "[a-zA-Z]", c in "[a-zA-Z ]{0,6}") {
            let (text, access) = access_label(&format!("{a}&{x}{b}&{y}{c}"));
            prop_assert_eq!(&text, &format!("{a}{x}{b}{y}{c}"));
            prop_assert_eq!(access, Some(a.chars().count()));
            prop_assert_eq!(access_label(&format!("{a}{b}&")), (format!("{a}{b}"), None));
        }

        /// A menu shows each row it is given, in order, with its words, its
        /// access letter underlined, greyed when it cannot be chosen, and
        /// its shortcut; and a line only between two rows, one however many
        /// separators were given there, none at either end.
        #[test]
        fn a_menu_shows_its_rows_and_lines_only_between_them(lines in proptest::collection::vec(line(), 0..10)) {
            let mut wanted = Vec::new();
            let mut parted = false;
            for line in &lines {
                match line {
                    None => parted = !wanted.is_empty(),
                    Some((label, enabled, keys)) => {
                        if parted {
                            wanted.push(Shown::Line);
                            parted = false;
                        }
                        let (label, access) = access_label(label);
                        let hint = keys.as_ref().and_then(|(key, modifiers)| hint(key, *modifiers));
                        wanted.push(Shown::Row { label, enabled: *enabled, access, hint });
                    }
                }
            }
            let given = lines.into_iter().map(|line| line.map(|(label, enabled, keys)| Line { label, message: 0u8, enabled, keys }));
            prop_assert_eq!(shown(rows(given), &OddText), wanted);
        }

        /// Each title's access letter is underlined, under the letter its
        /// `&` marks; a title with no `&` has no underline.
        #[test]
        fn a_titles_access_letter_is_underlined(labels in proptest::collection::vec(label(), 1..6), size in 9f32..24.0) {
            let menus = labels.iter().map(|label| (label.clone(), MenuList::<u8>::new())).collect();
            let menus = bar(Id::new("bar"), menus).0;
            let store = StateStore::new();
            let mut theme = Theme::dark();
            theme.typography.body = size;
            let node = menus.layout(&Limits::new(Size::ZERO, Size::new(10_000.0, 80.0)), LayoutCtx { measurer: &OddText, store: &store, theme: &theme });
            let layout = Layout::new(&node);
            let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer: &OddText, visible: node.bounds() };
            let titles = menus.semantics(layout, ctx).children;
            let drawn = menus.draw(layout, ctx);
            let (mut words, mut painted) = (Vec::new(), Vec::new());
            texts(&drawn, &mut words);
            quads(&drawn, &mut painted);
            prop_assert_eq!(words.len(), labels.len());
            for ((title, text), label) in titles.iter().zip(&words).zip(&labels) {
                let b = title.bounds;
                let under: Vec<&Quad> = painted
                    .iter()
                    .filter(|q| q.bounds.height == 1.0 && q.bounds.y >= b.y && q.bounds.y < b.y + b.height && q.bounds.x >= b.x && q.bounds.x < b.x + b.width)
                    .collect();
                let at = under.iter().find_map(|q| {
                    (0..text.content.chars().count()).find(|&at| {
                        let before: String = text.content.chars().take(at).collect();
                        (text.bounds.x + OddText.measure_text(&before, text.size, None).width - q.bounds.x).abs() < 1e-3
                    })
                });
                prop_assert_eq!(at, access_label(label).1, "{:?}", label);
            }
        }

        /// The rows of a menu opened from the bar get room too: each row's
        /// words, measured as drawn, fit the box they are drawn in with
        /// room to spare, a row as wide as its menu (no shortcut) included.
        #[test]
        fn an_open_menus_rows_get_room(labels in proptest::collection::vec("[A-Za-z][A-Za-z ]{24,40}[A-Za-z]", 1..5), size in 9f32..24.0) {
            let menu = rows(labels.iter().map(|label| Some(Line { label: label.clone(), message: 0u8, enabled: true, keys: None })));
            let mut menus: Element<u8> = Roomy::words(bar(Id::new("bar"), vec![("&File".to_owned(), menu)]).0).into();
            let mut theme = still(Theme::dark());
            theme.typography.body = size;
            let opened = open(&mut menus, 0, &OddText, &theme);
            prop_assert_eq!(opened.words.len(), labels.len());
            for text in &opened.words {
                let words = OddText.measure_text(&text.content, text.size, None).width;
                prop_assert!(text.bounds.width >= words + 2.0, "{:?} is {} wide in {:?}", text.content, words, text.bounds);
            }
        }
    }

    /// Each named key is written as a native menu writes it, short where
    /// it is long; a key with no name has no hint.
    #[test]
    fn named_keys_are_written_short() {
        let said = |key: Key| hint(&key, Modifiers::NONE);
        for (key, words) in [
            (Key::Character(' '), "Space"),
            (Key::Named(Named::Space), "Space"),
            (Key::Named(Named::Delete), "Del"),
            (Key::Named(Named::Escape), "Esc"),
            (Key::Named(Named::ArrowUp), "Up"),
            (Key::Named(Named::ArrowDown), "Down"),
            (Key::Named(Named::ArrowLeft), "Left"),
            (Key::Named(Named::ArrowRight), "Right"),
            (Key::Named(Named::PageUp), "PgUp"),
            (Key::Named(Named::PageDown), "PgDn"),
            (Key::Named(Named::Home), "Home"),
            (Key::Named(Named::F10), "F10"),
        ] {
            assert_eq!(said(key.clone()).as_deref(), Some(words), "{key:?}");
        }
        assert_eq!(said(Key::Unidentified), None);
    }

    proptest! {
        /// A label's `&` is taken out and marks the letter after it; `&&`
        /// is one `&` and marks nothing.
        #[test]
        fn an_ampersand_marks_the_letter_after_it(before in "[a-zA-Z …]{0,12}", letter in "[a-zA-Z0-9]", after in "[a-zA-Z …]{0,12}") {
            let (text, access) = access_label(&format!("{before}&{letter}{after}"));
            prop_assert_eq!(&text, &format!("{before}{letter}{after}"));
            let at = access.expect("an access letter");
            prop_assert_eq!(text.chars().nth(at).map(String::from), Some(letter));
            prop_assert_eq!(access_label(&format!("{before}&&{after}")), (format!("{before}&{after}"), None));
        }

        /// A shortcut is written modifiers first, in one order, then its key.
        #[test]
        fn a_shortcut_is_written_modifiers_first(c in proptest::char::range('a', 'z'), held in any::<[bool; 4]>()) {
            let [shift, control, alt, logo] = held;
            let words = hint(&Key::Character(c), Modifiers { shift, control, alt, logo }).expect("a letter has a name");
            let parts: Vec<&str> = words.split('+').collect();
            let key = c.to_uppercase().to_string();
            prop_assert_eq!(*parts.last().unwrap(), key.as_str());
            let logo_name = if cfg!(target_os = "macos") { "Cmd" } else { "Super" };
            let names: Vec<&str> =
                [(control, "Ctrl"), (alt, "Alt"), (shift, "Shift"), (logo, logo_name)].into_iter().filter(|h| h.0).map(|h| h.1).collect();
            prop_assert_eq!(&parts[..parts.len() - 1], names.as_slice());
        }

        /// Each title's words, measured as drawn, fit inside the box they
        /// are drawn in with room to spare, and that box lies inside the
        /// title: no title loses its last letter ("Edit" showed as "Edi"),
        /// wherever its `&` is.
        #[test]
        fn no_title_is_cut_off(labels in proptest::collection::vec(label(), 1..7), size in 9f32..24.0) {
            let menus = labels.iter().map(|label| (label.clone(), MenuList::<u8>::new())).collect();
            let bar = Roomy::words(bar(Id::new("bar"), menus).0);
            let store = primer::widget::custom::StateStore::new();
            let mut theme = Theme::dark();
            theme.typography.body = size;
            let ctx = LayoutCtx { measurer: &OddText, store: &store, theme: &theme };
            let node = bar.layout(&Limits::new(Size::ZERO, Size::new(10_000.0, 80.0)), ctx);
            let layout = Layout::new(&node);
            let cursor = primer::widget::custom::Cursor::Unavailable;
            let draw = Ctx { cursor, theme: &theme, store: &store, measurer: &OddText, visible: node.bounds() };
            let said = bar.semantics(layout, draw);
            let mut drawn = Vec::new();
            texts(&bar.draw(layout, draw), &mut drawn);
            prop_assert_eq!(drawn.len(), labels.len());
            for ((text, label), title) in drawn.iter().zip(&labels).zip(&said.children) {
                prop_assert_eq!(&text.content, &access_label(label).0);
                let words = OddText.measure_text(&text.content, text.size, None).width;
                prop_assert!(text.bounds.width >= words + WORD_ROOM / 2.0, "{:?} is {} wide in {:?}", text.content, words, text.bounds);
                let title = title.bounds;
                prop_assert!(
                    text.bounds.x >= title.x && text.bounds.x + text.bounds.width <= title.x + title.width + 1e-3,
                    "{:?} past {:?}",
                    text.bounds,
                    title
                );
            }
        }

        /// Widening the words moves no drawn thing but a left-aligned
        /// text's right edge, and that only out to `room` past its words:
        /// a box already that wide (as Primer 0.20.1 draws a title's,
        /// running on to the title's edge) is left as it is, so it stays
        /// inside its title. Words inside anything a drawing wraps them in
        /// (a clip, a transform, a layer, an overlay, a fade, a group) are
        /// widened the same.
        #[test]
        fn widening_moves_only_left_aligned_right_edges(
            boxes in proptest::collection::vec((0f32..500.0, 0f32..500.0, 0f32..200.0, 0usize..3, "[A-Za-z ]{0,12}", proptest::collection::vec(0usize..6, 0..3)), 0..8),
            room in 0f32..20.0,
        ) {
            let aligns = [Horizontal::Left, Horizontal::Center, Horizontal::Right];
            let area = Rectangle::new(Point::ORIGIN, Size::new(1000.0, 1000.0));
            let children: Vec<RenderNode> = boxes
                .iter()
                .map(|(x, y, w, a, words, wraps)| {
                    let bounds = Rectangle::new(Point::new(*x, *y), Size::new(*w, 20.0));
                    let text = Text::new(words.clone(), bounds, 14.0, Color::WHITE).with_align(aligns[*a]);
                    wraps.iter().fold(RenderNode::text(text), |node, wrap| {
                        let child = Box::new(node);
                        match wrap {
                            0 => RenderNode::Opacity { alpha: 1.0, child },
                            1 => RenderNode::Clip { bounds: area, child },
                            2 => RenderNode::Transform { translate: Vector::new(3.0, 4.0), scale: 1.5, child },
                            3 => RenderNode::Layer { child },
                            4 => RenderNode::Overlay { child },
                            _ => RenderNode::group(area, vec![*child]),
                        }
                    })
                })
                .collect();
            let whole = RenderNode::group(Rectangle::new(Point::ORIGIN, Size::new(1000.0, 1000.0)), children);
            let (mut before, mut after) = (Vec::new(), Vec::new());
            texts(&whole, &mut before);
            texts(&widen_words(whole, room, &OddText), &mut after);
            prop_assert_eq!(before.len(), after.len());
            for (old, new) in before.iter().zip(&after) {
                let wanted = OddText.measure_text(&old.content, old.size, None).width + room;
                let width = if old.align == Horizontal::Left { old.bounds.width.max(wanted) } else { old.bounds.width };
                prop_assert_eq!(new.bounds.position(), old.bounds.position());
                prop_assert_eq!(new.bounds.width, width);
                prop_assert_eq!(new.bounds.height, old.bounds.height);
            }
        }
    }

    /// Only macOS has the menus outside the window.
    #[test]
    fn the_menus_are_in_the_window_but_on_macos() {
        assert_eq!(in_window(), !cfg!(target_os = "macos"));
    }
}
