//! Frame ranges (`tumblepipe.config.timeline`).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::config::{Config, ConfigError, Properties};
use crate::uri::Uri;

/// `first..=last` by `step`. Unconstructable when `step <= 0` or
/// `first > last`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct BlockRange {
    pub first_frame: i64,
    pub last_frame: i64,
    pub step_size: i64,
}

impl BlockRange {
    pub fn new(first_frame: i64, last_frame: i64, step_size: i64) -> Result<Self, String> {
        if step_size <= 0 {
            return Err(format!("step_size must be positive, got {step_size}"));
        }
        if first_frame > last_frame {
            return Err(format!("first_frame ({first_frame}) cannot be greater than last_frame ({last_frame})"));
        }
        Ok(BlockRange { first_frame, last_frame, step_size })
    }

    /// How many frames it has.
    pub fn len(&self) -> usize {
        ((self.last_frame - self.first_frame) / self.step_size + 1) as usize
    }

    pub fn is_empty(&self) -> bool {
        false
    }

    /// Its frames, in order.
    pub fn frames(&self) -> impl Iterator<Item = i64> + use<> {
        (self.first_frame..=self.last_frame).step_by(self.step_size as usize)
    }

    pub fn contains(&self, frame: i64) -> bool {
        frame >= self.first_frame && frame <= self.last_frame && (frame - self.first_frame) % self.step_size == 0
    }
}

/// A shot's range: its frames `start..=end` and the roll either side.
/// Unconstructable when a [`BlockRange`] of it would be, when a roll is
/// negative, or when the first rolled frame is not positive.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct FrameRange {
    pub start_frame: i64,
    pub end_frame: i64,
    pub start_roll: i64,
    pub end_roll: i64,
    pub step_size: i64,
}

impl FrameRange {
    pub fn new(start_frame: i64, end_frame: i64, start_roll: i64, end_roll: i64, step_size: i64) -> Result<Self, String> {
        if step_size <= 0 {
            return Err(format!("step_size must be positive, got {step_size}"));
        }
        if start_frame > end_frame {
            return Err(format!("start_frame ({start_frame}) cannot be greater than end_frame ({end_frame})"));
        }
        if start_roll < 0 {
            return Err(format!("start_roll must be non-negative, got {start_roll}"));
        }
        if end_roll < 0 {
            return Err(format!("end_roll must be non-negative, got {end_roll}"));
        }
        if start_frame - start_roll <= 0 {
            return Err(format!(
                "start_frame - start_roll must be positive, got {start_frame} - {start_roll} = {}",
                start_frame - start_roll
            ));
        }
        Ok(FrameRange { start_frame, end_frame, start_roll, end_roll, step_size })
    }

    /// The frames without roll.
    pub fn play_range(&self) -> BlockRange {
        BlockRange { first_frame: self.start_frame, last_frame: self.end_frame, step_size: self.step_size }
    }

    /// The frames with roll: what a playblast covers.
    pub fn full_range(&self) -> BlockRange {
        BlockRange {
            first_frame: self.start_frame - self.start_roll,
            last_frame: self.end_frame + self.end_roll,
            step_size: self.step_size,
        }
    }
}

/// An integer property, as Python's `int(...)` would take it from JSON.
fn integer(properties: &Properties, key: &str) -> Result<i64, String> {
    match properties.get(key) {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)).ok_or_else(|| format!("{key} is {n}")),
        Some(Value::Bool(b)) => Ok(*b as i64),
        other => Err(format!("{key} is {other:?}, not a number")),
    }
}

/// `entity`'s frame range (`get_frame_range`); None when it does not exist
/// or lacks one of `frame_start`, `frame_end`, `roll_start`, `roll_end`.
pub fn get_frame_range(config: &Config, entity: &Uri) -> Result<Option<FrameRange>, String> {
    let Some(properties) = config.get_properties(entity).map_err(|e: ConfigError| e.to_string())? else { return Ok(None) };
    if ["frame_start", "frame_end", "roll_start", "roll_end"].iter().any(|key| !properties.contains_key(*key)) {
        return Ok(None);
    }
    FrameRange::new(
        integer(&properties, "frame_start")?,
        integer(&properties, "frame_end")?,
        integer(&properties, "roll_start")?,
        integer(&properties, "roll_end")?,
        1,
    )
    .map(Some)
}

/// `entity`'s fps (`get_fps`), its own or inherited; None when none is set.
pub fn get_fps(config: &Config, entity: &Uri) -> Result<Option<i64>, String> {
    let Some(properties) = config.get_properties(entity).map_err(|e| e.to_string())? else { return Ok(None) };
    if !properties.contains_key("fps") {
        return Ok(None);
    }
    integer(&properties, "fps").map(Some)
}

/// [`get_fps`], refused when there is none (`require_fps`).
pub fn require_fps(config: &Config, entity: &Uri) -> Result<i64, String> {
    get_fps(config, entity)?.ok_or_else(|| format!("No fps configured for {entity} or the project"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranges_count_like_python() {
        let range = BlockRange::new(1, 10, 3).unwrap();
        assert_eq!(range.frames().collect::<Vec<_>>(), [1, 4, 7, 10]);
        assert_eq!(range.len(), 4);
        assert!(range.contains(7) && !range.contains(8));
        assert!(BlockRange::new(2, 1, 1).is_err());
        let shot = FrameRange::new(1001, 1010, 5, 2, 1).unwrap();
        assert_eq!(shot.full_range(), BlockRange::new(996, 1012, 1).unwrap());
        assert!(FrameRange::new(3, 10, 3, 0, 1).is_err());
    }
}
