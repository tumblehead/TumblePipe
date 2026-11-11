//! The editor's menu bar (File, Edit, Playback and View) and its keys.
//!
//! Both are read from [`State`], the little of the editor they depend on, so
//! the tests can check them against each other: an item's shortcut is the key
//! that sends its message, an item is greyed when its key does nothing, and no
//! key acts with Command or Alt held unless the menu shows that chord.
//!
//! On macOS the menu is the global menu bar. On Windows and Linux the player is
//! built without Primer's `os` feature (the native Windows bar ignores the
//! theme, and Linux's would need GTK), and Primer draws the same declaration
//! inside the window instead, as a themed bar above the editor: the `&` in a
//! label is its access letter there,
//! and the shortcuts are hints, the keys being [`key_message`]'s either way.
//! Every item is greyed while a dialog is open, as the keys are then, and
//! the ones that change the cut while the edit is read-only.

use primer::keyboard::{Key, Modifiers, Named};
use primer::menu::{Accelerator, Menu, MenuItem};

use super::{Editor, Message, OpenDialog};
use crate::editing::Origin;
use crate::take_menu::TakeAxis;

const COMMAND: Modifiers = Modifiers::COMMAND;
const SHIFT: Modifiers = Modifiers { shift: true, ..Modifiers::NONE };
const ALT: Modifiers = Modifiers { alt: true, ..Modifiers::NONE };
const COMMAND_SHIFT: Modifiers = Modifiers { shift: true, ..Modifiers::COMMAND };
const NONE: Modifiers = Modifiers::NONE;

/// What the menu and the keys depend on.
#[derive(Debug, Clone, Copy)]
pub(super) struct State {
    /// No dialog is open.
    pub free: bool,
    /// Free, and the edit is not read-only.
    pub writable: bool,
    /// The edit has a name, so it can be published.
    pub named: bool,
    /// For an edit in the working area: whether it is the master edit.
    pub master: Option<bool>,
    /// The bin has a pick to put in the cut.
    pub picked: bool,
    pub playing: bool,
    pub burnins: bool,
    /// The cut's end, one past its last frame: where Go to End parks, so
    /// an Insert there appends (#25).
    pub end: usize,
}

