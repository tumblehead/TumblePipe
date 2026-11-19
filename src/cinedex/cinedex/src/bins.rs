//! What the bins list and what a double-click on a row puts in the cut,
//! without a window (the panel is `editor/bin_panel.rs`).
//!
//! A row is a sequence's name or something to put in the cut (a shot, a
//! media item). A double-click names a [`Target`]: an item puts itself, a
//! sequence's name every shot of the sequence, in natural name order
//! ([`natural_cmp`]: numbers by value, so `sh9` comes before `sh10`), back
//! to back from the playhead ([`put_run`]) as one undo step. Insert or
//! Overlay is the same choice for one item and for a run.
//!
//! The grid view lays the same rows out as tiles ([`grid_rows`]): a
//! sequence's name on a line of its own, then its shots, so many a line.

use std::cmp::Ordering;
use std::time::{Duration, Instant};

use th_edit::document::{AudioClip, Frame, Source, VideoClip, WorkingEdit};
use th_edit::ops::{Op, Refused, Sources};

use crate::editing::{self, BinShot};

/// One run of a name: digits (a number) or anything else.
fn chunks(name: &str) -> Vec<(bool, &str)> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut digits = None;
    for (at, c) in name.char_indices() {
        let digit = c.is_ascii_digit();
        if digits.is_some_and(|d| d != digit) {
            out.push((!digit, &name[start..at]));
            start = at;
        }
        digits = Some(digit);
    }
    if let Some(digit) = digits {
        out.push((digit, &name[start..]));
    }
    out
}

/// Natural name order: runs of digits compare as numbers (`sh9` before
/// `sh10`, `061` between `060` and `070`), a number sorts before words,
/// words compare without case; names equal that way (`sh01`, `sh1`) fall
/// back to plain order, so only the same name is equal.
pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    let (left, right) = (chunks(a), chunks(b));
    for (l, r) in left.iter().zip(&right) {
        let order = match (l, r) {
            ((true, x), (true, y)) => {
                let (x, y) = (x.trim_start_matches('0'), y.trim_start_matches('0'));
                x.len().cmp(&y.len()).then_with(|| x.cmp(y))
            }
            ((true, _), (false, _)) => Ordering::Less,
            ((false, _), (true, _)) => Ordering::Greater,
            ((false, x), (false, y)) => x.to_lowercase().cmp(&y.to_lowercase()),
        };
        if order != Ordering::Equal {
            return order;
        }
    }
    left.len().cmp(&right.len()).then_with(|| a.cmp(b))
}

/// A row of a bin: a sequence's name over its shots, or something to put
/// in the cut (`index` in the bin's shots or media).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BinRow {
    Sequence(String),
    Item { index: usize, name: String, detail: String },
}

impl BinRow {
    pub fn item(&self) -> Option<usize> {
        match self {
            BinRow::Item { index, .. } => Some(*index),
            BinRow::Sequence(_) => None,
        }
    }

    /// What a double-click on the row puts in the cut.
    pub fn target(&self) -> Target {
        match self {
            BinRow::Item { index, .. } => Target::Item(*index),
            BinRow::Sequence(name) => Target::Sequence(name.clone()),
        }
    }
}

/// `shots` (sorted by sequence, as [`editing::project_shots`] lists them)
/// as bin rows: each sequence's name, then its shots.
pub fn shot_rows(shots: &[BinShot]) -> Vec<BinRow> {
    let mut out = Vec::new();
    for (index, shot) in shots.iter().enumerate() {
        if index == 0 || shots[index - 1].sequence != shot.sequence {
            out.push(BinRow::Sequence(shot.sequence.clone()));
        }
        let frames = shot.last - shot.first + 1;
        let detail = format!("{frames} frames, {}–{}", shot.first, shot.last);
        out.push(BinRow::Item { index, name: shot.name.clone(), detail });
    }
    out
}

/// What a double-click in a bin puts in the cut: one item (its index in the
/// bin's shots or media), or every shot of a sequence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    Item(usize),
    Sequence(String),
}

