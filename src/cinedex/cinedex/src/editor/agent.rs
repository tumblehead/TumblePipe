//! The agent commands (`crate::agent`), answered by the editor: each call
//! through the player's endpoint that is not `ping`, `view_shot` (Houdini's
//! Live view, master edit only) or `events` comes here. An agent's edits go
//! through the same document and undo history as the user's; the window
//! shows them as they land.

use serde_json::{Value, json};
use th_edit::document::{ClipRef, Source};
use th_edit::ops::Op;
use th_pipe::edits::EditSources;
use th_pipe::master::{self, Change};

use super::{Command, Editor, Message, Origin, editing, source_name};
use crate::agent;
use crate::rpc::{self, Call, Refusal};

fn refused(message: impl Into<String>) -> Refusal {
    Refusal::new(rpc::REFUSED, message)
}

fn clip_ref(clip: ClipRef) -> Value {
    serde_json::to_value(clip).expect("a clip address is JSON")
}

/// A master publish's shot change, for an agent.
fn change_json(change: &Change, ticked: bool, flag: Option<&str>) -> Value {
    let kind = match change {
        Change::Create { .. } => "create",
        Change::Range { .. } => "range",
        Change::Fps { .. } => "fps",
        Change::Retire { .. } => "retire",
    };
    json!({"kind": kind, "shot": change.shot().to_string(), "change": change.describe(), "ticked": ticked, "flag": flag})
}

/// What an agent's `save` of a document from `origin` saves: the edit's
/// name and whether it is made first (an untitled edit, named `name`).
fn save_target(origin: &Origin, name: Option<String>) -> Result<(String, bool), Refusal> {
    match (origin, name) {
        (Origin::Published { .. }, _) => Err(refused("a published version is read-only: Save As in the window starts a working version")),
        (Origin::Working { name, .. }, None) => Ok((name.clone(), false)),
        (Origin::Working { name, .. }, Some(given)) if given == *name => Ok((given, false)),
        (Origin::Working { name, .. }, Some(_)) => {
            Err(Refusal::new(rpc::BAD_PARAMS, format!("this is {name}: a name is only for an untitled edit")))
        }
        (Origin::Untitled, None) => Err(Refusal::new(rpc::BAD_PARAMS, "an untitled edit needs a name to save")),
        (Origin::Untitled, Some(given)) => match editing::edit_name_problem(&given) {
            Some(problem) => Err(Refusal::new(rpc::BAD_PARAMS, format!("{given:?}: an edit's name {problem}"))),
            None => Ok((given, true)),
        },
    }
}

impl Editor {
    /// Answer agent command `method` with `params` (the token taken out).
    /// Most are answered at once; `list_takes` and `save` when the work they
    /// start is done.
    pub(super) fn agent_call(&mut self, call: Call, method: &str, params: &Value) -> Command<Message> {
        if method == "tools/list" {
            call.answer(Ok(agent::list()));
            return Command::none();
        }
        let Some(tool) = agent::tool(method) else {
            call.answer(Err(Refusal::new(rpc::NO_METHOD, format!("no command {method:?} (tools/list lists them)"))));
            return Command::none();
        };
        if let Some(problem) = agent::params_problem(tool, params) {
            call.answer(Err(Refusal::new(rpc::BAD_PARAMS, problem)));
            return Command::none();
        }
        let text = |key: &str| params.get(key).and_then(Value::as_str).map(str::to_owned);
        let (answer, command) = match method {
            "get_state" => (Ok(self.agent_state()), Command::none()),
            "get_edit" => (Ok(self.agent_edit()), Command::none()),
            "list_shots" => (Ok(self.agent_shots()), Command::none()),
            "list_takes" => return self.agent_takes(call, text("shot").unwrap_or_default()),
            "view_shot" => {
                let shot = text("shot").unwrap_or_default();
                if !shot.starts_with("entity:/shots/") {
                    (Err(Refusal::new(rpc::BAD_PARAMS, "view_shot wants a shot (entity:/shots/...)")), Command::none())
                } else {
                    self.view_shot(&shot, params.get("frame").and_then(Value::as_i64), false)
                }
            }
            "seek" => {
                let frame = params["frame"].as_u64().unwrap_or(0) as usize;
                self.move_to(frame);
                self.leave_loop_outside();
                (Ok(json!({"playhead": self.position})), Command::none())
            }
            "play" => {
                self.set_playing(params["playing"].as_bool().unwrap_or(false));
                (Ok(json!({"playing": self.playing})), Command::none())
            }
            "edit" => self.agent_edit_ops(params),
            "undo" | "redo" => {
                let done = if method == "undo" { self.doc.undo() } else { self.doc.redo() };
                let command = if done { self.changed() } else { Command::none() };
                (Ok(json!({"done": done, "length": self.doc.edit.length()})), command)
            }
            "save" => return self.agent_save(call, text("name"), text("note").unwrap_or_default()),
            "publish" => (self.agent_publish_preview(), Command::none()),
            // Answered by the endpoint itself, never handed over.
            _ => (Err(refused(format!("{method} is answered by the endpoint"))), Command::none()),
        };
        call.answer(answer);
        command
    }

