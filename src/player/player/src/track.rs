//! The track under the video: a ruler over one clip per shot, each as long as
//! the shot. The ruler has the frame ticks, the frames already buffered (a
//! strip along its bottom) and the playhead. A clip shows the first frame of
//! the take it plays, the shot's name, the take and what it is (a playblast or
//! a render); the shots a change applies to are outlined.
//!
//! Pressing or dragging seeks. A plain click on a clip also clears the
//! selection, so a change applies to the playhead's shot again; Ctrl-click
//! (Cmd on macOS) adds a shot or takes it out, and Shift-click selects the run
//! of shots from the last one clicked.

use std::collections::BTreeSet;

use primer::ImageHandle;
use primer::Horizontal as H;
use primer::Vertical as V;
use primer::prelude::*;
use primer::widget::custom::{Cursor, Event, Status, keyboard, mouse};

/// The track's height: the ruler over the clips.
pub const HEIGHT: f32 = 86.0;
/// The ruler's height, buffered strip included.
const RULER: f32 = 18.0;
/// The buffered strip along the ruler's bottom.
const STRIP: f32 = 3.0;
/// Space around a clip's thumbnail and text.
const INSET: f32 = 4.0;
/// The narrowest thumbnail drawn; a clip with less room shows none.
const MIN_THUMBNAIL: f32 = 12.0;
/// The shape a thumbnail not made yet keeps room for.
const PLACEHOLDER: (u32, u32) = (16, 9);

/// One shot's clip.
#[derive(Debug, Clone)]
pub struct Segment {
    /// Its first timeline frame and how many it has.
    pub start: usize,
    pub len: usize,
    pub name: String,
    /// The take it plays ("lighting v0012") and what that is ("Render").
    pub take: String,
    pub kind: &'static str,
    /// The take's first frame, once it is made.
    pub thumbnail: Option<ImageHandle>,
    /// A change (a version, a department, playblast or render) applies to it.
    pub target: bool,
}

/// How a click on a clip changes the selection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pick {
    /// A plain click: nothing selected, so the playhead's shot is changed.
    Only,
    /// Ctrl-click (Cmd on macOS): add the shot, or take it out.
    Toggle,
    /// Shift-click: the run of shots from the last one clicked.
    Range,
}

impl Pick {
    fn of(modifiers: keyboard::Modifiers) -> Self {
        if modifiers.shift {
            Self::Range
        } else if modifiers.command() {
            Self::Toggle
        } else {
            Self::Only
        }
    }
}

/// The shots selected after `how` a click on `shot`, and the shot a later
/// Shift-click runs from. `playhead` is the playhead's shot: what a change
/// applies to while nothing is selected, so a Ctrl-click then adds to it.
pub fn pick(selection: &BTreeSet<usize>, anchor: Option<usize>, shot: usize, how: Pick, playhead: usize) -> (BTreeSet<usize>, Option<usize>) {
    match how {
        Pick::Only => (BTreeSet::new(), Some(shot)),
        Pick::Toggle => {
            let mut next = selection.clone();
            if next.is_empty() {
                next.extend([playhead, shot]);
            } else if !next.remove(&shot) {
                next.insert(shot);
            }
            (next, Some(shot))
        }
        Pick::Range => {
            let from = anchor.unwrap_or(playhead);
            ((from.min(shot)..=from.max(shot)).collect(), Some(from))
        }
    }
}

#[derive(Debug, Clone)]
pub struct Track<Message> {
    pub segments: Vec<Segment>,
    pub total: usize,
    /// Buffered frames as half-open timeline ranges.
    pub buffered: Vec<(usize, usize)>,
    pub playhead: usize,
    pub on_seek: fn(usize) -> Message,
    /// A click on a clip: the segment, the frame under the click, and how.
    pub on_pick: fn(usize, usize, Pick) -> Message,
    /// A right press on a clip: its segment, so the take menu around the
    /// track lists that shot's takes.
    pub on_menu: fn(usize) -> Message,
}

/// Runs of consecutive positions, from sorted `positions`.
pub fn ranges(positions: &[usize]) -> Vec<(usize, usize)> {
    let mut out: Vec<(usize, usize)> = Vec::new();
    for &p in positions {
        match out.last_mut() {
            Some((_, end)) if *end == p => *end = p + 1,
            _ => out.push((p, p + 1)),
        }
    }
    out
}

/// The timeline frame under local x `x` of a track `width` wide.
pub fn position_at(x: f32, width: f32, total: usize) -> usize {
    if total == 0 || width <= 0.0 {
        return 0;
    }
    let p = (x / width * total as f32).floor();
    (p.max(0.0) as usize).min(total - 1)
}

