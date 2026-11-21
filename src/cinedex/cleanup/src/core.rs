//! What every category shares: entries, plans, deleting and logging
//! (`tumblepipe.cleanup.core`).
//!
//! A category reads the project and gives every thing it looked at an
//! [`Entry`]: a file or folder, its size, whether it goes and why. Applying
//! a plan deletes the entries marked to go, checking each once more just
//! before it goes. The project is read once ([`crate::inventory::scan`])
//! for all the categories picked.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use crate::py::{format_g, human, left, right};
use crate::roots::ProjectRoots;
use crate::{backups, caches, deleted, exports, inventory, jobs, renders};

/// Where the cleanup logs what it deleted, under the export root.
pub const LOGS_DIR: [&str; 2] = ["other", "logs"];

/// A version folder holding a file of this name is never deleted.
pub const KEEP_FILE: &str = ".keep";
/// A version written to this recently may still be being written.
pub const RECENT_DAYS: f64 = 2.0;

/// A line end in a log, as Python's text-mode file writes one.
pub const NEWLINE: &str = if cfg!(windows) { "\r\n" } else { "\n" };

/// What the categories keep. Each default is the careful one.
#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    /// Only shots, only assets, or a path under them (`shots/sq010`):
    /// narrows exports, renders, caches and backups.
    pub context: Option<String>,
    pub keep_exports: i64,
    pub keep_renders: i64,
    pub check_render_days: f64,
    pub keep_caches: i64,
    pub keep_backups: i64,
    pub backup_days: f64,
    pub deleted_days: f64,
    pub job_days: f64,
    pub local_job_days: f64,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            context: None,
            keep_exports: 3,
            keep_renders: 2,
            check_render_days: 14.0,
            keep_caches: 2,
            keep_backups: 5,
            backup_days: 7.0,
            deleted_days: 30.0,
            job_days: 2.0,
            local_job_days: 14.0,
        }
    }
}

impl Settings {
    /// What is wrong with these settings, if anything.
    pub fn problem(&self) -> Option<String> {
        if self.keep_exports < 1 {
            return Some("keep at least one export version".into());
        }
        if self.keep_renders < 1 {
            return Some("keep at least one render version".into());
        }
        if self.keep_caches < 1 {
            return Some("keep at least one cache version".into());
        }
        if self.keep_backups < 0 {
            return Some("the number of backups to keep cannot be negative".into());
        }
        for (name, value) in [
            ("check_render_days", self.check_render_days),
            ("backup_days", self.backup_days),
            ("deleted_days", self.deleted_days),
            ("job_days", self.job_days),
            ("local_job_days", self.local_job_days),
        ] {
            if value < 0.0 {
                return Some(format!("{} cannot be negative", name.replace('_', " ")));
            }
        }
        if let Some(context) = self.context.as_deref().filter(|c| !c.is_empty()) {
            let top = context.trim_matches('/').split('/').next().unwrap_or("");
            if top != "shots" && top != "assets" {
                return Some("the context must be shots, assets or a path under one of them".into());
            }
        }
        None
    }

    /// The context, when one is set (`''` is none).
    pub fn context(&self) -> Option<&str> {
        self.context.as_deref().filter(|c| !c.is_empty())
    }
}

/// One file or folder a category looked at.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub path: PathBuf,
    /// How it is listed and logged: its path under its storage root.
    pub label: String,
    /// What the listing totals by (an entity, a department folder).
    pub group: String,
    pub size: u64,
    pub prune: bool,
    pub reason: String,
}

