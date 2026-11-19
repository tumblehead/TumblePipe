//! How far a drag of a clip can go: to the end of its source's frames, and
//! never to an empty clip.
//!
//! A clip keeps its source and shows `source_in ..` of it for its length, so
//! a trim only moves those two numbers: the frames trimmed away are still
//! the source's, and trimming back out shows them again. What limits a trim,
//! a slip or a roll is what the source has ([`th_edit::ops::Sources`]): an
//! op that would show a frame its source has not is refused whole, never
//! clamped (`th_edit::ops`). So the drag is held here instead: the edge stops
//! where the footage ends ([`Stop::Footage`]) and the tracks say so, and
//! letting go makes an op the document takes.
//!
//! Up to 1.112.0 the drag went as far as the pointer, and one frame past the
//! footage refused the whole trim: a clip trimmed down by 25 frames and
//! dragged back out by 87 snapped back to its trimmed length, which read as
//! the trim having thrown its frames away (2026-11-18).

use th_edit::document::Frame;
use th_edit::ops::Available;

use super::Part;

/// The source frames a clip does not show: how many its source has before
/// its first and after its last.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Room {
    pub head: Frame,
    pub tail: Frame,
}

/// The room of a clip showing `duration` frames from `source_in` of a source
/// with `available`; None when it has any frame (a slug, a planned shot). A
/// clip already past what its source has (a shot shortened since) has no
/// room on that side.
pub fn room(available: Available, source_in: Frame, duration: Frame) -> Option<Room> {
    match available {
        Available::Any => None,
        Available::Nothing => Some(Room { head: 0, tail: 0 }),
        Available::Frames(first, last) => Some(Room { head: (source_in - first).max(0), tail: (last - (source_in + duration - 1)).max(0) }),
    }
}

/// What held a drag back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stop {
    /// The source has no more frames that way.
    Footage,
    /// A clip would be left with no frame.
    Length,
}

/// A clip as a drag sees it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub at: Frame,
    pub duration: Frame,
    /// None: its source has any frame, or is not known yet (the drag is
    /// not held; the document still judges the op).
    pub room: Option<Room>,
}

impl Span {
    fn end(&self) -> Frame {
        self.at + self.duration
    }
}

/// How far a drag went, and what stopped it short of the pointer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Reach {
    pub dx: Frame,
    pub stop: Option<Stop>,
}

/// The nearer of two limits; the footage's when they are the same.
fn nearer(a: Option<(Frame, Stop)>, b: Option<(Frame, Stop)>, lower: bool) -> Option<(Frame, Stop)> {
    match (a, b) {
        (Some(a), Some(b)) => {
            let a_nearer = if lower { a.0 > b.0 } else { a.0 < b.0 };
            Some(if a_nearer || (a.0 == b.0 && a.1 == Stop::Footage) { a } else { b })
        }
        (a, b) => a.or(b),
    }
}

