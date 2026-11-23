//! Reviewing as the window has it: the editor's real picture (the video,
//! the burn-ins, the review surface and the notes, `picture_view`) laid out
//! at a size and sent the pointer's events at pixels, a pen's pressure
//! among them; between events it is drawn and its widget state pruned, as
//! the runtime does after every message. What the surface publishes goes
//! to the editor, the editor writes the marks' files, and a second editor
//! on the same project share reads them, as another machine would.
//!
//! The take is a media item whose frames are never decoded here, so the
//! frame on screen is put there as the playback would put it (`show`).

#![allow(clippy::float_cmp)]

use std::collections::{BTreeSet, HashMap};
use std::path::PathBuf;

use primer::keyboard::Named;
use primer::widget::custom::{Ctx, Cursor, Event, Layout as Placed, LayoutCtx, Limits, Shell, StateStore, Widget, mouse};
use proptest::prelude::*;
use th_edit::document::{Track, VideoClip};
use th_edit::ops::Available;
use th_media::{JpgSequence, Pixels};
use th_pipe::review as files;

use super::entries::Resolved;
use super::playback::Shown;
use super::timeline_window_tests::{FixedText, Kept};
use super::*;
use crate::review::Tool;
use crate::video::Picture;

/// The picture's size on screen.
const SIZE: Size = Size { width: 900.0, height: 520.0 };
/// The take's frames: `FIRST` on, `FRAMES` of them, the cut's first.
const FIRST: i64 = 1001;
const FRAMES: i64 = 24;

/// A clip of a cut: on track `track` (0 = V1), of media item `item`, at
/// cut frame `at`, from the item's frame `from`, `len` frames long.
#[derive(Debug, Clone)]
struct Placed_ {
    track: usize,
    item: &'static str,
    at: usize,
    from: i64,
    len: usize,
}

/// An editor on the project template whose cut is one clip of a media
/// item, resolved with its marks kept in `share`, and its picture.
pub(in crate::editor) struct Viewer {
    pub(in crate::editor) editor: Editor,
    store: StateStore,
    clipboard: Kept,
    /// The frame's pixels, as the take's frames are (their shape).
    frame: (u32, u32),
    _prefs: tempfile::TempDir,
}

impl Viewer {
    pub(in crate::editor) fn new(share: &Path, frame: (u32, u32)) -> Self {
        Self::cut(share, frame, &[Placed_ { track: 0, item: "plate", at: 0, from: FIRST, len: FRAMES as usize }])
    }

    /// An editor whose cut is `clips`, each of a media item of `FRAMES`
    /// frames from `FIRST` on; each item's marks in `share/review/<item>`.
    fn cut(share: &Path, frame: (u32, u32), clips: &[Placed_]) -> Self {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let tracks = clips.iter().map(|c| c.track + 1).max().unwrap_or(1);
        for t in 0..tracks {
            let mut on: Vec<&Placed_> = clips.iter().filter(|c| c.track == t).collect();
            on.sort_by_key(|c| c.at);
            let clips = on
                .iter()
                .map(|c| {
                    let source = Source::Media { item: c.item.into(), version: th_edit::document::VersionRule::Latest };
                    VideoClip::new(c.at as i64, c.len as i64, c.from, source)
                })
                .collect();
            edit.video.push(Track { name: format!("V{}", t + 1), clips, color: None });
        }
        let prefs = tempfile::tempdir().expect("a folder for the prefs");
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
        let colour = Colour::open(&ocio).expect("the template's OCIO config");
        let layout = prefs.path().join("layout.json");
        let mut editor = Editor::with(paths, ocio, Document::new(edit, Origin::Untitled), colour, None, None, Some(layout));
        drop(editor.changed());
        let sources: Vec<Source> = editor.entries.iter().map(|e| e.source.clone()).collect();
        for (index, source) in sources.into_iter().enumerate() {
            let Source::Media { item, .. } = &source else { continue };
            let files = (0..FRAMES).map(|i| share.join(format!("{item}.{}.jpg", FIRST + i))).collect();
            let media = Media::jpg(JpgSequence::new(FIRST, 1, files), (24, 1));
            let take = Take {
                label: format!("{item} v0001"),
                department: "media".into(),
                version: "v0001".into(),
                media: th_playlist::Media::Movie { path: String::new() },
                multi_range: false,
                check: false,
            };
            let review = if clips.len() == 1 && item == "plate" { share.join("review") } else { share.join("review").join(item) };
            let resolved = Resolved {
                takes: vec![take],
                take: Some(0),
                media: Some(Arc::new(media)),
                first: FIRST,
                nothing: None,
                available: Some(Available::Frames(FIRST, FIRST + FRAMES - 1)),
                review: Some(review),
                ..Resolved::nothing("")
            };
            drop(editor.update(Message::Resolved(index, Ok(resolved))));
        }
        let mut viewer = Viewer { editor, store: StateStore::new(), clipboard: Kept::default(), frame, _prefs: prefs };
        if !viewer.editor.engine.layers(viewer.editor.position).is_empty() {
            viewer.show();
        }
        viewer
    }

