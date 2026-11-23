//! The transport bar under the picture: go to start, previous edit point,
//! a frame back, play or pause, a frame forward, next edit point, go to
//! end, a scrub bar over the cut and where the playhead is.
//!
//! It has no actions of its own. Each button is a row of the Playback menu
//! (`menu_bar`), found by its label: it sends that row's message, is greyed
//! when the row is, and its tooltip is the row's words and its shortcut. So
//! a button, its menu row and its key cannot come apart, and the tests hold
//! the three to each other. The scrub bar sends what a click on the ruler
//! does (`Message::Seek`).
//!
//! No loop button: the editor loops only what a Live view asks it to (the
//! chip in the toolbar ends that).

use primer::keyboard::{Key, Modifiers, Named};
use primer::prelude::*;
use th_edit::document::Frame as EditFrame;

use super::menu_bar::{self, Entry, State};
use super::{Editor, Message};
use crate::editing;
use crate::glyph::{self, Glyph};
use crate::look;

/// The bar's height: the picture gives up this much.
pub(super) const HEIGHT: f32 = 28.0;

/// One button of the bar.
#[derive(Debug, Clone)]
pub(super) struct Control {
    /// Its id's name.
    pub name: &'static str,
    pub glyph: Glyph,
    /// What it does and its key, "Next Frame (Right)".
    pub tip: String,
    /// What a press sends: its menu row's message.
    pub message: Message,
    /// Whether a press does anything: whether its menu row is enabled.
    pub enabled: bool,
    /// The key that does the same: what the tooltip names (read by the
    /// tests, which press it).
    #[cfg_attr(not(test), allow(dead_code))]
    pub keys: (Key, Modifiers),
}

impl Control {
    /// What a press on it sends: nothing while its menu row is greyed.
    pub(super) fn pressed(&self) -> Option<Message> {
        self.enabled.then(|| self.message.clone())
    }
}

/// The buttons, left to right, each with the Playback menu row it stands
/// for (play or pause by whichever the menu offers now).
const ROWS: [(&str, Glyph, &[&str]); 7] = [
    ("start", Glyph::ToStart, &["Go to &Start"]),
    ("previous cut", Glyph::PrevCut, &["Previous Edit Point"]),
    ("back", Glyph::StepBack, &["Previous Frame"]),
    ("play", Glyph::Play, &["&Play", "&Pause"]),
    ("forward", Glyph::StepForward, &["Next Frame"]),
    ("next cut", Glyph::NextCut, &["Next Edit Point"]),
    ("end", Glyph::ToEnd, &["Go to &End"]),
];

/// The bar's buttons in `state`.
pub(super) fn controls(state: &State) -> Vec<Control> {
    let playback: Vec<Entry> = menu_bar::headings(state).into_iter().filter(|h| h.label == "&Playback").flat_map(|h| h.entries).collect();
    let mut rows = playback;
    let mut out = Vec::new();
    for (name, glyph, labels) in ROWS {
        let Some(at) = rows.iter().position(|entry| matches!(entry, Entry::Item { label, .. } if labels.contains(label))) else { continue };
        let Entry::Item { label, message, enabled, keys: Some(keys) } = rows.remove(at) else { continue };
        let glyph = if label == "&Pause" { Glyph::Pause } else { glyph };
        let mut tip = format!("{} ({}", label.replace('&', ""), keys_text(keys.0.clone(), keys.1));
        // Play and pause have a second key, which only this tooltip can name.
        if matches!(message, Message::TogglePlay) {
            tip.push_str(&format!(" or {}", keys_text(menu_bar::ALSO_PLAYS.0.clone(), menu_bar::ALSO_PLAYS.1)));
        }
        tip.push(')');
        out.push(Control { name, glyph, tip, message, enabled, keys });
    }
    out
}

