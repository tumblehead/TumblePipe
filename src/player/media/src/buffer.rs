//! The decoded-frame buffer: every frame the player has decoded, kept under a
//! byte budget. Stepping onto a frame in the buffer costs nothing; when the
//! budget is exceeded the frames furthest from the playhead go first, so what
//! survives is what the user is most likely to step to next.

use std::collections::HashMap;

use crate::Frame;

/// A frame's address: which clip of the playlist, which frame of it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct FrameKey {
    pub clip: usize,
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

    /// Keep `frame`, then evict down to the budget, furthest from `playhead`
    /// first. `distance` measures how far a key is from the playhead in the
    /// player's own timeline order (clips laid end to end).
    pub fn insert(&mut self, key: FrameKey, frame: Frame, playhead: FrameKey, distance: impl Fn(FrameKey, FrameKey) -> usize) {
        self.used += frame.byte_size();
        if let Some(old) = self.frames.insert(key, frame) {
            self.used -= old.byte_size();
        }
        if self.used <= self.budget {
            return;
        }
        let mut keys: Vec<FrameKey> = self.frames.keys().copied().collect();
        keys.sort_by_key(|&k| std::cmp::Reverse(distance(k, playhead)));
        for k in keys {
            if self.used <= self.budget || k == playhead {
                break;
            }
            if let Some(old) = self.frames.remove(&k) {
                self.used -= old.byte_size();
            }
        }
    }

    /// Drop every frame of `clip` (its view changed, so they show the wrong
    /// thing).
    pub fn remove_clip(&mut self, clip: usize) {
        let frames = std::mem::take(&mut self.frames);
        self.frames = frames.into_iter().filter(|(k, _)| k.clip != clip).collect();
        self.used = self.frames.values().map(Frame::byte_size).sum();
    }

    pub fn clear(&mut self) {
        self.frames.clear();
        self.used = 0;
    }
}
