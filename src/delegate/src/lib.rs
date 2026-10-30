//! Rendering core of `hdThPlayblast`, TumblePipe's playblast render delegate.
//!
//! The C++ shim (`cpp/delegate.cpp`) is the Hydra side: it registers the
//! renderer plugin, pulls meshes, instancers, materials and textures out of
//! Hydra, and owns the render buffers husk binds as AOVs. It hands this core
//! a [`ffi::ThFrame`] (camera, size) and a list of [`ffi::ThMeshView`]s
//! (borrowed geometry), and gets back colour and depth. Everything that
//! decides what a pixel looks like lives on this side of the C ABI.
//!
//! The look is Houdini's smooth-shaded viewport: a grey backdrop gradient,
//! a headlight, display colours or the material's base colour/texture.
//! Normals are the mesh's own, else per face for a polygonal mesh (the
//! shim's `faceted`) and smoothed per point for the rest, as Storm does.
//! It runs on the CPU, so a farm node needs no GPU, GL context or display;
//! built with the `gpu` feature it can draw the same picture with wgpu
//! instead, on request (`TH_PLAYBLAST_GPU=1`, see `ffi::render`).
//!
//! Every exported function runs under `catch_unwind` (`ffi_guard`): a panic
//! becomes an error code the shim reports, never an unwind into C++.

pub mod ffi;
#[cfg(feature = "gpu")]
pub mod gpu;
pub mod math;
pub mod raster;
pub mod texture;

#[cfg(test)]
mod props;

use std::panic::{self, AssertUnwindSafe};

/// Success.
pub const TH_OK: i32 = 0;
/// A null pointer or a zero-sized image was passed.
pub const TH_ERR_ARGS: i32 = 1;
/// The core panicked; the buffer contents are undefined.
pub const TH_ERR_PANIC: i32 = 2;

pub(crate) fn ffi_guard(body: impl FnOnce() -> i32) -> i32 {
    panic::catch_unwind(AssertUnwindSafe(body)).unwrap_or(TH_ERR_PANIC)
}
