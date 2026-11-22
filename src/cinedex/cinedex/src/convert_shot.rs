//! Convert to Shot (Remy's RFE, 2026-11-22: "map out a 2D animatic on the
//! timeline, then right-click a clip and convert it to a shot").
//!
//! Right-click a video clip that is not a shot (an animatic movie or still
//! from the media library, a slug) > **Convert to Shot…**: the New shot
//! dialog opens over the clip's frames, as an Alt+drag on empty track does
//! (`editor::range_shot`), and Create Shot (or Plan Shot) puts the new
//! shot's clip in the clip's place, on its track at its frame, as one undo
//! step. The clip it replaced is kept in the shot clip
//! (`VideoClip::original`, saved with the edit), so the shot clip's
//! right-click offers **Swap to Original Clip**, which puts the animatic
//! back in the shot clip's span, keeping the shot clip in turn to swap
//! back to. Swapping is a plain edit: undo takes it back.
//!
//! The shot clip keeps the animatic's fades (its dissolves stay), not its
//! opacity or framing (a shot is framed as rendered).

use th_edit::document::{ClipRef, Source, TrackKind, VideoClip, WorkingEdit};
use th_edit::ops::Op;

use crate::editing;

/// Whether `clip` can be converted to a shot: a clip of anything but a
/// shot.
pub fn convertible(clip: &VideoClip<Source>) -> bool {
    !matches!(clip.source, Source::Shot { .. })
}

/// `clip` without the clip it replaced: what is kept to swap back to (one
/// level, never a chain).
fn bare(clip: &VideoClip<Source>) -> Box<VideoClip<Source>> {
    Box::new(VideoClip { original: None, ..clip.clone() })
}

/// `shot` (a new shot's clip) as the clip that replaces `original`: the
/// shot's own frames, `original`'s fades where they fit in it, and
/// `original` kept to swap back to, in the shot clip's span and fades
/// from the source frame it started on. The edit keeps the kept clip so
/// from then on (`th_edit::Clip`'s setters): a split, trim, roll, slip or
/// move of the shot clip is one of it too.
pub fn converted(original: &VideoClip<Source>, shot: VideoClip<Source>) -> VideoClip<Source> {
    let (fade_in, fade_out) =
        if original.fade_in + original.fade_out <= shot.duration { (original.fade_in, original.fade_out) } else { (0, 0) };
    let kept = VideoClip { at: shot.at, duration: shot.duration, fade_in, fade_out, ..*bare(original) };
    VideoClip { fade_in, fade_out, original: Some(Box::new(kept)), ..shot }
}

/// The clip that takes `clip`'s place when it is swapped with the clip it
/// replaced: that clip (its source from the frame it started on, its
/// opacity and framing) in `clip`'s span and fades, keeping `clip` to swap
/// back to. None when `clip` replaced none.
pub fn swapped(clip: &VideoClip<Source>) -> Option<VideoClip<Source>> {
    let original = clip.original.as_deref()?;
    Some(VideoClip {
        at: clip.at,
        duration: clip.duration,
        fade_in: clip.fade_in,
        fade_out: clip.fade_out,
        original: Some(bare(clip)),
        ..original.clone()
    })
}

/// Video clip `clip` of `edit`, if there is one.
pub fn video_clip(edit: &WorkingEdit, clip: ClipRef) -> Option<&VideoClip<Source>> {
    (clip.track.kind == TrackKind::Video).then_some(())?;
    edit.video.get(clip.track.index)?.clips.get(clip.index)
}

/// The clip on video track `track` of `edit` that starts on frame `at`
/// and can be converted: the one a Convert to Shot was asked on.
pub fn convertible_at(edit: &WorkingEdit, track: usize, at: i64) -> Option<ClipRef> {
    let index = edit.video.get(track)?.clips.iter().position(|c| c.at == at && convertible(c))?;
    Some(ClipRef { track: th_edit::document::TrackRef::video(track), index })
}

/// The ops that put `with` in place of video clip `clip` of `edit`: the
/// clip taken out, then `with` laid at its own frame on that track (on the
/// lowest track above with room when it is longer than the room there).
/// None when there is no such clip.
pub fn replace_ops(edit: &WorkingEdit, clip: ClipRef, with: VideoClip<Source>) -> Option<Vec<Op<Source>>> {
    video_clip(edit, clip)?;
    let mut lifted = edit.clone();
    lifted.video[clip.track.index].clips.remove(clip.index);
    let mut ops = vec![Op::Lift { clip }];
    ops.extend(editing::put(&lifted, Some(with), None, clip.track.index, true));
    Some(ops)
}

/// What a right-click on video clip `clip` of `edit` offers here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// Convert to Shot…: the New shot dialog over its frames.
    Convert,
    /// Swap with the clip it replaced.
    Swap,
}

