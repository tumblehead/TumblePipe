//! How the pipeline names entities and versions (`tumblepipe.naming`,
//! `tumblepipe.pipe.paths.naming`).

/// An entity name: ASCII letters and digits only. Names are joined with `_`
/// all over the pipeline, so `PROP/old_tree` and `PROP_old/tree` would
/// collide.
pub fn is_valid_entity_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric())
}

/// `v` and four or more ASCII digits (`v0001`, `v10000`).
pub fn is_valid_version_name(name: &str) -> bool {
    name.len() >= 5 && name.starts_with('v') && name[1..].bytes().all(|b| b.is_ascii_digit())
}

/// `v0003` for 3.
pub fn version_name(code: u64) -> String {
    format!("v{code:04}")
}

/// 3 for `v0003`; None for anything that is not a version name.
pub fn version_code(name: &str) -> Option<u64> {
    if !is_valid_version_name(name) {
        return None;
    }
    name[1..].parse().ok()
}

/// The sort key of a version name: its number (a non-version sorts first).
pub fn version_key(name: &str) -> u64 {
    version_code(name).unwrap_or(0)
}

/// `<entity>_<part>_...`: the entity's segments without the first (the kind
/// is the directory tree already), then `parts`, joined with `_`.
/// `entity_stem(entity:/shots/010/sh020, [lighting, v0003])` is
/// `010_sh020_lighting_v0003`.
pub fn entity_stem(segments: &[String], parts: &[&str]) -> String {
    segments.iter().skip(1).map(String::as_str).chain(parts.iter().copied()).collect::<Vec<_>>().join("_")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert!(is_valid_version_name("v0001"));
        assert!(is_valid_version_name("v10000"));
        assert!(!is_valid_version_name("v001"));
        assert!(!is_valid_version_name("x0001"));
        assert!(!is_valid_version_name("v00a1"));
        assert_eq!(version_code("v10000"), Some(10000));
        assert_eq!(version_name(7), "v0007");
    }

    #[test]
    fn stems() {
        let segments: Vec<String> = ["shots", "010", "sh020"].map(String::from).to_vec();
        assert_eq!(entity_stem(&segments, &["lighting", "v0003"]), "010_sh020_lighting_v0003");
        assert!(is_valid_entity_name("sh020"));
        assert!(!is_valid_entity_name("sh_020"));
        assert!(!is_valid_entity_name(""));
    }
}