/// Frames between ticks when one frame is `slot` pixels wide: every frame
/// when they are far enough apart, else the smallest of 5, 10, 25, ... that
/// keeps ticks 6 pixels apart. Every fifth tick is drawn taller.
pub fn tick_step(slot: f32) -> usize {
    const MIN_GAP: f32 = 6.0;
    const STEPS: [usize; 7] = [1, 5, 10, 25, 50, 100, 250];
    if slot.is_nan() || slot <= 0.0 {
        return usize::MAX;
    }
    let mut step = STEPS.into_iter().find(|&s| s as f32 * slot >= MIN_GAP).unwrap_or(500);
    while (step as f32) * slot < MIN_GAP {
        step *= 2;
    }
    step
}

/// Where a `size` thumbnail goes in a clip spanning `x0..x1` of a track
/// `height` tall: at the clip's left, as tall as its content (aspect kept),
/// narrowed to fit a short clip. None when that leaves it too narrow to see.
pub fn thumbnail_rect(x0: f32, x1: f32, height: f32, size: (u32, u32)) -> Option<Rectangle> {
    let (w, h) = (size.0.max(1) as f32, size.1.max(1) as f32);
    let room = (x1 - x0 - 2.0 * INSET, height - RULER - 2.0 * INSET);
    let width = (room.1 * w / h).min(room.0);
    if width.is_nan() || width < MIN_THUMBNAIL {
        return None;
    }
    let tall = width * h / w;
    Some(Rectangle { x: x0 + INSET, y: RULER + (height - RULER - tall) / 2.0, width, height: tall })
}

#[derive(Debug, Default)]
pub struct Drag {
    /// Seeking with the pointer held down.
    active: bool,
    /// The modifier keys held: they decide what a click on a clip does.
    modifiers: keyboard::Modifiers,
}

impl<Message> Track<Message> {
    fn x(&self, position: usize, width: f32) -> f32 {
        position as f32 / self.total.max(1) as f32 * width
    }

    fn segment_at(&self, position: usize) -> Option<usize> {
        self.segments.iter().position(|s| (s.start..s.start + s.len).contains(&position))
    }
}

fn grey(level: u8) -> Color {
    Color::from_rgb8(level, level, level)
}

impl<Message> Program<Message> for Track<Message> {
    type State = Drag;

    // Neutral greys only: a tinted surround shifts how the image's colours
    // read. Drawn in passes (every clip's body, then every thumbnail, then the
    // text and outlines) so the canvas needs few paint bands, whatever the
    // number of shots.
    fn draw(&self, _state: &Drag, frame: &mut Frame, bounds: Rectangle, _cursor: Cursor) {
        let (w, h) = (bounds.width, bounds.height);
        let slot = w / self.total.max(1) as f32;
        let spans: Vec<(f32, f32)> = self.segments.iter().map(|s| (self.x(s.start, w), self.x(s.start + s.len, w))).collect();
        frame.fill_rectangle(Point::new(0.0, 0.0), Size::new(w, h), grey(26));
        frame.fill_rectangle(Point::new(0.0, 0.0), Size::new(w, RULER), grey(34));

        let step = tick_step(slot);
        for (segment, &(x0, _)) in self.segments.iter().zip(&spans) {
            for index in (step..segment.len).step_by(step) {
                let tick = if index % (step * 5) == 0 { 7.0 } else { 4.0 };
                frame.fill_rectangle(Point::new(self.x(segment.start + index, w), RULER - STRIP - tick), Size::new(1.0, tick), grey(90));
            }
            frame.fill_rectangle(Point::new(x0, 0.0), Size::new(1.0, RULER), grey(110));
        }
        for &(start, end) in &self.buffered {
            let (x0, x1) = (self.x(start, w), self.x(end, w));
            frame.fill_rectangle(Point::new(x0, RULER - STRIP), Size::new((x1 - x0).max(1.0), STRIP), Color::from_rgb8(70, 170, 110));
        }

        for (i, &(x0, x1)) in spans.iter().enumerate() {
            let body = if i % 2 == 0 { grey(52) } else { grey(44) };
            frame.fill_rectangle(Point::new(x0, RULER + 1.0), Size::new((x1 - x0 - 1.0).max(1.0), h - RULER - 1.0), body);
            if self.segments[i].thumbnail.is_none()
                && let Some(room) = thumbnail_rect(x0, x1, h, PLACEHOLDER)
            {
                frame.fill_rectangle(Point::new(room.x, room.y), Size::new(room.width, room.height), grey(36));
            }
        }
        for (segment, &(x0, x1)) in self.segments.iter().zip(&spans) {
            if let Some(handle) = &segment.thumbnail {
                let size = (handle.image().width, handle.image().height);
                if let Some(rect) = thumbnail_rect(x0, x1, h, size) {
                    frame.draw_image(handle, rect);
                }
            }
        }

        for (segment, &(x0, x1)) in self.segments.iter().zip(&spans) {
            let size = segment.thumbnail.as_ref().map_or(PLACEHOLDER, |t| (t.image().width, t.image().height));
            let left = thumbnail_rect(x0, x1, h, size).map_or(x0 + INSET, |r| r.x + r.width + INSET);
            let width = x1 - left - INSET;
            if width >= 8.0 {
                let line = |y: f32, tall: f32| Rectangle { x: left, y: RULER + INSET + y, width, height: tall };
                frame.fill_text_in(segment.name.clone(), line(0.0, 16.0), H::Left, V::Top, 13.0, grey(220));
                frame.fill_text_in(segment.take.clone(), line(18.0, 14.0), H::Left, V::Top, 11.0, grey(175));
                frame.fill_text_in(segment.kind.to_owned(), line(33.0, 14.0), H::Left, V::Top, 11.0, grey(135));
            }
            if segment.target {
                let (top, tall, wide) = (RULER + 1.0, h - RULER - 1.0, (x1 - x0 - 1.0).max(1.0));
                let outline = grey(205);
                frame.fill_rectangle(Point::new(x0, top), Size::new(wide, 2.0), outline);
                frame.fill_rectangle(Point::new(x0, h - 2.0), Size::new(wide, 2.0), outline);
                frame.fill_rectangle(Point::new(x0, top), Size::new(2.0, tall), outline);
                frame.fill_rectangle(Point::new(x0 + wide - 2.0, top), Size::new(2.0, tall), outline);
            }
        }

        // A thin line on the frame's centre and a cap on the ruler, so a short
        // shot's name stays readable under it.
        let centre = self.x(self.playhead, w) + slot / 2.0;
        let accent = Color::from_rgb8(240, 180, 60);
        frame.fill_rectangle(Point::new(centre - 1.0, 0.0), Size::new(2.0, h), accent);
        frame.fill_rectangle(Point::new(centre - 4.0, 0.0), Size::new(8.0, 3.0), accent);
    }