/// What a category's own delete needs besides its entries.
#[derive(Debug, Clone, PartialEq)]
pub enum Payload {
    None,
    Exports(exports::ExportPlan),
    /// The render root.
    Renders(Option<PathBuf>),
    /// Every root, by purpose (caches, backups).
    Roots(Vec<(String, PathBuf)>),
    Jobs {
        jobs_path: PathBuf,
        verdicts: Vec<jobs::Verdict>,
    },
    /// The `_deleted` folder of every root.
    Deleted(Vec<(String, PathBuf)>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct CategoryPlan {
    pub key: String,
    pub title: String,
    /// What one entry is called: `version`, `folder`, `file`.
    pub noun: String,
    pub entries: Vec<Entry>,
    /// Lines the listing shows under the category.
    pub notes: Vec<String>,
    pub errors: Vec<String>,
    pub payload: Payload,
}

impl CategoryPlan {
    pub fn new(key: &str, title: &str, noun: &str) -> Self {
        CategoryPlan {
            key: key.into(),
            title: title.into(),
            noun: noun.into(),
            entries: Vec::new(),
            notes: Vec::new(),
            errors: Vec::new(),
            payload: Payload::None,
        }
    }

    pub fn prunable(&self) -> impl Iterator<Item = &Entry> {
        self.entries.iter().filter(|e| e.prune)
    }

    pub fn prunable_count(&self) -> usize {
        self.prunable().count()
    }

    pub fn prunable_size(&self) -> u64 {
        self.prunable().map(|e| e.size).sum()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub roots: ProjectRoots,
    pub settings: Settings,
    pub categories: Vec<CategoryPlan>,
}

impl Plan {
    pub fn get(&self, key: &str) -> Option<&CategoryPlan> {
        self.categories.iter().find(|c| c.key == key)
    }

    pub fn prunable_count(&self) -> usize {
        self.categories.iter().map(CategoryPlan::prunable_count).sum()
    }

    pub fn prunable_size(&self) -> u64 {
        self.categories.iter().map(CategoryPlan::prunable_size).sum()
    }
}

/// One category, as the window and `--help` list it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Category {
    pub key: &'static str,
    pub title: &'static str,
    /// One line for the window and `--help`.
    pub about: &'static str,
    /// The storage roots it needs read.
    pub purposes: &'static [&'static str],
}

pub const CATEGORY_KEYS: [&str; 6] = ["exports", "renders", "caches", "backups", "jobs", "deleted"];

/// The categories, in the order they are listed.
pub fn categories() -> [Category; 6] {
    [exports::CATEGORY, renders::CATEGORY, caches::CATEGORY, backups::CATEGORY, jobs::CATEGORY, deleted::CATEGORY]
}

/// `(now - stamp)` in days.
pub fn age_days(stamp: f64, now: f64) -> f64 {
    (now - stamp) / 86400.0
}

/// Today's log under the export root (`cleanup-<date>.log`).
pub fn log_path_for(roots: &ProjectRoots) -> PathBuf {
    let mut path = roots.export_path();
    path.extend(LOGS_DIR);
    path.join(format!("cleanup-{}.log", chrono::Local::now().format("%Y-%m-%d")))
}

/// `now` as Python's `isoformat(timespec='seconds')`.
pub fn stamp_now() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

/// Who is deleting, for the log (`$TH_USER`).
pub fn user() -> String {
    std::env::var("TH_USER").ok().filter(|u| !u.is_empty()).unwrap_or_else(|| "unknown".into())
}

/// Whether `label` (`shots/sq010/010/fx/...`) is under `context`
/// (`shots`, `shots/sq010`); everything is under no context.
pub fn under_context(label: &str, context: Option<&str>) -> bool {
    let prefix = context.map(|c| c.trim_matches('/')).unwrap_or("");
    prefix.is_empty() || label == prefix || label.starts_with(&format!("{prefix}/"))
}

/// `Reason.X.format(days=...)`: a day count as Python's `{days:g}`.
pub fn days(value: f64) -> String {
    format_g(value)
}

/// Asks the farm what still uses a job folder. The real one asks
/// Deadline's `deadlinecommand` and this machine's local farm queue
/// ([`jobs::LiveFarm`]); tests answer for it.
pub trait Farm: Sync {
    /// `{folder uuid: [statuses]}` of every TumblePipe job Deadline holds,
    /// or the note saying why there is no answer.
    fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String>;
    /// The job folders (uuids) this machine's local farm queue runs from,
    /// or why its queue could not be read.
    fn local_folders(&self) -> Result<std::collections::HashSet<String>, String>;
}

/// A plan for the categories in `only` (all of them when None). Reads the
/// project; changes nothing. An error for wrong settings, an unknown
/// category, or what Python's listing raises on (a cache sidecar that
/// cannot be read, an `entity:/` ref naming two channels).
pub fn make_plan(
    roots: &ProjectRoots,
    settings: &Settings,
    only: Option<&[String]>,
    farm: &dyn Farm,
    progress: &mut dyn FnMut(&str),
) -> Result<Plan, String> {
    let never = AtomicBool::new(false);
    let watch = Watch::new(THREADS, false, &never, &|_| {}, &|_, _| {});
    make_plan_watched(roots, settings, only, farm, progress, &watch)
}

/// Reads a scan keeps in flight on the share at once. Measured on a
/// 100 Mbit/s share: listing folders 33 s alone, 19 s at 8; more gains
/// little, and what is bound by the wire (the workfiles) gains nothing.
pub const THREADS: usize = 8;

/// What `make_plan_watched` says it is doing.
pub const CANCELLED: &str = "The scan was cancelled.";

/// How far a running scan is, for a window: the reading of the project
/// (`category` None) or one category's work.
#[derive(Debug, Clone, PartialEq)]
pub struct Progress {
    /// The category's key; None while the project itself is read.
    pub category: Option<&'static str>,
    /// What it is doing, in a few words ("Listing folders").
    pub doing: &'static str,
    /// How many of `total` are done (a rising count with no total).
    pub done: usize,
    pub total: Option<usize>,
    /// The bytes measured or read so far.
    pub bytes: u64,
    /// The bytes there are to read, where known.
    pub total_bytes: Option<u64>,
    /// A category's: what it has found to purge so far, items and bytes
    /// (rising through all its steps; its plan has the exact figures).
    pub found: usize,
    pub found_bytes: u64,
}

/// How a scan reports and is stopped. Reports come from any thread, at
/// most about ten a second per [`Meter`] (and each meter's last).
pub struct Watch<'a> {
    /// Reads in flight on the share at once.
    pub threads: usize,
    /// The categories run at the same time (a window); one after another,
    /// each line as it starts (the command line).
    pub concurrent: bool,
    /// Set to stop the scan: it returns `Err(CANCELLED)` soon after.
    pub cancel: &'a AtomicBool,
    pub report: &'a (dyn Fn(&Progress) + Sync),
    /// A category's plan the moment it is made, with its place among the
    /// categories picked (they can finish out of order).
    pub finished: &'a (dyn Fn(usize, &CategoryPlan) + Sync),
    /// What each category has found to purge so far, by key.
    found: Mutex<HashMap<&'static str, (usize, u64)>>,
}

