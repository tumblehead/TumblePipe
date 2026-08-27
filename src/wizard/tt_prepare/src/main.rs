//! `tt_prepare` — bring a project's `_config` forward at launch time.
//!
//! ## Why this hook
//!
//! Migrations reach other studios through nothing at all today. `migrate-config`
//! carries a `label`, which makes it a *button* in the desktop's per-project
//! Scripts panel that somebody has to notice and click, and the automatic entry
//! point in the Python module had no callers. So a studio upgrading to a version
//! with a new migration keeps its old `_config` until it happens to click, and
//! nothing tells it otherwise.
//!
//! No TumbleTrove hook fires on *upgrade*: `tt_install` is per-project rather
//! than per-version and does not re-fire, and `tt_setup` is driven by unfilled
//! required vars or the Configure button. `tt_prepare` runs on every
//! `launch_project`, which makes it the only hook that reliably runs after a
//! package upgrade.
//!
//! ## The contract this must honour
//!
//! * **One JSON object on stdout, everything else on stderr.** hpm reads stdout
//!   through a pipe; anything else there is a malformed payload. This is a
//!   console-subsystem binary for the same reason `tt_setup` is — a `windows`
//!   subsystem build would detach stdout.
//! * **Never refuse a launch.** A non-zero exit or bad JSON is logged and the
//!   launch proceeds, so failing loudly here buys nothing. Every path below
//!   exits 0 with an empty payload; problems go to stderr, where the desktop
//!   log keeps them.
//! * **Never open a window where nobody can answer it.** The hook runs on every
//!   launch, including any automated one, and the hook contract documents no
//!   timeout — a modal on a headless host would hang the launch indefinitely.
//! * **Never migrate a shared project twice at once.** `_config` lives on a
//!   studio share and every artist's launch runs this, so the migration itself
//!   is taken under a lock.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use th_project_core::migration::{
    self, config_dir, current_version, latest_version, pending, Readiness, Report, Step,
};

/// Status colours matching tt_setup, chosen to contrast on both themes.
const STATUS_ERROR: egui::Color32 = egui::Color32::from_rgb(0xe0, 0x6c, 0x75);
const STATUS_OK: egui::Color32 = egui::Color32::from_rgb(0x98, 0xc3, 0x79);

/// A stale lock must not wedge the studio, so one is ignored past this age.
const LOCK_STALE_AFTER: Duration = Duration::from_secs(15 * 60);

fn main() -> std::process::ExitCode {
    // `--migrate` is the CLI face of the same core: no window, a readable
    // report on stdout, and a non-zero exit when it fails. It exists so a
    // person or a script can migrate deliberately (one project or a hundred)
    // without the launch-hook contract getting in the way — the hook path
    // below must never fail a launch, which is exactly wrong for a CLI.
    let parsed = args();
    if parsed.migrate {
        return match run_cli(&parsed) {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(reason) => {
                eprintln!("tt_prepare: {reason}");
                std::process::ExitCode::FAILURE
            }
        };
    }

    // Whatever happens, say "no env var changes" and exit clean: this hook must
    // never be the reason a launch fails.
    let payload = "{\"envVars\":{}}";

    if let Err(reason) = run(&parsed) {
        eprintln!("tt_prepare: {reason}");
    }

    println!("{payload}");
    std::process::ExitCode::SUCCESS
}

/// `tt_prepare --migrate [--dry-run] [<project>]` — headless, for a person or a
/// bulk script. Falls back to `$TH_PROJECT_PATH` when no path is given, so it
/// matches how the desktop's Scripts panel runs things.
fn run_cli(parsed: &Args) -> Result<(), String> {
    let dry_run = parsed.dry_run;
    let project = match &parsed.project {
        Some(path) => path.clone(),
        None => project_path()?,
    };
    let template = template_dir_from(parsed);

    let at = current_version(&project);
    let steps = pending(&project);
    if steps.is_empty() {
        println!("{}: already at _config v{at}", project.display());
        return Ok(());
    }

    println!("{}: v{at} -> v{}", project.display(), latest_version());
    for (step, readiness) in migration::preflight(&project, &template) {
        match readiness {
            Readiness::Ready => println!("  v{}: {}", step.version(), step.description()),
            Readiness::Blocked(why) => {
                println!("  v{}: BLOCKED — {why}", step.version());
            }
        }
    }

    if dry_run {
        println!("dry run - nothing written");
        return Ok(());
    }

    let _lock = Lock::acquire(&project)?;
    let report = migration::migrate(&project, &template);
    if let Some((step, why)) = &report.refused {
        return Err(format!(
            "refused before writing anything - v{} cannot run: {why}",
            step.version()
        ));
    }
    for (step, result) in &report.applied {
        match result {
            Ok(()) => println!("  applied v{}", step.version()),
            Err(why) => return Err(format!("failed at v{}: {why}", step.version())),
        }
    }
    println!("now at _config v{}", report.to);
    Ok(())
}

