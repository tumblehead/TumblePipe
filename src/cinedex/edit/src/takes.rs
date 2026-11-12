//! Which of a shot's takes a rule picks (`designs/edit-entity.md` §2.2,
//! §8.1).
//!
//! A shot's takes are the pipeline's discovery (`th_pipe::discovery`):
//! playblasts, then complete renders, each department in pipeline order
//! (later is more downstream), versions oldest first. A rule narrows them
//! field by field; what it leaves open is chosen as **best available**:
//!
//! 1. kind: a render when there is one, else a playblast;
//! 2. department: the most downstream with a take of that kind;
//! 3. layer: a playblast's plain movie before its stack of layers; a
//!    render's `slapcomp`, else `default`, else its first layer (a stack
//!    only when the rule names it, `layers`);
//! 4. version: the newest (`latest`), or the one pinned.
//!
//! A rule with every field set and a pinned version picks exactly that
//! take, so a frozen take thawed into a rule picks itself again.
//!
//! A layer the rule names (chosen in the clip's take menu, #58) that no
//! take fits, say a render of it not yet complete, falls back: the rule is
//! picked again with its layer open, so the clip plays what best available
//! plays for the rest of the rule rather than nothing. The rule keeps the
//! layer, so the clip changes to it by itself once its render is complete.

use th_playlist::{Kind, Media, STACK_STREAM, Take};

use crate::document::{FrozenTake, Rule, TakeKind, Version, VersionRule};

/// The render layer a best-available render shows first.
pub const SLAPCOMP: &str = "slapcomp";
pub const DEFAULT_LAYER: &str = "default";

/// A take's kind as a rule names it.
pub fn kind_of(take: &Take) -> TakeKind {
    match take.kind() {
        // A live take is a playblast still rendering; the editor plays one
        // over the rule, and never hands it to a rule (or a publish).
        Kind::Movie | Kind::Live => TakeKind::Playblast,
        Kind::Exr => TakeKind::Render,
    }
}

/// The take of `takes` that `rule` picks, by index; None when none fits.
/// A named layer no take fits falls back to the rule with its layer open.
pub fn pick(takes: &[Take], rule: &Rule) -> Option<usize> {
    pick_exactly(takes, rule).or_else(|| match rule.layer {
        Some(_) => pick_exactly(takes, &Rule { layer: None, ..rule.clone() }),
        None => None,
    })
}

/// Whether `rule` names a layer and no take fits it, so [`pick`] shows the
/// rule's fallback instead (the take menu says so).
pub fn layer_falls_back(takes: &[Take], rule: &Rule) -> bool {
    rule.layer.is_some() && pick_exactly(takes, rule).is_none()
}

/// The take a clip with `rule` shows while the editor shows every shot as
/// `show` (#57, View > Show; the view's only, never saved or published),
/// and whether the shot has no take of that kind at all, so the clip shows
/// its cut's take (`pick`) instead. `None` shows the cut. A clip whose cut
/// already shows that kind shows its cut (a pinned version too); else the
/// newest of that kind in the rule's department and layer, else best
/// available of that kind.
pub fn pick_shown(takes: &[Take], rule: &Rule, show: Option<TakeKind>) -> (Option<usize>, bool) {
    let cut = pick(takes, rule);
    let Some(kind) = show else { return (cut, false) };
    if cut.is_some_and(|t| kind_of(&takes[t]) == kind) {
        return (cut, false);
    }
    let same = Rule { kind: Some(kind), version: VersionRule::Latest, ..rule.clone() };
    let any = Rule { kind: Some(kind), layer: rule.layer.clone(), ..Rule::best() };
    match pick(takes, &same).or_else(|| pick(takes, &any)) {
        Some(t) => (Some(t), false),
        None => (cut, true),
    }
}

