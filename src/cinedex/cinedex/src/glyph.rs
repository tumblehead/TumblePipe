//! The transport's glyphs (play, pause, step, edit point, go to an end),
//! drawn rather than set in a font: the UI font has no media symbols.
//!
//! Each is rasterised once into a small image, not drawn on a canvas: any
//! canvas geometry in a window makes Primer draw that whole window's scene
//! pass at 4x multisampling (see `tracks::Paint`), and the transport is
//! always on screen. The image is three times the size it shows at, so it
//! stays crisp on a scaled display.

use primer::ImageHandle;
use primer::prelude::*;

use crate::look;

/// A transport glyph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) enum Glyph {
    Play,
    Pause,
    /// One frame back: an arrow left, up against a bar.
    StepBack,
    StepForward,
    /// The edit point before: a bar, an arrow left towards it.
    PrevCut,
    NextCut,
    ToStart,
    ToEnd,
}

/// The side of the square a glyph is shown in, in logical pixels.
pub(crate) const SIZE: f32 = 12.0;

/// The image's pixels per logical pixel.
const SCALE: u32 = 3;

/// Samples per image pixel, each way, for the slanted edges.
const SAMPLES: u32 = 4;

/// The glyphs are drawn between these heights of the unit square.
const TOP: f32 = 0.1;
const BOTTOM: f32 = 0.9;

/// A part of a glyph, in the unit square, [`TOP`] to [`BOTTOM`] tall.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Shape {
    /// A bar from `left` to `right`.
    Bar(f32, f32),
    /// An arrow head: its upright side at `base`, its tip at `tip`, half
    /// way up. It points right when `tip > base`, left when under it.
    Arrow { base: f32, tip: f32 },
}

impl Shape {
    fn mirrored(self) -> Shape {
        match self {
            Shape::Bar(left, right) => Shape::Bar(1.0 - right, 1.0 - left),
            Shape::Arrow { base, tip } => Shape::Arrow { base: 1.0 - base, tip: 1.0 - tip },
        }
    }

    fn covers(self, x: f32, y: f32) -> bool {
        if !(TOP..BOTTOM).contains(&y) {
            return false;
        }
        match self {
            Shape::Bar(left, right) => (left..right).contains(&x),
            Shape::Arrow { base, tip } => {
                // How far from the base towards the tip, 0 to 1: the arrow
                // is as tall there as it has left to go.
                let along = (x - base) / (tip - base);
                let half = (BOTTOM - TOP) / 2.0;
                (0.0..1.0).contains(&along) && (y - (TOP + half)).abs() <= half * (1.0 - along)
            }
        }
    }
}

/// What `glyph` is made of. A glyph that goes back is its forward one
/// mirrored.
fn shapes(glyph: Glyph) -> Vec<Shape> {
    let mirror = |forward: Glyph| shapes(forward).into_iter().map(Shape::mirrored).collect();
    match glyph {
        Glyph::Play => vec![Shape::Arrow { base: 0.18, tip: 0.9 }],
        Glyph::Pause => vec![Shape::Bar(0.18, 0.42), Shape::Bar(0.58, 0.82)],
        Glyph::StepForward => vec![Shape::Bar(0.1, 0.26), Shape::Arrow { base: 0.38, tip: 0.9 }],
        Glyph::NextCut => vec![Shape::Arrow { base: 0.1, tip: 0.68 }, Shape::Bar(0.74, 0.9)],
        Glyph::ToEnd => vec![Shape::Arrow { base: 0.02, tip: 0.42 }, Shape::Arrow { base: 0.42, tip: 0.82 }, Shape::Bar(0.82, 0.96)],
        Glyph::StepBack => mirror(Glyph::StepForward),
        Glyph::PrevCut => mirror(Glyph::NextCut),
        Glyph::ToStart => mirror(Glyph::ToEnd),
    }
}

/// How much of each pixel of a `side` x `side` image `glyph` covers, row
/// by row, 0 to 255.
fn coverage(glyph: Glyph, side: u32) -> Vec<u8> {
    let shapes = shapes(glyph);
    let n = side * SAMPLES;
    let mut out = Vec::with_capacity((side * side) as usize);
    for py in 0..side {
        for px in 0..side {
            let mut hit = 0u32;
            for sy in 0..SAMPLES {
                for sx in 0..SAMPLES {
                    let x = ((px * SAMPLES + sx) as f32 + 0.5) / n as f32;
                    let y = ((py * SAMPLES + sy) as f32 + 0.5) / n as f32;
                    hit += u32::from(shapes.iter().any(|s| s.covers(x, y)));
                }
            }
            out.push((hit * 255 / (SAMPLES * SAMPLES)) as u8);
        }
    }
    out
}

