//! A shot's takes, for its clip's right-click menu: three submenus, each one
//! choice given the other two. **Source** picks playblast or render,
//! **Department** the department (and layer) of that source, **Version** a
//! version of that department. The menu only lists what is there for the
//! take shown. Only a Version row pins: a source or
//! department row follows that stream's newest version, so a new playblast
//! or render shows up by itself (docs/asset-browser/play-shots.md).
//! A fourth, **Layer** (#58), sets only the rule's render layer, Best
//! available by default; the clip's name says which layer plays and how
//! many others there are ([`layer_note`]).

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
        .map(|t| (t, format!("{}{}{}", takes[t].version, if takes[t].multi_range { " \u{b7} frame list" } else { "" }, mark(t == shown))))
        .collect();

    vec![
        Submenu { label: format!("Source: {}", kind_word(current.kind())), rows: sources, pins: false },
        Submenu { label: format!("Department: {}", current.department), rows: departments, pins: false },
        Submenu { label: format!("Version: {}", current.version), rows: versions, pins: true },
    ]
}

/// The render layers the Layer submenu offers for the take `shown`, in
/// playlist order: every layer of a take of its kind, in the rule's
/// department when it names one, else in any department (best available
/// then finds the most downstream with the layer). A playblast's plain
/// movie has no layer and is not one; a stack is, as `layers`.
pub fn layers<'a>(takes: &'a [Take], shown: usize, rule: &Rule) -> Vec<&'a str> {
    let Some(current) = takes.get(shown) else {
        return Vec::new();
    };
    let mut layers: Vec<&str> = Vec::new();
    for take in takes {
        let in_scope = take.kind() == current.kind() && rule.department.as_ref().is_none_or(|d| &take.department == d);
        if let Some(layer) = take.layer().filter(|_| in_scope)
            && !layers.contains(&layer)
        {
            layers.push(layer);
        }
    }
    layers
}

/// A row of the Layer submenu: the layer it sets on the clip's rule (None,
/// Best available) and its label.
pub type LayerRow = (Option<String>, String);

/// The Layer submenu (#58) for a clip whose rule is `rule` and whose take
/// on screen is `shown`: its label (the layer chosen, else best available)
/// and its rows: Best available, then [`layers`], the one shown marked. A
/// layer chosen that no take fits yet (its render not complete) is listed
/// as not ready, and the label says so. None when the shot has no layer
/// to choose (only plain playblast movies).
pub fn layer_submenu(takes: &[Take], shown: usize, rule: &Rule) -> Option<(String, Vec<LayerRow>)> {
    let current = takes.get(shown)?;
    let offered = layers(takes, shown, rule);
    if offered.is_empty() && rule.layer.is_none() {
        return None;
    }
    let waiting = th_edit::takes::layer_falls_back(takes, rule);
    let label = match rule.layer.as_deref() {
        None => "Layer: best available".to_string(),
        Some(chosen) if waiting => format!("Layer: {chosen}{NOT_READY}"),
        Some(chosen) => format!("Layer: {chosen}"),
    };
    let mut rows: Vec<LayerRow> = vec![(None, "Best available".to_string())];
    for layer in &offered {
        let mark = if current.layer() == Some(layer) { SHOWN } else { "" };
        rows.push((Some((*layer).to_string()), format!("{layer}{mark}")));
    }
    if let Some(chosen) = rule.layer.as_deref().filter(|c| !offered.contains(c)) {
        rows.push((Some(chosen.to_string()), format!("{chosen}{NOT_READY}")));
    }
    Some((label, rows))
}

/// The mark on a chosen layer no take fits yet.
const NOT_READY: &str = "  (not ready)";

/// `rule` with its layer set to `layer` (None: best available), the rest
/// kept: a Layer row changes only the layer.
pub fn with_layer(rule: &Rule, layer: Option<String>) -> Rule {
    Rule { layer, ..rule.clone() }
}

/// What a shot clip's name says after the shot (#58): the layer of the take
/// shown and how many other layers its Layer submenu offers (`chars (+2)`),
/// and a chosen layer it falls back from (`chars (+2) · default not
/// ready`). None for a take with no layer (a playblast's plain movie).
pub fn layer_note(takes: &[Take], shown: usize, rule: &Rule) -> Option<String> {
    let layer = takes.get(shown)?.layer()?;
    let others = layers(takes, shown, rule).into_iter().filter(|l| *l != layer).count();
    let mut note = match others {
        0 => layer.to_string(),
        n => format!("{layer} (+{n})"),
    };
    if let Some(chosen) = rule.layer.as_deref().filter(|_| th_edit::takes::layer_falls_back(takes, rule)) {
        note.push_str(&format!(" · {chosen} not ready"));
    }
    Some(note)
}

/// What a take key steps through (#47): the shown stream's versions (Up /
/// Down), the departments of the shown kind (Ctrl+Up / Down), or playblast
/// and render (Shift+Up / Down), the keys the old playlist player had.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TakeAxis {
    Version,
    Department,
    Source,
}

