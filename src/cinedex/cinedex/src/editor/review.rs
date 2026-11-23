//! Reviewing in the editor: drawing on the frame shown and writing notes
//! on it, for everyone with access to the project (`crate::review` is the
//! model, `crate::review_view` the picture's surface).
//!
//! Review mode (D, View > Review Mode) opens the review panel at the
//! viewer's right: the pen and the eraser (E), the colours, the brush's
//! size, whether a pen's pressure sets the width, Undo, Redo, Clear and
//! Done, then the frame's comments as a conversation, oldest first, each
//! with who wrote it and when, to change or delete, and the field to add
//! one. While it is on, the picture takes the brush. The marks of the frame
//! shown are drawn over the picture whether or not it is on (View > Show
//! Review Marks hides them).
//!
//! A mark belongs to the take the clip on top plays and to its own frame
//! of it, not to the cut: every edit showing that take shows it, and a
//! trim or a move leaves it on its frame. The frames of the cut with marks
//! show on the ruler; Shift+Left and Shift+Right go to the one before and
//! after the playhead.
//!
//! Undo and Redo (Ctrl+Z, Ctrl+Shift+Z) undo the review marks made here
//! while review mode is on, the edit's changes while it is off.

use std::path::PathBuf;
use std::time::Duration;

use primer::prelude::*;
use th_media::{FrameKey, Media};
use th_pipe::review::{self as files, Body};

use super::{Editor, Message, source_name};
use crate::look;
use crate::review::{self as model, COLOURS, Draft, Geometry, Tool};
use crate::review_view::{self, Wiring};

/// How long the slow sweep waits between reads of each of the cut's
/// other takes' folders.
const SWEEP_EVERY: Duration = Duration::from_secs(30);
/// How wide the review panel is, at the viewer's right.
pub(super) const PANEL_WIDTH: f32 = 300.0;

/// A section's heading in the panel.
fn heading(words: impl Into<String>) -> Text {
    text(words).font_size(11.0).color(look::quiet()).width(Length::Fill)
}

/// The comments among `marks`, oldest first (then by name, so two made the
/// same millisecond keep one order everywhere).
pub(super) fn conversation<'a>(marks: impl Iterator<Item = &'a files::Mark>) -> Vec<files::Mark> {
    let mut notes: Vec<files::Mark> = marks.filter(|m| matches!(m.body, Body::Note(_))).cloned().collect();
    notes.sort_by_key(|m| (m.created, m.file_name()));
    notes
}

#[derive(Debug, Clone)]
pub enum ReviewMessage {
    /// Review mode on or off (D).
    Mode(bool),
    /// The marks drawn over the picture, or not.
    Shown(bool),
    Tool(Tool),
    /// A colour of [`COLOURS`] (and the pen to draw with it).
    Colour(usize),
    Size(f32),
    Pressure(bool),
    /// A stroke or an erase begun on the picture.
    Begin,
    Stroke(Vec<files::Point>),
    /// The eraser touched these marks (by file name).
    Erase(Vec<String>),
    /// Take every stroke off the frame shown.
    Clear,
    Undo,
    Redo,
    /// The note field's text.
    Draft(String),
    /// Add the note, or save the change to the one being changed.
    Post,
    /// Change a note of the frame shown (by file name).
    EditNote(String),
    DeleteNote(String),
    /// Stop changing a note.
    CancelNote,
    /// To the frame with marks before (negative) or after the playhead.
    Jump(i64),
    /// Read the folders for other machines' changes.
    Poll,
}

/// Where a mark made now goes: the folder of the take on top of the
/// picture shown, its frame there, and where its picture is on screen.
#[derive(Debug, Clone)]
pub(super) struct Target {
    pub dir: PathBuf,
    pub frame: i64,
    pub geometry: Geometry,
    /// The shot (or item) and take, as the strip names them.
    pub label: String,
}

