//! The editor's look: every colour it draws with, in one place.
//!
//! The surround is untinted grey from end to end: a tinted surround shifts
//! how the picture's colours read, so the editor gets its character from
//! type and structure instead of hue. One accent, [`now`], marks the
//! present moment (the playhead, the timecode, a clip being dragged, the
//! focused control) and nothing else. Clip colour carries information: a
//! video clip takes a muted hue of the department its take comes from, so a
//! cut reads as "layout here, animation there, lighting at the end" at a
//! glance; a clip with nothing to play is hatched, not red.

use primer::prelude::*;

fn grey(v: u8) -> Color {
    Color::from_rgb8(v, v, v)
}

/// The window's backdrop, around the picture.
pub fn surround() -> Color {
    grey(30)
}
/// Panels: the toolbar, the bins, the ruler.
pub fn panel() -> Color {
    grey(38)
}
/// The video tracks' band.
pub fn band() -> Color {
    grey(33)
}
/// The audio tracks' band.
pub fn band_audio() -> Color {
    grey(29)
}
/// Hairlines between rows and around controls.
pub fn rule() -> Color {
    grey(58)
}
/// The lines between track rows.
pub fn seam() -> Color {
    grey(22)
}
/// Text.
pub fn text() -> Color {
    grey(225)
}
/// Secondary text: details, labels, the track names.
pub fn quiet() -> Color {
    grey(140)
}
/// Secondary text on a clip: lighter than [`quiet`], to read on the
/// department hues.
pub fn quiet_on_clip() -> Color {
    grey(185)
}

/// What is on: a selected tab, a ticked box, the highlighted menu row.
/// White words on it read (better than 5:1).
pub fn on() -> Color {
    grey(100)
}

/// Ticks on the ruler.
pub fn tick() -> Color {
    grey(92)
}
/// The ruler's strip of frames already buffered.
pub fn buffered() -> Color {
    grey(104)
}
/// Markers on the ruler, and a selected clip's outline.
pub fn mark() -> Color {
    grey(215)
}
/// The present moment: the playhead, the timecode, a drag, focus.
pub fn now() -> Color {
    Color::from_rgb8(240, 180, 60)
}
/// Something went wrong.
pub fn error() -> Color {
    Color::from_rgb8(235, 120, 105)
}
/// Worth knowing before going on: a newer version, a change left unticked.
/// Paler than [`now`], which it must not be mistaken for.
pub fn caution() -> Color {
    Color::from_rgb8(225, 200, 150)
}

/// An audio clip.
pub fn audio() -> Color {
    Color::from_rgb8(50, 55, 62)
}
/// A video clip with no department (a media item, a slug).
pub fn clip() -> Color {
    grey(60)
}
/// The ground of a clip with nothing to play, under its hatching.
pub fn missing() -> Color {
    grey(36)
}
/// The hatching of a clip with nothing to play.
pub fn hatch() -> Color {
    grey(58)
}

/// A button on the panels: the neutral fill. primer's default intent fills
/// a button with the theme's `primary`, the light grey that marks what is
/// on, and white words on it do not read.
pub fn button<Message>(id: Id, child: impl Into<Element<Message>>) -> primer::widget::Button<Message> {
    primer::prelude::button(id, child).intent(Intent::Secondary)
}

/// A checkbox that reads at a glance (#63): ticked is a light square in a
/// light frame, unticked an empty quiet frame. Primer's checkbox draws its
/// mark in the theme's `primary` grey, which white words must read on, so a
/// tick there is a faint grey square on grey.
pub fn tick_box<Message: 'static>(id: Id, ticked: bool, on_toggle: impl Fn(bool) -> Message + 'static) -> primer::widget::Button<Message> {
    let (frame_colour, fill) = tick_look(ticked);
    let mut mark = container(primer::prelude::text(String::new())).width(Length::fixed(8.0)).height(Length::fixed(8.0));
    if let Some(fill) = fill {
        mark = mark.background(fill);
    }
    let frame = container(mark).padding(2.0).border(1.5, frame_colour).border_radius(2.0);
    primer::prelude::button(id, frame).background(Color::TRANSPARENT).padding(2.0).on_press(move || on_toggle(!ticked))
}

/// A tick box's frame colour and its mark's fill (None: no mark).
fn tick_look(ticked: bool) -> (Color, Option<Color>) {
    if ticked { (text(), Some(text())) } else { (quiet(), None) }
}

