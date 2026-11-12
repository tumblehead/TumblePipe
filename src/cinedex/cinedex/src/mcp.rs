//! `cinedex --mcp [--project <root>]`: an MCP server on stdin/stdout
//! (newline-delimited JSON-RPC 2.0) that gives an agent the running
//! players' commands (`crate::agent`) as tools. It opens no window: each
//! tool call finds a player of this user through its endpoint file
//! (`th_pipe::player_rpc`) and relays the call there, its answer back.
//!
//! The project is `--project`, else `$TH_PROJECT_PATH`, else any: with
//! one, only its players are asked. Every tool takes an optional `player`
//! (a pid, as `list_players` lists them) for when more than one runs.

use std::io::{BufRead, BufReader, Write};
use std::net::{Ipv4Addr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Value, json};
use th_pipe::live::{PLAYERS_DIR, PlayerEndpoint};

use crate::agent;

/// The MCP version answered when the client asks for none it knows.
const PROTOCOL: &str = "2025-06-18";
/// How long a relayed call may take: the player's own limit (a save) and
/// an `events` wait, with room.
const RELAY_WITHIN: Duration = Duration::from_secs(90);

const INSTRUCTIONS: &str = "Drives the running TumblePipe editor (Cinedex) of this user: read the open edit and the project's \
shots and takes, edit the cut with th_edit ops (one undo step per call; dry_run to preview), save working versions, preview a master \
publish. Start with get_state and get_edit; clip addresses are {track: {kind, index}, index} as get_edit lists them. Follow what \
happens with events (pass `next` back as `after`). The user sees every change in the window and can undo it.";

/// A player found: its pid, its project's key and how to reach it.
#[derive(Debug, Clone)]
pub(crate) struct Player {
    pub(crate) project_key: String,
    pub(crate) endpoint: PlayerEndpoint,
}

/// Where the bridge looks for players: the live root, and the project
/// (its path) when one is named.
pub(crate) struct Bridge {
    pub(crate) live_root: PathBuf,
    pub(crate) project: Option<String>,
}

impl Bridge {
    /// The players running, of the project when one is named.
    pub(crate) fn players(&self) -> Vec<Player> {
        let root = self.live_root.join(PLAYERS_DIR);
        let dirs: Vec<PathBuf> = match &self.project {
            Some(project) => vec![th_pipe::live::players_dir(&self.live_root, project)],
            None => std::fs::read_dir(&root).map(|r| r.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect()).unwrap_or_default(),
        };
        let mut found = Vec::new();
        for dir in dirs {
            let project_key = dir.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let Some(endpoint) = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<PlayerEndpoint>(&b).ok()) else {
                    continue;
                };
                // A pid since given to another program is no player.
                if th_pipe::player_rpc::is_player(endpoint.pid) {
                    found.push(Player { project_key: project_key.clone(), endpoint });
                }
            }
        }
        found.sort_by_key(|p| p.endpoint.pid);
        found
    }

    /// Answer one line from the client; None for a notification (no id),
    /// which is answered with nothing.
    pub(crate) fn answer(&self, line: &str) -> Option<Value> {
        let message: Value = match serde_json::from_str(line.trim()) {
            Ok(m) => m,
            Err(_) => return Some(json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "not JSON"}})),
        };
        let id = message.get("id").cloned().filter(|id| !id.is_null())?;
        let params = message.get("params").cloned().unwrap_or(json!({}));
        let result =
            match message.get("method").and_then(Value::as_str).unwrap_or("") {
                "initialize" => {
                    let asked = params.get("protocolVersion").and_then(Value::as_str).unwrap_or(PROTOCOL);
                    Ok(json!({
                        "protocolVersion": if asked.is_empty() { PROTOCOL } else { asked },
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "cinedex", "version": env!("CARGO_PKG_VERSION")},
                        "instructions": INSTRUCTIONS,
                    }))
                }
                "ping" => Ok(json!({})),
                "tools/list" => Ok(tools()),
                "tools/call" => Ok(self
                    .call(params.get("name").and_then(Value::as_str).unwrap_or(""), params.get("arguments").cloned().unwrap_or(json!({})))),
                other => Err(format!("no method {other:?}")),
            };
        Some(match result {
            Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
            Err(message) => json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": message}}),
        })
    }

    /// A tool call, as MCP's tool result.
    fn call(&self, name: &str, arguments: Value) -> Value {
        let mut arguments = match arguments {
            Value::Object(map) => map,
            Value::Null => serde_json::Map::new(),
            _ => return tool_error("arguments is an object"),
        };
        let pid = match arguments.remove("player") {
            None | Some(Value::Null) => None,
            Some(v) => match v.as_u64().and_then(|v| u32::try_from(v).ok()) {
                Some(pid) => Some(pid),
                None => return tool_error("player is a pid (list_players lists them)"),
            },
        };
        if name == "list_players" {
            return tool_ok(json!({"players": self.players().iter().map(describe_player).collect::<Vec<_>>()}));
        }
        if agent::tool(name).is_none() {
            return tool_error(&format!("no tool {name:?}"));
        }
        let player = match self.choose(pid) {
            Ok(p) => p,
            Err(e) => return tool_error(&e),
        };
        match relay(&player.endpoint, name, Value::Object(arguments)) {
            Ok(result) => tool_ok(result),
            Err(e) => tool_error(&e),
        }
    }

    /// The player to ask: the one with `pid`, or the only one.
    fn choose(&self, pid: Option<u32>) -> Result<Player, String> {
        let players = self.players();
        let scope = self.project.as_deref().map_or(String::new(), |p| format!(" for {p}"));
        match pid {
            Some(pid) => players.into_iter().find(|p| p.endpoint.pid == pid).ok_or_else(|| format!("no player {pid} is running{scope}")),
            None => match players.len() {
                0 => Err(format!("no player is running{scope}: open the editor (Play Shots or an edit in the asset browser) first")),
                1 => Ok(players.into_iter().next().unwrap()),
                _ => {
                    let listed: Vec<String> = players.iter().map(|p| describe_player(p).to_string()).collect();
                    Err(format!("{} players are running: name one with `player` (pid): {}", players.len(), listed.join("; ")))
                }
            },
        }
    }
}

