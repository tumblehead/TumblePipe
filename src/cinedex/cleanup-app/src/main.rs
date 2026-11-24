//! Clean Up Project: purges what a TumblePipe project no longer needs.
//!
//! ```text
//! cleanup                      the window (TH_PROJECT_PATH filled in)
//! cleanup --gui [project]      the window on a project
//! cleanup [options] [project]  python -m tumblepipe.cleanup's command line
//! ```
//!
//! The rules are `th_cleanup`'s, the Python cleanup's ported; the window is
//! built from Primer's Tumblehead UI Convention components on the
//! convention theme. The project is the one given on launch (the launcher's
//! `TH_PROJECT_PATH`): the title names it and there is no field to type
//! another; started on none, the window says so and offers no Scan. A scan
//! always covers shots and assets alike. There is no status line. Using it
//! is three steps, in the order they are on screen:
//!
//! 1. The rules, on the left ("Rules"): per category what counts as old.
//! 2. **Scan**: reads the project under every category's rules and deletes
//!    nothing. It sits in the bottom-right row directly left of Purge:
//!    `Scan`, the window's one Primary button, before the first scan (an
//!    empty state says what it does), `Scan Again` (Secondary) after. While a
//!    scan runs it is `Cancel` (a scan changes nothing, so stopping it
//!    leaves the window as it was). From 300 ms in (a quicker scan flashes
//!    nothing) the results side is only a centred block: two labelled bars,
//!    `Indexing` (reading the project) then `Sizing` (the six categories at
//!    once), each with its count, live while its phase runs and full with a
//!    check once done; under them the six categories, each a bar in its
//!    colour that grows with what it has found to purge; then the time the
//!    scan has run. The results replace it when the scan is done. A rule
//!    changed after a scan marks the results stale (a warning notice with a
//!    `Scan Again` link) until the next one.
//! 3. Untick, in the results table, the categories that should **not** be
//!    purged (all are ticked after a scan); the totals, the stacked bar of
//!    what each category frees and the Purge button follow at once, without
//!    scanning again. `Purge` (Danger) asks once in a Confirm sheet
//!    listing only what is ticked (`Cancel` first and the default: `Enter`
//!    and `Esc` cancel), purges exactly that, then scans again so the table
//!    shows what is left.
//!
//! Each category has one colour (the theme's `dept` hues, in order), the
//! same in the stacked bar, its row's bar and the swatch beside its name.
//! Scanning and purging run off the window's thread. A failure stays as a
//! danger notice until dismissed; a purge that went is a success notice
//! until dismissed or the next Scan or Purge.

mod app_icon;
mod bars;
mod elide;
mod flat;
mod form;
mod loose;
mod progress;
mod results;

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use primer::prelude::*;
use primer::subscription::time;
use th_cleanup::core::{self, CATEGORY_KEYS, CategoryPlan, Outcome, Progress, Watch};
use th_cleanup::jobs::LiveFarm;
use th_cleanup::overview::{self, CategoryOverview, Overview, Row, Tone};
use th_cleanup::py::{human, thousands};
use th_cleanup::{Plan, ProjectRoots, Settings, cli};

use results::Unit;

/// The right-aligned label column the number fields line up after.
const LABEL_WIDTH: f32 = 112.0;
/// A number field's width.
const FIELD_WIDTH: f32 = 56.0;
/// The left side's width as the window opens.
const LEFT: f32 = 348.0;
/// The table's category column: the toggle, the tick, the swatch and the
/// longest name.
const PRIMARY: f32 = 206.0;
/// A row bar's height.
const ROW_BAR: f32 = 6.0;
/// A swatch's side.
const SWATCH: f32 = 8.0;
/// How long a scan runs before its progress shows (the convention's
/// spinner delay): a quick scan flashes nothing.
pub const SHOW_DELAY: Duration = Duration::from_millis(300);

/// What the empty state before the first scan says.
const SCAN_BODY: &str =
    "Press Scan to find what could be purged under the rules on the left. Scanning only reads: nothing is purged until you confirm.";
/// The stale notice's title and words.
const STALE_TITLE: &str = "The rules changed since this scan.";
const STALE_TEXT: &str = "Scan again to see what they would purge.";
/// The bottom row's Danger button: the count and size are the Confirm's.
const PURGE: &str = "Purge";
/// The one reason said beside Purge: nothing else on screen says it.
const NOTHING_TICKED: &str = "Nothing ticked to purge.";
/// The empty state when started without a project.
const NO_PROJECT: &str = "No project";
const NO_PROJECT_BODY: &str = "Start Clean Up Project from a TumbleTrove project.";
/// What a category not yet sized says in the scan's list.
const WAITING: &str = "Waiting";
/// The Scan button while a scan runs.
const CANCEL_SCAN: &str = "Cancel";
/// A scan's two phases: reading the project, then the categories.
const INDEXING: &str = "Indexing";
const SIZING: &str = "Sizing";
/// The phase bars' thickness: thick enough that the track reads.
const PHASE_BAR: f32 = 8.0;
/// The scan block's width: a share of the pane, within these bounds.
const BLOCK_SHARE: f32 = 0.6;
const BLOCK_MIN: f32 = 240.0;
const BLOCK_MAX: f32 = 420.0;
/// The purge block's bar, as it runs, is held, and is told to stop.
const PURGING: &str = "Purging";
const PAUSED: &str = "Paused";
const STOPPING: &str = "Stopping\u{2026}";
/// The bottom row while a purge runs.
const PAUSE: &str = "Pause";
const RESUME: &str = "Resume";
const STOP: &str = "Stop";
/// A purge stopped before its first item went.
const STOPPED_EARLY: &str = "Stopped before anything was purged.";

