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
//!
//! Not `editable` (a preview window's frames, `frames_view`), the clips are
//! only shown: a press or drag anywhere right of the names scrubs.

use primer::Horizontal as H;
use primer::ImageHandle;
use primer::Vertical as V;
use primer::prelude::*;
use primer::widget::custom::{
    BoxStyle, Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Node, RenderNode, Shell, Sizing, Status, TextNode, Widget, keyboard, mouse,
};
use th_edit::document::{ClipRef, Frame as EditFrame, Source, TrackKind, TrackRef};
use th_edit::ops::{Edge, Op};

use crate::look;

pub mod landing;
mod lanes;
mod marks;
pub mod overview;
pub mod picks;
mod reach;
pub mod sweep;
pub mod tags;
pub use landing::{Landing, Moving};
#[cfg(test)]
pub use lanes::ROW_MAX;
pub use lanes::{ROW_MIN, row_height};
pub use reach::{Reach, Room, Span, Stop, reach, room};

/// The ruler's height, buffered strip included.
pub const RULER: f32 = 20.0;
/// One track's height before the user drags its header's bottom edge.
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
/// The bar on a dragged edge that has reached the end of its footage, and
/// how far past it the hatching goes.
const LIMIT_BAR: f32 = 3.0;
const LIMIT_BEYOND: f32 = 18.0;
/// The line under the rows that says which tracks are not shown.
pub const NOTE: f32 = 20.0;

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
    /// The state of its shot's job on the farm, when the shot has one to
    /// show (`farm_jobs`): a line along the clip's top and, with room, a
    /// badge left of the preview badge.
    pub farm: Option<th_pipe::farm_jobs::Status>,
    /// The frames its source has that it does not show ([`room`]): how far
    /// its edges drag out. None: any (a slug), or not known yet.
    pub room: Option<Room>,
    /// An audio clip's waveform (`waveform`): None on a video clip and
    /// on a clip with no sound.
    pub wave: Option<crate::waveform::Wave>,
}

impl ClipView {
    fn span(&self) -> Span {
        Span { at: self.at, duration: self.duration, room: self.room }
    }
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
    /// How tall the row is drawn ([`ROW`] until the user drags it).
    pub height: f32,
    /// The only track shown (its name was double-clicked): its name says so.
    pub solo: bool,
    /// The spare layer: an empty row over the top video track for a track
    /// the edit has not got yet. A clip dragged onto it makes the track.
    pub spare: bool,
    /// The colour the track is marked with, when it has one: a stripe
    /// down the left of its name.
    pub color: Option<Color>,
}

/// The rows as drawn, top first: video tracks top track first, then audio.
pub fn row_order(video: usize, audio: usize) -> Vec<TrackRef> {
    (0..video).rev().map(TrackRef::video).chain((0..audio).map(TrackRef::audio)).collect()
}

/// What part of a clip a press took.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Part {
    Body,
    /// The body with Ctrl (Cmd on macOS) held: the clip and every clip
    /// after it on its track move as one ([`Op::Shift`]).
    Push,
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
        Part::Push => Op::Shift { clip, delta: dx },
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

/// The op a drag of `part` of clip `index` of `clips` (a track's, in order)
/// by `dx` frames makes, let go over track `to`, and how far the drag went:
/// an edge, a slip or a roll stops where the footage ends ([`reach`]), so
/// the op is one the document takes.
pub fn drag_op(
    part: Part,
    track: TrackRef,
    clips: &[ClipView],
    index: usize,
    dx: EditFrame,
    to: Option<TrackRef>,
) -> (Option<Op<Source>>, Reach) {
    let span = |i: Option<usize>| i.and_then(|i| clips.get(i)).map(ClipView::span);
    let held = reach(part, clips[index].span(), span(index.checked_sub(1)), span(index.checked_add(1)), dx);
    (op_for(part, ClipRef { track, index }, &clips[index], held.dx, to), held)
}

/// What a right press off the clips is on, for the timeline's menu: the
/// frame under it as given, and the track of row `row` of `rows`, unless
/// that is the spare layer (no track of the edit yet).
pub fn context_at(rows: &[RowView], row: Option<usize>, frame: Option<usize>) -> (Option<usize>, Option<TrackRef>) {
    (frame, row.and_then(|r| rows.get(r)).filter(|r| !r.spare).map(|r| r.track))
}

pub struct Tracks<Message> {
    /// Top first (see [`row_order`]).
    pub rows: Vec<RowView>,
    pub markers: Vec<EditFrame>,
    /// Edit frames the width spans, from frame `first` on.
    pub total: usize,
    /// The edit frame at the left edge of the tracks' time: what the
    /// timeline is scrolled to.
    pub first: f64,
    /// The wheel over the tracks asks for another view of them.
    pub on_view: fn(ViewChange) -> Message,
    /// How many times the cut's clips have changed: a drag keeps the count
    /// it was pressed at and is dropped once the count moves on, however
    /// alike the clip now at its place ([`Tracks::held`]).
    pub cut: u64,
    pub playhead: usize,
    /// Buffered frames as half-open ranges.
    pub buffered: Vec<(usize, usize)>,
    /// Renders on their way (`rendering`): a thin strip along the bottom
    /// of each clip waiting for one, the frames on disk lit.
    pub rendering: Vec<crate::rendering::Strip>,
    pub on_seek: fn(usize) -> Message,
    /// A click on a clip (None: on empty track), whether it adds to the
    /// selection, and the edit frame under it.
    pub on_select: fn(Option<ClipRef>, bool, usize) -> Message,
    /// The pointer moved while it holds a clip: redraw its outline.
    pub on_drag: fn() -> Message,
    pub on_op: fn(Op<Source>) -> Message,
    /// A right press on a clip, just before the take menu opens.
    pub on_menu: fn(ClipRef) -> Message,
    /// The bottom edge of a track's header was dragged: the track, and the
    /// height its row is now.
    pub on_row_height: fn(TrackRef, f32) -> Message,
    /// A track's name was double-clicked: show only it, or the others again.
    pub on_solo: fn(TrackRef) -> Message,
    /// A right press off the clips, just before the timeline's menu opens:
    /// the edit frame under it (None on a track's name) and the track of
    /// the row it is on (None on the ruler and the spare layer).
    pub on_context: fn(Option<usize>, Option<TrackRef>) -> Message,
    /// A press or drag that picks more than one clip, or a gap.
    pub on_gesture: fn(Gesture) -> Message,
    /// The gap selected, named by the clip after it: drawn outlined.
    pub gap: Option<ClipRef>,
    /// What the rows leave out, said in a line under them ("2 audio tracks
    /// hidden"); None when every track has its row.
    pub note: Option<String>,
    /// Clips can be selected, dragged and right-clicked; else every press
    /// scrubs.
    pub editable: bool,
}

/// What a press or drag on the tracks picks, beyond one clip ([`picks`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Gesture {
    /// A clip pressed with Shift held: the range from the clip clicked
    /// last to it.
    Range(ClipRef),
    /// A press on the empty frames before a clip: that gap, named by the
    /// clip, and the edit frame under the press.
    Gap(ClipRef, usize),
    /// A box dragged from empty track: the clips it touches now.
    Box(Vec<ClipRef>),
    /// An Alt+drag along empty video track let go ([`sweep`]): the track
    /// and the edit frames it swept, from `at`, `frames` long.
    Sweep { track: TrackRef, at: EditFrame, frames: EditFrame },
}

/// What the wheel over the tracks asks for. Ctrl (Cmd) and the wheel
/// zooms about the frame under the pointer; Shift and the wheel, or a
/// sideways wheel or trackpad swipe, scrolls through time. The wheel
/// alone is not taken: it scrolls the tracks up and down when they are
/// taller than the timeline.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ViewChange {
    /// Scroll by this many frames (positive: later).
    Scroll(f64),
    /// Zoom by `factor` (above 1: in) about edit frame `about`.
    Zoom { about: f64, factor: f64 },
}

/// One notch of the wheel, in the pixels the wheel event counts.
const WHEEL_NOTCH: f32 = 16.0;
/// How much one notch of the wheel zooms.
pub const ZOOM_NOTCH: f64 = 1.25;

/// How far (pixels) a press on empty track is dragged before it is a box.
const BOX_SLOP: f32 = 4.0;

impl<Message> Tracks<Message> {
    /// Each row's clips as `(at, end)`.
    /// Whether an Alt+drag on row `row` sweeps frames ([`sweep`]): a
    /// video track's, never the spare layer's.
    fn sweeps(&self, row: usize) -> bool {
        self.rows.get(row).is_some_and(|r| !r.spare && r.track.kind == TrackKind::Video)
    }

    /// The frames an Alt+drag on row `row` from local `from_x` to `to_x`
    /// sweeps ([`sweep::swept`]).
    fn swept(&self, row: usize, from_x: f32, to_x: f32, width: f32) -> Option<(EditFrame, EditFrame)> {
        let spans = self.row_spans().into_iter().nth(row)?;
        sweep::swept(&spans, self.frame_at(from_x, width), self.frame_at(to_x, width))
    }

    fn row_spans(&self) -> Vec<Vec<(EditFrame, EditFrame)>> {
        self.rows.iter().map(|r| r.clips.iter().map(|c| (c.at, c.at + c.duration)).collect()).collect()
    }

    /// The row at local `y`, or the nearest: the top one above them, the
    /// bottom one below.
    fn row_near(&self, y: f32) -> usize {
        self.row_at(y).unwrap_or(if y < RULER { 0 } else { self.rows.len().saturating_sub(1) })
    }

    /// The clips a box dragged from local `from` to local `to` picks.
    fn boxed(&self, from: (f32, f32), to: (f32, f32), width: f32) -> Vec<ClipRef> {
        let frame = |x: f32| self.frame_at(x.max(HEADER), width).floor() as EditFrame;
        let picked = picks::boxed(&self.row_spans(), self.row_near(from.1), self.row_near(to.1), frame(from.0), frame(to.0));
        picked.into_iter().map(|(r, index)| ClipRef { track: self.rows[r].track, index }).collect()
    }

    pub fn height(&self) -> f32 {
        RULER + self.rows.iter().map(|r| r.height).sum::<f32>() + if self.note.is_some() { NOTE } else { 0.0 }
    }

    fn heights(&self) -> Vec<f32> {
        self.rows.iter().map(|r| r.height).collect()
    }

    /// The top of row `row`, in local pixels.
    fn row_top(&self, row: usize) -> f32 {
        RULER + lanes::row_top(&self.heights(), row)
    }

    /// Where edit frame `frame` starts, in local pixels: left of the names'
    /// column, or past the width, when it is scrolled out of sight.
    fn x(&self, frame: EditFrame, width: f32) -> f32 {
        HEADER + ((frame as f64 - self.first) / self.total.max(1) as f64) as f32 * (width - HEADER).max(1.0)
    }

    /// The edit frame under local `x` (unclamped, may be negative).
    fn frame_at(&self, x: f32, width: f32) -> f64 {
        self.first + f64::from(x - HEADER) / f64::from((width - HEADER).max(1.0)) * self.total.max(1) as f64
    }

    fn position_at(&self, x: f32, width: f32) -> usize {
        let last = ((self.first + self.total as f64).ceil() as usize).saturating_sub(1);
        (self.frame_at(x, width).floor().max(0.0) as usize).min(last)
    }

