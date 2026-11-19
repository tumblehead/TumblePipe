//! Dragging out of the bins onto the timeline: a shot, a media item,
//! several picked together, or a sequence's name for all its shots.
//!
//! A press on a row takes hold of what the row stands for ([`Drag::Held`]);
//! once the pointer has moved [`THRESHOLD`] pixels it is carried
//! ([`Drag::Carried`]), and where it is over the tracks ([`Lanes::over`])
//! and which key is held decide where it would land ([`landing`]): the same
//! ops Insert and Overlay make (`bins::put_run`), started at the frame and
//! on the track under the pointer instead of at the playhead.
//!
//! - Let go plain, it is **laid over** the cut: on the track under the
//!   pointer when that has room, else the next one up with room, else a new
//!   track on top. Nothing that is there moves.
//! - Let go with **Ctrl** held (Cmd on macOS), it is **inserted** on the
//!   track under the pointer, and what follows moves on, on every track.
//! - On the spare layer (the empty row over the top video track) it makes
//!   that track, in the same undo step.
//!
//! The placeholder drawn while carrying is read off those same ops
//! ([`spans`]), so it is where the clips go. A drop that cannot land (a
//! picture on an audio track, sound on a video track, a read-only edit, or
//! anything `th_edit` refuses) is known while carrying, and shown.
//!
//! Sound alone goes where Overlay puts it: the first audio track with room.

use primer::keyboard::Modifiers;
use primer::prelude::{Point, Rectangle, Size};
use th_edit::document::{Frame, Source, TrackKind, TrackRef, WorkingEdit};
use th_edit::ops::{NewClip, Op, Refused, Sources};

use crate::bins::{self, Clips, Target};
use crate::tracks::{HEADER, RULER};

/// How far the pointer moves with a row held before it is a drag, in
/// pixels: under it, the press was a click.
pub const THRESHOLD: f32 = 5.0;

/// Whether a drop with `modifiers` held inserts (rippling); else it is
/// laid over what is there.
pub fn inserts(modifiers: Modifiers) -> bool {
    modifiers.command()
}

/// The name of the key that makes a drop insert, for the words on screen.
pub fn insert_key() -> &'static str {
    if cfg!(target_os = "macos") { "Cmd" } else { "Ctrl" }
}

/// Where the tracks' rows are, as the timeline draws them: each row's
/// height and track, top row first (the spare layer, when there is one, is
/// a row like any other, of the video track one past the top), and the
/// frames its width spans.
#[derive(Debug, Clone, PartialEq)]
pub struct Lanes {
    pub rows: Vec<(TrackRef, f32)>,
    pub total: usize,
}

/// The pointer over the tracks: the frame, and the row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Over {
    pub frame: Frame,
    pub row: usize,
}

impl Lanes {
    fn span(&self, width: f32) -> f32 {
        (width - HEADER).max(1.0)
    }

    /// What local `point` of tracks `width` wide is over: None on the
    /// ruler, on the rows' names, under the last row, or outside.
    pub fn over(&self, point: Point, width: f32) -> Option<Over> {
        if point.x < HEADER || point.x >= width || point.y < RULER {
            return None;
        }
        let mut top = RULER;
        let row = self.rows.iter().position(|&(_, height)| {
            top += height;
            point.y < top
        })?;
        let frame = (f64::from(point.x - HEADER) / f64::from(self.span(width)) * self.total.max(1) as f64).floor() as Frame;
        Some(Over { frame: frame.max(0), row })
    }

    /// Where frames `at .. at + duration` of `track` are drawn, in local
    /// pixels; None when the track has no row.
    pub fn rect(&self, track: TrackRef, at: Frame, duration: Frame, width: f32) -> Option<Rectangle> {
        let row = self.rows.iter().position(|&(t, _)| t == track)?;
        let top = RULER + self.rows[..row].iter().map(|r| r.1).sum::<f32>();
        let x = |frame: Frame| HEADER + frame as f32 / self.total.max(1) as f32 * self.span(width);
        Some(Rectangle::new(Point::new(x(at), top), Size::new((x(at + duration) - x(at)).max(2.0), self.rows[row].1)))
    }
}

