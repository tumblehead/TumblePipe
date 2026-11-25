//! The window before its edit is in (`editor/opening.rs`), wired: an
//! [`Editor`] on the project template made as `main` makes it, sent what a
//! person at the window sends while the edit is read, then the read's
//! answer as the background hands it over.

use proptest::prelude::*;
use th_edit::document::{Track, VideoClip};

use super::opening::{ConfigText, Loaded, Opening, Request, placeholder, read};
use super::*;

/// The project template's paths and OCIO config.
fn template() -> (ProjectPaths, String) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
    let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
    let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
    (paths, ocio)
}

/// An editor waiting for `request`, its prefs in `prefs`, as
/// [`Editor::opening`] makes one (no speaker here).
fn opening_editor(prefs: &Path, request: Request) -> Editor {
    let (paths, ocio) = template();
    let colour = Colour::raw().expect("OCIO's raw config");
    Editor::with(paths, ocio, placeholder(), colour, None, None, Some(prefs.join("layout.json"))).waiting_for(request)
}

/// A cut of `lengths` slugs on one track, back to back.
fn slugs(lengths: &[i64]) -> WorkingEdit {
    let mut edit = WorkingEdit::new(25, [1920, 1080], "01:00:00:00");
    let mut at = 0;
    let clips = lengths
        .iter()
        .enumerate()
        .map(|(i, &duration)| {
            let clip = VideoClip::new(at, duration, 0, Source::Slug { text: format!("S{i}") });
            at += duration;
            clip
        })
        .collect();
    edit.video.push(Track { name: "V1".into(), clips, color: None });
    edit
}

/// What the background read would hand over for `doc`.
fn loaded(doc: Document, recovered: Option<WorkingEdit>) -> Result<Box<Loaded>, String> {
    let (_, ocio) = template();
    Ok(Box::new(Loaded { doc, recovered, config: ConfigText::read(&ocio).expect("the template's OCIO config") }))
}

/// What a person may press while the edit opens: edits, keys, menus,
/// buttons, the bins' puts and clicks.
fn pressed() -> impl Strategy<Value = Message> {
    prop_oneof![
        Just(Message::SelectAll),
        Just(Message::Split),
        Just(Message::Lift),
        Just(Message::RippleDelete),
        Just(Message::Marker),
        Just(Message::Undo),
        Just(Message::Redo),
        Just(Message::TogglePlay),
        (-30i64..30).prop_map(Message::Step),
        (0usize..200).prop_map(Message::Seek),
        Just(Message::Open(OpenDialog::Save)),
        Just(Message::Open(OpenDialog::SaveAs)),
        Just(Message::Open(OpenDialog::Publish)),
        Just(Message::Open(OpenDialog::NewShot)),
        Just(Message::Open(OpenDialog::Import)),
        Just(Message::Confirm),
        any::<bool>().prop_map(Message::BinPut),
        Just(Message::FitTimeline),
        any::<bool>().prop_map(Message::Flip),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(48))]

    /// While the edit opens, whatever is pressed changes nothing (no
    /// edit, no dialog, no playing) and the window says what it waits for;
    /// once the edit is in it is the one edited, and pressing works.
    #[test]
    fn editing_waits_for_the_edit_then_edits_it(
        presses in prop::collection::vec(pressed(), 1..12),
        lengths in prop::collection::vec(1i64..30, 1..5),
    ) {
        let prefs = tempfile::tempdir().unwrap();
        let mut editor = opening_editor(prefs.path(), Request { edit: Some("entity:/edits/cut".into()), shots: Vec::new() });
        let before = editor.doc.edit.clone();
        for press in presses {
            drop(editor.update(press));
            prop_assert_eq!(&editor.doc.edit, &before);
            prop_assert!(editor.dialog.is_none());
            prop_assert!(!editor.playing);
            let said = editor.status.clone().map(|s| s.0).unwrap_or_default();
            prop_assert!(said.contains("still opening cut"), "{}", said);
        }
        prop_assert_eq!(editor.opening.clone(), Some(Opening::Reading("cut".into())));

        let cut = slugs(&lengths);
        let origin = Origin::Working { name: "cut".into(), version: Some("v0003".into()) };
        drop(editor.update(Message::Loaded(loaded(Document::new(cut.clone(), origin.clone()), None))));
        prop_assert!(editor.opening.is_none());
        prop_assert_eq!(&editor.doc.edit, &cut);
        prop_assert_eq!(&editor.doc.origin, &origin);
        prop_assert!(editor.status.is_none(), "the waiting note stays: {:?}", editor.status);
        // The project's own config shows it, not the raw one held meanwhile.
        prop_assert_eq!(editor.display.colour.default(), Colour::open(&template().1).unwrap().default());

        drop(editor.update(Message::SelectAll));
        prop_assert_eq!(editor.selection.len(), lengths.len());
        drop(editor.update(Message::Lift));
        prop_assert!(editor.doc.edit.video[0].clips.is_empty(), "the edit that came in is not the one edited");
    }
}

