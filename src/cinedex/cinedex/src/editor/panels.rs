//! The editor's two resizable panels: the bins beside the viewer, and the
//! timeline under them (or under the board). Drag a handle to resize, double-
//! click it to fold its panel to a strip, click the strip to bring it back;
//! the sizes are this user's (`layout_prefs`).
//!
//! The handles stay out of keyboard focus (`focusable(false)`): Primer makes
//! a split's handle a Tab stop by default, and a focused handle would take
//! the arrow keys and Space, so the player's keys would stop working after a
//! drag. Every split in Cinedex is built here, and the tests hold the rest
//! of `src/` to it.
//!
//! The tracks' scrollable (`tracks_view`) is a focus stop of Primer's own
//! while its tracks overflow it, and Primer 0.15 has no way to turn that
//! off; it is not built here.

use primer::prelude::*;

use crate::layout_prefs::Layout;
use crate::tracks;

/// The narrowest the bins can be dragged, and the narrowest the viewer
/// beside them can be squeezed.
pub(super) const BINS_MIN: f32 = 180.0;
pub(super) const VIEWER_MIN: f32 = 240.0;
/// The lowest the viewer (or the board) above the timeline, and the
/// timeline itself, can be dragged.
pub(super) const ABOVE_TIMELINE_MIN: f32 = 160.0;
pub(super) const TIMELINE_MIN: f32 = 60.0;
/// The clip fields' row above the tracks, for the timeline's height before
/// the user sets one.
pub(super) const CLIP_FIELDS_HEIGHT: f32 = 34.0;

/// The timeline's height before the user drags it: the clip fields and
/// `tracks` tracks (one at least).
pub(super) fn fitted_timeline_height(tracks: usize) -> f32 {
    CLIP_FIELDS_HEIGHT + tracks::RULER + tracks.max(1) as f32 * tracks::ROW
}

/// The bins beside the viewer, at this user's width, folded or not.
pub(super) fn bins_split<M: 'static>(
    bins: impl Into<Element<M>>,
    viewer: impl Into<Element<M>>,
    layout: &Layout,
    on_resize: impl Fn(SplitChange) -> M + 'static,
) -> Split<M> {
    split(Id::new("bins split"), bins, viewer)
        .size(SplitSize::First(layout.bins_width))
        .collapsed(layout.bins_collapsed)
        .collapsible(SplitSide::First)
        .min_sizes(BINS_MIN, VIEWER_MIN)
        .focusable(false)
        .on_resize(on_resize)
}

/// What is `above` over the timeline, `height` tall (this user's, else
/// [`fitted_timeline_height`]), folded or not.
pub(super) fn timeline_split<M: 'static>(
    above: impl Into<Element<M>>,
    timeline: impl Into<Element<M>>,
    height: f32,
    collapsed: bool,
    on_resize: impl Fn(SplitChange) -> M + 'static,
) -> Split<M> {
    split(Id::new("timeline split"), above, timeline)
        .axis(Axis::Vertical)
        .size(SplitSize::Second(height))
        .collapsed(collapsed)
        .collapsible(SplitSide::Second)
        .min_sizes(ABOVE_TIMELINE_MIN, TIMELINE_MIN)
        .focusable(false)
        .on_resize(on_resize)
}

#[cfg(test)]
mod tests {
    // Spans are sums and differences of the same few floats.
    #![allow(clippy::float_cmp)]

    use std::path::Path;

    use primer::core::measure::{TextLayout, TextMeasurer};
    use primer::core::render::TextSpan;
    use primer::widget::custom::{Ctx, LayoutCtx, Limits, Node, Role, Semantics, StateStore, Widget};
    use primer::widget::custom::{Cursor, Layout as Placed};
    use proptest::prelude::*;
    use serde_json::{Value, json};

    use super::*;

    /// The panes here are spaces: nothing is measured.
    struct NoText;

