//! The editor's document, without a window: the working edit and its undo
//! history, where it lives (untitled, a working version, a published
//! version read-only), and what of it the engine plays.
//!
//! Edit frames are timeline positions one for one, at the edit's rate. A
//! clip shows source frames `source_in ..` in its source's own numbers (a
//! shot's are its frame numbers, 1001-based); the engine wants media
//! indices, so each clip's source is bound ([`Bound`]) to the engine source
//! playing it and the frame number of that media's index 0. Part of a clip
//! before its media's first frame shows nothing (what is below shows).

use std::collections::BTreeSet;
use std::path::Path;

use th_edit::document::{
    AudioClip, ClipRef, Frame, Rule, Source, Track, TrackKind, TrackRef, VersionRule, VideoClip, WorkingEdit, timecode_frames,
};
use th_edit::history::History;
use th_edit::ops::{NewClip, Op, Refused, Sources};
use th_media::sound::Sound;
use th_media::{FrameRanges, Media};
use th_pipe::Project;
use th_pipe::media::{ItemVersion, Measure};

use crate::engine::Clip;

/// Where the document lives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Origin {
    /// Not saved yet: Save asks for a name and makes the edit.
    Untitled,
    /// Working version `version` (None: the edit exists, no version saved).
    Working { name: String, version: Option<String> },
    /// Published version `version`: read-only; Save As starts a working
    /// version from it.
    Published { name: String, version: String },
}

/// The working edit being edited.
#[derive(Debug)]
pub struct Document {
    pub edit: WorkingEdit,
    history: History<Source>,
    pub origin: Origin,
    /// The edit as it was opened or last saved: it is dirty when it differs.
    saved: WorkingEdit,
    /// Opened as the project's shots because there was nothing to open
    /// (see [`project_fill`]), not from a saved version or asked-for shots.
    pub filled: bool,
}

impl Document {
    pub fn new(edit: WorkingEdit, origin: Origin) -> Self {
        Self { saved: edit.clone(), edit, history: History::new(), origin, filled: false }
    }

    /// A document of [`project_fill`]: unchanged until the user changes it.
    pub fn filled(edit: WorkingEdit, origin: Origin) -> Self {
        Self { filled: true, ..Self::new(edit, origin) }
    }

    /// Changed from what was opened or last saved (undoing back to it is
    /// not a change).
    pub fn dirty(&self) -> bool {
        self.edit != self.saved
    }

    /// Nothing here to keep when another edit opens: untitled, unchanged,
    /// and empty or the project's shots it opened as.
    pub fn replaceable(&self) -> bool {
        matches!(self.origin, Origin::Untitled) && !self.dirty() && (self.filled || self.edit.length() == 0)
    }

    /// The edit as it is now is what is saved.
    pub fn mark_saved(&mut self) {
        self.saved = self.edit.clone();
    }

    pub fn read_only(&self) -> bool {
        matches!(self.origin, Origin::Published { .. })
    }

    /// Apply `ops` as one undo step, all or none (refused on a published
    /// version, and as `th_edit` refuses).
    pub fn apply(&mut self, ops: &[Op<Source>], sources: &impl Sources<Source>) -> Result<(), Refused> {
        if self.read_only() {
            return Err(Refused("a published version is read-only: Save As to change it".into()));
        }
        if ops.is_empty() {
            return Ok(());
        }
        self.history.apply_all(&mut self.edit, ops, sources)
    }

    pub fn undo(&mut self) -> bool {
        self.history.undo(&mut self.edit)
    }

    pub fn redo(&mut self) -> bool {
        self.history.redo(&mut self.edit)
    }

    pub fn can_undo(&self) -> bool {
        self.history.can_undo()
    }

    pub fn can_redo(&self) -> bool {
        self.history.can_redo()
    }

    /// What the title bar says: the name and version, and whether it changed.
    pub fn title(&self) -> String {
        let mark = if self.dirty() { " *" } else { "" };
        match &self.origin {
            Origin::Untitled => format!("Untitled edit{mark}"),
            Origin::Working { name, version: Some(v) } => format!("{name} {v}{mark}"),
            Origin::Working { name, version: None } => format!("{name}{mark}"),
            Origin::Published { name, version } => format!("{name} {version} (published, read-only)"),
        }
    }
}

/// An untitled edit of `shots` (each its URI and the frames it plays) laid
/// end to end on V1, each its best available take, at `fps` and
/// `resolution`: what "Play N Shots" opens.
pub fn of_shots(shots: &[(String, Frame, Frame)], fps: u32, resolution: [u32; 2]) -> WorkingEdit {
    let mut edit = WorkingEdit::new(fps, resolution, "01:00:00:00");
    let mut at = 0;
    let clips = shots
        .iter()
        .filter(|(_, first, last)| last >= first)
        .map(|(shot, first, last)| {
            let duration = last - first + 1;
            let clip = VideoClip {
                at,
                duration,
                source_in: *first,
                source: Source::Shot { shot: shot.clone(), take: Rule::best() },
                fade_in: 0,
                fade_out: 0,
                opacity: 1.0,
            };
            at += duration;
            clip
        })
        .collect();
    edit.video.push(Track { name: "V1".into(), clips });
    edit.audio.push(Track { name: "A1".into(), clips: Vec::new() });
    edit
}

/// What an edit with nothing in it yet opens with: every shot of the
/// project (`shots`, as [`project_shots`] lists them, in sequence then shot
/// order) end to end, each its play range and best available take. Artists
/// start a cut from the whole film, not from an empty timeline.
pub fn project_fill(shots: &[BinShot], fps: u32, resolution: [u32; 2]) -> WorkingEdit {
    let ranges: Vec<_> = shots.iter().map(|s| (s.uri.clone(), s.first, s.last)).collect();
    of_shots(&ranges, fps, resolution)
}

/// A clip's source as the engine plays it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bound {
    /// The engine source.
    pub source: usize,
    /// The frame number of its media's index 0.
    pub first: Frame,
    /// The frames its media's pictures are when they are not one run from
    /// `first`: a multi-range render's mp4 (#86).
    pub ranges: Option<FrameRanges>,
}

