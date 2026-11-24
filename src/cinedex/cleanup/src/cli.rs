//! The command line (`python -m tumblepipe.cleanup`): list what a project no
//! longer needs and delete it with `--apply`. Same arguments, same output.

use std::io::Write;
use std::path::PathBuf;

use crate::core::{self, CATEGORY_KEYS, Farm, Settings};
use crate::py::{self, format_g, human};
use crate::roots::ProjectRoots;

pub const PROG: &str = "cleanup";

/// `(flag, metavar, help)` of every option taking a value.
fn options(defaults: &Settings) -> Vec<(&'static str, &'static str, String)> {
    vec![
        ("--only", "CATEGORIES", format!("comma-separated categories (default: all of {})", CATEGORY_KEYS.join(","))),
        (
            "--context",
            "CONTEXT",
            "exports, renders, caches and backups: only shots, only assets, or a path under them (shots/sq010); job folders and deleted entities are of the whole project".into(),
        ),
        ("--keep-exports", "KEEP_EXPORTS", format!("finished versions to keep of every export (default {})", defaults.keep_exports)),
        ("--keep-renders", "KEEP_RENDERS", format!("complete versions to keep of every render layer (default {})", defaults.keep_renders)),
        (
            "--check-render-days",
            "CHECK_RENDER_DAYS",
            format!("delete a check render once it is this old (default {})", format_g(defaults.check_render_days)),
        ),
        ("--keep-caches", "KEEP_CACHES", format!("versions to keep of every th::cache (default {})", defaults.keep_caches)),
        (
            "--keep-backups",
            "KEEP_BACKUPS",
            format!("Houdini backups to keep of each department's latest workfile version (default {})", defaults.keep_backups),
        ),
        ("--backup-days", "BACKUP_DAYS", format!("never delete a backup newer than this (default {})", format_g(defaults.backup_days))),
        (
            "--deleted-days",
            "DELETED_DAYS",
            format!("empty a _deleted folder once its delete is this old (default {})", format_g(defaults.deleted_days)),
        ),
        (
            "--job-days",
            "JOB_DAYS",
            format!("keep a farm job folder no Deadline job names until it is this old (default {})", format_g(defaults.job_days)),
        ),
        (
            "--local-job-days",
            "LOCAL_JOB_DAYS",
            format!(
                "keep a local farm job folder no batch on this machine uses until it is this old (default {})",
                format_g(defaults.local_job_days)
            ),
        ),
    ]
}

fn usage() -> String {
    format!(
        "usage: {PROG} [-h] [--only CATEGORIES] [--context CONTEXT] [--keep-exports KEEP_EXPORTS] [--keep-renders KEEP_RENDERS] \
         [--check-render-days CHECK_RENDER_DAYS] [--keep-caches KEEP_CACHES] [--keep-backups KEEP_BACKUPS] [--backup-days BACKUP_DAYS] \
         [--deleted-days DELETED_DAYS] [--job-days JOB_DAYS] [--local-job-days LOCAL_JOB_DAYS] [--apply] [--gui] [project]"
    )
}

/// `--help`'s text.
pub fn help() -> String {
    let defaults = Settings::default();
    let mut lines = vec![usage(), String::new(), "List what a project no longer needs, by category, and delete it with --apply.".into()];
    lines.push(String::new());
    lines.push("positional arguments:".into());
    lines.push("  project               project folder (default: $TH_PROJECT_PATH)".into());
    lines.push(String::new());
    lines.push("options:".into());
    lines.push("  -h, --help            show this help message and exit".into());
    for (flag, metavar, about) in options(&defaults) {
        lines.push(format!("  {flag} {metavar}"));
        lines.push(format!("                        {about}"));
    }
    lines.push("  --apply               delete what is listed to delete (default: only list it)".into());
    lines.push("  --gui                 open the window instead (the default with no arguments at all)".into());
    lines.push(String::new());
    lines.push("Categories:".into());
    for category in core::categories() {
        lines.push(format!("  {} {}", py::left(category.key, 8), category.about));
    }
    lines.join("\n")
}

/// What the command line asked for.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Args {
    pub project: Option<String>,
    pub only: Option<String>,
    pub settings: Settings,
    pub apply: bool,
    pub gui: bool,
    pub help: bool,
}

/// Python's `float(text)`.
fn py_float(text: &str) -> Option<f64> {
    let body = py::strip(text);
    let lowered = body.to_ascii_lowercase();
    let unsigned = lowered.trim_start_matches(['+', '-']);
    if matches!(unsigned, "inf" | "infinity" | "nan") {
        return lowered.parse().ok();
    }
    if body.starts_with('_') || body.ends_with('_') || body.contains("__") {
        return None;
    }
    let cleaned: String =
        body.chars().filter(|c| *c != '_').map(|c| py::digit(c).map_or(c, |d| char::from_digit(d, 10).unwrap_or(c))).collect();
    if cleaned.chars().any(|c| c.is_ascii_alphabetic() && !matches!(c, 'e' | 'E')) {
        return None;
    }
    cleaned.parse().ok()
}

