//! Which frame numbers a movie's pictures are when they are not one
//! continuous run: a multi-range render's mp4 (#84) plays only the frames a
//! Frame List rendered, back to back (`1001-1030 1040-1055`), and records
//! the list in its `frame_ranges` tag (#86). Its index 30 is then frame
//! 1040, not 1031, and frames 1031-1039 are no picture of it at all.
//!
//! The tag's text is `tumblepipe.farm.specs.frame_ranges.format_ranges` of
//! the normalised ranges, read by the same grammar as `parse` there: words
//! split on spaces or commas, each a frame `N` or a range `A-B` (`A <= B`),
//! frames whole numbers from 0. Here the ranges must also come ascending and
//! apart (normalised), so one text names one numbering.

/// The movie tag a multi-range render's mp4 records its frames in
/// (`tumblepipe.farm.specs.frame_ranges.CONFIG_KEY`).
pub const FRAME_RANGES_KEY: &str = "frame_ranges";

/// A movie's frame numbers: inclusive ranges, ascending and apart, whose
/// frames its pictures are in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameRanges {
    ranges: Vec<(i64, i64)>,
    /// The index each range's first frame is.
    starts: Vec<usize>,
    count: usize,
}

/// A run of a movie's pictures whose frame numbers go up one by one:
/// frames `first..=last` are its indexes from `index`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Run {
    pub first: i64,
    pub last: i64,
    pub index: usize,
}

