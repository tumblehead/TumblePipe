//! The master edit (`designs/edit-entity.md` §4): the one edit whose
//! publish writes shot timing.
//!
//! `master_edit` on `config:/project` names it (`entity:/edits/<name>`;
//! unset or empty, there is none). Publishing it lists the shot changes
//! its cut makes, each the user ticks or not ([`changes`]): planned shots
//! to **create**, shots whose **range** changes to the frames their clip
//! shows, and shots the previous published master used that this one does
//! not, offered to **retire**, and shots that play at another **fps** than
//! the master (#47: the master never wrote fps, so a shot cut at the edit's
//! 24 could stay at 25). [`apply`] writes one: a range change pins only
//! `frame_start` and `frame_end`, an fps change only `fps`, on the shot's
//! own properties as they are on disk, so it keeps inheriting the rest
//! (roll included: the cut has no handles to say otherwise, and a created
//! shot takes the project's default, as the design has it); a created shot
//! goes on the
//! shared timelines of the Multis listing its sequence; a retire archives
//! the shot's folders and takes it out of its Multis, as the Entity Editor
//! does.
//!
//! Within the master a shot is shown by one video clip at most
//! ([`shot_uses`] refuses otherwise): its range is that clip's source
//! frames.

use serde_json::{Map, Value};
use th_edit::document::{Edit, Frame};

use crate::Project;
use crate::entities::{create_shot, delete_entity};
use crate::groups::place_new_members;
use crate::timeline::{get_fps, get_frame_range};
use crate::uri::Uri;

/// Where `master_edit` lives.
pub const MASTER_NODE: &str = "config:/project";
const MASTER_KEY: &str = "master_edit";

fn master_node() -> Uri {
    Uri::parse(MASTER_NODE).expect("a URI")
}

/// The master edit's name, None when the project has none.
pub fn master_edit(project: &Project) -> Result<Option<String>, String> {
    let properties = project.config.get_properties(&master_node()).map_err(|e| e.to_string())?.unwrap_or_default();
    let Some(Value::String(uri)) = properties.get(MASTER_KEY) else { return Ok(None) };
    if uri.is_empty() {
        return Ok(None);
    }
    let uri = Uri::parse(uri).map_err(|e| format!("master_edit {uri:?}: {e}"))?;
    crate::edit_files::edit_name(&uri).map(Some)
}

/// Make `name` the master edit, or with None leave the project without one.
pub fn set_master_edit(project: &Project, name: Option<&str>) -> Result<(), String> {
    let value = match name {
        Some(name) => {
            if !crate::edit_files::list_edits(project)?.iter().any(|e| e == name) {
                return Err(format!("there is no edit {name}"));
            }
            format!("entity:/edits/{name}")
        }
        None => String::new(),
    };
    let mut keys = Map::new();
    keys.insert(MASTER_KEY.into(), Value::String(value));
    project.config.set_own_keys(&master_node(), keys).map_err(|e| e.to_string())
}

/// A shot an edit shows, and the source frames its clip shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShotUse {
    pub shot: Uri,
    pub first: Frame,
    pub last: Frame,
}

/// The shots `edit`'s video clips show (`shot_of` names a clip's shot, if
/// it shows one), in the order they start, each with its clip's source
/// frames. Refuses a shot two clips show.
pub fn shot_uses<S>(edit: &Edit<S>, shot_of: impl Fn(&S) -> Option<&str>) -> Result<Vec<ShotUse>, String> {
    let mut found: Vec<(Frame, ShotUse)> = Vec::new();
    for track in &edit.video {
        for clip in &track.clips {
            let Some(shot) = shot_of(&clip.source) else { continue };
            let shot = Uri::parse_address(shot).map_err(|e| e.to_string())?;
            if found.iter().any(|(_, u)| u.shot == shot) {
                return Err(format!("{} is in the master edit twice: a shot's range comes from its one clip", shot_label(&shot)));
            }
            found.push((clip.at, ShotUse { shot, first: clip.source_in, last: clip.source_in + clip.duration - 1 }));
        }
    }
    found.sort_by_key(|(at, _)| *at);
    Ok(found.into_iter().map(|(_, u)| u).collect())
}

