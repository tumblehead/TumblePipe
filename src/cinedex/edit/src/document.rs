//! The edit document (`designs/edit-entity.md` §2.2).
//!
//! A working version, `edits:/<name>/edit/<name>_edit_v0007.json`, is a
//! [`WorkingEdit`]: its shot clips name a take *rule* that floats
//! (`version: "latest"`, or a department left open for the best
//! available). A publish holds a [`FrozenEdit`]: every rule resolved to a
//! department, kind, layer and version, with the media it played. They are
//! separate types, so a published document with a floating rule does not
//! parse.
//!
//! Positions are edit frames from 0 (`start_timecode` labels them);
//! `source_in` is in the source's own frame numbers (a shot's are
//! 1001-based), so a clip shows `source_in ..= source_in + duration - 1`.
//! Tracks are listed bottom first, and clips on one track never overlap.
//! Every field is required and no other is allowed, but for a video clip's
//! `opacity`, written only when it is not 1, and its `transform`, written
//! only when it moves, scales or turns the picture.

use serde::{Deserialize, Deserializer, Serialize};
use th_playlist::Media;

/// An edit frame, or a source frame.
pub type Frame = i64;

/// An edit: video tracks stacked bottom first (the top-most playable clip
/// shows; fades blend over what is below), audio tracks mixed, markers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Edit<S> {
    pub fps: u32,
    pub resolution: [u32; 2],
    /// The timecode of edit frame 0, `HH:MM:SS:FF`.
    pub start_timecode: String,
    pub video: Vec<Track<VideoClip<S>>>,
    pub audio: Vec<Track<AudioClip<S>>>,
    pub markers: Vec<Marker>,
}

/// An edit as it is worked on: take rules float.
pub type WorkingEdit = Edit<Source>;

/// An edit as a publish holds it: every rule resolved.
pub type FrozenEdit = Edit<FrozenSource>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Track<C> {
    pub name: String,
    /// In position order, never overlapping.
    pub clips: Vec<C>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VideoClip<S> {
    /// Where it starts in the edit.
    pub at: Frame,
    pub duration: Frame,
    /// The source frame it starts on.
    pub source_in: Frame,
    pub source: S,
    /// Frames it fades in over what is below (a dissolve, or from black).
    pub fade_in: Frame,
    /// Frames it fades out to what is below.
    pub fade_out: Frame,
    /// How much of it shows over what is below, 0 to 1, its fades on top
    /// (#60). Written only when it is not 1, and read as 1 when it is not
    /// there: an edit at full opacity is the bytes it always was, and an
    /// editor from before opacity reads it.
    #[serde(default = "full", skip_serializing_if = "is_full")]
    pub opacity: f64,
    /// Where its picture sits in the edit's. Written only when it is not
    /// [`Transform::IDENTITY`], and read as that when it is not there: an
    /// edit that transforms nothing is the bytes it always was, and an
    /// editor from before transforms reads it.
    #[serde(default, skip_serializing_if = "Transform::is_identity")]
    pub transform: Transform,
}

/// Where a video clip's picture sits in the edit's picture. The clip is
/// first fitted into the edit's picture whole and centred, as every clip
/// is; then it is scaled and turned about the picture's centre, then moved.
/// The units do not depend on anyone's resolution, so a proxy and the full
/// picture agree.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Transform {
    /// How far right it is moved, in picture heights (0.5 at 1080 lines:
    /// 540 pixels).
    pub x: f64,
    /// How far up, in picture heights.
    pub y: f64,
    /// Its size, of the fitted picture: 1 as fitted, 0.5 half as wide and
    /// as tall.
    pub scale: f64,
    /// Degrees it is turned, anticlockwise.
    pub rotation: f64,
}

impl Transform {
    /// As fitted: not moved, scaled or turned.
    pub const IDENTITY: Transform = Transform { x: 0.0, y: 0.0, scale: 1.0, rotation: 0.0 };
    /// The furthest a clip moves either way, in picture heights.
    pub const MAX_MOVE: f64 = 10.0;
    /// The smallest and largest scale.
    pub const MIN_SCALE: f64 = 0.01;
    pub const MAX_SCALE: f64 = 100.0;
    /// The most a clip turns either way, in degrees (ten turns).
    pub const MAX_ROTATION: f64 = 3600.0;

