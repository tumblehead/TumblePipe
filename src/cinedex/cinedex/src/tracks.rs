//! The editor's tracks under the video: a ruler (frame ticks, markers, the
//! frames buffered, the playhead) over one row per track, video tracks top
//! track on top, then the audio tracks. Each clip shows its source's first
//! frame, its name and its take, its fades as ramps, and an outline when it
//! is selected.
//!
//! Pressing or dragging on the ruler, or on empty track, seeks (empty track
//! also clears the selection). On a clip:
//! - a click selects it (Shift or Ctrl/Cmd adds it or takes it out);
//! - dragging its body moves it (to the track under the pointer, of its
//!   kind), with Alt it slips (the clip stays, its source frames move);
//! - dragging an edge trims it with ripple (what follows moves with it),
//!   with Alt it rolls the cut with its neighbour (the length holds);
//! - dragging its top band sets its fade in (left half) or out (right half).
//!
//! A drag becomes one `th_edit` op when it is let go (see [`op_for`]); until
//! then its outline shows where it goes. A right press names the clip to the
//! take menu around the tracks.

use primer::Horizontal as H;
use primer::ImageHandle;
use primer::Vertical as V;
use primer::prelude::*;
use primer::widget::custom::{Cursor, Event, Status, keyboard, mouse};
use th_edit::document::{ClipRef, Frame as EditFrame, Source, TrackKind, TrackRef};
use th_edit::ops::{Edge, Op};

use crate::look;

/// The ruler's height, buffered strip included.
pub const RULER: f32 = 20.0;
/// One track's height.
pub const ROW: f32 = 46.0;
/// The column of track names on the left.
pub const HEADER: f32 = 56.0;
/// How near an edge (pixels) a press trims rather than moves.
const EDGE: f32 = 6.0;
/// The band along a clip's top that sets its fades.
const FADE_BAND: f32 = 9.0;
/// The buffered strip along the ruler's bottom.
const STRIP: f32 = 3.0;
const INSET: f32 = 3.0;

/// One clip as the tracks show it.
#[derive(Debug, Clone)]
pub struct ClipView {
    pub at: EditFrame,
    pub duration: EditFrame,
    pub fade_in: EditFrame,
    pub fade_out: EditFrame,
    /// What it shows ("010", "animatic", "Slug").
    pub name: String,
    /// Its take ("animation v0012"), or why it cannot play.
    pub detail: String,
    pub thumbnail: Option<ImageHandle>,
    pub selected: bool,
    /// Its source has nothing to play.
    pub missing: bool,
    /// The department its take comes from: a video clip's colour.
    pub department: Option<String>,
    /// Its shot's badge when preview frames are ready on this machine (#75,
    /// `previews`), drawn at the clip's top right.
    pub preview: Option<&'static str>,
}

/// The badge's box at the top right of a clip drawn in `rect`, `wide`
/// pixels wide; None when the clip is too narrow to hold it beside its name.
pub fn badge_box(rect: Rectangle, wide: f32) -> Option<Rectangle> {
    let room = rect.width - 2.0 * INSET;
    (room >= 2.0 * wide).then_some(Rectangle { x: rect.x + rect.width - INSET - wide, y: rect.y + INSET, width: wide, height: 15.0 })
}

/// One track's row.
#[derive(Debug, Clone)]
pub struct RowView {
    pub track: TrackRef,
    pub name: String,
    /// In the track's order: a clip's index here is its index in the edit.
    pub clips: Vec<ClipView>,
}

/// The rows as drawn, top first: video tracks top track first, then audio.
pub fn row_order(video: usize, audio: usize) -> Vec<TrackRef> {
    (0..video).rev().map(TrackRef::video).chain((0..audio).map(TrackRef::audio)).collect()
}

