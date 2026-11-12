//! Departments and channels (`tumblepipe.config.department`,
//! `tumblepipe.config.channels`).

use serde::Serialize;
use serde_json::Value;

use crate::config::{Config, ConfigError, Properties};
use crate::uri::Uri;

/// One department of a context's pool.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Department {
    pub name: String,
    pub independent: bool,
    pub publishable: bool,
    pub renderable: bool,
    pub generated: bool,
    pub enabled: bool,
    pub short: Option<String>,
}

fn flag(properties: &Properties, key: &str, uri: &Uri) -> Result<bool, String> {
    match properties.get(key) {
        Some(value) => Ok(truthy(value)),
        None => Err(format!("{uri} has no '{key}': a broken department config")),
    }
}

/// Python's truth of a JSON value.
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

/// The departments of `context` (`shots`, `assets`, `render`, ...) in pool
/// order, which is pipeline order (`list_departments`). Generated ones are
/// left out unless `include_generated`; disabled ones unless
/// `include_disabled`.
pub fn list_departments(
    config: &Config,
    context: &str,
    include_generated: bool,
    include_disabled: bool,
) -> Result<Vec<Department>, String> {
    let context_uri = Uri::new("departments", [context]);
    let names = config.child_names(&context_uri).map_err(|e: ConfigError| e.to_string())?;
    let mut departments = Vec::new();
    for name in names {
        let uri = context_uri.join(&name);
        let properties = config.get_properties(&uri).map_err(|e| e.to_string())?.unwrap_or_default();
        let department = Department {
            independent: flag(&properties, "independent", &uri)?,
            publishable: flag(&properties, "publishable", &uri)?,
            renderable: flag(&properties, "renderable", &uri)?,
            generated: properties.get("generated").is_some_and(truthy),
            enabled: properties.get("enabled").is_none_or(truthy),
            short: properties.get("short").and_then(Value::as_str).map(str::to_string),
            name,
        };
        if (include_generated || !department.generated) && (include_disabled || department.enabled) {
            departments.push(department);
        }
    }
    Ok(departments)
}

/// The names of [`list_departments`] with its defaults (generated in,
/// disabled out).
pub fn department_names(config: &Config, context: &str) -> Result<Vec<String>, String> {
    Ok(list_departments(config, context, true, false)?.into_iter().map(|d| d.name).collect())
}

/// The channel every entity has, first.
pub const DEFAULT_CHANNEL: &str = "default";

/// The render layer that comps the others.
pub const SLAPCOMP: &str = "slapcomp";

