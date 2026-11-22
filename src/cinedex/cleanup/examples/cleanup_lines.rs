//! The cleanup's rules over a line protocol, for `tests/test_cleanup_parity.py`:
//! one JSON request per stdin line (`{"op": ..., ...}`), one reply per
//! stdout line (`{"ok": ...}` or `{"error": "..."}`).
//!
//! Ops: `plan` and `apply` (a project, its settings, `only`, and what the
//! farm answers: `deadline` `{"jobs": {...}}` or `{"problem": "..."}`,
//! `local` `{"folders": [...]}` or `{"error": "..."}`); `views` (the listing
//! and window texts of a plan given as data); `parse_jobs`; `layer_refs`;
//! `names` (how single names read); `cli` (the command line's output).
//!
//! Requests are read strictly: a key missing, unknown or of the wrong type
//! is an error reply, so a key renamed on one side cannot pass unnoticed.

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Write};
use std::path::PathBuf;

use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use th_cleanup::core::{CategoryPlan, Entry, Farm, Payload, Plan, Settings};
use th_cleanup::overview::{self, Row};
use th_cleanup::{ProjectRoots, backups, cli, core, deleted, inventory, jobs, usd};

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Plan {
        project: PathBuf,
        settings: SettingsIn,
        #[serde(deserialize_with = "nullable")]
        only: Option<Vec<String>>,
        deadline: Deadline,
        local: Local,
    },
    Apply {
        project: PathBuf,
        log_path: PathBuf,
        settings: SettingsIn,
        #[serde(deserialize_with = "nullable")]
        only: Option<Vec<String>>,
        deadline: Deadline,
        local: Local,
    },
    Views {
        categories: Vec<CategoryIn>,
        detail: usize,
        tree_detail: usize,
        results: Map<String, Value>,
    },
    ParseJobs {
        text: String,
    },
    LayerRefs {
        path: PathBuf,
    },
    Names {
        names: Vec<String>,
    },
    Cli {
        argv: Vec<String>,
        deadline: Deadline,
        local: Local,
    },
}

/// A key that must be there, `null` for none (`only`).
fn nullable<'de, D: Deserializer<'de>, T: Deserialize<'de>>(deserializer: D) -> Result<Option<T>, D::Error> {
    Option::deserialize(deserializer)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SettingsIn {
    /// Left out for the whole project.
    #[serde(default)]
    context: Option<String>,
    keep_exports: i64,
    keep_renders: i64,
    check_render_days: f64,
    keep_caches: i64,
    keep_backups: i64,
    backup_days: f64,
    deleted_days: f64,
    job_days: f64,
    local_job_days: f64,
}