    /// The part of the pixels `x0..x1` that is in the tracks' time (right
    /// of the names, inside the width); None when none of it is.
    fn seen(x0: f32, x1: f32, width: f32) -> Option<(f32, f32)> {
        (x1 > HEADER && x0 < width).then(|| (x0.max(HEADER), x1.min(width)))
    }

    fn row_at(&self, y: f32) -> Option<usize> {
        lanes::row_at(&self.heights(), y - RULER)
    }

    /// The row whose header's bottom edge local `(x, y)` is on: a press
    /// there drags its height.
    fn edge_at(&self, x: f32, y: f32) -> Option<usize> {
        (self.editable && x < HEADER).then(|| lanes::edge_at(&self.heights(), y - RULER))?
    }

    /// The clip under local `(x, y)`: its row, its index, and the part.
    fn hit(&self, x: f32, y: f32, width: f32, alt: bool) -> Option<(usize, usize, Part)> {
        // A clip scrolled under the names' column is not there to press.
        let row = self.row_at(y).filter(|_| x >= HEADER)?;
        let top = self.row_top(row);
        self.rows[row].clips.iter().enumerate().find_map(|(i, c)| {
            let (x0, x1) = (self.x(c.at, width), self.x(c.at + c.duration, width));
            (x >= x0 && x < x1.max(x0 + 2.0)).then(|| (row, i, part_at(x, y - top, x0, x1, alt)))
        })
    }

    /// The row of the clip `grab` took, while that clip is still where it
    /// was: on its track, at its index, over the same frames. None once the
    /// cut changed under the pointer (a Delete or an undo pressed while
    /// dragging, an agent's edit): the drag is then dropped, never painted
    /// from or let go on whatever clip is at that place now.
    fn held(&self, grab: &Grab) -> Option<usize> {
        // The span alone does not tell: a Shift+Delete of the clip pulls
        // the next one into its index and, as long, over its frames.
        if grab.cut != self.cut {
            return None;
        }
        let row = self.rows.iter().position(|r| !r.spare && r.track == grab.clip.track)?;
        let clip = self.rows[row].clips.get(grab.clip.index)?;
        ((clip.at, clip.duration) == grab.span).then_some(row)
    }

    /// The rectangle a drag in progress would leave its clip in, and how
    /// far the drag went; None when its clip is no longer held.
    fn ghost(&self, grab: &Grab, width: f32, cursor_x: f32, cursor_y: f32) -> Option<(Rectangle, Reach, Option<Landing>)> {
        let held = self.held(grab)?;
        let c = &self.rows[held].clips[grab.clip.index];
        let (reach, row, landing) = self.dragged(grab, held, cursor_x, cursor_y, width);
        let dx = reach.dx;
        let (mut at, mut end) = (c.at, c.at + c.duration);
        match grab.part() {
            Part::Push => {
                // The clip and all that follows it on its track, as one.
                let last = self.rows[held].clips.last().map_or(end, |l| l.at + l.duration);
                at += dx;
                end = last + dx;
            }
            Part::Body => {
                at += dx;
                end += dx;
            }
            Part::Head => at += dx,
            Part::Tail | Part::RollTail => end += dx,
            Part::RollHead => at += dx,
            Part::Slip | Part::FadeIn | Part::FadeOut => {}
        }
        // Scrolled out on the left, it is held at the names' column.
        let (x0, x1) = (self.x(at.max(0), width).max(HEADER), self.x(end.max(at.max(0) + 1), width).max(HEADER));
        let ghost = Rectangle { x: x0, y: self.row_top(row) + 1.0, width: (x1 - x0).max(2.0), height: self.rows[row].height - 2.0 };
        Some((ghost, reach, landing))
    }

    /// The row a body drag of `grab` (held on row `held`) lands on with
    /// the pointer `cursor_y` down the tracks: the row under it when that
    /// is of the clip's kind (the spare layer is), else its own.
    fn target_row(&self, grab: &Grab, held: usize, cursor_y: f32) -> usize {
        self.row_at(cursor_y).filter(|&r| self.rows[r].track.kind == grab.clip.track.kind).unwrap_or(held)
    }

    /// Where a body drag of `grab` by `dx` frames onto row `target` lands
    /// ([`landing`]): snapped to the cut's start, the playhead, the
    /// markers and the other clips' ends, then fitted among the clips that
    /// stay. Let go on its own row, a selected clip takes the rest of the
    /// selection with it.
    fn landing(&self, grab: &Grab, held: usize, target: usize, dx: EditFrame, width: f32) -> Landing {
        let dragged = &self.rows[held].clips[grab.clip.index];
        let together = target == held && dragged.selected && self.rows.iter().flat_map(|r| &r.clips).filter(|c| c.selected).count() > 1;
        let moves = |r: usize, i: usize| (r == held && i == grab.clip.index) || (together && self.rows[r].clips[i].selected);
        let mut moving = vec![Moving { row: target, at: dragged.at, duration: dragged.duration }];
        let mut staying: Vec<Vec<(EditFrame, EditFrame)>> = Vec::with_capacity(self.rows.len());
        for (r, row) in self.rows.iter().enumerate() {
            let mut stay = Vec::new();
            for (i, c) in row.clips.iter().enumerate() {
                if !moves(r, i) {
                    stay.push((c.at, c.at + c.duration));
                } else if (r, i) != (held, grab.clip.index) {
                    moving.push(Moving { row: r, at: c.at, duration: c.duration });
                }
            }
            staying.push(stay);
        }
        let mut points: Vec<EditFrame> = vec![0, self.playhead as EditFrame];
        points.extend(&self.markers);
        points.extend(staying.iter().flatten().flat_map(|&(at, end)| [at, end]));
        let pixels_a_frame = (width - HEADER).max(1.0) / self.total.max(1) as f32;
        // A clip not dragged along (a click, or a drag straight up to
        // another row) snaps to nothing: a click never moves a clip.
        let reach = if dx == 0 { 0 } else { (landing::SNAP_PIXELS / pixels_a_frame).round() as EditFrame };
        landing::land(&moving, &staying, &points, reach, dx)
    }

    /// How far the drag in progress has taken its clip, held on row
    /// `held`, the row it would land on, and for a body drag where it
    /// lands.
    fn dragged(&self, grab: &Grab, held: usize, cursor_x: f32, cursor_y: f32, width: f32) -> (Reach, usize, Option<Landing>) {
        let reach = self.reach(grab, held, cursor_x, width);
        if grab.part() != Part::Body {
            return (reach, held, None);
        }
        let target = self.target_row(grab, held, cursor_y);
        let landing = self.landing(grab, held, target, reach.dx, width);
        (Reach { dx: landing.delta, stop: None }, target, Some(landing))
    }

    fn frames_dragged(&self, grab: &Grab, cursor_x: f32, width: f32) -> EditFrame {
        (self.frame_at(cursor_x, width) - self.frame_at(grab.from_x, width)).round() as EditFrame
    }

    /// How far the drag in progress has taken its clip, on row `held`
    /// ([`Self::held`]): the pointer's frames, held where the footage ends
    /// ([`reach`]).
    fn reach(&self, grab: &Grab, held: usize, cursor_x: f32, width: f32) -> Reach {
        let row = &self.rows[held];
        drag_op(grab.part(), row.track, &row.clips, grab.clip.index, self.frames_dragged(grab, cursor_x, width), None).1
    }

    /// The words under a drag's outline: how far it went and what moves
    /// with it ([`tags`]).
    fn drag_words(&self, grab: &Grab, held: usize, reach: Reach, landing: Option<Landing>) -> String {
        let row = &self.rows[held];
        let c = &row.clips[grab.clip.index];
        let after = row.clips.len() - grab.clip.index - 1;
        let dx = reach.dx;
        match grab.part() {
            Part::Slip => format!("slip {:+}", -dx),
            Part::FadeIn => format!("fade {dx:+}"),
            Part::FadeOut => format!("fade {:+}", -dx),
            Part::Push => tags::push_words(dx, after, reach.stop == Some(Stop::Clip)),
            part @ (Part::Head | Part::Tail) => {
                let out = tags::taken_out(part == Part::Head, c.at, c.at + c.duration, dx);
                let staying: Vec<&str> = self
                    .rows
                    .iter()
                    .enumerate()
                    .filter(|(r, other)| *r != held && !other.spare)
                    .filter(|(_, other)| {
                        let spans: Vec<(EditFrame, EditFrame)> = other.clips.iter().map(|c| (c.at, c.at + c.duration)).collect();
                        out.is_some_and(|(from, to)| tags::stays(&spans, from, to))
                    })
                    .map(|(_, other)| other.name.as_str())
                    .collect();
                tags::trim_words(dx, after, &staying)
            }
            Part::Body => match landing {
                Some(Landing { bumped: true, .. }) => format!("{dx:+} \u{b7} beside the clip there"),
                Some(Landing { snapped: Some(_), .. }) => format!("{dx:+} \u{b7} snapped"),
                _ => format!("{dx:+}"),
            },
            Part::RollHead | Part::RollTail => format!("{dx:+}"),
        }
    }
}

/// A press held on a clip.
#[derive(Debug, Clone, Copy)]
pub struct Grab {
    /// The clip it took, and the frames that clip was over then (where it
    /// is and how long): what tells it from another clip at its place once
    /// the cut has changed ([`Tracks::held`]).
    clip: ClipRef,
    span: (EditFrame, EditFrame),
    /// [`Tracks::cut`] when it was taken.
    cut: u64,
    part: Part,
    from_x: f32,
    /// Ctrl (Cmd on macOS) is held now: a body drag pushes what follows.
    push: bool,
}

impl Grab {
    /// What it drags now: its body is a push while Ctrl/Cmd is held.
    fn part(&self) -> Part {
        if self.part == Part::Body && self.push { Part::Push } else { self.part }
    }
}

#[derive(Debug, Default)]
pub struct Pointer {
    /// Seeking with the button held.
    scrubbing: bool,
    grab: Option<Grab>,
    /// Where the pointer is, in local pixels, while a clip is held.
    at: (f32, f32),
    modifiers: keyboard::Modifiers,
    /// A header's bottom edge is held: its track, where the press was
    /// (local y) and the row's height then.
    resize: Option<(TrackRef, f32, f32)>,
    /// The last press on a track's name, for a double-click.
    header_press: Option<(TrackRef, std::time::Instant)>,
    /// A press on empty track is held: where it was (local), and whether
    /// it has been dragged far enough to be a box ([`BOX_SLOP`]).
    marquee: Option<((f32, f32), bool)>,
    /// An Alt+press on empty video track is held ([`sweep`]): where it was
    /// (local), its row, and whether it has been dragged far enough to
    /// sweep ([`BOX_SLOP`]).
    sweep: Option<((f32, f32), usize, bool)>,
}

/// The gap between the hatching's lines on a clip with nothing to play.
const HATCH: f32 = 7.0;

