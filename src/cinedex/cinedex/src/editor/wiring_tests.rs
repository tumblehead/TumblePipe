//! The editor's features held to each other, without a window.
//!
//! Each module of the editor has its own tests of its own rules. These are
//! of how they are wired: an [`Editor`] on the project template is sent the
//! messages its widgets send (the tracks' through the same
//! [`Tracks::update`] the window calls, at pixels worked out here from the
//! rows as drawn), and the cut, the rows and the selection are compared
//! with what a person at the timeline would expect, told here in the
//! tests' own words ([`View`], [`clips`]) rather than the editor's.
//!
//! The template has no shots and no media, so every source here has any
//! frame (a slug, a planned shot); what a source's own frames limit is
//! given to a test's editor as a resolve's answer would.

// Heights are whole pixels, compared as written.
#![allow(clippy::float_cmp)]

use std::collections::BTreeMap;

use primer::keyboard::Named;
use primer::widget::custom::{Cursor, Event, keyboard, mouse};
use proptest::prelude::*;
use proptest::sample::Index;
use th_edit::document::{AudioClip, Track, VideoClip};
use th_edit::ops::NewClip;

use super::clip_fields::{ClipField, HINT_NONE, HINT_SELECTED};
use super::*;
use crate::bin_thumbs::{Loaded, Shown, ThumbKey};
use crate::bins::Target;
use crate::tracks::{HEADER, Pointer, RULER, Room};

/// How many cuts each property is tried on: each is an editor of its own
/// (an engine, an OCIO config read), so fewer than a pure function's.
const CASES: u32 = 96;

/// An editor on the project template, with no speaker and its panel sizes
/// kept in a folder of its own.
struct Bench {
    editor: Editor,
    prefs: tempfile::TempDir,
}

impl Bench {
    fn new(edit: &WorkingEdit, read_only: bool) -> Self {
        Self::at(edit, read_only, tempfile::tempdir().expect("a folder for the prefs"))
    }

    /// An editor whose prefs are those left in `prefs`.
    fn at(edit: &WorkingEdit, read_only: bool, prefs: tempfile::TempDir) -> Self {
        assert_eq!(edit.problem(), None, "the test's own cut");
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
        let colour = Colour::open(&ocio).expect("the template's OCIO config");
        let origin = if read_only { Origin::Published { name: "cut".into(), version: "v0001".into() } } else { Origin::Untitled };
        let layout = prefs.path().join("layout.json");
        let editor = Editor::with(paths, ocio, Document::new(edit.clone(), origin), colour, None, None, Some(layout));
        Bench { editor, prefs }
    }

    fn send(&mut self, message: Message) {
        drop(self.editor.update(message));
    }

    fn edit(&self) -> &WorkingEdit {
        &self.editor.doc.edit
    }

    /// What the status line says, when it is an error.
    fn refusal(&self) -> Option<&str> {
        self.editor.status.as_ref().filter(|s| s.1).map(|s| s.0.as_str())
    }

    /// Close the editor and open the same cut again, as the same user.
    fn reopened(self, edit: &WorkingEdit, read_only: bool) -> Self {
        let Bench { editor, prefs } = self;
        drop(editor);
        Self::at(edit, read_only, prefs)
    }
}

/// One track's clips: each the gap before it and its length.
type Spans = Vec<(EditFrame, EditFrame)>;

fn spans() -> impl Strategy<Value = Spans> {
    prop::collection::vec((0i64..24, 1i64..40), 0..4)
}

fn placed(spans: &Spans) -> Vec<(EditFrame, EditFrame)> {
    let mut at = 0;
    spans
        .iter()
        .map(|&(gap, duration)| {
            at += gap;
            let span = (at, duration);
            at += duration;
            span
        })
        .collect()
}

/// A cut of one to three video tracks and up to two audio tracks, every
/// clip a slug named after its track and place ("V2.0"), so a clip is known
/// again wherever an edit leaves it.
fn cut() -> impl Strategy<Value = WorkingEdit> {
    (prop::collection::vec(spans(), 1..4), prop::collection::vec(spans(), 0..3)).prop_map(|(video, audio)| {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        for (t, spans) in video.iter().enumerate() {
            let name = format!("V{}", t + 1);
            let clips = placed(spans)
                .into_iter()
                .enumerate()
                .map(|(i, (at, duration))| VideoClip::new(at, duration, 0, Source::Slug { text: format!("{name}.{i}") }))
                .collect();
            edit.video.push(Track { name, clips });
        }
        for (t, spans) in audio.iter().enumerate() {
            let name = format!("A{}", t + 1);
            let clips = placed(spans)
                .into_iter()
                .enumerate()
                .map(|(i, (at, duration))| AudioClip {
                    at,
                    duration,
                    source_in: 0,
                    source: Source::Slug { text: format!("{name}.{i}") },
                    gain_db: 0.0,
                    fade_in: 0,
                    fade_out: 0,
                })
                .collect();
            edit.audio.push(Track { name, clips });
        }
        edit
    })
}

/// What a clip plays, as the tests name it.
fn named(source: &Source) -> String {
    match source {
        Source::Slug { text } => text.clone(),
        Source::Shot { shot, .. } => shot.clone(),
        Source::Media { item, .. } => item.clone(),
    }
}

/// A clip as the tests know it: what it plays, its track, where it is and
/// how long.
type Known = (String, TrackRef, EditFrame, EditFrame);

/// Every clip of `edit`, track by track, in order.
fn clips(edit: &WorkingEdit) -> Vec<Known> {
    let mut out = Vec::new();
    for track in edit.tracks() {
        let names: Vec<String> = match track.kind {
            TrackKind::Video => edit.video[track.index].clips.iter().map(|c| named(&c.source)).collect(),
            TrackKind::Audio => edit.audio[track.index].clips.iter().map(|c| named(&c.source)).collect(),
        };
        for (name, (at, end)) in names.into_iter().zip(edit.spans(track)) {
            out.push((name, track, at, end - at));
        }
    }
    out
}

/// Every clip of `edit` by track and index.
fn clip_refs(edit: &WorkingEdit, tracks: &[TrackRef]) -> Vec<ClipRef> {
    tracks.iter().flat_map(|&track| (0..edit.spans(track).len()).map(move |index| ClipRef { track, index })).collect()
}

/// What the timeline shows, kept by the test as a person would say it: the
/// track soloed, whether the audio is hidden, and the height each row was
/// last dragged to.
#[derive(Debug, Clone, Default)]
struct View {
    solo: Option<TrackRef>,
    audio_hidden: bool,
    heights: BTreeMap<(bool, usize), f32>,
}

impl View {
    /// The tracks with a row, top first: the soloed one alone, else the
    /// video tracks from the top one down and then, unless hidden, the
    /// audio tracks.
    fn rows(&self, edit: &WorkingEdit) -> Vec<TrackRef> {
        if let Some(solo) = self.solo {
            return vec![solo];
        }
        let audio = if self.audio_hidden { 0 } else { edit.audio.len() };
        (0..edit.video.len()).rev().map(TrackRef::video).chain((0..audio).map(TrackRef::audio)).collect()
    }

