//! The review marks over the picture, and the brush that draws them
//! (`review.rs` is the model).
//!
//! [`Surface`] lies over the video in the viewer's stack. It draws the
//! strokes of the frame on screen, placed by its [`Geometry`] so they sit
//! on the take's picture however the clip is placed or the view flipped,
//! and while drawing is on it takes the brush: a press starts a stroke, the
//! pointer adds to it and the release hands it to the editor in the take's
//! frame's terms. The eraser hands over each stroke it touches as it goes.
//! A pen's pressure comes as Primer's `mouse::Event::Pressure` just before
//! the move or press it is of; a mouse sends none, and draws at full width.
//!
//! The renderer paints a band by kind, meshes before shaders, so lines
//! drawn in the video's band would be under the picture: the surface
//! paints a band higher ([`RenderNode::layer`]).

use primer::prelude::*;
use primer::widget::custom::{Ctx, Cursor, Event, Layout, LayoutCtx, Limits, Node, RenderNode, Shell, Sizing, Status, Widget, mouse};
use th_pipe::review::{self as files, Stroke as Marked};

use crate::review::{self, Brush, Geometry, Tool};

/// What the surface sends the editor.
pub struct Wiring<Message> {
    /// A stroke was begun (the editor stops playing).
    pub begin: fn() -> Message,
    /// A stroke is done: its points in the take's frame (0 to 1, with
    /// pressures).
    pub stroke: fn(Vec<files::Point>) -> Message,
    /// The eraser touched these marks (by file name).
    pub erase: fn(Vec<String>) -> Message,
}

/// The marks of the frame on screen, and the brush while drawing is on.
pub struct Surface<Message> {
    id: Id,
    /// Each stroke of the frame and its mark's file name, in the order made.
    strokes: Vec<(String, Marked)>,
    /// Where the take's picture is; None while nothing is on screen (and
    /// then nothing is drawn or taken).
    geometry: Option<Geometry>,
    brush: Brush,
    /// The picture takes the brush.
    drawing: bool,
    wiring: Wiring<Message>,
}

/// `strokes` over the picture placed by `geometry`; with `drawing`, the
/// brush's strokes taken. Its pointer state lives under `id`.
pub fn surface<Message: 'static>(
    id: Id,
    strokes: Vec<(String, Marked)>,
    geometry: Option<Geometry>,
    brush: Brush,
    drawing: bool,
    wiring: Wiring<Message>,
) -> Surface<Message> {
    Surface { id, strokes, geometry, brush, drawing, wiring }
}

/// The pointer's part in a stroke, kept between events.
#[derive(Debug, Default)]
struct Pointer {
    /// The stroke being drawn: screen points (local to the surface) and
    /// each one's pressure.
    drawn: Option<Vec<(Point, f32)>>,
    /// The eraser is held down.
    erasing: bool,
    /// The pen's last pressure, until it lifts.
    pressure: Option<f32>,
    /// Marks the eraser has handed over this press.
    erased: Vec<String>,
    /// The pen's eraser end is down (Primer's `mouse::Event::Eraser`, just
    /// before its press): this stroke erases whatever the brush's tool.
    flipped: bool,
    /// Where the eraser was last seen this press.
    erased_at: Option<Point>,
}

impl<Message> Surface<Message> {
    /// The marks the eraser moving from `from` to `p` touches that it has
    /// not handed over.
    fn touched(&self, geometry: &Geometry, size: Size, from: Point, p: Point, erased: &[String]) -> Vec<String> {
        self.strokes
            .iter()
            .filter(|(name, stroke)| !erased.contains(name) && review::swept(&review::on_screen(stroke, geometry, size), from, p))
            .map(|(name, _)| name.clone())
            .collect()
    }

    fn line(frame: &mut Frame, points: &[(Point, f32)], color: Color) {
        for (run, width) in review::runs(points) {
            if run.len() == 1 || run.windows(2).all(|w| w[0] == w[1]) {
                frame.fill(&Path::circle(run[0], width / 2.0), color);
                continue;
            }
            let path = Path::new(|b| {
                b.move_to(run[0]);
                for &p in &run[1..] {
                    b.line_to(p);
                }
            });
            frame.stroke(&path, Stroke::new(width, color));
        }
    }

    fn paint(&self, state: &Pointer, frame: &mut Frame, size: Size, cursor: Option<Point>) {
        let Some(geometry) = &self.geometry else { return };
        for (_, stroke) in &self.strokes {
            let [r, g, b] = stroke.color.0;
            Self::line(frame, &review::on_screen(stroke, geometry, size), Color::from_rgb8(r, g, b));
        }
        let tall = geometry.height_on_screen(size);
        if let Some(drawn) = &state.drawn {
            let [r, g, b] = self.brush.rgb().0;
            let points: Vec<(Point, f32)> =
                drawn.iter().map(|&(p, pressure)| (p, (files::width_at(self.brush.share(), pressure) * tall).max(0.5))).collect();
            Self::line(frame, &points, Color::from_rgb8(r, g, b));
        }
        // Where the brush is, and how big: a ring at its full width (the
        // eraser's at its reach).
        if self.drawing
            && let Some(at) = cursor
        {
            let radius = match self.brush.tool {
                Tool::Pen => (self.brush.share() * tall / 2.0).max(1.5),
                Tool::Eraser => review::ERASER_REACH.max(4.0),
            };
            let ring = Path::circle(at, radius);
            frame.stroke(&ring, Stroke::new(2.5, Color::from_rgba8(0, 0, 0, 160)));
            frame.stroke(&ring, Stroke::new(1.0, Color::from_rgba8(255, 255, 255, 230)));
        }
    }
}

