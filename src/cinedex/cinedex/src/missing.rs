//! Why a shot's clip has nothing to play, in plain words.
//!
//! A shot clip keeps a rule (`th_edit::takes`), and plays the take the rule
//! picks of those the shot has today. When it picks none the clip is
//! hatched; up to 1.112.0 it said only "No take fits its rule". The reasons
//! a rule picks nothing are few, and [`why`] names the one at hand:
//!
//! - the shot has no take at all ([`NoTake::Nothing`]): no playblast was
//!   published and no render has every frame (a render still going, or one
//!   that stopped short, is not a take: [`unfinished_renders`] counts them);
//! - it has only check renders or Frame List renders
//!   ([`NoTake::OnlyUnpublished`]), which no rule picks by itself;
//! - the rule names a department with no take ([`NoTake::Department`]);
//! - the rule names a kind (render, playblast) the shot has none of
//!   ([`NoTake::Kind`]);
//! - the rule pins a version that is not there ([`NoTake::Version`]).
//!
//! Each has a short label for the clip ([`NoTake::label`]) and a sentence
//! saying what to do about it ([`NoTake::explain`]) for where there is
//! room. A take that is picked but cannot be opened is another matter (the
//! clip says "Cannot play" and why); [`open_failure`] says so in plain words
//! when its file is simply not on disk.

use th_edit::document::{Rule, TakeKind, VersionRule};
use th_edit::takes::{kind_of, pick};
use th_pipe::Project;
use th_playlist::Take;

/// Why a rule picks none of a shot's takes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NoTake {
    /// The shot has no take: nothing published, no render complete.
    Nothing,
    /// Every take it has is a check render's or a Frame List render's:
    /// picked only by a rule pinning its version.
    OnlyUnpublished,
    /// The rule names this department, which has no take to pick.
    Department(String),
    /// The rule names this kind, which the shot (the rule's department,
    /// when it names one) has no take of.
    Kind { kind: TakeKind, department: Option<String> },
    /// The rule pins this version, which is not among the takes.
    Version(String),
}

/// Why `rule` picks none of `takes`; None when it picks one.
pub fn why(takes: &[Take], rule: &Rule) -> Option<NoTake> {
    if pick(takes, rule).is_some() {
        return None;
    }
    if takes.is_empty() {
        return Some(NoTake::Nothing);
    }
    let pinned = match &rule.version {
        VersionRule::Pinned(version) => Some(version.as_str().to_owned()),
        VersionRule::Latest => None,
    };
    // What a rule may pick: an unpublished take only by its version pinned.
    let mut left: Vec<&Take> = takes.iter().filter(|t| pinned.is_some() || t.published()).collect();
    if left.is_empty() {
        return Some(NoTake::OnlyUnpublished);
    }
    if let Some(department) = &rule.department {
        left.retain(|t| &t.department == department);
        if left.is_empty() {
            return Some(NoTake::Department(department.clone()));
        }
    }
    if let Some(kind) = rule.kind {
        left.retain(|t| kind_of(t) == kind);
        if left.is_empty() {
            return Some(NoTake::Kind { kind, department: rule.department.clone() });
        }
    }
    // A rule that pins nothing picks one of what is left (`takes::pick`
    // falls back from a layer it names), so only a pinned version is left
    // to miss.
    Some(NoTake::Version(pinned.unwrap_or_default()))
}

fn kind_word(kind: TakeKind) -> &'static str {
    match kind {
        TakeKind::Render => "render",
        TakeKind::Playblast => "playblast",
    }
}

fn versions(count: usize) -> String {
    if count == 1 { "1 render version has".into() } else { format!("{count} render versions have") }
}