    fn height(&self, track: TrackRef) -> f32 {
        self.heights.get(&(track.kind == TrackKind::Video, track.index)).copied().unwrap_or(46.0)
    }
}

/// The editor's rows are `view`'s of its cut: a row for each track shown,
/// in order, with that track's clips, at the height it was given; over them
/// the spare layer exactly when the cut can change, no track is soloed and
/// the top video track is used, and it is no track of the cut. No clip
/// without a row is selected, and the line under the rows is there exactly
/// when a track has none.
fn check_rows(bench: &Bench, view: &View, writable: bool) -> Result<(), TestCaseError> {
    let edit = bench.edit();
    let rows = bench.editor.rows();
    let expected = view.rows(edit);
    let spare = writable && view.solo.is_none() && edit.video.last().is_none_or(|t| !t.clips.is_empty());
    prop_assert_eq!(rows.iter().filter(|r| r.spare).count(), usize::from(spare), "the spare layer");
    prop_assert_eq!(rows.first().is_some_and(|r| r.spare), spare, "the spare layer is the top row");
    let tracks: Vec<TrackRef> = rows.iter().filter(|r| !r.spare).map(|r| r.track).collect();
    prop_assert_eq!(&tracks, &expected);
    for row in &rows {
        prop_assert_eq!(row.height, view.height(row.track), "{:?}'s height", row.track);
        if row.spare {
            prop_assert_eq!(row.track, TrackRef::video(edit.video.len()));
            prop_assert!(row.clips.is_empty() && !row.solo);
        } else {
            let spans: Vec<(EditFrame, EditFrame)> = row.clips.iter().map(|c| (c.at, c.at + c.duration)).collect();
            prop_assert_eq!(spans, edit.spans(row.track), "{:?}'s clips", row.track);
            prop_assert_eq!(row.solo, view.solo == Some(row.track));
            prop_assert_eq!(&row.name, edit.track_name(row.track));
        }
    }
    for clip in &bench.editor.selection {
        prop_assert!(expected.contains(&clip.track), "{:?} is selected and has no row", clip);
        prop_assert!(clip.index < edit.spans(clip.track).len(), "{:?} is selected and is no clip", clip);
    }
    prop_assert_eq!(bench.editor.tracks_note().is_some(), expected.len() < edit.video.len() + edit.audio.len());
    Ok(())
}

/// Something done to the view.
#[derive(Debug, Clone)]
enum Look {
    Solo(Index),
    HideAudio(bool),
    ShowAll,
    Height(Index, f32),
    SelectAll,
    Click(Index, bool),
    ClickOff,
    Fit,
}

/// What changes which tracks have a row, and how tall.
fn focus_look() -> impl Strategy<Value = Look> {
    prop_oneof![
        3 => any::<Index>().prop_map(Look::Solo),
        3 => any::<bool>().prop_map(Look::HideAudio),
        1 => Just(Look::ShowAll),
        3 => (any::<Index>(), -40f32..400.0).prop_map(|(track, height)| Look::Height(track, height)),
    ]
}

fn look() -> impl Strategy<Value = Look> {
    prop_oneof![
        6 => focus_look(),
        2 => Just(Look::SelectAll),
        3 => (any::<Index>(), any::<bool>()).prop_map(|(clip, additive)| Look::Click(clip, additive)),
        1 => Just(Look::ClickOff),
        1 => Just(Look::Fit),
    ]
}

/// Do `look` in the editor, and say in `view` what it should have done.
fn do_look(bench: &mut Bench, view: &mut View, look: &Look) -> Result<(), TestCaseError> {
    let all: Vec<TrackRef> = bench.edit().tracks().collect();
    match look {
        Look::Solo(track) => {
            let track = *track.get(&all);
            bench.send(Message::Solo(track));
            view.solo = if view.solo == Some(track) { None } else { Some(track) };
        }
        Look::HideAudio(hidden) => {
            bench.send(Message::HideAudio(*hidden));
            view.audio_hidden = *hidden;
        }
        Look::ShowAll => {
            bench.send(Message::ShowAllTracks);
            (view.solo, view.audio_hidden) = (None, false);
        }
        Look::Height(track, height) => {
            // Any track, and the spare layer's (one past the top).
            let mut rows = all.clone();
            rows.push(TrackRef::video(bench.edit().video.len()));
            let track = *track.get(&rows);
            bench.send(Message::RowHeight(track, *height));
            view.heights.insert((track.kind == TrackKind::Video, track.index), height.round().clamp(24.0, 240.0));
        }
        Look::SelectAll => {
            bench.send(Message::SelectAll);
            let shown: BTreeSet<ClipRef> = clip_refs(bench.edit(), &view.rows(bench.edit())).into_iter().collect();
            prop_assert_eq!(&bench.editor.selection, &shown, "Select All takes every clip with a row, and no other");
        }
        Look::Click(clip, additive) => {
            let shown = clip_refs(bench.edit(), &view.rows(bench.edit()));
            if !shown.is_empty() {
                bench.send(Message::Select(Some(*clip.get(&shown)), *additive, 0));
            }
        }
        Look::ClickOff => bench.send(Message::Select(None, false, 0)),
        Look::Fit => bench.send(Message::FitTimeline),
    }
    Ok(())
}

/// The tracks as the window lays them out: their top-left anywhere (they
/// scroll), a whole number of pixels a frame.
struct Hand {
    pointer: Pointer,
    origin: (f32, f32),
    px: f32,
    now: Instant,
}

fn hand() -> impl Strategy<Value = ((i16, i16), u8)> {
    ((-300i16..300, -300i16..300), 4u8..9)
}

impl Hand {
    fn new(((x, y), px): ((i16, i16), u8)) -> Self {
        Hand { pointer: Pointer::default(), origin: (f32::from(x), f32::from(y)), px: f32::from(px), now: Instant::now() }
    }

    /// The window point over the middle of `frame`, `y` pixels below the
    /// tracks' top.
    fn over(&self, frame: EditFrame, y: f32) -> Point {
        self.at(HEADER + self.px * (frame as f32 + 0.5), y)
    }

    /// The window point `x`, `y` pixels from the tracks' top-left.
    fn at(&self, x: f32, y: f32) -> Point {
        Point::new(self.origin.0 + x, self.origin.1 + y)
    }

    /// `within` (0 to 1) of the way down row `row` of the editor's rows:
    /// under the ruler and the rows above.
    fn row_y(editor: &Editor, row: usize, within: f32) -> f32 {
        let rows = editor.rows();
        RULER + rows[..row].iter().map(|r| r.height).sum::<f32>() + within * rows[row].height
    }

    /// The tracks take `event` with the pointer at `at`, and the editor
    /// what they send for it: the message, as it prints.
    fn event(&mut self, editor: &mut Editor, event: Event, at: Point) -> Option<String> {
        let program = editor.tracks_program();
        let bounds =
            Rectangle { x: self.origin.0, y: self.origin.1, width: HEADER + self.px * program.total as f32, height: program.height() };
        let (_, message) = program.update(&mut self.pointer, &event, bounds, Cursor::Available(at), self.now);
        let said = message.as_ref().map(|m| format!("{m:?}"));
        if let Some(message) = message {
            drop(editor.update(message));
        }
        said
    }

