//! Answers questions about a project, one JSON request per stdin line and
//! one JSON reply per stdout line, for `tests/test_pipe_parity.py` and
//! `tests/test_live_parity.py`, `tests/test_previews_parity.py` and
//! `tests/test_frame_list_parity.py`, which
//! ask Python the same and compare.
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
use th_pipe::{Project, Uri, departments, discovery, entities, events, live, movie, previews, shared_journal, timeline, versions};

thread_local! {
    /// The journal readers `journal_follow` started, by journal path.
    static READERS: std::cell::RefCell<std::collections::HashMap<String, events::JournalReader>> = Default::default();
    /// The shared journal readers `shared_follow` started, by folder.
    static SHARED: std::cell::RefCell<std::collections::HashMap<String, shared_journal::SharedReader>> = Default::default();
}

fn text<'a>(request: &'a Value, key: &str) -> Result<&'a str, String> {
    request.get(key).and_then(Value::as_str).ok_or_else(|| format!("request has no '{key}'"))
}

fn int(request: &Value, key: &str) -> Result<i64, String> {
    request.get(key).and_then(Value::as_i64).ok_or_else(|| format!("request has no whole number '{key}'"))
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

/// The live playblast questions, which need no project: the live folder's
/// names and live.json as it stands. None for any other op.
fn live_answer(request: &Value) -> Option<Result<Value, String>> {
    let answer = match request.get("op")?.as_str()? {
        "live_key" => text(request, "path").map(|p| json!(live::project_key(p))),
        // A change journal reader at `path`, kept between requests:
        // `journal_follow` starts one (following from the end),
        // `journal_poll` is its poll (a list of changes, or null when lines
        // may have been lost).
        "journal_follow" => text(request, "path").map(|p| {
            let mut reader = events::JournalReader::new(p.into());
            reader.poll();
            READERS.with(|r| r.borrow_mut().insert(p.to_string(), reader));
            Value::Null
        }),
        "journal_poll" => text(request, "path").and_then(|p| {
            READERS.with(|r| {
                let mut readers = r.borrow_mut();
                let reader = readers.get_mut(p).ok_or("no reader follows that journal")?;
                Ok(match reader.poll() {
                    None => Value::Null,
                    Some(changes) => json!(changes
                        .iter()
                        .map(|c| json!({"project": c.project, "kind": c.kind, "uri": c.uri, "detail": c.detail, "pid": c.pid, "time": c.time, "id": c.id}))
                        .collect::<Vec<_>>()),
                })
            })
        }),
        // A shared journal reader of the folder `path`, kept between
        // requests: `shared_follow` makes one that passes over changes
        // named before `since` (ms), `shared_poll` is its poll.
        "shared_follow" => text(request, "path").and_then(|p| {
            let since = u64::try_from(int(request, "since")?).map_err(|e| e.to_string())?;
            SHARED.with(|r| r.borrow_mut().insert(p.to_string(), shared_journal::SharedReader::new(p.into(), since)));
            Ok(Value::Null)
        }),
        // Write a change into the shared journal folder `path` at `now`
        // (ms), as Keep as playblast does; the file's name.
        "shared_write" => (|| {
            let event = shared_journal::Event {
                id: text(request, "id")?.into(),
                kind: text(request, "kind")?.into(),
                uri: text(request, "uri")?.into(),
                detail: text(request, "detail")?.into(),
                host: text(request, "host")?.into(),
                time: request.get("time").and_then(Value::as_f64).ok_or("request has no 'time'")?,
            };
            let now = u64::try_from(int(request, "now")?).map_err(|e| e.to_string())?;
            let path = shared_journal::write(Path::new(text(request, "path")?), &event, now).map_err(|e| e.to_string())?;
            Ok(json!(path.file_name().map(|n| n.to_string_lossy().into_owned())))
        })(),
        "shared_poll" => text(request, "path").and_then(|p| {
            SHARED.with(|r| {
                let mut readers = r.borrow_mut();
                let reader = readers.get_mut(p).ok_or("no reader follows that folder")?;
                Ok(json!(reader
                    .poll()
                    .iter()
                    .map(|e| json!({"id": e.id, "kind": e.kind, "uri": e.uri, "detail": e.detail, "host": e.host, "time": e.time}))
                    .collect::<Vec<_>>()))
            })
        }),
        "live_folder" => (|| {
            let folder = live::live_folder(
                Path::new(text(request, "root")?),
                text(request, "project_path")?,
                &texts(request, "segments")?,
                text(request, "department")?,
            )?;
            Ok(json!(slashed(&folder)))
        })(),
        // Which program a running player is: `program` (null: no such
        // process) to one running `own` (null: none given).
        "is_player_program" => Ok(json!(th_pipe::player_rpc::is_player_program(
            request.get("program").and_then(Value::as_str),
            request.get("own").and_then(Value::as_str),
        ))),
        "players_dir" => (|| {
            let folder = live::players_dir(Path::new(text(request, "root")?), text(request, "project_path")?);
            Ok(json!({"folder": slashed(&folder), "protocol": live::PLAYER_PROTOCOL}))
        })(),
        "live_root" => (|| {
            let env = request.get("env").and_then(Value::as_object).ok_or("request has no 'env'")?;
            let root = live::live_root(|name| env.get(name).and_then(Value::as_str).map(str::to_string), text(request, "platform")?);
            Ok(json!(slashed(&root)))
        })(),
        "live_state" => (|| {
            let folder = Path::new(text(request, "folder")?);
            let state = live::read_state(folder).map(|s| live::effective_state(folder, s));
            serde_json::to_value(state).map_err(|e| e.to_string())
        })(),
        "live_fill" => (|| {
            let available: Vec<i64> = request
                .get("available")
                .and_then(Value::as_array)
                .ok_or("request has no 'available'")?
                .iter()
                .filter_map(Value::as_i64)
                .collect();
            let plan = live::fill_plan(int(request, "first")?, int(request, "last")?, &available);
            Ok(json!(plan.into_iter().map(|(f, s)| [f, s]).collect::<Vec<_>>()))
        })(),
        "live_keep_args" => (|| {
            let args = live::keep_args(
                text(request, "pattern")?,
                int(request, "first")?,
                int(request, "last")?,
                int(request, "framerate")?,
                text(request, "output")?,
            )?;
            Ok(json!(args))
        })(),
        // The local farm's preview frames (#75, `test_previews_parity`):
        // a batch folder's ready previews, each with its summary and frame
        // files; one frame file of a pattern; the farm's folder.
        // A Frame List movie's `frame_ranges` tag as Cinedex reads it (#86):
        // its ranges and its pictures' frame numbers, in order.
        "frame_ranges" => text(request, "text").and_then(th_playlist::FrameRanges::parse).map(|list| {
            json!({
                "ranges": list.ranges(),
                "frames": (0..list.count()).map(|i| list.frame_number(i)).collect::<Vec<_>>(),
            })
        }),
        // Whether a render version's context.json marks its review movie as a
        // Frame List render's, shown only pinned (#86).
        "records_frame_ranges" => request.get("context").map(|c| json!(discovery::records_frame_ranges(c))).ok_or_else(|| "request has no 'context'".into()),
        "previews_batch" => (|| {
            let read = previews::batch_previews(Path::new(text(request, "folder")?))?;
            Ok(json!(read
                .iter()
                .map(|p| json!({
                    "job": p.job, "entity": p.entity, "department": p.department, "version": p.version,
                    "frame_path": p.frame_path, "frames": p.frames, "summary": p.summary(),
                    "paths": p.frame_paths().iter().map(|(_, path)| slashed(path)).collect::<Vec<_>>(),
                }))
                .collect::<Vec<_>>()))
        })(),
        "previews_frame_file" => (|| {
            Ok(json!(previews::frame_file(text(request, "pattern")?, int(request, "frame")?).map(|p| slashed(&p))))
        })(),
        "previews_farm_root" => (|| {
            let env = request.get("env").and_then(Value::as_object).ok_or("request has no 'env'")?;
            let root = previews::farm_root(
                |name| env.get(name).and_then(Value::as_str).map(str::to_string),
                text(request, "platform")?,
                Path::new(text(request, "home")?),
            );
            Ok(json!(slashed(&root)))
        })(),
        "movie_export_args" => (|| {
            let size = |key: &str| int(request, key).and_then(|v| u32::try_from(v).map_err(|e| format!("{key}: {e}")));
            let audio = request.get("audio").and_then(Value::as_str);
            let frames = usize::try_from(int(request, "frames")?).map_err(|e| e.to_string())?;
            let args = movie::edit_export_args(
                size("width")?,
                size("height")?,
                size("fps")?,
                frames,
                audio,
                text(request, "start_timecode")?,
                text(request, "output")?,
            )?;
            Ok(json!(args))
        })(),
        _ => return None,
    };
    Some(answer)
}

fn answer(request: &Value) -> Result<Value, String> {
    if let Some(answer) = live_answer(request) {
        return answer;
    }
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
        "next_playblast" => json!(slashed(&discovery::next_playblast_path(&project, &uri(request)?, text(request, "department")?)?)),
        "daily" => json!(slashed(&discovery::daily_path(&project, &uri(request)?, text(request, "department")?)?)),
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
