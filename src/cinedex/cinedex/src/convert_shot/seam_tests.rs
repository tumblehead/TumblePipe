//! Convert to Shot where it meets the rest of the editor (2026-11-22): the
//! clip menu's Split / Join / Delete / Ripple Delete / Close Gap, trims,
//! rolls, slips, moves and insert-reorder, on a converted clip (one whose
//! `original` is set).
//!
//! The rule they keep: a converted clip and the clip it replaced are two
//! takes on one place in the cut. The clip kept always has the shot clip's
//! span and fades, and its source runs in step with the shot clip's (a
//! trim, slip, roll or split that moves where the shot clip starts in its
//! source moves the animatic as far). So whatever is done to the shot clip
//! is done to the animatic under it: Swap to Original after any op gives
//! the edit that op makes of the swapped edit. A Join that would put an
//! animatic that does not continue (or none) under a joined clip is
//! refused, as a join that would lose an opacity is.

use proptest::prelude::*;
use th_edit::document::{Clip, Rule, Track, TrackRef, VersionRule};
use th_edit::history::History;
use th_edit::ops::{Available, Edge, NewClip, Sources, apply};

use super::*;

struct Anything;
impl Sources<Source> for Anything {
    fn available(&self, _: &Source) -> Available {
        Available::Any
    }
}

fn shot(n: usize) -> Source {
    Source::Shot { shot: format!("entity:/shots/SQ010/SH{n:03}0"), take: Rule::best() }
}

fn animatic(n: usize) -> Source {
    if n.is_multiple_of(2) {
        Source::Media { item: format!("animatic{n}"), version: VersionRule::Latest }
    } else {
        Source::Slug { text: format!("panel {n}") }
    }
}

/// What a clip of the generated edits is.
#[derive(Debug, Clone, Copy)]
enum Kind {
    Shot,
    Animatic,
    /// An animatic clip converted to a shot.
    Converted,
}

fn kind() -> impl Strategy<Value = Kind> {
    prop_oneof![1 => Just(Kind::Shot), 1 => Just(Kind::Animatic), 2 => Just(Kind::Converted)]
}

/// An edit of 1 to 3 video tracks and an audio track, clips apart or
/// touching, shots, animatic clips and converted clips mixed, with fades
/// that fit. Two converted clips in a row are often a converted clip
/// split (each half's animatic continuing the other's).
fn edit() -> impl Strategy<Value = WorkingEdit> {
    let clip = (prop_oneof![Just(0i64), 0i64..6], 6i64..40, kind(), 0i64..3, 0i64..3, 900i64..1100, any::<bool>());
    let track = prop::collection::vec(clip, 1..6);
    prop::collection::vec(track, 1..4).prop_map(|tracks| {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
        let mut n = 0;
        for (t, clips) in tracks.into_iter().enumerate() {
            let mut at = 0;
            let mut out: Vec<VideoClip<Source>> = Vec::new();
            for (gap, duration, kind, fade_in, fade_out, source_in, continues) in clips {
                n += 1;
                at += gap;
                // The second half of a converted clip split, when it
                // touches one and `continues`.
                let left = out.last().filter(|c| matches!(kind, Kind::Converted) && continues && gap == 0 && c.original.is_some()).cloned();
                let half = left.is_some();
                let mut clip = match (kind, left) {
                    (_, Some(left)) => {
                        let mut right = VideoClip::new(at, duration, left.source_in + left.duration, left.source.clone());
                        let mut kept = (**left.original.as_ref().unwrap()).clone();
                        (kept.at, kept.duration, kept.source_in) = (at, duration, kept.source_in + left.duration);
                        right.original = Some(Box::new(kept));
                        right
                    }
                    (Kind::Shot, None) => VideoClip::new(at, duration, source_in, shot(n)),
                    (Kind::Animatic, None) => VideoClip::new(at, duration, source_in, animatic(n)),
                    (Kind::Converted, None) => {
                        let mut anim = VideoClip::new(at, duration, source_in, animatic(n));
                        anim.opacity = if n % 3 == 0 { 0.5 } else { 1.0 };
                        converted(&anim, VideoClip::new(at, duration, 1001 + n as i64, shot(n)))
                    }
                };
                set_fades(&mut clip, if half { 0 } else { fade_in }, fade_out);
                if half {
                    let before = out.last_mut().unwrap();
                    let fade_in = before.fade_in;
                    set_fades(before, fade_in, 0);
                }
                out.push(clip);
                at += duration;
            }
            edit.video.push(Track { name: format!("V{}", t + 1), clips: out, color: None });
        }
        edit.audio.push(Track { name: "A1".into(), clips: Vec::new(), color: None });
        edit
    })
}

/// `clip`'s fades, and its kept clip's alike.
fn set_fades(clip: &mut VideoClip<Source>, fade_in: i64, fade_out: i64) {
    (clip.fade_in, clip.fade_out) = (fade_in, fade_out);
    if let Some(kept) = clip.original.as_deref_mut() {
        (kept.fade_in, kept.fade_out) = (fade_in, fade_out);
    }
}

