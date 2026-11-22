//! The frames a preview window plays, laid out as a timeline (`frames_view`):
//! each group of frames (a preview of a batch, or a folder's frames) is a
//! run of *slots* end to end, one per frame number it should have, and each
//! slot's frame is on disk or not yet. A render still going leaves slots
//! not rendered yet: they stay on the timeline as gaps, hatched, and fill in
//! as the frames land.
//!
//! Which numbers a group has slots for ([`Fill`]): exactly its frames (a
//! batch's preview frames, first / middle / last, are stills, not a range);
//! the frame list the render was submitted with (`--expect`, which the
//! Farm Manager passes); else every number from its first frame on disk to
//! its last. A slot not on disk has the file its frame will be written to,
//! named as its group's frames are (`beauty.1001.exr` makes
//! `beauty.1023.exr`), so it plays once it is there without the group being
//! opened again.
//!
//! The playback rules a preview keeps to (stepping, looping, the clock,
//! waiting for frames) are [`Transport`]'s: Qt-free and window-free, so the
//! tests check them.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::previews::Stills;

/// Which frame numbers a group has slots for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Fill {
    /// Its frames on disk, and nothing between.
    Exact,
    /// Every number from its first frame on disk to its last.
    Span,
    /// These numbers (a render's frame list), and any frame on disk besides.
    List(Vec<i64>),
}

/// One frame of a group: its number (None for a file whose name has none),
/// its file, and whether that file is there.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Slot {
    pub(crate) number: Option<i64>,
    pub(crate) path: PathBuf,
    pub(crate) present: bool,
}

/// A group of frames, played as one source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Group {
    pub(crate) name: String,
    pub(crate) slots: Vec<Slot>,
}

impl Group {
    /// Its frame files, by slot: what its source reads.
    pub(crate) fn paths(&self) -> Vec<PathBuf> {
        self.slots.iter().map(|s| s.path.clone()).collect()
    }

    /// The first slot whose frame is on disk.
    pub(crate) fn first_present(&self) -> Option<usize> {
        self.slots.iter().position(|s| s.present)
    }

    /// The movie its slots are the pictures of, when it is one.
    pub(crate) fn movie(&self) -> Option<&Path> {
        self.slots.first().map(|s| s.path.as_path()).filter(|p| is_movie(p))
    }

    /// Whether its frames are JPGs (display-referred), not EXRs.
    pub(crate) fn is_jpg(&self) -> bool {
        self.slots.iter().find(|s| s.present).is_some_and(|s| is_jpg(&s.path))
    }
}

/// Whether `path` is a movie (a finished playblast), played as one source.
pub(crate) fn is_movie(path: &Path) -> bool {
    path.extension().is_some_and(|e| th_pipe::movie::MOVIE_EXTENSIONS.iter().any(|m| e.eq_ignore_ascii_case(m)))
}

pub(crate) fn is_jpg(path: &Path) -> bool {
    path.extension().is_some_and(|e| e.eq_ignore_ascii_case("jpg") || e.eq_ignore_ascii_case("jpeg"))
}

/// A run of slots of one group, all on disk or all not: a clip on the
/// timeline, or a gap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Run {
    pub(crate) group: usize,
    /// The timeline position of its first slot.
    pub(crate) at: usize,
    /// Its first slot's index in the group.
    pub(crate) first: usize,
    pub(crate) len: usize,
    pub(crate) present: bool,
}

impl Run {
    #[cfg(test)]
    pub(crate) fn end(&self) -> usize {
        self.at + self.len
    }
}

/// Everything a preview window plays: its groups end to end.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Reel {
    pub(crate) title: String,
    pub(crate) groups: Vec<Group>,
}

/// The file frame `number` of the sequence `like` belongs to is written to:
/// `like`'s name with its trailing number replaced, padded as wide. None
/// when `like`'s name has no number.
pub(crate) fn sibling(like: &Path, number: i64) -> Option<PathBuf> {
    let name = like.file_name()?.to_str()?;
    let (stem, ext) = name.rsplit_once('.').map_or((name, None), |(s, e)| (s, Some(e)));
    let digits = stem.len() - stem.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    if digits == 0 {
        return None;
    }
    let prefix = &stem[..stem.len() - digits];
    let file = match ext {
        Some(ext) => format!("{prefix}{number:0digits$}.{ext}"),
        None => format!("{prefix}{number:0digits$}"),
    };
    Some(like.with_file_name(file))
}

