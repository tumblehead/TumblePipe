//! What a running scan has done, as the window says it: two phases,
//! **Indexing** (reading the project: listing its files, sorting them,
//! reading the latest workfiles) and **Sizing** (the six categories, run at
//! once), each a share that never goes back, and what each category has
//! found to purge so far.
//!
//! On the 2.5 TB HideAndReek share indexing took about 25 s of listing,
//! 20 s of sorting and 75 s of workfiles, so those are its weights.

use std::time::Duration;

use th_cleanup::core::{CATEGORY_KEYS, Progress};
use th_cleanup::py::thousands;

/// The reading steps `th_cleanup` reports, in order, and their weights.
pub const LISTING: &str = "Listing files";
pub const SORTING: &str = "Sorting files";
pub const WORKFILES: &str = "Reading workfiles";
const READ_WEIGHTS: [(&str, f32); 3] = [(LISTING, 0.2), (SORTING, 0.2), (WORKFILES, 0.6)];
/// Never full before the phase is done.
const MOST: f32 = 0.99;
/// The files a listing is guessed to reach before anything is known.
const GUESS_FILES: usize = 100_000;

/// The scan as its reports leave it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Snapshot {
    /// The newest report of each reading step reached, in order.
    pub reading: Vec<Progress>,
    /// The newest report of each category, by its place in the six.
    pub categories: [Option<Progress>; 6],
    /// The categories finished.
    pub done: [bool; 6],
}

impl Snapshot {
    /// Take in one report.
    pub fn report(&mut self, progress: &Progress) {
        match progress.category {
            None => match self.reading.iter_mut().find(|p| p.doing == progress.doing) {
                Some(step) => *step = progress.clone(),
                None => self.reading.push(progress.clone()),
            },
            Some(key) => {
                if let Some(at) = CATEGORY_KEYS.iter().position(|k| *k == key) {
                    self.categories[at] = Some(progress.clone());
                }
            }
        }
    }

    /// Whether indexing is done: a category has started.
    pub fn indexed(&self) -> bool {
        self.categories.iter().any(Option::is_some) || self.done.iter().any(|d| *d)
    }

    /// Whether sizing is done: every category is.
    pub fn sized(&self) -> bool {
        self.done.iter().all(|d| *d)
    }

    /// Whether category `at` has started.
    pub fn started(&self, at: usize) -> bool {
        self.categories[at].is_some() || self.done[at]
    }

    fn step(&self, doing: &str) -> Option<&Progress> {
        self.reading.iter().find(|p| p.doing == doing)
    }

    /// The files the listing found: its count while it runs, the sorting's
    /// total once it is done.
    pub fn files(&self) -> Option<usize> {
        self.step(SORTING).and_then(|p| p.total).or(self.step(LISTING).map(|p| p.done))
    }

    /// How far indexing is, `0..=1`: 1 once it is done, below until then.
    /// The listing is done once the sorting starts; `last_files` (the
    /// files the last scan found) stands in for its unknown total.
    pub fn indexing(&self, last_files: Option<usize>) -> f32 {
        if self.indexed() {
            return 1.0;
        }
        let mut sum = 0.0;
        for (doing, weight) in READ_WEIGHTS {
            let share = match self.step(doing) {
                _ if doing == LISTING && self.step(SORTING).is_some() => 1.0,
                None => 0.0,
                Some(p) if doing == LISTING => {
                    let n = p.done as f32;
                    match last_files.filter(|n| *n > 0) {
                        Some(last) => (n / last as f32).min(0.95),
                        None => n / (n + GUESS_FILES as f32),
                    }
                }
                Some(p) => fraction(p).unwrap_or(0.0),
            };
            sum += weight * share;
        }
        sum.clamp(0.0, MOST)
    }

    /// How far sizing is, `0..=1`: each category a sixth, a started one a
    /// tenth of its sixth and its count where it has a total; 1 once all
    /// six are done.
    pub fn sizing(&self) -> f32 {
        if self.sized() {
            return 1.0;
        }
        let shares: f32 = (0..CATEGORY_KEYS.len())
            .map(|at| match &self.categories[at] {
                _ if self.done[at] => 1.0,
                Some(p) => 0.1 + 0.8 * fraction(p).unwrap_or(0.0),
                None => 0.0,
            })
            .sum();
        (shares / CATEGORY_KEYS.len() as f32).clamp(0.0, MOST)
    }

    /// What category `at` has found to purge so far: items and bytes.
    pub fn found(&self, at: usize) -> (usize, u64) {
        self.categories[at].as_ref().map_or((0, 0), |p| (p.found, p.found_bytes))
    }
}

