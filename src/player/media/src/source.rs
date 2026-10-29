//! A clip's media, whatever it is: the one interface the player's engine
//! decodes through.

use std::path::Path;
use std::sync::Mutex;

use crate::exr_sequence::{ExrLayer, ExrSequence, ExrView};
use crate::frame::Frame;
use crate::movie::MovieSource;
use crate::{Error, Result};

/// The rate a sequence plays at when nothing says otherwise.
pub const DEFAULT_RATE: (i32, i32) = (24, 1);

pub enum Source {
    /// A movie decodes forward from a keyframe, so one decoder serves one
    /// decode at a time.
    Movie(Mutex<MovieSource>),
    Exr {
        sequence: ExrSequence,
        layers: Vec<ExrLayer>,
        rate: (i32, i32),
        path: String,
        /// The colour space its frames say they are in.
        colour_space: Option<String>,
    },
}

impl std::fmt::Debug for Source {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Source").field("path", &self.path()).field("frames", &self.frame_count()).finish()
    }
}

fn is_exr(path: &Path) -> bool {
    path.extension().is_some_and(|e| e.eq_ignore_ascii_case("exr"))
}

impl Source {
    /// Open `path`: a movie, an EXR frame (its whole sequence), or a folder
    /// holding one EXR sequence. `rate` overrides the media's own.
    pub fn open(path: &str, rate: Option<(i32, i32)>) -> Result<Self> {
        let p = Path::new(path);
        let exr_frame = if p.is_dir() {
            let first = std::fs::read_dir(p)
                .map_err(|e| Error::Open { path: path.into(), message: e.to_string() })?
                .filter_map(|e| Some(e.ok()?.path()))
                .filter(|f| is_exr(f))
                .min();
            Some(first.ok_or_else(|| Error::Open { path: path.into(), message: "no EXR frames in the folder".into() })?)
        } else {
            is_exr(p).then(|| p.to_owned())
        };
        match exr_frame {
            Some(frame) => Self::exr(ExrSequence::scan(&frame)?, path, rate),
            None => {
                let movie = MovieSource::open(path)?;
                let _ = rate; // A movie's timestamps are its rate; it plays at them.
                Ok(Self::Movie(Mutex::new(movie)))
            }
        }
    }

    /// An EXR sequence as a source, named `path`.
    pub fn exr(sequence: ExrSequence, path: &str, rate: Option<(i32, i32)>) -> Result<Self> {
        let layers = sequence.layers()?;
        let rate = match rate {
            Some(rate) => rate,
            None => sequence.rate()?.unwrap_or(DEFAULT_RATE),
        };
        let colour_space = sequence.colour_space()?;
        Ok(Self::Exr { sequence, layers, rate, path: path.to_owned(), colour_space })
    }

    pub fn path(&self) -> String {
        match self {
            Self::Movie(m) => m.lock().unwrap().path().to_owned(),
            Self::Exr { path, .. } => path.clone(),
        }
    }

    pub fn frame_count(&self) -> usize {
        match self {
            Self::Movie(m) => m.lock().unwrap().frame_count(),
            Self::Exr { sequence, .. } => sequence.frame_count(),
        }
    }

    /// The frame number of index 0: an EXR sequence's own; None for a movie,
    /// whose frames carry no numbers.
    pub fn first_frame(&self) -> Option<i64> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { sequence, .. } => Some(sequence.first_frame()),
        }
    }

    pub fn rate(&self) -> (i32, i32) {
        match self {
            Self::Movie(m) => m.lock().unwrap().rate(),
            Self::Exr { rate, .. } => *rate,
        }
    }

    /// The EXR layers to pick a view from; empty for a movie.
    pub fn layers(&self) -> &[ExrLayer] {
        match self {
            Self::Movie(_) => &[],
            Self::Exr { layers, .. } => layers,
        }
    }

    /// The view the source opens on (meaningless for a movie).
    pub fn default_view(&self) -> ExrView {
        ExrSequence::default_view(self.layers())
    }

    /// The colour space an EXR source says it is in; None for a movie
    /// (display-referred) or an EXR that does not say.
    pub fn colour_space(&self) -> Option<&str> {
        match self {
            Self::Movie(_) => None,
            Self::Exr { colour_space, .. } => colour_space.as_deref(),
        }
    }

    /// True when its frames are scene-linear (shown through the display
    /// transform) rather than display-referred.
    pub fn is_scene_linear(&self) -> bool {
        matches!(self, Self::Exr { .. })
    }

    /// True when decodes of this source can run side by side.
    pub fn is_parallel(&self) -> bool {
        matches!(self, Self::Exr { .. })
    }

    /// Decode frame `index` (showing `view` of an EXR). `keep` gets every
    /// frame decoded on the way, the target included.
    pub fn decode(&self, index: usize, view: &ExrView, keep: impl FnMut(usize, &Frame)) -> Result<Frame> {
        self.decode_while(index, view, keep, || true)
    }

    /// [`decode`](Self::decode), stopping with [`Error::Cancelled`] once
    /// `wanted` says the frame is no longer wanted. An EXR frame asks between
    /// the pieces of its file as it reads them (a frame read whole from a
    /// file server can take seconds); a movie decodes on to its target.
    pub fn decode_while(
        &self,
        index: usize,
        view: &ExrView,
        mut keep: impl FnMut(usize, &Frame),
        wanted: impl Fn() -> bool,
    ) -> Result<Frame> {
        match self {
            Self::Movie(m) => m.lock().unwrap().decode(index, keep),
            Self::Exr { sequence, .. } => {
                let frame = sequence.decode_while(index, view, &wanted)?;
                keep(index, &frame);
                Ok(frame)
            }
        }
    }
}
