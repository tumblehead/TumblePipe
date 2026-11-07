//! What the editor does to an edit (`designs/edit-entity.md` §2.4).
//!
//! Each [`Op`] is applied to a copy: the result is checked (the document's
//! invariants, [`Edit::problem`], and every clip it changed or added against
//! its source's available frames, [`Sources`]) and the edit is replaced only
//! when it passes. An op that would break either is refused, never clamped.
//!
//! *Ripple* ops change the edit's length and move everything after their
//! edit point on **every** track, markers included, so what was in sync
//! stays in sync: room opened at a point splits whatever spans it, and a
//! span taken out is taken out of every track (a clip across it keeps the
//! frames either side, as two clips). The others move nothing else.

use std::fmt;

use crate::document::{AudioClip, Clip, ClipRef, Edit, Frame, Marker, TrackKind, TrackRef, VideoClip};

/// The source frames a clip may show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Available {
    /// `first ..= last`.
    Frames(Frame, Frame),
    /// Any (a slug).
    Any,
    /// None: the source is not there.
    Nothing,
}

/// The pipeline's answer to what a source has.
pub trait Sources<S> {
    fn available(&self, source: &S) -> Available;
}

/// A clip of either kind, to add.
#[derive(Debug, Clone, PartialEq)]
pub enum NewClip<S> {
    Video(VideoClip<S>),
    Audio(AudioClip<S>),
}

impl<S> NewClip<S> {
    fn kind(&self) -> TrackKind {
        match self {
            NewClip::Video(_) => TrackKind::Video,
            NewClip::Audio(_) => TrackKind::Audio,
        }
    }
}

/// Which end of a clip a trim moves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Edge {
    Head,
    Tail,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Op<S> {
    /// Put a clip in free space on a track (its `at`): nothing moves.
    Place {
        track: TrackRef,
        clip: NewClip<S>,
    },
    /// Ripple insert: open the clip's length at its `at` on every track,
    /// then put it there.
    Insert {
        track: TrackRef,
        clip: NewClip<S>,
    },
    /// Take a clip out, leaving a gap.
    Lift {
        clip: ClipRef,
    },
    /// Ripple delete: take the clip's span out of every track.
    RippleDelete {
        clip: ClipRef,
    },
    /// Move a clip to free space at `at` on a track of its kind.
    Move {
        clip: ClipRef,
        to: TrackRef,
        at: Frame,
    },
    /// Ripple trim: move an edge by `delta` frames (positive: later),
    /// moving everything after the edit point with it.
    Trim {
        clip: ClipRef,
        edge: Edge,
        delta: Frame,
    },
    /// Roll the cut between a clip and the next one on its track, which it
    /// must touch, by `delta` (positive: later): the edit's length holds.
    Roll {
        clip: ClipRef,
        delta: Frame,
    },
    /// Slip: show source frames `delta` later in the same span.
    Slip {
        clip: ClipRef,
        delta: Frame,
    },
    /// Split a clip in two at edit frame `at`.
    Split {
        clip: ClipRef,
        at: Frame,
    },
    /// Join a clip with the next on its track: they must touch in the edit
    /// and in their source, with no fade between them.
    Join {
        clip: ClipRef,
    },
    SetFades {
        clip: ClipRef,
        fade_in: Frame,
        fade_out: Frame,
    },
    /// Show another source in the same span (a shot's other take): its
    /// source frames must be there.
    SetSource {
        clip: ClipRef,
        source: S,
    },
    /// An audio clip's level.
    SetGain {
        clip: ClipRef,
        gain_db: f64,
    },
    AddMarker {
        at: Frame,
        note: String,
    },
    RemoveMarker {
        index: usize,
    },
    /// A new track on top of those of its kind.
    AddTrack {
        kind: TrackKind,
        name: String,
    },
    RemoveTrack {
        track: TrackRef,
    },
}

/// Why an op was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused(pub String);

