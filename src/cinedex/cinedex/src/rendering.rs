//! Renders on their way: which frames of a shot's unfinished render are on
//! disk, shown on its clips as a thin strip along their bottom edge and
//! counted in their second line ("lighting v0007: 34/120 rendered").
//!
//! A render with frames missing is not a take (`th_pipe::discovery`), so a
//! clip plays what its rule picks of the finished ones, or nothing, until
//! the last frame lands; then the take arrives the usual way. Until then
//! this says how far the render is. It is not the frame cache: the ruler's
//! strip says which frames of what plays are decoded in memory, this one
//! which frames of what is coming exist at all.
//!
//! The render a shot waits for is `th_pipe::unfinished`'s: the newest
//! version of a render layer when that version records its frame range and
//! is published (not a check render) but lacks frames; of several, the one
//! best available would pick once done. An older unfinished version under
//! a finished one is an abandoned render and is not shown.
//!
//! [`Watch`] keeps it cheap on a file server. Finding a shot's unfinished
//! render reads its render folders (a listing per layer), so only a few
//! shots are looked at per poll and each again only after a while; a
//! render being watched costs one listing of its frames folder per poll,
//! and is dropped the poll it is complete. All of it runs off the UI
//! thread ([`run`]), one poll at a time.

use std::collections::BTreeMap;

use th_edit::document::{Frame as EditFrame, Source, TrackRef};
use th_pipe::Project;
pub use th_pipe::unfinished::Unfinished;
use th_pipe::unfinished::find;

/// Shots looked at for an unfinished render per poll.
pub const LOOKS_AT_ONCE: usize = 2;
/// Polls before a shot with no unfinished render is looked at again: five
/// minutes of the editor's five second polls.
pub const LOOK_AGAIN: u32 = 60;
/// The strip's height on a clip, in pixels.
pub const STRIP: f32 = 3.0;

/// A clip's second line with the render it waits for said after it.
pub fn detail(detail: &str, unfinished: Option<&Unfinished>) -> String {
    match unfinished {
        None => detail.to_owned(),
        Some(u) if detail.is_empty() => u.label(),
        Some(u) => format!("{detail}  ·  {}", u.label()),
    }
}

/// The runs of a clip's frames that are among `frames` (ascending): each
/// `(start, end)` counted from the clip's first frame, `end` not included.
/// A clip `duration` long shows its source's frames from `source_in` on.
pub fn runs(frames: &[i64], source_in: EditFrame, duration: EditFrame) -> Vec<(EditFrame, EditFrame)> {
    let mut runs: Vec<(EditFrame, EditFrame)> = Vec::new();
    let from = frames.partition_point(|f| *f < source_in);
    for frame in frames[from..].iter().take_while(|f| **f < source_in + duration) {
        let at = frame - source_in;
        match runs.last_mut() {
            Some(run) if run.1 == at => run.1 = at + 1,
            Some(run) if run.1 > at => {}
            _ => runs.push((at, at + 1)),
        }
    }
    runs
}

/// One clip's strip, in edit frames: where its render is to have frames,
/// and where it has them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Strip {
    pub track: TrackRef,
    pub expected: Vec<(EditFrame, EditFrame)>,
    pub there: Vec<(EditFrame, EditFrame)>,
}

/// The strip of a clip at `at` on `track` showing `duration` frames of its
/// shot from `source_in`; None when the render has no frame the clip shows.
pub fn strip(unfinished: &Unfinished, track: TrackRef, at: EditFrame, source_in: EditFrame, duration: EditFrame) -> Option<Strip> {
    let placed = |frames: &[i64]| runs(frames, source_in, duration).into_iter().map(|(a, b)| (at + a, at + b)).collect::<Vec<_>>();
    let expected = placed(&unfinished.expected);
    (!expected.is_empty()).then(|| Strip { track, expected, there: placed(&unfinished.there) })
}

/// The strip's bars, left to right: each one's left edge and width in
/// pixels, and whether its frames are there (lit) or only expected.
/// `x` is where an edit frame's left edge is drawn. The expected bars come
/// first, so the lit ones are drawn over them; a bar is at least a pixel
/// wide, and stops a pixel short of the next frame as a clip does.
pub fn bars(strip: &Strip, x: impl Fn(EditFrame) -> f32) -> Vec<(f32, f32, bool)> {
    let bar = |&(start, end): &(EditFrame, EditFrame), there: bool| {
        let (x0, x1) = (x(start), x(end));
        (x0, (x1 - x0 - 1.0).max(1.0), there)
    };
    strip.expected.iter().map(|run| bar(run, false)).chain(strip.there.iter().map(|run| bar(run, true))).collect()
}

