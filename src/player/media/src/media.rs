//! A clip's media, opened: what it is (its frames, rate, layers, colour
//! space), read once and never changed after, so anything can read it from
//! any thread without waiting. Decoding state lives elsewhere: a movie's in
//! a [`MovieDecoder`](crate::MovieDecoder) the player keeps, an EXR
//! sequence's nowhere (any frame is one file read).

use std::sync::Arc;

use crate::exr_sequence::{ExrHeader, ExrLayer, ExrSequence, ExrView};
use crate::movie::MovieIndex;
use crate::{Error, Result};

#[derive(Debug)]
pub enum Media {
    Movie(Arc<MovieIndex>),
    Exr {
        sequence: ExrSequence,
        header: ExrHeader,
        /// The colour space its frames are in.
        colour_space: String,
        rate: (i32, i32),
    },
    /// A shot's render layers, bottom first, each named and opened: all
    /// movies or all EXR sequences (of one colour space), composited into
    /// one picture (see [`crate::composite`]).
    Stack(Vec<(String, Arc<Media>)>),
}

impl Media {
    /// The movie at `path`. It plays at its own timestamps.
    pub fn movie(path: &str) -> Result<Self> {
        Ok(Self::Movie(Arc::new(MovieIndex::read(path)?)))
    }

    /// The EXR `sequence`, played at `rate`. Its frames must say what colour
    /// space they are in: a render that does not cannot be shown right.
    pub fn exr(sequence: ExrSequence, rate: (i32, i32)) -> Result<Self> {
        let header = sequence.header()?;
        let colour_space = header.colour_space.clone().ok_or_else(|| Error::Open {
            path: sequence.describe(),
            message: "the frames do not say their colour space (oiio:ColorSpace)".into(),
        })?;
        Ok(Self::Exr { sequence, header, colour_space, rate })
    }

    /// `layers`, bottom first, as one stack. Refused: no layers, a stack
    /// among them, movies mixed with EXRs, EXRs of different colour spaces.
    pub fn stack(layers: Vec<(String, Arc<Media>)>) -> Result<Self> {
        let fail = |message: String| Error::Open { path: layers.iter().map(|l| l.0.as_str()).collect::<Vec<_>>().join(", "), message };
        let Some((first_name, first)) = layers.first() else {
            return Err(fail("a stack of no layers".into()));
        };
        for (name, layer) in &layers {
            if matches!(**layer, Self::Stack(_)) {
                return Err(fail(format!("layer {name} is a stack")));
            }
            if layer.is_exr() != first.is_exr() {
                return Err(fail(format!("layer {name} is not of the kind of {first_name}")));
            }
            if layer.colour_space() != first.colour_space() {
                return Err(fail(format!(
                    "layer {name} is in {}, {first_name} in {}",
                    layer.colour_space().unwrap_or_default(),
                    first.colour_space().unwrap_or_default()
                )));
            }
        }
        Ok(Self::Stack(layers))
    }

    /// Its frames: a stack's longest layer's (a shorter layer is left out of
    /// the frames past its end).
    pub fn frame_count(&self) -> usize {
        match self {
            Self::Movie(index) => index.frame_count(),
            Self::Exr { sequence, .. } => sequence.frame_count(),
            Self::Stack(layers) => layers.iter().map(|l| l.1.frame_count()).max().unwrap_or(0),
        }
    }

    pub fn rate(&self) -> (i32, i32) {
        match self {
            Self::Movie(index) => index.rate(),
            Self::Exr { rate, .. } => *rate,
            Self::Stack(layers) => layers[0].1.rate(),
        }
    }

    /// The frame number of index `index`: an EXR sequence's own; None for a
    /// movie, whose frames carry no numbers. A stack's is its bottom layer's.
    pub fn frame_number(&self, index: usize) -> Option<i64> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { sequence, .. } => Some(sequence.frame_number(index)),
            Self::Stack(layers) => layers[0].1.frame_number(index),
        }
    }

    /// The EXR layers to pick a view from; empty for a movie, and for a
    /// stack, each of whose layers shows its own default view.
    pub fn layers(&self) -> &[ExrLayer] {
        match self {
            Self::Movie(_) | Self::Stack(_) => &[],
            Self::Exr { header, .. } => &header.layers,
        }
    }

    /// The view the media opens on (meaningless for a movie or a stack).
    pub fn default_view(&self) -> ExrView {
        ExrSequence::default_view(self.layers())
    }

    /// The colour space of scene-linear frames (shown through the display
    /// transform); None for a movie, which is display-referred. A stack's is
    /// its layers'.
    pub fn colour_space(&self) -> Option<&str> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { colour_space, .. } => Some(colour_space),
            Self::Stack(layers) => layers[0].1.colour_space(),
        }
    }

    /// Whether its frames are EXR reads: an EXR sequence, or a stack of them.
    pub fn is_exr(&self) -> bool {
        match self {
            Self::Movie(_) => false,
            Self::Exr { .. } => true,
            Self::Stack(layers) => layers.first().is_some_and(|l| l.1.is_exr()),
        }
    }

    /// A stack's layer names, bottom first; empty for anything else.
    pub fn stack_layers(&self) -> Vec<&str> {
        match self {
            Self::Stack(layers) => layers.iter().map(|l| l.0.as_str()).collect(),
            _ => Vec::new(),
        }
    }
}