/// The index of `media` that shows frame number `frame`; None where it has
/// none. A stepped sequence numbers its frames `first + index * step`.
pub(super) fn index_of(media: &Media, frame: i64) -> Option<usize> {
    let first = media.frame_number(0)?;
    if let Some(i) = media.index_at(frame, first)
        && media.frame_number(i) == Some(frame)
    {
        return Some(i);
    }
    let step = media.frame_number(1).map_or(1, |n| (n - first).max(1));
    let at = frame.checked_sub(first)?;
    if at % step != 0 {
        return None;
    }
    let i = usize::try_from(at / step).ok()?;
    (i < media.frame_count() && media.frame_number(i) == Some(frame)).then_some(i)
}

fn send(message: ReviewMessage) -> Message {
    Message::Review(message)
}

impl Editor {
    /// Where a mark made now goes; None while the picture shown is no
    /// take's (a slug, nothing, a live playblast).
    pub(super) fn review_target(&self) -> Option<Target> {
        let shown = self.shown.as_ref()?;
        let &(key, _, transform) = shown.layers.last()?;
        let entry = self.entry_of_engine(key.source)?;
        let resolved = entry.resolved()?;
        let dir = resolved.review.clone()?;
        let media = resolved.media.as_ref()?;
        let frame = media.frame_number(key.index).unwrap_or(resolved.first + key.index as i64);
        let picture = (shown.picture.frame.width, shown.picture.frame.height);
        let source = self.engine.frame(key).map_or(picture, |f| (f.width, f.height));
        let geometry = Geometry { picture, source, placement: crate::placing::placement(transform), flipped: self.flipped };
        let label = format!("{}  {}", source_name(&entry.source), resolved.take_label());
        Some(Target { dir, frame, geometry, label })
    }

    /// The review folders of the cut's takes.
    pub(super) fn review_dirs(&self) -> Vec<PathBuf> {
        let mut dirs: Vec<PathBuf> = self.entries.iter().filter_map(|e| e.resolved()?.review.clone()).collect();
        dirs.sort();
        dirs.dedup();
        dirs
    }