/// The distinct shots among `sources`, in order of first use.
pub fn shots_of<'a>(sources: impl IntoIterator<Item = &'a Source>) -> Vec<String> {
    let mut shots: Vec<String> = Vec::new();
    for source in sources {
        if let Source::Shot { shot, .. } = source
            && !shots.contains(shot)
        {
            shots.push(shot.clone());
        }
    }
    shots
}

/// What one poll is to do for a shot.
#[derive(Debug, Clone, PartialEq, Eq)]
// A handful per poll, moved once: not worth a box each.
#[allow(clippy::large_enum_variant)]
pub enum Job {
    /// Look for its unfinished render.
    Find(String),
    /// List the frames of the one being watched.
    Relist(String, Unfinished),
}

#[derive(Debug, Clone, PartialEq, Eq)]
// A handful per poll, moved once: not worth a box each.
#[allow(clippy::large_enum_variant)]
enum Seen {
    Unfinished(Unfinished),
    /// Nothing unfinished when last looked at, at poll `at`.
    Nothing {
        at: u32,
    },
}

/// What a poll found: each shot it looked at, and its unfinished render.
pub type Found = Vec<(String, Option<Unfinished>)>;

/// The cut's shots and the unfinished render each waits for.
#[derive(Debug, Clone, Default)]
pub struct Watch {
    shots: BTreeMap<String, Seen>,
    /// A poll is out: none is started until it is back.
    busy: bool,
    polls: u32,
}

impl Watch {
    /// The unfinished render `source` waits for, when it is a shot.
    pub fn of_source(&self, source: &Source) -> Option<&Unfinished> {
        match source {
            Source::Shot { shot, .. } => self.of(shot),
            _ => None,
        }
    }

    /// The unfinished render `shot` waits for.
    pub fn of(&self, shot: &str) -> Option<&Unfinished> {
        match self.shots.get(shot) {
            Some(Seen::Unfinished(unfinished)) => Some(unfinished),
            _ => None,
        }
    }

    /// This poll's work for a cut of `shots`: the frames of every render
    /// being watched listed again, and up to [`LOOKS_AT_ONCE`] shots looked
    /// at for one, those never looked at first, then those not looked at
    /// for [`LOOK_AGAIN`] polls, longest ago first. Nothing while the poll
    /// before is still out. Shots no longer in the cut are forgotten.
    pub fn plan(&mut self, shots: &[String]) -> Vec<Job> {
        if self.busy {
            return Vec::new();
        }
        self.polls = self.polls.wrapping_add(1);
        self.shots.retain(|shot, _| shots.contains(shot));
        let mut jobs = Vec::new();
        let mut looks: Vec<(Option<u32>, &String)> = Vec::new();
        let distinct: std::collections::BTreeSet<&String> = shots.iter().collect();
        for shot in distinct {
            match self.shots.get(shot) {
                Some(Seen::Unfinished(unfinished)) => jobs.push(Job::Relist(shot.clone(), unfinished.clone())),
                Some(Seen::Nothing { at }) if self.polls.wrapping_sub(*at) >= LOOK_AGAIN => looks.push((Some(*at), shot)),
                Some(Seen::Nothing { .. }) => {}
                None => looks.push((None, shot)),
            }
        }
        // Never looked at (None) sorts first; then the longest ago.
        looks.sort_by_key(|(at, _)| at.map(|at| self.polls.wrapping_sub(at)).map(std::cmp::Reverse));
        jobs.extend(looks.into_iter().take(LOOKS_AT_ONCE).map(|(_, shot)| Job::Find(shot.clone())));
        self.busy = !jobs.is_empty();
        jobs
    }

    /// A poll is back with `found`. A render with every frame is watched
    /// no longer: its take arrives as any new take does.
    pub fn apply(&mut self, found: Found) {
        self.busy = false;
        for (shot, unfinished) in found {
            let seen = match unfinished.filter(|u| !u.complete()) {
                Some(unfinished) => Seen::Unfinished(unfinished),
                None => Seen::Nothing { at: self.polls },
            };
            self.shots.insert(shot, seen);
        }
    }
}

