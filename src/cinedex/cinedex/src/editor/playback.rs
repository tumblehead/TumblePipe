//! Playing the cut: the playhead, the clock and sound, and the picture
//! shown at the playhead.

use std::sync::Arc;
use std::time::Instant;

use primer::ImageHandle;
use th_edit::document::{Frame as EditFrame, Source, Transform};
use th_media::{Frame, FrameKey, Proxy};

use crate::audio::{Placed, Speaker};
use crate::colour::Lut;
use crate::engine::Layer;
use crate::video::Picture;

use super::entries::{EntryState, Resolved};
use super::{Editor, source_name};

/// The frame on screen and the layers it was made of.
pub(super) struct Shown {
    /// Each layer's frame, its opacity's bits and its transform.
    pub(super) layers: Vec<(FrameKey, u32, Transform)>,
    pub(super) picture: Picture,
    pub(super) source: usize,
}

impl Editor {
    pub(super) fn move_to(&mut self, position: usize) {
        self.position = position.min(self.length().saturating_sub(1));
        self.engine.set_playhead(self.position);
        self.refresh_shown();
    }

    fn sound_clock(&self) -> Option<&Speaker> {
        self.speaker.as_ref().filter(|s| s.has_voices())
    }

    pub(super) fn seconds(&self, position: usize) -> f64 {
        position as f64 / f64::from(self.fps())
    }

    fn position_at(&self, seconds: f64) -> usize {
        (seconds * f64::from(self.fps()) + 1e-6).max(0.0) as usize
    }

    pub(super) fn set_playing(&mut self, playing: bool) {
        self.playing = playing;
        self.clock = (Instant::now(), self.position);
        if let Some(speaker) = &self.speaker {
            if playing {
                speaker.play_from(self.seconds(self.position));
            } else {
                speaker.stop();
            }
        }
    }

    /// The sound of every clip that has one.
    pub(super) fn place_sounds(&self) {
        let Some(speaker) = &self.speaker else { return };
        let fps = f64::from(self.fps());
        let mut voices = Vec::new();
        let mut place =
            |source: &Source, at: EditFrame, duration: EditFrame, source_in: EditFrame, gain_db: f64, fades: (EditFrame, EditFrame)| {
                let Some(i) = self.entry_of(source) else { return };
                let Some(resolved) = self.entries[i].resolved() else { return };
                let Some(sound) = resolved.sound.clone() else { return };
                let skip = (resolved.first - source_in).max(0);
                if skip >= duration {
                    return;
                }
                voices.push(Placed {
                    sound,
                    at: (at + skip) as f64 / fps,
                    length: (duration - skip) as f64 / fps,
                    offset: (source_in + skip - resolved.first) as f64 / fps,
                    gain: 10f64.powf(gain_db / 20.0) as f32,
                    fade_in: (fades.0 - skip).max(0) as f64 / fps,
                    fade_out: fades.1 as f64 / fps,
                });
            };
        for c in self.doc.edit.video.iter().flat_map(|t| &t.clips) {
            if matches!(c.source, Source::Shot { .. }) {
                place(&c.source, c.at, c.duration, c.source_in, 0.0, (c.fade_in, c.fade_out));
            }
        }
        for c in self.doc.edit.audio.iter().flat_map(|t| &t.clips) {
            place(&c.source, c.at, c.duration, c.source_in, c.gain_db, (c.fade_in, c.fade_out));
        }
        speaker.set_voices(voices);
    }

    /// Whether `position`'s picture can show: every layer's frame is in or
    /// failed.
    fn settled(&self, position: usize) -> bool {
        self.engine.layers(position).iter().all(|l| self.engine.is_buffered(l.key) || self.engine.failure(l.key).is_some())
    }

    /// One playback tick , at the edit's rate.
    pub(super) fn advance(&mut self) {
        let next = self.position + 1;
        if let Some((start, end)) = self.loop_range()
            && next >= end.min(self.length())
        {
            self.move_to(start);
            self.set_playing(true);
            return;
        }
        if next >= self.length() {
            self.set_playing(false);
            return;
        }
        if let Some(speaker) = self.sound_clock()
            && !speaker.is_playing()
        {
            if self.settled(next) {
                speaker.play_from(self.seconds(self.position));
            }
            return;
        }
        let due = match self.sound_clock() {
            Some(speaker) => self.position_at(speaker.seconds()),
            None => {
                let (started, from) = self.clock;
                from + (started.elapsed().as_secs_f64() * f64::from(self.fps())) as usize
            }
        };
        if self.position >= due {
            return;
        }
        let last = self.loop_range().map_or(self.length(), |(_, end)| end.min(self.length())) - 1;
        let target = if self.sound_clock().is_some() { due.min(last) } else { next };
        let ready = (next..=target).take_while(|&p| self.settled(p)).last();
        if ready != Some(target) {
            self.clock = (Instant::now(), ready.unwrap_or(self.position));
            if let Some(speaker) = self.sound_clock() {
                speaker.stop();
            }
        }
        if let Some(to) = ready {
            self.move_to(to);
        }
    }

