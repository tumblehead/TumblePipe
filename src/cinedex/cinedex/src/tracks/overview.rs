//! The strip under the tracks: the whole timeline in little, and on it the
//! part the tracks show (the thumb). It is the tracks' horizontal
//! scrollbar: the thumb is as wide as the part shown is of the whole; drag
//! it to scroll through time; drag one of its ends to show more or fewer
//! frames (the other end stays); press the strip off the thumb to page a
//! view's width towards the press.
//!
//! The strip spans the same pixels as the tracks' time (right of the
//! names), so the thumb's edges are over the frames the tracks start and
//! end at when the whole timeline is shown. It is drawn from the editor's
//! `TimelineView` every frame, so the wheel, a zoom and a fit move it too.
//!
//! The held thumb lives in the window's state store, which drops every
//! state no draw reads (`StateStore::prune`): the draw reads it, else the
//! grip was gone after the first redraw and the drag went dead after one
//! move (2026-11-22 RFE: "the bottom bar is not responsive").

use primer::prelude::*;
use primer::widget::custom::{Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Node, RenderNode, Shell, Sizing, Status, Widget, mouse};
use th_edit::document::Frame as EditFrame;

use super::{HEADER, Nodes, Paint};
use crate::look;

/// The strip's height.
pub const HEIGHT: f32 = 14.0;
/// The thumb is never narrower than this, to stay grabbable.
const THUMB_MIN: f32 = 12.0;
/// How far in from each end of the thumb (and out past it) a press takes
/// that end rather than the whole thumb.
const END_GRIP: f32 = 5.0;

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

/// The edit frame at pixel `x` of the strip: the inverse of [`thumb`]'s
/// left edge where the thumb is not held at an end. Never before frame 0.
pub fn first_at(x: f32, span: usize, width: f32) -> f64 {
    let room = f64::from((width - HEADER).max(1.0));
    (f64::from(x - HEADER) / room * span.max(1) as f64).max(0.0)
}

/// What part of the thumb a press holds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Grip {
    /// The whole thumb, this many pixels right of its left edge.
    Body(f32),
    /// Its left end: the first frame shown moves, the last stays.
    Head,
    /// Its right end: the last frame shown moves, the first stays.
    Tail,
}

/// What a press at strip pixel `x` does.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Press {
    Hold(Grip),
    /// Off the thumb: a page earlier (false) or later (true).
    Page(bool),
}

/// What a press at pixel `x` of the strip, its thumb at `x0..x1`, takes.
/// An end grip reaches [`END_GRIP`] in and out from the end, never more
/// than a quarter of the thumb in, so a narrow thumb keeps a body to drag.
pub fn pressed(x: f32, (x0, x1): (f32, f32)) -> Press {
    let inside = END_GRIP.min((x1 - x0) / 4.0);
    if (x0 - END_GRIP..x0 + inside).contains(&x) {
        Press::Hold(Grip::Head)
    } else if (x1 - inside..=x1 + END_GRIP).contains(&x) {
        Press::Hold(Grip::Tail)
    } else if (x0..x1).contains(&x) {
        Press::Hold(Grip::Body(x - x0))
    } else {
        Press::Page(x >= x1)
    }
}

/// The first frame shown after a page of a view `first` to `first +
/// shown` earlier or `later`: a view's width on.
pub fn paged(first: f64, shown: usize, later: bool) -> f64 {
    if later { first + shown as f64 } else { (first - shown as f64).max(0.0) }
}

/// The frames shown (first, how many) after `grip`, an end of the thumb,
/// is dragged to strip pixel `x`: the other end stays where it is; never
/// fewer than `least` frames, never past either end of the timeline.
/// `Body` changes nothing here (it scrolls, [`first_at`]).
pub fn ended(grip: Grip, x: f32, span: usize, first: f64, shown: usize, least: usize, width: f32) -> (f64, usize) {
    let least = least.clamp(1, span.max(1));
    let at = first_at(x, span, width).min(span as f64);
    match grip {
        Grip::Body(_) => (first, shown),
        Grip::Head => {
            let end = (first + shown as f64).min(span as f64);
            let start = at.min(end - least as f64).max(0.0);
            (start, ((end - start).round() as usize).clamp(least, span))
        }
        Grip::Tail => {
            let end = at.max(first + least as f64).min(span as f64);
            (first, ((end - first).round() as usize).clamp(least, span))
        }
    }
}

