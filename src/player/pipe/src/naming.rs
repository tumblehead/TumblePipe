//! How the pipeline names entities and versions (`tumblepipe.naming`,
//! `tumblepipe.pipe.paths.naming`).

/// The device names Windows keeps in every folder, whatever the case
/// (`naming.WINDOWS_RESERVED`): a folder of one of these names cannot be
/// made or opened there, so an entity named `con` or `Aux` breaks every
/// Windows machine of the project.
pub const WINDOWS_RESERVED: [&str; 24] = [
    "con", "prn", "aux", "nul", "com0", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9", "lpt0", "lpt1", "lpt2",
    "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];

/// Whether `name` is one of [`WINDOWS_RESERVED`], in any case.
pub fn is_windows_reserved(name: &str) -> bool {
    WINDOWS_RESERVED.contains(&name.to_ascii_lowercase().as_str())
}

/// ASCII letters and digits, non-empty (`naming.is_name_alphabet`): for
/// telling names from other folders when listing what exists. A new name
/// must also pass [`is_valid_entity_name`].
pub fn is_name_alphabet(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric())
}

/// Why `name` cannot name a new entity, finishing a sentence that starts
/// with the name (`naming.entity_name_problem`); None when it can.
pub fn entity_name_problem(name: &str) -> Option<&'static str> {
    if !is_name_alphabet(name) {
        Some("may only use letters and digits")
    } else if is_windows_reserved(name) {
        Some("is a device name Windows reserves (CON, PRN, AUX, NUL, COM0-9, LPT0-9)")
    } else {
        None
    }
}

/// [`entity_name_problem`] for a name already found wanting.
pub fn entity_name_reason(name: &str) -> &'static str {
    entity_name_problem(name).unwrap_or("may only use letters and digits")
}

/// An entity name: ASCII letters and digits only, and no Windows device
/// name. Names are joined with `_` all over the pipeline, so `PROP/old_tree`
/// and `PROP_old/tree` would collide.
pub fn is_valid_entity_name(name: &str) -> bool {
    entity_name_problem(name).is_none()
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

    use proptest::prelude::*;

    /// Windows' device names, spelled out apart from [`WINDOWS_RESERVED`].
    fn devices() -> Vec<String> {
        let mut all: Vec<String> = ["con", "prn", "aux", "nul"].map(String::from).to_vec();
        for stem in ["com", "lpt"] {
            all.extend((0..10).map(|i| format!("{stem}{i}")));
        }
        all
    }

    fn any_case(word: &str, flips: &[bool]) -> String {
        word.chars()
            .zip(flips.iter().chain(std::iter::repeat(&false)))
            .map(|(c, &up)| if up { c.to_ascii_uppercase() } else { c })
            .collect()
    }

    proptest! {
        /// `con`, `Aux`, `LPT1` and the rest passed the letters-and-digits
        /// rule, though no Windows machine can make or open a folder of that
        /// name. In any case they are refused, and the reason names Windows.
        #[test]
        fn a_device_name_in_any_case_is_refused(device in proptest::sample::select(devices()), flips in prop::collection::vec(any::<bool>(), 0..4)) {
            let name = any_case(&device, &flips);
            prop_assert!(!is_valid_entity_name(&name));
            prop_assert!(entity_name_problem(&name).is_some_and(|p| p.contains("Windows")), "{:?}", entity_name_problem(&name));
            prop_assert!(is_name_alphabet(&name), "listing still sees {}", name);
        }

        /// A name that only contains a device name (`console`, `com10`,
        /// `Xnul`) is still valid.
        #[test]
        fn a_name_around_a_device_name_is_valid(device in proptest::sample::select(devices()), extra in "[A-Za-z0-9]{1,3}", before in any::<bool>()) {
            let name = if before { format!("{extra}{device}") } else { format!("{device}{extra}") };
            prop_assert_eq!(is_valid_entity_name(&name), !devices().contains(&name.to_ascii_lowercase()));
        }

        /// The problem is None exactly when the name is valid, and the
        /// reason given for a refused name is that problem.
        #[test]
        fn the_problem_is_none_exactly_when_valid(name in "[A-Za-z0-9_ é]{0,6}") {
            prop_assert_eq!(entity_name_problem(&name).is_none(), is_valid_entity_name(&name));
            if let Some(problem) = entity_name_problem(&name) {
                prop_assert_eq!(entity_name_reason(&name), problem);
            }
        }
    }
}
