//! The timeline as the window has it: the strip under the tracks
//! (`tracks::overview`) and the audio clips' waveforms (`waves`). The
//! editor's whole view is laid out at a window's size, sent mouse events
//! at pixels and, between them, drawn and its widget state pruned as the
//! runtime does after every message (a widget state no draw reads is
//! dropped then). The strip's own rules are tested in its module; this is
//! of it in the window, where a thumb that let go of the pointer after the
//! first redraw once made the bar look dead (2026-11-22 RFE).

#![allow(clippy::float_cmp)]

use primer::core::clipboard::Clipboard;
use primer::core::measure::{TextLayout, TextLine, TextMeasurer, VisualLines};
use primer::core::render::TextSpan;
use primer::widget::custom::{Ctx, Cursor, Event, Layout as Placed, LayoutCtx, Limits, Shell, StateStore, Widget, mouse};

use super::*;
use crate::tracks::overview;

pub(super) struct FixedText;

impl TextMeasurer for FixedText {
    fn measure_text(&self, content: &str, size: f32, _: Option<f32>) -> Size {
        Size::new(content.chars().count() as f32 * size * 0.5, size * 1.3)
    }
    fn measure_styled(&self, content: &str, _: &[TextSpan], size: f32, max: Option<f32>) -> Size {
        self.measure_text(content, size, max)
    }
    fn layout_text(&self, content: &str, size: f32, _: Option<f32>) -> TextLayout {
        let count = content.chars().count();
        let carets = (0..=count).map(|c| c as f32 * size * 0.5).collect();
        let line = TextLine { start: 0, end: count, width: count as f32 * size * 0.5, carets };
        TextLayout { lines: VisualLines::new(vec![line]), size: self.measure_text(content, size, None) }
    }
}

#[derive(Default)]
pub(super) struct Kept(Vec<String>);

impl Clipboard for Kept {
    fn read(&mut self) -> Option<String> {
        self.0.last().cloned()
    }
    fn write(&mut self, contents: String) {
        self.0.push(contents);
    }
}

const WINDOW: Size = Size { width: 1600.0, height: 1000.0 };

struct Window {
    editor: Editor,
    store: StateStore,
    clipboard: Kept,
    _prefs: tempfile::TempDir,
}

impl Window {
    fn new(edit: &WorkingEdit) -> Self {
        let prefs = tempfile::tempdir().expect("a folder for the prefs");
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let ocio = root.join("_config/ocio/tumblehead.ocio").to_string_lossy().replace('\\', "/");
        let colour = Colour::open(&ocio).expect("the template's OCIO config");
        let layout = prefs.path().join("layout.json");
        let editor = Editor::with(paths, ocio, Document::new(edit.clone(), Origin::Untitled), colour, None, None, Some(layout));
        let mut window = Window { editor, store: StateStore::new(), clipboard: Kept::default(), _prefs: prefs };
        window.draw(Point::new(0.0, 0.0));
        window
    }

    /// One frame drawn and the store pruned, as after every message.
    fn draw(&mut self, at: Point) {
        let theme = Theme::dark();
        let view = self.editor.view(window::Id::MAIN);
        let node = view.layout(&Limits::new(Size::ZERO, WINDOW), LayoutCtx { measurer: &FixedText, store: &self.store, theme: &theme });
        self.store.begin_draw();
        let ctx = Ctx {
            cursor: Cursor::Available(at),
            theme: &theme,
            store: &self.store,
            measurer: &FixedText,
            visible: Rectangle::new(Point::new(0.0, 0.0), WINDOW),
        };
        drop(view.draw(Placed::new(&node), ctx));
        self.store.prune();
    }

