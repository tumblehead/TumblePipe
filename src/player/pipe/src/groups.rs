//! Multis' member lists, as far as creating and deleting an entity touch
//! them (`tumblepipe.config.groups`, `tumblepipe.config.multi_timeline`).
//!
//! A Multi lists its members by URI, so an entity that goes must be taken
//! out of every Multi's list and off its shared timeline: left there, the
//! Multi's template lays out a graph for an entity that is gone. A shot made
//! in a sequence a Multi lists is its member at once, and goes on its shared
//! timeline ([`place_new_members`]).

use std::collections::{BTreeMap, HashMap};

use serde_json::{Map, Value};

use crate::config::{Config, Properties};
use crate::uri::Uri;

/// What one Multi held before a change: its URI, its own member list and
/// its timeline starts (member URI text to frame), for [`restore_members`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Saved {
    pub group: Uri,
    pub members: Vec<String>,
    pub starts: Vec<(String, i64)>,
}

/// Every Multi of `context` (`shots`, `assets`): its URI and resolved
/// properties.
fn list_groups(config: &Config, context: &str) -> Result<Vec<(Uri, Properties)>, String> {
    let mut groups = Vec::new();
    for uri in config.list_entity_uris(&Uri::new("groups", [context]), false).map_err(|e| e.to_string())? {
        let properties = config.get_properties(&uri).map_err(|e| e.to_string())?.unwrap_or_default();
        groups.push((uri, properties));
    }
    Ok(groups)
}

fn stored_members(properties: &Map<String, Value>) -> Result<Vec<Uri>, String> {
    let Some(Value::Array(members)) = properties.get("members") else { return Ok(Vec::new()) };
    members
        .iter()
        .map(|m| m.as_str().ok_or_else(|| format!("member {m} is not a URI")).and_then(|s| Uri::parse(s).map_err(|e| e.to_string())))
        .collect()
}

fn timeline_starts(properties: &Map<String, Value>) -> Result<Vec<(String, i64)>, String> {
    let Some(Value::Object(starts)) = properties.get("timeline_starts") else { return Ok(Vec::new()) };
    starts
        .iter()
        .map(|(member, frame)| {
            let frame = frame
                .as_i64()
                .or_else(|| frame.as_f64().map(|f| f.trunc() as i64))
                .ok_or_else(|| format!("start {frame} of {member} is not a frame"))?;
            Ok((member.clone(), frame))
        })
        .collect()
}

fn is_or_under(entity: &Uri, member: &str) -> bool {
    Uri::parse(member).is_ok_and(|member| entity.is_or_contains(&member))
}

/// The Multis listing `entity` or anything under it, or with one of them on
/// their shared timeline, with what they hold (`_members_of`).
fn members_of(config: &Config, entity: &Uri) -> Result<Vec<Saved>, String> {
    if entity.purpose != "entity" || entity.segments.is_empty() {
        return Ok(Vec::new());
    }
    let mut found = Vec::new();
    for (group, properties) in list_groups(config, &entity.segments[0])? {
        let listed = stored_members(&properties)?;
        let starts = timeline_starts(&properties)?;
        if listed.iter().any(|m| entity.is_or_contains(m)) || starts.iter().any(|(m, _)| is_or_under(entity, m)) {
            found.push(Saved { group, members: listed.iter().map(Uri::to_string).collect(), starts });
        }
    }
    Ok(found)
}

/// Write a Multi's member list and starts, as `_set_members`: only those
/// keys of its own properties change; a Multi that never had a shared
/// timeline is not given an empty one.
fn set_members(config: &Config, group: &Uri, members: &[String], starts: &[(String, i64)]) -> Result<(), String> {
    let mut own = config.get_own_properties(group).map_err(|e| e.to_string())?.unwrap_or_default();
    own.insert("members".into(), Value::Array(members.iter().cloned().map(Value::String).collect()));
    if !starts.is_empty() || own.contains_key("timeline_starts") {
        own.insert("timeline_starts".into(), Value::Object(starts.iter().map(|(m, f)| (m.clone(), Value::from(*f))).collect()));
    }
    config.set_own_properties(group, own).map_err(|e| e.to_string())
}

/// Take `entity` (and anything under it) out of every Multi and off its
/// shared timeline; what each changed Multi held before.
pub fn drop_member_everywhere(config: &Config, entity: &Uri) -> Result<Vec<Saved>, String> {
    let before = members_of(config, entity)?;
    for saved in &before {
        let members: Vec<String> = saved.members.iter().filter(|m| !is_or_under(entity, m)).cloned().collect();
        let starts: Vec<(String, i64)> = saved.starts.iter().filter(|(m, _)| !is_or_under(entity, m)).cloned().collect();
        set_members(config, &saved.group, &members, &starts)?;
    }
    Ok(before)
}

/// A stored member that stands for the shots (or assets) under it: a
/// sequence or category that exists (`is_parent_member`).
fn is_parent_member(config: &Config, uri: &Uri) -> Result<bool, String> {
    if uri.purpose != "entity" || uri.segments.len() < 2 {
        return Ok(false);
    }
    if config.is_terminal_entity(uri).map_err(|e| e.to_string())? {
        return Ok(false);
    }
    Ok(config.get_properties(uri).map_err(|e| e.to_string())?.is_some())
}