/// What the tracks paint with, in local pixels (origin at their top-left).
///
/// Not a canvas: any canvas geometry (a `Frame` mesh) in a window makes
/// Primer draw that whole window's scene pass at 4x multisampling, and the
/// tracks are always on screen, so every frame of the editor paid for it. On
/// an RTX 3070 the scene pass was 2.6 ms at 4x against 0.17 ms single-
/// sampled; on a laptop GPU it made the editor lag the pointer by a quarter
/// of a second (2026-11-16). So rectangles are quads, the hatching an image,
/// and only the rare slanted shapes (fade ramps, markers) are geometry.
trait Paint {
    fn rect(&mut self, top_left: Point, size: Size, color: Color);
    /// Lines rising left to right, [`HATCH`] apart, over `r`.
    fn hatch(&mut self, r: Rectangle, color: Color);
    fn fill(&mut self, path: &Path, color: Color);
    fn stroke(&mut self, path: &Path, stroke: Stroke);
    fn text(&mut self, content: String, bounds: Rectangle, h: H, v: V, size: f32, color: Color);
    fn image(&mut self, handle: &ImageHandle, bounds: Rectangle);
    /// What is painted after this paints above everything before it.
    fn raise(&mut self);
}

/// What a clip paints above the clips' level ([`Tracks::paint`]): a bar, a
/// badge (its ground, words and their colour) or a dot.
enum Over {
    Rect(Rectangle, Color),
    Tag(Rectangle, Color, String, Color),
    Dot(Rectangle, Color),
}

fn outline(p: &mut impl Paint, r: Rectangle, thick: f32, color: Color) {
    p.rect(Point::new(r.x, r.y), Size::new(r.width, thick), color);
    p.rect(Point::new(r.x, r.y + r.height - thick), Size::new(r.width, thick), color);
    p.rect(Point::new(r.x, r.y), Size::new(thick, r.height), color);
    p.rect(Point::new(r.x + r.width - thick, r.y), Size::new(thick, r.height), color);
}

/// One level of [`Nodes`]: its quads, images and words, and its geometry.
struct Level {
    nodes: Vec<RenderNode>,
    geometry: Frame,
}

/// [`Paint`] into render nodes at `origin` (the tracks' top-left in the
/// window), clipped to the tracks.
struct Nodes {
    origin: Vector,
    size: Size,
    levels: Vec<Level>,
}

impl Nodes {
    fn new(bounds: Rectangle) -> Self {
        let size = bounds.size();
        Self { origin: Vector::new(bounds.x, bounds.y), size, levels: vec![Level { nodes: Vec::new(), geometry: Frame::new(size) }] }
    }

    fn level(&mut self) -> &mut Level {
        self.levels.last_mut().expect("a first level")
    }

    fn placed(&self, r: Rectangle) -> Rectangle {
        r.translate(self.origin)
    }

    /// The levels, each a paint band above the one before.
    fn finish(self) -> RenderNode {
        let bounds = Rectangle::new(Point::new(self.origin.x, self.origin.y), self.size);
        let origin = self.origin;
        let mut above: Option<RenderNode> = None;
        for level in self.levels.into_iter().rev() {
            let mut nodes = level.nodes;
            let mut mesh = level.geometry.into_mesh();
            if !mesh.is_empty() {
                for vertex in &mut mesh.vertices {
                    vertex.position = [vertex.position[0] + origin.x, vertex.position[1] + origin.y];
                }
                mesh.bounds = mesh.bounds.translate(origin);
                nodes.push(RenderNode::mesh(mesh));
            }
            nodes.extend(above.take().map(RenderNode::layer));
            above = Some(RenderNode::group(bounds, nodes));
        }
        RenderNode::clip(bounds, above.expect("a first level"))
    }
}

impl Paint for Nodes {
    fn rect(&mut self, top_left: Point, size: Size, color: Color) {
        let r = self.placed(Rectangle::new(top_left, size));
        self.level().nodes.push(RenderNode::quad(r, BoxStyle::solid(color)));
    }

    fn hatch(&mut self, r: Rectangle, color: Color) {
        let r = self.placed(r);
        let tile = hatch_tile(color);
        let mut tiles = Vec::new();
        let mut y = r.y;
        while y < r.y + r.height {
            let mut x = r.x;
            while x < r.x + r.width {
                let size = Size::new(HATCH_TILE_WIDE, HATCH_TILE_TALL);
                tiles.push(RenderNode::image(Rectangle::new(Point::new(x, y), size), tile.clone()));
                x += HATCH_TILE_WIDE;
            }
            y += HATCH_TILE_TALL;
        }
        self.level().nodes.push(RenderNode::clip(r, RenderNode::group(r, tiles)));
    }

    fn fill(&mut self, path: &Path, color: Color) {
        self.level().geometry.fill(path, color);
    }

    fn stroke(&mut self, path: &Path, stroke: Stroke) {
        self.level().geometry.stroke(path, stroke);
    }

    fn text(&mut self, content: String, bounds: Rectangle, h: H, v: V, size: f32, color: Color) {
        let words = TextNode::new(content, self.placed(bounds), size, color).with_align(h).with_valign(v);
        self.level().nodes.push(RenderNode::text(words));
    }

    fn image(&mut self, handle: &ImageHandle, bounds: Rectangle) {
        let r = self.placed(bounds);
        self.level().nodes.push(RenderNode::image(r, handle.clone()));
    }

    fn raise(&mut self) {
        let size = self.size;
        self.levels.push(Level { nodes: Vec::new(), geometry: Frame::new(size) });
    }
}

/// One tile of the hatching, in logical pixels: a whole number of line
/// gaps wide and tall, so tiles side by side and one under another continue
/// the lines over a row of any height.
const HATCH_TILE_WIDE: f32 = HATCH * 64.0;
const HATCH_TILE_TALL: f32 = HATCH * 8.0;
/// The tile's pixels per logical pixel: sharp up to 200% display scaling.
const HATCH_TILE_SCALE: f32 = 2.0;

/// The hatching tile in `color`, made once per colour.
fn hatch_tile(color: Color) -> ImageHandle {
    static TILES: std::sync::Mutex<Vec<([u8; 4], ImageHandle)>> = std::sync::Mutex::new(Vec::new());
    let key = color.to_srgba8();
    let mut tiles = TILES.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some((_, tile)) = tiles.iter().find(|(k, _)| *k == key) {
        return tile.clone();
    }
    let (wide, tall) = ((HATCH_TILE_WIDE * HATCH_TILE_SCALE) as u32, (HATCH_TILE_TALL * HATCH_TILE_SCALE) as u32);
    let mut rgba = Vec::with_capacity((wide * tall * 4) as usize);
    for py in 0..tall {
        for px in 0..wide {
            let (x, y) = ((px as f32 + 0.5) / HATCH_TILE_SCALE, (py as f32 + 0.5) / HATCH_TILE_SCALE);
            let coverage = hatch_coverage(x, y);
            rgba.extend_from_slice(&[key[0], key[1], key[2], (f32::from(key[3]) * coverage).round() as u8]);
        }
    }
    let tile = ImageHandle::from_rgba(wide, tall, rgba);
    tiles.push((key, tile.clone()));
    tile
}

/// How much of the logical pixel at `(x, y)` a hatching line covers: lines
/// one pixel wide where `x + y` is a multiple of [`HATCH`] (rising left to
/// right, as y grows downwards), anti-aliased over one tile pixel.
fn hatch_coverage(x: f32, y: f32) -> f32 {
    let along = (x + y).rem_euclid(HATCH);
    let across = along.min(HATCH - along) / std::f32::consts::SQRT_2;
    (0.5 + (0.5 - across) * HATCH_TILE_SCALE).clamp(0.0, 1.0)
}

/// The tracks as a widget: `Tracks` painted as quads (see [`Paint`]), its
/// pointer state kept under `id` as an interactive canvas keeps its own.
pub struct TrackView<Message> {
    id: Id,
    tracks: Tracks<Message>,
    height: f32,
}

/// `tracks` as a widget `height` tall, filling the width; its gesture state
/// lives under `id` (frame-stable).
pub fn track_view<Message: 'static>(id: Id, tracks: Tracks<Message>, height: f32) -> TrackView<Message> {
    TrackView { id, tracks, height }
}

impl<Message> Widget<Message> for TrackView<Message> {
    fn sizing(&self) -> Sizing {
        Sizing { width: Length::Fill, height: Length::fixed(self.height) }
    }

    fn layout(&self, limits: &Limits, _ctx: LayoutCtx<'_>) -> Node {
        Node::new(limits.resolve(self.sizing(), Size::ZERO))
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        let idle = Pointer::default();
        let state = ctx.store.state::<Pointer>(&self.id).unwrap_or(&idle);
        let mut nodes = Nodes::new(bounds);
        self.tracks.paint(state, &mut nodes, bounds.width, bounds.height);
        nodes.finish()
    }

    // As an interactive canvas: the press the tracks take focuses them, so
    // the keys the timeline ignores reach `on_key` again.
    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        let bounds = layout.bounds();
        let cursor = shell.cursor();
        let now = shell.store().now();
        let state = shell.store_mut().state_mut::<Pointer>(&self.id);
        let (status, message) = self.tracks.update(state, event, bounds, cursor, now);
        if status == Status::Captured
            && matches!(event, Event::Mouse(mouse::Event::ButtonPressed(_)))
            && !shell.store_mut().is_focused(&self.id)
        {
            shell.store_mut().set_focus(self.id);
            shell.request_redraw();
        }
        if let Some(message) = message {
            shell.publish(message);
        }
        status
    }

    fn mouse_interaction(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> mouse::Interaction {
        let idle = Pointer::default();
        let state = ctx.store.state::<Pointer>(&self.id).unwrap_or(&idle);
        self.tracks.mouse_interaction(state, layout.bounds(), ctx.cursor)
    }

    fn hover(&self, layout: Layout<'_>, _ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        // The cursor shape follows the clip edges under the pointer.
        regions.motion(layout.bounds());
    }
}

impl<Message: 'static> From<TrackView<Message>> for Element<Message> {
    fn from(view: TrackView<Message>) -> Self {
        Element::new(view)
    }
}

