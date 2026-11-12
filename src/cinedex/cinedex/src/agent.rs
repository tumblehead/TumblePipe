//! The agent commands: what a program (Claude through `cinedex --mcp`,
//! tumblepipe-mcp, a script) can ask a running player to do, through its
//! endpoint (`th_pipe::player_rpc`; `designs/shot-dashboard-live-
//! playblast.md` §8). Each is a JSON-RPC method of the same name whose
//! params are described by a JSON schema, listed by the `tools/list`
//! method in MCP's shape, so the bridge (`mcp`) relays them as they are.
//!
//! Edits are `th_edit` ops (as JSON, `Op`'s serde form): an `edit` call
//! applies its ops as one undo step, all or none, or with `dry_run` says
//! what they would change (`th_edit::diff`) and changes nothing. Publishing
//! is only previewed: `publish` with `dry_run` lists the shot changes a
//! master publish would write (shots made, ranges set, shots retired);
//! the publish itself is done in the window.
//!
//! The editor answers them (`editor/agent.rs`); `events` is answered by the
//! endpoint itself.

use serde_json::{Value, json};

/// A command: its name (the method), what it does, its params' schema,
/// and whether it changes nothing (read only) or what it changes cannot
/// be undone (destructive).
pub(crate) struct Tool {
    pub(crate) name: &'static str,
    pub(crate) description: &'static str,
    pub(crate) schema: fn() -> Value,
    pub(crate) read_only: bool,
    pub(crate) destructive: bool,
}

