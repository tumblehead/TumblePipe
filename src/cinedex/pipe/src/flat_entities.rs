//! The project settings *Flat asset list* and *Flat shot list*: a port of
//! `bucket_from` and `flat_bucket_in` in
//! `python/tumblepipe/config/flat_entities.py`.
//!
//! Entities stay two levels deep (`entity:/shots/<sequence>/<shot>`); a
//! tiny project that wants no sequences (or no categories) gets one
//! *implicit bucket* per kind, which the browser and the editor's bins stop
//! showing. Stored as `flat_assets` / `flat_shots` on `config:/project`,
//! each the name of its bucket; a project that never set them is not flat.

use serde_json::Value;

use crate::config::{Config, Properties};
use crate::uri::Uri;

/// The two entity kinds that have a bucket level.
pub const KINDS: [&str; 2] = ["assets", "shots"];

/// The `config:/project` property of `kind`, and the bucket a hand-written
/// `true` means.
fn key_and_default(kind: &str) -> Result<(&'static str, &'static str), String> {
    match kind {
        "assets" => Ok(("flat_assets", "ASSETS")),
        "shots" => Ok(("flat_shots", "SHOTS")),
        _ => Err(format!("kind must be one of ('assets', 'shots'), not '{kind}'")),
    }
}

/// Python's `str.isspace`, which `str.strip()` trims: Rust's whitespace and
/// the information separators U+001C to U+001F.
fn python_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// The implicit bucket `properties` (`config:/project`'s) name for `kind`,
/// or None when that kind is not flat. A `true` means the default bucket;
/// anything that is not a non-blank string or `true` (`false`, `""`, `1`)
/// means off.
pub fn bucket_from(properties: Option<&Properties>, kind: &str) -> Result<Option<String>, String> {
    let (key, default) = key_and_default(kind)?;
    Ok(match properties.and_then(|p| p.get(key)) {
        Some(Value::Bool(true)) => Some(default.to_owned()),
        Some(Value::String(text)) => {
            let trimmed = text.trim_matches(python_space);
            (!trimmed.is_empty()).then(|| trimmed.to_owned())
        }
        _ => None,
    })
}

/// The characters whose Unicode case folding (Python's `str.casefold`) is
/// all ASCII, but which are not ASCII themselves, and what they fold to.
const FOLD_TO_ASCII: [(char, &str); 11] = [
    ('\u{df}', "ss"),   // ß
    ('\u{17f}', "s"),   // ſ, long s
    ('\u{1e9e}', "ss"), // ẞ
    ('\u{212a}', "k"),  // Kelvin sign
    ('\u{fb00}', "ff"),
    ('\u{fb01}', "fi"),
    ('\u{fb02}', "fl"),
    ('\u{fb03}', "ffi"),
    ('\u{fb04}', "ffl"),
    ('\u{fb05}', "st"),
    ('\u{fb06}', "st"),
];

/// `name` case-folded as Python's `str.casefold` does, when that is all
/// ASCII; None when it is not (it then equals no ASCII name's fold).
fn ascii_fold(name: &str) -> Option<String> {
    let mut out = String::with_capacity(name.len());
    for c in name.chars() {
        if c.is_ascii() {
            out.push(c.to_ascii_lowercase());
        } else {
            out.push_str(FOLD_TO_ASCII.iter().find(|(from, _)| *from == c)?.1);
        }
    }
    Some(out)
}

/// The bucket already holding an entity of `kind` named `name` by
/// casefold, or None: a port of `name_taken` in `flat_entities.py`. A flat
/// list shows no bucket, so two shots `010` in two sequences would be two
/// identical rows: a flat kind refuses a name any of its buckets has.
/// `entities` are entity URI segment paths (`["shots", "SQ", "010"]`);
/// the first holder in path order answers.
///
/// `name` is a new entity's, an entity name (ASCII letters and digits,
/// `naming::entity_name_problem`), and is compared by Python's casefold
/// with names already there, whatever they hold. (A `name` that is not
/// ASCII is compared by Rust's lower case, which is Python's casefold but
/// for a few letters such as `ß`.)
pub fn name_taken(kind: &str, name: &str, entities: &[Vec<String>]) -> Result<Option<String>, String> {
    key_and_default(kind)?;
    let same: Box<dyn Fn(&str) -> bool> = if name.is_ascii() {
        let fold = name.to_ascii_lowercase();
        Box::new(move |other: &str| ascii_fold(other).is_some_and(|o| o == fold))
    } else {
        let fold = name.to_lowercase();
        Box::new(move |other: &str| other.to_lowercase() == fold)
    };
    let mut paths: Vec<&Vec<String>> = entities.iter().filter(|p| p.len() == 3 && p[0] == kind).collect();
    paths.sort();
    Ok(paths.into_iter().find(|p| same(&p[2])).map(|p| p[1].clone()))
}

/// The implicit bucket of `kind` in the project of `config`, or None when
/// that kind is not flat.
pub fn flat_bucket_in(config: &Config, kind: &str) -> Result<Option<String>, String> {
    let project = Uri::parse("config:/project").map_err(|e| e.to_string())?;
    let properties = config.get_properties(&project).map_err(|e| e.to_string())?;
    bucket_from(properties.as_ref(), kind)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn props(value: Value) -> Properties {
        value.as_object().cloned().unwrap()
    }

    #[test]
    fn only_a_non_blank_string_or_true_turns_a_kind_flat() {
        for (value, want) in [
            (json!(true), Some("SHOTS")),
            (json!(" 000 "), Some("000")),
            (json!("\u{1c}SQ\u{1f}"), Some("SQ")),
            (json!(false), None),
            (json!(""), None),
            (json!("  "), None),
            (json!(1), None),
            (json!(null), None),
            (json!([]), None),
        ] {
            let got = bucket_from(Some(&props(json!({ "flat_shots": value }))), "shots").unwrap();
            assert_eq!(got.as_deref(), want, "{value}");
        }
        assert_eq!(bucket_from(None, "assets").unwrap(), None);
        // One kind flat says nothing about the other.
        assert_eq!(bucket_from(Some(&props(json!({ "flat_shots": "X" }))), "assets").unwrap(), None);
        assert!(bucket_from(None, "edits").is_err());
    }

    fn paths(list: &[&str]) -> Vec<Vec<String>> {
        list.iter().map(|p| p.split('/').map(str::to_owned).collect()).collect()
    }

    #[test]
    fn a_flat_name_is_taken_by_any_bucket_by_casefold() {
        let entities =
            paths(&["shots/B/sh010", "shots/A/SH020", "shots/C", "assets/CHAR/sh030", "shots/Z/stra\u{df}e", "shots/Y/\u{212a}1"]);
        let taken = |name: &str| name_taken("shots", name, &entities).unwrap();
        assert_eq!(taken("SH010").as_deref(), Some("B"));
        assert_eq!(taken("sh020").as_deref(), Some("A"));
        assert_eq!(taken("SH030"), None, "an asset's name is not a shot's");
        assert_eq!(taken("C"), None, "a sequence is no shot");
        assert_eq!(taken("STRASSE").as_deref(), Some("Z"), "\u{df} folds to ss");
        assert_eq!(taken("k1").as_deref(), Some("Y"), "the Kelvin sign folds to k");
        assert_eq!(taken("SH040"), None);
        let twice = paths(&["shots/B/x1", "shots/A/X1"]);
        assert_eq!(name_taken("shots", "x1", &twice).unwrap().as_deref(), Some("A"), "the first in path order");
        assert!(name_taken("edits", "x", &entities).is_err());
    }
}