    fn press(&mut self, editor: &mut Editor, at: Point) -> Option<String> {
        self.event(editor, Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), at)
    }

    fn right_press(&mut self, editor: &mut Editor, at: Point) -> Option<String> {
        self.event(editor, Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)), at)
    }

    fn move_to(&mut self, editor: &mut Editor, at: Point) -> Option<String> {
        self.event(editor, Event::Mouse(mouse::Event::CursorMoved { position: at }), at)
    }

    fn release(&mut self, editor: &mut Editor, at: Point) -> Option<String> {
        self.event(editor, Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), at)
    }

    fn hold_alt(&mut self, editor: &mut Editor, alt: bool) {
        let modifiers = Modifiers { alt, ..Modifiers::NONE };
        self.event(editor, Event::Keyboard(keyboard::Event::ModifiersChanged(modifiers)), self.at(0.0, 0.0));
    }

    /// Where a press takes `grip` of `clip`: a pixel inside its head or
    /// its tail, or its middle, half way down its row.
    fn grip_point(&self, editor: &Editor, clip: ClipRef, grip: Grip) -> Point {
        let rows = editor.rows();
        let row = rows.iter().position(|r| !r.spare && r.track == clip.track).expect("the clip's track has a row");
        let (at, end) = editor.doc.edit.spans(clip.track)[clip.index];
        let x = match grip {
            Grip::Head | Grip::RollHead => HEADER + self.px * at as f32 + 0.5,
            Grip::Tail | Grip::RollTail => HEADER + self.px * end as f32 - 1.0,
            Grip::Body | Grip::Slip => HEADER + self.px * (at + end) as f32 / 2.0,
        };
        self.at(x, Self::row_y(editor, row, 0.5))
    }

    /// Press `grip` of `clip`, drag it `dx` frames along (to `row_y` pixels
    /// below the tracks' top when given, for a clip moved to another
    /// row), and let go.
    fn drag(&mut self, editor: &mut Editor, clip: ClipRef, grip: Grip, dx: EditFrame, row_y: Option<f32>) {
        let alt = matches!(grip, Grip::RollHead | Grip::RollTail | Grip::Slip);
        self.hold_alt(editor, alt);
        let from = self.grip_point(editor, clip, grip);
        self.press(editor, from);
        let to = Point::new(from.x + self.px * dx as f32, row_y.map_or(from.y, |y| self.origin.1 + y));
        self.move_to(editor, to);
        self.release(editor, to);
        self.hold_alt(editor, false);
    }
}

/// The part of a clip a press takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Grip {
    Head,
    Tail,
    RollHead,
    RollTail,
    Slip,
    Body,
}

fn grip() -> impl Strategy<Value = Grip> {
    prop_oneof![
        3 => Just(Grip::Head),
        3 => Just(Grip::Tail),
        1 => Just(Grip::RollHead),
        1 => Just(Grip::RollTail),
        1 => Just(Grip::Slip),
        2 => Just(Grip::Body),
    ]
}

