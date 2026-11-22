//! What File > Export Timeline… needs besides the edit itself: frame rates
//! and timecodes as an NLE writes them, a path as a `file:` URL, what the
//! pipeline is asked about a clip's media ([`Probe`]), and the summary of
//! what an export carried and what it dropped or approximated
//! ([`Summary`]). The formats are [`crate::xmeml`] (Final Cut Pro 7 XML,
//! for Resolve and Premiere Pro) and [`crate::otio`].
//!
//! Pure, as the rest of the crate: the editor answers [`Probe`] with its
//! decoder and writes the files.

use std::collections::BTreeMap;

use serde_json::Value;
use th_playlist::Media;

use crate::document::{DEFAULT_START_FRAME, Frame, FrozenEdit, FrozenSource};

/// A frame rate as Final Cut Pro 7 XML has it: a whole `timebase`, and
/// `ntsc` for that rate times 1000/1001 (23.976 is 24 with `ntsc`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rate {
    pub timebase: u32,
    pub ntsc: bool,
}

impl Rate {
    /// A whole rate: an edit's (`Edit::fps`).
    pub fn whole(fps: u32) -> Self {
        Rate { timebase: fps, ntsc: false }
    }

    /// The rate `num / den` frames a second: a whole one, or a whole one
    /// times 1000/1001. None for any other (xmeml cannot say it).
    pub fn of(num: i64, den: i64) -> Option<Self> {
        if num <= 0 || den <= 0 {
            return None;
        }
        if num % den == 0 {
            return u32::try_from(num / den).ok().map(Rate::whole);
        }
        let (top, bottom) = (num.checked_mul(1001)?, den.checked_mul(1000)?);
        if top % bottom == 0 {
            return u32::try_from(top / bottom).ok().map(|timebase| Rate { timebase, ntsc: true });
        }
        None
    }

    /// Frames a second, exactly, as `(num, den)`.
    pub fn ratio(self) -> (i64, i64) {
        if self.ntsc { (i64::from(self.timebase) * 1000, 1001) } else { (i64::from(self.timebase), 1) }
    }
}

/// Frame count `frames` as a non-drop timecode `HH:MM:SS:FF` at `fps`: what
/// `document::timecode_frames` reads back. Negative counts are 0.
pub fn timecode(frames: Frame, fps: u32) -> String {
    let fps = Frame::from(fps.max(1));
    let frames = frames.max(0);
    let (seconds, ff) = (frames / fps, frames % fps);
    format!("{:02}:{:02}:{:02}:{:02}", seconds / 3600, seconds / 60 % 60, seconds % 60, ff)
}

/// Where a path is: the server of a UNC path (`//server/share/x`), and the
/// path on it from its root (`/share/x`, `/P:/x`, `/mnt/x`), forward
/// slashes. A path that is not absolute is taken from the root.
fn location(path: &str) -> (Option<String>, String) {
    let slashed = path.replace('\\', "/");
    if let Some(rest) = slashed.strip_prefix("//")
        && !rest.starts_with('/')
        && !rest.is_empty()
    {
        return match rest.split_once('/') {
            Some((host, tail)) => (Some(host.to_owned()), format!("/{tail}")),
            None => (Some(rest.to_owned()), "/".to_owned()),
        };
    }
    if slashed.starts_with('/') { (None, slashed) } else { (None, format!("/{slashed}")) }
}

/// Whether `path` (from its root) opens with a drive letter: `/P:` then a
/// slash or nothing.
fn has_drive(path: &str) -> bool {
    let b = path.as_bytes();
    b.len() >= 3 && b[0] == b'/' && b[1].is_ascii_alphabetic() && b[2] == b':' && (b.len() == 3 || b[3] == b'/')
}

