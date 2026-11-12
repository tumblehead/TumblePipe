//! Freezing: a working edit with every take rule resolved, as a publish
//! holds it (`designs/edit-entity.md` §2.2).
//!
//! Positions, spans and fades do not change; each shot clip's rule becomes
//! the department, kind, layer and version it picks today, with the media
//! it plays, and each media clip's version becomes the one it plays.
//! [`thaw`] turns a frozen edit back into a working one with every rule
//! pinned to what was frozen, so freezing that again gives the same edit.

use th_playlist::Media;

use crate::document::{FrozenEdit, FrozenSource, FrozenTake, Rule, Source, Version, VersionRule, WorkingEdit};

/// The pipeline's answer to what a rule picks today.
pub trait Resolver {
    /// The take of `shot` that `rule` picks, or none when no take fits (a
    /// planned shot, one with nothing made yet). A rule with every field set
    /// and a pinned version picks exactly that take.
    fn take(&self, shot: &str, rule: &Rule) -> Result<Option<FrozenTake>, String>;
    /// The version of library item `item` that `version` names, and its
    /// media.
    fn media(&self, item: &str, version: &VersionRule) -> Result<(Version, Media), String>;
}

/// `edit` frozen, or the first source that cannot be resolved (a missing
/// item, a pipeline that cannot be read), named. A shot clip with no take
/// that fits is frozen with none.
pub fn freeze(edit: &WorkingEdit, resolver: &impl Resolver) -> Result<FrozenEdit, String> {
    edit.try_map(|source| match source {
        Source::Shot { shot, take } => {
            let frozen = resolver.take(shot, take).map_err(|why| format!("{shot}: {why}"))?;
            Ok(FrozenSource::Shot { shot: shot.clone(), take: frozen })
        }
        Source::Media { item, version } => {
            let (version, media) = resolver.media(item, version).map_err(|why| format!("media {item}: {why}"))?;
            Ok(FrozenSource::Media { item: item.clone(), version, media })
        }
        Source::Slug { text } => Ok(FrozenSource::Slug { text: text.clone() }),
    })
}

/// `edit` as a working edit whose rules pick exactly what it froze (a shot
/// frozen without a take gets the best available, to pick one once there
/// is one).
pub fn thaw(edit: &FrozenEdit) -> WorkingEdit {
    edit.try_map(|source| {
        Ok::<_, ()>(match source {
            FrozenSource::Shot { shot, take: Some(take) } => Source::Shot {
                shot: shot.clone(),
                take: Rule {
                    department: Some(take.department.clone()),
                    kind: Some(take.kind),
                    layer: take.layer.clone(),
                    version: VersionRule::Pinned(take.version.clone()),
                },
            },
            FrozenSource::Shot { shot, take: None } => Source::Shot { shot: shot.clone(), take: Rule::best() },
            FrozenSource::Media { item, version, .. } => {
                Source::Media { item: item.clone(), version: VersionRule::Pinned(version.clone()) }
            }
            FrozenSource::Slug { text } => Source::Slug { text: text.clone() },
        })
    })
    .expect("thawing cannot fail")
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::document::{Clip, TakeKind};
    use crate::ops::tests::edits;
    use proptest::prelude::*;

    /// A resolver for the tests: an open department is `animation`, an open
    /// kind a playblast, `latest` is v0003; media is a movie named after
    /// what it is.
    pub(crate) struct Fixed;

    fn version(rule: &VersionRule) -> Version {
        match rule {
            VersionRule::Latest => Version::try_from("v0003".to_string()).unwrap(),
            VersionRule::Pinned(v) => v.clone(),
        }
    }

    impl Resolver for Fixed {
        fn take(&self, shot: &str, rule: &Rule) -> Result<Option<FrozenTake>, String> {
            if shot.ends_with("/planned") {
                return Ok(None);
            }
            let department = rule.department.clone().unwrap_or_else(|| "animation".into());
            let kind = rule.kind.unwrap_or(TakeKind::Playblast);
            let version = version(&rule.version);
            let path = format!("{shot}/{department}/{}.mov", version.as_str());
            Ok(Some(FrozenTake { department, kind, layer: rule.layer.clone(), version, media: Media::Movie { path } }))
        }
        fn media(&self, item: &str, rule: &VersionRule) -> Result<(Version, Media), String> {
            let v = version(rule);
            let path = format!("media/{item}/{}.mov", v.as_str());
            Ok((v, Media::Movie { path }))
        }
    }

    fn with_shots(edit: WorkingEdit) -> WorkingEdit {
        let mut n = 0;
        edit.try_map(|s| {
            n += 1;
            Ok::<_, ()>(match (s, n % 3) {
                (Source::Slug { .. }, 0) => Source::Shot { shot: format!("entity:/shots/S/{n:03}"), take: Rule::best() },
                (Source::Slug { .. }, 1) => Source::Shot { shot: "entity:/shots/S/planned".into(), take: Rule::best() },
                _ => s.clone(),
            })
        })
        .unwrap()
    }

    proptest! {
        /// Freezing changes no position, span or fade; it is idempotent
        /// through thaw; and the frozen edit round-trips as JSON.
        #[test]
        fn freezing_moves_nothing_and_is_idempotent(edit in edits()) {
            let edit = with_shots(edit);
            let frozen = freeze(&edit, &Fixed).unwrap();
            let spans = |e: &WorkingEdit| e.tracks().map(|t| e.spans(t)).collect::<Vec<_>>();
            prop_assert_eq!(spans(&thaw(&frozen)), spans(&edit));
            for (a, b) in frozen.video.iter().zip(&edit.video) {
                for (x, y) in a.clips.iter().zip(&b.clips) {
                    prop_assert_eq!((x.fade_in(), x.fade_out(), x.source_in()), (y.fade_in(), y.fade_out(), y.source_in()));
                }
            }
            prop_assert_eq!(freeze(&thaw(&frozen), &Fixed).unwrap(), frozen.clone());
            prop_assert_eq!(FrozenEdit::parse(&frozen.to_json()).unwrap(), frozen);
        }
    }

    #[test]
    fn an_unresolvable_source_is_named_and_a_shot_without_a_take_is_frozen_without() {
        struct Nothing;
        impl Resolver for Nothing {
            fn take(&self, shot: &str, _: &Rule) -> Result<Option<FrozenTake>, String> {
                if shot.ends_with("010") { Ok(None) } else { Err("cannot read".into()) }
            }
            fn media(&self, _: &str, _: &VersionRule) -> Result<(Version, Media), String> {
                Err("no item".into())
            }
        }
        let shot = |name: &str| Source::Shot { shot: format!("entity:/shots/S/{name}"), take: Rule::best() };
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(crate::Track { name: "V1".into(), clips: vec![crate::ops::tests::vclip(0, 10, 1001, shot("010"))] });
        let frozen = freeze(&edit, &Nothing).unwrap();
        assert!(matches!(&frozen.video[0].clips[0].source, FrozenSource::Shot { take: None, .. }));
        assert_eq!(thaw(&frozen), edit);
        assert_eq!(FrozenEdit::parse(&frozen.to_json()).unwrap(), frozen);
        edit.video[0].clips.push(crate::ops::tests::vclip(10, 10, 1001, shot("020")));
        assert_eq!(freeze(&edit, &Nothing).unwrap_err(), "entity:/shots/S/020: cannot read");
    }
}
