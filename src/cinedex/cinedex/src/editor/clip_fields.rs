//! The clip fields (#60): the selected clip's fades as typed frame counts,
//! beside the drag handles on its top band, and a video clip's opacity as
//! a percentage and its transform (how far right and up it is moved, in
//! percent of the picture's height, its scale in percent and its rotation
//! in degrees, anticlockwise; Reset puts it back as fitted). A field shows
//! the clip's value until something is typed into it; Enter sets what was
//! typed, as one op (`SetFades`, `SetOpacity`, `SetTransform`) through the
//! document's history, refused (the status says why) rather than clamped,
//! as every op is. What is typed may be maths (`100/2`, `+=10` from the
//! field's value: `crate::number_field`); a fade rounds to whole frames.

use primer::prelude::*;
use th_edit::document::{Clip, ClipRef, Frame, Source, TrackKind, Transform, WorkingEdit};
use th_edit::ops::Op;

use super::{Editor, Message};
use crate::ladder::{Number, ladder};
use crate::look;
use crate::number_field;

/// A clip field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClipField {
    FadeIn,
    FadeOut,
    Opacity,
    /// The transform's: right and up in percent of the picture's height,
    /// the scale in percent, the rotation in degrees.
    X,
    Y,
    Scale,
    Rotation,
}

impl ClipField {
    /// The fields in the order the row shows them.
    pub(crate) const ALL: [ClipField; 7] =
        [ClipField::FadeIn, ClipField::FadeOut, ClipField::Opacity, ClipField::X, ClipField::Y, ClipField::Scale, ClipField::Rotation];

    /// Whether it is one of the transform's.
    pub(crate) fn of_transform(self) -> bool {
        matches!(self, ClipField::X | ClipField::Y | ClipField::Scale | ClipField::Rotation)
    }

    /// Whether only a video clip has it.
    pub(crate) fn video_only(self) -> bool {
        self == ClipField::Opacity || self.of_transform()
    }

    /// What it is called in a refusal.
    fn name(self) -> &'static str {
        match self {
            ClipField::FadeIn | ClipField::FadeOut => "a fade",
            ClipField::Opacity => "an opacity",
            ClipField::X | ClipField::Y => "a position",
            ClipField::Scale => "a scale",
            ClipField::Rotation => "a rotation",
        }
    }

    /// The least and most a transform field takes, as typed.
    fn limits(self) -> (f64, f64) {
        match self {
            ClipField::X | ClipField::Y => (-Transform::MAX_MOVE * 100.0, Transform::MAX_MOVE * 100.0),
            ClipField::Scale => (Transform::MIN_SCALE * 100.0, Transform::MAX_SCALE * 100.0),
            ClipField::Rotation => (-Transform::MAX_ROTATION, Transform::MAX_ROTATION),
            ClipField::Opacity => (0.0, 100.0),
            ClipField::FadeIn | ClipField::FadeOut => (0.0, 1_000_000.0),
        }
    }
}

/// `field` of `transform` as its field shows it: percent, or degrees.
fn transform_value(transform: &Transform, field: ClipField) -> f64 {
    match field {
        ClipField::X => transform.x * 100.0,
        ClipField::Y => transform.y * 100.0,
        ClipField::Scale => transform.scale * 100.0,
        _ => transform.rotation,
    }
}

/// `transform` with `field` set to `value` (as typed: percent, or degrees).
fn with_value(transform: Transform, field: ClipField, value: f64) -> Transform {
    match field {
        ClipField::X => Transform { x: value / 100.0, ..transform },
        ClipField::Y => Transform { y: value / 100.0, ..transform },
        ClipField::Scale => Transform { scale: value / 100.0, ..transform },
        _ => Transform { rotation: value, ..transform },
    }
}

/// A number to a tenth, without a trailing `.0` (and no `-0`).
pub(crate) fn tenths(value: f64) -> String {
    let tenths = (value * 10.0).round() / 10.0;
    if tenths == 0.0 {
        "0".to_owned()
    } else if tenths.fract() == 0.0 {
        format!("{tenths:.0}")
    } else {
        format!("{tenths:.1}")
    }
}

/// The op that puts `clip` back as fitted: None when it is already, or is
/// no video clip.
pub(crate) fn reset_op(edit: &WorkingEdit, clip: ClipRef) -> Option<Op<Source>> {
    let (.., transform) = numbers(edit, clip)?;
    (clip.track.kind == TrackKind::Video && !transform.is_identity()).then_some(Op::SetTransform { clip, transform: Transform::IDENTITY })
}

