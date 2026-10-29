//! Takes opened off the UI thread. Opening one reads its folder and its
//! first frame's header, which on a slow file server takes seconds; the
//! window stays live meanwhile, showing the take it is switching to.
//! [`Aims`] keeps which take each shot is switching to, apart from the
//! window, so its rules can be tested.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::thread;

use th_media::Source;

/// A shot (playlist index) and one of its takes.
pub type Key = (usize, usize);

/// A take that finished opening, or the reason it did not open.
pub struct Opened {
    pub key: Key,
    pub result: Result<Source, String>,
}

/// The take each shot is switching to, while it opens. Keys pressed meanwhile
/// step on from it, and a take that finishes opening shows only when its
/// shot is still switching to it.
#[derive(Default)]
pub struct Aims {
    wanted: HashMap<usize, usize>,
}

/// What a shot does when a take is asked for.
#[derive(Debug, PartialEq, Eq)]
pub enum Ask {
    /// It shows, or is switching to, that take already.
    Nothing,
    /// The take is open: show it now.
    Show,
    /// Open the take; it shows once it has.
    Open,
}

impl Aims {
    /// The take `shot` is switching to, else `shown`, the one it shows.
    pub fn aimed(&self, shot: usize, shown: usize) -> usize {
        self.wanted.get(&shot).copied().unwrap_or(shown)
    }

    /// `take` is asked for `shot`, which shows `shown`; `open` says whether
    /// that take is open already.
    pub fn ask(&mut self, shot: usize, shown: usize, take: usize, open: bool) -> Ask {
        if take == self.aimed(shot, shown) {
            return Ask::Nothing;
        }
        self.wanted.remove(&shot);
        if open {
            return Ask::Show;
        }
        self.wanted.insert(shot, take);
        Ask::Open
    }

    /// `take` of `shot` finished opening, or failed to: whether the shot was
    /// switching to it (and is not any more).
    pub fn opened(&mut self, shot: usize, take: usize) -> bool {
        let waiting = self.wanted.get(&shot) == Some(&take);
        if waiting {
            self.wanted.remove(&shot);
        }
        waiting
    }

    pub fn is_opening(&self, shot: usize) -> bool {
        self.wanted.contains_key(&shot)
    }
}

#[derive(Default)]
struct State {
    /// Being opened.
    opening: HashSet<Key>,
    /// Opened, not collected yet.
    done: Vec<Opened>,
}

#[derive(Default)]
struct Shared {
    state: Mutex<State>,
    notify: Mutex<Option<Box<dyn Fn() + Send>>>,
}

pub struct Opener {
    shared: Arc<Shared>,
}

impl Opener {
    pub fn start() -> Self {
        Self { shared: Arc::new(Shared::default()) }
    }

    /// Called (from an opening thread) whenever a take has opened or failed.
    pub fn set_notifier(&self, notify: impl Fn() + Send + 'static) {
        *self.shared.notify.lock().unwrap() = Some(Box::new(notify));
    }

    /// Open the take at `path` as `key`, on a thread of its own (each mostly
    /// waits on the file server), unless it is being opened already.
    pub fn request(&self, key: Key, path: String, rate: Option<(i32, i32)>) {
        if !self.shared.state.lock().unwrap().opening.insert(key) {
            return;
        }
        let shared = Arc::clone(&self.shared);
        thread::Builder::new()
            .name(format!("th_player open {}.{}", key.0, key.1))
            .spawn(move || {
                let result = Source::open(&path, rate).map_err(|e| e.to_string());
                {
                    let mut state = shared.state.lock().unwrap();
                    state.opening.remove(&key);
                    state.done.push(Opened { key, result });
                }
                if let Some(notify) = &*shared.notify.lock().unwrap() {
                    notify();
                }
            })
            .expect("spawning a thread to open a take");
    }

