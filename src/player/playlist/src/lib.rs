//! A shot's takes and their media, as TumblePipe finds them: what a shot
//! can show (one take per department and version), each a playblast movie,
//! a render's EXR sequence, or a stack of a department's render layers.
//!
//! `th_pipe::discovery` finds them on disk (the port of Python's
//! `tumblepipe.pipe.review_playlist`); a published edit freezes the take
//! each shot clip played (`th_edit::FrozenTake`), so the JSON form is part
//! of the edit file: every field is required and no other is allowed, so a
//! writer and reader out of step fail loudly instead of playing something
//! else:
//!
//! ```json
//! {"label": "denoise v0030 default beauty", "department": "denoise", "version": "v0030",
//!  "media": {"kind": "exr", "layer": "default",
//!            "frames": {"directory": "P:/.../beauty", "prefix": "beauty.", "suffix": ".exr",
//!                       "padding": 4, "first": 1001, "last": 1100, "step": 1}}}
//! ```
//!
//! A shot's takes are grouped by kind (movies, then EXRs), department in
//! pipeline order, render layer, and versions oldest first. A `stack` take
//! is a shot's render layers (its channels) on top of each other, bottom
//! first: a layered playblast's movies (straight alpha, shown as they are)
//! or a render version's layers (premultiplied EXRs, shown through OCIO).
//! Its layers are all movies or all EXRs, never stacks, and the EXRs of one
//! stack share their frame range ([`Media::problem`]). A department's stacks
//! follow its plain takes of that kind, as their layer [`STACK_STREAM`].

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
/// A shot and its takes, as discovery finds them.
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

/// The layer a stack take names: a department's stacks are apart from its
/// plain takes.
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

/// A stream: a kind, department and layer, whose takes are its versions
/// (the take menu's grouping).
pub type Stream<'a> = (Kind, &'a str, Option<&'a str>);

impl Media {
    /// Its kind; a stack's is its layers' (a movie for a stack with none,
    /// which [`Media::problem`] refuses).
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
    pub fn problem(&self) -> Option<String> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::{Value, json};

    fn exr(name: &str, last: i64) -> StackLayer {
        let frames =
            Frames { directory: "P:/r".into(), prefix: "beauty.".into(), suffix: ".exr".into(), padding: 4, first: 1001, last, step: 1 };
        StackLayer { name: name.into(), media: Media::Exr { layer: name.into(), frames } }
    }

    fn movie(name: &str) -> StackLayer {
        StackLayer { name: name.into(), media: Media::Movie { path: format!("P:/p/{name}.mov") } }
    }

    /// A stack that cannot play: empty, nested, mixed, EXRs of different
    /// frames, a layer named twice or not at all, a layer without frames.
    #[test]
    fn what_cannot_stack_is_a_problem() {
        let stack = |layers: Vec<StackLayer>| Media::Stack { layers }.problem();
        assert_eq!(stack(vec![exr("default", 1100)]), None, "one layer stacks");
        assert_eq!(stack(vec![movie("default"), movie("fx")]), None, "movies stack");
        assert!(stack(vec![]).is_some(), "no layers");
        assert!(stack(vec![exr("default", 1100), movie("fx")]).is_some(), "mixed kinds");
        assert!(stack(vec![exr("default", 1100), exr("default", 1100)]).is_some(), "a layer named twice");
        assert!(stack(vec![exr("default", 1100), exr("", 1100)]).is_some(), "a layer named nothing");
        assert!(stack(vec![exr("default", 1100), exr("fx", 1040)]).is_some(), "other frames");
        assert!(stack(vec![exr("default", 1100), exr("fx", 1000)]).is_some(), "a layer without frames");
        let nested = StackLayer { name: "fx".into(), media: Media::Stack { layers: vec![exr("fx", 1100)] } };
        assert!(stack(vec![exr("default", 1100), nested]).is_some(), "a stack in a stack");
        let take = Take {
            label: "l".into(),
            department: "light".into(),
            version: "v0001".into(),
            media: Media::Stack { layers: vec![movie("a")] },
        };
        assert_eq!((take.kind(), take.layer(), take.is_stack()), (Kind::Movie, Some(STACK_STREAM), true));
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
        /// A take reads back as it was written, and what discovery writes
        /// plays: no take it makes has a problem.
        #[test]
        fn takes_round_trip(take in takes()) {
            let json = serde_json::to_string(&take).unwrap();
            prop_assert_eq!(serde_json::from_str::<Take>(&json).unwrap(), take.clone());
            prop_assert_eq!(take.media.problem(), None);
        }

        /// A field the reader does not know, or one it needs missing, on any
        /// object of a take, is an error, never quietly skipped: a published
        /// edit keeps its takes in this form.
        #[test]
        fn unknown_and_missing_fields_are_errors(take in takes(), pick in any::<prop::sample::Index>(), drop in any::<prop::sample::Index>()) {
            let doc = serde_json::to_value(&take).unwrap();
            let mut paths = Vec::new();
            objects(&doc, Vec::new(), &mut paths);
            let path = &paths[pick.index(paths.len())];

            let mut extra = doc.clone();
            object_at(&mut extra, path).insert("extra".into(), json!(1));
            prop_assert!(serde_json::from_value::<Take>(extra).is_err(), "an unknown field at {:?} was read", path);

            let mut missing = doc.clone();
            let object = object_at(&mut missing, path);
            let key = object.keys().nth(drop.index(object.len())).unwrap().clone();
            object.remove(&key);
            prop_assert!(serde_json::from_value::<Take>(missing).is_err(), "{:?} without {} was read", path, key);
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
    }
}
