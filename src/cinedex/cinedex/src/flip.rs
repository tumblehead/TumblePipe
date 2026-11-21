//! Flip Horizontally: the picture mirrored left to right, to check a pose
//! or a silhouette with a fresh eye.
//!
//! It is the view's only, as the exposure is: the edit, its saves and
//! publishes, Export Movie and the burn-ins never see it. The video view
//! mirrors where it samples the frame ([`sample_x`], `video.wgsl`); the
//! burn-ins are drawn over it unmirrored, each in its own corner, so they
//! still read. A window shows it is flipped by a chip in its toolbar
//! ([`CHIP`]), which unflips when pressed. It lasts the window's session:
//! a new window shows the picture the right way round.
//!
//! The editor and a preview window (`frames_view`) both offer it: in the
//! View menu, on a right-click on the picture, and with Shift+H ([`KEY`]).
//! The editor's right-click menu on a shot's clip has it too.

use primer::keyboard::{Key, Modifiers};
use primer::prelude::*;

use crate::look;

/// The key that flips and unflips: Shift+H, alone. Neither window gives H
/// a meaning with or without Shift otherwise.
pub(crate) const KEY: (char, Modifiers) = ('h', Modifiers { shift: true, ..Modifiers::NONE });

/// Whether `key` with `modifiers` held is [`KEY`].
pub(crate) fn is_key(key: &Key, modifiers: Modifiers) -> bool {
    matches!(key, Key::Character(c) if c.eq_ignore_ascii_case(&KEY.0)) && modifiers == KEY.1
}

/// The View menu's row: ticked while the picture is flipped, `&` marking
/// its access letter.
pub(crate) fn menu_label(flipped: bool) -> &'static str {
    if flipped { "\u{2713} Flip &Horizontally" } else { "Flip &Horizontally" }
}

/// The row of a right-click menu, which has no access letters.
pub(crate) fn context_label(flipped: bool) -> &'static str {
    if flipped { "\u{2713} Flip Horizontally" } else { "Flip Horizontally" }
}

/// What the toolbar says while the picture is flipped; pressed, it unflips.
pub(crate) const CHIP: &str = "Flipped  \u{2715}";

/// The chip's tooltip.
pub(crate) const TIP: &str = "The picture is mirrored for viewing only: the edit, Export Movie and the burn-ins are not. Click to show it the right way round (Shift+H)";

/// The toolbar's chip saying the picture is flipped, sending `unflip` when
/// pressed; as the editor's View > Show chip is drawn.
pub(crate) fn chip<M: Clone + 'static>(unflip: M) -> Element<M> {
    let chip = look::button(Id::new("flipped"), text(CHIP).font_size(12.0))
        .padding(Padding::from([3.0, 8.0]))
        .focusable(false)
        .on_press(move || unflip.clone());
    tooltip(Id::new("flipped tip"), chip, TIP).into()
}

/// Where across the frame (0 its left edge, 1 its right) the video view
/// samples for the point `x` across the picture on screen: the same place,
/// or the mirrored one. `video.wgsl` does this with `u.display.w` as
/// [`uniform`]; this is its model, which the tests hold the shader to.
#[cfg(test)]
pub(crate) fn sample_x(x: f32, flipped: bool) -> f32 {
    let flip = uniform(flipped);
    x + (1.0 - 2.0 * x) * flip
}

/// The shader's flip flag: 1 flipped, 0 not.
pub(crate) fn uniform(flipped: bool) -> f32 {
    if flipped { 1.0 } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use super::*;

    proptest! {
        /// Flipped, the picture's left edge shows the frame's right edge
        /// and the order across is reversed; flipping twice, or not at
        /// all, samples where it is.
        #[test]
        fn a_flipped_picture_is_the_frame_mirrored(x in 0f32..=1.0, y in 0f32..=1.0) {
            prop_assert_eq!(sample_x(x, false), x);
            prop_assert!((sample_x(x, true) - (1.0 - x)).abs() < 1e-6);
            prop_assert!((sample_x(sample_x(x, true), true) - x).abs() < 1e-6);
            prop_assert!((0.0..=1.0).contains(&sample_x(x, true)));
            if x < y {
                prop_assert!(sample_x(x, true) >= sample_x(y, true));
            }
        }

        /// Shift+H and nothing else held is the key, in either case; H
        /// alone or with anything else is not.
        #[test]
        fn only_shift_h_flips(c in proptest::char::range('a', 'z'), upper in any::<bool>(), held in any::<[bool; 4]>()) {
            let [shift, control, alt, logo] = held;
            let modifiers = Modifiers { shift, control, alt, logo };
            let key = Key::Character(if upper { c.to_ascii_uppercase() } else { c });
            prop_assert_eq!(is_key(&key, modifiers), c == 'h' && shift && !control && !alt && !logo);
        }
    }

    /// The menu rows say the same, ticked exactly while flipped; the
    /// right-click one has no access letter.
    #[test]
    fn the_rows_are_ticked_while_flipped() {
        for flipped in [false, true] {
            assert_eq!(menu_label(flipped).starts_with('\u{2713}'), flipped);
            assert_eq!(menu_label(flipped).replace('&', ""), context_label(flipped));
            assert!(!context_label(flipped).contains('&'));
        }
    }

    /// The shader mirrors as [`sample_x`] does, by the flag [`uniform`]
    /// puts in `display.w`.
    #[test]
    fn the_shader_samples_as_sample_x() {
        let source = include_str!("video.wgsl");
        assert!(source.contains("corner.x + (1.0 - 2.0 * corner.x) * u.display.w"), "video.wgsl mirrors by display.w");
        assert_eq!((uniform(false), uniform(true)), (0.0, 1.0));
    }
}
