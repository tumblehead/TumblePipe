//! The board: every shot by every shot department, each cell the
//! department's latest workfile (version, who saved it, how long ago), and
//! whether a Houdini has it open. A click opens it: TumbleTrove Desktop
//! raises the session that has it, or launches a new one on it
//! (`th_pipe::desktop`).

use chrono::NaiveDateTime;
use th_pipe::Project;
use th_pipe::departments::list_departments;
use th_pipe::desktop::{Session, session_with};
use th_pipe::workfiles::{Workfile, latest_workfile};

use crate::editing::BinShot;

/// A shot's row: one cell per department of [`Board::departments`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BoardShot {
    pub uri: String,
    pub sequence: String,
    pub name: String,
    pub cells: Vec<Option<Workfile>>,
}

/// The project's shots by its shot departments, in pipeline order.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Board {
    pub departments: Vec<String>,
    pub shots: Vec<BoardShot>,
}

impl Board {
    /// The workfile of `shot` (a URI) in `department`.
    pub fn workfile(&self, shot: &str, department: &str) -> Option<&Workfile> {
        let column = self.departments.iter().position(|d| d == department)?;
        self.shots.iter().find(|s| s.uri == shot)?.cells.get(column)?.as_ref()
    }

    /// The departments `shot` has a workfile in, with it.
    pub fn workfiles_of(&self, shot: &str) -> Vec<(&str, &Workfile)> {
        let Some(row) = self.shots.iter().find(|s| s.uri == shot) else { return Vec::new() };
        self.departments.iter().zip(&row.cells).filter_map(|(d, c)| Some((d.as_str(), c.as_ref()?))).collect()
    }
}

/// The board of `project`: `shots` by the departments artists work in
/// (generated and disabled ones have no workfiles of their own).
pub fn load(project: &Project, shots: &[BinShot]) -> Result<Board, String> {
    let departments: Vec<String> = list_departments(&project.config, "shots", false, false)?.into_iter().map(|d| d.name).collect();
    let mut rows = Vec::with_capacity(shots.len());
    for shot in shots {
        let uri = th_pipe::Uri::parse(&shot.uri).map_err(|e| e.to_string())?;
        let cells = departments.iter().map(|d| latest_workfile(project, &uri, d)).collect::<Result<Vec<_>, _>>()?;
        rows.push(BoardShot { uri: shot.uri.clone(), sequence: shot.sequence.clone(), name: shot.name.clone(), cells });
    }
    Ok(Board { departments, shots: rows })
}

/// How long ago `timestamp` (a `_context` entry's, local ISO time) was, at
/// `now`: `now`, `12m`, `3h`, `5d`, `7w`; empty when it does not parse.
pub fn age(timestamp: &str, now: NaiveDateTime) -> String {
    let Ok(then) = NaiveDateTime::parse_from_str(timestamp, "%Y-%m-%dT%H:%M:%S%.f") else { return String::new() };
    let minutes = (now - then).num_minutes().max(0);
    match minutes {
        0 => "now".into(),
        1..60 => format!("{minutes}m"),
        60..1440 => format!("{}h", minutes / 60),
        1440..20160 => format!("{}d", minutes / 1440),
        _ => format!("{}w", minutes / 10080),
    }
}

/// What a cell says under its version: who saved it and how long ago, or
/// that it is being saved, or that a Houdini has it open.
pub fn detail(workfile: &Workfile, sessions: &[Session], now: NaiveDateTime) -> (String, bool) {
    if session_with(sessions, &workfile.path).is_some() {
        return ("open in Houdini".into(), true);
    }
    if workfile.saving {
        return ("saving…".into(), false);
    }
    let when = workfile.timestamp.as_deref().map(|t| age(t, now)).unwrap_or_default();
    let words = match (workfile.user.as_deref(), when.is_empty()) {
        (Some(user), false) => format!("{user} · {when}"),
        (Some(user), true) => user.to_string(),
        (None, _) => when,
    };
    (words, false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::path::PathBuf;

    fn at(text: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S").unwrap()
    }

    fn workfile(path: &str) -> Workfile {
        Workfile {
            path: PathBuf::from(path),
            version: "v0003".into(),
            user: Some("ann".into()),
            timestamp: Some("2026-11-10T11:00:00.5".into()),
            saving: false,
        }
    }

    #[test]
    fn ages() {
        let now = at("2026-11-11T12:00:00");
        assert_eq!(age("2026-11-11T11:59:40.123456", now), "now");
        assert_eq!(age("2026-11-11T11:30:00", now), "30m");
        assert_eq!(age("2026-11-11T06:59:59.9", now), "5h");
        assert_eq!(age("2026-11-08T12:00:00", now), "3d");
        assert_eq!(age("2026-09-11T12:00:00", now), "8w");
        assert_eq!(age("2026-11-12T12:00:00", now), "now");
        assert_eq!(age("yesterday", now), "");
    }

    #[test]
    fn details() {
        let now = at("2026-11-11T12:00:00");
        let file = workfile("C:/p/shots/A/010/anim/A_010_anim_v0003.hip");
        assert_eq!(detail(&file, &[], now), ("ann · 1d".into(), false));
        let open = Session {
            id: "s".into(),
            pid: None,
            state: "running".into(),
            hip_file: Some("C:\\p\\shots\\A\\010\\anim\\A_010_anim_v0003.hip".into()),
        };
        assert_eq!(detail(&file, std::slice::from_ref(&open), now), ("open in Houdini".into(), true));
        let ended = Session { state: "exited".into(), ..open };
        assert!(!detail(&file, &[ended], now).1);
        assert_eq!(detail(&Workfile { saving: true, ..file.clone() }, &[], now).0, "saving…");
        assert_eq!(detail(&Workfile { user: None, timestamp: None, ..file }, &[], now).0, "");
    }

    proptest! {
        /// A board answers for exactly the cells it holds.
        #[test]
        fn lookups_match_cells(filled in proptest::collection::vec(proptest::collection::vec(any::<bool>(), 3), 0..5)) {
            let departments: Vec<String> = ["layout", "animation", "light"].map(String::from).to_vec();
            let shots: Vec<BoardShot> = filled.iter().enumerate().map(|(i, row)| BoardShot {
                uri: format!("entity:/shots/A/{i:03}"),
                sequence: "A".into(),
                name: format!("{i:03}"),
                cells: row.iter().enumerate().map(|(d, has)| has.then(|| workfile(&format!("C:/{i}/{d}.hip")))).collect(),
            }).collect();
            let board = Board { departments: departments.clone(), shots };
            for (i, row) in filled.iter().enumerate() {
                let uri = format!("entity:/shots/A/{i:03}");
                for (d, has) in row.iter().enumerate() {
                    prop_assert_eq!(board.workfile(&uri, &departments[d]).is_some(), *has);
                }
                prop_assert_eq!(board.workfiles_of(&uri).len(), row.iter().filter(|h| **h).count());
            }
            prop_assert!(board.workfile("entity:/shots/B/000", "layout").is_none());
            prop_assert!(board.workfile("entity:/shots/A/000", "fx").is_none());
        }
    }
}