/// What `field` of `clip` holds now: frames for a fade, a percentage for
/// an opacity (an audio clip's is 100). None: no such clip.
pub(crate) fn field_text(edit: &WorkingEdit, clip: ClipRef, field: ClipField) -> Option<String> {
    let (fade_in, fade_out, opacity, transform) = numbers(edit, clip)?;
    Some(match field {
        ClipField::FadeIn => fade_in.to_string(),
        ClipField::FadeOut => fade_out.to_string(),
        ClipField::Opacity => percent(opacity),
        _ => tenths(transform_value(&transform, field)),
    })
}

/// The op that setting `field` of `clip` to `text` makes: None when it is
/// what the clip has already; an error when `text` is no value of that
/// field. Whether it fits the clip is the op's to say.
pub(crate) fn field_op(edit: &WorkingEdit, clip: ClipRef, field: ClipField, text: &str) -> Result<Option<Op<Source>>, String> {
    let (fade_in, fade_out, opacity, transform) = numbers(edit, clip).ok_or("that clip is gone")?;
    let text = text.trim();
    match field {
        ClipField::FadeIn | ClipField::FadeOut => {
            let own = if field == ClipField::FadeIn { fade_in } else { fade_out };
            let typed = number_field::whole(text, Some(own));
            let frames: Frame = typed
                .ok()
                .filter(|f| *f >= 0)
                .ok_or_else(|| number_field::refusal(text, typed.err(), "a fade is a whole number of frames, 0 or more"))?;
            let (new_in, new_out) = if field == ClipField::FadeIn { (frames, fade_out) } else { (fade_in, frames) };
            Ok(((new_in, new_out) != (fade_in, fade_out)).then_some(Op::SetFades { clip, fade_in: new_in, fade_out: new_out }))
        }
        ClipField::Opacity => {
            if clip.track.kind != TrackKind::Video {
                return Err("only a video clip has an opacity".into());
            }
            let typed = number_field::number(text, &['%'], Some(opacity * 100.0));
            let number: f64 = typed
                .ok()
                .filter(|p: &f64| (0.0..=100.0).contains(p))
                .ok_or_else(|| number_field::refusal(text, typed.err(), "an opacity is a percentage, 0 to 100"))?;
            let new = number / 100.0;
            Ok((percent(new) != percent(opacity)).then_some(Op::SetOpacity { clip, opacity: new }))
        }
        ClipField::X | ClipField::Y | ClipField::Scale | ClipField::Rotation => {
            if clip.track.kind != TrackKind::Video {
                return Err("only a video clip has a transform".into());
            }
            let (min, max) = field.limits();
            let unit = if field == ClipField::Rotation { '\u{b0}' } else { '%' };
            let typed = number_field::number(text, &[unit], Some(transform_value(&transform, field)));
            let number: f64 = typed.ok().filter(|v: &f64| (min..=max).contains(v)).ok_or_else(|| {
                number_field::refusal(text, typed.err(), &format!("{} is a number from {} to {}", field.name(), tenths(min), tenths(max)))
            })?;
            // What the field shows already is no change.
            let shown = tenths(transform_value(&transform, field));
            Ok((tenths(number) != shown).then_some(Op::SetTransform { clip, transform: with_value(transform, field, number) }))
        }
    }
}

/// `field` as a ladder number: its value is what the field shows (`typed`,
/// else the clip's own `shown`), its limits and default those of a fade
/// (whole frames, 0 or more, default 0), an opacity (0 to 100 %, to a
/// tenth, default 100) or a part of the transform (to a tenth; default: as
/// fitted, so no move, 100 % and no turn).
pub(crate) fn field_number(field: ClipField, typed: &str, shown: &str) -> Number {
    // Maths half typed is no value yet: the ladder then starts from the
    // clip's own.
    let own = number_field::number(shown, &['%', '\u{b0}'], None).ok();
    let value = number_field::number(typed, &['%', '\u{b0}'], own).ok().or(own).unwrap_or(0.0);
    match field {
        ClipField::FadeIn | ClipField::FadeOut => Number { value, min: 0.0, max: 1_000_000.0, decimals: 0, default: 0.0 },
        ClipField::Opacity => Number { value, min: 0.0, max: 100.0, decimals: 1, default: 100.0 },
        _ => {
            let (min, max) = field.limits();
            Number { value, min, max, decimals: 1, default: transform_value(&Transform::IDENTITY, field) }
        }
    }
}