impl<'a> Watch<'a> {
    pub fn new(
        threads: usize,
        concurrent: bool,
        cancel: &'a AtomicBool,
        report: &'a (dyn Fn(&Progress) + Sync),
        finished: &'a (dyn Fn(usize, &CategoryPlan) + Sync),
    ) -> Self {
        Watch { threads, concurrent, cancel, report, finished, found: Mutex::new(HashMap::new()) }
    }
}

impl Watch<'_> {
    /// What `category` has found to purge so far.
    pub fn found(&self, category: Option<&str>) -> (usize, u64) {
        let Some(category) = category else { return (0, 0) };
        self.found.lock().map(|found| found.get(category).copied().unwrap_or((0, 0))).unwrap_or((0, 0))
    }

    /// A category done: its tally set to what its plan purges, and said.
    fn settle(&self, category: &'static str, made: &CategoryPlan) {
        if let Ok(mut found) = self.found.lock() {
            found.insert(category, (made.prunable_count(), made.prunable_size()));
        }
        self.meter(Some(category), "Found", None);
    }

    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }

    /// `Err(CANCELLED)` once the scan is to stop.
    pub fn check(&self) -> Result<(), String> {
        if self.cancelled() { Err(CANCELLED.into()) } else { Ok(()) }
    }

    /// A meter for one step, reporting it has started.
    pub fn meter(&self, category: Option<&'static str>, doing: &'static str, total: Option<usize>) -> Meter<'_> {
        let meter = Meter {
            watch: self,
            category,
            doing,
            total,
            total_bytes: None,
            done: AtomicUsize::new(0),
            bytes: AtomicU64::new(0),
            last: Mutex::new(None),
        };
        meter.send(true);
        meter
    }
}

