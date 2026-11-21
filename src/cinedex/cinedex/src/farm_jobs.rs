//! Which shots in the timeline have work on the farm, and what their clips
//! show of it.
//!
//! The farm is read by `th_pipe::farm_jobs` (Deadline through
//! `deadlinecommand -Json GetJobs`, this machine's local farm through its
//! queue folder), every [`EVERY`] on a worker thread, one read at a time:
//! a slow `deadlinecommand` holds up only the next read, never the
//! drawing, and is given up on after `th_pipe::farm_jobs::DEADLINE_TIMEOUT`.
//! No farm (no Deadline here, no local queue) is no badges, not an error.
//!
//! A shot's video clip shows the state of the job that speaks for the
//! shot's newest submission ([`th_pipe::farm_jobs::ShotJobs`]): a line in
//! the state's colour along the clip's top, at any width, and where there
//! is room a badge at its top right saying the state, or a dot of that
//! colour where the word does not fit ([`marks`]). The colours are the UI
//! convention's state tones (primer's `Theme`): rendering is `info`,
//! suspended `warning`, failed `danger`, queued and waiting the muted grey.
//!
//! Right-clicking such a clip offers the job ([`menu_rows`]): **Show Farm
//! Job in Farm Manager** opens the pipeline's Farm Manager on it
//! (`farm_submit::manager_launch`, wherever Farm Submit can open), and
//! **Copy Farm Job ID** puts its id on the clipboard.

use std::time::Duration;

use primer::prelude::*;
use th_pipe::farm_jobs::{ShotJob, ShotJobs, Status};

/// How often the farm is asked. `GetJobs` takes about a second for a few
/// hundred jobs; the Farm Manager itself asks every few seconds.
pub(crate) const EVERY: Duration = Duration::from_secs(15);

/// The clip edge inset the tracks draw with (`tracks::INSET`).
const INSET: f32 = 3.0;
/// The status line along a clip's top.
pub(crate) const LINE: f32 = 2.0;
/// The badge's height, and the dot's size.
const BADGE_TALL: f32 = 15.0;
const DOT: f32 = 8.0;

/// What the clip draws for its job's state.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Marks {
    /// The state's line along the clip's top: always, at any width.
    pub(crate) line: Rectangle,
    pub(crate) badge: Option<Badge>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum Badge {
    /// The state's word on its colour.
    Word(Rectangle),
    /// Its colour alone, where the word does not fit.
    Dot(Rectangle),
}

impl Badge {
    #[cfg(test)]
    pub(crate) fn rect(self) -> Rectangle {
        match self {
            Badge::Word(r) | Badge::Dot(r) => r,
        }
    }
}

/// How wide the badge saying `status` is drawn (10 px type).
pub(crate) fn word_wide(status: Status) -> f32 {
    status.word().chars().count() as f32 * 6.0 + 10.0
}

/// What a clip drawn in `rect` shows of a job in `status`, its badge
/// ending at `right` (the clip's right edge less its inset, or left of the
/// preview badge that sits there). The badge says the word only where the
/// clip keeps at least as much room again for its name, else is a dot, and
/// none where even a dot leaves no room; everything stays inside the clip.
pub(crate) fn marks(rect: Rectangle, right: f32, status: Status) -> Marks {
    let line = Rectangle { x: rect.x, y: rect.y, width: rect.width.max(0.0), height: LINE.min(rect.height.max(0.0)) };
    let right = right.min(rect.x + rect.width - INSET);
    let room = right - (rect.x + INSET);
    let wide = word_wide(status);
    let fits_tall = rect.height >= BADGE_TALL + 2.0 * INSET;
    let badge = if room >= 2.0 * wide && fits_tall {
        Some(Badge::Word(Rectangle { x: right - wide, y: rect.y + INSET, width: wide, height: BADGE_TALL }))
    } else if room >= 3.0 * DOT && rect.height >= DOT + 2.0 * INSET + 4.0 {
        Some(Badge::Dot(Rectangle { x: right - DOT, y: rect.y + INSET + 4.0, width: DOT, height: DOT }))
    } else {
        None
    };
    Marks { line, badge }
}

/// The state's colour, and the colour of words on it (the tracks' dark
/// surround, as on the preview badge).
pub(crate) fn colour(status: Status) -> (Color, Color) {
    let theme = crate::display::theme();
    let fill = match status {
        Status::Rendering => theme.info.base,
        Status::Suspended => theme.warning.base,
        Status::Failed => theme.danger.base,
        _ => theme.text_muted,
    };
    (fill, crate::look::surround())
}

/// What a right-click on the clip of a shot with `job` offers, and what
/// each row does: the Farm Manager on the job (when it can be opened
/// here), and its id copied.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Act {
    Show(String),
    Copy(String),
}

