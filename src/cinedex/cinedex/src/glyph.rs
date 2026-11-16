//! The transport's glyphs (play, pause, step, go to an end, loop), drawn
//! rather than set in a font: the UI font has no media symbols, and drawn
//! shapes stay crisp at any scale.

use primer::prelude::*;
use primer::widget::custom::Cursor;

use crate::look;

/// A transport glyph.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Glyph {
    Play,
    Pause,
    StepBack,
    StepForward,
    ToStart,
    ToEnd,
}

/// The side of the square a glyph is drawn in, in logical pixels.
pub(crate) const SIZE: f32 = 12.0;

/// A glyph drawn in `colour`.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Drawn {
    pub(crate) glyph: Glyph,
    pub(crate) colour: Color,
}

fn triangle(frame: &mut Frame, x0: f32, x1: f32, top: f32, bottom: f32, colour: Color) {
    // Pointing right when x1 > x0, left when x1 < x0.
    let tip = Path::new(|p| {
        p.move_to(Point::new(x0, top));
        p.line_to(Point::new(x1, (top + bottom) / 2.0));
        p.line_to(Point::new(x0, bottom));
        p.close();
    });
    frame.fill(&tip, colour);
}

impl<Message> Program<Message> for Drawn {
    type State = ();

    fn draw(&self, _state: &(), frame: &mut Frame, bounds: Rectangle, _cursor: Cursor) {
        let s = bounds.width.min(bounds.height);
        let (x, y) = ((bounds.width - s) / 2.0, (bounds.height - s) / 2.0);
        let (top, bottom) = (y + s * 0.1, y + s * 0.9);
        let bar = |frame: &mut Frame, left: f32, wide: f32| {
            frame.fill_rectangle(Point::new(x + left * s, top), Size::new(wide * s, bottom - top), self.colour);
        };
        match self.glyph {
            Glyph::Play => triangle(frame, x + s * 0.18, x + s * 0.9, top, bottom, self.colour),
            Glyph::Pause => {
                bar(frame, 0.18, 0.24);
                bar(frame, 0.58, 0.24);
            }
            Glyph::StepBack => {
                bar(frame, 0.1, 0.16);
                triangle(frame, x + s * 0.9, x + s * 0.32, top, bottom, self.colour);
            }
            Glyph::StepForward => {
                triangle(frame, x + s * 0.1, x + s * 0.68, top, bottom, self.colour);
                bar(frame, 0.74, 0.16);
            }
            Glyph::ToStart => {
                bar(frame, 0.04, 0.14);
                triangle(frame, x + s * 0.58, x + s * 0.18, top, bottom, self.colour);
                triangle(frame, x + s * 0.98, x + s * 0.58, top, bottom, self.colour);
            }
            Glyph::ToEnd => {
                triangle(frame, x + s * 0.02, x + s * 0.42, top, bottom, self.colour);
                triangle(frame, x + s * 0.42, x + s * 0.82, top, bottom, self.colour);
                bar(frame, 0.82, 0.14);
            }
        }
    }
}

/// A transport button showing `glyph`, out of keyboard focus (the player's
/// keys stay with the window), with `tip` (what it does, and its key).
pub(crate) fn button<Message: Clone + 'static>(name: &str, glyph: Glyph, tip: &str, message: Message) -> Element<Message> {
    let drawn = canvas(Drawn { glyph, colour: look::text() }).width(Length::fixed(SIZE)).height(Length::fixed(SIZE));
    let b = look::button(Id::new(format!("transport {name}")), drawn)
        .padding(Padding::from([5.0, 9.0]))
        .focusable(false)
        .on_press(move || message.clone());
    tooltip(Id::new(format!("transport {name} tip")), b, tip.to_owned()).into()
}
