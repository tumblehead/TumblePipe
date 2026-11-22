//! Which edit frames the tracks' width shows: the timeline's scale and
//! where it is scrolled to.
//!
//! The timeline is `span` frames long: the cut and a second past it. The
//! tracks show `shown` of those frames from frame `first` on. Fitted
//! (when the editor opens an edit, on View > Fit Timeline (F), and when
//! the first clip goes into an empty edit) they show all of it.
//!
//! Only the view's own commands change what is shown: a zoom (Ctrl+wheel,
//! about the frame under the pointer), a scroll (Shift+wheel, a sideways
//! wheel or trackpad, the strip under the tracks), Fit Timeline, and Fit
//! to Clips (Shift+F: from the first clip's start to the last clip's end).
//! An edit that changes the cut (a move, a trim, a slip, a roll, a fade,
//! an undo) never rescales it: the clips keep their size on screen and the
//! ruler keeps its scale, so what was dragged stays under the pointer. A
//! cut that grows past the span lengthens the timeline, once, when the
//! change lands (a drag only changes the cut when it is let go, so never
//! mid-drag): shown whole, the tracks go on showing it whole; zoomed in,
//! they keep their scale and place. A cut that shrinks leaves it as it was
//! (2026-11-16 RFE: the span was the cut's length, so it refitted on every
//! trim and the clip jumped from a long bar to a short one and back under
//! the pointer).

use th_edit::document::WorkingEdit;

use crate::tracks::ViewChange;

/// The fewest frames the tracks zoom in to.
pub(crate) const SHOWN_MIN: usize = 8;

/// The timeline's length, the part of it shown, and whether the cut was
/// empty when it was last seen.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct TimelineView {
    span: usize,
    empty: bool,
    first: f64,
    shown: usize,
}

/// The span that fits a cut `length` frames long, `tail` frames (a second)
/// past it to park the playhead on and insert at.
pub(crate) fn fitted_span(length: i64, tail: usize) -> usize {
    (length.max(0) as usize + tail).max(1)
}

impl TimelineView {
    /// Fitted to a cut `length` frames long: all of it shown.
    pub(crate) fn fitted(length: i64, tail: usize) -> Self {
        let span = fitted_span(length, tail);
        Self { span, empty: length <= 0, first: 0.0, shown: span }
    }

    /// The timeline's length in edit frames.
    pub(crate) fn span(self) -> usize {
        self.span
    }

    /// The edit frames the tracks' width shows.
    pub(crate) fn shown(self) -> usize {
        self.shown
    }

    /// The edit frame at the tracks' left edge.
    pub(crate) fn first(self) -> f64 {
        self.first
    }

    /// The whole timeline is shown.
    pub(crate) fn whole(self) -> bool {
        self.shown == self.span
    }

    /// `first` held to where the part shown stays inside the timeline.
    fn held(self, first: f64) -> Self {
        Self { first: first.clamp(0.0, (self.span - self.shown) as f64), ..self }
    }

    /// After the cut changed to `length` frames: the timeline as long as
    /// it was, longer if the cut no longer fits in it, fitted if the cut
    /// was empty. Shown whole it stays shown whole; else what is shown
    /// stays as it is.
    #[must_use]
    pub(crate) fn changed(self, length: i64, tail: usize) -> Self {
        if self.empty && length > 0 {
            return Self::fitted(length, tail);
        }
        let span = self.span.max(fitted_span(length, tail));
        let shown = if self.whole() { span } else { self.shown };
        Self { span, empty: length <= 0, shown, ..self }
    }

    /// Scrolled `frames` later (negative: earlier), never past either end.
    #[must_use]
    pub(crate) fn scrolled(self, frames: f64) -> Self {
        self.held(self.first + frames)
    }

    /// Scrolled so `first` is the first frame shown, as near as the ends
    /// allow.
    #[must_use]
    pub(crate) fn scrolled_to(self, first: f64) -> Self {
        self.held(first)
    }

    /// Showing `shown` frames from `first` (an end of the strip's thumb
    /// dragged): never fewer than [`SHOWN_MIN`] nor more than the whole
    /// timeline, inside it.
    #[must_use]
    pub(crate) fn showing(self, first: f64, shown: usize) -> Self {
        if !first.is_finite() {
            return self;
        }
        let shown = shown.clamp(SHOWN_MIN.min(self.span), self.span);
        Self { shown, ..self }.held(first)
    }

    /// Zoomed by `factor` (above 1: in, fewer frames shown) about frame
    /// `about`, which stays where it is across the width unless an end of
    /// the timeline is reached. Never fewer than [`SHOWN_MIN`] frames nor
    /// more than the whole timeline; a factor other than 1 always changes
    /// the frames shown by at least one, until a limit holds it.
    #[must_use]
    pub(crate) fn zoomed(self, about: f64, factor: f64) -> Self {
        if !(factor.is_finite() && factor > 0.0) || factor == 1.0 {
            return self;
        }
        let least = SHOWN_MIN.min(self.span);
        let mut shown = (self.shown as f64 / factor).round() as usize;
        if shown == self.shown {
            shown = if factor > 1.0 { shown.saturating_sub(1) } else { shown + 1 };
        }
        let shown = shown.clamp(least, self.span);
        let across = (about - self.first) / self.shown as f64;
        Self { shown, ..self }.held(about - across * shown as f64)
    }

