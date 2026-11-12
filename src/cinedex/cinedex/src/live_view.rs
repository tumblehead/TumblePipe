//! Live view: Houdini asks the player to show the shot an animator renders
//! live, beside the shots around it (`designs/shot-dashboard-live-
//! playblast.md` §5.3).
//!
//! On the master edit, the view is the shot's first clip (the earliest on
//! the video tracks), looping from the start of the clip before it on its
//! track to the end of the clip after it (the edit's ends where there is
//! none), the playhead on the frame Houdini is at. The window comes to the
//! front without taking the keyboard from Houdini (`crate::raise`; a
//! minimised one on Linux does take it, the only way GNOME restores it).
//!
//! The loop is kept as the clips it covers, not as timeline positions
//! ([`Loop`]): the cut can change while it loops (a clip trimmed, moved,
//! inserted or split before it or in it), and the loop then still covers
//! the same clips, wherever they now are.

use std::cmp::Reverse;

use th_edit::document::{Source, VideoClip, WorkingEdit};

/// What a Live view shows: the timeline positions it loops over
/// (`start..end`) when it is asked for, where the playhead goes, and the
/// loop as the clips it covers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct View {
    pub(crate) start: usize,
    pub(crate) end: usize,
    pub(crate) playhead: usize,
    pub(crate) looping: Loop,
}

/// What a clip shows, whichever take or version of it: the shot, the media
/// item, the slug's text.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Shows {
    Shot(String),
    Media(String),
    Slug(String),
}

impl Shows {
    fn of(source: &Source) -> Self {
        match source {
            Source::Shot { shot, .. } => Shows::Shot(shot.clone()),
            Source::Media { item, .. } => Shows::Media(item.clone()),
            Source::Slug { text } => Shows::Slug(text.clone()),
        }
    }
}

/// A clip of the cut, named by what it shows (its shot and source frames)
/// rather than where it sits, as it was when last seen: the cut keeps no
/// clip ids, and its clips move, change track, are trimmed and split.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ClipId {
    track: usize,
    shows: Shows,
    source_in: i64,
    duration: i64,
    at: i64,
}

impl ClipId {
    fn of(track: usize, clip: &VideoClip<Source>) -> Self {
        Self { track, shows: Shows::of(&clip.source), source_in: clip.source_in, duration: clip.duration, at: clip.at }
    }

    /// How many of its source frames `clip` shows (none: another clip).
    fn overlap(&self, clip: &VideoClip<Source>) -> i64 {
        if clip.duration <= 0 || Shows::of(&clip.source) != self.shows {
            return 0;
        }
        (self.source_in + self.duration).min(clip.source_in + clip.duration) - self.source_in.max(clip.source_in)
    }

    /// The clips of `track` (`clips`) that continue `best` in its source on
    /// either side, in the same order in the edit, with it, and still show
    /// some of this clip's frames (the pieces of a split, or of an insert
    /// in it), leaving out those `taken`.
    fn pieces(&self, clips: &[VideoClip<Source>], best: usize, taken: &dyn Fn(usize) -> bool) -> Vec<usize> {
        let mut pieces = vec![best];
        let ours = |i: usize, c: &VideoClip<Source>| !taken(i) && self.overlap(c) > 0;
        loop {
            let (first, last) = (&clips[pieces[0]], &clips[pieces[pieces.len() - 1]]);
            let before = clips
                .iter()
                .enumerate()
                .position(|(i, c)| ours(i, c) && c.source_in + c.duration == first.source_in && c.at + c.duration <= first.at);
            let after = clips
                .iter()
                .enumerate()
                .position(|(i, c)| ours(i, c) && c.source_in == last.source_in + last.duration && c.at >= last.at + last.duration);
            match (before, after) {
                (Some(i), _) => pieces.insert(0, i),
                (None, Some(i)) => pieces.push(i),
                (None, None) => return pieces,
            }
        }
    }
}

