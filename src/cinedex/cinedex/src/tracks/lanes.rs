//! Where the tracks' rows are: each row has a height of its own, so a row's
//! top is the heights above it, and the bottom edge of a row's header is
//! what a drag makes it taller or lower by.

use super::ROW;

/// The lowest and tallest a track's row is dragged: a line of its clips'
/// names at least, and at most a clip's picture large enough to read.
pub const ROW_MIN: f32 = 24.0;
pub const ROW_MAX: f32 = 240.0;
/// How near a header's bottom edge (pixels, either side) a press takes it.
pub const GRIP: f32 = 4.0;

/// `height` as a row may have it: whole pixels between [`ROW_MIN`] and
/// [`ROW_MAX`], the default ([`ROW`]) for what is no number.
pub fn row_height(height: f32) -> f32 {
    if height.is_finite() { height.round().clamp(ROW_MIN, ROW_MAX) } else { ROW }
}

/// The top of row `row` of rows `heights` tall, top first, in pixels below
/// the first row's top.
pub fn row_top(heights: &[f32], row: usize) -> f32 {
    heights.iter().take(row).sum()
}

/// The row `y` pixels below the first row's top is in.
pub fn row_at(heights: &[f32], y: f32) -> Option<usize> {
    if y < 0.0 {
        return None;
    }
    let mut bottom = 0.0;
    heights.iter().position(|h| {
        bottom += h;
        y < bottom
    })
}

/// The row whose bottom edge is within [`GRIP`] of `y`: the one a press
/// there resizes. Between two rows it is the upper one's, whose edge it is.
pub fn edge_at(heights: &[f32], y: f32) -> Option<usize> {
    let mut bottom = 0.0;
    heights.iter().position(|h| {
        bottom += h;
        (y - bottom).abs() <= GRIP
    })
}

/// The height of a row that was `from` tall when its edge was taken, the
/// pointer now `dy` pixels lower.
pub fn dragged_height(from: f32, dy: f32) -> f32 {
    row_height(from + dy)
}

/// How soon a second press on the same thing makes a double-click (as
/// Primer's lists count it).
pub const DOUBLE_CLICK: std::time::Duration = std::time::Duration::from_millis(400);

/// Whether a press on `this` at `now` is the second of a double-click:
/// `last` was a press on the same thing within [`DOUBLE_CLICK`].
pub fn completes_double_click<T: PartialEq>(last: Option<(T, std::time::Instant)>, this: T, now: std::time::Instant) -> bool {
    last.is_some_and(|(what, when)| what == this && now.saturating_duration_since(when) <= DOUBLE_CLICK)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// Two presses make a double-click only on the same thing and soon
        /// enough; a press with none before it never does.
        #[test]
        fn a_double_click_is_two_presses_on_one_thing(first in 0u8..4, second in 0u8..4, apart in 0u64..1200) {
            let t0 = std::time::Instant::now();
            let now = t0 + std::time::Duration::from_millis(apart);
            prop_assert!(!completes_double_click(None, second, now));
            prop_assert_eq!(completes_double_click(Some((first, t0)), second, now), first == second && apart <= 400);
        }
    }

    fn heights() -> impl Strategy<Value = Vec<f32>> {
        prop::collection::vec((ROW_MIN..=ROW_MAX).prop_map(row_height), 0..9)
    }

    proptest! {
        /// A row's height is always one a row may have, and one it may have
        /// is kept as it is.
        #[test]
        fn a_height_is_kept_within_reason(height in prop_oneof![-1e6f32..1e6, Just(f32::NAN), Just(f32::INFINITY)]) {
            let h = row_height(height);
            prop_assert!((ROW_MIN..=ROW_MAX).contains(&h));
            prop_assert_eq!(row_height(h), h);
            if (ROW_MIN..=ROW_MAX).contains(&height) {
                prop_assert!((h - height).abs() <= 0.5);
            }
        }

        /// The rows tile the space under the ruler: every point in it is in
        /// exactly the row whose top and height span it, whatever heights
        /// the rows have. (With one height for all, `y / ROW`, a row made
        /// taller would take presses meant for the rows under it.)
        #[test]
        fn every_point_is_in_the_row_drawn_there(heights in heights(), y in -50f32..2500.0) {
            let total: f32 = heights.iter().sum();
            match row_at(&heights, y) {
                Some(row) => {
                    let top = row_top(&heights, row);
                    prop_assert!(y >= top && y < top + heights[row]);
                }
                None => prop_assert!(y < 0.0 || y >= total),
            }
        }

        /// Dragging a header's bottom edge changes that row's height by the
        /// pointer's distance, within the limits, and no other row's: the
        /// rows above stay where they are and the rows below only move.
        #[test]
        fn a_dragged_edge_resizes_only_its_row(heights in heights().prop_filter("a row", |h| !h.is_empty()), row in 0usize..9, dy in -400f32..400.0) {
            let row = row % heights.len();
            let edge = row_top(&heights, row) + heights[row];
            prop_assert_eq!(edge_at(&heights, edge), Some(row));
            let mut after = heights.clone();
            after[row] = dragged_height(heights[row], dy);
            prop_assert!((after[row] - (heights[row] + dy).round().clamp(ROW_MIN, ROW_MAX)).abs() < 1e-3);
            for r in 0..=row {
                prop_assert_eq!(row_top(&after, r), row_top(&heights, r));
            }
            for r in row + 1..heights.len() {
                prop_assert!((row_top(&after, r) - row_top(&heights, r) - (after[row] - heights[row])).abs() < 1e-2);
            }
            // The edge follows the pointer while the height is in range.
            if (ROW_MIN..=ROW_MAX).contains(&(heights[row] + dy)) {
                prop_assert!((row_top(&after, row) + after[row] - (edge + dy)).abs() <= 0.5);
            }
        }

        /// An edge is taken only near a row's bottom: the middle of a row
        /// is never a resize.
        #[test]
        fn the_middle_of_a_row_is_no_edge(heights in heights(), row in 0usize..9, along in 0.3f32..0.7) {
            prop_assume!(!heights.is_empty());
            let row = row % heights.len();
            let y = row_top(&heights, row) + heights[row] * along;
            prop_assert_eq!(edge_at(&heights, y), None);
        }
    }

    /// The limits in pixels, as written: a row is never under 24 or over
    /// 240 tall, whatever height is asked for or dragged to.
    #[test]
    #[allow(clippy::float_cmp)]
    fn a_row_is_24_to_240_pixels_tall() {
        for (asked, tall) in [(-5.0, 24.0), (23.4, 24.0), (24.0, 24.0), (100.4, 100.0), (240.0, 240.0), (241.0, 240.0), (1.0e9, 240.0)] {
            assert_eq!(row_height(asked), tall, "asked for {asked}");
        }
        assert_eq!(dragged_height(200.0, 500.0), 240.0);
        assert_eq!(dragged_height(60.0, -500.0), 24.0);
    }
}
