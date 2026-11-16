//! The clip fields (#60): the selected clip's fades as typed frame counts,
//! beside the drag handles on its top band, and a video clip's opacity as
//! a percentage. A field shows the clip's value until something is typed
//! into it; Enter sets what was typed, as one op (`SetFades`,
//! `SetOpacity`) through the document's history, refused (the status says
//! why) rather than clamped, as every op is.

use primer::prelude::*;
use th_edit::document::{Clip, ClipRef, Frame, Source, TrackKind, WorkingEdit};
use th_edit::ops::Op;

use super::{Editor, Message};
use crate::ladder::{Number, ladder};
use crate::look;

/// A clip field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClipField {
    FadeIn,
    FadeOut,
    Opacity,
}

/// What `field` of `clip` holds now: frames for a fade, a percentage for
/// an opacity (an audio clip's is 100). None: no such clip.
pub(crate) fn field_text(edit: &WorkingEdit, clip: ClipRef, field: ClipField) -> Option<String> {
    let (fade_in, fade_out, opacity) = numbers(edit, clip)?;
    Some(match field {
        ClipField::FadeIn => fade_in.to_string(),
        ClipField::FadeOut => fade_out.to_string(),
        ClipField::Opacity => percent(opacity),
    })
}

/// The op that setting `field` of `clip` to `text` makes: None when it is
/// what the clip has already; an error when `text` is no value of that
/// field. Whether it fits the clip is the op's to say.
pub(crate) fn field_op(edit: &WorkingEdit, clip: ClipRef, field: ClipField, text: &str) -> Result<Option<Op<Source>>, String> {
    let (fade_in, fade_out, opacity) = numbers(edit, clip).ok_or("that clip is gone")?;
    let text = text.trim();
    match field {
        ClipField::FadeIn | ClipField::FadeOut => {
            let frames: Frame =
                text.parse().ok().filter(|f| *f >= 0).ok_or_else(|| format!("{text:?}: a fade is a whole number of frames, 0 or more"))?;
            let (new_in, new_out) = if field == ClipField::FadeIn { (frames, fade_out) } else { (fade_in, frames) };
            Ok(((new_in, new_out) != (fade_in, fade_out)).then_some(Op::SetFades { clip, fade_in: new_in, fade_out: new_out }))
        }
        ClipField::Opacity => {
            if clip.track.kind != TrackKind::Video {
                return Err("only a video clip has an opacity".into());
            }
            let number: f64 = text
                .strip_suffix('%')
                .unwrap_or(text)
                .trim()
                .parse()
                .ok()
                .filter(|p: &f64| (0.0..=100.0).contains(p))
                .ok_or_else(|| format!("{text:?}: an opacity is a percentage, 0 to 100"))?;
            let new = number / 100.0;
            Ok((percent(new) != percent(opacity)).then_some(Op::SetOpacity { clip, opacity: new }))
        }
    }
}

/// `field` as a ladder number: its value is what the field shows (`typed`,
/// else the clip's own `shown`), its limits and default those of a fade
/// (whole frames, 0 or more, default 0) or an opacity (0 to 100 %, to a
/// tenth, default 100).
pub(crate) fn field_number(field: ClipField, typed: &str, shown: &str) -> Number {
    let parse = |t: &str| t.trim().strip_suffix('%').unwrap_or(t.trim()).trim().parse::<f64>().ok();
    let value = parse(typed).or_else(|| parse(shown)).unwrap_or(0.0);
    match field {
        ClipField::FadeIn | ClipField::FadeOut => Number { value, min: 0.0, max: 1_000_000.0, decimals: 0, default: 0.0 },
        ClipField::Opacity => Number { value, min: 0.0, max: 100.0, decimals: 1, default: 100.0 },
    }
}

/// A ladder value as `field`'s text.
pub(crate) fn field_value_text(field: ClipField, value: f64) -> String {
    match field {
        ClipField::FadeIn | ClipField::FadeOut => format!("{}", value.round() as Frame),
        ClipField::Opacity => percent(value / 100.0),
    }
}

/// An opacity as a percentage, to a tenth, without a trailing `.0`.
pub(crate) fn percent(opacity: f64) -> String {
    let tenths = (opacity * 1000.0).round() / 10.0;
    if tenths.fract() == 0.0 { format!("{tenths:.0}") } else { format!("{tenths:.1}") }
}