/// The first `Start` reads the edit and the bins in the background, and
/// the status line says so; nothing resolves before the edit is in.
#[test]
fn the_window_starts_by_reading() {
    let prefs = tempfile::tempdir().unwrap();
    let mut editor = opening_editor(prefs.path(), Request::default());
    assert_eq!(editor.activity().as_deref(), Some("Opening the project's shots\u{2026}"));
    drop(editor.update(Message::Start));
    assert!(editor.open_request.is_none(), "the read is started once");
    assert_eq!(editor.bins_reading, 1);
    assert!(editor.entries.is_empty());
    assert_eq!(editor.activity().as_deref(), Some("Opening the project's shots \u{b7} Reading the bins\u{2026}"));
    let shot = BinShot { uri: "entity:/shots/010/sh010".into(), sequence: "010".into(), name: "sh010".into(), first: 1001, last: 1010 };
    let mut bins = BinData::default();
    bins.shots.push(shot);
    drop(editor.update(Message::Bins(Ok(bins))));
    assert_eq!(editor.bins_reading, 0);
    // The thumbnails wait for the project's OCIO config, which comes with
    // the edit: through the stand-in one they would fail for good. The
    // shot's row is on screen all along.
    let key = crate::bin_thumbs::ThumbKey::Shot("entity:/shots/010/sh010".into());
    editor.bin_state.thumbs.seen.mark(&key, std::time::Instant::now());
    assert!(editor.bin_thumbs_pump().is_empty(), "a thumbnail is read before the config is in");
    // A second Start (the subscription again) reads nothing again.
    drop(editor.update(Message::Start));
    assert_eq!(editor.bins_reading, 0);
    // The window draws while it waits.
    drop(editor.view(window::Id::MAIN));
    assert!(editor.windows()[0].title.starts_with("Opening the project's shots"));
    let pumped = editor.update(Message::Loaded(loaded(placeholder(), None)));
    assert!(!pumped.is_empty());
    assert!(editor.bin_thumbs_pump().is_empty(), "the edit's arrival did not read the thumbnail on screen");
}

/// The read itself, on the template: every shot (none) when nothing is
/// named, and a named edit the project lacks says so.
#[test]
fn the_read_opens_what_is_asked() {
    let (paths, ocio) = template();
    let all = read(&paths, &ocio, &Request::default()).expect("the template opens");
    assert!(all.doc.filled && all.doc.origin == Origin::Untitled);
    let missing = read(&paths, &ocio, &Request { edit: Some("entity:/edits/nosuchedit".into()), shots: Vec::new() });
    assert!(missing.is_err_and(|e| e.contains("there is no edit nosuchedit")));
}

/// A read that fails leaves a window that says why, edits nothing, and
/// closes.
#[test]
fn a_failed_open_says_why() {
    let prefs = tempfile::tempdir().unwrap();
    let mut editor = opening_editor(prefs.path(), Request { edit: Some("entity:/edits/gone".into()), shots: Vec::new() });
    drop(editor.update(Message::Loaded(Err("there is no edit gone".into()))));
    assert_eq!(editor.opening, Some(Opening::Failed("there is no edit gone".into())));
    assert!(editor.status.as_ref().is_some_and(|(s, error)| *error && s.contains("there is no edit gone")));
    drop(editor.update(Message::Split));
    drop(editor.update(Message::Open(OpenDialog::Save)));
    assert!(editor.dialog.is_none() && editor.doc.edit.video.is_empty());
    drop(editor.view(window::Id::MAIN));
    assert_eq!(editor.windows()[0].title, "Nothing to open - Cinedex");
    drop(editor.update(Message::CloseRequested));
    assert!(editor.quit, "a window with nothing open closes when asked");
}

