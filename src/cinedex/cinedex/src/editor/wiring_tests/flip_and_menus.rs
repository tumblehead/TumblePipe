//! Flip Horizontally and the menus in the toolbar's row, wired: what the
//! editor tells the video view, its menus and its toolbar.

use primer::widget::custom::{Ctx, Layout, LayoutCtx, Limits, StateStore, Widget};

use super::*;
use crate::flip;
use crate::menu_words::tests::{OddText, texts};

/// The words the toolbar draws, laid out 2400 wide, left to right.
fn toolbar_words(editor: &Editor) -> Vec<String> {
    let toolbar = editor.toolbar();
    let (store, theme) = (StateStore::new(), Theme::dark());
    let node =
        toolbar.layout(&Limits::new(Size::ZERO, Size::new(2400.0, 80.0)), LayoutCtx { measurer: &OddText, store: &store, theme: &theme });
    let ctx = Ctx { cursor: Cursor::Unavailable, theme: &theme, store: &store, measurer: &OddText, visible: node.bounds() };
    let mut drawn = Vec::new();
    texts(&toolbar.draw(Layout::new(&node), ctx), &mut drawn);
    drawn.sort_by(|a, b| a.bounds.x.total_cmp(&b.bounds.x));
    drawn.into_iter().map(|text| text.content).collect()
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 16, ..ProptestConfig::default() })]

    /// Flip Horizontally mirrors the view and back, on a read-only edit
    /// too: the video view is told, the View menu and the right-click rows
    /// tick it and send the other state, the toolbar's chip shows exactly
    /// while it is flipped, and the cut never changes.
    #[test]
    fn flip_mirrors_the_view_and_says_so(edit in cut(), flips in prop::collection::vec(any::<bool>(), 1..5), read_only in any::<bool>()) {
        let mut bench = Bench::new(&edit, read_only);
        let before = bench.edit().clone();
        for on in flips {
            bench.send(Message::Flip(on));
            prop_assert_eq!(bench.editor.flipped, on);
            prop_assert_eq!(bench.editor.video().flipped, on);
            prop_assert_eq!(bench.editor.menu_state().flipped, on);
            let (label, sends) = bench.editor.flip_row();
            prop_assert_eq!(label, flip::context_label(on));
            prop_assert_eq!(format!("{sends:?}"), format!("{:?}", Message::Flip(!on)));
            let said = toolbar_words(&bench.editor);
            prop_assert_eq!(said.iter().any(|words| words == flip::CHIP), on, "{:?}", said);
            prop_assert_eq!(bench.edit(), &before);
        }
    }
}

/// On Windows and Linux the menus are the first thing in the toolbar's
/// row, and Primer is given no menu to stack a bar of its own above the
/// view; on macOS they are the global menu bar, not in the toolbar.
#[test]
fn the_menus_lead_the_toolbars_row_off_macos() {
    let bench = Bench::new(&WorkingEdit::new(24, [1920, 1080], "01:00:00:00"), false);
    assert_eq!(Application::menu(&bench.editor).is_none(), menu_row::in_window());
    let said = toolbar_words(&bench.editor);
    let titles = ["File", "Edit", "Playback", "View"];
    if menu_row::in_window() {
        assert_eq!(&said[..titles.len()], titles, "{said:?}");
    } else {
        assert!(!said.iter().any(|words| titles.contains(&words.as_str())), "{said:?}");
    }
}