impl<Message> Tracks<Message> {
    /// Paint the tracks `w` by `h` (local pixels) into `p`, the dragged
    /// clip's outline from `state`. Rectangles, the hatching, thumbnails and
    /// words first; then, above them all ([`Paint::raise`]), the markers, the
    /// fade ramps, the selection, a drag's outline and the playhead.
    fn paint(&self, state: &Pointer, p: &mut impl Paint, w: f32, h: f32) {
        p.rect(Point::new(0.0, 0.0), Size::new(w, h), look::seam());
        p.rect(Point::new(0.0, 0.0), Size::new(w, RULER), look::panel());
        let slot = (w - HEADER) / self.total.max(1) as f32;
        let step = tick_step(slot);
        if step != usize::MAX {
            // The ticks of the frames shown: from the first on a step.
            let from = (self.first.max(0.0).ceil() as usize).div_ceil(step) * step;
            let to = (self.first + self.total as f64).ceil().max(0.0) as usize;
            for f in (from..to).step_by(step) {
                let tall = if f % (step * 5) == 0 { 8.0 } else { 4.0 };
                p.rect(Point::new(self.x(f as EditFrame, w), RULER - STRIP - tall), Size::new(1.0, tall), look::tick());
            }
        }
        for &(start, end) in &self.buffered {
            if let Some((x0, x1)) =
                Self::seen(self.x(start as EditFrame, w), self.x(end as EditFrame, w).max(self.x(start as EditFrame, w) + 1.0), w)
            {
                p.rect(Point::new(x0, RULER - STRIP), Size::new((x1 - x0).max(1.0), STRIP), look::buffered());
            }
        }

        // The badges (Preview ready, the farm's state) and the farm's line,
        // painted just above this level: a level draws its quads under its
        // images, so on a clip with nothing to play the hatching striped
        // over a badge's ground and the line (live test, 2026-11-21, a
        // Queued badge on a shot not rendered yet). Their words were above.
        let mut over: Vec<Over> = Vec::new();
        let mut top = RULER;
        for row in &self.rows {
            let tall = row.height;
            let band = if row.track.kind == TrackKind::Video { look::band() } else { look::band_audio() };
            p.rect(Point::new(0.0, top), Size::new(HEADER - 1.0, tall - 1.0), look::panel());
            marks::paint(p, row, top);
            p.rect(Point::new(HEADER, top), Size::new(w - HEADER, tall - 1.0), band);
            let label = Rectangle { x: 10.0, y: top, width: HEADER - 14.0, height: tall };
            if row.solo {
                // The one track shown: its name lit, "solo" under it.
                let name = Rectangle { height: tall / 2.0 + 2.0, ..label };
                let word = Rectangle { y: top + tall / 2.0 + 1.0, height: 12.0, ..label };
                p.text(row.name.clone(), name, H::Left, V::Bottom, 13.0, look::text());
                p.text("solo".to_owned(), word, H::Left, V::Top, 10.0, look::quiet());
            } else if row.spare {
                // Not a track yet: its name faint, and what it is for.
                p.text(row.name.clone(), label, H::Left, V::Center, 13.0, look::tick());
                let hint = Rectangle { x: HEADER + 10.0, y: top, width: (w - HEADER - 20.0).max(1.0), height: tall };
                p.text("Drag a clip here for a new layer".to_owned(), hint, H::Left, V::Center, 11.0, look::tick());
            } else {
                p.text(row.name.clone(), label, H::Left, V::Center, 13.0, look::quiet());
            }
            for c in &row.clips {
                let Some(rect) = self.clip_rect(c, top, tall, w) else { continue };
                let body = match (row.track.kind, c.missing) {
                    (_, true) => look::missing(),
                    (TrackKind::Video, false) => c.department.as_deref().map_or_else(look::clip, look::department),
                    (TrackKind::Audio, false) => look::audio(),
                };
                p.rect(Point::new(rect.x, rect.y), Size::new(rect.width, rect.height), body);
                if c.missing {
                    p.hatch(rect, look::hatch());
                }
                if row.track.kind == TrackKind::Audio {
                    self.paint_wave(p, c, rect, w);
                }
                let mut left = rect.x + INSET;
                if let Some(handle) = &c.thumbnail {
                    let (iw, ih) = (handle.image().width.max(1) as f32, handle.image().height.max(1) as f32);
                    let tall = rect.height - 2.0 * INSET;
                    let wide = (tall * iw / ih).min(rect.width - 2.0 * INSET);
                    if wide >= 12.0 {
                        p.image(handle, Rectangle { x: left, y: rect.y + INSET, width: wide, height: wide * ih / iw });
                        left += wide + INSET;
                    }
                }
                let mut right = rect.x + rect.width;
                if let (TrackKind::Video, Some(words)) = (row.track.kind, c.preview)
                    && let Some(tag) = badge_box(rect, 86.0)
                {
                    over.push(Over::Tag(tag, look::now(), words.to_owned(), look::surround()));
                    right = tag.x - INSET;
                }
                if let (TrackKind::Video, Some(status)) = (row.track.kind, c.farm) {
                    let end = if c.preview.is_some() { right } else { right - INSET };
                    let marks = crate::farm_jobs::marks(rect, end, status);
                    let (fill, words) = crate::farm_jobs::colour(status);
                    over.push(Over::Rect(marks.line, fill));
                    match marks.badge {
                        Some(crate::farm_jobs::Badge::Word(tag)) => {
                            over.push(Over::Tag(tag, fill, status.word().to_owned(), words));
                            right = tag.x - INSET;
                        }
                        Some(crate::farm_jobs::Badge::Dot(dot)) => {
                            over.push(Over::Dot(dot, fill));
                            right = dot.x - INSET;
                        }
                        None => {}
                    }
                }
                let room = right - left - INSET;
                if room >= 10.0 {
                    let line = |y: f32, tall: f32| Rectangle { x: left, y: rect.y + y, width: room, height: tall };
                    // A row dragged too low for both lines keeps the name.
                    let both = rect.height >= FADE_BAND + 16.0 + 13.0;
                    let name_y = if both { FADE_BAND } else { ((rect.height - 15.0) / 2.0).clamp(0.0, FADE_BAND) };
                    p.text(c.name.clone(), line(name_y, 15.0), H::Left, V::Top, 12.0, look::text());
                    if both {
                        p.text(
                            c.detail.clone(),
                            line(FADE_BAND + 16.0, 13.0),
                            H::Left,
                            V::Top,
                            10.0,
                            if c.missing { look::error() } else { look::quiet_on_clip() },
                        );
                    }
                }
            }
            for strip in self.rendering.iter().filter(|s| s.track == row.track) {
                for (x, wide, there) in crate::rendering::bars(strip, |frame| self.x(frame, w)) {
                    let y = top + tall - 2.0 - crate::rendering::STRIP;
                    let bar = Rectangle { x, y, width: wide, height: crate::rendering::STRIP };
                    over.push(Over::Rect(bar, if there { look::now() } else { look::surround() }));
                }
            }
            top += tall;
        }
        if let Some(note) = &self.note {
            p.rect(Point::new(0.0, top), Size::new(w, NOTE - 1.0), look::panel());
            let line = Rectangle { x: 10.0, y: top, width: (w - 20.0).max(1.0), height: NOTE - 1.0 };
            p.text(note.clone(), line, H::Left, V::Center, 11.0, look::quiet());
        }

        p.raise();
        for o in over {
            match o {
                Over::Rect(r, color) => p.rect(Point::new(r.x, r.y), Size::new(r.width, r.height), color),
                Over::Tag(tag, ground, words, ink) => {
                    p.rect(Point::new(tag.x, tag.y), Size::new(tag.width, tag.height), ground);
                    p.text(words, tag, H::Center, V::Center, 10.0, ink);
                }
                Over::Dot(dot, color) => {
                    p.fill(&Path::circle(Point::new(dot.x + dot.width / 2.0, dot.y + dot.height / 2.0), dot.width / 2.0), color);
                }
            }
        }
        let marker = look::mark();
        for &m in &self.markers {
            let x = self.x(m, w) + slot / 2.0;
            if !(HEADER..=w).contains(&x) {
                continue;
            }
            let tip = Path::new(|b| {
                b.move_to(Point::new(x - 5.0, 0.0));
                b.line_to(Point::new(x + 5.0, 0.0));
                b.line_to(Point::new(x, 8.0));
                b.close();
            });
            p.fill(&tip, marker);
        }
        for (r, row) in self.rows.iter().enumerate() {
            let top = self.row_top(r);
            for c in &row.clips {
                let Some(rect) = self.clip_rect(c, top, row.height, w) else { continue };
                // Fades: a ramp from the clip's corner over its top band;
                // not drawn for an end that is scrolled out of sight.
                let ramp = Color::from_rgba8(240, 240, 240, 140);
                let (head_seen, tail_seen) = (self.x(c.at, w) >= HEADER, self.x(c.at + c.duration, w) <= w);
                if c.fade_in > 0 && head_seen {
                    let xe = self.x(c.at + c.fade_in, w).min(w);
                    let tri = Path::new(|b| {
                        b.move_to(Point::new(rect.x, rect.y + rect.height));
                        b.line_to(Point::new(xe, rect.y));
                        b.line_to(Point::new(rect.x, rect.y));
                        b.close();
                    });
                    p.fill(&tri, Color::from_rgba8(0, 0, 0, 90));
                    p.stroke(&Path::line(Point::new(rect.x, rect.y + rect.height), Point::new(xe, rect.y)), Stroke::new(1.0, ramp));
                }
                if c.fade_out > 0 && tail_seen {
                    let xs = self.x(c.at + c.duration - c.fade_out, w).max(HEADER);
                    let xr = rect.x + rect.width;
                    let tri = Path::new(|b| {
                        b.move_to(Point::new(xr, rect.y + rect.height));
                        b.line_to(Point::new(xs, rect.y));
                        b.line_to(Point::new(xr, rect.y));
                        b.close();
                    });
                    p.fill(&tri, Color::from_rgba8(0, 0, 0, 90));
                    p.stroke(&Path::line(Point::new(xs, rect.y), Point::new(xr, rect.y + rect.height)), Stroke::new(1.0, ramp));
                }
                if c.selected {
                    outline(p, rect, 2.0, look::mark());
                }
            }
        }

        if let Some(grab) = &state.grab
            && let Some((ghost, Reach { dx, stop }, landing)) = self.ghost(grab, w, state.at.0, state.at.1)
        {
            outline(p, ghost, 2.0, look::now());
            if let Some(point) = landing.and_then(|l| l.snapped).filter(|&point| self.x(point, w) >= HEADER) {
                // The line it snapped to, down every row.
                p.rect(Point::new(self.x(point, w) - 0.5, RULER), Size::new(1.0, h - RULER), look::now());
            }
            let mut words = self.held(grab).map(|held| self.drag_words(grab, held, Reach { dx, stop }, landing)).unwrap_or_default();
            let mut colour = look::now();
            if stop == Some(Stop::Footage) {
                // The edge is at the end of its source's frames: a bar on
                // it, and past it the hatching of a clip with nothing to
                // play, so it reads as "no footage there", not as stuck.
                let at_head = match grab.part {
                    Part::Slip => dx > 0,
                    part => matches!(part, Part::Head | Part::RollHead),
                };
                let edge = if at_head { ghost.x } else { ghost.x + ghost.width };
                let beyond = Rectangle {
                    x: if at_head { edge - LIMIT_BEYOND } else { edge },
                    y: ghost.y,
                    width: LIMIT_BEYOND,
                    height: ghost.height,
                };
                p.hatch(beyond, look::error());
                p.rect(Point::new(edge - LIMIT_BAR / 2.0, ghost.y), Size::new(LIMIT_BAR, ghost.height), look::error());
                words.push_str(" \u{b7} no more footage");
                colour = look::error();
            }
            let tag = Rectangle { x: ghost.x + 4.0, y: ghost.y + ghost.height - 15.0, width: 220.0, height: 14.0 };
            p.text(words, tag, H::Left, V::Center, 11.0, colour);
        }

        if let Some(gap) = self.gap
            && let Some(r) = self.rows.iter().position(|row| !row.spare && row.track == gap.track)
            && let Some((from, to)) = picks::gap_before(&self.row_spans()[r], gap.index)
            && let Some((x0, x1)) = Self::seen(self.x(from, w), self.x(to, w), w)
        {
            // The gap selected: washed and outlined as a selected clip is.
            let rect = Rectangle { x: x0, y: self.row_top(r) + 1.0, width: (x1 - x0 - 1.0).max(1.0), height: self.rows[r].height - 3.0 };
            p.hatch(rect, look::mark());
            outline(p, rect, 2.0, look::mark());
        }
        if let Some((from, row, true)) = state.sweep
            && let Some((at, end)) = self.swept(row, from.0, state.at.0, w)
            && let Some((x0, x1)) = Self::seen(self.x(at, w), self.x(end, w), w)
        {
            // The frames swept, outlined in the present's amber, and how
            // many: what a new shot there would be.
            let rect =
                Rectangle { x: x0, y: self.row_top(row) + 1.0, width: (x1 - x0 - 1.0).max(1.0), height: self.rows[row].height - 3.0 };
            outline(p, rect, 1.0, look::now());
            let frames = end - at;
            let words = format!("{frames} frame{}: let go for a new shot", if frames == 1 { "" } else { "s" });
            let tag = Rectangle { x: x0 + 4.0, y: rect.y + 2.0, width: 260.0, height: 14.0 };
            p.text(words, tag, H::Left, V::Center, 11.0, look::now());
        }
        if let Some((from, true)) = state.marquee {
            let (x0, x1) = (from.0.min(state.at.0).max(HEADER), from.0.max(state.at.0).max(HEADER));
            let (y0, y1) = (from.1.min(state.at.1).max(RULER), from.1.max(state.at.1).max(RULER));
            outline(p, Rectangle { x: x0, y: y0, width: (x1 - x0).max(1.0), height: (y1 - y0).max(1.0) }, 1.0, look::now());
        }

        let centre = self.x(self.playhead as EditFrame, w) + slot / 2.0;
        if (HEADER..=w).contains(&centre) {
            let accent = look::now();
            p.rect(Point::new(centre - 1.0, 0.0), Size::new(2.0, h), accent);
            p.rect(Point::new(centre - 4.0, 0.0), Size::new(8.0, 3.0), accent);
        }
    }

