//! The cleanup's rules over a line protocol, for `tests/test_cleanup_parity.py`:
//! one JSON request per stdin line (`{"op": ..., ...}`), one reply per
//! stdout line (`{"ok": ...}` or `{"error": "..."}`).
//!
//! Ops: `plan` and `apply` (a project, its settings, `only`, and what the
//! farm answers: `deadline` `{"jobs": {...}}` or `{"problem": "..."}`,
//! `local` `{"folders": [...]}` or `{"error": "..."}`); `views` (the listing
//! and window texts of a plan given as data); `parse_jobs`; `layer_refs`;
//! `names` (how single names read); `cli` (the command line's output).

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};
use th_cleanup::core::{CategoryPlan, Entry, Farm, Payload, Plan, Settings};
use th_cleanup::overview::{self, Row};
use th_cleanup::{ProjectRoots, backups, cli, core, deleted, inventory, jobs, usd};

struct Injected {
    deadline: Result<HashMap<String, Vec<String>>, String>,
    local: Result<HashSet<String>, String>,
}

impl Farm for Injected {
    fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String> {
        self.deadline.clone()
    }
    fn local_folders(&self) -> Result<HashSet<String>, String> {
        self.local.clone()
    }
}

fn farm(request: &Value) -> Injected {
    let deadline = match request.get("deadline") {
        Some(Value::Object(d)) if d.contains_key("jobs") => Ok(serde_json::from_value(d["jobs"].clone()).unwrap_or_default()),
        Some(Value::Object(d)) => Err(d.get("problem").and_then(Value::as_str).unwrap_or("").to_string()),
        _ => Err("Deadline is not installed on this machine: no job folder can be deleted safely.".into()),
    };
    let local = match request.get("local") {
        Some(Value::Object(l)) if l.contains_key("error") => Err(l["error"].as_str().unwrap_or("").to_string()),
        Some(Value::Object(l)) => Ok(serde_json::from_value(l.get("folders").cloned().unwrap_or(json!([]))).unwrap_or_default()),
        _ => Ok(HashSet::new()),
    };
    Injected { deadline, local }
}

fn settings(request: &Value) -> Settings {
    let mut settings = Settings::default();
    let Some(Value::Object(given)) = request.get("settings") else { return settings };
    let int = |key: &str, default: i64| given.get(key).and_then(Value::as_i64).unwrap_or(default);
    let float = |key: &str, default: f64| given.get(key).and_then(Value::as_f64).unwrap_or(default);
    settings.context = given.get("context").and_then(Value::as_str).map(String::from);
    settings.keep_exports = int("keep_exports", settings.keep_exports);
    settings.keep_renders = int("keep_renders", settings.keep_renders);
    settings.check_render_days = float("check_render_days", settings.check_render_days);
    settings.keep_caches = int("keep_caches", settings.keep_caches);
    settings.keep_backups = int("keep_backups", settings.keep_backups);
    settings.backup_days = float("backup_days", settings.backup_days);
    settings.deleted_days = float("deleted_days", settings.deleted_days);
    settings.job_days = float("job_days", settings.job_days);
    settings.local_job_days = float("local_job_days", settings.local_job_days);
    settings
}