impl fmt::Display for Refused {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Refused {}

fn refuse<T>(why: impl Into<String>) -> Result<T, Refused> {
    Err(Refused(why.into()))
}

/// `edit` with `op` applied, or why not. `edit` itself is left as it is.
pub fn apply<S: Clone + PartialEq>(edit: &Edit<S>, op: &Op<S>, sources: &impl Sources<S>) -> Result<Edit<S>, Refused> {
    let mut next = edit.clone();
    change(&mut next, op)?;
    if let Some(why) = next.problem() {
        return refuse(why);
    }
    check_sources(edit, &next, sources)?;
    Ok(next)
}

fn change<S: Clone + PartialEq>(edit: &mut Edit<S>, op: &Op<S>) -> Result<(), Refused> {
    match op {
        Op::Place { track, clip } => place(edit, *track, clip.clone()),
        Op::Insert { track, clip } => {
            let (at, duration) = match clip {
                NewClip::Video(c) => (c.at, c.duration),
                NewClip::Audio(c) => (c.at, c.duration),
            };
            if duration < 1 || at < 0 {
                return refuse(format!("a clip of {duration} frames at {at}"));
            }
            open_gap(edit, at, duration);
            place(edit, *track, clip.clone())
        }
        Op::Lift { clip } => on_clip(edit, *clip, |clips, i| {
            clips.remove(i);
            Ok(())
        }),
        Op::RippleDelete { clip } => {
            let (at, end) = span(edit, *clip)?;
            remove_span(edit, at, end);
            Ok(())
        }
        Op::Move { clip, to, at } => {
            if to.kind != clip.track.kind {
                return refuse("a clip moves only to a track of its kind");
            }
            track_exists(edit, *to)?;
            let mut taken = take(edit, *clip)?;
            match &mut taken {
                NewClip::Video(c) => c.at = *at,
                NewClip::Audio(c) => c.at = *at,
            }
            place(edit, *to, taken)
        }
        Op::Trim { clip, edge, delta } => trim(edit, *clip, *edge, *delta),
        Op::Roll { clip, delta } => on_clip(edit, *clip, |clips, i| roll(clips, i, *delta)),
        Op::Slip { clip, delta } => on_clip(edit, *clip, |clips, i| {
            let source_in = clips.get(i).source_in;
            clips.set_source_in(i, source_in + delta);
            Ok(())
        }),
        Op::Split { clip, at } => on_clip(edit, *clip, |clips, i| split(clips, i, *at)),
        Op::Join { clip } => on_clip(edit, *clip, |clips, i| join(clips, i)),
        Op::SetFades { clip, fade_in, fade_out } => on_clip(edit, *clip, |clips, i| {
            let view = clips.get(i);
            clips.set(i, ClipView { fade_in: *fade_in, fade_out: *fade_out, ..view });
            Ok(())
        }),
        Op::SetSource { clip, source } => on_clip(edit, *clip, |clips, i| {
            clips.set_source(i, source.clone());
            Ok(())
        }),
        Op::SetGain { clip, gain_db } => {
            if clip.track.kind != TrackKind::Audio {
                return refuse("only an audio clip has a level");
            }
            if !gain_db.is_finite() {
                return refuse(format!("a level of {gain_db} dB"));
            }
            let clips = &mut audio_track(edit, clip.track)?.clips;
            let c = clips.get_mut(clip.index).ok_or_else(|| Refused(format!("no clip {}", clip.index)))?;
            c.gain_db = *gain_db;
            Ok(())
        }
        Op::AddMarker { at, note } => {
            let index = edit.markers.partition_point(|m| m.at <= *at);
            edit.markers.insert(index, Marker { at: *at, note: note.clone() });
            Ok(())
        }
        Op::RemoveMarker { index } => {
            if *index >= edit.markers.len() {
                return refuse(format!("no marker {index}"));
            }
            edit.markers.remove(*index);
            Ok(())
        }
        Op::AddTrack { kind, name } => {
            match kind {
                TrackKind::Video => edit.video.push(crate::Track { name: name.clone(), clips: Vec::new() }),
                TrackKind::Audio => edit.audio.push(crate::Track { name: name.clone(), clips: Vec::new() }),
            }
            Ok(())
        }
        Op::RemoveTrack { track } => {
            track_exists(edit, *track)?;
            match track.kind {
                TrackKind::Video => drop(edit.video.remove(track.index)),
                TrackKind::Audio => drop(edit.audio.remove(track.index)),
            }
            Ok(())
        }
    }
}

/// Every clip `next` has that `before` has not (by source, span of source
/// frames and length) shows only frames its source has. A clip the op left
/// alone is not judged again: a shot that has shrunk since does not block
/// unrelated work.
fn check_sources<S: Clone + PartialEq>(before: &Edit<S>, next: &Edit<S>, sources: &impl Sources<S>) -> Result<(), Refused> {
    let old: Vec<(&S, Frame, Frame)> = shown(before);
    for (source, first, duration) in shown(next) {
        if old.iter().any(|&(s, f, d)| s == source && f == first && d == duration) {
            continue;
        }
        let last = first + duration - 1;
        match sources.available(source) {
            Available::Any => {}
            Available::Nothing => return refuse("a clip shows a source that is not there"),
            Available::Frames(from, to) => {
                if first < from || last > to {
                    return refuse(format!("a clip shows source frames {first} to {last}; its source has {from} to {to}"));
                }
            }
        }
    }
    Ok(())
}

fn shown<S: Clone>(edit: &Edit<S>) -> Vec<(&S, Frame, Frame)> {
    let video = edit.video.iter().flat_map(|t| t.clips.iter().map(|c| (&c.source, c.source_in, c.duration)));
    let audio = edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| (&c.source, c.source_in, c.duration)));
    video.chain(audio).collect()
}