    pub(super) fn refresh_shown(&mut self) {
        let layers = self.engine.layers(self.position);
        let wanted: Vec<(FrameKey, u32, Transform)> = layers.iter().map(|l| (l.key, l.opacity.to_bits(), l.transform)).collect();
        if !self.reshow && self.shown.as_ref().is_some_and(|s| s.layers == wanted) {
            return;
        }
        if layers.is_empty() {
            self.shown = None;
            return;
        }
        let top = layers.last().unwrap().key.source;
        // One whole clip as fitted goes to the screen as it is; anything
        // blended or transformed is made into one picture first.
        let blended = layers.len() > 1 || layers[0].opacity < 1.0 || !layers[0].transform.is_identity();
        let picture = if blended {
            self.dissolved(&layers).map(|f| (f, None))
        } else {
            let lut = self.lut_of(top);
            self.engine.frame(layers[0].key).map(|f| (f, lut))
        };
        if let Some((frame, lut)) = picture {
            self.reshow = false;
            self.frames_shown += 1;
            self.shown = Some(Shown { layers: wanted, picture: Picture { id: self.frames_shown, frame, lut }, source: top });
        }
    }

    fn lut_of(&self, source: usize) -> Option<Arc<Lut>> {
        match &self.entry_of_engine(source)?.state {
            EntryState::Ready { lut: Ok(lut), .. } => lut.clone(),
            _ => None,
        }
    }

    fn dissolved(&self, layers: &[Layer]) -> Option<Frame> {
        let frames: Vec<Frame> = layers.iter().map(|l| self.engine.frame(l.key)).collect::<Option<_>>()?;
        let luts: Vec<Option<Arc<Lut>>> = layers.iter().map(|l| self.lut_of(l.key.source)).collect();
        let top = frames.last()?;
        let gain = 2f32.powf(self.display.exposure);
        let input: Vec<th_media::composite::Placed> = frames
            .iter()
            .zip(layers)
            .map(|(frame, l)| th_media::composite::Placed { frame, opacity: l.opacity, placement: crate::placing::placement(l.transform) })
            .collect();
        // A transform places a clip in the edit's picture, so that is the
        // picture's shape then; else the top frame's own, as it was.
        let (width, height) = if layers.iter().any(|l| !l.transform.is_identity()) {
            crate::placing::canvas(self.doc.edit.resolution, frames.iter().map(|f| (f.width, f.height)))
        } else {
            (top.width, top.height)
        };
        Some(th_media::composite::dissolve_placed(width, height, &input, |layer, rgb| match &luts[layer] {
            Some(lut) => lut.sample(rgb.map(|c| c * gain)),
            None => rgb,
        }))
    }

    /// What the picture says when it has nothing: why the top clip under
    /// the playhead plays nothing.
    pub(super) fn card(&self) -> Option<String> {
        let at = self.position as EditFrame;
        let clip = self.doc.edit.video.iter().rev().flat_map(|t| t.clips.iter()).find(|c| c.at <= at && at < c.at + c.duration)?;
        let entry = &self.entries[self.entry_of(&clip.source)?];
        match (&entry.state, &clip.source) {
            (_, Source::Slug { text }) => Some(text.clone()),
            (EntryState::Ready { resolved, .. }, _) if resolved.media.is_some() => None,
            _ => Some(format!("{}: {}", source_name(&clip.source), entry.explain())),
        }
    }

    pub(super) fn collect_proxies(&mut self) {
        for (source, proxy) in self.engine.take_proxies() {
            let handle = self.proxy_handle(source, &proxy);
            self.proxies.insert(source, (proxy, handle));
        }
    }

    fn proxy_handle(&self, source: usize, proxy: &Proxy) -> Option<ImageHandle> {
        let thumbnail = match self.lut_of(source) {
            Some(lut) => proxy.shown(|rgb| lut.sample(rgb)),
            None => proxy.shown(|rgb| rgb),
        };
        Some(ImageHandle::from_rgba(thumbnail.width, thumbnail.height, thumbnail.rgba))
    }

    pub(super) fn set_display(&mut self) {
        let Self { entries, display, .. } = self;
        for entry in entries.iter_mut() {
            if let EntryState::Ready { resolved: Resolved { media: Some(media), .. }, lut, .. } = &mut entry.state {
                *lut = display.lut(media);
            }
        }
        self.bin_thumbs_redisplay();
        let sources: Vec<usize> = self.proxies.keys().copied().collect();
        for source in sources {
            let handle = self.proxy_handle(source, &self.proxies[&source].0);
            self.proxies.get_mut(&source).unwrap().1 = handle;
        }
        self.shown = None;
        self.refresh_shown();
    }

    /// The range playback loops over (`start..end`), if any.
    pub(super) fn loop_range(&self) -> Option<(usize, usize)> {
        self.live_loop.as_ref().map(crate::live_view::Loop::span)
    }

    /// The playhead was moved by hand: a loop it left is over.
    pub(super) fn leave_loop_outside(&mut self) {
        if self.loop_range().is_some_and(|(start, end)| self.position < start || self.position >= end) {
            self.live_loop = None;
        }
    }
}
