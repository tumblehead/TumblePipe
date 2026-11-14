//! The value ladder: Houdini's middle-mouse drag on a number field.
//!
//! Middle-press a number field and a column of step sizes (100, 10, 1, 0.1,
//! 0.01) opens at the pointer, on the 1 row. Moving up or down picks a
//! step; dragging left or right changes the value by that step every
//! [`NOTCH`] pixels. Releasing sets the value. Ctrl + middle-click puts the
//! field back to its default. A whole-number field leaves out the steps
//! below 1, and the field's own limits still hold. Left-mouse editing and
//! typing are the wrapped field's, unchanged.
//!
//! The maths is plain functions (tested below); [`ladder`] wraps any field
//! element with the gesture, so every number field gets it the same way.
//! The same rules, in Python, serve TumblePipe's Qt fields
//! (`tumblepipe.qt.value_ladder`).

use primer::prelude::*;
use primer::widget::custom::*;
use primer::widget::{Element, Id};

use crate::look;

/// A ladder row's height, in pixels: how far up or down picks the next step.
pub const ROW: f32 = 22.0;
/// Pixels of left/right drag per step.
pub const NOTCH: f32 = 10.0;
/// The finest step the ladder offers, as decimals: 0.01.
pub const FINEST: u32 = 2;
/// The ladder's width, in pixels.
const WIDTH: f32 = 76.0;

/// The ladder's steps, coarsest first, for a field showing `decimals`
/// decimals (0: a whole-number field, which gets no step below 1).
pub fn steps(decimals: u32) -> Vec<f64> {
    let finest = decimals.min(FINEST) as i32;
    (-2..=finest).map(|p| 10f64.powi(-p)).collect()
}

/// The row a drag starts on: the 1 step.
pub fn start_row(steps: &[f64]) -> usize {
    steps.iter().position(|s| *s == 1.0).unwrap_or(0)
}

/// The row picked `dy` pixels below the press (negative: above), starting
/// from `start`, kept on the ladder's `rows`.
pub fn row_at(start: usize, dy: f32, rows: usize) -> usize {
    let moved = (dy / ROW).round() as i64;
    (start as i64 + moved).clamp(0, rows.saturating_sub(1) as i64) as usize
}

/// `value` rounded to `decimals` decimals.
pub fn rounded(value: f64, decimals: u32) -> f64 {
    let scale = 10f64.powi(decimals.min(9) as i32);
    (value * scale).round() / scale
}

/// The value `dx` pixels of drag makes from `base` with `step`: one step per
/// whole [`NOTCH`], rounded to the field's decimals, inside its limits.
pub fn nudged(base: f64, dx: f32, step: f64, limits: (f64, f64), decimals: u32) -> f64 {
    let notches = (dx / NOTCH).trunc() as f64;
    rounded(base + notches * step, decimals).clamp(limits.0, limits.1)
}

/// What a number field is: its value now, its limits, its decimals and
/// what Ctrl + middle-click puts back.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Number {
    pub value: f64,
    pub min: f64,
    pub max: f64,
    pub decimals: u32,
    pub default: f64,
}

impl Number {
    /// The default, inside the limits.
    pub fn reset(&self) -> f64 {
        rounded(self.default, self.decimals).clamp(self.min, self.max)
    }
}

/// A middle-mouse drag in progress.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Grip {
    /// Where the press was.
    press: Point,
    /// The value the current step counts from, and the x it counts from:
    /// picking another step starts counting again where the value is.
    base: f64,
    anchor_x: f32,
    row: usize,
    /// The value now.
    value: f64,
}

impl Grip {
    pub fn new(number: &Number, press: Point) -> Self {
        let value = rounded(number.value, number.decimals).clamp(number.min, number.max);
        Self { press, base: value, anchor_x: press.x, row: start_row(&steps(number.decimals)), value }
    }

    /// The pointer at `at`: the step its height picks and the value its
    /// drag makes.
    pub fn moved(&mut self, number: &Number, at: Point) -> f64 {
        let steps = steps(number.decimals);
        let row = row_at(start_row(&steps), at.y - self.press.y, steps.len());
        if row != self.row {
            (self.row, self.base, self.anchor_x) = (row, self.value, at.x);
        }
        self.value = nudged(self.base, at.x - self.anchor_x, steps[self.row], (number.min, number.max), number.decimals);
        self.value
    }
}

#[derive(Debug, Default)]
struct State {
    grip: Option<Grip>,
}

