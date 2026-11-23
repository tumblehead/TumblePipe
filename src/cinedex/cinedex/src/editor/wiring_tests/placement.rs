//! Where the view's switches and the timer are drawn, in the whole window
//! laid out: Live and Burn-ins on the player, over the picture, and the
//! timer on the timeline, at its left, beside what each affects (Remy,
//! 2026-11-21: in the toolbar's corner they were a look across the window
//! away from the picture and the playhead).

use primer::core::render::{Primitive, RenderNode, Text};
use primer::widget::custom::{Ctx, Layout, LayoutCtx, Limits, StateStore, Widget};

use super::*;

/// Each character as wide as its code point says, in odd fractions of the
/// font size (so no two words line up by luck), one line high.
struct Odd;

impl primer::core::measure::TextMeasurer for Odd {
    fn measure_text(&self, content: &str, size: f32, max_width: Option<f32>) -> Size {
        self.layout_text(content, size, max_width).size
    }
    fn measure_styled(&self, content: &str, _: &[primer::core::render::TextSpan], size: f32, max_width: Option<f32>) -> Size {
        self.measure_text(content, size, max_width)
    }
    fn layout_text(&self, content: &str, size: f32, _: Option<f32>) -> primer::core::measure::TextLayout {
        use primer::core::measure::{TextLayout, TextLine, VisualLines, line_height};
        let advance = |c: char| size * (0.3 + (u32::from(c) % 17) as f32 * 0.0371);
        let mut carets = vec![0.0];
        for c in content.chars() {
            carets.push(carets.last().copied().unwrap_or(0.0) + advance(c));
        }
        let width = carets.last().copied().unwrap_or(0.0);
        TextLayout {
            lines: VisualLines::new(vec![TextLine { start: 0, end: content.chars().count(), width, carets }]),
            size: Size::new(width, line_height(size)),
        }
    }
}

/// Text that wraps, as the real measurer's does: a fixed advance per
/// character, lines broken at the last space that fits (or mid-word, for a
/// word longer than the line).
struct Wrapping;

impl primer::core::measure::TextMeasurer for Wrapping {
    fn measure_text(&self, content: &str, size: f32, max_width: Option<f32>) -> Size {
        self.layout_text(content, size, max_width).size
    }
    fn measure_styled(&self, content: &str, _: &[primer::core::render::TextSpan], size: f32, max_width: Option<f32>) -> Size {
        self.measure_text(content, size, max_width)
    }
    fn layout_text(&self, content: &str, size: f32, max_width: Option<f32>) -> primer::core::measure::TextLayout {
        use primer::core::measure::{TextLayout, TextLine, VisualLines, line_height};
        let advance = size * 0.55;
        let chars: Vec<char> = content.chars().collect();
        let fit = max_width.map_or(usize::MAX, |m| ((m / advance).floor() as usize).max(1));
        let mut lines = Vec::new();
        let mut start = 0;
        while start < chars.len() || lines.is_empty() {
            let mut end = (start + fit).min(chars.len());
            if end < chars.len()
                && let Some(space) = chars[start..end].iter().rposition(|&c| c == ' ').filter(|&i| i > 0)
            {
                end = start + space + 1;
            }
            let carets: Vec<f32> = (0..=end - start).map(|i| i as f32 * advance).collect();
            lines.push(TextLine { start, end, width: (end - start) as f32 * advance, carets });
            start = end;
        }
        let width = lines.iter().map(|l| l.width).fold(0.0, f32::max);
        let height = line_height(size) * lines.len() as f32;
        TextLayout { lines: VisualLines::new(lines), size: Size::new(width, height) }
    }
}

/// A word as the window draws it, and the part of the window it may show
/// in: the window cut down by every clip it is drawn inside.
struct Drawn {
    text: Text,
    room: Option<Rectangle>,
}

