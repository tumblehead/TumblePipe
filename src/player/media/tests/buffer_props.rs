//! Property tests for the decoded-frame buffer, over generated sequences of
//! inserts (frames of mixed sizes, re-inserts of a key, a moving playhead)
//! checked against a plain model of what it should hold.
//!
//! Stepping is only instant if the frames near the playhead survive, and the
//! budget is what keeps a long playlist of EXRs from eating the machine, so
//! both halves of the contract are pinned: the byte count is exact and kept
//! under budget, and eviction takes the furthest frames first and never the
//! playhead's.

use std::collections::HashMap;
use std::sync::Arc;

use half::f16;
use proptest::prelude::*;

use th_media::{Frame, FrameBuffer, FrameKey, Pixels};

/// Keys laid out as the player's timeline would, for a playlist of clips of
/// at most this many frames.
const CLIP_SPAN: usize = 1000;

fn position(key: FrameKey) -> usize {
    key.clip * CLIP_SPAN + key.index
}

fn distance(a: FrameKey, b: FrameKey) -> usize {
    position(a).abs_diff(position(b))
}

fn frame(bytes: usize) -> Frame {
    Frame { width: 1, height: 1, pixels: Arc::new(Pixels::Half { data: vec![f16::ZERO; bytes / 2], channels: 1 }) }
}

fn key() -> impl Strategy<Value = FrameKey> {
    (0usize..3, 0usize..40).prop_map(|(clip, index)| FrameKey { clip, index })
}

#[derive(Debug, Clone)]
struct Insert {
    key: FrameKey,
    bytes: usize,
    playhead: FrameKey,
}

fn inserts() -> impl Strategy<Value = Vec<Insert>> {
    let insert = (key(), 1usize..20, key()).prop_map(|(key, halfs, playhead)| Insert { key, bytes: halfs * 2, playhead });
    prop::collection::vec(insert, 1..120)
}

proptest! {
    #[test]
    fn insert_keeps_the_budget_and_the_nearest_frames(budget in 0usize..200, inserts in inserts()) {
        let mut buffer = FrameBuffer::new(budget);
        // What the buffer should hold, key -> bytes.
        let mut model: HashMap<FrameKey, usize> = HashMap::new();
        for Insert { key, bytes, playhead } in inserts {
            let playhead_held = model.contains_key(&playhead) || key == playhead;
            model.insert(key, bytes);
            buffer.insert(key, frame(bytes), playhead, distance);

            let evicted: Vec<FrameKey> = model.keys().copied().filter(|k| !buffer.contains(*k)).collect();
            for k in &evicted {
                model.remove(k);
            }
            let kept: Vec<FrameKey> = model.keys().copied().collect();

            prop_assert_eq!(buffer.len(), model.len(), "the buffer holds a key nobody inserted");
            prop_assert_eq!(buffer.used_bytes(), model.values().sum::<usize>(), "byte count drifted");
            for (k, &bytes) in &model {
                prop_assert_eq!(buffer.get(*k).map(Frame::byte_size), Some(bytes), "{:?} holds a stale frame", k);
            }
            if playhead_held {
                prop_assert!(buffer.contains(playhead), "the playhead's frame was evicted");
            }
            // Over budget only when the playhead's frame alone is too big.
            if buffer.used_bytes() > budget {
                prop_assert_eq!(kept.clone(), vec![playhead], "over budget with frames to spare");
            }
            // Nothing kept is further from the playhead than anything evicted.
            if let (Some(near_evicted), Some(far_kept)) = (
                evicted.iter().map(|&k| distance(k, playhead)).min(),
                kept.iter().map(|&k| distance(k, playhead)).max(),
            ) {
                prop_assert!(far_kept <= near_evicted, "kept a frame {} away, evicted one {} away", far_kept, near_evicted);
            }
        }
    }

    #[test]
    fn under_budget_nothing_is_evicted(keys in prop::collection::vec(key(), 1..80)) {
        let mut buffer = FrameBuffer::new(usize::MAX);
        for &key in &keys {
            buffer.insert(key, frame(8), keys[0], distance);
        }
        for key in keys {
            prop_assert!(buffer.contains(key));
        }
    }

    #[test]
    fn clear_empties(inserts in inserts()) {
        let mut buffer = FrameBuffer::new(100);
        for Insert { key, bytes, playhead } in inserts {
            buffer.insert(key, frame(bytes), playhead, distance);
        }
        buffer.clear();
        prop_assert!(buffer.is_empty());
        prop_assert_eq!(buffer.used_bytes(), 0);
    }
}

proptest! {
    #[test]
    fn remove_clip_drops_that_clip_only(inserts in inserts(), clip in 0usize..3) {
        let mut buffer = FrameBuffer::new(usize::MAX);
        let mut model: HashMap<FrameKey, usize> = HashMap::new();
        for Insert { key, bytes, playhead } in inserts {
            model.insert(key, bytes);
            buffer.insert(key, frame(bytes), playhead, distance);
        }
        buffer.remove_clip(clip);
        model.retain(|k, _| k.clip != clip);
        prop_assert_eq!(buffer.len(), model.len());
        prop_assert_eq!(buffer.used_bytes(), model.values().sum::<usize>());
        for k in model.keys() {
            prop_assert!(buffer.contains(*k));
        }
    }
}