fn track_exists<S: Clone>(edit: &Edit<S>, track: TrackRef) -> Result<(), Refused> {
    if track.index < edit.track_count(track.kind) { Ok(()) } else { refuse(format!("no {:?} track {}", track.kind, track.index)) }
}

fn audio_track<S>(edit: &mut Edit<S>, track: TrackRef) -> Result<&mut crate::Track<AudioClip<S>>, Refused> {
    edit.audio.get_mut(track.index).ok_or_else(|| Refused(format!("no audio track {}", track.index)))
}

/// Run `f` on the clips of `clip`'s track, with its index there.
fn on_clip<S: Clone + PartialEq>(
    edit: &mut Edit<S>,
    clip: ClipRef,
    f: impl FnOnce(&mut dyn ClipList<S>, usize) -> Result<(), Refused>,
) -> Result<(), Refused> {
    track_exists(edit, clip.track)?;
    let clips: &mut dyn ClipList<S> = match clip.track.kind {
        TrackKind::Video => &mut edit.video[clip.track.index].clips,
        TrackKind::Audio => &mut edit.audio[clip.track.index].clips,
    };
    if clip.index >= clips.len() {
        return refuse(format!("no clip {} on that track", clip.index));
    }
    f(clips, clip.index)
}

/// A track's clips, of either kind, as the ops below handle them.
trait ClipList<S> {
    fn len(&self) -> usize;
    fn remove(&mut self, index: usize);
    fn set_source_in(&mut self, index: usize, source_in: Frame);
    fn get(&self, index: usize) -> ClipView;
    fn set(&mut self, index: usize, view: ClipView);
    fn duplicate(&mut self, index: usize);
    fn same_source(&self, a: usize, b: usize) -> bool;
    fn set_source(&mut self, index: usize, source: S);
}

/// A clip's numbers.
#[derive(Debug, Clone, Copy)]
struct ClipView {
    at: Frame,
    duration: Frame,
    source_in: Frame,
    fade_in: Frame,
    fade_out: Frame,
}

impl ClipView {
    fn end(&self) -> Frame {
        self.at + self.duration
    }
}

impl<S: Clone + PartialEq, C: Clip<Source = S>> ClipList<S> for Vec<C> {
    fn len(&self) -> usize {
        Vec::len(self)
    }
    fn remove(&mut self, index: usize) {
        Vec::remove(self, index);
    }
    fn set_source_in(&mut self, index: usize, source_in: Frame) {
        self[index].set_source_in(source_in);
    }
    fn get(&self, index: usize) -> ClipView {
        let c = &self[index];
        ClipView { at: c.at(), duration: c.duration(), source_in: c.source_in(), fade_in: c.fade_in(), fade_out: c.fade_out() }
    }
    fn set(&mut self, index: usize, view: ClipView) {
        let c = &mut self[index];
        c.set_at(view.at);
        c.set_duration(view.duration);
        c.set_source_in(view.source_in);
        c.set_fades(view.fade_in, view.fade_out);
    }
    fn duplicate(&mut self, index: usize) {
        let copy = self[index].clone();
        self.insert(index + 1, copy);
    }
    fn same_source(&self, a: usize, b: usize) -> bool {
        self[a].source() == self[b].source()
    }
    fn set_source(&mut self, index: usize, source: S) {
        self[index].set_source(source);
    }
}

fn span<S: Clone>(edit: &Edit<S>, clip: ClipRef) -> Result<(Frame, Frame), Refused> {
    track_exists(edit, clip.track)?;
    edit.spans(clip.track).get(clip.index).copied().ok_or_else(|| Refused(format!("no clip {} on that track", clip.index)))
}

