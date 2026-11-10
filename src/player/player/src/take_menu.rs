//! A shot's takes, for its clip's right-click menu: three submenus, each one
//! choice given the other two. **Source** picks playblast or render,
//! **Department** the department (and layer) of that source, **Version** a
//! version of that department. The menu only lists what is there for the
//! take shown. Only a Version row pins: a source or
//! department row follows that stream's newest version, so a new playblast
//! or render shows up by itself (docs/asset-browser/play-shots.md).

use th_edit::document::{Rule, Version, VersionRule};
use th_playlist::{Kind, Stream, Take};

/// One of the take menu's submenus: its label, its rows, each a take (by
/// index) and the row's label, and whether choosing a row pins its version.
#[derive(Debug, PartialEq, Eq)]
pub struct Submenu {
    pub label: String,
    pub rows: Vec<(usize, String)>,
    pub pins: bool,
}

/// The rule a clip gets for the take menu's choice: `None` is Best
/// available; a take gives its department, kind and layer, and its version
/// only when `pin` (a Version row, on the clicked clip's own shot), else
/// the newest.
pub fn rule(picked: Option<&Take>, pin: bool) -> Rule {
    match picked {
        None => Rule::best(),
        Some(t) => Rule {
            department: Some(t.department.clone()),
            kind: Some(th_edit::takes::kind_of(t)),
            layer: t.layer().map(str::to_owned),
            version: match pin {
                true => Version::try_from(t.version.clone()).map(VersionRule::Pinned).unwrap_or(VersionRule::Latest),
                false => VersionRule::Latest,
            },
        },
    }
}

/// The mark on the row of what is shown.
const SHOWN: &str = "  (shown)";

/// The menu's submenus for `takes`, `shown` the take on screen. `of_kind` is the
/// take a switch to another kind shows, None when
/// the shot has none of it.
pub fn submenus(takes: &[Take], shown: usize, of_kind: impl Fn(Kind) -> Option<usize>) -> Vec<Submenu> {
    let Some(current) = takes.get(shown) else {
        return Vec::new();
    };
    let mark = |yes: bool| if yes { SHOWN } else { "" };

    let mut kinds: Vec<Kind> = Vec::new();
    for take in takes {
        if !kinds.contains(&take.kind()) {
            kinds.push(take.kind());
        }
    }
    let sources = kinds
        .into_iter()
        .filter_map(|kind| {
            let target = if kind == current.kind() { Some(shown) } else { of_kind(kind) }?;
            Some((target, format!("{}{}", kind_word(kind), mark(kind == current.kind()))))
        })
        .collect();

    // The streams of the shown kind, in playlist order, each going to its
    // newest version.
    let mut streams: Vec<Stream> = Vec::new();
    for take in takes.iter().filter(|t| t.kind() == current.kind()) {
        if !streams.contains(&take.stream()) {
            streams.push(take.stream());
        }
    }
    let departments = streams
        .iter()
        .map(|&s| {
            let newest = (0..takes.len()).rev().find(|&t| takes[t].stream() == s).expect("a stream has a take");
            let layers = streams.iter().filter(|o| o.1 == s.1).count();
            let name = match s.2 {
                Some(layer) if layers > 1 => format!("{} ({layer})", s.1),
                _ => s.1.to_owned(),
            };
            (newest, format!("{name}{}", mark(s == current.stream())))
        })
        .collect();

    // The versions of the shown stream, newest first.
    let versions = (0..takes.len())
        .rev()
        .filter(|&t| takes[t].stream() == current.stream())
        .map(|t| (t, format!("{}{}", takes[t].version, mark(t == shown))))
        .collect();

    vec![
        Submenu { label: format!("Source: {}", kind_word(current.kind())), rows: sources, pins: false },
        Submenu { label: format!("Department: {}", current.department), rows: departments, pins: false },
        Submenu { label: format!("Version: {}", current.version), rows: versions, pins: true },
    ]
}