/// The hatching of `r`: lines rising left to right, `HATCH` apart, each
/// cut to `r` (the frame has no clip regions).
fn hatch(frame: &mut Frame, r: Rectangle, color: Color) {
    let (left, right, bottom) = (r.x, r.x + r.width, r.y + r.height);
    let mut start = left - r.height;
    while start < right {
        // The line from (start, bottom) to (start + height, top), cut to r.
        let (x0, x1) = (start.max(left), (start + r.height).min(right));
        if x1 > x0 {
            let a = Point::new(x0, bottom - (x0 - start));
            let b = Point::new(x1, bottom - (x1 - start));
            frame.stroke(&Path::line(a, b), Stroke::new(1.0, color));
        }
        start += HATCH;
    }
}

/// What part of a clip a press took.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Part {
    Body,
    Slip,
    Head,
    Tail,
    /// Roll the cut at the clip's head (with the clip before it).
    RollHead,
    /// Roll the cut at its tail (with the clip after it).
    RollTail,
    FadeIn,
    FadeOut,
}

/// The part of a clip spanning `x0..x1` (pixels) that a press at `x`, `y`
/// pixels below its row's top, takes; `alt` held.
pub fn part_at(x: f32, y: f32, x0: f32, x1: f32, alt: bool) -> Part {
    let edge = EDGE.min((x1 - x0) / 3.0);
    if x - x0 < edge {
        return if alt { Part::RollHead } else { Part::Head };
    }
    if x1 - x < edge {
        return if alt { Part::RollTail } else { Part::Tail };
    }
    if y < FADE_BAND {
        return if x < (x0 + x1) / 2.0 { Part::FadeIn } else { Part::FadeOut };
    }
    if alt { Part::Slip } else { Part::Body }
}

/// The op a drag of `part` of `clip` (shown as `view`) by `dx` frames makes,
/// let go over track `to`; None when it changes nothing.
pub fn op_for(part: Part, clip: ClipRef, view: &ClipView, dx: EditFrame, to: Option<TrackRef>) -> Option<Op<Source>> {
    let to = to.filter(|t| t.kind == clip.track.kind).unwrap_or(clip.track);
    if dx == 0 && (part != Part::Body || to == clip.track) {
        return None;
    }
    Some(match part {
        Part::Body => Op::Move { clip, to, at: (view.at + dx).max(0) },
        // Dragging right moves the picture right: earlier source frames.
        Part::Slip => Op::Slip { clip, delta: -dx },
        Part::Head => Op::Trim { clip, edge: Edge::Head, delta: dx },
        Part::Tail => Op::Trim { clip, edge: Edge::Tail, delta: dx },
        Part::RollTail => Op::Roll { clip, delta: dx },
        Part::RollHead => Op::Roll { clip: ClipRef { track: clip.track, index: clip.index.checked_sub(1)? }, delta: dx },
        Part::FadeIn => {
            let fade_in = (view.fade_in + dx).clamp(0, view.duration - view.fade_out);
            (fade_in != view.fade_in).then_some(())?;
            Op::SetFades { clip, fade_in, fade_out: view.fade_out }
        }
        Part::FadeOut => {
            let fade_out = (view.fade_out - dx).clamp(0, view.duration - view.fade_in);
            (fade_out != view.fade_out).then_some(())?;
            Op::SetFades { clip, fade_in: view.fade_in, fade_out }
        }
    })
}

pub struct Tracks<Message> {
    /// Top first (see [`row_order`]).
    pub rows: Vec<RowView>,
    pub markers: Vec<EditFrame>,
    /// Edit frames the width spans.
    pub total: usize,
    pub playhead: usize,
    /// Buffered frames as half-open ranges.
    pub buffered: Vec<(usize, usize)>,
    pub on_seek: fn(usize) -> Message,
    /// A click on a clip (None: on empty track), whether it adds to the
    /// selection, and the edit frame under it.
    pub on_select: fn(Option<ClipRef>, bool, usize) -> Message,
    /// The pointer moved while it holds a clip: redraw its outline.
    pub on_drag: fn() -> Message,
    pub on_op: fn(Op<Source>) -> Message,
    /// A right press on a clip, just before the take menu opens.
    pub on_menu: fn(ClipRef) -> Message,
}