fn take<S: Clone>(edit: &mut Edit<S>, clip: ClipRef) -> Result<NewClip<S>, Refused> {
    track_exists(edit, clip.track)?;
    match clip.track.kind {
        TrackKind::Video => {
            let clips = &mut edit.video[clip.track.index].clips;
            if clip.index >= clips.len() {
                return refuse(format!("no clip {} on that track", clip.index));
            }
            Ok(NewClip::Video(clips.remove(clip.index)))
        }
        TrackKind::Audio => {
            let clips = &mut edit.audio[clip.track.index].clips;
            if clip.index >= clips.len() {
                return refuse(format!("no clip {} on that track", clip.index));
            }
            Ok(NewClip::Audio(clips.remove(clip.index)))
        }
    }
}

/// Put `clip` on `track` where its `at` says, in order; refused when it
/// would be over another.
fn place<S: Clone>(edit: &mut Edit<S>, track: TrackRef, clip: NewClip<S>) -> Result<(), Refused> {
    if clip.kind() != track.kind {
        return refuse("a clip goes only on a track of its kind");
    }
    track_exists(edit, track)?;
    fn put<C: Clip>(clips: &mut Vec<C>, clip: C) -> Result<(), Refused> {
        let (at, end) = (clip.at(), clip.end());
        if let Some(other) = clips.iter().find(|c| c.at() < end && at < c.end()) {
            return refuse(format!("frames {at} to {} are taken, by a clip at {} to {}", end - 1, other.at(), other.end() - 1));
        }
        let index = clips.partition_point(|c| c.at() < at);
        clips.insert(index, clip);
        Ok(())
    }
    match clip {
        NewClip::Video(c) => put(&mut edit.video[track.index].clips, c),
        NewClip::Audio(c) => put(&mut edit.audio[track.index].clips, c),
    }
}

/// Open `n` frames at edit frame `at` on every track: what starts there or
/// later moves `n` later, and a clip across `at` is split there.
fn open_gap<S: Clone>(edit: &mut Edit<S>, at: Frame, n: Frame) {
    fn on<C: Clip>(clips: &mut Vec<C>, at: Frame, n: Frame) {
        let mut out = Vec::with_capacity(clips.len() + 1);
        for c in clips.drain(..) {
            if c.at() >= at {
                let mut moved = c;
                moved.set_at(moved.at() + n);
                out.push(moved);
            } else if c.end() > at {
                let (left, mut right) = cut(&c, at);
                right.set_at(right.at() + n);
                out.push(left);
                out.push(right);
            } else {
                out.push(c);
            }
        }
        *clips = out;
    }
    for t in &mut edit.video {
        on(&mut t.clips, at, n);
    }
    for t in &mut edit.audio {
        on(&mut t.clips, at, n);
    }
    for m in &mut edit.markers {
        if m.at >= at {
            m.at += n;
        }
    }
}

/// Take edit frames `from .. to` out of every track: what is after moves
/// back, a clip across either end keeps its frames outside, and a marker
/// inside goes to `from`.
fn remove_span<S: Clone>(edit: &mut Edit<S>, from: Frame, to: Frame) {
    let n = to - from;
    fn on<C: Clip>(clips: &mut Vec<C>, from: Frame, to: Frame, n: Frame) {
        let mut out = Vec::with_capacity(clips.len() + 1);
        for c in clips.drain(..) {
            if c.end() <= from {
                out.push(c);
            } else if c.at() >= to {
                let mut moved = c;
                moved.set_at(moved.at() - n);
                out.push(moved);
            } else {
                if c.at() < from {
                    out.push(cut(&c, from).0);
                }
                if c.end() > to {
                    let mut right = cut(&c, to).1;
                    right.set_at(from);
                    out.push(right);
                }
            }
        }
        *clips = out;
    }
    for t in &mut edit.video {
        on(&mut t.clips, from, to, n);
    }
    for t in &mut edit.audio {
        on(&mut t.clips, from, to, n);
    }
    for m in &mut edit.markers {
        if m.at >= to {
            m.at -= n;
        } else if m.at > from {
            m.at = from;
        }
    }
}

/// `c` cut at edit frame `at` (inside it): the part before and the part
/// from `at`. Each keeps its outer fade, shortened to fit; the cut has none.
fn cut<C: Clip>(c: &C, at: Frame) -> (C, C) {
    let mut left = c.clone();
    left.set_duration(at - c.at());
    left.set_fades(c.fade_in().min(left.duration()), 0);
    let mut right = c.clone();
    right.set_at(at);
    right.set_source_in(c.source_in() + (at - c.at()));
    right.set_duration(c.end() - at);
    right.set_fades(0, c.fade_out().min(right.duration()));
    (left, right)
}

