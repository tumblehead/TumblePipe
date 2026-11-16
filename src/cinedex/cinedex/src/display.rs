//! What the editor shows its media through: the OCIO display and view with
//! the exposure, the media opened from a take's description, and the
//! window's theme.

use std::sync::Arc;

use primer::prelude::*;
use th_media::{ExrSequence, JpgSequence, Media, Under};

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

    /// Show through the config's display `index` (keeping the view when it
    /// has it, else its first); whether there is one.
    pub(crate) fn pick_display(&mut self, index: usize) -> bool {
        let Some(display) = self.colour.displays().get(index).cloned() else { return false };
        self.display = display;
        let views = self.colour.views(&self.display);
        if !views.contains(&self.view)
            && let Some(first) = views.first()
        {
            self.view = first.clone();
        }
        true
    }

    /// Show through the display's view `index`; whether there is one.
    pub(crate) fn pick_view(&mut self, index: usize) -> bool {
        let Some(view) = self.colour.views(&self.display).get(index).cloned() else { return false };
        self.view = view;
        true
    }
}

/// The bar under the picture, the editor's and a preview window's alike:
/// `leading` (the editor's AOV and layer choices, else empty), then the
/// OCIO display and view, and the exposure. Out of keyboard focus, so the
/// player's keys keep working after a pick.
pub(crate) fn display_bar<M: 'static>(
    display: &Display,
    leading: Row<M>,
    on_display: fn(usize) -> M,
    on_view: fn(usize) -> M,
    on_exposure: fn(f32) -> M,
) -> Element<M> {
    use crate::look;
    let displays = display.colour.displays();
    let views = display.colour.views(&display.display);
    let d = displays.iter().position(|d| *d == display.display).unwrap_or(0);
    let v = views.iter().position(|v| *v == display.view).unwrap_or(0);
    let label = |w: String| text(w).font_size(13.0).color(look::quiet());
    let controls = leading
        .spacing(8.0)
        .align_y(Vertical::Center)
        .push(label("Display".into()))
        .push(combo_box(Id::new("display"), displays).selected(d).on_select(on_display).width(Length::fixed(170.0)).focusable(false))
        .push(combo_box(Id::new("view"), views).selected(v).on_select(on_view).width(Length::fixed(200.0)).focusable(false))
        .push(space(Length::fixed(16.0), Length::Shrink))
        .push(label(format!("Exposure {:+.1}", display.exposure)))
        .push(
            slider(Id::new("exposure"), -8.0..=8.0, display.exposure, on_exposure)
                .step(0.5)
                .width(Length::fixed(120.0))
                .height(20.0)
                .focusable(false),
        );
    container(controls).padding(Padding::from([6.0, 8.0])).width(Length::Fill).align_x(Horizontal::Center).into()
}

/// A live playblast's frames (`live`) over a take's movie (`under`), which
/// shows where the render has not reached, frame for frame by frame number;
/// `shot_first` numbers the movie's first frame when it records no start.
/// Off the UI thread, like [`open_media`].
pub(crate) fn open_live_over(
    live: &th_playlist::Media,
    under: &th_playlist::Media,
    rate: (i32, i32),
    shot_first: i64,
) -> Result<Arc<Media>, String> {
    let th_playlist::Media::Live { frames } = live else { return Err("not a live playblast".into()) };
    let under = open_media(under, rate)?;
    let first = under.frame_number(0).unwrap_or(shot_first);
    let paths = (0..frames.count()).map(|index| frames.path(index)).collect();
    let sequence = JpgSequence::new(frames.first, frames.step, paths);
    Media::jpg_over(sequence, rate, Under { media: under, first }).map(Arc::new).map_err(|e| e.to_string())
}

/// `media` opened: a movie's index, or an EXR sequence's first header (at
/// `rate`), or a stack's layers, or a live playblast's frames (nothing read:
/// they may not be written yet). Runs off the UI thread; on a file server it
/// takes seconds.
pub(crate) fn open_media(media: &th_playlist::Media, rate: (i32, i32)) -> Result<Arc<Media>, String> {
    let media = match media {
        th_playlist::Media::Movie { path } => Media::movie(path),
        th_playlist::Media::Exr { frames, .. } => {
            let paths = (0..frames.count()).map(|index| frames.path(index)).collect();
            Media::exr(ExrSequence::new(frames.first, frames.step, paths), rate)
        }
        th_playlist::Media::Live { frames } => {
            let paths = (0..frames.count()).map(|index| frames.path(index)).collect();
            Ok(Media::jpg(JpgSequence::new(frames.first, frames.step, paths), rate))
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
