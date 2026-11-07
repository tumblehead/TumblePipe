//! Frame-accurate decoding for the sequence player.
//!
//! Everything is addressed by **frame index** (0-based, display order), never
//! by time: media knows how many frames it has and decodes any one of them
//! exactly. Seconds only appear where the player turns a clock into an index.
//!
//! - [`Media`]: a clip's media, opened and described once, read-only after.
//! - [`MovieIndex`] and [`MovieDecoder`]: a movie file through the vendored
//!   FFmpeg. The index maps a frame index to an exact timestamp and to the
//!   keyframe it must be decoded from; a decoder holds one reader's state.
//! - [`ExrSequence`]: one EXR per frame; any layer and channel of it.
//! - [`composite`]: render layers stacked into one picture ("over").
//! - [`proxy`]: a small picture of a frame, for the timeline.
//! - [`FrameBuffer`]: the decoded frames kept in memory under a byte budget,
//!   which is what makes stepping instant once a frame has been seen.
//! - [`sound`]: a file's audio decoded whole, and the mix of an edit's audio
//!   clips.

mod buffer;
pub mod composite;
mod exr_sequence;
mod frame;
mod media;
mod movie;
pub mod sound;
mod thumbnail;

pub use buffer::{FrameBuffer, FrameKey};
pub use exr_sequence::{ExrHeader, ExrLayer, ExrSequence, ExrView};
pub use frame::{Frame, Pixels, YuvMatrix};
pub use media::Media;
pub use movie::{MovieDecoder, MovieIndex, START_FRAME_KEY, is_rgba_format, parse_start_frame};
pub use thumbnail::{Proxy, Thumbnail, fit, proxy};

/// Why media could not open or decode.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{path}: {message}")]
    Open { path: String, message: String },
    #[error("frame {index} is out of range (the media has {count})")]
    OutOfRange { index: usize, count: usize },
    #[error("decoding frame {index}: {message}")]
    Decode { index: usize, message: String },
    /// A decode stopped because it was no longer wanted.
    #[error("decoding frame {index}: no longer wanted")]
    Cancelled { index: usize },
}

pub type Result<T> = std::result::Result<T, Error>;