/// Do `jobs` (the listings; off the UI thread). A project that does not
/// open finds nothing: every job still answers, so the watch goes on.
pub fn run(project: Result<Project, String>, jobs: Vec<Job>) -> Found {
    jobs.into_iter()
        .map(|job| match job {
            Job::Relist(shot, unfinished) => (shot, Some(unfinished.relisted())),
            Job::Find(shot) => {
                let uri = th_pipe::Uri::parse_address(&shot).ok();
                let found = project.as_ref().ok().zip(uri).and_then(|(project, uri)| find(project, &uri));
                (shot, found)
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::collections::BTreeSet;
    use std::path::PathBuf;
    use th_pipe::discovery::Aov;

    fn unfinished(expected: Vec<i64>, there: Vec<i64>) -> Unfinished {
        Unfinished {
            department: "lighting".into(),
            layer: "default".into(),
            version: "v0007".into(),
            aov: Aov { path: PathBuf::from("nowhere"), name: "sh010_default_beauty_v0007".into(), suffix: "exr".into() },
            expected,
            there,
            playblast: false,
        }
    }

    fn frames() -> impl Strategy<Value = (Vec<i64>, Vec<i64>)> {
        (990i64..1010, 1usize..60, 1i64..4).prop_flat_map(|(first, count, step)| {
            let expected: Vec<i64> = (0..count as i64).map(|i| first + i * step).collect();
            let picks = prop::collection::vec(any::<bool>(), count);
            (Just(expected), picks).prop_map(|(expected, picks)| {
                let there = expected.iter().zip(picks).filter(|(_, on)| *on).map(|(f, _)| *f).collect();
                (expected, there)
            })
        })
    }

    proptest! {
        /// A clip's runs cover exactly the frames it shows that are in the
        /// list, each frame once, as the fewest runs in order.
        #[test]
        fn runs_are_the_clips_frames_in_the_list((_, there) in frames(), source_in in 980i64..1100, duration in 1i64..90) {
            let got = runs(&there, source_in, duration);
            let covered: Vec<i64> = got.iter().flat_map(|(a, b)| *a..*b).collect();
            let want: Vec<i64> = (0..duration).filter(|k| there.contains(&(source_in + k))).collect();
            prop_assert_eq!(covered, want);
            prop_assert!(got.iter().all(|(a, b)| a < b));
            prop_assert!(got.windows(2).all(|w| w[0].1 < w[1].0), "runs touch or overlap: {:?}", got);
        }

        /// A strip is its clip's runs moved to where the clip is; a clip
        /// showing none of the render's frames has none; the frames there
        /// are always among those expected.
        #[test]
        fn a_strip_sits_on_its_clip((expected, there) in frames(), at in 0i64..500, source_in in 980i64..1100, duration in 1i64..90) {
            let u = unfinished(expected.clone(), there.clone());
            let track = TrackRef::video(0);
            let shows = |frames: &[i64]| -> BTreeSet<i64> { (0..duration).filter(|k| frames.contains(&(source_in + k))).map(|k| at + k).collect() };
            match strip(&u, track, at, source_in, duration) {
                None => prop_assert!(shows(&expected).is_empty()),
                Some(strip) => {
                    let cover = |runs: &[(i64, i64)]| -> BTreeSet<i64> { runs.iter().flat_map(|(a, b)| *a..*b).collect() };
                    prop_assert_eq!(cover(&strip.expected), shows(&expected));
                    prop_assert_eq!(cover(&strip.there), shows(&there));
                    prop_assert!(cover(&strip.there).is_subset(&cover(&strip.expected)));
                    prop_assert_eq!(strip.track, track);
                }
            }
        }

        /// A strip's bars: the expected runs dark first, then the lit ones
        /// over them, each from its first frame's left edge to a pixel
        /// short of the frame after its last, never thinner than a pixel.
        #[test]
        fn bars_are_drawn_expected_then_lit((expected, there) in frames(), at in 0i64..500, source_in in 980i64..1100, duration in 1i64..90, px in 0.05f32..12.0) {
            let u = unfinished(expected, there);
            let Some(strip) = strip(&u, TrackRef::video(1), at, source_in, duration) else { return Ok(()) };
            let x = |frame: i64| 120.0 + px * frame as f32;
            let got = bars(&strip, x);
            prop_assert_eq!(got.len(), strip.expected.len() + strip.there.len());
            let lit: Vec<bool> = got.iter().map(|b| b.2).collect();
            let mut sorted = lit.clone();
            sorted.sort();
            prop_assert_eq!(&lit, &sorted, "expected bars first");
            for ((left, wide, _), (start, end)) in got.iter().zip(strip.expected.iter().chain(&strip.there)) {
                prop_assert_eq!(*left, x(*start));
                prop_assert!(*wide >= 1.0);
                prop_assert!((*wide - (x(*end) - x(*start) - 1.0).max(1.0)).abs() < 1e-3);
                // Within its clip.
                prop_assert!(*left >= x(at) && *left <= x(at + duration));
            }
        }

        /// The cut's shots are each shot once, in order; media and slugs
        /// are none.
        #[test]
        fn the_cuts_shots_are_each_once(picks in prop::collection::vec(0usize..6, 0..12)) {
            let sources: Vec<Source> = picks.iter().map(|i| match i {
                4 => Source::Slug { text: "s".into() },
                5 => Source::Media { item: "m".into(), version: th_edit::document::VersionRule::Latest },
                n => Source::Shot { shot: format!("entity:/shots/S/0{n}0"), take: th_edit::document::Rule::best() },
            }).collect();
            let got = shots_of(&sources);
            let mut want: Vec<String> = Vec::new();
            for i in picks.iter().filter(|i| **i < 4) {
                let shot = format!("entity:/shots/S/0{i}0");
                if !want.contains(&shot) { want.push(shot); }
            }
            prop_assert_eq!(got, want);
        }

        /// The count and the label say how many of its frames are there;
        /// frames on disk that are not its own are not counted.
        #[test]
        fn the_label_counts_its_own_frames((expected, there) in frames(), stray in prop::collection::vec(0i64..2000, 0..5)) {
            let mut found: Vec<i64> = there.iter().copied().chain(stray.iter().copied()).collect();
            found.sort();
            found.dedup();
            let own = th_pipe::unfinished::present(&expected, &found);
            let want: Vec<i64> = expected.iter().copied().filter(|f| found.contains(f)).collect();
            prop_assert_eq!(&own, &want);
            let u = unfinished(expected.clone(), own.clone());
            prop_assert_eq!(detail("", Some(&u)), u.label());
            prop_assert_eq!(detail("animation v0004", Some(&u)), format!("animation v0004  ·  {}", u.label()));
            prop_assert_eq!(detail("animation v0004", None), "animation v0004");
        }

        /// The watch: a shot is looked at once, then again only after
        /// LOOK_AGAIN polls; at most LOOKS_AT_ONCE shots a poll; a render
        /// being watched is listed every poll until it is complete, then
        /// not at all; no poll starts while one is out; shots that left
        /// the cut are forgotten.
        #[test]
        fn the_watch_looks_seldom_and_lists_what_it_watches(
            shots in prop::collection::btree_set("[a-f]", 1..6),
            rendering in prop::collection::btree_set("[a-f]", 0..4),
            finishes_at in 2u32..20,
            polls in 20u32..150,
        ) {
            let shots: Vec<String> = shots.into_iter().collect();
            let mut watch = Watch::default();
            let mut looked: BTreeMap<String, Vec<u32>> = BTreeMap::new();
            let mut listed: BTreeMap<String, Vec<u32>> = BTreeMap::new();
            for poll in 1..=polls {
                let jobs = watch.plan(&shots);
                if !jobs.is_empty() {
                    prop_assert_eq!(watch.plan(&shots), Vec::new(), "a second poll while one is out");
                }
                let finds = jobs.iter().filter(|j| matches!(j, Job::Find(_))).count();
                prop_assert!(finds <= LOOKS_AT_ONCE);
                let mut found = Found::new();
                for job in jobs {
                    let done = poll >= finishes_at;
                    let state = |shot: &str| rendering.contains(shot).then(|| unfinished(vec![1, 2, 3], if done { vec![1, 2, 3] } else { vec![1] }));
                    match job {
                        Job::Find(shot) => {
                            looked.entry(shot.clone()).or_default().push(poll);
                            found.push((shot.clone(), state(&shot)));
                        }
                        Job::Relist(shot, was) => {
                            prop_assert!(!was.complete());
                            listed.entry(shot.clone()).or_default().push(poll);
                            found.push((shot.clone(), state(&shot)));
                        }
                    }
                }
                if found.is_empty() {
                    // Nothing went out, so nothing is awaited.
                    prop_assert!(!watch.busy);
                } else {
                    watch.apply(found);
                }
                for shot in &shots {
                    let waits = watch.of(shot).is_some();
                    if waits { prop_assert!(rendering.contains(shot) && poll < finishes_at); }
                }
            }
            for shot in &shots {
                let looks = looked.get(shot).cloned().unwrap_or_default();
                // Everyone is looked at early: the cut's shots, a few a poll.
                prop_assert!(!looks.is_empty() && looks[0] <= shots.len().div_ceil(LOOKS_AT_ONCE) as u32, "{} first looked at {:?}", shot, looks);
                // A shot found rendering is listed every poll from the next
                // one on until the poll that sees it complete.
                let lists = listed.get(shot).cloned().unwrap_or_default();
                let want: Vec<u32> = if rendering.contains(shot) && looks[0] < finishes_at { (looks[0] + 1..=finishes_at).collect() } else { Vec::new() };
                prop_assert_eq!(&lists, &want, "{} listed", shot);
                // Looks at a shot with nothing unfinished are LOOK_AGAIN apart.
                for pair in looks.windows(2) {
                    let between_lists = lists.iter().any(|l| *l > pair[0] && *l <= pair[1]);
                    prop_assert!(between_lists || pair[1] - pair[0] >= LOOK_AGAIN, "{} looked at {:?}", shot, looks);
                }
            }
            // A shot cut from the edit is forgotten at the next poll.
            let fewer = &shots[..shots.len() - 1];
            watch.plan(fewer);
            prop_assert!(watch.of(&shots[shots.len() - 1]).is_none());
            prop_assert!(!watch.shots.contains_key(&shots[shots.len() - 1]));
        }
    }
}
