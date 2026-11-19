//! Which clip of an edit a clip of the edit before it is.
//!
//! A clip has no name of its own: it is named by its track and its place
//! in it ([`ClipRef`]), and an edit changes those (a clip moved past
//! another, one taken out before it, a track put above it). What holds on
//! to a clip across an edit, an undo or a redo (a selection) asks
//! [`followed`] where it is now. The answer is that clip, or None; never
//! another clip that looks different or lies elsewhere:
//!
//! 1. The clip is told by what it is apart from where it starts: its
//!    source, source frames, length, fades, opacity, transform and gain.
//!    When one clip of the edit before is like that and one of the edit
//!    after, that is it, wherever it went. Unless another clip of the same
//!    source, holding all of its source frames, is itself no longer as it
//!    was: a ripple delete can take the clip away and cut that other one
//!    down to its very look, which the two edits cannot tell from the
//!    clip having stayed. Then it is not followed. Nor is it when a part
//!    of it lies where it lay: an insert cut it there, and the clip like
//!    it is the new one.
//! 2. When several clips are alike (two cuts of one shot with the same
//!    frames), it is followed only when a clip like it is at its place
//!    still: same track, same index, same start.
//! 3. When no clip is like it any more (it was trimmed, slipped, faded,
//!    given another take), and every track has as many clips as before,
//!    and on its track only it and at most one other clip changed, it is
//!    the changed clip at its place.
//! 4. Else it is not followed (a split or joined clip, a deleted one).

use crate::document::{AudioClip, ClipRef, Edit, Frame, TrackRef, Transform, VideoClip};

/// What a clip is, apart from its source and where it starts.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Rest {
    Video { duration: Frame, source_in: Frame, fade_in: Frame, fade_out: Frame, opacity: f64, transform: Transform },
    Audio { duration: Frame, source_in: Frame, fade_in: Frame, fade_out: Frame, gain_db: f64 },
}

impl Rest {
    /// The source frames it shows: the first, and one past the last.
    fn frames(&self) -> (Frame, Frame) {
        match *self {
            Rest::Video { duration, source_in, .. } | Rest::Audio { duration, source_in, .. } => (source_in, source_in + duration),
        }
    }
}

struct Seen<'a, S> {
    clip: ClipRef,
    at: Frame,
    rest: Rest,
    source: &'a S,
}

fn video<S>(c: &VideoClip<S>) -> (Frame, Rest, &S) {
    let rest = Rest::Video {
        duration: c.duration,
        source_in: c.source_in,
        fade_in: c.fade_in,
        fade_out: c.fade_out,
        opacity: c.opacity,
        transform: c.transform,
    };
    (c.at, rest, &c.source)
}

fn audio<S>(c: &AudioClip<S>) -> (Frame, Rest, &S) {
    let rest = Rest::Audio { duration: c.duration, source_in: c.source_in, fade_in: c.fade_in, fade_out: c.fade_out, gain_db: c.gain_db };
    (c.at, rest, &c.source)
}

fn seen<S>(edit: &Edit<S>) -> Vec<Seen<'_, S>> {
    let of =
        |track: TrackRef, index: usize, (at, rest, source): (Frame, Rest, _)| Seen { clip: ClipRef { track, index }, at, rest, source };
    let videos = edit.video.iter().enumerate().flat_map(|(t, track)| track.clips.iter().enumerate().map(move |(i, c)| (t, i, video(c))));
    let audios = edit.audio.iter().enumerate().flat_map(|(t, track)| track.clips.iter().enumerate().map(move |(i, c)| (t, i, audio(c))));
    videos.map(|(t, i, c)| of(TrackRef::video(t), i, c)).chain(audios.map(|(t, i, c)| of(TrackRef::audio(t), i, c))).collect()
}