    /// Whether it leaves the clip as fitted.
    #[allow(clippy::float_cmp)]
    pub fn is_identity(&self) -> bool {
        *self == Transform::IDENTITY
    }

    /// What makes it no transform, if anything: a number that is none, or
    /// out of range.
    pub fn problem(&self) -> Option<String> {
        let within = |value: f64, min: f64, max: f64| value.is_finite() && (min..=max).contains(&value);
        if !within(self.x, -Self::MAX_MOVE, Self::MAX_MOVE) || !within(self.y, -Self::MAX_MOVE, Self::MAX_MOVE) {
            return Some(format!("a position of {}, {}: it is within {} picture heights", self.x, self.y, Self::MAX_MOVE));
        }
        if !within(self.scale, Self::MIN_SCALE, Self::MAX_SCALE) {
            return Some(format!("a scale of {}: it is {} to {}", self.scale, Self::MIN_SCALE, Self::MAX_SCALE));
        }
        if !within(self.rotation, -Self::MAX_ROTATION, Self::MAX_ROTATION) {
            return Some(format!("a rotation of {} degrees: it is within {}", self.rotation, Self::MAX_ROTATION));
        }
        None
    }
}

impl Default for Transform {
    fn default() -> Self {
        Transform::IDENTITY
    }
}

/// A clip's opacity when its document gives none.
pub const FULL: f64 = 1.0;

fn full() -> f64 {
    FULL
}

#[allow(clippy::trivially_copy_pass_by_ref, clippy::float_cmp)]
fn is_full(opacity: &f64) -> bool {
    *opacity == FULL
}

impl<S> VideoClip<S> {
    /// A clip of `source` at `at`, no fades, at full opacity, as fitted.
    pub fn new(at: Frame, duration: Frame, source_in: Frame, source: S) -> Self {
        VideoClip { at, duration, source_in, source, fade_in: 0, fade_out: 0, opacity: FULL, transform: Transform::IDENTITY }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioClip<S> {
    pub at: Frame,
    pub duration: Frame,
    pub source_in: Frame,
    pub source: S,
    /// Its level, in decibels (0 = as recorded).
    pub gain_db: f64,
    pub fade_in: Frame,
    pub fade_out: Frame,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Marker {
    pub at: Frame,
    pub note: String,
}

/// Where a working clip's frames come from.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Source {
    /// A shot, existing or planned (`entity:/shots/<sequence>/<shot>`): the
    /// take its rule picks.
    Shot { shot: String, take: Rule },
    /// A media library item (`export:/media/<item>/<version>/`).
    Media { item: String, version: VersionRule },
    /// Text on black.
    Slug { text: String },
}

/// Which of a shot's takes a clip shows. A field left `null` is open: with
/// no department it is the best available (the newest take of the most
/// downstream department); each field set narrows it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    #[serde(deserialize_with = "required")]
    pub department: Option<String>,
    #[serde(deserialize_with = "required")]
    pub kind: Option<TakeKind>,
    #[serde(deserialize_with = "required")]
    pub layer: Option<String>,
    pub version: VersionRule,
}

impl Rule {
    /// The best available take, newest.
    pub fn best() -> Self {
        Rule { department: None, kind: None, layer: None, version: VersionRule::Latest }
    }
}

/// A playblast (movie) or a render (EXRs).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TakeKind {
    Playblast,
    Render,
}

/// `"latest"`, or a pinned version.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub enum VersionRule {
    Latest,
    Pinned(Version),
}

impl TryFrom<String> for VersionRule {
    type Error = String;
    fn try_from(text: String) -> Result<Self, String> {
        if text == "latest" { Ok(VersionRule::Latest) } else { Version::try_from(text).map(VersionRule::Pinned) }
    }
}

impl From<VersionRule> for String {
    fn from(rule: VersionRule) -> String {
        match rule {
            VersionRule::Latest => "latest".into(),
            VersionRule::Pinned(version) => version.0,
        }
    }
}

/// A version name: `v` and at least four digits (`v0012`, `v10000`).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct Version(String);