/// A player for `list_players`: its pid, its project's key and, when it
/// answers, its edit.
fn describe_player(player: &Player) -> Value {
    let state = relay(&player.endpoint, "get_state", json!({})).ok();
    json!({
        "player": player.endpoint.pid,
        "project": player.project_key,
        "title": state.as_ref().map(|s| s["title"].clone()),
        "master": state.as_ref().map(|s| s["master"].clone()),
    })
}

/// Call `method` with `params` on the player at `endpoint`.
pub(crate) fn relay(endpoint: &PlayerEndpoint, method: &str, params: Value) -> Result<Value, String> {
    let mut params = match params {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    params.insert("token".into(), json!(endpoint.token));
    let stream =
        TcpStream::connect((Ipv4Addr::LOCALHOST, endpoint.port)).map_err(|e| format!("player {} does not answer: {e}", endpoint.pid))?;
    stream.set_read_timeout(Some(RELAY_WITHIN)).map_err(|e| e.to_string())?;
    let line = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    (&stream).write_all(format!("{line}\n").as_bytes()).map_err(|e| e.to_string())?;
    let mut reply = String::new();
    BufReader::new(&stream).read_line(&mut reply).map_err(|e| format!("player {}: {e}", endpoint.pid))?;
    let reply: Value = serde_json::from_str(&reply).map_err(|_| format!("player {} answered no JSON", endpoint.pid))?;
    match reply.get("error") {
        Some(error) => Err(error["message"].as_str().unwrap_or("refused").to_owned()),
        None => Ok(reply["result"].clone()),
    }
}

fn tool_ok(result: Value) -> Value {
    let text = serde_json::to_string_pretty(&result).unwrap_or_default();
    let structured = if result.is_object() { result } else { json!({"value": result}) };
    json!({"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": false})
}

fn tool_error(message: &str) -> Value {
    json!({"content": [{"type": "text", "text": message}], "isError": true})
}

/// The tools: the editor's commands, each also taking `player`, and
/// `list_players`.
pub(crate) fn tools() -> Value {
    let mut tools: Vec<Value> = agent::TOOLS
        .iter()
        .map(|tool| {
            let mut described = agent::describe(tool);
            described["inputSchema"]["properties"]["player"] =
                json!({"type": "integer", "description": "the player (pid) to ask, when more than one runs (list_players)"});
            described
        })
        .collect();
    tools.push(json!({
        "name": "list_players",
        "description": "The editors (cinedex) of this user running, of the project when the server has one: pid, project, the edit open, whether it is the master edit.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": false},
        "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
    }));
    json!({"tools": tools})
}