    fn update(&self, state: &mut Drag, event: &Event, bounds: Rectangle, cursor: Cursor) -> (Status, Option<Message>) {
        let seek = |x: f32| (self.on_seek)(position_at(x, bounds.width, self.total));
        match event {
            Event::Keyboard(keyboard::Event::ModifiersChanged(modifiers)) => {
                state.modifiers = *modifiers;
                (Status::Ignored, None)
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => match cursor.position_relative_to(bounds) {
                Some(local) if local.y < RULER => {
                    state.active = true;
                    (Status::Captured, Some(seek(local.x)))
                }
                Some(local) => {
                    let position = position_at(local.x, bounds.width, self.total);
                    let Some(segment) = self.segment_at(position) else {
                        return (Status::Captured, None);
                    };
                    let how = Pick::of(state.modifiers);
                    // A plain click seeks, and dragging on from it scrubs; a
                    // click that selects leaves the playhead where it is.
                    state.active = how == Pick::Only;
                    (Status::Captured, Some((self.on_pick)(segment, position, how)))
                }
                None => (Status::Ignored, None),
            },
            // A right press on a clip names its shot to the take menu around
            // the track, and lets the press on so the menu opens. On the
            // ruler it opens none.
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) => match cursor.position_relative_to(bounds) {
                Some(local) if local.y >= RULER => match self.segment_at(position_at(local.x, bounds.width, self.total)) {
                    Some(segment) => (Status::Ignored, Some((self.on_menu)(segment))),
                    None => (Status::Captured, None),
                },
                Some(_) => (Status::Captured, None),
                None => (Status::Ignored, None),
            },
            Event::Mouse(mouse::Event::CursorMoved { position }) if state.active => (Status::Captured, Some(seek(position.x - bounds.x))),
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) if state.active => {
                state.active = false;
                (Status::Captured, None)
            }
            _ => (Status::Ignored, None),
        }
    }

    fn mouse_interaction(&self, _state: &Drag, bounds: Rectangle, cursor: Cursor) -> mouse::Interaction {
        if cursor.position_relative_to(bounds).is_some() { mouse::Interaction::Pointer } else { mouse::Interaction::Idle }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn ranges_cover_exactly_the_positions(positions in prop::collection::btree_set(0usize..500, 0..200)) {
            let positions: Vec<usize> = positions.into_iter().collect();
            let runs = ranges(&positions);
            let covered: Vec<usize> = runs.iter().flat_map(|&(a, b)| a..b).collect();
            prop_assert_eq!(&covered, &positions);
            // Maximal runs: no two touch.
            for pair in runs.windows(2) {
                prop_assert!(pair[0].1 < pair[1].0);
            }
        }

        /// Ticks never crowd closer than 6 pixels, and a frame wide enough
        /// gets a tick of its own.
        #[test]
        fn ticks_keep_their_distance(slot in 0.001f32..100.0) {
            let step = tick_step(slot);
            prop_assert!(step as f32 * slot >= 6.0);
            if slot >= 6.0 {
                prop_assert_eq!(step, 1);
            }
        }

        /// Every frame is reachable by clicking, and clicks map in order.
        #[test]
        fn position_at_is_monotonic_and_onto(total in 1usize..5000, width in 50f32..4000.0, a in 0f32..1.0, b in 0f32..1.0) {
            let (pa, pb) = (position_at(a * width, width, total), position_at(b * width, width, total));
            prop_assert!(pa < total && pb < total);
            if a <= b {
                prop_assert!(pa <= pb);
            }
            prop_assert_eq!(position_at(-10.0, width, total), 0);
            prop_assert_eq!(position_at(width + 10.0, width, total), total - 1);
            // The centre of frame p's slot maps back to p.
            let p = ((a * total as f32) as usize).min(total - 1);
            let centre = (p as f32 + 0.5) / total as f32 * width;
            prop_assert_eq!(position_at(centre, width, total), p);
        }

        /// A thumbnail stays inside its clip, below the ruler, keeps the
        /// frame's shape, and is left out only when it would be too narrow.
        #[test]
        fn thumbnails_fit_their_clips(x0 in 0f32..2000.0, len in 0f32..400.0, size in (1u32..4096, 1u32..4096)) {
            let x1 = x0 + len;
            let (w, h) = (size.0 as f32, size.1 as f32);
            let full = (HEIGHT - RULER - 2.0 * INSET) * w / h;
            match thumbnail_rect(x0, x1, HEIGHT, size) {
                Some(r) => {
                    let slack = 1e-3 * (1.0 + x1);
                    prop_assert!(r.x >= x0 + INSET - slack && r.x + r.width <= x1 - INSET + slack, "{r:?} in {x0}..{x1}");
                    prop_assert!(r.y >= RULER + INSET - 1e-3 && r.y + r.height <= HEIGHT - INSET + 1e-3, "{r:?}");
                    prop_assert!((r.width * h - r.height * w).abs() <= 1e-3 * r.width * h, "{r:?} for {size:?}");
                    prop_assert!(r.width >= MIN_THUMBNAIL);
                }
                None => prop_assert!(full.min(len - 2.0 * INSET) < MIN_THUMBNAIL),
            }
        }

        /// A plain click clears the selection; a Ctrl-click flips one shot
        /// (adding it to the playhead's when nothing was selected); a
        /// Shift-click selects exactly the run from the anchor. Each but a
        /// Shift-click anchors on the shot clicked.
        #[test]
        fn picks_change_the_selection_as_asked(
            selection in prop::collection::btree_set(0usize..20, 0..6),
            anchor in prop::option::of(0usize..20),
            shot in 0usize..20,
            playhead in 0usize..20,
            how in prop::sample::select(vec![Pick::Only, Pick::Toggle, Pick::Range]),
        ) {
            let (next, next_anchor) = pick(&selection, anchor, shot, how, playhead);
            match how {
                Pick::Only => {
                    prop_assert!(next.is_empty());
                    prop_assert_eq!(next_anchor, Some(shot));
                }
                Pick::Toggle => {
                    let before = if selection.is_empty() { BTreeSet::from([playhead]) } else { selection.clone() };
                    let others = |s: &BTreeSet<usize>| s.iter().copied().filter(|&x| x != shot).collect::<BTreeSet<_>>();
                    prop_assert_eq!(others(&next), others(&before));
                    let keeps_shot = selection.is_empty() || !selection.contains(&shot);
                    prop_assert_eq!(next.contains(&shot), keeps_shot);
                    prop_assert_eq!(next_anchor, Some(shot));
                }
                Pick::Range => {
                    let from = anchor.unwrap_or(playhead);
                    prop_assert_eq!(next_anchor, Some(from));
                    prop_assert!(next.contains(&from) && next.contains(&shot));
                    let run: Vec<usize> = next.iter().copied().collect();
                    prop_assert!(run.windows(2).all(|p| p[1] == p[0] + 1), "{run:?}");
                    prop_assert_eq!(run.len(), from.abs_diff(shot) + 1);
                }
            }
        }
    }

    #[test]
    fn modifiers_choose_the_pick() {
        let mut modifiers = keyboard::Modifiers::default();
        assert_eq!(Pick::of(modifiers), Pick::Only);
        modifiers.shift = true;
        assert_eq!(Pick::of(modifiers), Pick::Range);
        modifiers = keyboard::Modifiers::COMMAND;
        assert_eq!(Pick::of(modifiers), Pick::Toggle);
    }
}