/// Read the arguments as argparse does: `--flag value`, `--flag=value`, an
/// unambiguous prefix of a flag, `--` before a project that starts with `-`.
pub fn parse(argv: &[String]) -> Result<Args, String> {
    let defaults = Settings::default();
    let flags: Vec<&str> = options(&defaults).iter().map(|(f, _, _)| *f).chain(["--apply", "--gui", "--help"]).collect();
    let mut args = Args { settings: defaults, ..Args::default() };
    let mut at = 0;
    let mut positional_only = false;
    while at < argv.len() {
        let arg = &argv[at];
        at += 1;
        if positional_only || !arg.starts_with('-') || arg == "-" {
            if args.project.is_some() {
                return Err(format!("unrecognized arguments: {arg}"));
            }
            args.project = Some(arg.clone());
            continue;
        }
        if arg == "--" {
            positional_only = true;
            continue;
        }
        if arg == "-h" {
            args.help = true;
            continue;
        }
        let (name, inline) = match arg.split_once('=') {
            Some((name, value)) if name.starts_with("--") => (name, Some(value.to_string())),
            _ => (arg.as_str(), None),
        };
        let matches: Vec<&str> = flags.iter().copied().filter(|f| *f == name).collect();
        let matches =
            if matches.is_empty() { flags.iter().copied().filter(|f| name.len() > 2 && f.starts_with(name)).collect() } else { matches };
        let flag = match matches.as_slice() {
            [flag] => *flag,
            [] => return Err(format!("unrecognized arguments: {arg}")),
            many => {
                return Err(format!("ambiguous option: {name} could match {}", many.join(", ")));
            }
        };
        match flag {
            "--apply" | "--gui" | "--help" => {
                if inline.is_some() {
                    return Err(format!("argument {flag}: ignored explicit argument '{}'", inline.unwrap_or_default()));
                }
                match flag {
                    "--apply" => args.apply = true,
                    "--gui" => args.gui = true,
                    _ => args.help = true,
                }
                continue;
            }
            _ => {}
        }
        let value = match inline {
            Some(value) => value,
            None => match argv.get(at) {
                Some(next) if !(next.starts_with('-') && next.len() > 1 && py_float(next).is_none()) => {
                    at += 1;
                    next.clone()
                }
                _ => return Err(format!("argument {flag}: expected one argument")),
            },
        };
        let int = |value: &str| {
            py::int_str(value).and_then(|v| i64::try_from(v).ok()).ok_or_else(|| format!("argument {flag}: invalid int value: '{value}'"))
        };
        let float = |value: &str| py_float(value).ok_or_else(|| format!("argument {flag}: invalid float value: '{value}'"));
        let settings = &mut args.settings;
        match flag {
            "--only" => args.only = Some(value),
            "--context" => settings.context = Some(value),
            "--keep-exports" => settings.keep_exports = int(&value)?,
            "--keep-renders" => settings.keep_renders = int(&value)?,
            "--check-render-days" => settings.check_render_days = float(&value)?,
            "--keep-caches" => settings.keep_caches = int(&value)?,
            "--keep-backups" => settings.keep_backups = int(&value)?,
            "--backup-days" => settings.backup_days = float(&value)?,
            "--deleted-days" => settings.deleted_days = float(&value)?,
            "--job-days" => settings.job_days = float(&value)?,
            _ => settings.local_job_days = float(&value)?,
        }
    }
    Ok(args)
}

/// The categories `--only` names, or None for all of them; an error naming
/// what is not one.
pub fn only_keys(only: Option<&str>) -> Result<Option<Vec<String>>, String> {
    let Some(only) = only.filter(|o| !o.is_empty()) else { return Ok(None) };
    let keys: Vec<String> = only.split(',').map(|k| py::strip(k).to_string()).filter(|k| !k.is_empty()).collect();
    let mut unknown: Vec<&str> = keys.iter().map(String::as_str).filter(|k| !CATEGORY_KEYS.contains(k)).collect();
    unknown.sort();
    unknown.dedup();
    if !unknown.is_empty() {
        return Err(format!("no such category: {} (there are {})", unknown.join(", "), CATEGORY_KEYS.join(", ")));
    }
    Ok(Some(keys))
}

