//! The file dialogs, wired: typing in a field changes the dialog and never
//! the cut; turning an autosave down (Recover's "Discard", Unsaved's
//! "Don't save") removes it.

use th_pipe::edit_files;

use super::*;

/// An editor on a project of its own on disk, editing working edit `name`
/// (made in it) with an autosave of `edit` there.
fn on_disk(edit: &WorkingEdit, name: &str) -> (tempfile::TempDir, Editor, PathBuf) {
    let (dir, root) = th_pipe::testing::make_project();
    let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
    let project = paths.open().unwrap();
    edit_files::create_edit(&project, name).unwrap();
    edit_files::autosave(&project, name, edit).unwrap();
    let autosave = edit_files::autosave_path(&project, name).unwrap();
    let template = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
    let ocio = template.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
    let colour = Colour::open(&ocio).expect("the template's OCIO config");
    let origin = Origin::Working { name: name.into(), version: None };
    let layout = dir.path().join("layout.json");
    let editor = Editor::with(paths, ocio, Document::new(edit.clone(), origin), colour, Some(edit.clone()), None, Some(layout));
    (dir, editor, autosave)
}

fn slug_cut() -> WorkingEdit {
    let mut edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
    edit.video.push(Track { color: None, name: "V1".into(), clips: vec![VideoClip::new(0, 10, 0, Source::Slug { text: "a".into() })] });
    edit
}

#[test]
fn discarding_a_recovery_removes_the_autosave() {
    let (_dir, mut editor, autosave) = on_disk(&slug_cut(), "cut");
    assert!(matches!(editor.dialog, Some(Dialog::Recover { .. })), "{:?}", editor.dialog);
    assert!(autosave.is_file());
    drop(editor.update(Message::Alternate));
    assert!(!autosave.exists(), "the declined autosave is still there");
    assert!(editor.dialog.is_none() && editor.recovered.is_none());
}

#[test]
fn not_saving_on_close_removes_the_autosave() {
    let (_dir, mut editor, autosave) = on_disk(&slug_cut(), "cut");
    editor.dialog = Some(Dialog::Unsaved);
    drop(editor.update(Message::Alternate));
    assert!(!autosave.exists(), "the unsaved changes' autosave is still there");
    assert!(editor.quit);
}

/// An autosave that cannot be removed (a folder where its file goes), or a
/// project that does not open: turning it down says so, as an error, for
/// it will be offered again.
#[test]
fn an_autosave_not_removed_is_reported() {
    for unsaved in [false, true] {
        for gone in [false, true] {
            let (dir, mut editor, autosave) = on_disk(&slug_cut(), "cut");
            if gone {
                editor.paths.root = dir.path().join("nowhere");
                editor.paths.config = dir.path().join("nowhere/_config");
            } else {
                std::fs::remove_file(&autosave).unwrap();
                std::fs::create_dir(&autosave).unwrap();
            }
            if unsaved {
                editor.dialog = Some(Dialog::Unsaved);
            }
            drop(editor.update(Message::Alternate));
            let status = editor.status.clone();
            assert!(
                status.as_ref().is_some_and(|(words, error)| *error && words.starts_with("The autosave was not removed")),
                "unsaved {unsaved}, gone {gone}: {status:?}"
            );
            assert_eq!(editor.quit, unsaved);
            assert!(editor.dialog.is_none() || unsaved);
        }
    }
}

/// Every dialog a field can be typed into, and the others, as they open.
fn dialogs() -> impl Strategy<Value = Dialog> {
    let text = || "[a-z0-9 ]{0,8}";
    prop_oneof![
        Just(Dialog::OpenEdit { edits: Vec::new(), problem: None }),
        text().prop_map(|note| Dialog::Save { note, warning: None }),
        (text(), text(), any::<bool>()).prop_map(|(name, note, tried)| Dialog::SaveAs { name, note, tried }),
        text().prop_map(|note| Dialog::Publish { note, changes: Vec::new(), problem: None }),
        (text(), any::<bool>()).prop_map(|(name, tried)| Dialog::RenameTrack {
            track: TrackRef::video(0),
            name,
            others: vec!["V2".into()],
            tried
        }),
        (text(), text(), text()).prop_map(|(sequence, name, frames)| Dialog::NewShot {
            sequence,
            name: editing::Offered::new(name),
            frames
        }),
        (text(), text(), text()).prop_map(|(path, item, note)| Dialog::Import { path, item: editing::Offered::new(item), note }),
        Just(Dialog::Recover { name: "cut".into() }),
        Just(Dialog::Unsaved),
        text().prop_map(Dialog::Busy),
    ]
}

fn fields() -> impl Strategy<Value = Field> {
    prop::sample::select(vec![Field::Name, Field::Note, Field::Path, Field::Item, Field::Sequence, Field::Frames])
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// Typing into any field of any dialog leaves the cut, its history and
    /// where it is saved as they were.
    #[test]
    fn typing_in_a_dialog_never_changes_the_cut(
        edit in cut(),
        dialog in dialogs(),
        typed in prop::collection::vec((fields(), "\\PC{0,12}"), 1..6),
    ) {
        let mut bench = Bench::new(&edit, false);
        bench.editor.dialog = Some(dialog);
        let (before, dirty, origin) = (bench.edit().clone(), bench.editor.doc.dirty(), bench.editor.doc.origin.clone());
        for (field, value) in typed {
            bench.send(Message::Text(field, value));
        }
        prop_assert_eq!(bench.edit(), &before);
        prop_assert_eq!(bench.editor.doc.dirty(), dirty);
        prop_assert_eq!(&bench.editor.doc.origin, &origin);
        prop_assert!(!bench.editor.doc.can_undo());
    }
}