    fn agent_state(&self) -> Value {
        let (name, version, published) = match &self.doc.origin {
            Origin::Untitled => (None, None, false),
            Origin::Working { name, version } => (Some(name.clone()), version.clone(), false),
            Origin::Published { name, version } => (Some(name.clone()), Some(version.clone()), true),
        };
        let edit = &self.doc.edit;
        json!({
            "title": self.doc.title(),
            "edit": name,
            "version": version,
            "published": published,
            "read_only": self.doc.read_only(),
            "dirty": self.doc.dirty(),
            "master": name.is_some() && self.bins.master == name,
            "master_edit": self.bins.master,
            "fps": edit.fps,
            "resolution": edit.resolution,
            "start_timecode": edit.start_timecode,
            "length": edit.length(),
            "playhead": self.position,
            "timecode": editing::timecode(self.position as i64, edit),
            "playing": self.playing,
            "loop": self.loop_range().map(|(start, end)| json!({"start": start, "end": end})),
            "live": self.show_live,
            "show": super::show_word(self.show),
            "can_undo": self.doc.can_undo(),
            "can_redo": self.doc.can_redo(),
            "selection": self.selection.iter().map(|c| clip_ref(*c)).collect::<Vec<_>>(),
            "status": self.status.as_ref().map(|(text, error)| json!({"text": text, "error": error})),
        })
    }

    fn agent_edit(&self) -> Value {
        let edit = &self.doc.edit;
        let mut clips = Vec::new();
        for track in edit.tracks() {
            for (index, (at, end)) in edit.spans(track).into_iter().enumerate() {
                let clip = ClipRef { track, index };
                let source = match track.kind {
                    th_edit::document::TrackKind::Video => &edit.video[track.index].clips[index].source,
                    th_edit::document::TrackKind::Audio => &edit.audio[track.index].clips[index].source,
                };
                let entry = self.entry_of(source).map(|i| &self.entries[i]);
                clips.push(json!({
                    "clip": clip_ref(clip),
                    "track_name": edit.track_name(track),
                    "at": at,
                    "duration": end - at,
                    "name": source_name(source),
                    "shows": entry.map(|e| e.detail()),
                    "missing": entry.is_some_and(|e| e.missing()),
                }));
            }
        }
        json!({"edit": serde_json::to_value(edit).expect("an edit is JSON"), "clips": clips})
    }

    fn agent_shots(&self) -> Value {
        let shots: Vec<Value> = self
            .bins
            .shots
            .iter()
            .map(|s| json!({"shot": s.uri, "sequence": s.sequence, "name": s.name, "first": s.first, "last": s.last}))
            .collect();
        let media: Vec<Value> = self
            .bins
            .media
            .iter()
            .map(|m| json!({"item": m.item, "version": m.version, "frames": m.frames, "sound_frames": m.sound}))
            .collect();
        json!({"shots": shots, "media": media})
    }

    /// A shot's takes, listed off the UI thread (a file server is slow).
    fn agent_takes(&self, call: Call, shot: String) -> Command<Message> {
        let (paths, fps) = (self.paths.clone(), self.fps());
        Command::perform_blocking(
            move || {
                let project = paths.open()?;
                let takes = EditSources::new(&project, fps).takes(&shot)?;
                let listed: Vec<Value> = takes
                    .iter()
                    .map(|t| {
                        json!({
                            "label": t.label,
                            "department": t.department,
                            "version": t.version,
                            "kind": th_edit::takes::kind_of(t),
                            "media": t.media,
                            "frame_list": t.multi_range,
                        })
                    })
                    .collect();
                Ok(json!({"shot": shot, "takes": listed}))
            },
            move |result: Result<Value, String>| Message::Answer(call.clone(), result.map_err(refused)),
        )
    }

