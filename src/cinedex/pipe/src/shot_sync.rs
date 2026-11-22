//! Shot frames that follow the master edit's trims (Remy's RFE,
//! 2026-11-22): with Edit > Sync Shot Ranges on, trimming a shot's clip in
//! the master edit sets the shot's own `frame_start` / `frame_end` there and
//! then, instead of waiting for the master's publish ([`crate::master`])
//! to offer the range change.
//!
//! A shot's range is its one clip's source frames (`master::shot_uses`),
//! so a trim of the tail moves `frame_end` and a trim of the head moves
//! `frame_start`: frame 1001 of the shot stays frame 1001 of its renders,
//! and a head trimmed by 8 starts the shot on 1009. Only the end the clip
//! moved is written ([`synced`]); the other keeps the shot's value, even
//! where it differs from the clip (the publish still offers that). A slip
//! moves both ends, a roll one end of each of two shots. An undo moves the
//! clip back, and its sync writes the end back.
//!
//! Each write is guarded ([`write`]): the shot's range on disk must still
//! be the one the editor knew (`from`), and the database file must not
//! have been written between that read and the write
//! (`Config::set_own_keys_guarded`); otherwise nothing is written and the
//! refusal says why.

use serde_json::{Map, Value};
use th_edit::document::Frame;

use crate::config::Config;
use crate::master::ShotUse;
use crate::timeline::get_frame_range;
use crate::{Project, Uri};

/// One shot's range to write: from `from` (what it is now) to `to`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RangeSync {
    pub shot: Uri,
    pub from: (Frame, Frame),
    pub to: (Frame, Frame),
}

impl RangeSync {
    pub fn describe(&self) -> String {
        let ((a, b), (c, d)) = (self.from, self.to);
        format!("{}: {a}-{b} becomes {c}-{d}", crate::master::shot_label(&self.shot))
    }
}

/// The range a shot of `range` takes when its clip's source frames go from
/// `before` to `after` (each first and last, inclusive): an end the clip
/// moved becomes the clip's, an end it kept stays the shot's. None when the
/// range stays as it is, or would be empty.
pub fn synced(range: (Frame, Frame), before: (Frame, Frame), after: (Frame, Frame)) -> Option<(Frame, Frame)> {
    let first = if after.0 != before.0 { after.0 } else { range.0 };
    let last = if after.1 != before.1 { after.1 } else { range.1 };
    (first <= last && (first, last) != range).then_some((first, last))
}

/// The ranges a change of the master's cut from `before` to `after` (the
/// shot uses of each, `master::shot_uses`) writes, in `after`'s order: one
/// per shot shown by a clip in both whose source frames changed, and that
/// `range_of` has a range for (None: a planned shot, or a shot without a
/// range, which is left alone). A shot put in or taken out writes nothing.
pub fn plan(before: &[ShotUse], after: &[ShotUse], range_of: impl Fn(&Uri) -> Option<(Frame, Frame)>) -> Vec<RangeSync> {
    after
        .iter()
        .filter_map(|now| {
            let was = before.iter().find(|u| u.shot == now.shot)?;
            let range = range_of(&now.shot)?;
            let to = synced(range, (was.first, was.last), (now.first, now.last))?;
            Some(RangeSync { shot: now.shot.clone(), from: range, to })
        })
        .collect()
}

/// The frames a new shot of `frames` frames gets when it starts on `start`
/// (the project's start frame, `master::new_shot_start`): `start` to
/// `start + frames - 1`. None for no frames.
pub fn new_shot_frames(start: Frame, frames: Frame) -> Option<(Frame, Frame)> {
    (frames > 0).then(|| (start, start + frames - 1))
}