fn run(parsed: &Args) -> Result<(), String> {
    let project = project_path()?;
    let template = template_dir_from(parsed);

    // The common case, and the one that must stay cheap: read one small file,
    // compare an integer, and return before egui is ever touched.
    let steps = pending(&project);
    if steps.is_empty() {
        return Ok(());
    }

    let at = current_version(&project);
    eprintln!(
        "tt_prepare: {} is at _config v{at}, {} step(s) behind v{}",
        project.display(),
        steps.len(),
        latest_version()
    );

    if let Some(reason) = non_interactive_reason() {
        eprintln!(
            "tt_prepare: {reason} — not opening the migration window. \
             Run 'Migrate Project Config' from the launcher's Scripts panel."
        );
        return Ok(());
    }

    let blocker = migration::first_blocker(&project, &template);
    let plan = Plan {
        project,
        template,
        from: at,
        to: latest_version(),
        steps,
        blocker,
    };
    show(plan)
}

/// `TH_PROJECT_PATH` is a required `[runtime]` var, so the hook environment has
/// it whenever a project is actually being launched.
fn project_path() -> Result<PathBuf, String> {
    match std::env::var("TH_PROJECT_PATH") {
        Ok(value) if !value.trim().is_empty() => Ok(PathBuf::from(value)),
        _ => Err("TH_PROJECT_PATH is unset — nothing to migrate".to_string()),
    }
}

/// The command line, parsed once.
///
/// Parsed as a whole rather than scanned per-flag: `--template-dir <value>`
/// puts a bare path in argv that looks exactly like a positional, so a naive
/// "first argument not starting with --" search picks up the template
/// directory and treats it as the project to migrate.
#[derive(Default)]
struct Args {
    migrate: bool,
    dry_run: bool,
    template_dir: Option<PathBuf>,
    project: Option<PathBuf>,
}

fn parse_args<I: IntoIterator<Item = String>>(argv: I) -> Args {
    let mut parsed = Args::default();
    let mut args = argv.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--migrate" => parsed.migrate = true,
            "--dry-run" => parsed.dry_run = true,
            "--template-dir" => {
                if let Some(value) = args.next() {
                    parsed.template_dir = Some(PathBuf::from(value));
                }
            }
            other => {
                if let Some(value) = other.strip_prefix("--template-dir=") {
                    parsed.template_dir = Some(PathBuf::from(value));
                } else if !other.starts_with("--") && parsed.project.is_none() {
                    parsed.project = Some(PathBuf::from(other));
                }
            }
        }
    }
    parsed
}

fn args() -> Args {
    parse_args(std::env::args().skip(1))
}

/// The bundled `project_template/`, which the migrations copy from. Mirrors
/// `tt_setup`'s resolution: an explicit `--template-dir`, else relative to the
/// package root the hook is invoked from.
fn template_dir_from(parsed: &Args) -> PathBuf {
    if let Some(explicit) = &parsed.template_dir {
        return explicit.clone();
    }
    if let Ok(root) = std::env::var("HPM_PACKAGE_ROOT") {
        return PathBuf::from(root).join("scripts").join("project_template");
    }
    PathBuf::from("scripts").join("project_template")
}

/// Why a window must not be opened here, if it must not.
///
/// A launch nobody is watching has nobody to answer a modal, and the hook
/// contract sets no timeout — so the safe default on any doubt is to say
/// nothing and let the launch proceed.
fn non_interactive_reason() -> Option<&'static str> {
    for flag in ["TT_NONINTERACTIVE", "CI", "TH_FARM_WORKER"] {
        if std::env::var_os(flag).is_some() {
            return Some("running non-interactively");
        }
    }
    // On X11/Wayland there is no window server to talk to without these.
    if cfg!(target_os = "linux")
        && std::env::var_os("DISPLAY").is_none()
        && std::env::var_os("WAYLAND_DISPLAY").is_none()
    {
        return Some("no display available");
    }
    None
}

