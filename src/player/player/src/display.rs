//! What the editor shows its media through: the OCIO display and view with
//! the exposure, the media opened from a take's description, and the
//! window's theme.

use std::sync::Arc;

use primer::prelude::*;
use th_media::{ExrSequence, Media};

use crate::colour::{Colour, Lut};

/// The display side: the OCIO config and what it shows.
pub(crate) struct Display {
    pub(crate) colour: Colour,
    pub(crate) display: String,
    pub(crate) view: String,
    pub(crate) exposure: f32,
}

impl Display {
    /// The display transform for `media` as the display and view show it.
    pub(crate) fn lut(&mut self, media: &Media) -> Result<Option<Arc<Lut>>, String> {
        media.colour_space().map(|cs| self.colour.lut(cs, &self.display, &self.view)).transpose()
    }
}

/// `media` opened: a movie's index, or an EXR sequence's first header (at
/// `rate`), or a stack's layers. Runs off the UI thread; on a file server it
/// takes seconds.
pub(crate) fn open_media(media: &th_playlist::Media, rate: (i32, i32)) -> Result<Arc<Media>, String> {
    let media = match media {
        th_playlist::Media::Movie { path } => Media::movie(path),
        th_playlist::Media::Exr { frames, .. } => {
            let paths = (0..frames.count()).map(|index| frames.path(index)).collect();
            Media::exr(ExrSequence::new(frames.first, frames.step, paths), rate)
        }
        // Every layer at once: each is a round trip or more to the file server.
        th_playlist::Media::Stack { layers } => {
            let opened: Vec<Result<Arc<Media>, String>> = std::thread::scope(|scope| {
                let opening: Vec<_> = layers.iter().map(|layer| scope.spawn(move || open_media(&layer.media, rate))).collect();
                opening.into_iter().map(|t| t.join().unwrap_or_else(|_| Err("opening the layer panicked".into()))).collect()
            });
            let mut stack = Vec::with_capacity(layers.len());
            for (layer, media) in layers.iter().zip(opened) {
                stack.push((layer.name.clone(), media.map_err(|e| format!("layer {}: {e}", layer.name))?));
            }
            Media::stack(stack)
        }
    };
    media.map(Arc::new).map_err(|e| e.to_string())
}

/// Neutral greys around the image (see [`crate::look`]): a tinted surround
/// shifts how its colours read. `primary` marks what is on (a ticked
/// checkbox, a slider's fill, the selected tab, the highlighted menu row):
/// clear of the surface, and dark enough that white words on it read;
/// focus is the accent. Corners are nearly square,
/// like the picture they sit around.
pub fn theme() -> Theme {
    use crate::look;
    Theme {
        background: look::surround(),
        surface: look::panel(),
        text: look::text(),
        primary: look::on(),
        secondary: Color::from_rgb8(54, 54, 54),
        border: look::rule(),
        focus: look::now(),
        radius: 2.0,
        control_radius: 2.0,
        ..Theme::dark()
    }
}