/// A ladder value as `field`'s text.
pub(crate) fn field_value_text(field: ClipField, value: f64) -> String {
    match field {
        ClipField::FadeIn | ClipField::FadeOut => format!("{}", value.round() as Frame),
        ClipField::Opacity => percent(value / 100.0),
        _ => tenths(value),
    }
}

/// An opacity as a percentage, to a tenth, without a trailing `.0`.
pub(crate) fn percent(opacity: f64) -> String {
    let tenths = (opacity * 1000.0).round() / 10.0;
    if tenths.fract() == 0.0 { format!("{tenths:.0}") } else { format!("{tenths:.1}") }
}

fn numbers(edit: &WorkingEdit, clip: ClipRef) -> Option<(Frame, Frame, f64, Transform)> {
    fn of<C: Clip>(c: &C) -> (Frame, Frame, f64, Transform) {
        (c.fade_in(), c.fade_out(), c.opacity(), c.transform())
    }
    match clip.track.kind {
        TrackKind::Video => edit.video.get(clip.track.index)?.clips.get(clip.index).map(of),
        TrackKind::Audio => edit.audio.get(clip.track.index)?.clips.get(clip.index).map(of),
    }
}

impl Editor {
    /// The one clip selected, whose fields show.
    fn field_clip(&self) -> Option<ClipRef> {
        match self.selection.iter().collect::<Vec<_>>().as_slice() {
            [clip] => Some(**clip),
            _ => None,
        }
    }

    /// Text typed into a clip field, not set yet.
    pub(super) fn type_into(&mut self, field: ClipField, text: String) {
        if let Some(clip) = self.field_clip() {
            self.clip_draft = Some((clip, field, text));
        }
    }

    /// Enter in a clip field: set what was typed.
    pub(super) fn set_field(&mut self, field: ClipField) -> Command<Message> {
        let Some((clip, typed, text)) = self.clip_draft.take() else { return Command::none() };
        if typed != field || Some(clip) != self.field_clip() {
            return Command::none();
        }
        match field_op(&self.doc.edit, clip, field, &text) {
            Ok(Some(op)) => self.edit(&[op]),
            Ok(None) => Command::none(),
            Err(why) => {
                self.status = Some((format!("Not done: {why}"), true));
                Command::none()
            }
        }
    }

    /// The selected clip's fades, opacity and transform, as fields; with
    /// no one clip selected, the same fields greyed and the hint to select
    /// one.
    pub(super) fn clip_fields(&self) -> Element<Message> {
        let (fields, reset, hint) = self.clip_fields_shown();
        fields_row(fields, reset, hint)
    }

    /// What the fields row shows: each field (in [`ClipField::ALL`]'s
    /// order), the op Reset sends, and the hint at its end.
    pub(super) fn clip_fields_shown(&self) -> ([FieldShown; 7], Option<Op<Source>>, &'static str) {
        let clip = self.field_clip();
        let shown = |which: ClipField| -> FieldShown {
            let Some(clip) = clip else { return FieldShown::default() };
            let own = field_text(&self.doc.edit, clip, which).unwrap_or_default();
            let value = match &self.clip_draft {
                Some((c, f, typed)) if *c == clip && *f == which => typed.clone(),
                _ => own.clone(),
            };
            // An audio clip has no opacity or transform: the fields stay,
            // greyed. Nor does a published edit take typing.
            let enabled = !self.doc.read_only() && (!which.video_only() || clip.track.kind == TrackKind::Video);
            FieldShown { value, shown: own, enabled }
        };
        let hint = if clip.is_some() { HINT_SELECTED } else { HINT_NONE };
        let reset = clip.filter(|_| !self.doc.read_only()).and_then(|clip| reset_op(&self.doc.edit, clip));
        (ClipField::ALL.map(shown), reset, hint)
    }
}

/// The hint at the row's end with one clip selected, and with none (or
/// several).
pub(crate) const HINT_SELECTED: &str = "Enter sets a field, middle-drag steps it";
pub(crate) const HINT_NONE: &str = "Select one clip to type its fades, opacity and transform";

/// One clip field as the row shows it: the text in it (typed, else the
/// clip's), the clip's own value, and whether it takes typing.
#[derive(Debug, Clone, Default)]
pub(crate) struct FieldShown {
    pub value: String,
    pub shown: String,
    pub enabled: bool,
}