// ---------- the lock -------------------------------------------------------

/// Held while a migration runs so two artists launching at once cannot both
/// rewrite the same shared `_config`.
struct Lock {
    path: PathBuf,
}

impl Lock {
    fn acquire(project: &Path) -> Result<Lock, String> {
        let path = config_dir(project).join(".migration.lock");
        if let Ok(metadata) = std::fs::metadata(&path) {
            let age = metadata
                .modified()
                .ok()
                .and_then(|m| SystemTime::now().duration_since(m).ok())
                .unwrap_or(Duration::ZERO);
            if age < LOCK_STALE_AFTER {
                return Err(format!(
                    "another migration is already running ({} exists). If nothing is running, \
                     delete it and try again.",
                    path.display()
                ));
            }
            // Stale: a crashed run must not wedge the studio.
            let _ = std::fs::remove_file(&path);
        }
        std::fs::write(&path, "tt_prepare\n")
            .map_err(|e| format!("could not take the migration lock at {}: {e}", path.display()))?;
        Ok(Lock { path })
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

// ---------- the window -----------------------------------------------------

struct Plan {
    project: PathBuf,
    template: PathBuf,
    from: u32,
    to: u32,
    steps: Vec<Step>,
    blocker: Option<(Step, String)>,
}

#[derive(Default, Clone)]
enum Outcome {
    #[default]
    Deferred,
    Migrated(String),
    Failed(String),
}

struct App {
    plan: Plan,
    outcome: Arc<Mutex<Outcome>>,
    /// Set once the migration has run, so the window shows results instead of
    /// the plan.
    finished: Option<Result<String, String>>,
}

fn show(plan: Plan) -> Result<(), String> {
    let outcome = Arc::new(Mutex::new(Outcome::default()));
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([620.0, 420.0])
            .with_resizable(true),
        ..Default::default()
    };

    let app_outcome = Arc::clone(&outcome);
    eframe::run_native(
        "TumblePipe — project configuration",
        options,
        Box::new(move |_cc| {
            Ok(Box::new(App {
                plan,
                outcome: app_outcome,
                finished: None,
            }))
        }),
    )
    .map_err(|e| format!("could not open the migration window: {e}"))?;

    // Clone out first: a MutexGuard temporary would live to the end of the
    // block and outlive `outcome` itself.
    let final_outcome = outcome.lock().unwrap().clone();
    match &final_outcome {
        Outcome::Deferred => {
            eprintln!("tt_prepare: migration deferred by the user.");
            Ok(())
        }
        Outcome::Migrated(summary) => {
            eprintln!("tt_prepare: {summary}");
            Ok(())
        }
        Outcome::Failed(why) => Err(why.clone()),
    }
}

/// Run the migration under the lock and summarise what happened.
fn migrate_now(plan: &Plan) -> Result<String, String> {
    let _lock = Lock::acquire(&plan.project)?;
    let report: Report = migration::migrate(&plan.project, &plan.template);

    if let Some((step, why)) = &report.refused {
        return Err(format!(
            "refused before writing anything — {} cannot run: {why}",
            step.description()
        ));
    }
    if let Some((step, Err(why))) = report.applied.iter().find(|(_, r)| r.is_err()) {
        return Err(format!(
            "stopped at v{} ({}): {why}",
            step.version(),
            step.description()
        ));
    }
    Ok(format!(
        "migrated _config v{} -> v{}",
        report.from, report.to
    ))
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::TopBottomPanel::bottom("actions").show(ctx, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                match (&self.finished, &self.plan.blocker) {
                    // Already run: one way out.
                    (Some(_), _) => {
                        if ui.button("Close").clicked() {
                            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                        }
                    }
                    // Cannot run: explain, and let the launch continue.
                    (None, Some(_)) => {
                        if ui.button("Continue without migrating").clicked() {
                            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                        }
                    }
                    (None, None) => {
                        if ui.button("Migrate now").clicked() {
                            let result = migrate_now(&self.plan);
                            *self.outcome.lock().unwrap() = match &result {
                                Ok(summary) => Outcome::Migrated(summary.clone()),
                                Err(why) => Outcome::Failed(why.clone()),
                            };
                            self.finished = Some(result);
                        }
                        // Deferring is a choice, not a failure — the launch
                        // carries on and the project is untouched.
                        if ui.button("Not now").clicked() {
                            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                        }
                    }
                }
            });
            ui.add_space(6.0);
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.add_space(4.0);
            ui.heading("This project's configuration is out of date");
            ui.add_space(4.0);
            ui.label(format!("{}", self.plan.project.display()));
            ui.label(format!(
                "_config is at v{}; this version of TumblePipe expects v{}.",
                self.plan.from, self.plan.to
            ));
            ui.separator();

