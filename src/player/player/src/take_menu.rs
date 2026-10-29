//! A shot's takes, for its clip's right-click menu: three submenus, each one
//! choice given the other two, as the keys make them. **Source** picks
//! playblast or render, **Department** the department (and layer) of that
//! source, **Version** a version of that department. Choosing a source or
//! department shows the take its key would; the menu only lists what is
//! there for the take shown.

use th_playlist::{Kind, Take};

/// One of the take menu's submenus: its label, and its rows, each a take
/// (by index) and the row's label.
#[derive(Debug, PartialEq, Eq)]
pub struct Submenu {
    pub label: String,
    pub rows: Vec<(usize, String)>,
}

/// The mark on the row of what is shown.
const SHOWN: &str = "  (shown)";

/// The menu's submenus for `takes`, `shown` the take on screen. `of_kind` is the
/// take a switch to another kind shows (the Shift+Up/Down key's), None when
/// the shot has none of it.
pub fn submenus(takes: &[Take], shown: usize, of_kind: impl Fn(Kind) -> Option<usize>) -> Vec<Submenu> {
    let Some(current) = takes.get(shown) else {
        return Vec::new();
    };
    let mark = |yes: bool| if yes { SHOWN } else { "" };

    let mut kinds: Vec<Kind> = Vec::new();
    for take in takes {
        if !kinds.contains(&take.kind) {
            kinds.push(take.kind);
        }
    }
    let sources = kinds
        .into_iter()
        .filter_map(|kind| {
            let target = if kind == current.kind { Some(shown) } else { of_kind(kind) }?;
            Some((target, format!("{}{}", kind_word(kind), mark(kind == current.kind))))
        })
        .collect();

    // The streams of the shown kind, in playlist order, each going to its
    // newest version (the Ctrl+Up/Down key's).
    let mut streams: Vec<Stream> = Vec::new();
    for take in takes.iter().filter(|t| t.kind == current.kind) {
        if !streams.contains(&stream(take)) {
            streams.push(stream(take));
        }
    }
    let departments = streams
        .iter()
        .map(|&s| {
            let newest = (0..takes.len()).rev().find(|&t| stream(&takes[t]) == s).expect("a stream has a take");
            let layers = streams.iter().filter(|o| o.1 == s.1).count();
            let name = match s.2 {
                Some(layer) if layers > 1 => format!("{} ({layer})", department_word(s.1)),
                _ => department_word(s.1),
            };
            (newest, format!("{name}{}", mark(s == stream(current))))
        })
        .collect();

    // The versions of the shown stream, newest first.
    let versions = (0..takes.len())
        .rev()
        .filter(|&t| stream(&takes[t]) == stream(current))
        .map(|t| (t, format!("{}{}", version_word(&takes[t]), mark(t == shown))))
        .collect();

    vec![
        Submenu { label: format!("Source: {}", kind_word(current.kind)), rows: sources },
        Submenu { label: format!("Department: {}", department_word(&current.department)), rows: departments },
        Submenu { label: format!("Version: {}", version_word(current)), rows: versions },
    ]
}

/// The stream a take is a version of: its kind, department and layer.
type Stream<'a> = (Kind, &'a str, Option<&'a str>);

fn stream(take: &Take) -> Stream<'_> {
    (take.kind, &take.department, take.layer.as_deref())
}

/// The word a menu uses for a take's kind.
pub fn kind_word(kind: Kind) -> &'static str {
    match kind {
        Kind::Movie => "Playblast",
        Kind::Exr => "Render",
    }
}

fn department_word(department: &str) -> String {
    if department.is_empty() { "Other".to_string() } else { department.to_string() }
}

