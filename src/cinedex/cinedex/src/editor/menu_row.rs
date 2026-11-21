//! The menus at the start of the toolbar's row, on Windows and Linux.
//!
//! There the player has no native menu bar (it is built without Primer's
//! `os` feature, see `menu_bar`), and Primer would draw the menus as a bar
//! of their own above the whole view, a row of the window holding nothing
//! else. The editor draws them itself instead, as the first thing in its
//! toolbar, before Cut|Board ([`Editor::menu_row`]), and gives Primer no
//! menu ([`in_window`]). On macOS the menus are the global menu bar at the
//! top of the screen, outside the window, so the toolbar is the window's
//! only row there already.
//!
//! The bar is Primer's own [`MenuBar`](primer::widgets::MenuBar), built
//! (`menu_words`) from the same [`headings`] as the native bar and the keys,
//! its words given room so none loses its last letter. It is made to take
//! only its titles' width in the row ([`Hugged`]) and drawn on the toolbar's
//! colour, not as a band of the page's.

use primer::prelude::*;
use primer::widget::custom::{
    Ctx, Event, Layout, LayoutCtx, Limits, Node, Overlay, RenderNode, Semantics, Shell, Sizing, Status, Widget, mouse,
};

pub(super) use crate::menu_words::in_window;
use crate::menu_words::{self, Line, Roomy};

use super::menu_bar::{Entry, Heading, State, headings};
use super::{Editor, Message};

/// Room Primer's menu bar leaves at each end, and either side of a title's
/// words (`primer-widgets/src/menu_bar.rs`); `a_bar_is_as_wide_as_its_titles`
/// holds these to what the bar draws.
const BAR_PAD_X: f32 = 4.0;
const TITLE_PAD_X: f32 = 10.0;

/// A menu's entries as the rows of a bar drawn in the window.
fn lines(entries: Vec<Entry>) -> impl Iterator<Item = Option<Line<Message>>> {
    entries.into_iter().map(|entry| match entry {
        Entry::Separator => None,
        Entry::Item { label, message, enabled, keys } => Some(Line { label: label.to_owned(), message, enabled, keys }),
    })
}

/// The bar of the menus `state` makes.
fn bar(state: &State) -> Hugged {
    bar_of(headings(state))
}

/// The bar of `headings`.
fn bar_of(headings: Vec<Heading>) -> Hugged {
    let menus = headings.into_iter().map(|heading| (heading.label.to_owned(), menu_words::rows(lines(heading.entries)))).collect();
    let (bar, titles) = menu_words::bar(Id::new("menu row"), menus);
    Hugged { bar: Roomy::words(bar).into(), titles }
}

impl Editor {
    /// The menus as a bar at the start of the toolbar's row.
    pub(super) fn menu_row(&self) -> Element<Message> {
        Element::new(bar(&self.menu_state()))
    }
}

/// The width a menu bar of `titles` needs, each `measured` that wide: its
/// titles side by side, each with its room either side, and its own room at
/// both ends.
pub(super) fn bar_width(measured: impl IntoIterator<Item = f32>) -> f32 {
    2.0 * BAR_PAD_X + measured.into_iter().map(|width| width + 2.0 * TITLE_PAD_X).sum::<f32>()
}

/// Primer's menu bar, as wide as its titles (it fills its row otherwise)
/// and drawn on whatever is under it: the band and the hairline under it
/// that set it off from the page as a row of its own are left out.
struct Hugged {
    /// The bar, its words given room ([`Roomy`]).
    bar: Element<Message>,
    /// The titles, as the bar shows them.
    titles: Vec<String>,
}

impl Hugged {
    /// The titles' width, to the next whole pixel: Primer adds the same
    /// widths up in another order, which can come to a hair more.
    fn width(&self, ctx: LayoutCtx<'_>) -> f32 {
        let size = ctx.theme.typography.body;
        bar_width(self.titles.iter().map(|title| ctx.measurer.measure_text(title, size, None).width)).ceil()
    }
}

