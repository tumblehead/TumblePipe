//! Worker threads started once and kept for the life of the process.
//!
//! The rasteriser and the post pass split each step of a frame across every
//! core, a few dozen steps a frame. Starting threads for each step costs
//! little in a bare process (0.4 ms for twenty), but husk has hundreds of
//! DLLs loaded, each told of every new thread, and there it took 4.6 ms: at
//! eight samples a pixel a fifth of the frame went on starting threads.

use std::cell::Cell;
use std::panic::{self, AssertUnwindSafe};
use std::sync::{Condvar, Mutex, MutexGuard, Once, OnceLock, PoisonError};

/// Threads to split work across: the machine's.
pub(crate) fn threads() -> usize {
    std::thread::available_parallelism().map_or(4, |n| n.get())
}

type Job = &'static (dyn Fn() + Sync);

struct State {
    job: Option<Job>,
    // Bumped for each job, so that a worker runs each once.
    epoch: u64,
    // Workers started, and those still running the current job.
    workers: usize,
    running: usize,
    panicked: bool,
}

struct Pool {
    state: Mutex<State>,
    start: Condvar,
    done: Condvar,
    // One job at a time: a second caller waits its turn.
    turn: Mutex<()>,
}

thread_local! {
    // On a worker, or on a caller inside its job: work started there runs
    // on that thread alone, where waiting on the pool would never end.
    static INSIDE: Cell<bool> = const { Cell::new(false) };
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn pool() -> &'static Pool {
    static POOL: OnceLock<Pool> = OnceLock::new();
    static STARTED: Once = Once::new();
    let pool = POOL.get_or_init(|| Pool {
        state: Mutex::new(State { job: None, epoch: 0, workers: 0, running: 0, panicked: false }),
        start: Condvar::new(),
        done: Condvar::new(),
        turn: Mutex::new(()),
    });
    STARTED.call_once(|| {
        // The caller works too, so one fewer. A thread the OS refuses is
        // one fewer again, never a job waiting on it.
        for _ in 1..threads() {
            let started = std::thread::Builder::new().name("hdThPlayblast".into()).spawn(move || pool.work());
            if started.is_ok() {
                lock(&pool.state).workers += 1;
            }
        }
    });
    pool
}

impl Pool {
    fn work(&self) {
        INSIDE.with(|c| c.set(true));
        let mut seen = 0;
        loop {
            let job = {
                let mut s = lock(&self.state);
                while s.epoch == seen {
                    s = self.start.wait(s).unwrap_or_else(PoisonError::into_inner);
                }
                seen = s.epoch;
                s.job.expect("every epoch has a job")
            };
            let ok = panic::catch_unwind(AssertUnwindSafe(job)).is_ok();
            let mut s = lock(&self.state);
            s.panicked |= !ok;
            s.running -= 1;
            if s.running == 0 {
                self.done.notify_all();
            }
        }
    }
}

/// Run `f` on every worker and on the calling thread at once, and return
/// when all have finished it: `std::thread::scope` spawning `f` on
/// [`threads`] threads, without starting any. A panic in `f` on any of them
/// panics here once all have finished.
pub(crate) fn each_thread(f: &(dyn Fn() + Sync)) {
    if INSIDE.with(Cell::get) {
        f();
        return;
    }
    let pool = pool();
    let _turn = lock(&pool.turn);
    // SAFETY: the workers are done with `job` before they count themselves
    // out of `running`, and nothing between here and the wait below can
    // unwind (`f`'s panic is caught), so no worker calls `f` after this
    // function returns.
    let job: Job = unsafe { std::mem::transmute::<&(dyn Fn() + Sync), Job>(f) };
    {
        let mut s = lock(&pool.state);
        s.job = Some(job);
        s.epoch += 1;
        s.running = s.workers;
        s.panicked = false;
    }
    pool.start.notify_all();
    INSIDE.with(|c| c.set(true));
    let mine = panic::catch_unwind(AssertUnwindSafe(f));
    INSIDE.with(|c| c.set(false));
    let theirs = {
        let mut s = lock(&pool.state);
        while s.running > 0 {
            s = pool.done.wait(s).unwrap_or_else(PoisonError::into_inner);
        }
        s.job = None;
        s.panicked
    };
    if let Err(e) = mine {
        panic::resume_unwind(e);
    }
    assert!(!theirs, "a worker thread panicked");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn every_thread_runs_the_job_once_and_the_call_waits_for_them() {
        for _ in 0..200 {
            let ran = AtomicUsize::new(0);
            each_thread(&|| {
                std::thread::yield_now();
                ran.fetch_add(1, Ordering::SeqCst);
            });
            assert_eq!(ran.load(Ordering::SeqCst), lock(&pool().state).workers + 1);
        }
    }

    #[test]
    fn work_started_inside_a_job_runs_inline() {
        let ran = AtomicUsize::new(0);
        each_thread(&|| each_thread(&|| {
            ran.fetch_add(1, Ordering::SeqCst);
        }));
        assert_eq!(ran.load(Ordering::SeqCst), lock(&pool().state).workers + 1);
    }

    #[test]
    fn a_panic_on_a_worker_reaches_the_caller_and_the_pool_carries_on() {
        let caller = std::thread::current().id();
        let r = panic::catch_unwind(|| each_thread(&|| assert_eq!(std::thread::current().id(), caller, "TEST")));
        assert!(r.is_err() || lock(&pool().state).workers == 0);
        let ran = AtomicUsize::new(0);
        each_thread(&|| {
            ran.fetch_add(1, Ordering::SeqCst);
        });
        assert_eq!(ran.load(Ordering::SeqCst), lock(&pool().state).workers + 1);
    }

    #[test]
    fn callers_on_several_threads_take_turns() {
        let ran = AtomicUsize::new(0);
        std::thread::scope(|scope| {
            for _ in 0..4 {
                scope.spawn(|| {
                    for _ in 0..50 {
                        each_thread(&|| {
                            ran.fetch_add(1, Ordering::SeqCst);
                        });
                    }
                });
            }
        });
        assert_eq!(ran.load(Ordering::SeqCst), 4 * 50 * (lock(&pool().state).workers + 1));
    }
}
