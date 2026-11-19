//! The viewer's burn-ins painted into a frame's pixels, for Export Movie.
//!
//! The viewer draws its burn-ins with the window's text renderer, on the
//! GPU; an export has no window, so the same four lines are shaped with
//! the same shaper and font (cosmic-text's system sans-serif, as Primer
//! uses) and painted here, laid out as `burnin` lays them out: a margin in
//! from each corner, each line over a dark outline of four copies a pixel
//! off each way.
//!
//! The viewer sizes its burn-ins in logical pixels from the picture's
//! height on screen (`burnin::text_size`), and no picture is shown more
//! than about a full-HD screen tall. So a movie up to [`REFERENCE_HEIGHT`]
//! pixels tall is laid out as the viewer lays out a picture that many
//! logical pixels tall, and a taller one as that layout scaled up: a UHD
//! movie's burn-ins are twice a full-HD movie's, the same share of the
//! picture.

use cosmic_text::{Attrs, Buffer, Color, Family, FontSystem, Metrics, Shaping, SwashCache};

use crate::burnin;

/// The movie height the viewer's layout is used at as it is; a taller
/// movie's burn-ins grow with it.
pub const REFERENCE_HEIGHT: f32 = 1080.0;
/// Primer's line height for text of size 1 (`primer_core::measure`).
const LINE_HEIGHT_RATIO: f32 = 1.3;
/// The outline's and the words' colours, as `burnin` draws them.
const OUTLINE: [u8; 4] = [0, 0, 0, 200];
const WORDS: [u8; 4] = [235, 235, 235, 255];

/// The burn-ins' measures, in pixels of the frame painted on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sizes {
    /// The font size.
    pub text: f32,
    /// A line's height.
    pub line: f32,
    /// Space between a burn-in and the picture's edge.
    pub margin: f32,
    /// How far each outline copy is off the words.
    pub outline: f32,
}

/// The measures for a movie `movie_height` pixels tall, painted on frames
/// `frame_height` tall that are scaled to the movie afterwards (the same
/// height when they are not): in the movie they are the viewer's.
pub fn sizes(movie_height: u32, frame_height: u32) -> Sizes {
    let movie = movie_height.max(1) as f32;
    // A physical pixel per logical one up to the reference height.
    let dots = (movie / REFERENCE_HEIGHT).max(1.0);
    let to_frame = dots * frame_height.max(1) as f32 / movie;
    let text = burnin::text_size(movie / dots) * to_frame;
    Sizes { text, line: text * LINE_HEIGHT_RATIO, margin: burnin::MARGIN * to_frame, outline: to_frame }
}

/// A corner of the picture.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Corner {
    TopLeft,
    TopRight,
    BottomLeft,
    BottomRight,
}

impl Corner {
    /// In the order of [`Painter::paint`]'s lines.
    pub const ALL: [Corner; 4] = [Corner::TopLeft, Corner::TopRight, Corner::BottomLeft, Corner::BottomRight];
}

/// Where the top left of a line `wide` pixels wide goes in a frame
/// `frame` big: `margin` in from its corner's two edges.
pub fn origin(corner: Corner, frame: (u32, u32), sizes: &Sizes, wide: f32) -> (f32, f32) {
    let (width, height) = (frame.0 as f32, frame.1 as f32);
    let left = sizes.margin;
    let right = width - sizes.margin - wide;
    let top = sizes.margin;
    let bottom = height - sizes.margin - sizes.line;
    match corner {
        Corner::TopLeft => (left, top),
        Corner::TopRight => (right, top),
        Corner::BottomLeft => (left, bottom),
        Corner::BottomRight => (right, bottom),
    }
}

/// `over` at `alpha` (0-255, with the colour's own) laid on `under`.
pub fn laid(under: u8, over: u8, alpha: u32) -> u8 {
    let alpha = alpha.min(255);
    ((u32::from(over) * alpha + u32::from(under) * (255 - alpha) + 127) / 255) as u8
}

/// Paints burn-ins: the fonts, loaded once, and the glyphs drawn so far.
pub struct Painter {
    fonts: FontSystem,
    glyphs: SwashCache,
}

impl Painter {
    /// Loads the system's fonts (a moment).
    pub fn new() -> Self {
        Self { fonts: FontSystem::new(), glyphs: SwashCache::new() }
    }

    /// Whether there is a font to paint with (a machine with none paints
    /// nothing).
    pub fn has_fonts(&self) -> bool {
        self.fonts.db().faces().next().is_some()
    }