/// One row of a menu. A handful are built per frame, so the size of a
/// separator does not matter.
#[allow(clippy::large_enum_variant)]
pub(super) enum Entry {
    Item { label: &'static str, message: Message, enabled: bool, keys: Option<(Key, Modifiers)> },
    Separator,
}

/// A menu of the bar: its title and rows.
pub(super) struct Heading {
    pub label: &'static str,
    pub entries: Vec<Entry>,
}

fn item(label: &'static str, message: Message, enabled: bool, keys: Option<(Key, Modifiers)>) -> Entry {
    Entry::Item { label, message, enabled, keys }
}

fn letter(c: char, modifiers: Modifiers) -> Option<(Key, Modifiers)> {
    Some((Key::Character(c), modifiers))
}

fn named(key: Named, modifiers: Modifiers) -> Option<(Key, Modifiers)> {
    Some((Key::Named(key), modifiers))
}

/// The menus of the bar, in order (the macOS application menu aside).
pub(super) fn headings(state: &State) -> Vec<Heading> {
    vec![
        Heading { label: "&File", entries: file_menu(state) },
        Heading { label: "&Edit", entries: edit_menu(state) },
        Heading { label: "&Playback", entries: playback_menu(state) },
        Heading { label: "&View", entries: view_menu(state) },
    ]
}

fn file_menu(s: &State) -> Vec<Entry> {
    let mut menu = vec![
        item("&Open…", Message::Open(OpenDialog::Open), s.free, letter('o', COMMAND)),
        item("&Save", Message::Open(OpenDialog::Save), s.writable, letter('s', COMMAND)),
        item("Save &As…", Message::Open(OpenDialog::SaveAs), s.free, letter('s', COMMAND_SHIFT)),
        item("&Publish…", Message::Open(OpenDialog::Publish), s.writable && s.named, None),
    ];
    if let Some(is_master) = s.master {
        let label = if is_master { "Stop Being the Master Edit" } else { "Make the Master Edit" };
        menu.push(item(label, Message::SetMaster(!is_master), s.free, None));
    }
    menu.extend([
        Entry::Separator,
        item("New S&hot…", Message::Open(OpenDialog::NewShot), s.writable, None),
        item("&Import Media…", Message::Open(OpenDialog::Import), s.free, None),
    ]);
    if !cfg!(target_os = "macos") {
        menu.extend([Entry::Separator, item("E&xit", Message::CloseRequested, true, None)]);
    }
    menu
}

fn edit_menu(s: &State) -> Vec<Entry> {
    let picked = s.writable && s.picked;
    vec![
        item("&Undo", Message::Undo, s.writable, letter('z', COMMAND)),
        item("&Redo", Message::Redo, s.writable, letter('z', COMMAND_SHIFT)),
        Entry::Separator,
        item("&Split at Playhead", Message::Split, s.writable, letter('s', NONE)),
        item("&Lift", Message::Lift, s.writable, named(Named::Delete, NONE)),
        item("Ripple &Delete", Message::RippleDelete, s.writable, named(Named::Delete, SHIFT)),
        item("Add &Marker", Message::Marker, s.writable, letter('m', NONE)),
        Entry::Separator,
        item("Select &All", Message::SelectAll, s.free, letter('a', COMMAND)),
        item("Deselect", Message::ClearSelection, s.free, named(Named::Escape, NONE)),
        Entry::Separator,
        item("&Insert Bin Pick", Message::BinPut(false), picked, None),
        item("&Overlay Bin Pick", Message::BinPut(true), picked, None),
        Entry::Separator,
        // The take keys (#47), as the old playlist player had them.
        item("&Newer Version", Message::TakeStep(TakeAxis::Version, 1), s.writable, named(Named::ArrowUp, NONE)),
        item("Older &Version", Message::TakeStep(TakeAxis::Version, -1), s.writable, named(Named::ArrowDown, NONE)),
        item("&Previous Department", Message::TakeStep(TakeAxis::Department, -1), s.writable, named(Named::ArrowUp, COMMAND)),
        item("Ne&xt Department", Message::TakeStep(TakeAxis::Department, 1), s.writable, named(Named::ArrowDown, COMMAND)),
        item("Previous Source (Playblast / Render)", Message::TakeStep(TakeAxis::Source, -1), s.writable, named(Named::ArrowUp, SHIFT)),
        item("Next Source (Playblast / Render)", Message::TakeStep(TakeAxis::Source, 1), s.writable, named(Named::ArrowDown, SHIFT)),
    ]
}

fn playback_menu(s: &State) -> Vec<Entry> {
    vec![
        item(if s.playing { "&Pause" } else { "&Play" }, Message::TogglePlay, s.free, named(Named::Space, NONE)),
        Entry::Separator,
        item("Previous Frame", Message::Step(-1), s.free, named(Named::ArrowLeft, NONE)),
        item("Next Frame", Message::Step(1), s.free, named(Named::ArrowRight, NONE)),
        item("Back One Second", Message::Seconds(-1), s.free, named(Named::ArrowLeft, ALT)),
        item("Forward One Second", Message::Seconds(1), s.free, named(Named::ArrowRight, ALT)),
        item("Previous Edit Point", Message::Cut(-1), s.free, named(Named::ArrowLeft, COMMAND)),
        item("Next Edit Point", Message::Cut(1), s.free, named(Named::ArrowRight, COMMAND)),
        Entry::Separator,
        item("Go to &Start", Message::Seek(0), s.free, named(Named::Home, NONE)),
        item("Go to &End", Message::Seek(s.end), s.free, named(Named::End, NONE)),
    ]
}

fn view_menu(s: &State) -> Vec<Entry> {
    let label = if s.burnins { "Hide &Burn-ins" } else { "Show &Burn-ins" };
    vec![item(label, Message::Burnins(!s.burnins), s.free, letter('b', NONE))]
}

/// What a key pressed in the window sends: nothing while a dialog is open
/// (its fields take the keys). A bare letter acts only with neither Command
/// nor Alt held, so Ctrl+B or Alt+M is not taken for B or M.
pub(super) fn key_message(state: &State, key: Key, modifiers: Modifiers) -> Option<Message> {
    if !state.free {
        return None;
    }
    let command = modifiers.command();
    let bare = !command && !modifiers.alt;
    match key {
        Key::Named(Named::ArrowRight) if command => Some(Message::Cut(1)),
        Key::Named(Named::ArrowLeft) if command => Some(Message::Cut(-1)),
        Key::Named(Named::ArrowRight) if modifiers.alt => Some(Message::Seconds(1)),
        Key::Named(Named::ArrowLeft) if modifiers.alt => Some(Message::Seconds(-1)),
        Key::Named(Named::ArrowRight) => Some(Message::Step(1)),
        Key::Named(Named::ArrowLeft) => Some(Message::Step(-1)),
        Key::Named(Named::ArrowUp) if command => Some(Message::TakeStep(TakeAxis::Department, -1)),
        Key::Named(Named::ArrowDown) if command => Some(Message::TakeStep(TakeAxis::Department, 1)),
        Key::Named(Named::ArrowUp) if modifiers.shift => Some(Message::TakeStep(TakeAxis::Source, -1)),
        Key::Named(Named::ArrowDown) if modifiers.shift => Some(Message::TakeStep(TakeAxis::Source, 1)),
        Key::Named(Named::ArrowUp) => Some(Message::TakeStep(TakeAxis::Version, 1)),
        Key::Named(Named::ArrowDown) => Some(Message::TakeStep(TakeAxis::Version, -1)),
        Key::Named(Named::Home) => Some(Message::Seek(0)),
        Key::Named(Named::End) => Some(Message::Seek(state.end)),
        Key::Named(Named::Space) => Some(Message::TogglePlay),
        Key::Named(Named::Escape) => Some(Message::ClearSelection),
        Key::Named(Named::Delete | Named::Backspace) if modifiers.shift => Some(Message::RippleDelete),
        Key::Named(Named::Delete | Named::Backspace) => Some(Message::Lift),
        Key::Character('z' | 'Z') if command && modifiers.shift => Some(Message::Redo),
        Key::Character('z' | 'Z') if command => Some(Message::Undo),
        Key::Character('y' | 'Y') if command => Some(Message::Redo),
        Key::Character('s' | 'S') if command && modifiers.shift => Some(Message::Open(OpenDialog::SaveAs)),
        Key::Character('s' | 'S') if command => Some(Message::Open(OpenDialog::Save)),
        Key::Character('a' | 'A') if command => Some(Message::SelectAll),
        Key::Character('o' | 'O') if command => Some(Message::Open(OpenDialog::Open)),
        Key::Character('s' | 'S') if bare => Some(Message::Split),
        Key::Character('m' | 'M') if bare => Some(Message::Marker),
        Key::Character('b' | 'B') if bare => Some(Message::Burnins(!state.burnins)),
        _ => None,
    }
}

/// The shortcut shown beside an item. On Windows it is only the hint: the
/// key itself reaches `Application::on_key` (winit never hands it to the
/// menu). On macOS the menu takes its shortcut before the window sees it, so
/// only the Command chords are given to it; a bare key (S for Split, Space)
/// stays the window's, where it cannot steal a letter typed in a dialog.
fn shortcut(key: Key, modifiers: Modifiers) -> Option<Accelerator> {
    if cfg!(target_os = "macos") && !modifiers.command() {
        return None;
    }
    Some(Accelerator::new(key, modifiers))
}

fn menu_item(entry: Entry) -> MenuItem<Message> {
    match entry {
        Entry::Separator => MenuItem::separator(),
        Entry::Item { label, message, enabled, keys } => {
            let button = MenuItem::button(label, message).enabled(enabled);
            match keys.and_then(|(key, modifiers)| shortcut(key, modifiers)) {
                Some(accelerator) => button.accelerator(accelerator),
                None => button,
            }
        }
    }
}

fn menu(entries: Vec<Entry>) -> Menu<Message> {
    entries.into_iter().fold(Menu::new(), |menu, entry| menu.push(menu_item(entry)))
}

impl Editor {
    pub(super) fn menu_state(&self) -> State {
        let free = self.dialog.is_none();
        let writable = free && !self.doc.read_only();
        let master = match &self.doc.origin {
            Origin::Working { name, .. } => Some(self.bins.master.as_deref() == Some(name.as_str())),
            _ => None,
        };
        State {
            free,
            writable,
            named: !matches!(self.doc.origin, Origin::Untitled),
            master,
            picked: self.bin_pick.is_some(),
            playing: self.playing,
            burnins: self.burnins,
            end: self.end(),
        }
    }