/// The numbers a group with frames `on_disk` (numbered) has slots for.
pub(crate) fn slot_numbers(on_disk: &BTreeSet<i64>, fill: &Fill) -> Vec<i64> {
    match fill {
        Fill::Exact => on_disk.iter().copied().collect(),
        Fill::Span => match (on_disk.first(), on_disk.last()) {
            (Some(&first), Some(&last)) => (first..=last).collect(),
            _ => Vec::new(),
        },
        Fill::List(numbers) => {
            let mut all: BTreeSet<i64> = numbers.iter().copied().collect();
            all.extend(on_disk);
            all.into_iter().collect()
        }
    }
}

impl Reel {
    /// The reel of `stills` (frames on disk, in showing order; consecutive
    /// frames of one group name a group), its groups' slots as `fill` says.
    pub(crate) fn of(stills: &Stills, fill: &Fill) -> Self {
        let mut groups: Vec<Group> = Vec::new();
        let mut start = 0;
        while start < stills.frames.len() {
            let name = &stills.frames[start].group;
            let end = stills.frames[start..].iter().position(|s| &s.group != name).map_or(stills.frames.len(), |n| start + n);
            let frames = &stills.frames[start..end];
            let numbered: Vec<(i64, &PathBuf)> = frames.iter().filter_map(|s| s.number.map(|n| (n, &s.path))).collect();
            let on_disk: BTreeSet<i64> = numbered.iter().map(|(n, _)| *n).collect();
            let mut slots: Vec<Slot> = Vec::new();
            if let Some((_, like)) = numbered.first() {
                for number in slot_numbers(&on_disk, fill) {
                    match numbered.iter().find(|(n, _)| *n == number) {
                        Some((_, path)) => slots.push(Slot { number: Some(number), path: (*path).clone(), present: true }),
                        None => {
                            let path = sibling(like, number).unwrap_or_else(|| (*like).clone());
                            slots.push(Slot { number: Some(number), path, present: false });
                        }
                    }
                }
            }
            slots.extend(frames.iter().filter(|s| s.number.is_none()).map(|s| Slot { number: None, path: s.path.clone(), present: true }));
            groups.push(Group { name: name.clone(), slots });
            start = end;
        }
        Reel { title: stills.title.clone(), groups }
    }

    /// The reel of a render whose first frame has not landed yet (Remy,
    /// 2026-11-20): one group, named after `folder` as its frames' group
    /// will be, with a slot for each of `numbers` (the frame list it
    /// renders), none on disk. A slot's file is a stand-in, never read:
    /// once frames land the folder's real reel takes its place, and its
    /// group opens on their files.
    pub(crate) fn waiting(folder: &Path, numbers: &[i64]) -> Self {
        let name = folder.file_name().map_or_else(|| folder.display().to_string(), |n| n.to_string_lossy().into_owned());
        let slots = slot_numbers(&BTreeSet::new(), &Fill::List(numbers.to_vec()))
            .into_iter()
            .map(|number| Slot { number: Some(number), path: folder.join(format!("{name}.{number}")), present: false })
            .collect();
        Reel { title: name.clone(), groups: vec![Group { name, slots }] }
    }

    /// Whether no frame of it is on disk yet: a [`Self::waiting`] reel.
    pub(crate) fn is_waiting(&self) -> bool {
        self.rendered().0 == 0
    }

    /// Timeline positions: every group's slots.
    pub(crate) fn len(&self) -> usize {
        self.groups.iter().map(|g| g.slots.len()).sum()
    }

    /// The group and slot at timeline `position`.
    pub(crate) fn slot_at(&self, position: usize) -> Option<(usize, usize)> {
        let mut at = position;
        for (g, group) in self.groups.iter().enumerate() {
            if at < group.slots.len() {
                return Some((g, at));
            }
            at -= group.slots.len();
        }
        None
    }

    pub(crate) fn slot(&self, position: usize) -> Option<&Slot> {
        self.slot_at(position).map(|(g, i)| &self.groups[g].slots[i])
    }