/// What to do, before it is fitted to an edit: which op, and numbers to
/// pick its clip, frame and size with.
#[derive(Debug, Clone)]
struct Pick {
    op: u8,
    clip: prop::sample::Index,
    frame: prop::sample::Index,
    delta: i64,
    track: prop::sample::Index,
    at: i64,
}

fn pick() -> impl Strategy<Value = Pick> {
    (0u8..13, any::<prop::sample::Index>(), any::<prop::sample::Index>(), -8i64..9, any::<prop::sample::Index>(), -10i64..200)
        .prop_map(|(op, clip, frame, delta, track, at)| Pick { op, clip, frame, delta, track, at })
}

fn every_clip(edit: &WorkingEdit) -> Vec<ClipRef> {
    edit.video
        .iter()
        .enumerate()
        .flat_map(|(t, track)| (0..track.clips.len()).map(move |index| ClipRef { track: TrackRef::video(t), index }))
        .collect()
}

/// The ops `pick` makes of `edit`, as the editor sends them for the clip
/// menu's rows, the drags and the keys; None when the edit has no clip.
fn ops_of(edit: &WorkingEdit, pick: &Pick) -> Option<Vec<Op<Source>>> {
    let clips = every_clip(edit);
    let clip = *pick.clip.get(&clips);
    let c = video_clip(edit, clip)?;
    let interior: Vec<i64> = (c.at + 1..c.at + c.duration).collect();
    let tracks = edit.video.len();
    Some(match pick.op {
        // Split Here / Split at Playhead.
        0 | 1 => vec![Op::Split { clip, at: *pick.frame.get(&interior).max(&(c.at + 1)) }],
        // Join with Next Clip.
        2 => vec![Op::Join { clip }],
        // Delete.
        3 => vec![Op::Lift { clip }],
        // Ripple Delete.
        4 => vec![Op::RippleDelete { clip }],
        // Close Gap Before (`gestures::closing`).
        5 => {
            let before = if clip.index == 0 { 0 } else { edit.video[clip.track.index].clips[clip.index - 1].end() };
            vec![Op::Shift { clip, delta: before - c.at }]
        }
        // Trims (a snapped one is a trim by another delta).
        6 => vec![Op::Trim { clip, edge: Edge::Head, delta: pick.delta }],
        7 => vec![Op::Trim { clip, edge: Edge::Tail, delta: pick.delta }],
        8 => vec![Op::Roll { clip, delta: pick.delta }],
        9 => vec![Op::Slip { clip, delta: pick.delta }],
        // A move to free space on any video track.
        10 => {
            let to = *pick.track.get(&(0..tracks).collect::<Vec<_>>());
            vec![Op::Move { clip, to: TrackRef::video(to), at: pick.at.max(0) }]
        }
        // Insert-reorder along its own track (`tracks::reorder`), or the
        // plain move it leaves to the caller.
        11 => {
            let spans = edit.spans(clip.track);
            match crate::tracks::reorder::reorder(&spans, clip.index, pick.at) {
                Some(placed) => crate::tracks::reorder::rearrange_ops(clip.track, &spans, &placed),
                None => vec![Op::Move { clip, to: clip.track, at: pick.at.max(0) }],
            }
        }
        // A ripple insert across it (a clip put in, cutting what is
        // across its frame).
        _ => vec![Op::Insert {
            track: TrackRef::video(*pick.track.get(&(0..tracks).collect::<Vec<_>>())),
            clip: NewClip::Video(VideoClip::new(*pick.frame.get(&interior).max(&c.at), 4, 0, Source::Slug { text: "new".into() })),
        }],
    })
}

fn run(edit: &WorkingEdit, ops: &[Op<Source>]) -> Result<WorkingEdit, th_edit::ops::Refused> {
    ops.iter().try_fold(edit.clone(), |e, op| apply(&e, op, &Anything))
}

/// `edit` with every converted clip swapped with the clip it replaced, in
/// place (Swap to Original on each).
fn swap_all(edit: &WorkingEdit) -> WorkingEdit {
    let mut out = edit.clone();
    for track in &mut out.video {
        for clip in &mut track.clips {
            if let Some(other) = swapped(clip) {
                *clip = other;
            }
        }
    }
    out
}