/// The shots of `sequence` among `shots` (their indices), in natural name
/// order: the order a double-click on the sequence's name puts them in.
pub fn sequence_shots(shots: &[BinShot], sequence: &str) -> Vec<usize> {
    let mut found: Vec<usize> = (0..shots.len()).filter(|&i| shots[i].sequence == sequence).collect();
    found.sort_by(|&a, &b| natural_cmp(&shots[a].name, &shots[b].name));
    found
}

/// The clips one bin item is at a frame: its picture, its sound.
pub type Clips = (Option<VideoClip<Source>>, Option<AudioClip<Source>>);

/// The ops that put a run of bin items into `edit` back to back from `at`,
/// and where the playhead goes after (the end of the last, None when
/// nothing was put). `makers` give each item's clips at a frame; an item
/// with nothing to play is passed over. One list of ops: one undo step.
///
/// Inserted, each item goes in as [`editing::put`] inserts one, at the end
/// of the one before, into the edit as the ones before left it: a run of N
/// is N single inserts with the playhead following each.
///
/// Overlaid, the run stays together: its pictures go on one video track
/// and its sounds on one audio track, the lowest (from `track` up) with
/// room for the whole run, a new one on top when none has, as
/// [`editing::put`] finds room for one clip as long as the run. (Clip by
/// clip the run would step down onto a lower track wherever that has room.)
///
/// Refused when an item cannot go in (as `th_edit` refuses it); nothing is
/// put then.
pub fn put_run<F: Fn(Frame) -> Clips>(
    edit: &WorkingEdit,
    at: Frame,
    track: usize,
    overlay: bool,
    makers: impl IntoIterator<Item = F>,
    sources: &impl Sources<Source>,
) -> Result<(Vec<Op<Source>>, Option<Frame>), Refused> {
    let mut scratch = edit.clone();
    let (mut at, mut end) = (at, None);
    let mut all = Vec::new();
    let mut run: Vec<Clips> = Vec::new();
    for make in makers {
        let (video, audio) = make(at);
        let Some(next) = editing::put_end(video.as_ref(), audio.as_ref()) else { continue };
        if overlay {
            run.push((video, audio));
        } else {
            let ops = editing::put(&scratch, video, audio, track, false);
            for op in &ops {
                scratch = th_edit::ops::apply(&scratch, op, sources)?;
            }
            all.extend(ops);
        }
        (at, end) = (next, Some(next));
    }
    if overlay {
        all = overlay_run(edit, track, run);
        for op in &all {
            scratch = th_edit::ops::apply(&scratch, op, sources)?;
        }
    }
    Ok((all, end))
}

/// The ops that lay `run` (back to back) over what is in `edit`: where
/// [`editing::put`] puts one clip spanning all the run's pictures, and one
/// spanning all its sounds, each of the run's goes instead.
fn overlay_run(edit: &WorkingEdit, track: usize, run: Vec<Clips>) -> Vec<Op<Source>> {
    use th_edit::ops::NewClip;
    let videos: Vec<VideoClip<Source>> = run.iter().filter_map(|c| c.0.clone()).collect();
    let audios: Vec<AudioClip<Source>> = run.into_iter().filter_map(|c| c.1).collect();
    let whole_video = videos.first().map(|first| {
        let end = videos.iter().map(|v| v.at + v.duration).max().unwrap_or(first.at);
        VideoClip { duration: end - first.at, ..first.clone() }
    });
    let whole_audio = audios.first().map(|first| {
        let end = audios.iter().map(|a| a.at + a.duration).max().unwrap_or(first.at);
        AudioClip { duration: end - first.at, ..first.clone() }
    });
    let mut ops = Vec::new();
    for op in editing::put(edit, whole_video, whole_audio, track, true) {
        match op {
            Op::Place { track, clip: NewClip::Video(_) } => {
                ops.extend(videos.iter().cloned().map(|v| Op::Place { track, clip: NewClip::Video(v) }));
            }
            Op::Place { track, clip: NewClip::Audio(_) } => {
                ops.extend(audios.iter().cloned().map(|a| Op::Place { track, clip: NewClip::Audio(a) }));
            }
            other => ops.push(other),
        }
    }
    ops
}

/// The narrowest a grid tile is laid out.
pub const TILE_MIN: f32 = 112.0;
/// The gap between grid tiles.
pub const TILE_GAP: f32 = 6.0;