    /// The frame at the playhead on screen, as playback shows it once
    /// decoded.
    fn show(&mut self) {
        let layers = self.editor.engine.layers(self.editor.position);
        assert!(!layers.is_empty(), "the clip covers the playhead");
        let wanted = layers.iter().map(|l| (l.key, l.opacity.to_bits(), l.transform)).collect();
        let (w, h) = self.frame;
        let frame = th_media::Frame { width: w, height: h, pixels: Arc::new(Pixels::Rgba8(vec![0; (w * h * 4) as usize])) };
        let picture = Picture { id: self.editor.position as u64 + 1, frame, lut: None };
        self.editor.shown = Some(Shown { layers: wanted, picture, source: layers[layers.len() - 1].key.source });
        self.draw(None);
    }

    fn message(&mut self, message: Message) {
        drop(self.editor.update(message));
        self.show();
    }

    /// A key pressed in the window, as `on_key` takes it.
    fn key(&mut self, key: Key, modifiers: Modifiers) {
        let message = self.editor.on_key(window::Id::MAIN, key, modifiers).expect("the key does something");
        self.message(message);
    }

    /// One frame drawn and the store pruned, as after every message.
    fn draw(&mut self, at: Option<Point>) {
        let theme = Theme::dark();
        let view = self.editor.picture_view();
        let node = view.layout(&Limits::new(Size::ZERO, SIZE), LayoutCtx { measurer: &FixedText, store: &self.store, theme: &theme });
        self.store.begin_draw();
        let cursor = at.map_or(Cursor::Unavailable, Cursor::Available);
        let ctx =
            Ctx { cursor, theme: &theme, store: &self.store, measurer: &FixedText, visible: Rectangle::new(Point::new(0.0, 0.0), SIZE) };
        drop(view.draw(Placed::new(&node), ctx));
        self.store.prune();
    }

    /// `event` with the pointer at `at`; the editor is given what the
    /// picture published, then it draws. The messages, as they print.
    fn send(&mut self, event: Event, at: Point) -> Vec<String> {
        let theme = Theme::dark();
        let mut messages = Vec::new();
        {
            let mut view = self.editor.picture_view();
            let node = view.layout(&Limits::new(Size::ZERO, SIZE), LayoutCtx { measurer: &FixedText, store: &self.store, theme: &theme });
            let mut shell =
                Shell::new(&mut messages, &mut self.store, &FixedText, &mut self.clipboard, &theme, Cursor::Available(at), Modifiers::NONE);
            view.on_event(&event, Placed::new(&node), &mut shell);
        }
        let said = messages.iter().map(|m| format!("{m:?}")).collect();
        for message in messages {
            drop(self.editor.update(message));
        }
        self.draw(Some(at));
        said
    }