impl<Message> Widget<Message> for Surface<Message> {
    fn sizing(&self) -> Sizing {
        Sizing { width: Length::Fill, height: Length::Fill }
    }

    fn layout(&self, limits: &Limits, _ctx: LayoutCtx<'_>) -> Node {
        Node::new(limits.resolve(self.sizing(), Size::ZERO))
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        // Read every redraw, drawing or not: the store forgets a state a
        // redraw did not read (`primer-state-prune-trap`).
        let idle = Pointer::default();
        let state = ctx.store.state::<Pointer>(&self.id).unwrap_or(&idle);
        let mut frame = Frame::new(bounds.size());
        self.paint(state, &mut frame, bounds.size(), ctx.cursor.position_relative_to(bounds));
        let mut mesh = frame.into_mesh();
        if mesh.is_empty() {
            return RenderNode::group(bounds, Vec::new());
        }
        for vertex in &mut mesh.vertices {
            vertex.position = [vertex.position[0] + bounds.x, vertex.position[1] + bounds.y];
        }
        mesh.bounds = mesh.bounds.translate(Vector::new(bounds.x, bounds.y));
        RenderNode::clip(bounds, RenderNode::layer(RenderNode::group(bounds, vec![RenderNode::mesh(mesh)])))
    }

    fn on_event(&mut self, event: &Event, layout: Layout<'_>, shell: &mut Shell<'_, Message>) -> Status {
        let bounds = layout.bounds();
        let cursor: Cursor = shell.cursor();
        let (Some(geometry), true) = (self.geometry, self.drawing) else {
            // Not drawing: the picture's own (its menu) and nothing kept.
            shell.store_mut().state_mut::<Pointer>(&self.id).drawn = None;
            return Status::Ignored;
        };
        let size = bounds.size();
        let at = cursor.position_relative_to(bounds);
        let state = shell.store_mut().state_mut::<Pointer>(&self.id);
        match event {
            Event::Mouse(mouse::Event::Pressure(p)) => {
                state.pressure = Some(*p);
                Status::Ignored
            }
            Event::Mouse(mouse::Event::Eraser) => {
                state.flipped = true;
                Status::Ignored
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(at) = at else { return Status::Ignored };
                let pressure = self.brush.pressure_of(state.pressure);
                let tool = if state.flipped { Tool::Eraser } else { self.brush.tool };
                match tool {
                    Tool::Pen => state.drawn = Some(vec![(at, pressure)]),
                    Tool::Eraser => {
                        state.erasing = true;
                        state.erased.clear();
                        state.erased_at = Some(at);
                    }
                }
                let touched = if tool == Tool::Eraser { self.touched(&geometry, size, at, at, &[]) } else { Vec::new() };
                let state = shell.store_mut().state_mut::<Pointer>(&self.id);
                state.erased.extend(touched.iter().cloned());
                shell.publish((self.wiring.begin)());
                if !touched.is_empty() {
                    shell.publish((self.wiring.erase)(touched));
                }
                // As the tracks do: a press taken focuses the surface, so
                // the keys reach `on_key` again.
                if !shell.store_mut().is_focused(&self.id) {
                    shell.store_mut().set_focus(self.id);
                }
                shell.request_redraw();
                Status::Captured
            }
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let Some(at) =
                    cursor.position_relative_to(bounds).or_else(|| cursor.position().map(|p| Point::new(p.x - bounds.x, p.y - bounds.y)))
                else {
                    return Status::Ignored;
                };
                if let Some(drawn) = &mut state.drawn {
                    let pressure = self.brush.pressure_of(state.pressure);
                    let far = drawn
                        .last()
                        .is_none_or(|(last, _)| ((at.x - last.x).powi(2) + (at.y - last.y).powi(2)).sqrt() >= review::POINT_SPACING);
                    if far {
                        drawn.push((at, pressure));
                    } else if let Some(last) = drawn.last_mut() {
                        last.1 = last.1.max(pressure);
                    }
                    shell.request_redraw();
                    return Status::Captured;
                }
                if state.erasing {
                    let erased = state.erased.clone();
                    let from = state.erased_at.replace(at).unwrap_or(at);
                    let touched = self.touched(&geometry, size, from, at, &erased);
                    if !touched.is_empty() {
                        shell.store_mut().state_mut::<Pointer>(&self.id).erased.extend(touched.iter().cloned());
                        shell.publish((self.wiring.erase)(touched));
                    }
                    shell.request_redraw();
                    return Status::Captured;
                }
                // The brush's ring follows the pointer.
                shell.request_redraw();
                Status::Ignored
            }
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                state.pressure = None;
                state.erasing = false;
                state.flipped = false;
                let Some(drawn) = state.drawn.take() else { return Status::Ignored };
                let points: Vec<files::Point> = drawn
                    .into_iter()
                    .map(|(p, pressure)| {
                        let (x, y) = geometry.to_source(size, p);
                        files::Point { x, y, pressure }
                    })
                    .filter(|p| p.x.is_finite() && p.y.is_finite())
                    .collect();
                if !points.is_empty() {
                    shell.publish((self.wiring.stroke)(points));
                }
                shell.request_redraw();
                Status::Captured
            }
            _ => Status::Ignored,
        }
    }

    fn hover(&self, layout: Layout<'_>, _ctx: LayoutCtx<'_>, regions: &mut mouse::HoverRegions) {
        // While drawing, the brush's ring follows the pointer.
        if self.drawing {
            regions.motion(layout.bounds());
        }
    }
}

impl<Message: 'static> From<Surface<Message>> for Element<Message> {
    fn from(surface: Surface<Message>) -> Self {
        Element::new(surface)
    }
}