/// `field` with the value ladder. `on_change` gets each new value while
/// dragging (and the default on Ctrl + middle-click); `on_release` is sent
/// when the drag or reset ends, to set what `on_change` drafted. `id` is
/// the ladder's own, not the field's.
pub fn ladder<M: Clone + 'static>(
    id: Id,
    field: impl Into<Element<M>>,
    number: Number,
    on_change: impl Fn(f64) -> M + 'static,
    on_release: Option<M>,
) -> Element<M> {
    Element::new(Ladder { id, field: field.into(), number, on_change: Box::new(on_change), on_release })
}

struct Ladder<M> {
    id: Id,
    field: Element<M>,
    number: Number,
    on_change: Box<dyn Fn(f64) -> M>,
    on_release: Option<M>,
}

impl<M: Clone> Ladder<M> {
    fn finish(&self, shell: &mut Shell<'_, M>) {
        if let Some(m) = &self.on_release {
            shell.publish(m.clone());
        }
    }
}

impl<M: Clone> Widget<M> for Ladder<M> {
    fn sizing(&self) -> Sizing {
        self.field.sizing()
    }

    // The field's own layout: the ladder adds nothing to it.
    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        self.field.layout(limits, ctx)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let field = self.field.draw(layout, ctx);
        let Some(grip) = ctx.store.state::<State>(&self.id).and_then(|s| s.grip) else { return field };
        let steps = steps(self.number.decimals);
        let top = grip.press.y - (start_row(&steps) as f32 + 0.5) * ROW;
        let left = grip.press.x - WIDTH / 2.0;
        let mut rows = Vec::with_capacity(steps.len());
        for (i, step) in steps.iter().enumerate() {
            let bounds = Rectangle::new(Point::new(left, top + i as f32 * ROW), Size::new(WIDTH, ROW));
            let picked = i == grip.row;
            let words = if picked { format!("{}", rounded(grip.value, self.number.decimals)) } else { format!("{step}") };
            let style = BoxStyle::solid(if picked { look::band() } else { look::panel() }).with_border(1.0, look::rule());
            let label = TextNode::new(words, bounds, 12.0, if picked { look::text() } else { look::quiet() })
                .with_align(Horizontal::Center)
                .with_valign(Vertical::Center);
            rows.push(RenderNode::panel(bounds, style, vec![RenderNode::text(label)]));
        }
        let column = Rectangle::new(Point::new(left, top), Size::new(WIDTH, ROW * steps.len() as f32));
        RenderNode::group(layout.bounds(), vec![field, RenderNode::overlay(RenderNode::group(column, rows))])
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, M>) -> Status {
        let grip = shell.store().state::<State>(&self.id).and_then(|s| s.grip);
        match (event, grip) {
            (Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Middle)), None) => {
                let Some(at) = shell.cursor().position().filter(|p| layout.bounds().contains(*p)) else { return Status::Ignored };
                if shell.modifiers().control {
                    shell.publish((self.on_change)(self.number.reset()));
                    self.finish(shell);
                    return Status::Captured;
                }
                shell.store_mut().state_mut::<State>(&self.id).grip = Some(Grip::new(&self.number, at));
                shell.store_mut().hold_pointer(true);
                shell.request_redraw();
                Status::Captured
            }
            (Event::Mouse(mouse::Event::CursorMoved { position }), Some(mut grip)) => {
                let before = grip.value;
                let value = grip.moved(&self.number, *position);
                shell.store_mut().state_mut::<State>(&self.id).grip = Some(grip);
                if value != before {
                    shell.publish((self.on_change)(value));
                }
                shell.request_redraw();
                Status::Captured
            }
            (Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Middle)), Some(_)) => {
                shell.store_mut().state_mut::<State>(&self.id).grip = None;
                shell.store_mut().hold_pointer(false);
                self.finish(shell);
                shell.request_redraw();
                Status::Captured
            }
            // The field's own middle-button handling (a paste) gives way to
            // the ladder, and nothing reaches it mid-drag.
            (Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Middle) | mouse::Event::ButtonReleased(mouse::Button::Middle)), _)
            | (_, Some(_)) => Status::Ignored,
            _ => self.field.on_event(event, layout, shell),
        }
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Interaction {
        match ctx.store.state::<State>(&self.id).and_then(|s| s.grip) {
            Some(_) => Interaction::ResizeHorizontal,
            None => self.field.mouse_interaction(layout, ctx),
        }
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        self.field.hover(layout, ctx, regions);
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<Overlay<M>> {
        self.field.overlay(layout, ctx)
    }

    fn semantics(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Semantics {
        self.field.semantics(layout, ctx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn number() -> impl Strategy<Value = Number> {
        (0u32..4, -500i64..500, 0i64..1000, any::<bool>()).prop_flat_map(|(decimals, low, span, unbounded)| {
            let (min, max) = if unbounded { (f64::MIN, f64::MAX) } else { (low as f64, (low + span) as f64) };
            let scale = 10f64.powi(decimals as i32);
            (Just(decimals), Just(min), Just(max), -100_000i64..100_000, -100_000i64..100_000).prop_map(
                move |(decimals, min, max, value, default)| Number {
                    value: (value as f64 / scale).clamp(min, max),
                    min,
                    max,
                    decimals,
                    default: default as f64 / scale,
                },
            )
        })
    }

    proptest! {
        /// The ladder is powers of ten from 100 down, coarsest first; a
        /// whole-number field gets none below 1, and none is finer than
        /// 0.01. It starts on the 1 row.
        #[test]
        fn the_steps_are_powers_of_ten(decimals in 0u32..12) {
            let steps = steps(decimals);
            prop_assert_eq!(steps[0], 100.0);
            prop_assert_eq!(steps.len(), 3 + decimals.min(FINEST) as usize);
            for pair in steps.windows(2) {
                prop_assert!((pair[0] / pair[1] - 10.0).abs() < 1e-9);
            }
            prop_assert_eq!(steps[start_row(&steps)], 1.0);
            if decimals == 0 {
                prop_assert!(steps.iter().all(|s| *s >= 1.0));
            }
        }

        /// Up picks a coarser step, down a finer one, one per row height,
        /// and the pick never leaves the ladder.
        #[test]
        fn height_picks_the_step(start in 0usize..5, dy in -400f32..400.0, rows in 1usize..6) {
            let start = start.min(rows - 1);
            let row = row_at(start, dy, rows);
            prop_assert!(row < rows);
            if dy.abs() < ROW / 2.0 {
                prop_assert_eq!(row, start);
            }
            if dy < 0.0 { prop_assert!(row <= start) } else { prop_assert!(row >= start) }
        }

        /// A drag stays inside the field's limits and on its decimals (a
        /// whole-number field stays whole); back where it started, with the
        /// same step, it is the value it started from; further right is
        /// never less.
        #[test]
        fn a_drag_moves_by_whole_steps(number in number(), path in prop::collection::vec((-300f32..300.0, -90f32..90.0), 1..12)) {
            let press = Point::new(400.0, 300.0);
            let mut grip = Grip::new(&number, press);
            let scale = 10f64.powi(number.decimals as i32);
            for (dx, dy) in &path {
                let value = grip.moved(&number, Point::new(press.x + dx, press.y + dy));
                prop_assert!(number.min <= value && value <= number.max, "{} outside {}..{}", value, number.min, number.max);
                if value > number.min && value < number.max {
                    prop_assert!(((value * scale).round() - value * scale).abs() < 1e-6, "{} off {} decimals", value, number.decimals);
                }
            }
            // A drag that never changes row: right of the press is never less,
            // and back at the press is where it began.
            let mut grip = Grip::new(&number, press);
            let mut last = f64::MIN;
            for dx in [-120.0f32, -35.0, 0.0, 9.0, 10.0, 55.0, 200.0] {
                let value = grip.moved(&number, Point::new(press.x + dx, press.y));
                prop_assert!(value >= last);
                last = value;
            }
            prop_assert_eq!(grip.moved(&number, press), number.value);
        }

        /// One notch at the 1 step, unclamped, moves the value by exactly 1;
        /// a step picked mid-drag counts on from where the value is.
        #[test]
        fn a_notch_is_one_step(value in -1000i64..1000, decimals in 0u32..3, notches in -20i32..20) {
            let number = Number { value: value as f64, min: f64::MIN, max: f64::MAX, decimals, default: 0.0 };
            let press = Point::new(0.0, 0.0);
            let mut grip = Grip::new(&number, press);
            let dx = notches as f32 * NOTCH + NOTCH / 2.0 * (notches.signum() as f32);
            prop_assert_eq!(grip.moved(&number, Point::new(dx, 0.0)), value as f64 + notches as f64);
            // One row up: 10s, counting from the value reached.
            let reached = value as f64 + notches as f64;
            prop_assert_eq!(grip.moved(&number, Point::new(dx, -ROW)), reached);
            prop_assert_eq!(grip.moved(&number, Point::new(dx + NOTCH, -ROW)), reached + 10.0);
        }

        /// Ctrl + middle-click's value is the default, inside the limits.
        #[test]
        fn reset_is_the_default(number in number()) {
            let reset = number.reset();
            prop_assert!(number.min <= reset && reset <= number.max);
            if number.min <= number.default && number.default <= number.max {
                prop_assert_eq!(reset, rounded(number.default, number.decimals));
            }
        }
    }
}