/// The take `rule` picks with its layer as given.
fn pick_exactly(takes: &[Take], rule: &Rule) -> Option<usize> {
    // A Frame List render's movie (#84) is never published: only a rule
    // pinning its version picks it (#86).
    let pinned = matches!(rule.version, VersionRule::Pinned(_));
    let fits = |t: &Take| {
        (pinned || !t.multi_range)
            && rule.department.as_ref().is_none_or(|d| &t.department == d)
            && rule.kind.is_none_or(|k| kind_of(t) == k)
            && rule.layer.as_deref().is_none_or(|l| t.layer() == Some(l))
    };
    let candidates: Vec<usize> = (0..takes.len()).filter(|&i| fits(&takes[i])).collect();
    let of = |pred: &dyn Fn(&Take) -> bool| candidates.iter().copied().filter(|&i| pred(&takes[i])).collect::<Vec<_>>();

    let kind = match rule.kind {
        Some(kind) => kind,
        None if !of(&|t| kind_of(t) == TakeKind::Render).is_empty() => TakeKind::Render,
        None => TakeKind::Playblast,
    };
    let of_kind = of(&|t| kind_of(t) == kind);
    let department = match &rule.department {
        Some(d) => d.clone(),
        None => takes[*of_kind.last()?].department.clone(),
    };
    let in_department: Vec<usize> = of_kind.iter().copied().filter(|&i| takes[i].department == department).collect();
    let layers: Vec<Option<&str>> = {
        let mut seen = Vec::new();
        for &i in &in_department {
            let layer = takes[i].layer();
            if !seen.contains(&layer) {
                seen.push(layer);
            }
        }
        seen
    };
    let layer = match &rule.layer {
        Some(l) => Some(l.as_str()),
        None => {
            let plain: Vec<Option<&str>> = layers.iter().copied().filter(|l| *l != Some(STACK_STREAM)).collect();
            let prefer = [None, Some(SLAPCOMP), Some(DEFAULT_LAYER)];
            match prefer.iter().find(|p| plain.contains(p)) {
                Some(&p) => p,
                None => *plain.first().or(layers.first())?,
            }
        }
    };
    let stream: Vec<usize> = in_department.into_iter().filter(|&i| takes[i].layer() == layer).collect();
    match &rule.version {
        VersionRule::Latest => stream.last().copied(),
        VersionRule::Pinned(v) => stream.into_iter().find(|&i| takes[i].version == v.as_str()),
    }
}

/// The take a live playblast of `department` shows where its render has not
/// reached: the department's latest playblast movie (its plain movie, not a
/// stack of layers), else `picked` (what the rule picks) when that is a
/// movie. None when neither is: an EXR render is scene-linear and a live
/// frame is not, so nothing shows under it (the frame stays pending).
pub fn under_live(takes: &[Take], department: &str, picked: Option<usize>) -> Option<usize> {
    let movie = |t: &Take| matches!(t.media, Media::Movie { .. });
    (0..takes.len())
        .rev()
        .find(|&i| takes[i].department == department && movie(&takes[i]))
        .or_else(|| picked.filter(|&i| takes.get(i).is_some_and(movie)))
}

/// `take` as a frozen edit holds it.
pub fn frozen(take: &Take) -> Result<FrozenTake, String> {
    Ok(FrozenTake {
        department: take.department.clone(),
        kind: kind_of(take),
        layer: take.layer().map(str::to_string),
        version: Version::try_from(take.version.clone())?,
        media: take.media.clone(),
    })
}

/// `media` as one take, for tests and fixtures.
pub fn take(department: &str, version: &str, media: Media) -> Take {
    Take { label: format!("{department} {version}"), department: department.into(), version: version.into(), media, multi_range: false }
}

#[cfg(test)]
mod tests {
    use super::*;
    use th_playlist::{Frames, StackLayer};

    fn movie(d: &str, v: &str) -> Take {
        take(d, v, Media::Movie { path: format!("{d}/{v}.mp4") })
    }

    fn exr(d: &str, layer: &str, v: &str) -> Take {
        let frames = Frames {
            directory: format!("{d}/{layer}/{v}"),
            prefix: "beauty.".into(),
            suffix: ".exr".into(),
            padding: 4,
            first: 1001,
            last: 1010,
            step: 1,
        };
        take(d, v, Media::Exr { layer: layer.into(), frames })
    }