    /// A stroke through `points` (screen pixels), the pen pressing at
    /// `pressure` (none for a mouse).
    fn stroke(&mut self, points: &[Point], pressure: Option<f32>) -> Vec<String> {
        let mut said = Vec::new();
        for (i, &at) in points.iter().enumerate() {
            if let Some(p) = pressure {
                self.send(Event::Mouse(mouse::Event::Pressure(p)), at);
            }
            let event = if i == 0 { mouse::Event::ButtonPressed(mouse::Button::Left) } else { mouse::Event::CursorMoved { position: at } };
            said.extend(self.send(Event::Mouse(event), at));
        }
        said.extend(self.send(Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), *points.last().unwrap()));
        said
    }

    /// Where media item `item`'s marks are kept, when it is in the cut.
    fn dir_of(&self, item: &str) -> Option<PathBuf> {
        self.editor
            .entries
            .iter()
            .find(|e| matches!(&e.source, Source::Media { item: i, .. } if i == item))
            .and_then(|e| e.resolved()?.review.clone())
    }

    /// The cut's frames whose picture on screen (the top clip there) is a
    /// frame with marks.
    fn showing_marks(&self) -> Vec<usize> {
        (0..self.editor.end())
            .filter(|&p| {
                let layers = self.editor.engine.layers(p);
                let Some(top) = layers.last() else { return false };
                let Some(resolved) = self.editor.entry_of_engine(top.key.source).and_then(|e| e.resolved()) else { return false };
                let (Some(dir), Some(media)) = (&resolved.review, &resolved.media) else { return false };
                let Some(frame) = media.frame_number(top.key.index) else { return false };
                self.editor.review.folder(dir).is_some_and(|f| f.on(frame).next().is_some())
            })
            .collect()
    }

    /// The editor's marks of the frame at the playhead, by file name.
    fn marks_here(&self) -> Vec<files::Mark> {
        let target = self.editor.review_target().expect("the frame takes marks");
        self.editor.review.folder(&target.dir).map(|f| f.on(target.frame).cloned().collect()).unwrap_or_default()
    }
}