/// One step's counts, safe to bump from many threads; reported at most
/// every 100 ms, and always at its start and [`finish`](Meter::finish).
pub struct Meter<'a> {
    watch: &'a Watch<'a>,
    category: Option<&'static str>,
    doing: &'static str,
    total: Option<usize>,
    total_bytes: Option<u64>,
    done: AtomicUsize,
    bytes: AtomicU64,
    last: Mutex<Option<Instant>>,
}

impl Meter<'_> {
    /// The bytes there are to read.
    pub fn with_bytes(mut self, total: u64) -> Self {
        self.total_bytes = Some(total);
        self.send(true);
        self
    }

    /// `count` more done, `bytes` more measured.
    pub fn add(&self, count: usize, bytes: u64) {
        self.done.fetch_add(count, Ordering::Relaxed);
        self.bytes.fetch_add(bytes, Ordering::Relaxed);
        self.send(false);
    }

    /// Set the count outright (a walk counting what it has listed).
    pub fn set(&self, done: usize, bytes: u64) {
        self.done.fetch_max(done, Ordering::Relaxed);
        self.bytes.fetch_max(bytes, Ordering::Relaxed);
        self.send(false);
    }

    /// One more thing found to purge, `bytes` big: the category's running
    /// tally, which the window's bars fill up with.
    pub fn found(&self, bytes: u64) {
        let Some(category) = self.category else { return };
        if let Ok(mut found) = self.watch.found.lock() {
            let tally = found.entry(category).or_insert((0, 0));
            tally.0 += 1;
            tally.1 += bytes;
        }
        self.send(false);
    }

    pub fn finish(&self) {
        self.send(true);
    }

    fn send(&self, always: bool) {
        // Held while the counts are read and told, so two threads never
        // tell them out of order.
        let Ok(mut last) = self.last.lock() else { return };
        if !always && last.is_some_and(|at| at.elapsed() < Duration::from_millis(100)) {
            return;
        }
        *last = Some(Instant::now());
        let (found, found_bytes) = self.watch.found(self.category);
        (self.watch.report)(&Progress {
            found,
            found_bytes,
            category: self.category,
            doing: self.doing,
            done: self.done.load(Ordering::Relaxed),
            total: self.total,
            bytes: self.bytes.load(Ordering::Relaxed),
            total_bytes: self.total_bytes,
        });
    }
}