    /// Clip `c`'s waveform over its body drawn at `rect`: a bar per pixel
    /// column on screen, or a line while its sound is being read.
    fn paint_wave(&self, p: &mut impl Paint, c: &ClipView, rect: Rectangle, w: f32) {
        match &c.wave {
            None => {}
            Some(crate::waveform::Wave::Pending) => {
                p.rect(Point::new(rect.x, (rect.y + rect.height / 2.0).floor()), Size::new(rect.width, 1.0), look::wave_pending());
            }
            Some(crate::waveform::Wave::Ready(placed)) => {
                for bar in crate::waveform::bars(placed, rect, |x| self.frame_at(x, w)) {
                    p.rect(Point::new(bar.x, bar.y), Size::new(bar.width, bar.height), look::wave());
                }
            }
        }
    }

    /// Where clip `c` of the row at `top` is drawn: the part of it in the
    /// tracks' time; None when it is scrolled out of sight.
    fn clip_rect(&self, c: &ClipView, top: f32, tall: f32, w: f32) -> Option<Rectangle> {
        let (x0, x1) = (self.x(c.at, w), self.x(c.at + c.duration, w));
        let (x0, x1) = Self::seen(x0, (x1 - 1.0).max(x0 + 1.0), w)?;
        Some(Rectangle { x: x0, y: top + 1.0, width: x1 - x0, height: tall - 3.0 })
    }

    /// What the wheel, turned by `delta` with the pointer at local `x`,
    /// asks of the view ([`ViewChange`]); None when it is left to whatever
    /// scrolls the tracks up and down.
    fn wheel(&self, modifiers: keyboard::Modifiers, delta: Vector, x: f32, width: f32) -> Option<ViewChange> {
        if modifiers.command() {
            // Away from the user (up) zooms in.
            let factor = ZOOM_NOTCH.powf(f64::from(delta.y / WHEEL_NOTCH));
            return Some(ViewChange::Zoom { about: self.frame_at(x.max(HEADER), width), factor });
        }
        let along = match (modifiers.shift, delta.x != 0.0) {
            (true, false) => delta.y,
            (true, true) => delta.x,
            (false, _) if delta.x.abs() > delta.y.abs() => delta.x,
            (false, _) => return None,
        };
        // The wheel towards the user (down, or left) shows what is later.
        let frames_a_pixel = self.total.max(1) as f64 / f64::from((width - HEADER).max(1.0));
        Some(ViewChange::Scroll(-f64::from(along) * frames_a_pixel))
    }

    /// What a pointer or key `event` does to the tracks laid out in
    /// `bounds`, the pointer at `cursor`: the gesture's `state` moves on,
    /// and what it sends, if anything. The widget's whole behaviour, so the
    /// tests (the editor's too) drive the tracks through it.
    pub(crate) fn update(
        &self,
        state: &mut Pointer,
        event: &Event,
        bounds: Rectangle,
        cursor: Cursor,
        now: std::time::Instant,
    ) -> (Status, Option<Message>) {
        let w = bounds.width;
        match event {
            Event::Keyboard(keyboard::Event::ModifiersChanged(modifiers)) => {
                state.modifiers = *modifiers;
                // A held clip's outline follows the modifier: redraw it.
                let held = state.grab.as_mut().map(|grab| grab.push = modifiers.command());
                (Status::Ignored, held.map(|()| (self.on_drag)()))
            }
            Event::Mouse(mouse::Event::WheelScrolled { delta }) if self.editable => {
                let Some(local) = cursor.position_relative_to(bounds) else { return (Status::Ignored, None) };
                match self.wheel(state.modifiers, *delta, local.x, w) {
                    Some(change) => (Status::Captured, Some((self.on_view)(change))),
                    None => (Status::Ignored, None),
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(local) = cursor.position_relative_to(bounds) else { return (Status::Ignored, None) };
                if let Some(row) = self.edge_at(local.x, local.y) {
                    state.resize = Some((self.rows[row].track, local.y, self.rows[row].height));
                    return (Status::Captured, None);
                }
                if local.x < HEADER {
                    // A second press on the same track's name soon after
                    // the first: solo it (or show the others again).
                    let named = |r: &usize| self.editable && !self.rows[*r].spare;
                    let Some(track) = self.row_at(local.y).filter(named).map(|r| self.rows[r].track) else {
                        return (Status::Captured, None);
                    };
                    let double = lanes::completes_double_click(state.header_press, track, now);
                    state.header_press = if double { None } else { Some((track, now)) };
                    return (Status::Captured, double.then(|| (self.on_solo)(track)));
                }
                if local.y < RULER || !self.editable {
                    state.scrubbing = true;
                    return (Status::Captured, Some((self.on_seek)(self.position_at(local.x, w))));
                }
                match self.hit(local.x, local.y, w, state.modifiers.alt) {
                    Some((row, index, part)) => {
                        let additive = state.modifiers.shift || state.modifiers.command();
                        let clip = ClipRef { track: self.rows[row].track, index };
                        let taken = &self.rows[row].clips[index];
                        state.grab = Some(Grab {
                            clip,
                            span: (taken.at, taken.duration),
                            cut: self.cut,
                            part,
                            from_x: local.x,
                            push: state.modifiers.command(),
                        });
                        state.at = (local.x, local.y);
                        if state.modifiers.shift && !state.modifiers.command() {
                            return (Status::Captured, Some((self.on_gesture)(Gesture::Range(clip))));
                        }
                        (Status::Captured, Some((self.on_select)(Some(clip), additive, self.position_at(local.x, w))))
                    }
                    None => {
                        // Alt on empty video track: a drag from here sweeps
                        // frames for a new clip ([`sweep`]); the playhead
                        // comes here, nothing is picked.
                        if state.modifiers.alt
                            && let Some(row) = self.row_at(local.y).filter(|&r| self.sweeps(r))
                        {
                            state.sweep = Some(((local.x, local.y), row, false));
                            state.at = (local.x, local.y);
                            return (Status::Captured, Some((self.on_select)(None, false, self.position_at(local.x, w))));
                        }
                        // Empty track: the playhead comes here, the gap
                        // under the press is picked, and a drag from here
                        // draws a box.
                        state.marquee = Some(((local.x, local.y), false));
                        state.at = (local.x, local.y);
                        let position = self.position_at(local.x, w);
                        let gap = self.row_at(local.y).filter(|&r| !self.rows[r].spare).and_then(|r| {
                            let (index, _, _) = picks::gap_at(&self.row_spans()[r], self.frame_at(local.x, w).floor() as EditFrame)?;
                            Some(ClipRef { track: self.rows[r].track, index })
                        });
                        let message = match gap {
                            Some(clip) => (self.on_gesture)(Gesture::Gap(clip, position)),
                            None => (self.on_select)(None, false, position),
                        };
                        (Status::Captured, Some(message))
                    }
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) if !self.editable => (Status::Ignored, None),
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) => match cursor.position_relative_to(bounds) {
                Some(local) => match self.hit(local.x, local.y, w, false) {
                    Some((row, index, _)) => (Status::Ignored, Some((self.on_menu)(ClipRef { track: self.rows[row].track, index }))),
                    // Not taken: the menu around the tracks opens on it.
                    None => {
                        let (frame, track) =
                            context_at(&self.rows, self.row_at(local.y), (local.x >= HEADER).then(|| self.position_at(local.x, w)));
                        (Status::Ignored, Some((self.on_context)(frame, track)))
                    }
                },
                None => (Status::Ignored, None),
            },
            Event::Mouse(mouse::Event::CursorMoved { position }) => {
                let local = (position.x - bounds.x, position.y - bounds.y);
                if let Some((track, from_y, from)) = state.resize {
                    return (Status::Captured, Some((self.on_row_height)(track, lanes::dragged_height(from, local.1 - from_y))));
                }
                if state.scrubbing {
                    return (Status::Captured, Some((self.on_seek)(self.position_at(local.0, w))));
                }
                if state.grab.is_some() {
                    state.at = local;
                    return (Status::Captured, Some((self.on_drag)()));
                }
                if let Some((from, row, drawn)) = state.sweep {
                    state.at = local;
                    if !(drawn || (local.0 - from.0).abs() >= BOX_SLOP) {
                        return (Status::Captured, None);
                    }
                    state.sweep = Some((from, row, true));
                    return (Status::Captured, Some((self.on_drag)()));
                }
                if let Some((from, drawn)) = state.marquee {
                    state.at = local;
                    let far = (local.0 - from.0).abs().max((local.1 - from.1).abs()) >= BOX_SLOP;
                    if !(drawn || far) {
                        return (Status::Captured, None);
                    }
                    state.marquee = Some((from, true));
                    return (Status::Captured, Some((self.on_gesture)(Gesture::Box(self.boxed(from, local, w)))));
                }
                (Status::Ignored, None)
            }
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                state.scrubbing = false;
                if state.resize.take().is_some() {
                    return (Status::Captured, None);
                }
                if let Some((_, drawn)) = state.marquee.take() {
                    // The box goes; what it picked stays picked.
                    return (Status::Captured, drawn.then(|| (self.on_drag)()));
                }
                if let Some((from, row, drawn)) = state.sweep.take() {
                    // Let go: the frames swept go to the editor, which
                    // offers what to make of them.
                    let local = cursor.position_relative_to(bounds).map_or(state.at, |p| (p.x, p.y));
                    let swept = if drawn { self.swept(row, from.0, local.0, w) } else { None };
                    let gesture = swept.map(|(at, end)| Gesture::Sweep { track: self.rows[row].track, at, frames: end - at });
                    return (Status::Captured, Some(gesture.map_or_else(|| (self.on_drag)(), self.on_gesture)));
                }
                let Some(grab) = state.grab.take() else { return (Status::Ignored, None) };
                // The cut changed under the pointer: the drag is dropped.
                let Some(held) = self.held(&grab) else { return (Status::Captured, None) };
                let local = cursor.position_relative_to(bounds).map_or(state.at, |p| (p.x, p.y));
                let dx = self.frames_dragged(&grab, local.0, w);
                let row = &self.rows[held];
                let op = if grab.part() == Part::Body {
                    // A move lands where there is room for it ([`landing`]).
                    let target = self.target_row(&grab, held, local.1);
                    let landing = self.landing(&grab, held, target, dx, w);
                    op_for(Part::Body, grab.clip, &row.clips[grab.clip.index], landing.delta, Some(self.rows[target].track))
                } else {
                    drag_op(grab.part(), row.track, &row.clips, grab.clip.index, dx, None).0
                };
                (Status::Captured, op.map(self.on_op))
            }
            _ => (Status::Ignored, None),
        }
    }

