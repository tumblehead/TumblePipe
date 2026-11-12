//! Multis' member lists, as far as creating and deleting an entity touch
//! them (`tumblepipe.config.groups`, `tumblepipe.config.multi_timeline`).
//!
//! A Multi lists its members by URI, so an entity that goes must be taken
//! out of every Multi's list and off its shared timeline: left there, the
//! Multi's template lays out a graph for an entity that is gone. A shot made
//! in a sequence a Multi lists is its member at once, and goes on its shared
//! timeline ([`place_new_members`]).
//!
//! The edits a Multi's own card makes (`add_group`, `add_member`,
//! `remove_member`, `add_department`, `remove_department`) are here too,
//! with the refusals Python makes: a member already covered for one of the
//! Multi's departments by another Multi ([`overlap`]), a shot that comes
//! from a listed sequence ([`inherited_from`]). An entity renamed is renamed
//! in every Multi's list ([`rename_member_everywhere`]).

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

/// Put every new member on its Multis' shared timelines
/// (`place_new_members`): every Multi in `uris` (`groups:/<context>/<name>`,
/// whose member list changed) and every Multi a new shot in `uris` joined
/// by being made in a listed sequence has its unplaced members placed
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
    let mut touched: Vec<Uri> = uris.iter().filter(|uri| is_group_uri(uri)).cloned().collect();
    for context in order {
        let new = &by_context[&context];
        for (group, properties) in list_groups(config, &context)? {
            if !properties.get("shared_timeline").is_some_and(truthy) {
                continue;
            }
            let members = resolve_members(config, &stored_members(&properties)?)?;
            if members.iter().any(|m| new.contains(&m.to_string())) {
                touched.push(group);
            }
        }
    }
    let mut seen = Vec::new();
    for group in touched {
        if !seen.contains(&group) {
            place_members(config, &group)?;
            seen.push(group);
        }
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
pub fn place_members(config: &Config, group: &Uri) -> Result<(), String> {
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

/// A Multi's URI: `groups:/<context>/<name>` (`is_group_uri`).
pub fn is_group_uri(uri: &Uri) -> bool {
    uri.purpose == "groups" && uri.segments.len() == 2
}

/// A Multi as its card reads it (`groups._group`): its stored member list,
/// that list with its sequences resolved to their shots, its departments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Group {
    pub uri: Uri,
    pub members: Vec<Uri>,
    pub stored_members: Vec<Uri>,
    pub departments: Vec<String>,
}

fn names(properties: &Properties, key: &str) -> Vec<String> {
    match properties.get(key) {
        Some(Value::Array(values)) => values
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn group_of(config: &Config, uri: &Uri, properties: &Properties) -> Result<Group, String> {
    let stored = stored_members(properties)?;
    Ok(Group {
        uri: uri.clone(),
        members: resolve_members(config, &stored)?,
        stored_members: stored,
        departments: names(properties, "departments"),
    })
}

/// The Multi at `uri` (`get_group`); None when it does not exist.
pub fn get_group(config: &Config, uri: &Uri) -> Result<Option<Group>, String> {
    let Some(properties) = config.get_properties(uri).map_err(|e| e.to_string())? else { return Ok(None) };
    group_of(config, uri, &properties).map(Some)
}

/// Every Multi of `context` (`list_groups`).
pub fn groups_of(config: &Config, context: &str) -> Result<Vec<Group>, String> {
    list_groups(config, context)?.iter().map(|(uri, properties)| group_of(config, uri, properties)).collect()
}

/// The stored parent member (a sequence) `member` comes from in `group`,
/// or None when it is listed itself, or not a member (`inherited_from`).
pub fn inherited_from(config: &Config, group: &Group, member: &Uri) -> Result<Option<Uri>, String> {
    if group.stored_members.contains(member) || !group.members.contains(member) {
        return Ok(None);
    }
    for parent in &group.stored_members {
        if parent.contains(member) && is_parent_member(config, parent)? {
            return Ok(Some(parent.clone()));
        }
    }
    Ok(None)
}

/// Why `members` cannot be in a Multi of `context` covering `departments`
/// (`overlap`): one of them is in another Multi that covers one of those
/// departments too, so which Multi's workfile it opens would be a guess.
/// Empty when nothing clashes. `group` is the Multi itself, left out. A
/// sequence in `members` is checked shot by shot.
pub fn overlap(config: &Config, context: &str, members: &[Uri], departments: &[String], group: Option<&Uri>) -> Result<String, String> {
    let members = resolve_members(config, members)?;
    for other in groups_of(config, context)? {
        if Some(&other.uri) == group {
            continue;
        }
        let shared: Vec<&str> = departments.iter().filter(|d| other.departments.contains(d)).map(String::as_str).collect();
        if shared.is_empty() {
            continue;
        }
        if let Some(member) = members.iter().find(|m| other.members.contains(m)) {
            let verb = if shared.len() == 1 { "is" } else { "are" };
            return Ok(format!(
                "{}'s {} {verb} already covered by Multi {}",
                member.last().unwrap_or_default(),
                shared.join(", "),
                other.uri.last().unwrap_or_default()
            ));
        }
    }
    Ok(String::new())
}

/// Create the Multi `groups:/<context>/<name>` (`add_group`). Refuses one
/// that exists, a member not under `entity:/<context>`, and a member
/// another Multi covers for one of `departments` ([`overlap`]).
pub fn add_group(config: &Config, context: &str, name: &str, members: &[Uri], departments: &[String]) -> Result<Uri, String> {
    let group = Uri::new("groups", [context, name]);
    if config.get_properties(&group).map_err(|e| e.to_string())?.is_some() {
        return Err("Group already exists".into());
    }
    let root = Uri::new("entity", [context]);
    if members.iter().any(|m| !root.contains(m)) {
        return Err("Invalid group member list".into());
    }
    let clash = overlap(config, context, members, departments, None)?;
    if !clash.is_empty() {
        return Err(clash);
    }
    let mut properties = Properties::new();
    properties.insert("members".into(), Value::Array(members.iter().map(|m| Value::String(m.to_string())).collect()));
    properties.insert("departments".into(), Value::Array(departments.iter().cloned().map(Value::String).collect()));
    config.add_entity(&group, properties).map_err(|e| e.to_string())?;
    Ok(group)
}

/// `group`'s resolved properties, refused when it does not exist.
fn group_properties(config: &Config, group: &Uri) -> Result<Properties, String> {
    config.get_properties(group).map_err(|e| e.to_string())?.ok_or_else(|| "Group does not exist".to_string())
}

fn list_of(properties: &Properties, key: &str) -> Vec<Value> {
    match properties.get(key) {
        Some(Value::Array(values)) => values.clone(),
        _ => Vec::new(),
    }
}

fn pin(config: &Config, group: &Uri, key: &str, value: Value) -> Result<(), String> {
    let mut keys = Properties::new();
    keys.insert(key.into(), value);
    config.set_own_keys(group, keys).map_err(|e| e.to_string())
}

fn check_member(group: &Uri, member: &Uri) -> Result<(), String> {
    let context = group.segments.first().cloned().unwrap_or_default();
    if !Uri::new("entity", [context]).contains(member) {
        return Err("Invalid group member".into());
    }
    Ok(())
}

/// List `member` on the Multi `group` (`add_member`), and place it on the
/// shared timeline. Refuses a member listed already, a shot that comes from
/// a listed sequence, and one another Multi covers for one of the Multi's
/// departments.
pub fn add_member(config: &Config, group: &Uri, member: &Uri) -> Result<(), String> {
    check_member(group, member)?;
    let properties = group_properties(config, group)?;
    let mut members = list_of(&properties, "members");
    let raw = Value::String(member.to_string());
    if members.contains(&raw) {
        return Err("Already a member of group".into());
    }
    if let Some(parent) = inherited_from(config, &group_of(config, group, &properties)?, member)? {
        return Err(format!(
            "{} is already a member: it comes from {}",
            member.last().unwrap_or_default(),
            parent.last().unwrap_or_default()
        ));
    }
    let clash = overlap(config, &group.segments[0], std::slice::from_ref(member), &names(&properties, "departments"), Some(group))?;
    if !clash.is_empty() {
        return Err(clash);
    }
    members.push(raw);
    pin(config, group, "members", Value::Array(members))?;
    place_members(config, group)
}

/// Take `member` off the Multi `group`'s list and its shared timeline
/// (`remove_member`); every shot it stood for leaves the timeline, the
/// others keep their frames. Refuses a shot that comes from a listed
/// sequence: the sequence is what to remove.
pub fn remove_member(config: &Config, group: &Uri, member: &Uri) -> Result<(), String> {
    check_member(group, member)?;
    let properties = group_properties(config, group)?;
    let mut members = list_of(&properties, "members");
    let raw = member.to_string();
    let Some(index) = members.iter().position(|m| m.as_str() == Some(raw.as_str())) else {
        if let Some(parent) = inherited_from(config, &group_of(config, group, &properties)?, member)? {
            let parent = parent.last().unwrap_or_default().to_string();
            return Err(format!("{} comes from {parent}; remove {parent} from the Multi instead", member.last().unwrap_or_default()));
        }
        return Err("Not a member of group".into());
    };
    let parse = |values: &[Value]| -> Result<Vec<Uri>, String> {
        values
            .iter()
            .map(|m| m.as_str().ok_or_else(|| format!("member {m} is not a URI")).and_then(|s| Uri::parse(s).map_err(|e| e.to_string())))
            .collect()
    };
    let before = resolve_members(config, &parse(&members)?)?;
    members.remove(index);
    let after: Vec<String> = resolve_members(config, &parse(&members)?)?.iter().map(Uri::to_string).collect();
    let mut starts = match properties.get("timeline_starts") {
        Some(Value::Object(starts)) => starts.clone(),
        _ => Map::new(),
    };
    let mut leaving: Vec<String> = before.iter().map(Uri::to_string).filter(|m| !after.contains(m)).collect();
    leaving.push(raw);
    let mut popped = false;
    for key in leaving {
        // Python's `dict.pop(key, None)`: a stored `null` pops as None.
        popped |= starts.shift_remove(&key).is_some_and(|frame| !frame.is_null());
    }
    let mut keys = Properties::new();
    keys.insert("members".into(), Value::Array(members));
    if popped {
        keys.insert("timeline_starts".into(), Value::Object(starts));
    }
    config.set_own_keys(group, keys).map_err(|e| e.to_string())
}

/// Have the Multi `group` cover `department` too (`add_department`).
/// Refuses one it covers, and one another Multi covers for a member.
pub fn add_department(config: &Config, group: &Uri, department: &str) -> Result<(), String> {
    let properties = group_properties(config, group)?;
    let mut departments = list_of(&properties, "departments");
    if departments.iter().any(|d| d.as_str() == Some(department)) {
        return Err("Already a department of group".into());
    }
    let clash = overlap(config, &group.segments[0], &stored_members(&properties)?, &[department.to_string()], Some(group))?;
    if !clash.is_empty() {
        return Err(clash);
    }
    departments.push(Value::String(department.into()));
    pin(config, group, "departments", Value::Array(departments))
}

/// Stop the Multi `group` covering `department` (`remove_department`).
pub fn remove_department(config: &Config, group: &Uri, department: &str) -> Result<(), String> {
    let properties = group_properties(config, group)?;
    let mut departments = list_of(&properties, "departments");
    let Some(index) = departments.iter().position(|d| d.as_str() == Some(department)) else {
        return Err("Not a department of group".into());
    };
    departments.remove(index);
    pin(config, group, "departments", Value::Array(departments))
}

/// `raw` with `entity` (or what is under it) moved to `new` (`_moved`).
fn moved(entity: &Uri, new: &Uri, raw: &str) -> String {
    match Uri::parse(raw) {
        Ok(member) if entity.is_or_contains(&member) => {
            let mut segments = new.segments.clone();
            segments.extend_from_slice(&member.segments[entity.segments.len()..]);
            Uri::new(&member.purpose, segments).to_string()
        }
        _ => raw.to_string(),
    }
}

/// Rename `entity` (and anything under it) to `new` in every Multi's member
/// list and on its shared timeline, each keeping its place
/// (`rename_member_everywhere`); what each changed Multi held before.
pub fn rename_member_everywhere(config: &Config, entity: &Uri, new: &Uri) -> Result<Vec<Saved>, String> {
    let before = members_of(config, entity)?;
    for saved in &before {
        let members: Vec<String> = saved.members.iter().map(|m| moved(entity, new, m)).collect();
        let starts: Vec<(String, i64)> = saved.starts.iter().map(|(m, f)| (moved(entity, new, m), *f)).collect();
        set_members(config, &saved.group, &members, &starts)?;
    }
    Ok(before)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    const SEQUENCES: [&str; 2] = ["S1", "S2"];
    const SHOTS: [&str; 2] = ["sh010", "sh020"];
    const DEPARTMENTS: [&str; 3] = ["animation", "layout", "cfx"];

    fn shot(sequence: &str, name: &str) -> String {
        format!("entity:/shots/{sequence}/{name}")
    }

    /// Every member a Multi can list: the shots, then their sequences.
    fn pool() -> Vec<String> {
        let mut pool: Vec<String> = SEQUENCES.iter().flat_map(|s| SHOTS.iter().map(move |n| shot(s, n))).collect();
        pool.extend(SEQUENCES.iter().map(|s| format!("entity:/shots/{s}")));
        pool
    }

    /// The model's resolution: a sequence stands for its shots.
    fn resolve(member: &str) -> Vec<String> {
        match SEQUENCES.iter().find(|s| member == format!("entity:/shots/{s}")) {
            Some(s) => SHOTS.iter().map(|n| shot(s, n)).collect(),
            None => vec![member.to_string()],
        }
    }

    #[derive(Debug, Clone)]
    struct Multi {
        members: Vec<String>,
        departments: Vec<String>,
        starts: bool,
    }

    fn multis() -> impl Strategy<Value = Vec<Multi>> {
        let multi = (
            proptest::sample::subsequence(pool(), 0..=4).prop_shuffle(),
            proptest::sample::subsequence(DEPARTMENTS.map(String::from).to_vec(), 0..=3),
            any::<bool>(),
        )
            .prop_map(|(members, departments, starts)| Multi { members, departments, starts });
        proptest::collection::vec(multi, 1..4)
    }

    /// A project with the shots and `multis` as `groups:/shots/m<i>`.
    fn project(multis: &[Multi]) -> (tempfile::TempDir, Config) {
        let (dir, root) = crate::testing::make_project();
        let schemas = json!({"properties": {}, "children": {"entity": {"properties": {"fps": 24}, "children": {"shots": {"properties": {}, "children": {"sequence": {"properties": {}, "children": {"shot": {"properties": {}, "children": {}}}}}}}}}});
        std::fs::write(root.join("_config").join("db").join("schemas.json"), schemas.to_string()).unwrap();
        let config = Config::new(&root.join("_config"));
        for sequence in SEQUENCES {
            config.add_entity(&Uri::new("entity", ["shots", sequence]), Properties::new()).unwrap();
            for name in SHOTS {
                let props = json!({"frame_start": 1001, "frame_end": 1010, "roll_start": 0, "roll_end": 0});
                config.add_entity(&Uri::new("entity", ["shots", sequence, name]), props.as_object().unwrap().clone()).unwrap();
            }
        }
        for (index, multi) in multis.iter().enumerate() {
            let mut props = json!({"members": multi.members, "departments": multi.departments});
            if multi.starts {
                let starts: Map<String, Value> =
                    multi.members.iter().flat_map(|m| resolve(m)).enumerate().map(|(i, m)| (m, json!(1001 + 10 * i))).collect();
                props["timeline_starts"] = Value::Object(starts);
            }
            config
                .set_own_properties(&Uri::new("groups", ["shots".to_string(), format!("m{index}")]), props.as_object().unwrap().clone())
                .unwrap();
        }
        (dir, config)
    }

    fn stored(config: &Config, group: &Uri) -> Vec<String> {
        let own = config.get_own_properties(group).unwrap().unwrap();
        own["members"].as_array().unwrap().iter().map(|m| m.as_str().unwrap().to_string()).collect()
    }

    proptest! {
        /// `add_member` decides as Python's: a member listed already, then a
        /// shot of a listed sequence, then a member another Multi covers for
        /// one of this Multi's departments (`overlap`) is refused, each with
        /// its message; anything else is appended to the stored list.
        #[test]
        fn add_member_refuses_as_python(multis in multis(), target in 0usize..3, member in proptest::sample::select(pool())) {
            let target = target % multis.len();
            let (_dir, config) = project(&multis);
            let group = Uri::new("groups", ["shots".to_string(), format!("m{target}")]);
            let before = stored(&config, &group);
            let mine = &multis[target];
            let resolved: Vec<String> = resolve(&member);
            let parent = mine.members.iter().find(|m| *m != &member && resolve(m).contains(&member) && resolve(m).len() > 1);
            let clash = multis.iter().enumerate().find(|(i, other)| {
                *i != target
                    && mine.departments.iter().any(|d| other.departments.contains(d))
                    && resolved.iter().any(|r| other.members.iter().any(|m| resolve(m).contains(r)))
            });
            let result = add_member(&config, &group, &Uri::parse(&member).unwrap());
            if mine.members.contains(&member) {
                prop_assert_eq!(result, Err("Already a member of group".to_string()));
            } else if let Some(parent) = parent {
                prop_assert_eq!(result, Err(format!("{} is already a member: it comes from {}", member.rsplit('/').next().unwrap(), parent.rsplit('/').next().unwrap())));
            } else if let Some((index, _)) = clash {
                let error = result.unwrap_err();
                prop_assert!(error.contains("already covered by Multi m"), "{}", error);
                prop_assert!(error.ends_with(&format!("Multi m{index}")), "{}", error);
                prop_assert_eq!(stored(&config, &group), before);
            } else {
                prop_assert_eq!(result, Ok(()));
                let mut expected = before;
                expected.push(member);
                prop_assert_eq!(stored(&config, &group), expected);
            }
        }

        /// A member removed leaves the shared timeline with every shot it
        /// stood for that no other listed member still covers; the others
        /// keep their frames.
        #[test]
        fn remove_member_takes_it_off_the_timeline(multis in multis(), pick in any::<proptest::sample::Index>()) {
            let mut multis = multis;
            multis[0].starts = true;
            prop_assume!(!multis[0].members.is_empty());
            let (_dir, config) = project(&multis);
            let group = Uri::new("groups", ["shots", "m0"]);
            let member = pick.get(&multis[0].members).clone();
            let starts_before = config.get_own_properties(&group).unwrap().unwrap()["timeline_starts"].as_object().unwrap().clone();
            remove_member(&config, &group, &Uri::parse(&member).unwrap()).unwrap();
            let rest: Vec<&String> = multis[0].members.iter().filter(|m| **m != member).collect();
            let still: Vec<String> = rest.iter().flat_map(|m| resolve(m)).collect();
            let expected: Map<String, Value> = starts_before.into_iter().filter(|(k, _)| *k != member && (still.contains(k) || !resolve(&member).contains(k))).collect();
            let own = config.get_own_properties(&group).unwrap().unwrap();
            prop_assert_eq!(own["timeline_starts"].as_object().unwrap(), &expected);
            prop_assert_eq!(stored(&config, &group), rest.into_iter().cloned().collect::<Vec<_>>());
        }

        /// A sequence renamed is renamed in every Multi's list and timeline,
        /// in place; nothing else changes.
        #[test]
        fn rename_keeps_every_multi_in_place(multis in multis(), which in 0usize..2) {
            let (_dir, config) = project(&multis);
            let old = Uri::new("entity", ["shots", SEQUENCES[which]]);
            let new = Uri::new("entity", ["shots", "S9"]);
            let prefix = old.to_string();
            let rename = |raw: &str| match raw.strip_prefix(&prefix) {
                Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{new}{rest}"),
                _ => raw.to_string(),
            };
            config.rename_entity(&old, "S9").unwrap();
            rename_member_everywhere(&config, &old, &new).unwrap();
            for (index, multi) in multis.iter().enumerate() {
                let group = Uri::new("groups", ["shots".to_string(), format!("m{index}")]);
                let expected: Vec<String> = multi.members.iter().map(|m| rename(m)).collect();
                prop_assert_eq!(stored(&config, &group), expected);
                if multi.starts {
                    let own = config.get_own_properties(&group).unwrap().unwrap();
                    let keys: Vec<String> = own["timeline_starts"].as_object().unwrap().keys().cloned().collect();
                    let expected: Vec<String> = multi.members.iter().flat_map(|m| resolve(m)).map(|m| rename(&m)).collect();
                    let mut unique = Vec::new();
                    for key in expected { if !unique.contains(&key) { unique.push(key); } }
                    prop_assert_eq!(keys, unique);
                }
            }
        }

        /// A department added to a Multi is refused exactly when another
        /// Multi covering it lists one of this Multi's members.
        #[test]
        fn add_department_refuses_an_overlap(multis in multis(), department in proptest::sample::select(DEPARTMENTS.to_vec())) {
            let (_dir, config) = project(&multis);
            let group = Uri::new("groups", ["shots", "m0"]);
            let mine: Vec<String> = multis[0].members.iter().flat_map(|m| resolve(m)).collect();
            let result = add_department(&config, &group, department);
            if multis[0].departments.iter().any(|d| d == department) {
                prop_assert_eq!(result, Err("Already a department of group".to_string()));
            } else if multis[1..].iter().any(|o| o.departments.iter().any(|d| d == department) && o.members.iter().flat_map(|m| resolve(m)).any(|m| mine.contains(&m))) {
                let wanted = format!("{department} is already covered by Multi");
                let error = result.unwrap_err();
                prop_assert!(error.contains(&wanted), "{}", error);
            } else {
                prop_assert_eq!(result, Ok(()));
            }
        }
    }

    #[test]
    fn a_new_multi_is_refused_over_another() {
        let multis =
            [Multi { members: vec!["entity:/shots/S1".into()], departments: vec!["animation".into(), "layout".into()], starts: false }];
        let (_dir, config) = project(&multis);
        let members = [Uri::parse(&shot("S1", "sh020")).unwrap()];
        let both = ["animation".to_string(), "layout".to_string()];
        assert_eq!(
            add_group(&config, "shots", "x", &members, &both),
            Err("sh020's animation, layout are already covered by Multi m0".into())
        );
        assert_eq!(add_group(&config, "shots", "m0", &[], &[]), Err("Group already exists".into()));
        assert_eq!(
            add_group(&config, "shots", "y", &[Uri::parse("entity:/assets/A/b").unwrap()], &[]),
            Err("Invalid group member list".into())
        );
        add_group(&config, "shots", "z", &members, &["cfx".to_string()]).unwrap();
        assert_eq!(stored(&config, &Uri::new("groups", ["shots", "z"])), vec![shot("S1", "sh020")]);
    }
}