/// The fields row: fade in, fade out, opacity and the transform (X, Y,
/// scale, rotation, and Reset, which sends `reset` and is greyed without
/// one: nothing to reset, or no video clip), always the same widgets
/// in the same places at [`CLIP_FIELDS_HEIGHT`](super::panels::CLIP_FIELDS_HEIGHT),
/// so selecting a clip, clearing the selection or dragging never moves the
/// tracks under it (2026-11-16: the row was swapped for a shorter line with
/// nothing selected, and the tracks jumped). A field that takes no typing
/// is greyed, with no value ladder.
pub(crate) fn fields_row(fields: [FieldShown; 7], reset: Option<Op<Source>>, hint: &str) -> Element<Message> {
    let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
    let [fade_in, fade_out, opacity, x, y, scale, rotation] = fields;
    let field = |id: &'static str, which: ClipField, f: FieldShown| -> Element<Message> {
        let input = text_input(Id::new(id), f.value.clone()).font_size(12.0).width(Length::fixed(56.0));
        if !f.enabled {
            return input.disabled(true).into();
        }
        let input = input.on_input(move |s| Message::ClipField(which, s)).on_submit(move || Message::ClipFieldSet(which));
        // Middle-drag: Houdini's value ladder; release sets it.
        let number = field_number(which, &f.value, &f.shown);
        ladder(
            Id::new(format!("{id} ladder")),
            input,
            number,
            move |v| Message::ClipField(which, field_value_text(which, v)),
            Some(Message::ClipFieldSet(which)),
        )
    };
    let fields = row()
        .spacing(8.0)
        .align_y(Vertical::Center)
        .push(label("Fade in"))
        .push(field("fade in field", ClipField::FadeIn, fade_in))
        .push(label("Fade out"))
        .push(field("fade out field", ClipField::FadeOut, fade_out))
        .push(label("frames"))
        .push(space(Length::fixed(12.0), Length::Shrink))
        .push(label("Opacity"))
        .push(field("opacity field", ClipField::Opacity, opacity))
        .push(label("%"))
        .push(space(Length::fixed(12.0), Length::Shrink))
        .push(tooltip(Id::new("transform x tip"), label("X"), "How far right the clip is moved, in percent of the picture's height"))
        .push(field("transform x field", ClipField::X, x))
        .push(tooltip(Id::new("transform y tip"), label("Y"), "How far up the clip is moved, in percent of the picture's height"))
        .push(field("transform y field", ClipField::Y, y))
        .push(tooltip(Id::new("transform scale tip"), label("Scale"), "The clip's size in percent: 100 fits the picture"))
        .push(field("transform scale field", ClipField::Scale, scale))
        .push(tooltip(Id::new("transform rotation tip"), label("Rotate"), "Degrees the clip is turned, anticlockwise"))
        .push(field("transform rotation field", ClipField::Rotation, rotation))
        .push(tooltip(Id::new("transform reset tip"), reset_button(reset), "Put the clip back as fitted: not moved, scaled or turned"))
        .push(space(Length::fixed(12.0), Length::Shrink))
        .push(label(hint));
    container(fields)
        .padding(Padding::from([3.0, 14.0]))
        .width(Length::Fill)
        .height(Length::fixed(super::panels::CLIP_FIELDS_HEIGHT))
        .align_y(Vertical::Center)
        .background(look::panel())
        .into()
}

