//! What the player knows of each shot: the take it shows, the take it is
//! switching to, and every take it has asked for, opening, open or failed.
//! The window is none of its business, so its rules can be tested alone.
//!
//! A shot always shows a take: the playlist's at first, while it opens. A
//! take asked for that is open (or failed: its error is what the shot then
//! shows) shows at once; one not open yet starts opening and shows once it
//! has, provided the shot is still switching to it. Keys pressed meanwhile
//! step on from the take the shot is switching to, so presses add up.

use std::collections::HashMap;

use th_playlist::{Kind, Playlist, Shot};

/// A shot (by its place in the playlist) and one of its takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct TakeKey {
    pub shot: usize,
    pub take: usize,
}

/// A take as far as the player has it. `T` is an open take.
#[derive(Debug)]
pub enum TakeState<T> {
    Opening,
    Open(T),
    Failed(String),
}

/// What asking for a take comes to.
#[derive(Debug, PartialEq, Eq)]
pub enum Ask {
    /// The shot shows, or is switching to, that take already.
    Nothing,
    /// The shot shows it now.
    Show,
    /// Open it; the shot shows it once it has.
    Open,
    /// It is opening already; the shot shows it once it has.
    Wait,
}

pub struct Session<T> {
    pub playlist: Playlist,
    takes: HashMap<TakeKey, TakeState<T>>,
    shown: Vec<usize>,
    /// The take each shot is switching to, while it opens.
    aimed: Vec<Option<usize>>,
    /// The take each shot last showed of each kind, so a switch from the
    /// render back to the playblast finds the one left.
    remembered: HashMap<(usize, Kind), usize>,
}

impl<T> Session<T> {
    /// The playlist's shots, each showing its take, which is opening: the
    /// caller opens each of [`Session::shown_keys`].
    pub fn new(playlist: Playlist) -> Self {
        let shown: Vec<usize> = playlist.shots.iter().map(|s| s.take).collect();
        let takes = shown.iter().enumerate().map(|(shot, &take)| (TakeKey { shot, take }, TakeState::Opening)).collect();
        Self { aimed: vec![None; shown.len()], shown, takes, playlist, remembered: HashMap::new() }
    }

    pub fn shots(&self) -> usize {
        self.playlist.shots.len()
    }

    pub fn shot(&self, shot: usize) -> &Shot {
        &self.playlist.shots[shot]
    }

    /// The take `shot` shows.
    pub fn shown(&self, shot: usize) -> TakeKey {
        TakeKey { shot, take: self.shown[shot] }
    }

    pub fn shown_keys(&self) -> Vec<TakeKey> {
        (0..self.shots()).map(|shot| self.shown(shot)).collect()
    }

    /// The take `shot` is switching to, else the one it shows.
    pub fn aimed(&self, shot: usize) -> usize {
        self.aimed[shot].unwrap_or(self.shown[shot])
    }

    /// Whether `shot` is switching to a take still opening.
    pub fn is_switching(&self, shot: usize) -> bool {
        self.aimed[shot].is_some()
    }

    pub fn state(&self, key: TakeKey) -> Option<&TakeState<T>> {
        self.takes.get(&key)
    }

    /// The open take `key`, if it is open.
    pub fn open(&self, key: TakeKey) -> Option<&T> {
        match self.takes.get(&key) {
            Some(TakeState::Open(open)) => Some(open),
            _ => None,
        }
    }

    pub fn open_mut(&mut self, key: TakeKey) -> Option<&mut T> {
        match self.takes.get_mut(&key) {
            Some(TakeState::Open(open)) => Some(open),
            _ => None,
        }
    }

    /// Every open take.
    pub fn open_takes_mut(&mut self) -> impl Iterator<Item = (TakeKey, &mut T)> {
        self.takes.iter_mut().filter_map(|(&key, state)| match state {
            TakeState::Open(open) => Some((key, open)),
            _ => None,
        })
    }

    /// Ask for `take` of `shot`.
    pub fn ask(&mut self, shot: usize, take: usize) -> Ask {
        let from = self.aimed(shot);
        if take == from {
            return Ask::Nothing;
        }
        let kind = self.shot(shot).takes[from].kind();
        self.remembered.insert((shot, kind), from);
        let key = TakeKey { shot, take };
        match self.takes.get(&key) {
            Some(TakeState::Opening) if take == self.shown[shot] => {
                // Back to the take it shows, which is still opening.
                self.aimed[shot] = None;
                Ask::Nothing
            }
            Some(TakeState::Opening) => {
                self.aimed[shot] = Some(take);
                Ask::Wait
            }
            Some(TakeState::Open(_) | TakeState::Failed(_)) => {
                self.aimed[shot] = None;
                self.shown[shot] = take;
                Ask::Show
            }
            None => {
                self.takes.insert(key, TakeState::Opening);
                self.aimed[shot] = Some(take);
                Ask::Open
            }
        }
    }

    /// `key` finished opening, or failed to: whether its shot shows it now
    /// (it was the shot's take, or the one the shot was switching to).
    pub fn opened(&mut self, key: TakeKey, result: Result<T, String>) -> bool {
        self.takes.insert(key, result.map_or_else(TakeState::Failed, TakeState::Open));
        if self.aimed[key.shot] == Some(key.take) {
            self.aimed[key.shot] = None;
            self.shown[key.shot] = key.take;
        }
        self.shown[key.shot] == key.take
    }