/// Where each of `ids` is in `edit` now: its track and its pieces there, in
/// order (more than one when it was split, or an insert opened room in it),
/// or None when no clip shows any of its frames (it was deleted).
///
/// Each clip of the cut is at most one of them (a piece slipped or trimmed
/// onto the frames of the next is still itself). The pairs are taken best
/// first: the clip showing most of the id's frames; on a tie (frames shown
/// twice) the one starting nearest its first frame, then the nearest to
/// where it was, then the one on its own track. Then each takes the clips
/// left on its track that continue it in its source.
fn resolve(ids: &[ClipId], edit: &WorkingEdit) -> Vec<Option<(usize, Vec<usize>)>> {
    let clips = || edit.video.iter().enumerate().flat_map(|(t, track)| track.clips.iter().enumerate().map(move |(i, c)| (t, i, c)));
    let mut pairs: Vec<_> = ids
        .iter()
        .enumerate()
        .flat_map(|(k, id)| clips().map(move |(t, i, c)| (k, id, t, i, c)))
        .filter(|(_, id, _, _, c)| id.overlap(c) > 0)
        .map(|(k, id, t, i, c)| {
            let key = (Reverse(id.overlap(c)), (c.source_in - id.source_in).abs(), (c.at - id.at).abs(), t != id.track);
            (key, k, t, i)
        })
        .collect();
    pairs.sort();
    let mut best: Vec<Option<(usize, usize)>> = vec![None; ids.len()];
    let mut taken: std::collections::BTreeSet<(usize, usize)> = Default::default();
    for (_, k, t, i) in pairs {
        if best[k].is_none() && taken.insert((t, i)) {
            best[k] = Some((t, i));
        }
    }
    let mut found = Vec::with_capacity(ids.len());
    for (id, best) in ids.iter().zip(best) {
        found.push(best.map(|(track, index)| {
            let pieces = id.pieces(&edit.video[track].clips, index, &|i| taken.contains(&(track, i)));
            taken.extend(pieces.iter().map(|&i| (track, i)));
            (track, pieces)
        }));
    }
    found
}

/// A loop over clips of the cut: from `head` frames into the first to
/// `tail` frames before the end of the last, covering the clips between,
/// resolved against the cut as it is now ([`Loop::follow`]).
///
/// A clip of the loop deleted from the cut leaves the loop to the others
/// (it shrinks to them); an undo that brings the clip back brings it back
/// into the loop. When none of its clips is left the loop is over.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Loop {
    clips: Vec<ClipId>,
    head: i64,
    tail: i64,
    /// Where it loops (`start..end`), as of the last [`Loop::follow`].
    span: (usize, usize),
}

impl Loop {
    /// The loop over `clips` of `edit` (track, index), first to last, from
    /// the first's start to the last's end.
    fn over(edit: &WorkingEdit, clips: &[(usize, usize)]) -> Option<Self> {
        let clips = clips.iter().map(|&(t, i)| ClipId::of(t, &edit.video[t].clips[i])).collect();
        let mut looping = Self { clips, head: 0, tail: 0, span: (0, 0) };
        looping.follow(edit).then_some(looping)
    }

    /// Where it loops (`start..end`) on the timeline.
    pub(crate) fn span(&self) -> (usize, usize) {
        self.span
    }

    /// Resolve the loop against `edit` (the cut after a change), taking
    /// its clips as they now are; false when none of them is left, or they
    /// no longer make a loop (the loop is then over).
    pub(crate) fn follow(&mut self, edit: &WorkingEdit) -> bool {
        let found = resolve(&self.clips, edit);
        let extent = |(track, pieces): &(usize, Vec<usize>)| {
            let clips = &edit.video[*track].clips;
            let (first, last) = (&clips[pieces[0]], &clips[pieces[pieces.len() - 1]]);
            (first.at.max(0), last.at.max(0) + last.duration, first.duration, last.duration)
        };
        let extents: Vec<_> = found.iter().flatten().map(extent).collect();
        let (Some(&start), Some(&end)) = (extents.iter().map(|e| e.0).min().as_ref(), extents.iter().map(|e| e.1).max().as_ref()) else {
            return false;
        };
        // The offsets hold while the clip they are into is still the first
        // (last) one of the loop.
        let start = match found.first().and_then(|f| f.as_ref()).map(extent) {
            Some((at, _, duration, _)) if at == start => at + self.head.clamp(0, duration - 1),
            _ => start,
        };
        let end = match found.last().and_then(|f| f.as_ref()).map(extent) {
            Some((_, last_end, _, duration)) if last_end == end => last_end - self.tail.clamp(0, duration - 1),
            _ => end,
        };
        if start >= end {
            return false;
        }
        // Each clip as it now is; a clip now in pieces is each piece from
        // here on (a piece slipped or moved later is still followed). One
        // deleted keeps what it was, for an undo to bring it back.
        let clips = std::mem::take(&mut self.clips);
        for (id, found) in clips.into_iter().zip(found) {
            match found {
                Some((track, pieces)) => self.clips.extend(pieces.iter().map(|&i| ClipId::of(track, &edit.video[track].clips[i]))),
                None => self.clips.push(id),
            }
        }
        self.span = (start as usize, end as usize);
        true
    }
}