    fn stack(d: &str, v: &str) -> Take {
        let layer = |n: &str| StackLayer { name: n.into(), media: Media::Movie { path: format!("{d}/{n}/{v}.mov") } };
        take(d, v, Media::Stack { layers: vec![layer("default"), layer("fx")] })
    }

    fn takes() -> Vec<Take> {
        vec![
            movie("layout", "v0001"),
            movie("layout", "v0002"),
            movie("animation", "v0004"),
            movie("animation", "v0005"),
            stack("animation", "v0005"),
            exr("light", "default", "v0010"),
            exr("light", "fx", "v0011"),
            exr("light", "default", "v0012"),
            exr("denoise", "default", "v0020"),
            exr("denoise", "slapcomp", "v0019"),
        ]
    }

    fn rule(department: Option<&str>, kind: Option<TakeKind>, layer: Option<&str>, version: Option<&str>) -> Rule {
        Rule {
            department: department.map(Into::into),
            kind,
            layer: layer.map(Into::into),
            version: version.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(v.to_string()).unwrap())),
        }
    }

    fn label(takes: &[Take], rule: &Rule) -> Option<String> {
        pick(takes, rule).map(|i| format!("{} {:?} {}", takes[i].department, takes[i].layer(), takes[i].version))
    }

    #[test]
    fn best_available_is_the_newest_render_of_the_most_downstream_department() {
        let t = takes();
        assert_eq!(label(&t, &Rule::best()).unwrap(), r#"denoise Some("slapcomp") v0019"#);
        let playblast = rule(None, Some(TakeKind::Playblast), None, None);
        assert_eq!(label(&t, &playblast).unwrap(), "animation None v0005");
        assert_eq!(label(&t, &rule(Some("light"), None, None, None)).unwrap(), r#"light Some("default") v0012"#);
        assert_eq!(label(&t, &rule(Some("layout"), None, None, Some("v0001"))).unwrap(), "layout None v0001");
        assert_eq!(label(&t, &rule(Some("animation"), None, Some("layers"), None)).unwrap(), r#"animation Some("layers") v0005"#);
        assert_eq!(label(&t[..5], &Rule::best()).unwrap(), "animation None v0005", "no render: the best playblast");
        assert_eq!(label(&t, &rule(Some("comp"), None, None, None)), None);
        assert_eq!(label(&t, &rule(Some("layout"), None, None, Some("v0009"))), None);
        assert_eq!(label(&[], &Rule::best()), None);
    }

    #[test]
    fn a_frozen_take_thawed_picks_itself() {
        let t = takes();
        for (i, take) in t.iter().enumerate() {
            let f = frozen(take).unwrap();
            let pinned =
                Rule { department: Some(f.department), kind: Some(f.kind), layer: f.layer, version: VersionRule::Pinned(f.version) };
            assert_eq!(pick(&t, &pinned), Some(i), "{}", take.label);
        }
    }

    use proptest::prelude::*;

    /// One render department's takes: plain ones as (layer, version), and
    /// its stacks' versions.
    type Department = (Vec<(usize, u32)>, Vec<u32>);

    /// A shot's renders as discovery lists them: the render departments in
    /// pipeline order (render, denoise, composite), each one's plain takes
    /// by layer (the slapcomp last) then version, then its stacks; after
    /// the playblasts. A department is its layers' versions and its stacks'.
    fn discovered(departments: &[Department]) -> Vec<Take> {
        let mut takes = vec![movie("animation", "v0001")];
        for (name, (plain, stacks)) in ["render", "denoise", "composite"].iter().zip(departments) {
            let mut plain = plain.clone();
            plain.sort_by_key(|&(layer, version)| (layer == 2, layer, version));
            plain.dedup();
            for (layer, version) in plain {
                takes.push(exr(name, ["default", "fx", SLAPCOMP][layer], &format!("v{version:04}")));
            }
            let mut stacks = stacks.clone();
            stacks.sort_unstable();
            stacks.dedup();
            takes.extend(stacks.into_iter().map(|v| stack(name, &format!("v{v:04}"))));
        }
        takes
    }

    fn department_strategy() -> impl Strategy<Value = (Vec<(usize, u32)>, Vec<u32>)> {
        (prop::collection::vec((0usize..3, 1u32..30), 0..6), prop::collection::vec(1u32..30, 0..3))
    }

    proptest! {
        /// With a composite version, best available is the composite
        /// department's merged comp, its newest version, whatever the
        /// earlier departments hold and whatever stacks the composite has.
        #[test]
        fn best_available_picks_the_composite_slapcomp_when_there_is_one(
            render in department_strategy(),
            denoise in department_strategy(),
            composite in department_strategy(),
        ) {
            let newest = composite.0.iter().filter(|&&(layer, _)| layer == 2).map(|&(_, v)| v).max();
            let takes = discovered(&[render, denoise, composite.clone()]);
            let picked = pick(&takes, &Rule::best()).map(|i| &takes[i]);
            if let Some(newest) = newest {
                let picked = picked.expect("a composite slapcomp exists");
                prop_assert_eq!(picked.department.as_str(), "composite");
                prop_assert_eq!(picked.layer(), Some(SLAPCOMP));
                prop_assert_eq!(&picked.version, &format!("v{newest:04}"));
            } else if !composite.0.is_empty() {
                // No merged comp: still the composite department, never an
                // earlier one.
                prop_assert_eq!(picked.expect("a composite render exists").department.as_str(), "composite");
            }
        }
    }

    proptest! {
        /// A layer chosen in the take menu (#58): when a take of the rule
        /// fits it, the pick is of that layer and otherwise as the rule
        /// says (the newest of the stream, the pinned version); when none
        /// does (the layer's render not complete), the pick is exactly the
        /// rule's with its layer open, never nothing when that finds one.
        #[test]
        fn a_chosen_layer_plays_when_there_else_the_rule_falls_back(
            render in department_strategy(),
            denoise in department_strategy(),
            composite in department_strategy(),
            layer in prop::sample::select(vec!["default", "fx", SLAPCOMP, "chars", STACK_STREAM]),
            department in prop::option::of(prop::sample::select(vec!["render", "denoise", "composite", "animation"])),
            kind in prop::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]),
            pinned in prop::option::of(1u32..30),
        ) {
            let takes = discovered(&[render, denoise, composite]);
            let version = pinned.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(format!("v{v:04}")).unwrap()));
            let chosen = Rule { department: department.map(Into::into), kind, layer: Some(layer.into()), version };
            let open = Rule { layer: None, ..chosen.clone() };
            let fits = |t: &Take| {
                chosen.department.as_ref().is_none_or(|d| &t.department == d)
                    && chosen.kind.is_none_or(|k| kind_of(t) == k)
                    && t.layer() == Some(layer)
                    && match &chosen.version { VersionRule::Latest => true, VersionRule::Pinned(v) => t.version == v.as_str() }
            };
            let got = pick(&takes, &chosen);
            prop_assert_eq!(got, pick_exactly(&takes, &chosen).or_else(|| pick(&takes, &open)));
            // A version pinned with the department open is looked for in the
            // most downstream department only, as for any rule.
            let decided = chosen.version == VersionRule::Latest || chosen.department.is_some();
            if decided && takes.iter().any(fits) {
                let i = got.expect("a take fits the chosen layer");
                prop_assert_eq!(takes[i].layer(), Some(layer));
                prop_assert!(fits(&takes[i]));
                prop_assert!(!layer_falls_back(&takes, &chosen));
                if chosen.version == VersionRule::Latest {
                    let newest = takes.iter().enumerate().filter(|(_, t)| t.stream() == takes[i].stream()).map(|(j, _)| j).max();
                    prop_assert_eq!(Some(i), newest);
                }
            } else if !takes.iter().any(fits) {
                prop_assert_eq!(got, pick(&takes, &open));
                prop_assert!(layer_falls_back(&takes, &chosen));
            }
            prop_assert!(!layer_falls_back(&takes, &open));
        }
    }

    #[test]
    fn a_chosen_layer_not_complete_yet_shows_best_available() {
        // The issue's case: `default` not complete, `chars` there.
        let t = vec![movie("animation", "v0003"), exr("render", "chars", "v0002"), exr("render", "env", "v0002")];
        assert_eq!(label(&t, &rule(None, None, Some("default"), None)).unwrap(), r#"render Some("chars") v0002"#);
        assert_eq!(label(&t, &rule(None, None, Some("env"), None)).unwrap(), r#"render Some("env") v0002"#);
        assert_eq!(label(&t, &rule(Some("render"), None, Some("chars"), Some("v0009"))), None, "a missing pinned version stays missing");
        assert!(layer_falls_back(&t, &rule(None, None, Some("default"), None)));
    }

    proptest! {
        /// View > Show (#57): as cut, a clip shows what its rule picks.
        /// As renders or playblasts, a shot with a take of that kind shows
        /// one: its cut's take when that is of the kind already, else one
        /// of its rule's department when that has one; a shot with none of
        /// that kind shows its cut's take, said to have none.
        #[test]
        fn show_moves_each_clip_to_its_kind_or_keeps_its_cut(
            takes in mixed(),
            department in prop::option::of(prop::sample::select(vec!["layout", "animation", "lighting", "fx"])),
            kind in prop::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]),
            layer in prop::option::of(prop::sample::select(vec![DEFAULT_LAYER, STACK_STREAM, "fx"])),
            pinned in prop::option::of(prop_oneof![1 => (1u32..20).prop_map(Err), 3 => any::<prop::sample::Index>().prop_map(Ok)]),
            show in prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)],
            chosen in prop::option::of(any::<prop::sample::Index>()),
            pin in any::<bool>(),
        ) {
            // A pinned version: mostly one of the shot's (often an older one), now and then any.
            let pinned = pinned.map(|p| match p {
                Ok(i) if !takes.is_empty() => takes[i.index(takes.len())].version.clone(),
                Ok(_) => "v0001".to_owned(),
                Err(v) => format!("v{v:04}"),
            });
            let version = pinned.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(v).unwrap()));
            let mut rule = Rule { department: department.map(Into::into), kind, layer: layer.map(Into::into), version };
            // Half the time the rule of one of its takes, pinned or not, as
            // the take menu gives a clip.
            if let Some(i) = chosen.filter(|_| !takes.is_empty()) {
                let t = &takes[i.index(takes.len())];
                rule = Rule {
                    department: Some(t.department.clone()),
                    kind: Some(kind_of(t)),
                    layer: t.layer().map(str::to_owned),
                    version: if pin { VersionRule::Pinned(Version::try_from(t.version.clone()).unwrap()) } else { VersionRule::Latest },
                };
            }
            let cut = pick(&takes, &rule);
            prop_assert_eq!(pick_shown(&takes, &rule, None), (cut, false));
            let (got, none) = pick_shown(&takes, &rule, Some(show));
            let of_kind = |t: &Take| kind_of(t) == show;
            if !takes.iter().any(of_kind) {
                prop_assert_eq!((got, none), (cut, true));
                return Ok(());
            }
            prop_assert!(!none);
            let t = got.expect("a shot with a take of the kind shows one");
            prop_assert!(of_kind(&takes[t]));
            if cut.is_some_and(|c| of_kind(&takes[c])) {
                prop_assert_eq!(got, cut);
            } else if let Some(d) = &rule.department
                && takes.iter().any(|t| of_kind(t) && &t.department == d) {
                prop_assert_eq!(&takes[t].department, d);
            }
        }
    }

    /// A shot's takes in discovery's order: playblast movies (a plain movie
    /// per version, a stack of layers on some) then renders, per department
    /// in pipeline order, versions oldest first.
    fn mixed() -> impl Strategy<Value = Vec<Take>> {
        let departments = ["layout", "animation", "lighting"];
        prop::collection::vec((0usize..3, 1u32..20, 0usize..3), 0..12).prop_map(move |picks| {
            let mut picks = picks;
            picks.sort_by_key(|&(d, v, kind)| (kind == 2, d, v, kind));
            picks.dedup_by_key(|p| (p.0, p.1, p.2));
            picks
                .into_iter()
                .map(|(d, v, kind)| {
                    let (d, v) = (departments[d], format!("v{v:04}"));
                    match kind {
                        0 => movie(d, &v),
                        1 => stack(d, &v),
                        _ => exr(d, DEFAULT_LAYER, &v),
                    }
                })
                .collect()
        })
    }

    proptest! {
        /// Under a live playblast shows the department's newest plain
        /// playblast movie; without one, the rule's pick if it is a movie;
        /// never a stack or an EXR render (a live frame is display-referred).
        #[test]
        fn under_a_live_playblast_is_its_departments_latest_playblast(
            takes in mixed(),
            department in prop::sample::select(vec!["layout", "animation", "lighting", "fx"]),
            picked in prop::option::of(0usize..14),
        ) {
            let movie = |t: &Take| matches!(t.media, Media::Movie { .. });
            let newest = takes.iter().enumerate().filter(|(_, t)| t.department == department && movie(t)).map(|(i, _)| i).max_by_key(|&i| &takes[i].version);
            let got = under_live(&takes, department, picked);
            match newest {
                Some(i) => prop_assert_eq!(got, Some(i)),
                None => prop_assert_eq!(got, picked.filter(|&p| takes.get(p).is_some_and(movie))),
            }
            if let Some(i) = got {
                prop_assert!(movie(&takes[i]));
            }
        }
    }

    proptest! {
        /// A Frame List render's movie (#84, never published) shows only
        /// pinned (#86): a rule of the newest version, best available or
        /// not, and View > Show, pick what they would were those movies not
        /// there; a rule pinning a version picks as if none were marked.
        #[test]
        fn a_frame_list_render_shows_only_pinned(
            render in department_strategy(),
            denoise in department_strategy(),
            movies in prop::collection::btree_set(1u32..30, 0..6),
            marked in any::<u64>(),
            department in prop::option::of(prop::sample::select(vec!["render", "denoise", "composite", "animation"])),
            kind in prop::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]),
            layer in prop::option::of(prop::sample::select(vec!["default", "fx", SLAPCOMP, STACK_STREAM])),
            pinned in prop::option::of(1u32..30),
            show in prop::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]),
        ) {
            // The render department's review movies come after the other
            // playblasts, before the renders, as discovery lists them.
            let mut takes = discovered(&[render, denoise, (Vec::new(), Vec::new())]);
            let at = takes.iter().position(|t| kind_of(t) == TakeKind::Render).unwrap_or(takes.len());
            let review: Vec<Take> = movies.iter().map(|v| movie("render", &format!("v{v:04}"))).collect();
            takes.splice(at..at, review);
            let plain = takes.clone();
            for (i, t) in takes.iter_mut().enumerate() {
                t.multi_range = matches!(t.media, Media::Movie { .. }) && t.department == "render" && marked >> (i % 64) & 1 == 1;
            }
            let version = pinned.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(format!("v{v:04}")).unwrap()));
            let rule = Rule { department: department.map(Into::into), kind, layer: layer.map(Into::into), version };
            // Each marked movie is there to be pinned.
            for (i, t) in takes.iter().enumerate().filter(|(_, t)| t.multi_range) {
                let version = VersionRule::Pinned(Version::try_from(t.version.clone()).unwrap());
                let its = Rule { department: Some("render".into()), kind: Some(TakeKind::Playblast), layer: None, version };
                prop_assert_eq!(pick(&takes, &its), Some(i));
            }
            if pinned.is_some() {
                prop_assert_eq!(pick(&takes, &rule), pick(&plain, &rule));
            } else {
                let kept: Vec<usize> = (0..takes.len()).filter(|&i| !takes[i].multi_range).collect();
                let without: Vec<Take> = kept.iter().map(|&i| takes[i].clone()).collect();
                let back = |found: Option<usize>| found.map(|j| kept[j]);
                prop_assert_eq!(pick(&takes, &rule), back(pick(&without, &rule)));
                let (shown, lacks) = pick_shown(&takes, &rule, show);
                let (alone, alone_lacks) = pick_shown(&without, &rule, show);
                prop_assert_eq!((shown, lacks), (back(alone), alone_lacks));
                prop_assert!(shown.is_none_or(|i| !takes[i].multi_range));
            }
        }
    }
}