/// A shortcut as the menu writes it: the held keys, then the key
/// ("Ctrl+Left", "Space").
pub(super) fn keys_text(key: Key, modifiers: Modifiers) -> String {
    let key = match key {
        Key::Character(' ') => "Space".to_owned(),
        Key::Character(c) => c.to_uppercase().collect(),
        Key::Named(Named::ArrowLeft) => "Left".to_owned(),
        Key::Named(Named::ArrowRight) => "Right".to_owned(),
        Key::Named(Named::ArrowUp) => "Up".to_owned(),
        Key::Named(Named::ArrowDown) => "Down".to_owned(),
        Key::Named(Named::PageUp) => "Page Up".to_owned(),
        Key::Named(Named::PageDown) => "Page Down".to_owned(),
        Key::Named(named) => format!("{named:?}"),
        Key::Unidentified => "?".to_owned(),
    };
    let logo = if cfg!(target_os = "macos") { "Cmd" } else { "Super" };
    let held = [(modifiers.control, "Ctrl"), (modifiers.alt, "Alt"), (modifiers.shift, "Shift"), (modifiers.logo, logo)];
    let mut parts: Vec<String> = held.into_iter().filter(|(on, _)| *on).map(|(_, name)| name.to_owned()).collect();
    parts.push(key);
    parts.join("+")
}

/// Where the playhead is in a cut of `end` frames: the edit frame it is on
/// (from 0; `end` itself where Go to End parks) and the cut's length.
pub(super) fn frame_text(position: usize, end: usize) -> String {
    format!("{position} / {end}")
}

/// The frame the scrub bar's `value` asks for, in a cut of `end` frames.
pub(super) fn scrub_frame(value: f32, end: usize) -> usize {
    if value.is_nan() { 0 } else { (value.round().max(0.0) as usize).min(end) }
}

