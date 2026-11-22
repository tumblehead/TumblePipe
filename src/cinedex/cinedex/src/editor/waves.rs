//! What an audio clip's row draws of its sound (`waveform`): its source's
//! peaks placed as `place_sounds` places the sound for the speaker (the
//! same in point, gain and fades), or a placeholder while the source is
//! still being resolved and its sound read.

use th_edit::document::{TrackKind, TrackRef};

use super::Editor;
use super::entries::EntryState;
use crate::waveform::{Placed, Wave};

impl Editor {
    /// The waveform of clip `index` of `track`: None on a video track, on
    /// a source that failed, and on one with no sound.
    pub(super) fn clip_wave(&self, track: TrackRef, index: usize) -> Option<Wave> {
        if track.kind != TrackKind::Audio {
            return None;
        }
        let clip = self.doc.edit.audio.get(track.index)?.clips.get(index)?;
        let Some(entry) = self.entry_of(&clip.source).map(|i| &self.entries[i]) else { return Some(Wave::Pending) };
        match &entry.state {
            EntryState::Resolving => Some(Wave::Pending),
            EntryState::Failed(_) => None,
            EntryState::Ready { resolved, .. } => {
                let peaks = resolved.peaks.clone()?;
                let fps = f64::from(self.fps());
                Some(Wave::Ready(Placed {
                    peaks,
                    at: clip.at,
                    duration: clip.duration,
                    offset: (clip.source_in - resolved.first) as f64 / fps,
                    fps,
                    gain: 10f64.powf(clip.gain_db / 20.0) as f32,
                    fade_in: clip.fade_in,
                    fade_out: clip.fade_out,
                }))
            }
        }
    }
}
