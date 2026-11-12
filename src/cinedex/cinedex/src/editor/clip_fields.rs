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
    /// selected, what selecting one shows.
    pub(super) fn clip_fields(&self) -> Element<Message> {
        let label = |w: &str| text(w.to_owned()).font_size(12.0).color(look::quiet());
        let mut fields = row().spacing(8.0).align_y(Vertical::Center);
        match self.field_clip() {
            None => fields = fields.push(label("Select one clip to type its fades and opacity")),
            Some(clip) => {
                let field = |id: &'static str, which: ClipField| {
                    let value = match &self.clip_draft {
                        Some((c, f, typed)) if *c == clip && *f == which => typed.clone(),
                        _ => field_text(&self.doc.edit, clip, which).unwrap_or_default(),
                    };
                    text_input(Id::new(id), value)
                        .on_input(move |s| Message::ClipField(which, s))
                        .on_submit(move || Message::ClipFieldSet(which))
                        .font_size(12.0)
                        .width(Length::fixed(56.0))
                };
                fields = fields
                    .push(label("Fade in"))
                    .push(field("fade in field", ClipField::FadeIn))
                    .push(label("Fade out"))
                    .push(field("fade out field", ClipField::FadeOut))
                    .push(label("frames"));
                if clip.track.kind == TrackKind::Video {
                    fields = fields
                        .push(space(Length::fixed(12.0), Length::Shrink))
                        .push(label("Opacity"))
                        .push(field("opacity field", ClipField::Opacity))
                        .push(label("%"));
                }
                fields = fields.push(space(Length::fixed(12.0), Length::Shrink)).push(label("Enter sets a field"));
            }
        }
        container(fields).padding(Padding::from([3.0, 14.0])).width(Length::Fill).background(look::panel()).into()
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
        edit.video.push(Track { name: "V1".into(), clips: vec![v] });
        let a = AudioClip { at: 0, duration, source_in: 0, source, gain_db: 0.0, fade_in: fades.0, fade_out: fades.1 };
        edit.audio.push(Track { name: "A1".into(), clips: vec![a] });
        edit
    }

    proptest! {
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