impl Bound {
    /// The runs of its media's frames, as (first frame, last frame, index of
    /// the first): its list's, else one from `first` on.
    fn runs(&self) -> Vec<(Frame, Frame, usize)> {
        match &self.ranges {
            Some(list) => list.runs().map(|r| (r.first, r.last, r.index)).collect(),
            None => vec![(self.first, Frame::MAX, 0)],
        }
    }
}

/// The engine clips of `edit`'s video tracks: every clip whose source is
/// bound, from its in point (as a media index), on its track, with its
/// fades. Frames its media has no picture of are left out (before its
/// first, and a multi-range render's gaps, #86): a clip plays in a piece
/// per run of its media's frames it covers, its fades still its own.
pub fn engine_clips(edit: &WorkingEdit, bound: impl Fn(&Source) -> Option<Bound>) -> Vec<Clip> {
    let rate = (edit.fps as i32, 1);
    let mut out = Vec::new();
    for (track, t) in edit.video.iter().enumerate() {
        for c in &t.clips {
            let Some(b) = bound(&c.source) else { continue };
            let (from, to) = (c.source_in, c.source_in + c.duration - 1);
            for (first, last, index) in b.runs() {
                let (a, z) = (from.max(first), to.min(last));
                if a > z {
                    continue;
                }
                out.push(Clip {
                    source: b.source,
                    track,
                    at: (c.at + a - from) as usize,
                    frames: (z - a + 1) as usize,
                    first: index + (a - first) as usize,
                    fade_in: c.fade_in.max(0) as usize,
                    fade_out: c.fade_out.max(0) as usize,
                    lead: (a - from) as usize,
                    tail: (to - z) as usize,
                    opacity: c.opacity as f32,
                    rate,
                });
            }
        }
    }
    out
}

/// Edit frame `frame` as a timecode, from the edit's start timecode.
pub fn timecode(frame: Frame, edit: &WorkingEdit) -> String {
    let fps = Frame::from(edit.fps.max(1));
    let total = timecode_frames(&edit.start_timecode, edit.fps).unwrap_or(0) + frame.max(0);
    let (seconds, frames) = (total / fps, total % fps);
    format!("{:02}:{:02}:{:02}:{:02}", seconds / 3600, seconds / 60 % 60, seconds % 60, frames)
}

/// Where clips start and end, in order, from frame 0: where Ctrl+Left and
/// Ctrl+Right go.
pub fn edit_points(edit: &WorkingEdit) -> Vec<Frame> {
    let mut points: BTreeSet<Frame> = BTreeSet::from([0]);
    for track in edit.tracks() {
        for (at, end) in edit.spans(track) {
            points.extend([at, end]);
        }
    }
    points.into_iter().collect()
}

/// A shot of the project, for the shot bin: its play range.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BinShot {
    pub uri: String,
    pub sequence: String,
    pub name: String,
    pub first: Frame,
    pub last: Frame,
}

impl BinShot {
    /// It as a clip at `at`: its play range, its best available take.
    pub fn clip(&self, at: Frame) -> VideoClip<Source> {
        VideoClip {
            at,
            duration: self.last - self.first + 1,
            source_in: self.first,
            source: Source::Shot { shot: self.uri.clone(), take: Rule::best() },
            fade_in: 0,
            fade_out: 0,
            opacity: 1.0,
        }
    }
}

/// The project's shots with a frame range, by sequence and name.
pub fn project_shots(project: &Project) -> Result<Vec<BinShot>, String> {
    let shots = th_pipe::Uri::parse("entity:/shots").map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for uri in project.config.list_entity_uris(&shots, true).map_err(|e| e.to_string())? {
        if !project.config.is_terminal_entity(&uri).map_err(|e| e.to_string())? {
            continue;
        }
        let Some(range) = th_pipe::timeline::get_frame_range(&project.config, &uri)? else { continue };
        let play = range.play_range();
        out.push(BinShot {
            uri: uri.to_string(),
            sequence: th_pipe::discovery::sequence_of(&uri),
            name: uri.last().unwrap_or_default().to_owned(),
            first: play.first_frame,
            last: play.last_frame,
        });
    }
    out.sort_by(|a, b| (&a.sequence, &a.name).cmp(&(&b.sequence, &b.name)));
    Ok(out)
}

/// A media library item's newest version, for the media bin.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BinItem {
    pub item: String,
    pub version: String,
    /// A movie's frames (played at the edit's rate).
    pub frames: Option<Frame>,
    /// Its sound's length in edit frames, when it has sound.
    pub sound: Option<Frame>,
}

impl BinItem {
    pub fn of(item: &str, version: &ItemVersion, fps: u32) -> Self {
        let m = &version.measure;
        BinItem {
            item: item.to_owned(),
            version: version.version.clone(),
            frames: m.frames.map(|(n, _)| n as Frame),
            sound: m.sound.then(|| (m.seconds * f64::from(fps)).floor() as Frame),
        }
    }

    pub fn describe(&self) -> String {
        let what = match (self.frames, self.sound) {
            (Some(n), Some(_)) => format!("{n} frames, sound"),
            (Some(n), None) => format!("{n} frames"),
            (None, Some(n)) => format!("sound, {n} frames long"),
            (None, None) => "nothing to play".into(),
        };
        format!("{} · {what}", self.version)
    }

    /// It as clips at `at`: its picture on a video track, its sound on an
    /// audio track, as long as each has (the picture's length for both when
    /// it has a picture), newest version always.
    pub fn clips(&self, at: Frame) -> (Option<VideoClip<Source>>, Option<AudioClip<Source>>) {
        let source = Source::Media { item: self.item.clone(), version: VersionRule::Latest };
        let video = self.frames.filter(|&n| n > 0).map(|duration| VideoClip {
            at,
            duration,
            source_in: 0,
            source: source.clone(),
            fade_in: 0,
            fade_out: 0,
            opacity: 1.0,
        });
        let audio = self.sound.map(|n| self.frames.unwrap_or(n)).filter(|&n| n > 0).map(|duration| AudioClip {
            at,
            duration,
            source_in: 0,
            source: source.clone(),
            gain_db: 0.0,
            fade_in: 0,
            fade_out: 0,
        });
        (video, audio)
    }
}

