//! The decoded-frame buffer: every frame the player has decoded, kept under a
//! byte budget. Stepping onto a frame in the buffer costs nothing; when the
//! budget is exceeded the frames furthest from the playhead go first, so what
//! survives is what the user is most likely to step to next. The frames at
//! the playhead (every layer it shows) are never evicted.

use std::collections::HashMap;

use crate::Frame;

/// A frame's address: which source (a take the player has open), which
/// frame of its media. Clips placed on the player's timeline show frames of
/// sources, so two clips of one take share their frames.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct FrameKey {
    pub source: usize,
    pub index: usize,
}

#[derive(Debug)]
pub struct FrameBuffer {
    budget: usize,
    used: usize,
    frames: HashMap<FrameKey, Frame>,
}

impl FrameBuffer {
    pub fn new(budget_bytes: usize) -> Self {
        Self { budget: budget_bytes, used: 0, frames: HashMap::new() }
    }

    pub fn get(&self, key: FrameKey) -> Option<&Frame> {
        self.frames.get(&key)
    }

    pub fn contains(&self, key: FrameKey) -> bool {
        self.frames.contains_key(&key)
    }

    pub fn keys(&self) -> impl Iterator<Item = FrameKey> + '_ {
        self.frames.keys().copied()
    }

    pub fn len(&self) -> usize {
        self.frames.len()
    }

    pub fn is_empty(&self) -> bool {
        self.frames.is_empty()
    }

    pub fn used_bytes(&self) -> usize {
        self.used
    }

    /// Keep `frame`, then evict down to the budget, furthest from the
    /// playhead first. `distance` measures how far a key is from the playhead
    /// in the player's timeline; a key at distance 0 (a frame the playhead
    /// shows) is never evicted.
    pub fn insert(&mut self, key: FrameKey, frame: Frame, distance: impl Fn(FrameKey) -> usize) {
        self.used += frame.byte_size();
        if let Some(old) = self.frames.insert(key, frame) {
            self.used -= old.byte_size();
        }
        if self.used <= self.budget {
            return;
        }
        let mut keys: Vec<(usize, FrameKey)> = self.frames.keys().map(|&k| (distance(k), k)).collect();
        keys.sort_by_key(|&(d, _)| std::cmp::Reverse(d));
        for (d, k) in keys {
            if self.used <= self.budget || d == 0 {
                break;
            }
            if let Some(old) = self.frames.remove(&k) {
                self.used -= old.byte_size();
            }
        }
    }

    /// Drop the frame of `key`, if kept (its file was written anew).
    pub fn remove(&mut self, key: FrameKey) {
        if let Some(old) = self.frames.remove(&key) {
            self.used -= old.byte_size();
        }
    }

    /// Drop every frame of `source` (its view changed, so they show the
    /// wrong thing).
    pub fn remove_source(&mut self, source: usize) {
        let frames = std::mem::take(&mut self.frames);
        self.frames = frames.into_iter().filter(|(k, _)| k.source != source).collect();
        self.used = self.frames.values().map(Frame::byte_size).sum();
    }

    pub fn clear(&mut self) {
        self.frames.clear();
        self.used = 0;
    }
}
