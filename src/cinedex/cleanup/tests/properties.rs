//! Properties of the cleanup's rules that need no Python to state (the
//! parity with Python is `tests/test_cleanup_parity.py` at the repo root).
//! Every case is drawn directly: nothing is discarded.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::time::Duration;

use chrono::{Local, TimeZone};
use proptest::prelude::*;
use th_cleanup::core::{CategoryPlan, Entry, Farm, Plan, Progress, Settings, Watch};
use th_cleanup::roots::ProjectRoots;
use th_cleanup::{backups, core, deleted, fsx, jobs, overview, py};

fn stem() -> impl Strategy<Value = String> {
    "[a-zA-Z0-9_]{1,10}"
}

proptest! {
    /// A backup's name says its workfile version and number, whatever the
    /// extension's case.
    #[test]
    fn backup_names_read_back(stem in stem(), version in 0u32..200_000, number in 0u32..1000, ext in 0usize..3, upper in any::<bool>()) {
        let ext = ["hip", "hiplc", "hipnc"][ext];
        let ext = if upper { ext.to_uppercase() } else { ext.to_string() };
        let name = format!("{stem}_v{version:04}_bak{number}.{ext}");
        prop_assert_eq!(backups::backup_of(&name), Some((i128::from(version), i128::from(number))));
        // One character less of the version is not a backup's name.
        let short = format!("{stem}_v{:03}_bak{number}.{ext}", version % 1000);
        prop_assert_eq!(backups::backup_of(&short), None);
    }

    /// A `_deleted` folder's stamp reads back as the moment it names.
    #[test]
    fn stamps_read_back(seconds in 100_000_000i64..4_000_000_000) {
        let at = Local.timestamp_opt(seconds, 0).single().expect("a time");
        let name = at.format("%Y%m%d-%H%M%S").to_string();
        let read = deleted::stamp_time(&name).expect("a stamp");
        let back = Local.timestamp_opt(read as i64, 0).single().expect("a time").format("%Y%m%d-%H%M%S").to_string();
        prop_assert_eq!(back, name);
    }

    /// Deadline's listing gives every job's statuses under each folder it
    /// names, in the order listed, however the blocks are separated.
    #[test]
    fn job_listings_name_their_folders(jobs_drawn in proptest::collection::vec((0u128..u128::MAX, 0usize..4, any::<bool>()), 0..8), crlf in any::<bool>()) {
        let statuses = ["Completed", "Suspended", "Queued", "Failed"];
        let newline = if crlf { "\r\n" } else { "\n" };
        let mut text = String::new();
        let mut expected: HashMap<String, Vec<String>> = HashMap::new();
        for (bits, status, windows) in &jobs_drawn {
            let uuid = format!("{bits:032x}");
            let uuid = format!("{}-{}-{}-{}-{}", &uuid[..8], &uuid[8..12], &uuid[12..16], &uuid[16..20], &uuid[20..]);
            let sep = if *windows { "\\" } else { "/" };
            text += &format!("JobStatus={}{newline}PluginInfoDictionary=StartupDirectory=P:{sep}other{sep}jobs{sep}{}{sep}{newline}{newline}", statuses[*status], uuid.to_uppercase());
            expected.entry(uuid).or_default().push(statuses[*status].to_string());
        }
        // `text=True` reads `\r\n` as `\n`: so does the live query.
        let text = text.replace("\r\n", "\n");
        prop_assert_eq!(jobs::parse_jobs(&text), expected);
    }

    /// `{:,}` and back.
    #[test]
    fn thousands_read_back(n in any::<u64>()) {
        let text = py::thousands(n as usize);
        prop_assert_eq!(text.replace(',', ""), n.to_string());
        prop_assert!(text.split(',').skip(1).all(|group| group.len() == 3));
    }

    /// The overview's totals are the plan's, and the reasons and groups
    /// split it exactly, deleting first and biggest first.
    #[test]
    fn overviews_add_up(drawn in proptest::collection::vec(proptest::collection::vec((0usize..5, 0u64..1 << 40, any::<bool>(), 0usize..3), 0..30), 0..6)) {
        let mut plan = Plan { roots: empty_roots(), settings: Settings::default(), categories: Vec::new() };
        for (index, entries) in drawn.iter().enumerate() {
            let mut category = CategoryPlan::new(&format!("c{index}"), "Title", "version");
            for (number, (group, size, prune, reason)) in entries.iter().enumerate() {
                category.entries.push(Entry {
                    path: format!("/p/{number}").into(),
                    label: format!("g{group}/v{number:04}"),
                    group: format!("g{group}"),
                    size: *size,
                    prune: *prune,
                    reason: format!("r{reason}"),
                });
            }
            plan.categories.push(category);
        }
        let view = overview::overview(&plan);
        prop_assert_eq!(view.to_delete(), plan.prunable_count());
        prop_assert_eq!(view.size(), plan.prunable_size());
        for (category, source) in view.categories.iter().zip(&plan.categories) {
            prop_assert_eq!(category.reasons.iter().map(|r| r.count).sum::<usize>(), source.entries.len());
            prop_assert_eq!(category.groups.iter().map(|g| g.size).sum::<u64>(), source.prunable_size());
            prop_assert!(category.reasons.windows(2).all(|w| (!w[0].prune, std::cmp::Reverse(w[0].size)) <= (!w[1].prune, std::cmp::Reverse(w[1].size))));
            prop_assert!(category.groups.windows(2).all(|w| w[0].size >= w[1].size));
        }
    }

    /// A `_deleted` stamp folder goes exactly when its delete is old enough,
    /// anything else there stays, and applying deletes exactly what goes.
    #[test]
    fn deleted_folders_go_by_age(ages in proptest::collection::vec(0u32..90, 0..6), junk in any::<bool>(), days in 1u32..60) {
        let home = tempfile::tempdir().expect("a folder");
        let project = home.path().join("Proj");
        let config = project.join("_config");
        std::fs::create_dir_all(&config).expect("config");
        std::fs::write(config.join("storage.json"), include_bytes!("../../../../scripts/project_template/_config/storage.json")).expect("storage");
        std::fs::write(config.join("version.json"), br#"{"version": 22}"#).expect("version");
        let holder = project.join("shots").join("_deleted");
        let now = Local::now().timestamp();
        let mut expected = HashSet::new();
        for (index, age) in ages.iter().enumerate() {
            // Whole days plus an hour and the index, never on the threshold.
            let at = now - i64::from(*age) * 86_400 - 3_600 - index as i64 * 7;
            let name = Local.timestamp_opt(at, 0).single().expect("a time").format("%Y%m%d-%H%M%S").to_string();
            std::fs::create_dir_all(holder.join(&name).join("x")).expect("stamp");
            std::fs::write(holder.join(&name).join("x").join("f"), b"data").expect("file");
            if *age >= days {
                expected.insert(name);
            }
        }
        if junk {
            std::fs::create_dir_all(holder.join("notastamp")).expect("junk");
            std::fs::write(holder.join("loose.txt"), b"x").expect("junk");
        }
        let roots = ProjectRoots::open(&project).expect("roots");
        let settings = Settings { deleted_days: f64::from(days), ..Settings::default() };
        let plan = core::make_plan(&roots, &settings, Some(&["deleted".to_string()]), &NoFarm, &mut |_| {}).expect("a plan");
        let going: HashSet<String> = plan.categories[0].prunable().map(|e| e.label.rsplit('/').next().unwrap_or("").to_string()).collect();
        prop_assert_eq!(&going, &expected);
        let log = home.path().join("log.txt");
        let results = core::apply(&plan, &log);
        prop_assert_eq!(results[0].1 .0, expected.len());
        let left: HashSet<String> = std::fs::read_dir(&holder).map(|r| r.filter_map(Result::ok).map(|e| e.file_name().to_string_lossy().into_owned()).collect()).unwrap_or_default();
        prop_assert!(expected.iter().all(|name| !left.contains(name)));
        prop_assert_eq!(left.len(), ages.len() - expected.len() + if junk { 2 } else { 0 });
    }

    /// A purge stopped after `k` items removes and logs exactly the first
    /// `k` (in the plan's order), leaves the rest on disk, and its reports
    /// only rise, every item told.
    #[test]
    fn stopped_purges_end_between_items(n in 1usize..8, k in 0usize..9) {
        let home = tempfile::tempdir().expect("a folder");
        let (project, holder, names) = old_stamps(home.path(), n);
        let roots = ProjectRoots::open(&project).expect("roots");
        let plan = core::make_plan(&roots, &Settings::default(), Some(&["deleted".to_string()]), &NoFarm, &mut |_| {}).expect("a plan");
        let order: Vec<String> = plan.categories[0].prunable().map(|e| e.label.rsplit('/').next().unwrap_or("").to_string()).collect();
        prop_assert_eq!(order.iter().cloned().collect::<HashSet<_>>(), names.iter().cloned().collect::<HashSet<_>>());
        let control = AtomicU8::new(core::RUN);
        let told = Mutex::new(Vec::new());
        let report = |r: &core::Removal<'_>| {
            told.lock().unwrap().push((r.handled, r.removed, r.bytes));
            if r.handled >= k {
                control.store(core::STOP, Ordering::SeqCst);
            }
        };
        if k == 0 {
            control.store(core::STOP, Ordering::SeqCst);
        }
        let log = home.path().join("log.txt");
        let results = core::apply_steered(&plan, &log, &core::Steer::new(&control, &report).throttle(Duration::ZERO));
        let gone = k.min(n);
        prop_assert_eq!(results[0].1 .0, gone);
        for (at, name) in order.iter().enumerate() {
            prop_assert_eq!(holder.join(name).exists(), at >= gone, "{} at {}", name, at);
        }
        let logged: Vec<String> = std::fs::read_to_string(&log)
            .unwrap_or_default()
            .lines()
            .filter(|l| !l.starts_with('#') && !l.is_empty())
            .map(|l| l.split('\t').next().unwrap_or("").rsplit('/').next().unwrap_or("").to_string())
            .collect();
        prop_assert_eq!(&logged, &order[..gone].to_vec());
        let told = told.into_inner().unwrap();
        prop_assert!(told.windows(2).all(|w| w[1].0 >= w[0].0 && w[1].2 >= w[0].2), "{:?}", told);
        if gone > 0 {
            prop_assert_eq!(told.last().map(|t| t.0), Some(gone));
        }
    }

    /// Listing a tree many folders at once gives exactly the files and
    /// errors of listing it one folder at a time, in the same order.
    #[test]
    fn parallel_walks_match(files in proptest::collection::vec(drawn_path(), 0..40), threads in 2usize..9) {
        let home = tempfile::tempdir().expect("a folder");
        let root = home.path().join("root");
        std::fs::create_dir_all(&root).expect("root");
        for (parts, _) in &files {
            write_drawn(&root, parts, b"x");
        }
        let skip = vec![root.join("b")];
        let mut one_errors = Vec::new();
        let one = fsx::walk_files(&root, &skip, &mut one_errors);
        let mut many_errors = Vec::new();
        let never = AtomicBool::new(false);
        let many = fsx::walk_files_threads(&root, &skip, &mut many_errors, threads, &never, &|_, _| {});
        prop_assert_eq!(one, many);
        prop_assert_eq!(one_errors, many_errors);
    }

    /// A scan with its reads in parallel and its categories at once makes
    /// exactly the plan of one read and one category at a time, and its
    /// progress never goes back within a step.
    #[test]
    fn parallel_scans_match(drawn in proptest::collection::vec((0usize..6, drawn_path(), 0usize..4), 0..40), threads in 2usize..9) {
        let home = tempfile::tempdir().expect("a folder");
        let project = home.path().join("Proj");
        let config = project.join("_config");
        std::fs::create_dir_all(&config).expect("config");
        std::fs::write(config.join("storage.json"), include_bytes!("../../../../scripts/project_template/_config/storage.json")).expect("storage");
        std::fs::write(config.join("version.json"), br#"{"version": 22}"#).expect("version");
        let roots_at = [
            project.join("shots"),
            project.join("assets"),
            project.join("export"),
            home.path().join("Proj_render"),
            project.join("export").join("other").join("jobs"),
            project.join("shots").join("_deleted"),
        ];
        let contents: [&[u8]; 4] = [
            b"x",
            br#"{"first_frame": 1, "last_frame": 2, "kind": "cache"}"#,
            b"version\t[ 0\tlocks=0 ]\t(\tv0002\t)\nentity\t[ 0\tlocks=0 ]\t(\t\"entity:/shots/s/h\"\t)\n",
            b"#usda 1.0\n@../v0001/a.usda@\n",
        ];
        for (root, (parts, _), content) in &drawn {
            write_drawn(&roots_at[*root], parts, contents[*content]);
        }
        let roots = ProjectRoots::open(&project).expect("roots");
        let settings = Settings { keep_exports: 1, keep_renders: 1, keep_caches: 1, keep_backups: 1, ..Settings::default() };
        let never = AtomicBool::new(false);
        let one_watch = Watch::new(1, false, &never, &|_| {}, &|_, _| {});
        let one = core::make_plan_watched(&roots, &settings, None, &NoFarm, &mut |_| {}, &one_watch);
        let seen: Mutex<HashMap<Step, (usize, u64)>> = Mutex::new(HashMap::new());
        let backwards = AtomicBool::new(false);
        let found: Mutex<HashMap<&str, (usize, u64)>> = Mutex::new(HashMap::new());
        let report = |p: &Progress| {
            let mut seen = seen.lock().unwrap();
            let last = seen.entry((p.category, p.doing)).or_insert((0, 0));
            if p.done < last.0 || p.bytes < last.1 {
                backwards.store(true, std::sync::atomic::Ordering::Relaxed);
            }
            *last = (p.done, p.bytes);
            // What a category has found only grows; its last word is its plan's.
            if let Some(key) = p.category {
                let mut found = found.lock().unwrap();
                let tally = found.entry(key).or_insert((0, 0));
                if p.found < tally.0 || p.found_bytes < tally.1 {
                    backwards.store(true, std::sync::atomic::Ordering::Relaxed);
                }
                *tally = (p.found, p.found_bytes);
            }
        };
        let finished = Mutex::new(Vec::new());
        let on_finished = |at: usize, _: &CategoryPlan| finished.lock().unwrap().push(at);
        let many_watch = Watch::new(threads, true, &never, &report, &on_finished);
        let many = core::make_plan_watched(&roots, &settings, None, &NoFarm, &mut |_| {}, &many_watch);
        prop_assert_eq!(one.map(|p| p.categories), many.as_ref().map(|p| p.categories.clone()).map_err(Clone::clone));
        prop_assert!(!backwards.load(std::sync::atomic::Ordering::Relaxed));
        if let Ok(plan) = &many {
            let found = found.lock().unwrap();
            for category in &plan.categories {
                let key = core::CATEGORY_KEYS.iter().find(|k| **k == category.key).copied().unwrap_or_default();
                prop_assert_eq!(found.get(key).copied(), Some((category.prunable_count(), category.prunable_size())), "{}", key);
            }
        }
        if many.is_ok() {
            let mut finished = finished.into_inner().unwrap();
            finished.sort();
            prop_assert_eq!(finished, (0..6).collect::<Vec<_>>());
        }
    }

    /// A cancelled scan stops with the cancelled error, never a plan.
    #[test]
    fn cancelled_scans_make_no_plan(drawn in proptest::collection::vec(drawn_path(), 0..20), concurrent in any::<bool>()) {
        let home = tempfile::tempdir().expect("a folder");
        let project = home.path().join("Proj");
        let config = project.join("_config");
        std::fs::create_dir_all(&config).expect("config");
        std::fs::write(config.join("storage.json"), include_bytes!("../../../../scripts/project_template/_config/storage.json")).expect("storage");
        std::fs::write(config.join("version.json"), br#"{"version": 22}"#).expect("version");
        for (parts, _) in &drawn {
            write_drawn(&project.join("export"), parts, b"x");
        }
        let roots = ProjectRoots::open(&project).expect("roots");
        let cancel = AtomicBool::new(true);
        let watch = Watch::new(4, concurrent, &cancel, &|_| {}, &|_, _| {});
        let plan = core::make_plan_watched(&roots, &Settings::default(), None, &NoFarm, &mut |_| {}, &watch);
        prop_assert_eq!(plan.err(), Some(core::CANCELLED.to_string()));
    }
}

proptest! {
    /// A purge over every category stopped after `k` items in all handles
    /// exactly `min(k, all)`: the category under way ends after its item,
    /// and every category after it is left untouched, its outcome empty
    /// and no log header written for it.
    #[test]
    fn a_stop_leaves_later_categories_untouched(
        stamps in 1usize..5,
        backups in 2usize..6,
        drawn in proptest::collection::vec((0usize..6, drawn_path(), 0usize..4), 0..30),
        k in 0usize..12,
    ) {
        let home = tempfile::tempdir().expect("a folder");
        let (project, _, _) = old_stamps(home.path(), stamps);
        // Backups of one workfile version, all but the newest to go, so at
        // least two categories (backups, then deleted) have items.
        let backup = project.join("shots").join("s").join("h").join("lighting").join("backup");
        std::fs::create_dir_all(&backup).expect("backup folder");
        for number in 1..=backups {
            std::fs::write(backup.join(format!("h_v0001_bak{number}.hip")), b"x").expect("backup");
        }
        let roots_at = [
            project.join("shots"),
            project.join("assets"),
            project.join("export"),
            home.path().join("Proj_render"),
            project.join("export").join("other").join("jobs"),
            project.join("shots").join("_deleted"),
        ];
        let contents: [&[u8]; 4] = [
            b"x",
            br#"{"first_frame": 1, "last_frame": 2, "kind": "cache"}"#,
            b"version\t[ 0\tlocks=0 ]\t(\tv0002\t)\nentity\t[ 0\tlocks=0 ]\t(\t\"entity:/shots/s/h\"\t)\n",
            b"#usda 1.0\n@../v0001/a.usda@\n",
        ];
        for (root, (parts, _), content) in &drawn {
            // A context that is not JSON fails the whole scan (as Python's
            // does), so a .json file is always JSON here.
            // A pyvenv.cfg makes its folders an environment, hiding the
            // seeded backups.
            if parts.last().is_some_and(|name| name == "pyvenv.cfg") {
                continue;
            }
            let json = parts.last().is_some_and(|name| name.ends_with(".json"));
            write_drawn(&roots_at[*root], parts, contents[if json { 1 } else { *content }]);
        }
        let roots = ProjectRoots::open(&project).expect("roots");
        let settings =
            Settings { keep_exports: 1, keep_renders: 1, keep_caches: 1, keep_backups: 1, backup_days: 0.0, ..Settings::default() };
        let plan = core::make_plan(&roots, &settings, None, &NoFarm, &mut |_| {}).expect("a plan");
        prop_assert!(plan.categories.iter().filter(|c| c.prunable_count() > 0).count() >= 2);
        let all: usize = plan.categories.iter().map(CategoryPlan::prunable_count).sum();
        let control = AtomicU8::new(if k == 0 { core::STOP } else { core::RUN });
        let told: Mutex<HashMap<String, usize>> = Mutex::new(HashMap::new());
        let report = |r: &core::Removal<'_>| {
            let mut told = told.lock().unwrap();
            told.insert(r.category.to_string(), r.handled);
            if told.values().sum::<usize>() >= k {
                control.store(core::STOP, Ordering::SeqCst);
            }
        };
        let log = home.path().join("log.txt");
        let results = core::apply_steered(&plan, &log, &core::Steer::new(&control, &report).throttle(Duration::ZERO));
        let told = told.into_inner().unwrap();
        prop_assert_eq!(told.values().sum::<usize>(), k.min(all), "{:?}", told);
        let headers: HashSet<String> = std::fs::read_to_string(&log)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| l.strip_prefix("# cleanup ")?.split(' ').next().map(String::from))
            .collect();
        // The last category the purge reached; none after it was touched.
        let reached = plan.categories.iter().rposition(|c| told.get(&c.key).is_some_and(|n| *n > 0));
        for (at, (category, (key, outcome))) in plan.categories.iter().zip(&results).enumerate() {
            prop_assert_eq!(&category.key, key);
            prop_assert!(outcome.0 + outcome.2.len() <= category.prunable_count(), "{}: {:?}", key, outcome);
            if reached.is_none_or(|r| at > r) {
                prop_assert_eq!(outcome, &(0, 0, Vec::new()), "{} after the stop", key);
                prop_assert!(!headers.contains(key), "{} logged after the stop", key);
            }
        }
    }
}

