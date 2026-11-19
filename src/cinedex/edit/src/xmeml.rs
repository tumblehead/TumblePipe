//! A frozen edit as Final Cut Pro 7 XML (`xmeml` version 5), the timeline
//! interchange both DaVinci Resolve and Adobe Premiere Pro import (File >
//! Export Timeline…).
//!
//! - Tracks map one to one, bottom first; a gap is the space between two
//!   clip items.
//! - A clip is a `clipitem` named after its shot (or media item), its `in`
//!   counted from its media's first frame: a clip's `source_in` is a frame
//!   number (a shot's are 1001-based), a movie's first picture the frame
//!   its `start_frame` tag says ([`Probe`]).
//! - Every fade is a single-sided Cross Dissolve on the clip's own track
//!   (`start-black` in, `end-black` out), which over a track below is the
//!   dissolve the edit shows and over nothing a fade from or to black.
//!   An audio fade is a single-sided Cross Fade.
//! - Opacity is an Opacity filter, an audio clip's level an Audio Levels
//!   filter.
//! - The sound of a shot's movie on a video track (Cinedex plays it) is an
//!   audio clip linked to the video one, on a track of its own after the
//!   edit's audio tracks.
//! - Markers are sequence markers.
//! - What has no equal is written as near as it goes and noted in the
//!   [`Summary`]: a shot with no take is an offline clip (no `pathurl`), a
//!   slug a gap with a marker carrying its text, a stack of render layers
//!   its bottom layer, and a clip running past its media is shortened to
//!   it. Nothing is left out without a note.
//!
//! A clip's transform (moved, scaled or turned) is not written: the clip
//! item is as fitted, and the [`Summary`] names it. [`motion_filter`] is
//! the hook for it, and writes nothing yet.

use std::collections::BTreeMap;
use std::fmt::Display;

use th_playlist::{Frames, Media};

use crate::document::{AudioClip, Clip, Frame, FrozenEdit, FrozenSource, timecode_frames};
use crate::timeline_export::{Kind, Probe, Rate, Summary, clip_name, leaf, media_of, path_url};

/// Which NLE an image sequence is written for: they do not read one the
/// same way (unverified in either; see [`sequence_reference`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Sequences {
    /// One file name with the frame range in brackets, as Resolve names a
    /// sequence in its media pool: `beauty.[1001-1100].exr`.
    #[default]
    Resolve,
    /// The sequence's first frame, as Premiere Pro refers to one.
    Premiere,
}

/// How an export is written.
#[derive(Debug, Clone, Default)]
pub struct Options {
    pub sequences: Sequences,
    /// Movies to refer to elsewhere: a movie's path, as the edit has it,
    /// to the path written (media collected beside the file).
    pub links: BTreeMap<String, String>,
}

/// The file name and path an image sequence is referred to by.
///
/// UNVERIFIED in both NLEs. For Resolve: the name it gives a sequence in
/// its media pool and writes into the timelines it exports, the range in
/// brackets at the files' own padding. For Premiere Pro: its first frame
/// (Premiere may import that as a still: see the docs).
pub fn sequence_reference(frames: &Frames, dialect: Sequences) -> (String, String) {
    let padding = frames.padding;
    let name = match dialect {
        Sequences::Resolve => format!("{}[{:0padding$}-{:0padding$}]{}", frames.prefix, frames.first, frames.last, frames.suffix),
        Sequences::Premiere => format!("{}{:0padding$}{}", frames.prefix, frames.first, frames.suffix),
    };
    let directory = frames.directory.replace('\\', "/");
    let path = format!("{}/{name}", directory.trim_end_matches('/'));
    (name, path)
}

/// `text` as XML character data: the five characters XML reserves as
/// entities, and what XML 1.0 cannot hold (control characters but tab and
/// the line ends, U+FFFE and U+FFFF) as a space.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&apos;"),
            '\t' | '\n' | '\r' => out.push(c),
            c if (c as u32) < 0x20 || c == '\u{FFFE}' || c == '\u{FFFF}' => out.push(' '),
            c => out.push(c),
        }
    }
    out
}

/// An XML document being written, indented.
struct Xml {
    out: String,
    depth: usize,
}

impl Xml {
    fn line(&mut self, text: &str) {
        for _ in 0..self.depth {
            self.out.push_str("  ");
        }
        self.out.push_str(text);
        self.out.push('\n');
    }

    fn open(&mut self, tag: &str) {
        self.line(&format!("<{tag}>"));
        self.depth += 1;
    }

    fn open_id(&mut self, tag: &str, id: &str) {
        self.line(&format!("<{tag} id=\"{}\">", escape(id)));
        self.depth += 1;
    }

    fn close(&mut self, tag: &str) {
        self.depth -= 1;
        self.line(&format!("</{tag}>"));
    }

    fn leaf(&mut self, tag: &str, value: impl Display) {
        self.line(&format!("<{tag}>{}</{tag}>", escape(&value.to_string())));
    }

    fn rate(&mut self, rate: Rate) {
        self.open("rate");
        self.leaf("timebase", rate.timebase);
        self.leaf("ntsc", if rate.ntsc { "TRUE" } else { "FALSE" });
        self.close("rate");
    }
}

/// A number as xmeml has it: no exponent, no trailing zeros.
fn number(value: f64) -> String {
    let text = format!("{value:.5}");
    let text = text.trim_end_matches('0').trim_end_matches('.');
    if text.is_empty() || text == "-0" { "0".into() } else { text.into() }
}

/// A media file the sequence refers to, written whole the first time a
/// clip uses it and by its id after.
#[derive(Debug)]
struct File {
    name: String,
    /// None: offline.
    url: Option<String>,
    rate: Rate,
    /// Its frames, in its own rate.
    duration: Frame,
    video: bool,
    audio: bool,
    written: bool,
}

/// What a clip plays, as the sequence refers to it.
struct Target {
    key: String,
    name: String,
    url: Option<String>,
    /// The frame number its first frame is.
    first: Frame,
    /// Frame numbers between two of its frames (over 1: a check render).
    step: Frame,
    /// How many frames it has, when that is known and they are one run.
    frames: Option<Frame>,
    rate: Rate,
    video: bool,
    audio: bool,
}

/// A clip item, placed.
#[derive(Debug, Clone)]
struct Item {
    id: usize,
    name: String,
    start: Frame,
    end: Frame,
    /// Its first frame's index in its media.
    r#in: Frame,
    fade_in: Frame,
    fade_out: Frame,
    file: usize,
    opacity: f64,
    gain_db: f64,
    /// The clip item it is linked to (its sound, or its picture): `(item
    /// id, track index, index on the track)`, its own after.
    link: Option<[(usize, usize, usize); 2]>,
}

struct Writer<'a, P: Probe> {
    edit: &'a FrozenEdit,
    probe: &'a P,
    options: &'a Options,
    rate: Rate,
    files: Vec<File>,
    keys: BTreeMap<String, usize>,
    summary: Summary,
    next_id: usize,
    /// Slugs, as markers: `(at, end, text)`.
    slugs: Vec<(Frame, Frame, String)>,
    /// Probed movies, by path.
    probed: BTreeMap<String, Option<crate::timeline_export::Movie>>,
}