/// [`make_plan`] watched: progress reported, stoppable, its reads
/// `watch.threads` at a time, and the categories at once when
/// `watch.concurrent`. The plan is exactly [`make_plan`]'s.
pub fn make_plan_watched(
    roots: &ProjectRoots,
    settings: &Settings,
    only: Option<&[String]>,
    farm: &dyn Farm,
    progress: &mut dyn FnMut(&str),
    watch: &Watch<'_>,
) -> Result<Plan, String> {
    if let Some(problem) = settings.problem() {
        return Err(problem);
    }
    let picked: Vec<Category> = categories().into_iter().filter(|c| only.is_none_or(|o| o.iter().any(|k| k == c.key))).collect();
    if let Some(only) = only {
        let mut unknown: Vec<&str> = only.iter().map(String::as_str).filter(|k| !CATEGORY_KEYS.contains(k)).collect();
        unknown.sort();
        unknown.dedup();
        if !unknown.is_empty() {
            return Err(format!("no such category: {} (there are {})", unknown.join(", "), CATEGORY_KEYS.join(", ")));
        }
    }
    let mut purposes: Vec<&str> = Vec::new();
    for category in &picked {
        for purpose in category.purposes {
            if !purposes.contains(purpose) {
                purposes.push(purpose);
            }
        }
    }
    let mut inventory = None;
    if !purposes.is_empty() {
        progress("Reading the project …");
        inventory = Some(inventory::scan_watched(roots, &purposes, watch)?);
    }
    // The latest workfiles, read once for every category that reads them.
    // After the listing, not beside it: read beside it they took the
    // share's bandwidth and the listing went from 25 s to 98 s, for 5 s
    // saved in all.
    let keys: Vec<&str> = picked.iter().map(|c| c.key).collect();
    let workfiles = if keys.iter().any(|k| matches!(*k, "exports" | "renders" | "caches")) {
        Some(Workfiles::read(roots, &keys, watch)?)
    } else {
        None
    };
    let mut plan = Plan { roots: roots.clone(), settings: settings.clone(), categories: Vec::new() };
    let now = crate::fsx::now();
    let inventory = inventory.as_ref();
    let empty = Workfiles::default();
    let workfiles = workfiles.as_ref().unwrap_or(&empty);
    let make = |at: usize, category: &Category| -> Result<CategoryPlan, String> {
        watch.check()?;
        watch.meter(Some(category.key), "Scanning", None);
        let made = match category.key {
            "exports" => exports::plan(roots, inventory, settings, workfiles, watch)?,
            "renders" => renders::plan(roots, inventory, settings, now, workfiles, watch)?,
            "caches" => caches::plan(roots, inventory, settings, now, workfiles, watch)?,
            "backups" => backups::plan(roots, inventory, settings, now),
            "jobs" => jobs::plan(roots, settings, farm, now, watch)?,
            _ => deleted::plan(roots, settings, now, watch)?,
        };
        watch.check()?;
        watch.settle(category.key, &made);
        (watch.finished)(at, &made);
        Ok(made)
    };
    let make = &make;
    if watch.concurrent {
        let made: Vec<Result<CategoryPlan, String>> = std::thread::scope(|scope| {
            let running: Vec<_> = picked.iter().enumerate().map(|(at, category)| scope.spawn(move || make(at, category))).collect();
            running.into_iter().map(|run| run.join().unwrap_or_else(|_| Err("a category's scan failed".into()))).collect()
        });
        // Said and taken in order: the first category to fail decides, as
        // one after another would.
        for (category, made) in picked.iter().zip(made) {
            progress(&format!("{} …", category.title));
            plan.categories.push(made?);
        }
    } else {
        for (at, category) in picked.iter().enumerate() {
            progress(&format!("{} …", category.title));
            plan.categories.push(make(at, category)?);
        }
    }
    Ok(plan)
}

/// What a latest workfile says, read once for every category that asks:
/// each category's own reading of its text.
#[derive(Debug, Default)]
pub struct HipFacts {
    pub exports: Option<Result<ExportPins, String>>,
    pub renders: Option<HashSet<renders::Pin>>,
    pub caches: Option<CacheNames>,
}

/// `exports::hip_pins`: the pins and the exact versions a workfile names.
pub type ExportPins = (HashSet<exports::Pin>, HashSet<Vec<String>>);
/// `caches::hip_names`: the `(name, version)` pairs and the paths.
pub type CacheNames = (HashSet<(String, String)>, HashSet<String>);

/// A latest workfile and what reading it gave: None for one that is not
/// a `.hip` (never read), the error line when it could not be read.
#[derive(Debug)]
pub struct WorkfileRead {
    pub workfile: crate::hip::LatestWorkfile,
    pub read: Option<Result<HipFacts, String>>,
}

