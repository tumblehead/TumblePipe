//! An edit: a cut of the film, the sequence editor's document
//! (`designs/edit-entity.md` §2).
//!
//! - [`document`]: the working edit (take rules that float) and the frozen
//!   one a publish holds (every rule resolved), as JSON.
//! - [`ops`]: what the editor does to an edit, each checked against the
//!   document's invariants and its sources' available frames, refused
//!   rather than clamped.
//! - [`history`]: undo and redo.
//! - [`diff`]: what changed between two versions of an edit.
//! - [`picture`]: what is on screen and in the mix at an edit frame.
//! - [`freeze`]: a working edit resolved for publish.
//! - [`otio`]: the frozen edit as an OpenTimelineIO timeline, for Resolve.
//!
//! Pure: no window, no decoding, no file system. The pipeline (`th_pipe`)
//! answers the [`ops::Sources`] and [`freeze::Resolver`] questions.

pub mod diff;
pub mod document;
pub mod freeze;
pub mod history;
pub mod ops;
pub mod otio;
pub mod picture;
pub mod takes;

pub use document::{
    AudioClip, Clip, Edit, FrozenEdit, FrozenSource, FrozenTake, Marker, Rule, Source, TakeKind, Track, Version, VersionRule, VideoClip,
    WorkingEdit,
};
