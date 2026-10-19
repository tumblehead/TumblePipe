//! `entity://` URI parsing.
//!
//! Accepted shapes:
//!   entity:/assets/SET/Arena?dept=lookdev&channel=default&version=v0013
//!   entity:/scenes/arena?version=v0001
//!   entity:assets/CHAR/Crowd?channel=cheering
//!
//! The second form (no slash after the colon) is tolerated because USD
//! occasionally normalizes identifiers that way. Both parse to the same
//! structure.

use crate::error::{ResolveError, ResolveResult};

pub const DEFAULT_CHANNEL: &str = "default";
/// The query key naming the publish channel, as every writer emits it.
pub const CHANNEL_KEY: &str = "channel";
/// What [`CHANNEL_KEY`] was spelled in everything published before the
/// variant→channel rename. Read by [`channel_from_query`] and nowhere else.
pub const LEGACY_CHANNEL_KEY: &str = "variant";
pub const SHARED_CHANNEL: &str = "_shared";
pub const SCHEME: &str = "entity:";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EntityUri {
    pub segments: Vec<String>,
    pub department: Option<String>,
    pub channel: String,
    /// `None` means "latest" (caller applies auto-discovery).
    pub version: Option<String>,
}

/// The channel a URI's query names, from its `channel` and legacy `variant`
/// values.
///
/// This is the resolver's one reader of the old spelling, and the reason it
/// exists is published data: every staged `.usda` and every exported layer
/// written before the variant→channel rename sublayers `?variant=` URIs, and
/// published versions are immutable — nothing rewrites them. So the old key
/// must resolve exactly like the new one, forever, while every writer emits
/// only `channel`. Python's counterpart is
/// `tumblepipe.config.channels.read_published_channel`.
///
/// Neither present is the default channel. Both present is accepted only when
/// they agree: disagreeing is unresolvable data, and picking either would be
/// a guess with a wrong render behind it.
pub fn channel_from_query(
    channel: Option<String>,
    legacy: Option<String>,
    raw: &str,
) -> ResolveResult<String> {
    match (channel, legacy) {
        (Some(c), Some(v)) if c != v => Err(ResolveError::Parse(format!(
            "{raw:?} carries both \"{CHANNEL_KEY}\"={c:?} and \"{LEGACY_CHANNEL_KEY}\"={v:?}, \
             which name different channels; they are two spellings of one key and must agree"
        ))),
        (Some(c), _) => Ok(c),
        (None, Some(v)) => Ok(v),
        (None, None) => Ok(DEFAULT_CHANNEL.to_owned()),
    }
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
        let mut channel: Option<String> = None;
        let mut legacy_channel: Option<String> = None;
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
                CHANNEL_KEY => channel = Some(v.to_owned()),
                LEGACY_CHANNEL_KEY => legacy_channel = Some(v.to_owned()),
                "version" => version = Some(v.to_owned()),
                // An unrecognised key is refused, never ignored. Silently
                // dropping one and falling through to the default channel is
                // how a typo (`?chanel=bg`) resolves the *default* channel:
                // the wrong layers, a plausible render, no diagnostic.
                _ => {
                    return Err(ResolveError::Parse(format!(
                        "unknown query key {k:?} in {raw:?}; expected one of \
                         \"dept\", \"{CHANNEL_KEY}\", \"version\" (or the published \
                         \"{LEGACY_CHANNEL_KEY}\")"
                    )))
                }
            }
        }

        let channel = channel_from_query(channel, legacy_channel, raw)?;
        let version = version.filter(|v| !v.is_empty() && v != "latest");

        Ok(EntityUri {
            segments,
            department,
            channel,
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
            "entity:/assets/SET/Arena?dept=lookdev&channel=default&version=v0013",
        )
        .unwrap();
        assert_eq!(u.segments, vec!["assets", "SET", "Arena"]);
        assert_eq!(u.department.as_deref(), Some("lookdev"));
        assert_eq!(u.channel, "default");
        assert_eq!(u.version.as_deref(), Some("v0013"));
        assert!(!u.is_scene());
    }

    #[test]
    fn parses_staged_uri() {
        let u = EntityUri::parse("entity:/assets/CHAR/Crowd?channel=cheering").unwrap();
        assert_eq!(u.segments, vec!["assets", "CHAR", "Crowd"]);
        assert!(u.department.is_none());
        assert_eq!(u.channel, "cheering");
        assert!(u.version.is_none());
    }

    #[test]
    fn defaults_channel_to_default() {
        let u = EntityUri::parse("entity:/assets/PROP/Box").unwrap();
        assert_eq!(u.channel, DEFAULT_CHANNEL);
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
    fn a_published_variant_uri_parses_like_its_channel_spelling() {
        // Every staged file published before the rename says `?variant=`;
        // it must resolve the SAME channel, not the default.
        let legacy = EntityUri::parse("entity:/assets/CHAR/Crowd?variant=cheering").unwrap();
        let current = EntityUri::parse("entity:/assets/CHAR/Crowd?channel=cheering").unwrap();
        assert_eq!(legacy.channel, "cheering");
        assert_eq!(legacy, current, "the two spellings must parse identically");
    }

    #[test]
    fn accepts_both_spellings_together_when_they_agree() {
        let u = EntityUri::parse(
            "entity:/assets/CHAR/Crowd?dept=lookdev&variant=cheering&channel=cheering",
        )
        .unwrap();
        assert_eq!(u.channel, "cheering");
        assert_eq!(u.department.as_deref(), Some("lookdev"));
    }

    #[test]
    fn rejects_the_two_spellings_disagreeing() {
        let err = EntityUri::parse("entity:/assets/CHAR/Crowd?variant=bg&channel=fg")
            .expect_err("disagreeing spellings must not parse");
        let message = format!("{err}");
        assert!(message.contains("bg") && message.contains("fg"), "{message}");
    }

    #[test]
    fn rejects_any_unknown_query_key() {
        let err = EntityUri::parse("entity:/assets/CHAR/Crowd?chanel=cheering")
            .expect_err("a misspelled key must not be ignored");
        assert!(format!("{err}").contains("chanel"));
    }

    #[test]
    fn still_accepts_every_key_that_was_ever_written() {
        // Writers emit dept/channel/version; everything published before the
        // rename says variant. All of it must keep parsing.
        for raw in [
            "entity:/assets/SET/Arena?dept=lookdev&channel=default&version=v0013",
            "entity:/assets/SET/Arena?dept=lookdev",
            "entity:/assets/SET/Arena?channel=_shared",
            "entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013",
            "entity:/assets/SET/Arena?variant=_shared",
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