fn only(request: &Value) -> Option<Vec<String>> {
    request.get("only").and_then(Value::as_array).map(|keys| keys.iter().filter_map(Value::as_str).map(String::from).collect())
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

fn make(request: &Value) -> Result<(ProjectRoots, Plan), String> {
    let project = PathBuf::from(request["project"].as_str().ok_or("no project")?);
    let roots = ProjectRoots::open(&project)?;
    let only = only(request);
    let plan = core::make_plan(&roots, &settings(request), only.as_deref(), &farm(request), &mut |_| {})?;
    Ok((roots, plan))
}

/// A plan given as data (what Python listed), for the views.
fn plan_from(request: &Value) -> Result<Plan, String> {
    let roots = ProjectRoots { project_path: PathBuf::new(), config_path: PathBuf::new(), roots: Vec::new() };
    let mut plan = Plan { roots, settings: Settings::default(), categories: Vec::new() };
    for category in request["categories"].as_array().ok_or("no categories")? {
        let text = |key: &str| category[key].as_str().unwrap_or("").to_string();
        let strings = |key: &str| -> Vec<String> {
            category[key].as_array().map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect()).unwrap_or_default()
        };
        let mut out = CategoryPlan::new(&text("key"), &text("title"), &text("noun"));
        out.notes = strings("notes");
        out.errors = strings("errors");
        out.payload = Payload::None;
        for entry in category["entries"].as_array().ok_or("no entries")? {
            out.entries.push(Entry {
                path: PathBuf::from(entry["path"].as_str().unwrap_or("")),
                label: entry["label"].as_str().unwrap_or("").into(),
                group: entry["group"].as_str().unwrap_or("").into(),
                size: entry["size"].as_u64().ok_or("a size that is not a whole number")?,
                prune: entry["prune"].as_bool().unwrap_or(false),
                reason: entry["reason"].as_str().unwrap_or("").into(),
            });
        }
        plan.categories.push(out);
    }
    Ok(plan)
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

fn results_from(value: &Value) -> Vec<(String, core::Outcome)> {
    let Some(map) = value.as_object() else { return Vec::new() };
    map.iter()
        .map(|(key, outcome)| {
            let removed = outcome[0].as_u64().unwrap_or(0) as usize;
            let bytes = outcome[1].as_u64().unwrap_or(0);
            let errors = outcome[2].as_array().map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect()).unwrap_or_default();
            (key.clone(), (removed, bytes, errors))
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

fn handle(request: &Value) -> Result<Value, String> {
    match request["op"].as_str().unwrap_or("") {
        "plan" => {
            let (_, plan) = make(request)?;
            Ok(plan_json(&plan))
        }
        "apply" => {
            let (_, plan) = make(request)?;
            let log_path = PathBuf::from(request["log_path"].as_str().ok_or("no log path")?);
            let results = core::apply(&plan, &log_path);
            Ok(json!({ "plan": plan_json(&plan), "results": results_json(&results) }))
        }
        "views" => {
            let plan = plan_from(request)?;
            let detail = request["detail"].as_u64().map_or(40, |d| d as usize);
            let tree_detail = request["tree_detail"].as_u64().map_or(overview::DETAIL, |d| d as usize);
            let view = overview::overview(&plan);
            let results = results_from(&request["results"]);
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
        "parse_jobs" => Ok(json!(jobs::parse_jobs(request["text"].as_str().unwrap_or("")))),
        "layer_refs" => usd::layer_references(Path::new(request["path"].as_str().unwrap_or(""))).map(|refs| json!(refs)),
        "names" => {
            let names: Vec<&str> = request["names"].as_array().map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            Ok(json!(
                names
                    .iter()
                    .map(|name| json!({
                        "backup_of": backups::backup_of(name).map(|(v, n)| json!([v as i64, n as i64])),
                        "stamp_time": deleted::stamp_time(name),
                        "workfile_version": inventory::workfile_version(name),
                        "is_uuid": jobs::is_uuid(&name.to_lowercase()),
                    }))
                    .collect::<Vec<_>>()
            ))
        }
        "cli" => {
            let argv: Vec<String> =
                request["argv"].as_array().map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect()).unwrap_or_default();
            let (mut out, mut err) = (Vec::new(), Vec::new());
            let code = cli::run(&argv, &farm(request), &mut out, &mut err);
            Ok(json!({ "code": code, "out": String::from_utf8_lossy(&out), "err": String::from_utf8_lossy(&err) }))
        }
        other => Err(format!("unknown op {other:?}")),
    }
}

fn main() {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let reply = match serde_json::from_str::<Value>(&line) {
            Ok(request) => match handle(&request) {
                Ok(value) => json!({ "ok": value }),
                Err(error) => json!({ "error": error }),
            },
            Err(error) => json!({ "error": format!("bad request: {error}") }),
        };
        let _ = writeln!(stdout, "{reply}");
        let _ = stdout.flush();
    }
}
