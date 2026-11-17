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
    /// Clips can be selected, dragged and right-clicked; else every press
    /// scrubs.
    pub editable: bool,
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
        let mut x = r.x;
        while x < r.x + r.width {
            tiles.push(RenderNode::image(Rectangle::new(Point::new(x, r.y), Size::new(HATCH_TILE_WIDE, ROW)), tile.clone()));
            x += HATCH_TILE_WIDE;
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
/// gaps wide (so tiles side by side continue the lines), a row tall.
const HATCH_TILE_WIDE: f32 = HATCH * 64.0;
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
    let (wide, tall) = ((HATCH_TILE_WIDE * HATCH_TILE_SCALE) as u32, (ROW * HATCH_TILE_SCALE) as u32);
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
        let state = shell.store_mut().state_mut::<Pointer>(&self.id);
        let (status, message) = self.tracks.update(state, event, bounds, cursor);
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
            for f in (0..self.total).step_by(step) {
                let tall = if f % (step * 5) == 0 { 8.0 } else { 4.0 };
                p.rect(Point::new(self.x(f as EditFrame, w), RULER - STRIP - tall), Size::new(1.0, tall), look::tick());
            }
        }
        for &(start, end) in &self.buffered {
            let (x0, x1) = (self.x(start as EditFrame, w), self.x(end as EditFrame, w));
            p.rect(Point::new(x0, RULER - STRIP), Size::new((x1 - x0).max(1.0), STRIP), look::buffered());
        }

        for (r, row) in self.rows.iter().enumerate() {
            let top = RULER + r as f32 * ROW;
            let band = if row.track.kind == TrackKind::Video { look::band() } else { look::band_audio() };
            p.rect(Point::new(0.0, top), Size::new(HEADER - 1.0, ROW - 1.0), look::panel());
            p.rect(Point::new(HEADER, top), Size::new(w - HEADER, ROW - 1.0), band);
            let label = Rectangle { x: 10.0, y: top, width: HEADER - 14.0, height: ROW };
            p.text(row.name.clone(), label, H::Left, V::Center, 13.0, look::quiet());
            for c in &row.clips {
                let rect = self.clip_rect(c, top, w);
                let body = match (row.track.kind, c.missing) {
                    (_, true) => look::missing(),
                    (TrackKind::Video, false) => c.department.as_deref().map_or_else(look::clip, look::department),
                    (TrackKind::Audio, false) => look::audio(),
                };
                p.rect(Point::new(rect.x, rect.y), Size::new(rect.width, rect.height), body);
                if c.missing {
                    p.hatch(rect, look::hatch());
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
                    p.rect(Point::new(tag.x, tag.y), Size::new(tag.width, tag.height), look::now());
                    p.text(words.to_owned(), tag, H::Center, V::Center, 10.0, look::surround());
                    right = tag.x - INSET;
                }
                let room = right - left - INSET;
                if room >= 10.0 {
                    let line = |y: f32, tall: f32| Rectangle { x: left, y: rect.y + y, width: room, height: tall };
                    p.text(c.name.clone(), line(FADE_BAND, 15.0), H::Left, V::Top, 12.0, look::text());
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

        p.raise();
        let marker = look::mark();
        for &m in &self.markers {
            let x = self.x(m, w) + slot / 2.0;
            let tip = Path::new(|b| {
                b.move_to(Point::new(x - 5.0, 0.0));
                b.line_to(Point::new(x + 5.0, 0.0));
                b.line_to(Point::new(x, 8.0));
                b.close();
            });
            p.fill(&tip, marker);
        }
        for (r, row) in self.rows.iter().enumerate() {
            let top = RULER + r as f32 * ROW;
            for c in &row.clips {
                let rect = self.clip_rect(c, top, w);
                // Fades: a ramp from the clip's corner over its top band.
                let ramp = Color::from_rgba8(240, 240, 240, 140);
                if c.fade_in > 0 {
                    let xe = self.x(c.at + c.fade_in, w);
                    let tri = Path::new(|b| {
                        b.move_to(Point::new(rect.x, rect.y + rect.height));
                        b.line_to(Point::new(xe, rect.y));
                        b.line_to(Point::new(rect.x, rect.y));
                        b.close();
                    });
                    p.fill(&tri, Color::from_rgba8(0, 0, 0, 90));
                    p.stroke(&Path::line(Point::new(rect.x, rect.y + rect.height), Point::new(xe, rect.y)), Stroke::new(1.0, ramp));
                }
                if c.fade_out > 0 {
                    let xs = self.x(c.at + c.duration - c.fade_out, w);
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
            && let Some(ghost) = self.ghost(grab, w, state.at.0, state.at.1)
        {
            outline(p, ghost, 2.0, look::now());
            let dx = self.frames_dragged(grab, state.at.0, w);
            let words = match grab.part {
                Part::Slip => format!("slip {:+}", -dx),
                Part::FadeIn | Part::FadeOut => format!("fade {:+}", if grab.part == Part::FadeIn { dx } else { -dx }),
                _ => format!("{dx:+}"),
            };
            let tag = Rectangle { x: ghost.x + 2.0, y: ghost.y + ghost.height - 15.0, width: 120.0, height: 14.0 };
            p.text(words, tag, H::Left, V::Center, 11.0, look::now());
        }

        let centre = self.x(self.playhead as EditFrame, w) + slot / 2.0;
        let accent = look::now();
        p.rect(Point::new(centre - 1.0, 0.0), Size::new(2.0, h), accent);
        p.rect(Point::new(centre - 4.0, 0.0), Size::new(8.0, 3.0), accent);
    }

    /// Where clip `c` of the row at `top` is drawn.
    fn clip_rect(&self, c: &ClipView, top: f32, w: f32) -> Rectangle {
        let (x0, x1) = (self.x(c.at, w), self.x(c.at + c.duration, w));
        Rectangle { x: x0, y: top + 1.0, width: (x1 - x0 - 1.0).max(1.0), height: ROW - 3.0 }
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
                if local.y < RULER || !self.editable {
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
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right)) if !self.editable => (Status::Ignored, None),
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

    fn tracks_of(rows: Vec<RowView>, markers: Vec<EditFrame>, total: usize) -> Tracks<()> {
        Tracks {
            rows,
            markers,
            total,
            playhead: 0,
            buffered: vec![(0, total / 2)],
            on_seek: |_| (),
            on_select: |_, _, _| (),
            on_drag: || (),
            on_op: |_| (),
            on_menu: |_| (),
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
            let row = RowView { track: TrackRef::video(0), name: "V1".into(), clips: views };
            let any_clip = !row.clips.is_empty();
            let tracks = tracks_of(vec![row], markers.clone(), 600);
            let mut state = Pointer::default();
            if grabbed && any_clip {
                state.grab = Some(Grab { row: 0, index: 0, part: Part::Body, from_x: 100.0 });
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
            prop_assert!((hatch_coverage(x + 1.0, y - 1.0) - here).abs() < 1e-3);
        }
    }

    #[test]
    fn hatching_lines_are_a_gap_apart() {
        // On a line, fully covered; halfway between two, bare.
        assert!((hatch_coverage(HATCH, 0.0) - 1.0).abs() < 1e-6);
        assert_eq!(hatch_coverage(HATCH / 2.0, 0.0), 0.0);
        const { assert!(HATCH_TILE_WIDE % HATCH == 0.0) };
    }
}