/// A drag of `part` of `clip` by `dx` frames (positive: later), held to what
/// its source has and to clips of a frame at least. `before` and `after` are
/// the clips beside it on its track, for a roll (which changes the neighbour
/// too). A move and the fades are not held here.
pub fn reach(part: Part, clip: Span, before: Option<Span>, after: Option<Span>, dx: Frame) -> Reach {
    let footage = |frames: Frame| (frames, Stop::Footage);
    let length = |frames: Frame| (frames, Stop::Length);
    // A roll of the cut between `left` and `right`, which must touch (else
    // the document refuses it, and says why).
    let roll = |left: Span, right: Span| {
        if left.end() != right.at {
            return (None, None);
        }
        let low = nearer(Some(length(-(left.duration - 1))), right.room.map(|r| footage(-r.head)), true);
        let high = nearer(Some(length(right.duration - 1)), left.room.map(|r| footage(r.tail)), false);
        (low, high)
    };
    let (low, high) = match part {
        Part::Body | Part::FadeIn | Part::FadeOut => (None, None),
        Part::Head => (clip.room.map(|r| footage(-r.head)), Some(length(clip.duration - 1))),
        Part::Tail => (Some(length(-(clip.duration - 1))), clip.room.map(|r| footage(r.tail))),
        // Dragging right shows earlier source frames.
        Part::Slip => (clip.room.map(|r| footage(-r.tail)), clip.room.map(|r| footage(r.head))),
        Part::RollTail => after.map_or((None, None), |next| roll(clip, next)),
        Part::RollHead => before.map_or((None, None), |previous| roll(previous, clip)),
    };
    // Each limit lets the clip stay as it is: `low <= 0 <= high`.
    if let Some((limit, stop)) = low.filter(|l| dx < l.0) {
        return Reach { dx: limit, stop: Some(stop) };
    }
    if let Some((limit, stop)) = high.filter(|h| dx > h.0) {
        return Reach { dx: limit, stop: Some(stop) };
    }
    Reach { dx, stop: None }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{ClipRef, Source, Track, TrackRef, VersionRule, VideoClip, WorkingEdit};
    use th_edit::ops::{Op, Sources, apply};

    use crate::tracks::{ClipView, drag_op};

    /// Media item `m<first>_<last>` has frames first to last.
    struct Ranges;

    fn media(first: Frame, last: Frame) -> Source {
        Source::Media { item: format!("m{first}_{last}"), version: VersionRule::Latest }
    }

    fn frames_of(source: &Source) -> (Frame, Frame) {
        let Source::Media { item, .. } = source else { panic!("a media item") };
        let (first, last) = item[1..].split_once('_').expect("m<first>_<last>");
        (first.parse().unwrap(), last.parse().unwrap())
    }

    impl Sources<Source> for Ranges {
        fn available(&self, source: &Source) -> Available {
            let (first, last) = frames_of(source);
            Available::Frames(first, last)
        }
    }

    /// One source's frames, and the part of them a clip shows: (first, last,
    /// source_in, duration).
    fn shown() -> impl Strategy<Value = (Frame, Frame, Frame, Frame)> {
        (900i64..1100, 1i64..200).prop_flat_map(|(first, count)| {
            let last = first + count - 1;
            (Just(first), Just(last), first..=last)
                .prop_flat_map(|(first, last, source_in)| (Just(first), Just(last), Just(source_in), 1..=(last - source_in + 1)))
        })
    }

    /// A track of clips touching one another from frame `at`, each showing
    /// part of a source of its own.
    fn track(at: Frame, clips: &[(Frame, Frame, Frame, Frame)]) -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        let mut next = at;
        let clips = clips
            .iter()
            .map(|&(first, last, source_in, duration)| {
                let clip = VideoClip {
                    at: next,
                    duration,
                    source_in,
                    source: media(first, last),
                    fade_in: 0,
                    fade_out: 0,
                    opacity: 1.0,
                    transform: Default::default(),
                };
                next += duration;
                clip
            })
            .collect();
        edit.video.push(Track { name: "V1".into(), clips });
        edit
    }

    /// The track's clips as the tracks show them, each with its room.
    fn views(edit: &WorkingEdit) -> Vec<ClipView> {
        edit.video[0]
            .clips
            .iter()
            .map(|c| ClipView {
                at: c.at,
                duration: c.duration,
                fade_in: c.fade_in,
                fade_out: c.fade_out,
                name: String::new(),
                detail: String::new(),
                thumbnail: None,
                selected: false,
                missing: false,
                department: None,
                preview: None,
                room: room(Ranges.available(&c.source), c.source_in, c.duration),
            })
            .collect()
    }

    fn part() -> impl Strategy<Value = Part> {
        prop_oneof![Just(Part::Head), Just(Part::Tail), Just(Part::Slip), Just(Part::RollHead), Just(Part::RollTail)]
    }

    const V1: TrackRef = TrackRef { kind: th_edit::document::TrackKind::Video, index: 0 };

    proptest! {
        /// However far an edge, a slip or a roll is dragged, the op it makes
        /// is one the document takes: no drag is refused for going past the
        /// footage or leaving a clip empty. (Up to 1.112.0 the op carried
        /// the pointer's distance, and one frame too many refused it all.)
        #[test]
        fn no_drag_is_refused_for_going_too_far(
            at in 0i64..50,
            clips in prop::collection::vec(shown(), 1..4),
            index in 0usize..4,
            part in part(),
            dx in -600i64..600,
        ) {
            let edit = track(at, &clips);
            let index = index % clips.len();
            let (op, reach) = drag_op(part, V1, &views(&edit), index, dx, None);
            prop_assert!(reach.dx.abs() <= dx.abs() && reach.dx * dx >= 0, "a drag never goes further than, or against, the pointer");
            prop_assert_eq!(reach.stop.is_some(), reach.dx != dx);
            // A roll with no clip the other side of the cut is not a roll:
            // the document says so.
            let lonely = (part == Part::RollHead && index == 0) || (part == Part::RollTail && index + 1 == clips.len());
            match op {
                Some(op) if !lonely => {
                    let done = apply(&edit, &op, &Ranges);
                    prop_assert!(done.is_ok(), "{:?} dragged {} made {:?}: {:?}", part, dx, op, done.err());
                }
                Some(_) => {}
                // Nothing to do: held at where it is.
                None => prop_assert!(reach.dx == 0 || lonely),
            }
        }

        /// Trimming is not destructive: a clip trimmed down at either end
        /// and dragged back out, however far, shows every frame it showed
        /// before, and as many more as its source has that way; the edge
        /// stops at the end of the footage and says so.
        #[test]
        fn a_trimmed_clip_drags_back_out_to_all_its_footage(
            (first, last, source_in, duration) in shown().prop_filter("two frames to trim one", |s| s.3 >= 2),
            cut in 1i64..200,
            past in 0i64..400,
            tail in any::<bool>(),
        ) {
            let edit = track(0, &[(first, last, source_in, duration)]);
            let clip = ClipRef { track: V1, index: 0 };
            let cut = cut.min(duration - 1);
            let (part, inwards) = if tail { (Part::Tail, -cut) } else { (Part::Head, cut) };
            let (op, reach) = drag_op(part, V1, &views(&edit), 0, inwards, None);
            prop_assert_eq!(reach, Reach { dx: inwards, stop: None });
            let trimmed = apply(&edit, &op.expect("a trim"), &Ranges).expect("trimmed down");
            prop_assert_eq!(trimmed.video[0].clips[0].duration, duration - cut);

            // Back out: by what was cut, and `past` frames more.
            let outwards = -inwards.signum() * (cut + past);
            let (op, reach) = drag_op(part, V1, &views(&trimmed), 0, outwards, None);
            let back = apply(&trimmed, &op.expect("a trim"), &Ranges).expect("dragged back out");
            let c = &back.video[0].clips[0];
            let (now_first, now_last) = (c.source_in, c.source_in + c.duration - 1);
            prop_assert!(now_first <= source_in && now_last >= source_in + duration - 1, "frames it showed are gone");
            prop_assert!(now_first >= first && now_last <= last);
            // It shows `past` more that way, or all the source has.
            let (wanted, got, end) = if tail {
                (source_in + duration - 1 + past, now_last, last)
            } else {
                (-(source_in - past), -now_first, -first)
            };
            prop_assert_eq!(got, wanted.min(end));
            prop_assert_eq!(reach.stop, (wanted > end).then_some(Stop::Footage));
            prop_assert_eq!(apply(&back, &Op::Trim { clip, edge: if tail { th_edit::ops::Edge::Tail } else { th_edit::ops::Edge::Head }, delta: if tail { 1 } else { -1 } }, &Ranges).is_err(), got == end, "the document's own limit is where the drag stops");
        }

        /// An edge dragged in by `n` frames and back out by `n` is the edit
        /// it was, to the byte: the clip shows the frames it showed, where
        /// it sat in the picture (its transform), at its opacity, with its
        /// fades. The trimmed clip between has them too.
        #[test]
        fn an_edge_dragged_in_and_back_out_is_the_edit_it_was(
            (first, last, source_in, duration) in shown().prop_filter("two frames to trim one", |s| s.3 >= 2),
            n in 1i64..200,
            tail in any::<bool>(),
            moved in prop::option::of((-10f64..=10.0, -10f64..=10.0, 0.01f64..=100.0, -3600f64..=3600.0)),
            opacity in prop::sample::select(vec![1.0f64, 0.5, 0.0]),
            fades in (0i64..5, 0i64..5),
        ) {
            let n = n.min(duration - 1);
            let mut edit = track(0, &[(first, last, source_in, duration)]);
            let clip = &mut edit.video[0].clips[0];
            if let Some((x, y, scale, rotation)) = moved {
                clip.transform = th_edit::Transform { x, y, scale, rotation };
            }
            clip.opacity = opacity;
            // Fades the trimmed clip still has the frames for.
            let left = duration - n;
            (clip.fade_in, clip.fade_out) = (fades.0.min(left / 2), fades.1.min(left - left / 2));
            let (transform, source) = (clip.transform, clip.source.clone());
            prop_assert_eq!(edit.problem(), None);
            let (part, inwards) = if tail { (Part::Tail, -n) } else { (Part::Head, n) };
            let (op, reach) = drag_op(part, V1, &views(&edit), 0, inwards, None);
            prop_assert_eq!(reach, Reach { dx: inwards, stop: None });
            let trimmed = apply(&edit, &op.expect("a trim"), &Ranges).expect("trimmed down");
            let short = &trimmed.video[0].clips[0];
            prop_assert_eq!((short.duration, short.transform, short.opacity, &short.source), (left, transform, opacity, &source));
            prop_assert_eq!((short.fade_in, short.fade_out), (edit.video[0].clips[0].fade_in, edit.video[0].clips[0].fade_out));
            let (op, reach) = drag_op(part, V1, &views(&trimmed), 0, -inwards, None);
            prop_assert_eq!(reach, Reach { dx: -inwards, stop: None });
            let back = apply(&trimmed, &op.expect("a trim"), &Ranges).expect("dragged back out");
            prop_assert_eq!(back.to_json(), edit.to_json());
        }

        /// A source with any frame (a slug, a planned shot) holds no drag
        /// but at a clip of one frame.
        #[test]
        fn a_source_with_any_frame_only_keeps_a_frame(duration in 1i64..300, dx in -600i64..600, tail in any::<bool>()) {
            let clip = Span { at: 10, duration, room: room(Available::Any, 0, duration) };
            let reach = reach(if tail { Part::Tail } else { Part::Head }, clip, None, None, dx);
            let longer = if tail { reach.dx } else { -reach.dx };
            prop_assert_eq!(duration + longer, (duration + if tail { dx } else { -dx }).max(1));
            prop_assert!(reach.stop != Some(Stop::Footage));
        }
    }

    #[test]
    fn room_is_what_the_source_has_either_side() {
        assert_eq!(room(Available::Frames(1001, 1100), 1011, 50), Some(Room { head: 10, tail: 40 }));
        assert_eq!(room(Available::Frames(1001, 1100), 1001, 100), Some(Room { head: 0, tail: 0 }));
        // A shot shortened since: no room, not less than none.
        assert_eq!(room(Available::Frames(1010, 1020), 1001, 100), Some(Room { head: 0, tail: 0 }));
        assert_eq!(room(Available::Any, 0, 10), None);
        assert_eq!(room(Available::Nothing, 0, 10), Some(Room { head: 0, tail: 0 }));
    }

    /// Remy's drag (2026-11-18): a shot's clip trimmed down by 25 frames,
    /// then its tail dragged 87 frames out. It comes back whole and stops.
    #[test]
    fn the_reported_drag_brings_the_clip_back() {
        let edit = track(0, &[(1001, 1048, 1001, 48)]);
        let (op, _) = drag_op(Part::Tail, V1, &views(&edit), 0, -25, None);
        let trimmed = apply(&edit, &op.unwrap(), &Ranges).unwrap();
        let (op, reach) = drag_op(Part::Tail, V1, &views(&trimmed), 0, 87, None);
        assert_eq!(reach, Reach { dx: 25, stop: Some(Stop::Footage) });
        assert_eq!(apply(&trimmed, &op.unwrap(), &Ranges).unwrap(), edit);
        // As the tracks did it before: the pointer's 87 frames, refused.
        let old = Op::Trim { clip: ClipRef { track: V1, index: 0 }, edge: th_edit::ops::Edge::Tail, delta: 87 };
        assert!(apply(&trimmed, &old, &Ranges).is_err());
    }
}