/// The view of `shot` (its address, `entity:/shots/...`) in `edit`, at shot
/// frame `frame` (its clip's first frame when None, the nearest of its
/// frames when outside it); None when no video clip of the edit shows it.
pub(crate) fn shot_view(edit: &WorkingEdit, shot: &str, frame: Option<i64>) -> Option<View> {
    let is_shot = |source: &Source| matches!(source, Source::Shot { shot: s, .. } if s == shot);
    let (track, index) = edit
        .video
        .iter()
        .enumerate()
        .flat_map(|(t, track)| track.clips.iter().enumerate().filter(|(_, c)| is_shot(&c.source)).map(move |(i, c)| (c.at, t, i)))
        .min()
        .map(|(_, t, i)| (t, i))?;
    let clips = &edit.video[track].clips;
    let clip = &clips[index];
    let (at, duration) = (clip.at.max(0), clip.duration.max(1));
    let end_of = |c: &VideoClip<Source>| c.at.max(0) + c.duration.max(0);
    let enumerated = || clips.iter().enumerate();
    let before = enumerated().filter(|(_, c)| end_of(c) <= at && c.duration > 0).max_by_key(|(_, c)| c.at);
    let after = enumerated().filter(|(_, c)| c.at >= at + duration && c.duration > 0).min_by_key(|(_, c)| c.at);
    let start = before.map_or(at, |(_, c)| c.at.max(0));
    let end = after.map_or(at + duration, |(_, c)| end_of(c));
    let offset = frame.map_or(0, |f| (f - clip.source_in).clamp(0, duration - 1));
    let covered: Vec<(usize, usize)> =
        before.map(|(i, _)| i).into_iter().chain([index]).chain(after.map(|(i, _)| i)).map(|i| (track, i)).collect();
    let looping = Loop::over(edit, &covered)?;
    Some(View { start: start as usize, end: end as usize, playhead: (at + offset) as usize, looping })
}