/// Whether `at .. end` is free on track `track` of `edit`.
fn free(edit: &WorkingEdit, track: TrackRef, at: Frame, end: Frame) -> bool {
    edit.spans(track).iter().all(|&(a, e)| e <= at || a >= end)
}

/// The ops that put `video` and `audio` (the same media at the same frame)
/// into `edit`: inserted on video track `track` (everything from there on
/// moves later, on every track), or laid over what is there (on the lowest
/// track from `track` up with room, a new one on top when none has). A
/// missing track is added first.
pub fn put(
    edit: &WorkingEdit,
    video: Option<VideoClip<Source>>,
    audio: Option<AudioClip<Source>>,
    track: usize,
    overlay: bool,
) -> Vec<Op<Source>> {
    let mut ops = Vec::new();
    let add = |ops: &mut Vec<Op<Source>>, kind: TrackKind, count: usize| {
        let name = match kind {
            TrackKind::Video => format!("V{}", count + 1),
            TrackKind::Audio => format!("A{}", count + 1),
        };
        ops.push(Op::AddTrack { kind, name });
    };
    // Where each goes: (track index, whether that track is new).
    let room = |kind: TrackKind, from: usize, at: Frame, end: Frame| -> (usize, bool) {
        let count = edit.track_count(kind);
        let from = from.min(count);
        match (from..count).find(|&i| free(edit, TrackRef { kind, index: i }, at, end)) {
            Some(i) => (i, false),
            None => (count, true),
        }
    };
    if !overlay {
        match (video, audio) {
            (Some(v), audio) => {
                let track = if edit.video.is_empty() {
                    add(&mut ops, TrackKind::Video, 0);
                    0
                } else {
                    track.min(edit.video.len() - 1)
                };
                ops.push(Op::Insert { track: TrackRef::video(track), clip: NewClip::Video(v) });
                if let Some(a) = audio {
                    // The insert opened room on every track: its sound goes on
                    // the first audio track (free after the insert).
                    if edit.audio.is_empty() {
                        add(&mut ops, TrackKind::Audio, 0);
                    }
                    ops.push(Op::Place { track: TrackRef::audio(0), clip: NewClip::Audio(a) });
                }
            }
            (None, Some(a)) => {
                if edit.audio.is_empty() {
                    add(&mut ops, TrackKind::Audio, 0);
                }
                ops.push(Op::Insert { track: TrackRef::audio(0), clip: NewClip::Audio(a) });
            }
            (None, None) => {}
        }
        return ops;
    }
    if let Some(v) = video {
        let (index, new) = room(TrackKind::Video, track, v.at, v.at + v.duration);
        if new {
            add(&mut ops, TrackKind::Video, index);
        }
        ops.push(Op::Place { track: TrackRef::video(index), clip: NewClip::Video(v) });
    }
    if let Some(a) = audio {
        let (index, new) = room(TrackKind::Audio, 0, a.at, a.at + a.duration);
        if new {
            add(&mut ops, TrackKind::Audio, index);
        }
        ops.push(Op::Place { track: TrackRef::audio(index), clip: NewClip::Audio(a) });
    }
    ops
}

/// Where the playhead goes after [`put`]: the end of what it put, as NLEs
/// do, so Insert after Insert appends in order (#25: three Inserts at a
/// playhead that stayed put built the cut backwards). None when nothing
/// was put.
pub fn put_end(video: Option<&VideoClip<Source>>, audio: Option<&AudioClip<Source>>) -> Option<Frame> {
    let video = video.map(|v| v.at + v.duration);
    let audio = audio.map(|a| a.at + a.duration);
    video.into_iter().chain(audio).max()
}

/// A shot name split into its letters and its number (`SH030` → `("SH",
/// "030")`); None for a name not of that shape.
fn name_parts(name: &str) -> Option<(&str, &str)> {
    let digits = name.len() - name.bytes().rev().take_while(u8::is_ascii_digit).count();
    let (prefix, number) = name.split_at(digits);
    (!number.is_empty() && prefix.bytes().all(|b| b.is_ascii_alphabetic())).then_some((prefix, number))
}

/// The name New shot… offers for a shot of `sequence` (#63), given every
/// shot `taken` (sequence, name), made or planned: the next free one in the
/// style the shots are named in, counting in tens. The style (the letters
/// before the number and how many digits it has) is the commonest among
/// the sequence's shots, else the project's, else `SH` and three digits;
/// the number is the next ten above the highest of that style in the
/// sequence, so it is free.
pub fn next_shot_name(taken: &[(String, String)], sequence: &str) -> String {
    let in_sequence = |s: &str| s.eq_ignore_ascii_case(sequence);
    let style_of = |names: Vec<&str>| {
        let mut counts: std::collections::BTreeMap<(String, usize), usize> = std::collections::BTreeMap::new();
        for (prefix, number) in names.into_iter().filter_map(name_parts) {
            *counts.entry((prefix.to_owned(), number.len())).or_default() += 1;
        }
        // The commonest; among as common, the first in order.
        let most = counts.values().copied().max()?;
        counts.into_iter().find(|(_, n)| *n == most).map(|(style, _)| style)
    };
    let (prefix, width) = style_of(taken.iter().filter(|(s, _)| in_sequence(s)).map(|(_, n)| n.as_str()).collect())
        .or_else(|| style_of(taken.iter().map(|(_, n)| n.as_str()).collect()))
        .unwrap_or_else(|| ("SH".to_owned(), 3));
    // As digit strings, not integers: a typed number may be longer than
    // any integer holds, and must still be counted (#63).
    let highest = taken
        .iter()
        .filter(|(s, _)| in_sequence(s))
        .filter_map(|(_, n)| name_parts(n))
        .filter(|(p, _)| p.eq_ignore_ascii_case(&prefix))
        .map(|(_, number)| number.trim_start_matches('0'))
        .max_by_key(|digits| (digits.len(), *digits))
        .unwrap_or("");
    // Above every number of these letters in the sequence, so no name
    // there is this one in any case.
    let number = next_ten(highest);
    format!("{prefix}{number:0>width$}")
}