/// A drag out of the bins, as far as it has got.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum Drag {
    #[default]
    Idle,
    /// A row is pressed: what it stands for, and where the press was.
    Held { target: Target, from: Point },
    /// It is being carried: the pointer, what it is over on the tracks,
    /// and whether letting go inserts.
    Carried { target: Target, at: Point, over: Option<Over>, insert: bool },
}

impl Drag {
    pub fn carried(&self) -> Option<&Target> {
        match self {
            Drag::Carried { target, .. } => Some(target),
            _ => None,
        }
    }

    /// The pointer moved to `to` with `insert` held: a held row is carried
    /// once it is [`THRESHOLD`] from its press, and a carried one follows.
    /// What it is over is the tracks' to say ([`Drag::over`]).
    pub fn moved(self, to: Point, insert: bool) -> Drag {
        match self {
            Drag::Held { target, from } if (to.x - from.x).hypot(to.y - from.y) >= THRESHOLD => {
                Drag::Carried { target, at: to, over: None, insert }
            }
            Drag::Carried { target, over, .. } => Drag::Carried { target, at: to, over, insert },
            other => other,
        }
    }

    /// The tracks say what the pointer is over.
    pub fn over(self, now: Option<Over>) -> Drag {
        match self {
            Drag::Carried { target, at, insert, .. } => Drag::Carried { target, at, over: now, insert },
            other => other,
        }
    }
}

/// What a drop does.
#[derive(Debug, Clone, PartialEq)]
pub struct Landing {
    pub ops: Vec<Op<Source>>,
    /// Where the playhead goes after: the end of what was put.
    pub end: Option<Frame>,
}

pub const READ_ONLY: &str = "This edit is read-only: nothing can be dropped into it";
pub const PICTURE_ON_AUDIO: &str = "A picture goes on a video track";
pub const SOUND_ON_VIDEO: &str = "Sound goes on an audio track";
pub const NOTHING: &str = "There is nothing to put";

/// What dropping the items `makers` give the clips of does to `edit`, let
/// go at `frame` on `track` (a row's: the video track one past the top is
/// the spare layer), inserted or laid over. Refused, with why, when it
/// cannot land; `sources` say which frames each source has, as for any op.
pub fn landing<F: Fn(Frame) -> Clips>(
    edit: &WorkingEdit,
    writable: bool,
    track: TrackRef,
    frame: Frame,
    insert: bool,
    makers: &[F],
    sources: &impl Sources<Source>,
) -> Result<Landing, Refused> {
    let refuse = |why: &str| Err(Refused(why.to_owned()));
    if !writable {
        return refuse(READ_ONLY);
    }
    let at = frame.max(0);
    // What is carried, as it is at this frame: any picture, or sound alone.
    let kinds: Vec<Clips> = makers.iter().map(|make| make(at)).filter(|c| c.0.is_some() || c.1.is_some()).collect();
    if kinds.is_empty() {
        return refuse(NOTHING);
    }
    let picture = kinds.iter().any(|c| c.0.is_some());
    match (track.kind, picture) {
        (TrackKind::Audio, true) => return refuse(PICTURE_ON_AUDIO),
        (TrackKind::Video, false) => return refuse(SOUND_ON_VIDEO),
        _ => {}
    }
    let spare = track.kind == TrackKind::Video && track.index >= edit.video.len();
    let (ops, end) = if insert && spare {
        // An insert goes on a track that is there: the spare layer is made
        // first, in the same step.
        let add = Op::AddTrack { kind: TrackKind::Video, name: format!("V{}", edit.video.len() + 1) };
        let with_track = th_edit::ops::apply(edit, &add, sources)?;
        let (ops, end) = bins::put_run(&with_track, at, edit.video.len(), false, makers, sources)?;
        (std::iter::once(add).chain(ops).collect(), end)
    } else {
        let from = if track.kind == TrackKind::Video { track.index } else { 0 };
        bins::put_run(edit, at, from, !insert, makers, sources)?
    };
    Ok(Landing { ops, end })
}