/// The marks written to `dir`, read as another machine reads them, once
/// `count` are there.
fn on_disk(dir: &Path, count: usize) -> Vec<files::Mark> {
    let start = std::time::Instant::now();
    loop {
        let changes = files::sync(dir, &HashMap::new()).expect("the folder reads");
        if changes.changed.len() == count || start.elapsed() > Duration::from_secs(10) {
            return changes.changed.into_iter().map(|(_, _, mark)| mark).collect();
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

/// Points of a stroke, as fractions of the picture's size.
fn strokes() -> impl Strategy<Value = Vec<(usize, Vec<(f32, f32)>)>> {
    prop::collection::vec((0usize..FRAMES as usize, prop::collection::vec((0.05f32..0.95, 0.05f32..0.95), 1..6)), 1..4)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 10, ..ProptestConfig::default() })]

    /// Strokes drawn on frames of the cut are saved on their take's own
    /// frames, where they were drawn on the picture (whatever its shape,
    /// and flipped or not) and at the pen's pressure; the ruler shows those
    /// frames and Shift+Left and Shift+Right step through them; another
    /// machine reads them all; the eraser takes a stroke off and Undo
    /// brings it back.
    #[test]
    fn strokes_are_saved_where_they_were_drawn(
        frame in (64u32..400, 64u32..400),
        flipped in any::<bool>(),
        strokes in strokes(),
        pressure in proptest::option::of(0.05f32..1.0),
    ) {
        let share = tempfile::tempdir().unwrap();
        let dir = share.path().join("review");
        let mut viewer = Viewer::new(share.path(), frame);
        viewer.message(Message::Flip(flipped));
        viewer.key(Key::Character('d'), Modifiers::NONE);
        prop_assert!(viewer.editor.review.drawing);
        let mut drawn: Vec<(usize, Vec<Point>)> = Vec::new();
        for (at, fractions) in &strokes {
            viewer.message(Message::Seek(*at));
            let points: Vec<Point> = fractions.iter().map(|&(x, y)| Point::new(x * SIZE.width, y * SIZE.height)).collect();
            let said = viewer.stroke(&points, pressure);
            prop_assert_eq!(said.first().map(String::as_str), Some("Review(Begin)"));
            prop_assert!(said.last().is_some_and(|s| s.starts_with("Review(Stroke(")), "{:?}", said);
            prop_assert_eq!(viewer.editor.status.as_ref().filter(|s| s.1), None);
            drawn.push((*at, points));
        }

        // On disk, each on its frame of the take, where it was drawn.
        let saved = on_disk(&dir, drawn.len());
        prop_assert_eq!(saved.len(), drawn.len());
        let geometry = viewer.editor.review_target().unwrap().geometry;
        for (at, points) in &drawn {
            let found = saved.iter().filter(|m| m.frame == FIRST + *at as i64).filter_map(|m| m.stroke()).any(|s| {
                let back = geometry.to_screen(SIZE, (s.points[0].x, s.points[0].y));
                (back.x - points[0].x).abs() < 0.6 && (back.y - points[0].y).abs() < 0.6
                    && s.points.iter().all(|p| (p.pressure - pressure.unwrap_or(1.0)).abs() < 2e-3)
                    && s.points.iter().all(|p| {
                        let q = geometry.to_screen(SIZE, (p.x, p.y));
                        points.iter().any(|d| (q.x - d.x).abs() < 0.6 && (q.y - d.y).abs() < 0.6)
                    })
            });
            prop_assert!(found, "no saved stroke on frame {} where it was drawn", FIRST + *at as i64);
        }

        // The ruler's keys are those frames, and Shift+Right and Shift+Left
        // step through them.
        let marked: Vec<usize> = drawn.iter().map(|d| d.0).collect::<BTreeSet<_>>().into_iter().collect();
        prop_assert_eq!(&viewer.editor.review_positions(), &marked);
        prop_assert_eq!(&viewer.editor.tracks_program().reviews, &marked);
        viewer.message(Message::Seek(0));
        let mut visited = Vec::new();
        loop {
            let before = viewer.editor.position;
            viewer.key(Key::Named(Named::ArrowRight), Modifiers { shift: true, ..Modifiers::NONE });
            if viewer.editor.position == before {
                break;
            }
            visited.push(viewer.editor.position);
        }
        let after_start: Vec<usize> = marked.iter().copied().filter(|&p| p > 0).collect();
        prop_assert_eq!(&visited, &after_start);
        let end = viewer.editor.position;
        let mut back = Vec::new();
        loop {
            let before = viewer.editor.position;
            viewer.key(Key::Named(Named::ArrowLeft), Modifiers { shift: true, ..Modifiers::NONE });
            if viewer.editor.position == before {
                break;
            }
            back.push(viewer.editor.position);
        }
        let before_end: Vec<usize> = marked.iter().rev().copied().filter(|&p| p < end).collect();
        prop_assert_eq!(&back, &before_end);

        // Another machine on the same share reads them all.
        let mut other = Viewer::new(share.path(), frame);
        other.message(Message::Review(review::ReviewMessage::Poll));
        let start = std::time::Instant::now();
        while !other.editor.review.folder(&dir).is_some_and(|f| f.read) && start.elapsed() < Duration::from_secs(10) {
            other.editor.review_collect();
            std::thread::sleep(Duration::from_millis(5));
        }
        prop_assert_eq!(&other.editor.review_positions(), &marked);

        // The eraser at a stroke's first point takes it off; Undo (Ctrl+Z
        // in review mode) brings it back.
        let (at, points) = &drawn[0];
        viewer.message(Message::Seek(*at));
        let count = viewer.marks_here().len();
        viewer.key(Key::Character('e'), Modifiers::NONE);
        prop_assert_eq!(viewer.editor.review.brush.tool, Tool::Eraser);
        let said = viewer.stroke(&points[..1], None);
        prop_assert!(said.iter().any(|s| s.starts_with("Review(Erase(")), "{:?}", said);
        prop_assert!(viewer.marks_here().len() < count);
        viewer.key(Key::Character('z'), Modifiers::COMMAND);
        prop_assert_eq!(viewer.marks_here().len(), count);
    }
}