/// Write `sync`: `to` as the shot's own `frame_start` and `frame_end`,
/// refused when its range on disk is no longer `from` (someone changed it
/// since the editor read it) or its file was written between this read
/// and the write.
pub fn write(project: &Project, sync: &RangeSync) -> Result<(), String> {
    // A fresh read, not the project's snapshot.
    let config = Config::new(&project.config_path);
    let mtime = config.file_mtime(&sync.shot.purpose);
    let now = get_frame_range(&config, &sync.shot)?.map(|r| (r.start_frame, r.end_frame));
    let label = crate::master::shot_label(&sync.shot);
    match now {
        Some(now) if now == sync.from => {}
        Some((a, b)) => {
            let (c, d) = sync.from;
            return Err(format!("{label} is {a}-{b} on disk, not {c}-{d}: it was changed elsewhere, so it was not synced"));
        }
        None => return Err(format!("{label} has no frame range on disk, so it was not synced")),
    }
    let mut keys = Map::new();
    if sync.to.0 != sync.from.0 {
        keys.insert("frame_start".into(), Value::from(sync.to.0));
    }
    if sync.to.1 != sync.from.1 {
        keys.insert("frame_end".into(), Value::from(sync.to.1));
    }
    config.set_own_keys_guarded(&sync.shot, keys, mtime).map_err(|e| format!("{label}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::entities::create_shot;
    use crate::testing::make_project;
    use proptest::prelude::*;

    const SCHEMAS: &str = r#"{"properties":{},"children":{"entity":{"properties":{"roll_start":5,"roll_end":5},"children":{"shots":{"properties":{},"children":{"sequence":{"properties":{},"children":{"shot":{"properties":{},"children":{}}}}}}}}}}"#;

    fn uri(s: &str) -> Uri {
        Uri::parse(&format!("entity:/shots/{s}")).unwrap()
    }

    fn range_on_disk(root: &std::path::Path, shot: &str) -> (Frame, Frame) {
        let p = Project::open(root, root).unwrap();
        let r = get_frame_range(&p.config, &uri(shot)).unwrap().unwrap();
        (r.start_frame, r.end_frame)
    }

    proptest! {
        /// A trim of the tail moves only the shot's last frame, of the head
        /// only its first, each to the clip's; a slip both; nothing else
        /// changes the range, and moving the clip back gives the range back
        /// whenever the shot's range was the clip's.
        #[test]
        fn a_trim_moves_the_end_it_trims(
            first in 900i64..1100, len in 1i64..200,
            head in -50i64..50, tail in -50i64..50,
            own in proptest::option::of((900i64..1100, 1i64..200)),
        ) {
            let before = (first, first + len - 1);
            let after = (before.0 + head, before.1 + tail);
            prop_assume!(after.0 <= after.1);
            let range = own.map_or(before, |(a, n)| (a, a + n - 1));
            let got = synced(range, before, after);
            let expect = (if head != 0 { after.0 } else { range.0 }, if tail != 0 { after.1 } else { range.1 });
            if expect == range || expect.0 > expect.1 {
                prop_assert_eq!(got, None);
            } else {
                prop_assert_eq!(got, Some(expect));
                if range == before {
                    // The undo: the clip goes back, and so does the range.
                    prop_assert_eq!(synced(expect, after, before), Some(before));
                }
            }
        }

        /// The plan writes exactly the shots in both cuts whose frames
        /// changed and which have a range, each from its range.
        #[test]
        fn the_plan_is_the_changed_shots_with_ranges(
            shots in prop::collection::vec((0i64..3, 0i64..3, any::<bool>(), any::<bool>()), 1..6),
        ) {
            let mut before = Vec::new();
            let mut after = Vec::new();
            let mut ranged = Vec::new();
            for (i, &(head, tail, in_before, has_range)) in shots.iter().enumerate() {
                let shot = uri(&format!("S/{:03}", (i + 1) * 10));
                if in_before {
                    before.push(ShotUse { shot: shot.clone(), first: 1001, last: 1048 });
                }
                after.push(ShotUse { shot: shot.clone(), first: 1001 + head, last: 1048 + tail });
                if has_range {
                    ranged.push(shot);
                }
            }
            let plan = plan(&before, &after, |s| ranged.contains(s).then_some((1001, 1048)));
            let expect: Vec<Uri> = shots
                .iter()
                .enumerate()
                .filter(|(_, (head, tail, in_before, has_range))| *in_before && *has_range && (*head != 0 || *tail != 0))
                .map(|(i, _)| uri(&format!("S/{:03}", (i + 1) * 10)))
                .collect();
            prop_assert_eq!(plan.iter().map(|s| s.shot.clone()).collect::<Vec<_>>(), expect);
            for s in &plan {
                prop_assert_eq!(s.from, (1001, 1048));
            }
        }

        /// A new shot of `frames` frames from `start` spans exactly them.
        #[test]
        fn a_new_shot_spans_the_dragged_frames(start in -10_000i64..10_000, frames in -5i64..10_000) {
            match new_shot_frames(start, frames) {
                Some((a, b)) => {
                    prop_assert!(frames > 0);
                    prop_assert_eq!((a, b - a + 1), (start, frames));
                }
                None => prop_assert!(frames <= 0),
            }
        }
    }

    #[test]
    fn a_sync_writes_only_the_moved_end_and_an_undo_writes_it_back() {
        let (_dir, root) = make_project();
        std::fs::write(root.join("_config/db/schemas.json"), SCHEMAS).unwrap();
        let p = Project::open(&root, &root).unwrap();
        create_shot(&p, "S", "010", 1001, 1048).unwrap();
        let tail = RangeSync { shot: uri("S/010"), from: (1001, 1048), to: (1001, 1060) };
        write(&p, &tail).unwrap();
        assert_eq!(range_on_disk(&root, "S/010"), (1001, 1060));
        let own = Project::open(&root, &root).unwrap().config.get_own_properties(&uri("S/010")).unwrap().unwrap();
        assert_eq!(own.get("frame_end"), Some(&Value::from(1060)));
        let back = RangeSync { shot: uri("S/010"), from: (1001, 1060), to: (1001, 1048) };
        write(&p, &back).unwrap();
        assert_eq!(range_on_disk(&root, "S/010"), (1001, 1048));
        let head = RangeSync { shot: uri("S/010"), from: (1001, 1048), to: (1009, 1048) };
        write(&p, &head).unwrap();
        assert_eq!(range_on_disk(&root, "S/010"), (1009, 1048));
    }

    #[test]
    fn a_range_changed_elsewhere_is_not_overwritten() {
        let (_dir, root) = make_project();
        std::fs::write(root.join("_config/db/schemas.json"), SCHEMAS).unwrap();
        let p = Project::open(&root, &root).unwrap();
        create_shot(&p, "S", "010", 1001, 1048).unwrap();
        // Someone else made it 1001-1100 since the editor read 1001-1048.
        let mut keys = Map::new();
        keys.insert("frame_end".into(), Value::from(1100));
        Project::open(&root, &root).unwrap().config.set_own_keys(&uri("S/010"), keys).unwrap();
        let stale = RangeSync { shot: uri("S/010"), from: (1001, 1048), to: (1001, 1050) };
        let refused = write(&p, &stale).unwrap_err();
        assert!(refused.contains("changed elsewhere"), "{refused}");
        assert_eq!(range_on_disk(&root, "S/010"), (1001, 1100));
        // A shot not made is refused, never made.
        let planned = RangeSync { shot: uri("S/020"), from: (1001, 1048), to: (1001, 1050) };
        assert!(write(&p, &planned).is_err());
        assert!(!Project::open(&root, &root).unwrap().config.exists(&uri("S/020")).unwrap());
    }

    #[test]
    fn a_file_written_after_its_read_is_refused() {
        let (_dir, root) = make_project();
        let p = Project::open(&root, &root).unwrap();
        create_shot(&p, "S", "010", 1001, 1048).unwrap();
        let config = Config::new(&p.config_path);
        let then = config.file_mtime("entity");
        // Another write lands between the read and this write.
        std::thread::sleep(std::time::Duration::from_millis(20));
        create_shot(&p, "S", "020", 1001, 1010).unwrap();
        let mut keys = Map::new();
        keys.insert("frame_end".into(), Value::from(1050));
        let refused = config.set_own_keys_guarded(&uri("S/010"), keys.clone(), then).unwrap_err();
        assert!(matches!(refused, crate::config::ConfigError::Changed(_)));
        config.set_own_keys_guarded(&uri("S/010"), keys, config.file_mtime("entity")).unwrap();
    }
}