/// `text` with every byte but the unreserved ones (letters, digits, `-`,
/// `.`, `_`, `~`) and `/` as `%XX` of its UTF-8, a drive letter's colon
/// kept.
fn encode(text: &str, drive: bool) -> String {
    let mut out = String::with_capacity(text.len());
    for (i, b) in text.bytes().enumerate() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~' | b'/') || (drive && i == 2) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// `path` as a `file:` URL with `local` for the host of a path on this
/// machine (`""`: `file:///P:/x`; `"localhost"`: `file://localhost/P:/x`),
/// a UNC path's server for its host (`file://server/share/x`).
pub(crate) fn url(path: &str, local: &str) -> String {
    let (host, rest) = location(path);
    let host = host.map_or_else(|| local.to_owned(), |h| encode(&h, false));
    format!("file://{host}{}", encode(&rest, has_drive(&rest)))
}

/// `path` as Final Cut Pro 7 XML's `pathurl`: `file://localhost/P:/x` for
/// a drive path, `file://localhost/mnt/x` for a POSIX one,
/// `file://server/share/x` for a UNC one, every byte outside the
/// unreserved set percent-encoded as UTF-8 (a space is `%20`).
pub fn path_url(path: &str) -> String {
    url(path, "localhost")
}

/// The path a `file:` URL names, forward slashes: `P:/x`, `/mnt/x`,
/// `//server/share/x`. None when it is no `file:` URL or its escapes are
/// not UTF-8.
pub fn url_path(url: &str) -> Option<String> {
    let rest = url.strip_prefix("file://")?;
    let (host, path) = match rest.find('/') {
        Some(at) => (&rest[..at], &rest[at..]),
        None => (rest, "/"),
    };
    let (host, path) = (decode(host)?, decode(path)?);
    if !host.is_empty() && host != "localhost" {
        return Some(format!("//{host}{path}"));
    }
    Some(if has_drive(&path) { path[1..].to_owned() } else { path })
}

fn decode(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = text.get(i + 1..i + 3)?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// A movie or sound file, as far as an export asks.
#[derive(Debug, Clone, PartialEq)]
pub struct Movie {
    /// The frame number its first picture is, when it records one (a
    /// TumblePipe playblast's `start_frame` tag, a frame list's first).
    pub start_frame: Option<Frame>,
    /// Its pictures; None for sound alone.
    pub frames: Option<Frame>,
    /// Its pictures' rate, `(num, den)`.
    pub rate: Option<(i64, i64)>,
    /// Whether it has sound.
    pub audio: bool,
    /// How long it plays, in seconds.
    pub seconds: f64,
    /// Its pictures are not one run of frame numbers (a Frame List
    /// render's movie).
    pub gaps: bool,
}

/// The pipeline's and the disk's answers about a clip's media.
pub trait Probe {
    /// The movie or sound file at `path`; None when it is not there or
    /// cannot be read.
    fn movie(&self, path: &str) -> Option<Movie>;
    /// Whether the file at `path` is on disk.
    fn exists(&self, path: &str) -> bool;
    /// The first frame of `shot`'s range with its roll: the frame a take's
    /// movie starts on when it records none; an error when the pipeline
    /// cannot say.
    fn shot_first(&self, shot: &str) -> Result<Frame, String>;
}

/// Something an export did not carry as it is in the edit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Kind {
    /// A shot clip with no take its rule picks.
    NoTake,
    /// A slug: text on black.
    Slug,
    /// A stack of render layers.
    Stack,
    /// A check render: every Nth frame.
    Check,
    /// Media that is not on disk.
    Missing,
    /// A movie on disk that could not be read.
    Unreadable,
    /// A shot whose first frame the pipeline could not give.
    ShotFirst,
    /// A clip running past its media's first or last frame.
    Trimmed,
    /// A clip none of whose frames its media has.
    Outside,
    /// A Frame List render's movie: its frames are not one run.
    FrameList,
    /// A movie at another rate than the edit's.
    Rate,
    /// A level over what the format holds.
    Gain,
    /// An image sequence, written the way one NLE reads it.
    Sequence,
    /// A fade over the tracks below (OTIO).
    Fade,
    /// An opacity under 100% (OTIO).
    Opacity,
    /// A transform: a clip moved, scaled or turned.
    Transform,
    /// An audio clip's level or fades (OTIO).
    AudioLevel,
    /// A track with a colour: by the track.
    TrackColor,
    /// An audio clip of nothing that sounds.
    Silent,
}

/// What an export wrote, and what it dropped or approximated.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Summary {
    pub video_tracks: usize,
    pub audio_tracks: usize,
    /// Video clips written, the offline ones too.
    pub clips: usize,
    /// Audio clips written.
    pub audio_clips: usize,
    /// Of them, the sound of shot clips on video tracks.
    pub shot_sounds: usize,
    pub transitions: usize,
    pub markers: usize,
    /// Per kind, the clips it happened to, by name, in edit order.
    pub notes: BTreeMap<Kind, Vec<String>>,
}

