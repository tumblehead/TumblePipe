//! Pipeline URIs: `purpose:/seg/seg[?k=v&...]`, as `tumblepipe.util.uri`.
//!
//! [`Uri::parse`] reads what Python's `Uri.parse` reads: a segment is
//! `[A-Za-z0-9_.-]+` or the wildcard `*`, and the query is kept sorted so
//! equal URIs compare equal whatever order their query was written in.
//! [`Uri::parse_address`] is the stricter form a write addresses one node
//! with (tumblepipe-mcp's): no wildcard, no query, a purpose.

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Characters allowed in a URI segment: Python's `NAME_ALPHABET`.
fn is_name_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.'
}

fn is_valid_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(is_name_char)
}

fn is_valid_segment(segment: &str) -> bool {
    segment == "*" || is_valid_name(segment)
}

/// Validate a raw segment name: no slashes, no wildcard, within the
/// alphabet. For tools that take names rather than URIs.
pub fn validate_segment(name: &str) -> Result<(), UriError> {
    if name.is_empty() {
        return Err(UriError::EmptySegment(name.to_string()));
    }
    if name == "*" {
        return Err(UriError::Wildcard(name.to_string()));
    }
    if !is_valid_name(name) {
        return Err(UriError::InvalidSegment { segment: name.to_string() });
    }
    Ok(())
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum UriError {
    #[error("uri missing ':' separator: {0:?}")]
    NoPurposeSeparator(String),
    #[error("uri purpose is empty: {0:?}")]
    EmptyPurpose(String),
    #[error("uri path must start with '/': {0:?}")]
    PathMissingSlash(String),
    #[error("uri path contains an empty segment: {0:?}")]
    EmptySegment(String),
    #[error("wildcard segments are not allowed: {0:?}")]
    Wildcard(String),
    #[error("invalid character in segment {segment:?}: only [A-Za-z0-9_\\-.] are allowed")]
    InvalidSegment { segment: String },
    #[error("query strings are not supported on write URIs: {0:?}")]
    QueryNotSupported(String),
}

/// A parsed URI. `query` is sorted by key (Python sorts its pairs, and a
/// parsed query holds each key once, the last value winning).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Uri {
    pub purpose: String,
    pub segments: Vec<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub query: BTreeMap<String, String>,
}

impl Uri {
    /// The URI `purpose:/segments...`, no query. Segments are not checked:
    /// for names already read from the config or the disk.
    pub fn new<S: Into<String>>(purpose: &str, segments: impl IntoIterator<Item = S>) -> Self {
        Uri { purpose: purpose.to_string(), segments: segments.into_iter().map(Into::into).collect(), query: BTreeMap::new() }
    }

    /// `purpose:/`, the root.
    pub fn root(purpose: &str) -> Self {
        Uri::new::<String>(purpose, [])
    }

    /// Parse as Python's `Uri.parse` does.
    pub fn parse(raw: &str) -> Result<Self, UriError> {
        let (purpose, rest) = raw.split_once(':').ok_or_else(|| UriError::NoPurposeSeparator(raw.to_string()))?;
        let (path, query_string) = match rest.split_once('?') {
            Some((path, query)) => (path, Some(query)),
            None => (rest, None),
        };
        let mut query = BTreeMap::new();
        if let Some(query_string) = query_string {
            for (key, value) in parse_qsl(query_string) {
                query.insert(key, value);
            }
        }
        let Some(path) = path.strip_prefix('/') else {
            return Err(UriError::PathMissingSlash(raw.to_string()));
        };
        let mut segments = Vec::new();
        if !path.is_empty() {
            for part in path.split('/') {
                if !is_valid_segment(part) {
                    return Err(if part.is_empty() {
                        UriError::EmptySegment(raw.to_string())
                    } else {
                        UriError::InvalidSegment { segment: part.to_string() }
                    });
                }
                segments.push(part.to_string());
            }
        }
        Ok(Uri { purpose: purpose.to_string(), segments, query })
    }

    /// Parse a URI that addresses one node of a `_config/db/<purpose>.json`:
    /// a purpose, no wildcard, no query.
    pub fn parse_address(raw: &str) -> Result<Self, UriError> {
        if raw.contains('?') {
            return Err(UriError::QueryNotSupported(raw.to_string()));
        }
        let (purpose, _) = raw.split_once(':').ok_or_else(|| UriError::NoPurposeSeparator(raw.to_string()))?;
        if purpose.is_empty() {
            return Err(UriError::EmptyPurpose(raw.to_string()));
        }
        let uri = Uri::parse(raw)?;
        if uri.is_wild() {
            return Err(UriError::Wildcard(raw.to_string()));
        }
        Ok(uri)
    }

