//! Clean Up Project: list what a TumblePipe project no longer needs, by
//! category, and delete it (`tumblepipe.cleanup`, ported).
//!
//! The six categories decide exactly as the Python package does, entry for
//! entry, reason for reason; `tests/test_cleanup_parity.py` holds the two
//! to that through the `cleanup_lines` example. Nothing here needs the
//! window's crates, so the library builds and tests on its own.

pub mod backups;
pub mod caches;
pub mod cli;
pub mod core;
pub mod deleted;
pub mod exports;
pub mod fsx;
pub mod hip;
pub mod inventory;
pub mod jobs;
pub mod overview;
pub mod py;
pub mod renders;
pub mod roots;
pub mod usd;

pub use crate::core::{CategoryPlan, Entry, Farm, Plan, Settings, apply, make_plan, summary};
pub use crate::roots::ProjectRoots;
