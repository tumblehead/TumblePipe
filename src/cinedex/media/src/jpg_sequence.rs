//! A JPG image sequence: a live playblast's frames (`frame.####.jpg`), one
//! file per frame, written by husk while the player shows them.
//!
//! The frames are display-referred (TumbleView's sRGB output, like a
//! playblast movie's), so they decode to 8-bit RGBA code values and show
//! without the OCIO display transform. A frame not on disk yet is
//! [`Error::Pending`]: the render has not reached it, which is not a fault.

use std::path::PathBuf;
use std::sync::Arc;

use zune_jpeg::JpegDecoder;
use zune_jpeg::zune_core::bytestream::ZCursor;
use zune_jpeg::zune_core::colorspace::ColorSpace;
use zune_jpeg::zune_core::options::DecoderOptions;

use crate::frame::{Frame, Pixels};
use crate::{Error, Result};

/// The frame files of a sequence, in playback order: index `i` is frame
/// number `first + i * step`.
#[derive(Debug, Clone)]
pub struct JpgSequence {
    first: i64,
    step: i64,
    frames: Vec<PathBuf>,
}

impl JpgSequence {
    pub fn new(first: i64, step: i64, frames: Vec<PathBuf>) -> Self {
        Self { first, step, frames }
    }

    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    /// The frame number of index `index`.
    pub fn frame_number(&self, index: usize) -> i64 {
        self.first + index as i64 * self.step
    }

    /// What it is read from, for messages: its first frame.
    pub fn describe(&self) -> String {
        self.frames.first().map_or_else(String::new, |p| p.display().to_string())
    }

    /// Frame `index`, as straight RGBA code values (opaque). Pending when
    /// its file is not there yet.
    pub fn decode(&self, index: usize) -> Result<Frame> {
        let path = self.frames.get(index).ok_or(Error::OutOfRange { index, count: self.frames.len() })?;
        let bytes = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(Error::Pending { index }),
            Err(e) => return Err(Error::Decode { index, message: format!("{}: {e}", path.display()) }),
        };
        decode_jpg(&bytes).map_err(|message| Error::Decode { index, message: format!("{}: {message}", path.display()) })
    }
}

/// The marker every complete JPG ends with (end of image). Inside the
/// compressed data a 0xFF byte is always escaped, so a file without it at
/// its end is one husk has not finished writing.
const END_OF_IMAGE: [u8; 2] = [0xFF, 0xD9];

/// A JPG file's bytes as an opaque RGBA frame. A file cut short (husk still
/// writing it) is refused: the decoder would fill its missing rows in.
pub fn decode_jpg(bytes: &[u8]) -> std::result::Result<Frame, String> {
    if !bytes.ends_with(&END_OF_IMAGE) {
        return Err("the file is not complete (it has no end-of-image marker)".into());
    }
    let options = DecoderOptions::default().jpeg_set_out_colorspace(ColorSpace::RGBA);
    let mut decoder = JpegDecoder::new_with_options(ZCursor::new(bytes), options);
    let pixels = decoder.decode().map_err(|e| e.to_string())?;
    let info = decoder.info().ok_or("no image information")?;
    let (width, height) = (u32::from(info.width), u32::from(info.height));
    if pixels.len() != width as usize * height as usize * 4 {
        return Err(format!("{} bytes for {width}x{height} RGBA", pixels.len()));
    }
    Ok(Frame { width, height, pixels: Arc::new(Pixels::Rgba8(pixels)) })
}
