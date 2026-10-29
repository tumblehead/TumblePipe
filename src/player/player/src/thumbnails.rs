//! The timeline's thumbnails: the first frame of each shot's current take,
//! as an image. Workers of their own make them. Each opens the take afresh,
//! so a movie's decoder in the engine keeps its place (a movie decodes
//! forward from its keyframe; a thumbnail's seek to frame 0 would send it
//! back to the start).

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;

use primer::ImageHandle;
use th_media::Source;

use crate::colour::Lut;

/// The most pixels a thumbnail has each way: twice what the track shows at
/// most, for high-DPI screens.
const MAX_WIDTH: u32 = 240;
const MAX_HEIGHT: u32 = 120;
/// Thumbnails being made at once.
const WORKERS: usize = 2;

/// A shot (playlist index) and one of its takes.
pub type Key = (usize, usize);

/// A take to make a thumbnail of.
pub struct Request {
    pub key: Key,
    pub path: String,
    pub rate: Option<(i32, i32)>,
    /// The display transform for an EXR take; None for a movie (or no
    /// config: the frame is then shown sRGB-encoded, as the video view does).
    pub lut: Option<Arc<Lut>>,
}

/// Which thumbnails are made, failed, or on their way. `T` is a made one.
struct Tickets<T> {
    /// Asked for and not made yet (queued or being made), by the ticket of
    /// the request: a thumbnail made for another ticket was forgotten.
    pending: HashMap<Key, u64>,
    next_ticket: u64,
    ready: HashMap<Key, T>,
    failed: HashSet<Key>,
}

impl<T> Default for Tickets<T> {
    fn default() -> Self {
        Self { pending: HashMap::new(), next_ticket: 0, ready: HashMap::new(), failed: HashSet::new() }
    }
}

impl<T> Tickets<T> {
    /// The ticket to make `key`'s thumbnail under; None when it is made,
    /// being made, or failed.
    fn ask(&mut self, key: Key) -> Option<u64> {
        if self.ready.contains_key(&key) || self.failed.contains(&key) || self.pending.contains_key(&key) {
            return None;
        }
        let ticket = self.next_ticket;
        self.next_ticket += 1;
        self.pending.insert(key, ticket);
        Some(ticket)
    }

    /// `key`'s thumbnail made under `ticket`, or failed: whether it was kept
    /// (it is not when the key was forgotten since, maybe asked for again).
    fn finish(&mut self, key: Key, ticket: u64, made: Result<T, String>) -> bool {
        if self.pending.get(&key) != Some(&ticket) {
            return false;
        }
        self.pending.remove(&key);
        match made {
            Ok(thumbnail) => {
                self.ready.insert(key, thumbnail);
            }
            Err(_) => {
                self.failed.insert(key);
            }
        }
        true
    }

    fn forget(&mut self, stale: &impl Fn(Key) -> bool) {
        self.ready.retain(|&k, _| !stale(k));
        self.failed.retain(|&k| !stale(k));
        self.pending.retain(|&k, _| !stale(k));
    }
}

#[derive(Default)]
struct State {
    queue: VecDeque<(Request, u64)>,
    tickets: Tickets<ImageHandle>,
    quit: bool,
}

#[derive(Default)]
struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    notify: Mutex<Option<Box<dyn Fn() + Send>>>,
}

pub struct Thumbnails {
    shared: Arc<Shared>,
}

impl Thumbnails {
    pub fn start() -> Self {
        let shared = Arc::new(Shared::default());
        for worker in 0..WORKERS {
            let shared = Arc::clone(&shared);
            thread::Builder::new()
                .name(format!("th_player thumbnail {worker}"))
                .spawn(move || work(&shared))
                .expect("spawning a thumbnail thread");
        }
        Self { shared }
    }

    /// Called (from a worker thread) whenever a thumbnail is ready.
    pub fn set_notifier(&self, notify: impl Fn() + Send + 'static) {
        *self.shared.notify.lock().unwrap() = Some(Box::new(notify));
    }

    /// Make `request`'s thumbnail, unless it is made, being made, or failed.
    pub fn request(&self, request: Request) {
        let mut state = self.shared.state.lock().unwrap();
        let Some(ticket) = state.tickets.ask(request.key) else {
            return;
        };
        state.queue.push_back((request, ticket));
        self.shared.wake.notify_one();
    }

    pub fn get(&self, key: Key) -> Option<ImageHandle> {
        self.shared.state.lock().unwrap().tickets.ready.get(&key).cloned()
    }

    /// Drop the thumbnails of the takes `stale` names (made through a display
    /// transform that has changed), made or on their way.
    pub fn forget(&self, stale: impl Fn(Key) -> bool) {
        let mut state = self.shared.state.lock().unwrap();
        state.tickets.forget(&stale);
        state.queue.retain(|(r, _)| !stale(r.key));
    }
}

impl Drop for Thumbnails {
    fn drop(&mut self) {
        self.shared.state.lock().unwrap().quit = true;
        self.shared.wake.notify_all();
    }
}