fn trim<S: Clone + PartialEq>(edit: &mut Edit<S>, clip: ClipRef, edge: Edge, delta: Frame) -> Result<(), Refused> {
    let (at, end) = span(edit, clip)?;
    let duration = end - at;
    if delta == 0 {
        return Ok(());
    }
    let new_duration = match edge {
        Edge::Head => duration - delta,
        Edge::Tail => duration + delta,
    };
    if new_duration < 1 {
        return refuse(format!("a trim of {delta} leaves the clip {new_duration} frames long"));
    }
    let view = {
        let mut v = None;
        on_clip(edit, clip, |clips, i| {
            v = Some(clips.get(i));
            Ok(())
        })?;
        v.expect("the clip is there")
    };
    // The clips on its track before it keep their indices through either
    // ripple below, so it is found again at `clip.index`.
    match (edge, delta > 0) {
        (Edge::Tail, true) => open_gap(edit, end, delta),
        (Edge::Tail, false) => remove_span(edit, end + delta, end),
        (Edge::Head, true) => remove_span(edit, at, at + delta),
        (Edge::Head, false) => open_gap(edit, at, -delta),
    }
    on_clip(edit, clip, |clips, i| {
        let (source_in, new_at) = match edge {
            Edge::Head => (view.source_in + delta, at),
            Edge::Tail => (view.source_in, at),
        };
        // An open gap before it split nothing on its track (it starts
        // there); taking its head out left only its right part. Either way
        // the clip is at `i`: put it back whole, with its own fades.
        let fade_in = view.fade_in.min(new_duration);
        let fade_out = view.fade_out.min(new_duration - fade_in);
        clips.set(i, ClipView { at: new_at, duration: new_duration, source_in, fade_in, fade_out });
        Ok(())
    })
}

fn roll<S>(clips: &mut dyn ClipList<S>, i: usize, delta: Frame) -> Result<(), Refused> {
    if i + 1 >= clips.len() {
        return refuse("a roll needs the clip after it");
    }
    let (mut left, mut right) = (clips.get(i), clips.get(i + 1));
    if left.end() != right.at {
        return refuse("a roll needs the next clip to start where it ends");
    }
    left.duration += delta;
    right.at += delta;
    right.source_in += delta;
    right.duration -= delta;
    if left.duration < 1 || right.duration < 1 {
        return refuse(format!("a roll of {delta} leaves a clip empty"));
    }
    clips.set(i, left);
    clips.set(i + 1, right);
    Ok(())
}

fn split<S>(clips: &mut dyn ClipList<S>, i: usize, at: Frame) -> Result<(), Refused> {
    let c = clips.get(i);
    if at <= c.at || at >= c.end() {
        return refuse(format!("frame {at} is not inside the clip ({} to {})", c.at, c.end() - 1));
    }
    if at < c.at + c.fade_in || at > c.end() - c.fade_out {
        return refuse(format!("frame {at} is inside a fade"));
    }
    clips.duplicate(i);
    clips.set(i, ClipView { duration: at - c.at, fade_out: 0, ..c });
    clips.set(i + 1, ClipView { at, duration: c.end() - at, source_in: c.source_in + (at - c.at), fade_in: 0, ..c });
    Ok(())
}