    /// Showing the frames `from` to `to` (the first clip's start to the
    /// last clip's end) and nothing else, or [`SHOWN_MIN`] frames from
    /// `from` when they are fewer; as it is when there are none.
    #[must_use]
    pub(crate) fn fitted_to(self, from: i64, to: i64) -> Self {
        if to <= from || from < 0 {
            return self;
        }
        let shown = ((to - from) as usize).clamp(SHOWN_MIN.min(self.span), self.span);
        Self { shown, ..self }.held(from as f64)
    }
}

/// `view` after the wheel asked `change` of it.
pub(crate) fn viewed(view: TimelineView, change: ViewChange) -> TimelineView {
    match change {
        ViewChange::Scroll(frames) => view.scrolled(frames),
        ViewChange::Zoom { about, factor } => view.zoomed(about, factor),
    }
}

/// The frames the clips of `edit` lie in: the first clip's start and the
/// last clip's end, on any track; `(0, 0)` when it has none.
pub(crate) fn clips_span(edit: &WorkingEdit) -> (i64, i64) {
    let spans: Vec<(i64, i64)> = edit.tracks().flat_map(|track| edit.spans(track)).collect();
    let from = spans.iter().map(|s| s.0).min().unwrap_or(0);
    (from, spans.iter().map(|s| s.1).max().unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Something done to the view.
    #[derive(Debug, Clone)]
    enum Do {
        Scroll(f64),
        ScrollTo(f64),
        Zoom(f64, f64),
        FitTo(i64, i64),
        Changed(i64),
        Showing(f64, usize),
    }

    fn doing() -> impl Strategy<Value = Do> {
        prop_oneof![
            (-3000f64..3000.0).prop_map(Do::Scroll),
            (-500f64..6000.0).prop_map(Do::ScrollTo),
            (0f64..1.0, 0.2f64..5.0).prop_map(|(across, factor)| Do::Zoom(across, factor)),
            (0i64..5000, 0i64..5000).prop_map(|(a, b)| Do::FitTo(a, b)),
            (0i64..6000).prop_map(Do::Changed),
            (-500f64..6000.0, 0usize..7000).prop_map(|(first, shown)| Do::Showing(first, shown)),
        ]
    }

    fn done(view: TimelineView, what: &Do, tail: usize) -> TimelineView {
        match *what {
            Do::Scroll(frames) => view.scrolled(frames),
            Do::ScrollTo(first) => view.scrolled_to(first),
            Do::Zoom(across, factor) => view.zoomed(view.first() + across * view.shown() as f64, factor),
            Do::FitTo(from, to) => view.fitted_to(from, to),
            Do::Changed(length) => view.changed(length, tail),
            Do::Showing(first, shown) => view.showing(first, shown),
        }
    }

    proptest! {
        /// Whatever is done to it, in any order: the part shown is inside
        /// the timeline (never before frame 0, never past its end), at
        /// least a frame and at least 8 when the timeline has them; and
        /// the timeline's length only ever changes when the cut does.
        #[test]
        fn what_is_shown_is_always_inside_the_timeline(start in 0i64..5000, tail in 1usize..120, steps in prop::collection::vec(doing(), 0..30)) {
            let mut view = TimelineView::fitted(start, tail);
            for step in &steps {
                let next = done(view, step, tail);
                prop_assert!(next.first() >= 0.0 && next.first() + next.shown() as f64 <= next.span() as f64 + 1e-9, "{:?} after {:?}", next, step);
                prop_assert!(next.shown() >= SHOWN_MIN.min(next.span()) && next.shown() >= 1, "{:?} after {:?}", next, step);
                if !matches!(step, Do::Changed(_)) {
                    prop_assert_eq!(next.span(), view.span(), "{:?} changed the timeline's length", step);
                }
                view = next;
            }
        }

        /// A scroll moves what is shown by exactly the frames asked, or to
        /// the end it ran into, and never changes the scale.
        #[test]
        fn a_scroll_moves_by_what_is_asked_up_to_the_ends(start in 100i64..5000, zoom in 1.5f64..20.0, to in 0f64..1.0, by in -6000f64..6000.0) {
            let whole = TimelineView::fitted(start, 24);
            let view = whole.zoomed(0.0, zoom).scrolled_to(to * whole.span() as f64);
            let next = view.scrolled(by);
            prop_assert_eq!(next.shown(), view.shown());
            let last = (view.span() - view.shown()) as f64;
            prop_assert_eq!(next.first(), (view.first() + by).clamp(0.0, last));
            prop_assert_eq!(view.scrolled_to(view.first() + by), next);
            // The whole timeline shown has nowhere to scroll to.
            prop_assert_eq!(whole.scrolled(by), whole);
        }

        /// A zoom keeps the frame under the pointer under the pointer: as
        /// far across the width after as before, unless the timeline's
        /// start or end is reached (then that end is at the edge). In
        /// shows fewer frames and out more, by the factor, between 8
        /// frames and the whole timeline.
        #[test]
        fn a_zoom_keeps_the_frame_under_the_pointer(
            start in 100i64..5000, zoom in 1.0f64..20.0, to in 0f64..1.0, across in 0f64..1.0, factor in prop_oneof![0.2f64..0.99, 1.01f64..5.0],
        ) {
            let whole = TimelineView::fitted(start, 24);
            let view = whole.zoomed(0.0, zoom).scrolled_to(to * whole.span() as f64);
            let about = view.first() + across * view.shown() as f64;
            let next = view.zoomed(about, factor);
            let wanted = (view.shown() as f64 / factor).round().clamp(SHOWN_MIN as f64, view.span() as f64);
            if next.shown() as f64 != wanted {
                // Rounded to the same frames: one frame more or fewer.
                prop_assert_eq!(wanted as usize, view.shown());
                prop_assert_eq!(next.shown() as i64 - view.shown() as i64, if factor > 1.0 { -1 } else { 1 });
            }
            prop_assert_eq!(next.shown() < view.shown(), factor > 1.0 && view.shown() > SHOWN_MIN);
            prop_assert_eq!(next.shown() > view.shown(), factor < 1.0 && view.shown() < view.span());
            let across_now = (about - next.first()) / next.shown() as f64;
            let at_an_end = next.first() == 0.0 || next.first() == (next.span() - next.shown()) as f64;
            prop_assert!((across_now - across).abs() < 1e-9 || at_an_end, "the frame went from {} to {} across", across, across_now);
            // In, never at an end it was not at: the frame stays put.
            if factor > 1.0 {
                prop_assert!((across_now - across).abs() < 1e-9, "zooming in moved the frame under the pointer");
            }
            prop_assert_eq!(view.zoomed(about, 1.0), view);
        }

        /// Fit to Clips shows exactly the frames from the first clip's
        /// start to the last clip's end; with no clips, nothing changes.
        #[test]
        fn fit_to_clips_shows_the_first_clip_to_the_last(start in 1i64..5000, a in 0f64..1.0, b in 0f64..1.0, zoom in 1.0f64..10.0) {
            let view = TimelineView::fitted(start, 24).zoomed(0.0, zoom);
            let (from, to) = ((a.min(b) * start as f64) as i64, (a.max(b) * start as f64) as i64);
            let next = view.fitted_to(from, to);
            if to > from {
                // At least 8 frames, which near the end start earlier.
                let shown = ((to - from) as usize).max(SHOWN_MIN);
                prop_assert_eq!(next.shown(), shown);
                prop_assert_eq!(next.first(), (from as f64).min((view.span() - shown) as f64));
            } else {
                prop_assert_eq!(next, view);
            }
            prop_assert_eq!(view.fitted_to(-1, to), view);
        }

        /// Whatever edits follow one another (lengths up or down, the cut
        /// emptied and filled), the timeline never shortens on its own,
        /// always holds the cut and a second past it, and refits only when
        /// a clip goes into an empty cut. Shown whole it stays whole.
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
                prop_assert_eq!((next.first(), next.shown()), (0.0, next.span()), "shown whole, it is no longer");
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

        /// Zoomed in, an edit never changes the scale or the place: the
        /// frames shown stay the frames shown, however the cut's length
        /// changes (it is never emptied here).
        #[test]
        fn zoomed_in_an_edit_keeps_scale_and_place(start in 100i64..5000, zoom in 1.5f64..20.0, to in 0f64..1.0, length in 1i64..9000) {
            let whole = TimelineView::fitted(start, 24);
            let view = whole.zoomed(0.0, zoom).scrolled_to(to * whole.span() as f64);
            prop_assume!(!view.whole());
            let next = view.changed(length, 24);
            prop_assert_eq!((next.first(), next.shown()), (view.first(), view.shown()));
            prop_assert_eq!(next.span(), view.span().max(fitted_span(length, 24)));
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
        /// second, all of it shown, and fitting again changes nothing.
        #[test]
        fn fit_is_the_cut_and_a_second(length in -10i64..5000, tail in 1usize..120) {
            let view = TimelineView::fitted(length, tail);
            prop_assert_eq!(view.span(), length.max(0) as usize + tail);
            prop_assert_eq!((view.first(), view.shown()), (0.0, view.span()));
            prop_assert_eq!(view.changed(length, tail), view);
        }
    }
}