    /// The frame number at timeline `position`, when its slot has one.
    pub(crate) fn number_at(&self, position: usize) -> Option<i64> {
        self.slot(position).and_then(|s| s.number)
    }

    /// Where the playhead goes to keep showing frame `number` (what it
    /// showed of another version or AOV): the first slot of that number,
    /// else of the nearest number (the lower of two as near); the start
    /// for None or a reel without numbers.
    pub(crate) fn position_of(&self, number: Option<i64>) -> usize {
        let Some(number) = number else { return 0 };
        let numbers = self.groups.iter().flat_map(|g| &g.slots).enumerate().filter_map(|(at, s)| s.number.map(|n| (at, n)));
        numbers.min_by_key(|&(at, n)| (n.abs_diff(number), n, at)).map_or(0, |(at, _)| at)
    }

    /// The groups' runs of frames on disk and of frames not yet, in order.
    pub(crate) fn runs(&self) -> Vec<Run> {
        let mut runs: Vec<Run> = Vec::new();
        let mut at = 0;
        for (g, group) in self.groups.iter().enumerate() {
            for (i, slot) in group.slots.iter().enumerate() {
                match runs.last_mut() {
                    Some(run) if run.group == g && run.present == slot.present => run.len += 1,
                    _ => runs.push(Run { group: g, at: at + i, first: i, len: 1, present: slot.present }),
                }
            }
            at += group.slots.len();
        }
        runs
    }

    /// Where the runs start, and the end: where Ctrl+Left and Ctrl+Right go.
    pub(crate) fn edit_points(&self) -> Vec<usize> {
        let mut points: Vec<usize> = self.runs().iter().map(|r| r.at).collect();
        points.push(self.len());
        points.dedup();
        points
    }

    /// Frames on disk, of how many slots.
    pub(crate) fn rendered(&self) -> (usize, usize) {
        (self.groups.iter().flat_map(|g| &g.slots).filter(|s| s.present).count(), self.len())
    }

    /// Whether `other` has the same groups reading the same files (only
    /// which are on disk may differ): it plays through the same sources.
    #[cfg(test)]
    pub(crate) fn same_files(&self, other: &Reel) -> bool {
        self.groups.len() == other.groups.len()
            && self.groups.iter().zip(&other.groups).all(|(a, b)| a.name == b.name && a.paths() == b.paths())
    }
}

/// What a playback tick did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Tick {
    /// Nothing to do yet: the next frame is not due.
    Waiting,
    /// Moved to this position.
    Moved(usize),
    /// The next frame is due but not decoded: the clock holds until it is.
    Stalled,
    /// Played to the end and stopped.
    Ended,
}

/// How many recent frame times the achieved rate is measured over.
const RATE_WINDOW: usize = 24;

/// The playhead and its clock: play, pause, step, loop, at a frame rate.
/// The clock waits for a frame that is not decoded yet, so every frame
/// plays (a slow file server plays slower, never skipping); the achieved
/// rate says so.
#[derive(Debug, Clone)]
pub(crate) struct Transport {
    pub(crate) position: usize,
    pub(crate) len: usize,
    pub(crate) fps: u32,
    pub(crate) playing: bool,
    pub(crate) looping: bool,
    /// When playback (re)started counting, and from which position.
    clock: (Instant, usize),
    /// When the last frames were shown while playing, oldest first.
    shown_at: Vec<Instant>,
    /// The last tick waited on a frame.
    pub(crate) stalled: bool,
}

impl Transport {
    pub(crate) fn new(len: usize, fps: u32, now: Instant) -> Self {
        Transport {
            position: 0,
            len,
            fps: fps.max(1),
            playing: false,
            looping: true,
            clock: (now, 0),
            shown_at: Vec::new(),
            stalled: false,
        }
    }

    fn last(&self) -> usize {
        self.len.saturating_sub(1)
    }

    /// Start or stop. Starting on the last frame without looping plays from
    /// the start.
    pub(crate) fn set_playing(&mut self, playing: bool, now: Instant) {
        if playing && !self.looping && self.position >= self.last() {
            self.position = 0;
        }
        self.playing = playing && self.len > 0;
        self.clock = (now, self.position);
        self.shown_at.clear();
        self.stalled = false;
    }

    pub(crate) fn toggle(&mut self, now: Instant) {
        self.set_playing(!self.playing, now);
    }

