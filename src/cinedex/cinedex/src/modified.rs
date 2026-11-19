//! A widget's messages, told which keys were held when it sent them.
//!
//! Primer's list reports a double-click as its row and nothing more, and a
//! button a press; neither says whether Shift was down. [`modified`] wraps
//! any element and passes each message it publishes through a function with
//! the modifiers of the event that made it, so Shift+double-click in a bin
//! can overlay where a plain double-click inserts.

use primer::keyboard::Modifiers;
use primer::prelude::*;
use primer::widget::custom::{Ctx, Event, Layout, LayoutCtx, Limits, Node, Overlay, RenderNode, Shell, Sizing, Status, Widget, mouse};

/// `child`, each message it publishes passed through `with` together with
/// the modifiers held at the event.
pub fn modified<Message: 'static>(
    child: impl Into<Element<Message>>,
    with: impl Fn(Message, Modifiers) -> Message + 'static,
) -> Modified<Message> {
    Modified { child: child.into(), with: Box::new(with) }
}

pub struct Modified<Message> {
    child: Element<Message>,
    with: Box<dyn Fn(Message, Modifiers) -> Message>,
}

// The wrapper is its child: one node, one layout, everything forwarded.
impl<Message: 'static> Widget<Message> for Modified<Message> {
    fn sizing(&self) -> Sizing {
        self.child.sizing()
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        self.child.layout(limits, ctx)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        self.child.draw(layout, ctx)
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        let modifiers = shell.modifiers();
        let child = &mut self.child;
        let (status, messages) = shell.with_message_type::<Message, _>(|inner| child.on_event(event, layout, inner));
        for message in messages {
            shell.publish((self.with)(message, modifiers));
        }
        status
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.child.mouse_interaction(layout, ctx)
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        self.child.hover(layout, ctx, regions);
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<Message>> {
        self.child.overlay(layout, ctx)
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> primer::widget::custom::Semantics {
        self.child.semantics(layout, ctx)
    }
}

impl<Message: 'static> From<Modified<Message>> for Element<Message> {
    fn from(widget: Modified<Message>) -> Self {
        Element::new(widget)
    }
}