    fn mouse_interaction(&self, state: &Pointer, bounds: Rectangle, cursor: Cursor) -> mouse::Interaction {
        if state.resize.is_some() {
            return mouse::Interaction::ResizeVertical;
        }
        if state.grab.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let Some(local) = cursor.position_relative_to(bounds) else { return mouse::Interaction::Idle };
        if self.edge_at(local.x, local.y).is_some() {
            return mouse::Interaction::ResizeVertical;
        }
        if !self.editable {
            return mouse::Interaction::Pointer;
        }
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
            farm: None,
            room: None,
            wave: None,
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
        // A push stays on its track, wherever it is let go.
        assert_eq!(op_for(Part::Push, clip, &v, 7, Some(TrackRef::video(1))), Some(Op::Shift { clip, delta: 7 }));
        assert_eq!(op_for(Part::Push, clip, &v, 0, Some(TrackRef::video(1))), None);
        // It is held by the clip before it, or by the cut's start.
        let row = [view(10, 20, 0, 0), view(40, 10, 0, 0), view(60, 5, 0, 0)];
        let pushed = |index, dx| drag_op(Part::Push, TrackRef::video(0), &row, index, dx, None);
        assert_eq!(
            pushed(1, -30),
            (
                Some(Op::Shift { clip: ClipRef { track: TrackRef::video(0), index: 1 }, delta: -10 }),
                Reach { dx: -10, stop: Some(Stop::Clip) }
            )
        );
        assert_eq!(pushed(0, -30).1, Reach { dx: -10, stop: Some(Stop::Clip) });
        assert_eq!(pushed(1, 25).1, Reach { dx: 25, stop: None });
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

    /// A painter that counts what would be canvas geometry: the paths.
    #[derive(Default)]
    struct Geometry {
        paths: usize,
    }

    impl Paint for Geometry {
        fn rect(&mut self, _: Point, _: Size, _: Color) {}
        fn hatch(&mut self, _: Rectangle, _: Color) {}
        fn fill(&mut self, _: &Path, _: Color) {
            self.paths += 1;
        }
        fn stroke(&mut self, _: &Path, _: Stroke) {
            self.paths += 1;
        }
        fn text(&mut self, _: String, _: Rectangle, _: H, _: V, _: f32, _: Color) {}
        fn image(&mut self, _: &ImageHandle, _: Rectangle) {}
        fn raise(&mut self) {}
    }

    /// A painter that keeps, per level, the order of its hatchings and quads:
    /// `Nodes` draws a level's quads under its images, so a quad painted
    /// after a hatching in the same level still ends up under its stripes.
    #[derive(Default)]
    struct Order {
        level: usize,
        hatched: Vec<(usize, Rectangle)>,
        /// Quads that would sit under a hatching painted before them.
        striped: Vec<Rectangle>,
    }

    impl Paint for Order {
        fn rect(&mut self, top_left: Point, size: Size, _: Color) {
            let r = Rectangle::new(top_left, size);
            let overlaps = |h: &Rectangle| r.x < h.x + h.width && h.x < r.x + r.width && r.y < h.y + h.height && h.y < r.y + r.height;
            if self.hatched.iter().any(|(level, h)| *level == self.level && overlaps(h)) {
                self.striped.push(r);
            }
        }
        fn hatch(&mut self, r: Rectangle, _: Color) {
            self.hatched.push((self.level, r));
        }
        fn fill(&mut self, _: &Path, _: Color) {}
        fn stroke(&mut self, _: &Path, _: Stroke) {}
        fn text(&mut self, _: String, _: Rectangle, _: H, _: V, _: f32, _: Color) {}
        fn image(&mut self, _: &ImageHandle, _: Rectangle) {}
        fn raise(&mut self) {
            self.level += 1;
        }
    }

    proptest! {
        /// Nothing a clip paints over itself is striped by the hatching of a
        /// clip with nothing to play: the farm's line and badge and the
        /// Preview ready badge sit above it. A Queued badge on a shot not
        /// rendered yet was crossed out (live test, 2026-11-21).
        #[test]
        fn nothing_drawn_over_a_clip_is_under_its_hatching(
            clips in prop::collection::vec((0i64..30, 1i64..120, any::<bool>(), any::<bool>(), proptest::option::of(0usize..8)), 1..8),
            width in 200f32..3000.0,
            height in ROW_MIN..=ROW_MAX,
        ) {
            // A track's clips never overlap: each starts after a gap past the last.
            let mut end = 0;
            let views: Vec<ClipView> = clips
                .iter()
                .map(|&(gap, duration, missing, preview, farm)| {
                    let at = end + gap;
                    end = at + duration;
                    ClipView {
                        missing,
                        preview: preview.then_some("Preview ready"),
                        farm: farm.map(|n| th_pipe::farm_jobs::Status::ALL[n]),
                        name: "SH010".into(),
                        ..view(at, duration, 0, 0)
                    }
                })
                .collect();
            // A render on its way over each clip (a hatched clip keeps its
            // hatching until the render is done, its strip along the bottom).
            let strips: Vec<crate::rendering::Strip> = views
                .iter()
                .map(|c| crate::rendering::Strip {
                    track: TrackRef::video(0),
                    expected: vec![(c.at, c.at + c.duration)],
                    there: vec![(c.at, c.at + c.duration / 2)],
                })
                .collect();
            let row = RowView { track: TrackRef::video(0), name: "V1".into(), clips: views, height, solo: false, spare: false, color: None };
            let mut tracks = tracks_of(vec![row], Vec::new(), 600);
            tracks.rendering = strips;
            let mut painted = Order::default();
            tracks.paint(&Pointer::default(), &mut painted, width, tracks.height());
            prop_assert!(painted.striped.is_empty(), "under the hatching: {:?}", painted.striped);
        }
    }

    fn tracks_of(rows: Vec<RowView>, markers: Vec<EditFrame>, total: usize) -> Tracks<()> {
        Tracks {
            rows,
            markers,
            total,
            playhead: 0,
            buffered: vec![(0, total / 2)],
            rendering: Vec::new(),
            on_seek: |_| (),
            on_select: |_, _, _| (),
            on_drag: || (),
            on_op: |_| (),
            on_menu: |_| (),
            on_row_height: |_, _| (),
            on_solo: |_| (),
            on_context: |_, _| (),
            on_gesture: |_| (),
            first: 0.0,
            on_view: |_| (),
            cut: 0,
            gap: None,
            note: None,
            editable: true,
        }
    }

    proptest! {
        /// The tracks paint no geometry (which would multisample the whole
        /// window, see `Paint`) unless a clip has a fade or the cut a
        /// marker: missing (hatched), selected and dragged clips, the
        /// ruler and the playhead are all quads or images.
        #[test]
        fn only_fades_and_markers_are_geometry(
            clips in prop::collection::vec((0i64..400, 1i64..200, 0i64..3, 0i64..3, any::<bool>(), any::<bool>()), 0..12),
            markers in prop::collection::vec(0i64..600, 0..3),
            width in 60f32..3000.0,
            grabbed in any::<bool>(),
            height in ROW_MIN..=ROW_MAX,
        ) {
            let views: Vec<ClipView> = clips
                .iter()
                .map(|&(at, duration, fade_in, fade_out, missing, selected)| ClipView {
                    missing,
                    selected,
                    ..view(at, duration, fade_in.min(duration), fade_out.min(duration - fade_in.min(duration)))
                })
                .collect();
            let faded = views.iter().any(|c| c.fade_in > 0 || c.fade_out > 0);
            let row = RowView { track: TrackRef::video(0), name: "V1".into(), clips: views, height, solo: grabbed, spare: false, color: None };
            let over = RowView { track: TrackRef::video(1), name: "V2".into(), clips: Vec::new(), height, solo: false, spare: true, color: None };
            let any_clip = !row.clips.is_empty();
            let tracks = tracks_of(vec![over, row], markers.clone(), 600);
            let mut state = Pointer::default();
            if grabbed && any_clip {
                let taken = &tracks.rows[1].clips[0];
                let clip = ClipRef { track: TrackRef::video(0), index: 0 };
                state.grab = Some(Grab { clip, span: (taken.at, taken.duration), cut: 0, part: Part::Body, from_x: 100.0, push: false });
                state.at = (140.0, RULER + 10.0);
            }
            let mut painted = Geometry::default();
            tracks.paint(&state, &mut painted, width, tracks.height());
            prop_assert_eq!(painted.paths > 0, faded || !markers.is_empty());
        }

        /// The hatching tile is a line gap periodic both ways, so tiles laid
        /// side by side (and any row height) continue its lines; the lines
        /// rise left to right, and cover between nothing and all of a pixel.
        #[test]
        fn hatching_tiles_seamlessly(x in 0f32..HATCH_TILE_WIDE, y in 0f32..ROW) {
            let here = hatch_coverage(x, y);
            prop_assert!((0.0..=1.0).contains(&here));
            prop_assert!((hatch_coverage(x + HATCH_TILE_WIDE, y) - here).abs() < 1e-3);
            prop_assert!((hatch_coverage(x, y + HATCH_TILE_TALL) - here).abs() < 1e-3);
            prop_assert!((hatch_coverage(x + 1.0, y - 1.0) - here).abs() < 1e-3);
        }
    }

    /// What the tracks under test send.
    #[derive(Debug, Clone, PartialEq)]
    enum Sent {
        Seek(usize),
        Select(Option<ClipRef>, bool, usize),
        Drag,
        Op(Op<Source>),
        Menu(ClipRef),
        RowHeight(TrackRef, f32),
        Solo(TrackRef),
        Context(Option<usize>, Option<TrackRef>),
        Gesture(Gesture),
        View(ViewChange),
    }

    /// The frames the tracks under test span.
    const TOTAL: usize = 400;

    fn sending(rows: Vec<RowView>, note: bool) -> Tracks<Sent> {
        Tracks {
            rows,
            markers: Vec::new(),
            total: TOTAL,
            playhead: 0,
            buffered: Vec::new(),
            rendering: Vec::new(),
            on_seek: Sent::Seek,
            on_select: Sent::Select,
            on_drag: || Sent::Drag,
            on_op: Sent::Op,
            on_menu: Sent::Menu,
            on_row_height: Sent::RowHeight,
            on_solo: Sent::Solo,
            on_context: Sent::Context,
            on_gesture: Sent::Gesture,
            first: 0.0,
            on_view: Sent::View,
            cut: 0,
            gap: None,
            note: note.then(|| "1 audio track hidden".to_owned()),
            editable: true,
        }
    }

    /// One track's clips: each the gap before it and its length.
    type Spans = Vec<(EditFrame, EditFrame)>;

    fn spans() -> impl Strategy<Value = Spans> {
        prop::collection::vec((0i64..20, 1i64..30), 0..4)
    }

    fn row_of(track: TrackRef, spans: &Spans, height: u32) -> RowView {
        let mut at = 0;
        let clips = spans
            .iter()
            .map(|&(gap, duration)| {
                at += gap;
                let clip = view(at, duration, 0, 0);
                at += duration;
                clip
            })
            .collect();
        RowView { track, name: String::new(), clips, height: height as f32, solo: false, spare: false, color: None }
    }

    /// Rows as the editor makes them: the spare layer first when there is
    /// one (the video track one past the top, empty), then the video
    /// tracks top first, then the audio tracks; each as tall as a row may
    /// be dragged (24 to 240 pixels).
    fn rows() -> impl Strategy<Value = Vec<RowView>> {
        let track = || (spans(), 24u32..=240);
        (prop::option::of(24u32..=240), prop::collection::vec(track(), 1..4), prop::collection::vec(track(), 0..3)).prop_map(
            |(spare, video, audio)| {
                let mut rows = Vec::new();
                if let Some(height) = spare {
                    rows.push(RowView { spare: true, ..row_of(TrackRef::video(video.len()), &Vec::new(), height) });
                }
                for (index, (spans, height)) in video.iter().enumerate().rev() {
                    rows.push(row_of(TrackRef::video(index), spans, *height));
                }
                for (index, (spans, height)) in audio.iter().enumerate() {
                    rows.push(row_of(TrackRef::audio(index), spans, *height));
                }
                rows
            },
        )
    }

    /// The tracks on screen: their top-left anywhere in the window (they
    /// scroll), a whole number of pixels a frame.
    #[derive(Debug, Clone, Copy)]
    struct Stage {
        origin: (f32, f32),
        px: f32,
    }

    fn stage() -> impl Strategy<Value = Stage> {
        ((-500i32..500, -500i32..500), 4u8..9)
            .prop_map(|(origin, px)| Stage { origin: (origin.0 as f32, origin.1 as f32), px: f32::from(px) })
    }

    impl Stage {
        fn bounds(&self, tracks: &Tracks<Sent>) -> Rectangle {
            Rectangle { x: self.origin.0, y: self.origin.1, width: HEADER + self.px * TOTAL as f32, height: tracks.height() }
        }

        /// The window point `x`, `y` pixels from the tracks' top-left.
        fn point(&self, x: f32, y: f32) -> Point {
            Point::new(self.origin.0 + x, self.origin.1 + y)
        }

        /// The middle of `frame`, in pixels from the tracks' left.
        fn x(&self, frame: EditFrame) -> f32 {
            HEADER + self.px * (frame as f32 + 0.5)
        }

        /// `within` (0 to 1) of the way down row `row`, in pixels from the
        /// tracks' top: the ruler, then the heights of the rows above.
        fn y(&self, rows: &[RowView], row: usize, within: f32) -> f32 {
            20.0 + rows[..row].iter().map(|r| r.height).sum::<f32>() + within * rows[row].height
        }

        fn send(
            &self,
            tracks: &Tracks<Sent>,
            state: &mut Pointer,
            event: mouse::Event,
            at: Point,
            now: std::time::Instant,
        ) -> Option<Sent> {
            tracks.update(state, &Event::Mouse(event), self.bounds(tracks), Cursor::Available(at), now).1
        }
    }

    const LEFT: mouse::Event = mouse::Event::ButtonPressed(mouse::Button::Left);
    const RIGHT: mouse::Event = mouse::Event::ButtonPressed(mouse::Button::Right);
    const UP: mouse::Event = mouse::Event::ButtonReleased(mouse::Button::Left);

    /// The clip of `row` over `frame`, by the rows themselves.
    fn clip_over(row: &RowView, frame: EditFrame) -> Option<ClipRef> {
        let index = row.clips.iter().position(|c| c.at <= frame && frame < c.at + c.duration)?;
        Some(ClipRef { track: row.track, index })
    }

    proptest! {
        /// A press is on the row drawn under it and the frame drawn under
        /// it, whatever the rows' heights, wherever the tracks are scrolled
        /// to and however wide a frame is: on a clip it names that clip (a
        /// left press selects it, a right one opens its take menu); off the
        /// clips a left press seeks there and a right one names the frame
        /// and the row's track to the timeline's menu. The spare layer is
        /// no track: a right press on it names none. The ruler and the line
        /// under the rows name a frame and no track, a track's name its
        /// track and no frame.
        #[test]
        fn a_press_is_on_the_row_and_frame_drawn_under_it(
            rows in rows(), stage in stage(), note in any::<bool>(),
            row in any::<prop::sample::Index>(), frame in 0i64..TOTAL as i64, within in 0.02f32..0.98,
        ) {
            let tracks = sending(rows.clone(), note);
            let now = std::time::Instant::now();
            let press = |event: mouse::Event, x: f32, y: f32| stage.send(&tracks, &mut Pointer::default(), event, stage.point(x, y), now);
            let r = row.index(rows.len());
            let (x, y) = (stage.x(frame), stage.y(&rows, r, within));
            let at = frame as usize;
            let track = (!rows[r].spare).then_some(rows[r].track);
            match clip_over(&rows[r], frame) {
                Some(clip) => {
                    prop_assert_eq!(press(RIGHT, x, y), Some(Sent::Menu(clip)));
                    prop_assert_eq!(press(LEFT, x, y), Some(Sent::Select(Some(clip), false, at)));
                }
                None => {
                    prop_assert_eq!(press(RIGHT, x, y), Some(Sent::Context(Some(at), track)));
                    // Before a clip of a track: the gap there, named by
                    // that clip. After the last, and on the spare layer:
                    // nothing is picked.
                    let next = rows[r].clips.iter().position(|c| c.at > frame).filter(|_| !rows[r].spare);
                    let expected = match next {
                        Some(index) => Sent::Gesture(Gesture::Gap(ClipRef { track: rows[r].track, index }, at)),
                        None => Sent::Select(None, false, at),
                    };
                    prop_assert_eq!(press(LEFT, x, y), Some(expected));
                }
            }
            // With Shift a press on a clip asks for the range to it; with
            // Ctrl (Cmd), and with both, it adds the clip or takes it out.
            if let Some(clip) = clip_over(&rows[r], frame) {
                for (shift, command) in [(true, false), (false, true), (true, true)] {
                    let mut state = Pointer::default();
                    let held = keyboard::Modifiers { shift, ..if command { keyboard::Modifiers::COMMAND } else { keyboard::Modifiers::NONE } };
                    tracks.update(&mut state, &Event::Keyboard(keyboard::Event::ModifiersChanged(held)), stage.bounds(&tracks), Cursor::Unavailable, now);
                    let sent = stage.send(&tracks, &mut state, LEFT, stage.point(x, y), now);
                    let expected = if command { Sent::Select(Some(clip), true, at) } else { Sent::Gesture(Gesture::Range(clip)) };
                    prop_assert_eq!(sent, Some(expected), "shift {} command {}", shift, command);
                }
            }
            // The ruler, the line under the rows, and a track's name.
            prop_assert_eq!(press(RIGHT, x, within * 20.0), Some(Sent::Context(Some(at), None)));
            prop_assert_eq!(press(LEFT, x, within * 20.0), Some(Sent::Seek(at)));
            if note {
                let under = stage.y(&rows, rows.len() - 1, 1.0) + within * 20.0;
                prop_assert_eq!(press(RIGHT, x, under), Some(Sent::Context(Some(at), None)));
            }
            prop_assert_eq!(press(RIGHT, within * 56.0, y), Some(Sent::Context(None, track)));
        }

        /// A press on empty track dragged away draws a box: once it has
        /// gone 4 pixels, every move sends the clips whose drawn rectangle
        /// the box from the press to the pointer touches (rows by their
        /// drawn heights, frames by their drawn width), and no other; a
        /// shorter drag sends nothing; letting go sends no pick.
        #[test]
        fn a_drag_from_empty_track_boxes_the_clips_it_touches(
            rows in rows(), stage in stage(), row in any::<prop::sample::Index>(), frame in 0i64..TOTAL as i64,
            to_row in any::<prop::sample::Index>(), to_frame in 0i64..TOTAL as i64, within in 0.05f32..0.95, to_within in 0.05f32..0.95,
        ) {
            let r = row.index(rows.len());
            prop_assume!(clip_over(&rows[r], frame).is_none());
            let tracks = sending(rows.clone(), false);
            let now = std::time::Instant::now();
            let mut state = Pointer::default();
            let from = (stage.x(frame), stage.y(&rows, r, within));
            stage.send(&tracks, &mut state, LEFT, stage.point(from.0, from.1), now);
            // Three pixels: not yet a box.
            let near = stage.point(from.0 + 3.0, from.1 + 3.0);
            prop_assert_eq!(stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: near }, near, now), None);
            let t = to_row.index(rows.len());
            let to = (stage.x(to_frame), stage.y(&rows, t, to_within));
            prop_assume!((to.0 - from.0).abs() >= 4.0 || (to.1 - from.1).abs() >= 4.0);
            let over = stage.point(to.0, to.1);
            let sent = stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: over }, over, now);
            let Some(Sent::Gesture(Gesture::Box(picked))) = sent else { panic!("no box: {sent:?}") };
            let (x0, x1) = (from.0.min(to.0), from.0.max(to.0));
            let mut expected = Vec::new();
            for (at_row, row) in rows.iter().enumerate().filter(|(at_row, _)| (r.min(t)..=r.max(t)).contains(at_row)) {
                for (index, c) in row.clips.iter().enumerate() {
                    // The clip's drawn left and right edges.
                    let (left, right) = (HEADER + stage.px * c.at as f32, HEADER + stage.px * (c.at + c.duration) as f32);
                    if left <= x1 && x0 < right {
                        expected.push(ClipRef { track: rows[at_row].track, index });
                    }
                }
            }
            prop_assert_eq!(picked, expected);
            prop_assert_eq!(stage.send(&tracks, &mut state, UP, over, now), Some(Sent::Drag));
            // The box is gone: a move sends nothing.
            prop_assert_eq!(stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: near }, near, now), None);
        }

        /// Scrolled, a press is still on the frame drawn under it: the
        /// frame as many frames after the first one shown as the press is
        /// frames' widths right of the names. A clip scrolled under the
        /// names' column is not there to press: a right press on a name
        /// names the track, never a clip.
        #[test]
        fn scrolled_a_press_is_on_the_frame_drawn_under_it(
            rows in rows(), stage in stage(), first in 0u32..300, row in any::<prop::sample::Index>(),
            across in 0i64..TOTAL as i64, within in 0.02f32..0.98,
        ) {
            let mut tracks = sending(rows.clone(), false);
            tracks.first = f64::from(first);
            let now = std::time::Instant::now();
            let press = |event: mouse::Event, x: f32, y: f32| stage.send(&tracks, &mut Pointer::default(), event, stage.point(x, y), now);
            let r = row.index(rows.len());
            let frame = i64::from(first) + across;
            let (x, y) = (stage.x(across), stage.y(&rows, r, within));
            let track = (!rows[r].spare).then_some(rows[r].track);
            match clip_over(&rows[r], frame) {
                Some(clip) => {
                    prop_assert_eq!(press(RIGHT, x, y), Some(Sent::Menu(clip)));
                    prop_assert_eq!(press(LEFT, x, y), Some(Sent::Select(Some(clip), false, frame as usize)));
                }
                None => prop_assert_eq!(press(RIGHT, x, y), Some(Sent::Context(Some(frame as usize), track))),
            }
            prop_assert_eq!(press(RIGHT, within * 56.0, y), Some(Sent::Context(None, track)));
            prop_assert_eq!(press(LEFT, x, within * 20.0), Some(Sent::Seek(frame as usize)));
        }

        /// Two presses on a track's name, soon enough, solo it; the spare
        /// layer has no track to solo, and one press never does.
        #[test]
        fn a_double_click_on_a_name_solos_a_track_never_the_spare(
            rows in rows(), stage in stage(), row in any::<prop::sample::Index>(), apart in 0u64..900,
        ) {
            let tracks = sending(rows.clone(), false);
            let r = row.index(rows.len());
            let at = stage.point(20.0, stage.y(&rows, r, 0.5));
            let start = std::time::Instant::now();
            let mut state = Pointer::default();
            prop_assert_eq!(stage.send(&tracks, &mut state, LEFT, at, start), None);
            let second = stage.send(&tracks, &mut state, LEFT, at, start + std::time::Duration::from_millis(apart));
            let solo = (apart <= 400 && !rows[r].spare).then_some(Sent::Solo(rows[r].track));
            prop_assert_eq!(second, solo);
        }

        /// A clip dragged by its body and let go over a row moves to that
        /// row's track when it is of the clip's kind (the spare layer is:
        /// the editor makes the track), else stays on its own; by the
        /// frames the pointer went, never before frame 0.
        #[test]
        fn a_clip_let_go_over_a_row_moves_to_its_track(
            rows in rows(), stage in stage(),
            from in any::<prop::sample::Index>(), to in any::<prop::sample::Index>(), dx in -120i64..120,
            markers in prop::collection::vec(0i64..200, 0..3), playhead in 0usize..200,
            selected in prop::collection::vec(any::<bool>(), 12), own_row in any::<bool>(),
        ) {
            let mut rows = rows;
            for (n, clip) in rows.iter_mut().flat_map(|row| &mut row.clips).enumerate() {
                clip.selected = selected[n % selected.len()];
            }
            let held: Vec<(usize, usize)> = rows.iter().enumerate().flat_map(|(r, row)| (0..row.clips.len()).map(move |i| (r, i))).collect();
            prop_assume!(!held.is_empty());
            let (r, index) = *from.get(&held);
            let target = if own_row { r } else { to.index(rows.len()) };
            let mut tracks = sending(rows.clone(), false);
            tracks.markers.clone_from(&markers);
            tracks.playhead = playhead;
            let c = &rows[r].clips[index];
            let clip = ClipRef { track: rows[r].track, index };
            let now = std::time::Instant::now();
            let middle = HEADER + stage.px * (c.at as f32 + c.duration as f32 / 2.0);
            let mut state = Pointer::default();
            let pressed = stage.send(&tracks, &mut state, LEFT, stage.point(middle, stage.y(&rows, r, 0.5)), now);
            prop_assert!(matches!(pressed, Some(Sent::Select(Some(c), false, _)) if c == clip), "{:?}", pressed);
            let over = stage.point(middle + stage.px * dx as f32, stage.y(&rows, target, 0.5));
            prop_assert_eq!(stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: over }, over, now), Some(Sent::Drag));
            let sent = stage.send(&tracks, &mut state, UP, over, now);
            let onto = if rows[target].track.kind == clip.track.kind { target } else { r };
            // Where it lands: snapped to the cut's start, the playhead, the
            // markers and the ends of every clip shown that stays, within
            // 8 pixels, then fitted among the clips that stay. A selected
            // clip let go on its own row takes the other selected clips
            // along, each on its own row; let go on another row it goes
            // alone.
            let together = onto == r && c.selected && rows.iter().flat_map(|row| &row.clips).filter(|c| c.selected).count() > 1;
            let moves = |at_row: usize, i: usize| (at_row, i) == (r, index) || (together && rows[at_row].clips[i].selected);
            let staying: Vec<Vec<(EditFrame, EditFrame)>> = rows
                .iter()
                .enumerate()
                .map(|(at_row, row)| {
                    let others = row.clips.iter().enumerate().filter(|(i, _)| !moves(at_row, *i));
                    others.map(|(_, other)| (other.at, other.at + other.duration)).collect()
                })
                .collect();
            let mut points: Vec<EditFrame> = staying.iter().flatten().flat_map(|&(at, end)| [at, end]).chain([0, playhead as EditFrame]).collect();
            points.extend(&markers);
            let mut moving = vec![Moving { row: onto, at: c.at, duration: c.duration }];
            for (at_row, i) in held.iter().copied().filter(|&(at_row, i)| (at_row, i) != (r, index) && moves(at_row, i)) {
                let other = &rows[at_row].clips[i];
                moving.push(Moving { row: at_row, at: other.at, duration: other.duration });
            }
            prop_assert_eq!(moving.len() > 1, together);
            // Not dragged along, it snaps to nothing: a click moves no clip.
            let reach = if dx == 0 { 0 } else { (8.0 / stage.px).round() as EditFrame };
            for m in &moving {
                let landed = landing::land(&moving, &staying, &points, reach, dx);
                let (at, end) = (m.at + landed.delta, m.at + m.duration + landed.delta);
                prop_assert!(at >= 0 && staying[m.row].iter().all(|&(a, e)| end <= a || e <= at), "a moving clip lands on one that stays");
            }
            if dx == 0 && onto == r {
                prop_assert_eq!(&sent, &None, "a click moved a clip");
            }
            let landed = landing::land(&moving, &staying, &points, reach, dx);
            let (at, end) = (c.at + landed.delta, c.at + c.duration + landed.delta);
            prop_assert!(at >= 0 && staying[onto].iter().all(|&(a, e)| end <= a || e <= at), "it lands on a clip: {:?}", landed);
            let expected =
                (landed.delta != 0 || onto != r).then(|| Sent::Op(Op::Move { clip, to: rows[onto].track, at }));
            prop_assert_eq!(sent, expected);
        }
    }

    /// How the rows change while a clip is held: a key or an agent's call
    /// edits the cut under the pointer.
    #[derive(Debug, Clone)]
    enum Under {
        /// Nothing changes.
        Nothing,
        /// A clip of the held clip's row goes (a Delete, an undo).
        Gone(prop::sample::Index),
        /// A row appears on top (the spare layer, after an edit).
        RowOnTop,
        /// The held clip's row goes (the audio is hidden, a track soloed).
        RowGone,
        /// Another cut altogether.
        Other(Vec<RowView>),
    }

    fn under() -> impl Strategy<Value = Under> {
        prop_oneof![
            Just(Under::Nothing),
            any::<prop::sample::Index>().prop_map(Under::Gone),
            Just(Under::RowOnTop),
            Just(Under::RowGone),
            rows().prop_map(Under::Other),
        ]
    }

    proptest! {
        /// The cut can change while a clip is held (a key pressed mid-drag,
        /// an undo, an agent's edit): the drag then goes on only if the
        /// clip it took is still where it was on its track, and is dropped
        /// otherwise. It is never painted from, and never let go on, a
        /// clip that is no longer there or another clip that took its
        /// place. (Up to 1.112.0 the held clip was a row and an index: a
        /// Delete while dragging the last clip of a track stopped the
        /// editor, and with a clip after it the drag ended on that one.)
        #[test]
        fn a_cut_changed_under_a_held_clip_drops_the_drag(
            rows in rows(), stage in stage(), from in any::<prop::sample::Index>(), edge in 0usize..3,
            under in under(), dx in -60i64..60, to in any::<prop::sample::Index>(),
        ) {
            let held: Vec<(usize, usize)> = rows.iter().enumerate().flat_map(|(r, row)| (0..row.clips.len()).map(move |i| (r, i))).collect();
            prop_assume!(!held.is_empty());
            let (r, index) = *from.get(&held);
            let c = rows[r].clips[index].clone();
            let clip = ClipRef { track: rows[r].track, index };
            let x = match edge {
                0 => HEADER + stage.px * c.at as f32 + 0.5,
                1 => HEADER + stage.px * (c.at + c.duration) as f32 - 1.0,
                _ => HEADER + stage.px * (c.at as f32 + c.duration as f32 / 2.0),
            };
            let now = std::time::Instant::now();
            let mut state = Pointer::default();
            let before = sending(rows.clone(), false);
            let pressed = stage.send(&before, &mut state, LEFT, stage.point(x, stage.y(&rows, r, 0.5)), now);
            prop_assert!(matches!(pressed, Some(Sent::Select(Some(c), false, _)) if c == clip), "{:?}", pressed);

            let mut after = rows.clone();
            match under {
                Under::Nothing => {}
                Under::Gone(which) => {
                    let gone = which.index(after[r].clips.len());
                    after[r].clips.remove(gone);
                }
                Under::RowOnTop => after.insert(0, RowView { spare: true, ..row_of(TrackRef::video(9), &Vec::new(), 46) }),
                Under::RowGone => {
                    after.remove(r);
                }
                Under::Other(other) => after = other,
            }
            // The clip it took, where it was: still on its track, at its
            // index, over the same frames.
            let still = after.iter().any(|row| {
                !row.spare && row.track == clip.track && row.clips.get(index).is_some_and(|now| (now.at, now.duration) == (c.at, c.duration))
            });
            let tracks = sending(after.clone(), false);
            let over = stage.point(x + stage.px * dx as f32, 20.0 + 5.0 + to.index(240) as f32);
            let _ = stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: over }, over, now);
            tracks.paint(&state, &mut Geometry::default(), HEADER + stage.px * TOTAL as f32, tracks.height());
            let _ = tracks.mouse_interaction(&state, stage.bounds(&tracks), Cursor::Available(over));
            let sent = stage.send(&tracks, &mut state, UP, over, now);
            match sent {
                None => {}
                Some(Sent::Op(op)) => {
                    prop_assert!(still, "the held clip is gone, yet the drag made {:?}", op);
                    let named = match &op {
                        Op::Move { clip, .. } | Op::Trim { clip, .. } | Op::Slip { clip, .. } | Op::SetFades { clip, .. } => *clip,
                        other => return Err(TestCaseError::fail(format!("no drag of these makes {other:?}"))),
                    };
                    prop_assert_eq!(named, clip);
                }
                Some(other) => return Err(TestCaseError::fail(format!("a release sent {other:?}"))),
            }
            // Either way the clip is let go: the next move drags nothing.
            prop_assert_eq!(stage.send(&tracks, &mut state, mouse::Event::CursorMoved { position: over }, over, now), None);
        }
    }

    #[test]
    fn hatching_lines_are_a_gap_apart() {
        // On a line, fully covered; halfway between two, bare.
        assert!((hatch_coverage(HATCH, 0.0) - 1.0).abs() < 1e-6);
        assert_eq!(hatch_coverage(HATCH / 2.0, 0.0), 0.0);
        const { assert!(HATCH_TILE_WIDE % HATCH == 0.0 && HATCH_TILE_TALL % HATCH == 0.0) };
    }
}
