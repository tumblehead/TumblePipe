//! Answers questions about a project, one JSON request per stdin line and
//! one JSON reply per stdout line, for `tests/test_pipe_parity.py`, which
//! asks Python the same and compares.
//!
//! The project is the one the request names (`project`, `pipeline`,
//! `config`), else the one the environment names (`TH_PROJECT_PATH`,
//! `TH_PIPELINE_PATH`, `TH_CONFIG_PATH`), opened afresh for every request,
//! so every answer sees what Python wrote before it. A reply is
//! `{"ok": <value>}` or `{"error": "<why>"}`.

use std::io::{BufRead, Write};
use std::path::Path;

use serde_json::{Value, json};
use th_pipe::storage::slashed;
use th_pipe::{Project, Uri, departments, discovery, entities, timeline, versions};

fn text<'a>(request: &'a Value, key: &str) -> Result<&'a str, String> {
    request.get(key).and_then(Value::as_str).ok_or_else(|| format!("request has no '{key}'"))
}

fn uri(request: &Value) -> Result<Uri, String> {
    Uri::parse(text(request, "uri")?).map_err(|e| e.to_string())
}

fn named_uri(request: &Value, key: &str) -> Result<Uri, String> {
    Uri::parse(text(request, key)?).map_err(|e| e.to_string())
}

fn texts(request: &Value, key: &str) -> Result<Vec<String>, String> {
    let values = request.get(key).and_then(Value::as_array).ok_or_else(|| format!("request has no '{key}'"))?;
    values.iter().map(|v| v.as_str().map(str::to_string).ok_or_else(|| format!("'{key}' holds {v}, not text"))).collect()
}

fn uris(request: &Value, key: &str) -> Result<Vec<Uri>, String> {
    texts(request, key)?.iter().map(|u| Uri::parse(u).map_err(|e| e.to_string())).collect()
}

/// The project the request names (`project`, `pipeline`, `config`), else
/// the environment's.
fn project(request: &Value) -> Result<Project, String> {
    match (request.get("project"), request.get("pipeline"), request.get("config")) {
        (Some(Value::String(root)), Some(Value::String(pipeline)), Some(Value::String(config))) => {
            Project::open_paths(Path::new(root), Path::new(pipeline), Path::new(config))
        }
        _ => Project::from_env(),
    }
}