/// The project folder: the one given, else `$TH_PROJECT_PATH`, else here.
pub fn project_path(project: Option<&str>) -> PathBuf {
    let given = project.filter(|p| !p.is_empty()).map(String::from);
    let from_env = std::env::var("TH_PROJECT_PATH").ok().filter(|p| !p.is_empty());
    PathBuf::from(given.or(from_env).unwrap_or_else(|| ".".into()))
}

/// Run the command line on `argv` (without the program's name). What
/// Python prints to stdout goes to `out`, its errors to `err`; the exit
/// code is returned.
pub fn run(argv: &[String], farm: &dyn Farm, out: &mut dyn Write, err: &mut dyn Write) -> i32 {
    let args = match parse(argv) {
        Ok(args) => args,
        Err(message) => {
            let _ = writeln!(err, "{}\n{PROG}: error: {message}", usage());
            return 2;
        }
    };
    if args.help {
        let _ = writeln!(out, "{}", help());
        return 0;
    }
    run_args(&args, farm, out, err)
}

pub fn run_args(args: &Args, farm: &dyn Farm, out: &mut dyn Write, err: &mut dyn Write) -> i32 {
    if let Some(problem) = args.settings.problem() {
        let _ = writeln!(err, "{problem}");
        return 2;
    }
    let only = match only_keys(args.only.as_deref()) {
        Ok(only) => only,
        Err(message) => {
            let _ = writeln!(err, "{message}");
            return 2;
        }
    };
    let project = project_path(args.project.as_deref());
    if !project.is_dir() {
        let _ = writeln!(err, "no project: pass its folder or set TH_PROJECT_PATH");
        return 2;
    }
    let roots = match ProjectRoots::open(&project) {
        Ok(roots) => roots,
        Err(message) => {
            let _ = writeln!(err, "{message}");
            return 1;
        }
    };
    let plan = {
        let mut progress = |text: &str| {
            let _ = writeln!(out, "{text}");
            let _ = out.flush();
        };
        core::make_plan(&roots, &args.settings, only.as_deref(), farm, &mut progress)
    };
    let plan = match plan {
        Ok(plan) => plan,
        Err(message) => {
            let _ = writeln!(err, "{message}");
            return 1;
        }
    };
    let _ = writeln!(out);
    let _ = writeln!(out, "{}", core::summary(&plan, 40).join("\n"));
    if plan.prunable_count() == 0 {
        return 0;
    }
    if !args.apply {
        let _ = writeln!(
            out,
            "\nNothing deleted. Run again with --apply to delete the {} listed ({}).",
            plan.prunable_count(),
            human(plan.prunable_size())
        );
        return 0;
    }
    let log_path = core::log_path_for(&roots);
    let results = core::apply(&plan, &log_path);
    let mut failed = false;
    let _ = writeln!(out);
    for category in &plan.categories {
        let (removed, removed_bytes, errors) = results.iter().find(|(k, _)| *k == category.key).map(|(_, o)| o.clone()).unwrap_or_default();
        if removed > 0 || !errors.is_empty() {
            let _ = writeln!(out, "{}: deleted {removed}, {}.", category.title, human(removed_bytes));
        }
        for error in &errors {
            failed = true;
            let _ = writeln!(out, "  {error}");
        }
    }
    let _ = writeln!(out, "Logged to {}.", log_path.display());
    i32::from(failed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn argv(text: &str) -> Vec<String> {
        text.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn arguments_read_as_argparse() {
        let args = parse(&argv("P --only=exports,jobs --keep-e 4 --backup-days 1.5 --apply")).unwrap();
        assert_eq!(args.project.as_deref(), Some("P"));
        assert_eq!(args.only.as_deref(), Some("exports,jobs"));
        assert_eq!(args.settings.keep_exports, 4);
        assert_eq!(args.settings.backup_days, 1.5);
        assert!(args.apply);
        assert!(parse(&argv("--keep 3")).is_err());
        assert!(parse(&argv("--keep-exports x")).is_err());
        assert!(parse(&argv("--keep-exports")).is_err());
        assert!(parse(&argv("a b")).is_err());
        assert_eq!(parse(&argv("--job-days -1")).unwrap().settings.job_days, -1.0);
        assert_eq!(parse(&argv("--keep-caches 1_0")).unwrap().settings.keep_caches, 10);
    }

    #[test]
    fn only_is_checked() {
        assert_eq!(only_keys(Some(" exports , jobs,")).unwrap(), Some(vec!["exports".to_string(), "jobs".to_string()]));
        assert_eq!(only_keys(Some("")).unwrap(), None);
        assert_eq!(only_keys(Some(",")).unwrap(), Some(vec![]));
        assert_eq!(
            only_keys(Some("x,a,x")).unwrap_err(),
            "no such category: a, x (there are exports, renders, caches, backups, jobs, deleted)"
        );
    }
}