/// Where `ops` put clips: (track, frame, length, what of), in the
/// order they go in. A track the ops add is the one past those `edit` has.
pub fn spans(ops: &[Op<Source>]) -> Vec<(TrackRef, Frame, Frame, Source)> {
    ops.iter()
        .filter_map(|op| match op {
            Op::Insert { track, clip } | Op::Place { track, clip } => Some((*track, clip)),
            _ => None,
        })
        .map(|(track, clip)| match clip {
            NewClip::Video(v) => (track, v.at, v.duration, v.source.clone()),
            NewClip::Audio(a) => (track, a.at, a.duration, a.source.clone()),
        })
        .collect()
}

/// A source whose every frame is there: what a drop is tried with while it
/// is carried. The bin made the clips from its own listing of each shot's
/// and item's frames, so only a listing gone stale is refused later, by the
/// drop itself, which asks the project.
pub struct AnyFrames;

impl Sources<Source> for AnyFrames {
    fn available(&self, _: &Source) -> th_edit::ops::Available {
        th_edit::ops::Available::Any
    }
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;
    use th_edit::document::{AudioClip, Rule, Track, VideoClip};

    use super::*;

    fn lanes() -> impl Strategy<Value = Lanes> {
        (prop::collection::vec(20u8..90, 1..6), 1usize..4, 1usize..400).prop_map(|(heights, video, total)| {
            let video = video.min(heights.len());
            let rows = heights
                .iter()
                .enumerate()
                .map(|(i, &h)| {
                    let track = if i < video { TrackRef::video(video - 1 - i) } else { TrackRef::audio(i - video) };
                    (track, f32::from(h))
                })
                .collect();
            Lanes { rows, total }
        })
    }