    /// `edit`: the ops as one undo step, or with `dry_run` what they would
    /// change.
    fn agent_edit_ops(&mut self, params: &Value) -> (Result<Value, Refusal>, Command<Message>) {
        let ops: Vec<Op<Source>> = match serde_json::from_value(params["ops"].clone()) {
            Ok(ops) => ops,
            Err(e) => return (Err(Refusal::new(rpc::BAD_PARAMS, format!("ops: {e}"))), Command::none()),
        };
        if ops.is_empty() {
            return (Err(Refusal::new(rpc::BAD_PARAMS, "ops: at least one")), Command::none());
        }
        let project = match self.paths.open() {
            Ok(p) => p,
            Err(e) => return (Err(refused(e)), Command::none()),
        };
        let sources = EditSources::new(&project, self.fps());
        if params.get("dry_run").and_then(Value::as_bool).unwrap_or(false) {
            if self.doc.read_only() {
                return (Err(refused("a published version is read-only: Save As to change it")), Command::none());
            }
            let mut after = self.doc.edit.clone();
            for (i, op) in ops.iter().enumerate() {
                match th_edit::ops::apply(&after, op, &sources) {
                    Ok(next) => after = next,
                    Err(e) => return (Err(refused(format!("op {i}: {e}"))), Command::none()),
                }
            }
            let diff = th_edit::diff::diff(&self.doc.edit, &after);
            return (Ok(json!({"dry_run": true, "diff": diff})), Command::none());
        }
        let before = self.doc.edit.clone();
        match self.doc.apply(&ops, &sources) {
            Ok(()) => {
                self.status = None;
                let diff = th_edit::diff::diff(&before, &self.doc.edit);
                let command = self.changed();
                (Ok(json!({"dry_run": false, "diff": diff})), command)
            }
            Err(e) => (Err(refused(e.0)), Command::none()),
        }
    }

    /// `save`: the next working version, answered when it is written.
    fn agent_save(&mut self, call: Call, name: Option<String>, note: String) -> Command<Message> {
        if self.dialog.is_some() {
            call.answer(Err(refused("the window has a dialog open: try again when it is closed")));
            return Command::none();
        }
        let (name, create) = match save_target(&self.doc.origin, name) {
            Ok(target) => target,
            Err(refusal) => {
                call.answer(Err(refusal));
                return Command::none();
            }
        };
        self.agent_save = Some(call);
        self.save(name, note, create)
    }

    /// `publish` with `dry_run`: what publishing would write.
    fn agent_publish_preview(&self) -> Result<Value, Refusal> {
        let name = match &self.doc.origin {
            Origin::Working { name, .. } => name.clone(),
            Origin::Untitled => return Err(refused("an untitled edit is saved before it is published")),
            Origin::Published { name, version } => return Err(refused(format!("{name} {version} is a published version"))),
        };
        let project = self.paths.open().map_err(refused)?;
        let is_master = master::master_edit(&project).map_err(refused)?.as_deref() == Some(name.as_str());
        let changes = if is_master { self.master_changes(&project, &name).map_err(refused)? } else { Vec::new() };
        Ok(json!({
            "dry_run": true,
            "edit": name,
            "master": is_master,
            "dirty": self.doc.dirty(),
            "changes": changes.iter().map(|row| change_json(&row.change, row.ticked, row.flag.as_deref())).collect::<Vec<_>>(),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// An agent saves a working edit under its own name, makes an
        /// untitled one under a good name it gives, and never saves a
        /// published version, renames a working one, or an untitled one
        /// without a name or under a bad one.
        #[test]
        fn saves_go_where_the_window_would(
            kind in 0u8..3,
            own in "[A-Za-z][A-Za-z0-9]{0,7}",
            given in prop::option::of(prop_oneof!["[A-Za-z][A-Za-z0-9]{0,7}", "[a-z]{1,4}[ _/-][a-z]{1,4}", Just(String::new())]),
        ) {
            let origin = match kind {
                0 => Origin::Untitled,
                1 => Origin::Working { name: own.clone(), version: Some("v0003".into()) },
                _ => Origin::Published { name: own.clone(), version: "v0002".into() },
            };
            let result = save_target(&origin, given.clone());
            match (&origin, &given) {
                (Origin::Published { .. }, _) => prop_assert_eq!(result.unwrap_err().code, rpc::REFUSED),
                (Origin::Working { .. }, None) => prop_assert_eq!(result.unwrap(), (own.clone(), false)),
                (Origin::Working { .. }, Some(g)) if *g == own => prop_assert_eq!(result.unwrap(), (own.clone(), false)),
                (Origin::Working { .. }, Some(_)) | (Origin::Untitled, None) => prop_assert_eq!(result.unwrap_err().code, rpc::BAD_PARAMS),
                (Origin::Untitled, Some(g)) => match editing::edit_name_problem(g) {
                    Some(_) => prop_assert_eq!(result.unwrap_err().code, rpc::BAD_PARAMS),
                    None => prop_assert_eq!(result.unwrap(), (g.clone(), true)),
                },
            }
        }
    }

    #[test]
    fn changes_say_their_kind() {
        let shot = th_pipe::Uri::parse_address("entity:/shots/SQ010/SH020").unwrap();
        let retire = change_json(&Change::Retire { shot: shot.clone() }, false, None);
        assert_eq!((retire["kind"].clone(), retire["ticked"].clone()), (json!("retire"), json!(false)));
        assert_eq!(retire["shot"], json!("entity:/shots/SQ010/SH020"));
        let range = change_json(&Change::Range { shot, from: Some((1001, 1040)), first: 1001, last: 1050 }, true, Some("needs a new take"));
        assert_eq!(range["kind"], json!("range"));
        assert_eq!(range["flag"], json!("needs a new take"));
    }
}
