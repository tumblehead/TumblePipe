//! The sequence player's playlist: shots in order, each with the takes it
//! can show (one per department and version).
//!
//! TumblePipe writes it (`tumblepipe.pipe.review_playlist`) as JSON on the
//! player's standard input; every field is required and no other is
//! allowed, so a writer and reader out of step fail loudly instead of
//! playing something else:
//!
//! ```json
//! {"name": "HideAndReek review",
//!  "ocio": "P:/HideAndReek/_config/ocio/tumblehead.ocio",
//!  "shots": [
//!   {"name": "010", "sequence": "HideAndReek", "fps": 24, "take": 0,
//!    "takes": [
//!     {"label": "animation v0015", "department": "animation", "version": "v0015",
//!      "media": {"kind": "movie", "path": "P:/.../v0015.mp4"}},
//!     {"label": "animation v0015 layers", "department": "animation", "version": "v0015",
//!      "media": {"kind": "stack", "layers": [
//!       {"name": "default", "media": {"kind": "movie", "path": "P:/.../default/..._v0015.mov"}},
//!       {"name": "fx", "media": {"kind": "movie", "path": "P:/.../fx/..._v0015.mov"}}]}},
//!     {"label": "denoise v0030 default beauty", "department": "denoise", "version": "v0030",
//!      "media": {"kind": "exr", "layer": "default",
//!                "frames": {"directory": "P:/.../beauty", "prefix": "beauty.", "suffix": ".exr",
//!                           "padding": 4, "first": 1001, "last": 1100, "step": 1}}}]}]}
//! ```
//!
//! The takes come in the order the player steps through them: grouped by
//! kind (movies, then EXRs), department in pipeline order, render layer, and
//! versions oldest first. A department (and, for a render, its layer) is a
//! *stream*; its takes are that stream's versions. See [`Shot::version_step`],
//! [`Shot::department_step`] and [`Shot::last_of_kind`].
//!
//! A `stack` take is a shot's render layers (its channels) on top of each
//! other, bottom first: a layered playblast's movies (straight alpha, shown
//! as they are) or a render version's layers (premultiplied EXRs, shown
//! through OCIO). Its layers are all movies or all EXRs, never stacks, and
//! the EXRs of one stack share their frame range. A department's stacks are
//! a stream of their own, `(kind, department, Some("layers"))`, written
//! right after its plain takes of that kind.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// What a take's media is: a movie (TumblePipe's playblasts) or an EXR
/// sequence (its renders).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Kind {
    Movie,
    Exr,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Playlist {
    pub name: String,
    /// The OCIO config EXRs are shown through.
    pub ocio: String,
    pub shots: Vec<Shot>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Shot {
    pub name: String,
    /// The sequence the shot is in (its entity's path between `shots` and
    /// the shot).
    pub sequence: String,
    /// The rate its EXR takes play at; a movie plays at its own timestamps.
    pub fps: u32,
    /// Its length in frames, its cut: how long it stands in the sequence
    /// while its take opens, or when the take cannot play.
    pub length: u32,
    /// The take it opens on.
    pub take: usize,
    pub takes: Vec<Take>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Take {
    pub label: String,
    /// The department that made it.
    pub department: String,
    /// Its version ("v0015").
    pub version: String,
    pub media: Media,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Media {
    Movie {
        path: String,
    },
    /// A render: one of its layers ("default", "slapcomp"), as the frames of
    /// the AOV it shows.
    Exr {
        layer: String,
        frames: Frames,
    },
    /// Render layers stacked, bottom first: all movies or all EXRs.
    Stack {
        layers: Vec<StackLayer>,
    },
}

/// One layer of a stack: its name (the shot's channel) and its media, a
/// movie or an EXR sequence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StackLayer {
    pub name: String,
    pub media: Media,
}

/// The layer a stack take's stream names: a department's stacks are one
/// stream, apart from its plain takes.
pub const STACK_STREAM: &str = "layers";

/// An EXR sequence's frame files: frame `n` is
/// `directory/prefix + n (zero-padded to padding digits) + suffix`, for `n`
/// from `first` to `last` by `step`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Frames {
    pub directory: String,
    pub prefix: String,
    pub suffix: String,
    pub padding: usize,
    pub first: i64,
    pub last: i64,
    pub step: i64,
}

impl Frames {
    pub fn count(&self) -> usize {
        ((self.last - self.first) / self.step + 1) as usize
    }

    /// The frame number of index `index`.
    pub fn number(&self, index: usize) -> i64 {
        self.first + index as i64 * self.step
    }

    pub fn path(&self, index: usize) -> PathBuf {
        let (number, padding) = (self.number(index), self.padding);
        PathBuf::from(&self.directory).join(format!("{}{number:0padding$}{}", self.prefix, self.suffix))
    }
}

/// A stream: a kind, department and layer, whose takes are its versions.
pub type Stream<'a> = (Kind, &'a str, Option<&'a str>);

impl Media {
    /// Its kind; a stack's is its layers' (a movie for a stack with none,
    /// which a checked playlist never has).
    pub fn kind(&self) -> Kind {
        match self {
            Self::Movie { .. } => Kind::Movie,
            Self::Exr { .. } => Kind::Exr,
            Self::Stack { layers } => layers.first().map_or(Kind::Movie, |l| l.media.kind()),
        }
    }

    /// The layers of a stack; None for anything else.
    pub fn stack(&self) -> Option<&[StackLayer]> {
        match self {
            Self::Stack { layers } => Some(layers),
            _ => None,
        }
    }

    /// What it is read from, for messages: the movie, the sequence's first
    /// frame, or each of a stack's.
    pub fn describe(&self) -> String {
        match self {
            Self::Movie { path } => path.clone(),
            Self::Exr { frames, .. } => frames.path(0).display().to_string(),
            Self::Stack { layers } => layers.iter().map(|l| l.media.describe()).collect::<Vec<_>>().join(", "),
        }
    }

    /// What makes it unplayable, if anything: an empty frame range; a stack
    /// that is empty, holds a stack, mixes movies and EXRs, names a layer
    /// twice or not at all, or whose EXRs do not share their frame range.
    fn problem(&self) -> Option<String> {
        match self {
            Self::Movie { .. } => None,
            Self::Exr { frames, .. } => (frames.step <= 0 || frames.last < frames.first)
                .then(|| format!("frames {} to {} by {}", frames.first, frames.last, frames.step)),
            Self::Stack { layers } => {
                let Some(first) = layers.first().map(|l| &l.media) else {
                    return Some("a stack of no layers".into());
                };
                for (i, layer) in layers.iter().enumerate() {
                    if layer.name.is_empty() || layers[..i].iter().any(|l| l.name == layer.name) {
                        return Some(format!("stack layer {i} is named '{}', empty or twice", layer.name));
                    }
                    if layer.media.stack().is_some() {
                        return Some(format!("stack layer {} is a stack", layer.name));
                    }
                    if layer.media.kind() != first.kind() {
                        return Some(format!("stack layer {} is not of the kind of {}", layer.name, layers[0].name));
                    }
                    if let Some(problem) = layer.media.problem() {
                        return Some(format!("stack layer {}: {problem}", layer.name));
                    }
                    if let (Self::Exr { frames: a, .. }, Self::Exr { frames: b, .. }) = (first, &layer.media)
                        && (a.first, a.last, a.step) != (b.first, b.last, b.step)
                    {
                        return Some(format!("stack layer {} has other frames than {}", layer.name, layers[0].name));
                    }
                }
                None
            }
        }
    }
}

impl Take {
    pub fn kind(&self) -> Kind {
        self.media.kind()
    }

    /// A render's layer, [`STACK_STREAM`] for a stack; None for a playblast.
    pub fn layer(&self) -> Option<&str> {
        match &self.media {
            Media::Movie { .. } => None,
            Media::Exr { layer, .. } => Some(layer),
            Media::Stack { .. } => Some(STACK_STREAM),
        }
    }

    /// Whether it is a stack of layers.
    pub fn is_stack(&self) -> bool {
        self.media.stack().is_some()
    }

    /// The stream it is a version of: its kind, department and layer.
    pub fn stream(&self) -> Stream<'_> {
        (self.kind(), &self.department, self.layer())
    }
}

