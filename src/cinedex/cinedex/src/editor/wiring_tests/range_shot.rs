//! Remy's two timeline RFEs (2026-11-22), wired: an Alt+drag along empty
//! video track offers a new shot of the frames swept (`range_shot`), and
//! trims of the master edit's shot clips set the shots' frames
//! (`shot_sync`), through real pointer events.

use th_pipe::{Uri, master};

use super::*;
use crate::editor::file_dialogs::Dialog;
use crate::editor::range_shot::Place;

const ALT: Modifiers = Modifiers { alt: true, ..Modifiers::NONE };

/// Schemas with a roll, so a shot has a whole frame range.
const SCHEMAS: &str = r#"{"properties":{},"children":{"entity":{"properties":{"roll_start":5,"roll_end":5},"children":{"shots":{"properties":{},"children":{"sequence":{"properties":{},"children":{"shot":{"properties":{},"children":{}}}}}},"edits":{"properties":{},"children":{"edit":{"properties":{},"children":{}}}}}}}}"#;

/// An editor on a project of its own on disk, on working edit `cut` (made
/// there) showing `edit`, with the shots `shots` made (sequence `S`, each
/// name and range), and `cut` the master edit when `master`.
fn on_disk(edit: &WorkingEdit, shots: &[(&str, EditFrame, EditFrame)], master: bool) -> (tempfile::TempDir, Editor, PathBuf) {
    let (dir, root) = th_pipe::testing::make_project();
    std::fs::write(root.join("_config/db/schemas.json"), SCHEMAS).unwrap();
    let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
    let project = paths.open().unwrap();
    th_pipe::edit_files::create_edit(&project, "cut").unwrap();
    for (name, first, last) in shots {
        th_pipe::entities::create_shot(&project, "S", name, *first, *last).unwrap();
    }
    if master {
        master::set_master_edit(&project, Some("cut")).unwrap();
    }
    let template = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
    let ocio = template.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
    let colour = Colour::open(&ocio).expect("the template's OCIO config");
    let origin = Origin::Working { name: "cut".into(), version: None };
    let layout = dir.path().join("layout.json");
    let mut editor = Editor::with(paths, ocio, Document::new(edit.clone(), origin), colour, None, None, Some(layout));
    // The bins as a read of them would have them.
    editor.bins.shots = editing::project_shots(&project).unwrap();
    editor.bins.master = master.then(|| "cut".to_owned());
    (dir, editor, root)
}

/// Shot `S/<name>`'s frame range on disk.
fn range_on_disk(root: &Path, name: &str) -> Option<(EditFrame, EditFrame)> {
    let project = th_pipe::Project::open(root, root).unwrap();
    let uri = Uri::parse(&format!("entity:/shots/S/{name}")).unwrap();
    th_pipe::timeline::get_frame_range(&project.config, &uri).unwrap().map(|r| (r.start_frame, r.end_frame))
}

fn shot_clip(at: EditFrame, duration: EditFrame, source_in: EditFrame, name: &str) -> VideoClip<Source> {
    VideoClip::new(at, duration, source_in, Source::Shot { shot: format!("entity:/shots/S/{name}"), take: th_edit::document::Rule::best() })
}

/// Alt+drag on row `row` from frame `from` to frame `to`, let go.
fn sweep(hand: &mut Hand, editor: &mut Editor, row: usize, from: EditFrame, to: EditFrame) {
    hand.hold(editor, ALT);
    let y = Hand::row_y(editor, row, 0.5);
    hand.press(editor, hand.over(from, y));
    hand.move_to(editor, hand.over(to, y));
    hand.release(editor, hand.over(to, y));
    hand.hold(editor, Modifiers::NONE);
}