/// The word a menu uses for a take's kind.
pub fn kind_word(kind: Kind) -> &'static str {
    match kind {
        Kind::Movie => "Playblast",
        Kind::Exr => "Render",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_playlist::{Frames, Media};

    /// A take of `kind`; a render is always of a layer, `layer` or "default".
    fn take(kind: Kind, department: &str, layer: Option<&str>, version: &str) -> Take {
        let media = match kind {
            Kind::Movie => Media::Movie { path: String::new() },
            Kind::Exr => {
                let frames = Frames {
                    directory: String::new(),
                    prefix: String::new(),
                    suffix: String::new(),
                    padding: 4,
                    first: 1,
                    last: 1,
                    step: 1,
                };
                Media::Exr { layer: layer.unwrap_or("default").into(), frames }
            }
        };
        Take { label: format!("{department} {version}"), department: department.into(), version: version.into(), media }
    }

    /// The last take of a kind, as the player falls back to.
    fn last_of(takes: &[Take]) -> impl Fn(Kind) -> Option<usize> + '_ {
        |kind| (0..takes.len()).rev().find(|&t| takes[t].kind() == kind)
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
        let g = |s: &str, rows: Vec<(usize, String)>, pins| Submenu { label: s.into(), rows, pins };
        assert_eq!(
            submenus(&takes, 1, last_of(&takes)),
            vec![
                g("Source: Render", vec![t(2, "Playblast"), t(1, "Render  (shown)")], false),
                g("Department: light", vec![t(5, "light (default)  (shown)"), t(3, "light (slapcomp)"), t(4, "comp")], false),
                g("Version: v0003", vec![t(5, "v0004"), t(1, "v0003  (shown)")], true),
            ]
        );
    }

    fn arb_take() -> impl Strategy<Value = Take> {
        let kind = prop_oneof![Just(Kind::Movie), Just(Kind::Exr)];
        let department = prop::sample::select(vec!["animation", "light", "comp"]);
        let layer = prop::option::of(prop::sample::select(vec!["default", "slapcomp"]));
        let version = prop::sample::select(vec!["v0001", "v0002"]);
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

            let kinds: Vec<Kind> = targets(groups[0]).iter().map(|&t| takes[t].kind()).collect();
            let mut unique = kinds.clone();
            unique.dedup();
            prop_assert_eq!(unique.len(), kinds.len());
            prop_assert!(kinds.contains(&takes[shown].kind()));
            prop_assert_eq!(marked(groups[0]), vec![shown]);

            let streams: Vec<_> = targets(groups[1]).iter().map(|&t| takes[t].stream()).collect();
            let all: std::collections::HashSet<_> = takes.iter().filter(|t| t.kind() == takes[shown].kind()).map(Take::stream).collect();
            prop_assert_eq!(streams.len(), all.len());
            for &t in &targets(groups[1]) {
                prop_assert!(takes[t + 1..].iter().all(|o| o.stream() != takes[t].stream()), "the newest version");
            }
            let marked_department = marked(groups[1]);
            prop_assert_eq!(marked_department.len(), 1);
            prop_assert_eq!(takes[marked_department[0]].stream(), takes[shown].stream());

            let versions = targets(groups[2]);
            let expected: Vec<usize> = (0..takes.len()).rev().filter(|&t| takes[t].stream() == takes[shown].stream()).collect();
            prop_assert_eq!(versions, expected);
            prop_assert_eq!(marked(groups[2]), vec![shown]);
        }

        /// Only the Version submenu pins. A rule from a source or
        /// department row follows the newest version of that department,
        /// kind and layer; a Version row's rule pins exactly that take's
        /// version; no choice is Best available.
        #[test]
        fn only_a_version_row_pins(takes in prop::collection::vec(arb_take(), 1..30), at in any::<prop::sample::Index>()) {
            let shown = at.index(takes.len());
            let menu = submenus(&takes, shown, last_of(&takes));
            prop_assert_eq!(menu.iter().map(|s| s.pins).collect::<Vec<_>>(), vec![false, false, true]);
            for submenu in &menu {
                for &(t, _) in &submenu.rows {
                    let r = rule(Some(&takes[t]), submenu.pins);
                    prop_assert_eq!(r.department.as_deref(), Some(takes[t].department.as_str()));
                    prop_assert_eq!(r.kind, Some(th_edit::takes::kind_of(&takes[t])));
                    prop_assert_eq!(r.layer.as_deref(), takes[t].layer());
                    match submenu.pins {
                        true => prop_assert_eq!(r.version, VersionRule::Pinned(Version::try_from(takes[t].version.clone()).unwrap())),
                        false => prop_assert_eq!(r.version, VersionRule::Latest),
                    }
                }
            }
            prop_assert_eq!(rule(None, true), Rule::best());
        }
    }
}
