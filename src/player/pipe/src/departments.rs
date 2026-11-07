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