/// A frame of `track` of `edit` on no clip, chosen by `pick`, with the
/// frames from it to the end of its gap; None when there is none.
fn empty_frame(edit: &WorkingEdit, track: TrackRef, pick: Index, shown: EditFrame) -> Option<(EditFrame, EditFrame)> {
    let spans = edit.spans(track);
    let free: Vec<EditFrame> = (0..shown).filter(|f| !spans.iter().any(|&(a, e)| a <= *f && *f < e)).collect();
    if free.is_empty() {
        return None;
    }
    let frame = *pick.get(&free);
    Some((frame, spans.iter().map(|s| s.0).filter(|&a| a > frame).min().unwrap_or(EditFrame::MAX)))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// An Alt+drag along empty frames of a video track changes nothing in
    /// the cut and opens New shot over exactly the frames swept (within
    /// their gap), its name the next free one and its frames their count;
    /// Placeholder then lays a slug over exactly those frames on that
    /// track, and Plan Shot a planned shot's clip of them from the
    /// project's start frame, each one undo step.
    #[test]
    fn an_alt_drag_on_empty_track_offers_a_shot_of_its_frames(
        edit in cut(), stage in hand(), track in any::<Index>(), pick in any::<Index>(), along in -30i64..60, plan in any::<bool>(),
    ) {
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        let rows = bench.editor.rows();
        let video: Vec<usize> = (0..rows.len()).filter(|&r| !rows[r].spare && rows[r].track.kind == TrackKind::Video).collect();
        let row = *track.get(&video);
        let track = rows[row].track;
        let shown = bench.editor.timeline_view.shown() as EditFrame;
        let Some((from, gap_end)) = empty_frame(&edit, track, pick, shown) else { return Ok(()) };
        let to = (from + along).clamp(0, shown - 1);
        sweep(&mut hand, &mut bench.editor, row, from, to);
        prop_assert_eq!(bench.edit(), &edit, "a sweep changed the cut");
        let low = edit.spans(track).iter().map(|s| s.1).filter(|&e| e <= from).max().unwrap_or(0);
        let (at, end) = (from.min(to).max(low), from.max(to).min(gap_end - 1) + 1);
        let moved = (to - from).abs() as f32 * hand.px >= 4.0;
        let Some(Dialog::NewShot { place, frames, name, now, .. }) = bench.editor.dialog.clone() else {
            prop_assert!(!moved || bench.editor.dialog.is_some(), "no dialog after a sweep of {} frames", end - at);
            return Ok(());
        };
        prop_assert!(now, "a sweep's shot is made now unless unticked");
        prop_assert_eq!(place, Some(Place { track: track.index, at, frames: end - at, replaces: false }));
        prop_assert_eq!(frames, (end - at).to_string());
        prop_assert_eq!(name.text, "SH010");
        if plan {
            // Unticked, nothing is written: the template stays as it is.
            bench.send(Message::NewShotNow(false));
            bench.send(Message::Text(Field::Sequence, "S".into()));
            bench.send(Message::Confirm);
        } else {
            bench.send(Message::Alternate);
        }
        prop_assert_eq!(bench.refusal(), None);
        prop_assert!(bench.editor.dialog.is_none());
        let laid = &bench.edit().video[track.index];
        let clip = laid.clips.iter().find(|c| c.at == at).expect("the new clip, where it was swept");
        prop_assert_eq!(clip.duration, end - at);
        match &clip.source {
            Source::Shot { shot, .. } if plan => {
                prop_assert_eq!(shot, "entity:/shots/S/SH010");
                prop_assert_eq!(clip.source_in, th_edit::document::DEFAULT_START_FRAME);
            }
            Source::Slug { text } if !plan => prop_assert_eq!(text, "Placeholder"),
            other => prop_assert!(false, "{:?}", other),
        }
        prop_assert_eq!(clips(bench.edit()).len(), clips(&edit).len() + 1);
        bench.send(Message::Undo);
        prop_assert_eq!(bench.edit(), &edit, "one undo step");
    }
}