impl Summary {
    pub fn note(&mut self, kind: Kind, name: &str) {
        self.notes.entry(kind).or_default().push(name.to_owned());
    }

    pub fn count(&self, kind: Kind) -> usize {
        self.notes.get(&kind).map_or(0, Vec::len)
    }

    /// What was carried, in a line.
    pub fn carried(&self) -> String {
        let plural = |n: usize, one: &str| if n == 1 { format!("1 {one}") } else { format!("{n} {one}s") };
        let mut line = format!(
            "{} and {}: {}, {}, {}, {}",
            plural(self.video_tracks, "video track"),
            plural(self.audio_tracks, "audio track"),
            plural(self.clips, "video clip"),
            plural(self.audio_clips, "audio clip"),
            plural(self.transitions, "fade"),
            plural(self.markers, "marker"),
        );
        if self.shot_sounds > 0 {
            line += &format!(" ({} the sound of shot clips, on tracks of their own)", self.shot_sounds);
        }
        line + "."
    }

    /// What was dropped or approximated, a line per kind, the clips named
    /// (the first few).
    pub fn dropped(&self) -> Vec<String> {
        self.notes
            .iter()
            .map(|(kind, names)| {
                let n = names.len();
                let s = if n == 1 { "" } else { "s" };
                let what = match kind {
                    Kind::NoTake => format!("{n} shot clip{s} with no take: written offline, to relink once there is one"),
                    Kind::Slug => format!("{n} slug{s}: a gap, with a marker carrying its text"),
                    Kind::Stack => format!("{n} render-layer stack{s}: only the bottom layer is referenced"),
                    Kind::Check => format!("{n} check render{s} (every Nth frame): referenced, but will not play at speed"),
                    Kind::Missing => format!("{n} clip{s} whose media is not on disk: referenced as written, offline until it is there"),
                    Kind::Unreadable => {
                        format!("{n} clip{s} whose movie could not be read: the in point is counted from the shot's first frame")
                    }
                    Kind::ShotFirst => format!(
                        "{n} shot clip{s} whose shot's frame range could not be read: the in point is counted from frame {DEFAULT_START_FRAME}"
                    ),
                    Kind::Trimmed => format!("{n} clip{s} running past the frames the media has: shortened to them"),
                    Kind::Outside => format!("{n} clip{s} showing none of the frames the media has: written offline"),
                    Kind::FrameList => format!("{n} Frame List render{s}: the frames are not one run, the in point may be off"),
                    Kind::Rate => format!("{n} clip{s} of a movie at another rate than the edit's: the NLE plays it at its own rate"),
                    Kind::Gain => format!("{n} audio level{s} over +12 dB: written as +12 dB"),
                    Kind::Sequence => {
                        format!("{n} image sequence clip{s}: Resolve and Premiere Pro read these differently; check they conform")
                    }
                    Kind::Fade => format!("{n} fade{s} over the tracks below: not in the timeline (a yellow marker says where)"),
                    Kind::Opacity => format!("{n} clip{s} with an opacity under 100%: not in the timeline (a yellow marker says where)"),
                    Kind::Transform => format!("{n} clip{s} moved, scaled or turned: not in the timeline, the clip is written as fitted"),
                    Kind::AudioLevel => format!("{n} audio clip{s} with a level or fades: only in the clip's metadata"),
                    Kind::Silent => format!("{n} audio clip{s} of something with no sound: not written"),
                    Kind::TrackColor => {
                        format!("{n} track colour{s}: an OTIO keeps it in the track's metadata, an XML has no place for it; an NLE shows neither")
                    }
                };
                format!("{what} ({}).", examples(names))
            })
            .collect()
    }

    /// The whole summary, a line each: what was carried, then what was not.
    pub fn lines(&self) -> Vec<String> {
        let mut lines = vec![self.carried()];
        let dropped = self.dropped();
        if dropped.is_empty() {
            lines.push("Nothing was dropped or approximated.".into());
        }
        lines.extend(dropped);
        lines
    }
}

/// The first few of `names`, each once, and how many more.
fn examples(names: &[String]) -> String {
    let mut unique: Vec<&str> = Vec::new();
    for name in names {
        if !unique.contains(&name.as_str()) {
            unique.push(name);
        }
    }
    let shown = unique.iter().take(5).copied().collect::<Vec<_>>().join(", ");
    if unique.len() > 5 { format!("{shown} and {} more", unique.len() - 5) } else { shown }
}

