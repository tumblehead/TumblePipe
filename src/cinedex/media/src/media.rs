//! A clip's media, opened: what it is (its frames, rate, layers, colour
//! space), read once and never changed after, so anything can read it from
//! any thread without waiting. Decoding state lives elsewhere: a movie's in
//! a [`MovieDecoder`](crate::MovieDecoder) the player keeps, an EXR
//! sequence's nowhere (any frame is one file read).

use std::sync::Arc;

use crate::exr_sequence::{ExrHeader, ExrLayer, ExrSequence, ExrView};
use crate::jpg_sequence::JpgSequence;
use crate::movie::MovieIndex;
use crate::{Error, Result};

/// The take a live playblast shows where its render has not reached: the
/// shot's latest playblast movie, frame for frame by frame number (the live
/// render and the movie both count the shot's frames).
#[derive(Debug)]
pub struct Under {
    pub media: Arc<Media>,
    /// The frame number of its index 0.
    pub first: i64,
}

impl Under {
    /// Its index showing frame number `frame`; None where it has none.
    pub fn index(&self, frame: i64) -> Option<usize> {
        self.media.index_at(frame, self.first)
    }
}

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
    /// A live playblast's JPG frames, display-referred like a movie's,
    /// played at `rate`. Frames may still be on their way (see
    /// [`JpgSequence`]); where one is, `under` shows, if there is one.
    Jpg {
        sequence: JpgSequence,
        rate: (i32, i32),
        under: Option<Under>,
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

    /// The live JPG `sequence`, played at `rate`. Opening reads nothing:
    /// its frames may not be written yet.
    pub fn jpg(sequence: JpgSequence, rate: (i32, i32)) -> Self {
        Self::Jpg { sequence, rate, under: None }
    }

    /// The live JPG `sequence` over `under`, which shows where the render
    /// has not reached yet. Refused: `under` is not a movie (an EXR is
    /// scene-linear, shown through a display transform the live frames do
    /// not take; a stack composites, which a frame of a live take does not).
    pub fn jpg_over(sequence: JpgSequence, rate: (i32, i32), under: Under) -> Result<Self> {
        if !matches!(*under.media, Self::Movie(_)) {
            return Err(Error::Open { path: sequence.describe(), message: "only a movie can show under a live playblast".into() });
        }
        Ok(Self::Jpg { sequence, rate, under: Some(under) })
    }

    /// What shows for live frame `index` while its render has not reached
    /// it: the media under it and the index there. None for anything but a
    /// live playblast with media under it, or where that media has no frame.
    pub fn under(&self, index: usize) -> Option<(&Arc<Media>, usize)> {
        match self {
            Self::Jpg { sequence, under: Some(under), .. } => under.index(sequence.frame_number(index)).map(|at| (&under.media, at)),
            _ => None,
        }
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
            if matches!(**layer, Self::Jpg { .. }) {
                return Err(fail(format!("layer {name} is a live playblast")));
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
            Self::Jpg { sequence, .. } => sequence.frame_count(),
            Self::Stack(layers) => layers.iter().map(|l| l.1.frame_count()).max().unwrap_or(0),
        }
    }

    pub fn rate(&self) -> (i32, i32) {
        match self {
            Self::Movie(index) => index.rate(),
            Self::Exr { rate, .. } | Self::Jpg { rate, .. } => *rate,
            Self::Stack(layers) => layers[0].1.rate(),
        }
    }

    /// The frame number of index `index`: an EXR sequence's own; a movie's
    /// its frame list's (a multi-range render's, #86), else counted from the
    /// start frame it records, None when it records neither. A stack's is
    /// its bottom layer's.
    pub fn frame_number(&self, index: usize) -> Option<i64> {
        match self {
            Self::Movie(movie) => match movie.frame_ranges() {
                Some(list) => list.frame_number(index),
                None => movie.start_frame().map(|start| start + index as i64),
            },
            Self::Exr { sequence, .. } => Some(sequence.frame_number(index)),
            Self::Jpg { sequence, .. } => Some(sequence.frame_number(index)),
            Self::Stack(layers) => layers[0].1.frame_number(index),
        }
    }

    /// Its index showing frame number `frame`, its index 0 being frame
    /// `first`: by its frame list when it records one (a multi-range
    /// render's mp4, #86: None in a gap). None where it has no picture.
    pub fn index_at(&self, frame: i64, first: i64) -> Option<usize> {
        if let Some(list) = self.frame_ranges() {
            return list.index_of(frame);
        }
        let at = usize::try_from(frame.checked_sub(first)?).ok()?;
        (at < self.frame_count()).then_some(at)
    }

    /// The frames a movie's pictures are when it records a list of them (a
    /// multi-range render's mp4, #86): its frames are not one run then. A
    /// stack's is its bottom layer's; a sequence's never (each frame is its
    /// own file, numbered).
    pub fn frame_ranges(&self) -> Option<&crate::FrameRanges> {
        match self {
            Self::Movie(movie) => movie.frame_ranges(),
            Self::Stack(layers) => layers[0].1.frame_ranges(),
            Self::Exr { .. } | Self::Jpg { .. } => None,
        }
    }

    /// The EXR layers to pick a view from; empty for a movie, and for a
    /// stack, each of whose layers shows its own default view.
    pub fn layers(&self) -> &[ExrLayer] {
        match self {
            Self::Movie(_) | Self::Jpg { .. } | Self::Stack(_) => &[],
            Self::Exr { header, .. } => &header.layers,
        }
    }

    /// The view the media opens on (meaningless for a movie or a stack).
    pub fn default_view(&self) -> ExrView {
        ExrSequence::default_view(self.layers())
    }

    /// The colour space of scene-linear frames (shown through the display
    /// transform); None for a movie or a live playblast, which are
    /// display-referred. A stack's is its layers'.
    pub fn colour_space(&self) -> Option<&str> {
        match self {
            Self::Movie(_) | Self::Jpg { .. } => None,
            Self::Exr { colour_space, .. } => Some(colour_space),
            Self::Stack(layers) => layers[0].1.colour_space(),
        }
    }

    /// Whether its frames are EXR reads: an EXR sequence, or a stack of them.
    pub fn is_exr(&self) -> bool {
        match self {
            Self::Movie(_) | Self::Jpg { .. } => false,
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
