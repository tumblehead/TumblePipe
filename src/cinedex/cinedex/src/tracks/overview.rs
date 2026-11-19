//! The strip under the tracks: the whole timeline in little, and on it the
//! part the tracks show (the thumb). It is the tracks' horizontal
//! scrollbar: drag the thumb to scroll through time; a press off the thumb
//! brings its middle under the pointer, and the drag goes on from there.
//!
//! The strip spans the same pixels as the tracks' time (right of the
//! names), so the thumb's edges are over the frames the tracks start and
//! end at when the whole timeline is shown.

use primer::prelude::*;
use primer::widget::custom::{Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Node, RenderNode, Shell, Sizing, Status, Widget, mouse};
use th_edit::document::Frame as EditFrame;

use super::{HEADER, Nodes, Paint};
use crate::look;

/// The strip's height.
pub const HEIGHT: f32 = 12.0;
/// The thumb is never narrower than this, to stay grabbable.
const THUMB_MIN: f32 = 12.0;

/// The pixels (from the strip's left) of the thumb: the frames `first` to
/// `first + shown` of a timeline `span` frames long on a strip `width`
/// wide. At least [`THUMB_MIN`] wide, and inside the strip's time.
pub fn thumb(span: usize, first: f64, shown: usize, width: f32) -> (f32, f32) {
    let room = (width - HEADER).max(1.0);
    let scale = f64::from(room) / span.max(1) as f64;
    let wide = ((shown as f64 * scale) as f32).clamp(THUMB_MIN.min(room), room);
    let x0 = ((first * scale) as f32).clamp(0.0, room - wide);
    (HEADER + x0, HEADER + x0 + wide)
}

/// The first frame shown when the thumb's left edge is at pixel `x` of
/// the strip: the inverse of [`thumb`] where the thumb is not held at an
/// end. Never before frame 0.
pub fn first_at(x: f32, span: usize, width: f32) -> f64 {
    let room = f64::from((width - HEADER).max(1.0));
    (f64::from(x - HEADER) / room * span.max(1) as f64).max(0.0)
}

pub struct Overview<Message> {
    /// The timeline's length in frames.
    pub span: usize,
    /// The first frame the tracks show, and how many.
    pub first: f64,
    pub shown: usize,
    /// Where there are clips, on any track: each `(at, end)`.
    pub clips: Vec<(EditFrame, EditFrame)>,
    pub playhead: usize,
    /// The thumb was dragged: the first frame to show now.
    pub on_scroll: fn(f64) -> Message,
}

/// The thumb is held: how far right of its left edge.
#[derive(Debug, Default)]
pub struct Held(Option<f32>);

impl<Message> Overview<Message> {
    /// What a pointer `event` does to the strip laid out in `bounds`.
    pub(crate) fn update(&self, state: &mut Held, event: &Event, bounds: Rectangle, cursor: Cursor) -> (Status, Option<Message>) {
        match event {
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(local) = cursor.position_relative_to(bounds) else { return (Status::Ignored, None) };
                let (x0, x1) = thumb(self.span, self.first, self.shown, bounds.width);
                if (x0..x1).contains(&local.x) {
                    state.0 = Some(local.x - x0);
                    return (Status::Captured, None);
                }
                // Off the thumb: its middle comes under the pointer.
                let grip = (x1 - x0) / 2.0;
                state.0 = Some(grip);
                (Status::Captured, Some((self.on_scroll)(first_at(local.x - grip, self.span, bounds.width))))
            }
            Event::Mouse(mouse::Event::CursorMoved { position }) => match state.0 {
                Some(grip) => (Status::Captured, Some((self.on_scroll)(first_at(position.x - bounds.x - grip, self.span, bounds.width)))),
                None => (Status::Ignored, None),
            },
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => match state.0.take() {
                Some(_) => (Status::Captured, None),
                None => (Status::Ignored, None),
            },
            _ => (Status::Ignored, None),
        }
    }

    fn paint(&self, p: &mut impl Paint, w: f32, h: f32) {
        p.rect(Point::new(0.0, 0.0), Size::new(w, h), look::panel());
        let room = (w - HEADER).max(1.0);
        let x = |frame: f64| HEADER + (frame / self.span.max(1) as f64) as f32 * room;
        for &(at, end) in &self.clips {
            let (x0, x1) = (x(at as f64), x(end as f64).min(w));
            p.rect(Point::new(x0, h / 2.0 - 1.0), Size::new((x1 - x0).max(1.0), 2.0), look::tick());
        }
        let (x0, x1) = thumb(self.span, self.first, self.shown, w);
        p.rect(Point::new(x0, 1.0), Size::new(x1 - x0, h - 2.0), look::band());
        super::outline(p, Rectangle { x: x0, y: 1.0, width: x1 - x0, height: h - 2.0 }, 1.0, look::quiet());
        p.rect(Point::new(x(self.playhead as f64), 0.0), Size::new(1.0, h), look::now());
    }
}

/// [`Overview`] as a widget, its held thumb kept under `id`.
pub struct OverviewView<Message> {
    id: Id,
    overview: Overview<Message>,
}

pub fn overview_view<Message: 'static>(id: Id, overview: Overview<Message>) -> OverviewView<Message> {
    OverviewView { id, overview }
}