/// A note written on a frame is on it for the other machine too, with who
/// wrote it; anyone may change it, which says who did; deleting it takes
/// the frame off the ruler.
#[test]
fn notes_are_shared_and_anyone_may_change_them() {
    let share = tempfile::tempdir().unwrap();
    let dir = share.path().join("review");
    let mut here = Viewer::new(share.path(), (192, 108));
    here.editor.review.me = "remy".into();
    here.message(Message::Seek(7));
    here.message(Message::Review(review::ReviewMessage::Draft("the hand pops".into())));
    here.message(Message::Review(review::ReviewMessage::Post));
    assert_eq!(here.editor.review_comments().len(), 1, "the note shows on its frame");
    assert_eq!(here.editor.review_positions(), vec![7]);
    let saved = on_disk(&dir, 1);
    assert_eq!(saved[0].frame, FIRST + 7);
    assert_eq!(saved[0].author, "remy");

    let mut there = Viewer::new(share.path(), (192, 108));
    there.editor.review.me = "soren".into();
    there.message(Message::Review(review::ReviewMessage::Poll));
    let start = std::time::Instant::now();
    while !there.editor.review.folder(&dir).is_some_and(|f| f.read) && start.elapsed() < Duration::from_secs(10) {
        there.editor.review_collect();
        std::thread::sleep(Duration::from_millis(5));
    }
    there.message(Message::Seek(7));
    let name = there.marks_here()[0].file_name();
    there.message(Message::Review(review::ReviewMessage::EditNote(name.clone())));
    assert_eq!(there.editor.review.draft.text, "the hand pops");
    there.message(Message::Review(review::ReviewMessage::Draft("fixed in v0002".into())));
    there.message(Message::Review(review::ReviewMessage::Post));
    let start = std::time::Instant::now();
    let changed = loop {
        let mark = on_disk(&dir, 1).into_iter().next().unwrap();
        if mark.note().is_some_and(|n| n.text == "fixed in v0002") || start.elapsed() > Duration::from_secs(10) {
            break mark;
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let note = changed.note().unwrap();
    assert_eq!(note.text, "fixed in v0002");
    assert_eq!(changed.author, "remy", "the note stays its writer's");
    assert_eq!(note.edited.as_ref().map(|e| e.1.as_str()), Some("soren"));

    there.message(Message::Review(review::ReviewMessage::DeleteNote(name)));
    assert!(there.editor.review_positions().is_empty());
    assert!(on_disk(&dir, 0).is_empty());
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(8))]

    /// A pen turned over erases with its other end while the pen tool stays
    /// picked: the stroke it crosses goes, the tool is still the pen, and
    /// the next stroke with the tip draws again.
    #[test]
    fn a_pens_eraser_end_erases(pressure in 0.0f32..=1.0, row in 0.3f32..0.7) {
        let share = tempfile::tempdir().unwrap();
        let mut viewer = Viewer::new(share.path(), (192, 108));
        viewer.message(Message::Review(review::ReviewMessage::Mode(true)));
        let across = |y: f32| [Point::new(SIZE.width * 0.3, SIZE.height * y), Point::new(SIZE.width * 0.7, SIZE.height * y)];
        viewer.stroke(&across(row), Some(pressure));
        prop_assert_eq!(viewer.marks_here().len(), 1);

        // Turned over: Primer says Eraser just before the press.
        let down = [Point::new(SIZE.width * 0.5, SIZE.height * 0.15), Point::new(SIZE.width * 0.5, SIZE.height * 0.85)];
        viewer.send(Event::Mouse(mouse::Event::Eraser), down[0]);
        viewer.stroke(&down, Some(pressure));
        prop_assert_eq!(viewer.marks_here().len(), 0, "the eraser end took the stroke off");
        prop_assert_eq!(viewer.editor.review.brush.tool, crate::review::Tool::Pen);

        viewer.stroke(&across(row), Some(pressure));
        prop_assert_eq!(viewer.marks_here().len(), 1, "the tip draws again");
    }
}

const ITEMS: [&str; 2] = ["plate", "other"];