/// `glyph` in `colour` as an image, made once per glyph and colour.
pub(crate) fn image_of(glyph: Glyph, colour: Color) -> ImageHandle {
    type Made = Vec<((Glyph, [u8; 4]), ImageHandle)>;
    static MADE: std::sync::Mutex<Made> = std::sync::Mutex::new(Vec::new());
    let key = (glyph, colour.to_srgba8());
    let mut made = MADE.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some((_, handle)) = made.iter().find(|(k, _)| *k == key) {
        return handle.clone();
    }
    let side = SIZE as u32 * SCALE;
    let [r, g, b, a] = key.1;
    let rgba = coverage(glyph, side).into_iter().flat_map(|c| [r, g, b, (u32::from(a) * u32::from(c) / 255) as u8]).collect();
    let handle = ImageHandle::from_rgba(side, side, rgba);
    made.push((key, handle.clone()));
    handle
}

/// A transport button showing `glyph`, out of keyboard focus (the player's
/// keys stay with the window), with `tip` (what it does, and its key).
pub(crate) fn button<Message: Clone + 'static>(name: &str, glyph: Glyph, tip: &str, message: Message) -> Element<Message> {
    control(name, glyph, tip, Some(message))
}

/// [`button`], greyed and doing nothing when it has no `message`.
pub(crate) fn control<Message: Clone + 'static>(name: &str, glyph: Glyph, tip: &str, message: Option<Message>) -> Element<Message> {
    let colour = if message.is_some() { look::text() } else { look::quiet() };
    let drawn = image(image_of(glyph, colour)).width(Length::fixed(SIZE)).height(Length::fixed(SIZE));
    let mut b = look::button(Id::new(format!("transport {name}")), drawn).padding(Padding::from([5.0, 9.0])).focusable(false);
    if let Some(message) = message {
        b = b.on_press(move || message.clone());
    }
    tooltip(Id::new(format!("transport {name} tip")), b, tip.to_owned()).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    const ALL: [Glyph; 8] =
        [Glyph::Play, Glyph::Pause, Glyph::StepBack, Glyph::StepForward, Glyph::PrevCut, Glyph::NextCut, Glyph::ToStart, Glyph::ToEnd];

    fn glyph() -> impl Strategy<Value = Glyph> {
        prop::sample::select(ALL.to_vec())
    }

    proptest! {
        /// Every glyph draws something, inside its square with a clear
        /// margin above and below, and no two glyphs draw alike.
        #[test]
        fn every_glyph_is_its_own_shape_inside_the_square(a in glyph(), b in glyph(), side in prop::sample::select(vec![12u32, 24, 36])) {
            let drawn = coverage(a, side);
            prop_assert_eq!(drawn.len(), (side * side) as usize);
            let inked = drawn.iter().filter(|&&c| c == 255).count();
            prop_assert!(inked * 8 > drawn.len(), "{:?} is nearly empty", a);
            prop_assert!(inked * 10 < drawn.len() * 9, "{:?} fills the square", a);
            let rows = |from: u32, to: u32| (from..to).flat_map(|y| (0..side).map(move |x| (y * side + x) as usize));
            prop_assert!(rows(0, side / 12).all(|i| drawn[i] == 0), "{:?} touches the top", a);
            prop_assert!(rows(side - side / 12, side).all(|i| drawn[i] == 0), "{:?} touches the bottom", a);
            if a != b {
                let other = coverage(b, side);
                let differ = drawn.iter().zip(&other).filter(|(x, y)| x.abs_diff(**y) > 127).count();
                prop_assert!(differ * 20 > drawn.len(), "{:?} and {:?} draw alike", a, b);
            }
        }

        /// A glyph that goes back is the one that goes forward, mirrored,
        /// pixel for pixel; play and pause mirror onto themselves or not
        /// at all (play points right).
        #[test]
        fn back_is_forward_mirrored(pair in prop::sample::select(vec![
            (Glyph::StepBack, Glyph::StepForward), (Glyph::PrevCut, Glyph::NextCut), (Glyph::ToStart, Glyph::ToEnd), (Glyph::Pause, Glyph::Pause),
        ]), side in prop::sample::select(vec![12u32, 36])) {
            let (back, forward) = (coverage(pair.0, side), coverage(pair.1, side));
            for y in 0..side {
                for x in 0..side {
                    let (here, there) = (back[(y * side + x) as usize], forward[(y * side + side - 1 - x) as usize]);
                    prop_assert!(here.abs_diff(there) <= 255 / (SAMPLES * SAMPLES) as u8, "{:?} at {},{}: {} against {}", pair.0, x, y, here, there);
                }
            }
            // Play points right: more of it is in its left half.
            let play = coverage(Glyph::Play, side);
            let half = |left: bool| (0..side * side).filter(|i| (i % side < side / 2) == left).map(|i| u32::from(play[i as usize])).sum::<u32>();
            prop_assert!(half(true) > half(false));
        }
    }
}
