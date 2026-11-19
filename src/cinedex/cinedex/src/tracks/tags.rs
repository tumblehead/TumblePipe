//! What a drag in progress says under its outline: how far it went and
//! what moves with it, so a ripple is seen before it is let go.
//!
//! An edge drag is a ripple trim (`th_edit::ops::Op::Trim`): the clips
//! after the edge on its track always move with it; on each other track
//! the clips starting at or after the edit point move too when they can
//! (`th_edit::ops::follows`), else that track stays as it is. A push
//! (`Op::Shift`) moves the clip and what follows it on its own track only.
//! The words here are read from the same rule the document applies, and
//! the tests hold them to what the op then does.

use th_edit::document::Frame;
use th_edit::ops::follows;

/// The frames an edge drag takes out of the cut, `from .. to`: the clip is
/// at `at .. end` and its head (else its tail) went `dx` frames later. None
/// when it puts frames in (or changes nothing).
pub fn taken_out(head: bool, at: Frame, end: Frame, dx: Frame) -> Option<(Frame, Frame)> {
    match (head, dx) {
        (true, dx) if dx > 0 => Some((at, at + dx)),
        (false, dx) if dx < 0 => Some((end + dx, end)),
        _ => None,
    }
}

/// Whether a track with clips at `spans` (each `(at, end)`, in order) is
/// left behind by a trim that takes frames `from .. to` out: it has clips
/// after those frames, and they cannot move along.
pub fn stays(spans: &[(Frame, Frame)], from: Frame, to: Frame) -> bool {
    spans.iter().any(|&(at, _)| at >= to) && !follows(spans, from, to)
}

fn clips(n: usize) -> String {
    if n == 1 { "1 clip follows".to_owned() } else { format!("{n} clips follow") }
}

/// The words for an edge dragged `dx` frames: `after` clips follow it on
/// its track, and the tracks named in `staying` cannot.
pub fn trim_words(dx: Frame, after: usize, staying: &[&str]) -> String {
    let mut words = format!("{dx:+}");
    if dx != 0 && after > 0 {
        words.push_str(&format!(" \u{b7} {}", clips(after)));
    }
    if dx != 0 && !staying.is_empty() {
        words.push_str(&format!(" \u{b7} {} {} (no room)", staying.join(", "), if staying.len() == 1 { "stays" } else { "stay" }));
    }
    words
}

/// The words for a clip pushed `dx` frames with the `after` clips after it;
/// `held`: the clip before it (or the cut's start) stopped it.
pub fn push_words(dx: Frame, after: usize, held: bool) -> String {
    let mut words = format!("push {dx:+}");
    if after > 0 {
        words.push_str(&format!(" \u{b7} {}", clips(after)));
    }
    if held {
        words.push_str(" \u{b7} against what is before");
    }
    words
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{ClipRef, Edit, Source, Track, TrackRef, VideoClip};
    use th_edit::ops::{Available, Edge, Op, Sources, apply};

    struct AnyFrames;

    impl Sources<Source> for AnyFrames {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    fn spans() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((0i64..15, 1i64..25), 0..5).prop_map(|gaps| {
            let mut at = 0;
            gaps.into_iter()
                .map(|(gap, long)| {
                    at += gap;
                    let span = (at, at + long);
                    at += long;
                    span
                })
                .collect()
        })
    }

    proptest! {
        /// The tracks said to stay are exactly those the trim then leaves
        /// with a clip after the frames taken out that did not move; every
        /// other track's later clips moved by the trim. And the count of
        /// clips that follow on its own track is the clips after it, each
        /// moved by the trim.
        #[test]
        fn the_words_are_what_the_trim_does(
            tracks in prop::collection::vec(spans(), 1..4), t in any::<prop::sample::Index>(), c in any::<prop::sample::Index>(),
            head in any::<bool>(), dx in -30i64..30,
        ) {
            let mut edit = Edit::<Source>::new(24, [1920, 1080], "01:00:00:00");
            for (i, spans) in tracks.iter().enumerate() {
                let clips = spans.iter().map(|&(at, end)| VideoClip::new(at, end - at, 100, Source::Slug { text: "s".into() })).collect();
                edit.video.push(Track { name: format!("V{}", i + 1), clips, color: None });
            }
            let used: Vec<usize> = (0..tracks.len()).filter(|&i| !tracks[i].is_empty()).collect();
            prop_assume!(!used.is_empty());
            let own = *t.get(&used);
            let index = c.index(tracks[own].len());
            let (at, end) = tracks[own][index];
            let clip = ClipRef { track: TrackRef::video(own), index };
            let edge = if head { Edge::Head } else { Edge::Tail };
            let Ok(next) = apply(&edit, &Op::Trim { clip, edge, delta: dx }, &AnyFrames) else { return Ok(()) };
            let longer = if head { -dx } else { dx };
            let out = taken_out(head, at, end, dx);
            prop_assert_eq!(out.is_some(), longer < 0);
            for (i, spans) in tracks.iter().enumerate() {
                let now = next.spans(TrackRef::video(i));
                if i == own {
                    let after = spans.len() - index - 1;
                    let moved = (index + 1..spans.len()).filter(|&j| now[j].0 == spans[j].0 + longer).count();
                    prop_assert_eq!(moved, after);
                    continue;
                }
                let Some((from, to)) = out else { continue };
                let later: Vec<usize> = (0..spans.len()).filter(|&j| spans[j].0 >= to).collect();
                let left_behind = !later.is_empty() && later.iter().all(|&j| now[j] == spans[j]);
                let went_along = later.iter().all(|&j| now[j].0 == spans[j].0 + longer);
                prop_assert_eq!(stays(spans, from, to), left_behind, "V{} with {}..{} out", i + 1, from, to);
                prop_assert!(left_behind || went_along);
            }
        }
    }

    #[test]
    fn the_words_say_what_moves() {
        assert_eq!(trim_words(-5, 3, &[]), "-5 \u{b7} 3 clips follow");
        assert_eq!(trim_words(4, 1, &[]), "+4 \u{b7} 1 clip follows");
        assert_eq!(trim_words(-5, 0, &["V2"]), "-5 \u{b7} V2 stays (no room)");
        assert_eq!(trim_words(-5, 2, &["V2", "A1"]), "-5 \u{b7} 2 clips follow \u{b7} V2, A1 stay (no room)");
        assert_eq!(trim_words(0, 2, &["V2"]), "+0");
        assert_eq!(push_words(12, 2, false), "push +12 \u{b7} 2 clips follow");
        assert_eq!(push_words(-3, 0, true), "push -3 \u{b7} against what is before");
    }
}