/// A cut of one or two tracks, each a few clips of the two items with
/// gaps between, trimmed anywhere in the items' frames.
fn cut_clips() -> impl Strategy<Value = Vec<Placed_>> {
    let clip = (0usize..4, 1usize..10, 0i64..FRAMES, 0usize..2);
    let track = prop::collection::vec(clip, 1..4);
    prop::collection::vec(track, 1..3).prop_map(|tracks| {
        let mut out = Vec::new();
        for (t, clips) in tracks.into_iter().enumerate() {
            let mut at = 0;
            for (gap, len, offset, item) in clips {
                at += gap;
                let len = len.min((FRAMES - offset) as usize).max(1);
                out.push(Placed_ { track: t, item: ITEMS[item], at, from: FIRST + offset, len });
                at += len;
            }
        }
        out
    })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(32))]

    /// On any cut (clips trimmed, repeated, in gaps, one track over
    /// another), the ruler's keys are exactly the frames whose picture on
    /// screen has marks: none for a mark hidden under a clip on a higher
    /// track, none outside a clip's trim. Shift+Right and Shift+Left go to
    /// the nearest of them after and before the playhead, and stay put
    /// when there is none.
    #[test]
    fn the_ruler_keys_the_frames_showing_marks(
        clips in cut_clips(),
        marked in prop::collection::btree_set((0usize..2, FIRST..FIRST + FRAMES), 0..12),
        here in 0usize..64,
    ) {
        let share = tempfile::tempdir().unwrap();
        let mut viewer = Viewer::cut(share.path(), (192, 108), &clips);
        for &(item, frame) in &marked {
            let Some(dir) = viewer.dir_of(ITEMS[item]) else { continue };
            let step = viewer.editor.review.stroke_step(&dir, frame, vec![files::Point { x: 0.5, y: 0.5, pressure: 1.0 }]);
            viewer.editor.review.apply(step);
        }
        let keys = viewer.showing_marks();
        prop_assert_eq!(viewer.editor.review_positions(), keys.clone());
        prop_assert_eq!(viewer.editor.tracks_program().reviews, keys.clone());

        let here = here.min(viewer.editor.end().saturating_sub(1));
        for (direction, want) in [(1, keys.iter().copied().find(|&p| p > here)), (-1, keys.iter().rev().copied().find(|&p| p < here))] {
            drop(viewer.editor.update(Message::Seek(here)));
            drop(viewer.editor.update(Message::Review(review::ReviewMessage::Jump(direction))));
            prop_assert_eq!(viewer.editor.position, want.unwrap_or(here), "jump {} from {}", direction, here);
        }
    }

    /// Ctrl+Z follows review mode: in it, it takes back review marks and
    /// never the edit's changes; out of it, the edit's changes and never a
    /// mark.
    #[test]
    fn undo_follows_review_mode(
        markers in prop::collection::btree_set(0usize..FRAMES as usize, 1..5),
        strokes in 1usize..5,
        undone in 0usize..5,
    ) {
        let undone = undone.min(strokes);
        let share = tempfile::tempdir().unwrap();
        let mut viewer = Viewer::new(share.path(), (192, 108));
        for &m in &markers {
            viewer.message(Message::Seek(m));
            viewer.message(Message::Marker);
        }
        prop_assert_eq!(viewer.editor.doc.edit.markers.len(), markers.len());
        viewer.message(Message::Seek(3));
        viewer.message(Message::Review(review::ReviewMessage::Mode(true)));
        for i in 0..strokes {
            let y = SIZE.height * (0.2 + 0.15 * i as f32);
            viewer.stroke(&[Point::new(SIZE.width * 0.3, y), Point::new(SIZE.width * 0.6, y)], None);
        }
        prop_assert_eq!(viewer.marks_here().len(), strokes);
        for _ in 0..undone {
            viewer.key(Key::Character('z'), Modifiers::COMMAND);
        }
        prop_assert_eq!(viewer.marks_here().len(), strokes - undone);
        prop_assert_eq!(viewer.editor.doc.edit.markers.len(), markers.len(), "review undo left the edit alone");

        viewer.message(Message::Review(review::ReviewMessage::Mode(false)));
        for _ in 0..markers.len() {
            viewer.key(Key::Character('z'), Modifiers::COMMAND);
        }
        prop_assert!(viewer.editor.doc.edit.markers.is_empty());
        prop_assert_eq!(viewer.marks_here().len(), strokes - undone, "edit undo left the marks alone");
    }
}