impl Shot {
    /// The rate its EXR takes play at, as a fraction.
    pub fn rate(&self) -> (i32, i32) {
        (self.fps as i32, 1)
    }

    /// The take `delta` versions from `from` (positive: newer) in its
    /// stream, stopping at the oldest and the newest.
    pub fn version_step(&self, from: usize, delta: i64) -> usize {
        let stream = self.takes[from].stream();
        let versions: Vec<usize> = (0..self.takes.len()).filter(|&t| self.takes[t].stream() == stream).collect();
        let at = versions.iter().position(|&t| t == from).expect("a take is a version of its own stream") as i64;
        versions[(at + delta).clamp(0, versions.len() as i64 - 1) as usize]
    }

    /// The newest version of the stream `delta` streams from `from`'s
    /// (positive: downstream) among those of its kind, in playlist order.
    /// `from` when there is no stream that far.
    pub fn department_step(&self, from: usize, delta: i64) -> usize {
        let shown = self.takes[from].stream();
        let mut streams: Vec<Stream> = Vec::new();
        for take in self.takes.iter().filter(|t| t.kind() == shown.0) {
            if !streams.contains(&take.stream()) {
                streams.push(take.stream());
            }
        }
        let at = streams.iter().position(|&s| s == shown).expect("a take's stream is among its kind's") as i64;
        match usize::try_from(at + delta).ok().and_then(|t| streams.get(t)) {
            Some(&target) if target != shown => {
                (0..self.takes.len()).rev().find(|&t| self.takes[t].stream() == target).expect("a stream has a take")
            }
            _ => from,
        }
    }