/// `done` of `total`, or the bytes of theirs, where a total is known.
pub fn fraction(p: &Progress) -> Option<f32> {
    match (p.total_bytes, p.total) {
        (Some(total), _) if total > 0 => Some((p.bytes as f32 / total as f32).min(1.0)),
        (_, Some(total)) if total > 0 => Some((p.done as f32 / total as f32).min(1.0)),
        (_, Some(_)) => Some(1.0),
        _ => None,
    }
}

/// Indexing's count: "18,402 files".
pub fn files_words(files: Option<usize>) -> String {
    match files {
        Some(1) => "1 file".into(),
        Some(n) => format!("{} files", thousands(n)),
        None => String::new(),
    }
}

/// `m:ss`, or `h:mm:ss` past an hour.
pub fn clock(elapsed: Duration) -> String {
    let seconds = elapsed.as_secs();
    let (h, m, s) = (seconds / 3600, seconds / 60 % 60, seconds % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(category: Option<&'static str>, doing: &'static str, done: usize, total: Option<usize>) -> Progress {
        Progress { category, doing, done, total, bytes: 0, total_bytes: None, found: 0, found_bytes: 0 }
    }

    #[test]
    fn clocks() {
        assert_eq!(clock(Duration::from_secs(0)), "0:00");
        assert_eq!(clock(Duration::from_secs(102)), "1:42");
        assert_eq!(clock(Duration::from_secs(3723)), "1:02:03");
    }

    /// Indexing grows through its steps while sizing waits at 0; then
    /// indexing is full and sizing grows; neither is full before its end.
    #[test]
    fn indexing_then_sizing() {
        let mut s = Snapshot::default();
        let mut last = 0.0;
        for step in [
            p(None, LISTING, 10, None),
            p(None, LISTING, 50_000, None),
            p(None, SORTING, 0, Some(100)),
            p(None, SORTING, 100, Some(100)),
            p(None, WORKFILES, 1, Some(4)),
        ] {
            s.report(&step);
            let now = s.indexing(None);
            assert!(now > last && now < 1.0, "{step:?}: {now} after {last}");
            assert_eq!(s.sizing(), 0.0);
            last = now;
        }
        s.report(&p(Some("renders"), "Reading render records", 5, Some(10)));
        assert_eq!(s.indexing(None), 1.0);
        let sizing = s.sizing();
        assert!(sizing > 0.0 && sizing < 1.0);
        s.done = [true; 6];
        assert_eq!(s.sizing(), 1.0);
    }

    use proptest::prelude::*;

    /// A count that only rises: running sums of drawn steps.
    fn rising(max: usize) -> impl Strategy<Value = Vec<usize>> {
        proptest::collection::vec(0..max, 0..6).prop_map(|steps| {
            steps
                .into_iter()
                .scan(0, |sum, step| {
                    *sum += step;
                    Some(*sum)
                })
                .collect()
        })
    }

    proptest! {
        /// Reading reports in step order, each step's count rising, move
        /// indexing only forward and never to full; a category started
        /// fills it; sizing stays below full until all six are done, and
        /// both stay within 0..=1 whatever the categories report.
        #[test]
        fn the_bars_rise_and_fill_only_when_done(
            listing in rising(40_000),
            sorted in 0usize..200_000,
            sorting in rising(50_000),
            workfiles_total in 0usize..50,
            workfiles in rising(20),
            last_files in proptest::option::of(0usize..300_000),
            categories in proptest::collection::vec((0usize..6, 0usize..50, proptest::option::of(0usize..50), any::<bool>()), 0..20),
        ) {
            let mut s = Snapshot::default();
            let mut last = 0.0f32;
            let steps = listing.iter().map(|d| p(None, LISTING, *d, None))
                .chain(sorting.iter().map(|d| p(None, SORTING, (*d).min(sorted), Some(sorted))))
                .chain(workfiles.iter().map(|d| p(None, WORKFILES, (*d).min(workfiles_total), Some(workfiles_total))));
            for step in steps {
                s.report(&step);
                let now = s.indexing(last_files);
                prop_assert!(now >= last && now < 1.0, "{:?}: {} after {}", step, now, last);
                prop_assert_eq!(s.sizing(), 0.0);
                last = now;
            }
            for (at, done, total, finished) in categories {
                s.report(&p(Some(CATEGORY_KEYS[at]), "Measuring", done, total));
                s.done[at] |= finished;
                prop_assert_eq!(s.indexing(last_files), 1.0);
                let sizing = s.sizing();
                prop_assert!((0.0..=1.0).contains(&sizing));
                prop_assert_eq!(sizing == 1.0, s.sized(), "{}", sizing);
                let shown = fraction(&p(None, "x", done, total));
                prop_assert!(shown.is_none_or(|f| (0.0..=1.0).contains(&f)));
            }
        }
    }
}