impl Version {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<String> for Version {
    type Error = String;
    fn try_from(text: String) -> Result<Self, String> {
        let digits = text.strip_prefix('v').unwrap_or("");
        if digits.len() >= 4 && digits.bytes().all(|b| b.is_ascii_digit()) {
            Ok(Version(text))
        } else {
            Err(format!("'{text}' is not a version (v and at least four digits)"))
        }
    }
}

impl From<Version> for String {
    fn from(version: Version) -> String {
        version.0
    }
}

/// Where a published clip's frames came from: a working [`Source`] with its
/// rule resolved and the media it played.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum FrozenSource {
    /// `take` is null when the shot had no take its rule picks: a planned
    /// shot, or one with nothing playblasted or rendered yet.
    Shot {
        shot: String,
        #[serde(deserialize_with = "required")]
        take: Option<FrozenTake>,
    },
    Media {
        item: String,
        version: Version,
        media: Media,
    },
    Slug {
        text: String,
    },
}

/// A take, concretely.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenTake {
    pub department: String,
    pub kind: TakeKind,
    /// A render's layer (`"layers"` for a stack); null for a playblast.
    #[serde(deserialize_with = "required")]
    pub layer: Option<String>,
    pub version: Version,
    pub media: Media,
}

/// An `Option` field that must be present (as `null` or a value): serde
/// otherwise reads a missing `Option` as `None`.
fn required<'de, D: Deserializer<'de>, T: Deserialize<'de>>(deserializer: D) -> Result<Option<T>, D::Error> {
    Option::<T>::deserialize(deserializer)
}

/// What every clip has: a span of the edit and the source frames it shows,
/// and its fades.
pub trait Clip: Clone {
    type Source;
    fn at(&self) -> Frame;
    fn duration(&self) -> Frame;
    fn source_in(&self) -> Frame;
    fn source(&self) -> &Self::Source;
    fn fade_in(&self) -> Frame;
    fn fade_out(&self) -> Frame;
    /// A video clip's opacity; an audio clip's is 1.
    fn opacity(&self) -> f64;
    /// A video clip's transform; an audio clip's is the identity.
    fn transform(&self) -> Transform;
    fn set_at(&mut self, at: Frame);
    fn set_duration(&mut self, duration: Frame);
    fn set_source_in(&mut self, source_in: Frame);
    fn set_fades(&mut self, fade_in: Frame, fade_out: Frame);
    fn set_source(&mut self, source: Self::Source);

    /// The edit frame after its last.
    fn end(&self) -> Frame {
        self.at() + self.duration()
    }

    /// The source frame after its last.
    fn source_out(&self) -> Frame {
        self.source_in() + self.duration()
    }

    /// Whether it is on screen (or heard) at edit frame `at`.
    fn covers(&self, at: Frame) -> bool {
        self.at() <= at && at < self.end()
    }
}

macro_rules! clip_impl {
    ($clip:ident, $c:ident => $opacity:expr, $transform:expr) => {
        impl<S: Clone> Clip for $clip<S> {
            type Source = S;
            fn at(&self) -> Frame {
                self.at
            }
            fn duration(&self) -> Frame {
                self.duration
            }
            fn source_in(&self) -> Frame {
                self.source_in
            }
            fn source(&self) -> &S {
                &self.source
            }
            fn fade_in(&self) -> Frame {
                self.fade_in
            }
            fn fade_out(&self) -> Frame {
                self.fade_out
            }
            fn opacity(&self) -> f64 {
                let $c = self;
                $opacity
            }
            fn transform(&self) -> Transform {
                let $c = self;
                $transform
            }
            fn set_at(&mut self, at: Frame) {
                self.at = at;
            }
            fn set_duration(&mut self, duration: Frame) {
                self.duration = duration;
            }
            fn set_source_in(&mut self, source_in: Frame) {
                self.source_in = source_in;
            }
            fn set_source(&mut self, source: S) {
                self.source = source;
            }
            fn set_fades(&mut self, fade_in: Frame, fade_out: Frame) {
                self.fade_in = fade_in;
                self.fade_out = fade_out;
            }
        }
    };
}

clip_impl!(VideoClip, c => c.opacity, c.transform);
clip_impl!(AudioClip, _c => FULL, Transform::IDENTITY);

/// Video or audio.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TrackKind {
    Video,
    Audio,
}

