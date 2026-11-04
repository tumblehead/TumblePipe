//! th_player: plays shots back to back, frame-accurately.
//!
//! `th_player < playlist.json` plays the TumblePipe playlist on standard
//! input (see the `th_playlist` crate), showing its renders through the OCIO
//! config it names. A playlist that does not read, or a config that does not
//! open, leaves nothing to play: the window says why.
//!
//! Under the video the track shows the shots as clips (see `app` and
//! `track`). Keys: Left/Right step a frame, Alt+Left/Right a second (at the
//! rate of the shot under the playhead), Ctrl+Left/Right jump to the
//! previous/next shot, Home/End the first/last frame, Space play/pause.
//! Up/Down show the next newer/older version, Ctrl+Up/Down (Cmd on macOS) the
//! department upstream/downstream, Shift+Up/Down the playblast/the render;
//! Ctrl+A selects every shot and Escape clears the selection; B shows or
//! hides the burn-ins (see `burnin`); 1-9 show or hide layer N (bottom = 1)
//! of a stack take (see `track::LayerRows`). The controls never take the keyboard
//! from these. Playback shows every frame: when the next one is not decoded
//! yet it waits rather than skips.

mod app;
mod burnin;
mod colour;
mod engine;
mod session;
mod take_menu;
mod track;
mod video;

use primer::prelude::*;
use th_playlist::Playlist;

use app::Player;
use colour::Colour;

/// The window shown when there is nothing to play, saying why.
struct Failed {
    message: String,
}

impl Application for Failed {
    type Message = ();

    fn windows(&self) -> Vec<window::Window> {
        vec![window::Window::new(window::Id::MAIN).title("TumblePipe Player").size(Size::new(720.0, 240.0)).theme(app::theme())]
    }

    fn update(&mut self, _message: ()) -> Command<()> {
        Command::none()
    }

    fn view(&self, _window: window::Id) -> Element<()> {
        let words = text(format!("Nothing to play: {}", self.message)).font_size(14.0).color(Color::from_rgb8(235, 120, 105));
        container(words).padding(16.0).width(Length::Fill).height(Length::Fill).align_x(Horizontal::Center).align_y(Vertical::Center).into()
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::args().len() > 1 {
        eprintln!("usage: th_player < playlist.json (TumblePipe writes the playlist on standard input)");
        std::process::exit(2);
    }
    let opened = Playlist::read_stdin().and_then(|playlist| {
        let colour = Colour::open(&playlist.ocio).map_err(|e| format!("the OCIO config: {e}"))?;
        Ok((playlist, colour))
    });
    match opened {
        Ok((playlist, colour)) => run(Player::new(playlist, colour), Setup::default())?,
        Err(message) => {
            eprintln!("th_player: {message}");
            run(Failed { message }, Setup::default())?;
        }
    }
    Ok(())
}