fn drawn(node: &RenderNode, room: Option<Rectangle>, out: &mut Vec<Drawn>) {
    match node {
        RenderNode::Leaf(Primitive::Text(text)) => out.push(Drawn { text: text.clone(), room }),
        RenderNode::Clip { bounds, child } => drawn(child, room.and_then(|r| r.intersection(*bounds)), out),
        RenderNode::Group { children, .. } => children.iter().for_each(|child| drawn(child, room, out)),
        RenderNode::Overlay { child }
        | RenderNode::Opacity { child, .. }
        | RenderNode::Transform { child, .. }
        | RenderNode::Layer { child } => {
            drawn(child, room, out);
        }
        RenderNode::Leaf(_) => {}
    }
}

/// The window's parts, where they were laid out.
struct Window {
    toolbar: Rectangle,
    /// The viewer: the player's header, the picture and its bars (Cut
    /// only).
    player: Option<Rectangle>,
    /// The timeline, `None` while it is folded away.
    timeline: Option<Rectangle>,
    words: Vec<Drawn>,
}

/// The editor's whole window laid out `w` by `h` and drawn.
fn window(editor: &Editor, w: f32, h: f32) -> Window {
    window_measured(editor, w, h, &Odd)
}

/// The window laid out and drawn with `measurer`'s text.
fn window_measured(editor: &Editor, w: f32, h: f32, measurer: &dyn primer::core::measure::TextMeasurer) -> Window {
    let view = Application::view(editor, window::Id::MAIN);
    let (store, theme) = (StateStore::new(), Theme::dark());
    let node = view.layout(&Limits::new(Size::ZERO, Size::new(w, h)), LayoutCtx { measurer, store: &store, theme: &theme });
    let root = Layout::new(&node);
    // The modal over the page; the page a column of the toolbar, the body
    // and the status line; the body the timeline split.
    let page = root.children().next().expect("the page under the modal");
    let parts: Vec<Layout> = page.children().collect();
    assert_eq!(parts.len(), 3, "toolbar, body, status line");
    let body: Vec<Layout> = parts[1].children().collect();
    assert_eq!(body.len(), 2, "what is above the timeline, and the timeline");
    let player = (editor.mode == 0).then(|| {
        let above: Vec<Layout> = body[0].children().collect();
        assert_eq!(above.len(), 2, "the bins and the viewer");
        above[1].bounds()
    });
    let timeline = (!editor.layout.timeline_collapsed).then(|| body[1].bounds());
    let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer, visible: node.bounds() };
    let mut words = Vec::new();
    drawn(&view.draw(root, ctx), Some(Rectangle { x: 0.0, y: 0.0, width: w, height: h }), &mut words);
    Window { toolbar: parts[0].bounds(), player, timeline, words }
}

fn inside(inner: Rectangle, outer: Rectangle) -> bool {
    const EPS: f32 = 1e-3;
    inner.x >= outer.x - EPS
        && inner.y >= outer.y - EPS
        && inner.x + inner.width <= outer.x + outer.width + EPS
        && inner.y + inner.height <= outer.y + outer.height + EPS
}

/// Two boxes sharing more than an edge.
fn overlap(a: Rectangle, b: Rectangle) -> bool {
    const EPS: f32 = 1e-3;
    a.x + EPS < b.x + b.width && b.x + EPS < a.x + a.width && a.y + EPS < b.y + b.height && b.y + EPS < a.y + a.height
}

impl Window {
    /// The words drawn that `want` picks.
    fn find(&self, want: impl Fn(&Text) -> bool) -> Vec<&Drawn> {
        self.words.iter().filter(|d| want(&d.text)).collect()
    }