impl<Message> Tracks<Message> {
    pub fn height(&self) -> f32 {
        RULER + self.rows.len() as f32 * ROW
    }

    fn x(&self, frame: EditFrame, width: f32) -> f32 {
        HEADER + frame as f32 / self.total.max(1) as f32 * (width - HEADER).max(1.0)
    }

    /// The edit frame under local `x` (unclamped, may be negative).
    fn frame_at(&self, x: f32, width: f32) -> f64 {
        f64::from(x - HEADER) / f64::from((width - HEADER).max(1.0)) * self.total.max(1) as f64
    }

    fn position_at(&self, x: f32, width: f32) -> usize {
        (self.frame_at(x, width).floor().max(0.0) as usize).min(self.total.saturating_sub(1))
    }

    fn row_at(&self, y: f32) -> Option<usize> {
        if y < RULER {
            return None;
        }
        let row = ((y - RULER) / ROW) as usize;
        (row < self.rows.len()).then_some(row)
    }

    /// The clip under local `(x, y)`: its row, its index, and the part.
    fn hit(&self, x: f32, y: f32, width: f32, alt: bool) -> Option<(usize, usize, Part)> {
        let row = self.row_at(y)?;
        let top = RULER + row as f32 * ROW;
        self.rows[row].clips.iter().enumerate().find_map(|(i, c)| {
            let (x0, x1) = (self.x(c.at, width), self.x(c.at + c.duration, width));
            (x >= x0 && x < x1.max(x0 + 2.0)).then(|| (row, i, part_at(x, y - top, x0, x1, alt)))
        })
    }

    /// The rectangle a drag in progress would leave its clip in.
    fn ghost(&self, grab: &Grab, width: f32, cursor_x: f32, cursor_y: f32) -> Option<Rectangle> {
        let c = &self.rows[grab.row].clips[grab.index];
        let dx = self.frames_dragged(grab, cursor_x, width);
        let (mut at, mut end) = (c.at, c.at + c.duration);
        let mut row = grab.row;
        match grab.part {
            Part::Body => {
                at += dx;
                end += dx;
                if let Some(r) = self.row_at(cursor_y).filter(|&r| self.rows[r].track.kind == self.rows[grab.row].track.kind) {
                    row = r;
                }
            }
            Part::Head => at += dx,
            Part::Tail | Part::RollTail => end += dx,
            Part::RollHead => at += dx,
            Part::Slip | Part::FadeIn | Part::FadeOut => {}
        }
        let (x0, x1) = (self.x(at.max(0), width), self.x(end.max(at.max(0) + 1), width));
        Some(Rectangle { x: x0, y: RULER + row as f32 * ROW + 1.0, width: (x1 - x0).max(2.0), height: ROW - 2.0 })
    }

    fn frames_dragged(&self, grab: &Grab, cursor_x: f32, width: f32) -> EditFrame {
        (self.frame_at(cursor_x, width) - self.frame_at(grab.from_x, width)).round() as EditFrame
    }
}

/// A press held on a clip.
#[derive(Debug, Clone, Copy)]
pub struct Grab {
    row: usize,
    index: usize,
    part: Part,
    from_x: f32,
}

#[derive(Debug, Default)]
pub struct Pointer {
    /// Seeking with the button held.
    scrubbing: bool,
    grab: Option<Grab>,
    /// Where the pointer is, in local pixels, while a clip is held.
    at: (f32, f32),
    modifiers: keyboard::Modifiers,
}

/// The gap between the hatching's lines on a clip with nothing to play.
const HATCH: f32 = 7.0;