    pub fn is_root(&self) -> bool {
        self.segments.is_empty()
    }

    pub fn is_wild(&self) -> bool {
        self.segments.iter().any(|segment| segment == "*")
    }

    pub fn last(&self) -> Option<&str> {
        self.segments.last().map(String::as_str)
    }

    pub fn first(&self) -> Option<&str> {
        self.segments.first().map(String::as_str)
    }

    /// `entity:/shots/010` for `entity:/shots/010/sh020`; None for a root.
    pub fn parent(&self) -> Option<Uri> {
        let (_, rest) = self.segments.split_last()?;
        Some(Uri { purpose: self.purpose.clone(), segments: rest.to_vec(), query: BTreeMap::new() })
    }

    /// This URI with `segment` appended, the query kept (Python's `/`).
    pub fn join(&self, segment: &str) -> Uri {
        let mut joined = self.clone();
        joined.segments.push(segment.to_string());
        joined
    }

    /// This URI with every one of `segments` appended.
    pub fn join_all<S: AsRef<str>>(&self, segments: &[S]) -> Uri {
        let mut joined = self.clone();
        joined.segments.extend(segments.iter().map(|segment| segment.as_ref().to_string()));
        joined
    }

    /// Whether `other` lies strictly below this URI (Python's `contains`).
    pub fn contains(&self, other: &Uri) -> bool {
        self.purpose == other.purpose
            && self.segments.len() < other.segments.len()
            && self.segments.iter().zip(&other.segments).all(|(a, b)| a == b)
    }

    /// `other` is this URI or lies below it.
    pub fn is_or_contains(&self, other: &Uri) -> bool {
        self == other || self.contains(other)
    }
}

impl fmt::Display for Uri {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:/{}", self.purpose, self.segments.join("/"))?;
        for (index, (key, value)) in self.query.iter().enumerate() {
            f.write_str(if index == 0 { "?" } else { "&" })?;
            write!(f, "{}={}", quote(key), quote(value))?;
        }
        Ok(())
    }
}

impl std::str::FromStr for Uri {
    type Err = UriError;
    fn from_str(raw: &str) -> Result<Self, UriError> {
        Uri::parse(raw)
    }
}

/// `urllib.parse.quote(text, safe="")`: everything but `A-Za-z0-9_.-~`
/// percent-encoded, as UTF-8.
fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || b"_.-~".contains(&byte) {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// `urllib.parse.parse_qsl(text, keep_blank_values=True)`: `&`-separated
/// `key=value` pairs (a pair without `=` has an empty value), `+` read as a
/// space and `%XX` decoded.
fn parse_qsl(text: &str) -> Vec<(String, String)> {
    text.split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            (unquote_plus(key), unquote_plus(value))
        })
        .collect()
}

fn unquote_plus(text: &str) -> String {
    let bytes = text.replace('+', " ").into_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok());
            if let Some(byte) = hex {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_like_python() {
        let uri = Uri::parse("entity:/shots/010/sh020?channel=bg&a=1").unwrap();
        assert_eq!(uri.purpose, "entity");
        assert_eq!(uri.segments, ["shots", "010", "sh020"]);
        assert_eq!(uri.to_string(), "entity:/shots/010/sh020?a=1&channel=bg");
        assert_eq!(Uri::parse("entity:/").unwrap().segments.len(), 0);
        assert!(Uri::parse("entity:/a/*").unwrap().is_wild());
        assert!(Uri::parse("entity:a").is_err());
        assert!(Uri::parse("entity:/a//b").is_err());
        assert!(Uri::parse("entity:/a/").is_err());
        assert!(Uri::parse("entity:/a b").is_err());
        assert!(Uri::parse("noseparator").is_err());
    }

    #[test]
    fn addresses_are_strict() {
        assert!(Uri::parse_address("entity:/a?x=1").is_err());
        assert!(Uri::parse_address("entity:/a/*").is_err());
        assert!(Uri::parse_address(":/a").is_err());
        assert!(Uri::parse_address("entity:/a/b").is_ok());
    }

    #[test]
    fn query_round_trips() {
        let uri = Uri::parse("x:/a?k=a%20b+c&e").unwrap();
        assert_eq!(uri.query["k"], "a b c");
        assert_eq!(uri.query["e"], "");
        assert_eq!(Uri::parse(&uri.to_string()).unwrap(), uri);
    }

    #[test]
    fn containment() {
        let shots = Uri::parse("entity:/shots").unwrap();
        let shot = Uri::parse("entity:/shots/010/sh020").unwrap();
        assert!(shots.contains(&shot));
        assert!(!shot.contains(&shot));
        assert!(shot.is_or_contains(&shot));
        assert_eq!(shot.parent().unwrap().to_string(), "entity:/shots/010");
        assert_eq!(shots.join("010").join("sh020"), shot);
    }
}