/// `sequence/shot`, for messages.
pub fn shot_label(shot: &Uri) -> String {
    shot.segments.iter().skip(1).cloned().collect::<Vec<_>>().join("/")
}

/// One change a master publish offers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Change {
    /// A planned shot, made with these frames.
    Create { shot: Uri, first: Frame, last: Frame },
    /// A shot whose frames become `first ..= last` (from `from`, when it had
    /// a range).
    Range { shot: Uri, from: Option<(Frame, Frame)>, first: Frame, last: Frame },
    /// A shot the master shows that plays at another rate (`from`, None when
    /// none is set) than the master's `fps`.
    Fps { shot: Uri, from: Option<i64>, fps: i64 },
    /// A shot the previous master used and this one does not.
    Retire { shot: Uri },
}

impl Change {
    pub fn shot(&self) -> &Uri {
        match self {
            Change::Create { shot, .. } | Change::Range { shot, .. } | Change::Fps { shot, .. } | Change::Retire { shot } => shot,
        }
    }

    /// Ticked unless the user ticks it: every change but a retire.
    pub fn ticked_by_default(&self) -> bool {
        !matches!(self, Change::Retire { .. })
    }

    pub fn describe(&self) -> String {
        match self {
            Change::Create { shot, first, last } => format!("Create {} ({first}-{last})", shot_label(shot)),
            Change::Range { shot, from: Some((a, b)), first, last } => {
                format!("{}: {a}-{b} becomes {first}-{last}", shot_label(shot))
            }
            Change::Range { shot, from: None, first, last } => format!("{}: becomes {first}-{last}", shot_label(shot)),
            Change::Fps { shot, from: Some(from), fps } => {
                format!("{}: plays at {from} fps, becomes {fps} (the master's)", shot_label(shot))
            }
            Change::Fps { shot, from: None, fps } => format!("{}: plays at {fps} fps (the master's)", shot_label(shot)),
            Change::Retire { shot } => format!("Retire {} (no longer in the master)", shot_label(shot)),
        }
    }
}

/// The fps `shot` plays at: its own or inherited, or for a shot not made
/// yet what it would inherit from its sequence or `entity:/shots`; else
/// the project's (`config:/project`), as an entity without one plays at.
fn rate_of(project: &Project, shot: &Uri) -> Result<Option<i64>, String> {
    let mut places = vec![shot.clone()];
    if let [_, sequence, _] = shot.segments.as_slice() {
        places.push(Uri::parse(&format!("entity:/shots/{sequence}")).map_err(|e| e.to_string())?);
    }
    places.push(Uri::parse("entity:/shots").map_err(|e| e.to_string())?);
    for place in places {
        if project.config.exists(&place).map_err(|e| e.to_string())? {
            if let Some(fps) = get_fps(&project.config, &place)? {
                return Ok(Some(fps));
            }
            break;
        }
    }
    get_fps(&project.config, &master_node())
}