    impl TextMeasurer for NoText {
        fn measure_text(&self, _: &str, _: f32, _: Option<f32>) -> Size {
            unreachable!("the panes hold no text")
        }
        fn measure_styled(&self, _: &str, _: &[TextSpan], _: f32, _: Option<f32>) -> Size {
            unreachable!("the panes hold no text")
        }
        fn layout_text(&self, _: &str, _: f32, _: Option<f32>) -> TextLayout {
            unreachable!("the panes hold no text")
        }
    }

    fn pane() -> Element<()> {
        space(Length::Fill, Length::Fill).into()
    }

    /// The editor's body as `view` builds it: the bins split over the
    /// timeline split, panes standing in for the bins, viewer and tracks.
    fn body(layout: &Layout, tracks: usize) -> Split<()> {
        let height = layout.timeline_height.unwrap_or_else(|| fitted_timeline_height(tracks));
        let middle = bins_split(pane(), pane(), layout, |_| ());
        timeline_split(middle, pane(), height, layout.timeline_collapsed, |_| ())
    }

    fn lay(split: &Split<()>, store: &StateStore, w: f32, h: f32) -> Node {
        let theme = Theme::dark();
        split.layout(&Limits::new(Size::ZERO, Size::new(w, h)), LayoutCtx { measurer: &NoText, store, theme: &theme })
    }

    fn splitters(tree: &Semantics, out: &mut Vec<Semantics>) {
        if tree.role == Role::Splitter {
            out.push(tree.clone());
        }
        for child in &tree.children {
            splitters(child, out);
        }
    }