/// Why the clip kept in a clip of `edit` is not one to swap back to, if
/// it is not: it keeps one in turn, or is not in the clip's span and
/// fades.
fn kept_problem(edit: &WorkingEdit) -> Option<String> {
    for (t, track) in edit.video.iter().enumerate() {
        for (i, c) in track.clips.iter().enumerate() {
            let Some(o) = c.original.as_deref() else { continue };
            if o.original.is_some() {
                return Some(format!("V{} clip {i} keeps a chain", t + 1));
            }
            if (o.at, o.duration, o.fade_in, o.fade_out) != (c.at, c.duration, c.fade_in, c.fade_out) {
                return Some(format!("V{} clip {i} at {} for {} keeps a clip at {} for {}", t + 1, c.at, c.duration, o.at, o.duration));
            }
        }
    }
    None
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(400))]

    /// Whatever the clip menu, a drag or a key does to an edit holding
    /// converted clips, swapping them all to their originals afterwards
    /// gives what that op makes of the edit swapped first: the animatic
    /// under a shot clip is split, joined, trimmed, rolled, slipped, moved,
    /// deleted and reordered with it. Each kept clip stays one level, in
    /// its clip's span; undo puts the edit back exactly.
    #[test]
    fn edits_of_a_converted_clip_are_edits_of_its_original(edit in edit(), pick in pick()) {
        prop_assert_eq!(kept_problem(&edit), None, "the generated edit");
        let Some(ops) = ops_of(&edit, &pick) else { return Ok(()) };
        let Ok(after) = run(&edit, &ops) else { return Ok(()) };
        prop_assert_eq!(kept_problem(&after), None, "after {:?}", ops);
        let swapped_first = swap_all(&edit);
        let swapped_after = run(&swapped_first, &ops);
        prop_assert!(swapped_after.is_ok(), "{:?} is taken on the shot clips but refused on their originals: {:?}", ops, swapped_after);
        prop_assert_eq!(&swap_all(&after), &swapped_after.unwrap(), "{:?}", ops);
        // One undo step back to exactly what was, originals and all.
        let mut doc = edit.clone();
        let mut history = History::new();
        history.apply_all(&mut doc, &ops, &Anything).unwrap();
        prop_assert_eq!(&doc, &after);
        // (A reorder to where it is is no ops, and no step.)
        prop_assert_eq!(history.undo(&mut doc), !ops.is_empty());
        prop_assert_eq!(&doc, &edit);
    }

    /// After any op, Swap to Original on any clip that has one is taken
    /// and gives a valid edit, and Swap Back then gives the edit before
    /// the swap exactly.
    #[test]
    fn swap_and_swap_back_after_any_op(edit in edit(), pick in pick(), which in any::<prop::sample::Index>()) {
        let Some(ops) = ops_of(&edit, &pick) else { return Ok(()) };
        let Ok(after) = run(&edit, &ops) else { return Ok(()) };
        let converted: Vec<ClipRef> = every_clip(&after).into_iter().filter(|&c| video_clip(&after, c).unwrap().original.is_some()).collect();
        if converted.is_empty() {
            return Ok(());
        }
        let clip = *which.get(&converted);
        let with = swapped(video_clip(&after, clip).unwrap()).unwrap();
        let swap = run(&after, &replace_ops(&after, clip, with).unwrap());
        prop_assert!(swap.is_ok(), "Swap to Original after {:?}: {:?}", ops, swap);
        let swap = swap.unwrap();
        prop_assert_eq!(swap.problem(), None);
        prop_assert_eq!(swap.video[clip.track.index].clips.len(), after.video[clip.track.index].clips.len());
        let back = swapped(video_clip(&swap, clip).unwrap()).unwrap();
        let back = run(&swap, &replace_ops(&swap, clip, back).unwrap()).unwrap();
        prop_assert_eq!(&back, &after);
    }

    /// A Join of a converted clip and a neighbour whose kept clips do not
    /// continue each other (one has none, or another animatic, or not the
    /// frames after) is refused; one whose do continue is taken, and the
    /// joined clip keeps the two as one.
    #[test]
    fn a_join_keeps_only_an_original_that_continues(
        duration in 6i64..30, second in 6i64..30, offset in -2i64..3, other_source in any::<bool>(), right_has in any::<bool>(),
    ) {
        let anim = VideoClip::new(0, duration, 100, animatic(2));
        let left = converted(&anim, VideoClip::new(0, duration, 1001, shot(1)));
        let mut right = VideoClip::new(duration, second, 1001 + duration, shot(1));
        if right_has {
            let source = if other_source { animatic(4) } else { animatic(2) };
            let kept = VideoClip::new(duration, second, 100 + duration + offset, source);
            right.original = Some(Box::new(kept));
        }
        let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![left.clone(), right], color: None });
        let joined = apply(&edit, &Op::Join { clip: ClipRef { track: TrackRef::video(0), index: 0 } }, &Anything);
        let continues = right_has && !other_source && offset == 0;
        prop_assert_eq!(joined.is_ok(), continues, "{:?}", joined);
        if let Ok(joined) = joined {
            let clip = &joined.video[0].clips[0];
            let kept = clip.original.as_deref().unwrap();
            prop_assert_eq!((kept.at, kept.duration, kept.source_in), (0, duration + second, 100));
            prop_assert_eq!(&kept.source, &anim.source);
        }
    }
}