/// A plain drag on the same empty frames is still the box that picks
/// clips, and an Alt+drag on the spare layer or a read-only edit opens
/// nothing.
#[test]
fn a_plain_drag_still_boxes_and_a_read_only_edit_offers_nothing() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    edit.video.push(Track { name: "V1".into(), clips: vec![VideoClip::new(0, 10, 0, Source::Slug { text: "a".into() })], color: None });
    let mut bench = Bench::new(&edit, false);
    let mut hand = Hand::new(((0, 0), 6));
    let rows = bench.editor.rows();
    let v1 = rows.iter().position(|r| !r.spare).unwrap();
    let y = Hand::row_y(&bench.editor, v1, 0.5);
    hand.press(&mut bench.editor, hand.over(30, y));
    let said = hand.move_to(&mut bench.editor, hand.over(5, y));
    assert!(said.is_some_and(|s| s.starts_with("Tracks(Box(")), "a plain drag is a box");
    hand.release(&mut bench.editor, hand.over(5, y));
    assert!(bench.editor.dialog.is_none());
    // The spare layer: no track to sweep.
    let spare = rows.iter().position(|r| r.spare).unwrap();
    sweep(&mut hand, &mut bench.editor, spare, 2, 20);
    assert!(bench.editor.dialog.is_none());
    let mut read_only = Bench::new(&edit, true);
    let v1 = read_only.editor.rows().iter().position(|r| !r.spare).unwrap();
    sweep(&mut hand, &mut read_only.editor, v1, 20, 40);
    assert!(read_only.editor.dialog.is_none());
    assert_eq!(read_only.edit(), &edit);
}

/// Create Shot makes the shot in the project with the frames swept from
/// the project's start frame, and lays its clip there; an undo takes the
/// clip out and leaves the shot made.
#[test]
fn create_shot_makes_the_shot_with_the_swept_frames() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    edit.video.push(Track { name: "V1".into(), clips: vec![shot_clip(0, 48, 1001, "SH010")], color: None });
    let (_dir, mut editor, root) = on_disk(&edit, &[("SH010", 1001, 1048)], true);
    let mut hand = Hand::new(((0, 0), 4));
    let v1 = editor.rows().iter().position(|r| !r.spare).unwrap();
    sweep(&mut hand, &mut editor, v1, 50, 73);
    let Some(Dialog::NewShot { place, name, sequence, .. }) = editor.dialog.clone() else { panic!("no New shot: {:?}", editor.dialog) };
    assert_eq!(place, Some(Place { track: 0, at: 50, frames: 24, replaces: false }));
    assert_eq!((sequence.as_str(), name.text.as_str()), ("S", "SH020"), "the next shot in the project's naming");
    drop(editor.update(Message::Confirm));
    assert_eq!(editor.status.as_ref().filter(|s| s.1), None);
    assert_eq!(range_on_disk(&root, "SH020"), Some((1001, 1024)));
    let clip = &editor.doc.edit.video[0].clips[1];
    assert_eq!((clip.at, clip.duration, clip.source_in), (50, 24, 1001));
    // The publish has nothing to make or move for it.
    let project = editor.paths.open().unwrap();
    let rows = editor.master_changes(&project, "cut").unwrap();
    assert!(rows.iter().all(|r| r.change.shot().to_string() != "entity:/shots/S/SH020"), "{rows:?}");
    drop(editor.update(Message::Undo));
    assert_eq!(editor.doc.edit, edit);
    assert_eq!(range_on_disk(&root, "SH020"), Some((1001, 1024)), "an undo takes the clip, not the shot");
    // A name taken is refused, the dialog kept, nothing written.
    sweep(&mut hand, &mut editor, v1, 50, 60);
    if let Some(Dialog::NewShot { name, .. }) = &mut editor.dialog {
        name.type_text("SH010".into());
    }
    drop(editor.update(Message::Confirm));
    assert!(editor.dialog.is_some());
    assert_eq!(range_on_disk(&root, "SH010"), Some((1001, 1048)));
}