pub(crate) const SHOW: &str = "Show Farm Job in Farm Manager";
pub(crate) const COPY: &str = "Copy Farm Job ID";

/// The rows for the clip of a shot whose farm job is `job` (None: no row
/// at all); `manager` says the Farm Manager can be opened here.
pub(crate) fn menu_rows(job: Option<&ShotJob>, manager: bool) -> Vec<(String, Act)> {
    let Some(job) = job else { return Vec::new() };
    let mut rows = Vec::new();
    if manager {
        rows.push((format!("{SHOW} ({})", job.status.word().to_lowercase()), Act::Show(job.id.clone())));
    }
    rows.push((COPY.to_owned(), Act::Copy(job.id.clone())));
    rows
}

/// Ask the farm for `project`'s shots off the UI thread, then `done` with
/// them. The editor's tests never ask the real farm.
pub(crate) fn poll<M: Send + 'static>(project: String, done: impl Fn(ShotJobs) -> M + Send + 'static) -> Command<M> {
    #[cfg(not(test))]
    let ask = move || ShotJobs::ask(&th_pipe::farm_jobs::LiveFarmJobs::here(), &project);
    #[cfg(test)]
    let ask = move || {
        let _ = project;
        ShotJobs::default()
    };
    Command::perform_blocking(ask, done)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn status() -> impl Strategy<Value = Status> {
        proptest::sample::select(Status::ALL.to_vec())
    }

    fn inside(inner: Rectangle, outer: Rectangle) -> bool {
        let e = 0.01;
        inner.x >= outer.x - e
            && inner.y >= outer.y - e
            && inner.x + inner.width <= outer.x + outer.width + e
            && inner.y + inner.height <= outer.y + outer.height + e
    }

    proptest! {
        /// At any width and height, and whatever the preview badge leaves,
        /// the line and the badge stay inside the clip and left of where
        /// the badge must end; a word badge leaves the name at least its own
        /// width; a clip too narrow for a dot still has its line.
        #[test]
        fn marks_stay_inside_their_clip(
            x in -500f32..2000.0, width in 0f32..800.0, height in 0f32..120.0, cut in 0f32..1.0, status in status(),
        ) {
            let rect = Rectangle { x, y: 40.0, width, height };
            let right = rect.x + INSET + (rect.width - 2.0 * INSET).max(0.0) * cut;
            let m = marks(rect, right, status);
            prop_assert!(inside(m.line, rect));
            prop_assert!((m.line.width - width).abs() < 0.01);
            prop_assert!(m.line.height > 0.0 || height == 0.0);
            if let Some(badge) = m.badge {
                let r = badge.rect();
                prop_assert!(inside(r, rect), "{r:?} in {rect:?}");
                prop_assert!(r.x + r.width <= right + 0.01);
                if let Badge::Word(r) = badge {
                    prop_assert!(r.x - (rect.x + INSET) >= r.width - 0.01, "room for the name");
                    prop_assert!((r.width - word_wide(status)).abs() < 0.01);
                }
            }
            let room = right - (rect.x + INSET);
            if room >= 2.0 * word_wide(status) && height >= BADGE_TALL + 2.0 * INSET {
                prop_assert!(matches!(m.badge, Some(Badge::Word(_))), "a word where it fits");
            }
        }

        /// The right-click rows appear only for a clip whose shot has a job,
        /// and name that job; the Farm Manager row only where it can open.
        #[test]
        fn rows_only_for_a_clip_with_a_job(id in "[0-9a-f]{24}", status in status(), manager in any::<bool>(), has in any::<bool>()) {
            let job = ShotJob { source: th_pipe::farm_jobs::Source::Deadline, id: id.clone(), name: "render".into(), status };
            let rows = menu_rows(has.then_some(&job), manager);
            if !has {
                prop_assert!(rows.is_empty());
            } else {
                prop_assert_eq!(rows.len(), if manager { 2 } else { 1 });
                prop_assert!(rows.iter().all(|(_, act)| matches!(act, Act::Show(i) | Act::Copy(i) if *i == id)));
                prop_assert_eq!(rows.iter().any(|(label, _)| label.starts_with(SHOW)), manager);
                prop_assert_eq!(&rows.last().unwrap().0, COPY);
            }
        }
    }

    /// Each shown state has its own colour, and failed is the convention's
    /// danger, rendering its info.
    #[test]
    fn states_take_the_conventions_tones() {
        let theme = crate::display::theme();
        assert_eq!(colour(Status::Failed).0, theme.danger.base);
        assert_eq!(colour(Status::Rendering).0, theme.info.base);
        assert_eq!(colour(Status::Suspended).0, theme.warning.base);
        assert_eq!(colour(Status::Queued).0, colour(Status::Pending).0);
        assert_ne!(colour(Status::Queued).0, colour(Status::Rendering).0);
    }
}