impl NoTake {
    /// What the clip says under its name: a few words. `unfinished` is
    /// [`unfinished_renders`] of the shot.
    pub fn label(&self, unfinished: usize) -> String {
        match self {
            NoTake::Nothing if unfinished > 0 => "Render not finished, no playblast".into(),
            NoTake::Nothing => "No playblast or render yet".into(),
            NoTake::OnlyUnpublished => "Only check renders so far".into(),
            NoTake::Department(department) => format!("Nothing from {department} yet"),
            NoTake::Kind { kind: TakeKind::Render, .. } if unfinished > 0 => "Render not finished".into(),
            NoTake::Kind { kind, department: Some(department) } => format!("No {department} {} yet", kind_word(*kind)),
            NoTake::Kind { kind, department: None } => format!("No {} yet", kind_word(*kind)),
            NoTake::Version(version) => format!("{version} is not there"),
        }
    }

    /// The whole of it, and what to do: for the viewer's card and wherever
    /// there is room for a sentence.
    pub fn explain(&self, unfinished: usize) -> String {
        let rendering = if unfinished > 0 {
            format!(
                " {} frames missing: a render still going, or one that stopped short, shows once every frame is there.",
                versions(unfinished)
            )
        } else {
            String::new()
        };
        match self {
            NoTake::Nothing => format!("Nothing to play yet: no playblast of this shot has been published and it has no finished render.{rendering}"),
            NoTake::OnlyUnpublished => "This shot has only check renders (Farm Submit's Step) or Frame List renders so far, and those are never shown by themselves. Right-click the clip and choose a version to show one.".into(),
            NoTake::Department(department) => format!(
                "This clip is set to show {department}, which has no playblast or finished render of this shot yet. Right-click the clip and choose Best available to show what the shot has."
            ),
            NoTake::Kind { kind, department } => {
                let from = department.as_ref().map(|d| format!(" from {d}")).unwrap_or_default();
                let other = match kind {
                    TakeKind::Render => "playblast",
                    TakeKind::Playblast => "render",
                };
                format!(
                    "This clip is set to show a {}{from}, and the shot has none yet.{rendering} Right-click the clip and choose Best available, or its {other} under Source.",
                    kind_word(*kind)
                )
            }
            NoTake::Version(version) => format!(
                "This clip is pinned to {version}, which is not there: it was removed, or it is a render that no longer has every frame. Right-click the clip and choose another version, or Best available."
            ),
        }
    }
}

/// How many render versions of `shot` are on disk but cannot play: their
/// frames are not all there (still rendering, or stopped short), or they
/// record no frame range. Looked at only for a shot with nothing to play;
/// 0 when it cannot be read.
pub fn unfinished_renders(project: &Project, shot: &th_pipe::Uri) -> usize {
    let Ok(departments) = th_pipe::departments::list_departments(&project.config, "render", false, false) else { return 0 };
    let mut versions = Vec::new();
    for department in departments {
        if let Ok(Some(layers)) = th_pipe::discovery::get_render(project, shot, &department.name) {
            versions.extend(layers.into_iter().flat_map(|(_, versions)| versions));
        }
    }
    versions.iter().filter(|v| v.playable_range().is_none()).count()
}

/// The first file `media` plays from that is not on disk.
fn gone(media: &th_playlist::Media) -> Option<String> {
    use th_playlist::Media;
    match media {
        Media::Movie { path } => (!std::path::Path::new(path).exists()).then(|| path.clone()),
        Media::Exr { frames, .. } => {
            let first = frames.path(0);
            (!first.exists()).then(|| first.display().to_string())
        }
        Media::Stack { layers } => layers.iter().find_map(|l| gone(&l.media)),
        // A live playblast's frames may not be written yet.
        Media::Live { .. } => None,
    }
}