    /// The take a version step of `shot` asks for: `delta` versions from the
    /// one it is switching to.
    pub fn version_step(&self, shot: usize, delta: i64) -> usize {
        self.shot(shot).version_step(self.aimed(shot), delta)
    }

    /// The take a department step of `shot` asks for.
    pub fn department_step(&self, shot: usize, delta: i64) -> usize {
        self.shot(shot).department_step(self.aimed(shot), delta)
    }

    /// The take a switch of `shot` to `kind` shows: the one it showed last of
    /// that kind, else the newest of the most downstream department. None
    /// when it shows (or is switching to) that kind already, or has none.
    pub fn take_of_kind(&self, shot: usize, kind: Kind) -> Option<usize> {
        let s = self.shot(shot);
        if s.takes[self.aimed(shot)].kind() == kind {
            return None;
        }
        self.remembered.get(&(shot, kind)).copied().or_else(|| s.last_of_kind(kind))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use th_playlist::{Media, Take};

    /// Takes of the one shot the property drives.
    const TAKES: usize = 5;

    fn session() -> Session<()> {
        let take = |t: usize| Take {
            label: format!("t{t}"),
            department: "animation".into(),
            version: format!("v{t:04}"),
            media: Media::Movie { path: format!("v{t}.mp4") },
        };
        let shot = Shot { name: "s".into(), sequence: "q".into(), fps: 24, length: 10, take: 0, takes: (0..TAKES).map(take).collect() };
        Session::new(Playlist { name: String::new(), ocio: String::new(), shots: vec![shot] })
    }

    /// One thing happening to a shot: a take asked for (a key or the take
    /// menu), or one of the takes being opened finishing, opened or not.
    #[derive(Debug, Clone)]
    enum Op {
        Ask(usize),
        Finish(prop::sample::Index, bool),
    }

    fn ops() -> impl Strategy<Value = Vec<Op>> {
        let op =
            prop_oneof![(0..TAKES).prop_map(Op::Ask), (any::<prop::sample::Index>(), any::<bool>()).prop_map(|(i, ok)| Op::Finish(i, ok)),];
        prop::collection::vec(op, 0..40)
    }

    proptest! {
        /// However asks and finished opens interleave (opens finishing in
        /// any order, some failing): each take is opened once; the shot shows
        /// a take that is open, failed, or (its first) opening; keys step
        /// from the take last asked for; a take finishing after the shot
        /// moved on never shows; and once nothing is opening, the shot shows
        /// the take last asked for, whether it opened or failed.
        #[test]
        fn a_shot_shows_the_take_last_asked_for(ops in ops()) {
            let mut session = session();
            let mut opening: Vec<usize> = vec![0];
            let mut opened_ever: Vec<usize> = vec![0];
            let mut last_asked = 0;
            for op in ops {
                match op {
                    Op::Ask(take) => {
                        let before = session.aimed(0);
                        let ask = session.ask(0, take);
                        match &ask {
                            Ask::Nothing => prop_assert!(take == before || (take == session.shown(0).take && opening.contains(&take))),
                            Ask::Show => prop_assert!(!opening.contains(&take) && opened_ever.contains(&take)),
                            Ask::Open => {
                                prop_assert!(!opened_ever.contains(&take), "take {} opened twice", take);
                                opening.push(take);
                                opened_ever.push(take);
                            }
                            Ask::Wait => prop_assert!(opening.contains(&take)),
                        }
                        prop_assert_eq!(session.aimed(0), take, "keys step from the take asked for");
                        last_asked = take;
                    }
                    Op::Finish(i, ok) => {
                        if opening.is_empty() {
                            continue;
                        }
                        let take = opening.remove(i.index(opening.len()));
                        let wanted = session.aimed(0) == take || session.shown(0).take == take;
                        let shows = session.opened(TakeKey { shot: 0, take }, if ok { Ok(()) } else { Err("no".into()) });
                        prop_assert_eq!(shows, wanted, "a finished take shows exactly when the shot wants it");
                    }
                }
                let shown = session.shown(0);
                let first = shown.take == 0 && opening.contains(&0);
                prop_assert!(first || !opening.contains(&shown.take), "the shot shows a take still opening");
                prop_assert_eq!(session.is_switching(0), session.aimed(0) != shown.take);
            }
            for take in opening.drain(..) {
                session.opened(TakeKey { shot: 0, take }, Ok(()));
            }
            prop_assert!(!session.is_switching(0));
            prop_assert_eq!(session.shown(0).take, last_asked, "the shot ends on the take last asked for");
        }
    }

    #[test]
    fn a_kind_switch_goes_back_to_the_take_left() {
        let mut session = session();
        let exr = |t: usize| Take {
            label: format!("r{t}"),
            department: "light".into(),
            version: format!("v{t:04}"),
            media: Media::Exr {
                layer: "default".into(),
                frames: th_playlist::Frames {
                    directory: "d".into(),
                    prefix: "a.".into(),
                    suffix: ".exr".into(),
                    padding: 4,
                    first: 1,
                    last: 2,
                    step: 1,
                },
            },
        };
        session.playlist.shots[0].takes.extend([exr(1), exr(2)]);
        session.opened(TakeKey { shot: 0, take: 0 }, Ok(()));
        session.ask(0, 2);
        // The newest render first, then back to the playblast left.
        assert_eq!(session.take_of_kind(0, Kind::Exr), Some(TAKES + 1));
        session.ask(0, TAKES + 1);
        assert_eq!(session.take_of_kind(0, Kind::Movie), Some(2));
        assert_eq!(session.take_of_kind(0, Kind::Exr), None, "it shows a render already");
    }
}
