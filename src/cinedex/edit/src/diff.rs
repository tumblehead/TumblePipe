//! What changed between two versions of an edit, track by track: the
//! clips taken out and the clips put in, and the markers likewise. What
//! the agent endpoint's dry runs answer (`designs/shot-dashboard-live-
//! playblast.md` §8): the edit an op would make, said as its difference.
//!
//! Clips on a track never overlap, so a clip is the same clip when every
//! field is the same; one that moved, was trimmed or shows another take is
//! one taken out and one put in.

use serde::Serialize;

use crate::document::{Clip, Edit, Frame, Marker, TrackKind, TrackRef};

/// A track's change: the clips (by their index before) taken out and the
/// clips (by their index after) put in, as JSON. A track only one side has
/// is all taken out, or all put in.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TrackDiff {
    pub track: TrackRef,
    pub name: String,
    pub removed: Vec<(usize, serde_json::Value)>,
    pub added: Vec<(usize, serde_json::Value)>,
}

/// The difference between `before` and `after`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Diff {
    pub length_before: Frame,
    pub length_after: Frame,
    /// Only tracks that changed.
    pub tracks: Vec<TrackDiff>,
    pub markers_removed: Vec<Marker>,
    pub markers_added: Vec<Marker>,
}

impl Diff {
    /// Nothing changed.
    pub fn is_empty(&self) -> bool {
        self.tracks.is_empty()
            && self.markers_removed.is_empty()
            && self.markers_added.is_empty()
            && self.length_before == self.length_after
    }
}

/// The items of `a` not in `b`, with their indices in `a`, counted as a
/// multiset: two equal markers in `a` and one in `b` leave one missing.
fn missing<T: PartialEq + Clone>(a: &[T], b: &[T]) -> Vec<(usize, T)> {
    let mut used = vec![false; b.len()];
    let mut out = Vec::new();
    for (i, x) in a.iter().enumerate() {
        match (0..b.len()).find(|&j| !used[j] && b[j] == *x) {
            Some(j) => used[j] = true,
            None => out.push((i, x.clone())),
        }
    }
    out
}

fn track_diff<C: Clip + PartialEq + Clone + Serialize>(track: TrackRef, name: &str, before: &[C], after: &[C]) -> Option<TrackDiff> {
    let json = |list: Vec<(usize, C)>| list.into_iter().map(|(i, c)| (i, serde_json::to_value(c).expect("a clip is JSON"))).collect();
    let (removed, added): (Vec<_>, Vec<_>) = (json(missing(before, after)), json(missing(after, before)));
    (!removed.is_empty() || !added.is_empty()).then(|| TrackDiff { track, name: name.to_owned(), removed, added })
}

/// What changed from `before` to `after`.
pub fn diff<S: Clone + PartialEq + Serialize>(before: &Edit<S>, after: &Edit<S>) -> Diff {
    let mut tracks = Vec::new();
    for index in 0..before.video.len().max(after.video.len()) {
        let (b, a) = (before.video.get(index), after.video.get(index));
        let name = a.or(b).map_or("", |t| t.name.as_str());
        let empty = Vec::new();
        let changed = track_diff(TrackRef::video(index), name, b.map_or(&empty, |t| &t.clips), a.map_or(&empty, |t| &t.clips));
        tracks.extend(changed.or_else(|| (b.map(|t| &t.name) != a.map(|t| &t.name)).then(|| renamed(TrackKind::Video, index, name))));
    }
    for index in 0..before.audio.len().max(after.audio.len()) {
        let (b, a) = (before.audio.get(index), after.audio.get(index));
        let name = a.or(b).map_or("", |t| t.name.as_str());
        let empty = Vec::new();
        let changed = track_diff(TrackRef::audio(index), name, b.map_or(&empty, |t| &t.clips), a.map_or(&empty, |t| &t.clips));
        tracks.extend(changed.or_else(|| (b.map(|t| &t.name) != a.map(|t| &t.name)).then(|| renamed(TrackKind::Audio, index, name))));
    }
    Diff {
        length_before: before.length(),
        length_after: after.length(),
        tracks,
        markers_removed: missing(&before.markers, &after.markers).into_iter().map(|(_, m)| m).collect(),
        markers_added: missing(&after.markers, &before.markers).into_iter().map(|(_, m)| m).collect(),
    }
}