pub struct Overview<Message> {
    /// The timeline's length in frames.
    pub span: usize,
    /// The first frame the tracks show, and how many.
    pub first: f64,
    pub shown: usize,
    /// The fewest frames the tracks zoom in to.
    pub least: usize,
    /// Where there are clips, on any track: each `(at, end)`.
    pub clips: Vec<(EditFrame, EditFrame)>,
    pub playhead: usize,
    /// The thumb was dragged, or the strip paged: the first frame to show.
    pub on_scroll: fn(f64) -> Message,
    /// An end of the thumb was dragged: the first frame to show, and how
    /// many.
    pub on_frames: fn(f64, usize) -> Message,
}

/// The thumb is held, and by what.
#[derive(Debug, Default)]
pub struct Held(Option<Grip>);

impl Held {
    pub fn grip(&self) -> Option<Grip> {
        self.0
    }
}

impl<Message> Overview<Message> {
    fn thumb(&self, width: f32) -> (f32, f32) {
        thumb(self.span, self.first, self.shown, width)
    }

    /// What `grip` held at strip pixel `x` sends.
    fn dragged(&self, grip: Grip, x: f32, width: f32) -> Message {
        match grip {
            Grip::Body(grab) => (self.on_scroll)(first_at(x - grab, self.span, width)),
            end => {
                let (first, shown) = ended(end, x, self.span, self.first, self.shown, self.least, width);
                (self.on_frames)(first, shown)
            }
        }
    }