impl FrameRanges {
    /// `text` (a `frame_ranges` tag) read; an error says what is wrong with
    /// it. Nothing listed, a word that is no frame or range, a range that
    /// runs backwards, or ranges out of order or overlapping are errors.
    pub fn parse(text: &str) -> Result<Self, String> {
        let fail = |why: String| format!("its {FRAME_RANGES_KEY} tag '{text}' {why}");
        let mut ranges: Vec<(i64, i64)> = Vec::new();
        for word in text.split([' ', '\t', ',']).filter(|w| !w.is_empty()) {
            let frame = |digits: &str| -> Option<i64> {
                (!digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit())).then(|| digits.parse().ok()).flatten()
            };
            let (first, last) = match word.split_once('-') {
                Some((a, b)) => (frame(a), frame(b)),
                None => (frame(word), frame(word)),
            };
            let (Some(first), Some(last)) = (first, last) else {
                return Err(fail(format!("has '{word}', not a frame or a range")));
            };
            if first > last {
                return Err(fail(format!("has '{word}', which runs backwards")));
            }
            if let Some(&(_, before)) = ranges.last()
                && first <= before
            {
                return Err(fail(format!("has '{word}' out of order or overlapping")));
            }
            ranges.push((first, last));
        }
        if ranges.is_empty() {
            return Err(fail("lists no frames".into()));
        }
        let mut starts = Vec::with_capacity(ranges.len());
        let mut count: usize = 0;
        for &(first, last) in &ranges {
            starts.push(count);
            let frames = usize::try_from(last - first).ok().and_then(|n| n.checked_add(1));
            count = frames.and_then(|n| count.checked_add(n)).ok_or_else(|| fail("lists too many frames".into()))?;
        }
        Ok(Self { ranges, starts, count })
    }

    /// The inclusive ranges, ascending and apart.
    pub fn ranges(&self) -> &[(i64, i64)] {
        &self.ranges
    }

    /// How many frames it lists: how many pictures its movie has.
    pub fn count(&self) -> usize {
        self.count
    }

    /// The runs of indexes whose frames go up one by one, in order.
    pub fn runs(&self) -> impl Iterator<Item = Run> + '_ {
        self.ranges.iter().zip(&self.starts).map(|(&(first, last), &index)| Run { first, last, index })
    }

    /// The frame number of index `index`; None past the last.
    pub fn frame_number(&self, index: usize) -> Option<i64> {
        if index >= self.count {
            return None;
        }
        let run = self.starts.partition_point(|&start| start <= index) - 1;
        Some(self.ranges[run].0 + (index - self.starts[run]) as i64)
    }

    /// The index showing frame number `frame`; None for a frame it does not
    /// list (in a gap, or outside).
    pub fn index_of(&self, frame: i64) -> Option<usize> {
        let run = self.ranges.partition_point(|&(first, _)| first <= frame).checked_sub(1)?;
        let (first, last) = self.ranges[run];
        (frame <= last).then(|| self.starts[run] + (frame - first) as usize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Ascending ranges apart, from gaps and lengths.
    fn ranges() -> impl Strategy<Value = Vec<(i64, i64)>> {
        (0i64..2000, prop::collection::vec((1i64..20, 0i64..15), 1..8)).prop_map(|(start, parts)| {
            let mut at = start;
            parts
                .into_iter()
                .map(|(gap, len)| {
                    let first = at + gap;
                    at = first + len + 1;
                    (first, first + len)
                })
                .collect()
        })
    }

    /// The text `format_ranges` writes: `A-B`, or `N` for a range of one.
    fn format(ranges: &[(i64, i64)], sep: &str) -> String {
        ranges.iter().map(|&(a, b)| if a == b { a.to_string() } else { format!("{a}-{b}") }).collect::<Vec<_>>().join(sep)
    }

    proptest! {
        /// A listed numbering reads back as written (spaces or commas), and
        /// numbers its pictures by it: index i is the i-th listed frame,
        /// every listed frame is shown by exactly its index, a frame in a
        /// gap or outside by none, and the runs cover the indexes in order.
        #[test]
        fn a_frame_list_numbers_the_pictures_back_to_back(ranges in ranges(), comma in any::<bool>(), probe in -10i64..2400) {
            let text = format(&ranges, if comma { ", " } else { " " });
            let list = FrameRanges::parse(&text).unwrap();
            prop_assert_eq!(list.ranges(), &ranges[..]);
            let frames: Vec<i64> = ranges.iter().flat_map(|&(a, b)| a..=b).collect();
            prop_assert_eq!(list.count(), frames.len());
            for (index, &frame) in frames.iter().enumerate() {
                prop_assert_eq!(list.frame_number(index), Some(frame));
                prop_assert_eq!(list.index_of(frame), Some(index));
            }
            prop_assert_eq!(list.frame_number(frames.len()), None);
            prop_assert_eq!(list.index_of(probe), frames.iter().position(|&f| f == probe));
            let mut next = 0;
            for run in list.runs() {
                prop_assert_eq!(run.index, next);
                prop_assert_eq!(list.frame_number(run.index), Some(run.first));
                next += (run.last - run.first + 1) as usize;
            }
            prop_assert_eq!(next, list.count());
        }

        /// A list out of order or overlapping (by one frame too), a word
        /// that is no frame (a sign, a letter, a dot), a backwards range or
        /// nothing at all is refused, naming the tag.
        #[test]
        fn a_frame_list_that_is_not_one_numbering_is_refused(ranges in ranges(), bad in prop_oneof![
            Just("-5"), Just("10-"), Just("a"), Just("1.5"), Just("3-1"), Just("+4"), Just("1--2"), Just("１")
        ]) {
            let mut swapped = ranges.clone();
            swapped.reverse();
            if ranges.len() > 1 {
                prop_assert!(FrameRanges::parse(&format(&swapped, " ")).is_err());
                let (a, b) = ranges[0];
                let overlapping = format!("{} {}-{}", format(&ranges, " "), a, b);
                prop_assert!(FrameRanges::parse(&overlapping).is_err());
            }
            // A range from the frame the one before ends on shares it.
            let end = ranges[ranges.len() - 1].1;
            let sharing = format!("{} {end}-{}", format(&ranges, " "), end + 3);
            prop_assert!(FrameRanges::parse(&sharing).is_err());
            let error = FrameRanges::parse(&format!("{} {bad}", format(&ranges, " "))).unwrap_err();
            prop_assert!(error.contains(FRAME_RANGES_KEY), "{}", error);
            prop_assert!(FrameRanges::parse(" , ").is_err());
        }
    }
}
