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
use th_edit::document::{AudioClip, Track, Transform, VideoClip};
use th_edit::ops::{Available, NewClip};

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
            edit.video.push(Track { name, clips, color: None });
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
            edit.audio.push(Track { name, clips, color: None });
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
    /// Ctrl (Cmd) goes down while an edge is held: it stays a trim.
    ctrl_on_edges: bool,
    now: Instant,
}

fn hand() -> impl Strategy<Value = ((i16, i16), u8)> {
    ((-300i16..300, -300i16..300), 4u8..9)
}

impl Hand {
    fn new(((x, y), px): ((i16, i16), u8)) -> Self {
        Hand {
            pointer: Pointer::default(),
            origin: (f32::from(x), f32::from(y)),
            px: f32::from(px),
            ctrl_on_edges: false,
            now: Instant::now(),
        }
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
        self.hold(editor, Modifiers { alt, ..Modifiers::NONE });
    }

    /// The keys held change to `modifiers`.
    fn hold(&mut self, editor: &mut Editor, modifiers: Modifiers) {
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
            Grip::Body | Grip::Push | Grip::Slip => HEADER + self.px * (at + end) as f32 / 2.0,
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
        if grip == Grip::Push || (self.ctrl_on_edges && matches!(grip, Grip::Head | Grip::Tail)) {
            // Ctrl (Cmd) pressed once the clip is held: a push from here on.
            self.hold(editor, Modifiers::COMMAND);
        }
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
    /// The body, with Ctrl (Cmd) held: what follows on its track moves too.
    Push,
}

fn grip() -> impl Strategy<Value = Grip> {
    prop_oneof![
        3 => Just(Grip::Head),
        3 => Just(Grip::Tail),
        1 => Just(Grip::RollHead),
        1 => Just(Grip::RollTail),
        1 => Just(Grip::Slip),
        2 => Just(Grip::Body),
        2 => Just(Grip::Push),
    ]
}

/// What a trim does to another track, whose clips are at `spans` (each
/// where it starts and how long, in order): the trim made its clip
/// `longer` frames longer at frame `at` (shorter: it took the frames
/// `at .. at - longer` out). No clip is ever cut or lost: the clips that
/// start at or after those frames all move along by `longer`, or, when
/// moving them earlier would put one on a clip that stays or a clip starts
/// in the frames taken out, the whole track stays as it is.
fn trim_rippled(spans: &[(EditFrame, EditFrame)], at: EditFrame, longer: EditFrame) -> Vec<(EditFrame, EditFrame)> {
    let to = if longer < 0 { at - longer } else { at };
    let moved: Vec<(EditFrame, EditFrame)> = spans.iter().map(|&(a, d)| (if a >= to { a + longer } else { a }, d)).collect();
    let apart = moved.iter().enumerate().all(|(i, a)| moved[i + 1..].iter().all(|b| a.0 + a.1 <= b.0 || b.0 + b.1 <= a.0));
    let inside = spans.iter().any(|&(a, _)| at <= a && a < to);
    if longer > 0 || (apart && !inside) { moved } else { spans.to_vec() }
}

/// The edit frame a trim of `longer` frames puts frames in at, or takes
/// them out from: the clip is at `at .. end`, trimmed at its tail (else
/// its head, which stays where it is while what it shows changes).
fn trim_place(tail: bool, at: EditFrame, end: EditFrame, longer: EditFrame) -> EditFrame {
    match (tail, longer < 0) {
        (true, true) => end + longer,
        (true, false) => end,
        (false, _) => at,
    }
}

/// Each track's clips as where they start and how long.
fn track_spans(edit: &WorkingEdit, track: TrackRef) -> Vec<(EditFrame, EditFrame)> {
    edit.spans(track).into_iter().map(|(at, end)| (at, end - at)).collect()
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
        let changes = |message: &Message| matches!(message, Message::Edit(_) | Message::At(..) | Message::CloseGap(_));
        // Close Gap is offered exactly on a gap: empty frames of a track
        // with a clip of it after them.
        let in_gap = at.zip(track).is_some_and(|(frame, t)| {
            let spans = before.spans(t);
            let frame = frame as EditFrame;
            !spans.iter().any(|&(a, e)| a <= frame && frame < e) && spans.iter().any(|&(a, _)| a > frame)
        });
        prop_assert_eq!(labels.contains(&"Close Gap"), !read_only && in_gap, "{:?}", labels);
        if read_only {
            prop_assert!(!entries.iter().any(|(_, m)| changes(m)), "a published cut is offered {:?}", labels);
        }
        prop_assert_eq!(labels.iter().any(|l| l.contains("Here")), !read_only && at.is_some(), "{:?}", labels);
        let removable = track.is_some_and(|t| before.spans(t).is_empty() && before.track_count(t.kind) > 1);
        prop_assert_eq!(labels.iter().any(|l| l.starts_with("Remove Track")), !read_only && removable, "{:?}", labels);
        // A track with clips is deleted with them, and its row says so.
        let deletable = track.is_some_and(|t| !before.spans(t).is_empty() && before.track_count(t.kind) > 1);
        prop_assert_eq!(labels.iter().any(|l| l.starts_with("Delete Track")), !read_only && deletable, "{:?}", labels);

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
            "Close Gap" => {
                // The clips after the click on that track, earlier by the
                // empty frames around it; nothing else.
                let (t, frame) = (track.unwrap(), at.unwrap() as EditFrame);
                let spans = before.spans(t);
                let next = spans.iter().find(|s| s.0 > frame).unwrap().0;
                let gap = next - spans.iter().filter(|s| s.1 <= frame).map(|s| s.1).max().unwrap_or(0);
                let expected: Vec<Known> =
                    known(&before).into_iter().map(|c| if c.1 == t && c.2 >= next { (c.0, c.1, c.2 - gap, c.3) } else { c }).collect();
                prop_assert_eq!(known(&after), expected);
            }
            l if l.starts_with("Remove Track") => {
                let gone = track.unwrap();
                prop_assert!(l.ends_with(before.track_name(gone)));
                prop_assert_eq!(after.track_count(gone.kind), before.track_count(gone.kind) - 1, "{:?}", bench.refusal());
                prop_assert_eq!(known(&after).len(), known(&before).len(), "Remove Track took clips with it");
            }
            l if l.starts_with("Delete Track") => {
                // The track and its clips, and no other clip (`track_menu`
                // holds it to the rest).
                let gone = track.unwrap();
                let n = before.spans(gone).len();
                prop_assert!(l.ends_with(&format!("and {n} Clip{}", if n == 1 { "" } else { "s" })), "{}", l);
                prop_assert_eq!(known(&after).len(), known(&before).len() - n, "{:?}", bench.refusal());
            }
            l if l.starts_with("Move Track") => {
                // Every clip is still there, as long and where in time it
                // was (`track_menu` holds it to the order).
                let timed = |edit: &WorkingEdit| -> BTreeSet<(String, EditFrame, EditFrame)> { known(edit).into_iter().map(|c| (c.0, c.2, c.3)).collect() };
                prop_assert_eq!(timed(&after), timed(&before), "{:?}", bench.refusal());
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
        pick in any::<Index>(), grip in grip(), dx in -70i64..70, playhead in any::<Index>(), ctrl in any::<bool>(),
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

        let mut hand = Hand { ctrl_on_edges: ctrl, ..Hand::new(stage) };
        hand.drag(&mut bench.editor, clip, grip, dx, None);
        let after = clips(bench.edit());
        let refused = bench.refusal().map(str::to_owned);
        let now = |name: &str| after.iter().filter(|c| c.0 == name).cloned().collect::<Vec<Known>>();
        // How much longer a trim made the clip, and where (`trim_place`).
        let mut ripple: Option<(EditFrame, EditFrame)> = None;
        match grip {
            Grip::Head | Grip::Tail => {
                prop_assert_eq!(&refused, &None, "an edge drag was refused");
                let longer = if grip == Grip::Tail { dx.max(1 - duration) } else { (-dx).max(1 - duration) };
                prop_assert_eq!(now(&name), vec![(name.clone(), clip.track, at, duration + longer)]);
                if longer != 0 {
                    ripple = Some((longer, trim_place(grip == Grip::Tail, at, end, longer)));
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
            Grip::Push => {
                // The clip and all after it on its track, by as far as the
                // clip before it (or the cut's start) lets them; nothing
                // else, on any track; and it is one undo step.
                prop_assert_eq!(&refused, &None, "a push was refused");
                let floor = clip.index.checked_sub(1).map_or(0, |i| own[i].1);
                let moved = dx.max(floor - at);
                let expected: Vec<Known> = before
                    .iter()
                    .map(|c| if c.1 == clip.track && c.2 >= at { (c.0.clone(), c.1, c.2 + moved, c.3) } else { c.clone() })
                    .collect();
                prop_assert_eq!(&after, &expected, "a push by {} (held to {})", dx, moved);
                if moved != 0 {
                    let mut again = Bench::new(&edit, false);
                    Hand::new(stage).drag(&mut again.editor, clip, grip, dx, None);
                    again.send(Message::Undo);
                    prop_assert_eq!(again.edit(), &edit, "a push was not one undo step");
                    prop_assert!(!again.editor.doc.can_undo());
                }
            }
            Grip::Body => {
                // A move is never refused for want of room, and never lands
                // on a clip: it goes where it was let go when those frames
                // are free and nothing is near enough to snap to, else on a
                // snap point within 8 pixels or the nearest free place
                // beside what is in the way. Nothing else moves.
                prop_assert_eq!(&refused, &None, "a move was refused");
                let to = at + dx;
                let free = |to: EditFrame| to >= 0 && own.iter().enumerate().all(|(i, &(a, e))| i == clip.index || e <= to || a >= to + duration);
                let landed = now(&name);
                prop_assert_eq!(landed.len(), 1);
                let got = landed[0].2;
                prop_assert_eq!(&landed[0], &(name.clone(), clip.track, got, duration));
                prop_assert!(free(got), "the clip landed on another, at {}", got);
                let others = |all: &[Known]| all.iter().filter(|c| c.0 != name).cloned().collect::<Vec<_>>();
                prop_assert_eq!(others(&after), others(&before));
                // What it may snap to: the cut's start, the playhead, and
                // the ends of every other clip that has a row.
                let reach = if dx == 0 { 0 } else { (8.0 / hand.px).round() as EditFrame };
                let mut points = vec![0, bench.editor.position as EditFrame];
                points.extend(before.iter().filter(|c| c.0 != name && view.rows(&edit).contains(&c.1)).flat_map(|c| [c.2, c.2 + c.3]));
                let near = points.iter().any(|&p| (p - to).abs() <= reach || (p - to - duration).abs() <= reach);
                if free(to) && !near {
                    prop_assert_eq!(got, to, "free frames, nothing to snap to");
                }
                // No free place is much nearer than where it landed.
                let nearest = (0..400).filter(|&d| free(to - d) || free(to + d)).min().unwrap();
                prop_assert!((got - to).abs() <= nearest + 2 * reach, "landed {} from {}, a free place was {} away", got - to, to, nearest);
                if got != to {
                    let on_point = points.contains(&got) || points.contains(&(got + duration));
                    let beside = own.iter().enumerate().any(|(i, &(a, e))| i != clip.index && (e == got || a == got + duration));
                    prop_assert!(on_point || beside || got == 0, "it landed at {} for no reason", got);
                }
            }
        }
        // Every clip is still there, each track's in the order they were.
        if let Some((longer, place)) = ripple {
            for (other, track, was_at, was_long) in &before {
                if *other != name && *track == clip.track {
                    // Its own track: the clips after it move along.
                    let new_at = if *was_at > at { was_at + longer } else { *was_at };
                    prop_assert_eq!(now(other), vec![(other.clone(), *track, new_at, *was_long)], "{} after {:?} by {}", other, grip, dx);
                }
            }
            // Every other track, shown or not, to the frame: nothing of it
            // is cut or lost.
            for track in edit.tracks().filter(|t| *t != clip.track) {
                prop_assert_eq!(track_spans(bench.edit(), track), trim_rippled(&track_spans(&edit, track), place, longer), "{:?} after {:?} by {}", track, grip, dx);
            }
            let names = |all: &[Known]| all.iter().map(|c| c.0.clone()).collect::<Vec<_>>();
            prop_assert_eq!(names(&after), names(&before), "a trim took a clip away, or cut one");
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
        // No clip of any track was cut or lost on the way.
        let names = |edit: &WorkingEdit| clips(edit).into_iter().map(|c| (c.0, c.1, c.3)).collect::<Vec<_>>();
        prop_assert_eq!(names(bench.edit()), names(&edit));
        // The whole cut is as it was when every other track followed the
        // trim in (one that had no room stayed, and is pushed on the way
        // back out).
        let place = trim_place(tail, at, end, -by);
        let followed = edit.tracks().filter(|t| *t != clip.track).all(|t| {
            let spans = track_spans(&edit, t);
            let moved = trim_rippled(&spans, place, -by);
            moved != spans || !spans.iter().any(|s| s.0 >= place)
        });
        if followed {
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
        prop_assert_eq!(landed.len(), 1);
        let got = landed[0].2;
        prop_assert_eq!(&landed[0], &(name.clone(), top, got, end - at));
        // Where it was let go, or within 8 pixels of there on a snap
        // point: the cut's start, the playhead or another clip's end.
        let reach = if dx == 0 { 0 } else { (8.0 / hand.px).round() as EditFrame };
        let mut points = vec![0, bench.editor.position as EditFrame];
        points.extend(clips(&edit).iter().filter(|c| c.0 != name && view.rows(&edit).contains(&c.1)).flat_map(|c| [c.2, c.2 + c.3]));
        let snapped = points.contains(&got) || points.contains(&(got + end - at));
        prop_assert!(got == at + dx || (snapped && (got - at - dx).abs() <= reach) || (got == 0 && at + dx < 0), "landed at {}, let go at {}", got, at + dx);
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

    /// Maths typed into a clip field and entered (`48/2`, `+=5`) leaves the
    /// cut exactly as the number it works out to does when typed plainly,
    /// in one undo step; a sum with no answer (a division by zero) changes
    /// nothing and the status line says so.
    #[test]
    fn maths_entered_in_a_clip_field_is_its_number_entered(
        edit in cut(), pick in any::<Index>(), field in any::<Index>(),
        a in 0u32..200, b in 0u32..40, op in prop::sample::select(vec!['+', '-', '*', '/']), relative in any::<bool>(),
    ) {
        let video: Vec<TrackRef> = edit.tracks().filter(|t| t.kind == TrackKind::Video).collect();
        let every = clip_refs(&edit, &video);
        prop_assume!(!every.is_empty());
        let clip = *pick.get(&every);
        let which = *field.get(&ClipField::ALL);
        let at = ClipField::ALL.iter().position(|f| *f == which).unwrap();
        let enter = |text: &str| {
            let mut bench = Bench::new(&edit, false);
            bench.send(Message::Select(Some(clip), false, 0));
            bench.send(Message::ClipField(which, text.to_owned()));
            bench.send(Message::ClipFieldSet(which));
            bench
        };
        // What the field shows is where a relative entry starts from.
        let own: f64 = {
            let mut bench = Bench::new(&edit, false);
            bench.send(Message::Select(Some(clip), false, 0));
            bench.editor.clip_fields_shown().0[at].shown.parse().expect("a field shows a number")
        };
        let (x, y) = (f64::from(a) / 4.0, f64::from(b) / 4.0);
        let left = if relative { own } else { x };
        let value = match op { '+' => left + y, '-' => left - y, '*' => left * y, _ => left / y };
        let typed = if relative { format!("{op}={y}") } else { format!("{x}{op}{y}") };
        let mut sum = enter(&typed);
        if !value.is_finite() {
            prop_assert_eq!(sum.edit(), &edit, "{} changed the cut", typed);
            prop_assert!(sum.refusal().is_some_and(|r| r.contains("divides by zero")), "{:?}", sum.refusal());
            return Ok(());
        }
        let fade = matches!(which, ClipField::FadeIn | ClipField::FadeOut);
        let plain = enter(&if fade { format!("{}", value.round() as i64) } else { format!("{value}") });
        prop_assert_eq!(sum.edit(), plain.edit(), "{} is not {} entered", typed, value);
        prop_assert_eq!(sum.refusal().is_some(), plain.refusal().is_some());
        if sum.edit() != &edit {
            sum.send(Message::Undo);
            prop_assert_eq!(sum.edit(), &edit);
            prop_assert!(!sum.editor.doc.can_undo(), "more than one undo step");
        }
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
        edit.video.push(Track { name: "V1".into(), clips: vec![clip], color: None });
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
    edit.video.push(Track { name: "V1".into(), clips: vec![clip(0, "a")], color: None });
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

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// File > Export Movie…: the dialog opens on the whole cut at the
    /// edit's size (a new user's settings), offers the selected clips'
    /// extent only when clips are selected, and each control sets its own
    /// thing: the line under them says the movie they come to, or why
    /// there is none. Export with settings that make no movie starts
    /// nothing and keeps nothing; with ones that do, the export runs and
    /// the settings are this user's the next time the editor opens.
    #[test]
    fn export_movie_asks_keeps_and_offers_its_settings_again(
        edit in cut(), settings in crate::export_settings::tests::settings(), pick in any::<Index>(), select in any::<bool>(),
    ) {
        use super::export_dialog::{ExportDialog, ExportMessage};
        use crate::export_settings::{self, Form, Settings, Size, SizeKind, Span, SpanKind, Spans};
        use th_pipe::movie::{Codec, Quality};

        let resolution = edit.resolution;
        let every = clip_refs(&edit, &edit.tracks().collect::<Vec<_>>());
        let selected = (select && !every.is_empty()).then(|| *pick.get(&every));
        let mut bench = Bench::new(&edit, false);
        if let Some(clip) = selected {
            bench.send(Message::Select(Some(clip), false, 0));
        }
        let export = |bench: &mut Bench, m: ExportMessage| bench.send(Message::Export(m));
        export(&mut bench, ExportMessage::Open);
        let length = edit.length().max(0) as usize;
        let selection = selected.map(|clip| edit.spans(clip.track)[clip.index]).map(|(at, end)| (at as usize, end as usize));
        let want_spans = Spans { length, selection, looped: None };
        let setup = |bench: &Bench| match &bench.editor.dialog {
            Some(Dialog::Export(ExportDialog::Setup { form, spans, summary, problem, hffmpeg, path, .. })) => {
                Some(((**form).clone(), *spans, summary.clone(), problem.clone(), hffmpeg.is_ok(), path.clone()))
            }
            _ => None,
        };
        let (form, spans, _, problem, has_encoder, _) = setup(&bench).expect("the dialog is open on its settings");
        prop_assert_eq!(spans, want_spans);
        prop_assert_eq!(&form, &Form::new(&Settings::default(), resolution, &spans));
        prop_assert_eq!(problem, None);

        // Each control, as a person would set it. A width typed brings the
        // height of the edit's shape; a span the cut does not offer is not
        // there to press.
        let asked_size = match settings.size {
            Size::Custom { width, .. } => {
                let [width, height] = export_settings::from_width(resolution, width);
                Size::Custom { width, height }
            }
            other => other,
        };
        let size_kind = match asked_size {
            Size::Edit => SizeKind::Edit,
            Size::Half => SizeKind::Half,
            Size::Custom { .. } => SizeKind::Custom,
        };
        export(&mut bench, ExportMessage::Size(SizeKind::ALL.iter().position(|k| *k == size_kind).unwrap()));
        if let Size::Custom { width, .. } = asked_size {
            export(&mut bench, ExportMessage::Width(width.to_string()));
        }
        let span_kind = match settings.span {
            Span::Whole => SpanKind::Whole,
            Span::Selection => SpanKind::Selection,
            Span::Loop => SpanKind::Loop,
            Span::Custom { .. } => SpanKind::Custom,
        };
        let offered = spans.kinds().iter().position(|k| *k == span_kind);
        let asked_span = if offered.is_some() { settings.span } else { Span::Whole };
        if let Some(at) = offered {
            export(&mut bench, ExportMessage::Span(at));
        }
        if let Span::Custom { first, last } = settings.span {
            export(&mut bench, ExportMessage::First(first.to_string()));
            // The last frame as a sum: the field works it out.
            export(&mut bench, ExportMessage::Last(format!("{}+1", last - 1)));
        }
        export(&mut bench, ExportMessage::Codec(Codec::ALL.iter().position(|c| *c == settings.codec).unwrap()));
        // The offered .mp4 follows the codec: Motion JPEG goes in a .mov.
        let (_, _, _, _, _, offered_path) = setup(&bench).expect("still asking");
        prop_assert_eq!(offered_path.ends_with(".mov"), settings.codec == Codec::Mjpeg, "{}", offered_path);
        prop_assert_eq!(offered_path.ends_with(".mp4"), settings.codec == Codec::H264, "{}", offered_path);
        export(&mut bench, ExportMessage::Quality(Quality::ALL.iter().position(|q| *q == settings.quality).unwrap()));
        export(&mut bench, ExportMessage::Audio(settings.audio));
        export(&mut bench, ExportMessage::Burnins(settings.burnins));
        let extension = if settings.codec == Codec::Mjpeg { "mov" } else { "mp4" };
        let movie = bench.prefs.path().join(format!("cut.{extension}")).to_string_lossy().into_owned();
        export(&mut bench, ExportMessage::Path(movie));

        let asked = Settings { size: asked_size, span: asked_span, ..settings };
        let (form, _, summary, _, _, _) = setup(&bench).expect("still asking");
        prop_assert_eq!(form.settings(), Ok(asked));
        let plan = asked.plan(resolution, &spans);
        match &plan {
            Ok(plan) => {
                let line = crate::export::summary(&edit, &bench.editor.display.display, &bench.editor.display.view, plan);
                prop_assert_eq!(summary, Ok(line));
            }
            Err(why) => prop_assert_eq!(summary, Err(why.clone())),
        }

        let kept = export_settings::path(&bench.prefs.path().join("layout.json"));
        export(&mut bench, ExportMessage::Start);
        if plan.is_err() || !has_encoder {
            let (_, _, _, problem, _, _) = setup(&bench).expect("nothing started");
            prop_assert!(problem.is_some(), "it says why it did not start");
            prop_assert!(!kept.exists(), "settings that make no movie are not kept");
            return Ok(());
        }
        prop_assert!(matches!(bench.editor.dialog, Some(Dialog::Export(ExportDialog::Running { .. }))), "the export runs");
        prop_assert_eq!(export_settings::load(&kept, resolution), asked);

        // The same user, the editor opened again with nothing selected.
        let mut again = bench.reopened(&edit, false);
        again.send(Message::Export(ExportMessage::Open));
        let (form, spans, _, _, _, path) = setup(&again).expect("the dialog opens again");
        prop_assert_eq!(&form, &Form::new(&asked, resolution, &spans));
        prop_assert_eq!((form.codec, form.quality, form.audio, form.burnins), (settings.codec, settings.quality, settings.audio, settings.burnins));
        if asked.span == Span::Selection {
            prop_assert_eq!(form.span, SpanKind::Whole, "no selection now: the whole cut");
        }
        prop_assert_eq!(path.ends_with(".mov"), settings.codec == Codec::Mjpeg, "{}", path);
    }
}

/// A shot waiting for a render says so on its clips: the poll looks at the
/// cut's shots (one look at a time, never at a slug), and once a render is
/// found each clip of the shot counts its frames in its second line and
/// carries a strip of the frames it shows, lit where they are on disk,
/// which the tracks draw. When the render is complete both go.
#[test]
fn a_render_on_its_way_shows_on_its_shots_clips() {
    use crate::rendering::{Job, Strip, Unfinished};
    use th_edit::document::Rule;
    use th_pipe::discovery::Aov;

    let shot = "entity:/shots/S/010".to_owned();
    let of_shot = || Source::Shot { shot: shot.clone(), take: Rule::best() };
    let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
    let clips = vec![
        VideoClip::new(0, 10, 1001, of_shot()),
        VideoClip::new(10, 10, 0, Source::Slug { text: "gap".into() }),
        VideoClip::new(24, 10, 1005, of_shot()),
    ];
    edit.video.push(Track { name: "V1".into(), clips, color: None });
    let mut bench = Bench::new(&edit, false);
    assert!(bench.editor.render_strips().is_empty());

    // A fresh watch would look at the shot, and only at it; the editor's
    // poll starts that look, so none starts while it is out.
    let mut fresh = crate::rendering::Watch::default();
    let cut_shots = crate::rendering::shots_of(edit.video[0].clips.iter().map(|c| &c.source));
    assert_eq!(cut_shots, vec![shot.clone()]);
    assert_eq!(fresh.plan(&cut_shots), vec![Job::Find(shot.clone())]);
    bench.send(Message::PollPreviews);
    assert_eq!(bench.editor.rendering.plan(&cut_shots), Vec::new(), "the editor's look is out");

    let details = |bench: &Bench| -> Vec<String> {
        let rows = bench.editor.rows();
        let row = rows.iter().find(|r| r.track == TrackRef::video(0)).expect("V1 has a row");
        row.clips.iter().map(|c| c.detail.clone()).collect()
    };
    let before = details(&bench);
    assert!(before.iter().all(|d| !d.contains("rendered")), "{before:?}");

    // The look is back: lighting v0007 has four of its twelve frames.
    let aov = Aov { path: bench.prefs.path().join("nowhere"), name: "S_010_default_beauty_v0007".into(), suffix: "exr".into() };
    let unfinished = Unfinished {
        department: "lighting".into(),
        layer: "default".into(),
        version: "v0007".into(),
        aov,
        expected: (1001..=1012).collect(),
        there: vec![1001, 1002, 1003, 1008],
        playblast: false,
    };
    bench.send(Message::Rendering(vec![(shot.clone(), Some(unfinished.clone()))]));
    let now = details(&bench);
    for at in [0, 2] {
        assert!(now[at].ends_with("lighting v0007: 4/12 rendered"), "{:?}", now[at]);
        assert!(now[at].starts_with(&before[at]), "it keeps what it said: {:?}", now[at]);
    }
    assert_eq!(now[1], before[1], "a slug waits for nothing");
    let track = TrackRef::video(0);
    let want = vec![
        // Frames 1001 to 1010 of the shot at 0: all expected, 1001-1003 and 1008 there.
        Strip { track, expected: vec![(0, 10)], there: vec![(0, 3), (7, 8)] },
        // Frames 1005 to 1014 at 24: 1005-1012 expected, 1008 there.
        Strip { track, expected: vec![(24, 32)], there: vec![(27, 28)] },
    ];
    assert_eq!(bench.editor.render_strips(), want);
    assert_eq!(bench.editor.tracks_program().rendering, want, "the tracks are given them to draw");

    // The next poll lists the watched render again (one listing).
    let again = bench.editor.rendering.plan(&cut_shots);
    assert!(matches!(again.as_slice(), [Job::Relist(s, u)] if *s == shot && *u == unfinished), "{again:?}");

    // Every frame landed: nothing more is said or drawn.
    let complete = Unfinished { there: unfinished.expected.clone(), ..unfinished };
    bench.send(Message::Rendering(vec![(shot.clone(), Some(complete))]));
    assert_eq!(details(&bench), before);
    assert!(bench.editor.render_strips().is_empty());
    assert!(bench.editor.tracks_program().rendering.is_empty());
}

mod dialogs;
mod flip_and_menus;
mod picks;
mod placement;

mod track_menu;
mod view;