/// A dialog field that offers a value until the user types one: New shot's
/// name (the sequence's next free name, #63), Import's item (the file's
/// name). What the user typed stays; emptying the field hands it back to
/// the offer at the next change. Typed is remembered, not guessed by
/// comparing with the offer: a typed name that happened to be another
/// offer was taken over when the offer changed again.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Offered {
    pub text: String,
    typed: bool,
}

impl Offered {
    pub fn new(offer: String) -> Self {
        Self { text: offer, typed: false }
    }

    /// The user typed `value`.
    pub fn type_text(&mut self, value: String) {
        self.typed = !value.is_empty();
        self.text = value;
    }

    /// What is offered is now `offer`.
    pub fn follow(&mut self, offer: String) {
        if !self.typed {
            self.text = offer;
        }
    }
}

/// The (sequence, name) of a shot's URI, `entity:/shots/<sequence>/<name>`;
/// None for anything else (another kind of entity, more or fewer parts).
pub fn shot_of_uri(uri: &str) -> Option<(String, String)> {
    let (sequence, name) = uri.strip_prefix("entity:/shots/")?.split_once('/')?;
    (!sequence.is_empty() && !name.is_empty() && !name.contains('/')).then(|| (sequence.to_owned(), name.to_owned()))
}

/// The next multiple of ten above the number `digits` writes (`"35"` →
/// `"40"`, `"40"` → `"50"`, `""` → `"10"`), of any length.
fn next_ten(digits: &str) -> String {
    let tens = digits.get(..digits.len().saturating_sub(1)).unwrap_or("");
    let mut out: Vec<u8> = tens.bytes().collect();
    // Add one to the tens, carrying.
    let mut i = out.len();
    loop {
        if i == 0 {
            out.insert(0, b'1');
            break;
        }
        i -= 1;
        if out[i] == b'9' {
            out[i] = b'0';
        } else {
            out[i] += 1;
            break;
        }
    }
    out.push(b'0');
    String::from_utf8(out).expect("digits")
}

/// Why `name` cannot name an edit, for under the Save As field (#25: the
/// refusal showed in the status bar at the window's foot). None when it can.
pub fn edit_name_problem(name: &str) -> Option<String> {
    let name = name.trim();
    if name.is_empty() {
        return Some("Name the edit: letters and digits.".into());
    }
    th_pipe::naming::entity_name_problem(name).map(|problem| format!("{name:?}: an edit's name {problem}."))
}

/// Ops moving every clip of `selection` `delta` frames on its own track:
/// the latest first when moving later, the earliest first when earlier, so
/// none lands on another still to move.
pub fn move_all(edit: &WorkingEdit, selection: &BTreeSet<ClipRef>, delta: Frame) -> Vec<Op<Source>> {
    let mut clips: Vec<(ClipRef, Frame)> = selection.iter().filter_map(|&c| Some((c, edit.spans(c.track).get(c.index)?.0))).collect();
    clips.sort_by_key(|&(c, at)| (c.track.kind == TrackKind::Audio, c.track.index, at));
    if delta > 0 {
        clips.reverse();
    }
    clips.into_iter().map(|(clip, at)| Op::Move { clip, to: clip.track, at: (at + delta).max(0) }).collect()
}

/// `clips`, the latest-starting first: ripple deletes in this order leave
/// the earlier ones where they were.
pub fn latest_spans_first(edit: &WorkingEdit, mut clips: Vec<ClipRef>) -> Vec<ClipRef> {
    clips.sort_by_key(|&c| {
        std::cmp::Reverse((edit.spans(c.track).get(c.index).map_or(0, |s| s.0), c.track.kind == TrackKind::Audio, c.track.index, c.index))
    });
    clips
}

/// What a file to import is: a movie's frames and rate, its length, its
/// sound. Decodes its sound whole, so it runs off the UI thread.
pub fn measure(file: &Path) -> Result<Measure, String> {
    let path = file.to_string_lossy().replace('\\', "/");
    if !file.is_file() {
        return Err(format!("{path}: not a file"));
    }
    let sound = Sound::read(&path).map_err(|e| e.to_string())?;
    let movie = Media::movie(&path).ok().filter(|m| m.frame_count() > 0);
    let frames = movie.as_ref().map(|m| (m.frame_count() as u64, m.rate()));
    let seconds = match (&frames, &sound) {
        (Some((n, (num, den))), _) => *n as f64 * f64::from(*den) / f64::from(*num),
        (None, Some(s)) => s.seconds(),
        (None, None) => return Err(format!("{path}: no picture or sound the player can read")),
    };
    Ok(Measure { frames, seconds, sound: sound.is_some() })
}