/// An OCIO config that cannot be read fails the open the same way.
#[test]
fn a_config_that_does_not_parse_fails_the_open() {
    let prefs = tempfile::tempdir().unwrap();
    let mut editor = opening_editor(prefs.path(), Request::default());
    let dir = tempfile::tempdir().unwrap();
    let bad = dir.path().join("bad.ocio");
    std::fs::write(&bad, "not: [an ocio config").unwrap();
    editor.ocio = bad.to_string_lossy().replace('\\', "/");
    let config = ConfigText::read(&editor.ocio).unwrap();
    drop(editor.update(Message::Loaded(Ok(Box::new(Loaded { doc: placeholder(), recovered: None, config })))));
    assert!(matches!(&editor.opening, Some(Opening::Failed(why)) if why.contains("OCIO config")), "{:?}", editor.opening);
}

/// An autosave newer than the edit that came in is offered back, as when
/// the edit was read before the window.
#[test]
fn an_autosave_is_offered_once_the_edit_is_in() {
    let prefs = tempfile::tempdir().unwrap();
    let mut editor = opening_editor(prefs.path(), Request { edit: Some("entity:/edits/cut".into()), shots: Vec::new() });
    let origin = Origin::Working { name: "cut".into(), version: Some("v0001".into()) };
    drop(editor.update(Message::Loaded(loaded(Document::new(slugs(&[10]), origin), Some(slugs(&[10, 5]))))));
    assert!(matches!(&editor.dialog, Some(Dialog::Recover { name }) if name == "cut"));
    drop(editor.update(Message::Confirm));
    assert_eq!(editor.doc.edit, slugs(&[10, 5]));
}

/// What goes on by itself while the edit opens (`opening::waits` lets it
/// through): the polls, the reads' answers, the bins looked over.
fn background() -> impl Strategy<Value = Message> {
    use super::bin_panel::BinMessage;
    use super::review::ReviewMessage;
    prop_oneof![
        Just(Message::Wake),
        Just(Message::Tick),
        Just(Message::PollJournal),
        Just(Message::SharedJournal(Vec::new())),
        Just(Message::PollLive),
        Just(Message::PollSessions),
        Just(Message::Sessions(Ok(Vec::new()))),
        Just(Message::Refresh),
        Just(Message::Autosave),
        Just(Message::Dismiss),
        Just(Message::Board(Err("unread".into()))),
        Just(Message::Review(ReviewMessage::Poll)),
        (0usize..2).prop_map(Message::Mode),
        (0usize..2).prop_map(Message::BinTab),
        any::<bool>().prop_map(|on| Message::Bin(BinMessage::Grid(on))),
        Just(Message::Bin(BinMessage::Hover(None))),
        Just(Message::Bin(BinMessage::Over(None))),
        Just(Message::Bin(BinMessage::Cancel)),
        "[a-z]{1,4}".prop_map(|s| Message::Bin(BinMessage::Fold(s))),
    ]
}