/// A path of a few folders from a small pool (so they share folders),
/// ending in a file name; the bool is unused room for callers.
fn drawn_path() -> impl Strategy<Value = (Vec<String>, bool)> {
    let folder =
        prop::sample::select(vec!["a", "b", "s", "h", "v0001", "v0002", "v0003", "render", "_staged", "backup", "cache", "lighting"]);
    let file = prop::sample::select(vec![
        "context.json",
        "a.usda",
        "f.exr",
        "h_v0001.hip",
        "h_v0002.hip",
        "h_v0003.hiplc",
        "x_v0002.mp4",
        "noise.tmp",
        "pyvenv.cfg",
    ]);
    (proptest::collection::vec(folder, 0..4), file, any::<bool>()).prop_map(|(folders, file, flag)| {
        let mut parts: Vec<String> = folders.into_iter().map(String::from).collect();
        parts.push(file.to_string());
        (parts, flag)
    })
}

/// Write `content` at `parts` under `root`, unless a file is in the way.
fn write_drawn(root: &Path, parts: &[String], content: &[u8]) {
    let mut path = root.to_path_buf();
    path.extend(parts);
    if path.parent().is_some_and(|parent| std::fs::create_dir_all(parent).is_ok()) && !path.is_dir() {
        let _ = std::fs::write(&path, content);
    }
}