fn outline(frame: &mut Frame, r: Rectangle, thick: f32, color: Color) {
    frame.fill_rectangle(Point::new(r.x, r.y), Size::new(r.width, thick), color);
    frame.fill_rectangle(Point::new(r.x, r.y + r.height - thick), Size::new(r.width, thick), color);
    frame.fill_rectangle(Point::new(r.x, r.y), Size::new(thick, r.height), color);
    frame.fill_rectangle(Point::new(r.x + r.width - thick, r.y), Size::new(thick, r.height), color);
}

impl<Message> Program<Message> for Tracks<Message> {
    type State = Pointer;

    fn draw(&self, state: &Pointer, frame: &mut Frame, bounds: Rectangle, _cursor: Cursor) {
        let (w, h) = (bounds.width, bounds.height);
        frame.fill_rectangle(Point::new(0.0, 0.0), Size::new(w, h), look::seam());
        frame.fill_rectangle(Point::new(0.0, 0.0), Size::new(w, RULER), look::panel());
        let slot = (w - HEADER) / self.total.max(1) as f32;
        let step = tick_step(slot);
        if step != usize::MAX {
            for f in (0..self.total).step_by(step) {
                let tall = if f % (step * 5) == 0 { 8.0 } else { 4.0 };
                frame.fill_rectangle(Point::new(self.x(f as EditFrame, w), RULER - STRIP - tall), Size::new(1.0, tall), look::tick());
            }
        }
        for &(start, end) in &self.buffered {
            let (x0, x1) = (self.x(start as EditFrame, w), self.x(end as EditFrame, w));
            frame.fill_rectangle(Point::new(x0, RULER - STRIP), Size::new((x1 - x0).max(1.0), STRIP), look::buffered());
        }
        let marker = look::mark();
        for &m in &self.markers {
            let x = self.x(m, w) + slot / 2.0;
            let tip = Path::new(|p| {
                p.move_to(Point::new(x - 5.0, 0.0));
                p.line_to(Point::new(x + 5.0, 0.0));
                p.line_to(Point::new(x, 8.0));
                p.close();
            });
            frame.fill(&tip, marker);
        }

        for (r, row) in self.rows.iter().enumerate() {
            let top = RULER + r as f32 * ROW;
            let band = if row.track.kind == TrackKind::Video { look::band() } else { look::band_audio() };
            frame.fill_rectangle(Point::new(0.0, top), Size::new(HEADER - 1.0, ROW - 1.0), look::panel());
            frame.fill_rectangle(Point::new(HEADER, top), Size::new(w - HEADER, ROW - 1.0), band);
            let label = Rectangle { x: 10.0, y: top, width: HEADER - 14.0, height: ROW };
            frame.fill_text_in(row.name.clone(), label, H::Left, V::Center, 13.0, look::quiet());
            for c in &row.clips {
                let (x0, x1) = (self.x(c.at, w), self.x(c.at + c.duration, w));
                let rect = Rectangle { x: x0, y: top + 1.0, width: (x1 - x0 - 1.0).max(1.0), height: ROW - 3.0 };
                let body = match (row.track.kind, c.missing) {
                    (_, true) => look::missing(),
                    (TrackKind::Video, false) => c.department.as_deref().map_or_else(look::clip, look::department),
                    (TrackKind::Audio, false) => look::audio(),
                };
                frame.fill_rectangle(Point::new(rect.x, rect.y), Size::new(rect.width, rect.height), body);
                if c.missing {
                    hatch(frame, rect, look::hatch());
                }
                let mut left = rect.x + INSET;
                if let Some(handle) = &c.thumbnail {
                    let (iw, ih) = (handle.image().width.max(1) as f32, handle.image().height.max(1) as f32);
                    let tall = rect.height - 2.0 * INSET;
                    let wide = (tall * iw / ih).min(rect.width - 2.0 * INSET);
                    if wide >= 12.0 {
                        frame.draw_image(handle, Rectangle { x: left, y: rect.y + INSET, width: wide, height: wide * ih / iw });
                        left += wide + INSET;
                    }
                }
                let mut right = rect.x + rect.width;
                if let (TrackKind::Video, Some(words)) = (row.track.kind, c.preview)
                    && let Some(tag) = badge_box(rect, 86.0)
                {
                    frame.fill_rectangle(Point::new(tag.x, tag.y), Size::new(tag.width, tag.height), look::now());
                    frame.fill_text_in(words.to_owned(), tag, H::Center, V::Center, 10.0, look::surround());
                    right = tag.x - INSET;
                }
                let room = right - left - INSET;
                if room >= 10.0 {
                    let line = |y: f32, tall: f32| Rectangle { x: left, y: rect.y + y, width: room, height: tall };
                    frame.fill_text_in(c.name.clone(), line(FADE_BAND, 15.0), H::Left, V::Top, 12.0, look::text());
                    frame.fill_text_in(
                        c.detail.clone(),
                        line(FADE_BAND + 16.0, 13.0),
                        H::Left,
                        V::Top,
                        10.0,
                        if c.missing { look::error() } else { look::quiet_on_clip() },
                    );
                }
                // Fades: a ramp from the clip's corner over its top band.
                let ramp = Color::from_rgba8(240, 240, 240, 140);
                if c.fade_in > 0 {
                    let xe = self.x(c.at + c.fade_in, w);
                    let tri = Path::new(|p| {
                        p.move_to(Point::new(rect.x, rect.y + rect.height));
                        p.line_to(Point::new(xe, rect.y));
                        p.line_to(Point::new(rect.x, rect.y));
                        p.close();
                    });
                    frame.fill(&tri, Color::from_rgba8(0, 0, 0, 90));
                    frame.stroke(&Path::line(Point::new(rect.x, rect.y + rect.height), Point::new(xe, rect.y)), Stroke::new(1.0, ramp));
                }
                if c.fade_out > 0 {
                    let xs = self.x(c.at + c.duration - c.fade_out, w);
                    let xr = rect.x + rect.width;
                    let tri = Path::new(|p| {
                        p.move_to(Point::new(xr, rect.y + rect.height));
                        p.line_to(Point::new(xs, rect.y));
                        p.line_to(Point::new(xr, rect.y));
                        p.close();
                    });
                    frame.fill(&tri, Color::from_rgba8(0, 0, 0, 90));
                    frame.stroke(&Path::line(Point::new(xs, rect.y), Point::new(xr, rect.y + rect.height)), Stroke::new(1.0, ramp));
                }
                if c.selected {
                    outline(frame, rect, 2.0, look::mark());
                }
            }
        }

        if let Some(grab) = &state.grab
            && let Some(ghost) = self.ghost(grab, w, state.at.0, state.at.1)
        {
            outline(frame, ghost, 2.0, look::now());
            let dx = self.frames_dragged(grab, state.at.0, w);
            let words = match grab.part {
                Part::Slip => format!("slip {:+}", -dx),
                Part::FadeIn | Part::FadeOut => format!("fade {:+}", if grab.part == Part::FadeIn { dx } else { -dx }),
                _ => format!("{dx:+}"),
            };
            let tag = Rectangle { x: ghost.x + 2.0, y: ghost.y + ghost.height - 15.0, width: 120.0, height: 14.0 };
            frame.fill_text_in(words, tag, H::Left, V::Center, 11.0, look::now());
        }

        let centre = self.x(self.playhead as EditFrame, w) + slot / 2.0;
        let accent = look::now();
        frame.fill_rectangle(Point::new(centre - 1.0, 0.0), Size::new(2.0, h), accent);
        frame.fill_rectangle(Point::new(centre - 4.0, 0.0), Size::new(8.0, 3.0), accent);
    }