/// How many tiles a grid `width` wide holds a line: as many of
/// [`TILE_MIN`] as fit with [`TILE_GAP`] between them, one at least.
pub fn grid_columns(width: f32) -> usize {
    if !width.is_finite() || width <= TILE_MIN {
        return 1;
    }
    (((width + TILE_GAP) / (TILE_MIN + TILE_GAP)).floor() as usize).max(1)
}

/// A line of the grid view: a sequence's name, or up to a line's worth of
/// items (indices into the rows they were made from).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GridRow {
    Sequence(usize),
    Tiles(Vec<usize>),
}

/// `rows` as the grid view's lines, `columns` tiles a line: every row once,
/// in order, a sequence's name on a line of its own and the items after it
/// filling lines until the next name.
pub fn grid_rows(rows: &[BinRow], columns: usize) -> Vec<GridRow> {
    let columns = columns.max(1);
    let mut out: Vec<GridRow> = Vec::new();
    for (at, row) in rows.iter().enumerate() {
        match (row, out.last_mut()) {
            (BinRow::Sequence(_), _) => out.push(GridRow::Sequence(at)),
            (BinRow::Item { .. }, Some(GridRow::Tiles(line))) if line.len() < columns => line.push(at),
            (BinRow::Item { .. }, _) => out.push(GridRow::Tiles(vec![at])),
        }
    }
    out
}

/// How soon a second press on a grid tile makes a double-click: the
/// toolkit's own interval for its lists.
pub const DOUBLE_CLICK: Duration = Duration::from_millis(400);

/// Whether a press on `target` at `now` completes a double-click: the
/// press before it (`last`) was on the same thing, no longer ago than
/// [`DOUBLE_CLICK`].
pub fn completes_double_click(last: Option<&(Target, Instant)>, target: &Target, now: Instant) -> bool {
    last.is_some_and(|(before, at)| before == target && now.saturating_duration_since(*at) <= DOUBLE_CLICK)
}

#[cfg(test)]
mod press_tests {
    use super::*;
    use proptest::prelude::*;

    fn target() -> impl Strategy<Value = Target> {
        prop_oneof![(0usize..4).prop_map(Target::Item), "[AB]".prop_map(Target::Sequence)]
    }