#[derive(Clone)]
enum Message {
    Field(&'static str, String),
    /// A field's Enter: its text shows what is held.
    FieldDone(&'static str),
    Split(SplitChange),
    Toggle(String),
    Scan,
    /// Stop the running scan: nothing it read is kept.
    CancelScan,
    Poll,
    Scanned(u64, Result<Arc<Plan>, String>),
    /// A category's tick in the table: purge it, or not.
    Tick(String, bool),
    ShowWarnings(bool),
    ShowFailure(bool),
    DismissFailure,
    DismissScanError,
    ShowPurged(bool),
    DismissPurged,
    Purge,
    Cancel,
    Confirm,
    Purged(Arc<Plan>, Arc<Vec<(String, Outcome)>>, PathBuf),
    /// The running purge held before its next item, and let go on.
    PausePurge,
    ResumePurge,
    /// End the running purge once the item under way is done.
    StopPurge,
    /// The window's close button while a purge runs: stop it, then quit.
    CloseRequested,
}

/// A purge's thread: not started, running, or done ([`PurgeJob::done`]).
const NOT_STARTED: u8 = 0;
const RUNNING: u8 = 1;
const DONE: u8 = 2;

/// A category a purge purges: its place among the six, key, title, and
/// its items and bytes to go.
#[derive(Debug, Clone)]
struct Chosen {
    index: usize,
    key: String,
    title: String,
    items: usize,
    bytes: u64,
}

/// Each chosen category's last word from a purge: (handled, removed, bytes).
type Told = Arc<Mutex<HashMap<String, (usize, usize, u64)>>>;

/// A purge running: steered from the window (pause, resume, stop between
/// items), its counts written from its thread and read by the poll.
#[derive(Clone)]
struct PurgeJob {
    plan: Arc<Plan>,
    log: PathBuf,
    /// [`core::RUN`], [`core::PAUSE`] or [`core::STOP`].
    control: Arc<AtomicU8>,
    /// Each category's last word: (handled, removed, bytes).
    told: Told,
    chosen: Vec<Chosen>,
    started: Instant,
    /// Since when it is paused, and how long it was paused before.
    paused_at: Option<Instant>,
    paused_for: Duration,
    /// The window asked to close: once the purge ends, quit.
    closing: bool,
    /// Where its thread is ([`NOT_STARTED`], [`RUNNING`], [`DONE`]), so a
    /// window closed mid-purge waits for the item under way.
    done: Arc<(Mutex<u8>, Condvar)>,
}

impl PurgeJob {
    /// Purge the plan, steered by `control`; each report also goes to
    /// `also` (tests), at most once per `every`.
    fn run(&self, also: &(dyn Fn(&core::Removal<'_>) + Sync), every: Duration) -> Vec<(String, Outcome)> {
        let (state, signal) = &*self.done;
        if let Ok(mut state) = state.lock() {
            *state = RUNNING;
        }
        let report = |removal: &core::Removal<'_>| {
            if let Ok(mut told) = self.told.lock() {
                told.insert(removal.category.to_string(), (removal.handled, removal.removed, removal.bytes));
            }
            also(removal);
        };
        let results = core::apply_steered(&self.plan, &self.log, &core::Steer::new(&self.control, &report).throttle(every));
        if let Ok(mut state) = state.lock() {
            *state = DONE;
        }
        signal.notify_all();
        results
    }

    fn paused(&self) -> bool {
        self.control.load(Ordering::SeqCst) == core::PAUSE
    }

    fn stopping(&self) -> bool {
        self.control.load(Ordering::SeqCst) == core::STOP
    }

    /// How long it has run, the paused time left out.
    fn elapsed(&self) -> Duration {
        let now = self.paused_at.unwrap_or_else(Instant::now);
        now.saturating_duration_since(self.started).saturating_sub(self.paused_for)
    }
}

/// A purge that left something behind: a danger notice until dismissed.
#[derive(Debug, Clone, PartialEq)]
struct Failure {
    title: String,
    text: String,
    details: Vec<String>,
    open: bool,
}

/// What a running scan has done, written from its thread and read by the
/// window's poll.
#[derive(Default)]
struct Live {
    generation: u64,
    /// The reports so far.
    snapshot: progress::Snapshot,
    /// The categories finished, by their place among the six.
    done: Vec<(usize, Arc<CategoryPlan>)>,
}

impl Live {
    fn report(&mut self, progress: &Progress) {
        self.snapshot.report(progress);
    }

    fn finished(&mut self, at: usize, category: &CategoryPlan) {
        if let Some(done) = self.snapshot.done.get_mut(at) {
            *done = true;
        }
        self.done.push((at, Arc::new(category.clone())));
    }
}

/// One scan to run: every category, under the rules as they were.
#[derive(Clone)]
struct ScanJob {
    generation: u64,
    project: PathBuf,
    settings: Settings,
    /// Set to stop it.
    cancel: Arc<AtomicBool>,
}

impl ScanJob {
    fn picked() -> Vec<String> {
        CATEGORY_KEYS.iter().map(|k| k.to_string()).collect()
    }

    /// Read the project, [`core::THREADS`] reads at a time, the categories
    /// at once; `report` and `finished` are told from the scan's threads.
    fn run(
        &self,
        farm: &dyn core::Farm,
        report: &(dyn Fn(&Progress) + Sync),
        finished: &(dyn Fn(usize, &CategoryPlan) + Sync),
    ) -> Result<Plan, String> {
        let roots = ProjectRoots::open(&self.project)?;
        let watch = Watch::new(core::THREADS, true, &self.cancel, report, finished);
        core::make_plan_watched(&roots, &self.settings, Some(&Self::picked()), farm, &mut |_| {}, &watch)
    }
}

/// A category as the table shows it.
#[derive(Debug, Clone)]
struct Cat {
    /// Its place in [`CATEGORY_KEYS`], which picks its colour.
    index: usize,
    key: String,
    title: String,
    noun: String,
    to_delete: usize,
    size: u64,
    ticked: bool,
}

struct Window {
    theme: Theme,
    /// The project given on launch, and whether it is one (a folder with
    /// `_config`): there is no field to type another.
    project: String,
    has_project: bool,
    settings: Settings,
    /// The text in each setting's field, by field name.
    texts: Vec<(&'static str, String)>,
    left: f32,
    /// Scans started so far; only the newest one's result is shown.
    generation: u64,
    /// The scan running, if any, and when it started.
    busy: Option<ScanJob>,
    scan_started: Option<Instant>,
    live: Arc<Mutex<Live>>,
    /// What the running scan has finished (by place among the six), and
    /// its reports as of the last poll.
    partial: Vec<Option<CategoryOverview>>,
    snapshot: progress::Snapshot,
    /// The phase bars (indexing, sizing) and what each category has found
    /// (items, bytes): only ever grow within a scan.
    phases: (f32, f32),
    found: [(usize, u64); 6],
    /// The files the last scan listed: the next one's guess.
    last_files: Option<usize>,
    /// The purge running, if any, and its counts per chosen category as of
    /// the last poll (handled, removed, bytes; only ever growing).
    purge: Option<PurgeJob>,
    purge_counts: Vec<(usize, usize, u64)>,
    /// The scan shown, and the overview made of it.
    plan: Option<Arc<Plan>>,
    view: Option<Overview>,
    open: HashSet<String>,
    /// The categories ticked to purge, by key.
    ticked: HashSet<String>,
    /// A rule changed since the shown scan was made.
    stale: bool,
    warnings_open: bool,
    scan_error: Option<String>,
    failure: Option<Failure>,
    /// The Confirm sheet is up, logging to this.
    confirm: Option<PathBuf>,
    /// What the last purge did, a success notice until dismissed or the
    /// next Scan or Purge (not the scan that follows it on its own).
    purged: Option<Purged>,
}

/// A purge that went: what it freed, and its log behind "Show details".
#[derive(Debug, Clone, PartialEq)]
struct Purged {
    tone: NoticeTone,
    text: String,
    log: String,
    open: bool,
}

fn theme() -> Theme {
    Theme::default()
}

/// Category `index`'s colour: the same wherever it is drawn.
fn colour_of(theme: &Theme, index: usize) -> Color {
    theme.dept[index % theme.dept.len()]
}

impl Window {
    fn new(project: String) -> Self {
        let settings = Settings::default();
        let texts = form::all_fields().map(|f| (f.name, form::value(&settings, f.name).to_string())).collect();
        let has_project = !project.trim().is_empty() && Path::new(project.trim()).join("_config").is_dir();
        Window {
            theme: theme(),
            project,
            has_project,
            settings,
            texts,
            left: LEFT,
            generation: 0,
            busy: None,
            scan_started: None,
            live: Arc::new(Mutex::new(Live::default())),
            partial: vec![None; CATEGORY_KEYS.len()],
            snapshot: progress::Snapshot::default(),
            phases: (0.0, 0.0),
            found: [(0, 0); 6],
            last_files: None,
            purge: None,
            purge_counts: Vec::new(),
            plan: None,
            view: None,
            open: HashSet::new(),
            ticked: HashSet::new(),
            stale: false,
            warnings_open: false,
            scan_error: None,
            failure: None,
            confirm: None,
            purged: None,
        }
    }

    /// A rule changed: a scan shown no longer says what they
    /// would purge.
    fn rules_changed(&mut self) {
        if self.plan.is_some() {
            self.stale = true;
        }
    }

    /// The scan the window's rules ask for: every category, shots and
    /// assets alike; none without a project.
    fn job(&self) -> Option<ScanJob> {
        self.has_project.then(|| ScanJob {
            generation: self.generation,
            project: PathBuf::from(self.project.trim()),
            settings: self.settings.clone(),
            cancel: Arc::new(AtomicBool::new(false)),
        })
    }

    /// Stop the running scan. A scan changes nothing, so nothing is left to
    /// undo: what it read is dropped and the window is as it was before it
    /// (the last scan's results, if any, or none).
    fn cancel_scan(&mut self) {
        let Some(job) = self.busy.take() else { return };
        job.cancel.store(true, Ordering::Relaxed);
        self.scan_started = None;
        self.partial = vec![None; CATEGORY_KEYS.len()];
        self.snapshot = progress::Snapshot::default();
        self.phases = (0.0, 0.0);
        self.found = [(0, 0); 6];
    }

    /// Start a scan of every category.
    fn scan(&mut self) -> Command<Message> {
        if self.busy.is_some() || self.purging() {
            return Command::none();
        }
        self.generation += 1;
        let Some(job) = self.job() else { return Command::none() };
        self.partial = vec![None; CATEGORY_KEYS.len()];
        self.snapshot = progress::Snapshot::default();
        self.phases = (0.0, 0.0);
        self.found = [(0, 0); 6];
        if let Ok(mut live) = self.live.lock() {
            *live = Live { generation: job.generation, ..Live::default() };
        }
        self.busy = Some(job.clone());
        self.scan_started = Some(Instant::now());
        let live = self.live.clone();
        let generation = job.generation;
        Command::perform_blocking(
            move || {
                let report = |progress: &Progress| {
                    if let Ok(mut shown) = live.lock()
                        && shown.generation == job.generation
                    {
                        shown.report(progress);
                    }
                };
                let finished = |at: usize, category: &CategoryPlan| {
                    if let Ok(mut shown) = live.lock()
                        && shown.generation == job.generation
                    {
                        shown.finished(at, category);
                    }
                };
                job.run(&LiveFarm, &report, &finished).map(Arc::new)
            },
            move |result| Message::Scanned(generation, result),
        )
    }

    /// Take in what the running purge has done since the last poll: each
    /// count only grows.
    fn poll_purge(&mut self) {
        let Some(job) = &self.purge else { return };
        let Ok(told) = job.told.lock() else { return };
        for (at, chosen) in job.chosen.iter().enumerate() {
            let (Some(now), Some(shown)) = (told.get(&chosen.key), self.purge_counts.get_mut(at)) else { continue };
            *shown = (shown.0.max(now.0), shown.1.max(now.1), shown.2.max(now.2));
        }
    }

    /// Take in what the running scan, or purge, has done since the last
    /// poll.
    fn poll(&mut self) {
        self.poll_purge();
        let Some(generation) = self.busy.as_ref().map(|job| job.generation) else { return };
        let fresh: Vec<(usize, Arc<CategoryPlan>)> = {
            let Ok(live) = self.live.lock() else { return };
            if live.generation != generation {
                return;
            }
            self.snapshot = live.snapshot.clone();
            live.done.iter().filter(|(at, _)| self.partial.get(*at).is_some_and(Option::is_none)).cloned().collect()
        };
        for (index, category) in fresh {
            if let Some(slot) = self.partial.get_mut(index) {
                *slot = Some(overview::category_overview(&category));
            }
        }
        let (indexing, sizing) = self.phases;
        self.phases = (indexing.max(self.snapshot.indexing(self.last_files)), sizing.max(self.snapshot.sizing()));
        for at in 0..CATEGORY_KEYS.len() {
            // A finished category's figures are its plan's, as the results
            // will show them.
            let now = match self.partial.get(at).and_then(Option::as_ref) {
                Some(done) => (done.to_delete, done.size),
                None => self.snapshot.found(at),
            };
            self.found[at] = (self.found[at].0.max(now.0), self.found[at].1.max(now.1));
        }
    }

    fn scanned(&mut self, generation: u64, result: Result<Arc<Plan>, String>) {
        let Some(job) = self.busy.take_if(|job| job.generation == generation) else {
            // A superseded or cancelled scan: its result is never shown.
            return;
        };
        if let Ok(live) = self.live.lock()
            && live.generation == generation
            && let Some(files) = live.snapshot.files()
        {
            self.last_files = Some(files);
        }
        self.scan_started = None;
        self.partial = vec![None; CATEGORY_KEYS.len()];
        self.snapshot = progress::Snapshot::default();
        self.phases = (0.0, 0.0);
        self.found = [(0, 0); 6];
        match result {
            Ok(plan) => {
                let view = overview::overview(&plan);
                let rows: Vec<Row> = view.categories.iter().map(|c| results::category_row(c, Unit::for_largest(c.size))).collect();
                self.open = flat::initially_open(&rows);
                self.ticked = view.categories.iter().map(|c| c.key.clone()).collect();
                self.view = Some(view);
                self.plan = Some(plan);
                self.scan_error = None;
                // A rule changed while it ran is not in it.
                self.stale = self.settings != job.settings;
            }
            Err(message) => {
                self.scan_error = Some(message);
            }
        }
    }

    /// Whether the running scan's progress is on screen: once it has run
    /// [`SHOW_DELAY`].
    fn showing_scan(&self) -> bool {
        self.busy.is_some() && self.scan_started.is_some_and(|at| at.elapsed() >= SHOW_DELAY)
    }

    /// The categories the running scan has finished.
    fn finished(&self) -> usize {
        self.partial.iter().filter(|p| p.is_some()).count()
    }

    /// Whether indexing, and sizing, are done: a category has started, and
    /// all six have finished.
    fn indexed(&self) -> bool {
        self.snapshot.indexed() || self.finished() > 0
    }

    fn sized(&self) -> bool {
        self.finished() == CATEGORY_KEYS.len()
    }

    /// Indexing's and sizing's states: (running, so live; done, so full
    /// with a check). Sizing waits, still, until indexing is done.
    fn phase_states(&self) -> [(bool, bool); 2] {
        let (indexed, sized) = (self.indexed(), self.sized());
        [(!indexed, indexed), (indexed && !sized, sized)]
    }

    fn purging(&self) -> bool {
        self.purge.is_some()
    }

    /// Stop the running purge once the item under way is done: what went
    /// stays gone (the convention's Stop: finished parts remain).
    fn stop_purge(&mut self) {
        if let Some(job) = self.purge.as_mut() {
            job.control.store(core::STOP, Ordering::SeqCst);
            if let Some(at) = job.paused_at.take() {
                job.paused_for += at.elapsed();
            }
        }
    }

    /// What a purge did, as a notice: a success (or, stopped before
    /// anything went, an info) notice, or a danger one if any item was left
    /// with an error. `stopped_of` is the items chosen, for a purge stopped
    /// before its end.
    fn purged(&mut self, results: &[(String, Outcome)], log_path: &Path, stopped_of: Option<usize>) {
        let removed: usize = results.iter().map(|(_, (count, _, _))| count).sum();
        let freed: u64 = results.iter().map(|(_, (_, size, _))| size).sum();
        let errors: Vec<String> = results.iter().flat_map(|(_, (_, _, errors))| errors.iter().cloned()).collect();
        let what = match stopped_of {
            Some(_) if removed == 0 => STOPPED_EARLY.to_string(),
            Some(of) => {
                format!("Stopped after purging {} of {}, freed {}.", thousands(removed), overview::plural(of, "item"), human(freed))
            }
            None => format!("Purged {}, freed {}.", overview::plural(removed, "item"), human(freed)),
        };
        if errors.is_empty() {
            self.purged = Some(Purged {
                tone: if removed == 0 { NoticeTone::Info } else { NoticeTone::Success },
                text: what,
                log: format!("Each one is logged to {}.", log_path.display()),
                open: false,
            });
        } else {
            // A partial result is a failure, never a success.
            self.purged = None;
            let stopped = if stopped_of.is_some() { format!(" {what}") } else { String::new() };
            self.failure = Some(Failure {
                title: format!("{} could not be purged.", overview::plural(errors.len(), "item")),
                text: format!(
                    "They were left on disk; {} ({}) went. Each is named in {}.{stopped}",
                    overview::plural(removed, "item"),
                    human(freed),
                    log_path.display()
                ),
                details: errors,
                open: false,
            });
        }
    }

    /// The categories as the table shows them: the last scan's.
    fn cats(&self) -> Vec<Cat> {
        let category = |index: usize, c: &CategoryOverview| Cat {
            index,
            key: c.key.clone(),
            title: c.title.clone(),
            noun: c.noun.clone(),
            to_delete: c.to_delete,
            size: c.size,
            ticked: self.ticked.contains(&c.key),
        };
        self.view.as_ref().map_or_else(Vec::new, |view| {
            view.categories.iter().map(|c| category(CATEGORY_KEYS.iter().position(|k| *k == c.key).unwrap_or(0), c)).collect()
        })
    }

    /// The categories ticked to purge that have something to purge.
    fn chosen(&self) -> Vec<&CategoryOverview> {
        self.view
            .as_ref()
            .map_or_else(Vec::new, |view| view.categories.iter().filter(|c| c.to_delete > 0 && self.ticked.contains(&c.key)).collect())
    }

    /// How many items, and how many bytes, Purge would purge.
    fn count(&self) -> usize {
        self.chosen().iter().map(|c| c.to_delete).sum()
    }

    fn size(&self) -> u64 {
        self.chosen().iter().map(|c| c.size).sum()
    }

    /// The scan cut to what is ticked: what Purge purges.
    fn purge_plan(&self) -> Option<Arc<Plan>> {
        let mut plan = (**self.plan.as_ref()?).clone();
        plan.categories.retain(|c| self.ticked.contains(&c.key));
        Some(Arc::new(plan))
    }

    fn can_purge(&self) -> bool {
        self.purge_reason().is_none()
    }

    /// Why Purge is disabled. Only [`NOTHING_TICKED`] is said beside it;
    /// the window says the others elsewhere.
    fn purge_reason(&self) -> Option<&'static str> {
        if self.purging() {
            Some("Purging\u{2026}")
        } else if self.busy.is_some() {
            Some("Scanning\u{2026}")
        } else if self.view.is_none() {
            Some("Scan first.")
        } else if self.stale {
            Some("Rules changed: scan again.")
        } else if self.view.as_ref().is_some_and(|v| v.to_delete() == 0) {
            Some("Nothing to purge.")
        } else if self.count() == 0 {
            Some(NOTHING_TICKED)
        } else {
            None
        }
    }

    /// The window's title: the project's name, the one place it is named.
    fn title(&self) -> String {
        let name = Path::new(self.project.trim()).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        if !self.has_project || name.is_empty() { "Clean Up Project".into() } else { format!("Clean Up Project \u{2014} {name}") }
    }

    // ── views ─────────────────────────────────────────────

    /// One category's rules: its name as a small heading, then a line per
    /// number field, the labels right-aligned in one column so every field
    /// starts at one edge.
    fn category(&self, index: usize) -> Element<Message> {
        let t = &self.theme;
        let category = &core::categories()[index];
        let mut group = column().spacing(t.spacing.xs).width(Length::Fill).push(tooltip(
            Id::new(format!("about {index}")),
            text(category.title).semibold().color(t.text),
            form::sentence(category.about),
        ));
        for field in form::fields_of(category.key) {
            let value = self.texts.iter().find(|(n, _)| *n == field.name).map(|(_, t)| t.clone()).unwrap_or_default();
            let name = field.name;
            group = group.push(
                row()
                    .spacing(t.spacing.sm)
                    .align_y(Vertical::Center)
                    .push(text(field.label).color(t.text_muted).width(Length::fixed(LABEL_WIDTH)).align(Horizontal::Right))
                    .push(tooltip(
                        Id::new(format!("tip {name}")),
                        text_input(Id::new(format!("field {name}")), value)
                            .on_input(move |s| Message::Field(name, s))
                            .on_submit(move || Message::FieldDone(name))
                            .width(Length::fixed(FIELD_WIDTH)),
                        field.tip,
                    ))
                    .push(text(field.unit).color(t.text_muted)),
            );
        }
        group.into()
    }

    fn rules_side(&self) -> Element<Message> {
        let t = &self.theme;
        let mut rows = column().spacing(t.spacing.md).width(Length::Fill);
        for index in 0..CATEGORY_KEYS.len() {
            rows = rows.push(self.category(index));
        }
        let body = column()
            .padding(Padding { top: t.spacing.lg, right: t.spacing.lg, bottom: t.spacing.lg, left: t.spacing.lg })
            .width(Length::Fill)
            .push(section("Rules", rows));
        scrollable(Id::new("rules"), body).width(Length::Fill).height(Length::Fill).into()
    }

    /// The colour a category's bars are drawn in: its own while it is to be
    /// purged, grey while not.
    fn bar_colour(&self, cat: &Cat) -> Color {
        if cat.ticked { colour_of(&self.theme, cat.index) } else { self.theme.border_strong }
    }

    fn tree(&self) -> Element<Message> {
        let t = self.theme;
        let cats = Arc::new(self.cats());
        let unit = Unit::for_largest(cats.iter().map(|c| c.size).max().unwrap_or(0));
        let rows: Vec<Row> = match &self.view {
            Some(view) => view.categories.iter().map(|c| results::category_row(c, unit)).collect(),
            None => Vec::new(),
        };
        let largest = cats.iter().map(|c| c.size).max().unwrap_or(0);
        let dim_all = self.stale;
        let scanning = self.busy.is_some();
        let rows = Arc::new(flat::visible(&rows, &self.open));
        let cat_of = {
            let cats = cats.clone();
            move |key: &str| key.split('/').next().and_then(|i| i.parse::<usize>().ok()).and_then(|i| cats.get(i).cloned())
        };
        // A row is dimmed while its category is not to be purged.
        let dimmed = {
            let cat_of = cat_of.clone();
            move |key: &str| dim_all || cat_of(key).is_some_and(|c| c.to_delete > 0 && !c.ticked)
        };
        let keys = rows.clone();
        let cells = rows.clone();
        let depths = rows.clone();
        let colour = move |tone: Tone, dim: bool| match tone {
            _ if dim => t.text_muted,
            Tone::Normal | Tone::Heading | Tone::Warning => t.text,
            Tone::Dim => t.text_muted,
            Tone::Error => t.danger.text,
        };
        let disabled = t.text_disabled;
        let column_of = |index: usize, title: String, width: f32| {
            let rows = cells.clone();
            let dimmed = dimmed.clone();
            // Numbers keep one weight down a column, the group rows' too.
            DataColumn::new(format!("c{index}"), title, move |at| {
                let row = &rows[at];
                let cell = &row.cells[index];
                let shade = if cell == results::EMPTY { disabled } else { colour(row.tone, dimmed(&row.key)) };
                text(cell.clone()).color(shade).into()
            })
            .width(Length::fixed(width))
            .align(Horizontal::Right)
        };
        // Each category's share of what the largest frees, in its colour.
        let bar_column = {
            let rows = cells.clone();
            let cat_of = cat_of.clone();
            let colours: Vec<Color> = cats.iter().map(|c| self.bar_colour(c)).collect();
            DataColumn::new("bar", String::new(), move |at| {
                let row = &rows[at];
                let Some(cat) = cat_of(&row.key).filter(|_| row.depth == 0) else { return space(Length::Fill, Length::Shrink).into() };
                let shown: Element<Message> = if cat.size > 0 && largest > 0 {
                    progress_bar(cat.size as f32 / largest as f32)
                        .fill(colours[cat.index.min(colours.len() - 1)])
                        .height(ROW_BAR)
                        .width(Length::Fill)
                        .into()
                } else {
                    space(Length::Fill, Length::Shrink).into()
                };
                container(shown).align_y(Vertical::Center).width(Length::Fill).height(Length::Fill).into()
            })
            .width(Length::Fill)
        };
        let cell_dimmed = dimmed.clone();
        let mut tree = primer::prelude::tree(
            Id::new("overview"),
            rows.len(),
            t.size.row,
            move |at| keys[at].key.clone(),
            move |at| {
                let row = &rows[at];
                let dim = cell_dimmed(&row.key);
                // A category's name is short and whole, after its tick and
                // swatch; what opens under it can be a sentence, cut to its
                // one line (the whole of it in the tooltip).
                let words: Element<Message> = if row.depth == 0 {
                    text(row.cells[0].clone()).color(colour(row.tone, dim)).semibold().into()
                } else {
                    elide::Elided::new(row.cells[0].clone(), colour(row.tone, dim)).into()
                };
                // A warning or an error says so with its icon as well as
                // its colour.
                let words: Element<Message> = match row.tone {
                    Tone::Warning if row.depth > 0 => row_with_icon(icon(Icon::TriangleAlert).color(t.warning.base).into(), words, &t),
                    Tone::Error => row_with_icon(icon(Icon::CircleX).color(t.danger.text).into(), words, &t),
                    _ => words,
                };
                let words: Element<Message> =
                    if row.tip.is_empty() { words } else { tooltip(Id::new(format!("tip {}", row.key)), words, row.tip.clone()).into() };
                let cell: Element<Message> = match cat_of(&row.key) {
                    Some(cat) if row.depth == 0 => {
                        let has = cat.to_delete > 0;
                        let key = cat.key.clone();
                        // Purge these: a category with nothing to purge has
                        // a blank, disabled tick; none changes mid-scan.
                        let tick = checkbox(Id::new(format!("purge {key}")), cat.ticked && has)
                            .on_toggle(move |v| Message::Tick(key.clone(), v))
                            .disabled(!has || scanning);
                        let swatch = container(space(Length::fixed(SWATCH), Length::fixed(SWATCH)))
                            .background(colour_of(&t, cat.index))
                            .border_radius(2.0);
                        primer::prelude::row()
                            .spacing(t.spacing.sm)
                            .width(Length::Fill)
                            .align_y(Vertical::Center)
                            .push(tick)
                            .push(swatch)
                            .push(words)
                            .into()
                    }
                    _ => words,
                };
                let line = TreeRow::new(row.depth, cell);
                match row.open {
                    Some(open) => line.expandable(open),
                    None => line,
                }
            },
        )
        .depths(move |at| depths[at].depth)
        .on_toggle(Message::Toggle)
        .header("Purge \u{00b7} Category")
        .primary_width(Length::fixed(PRIMARY))
        .width(Length::Fill)
        .height(Length::Fill);
        tree = tree
            .column(bar_column)
            .column(column_of(1, results::FOUND.0.into(), results::FOUND.1))
            .column(column_of(2, results::TO_PURGE.0.into(), results::TO_PURGE.1))
            .column(column_of(3, unit.header(), results::FREES_WIDTH));
        tree.into()
    }

    /// Under the headline: what each category frees, side by side, in its
    /// colour; grey while it is not to be purged.
    fn stacked(&self) -> Element<Message> {
        let t = &self.theme;
        let segments = self
            .cats()
            .into_iter()
            .filter(|c| c.to_delete > 0 && c.size > 0)
            .map(|c| {
                let tip = format!(
                    "{}: {}, {}{}",
                    c.title,
                    human(c.size),
                    overview::plural(c.to_delete, &c.noun),
                    if c.ticked { "" } else { " (not ticked: kept)" }
                );
                let fill =
                    container(space(Length::Fill, Length::Fill)).background(self.bar_colour(&c)).width(Length::Fill).height(Length::Fill);
                (c.size, tooltip(Id::new(format!("segment {}", c.key)), fill, tip).into())
            })
            .collect();
        container(bars::Stacked::new(segments)).border_radius(t.radius.sm).width(Length::Fill).into()
    }

    fn notices(&self) -> Vec<Element<Message>> {
        let mut out: Vec<Element<Message>> = Vec::new();
        if let Some(done) = &self.purged {
            let open = done.open;
            let words = if open { format!("{}\n{}", done.text, done.log) } else { done.text.clone() };
            out.push(
                notice(done.tone, words)
                    .link(Id::new("purged details"), if open { "Hide details" } else { "Show details" }, move || Message::ShowPurged(!open))
                    .on_dismiss(Id::new("purged dismiss"), || Message::DismissPurged)
                    .into(),
            );
        }
        if self.stale && self.view.is_some() {
            out.push(
                notice(NoticeTone::Warning, STALE_TEXT)
                    .title(STALE_TITLE)
                    .link(Id::new("stale scan again"), "Scan Again", || Message::Scan)
                    .into(),
            );
        }
        if let Some(failure) = &self.failure {
            let mut words = failure.text.clone();
            if failure.open {
                for line in &failure.details {
                    words.push('\n');
                    words.push_str(line);
                }
            }
            let open = failure.open;
            out.push(
                notice(NoticeTone::Danger, words)
                    .title(failure.title.clone())
                    .link(Id::new("failure details"), if open { "Hide details" } else { "Show details" }, move || {
                        Message::ShowFailure(!open)
                    })
                    .on_dismiss(Id::new("failure dismiss"), || Message::DismissFailure)
                    .into(),
            );
        }
        if let Some(error) = &self.scan_error {
            out.push(
                notice(NoticeTone::Danger, format!("{error}. Check the project folder and the rules, then scan again."))
                    .title("The scan failed.")
                    .on_dismiss(Id::new("scan error dismiss"), || Message::DismissScanError)
                    .into(),
            );
        }
        if let Some(view) = self.view.as_ref().filter(|_| !self.showing_scan()) {
            let warnings = overview::warnings(view);
            if let Some((category, first)) = warnings.first() {
                let (title, rest) = results::split_sentence(first);
                let mut words = rest;
                let more = warnings.len() - 1;
                if self.warnings_open {
                    for (category, text) in &warnings[1..] {
                        words.push_str(&format!("\n{category}: {text}"));
                    }
                } else if more > 0 {
                    words = format!("{words} {} more {}.", more, if more == 1 { "warning" } else { "warnings" }).trim().to_string();
                }
                let mut shown = notice(NoticeTone::Warning, words).title(format!("{category}: {title}"));
                if more > 0 {
                    let open = self.warnings_open;
                    shown = shown.link(Id::new("warning details"), if open { "Hide details" } else { "Show details" }, move || {
                        Message::ShowWarnings(!open)
                    });
                }
                out.push(shown.into());
            }
        }
        out
    }

    /// How long the running scan has run.
    fn elapsed(&self) -> Duration {
        self.scan_started.map_or(Duration::ZERO, |at| at.elapsed())
    }

    /// One phase of a scan: its label, its count on the right, then its
    /// bar on an outlined track: live while the phase runs, at 0 and still
    /// before, full with a check once done.
    fn phase(&self, key: &str, label: &str, words: String, share: f32, running: bool, done: bool) -> Element<Message> {
        let t = &self.theme;
        let mark: Element<Message> = if done {
            icon(Icon::CircleCheck).size(14.0).color(t.text_muted).into()
        } else {
            space(Length::fixed(14.0), Length::fixed(14.0)).into()
        };
        let head = row()
            .spacing(t.spacing.sm)
            .width(Length::Fill)
            .align_y(Vertical::Center)
            .push(text(label.to_string()).semibold().color(if running || done { t.text } else { t.text_muted }))
            .push(space(Length::Fill, Length::Shrink))
            .push(text(words).color(t.text_muted))
            .push(mark);
        let bar = container(
            progress_bar(if done { 1.0 } else { share })
                .id(Id::new(format!("phase {key}")))
                .live(running)
                .height(PHASE_BAR)
                .width(Length::Fill),
        )
        .border(1.0, t.border_strong)
        .border_radius(PHASE_BAR / 2.0 + 1.0)
        .width(Length::Fill);
        column().spacing(t.spacing.xs).width(Length::Fill).push(head).push(bar).into()
    }

    /// One category in a block's list: its swatch and name, `words` on the
    /// right, and under them a bar in its colour `share` long. Its state:
    /// (`on`: the name not muted; `live`: the bar sweeps; `done`: a check).
    fn list_line(&self, at: usize, title: &str, words: String, share: f32, (on, live, done): (bool, bool, bool)) -> Element<Message> {
        let t = &self.theme;
        let mark: Element<Message> = if done {
            icon(Icon::CircleCheck).size(12.0).color(t.text_muted).into()
        } else {
            space(Length::fixed(12.0), Length::fixed(12.0)).into()
        };
        let swatch = container(space(Length::fixed(SWATCH), Length::fixed(SWATCH))).background(colour_of(t, at)).border_radius(2.0);
        let head = row()
            .spacing(t.spacing.sm)
            .width(Length::Fill)
            .align_y(Vertical::Center)
            .push(swatch)
            .push(text(title.to_string()).color(if on { t.text } else { t.text_muted }))
            .push(space(Length::Fill, Length::Shrink))
            .push(text(words).color(t.text_muted))
            .push(mark);
        let bar = container(progress_bar(share).fill(colour_of(t, at)).live(live).height(ROW_BAR).width(Length::Fill))
            .border(1.0, t.border)
            .border_radius(ROW_BAR / 2.0 + 1.0)
            .width(Length::Fill);
        column().spacing(t.spacing.xs).width(Length::Fill).push(head).push(bar).into()
    }

    /// One category in the scan's list: what it has found to purge so far,
    /// its bar as long as its bytes found against the largest so far
    /// (growing while it runs, settled with a check once done; an empty
    /// track, muted, before it starts).
    fn found_line(&self, at: usize, largest: u64) -> Element<Message> {
        let done = self.partial.get(at).is_some_and(Option::is_some);
        let started = done || self.snapshot.started(at);
        let (items, bytes) = self.found[at];
        let words = if started { format!("{} \u{00b7} {}", overview::plural(items, "item"), human(bytes)) } else { WAITING.into() };
        let share = if largest > 0 { bytes as f32 / largest as f32 } else { 0.0 };
        self.list_line(at, core::categories()[at].title, words, share, (started, started && !done, done))
    }

    /// The running purge's states: (live, so its sweep runs; paused;
    /// stopping) and each chosen category's (live, done).
    fn purge_states(&self) -> ((bool, bool, bool), Vec<(bool, bool)>) {
        let Some(job) = &self.purge else { return ((false, false, false), Vec::new()) };
        let (paused, stopping) = (job.paused(), job.stopping());
        let running = !paused && !stopping;
        let lines = job
            .chosen
            .iter()
            .zip(&self.purge_counts)
            .map(|(chosen, (handled, _, _))| {
                let done = *handled >= chosen.items;
                (running && *handled > 0 && !done, done)
            })
            .collect();
        ((running, paused, stopping), lines)
    }

    /// The running purge, alone and centred in the results pane like the
    /// scan: one bar (the bytes removed of those chosen), each chosen
    /// category's bar filling in its colour over its own total as its items
    /// go, and the time it has run (held while paused).
    fn purge_block(&self, job: &PurgeJob) -> Element<Message> {
        let t = &self.theme;
        let ((running, paused, stopping), lines) = self.purge_states();
        let items: usize = job.chosen.iter().map(|c| c.items).sum();
        let bytes: u64 = job.chosen.iter().map(|c| c.bytes).sum();
        let handled: usize = self.purge_counts.iter().map(|c| c.0).sum();
        let freed: u64 = self.purge_counts.iter().map(|c| c.2).sum();
        let words = format!("{} of {} \u{00b7} {} of {}", thousands(handled), overview::plural(items, "item"), human(freed), human(bytes));
        let share = if bytes > 0 {
            freed as f32 / bytes as f32
        } else if items > 0 {
            handled as f32 / items as f32
        } else {
            0.0
        };
        let label = if stopping {
            STOPPING
        } else if paused {
            PAUSED
        } else {
            PURGING
        };
        let mut list = column().spacing(t.spacing.sm).width(Length::Fill);
        for ((chosen, (handled, _, freed)), (live, done)) in job.chosen.iter().zip(&self.purge_counts).zip(lines) {
            let words = format!("{} of {} \u{00b7} {} freed", thousands(*handled), overview::plural(chosen.items, "item"), human(*freed));
            let share = if chosen.bytes > 0 {
                *freed as f32 / chosen.bytes as f32
            } else if chosen.items > 0 {
                *handled as f32 / chosen.items as f32
            } else {
                0.0
            };
            list = list.push(self.list_line(chosen.index, &chosen.title, words, share, (*handled > 0 || done, live, done)));
        }
        let block = column()
            .spacing(t.spacing.md)
            .width(Length::Fill)
            .push(self.phase("purge", label, words, share, running, false))
            .push(space(Length::Fill, Length::fixed(t.spacing.sm)))
            .push(list)
            .push(
                container(text(format!("{} elapsed", progress::clock(job.elapsed()))).color(t.text_muted))
                    .width(Length::Fill)
                    .align_x(Horizontal::Center),
            );
        loose::Loose::new(block).width(BLOCK_SHARE, BLOCK_MIN, BLOCK_MAX).into()
    }

    /// The running scan, alone and centred in the results pane: the
    /// Indexing and Sizing bars, the six categories filling up as they
    /// find what goes, and the time it has run.
    fn scan_block(&self) -> Element<Message> {
        let t = &self.theme;
        let [(indexing, indexed), (sizing, sized)] = self.phase_states();
        let found_bytes: u64 = self.found.iter().map(|(_, bytes)| bytes).sum();
        let mut sizing_words = format!("{} of {} categories", self.finished(), CATEGORY_KEYS.len());
        if found_bytes > 0 {
            sizing_words = format!("{sizing_words} \u{00b7} {}", human(found_bytes));
        }
        let largest = self.found.iter().map(|(_, bytes)| *bytes).max().unwrap_or(0);
        let mut list = column().spacing(t.spacing.sm).width(Length::Fill);
        for at in 0..CATEGORY_KEYS.len() {
            list = list.push(self.found_line(at, largest));
        }
        let block = column()
            .spacing(t.spacing.md)
            .width(Length::Fill)
            .push(self.phase(INDEXING, INDEXING, progress::files_words(self.snapshot.files()), self.phases.0, indexing, indexed))
            .push(self.phase(SIZING, SIZING, sizing_words, if indexed { self.phases.1 } else { 0.0 }, sizing, sized))
            .push(space(Length::Fill, Length::fixed(t.spacing.sm)))
            .push(list)
            .push(
                container(text(format!("{} elapsed", progress::clock(self.elapsed()))).color(t.text_muted))
                    .width(Length::Fill)
                    .align_x(Horizontal::Center),
            );
        loose::Loose::new(block).width(BLOCK_SHARE, BLOCK_MIN, BLOCK_MAX).into()
    }

    /// The results' body: the scan while one runs, the table, or an empty
    /// state saying why not.
    fn results_body(&self) -> Element<Message> {
        if let Some(job) = &self.purge {
            return self.purge_block(job);
        }
        if self.showing_scan() {
            return self.scan_block();
        }
        let state = match &self.view {
            // Started on no project: nothing to scan, and nowhere to type
            // one in.
            None if !self.has_project => empty_state(NO_PROJECT).icon(Icon::FolderOpen).body(NO_PROJECT_BODY),
            // Before the first scan: what Scan (in the bottom row) does.
            None => empty_state("Scan the project").icon(Icon::Search).body(SCAN_BODY),
            Some(view) if view.to_delete() == 0 => empty_state("Nothing to clean up").icon(Icon::CircleCheck).body(format!(
                "Of {} found in {} categories, nothing is old enough to purge: everything stays.",
                overview::plural(view.found(), "item"),
                view.categories.len()
            )),
            Some(_) => return self.tree(),
        };
        // Centred both ways in the bounded pane the column leaves it (the
        // results side is no scrollable): loose limits, so the empty
        // state's column takes only what it needs and centres itself.
        loose::Loose::new(state).into()
    }

    fn results_side(&self) -> Element<Message> {
        let t = &self.theme;
        let mut side = column()
            .spacing(t.spacing.sm)
            .padding(Padding { top: t.spacing.lg, right: t.spacing.lg, bottom: t.spacing.md, left: t.spacing.lg })
            .width(Length::Fill)
            .height(Length::Fill);
        let scanning = self.showing_scan() || self.purging();
        if scanning {
            // The scan's or the purge's block alone: no headline, no
            // notices.
        } else if let Some(view) = self.view.as_ref().filter(|v| v.to_delete() > 0) {
            let size = format!("{} can be freed", human(self.size()));
            let count = format!("{} of {}", thousands(self.count()), overview::plural(view.found(), "item"));
            let ink = if self.stale { t.text_muted } else { t.text };
            side = side
                .push(
                    row()
                        .spacing(t.spacing.md)
                        .align_y(Vertical::Center)
                        .push(text(size).font_size(t.typography.title).semibold().color(ink))
                        .push(text(count).color(t.text_muted)),
                )
                .push(self.stacked());
        }
        if !scanning {
            for shown in self.notices() {
                side = side.push(shown);
            }
        }
        side = side.push(self.results_body());
        if !self.has_project {
            return side.into();
        }
        if let Some(job) = &self.purge {
            // While a purge runs: Pause (Resume while paused) then Stop,
            // both Secondary; Scan and Purge are not there. Stop ends the
            // purge after the item under way: what went stays gone.
            let stopping = job.stopping();
            let mut hold = if job.paused() {
                button(Id::new("resume purge"), text(RESUME)).on_press(|| Message::ResumePurge)
            } else {
                button(Id::new("pause purge"), text(PAUSE)).on_press(|| Message::PausePurge)
            };
            let mut stop = button(Id::new("stop purge"), text(STOP)).on_press(|| Message::StopPurge);
            if stopping {
                hold = hold.disabled(true);
                stop = stop.disabled(true);
            }
            let hold = tooltip(Id::new("pause purge tip"), hold, "Hold the purge before its next item. Resume goes on.");
            let stop = tooltip(Id::new("stop purge tip"), stop, "End the purge once the item under way is done. What went stays gone.");
            return side.push(button_row().push(hold).push(stop)).into();
        }
        let mut purge = button(Id::new("purge"), text(PURGE)).intent(Intent::Danger);
        purge = if self.can_purge() { purge.on_press(|| Message::Purge) } else { purge.disabled(true) };
        // Scan directly left of Purge: `Scan`, the window's one Primary,
        // before the first scan; `Scan Again` after; `Cancel` while one
        // runs (it changed nothing, so stopping it leaves nothing changed).
        let scan: Element<Message> = if self.busy.is_some() {
            let cancel = button(Id::new("cancel scan"), text(CANCEL_SCAN)).on_press(|| Message::CancelScan);
            tooltip(Id::new("cancel scan tip"), cancel, "Stop the scan. Nothing has been purged.").into()
        } else {
            let mut scan = if self.view.is_none() {
                button(Id::new("scan"), text("Scan")).intent(Intent::Primary)
            } else {
                button(Id::new("scan again"), text("Scan Again"))
            };
            scan = if self.purging() { scan.disabled(true) } else { scan.on_press(|| Message::Scan) };
            tooltip(Id::new("scan tip"), scan, "Read the project under the rules on the left. Nothing is purged.").into()
        };
        let mut foot = button_row();
        // Only a reason nothing else on screen says: the empty states, the
        // stale notice and the progress line say the others.
        if let Some(reason) = self.purge_reason().filter(|r| *r == NOTHING_TICKED) {
            foot = foot.push(text(reason).color(t.text_muted));
        }
        side.push(foot.push(scan).push(purge)).into()
    }

    /// What the Confirm sheet says, owned so the modal can build it
    /// whenever it asks. The main screen already shows what goes and why,
    /// so the Confirm only names the size, the count and what of it a
    /// workfile can make again (`overview::confirm_note`).
    fn confirm_data(&self) -> Option<Confirm> {
        self.plan.as_ref()?;
        Some(Confirm { title: format!("Free up {}?", human(self.size())), body: overview::confirm_note(self.chosen()) })
    }
}

/// Closing the window mid-scan stops the scan: its threads end within a
/// read instead of reading on. Mid-purge (however the window went) the
/// purge stops after the item under way, and the window waits for it, so
/// no folder is left half deleted and its log line is written.
impl Drop for Window {
    fn drop(&mut self) {
        if let Some(job) = &self.busy {
            job.cancel.store(true, Ordering::Relaxed);
        }
        if let Some(job) = &self.purge {
            job.control.store(core::STOP, Ordering::SeqCst);
            let (state, signal) = &*job.done;
            if let Ok(mut state) = state.lock() {
                while *state == RUNNING {
                    match signal.wait(state) {
                        Ok(next) => state = next,
                        Err(_) => return,
                    }
                }
            }
        }
    }
}

fn row_with_icon<Message: 'static>(mark: Element<Message>, words: Element<Message>, theme: &Theme) -> Element<Message> {
    row().spacing(theme.spacing.xs).width(Length::Fill).align_y(Vertical::Center).push(mark).push(words).into()
}

/// The Confirm sheet's words.
#[derive(Clone, Debug)]
struct Confirm {
    /// "Free up 9.2 GB?"
    title: String,
    /// One sentence: how many go, and what a workfile can make again.
    body: String,
}

/// The Confirm: a short title, one sentence, and `Cancel` then `Purge`.
/// No act is the default but Cancel.
fn confirm_sheet(data: &Confirm, _theme: &Theme) -> Element<Message> {
    let footer = button_row()
        .push(button(Id::new("confirm cancel"), text("Cancel")).on_press(|| Message::Cancel))
        .push(button(Id::new("confirm purge"), text(PURGE_VERB)).intent(Intent::Danger).on_press(|| Message::Confirm));
    sheet(data.title.clone()).push(text(data.body.clone()).width(Length::Fill)).footer(footer).into()
}

/// The verb on both Purge buttons.
const PURGE_VERB: &str = "Purge";

impl Application for Window {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        vec![
            window::Window::new(window::Id::MAIN)
                .title(self.title())
                .app_id("cleanup")
                .size(Size::new(1180.0, 780.0))
                .min_size(Size::new(720.0, 520.0))
                .theme(self.theme),
        ]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Field(name, typed) => {
                if let Some(field) = form::all_fields().find(|f| f.name == name).copied() {
                    let current = form::value(&self.settings, name);
                    if let Some(value) = form::read_field(&field, &typed, current)
                        && value != current
                    {
                        form::set(&mut self.settings, name, value);
                        self.rules_changed();
                    }
                }
                if let Some((_, text)) = self.texts.iter_mut().find(|(n, _)| *n == name) {
                    *text = typed;
                }
            }
            Message::FieldDone(name) => {
                let held = form::value(&self.settings, name).to_string();
                if let Some((_, text)) = self.texts.iter_mut().find(|(n, _)| *n == name) {
                    *text = held;
                }
            }
            Message::Split(change) => {
                if let SplitSize::First(left) = change.size {
                    self.left = left;
                }
            }
            Message::Toggle(key) => {
                if !self.open.remove(&key) {
                    self.open.insert(key);
                }
            }
            Message::Scan => {
                // A scan asked for clears what the last purge said; the one
                // that follows a purge on its own does not.
                if self.busy.is_none() && !self.purging() {
                    self.purged = None;
                }
                return self.scan();
            }
            Message::CancelScan => self.cancel_scan(),
            Message::Poll => self.poll(),
            Message::Scanned(generation, result) => self.scanned(generation, result),
            Message::Tick(key, on) => {
                if on {
                    self.ticked.insert(key);
                } else {
                    self.ticked.remove(&key);
                }
            }
            Message::ShowWarnings(open) => self.warnings_open = open,
            Message::ShowFailure(open) => {
                if let Some(failure) = &mut self.failure {
                    failure.open = open;
                }
            }
            Message::DismissFailure => self.failure = None,
            Message::DismissScanError => self.scan_error = None,
            Message::ShowPurged(open) => {
                if let Some(done) = &mut self.purged {
                    done.open = open;
                }
            }
            Message::DismissPurged => self.purged = None,
            Message::Purge => {
                if let Some(plan) = self.plan.as_ref().filter(|_| self.can_purge()) {
                    self.confirm = Some(core::log_path_for(&plan.roots));
                }
            }
            Message::Cancel => self.confirm = None,
            Message::Confirm => {
                let Some(log_path) = self.confirm.take() else { return Command::none() };
                let Some(plan) = self.purge_plan().filter(|_| self.can_purge()) else { return Command::none() };
                let chosen: Vec<Chosen> = self
                    .chosen()
                    .iter()
                    .map(|c| Chosen {
                        index: CATEGORY_KEYS.iter().position(|k| *k == c.key).unwrap_or(0),
                        key: c.key.clone(),
                        title: c.title.clone(),
                        items: c.to_delete,
                        bytes: c.size,
                    })
                    .collect();
                let job = PurgeJob {
                    plan,
                    log: log_path,
                    control: Arc::new(AtomicU8::new(core::RUN)),
                    told: Arc::new(Mutex::new(HashMap::new())),
                    chosen,
                    started: Instant::now(),
                    paused_at: None,
                    paused_for: Duration::ZERO,
                    closing: false,
                    done: Arc::new((Mutex::new(NOT_STARTED), Condvar::new())),
                };
                self.purge_counts = vec![(0, 0, 0); job.chosen.len()];
                self.purge = Some(job.clone());
                self.purged = None;
                return Command::perform_blocking(
                    move || {
                        let results = job.run(&|_| {}, Duration::from_millis(100));
                        (job.plan.clone(), Arc::new(results), job.log.clone())
                    },
                    |(plan, results, log_path)| Message::Purged(plan, results, log_path),
                );
            }
            Message::Purged(_plan, results, log_path) => {
                let job = self.purge.take();
                self.purge_counts.clear();
                let stopped_of = job.as_ref().filter(|j| j.stopping()).map(|j| j.chosen.iter().map(|c| c.items).sum());
                self.purged(&results, &log_path, stopped_of);
                if job.is_some_and(|j| j.closing) {
                    // Closed mid-purge: the item under way is done and
                    // logged; now the window goes.
                    return Command::quit();
                }
                // The one scan that starts on its own: what is left.
                return self.scan();
            }
            Message::PausePurge => {
                if let Some(job) = self.purge.as_mut().filter(|j| !j.stopping() && !j.paused()) {
                    job.control.store(core::PAUSE, Ordering::SeqCst);
                    job.paused_at = Some(Instant::now());
                }
            }
            Message::ResumePurge => {
                if let Some(job) = self.purge.as_mut().filter(|j| j.paused()) {
                    job.control.store(core::RUN, Ordering::SeqCst);
                    if let Some(at) = job.paused_at.take() {
                        job.paused_for += at.elapsed();
                    }
                }
            }
            Message::StopPurge => self.stop_purge(),
            Message::CloseRequested => {
                self.stop_purge();
                if let Some(job) = self.purge.as_mut() {
                    job.closing = true;
                }
            }
        }
        Command::none()
    }