/// What an edit of every track does to a clip that is not the one edited:
/// frames `from .. to` taken out of the cut (`to` before `from`: that many
/// put in at `to`). Its new place, or None when the ripple cuts into it
/// (the document's rule: a clip across the place is split, one under frames
/// taken out loses them).
fn rippled((at, duration): (EditFrame, EditFrame), from: EditFrame, to: EditFrame) -> Option<(EditFrame, EditFrame)> {
    let end = at + duration;
    if to >= from {
        // Frames taken out.
        if end <= from {
            Some((at, duration))
        } else if at >= to {
            Some((at - (to - from), duration))
        } else {
            None
        }
    } else {
        // Frames put in at `to`.
        if end <= to {
            Some((at, duration))
        } else if at >= to {
            Some((at + (from - to), duration))
        } else {
            None
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// Solo, Hide Audio, Show All Tracks, a row's height, the selection
    /// and Fit Timeline are the view's alone, on a cut that can change and
    /// on a published one: whatever is done, in any order, the cut is as
    /// it was (nothing to undo, nothing to save), the rows are the tracks
    /// the view shows with each track's own clips and its own height (a
    /// height stays with its track when rows above it come and go), and no
    /// clip without a row is selected, Select All's included. The heights
    /// and the hidden audio are this user's next time; a solo is not.
    #[test]
    fn the_view_never_changes_the_cut(edit in cut(), looks in prop::collection::vec(look(), 1..12), read_only in any::<bool>()) {
        let mut bench = Bench::new(&edit, read_only);
        let mut view = View::default();
        check_rows(&bench, &view, !read_only)?;
        for look in &looks {
            do_look(&mut bench, &mut view, look)?;
            check_rows(&bench, &view, !read_only)?;
            prop_assert_eq!(bench.edit(), &edit, "{:?} changed the cut", look);
            prop_assert!(!bench.editor.doc.dirty() && !bench.editor.doc.can_undo());
        }
        let again = bench.reopened(&edit, read_only);
        check_rows(&again, &View { solo: None, ..view }, !read_only)?;
    }

    /// A solo does not outlive its track: undo the track's making and
    /// every track shows again, and the track made anew is not soloed.
    #[test]
    fn a_solo_goes_with_its_track(edit in cut(), audio in any::<bool>(), looks in prop::collection::vec(focus_look(), 0..3)) {
        let mut bench = Bench::new(&edit, false);
        let mut view = View::default();
        for look in &looks {
            do_look(&mut bench, &mut view, look)?;
        }
        let (kind, name) = if audio { (TrackKind::Audio, "A9") } else { (TrackKind::Video, "V9") };
        bench.send(Message::Edit(Op::AddTrack { kind, name: name.into() }));
        prop_assert_eq!(bench.refusal(), None);
        let track = TrackRef { kind, index: bench.edit().track_count(kind) - 1 };
        bench.send(Message::Solo(track));
        view.solo = Some(track);
        check_rows(&bench, &view, true)?;
        bench.send(Message::Undo);
        view.solo = None;
        prop_assert_eq!(bench.edit(), &edit);
        check_rows(&bench, &view, true)?;
        bench.send(Message::Redo);
        check_rows(&bench, &view, true)?;
    }

    /// A right-click on the timeline names what is drawn under it, however
    /// tall the rows are, whichever have a row, wherever the tracks are
    /// scrolled to and however wide a frame is: a clip to its take menu,
    /// else the frame and the row's track to the timeline's menu (the
    /// ruler and the spare layer name no track). The menu then offers a
    /// published cut nothing that changes it, and Remove Track only for a
    /// track with no clip that is not the last of its kind; and whichever
    /// row is chosen does what it says: one that acts "here" parks the
    /// playhead on the frame clicked (the cut's end, past it) and acts
    /// there, as one undo step; a view's row leaves the cut alone.
    #[test]
    fn a_right_click_names_what_is_under_it_and_its_menu_does_what_it_says(
        edit in cut(), looks in prop::collection::vec(focus_look(), 0..5), read_only in any::<bool>(), stage in hand(),
        row in any::<Index>(), frame in any::<Index>(), within in 0.05f32..0.95, place in 0u8..8,
        mark in 0u8..4, picked in any::<bool>(), choice in any::<Index>(),
    ) {
        let mut bench = Bench::new(&edit, read_only);
        let mut view = View::default();
        for look in &looks {
            do_look(&mut bench, &mut view, look)?;
        }
        let mut hand = Hand::new(stage);
        let total = bench.editor.tracks_program().total;
        let frame = frame.index(total);
        // A marker already on the frame, or on the cut's end.
        let end = edit.length() as usize;
        match mark {
            0 => bench.send(Message::At(frame, Box::new(Message::Marker))),
            1 => bench.send(Message::At(end, Box::new(Message::Marker))),
            _ => {}
        }
        if picked {
            bench.editor.bins.shots = vec![BinShot { uri: "entity:/shots/A/010".into(), sequence: "A".into(), name: "010".into(), first: 1001, last: 1012 }];
            bench.send(Message::BinPick(0));
        }
        let before = bench.edit().clone();
        let end = before.length() as usize;
        let rows = bench.editor.rows();
        let r = row.index(rows.len());
        let (y, track) = match place {
            // The ruler.
            0 => (within * RULER, None),
            _ => (Hand::row_y(&bench.editor, r, within), (!rows[r].spare).then_some(rows[r].track)),
        };
        // On a track's name there is no frame.
        let (point, at) = if place == 1 { (hand.at(within * HEADER, y), None) } else { (hand.over(frame as EditFrame, y), Some(frame)) };
        let said = hand.right_press(&mut bench.editor, point);
        let under = track.filter(|_| at.is_some()).and_then(|track| {
            let index = before.spans(track).iter().position(|&(from, to)| from <= frame as EditFrame && (frame as EditFrame) < to)?;
            Some(ClipRef { track, index })
        });
        if let Some(clip) = under {
            prop_assert_eq!(said, Some(format!("{:?}", Message::ClipMenu(clip))));
            return Ok(());
        }
        prop_assert_eq!(said, Some(format!("{:?}", Message::TimelineMenu(at, track))));
        prop_assert_eq!(bench.edit(), &before, "a right-click changed the cut");

        let (clicked, on) = bench.editor.menu_empty.expect("the menu is for that click");
        let entries = timeline_menu::entries(&bench.editor.timeline_menu_state(clicked, on));
        let labels: Vec<&str> = entries.iter().map(|(label, _)| label.as_str()).collect();
        let changes = |message: &Message| matches!(message, Message::Edit(_) | Message::At(..));
        if read_only {
            prop_assert!(!entries.iter().any(|(_, m)| changes(m)), "a published cut is offered {:?}", labels);
        }
        prop_assert_eq!(labels.iter().any(|l| l.contains("Here")), !read_only && at.is_some(), "{:?}", labels);
        let removable = track.is_some_and(|t| before.spans(t).is_empty() && before.track_count(t.kind) > 1);
        prop_assert_eq!(labels.iter().any(|l| l.starts_with("Remove Track")), !read_only && removable, "{:?}", labels);

        let (label, message) = choice.get(&entries).clone();
        let changing = changes(&message);
        bench.send(message);
        let after = bench.edit().clone();
        let here = at.map(|frame| frame.min(end));
        if label.contains("Bin Pick Here") {
            // The playhead goes on to the end of what was put, as Insert's does.
            prop_assert_eq!(Some(bench.editor.position), here.map(|at| at + 12), "{} is not where the click was", label);
        } else if label.contains("Here") {
            prop_assert_eq!(Some(bench.editor.position), here, "{} is not where the click was", label);
        }
        let known = |edit: &WorkingEdit| clips(edit);
        let marks = |edit: &WorkingEdit| edit.markers.iter().map(|m| m.at).collect::<Vec<_>>();
        match label.as_str() {
            "Add Marker Here" => {
                let mut expected = marks(&before);
                expected.push(here.unwrap() as EditFrame);
                expected.sort_unstable();
                let mut got = marks(&after);
                got.sort_unstable();
                prop_assert_eq!(got, expected, "{:?}", bench.refusal());
                prop_assert_eq!(known(&after), known(&before));
            }
            "Remove Marker Here" => {
                let mut expected = marks(&before);
                let gone = expected.iter().position(|&m| m == here.unwrap() as EditFrame);
                prop_assert!(gone.is_some(), "no marker is there to remove");
                expected.remove(gone.unwrap());
                prop_assert_eq!(marks(&after), expected, "{:?}", bench.refusal());
            }
            "New Shot Here\u{2026}" => {
                prop_assert!(bench.editor.dialog.is_some());
                prop_assert_eq!(&after, &before);
            }
            "Insert Bin Pick Here" | "Overlay Bin Pick Here" => {
                let new: Vec<Known> = known(&after).into_iter().filter(|c| c.0.starts_with("entity:")).collect();
                prop_assert_eq!(new.len(), 1, "{:?}", bench.refusal());
                prop_assert_eq!((new[0].2, new[0].3), (here.unwrap() as EditFrame, 12));
                if label.starts_with("Overlay") {
                    let old: Vec<Known> = known(&after).into_iter().filter(|c| !c.0.starts_with("entity:")).collect();
                    prop_assert_eq!(old, known(&before), "an overlay moved what was there");
                }
            }
            "Add Video Track" | "Add Audio Track" => {
                let kind = if label.contains("Video") { TrackKind::Video } else { TrackKind::Audio };
                prop_assert_eq!(after.track_count(kind), before.track_count(kind) + 1, "{:?}", bench.refusal());
                let top = TrackRef { kind, index: before.track_count(kind) };
                prop_assert!(after.spans(top).is_empty());
                prop_assert_eq!(known(&after), known(&before));
            }
            l if l.starts_with("Remove Track") => {
                let gone = track.unwrap();
                prop_assert!(l.ends_with(before.track_name(gone)));
                prop_assert_eq!(after.track_count(gone.kind), before.track_count(gone.kind) - 1, "{:?}", bench.refusal());
                prop_assert_eq!(known(&after).len(), known(&before).len(), "Remove Track took clips with it");
            }
            _ => {
                prop_assert!(!changing, "{} is not known to this test", label);
                prop_assert_eq!(&after, &before, "{} changed the cut", label);
            }
        }
        if changing && label != "New Shot Here\u{2026}" {
            prop_assert_eq!(bench.refusal(), None);
            prop_assert!(after != before, "{} did nothing", label);
            // One step: one undo is the cut as it was.
            bench.send(Message::Undo);
            prop_assert_eq!(bench.edit(), &before, "{} was not one undo step", label);
        }
    }

    /// A drag of a clip's edge, its body, a slip or a roll, on any cut and
    /// whichever tracks have a row, the playhead anywhere: an edge or a
    /// slip is always taken (never "Not done"); a roll is taken when the
    /// two clips touch, and a move when its frames are free on that track;
    /// and a drag that is refused changes nothing at all. No drag takes a
    /// clip away or changes the order of a track's clips: every clip is
    /// there after, the dragged one as long as the drag made it (a frame
    /// at least), the others of its track only moved along by a trim's
    /// ripple. On the other tracks a trim's ripple moves what is after it;
    /// only a clip the ripple cuts into is not held to its place here.
    #[test]
    fn no_drag_takes_a_clip_away(
        edit in cut(), looks in prop::collection::vec(focus_look(), 0..3), stage in hand(),
        pick in any::<Index>(), grip in grip(), dx in -70i64..70, playhead in any::<Index>(),
    ) {
        let mut bench = Bench::new(&edit, false);
        let mut view = View::default();
        for look in &looks {
            do_look(&mut bench, &mut view, look)?;
        }
        let shown = clip_refs(&edit, &view.rows(&edit));
        prop_assume!(!shown.is_empty());
        let clip = *pick.get(&shown);
        bench.send(Message::Seek(playhead.index(edit.length() as usize + 1)));
        let (at, end) = edit.spans(clip.track)[clip.index];
        let duration = end - at;
        let own = edit.spans(clip.track);
        let before = clips(&edit);
        let name = before.iter().find(|c| c.1 == clip.track && c.2 == at).unwrap().0.clone();

        let mut hand = Hand::new(stage);
        hand.drag(&mut bench.editor, clip, grip, dx, None);
        let after = clips(bench.edit());
        let refused = bench.refusal().map(str::to_owned);
        let now = |name: &str| after.iter().filter(|c| c.0 == name).cloned().collect::<Vec<Known>>();
        // How much longer a trim made the clip, and the frames its ripple
        // took out of every track (or put in: see `rippled`).
        let mut ripple: Option<(EditFrame, (EditFrame, EditFrame))> = None;
        match grip {
            Grip::Head | Grip::Tail => {
                prop_assert_eq!(&refused, &None, "an edge drag was refused");
                let longer = if grip == Grip::Tail { dx.max(1 - duration) } else { (-dx).max(1 - duration) };
                prop_assert_eq!(now(&name), vec![(name.clone(), clip.track, at, duration + longer)]);
                let frames = match (grip, longer < 0) {
                    // The tail's last frames go; or frames are put in after it.
                    (Grip::Tail, _) => (end + longer, end),
                    // The head's first frames go; or frames are put in before it.
                    (_, true) => (at, at - longer),
                    (_, false) => (at + longer, at),
                };
                if longer != 0 {
                    ripple = Some((longer, frames));
                }
            }
            Grip::Slip => {
                prop_assert_eq!(&refused, &None, "a slip was refused");
                prop_assert_eq!(&after, &before);
            }
            Grip::RollHead | Grip::RollTail => {
                let left = if grip == Grip::RollTail { Some(clip.index) } else { clip.index.checked_sub(1) };
                let touching = left.filter(|&i| own.get(i + 1).is_some_and(|next| next.0 == own[i].1));
                match touching {
                    Some(i) => {
                        prop_assert_eq!(&refused, &None, "a roll of two clips that touch was refused");
                        let (a, b) = (own[i], own[i + 1]);
                        let moved = dx.clamp(1 - (a.1 - a.0), (b.1 - b.0) - 1);
                        let got = bench.edit().spans(clip.track);
                        prop_assert_eq!(got[i], (a.0, a.1 + moved));
                        prop_assert_eq!(got[i + 1], (b.0 + moved, b.1));
                        let others = |all: &[Known]| all.iter().filter(|c| !(c.1 == clip.track && (c.2 == a.0 || c.2 == b.0 || c.2 == b.0 + moved))).cloned().collect::<Vec<_>>();
                        prop_assert_eq!(others(&after), others(&before));
                    }
                    None => prop_assert_eq!(&after, &before, "a roll with no clip touching changed the cut"),
                }
            }
            Grip::Body => {
                let to = (at + dx).max(0);
                let free = own.iter().enumerate().all(|(i, &(a, e))| i == clip.index || e <= to || a >= to + duration);
                if free {
                    prop_assert_eq!(&refused, &None, "a move to free frames was refused");
                    prop_assert_eq!(now(&name), vec![(name.clone(), clip.track, to, duration)]);
                    let others = |all: &[Known]| all.iter().filter(|c| c.0 != name).cloned().collect::<Vec<_>>();
                    prop_assert_eq!(others(&after), others(&before));
                } else {
                    prop_assert!(refused.as_deref().is_some_and(|why| why.contains("are taken")), "{:?}", refused);
                    prop_assert_eq!(bench.edit(), &edit, "a refused move changed the cut");
                }
            }
        }
        // Every clip is still there, each track's in the order they were.
        if let Some((longer, (from, to))) = ripple {
            for (other, track, was_at, was_long) in &before {
                if *other == name {
                    continue;
                }
                let held = if *track == clip.track {
                    // Its own track's clips are never cut into: those after it move along.
                    Some((if *was_at > at { was_at + longer } else { *was_at }, *was_long))
                } else {
                    rippled((*was_at, *was_long), from, to)
                };
                if let Some((new_at, new_long)) = held {
                    prop_assert_eq!(now(other), vec![(other.clone(), *track, new_at, new_long)], "{} after {:?} by {}", other, grip, dx);
                }
            }
        }
        // Its track has the clips it had: in the order they were, but for a
        // clip moved along it.
        let order = |all: &[Known]| {
            let mut names: Vec<String> = all.iter().filter(|c| c.1 == clip.track).map(|c| c.0.clone()).collect();
            if grip == Grip::Body {
                names.sort();
            }
            names
        };
        prop_assert_eq!(order(&after), order(&before), "the track's clips changed order, or one is gone");
        prop_assert_eq!(bench.edit().problem(), None);
    }

    /// Trimming throws nothing away: an edge dragged in and then back out
    /// by as much leaves the clip, and its track, exactly as they were;
    /// and the whole cut too, unless the ripple cut into a clip on another
    /// track on the way.
    #[test]
    fn an_edge_dragged_in_and_back_out_is_as_it_was(
        edit in cut(), stage in hand(), pick in any::<Index>(), tail in any::<bool>(), by in 1i64..60, playhead in any::<Index>(),
    ) {
        let mut bench = Bench::new(&edit, false);
        let all: Vec<TrackRef> = edit.tracks().collect();
        let shown = clip_refs(&edit, &all);
        prop_assume!(!shown.is_empty());
        let clip = *pick.get(&shown);
        let (at, end) = edit.spans(clip.track)[clip.index];
        prop_assume!(end - at >= 2);
        let by = by.min(end - at - 1);
        bench.send(Message::Seek((at as usize) + playhead.index((end - at) as usize)));
        let (grip, inwards) = if tail { (Grip::Tail, -by) } else { (Grip::Head, by) };
        let mut hand = Hand::new(stage);
        hand.drag(&mut bench.editor, clip, grip, inwards, None);
        prop_assert_eq!(bench.refusal(), None);
        prop_assert_eq!(bench.edit().spans(clip.track)[clip.index], (at, end - by));
        hand.drag(&mut bench.editor, clip, grip, -inwards, None);
        prop_assert_eq!(bench.refusal(), None);
        let track_of = |edit: &WorkingEdit| clips(edit).into_iter().filter(|c| c.1 == clip.track).collect::<Vec<_>>();
        prop_assert_eq!(track_of(bench.edit()), track_of(&edit));
        // The frames the trim took out of every track, and put back.
        let (from, to) = if tail { (end - by, end) } else { (at, at + by) };
        let cut_into = clips(&edit).iter().any(|c| c.1 != clip.track && rippled((c.2, c.3), from, to).is_none());
        if !cut_into {
            prop_assert_eq!(bench.edit(), &edit);
        }
    }

    /// The cut can change while a clip is held (a key reaches the editor
    /// mid-drag: Delete, an undo; or an agent's edit): the drag is then
    /// dropped. Letting go changes nothing more, and never another clip
    /// than the one that was pressed. A key that leaves the clip where it
    /// is (a marker) leaves the drag going.
    #[test]
    fn a_key_pressed_while_dragging_never_lands_the_drag_on_another_clip(
        edit in cut(), stage in hand(), pick in any::<Index>(), grip in prop_oneof![Just(Grip::Head), Just(Grip::Tail), Just(Grip::Body)],
        dx in -40i64..40, key in 0u8..5,
    ) {
        let mut bench = Bench::new(&edit, false);
        // Something to undo: a clip put first in the cut.
        let first = VideoClip::new(0, 7, 0, Source::Slug { text: "first".into() });
        bench.send(Message::Edit(Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(first) }));
        let edit = bench.edit().clone();
        let all: Vec<TrackRef> = edit.tracks().collect();
        let shown = clip_refs(&edit, &all);
        let clip = *pick.get(&shown);
        let (at, end) = edit.spans(clip.track)[clip.index];
        let mut hand = Hand::new(stage);
        let from = hand.grip_point(&bench.editor, clip, grip);
        let pressed = hand.press(&mut bench.editor, from);
        prop_assert!(pressed.is_some_and(|m| m.starts_with("Select(Some(")));
        let state = bench.editor.menu_state();
        let pressed_key = match key {
            0 => menu_bar::key_message(&state, Key::Named(Named::Delete), Modifiers::NONE),
            1 => menu_bar::key_message(&state, Key::Named(Named::Delete), Modifiers { shift: true, ..Modifiers::NONE }),
            2 => menu_bar::key_message(&state, Key::Character('z'), Modifiers::COMMAND),
            3 => Some(Message::Edit(Op::Insert {
                track: TrackRef::video(0),
                clip: NewClip::Video(VideoClip::new(0, 5, 0, Source::Slug { text: "agent".into() })),
            })),
            _ => menu_bar::key_message(&state, Key::Character('m'), Modifiers::NONE),
        };
        bench.send(pressed_key.expect("the key does something"));
        let under = bench.edit().clone();
        let to = Point::new(from.x + hand.px * dx as f32, from.y);
        hand.move_to(&mut bench.editor, to);
        hand.release(&mut bench.editor, to);
        if key < 4 {
            prop_assert_eq!(bench.edit(), &under, "the drag went on after the cut changed under it");
        } else {
            // The marker: the clip is where it was, and the drag is its.
            let long = end - at;
            let expected = match grip {
                Grip::Tail => Some((at, at + (long + dx).max(1))),
                Grip::Head => Some((at, at + (long - dx).max(1))),
                _ => None,
            };
            if let Some(expected) = expected {
                prop_assert_eq!(bench.edit().spans(clip.track)[clip.index], expected);
            }
        }
    }

    /// A clip dragged up onto the spare layer makes the layer and lands on
    /// it, as one undo step, and a new spare shows over it. The spare is
    /// only ever a row: until a clip lands on it the cut has no such track.
    #[test]
    fn a_clip_dragged_onto_the_spare_layer_is_one_undo_step(
        edit in cut().prop_filter("the top track is used", |e| e.video.last().is_some_and(|t| !t.clips.is_empty())),
        stage in hand(), pick in any::<Index>(), dx in -50i64..50, hidden in any::<bool>(),
    ) {
        let mut bench = Bench::new(&edit, false);
        let mut view = View::default();
        do_look(&mut bench, &mut view, &Look::HideAudio(hidden))?;
        let video: Vec<TrackRef> = (0..edit.video.len()).map(TrackRef::video).collect();
        let clip = *pick.get(&clip_refs(&edit, &video));
        let (at, end) = edit.spans(clip.track)[clip.index];
        let name = named(&edit.video[clip.track.index].clips[clip.index].source);
        prop_assert!(bench.editor.rows()[0].spare);
        let mut hand = Hand::new(stage);
        let spare_y = Hand::row_y(&bench.editor, 0, 0.5);
        hand.drag(&mut bench.editor, clip, Grip::Body, dx, Some(spare_y));
        prop_assert_eq!(bench.refusal(), None);
        let top = TrackRef::video(edit.video.len());
        let after = clips(bench.edit());
        prop_assert_eq!(bench.edit().video.len(), edit.video.len() + 1);
        let landed: Vec<Known> = after.iter().filter(|c| c.1 == top).cloned().collect();
        prop_assert_eq!(landed, vec![(name.clone(), top, (at + dx).max(0), end - at)]);
        let others = |all: &[Known]| all.iter().filter(|c| c.0 != name).cloned().collect::<Vec<_>>();
        prop_assert_eq!(others(&after), others(&clips(&edit)));
        check_rows(&bench, &view, true)?;
        bench.send(Message::Undo);
        prop_assert_eq!(bench.edit(), &edit, "the drop was not one undo step");
        prop_assert!(!bench.editor.doc.can_undo());
    }

    /// The clip fields take typing exactly when one clip is selected on a
    /// cut that can change, and the opacity and the transform's only for a
    /// video clip. A value typed and entered is then one undo step that
    /// changes that one number of that one clip; text that is no value of
    /// the field, a value on a field that is greyed, or anything on a
    /// published cut, changes nothing.
    #[test]
    fn the_clip_fields_change_one_number_of_one_clip(
        edit in cut(), clicks in prop::collection::vec(any::<Index>(), 0..3), read_only in any::<bool>(),
        field in any::<Index>(), tenths in -9000i64..9000, junk in prop::option::weighted(0.25, prop::sample::select(vec!["", "x", "--1", "1e12", "nan"])),
    ) {
        let mut bench = Bench::new(&edit, read_only);
        let all: Vec<TrackRef> = edit.tracks().collect();
        let every = clip_refs(&edit, &all);
        for (n, click) in clicks.iter().enumerate() {
            if !every.is_empty() {
                bench.send(Message::Select(Some(*click.get(&every)), n > 0, 0));
            }
        }
        let selected: Vec<ClipRef> = bench.editor.selection.iter().copied().collect();
        let one = (selected.len() == 1).then(|| selected[0]);
        let (fields, reset, hint) = bench.editor.clip_fields_shown();
        for (which, shown) in ClipField::ALL.iter().zip(&fields) {
            let fade = matches!(which, ClipField::FadeIn | ClipField::FadeOut);
            let takes = !read_only && one.is_some_and(|clip| fade || clip.track.kind == TrackKind::Video);
            prop_assert_eq!(shown.enabled, takes, "{:?} with {:?} selected", which, selected);
        }
        prop_assert_eq!(hint, if one.is_some() { HINT_SELECTED } else { HINT_NONE });
        prop_assert_eq!(reset, None, "nothing here is moved, scaled or turned");

        let which = *field.get(&ClipField::ALL);
        let at = ClipField::ALL.iter().position(|f| *f == which).unwrap();
        // A value the field takes: frames, a percentage, percent or degrees.
        let value = match which {
            ClipField::FadeIn | ClipField::FadeOut => (tenths.abs() % 60) as f64,
            ClipField::Opacity => (tenths.abs() % 1001) as f64 / 10.0,
            ClipField::Scale => (tenths.abs() + 10) as f64 / 10.0,
            _ => tenths as f64 / 10.0,
        };
        let text = junk.map_or_else(|| format!("{value}"), str::to_owned);
        bench.send(Message::ClipField(which, text.clone()));
        bench.send(Message::ClipFieldSet(which));
        let after = bench.edit().clone();
        let Some(clip) = one.filter(|_| fields[at].enabled && junk.is_none()) else {
            prop_assert_eq!(&after, &edit, "{:?} typed into {:?} changed the cut", text, which);
            prop_assert!(!bench.editor.doc.can_undo());
            if one.is_some() && !read_only && junk.is_some() {
                prop_assert!(bench.refusal().is_some(), "{:?} was taken without a word", text);
            }
            return Ok(());
        };
        // The one number, read from the cut.
        let number = |edit: &WorkingEdit| -> f64 {
            let (fade_in, fade_out, opacity, transform) = match clip.track.kind {
                TrackKind::Video => {
                    let c = &edit.video[clip.track.index].clips[clip.index];
                    (c.fade_in, c.fade_out, c.opacity, c.transform)
                }
                TrackKind::Audio => {
                    let c = &edit.audio[clip.track.index].clips[clip.index];
                    (c.fade_in, c.fade_out, 1.0, Transform::IDENTITY)
                }
            };
            match which {
                ClipField::FadeIn => fade_in as f64,
                ClipField::FadeOut => fade_out as f64,
                ClipField::Opacity => opacity * 100.0,
                ClipField::X => transform.x * 100.0,
                ClipField::Y => transform.y * 100.0,
                ClipField::Scale => transform.scale * 100.0,
                ClipField::Rotation => transform.rotation,
            }
        };
        let was = number(&edit);
        let long = { let (at, end) = edit.spans(clip.track)[clip.index]; (end - at) as f64 };
        let fits = !matches!(which, ClipField::FadeIn | ClipField::FadeOut) || value <= long;
        if (value - was).abs() < 1e-9 || !fits {
            prop_assert_eq!(&after, &edit);
            prop_assert_eq!(bench.refusal().is_some(), !fits);
            return Ok(());
        }
        prop_assert_eq!(bench.refusal(), None);
        prop_assert!((number(&after) - value).abs() < 1e-9, "{:?} is {} after {:?} was entered", which, number(&after), text);
        // That number only: put back, the cut is as it was; and it was one step.
        prop_assert_eq!(clips(&after), clips(&edit));
        bench.send(Message::Undo);
        prop_assert_eq!(bench.edit(), &edit);
        prop_assert!(!bench.editor.doc.can_undo(), "more than one undo step");
    }

    /// A double-click on a sequence's name in the Shots bin (or two presses
    /// on it in the grid; with Shift, laid over) puts every shot of the
    /// sequence in the cut, once, in natural name order whatever order the
    /// bin lists them in, back to back from the playhead, on one track,
    /// each as long as its range; the playhead ends after the last. It is
    /// one undo step. Inserted, what was there moves exactly as one insert
    /// of that length would move it; laid over, nothing that was there
    /// moves at all. And it lands where it would with every track shown:
    /// a solo or hidden audio is the view's, so the same cut comes of it
    /// (onto a track without a row, if that is where there is room; the
    /// line under the rows says tracks are hidden).
    #[test]
    fn a_sequence_goes_in_whole_whatever_the_rows_show(
        edit in cut(), numbers in prop::collection::btree_map(1u32..300, (0usize..5, 1i64..20), 1..6),
        others in prop::collection::btree_set(1u32..300, 0..3), seed in any::<u64>(),
        playhead in any::<Index>(), overlay in any::<bool>(), grid in any::<bool>(),
        looks in prop::collection::vec(focus_look(), 1..5),
    ) {
        let shot = |sequence: &str, name: String, frames: i64| BinShot {
            uri: format!("entity:/shots/{sequence}/{name}"),
            sequence: sequence.into(),
            name,
            first: 1001,
            last: 1000 + frames,
        };
        // Its shots by number, each padded its own way: "sh9", "sh010".
        let ordered: Vec<BinShot> = numbers.iter().map(|(n, (pad, frames))| shot("A", format!("sh{n:0pad$}"), *frames)).collect();
        let mut listed: Vec<BinShot> = ordered.iter().cloned().chain(others.iter().map(|n| shot("B", format!("{n}"), 9))).collect();
        for i in 0..listed.len() {
            let j = (seed as usize).wrapping_mul(i + 13) % listed.len();
            listed.swap(i, j);
        }
        let at = playhead.index(edit.length() as usize + 1);
        let total: EditFrame = ordered.iter().map(|s| s.last - s.first + 1).sum();
        let put = |bench: &mut Bench| {
            bench.editor.bins.shots = listed.clone();
            bench.send(Message::Seek(at));
            let target = Target::Sequence("A".into());
            if grid {
                bench.send(Message::Bin(bin_panel::BinMessage::Press(target.clone(), overlay)));
                bench.send(Message::Bin(bin_panel::BinMessage::Press(target, overlay)));
            } else {
                bench.send(Message::Bin(bin_panel::BinMessage::Activate(target, overlay)));
            }
        };
        let mut bench = Bench::new(&edit, false);
        put(&mut bench);
        prop_assert_eq!(bench.refusal(), None);
        let after = bench.edit().clone();
        prop_assert_eq!(after.problem(), None);
        let mut new: Vec<Known> = clips(&after).into_iter().filter(|c| c.0.starts_with("entity:/shots/")).collect();
        new.sort_by_key(|c| c.2);
        let mut next = at as EditFrame;
        prop_assert_eq!(new.len(), ordered.len(), "every shot of the sequence, once");
        for (clip, shot) in new.iter().zip(&ordered) {
            prop_assert_eq!(&clip.0, &shot.uri, "natural order");
            prop_assert_eq!((clip.1, clip.2, clip.3), (new[0].1, next, shot.last - shot.first + 1), "back to back on one track");
            next += clip.3;
        }
        prop_assert_eq!(bench.editor.position as EditFrame, at as EditFrame + total, "the playhead ends after the last");
        let old: Vec<Known> = clips(&after).into_iter().filter(|c| !c.0.starts_with("entity:/shots/")).collect();
        if overlay {
            prop_assert_eq!(&old, &clips(&edit), "an overlay moved what was there");
        } else {
            // One insert of the run's length, on any track, moves the rest the same.
            let one = VideoClip::new(at as EditFrame, total, 0, Source::Slug { text: "entity:/shots/one".into() });
            let single = th_edit::ops::apply(&edit, &Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(one) }, &AnyFrames).unwrap();
            let rest: Vec<Known> = clips(&single).into_iter().filter(|c| !c.0.starts_with("entity:/shots/")).collect();
            prop_assert_eq!(&old, &rest, "an insert moved what was there otherwise than one insert does");
        }
        // The same cut whatever has a row.
        let mut focused = Bench::new(&edit, false);
        let mut view = View::default();
        for look in &looks {
            do_look(&mut focused, &mut view, look)?;
        }
        put(&mut focused);
        prop_assert_eq!(focused.edit(), &after, "the view changed where the sequence went");
        check_rows(&focused, &view, true)?;
        // One undo step.
        bench.send(Message::Undo);
        prop_assert_eq!(bench.edit(), &edit, "the sequence was not one undo step");
        prop_assert!(!bench.editor.doc.can_undo());
    }

    /// The tracks hold a drag to the frames the clip's source has, as the
    /// editor resolved them: an edge dragged out, however far, shows as
    /// many more frames as the source has that way, and no more.
    #[test]
    fn an_edge_stops_where_the_clips_footage_ends(
        head in 0i64..30, tail in 0i64..30, duration in 1i64..40, dx in 1i64..80, drag_tail in any::<bool>(), stage in hand(),
    ) {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let clip = VideoClip::new(40, duration, 1001 + head, Source::Slug { text: "V1.0".into() });
        edit.video.push(Track { name: "V1".into(), clips: vec![clip] });
        let mut bench = Bench::new(&edit, false);
        // Its source resolves, with frames 1001 to the clip's last and `tail` more.
        drop(bench.editor.changed());
        let last = 1001 + head + duration - 1 + tail;
        let resolved = Resolved { available: Some(Available::Frames(1001, last)), ..Resolved::nothing("a test's source") };
        bench.send(Message::Resolved(0, Ok(resolved)));
        let rows = bench.editor.rows();
        let shown = rows.iter().find(|r| !r.spare).unwrap();
        prop_assert_eq!(shown.clips[0].room, Some(Room { head, tail }));
        let clip = ClipRef { track: TrackRef::video(0), index: 0 };
        let mut hand = Hand::new(stage);
        let (grip, by, room) = if drag_tail { (Grip::Tail, dx, tail) } else { (Grip::Head, -dx, head) };
        hand.drag(&mut bench.editor, clip, grip, by, None);
        prop_assert_eq!(bench.refusal(), None);
        prop_assert_eq!(bench.edit().video[0].clips[0].duration, duration + dx.min(room));
    }

    /// The journal says a shot has a new playblast or render: that shot's
    /// thumbnail is read again when it is next on screen, and no other's;
    /// another project's line, or another kind of change, reads none.
    /// View > Refresh reads them all again.
    #[test]
    fn a_new_playblast_or_render_reads_that_shots_thumbnail_again(
        shots in 1usize..6,
        changes in prop::collection::vec((prop::sample::select(vec!["playblast", "render", "export", "workfile"]), any::<bool>(), 0usize..8, any::<bool>()), 0..5),
    ) {
        let edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let mut bench = Bench::new(&edit, false);
        let uri = |n: usize| format!("entity:/shots/A/{n:03}");
        let keys: Vec<ThumbKey> = (0..shots).map(|n| ThumbKey::Shot(uri(n))).collect();
        let now = Instant::now();
        let picture = || Loaded { proxy: Some(Proxy { width: 1, height: 1, rgb: vec![[0.5; 3]], linear: false }), colour_space: None, note: None };
        let thumbs = &mut bench.editor.bin_state.thumbs;
        for key in &keys {
            thumbs.seen.mark(key, now);
        }
        for _ in 0..shots {
            for key in thumbs.start(&keys, now) {
                thumbs.arrived(&key, Ok(picture()), |_| Err("no display".into()));
            }
        }
        prop_assert!(keys.iter().all(|k| matches!(thumbs.shown(k), Shown::Picture(_))));
        let project = bench.editor.journal_key.clone();
        let lines: Vec<th_pipe::events::Change> = changes
            .iter()
            .map(|(kind, own, shot, slash)| th_pipe::events::Change {
                project: if *own { project.clone() } else { "another project".into() },
                kind: (*kind).to_owned(),
                uri: format!("{}{}", uri(*shot), if *slash { "/" } else { "" }),
                detail: String::new(),
                pid: 1,
                time: 0.0,
                id: String::new(),
            })
            .collect();
        bench.editor.bin_thumbs_changed(Some(&lines));
        for (n, key) in keys.iter().enumerate() {
            let changed = changes.iter().any(|(kind, own, shot, _)| *own && *shot == n && matches!(*kind, "playblast" | "render"));
            let again = matches!(bench.editor.bin_state.thumbs.shown(key), Shown::Pending);
            prop_assert_eq!(again, changed, "shot {} after {:?}", n, changes);
        }
        bench.editor.bin_thumbs_changed(None);
        prop_assert!(keys.iter().all(|k| matches!(bench.editor.bin_state.thumbs.shown(k), Shown::Pending)));
    }
}