/// An entity's channels (`list_channels`): `default`, then its `channels`
/// property's names.
pub fn list_channels(config: &Config, entity: &Uri) -> Result<Vec<String>, String> {
    let Some(properties) = config.get_properties(entity).map_err(|e| e.to_string())? else {
        return Ok(vec![DEFAULT_CHANNEL.to_string()]);
    };
    let mut channels = vec![DEFAULT_CHANNEL.to_string()];
    if let Some(Value::Array(names)) = properties.get("channels") {
        for name in names {
            let name = match name {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            if name != DEFAULT_CHANNEL {
                channels.push(name);
            }
        }
    }
    Ok(channels)
}

/// Why `name` cannot name a new channel, or empty (`channel_name_problem`):
/// the entity name rule, letters and digits.
pub fn channel_name_problem(name: &str) -> String {
    if name.is_empty() {
        return "No channel name".into();
    }
    if let Some(problem) = crate::naming::entity_name_problem(name) {
        return format!("Channel name '{name}' {problem}");
    }
    String::new()
}

/// The `channels` an entity resolves to, the basis an edit starts from
/// (`read_channel_names`: inherited ones included), refused when it does
/// not exist.
fn resolved_channels(config: &Config, entity: &Uri) -> Result<Vec<Value>, String> {
    let properties = config.get_properties(entity).map_err(|e| e.to_string())?.ok_or_else(|| format!("Entity not found: {entity}"))?;
    Ok(match properties.get("channels") {
        Some(Value::Array(names)) => names.clone(),
        _ => Vec::new(),
    })
}

fn pin_channels(config: &Config, entity: &Uri, channels: Vec<Value>) -> Result<(), String> {
    let mut keys = Properties::new();
    keys.insert("channels".into(), Value::Array(channels));
    config.set_own_keys(entity, keys).map_err(|e| e.to_string())
}

/// Add the channel `name` to `entity` (`channels.add_channel`): the
/// channels it resolves to (inherited ones too) plus `name`, pinned on its
/// own properties. Refuses `default` (always there) and one it has.
pub fn add_channel(config: &Config, entity: &Uri, name: &str) -> Result<(), String> {
    if name == DEFAULT_CHANNEL {
        return Err(format!("Cannot add '{DEFAULT_CHANNEL}' channel - it exists implicitly"));
    }
    let mut channels = resolved_channels(config, entity)?;
    if channels.iter().any(|c| c.as_str() == Some(name)) {
        return Err(format!("Channel already exists: {name}"));
    }
    channels.push(Value::String(name.into()));
    pin_channels(config, entity, channels)
}

/// Remove the channel `name` from `entity` (`channels.remove_channel`): the
/// channels it resolves to (inherited ones too) less `name`, pinned on its
/// own properties. Refuses `default` and one it does not have.
pub fn remove_channel(config: &Config, entity: &Uri, name: &str) -> Result<(), String> {
    if name == DEFAULT_CHANNEL {
        return Err(format!("Cannot remove '{DEFAULT_CHANNEL}' channel"));
    }
    let mut channels = resolved_channels(config, entity)?;
    let Some(index) = channels.iter().position(|c| c.as_str() == Some(name)) else {
        return Err(format!("Channel not found: {name}"));
    };
    channels.remove(index);
    pin_channels(config, entity, channels)
}

/// Names the pipeline uses for pseudo-departments never in the pool
/// (`department.RESERVED_NAMES`): the scene-build root layer, the build
/// task's synthetic department, import_shot's "exclude nothing".
pub const RESERVED_NAMES: [&str; 3] = ["none", "root", "staged"];

/// Refuse `name` as a department name (`department.validate_name`): it is
/// a path segment, and not a reserved one.
pub fn validate_department_name(name: &str) -> Result<(), String> {
    if name.is_empty() || name.trim() != name {
        return Err("Department name must be non-empty and unpadded".into());
    }
    if name.contains('/') || name.contains('\\') {
        return Err("Department name may not contain a path separator".into());
    }
    if RESERVED_NAMES.contains(&name) {
        return Err(format!("'{name}' is reserved for a pipeline pseudo-department ({})", RESERVED_NAMES.join(", ")));
    }
    if crate::naming::is_windows_reserved(name) {
        // A department is a folder in every workspace and export path.
        return Err(format!("'{name}' is a device name Windows reserves (CON, PRN, AUX, NUL, COM0-9, LPT0-9)"));
    }
    Ok(())
}

/// A new department's flags (`department.add_department`'s arguments).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewDepartment {
    pub independent: bool,
    pub publishable: bool,
    pub renderable: bool,
    pub generated: bool,
    pub enabled: bool,
    pub short: Option<String>,
}

impl Default for NewDepartment {
    /// Python's defaults.
    fn default() -> Self {
        NewDepartment { independent: false, publishable: true, renderable: false, generated: false, enabled: true, short: None }
    }
}

