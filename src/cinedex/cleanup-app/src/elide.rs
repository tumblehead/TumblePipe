//! One line of words cut with an ellipsis to fit, which Primer's `Text`
//! does not do (it wraps). The tree table's reasons, notes and errors can be
//! a sentence long; in a 28px row a wrapped one ran over the rows below.
//! Cut at the end, as the convention cuts a long name whose start is what
//! matters, the whole text in the row's tooltip.

use primer::core::measure::{TextMeasurer, line_height};
use primer::prelude::*;
use primer::widget::custom::{Ctx, Layout, LayoutCtx, Limits, Node, RenderNode, Sizing, TextNode, Widget};

/// Room left between drawn words and the box they are drawn in, so
/// shaping never wraps a word that measured exactly as wide as its box.
const SLACK: f32 = 1.0;
const ELLIPSIS: char = '\u{2026}';

/// `words` as they fit `room` wide at `size`: whole, or the longest start
/// of them that fits with an ellipsis after it, or nothing when not even the
/// ellipsis fits.
pub fn elide(measurer: &dyn TextMeasurer, words: &str, size: f32, room: f32) -> String {
    let fits = |s: &str| measurer.measure_text(s, size, None).width <= room;
    if fits(words) {
        return words.to_string();
    }
    let chars: Vec<char> = words.chars().collect();
    // The longest start that fits: the measure grows with each character.
    let (mut low, mut high) = (0usize, chars.len());
    let cut = |n: usize| {
        let mut s: String = chars[..n].iter().collect::<String>().trim_end().to_string();
        s.push(ELLIPSIS);
        s
    };
    while low < high {
        let middle = (low + high).div_ceil(2);
        if fits(&cut(middle)) {
            low = middle;
        } else {
            high = middle - 1;
        }
    }
    let shown = cut(low);
    if fits(&shown) { shown } else { String::new() }
}

/// One line of words as wide as it is given, cut with an ellipsis to fit,
/// left-aligned, in the theme's body size.
pub struct Elided {
    words: String,
    colour: Color,
}

impl Elided {
    pub fn new(words: impl Into<String>, colour: Color) -> Self {
        Elided { words: words.into(), colour }
    }
}

impl<Message> Widget<Message> for Elided {
    fn sizing(&self) -> Sizing {
        Sizing::new(Length::Fill, Length::Shrink)
    }

    fn layout(&self, limits: &Limits, ctx: LayoutCtx<'_>) -> Node {
        let size = ctx.theme.typography.body;
        let whole = ctx.measurer.measure_text(&self.words, size, None).width;
        Node::new(limits.resolve(<Self as Widget<Message>>::sizing(self), Size::new(whole, line_height(size))))
    }

    fn draw(&self, layout: Layout<'_>, ctx: Ctx<'_>) -> RenderNode {
        let bounds = layout.bounds();
        let size = ctx.theme.typography.body;
        let shown = elide(ctx.measurer, &self.words, size, (bounds.width - SLACK).max(0.0));
        if shown.is_empty() || !ctx.is_visible(bounds) {
            return RenderNode::group(bounds, Vec::new());
        }
        RenderNode::text(TextNode::new(shown, bounds, size, self.colour))
    }
}

impl<Message> From<Elided> for Element<Message> {
    fn from(elided: Elided) -> Self {
        Element::new(elided)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use primer::renderer::{Fonts, TextMeasure};

    /// A cut line keeps as much as fits, ends in an ellipsis, and fits;
    /// with no room it is nothing.
    #[test]
    fn a_line_is_cut_to_fit() {
        let measure = TextMeasure::new(Fonts::load());
        let size = 14.0;
        let full = "Deadline could not be asked, so no job folder is purged";
        let whole = measure.measure_text(full, size, None).width;
        assert_eq!(elide(&measure, full, size, whole), full);
        for room in [0.0, 2.0, 20.0, 80.0, 200.0, whole - 1.0] {
            let cut = elide(&measure, full, size, room);
            assert!(measure.measure_text(&cut, size, None).width <= room, "{cut:?} in {room}");
            if !cut.is_empty() {
                let start = cut.strip_suffix(ELLIPSIS).expect("an ellipsis");
                assert!(full.starts_with(start) && start.len() < full.len());
                let next: String = full.chars().take(start.chars().count() + 1).collect();
                let next = format!("{}{ELLIPSIS}", next.trim_end());
                assert!(next == cut || measure.measure_text(&next, size, None).width > room, "{cut:?} could keep more in {room}");
            }
        }
        assert_eq!(elide(&measure, full, size, 0.0), "");
    }
}
