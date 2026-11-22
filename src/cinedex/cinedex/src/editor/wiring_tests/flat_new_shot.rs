//! A flat project (`flat_shots` on `config:/project`, 2026-11-22) meets
//! Cinedex's New shot: the menu's New Shot…, Convert to Shot… and the
//! Alt+drag all open New shot, which offered "the sequence's next free
//! name" in the sequence of the shot picked in the bins (or the first).
//! A flat project has one hidden sequence and lists its shots by name
//! alone, so the new shot goes into that sequence and its name may be no
//! shot's of any sequence, by casefold: the Asset Browser's rule
//! (`flat_entities.name_taken`, `creation_check`), wired with a project on
//! disk.

use th_pipe::Uri;

use super::*;
use crate::editor::file_dialogs::Dialog;

/// Schemas with a roll, so a shot has a whole frame range.
const SCHEMAS: &str = r#"{"properties":{},"children":{"entity":{"properties":{"roll_start":5,"roll_end":5},"children":{"shots":{"properties":{},"children":{"sequence":{"properties":{},"children":{"shot":{"properties":{},"children":{}}}}}},"edits":{"properties":{},"children":{"edit":{"properties":{},"children":{}}}}}}}}"#;

const SEQUENCES: [&str; 3] = ["SQA", "SQB", "SQC"];

/// A shot already in the project: its sequence, its name, and whether it
/// has a frame range (one without is in no bin, but holds its name).
type Existing = (usize, String, bool);

/// Shot names of a few styles and cases, none a Windows device name.
fn shot_name() -> impl Strategy<Value = String> {
    (prop::sample::select(vec!["SH", "sh", "Sh", "A", "x"]), 1u32..30, prop_oneof![Just(3usize), Just(4)])
        .prop_map(|(prefix, n, width)| format!("{prefix}{:0width$}", n * 10))
}