/// An edit and its origin as the background read hands them over, with
/// or without an autosave newer than it.
fn coming() -> impl Strategy<Value = (WorkingEdit, Origin, Option<WorkingEdit>)> {
    let origin = prop_oneof![
        Just(Origin::Untitled),
        (1u32..20).prop_map(|v| Origin::Working { name: "cut".into(), version: Some(format!("v{v:04}")) }),
        Just(Origin::Working { name: "cut".into(), version: None }),
        (1u32..20).prop_map(|v| Origin::Published { name: "cut".into(), version: format!("v{v:04}") }),
    ];
    let lengths = || prop::collection::vec(1i64..30, 0..5);
    (lengths(), origin, proptest::option::of(lengths()))
        .prop_map(|(cut, origin, autosave)| (slugs(&cut), origin, autosave.map(|a| slugs(&a))))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(48))]

    /// Nothing that goes on by itself while the edit opens, mixed with
    /// anything pressed, touches the edit, opens a dialog or plays; the
    /// window keeps waiting, and draws, until the edit is in.
    #[test]
    fn what_goes_on_while_opening_never_edits(
        messages in prop::collection::vec(prop_oneof![3 => background(), 1 => pressed()], 1..30),
    ) {
        let prefs = tempfile::tempdir().unwrap();
        let mut editor = opening_editor(prefs.path(), Request::default());
        drop(editor.update(Message::Start));
        let before = editor.doc.edit.clone();
        for message in messages {
            drop(editor.update(message));
            prop_assert_eq!(&editor.doc.edit, &before);
            prop_assert!(editor.dialog.is_none(), "{:?}", editor.dialog);
            prop_assert!(!editor.playing && !editor.quit);
            prop_assert!(matches!(editor.opening, Some(Opening::Reading(_))));
            drop(editor.view(window::Id::MAIN));
        }
    }

    /// However the bins' reads and their answers interleave (each read
    /// answered once, in any order, read or failed; the edit open or still
    /// opening), the window counts the reads still out and says "Reading
    /// the bins" exactly while one is: never stuck on, never off too soon.
    #[test]
    fn the_bins_count_what_is_still_out(
        opened in any::<bool>(),
        steps in prop::collection::vec((any::<bool>(), any::<bool>()), 0..30),
    ) {
        let prefs = tempfile::tempdir().unwrap();
        let mut editor = opening_editor(prefs.path(), Request::default());
        if opened {
            drop(editor.update(Message::Loaded(loaded(placeholder(), None))));
        }
        let mut out = 0usize;
        for (ask, read) in steps {
            if ask || out == 0 {
                drop(editor.update(Message::Refresh));
                out += 1;
            } else {
                let answer = if read { Ok(BinData::default()) } else { Err("the share is away".into()) };
                drop(editor.update(Message::Bins(answer)));
                out -= 1;
            }
            prop_assert_eq!(editor.bins_reading, out);
            let said = editor.activity().unwrap_or_default();
            prop_assert_eq!(said.contains("Reading the bins"), out > 0, "{}", said);
        }
    }

    /// The edit comes in whenever its read is done: before the window's
    /// Start or after, before the bins or after, among anything else. It is
    /// then the edit, and it edits.
    #[test]
    fn the_edit_comes_in_whenever_it_is_read(
        before in prop::collection::vec(background(), 0..10),
        after in prop::collection::vec(background(), 0..10),
        start_first in any::<bool>(),
        lengths in prop::collection::vec(1i64..30, 1..5),
    ) {
        let prefs = tempfile::tempdir().unwrap();
        let mut editor = opening_editor(prefs.path(), Request { edit: Some("entity:/edits/cut".into()), shots: Vec::new() });
        if start_first {
            drop(editor.update(Message::Start));
        }
        for message in before {
            drop(editor.update(message));
        }
        let cut = slugs(&lengths);
        let origin = Origin::Working { name: "cut".into(), version: Some("v0002".into()) };
        drop(editor.update(Message::Loaded(loaded(Document::new(cut.clone(), origin), None))));
        drop(editor.update(Message::Start));
        for message in after {
            drop(editor.update(message));
        }
        prop_assert!(editor.opening.is_none());
        prop_assert_eq!(&editor.doc.edit, &cut);
        drop(editor.update(Message::Mode(0)));
        drop(editor.update(Message::SelectAll));
        prop_assert_eq!(editor.selection.len(), lengths.len());
    }

    /// A window that opened behind its read ends where a window on the same
    /// edit read before it did: the same edit, origin, title, timeline fit,
    /// colour, and the same autosave offer.
    #[test]
    fn opening_behind_ends_where_opening_before_did((cut, origin, autosave) in coming()) {
        let (paths, ocio) = template();
        let prefs = tempfile::tempdir().unwrap();
        let colour = Colour::open(&ocio).unwrap();
        let doc = Document::new(cut.clone(), origin.clone());
        let mut before = Editor::with(paths, ocio, doc, colour, autosave.clone(), None, Some(prefs.path().join("before.json")));
        drop(before.update(Message::Start));

        let mut behind = opening_editor(prefs.path(), Request::default());
        drop(behind.update(Message::Start));
        drop(behind.update(Message::Loaded(loaded(Document::new(cut, origin), autosave))));

        prop_assert!(behind.opening.is_none());
        prop_assert_eq!(&behind.doc.edit, &before.doc.edit);
        prop_assert_eq!(&behind.doc.origin, &before.doc.origin);
        prop_assert_eq!(behind.doc.dirty(), before.doc.dirty());
        prop_assert_eq!(&behind.windows()[0].title, &before.windows()[0].title);
        prop_assert_eq!(behind.timeline_view, before.timeline_view);
        prop_assert_eq!(behind.entries.len(), before.entries.len());
        prop_assert_eq!(&behind.seen, &before.seen);
        prop_assert_eq!(behind.display.colour.default(), before.display.colour.default());
        prop_assert_eq!((&behind.display.display, &behind.display.view), (&before.display.display, &before.display.view));
        prop_assert_eq!(behind.dialog.is_some(), before.dialog.is_some(), "{:?} vs {:?}", behind.dialog, before.dialog);
        prop_assert_eq!(behind.recovered.is_some(), before.recovered.is_some());
    }
}