    fn update(&self, state: &mut Pointer, event: &Event, bounds: Rectangle, cursor: Cursor) -> (Status, Option<Message>) {
        let w = bounds.width;
        match event {
            Event::Keyboard(keyboard::Event::ModifiersChanged(modifiers)) => {
                state.modifiers = *modifiers;
                (Status::Ignored, None)
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(local) = cursor.position_relative_to(bounds) else { return (Status::Ignored, None) };
                if local.x < HEADER {
                    return (Status::Captured, None);
                }
                if local.y < RULER {
                    state.scrubbing = true;
                    return (Status::Captured, Some((self.on_seek)(self.position_at(local.x, w))));
                }
                match self.hit(local.x, local.y, w, state.modifiers.alt) {
                    Some((row, index, part)) => {
                        let additive = state.modifiers.shift || state.modifiers.command();
                        state.grab = Some(Grab { row, index, part, from_x: local.x });
                        state.at = (local.x, local.y);
                        let clip = ClipRef { track: self.rows[row].track, index };
                        (Status::Captured, Some((self.on_select)(Some(clip), additive, self.position_at(local.x, w))))
                    }
                    None => {
                        state.scrubbing = true;
                        (Status::Captured, Some((self.on_select)(None, false, self.position_at(local.x, w))))
                    }
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) => match cursor.position_relative_to(bounds) {
                Some(local) => match self.hit(local.x, local.y, w, false) {
                    Some((row, index, _)) => (Status::Ignored, Some((self.on_menu)(ClipRef { track: self.rows[row].track, index }))),
                    None => (Status::Captured, None),
                },
                None => (Status::Ignored, None),
            },
            Event::Mouse(mouse::Event::CursorMoved { position }) => {
                let local = (position.x - bounds.x, position.y - bounds.y);
                if state.scrubbing {
                    return (Status::Captured, Some((self.on_seek)(self.position_at(local.0, w))));
                }
                if state.grab.is_some() {
                    state.at = local;
                    return (Status::Captured, Some((self.on_drag)()));
                }
                (Status::Ignored, None)
            }
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                state.scrubbing = false;
                let Some(grab) = state.grab.take() else { return (Status::Ignored, None) };
                let local = cursor.position_relative_to(bounds).map_or(state.at, |p| (p.x, p.y));
                let dx = self.frames_dragged(&grab, local.0, w);
                let to = self.row_at(local.1).map(|r| self.rows[r].track);
                let clip = ClipRef { track: self.rows[grab.row].track, index: grab.index };
                let op = op_for(grab.part, clip, &self.rows[grab.row].clips[grab.index], dx, to);
                (Status::Captured, op.map(self.on_op))
            }
            _ => (Status::Ignored, None),
        }
    }

