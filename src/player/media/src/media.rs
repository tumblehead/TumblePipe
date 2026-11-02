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

    pub fn frame_count(&self) -> usize {
        match self {
            Self::Movie(index) => index.frame_count(),
            Self::Exr { sequence, .. } => sequence.frame_count(),
        }
    }

    pub fn rate(&self) -> (i32, i32) {
        match self {
            Self::Movie(index) => index.rate(),
            Self::Exr { rate, .. } => *rate,
        }
    }

    /// The frame number of index `index`: an EXR sequence's own; None for a
    /// movie, whose frames carry no numbers.
    pub fn frame_number(&self, index: usize) -> Option<i64> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { sequence, .. } => Some(sequence.frame_number(index)),
        }
    }

    /// The EXR layers to pick a view from; empty for a movie.
    pub fn layers(&self) -> &[ExrLayer] {
        match self {
            Self::Movie(_) => &[],
            Self::Exr { header, .. } => &header.layers,
        }
    }

    /// The view the media opens on (meaningless for a movie).
    pub fn default_view(&self) -> ExrView {
        ExrSequence::default_view(self.layers())
    }

    /// The colour space of scene-linear frames (shown through the display
    /// transform); None for a movie, which is display-referred.
    pub fn colour_space(&self) -> Option<&str> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { colour_space, .. } => Some(colour_space),
        }
    }
}
