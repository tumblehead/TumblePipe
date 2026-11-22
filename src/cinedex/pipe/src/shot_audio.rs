//! A shot's sound from the edit (Remy's RFE, 2026-11-22): the edit's audio
//! tracks over the shot's frames, for the shot's Houdini scene to play.
//!
//! The shot's clip in the edit maps its frames onto the edit: edit position
//! `at` shows shot frame `source_in`, so shot frame `f` is edit frame
//! `at + (f - source_in)`. A shot's frames (roll included, what the scene's
//! playbar covers) are a window of the edit that may reach past the clip:
//! there the sound is what the edit plays around the cut, the next shot's
//! dialogue running over, which is what the shot is timed against. A window
//! before the edit's frame 0 is silence.
//!
//! The pure parts are here, testable without the editor: finding the clip
//! ([`shot_clip`]), the window ([`edit_window`]), its samples
//! ([`sample_window`]) and which tracks are mixed ([`select_tracks`]).
//! `cinedex --shot-audio` mixes and writes the WAV.

use th_edit::document::{Edit, Frame};

use crate::Uri;

/// The shot's clip on the edit: where it starts, how long it is and the
/// shot frame it starts on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShotClip {
    pub at: Frame,
    pub duration: Frame,
    pub source_in: Frame,
}

impl ShotClip {
    /// The edit frame shot frame `frame` falls on (before 0, or past the
    /// clip, when the frame is outside it).
    pub fn edit_frame(&self, frame: Frame) -> Frame {
        self.at + (frame - self.source_in)
    }

    /// The shot frames the clip shows, first and last.
    pub fn shot_frames(&self) -> (Frame, Frame) {
        (self.source_in, self.source_in + self.duration - 1)
    }
}

/// The clip of `edit` that shows `shot` (`shot_of` names a clip's shot,
/// if it shows one). An edit with the shot twice is refused, as the master
/// edit refuses it (`master::shot_uses`): which clip's sound would be
/// ambiguous. None when no clip shows it.
pub fn shot_clip<S>(edit: &Edit<S>, shot: &Uri, shot_of: impl Fn(&S) -> Option<&str>) -> Result<Option<ShotClip>, String> {
    let mut found = None;
    for clip in edit.video.iter().flat_map(|t| &t.clips) {
        let Some(this) = shot_of(&clip.source) else { continue };
        if Uri::parse_address(this).map_err(|e| e.to_string())? != *shot {
            continue;
        }
        if found.is_some() {
            return Err(format!("{} is in the edit twice: which clip's sound it has is not clear", crate::master::shot_label(shot)));
        }
        found = Some(ShotClip { at: clip.at, duration: clip.duration, source_in: clip.source_in });
    }
    Ok(found)
}

/// The edit frames shot frames `first..=last` are, through `clip`: the
/// edit frame of `first` and how many frames. Refuses an empty range.
pub fn edit_window(clip: &ShotClip, first: Frame, last: Frame) -> Result<(Frame, u64), String> {
    if last < first {
        return Err(format!("the frames {first}-{last} are no range"));
    }
    Ok((clip.edit_frame(first), (last - first + 1) as u64))
}

/// The sample frames at `rate` that `frames` edit frames from edit frame
/// `first` are, at `fps`: the first (negative before the edit's start) and
/// how many. Windows that follow each other share no sample and leave none
/// out, as `render::mix_span` has a render's.
pub fn sample_window(first: Frame, frames: u64, fps: u32, rate: u32) -> (i64, u64) {
    let sample = |frame: i64| {
        // Exact in integers: frame * rate / fps, halves away from zero.
        let n = i128::from(frame) * i128::from(rate);
        let d = i128::from(fps.max(1));
        let q = (2 * n.abs() + d) / (2 * d);
        (if n < 0 { -q } else { q }) as i64
    };
    let start = sample(first);
    (start, (sample(first + frames as i64) - start) as u64)
}