/// `theme` with the page's colour and the border see-through: what the bar
/// draws its band and its hairline in.
fn on_toolbar(theme: &Theme) -> Theme {
    Theme { bg_page: Color::TRANSPARENT, border: Color::TRANSPARENT, ..*theme }
}

impl Widget<Message> for Hugged {
    fn sizing(&self) -> Sizing {
        Sizing::new(Length::Shrink, Length::Shrink)
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        let width = self.width(ctx).min(limits.max().width);
        let narrowed = Limits::new(Size::new(width, limits.min().height), Size::new(width, limits.max().height));
        self.bar.layout(&narrowed, ctx)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let theme = on_toolbar(ctx.theme);
        self.bar.draw(layout, Ctx { theme: &theme, ..ctx })
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        self.bar.on_event(event, layout, shell)
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.bar.mouse_interaction(layout, ctx)
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        self.bar.hover(layout, ctx, regions);
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<Message>> {
        self.bar.overlay(layout, ctx)
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Semantics {
        self.bar.semantics(layout, ctx)
    }
}

#[cfg(test)]
mod tests {
    use primer::core::measure::{TextLayout, TextMeasurer};
    use primer::core::render::{Primitive, Quad, TextSpan};
    use primer::widget::custom::{Cursor, StateStore};
    use proptest::prelude::*;
    use th_edit::document::TakeKind;

    use super::*;
    use crate::menu_words::tests::{OddText, drawn_whole, label, open, scale, still, texts, with_real};
    use crate::menu_words::{WORD_ROOM, access_label};

    /// Every character half its size wide.
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

    fn state() -> impl Strategy<Value = State> {
        let show = proptest::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]);
        (any::<[bool; 9]>(), proptest::option::of(any::<bool>()), 0usize..10_000, show).prop_map(|(b, master, end, show)| State {
            free: b[0],
            writable: b[0] && b[1],
            named: b[2],
            master,
            picked: b[3],
            playing: b[4],
            burnins: b[5],
            show,
            audio_hidden: b[6],
            focused: b[6] || b[7],
            flipped: b[8],
            end,
        })
    }

    /// The quads a drawing paints, anywhere in it.
    fn quads(node: &RenderNode, out: &mut Vec<Quad>) {
        match node {
            RenderNode::Leaf(Primitive::Quad(quad)) => out.push(*quad),
            RenderNode::Group { children, .. } => children.iter().for_each(|child| quads(child, out)),
            _ => {}
        }
    }

    proptest! {
        /// The bar in the toolbar's row is as wide as Primer draws its
        /// titles, to the next whole pixel, so none is cut off and it leaves
        /// the rest of the row to Cut|Board and the switches; its titles are
        /// the menus' in order, and it paints no band or hairline of its own
        /// over the toolbar.
        #[test]
        fn a_bar_is_as_wide_as_its_titles(state in state(), room in 400f32..3000.0) {
            let bar = bar(&state);
            let (store, theme) = (StateStore::new(), Theme::dark());
            let ctx = LayoutCtx { measurer: &FixedText, store: &store, theme: &theme };
            let node = bar.layout(&Limits::new(Size::ZERO, Size::new(room, 60.0)), ctx);
            let layout = Layout::new(&node);
            let draw = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer: &FixedText, visible: node.bounds() };
            let said = bar.semantics(layout, draw);
            let titles: Vec<String> = said.children.iter().filter_map(|s| s.label.clone()).collect();
            prop_assert_eq!(titles, ["File", "Edit", "Playback", "View"].map(String::from).to_vec());
            let last = said.children.last().expect("a title").bounds;
            let slack = node.bounds().width - (last.x + last.width + BAR_PAD_X);
            prop_assert!((0.0..1.0).contains(&slack), "{:?} in {:?}", last, node.bounds());
            prop_assert!((said.children[0].bounds.x - BAR_PAD_X).abs() < 1e-3);
            let mut painted = Vec::new();
            quads(&bar.draw(layout, draw), &mut painted);
            let wide = node.bounds().width;
            prop_assert!(painted.iter().all(|q| !q.style.is_visible() || q.bounds.width < wide), "a band over the toolbar: {:?}", painted);
        }