/// What publishing a master that shows `uses` at `fps` changes, given the
/// previous published master showed `previous`: per shot in cut order its
/// create or range change, then its fps change when it plays at another
/// rate; then retires (of shots that still exist) in the previous cut's
/// order. [`apply`] in this order makes a created shot before its fps.
pub fn changes(project: &Project, uses: &[ShotUse], previous: &[ShotUse], fps: i64) -> Result<Vec<Change>, String> {
    let mut out = Vec::new();
    for u in uses {
        if !project.config.exists(&u.shot).map_err(|e| e.to_string())? {
            out.push(Change::Create { shot: u.shot.clone(), first: u.first, last: u.last });
        } else {
            let from = get_frame_range(&project.config, &u.shot)?.map(|r| (r.start_frame, r.end_frame));
            if from != Some((u.first, u.last)) {
                out.push(Change::Range { shot: u.shot.clone(), from, first: u.first, last: u.last });
            }
        }
        let rate = rate_of(project, &u.shot)?;
        if rate != Some(fps) {
            out.push(Change::Fps { shot: u.shot.clone(), from: rate, fps });
        }
    }
    for p in previous {
        if !uses.iter().any(|u| u.shot == p.shot) && project.config.exists(&p.shot).map_err(|e| e.to_string())? {
            out.push(Change::Retire { shot: p.shot.clone() });
        }
    }
    Ok(out)
}

/// The frame a shot made in `sequence` starts on (a planned shot of the
/// master edit, see [`Change::Create`]): the `frame_start` it would inherit
/// there, from the sequence or, for a sequence not made yet, from
/// `entity:/shots`; 1001 when the project sets none.
pub fn new_shot_start(project: &Project, sequence: &str) -> Result<Frame, String> {
    let mut places = vec!["entity:/shots".to_string()];
    if !sequence.is_empty() {
        places.insert(0, format!("entity:/shots/{sequence}"));
    }
    for place in places {
        let uri = Uri::parse(&place).map_err(|e| e.to_string())?;
        let Some(properties) = project.config.get_properties(&uri).map_err(|e| e.to_string())? else { continue };
        match properties.get("frame_start") {
            Some(Value::Number(n)) => {
                if let Some(start) = n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)) {
                    return Ok(start);
                }
            }
            Some(Value::String(s)) => {
                if let Ok(start) = s.trim().parse::<Frame>() {
                    return Ok(start);
                }
            }
            _ => {}
        }
    }
    Ok(1001)
}

