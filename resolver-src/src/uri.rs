//! `entity://` URI parsing.
//!
//! Accepted shapes:
//!   entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013
//!   entity:/scenes/arena?version=v0001
//!   entity:assets/CHAR/Crowd?variant=cheering
//!
//! The second form (no slash after the colon) is tolerated because USD
//! occasionally normalizes identifiers that way. Both parse to the same
//! structure.

use crate::error::{ResolveError, ResolveResult};

pub const DEFAULT_VARIANT: &str = "default";
pub const SHARED_VARIANT: &str = "_shared";
pub const SCHEME: &str = "entity:";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EntityUri {
    pub segments: Vec<String>,
    pub department: Option<String>,
    pub variant: String,
    /// `None` means "latest" (caller applies auto-discovery).
    pub version: Option<String>,
}

impl EntityUri {
    pub fn parse(raw: &str) -> ResolveResult<Self> {
        let body = strip_scheme(raw)
            .ok_or_else(|| ResolveError::Parse(format!("not an entity URI: {raw:?}")))?;

        let (path_part, query_part) = match body.split_once('?') {
            Some((p, q)) => (p, q),
            None => (body, ""),
        };

        let segments: Vec<String> = path_part
            .split('/')
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect();

        if segments.len() < 2 {
            return Err(ResolveError::Parse(format!(
                "entity URI needs at least 2 path segments: {raw:?}"
            )));
        }

        let mut department: Option<String> = None;
        let mut variant: Option<String> = None;
        let mut version: Option<String> = None;

        for pair in query_part.split('&') {
            if pair.is_empty() {
                continue;
            }
            let (k, v) = match pair.split_once('=') {
                Some(kv) => kv,
                None => continue,
            };
            match k {
                "dept" => department = Some(v.to_owned()),
                "variant" => variant = Some(v.to_owned()),
                "version" => version = Some(v.to_owned()),
                _ => {}
            }
        }

        let variant = variant.unwrap_or_else(|| DEFAULT_VARIANT.to_owned());
        let version = version.filter(|v| !v.is_empty() && v != "latest");

        Ok(EntityUri {
            segments,
            department,
            variant,
            version,
        })
    }

    pub fn is_scene(&self) -> bool {
        self.segments
            .first()
            .map(|s| s == "scenes")
            .unwrap_or(false)
    }
}

fn strip_scheme(raw: &str) -> Option<&str> {
    let rest = raw.strip_prefix(SCHEME)?;
    // Allow both "entity:/foo" and "entity:foo".
    Some(rest.strip_prefix('/').unwrap_or(rest))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_department_uri() {
        let u = EntityUri::parse(
            "entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013",
        )
        .unwrap();
        assert_eq!(u.segments, vec!["assets", "SET", "Arena"]);
        assert_eq!(u.department.as_deref(), Some("lookdev"));
        assert_eq!(u.variant, "default");
        assert_eq!(u.version.as_deref(), Some("v0013"));
        assert!(!u.is_scene());
    }

    #[test]
    fn parses_staged_uri() {
        let u = EntityUri::parse("entity:/assets/CHAR/Crowd?variant=cheering").unwrap();
        assert_eq!(u.segments, vec!["assets", "CHAR", "Crowd"]);
        assert!(u.department.is_none());
        assert_eq!(u.variant, "cheering");
        assert!(u.version.is_none());
    }

    #[test]
    fn defaults_variant_to_default() {
        let u = EntityUri::parse("entity:/assets/PROP/Box").unwrap();
        assert_eq!(u.variant, DEFAULT_VARIANT);
    }

    #[test]
    fn version_latest_literal_becomes_none() {
        let u = EntityUri::parse("entity:/scenes/arena?version=latest").unwrap();
        assert!(u.version.is_none());
    }

    #[test]
    fn accepts_no_slash_after_colon() {
        let u = EntityUri::parse("entity:assets/SET/Arena?dept=lookdev").unwrap();
        assert_eq!(u.segments, vec!["assets", "SET", "Arena"]);
    }

    #[test]
    fn rejects_non_entity_scheme() {
        assert!(EntityUri::parse("file:///tmp/x").is_err());
    }

    #[test]
    fn rejects_short_path() {
        assert!(EntityUri::parse("entity:/assets").is_err());
    }

    #[test]
    fn scene_routing_flag() {
        let u = EntityUri::parse("entity:/scenes/arena?version=v0001").unwrap();
        assert!(u.is_scene());
    }
}
