//! A decoded frame, in the form the player uploads it: movie frames stay
//! planar Y'CbCr (the shader converts), EXR channels stay scene-linear halfs.

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
    /// Interleaved half floats, `channels` per pixel (1 = a single channel
    /// shown as grey, 3 = RGB, 4 = RGBA), rows top to bottom.
    Half { data: Vec<f16>, channels: usize },
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
            Pixels::Half { data, .. } => data.len() * 2,
        }
    }
}