/// Write `change` (`stamp` names a retire's archive folder, see
/// `entities::archive_stamp`).
pub fn apply(project: &Project, change: &Change, stamp: &str) -> Result<(), String> {
    match change {
        Change::Create { shot, first, last } => {
            let [_, sequence, name] = shot.segments.as_slice() else {
                return Err(format!("{shot} is not entity:/shots/<sequence>/<shot>"));
            };
            let uri = create_shot(project, sequence, name, *first, *last)?;
            place_new_members(&project.config, &[uri])
        }
        Change::Range { shot, first, last, .. } => {
            let mut keys = Map::new();
            keys.insert("frame_start".into(), Value::from(*first));
            keys.insert("frame_end".into(), Value::from(*last));
            project.config.set_own_keys(shot, keys).map_err(|e| e.to_string())
        }
        Change::Fps { shot, fps, .. } => {
            let mut keys = Map::new();
            keys.insert("fps".into(), Value::from(*fps));
            project.config.set_own_keys(shot, keys).map_err(|e| e.to_string())
        }
        Change::Retire { shot } => delete_entity(project, shot, stamp).map(|_| ()).map_err(|e| e.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::make_project;
    use th_edit::document::{Rule, Source, Track, VideoClip, WorkingEdit};

    const SCHEMAS: &str = r#"{"properties":{},"children":{"entity":{"properties":{"roll_start":5,"roll_end":5},"children":{"shots":{"properties":{},"children":{"sequence":{"properties":{},"children":{"shot":{"properties":{},"children":{}}}}}},"edits":{"properties":{},"children":{"edit":{"properties":{},"children":{}}}}}}}}"#;

    fn project() -> (tempfile::TempDir, std::path::PathBuf) {
        let (dir, root) = make_project();
        std::fs::write(root.join("_config/db/schemas.json"), SCHEMAS).unwrap();
        (dir, root)
    }

    fn open(root: &std::path::Path) -> Project {
        Project::open(root, root).unwrap()
    }

    fn shot(name: &str) -> Source {
        Source::Shot { shot: format!("entity:/shots/{name}"), take: Rule::best() }
    }

    fn clip(at: Frame, duration: Frame, source_in: Frame, source: Source) -> VideoClip<Source> {
        VideoClip { at, duration, source_in, source, fade_in: 0, fade_out: 0, opacity: 1.0 }
    }

    fn edit(tracks: Vec<Vec<VideoClip<Source>>>) -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        for (i, clips) in tracks.into_iter().enumerate() {
            edit.video.push(Track { name: format!("V{}", i + 1), clips });
        }
        edit
    }

    fn shot_of(source: &Source) -> Option<&str> {
        match source {
            Source::Shot { shot, .. } => Some(shot),
            _ => None,
        }
    }

    #[test]
    fn uses_are_in_cut_order_and_a_shot_twice_is_refused() {
        let cut = edit(vec![
            vec![clip(0, 10, 1001, shot("S/010")), clip(30, 5, 1001, Source::Slug { text: "end".into() })],
            vec![clip(10, 20, 1005, shot("S/020"))],
        ]);
        let uses = shot_uses(&cut, shot_of).unwrap();
        assert_eq!(
            uses.iter().map(|u| (shot_label(&u.shot), u.first, u.last)).collect::<Vec<_>>(),
            [("S/010".to_string(), 1001, 1010), ("S/020".to_string(), 1005, 1024)]
        );
        let twice = edit(vec![vec![clip(0, 10, 1001, shot("S/010")), clip(10, 10, 1011, shot("S/010"))]]);
        assert!(shot_uses(&twice, shot_of).unwrap_err().contains("S/010 is in the master edit twice"));
    }

    #[test]
    fn changes_create_move_and_retire() {
        let (_dir, root) = project();
        let p = open(&root);
        create_shot(&p, "S", "010", 1001, 1010).unwrap();
        create_shot(&p, "S", "020", 1001, 1050).unwrap();
        create_shot(&p, "S", "030", 1001, 1020).unwrap();
        let p = open(&root);
        let uses = |list: &[(&str, Frame, Frame)]| {
            list.iter()
                .map(|(s, a, b)| ShotUse { shot: Uri::parse(&format!("entity:/shots/{s}")).unwrap(), first: *a, last: *b })
                .collect::<Vec<_>>()
        };
        let now = uses(&[("S/010", 1001, 1010), ("S/020", 1001, 1040), ("S/040", 1001, 1030)]);
        let before = uses(&[("S/010", 1001, 1010), ("S/030", 1001, 1020), ("S/099", 1001, 1002)]);
        let found = changes(&p, &now, &before, 24).unwrap();
        let shot = |s: &str| Uri::parse(&format!("entity:/shots/{s}")).unwrap();
        assert_eq!(
            found,
            [
                Change::Range { shot: shot("S/020"), from: Some((1001, 1050)), first: 1001, last: 1040 },
                Change::Create { shot: shot("S/040"), first: 1001, last: 1030 },
                Change::Retire { shot: shot("S/030") },
            ]
        );
        assert!(!found[2].ticked_by_default());

        for change in &found {
            apply(&p, change, "20261106-120000").unwrap();
        }
        let p = open(&root);
        let range = |s: &str| get_frame_range(&p.config, &shot(s)).unwrap().map(|r| (r.start_frame, r.end_frame));
        assert_eq!(range("S/020"), Some((1001, 1040)));
        assert_eq!(range("S/040"), Some((1001, 1030)));
        assert!(!p.config.exists(&shot("S/030")).unwrap());
        assert_eq!(changes(&p, &now, &before, 24).unwrap(), [], "applied, nothing is left to change");
    }

    proptest::proptest! {
        #![proptest_config(proptest::prelude::ProptestConfig::with_cases(16))]

        /// #47: the master writes fps. A shot that plays at another rate
        /// than the master (its own fps, or the project's 24 it inherits)
        /// gets one fps change right after its create or range change;
        /// applying them pins only `fps` on the shot, and then nothing is
        /// left to change.
        #[test]
        fn a_shot_at_another_rate_gets_the_masters_fps(
            master_fps in proptest::sample::select(vec![24i64, 25, 30]),
            own in proptest::option::of(proptest::sample::select(vec![24i64, 25, 30])),
            planned in proptest::bool::ANY,
        ) {
            let (_dir, root) = project();
            let p = open(&root);
            let uri = Uri::parse("entity:/shots/S/010").unwrap();
            if !planned {
                create_shot(&p, "S", "010", 1001, 1010).unwrap();
                if let Some(fps) = own {
                    let mut keys = Map::new();
                    keys.insert("fps".into(), Value::from(fps));
                    open(&root).config.set_own_keys(&uri, keys).unwrap();
                }
            }
            let p = open(&root);
            let now = [ShotUse { shot: uri.clone(), first: 1001, last: 1010 }];
            let found = changes(&p, &now, &[], master_fps).unwrap();
            let rate = if planned { 24 } else { own.unwrap_or(24) };
            let fps_changes: Vec<&Change> = found.iter().filter(|c| matches!(c, Change::Fps { .. })).collect();
            if rate == master_fps {
                proptest::prop_assert!(fps_changes.is_empty(), "{:?}", found);
            } else {
                let expected = Change::Fps { shot: uri.clone(), from: Some(rate), fps: master_fps };
                proptest::prop_assert_eq!(fps_changes, vec![&expected]);
                proptest::prop_assert!(matches!(found.last(), Some(Change::Fps { .. })), "after the shot's create or range change");
            }
            for change in &found {
                apply(&open(&root), change, "x").unwrap();
            }
            let p = open(&root);
            proptest::prop_assert_eq!(rate_of(&p, &uri).unwrap(), Some(master_fps));
            proptest::prop_assert_eq!(changes(&p, &now, &[], master_fps).unwrap(), Vec::<Change>::new());
            let own_keys = p.config.get_own_properties(&uri).unwrap().unwrap();
            proptest::prop_assert_eq!(
                own_keys.contains_key("fps"),
                rate != master_fps || (!planned && own.is_some()),
                "fps pinned only by the change (or as it was)"
            );
        }
    }

    #[test]
    fn a_range_change_pins_only_the_frames() {
        let (_dir, root) = project();
        let p = open(&root);
        let uri = create_shot(&p, "S", "010", 1001, 1010).unwrap();
        let mut own = p.config.get_own_properties(&uri).unwrap().unwrap();
        own.insert("note".into(), Value::from("keep me"));
        p.config.set_own_properties(&uri, own).unwrap();
        let stale = open(&root);
        // Someone else pins another key after `stale` was read.
        let other = open(&root);
        let mut own = other.config.get_own_properties(&uri).unwrap().unwrap();
        own.insert("camera".into(), Value::from("cam1"));
        other.config.set_own_properties(&uri, own).unwrap();
        apply(&stale, &Change::Range { shot: uri.clone(), from: None, first: 990, last: 1020 }, "x").unwrap();
        let own = open(&root).config.get_own_properties(&uri).unwrap().unwrap();
        assert_eq!(Value::Object(own), serde_json::json!({"frame_start": 990, "frame_end": 1020, "note": "keep me", "camera": "cam1"}));
    }

    #[test]
    fn the_master_is_named_on_the_project() {
        let (_dir, root) = project();
        let p = open(&root);
        assert_eq!(master_edit(&p).unwrap(), None);
        assert!(set_master_edit(&p, Some("Main")).is_err(), "no such edit");
        crate::edit_files::create_edit(&p, "Main").unwrap();
        let p = open(&root);
        set_master_edit(&p, Some("Main")).unwrap();
        assert_eq!(master_edit(&open(&root)).unwrap().as_deref(), Some("Main"));
        let config = open(&root).config.get_own_properties(&master_node()).unwrap().unwrap();
        assert_eq!(config.get("fps"), Some(&Value::from(24)), "the project keeps its other keys");
        set_master_edit(&open(&root), None).unwrap();
        assert_eq!(master_edit(&open(&root)).unwrap(), None);
    }

    fn pin(root: &std::path::Path, uri: &str, start: Frame) {
        let p = open(root);
        let uri = Uri::parse(uri).unwrap();
        let mut own = p.config.get_own_properties(&uri).unwrap().unwrap_or_default();
        own.insert("frame_start".into(), Value::from(start));
        p.config.set_own_properties(&uri, own).unwrap();
    }

    proptest::proptest! {
        #![proptest_config(proptest::prelude::ProptestConfig::with_cases(24))]

        /// QA 2026-11-10 (qabeta, master publish): a planned shot always
        /// started on 1001, so in a project whose shots start on 1 New
        /// Shot... made SQ010/SH040 1001-1048 beside SH010 1-48. It starts
        /// where its sequence's shots start, and is created there.
        #[test]
        fn a_planned_shot_starts_where_its_sequence_starts(
            // Above the schema's 5 frames of roll: a range starts on frame 1 or later.
            project_start in proptest::option::of(6i64..2000),
            sequence_start in proptest::option::of(6i64..2000),
            new_sequence in proptest::bool::ANY,
            frames in 1i64..200,
        ) {
            let (_dir, root) = project();
            let p = open(&root);
            create_shot(&p, "S", "010", 5, 9).unwrap();
            {
                // The sequence and the shot made beside it inherit the range.
                let p = open(&root);
                let seq = Uri::parse("entity:/shots/S").unwrap();
                p.config.set_own_properties(&seq, Map::new()).unwrap();
            }
            if let Some(start) = project_start {
                pin(&root, "entity:/shots", start);
            }
            if let Some(start) = sequence_start {
                pin(&root, "entity:/shots/S", start);
            }
            let sequence = if new_sequence { "T" } else { "S" };
            let want = match (new_sequence, sequence_start, project_start) {
                (false, Some(s), _) => s,
                (_, _, Some(s)) => s,
                _ => 1001,
            };
            let p = open(&root);
            let first = new_shot_start(&p, sequence).unwrap();
            proptest::prop_assert_eq!(first, want);
            let shot = Uri::parse(&format!("entity:/shots/{sequence}/020")).unwrap();
            apply(&p, &Change::Create { shot: shot.clone(), first, last: first + frames - 1 }, "x").unwrap();
            let range = get_frame_range(&open(&root).config, &shot).unwrap().map(|r| (r.start_frame, r.end_frame));
            proptest::prop_assert_eq!(range, Some((want, want + frames - 1)));
        }
    }

    #[test]
    fn a_created_shot_goes_on_its_sequences_shared_timeline() {
        let (_dir, root) = project();
        let p = open(&root);
        create_shot(&p, "S", "010", 1001, 1010).unwrap();
        let group = Uri::parse("groups:/shots/Multi").unwrap();
        let mut props = Map::new();
        props.insert("members".into(), serde_json::json!(["entity:/shots/S"]));
        props.insert("departments".into(), serde_json::json!(["animation"]));
        props.insert("shared_timeline".into(), Value::Bool(true));
        props.insert("timeline_starts".into(), serde_json::json!({"entity:/shots/S/010": 1001}));
        p.config.set_own_properties(&group, props).unwrap();
        let p = open(&root);
        let new = Uri::parse("entity:/shots/S/020").unwrap();
        apply(&p, &Change::Create { shot: new, first: 1001, last: 1020 }, "x").unwrap();
        let own = open(&root).config.get_own_properties(&group).unwrap().unwrap();
        assert_eq!(own["timeline_starts"], serde_json::json!({"entity:/shots/S/010": 1001, "entity:/shots/S/020": 1011}));
    }
}