            match &self.finished {
                Some(Ok(summary)) => {
                    ui.colored_label(STATUS_OK, summary);
                    ui.add_space(4.0);
                    ui.label(
                        "Files that were replaced were backed up alongside as .bak, and the \
                         property keys behind any renamed label are unchanged.",
                    );
                }
                Some(Err(why)) => {
                    ui.colored_label(STATUS_ERROR, why);
                    ui.add_space(4.0);
                    ui.label(
                        "The project was left as it was. Nothing needs undoing; report this \
                         and launch as normal.",
                    );
                }
                None => {
                    if let Some((step, why)) = &self.plan.blocker {
                        ui.colored_label(
                            STATUS_ERROR,
                            format!("Cannot migrate: {}", step.description()),
                        );
                        ui.add_space(4.0);
                        ui.label(why);
                        ui.add_space(6.0);
                        ui.label(
                            "Nothing has been changed. The launch will carry on — this project \
                             works as it is, but it will not pick up newer config features.",
                        );
                    } else {
                        ui.label("These steps will run:");
                        ui.add_space(4.0);
                        for step in &self.plan.steps {
                            ui.label(format!("  v{} — {}", step.version(), step.description()));
                        }
                        ui.add_space(8.0);
                        ui.label(
                            "Anything replaced is backed up alongside as .bak first. You can \
                             defer this and keep working; the project is only changed if you \
                             choose to migrate.",
                        );
                    }
                }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_project_path_is_an_error_not_a_panic() {
        // The hook still exits 0; run() only reports why it did nothing.
        std::env::remove_var("TH_PROJECT_PATH");
        assert!(project_path().is_err());
    }

    fn argv(items: &[&str]) -> Args {
        parse_args(items.iter().map(|s| s.to_string()))
    }

    /// The bug this parser exists for: `--template-dir <value>` leaves a bare
    /// path in argv, and taking "the first argument not starting with --" as
    /// the project picked up the *template* directory instead. Pointed at a
    /// template that was behind, that would have migrated the packaged
    /// scaffold rather than the project.
    #[test]
    fn a_template_dir_value_is_never_mistaken_for_the_project() {
        let parsed = argv(&["--migrate", "--template-dir", "/pkg/template", "/projects/film"]);
        assert!(parsed.migrate);
        assert_eq!(parsed.template_dir, Some(PathBuf::from("/pkg/template")));
        assert_eq!(parsed.project, Some(PathBuf::from("/projects/film")));

        // ...and with no project given at all, there is no project — not the
        // template standing in for one.
        let parsed = argv(&["--migrate", "--template-dir", "/pkg/template"]);
        assert_eq!(parsed.project, None);
    }

    #[test]
    fn the_equals_form_and_flags_parse() {
        let parsed = argv(&["--migrate", "--dry-run", "--template-dir=/pkg/t"]);
        assert!(parsed.migrate && parsed.dry_run);
        assert_eq!(parsed.template_dir, Some(PathBuf::from("/pkg/t")));
        assert_eq!(parsed.project, None);
    }

    #[test]
    fn template_dir_falls_back_to_the_package_root() {
        std::env::set_var("HPM_PACKAGE_ROOT", "/pkg");
        assert_eq!(
            template_dir_from(&Args::default()),
            PathBuf::from("/pkg").join("scripts").join("project_template")
        );
        std::env::remove_var("HPM_PACKAGE_ROOT");
    }

    #[test]
    fn non_interactive_flags_suppress_the_window() {
        std::env::set_var("TT_NONINTERACTIVE", "1");
        assert!(non_interactive_reason().is_some());
        std::env::remove_var("TT_NONINTERACTIVE");
    }

    #[test]
    fn a_fresh_lock_blocks_a_second_holder_and_releases_on_drop() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("_config")).unwrap();

        let first = Lock::acquire(dir.path()).expect("first lock");
        assert!(Lock::acquire(dir.path()).is_err(), "second must be refused");
        drop(first);
        Lock::acquire(dir.path()).expect("lock is released on drop");
    }
}