    /// Stop and go to `position` (clamped to the last frame).
    pub(crate) fn seek(&mut self, position: usize, now: Instant) {
        self.set_playing(false, now);
        self.position = position.min(self.last());
    }

    /// Stop and step `by` frames, clamped to the ends.
    pub(crate) fn step(&mut self, by: i64, now: Instant) {
        let to = (self.position as i64).saturating_add(by).clamp(0, self.last() as i64);
        self.seek(to as usize, now);
    }

    /// Go to `position` (clamped) without stopping: what is shown was
    /// swapped for another version or AOV, and playback goes on from there.
    pub(crate) fn move_to(&mut self, position: usize, now: Instant) {
        self.position = position.min(self.last());
        self.clock = (now, self.position);
        self.shown_at.clear();
        self.stalled = false;
    }

    /// The reel changed length (frames landed past the end); the playhead
    /// stays, or moves to the new last frame.
    pub(crate) fn set_len(&mut self, len: usize) {
        self.len = len;
        self.position = self.position.min(self.last());
    }

    /// The position the clock says is due at `now`.
    pub(crate) fn due(&self, now: Instant) -> usize {
        let (started, from) = self.clock;
        from + (now.saturating_duration_since(started).as_secs_f64() * f64::from(self.fps) + 1e-6) as usize
    }

    /// One tick while playing: on to the next frame when it is due and
    /// `ready` (decoded, or nothing to decode: a gap, a frame that failed),
    /// round to the start at the end when looping.
    pub(crate) fn tick(&mut self, now: Instant, ready: impl Fn(usize) -> bool) -> Tick {
        if !self.playing {
            return Tick::Waiting;
        }
        // Stalled, the next frame is checked on every tick: it is overdue.
        if !self.stalled && self.position >= self.due(now) {
            return Tick::Waiting;
        }
        let next = if self.position + 1 >= self.len {
            if !self.looping {
                self.playing = false;
                self.stalled = false;
                return Tick::Ended;
            }
            0
        } else {
            self.position + 1
        };
        if !ready(next) {
            self.stalled = true;
            return Tick::Stalled;
        }
        // After a wait (or round the loop) the clock counts from this frame
        // shown now: playback goes on at its rate, not racing to catch up.
        if self.stalled || next == 0 {
            self.clock = (now, next);
        }
        self.position = next;
        self.stalled = false;
        self.shown_at.push(now);
        if self.shown_at.len() > RATE_WINDOW {
            self.shown_at.remove(0);
        }
        Tick::Moved(next)
    }

    /// The rate frames were shown at lately, while playing; None until a
    /// few have been.
    pub(crate) fn achieved_fps(&self) -> Option<f64> {
        let (first, last) = (self.shown_at.first()?, self.shown_at.last()?);
        let span = last.saturating_duration_since(*first);
        (self.shown_at.len() >= 4 && span > Duration::ZERO).then(|| (self.shown_at.len() - 1) as f64 / span.as_secs_f64())
    }