/// What a take that would not open says: that its file is not on disk
/// (moved, deleted, or the share is away) when that is why, else `error`.
pub fn open_failure(media: &th_playlist::Media, error: String) -> String {
    match gone(media) {
        Some(path) => format!("its file is not on disk (moved or deleted, or the share is offline): {path}"),
        None => error,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::Version;
    use th_playlist::{Frames, Media};

    const DEPARTMENTS: [&str; 3] = ["layout", "animation", "light"];

    fn take(department: usize, render: bool, layer: usize, version: u32, check: bool, multi_range: bool) -> Take {
        let (department, version) = (DEPARTMENTS[department], format!("v{version:04}"));
        let media = if render {
            let layer = ["default", "slapcomp", "fx"][layer];
            let frames = Frames {
                directory: format!("{department}/{layer}/{version}"),
                prefix: "beauty.".into(),
                suffix: ".exr".into(),
                padding: 4,
                first: 1001,
                last: 1010,
                step: 1,
            };
            Media::Exr { layer: layer.into(), frames }
        } else {
            Media::Movie { path: format!("{department}/{version}.mp4") }
        };
        Take { check, multi_range, ..th_edit::takes::take(department, &version, media) }
    }

    fn takes() -> impl Strategy<Value = Vec<Take>> {
        // Mostly published takes, sometimes every one a check render's.
        let one = |unpublished: bool| {
            (0usize..3, any::<bool>(), 0usize..3, 1u32..4, any::<bool>(), any::<bool>()).prop_map(
                move |(d, render, layer, version, check, multi)| {
                    let (check, multi) = if unpublished { (true, multi) } else { (check && multi, false) };
                    take(d, render, layer, version, check, multi)
                },
            )
        };
        prop_oneof![prop::collection::vec(one(false), 0..6), prop::collection::vec(one(true), 1..4)]
    }

    fn rule() -> impl Strategy<Value = Rule> {
        (prop::option::of(0usize..3), prop::option::of(any::<bool>()), prop::option::of(0usize..3), prop::option::of(1u32..5)).prop_map(
            |(department, render, layer, version)| Rule {
                department: department.map(|d| DEPARTMENTS[d].to_owned()),
                kind: render.map(|r| if r { TakeKind::Render } else { TakeKind::Playblast }),
                layer: layer.map(|l| ["default", "slapcomp", "fx"][l].to_owned()),
                version: version.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(format!("v{v:04}")).unwrap())),
            },
        )
    }

    proptest! {
        /// A clip has a reason exactly when its rule picks nothing, and the
        /// reason is true of the takes: nothing at all, nothing a rule may
        /// pick by itself, nothing from the department it names, nothing of
        /// the kind it names, or (only then) a pinned version that is not
        /// there. Its words name what is missing and never the old bare
        /// "No take fits its rule".
        #[test]
        fn the_reason_is_the_one_at_hand(takes in takes(), rule in rule(), unfinished in 0usize..3) {
            let reason = why(&takes, &rule);
            prop_assert_eq!(reason.is_none(), pick(&takes, &rule).is_some());
            let Some(reason) = reason else { return Ok(()) };
            let pinned = matches!(rule.version, VersionRule::Pinned(_));
            let pickable: Vec<&Take> = takes.iter().filter(|t| pinned || t.published()).collect();
            // Those of the department the rule names (all, when it names none).
            let here: Vec<&Take> = pickable.iter().copied().filter(|t| rule.department.as_ref().is_none_or(|d| &t.department == d)).collect();
            match &reason {
                NoTake::Nothing => prop_assert!(takes.is_empty()),
                NoTake::OnlyUnpublished => prop_assert!(!takes.is_empty() && !pinned && takes.iter().all(|t| !t.published())),
                NoTake::Department(department) => {
                    prop_assert_eq!(Some(department), rule.department.as_ref());
                    prop_assert!(!pickable.is_empty() && here.is_empty());
                }
                NoTake::Kind { kind, department } => {
                    prop_assert_eq!((Some(*kind), department), (rule.kind, &rule.department));
                    prop_assert!(!here.is_empty() && !here.iter().any(|t| kind_of(t) == *kind));
                }
                NoTake::Version(version) => {
                    let VersionRule::Pinned(pinned) = &rule.version else { return Err(TestCaseError::fail("a version missing from a rule pinning none")) };
                    prop_assert_eq!(version.as_str(), pinned.as_str());
                    prop_assert!(here.iter().any(|t| rule.kind.is_none_or(|k| kind_of(t) == k)));
                }
            }
            let (label, explain) = (reason.label(unfinished), reason.explain(unfinished));
            prop_assert!(!label.is_empty() && label.chars().count() <= 40, "{}", label);
            prop_assert!(explain.len() > label.len());
            prop_assert!(!label.contains("fits its rule") && !explain.contains("fits its rule"));
            match &reason {
                NoTake::Department(named) | NoTake::Version(named) => prop_assert!(label.contains(named) && explain.contains(named)),
                NoTake::Kind { kind, .. } => prop_assert!(explain.contains(kind_word(*kind))),
                NoTake::Nothing | NoTake::OnlyUnpublished => {}
            }
            // A render on its way is said, where a render would have done.
            let awaits_render = matches!(reason, NoTake::Nothing | NoTake::Kind { kind: TakeKind::Render, .. });
            prop_assert_eq!(label.contains("not finished"), awaits_render && unfinished > 0);
        }
    }

    #[test]
    fn the_words_for_each_reason() {
        assert_eq!(NoTake::Nothing.label(0), "No playblast or render yet");
        assert_eq!(NoTake::Nothing.label(2), "Render not finished, no playblast");
        assert!(NoTake::Nothing.explain(1).contains("1 render version has frames missing"));
        assert_eq!(NoTake::OnlyUnpublished.label(0), "Only check renders so far");
        assert_eq!(NoTake::Department("light".into()).label(0), "Nothing from light yet");
        assert_eq!(NoTake::Kind { kind: TakeKind::Render, department: None }.label(0), "No render yet");
        assert_eq!(NoTake::Kind { kind: TakeKind::Playblast, department: Some("layout".into()) }.label(0), "No layout playblast yet");
        assert_eq!(NoTake::Version("v0009".into()).label(0), "v0009 is not there");
    }

    #[test]
    fn a_take_whose_file_is_gone_says_so() {
        let dir = tempfile::tempdir().unwrap();
        let here = dir.path().join("here.mp4");
        std::fs::write(&here, b"x").unwrap();
        let here = Media::Movie { path: here.display().to_string() };
        assert_eq!(open_failure(&here, "not a movie".into()), "not a movie");
        let away = Media::Movie { path: dir.path().join("away.mp4").display().to_string() };
        let said = open_failure(&away, "No such file or directory".into());
        assert!(said.starts_with("its file is not on disk") && said.contains("away.mp4"), "{said}");
    }

    /// A render says so too, by its first frame, and a stack of layers by
    /// the first layer whose file is gone; one whose files are all there
    /// keeps the error it had.
    #[test]
    fn a_render_or_a_layer_whose_file_is_gone_says_so() {
        let dir = tempfile::tempdir().unwrap();
        let exr = |name: &str| Media::Exr {
            layer: "default".into(),
            frames: Frames {
                directory: dir.path().display().to_string(),
                prefix: format!("{name}."),
                suffix: ".exr".into(),
                padding: 4,
                first: 1001,
                last: 1003,
                step: 1,
            },
        };
        std::fs::write(dir.path().join("here.1001.exr"), b"x").unwrap();
        assert_eq!(open_failure(&exr("here"), "not an EXR".into()), "not an EXR");
        let said = open_failure(&exr("away"), "cannot open".into());
        assert!(said.starts_with("its file is not on disk") && said.contains("away.1001.exr"), "{said}");

        let stack = |names: &[&str]| Media::Stack {
            layers: names.iter().map(|name| th_playlist::StackLayer { name: (*name).to_owned(), media: exr(name) }).collect(),
        };
        assert_eq!(open_failure(&stack(&["here", "here"]), "layers differ".into()), "layers differ");
        let said = open_failure(&stack(&["here", "away", "lost"]), "cannot open".into());
        assert!(said.starts_with("its file is not on disk") && said.contains("away.1001.exr") && !said.contains("lost"), "{said}");
    }
}