    proptest! {
        /// A point over the tracks is over the row whose band holds it and
        /// the frame whose pixels hold it; the ruler, the names and below
        /// the rows are over nothing. And a frame's rectangle is where that
        /// says: every point inside it is over that frame and row.
        #[test]
        fn a_point_is_over_the_row_and_frame_drawn_there(lanes in lanes(), px in 1u8..9, row in any::<prop::sample::Index>(), frame in any::<prop::sample::Index>(), within in 0.05f32..0.95) {
            let width = HEADER + f32::from(px) * lanes.total as f32;
            let row = row.index(lanes.rows.len());
            let frame = frame.index(lanes.total) as Frame;
            // The test's own geometry: rows stacked under the ruler, a
            // frame `px` wide.
            let top: f32 = RULER + lanes.rows[..row].iter().map(|r| r.1).sum::<f32>();
            let point = Point::new(HEADER + f32::from(px) * (frame as f32 + within), top + within * lanes.rows[row].1);
            prop_assert_eq!(lanes.over(point, width), Some(Over { frame, row }));
            prop_assert_eq!(lanes.over(Point::new(HEADER - 1.0, point.y), width), None, "the names");
            prop_assert_eq!(lanes.over(Point::new(point.x, RULER - 1.0), width), None, "the ruler");
            prop_assert_eq!(lanes.over(Point::new(width + 1.0, point.y), width), None);
            let below = RULER + lanes.rows.iter().map(|r| r.1).sum::<f32>() + 1.0;
            prop_assert_eq!(lanes.over(Point::new(point.x, below), width), None, "under the rows");
            let rect = lanes.rect(lanes.rows[row].0, frame, 1, width).unwrap();
            prop_assert!((rect.x - (HEADER + f32::from(px) * frame as f32)).abs() < 0.01 && (rect.y - top).abs() < 0.01, "{:?}", rect);
            prop_assert!((rect.height - lanes.rows[row].1).abs() < 0.01);
            prop_assert!(rect.contains(point), "{:?} does not hold {:?}", rect, point);
            prop_assert_eq!(lanes.rect(TrackRef::audio(99), frame, 1, width), None, "a track without a row");
        }

        /// A press is a click until the pointer has gone the threshold from
        /// it; from then it is carried, wherever it goes, and keeps what it
        /// was over until the tracks say otherwise.
        #[test]
        fn a_held_row_is_carried_once_it_has_moved_far_enough(
            from in (0f32..500.0, 0f32..500.0), steps in prop::collection::vec((-9f32..9.0, -9f32..9.0, any::<bool>()), 1..12),
        ) {
            let target = Target::Item(3);
            let from = Point::new(from.0, from.1);
            let mut drag = Drag::Held { target: target.clone(), from };
            let (mut at, mut far) = (from, false);
            for (dx, dy, insert) in steps {
                at = Point::new(at.x + dx, at.y + dy);
                // The oracle: Euclid, once.
                far = far || ((at.x - from.x).powi(2) + (at.y - from.y).powi(2)).sqrt() >= THRESHOLD;
                drag = drag.moved(at, insert);
                if far {
                    prop_assert_eq!(&drag, &Drag::Carried { target: target.clone(), at, over: None, insert });
                    prop_assert_eq!(drag.carried(), Some(&target));
                } else {
                    prop_assert_eq!(&drag, &Drag::Held { target: target.clone(), from });
                    prop_assert_eq!(drag.carried(), None);
                }
            }
            let over = Some(Over { frame: 4, row: 1 });
            let told = drag.clone().over(over);
            if far {
                prop_assert_eq!(&told, &Drag::Carried { target: target.clone(), at, over, insert: matches!(drag, Drag::Carried { insert: true, .. }) });
                prop_assert_eq!(told.moved(Point::new(1.0, 1.0), true), Drag::Carried { target, at: Point::new(1.0, 1.0), over, insert: true });
            } else {
                prop_assert_eq!(told, drag);
            }
            prop_assert_eq!(Drag::Idle.moved(at, true).over(over), Drag::Idle);
        }
    }

    fn slug(text: &str) -> Source {
        Source::Slug { text: text.into() }
    }

