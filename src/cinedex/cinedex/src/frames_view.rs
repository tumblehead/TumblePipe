//! The window `--frames` opens (#75): preview frames as stills, one at a
//! time, through the project's OCIO display (`previews` says which).
//!
//! Preview frames are a few frames of a render still going (first, middle,
//! last), there to catch a mistake before the full range renders, so they
//! are stepped through, not played: Left/Right (or the frame buttons) move
//! between them, Home/End go to the first and last. Each is decoded off the
//! UI thread when first shown, and kept.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use primer::keyboard::{Key, Modifiers, Named};
use primer::prelude::*;
use th_media::{ExrSequence, Frame, JpgSequence, Media};

use crate::colour::Colour;
use crate::display::theme;
use crate::look;
use crate::previews::Stills;
use crate::video::{Picture, Video};

/// A still decoded: its frame and, for a scene-linear one, its colour space.
#[derive(Clone)]
pub(crate) struct Decoded {
    frame: Frame,
    colour_space: Option<String>,
}

#[derive(Clone)]
pub(crate) enum Message {
    Start,
    Show(usize),
    Step(i64),
    Decoded(usize, Result<Decoded, String>),
}

/// The still shown after a step of `by` from `at` among `count`, clamped.
pub(crate) fn stepped(at: usize, by: i64, count: usize) -> usize {
    if count == 0 {
        return 0;
    }
    (at as i64).saturating_add(by).clamp(0, count as i64 - 1) as usize
}

/// What a key does here: a step, or None.
pub(crate) fn key_step(key: &Key) -> Option<i64> {
    match key {
        Key::Named(Named::ArrowLeft) => Some(-1),
        Key::Named(Named::ArrowRight) => Some(1),
        Key::Named(Named::Home) => Some(i64::MIN / 2),
        Key::Named(Named::End) => Some(i64::MAX / 2),
        _ => None,
    }
}

/// Decode the still at `path`: an EXR's beauty (its first layer else), or a JPG.
fn decode(path: PathBuf) -> Result<Decoded, String> {
    let is_jpg = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("jpg") || e.eq_ignore_ascii_case("jpeg"));
    if is_jpg {
        let frame = JpgSequence::new(0, 1, vec![path]).decode(0).map_err(|e| e.to_string())?;
        return Ok(Decoded { frame, colour_space: None });
    }
    let sequence = ExrSequence::new(0, 1, vec![path]);
    let media = Media::exr(sequence.clone(), (24, 1)).map_err(|e| e.to_string())?;
    let view = media.default_view();
    let frame = sequence.decode(0, &view).map_err(|e| e.to_string())?;
    Ok(Decoded { frame, colour_space: media.colour_space().map(str::to_owned) })
}

pub(crate) struct FramesView {
    stills: Stills,
    colour: Colour,
    display: String,
    view: String,
    at: usize,
    decoded: HashMap<usize, Result<Picture, String>>,
    asked: HashSet<usize>,
    next_id: u64,
}

impl FramesView {
    pub(crate) fn new(stills: Stills, colour: Colour) -> Self {
        let (display, view) = colour.default();
        FramesView { stills, colour, display, view, at: 0, decoded: HashMap::new(), asked: Default::default(), next_id: 1 }
    }

    /// Decode the still shown, unless it is decoded or being decoded.
    fn ask(&mut self) -> Command<Message> {
        let at = self.at;
        let Some(still) = self.stills.frames.get(at) else { return Command::none() };
        if !self.asked.insert(at) {
            return Command::none();
        }
        let path = still.path.clone();
        Command::perform_blocking(move || decode(path), move |r| Message::Decoded(at, r))
    }

    /// The header: which preview, which frame, how far along.
    fn caption(&self) -> String {
        let Some(still) = self.stills.frames.get(self.at) else { return String::new() };
        let frame = still
            .number
            .map_or_else(|| still.path.file_name().unwrap_or_default().to_string_lossy().into_owned(), |n| format!("frame {n}"));
        format!("{} · {frame} ({} of {}) · {} / {}", still.group, self.at + 1, self.stills.frames.len(), self.display, self.view)
    }
}

impl Application for FramesView {
    type Message = Message;

    fn windows(&self) -> Vec<window::Window> {
        vec![
            window::Window::new(window::Id::MAIN)
                .title(format!("Cinedex · Preview frames · {}", self.stills.title))
                .app_id(crate::APP_ID)
                .size(Size::new(1280.0, 820.0))
                .theme(theme()),
        ]
    }