/// The take a take key moves to from `shown`, `delta` steps along `axis`,
/// and whether it pins: a version step pins that version (as a Version row
/// does); a department or source step follows that stream's newest (as
/// their rows do). Versions run oldest to newest, so +1 is newer;
/// departments and sources run in playlist order. None at either end, or
/// when there is nothing to step to. `of_kind` is as for [`submenus`].
pub fn step(takes: &[Take], shown: usize, axis: TakeAxis, delta: i8, of_kind: impl Fn(Kind) -> Option<usize>) -> Option<(usize, bool)> {
    let current = takes.get(shown)?;
    let neighbour = |len: usize, at: usize| at.checked_add_signed(isize::from(delta)).filter(|&j| j < len && j != at);
    match axis {
        TakeAxis::Version => {
            let same: Vec<usize> = (0..takes.len()).filter(|&t| takes[t].stream() == current.stream()).collect();
            let at = same.iter().position(|&t| t == shown)?;
            neighbour(same.len(), at).map(|j| (same[j], true))
        }
        TakeAxis::Department => {
            let mut streams: Vec<Stream> = Vec::new();
            for take in takes.iter().filter(|t| t.kind() == current.kind()) {
                if !streams.contains(&take.stream()) {
                    streams.push(take.stream());
                }
            }
            let at = streams.iter().position(|&s| s == current.stream())?;
            let to = streams[neighbour(streams.len(), at)?];
            (0..takes.len()).rev().find(|&t| takes[t].stream() == to).map(|t| (t, false))
        }
        TakeAxis::Source => {
            let mut kinds: Vec<Kind> = Vec::new();
            for take in takes {
                if !kinds.contains(&take.kind()) {
                    kinds.push(take.kind());
                }
            }
            let at = kinds.iter().position(|&k| k == current.kind())?;
            of_kind(kinds[neighbour(kinds.len(), at)?]).map(|t| (t, false))
        }
    }
}