/// Serve MCP on stdin/stdout until stdin closes.
pub(crate) fn run(project: Option<&Path>) -> std::io::Result<()> {
    let bridge = Bridge { live_root: th_pipe::live::this_live_root(), project: project.map(th_pipe::storage::slashed) };
    let stdout = std::io::stdout();
    for line in std::io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        if let Some(reply) = bridge.answer(&line) {
            let mut out = stdout.lock();
            writeln!(out, "{reply}")?;
            out.flush()?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::sync::{Arc, Mutex};
    use th_pipe::player_rpc::{self, Call, Events, Refusal, Request};

    /// A player of project `project` under a temporary live root, its editor
    /// a stand-in that records each call and answers `{"echo": params}`, or
    /// refuses the method "nope".
    fn stand_in(root: &Path, project: &str) -> (player_rpc::Server, Arc<Mutex<Vec<Request>>>) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let log = Arc::clone(&seen);
        let dir = th_pipe::live::players_dir(root, project);
        let server = player_rpc::serve(&dir, Events::new(), move |call: Call| {
            log.lock().unwrap().push(call.request.clone());
            match &call.request {
                Request::Call { method, .. } if method == "nope" => call.answer(Err(Refusal::new(player_rpc::REFUSED, "not today"))),
                Request::Call { params, .. } => call.answer(Ok(json!({"echo": params}))),
                Request::ViewShot { .. } => call.answer(Ok(json!({}))),
            }
            true
        })
        .unwrap();
        (server, seen)
    }

    fn call(bridge: &Bridge, name: &str, arguments: Value) -> Value {
        let line = json!({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}).to_string();
        let reply = bridge.answer(&line).unwrap();
        assert_eq!(reply["id"], json!(7));
        reply["result"].clone()
    }

    fn scalars() -> impl Strategy<Value = Value> {
        prop_oneof![
            any::<i64>().prop_map(|n| json!(n)),
            "[a-z ]{0,8}".prop_map(|s| json!(s)),
            any::<bool>().prop_map(|b| json!(b)),
            Just(Value::Null)
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 24, ..ProptestConfig::default() })]
        /// Whatever arguments a tool call carries, the player is handed
        /// exactly them under the tool's name (the bridge's `player` taken
        /// out, the endpoint's token not among them), and what it answers
        /// comes back as the tool's structured result.
        #[test]
        fn a_tool_call_reaches_the_player_as_it_was_made(
            arguments in prop::collection::btree_map("[a-z_]{1,8}", scalars(), 0..5),
            name_player in any::<bool>(),
            tool in prop::sample::select(vec!["get_state", "edit", "seek", "list_takes"]),
        ) {
            let root = tempfile::tempdir().unwrap();
            let (_server, seen) = stand_in(root.path(), "P:/show");
            let bridge = Bridge { live_root: root.path().to_path_buf(), project: Some("P:/show".into()) };
            let mut given: serde_json::Map<String, Value> = arguments.into_iter().filter(|(k, _)| k != "player" && k != "token").collect();
            let want = Value::Object(given.clone());
            if name_player {
                given.insert("player".into(), json!(std::process::id()));
            }
            let result = call(&bridge, tool, Value::Object(given));
            prop_assert_eq!(&result["isError"], &json!(false));
            prop_assert_eq!(&result["structuredContent"]["echo"], &want);
            prop_assert_eq!(seen.lock().unwrap().clone(), vec![Request::Call { method: tool.into(), params: want }]);
        }
    }

    #[test]
    fn refusals_and_missing_players_are_tool_errors() {
        let root = tempfile::tempdir().unwrap();
        let bridge = Bridge { live_root: root.path().to_path_buf(), project: Some("P:/show".into()) };
        let none = call(&bridge, "get_state", json!({}));
        assert_eq!(none["isError"], json!(true));
        assert!(none["content"][0]["text"].as_str().unwrap().contains("no player is running"));
        let (_server, seen) = stand_in(root.path(), "P:/show");
        assert_eq!(call(&bridge, "bogus", json!({}))["isError"], json!(true), "not a tool");
        assert_eq!(call(&bridge, "get_state", json!({"player": 1}))["isError"], json!(true), "no such pid");
        assert!(seen.lock().unwrap().is_empty());
        // A player of another project is not asked when the bridge has one.
        let other = Bridge { live_root: root.path().to_path_buf(), project: Some("P:/other".into()) };
        assert_eq!(call(&other, "get_state", json!({}))["isError"], json!(true));
        // With no project, any player of this user is.
        let any = Bridge { live_root: root.path().to_path_buf(), project: None };
        assert_eq!(call(&any, "get_state", json!({}))["isError"], json!(false));
        let players = call(&any, "list_players", json!({}));
        assert_eq!(players["structuredContent"]["players"][0]["player"], json!(std::process::id()));
    }

    #[test]
    fn the_handshake_lists_the_commands_and_notifications_get_no_answer() {
        let bridge = Bridge { live_root: PathBuf::from("."), project: None };
        let init = bridge.answer(r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}"#).unwrap();
        assert_eq!(init["result"]["protocolVersion"], json!("2025-03-26"));
        assert!(init["result"]["capabilities"]["tools"].is_object());
        assert!(bridge.answer(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#).is_none());
        let listed = bridge.answer(r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#).unwrap();
        let tools = listed["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), agent::TOOLS.len() + 1);
        assert!(tools.iter().all(|t| t["inputSchema"]["type"] == json!("object")));
        assert!(tools.iter().filter(|t| t["name"] != json!("list_players")).all(|t| t["inputSchema"]["properties"]["player"].is_object()));
        assert_eq!(bridge.answer(r#"{"jsonrpc":"2.0","id":3,"method":"bogus"}"#).unwrap()["error"]["code"], json!(-32601));
        assert_eq!(bridge.answer("{nope").unwrap()["error"]["code"], json!(-32700));
    }
}