/// Where clip `clip` of `before` is in `after`, an edit made from it (by
/// ops, an undo or a redo); None when it cannot be told (see the module's
/// rules). `same` says whether two sources are one.
pub fn followed_by<S: Clone>(before: &Edit<S>, after: &Edit<S>, clip: ClipRef, same: impl Fn(&S, &S) -> bool) -> Option<ClipRef> {
    let (was, now) = (seen(before), seen(after));
    let me = was.iter().find(|c| c.clip == clip)?;
    let alike = |a: &Seen<'_, S>, b: &Seen<'_, S>| a.rest == b.rest && same(a.source, b.source);
    let there = now.iter().find(|c| c.clip == clip);
    // Rule 3 first: a clip changed where it lies may have come to look
    // like another did (a roll can do that).
    let counts = |edit: &Edit<S>| edit.tracks().map(|t| edit.spans(t).len()).collect::<Vec<_>>();
    if counts(before) == counts(after)
        && let Some(here) = there
        && !was.iter().any(|w| alike(w, here))
    {
        let old = was.iter().filter(|c| c.clip.track == clip.track);
        let new = now.iter().filter(|c| c.clip.track == clip.track);
        let changed = old.zip(new).filter(|(a, b)| !alike(a, b)).count();
        return (changed <= 2).then_some(clip);
    }
    let like_me: Vec<&Seen<'_, S>> = now.iter().filter(|c| alike(c, me)).collect();
    if let [one] = like_me[..]
        && was.iter().filter(|c| alike(c, me)).count() == 1
    {
        // Unless it may be another clip cut down to this one's look while
        // this one was taken away (a ripple delete does both at once), or
        // the other way round on the way back: a clip of the same source
        // holding all these source frames, that is itself no longer as it
        // was. The edits alone cannot tell the two apart.
        let holds = |whole: &Seen<'_, S>, part: &Seen<'_, S>| {
            let ((a, b), (c, d)) = (whole.rest.frames(), part.rest.frames());
            same(whole.source, part.source) && a <= c && d <= b
        };
        let like = |all: &[Seen<'_, S>], c: &Seen<'_, S>| all.iter().filter(|o| alike(o, c)).count();
        let cut_down = was.iter().any(|x| x.clip != me.clip && holds(x, me) && like(&now, x) < like(&was, x));
        let cut_from = now.iter().any(|y| y.clip != one.clip && holds(y, one) && like(&was, y) < like(&now, y));
        // Nor when a part of it lies where it lay (it was cut there, by
        // an insert say, and the clip like it is a new one), or the
        // other way round on the way back.
        let part_at = |all: &[Seen<'_, S>], whole: &Seen<'_, S>| {
            all.iter().any(|p| p.clip.track == whole.clip.track && p.at == whole.at && !alike(p, whole) && holds(whole, p))
        };
        let cut_here = one.clip != me.clip && (part_at(&now, me) || part_at(&was, one));
        return (!cut_down && !cut_from && !cut_here).then_some(one.clip);
    }
    there.filter(|c| alike(c, me) && c.at == me.at).map(|c| c.clip)
}

/// [`followed_by`] with sources that are one when they are equal.
pub fn followed<S: Clone + PartialEq>(before: &Edit<S>, after: &Edit<S>, clip: ClipRef) -> Option<ClipRef> {
    followed_by(before, after, clip, |a, b| a == b)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::Source;
    use crate::ops::tests::{Lengths, any_op, choose, edits};
    use crate::ops::{Op, apply};
    use proptest::prelude::*;

    /// `edit` with every clip's source its own: the number in it is the
    /// clip's hidden name. A slug stays a slug and media stays media.
    fn named(edit: &Edit<Source>) -> Edit<Source> {
        let mut n = 0;
        edit.try_map(|s| {
            n += 1;
            Ok::<_, ()>(match s {
                Source::Slug { .. } => Source::Slug { text: format!("s{n}") },
                _ => crate::ops::tests::media(100 + n),
            })
        })
        .unwrap()
    }

    /// Sources as the eye has them: every slug alike, all media alike.
    fn looks_same(a: &Source, b: &Source) -> bool {
        std::mem::discriminant(a) == std::mem::discriminant(b)
    }

    fn source_at(edit: &Edit<Source>, clip: ClipRef) -> Option<(Source, Frame)> {
        use crate::document::TrackKind;
        match clip.track.kind {
            TrackKind::Video => edit.video.get(clip.track.index)?.clips.get(clip.index).map(|c| (c.source.clone(), c.at)),
            TrackKind::Audio => edit.audio.get(clip.track.index)?.clips.get(clip.index).map(|c| (c.source.clone(), c.at)),
        }
    }

    fn every_clip(edit: &Edit<Source>) -> Vec<ClipRef> {
        edit.tracks().flat_map(|track| (0..edit.spans(track).len()).map(move |index| ClipRef { track, index })).collect()
    }

    /// A cut of look-alikes: every clip the same media from its first
    /// frame, two or three frames long, so most have a twin.
    fn twins() -> impl Strategy<Value = Edit<Source>> {
        let track = prop::collection::vec((0i64..3, 2i64..4), 2..5);
        prop::collection::vec(track, 1..3).prop_map(|tracks| {
            let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
            for (t, clips) in tracks.iter().enumerate() {
                let mut at = 0;
                let mut placed = Vec::new();
                for &(gap, duration) in clips {
                    at += gap;
                    placed.push(crate::ops::tests::vclip(at, duration, 0, crate::ops::tests::media(100)));
                    at += duration;
                }
                edit.video.push(crate::document::Track { name: format!("V{}", t + 1), clips: placed, color: None });
            }
            edit
        })
    }

    proptest! {
        /// Through any op, and its undo, with clips that look alike (only
        /// their hidden names tell them apart): a clip is followed to
        /// itself, or to a clip that looks the same at the same place, or
        /// not at all. An op that neither adds, removes nor reorders
        /// clips leaves each where it was, so it is followed there or not
        /// at all.
        #[test]
        fn a_clip_is_never_followed_to_another(edit in prop_oneof![edits(), twins()], tried in any_op()) {
            let before = named(&edit);
            let Some(op) = choose(&before, &tried) else { return Ok(()) };
            let Ok(after) = apply(&before, &op, &Lengths) else { return Ok(()) };
            let counts = |e: &Edit<Source>| e.tracks().map(|t| e.spans(t).len()).collect::<Vec<_>>();
            // (A track moved takes its clips to another place among the
            // tracks, which may hold as many clips as the one it passes.)
            let in_place = counts(&before) == counts(&after) && !matches!(op, Op::Move { .. } | Op::MoveTrack { .. });
            for (from, to) in [(&before, &after), (&after, &before)] {
                for clip in every_clip(from) {
                    let Some(now) = followed_by(from, to, clip, looks_same) else { continue };
                    let (name, at) = source_at(from, clip).unwrap();
                    let (name_now, at_now) = source_at(to, now).expect("followed to no clip");
                    if in_place {
                        prop_assert_eq!(now, clip, "{:?} leaves every clip where it is", op);
                    }
                    let itself = name == name_now;
                    let twin = now == clip && at == at_now && looks_same(&name, &name_now);
                    prop_assert!(itself || twin, "{:?}: {:?} followed to {:?}, another clip", op, clip, now);
                }
            }
        }

        /// With every clip its own (no two alike): a clip an op leaves as
        /// it was, but for where it starts, is followed wherever it went;
        /// one the op changed where it lies (no clip added or removed) is
        /// followed there; and none is followed to a clip of another
        /// source.
        #[test]
        fn a_clip_like_no_other_is_followed_wherever_it_goes(edit in edits(), tried in any_op()) {
            let before = named(&edit);
            let Some(op) = choose(&before, &tried) else { return Ok(()) };
            let Ok(after) = apply(&before, &op, &Lengths) else { return Ok(()) };
            let counts = |e: &Edit<Source>| e.tracks().map(|t| e.spans(t).len()).collect::<Vec<_>>();
            let (was, now) = (seen(&before), seen(&after));
            for me in &was {
                let got = followed(&before, &after, me.clip);
                let mine: Vec<&Seen<'_, Source>> = now.iter().filter(|c| c.source == me.source).collect();
                let whole: Vec<&&Seen<'_, Source>> = mine.iter().filter(|c| c.rest == me.rest).collect();
                if let [one] = whole[..] {
                    prop_assert_eq!(got, Some(one.clip), "{:?}: unchanged, not followed", op);
                } else if counts(&before) == counts(&after) {
                    prop_assert_eq!(got, Some(me.clip), "{:?}: changed where it lies, not followed", op);
                }
                if let Some(to) = got {
                    prop_assert!(mine.iter().any(|c| c.clip == to), "{:?}: followed to another clip", op);
                }
            }
        }
    }

    /// Two edits that cannot tell "the second clip was taken away and the
    /// first cut down to its look" from "the first was taken away and the
    /// second moved up" (a ripple delete on another track does the
    /// first): neither clip is followed, either way. When the clip that
    /// went could not have been cut to the other's look (another source,
    /// or not its frames), the one that stayed is followed.
    #[test]
    fn a_clip_another_may_have_been_cut_to_is_not_followed() {
        use crate::ops::tests::{media, vclip};
        let cut = |clips: Vec<VideoClip<Source>>| {
            let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
            edit.video.push(crate::document::Track { name: "V1".into(), clips, color: None });
            edit
        };
        let at = |index| ClipRef { track: TrackRef::video(0), index };
        // (the first clip's source and first source frame, whether the second is followed)
        for (source, source_in, told) in [(media(1), 0, false), (media(1), 1, true), (media(1), 10, true), (media(2), 0, true)] {
            let before = cut(vec![vclip(0, 3, source_in, source), vclip(3, 2, 0, media(1))]);
            let after = cut(vec![vclip(0, 2, 0, media(1))]);
            assert_eq!(followed(&before, &after, at(1)), told.then_some(at(0)), "forward, from frame {source_in}");
            assert_eq!(followed(&after, &before, at(0)), told.then_some(at(1)), "back, from frame {source_in}");
            assert_eq!(followed(&before, &after, at(0)), None);
        }
        // A clip cut where it lies by an insert of a clip of its look is
        // not followed to the new one, nor the new one back to it.
        let before = cut(vec![vclip(1, 2, 0, media(1)), vclip(4, 3, 0, media(1))]);
        let after = cut(vec![vclip(1, 2, 0, media(1)), vclip(4, 1, 0, media(1)), vclip(5, 3, 0, media(1)), vclip(8, 2, 1, media(1))]);
        assert_eq!(followed(&before, &after, at(1)), None);
        assert_eq!(followed(&after, &before, at(2)), None);
        assert_eq!(followed(&before, &after, at(0)), Some(at(0)));
    }

    #[test]
    fn a_moved_clip_is_followed_past_the_clips_it_jumps() {
        use crate::ops::tests::{media, vclip};
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        let clips = vec![vclip(0, 10, 0, media(1)), vclip(10, 10, 0, media(2)), vclip(20, 10, 0, media(3))];
        edit.video.push(crate::document::Track { name: "V1".into(), clips, color: None });
        let first = ClipRef { track: TrackRef::video(0), index: 0 };
        let after = apply(&edit, &Op::Move { clip: first, to: first.track, at: 40 }, &Lengths).unwrap();
        assert_eq!(followed(&edit, &after, first), Some(ClipRef { index: 2, ..first }));
        assert_eq!(followed(&edit, &after, ClipRef { index: 1, ..first }), Some(first));
        // Deleted: not followed to the clip now at its place.
        let after = apply(&edit, &Op::Lift { clip: first }, &Lengths).unwrap();
        assert_eq!(followed(&edit, &after, first), None);
        assert_eq!(followed(&edit, &after, ClipRef { index: 2, ..first }), Some(ClipRef { index: 1, ..first }));
    }
}