/// An item name from a file's name: its stem's letters and digits.
pub fn item_name_of(path: &str) -> String {
    let path = path.trim().trim_matches('"');
    let stem = Path::new(path).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    stem.chars().filter(char::is_ascii_alphanumeric).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::ops::Available;

    struct Anything;
    impl Sources<Source> for Anything {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    /// A multi-range render's frame list (#86) about frames 985-1080:
    /// ascending ranges apart, read from the text its mp4 records.
    fn frame_list() -> impl Strategy<Value = FrameRanges> {
        (985i64..1020, prop::collection::vec((1i64..6, 0i64..8), 1..5)).prop_map(|(start, parts)| {
            let mut at = start;
            let words: Vec<String> = parts
                .into_iter()
                .map(|(gap, len)| {
                    let first = at + gap;
                    at = first + len + 1;
                    format!("{first}-{}", first + len)
                })
                .collect();
            FrameRanges::parse(&words.join(" ")).expect("a frame list")
        })
    }

    fn shot(n: &str) -> Source {
        Source::Shot { shot: format!("entity:/shots/S/{n}"), take: Rule::best() }
    }

    #[test]
    fn shots_lay_end_to_end_on_their_own_frames() {
        let edit = of_shots(&[("entity:/shots/S/010".into(), 1001, 1040), ("entity:/shots/S/020".into(), 1001, 1010)], 24, [1920, 1080]);
        assert_eq!(edit.problem(), None);
        let clips = &edit.video[0].clips;
        assert_eq!((clips[0].at, clips[0].duration, clips[0].source_in), (0, 40, 1001));
        assert_eq!((clips[1].at, clips[1].duration), (40, 10));
        assert_eq!(edit.length(), 50);
    }

    #[test]
    fn timecodes_count_from_the_start() {
        let edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
        assert_eq!(timecode(0, &edit), "01:00:00:00");
        assert_eq!(timecode(24 * 61 + 5, &edit), "01:01:01:05");
    }

    #[test]
    fn edit_points_are_every_clip_end() {
        let edit = of_shots(&[("entity:/shots/S/010".into(), 1001, 1040), ("entity:/shots/S/020".into(), 1001, 1010)], 24, [1920, 1080]);
        assert_eq!(edit_points(&edit), [0, 40, 50]);
    }

    #[test]
    fn items_are_named_from_their_files() {
        assert_eq!(item_name_of("\"C:/refs/cut_v3 final.mov\""), "cutv3final");
        assert_eq!(item_name_of("music.wav"), "music");
    }

    fn apply_all(edit: &WorkingEdit, ops: &[Op<Source>]) -> WorkingEdit {
        let mut edit = edit.clone();
        for op in ops {
            edit = th_edit::ops::apply(&edit, op, &Anything).unwrap_or_else(|e| panic!("{op:?}: {e}"));
        }
        edit
    }

    #[test]
    fn insert_ripples_and_overlay_finds_room_above() {
        let edit = of_shots(&[("entity:/shots/S/010".into(), 1001, 1040)], 24, [1920, 1080]);
        let bin = BinShot { uri: "entity:/shots/S/020".into(), sequence: "S".into(), name: "020".into(), first: 1001, last: 1010 };
        let inserted = apply_all(&edit, &put(&edit, Some(bin.clip(20)), None, 0, false));
        let v1: Vec<(Frame, Frame)> = inserted.video[0].clips.iter().map(|c| (c.at, c.duration)).collect();
        assert_eq!(v1, [(0, 20), (20, 10), (30, 20)]);
        let laid = apply_all(&edit, &put(&edit, Some(bin.clip(20)), None, 0, true));
        assert_eq!(laid.video.len(), 2, "no room on V1: a V2 on top");
        assert_eq!((laid.video[1].clips[0].at, laid.video[0].clips[0].duration), (20, 40));
        // Over the end of V1 there is room on V1 itself.
        let after = apply_all(&edit, &put(&edit, Some(bin.clip(40)), None, 0, true));
        assert_eq!((after.video.len(), after.video[0].clips.len()), (1, 2));
    }

    #[test]
    fn a_movie_with_sound_goes_in_on_both_kinds_of_track() {
        let edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
        let item = BinItem { item: "animatic".into(), version: "v0001".into(), frames: Some(48), sound: Some(50) };
        let (video, audio) = item.clips(0);
        let edit = apply_all(&edit, &put(&edit, video, audio, 0, false));
        assert_eq!((edit.video[0].clips[0].duration, edit.audio[0].clips[0].duration), (48, 48));
        let music = BinItem { item: "music".into(), version: "v0002".into(), frames: None, sound: Some(100) };
        let (video, audio) = music.clips(10);
        assert!(video.is_none());
        let edit = apply_all(&edit, &put(&edit, video, audio, 0, true));
        assert_eq!(edit.audio.len(), 2);
        assert_eq!(edit.audio[1].clips[0].duration, 100);
    }

    #[test]
    fn moving_several_keeps_their_spacing() {
        let edit = of_shots(&[("entity:/shots/S/010".into(), 1001, 1010), ("entity:/shots/S/020".into(), 1001, 1010)], 24, [1920, 1080]);
        let both = BTreeSet::from([ClipRef { track: TrackRef::video(0), index: 0 }, ClipRef { track: TrackRef::video(0), index: 1 }]);
        let later = apply_all(&edit, &move_all(&edit, &both, 5));
        assert_eq!(later.video[0].clips.iter().map(|c| c.at).collect::<Vec<_>>(), [5, 15]);
        let back = apply_all(&later, &move_all(&later, &both, -5));
        assert_eq!(back, edit);
    }

    #[test]
    fn a_published_document_refuses_changes() {
        let edit = of_shots(&[("entity:/shots/S/010".into(), 1001, 1040)], 24, [1920, 1080]);
        let mut doc = Document::new(edit.clone(), Origin::Published { name: "Main".into(), version: "v0002".into() });
        let split = Op::Split { clip: th_edit::document::ClipRef { track: TrackRef::video(0), index: 0 }, at: 10 };
        assert!(doc.apply(std::slice::from_ref(&split), &Anything).is_err());
        assert!(!doc.dirty());
        let mut doc = Document::new(edit, Origin::Untitled);
        doc.apply(std::slice::from_ref(&split), &Anything).unwrap();
        assert!(doc.dirty());
        assert_eq!(doc.title(), "Untitled edit *");
        assert_eq!(doc.edit.video[0].clips.len(), 2);
        assert!(doc.undo());
        assert_eq!(doc.edit.video[0].clips.len(), 1);
        assert!(!doc.dirty(), "undone back to what was opened is no change");
        assert!(doc.redo());
        doc.mark_saved();
        assert_eq!(doc.title(), "Untitled edit");
    }

    #[test]
    fn only_an_untouched_untitled_edit_gives_way_to_another() {
        let shots = [BinShot { uri: "entity:/shots/S/010".into(), sequence: "S".into(), name: "010".into(), first: 1001, last: 1040 }];
        let fill = project_fill(&shots, 24, [1920, 1080]);
        assert!(Document::filled(fill.clone(), Origin::Untitled).replaceable(), "the project's shots as opened");
        assert!(Document::new(of_shots(&[], 24, [1920, 1080]), Origin::Untitled).replaceable(), "an empty edit");
        assert!(!Document::new(fill.clone(), Origin::Untitled).replaceable(), "shots the user asked for");
        assert!(!Document::filled(fill.clone(), Origin::Working { name: "Main".into(), version: None }).replaceable(), "a named edit");
        let mut doc = Document::filled(fill, Origin::Untitled);
        let split = Op::Split { clip: th_edit::document::ClipRef { track: TrackRef::video(0), index: 0 }, at: 10 };
        doc.apply(std::slice::from_ref(&split), &Anything).unwrap();
        assert!(!doc.replaceable(), "changed since it opened");
        assert!(doc.undo());
        assert!(doc.replaceable(), "undone back to how it opened");
    }

    /// A digit string's value as (digits without leading zeros, them), which
    /// orders like the number however long it is.
    fn numeric(digits: &str) -> (usize, String) {
        let d = digits.trim_start_matches('0');
        (d.len(), d.to_owned())
    }

    proptest! {
        /// The name offered is never one taken in the sequence (in any
        /// case), is a good entity name, and is in the commonest style of
        /// the sequence's shots (else the project's): their letters and at
        /// least their digits, above the highest of that style there.
        #[test]
        fn a_new_shot_is_offered_the_next_free_name_in_style(
            taken in prop::collection::vec((prop::sample::select(vec!["SQ010", "SQ020", "sq010"]), prop_oneof![
                (prop::sample::select(vec!["SH", "sh", "", "Shot"]), 0u64..400, 2usize..5).prop_map(|(p, n, w)| format!("{p}{n:0w$}")),
                // Any digits, past what a u64 holds (a typed SH99999999999999999999).
                (prop::sample::select(vec!["SH", ""]), "[0-9]{1,22}").prop_map(|(p, d)| format!("{p}{d}")),
                "[A-Za-z]{1,5}".prop_map(|s| s),
            ]), 0..12),
            sequence in prop::sample::select(vec!["SQ010", "SQ020", "SQ030"]),
        ) {
            let taken: Vec<(String, String)> = taken.into_iter().map(|(s, n)| (s.to_owned(), n)).collect();
            let name = next_shot_name(&taken, sequence);
            prop_assert!(th_pipe::naming::entity_name_problem(&name).is_none(), "{} is not a good name", name);
            prop_assert!(!taken.iter().any(|(s, n)| s.eq_ignore_ascii_case(sequence) && n.eq_ignore_ascii_case(&name)), "{} is taken", name);
            let (prefix, number) = name_parts(&name).unwrap();
            let ours: Vec<&str> = taken.iter().filter(|(s, _)| s.eq_ignore_ascii_case(sequence)).map(|(_, n)| n.as_str()).collect();
            let styled: Vec<(&str, &str)> = ours.iter().copied().filter_map(name_parts).collect();
            let pool: Vec<(&str, &str)> = if styled.is_empty() { taken.iter().filter_map(|(_, n)| name_parts(n)).collect() } else { styled };
            if pool.is_empty() {
                prop_assert_eq!((prefix, number.len()), ("SH", 3));
            } else {
                let count = |p: &str, w: usize| pool.iter().filter(|(q, d)| *q == p && d.len() == w).count();
                let most = pool.iter().map(|(p, d)| count(p, d.len())).max().unwrap();
                // Padded to the style's digits, or longer once the number
                // outgrows them (SH090 → SH100).
                let fits = (1..=number.len()).any(|w| count(prefix, w) == most && (w == number.len() || !number.starts_with('0')));
                prop_assert!(fits, "{} is not in the commonest style", name);
            }
            let above = taken
                .iter()
                .filter(|(s, _)| s.eq_ignore_ascii_case(sequence))
                .filter_map(|(_, n)| name_parts(n))
                .filter(|(p, _)| p.eq_ignore_ascii_case(prefix))
                .map(|(_, d)| numeric(d))
                .max();
            prop_assert!(above.is_none_or(|a| numeric(number) > a), "{} is not above every number taken", name);
        }
    }

    proptest! {
        /// next_ten is (n / 10 + 1) * 10 wherever an integer holds n.
        #[test]
        fn next_ten_counts_like_integers(n in any::<u64>(), zeros in 0usize..3) {
            let digits = format!("{}{n}", "0".repeat(zeros));
            let digits = digits.trim_start_matches('0');
            let want = (u128::from(n) / 10 + 1) * 10;
            prop_assert_eq!(next_ten(digits), want.to_string());
        }
    }

    /// What the user does in the New shot dialog.
    #[derive(Debug, Clone)]
    enum Step {
        Sequence(String),
        Name(String),
    }

    proptest! {
        /// Through any typing in the New shot dialog, the name is the offer
        /// for the sequence on screen until the user types one of their own,
        /// which then stays; an emptied field stays empty until the
        /// sequence changes, and then takes the offer again.
        #[test]
        fn the_offered_name_follows_the_sequence_until_one_is_typed(
            taken in prop::collection::vec((prop::sample::select(vec!["SQ010", "SQ020"]), "SH0[1-5]0"), 0..6),
            steps in prop::collection::vec(prop_oneof![
                prop::sample::select(vec!["SQ010", "SQ020", "SQ030", ""]).prop_map(|s| Step::Sequence(s.into())),
                prop::sample::select(vec!["", "SH010", "SH020", "SH060", "Hero"]).prop_map(|s| Step::Name(s.into())),
            ], 0..12),
        ) {
            let taken: Vec<(String, String)> = taken.into_iter().map(|(s, n)| (s.to_owned(), n)).collect();
            let mut sequence = "SQ010".to_owned();
            let mut field = Offered::new(next_shot_name(&taken, &sequence));
            // The model: what the user typed last, unless they emptied the
            // field and changed the sequence since.
            let mut typed: Option<String> = None;
            for step in steps {
                match step {
                    Step::Sequence(s) => {
                        field.follow(next_shot_name(&taken, &s));
                        if typed.as_deref() == Some("") {
                            typed = None;
                        }
                        sequence = s;
                    }
                    Step::Name(n) => {
                        field.type_text(n.clone());
                        typed = Some(n);
                    }
                }
                match &typed {
                    Some(t) => prop_assert_eq!(&field.text, t, "what the user typed was replaced"),
                    None => prop_assert_eq!(&field.text, &next_shot_name(&taken, &sequence), "the offer did not follow"),
                }
            }
        }

        /// Import's item through any paths and typing: the file's name until
        /// the user types one, which stays, though it is some file's name
        /// (the old guess took a typed `animatic` over once animatic.mov was
        /// picked and then another file).
        #[test]
        fn the_item_follows_the_file_until_one_is_typed(
            steps in prop::collection::vec(prop_oneof![
                prop::sample::select(vec!["C:/m/animatic.mov", "C:/m/other.mov", "", "C:/m/edit_v2.wav"]).prop_map(|s| Step::Sequence(s.into())),
                prop::sample::select(vec!["", "animatic", "other", "Mine"]).prop_map(|s| Step::Name(s.into())),
            ], 0..12),
        ) {
            let mut path = String::new();
            let mut field = Offered::default();
            let mut typed: Option<String> = None;
            for step in steps {
                match step {
                    Step::Sequence(p) => {
                        field.follow(item_name_of(&p));
                        if typed.as_deref() == Some("") {
                            typed = None;
                        }
                        path = p;
                    }
                    Step::Name(n) => {
                        field.type_text(n.clone());
                        typed = Some(n);
                    }
                }
                match &typed {
                    Some(t) => prop_assert_eq!(&field.text, t, "what the user typed was replaced"),
                    None => prop_assert_eq!(&field.text, &item_name_of(&path), "the item did not follow the file"),
                }
            }
        }

        /// shot_of_uri reads back every shot URI the editor writes, and
        /// nothing that is not one.
        #[test]
        fn a_shot_uri_reads_back(sequence in "[A-Za-z0-9]{1,8}", name in "[A-Za-z0-9]{1,8}", junk in "[a-z/:]{0,20}") {
            let uri = format!("entity:/shots/{sequence}/{name}");
            prop_assert_eq!(shot_of_uri(&uri), Some((sequence.clone(), name.clone())));
            prop_assert_eq!(shot_of_uri(&format!("{uri}/more")), None);
            prop_assert_eq!(shot_of_uri(&format!("entity:/assets/{sequence}/{name}")), None);
            prop_assert_eq!(shot_of_uri(&format!("entity:/shots/{sequence}")), None);
            if let Some((s, n)) = shot_of_uri(&junk) {
                prop_assert_eq!(format!("entity:/shots/{s}/{n}"), junk);
            }
        }
    }

    #[test]
    fn a_new_shot_after_sh030_is_sh040() {
        let taken: Vec<(String, String)> = ["SH010", "SH020", "SH030"].iter().map(|n| ("SQ010".to_owned(), n.to_string())).collect();
        assert_eq!(next_shot_name(&taken, "SQ010"), "SH040");
        assert_eq!(next_shot_name(&taken, "SQ020"), "SH010", "another sequence, the project's style");
        assert_eq!(next_shot_name(&[], "SQ010"), "SH010");
        let bare: Vec<(String, String)> = vec![("A".into(), "010".into()), ("A".into(), "025".into())];
        assert_eq!(next_shot_name(&bare, "A"), "030");
    }

    #[test]
    fn an_edit_name_problem_says_why() {
        assert_eq!(edit_name_problem(" Main "), None);
        assert!(edit_name_problem("").unwrap().contains("Name the edit"));
        assert!(edit_name_problem("sq010 cut!").unwrap().contains("letters and digits"));
        assert!(edit_name_problem("Con").unwrap().contains("Windows"));
    }

    proptest! {
        /// Inserting bin shots one after another at the playhead, the
        /// playhead moving to [`put_end`] after each, puts them in the order
        /// picked, end to end from where the playhead started (a cut point:
        /// the start, a cut, or the end), and leaves the playhead at their
        /// end (#25). Overlay parks it at the end of the laid clip too.
        #[test]
        fn inserts_at_an_advancing_playhead_append_in_order(
            existing in prop::collection::vec(1i64..30, 0..4),
            picks in prop::collection::vec(1i64..30, 1..5),
            start in 0usize..5,
            overlay in any::<bool>(),
        ) {
            let shots: Vec<(String, Frame, Frame)> = existing
                .iter()
                .enumerate()
                .map(|(i, n)| (format!("entity:/shots/S/{:03}", (i + 1) * 10), 1001, 1000 + n))
                .collect();
            let mut edit = of_shots(&shots, 24, [1920, 1080]);
            let points = edit_points(&edit);
            let mut playhead = points.get(start % points.len().max(1)).copied().unwrap_or(0);
            let from = playhead;
            let mut uris = Vec::new();
            for (i, n) in picks.iter().enumerate() {
                let uri = format!("entity:/shots/P/{:03}", (i + 1) * 10);
                let bin = BinShot { uri: uri.clone(), sequence: "P".into(), name: format!("{:03}", (i + 1) * 10), first: 1001, last: 1000 + n };
                let clip = bin.clip(playhead);
                let end = put_end(Some(&clip), None).unwrap();
                prop_assert_eq!(end, playhead + n);
                edit = apply_all(&edit, &put(&edit, Some(clip), None, 0, overlay));
                playhead = end;
                uris.push(uri);
            }
            let total: Frame = picks.iter().sum();
            prop_assert_eq!(playhead, from + total);
            // Each pick sits where the playhead was when it went in, in order.
            let mut at = from;
            for (uri, n) in uris.iter().zip(&picks) {
                let found = edit.video.iter().flat_map(|t| t.clips.iter()).find(|c| {
                    matches!(&c.source, Source::Shot { shot, .. } if shot == uri)
                });
                let found = found.expect("every pick is in the edit");
                prop_assert_eq!((found.at, found.duration), (at, *n));
                at += n;
            }
        }
    }

    proptest! {
        /// A new edit holds every shot with a play range, once, in the
        /// order listed, end to end from frame 0 with no gaps, each playing
        /// exactly its play range in its best take; sound starts empty.
        #[test]
        fn a_new_edit_is_every_shot_end_to_end(
            ranges in prop::collection::vec((990i64..1100, -3i64..120), 0..12),
        ) {
            let shots: Vec<BinShot> = ranges
                .iter()
                .enumerate()
                .map(|(i, (first, length))| BinShot {
                    uri: format!("entity:/shots/S/{:03}", (i + 1) * 10),
                    sequence: "S".into(),
                    name: format!("{:03}", (i + 1) * 10),
                    first: *first,
                    last: first + length - 1,
                })
                .collect();
            let edit = project_fill(&shots, 24, [1920, 1080]);
            let playable: Vec<&BinShot> = shots.iter().filter(|s| s.last >= s.first).collect();
            prop_assert_eq!(edit.video.len(), 1);
            prop_assert_eq!(edit.audio.len(), 1);
            prop_assert!(edit.audio[0].clips.is_empty());
            let clips = &edit.video[0].clips;
            prop_assert_eq!(clips.len(), playable.len());
            let mut at = 0;
            for (clip, shot) in clips.iter().zip(&playable) {
                prop_assert_eq!(clip.at, at);
                prop_assert_eq!(clip.source_in, shot.first);
                prop_assert_eq!(clip.duration, shot.last - shot.first + 1);
                prop_assert_eq!(&clip.source, &Source::Shot { shot: shot.uri.clone(), take: Rule::best() });
                at += clip.duration;
            }
            prop_assert_eq!(edit.length(), at);
        }

        /// Every bound clip plays, at each edit frame it covers, the media
        /// index of its source frame there; frames before the media's first
        /// are left out, and so are a multi-range render's gaps (#86): a
        /// listed frame plays its place in the list; unbound clips play
        /// nothing.
        #[test]
        fn clips_play_their_source_frames(
            clips in prop::collection::vec((0i64..30, 1i64..60, 990i64..1010, 0usize..3, 0i64..5, 0i64..5), 1..6),
            firsts in prop::collection::vec(990i64..1010, 3),
            lists in prop::collection::vec(prop::option::of(frame_list()), 3),
            unbound in 0usize..4,
        ) {
            let mut edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
            let names = ["010", "020", "030"];
            let mut at = 0;
            let mut track = Vec::new();
            for (gap, duration, source_in, s, fade_in, fade_out) in clips {
                at += gap;
                let fade_in = fade_in.min(duration);
                let fade_out = fade_out.min(duration - fade_in);
                track.push(VideoClip { at, duration, source_in, source: shot(names[s]), fade_in, fade_out, opacity: 1.0 });
                at += duration;
            }
            edit.video.push(Track { name: "V1".into(), clips: track.clone() });
            let bind = |source: &Source| {
                let Source::Shot { shot, .. } = source else { return None };
                let s = names.iter().position(|n| shot.ends_with(n))?;
                (s != unbound).then(|| Bound { source: s, first: firsts[s], ranges: lists[s].clone() })
            };
            let engine = engine_clips(&edit, bind);
            for c in &track {
                let b = bind(&c.source);
                for f in c.at..c.at + c.duration {
                    let source_frame = c.source_in + f - c.at;
                    let playing: Vec<&Clip> = engine.iter().filter(|e| e.covers(f as usize)).collect();
                    let index = b.as_ref().and_then(|b| match &b.ranges {
                        Some(list) => list.index_of(source_frame),
                        None => usize::try_from(source_frame - b.first).ok(),
                    });
                    match (&b, index) {
                        (Some(b), Some(index)) => {
                            prop_assert_eq!(playing.len(), 1);
                            let key = playing[0].key(f as usize);
                            prop_assert_eq!(key.source, b.source);
                            prop_assert_eq!(key.index, index);
                        }
                        _ => prop_assert!(playing.is_empty(), "frame {} plays though its source does not", f),
                    }
                }
            }
        }

        /// The editor's picture is the farm render's (#60): at every frame
        /// the engine's layers are `th_edit::picture`'s, clip for clip, and
        /// each shows as much of itself, its opacity times its fade; a clip
        /// of a multi-range render (#86) is transparent in its gaps and its
        /// pieces fade as the whole clip does.
        #[test]
        fn the_engine_blends_as_the_edit_does(
            tracks in prop::collection::vec(
                prop::collection::vec((0i64..6, 1i64..20, 0i64..6, 0i64..6, prop::sample::select(vec![1.0f64, 1.0, 0.75, 0.5, 0.0])), 0..4),
                1..4,
            ),
            list in prop::option::of(frame_list()),
        ) {
            use th_edit::document::Clip as _;
            use th_edit::picture::{Playable, layers_at};
            struct Every(Option<FrameRanges>);
            impl Playable<Source> for Every {
                fn playable(&self, _: &Source, frame: Frame) -> bool {
                    self.0.as_ref().is_none_or(|list| list.index_of(frame).is_some())
                }
            }
            let mut edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
            for (t, clips) in tracks.iter().enumerate() {
                let mut at = 0;
                let mut track = Vec::new();
                for &(gap, duration, fade_in, fade_out, opacity) in clips {
                    at += gap;
                    let fade_in = fade_in.min(duration);
                    let fade_out = fade_out.min(duration - fade_in);
                    track.push(VideoClip { at, duration, source_in: 1001, source: shot("010"), fade_in, fade_out, opacity });
                    at += duration;
                }
                edit.video.push(Track { name: format!("V{}", t + 1), clips: track });
            }
            prop_assert_eq!(edit.problem(), None);
            let first = list.as_ref().map_or(1001, |l| l.ranges()[0].0);
            let timeline =
                crate::engine::Timeline::new(engine_clips(&edit, |_| Some(Bound { source: 0, first, ranges: list.clone() })));
            let every = Every(list.clone());
            // An engine clip as picture layers name it: its track and the
            // edit clip there it is (a piece of).
            let named = |clip: usize, at: Frame| {
                let track = timeline.clips[clip].track;
                (track, edit.video[track].clips.iter().position(|c| c.covers(at)).expect("an engine clip is in an edit clip"))
            };
            for at in 0..edit.length() {
                let want: Vec<((usize, usize), u32)> =
                    layers_at(&edit, at, &every).iter().map(|l| ((l.track, l.clip), l.opacity.to_bits())).collect();
                let got: Vec<((usize, usize), u32)> =
                    timeline.layers(at as usize, |_| true).iter().map(|l| (named(l.clip, at), l.opacity.to_bits())).collect();
                prop_assert_eq!(got, want, "frame {}", at);
            }
        }
    }
}