impl<Message> Widget<Message> for OverviewView<Message> {
    fn sizing(&self) -> Sizing {
        Sizing { width: Length::Fill, height: Length::fixed(HEIGHT) }
    }

    fn layout(&self, limits: &Limits, _ctx: LayoutCtx<'_>) -> Node {
        Node::new(limits.resolve(self.sizing(), Size::ZERO))
    }

    fn draw(&self, layout: Layout<'_>, _ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        let mut nodes = Nodes::new(bounds);
        self.overview.paint(&mut nodes, bounds.width, bounds.height);
        nodes.finish()
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        let bounds = layout.bounds();
        let cursor = shell.cursor();
        let state = shell.store_mut().state_mut::<Held>(&self.id);
        let (status, message) = self.overview.update(state, event, bounds, cursor);
        if let Some(message) = message {
            shell.publish(message);
        }
        status
    }
}

impl<Message: 'static> From<OverviewView<Message>> for Element<Message> {
    fn from(view: OverviewView<Message>) -> Self {
        Element::new(view)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    const LEFT: mouse::Event = mouse::Event::ButtonPressed(mouse::Button::Left);
    const UP: mouse::Event = mouse::Event::ButtonReleased(mouse::Button::Left);

    fn strip(span: usize, first: f64, shown: usize) -> Overview<f64> {
        Overview { span, first, shown, clips: Vec::new(), playhead: 0, on_scroll: |first| first }
    }

    proptest! {
        /// The thumb lies inside the strip's time, is as wide as the part
        /// shown is of the whole (never under 12 pixels), and starts where
        /// the first frame shown is, unless that would push it off the end.
        #[test]
        fn the_thumb_is_the_part_shown(span in 1usize..5000, first in 0f64..1.0, shown in 0f64..1.0, width in 200f32..2000.0) {
            let shown = ((span as f64 * shown) as usize).clamp(1, span);
            let first = first * (span - shown) as f64;
            let (x0, x1) = thumb(span, first, shown, width);
            let room = width - HEADER;
            prop_assert!(HEADER <= x0 && x0 < x1 && x1 <= width + 0.01, "{} to {}", x0, x1);
            let wide = shown as f32 / span as f32 * room;
            prop_assert!((x1 - x0 - wide.max(12.0)).abs() < 0.01, "{} wide, not {}", x1 - x0, wide);
            let left = HEADER + (first / span as f64) as f32 * room;
            prop_assert!((x0 - left.min(width - (x1 - x0))).abs() < 0.01);
            // And back: the thumb's left edge names the first frame shown.
            if wide >= 12.0 {
                prop_assert!((first_at(x0, span, width) - first).abs() < span as f64 / f64::from(room) + 1e-3);
            }
        }

        /// The thumb pressed and dragged scrolls by the pixels dragged, as
        /// frames of the whole timeline; a press off it first brings its
        /// middle under the pointer. Nothing is sent once it is let go.
        #[test]
        fn dragging_the_thumb_scrolls_by_what_is_dragged(
            span in 100usize..5000, first in 0f64..0.5, on in 0.05f32..0.95, press_at in 0f32..1.0, dx in -300f32..300.0, off in any::<bool>(),
        ) {
            let shown = span / 4;
            let first = first * span as f64;
            let bounds = Rectangle { x: 30.0, y: 500.0, width: 1056.0, height: HEIGHT };
            let strip = strip(span, first, shown);
            let (x0, x1) = thumb(span, first, shown, bounds.width);
            let frames_a_pixel = span as f64 / 1000.0;
            let mut held = Held::default();
            let at = |x: f32| Cursor::Available(Point::new(bounds.x + x, bounds.y + 5.0));
            let x = if off { HEADER + press_at * 1000.0 } else { x0 + on * (x1 - x0) };
            let (status, sent) = strip.update(&mut held, &Event::Mouse(LEFT), bounds, at(x));
            prop_assert_eq!(status, Status::Captured);
            let start = if (x0..x1).contains(&x) {
                prop_assert_eq!(sent, None);
                first
            } else {
                // The thumb's middle is under the press.
                let middle = (f64::from(x - HEADER) * frames_a_pixel - shown as f64 / 2.0).max(0.0);
                prop_assert!((sent.unwrap() - middle).abs() < 0.01 * span as f64 / 100.0 + 0.5, "{:?} not {}", sent, middle);
                sent.unwrap()
            };
            let moved = Point::new(bounds.x + x + dx, bounds.y - 40.0);
            let (_, sent) = strip.update(&mut held, &Event::Mouse(mouse::Event::CursorMoved { position: moved }), bounds, Cursor::Available(moved));
            let expected = (start + f64::from(dx) * frames_a_pixel).max(0.0);
            // From an unclamped start: a press off the thumb near the left end starts at 0.
            if start > 0.0 {
                prop_assert!((sent.unwrap() - expected).abs() < frames_a_pixel + 0.5, "{:?} not {}", sent, expected);
            }
            prop_assert_eq!(strip.update(&mut held, &Event::Mouse(UP), bounds, Cursor::Available(moved)), (Status::Captured, None));
            let (status, sent) = strip.update(&mut held, &Event::Mouse(mouse::Event::CursorMoved { position: moved }), bounds, Cursor::Available(moved));
            prop_assert_eq!((status, sent), (Status::Ignored, None));
        }
    }
}