    /// The timeline positions whose picture (the top clip there, the one
    /// marks are drawn on) is a frame with marks, in order. A marked frame
    /// under a clip on a higher track is not on screen, so has no key.
    pub(super) fn review_positions(&self) -> Vec<usize> {
        let mut out = Vec::new();
        for (i, entry) in self.entries.iter().enumerate() {
            let Some(resolved) = entry.resolved() else { continue };
            let (Some(dir), Some(media)) = (&resolved.review, &resolved.media) else { continue };
            let Some(folder) = self.review.folder(dir) else { continue };
            for frame in folder.frames() {
                if let Some(index) = index_of(media, frame) {
                    let key = FrameKey { source: i + 1, index };
                    out.extend(self.timeline.positions(key).filter(|&p| self.engine.layers(p).last().is_some_and(|l| l.key == key)));
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }

    /// Take in what the review files' thread did; a failure is said.
    pub(super) fn review_collect(&mut self) {
        let (_, failed) = self.review.collect();
        if let Some(first) = failed.into_iter().next() {
            self.status = Some((first, true));
        }
    }

    /// Read every review folder of the cut again (Refresh).
    pub(super) fn review_refresh(&mut self) {
        for dir in self.review_dirs() {
            self.review.poll(&dir, Duration::ZERO);
        }
    }

    /// Say why nothing can be reviewed at the playhead.
    fn no_target(&mut self) {
        self.status =
            Some(("Nothing to review here: marks go on a shot's take or a media item, not a slug, a gap or a live playblast".into(), true));
    }

    pub(super) fn review_handle(&mut self, message: ReviewMessage) {
        match message {
            ReviewMessage::Mode(on) => {
                self.review.drawing = on;
                if on {
                    self.review.shown = true;
                    if self.review_target().is_none() {
                        self.no_target();
                    }
                } else {
                    self.review.draft = Draft::default();
                }
            }
            ReviewMessage::Shown(on) => self.review.shown = on,
            ReviewMessage::Tool(tool) => {
                self.review.brush.tool = tool;
                self.review.drawing = true;
                self.review.shown = true;
            }
            ReviewMessage::Colour(colour) => {
                self.review.brush.colour = colour.min(COLOURS.len() - 1);
                self.review.brush.tool = Tool::Pen;
            }
            ReviewMessage::Size(size) => self.review.brush.size = size.clamp(model::SIZE_MIN, model::SIZE_MAX),
            ReviewMessage::Pressure(on) => self.review.brush.pressure = on,
            ReviewMessage::Begin => {
                self.set_playing(false);
                self.review.joining = false;
            }
            ReviewMessage::Stroke(points) => {
                let Some(target) = self.review_target() else { return self.no_target() };
                let step = self.review.stroke_step(&target.dir, target.frame, points);
                self.review.apply(step);
                self.status = None;
            }
            ReviewMessage::Erase(names) => {
                let Some(target) = self.review_target() else { return self.no_target() };
                let step = self.review.remove_step(&target.dir, &names);
                // One drag of the eraser is one undo step.
                if self.review.joining {
                    self.review.join(step);
                } else {
                    self.review.apply(step);
                    self.review.joining = true;
                }
            }
            ReviewMessage::Clear => {
                let Some(target) = self.review_target() else { return self.no_target() };
                let step = self.review.clear_step(&target.dir, target.frame);
                if step.is_empty() {
                    self.status = Some(("This frame has no drawings".into(), false));
                }
                self.review.apply(step);
            }
            ReviewMessage::Undo => {
                if !self.review.undo() {
                    self.status = Some(("No review mark of this session to undo".into(), false));
                }
            }
            ReviewMessage::Redo => {
                if !self.review.redo() {
                    self.status = Some(("No review mark to redo".into(), false));
                }
            }
            ReviewMessage::Draft(text) => self.review.draft.text = text,
            ReviewMessage::Post => {
                let editing = self.review.draft.editing.clone();
                let step = match (editing, self.review_target()) {
                    (Some((dir, _)), _) => self.review.note_step(&dir, 0),
                    (None, Some(target)) => self.review.note_step(&target.dir, target.frame),
                    (None, None) => return self.no_target(),
                };
                self.review.apply(step);
                self.review.draft = Draft::default();
                self.status = None;
            }
            ReviewMessage::EditNote(name) => {
                let Some(target) = self.review_target() else { return self.no_target() };
                let Some(note) = self.review.folder(&target.dir).and_then(|f| f.get(&name)).and_then(|m| m.note()) else { return };
                self.review.draft = Draft { text: note.text.clone(), editing: Some((target.dir, name)) };
                self.review.drawing = true;
            }
            ReviewMessage::DeleteNote(name) => {
                let Some(target) = self.review_target() else { return self.no_target() };
                let step = self.review.remove_step(&target.dir, std::slice::from_ref(&name));
                self.review.apply(step);
                if self.review.draft.editing.as_ref().is_some_and(|(_, n)| *n == name) {
                    self.review.draft = Draft::default();
                }
            }
            ReviewMessage::CancelNote => self.review.draft = Draft::default(),
            ReviewMessage::Jump(direction) => {
                let positions = self.review_positions();
                let here = self.position;
                let to = if direction < 0 { positions.iter().rev().find(|&&p| p < here) } else { positions.iter().find(|&&p| p > here) };
                match to {
                    Some(&to) => {
                        self.set_playing(false);
                        self.move_to(to);
                        self.leave_loop_outside();
                        self.status = None;
                    }
                    None => {
                        let word = if direction < 0 { "before" } else { "after" };
                        self.status = Some((format!("No frame with review marks {word} the playhead"), false));
                    }
                }
            }
            ReviewMessage::Poll => {
                let dirs = self.review_dirs();
                self.review.want(dirs.iter().cloned());
                if let Some(target) = self.review_target() {
                    self.review.poll(&target.dir, model::POLL_EVERY);
                }
                self.review.poll_turn(&dirs, SWEEP_EVERY);
            }
        }
    }

    /// The marks of the frame shown over the picture, taking the brush in
    /// review mode; None while they are hidden and review mode is off.
    pub(super) fn review_surface(&self) -> Option<Element<Message>> {
        if !self.review.shown && !self.review.drawing {
            return None;
        }
        let target = self.review_target();
        let strokes = match (&target, self.review.shown) {
            (Some(t), true) => self
                .review
                .folder(&t.dir)
                .map(|f| f.on(t.frame).filter_map(|m| Some((m.file_name(), m.stroke()?.clone()))).collect())
                .unwrap_or_default(),
            _ => Vec::new(),
        };
        let wiring = Wiring {
            begin: || send(ReviewMessage::Begin),
            stroke: |points| send(ReviewMessage::Stroke(points)),
            erase: |names| send(ReviewMessage::Erase(names)),
        };
        let geometry = target.map(|t| t.geometry);
        Some(review_view::surface(Id::new("review surface"), strokes, geometry, self.review.brush, self.review.drawing, wiring).into())
    }

    /// The frame's comments, oldest first: the conversation the panel shows.
    pub(super) fn review_comments(&self) -> Vec<files::Mark> {
        let Some(target) = self.review_target() else { return Vec::new() };
        self.review.folder(&target.dir).map(|f| conversation(f.on(target.frame))).unwrap_or_default()
    }

    /// The review panel at the viewer's right in review mode: the brush,
    /// Undo, Redo and Clear, then the frame's comments as a conversation
    /// (oldest first, who and when on each) with the field to add one under
    /// them.
    pub(super) fn review_panel(&self) -> Option<Element<Message>> {
        if !self.review.drawing {
            return None;
        }
        let brush = self.review.brush;
        let target = self.review_target();
        let reviewable = target.is_some();
        let act = |id: &'static str, words: &'static str, message: ReviewMessage, enabled: bool| {
            let press = look::button(Id::new(id), text(words).font_size(12.0)).padding(Padding::from([2.0, 8.0])).focusable(false);
            if enabled { press.on_press(move || send(message.clone())) } else { press }
        };

        let done =
            tooltip(Id::new("review done tip"), act("review done", "Done", ReviewMessage::Mode(false), true), "Leave review mode (D)");
        let title = row()
            .align_y(Vertical::Center)
            .push(text("Review").font_size(14.0).color(look::text()))
            .push(space(Length::Fill, Length::Shrink))
            .push(done);

        let tools = segmented(Id::new("review tool"), usize::from(brush.tool == Tool::Eraser))
            .push("Pen")
            .push("Eraser")
            .on_select(|i| send(ReviewMessage::Tool(if i == 1 { Tool::Eraser } else { Tool::Pen })))
            .focusable(false);
        let mut swatches = row().spacing(2.0).align_y(Vertical::Center);
        for (i, (name, rgb)) in COLOURS.iter().enumerate() {
            let [r, g, b] = rgb.0;
            let picked = i == brush.colour && brush.tool == Tool::Pen;
            let chip =
                container(text(String::new())).width(Length::fixed(16.0)).height(Length::fixed(16.0)).background(Color::from_rgb8(r, g, b));
            let ring = container(chip).padding(1.5).border(1.5, if picked { look::text() } else { Color::TRANSPARENT }).border_radius(2.0);
            let press = primer::prelude::button(Id::new(format!("review colour {i}")), ring)
                .background(Color::TRANSPARENT)
                .padding(1.0)
                .focusable(false)
                .on_press(move || send(ReviewMessage::Colour(i)));
            swatches = swatches.push(tooltip(Id::new(format!("review colour {i} tip")), press, *name));
        }
        let size = row()
            .spacing(8.0)
            .align_y(Vertical::Center)
            .push(text(format!("Size {:.0}", brush.size)).font_size(12.0).color(look::quiet()).width(Length::fixed(48.0)))
            .push(
                slider(Id::new("review size"), model::SIZE_MIN..=model::SIZE_MAX, brush.size, |v| send(ReviewMessage::Size(v)))
                    .step(1.0)
                    .width(Length::Fill),
            );
        let pressure = row()
            .spacing(6.0)
            .align_y(Vertical::Center)
            .push(look::tick_box(Id::new("review pressure"), brush.pressure, |on| send(ReviewMessage::Pressure(on))).focusable(false))
            .push(text("Pen pressure sets the width").font_size(12.0).color(look::quiet()));
        let actions = row()
            .spacing(6.0)
            .push(act("review undo", "Undo", ReviewMessage::Undo, self.review.can_undo()))
            .push(act("review redo", "Redo", ReviewMessage::Redo, self.review.can_redo()))
            .push(space(Length::Fill, Length::Shrink))
            .push(act("review clear", "Clear Frame", ReviewMessage::Clear, reviewable));
        let drawing = column().spacing(8.0).push(heading("Draw")).push(tools).push(swatches).push(size).push(pressure).push(actions);

        // The conversation on the frame shown.
        let about = match &target {
            Some(t) => format!("Comments on {} frame {}", t.label, t.frame),
            None => "Comments".to_owned(),
        };
        let comments = self.review_comments();
        let now = files::now_ms();
        let mut thread = column().spacing(10.0).width(Length::Fill);
        if comments.is_empty() {
            let words = if reviewable { "No comments on this frame yet." } else { "Nothing to review here." };
            thread = thread.push(text(words).font_size(12.0).color(look::quiet()));
        }
        let editing_name = self.review.draft.editing.as_ref().map(|(_, n)| n.clone());
        for mark in &comments {
            let Body::Note(note) = &mark.body else { continue };
            let name = mark.file_name();
            let small = |id: String, words: &'static str, tip: &'static str, message: Message| {
                let press = look::button(Id::new(id.clone()), text(words).font_size(11.0))
                    .padding(Padding::from([1.0, 6.0]))
                    .focusable(false)
                    .on_press(move || message.clone());
                tooltip(Id::new(format!("{id} tip")), press, tip)
            };
            let head = row()
                .spacing(6.0)
                .align_y(Vertical::Center)
                .push(text(model::who(&mark.author)).font_size(12.0).semibold().color(look::text()))
                .push(text(model::ago(mark.created, now)).font_size(11.0).color(look::quiet()))
                .push(space(Length::Fill, Length::Shrink))
                .push(small(
                    format!("edit note {name}"),
                    "Edit",
                    "Change this comment: anyone may",
                    send(ReviewMessage::EditNote(name.clone())),
                ))
                .push(small(
                    format!("delete note {name}"),
                    "\u{2715}",
                    "Delete this comment",
                    send(ReviewMessage::DeleteNote(name.clone())),
                ));
            let mut card =
                column().spacing(3.0).push(head).push(text(note.text.clone()).font_size(13.0).color(look::text()).width(Length::Fill));
            if let Some((when, by)) = &note.edited {
                card = card
                    .push(text(format!("changed by {} {}", model::who(by), model::ago(*when, now))).font_size(10.0).color(look::quiet()));
            }
            let mut boxed = container(card)
                .padding(Padding::from([6.0, 8.0]))
                .width(Length::Fill)
                .background(Color::from_rgb8(30, 30, 30))
                .border_radius(4.0);
            if editing_name.as_deref() == Some(name.as_str()) {
                boxed = boxed.border(1.0, look::review());
            }
            thread = thread.push(boxed);
        }

        let editing = self.review.draft.editing.is_some();
        let placeholder = match (reviewable, editing, comments.is_empty()) {
            (_, true, _) => "Change the comment; empty deletes it",
            (true, false, true) => "Write a comment on this frame",
            (true, false, false) => "Reply",
            (false, false, _) => "Nothing to review here",
        };
        let mut field =
            text_input(Id::new("review note"), self.review.draft.text.clone()).placeholder(placeholder).font_size(12.0).width(Length::Fill);
        if reviewable || editing {
            field = field.on_input(|t| send(ReviewMessage::Draft(t))).on_submit(|| send(ReviewMessage::Post));
        }
        let can_post = !self.review.draft.text.trim().is_empty() || editing;
        let mut send_row = row().spacing(6.0).push(space(Length::Fill, Length::Shrink));
        if editing {
            send_row = send_row.push(act("review cancel note", "Cancel", ReviewMessage::CancelNote, true));
        }
        let send_row = send_row.push(act(
            "review post",
            if editing { "Save" } else { "Send" },
            ReviewMessage::Post,
            can_post && (reviewable || editing),
        ));
        // The title stays at the top and the field and Send at the bottom
        // whatever the viewer's height: the brush and the conversation
        // scroll between them.
        let middle = column().spacing(16.0).width(Length::Fill).push(drawing).push(column().spacing(8.0).push(heading(about)).push(thread));
        let middle = scrollable(Id::new("review panel scroll"), middle).width(Length::Fill).height(Length::Fill);
        let body = column().spacing(10.0).height(Length::Fill).push(title).push(middle).push(field).push(send_row);
        Some(container(body).padding(12.0).width(Length::fixed(PANEL_WIDTH)).height(Length::Fill).background(look::panel()).into())
    }
}

#[cfg(test)]
mod tests {

    proptest::proptest! {
        /// The conversation is every comment of the marks given and no
        /// stroke, oldest first, whatever order they come in.
        #[test]
        fn the_conversation_is_the_comments_oldest_first(
            made in proptest::collection::vec((0u64..50, proptest::bool::ANY, 0u64..1000), 0..12),
            seed in 0usize..1000,
        ) {
            let mut marks: Vec<files::Mark> = made
                .iter()
                .enumerate()
                .map(|(i, &(created, is_note, salt))| files::Mark {
                    id: format!("{created:013}-{salt:016x}{i:x}"),
                    frame: 1001,
                    author: format!("a{i}"),
                    created,
                    body: if is_note {
                        Body::Note(files::Note { text: format!("n{i}"), edited: None })
                    } else {
                        Body::Stroke(files::Stroke { color: files::Rgb([0, 0, 0]), size: 0.01, points: Vec::new() })
                    },
                })
                .collect();
            let len = marks.len().max(1);
            marks.rotate_left(seed % len);
            let talk = conversation(marks.iter());
            proptest::prop_assert_eq!(talk.len(), made.iter().filter(|m| m.1).count());
            proptest::prop_assert!(talk.iter().all(|m| matches!(m.body, Body::Note(_))));
            proptest::prop_assert!(talk.windows(2).all(|w| (w[0].created, w[0].file_name()) <= (w[1].created, w[1].file_name())));
            marks.reverse();
            let again: Vec<String> = conversation(marks.iter()).iter().map(files::Mark::file_name).collect();
            proptest::prop_assert_eq!(again, talk.iter().map(files::Mark::file_name).collect::<Vec<_>>());
        }
    }
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// Every frame a sequence has is found at its index, stepped or
        /// not, and no frame between its steps or outside it is.
        #[test]
        fn a_frame_is_found_at_its_index(first in -50i64..2000, step in 1i64..4, count in 1usize..40, pick in any::<prop::sample::Index>()) {
            // A sequence's frame numbers come from its first and step; the
            // files need not be there.
            let frames: Vec<std::path::PathBuf> = (0..count).map(|i| format!("f.{}.jpg", first + i as i64 * step).into()).collect();
            let media = Media::jpg(th_media::JpgSequence::new(first, step, frames), (24, 1));
            let i = pick.index(count);
            prop_assert_eq!(index_of(&media, first + i as i64 * step), Some(i));
            prop_assert_eq!(index_of(&media, first - step), None);
            prop_assert_eq!(index_of(&media, first + count as i64 * step), None);
            if step > 1 {
                prop_assert_eq!(index_of(&media, first + i as i64 * step + 1), None);
            }
        }
    }
}