/// A track of an edit: its kind and its index (bottom first).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrackRef {
    pub kind: TrackKind,
    pub index: usize,
}

impl TrackRef {
    pub fn video(index: usize) -> Self {
        TrackRef { kind: TrackKind::Video, index }
    }

    pub fn audio(index: usize) -> Self {
        TrackRef { kind: TrackKind::Audio, index }
    }
}

/// A clip of an edit: its track and its index there.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClipRef {
    pub track: TrackRef,
    pub index: usize,
}

impl<S: Clone> Edit<S> {
    /// An empty edit.
    pub fn new(fps: u32, resolution: [u32; 2], start_timecode: &str) -> Self {
        Edit { fps, resolution, start_timecode: start_timecode.into(), video: Vec::new(), audio: Vec::new(), markers: Vec::new() }
    }

    /// Its length: the end of its last clip, video or audio.
    pub fn length(&self) -> Frame {
        let video = self.video.iter().flat_map(|t| t.clips.iter().map(Clip::end));
        let audio = self.audio.iter().flat_map(|t| t.clips.iter().map(Clip::end));
        video.chain(audio).max().unwrap_or(0)
    }

    /// How many tracks of `kind` it has.
    pub fn track_count(&self, kind: TrackKind) -> usize {
        match kind {
            TrackKind::Video => self.video.len(),
            TrackKind::Audio => self.audio.len(),
        }
    }

    /// Every track, video then audio.
    pub fn tracks(&self) -> impl Iterator<Item = TrackRef> + '_ {
        (0..self.video.len()).map(TrackRef::video).chain((0..self.audio.len()).map(TrackRef::audio))
    }

    /// The spans `(at, end)` of the clips on `track`.
    pub fn spans(&self, track: TrackRef) -> Vec<(Frame, Frame)> {
        match track.kind {
            TrackKind::Video => self.video[track.index].clips.iter().map(|c| (c.at, c.end())).collect(),
            TrackKind::Audio => self.audio[track.index].clips.iter().map(|c| (c.at, c.end())).collect(),
        }
    }

    /// What makes it no edit, if anything: a rate or resolution of zero, a
    /// timecode that is none at its rate, a track without a name or with
    /// two of one name, or a clip that is empty, before frame 0, out of
    /// order or over another on its track, or whose fades do not fit in it.
    pub fn problem(&self) -> Option<String> {
        if self.fps == 0 {
            return Some("an fps of 0".into());
        }
        if self.resolution.contains(&0) {
            return Some(format!("a resolution of {}x{}", self.resolution[0], self.resolution[1]));
        }
        if let Err(why) = timecode_frames(&self.start_timecode, self.fps) {
            return Some(why);
        }
        let names = |tracks: Vec<&str>, kind: &str| {
            for (i, name) in tracks.iter().enumerate() {
                if name.is_empty() || tracks[..i].contains(name) {
                    return Some(format!("{kind} track {i} is named '{name}', empty or twice"));
                }
            }
            None
        };
        if let Some(why) = names(self.video.iter().map(|t| t.name.as_str()).collect(), "video") {
            return Some(why);
        }
        if let Some(why) = names(self.audio.iter().map(|t| t.name.as_str()).collect(), "audio") {
            return Some(why);
        }
        for track in self.tracks() {
            let name = self.track_name(track);
            let fades = self.fades(track);
            let mut end = 0;
            for (i, (&(at, clip_end), &(fade_in, fade_out))) in self.spans(track).iter().zip(&fades).enumerate() {
                let duration = clip_end - at;
                if at < 0 || duration < 1 {
                    return Some(format!("{name} clip {i} spans frames {at} to {clip_end}"));
                }
                if at < end {
                    return Some(format!("{name} clip {i} at {at} is over the clip before it, which ends at {end}"));
                }
                if fade_in < 0 || fade_out < 0 || fade_in + fade_out > duration {
                    return Some(format!("{name} clip {i} fades {fade_in} in and {fade_out} out in {duration} frames"));
                }
                end = clip_end;
            }
        }
        for (t, track) in self.video.iter().enumerate() {
            if let Some((i, c)) = track.clips.iter().enumerate().find(|(_, c)| !(0.0..=1.0).contains(&c.opacity)) {
                return Some(format!("{} clip {i} has an opacity of {}", self.video[t].name, c.opacity));
            }
            if let Some((i, why)) = track.clips.iter().enumerate().find_map(|(i, c)| Some((i, c.transform.problem()?))) {
                return Some(format!("{} clip {i} has {why}", self.video[t].name));
            }
        }
        if let Some(marker) = self.markers.iter().find(|m| m.at < 0) {
            return Some(format!("a marker at {}", marker.at));
        }
        None
    }

    /// The same edit over other sources: `f` of each clip's source, in
    /// track order, video first. Positions, spans and fades are kept.
    pub fn try_map<T, E>(&self, mut f: impl FnMut(&S) -> Result<T, E>) -> Result<Edit<T>, E> {
        let mut video = Vec::with_capacity(self.video.len());
        for t in &self.video {
            let mut clips = Vec::with_capacity(t.clips.len());
            for c in &t.clips {
                let VideoClip { at, duration, source_in, fade_in, fade_out, opacity, transform, .. } = *c;
                clips.push(VideoClip { at, duration, source_in, source: f(&c.source)?, fade_in, fade_out, opacity, transform });
            }
            video.push(Track { name: t.name.clone(), clips });
        }
        let mut audio = Vec::with_capacity(self.audio.len());
        for t in &self.audio {
            let mut clips = Vec::with_capacity(t.clips.len());
            for c in &t.clips {
                let AudioClip { at, duration, source_in, gain_db, fade_in, fade_out, .. } = *c;
                clips.push(AudioClip { at, duration, source_in, source: f(&c.source)?, gain_db, fade_in, fade_out });
            }
            audio.push(Track { name: t.name.clone(), clips });
        }
        Ok(Edit {
            fps: self.fps,
            resolution: self.resolution,
            start_timecode: self.start_timecode.clone(),
            video,
            audio,
            markers: self.markers.clone(),
        })
    }

    pub fn track_name(&self, track: TrackRef) -> &str {
        match track.kind {
            TrackKind::Video => &self.video[track.index].name,
            TrackKind::Audio => &self.audio[track.index].name,
        }
    }

    fn fades(&self, track: TrackRef) -> Vec<(Frame, Frame)> {
        match track.kind {
            TrackKind::Video => self.video[track.index].clips.iter().map(|c| (c.fade_in, c.fade_out)).collect(),
            TrackKind::Audio => self.audio[track.index].clips.iter().map(|c| (c.fade_in, c.fade_out)).collect(),
        }
    }
}