/// The latest workfiles (`prune_exports.latest_workfiles`), each read
/// once. Reading them is bound by the wire (840 MB of `.hip` on the
/// 100 Mbit/s share took 74 s at 1, 4 or 8 at once), so the three
/// categories that read them share one read instead of three.
#[derive(Debug, Default)]
pub struct Workfiles {
    pub list: Vec<WorkfileRead>,
}

impl Workfiles {
    pub fn read(roots: &ProjectRoots, keys: &[&str], watch: &Watch<'_>) -> Result<Workfiles, String> {
        let latest = crate::hip::latest_workfiles(roots);
        let readable = |w: &crate::hip::LatestWorkfile| {
            w.path.file_name().map(crate::fsx::name_of).unwrap_or_default().to_lowercase().ends_with(".hip")
        };
        let sizes: Vec<u64> = crate::fsx::par_map(&latest, watch.threads, |w| if readable(w) { crate::fsx::lsize(&w.path) } else { 0 });
        let total: usize = latest.iter().filter(|w| readable(w)).count();
        let meter = watch.meter(None, "Reading workfiles", Some(total)).with_bytes(sizes.iter().sum());
        let wants = |key: &str| keys.contains(&key);
        let list = crate::fsx::par_map(&latest, 2, |workfile| {
            if !readable(workfile) || watch.cancelled() {
                return WorkfileRead { workfile: workfile.clone(), read: None };
            }
            let text = crate::hip::read_text_metered(&workfile.path, &|n| {
                meter.add(0, n);
                !watch.cancelled()
            });
            meter.add(1, 0);
            let read = Some(text.map_err(|error| format!("{}: {error}", workfile.path.display())).map(|text| {
                let own = crate::hip::workfile_entity(&workfile.purpose, &workfile.parts);
                HipFacts {
                    exports: wants("exports").then(|| exports::hip_pins(&text, own.as_deref())),
                    renders: wants("renders").then(|| renders::hip_pins(&text, own.as_deref())),
                    caches: wants("caches").then(|| caches::hip_names(&text)),
                }
            }));
            WorkfileRead { workfile: workfile.clone(), read }
        });
        meter.finish();
        watch.check()?;
        Ok(Workfiles { list })
    }
}

/// What deleting a category did: `(removed, bytes, errors)`.
pub type Outcome = (usize, u64, Vec<String>);

/// A purge's control values ([`Steer::control`]): run on, pause before the
/// next item, or stop before it.
pub const RUN: u8 = 0;
pub const PAUSE: u8 = 1;
pub const STOP: u8 = 2;

/// How far a category's purge is: items handled (removed or left with an
/// error), of them removed, and the bytes removed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Removal<'a> {
    pub category: &'a str,
    pub handled: usize,
    pub removed: usize,
    pub bytes: u64,
}

/// A purge steered from outside: paused or stopped between items (never in
/// the middle of one), and told how far it is, at most ten times a second
/// plus each category's last word.
pub struct Steer<'a> {
    pub control: &'a AtomicU8,
    pub report: &'a (dyn Fn(&Removal<'_>) + Sync),
    every: Duration,
    last: Mutex<Option<Instant>>,
}