/// On the master edit, a shot clip's tail trimmed sets the shot's last
/// frame, its head its first (past the frames the shot had, too); an undo
/// and a redo write them back and forth; a slug's trim writes nothing.
#[test]
fn trims_of_the_masters_shot_clips_set_the_shots_frames() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    edit.video.push(Track {
        name: "V1".into(),
        clips: vec![shot_clip(0, 48, 1001, "SH010"), VideoClip::new(60, 10, 0, Source::Slug { text: "end".into() })],
        color: None,
    });
    let (_dir, mut editor, root) = on_disk(&edit, &[("SH010", 1001, 1048)], true);
    assert!(editor.syncing());
    let mut hand = Hand::new(((0, 0), 4));
    let shot = ClipRef { track: TrackRef::video(0), index: 0 };
    hand.drag(&mut editor, shot, Grip::Tail, 10, None);
    assert_eq!(editor.doc.edit.video[0].clips[0].duration, 58, "{:?}", editor.status);
    assert_eq!(range_on_disk(&root, "SH010"), Some((1001, 1058)), "the tail sets the last frame, past the shot's own");
    drop(editor.update(Message::Undo));
    assert_eq!(range_on_disk(&root, "SH010"), Some((1001, 1048)), "the undo writes it back");
    drop(editor.update(Message::Redo));
    assert_eq!(range_on_disk(&root, "SH010"), Some((1001, 1058)));
    hand.drag(&mut editor, shot, Grip::Head, 8, None);
    assert_eq!(range_on_disk(&root, "SH010"), Some((1009, 1058)), "the head sets the first frame");
    let slug = ClipRef { track: TrackRef::video(0), index: 1 };
    let entity = std::fs::read(root.join("_config/db/entity.json")).unwrap();
    hand.drag(&mut editor, slug, Grip::Tail, 5, None);
    assert_eq!(std::fs::read(root.join("_config/db/entity.json")).unwrap(), entity, "a slug's trim wrote the database");
    // Changed elsewhere since the bins read it: refused, said, not written.
    let mut keys = serde_json::Map::new();
    keys.insert("frame_end".into(), serde_json::Value::from(1100));
    th_pipe::Project::open(&root, &root).unwrap().config.set_own_keys(&Uri::parse("entity:/shots/S/SH010").unwrap(), keys).unwrap();
    hand.drag(&mut editor, shot, Grip::Tail, -4, None);
    assert!(editor.status.as_ref().is_some_and(|s| s.1 && s.0.contains("changed elsewhere")), "{:?}", editor.status);
    assert_eq!(range_on_disk(&root, "SH010"), Some((1009, 1100)));
}

/// With Sync Shot Ranges off, or on an edit that is not the master, a
/// shot clip's trim writes nothing, and holds to the frames the shot has.
#[test]
fn no_sync_off_the_master_or_when_turned_off() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    edit.video.push(Track { name: "V1".into(), clips: vec![shot_clip(0, 48, 1001, "SH010")], color: None });
    for master in [false, true] {
        let (_dir, mut editor, root) = on_disk(&edit, &[("SH010", 1001, 1048)], master);
        if master {
            drop(editor.update(Message::SyncRanges(false)));
        }
        assert!(!editor.syncing());
        let entity = std::fs::read(root.join("_config/db/entity.json")).unwrap();
        let mut hand = Hand::new(((0, 0), 4));
        hand.drag(&mut editor, ClipRef { track: TrackRef::video(0), index: 0 }, Grip::Tail, -6, None);
        assert_eq!(editor.doc.edit.video[0].clips[0].duration, 42, "{:?}", editor.status);
        assert_eq!(std::fs::read(root.join("_config/db/entity.json")).unwrap(), entity, "master {master}: written");
        // Out past the roll's 5 frames is refused, as before.
        drop(editor.update(Message::Edit(Op::Trim {
            clip: ClipRef { track: TrackRef::video(0), index: 0 },
            edge: th_edit::ops::Edge::Tail,
            delta: 20,
        })));
        assert_eq!(editor.doc.edit.video[0].clips[0].duration, 42);
    }
}

