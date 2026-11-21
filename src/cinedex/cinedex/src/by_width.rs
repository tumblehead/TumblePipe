//! An element built for the width it is laid out in.
//!
//! A `view` builds its tree before anything is laid out, so what it builds
//! can only follow the widths the model knows. A Primer split shows a drag
//! of its handle itself and tells the model the new size only on release,
//! so a pane's content built from the model's width is built for the old
//! width all through a drag, then rebuilt for the new one when the handle
//! is let go: the bins' grid kept its column count while the panel was
//! dragged and jumped to another on release.
//!
//! [`by_width`] puts the building off until layout: `build` is called with
//! the width the element is given, and called again only when that width
//! changes (each pointer move of a drag re-lays the split out). Every other
//! pass goes to what was built last.

use std::cell::RefCell;

use primer::prelude::*;
use primer::widget::custom::{
    Ctx, Event, Layout, LayoutCtx, Limits, Node, Overlay, RenderNode, Semantics, Shell, Sizing, Status, Widget, mouse,
};

type Build<Message> = Box<dyn Fn(f32) -> Element<Message>>;

/// An element `sizing` big, built by `build` for the width it is laid out
/// at.
pub fn by_width<Message: 'static>(sizing: Sizing, build: impl Fn(f32) -> Element<Message> + 'static) -> ByWidth<Message> {
    ByWidth { sizing, build: Box::new(build), built: RefCell::new(None) }
}

pub struct ByWidth<Message> {
    sizing: Sizing,
    build: Build<Message>,
    /// The width last built for, and what was built.
    built: RefCell<Option<(f32, Element<Message>)>>,
}

/// The width an element laid out within `limits` is built for: the most it
/// may be, or the least when that is unbounded.
pub fn built_width(limits: &Limits) -> f32 {
    let max = limits.max().width;
    if max.is_finite() { max } else { limits.min().width }
}

impl<Message: 'static> ByWidth<Message> {
    /// What is built for `width`, building it first when it was built for
    /// another width or not yet.
    fn ensure(&self, width: f32) {
        let mut built = self.built.borrow_mut();
        if built.as_ref().is_none_or(|(at, _)| *at != width) {
            *built = Some((width, (self.build)(width)));
        }
    }

    /// Built for `width` when nothing was yet.
    fn ensure_any(&self, width: f32) {
        if self.built.borrow().is_none() {
            self.ensure(width);
        }
    }

    /// What was built last; built for `width` when nothing was yet.
    fn with<R>(&self, width: f32, f: impl FnOnce(&Element<Message>) -> R) -> R {
        self.ensure_any(width);
        let built = self.built.borrow();
        f(&built.as_ref().expect("built above").1)
    }
}

impl<Message: 'static> Widget<Message> for ByWidth<Message> {
    fn sizing(&self) -> Sizing {
        self.sizing
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        self.ensure(built_width(limits));
        self.with(0.0, |child| child.layout(limits, ctx))
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        self.with(layout.bounds().width, |child| child.draw(layout, ctx))
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        self.ensure_any(layout.bounds().width);
        let built = self.built.get_mut();
        built.as_mut().expect("built above").1.on_event(event, layout, shell)
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.with(layout.bounds().width, |child| child.mouse_interaction(layout, ctx))
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        self.with(layout.bounds().width, |child| child.hover(layout, ctx, regions));
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<Message>> {
        self.with(layout.bounds().width, |child| child.overlay(layout, ctx))
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Semantics {
        self.with(layout.bounds().width, |child| child.semantics(layout, ctx))
    }
}

impl<Message: 'static> From<ByWidth<Message>> for Element<Message> {
    fn from(widget: ByWidth<Message>) -> Self {
        Element::new(widget)
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::rc::Rc;

    use primer::core::measure::{TextLayout, TextMeasurer};
    use primer::core::render::TextSpan;
    use primer::widget::custom::StateStore;
    use proptest::prelude::*;

    use super::*;

    struct NoText;

    impl TextMeasurer for NoText {
        fn measure_text(&self, _: &str, _: f32, _: Option<f32>) -> Size {
            Size::ZERO
        }
        fn measure_styled(&self, _: &str, _: &[TextSpan], _: f32, _: Option<f32>) -> Size {
            Size::ZERO
        }
        fn layout_text(&self, _: &str, _: f32, _: Option<f32>) -> TextLayout {
            unreachable!("nothing here edits text")
        }
    }

    proptest! {
        /// What is built is built for the width it is laid out in, at each
        /// layout, and only rebuilt when that width changes: a drag's run of
        /// layouts at new widths builds once per width.
        #[test]
        fn it_is_built_for_each_width_it_is_laid_out_at(widths in prop::collection::vec(1u16..2000, 1..12), tall in 1f32..900.0) {
            let asked: Rc<RefCell<Vec<f32>>> = Rc::default();
            let calls = Rc::new(Cell::new(0usize));
            let widget = {
                let (asked, calls) = (Rc::clone(&asked), Rc::clone(&calls));
                by_width::<()>(Sizing::FILL, move |width| {
                    asked.borrow_mut().push(width);
                    calls.set(calls.get() + 1);
                    space(Length::Fill, Length::Fill).into()
                })
            };
            let (store, theme) = (StateStore::new(), Theme::dark());
            let mut changes = 0;
            let mut last = None;
            for &w in &widths {
                let w = f32::from(w);
                let node = widget.layout(&Limits::new(Size::ZERO, Size::new(w, tall)), LayoutCtx { measurer: &NoText, store: &store, theme: &theme });
                prop_assert_eq!(node.bounds().width, w);
                prop_assert_eq!(asked.borrow().last().copied(), Some(w), "laid out at {} but built for another width", w);
                if last != Some(w) {
                    changes += 1;
                }
                last = Some(w);
            }
            prop_assert_eq!(calls.get(), changes, "rebuilt without the width changing");
        }
    }

    /// An event that comes before any layout is given to what is built for
    /// the width it is placed at, not lost or a panic.
    #[test]
    fn an_event_before_any_layout_builds_for_its_width() {
        let asked: Rc<RefCell<Vec<f32>>> = Rc::default();
        let mut widget = {
            let asked = Rc::clone(&asked);
            by_width::<()>(Sizing::FILL, move |width| {
                asked.borrow_mut().push(width);
                space(Length::Fill, Length::Fill).into()
            })
        };
        let node = Node::new(Size::new(321.0, 40.0));
        let (mut store, theme, mut said) = (StateStore::new(), Theme::dark(), Vec::new());
        let mut clipboard = primer::core::clipboard::NoopClipboard;
        let cursor = primer::widget::custom::Cursor::Unavailable;
        let mut shell = Shell::new(&mut said, &mut store, &NoText, &mut clipboard, &theme, cursor, primer::keyboard::Modifiers::NONE);
        let event = Event::Mouse(mouse::Event::CursorMoved { position: Point::new(5.0, 5.0) });
        widget.on_event(&event, Layout::new(&node), &mut shell);
        assert_eq!(*asked.borrow(), [321.0]);
    }

    #[test]
    fn an_unbounded_width_builds_for_the_least() {
        let limits = Limits::new(Size::new(30.0, 0.0), Size::new(f32::INFINITY, 10.0));
        assert_eq!(built_width(&limits), 30.0);
        assert_eq!(built_width(&Limits::new(Size::ZERO, Size::new(250.0, 10.0))), 250.0);
    }
}