impl<'a> Steer<'a> {
    pub fn new(control: &'a AtomicU8, report: &'a (dyn Fn(&Removal<'_>) + Sync)) -> Self {
        Steer { control, report, every: Duration::from_millis(100), last: Mutex::new(None) }
    }

    /// Report at most once per `every` (ten a second unless told; zero
    /// reports every item).
    pub fn throttle(mut self, every: Duration) -> Self {
        self.every = every;
        self
    }

    /// Before an item: wait out a pause; `false` once stopped.
    pub fn go(&self) -> bool {
        loop {
            match self.control.load(Ordering::SeqCst) {
                STOP => return false,
                PAUSE => std::thread::sleep(Duration::from_millis(20)),
                _ => return true,
            }
        }
    }

    pub fn stopped(&self) -> bool {
        self.control.load(Ordering::SeqCst) == STOP
    }

    fn tell(&self, removal: &Removal<'_>, always: bool) {
        if let Ok(mut last) = self.last.lock() {
            if !always && last.is_some_and(|at| at.elapsed() < self.every) {
                return;
            }
            *last = Some(Instant::now());
        }
        (self.report)(removal);
    }
}

/// Purge `items` one at a time for `category`: before each, the steer may
/// pause or stop; `one` removes it and logs it (its error is the item's,
/// the outer one the category's: a log that cannot be written).
pub fn each_item<T>(
    steer: &Steer<'_>,
    category: &str,
    items: impl IntoIterator<Item = T>,
    mut one: impl FnMut(T) -> Result<Result<u64, String>, String>,
) -> Result<Outcome, String> {
    let mut removal = Removal { category, handled: 0, removed: 0, bytes: 0 };
    let mut errors = Vec::new();
    for item in items {
        if !steer.go() {
            break;
        }
        match one(item)? {
            Ok(size) => {
                removal.removed += 1;
                removal.bytes += size;
            }
            Err(error) => errors.push(error),
        }
        removal.handled += 1;
        steer.tell(&removal, false);
    }
    steer.tell(&removal, true);
    Ok((removal.removed, removal.bytes, errors))
}

/// Delete what `plan` marks to go; per category what was deleted. A
/// category that fails outright is reported as its error and the others
/// still run.
pub fn apply(plan: &Plan, log_path: &Path) -> Vec<(String, Outcome)> {
    let run = AtomicU8::new(RUN);
    apply_steered(plan, log_path, &Steer::new(&run, &|_| {}))
}

/// [`apply`], paused, stopped and told by `steer`: a stop ends the item
/// under way, then the purge; the categories it never reached are untouched
/// (no log header either).
pub fn apply_steered(plan: &Plan, log_path: &Path, steer: &Steer<'_>) -> Vec<(String, Outcome)> {
    let mut results = Vec::new();
    for category in &plan.categories {
        if category.prunable_count() == 0 || steer.stopped() {
            results.push((category.key.clone(), (0, 0, Vec::new())));
            continue;
        }
        let outcome = match category.key.as_str() {
            "exports" => exports::apply(category, log_path, steer),
            "renders" => renders::apply(category, log_path, steer),
            "caches" => caches::apply(category, log_path, steer),
            "backups" => backups::apply(category, log_path, steer),
            "jobs" => jobs::apply(category, log_path, steer),
            _ => deleted::apply(category, log_path, steer),
        };
        let outcome = outcome.unwrap_or_else(|error| (0, 0, vec![format!("{}: {error}", category.title)]));
        results.push((category.key.clone(), outcome));
    }
    results
}

/// Open the log to append to, its folder made.
pub fn open_log(log_path: &Path) -> Result<std::fs::File, String> {
    if let Some(parent) = log_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("OSError: {e}"))?;
    }
    std::fs::OpenOptions::new().create(true).append(true).open(log_path).map_err(|e| format!("OSError: {e}"))
}

/// Write `line` and a line end to `log`.
pub fn log_line(log: &mut std::fs::File, line: &str) -> Result<(), String> {
    log.write_all(format!("{line}{NEWLINE}").as_bytes()).map_err(|e| format!("OSError: {e}"))
}

/// Delete `path`: a folder and all in it, else the file or link.
pub fn remove(path: &Path) -> std::io::Result<()> {
    let meta = std::fs::symlink_metadata(path)?;
    if meta.is_dir() { std::fs::remove_dir_all(path) } else { std::fs::remove_file(path) }
}

/// Delete the entries marked to go, each only if `still_ok` finds no
/// problem with it now; log each one removed.
pub fn delete_entries(
    category: &CategoryPlan,
    log_path: &Path,
    steer: &Steer<'_>,
    still_ok: impl Fn(&Entry) -> Option<String>,
) -> Result<Outcome, String> {
    let mut log = open_log(log_path)?;
    log_line(&mut log, &format!("# cleanup {} {} by {}", category.key, stamp_now(), user()))?;
    each_item(steer, &category.key, category.prunable(), |entry| {
        if let Some(problem) = still_ok(entry) {
            return Ok(Err(format!("{}: {problem}, left alone", entry.path.display())));
        }
        if let Err(error) = remove(&entry.path) {
            return Ok(Err(format!("{}: {error}", entry.path.display())));
        }
        log_line(&mut log, &format!("{}\t{}\t{}", entry.label, entry.size, entry.reason))?;
        Ok(Ok(entry.size))
    })
}