/// What every edit command's ops look like: `th_edit::ops::Op` as JSON.
fn op_defs() -> Value {
    let int = json!({"type": "integer"});
    let index = json!({"type": "integer", "minimum": 0});
    let track = json!({"$ref": "#/$defs/track"});
    let clip = json!({"$ref": "#/$defs/clip"});
    let op = |name: &str, description: &str, fields: Value| {
        let mut properties = fields.as_object().cloned().unwrap_or_default();
        let required: Vec<Value> = std::iter::once(json!("op")).chain(properties.keys().map(|k| json!(k))).collect();
        properties.insert("op".into(), json!({"const": name}));
        json!({"type": "object", "description": description, "properties": properties, "required": required, "additionalProperties": false})
    };
    let clip_fields = |audio: bool| {
        let mut fields = json!({
            "at": {"type": "integer", "description": "edit frame it starts on (from 0)"},
            "duration": {"type": "integer", "minimum": 1},
            "source_in": {"type": "integer", "description": "the source frame it starts on (a shot's frames are its own, 1001-based)"},
            "source": {"$ref": "#/$defs/source"},
            "fade_in": {"type": "integer", "minimum": 0},
            "fade_out": {"type": "integer", "minimum": 0},
        });
        if audio {
            fields["gain_db"] = json!({"type": "number"});
        }
        let required: Vec<Value> = fields.as_object().unwrap().keys().map(|k| json!(k)).collect();
        if !audio {
            // Optional: 1 (whole) when left out, as the edit file has it.
            fields["opacity"] = json!({"type": "number", "minimum": 0, "maximum": 1});
        }
        json!({"type": "object", "properties": fields, "required": required, "additionalProperties": false})
    };
    json!({
        "track": {
            "type": "object",
            "description": "a track: its kind and index, bottom first",
            "properties": {"kind": {"enum": ["video", "audio"]}, "index": index},
            "required": ["kind", "index"],
            "additionalProperties": false,
        },
        "clip": {
            "type": "object",
            "description": "a clip: its track and its index there (clips are in position order); get_edit lists them",
            "properties": {"track": track, "index": index},
            "required": ["track", "index"],
            "additionalProperties": false,
        },
        "source": {
            "description": "where a clip's frames come from",
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "shot"},
                        "shot": {"type": "string", "description": "entity:/shots/<sequence>/<shot>"},
                        "take": {
                            "type": "object",
                            "description": "which take: a field null is open (no department: the best available, the newest take of the most downstream department)",
                            "properties": {
                                "department": {"type": ["string", "null"]},
                                "kind": {"enum": ["playblast", "render", null]},
                                "layer": {"type": ["string", "null"]},
                                "version": {"type": "string", "description": "\"latest\" or a version such as \"v0003\""},
                            },
                            "required": ["department", "kind", "layer", "version"],
                            "additionalProperties": false,
                        },
                    },
                    "required": ["kind", "shot", "take"],
                    "additionalProperties": false,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "media"},
                        "item": {"type": "string", "description": "a media library item"},
                        "version": {"type": "string", "description": "\"latest\" or a version"},
                    },
                    "required": ["kind", "item", "version"],
                    "additionalProperties": false,
                },
                {
                    "type": "object",
                    "properties": {"kind": {"const": "slug"}, "text": {"type": "string"}},
                    "required": ["kind", "text"],
                    "additionalProperties": false,
                },
            ],
        },
        "new_clip": {
            "description": "a clip to add: {\"video\": {...}} or {\"audio\": {...}}",
            "oneOf": [
                {"type": "object", "properties": {"video": clip_fields(false)}, "required": ["video"], "additionalProperties": false},
                {"type": "object", "properties": {"audio": clip_fields(true)}, "required": ["audio"], "additionalProperties": false},
            ],
        },
        "op": {"oneOf": [
            op("place", "put a clip in free space on a track (at its `at`): nothing moves", json!({"track": track, "clip": {"$ref": "#/$defs/new_clip"}})),
            op("insert", "ripple insert: open the clip's length at its `at` on every track, then put it there", json!({"track": track, "clip": {"$ref": "#/$defs/new_clip"}})),
            op("lift", "take a clip out, leaving a gap", json!({"clip": clip})),
            op("ripple_delete", "take the clip's span out of every track", json!({"clip": clip})),
            op("move", "move a clip to free space at `at` on a track of its kind", json!({"clip": clip, "to": track, "at": int})),
            op("trim", "ripple trim: move an edge by delta frames (positive: later), everything after the edit point with it", json!({"clip": clip, "edge": {"enum": ["head", "tail"]}, "delta": int})),
            op("roll", "move the cut between a clip and the next on its track (they must touch) by delta: the length holds", json!({"clip": clip, "delta": int})),
            op("slip", "show source frames delta later in the same span", json!({"clip": clip, "delta": int})),
            op("split", "split a clip in two at edit frame `at`", json!({"clip": clip, "at": int})),
            op("join", "join a clip with the next on its track (touching in the edit and the source, no fade between)", json!({"clip": clip})),
            op("set_fades", "a clip's fades, in frames", json!({"clip": clip, "fade_in": {"type": "integer", "minimum": 0}, "fade_out": {"type": "integer", "minimum": 0}})),
            op("set_source", "show another source in the same span (a shot's other take: change its take rule); its frames must be there", json!({"clip": clip, "source": {"$ref": "#/$defs/source"}})),
            op("set_opacity", "a video clip's opacity, 0 to 1 (1: whole)", json!({"clip": clip, "opacity": {"type": "number", "minimum": 0, "maximum": 1}})),
            op("set_gain", "an audio clip's level in decibels", json!({"clip": clip, "gain_db": {"type": "number"}})),
            op("add_marker", "a marker at edit frame `at`", json!({"at": int, "note": {"type": "string"}})),
            op("remove_marker", "remove a marker by its index (get_edit lists them)", json!({"index": index})),
            op("add_track", "a new track on top of those of its kind", json!({"kind": {"enum": ["video", "audio"]}, "name": {"type": "string"}})),
            op("remove_track", "remove an empty track", json!({"track": track})),
        ]},
    })
}

fn object(properties: Value, required: &[&str]) -> Value {
    json!({"type": "object", "properties": properties, "required": required, "additionalProperties": false})
}

fn no_params() -> Value {
    object(json!({}), &[])
}