fn answer(request: &Value) -> Result<Value, String> {
    let project = project(request)?;
    let config = &project.config;
    let err = |e: th_pipe::config::ConfigError| e.to_string();
    Ok(match text(request, "op")? {
        "resolve" => json!(slashed(&project.storage.resolve(&uri(request)?).map_err(|e| e.to_string())?)),
        "properties" => json!(config.get_properties(&uri(request)?).map_err(err)?),
        "own_properties" => json!(config.get_own_properties(&uri(request)?).map_err(err)?),
        "schema_uri" => json!(config.get_entity_schema_uri(&uri(request)?).map_err(err)?.map(|u| u.to_string())),
        "terminal" => json!(config.is_terminal_entity(&uri(request)?).map_err(err)?),
        "list" => {
            let closure = request.get("closure").and_then(Value::as_bool).unwrap_or(false);
            json!(config.list_entity_uris(&uri(request)?, closure).map_err(err)?.iter().map(Uri::to_string).collect::<Vec<_>>())
        }
        "departments" => {
            let flag = |key: &str, default: bool| request.get(key).and_then(Value::as_bool).unwrap_or(default);
            json!(departments::list_departments(
                config,
                text(request, "context")?,
                flag("include_generated", true),
                flag("include_disabled", false)
            )?)
        }
        "channels" => json!(departments::list_channels(config, &uri(request)?)?),
        "frame_range" => {
            json!(timeline::get_frame_range(config, &uri(request)?)?.map(|r| [r.start_frame, r.end_frame, r.start_roll, r.end_roll]))
        }
        "fps" => json!(timeline::get_fps(config, &uri(request)?)?),
        "shot" => serde_json::to_value(discovery::get_shot(&project, &uri(request)?)?).map_err(|e| e.to_string())?,
        "latest_version" => json!(versions::latest_version_path(Path::new(text(request, "path")?)).map(|p| slashed(&p))),
        "next_version" => json!(slashed(&versions::next_version_path(Path::new(text(request, "path")?)))),
        "workspace" => {
            let ws = th_pipe::workfiles::department_workspace(&project, &uri(request)?, text(request, "department")?)?;
            json!({"workfile_uri": ws.workfile_uri.to_string(), "path": slashed(&ws.path)})
        }
        "latest_workfile" => {
            let ws = th_pipe::workfiles::department_workspace(&project, &uri(request)?, text(request, "department")?)?;
            json!(th_pipe::workfiles::latest_hip(&ws)?.map(|p| slashed(&p)))
        }
        "folders" => json!(entities::entity_folders(&project, &uri(request)?)?.iter().map(|(_, f)| slashed(f)).collect::<Vec<_>>()),
        "has_files" => json!(entities::has_entity_files(&project, &uri(request)?)?),
        "delete" => {
            let moved = entities::delete_entity(&project, &uri(request)?, text(request, "stamp")?).map_err(|e| e.to_string())?;
            json!(moved.iter().map(|(from, to)| [slashed(from), slashed(to)]).collect::<Vec<_>>())
        }
        "create_shot" => {
            let number = |key: &str| request.get(key).and_then(Value::as_i64).ok_or_else(|| format!("request has no '{key}'"));
            let created = entities::create_shot(
                &project,
                text(request, "sequence")?,
                text(request, "shot")?,
                number("frame_start")?,
                number("frame_end")?,
            )?;
            json!(created.to_string())
        }
        "place_new_members" => {
            let uris = request.get("uris").and_then(Value::as_array).ok_or("request has no 'uris'")?;
            let uris = uris
                .iter()
                .map(|u| u.as_str().ok_or("a URI is text").and_then(|u| Uri::parse(u).map_err(|_| "a bad URI")))
                .collect::<Result<Vec<_>, _>>()?;
            th_pipe::groups::place_new_members(config, &uris)?;
            Value::Null
        }
        "add_group" => {
            let group = th_pipe::groups::add_group(
                config,
                text(request, "context")?,
                text(request, "name")?,
                &uris(request, "members")?,
                &texts(request, "departments")?,
            )?;
            json!(group.to_string())
        }
        "add_member" => {
            th_pipe::groups::add_member(config, &named_uri(request, "group")?, &named_uri(request, "member")?).map(|()| Value::Null)?
        }
        "remove_member" => {
            th_pipe::groups::remove_member(config, &named_uri(request, "group")?, &named_uri(request, "member")?).map(|()| Value::Null)?
        }
        "add_group_department" => {
            th_pipe::groups::add_department(config, &named_uri(request, "group")?, text(request, "department")?).map(|()| Value::Null)?
        }
        "remove_group_department" => {
            th_pipe::groups::remove_department(config, &named_uri(request, "group")?, text(request, "department")?).map(|()| Value::Null)?
        }
        "overlap" => {
            let group = request.get("group").and_then(Value::as_str).map(Uri::parse).transpose().map_err(|e| e.to_string())?;
            json!(th_pipe::groups::overlap(
                config,
                text(request, "context")?,
                &uris(request, "members")?,
                &texts(request, "departments")?,
                group.as_ref()
            )?)
        }
        "rename_entity" => json!(entities::rename_entity(&project, &uri(request)?, text(request, "name")?)?.to_string()),
        "add_channel" => departments::add_channel(config, &uri(request)?, text(request, "name")?).map(|()| Value::Null)?,
        "remove_channel" => departments::remove_channel(config, &uri(request)?, text(request, "name")?).map(|()| Value::Null)?,
        "add_department" => {
            let flag = |key: &str, default: bool| request.get(key).and_then(Value::as_bool).unwrap_or(default);
            let flags = departments::NewDepartment {
                independent: flag("independent", false),
                publishable: flag("publishable", true),
                renderable: flag("renderable", false),
                generated: flag("generated", false),
                enabled: flag("enabled", true),
                short: request.get("short").and_then(Value::as_str).map(str::to_string),
            };
            json!(departments::add_department(config, text(request, "context")?, text(request, "name")?, &flags)?.to_string())
        }
        other => return Err(format!("unknown op '{other}'")),
    })
}

fn main() {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let reply = match serde_json::from_str::<Value>(&line).map_err(|e| e.to_string()).and_then(|request| answer(&request)) {
            Ok(value) => json!({ "ok": value }),
            Err(error) => json!({ "error": error }),
        };
        writeln!(stdout, "{reply}").expect("stdout");
        stdout.flush().expect("stdout");
    }
}