    pub(super) fn menu_bar(&self) -> Menu<Message> {
        let state = self.menu_state();
        let mut bar = Menu::new();
        if cfg!(target_os = "macos") {
            // The bold application menu: macOS's home for Quit, which goes
            // through the editor's close (it asks about unsaved changes),
            // not the platform's own terminate.
            bar = bar.app_submenu(
                "TumblePipe Editor",
                menu(vec![item("Quit TumblePipe Editor", Message::CloseRequested, true, letter('q', COMMAND))]),
            );
        }
        headings(&state).into_iter().fold(bar, |bar, heading| bar.submenu(heading.label, menu(heading.entries)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn state() -> impl Strategy<Value = State> {
        (any::<[bool; 7]>(), proptest::option::of(any::<bool>()), 0usize..10_000).prop_map(|(b, master, end)| State {
            free: b[0],
            writable: b[0] && b[1],
            named: b[2],
            master,
            picked: b[3],
            playing: b[4],
            burnins: b[5],
            end,
        })
    }

    fn modifiers() -> impl Strategy<Value = Modifiers> {
        any::<[bool; 4]>().prop_map(|[shift, control, alt, logo]| Modifiers { shift, control, alt, logo })
    }

    fn key() -> impl Strategy<Value = Key> {
        let named = [
            Named::ArrowLeft,
            Named::ArrowRight,
            Named::ArrowUp,
            Named::ArrowDown,
            Named::Home,
            Named::End,
            Named::Space,
            Named::Escape,
            Named::Delete,
            Named::Backspace,
            Named::Enter,
            Named::Tab,
        ];
        prop_oneof![
            proptest::sample::select(named.to_vec()).prop_map(Key::Named),
            proptest::char::range('a', 'z').prop_map(Key::Character),
            proptest::char::range('A', 'Z').prop_map(Key::Character),
        ]
    }

    /// An item of the bar: its menu's title, its label, its message as
    /// written, whether it is enabled, and its shortcut.
    type Row = (&'static str, &'static str, String, bool, Option<(Key, Modifiers)>);

    fn items(state: &State) -> Vec<Row> {
        let mut out = Vec::new();
        for heading in headings(state) {
            for entry in heading.entries {
                if let Entry::Item { label, message, enabled, keys } = entry {
                    out.push((heading.label, label, format!("{message:?}"), enabled, keys));
                }
            }
        }
        out
    }

    /// Whether `message` changes the cut or its file, so needs a writable edit.
    fn writes(message: &str) -> bool {
        ["Undo", "Redo", "Split", "Lift", "RippleDelete", "Marker", "BinPut", "TakeStep", "Open(Save)", "Open(Publish)", "Open(NewShot)"]
            .iter()
            .any(|w| message == *w || message.starts_with(&format!("{w}(")))
    }

    proptest! {
        /// The shortcut an item shows is a key that sends that item's message.
        #[test]
        fn a_shortcut_sends_its_items_message(state in state()) {
            let state = State { free: true, ..state };
            for (menu, label, message, _, keys) in items(&state) {
                if let Some((key, modifiers)) = keys {
                    let sent = key_message(&state, key.clone(), modifiers).map(|m| format!("{m:?}"));
                    prop_assert_eq!(sent, Some(message.clone()), "{} > {}: {:?} {:?}", menu, label, key, modifiers);
                }
            }
        }

        /// While a dialog is open no key acts, and every item but Exit is greyed.
        #[test]
        fn a_dialog_takes_every_key_and_greys_the_menu(state in state(), key in key(), modifiers in modifiers()) {
            let state = State { free: false, writable: false, ..state };
            prop_assert!(key_message(&state, key, modifiers).is_none());
            for (menu, label, message, enabled, _) in items(&state) {
                prop_assert!(!enabled || message == "CloseRequested", "{} > {} is enabled under a dialog", menu, label);
            }
        }

        /// What changes the cut is greyed on an edit that cannot change.
        #[test]
        fn a_read_only_edit_greys_what_writes(state in state()) {
            let state = State { writable: false, ..state };
            for (menu, label, message, enabled, _) in items(&state) {
                prop_assert!(!(enabled && writes(&message)), "{} > {} ({}) is enabled read-only", menu, label, message);
            }
        }

        /// With Command or Alt held a letter acts only through a Command chord
        /// the menu shows on that letter (Ctrl+Y for Redo aside), so Ctrl+B or
        /// Alt+M is not B or M.
        #[test]
        fn a_held_modifier_does_not_fall_through_to_a_bare_letter(state in state(), c in proptest::char::range('a', 'z'), upper in any::<bool>(), modifiers in modifiers()) {
            let state = State { free: true, ..state };
            prop_assume!(modifiers.command() || modifiers.alt);
            let key = Key::Character(if upper { c.to_ascii_uppercase() } else { c });
            if key_message(&state, key, modifiers).is_some() {
                let shown = items(&state).into_iter().any(|(_, _, _, _, keys)| {
                    matches!(keys, Some((Key::Character(k), m)) if k == c && m.command() && modifiers.command())
                });
                prop_assert!(shown || (c == 'y' && modifiers.command()), "{:?} {:?} acts with no chord in the menu", c, modifiers);
            }
        }

        /// No two items show the same shortcut.
        #[test]
        fn no_two_items_share_a_shortcut(state in state()) {
            let all = items(&state);
            for (i, (_, a, _, _, ka)) in all.iter().enumerate() {
                for (_, b, _, _, kb) in &all[i + 1..] {
                    prop_assert!(ka.is_none() || ka != kb, "{} and {} share {:?}", a, b, ka);
                }
            }
        }

        /// Each menu keeps an access letter of its own on its rows: no two rows
        /// of one menu are reached by the same `&` letter. And each `&` marks
        /// one: a label has at most one, followed by a letter or digit (not
        /// `&&`, which the in-window bar on Linux would show as a literal `&`).
        #[test]
        fn a_menus_access_letters_are_distinct(state in state()) {
            for heading in headings(&state) {
                let mut seen = Vec::new();
                for entry in heading.entries {
                    let Entry::Item { label, .. } = entry else { continue };
                    prop_assert!(label.matches('&').count() <= 1, "{}: more than one `&`", label);
                    if let Some(at) = label.find('&') {
                        let next = label[at + 1..].chars().next();
                        prop_assert!(next.is_some_and(char::is_alphanumeric), "{}: `&` marks no letter", label);
                        let c = next.unwrap().to_ascii_lowercase();
                        prop_assert!(!seen.contains(&c), "{}: '{}' is used twice", heading.label, c);
                        seen.push(c);
                    }
                }
            }
        }
    }

    /// The bar's titles have distinct access letters too.
    #[test]
    fn the_bars_access_letters_are_distinct() {
        let state =
            State { free: true, writable: true, named: true, master: Some(false), picked: true, playing: false, burnins: true, end: 99 };
        let letters: Vec<char> = headings(&state)
            .iter()
            .map(|h| h.label[h.label.find('&').unwrap() + 1..].chars().next().unwrap().to_ascii_lowercase())
            .collect();
        let mut unique = letters.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), letters.len(), "{letters:?}");
    }
}