/// Every source here has any frame.
struct AnyFrames;

impl th_edit::ops::Sources<Source> for AnyFrames {
    fn available(&self, _: &Source) -> Available {
        Available::Any
    }
}

/// Grid or list is this user's choice, there the next time the editor
/// opens; a fresh user's bins are a list.
#[test]
fn the_grid_choice_is_there_next_time() {
    let edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    let mut bench = Bench::new(&edit, false);
    assert!(!bench.editor.layout.bins_grid);
    bench.send(Message::Bin(bin_panel::BinMessage::Grid(true)));
    assert!(bench.editor.layout.bins_grid);
    let mut bench = bench.reopened(&edit, false);
    assert!(bench.editor.layout.bins_grid);
    bench.send(Message::Bin(bin_panel::BinMessage::Grid(false)));
    assert!(!bench.reopened(&edit, false).editor.layout.bins_grid);
}

/// A refusal on the status line is about the last thing tried: an undo or
/// a redo after it takes it away, so a clip the undo took out is not read
/// as lost by the drag that was refused before it.
#[test]
fn an_undo_takes_an_old_refusal_off_the_status_line() {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    let clip = |at, text: &str| VideoClip::new(at, 72, 0, Source::Slug { text: text.into() });
    edit.video.push(Track { name: "V1".into(), clips: vec![clip(0, "a")] });
    let mut bench = Bench::new(&edit, false);
    bench.send(Message::Edit(Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(clip(72, "b")) }));
    assert_eq!(bench.refusal(), None);
    // The second clip dragged back over the first: refused, nothing changes.
    let second = ClipRef { track: TrackRef::video(0), index: 1 };
    let before = bench.edit().clone();
    bench.send(Message::Edit(Op::Move { clip: second, to: second.track, at: 34 }));
    assert_eq!(bench.refusal(), Some("Not done: frames 34 to 105 are taken, by a clip at 0 to 71"));
    assert_eq!(bench.edit(), &before);
    bench.send(Message::Undo);
    assert_eq!(bench.edit(), &edit, "the undo took the second clip back out");
    assert_eq!(bench.refusal(), None, "the refusal is of the move before the undo");
    bench.send(Message::Edit(Op::Move { clip: second, to: second.track, at: 34 }));
    assert!(bench.refusal().is_some());
    bench.send(Message::Redo);
    assert_eq!(bench.refusal(), None);
}