impl<S: Clone + for<'de> Deserialize<'de>> Edit<S> {
    /// The edit in `json`, checked (see [`Edit::problem`]).
    pub fn parse(json: &str) -> Result<Self, String> {
        let edit: Self = serde_json::from_str(json).map_err(|e| format!("not an edit: {e}"))?;
        match edit.problem() {
            Some(why) => Err(format!("not an edit: {why}")),
            None => Ok(edit),
        }
    }
}

impl<S: Serialize> Edit<S> {
    /// As JSON, indented.
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).expect("an edit serializes")
    }
}

/// The frame count `timecode` (`HH:MM:SS:FF`, non-drop) stands for at `fps`.
pub fn timecode_frames(timecode: &str, fps: u32) -> Result<Frame, String> {
    let bad = || format!("'{timecode}' is not a timecode (HH:MM:SS:FF) at {fps} fps");
    let parts: Vec<&str> = timecode.split(':').collect();
    if parts.len() != 4 || parts.iter().any(|p| p.len() != 2 || !p.bytes().all(|b| b.is_ascii_digit())) {
        return Err(bad());
    }
    let n: Vec<Frame> = parts.iter().map(|p| p.parse().expect("two digits")).collect();
    if n[1] >= 60 || n[2] >= 60 || n[3] >= Frame::from(fps) {
        return Err(bad());
    }
    Ok(((n[0] * 60 + n[1]) * 60 + n[2]) * Frame::from(fps) + n[3])
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    const WORKING: &str = r#"{
      "fps": 24,
      "resolution": [1920, 1080],
      "start_timecode": "01:00:00:00",
      "video": [
        {"name": "V1", "clips": [
          {"at": 0, "duration": 2400, "source_in": 1,
           "source": {"kind": "media", "item": "animatic", "version": "latest"},
           "fade_in": 0, "fade_out": 0}]},
        {"name": "V2", "clips": [
          {"at": 0, "duration": 86, "source_in": 1005,
           "source": {"kind": "shot", "shot": "entity:/shots/HideAndReek/010",
                      "take": {"department": null, "kind": null, "layer": null, "version": "latest"}},
           "fade_in": 0, "fade_out": 0}]},
        {"name": "V3", "clips": [
          {"at": 74, "duration": 60, "source_in": 1001,
           "source": {"kind": "shot", "shot": "entity:/shots/HideAndReek/015",
                      "take": {"department": "animation", "kind": "playblast", "layer": null, "version": "v0012"}},
           "fade_in": 12, "fade_out": 0}]}],
      "audio": [
        {"name": "A1", "clips": [
          {"at": 0, "duration": 2400, "source_in": 0,
           "source": {"kind": "media", "item": "music", "version": "latest"},
           "gain_db": 0.0, "fade_in": 0, "fade_out": 24}]}],
      "markers": [{"at": 312, "note": "hold longer on the reveal"}]
    }"#;

    #[test]
    fn the_design_example_parses_and_round_trips() {
        let edit = WorkingEdit::parse(WORKING).unwrap();
        assert_eq!(edit.video.len(), 3);
        assert_eq!(edit.length(), 2400);
        assert_eq!(
            edit.video[2].clips[0].source,
            Source::Shot {
                shot: "entity:/shots/HideAndReek/015".into(),
                take: Rule {
                    department: Some("animation".into()),
                    kind: Some(TakeKind::Playblast),
                    layer: None,
                    version: VersionRule::Pinned(Version::try_from("v0012".to_string()).unwrap()),
                },
            }
        );
        assert_eq!(WorkingEdit::parse(&edit.to_json()).unwrap(), edit);
    }

    #[test]
    fn unknown_and_missing_fields_are_errors() {
        for (from, to) in [
            (r#""fade_out": 24}"#, r#""fade_out": 24, "speed": 2}"#),
            (r#""gain_db": 0.0, "#, ""),
            (r#""layer": null, "version": "latest""#, r#""version": "latest""#),
            (r#""item": "music", "#, r#""item": "music", "path": "x", "#),
            (r#""markers": [{"at": 312, "note": "hold longer on the reveal"}]"#, r#""markers": [], "length": 3"#),
        ] {
            assert!(WORKING.contains(from), "{from}");
            let json = WORKING.replacen(from, to, 1);
            assert!(WorkingEdit::parse(&json).is_err(), "{to} parsed");
        }
    }

    use proptest::prelude::*;

    proptest! {
        /// A shot clip's rule, with a layer chosen (#58) or left open,
        /// writes and reads back as itself; an open layer is written as
        /// `null` (the edits written before the take menu had a Layer
        /// submenu, which read as best available), a chosen one as its name.
        #[test]
        fn a_rule_with_or_without_a_layer_round_trips(
            department in prop::option::of(prop::sample::select(vec!["animation", "render", "comp"])),
            kind in prop::option::of(prop_oneof![Just(TakeKind::Playblast), Just(TakeKind::Render)]),
            layer in prop::option::of(prop::sample::select(vec!["default", "chars", "slapcomp", "layers", "env2"])),
            pinned in prop::option::of(1u32..10000),
        ) {
            let version = pinned.map_or(VersionRule::Latest, |v| VersionRule::Pinned(Version::try_from(format!("v{v:04}")).unwrap()));
            let rule = Rule { department: department.map(Into::into), kind, layer: layer.map(Into::into), version };
            let mut edit = WorkingEdit::parse(WORKING).unwrap();
            edit.video[1].clips[0].source = Source::Shot { shot: "entity:/shots/A/010".into(), take: rule.clone() };
            let json = edit.to_json();
            let written = match layer {
                Some(l) => format!(r#""layer": "{l}""#),
                None => r#""layer": null"#.to_string(),
            };
            prop_assert!(json.contains(&written), "{}", json);
            let back = WorkingEdit::parse(&json).unwrap();
            prop_assert_eq!(&back, &edit);
            prop_assert_eq!(&back.video[1].clips[0].source, &Source::Shot { shot: "entity:/shots/A/010".into(), take: rule });
        }
    }

    proptest! {
        /// A video clip's opacity (#60) is written only when it is not 1,
        /// and reads back as itself: an edit at full opacity writes no
        /// `opacity` at all (the bytes an editor from before opacity wrote
        /// and reads), one written without it reads as 1, and one outside
        /// 0 to 1 is no edit.
        #[test]
        fn opacity_is_written_only_when_it_is_not_one(
            opacities in prop::collection::vec(prop::sample::select(vec![1.0f64, 1.0, 0.0, 0.5, 0.25, 0.999, 1e-3]), 3),
            bad in prop::sample::select(vec![-0.5f64, 1.5, 2.0, -1e-9, 1.000001]),
        ) {
            let mut edit = WorkingEdit::parse(WORKING).unwrap();
            let whole = edit.to_json();
            prop_assert!(!whole.contains("opacity"), "{}", whole);
            for (t, opacity) in opacities.iter().enumerate() {
                edit.video[t].clips[0].opacity = *opacity;
            }
            let json = edit.to_json();
            prop_assert_eq!(json.matches("\"opacity\"").count(), opacities.iter().filter(|&&o| o != 1.0).count());
            let back = WorkingEdit::parse(&json).unwrap();
            prop_assert_eq!(&back, &edit);
            if opacities.iter().all(|&o| o == 1.0) {
                prop_assert_eq!(&json, &whole);
            }
            // Written out by hand at 1, it reads as left out.
            let explicit = WORKING.replacen(r#""fade_in": 12, "fade_out": 0}"#, r#""fade_in": 12, "fade_out": 0, "opacity": 1.0}"#, 1);
            prop_assert_ne!(&explicit, WORKING);
            prop_assert_eq!(WorkingEdit::parse(&explicit).unwrap(), WorkingEdit::parse(WORKING).unwrap());
            edit.video[1].clips[0].opacity = bad;
            prop_assert!(edit.problem().is_some_and(|why| why.contains("opacity")));
            prop_assert!(WorkingEdit::parse(&edit.to_json()).is_err());
        }
    }

    /// A transform within its limits.
    pub(crate) fn transforms() -> impl Strategy<Value = Transform> {
        let whole = prop_oneof![Just(0.0f64), Just(0.25), Just(-0.5), -10.0f64..=10.0];
        let scale = prop_oneof![Just(1.0f64), Just(0.5), Just(2.0), 0.01f64..=100.0];
        let turn = prop_oneof![Just(0.0f64), Just(90.0), Just(-45.0), -3600.0f64..=3600.0];
        (whole.clone(), whole, scale, turn).prop_map(|(x, y, scale, rotation)| Transform { x, y, scale, rotation })
    }

    proptest! {
        /// A video clip's transform is written only when it is not the
        /// identity, and reads back as itself to the bit: an edit that
        /// transforms nothing writes no `transform` at all (the bytes an
        /// editor from before transforms wrote, and reads), one written
        /// without it reads as the identity, and so does the identity
        /// written out by hand. Over other sources (frozen, as a publish
        /// holds it) it is the same transform.
        #[test]
        fn a_transform_is_written_only_when_it_is_not_the_identity(
            given in prop::collection::vec(prop_oneof![1 => Just(Transform::IDENTITY), 2 => transforms()], 3),
        ) {
            let mut edit = WorkingEdit::parse(WORKING).unwrap();
            let whole = edit.to_json();
            prop_assert!(!whole.contains("transform"), "{}", whole);
            for (t, transform) in given.iter().enumerate() {
                edit.video[t].clips[0].transform = *transform;
            }
            let json = edit.to_json();
            prop_assert_eq!(json.matches("\"transform\"").count(), given.iter().filter(|t| !t.is_identity()).count());
            let back = WorkingEdit::parse(&json).unwrap();
            prop_assert_eq!(&back, &edit);
            let bits = |t: &Transform| [t.x, t.y, t.scale, t.rotation].map(f64::to_bits);
            for (t, transform) in given.iter().enumerate() {
                if !transform.is_identity() {
                    prop_assert_eq!(bits(&back.video[t].clips[0].transform), bits(transform));
                }
            }
            if given.iter().all(Transform::is_identity) {
                prop_assert_eq!(&json, &whole);
            }
            let explicit = WORKING.replacen(
                r#""fade_in": 12, "fade_out": 0}"#,
                r#""fade_in": 12, "fade_out": 0, "transform": {"x": 0.0, "y": 0, "scale": 1.0, "rotation": -0.0}}"#,
                1,
            );
            prop_assert_ne!(&explicit, WORKING);
            let read = WorkingEdit::parse(&explicit).unwrap();
            prop_assert_eq!(&read, &WorkingEdit::parse(WORKING).unwrap());
            prop_assert_eq!(read.to_json(), whole);
            let kept = edit.try_map(|s| Ok::<_, ()>(s.clone())).unwrap();
            prop_assert_eq!(&kept, &edit);
        }

        /// A transform out of its limits, or with a number that is none,
        /// is no edit; nor is one with a field missing or added.
        #[test]
        fn a_transform_out_of_range_is_no_edit(
            good in transforms(),
            which in 0usize..4,
            bad in prop::sample::select(vec![f64::NAN, f64::INFINITY, -f64::INFINITY, 1e9, -1e9]),
            tiny in prop::sample::select(vec![0.0f64, -1.0, 0.009, 100.5]),
        ) {
            let mut edit = WorkingEdit::parse(WORKING).unwrap();
            prop_assert_eq!(good.problem(), None);
            let mut broken = good;
            match which {
                0 => broken.x = bad,
                1 => broken.y = bad,
                2 => broken.scale = tiny,
                _ => broken.rotation = bad,
            }
            prop_assert!(broken.problem().is_some(), "{:?}", broken);
            edit.video[1].clips[0].transform = broken;
            prop_assert!(edit.problem().is_some());
            for transform in [r#"{"x": 0.1, "y": 0, "scale": 1}"#, r#"{"x": 0.1, "y": 0, "scale": 1, "rotation": 0, "skew": 1}"#, "null"] {
                let json = WORKING.replacen(
                    r#""fade_in": 12, "fade_out": 0}"#,
                    &format!(r#""fade_in": 12, "fade_out": 0, "transform": {transform}}}"#),
                    1,
                );
                prop_assert!(WorkingEdit::parse(&json).is_err(), "{} parsed", transform);
            }
        }
    }

    #[test]
    fn a_working_edit_is_not_a_frozen_one() {
        assert!(FrozenEdit::parse(WORKING).is_err());
    }

    #[test]
    fn versions_and_timecodes_are_checked() {
        for bad in ["latest!", "v12", "v00a1", "0012", ""] {
            assert!(serde_json::from_str::<VersionRule>(&format!("\"{bad}\"")).is_err(), "{bad}");
        }
        assert_eq!(serde_json::from_str::<VersionRule>("\"latest\"").unwrap(), VersionRule::Latest);
        assert!(serde_json::from_str::<VersionRule>("\"v10000\"").is_ok());
        assert_eq!(timecode_frames("01:00:00:00", 24).unwrap(), 86400);
        assert_eq!(timecode_frames("00:00:01:05", 25).unwrap(), 30);
        for bad in ["01:00:00:24", "1:00:00:00", "00:60:00:00", "00:00:00", "aa:00:00:00"] {
            assert!(timecode_frames(bad, 24).is_err(), "{bad}");
        }
    }

    #[test]
    fn overlaps_and_bad_fades_are_no_edit() {
        let overlap = WORKING.replacen(
            r#""fade_in": 0, "fade_out": 0}]},
        {"name": "V2""#,
            r#""fade_in": 0, "fade_out": 0},
          {"at": 2399, "duration": 5, "source_in": 1, "source": {"kind": "slug", "text": "x"}, "fade_in": 0, "fade_out": 0}]},
        {"name": "V2""#,
            1,
        );
        assert_ne!(overlap, WORKING);
        assert!(WorkingEdit::parse(&overlap).unwrap_err().contains("over the clip before"));
        let fades = WORKING.replacen(r#""fade_in": 12"#, r#""fade_in": 61"#, 1);
        assert!(WorkingEdit::parse(&fades).unwrap_err().contains("fades"));
        let names = WORKING.replacen(r#""name": "V3""#, r#""name": "V1""#, 1);
        assert!(WorkingEdit::parse(&names).unwrap_err().contains("twice"));
    }
}