/// A clip's name in an NLE: its shot's name (`010`), its media item's, or
/// its slug's text.
pub fn clip_name(source: &FrozenSource) -> String {
    match source {
        FrozenSource::Shot { shot, .. } => shot.rsplit('/').next().unwrap_or(shot).to_owned(),
        FrozenSource::Media { item, .. } => item.rsplit(['/', ':']).next().unwrap_or(item).to_owned(),
        FrozenSource::Slug { text } => text.clone(),
    }
}

/// The media a clip of `source` plays; None for a slug and a shot with no
/// take.
pub fn media_of(source: &FrozenSource) -> Option<&Media> {
    match source {
        FrozenSource::Shot { take, .. } => take.as_ref().map(|t| &t.media),
        FrozenSource::Media { media, .. } => Some(media),
        FrozenSource::Slug { .. } => None,
    }
}

/// The one file or sequence an export refers to for `media`: itself, or a
/// stack's bottom layer (true then).
pub fn leaf(media: &Media) -> (Option<&Media>, bool) {
    match media {
        Media::Stack { layers } => (layers.first().map(|l| leaf(&l.media).0).unwrap_or(None), true),
        other => (Some(other), false),
    }
}

/// What `otio` (`otio::timeline(edit, ..)`) carried of `edit` and what it
/// did not: its counts, the fades, opacities and transforms it marked as
/// left out, and what Resolve will not make of the rest.
pub fn otio_summary(edit: &FrozenEdit, otio: &Value, probe: &impl Probe) -> Summary {
    let mut summary =
        Summary { video_tracks: edit.video.len(), audio_tracks: edit.audio.len(), markers: edit.markers.len(), ..Summary::default() };
    let tracks = otio["tracks"]["children"].as_array().cloned().unwrap_or_default();
    for track in &tracks {
        let video = track["kind"] == "Video";
        for item in track["children"].as_array().into_iter().flatten() {
            match item["OTIO_SCHEMA"].as_str() {
                Some("Clip.2") if video => summary.clips += 1,
                Some("Clip.2") => summary.audio_clips += 1,
                Some("Transition.1") => summary.transitions += 1,
                _ => {}
            }
        }
    }
    for marker in otio["tracks"]["markers"].as_array().into_iter().flatten().filter(|m| m["color"] == "YELLOW") {
        let name = marker["name"].as_str().unwrap_or_default();
        let at = marker["marked_range"]["start_time"]["value"].as_f64().unwrap_or(0.0) as Frame;
        let kind = if name.starts_with("opacity") {
            Kind::Opacity
        } else if name.starts_with("transform") {
            Kind::Transform
        } else {
            Kind::Fade
        };
        summary.note(kind, &format!("frame {at}"));
    }
    let source = |source: &FrozenSource, summary: &mut Summary| {
        let name = clip_name(source);
        match source {
            FrozenSource::Shot { take: None, .. } => summary.note(Kind::NoTake, &name),
            FrozenSource::Slug { .. } => summary.note(Kind::Slug, &name),
            _ => {}
        }
        let Some(media) = media_of(source) else { return };
        let (leaf, stacked) = leaf(media);
        if stacked {
            summary.note(Kind::Stack, &name);
        }
        match leaf {
            Some(Media::Movie { path }) if !probe.exists(path) => summary.note(Kind::Missing, &name),
            Some(Media::Exr { frames, .. } | Media::Live { frames }) => {
                if frames.step > 1 {
                    summary.note(Kind::Check, &name);
                }
                if !probe.exists(&frames.path(0).to_string_lossy()) {
                    summary.note(Kind::Missing, &name);
                }
            }
            _ => {}
        }
    };
    // A track's colour is in its metadata only: no NLE reads it from there.
    for track in tracks.iter().filter(|t| t["metadata"]["tumblepipe"]["color"].is_string()) {
        summary.note(Kind::TrackColor, track["name"].as_str().unwrap_or_default());
    }
    for clip in edit.video.iter().flat_map(|t| &t.clips) {
        source(&clip.source, &mut summary);
    }
    for clip in edit.audio.iter().flat_map(|t| &t.clips) {
        source(&clip.source, &mut summary);
        if clip.gain_db != 0.0 || clip.fade_in > 0 || clip.fade_out > 0 {
            summary.note(Kind::AudioLevel, &clip_name(&clip.source));
        }
    }
    summary
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::document::timecode_frames;
    use proptest::prelude::*;

    /// A path's folder and file names: plain, with spaces, the characters
    /// a URL reserves, and letters outside ASCII.
    pub(crate) fn segment() -> impl Strategy<Value = String> {
        prop_oneof![
            4 => "[a-zA-Z0-9_.-]{1,8}",
            2 => "[a-z]{1,4} [a-z#%&+?=;,@\\[\\]()']{1,4}",
            2 => "(Blå|søren|日本|ø|é v2|Ünïcode~1)[a-z0-9]{0,3}",
        ]
        .prop_filter("a name, not . or ..", |s| s != "." && s != "..")
    }

    /// An absolute path, forward slashes: on a drive, POSIX, or UNC.
    pub(crate) fn any_path() -> impl Strategy<Value = String> {
        let tail = || prop::collection::vec(segment(), 1..5).prop_map(|s| s.join("/"));
        prop_oneof![
            3 => ("[A-Za-z]", tail()).prop_map(|(d, t)| format!("{d}:/{t}")),
            2 => tail().prop_map(|t| format!("/{t}")),
            2 => ("[a-z][a-z0-9.-]{0,8}", tail()).prop_map(|(h, t)| format!("//{h}/{t}")),
        ]
    }

    #[test]
    fn urls_of_each_kind_of_path() {
        assert_eq!(path_url("P:/Show/a b.mov"), "file://localhost/P:/Show/a%20b.mov");
        assert_eq!(path_url(r"P:\Show\010\v0001.mp4"), "file://localhost/P:/Show/010/v0001.mp4");
        assert_eq!(path_url("/mnt/show/x.mov"), "file://localhost/mnt/show/x.mov");
        assert_eq!(path_url("//nas01/proj/Blå/x#1.mov"), "file://nas01/proj/Bl%C3%A5/x%231.mov");
        assert_eq!(path_url(r"\\nas01\proj\x.mov"), "file://nas01/proj/x.mov");
        assert_eq!(path_url("P:/a/100%.mov"), "file://localhost/P:/a/100%25.mov");
        assert_eq!(url_path("file://localhost/P:/Show/a%20b.mov").unwrap(), "P:/Show/a b.mov");
        assert_eq!(url_path("file:///P:/Show/a%20b.mov").unwrap(), "P:/Show/a b.mov");
        assert_eq!(url_path("file://nas01/proj/x.mov").unwrap(), "//nas01/proj/x.mov");
        assert_eq!(url_path("http://x/y"), None);
        assert_eq!(url_path("file://localhost/a%ZZ"), None);
    }

    #[test]
    fn rates_as_xmeml_has_them() {
        assert_eq!(Rate::of(24, 1), Some(Rate { timebase: 24, ntsc: false }));
        assert_eq!(Rate::of(24000, 1001), Some(Rate { timebase: 24, ntsc: true }));
        assert_eq!(Rate::of(30000, 1001), Some(Rate { timebase: 30, ntsc: true }));
        assert_eq!(Rate::of(60000, 1001), Some(Rate { timebase: 60, ntsc: true }));
        assert_eq!(Rate::of(50, 2), Some(Rate::whole(25)));
        assert_eq!(Rate::of(2997, 100), None, "29.97 as a decimal is not 30000/1001");
        assert_eq!(Rate::of(0, 1), None);
        assert_eq!(Rate::of(24, 0), None);
        assert_eq!(timecode(86400, 24), "01:00:00:00");
        assert_eq!(timecode(30, 25), "00:00:01:05");
        assert_eq!(timecode(-4, 24), "00:00:00:00");
    }

    proptest! {
        /// A rate reads back from its own ratio, whatever the ratio is
        /// multiplied by; an NTSC rate is under its timebase and a whole
        /// one is it; and a ratio that is neither is no rate.
        #[test]
        fn a_rate_is_its_ratio(timebase in 1u32..240, ntsc in any::<bool>(), k in 1i64..50, off in 1i64..999) {
            let rate = Rate { timebase, ntsc };
            let (num, den) = rate.ratio();
            prop_assert_eq!(Rate::of(num * k, den * k), Some(rate));
            let fps = num as f64 / den as f64;
            if ntsc {
                prop_assert!(fps < f64::from(timebase) && fps > f64::from(timebase) * 0.998);
            } else {
                prop_assert_eq!(fps, f64::from(timebase));
            }
            // Between a whole rate and the next: only t*1000/1001 is one.
            let between = (i64::from(timebase) * 1000 + off, 1000);
            if let Some(found) = Rate::of(between.0, between.1) {
                let (n, d) = found.ratio();
                prop_assert_eq!(n * between.1, between.0 * d, "{:?} is not {:?}", found, between);
            }
        }

        /// A frame count written as a timecode reads back as itself, at any
        /// rate; the timecode is `HH:MM:SS:FF`, and a frame later is a
        /// timecode later.
        #[test]
        fn a_timecode_reads_back(fps in 1u32..=99, frames in 0i64..8_000_000) {
            let frames = frames.min(99 * 3600 * i64::from(fps));
            let written = timecode(frames, fps);
            prop_assert_eq!(written.len(), 11, "{}", &written);
            prop_assert_eq!(timecode_frames(&written, fps), Ok(frames));
            prop_assert!(timecode(frames + 1, fps) > written);
        }

        /// A path's URL is plain ASCII with nothing a URL reserves but its
        /// slashes, percent signs and a drive's colon, names `localhost`
        /// or the UNC server, and reads back as the path: drive letters,
        /// UNC, POSIX, spaces, letters outside ASCII. Backslashes write the
        /// same URL.
        #[test]
        fn a_path_reads_back_from_its_url(path in any_path()) {
            let url = path_url(&path);
            prop_assert!(url.starts_with("file://"), "{}", url);
            let rest = &url["file://".len()..];
            prop_assert!(rest.bytes().all(|b| b.is_ascii_alphanumeric() || b"-._~/%:".contains(&b)), "{}", url);
            prop_assert!(rest.matches(':').count() <= 1, "{}", url);
            let host = rest.split('/').next().unwrap();
            let server = path.strip_prefix("//").map(|unc| unc.split('/').next().unwrap());
            prop_assert_eq!(host, server.unwrap_or("localhost"));
            prop_assert_eq!(url_path(&url), Some(path.clone()));
            prop_assert_eq!(path_url(&path.replace('/', "\\")), url);
            // The OTIO form names no host for a local path, and reads back too.
            let otio = crate::otio::file_url(&path);
            prop_assert_eq!(otio.starts_with("file:///"), !path.starts_with("//"), "{}", &otio);
            prop_assert_eq!(url_path(&otio), Some(path));
        }

        /// An ordinary path (letters, digits, `_`, `-`, `.`, `~`, spaces,
        /// `#` and `%`) has the OTIO URL Publish always wrote for it: the
        /// path with only `%`, a space and `#` escaped.
        #[test]
        fn an_ordinary_path_keeps_its_otio_url(drive in "[A-Z]", tail in prop::collection::vec("[a-zA-Z0-9_.~#%-]{1,6}( [a-z0-9~]{1,3})?", 1..5), posix in any::<bool>()) {
            let tail = tail.join("/");
            let path = if posix { format!("/{tail}") } else { format!("{drive}:/{tail}") };
            let escaped = path.replace('%', "%25").replace(' ', "%20").replace('#', "%23");
            let before = if escaped.starts_with('/') { format!("file://{escaped}") } else { format!("file:///{escaped}") };
            prop_assert_eq!(crate::otio::file_url(&path), before);
        }
    }

    #[test]
    fn a_summary_says_what_was_carried_and_what_was_not() {
        let mut summary =
            Summary { video_tracks: 2, audio_tracks: 1, clips: 3, audio_clips: 1, transitions: 1, markers: 0, ..Summary::default() };
        assert_eq!(
            summary.lines(),
            [
                "2 video tracks and 1 audio track: 3 video clips, 1 audio clip, 1 fade, 0 markers.".to_owned(),
                "Nothing was dropped or approximated.".to_owned(),
            ]
        );
        for n in 0..7 {
            summary.note(Kind::NoTake, &format!("0{n}0"));
        }
        summary.note(Kind::NoTake, "000");
        summary.note(Kind::Slug, "end");
        let lines = summary.lines();
        assert_eq!(lines.len(), 3);
        assert_eq!(
            lines[1],
            "8 shot clips with no take: written offline, to relink once there is one (000, 010, 020, 030, 040 and 2 more)."
        );
        assert_eq!(lines[2], "1 slug: a gap, with a marker carrying its text (end).");
    }
}
