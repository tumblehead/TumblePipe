//! A render's frame list as `--expect` hands it over (`1001-1100`,
//! `1001,1050,1100`, `1-99x2`, Deadline's `1-99:2` and `1-99by2`).
//!
//! Cinedex reads `--expect` with this; `th_pipe`'s `pipe_lines` example
//! answers `tests/test_expect_parity.py` with it, which checks the Farm
//! Manager's Python (`frames_preview.expected_frames`) hands over only
//! lists read here. Not [`crate::FrameRanges`]: a job's list has steps and
//! need not be ascending.

/// The most frames a list may name.
pub const MAX_FRAMES: usize = 1_000_000;

/// `text`'s frame numbers, in the order written. An error says what is not
/// a frame or range. A list of more than [`MAX_FRAMES`] frames is refused,
/// as is a frame number outside 64 bits; no input panics.
pub fn parse_frame_list(text: &str) -> Result<Vec<i64>, String> {
    let mut out: Vec<i64> = Vec::new();
    let number = |s: &str| s.trim().parse::<i64>().map_err(|_| format!("'{text}': '{s}' is not a frame number"));
    for word in text.split([',', ' ', '\t']).filter(|w| !w.is_empty()) {
        let (range, step) = match word.split_once(['x', ':']).or_else(|| word.split_once("by")) {
            Some((range, step)) => (range, number(step)?),
            None => (word, 1),
        };
        if step < 1 {
            return Err(format!("'{text}': a step of {step}"));
        }
        // A leading minus is a negative frame, not a range.
        let split = range.char_indices().skip(1).find(|&(_, c)| c == '-').map(|(i, _)| i);
        let (first, last) = match split {
            Some(i) => (number(&range[..i])?, number(&range[i + 1..])?),
            None => (number(range)?, number(range)?),
        };
        let (low, high) = (first.min(last), first.max(last));
        // In 128 bits: the span of two 64-bit frames does not fit in 64.
        let count = (i128::from(high) - i128::from(low)) / i128::from(step) + 1;
        if count > (MAX_FRAMES - out.len()) as i128 {
            return Err(format!("'{text}' is more than a million frames"));
        }
        out.extend((low..=high).step_by(step as usize));
    }
    if out.is_empty() {
        return Err(format!("'{text}' lists no frames"));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn frame_lists_read_as_renders_write_them() {
        assert_eq!(parse_frame_list("1001-1004").unwrap(), [1001, 1002, 1003, 1004]);
        assert_eq!(parse_frame_list("1001,1050 1100").unwrap(), [1001, 1050, 1100]);
        assert_eq!(parse_frame_list("1-9x4").unwrap(), [1, 5, 9]);
        assert_eq!(parse_frame_list("1-9:4").unwrap(), [1, 5, 9]);
        assert_eq!(parse_frame_list("1-9by4").unwrap(), [1, 5, 9]);
        assert_eq!(parse_frame_list("-2-1").unwrap(), [-2, -1, 0, 1]);
        assert!(parse_frame_list("").is_err());
        assert!(parse_frame_list("a-b").is_err());
        assert!(parse_frame_list("1-9x0").is_err());
        assert!(parse_frame_list("0-999999999").is_err());
    }

    #[test]
    fn the_widest_spans_are_refused_not_overflowed() {
        let (min, max) = (i64::MIN, i64::MAX);
        assert!(parse_frame_list(&format!("{min}-{max}")).is_err());
        assert!(parse_frame_list(&format!("{min}-{max}x{max}")).unwrap().len() == 3);
        assert!(parse_frame_list("99999999999999999999").is_err());
        assert!(parse_frame_list("1-5x99999999999999999999").is_err());
        assert_eq!(parse_frame_list(&format!("{max}")).unwrap(), [max]);
    }

    #[test]
    fn the_million_counts_across_words() {
        assert_eq!(parse_frame_list("1-500000,1-500000").unwrap().len(), MAX_FRAMES);
        assert!(parse_frame_list("1-500000,1-500001").is_err());
    }

    proptest! {
        #[test]
        fn no_text_panics(text in "[-0-9x:by, \t+]{0,40}") {
            let _ = parse_frame_list(&text);
        }

        #[test]
        fn no_pair_of_frames_panics(a in any::<i64>(), b in any::<i64>(), step in 1i64..=i64::MAX) {
            if let Ok(frames) = parse_frame_list(&format!("{a}-{b}x{step}")) {
                prop_assert!(frames.len() <= MAX_FRAMES);
                prop_assert_eq!(frames[0], a.min(b));
            }
        }
    }
}