    /// The takes that finished opening since the last call.
    pub fn collect(&self) -> Vec<Opened> {
        std::mem::take(&mut self.shared.state.lock().unwrap().done)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::time::Duration;

    const MISSING: &str = "no/such/take.mp4";

    /// An opener, and what its notifier sends on.
    fn opener() -> (Opener, mpsc::Receiver<()>) {
        let (woke, woken) = mpsc::channel();
        let opener = Opener::start();
        opener.set_notifier(move || {
            let _ = woke.send(());
        });
        (opener, woken)
    }

    #[test]
    fn a_take_that_does_not_open_comes_back_with_the_reason() {
        let (opener, woken) = opener();
        opener.request((2, 5), MISSING.into(), None);
        woken.recv_timeout(Duration::from_secs(10)).expect("the opener wakes the UI");
        let opened = opener.collect();
        assert_eq!(opened.len(), 1);
        assert_eq!(opened[0].key, (2, 5));
        assert!(opened[0].result.is_err());
        assert!(opener.collect().is_empty(), "a collected take is not collected again");
    }

    #[test]
    fn a_take_being_opened_is_not_opened_again() {
        let (opener, woken) = opener();
        opener.shared.state.lock().unwrap().opening.insert((0, 1));
        opener.request((0, 1), MISSING.into(), None);
        assert!(woken.recv_timeout(Duration::from_millis(500)).is_err(), "nothing was opened");
        assert!(opener.collect().is_empty());
        // Once that open has finished, the take can be asked for again.
        opener.shared.state.lock().unwrap().opening.remove(&(0, 1));
        opener.request((0, 1), MISSING.into(), None);
        woken.recv_timeout(Duration::from_secs(10)).expect("the opener wakes the UI");
        assert_eq!(opener.collect().len(), 1);
    }

    use proptest::prelude::*;

    /// Takes of the one shot the property drives.
    const TAKES: usize = 5;

    /// One thing happening to a shot: a take asked for (a key or the Take
    /// menu), or one of the takes being opened finishing, opened or not.
    #[derive(Debug, Clone)]
    enum Op {
        Ask(usize),
        Finish(prop::sample::Index, bool),
    }

    fn ops() -> impl Strategy<Value = Vec<Op>> {
        let op = prop_oneof![
            (0..TAKES).prop_map(Op::Ask),
            (any::<prop::sample::Index>(), any::<bool>()).prop_map(|(i, ok)| Op::Finish(i, ok)),
        ];
        prop::collection::vec(op, 0..40)
    }

    proptest! {
        /// However asks and finished opens interleave (opens finishing in
        /// any order, some failing): the shot only ever shows an open take;
        /// keys step from the take last asked for; a take finishing after
        /// the shot moved on never shows; and once nothing is opening, the
        /// shot shows the take last asked for, unless that one failed.
        #[test]
        fn a_shot_shows_the_take_last_asked_for(initial_open in prop::collection::btree_set(0..TAKES, 0..TAKES), ops in ops()) {
            let mut aims = Aims::default();
            let mut shown = 0;
            let mut open: HashSet<usize> = initial_open.into_iter().collect();
            open.insert(shown);
            // The opener's threads, as set_take_of drives them: one open
            // per take at a time.
            let mut in_flight: Vec<usize> = Vec::new();
            let mut last_asked = shown;
            let mut last_failed = false;
            for op in ops {
                match op {
                    Op::Ask(take) => {
                        let before = aims.aimed(0, shown);
                        match aims.ask(0, shown, take, open.contains(&take)) {
                            Ask::Nothing => prop_assert_eq!(take, before),
                            Ask::Show => {
                                prop_assert!(open.contains(&take));
                                shown = take;
                            }
                            Ask::Open => {
                                prop_assert!(!open.contains(&take));
                                if !in_flight.contains(&take) {
                                    in_flight.push(take);
                                }
                            }
                        }
                        prop_assert_eq!(aims.aimed(0, shown), take, "keys step from the take asked for");
                        if take != before {
                            last_asked = take;
                            last_failed = false;
                        }
                    }
                    Op::Finish(i, ok) => {
                        if in_flight.is_empty() {
                            continue;
                        }
                        let take = in_flight.remove(i.index(in_flight.len()));
                        let was_aimed = aims.aimed(0, shown) == take && aims.is_opening(0);
                        let waiting = aims.opened(0, take);
                        prop_assert_eq!(waiting, was_aimed);
                        if ok {
                            open.insert(take);
                        }
                        if waiting {
                            if ok {
                                shown = take;
                            } else {
                                last_failed = true;
                            }
                        }
                        prop_assert!(!aims.is_opening(0) || aims.aimed(0, shown) != take, "a finished take is no longer awaited");
                    }
                }
                prop_assert!(open.contains(&shown), "the shot shows an open take");
                prop_assert_eq!(aims.is_opening(0), aims.aimed(0, shown) != shown, "a shot is opening exactly when it aims past the take it shows");
            }
            // Let every open finish, all opening.
            for take in in_flight.drain(..) {
                if aims.opened(0, take) {
                    open.insert(take);
                    shown = take;
                }
            }
            prop_assert!(!aims.is_opening(0));
            if !last_failed {
                prop_assert_eq!(shown, last_asked, "the shot ends on the take last asked for");
            }
        }
    }
}
