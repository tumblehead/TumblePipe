//! The mark of a track's colour: a stripe down the left of its name.

use primer::prelude::*;

use super::{Paint, RowView};

/// How wide the stripe is.
pub(super) const STRIPE: f32 = 4.0;

/// Where the stripe of a row `tall` high that starts at `top` is: down the
/// left edge of the names' column, inside the row (not over the line
/// between two rows).
pub(super) fn stripe(top: f32, tall: f32) -> Rectangle {
    Rectangle { x: 0.0, y: top, width: STRIPE, height: (tall - 1.0).max(0.0) }
}

/// Paint `row`'s stripe, when the track has a colour. The spare layer is
/// no track yet and has none.
pub(super) fn paint(p: &mut impl Paint, row: &RowView, top: f32) {
    if let (Some(color), false) = (row.color, row.spare) {
        let r = stripe(top, row.height);
        p.rect(Point::new(r.x, r.y), Size::new(r.width, r.height), color);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tracks::{H, HEADER, Path, Stroke, V};
    use primer::ImageHandle;
    use proptest::prelude::*;
    use th_edit::document::TrackRef;

    /// A painter that keeps the rectangles it is given.
    #[derive(Default)]
    struct Rects(Vec<(Rectangle, Color)>);

    impl Paint for Rects {
        fn rect(&mut self, top_left: Point, size: Size, color: Color) {
            self.0.push((Rectangle::new(top_left, size), color));
        }
        fn hatch(&mut self, _: Rectangle, _: Color) {}
        fn fill(&mut self, _: &Path, _: Color) {}
        fn stroke(&mut self, _: &Path, _: Stroke) {}
        fn text(&mut self, _: String, _: Rectangle, _: H, _: V, _: f32, _: Color) {}
        fn image(&mut self, _: &ImageHandle, _: Rectangle) {}
        fn raise(&mut self) {}
    }

    proptest! {
        /// A track with a colour gets one stripe of exactly that colour,
        /// inside its row and inside the names' column; a track without,
        /// and the spare layer whatever it says, gets nothing.
        #[test]
        fn a_coloured_track_gets_one_stripe_in_its_row(
            top in 0f32..2000.0, tall in 24f32..240.0, rgb in any::<[u8; 3]>(), coloured in any::<bool>(), spare in any::<bool>(),
        ) {
            let color = Color::from_rgb8(rgb[0], rgb[1], rgb[2]);
            let row = RowView {
                track: TrackRef::video(0),
                name: "V1".into(),
                clips: Vec::new(),
                height: tall,
                solo: false,
                spare,
                color: coloured.then_some(color),
            };
            let mut seen = Rects::default();
            paint(&mut seen, &row, top);
            if coloured && !spare {
                prop_assert_eq!(seen.0.len(), 1);
                let (r, painted) = seen.0[0];
                prop_assert_eq!(painted, color);
                prop_assert!(r.x >= 0.0 && r.x + r.width <= HEADER && r.width >= 2.0);
                prop_assert!(r.y >= top && r.y + r.height < top + tall && r.height >= tall - 2.0);
            } else {
                prop_assert!(seen.0.is_empty());
            }
        }
    }
}
