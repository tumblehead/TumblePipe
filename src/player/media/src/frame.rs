//! A decoded frame, in the form the player uploads it: movie frames stay
//! planar Y'CbCr (the shader converts), EXR frames are scene-linear RGBA
//! halfs.

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
            Pixels::Rgba16f(data) => data.len() * 2,
        }
    }
}