impl Editor {
    /// The transport bar, [`HEIGHT`] tall under the picture. Out of
    /// keyboard focus, like every control around the picture: after a
    /// click on it Space still plays.
    pub(super) fn transport_bar(&self) -> Element<Message> {
        let state = self.menu_state();
        let mut buttons = row().spacing(2.0).align_y(Vertical::Center);
        for c in controls(&state) {
            buttons = buttons.push(glyph::control(c.name, c.glyph, &c.tip, c.pressed()));
        }
        let end = self.end();
        // An empty cut has nowhere to scrub: the bar is there, still.
        let scrub = slider(Id::new("transport scrub"), 0.0..=end.max(1) as f32, self.position.min(end) as f32, move |v| {
            Message::Seek(scrub_frame(v, end))
        })
        .step(1.0)
        .width(Length::Fill)
        .height(16.0)
        .focusable(false);
        let scrub = tooltip(Id::new("transport scrub tip"), scrub, "Drag to move the playhead through the cut");
        let timecode = text(editing::timecode(self.position as EditFrame, &self.doc.edit)).font_size(13.0).color(look::text());
        let frame = text(frame_text(self.position, end)).font_size(13.0).color(look::quiet());
        let readout = tooltip(
            Id::new("transport readout tip"),
            row().spacing(10.0).align_y(Vertical::Center).push(timecode).push(frame),
            "The playhead's timecode, and its edit frame of the cut's length",
        );
        let bar = row().spacing(12.0).align_y(Vertical::Center).push(buttons).push(scrub).push(readout);
        container(bar)
            .padding(Padding::from([0.0, 8.0]))
            .width(Length::Fill)
            .height(Length::fixed(HEIGHT))
            .align_y(Vertical::Center)
            .background(look::panel())
            .into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::TakeKind;

    fn state() -> impl Strategy<Value = State> {
        let show = proptest::option::of(prop_oneof![Just(TakeKind::Render), Just(TakeKind::Playblast)]);
        (any::<[bool; 9]>(), proptest::option::of(any::<bool>()), 0usize..10_000, show).prop_map(|(b, master, end, show)| State {
            free: b[0],
            writable: b[0] && b[1],
            named: b[2],
            master,
            picked: b[3],
            playing: b[4],
            burnins: b[5],
            audio_hidden: b[6],
            focused: b[6] || b[7],
            flipped: b[8],
            sync_ranges: b[4],
            reviewing: b[1],
            eraser: b[2],
            reviewable: b[3],
            marks_shown: b[5],
            show,
            end,
        })
    }

    proptest! {
        /// The bar is the seven buttons in order, whatever the editor is
        /// doing, an empty cut included (where Go to Start and Go to End
        /// send the same frame); the middle one shows pause exactly while
        /// playing, and play otherwise.
        #[test]
        fn the_bar_is_seven_buttons_and_play_shows_the_state(state in state(), empty in any::<bool>()) {
            let state = State { end: if empty { 0 } else { state.end }, ..state };
            let bar = controls(&state);
            let glyphs: Vec<Glyph> = bar.iter().map(|c| c.glyph).collect();
            let middle = if state.playing { Glyph::Pause } else { Glyph::Play };
            prop_assert_eq!(glyphs, vec![
                Glyph::ToStart, Glyph::PrevCut, Glyph::StepBack, middle, Glyph::StepForward, Glyph::NextCut, Glyph::ToEnd,
            ]);
            let word = if state.playing { "Pause" } else { "Play" };
            prop_assert!(bar[3].tip.starts_with(word), "{}", bar[3].tip);
            let mut names: Vec<&str> = bar.iter().map(|c| c.name).collect();
            names.sort_unstable();
            names.dedup();
            prop_assert_eq!(names.len(), 7);
            let sent: Vec<String> = bar.iter().map(|c| format!("{:?}", c.message)).collect();
            let want = ["Seek(0)", "Cut(-1)", "Step(-1)", "TogglePlay", "Step(1)", "Cut(1)", &format!("Seek({})", state.end)];
            prop_assert_eq!(sent, want);
        }

        /// A button does what its key does: the key its tooltip names
        /// sends, through the window's key map, the very message a press
        /// sends. While a dialog is open neither does anything: the button
        /// is greyed as the key is dead.
        #[test]
        fn a_button_is_its_key(state in state()) {
            for c in controls(&state) {
                let key = menu_bar::key_message(&state, c.keys.0.clone(), c.keys.1).map(|m| format!("{m:?}"));
                prop_assert_eq!(c.enabled, state.free, "{}", c.name);
                prop_assert_eq!(&key, &c.enabled.then(|| format!("{:?}", c.message)), "{}", c.name);
                // And a press: the key's message, or nothing when greyed.
                prop_assert_eq!(c.pressed().map(|m| format!("{m:?}")), key.clone(), "{}", c.name);
                // Play and pause name their second key too, and it sends the same.
                let plays = matches!(c.message, Message::TogglePlay);
                let (also, held) = menu_bar::ALSO_PLAYS;
                let second = if plays { format!(" or {}", keys_text(also.clone(), held)) } else { String::new() };
                let named = format!("({}{})", keys_text(c.keys.0.clone(), c.keys.1), second);
                prop_assert!(c.tip.ends_with(&named), "{} does not name {}", c.tip, named);
                if plays {
                    prop_assert_eq!(menu_bar::key_message(&state, also, held).map(|m| format!("{m:?}")), key.clone(), "{}", c.name);
                }
                prop_assert!(!c.tip.contains('&'), "{}", c.tip);
            }
        }

        /// The scrub bar asks only for frames of the cut, the frame under
        /// the handle, and the readout says the frame and the length.
        #[test]
        fn the_scrub_bar_stays_in_the_cut(value in prop_oneof![-10f32..20_000.0, Just(f32::NAN), Just(f32::INFINITY)], end in 0usize..10_000, frame in 0usize..10_000) {
            let to = scrub_frame(value, end);
            prop_assert!(to <= end);
            let frame = frame.min(end);
            prop_assert_eq!(scrub_frame(frame as f32, end), frame);
            prop_assert_eq!(scrub_frame(frame as f32 + 0.4, end), frame);
            prop_assert_eq!(frame_text(frame, end), format!("{frame} / {end}"));
        }
    }

    /// The shortcuts read as the menu writes them.
    #[test]
    fn shortcuts_read_as_the_menu_writes_them() {
        assert_eq!(keys_text(Key::Named(Named::Space), Modifiers::NONE), "Space");
        assert_eq!(keys_text(Key::Named(Named::ArrowLeft), Modifiers::NONE), "Left");
        assert_eq!(keys_text(Key::Named(Named::Home), Modifiers::NONE), "Home");
        assert_eq!(keys_text(Key::Named(Named::ArrowUp), Modifiers::NONE), "Up");
        assert_eq!(keys_text(Key::Named(Named::PageUp), Modifiers::NONE), "Page Up");
        assert_eq!(keys_text(Key::Named(Named::PageDown), Modifiers { shift: true, ..Modifiers::NONE }), "Shift+Page Down");
        assert_eq!(keys_text(Key::Character('s'), Modifiers { shift: true, control: true, ..Modifiers::NONE }), "Ctrl+Shift+S");
        let chord = keys_text(Key::Named(Named::ArrowRight), Modifiers::COMMAND);
        assert_eq!(chord, if cfg!(target_os = "macos") { "Cmd+Right" } else { "Ctrl+Right" });
    }
}