    /// How often to tick while playing: twice a frame.
    pub(crate) fn tick_every(&self) -> Duration {
        Duration::from_secs_f64(0.5 / f64::from(self.fps))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::previews::Still;
    use proptest::prelude::*;

    fn stills(groups: &[(&str, &[i64])]) -> Stills {
        let frames = groups
            .iter()
            .flat_map(|(name, numbers)| {
                numbers.iter().map(move |n| Still {
                    group: (*name).to_owned(),
                    number: Some(*n),
                    path: PathBuf::from(format!("R:/{name}/{name}.{n:04}.exr")),
                })
            })
            .collect();
        Stills { title: "t".into(), frames, exact: false }
    }

    #[test]
    fn missing_frames_are_named_as_their_group_s() {
        assert_eq!(sibling(Path::new("R:/v/beauty.1001.exr"), 1023), Some(PathBuf::from("R:/v/beauty.1023.exr")));
        assert_eq!(sibling(Path::new("R:/v/beauty.0001.exr"), 12345), Some(PathBuf::from("R:/v/beauty.12345.exr")));
        assert_eq!(sibling(Path::new("R:/v/plate_07.jpg"), 9), Some(PathBuf::from("R:/v/plate_09.jpg")));
        assert_eq!(sibling(Path::new("R:/v/still.exr"), 9), None);
    }

    proptest! {
        /// Swapping keeps the frame: the playhead lands on the slot of the
        /// same number when the new reel has it, else on a nearest one; and
        /// a move keeps playing or paused as it was.
        #[test]
        fn a_swap_keeps_the_frame_and_the_playback(
            numbers in prop::collection::btree_set(1i64..80, 1..20), wanted in 1i64..80, playing in any::<bool>(),
        ) {
            let numbers: Vec<i64> = numbers.into_iter().collect();
            let reel = Reel::of(&stills(&[("beauty", &numbers)]), &Fill::Exact);
            let at = reel.position_of(Some(wanted));
            let got = reel.number_at(at).unwrap();
            let nearest = numbers.iter().map(|n| n.abs_diff(wanted)).min().unwrap();
            prop_assert_eq!(got.abs_diff(wanted), nearest);
            if numbers.contains(&wanted) {
                prop_assert_eq!(got, wanted);
            }
            prop_assert_eq!(reel.position_of(None), 0);
            let now = Instant::now();
            let mut transport = Transport::new(reel.len(), 24, now);
            transport.set_playing(playing, now);
            transport.move_to(at + 100, now);
            prop_assert_eq!(transport.playing, playing);
            prop_assert_eq!(transport.position, reel.len() - 1);
            transport.move_to(at, now);
            prop_assert_eq!((transport.position, transport.playing), (at, playing));
        }
    }

    #[test]
    fn a_movie_is_one_group_of_its_pictures() {
        let path = PathBuf::from("R:/pb/a_v0001.mp4");
        let frames = (0..3).map(|i| Still { group: "a_v0001.mp4".into(), number: Some(1001 + i), path: path.clone() }).collect();
        let reel = Reel::of(&Stills { title: "a_v0001.mp4".into(), frames, exact: true }, &Fill::Exact);
        assert_eq!((reel.groups.len(), reel.len(), reel.rendered()), (1, 3, (3, 3)));
        assert_eq!(reel.groups[0].movie(), Some(path.as_path()));
        assert!(!reel.groups[0].is_jpg());
        assert_eq!(Reel::of(&stills(&[("beauty", &[1, 2])]), &Fill::Span).groups[0].movie(), None);
    }

    #[test]
    fn a_span_shows_the_frames_not_rendered_between() {
        let reel = Reel::of(&stills(&[("beauty", &[1001, 1002, 1005])]), &Fill::Span);
        assert_eq!(reel.len(), 5);
        let present: Vec<bool> = reel.groups[0].slots.iter().map(|s| s.present).collect();
        assert_eq!(present, [true, true, false, false, true]);
        assert_eq!(reel.groups[0].slots[2].path, PathBuf::from("R:/beauty/beauty.1003.exr"));
        let runs = reel.runs();
        assert_eq!(runs.len(), 3);
        assert_eq!((runs[1].at, runs[1].len, runs[1].present), (2, 2, false));
        assert_eq!(reel.edit_points(), [0, 2, 4, 5]);
        assert_eq!(reel.rendered(), (3, 5));
    }

    #[test]
    fn exact_previews_are_stills_end_to_end() {
        let reel = Reel::of(&stills(&[("a", &[1050, 1001, 1100]), ("b", &[1, 2])]), &Fill::Exact);
        assert_eq!(reel.len(), 5);
        assert_eq!(reel.groups[0].slots.iter().map(|s| s.number.unwrap()).collect::<Vec<_>>(), [1001, 1050, 1100]);
        assert_eq!(reel.slot_at(3), Some((1, 0)));
        assert_eq!(reel.slot_at(5), None);
        assert_eq!(reel.runs().len(), 2, "a run per group, even back to back");
    }

    #[test]
    fn an_expected_list_leaves_room_for_frames_to_come() {
        let reel = Reel::of(&stills(&[("beauty", &[1003])]), &Fill::List((1001..=1010).collect()));
        assert_eq!(reel.len(), 10);
        assert_eq!(reel.groups[0].first_present(), Some(2));
        // The frame lands: the same files, one more on disk.
        let more = Reel::of(&stills(&[("beauty", &[1003, 1004])]), &Fill::List((1001..=1010).collect()));
        assert!(reel.same_files(&more));
        assert_ne!(reel, more);
        let spanned = Reel::of(&stills(&[("beauty", &[1003, 1004])]), &Fill::Span);
        assert!(!reel.same_files(&spanned));
    }

    proptest! {
        /// Before the first frame, a waiting reel has a slot per frame of
        /// the list, in order, none on disk; once frames land, the folder's
        /// reel has the same numbers, under the same group name, with
        /// those frames on disk.
        #[test]
        fn a_waiting_reel_is_the_frame_list_as_gaps(
            list in prop::collection::vec(990i64..1030, 1..30),
            landed in prop::collection::btree_set(990i64..1030, 1..6),
        ) {
            let waiting = Reel::waiting(Path::new("R:/beauty"), &list);
            let wanted: Vec<i64> = list.iter().copied().collect::<BTreeSet<i64>>().into_iter().collect();
            prop_assert_eq!(waiting.groups.len(), 1);
            prop_assert_eq!(waiting.groups[0].slots.iter().filter_map(|s| s.number).collect::<Vec<_>>(), wanted.clone());
            prop_assert!(waiting.is_waiting());
            prop_assert_eq!(waiting.rendered(), (0, wanted.len()));
            prop_assert_eq!(waiting.clone(), Reel::waiting(Path::new("R:/beauty"), &list), "read again, the same: nothing changes");
            let numbers: Vec<i64> = landed.iter().copied().collect();
            let real = Reel::of(&stills(&[("beauty", &numbers)]), &Fill::List(list.clone()));
            prop_assert!(!real.is_waiting());
            prop_assert_eq!(&real.groups[0].name, &waiting.groups[0].name);
            let all: BTreeSet<i64> = wanted.iter().copied().chain(numbers.iter().copied()).collect();
            prop_assert_eq!(real.groups[0].slots.iter().filter_map(|s| s.number).collect::<BTreeSet<_>>(), all);
        }

        /// Whatever frames are on disk and however the slots are filled,
        /// every frame on disk has a slot that is present, slots go up by
        /// number, the runs cover every position once, alternating within a
        /// group, and a slot not on disk is never present.
        #[test]
        fn runs_cover_the_reel(
            groups in prop::collection::vec(prop::collection::btree_set(990i64..1030, 1..12), 1..4),
            list in prop::collection::vec(995i64..1025, 0..20),
            fill in 0u8..3,
        ) {
            let named: Vec<(String, Vec<i64>)> = groups.iter().enumerate().map(|(i, g)| (format!("g{i}"), g.iter().copied().collect())).collect();
            let refs: Vec<(&str, &[i64])> = named.iter().map(|(n, f)| (n.as_str(), f.as_slice())).collect();
            let fill = match fill { 0 => Fill::Exact, 1 => Fill::Span, _ => Fill::List(list) };
            let reel = Reel::of(&stills(&refs), &fill);
            for (group, on_disk) in reel.groups.iter().zip(&groups) {
                let present: BTreeSet<i64> = group.slots.iter().filter(|s| s.present).filter_map(|s| s.number).collect();
                prop_assert_eq!(&present, on_disk);
                let numbers: Vec<i64> = group.slots.iter().filter_map(|s| s.number).collect();
                prop_assert!(numbers.windows(2).all(|w| w[0] < w[1]));
            }
            let runs = reel.runs();
            let mut at = 0;
            for (i, run) in runs.iter().enumerate() {
                prop_assert_eq!(run.at, at);
                prop_assert!(run.len > 0);
                for p in run.at..run.end() {
                    let (g, s) = reel.slot_at(p).unwrap();
                    prop_assert_eq!(g, run.group);
                    prop_assert_eq!(s, run.first + (p - run.at));
                    prop_assert_eq!(reel.groups[g].slots[s].present, run.present);
                }
                if i > 0 && runs[i - 1].group == run.group {
                    prop_assert_ne!(runs[i - 1].present, run.present);
                }
                at = run.end();
            }
            prop_assert_eq!(at, reel.len());
        }

        /// Steps land on the reel and clamp at its ends.
        #[test]
        fn steps_clamp_to_the_reel(len in 1usize..500, at in 0usize..500, by in -1000i64..1000) {
            let now = Instant::now();
            let mut t = Transport::new(len, 24, now);
            t.seek(at, now);
            prop_assert!(t.position < len);
            let from = t.position;
            t.step(by, now);
            prop_assert_eq!(t.position as i64, (from as i64 + by).clamp(0, len as i64 - 1));
            prop_assert!(!t.playing);
        }

        /// Played long enough with every frame ready, it visits every
        /// frame in order and loops round; never past the end.
        #[test]
        fn playing_visits_every_frame_in_order(len in 1usize..60, fps in 1u32..60, looping in any::<bool>()) {
            let start = Instant::now();
            let mut t = Transport::new(len, fps, start);
            t.looping = looping;
            t.set_playing(true, start);
            let mut seen = vec![0usize];
            let mut now = start;
            for _ in 0..(len * 4 + 8) {
                now += t.tick_every();
                match t.tick(now, |_| true) {
                    Tick::Moved(p) => seen.push(p),
                    Tick::Ended => break,
                    _ => {}
                }
                prop_assert!(t.position < len);
            }
            for w in seen.windows(2) {
                prop_assert!(w[1] == w[0] + 1 || (looping && w[1] == 0 && w[0] == len - 1), "{:?}", seen);
            }
            if !looping {
                prop_assert_eq!(*seen.last().unwrap(), len - 1);
                prop_assert!(!t.playing);
            } else if len > 1 {
                prop_assert!(seen.len() > len, "looped round");
            }
        }
    }

    #[test]
    fn the_clock_waits_for_a_frame_not_decoded() {
        let start = Instant::now();
        let mut t = Transport::new(10, 24, start);
        t.set_playing(true, start);
        let frame = Duration::from_secs_f64(1.0 / 24.0);
        assert_eq!(t.tick(start, |_| true), Tick::Waiting, "nothing due at once");
        assert_eq!(t.tick(start + frame, |p| p != 1), Tick::Stalled);
        assert!(t.stalled);
        assert_eq!(t.position, 0);
        // Much later, it is decoded: it shows at once, one frame on, and the
        // clock counts from there rather than racing to where it would have been.
        let later = start + frame * 50;
        assert_eq!(t.tick(later, |_| true), Tick::Moved(1));
        assert!(!t.stalled);
        assert_eq!(t.tick(later + frame / 2, |_| true), Tick::Waiting);
        assert_eq!(t.tick(later + frame, |_| true), Tick::Moved(2));
    }

    #[test]
    fn fps_timing_and_the_rate_achieved() {
        let start = Instant::now();
        let mut t = Transport::new(100, 25, start);
        assert_eq!(t.due(start + Duration::from_millis(1000)), 25);
        assert_eq!(t.tick_every(), Duration::from_millis(20));
        t.set_playing(true, start);
        assert_eq!(t.achieved_fps(), None);
        let mut now = start;
        for _ in 0..40 {
            now += Duration::from_millis(40);
            t.tick(now, |_| true);
        }
        let rate = t.achieved_fps().unwrap();
        assert!((rate - 25.0).abs() < 0.5, "{rate}");
        // Frames that arrive one every 100 ms play at about 10 fps, every one.
        let mut slow = Transport::new(100, 25, start);
        slow.set_playing(true, start);
        let mut now = start;
        let mut shown = Vec::new();
        for _ in 0..200 {
            now += Duration::from_millis(20);
            if let Tick::Moved(p) = slow.tick(now, |p| now >= start + Duration::from_millis(100) * p as u32) {
                shown.push(p);
            }
        }
        let rate = slow.achieved_fps().unwrap();
        assert!((8.0..=10.5).contains(&rate), "{rate}");
        assert!(shown.windows(2).all(|w| w[1] == w[0] + 1), "no frame skipped");
    }

    #[test]
    fn play_at_the_end_starts_over_unless_looping() {
        let now = Instant::now();
        let mut t = Transport::new(5, 24, now);
        t.looping = false;
        t.seek(4, now);
        t.toggle(now);
        assert!(t.playing);
        assert_eq!(t.position, 0);
        let mut empty = Transport::new(0, 24, now);
        empty.toggle(now);
        assert!(!empty.playing, "nothing to play");
        t.set_len(2);
        assert!(t.position <= 1);
    }
}