fn join<S>(clips: &mut dyn ClipList<S>, i: usize) -> Result<(), Refused> {
    if i + 1 >= clips.len() {
        return refuse("a join needs the clip after it");
    }
    let (left, right) = (clips.get(i), clips.get(i + 1));
    if left.end() != right.at || left.source_in + left.duration != right.source_in || !clips.same_source(i, i + 1) {
        return refuse("a join needs the next clip to continue it, in the edit and in its source");
    }
    if left.fade_out != 0 || right.fade_in != 0 {
        return refuse("a join would lose the fade between them");
    }
    clips.set(i, ClipView { duration: left.duration + right.duration, fade_out: right.fade_out, ..left });
    clips.remove(i + 1);
    Ok(())
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::document::{Source, Track, VersionRule};
    use proptest::prelude::*;

    /// Sources for the tests: a slug has any frames; media item `m<n>` has
    /// frames 0 to n*10.
    pub(crate) struct Lengths;

    impl Sources<Source> for Lengths {
        fn available(&self, source: &Source) -> Available {
            match source {
                Source::Slug { .. } => Available::Any,
                Source::Media { item, .. } => match item.strip_prefix('m').and_then(|n| n.parse::<Frame>().ok()) {
                    Some(n) => Available::Frames(0, n * 10),
                    None => Available::Nothing,
                },
                Source::Shot { .. } => Available::Frames(1001, 1100),
            }
        }
    }

    pub(crate) fn media(n: Frame) -> Source {
        Source::Media { item: format!("m{n}"), version: VersionRule::Latest }
    }

    pub(crate) fn vclip(at: Frame, duration: Frame, source_in: Frame, source: Source) -> VideoClip<Source> {
        VideoClip { at, duration, source_in, source, fade_in: 0, fade_out: 0 }
    }

    pub(crate) fn aclip(at: Frame, duration: Frame, source_in: Frame, source: Source) -> AudioClip<Source> {
        AudioClip { at, duration, source_in, source, gain_db: 0.0, fade_in: 0, fade_out: 0 }
    }

    /// An edit of `tracks` video tracks and one audio track, each holding
    /// clips of media long enough to trim around.
    pub(crate) fn edits() -> impl Strategy<Value = Edit<Source>> {
        let clip = (0i64..20, 1i64..40, 0i64..30, 0usize..4);
        let track = prop::collection::vec(clip, 0..5);
        (prop::collection::vec(track.clone(), 1..4), track).prop_map(|(video, audio)| {
            let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
            let lay = |clips: &[(i64, i64, i64, usize)]| {
                let mut at = 0;
                let mut out = Vec::new();
                for &(gap, duration, source_in, n) in clips {
                    at += gap;
                    out.push((at, duration, source_in, if n == 0 { Source::Slug { text: "s".into() } } else { media(100) }));
                    at += duration;
                }
                out
            };
            for (i, clips) in video.iter().enumerate() {
                edit.video.push(Track {
                    name: format!("V{}", i + 1),
                    clips: lay(clips).into_iter().map(|(a, d, s, src)| vclip(a, d, s, src)).collect(),
                });
            }
            edit.audio
                .push(Track { name: "A1".into(), clips: lay(&audio).into_iter().map(|(a, d, s, src)| aclip(a, d, s, src)).collect() });
            edit.markers = vec![Marker { at: 10, note: "a".into() }, Marker { at: 50, note: "b".into() }];
            edit
        })
    }

    /// A clip of `edit` named by two indices, or None when it has none.
    pub(crate) fn pick(edit: &Edit<Source>, t: prop::sample::Index, c: prop::sample::Index) -> Option<ClipRef> {
        let tracks: Vec<TrackRef> = edit.tracks().filter(|&t| !edit.spans(t).is_empty()).collect();
        if tracks.is_empty() {
            return None;
        }
        let track = tracks[t.index(tracks.len())];
        Some(ClipRef { track, index: c.index(edit.spans(track).len()) })
    }

    /// Every clip of `edit`, as (track, at, duration, source_in).
    fn layout(edit: &Edit<Source>) -> Vec<(TrackRef, Frame, Frame, Frame)> {
        let mut out = Vec::new();
        for track in edit.tracks() {
            match track.kind {
                TrackKind::Video => out.extend(edit.video[track.index].clips.iter().map(|c| (track, c.at, c.duration, c.source_in))),
                TrackKind::Audio => out.extend(edit.audio[track.index].clips.iter().map(|c| (track, c.at, c.duration, c.source_in))),
            }
        }
        out
    }

    #[test]
    fn insert_opens_room_on_every_track_and_splits_what_spans_it() {
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 100, 0, media(20))] });
        edit.video.push(Track { name: "V2".into(), clips: vec![vclip(40, 10, 1001, Source::Slug { text: "x".into() })] });
        let op = Op::Insert { track: TrackRef::video(1), clip: NewClip::Video(vclip(20, 5, 0, media(20))) };
        let next = apply(&edit, &op, &Lengths).unwrap();
        assert_eq!(next.length(), 105);
        let v1: Vec<_> = next.video[0].clips.iter().map(|c| (c.at, c.duration, c.source_in)).collect();
        assert_eq!(v1, vec![(0, 20, 0), (25, 80, 20)]);
        let v2: Vec<_> = next.video[1].clips.iter().map(|c| (c.at, c.duration)).collect();
        assert_eq!(v2, vec![(20, 5), (45, 10)]);
    }

    #[test]
    fn a_clip_past_its_source_is_refused() {
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 100, 0, media(10))] });
        let slip = Op::Slip { clip: ClipRef { track: TrackRef::video(0), index: 0 }, delta: 2 };
        assert!(apply(&edit, &slip, &Lengths).unwrap_err().0.contains("0 to 100"));
        let back = Op::Slip { clip: ClipRef { track: TrackRef::video(0), index: 0 }, delta: -1 };
        assert!(apply(&edit, &back, &Lengths).is_err());
        let fine = Op::Slip { clip: ClipRef { track: TrackRef::video(0), index: 0 }, delta: 1 };
        assert!(apply(&edit, &fine, &Lengths).is_ok());
    }

    #[test]
    fn another_source_shows_only_where_it_has_the_frames() {
        let mut edit = Edit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 50, 30, media(10))] });
        let clip = ClipRef { track: TrackRef::video(0), index: 0 };
        let longer = apply(&edit, &Op::SetSource { clip, source: media(20) }, &Lengths).unwrap();
        assert_eq!(longer.video[0].clips[0].source, media(20));
        assert_eq!((longer.video[0].clips[0].at, longer.video[0].clips[0].duration), (0, 50));
        // m5 has frames 0 to 50: the clip shows 30 to 79.
        assert!(apply(&edit, &Op::SetSource { clip, source: media(5) }, &Lengths).is_err());
        assert!(
            apply(&edit, &Op::SetSource { clip, source: Source::Media { item: "x".into(), version: VersionRule::Latest } }, &Lengths)
                .is_err()
        );
    }

    proptest! {
        /// Whatever op is tried: a refused one leaves nothing changed (the
        /// edit is not touched), and an applied one leaves an edit with no
        /// problem whose new clips show only frames their sources have.
        #[test]
        fn every_result_is_an_edit(edit in edits(), ops in prop::collection::vec(any_op(), 1..20)) {
            let mut edit = edit;
            for op in ops {
                let Some(op) = choose(&edit, &op) else { continue };
                if let Ok(next) = apply(&edit, &op, &Lengths) {
                    prop_assert_eq!(next.problem(), None);
                    edit = next;
                }
            }
        }

        /// Roll keeps the edit's length; ripple trim changes it by exactly
        /// the trim; lift and slip move no other clip.
        #[test]
        fn lengths_follow_the_laws(edit in edits(), t in any::<prop::sample::Index>(), c in any::<prop::sample::Index>(), delta in -30i64..30, head in any::<bool>()) {
            let Some(clip) = pick(&edit, t, c) else { return Ok(()) };
            if let Ok(next) = apply(&edit, &Op::Roll { clip, delta }, &Lengths) {
                prop_assert_eq!(next.length(), edit.length());
            }
            let edge = if head { Edge::Head } else { Edge::Tail };
            if let Ok(next) = apply(&edit, &Op::Trim { clip, edge, delta }, &Lengths) {
                let change = if head { -delta } else { delta };
                prop_assert_eq!(next.length(), edit.length() + change);
            }
            if let Ok(next) = apply(&edit, &Op::Lift { clip }, &Lengths) {
                let mut before = layout(&edit);
                let gone = before.iter().position(|&(tr, at, ..)| tr == clip.track && at == edit.spans(clip.track)[clip.index].0).unwrap();
                before.remove(gone);
                prop_assert_eq!(layout(&next), before);
            }
            if let Ok(next) = apply(&edit, &Op::Slip { clip, delta }, &Lengths) {
                let positions = |e: &Edit<Source>| layout(e).into_iter().map(|(t, a, d, _)| (t, a, d)).collect::<Vec<_>>();
                prop_assert_eq!(positions(&next), positions(&edit));
            }
        }

        /// Split then join is the identity.
        #[test]
        fn split_then_join_is_identity(edit in edits(), t in any::<prop::sample::Index>(), c in any::<prop::sample::Index>(), at in 0i64..200) {
            let Some(clip) = pick(&edit, t, c) else { return Ok(()) };
            if let Ok(split) = apply(&edit, &Op::Split { clip, at }, &Lengths) {
                let joined = apply(&split, &Op::Join { clip }, &Lengths).unwrap();
                prop_assert_eq!(joined, edit);
            }
        }

        /// Ripple ops keep the tracks in sync: every clip and marker that
        /// started at or after the edit point moves by the same amount,
        /// with the same source frames, and none before it moves.
        #[test]
        fn ripples_move_every_track_together(edit in edits(), t in any::<prop::sample::Index>(), c in any::<prop::sample::Index>(), delta in -30i64..30, head in any::<bool>(), insert_at in 0i64..150, n in 1i64..20) {
            let check = |next: &Edit<Source>, point: Frame, shift: Frame, removed_to: Frame, own: Option<(TrackRef, Frame)>| -> Result<(), TestCaseError> {
                for (track, at, duration, source_in) in layout(&edit) {
                    if own == Some((track, at)) {
                        continue;
                    }
                    if at >= removed_to.max(point) {
                        prop_assert!(layout(next).contains(&(track, at + shift, duration, source_in)), "{:?} at {} did not move by {}", track, at, shift);
                    } else if at + duration <= point {
                        prop_assert!(layout(next).contains(&(track, at, duration, source_in)), "{:?} at {} moved", track, at);
                    }
                }
                let mut want: Vec<Frame> = edit.markers.iter().map(|m| if m.at >= removed_to.max(point) { m.at + shift } else if m.at > point { point } else { m.at }).collect();
                want.sort();
                let got: Vec<Frame> = next.markers.iter().map(|m| m.at).collect();
                prop_assert_eq!(got, want);
                Ok(())
            };
            let op = Op::Insert { track: TrackRef::video(0), clip: NewClip::Video(vclip(insert_at, n, 0, Source::Slug { text: "i".into() })) };
            if let Ok(next) = apply(&edit, &op, &Lengths) {
                check(&next, insert_at, n, insert_at, None)?;
            }
            let Some(clip) = pick(&edit, t, c) else { return Ok(()) };
            let (at, end) = edit.spans(clip.track)[clip.index];
            if let Ok(next) = apply(&edit, &Op::RippleDelete { clip }, &Lengths) {
                check(&next, at, -(end - at), end, None)?;
            }
            if !head && delta > 0
                && let Ok(next) = apply(&edit, &Op::Trim { clip, edge: Edge::Tail, delta }, &Lengths) {
                    check(&next, end, delta, end, Some((clip.track, at)))?;
                }
        }

        /// The document round-trips through JSON.
        #[test]
        fn json_round_trips(edit in edits()) {
            prop_assert_eq!(Edit::<Source>::parse(&edit.to_json()).unwrap(), edit);
        }
    }

    /// An op to try, as data: [`choose`] makes it an op of the edit it is
    /// tried on.
    #[derive(Debug, Clone)]
    pub(crate) enum Try {
        Add { t: prop::sample::Index, at: Frame, n: Frame, ripple: bool },
        OnClip { t: prop::sample::Index, c: prop::sample::Index, delta: Frame, which: u8, at: Frame },
        Marker { at: Frame },
    }

    pub(crate) fn any_op() -> impl Strategy<Value = Try> {
        let i = any::<prop::sample::Index>;
        prop_oneof![
            1 => (i(), 0i64..120, 1i64..30, any::<bool>()).prop_map(|(t, at, n, ripple)| Try::Add { t, at, n, ripple }),
            3 => (i(), i(), -20i64..20, 0u8..9, 0i64..150).prop_map(|(t, c, delta, which, at)| Try::OnClip { t, c, delta, which, at }),
            1 => (0i64..100).prop_map(|at| Try::Marker { at }),
        ]
    }

    pub(crate) fn choose(e: &Edit<Source>, op: &Try) -> Option<Op<Source>> {
        match *op {
            Try::Add { t, at, n, ripple } => {
                if e.video.is_empty() {
                    return None;
                }
                let track = TrackRef::video(t.index(e.video.len()));
                let clip = NewClip::Video(vclip(at, n, 0, media(10)));
                Some(if ripple { Op::Insert { track, clip } } else { Op::Place { track, clip } })
            }
            Try::OnClip { t, c, delta, which, at } => {
                let clip = pick(e, t, c)?;
                Some(match which {
                    0 => Op::Lift { clip },
                    1 => Op::RippleDelete { clip },
                    2 => Op::Trim { clip, edge: if delta % 2 == 0 { Edge::Head } else { Edge::Tail }, delta },
                    3 => Op::Roll { clip, delta },
                    4 => Op::Slip { clip, delta },
                    5 => Op::Split { clip, at },
                    6 => Op::Join { clip },
                    7 => Op::SetFades { clip, fade_in: delta.abs() / 2, fade_out: delta.abs() / 3 },
                    _ => Op::Move { clip, to: clip.track, at },
                })
            }
            Try::Marker { at } => Some(Op::AddMarker { at, note: "m".into() }),
        }
    }
}