/// Every command the editor answers.
pub(crate) const TOOLS: &[Tool] = &[
    Tool {
        name: "get_state",
        description: "The open edit (name, version, published or working, unsaved changes, whether it is the project's master edit), its format and length, the playhead, playback, the Live view loop, what View > Show plays (`show`: cut, renders or playblasts; never saved), undo/redo and the selection.",
        schema: no_params,
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "get_edit",
        description: "The edit itself (th_edit JSON: tracks bottom first, clips in position order, markers), and per clip its address for ops ({track, index}), name and what it shows now (the take its rule picks, or why nothing plays).",
        schema: no_params,
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "list_shots",
        description: "The project's shots (URI, sequence, name, play range) and media library items, as the bins list them.",
        schema: no_params,
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "list_takes",
        description: "A shot's takes: every department's playblasts and renders (department, kind, version, label), newest last, as a clip's take rule picks from. A take with frame_list true is a Frame List render's movie (never published): only a rule pinning its version picks it.",
        schema: || object(json!({"shot": {"type": "string", "description": "entity:/shots/<sequence>/<shot>"}}), &["shot"]),
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "view_shot",
        description: "Show a shot: its first clip, looping from the clip before to the clip after, the playhead on a shot frame, live playblasts on, playing.",
        schema: || {
            object(
                json!({"shot": {"type": "string", "description": "entity:/shots/<sequence>/<shot>"}, "frame": {"type": "integer", "description": "a shot frame (its own numbering); its first when left out"}}),
                &["shot"],
            )
        },
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "seek",
        description: "Put the playhead on an edit frame (from 0).",
        schema: || object(json!({"frame": {"type": "integer", "minimum": 0}}), &["frame"]),
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "play",
        description: "Start or stop playback.",
        schema: || object(json!({"playing": {"type": "boolean"}}), &["playing"]),
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "edit",
        description: "Apply edit ops to the open edit as one undo step: all of them in order, or none when one is refused (the error says why). With dry_run, change nothing and answer what they would change (clips taken out and put in per track, markers, the length). Clip addresses are {track, index} as get_edit lists them, and each op sees the edit the ops before it made. A published edit is read only.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "ops": {"type": "array", "items": {"$ref": "#/$defs/op"}, "minItems": 1},
                    "dry_run": {"type": "boolean", "default": false},
                },
                "required": ["ops"],
                "additionalProperties": false,
                "$defs": op_defs(),
            })
        },
        read_only: false,
        destructive: false,
    },
    Tool {
        name: "undo",
        description: "Undo the last edit step (the user's or an agent's: they share one history).",
        schema: no_params,
        read_only: false,
        destructive: false,
    },
    Tool { name: "redo", description: "Redo the last undone edit step.", schema: no_params, read_only: false, destructive: false },
    Tool {
        name: "save",
        description: "Save the edit as its next working version (a new file; earlier versions stay). An untitled edit needs a name (letters and digits) and becomes a new edit of the project.",
        schema: || object(json!({"note": {"type": "string"}, "name": {"type": "string", "description": "for an untitled edit"}}), &[]),
        read_only: false,
        destructive: false,
    },
    Tool {
        name: "publish",
        description: "Preview publishing the edit: dry_run must be true (publishing itself is done in the window). For the project's master edit, answers the shot changes the publish would write (planned shots made, frame ranges set, frame rates, shots it no longer uses retired), each with whether it is ticked by default and what to know (a take that no longer covers the new range).",
        schema: || object(json!({"dry_run": {"const": true}}), &["dry_run"]),
        read_only: true,
        destructive: false,
    },
    Tool {
        name: "events",
        description: "The editor's events after number `after` (edit_changed, saved, published, view, take_changed, opened), waiting up to wait_ms (at most 25000) for one when there is none yet. Pass the answer's `next` as the following call's `after`; `lost` says some were dropped (only the last 256 are kept).",
        schema: || {
            object(
                json!({"after": {"type": "integer", "minimum": 0, "default": 0}, "wait_ms": {"type": "integer", "minimum": 0, "maximum": 25000, "default": 0}}),
                &[],
            )
        },
        read_only: true,
        destructive: false,
    },
];