/// Add the department `name` to the end of `context`'s pool
/// (`department.add_department` without `index`). Refuses a bad or
/// reserved name and one that exists.
pub fn add_department(config: &Config, context: &str, name: &str, flags: &NewDepartment) -> Result<Uri, String> {
    validate_department_name(name)?;
    let uri = Uri::new("departments", [context, name]);
    if config.get_properties(&uri).map_err(|e| e.to_string())?.is_some() {
        return Err("Department already exists".into());
    }
    let mut properties = Properties::new();
    properties.insert("independent".into(), Value::Bool(flags.independent));
    properties.insert("publishable".into(), Value::Bool(flags.publishable));
    properties.insert("renderable".into(), Value::Bool(flags.renderable));
    properties.insert("generated".into(), Value::Bool(flags.generated));
    properties.insert("enabled".into(), Value::Bool(flags.enabled));
    properties.insert("short".into(), flags.short.clone().map_or(Value::Null, Value::String));
    config.set_own_properties(&uri, properties).map_err(|e| e.to_string())?;
    Ok(uri)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    fn shot() -> Uri {
        Uri::new("entity", ["shots", "S1", "sh010"])
    }

    /// A project whose shot inherits `inherited` channels from its
    /// sequence and pins `own` (when given) itself.
    fn project(inherited: &[String], own: Option<&[String]>) -> (tempfile::TempDir, Config) {
        let (dir, root) = crate::testing::make_project();
        let config = Config::new(&root.join("_config"));
        config.add_entity(&Uri::new("entity", ["shots", "S1"]), json!({"channels": inherited}).as_object().unwrap().clone()).unwrap();
        let props = own.map_or_else(Properties::new, |own| json!({"channels": own}).as_object().unwrap().clone());
        config.add_entity(&shot(), props).unwrap();
        (dir, config)
    }

    fn channel_set() -> impl Strategy<Value = Vec<String>> {
        proptest::sample::subsequence(vec!["bg".to_string(), "fg".into(), "chars".into(), "fx".into()], 0..=4)
    }

    proptest! {
        /// A channel added or removed starts from what the entity resolves
        /// to, its inherited channels included (`channels.add_channel`), so
        /// none of those is dropped, and an inherited one can be removed.
        #[test]
        fn channel_edits_start_from_the_resolved_list(
            inherited in channel_set(),
            own in proptest::option::of(channel_set()),
            name in proptest::sample::select(vec!["bg", "fx", "rim", "default"]),
            add in any::<bool>(),
        ) {
            let (_dir, config) = project(&inherited, own.as_deref());
            let resolved: Vec<String> = own.clone().unwrap_or(inherited.clone());
            let result = if add { add_channel(&config, &shot(), name) } else { remove_channel(&config, &shot(), name) };
            let has = resolved.iter().any(|c| c == name);
            let expected: Option<Vec<String>> = match (name, add, has) {
                ("default", _, _) => None,
                (_, true, false) => Some(resolved.iter().cloned().chain([name.to_string()]).collect()),
                (_, false, true) => Some(resolved.iter().filter(|c| *c != name).cloned().collect()),
                _ => None,
            };
            match expected {
                Some(expected) => {
                    prop_assert_eq!(result, Ok(()));
                    prop_assert_eq!(list_channels(&config, &shot()).unwrap()[1..].to_vec(), expected.clone());
                    let own = config.get_own_properties(&shot()).unwrap().unwrap();
                    prop_assert_eq!(&own["channels"], &json!(expected));
                }
                None => prop_assert!(result.is_err()),
            }
        }

        /// A department is refused for a reserved, padded or path-like name
        /// and one that exists; anything else is appended to the pool with
        /// every flag Python writes.
        #[test]
        fn add_department_refuses_as_python(name in proptest::sample::select(vec!["root", "staged", "none", " x", "a/b", "", "model", "rig", "Root", "aux", "Com1", "LPT9", "console"])) {
            let (_dir, root) = crate::testing::make_project();
            let config = Config::new(&root.join("_config"));
            add_department(&config, "assets", "model", &NewDepartment::default()).unwrap();
            let result = add_department(&config, "assets", name, &NewDepartment::default());
            let refused = ["root", "staged", "none", " x", "a/b", "", "model", "aux", "Com1", "LPT9"].contains(&name);
            prop_assert_eq!(result.is_err(), refused, "{:?}", result);
            if !refused {
                prop_assert_eq!(config.child_names(&Uri::new("departments", ["assets"])).unwrap(), vec!["model".to_string(), name.to_string()]);
                let own = config.get_own_properties(&Uri::new("departments", ["assets", name])).unwrap().unwrap();
                prop_assert_eq!(Value::Object(own), json!({"independent": false, "publishable": true, "renderable": false, "generated": false, "enabled": true, "short": null}));
            }
        }
    }
}
