//! How many edit frames the tracks' width spans: the timeline's scale.
//!
//! It is fitted to the cut (its length and a second past it) only when asked:
//! when the editor opens an edit, on View > Fit Timeline (F), and when the
//! first clip goes into an empty edit. An edit that changes the cut (a move,
//! a trim, a slip, a roll, a fade, an undo) never refits it: the clips keep
//! their size on screen and the ruler keeps its scale, so what was dragged
//! stays under the pointer. A cut that grows past the span widens it, once,
//! when the change lands (a drag only changes the cut when it is let go, so
//! never mid-drag); a cut that shrinks leaves it as it was (2026-11-16 RFE:
//! the span was the cut's length, so it refitted on every trim and the clip
//! jumped from a long bar to a short one and back under the pointer).

/// The tracks' span, and whether the cut was empty when it was last seen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TimelineView {
    span: usize,
    empty: bool,
}

/// The span that fits a cut `length` frames long, `tail` frames (a second)
/// past it to park the playhead on and insert at.
pub(crate) fn fitted_span(length: i64, tail: usize) -> usize {
    (length.max(0) as usize + tail).max(1)
}

impl TimelineView {
    /// Fitted to a cut `length` frames long.
    pub(crate) fn fitted(length: i64, tail: usize) -> Self {
        Self { span: fitted_span(length, tail), empty: length <= 0 }
    }

    /// The edit frames the tracks' width spans.
    pub(crate) fn span(self) -> usize {
        self.span
    }

    /// After the cut changed to `length` frames: the span as it was, wider
    /// if the cut no longer fits in it, fitted if the cut was empty.
    #[must_use]
    pub(crate) fn changed(self, length: i64, tail: usize) -> Self {
        if self.empty && length > 0 {
            return Self::fitted(length, tail);
        }
        Self { span: self.span.max(fitted_span(length, tail)), empty: length <= 0 }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// Whatever edits follow one another (lengths up or down, the cut
        /// emptied and filled), the span never shrinks on its own, always
        /// holds the cut and a second past it, and refits only when a clip
        /// goes into an empty cut.
        #[test]
        fn edits_never_shrink_or_refit_the_span(
            start in 0i64..5000,
            tail in 1usize..120,
            lengths in prop::collection::vec(prop_oneof![Just(0i64), 0i64..5000], 1..40),
        ) {
            let mut view = TimelineView::fitted(start, tail);
            let mut last = start;
            for length in lengths {
                let next = view.changed(length, tail);
                prop_assert!(next.span() >= fitted_span(length, tail), "the cut no longer fits");
                if last <= 0 && length > 0 {
                    prop_assert_eq!(next, TimelineView::fitted(length, tail), "the first clip fits the cut");
                } else {
                    prop_assert!(next.span() >= view.span(), "an edit shrank the span");
                    prop_assert_eq!(next.span(), view.span().max(fitted_span(length, tail)));
                }
                view = next;
                last = length;
            }
        }

        /// A trim, a slip, a roll, a fade or a move inside the span (the cut
        /// still fits): the span, so every clip's size on screen and the
        /// ruler's scale, is what it was.
        #[test]
        fn an_edit_that_still_fits_keeps_the_span(start in 1i64..5000, tail in 1usize..120, shrink in 0i64..5000) {
            let view = TimelineView::fitted(start, tail);
            let length = (start - shrink).max(1);
            prop_assert_eq!(view.changed(length, tail).span(), view.span());
            // Shrunk, then grown back to where it was: still the same span.
            prop_assert_eq!(view.changed(length, tail).changed(start, tail).span(), view.span());
        }

        /// A cut that grows past the span widens it to fit, no more.
        #[test]
        fn a_longer_cut_widens_the_span_to_fit(start in 1i64..5000, tail in 1usize..120, grow in 1i64..5000) {
            let view = TimelineView::fitted(start, tail);
            prop_assert_eq!(view.changed(start + grow, tail).span(), fitted_span(start + grow, tail));
        }

        /// Fitting (opening an edit, View > Fit Timeline) is the cut and its
        /// second, and fitting again changes nothing.
        #[test]
        fn fit_is_the_cut_and_a_second(length in -10i64..5000, tail in 1usize..120) {
            let view = TimelineView::fitted(length, tail);
            prop_assert_eq!(view.span(), length.max(0) as usize + tail);
            prop_assert_eq!(view.changed(length, tail), view);
        }
    }
}
