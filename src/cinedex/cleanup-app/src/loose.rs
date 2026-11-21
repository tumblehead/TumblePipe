//! A pane that hands its child loose limits.
//!
//! A column gives a `Fill` child tight limits (its minimum is the whole
//! share), and an `empty_state`'s inner column resolves its shrink height
//! to that minimum, so its icon, title and words stack at the top instead
//! of sitting centred. Laid out in this pane, which fills the bounded share
//! it is given (never a scrollable's unbounded height) but lets its child
//! take as little as it needs, the empty state centres itself both ways.

use primer::prelude::*;
use primer::widget::custom::{Ctx, Event, Layout, LayoutCtx, Limits, Node, Overlay, RenderNode, Shell, Sizing, Status, Widget, mouse};

pub struct Loose<Message> {
    child: Element<Message>,
    /// A fixed width for the child: a share of the pane within bounds,
    /// the child then centred both ways by the pane itself.
    width: Option<(f32, f32, f32)>,
}

impl<Message> Loose<Message> {
    pub fn new(child: impl Into<Element<Message>>) -> Self {
        Loose { child: child.into(), width: None }
    }

    /// Lay the child out `share` of the pane wide, but no narrower than
    /// `min` (nor the pane) and no wider than `max`, centred.
    pub fn width(mut self, share: f32, min: f32, max: f32) -> Self {
        self.width = Some((share, min, max));
        self
    }
}

impl<Message> Widget<Message> for Loose<Message> {
    fn sizing(&self) -> Sizing {
        Sizing::new(Length::Fill, Length::Fill)
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        let size = limits.max();
        let Some((share, min, max)) = self.width else {
            let child = self.child.layout(&Limits::new(Size::ZERO, size), ctx);
            return Node::with_children(size, vec![child]);
        };
        let wide = (size.width * share).clamp(min, max).min(size.width);
        let child = self.child.layout(&Limits::new(Size::new(wide, 0.0), Size::new(wide, size.height)), ctx);
        let at = Point::new(((size.width - child.size().width) / 2.0).max(0.0), ((size.height - child.size().height) / 2.0).max(0.0));
        Node::with_children(size, vec![child.move_to(at)])
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let children = layout.children().map(|at| self.child.draw(at, ctx)).collect();
        RenderNode::group(layout.bounds(), children)
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        match layout.children().next() {
            Some(at) => self.child.on_event(event, at, shell),
            None => Status::Ignored,
        }
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        layout.children().next().map_or(mouse::Interaction::Idle, |at| self.child.mouse_interaction(at, ctx))
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        if let Some(at) = layout.children().next() {
            self.child.hover(at, ctx, regions);
        }
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<Message>> {
        self.child.overlay(layout.children().next()?, ctx)
    }
}

impl<Message: 'static> From<Loose<Message>> for Element<Message> {
    fn from(pane: Loose<Message>) -> Self {
        Element::new(pane)
    }
}