fn numbers(edit: &WorkingEdit, clip: ClipRef) -> Option<(Frame, Frame, f64)> {
    match clip.track.kind {
        TrackKind::Video => edit.video.get(clip.track.index)?.clips.get(clip.index).map(|c| (c.fade_in(), c.fade_out(), c.opacity())),
        TrackKind::Audio => edit.audio.get(clip.track.index)?.clips.get(clip.index).map(|c| (c.fade_in(), c.fade_out(), c.opacity())),
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

    /// The selected clip's fades and opacity, as fields; with no one clip
    /// selected, the same fields greyed and the hint to select one.
    pub(super) fn clip_fields(&self) -> Element<Message> {
        let clip = self.field_clip();
        let shown = |which: ClipField| -> FieldShown {
            let Some(clip) = clip else { return FieldShown::default() };
            let own = field_text(&self.doc.edit, clip, which).unwrap_or_default();
            let value = match &self.clip_draft {
                Some((c, f, typed)) if *c == clip && *f == which => typed.clone(),
                _ => own.clone(),
            };
            // An audio clip has no opacity: its field stays, greyed.
            let enabled = which != ClipField::Opacity || clip.track.kind == TrackKind::Video;
            FieldShown { value, shown: own, enabled }
        };
        let hint = if clip.is_some() { HINT_SELECTED } else { HINT_NONE };
        fields_row([shown(ClipField::FadeIn), shown(ClipField::FadeOut), shown(ClipField::Opacity)], hint)
    }
}

/// The hint at the row's end with one clip selected, and with none (or
/// several).
pub(crate) const HINT_SELECTED: &str = "Enter sets a field, middle-drag steps it";
pub(crate) const HINT_NONE: &str = "Select one clip to type its fades and opacity";

/// One clip field as the row shows it: the text in it (typed, else the
/// clip's), the clip's own value, and whether it takes typing.
#[derive(Debug, Clone, Default)]
pub(crate) struct FieldShown {
    pub value: String,
    pub shown: String,
    pub enabled: bool,
}

/// The fields row: fade in, fade out and opacity, always the same widgets
/// in the same places at [`CLIP_FIELDS_HEIGHT`](super::panels::CLIP_FIELDS_HEIGHT),
/// so selecting a clip, clearing the selection or dragging never moves the
/// tracks under it (2026-11-16: the row was swapped for a shorter line with
/// nothing selected, and the tracks jumped). A field that takes no typing
/// is greyed, with no value ladder.
pub(crate) fn fields_row(fields: [FieldShown; 3], hint: &str) -> Element<Message> {
    let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
    let [fade_in, fade_out, opacity] = fields;
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
        .push(label(hint));
    container(fields)
        .padding(Padding::from([3.0, 14.0]))
        .width(Length::Fill)
        .height(Length::fixed(super::panels::CLIP_FIELDS_HEIGHT))
        .align_y(Vertical::Center)
        .background(look::panel())
        .into()
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
        edit.video.push(Track { name: "V1".into(), clips: vec![v] });
        let a = AudioClip { at: 0, duration, source_in: 0, source, gain_db: 0.0, fade_in: fades.0, fade_out: fades.1 };
        edit.audio.push(Track { name: "A1".into(), clips: vec![a] });
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
    fn row_heights(fields: [FieldShown; 3], hint: &str, width: f32) -> (f32, f32) {
        use primer::widget::custom::{LayoutCtx, Limits, StateStore, Widget};
        let element = fields_row(fields, hint);
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
        /// The fields row is one height whatever is selected: one video clip,
        /// one audio clip (opacity greyed), none or several (all greyed, the
        /// hint to select one), whatever the values and the window's width;
        /// and what it holds fits inside it, so nothing is cut off.
        #[test]
        fn the_fields_row_never_changes_height(
            values in prop::collection::vec("[0-9]{0,6}", 3),
            selected in 0usize..3,
            width in 200f32..4000.0,
        ) {
            let fields = |enabled: [bool; 3]| -> [FieldShown; 3] {
                [shown(&values[0], enabled[0]), shown(&values[1], enabled[1]), shown(&values[2], enabled[2])]
            };
            let (none, none_inner) = row_heights([FieldShown::default(), FieldShown::default(), FieldShown::default()], HINT_NONE, width);
            let (h, inner) = match selected {
                0 => row_heights(fields([true; 3]), HINT_SELECTED, width),
                1 => row_heights(fields([true, true, false]), HINT_SELECTED, width),
                _ => row_heights(fields([false; 3]), HINT_NONE, width),
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
            for bad in ["", "x", "1.5", "3f", "--2"] {
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
            field in prop::sample::select(vec![ClipField::FadeIn, ClipField::FadeOut, ClipField::Opacity]),
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
            prop_assert_eq!(reset, if field == ClipField::Opacity { "100" } else { "0" });
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
}