/// The thumbnail of `request`'s first frame.
fn make(request: &Request) -> Result<ImageHandle, String> {
    let source = Source::open(&request.path, request.rate).map_err(|e| e.to_string())?;
    let frame = source.decode(0, &source.default_view(), |_, _| {}).map_err(|e| e.to_string())?;
    let display = |rgb: [f32; 3]| match &request.lut {
        Some(lut) => lut.sample(rgb),
        None => rgb.map(th_media::srgb_encode),
    };
    let thumb = th_media::thumbnail(&frame, MAX_WIDTH, MAX_HEIGHT, display);
    Ok(ImageHandle::from_rgba(thumb.width, thumb.height, thumb.rgba))
}

fn work(shared: &Shared) {
    loop {
        let (request, ticket) = {
            let mut state = shared.state.lock().unwrap();
            loop {
                if state.quit {
                    return;
                }
                if let Some(next) = state.queue.pop_front() {
                    break next;
                }
                state = shared.wake.wait(state).unwrap();
            }
        };
        let made = make(&request);
        if !shared.state.lock().unwrap().tickets.finish(request.key, ticket, made) {
            // Forgotten while it was being made (and maybe asked for again).
            continue;
        }
        if let Some(notify) = &*shared.notify.lock().unwrap() {
            notify();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Keys the property uses: few, so they collide.
    const KEYS: usize = 3;

    /// One thing happening to the thumbnails: one asked for, a key's
    /// forgotten, a worker taking the next request off the queue, or one it
    /// took finishing, made or not.
    #[derive(Debug, Clone)]
    enum Op {
        Ask(usize),
        Forget(usize),
        Take,
        Finish(prop::sample::Index, bool),
    }

    fn op() -> impl Strategy<Value = Op> {
        prop_oneof![
            (0..KEYS).prop_map(Op::Ask),
            (0..KEYS).prop_map(Op::Forget),
            Just(Op::Take),
            (any::<prop::sample::Index>(), any::<bool>()).prop_map(|(i, ok)| Op::Finish(i, ok)),
        ]
    }

    proptest! {
        /// However asks, forgets and finishes interleave: a key is asked for
        /// once until it is forgotten; a thumbnail kept is one asked for
        /// since the key was last forgotten, made under the key's latest
        /// ticket, and what that request made; nothing is both made and
        /// failed; and a key no longer pending is never left without its
        /// thumbnail or failure unless it was forgotten.
        #[test]
        fn only_the_latest_ask_since_a_forget_lands(ops in prop::collection::vec(op(), 0..60)) {
            let mut tickets: Tickets<u64> = Tickets::default();
            let key = |k: usize| (k, 0);
            let mut queue: Vec<(Key, u64)> = Vec::new();
            let mut taken: Vec<(Key, u64)> = Vec::new();
            // The ticket each key was last asked for under, since its last
            // forget.
            let mut current: HashMap<Key, u64> = HashMap::new();
            for op in ops {
                match op {
                    Op::Ask(k) => {
                        let before = (tickets.ready.contains_key(&key(k)), tickets.failed.contains(&key(k)), tickets.pending.contains_key(&key(k)));
                        match tickets.ask(key(k)) {
                            Some(ticket) => {
                                prop_assert_eq!(before, (false, false, false), "asked for only when nothing is known of it");
                                prop_assert!(current.values().all(|&t| t != ticket), "a fresh ticket");
                                current.insert(key(k), ticket);
                                queue.push((key(k), ticket));
                            }
                            None => prop_assert!(before != (false, false, false)),
                        }
                    }
                    Op::Forget(k) => {
                        tickets.forget(&|other| other == key(k));
                        queue.retain(|&(q, _)| q != key(k));
                        current.remove(&key(k));
                    }
                    Op::Take => {
                        if !queue.is_empty() {
                            taken.push(queue.remove(0));
                        }
                    }
                    Op::Finish(i, ok) => {
                        if taken.is_empty() {
                            continue;
                        }
                        let (k, ticket) = taken.remove(i.index(taken.len()));
                        let latest = current.get(&k) == Some(&ticket);
                        let kept = tickets.finish(k, ticket, if ok { Ok(ticket) } else { Err("no".into()) });
                        prop_assert_eq!(kept, latest, "kept exactly when no forget came since its ask");
                        if kept {
                            prop_assert_eq!(tickets.ready.get(&k).copied(), ok.then_some(ticket));
                            prop_assert_eq!(tickets.failed.contains(&k), !ok);
                        }
                    }
                }
                for k in 0..KEYS {
                    let k = key(k);
                    prop_assert!(!(tickets.ready.contains_key(&k) && tickets.failed.contains(&k)));
                    prop_assert_eq!(tickets.pending.get(&k), current.get(&k).filter(|_| !tickets.ready.contains_key(&k) && !tickets.failed.contains(&k)));
                    if let Some(&made) = tickets.ready.get(&k) {
                        prop_assert_eq!(Some(&made), current.get(&k), "a kept thumbnail is the latest ask's");
                    }
                    if !current.contains_key(&k) {
                        prop_assert!(!tickets.ready.contains_key(&k) && !tickets.failed.contains(&k) && !tickets.pending.contains_key(&k), "a forgotten key is gone");
                    }
                }
            }
        }
    }
}
