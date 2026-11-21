//! The stacked bar under the results' headline: one segment per category
//! that frees anything, as wide as its share, in the category's colour.
//!
//! Primer lays a row's children out by fixed sizes or fill portions; neither
//! keeps a tiny share visible, so this lays its segments out itself
//! ([`segment_widths`]): proportional to their sizes, none under a minimum
//! width, all together exactly the bar's width. Each segment is an ordinary
//! element (a coloured box in a tooltip), so hovering one names it.

use primer::prelude::*;
use primer::widget::custom::{Ctx, Event, Layout, LayoutCtx, Limits, Node, RenderNode, Shell, Sizing, Status, Widget, mouse};

/// The narrowest a segment with a non-zero share is drawn.
pub const MIN_SEGMENT: f32 = 3.0;
/// The bar's height.
pub const HEIGHT: f32 = 10.0;

/// Each of `sizes`' widths in a bar `width` wide: proportional to its size,
/// at least `min` for a non-zero size (the others then share what is left in
/// proportion), zero for a zero size, summing to `width`.
pub fn segment_widths(sizes: &[u64], width: f32, min: f32) -> Vec<f32> {
    let mut widths = vec![0.0; sizes.len()];
    let shown: Vec<usize> = (0..sizes.len()).filter(|&i| sizes[i] > 0).collect();
    if shown.is_empty() || width <= 0.0 {
        return widths;
    }
    if min * shown.len() as f32 >= width {
        for &i in &shown {
            widths[i] = width / shown.len() as f32;
        }
        return widths;
    }
    // Pin the ones a plain share would draw under `min`, and share the rest
    // among the others; pinning one can push another under, so repeat.
    let mut pinned = vec![false; sizes.len()];
    loop {
        let free: f64 = shown.iter().filter(|&&i| !pinned[i]).map(|&i| sizes[i] as f64).sum();
        let room = width - min * pinned.iter().filter(|p| **p).count() as f32;
        let mut changed = false;
        for &i in &shown {
            if pinned[i] {
                widths[i] = min;
                continue;
            }
            widths[i] = (room as f64 * sizes[i] as f64 / free) as f32;
            if widths[i] < min {
                pinned[i] = true;
                changed = true;
            }
        }
        if !changed {
            return widths;
        }
    }
}

/// A bar of segments, each `(size, element)`, laid side by side at
/// [`segment_widths`].
pub struct Stacked<Message> {
    segments: Vec<(u64, Element<Message>)>,
}

impl<Message> Stacked<Message> {
    pub fn new(segments: Vec<(u64, Element<Message>)>) -> Self {
        Stacked { segments }
    }
}

impl<Message> Widget<Message> for Stacked<Message> {
    fn sizing(&self) -> Sizing {
        Sizing::new(Length::Fill, Length::fixed(HEIGHT))
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        let size = limits.resolve(<Self as Widget<Message>>::sizing(self), Size::new(limits.max().width, HEIGHT));
        let sizes: Vec<u64> = self.segments.iter().map(|(s, _)| *s).collect();
        let widths = segment_widths(&sizes, size.width, MIN_SEGMENT);
        let mut x = 0.0;
        let children = self
            .segments
            .iter()
            .zip(widths)
            .map(|((_, child), width)| {
                let cell = Size::new(width, size.height);
                let node = child.layout(&Limits::new(cell, cell), ctx).move_to(Point::new(x, 0.0));
                x += width;
                node
            })
            .collect();
        Node::with_children(size, children)
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let children = self.segments.iter().zip(layout.children()).map(|((_, child), at)| child.draw(at, ctx)).collect();
        RenderNode::group(layout.bounds(), children)
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        for ((_, child), at) in self.segments.iter_mut().zip(layout.children()) {
            if child.on_event(event, at, shell) == Status::Captured {
                return Status::Captured;
            }
        }
        Status::Ignored
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        self.segments
            .iter()
            .zip(layout.children())
            .map(|((_, child), at)| child.mouse_interaction(at, ctx))
            .find(|i| *i != mouse::Interaction::Idle)
            .unwrap_or(mouse::Interaction::Idle)
    }

    fn hover(&self, layout: Layout<'_>, ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        for ((_, child), at) in self.segments.iter().zip(layout.children()) {
            child.hover(at, ctx, regions);
        }
    }

    fn overlay(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> Option<primer::widget::custom::Overlay<Message>> {
        self.segments.iter().zip(layout.children()).find_map(|((_, child), at)| child.overlay(at, ctx))
    }
}

impl<Message: 'static> From<Stacked<Message>> for Element<Message> {
    fn from(bar: Stacked<Message>) -> Self {
        Element::new(bar)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn shares_are_proportional() {
        let widths = segment_widths(&[100, 300, 0, 600], 500.0, MIN_SEGMENT);
        assert_eq!(widths, vec![50.0, 150.0, 0.0, 300.0]);
        // A tiny share is kept visible and the others give way.
        let widths = segment_widths(&[1, 1_000_000], 100.0, MIN_SEGMENT);
        assert_eq!(widths[0], MIN_SEGMENT);
        assert!((widths[1] - 97.0).abs() < 1e-3);
        assert_eq!(segment_widths(&[0, 0], 100.0, MIN_SEGMENT), vec![0.0, 0.0]);
    }

    proptest! {
        /// The segments fill the bar exactly, a non-zero size never draws
        /// under the minimum, a zero size draws nothing, and among those not
        /// held up to the minimum widths are proportional to sizes.
        #[test]
        fn segments_fill_the_bar(sizes in proptest::collection::vec(0u64..10_000_000, 1..7), width in 30.0f32..1200.0) {
            let widths = segment_widths(&sizes, width, MIN_SEGMENT);
            let any = sizes.iter().any(|s| *s > 0);
            let sum: f32 = widths.iter().sum();
            if any {
                prop_assert!((sum - width).abs() < 0.01, "{} != {}", sum, width);
            } else {
                prop_assert_eq!(sum, 0.0);
            }
            for (size, w) in sizes.iter().zip(&widths) {
                if *size == 0 {
                    prop_assert_eq!(*w, 0.0);
                } else {
                    prop_assert!(*w >= MIN_SEGMENT - 1e-3);
                }
            }
            let free: Vec<(u64, f32)> = sizes.iter().zip(&widths).filter(|(s, w)| **s > 0 && **w > MIN_SEGMENT + 1e-3).map(|(s, w)| (*s, *w)).collect();
            for pair in free.windows(2) {
                let (a, b) = (pair[0], pair[1]);
                let ratio = a.1 as f64 / b.1 as f64;
                let want = a.0 as f64 / b.0 as f64;
                prop_assert!((ratio - want).abs() / want < 1e-3, "{:?}", pair);
            }
        }
    }
}