    fn mouse_interaction(&self, state: &Pointer, bounds: Rectangle, cursor: Cursor) -> mouse::Interaction {
        if state.grab.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let Some(local) = cursor.position_relative_to(bounds) else { return mouse::Interaction::Idle };
        match self.hit(local.x, local.y, bounds.width, state.modifiers.alt) {
            Some((_, _, Part::Head | Part::Tail | Part::RollHead | Part::RollTail)) => mouse::Interaction::ResizeHorizontal,
            Some(_) => mouse::Interaction::Grab,
            None => mouse::Interaction::Pointer,
        }
    }
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
    }

    fn view(at: EditFrame, duration: EditFrame, fade_in: EditFrame, fade_out: EditFrame) -> ClipView {
        ClipView {
            at,
            duration,
            fade_in,
            fade_out,
            name: String::new(),
            detail: String::new(),
            thumbnail: None,
            selected: false,
            missing: false,
            department: None,
            preview: None,
        }
    }

    proptest! {
        /// A clip's badge sits inside it at its top right, and leaves at
        /// least as much room for the name; a clip too narrow has none.
        #[test]
        fn badges_fit_inside_their_clip(x in -500f32..2000.0, width in 0f32..800.0, wide in 10f32..120.0) {
            let rect = Rectangle { x, y: 40.0, width, height: ROW - 3.0 };
            match badge_box(rect, wide) {
                Some(tag) => {
                    prop_assert!(tag.x >= rect.x + INSET + wide - 0.01);
                    prop_assert!((tag.x + tag.width - (rect.x + rect.width - INSET)).abs() < 0.01);
                    prop_assert!(tag.y + tag.height <= rect.y + rect.height);
                }
                None => prop_assert!(width - 2.0 * INSET < 2.0 * wide),
            }
        }
    }

    #[test]
    fn rows_put_the_top_video_track_first_then_audio() {
        assert_eq!(row_order(2, 2), [TrackRef::video(1), TrackRef::video(0), TrackRef::audio(0), TrackRef::audio(1)]);
    }

    #[test]
    fn edges_trim_alt_rolls_the_top_band_fades() {
        assert_eq!(part_at(101.0, 20.0, 100.0, 200.0, false), Part::Head);
        assert_eq!(part_at(199.0, 20.0, 100.0, 200.0, false), Part::Tail);
        assert_eq!(part_at(199.0, 20.0, 100.0, 200.0, true), Part::RollTail);
        assert_eq!(part_at(101.0, 20.0, 100.0, 200.0, true), Part::RollHead);
        assert_eq!(part_at(120.0, 2.0, 100.0, 200.0, false), Part::FadeIn);
        assert_eq!(part_at(180.0, 2.0, 100.0, 200.0, false), Part::FadeOut);
        assert_eq!(part_at(150.0, 20.0, 100.0, 200.0, false), Part::Body);
        assert_eq!(part_at(150.0, 20.0, 100.0, 200.0, true), Part::Slip);
        // A clip a few pixels wide is still grabbed by its body.
        assert_eq!(part_at(101.5, 20.0, 100.0, 103.0, false), Part::Body);
    }

    #[test]
    fn drags_become_ops() {
        let clip = ClipRef { track: TrackRef::video(0), index: 2 };
        let v = view(100, 50, 0, 5);
        assert_eq!(op_for(Part::Body, clip, &v, 0, Some(TrackRef::video(0))), None);
        assert_eq!(op_for(Part::Body, clip, &v, 0, Some(TrackRef::video(1))), Some(Op::Move { clip, to: TrackRef::video(1), at: 100 }));
        // Onto an audio track a video clip stays on its own.
        assert_eq!(op_for(Part::Body, clip, &v, -200, Some(TrackRef::audio(0))), Some(Op::Move { clip, to: TrackRef::video(0), at: 0 }));
        assert_eq!(op_for(Part::Slip, clip, &v, 4, None), Some(Op::Slip { clip, delta: -4 }));
        assert_eq!(op_for(Part::Tail, clip, &v, -3, None), Some(Op::Trim { clip, edge: Edge::Tail, delta: -3 }));
        assert_eq!(op_for(Part::RollHead, clip, &v, 2, None), Some(Op::Roll { clip: ClipRef { index: 1, ..clip }, delta: 2 }));
        assert_eq!(op_for(Part::RollHead, ClipRef { index: 0, ..clip }, &v, 2, None), None);
        assert_eq!(op_for(Part::FadeIn, clip, &v, 12, None), Some(Op::SetFades { clip, fade_in: 12, fade_out: 5 }));
        assert_eq!(op_for(Part::FadeOut, clip, &v, -7, None), Some(Op::SetFades { clip, fade_in: 0, fade_out: 12 }));
    }

    proptest! {
        /// However far a fade handle is dragged, the fades fit in the clip.
        #[test]
        fn fades_always_fit(duration in 1i64..200, a in 0i64..200, b in 0i64..200, dx in -400i64..400, out in any::<bool>()) {
            let fade_in = a.min(duration);
            let fade_out = b.min(duration - fade_in);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            let part = if out { Part::FadeOut } else { Part::FadeIn };
            if let Some(Op::SetFades { fade_in, fade_out, .. }) = op_for(part, clip, &view(0, duration, fade_in, fade_out), dx, None) {
                prop_assert!(fade_in >= 0 && fade_out >= 0 && fade_in + fade_out <= duration);
            }
        }
    }
}