/// Remy's Convert to Shot (2026-11-22): on an animatic clip it opens New
/// shot over the clip's frames, no Placeholder; Create Shot makes the shot
/// of the clip's length and puts its clip in the animatic's place, keeping
/// the animatic; the shot clip's Swap puts the animatic back where the
/// shot clip is, and an undo the shot clip. A shot clip has no Convert,
/// and a read-only edit neither row.
#[test]
fn convert_to_shot_replaces_the_animatic_and_keeps_it() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    let mut panel = VideoClip::new(48, 30, 0, Source::Slug { text: "panel 2".into() });
    panel.fade_in = 4;
    edit.video.push(Track { name: "V1".into(), clips: vec![shot_clip(0, 48, 1001, "SH010"), panel.clone()], color: None });
    let (_dir, mut editor, root) = on_disk(&edit, &[("SH010", 1001, 1048)], true);
    let (shot, animatic) = (ClipRef { track: TrackRef::video(0), index: 0 }, ClipRef { track: TrackRef::video(0), index: 1 });
    let rows = |editor: &Editor, clip| crate::convert_shot::rows(&editor.doc.edit, clip, !editor.doc.read_only());
    assert!(rows(&editor, shot).is_empty(), "a shot clip has no Convert to Shot");
    assert_eq!(rows(&editor, animatic).len(), 1);
    drop(editor.update(Message::ConvertToShot(animatic)));
    let Some(Dialog::NewShot { place, frames, now, name, .. }) = editor.dialog.clone() else { panic!("no New shot: {:?}", editor.dialog) };
    assert_eq!(place, Some(Place { track: 0, at: 48, frames: 30, replaces: true }));
    assert_eq!((frames.as_str(), now, name.text.as_str()), ("30", true, "SH020"));
    assert_eq!(editor.doc.edit, edit, "opening the dialog changed the cut");
    drop(editor.update(Message::Alternate));
    assert_eq!(editor.doc.edit, edit, "no Placeholder in place of a clip");
    drop(editor.update(Message::Confirm));
    assert_eq!(editor.status.as_ref().filter(|s| s.1), None);
    assert_eq!(range_on_disk(&root, "SH020"), Some((1001, 1030)), "a shot of the clip's length");
    let converted = editor.doc.edit.video[0].clips[1].clone();
    assert_eq!((converted.at, converted.duration, converted.source_in, converted.fade_in), (48, 30, 1001, 4));
    assert!(matches!(&converted.source, Source::Shot { shot, .. } if shot == "entity:/shots/S/SH020"));
    assert_eq!(converted.original.as_deref(), Some(&panel));
    assert_eq!(editor.doc.edit.video[0].clips.len(), 2, "the animatic replaced, not kept beside");
    let labels: Vec<String> = rows(&editor, animatic).into_iter().map(|(l, _)| l).collect();
    assert_eq!(labels, vec!["Swap to Original Clip".to_owned()]);
    // Swap back: the animatic where the shot clip is, keeping the shot clip.
    drop(editor.update(Message::SwapOriginal(animatic)));
    let back = editor.doc.edit.video[0].clips[1].clone();
    assert_eq!(VideoClip { original: None, ..back.clone() }, panel);
    assert!(matches!(back.original.as_deref().map(|o| &o.source), Some(Source::Shot { .. })));
    drop(editor.update(Message::Undo));
    assert_eq!(editor.doc.edit.video[0].clips[1], converted, "an undo puts the shot clip back");
    // Read-only: nothing offered.
    let mut read_only = Bench::new(&edit, true);
    assert!(crate::convert_shot::rows(&read_only.editor.doc.edit, animatic, !read_only.editor.doc.read_only()).is_empty());
    drop(read_only.editor.update(Message::ConvertToShot(animatic)));
    assert!(read_only.editor.dialog.is_none());
}