/// Bring this process's window to the front without taking the keyboard
/// focus (the animator keeps typing in Houdini) where the OS allows it,
/// restoring it when it is minimised (`crate::raise`). True when only the
/// compositor can (Wayland): the caller then asks it for attention. What
/// could not be done goes to the log.
pub(crate) fn raise_without_focus() -> bool {
    match crate::raise::raise_without_focus() {
        Ok(crate::raise::Raise::Raised(_)) => false,
        Ok(crate::raise::Raise::Compositor) => true,
        Err(e) => {
            eprintln!("cinedex: Live view could not raise the window: {e}");
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{ClipRef, Rule, Track, TrackRef};
    use th_edit::ops::{self, Available, Edge, NewClip, Op, Sources};

    fn shot(name: &str) -> Source {
        Source::Shot { shot: format!("entity:/shots/S/{name}"), take: Rule::best() }
    }

    /// A video track of clips end to end (with gaps), each `(name, duration,
    /// gap before)`, shots from frame 1001.
    fn track(clips: &[(String, i64, i64)]) -> Track<VideoClip<Source>> {
        let mut at = 0;
        let mut out = Vec::new();
        for (name, duration, gap) in clips {
            at += gap;
            out.push(VideoClip { at, duration: *duration, source_in: 1001, source: shot(name), fade_in: 0, fade_out: 0, opacity: 1.0 });
            at += duration;
        }
        Track { name: "V1".into(), clips: out }
    }

    fn clips() -> impl Strategy<Value = Vec<(String, i64, i64)>> {
        prop::collection::vec((prop::sample::select(vec!["A", "B", "C", "D"]).prop_map(str::to_owned), 1i64..50, 0i64..5), 1..8)
    }

    fn edit(video: Vec<Track<VideoClip<Source>>>) -> WorkingEdit {
        WorkingEdit { fps: 24, resolution: [1920, 1080], start_timecode: "00:00:00:00".into(), video, audio: vec![], markers: vec![] }
    }

    proptest! {
        /// On one track: the view loops from the start of the clip before
        /// the shot's first clip to the end of the one after (the clip's own
        /// ends where it has no neighbour), and the playhead is on Houdini's
        /// frame, kept inside the clip.
        #[test]
        fn the_view_loops_the_neighbours_around_the_shots_first_clip(
            clips in clips(),
            want in prop::sample::select(vec!["A", "B", "C", "D", "Z"]),
            frame in prop::option::of(990i64..1060),
        ) {
            let edit = edit(vec![track(&clips)]);
            let address = format!("entity:/shots/S/{want}");
            let view = shot_view(&edit, &address, frame);
            let track = &edit.video[0].clips;
            let Some(index) = track.iter().position(|c| c.source == shot(want)) else {
                prop_assert!(view.is_none());
                return Ok(());
            };
            let view = view.expect("a clip shows the shot");
            let clip = &track[index];
            let start = if index > 0 { track[index - 1].at } else { clip.at };
            let end = track.get(index + 1).map_or(clip.at + clip.duration, |c| c.at + c.duration);
            prop_assert_eq!((view.start, view.end), (start as usize, end as usize));
            prop_assert_eq!(view.looping.span(), (view.start, view.end));
            let offset = frame.map_or(0, |f| (f - 1001).clamp(0, clip.duration - 1));
            prop_assert_eq!(view.playhead, (clip.at + offset) as usize);
            prop_assert!(view.start <= view.playhead && view.playhead < view.end);
        }

        /// Over several tracks the shot's earliest clip is the one shown,
        /// whichever track it is on.
        #[test]
        fn the_earliest_clip_of_the_shot_is_shown(lower in clips(), upper in clips()) {
            let edit = edit(vec![track(&lower), track(&upper)]);
            let earliest = edit.video.iter().flat_map(|t| t.clips.iter()).filter(|c| c.source == shot("A")).map(|c| c.at).min();
            let view = shot_view(&edit, "entity:/shots/S/A", None);
            prop_assert_eq!(view.map(|v| v.playhead as i64), earliest);
        }
    }

    // The loop as the cut changes. Clips are named C0, C1, ... (each name
    // once, so a clip's content is its name) on V1, over an empty V2, with
    // source frames from anywhere; inserted clips are N0, N1, ...

    /// Every source has every frame: the ops' checks never refuse for want
    /// of frames.
    struct AllFrames;

    impl Sources<Source> for AllFrames {
        fn available(&self, _source: &Source) -> Available {
            Available::Any
        }
    }

    /// A cut of clips `(duration, gap before, source in)` named C0, C1...
    fn cut(clips: &[(i64, i64, i64)]) -> WorkingEdit {
        let mut at = 0;
        let mut out = Vec::new();
        for (i, (duration, gap, source_in)) in clips.iter().enumerate() {
            at += gap;
            out.push(VideoClip {
                at,
                duration: *duration,
                source_in: *source_in,
                source: shot(&format!("C{i}")),
                fade_in: 0,
                fade_out: 0,
                opacity: 1.0,
            });
            at += duration;
        }
        edit(vec![Track { name: "V1".into(), clips: out }, Track { name: "V2".into(), clips: vec![] }])
    }

    fn cut_clips() -> impl Strategy<Value = Vec<(i64, i64, i64)>> {
        prop::collection::vec((2i64..40, 0i64..4, 900i64..1100), 2..9)
    }

    /// An op to try on the cut: `(kind, which clip, a size, where)`.
    type Change = (u8, prop::sample::Index, i64, prop::sample::Index);

    fn changes() -> impl Strategy<Value = Vec<Change>> {
        prop::collection::vec((0u8..6, any::<prop::sample::Index>(), -30i64..30, any::<prop::sample::Index>()), 1..5)
    }

    fn name_of(clip: &VideoClip<Source>) -> String {
        match &clip.source {
            Source::Shot { shot, .. } => shot.rsplit('/').next().unwrap_or_default().to_owned(),
            _ => String::new(),
        }
    }

    /// Where the clips named `names` are in `edit`, first start to last end.
    fn extent_of(edit: &WorkingEdit, names: &[String]) -> Option<(usize, usize)> {
        let spans: Vec<(i64, i64)> =
            edit.video.iter().flat_map(|t| &t.clips).filter(|c| names.contains(&name_of(c))).map(|c| (c.at, c.at + c.duration)).collect();
        Some((spans.iter().map(|s| s.0).min()? as usize, spans.iter().map(|s| s.1).max()? as usize))
    }

    /// `change` made to `edit` (the `n`th change; an insert is named Nn),
    /// with its edit point: None when the op is refused (or makes nothing).
    fn make(edit: &WorkingEdit, change: &Change, n: usize) -> Option<(WorkingEdit, i64)> {
        let (kind, which, size, place) = change;
        let v1 = &edit.video[0].clips;
        if v1.is_empty() {
            return None;
        }
        let index = which.index(v1.len());
        let clip = ClipRef { track: TrackRef::video(0), index };
        let target = &v1[index];
        let op = match kind {
            // A ripple insert anywhere: between clips or in one.
            0 => {
                let at = place.index(edit.length() as usize + 1) as i64;
                let new = VideoClip {
                    at,
                    duration: size.abs() + 1,
                    source_in: 1001,
                    source: shot(&format!("N{n}")),
                    fade_in: 0,
                    fade_out: 0,
                    opacity: 1.0,
                };
                Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(new) }
            }
            1 => Op::Trim { clip, edge: if place.index(2) == 0 { Edge::Head } else { Edge::Tail }, delta: *size },
            2 => Op::Split { clip, at: target.at + place.index(target.duration as usize) as i64 },
            // A move to V2, where it is.
            3 => Op::Move { clip, to: TrackRef::video(1), at: target.at },
            4 => Op::Roll { clip, delta: *size },
            _ => Op::Slip { clip, delta: size.signum() * size.abs().min(target.duration - 1) },
        };
        let point = match kind {
            0 => match &op {
                Op::Insert { clip: NewClip::Video(c), .. } => c.at,
                _ => unreachable!(),
            },
            _ => target.at,
        };
        ops::apply(edit, &op, &AllFrames).ok().map(|next| (next, point))
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(8192))]

        /// Whatever is done to the cut while it loops (clips inserted,
        /// trimmed, split, moved to another track, rolled or slipped,
        /// before the loop, in it or after it, one change after another),
        /// the loop covers the same clips: from where the first of them now
        /// starts to where the last now ends; a clip inserted in it is
        /// covered, one inserted before or after it is not.
        #[test]
        fn the_loop_keeps_its_clips_as_the_cut_changes(
            clips in cut_clips(),
            which in any::<prop::sample::Index>(),
            changes in changes(),
        ) {
            let mut edit = cut(&clips);
            let k = which.index(clips.len());
            let view = shot_view(&edit, &format!("entity:/shots/S/C{k}"), None).expect("a clip shows the shot");
            let names: Vec<String> = (k.saturating_sub(1)..=(k + 1).min(clips.len() - 1)).map(|i| format!("C{i}")).collect();
            let mut looping = view.looping;
            for (n, change) in changes.iter().enumerate() {
                let Some((next, point)) = make(&edit, change, n) else { continue };
                let (old_start, old_end) = looping.span();
                prop_assert!(looping.follow(&next), "the loop is lost after {:?}", change);
                let expected = extent_of(&next, &names).expect("no clip of the loop was deleted");
                prop_assert_eq!(looping.span(), expected, "after {:?}", change);
                if change.0 == 0 {
                    let inserted = format!("N{n}");
                    let covered = next.video[0].clips.iter().filter(|c| name_of(c) == inserted).all(|c| {
                        looping.span().0 <= c.at as usize && (c.at + c.duration) as usize <= looping.span().1
                    });
                    let inside = (old_start as i64) < point && point < old_end as i64;
                    prop_assert_eq!(covered, inside, "an insert at {} with the loop at {:?}", point, (old_start, old_end));
                }
                edit = next;
            }
        }

        /// Clips of the loop deleted (ripple or lift): the loop shrinks to
        /// those left, and an undo (the cut as it was) brings the deleted
        /// back into it; with none of them left the loop is over.
        #[test]
        fn a_deleted_clip_leaves_the_loop_to_the_others(
            clips in cut_clips(),
            which in any::<prop::sample::Index>(),
            gone in prop::collection::btree_set(0usize..3, 1..=3),
            ripple in any::<bool>(),
        ) {
            let edit = cut(&clips);
            let k = which.index(clips.len());
            let view = shot_view(&edit, &format!("entity:/shots/S/C{k}"), None).unwrap();
            let names: Vec<String> = (k.saturating_sub(1)..=(k + 1).min(clips.len() - 1)).map(|i| format!("C{i}")).collect();
            let gone: std::collections::BTreeSet<String> = gone.into_iter().map(|i| names[i % names.len()].clone()).collect();
            let mut next = edit.clone();
            for name in &gone {
                let index = next.video[0].clips.iter().position(|c| name_of(c) == *name).unwrap();
                let clip = ClipRef { track: TrackRef::video(0), index };
                let op = if ripple { Op::RippleDelete { clip } } else { Op::Lift { clip } };
                next = ops::apply(&next, &op, &AllFrames).unwrap();
            }
            let left: Vec<String> = names.iter().filter(|n| !gone.contains(*n)).cloned().collect();
            let mut looping = view.looping.clone();
            let followed = looping.follow(&next);
            prop_assert_eq!(followed, !left.is_empty());
            if followed {
                prop_assert_eq!(looping.span(), extent_of(&next, &left).unwrap());
                prop_assert!(looping.follow(&edit), "the undo brings the loop back");
                prop_assert_eq!(looping.span(), (view.start, view.end));
            }
        }
    }

    /// The same shot used twice with the same frames: the loop keeps the
    /// one it was on (the nearest to where it was), not the other.
    #[test]
    fn the_same_frames_twice_keep_their_own_place() {
        let clip = |at: i64, name: &str| VideoClip {
            at,
            duration: 10,
            source_in: 1001,
            source: shot(name),
            fade_in: 0,
            fade_out: 0,
            opacity: 1.0,
        };
        let mut edit = edit(vec![Track { name: "V1".into(), clips: vec![clip(0, "A"), clip(10, "B"), clip(20, "A"), clip(30, "C")] }]);
        let mut looping = Loop::over(&edit, &[(0, 2)]).unwrap();
        assert_eq!(looping.span(), (20, 30));
        // Four frames inserted at the start: both A move on, the loop with
        // the second (now 4 frames from where it was, the first 16).
        for c in &mut edit.video[0].clips {
            c.at += 4;
        }
        assert!(looping.follow(&edit));
        assert_eq!(looping.span(), (24, 34));
    }

    /// Head and tail offsets hold into the first and last clip, kept inside
    /// them when the clip is trimmed shorter.
    #[test]
    fn offsets_hold_into_the_end_clips() {
        let clip = |at: i64, duration: i64, name: &str| VideoClip {
            at,
            duration,
            source_in: 1001,
            source: shot(name),
            fade_in: 0,
            fade_out: 0,
            opacity: 1.0,
        };
        let mut edit = edit(vec![Track { name: "V1".into(), clips: vec![clip(0, 20, "A"), clip(20, 20, "B")] }]);
        let mut looping = Loop::over(&edit, &[(0, 0), (0, 1)]).unwrap();
        (looping.head, looping.tail) = (5, 3);
        assert!(looping.follow(&edit));
        assert_eq!(looping.span(), (5, 37));
        edit.video[0].clips[1].duration = 2;
        assert!(looping.follow(&edit));
        assert_eq!(looping.span(), (5, 21), "the tail offset is kept inside B's two frames");
    }

    /// A piece slipped onto frames the next piece shows, then trimmed
    /// shorter than what they share: each clip of the loop is still its own
    /// piece (a clip of the cut is one of them at most), so the loop keeps
    /// both (found by the loop prop, 2026-11-12).
    #[test]
    fn pieces_sharing_frames_stay_themselves() {
        let mut edit = cut(&[(22, 0, 900), (2, 0, 900)]);
        let mut looping = shot_view(&edit, "entity:/shots/S/C0", None).unwrap().looping;
        let c0 = ClipRef { track: TrackRef::video(0), index: 0 };
        let changes =
            [Op::Split { clip: c0, at: 13 }, Op::Slip { clip: c0, delta: 3 }, Op::Trim { clip: c0, edge: Edge::Tail, delta: -11 }];
        for change in &changes {
            edit = ops::apply(&edit, change, &AllFrames).unwrap();
            assert!(looping.follow(&edit));
        }
        // C0 903-904 at 0, C0 913-921 at 2, C1 at 11.
        assert_eq!(looping.span(), (0, 13));
    }
}