/// A take's version, or its label when the playlist gives none.
fn version_word(take: &Take) -> &str {
    if take.version.is_empty() { &take.label } else { &take.version }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn take(kind: Kind, department: &str, layer: Option<&str>, version: &str) -> Take {
        Take {
            label: format!("{department} {version}"),
            path: String::new(),
            rate: None,
            kind,
            department: department.into(),
            layer: layer.map(Into::into),
            version: version.into(),
        }
    }

    /// The last take of a kind, as the player falls back to.
    fn last_of(takes: &[Take]) -> impl Fn(Kind) -> Option<usize> + '_ {
        |kind| (0..takes.len()).rev().find(|&t| takes[t].kind == kind)
    }

    #[test]
    fn the_menu_offers_each_choice_given_the_other_two() {
        let takes = [
            take(Kind::Movie, "animation", None, "v0001"),
            take(Kind::Exr, "light", Some("default"), "v0003"),
            take(Kind::Movie, "animation", None, "v0002"),
            take(Kind::Exr, "light", Some("slapcomp"), "v0003"),
            take(Kind::Exr, "comp", Some("default"), "v0001"),
            take(Kind::Exr, "light", Some("default"), "v0004"),
        ];
        let t = |i: usize, s: &str| (i, s.to_string());
        let g = |s: &str, rows: Vec<(usize, String)>| Submenu { label: s.into(), rows };
        assert_eq!(
            submenus(&takes, 1, last_of(&takes)),
            vec![
                g("Source: Render", vec![t(2, "Playblast"), t(1, "Render  (shown)")]),
                g("Department: light", vec![t(5, "light (default)  (shown)"), t(3, "light (slapcomp)"), t(4, "comp")]),
                g("Version: v0003", vec![t(5, "v0004"), t(1, "v0003  (shown)")]),
            ]
        );
    }

    fn arb_take() -> impl Strategy<Value = Take> {
        let kind = prop_oneof![Just(Kind::Movie), Just(Kind::Exr)];
        let department = prop::sample::select(vec!["", "animation", "light", "comp"]);
        let layer = prop::option::of(prop::sample::select(vec!["default", "slapcomp"]));
        let version = prop::sample::select(vec!["", "v0001", "v0002"]);
        (kind, department, layer, version).prop_map(|(k, d, l, v)| take(k, d, l, v))
    }

    fn marked(rows: &[(usize, String)]) -> Vec<usize> {
        rows.iter().filter(|(_, label)| label.ends_with(SHOWN)).map(|&(t, _)| t).collect()
    }

    proptest! {
        /// Source lists each kind the shot has once and goes to a take of
        /// it; Department lists every stream of the shown kind once and
        /// goes to its newest version; Version lists exactly the shown
        /// stream's takes, newest first. Each marks one row: the shown
        /// take's.
        #[test]
        fn each_submenu_lists_its_choices_once(takes in prop::collection::vec(arb_take(), 1..30), at in any::<prop::sample::Index>()) {
            let shown = at.index(takes.len());
            let menu = submenus(&takes, shown, last_of(&takes));
            prop_assert_eq!(menu.len(), 3);
            let groups: Vec<&Vec<(usize, String)>> = menu.iter().map(|s| &s.rows).collect();
            let targets = |rows: &Vec<(usize, String)>| rows.iter().map(|&(t, _)| t).collect::<Vec<_>>();

            let kinds: Vec<Kind> = targets(groups[0]).iter().map(|&t| takes[t].kind).collect();
            let mut unique = kinds.clone();
            unique.dedup();
            prop_assert_eq!(unique.len(), kinds.len());
            prop_assert!(kinds.contains(&takes[shown].kind));
            prop_assert_eq!(marked(groups[0]), vec![shown]);

            let streams: Vec<_> = targets(groups[1]).iter().map(|&t| stream(&takes[t])).collect();
            let all: std::collections::HashSet<_> = takes.iter().filter(|t| t.kind == takes[shown].kind).map(stream).collect();
            prop_assert_eq!(streams.len(), all.len());
            for &t in &targets(groups[1]) {
                prop_assert!(takes[t + 1..].iter().all(|o| stream(o) != stream(&takes[t])), "the newest version");
            }
            let marked_department = marked(groups[1]);
            prop_assert_eq!(marked_department.len(), 1);
            prop_assert_eq!(stream(&takes[marked_department[0]]), stream(&takes[shown]));

            let versions = targets(groups[2]);
            let expected: Vec<usize> = (0..takes.len()).rev().filter(|&t| stream(&takes[t]) == stream(&takes[shown])).collect();
            prop_assert_eq!(versions, expected);
            prop_assert_eq!(marked(groups[2]), vec![shown]);
        }
    }
}