    fn on_close_requested(&self, _window: window::Id) -> Option<Message> {
        // Mid-purge the window stays until the item under way is done;
        // otherwise closing quits (a scan is cancelled as the window goes).
        self.purging().then_some(Message::CloseRequested)
    }

    fn subscription(&self) -> Subscription<Message> {
        if self.busy.is_some() || self.purging() { time::every(Duration::from_millis(100), || Message::Poll) } else { Subscription::none() }
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        app_icon::apply();
        let t = &self.theme;
        let body = split(Id::new("sides"), self.rules_side(), self.results_side())
            .size(SplitSize::First(self.left))
            .min_sizes(330.0, 360.0)
            .on_resize(Message::Split)
            .width(Length::Fill)
            .height(Length::Fill);
        let page = column().spacing(0.0).width(Length::Fill).height(Length::Fill).push(body);
        let mut root = modal(container(page).background(t.bg_page).width(Length::Fill).height(Length::Fill));
        if self.confirm.is_some()
            && let Some(data) = self.confirm_data()
        {
            let theme = *t;
            // Esc cancels; so does Enter: in a Confirm for an act that
            // cannot be undone, Cancel is the default.
            root = root
                .dialog(Id::new("confirm sheet"), move || confirm_sheet(&data, &theme))
                .on_dismiss(|| Message::Cancel)
                .on_submit(|| Message::Cancel);
        }
        root.into()
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match cli::parse(&argv) {
        Ok(args) => args,
        // The command line says what is wrong, as Python's does.
        Err(_) => std::process::exit(run_cli(&argv)),
    };
    if argv.is_empty() || args.gui {
        let project = args.project.clone().or_else(|| std::env::var("TH_PROJECT_PATH").ok()).unwrap_or_default();
        // Nothing runs until the person presses Scan.
        if let Err(error) = run(Window::new(project), Setup::default()) {
            eprintln!("{}: {error}", cli::PROG);
            std::process::exit(1);
        }
        return;
    }
    std::process::exit(run_cli(&argv));
}

fn run_cli(argv: &[String]) -> i32 {
    let (stdout, stderr) = (std::io::stdout(), std::io::stderr());
    cli::run(argv, &LiveFarm, &mut stdout.lock(), &mut stderr.lock())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{HashMap, HashSet as Set};

    pub(super) struct NoFarm;

    impl core::Farm for NoFarm {
        fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String> {
            Err("no farm here".into())
        }
        fn local_folders(&self) -> Result<Set<String>, String> {
            Ok(Set::new())
        }
    }

    /// A project with one old `_deleted` stamp to go.
    pub(super) fn project(home: &Path) -> PathBuf {
        let project = home.join("Demo");
        let config = project.join("_config");
        std::fs::create_dir_all(&config).unwrap();
        std::fs::write(config.join("storage.json"), include_bytes!("../../../../scripts/project_template/_config/storage.json")).unwrap();
        std::fs::write(config.join("version.json"), br#"{"version": 22}"#).unwrap();
        let stamp = project.join("shots").join("_deleted").join("20200101-120000");
        std::fs::create_dir_all(&stamp).unwrap();
        std::fs::write(stamp.join("f"), b"data").unwrap();
        project
    }

    /// Press Scan and run the scan it starts, as the command would (with no
    /// farm): the window as it then shows.
    pub(super) fn scan(window: &mut Window) {
        let _ = window.update(Message::Scan);
        run_scan(window);
    }

    /// Run the scan in progress, if any, and hand its result over.
    pub(super) fn run_scan(window: &mut Window) {
        if let Some(job) = window.busy.clone() {
            let result = job.run(&NoFarm, &|_| {}, &|_, _| {}).map(Arc::new);
            let _ = window.update(Message::Scanned(job.generation, result));
        }
    }

    /// The window through a scan, the Confirm and a purge, as its messages
    /// drive it.
    #[test]
    fn a_scan_is_confirmed_then_purged() {
        let home = tempfile::tempdir().unwrap();
        let project = project(home.path());
        let mut window = Window::new(project.display().to_string());
        let _ = window.view(window::Id::MAIN);
        assert!(window.busy.is_none(), "nothing scans on its own");
        assert_eq!(window.purge_reason(), Some("Scan first."));

        scan(&mut window);
        assert!(window.busy.is_none() && window.can_purge(), "{:?}", window.scan_error);
        assert_eq!(window.count(), 1);
        let _ = window.view(window::Id::MAIN);

        // A rule changed makes the scan stale: nothing can be purged until
        // it is scanned again, and nothing scans on its own.
        let _ = window.update(Message::Field("deleted_days", "+=1".into()));
        assert!(window.stale && window.busy.is_none());
        assert_eq!(window.purge_reason(), Some("Rules changed: scan again."));
        assert_eq!(window.settings.deleted_days, Settings::default().deleted_days + 1.0);
        scan(&mut window);
        assert!(window.can_purge() && !window.stale);

        let _ = window.update(Message::Purge);
        assert!(window.confirm.is_some());
        let data = window.confirm_data().unwrap();
        assert_eq!(data.title, "Free up 4 B?");
        // A deleted entity: no workfile makes it again, and the words say
        // only what happens.
        assert_eq!(data.body, "1 old item will be removed from disk.");
        let _ = window.view(window::Id::MAIN);

        // Cancel changes nothing; Confirm purges what was ticked.
        let _ = window.update(Message::Cancel);
        assert!(window.confirm.is_none() && window.can_purge());
        let _ = window.update(Message::Purge);
        let log_path = window.confirm.clone().unwrap();
        let _ = window.update(Message::Confirm);
        assert!(window.purging() && !window.can_purge());
        let plan = window.purge_plan().unwrap();
        let results = core::apply(&plan, &log_path);
        let _ = window.update(Message::Purged(plan, Arc::new(results), log_path.clone()));
        assert!(!window.purging());
        assert!(!project.join("shots").join("_deleted").join("20200101-120000").exists());
        assert!(log_path.is_file());
        // It scans again at once, and says what the purge did.
        assert!(window.busy.is_some(), "the one scan that starts on its own");
        run_scan(&mut window);
        let done = window.purged.clone().expect("the success, kept across the scan that follows");
        assert_eq!(done.text, "Purged 1 item, freed 4 B.");
        assert!(done.log.contains(&log_path.display().to_string()));
        assert_eq!(window.count(), 0);
        assert_eq!(window.purge_reason(), Some("Nothing to purge."));
        assert!(window.failure.is_none());
        let _ = window.view(window::Id::MAIN);
        // A scan asked for clears it.
        scan(&mut window);
        assert!(window.purged.is_none());
    }

    /// Nothing is scanned without a project given on launch (none, a
    /// folder that is not there, a folder that is not a project).
    #[test]
    fn a_scan_needs_a_project() {
        let home = tempfile::tempdir().unwrap();
        for given in [String::new(), "Z:/no/such/project/here".into(), home.path().display().to_string()] {
            let mut window = Window::new(given.clone());
            assert!(!window.has_project, "{given}");
            assert_eq!(window.title(), "Clean Up Project");
            let _ = window.update(Message::Scan);
            assert!(window.busy.is_none(), "{given}");
        }
        let window = Window::new(project(home.path()).display().to_string());
        assert!(window.has_project);
        assert_eq!(window.title(), "Clean Up Project \u{2014} Demo");
    }

    /// A scan reads every category, and its reports reach the window: the
    /// reading first, then each category as it starts and finishes.
    #[test]
    fn a_scan_reports_its_steps() {
        assert_eq!(ScanJob::picked(), CATEGORY_KEYS.map(String::from).to_vec());
        let home = tempfile::tempdir().unwrap();
        let window = Window::new(project(home.path()).display().to_string());
        let job = window.job().unwrap();
        let seen = Mutex::new(Vec::new());
        let finished = Mutex::new(Vec::new());
        let plan =
            job.run(&NoFarm, &|p| seen.lock().unwrap().push((p.category, p.doing)), &|at, _| finished.lock().unwrap().push(at)).unwrap();
        assert_eq!(plan.categories.len(), 6);
        let seen = seen.into_inner().unwrap();
        assert_eq!(seen.first(), Some(&(None, progress::LISTING)), "{seen:?}");
        assert!(seen.contains(&(None, progress::WORKFILES)), "{seen:?}");
        let first_category = seen.iter().position(|(c, _)| c.is_some()).unwrap();
        assert!(seen[..first_category].iter().all(|(c, _)| c.is_none()), "the project is read before any category: {seen:?}");
        for key in CATEGORY_KEYS {
            assert!(seen.contains(&(Some(key), "Scanning")), "{key} starts: {seen:?}");
        }
        let mut finished = finished.into_inner().unwrap();
        finished.sort();
        assert_eq!(finished, (0..6).collect::<Vec<_>>());
    }

    /// Cancelling a scan stops it: its result never shows, nothing is
    /// planned and Purge stays off; the last scan's results come back.
    #[test]
    fn a_cancelled_scan_leaves_the_window_as_it_was() {
        let home = tempfile::tempdir().unwrap();
        let mut window = Window::new(project(home.path()).display().to_string());
        let _ = window.update(Message::Scan);
        let job = window.busy.clone().unwrap();
        let _ = window.update(Message::CancelScan);
        assert!(job.cancel.load(Ordering::Relaxed), "the scan is told to stop");
        assert!(window.busy.is_none() && window.plan.is_none() && window.view.is_none());
        assert!(!window.can_purge());
        assert_eq!(window.finished(), 0);
        // What the stopped scan comes back with is dropped.
        let result = job.run(&NoFarm, &|_| {}, &|_, _| {});
        assert_eq!(result.as_ref().err().map(String::as_str), Some(core::CANCELLED));
        let _ = window.update(Message::Scanned(job.generation, result.map(Arc::new)));
        assert!(window.plan.is_none() && window.scan_error.is_none() && !window.can_purge());
        let _ = window.view(window::Id::MAIN);

        // After a scan, a cancelled Scan Again keeps that scan.
        scan(&mut window);
        assert!(window.can_purge());
        let _ = window.update(Message::Scan);
        let _ = window.update(Message::CancelScan);
        assert!(window.busy.is_none() && window.plan.is_some() && window.can_purge());
    }
}

#[cfg(test)]
mod view_tests;