    /// `word` is drawn whole, inside the room it is given, over no other
    /// word where that word shows (a word scrolled out under a clip is not
    /// there).
    fn whole_and_alone(&self, word: &Drawn) -> Result<(), TestCaseError> {
        let at = word.text.bounds;
        let room = word.room.ok_or_else(|| TestCaseError::fail(format!("{:?} is clipped away", word.text.content)))?;
        prop_assert!(inside(at, room), "{:?} at {:?} is cut off by {:?}", word.text.content, at, room);
        for other in &self.words {
            if std::ptr::eq(other, word) || other.text.content.trim().is_empty() {
                continue;
            }
            let Some(shown) = other.room.and_then(|r| r.intersection(other.text.bounds)) else { continue };
            prop_assert!(!overlap(at, shown), "{:?} at {:?} over {:?} at {:?}", word.text.content, at, other.text.content, shown);
        }
        Ok(())
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(48))]

    /// In Cut, Live and Burn-ins are drawn once each, in the player's strip
    /// over the picture, whole and over nothing else, and not in the
    /// toolbar; in Board, where no picture is shown, neither is drawn. The
    /// timer (the playhead's timecode, large) is drawn once: on the
    /// timeline, inside it and not in the toolbar, while the timeline is
    /// open; in the toolbar while it is folded away. Whatever the cut and
    /// the playhead, the bins folded or not, and the window from narrow
    /// (the bins and the viewer at their narrowest) to wide.
    #[test]
    fn the_switches_sit_on_the_player_and_the_timer_on_the_timeline(
        edit in cut(),
        board in any::<bool>(),
        bins_collapsed in any::<bool>(),
        timeline_collapsed in any::<bool>(),
        at in 0usize..200,
        w in prop_oneof![520f32..800.0, 800f32..2600.0],
        h in 600f32..1600.0,
    ) {
        let mut bench = Bench::new(&edit, false);
        bench.editor.layout.bins_collapsed = bins_collapsed;
        bench.editor.layout.timeline_collapsed = timeline_collapsed;
        bench.editor.mode = usize::from(board);
        bench.send(Message::Seek(at.min(bench.editor.end())));
        let shown = bench.editor.position as EditFrame;
        let timecode = editing::timecode(shown, bench.edit());
        let window = window(&bench.editor, w, h);

        for name in ["Live", "Burn-ins"] {
            let found = window.find(|t| t.content == name);
            match window.player {
                None => prop_assert!(found.is_empty(), "{} is drawn in Board, at {:?}", name, found.iter().map(|d| d.text.bounds).collect::<Vec<_>>()),
                Some(player) => {
                    prop_assert_eq!(found.len(), 1, "{} drawn {} times", name, found.len());
                    let at = found[0].text.bounds;
                    let strip = Rectangle { height: PLAYER_HEADER_HEIGHT, ..player };
                    prop_assert!(inside(at, strip), "{} at {:?} is not in the player's strip {:?}", name, at, strip);
                    prop_assert!(!overlap(at, window.toolbar), "{} at {:?} is in the toolbar {:?}", name, at, window.toolbar);
                    window.whole_and_alone(found[0])?;
                }
            }
        }

        let timers = window.find(|t| t.content == timecode && t.size == TIMER_SIZE);
        prop_assert_eq!(timers.len(), 1, "the timer {:?} drawn {} times", timecode, timers.len());
        let at = timers[0].text.bounds;
        match window.timeline {
            Some(timeline) => {
                let header = Rectangle { height: panels::CLIP_FIELDS_HEIGHT, ..timeline };
                prop_assert!(inside(at, header), "the timer at {:?} is not in the timeline's top row {:?}", at, header);
                prop_assert!(!overlap(at, window.toolbar), "the timer at {:?} is in the toolbar {:?}", at, window.toolbar);
                // At the timeline's left, over the tracks' names.
                prop_assert!(at.x < timeline.x + tracks::HEADER, "the timer at {:?} is not at the timeline's left {:?}", at, timeline);
            }
            None => prop_assert!(inside(at, window.toolbar), "the timer at {:?} is not in the toolbar {:?}", at, window.toolbar),
        }
        window.whole_and_alone(timers[0])?;
    }

    /// In review mode the review panel stands at the viewer's right, its
    /// whole width, beside the picture and not over it: its title, Done and
    /// Send each drawn once, whole, in it, at any viewer height (what is
    /// between them scrolls); Live
    /// and Burn-ins stay on the player's strip, left of it.
    #[test]
    fn the_review_panel_stands_at_the_viewers_right(
        edit in cut(),
        bins_collapsed in any::<bool>(),
        timeline_collapsed in any::<bool>(),
        w in 1000f32..2600.0,
        h in 600f32..1600.0,
    ) {
        let mut bench = Bench::new(&edit, false);
        bench.editor.layout.bins_collapsed = bins_collapsed;
        bench.editor.layout.timeline_collapsed = timeline_collapsed;
        bench.send(Message::Review(crate::editor::review::ReviewMessage::Mode(true)));
        let window = window(&bench.editor, w, h);
        let player = window.player.expect("the viewer in Cut");
        let panel = Rectangle { x: player.x + player.width - crate::editor::review::PANEL_WIDTH, width: crate::editor::review::PANEL_WIDTH, ..player };
        for name in ["Review", "Done", "Send"] {
            let found = window.find(|t| t.content == name);
            prop_assert_eq!(found.len(), 1, "{} drawn {} times", name, found.len());
            prop_assert!(inside(found[0].text.bounds, panel), "{} at {:?} is not in the panel {:?}", name, found[0].text.bounds, panel);
            window.whole_and_alone(found[0])?;
        }
        let comments = window.find(|t| t.content.starts_with("Comments"));
        // In a low viewer it is scrolled out of sight, never out of the panel.
        prop_assert!(comments.len() <= 1);
        for c in comments {
            prop_assert!(inside(c.text.bounds, panel), "the comments' heading at {:?} is not in the panel {:?}", c.text.bounds, panel);
        }
        for name in ["Live", "Burn-ins"] {
            let found = window.find(|t| t.content == name);
            prop_assert_eq!(found.len(), 1, "{} drawn {} times", name, found.len());
            prop_assert!(!overlap(found[0].text.bounds, panel), "{} at {:?} is in the panel {:?}", name, found[0].text.bounds, panel);
        }
    }

    /// However many comments a frame has, however long (a word too long
    /// for a line among them), the panel keeps its title, Done and Send
    /// whole in it, and every comment drawn stays within its width.
    #[test]
    fn a_long_conversation_stays_in_the_panel(
        texts in prop::collection::vec(prop_oneof![
            "[a-z]{1,12}( [a-z]{1,12}){0,60}",
            "[a-z]{40,120}",
        ], 0..14),
        w in 1000f32..2600.0,
        h in 600f32..1600.0,
    ) {
        let share = tempfile::tempdir().unwrap();
        let mut viewer = crate::editor::review_wiring_tests::Viewer::new(share.path(), (192, 108));
        let editor = &mut viewer.editor;
        drop(editor.update(Message::Review(crate::editor::review::ReviewMessage::Mode(true))));
        let target = editor.review_target().expect("a take to review");
        for t in &texts {
            editor.review.draft = crate::review::Draft { text: t.clone(), editing: None };
            let step = editor.review.note_step(&target.dir, target.frame);
            editor.review.apply(step);
        }
        editor.review.draft = crate::review::Draft::default();
        prop_assert_eq!(editor.review_comments().len(), texts.len());
        let window = window_measured(editor, w, h, &Wrapping);
        let player = window.player.expect("the viewer in Cut");
        let panel = Rectangle { x: player.x + player.width - crate::editor::review::PANEL_WIDTH, width: crate::editor::review::PANEL_WIDTH, ..player };
        for name in ["Review", "Done", "Send"] {
            let found = window.find(|t| t.content == name);
            prop_assert_eq!(found.len(), 1, "{} drawn {} times", name, found.len());
            prop_assert!(inside(found[0].text.bounds, panel), "{} at {:?} is not in the panel {:?}", name, found[0].text.bounds, panel);
            window.whole_and_alone(found[0])?;
        }
        for t in &texts {
            for d in window.find(|x| x.content == *t) {
                let b = d.text.bounds;
                prop_assert!(b.x >= panel.x - 1e-3 && b.x + b.width <= panel.x + panel.width + 1e-3, "a comment at {:?} spills out of the panel {:?}", b, panel);
                if let Some(room) = d.room {
                    prop_assert!(room.x >= panel.x - 1e-3 && room.x + room.width <= panel.x + panel.width + 1e-3, "a comment's room {:?} spills out of the panel {:?}", room, panel);
                }
            }
        }
    }
}