    /// What a pointer `event` does to the strip laid out in `bounds`.
    pub(crate) fn update(&self, state: &mut Held, event: &Event, bounds: Rectangle, cursor: Cursor) -> (Status, Option<Message>) {
        match event {
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(local) = cursor.position_relative_to(bounds) else { return (Status::Ignored, None) };
                match pressed(local.x, self.thumb(bounds.width)) {
                    Press::Hold(grip) => {
                        state.0 = Some(grip);
                        (Status::Captured, None)
                    }
                    Press::Page(later) => (Status::Captured, Some((self.on_scroll)(paged(self.first, self.shown, later)))),
                }
            }
            Event::Mouse(mouse::Event::CursorMoved { position }) => match state.0 {
                Some(grip) => (Status::Captured, Some(self.dragged(grip, position.x - bounds.x, bounds.width))),
                None => (Status::Ignored, None),
            },
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => match state.0.take() {
                Some(_) => (Status::Captured, None),
                None => (Status::Ignored, None),
            },
            _ => (Status::Ignored, None),
        }
    }

    /// The cursor over the strip: the resize arrows on the thumb's ends,
    /// the hand on its body (closed while held).
    fn interaction(&self, held: Option<Grip>, bounds: Rectangle, cursor: Cursor) -> mouse::Interaction {
        match held {
            Some(Grip::Body(_)) => return mouse::Interaction::Grabbing,
            Some(_) => return mouse::Interaction::ResizeHorizontal,
            None => {}
        }
        let Some(local) = cursor.position_relative_to(bounds) else { return mouse::Interaction::Idle };
        match pressed(local.x, self.thumb(bounds.width)) {
            Press::Hold(Grip::Body(_)) => mouse::Interaction::Grab,
            Press::Hold(_) => mouse::Interaction::ResizeHorizontal,
            Press::Page(_) => mouse::Interaction::Idle,
        }
    }

    fn paint(&self, p: &mut impl Paint, held: bool, w: f32, h: f32) {
        p.rect(Point::new(0.0, 0.0), Size::new(w, h), look::panel());
        let room = (w - HEADER).max(1.0);
        let x = |frame: f64| HEADER + (frame / self.span.max(1) as f64) as f32 * room;
        // The track the thumb runs in.
        p.rect(Point::new(HEADER, 2.0), Size::new(room, h - 4.0), look::seam());
        for &(at, end) in &self.clips {
            let (x0, x1) = (x(at as f64), x(end as f64).min(w));
            p.rect(Point::new(x0, h / 2.0 - 1.0), Size::new((x1 - x0).max(1.0), 2.0), look::tick());
        }
        let (x0, x1) = self.thumb(w);
        p.rect(Point::new(x0, 1.0), Size::new(x1 - x0, h - 2.0), if held { look::on() } else { look::band() });
        super::outline(
            p,
            Rectangle { x: x0, y: 1.0, width: x1 - x0, height: h - 2.0 },
            1.0,
            if held { look::text() } else { look::quiet() },
        );
        // The ends' grips: short bars inside each end.
        if x1 - x0 >= 4.0 * END_GRIP {
            for gx in [x0 + 3.0, x1 - 4.0] {
                p.rect(Point::new(gx, 4.0), Size::new(1.0, h - 8.0), look::quiet());
            }
        }
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

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        // Read every draw: it keeps the held thumb alive past the store's
        // prune (see the module's docs).
        let held = ctx.store.state::<Held>(&self.id).and_then(Held::grip).is_some();
        let mut nodes = Nodes::new(bounds);
        self.overview.paint(&mut nodes, held, bounds.width, bounds.height);
        nodes.finish()
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        let bounds = layout.bounds();
        let cursor = shell.cursor();
        let state = shell.store_mut().state_mut::<Held>(&self.id);
        let was = state.0.is_some();
        let (status, message) = self.overview.update(state, event, bounds, cursor);
        let now = state.0.is_some();
        if was != now {
            // A drag may go on past the window's edge.
            shell.store_mut().hold_pointer(now);
            shell.request_redraw();
        }
        if let Some(message) = message {
            shell.publish(message);
        }
        status
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        let held = ctx.store.state::<Held>(&self.id).and_then(Held::grip);
        self.overview.interaction(held, layout.bounds(), ctx.cursor)
    }

    fn hover(&self, layout: Layout<'_>, _ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        // The cursor changes over the thumb's ends and body.
        let bounds = layout.bounds();
        let (x0, x1) = self.overview.thumb(bounds.width);
        let inside = END_GRIP.min((x1 - x0) / 4.0);
        for (from, to) in [(x0 - END_GRIP, x0 + inside), (x0 + inside, x1 - inside), (x1 - inside, x1 + END_GRIP)] {
            regions.boundary(Rectangle { x: bounds.x + from, y: bounds.y, width: (to - from).max(0.0), height: bounds.height });
        }
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

    /// What the strip sent: a scroll to a first frame, or the frames shown.
    #[derive(Debug, Clone, Copy, PartialEq)]
    enum Sent {
        Scroll(f64),
        Frames(f64, usize),
    }

    fn strip(span: usize, first: f64, shown: usize) -> Overview<Sent> {
        Overview { span, first, shown, least: 8, clips: Vec::new(), playhead: 0, on_scroll: Sent::Scroll, on_frames: Sent::Frames }
    }

    fn scroll(sent: Option<Sent>) -> f64 {
        match sent {
            Some(Sent::Scroll(first)) => first,
            other => panic!("{other:?} is not a scroll"),
        }
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

        /// A press is on exactly one of: the head, the tail, the body, or
        /// the strip before or after the thumb; the body is always there to
        /// take, however narrow the thumb.
        #[test]
        fn a_press_takes_one_part_and_the_body_is_always_there(x0 in 56f32..1500.0, wide in 12f32..1500.0, x in 0f32..3200.0) {
            let x1 = x0 + wide;
            match pressed(x, (x0, x1)) {
                Press::Hold(Grip::Head) => prop_assert!(x < x0 + wide / 4.0 + 1e-3 && x >= x0 - END_GRIP),
                Press::Hold(Grip::Tail) => prop_assert!(x > x1 - wide / 4.0 - 1e-3 && x <= x1 + END_GRIP),
                Press::Hold(Grip::Body(grab)) => prop_assert!((x0..x1).contains(&x) && (grab - (x - x0)).abs() < 1e-3),
                Press::Page(later) => {
                    prop_assert!(!(x0 - END_GRIP..=x1 + END_GRIP).contains(&x));
                    prop_assert_eq!(later, x > x1);
                }
            }
            let Press::Hold(Grip::Body(grab)) = pressed((x0 + x1) / 2.0, (x0, x1)) else {
                return Err(TestCaseError::fail("the thumb's middle is not its body"));
            };
            prop_assert!((grab - (x1 - x0) / 2.0).abs() < 1e-3);
        }

        /// An end dragged moves only that end: the other stays, the frames
        /// shown are never under the least nor past the timeline, and the
        /// end lands on the frame under the pointer where the limits allow.
        #[test]
        fn an_end_dragged_keeps_the_other_end(
            span in 50usize..5000, first in 0f64..1.0, shown in 0f64..1.0, x in -200f32..2400.0, head in any::<bool>(), width in 400f32..2000.0,
        ) {
            let least = 8;
            let shown = ((span as f64 * shown) as usize).clamp(least, span);
            let first = (first * (span - shown) as f64).round();
            let (grip, end) = if head { (Grip::Head, first + shown as f64) } else { (Grip::Tail, first) };
            let (new_first, new_shown) = ended(grip, x, span, first, shown, least, width);
            prop_assert!(new_shown >= least && new_shown <= span);
            prop_assert!(new_first >= 0.0 && new_first + new_shown as f64 <= span as f64 + 0.5, "{} + {} past {}", new_first, new_shown, span);
            let at = first_at(x, span, width).min(span as f64);
            if head {
                prop_assert!((new_first + new_shown as f64 - end).abs() <= 0.5, "the tail moved");
                if at <= end - least as f64 {
                    prop_assert!((new_first - at).abs() < 1e-6, "the head is not under the pointer");
                }
            } else {
                prop_assert_eq!(new_first, end, "the head moved");
                if at >= first + least as f64 {
                    prop_assert!((new_first + new_shown as f64 - at).abs() <= 0.5, "the tail is not under the pointer");
                }
            }
        }

        /// The thumb pressed and dragged scrolls by the pixels dragged, as
        /// frames of the whole timeline, on every move until it is let go;
        /// a press off it pages a view's width towards the press.
        #[test]
        fn dragging_the_thumb_scrolls_by_what_is_dragged(
            span in 100usize..5000, first in 0f64..0.5, on in 0.3f32..0.7, press_at in 0f32..1.0, moves in prop::collection::vec(-300f32..300.0, 1..6),
        ) {
            let shown = span / 4;
            let first = first * span as f64;
            let bounds = Rectangle { x: 30.0, y: 500.0, width: 1056.0, height: HEIGHT };
            let strip = strip(span, first, shown);
            let (x0, x1) = thumb(span, first, shown, bounds.width);
            let frames_a_pixel = span as f64 / 1000.0;
            let at = |x: f32| Cursor::Available(Point::new(bounds.x + x, bounds.y + 5.0));
            // Off the thumb: a page.
            let off = HEADER + press_at * 1000.0;
            if !(x0 - END_GRIP..=x1 + END_GRIP).contains(&off) {
                let mut held = Held::default();
                let (status, sent) = strip.update(&mut held, &Event::Mouse(LEFT), bounds, at(off));
                prop_assert_eq!(status, Status::Captured);
                prop_assert_eq!(scroll(sent), paged(first, shown, off > x1));
                prop_assert_eq!(held.grip(), None, "a page holds nothing");
            }
            // On the body: a drag.
            let mut held = Held::default();
            let x = x0 + on * (x1 - x0);
            prop_assert_eq!(strip.update(&mut held, &Event::Mouse(LEFT), bounds, at(x)), (Status::Captured, None));
            let mut dx = 0.0;
            for step in moves {
                dx += step;
                let moved = Point::new(bounds.x + x + dx, bounds.y - 40.0);
                let (_, sent) = strip.update(&mut held, &Event::Mouse(mouse::Event::CursorMoved { position: moved }), bounds, Cursor::Available(moved));
                let expected = (first + f64::from(dx) * frames_a_pixel).max(0.0);
                prop_assert!((scroll(sent) - expected).abs() < frames_a_pixel + 0.5, "{:?} not {}", sent, expected);
            }
            let moved = Point::new(bounds.x + x + dx, bounds.y);
            prop_assert_eq!(strip.update(&mut held, &Event::Mouse(UP), bounds, Cursor::Available(moved)), (Status::Captured, None));
            let (status, sent) = strip.update(&mut held, &Event::Mouse(mouse::Event::CursorMoved { position: moved }), bounds, Cursor::Available(moved));
            prop_assert_eq!((status, sent), (Status::Ignored, None));
        }
    }

    /// The thumb's right end dragged out shows more frames from the same
    /// first frame.
    #[test]
    fn the_tail_dragged_out_shows_more() {
        let bounds = Rectangle { x: 0.0, y: 0.0, width: HEADER + 1000.0, height: HEIGHT };
        let strip = strip(1000, 100.0, 200);
        let (_, x1) = thumb(1000, 100.0, 200, bounds.width);
        let mut held = Held::default();
        let at = |x: f32| Cursor::Available(Point::new(x, 5.0));
        strip.update(&mut held, &Event::Mouse(LEFT), bounds, at(x1 - 1.0));
        assert_eq!(held.grip(), Some(Grip::Tail));
        let to = Point::new(x1 + 100.0, 5.0);
        let (_, sent) = strip.update(&mut held, &Event::Mouse(mouse::Event::CursorMoved { position: to }), bounds, Cursor::Available(to));
        assert_eq!(sent, Some(Sent::Frames(100.0, 300)));
    }
}