impl From<SettingsIn> for Settings {
    fn from(s: SettingsIn) -> Self {
        Settings {
            context: s.context,
            keep_exports: s.keep_exports,
            keep_renders: s.keep_renders,
            check_render_days: s.check_render_days,
            keep_caches: s.keep_caches,
            keep_backups: s.keep_backups,
            backup_days: s.backup_days,
            deleted_days: s.deleted_days,
            job_days: s.job_days,
            local_job_days: s.local_job_days,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
enum Deadline {
    Jobs(HashMap<String, Vec<String>>),
    Problem(String),
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
enum Local {
    Folders(HashSet<String>),
    Error(String),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CategoryIn {
    key: String,
    title: String,
    noun: String,
    notes: Vec<String>,
    errors: Vec<String>,
    entries: Vec<EntryIn>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EntryIn {
    path: PathBuf,
    label: String,
    group: String,
    size: u64,
    prune: bool,
    reason: String,
}

struct Injected {
    deadline: Deadline,
    local: Local,
}

impl Farm for Injected {
    fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String> {
        match &self.deadline {
            Deadline::Jobs(jobs) => Ok(jobs.clone()),
            Deadline::Problem(problem) => Err(problem.clone()),
        }
    }
    fn local_folders(&self) -> Result<HashSet<String>, String> {
        match &self.local {
            Local::Folders(folders) => Ok(folders.clone()),
            Local::Error(error) => Err(error.clone()),
        }
    }
}

fn entry_json(entry: &Entry) -> Value {
    json!({
        "path": entry.path.display().to_string(),
        "label": entry.label,
        "group": entry.group,
        "size": entry.size,
        "prune": entry.prune,
        "reason": entry.reason,
    })
}

fn plan_json(plan: &Plan) -> Value {
    let categories: Vec<Value> = plan
        .categories
        .iter()
        .map(|c| {
            json!({
                "key": c.key, "title": c.title, "noun": c.noun,
                "entries": c.entries.iter().map(entry_json).collect::<Vec<_>>(),
                "notes": c.notes, "errors": c.errors,
            })
        })
        .collect();
    json!({ "categories": categories })
}

fn make(project: PathBuf, settings: SettingsIn, only: Option<Vec<String>>, farm: Injected) -> Result<Plan, String> {
    let roots = ProjectRoots::open(&project)?;
    core::make_plan(&roots, &settings.into(), only.as_deref(), &farm, &mut |_| {})
}

/// A plan given as data (what Python listed), for the views.
fn plan_from(categories: Vec<CategoryIn>) -> Plan {
    let roots = ProjectRoots { project_path: PathBuf::new(), config_path: PathBuf::new(), roots: Vec::new() };
    let mut plan = Plan { roots, settings: Settings::default(), categories: Vec::new() };
    for category in categories {
        let mut out = CategoryPlan::new(&category.key, &category.title, &category.noun);
        out.notes = category.notes;
        out.errors = category.errors;
        out.payload = Payload::None;
        out.entries = category
            .entries
            .into_iter()
            .map(|e| Entry { path: e.path, label: e.label, group: e.group, size: e.size, prune: e.prune, reason: e.reason })
            .collect();
        plan.categories.push(out);
    }
    plan
}

fn row_json(row: &Row) -> Value {
    json!({
        "cells": row.cells,
        "tone": row.tone.name(),
        "tip": row.tip,
        "children": row.children.iter().map(row_json).collect::<Vec<_>>(),
        "expanded": row.expanded,
    })
}

/// Each category's `[removed, bytes, errors]`, in the order given.
fn results_from(results: Map<String, Value>) -> Result<Vec<(String, core::Outcome)>, String> {
    results
        .into_iter()
        .map(|(key, outcome)| {
            let outcome = serde_json::from_value(outcome).map_err(|e| format!("results of {key}: {e}"))?;
            Ok((key, outcome))
        })
        .collect()
}

fn results_json(results: &[(String, core::Outcome)]) -> Value {
    let mut map = Map::new();
    for (key, (removed, bytes, errors)) in results {
        map.insert(key.clone(), json!([removed, bytes, errors]));
    }
    Value::Object(map)
}

fn handle(request: Request) -> Result<Value, String> {
    match request {
        Request::Plan { project, settings, only, deadline, local } => {
            Ok(plan_json(&make(project, settings, only, Injected { deadline, local })?))
        }
        Request::Apply { project, log_path, settings, only, deadline, local } => {
            let plan = make(project, settings, only, Injected { deadline, local })?;
            let results = core::apply(&plan, &log_path);
            Ok(json!({ "plan": plan_json(&plan), "results": results_json(&results) }))
        }
        Request::Views { categories, detail, tree_detail, results } => {
            let plan = plan_from(categories);
            let view = overview::overview(&plan);
            let results = results_from(results)?;
            Ok(json!({
                "summary": core::summary(&plan, detail),
                "rows": overview::rows(&view, tree_detail).iter().map(row_json).collect::<Vec<_>>(),
                "headline": overview::headline(&view),
                "subline": overview::subline(&view),
                "delete_label": overview::delete_label(Some(&view)),
                "warnings": overview::warnings(&view),
                "confirm": overview::confirm_lines(&view),
                "deleted_rows": overview::deleted_rows(&plan, &results).iter().map(row_json).collect::<Vec<_>>(),
            }))
        }
        Request::ParseJobs { text } => Ok(json!(jobs::parse_jobs(&text))),
        Request::LayerRefs { path } => usd::layer_references(&path).map(|refs| json!(refs)),
        Request::Names { names } => Ok(json!(
            names
                .iter()
                .map(|name| json!({
                    "backup_of": backups::backup_of(name).map(|(v, n)| json!([v as i64, n as i64])),
                    "stamp_time": deleted::stamp_time(name),
                    "workfile_version": inventory::workfile_version(name),
                    "is_uuid": jobs::is_uuid(&name.to_lowercase()),
                }))
                .collect::<Vec<_>>()
        )),
        Request::Cli { argv, deadline, local } => {
            let (mut out, mut err) = (Vec::new(), Vec::new());
            let code = cli::run(&argv, &Injected { deadline, local }, &mut out, &mut err);
            Ok(json!({ "code": code, "out": String::from_utf8_lossy(&out), "err": String::from_utf8_lossy(&err) }))
        }
    }
}

fn main() {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let reply = match serde_json::from_str::<Request>(&line) {
            Ok(request) => match handle(request) {
                Ok(value) => json!({ "ok": value }),
                Err(error) => json!({ "error": error }),
            },
            Err(error) => json!({ "error": format!("bad request: {error}") }),
        };
        let _ = writeln!(stdout, "{reply}");
        let _ = stdout.flush();
    }
}