/// The word a menu uses for a take's kind.
pub fn kind_word(kind: Kind) -> &'static str {
    match kind {
        Kind::Movie => "Playblast",
        Kind::Exr => "Render",
        Kind::Live => "Live",
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
            Kind::Movie | Kind::Live => Media::Movie { path: String::new() },
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
        Take { label: format!("{department} {version}"), department: department.into(), version: version.into(), media, multi_range: false }
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

    #[test]
    fn the_layer_menu_and_note_say_which_layer_plays() {
        // #58 as seen live: `default` not complete, best available plays
        // `chars`, and the clip now says two other layers are there.
        let takes = [
            take(Kind::Movie, "animation", None, "v0003"),
            take(Kind::Exr, "render", Some("chars"), "v0002"),
            take(Kind::Exr, "render", Some("env"), "v0002"),
            take(Kind::Exr, "render", Some("fx"), "v0002"),
        ];
        let best = Rule::best();
        assert_eq!(layer_note(&takes, 1, &best).as_deref(), Some("chars (+2)"));
        let (label, rows) = layer_submenu(&takes, 1, &best).unwrap();
        assert_eq!(label, "Layer: best available");
        let labels: Vec<&str> = rows.iter().map(|r| r.1.as_str()).collect();
        assert_eq!(labels, ["Best available", "chars  (shown)", "env", "fx"]);
        let waiting = with_layer(&best, Some("default".into()));
        assert_eq!(layer_note(&takes, 1, &waiting).as_deref(), Some("chars (+2) · default not ready"));
        let (label, rows) = layer_submenu(&takes, 1, &waiting).unwrap();
        assert_eq!(label, "Layer: default  (not ready)");
        assert_eq!(rows.last().unwrap(), &(Some("default".to_string()), "default  (not ready)".to_string()));
        assert_eq!(layer_note(&takes, 0, &best), None, "a playblast movie has no layer");
        assert_eq!(layer_submenu(&takes[..1], 0, &best), None);
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

        /// The Layer submenu (#58): Best available first, then each layer
        /// of the shown kind (in the rule's department when it names one)
        /// once, the shown take's layer the one row marked shown, and a
        /// chosen layer no take fits listed as not ready, as its label
        /// says. A row sets only the rule's layer. The clip's note names
        /// the shown layer and counts the other rows of real layers.
        #[test]
        fn the_layer_submenu_lists_each_layer_once_and_sets_only_the_layer(
            takes in prop::collection::vec(arb_take(), 1..30),
            at in any::<prop::sample::Index>(),
            department in prop::option::of(prop::sample::select(vec!["animation", "light", "comp"])),
            layer in prop::option::of(prop::sample::select(vec!["default", "slapcomp", "chars"])),
            pinned in any::<bool>(),
        ) {
            let shown = at.index(takes.len());
            let department = department.filter(|d| *d == takes[shown].department);
            let version = if pinned { VersionRule::Pinned(Version::try_from("v0001".to_string()).unwrap()) } else { VersionRule::Latest };
            let rule = Rule { department: department.map(Into::into), kind: None, layer: layer.map(Into::into), version };
            let offered = layers(&takes, shown, &rule);
            let mut unique = offered.clone();
            unique.sort_unstable();
            unique.dedup();
            prop_assert_eq!(unique.len(), offered.len());
            for l in &offered {
                prop_assert!(takes.iter().any(|t| t.layer() == Some(l) && t.kind() == takes[shown].kind()
                    && rule.department.as_ref().is_none_or(|d| &t.department == d)));
            }
            let waiting = th_edit::takes::layer_falls_back(&takes, &rule);
            match layer_submenu(&takes, shown, &rule) {
                None => {
                    prop_assert!(offered.is_empty() && rule.layer.is_none());
                    prop_assert_eq!(layer_note(&takes, shown, &rule), None);
                }
                Some((label, rows)) => {
                    prop_assert_eq!(&rows[0], &(None, "Best available".to_string()));
                    let real: Vec<&str> = rows[1..].iter().filter(|r| !r.1.ends_with(NOT_READY)).map(|r| r.0.as_deref().unwrap()).collect();
                    prop_assert_eq!(&real, &offered);
                    let marked: Vec<&str> = rows.iter().filter(|r| r.1.ends_with(SHOWN)).map(|r| r.0.as_deref().unwrap()).collect();
                    prop_assert_eq!(marked, takes[shown].layer().filter(|l| offered.contains(l)).into_iter().collect::<Vec<_>>());
                    let not_ready: Vec<&str> = rows.iter().filter(|r| r.1.ends_with(NOT_READY)).map(|r| r.0.as_deref().unwrap()).collect();
                    let chosen_missing = rule.layer.as_deref().filter(|c| !offered.contains(c));
                    prop_assert_eq!(not_ready, chosen_missing.into_iter().collect::<Vec<_>>());
                    match rule.layer.as_deref() {
                        None => prop_assert_eq!(label, "Layer: best available"),
                        Some(c) => prop_assert_eq!(label, format!("Layer: {c}{}", if waiting { NOT_READY } else { "" })),
                    }
                    for (row, _) in rows {
                        let set = with_layer(&rule, row.clone());
                        prop_assert_eq!(&set.layer, &row);
                        prop_assert_eq!(Rule { layer: rule.layer.clone(), ..set }, rule.clone());
                    }
                }
            }
            if let Some(shown_layer) = takes[shown].layer() {
                let others = offered.iter().filter(|l| **l != shown_layer).count();
                let mut expected = if others == 0 { shown_layer.to_string() } else { format!("{shown_layer} (+{others})") };
                if waiting {
                    expected = format!("{expected} · {} not ready", rule.layer.as_deref().unwrap());
                }
                prop_assert_eq!(layer_note(&takes, shown, &rule), Some(expected));
            } else {
                prop_assert_eq!(layer_note(&takes, shown, &rule), None);
            }
        }

        /// A take key goes where the menu would (#47): a version step to
        /// the next newer or older take of the shown stream, pinned; a
        /// department step to the neighbouring Department row's take; a
        /// source step to the neighbouring Source row's take, neither
        /// pinned. Stepping back undoes a step; nothing past either end.
        #[test]
        fn a_take_key_steps_to_the_menus_neighbour(
            takes in prop::collection::vec(arb_take(), 1..30),
            at in any::<prop::sample::Index>(),
            axis in prop_oneof![Just(TakeAxis::Version), Just(TakeAxis::Department), Just(TakeAxis::Source)],
            delta in prop_oneof![Just(-1i8), Just(1i8)],
        ) {
            let shown = at.index(takes.len());
            let menu = submenus(&takes, shown, last_of(&takes));
            // The menu's rows in step order: versions oldest first, the
            // rest as listed.
            let (rows, pins): (Vec<usize>, bool) = match axis {
                TakeAxis::Version => (menu[2].rows.iter().rev().map(|&(t, _)| t).collect(), true),
                TakeAxis::Department => (menu[1].rows.iter().map(|&(t, _)| t).collect(), false),
                TakeAxis::Source => (menu[0].rows.iter().map(|&(t, _)| t).collect(), false),
            };
            let same_row = |t: usize| match axis {
                TakeAxis::Version => t == shown,
                TakeAxis::Department => takes[t].stream() == takes[shown].stream(),
                TakeAxis::Source => takes[t].kind() == takes[shown].kind(),
            };
            let i = rows.iter().position(|&t| same_row(t)).expect("the shown row");
            let expected = i.checked_add_signed(isize::from(delta)).and_then(|j| rows.get(j).copied());
            let got = step(&takes, shown, axis, delta, last_of(&takes));
            prop_assert_eq!(got, expected.map(|t| (t, pins)));
            if axis == TakeAxis::Version
                && let Some((to, _)) = got
            {
                prop_assert_eq!(step(&takes, to, axis, -delta, last_of(&takes)), Some((shown, true)));
            }
        }
    }
}
