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

use th_playlist::{Kind, Media, STACK_STREAM, Take};

use crate::document::{FrozenTake, Rule, TakeKind, Version, VersionRule};

/// The render layer a best-available render shows first.
pub const SLAPCOMP: &str = "slapcomp";
pub const DEFAULT_LAYER: &str = "default";

/// A take's kind as a rule names it.
pub fn kind_of(take: &Take) -> TakeKind {
    match take.kind() {
        Kind::Movie => TakeKind::Playblast,
        Kind::Exr => TakeKind::Render,
    }
}

/// The take of `takes` that `rule` picks, by index; None when none fits.
pub fn pick(takes: &[Take], rule: &Rule) -> Option<usize> {
    let fits = |t: &Take| {
        rule.department.as_ref().is_none_or(|d| &t.department == d)
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
    Take { label: format!("{department} {version}"), department: department.into(), version: version.into(), media }
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
}