    /// Paint `lines` (top left, top right, bottom left, bottom right) into
    /// `rgb`, a `width` x `height` frame of packed 8-bit RGB.
    pub fn paint(&mut self, rgb: &mut [u8], width: u32, height: u32, sizes: &Sizes, lines: &[String; 4]) {
        if rgb.len() != (width as usize) * (height as usize) * 3 || sizes.text <= 0.0 {
            return;
        }
        for (corner, words) in Corner::ALL.into_iter().zip(lines) {
            if words.is_empty() {
                continue;
            }
            let mut buffer = Buffer::new(&mut self.fonts, Metrics::new(sizes.text, sizes.line));
            buffer.set_size(None, None);
            buffer.set_text(words, &Attrs::new().family(Family::SansSerif), Shaping::Advanced, None);
            buffer.shape_until_scroll(&mut self.fonts, false);
            let wide = buffer.layout_runs().map(|run| run.line_w).fold(0.0, f32::max);
            let (x, y) = origin(corner, (width, height), sizes, wide);
            let off = sizes.outline;
            let passes = [(-off, -off, OUTLINE), (off, -off, OUTLINE), (-off, off, OUTLINE), (off, off, OUTLINE), (0.0, 0.0, WORDS)];
            for (dx, dy, [r, g, b, a]) in passes {
                let (left, top) = ((x + dx).round() as i32, (y + dy).round() as i32);
                buffer.draw(&mut self.fonts, &mut self.glyphs, Color::rgba(r, g, b, a), |px, py, w, h, colour| {
                    for (px, py) in (0..w as i32).flat_map(|i| (0..h as i32).map(move |j| (left + px + i, top + py + j))) {
                        if px < 0 || py < 0 || px >= width as i32 || py >= height as i32 {
                            continue;
                        }
                        let at = (py as usize * width as usize + px as usize) * 3;
                        let alpha = u32::from(colour.a());
                        for (k, over) in [colour.r(), colour.g(), colour.b()].into_iter().enumerate() {
                            rgb[at + k] = laid(rgb[at + k], over, alpha);
                        }
                    }
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// Up to the reference height a movie's burn-ins are the viewer's
        /// for a picture that tall: its text size, its 10 pixel margin, a
        /// one pixel outline. Taller, the reference layout scaled.
        #[test]
        fn a_movies_measures_are_the_viewers(height in 16u32..5000) {
            let s = sizes(height, height);
            if height as f32 <= REFERENCE_HEIGHT {
                prop_assert_eq!(s.text, burnin::text_size(height as f32));
                prop_assert_eq!((s.margin, s.outline), (10.0, 1.0));
            } else {
                let scale = height as f32 / 1080.0;
                let full = sizes(1080, 1080);
                prop_assert!((s.text - full.text * scale).abs() < 1e-3 && (s.margin - full.margin * scale).abs() < 1e-3);
                prop_assert!((s.outline - scale).abs() < 1e-4);
            }
            prop_assert!((s.line - s.text * 1.3).abs() < 1e-4);
        }

        /// Painted on frames that are scaled to the movie afterwards, the
        /// measures are the movie's times that scale: in the movie they
        /// come out as they would painted there.
        #[test]
        fn measures_follow_the_frames_scale(movie in 16u32..5000, frame in 16u32..5000) {
            let (direct, scaled) = (sizes(movie, movie), sizes(movie, frame));
            let k = frame as f32 / movie as f32;
            for (a, b) in [(scaled.text, direct.text), (scaled.line, direct.line), (scaled.margin, direct.margin), (scaled.outline, direct.outline)] {
                prop_assert!((a - b * k).abs() <= 1e-3 * b.max(1.0) * k.max(1.0), "{} is not {} x {}", a, b, k);
            }
        }

        /// A line sits a margin in from its own corner's two edges, and
        /// opposite corners mirror each other.
        #[test]
        fn a_line_sits_a_margin_from_its_corner(width in 64u32..5000, height in 64u32..5000, wide in 0f32..60.0) {
            let s = sizes(height, height);
            let frame = (width, height);
            let (w, h) = (width as f32, height as f32);
            for corner in Corner::ALL {
                let (x, y) = origin(corner, frame, &s, wide);
                let left = matches!(corner, Corner::TopLeft | Corner::BottomLeft);
                let top = matches!(corner, Corner::TopLeft | Corner::TopRight);
                let gap_x = if left { x } else { w - (x + wide) };
                let gap_y = if top { y } else { h - (y + s.line) };
                prop_assert!((gap_x - s.margin).abs() < 1e-2 && (gap_y - s.margin).abs() < 1e-2, "{:?}: {} {}", corner, gap_x, gap_y);
            }
        }

        /// Laying a colour on another: none of it is the pixel under, all
        /// of it is the colour, and between it lies between, in order.
        #[test]
        fn laying_blends(under in any::<u8>(), over in any::<u8>(), alpha in 0u32..256, more in 0u32..256) {
            prop_assert_eq!(laid(under, over, 0), under);
            prop_assert_eq!(laid(under, over, 255), over);
            let got = laid(under, over, alpha);
            prop_assert!(got >= under.min(over) && got <= under.max(over));
            let exact = (f64::from(over) * f64::from(alpha) + f64::from(under) * f64::from(255 - alpha)) / 255.0;
            prop_assert!((f64::from(got) - exact).abs() <= 0.5 + 1e-9);
            let (lo, hi) = (alpha.min(more), alpha.max(more));
            let (near, far) = (laid(under, over, lo), laid(under, over, hi));
            prop_assert!(under.abs_diff(near) <= under.abs_diff(far));
        }
    }

    /// The pixels of `after` that differ from `before`, as (x, y).
    fn changed(before: &[u8], after: &[u8], width: u32) -> Vec<(u32, u32)> {
        before
            .chunks(3)
            .zip(after.chunks(3))
            .enumerate()
            .filter(|(_, (a, b))| a != b)
            .map(|(i, _)| (i as u32 % width, i as u32 / width))
            .collect()
    }

    /// Each line is painted in its own corner and nowhere else: within the
    /// margin's box (less the outline), light words on a dark outline;
    /// no words, nothing painted; the same words, the same pixels.
    #[test]
    fn each_line_is_painted_in_its_corner() {
        let mut painter = Painter::new();
        if !painter.has_fonts() {
            eprintln!("no fonts here: burn-ins are not painted");
            return;
        }
        // To look at them: CINEDEX_BURNIN_PNG=<file.png> writes a full-HD
        // frame with all four painted.
        if let Ok(file) = std::env::var("CINEDEX_BURNIN_PNG") {
            let (w, h) = (1920u32, 1080u32);
            let mut frame: Vec<u8> = (0..w * h).flat_map(|i| if i / w < h / 2 { [20u8, 20, 20] } else { [40, 150, 40] }).collect();
            let lines = ["sh030", "denoise v0006 slapcomp", "01:00:08:09", "1060 (60 / 200)"].map(str::to_owned);
            painter.paint(&mut frame, w, h, &sizes(h, h), &lines);
            let mut encoder = png::Encoder::new(std::io::BufWriter::new(std::fs::File::create(file).unwrap()), w, h);
            encoder.set_color(png::ColorType::Rgb);
            encoder.set_depth(png::BitDepth::Eight);
            encoder.write_header().unwrap().write_image_data(&frame).unwrap();
        }
        for (width, height, frame_height) in [(640u32, 360u32, 360u32), (1920, 1080, 1080), (960, 540, 1080), (1280, 2160, 2160)] {
            // `frame_height` is what the frames painted on are tall when
            // they are scaled to a movie `height` tall afterwards.
            let (w, h) = (width * frame_height / height, frame_height);
            let s = sizes(height, frame_height);
            let grey = vec![128u8; (w * h * 3) as usize];
            let none = [String::new(), String::new(), String::new(), String::new()];
            let mut plain = grey.clone();
            painter.paint(&mut plain, w, h, &s, &none);
            assert_eq!(plain, grey, "no words, nothing painted");
            for (i, corner) in Corner::ALL.into_iter().enumerate() {
                let mut lines = none.clone();
                lines[i] = "sh030 1060 (60 / 200)".to_owned();
                let mut frame = grey.clone();
                painter.paint(&mut frame, w, h, &s, &lines);
                let touched = changed(&grey, &frame, w);
                assert!(touched.len() > 50, "{corner:?} painted {} pixels at {w}x{h}", touched.len());
                let slack = s.outline.ceil() as u32 + 1;
                let near = (s.margin.floor() as u32).saturating_sub(slack);
                let band = (s.margin + s.line).ceil() as u32 + slack;
                for (x, y) in &touched {
                    let (from_left, from_top) = (*x, *y);
                    let (from_right, from_bottom) = (w - 1 - x, h - 1 - y);
                    let (dx, dy) = match corner {
                        Corner::TopLeft => (from_left, from_top),
                        Corner::TopRight => (from_right, from_top),
                        Corner::BottomLeft => (from_left, from_bottom),
                        Corner::BottomRight => (from_right, from_bottom),
                    };
                    assert!(dx >= near && dy >= near && dy <= band, "{corner:?} painted {x},{y} of {w}x{h} (margin {})", s.margin);
                    assert!(dx < w / 2, "{corner:?} reached past the middle at {x},{y}");
                }
                // Light words, and darker pixels than the grey around them.
                let values: Vec<u8> = touched.iter().map(|(x, y)| frame[((y * w + x) * 3) as usize]).collect();
                assert!(values.iter().any(|v| *v > 200) && values.iter().any(|v| *v < 100), "{corner:?}: words and outline");
                let mut again = grey.clone();
                painter.paint(&mut again, w, h, &s, &lines);
                assert_eq!(again, frame, "the same words paint the same pixels");
                lines[i] = "sh040 1061 (61 / 200)".to_owned();
                let mut other = grey.clone();
                painter.paint(&mut other, w, h, &s, &lines);
                assert_ne!(other, frame, "other words paint other pixels");
            }
            // A frame of another size than said is left alone.
            let mut short = vec![128u8; 30];
            painter.paint(&mut short, w, h, &s, &[String::from("x"), String::new(), String::new(), String::new()]);
            assert_eq!(short, vec![128u8; 30]);
        }
    }
}
