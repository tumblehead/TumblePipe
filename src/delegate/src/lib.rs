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
//! display colours or the material's base colour/texture, lit by the
//! scene's own lights ([`lights`]; lights fixed to the camera in a scene
//! without any), with three tiers of screen-space ambient occlusion.
//! It is drawn the way a game engine draws: a rasteriser fills a render
//! buffer (colour, normal, depth) and a post pass ([`composite`]) occludes,
//! lights and filters it; [`composite::Shading`] picks lit, clay or flat
//! colour. A see-through mesh (opacity under 1, from its material or its
//! display opacity) is drawn after the opaque ones and blended over them
//! ([`raster::Surface`]). Normals are the mesh's own, else smoothed: per point for a subdivision
//! surface, and with Houdini's 60 degree cusp for a polygonal mesh (the
//! shim's `polygonal`), so curved surfaces are smooth and a box stays a box.
//! It runs on the CPU, so a farm node needs no GPU, GL context or display;
//! built with the `gpu` feature it can draw the same picture with wgpu
//! instead, on request (`ThFrame::gpu`, see `ffi::render`): in Houdini's
//! viewer by default, elsewhere when `th:gpu` or `TH_PLAYBLAST_GPU=1` asks.
//!
//! Every exported function runs under `catch_unwind` (`ffi_guard`): a panic
//! becomes an error code the shim reports, never an unwind into C++.

pub mod blur;
mod cancel;
pub mod composite;
pub mod curves;
pub mod ffi;
#[cfg(feature = "gpu")]
pub mod gpu;
pub mod lights;
pub mod math;
mod normals;
mod pool;
pub mod raster;
pub mod texture;
pub mod triangulate;

#[cfg(test)]
mod props;
#[cfg(test)]
mod props_curves;

use std::panic::{self, AssertUnwindSafe};

/// Success.
pub const TH_OK: i32 = 0;
/// A null pointer or a zero-sized image was passed.
pub const TH_ERR_ARGS: i32 = 1;
/// The core panicked; the buffer contents are undefined.
pub const TH_ERR_PANIC: i32 = 2;
/// The caller cancelled the frame (`th_render_ids`); nothing was copied out.
pub const TH_CANCELLED: i32 = 3;

pub(crate) fn ffi_guard(body: impl FnOnce() -> i32) -> i32 {
    panic::catch_unwind(AssertUnwindSafe(body)).unwrap_or(TH_ERR_PANIC)
}