/// The strict form tumblepipe-mcp addresses nodes with.
#[cfg(test)]
mod address_tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn parses_root() {
        let u = Uri::parse_address("entity:/").unwrap();
        assert_eq!(u.purpose, "entity");
        assert!(u.segments.is_empty());
        assert!(u.is_root());
        assert_eq!(u.to_string(), "entity:/");
    }

    #[test]
    fn parses_with_segments() {
        let u = Uri::parse_address("entity:/shots/010/010_010").unwrap();
        assert_eq!(u.purpose, "entity");
        assert_eq!(u.segments, vec!["shots", "010", "010_010"]);
        assert_eq!(u.to_string(), "entity:/shots/010/010_010");
        assert_eq!(u.last(), Some("010_010"));
        assert_eq!(u.parent().unwrap().to_string(), "entity:/shots/010");
    }

    #[test]
    fn rejects_no_colon() {
        assert!(matches!(Uri::parse_address("entity/shots").unwrap_err(), UriError::NoPurposeSeparator(_)));
    }

    #[test]
    fn rejects_empty_purpose() {
        assert!(matches!(Uri::parse_address(":/foo").unwrap_err(), UriError::EmptyPurpose(_)));
    }

    #[test]
    fn rejects_relative_path() {
        assert!(matches!(Uri::parse_address("entity:shots").unwrap_err(), UriError::PathMissingSlash(_)));
    }

    #[test]
    fn rejects_empty_segment() {
        assert!(matches!(Uri::parse_address("entity:/shots//010").unwrap_err(), UriError::EmptySegment(_)));
    }

    #[test]
    fn rejects_trailing_slash() {
        // "entity:/shots/" splits into ["shots", ""] -> empty segment
        assert!(matches!(Uri::parse_address("entity:/shots/").unwrap_err(), UriError::EmptySegment(_)));
    }

    #[test]
    fn rejects_wildcard() {
        assert!(matches!(Uri::parse_address("entity:/*/foo").unwrap_err(), UriError::Wildcard(_)));
    }

    #[test]
    fn rejects_invalid_char() {
        assert!(matches!(Uri::parse_address("entity:/sh ots").unwrap_err(), UriError::InvalidSegment { .. }));
    }

    #[test]
    fn rejects_query_string() {
        assert!(matches!(Uri::parse_address("entity:/shots?foo=bar").unwrap_err(), UriError::QueryNotSupported(_)));
    }

    fn purpose_strategy() -> impl Strategy<Value = String> {
        "[a-z][a-z0-9_]{0,7}"
    }

    fn segment_strategy() -> impl Strategy<Value = String> {
        // Match the parser's NAME_ALPHABET while keeping examples readable.
        "[A-Za-z0-9_.-]{1,8}".prop_filter("non-wildcard", |s| s != "*")
    }

    proptest! {
        /// Any URI we construct from valid parts round-trips through Display + parse.
        #[test]
        fn round_trips_through_display(
            purpose in purpose_strategy(),
            segments in proptest::collection::vec(segment_strategy(), 0..5),
        ) {
            let original = Uri::new(&purpose, segments.clone());
            let printed = original.to_string();
            let parsed = Uri::parse_address(&printed)
                .map_err(|e| TestCaseError::fail(format!("parse failed on {printed:?}: {e}")))?;
            prop_assert_eq!(parsed, original);
        }

        /// Parsing arbitrary strings never panics.
        #[test]
        fn parse_never_panics(input in ".{0,64}") {
            let _ = Uri::parse_address(&input);
        }

        /// A successfully parsed URI prints to an input that parses to the same value.
        #[test]
        fn parse_then_display_then_parse_is_idempotent(input in ".{0,64}") {
            if let Ok(u1) = Uri::parse_address(&input) {
                let printed = u1.to_string();
                let u2 = Uri::parse_address(&printed)
                    .map_err(|e| TestCaseError::fail(format!("re-parse failed on {printed:?}: {e}")))?;
                prop_assert_eq!(u1, u2);
            }
        }
    }
}