/// A track that only changed its name, or came or went empty.
fn renamed(kind: TrackKind, index: usize, name: &str) -> TrackDiff {
    TrackDiff { track: TrackRef { kind, index }, name: name.to_owned(), removed: Vec::new(), added: Vec::new() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::{Source, VideoClip};
    use crate::ops::tests::{Lengths, any_op, choose, edits};
    use crate::ops::{Op, apply};
    use proptest::prelude::*;

    /// `before`'s clips on a track with the diff applied: the removed taken
    /// out, the added put in at their indices.
    fn patched(before: &[serde_json::Value], track: Option<&TrackDiff>) -> Vec<serde_json::Value> {
        let Some(track) = track else { return before.to_vec() };
        let gone: Vec<usize> = track.removed.iter().map(|(i, _)| *i).collect();
        let mut out: Vec<serde_json::Value> =
            before.iter().enumerate().filter(|(i, _)| !gone.contains(i)).map(|(_, c)| c.clone()).collect();
        for (i, clip) in &track.added {
            out.insert(*i, clip.clone());
        }
        out
    }

    fn clips<C: Serialize>(list: &[C]) -> Vec<serde_json::Value> {
        list.iter().map(|c| serde_json::to_value(c).unwrap()).collect()
    }

    proptest! {
        /// Whatever ops make `after` of `before`, the diff says exactly how:
        /// each track of `before` with its removed clips taken out and its
        /// added clips put in is that track of `after`; a diff of an edit
        /// with itself is empty.
        #[test]
        fn the_diff_rebuilds_the_edit(edit in edits(), tries in prop::collection::vec(any_op(), 1..8)) {
            let mut after = edit.clone();
            for t in &tries {
                if let Some(op) = choose(&after, t)
                    && let Ok(next) = apply(&after, &op, &Lengths) {
                    after = next;
                }
            }
            let d = diff(&edit, &after);
            prop_assert!(diff(&edit, &edit).is_empty());
            prop_assert_eq!((d.length_before, d.length_after), (edit.length(), after.length()));
            let empty_v: Vec<VideoClip<Source>> = Vec::new();
            for index in 0..edit.video.len().max(after.video.len()) {
                let track = d.tracks.iter().find(|t| t.track == TrackRef::video(index));
                let before = clips(edit.video.get(index).map_or(&empty_v, |t| &t.clips));
                let want = clips(after.video.get(index).map_or(&empty_v, |t| &t.clips));
                prop_assert_eq!(patched(&before, track), want);
            }
            for index in 0..edit.audio.len().max(after.audio.len()) {
                let track = d.tracks.iter().find(|t| t.track == TrackRef::audio(index));
                let before = edit.audio.get(index).map(|t| clips(&t.clips)).unwrap_or_default();
                let want = after.audio.get(index).map(|t| clips(&t.clips)).unwrap_or_default();
                prop_assert_eq!(patched(&before, track), want);
            }
            let mut markers = edit.markers.clone();
            for gone in &d.markers_removed {
                let at = markers.iter().position(|m| m == gone).unwrap();
                markers.remove(at);
            }
            markers.extend(d.markers_added.iter().cloned());
            let order = |a: &Marker, b: &Marker| (a.at, &a.note).cmp(&(b.at, &b.note));
            markers.sort_by(order);
            let mut want = after.markers.clone();
            want.sort_by(order);
            prop_assert_eq!(markers, want);
        }
    }

    /// Equal markers count one by one: a second marker like one there is
    /// one put in, and taking one of two away is one taken out.
    #[test]
    fn equal_markers_are_counted() {
        let mut one = Edit::<Source>::new(24, [1920, 1080], "01:00:00:00");
        one.markers = vec![Marker { at: 5, note: "m".into() }];
        let mut two = one.clone();
        two.markers.push(Marker { at: 5, note: "m".into() });
        assert_eq!(diff(&one, &two).markers_added, vec![Marker { at: 5, note: "m".into() }]);
        assert_eq!(diff(&two, &one).markers_removed, vec![Marker { at: 5, note: "m".into() }]);
        assert!(diff(&two, &two).is_empty());
    }

    #[test]
    fn a_lift_is_one_clip_taken_out() {
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(crate::document::Track {
            name: "V1".into(),
            clips: vec![
                crate::ops::tests::vclip(0, 10, 0, crate::ops::tests::media(5)),
                crate::ops::tests::vclip(10, 10, 0, crate::ops::tests::media(5)),
            ],
        });
        let lifted = apply(&edit, &Op::Lift { clip: crate::document::ClipRef { track: TrackRef::video(0), index: 1 } }, &Lengths).unwrap();
        let d = diff(&edit, &lifted);
        assert_eq!(d.tracks.len(), 1);
        assert_eq!(d.tracks[0].removed.iter().map(|(i, _)| *i).collect::<Vec<_>>(), vec![1]);
        assert!(d.tracks[0].added.is_empty());
    }
}