/// The audio tracks (indices of `names`, the edit's audio tracks' names in
/// order) that `wanted` asks for: all of them when it is empty; else each
/// track whose name is one of `wanted` (case and surrounding space aside),
/// or whose place is (`A1` the first audio track, as the editor labels a
/// track without a name). A word naming no track is refused, so a typo is
/// not a silent mix without it.
pub fn select_tracks(names: &[String], wanted: &[String]) -> Result<Vec<usize>, String> {
    if wanted.is_empty() {
        return Ok((0..names.len()).collect());
    }
    let label = |i: usize| format!("A{}", i + 1);
    let mut picked = Vec::new();
    for word in wanted {
        let word = word.trim();
        let hits: Vec<usize> =
            (0..names.len()).filter(|&i| names[i].trim().eq_ignore_ascii_case(word) || label(i).eq_ignore_ascii_case(word)).collect();
        if hits.is_empty() {
            let have: Vec<String> =
                (0..names.len()).map(|i| if names[i].trim().is_empty() { label(i) } else { names[i].clone() }).collect();
            return Err(format!(
                "the edit has no audio track {word:?} (it has: {})",
                if have.is_empty() { "none".into() } else { have.join(", ") }
            ));
        }
        picked.extend(hits);
    }
    picked.sort_unstable();
    picked.dedup();
    Ok(picked)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{Track, VideoClip};

    fn edit_of(clips: &[(Frame, Frame, Frame, Option<&str>)]) -> Edit<Option<String>> {
        let mut at = 0;
        let clips = clips
            .iter()
            .map(|&(gap, duration, source_in, shot)| {
                at += gap;
                let clip = VideoClip {
                    at,
                    duration,
                    source_in,
                    source: shot.map(str::to_owned),
                    fade_in: 0,
                    fade_out: 0,
                    opacity: 1.0,
                    transform: th_edit::Transform::IDENTITY,
                    original: None,
                };
                at += duration;
                clip
            })
            .collect();
        Edit {
            fps: 24,
            resolution: [16, 9],
            start_timecode: "00:00:00:00".into(),
            video: vec![Track { name: "V1".into(), clips, color: None }],
            audio: vec![],
            markers: vec![],
        }
    }

    proptest! {
        /// The clip's own frames land on the clip: its first shot frame on
        /// `at`, its last on its last edit frame, each frame one on; a
        /// window of the shot's frames is as long as they are and starts
        /// where its first frame lands.
        #[test]
        fn shot_frames_land_on_their_clip(
            at in 0i64..10_000, duration in 1i64..500, source_in in -100i64..5000,
            first in -200i64..6000, len in 1i64..800,
        ) {
            let clip = ShotClip { at, duration, source_in };
            let (a, b) = clip.shot_frames();
            prop_assert_eq!(clip.edit_frame(a), at);
            prop_assert_eq!(clip.edit_frame(b), at + duration - 1);
            prop_assert_eq!(clip.edit_frame(first + 1), clip.edit_frame(first) + 1);
            let (start, frames) = edit_window(&clip, first, first + len - 1).unwrap();
            prop_assert_eq!(start, clip.edit_frame(first));
            prop_assert_eq!(frames, len as u64);
            prop_assert!(edit_window(&clip, first, first - 1).is_err());
        }

        /// The sample window is the frames' seconds at the rate: windows
        /// end to end are the one window of both (negative ones too), its
        /// length is within a sample of `frames / fps * rate`, and from
        /// frame 0 it is `render::mix_span`'s rounding.
        #[test]
        fn sample_windows_tile(
            first in -5000i64..5000, frames in 0u64..5000, more in 0u64..5000,
            fps in prop::sample::select(vec![24u32, 25, 30, 12, 60]), rate in prop::sample::select(vec![48_000u32, 44_100]),
        ) {
            let (start, total) = sample_window(first, frames, fps, rate);
            let (next, rest) = sample_window(first + frames as i64, more, fps, rate);
            prop_assert_eq!(next, start + total as i64);
            prop_assert_eq!(sample_window(first, frames + more, fps, rate), (start, total + rest));
            prop_assert!((total as f64 - frames as f64 / f64::from(fps) * f64::from(rate)).abs() <= 1.0);
            prop_assert!((start as f64 - first as f64 / f64::from(fps) * f64::from(rate)).abs() <= 0.5);
            if first >= 0 {
                prop_assert_eq!(start, (first as f64 / f64::from(fps) * f64::from(rate)).round() as i64);
            }
        }

        /// The clip found is the one clip showing the shot; a shot two
        /// clips show is refused; a shot none shows is None.
        #[test]
        fn the_shots_one_clip_is_found(
            clips in prop::collection::vec((0i64..20, 1i64..50, 990i64..1100, prop::sample::select(vec![Some("A"), Some("B"), Some("C"), None])), 0..6),
            wanted in prop::sample::select(vec!["A", "B", "C"]),
        ) {
            let named: Vec<(Frame, Frame, Frame, Option<String>)> =
                clips.iter().map(|&(g, d, s, n)| (g, d, s, n.map(|n| format!("entity:/shots/SQ010/{n}")))).collect();
            let as_ref: Vec<(Frame, Frame, Frame, Option<&str>)> = named.iter().map(|(g, d, s, n)| (*g, *d, *s, n.as_deref())).collect();
            let edit = edit_of(&as_ref);
            let shot = Uri::parse_address(&format!("entity:/shots/SQ010/{wanted}")).unwrap();
            let showing: Vec<&VideoClip<Option<String>>> =
                edit.video[0].clips.iter().filter(|c| c.source.as_deref() == Some(&*format!("entity:/shots/SQ010/{wanted}"))).collect();
            let got = shot_clip(&edit, &shot, |s| s.as_deref());
            match showing.len() {
                0 => prop_assert_eq!(got, Ok(None)),
                1 => prop_assert_eq!(got, Ok(Some(ShotClip { at: showing[0].at, duration: showing[0].duration, source_in: showing[0].source_in }))),
                _ => prop_assert!(got.is_err()),
            }
        }

        /// No words is every track; each word picks the tracks it names, by
        /// name or place, once each and in track order; a word naming none
        /// is refused.
        #[test]
        fn tracks_are_picked_by_name_or_place(
            names in prop::collection::vec(prop::sample::select(vec!["Dialog", "Music", "SFX", "", "dialog "]), 0..6),
            wanted in prop::collection::vec(prop::sample::select(vec!["dialog", "MUSIC", "A1", "a3", "Nope"]), 0..4),
        ) {
            let names: Vec<String> = names.into_iter().map(str::to_owned).collect();
            let wanted: Vec<String> = wanted.into_iter().map(str::to_owned).collect();
            let got = select_tracks(&names, &wanted);
            if wanted.is_empty() {
                prop_assert_eq!(got, Ok((0..names.len()).collect::<Vec<_>>()));
                return Ok(());
            }
            let hit = |w: &str, i: usize| names[i].trim().eq_ignore_ascii_case(w) || format!("A{}", i + 1).eq_ignore_ascii_case(w);
            let every_word_hits = wanted.iter().all(|w| (0..names.len()).any(|i| hit(w, i)));
            match got {
                Ok(picked) => {
                    prop_assert!(every_word_hits);
                    let want: Vec<usize> = (0..names.len()).filter(|&i| wanted.iter().any(|w| hit(w, i))).collect();
                    prop_assert_eq!(picked, want);
                }
                Err(_) => prop_assert!(!every_word_hits),
            }
        }
    }
}