    proptest! {
        /// Two presses make a double-click exactly when they are on the
        /// same tile (or the same sequence's name) and no more than the
        /// interval apart; a first press never does.
        #[test]
        fn a_double_click_is_two_presses_on_one_thing_in_time(first in target(), second in target(), apart in 0u64..1000) {
            let start = Instant::now();
            let now = start + Duration::from_millis(apart);
            prop_assert!(!completes_double_click(None, &second, now));
            let expected = first == second && apart <= 400;
            prop_assert_eq!(completes_double_click(Some(&(first, start)), &second, now), expected);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{Rule, TrackRef};
    use th_edit::ops::Available;

    use crate::editing::{BinItem, Document, Origin, of_shots};

    struct Anything;
    impl Sources<Source> for Anything {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    fn bin_shot(sequence: &str, name: &str, frames: Frame) -> BinShot {
        BinShot {
            uri: format!("entity:/shots/{sequence}/{name}"),
            sequence: sequence.into(),
            name: name.into(),
            first: 1001,
            last: 1000 + frames,
        }
    }

    fn name() -> impl Strategy<Value = String> {
        prop_oneof!["[a-cA-C]{0,2}[0-9]{1,3}[a-b]{0,1}", "[a-cA-C0-9_]{0,5}"]
    }

    #[test]
    fn numbers_sort_by_value() {
        let mut names = vec!["070", "sh10", "060", "sh9", "061", "SH2", "sh010b", "sh10a", "9"];
        names.sort_by(|a, b| natural_cmp(a, b));
        assert_eq!(names, ["9", "060", "061", "070", "SH2", "sh9", "sh10", "sh10a", "sh010b"]);
        assert_eq!(natural_cmp("sh01", "sh1"), Ordering::Less, "the same number: plain order");
    }

    proptest! {
        /// Natural order is a total order: only the same name is equal, the
        /// two ways round agree, and three names never go in a circle.
        #[test]
        fn natural_order_is_total(a in name(), b in name(), c in name()) {
            prop_assert_eq!(natural_cmp(&a, &b) == Ordering::Equal, a == b);
            prop_assert_eq!(natural_cmp(&a, &b), natural_cmp(&b, &a).reverse());
            if natural_cmp(&a, &b) != Ordering::Greater && natural_cmp(&b, &c) != Ordering::Greater {
                prop_assert_ne!(natural_cmp(&a, &c), Ordering::Greater, "{} <= {} <= {}", a, b, c);
            }
        }

        /// Two names alike but for a number sort by the number's value,
        /// however each is zero-padded.
        #[test]
        fn a_number_in_a_name_sorts_by_value(
            prefix in "[a-zA-Z_]{0,4}", suffix in "[a-zA-Z_]{0,3}",
            x in 0u32..5000, y in 0u32..5000, pad_x in 0usize..6, pad_y in 0usize..6,
        ) {
            prop_assume!(x != y);
            let (a, b) = (format!("{prefix}{x:0pad_x$}{suffix}"), format!("{prefix}{y:0pad_y$}{suffix}"));
            prop_assert_eq!(natural_cmp(&a, &b), x.cmp(&y), "{} vs {}", a, b);
        }

        /// A sequence's shots are exactly its own, each once, in natural
        /// order, however the bin lists them.
        #[test]
        fn a_sequence_gives_its_shots_in_natural_order(
            names in prop::collection::btree_set(name(), 0..8), others in prop::collection::btree_set(name(), 0..5), seed in any::<u64>(),
        ) {
            let mut shots: Vec<BinShot> = names.iter().map(|n| bin_shot("A", n, 10)).chain(others.iter().map(|n| bin_shot("B", n, 10))).collect();
            // Any listing order.
            let len = shots.len();
            for i in 0..len {
                shots.swap(i, (seed as usize).wrapping_mul(i + 7) % len);
            }
            let found = sequence_shots(&shots, "A");
            let got: Vec<&str> = found.iter().map(|&i| shots[i].name.as_str()).collect();
            let mut expected: Vec<&str> = names.iter().map(String::as_str).collect();
            expected.sort_by(|a, b| natural_cmp(a, b));
            prop_assert_eq!(got, expected);
            prop_assert!(found.iter().all(|&i| shots[i].sequence == "A"));
            prop_assert!(sequence_shots(&shots, "C").is_empty());
        }
    }

    fn apply_all(edit: &WorkingEdit, ops: &[Op<Source>]) -> WorkingEdit {
        let mut edit = edit.clone();
        for op in ops {
            edit = th_edit::ops::apply(&edit, op, &Anything).unwrap_or_else(|e| panic!("{op:?}: {e}"));
        }
        edit
    }

    /// Every video clip of `edit`: its track, where it is, how long, and
    /// the shot it shows.
    fn video_clips(edit: &WorkingEdit) -> Vec<(usize, Frame, Frame, String)> {
        let mut out = Vec::new();
        for (t, track) in edit.video.iter().enumerate() {
            for c in &track.clips {
                let Source::Shot { shot, .. } = &c.source else { continue };
                out.push((t, c.at, c.duration, shot.clone()));
            }
        }
        out
    }

    /// An edit of `existing` shots (named apart from any bin shot) on V1.
    fn cut(existing: &[Frame]) -> WorkingEdit {
        let shots: Vec<(String, Frame, Frame)> =
            existing.iter().enumerate().map(|(i, n)| (format!("entity:/shots/old/{i}"), 1001, 1000 + n)).collect();
        of_shots(&shots, 24, [1920, 1080])
    }

    proptest! {
        /// A sequence put in the cut (what a double-click on its name
        /// does): every one of its shots goes in, once, in natural order,
        /// back to back from the playhead, each as long as its range, on
        /// one track; the playhead ends at the end of the last; and it is
        /// what putting them one by one, the playhead following, makes.
        /// (Up to 1.112.0 a double-click on a sequence's name put its first
        /// shot only.)
        #[test]
        fn a_sequence_goes_in_whole_in_order_back_to_back(
            existing in prop::collection::vec(1i64..40, 0..4),
            names in prop::collection::btree_map(name(), 1i64..40, 1..7),
            at in 0i64..160, overlay in any::<bool>(),
        ) {
            let edit = cut(&existing);
            let at = at.min(edit.length());
            let shots: Vec<BinShot> = names.iter().rev().map(|(n, frames)| bin_shot("A", n, *frames)).collect();
            let order = sequence_shots(&shots, "A");
            let makers = order.iter().map(|&i| { let shot = &shots[i]; move |at: Frame| -> Clips { (Some(shot.clip(at)), None) } });
            let (ops, end) = put_run(&edit, at, 0, overlay, makers, &Anything).unwrap();
            let after = apply_all(&edit, &ops);
            prop_assert_eq!(after.problem(), None);

            let new: Vec<(usize, Frame, Frame, String)> = video_clips(&after).into_iter().filter(|c| c.3.starts_with("entity:/shots/A/")).collect();
            let mut by_place = new.clone();
            by_place.sort_by_key(|c| c.1);
            let mut expected_at = at;
            prop_assert_eq!(by_place.len(), order.len(), "every shot once");
            for (clip, &i) in by_place.iter().zip(&order) {
                prop_assert_eq!(&clip.3, &shots[i].uri, "natural order");
                prop_assert_eq!((clip.1, clip.2), (expected_at, shots[i].last - shots[i].first + 1), "back to back");
                prop_assert_eq!(clip.0, by_place[0].0, "on one track");
                expected_at += clip.2;
            }
            prop_assert_eq!(end, Some(expected_at));
            let total: Frame = expected_at - at;
            if overlay {
                // What was there is as it was.
                let old: Vec<_> = video_clips(&after).into_iter().filter(|c| !c.3.starts_with("entity:/shots/A/")).collect();
                prop_assert_eq!(old, video_clips(&edit));
            } else {
                prop_assert_eq!(after.length(), edit.length() + total, "what follows moved on");
            }

            // One by one, the playhead following each: the same cut when
            // inserted, and for a run of one either way.
            if !overlay || order.len() == 1 {
                let (mut single, mut playhead) = (edit.clone(), at);
                for &i in &order {
                    let clip = shots[i].clip(playhead);
                    playhead = editing::put_end(Some(&clip), None).unwrap();
                    let ops = editing::put(&single, Some(clip), None, 0, overlay);
                    single = apply_all(&single, &ops);
                }
                prop_assert_eq!(&after, &single);
            }

            // One undo step.
            let mut document = Document::new(edit.clone(), Origin::Untitled);
            document.apply(&ops, &Anything).unwrap();
            prop_assert_eq!(&document.edit, &after);
            prop_assert!(document.undo());
            prop_assert_eq!(&document.edit, &edit);
            prop_assert!(!document.can_undo());
        }

        /// The grid's lines hold every row once, in order: a sequence's name
        /// alone on its line, never more tiles a line than fit, and a line
        /// short of full only before a name or at the end.
        #[test]
        fn the_grid_holds_every_row_once(sizes in prop::collection::vec(0usize..9, 0..6), columns in 0usize..7) {
            let mut rows = Vec::new();
            for (s, &count) in sizes.iter().enumerate() {
                rows.push(BinRow::Sequence(format!("S{s}")));
                for _ in 0..count {
                    rows.push(BinRow::Item { index: rows.len(), name: String::new(), detail: String::new() });
                }
            }
            let lines = grid_rows(&rows, columns);
            let flat: Vec<usize> = lines.iter().flat_map(|l| match l { GridRow::Sequence(i) => vec![*i], GridRow::Tiles(t) => t.clone() }).collect();
            prop_assert_eq!(flat, (0..rows.len()).collect::<Vec<_>>());
            for (at, line) in lines.iter().enumerate() {
                match line {
                    GridRow::Sequence(i) => prop_assert!(matches!(rows[*i], BinRow::Sequence(_))),
                    GridRow::Tiles(tiles) => {
                        prop_assert!(!tiles.is_empty() && tiles.len() <= columns.max(1));
                        let items = tiles.iter().all(|&i| rows[i].item().is_some());
                        prop_assert!(items);
                        let last = !matches!(lines.get(at + 1), Some(GridRow::Tiles(_)));
                        prop_assert!(last || tiles.len() == columns.max(1), "a short line in the middle");
                    }
                }
            }
        }

        /// As many tiles a line as fit at their narrowest, one at least.
        #[test]
        fn tiles_fit_the_width(width in -50f32..3000.0) {
            let columns = grid_columns(width);
            prop_assert!(columns >= 1);
            let needs = |n: usize| n as f32 * TILE_MIN + (n as f32 - 1.0) * TILE_GAP;
            if columns > 1 {
                prop_assert!(needs(columns) <= width + 1e-3);
            }
            prop_assert!(needs(columns + 1) > width - 1e-3);
        }
    }

    #[test]
    fn rows_name_what_a_double_click_puts() {
        let shots = [bin_shot("A", "010", 10), bin_shot("A", "020", 10), bin_shot("B", "010", 10)];
        let rows = shot_rows(&shots);
        let targets: Vec<Target> = rows.iter().map(BinRow::target).collect();
        assert_eq!(
            targets,
            [Target::Sequence("A".into()), Target::Item(0), Target::Item(1), Target::Sequence("B".into()), Target::Item(2)]
        );
    }

    /// A run of media items brings each one's sound along, and an item with
    /// nothing to play is passed over.
    #[test]
    fn a_run_of_media_keeps_picture_and_sound_together() {
        let edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
        let items = [
            BinItem { item: "animatic".into(), version: "v0001".into(), frames: Some(48), sound: Some(50) },
            BinItem { item: "empty".into(), version: "v0001".into(), frames: None, sound: None },
            BinItem { item: "music".into(), version: "v0001".into(), frames: None, sound: Some(30) },
        ];
        let makers = items.iter().map(|item| move |at: Frame| item.clips(at));
        let (ops, end) = put_run(&edit, 0, 0, false, makers, &Anything).unwrap();
        let after = apply_all(&edit, &ops);
        assert_eq!(end, Some(78));
        assert_eq!(after.spans(TrackRef::video(0)), [(0, 48)]);
        assert_eq!(after.spans(TrackRef::audio(0)), [(0, 48), (48, 78)]);
        let none: [fn(Frame) -> Clips; 0] = [];
        assert_eq!(put_run(&edit, 0, 0, false, none, &Anything).unwrap(), (Vec::new(), None));
        let _ = Rule::best();
    }

    /// One track's clips: each the gap before it and its length.
    fn track_spans() -> impl Strategy<Value = Vec<(Frame, Frame)>> {
        prop::collection::vec((0i64..20, 1i64..30), 0..4)
    }

    /// A cut of one to three video tracks and up to two audio tracks, each
    /// clip a slug named after its track and place.
    fn busy() -> impl Strategy<Value = WorkingEdit> {
        use th_edit::document::Track;
        (prop::collection::vec(track_spans(), 1..4), prop::collection::vec(track_spans(), 0..3)).prop_map(|(video, audio)| {
            let mut edit = WorkingEdit::new(24, [16, 9], "01:00:00:00");
            for (t, spans) in video.iter().enumerate() {
                let mut at = 0;
                let mut clips = Vec::new();
                for (i, &(gap, duration)) in spans.iter().enumerate() {
                    at += gap;
                    clips.push(VideoClip::new(at, duration, 0, Source::Slug { text: format!("V{t}.{i}") }));
                    at += duration;
                }
                edit.video.push(Track { name: format!("V{}", t + 1), clips });
            }
            for (t, spans) in audio.iter().enumerate() {
                let mut at = 0;
                let mut clips = Vec::new();
                for (i, &(gap, duration)) in spans.iter().enumerate() {
                    at += gap;
                    let source = Source::Slug { text: format!("A{t}.{i}") };
                    clips.push(AudioClip { at, duration, source_in: 0, source, gain_db: 0.0, fade_in: 0, fade_out: 0 });
                    at += duration;
                }
                edit.audio.push(Track { name: format!("A{}", t + 1), clips });
            }
            edit
        })
    }

    /// Every clip of an edit: whether it is a picture, its track, where it
    /// is, how long, and what it plays.
    fn every_clip(edit: &WorkingEdit) -> Vec<(bool, usize, Frame, Frame, String)> {
        let name = |source: &Source| match source {
            Source::Slug { text } => text.clone(),
            Source::Shot { shot, .. } => shot.clone(),
            Source::Media { item, .. } => item.clone(),
        };
        let mut out = Vec::new();
        for (t, track) in edit.video.iter().enumerate() {
            out.extend(track.clips.iter().map(|c| (true, t, c.at, c.duration, name(&c.source))));
        }
        for (t, track) in edit.audio.iter().enumerate() {
            out.extend(track.clips.iter().map(|c| (false, t, c.at, c.duration, name(&c.source))));
        }
        out
    }

    proptest! {
        /// A run of bin media (each with a picture, some with sound) put
        /// into any cut, of several tracks with clips anywhere, at any
        /// frame and aimed at any video track: the cut is sound after; the
        /// run's pictures are on one video track back to back from the
        /// frame in the order given, each sound under its picture, all on
        /// one audio track; and it is one undo step. Laid over, no clip
        /// that was there moves or changes, and the pictures are on the
        /// aimed track or one above it. Inserted, what was there moves
        /// exactly as one insert as long as the whole run moves it.
        #[test]
        fn a_run_goes_into_a_cut_of_many_tracks(
            edit in busy(), items in prop::collection::vec((1i64..30, any::<bool>()), 1..5),
            at in any::<prop::sample::Index>(), track in any::<prop::sample::Index>(), overlay in any::<bool>(),
        ) {
            let at = at.index(edit.length() as usize + 1) as Frame;
            let track = track.index(edit.video.len());
            let items: Vec<BinItem> = items
                .iter()
                .enumerate()
                .map(|(i, &(frames, sound))| BinItem { item: format!("new{i}"), version: "v0001".into(), frames: Some(frames), sound: sound.then_some(frames + 3) })
                .collect();
            let makers = items.iter().map(|item| move |at: Frame| item.clips(at));
            let (ops, end) = put_run(&edit, at, track, overlay, makers, &Anything).unwrap();
            let mut document = Document::new(edit.clone(), Origin::Untitled);
            document.apply(&ops, &Anything).unwrap();
            let after = document.edit.clone();
            prop_assert_eq!(after.problem(), None);
            let total: Frame = items.iter().map(|item| item.frames.unwrap()).sum();
            prop_assert_eq!(end, Some(at + total));

            let (new, old): (Vec<_>, Vec<_>) = every_clip(&after).into_iter().partition(|c| c.4.starts_with("new"));
            let pictures: Vec<_> = new.iter().filter(|c| c.0).collect();
            let sounds: Vec<_> = new.iter().filter(|c| !c.0).collect();
            prop_assert_eq!(pictures.len(), items.len(), "every picture once");
            let mut next = at;
            for item in &items {
                let frames = item.frames.unwrap();
                let picture = pictures.iter().find(|c| c.4 == item.item).unwrap();
                prop_assert_eq!((picture.1, picture.2, picture.3), (pictures[0].1, next, frames), "{} is not in its place in the run", &item.item);
                let sound: Vec<_> = sounds.iter().filter(|c| c.4 == item.item).collect();
                prop_assert_eq!(sound.len(), usize::from(item.sound.is_some()));
                for sound in sound {
                    prop_assert_eq!((sound.1, sound.2, sound.3), (sounds[0].1, next, frames), "{}'s sound is not under its picture", &item.item);
                }
                next += frames;
            }
            if overlay {
                prop_assert_eq!(&old, &every_clip(&edit), "an overlay moved what was there");
                prop_assert!(pictures[0].1 >= track, "an overlay went under the track aimed at");
            } else {
                prop_assert_eq!(pictures[0].1, track, "an insert goes on the track aimed at");
                let one = VideoClip::new(at, total, 0, Source::Slug { text: "new".into() });
                let op = Op::Insert { track: TrackRef::video(track), clip: th_edit::ops::NewClip::Video(one) };
                let single = th_edit::ops::apply(&edit, &op, &Anything).unwrap();
                let rest: Vec<_> = every_clip(&single).into_iter().filter(|c| !c.4.starts_with("new")).collect();
                prop_assert_eq!(&old, &rest, "an insert moved what was there otherwise than one insert does");
            }
            prop_assert!(document.undo());
            prop_assert_eq!(&document.edit, &edit);
            prop_assert!(!document.can_undo(), "more than one undo step");
        }
    }
}