    /// An edit of video tracks `video` and audio tracks `audio`, each a
    /// list of (gap before, length).
    fn edit_of(video: &[Vec<(Frame, Frame)>], audio: &[Vec<(Frame, Frame)>]) -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
        for (index, spans) in video.iter().enumerate() {
            let mut at = 0;
            let clips = spans
                .iter()
                .map(|&(gap, length)| {
                    at += gap;
                    let clip = VideoClip::new(at, length, 0, slug(&format!("v{index}@{at}")));
                    at += length;
                    clip
                })
                .collect();
            edit.video.push(Track { name: format!("V{}", index + 1), clips, color: None });
        }
        for (index, spans) in audio.iter().enumerate() {
            let mut at = 0;
            let clips = spans
                .iter()
                .map(|&(gap, length)| {
                    at += gap;
                    let clip = AudioClip {
                        at,
                        duration: length,
                        source_in: 0,
                        source: slug(&format!("a{index}@{at}")),
                        gain_db: 0.0,
                        fade_in: 0,
                        fade_out: 0,
                    };
                    at += length;
                    clip
                })
                .collect();
            edit.audio.push(Track { name: format!("A{}", index + 1), clips, color: None });
        }
        edit
    }

    fn track_spans() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((0i64..12, 1i64..20), 0..4)
    }

    /// (track kind is video, index, at, length, name) of every clip.
    type Known = (bool, usize, Frame, Frame, String);

    /// A source in the tests' own words.
    fn label(source: &Source) -> String {
        match source {
            Source::Shot { shot, .. } => shot.clone(),
            Source::Media { item, .. } => item.clone(),
            Source::Slug { text } => text.clone(),
        }
    }

    fn known(edit: &WorkingEdit) -> Vec<Known> {
        let mut out = Vec::new();
        for (i, t) in edit.video.iter().enumerate() {
            out.extend(t.clips.iter().map(|c| (true, i, c.at, c.duration, label(&c.source))));
        }
        for (i, t) in edit.audio.iter().enumerate() {
            out.extend(t.clips.iter().map(|c| (false, i, c.at, c.duration, label(&c.source))));
        }
        out.sort();
        out
    }

    fn apply_all(edit: &WorkingEdit, ops: &[Op<Source>]) -> WorkingEdit {
        ops.iter().fold(edit.clone(), |edit, op| th_edit::ops::apply(&edit, op, &AnyFrames).expect("the landing's own ops"))
    }

    /// What is carried: each a length, and whether it is sound alone.
    fn makers(items: &[(Frame, bool)]) -> Vec<impl Fn(Frame) -> Clips + '_> {
        items
            .iter()
            .enumerate()
            .map(|(i, &(length, sound))| {
                move |at: Frame| -> Clips {
                    let source = Source::Shot { shot: format!("entity:/shots/new/{i}"), take: Rule::best() };
                    if sound {
                        (None, Some(AudioClip { at, duration: length, source_in: 0, source, gain_db: 0.0, fade_in: 0, fade_out: 0 }))
                    } else {
                        (Some(VideoClip::new(at, length, 1001, source)), None)
                    }
                }
            })
            .collect()
    }

    proptest! {
        /// A drop of pictures on a video row (the spare layer included):
        /// every item is in the cut once, back to back from the frame under
        /// the pointer, on one track; laid over, nothing that was there
        /// moved and the track is the one under the pointer when that has
        /// room, else the first above with room, else a new one on top;
        /// inserted, it is on the track under the pointer (the new one, on
        /// the spare layer) and everything else moved as one insert of the
        /// run's length moves it. The placeholder is where the clips went.
        #[test]
        fn a_drop_lands_under_the_pointer_and_the_placeholder_is_where_it_went(
            video in prop::collection::vec(track_spans(), 1..4), audio in prop::collection::vec(track_spans(), 0..3),
            lengths in prop::collection::vec(1i64..15, 1..5), row in any::<prop::sample::Index>(), frame in 0i64..80, insert in any::<bool>(),
        ) {
            let edit = edit_of(&video, &audio);
            let index = row.index(video.len() + 1);
            let track = TrackRef::video(index);
            let items: Vec<(Frame, bool)> = lengths.iter().map(|&l| (l, false)).collect();
            let landing = landing(&edit, true, track, frame, insert, &makers(&items), &AnyFrames).unwrap();
            let after = apply_all(&edit, &landing.ops);
            prop_assert_eq!(after.problem(), None);
            let total: Frame = lengths.iter().sum();
            prop_assert_eq!(landing.end, Some(frame + total));
            let new: Vec<Known> = known(&after).into_iter().filter(|c| c.4.contains("new")).collect();
            let mut by_frame = new.clone();
            by_frame.sort_by_key(|c| c.2);
            prop_assert_eq!(by_frame.len(), lengths.len(), "each item once");
            let on = by_frame[0].1;
            let mut next = frame;
            for (clip, &length) in by_frame.iter().zip(&lengths) {
                prop_assert_eq!((clip.0, clip.1, clip.2, clip.3), (true, on, next, length), "back to back on one track, in order");
                next += length;
            }
            // The placeholder: exactly where they are.
            let mut shown: Vec<Known> = spans(&landing.ops).into_iter().map(|(t, at, d, source)| (t.kind == TrackKind::Video, t.index, at, d, label(&source))).collect();
            shown.sort();
            prop_assert_eq!(&shown, &new);
            let old: Vec<Known> = known(&after).into_iter().filter(|c| !c.4.contains("new")).collect();
            let free = |i: usize| video.get(i).is_none_or(|_| edit.spans(TrackRef::video(i)).iter().all(|&(a, e)| e <= frame || a >= frame + total));
            if insert {
                prop_assert_eq!(on, index, "an insert goes on the row under the pointer");
                prop_assert_eq!(after.video.len(), video.len().max(index + 1));
                let one = Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(VideoClip::new(frame, total, 0, slug("new"))) };
                let single = th_edit::ops::apply(&edit, &one, &AnyFrames).unwrap();
                let rest: Vec<Known> = known(&single).into_iter().filter(|c| !c.4.contains("new")).collect();
                prop_assert_eq!(&old, &rest, "what was there moved otherwise than one insert moves it");
            } else {
                prop_assert_eq!(&old, &known(&edit), "an overlay moved what was there");
                let expected = (index..=video.len()).find(|&i| free(i)).unwrap();
                prop_assert_eq!(on, expected, "the row under the pointer, else the first above with room");
                prop_assert_eq!(after.video.len(), video.len().max(expected + 1));
            }
        }

        /// What cannot land is refused before anything is made: a picture
        /// on an audio row, sound alone on a video row, anything in a
        /// read-only edit, and nothing at all. Sound alone on an audio row
        /// lands on an audio track and leaves the pictures alone.
        #[test]
        fn a_drop_that_cannot_land_is_refused_with_why(
            video in prop::collection::vec(track_spans(), 1..3), audio in prop::collection::vec(track_spans(), 1..3),
            items in prop::collection::vec((1i64..15, any::<bool>()), 1..4), row in any::<prop::sample::Index>(), on_audio in any::<bool>(),
            frame in 0i64..60, insert in any::<bool>(),
        ) {
            let edit = edit_of(&video, &audio);
            let track = if on_audio { TrackRef::audio(row.index(audio.len())) } else { TrackRef::video(row.index(video.len() + 1)) };
            let makers = makers(&items);
            let picture = items.iter().any(|i| !i.1);
            let result = landing(&edit, true, track, frame, insert, &makers, &AnyFrames);
            match (on_audio, picture) {
                (true, true) => prop_assert_eq!(result, Err(Refused(PICTURE_ON_AUDIO.into()))),
                (false, false) => prop_assert_eq!(result, Err(Refused(SOUND_ON_VIDEO.into()))),
                (true, false) => {
                    let after = apply_all(&edit, &result.unwrap().ops);
                    prop_assert_eq!(after.problem(), None);
                    if !insert {
                        prop_assert_eq!(&after.video, &edit.video, "sound laid over moved the pictures");
                    }
                    let new = known(&after).into_iter().filter(|c| c.4.contains("new")).count();
                    prop_assert_eq!(new, items.len());
                    prop_assert!(known(&after).iter().filter(|c| c.4.contains("new")).all(|c| !c.0), "sound on a video track");
                }
                (false, true) => prop_assert!(result.is_ok()),
            }
            prop_assert_eq!(landing(&edit, false, track, frame, insert, &makers, &AnyFrames), Err(Refused(READ_ONLY.into())));
            let none: Vec<fn(Frame) -> Clips> = Vec::new();
            prop_assert_eq!(landing(&edit, true, track, frame, insert, &none, &AnyFrames), Err(Refused(NOTHING.into())));
        }
    }

    #[test]
    fn ctrl_or_cmd_inserts_and_nothing_else_does() {
        let command = if cfg!(target_os = "macos") {
            Modifiers { logo: true, ..Modifiers::NONE }
        } else {
            Modifiers { control: true, ..Modifiers::NONE }
        };
        assert!(inserts(command));
        assert!(!inserts(Modifiers::NONE));
        assert!(!inserts(Modifiers { shift: true, alt: true, ..Modifiers::NONE }));
        assert_eq!(insert_key(), if cfg!(target_os = "macos") { "Cmd" } else { "Ctrl" });
    }
}
