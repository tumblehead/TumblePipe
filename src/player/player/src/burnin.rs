//! Burn-ins: the shot and its frame written over the picture, in its corners,
//! as review movies carry them: the sequence and shot top left, the take top
//! right, the frame rate bottom left and the frame bottom right.
//!
//! Text only: the renderer paints text above the video, but anything else a
//! canvas draws (a backing box) below it. Each line is drawn over a dark
//! outline (four copies a pixel off each way), so it reads on light frames
//! as well as dark ones.

use primer::Horizontal as H;
use primer::Vertical as V;
use primer::prelude::*;
use primer::widget::custom::Cursor;

/// Space between a burn-in and the picture's edge, in logical pixels.
const MARGIN: f32 = 10.0;

/// What the burn-ins say, and the size of the frame they are on.
pub struct Burnin {
    pub frame_size: (f32, f32),
    pub top_left: String,
    pub top_right: String,
    pub bottom_left: String,
    pub bottom_right: String,
}

/// Where the video shows a frame `frame` pixels big inside `bounds`: as big as
/// fits, centred, its shape kept. `video.wgsl` fits it the same way.
pub fn fitted(frame: (f32, f32), bounds: Size) -> Rectangle {
    let (w, h) = (frame.0.max(1.0), frame.1.max(1.0));
    let fit = (bounds.width / w).min(bounds.height / h);
    let size = Size::new(w * fit, h * fit);
    Rectangle::new(Point::new((bounds.width - size.width) * 0.5, (bounds.height - size.height) * 0.5), size)
}

/// A rate as people say it: `24 fps`, `23.976 fps`.
pub fn rate_label((num, den): (i32, i32)) -> String {
    if den != 0 && num % den == 0 {
        return format!("{} fps", num / den);
    }
    let fps = format!("{:.3}", f64::from(num) / f64::from(den));
    format!("{} fps", fps.trim_end_matches('0').trim_end_matches('.'))
}

/// The frame burn-in for frame `index` of a take `frames` long: its place in
/// the take, after the frame's own number when the media has numbers (an EXR
/// sequence starting at `first_frame`): `1046 (46 / 68)`, `46 / 68`.
pub fn frame_text(first_frame: Option<i64>, index: usize, frames: usize) -> String {
    match first_frame {
        Some(first) => format!("{} ({} / {frames})", first + index as i64, index + 1),
        None => format!("{} / {frames}", index + 1),
    }
}

/// The burn-ins' text size for a picture `height` logical pixels tall: a
/// small share of it, so they stay out of the way on a small window and
/// readable on a large one.
pub fn text_size(height: f32) -> f32 {
    (height * 0.028).clamp(11.0, 22.0)
}

impl<Message> Program<Message> for Burnin {
    type State = ();

    fn draw(&self, _state: &(), frame: &mut Frame, bounds: Rectangle, _cursor: Cursor) {
        let picture = fitted(self.frame_size, bounds.size());
        let size = text_size(picture.height);
        let line = size * 1.4;
        let inner = Rectangle::new(
            Point::new(picture.x + MARGIN, picture.y + MARGIN),
            Size::new((picture.width - 2.0 * MARGIN).max(0.0), (picture.height - 2.0 * MARGIN).max(0.0)),
        );
        let top = Rectangle::new(inner.position(), Size::new(inner.width, line));
        let bottom = Rectangle::new(Point::new(inner.x, inner.y + inner.height - line), Size::new(inner.width, line));
        let lines = [(&self.top_left, top, H::Left, V::Top), (&self.top_right, top, H::Right, V::Top), (&self.bottom_left, bottom, H::Left, V::Bottom), (&self.bottom_right, bottom, H::Right, V::Bottom)];
        for (words, place, align, valign) in lines {
            for (dx, dy) in [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)] {
                let outline = Rectangle::new(Point::new(place.x + dx, place.y + dy), place.size());
                frame.fill_text_in(words.clone(), outline, align, valign, size, Color::from_rgba8(0, 0, 0, 200));
            }
            frame.fill_text_in(words.clone(), place, align, valign, size, Color::from_rgb8(235, 235, 235));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn rates_read_as_people_say_them() {
        assert_eq!(rate_label((24, 1)), "24 fps");
        assert_eq!(rate_label((48, 2)), "24 fps");
        assert_eq!(rate_label((24000, 1001)), "23.976 fps");
        assert_eq!(rate_label((25, 2)), "12.5 fps");
    }

    proptest! {
        /// The fitted picture stays inside the bounds, keeps the frame's
        /// shape, fills them along one side, and is centred.
        #[test]
        fn the_picture_fits_centred(w in 1f32..8000.0, h in 1f32..8000.0, bw in 1f32..4000.0, bh in 1f32..4000.0) {
            let r = fitted((w, h), Size::new(bw, bh));
            let slack = 1e-3 * bw.max(bh);
            prop_assert!(r.x >= -slack && r.y >= -slack && r.x + r.width <= bw + slack && r.y + r.height <= bh + slack);
            prop_assert!((r.width * h - r.height * w).abs() <= 1e-3 * r.width * h.max(1.0), "the shape is kept");
            prop_assert!((r.width - bw).abs() <= slack || (r.height - bh).abs() <= slack, "fills one side");
            prop_assert!((r.x * 2.0 + r.width - bw).abs() <= slack && (r.y * 2.0 + r.height - bh).abs() <= slack, "centred");
        }

        /// A numbered frame shows its number and its place; an unnumbered
        /// one its place, so both agree on where in the take it is.
        #[test]
        fn frame_texts_agree_on_the_place(first in -100i64..100_000, frames in 1usize..10_000, at in 0f64..1.0) {
            let index = ((at * frames as f64) as usize).min(frames - 1);
            let place = format!("{} / {frames}", index + 1);
            prop_assert_eq!(frame_text(Some(first), index, frames), format!("{} ({place})", first + index as i64));
            prop_assert_eq!(frame_text(None, index, frames), place);
        }

        /// The text grows with the picture, within its limits.
        #[test]
        fn text_grows_with_the_picture(a in 0f32..3000.0, b in 0f32..3000.0) {
            let (small, large) = (a.min(b), a.max(b));
            prop_assert!(text_size(small) <= text_size(large));
            prop_assert!((11.0..=22.0).contains(&text_size(a)));
        }
    }
}