/// The muted hues departments are drawn in, in order around the wheel: low
/// chroma and one lightness, so none outshouts another and text on them
/// reads the same.
const DEPARTMENT_HUES: [(u8, u8, u8); 8] = [
    (74, 64, 92), // violet
    (58, 72, 96), // blue
    (52, 82, 86), // teal
    (60, 84, 64), // green
    (84, 82, 56), // olive
    (96, 72, 52), // ochre
    (96, 60, 60), // brick
    (90, 60, 80), // plum
];

/// The pipeline's usual departments, in pipeline order, each its own hue so
/// the common ones never share one; any other department hashes to one.
const KNOWN: [&str; 8] = ["layout", "animation", "cfx", "fx", "lighting", "render", "comp", "slapcomp"];

/// The colour a video clip from `department`'s take is drawn in.
pub fn department(department: &str) -> Color {
    let name = department.to_ascii_lowercase();
    let index = KNOWN.iter().position(|k| *k == name).unwrap_or_else(|| {
        // FNV-1a: stable across runs and platforms, unlike the std hasher.
        let hash = name.bytes().fold(0x811c_9dc5u32, |h, b| (h ^ u32::from(b)).wrapping_mul(0x0100_0193));
        hash as usize % DEPARTMENT_HUES.len()
    });
    let (r, g, b) = DEPARTMENT_HUES[index];
    Color::from_rgb8(r, g, b)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn luma(c: Color) -> f32 {
        0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
    }

    /// The surround and panels are grey: a tint would shift the picture.
    #[test]
    fn the_surround_is_untinted() {
        for c in [surround(), panel(), band(), band_audio(), rule(), seam(), text(), quiet()] {
            assert!(c.r == c.g && c.g == c.b, "{c:?} is tinted");
        }
    }

    /// Clip text (TEXT, QUIET) reads on every department's hue, and no hue
    /// is as light as the selection outline or the accent.
    #[test]
    fn text_reads_on_every_department() {
        let hues = DEPARTMENT_HUES.iter().map(|&(r, g, b)| Color::from_rgb8(r, g, b));
        for hue in hues.chain([clip(), audio(), missing()]) {
            let (text, hue_l, quiet) = (luma(text()), luma(hue), luma(quiet()));
            assert!((text + 0.05) / (hue_l + 0.05) > 4.5, "{hue:?}");
            assert!(quiet > hue_l, "{hue:?}");
            assert!((luma(quiet_on_clip()) + 0.05) / (hue_l + 0.05) > 3.0, "{hue:?}");
            assert!(hue_l < luma(mark()) && hue_l < luma(now()), "{hue:?}");
        }
    }

    #[test]
    fn words_read_on_what_is_on() {
        assert!((luma(text()) + 0.05) / (luma(on()) + 0.05) > 4.5);
        assert!(luma(on()) > luma(panel()) * 1.5, "on stands clear of the panels");
    }

    /// A tick box reads at a glance (#63: Primer's tick was a faint grey
    /// square on grey): the light mark stands out from what it is drawn on
    /// at the non-text contrast of 3:1, and so does the frame round it, and
    /// an unticked frame is clearly dimmer than a ticked one.
    #[test]
    fn a_tick_reads_on_the_panels() {
        let ratio = |a: Color, b: Color| {
            let (a, b) = (luma(a), luma(b));
            (a.max(b) + 0.05) / (a.min(b) + 0.05)
        };
        let ((on_frame, mark), (off_frame, no_mark)) = (tick_look(true), tick_look(false));
        let mark = mark.expect("a ticked box has its mark");
        assert_eq!(no_mark, None, "an unticked box is empty");
        for ground in [surround(), panel(), band()] {
            assert!(ratio(mark, ground) > 3.0, "the tick on {ground:?}");
            assert!(ratio(on_frame, ground) > 3.0, "a ticked frame on {ground:?}");
            assert!(ratio(off_frame, ground) > 1.5, "an unticked frame on {ground:?}");
        }
        assert!(ratio(on_frame, off_frame) > 1.5, "ticked and unticked frames look alike");
    }

    #[test]
    fn departments_keep_their_colour() {
        assert_eq!(department("Animation"), department("animation"));
        assert_ne!(department("layout"), department("animation"));
        assert_eq!(department("previz"), department("previz"));
    }
}