/// A project under `home` with `n` `_deleted` stamp folders, each months
/// old (so all go): the project, the `_deleted` folder and the names.
fn old_stamps(home: &Path, n: usize) -> (std::path::PathBuf, std::path::PathBuf, Vec<String>) {
    let project = home.join("Proj");
    let config = project.join("_config");
    std::fs::create_dir_all(&config).expect("config");
    std::fs::write(config.join("storage.json"), include_bytes!("../../../../scripts/project_template/_config/storage.json"))
        .expect("storage");
    std::fs::write(config.join("version.json"), br#"{"version": 22}"#).expect("version");
    let holder = project.join("shots").join("_deleted");
    let now = Local::now().timestamp();
    let names: Vec<String> = (0..n)
        .map(|index| {
            let at = now - 400 * 86_400 - index as i64 * 3_601;
            let name = Local.timestamp_opt(at, 0).single().expect("a time").format("%Y%m%d-%H%M%S").to_string();
            std::fs::create_dir_all(holder.join(&name)).expect("stamp");
            std::fs::write(holder.join(&name).join("f"), vec![b'x'; index + 1]).expect("file");
            name
        })
        .collect();
    (project, holder, names)
}

/// A purge paused before it starts removes nothing while paused, however
/// long, and goes on to remove everything once resumed.
#[test]
fn paused_purges_hold_then_resume() {
    let home = tempfile::tempdir().expect("a folder");
    let (project, holder, names) = old_stamps(home.path(), 4);
    let roots = ProjectRoots::open(&project).expect("roots");
    let plan = core::make_plan(&roots, &Settings::default(), Some(&["deleted".to_string()]), &NoFarm, &mut |_| {}).expect("a plan");
    let control = AtomicU8::new(core::PAUSE);
    let told = Mutex::new(0usize);
    let log = home.path().join("log.txt");
    std::thread::scope(|s| {
        let purge = s.spawn(|| {
            let report = |_: &core::Removal<'_>| *told.lock().unwrap() += 1;
            core::apply_steered(&plan, &log, &core::Steer::new(&control, &report).throttle(Duration::ZERO))
        });
        for _ in 0..5 {
            std::thread::sleep(Duration::from_millis(60));
            assert!(names.iter().all(|n| holder.join(n).exists()), "nothing goes while paused");
            assert_eq!(*told.lock().unwrap(), 0);
        }
        control.store(core::RUN, Ordering::SeqCst);
        let results = purge.join().expect("the purge");
        assert_eq!(results.iter().find(|(k, _)| k == "deleted").map(|(_, o)| o.0), Some(4));
    });
    assert!(names.iter().all(|n| !holder.join(n).exists()), "all gone once resumed");
}

fn empty_roots() -> ProjectRoots {
    ProjectRoots { project_path: Path::new("").into(), config_path: Path::new("").into(), roots: Vec::new() }
}

/// A progress step: its category and what it is doing.
type Step = (Option<&'static str>, &'static str);

struct NoFarm;

impl Farm for NoFarm {
    fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String> {
        Err("no farm".into())
    }
    fn local_folders(&self) -> Result<HashSet<String>, String> {
        Ok(HashSet::new())
    }
}
