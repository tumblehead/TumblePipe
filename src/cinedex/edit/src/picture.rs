//! What is on screen, and in the mix, at an edit frame
//! (`designs/edit-entity.md` §2.3).
//!
//! The top-most clip whose media can be played shows. Inside its fade, or
//! at an opacity under 1 (#60), it blends over what the same rule gives for
//! the tracks below (black when nothing): a dissolve is an upper clip
//! fading in over a lower one, a fade to or from black one over nothing. A
//! clip that cannot play (a shot with no take yet, a planned shot), or is
//! at no opacity, is transparent. Audio tracks mix.

use crate::document::{Clip, Edit, Frame};

/// The engine's answer: can it show `source` at source frame `frame`?
pub trait Playable<S> {
    fn playable(&self, source: &S, frame: Frame) -> bool;
}

/// One video layer of a frame.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Layer {
    /// Its video track and clip index there.
    pub track: usize,
    pub clip: usize,
    /// The source frame it shows.
    pub frame: Frame,
    /// How much of it shows over the layers below: its clip's opacity
    /// times its fade (1 outside its fades).
    pub opacity: f32,
}

/// The layers of edit frame `at`, bottom first: the last is the top-most
/// playable clip; each one below shows through the one above's fade.
/// Empty: black.
pub fn layers_at<S: Clone>(edit: &Edit<S>, at: Frame, playable: &impl Playable<S>) -> Vec<Layer> {
    let mut out = Vec::new();
    for (track, t) in edit.video.iter().enumerate().rev() {
        let Some(clip) = t.clips.iter().position(|c| c.covers(at)) else { continue };
        let c = &t.clips[clip];
        let frame = c.source_in + (at - c.at);
        // A clip at no opacity is not there, as one that cannot play.
        if c.opacity <= 0.0 || !playable.playable(&c.source, frame) {
            continue;
        }
        let opacity = c.opacity as f32 * fade(c, at);
        out.push(Layer { track, clip, frame, opacity });
        if opacity >= 1.0 {
            break;
        }
    }
    out.reverse();
    out
}

/// One audio clip in the mix of a frame.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Voice {
    pub track: usize,
    pub clip: usize,
    pub frame: Frame,
    /// Its linear gain: its level with its fades.
    pub gain: f32,
}

/// The audio clips heard at edit frame `at`, on every audio track.
pub fn sound_at<S: Clone>(edit: &Edit<S>, at: Frame) -> Vec<Voice> {
    let mut out = Vec::new();
    for (track, t) in edit.audio.iter().enumerate() {
        if let Some(clip) = t.clips.iter().position(|c| c.covers(at)) {
            let c = &t.clips[clip];
            let level = 10f32.powf(c.gain_db as f32 / 20.0);
            out.push(Voice { track, clip, frame: c.source_in + (at - c.at), gain: level * fade(c, at) });
        }
    }
    out
}

/// How far into its fades `c` is at edit frame `at`: 1 outside them,
/// rising linearly through a fade in and falling through a fade out (frame
/// `k` of an `n`-frame fade is `(k + 0.5) / n`).
pub fn fade<C: Clip>(c: &C, at: Frame) -> f32 {
    let into = at - c.at();
    let left = c.end() - 1 - at;
    let mut opacity = 1.0f32;
    if into < c.fade_in() {
        opacity = opacity.min((into as f32 + 0.5) / c.fade_in() as f32);
    }
    if left < c.fade_out() {
        opacity = opacity.min((left as f32 + 0.5) / c.fade_out() as f32);
    }
    opacity
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::{Source, Track};
    use crate::ops::tests::{edits, vclip};
    use proptest::prelude::*;

    /// Slugs never play (stand-ins for shots without a take); media does.
    struct MediaOnly;

    impl Playable<Source> for MediaOnly {
        fn playable(&self, source: &Source, _: Frame) -> bool {
            matches!(source, Source::Media { .. })
        }
    }

    #[test]
    fn a_dissolve_blends_the_upper_clip_over_the_lower() {
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        let media = |n: &str| Source::Media { item: n.into(), version: crate::VersionRule::Latest };
        edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 20, 0, media("a"))] });
        let mut upper = vclip(16, 10, 100, media("b"));
        upper.fade_in = 4;
        edit.video.push(Track { name: "V2".into(), clips: vec![upper] });
        let at = |f| layers_at(&edit, f, &MediaOnly);
        assert_eq!(at(10).len(), 1);
        let mid = at(17);
        assert_eq!((mid[0].track, mid[1].track), (0, 1));
        assert_eq!(mid[1].frame, 101);
        assert!((mid[1].opacity - 0.375).abs() < 1e-6);
        assert_eq!(at(20).len(), 1, "after the fade only the upper shows");
        assert!(at(30).is_empty(), "black past the end");
    }

    proptest! {
        /// The top layer is the top-most playable clip at that frame; every
        /// layer under it is there because the one above is fading; outside
        /// fades and opacity there is exactly one layer, whole. A clip at
        /// no opacity is transparent; each layer shows its clip's opacity
        /// times its fade (#60).
        #[test]
        fn the_top_playable_clip_shows(
            edit in edits(),
            at in 0i64..200,
            fades in prop::collection::vec((0i64..6, 0i64..6, prop::sample::select(vec![1.0f64, 1.0, 0.6, 0.25, 0.0])), 0..12),
        ) {
            let mut edit = edit;
            let mut i = 0;
            for t in &mut edit.video {
                for c in &mut t.clips {
                    if let Some(&(a, b, opacity)) = fades.get(i) {
                        if a + b <= c.duration {
                            c.fade_in = a;
                            c.fade_out = b;
                        }
                        c.opacity = opacity;
                    }
                    i += 1;
                }
            }
            let layers = layers_at(&edit, at, &MediaOnly);
            let top = edit.video.iter().enumerate().rev().find_map(|(t, track)| {
                track.clips.iter().position(|c| c.covers(at) && c.opacity > 0.0 && matches!(c.source, Source::Media { .. })).map(|c| (t, c))
            });
            prop_assert_eq!(layers.last().map(|l| (l.track, l.clip)), top);
            for pair in layers.windows(2) {
                prop_assert!(pair[1].opacity < 1.0);
                prop_assert!(pair[0].track < pair[1].track);
            }
            for l in &layers {
                let c = &edit.video[l.track].clips[l.clip];
                prop_assert!(l.opacity > 0.0 && l.opacity <= 1.0);
                prop_assert_eq!(l.opacity, c.opacity as f32 * fade(c, at));
            }
            if let Some(last) = layers.last()
                && last.opacity >= 1.0 {
                    prop_assert_eq!(layers.len(), 1);
                }
        }
    }
}
