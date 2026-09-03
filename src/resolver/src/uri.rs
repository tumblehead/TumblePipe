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
/// The two accepted spellings of the channel query key.
///
/// `variant` is the original and is what every published file carries today.
/// `channel` is what the pipeline is moving to. BOTH are read, deliberately
/// and for a long time: the data lives on the shared project drive while every
/// reader ships per-machine in the hpm package, so the two update on their own
/// schedules and must be able to pass each other. A resolver that read only
/// one spelling would make the changeover a synchronised, all-at-once event;
/// reading both makes it a gradual one, and makes a half-finished migration a
/// non-event rather than a broken project.
///
/// Writers still emit `variant`. Flipping them is a separate, later release —
/// every reader has to accept `channel` *before* anything starts producing it.
pub const VARIANT_KEY: &str = "variant";
pub const CHANNEL_KEY: &str = "channel";
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
        // Tracked apart so the two spellings can be compared before collapsing.
        let mut variant: Option<String> = None;
        let mut channel: Option<String> = None;
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
                VARIANT_KEY => variant = Some(v.to_owned()),
                CHANNEL_KEY => channel = Some(v.to_owned()),
                "version" => version = Some(v.to_owned()),
                // An unrecognised key is refused, never ignored. Silently
                // dropping one and falling through to DEFAULT_VARIANT below is
                // how a typo (`?varaint=bg`) resolves the *default* channel:
                // the wrong layers, a plausible render, no diagnostic. Only
                // dept/variant/channel/version are ever written, so nothing in
                // existing data reaches this arm.
                _ => {
                    return Err(ResolveError::Parse(format!(
                        "unknown query key {k:?} in {raw:?}; expected one of \
                         \"dept\", \"variant\", \"channel\", \"version\""
                    )))
                }
            }
        }

        // Both spellings mean the same thing, so carrying both is fine while
        // writers cross over — but only if they agree. Disagreeing is
        // unresolvable data, and picking one would be a guess with a wrong
        // render on the other side of it.
        let variant = match (variant, channel) {
            (Some(v), Some(c)) if v != c => {
                return Err(ResolveError::Parse(format!(
                    "{raw:?} carries both \"variant\"={v:?} and \"channel\"={c:?}, \
                     which name different channels; they are two spellings of one \
                     key and must agree"
                )))
            }
            (Some(v), _) => v,
            (None, Some(c)) => c,
            (None, None) => DEFAULT_VARIANT.to_owned(),
        };
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
    fn reads_the_channel_key_as_the_variant_key() {
        // The point of accepting both: a URI written with the new spelling
        // resolves the SAME channel, not the default. Without this a
        // `?channel=` URI silently rendered the default channel's layers.
        let u = EntityUri::parse("entity:/assets/CHAR/Crowd?channel=cheering").unwrap();
        assert_eq!(u.variant, "cheering");
        let v = EntityUri::parse("entity:/assets/CHAR/Crowd?variant=cheering").unwrap();
        assert_eq!(u, v, "the two spellings must parse identically");
    }

    #[test]
    fn accepts_both_spellings_together_when_they_agree() {
        // Writers may emit both for one release so that packages on either
        // side of the flip read the same answer.
        let u = EntityUri::parse(
            "entity:/assets/CHAR/Crowd?dept=lookdev&variant=cheering&channel=cheering",
        )
        .unwrap();
        assert_eq!(u.variant, "cheering");
        assert_eq!(u.department.as_deref(), Some("lookdev"));
    }

    #[test]
    fn rejects_the_two_spellings_disagreeing() {
        // Unresolvable: picking either one is a guess with a wrong render
        // behind it.
        let err = EntityUri::parse("entity:/assets/CHAR/Crowd?variant=bg&channel=fg")
            .expect_err("disagreeing spellings must not parse");
        let message = format!("{err}");
        assert!(message.contains("bg") && message.contains("fg"), "{message}");
    }

    #[test]
    fn rejects_any_unknown_query_key() {
        let err = EntityUri::parse("entity:/assets/CHAR/Crowd?varaint=cheering")
            .expect_err("a misspelled key must not be ignored");
        assert!(format!("{err}").contains("varaint"));
    }

    #[test]
    fn still_accepts_every_key_that_is_actually_written() {
        // dept, variant and version are the only three any writer emits, so
        // the guard above must leave all existing data parsing.
        for raw in [
            "entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013",
            "entity:/assets/SET/Arena?dept=lookdev",
            "entity:/assets/SET/Arena?variant=_shared",
            "entity:/assets/SET/Arena?dept=lookdev&channel=default&version=v0013",
            "entity:/assets/SET/Arena?channel=_shared",
            "entity:/scenes/arena?version=v0001",
            "entity:/assets/SET/Arena",
        ] {
            assert!(EntityUri::parse(raw).is_ok(), "{raw} must still parse");
        }
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