/// The rows a right-click on `clip` gets, each its label: none on an edit
/// that cannot change, or off the video clips.
pub fn rows(edit: &WorkingEdit, clip: ClipRef, writable: bool) -> Vec<(String, Row)> {
    let Some(c) = video_clip(edit, clip).filter(|_| writable) else { return Vec::new() };
    let mut rows = Vec::new();
    if convertible(c) {
        rows.push(("Convert to Shot\u{2026}".to_owned(), Row::Convert));
    }
    if let Some(original) = &c.original {
        let label = match &original.source {
            Source::Shot { shot, .. } => match editing::shot_of_uri(shot) {
                Some((sequence, name)) => format!("Swap Back to Shot {sequence}/{name}"),
                None => "Swap Back to Shot".to_owned(),
            },
            _ => "Swap to Original Clip".to_owned(),
        };
        rows.push((label, Row::Swap));
    }
    rows
}

#[cfg(test)]
mod seam_tests;

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{Rule, Track, TrackRef, VersionRule};
    use th_edit::ops::{Available, Sources, apply};

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

    /// An edit of 1 to 3 video tracks, each of clips apart or touching,
    /// shots and animatic clips mixed, with fades that fit.
    fn edit() -> impl Strategy<Value = WorkingEdit> {
        let clip = (0i64..6, 6i64..40, any::<bool>(), 0i64..3, 0i64..3, 900i64..1100);
        let track = prop::collection::vec(clip, 1..6);
        prop::collection::vec(track, 1..4).prop_map(|tracks| {
            let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
            let mut n = 0;
            for (t, clips) in tracks.into_iter().enumerate() {
                let mut at = 0;
                let mut out = Vec::new();
                for (gap, duration, is_shot, fade_in, fade_out, source_in) in clips {
                    n += 1;
                    at += gap;
                    let source = if is_shot { shot(n) } else { animatic(n) };
                    let mut clip = VideoClip::new(at, duration, source_in, source);
                    (clip.fade_in, clip.fade_out) = (fade_in, fade_out);
                    out.push(clip);
                    at += duration;
                }
                edit.video.push(Track { name: format!("V{}", t + 1), clips: out, color: None });
            }
            edit
        })
    }

    /// An edit and one of its animatic clips, if it has one.
    fn with_animatic() -> impl Strategy<Value = (WorkingEdit, Option<ClipRef>)> {
        (edit(), any::<prop::sample::Index>()).prop_map(|(edit, pick)| {
            let all: Vec<ClipRef> = edit
                .video
                .iter()
                .enumerate()
                .flat_map(|(t, track)| (0..track.clips.len()).map(move |index| ClipRef { track: TrackRef::video(t), index }))
                .filter(|&c| convertible(video_clip(&edit, c).unwrap()))
                .collect();
            let picked = (!all.is_empty()).then(|| all[pick.index(all.len())]);
            (edit, picked)
        })
    }

    fn run(edit: &WorkingEdit, ops: &[Op<Source>]) -> WorkingEdit {
        ops.iter().fold(edit.clone(), |e, op| apply(&e, op, &Anything).unwrap_or_else(|why| panic!("{op:?}: {why}")))
    }

    /// The clips of every track but `track`, and of `track` all but the one
    /// at `skip`.
    fn others(edit: &WorkingEdit, track: usize, skip: i64) -> Vec<Vec<VideoClip<Source>>> {
        edit.video.iter().enumerate().map(|(t, tr)| tr.clips.iter().filter(|c| t != track || c.at != skip).cloned().collect()).collect()
    }

    proptest! {
        /// Converting an animatic clip to a shot of its length puts the
        /// shot clip in its place, on its track at its frame, keeping the
        /// animatic clip in it; every other clip stays as it was. Its
        /// rows then offer a swap, not another conversion.
        #[test]
        fn a_converted_clip_takes_the_animatics_place((edit, picked) in with_animatic(), first in 1001i64..1100) {
            let Some(clip) = picked else { return Ok(()) };
            let original = video_clip(&edit, clip).unwrap().clone();
            let shot_clip = VideoClip::new(original.at, original.duration, first, shot(999));
            let ops = replace_ops(&edit, clip, converted(&original, shot_clip.clone())).unwrap();
            let after = run(&edit, &ops);
            let track = clip.track.index;
            prop_assert_eq!(after.video.len(), edit.video.len(), "no track added");
            let now = convertible_at(&after, track, original.at);
            prop_assert!(now.is_none(), "the animatic clip is gone from its frame");
            let placed = after.video[track].clips.iter().find(|c| c.at == original.at).unwrap();
            prop_assert_eq!(&placed.source, &shot_clip.source);
            prop_assert_eq!((placed.duration, placed.source_in), (original.duration, first));
            prop_assert_eq!((placed.fade_in, placed.fade_out), (original.fade_in, original.fade_out));
            prop_assert_eq!(placed.original.as_deref(), Some(&original));
            prop_assert_eq!(others(&after, track, original.at), others(&edit, track, original.at));
            let index = after.video[track].clips.iter().position(|c| c.at == original.at).unwrap();
            let at = ClipRef { track: TrackRef::video(track), index };
            prop_assert_eq!(rows(&after, at, true).into_iter().map(|(_, r)| r).collect::<Vec<_>>(), vec![Row::Swap]);
            prop_assert!(rows(&after, at, false).is_empty(), "nothing on an edit that cannot change");
        }

        /// Swapping the shot clip back puts the animatic clip back as it
        /// was; swapping again puts the shot clip back as it was: every
        /// clip plays as before, whichever way round.
        #[test]
        fn swapping_back_and_forth_restores_each((edit, picked) in with_animatic(), first in 1001i64..1100) {
            let Some(clip) = picked else { return Ok(()) };
            let original = video_clip(&edit, clip).unwrap().clone();
            let shot_clip = converted(&original, VideoClip::new(original.at, original.duration, first, shot(999)));
            let converted_edit = run(&edit, &replace_ops(&edit, clip, shot_clip.clone()).unwrap());
            let track = clip.track.index;
            let find = |e: &WorkingEdit| {
                let index = e.video[track].clips.iter().position(|c| c.at == original.at).unwrap();
                ClipRef { track: TrackRef::video(track), index }
            };
            let at = find(&converted_edit);
            let back = swapped(video_clip(&converted_edit, at).unwrap()).unwrap();
            let restored = run(&converted_edit, &replace_ops(&converted_edit, at, back).unwrap());
            // As it was, but for the shot clip it now keeps.
            let mut plain = restored.clone();
            let at = find(&restored);
            let kept = plain.video[track].clips[at.index].original.take();
            prop_assert_eq!(&plain, &edit);
            prop_assert_eq!(kept.as_deref(), Some(&VideoClip { original: None, ..shot_clip.clone() }));
            prop_assert_eq!(rows(&restored, at, true).into_iter().map(|(_, r)| r).collect::<Vec<_>>(), vec![Row::Convert, Row::Swap]);
            let again = swapped(video_clip(&restored, at).unwrap()).unwrap();
            let reshot = run(&restored, &replace_ops(&restored, at, again).unwrap());
            let at = find(&reshot);
            let mut plain = reshot.clone();
            let kept = plain.video[track].clips[at.index].original.take();
            let mut expected = converted_edit.clone();
            expected.video[track].clips[at.index].original = None;
            prop_assert_eq!(&plain, &expected);
            prop_assert_eq!(kept.map(|k| k.source), Some(original.source.clone()));
        }

        /// A clip that replaced none has no swap, and a shot clip is
        /// never offered Convert to Shot.
        #[test]
        fn only_animatic_clips_convert_and_only_replacing_clips_swap(edit in edit()) {
            for (t, track) in edit.video.iter().enumerate() {
                for (index, c) in track.clips.iter().enumerate() {
                    let labels: Vec<Row> = rows(&edit, ClipRef { track: TrackRef::video(t), index }, true).into_iter().map(|(_, r)| r).collect();
                    prop_assert_eq!(labels, if convertible(c) { vec![Row::Convert] } else { vec![] });
                    prop_assert!(swapped(c).is_none());
                }
            }
        }
    }

    #[test]
    fn the_swap_row_names_the_shot_it_goes_back_to() {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
        let animatic = VideoClip::new(0, 24, 0, animatic(2));
        let shot_clip = converted(&animatic, VideoClip::new(0, 24, 1001, shot(1)));
        let back = swapped(&shot_clip).unwrap();
        edit.video.push(Track { name: "V1".into(), clips: vec![back], color: None });
        let labels: Vec<String> = rows(&edit, ClipRef { track: TrackRef::video(0), index: 0 }, true).into_iter().map(|(l, _)| l).collect();
        assert_eq!(labels, vec!["Convert to Shot\u{2026}".to_owned(), "Swap Back to Shot SQ010/SH0010".to_owned()]);
    }

    #[test]
    fn a_converted_clip_saves_and_reads_back_with_its_original() {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "00:00:00:00");
        let animatic = VideoClip::new(0, 24, 5, animatic(2));
        edit.video.push(Track { name: "V1".into(), clips: vec![converted(&animatic, VideoClip::new(0, 24, 1001, shot(1)))], color: None });
        let json = edit.to_json();
        assert!(json.contains("\"original\""));
        assert_eq!(WorkingEdit::parse(&json).unwrap(), edit);
        let mut plain = edit.clone();
        plain.video[0].clips[0].original = None;
        assert!(!plain.to_json().contains("\"original\""), "a clip that replaced none is written as before");
    }
}