/// The shots (or assets) a parent member stands for, in config order.
fn children_of(config: &Config, uri: &Uri) -> Result<Vec<Uri>, String> {
    let mut out = Vec::new();
    for child in config.list_entity_uris(uri, true).map_err(|e| e.to_string())? {
        if child != *uri && config.is_terminal_entity(&child).map_err(|e| e.to_string())? {
            out.push(child);
        }
    }
    Ok(out)
}

/// `stored` with every parent member replaced by its shots, in place, each
/// member once (`resolve_members`).
pub fn resolve_members(config: &Config, stored: &[Uri]) -> Result<Vec<Uri>, String> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for member in stored {
        let expanded = if is_parent_member(config, member)? { children_of(config, member)? } else { vec![member.clone()] };
        for uri in expanded {
            if seen.insert(uri.to_string()) {
                out.push(uri);
            }
        }
    }
    Ok(out)
}

/// `starts` with every unplaced member placed, in member order
/// (`multi_timeline.place`): a placed member keeps its start, a start for a
/// URI no longer a member is dropped; an unplaced member goes on the frame
/// after the last frame any placed member reaches (rolls not counted), or on
/// an empty timeline on its own start frame. A member without a frame range
/// (`ranges` gives `None`, or nothing) is left out.
pub fn place(starts: &[(String, i64)], members: &[Uri], ranges: &HashMap<String, Option<(i64, i64)>>) -> Vec<(String, i64)> {
    let keys: Vec<String> = members.iter().map(Uri::to_string).collect();
    let mut placed: Vec<(String, i64)> = starts.iter().filter(|(k, _)| keys.contains(k)).cloned().collect();
    let range = |key: &str| ranges.get(key).copied().flatten();
    let mut end = placed.iter().filter_map(|(k, start)| range(k).map(|(first, last)| start + last - first)).max();
    for key in keys {
        if placed.iter().any(|(k, _)| *k == key) {
            continue;
        }
        let Some((first, last)) = range(&key) else { continue };
        let start = end.map_or(first, |e| e + 1);
        placed.push((key, start));
        end = Some(end.unwrap_or(start).max(start + last - first));
    }
    placed
}

/// Put every new shot in `uris` on the shared timeline of each Multi it
/// joined by being made in a listed sequence (`place_new_members` for
/// entity URIs): those Multis' unplaced members are placed
/// (`place_members`); a Multi without a shared timeline is left alone.
pub fn place_new_members(config: &Config, uris: &[Uri]) -> Result<(), String> {
    let mut by_context: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();
    for uri in uris {
        if uri.purpose == "entity" && !uri.segments.is_empty() {
            let context = uri.segments[0].clone();
            if !by_context.contains_key(&context) {
                order.push(context.clone());
            }
            by_context.entry(context).or_default().push(uri.to_string());
        }
    }
    let mut touched: Vec<Uri> = Vec::new();
    for context in order {
        let new = &by_context[&context];
        for (group, properties) in list_groups(config, &context)? {
            if !properties.get("shared_timeline").is_some_and(truthy) {
                continue;
            }
            let members = resolve_members(config, &stored_members(&properties)?)?;
            if members.iter().any(|m| new.contains(&m.to_string())) && !touched.contains(&group) {
                touched.push(group);
            }
        }
    }
    for group in touched {
        place_members(config, &group)?;
    }
    Ok(())
}

/// Python's `bool()` of a stored value.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Place the Multi's unplaced members when its timeline is shared
/// (`multi_timeline.place_members`), pinning its timeline keys as
/// `groups.set_timeline` does.
fn place_members(config: &Config, group: &Uri) -> Result<(), String> {
    let Some(properties) = config.get_properties(group).map_err(|e| e.to_string())? else { return Ok(()) };
    let shared = properties.get("shared_timeline").is_some_and(truthy);
    if !shared {
        return Ok(());
    }
    let bookmarks = properties.get("timeline_bookmarks").is_some_and(truthy);
    let members = resolve_members(config, &stored_members(&properties)?)?;
    let starts = timeline_starts(&properties)?;
    let mut ranges = HashMap::new();
    for member in &members {
        let range = crate::timeline::get_frame_range(config, member)?.map(|r| (r.start_frame, r.end_frame));
        ranges.insert(member.to_string(), range);
    }
    let placed = place(&starts, &members, &ranges);
    let as_map = |s: &[(String, i64)]| s.iter().cloned().collect::<BTreeMap<_, _>>();
    if as_map(&placed) == as_map(&starts) {
        return Ok(());
    }
    let mut keys = Map::new();
    keys.insert("shared_timeline".into(), Value::Bool(shared));
    keys.insert("timeline_bookmarks".into(), Value::Bool(bookmarks));
    keys.insert("timeline_starts".into(), Value::Object(placed.into_iter().map(|(m, f)| (m, Value::from(f))).collect()));
    config.set_own_keys(group, keys).map_err(|e| e.to_string())
}

/// Put back what [`drop_member_everywhere`] changed.
pub fn restore_members(config: &Config, saved: &[Saved]) -> Result<(), String> {
    for saved in saved {
        set_members(config, &saved.group, &saved.members, &saved.starts)?;
    }
    Ok(())
}