/// Reset, greyed without an op to send. Out of keyboard focus, so the
/// player's keys keep working after a click.
fn reset_button(reset: Option<Op<Source>>) -> Element<Message> {
    let colour = if reset.is_some() { look::text() } else { look::quiet() };
    let button = look::button(Id::new("transform reset"), text("Reset").font_size(12.0).color(colour))
        .padding(Padding::from([3.0, 8.0]))
        .focusable(false);
    match reset {
        Some(op) => button.on_press(move || Message::Edit(op.clone())).into(),
        None => button.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{AudioClip, Track, TrackRef, VersionRule, VideoClip};
    use th_edit::ops::{Available, Sources, apply};

    struct Anything;
    impl Sources<Source> for Anything {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    fn edit(duration: Frame, fades: (Frame, Frame), opacity: f64) -> WorkingEdit {
        let source = Source::Media { item: "m".into(), version: VersionRule::Latest };
        let mut edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
        let mut v = VideoClip::new(0, duration, 0, source.clone());
        (v.fade_in, v.fade_out, v.opacity) = (fades.0, fades.1, opacity);
        edit.video.push(Track { name: "V1".into(), clips: vec![v], color: None });
        let a = AudioClip { at: 0, duration, source_in: 0, source, gain_db: 0.0, fade_in: fades.0, fade_out: fades.1 };
        edit.audio.push(Track { name: "A1".into(), clips: vec![a], color: None });
        edit
    }

    /// A fixed-advance measurer: half the font size a character, one line.
    struct Mono;

    impl primer::core::measure::TextMeasurer for Mono {
        fn measure_text(&self, content: &str, size: f32, max_width: Option<f32>) -> Size {
            self.layout_text(content, size, max_width).size
        }
        fn measure_styled(&self, content: &str, _: &[primer::core::render::TextSpan], size: f32, max_width: Option<f32>) -> Size {
            self.measure_text(content, size, max_width)
        }
        fn layout_text(&self, content: &str, size: f32, _: Option<f32>) -> primer::core::measure::TextLayout {
            use primer::core::measure::{TextLayout, TextLine, VisualLines, line_height};
            let n = content.chars().count();
            let carets: Vec<f32> = (0..=n).map(|i| i as f32 * size * 0.5).collect();
            let width = carets[n];
            TextLayout {
                lines: VisualLines::new(vec![TextLine { start: 0, end: n, width, carets }]),
                size: Size::new(width, line_height(size)),
            }
        }
    }

    /// The height the fields row lays out at, in a window `width` wide, and
    /// the height its widgets ask for inside it.
    fn row_heights(fields: [FieldShown; 7], reset: bool, hint: &str, width: f32) -> (f32, f32) {
        use primer::widget::custom::{LayoutCtx, Limits, StateStore, Widget};
        let clip = ClipRef { track: TrackRef::video(0), index: 0 };
        let element = fields_row(fields, reset.then_some(Op::SetTransform { clip, transform: Transform::IDENTITY }), hint);
        let (theme, store) = (Theme::dark(), StateStore::new());
        let node =
            element.layout(&Limits::new(Size::ZERO, Size::new(width, 1000.0)), LayoutCtx { measurer: &Mono, store: &store, theme: &theme });
        let inner = node.children().first().map_or(0.0, |c| c.bounds().height);
        (node.bounds().height, inner)
    }

    fn shown(value: &str, enabled: bool) -> FieldShown {
        FieldShown { value: value.into(), shown: value.into(), enabled }
    }

    proptest! {
        /// The fields row is one height whatever is selected: one video clip
        /// (with a transform to reset or none), one audio clip (opacity and
        /// transform greyed), none or several (all greyed, the hint to
        /// select one), whatever the values and the window's width; and
        /// what it holds fits inside it, so nothing is cut off.
        #[test]
        fn the_fields_row_never_changes_height(
            values in prop::collection::vec("-?[0-9]{0,6}", 7),
            selected in 0usize..3,
            reset in any::<bool>(),
            width in 200f32..4000.0,
        ) {
            let fields = |enabled: [bool; 7]| -> [FieldShown; 7] { std::array::from_fn(|i| shown(&values[i], enabled[i])) };
            let (none, none_inner) = row_heights(std::array::from_fn(|_| FieldShown::default()), false, HINT_NONE, width);
            let (h, inner) = match selected {
                0 => row_heights(fields([true; 7]), reset, HINT_SELECTED, width),
                1 => row_heights(fields([true, true, false, false, false, false, false]), false, HINT_SELECTED, width),
                _ => row_heights(fields([false; 7]), false, HINT_NONE, width),
            };
            prop_assert_eq!(h, super::super::panels::CLIP_FIELDS_HEIGHT);
            prop_assert_eq!(none, h);
            prop_assert_eq!(none_inner, inner, "the widgets inside differ in height");
            prop_assert!(inner > 0.0 && inner <= h - 6.0, "the widgets ({}) overflow the row ({})", inner, h);
        }

        /// A fade typed as frames (#60) sets that fade and keeps the other;
        /// it is set exactly when it fits the clip with the other fade, and
        /// the field then shows what was typed. The clip's own value makes
        /// no op; anything not a whole number of frames, 0 or more, is
        /// refused before any op.
        #[test]
        fn a_typed_fade_sets_that_fade(
            duration in 1i64..60,
            fades in (0i64..30, 0i64..30),
            typed in -5i64..70,
            out in any::<bool>(),
            audio in any::<bool>(),
            pad in prop::sample::select(vec!["", " ", "  "]),
        ) {
            let fades = (fades.0.min(duration), fades.1.min(duration - fades.0.min(duration)));
            let edit = edit(duration, fades, 1.0);
            let clip = ClipRef { track: if audio { TrackRef::audio(0) } else { TrackRef::video(0) }, index: 0 };
            let field = if out { ClipField::FadeOut } else { ClipField::FadeIn };
            let result = field_op(&edit, clip, field, &format!("{pad}{typed}{pad}"));
            if typed < 0 {
                prop_assert!(result.is_err());
                return Ok(());
            }
            let own = if out { fades.1 } else { fades.0 };
            let op = result.unwrap();
            if typed == own {
                prop_assert!(op.is_none());
                return Ok(());
            }
            let want = if out { (fades.0, typed) } else { (typed, fades.1) };
            prop_assert_eq!(op.clone(), Some(Op::SetFades { clip, fade_in: want.0, fade_out: want.1 }));
            let applied = apply(&edit, &op.unwrap(), &Anything);
            prop_assert_eq!(applied.is_ok(), want.0 + want.1 <= duration);
            if let Ok(next) = applied {
                prop_assert_eq!(field_text(&next, clip, field), Some(typed.to_string()));
                let other = if out { ClipField::FadeIn } else { ClipField::FadeOut };
                prop_assert_eq!(field_text(&next, clip, other), field_text(&edit, clip, other));
            }
            for bad in ["", "x", "3f", "--2", "1/0", "5,5", "2**3", "-1", "0-1"] {
                prop_assert!(field_op(&edit, clip, field, bad).is_err(), "{:?} taken", bad);
            }
        }

        /// Whatever the value ladder makes of a clip field, from any drag, is
        /// text the field takes (`field_op` never refuses it as no value of
        /// that field), and the ladder reads the field's text back as that
        /// value; Ctrl + middle-click's reset is the clip default (no fade,
        /// full opacity).
        #[test]
        fn a_ladder_value_is_text_the_field_takes(
            start in prop::sample::select(vec!["0", "12", "100", "37.5", "", "junk"]),
            field in prop::sample::select(ClipField::ALL.to_vec()),
            path in prop::collection::vec((-400f32..400.0, -80f32..80.0), 1..8),
        ) {
            let edit = edit(400, (0, 0), 0.5);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            let shown = field_text(&edit, clip, field).unwrap();
            let number = field_number(field, start, &shown);
            let press = primer::Point::new(100.0, 100.0);
            let mut grip = crate::ladder::Grip::new(&number, press);
            for (dx, dy) in path {
                let value = grip.moved(&number, primer::Point::new(press.x + dx, press.y + dy));
                let text = field_value_text(field, value);
                prop_assert!(field_op(&edit, clip, field, &text).is_ok(), "{:?} refused", text);
                prop_assert_eq!(field_number(field, &text, &shown).value, value);
            }
            let reset = field_value_text(field, number.reset());
            prop_assert_eq!(reset, if matches!(field, ClipField::Opacity | ClipField::Scale) { "100" } else { "0" });
        }

        /// A transform field typed as a number (percent of the picture's
        /// height, percent of the fitted size, degrees; with or without its
        /// unit) sets that part of the video clip's transform and keeps the
        /// others, and shows back as typed to a tenth; out of its range, no
        /// number, or on an audio clip, it is refused before any op. What
        /// the field shows already makes no op. Reset is the op that puts
        /// the clip back as fitted, there exactly while it is not, and
        /// after it every field shows its default.
        #[test]
        fn a_typed_transform_field_sets_that_part(
            start in (-300i64..300, -300i64..300, 1i64..4000, -3600i64..3600),
            field in prop::sample::select(vec![ClipField::X, ClipField::Y, ClipField::Scale, ClipField::Rotation]),
            typed in -120_000i64..120_000,
            unit in any::<bool>(),
            fitted in any::<bool>(),
        ) {
            let mut edit = edit(10, (0, 0), 1.0);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            let begin = if fitted {
                Transform::IDENTITY
            } else {
                Transform { x: start.0 as f64 / 1000.0, y: start.1 as f64 / 1000.0, scale: start.2 as f64 / 1000.0 + 0.01, rotation: start.3 as f64 / 10.0 }
            };
            edit.video[0].clips[0].transform = begin;
            prop_assert_eq!(reset_op(&edit, clip).is_some(), !begin.is_identity());
            let value = typed as f64 / 10.0;
            let suffix = match (unit, field) {
                (false, _) => "",
                (true, ClipField::Rotation) => "\u{b0}",
                (true, _) => "%",
            };
            let text = format!(" {}{suffix} ", tenths(value));
            let result = field_op(&edit, clip, field, &text);
            let (min, max) = match field {
                ClipField::Scale => (1.0, 10_000.0),
                ClipField::Rotation => (-3600.0, 3600.0),
                _ => (-1000.0, 1000.0),
            };
            if !(min..=max).contains(&value) {
                prop_assert!(result.is_err(), "{} taken", text);
            } else {
                let before = field_text(&edit, clip, field).unwrap();
                match result.unwrap() {
                    None => prop_assert_eq!(&before, &tenths(value)),
                    Some(op) => {
                        prop_assert_ne!(&before, &tenths(value));
                        let next = apply(&edit, &op, &Anything).unwrap();
                        prop_assert_eq!(field_text(&next, clip, field), Some(tenths(value)));
                        let (was, now) = (begin, next.video[0].clips[0].transform);
                        let want = match field {
                            ClipField::X => Transform { x: value / 100.0, ..was },
                            ClipField::Y => Transform { y: value / 100.0, ..was },
                            ClipField::Scale => Transform { scale: value / 100.0, ..was },
                            _ => Transform { rotation: value, ..was },
                        };
                        prop_assert_eq!(now, want);
                        let reset = reset_op(&next, clip);
                        prop_assert_eq!(reset.is_some(), !now.is_identity());
                        if let Some(reset) = reset {
                            let fitted = apply(&next, &reset, &Anything).unwrap();
                            prop_assert_eq!(fitted.video[0].clips[0].transform, Transform::IDENTITY);
                            for (which, default) in [(ClipField::X, "0"), (ClipField::Y, "0"), (ClipField::Scale, "100"), (ClipField::Rotation, "0")] {
                                prop_assert_eq!(field_text(&fitted, clip, which), Some(default.to_string()));
                            }
                            prop_assert_eq!(reset_op(&fitted, clip), None);
                        }
                    }
                }
            }
            for bad in ["", "x", "1,5", "--2", "nan", "inf"] {
                prop_assert!(field_op(&edit, clip, field, bad).is_err(), "{:?} taken", bad);
            }
            let audio = ClipRef { track: TrackRef::audio(0), index: 0 };
            prop_assert!(field_op(&edit, audio, field, "50").is_err());
            prop_assert_eq!(reset_op(&edit, audio), None);
        }

        /// Maths typed into a clip field (`100/2`; `+=5` and its like from
        /// what the field holds) makes exactly what the number it works out
        /// to makes when typed plainly: the same op, or a refusal where
        /// that number is refused. A fade rounds to whole frames, a half
        /// away from zero.
        #[test]
        fn maths_in_a_clip_field_is_the_number_typed(
            field in prop::sample::select(ClipField::ALL.to_vec()),
            a in 0u32..4000, b in 0u32..400,
            op in prop::sample::select(vec!['+', '-', '*', '/']),
            relative in any::<bool>(), unit in any::<bool>(), pad in prop::sample::select(vec!["", " "]),
        ) {
            let mut edit = edit(400, (3, 5), 0.5);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            edit.video[0].clips[0].transform = Transform { x: 0.25, y: -0.125, scale: 1.5, rotation: 30.0 };
            let (x, y) = (f64::from(a) / 8.0, f64::from(b) / 8.0);
            let own = match field {
                ClipField::FadeIn => 3.0,
                ClipField::FadeOut => 5.0,
                ClipField::Opacity => 0.5 * 100.0,
                ClipField::X => 0.25 * 100.0,
                ClipField::Y => -0.125 * 100.0,
                ClipField::Scale => 1.5 * 100.0,
                ClipField::Rotation => 30.0,
            };
            let left = if relative { own } else { x };
            let value = match op { '+' => left + y, '-' => left - y, '*' => left * y, _ => left / y };
            let suffix = match (unit, field) {
                (false, _) | (true, ClipField::FadeIn | ClipField::FadeOut) => "",
                (true, ClipField::Rotation) => "\u{b0}",
                (true, _) => "%",
            };
            let typed = if relative { format!("{op}={pad}{y}{suffix}") } else { format!("{x}{pad}{op}{pad}{y}{suffix}") };
            let got = field_op(&edit, clip, field, &typed);
            if !value.is_finite() {
                prop_assert!(got.is_err(), "{} taken", typed);
                return Ok(());
            }
            let fade = matches!(field, ClipField::FadeIn | ClipField::FadeOut);
            let plain = if fade { format!("{}", value.round() as i64) } else { format!("{}", if value == 0.0 { 0.0 } else { value }) };
            let want = field_op(&edit, clip, field, &plain);
            prop_assert_eq!(got.is_err(), want.is_err(), "{} against {}", typed, plain);
            prop_assert_eq!(got.ok(), want.ok(), "{} against {}", typed, plain);
        }

        /// An opacity typed as a percentage, with or without `%`, sets the
        /// video clip's opacity to that over 100 and shows it back as typed
        /// to a tenth; outside 0 to 100, or on an audio clip, it is refused.
        #[test]
        fn a_typed_opacity_sets_the_opacity(tenths in -100i64..1100, start in prop::sample::select(vec![1.0f64, 0.5, 0.0]), sign in any::<bool>()) {
            let edit = edit(10, (0, 0), start);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            let value = tenths as f64 / 10.0;
            let typed = format!("{}{}", percent(value / 100.0), if sign { "%" } else { "" });
            let typed = if value < 0.0 { format!("-{}", percent(-value / 100.0)) } else { typed };
            let result = field_op(&edit, clip, ClipField::Opacity, &typed);
            if !(0.0..=100.0).contains(&value) {
                prop_assert!(result.is_err(), "{} taken", typed);
                return Ok(());
            }
            match result.unwrap() {
                None => prop_assert_eq!(percent(start), percent(value / 100.0)),
                Some(op) => {
                    prop_assert_eq!(&op, &Op::SetOpacity { clip, opacity: value / 100.0 });
                    let next = apply(&edit, &op, &Anything).unwrap();
                    prop_assert_eq!(field_text(&next, clip, ClipField::Opacity), Some(percent(value / 100.0)));
                }
            }
            let audio = ClipRef { track: TrackRef::audio(0), index: 0 };
            prop_assert!(field_op(&edit, audio, ClipField::Opacity, "50").is_err());
            prop_assert_eq!(field_text(&edit, audio, ClipField::Opacity), Some("100".to_string()));
        }
    }

    /// The sums an artist types, in each kind of field, and what a refused
    /// one says.
    #[test]
    fn sums_set_what_they_work_out_to() {
        let edit = edit(400, (3, 5), 0.5);
        let clip = ClipRef { track: TrackRef::video(0), index: 0 };
        let op = |field, text: &str| field_op(&edit, clip, field, text);
        assert_eq!(op(ClipField::FadeIn, "100/2"), Ok(Some(Op::SetFades { clip, fade_in: 50, fade_out: 5 })));
        assert_eq!(op(ClipField::FadeOut, "+=10"), Ok(Some(Op::SetFades { clip, fade_in: 3, fade_out: 15 })));
        assert_eq!(op(ClipField::FadeIn, "7/2"), Ok(Some(Op::SetFades { clip, fade_in: 4, fade_out: 5 })));
        assert_eq!(op(ClipField::FadeIn, "(1+2)"), Ok(None), "what it has already");
        assert_eq!(op(ClipField::Opacity, "100/4%"), Ok(Some(Op::SetOpacity { clip, opacity: 0.25 })));
        assert_eq!(op(ClipField::Opacity, "*=2"), Ok(Some(Op::SetOpacity { clip, opacity: 1.0 })));
        let scaled = Transform { scale: 0.5, ..Transform::IDENTITY };
        assert_eq!(op(ClipField::Scale, "200/4"), Ok(Some(Op::SetTransform { clip, transform: scaled })));
        assert_eq!(op(ClipField::FadeIn, "1/0").unwrap_err(), "\"1/0\": it divides by zero");
        assert_eq!(op(ClipField::FadeIn, "2+").unwrap_err(), "\"2+\": a fade is a whole number of frames, 0 or more");
        assert_eq!(op(ClipField::Opacity, "50*3").unwrap_err(), "\"50*3\": an opacity is a percentage, 0 to 100");
        // The ladder starts from what the sum works out to, else the clip's own.
        assert_eq!(field_number(ClipField::Opacity, "100/4", "50").value, 25.0);
        assert_eq!(field_number(ClipField::Opacity, "100/", "50").value, 50.0);
        assert_eq!(field_number(ClipField::Opacity, "+=5", "50").value, 55.0);
    }
}
