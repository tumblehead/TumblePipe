//! A decoded frame, in the form the player uploads it: Y'CbCr movie frames
//! stay planar (the shader converts), movie frames with alpha (or in RGB)
//! are 8-bit RGBA code values, EXR frames are scene-linear RGBA halfs.

use std::sync::Arc;

use half::f16;

/// The Y'CbCr -> R'G'B' matrix a movie was encoded with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum YuvMatrix {
    Bt601,
    Bt709,
}

#[derive(Debug, Clone)]
pub enum Pixels {
    /// 8-bit 4:2:0: a full-size luma plane and two half-size chroma planes,
    /// each tightly packed (stride == width).
    Yuv420 {
        y: Vec<u8>,
        u: Vec<u8>,
        v: Vec<u8>,
        matrix: YuvMatrix,
        /// Full range (0-255) rather than video range (16-235).
        full_range: bool,
    },
    /// Display-referred R'G'B'A code values (sRGB, like a playblast's
    /// Y'CbCr once converted), straight (not premultiplied) alpha,
    /// interleaved, rows top to bottom: a movie with alpha or in RGB (PNG
    /// frames in a QuickTime movie).
    Rgba8(Vec<u8>),
    /// Scene-linear RGBA half floats, interleaved, rows top to bottom.
    Rgba16f(Vec<f16>),
}

/// One decoded frame. Cheap to clone: the pixels are shared.
#[derive(Debug, Clone)]
pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub pixels: Arc<Pixels>,
}

impl Frame {
    /// Bytes this frame holds, for the buffer's budget.
    pub fn byte_size(&self) -> usize {
        match &*self.pixels {
            Pixels::Yuv420 { y, u, v, .. } => y.len() + u.len() + v.len(),
            Pixels::Rgba8(data) => data.len(),
            Pixels::Rgba16f(data) => data.len() * 2,
        }
    }
}