    fn update(&mut self, message: Message) -> Command<Message> {
        match message {
            Message::Start => self.ask(),
            Message::Show(at) => {
                self.at = stepped(at, 0, self.stills.frames.len());
                self.ask()
            }
            Message::Step(by) => {
                self.at = stepped(self.at, by, self.stills.frames.len());
                self.ask()
            }
            Message::Decoded(at, result) => {
                let picture = result.and_then(|d| {
                    let lut = d.colour_space.as_deref().map(|cs| self.colour.lut(cs, &self.display, &self.view)).transpose()?;
                    self.next_id += 1;
                    Ok(Picture { id: self.next_id, frame: d.frame, lut })
                });
                if let Err(e) = &picture {
                    eprintln!("cinedex: {}: {e}", self.stills.frames[at].path.display());
                }
                self.decoded.insert(at, picture);
                Command::none()
            }
        }
    }

    fn on_key(&self, _window: window::Id, key: Key, _modifiers: Modifiers) -> Option<Message> {
        key_step(&key).map(Message::Step)
    }

    fn subscription(&self) -> Subscription<Message> {
        primer::subscription::channel(primer::subscription::hash_id(&"cinedex frames start"), 1, move |sender| async move {
            let mut sender = sender;
            let _ = sender.try_send(Message::Start);
            futures::future::pending::<()>().await;
        })
    }

    fn view(&self, _window: window::Id) -> Element<Message> {
        let shown = self.decoded.get(&self.at);
        let picture = shown.and_then(|r| r.as_ref().ok()).cloned();
        let mut layers = stack()
            .push(shader(Video { picture, exposure: 0.0 }).width(Length::Fill).height(Length::Fill).redraw(false))
            .width(Length::Fill)
            .height(Length::Fill);
        let card = match shown {
            None => Some(("Reading…".to_owned(), look::quiet())),
            Some(Err(e)) => Some((format!("Cannot show it: {e}"), look::error())),
            Some(Ok(_)) => None,
        };
        if let Some((words, colour)) = card {
            layers = layers.push(
                container(text(words).font_size(15.0).color(colour))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .align_x(Horizontal::Center)
                    .align_y(Vertical::Center),
            );
        }
        let mut strip = row().spacing(6.0);
        for (i, still) in self.stills.frames.iter().enumerate() {
            let label = still.number.map_or_else(|| (i + 1).to_string(), |n| n.to_string());
            let mut b = look::button(Id::new(format!("still {i}")), text(label).font_size(12.0))
                .padding(Padding::from([3.0, 8.0]))
                .focusable(false);
            if i == self.at {
                b = b.intent(Intent::Primary);
            }
            strip = strip.push(b.on_press(move || Message::Show(i)));
        }
        let header = container(text(self.caption()).font_size(13.0).color(look::text()))
            .padding(Padding::from([6.0, 14.0]))
            .width(Length::Fill)
            .background(look::panel());
        let footer = container(strip).padding(Padding::from([6.0, 14.0])).width(Length::Fill).background(look::panel());
        column().push(header).push(layers).push(footer).width(Length::Fill).height(Length::Fill).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// However far it steps, it lands on a still; a step of 0 stays,
        /// and Home/End reach the ends.
        #[test]
        fn steps_land_on_a_still(at in 0usize..50, by in -100i64..100, count in 1usize..50) {
            let at = at.min(count - 1);
            let to = stepped(at, by, count);
            prop_assert!(to < count);
            prop_assert_eq!(to as i64, (at as i64 + by).clamp(0, count as i64 - 1));
            prop_assert_eq!(stepped(at, key_step(&Key::Named(Named::Home)).unwrap(), count), 0);
            prop_assert_eq!(stepped(at, key_step(&Key::Named(Named::End)).unwrap(), count), count - 1);
        }
    }

    #[test]
    fn arrows_step_and_other_keys_do_nothing() {
        assert_eq!(key_step(&Key::Named(Named::ArrowLeft)), Some(-1));
        assert_eq!(key_step(&Key::Named(Named::ArrowRight)), Some(1));
        assert_eq!(key_step(&Key::Character('a')), None);
        assert_eq!(stepped(0, 5, 0), 0);
    }

    /// An EXR still decodes to its scene-linear frame and its colour space;
    /// one that says none cannot be shown right, and says so.
    #[test]
    fn exr_stills_decode_with_their_colour_space() {
        use exr::prelude::*;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("beauty.1001.exr");
        let channels = SpecificChannels::rgba(|_| (0.5f32, 0.25f32, 0.125f32, 1.0f32));
        let mut image = Image::from_channels((4, 2), channels);
        image.attributes.other.insert(Text::from("oiio:ColorSpace"), AttributeValue::Text(Text::from("ACEScg")));
        image.write().to_file(&path).unwrap();
        let decoded = decode(path).unwrap();
        assert_eq!((decoded.frame.width, decoded.frame.height), (4, 2));
        assert_eq!(decoded.colour_space.as_deref(), Some("ACEScg"));
        let bare = dir.path().join("bare.exr");
        Image::from_channels((2, 2), SpecificChannels::rgba(|_| (0.0f32, 0.0f32, 0.0f32, 1.0f32))).write().to_file(&bare).unwrap();
        assert!(decode(bare).err().is_some_and(|e| e.contains("colour space")));
    }
}