    /// The semantics tree of the laid-out body: what a screen reader and
    /// the focus order see.
    fn tree(split: &Split<()>, node: &Node, store: &StateStore) -> Semantics {
        let theme = Theme::dark();
        let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store, measurer: &NoText, visible: Rectangle::EVERYWHERE };
        split.semantics(Placed::new(node), ctx)
    }

    /// Every handle in the body.
    fn handles(split: &Split<()>, node: &Node, store: &StateStore) -> Vec<Semantics> {
        let mut out = Vec::new();
        splitters(&tree(split, node, store), &mut out);
        out
    }

    /// Every way the panels can be (either folded or not, a size or none):
    /// both handles are there and neither is a focus stop.
    #[test]
    fn the_handles_never_take_the_keyboard() {
        let store = StateStore::new();
        for bins_collapsed in [false, true] {
            for timeline_collapsed in [false, true] {
                for timeline_height in [None, Some(300.0)] {
                    let layout = Layout { bins_width: 260.0, bins_collapsed, timeline_height, timeline_collapsed };
                    let split = body(&layout, 3);
                    let node = lay(&split, &store, 1600.0, 900.0);
                    let found = handles(&split, &node, &store);
                    assert_eq!(found.len(), 2, "{layout:?}: both handles");
                    assert!(found.iter().all(|h| !h.is_focusable()), "{layout:?}: a handle is a Tab stop");
                }
            }
        }
    }

    /// The Rust files of `src/`, but this one (its tests name the call).
    fn sources(dir: &Path, out: &mut Vec<(String, String)>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                sources(&path, out);
            } else if path.extension().is_some_and(|e| e == "rs") {
                out.push((path.display().to_string(), std::fs::read_to_string(&path).unwrap()));
            }
        }
    }

    /// Every Primer `split(` in Cinedex's source carries `.focusable(false)`
    /// in its chain (up to the `;` or the closing brace that ends it): a
    /// split added anywhere without it fails here, whether or not it is
    /// built through this module.
    #[test]
    fn every_split_in_the_source_stays_out_of_focus() {
        let call = concat!("spl", "it(");
        let mut files = Vec::new();
        sources(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"), &mut files);
        let mut found = 0;
        for (path, source) in &files {
            let mut from = 0;
            while let Some(at) = source[from..].find(call).map(|i| i + from) {
                from = at + call.len();
                // `x.split(`, `rsplit(`, `str::split(`: not Primer's.
                let before = source[..at].chars().next_back();
                if before.is_some_and(|c| c.is_alphanumeric() || c == '_' || c == '.' || c == ':') {
                    continue;
                }
                let rest = &source[at..];
                let end = [rest.find(';'), rest.find("\n}")].into_iter().flatten().min().unwrap_or(rest.len());
                let chain = &rest[..end];
                let line = source[..at].matches('\n').count() + 1;
                assert!(chain.contains(".focusable(false)"), "{path}:{line}: a split without .focusable(false)");
                found += 1;
            }
        }
        // The scan sees the two it guards.
        assert!(found >= 2, "found {found} splits");
    }

    /// Any JSON a layout file might hold for a field.
    fn field() -> impl Strategy<Value = Value> {
        prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::from),
            any::<f64>().prop_map(|f| serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)),
            (-1.0e5f64..1.0e5).prop_map(|f| serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)),
            (0.0f64..5000.0).prop_map(|f| serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)),
        ]
    }

    /// A window's side: tiny (below a handle), small, or any up to 8K.
    fn side(max: f32) -> impl Strategy<Value = f32> {
        prop_oneof![0.0f32..20.0, 20.0f32..600.0, 0.0f32..=max]
    }

    /// The four lengths a window shows: bins width, viewer width, viewer
    /// height, timeline height; and the two handles' thicknesses (the
    /// bins', the timeline's).
    #[derive(Debug, Clone, Copy, PartialEq)]
    struct Spans {
        bins: f32,
        viewer_w: f32,
        viewer_h: f32,
        timeline: f32,
        bins_handle: f32,
        timeline_handle: f32,
    }

    fn spans(split: &Split<()>, store: &StateStore, w: f32, h: f32) -> Spans {
        let node = lay(split, store, w, h);
        let (above, timeline) = (&node.children()[0], &node.children()[1]);
        let (bins, viewer) = (&above.children()[0], &above.children()[1]);
        // The timeline split's handle is a child of the body's own node; the
        // bins split's is inside the pane above it.
        let top = tree(split, &node, store);
        let outer = top.children.iter().find(|c| c.role == Role::Splitter).expect("the timeline handle");
        let mut inside = Vec::new();
        for child in top.children.iter().filter(|c| c.role != Role::Splitter) {
            splitters(child, &mut inside);
        }
        assert_eq!(inside.len(), 1, "one handle inside the pane above the timeline");
        let inner = &inside[0];
        Spans {
            bins: bins.bounds().width,
            viewer_w: viewer.bounds().width,
            viewer_h: above.bounds().height,
            timeline: timeline.bounds().height,
            bins_handle: inner.bounds.width,
            timeline_handle: outer.bounds.height,
        }
    }

    /// Primer's handle and strip thicknesses (private to it), read off a
    /// laid-out split with room to spare.
    fn thicknesses() -> (f32, f32) {
        let store = StateStore::new();
        let open = Layout { bins_width: 260.0, bins_collapsed: false, timeline_height: Some(200.0), timeline_collapsed: false };
        let folded = Layout { bins_collapsed: true, ..open };
        let divider = spans(&body(&open, 1), &store, 2000.0, 2000.0).bins_handle;
        let strip = spans(&body(&folded, 1), &store, 2000.0, 2000.0).bins_handle;
        (divider, strip)
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(512))]

        /// Whatever a layout file says and whatever the window's size, from
        /// smaller than a handle to 8K: no length is negative or past the
        /// window; the four lengths and the handles fill it; the viewer,
        /// the bins, the view above the timeline and the timeline keep
        /// their minimums whenever the window can hold both of a pair; a
        /// saved size that fits is laid out as saved; a folded panel is a
        /// strip; and a window shrunk and grown back lays out as before.
        #[test]
        fn saved_sizes_always_lay_out_sanely(
            bins in field(), bins_collapsed in field(), timeline in field(), timeline_collapsed in field(),
            tracks in 0usize..40,
            w in side(7680.0), h in side(4320.0),
            small_w in side(7680.0), small_h in side(4320.0),
        ) {
            let (divider, strip) = thicknesses();
            prop_assert!(divider > 0.0 && strip > 0.0);
            let layout = Layout::from_json(&json!({
                "bins_width": bins, "bins_collapsed": bins_collapsed,
                "timeline_height": timeline, "timeline_collapsed": timeline_collapsed,
            }));
            let height = layout.timeline_height.unwrap_or_else(|| fitted_timeline_height(tracks));
            let store = StateStore::new();
            let split = body(&layout, tracks);
            let s = spans(&split, &store, w, h);
            let eps = 1e-3 * (1.0 + w.max(h));
            for (name, v, max) in [("bins", s.bins, w), ("viewer width", s.viewer_w, w), ("viewer height", s.viewer_h, h), ("timeline", s.timeline, h)] {
                prop_assert!(v.is_finite() && v >= 0.0 && v <= max + eps, "{} {} in a {}x{} window, {:?}", name, v, w, h, layout);
            }
            prop_assert!((s.bins + s.bins_handle + s.viewer_w - w).abs() <= eps, "{:?} across {}", s, w);
            prop_assert!((s.viewer_h + s.timeline_handle + s.timeline - h).abs() <= eps, "{:?} down {}", s, h);

            if layout.bins_collapsed {
                prop_assert_eq!(s.bins, 0.0);
                prop_assert!((s.bins_handle - strip.min(w)).abs() <= eps, "folded bins: strip {} of {}", s.bins_handle, w);
            } else {
                prop_assert!((s.bins_handle - divider.min(w)).abs() <= eps);
                if w - divider >= BINS_MIN + VIEWER_MIN {
                    prop_assert!(s.viewer_w >= VIEWER_MIN - eps && s.bins >= BINS_MIN - eps, "{:?} in {}", s, w);
                    if layout.bins_width <= w - divider - VIEWER_MIN && layout.bins_width >= BINS_MIN {
                        prop_assert!((s.bins - layout.bins_width).abs() <= eps, "saved bins {} laid out {}", layout.bins_width, s.bins);
                    }
                }
            }
            if layout.timeline_collapsed {
                prop_assert_eq!(s.timeline, 0.0);
                prop_assert!((s.timeline_handle - strip.min(h)).abs() <= eps, "folded timeline: strip {} of {}", s.timeline_handle, h);
            } else {
                prop_assert!((s.timeline_handle - divider.min(h)).abs() <= eps);
                if h - divider >= ABOVE_TIMELINE_MIN + TIMELINE_MIN {
                    prop_assert!(s.viewer_h >= ABOVE_TIMELINE_MIN - eps && s.timeline >= TIMELINE_MIN - eps, "{:?} in {}", s, h);
                    if height <= h - divider - ABOVE_TIMELINE_MIN && height >= TIMELINE_MIN {
                        prop_assert!((s.timeline - height).abs() <= eps, "saved timeline {} laid out {}", height, s.timeline);
                    }
                }
            }

            // Shrunk, then back: the same spans, the layout untouched.
            spans(&split, &store, small_w, small_h);
            prop_assert_eq!(spans(&split, &store, w, h), s);
            prop_assert_eq!(spans(&body(&layout, tracks), &StateStore::new(), w, h), s);
        }
    }

    /// The default layout in a common window: the bins at their default
    /// width, the timeline fitted to the tracks.
    #[test]
    fn the_default_layout_opens_as_designed() {
        let s = spans(&body(&Layout::default(), 2), &StateStore::new(), 1600.0, 900.0);
        assert_eq!(s.bins, crate::layout_prefs::BINS_WIDTH);
        assert_eq!(s.timeline, fitted_timeline_height(2));
    }
}
