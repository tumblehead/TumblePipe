//! Stopping a frame part way: the viewport's render thread starts a new
//! frame whenever the camera or the scene changes, and the one it was
//! drawing is no longer wanted.
//!
//! [`crate::ffi::th_render_ids`] takes a flag the caller sets to give up. It
//! is handed down as the calling thread's [`Token`] rather than through
//! [`crate::raster::Frame`], so every render entry point and test keeps its
//! signature; the rasteriser captures the token on the calling thread and
//! checks it between batches and bands (also on the pool's threads), the
//! post pass before its ambient occlusion. A cancelled frame's pixels are
//! undefined; `th_render_ids` reports it and copies nothing out.

use std::cell::Cell;
use std::sync::atomic::{AtomicU32, Ordering};

thread_local! {
    static CURRENT: Cell<*const AtomicU32> = const { Cell::new(std::ptr::null()) };
}

/// The calling thread's cancel flag, or none. Copied into work that runs on
/// other threads, it must not outlive the [`Scope`] that set it, which the
/// render call it is captured in never does.
#[derive(Clone, Copy)]
pub(crate) struct Token(*const AtomicU32);

// The flag is an atomic, read only, and outlives every render using it.
unsafe impl Send for Token {}
unsafe impl Sync for Token {}

impl Token {
    pub fn current() -> Token {
        Token(CURRENT.with(Cell::get))
    }

    /// Whether the caller has asked to give the frame up.
    pub fn is_set(&self) -> bool {
        // SAFETY: non-null only between a Scope's creation and drop, during
        // which the caller keeps the flag alive (th_render_ids's contract).
        !self.0.is_null() && unsafe { &*self.0 }.load(Ordering::Relaxed) != 0
    }
}

/// Makes `flag` the calling thread's token until dropped.
pub(crate) struct Scope {
    previous: *const AtomicU32,
}

impl Scope {
    /// # Safety
    /// `flag` must be null or stay valid until the scope is dropped.
    pub unsafe fn new(flag: *const AtomicU32) -> Scope {
        Scope { previous: CURRENT.with(|c| c.replace(flag)) }
    }
}

impl Drop for Scope {
    fn drop(&mut self) {
        CURRENT.with(|c| c.set(self.previous));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_token_sees_its_scope_flag_and_nothing_after() {
        assert!(!Token::current().is_set());
        let flag = AtomicU32::new(0);
        {
            let _scope = unsafe { Scope::new(&flag) };
            let token = Token::current();
            assert!(!token.is_set());
            flag.store(1, Ordering::Relaxed);
            assert!(token.is_set());
            assert!(std::thread::scope(|s| s.spawn(move || token.is_set()).join().unwrap()));
        }
        assert!(!Token::current().is_set());
    }
}