    /// `event` with the pointer at `at`; the editor is given what was
    /// published, then the window draws.
    fn send(&mut self, event: Event, at: Point) -> Vec<String> {
        let theme = Theme::dark();
        let mut messages = Vec::new();
        {
            let mut view = self.editor.view(window::Id::MAIN);
            let node = view.layout(&Limits::new(Size::ZERO, WINDOW), LayoutCtx { measurer: &FixedText, store: &self.store, theme: &theme });
            let mut shell =
                Shell::new(&mut messages, &mut self.store, &FixedText, &mut self.clipboard, &theme, Cursor::Available(at), Modifiers::NONE);
            view.on_event(&event, Placed::new(&node), &mut shell);
        }
        let said = messages.iter().map(|m| format!("{m:?}")).collect();
        for message in messages {
            drop(self.editor.update(message));
        }
        self.draw(at);
        said
    }

    fn press(&mut self, at: Point) -> Vec<String> {
        self.send(Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), at)
    }
    fn move_to(&mut self, at: Point) -> Vec<String> {
        self.send(Event::Mouse(mouse::Event::CursorMoved { position: at }), at)
    }
    fn release(&mut self, at: Point) -> Vec<String> {
        self.send(Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), at)
    }

    /// The strip's row on screen: the lowest y a press off the thumb
    /// scrolls from, found by pressing up from the bottom.
    fn strip_y(&mut self, x: f32) -> f32 {
        let mut y = WINDOW.height - 1.0;
        while y > 0.0 {
            let said = self.press(Point::new(x, y));
            self.release(Point::new(x, y));
            if said.iter().any(|s| s.starts_with("ScrollTo") || s.starts_with("ShowFrames")) {
                return y;
            }
            y -= 2.0;
        }
        panic!("no strip under the tracks answers a press");
    }
}

fn long_cut() -> WorkingEdit {
    let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
    let clips = (0..10).map(|i| th_edit::document::VideoClip::new(i * 100, 100, 0, Source::Slug { text: format!("s{i}") })).collect();
    edit.video.push(th_edit::document::Track { name: "V1".into(), clips, color: None });
    edit
}

/// Zoomed in to a quarter of the timeline: the thumb dragged across the
/// strip scrolls on every move of the drag, not only the first.
#[test]
fn the_thumb_dragged_scrolls_on_every_move() {
    let mut window = Window::new(&long_cut());
    window.editor.timeline_view = window.editor.timeline_view.zoomed(0.0, 4.0);
    window.draw(Point::new(0.0, 0.0));
    // Find the strip right of the thumb, then put the view back at 0.
    let y = window.strip_y(WINDOW.width - 40.0);
    window.editor.timeline_view = window.editor.timeline_view.scrolled_to(0.0);
    window.draw(Point::new(0.0, 0.0));
    let view = window.editor.timeline_view;
    let (x0, x1) = overview::thumb(view.span(), view.first(), view.shown(), WINDOW.width);
    let grab = Point::new((x0 + x1) / 2.0, y);
    window.press(grab);
    let mut last = window.editor.timeline_view.first();
    for step in 1..=10 {
        window.move_to(Point::new(grab.x + step as f32 * 20.0, y + 30.0));
        let first = window.editor.timeline_view.first();
        assert!(first > last, "move {step} of the drag did not scroll ({first} after {last})");
        last = first;
    }
    window.release(Point::new(grab.x + 200.0, y));
    window.move_to(Point::new(grab.x + 400.0, y));
    assert_eq!(window.editor.timeline_view.first(), last, "the thumb let go still scrolls");
}

/// A press on the strip off the thumb pages: the view moves by what it
/// shows, towards the press.
#[test]
fn a_press_off_the_thumb_pages_towards_it() {
    let mut window = Window::new(&long_cut());
    window.editor.timeline_view = window.editor.timeline_view.zoomed(0.0, 4.0);
    window.draw(Point::new(0.0, 0.0));
    let y = window.strip_y(WINDOW.width - 40.0);
    window.editor.timeline_view = window.editor.timeline_view.scrolled_to(0.0);
    window.draw(Point::new(0.0, 0.0));
    let shown = window.editor.timeline_view.shown() as f64;
    window.press(Point::new(WINDOW.width - 40.0, y));
    window.release(Point::new(WINDOW.width - 40.0, y));
    assert_eq!(window.editor.timeline_view.first(), shown);
}

