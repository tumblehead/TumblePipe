//! A widget that is its child and also watches what the pointer does over
//! it, for what Primer's own widgets do not report: which row of a list
//! the pointer is on (a list sends a click and a double-click, nothing
//! else, and shows no overlay of its rows, so a row has no tooltip of its
//! own), a press that may become a drag out of the list, a right press on
//! one row, the pointer over the timeline while something is carried.
//!
//! [`sense`] wraps any element. `on` is asked at every event, before the
//! child, with the wrapper's bounds, the pointer and the keys held, and may
//! send a message; the event still goes to the child, so a press the list
//! takes as a click is one here too. `float` shows words in a box over the
//! window (a row's tip, what a drag carries), `marks` paints outlined boxes
//! over the child (where a drop would land), and `copy` puts words on the
//! clipboard, which only a widget can reach.

use primer::keyboard::Modifiers;
use primer::prelude::*;
use primer::widget::custom::{
    BoxStyle, Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Node, Overlay, Placement, RenderNode, Shell, Sizing, Status, TextNode, Widget,
    mouse,
};

use crate::look;

/// Words in a box over the window: its top-left, and what on the page it
/// belongs to (it flips to the other side of that near the window's edge).
#[derive(Debug, Clone, PartialEq)]
pub struct Float {
    pub words: String,
    pub at: Point,
    pub anchor: Option<Rectangle>,
    /// Said as a refusal.
    pub caution: bool,
}

/// A box painted over the child, in the child's own pixels: an outline, a
/// wash inside it, and its words.
#[derive(Debug, Clone, PartialEq)]
pub struct Mark {
    pub rect: Rectangle,
    pub words: String,
    /// Drawn as somewhere the drop cannot go.
    pub refused: bool,
}

type On<Message> = Box<dyn Fn(&Event, Rectangle, Cursor, Modifiers) -> Option<Message>>;

pub struct Sense<Message> {
    child: Element<Message>,
    on: Option<On<Message>>,
    float: Option<Float>,
    marks: Option<Box<dyn Fn(Size) -> Vec<Mark>>>,
    copy: Option<(String, Message)>,
}

/// `child`, watched.
pub fn sense<Message: 'static>(child: impl Into<Element<Message>>) -> Sense<Message> {
    Sense { child: child.into(), on: None, float: None, marks: None, copy: None }
}

impl<Message> Sense<Message> {
    /// Ask `on` at every event: the event, these bounds, the pointer and
    /// the keys held.
    pub fn on(mut self, on: impl Fn(&Event, Rectangle, Cursor, Modifiers) -> Option<Message> + 'static) -> Self {
        self.on = Some(Box::new(on));
        self
    }

    pub fn float(mut self, float: Option<Float>) -> Self {
        self.float = float;
        self
    }

    /// The boxes to paint over the child, given its size.
    pub fn marks(mut self, marks: impl Fn(Size) -> Vec<Mark> + 'static) -> Self {
        self.marks = Some(Box::new(marks));
        self
    }

    /// Put `words` on the clipboard at the next event, then send `done`.
    pub fn copy(mut self, copy: Option<(String, Message)>) -> Self {
        self.copy = copy;
        self
    }
}

/// How thick a mark's outline is.
const OUTLINE: f32 = 2.0;

fn mark_nodes(mark: &Mark, origin: Point, nodes: &mut Vec<RenderNode>) {
    let r = Rectangle::new(Point::new(origin.x + mark.rect.x, origin.y + mark.rect.y), mark.rect.size());
    let colour = if mark.refused { look::error() } else { look::now() };
    let wash = Color { a: 0.18, ..colour };
    nodes.push(RenderNode::quad(r, BoxStyle::solid(wash)));
    let sides = [
        Rectangle::new(Point::new(r.x, r.y), Size::new(r.width, OUTLINE)),
        Rectangle::new(Point::new(r.x, r.y + r.height - OUTLINE), Size::new(r.width, OUTLINE)),
        Rectangle::new(Point::new(r.x, r.y), Size::new(OUTLINE, r.height)),
        Rectangle::new(Point::new(r.x + r.width - OUTLINE, r.y), Size::new(OUTLINE, r.height)),
    ];
    nodes.extend(sides.into_iter().map(|side| RenderNode::quad(side, BoxStyle::solid(colour))));
    if !mark.words.is_empty() && r.width > 24.0 {
        let inside = Rectangle::new(Point::new(r.x + 5.0, r.y + 3.0), Size::new(r.width - 10.0, 14.0));
        nodes.push(RenderNode::clip(r, RenderNode::text(TextNode::new(mark.words.clone(), inside, 11.0, look::text()))));
    }
}

impl<Message: Clone + 'static> Widget<Message> for Sense<Message> {
    fn sizing(&self) -> Sizing {
        self.child.sizing()
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        self.child.layout(limits, ctx)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        let child = self.child.draw(layout, ctx);
        let marks = self.marks.as_ref().map(|marks| marks(bounds.size())).unwrap_or_default();
        if marks.is_empty() {
            return child;
        }
        let mut over = Vec::new();
        for mark in &marks {
            mark_nodes(mark, Point::new(bounds.x, bounds.y), &mut over);
        }
        RenderNode::group(bounds, vec![child, RenderNode::layer(RenderNode::clip(bounds, RenderNode::group(bounds, over)))])
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        if let Some((words, done)) = self.copy.take() {
            shell.clipboard().write(words);
            shell.publish(done);
        }
        if let Some(on) = &self.on
            && let Some(message) = on(event, layout.bounds(), shell.cursor(), shell.modifiers())
        {
            shell.publish(message);
            shell.request_redraw();
        }
        self.child.on_event(event, layout, shell)
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.child.mouse_interaction(layout, ctx)
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        // The pointer's moves matter here wherever it is.
        regions.motion(layout.bounds());
        self.child.hover(layout, ctx, regions);
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<Message>> {
        // A menu or popup the child has open wins: one overlay at a time.
        if let Some(overlay) = self.child.overlay(layout, ctx) {
            return Some(overlay);
        }
        let float = self.float.as_ref()?;
        let colour = if float.caution { look::caution() } else { look::text() };
        let words =
            container(text(float.words.clone()).font_size(12.0).color(colour)).padding(Padding::from([4.0, 8.0])).background(look::band());
        Some(Overlay {
            placement: Placement::At { position: float.at, anchor: float.anchor },
            content: words.into(),
            opacity: 1.0,
            modal: None,
            // Decoration: it never takes the click or the pointer's shape.
            interactive: false,
        })
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> primer::widget::custom::Semantics {
        self.child.semantics(layout, ctx)
    }
}

impl<Message: Clone + 'static> From<Sense<Message>> for Element<Message> {
    fn from(widget: Sense<Message>) -> Self {
        Element::new(widget)
    }
}