/// An editor on a project of its own on disk that lists its shots flat in
/// `bucket`, holding the shots `existing`, on an untitled edit `edit`, its
/// bins read as a refresh reads them.
fn flat_project(edit: &WorkingEdit, bucket: &str, existing: &[Existing]) -> (tempfile::TempDir, Editor, PathBuf) {
    let (dir, root) = th_pipe::testing::make_project();
    let db = root.join("_config/db");
    std::fs::write(db.join("schemas.json"), SCHEMAS).unwrap();
    let config =
        format!(r#"{{"properties":{{}},"children":{{"project":{{"properties":{{"fps":24,"flat_shots":"{bucket}"}},"children":{{}}}}}}}}"#);
    std::fs::write(db.join("config.json"), config).unwrap();
    let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
    let project = paths.open().unwrap();
    for (sequence, name, ranged) in existing {
        let sequence = SEQUENCES[*sequence];
        if *ranged {
            th_pipe::entities::create_shot(&project, sequence, name, 1001, 1048).unwrap();
        } else {
            let sequence_uri = Uri::new("entity", ["shots", sequence]);
            if !project.config.exists(&sequence_uri).unwrap() {
                project.config.add_entity(&sequence_uri, Default::default()).unwrap();
            }
            project.config.add_entity(&sequence_uri.join(name), Default::default()).unwrap();
        }
    }
    let template = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
    let ocio = template.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
    let colour = Colour::open(&ocio).expect("the template's OCIO config");
    let layout = dir.path().join("layout.json");
    let mut editor = Editor::with(paths.clone(), ocio, Document::new(edit.clone(), Origin::Untitled), colour, None, None, Some(layout));
    editor.bins = super::super::watch::load_bins(&paths, 24).unwrap();
    (dir, editor, root)
}

/// Every shot on disk, as (sequence, name).
fn shots_on_disk(root: &Path) -> Vec<(String, String)> {
    let project = th_pipe::Project::open(root, root).unwrap();
    let shots = Uri::parse("entity:/shots").unwrap();
    let mut out: Vec<(String, String)> = project
        .config
        .list_entity_uris(&shots, true)
        .unwrap()
        .into_iter()
        .filter(|u| u.segments.len() == 3)
        .map(|u| (u.segments[1].clone(), u.segments[2].clone()))
        .collect();
    out.sort();
    out
}

/// `name` with the case of its letters turned as `flips` says.
fn recased(name: &str, flips: &[bool]) -> String {
    name.chars()
        .zip(flips.iter().cycle())
        .map(|(c, &flip)| {
            if !flip {
                c
            } else if c.is_ascii_uppercase() {
                c.to_ascii_lowercase()
            } else {
                c.to_ascii_uppercase()
            }
        })
        .collect()
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(64))]

    /// On a project that lists its shots flat, New Shot… and Convert to
    /// Shot… offer the implicit sequence and a name no shot of any
    /// sequence has in any case, and show no Sequence field; Create Shot
    /// makes the shot in that sequence (even with another sequence typed),
    /// and puts its clip in the edit. A name some sequence's shot has, in
    /// any case, is refused, and nothing is made.
    #[test]
    fn a_flat_projects_new_shot_goes_in_its_sequence_by_a_name_no_shot_has(
        existing in prop::collection::vec((0usize..3, shot_name(), any::<bool>()), 0..6),
        bucket in prop::sample::select(vec!["SQA", "SQB", "SHOTS"]),
        convert in any::<bool>(),
        clash in prop::option::of((any::<Index>(), prop::collection::vec(any::<bool>(), 1..4))),
        typed_sequence in any::<bool>(),
    ) {
        // One spelling of a name per sequence: the store refuses a case
        // variant beside it.
        let mut seen = std::collections::BTreeSet::new();
        let existing: Vec<Existing> = existing.into_iter().filter(|(s, n, _)| seen.insert((*s, n.to_ascii_lowercase()))).collect();
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![VideoClip::new(0, 24, 0, Source::Slug { text: "panel".into() })], color: None });
        let (_dir, editor, root) = flat_project(&edit, bucket, &existing);
        let mut bench = Bench { editor, prefs: tempfile::tempdir().unwrap() };
        let before = shots_on_disk(&root);

        if convert {
            bench.send(Message::ConvertToShot(ClipRef { track: TrackRef::video(0), index: 0 }));
        } else {
            bench.send(Message::Open(OpenDialog::NewShot));
            bench.send(Message::NewShotNow(true));
        }
        let Some(Dialog::NewShot { sequence, name, .. }) = bench.editor.dialog.clone() else {
            return Err(TestCaseError::fail("no New shot dialog"));
        };
        prop_assert_eq!(&sequence, bucket, "a flat project's new shot is offered another sequence");
        let offered = name.text.clone();
        prop_assert!(
            !before.iter().any(|(_, n)| n.eq_ignore_ascii_case(&offered)),
            "offered {} though a shot has that name: {:?}", offered, before
        );

        if typed_sequence {
            bench.send(Message::Text(Field::Sequence, "SQC".into()));
        }
        let clashing = clash.filter(|_| !before.is_empty()).map(|(which, flips)| recased(&which.get(&before).1, &flips));
        if let Some(typed) = &clashing {
            bench.send(Message::Text(Field::Name, typed.clone()));
        }
        bench.send(Message::Confirm);
        let after = shots_on_disk(&root);
        match &clashing {
            Some(typed) => {
                prop_assert!(bench.refusal().is_some(), "{} was taken though a shot has that name: {:?}", typed, before);
                prop_assert_eq!(&after, &before, "a shot was made");
                prop_assert_eq!(bench.edit(), &edit);
            }
            None => {
                prop_assert_eq!(bench.refusal(), None);
                let mut expected = before.clone();
                expected.push((bucket.to_owned(), offered.clone()));
                expected.sort();
                prop_assert_eq!(&after, &expected, "the new shot is not {}/{}", bucket, offered);
                let uri = format!("entity:/shots/{bucket}/{offered}");
                let placed = bench.edit().video.iter().flat_map(|t| &t.clips).any(|c| matches!(&c.source, Source::Shot { shot, .. } if *shot == uri));
                prop_assert!(placed, "no clip of {}", uri);
            }
        }
    }
}