/// The thumb's right end dragged out in the window, over several moves,
/// shows more frames from the same first frame.
#[test]
fn the_thumbs_end_dragged_shows_more_frames() {
    let mut window = Window::new(&long_cut());
    window.editor.timeline_view = window.editor.timeline_view.zoomed(0.0, 4.0);
    window.draw(Point::new(0.0, 0.0));
    let y = window.strip_y(WINDOW.width - 40.0);
    window.editor.timeline_view = window.editor.timeline_view.scrolled_to(0.0);
    window.draw(Point::new(0.0, 0.0));
    let before = window.editor.timeline_view;
    let (_, x1) = overview::thumb(before.span(), before.first(), before.shown(), WINDOW.width);
    window.press(Point::new(x1 - 1.0, y));
    let mut last = before.shown();
    for step in 1..=5 {
        window.move_to(Point::new(x1 + step as f32 * 30.0, y));
        let now = window.editor.timeline_view;
        assert_eq!(now.first(), 0.0, "the head moved");
        assert!(now.shown() > last, "move {step} of the end did not widen the view");
        last = now.shown();
    }
    window.release(Point::new(x1 + 150.0, y));
}

/// An audio clip shows a placeholder while its source resolves, then its
/// sound's peaks placed through its in point and gain; a source with no
/// sound shows none. The window draws them.
#[test]
fn an_audio_clip_shows_its_waveform_once_its_sound_is_read() {
    use std::sync::Arc;

    use th_media::sound::Sound;

    use crate::waveform::{Peaks, Wave};

    let mut edit = long_cut();
    let music = Source::Media { item: "music".into(), version: th_edit::VersionRule::Latest };
    let clip =
        th_edit::document::AudioClip { at: 24, duration: 96, source_in: 12, source: music.clone(), gain_db: -6.0, fade_in: 0, fade_out: 0 };
    edit.audio.push(th_edit::document::Track { name: "A1".into(), clips: vec![clip], color: None });
    let mut window = Window::new(&edit);
    // The sources are resolved as the window's start does (the resolves
    // themselves are not run here: their answer is given below).
    drop(window.editor.resolve_new());
    let track = TrackRef::audio(0);
    assert!(matches!(window.editor.clip_wave(track, 0), Some(Wave::Pending)), "no placeholder while resolving");

    let index = window.editor.entry_of(&music).expect("the clip's source is an entry");
    let sound = Sound { rate: 48_000, samples: (0..96_000).map(|i| ((i as f32) * 0.01).sin()).collect() };
    let mut resolved = Resolved::nothing("");
    resolved.nothing = None;
    resolved.peaks = Some(Arc::new(Peaks::of(&sound)));
    resolved.sound = Some(Arc::new(sound));
    drop(window.editor.update(Message::Resolved(index, Ok(resolved))));
    window.draw(Point::new(0.0, 0.0));
    let Some(Wave::Ready(placed)) = window.editor.clip_wave(track, 0) else { panic!("no waveform once the sound is read") };
    assert_eq!((placed.at, placed.duration), (24, 96));
    assert!((placed.offset - 12.0 / 24.0).abs() < 1e-9, "the in point is not where the waveform starts");
    assert!((placed.gain - 0.501).abs() < 1e-3, "the gain is not the clip's");
    assert!(window.editor.clip_wave(TrackRef::video(0), 0).is_none(), "a video clip drew a waveform");

    let mut silent = Resolved::nothing("");
    silent.nothing = None;
    drop(window.editor.update(Message::Resolved(index, Ok(silent))));
    assert!(window.editor.clip_wave(track, 0).is_none(), "a source with no sound drew a waveform");
}