        /// However narrow the toolbar's row, the bar takes no more of it
        /// than it is given.
        #[test]
        fn a_bar_takes_no_more_than_its_row(state in state(), room in 0f32..400.0) {
            let bar = bar(&state);
            let (store, theme) = (StateStore::new(), Theme::dark());
            let ctx = LayoutCtx { measurer: &FixedText, store: &store, theme: &theme };
            let node = bar.layout(&Limits::new(Size::ZERO, Size::new(room, 60.0)), ctx);
            prop_assert!(node.bounds().width <= room + 1e-3, "{} wide in a row of {}", node.bounds().width, room);
        }

        /// With the fonts the player draws with, at the common window
        /// scales and any between, each title of the editor's menus and
        /// each row of each menu they open (its words and its shortcut)
        /// draws whole on its one line, inside its title or menu, a row's
        /// words ending before its shortcut: none wraps a letter out of
        /// sight ("Edit" drew as "Edi") whatever the edit's state.
        #[test]
        fn the_editors_menus_draw_whole_at_any_scale(state in state(), scale in scale()) {
            with_real(|real| {
                let theme = still(crate::display::theme());
                let mut menus = Element::new(bar(&state));
                for index in 0..headings(&state).len() {
                    let opened = open(&mut menus, index, real, &theme);
                    prop_assert!(!opened.words.is_empty());
                    drawn_whole(real, &opened, scale)?;
                }
                Ok(())
            })?;
        }

        /// In the toolbar's row too, each title's words, measured as drawn,
        /// fit the box they are drawn in with room to spare, inside the
        /// title, wherever the label's `&` is: "Edit" no longer shows as
        /// "Edi".
        #[test]
        fn no_title_in_the_row_is_cut_off(labels in proptest::collection::vec(label(), 1..7), size in 9f32..24.0) {
            let leaked: Vec<&'static str> = labels.iter().map(|l| &*Box::leak(l.clone().into_boxed_str())).collect();
            let bar = bar_of(leaked.iter().map(|&label| Heading { label, entries: Vec::new() }).collect());
            let store = StateStore::new();
            let mut theme = Theme::dark();
            theme.typography.body = size;
            let ctx = LayoutCtx { measurer: &OddText, store: &store, theme: &theme };
            let node = bar.layout(&Limits::new(Size::ZERO, Size::new(10_000.0, 80.0)), ctx);
            let layout = Layout::new(&node);
            let draw = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer: &OddText, visible: node.bounds() };
            let said = bar.semantics(layout, draw);
            let mut drawn = Vec::new();
            texts(&bar.draw(layout, draw), &mut drawn);
            prop_assert_eq!(drawn.len(), labels.len());
            // The bar is as wide as its titles and its room: the last is not past it.
            let last = said.children.last().expect("a title").bounds;
            prop_assert!(node.bounds().width + 1e-3 >= last.x + last.width + BAR_PAD_X, "{:?} past {:?}", last, node.bounds());
            for ((text, label), title) in drawn.iter().zip(&labels).zip(&said.children) {
                prop_assert_eq!(&text.content, &access_label(label).0);
                let words = OddText.measure_text(&text.content, text.size, None).width;
                prop_assert!(text.bounds.width >= words + WORD_ROOM / 2.0, "{:?} is {} wide in {:?}", text.content, words, text.bounds);
                let title = title.bounds;
                prop_assert!(text.bounds.x >= title.x && text.bounds.x + text.bounds.width <= title.x + title.width + 1e-3);
            }
        }
    }
}