/// A command by name.
pub(crate) fn tool(name: &str) -> Option<&'static Tool> {
    TOOLS.iter().find(|t| t.name == name)
}

/// A command in MCP's `tools/list` shape.
pub(crate) fn describe(tool: &Tool) -> Value {
    json!({
        "name": tool.name,
        "description": tool.description,
        "inputSchema": (tool.schema)(),
        "annotations": {"readOnlyHint": tool.read_only, "destructiveHint": tool.destructive, "idempotentHint": tool.read_only, "openWorldHint": false},
    })
}

/// The `tools/list` answer.
pub(crate) fn list() -> Value {
    json!({"tools": TOOLS.iter().map(describe).collect::<Vec<_>>()})
}

/// Why `params` are not what `tool`'s schema allows at the top level (an
/// unknown param, a required one missing, a wrong JSON type); the deeper
/// checks are the command's own (an op's fields are read by serde).
pub(crate) fn params_problem(tool: &Tool, params: &Value) -> Option<String> {
    let schema = (tool.schema)();
    let Some(given) = params.as_object() else { return Some("params is an object".into()) };
    let properties = schema["properties"].as_object().cloned().unwrap_or_default();
    if let Some(unknown) = given.keys().find(|k| !properties.contains_key(*k)) {
        let known: Vec<&String> = properties.keys().collect();
        return Some(format!("{} takes no {unknown:?} (it takes {known:?})", tool.name));
    }
    for required in schema["required"].as_array().into_iter().flatten().filter_map(Value::as_str) {
        if !given.contains_key(required) {
            return Some(format!("{} wants {required:?}", tool.name));
        }
    }
    for (key, value) in given {
        let want = &properties[key];
        let fits = match want.get("type").and_then(Value::as_str) {
            Some("integer") => value.is_i64() || value.is_u64(),
            Some("number") => value.is_number(),
            Some("string") => value.is_string(),
            Some("boolean") => value.is_boolean(),
            Some("array") => value.is_array(),
            _ => want.get("const").is_none_or(|c| c == value),
        };
        if !fits {
            return Some(format!("{}: {key} is not {}", tool.name, want.get("type").or(want.get("const")).unwrap_or(&json!("allowed"))));
        }
        if let (Some(min), Some(v)) = (want.get("minimum").and_then(Value::as_i64), value.as_i64())
            && v < min
        {
            return Some(format!("{}: {key} is at least {min}", tool.name));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{AudioClip, ClipRef, Rule, Source, TakeKind, TrackKind, TrackRef, Version, VersionRule, VideoClip};
    use th_edit::ops::{Edge, NewClip, Op};

    fn sources() -> impl Strategy<Value = Source> {
        let name = "[A-Za-z0-9]{1,6}";
        let version = prop_oneof![
            Just(VersionRule::Latest),
            (1u32..20).prop_map(|n| VersionRule::Pinned(Version::try_from(format!("v{n:04}")).unwrap()))
        ];
        prop_oneof![
            (
                name,
                prop::option::of(name),
                prop::option::of(prop::sample::select(vec![TakeKind::Playblast, TakeKind::Render])),
                prop::option::of(name),
                version.clone()
            )
                .prop_map(|(shot, department, kind, layer, version)| Source::Shot {
                    shot: format!("entity:/shots/S/{shot}"),
                    take: Rule { department, kind, layer, version }
                }),
            (name, version).prop_map(|(item, version)| Source::Media { item, version }),
            name.prop_map(|text| Source::Slug { text }),
        ]
    }

    /// One op of every kind, from drawn values: the match below has no
    /// wildcard, so a new kind of op fails to compile here until it has a
    /// sample (and the schema a variant).
    fn ops() -> impl Strategy<Value = Vec<Op<Source>>> {
        (sources(), 0usize..3, 0usize..5, -50i64..50, 1i64..80, any::<bool>(), -12.0f64..6.0).prop_map(
            |(source, t, i, n, d, audio, gain)| {
                let track = TrackRef { kind: if audio { TrackKind::Audio } else { TrackKind::Video }, index: t };
                let clip = ClipRef { track, index: i };
                let new = if audio {
                    NewClip::Audio(AudioClip {
                        at: n.abs(),
                        duration: d,
                        source_in: n,
                        source: source.clone(),
                        gain_db: gain,
                        fade_in: 0,
                        fade_out: 1,
                    })
                } else {
                    NewClip::Video(VideoClip {
                        at: n.abs(),
                        duration: d,
                        source_in: n,
                        source: source.clone(),
                        fade_in: 2,
                        fade_out: 0,
                        opacity: if d % 2 == 0 { 0.5 } else { 1.0 },
                    })
                };
                let all = vec![
                    Op::Place { track, clip: new.clone() },
                    Op::Insert { track, clip: new },
                    Op::Lift { clip },
                    Op::RippleDelete { clip },
                    Op::Move { clip, to: track, at: n },
                    Op::Trim { clip, edge: if audio { Edge::Head } else { Edge::Tail }, delta: n },
                    Op::Roll { clip, delta: n },
                    Op::Slip { clip, delta: n },
                    Op::Split { clip, at: n },
                    Op::Join { clip },
                    Op::SetFades { clip, fade_in: d, fade_out: d / 2 },
                    Op::SetSource { clip, source },
                    Op::SetOpacity { clip, opacity: d as f64 / 100.0 },
                    Op::SetGain { clip, gain_db: gain },
                    Op::AddMarker { at: n, note: "note".into() },
                    Op::RemoveMarker { index: i },
                    Op::AddTrack { kind: track.kind, name: "V9".into() },
                    Op::RemoveTrack { track },
                ];
                for op in &all {
                    match op {
                        Op::Place { .. }
                        | Op::Insert { .. }
                        | Op::Lift { .. }
                        | Op::RippleDelete { .. }
                        | Op::Move { .. }
                        | Op::Trim { .. }
                        | Op::Roll { .. }
                        | Op::Slip { .. }
                        | Op::Split { .. }
                        | Op::Join { .. }
                        | Op::SetFades { .. }
                        | Op::SetOpacity { .. }
                        | Op::SetSource { .. }
                        | Op::SetGain { .. }
                        | Op::AddMarker { .. }
                        | Op::RemoveMarker { .. }
                        | Op::AddTrack { .. }
                        | Op::RemoveTrack { .. } => {}
                    }
                }
                all
            },
        )
    }

    /// Does `value` fit `schema` (the subset these schemas use: type,
    /// const, enum, properties, required, additionalProperties, items,
    /// oneOf, $ref into `defs`)?
    fn fits(value: &Value, schema: &Value, defs: &Value) -> Result<(), String> {
        if let Some(r) = schema.get("$ref").and_then(Value::as_str) {
            let name = r.strip_prefix("#/$defs/").ok_or("a $ref outside $defs")?;
            return fits(value, &defs[name], defs);
        }
        if let Some(one) = schema.get("oneOf").and_then(Value::as_array) {
            let matching = one.iter().filter(|s| fits(value, s, defs).is_ok()).count();
            return if matching == 1 { Ok(()) } else { Err(format!("{value} fits {matching} of oneOf")) };
        }
        if let Some(c) = schema.get("const")
            && c != value
        {
            return Err(format!("{value} is not {c}"));
        }
        if let Some(e) = schema.get("enum").and_then(Value::as_array)
            && !e.contains(value)
        {
            return Err(format!("{value} is not one of {e:?}"));
        }
        let type_fits = |t: &str| match t {
            "object" => value.is_object(),
            "array" => value.is_array(),
            "string" => value.is_string(),
            "integer" => value.is_i64() || value.is_u64(),
            "number" => value.is_number(),
            "boolean" => value.is_boolean(),
            "null" => value.is_null(),
            _ => false,
        };
        match schema.get("type") {
            Some(Value::String(t)) if !type_fits(t) => return Err(format!("{value} is not {t}")),
            Some(Value::Array(ts)) if !ts.iter().filter_map(Value::as_str).any(type_fits) => {
                return Err(format!("{value} is none of {ts:?}"));
            }
            _ => {}
        }
        if let (Some(object), Some(properties)) = (value.as_object(), schema.get("properties").and_then(Value::as_object)) {
            for required in schema.get("required").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                if !object.contains_key(required) {
                    return Err(format!("{value} lacks {required}"));
                }
            }
            for (key, v) in object {
                match properties.get(key) {
                    Some(s) => fits(v, s, defs).map_err(|e| format!("{key}: {e}"))?,
                    None if schema.get("additionalProperties") == Some(&json!(false)) => return Err(format!("{key} is not allowed")),
                    None => {}
                }
            }
        }
        if let (Some(items), Some(schema)) = (value.as_array(), schema.get("items")) {
            for item in items {
                fits(item, schema, defs)?;
            }
        }
        Ok(())
    }

    proptest! {
        /// The edit command's schema describes exactly what serde reads:
        /// every op of every kind, as JSON, fits it (one variant only), and
        /// a field taken out of it or added to it does not.
        #[test]
        fn the_op_schema_is_what_serde_reads(ops in ops()) {
            let schema = (tool("edit").unwrap().schema)();
            let defs = schema["$defs"].clone();
            for op in ops {
                let json = serde_json::to_value(&op).unwrap();
                prop_assert!(fits(&json, &defs["op"], &defs).is_ok(), "{}: {:?}", json, fits(&json, &defs["op"], &defs));
                let mut extra = json.clone();
                extra["bogus"] = json!(1);
                prop_assert!(fits(&extra, &defs["op"], &defs).is_err());
                for key in json.as_object().unwrap().keys().filter(|k| *k != "op") {
                    let mut less = json.clone();
                    less.as_object_mut().unwrap().remove(key);
                    prop_assert!(fits(&less, &defs["op"], &defs).is_err(), "{} without {} fits", json, key);
                    prop_assert!(serde_json::from_value::<Op<Source>>(less).is_err());
                }
            }
        }
    }

    /// Every command is listed once, in MCP's shape, with an object schema;
    /// the ones the editor changes nothing with say so.
    #[test]
    fn the_commands_are_listed_in_mcps_shape() {
        let listed = list();
        let tools = listed["tools"].as_array().unwrap();
        assert_eq!(tools.len(), TOOLS.len());
        let mut names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        names.sort();
        names.dedup();
        assert_eq!(names.len(), TOOLS.len(), "names are unique");
        for t in tools {
            assert_eq!(t["inputSchema"]["type"], json!("object"), "{}", t["name"]);
            assert!(t["description"].as_str().is_some_and(|d| !d.is_empty()));
        }
        assert!(tool("get_edit").unwrap().read_only);
        assert!(!tool("edit").unwrap().read_only);
    }

    #[test]
    fn params_outside_the_schema_are_named() {
        let edit = tool("edit").unwrap();
        assert!(params_problem(edit, &json!({})).unwrap().contains("ops"));
        assert!(params_problem(edit, &json!({"ops": [], "bogus": 1})).unwrap().contains("bogus"));
        assert!(params_problem(edit, &json!({"ops": 3})).is_some());
        assert!(params_problem(edit, &json!({"ops": [], "dry_run": true})).is_none());
        let seek = tool("seek").unwrap();
        assert!(params_problem(seek, &json!({"frame": -1})).is_some());
        assert!(params_problem(seek, &json!({"frame": 10})).is_none());
        let publish = tool("publish").unwrap();
        assert!(params_problem(publish, &json!({"dry_run": false})).is_some());
        assert!(params_problem(publish, &json!({"dry_run": true})).is_none());
        assert!(params_problem(tool("get_state").unwrap(), &json!([])).is_some());
    }
}