/// The listing as lines: per category what goes and what stays by reason,
/// then the biggest groups to go (`core.summary`).
pub fn summary(plan: &Plan, detail: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for category in &plan.categories {
        lines.push(format!(
            "{}: {} {}(s), {} to delete ({})",
            category.title,
            category.entries.len(),
            category.noun,
            category.prunable_count(),
            human(category.prunable_size())
        ));
        let mut by_reason: Vec<((bool, String), (usize, u64))> = Vec::new();
        for entry in &category.entries {
            let key = (entry.prune, entry.reason.clone());
            match by_reason.iter_mut().find(|(k, _)| *k == key) {
                Some((_, tally)) => {
                    tally.0 += 1;
                    tally.1 += entry.size;
                }
                None => by_reason.push((key, (1, entry.size))),
            }
        }
        by_reason.sort_by(|((pa, ra), _), ((pb, rb), _)| (!pa, ra).cmp(&(!pb, rb)));
        for ((prune, reason), (count, size)) in &by_reason {
            lines.push(format!(
                "  {}  {}  {}  {reason}",
                left(if *prune { "delete" } else { "keep" }, 6),
                right(&count.to_string(), 6),
                right(&human(*size), 10)
            ));
        }
        lines.extend(category.notes.iter().map(|note| format!("  {note}")));
        let mut per_group: Vec<(String, usize, u64)> = Vec::new();
        for entry in category.prunable() {
            match per_group.iter_mut().find(|(g, _, _)| *g == entry.group) {
                Some(tally) => {
                    tally.1 += 1;
                    tally.2 += entry.size;
                }
                None => per_group.push((entry.group.clone(), 1, entry.size)),
            }
        }
        // `sorted(..., key=-size)`: stable, biggest first.
        per_group.sort_by_key(|g| std::cmp::Reverse(g.2));
        if !per_group.is_empty() {
            lines.push("  To delete, biggest first:".into());
            for (group, count, size) in per_group.iter().take(detail) {
                lines.push(format!("    {}  {}  {group}", right(&human(*size), 10), right(&count.to_string(), 5)));
            }
            if per_group.len() > detail {
                let rest = &per_group[detail..];
                let size: u64 = rest.iter().map(|g| g.2).sum();
                let count: usize = rest.iter().map(|g| g.1).sum();
                lines.push(format!("    {}  {}  ({} more)", right(&human(size), 10), right(&count.to_string(), 5), rest.len()));
            }
        }
        for error in category.errors.iter().take(20) {
            lines.push(format!("  ! {error}"));
        }
        lines.push(String::new());
    }
    lines.push(format!("In all: {} to delete, {}.", plan.prunable_count(), human(plan.prunable_size())));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_problems() {
        assert_eq!(Settings::default().problem(), None);
        let bad = Settings { backup_days: -1.0, ..Settings::default() };
        assert_eq!(bad.problem().as_deref(), Some("backup days cannot be negative"));
        let bad = Settings { context: Some("edits/x".into()), ..Settings::default() };
        assert!(bad.problem().is_some());
        let fine = Settings { context: Some("/shots/sq010/".into()), ..Settings::default() };
        assert_eq!(fine.problem(), None);
    }

    #[test]
    fn contexts() {
        assert!(under_context("shots/a/b", Some("shots/a")));
        assert!(under_context("shots/a", Some("/shots/a/")));
        assert!(!under_context("shots/ab", Some("shots/a")));
        assert!(under_context("x", None));
        assert!(under_context("x", Some("")));
    }
}