    /// The take a switch to `kind` shows: the last of that kind, the newest
    /// version of its most downstream stream. None when there is none.
    pub fn last_of_kind(&self, kind: Kind) -> Option<usize> {
        (0..self.takes.len()).rev().find(|&t| self.takes[t].kind() == kind)
    }
}

impl Playlist {
    /// The playlist in `json`, checked: what parses but cannot play (no
    /// shots, a shot without takes or opening on a take it has not, a rate
    /// or frame range that is none) is an error too.
    pub fn parse(json: &str) -> Result<Self, String> {
        let playlist: Self = serde_json::from_str(json).map_err(|e| format!("not a TumblePipe playlist: {e}"))?;
        playlist.check()?;
        Ok(playlist)
    }

    /// The playlist on standard input.
    pub fn read_stdin() -> Result<Self, String> {
        let json = std::io::read_to_string(std::io::stdin()).map_err(|e| format!("reading the playlist: {e}"))?;
        Self::parse(&json)
    }

    fn check(&self) -> Result<(), String> {
        if self.shots.is_empty() {
            return Err("the playlist has no shots".into());
        }
        for shot in &self.shots {
            let fail = |what: String| Err(format!("shot {}: {what}", shot.name));
            if shot.takes.is_empty() {
                return fail("no takes".into());
            }
            if shot.take >= shot.takes.len() {
                return fail(format!("opens on take {} of {}", shot.take, shot.takes.len()));
            }
            if shot.fps == 0 || i32::try_from(shot.fps).is_err() {
                return fail(format!("fps {}", shot.fps));
            }
            if shot.length == 0 {
                return fail("no frames".into());
            }
            for take in &shot.takes {
                if take.department.is_empty() || take.version.is_empty() {
                    return fail(format!("{}: no department or version", take.label));
                }
                if let Some(problem) = take.media.problem() {
                    return fail(format!("{}: {problem}", take.label));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::{Value, json};

    fn fixture() -> Value {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/playlist.json");
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn reads_the_tumblepipe_schema() {
        let playlist = Playlist::parse(&fixture().to_string()).unwrap();
        assert_eq!(playlist.name, "HideAndReek review");
        assert_eq!(playlist.ocio, "P:/HideAndReek/_config/ocio/tumblehead.ocio");
        let s010 = &playlist.shots[0];
        assert_eq!((s010.name.as_str(), s010.sequence.as_str(), s010.rate(), s010.take), ("010", "HideAndReek", (24, 1), 1));
        assert_eq!(s010.takes[1].label, "animation v0015");
        assert_eq!(
            s010.takes[1].media,
            Media::Movie { path: "P:/HideAndReek_render/playblast/shots/HideAndReek/010/animation/v0015.mp4".into() }
        );
        let Media::Exr { layer, frames } = &s010.takes[2].media else { panic!("take 2 is a render") };
        assert_eq!(layer, "default");
        assert_eq!(frames.count(), 100);
        assert_eq!(
            frames.path(0),
            PathBuf::from("P:/HideAndReek_render/render/shots/HideAndReek/010/denoise/default/v0030/beauty/beauty.1001.exr")
        );
        assert_eq!(s010.takes[2].layer(), Some("default"));
        assert_eq!(s010.takes[1].layer(), None);

        let s020 = &playlist.shots[1];
        let movies = s020.takes[1].media.stack().expect("take 1 is a stack");
        assert_eq!(movies.iter().map(|l| l.name.as_str()).collect::<Vec<_>>(), ["default", "fx"]);
        assert_eq!(s020.takes[1].stream(), (Kind::Movie, "animation", Some(STACK_STREAM)));
        assert_eq!(s020.takes[2].stream(), (Kind::Exr, "light", Some(STACK_STREAM)));
        assert!(s020.takes[2].is_stack() && !s020.takes[0].is_stack());
    }

    /// A stack that cannot play: empty, nested, mixed, EXRs of different
    /// frames, a layer named twice or not at all.
    #[test]
    fn what_cannot_stack_is_an_error() {
        let with_layers = |layers: Value| {
            let mut doc = fixture();
            doc["shots"][1]["takes"][2]["media"]["layers"] = layers;
            Playlist::parse(&doc.to_string())
        };
        let doc = fixture();
        let exr = doc["shots"][1]["takes"][2]["media"]["layers"][0].clone();
        let movie = doc["shots"][1]["takes"][1]["media"]["layers"][0].clone();
        let named = |layer: &Value, name: &str| {
            let mut layer = layer.clone();
            layer["name"] = json!(name);
            layer
        };
        assert!(with_layers(json!([exr.clone()])).is_ok(), "one layer stacks");
        assert!(with_layers(json!([])).is_err(), "no layers");
        assert!(with_layers(json!([exr.clone(), named(&movie, "fx")])).is_err(), "mixed kinds");
        assert!(with_layers(json!([exr.clone(), named(&exr, "default")])).is_err(), "a layer named twice");
        assert!(with_layers(json!([exr.clone(), named(&exr, "")])).is_err(), "a layer named nothing");
        let mut shorter = named(&exr, "fx");
        shorter["media"]["frames"]["last"] = json!(1040);
        assert!(with_layers(json!([exr.clone(), shorter])).is_err(), "other frames");
        let mut empty = named(&exr, "fx");
        empty["media"]["frames"]["step"] = json!(0);
        assert!(with_layers(json!([exr.clone(), empty])).is_err(), "a layer without frames");
        let nested = json!({"name": "fx", "media": {"kind": "stack", "layers": [exr.clone()]}});
        assert!(with_layers(json!([exr.clone(), nested])).is_err(), "a stack in a stack");
    }

    /// A department's stacks are a stream of their own, right after its
    /// plain takes: Up/Down stay in it, Ctrl+Up/Down walk into and out of it.
    #[test]
    fn stacks_are_a_stream_after_their_departments_plain_takes() {
        let playlist = Playlist::parse(&fixture().to_string()).unwrap();
        let mut shot = playlist.shots[1].clone();
        // animation v0002, animation v0002 layers, then a newer pair.
        let (plain, stack) = (shot.takes[0].clone(), shot.takes[1].clone());
        shot.takes.truncate(2);
        let newer = |mut t: Take| {
            t.version = "v0003".into();
            t
        };
        shot.takes = vec![plain.clone(), newer(plain), stack.clone(), newer(stack)];
        assert_eq!(shot.version_step(2, 1), 3, "a newer stack");
        assert_eq!(shot.version_step(3, 5), 3, "the newest stack");
        assert_eq!(shot.version_step(0, 1), 1, "plain takes step over the stacks");
        assert_eq!(shot.department_step(0, 1), 3, "into the newest stack");
        assert_eq!(shot.department_step(2, -1), 1, "back to the newest plain take");
    }

    /// Every way the fixture can be put wrong, each its own error.
    #[test]
    fn what_cannot_play_is_an_error() {
        let broken = |path: &[&str], value: Value| {
            let mut doc = fixture();
            let mut at = &mut doc;
            for key in &path[..path.len() - 1] {
                at = match key.parse::<usize>() {
                    Ok(i) => &mut at[i],
                    Err(_) => &mut at[*key],
                };
            }
            at[path[path.len() - 1]] = value;
            Playlist::parse(&doc.to_string())
        };
        assert!(broken(&["shots"], json!([])).is_err(), "no shots");
        assert!(broken(&["shots", "0", "takes"], json!([])).is_err(), "no takes");
        assert!(broken(&["shots", "0", "take"], json!(3)).is_err(), "opens past its takes");
        assert!(broken(&["shots", "0", "fps"], json!(0)).is_err(), "no rate");
        assert!(broken(&["shots", "0", "length"], json!(0)).is_err(), "no length");
        assert!(broken(&["shots", "0", "takes", "2", "media", "frames", "step"], json!(0)).is_err(), "no step");
        assert!(broken(&["shots", "0", "takes", "2", "media", "frames", "last"], json!(1000)).is_err(), "ends before it starts");
        assert!(broken(&["shots", "0", "takes", "0", "media", "kind"], json!("mov")).is_err(), "unknown kind");
        assert!(broken(&["ocio"], Value::Null).is_err(), "no OCIO config");
        assert!(broken(&["shots", "0", "takes", "0", "department"], json!("")).is_err(), "no department");
    }

    /// A take of a few kinds, departments, layers and versions, stacks of
    /// one to three layers among them.
    fn takes() -> impl Strategy<Value = Take> {
        (any::<bool>(), 0..3usize, 0..2usize, 1..20u32, "[a-z ]{0,8}", 0..4usize).prop_map(|(exr, dept, layer, code, label, stacked)| {
            let frames = Frames {
                directory: "P:/r".into(),
                prefix: "beauty.".into(),
                suffix: ".exr".into(),
                padding: 4,
                first: 1001,
                last: 1001 + code as i64,
                step: 1,
            };
            let one = |layer: &str| {
                if exr {
                    Media::Exr { layer: layer.to_owned(), frames: frames.clone() }
                } else {
                    Media::Movie { path: format!("P:/p/{layer}/v{code:04}.mp4") }
                }
            };
            let media = match stacked {
                0 => one(["default", "slapcomp"][layer]),
                n => Media::Stack {
                    layers: ["default", "fx", "bg"][..n]
                        .iter()
                        .map(|&name| StackLayer { name: name.to_owned(), media: one(name) })
                        .collect(),
                },
            };
            Take { label, department: ["layout", "animation", "light"][dept].to_owned(), version: format!("v{code:04}"), media }
        })
    }

    /// A shot of takes in any order, showing any of them.
    fn shots() -> impl Strategy<Value = Shot> {
        (prop::collection::vec(takes(), 1..12), 1..60u32, 1..300u32).prop_flat_map(|(takes, fps, length)| {
            let n = takes.len();
            (Just(takes), 0..n).prop_map(move |(takes, take)| Shot { name: "s".into(), sequence: "q".into(), fps, length, take, takes })
        })
    }

    fn playlists() -> impl Strategy<Value = Playlist> {
        prop::collection::vec(shots(), 1..4).prop_map(|shots| Playlist { name: "p".into(), ocio: "c.ocio".into(), shots })
    }

    /// Every object in `value`, by its path.
    fn objects(value: &Value, at: Vec<String>, out: &mut Vec<Vec<String>>) {
        match value {
            Value::Object(map) => {
                out.push(at.clone());
                for (key, v) in map {
                    objects(v, [at.clone(), vec![key.clone()]].concat(), out);
                }
            }
            Value::Array(items) => {
                for (i, v) in items.iter().enumerate() {
                    objects(v, [at.clone(), vec![i.to_string()]].concat(), out);
                }
            }
            _ => {}
        }
    }

    fn object_at<'a>(value: &'a mut Value, path: &[String]) -> &'a mut serde_json::Map<String, Value> {
        let mut at = value;
        for key in path {
            at = match key.parse::<usize>() {
                Ok(i) if at.is_array() => &mut at[i],
                _ => &mut at[key.as_str()],
            };
        }
        at.as_object_mut().unwrap()
    }

    proptest! {
        /// A playlist reads back as it was written.
        #[test]
        fn playlists_round_trip(playlist in playlists()) {
            let json = serde_json::to_string(&playlist).unwrap();
            prop_assert_eq!(Playlist::parse(&json).unwrap(), playlist);
        }

        /// A field the reader does not know, or one it needs missing, on any
        /// object of the playlist, is an error, never quietly skipped.
        #[test]
        fn unknown_and_missing_fields_are_errors(playlist in playlists(), pick in any::<prop::sample::Index>(), drop in any::<prop::sample::Index>()) {
            let doc = serde_json::to_value(&playlist).unwrap();
            let mut paths = Vec::new();
            objects(&doc, Vec::new(), &mut paths);
            let path = &paths[pick.index(paths.len())];

            let mut extra = doc.clone();
            object_at(&mut extra, path).insert("extra".into(), json!(1));
            prop_assert!(Playlist::parse(&extra.to_string()).is_err(), "an unknown field at {:?} was read", path);

            let mut missing = doc.clone();
            let object = object_at(&mut missing, path);
            let key = object.keys().nth(drop.index(object.len())).unwrap().clone();
            object.remove(&key);
            prop_assert!(Playlist::parse(&missing.to_string()).is_err(), "{:?} without {} was read", path, key);
        }

        /// Frame paths are the frame numbers from first to last by step,
        /// padded, between the prefix and suffix.
        #[test]
        fn frame_paths_number_the_frames(first in -10i64..2000, count in 1usize..50, step in 1i64..4, padding in 0usize..6) {
            let frames = Frames {
                directory: "d".into(), prefix: "a.".into(), suffix: ".exr".into(), padding,
                first, last: first + (count as i64 - 1) * step + (step - 1) / 2, step,
            };
            prop_assert_eq!(frames.count(), count);
            for index in 0..count {
                let name = frames.path(index).file_name().unwrap().to_string_lossy().into_owned();
                let number = name.strip_prefix("a.").unwrap().strip_suffix(".exr").unwrap();
                let n = first + index as i64 * step;
                prop_assert_eq!(number.parse::<i64>().unwrap(), n);
                // Python's `f'{n:0{padding}d}'`: the sign counts toward the width.
                prop_assert_eq!(number.len(), padding.max(n.to_string().len()));
            }
        }

        /// Stepping versions stays in the shown take's stream, moves the way
        /// it is asked, and stepping newer visits each newer version once
        /// before it stops at the newest.
        #[test]
        fn version_steps_walk_the_stream(shot in shots(), delta in -3i64..4) {
            let stream = shot.takes[shot.take].stream();
            let to = shot.version_step(shot.take, delta);
            prop_assert_eq!(shot.takes[to].stream(), stream);
            let moved_as_asked = match delta.signum() {
                1 => to >= shot.take,
                -1 => to <= shot.take,
                _ => to == shot.take,
            };
            prop_assert!(moved_as_asked);
            let newer: Vec<usize> = (shot.take + 1..shot.takes.len()).filter(|&t| shot.takes[t].stream() == stream).collect();
            let mut walked = Vec::new();
            let mut at = shot.take;
            loop {
                let next = shot.version_step(at, 1);
                if next == at {
                    break;
                }
                walked.push(next);
                at = next;
            }
            prop_assert_eq!(walked, newer);
        }

        /// A department step keeps the kind and lands on the newest version
        /// of another stream, or stays; stepping back returns to the stream
        /// it left.
        #[test]
        fn department_steps_land_on_newest_versions(shot in shots(), delta in -2i64..3) {
            let shown = shot.takes[shot.take].stream();
            let to = shot.department_step(shot.take, delta);
            prop_assert_eq!(shot.takes[to].kind(), shot.takes[shot.take].kind());
            if to == shot.take {
                return Ok(());
            }
            prop_assert!(delta != 0);
            prop_assert_ne!(shot.takes[to].stream(), shown);
            let stream = shot.takes[to].stream();
            prop_assert!(shot.takes[to + 1..].iter().all(|t| t.stream() != stream), "not the newest version");
            let back = shot.department_step(to, -delta);
            prop_assert_eq!(shot.takes[back].stream(), shown);
        }

        /// A switch of kind shows the last take of that kind, if it has one.
        #[test]
        fn a_kind_switch_shows_the_last_take_of_the_kind(shot in shots(), exr in any::<bool>()) {
            let kind = if exr { Kind::Exr } else { Kind::Movie };
            match shot.last_of_kind(kind) {
                Some(t) => {
                    prop_assert_eq!(shot.takes[t].kind(), kind);
                    prop_assert!(shot.takes[t + 1..].iter().all(|x| x.kind() != kind));
                }
                None => prop_assert!(shot.takes.iter().all(|x| x.kind() != kind)),
            }
        }
    }
}
