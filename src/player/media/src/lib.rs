//! Frame-accurate decoding for the sequence player.
//!
//! Everything is addressed by **frame index** (0-based, display order), never
//! by time: a source knows how many frames it has and decodes any one of them
//! exactly. Seconds only appear where the player turns a clock into an index.
//!
//! - [`MovieSource`]: a movie file through the vendored FFmpeg. It indexes the
//!   file's packets on open, so a frame index maps to an exact timestamp and
//!   to the keyframe it must be decoded from.
//! - [`ExrSequence`]: one EXR per frame; any layer and channel of it.
//! - [`Source`]: either, behind the one interface the player decodes through.
//! - [`thumbnail`]: a small display-ready picture of a frame, for the timeline.
//! - [`FrameBuffer`]: the decoded frames kept in memory under a byte budget,
//!   which is what makes stepping instant once a frame has been seen.

mod buffer;
mod exr_sequence;
mod frame;
mod movie;
mod source;
mod thumbnail;

pub use buffer::{FrameBuffer, FrameKey};
pub use exr_sequence::{ExrLayer, ExrSequence, ExrView, sequence_frames, split_frame_name};
pub use frame::{Frame, Pixels, YuvMatrix};
pub use movie::MovieSource;
pub use source::{DEFAULT_RATE, Source};
pub use thumbnail::{Thumbnail, fit, srgb_encode, thumbnail};

/// Why a source could not open or decode.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{path}: {message}")]
    Open { path: String, message: String },
    #[error("frame {index} is out of range (the source has {count})")]
    OutOfRange { index: usize, count: usize },
    #[error("decoding frame {index}: {message}")]
    Decode { index: usize, message: String },
    /// A decode stopped because it was no longer wanted (see
    /// [`Source::decode_while`]).
    #[error("decoding frame {index}: no longer wanted")]
    Cancelled { index: usize },
}

pub type Result<T> = std::result::Result<T, Error>;