impl<P: Probe> Writer<'_, P> {
    fn movie(&mut self, path: &str) -> Option<crate::timeline_export::Movie> {
        if let Some(found) = self.probed.get(path) {
            return found.clone();
        }
        let found = self.probe.movie(path);
        self.probed.insert(path.to_owned(), found.clone());
        found
    }

    fn file(&mut self, target: &Target) -> usize {
        if let Some(&index) = self.keys.get(&target.key) {
            let file = &mut self.files[index];
            file.video |= target.video;
            file.audio |= target.audio;
            return index;
        }
        self.files.push(File {
            name: target.name.clone(),
            url: target.url.clone(),
            rate: target.rate,
            duration: target.frames.unwrap_or(0),
            video: target.video,
            audio: target.audio,
            written: false,
        });
        self.keys.insert(target.key.clone(), self.files.len() - 1);
        self.files.len() - 1
    }

    /// An offline file for a clip named `name` with nothing to refer to.
    fn offline(&self, name: &str, audio: bool) -> Target {
        Target {
            key: format!("offline:{}:{name}", if audio { "audio" } else { "video" }),
            name: name.to_owned(),
            url: None,
            first: 0,
            step: 1,
            frames: None,
            rate: self.rate,
            video: !audio,
            audio,
        }
    }

    /// What a clip of `source` refers to, noting what it cannot say; None
    /// when nothing is written for it (`audio`: on an audio track).
    fn target(&mut self, source: &FrozenSource, audio: bool) -> Option<Target> {
        let name = clip_name(source);
        let shot = match source {
            FrozenSource::Shot { shot, .. } => Some(shot.as_str()),
            _ => None,
        };
        let Some(media) = media_of(source) else {
            return match source {
                FrozenSource::Slug { .. } if audio => {
                    self.summary.note(Kind::Silent, &name);
                    None
                }
                FrozenSource::Slug { .. } => {
                    self.summary.note(Kind::Slug, &name);
                    None
                }
                _ => {
                    self.summary.note(Kind::NoTake, &name);
                    Some(self.offline(&name, audio))
                }
            };
        };
        let (leaf, stacked) = leaf(media);
        if stacked && !audio {
            self.summary.note(Kind::Stack, &name);
        }
        match leaf {
            None => {
                self.summary.note(Kind::Outside, &name);
                Some(self.offline(&name, audio))
            }
            Some(Media::Stack { .. }) => unreachable!("a leaf is no stack"),
            Some(Media::Exr { frames, .. } | Media::Live { frames }) => {
                if audio {
                    self.summary.note(Kind::Silent, &name);
                    return None;
                }
                let (file_name, path) = sequence_reference(frames, self.options.sequences);
                self.summary.note(Kind::Sequence, &name);
                if frames.step > 1 {
                    self.summary.note(Kind::Check, &name);
                }
                if !self.probe.exists(&frames.path(0).to_string_lossy()) {
                    self.summary.note(Kind::Missing, &name);
                }
                Some(Target {
                    key: path.clone(),
                    name: file_name,
                    url: Some(path_url(&path)),
                    first: frames.first,
                    step: frames.step.max(1),
                    frames: Some(frames.count() as Frame),
                    rate: self.rate,
                    video: true,
                    audio: false,
                })
            }
            Some(Media::Movie { path }) => {
                let written = self.options.links.get(path).unwrap_or(path).replace('\\', "/");
                let file_name = written.rsplit('/').next().unwrap_or(&written).to_owned();
                let fallback = shot.map_or(0, |s| self.probe.shot_first(s));
                let fps = f64::from(self.rate.timebase);
                let mut target = Target {
                    key: path.clone(),
                    name: file_name,
                    url: Some(path_url(&written)),
                    first: fallback,
                    step: 1,
                    frames: None,
                    rate: self.rate,
                    video: !audio,
                    audio,
                };
                let Some(movie) = self.movie(path) else {
                    let kind = if self.probe.exists(path) { Kind::Unreadable } else { Kind::Missing };
                    self.summary.note(kind, &name);
                    return Some(target);
                };
                // A media item's frames count from 0, whatever it records.
                if shot.is_some() {
                    target.first = movie.start_frame.unwrap_or(fallback);
                }
                if audio {
                    if !movie.audio {
                        self.summary.note(Kind::Silent, &name);
                        return None;
                    }
                    target.video = movie.frames.is_some();
                    target.frames = Some(match movie.frames {
                        Some(n) => n,
                        None => (movie.seconds * fps).floor() as Frame,
                    });
                } else {
                    let Some(frames) = movie.frames else {
                        self.summary.note(Kind::Outside, &name);
                        return Some(self.offline(&name, audio));
                    };
                    target.audio = movie.audio;
                    target.frames = Some(frames);
                    if movie.gaps {
                        self.summary.note(Kind::FrameList, &name);
                        target.frames = None;
                    }
                }
                if let Some((num, den)) = movie.rate {
                    let own = Rate::of(num, den);
                    if own != Some(self.rate) {
                        self.summary.note(Kind::Rate, &name);
                    }
                    if movie.frames.is_some() {
                        target.rate = own.unwrap_or(self.rate);
                    }
                }
                Some(target)
            }
        }
    }

    /// A clip of `target` over `at .. at + duration` showing frame numbers
    /// from `source_in`, shortened to the frames the media has.
    #[allow(clippy::too_many_arguments)]
    fn item(
        &mut self,
        target: Target,
        audio: bool,
        name: &str,
        at: Frame,
        duration: Frame,
        source_in: Frame,
        fades: (Frame, Frame),
    ) -> Item {
        let offline = target.url.is_none();
        let head = if offline { 0 } else { (target.first - source_in).max(0) };
        let known = target.frames.filter(|_| target.step == 1 && !offline);
        let tail = known.map_or(0, |n| (source_in + duration - (target.first + n)).max(0));
        let (target, head, tail) = if head + tail >= duration {
            // None of its frames are there.
            self.summary.note(Kind::Outside, name);
            (self.offline(name, audio), 0, 0)
        } else {
            if head + tail > 0 {
                self.summary.note(Kind::Trimmed, name);
            }
            (target, head, tail)
        };
        let offline = target.url.is_none();
        let file = self.file(&target);
        let (start, end) = (at + head, at + duration - tail);
        let r#in = if offline { 0 } else { (source_in + head - target.first) / target.step };
        let needs = r#in + (end - start);
        self.files[file].duration = self.files[file].duration.max(needs);
        self.next_id += 1;
        // A fade keeps where it ended (or began) in the edit, as far as
        // the shortened clip still has those frames.
        let fade_in = (fades.0 - head).clamp(0, end - start);
        let fade_out = (fades.1 - tail).clamp(0, end - start - fade_in);
        Item { id: self.next_id, name: name.to_owned(), start, end, r#in, fade_in, fade_out, file, opacity: 1.0, gain_db: 0.0, link: None }
    }

    /// The video tracks, and the sound of their shot clips: a track for
    /// each video track that has any, named after it.
    fn video(&mut self) -> (Vec<Vec<Item>>, Vec<Vec<Item>>) {
        let mut tracks = Vec::new();
        let mut sounds = Vec::new();
        for track in &self.edit.video {
            let mut items = Vec::new();
            let mut sound = Vec::new();
            for clip in &track.clips {
                let Some(target) = self.target(&clip.source, false) else {
                    let text = clip_name(&clip.source);
                    self.slugs.push((clip.at, clip.end(), text));
                    continue;
                };
                let has_sound = target.audio;
                let fades = (clip.fade_in, clip.fade_out);
                let mut item = self.item(target, false, &clip_name(&clip.source), clip.at, clip.duration, clip.source_in, fades);
                item.opacity = clip.opacity;
                if !clip.transform.is_identity() {
                    self.summary.note(Kind::Transform, &item.name);
                }
                // Cinedex plays the sound of a shot's movie (not a stack's,
                // nor a media item's on a video track).
                let sounds_here = matches!(&clip.source, FrozenSource::Shot { take: Some(take), .. } if matches!(take.media, Media::Movie { .. }))
                    && has_sound
                    && self.files[item.file].url.is_some();
                if sounds_here {
                    self.next_id += 1;
                    sound.push(Item { id: self.next_id, opacity: 1.0, link: None, ..item.clone() });
                    let last = sound.len() - 1;
                    // Linked both ways once the tracks' indices are known.
                    item.link = Some([(item.id, 0, items.len()), (sound[last].id, 0, last)]);
                }
                items.push(item);
            }
            tracks.push(items);
            sounds.push(sound);
        }
        (tracks, sounds)
    }

    fn audio(&mut self) -> Vec<Vec<Item>> {
        let mut tracks = Vec::new();
        for track in &self.edit.audio {
            let mut items = Vec::new();
            for clip in &track.clips {
                let AudioClip { at, duration, source_in, gain_db, fade_in, fade_out, .. } = *clip;
                let Some(target) = self.target(&clip.source, true) else { continue };
                let name = clip_name(&clip.source);
                let mut item = self.item(target, true, &name, at, duration, source_in, (fade_in, fade_out));
                item.gain_db = gain_db;
                if gain_db > MAX_GAIN_DB {
                    self.summary.note(Kind::Gain, &name);
                }
                items.push(item);
            }
            tracks.push(items);
        }
        tracks
    }

    fn write_file(&mut self, xml: &mut Xml, index: usize) {
        let id = format!("file-{}", index + 1);
        let [width, height] = self.edit.resolution;
        let file = &mut self.files[index];
        if file.written {
            xml.line(&format!("<file id=\"{id}\"/>"));
            return;
        }
        file.written = true;
        xml.open_id("file", &id);
        xml.leaf("name", &file.name);
        if let Some(url) = &file.url {
            xml.leaf("pathurl", url);
        }
        xml.rate(file.rate);
        xml.leaf("duration", file.duration);
        xml.open("media");
        if file.video {
            xml.open("video");
            // An offline clip stands in at the sequence's size; a file on
            // disk says its own.
            if file.url.is_none() {
                xml.open("samplecharacteristics");
                xml.leaf("width", width);
                xml.leaf("height", height);
                xml.close("samplecharacteristics");
            }
            xml.leaf("duration", file.duration);
            xml.close("video");
        }
        if file.audio {
            xml.open("audio");
            xml.leaf("channelcount", 2);
            xml.close("audio");
        }
        xml.close("media");
        xml.close("file");
    }

    /// A track's clip items in order, each fade a transition item beside
    /// its clip: a clip with one has -1 for that edge, which the
    /// transition gives.
    fn write_track(&mut self, xml: &mut Xml, items: &[Item], audio: bool) {
        xml.open("track");
        for item in items {
            if item.fade_in > 0 {
                write_transition(xml, item.start, item.start + item.fade_in, "start-black", self.rate, audio);
            }
            xml.open_id("clipitem", &format!("clipitem-{}", item.id));
            xml.leaf("masterclipid", format!("masterclip-{}", item.file + 1));
            xml.leaf("name", &item.name);
            xml.leaf("enabled", "TRUE");
            xml.leaf("duration", self.files[item.file].duration);
            xml.rate(self.rate);
            xml.leaf("start", if item.fade_in > 0 { -1 } else { item.start });
            xml.leaf("end", if item.fade_out > 0 { -1 } else { item.end });
            xml.leaf("in", item.r#in);
            xml.leaf("out", item.r#in + (item.end - item.start));
            self.write_file(xml, item.file);
            xml.open("sourcetrack");
            xml.leaf("mediatype", if audio { "audio" } else { "video" });
            xml.leaf("trackindex", 1);
            xml.close("sourcetrack");
            if audio && item.gain_db != 0.0 {
                write_level(xml, item.gain_db);
            }
            if !audio && item.opacity < 1.0 {
                write_opacity(xml, item.opacity);
            }
            if !audio {
                motion_filter(xml, item);
            }
            for (kind, (id, track, index)) in item.link.iter().flatten().enumerate() {
                xml.open("link");
                xml.leaf("linkclipref", format!("clipitem-{id}"));
                xml.leaf("mediatype", if kind == 0 { "video" } else { "audio" });
                xml.leaf("trackindex", track + 1);
                xml.leaf("clipindex", index + 1);
                xml.close("link");
            }
            xml.close("clipitem");
            if item.fade_out > 0 {
                write_transition(xml, item.end - item.fade_out, item.end, "end-black", self.rate, audio);
            }
        }
        xml.close("track");
    }
}

/// The highest level an Audio Levels filter holds.
const MAX_GAIN_DB: f64 = 12.0;

/// A level in decibels as an Audio Levels filter's linear gain, to its
/// most (+12 dB).
pub fn level(gain_db: f64) -> f64 {
    10f64.powf(gain_db.min(MAX_GAIN_DB) / 20.0)
}

fn write_level(xml: &mut Xml, gain_db: f64) {
    xml.open("filter");
    xml.open("effect");
    xml.leaf("name", "Audio Levels");
    xml.leaf("effectid", "audiolevels");
    xml.leaf("effectcategory", "audiolevels");
    xml.leaf("effecttype", "audiolevels");
    xml.leaf("mediatype", "audio");
    xml.open("parameter");
    xml.leaf("parameterid", "level");
    xml.leaf("name", "Level");
    xml.leaf("valuemin", 0);
    xml.leaf("valuemax", "3.98109");
    xml.leaf("value", number(level(gain_db)));
    xml.close("parameter");
    xml.close("effect");
    xml.close("filter");
}

fn write_opacity(xml: &mut Xml, opacity: f64) {
    xml.open("filter");
    xml.open("effect");
    xml.leaf("name", "Opacity");
    xml.leaf("effectid", "opacity");
    xml.leaf("effectcategory", "motion");
    xml.leaf("effecttype", "motion");
    xml.leaf("mediatype", "video");
    xml.open("parameter");
    xml.leaf("parameterid", "opacity");
    xml.leaf("name", "opacity");
    xml.leaf("valuemin", 0);
    xml.leaf("valuemax", 100);
    xml.leaf("value", number(opacity * 100.0));
    xml.close("parameter");
    xml.close("effect");
    xml.close("filter");
}

/// The hook for a clip's transform (position, scale, rotation): write a
/// `<filter>` holding the `Basic Motion` effect here
/// (`effectid` `basic`, `effectcategory` and `effecttype` `motion`, with
/// `scale`, `rotation` and `center` parameters; `center` is `<horiz>` and
/// `<vert>` in fractions of the frame from its middle). It writes nothing
/// today: both NLEs place the clip as fitted, and the summary says which
/// clips had a transform ([`Kind::Transform`]).
fn motion_filter(_xml: &mut Xml, _item: &Item) {}

fn write_transition(xml: &mut Xml, start: Frame, end: Frame, alignment: &str, rate: Rate, audio: bool) {
    xml.open("transitionitem");
    xml.leaf("start", start);
    xml.leaf("end", end);
    xml.leaf("alignment", alignment);
    xml.rate(rate);
    xml.open("effect");
    if audio {
        xml.leaf("name", "Cross Fade (+3dB)");
        xml.leaf("effectid", "KGAudioTransCrossFade3dB");
        xml.leaf("effecttype", "transition");
        xml.leaf("mediatype", "audio");
    } else {
        xml.leaf("name", "Cross Dissolve");
        xml.leaf("effectid", "Cross Dissolve");
        xml.leaf("effectcategory", "Dissolve");
        xml.leaf("effecttype", "transition");
        xml.leaf("mediatype", "video");
    }
    xml.leaf("wipecode", 0);
    xml.leaf("wipeaccuracy", 100);
    xml.leaf("startratio", 0);
    xml.leaf("endratio", 1);
    xml.leaf("reverse", "FALSE");
    xml.close("effect");
    xml.close("transitionitem");
}

fn write_marker(xml: &mut Xml, name: &str, comment: &str, at: Frame, out: Frame) {
    xml.open("marker");
    xml.leaf("comment", comment);
    xml.leaf("name", name);
    xml.leaf("in", at);
    xml.leaf("out", out);
    xml.close("marker");
}

/// `edit` as a Final Cut Pro 7 XML sequence named `name`, and what it
/// carried and did not. `probe` answers where each movie's frames start.
pub fn sequence(edit: &FrozenEdit, name: &str, probe: &impl Probe, options: &Options) -> (String, Summary) {
    let rate = Rate::whole(edit.fps);
    let mut writer = Writer {
        edit,
        probe,
        options,
        rate,
        files: Vec::new(),
        keys: BTreeMap::new(),
        summary: Summary::default(),
        next_id: 0,
        slugs: Vec::new(),
        probed: BTreeMap::new(),
    };
    let (mut video, sounds) = writer.video();
    let mut audio = writer.audio();
    // The sound tracks come after the edit's audio tracks; link each pair.
    for (v, sound) in sounds.into_iter().enumerate() {
        if sound.is_empty() {
            continue;
        }
        let a = audio.len();
        let mut sound = sound;
        for item in &mut video[v] {
            if let Some([picture, heard]) = &mut item.link {
                picture.1 = v;
                heard.1 = a;
                sound[heard.2].link = Some([*picture, *heard]);
            }
        }
        writer.summary.shot_sounds += sound.len();
        audio.push(sound);
    }
    let fades =
        |tracks: &[Vec<Item>]| tracks.iter().flatten().map(|i| usize::from(i.fade_in > 0) + usize::from(i.fade_out > 0)).sum::<usize>();
    writer.summary.video_tracks = video.len();
    writer.summary.audio_tracks = audio.len();
    writer.summary.clips = video.iter().map(Vec::len).sum();
    writer.summary.audio_clips = audio.iter().map(Vec::len).sum();
    writer.summary.transitions = fades(&video) + fades(&audio[..edit.audio.len()]);
    writer.summary.markers = edit.markers.len();

    let start = timecode_frames(&edit.start_timecode, edit.fps).expect("a checked edit's timecode reads");
    let [width, height] = edit.resolution;
    let mut xml = Xml { out: String::new(), depth: 0 };
    xml.line("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
    xml.line("<!DOCTYPE xmeml>");
    xml.line("<xmeml version=\"5\">");
    xml.depth += 1;
    xml.open_id("sequence", "sequence-1");
    xml.leaf("name", name);
    xml.leaf("duration", edit.length());
    xml.rate(rate);
    xml.open("timecode");
    xml.rate(rate);
    xml.leaf("string", &edit.start_timecode);
    xml.leaf("frame", start);
    xml.leaf("displayformat", "NDF");
    xml.close("timecode");
    xml.open("media");
    xml.open("video");
    xml.open("format");
    xml.open("samplecharacteristics");
    xml.rate(rate);
    xml.leaf("width", width);
    xml.leaf("height", height);
    xml.leaf("anamorphic", "FALSE");
    xml.leaf("pixelaspectratio", "square");
    xml.leaf("fielddominance", "none");
    xml.close("samplecharacteristics");
    xml.close("format");
    for items in &video {
        writer.write_track(&mut xml, items, false);
    }
    xml.close("video");
    xml.open("audio");
    xml.leaf("numOutputChannels", 2);
    xml.open("format");
    xml.open("samplecharacteristics");
    xml.leaf("depth", 16);
    xml.leaf("samplerate", 48000);
    xml.close("samplecharacteristics");
    xml.close("format");
    for items in &audio {
        writer.write_track(&mut xml, items, true);
    }
    xml.close("audio");
    xml.close("media");
    for marker in &edit.markers {
        write_marker(&mut xml, &marker.note, "", marker.at, -1);
    }
    let mut slugs = std::mem::take(&mut writer.slugs);
    slugs.sort();
    for (at, end, text) in &slugs {
        write_marker(&mut xml, if text.is_empty() { "Slug" } else { text }, "Slug", *at, *end);
    }
    xml.close("sequence");
    xml.depth -= 1;
    xml.line("</xmeml>");
    (xml.out, writer.summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::{FrozenTake, Marker, TakeKind, Track, Version, VideoClip};
    use crate::timeline_export::tests::{any_path, segment};
    use crate::timeline_export::{Movie, url_path};
    use proptest::prelude::*;
    use th_playlist::StackLayer;

    /// An element of a parsed document.
    #[derive(Debug, Clone, Default, PartialEq)]
    struct Node {
        tag: String,
        attributes: Vec<(String, String)>,
        children: Vec<Node>,
        text: String,
    }

    impl Node {
        fn all(&self, tag: &str) -> std::vec::IntoIter<&Node> {
            self.children.iter().filter(|c| c.tag == tag).collect::<Vec<_>>().into_iter()
        }

        fn child(&self, tag: &str) -> &Node {
            self.children.iter().find(|c| c.tag == tag).unwrap_or_else(|| panic!("<{}> has no <{tag}>", self.tag))
        }

        fn has(&self, tag: &str) -> bool {
            self.children.iter().any(|c| c.tag == tag)
        }

        fn text_of(&self, tag: &str) -> &str {
            &self.child(tag).text
        }

        fn int(&self, tag: &str) -> Frame {
            self.text_of(tag).parse().unwrap_or_else(|_| panic!("<{tag}> is {:?}", self.text_of(tag)))
        }

        fn attribute(&self, name: &str) -> Option<&str> {
            self.attributes.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
        }
    }

    fn unescape(text: &str) -> Result<String, String> {
        let mut out = String::new();
        let mut rest = text;
        while let Some(at) = rest.find('&') {
            out.push_str(&rest[..at]);
            let end = rest[at..].find(';').ok_or("an & that opens no entity")? + at;
            out.push(match &rest[at + 1..end] {
                "amp" => '&',
                "lt" => '<',
                "gt" => '>',
                "quot" => '"',
                "apos" => '\'',
                other => return Err(format!("entity &{other};")),
            });
            rest = &rest[end + 1..];
        }
        out.push_str(rest);
        Ok(out)
    }

    /// A strict little XML reader: the prolog, a DOCTYPE, elements with
    /// double-quoted attributes, character data with the five entities. A
    /// tag closed by another, a bare `<` or `&`, an unknown entity or a
    /// character XML 1.0 cannot hold is an error: what is written must be
    /// well formed.
    fn parse(xml: &str) -> Result<Node, String> {
        if let Some(bad) = xml.chars().find(|&c| ((c as u32) < 0x20 && !"\t\n\r".contains(c)) || c == '\u{FFFE}' || c == '\u{FFFF}') {
            return Err(format!("character {:#x} is not XML", bad as u32));
        }
        let mut rest = xml.strip_prefix("<?xml version=\"1.0\" encoding=\"UTF-8\"?>").ok_or("no prolog")?.trim_start();
        rest = rest.strip_prefix("<!DOCTYPE xmeml>").ok_or("no doctype")?;
        let mut stack: Vec<Node> = vec![Node::default()];
        while !rest.is_empty() {
            let Some(open) = rest.find('<') else {
                if !rest.trim().is_empty() {
                    return Err("text after the document".into());
                }
                break;
            };
            let text = &rest[..open];
            stack.last_mut().unwrap().text.push_str(&unescape(text)?);
            let close = rest[open..].find('>').ok_or("an unclosed tag")? + open;
            let tag = &rest[open + 1..close];
            rest = &rest[close + 1..];
            if let Some(name) = tag.strip_prefix('/') {
                let node = stack.pop().ok_or("too many closes")?;
                if node.tag != name {
                    return Err(format!("<{}> closed by </{name}>", node.tag));
                }
                let parent = stack.last_mut().ok_or("too many closes")?;
                let mut node = node;
                if !node.children.is_empty() {
                    if !node.text.trim().is_empty() {
                        return Err(format!("text beside elements in <{name}>"));
                    }
                    node.text.clear();
                }
                parent.children.push(node);
                continue;
            }
            let (tag, empty) = match tag.strip_suffix('/') {
                Some(t) => (t, true),
                None => (tag, false),
            };
            let (name, mut attrs) = tag.split_once(' ').unwrap_or((tag, ""));
            if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric()) {
                return Err(format!("a tag named {name:?}"));
            }
            let mut node = Node { tag: name.to_owned(), ..Node::default() };
            while !attrs.trim().is_empty() {
                let (key, after) = attrs.trim_start().split_once("=\"").ok_or("an attribute without a value")?;
                let (value, after) = after.split_once('"').ok_or("an unclosed attribute")?;
                node.attributes.push((key.to_owned(), unescape(value)?));
                attrs = after;
            }
            if empty {
                stack.last_mut().unwrap().children.push(node);
            } else {
                stack.push(node);
            }
        }
        if stack.len() != 1 {
            return Err(format!("<{}> is never closed", stack.last().unwrap().tag));
        }
        let mut root = stack.pop().unwrap();
        if root.children.len() != 1 || !root.text.trim().is_empty() {
            return Err("not one root element".into());
        }
        Ok(root.children.remove(0))
    }

    /// A clip item read back: its name, span, in and out, and the path of
    /// the file it refers to (None: offline), with its fades from the
    /// transition items beside it.
    #[derive(Debug, Clone, PartialEq)]
    struct Read {
        id: String,
        name: String,
        start: Frame,
        end: Frame,
        r#in: Frame,
        out: Frame,
        path: Option<String>,
        fade_in: Frame,
        fade_out: Frame,
        opacity: Option<f64>,
        level: Option<f64>,
        links: Vec<(String, String, Frame, Frame)>,
        file_duration: Frame,
    }

    /// The sequence's tracks read back, video then audio, and its markers.
    struct Sequence {
        root: Node,
        video: Vec<Vec<Read>>,
        audio: Vec<Vec<Read>>,
        markers: Vec<(Frame, Frame, String, String)>,
    }

    fn read(xml: &str) -> Sequence {
        let root = parse(xml).unwrap_or_else(|e| panic!("{e}:\n{xml}"));
        assert_eq!((root.tag.as_str(), root.attribute("version")), ("xmeml", Some("5")));
        let sequence = root.child("sequence").clone();
        let mut files: BTreeMap<String, (Option<String>, Frame)> = BTreeMap::new();
        let mut tracks = |kind: &str| -> Vec<Vec<Read>> {
            sequence
                .child("media")
                .child(kind)
                .all("track")
                .map(|track| {
                    let mut out: Vec<Read> = Vec::new();
                    // A transition's span waiting for the clip after it.
                    let mut pending: Option<(Frame, Frame)> = None;
                    for item in &track.children {
                        if item.tag == "transitionitem" {
                            let (start, end) = (item.int("start"), item.int("end"));
                            assert!(start < end, "a transition of no frames");
                            assert_eq!(item.child("effect").text_of("mediatype"), kind);
                            match item.text_of("alignment") {
                                "start-black" => {
                                    assert!(pending.is_none(), "two transitions into one clip");
                                    pending = Some((start, end));
                                }
                                "end-black" => {
                                    let last = out.last_mut().expect("a fade out after a clip");
                                    assert_eq!(last.end, -1, "the clip before a fade out leaves its end to it");
                                    last.end = end;
                                    last.fade_out = end - start;
                                }
                                other => panic!("alignment {other}"),
                            }
                            continue;
                        }
                        assert_eq!(item.tag, "clipitem");
                        let file = item.child("file");
                        let id = file.attribute("id").expect("a file has an id").to_owned();
                        if file.has("name") {
                            let path = file.has("pathurl").then(|| url_path(file.text_of("pathurl")).expect("a file URL"));
                            assert!(files.insert(id.clone(), (path, file.int("duration"))).is_none(), "{id} written twice");
                        }
                        let (path, file_duration) = files.get(&id).unwrap_or_else(|| panic!("{id} used before it is written")).clone();
                        let parameter = |effect: &str| {
                            item.all("filter").map(|f| f.child("effect")).find(|e| e.text_of("name") == effect).map(|e| {
                                let p = e.child("parameter");
                                let value: f64 = p.text_of("value").parse().unwrap();
                                let (min, max): (f64, f64) =
                                    (p.text_of("valuemin").parse().unwrap(), p.text_of("valuemax").parse().unwrap());
                                assert!(min <= value && value <= max, "{effect} {value} outside {min}..{max}");
                                value
                            })
                        };
                        let mut clip = Read {
                            id: item.attribute("id").expect("a clip item has an id").to_owned(),
                            name: item.text_of("name").to_owned(),
                            start: item.int("start"),
                            end: item.int("end"),
                            r#in: item.int("in"),
                            out: item.int("out"),
                            path,
                            fade_in: 0,
                            fade_out: 0,
                            opacity: parameter("Opacity"),
                            level: parameter("Audio Levels"),
                            links: item
                                .all("link")
                                .map(|l| {
                                    (
                                        l.text_of("linkclipref").to_owned(),
                                        l.text_of("mediatype").to_owned(),
                                        l.int("trackindex"),
                                        l.int("clipindex"),
                                    )
                                })
                                .collect(),
                            file_duration,
                        };
                        assert_eq!(item.child("sourcetrack").text_of("mediatype"), kind);
                        match pending.take() {
                            Some((start, end)) => {
                                assert_eq!(clip.start, -1, "the clip after a fade in leaves its start to it");
                                clip.start = start;
                                clip.fade_in = end - start;
                            }
                            None => assert!(clip.start >= 0, "a start of -1 with no transition before it"),
                        }
                        out.push(clip);
                    }
                    assert!(pending.is_none(), "a fade in with no clip after it");
                    for clip in &out {
                        assert!(clip.end >= 0, "an end of -1 with no transition after it");
                    }
                    out
                })
                .collect()
        };
        let (video, audio) = (tracks("video"), tracks("audio"));
        let markers = sequence
            .all("marker")
            .map(|m| (m.int("in"), m.int("out"), m.text_of("name").to_owned(), m.text_of("comment").to_owned()))
            .collect();
        Sequence { root: sequence, video, audio, markers }
    }

    /// The disk and pipeline of the tests: movies by path, every other
    /// file there unless its path says `gone`, every shot from 1001.
    #[derive(Debug, Clone, Default)]
    struct Disk {
        movies: BTreeMap<String, Movie>,
    }

    impl Probe for Disk {
        fn movie(&self, path: &str) -> Option<Movie> {
            self.movies.get(path).cloned()
        }
        fn exists(&self, path: &str) -> bool {
            !path.contains("gone")
        }
        fn shot_first(&self, _: &str) -> Frame {
            1001
        }
    }

    fn movie(start_frame: Option<Frame>, frames: Frame, audio: bool) -> Movie {
        Movie { start_frame, frames: Some(frames), rate: Some((24, 1)), audio, seconds: frames as f64 / 24.0, gaps: false }
    }

    fn version(v: &str) -> Version {
        Version::try_from(v.to_string()).unwrap()
    }

    fn shot(name: &str, media: Media) -> FrozenSource {
        FrozenSource::Shot {
            shot: format!("entity:/shots/S/{name}"),
            take: Some(FrozenTake { department: "anim".into(), kind: TakeKind::Playblast, layer: None, version: version("v0003"), media }),
        }
    }

    fn vclip(at: Frame, duration: Frame, source_in: Frame, source: FrozenSource) -> VideoClip<FrozenSource> {
        VideoClip::new(at, duration, source_in, source)
    }

    fn exr(directory: &str, first: Frame, last: Frame, step: Frame) -> Media {
        Media::Exr {
            layer: "default".into(),
            frames: Frames { directory: directory.into(), prefix: "beauty.".into(), suffix: ".exr".into(), padding: 4, first, last, step },
        }
    }

    #[test]
    fn a_small_cut_as_xmeml() {
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let path = "P:/Show/export/S/010/anim/v0003.mp4";
        let mut first = vclip(0, 48, 1009, shot("010", Media::Movie { path: path.into() }));
        first.fade_in = 12;
        first.opacity = 0.5;
        let mut second = vclip(60, 24, 1001, shot("020", exr("P:/Show/render/S/020", 1001, 1100, 1)));
        second.fade_out = 6;
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![first, second, vclip(90, 10, 0, FrozenSource::Slug { text: "End <card>".into() })],
        });
        edit.audio.push(Track {
            name: "A1".into(),
            clips: vec![AudioClip {
                at: 0,
                duration: 96,
                source_in: 24,
                source: FrozenSource::Media {
                    item: "music".into(),
                    version: version("v0001"),
                    media: Media::Movie { path: "P:/Show/media/music.wav".into() },
                },
                gain_db: -6.0,
                fade_in: 0,
                fade_out: 24,
            }],
        });
        edit.markers.push(Marker { at: 30, note: "hold & reveal".into() });
        let mut disk = Disk::default();
        disk.movies.insert(path.into(), movie(Some(1001), 120, true));
        disk.movies.insert(
            "P:/Show/media/music.wav".into(),
            Movie { start_frame: None, frames: None, rate: None, audio: true, seconds: 10.0, gaps: false },
        );
        let (xml, summary) = sequence(&edit, "Main v0003", &disk, &Options::default());
        let read = read(&xml);
        assert_eq!(read.root.text_of("name"), "Main v0003");
        assert_eq!(read.root.int("duration"), 100);
        assert_eq!(read.root.child("timecode").text_of("string"), "01:00:00:00");
        assert_eq!(read.root.child("timecode").int("frame"), 86400);
        assert_eq!(read.root.child("rate").text_of("ntsc"), "FALSE");
        let v1 = &read.video[0];
        assert_eq!(v1.len(), 2, "the slug is a gap");
        assert_eq!((v1[0].name.as_str(), v1[0].start, v1[0].end, v1[0].r#in, v1[0].out), ("010", 0, 48, 8, 56));
        assert_eq!((v1[0].fade_in, v1[0].fade_out, v1[0].opacity, v1[0].file_duration), (12, 0, Some(50.0), 120));
        assert_eq!(v1[0].path.as_deref(), Some(path));
        assert_eq!((v1[1].name.as_str(), v1[1].start, v1[1].end, v1[1].r#in, v1[1].out), ("020", 60, 84, 0, 24));
        assert_eq!((v1[1].fade_out, v1[1].file_duration), (6, 100));
        assert_eq!(v1[1].path.as_deref(), Some("P:/Show/render/S/020/beauty.[1001-1100].exr"));
        // The music, then the playblast's own sound on a track of its own.
        assert_eq!(read.audio.len(), 2);
        let music = &read.audio[0][0];
        assert_eq!((music.start, music.end, music.r#in, music.out, music.fade_out, music.file_duration), (0, 96, 24, 120, 24, 240));
        assert!((music.level.unwrap() - 0.5012).abs() < 1e-4, "{:?}", music.level);
        let sound = &read.audio[1][0];
        assert_eq!((sound.start, sound.end, sound.r#in, sound.out, sound.fade_in), (0, 48, 8, 56, 12));
        assert_eq!(sound.path.as_deref(), Some(path));
        let links = vec![(v1[0].id.clone(), "video".to_owned(), 1, 1), (sound.id.clone(), "audio".to_owned(), 2, 1)];
        assert_eq!((&v1[0].links, &sound.links), (&links, &links));
        assert_eq!(
            read.markers,
            [(30, -1, "hold & reveal".to_owned(), String::new()), (90, 100, "End <card>".to_owned(), "Slug".to_owned())]
        );
        assert_eq!(
            summary.lines(),
            [
                "1 video track and 2 audio tracks: 2 video clips, 2 audio clips, 3 fades, 1 marker (1 the sound of shot clips, on tracks of their own).",
                "1 slug: a gap, with a marker carrying its text (End <card>).",
                "1 image sequence clip: Resolve and Premiere Pro read these differently; check they conform (020).",
            ]
        );
    }

    #[test]
    fn what_has_no_equal_is_written_near_and_noted() {
        let mut edit = FrozenEdit::new(25, [2048, 858], "00:00:00:00");
        let stack = Media::Stack {
            layers: vec![
                StackLayer { name: "bg".into(), media: exr("P:/r/bg", 1001, 1041, 10) },
                StackLayer { name: "fg".into(), media: exr("P:/r/fg", 1001, 1041, 10) },
            ],
        };
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![
                vclip(0, 10, 1001, FrozenSource::Shot { shot: "entity:/shots/S/030".into(), take: None }),
                vclip(10, 20, 1011, shot("040", stack)),
                // Starts five frames before its movie does, and ends three after.
                vclip(30, 30, 996, shot("050", Media::Movie { path: "P:/r/050.mp4".into() })),
                vclip(60, 10, 1001, shot("060", Media::Movie { path: "P:/gone/060.mp4".into() })),
                vclip(70, 10, 2000, shot("070", Media::Movie { path: "P:/r/050.mp4".into() })),
            ],
        });
        edit.audio.push(Track {
            name: "A1".into(),
            clips: vec![AudioClip {
                at: 0,
                duration: 10,
                source_in: 0,
                source: FrozenSource::Slug { text: "quiet".into() },
                gain_db: 20.0,
                fade_in: 0,
                fade_out: 0,
            }],
        });
        let mut disk = Disk::default();
        disk.movies.insert("P:/r/050.mp4".into(), Movie { rate: Some((24000, 1001)), ..movie(None, 22, false) });
        let options = Options { sequences: Sequences::Premiere, links: BTreeMap::new() };
        let (xml, summary) = sequence(&edit, "cut", &disk, &options);
        let read = read(&xml);
        let v1 = &read.video[0];
        assert_eq!(v1.len(), 5);
        assert_eq!((v1[0].path.as_deref(), v1[0].r#in, v1[0].out), (None, 0, 10), "no take: offline");
        assert_eq!(v1[1].path.as_deref(), Some("P:/r/bg/beauty.1001.exr"), "the bottom layer, by its first frame for Premiere");
        assert_eq!((v1[1].r#in, v1[1].out), (1, 21));
        assert_eq!((v1[2].start, v1[2].end, v1[2].r#in, v1[2].out), (35, 57, 0, 22), "shortened to the movie's frames");
        assert_eq!((v1[3].path.as_deref(), v1[3].r#in), (Some("P:/gone/060.mp4"), 0), "not on disk: referred to all the same");
        assert_eq!((v1[4].path.as_deref(), v1[4].start, v1[4].end), (None, 70, 80), "none of its frames: offline");
        assert!(read.audio[0].is_empty());
        for (kind, names) in [
            (Kind::NoTake, vec!["030"]),
            (Kind::Stack, vec!["040"]),
            (Kind::Check, vec!["040"]),
            (Kind::Sequence, vec!["040"]),
            (Kind::Trimmed, vec!["050"]),
            (Kind::Rate, vec!["050", "070"]),
            (Kind::Missing, vec!["060"]),
            (Kind::Outside, vec!["070"]),
            (Kind::Silent, vec!["quiet"]),
        ] {
            assert_eq!(summary.notes.get(&kind).cloned().unwrap_or_default(), names, "{kind:?}");
        }
        assert_eq!(summary.notes.len(), 9, "{:?}", summary.notes);
        // The 23.976 movie says its own rate.
        let files: Vec<&Node> = read.root.child("media").child("video").child("track").all("clipitem").map(|c| c.child("file")).collect();
        assert_eq!((files[2].child("rate").text_of("timebase"), files[2].child("rate").text_of("ntsc")), ("24", "TRUE"));
    }

    #[test]
    fn collected_movies_are_referred_to_where_they_were_linked() {
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let a = "//nas/proj/S/010/v0001.mp4";
        let b = "//nas/proj/S/020/v0001.mp4";
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![
                vclip(0, 10, 1001, shot("010", Media::Movie { path: a.into() })),
                vclip(10, 10, 1001, shot("020", Media::Movie { path: b.into() })),
                vclip(20, 10, 1011, shot("010", Media::Movie { path: a.into() })),
            ],
        });
        let mut disk = Disk::default();
        disk.movies.insert(a.into(), movie(Some(1001), 50, false));
        disk.movies.insert(b.into(), movie(Some(1001), 50, false));
        let links: BTreeMap<String, String> =
            crate::otio::movie_links(&edit).into_iter().map(|(p, n)| (p, format!("D:/out/cut_media/{n}"))).collect();
        let (xml, _) = sequence(&edit, "cut", &disk, &Options { sequences: Sequences::Resolve, links });
        let v1 = &read(&xml).video[0];
        let paths: Vec<&str> = v1.iter().map(|c| c.path.as_deref().unwrap()).collect();
        assert_eq!(paths[0], "D:/out/cut_media/S_010_anim_playblast_v0003.mp4");
        assert_eq!(paths[1], "D:/out/cut_media/S_020_anim_playblast_v0003.mp4");
        assert_eq!(paths[2], paths[0]);
        assert_eq!(xml.matches("<pathurl>").count(), 2, "a file is written once");
        let (plain, _) = sequence(&edit, "cut", &disk, &Options::default());
        assert!(plain.contains("<pathurl>file://nas/proj/S/010/v0001.mp4</pathurl>"), "{plain}");
    }

    /// Text with what XML reserves, quotes, letters outside ASCII and
    /// control characters.
    fn awkward() -> impl Strategy<Value = String> {
        prop_oneof![
            3 => "[a-zA-Z0-9 _.-]{0,10}",
            3 => "[a-z<>&\"' ]{0,10}",
            1 => "(]]>|<!--|&amp;|&#38;|</name>|<?x?>)[a-z]{0,3}",
            1 => "[a-zæøåÆ日本\u{1}\u{8}\u{b}\u{1f}\u{7f}\u{fffe}\t\n]{0,8}",
        ]
    }

    proptest! {
        /// Whatever a name holds, it is written as XML that reads back as
        /// that name (what XML 1.0 cannot hold reading back as a space).
        #[test]
        fn names_are_escaped(name in awkward(), note in awkward(), slug in awkward()) {
            let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
            let item = FrozenSource::Media { item: name.clone(), version: version("v0001"), media: Media::Movie { path: format!("P:/m/{name}.mov") } };
            edit.video.push(Track { name: name.clone(), clips: vec![vclip(0, 10, 0, item), vclip(10, 5, 0, FrozenSource::Slug { text: slug.clone() })] });
            edit.markers.push(Marker { at: 3, note: note.clone() });
            let (xml, _) = sequence(&edit, &name, &Disk::default(), &Options::default());
            let clean = |text: &str| -> String {
                text.chars().map(|c| if ((c as u32) < 0x20 && !"\t\n\r".contains(c)) || c == '\u{FFFE}' { ' ' } else { c }).collect()
            };
            let read = read(&xml);
            prop_assert_eq!(read.root.text_of("name"), clean(&name));
            let clip = &read.video[0][0];
            prop_assert_eq!(&clip.name, &clean(name.rsplit(['/', ':']).next().unwrap()));
            prop_assert_eq!(read.markers[0].2.clone(), clean(&note));
            let want = if slug.is_empty() { "Slug".to_owned() } else { clean(&slug) };
            prop_assert_eq!(read.markers[1].2.clone(), want);
        }
    }

    /// What a generated clip plays: `(source, the frame number its media
    /// starts on, how many frames it has; None: unknown or offline)`.
    fn any_source() -> impl Strategy<Value = FrozenSource> {
        let name = prop::sample::select(vec!["010", "020", "030", "A&B", "040"]);
        let movie_path = (prop::sample::select(vec!["P:/r/S", "//nas/proj/Blå film", "/mnt/show/a b"]), 0usize..4)
            .prop_map(|(d, n)| format!("{d}/{n}/v0001.mp4"));
        let sequence = (0i64..3, prop::sample::select(vec![1i64, 1, 1, 5]))
            .prop_map(|(n, step)| exr(&format!("P:/r/beauty{n}"), 1001, 1001 + 60 * step, step));
        let leaf = prop_oneof![3 => movie_path.clone().prop_map(|path| Media::Movie { path }), 1 => sequence];
        let media = prop_oneof![
            5 => leaf.clone(),
            1 => prop::collection::vec(leaf, 1..3)
                .prop_map(|l| Media::Stack { layers: l.into_iter().enumerate().map(|(i, media)| StackLayer { name: format!("l{i}"), media }).collect() }),
        ];
        prop_oneof![
            6 => (name.clone(), media).prop_map(|(n, media)| shot(n, media)),
            2 => (prop::sample::select(vec!["music", "ref:/Look", "a<b"]), movie_path)
                .prop_map(|(item, path)| FrozenSource::Media { item: item.into(), version: version("v0002"), media: Media::Movie { path } }),
            1 => name.prop_map(|n| FrozenSource::Shot { shot: format!("entity:/shots/S/{n}"), take: None }),
            1 => "[a-z<& ]{0,6}".prop_map(|text| FrozenSource::Slug { text }),
        ]
    }

    /// Clips laid out on a track: `(gap before, duration, source_in,
    /// fades, source)`.
    type Laid = (Frame, Frame, Frame, (Frame, Frame), FrozenSource);

    fn laid() -> impl Strategy<Value = Vec<Laid>> {
        prop::collection::vec((0i64..12, 1i64..40, 990i64..1050, (0i64..10, 0i64..10), any_source()), 0..6)
    }

    /// Any cut: up to three video tracks and two audio tracks of clips of
    /// every kind of source, with fades, opacities, levels and markers.
    fn cuts() -> impl Strategy<Value = FrozenEdit> {
        let opacity = prop::sample::select(vec![1.0f64, 1.0, 0.5, 0.25, 0.0]);
        let gain = prop::sample::select(vec![0.0f64, 0.0, -6.0, 3.0, 20.0]);
        (
            prop::sample::select(vec![24u32, 25, 30, 12]),
            prop::collection::vec((laid(), prop::collection::vec(opacity, 6)), 1..4),
            prop::collection::vec((laid(), prop::collection::vec(gain, 6)), 0..3),
            prop::collection::vec((0i64..200, "[a-z&< ]{0,8}"), 0..4),
        )
            .prop_map(|(fps, video, audio, markers)| {
                let mut edit = FrozenEdit::new(fps, [1920, 1080], "01:00:00:00");
                let place = |clips: Vec<Laid>| {
                    let mut at = 0;
                    clips
                        .into_iter()
                        .map(|(gap, duration, source_in, (fade_in, fade_out), source)| {
                            at += gap;
                            let start = at;
                            at += duration;
                            // A media item's frames count from 0.
                            let source_in = if matches!(source, FrozenSource::Media { .. }) { source_in - 995 } else { source_in };
                            let (fade_in, fade_out) = if fade_in + fade_out <= duration { (fade_in, fade_out) } else { (0, 0) };
                            (start, duration, source_in, fade_in, fade_out, source)
                        })
                        .collect::<Vec<_>>()
                };
                for (i, (clips, opacities)) in video.into_iter().enumerate() {
                    let clips = place(clips)
                        .into_iter()
                        .zip(opacities)
                        .map(|((at, duration, source_in, fade_in, fade_out, source), opacity)| VideoClip {
                            at,
                            duration,
                            source_in,
                            source,
                            fade_in,
                            fade_out,
                            opacity,
                            transform: Default::default(),
                        })
                        .collect();
                    edit.video.push(Track { name: format!("V{}", i + 1), clips });
                }
                for (i, (clips, gains)) in audio.into_iter().enumerate() {
                    let clips = place(clips)
                        .into_iter()
                        .zip(gains)
                        .map(|((at, duration, source_in, fade_in, fade_out, source), gain_db)| AudioClip {
                            at,
                            duration,
                            source_in,
                            source,
                            gain_db,
                            fade_in,
                            fade_out,
                        })
                        .collect();
                    edit.audio.push(Track { name: format!("A{}", i + 1), clips });
                }
                edit.markers = markers.into_iter().map(|(at, note)| Marker { at, note }).collect();
                assert_eq!(edit.problem(), None);
                edit
            })
    }

    /// Every movie `edit` plays (a stack by its bottom layer).
    fn movie_paths(edit: &FrozenEdit) -> Vec<String> {
        let sources = edit
            .video
            .iter()
            .flat_map(|t| t.clips.iter().map(|c| &c.source))
            .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)));
        sources
            .filter_map(|s| match media_of(s).map(leaf) {
                Some((Some(Media::Movie { path }), _)) => Some(path.clone()),
                _ => None,
            })
            .collect()
    }

    /// A disk for `edit`: each of its movies there, or not, as `kinds`
    /// say (long with sound, long without, short, no start frame, at
    /// another rate, gone).
    fn disk_for(edit: &FrozenEdit, kinds: &[usize]) -> Disk {
        let mut disk = Disk::default();
        for (i, path) in movie_paths(edit).into_iter().enumerate() {
            let found = match kinds[i % kinds.len()] {
                0 => movie(Some(1001), 200, true),
                1 => movie(Some(990), 200, false),
                2 => movie(Some(1010), 20, true),
                3 => movie(None, 200, true),
                4 => Movie { rate: Some((30000, 1001)), ..movie(Some(1001), 200, true) },
                _ => continue,
            };
            disk.movies.entry(path).or_insert(found);
        }
        disk
    }

    /// The frame number a clip's media starts on and how many frames it
    /// has, as the writer is to take them; None where the clip is
    /// offline or its length unknown.
    fn media_span(source: &FrozenSource, disk: &Disk) -> Option<(Frame, Option<Frame>, Frame)> {
        let is_shot = matches!(source, FrozenSource::Shot { .. });
        match leaf(media_of(source)?).0? {
            Media::Movie { path } => match disk.movies.get(path) {
                Some(m) => Some((if is_shot { m.start_frame.unwrap_or(1001) } else { 0 }, m.frames, 1)),
                None => Some((if is_shot { 1001 } else { 0 }, None, 1)),
            },
            Media::Exr { frames, .. } | Media::Live { frames } => {
                Some((frames.first, (frames.step == 1).then(|| frames.count() as Frame), frames.step))
            }
            Media::Stack { .. } => unreachable!(),
        }
    }

    proptest! {
        /// Any cut, written and read back: the XML is well formed; every
        /// video track is there in order; each track's clip items are its
        /// clips in order but the slugs (each a marker over its span, with
        /// its text); a clip item is named after its shot or item, lies
        /// within its clip's span, never over the item before it, refers
        /// to its clip's file (through its URL, read back as the path) and
        /// shows on every one of its frames the very frame of its media
        /// the edit shows there; it is its clip's whole span unless the
        /// media does not have those frames, and then it is noted; fades,
        /// opacities, levels and markers read back; and what the summary
        /// counts is what was written.
        #[test]
        fn a_cut_reads_back_from_its_xmeml(edit in cuts(), kinds in prop::collection::vec(0usize..6, 1..5), premiere in any::<bool>()) {
            let disk = disk_for(&edit, &kinds);
            let options = Options { sequences: if premiere { Sequences::Premiere } else { Sequences::Resolve }, links: BTreeMap::new() };
            let (xml, summary) = sequence(&edit, "cut", &disk, &options);
            let read = read(&xml);
            // The edit counts its timecode without drop frames
            // (`timecode_frames`), and every timecode written says so.
            let formats: Vec<&str> = xml.split("<displayformat>").skip(1).map(|rest| &rest[..rest.find('<').unwrap()]).collect();
            prop_assert!(!formats.is_empty() && formats.iter().all(|f| *f == "NDF"), "{:?}", formats);
            prop_assert_eq!(read.root.int("duration"), edit.length());
            prop_assert_eq!(read.root.child("rate").int("timebase"), Frame::from(edit.fps));
            prop_assert_eq!(read.video.len(), edit.video.len());
            prop_assert!(read.audio.len() >= edit.audio.len());
            let mut slugs = Vec::new();
            let mut trimmed = 0;
            for (track, items) in edit.video.iter().zip(&read.video) {
                let mut items = items.iter();
                let mut cursor = 0;
                for clip in &track.clips {
                    if let FrozenSource::Slug { text } = &clip.source {
                        slugs.push((clip.at, clip.end(), if text.is_empty() { "Slug".to_owned() } else { text.clone() }, "Slug".to_owned()));
                        continue;
                    }
                    let item = items.next().expect("a clip item for every clip but a slug");
                    prop_assert_eq!(&item.name, &clip_name(&clip.source));
                    prop_assert!(clip.at <= item.start && item.start < item.end && item.end <= clip.end(), "{:?} outside {}..{}", item, clip.at, clip.end());
                    prop_assert!(cursor <= item.start, "{:?} over the item before it", item);
                    cursor = item.end;
                    prop_assert_eq!(item.out - item.r#in, item.end - item.start);
                    prop_assert!(0 <= item.r#in && item.out <= item.file_duration, "{:?} outside its file", item);
                    prop_assert!(item.fade_in + item.fade_out <= item.end - item.start);
                    prop_assert!(item.fade_in <= clip.fade_in && item.fade_out <= clip.fade_out);
                    // A fade ends (a fade out starts) on the edit frame it
                    // did, as far as a shortened item still has that frame.
                    prop_assert_eq!(item.start + item.fade_in, (clip.at + clip.fade_in).clamp(item.start, item.end), "{:?} of {:?}", item, clip);
                    let fades_out = (clip.end() - clip.fade_out).clamp(item.start + item.fade_in, item.end);
                    prop_assert_eq!(item.end - item.fade_out, fades_out, "{:?} of {:?}", item, clip);
                    prop_assert_eq!(item.opacity, (clip.opacity < 1.0).then_some(clip.opacity * 100.0));
                    let whole = (item.start, item.end) == (clip.at, clip.end());
                    if !whole {
                        trimmed += 1;
                    }
                    let Some((first, frames, step)) = media_span(&clip.source, &disk) else {
                        prop_assert_eq!((&item.path, item.r#in, whole), (&None, 0, true), "no media: offline, whole");
                        continue;
                    };
                    let Some(path) = &item.path else {
                        // Offline though it has media: none of its frames are there.
                        let last = frames.map_or(Frame::MAX, |n| first + n);
                        prop_assert!(clip.source_in + clip.duration <= first || clip.source_in >= last, "{:?} offline", item);
                        prop_assert!(whole);
                        continue;
                    };
                    match leaf(media_of(&clip.source).unwrap()).0.unwrap() {
                        Media::Movie { path: movie } => prop_assert_eq!(path, movie),
                        Media::Exr { frames, .. } | Media::Live { frames } => {
                            let name = path.rsplit('/').next().unwrap();
                            prop_assert_eq!(&path[..path.len() - name.len() - 1], &frames.directory);
                            let want = if premiere { "beauty.1001.exr".to_owned() } else { format!("beauty.[1001-{}].exr", frames.last) };
                            prop_assert_eq!(name, want);
                        }
                        Media::Stack { .. } => unreachable!(),
                    }
                    // Frame for frame what the edit shows.
                    if step == 1 {
                        prop_assert_eq!(item.r#in + first, clip.source_in + (item.start - clip.at));
                        if let Some(n) = frames {
                            prop_assert!(item.out <= n);
                            // Shortened only as far as the media makes it.
                            prop_assert_eq!(item.start - clip.at, (first - clip.source_in).max(0));
                            prop_assert_eq!(clip.end() - item.end, (clip.source_in + clip.duration - first - n).max(0));
                        } else {
                            prop_assert_eq!(clip.end(), item.end);
                        }
                    }
                    if whole {
                        prop_assert_eq!((item.fade_in, item.fade_out), (clip.fade_in, clip.fade_out));
                    }
                }
                prop_assert!(items.next().is_none(), "more clip items than clips");
            }
            // The edit's audio tracks: each written clip is one of the
            // track's, in order, in its span, at its level.
            for (track, items) in edit.audio.iter().zip(&read.audio) {
                let mut clips = track.clips.iter();
                for item in items {
                    let clip = clips.find(|c| c.at <= item.start && item.end <= c.end()).expect("an audio item is one of its track's clips");
                    prop_assert_eq!(&item.name, &clip_name(&clip.source));
                    let slug = matches!(clip.source, FrozenSource::Slug { .. });
                    prop_assert!(!slug, "a slug has no sound to write");
                    let level = (clip.gain_db != 0.0).then(|| level(clip.gain_db));
                    prop_assert!(match (item.level, level) { (Some(a), Some(b)) => (a - b).abs() < 1e-3, (None, None) => true, _ => false }, "{:?} at {} dB", item, clip.gain_db);
                    if let Some((first, _, _)) = media_span(&clip.source, &disk).filter(|_| item.path.is_some()) {
                        prop_assert_eq!(item.r#in + first, clip.source_in + (item.start - clip.at));
                    }
                }
            }
            // The sound of shot clips: each linked both ways to its
            // picture, over the same frames of the same file.
            let mut sounds = 0;
            for (t, items) in read.audio.iter().enumerate().skip(edit.audio.len()) {
                prop_assert!(!items.is_empty(), "an empty sound track");
                for (i, item) in items.iter().enumerate() {
                    sounds += 1;
                    prop_assert_eq!(item.links.len(), 2);
                    let (ref id, ref kind, track, index) = item.links[0];
                    prop_assert_eq!(kind, "video");
                    let picture = &read.video[track as usize - 1][index as usize - 1];
                    prop_assert_eq!(&picture.id, id);
                    prop_assert_eq!(&picture.links, &item.links);
                    prop_assert_eq!(&item.links[1], &(item.id.clone(), "audio".to_owned(), t as Frame + 1, i as Frame + 1));
                    prop_assert_eq!((picture.start, picture.end, picture.r#in, &picture.path), (item.start, item.end, item.r#in, &item.path));
                    let has_sound = picture.path.as_ref().and_then(|p| disk.movies.get(p)).is_some_and(|m| m.audio);
                    prop_assert!(has_sound, "{:?} has no sound", picture);
                }
            }
            for (track, items) in edit.video.iter().zip(&read.video) {
                for item in items.iter().filter(|i| i.links.is_empty()) {
                    // A shot's movie with sound never goes without its sound.
                    let sounding = item.path.as_ref().and_then(|p| disk.movies.get(p)).is_some_and(|m| m.audio);
                    let of_shot = track.clips.iter().any(|c| {
                        matches!(&c.source, FrozenSource::Shot { take: Some(take), .. } if matches!(&take.media, Media::Movie { .. }))
                            && c.at <= item.start
                            && item.end <= c.end()
                    });
                    prop_assert!(!(sounding && of_shot), "{:?} lost its sound", item);
                }
            }
            let ids: Vec<&String> = read.video.iter().chain(&read.audio).flatten().map(|i| &i.id).collect();
            let mut unique = ids.clone();
            unique.sort();
            unique.dedup();
            prop_assert_eq!(unique.len(), ids.len(), "clip item ids are their own");
            // Markers: the edit's, then the slugs.
            let mut want: Vec<(Frame, Frame, String, String)> = edit.markers.iter().map(|m| (m.at, -1, m.note.clone(), String::new())).collect();
            slugs.sort();
            want.extend(slugs);
            prop_assert_eq!(&read.markers, &want);
            // The summary counts what was written, and notes every clip
            // that was shortened or left without its media.
            prop_assert_eq!(summary.video_tracks, read.video.len());
            prop_assert_eq!(summary.audio_tracks, read.audio.len());
            prop_assert_eq!(summary.clips, read.video.iter().map(Vec::len).sum::<usize>());
            prop_assert_eq!(summary.audio_clips, read.audio.iter().map(Vec::len).sum::<usize>());
            prop_assert_eq!(summary.shot_sounds, sounds);
            prop_assert_eq!(summary.markers, edit.markers.len());
            let fades = |tracks: &[Vec<Read>]| tracks.iter().flatten().map(|i| usize::from(i.fade_in > 0) + usize::from(i.fade_out > 0)).sum::<usize>();
            prop_assert_eq!(summary.transitions, fades(&read.video) + fades(&read.audio[..edit.audio.len()]));
            let video_clips = || edit.video.iter().flat_map(|t| &t.clips);
            prop_assert_eq!(summary.count(Kind::Slug), video_clips().filter(|c| matches!(c.source, FrozenSource::Slug { .. })).count());
            let no_take = |s: &FrozenSource| matches!(s, FrozenSource::Shot { take: None, .. });
            let audio_no_take = edit.audio.iter().flat_map(|t| &t.clips).filter(|c| no_take(&c.source)).count();
            prop_assert_eq!(summary.count(Kind::NoTake), video_clips().filter(|c| no_take(&c.source)).count() + audio_no_take);
            let stacks = video_clips().filter(|c| matches!(media_of(&c.source), Some(Media::Stack { .. }))).count();
            prop_assert_eq!(summary.count(Kind::Stack), stacks);
            prop_assert!(summary.count(Kind::Trimmed) >= trimmed, "{} shortened, {:?}", trimmed, summary.notes);
            let offline = read.video.iter().chain(&read.audio).flatten().filter(|i| i.path.is_none()).count();
            prop_assert_eq!(offline, summary.count(Kind::NoTake) + summary.count(Kind::Outside));
            let written_audio: usize = read.audio[..edit.audio.len()].iter().map(Vec::len).sum();
            let audio_clips: usize = edit.audio.iter().map(|t| t.clips.len()).sum();
            prop_assert_eq!(audio_clips - written_audio, summary.count(Kind::Silent), "an audio clip left out without a note");
            // Every clip item and transition plays at the sequence's rate,
            // whatever its movie's; a file says its movie's own, when the
            // movie is there to say it.
            let rate_of = |node: &Node| (node.child("rate").int("timebase"), node.child("rate").text_of("ntsc") == "TRUE");
            let own = (Frame::from(edit.fps), false);
            prop_assert_eq!(rate_of(&read.root), own);
            prop_assert_eq!(rate_of(read.root.child("timecode")), own);
            for kind in ["video", "audio"] {
                for item in read.root.child("media").child(kind).all("track").flat_map(|t| &t.children) {
                    prop_assert_eq!(rate_of(item), own, "a {} at another rate than the sequence", &item.tag);
                    if item.tag != "clipitem" || !item.child("file").has("name") {
                        continue;
                    }
                    let file = item.child("file");
                    let movie = file.has("pathurl").then(|| url_path(file.text_of("pathurl")).unwrap()).and_then(|p| disk.movies.get(&p).cloned());
                    let want = match movie.and_then(|m| m.rate) {
                        Some((num, den)) => (num * 1001 / den / 1000, den == 1001),
                        None => own,
                    };
                    prop_assert_eq!(rate_of(file), want, "{}", file.text_of("name"));
                }
            }
            // The same cut is always the same file.
            prop_assert_eq!(sequence(&edit, "cut", &disk, &options).0, xml);
        }

        /// A clip's transform is not in the file (the hook for it writes
        /// nothing yet): a cut whose clips are moved, scaled or turned is
        /// written as the same cut with every clip as fitted, byte for
        /// byte, so no transform can spoil a clip item. The summary says
        /// which clips it was left out of: every transformed clip that
        /// is written (a slug is a gap), by name, in order; nothing else
        /// of the summary changes.
        #[test]
        fn a_transform_is_left_out_of_the_xmeml_and_named_in_the_summary(
            edit in cuts(),
            kinds in prop::collection::vec(0usize..6, 1..5),
            given in prop::collection::vec(prop_oneof![1 => Just(crate::Transform::IDENTITY), 2 => crate::document::tests::transforms()], 18),
        ) {
            let disk = disk_for(&edit, &kinds);
            let mut moved = edit.clone();
            let mut names = Vec::new();
            for (i, clip) in moved.video.iter_mut().flat_map(|t| &mut t.clips).enumerate() {
                clip.transform = given[i % given.len()];
                if !clip.transform.is_identity() && !matches!(clip.source, FrozenSource::Slug { .. }) {
                    names.push(clip_name(&clip.source));
                }
            }
            prop_assert_eq!(moved.problem(), None);
            let (plain_xml, plain) = sequence(&edit, "cut", &disk, &Options::default());
            let (xml, mut summary) = sequence(&moved, "cut", &disk, &Options::default());
            prop_assert_eq!(&xml, &plain_xml);
            read(&xml);
            prop_assert_eq!(plain.count(Kind::Transform), 0);
            prop_assert_eq!(summary.notes.remove(&Kind::Transform).unwrap_or_default(), names);
            prop_assert_eq!(&summary, &plain);
            prop_assert_eq!(summary.lines(), plain.lines());
        }

        /// Any path a movie is at reads back from the `pathurl` it is
        /// written under: drive letters, UNC shares, spaces, letters
        /// outside ASCII.
        #[test]
        fn a_movie_is_referred_to_by_its_path(folder in any_path(), file in segment()) {
            let path = format!("{folder}/{file}.mp4");
            let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
            edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 10, 1001, shot("010", Media::Movie { path: path.clone() }))] });
            let (xml, _) = sequence(&edit, "cut", &Disk::default(), &Options::default());
            let read = read(&xml);
            prop_assert_eq!(read.video[0][0].path.as_ref(), Some(&path));
            let file_name = format!("{file}.mp4");
            let written = read.root.child("media").child("video").child("track").child("clipitem").child("file").text_of("name").to_owned();
            prop_assert_eq!(written, file_name);
        }
    }

    #[test]
    fn the_reader_refuses_what_is_not_well_formed() {
        let head = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n";
        assert!(parse(&format!("{head}<a><b>x</b></a>")).is_ok());
        for bad in
            ["<a><b>x</a></b>", "<a>x & y</a>", "<a>x < y</a>", "<a>", "<a></a><b></b>", "<a>\u{1}</a>", "<a>&nbsp;</a>", "<a b=\"1></a>"]
        {
            assert!(parse(&format!("{head}{bad}")).is_err(), "{bad} parsed");
        }
        assert!(parse("<a></a>").is_err(), "no prolog");
    }

    #[test]
    fn numbers_and_levels() {
        assert_eq!(number(50.0), "50");
        assert_eq!(number(12.5), "12.5");
        assert_eq!(number(0.0), "0");
        assert_eq!(number(33.333333), "33.33333");
        assert_eq!(number(level(0.0)), "1");
        assert_eq!(number(level(12.0)), "3.98107");
        assert_eq!(number(level(40.0)), "3.98107", "no louder than the filter holds");
    }
}
