//! The sequence player's playlist: shots in order, each with the takes it
//! can show (one per department and version), read from an OTIO timeline.
//!
//! TumblePipe writes the timeline (`tumblepipe.pipe.review_playlist`): one
//! video track, a clip per shot whose media reference is the take it opens
//! on, and every take of the shot under the clip's `tumblepipe` metadata:
//!
//! ```json
//! "metadata": {"tumblepipe": {
//!     "entity": "entity:/shots/HideAndReek/010",
//!     "take": 0,
//!     "fps": 24,
//!     "takes": [
//!         {"label": "animation v0015", "department": "animation",
//!          "version": "v0015", "kind": "movie", "layer": null,
//!          "path": "P:/.../v0015.mp4"},
//!         {"label": "denoise v0030 default beauty", "department": "denoise",
//!          "version": "v0030", "kind": "exr", "layer": "default",
//!          "path": "P:/.../beauty.1001.exr"}
//!     ]}}
//! ```
//!
//! and the timeline's own `tumblepipe` metadata names the project's OCIO
//! config (`"ocio": "P:/.../tumblehead.ocio"`). Any other OTIO plays too: a
//! clip without the metadata is one take, its media reference. Gaps are
//! skipped; trims (`source_range`) are not applied, every take plays whole.
//!
//! The takes come in the order the player steps through them: grouped by
//! kind (movies, then EXRs), department in pipeline order, render layer, and
//! versions oldest first. A department (and, for a render, its layer) is a
//! *stream*; its takes are that stream's versions. See [`Shot::version_step`],
//! [`Shot::department_step`] and [`Shot::last_of_kind`].

use std::path::Path;

use otio::{Composable, MediaReference, Timeline};
use serde_json::Value;

/// What a take's media is: a movie (TumblePipe's playblasts) or an EXR
/// sequence (its renders).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Kind {
    Movie,
    Exr,
}

impl Kind {
    /// The kind of the media at `path`, by its name: EXR frames, or a folder
    /// of them, else a movie.
    pub fn of_path(path: &str) -> Self {
        let p = Path::new(path);
        if p.extension().is_some_and(|e| e.eq_ignore_ascii_case("exr")) || p.is_dir() { Self::Exr } else { Self::Movie }
    }

    /// The name the playlist schema gives it.
    pub fn name(self) -> &'static str {
        match self {
            Self::Movie => "movie",
            Self::Exr => "exr",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Take {
    pub label: String,
    /// A movie, or one frame (or the folder) of an EXR sequence.
    pub path: String,
    /// The rate the playlist gives, overriding an EXR sequence's own.
    pub rate: Option<(i32, i32)>,
    pub kind: Kind,
    /// The department that made it; empty when the playlist does not say.
    pub department: String,
    /// A render's layer ("default", "slapcomp"); None for a playblast.
    pub layer: Option<String>,
    /// Its version ("v0015"); empty when the playlist does not say.
    pub version: String,
}

impl Take {
    /// A take the playlist knows only the media of.
    pub fn of_media(label: String, path: String, rate: Option<(i32, i32)>) -> Self {
        let kind = Kind::of_path(&path);
        Self { label, path, rate, kind, department: String::new(), layer: None, version: String::new() }
    }

    /// The stream it is a version of: its kind, department and layer.
    fn stream(&self) -> (Kind, &str, Option<&str>) {
        (self.kind, &self.department, self.layer.as_deref())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Shot {
    pub name: String,
    /// The sequence the shot is in (its entity's path between `shots` and
    /// the shot); None when the playlist does not say.
    pub sequence: Option<String>,
    pub takes: Vec<Take>,
    /// The take shown.
    pub take: usize,
}

impl Shot {
    /// The take `delta` versions from the shown one (positive: newer) in its
    /// stream, stopping at the oldest and the newest.
    pub fn version_step(&self, delta: i64) -> usize {
        let stream = self.takes[self.take].stream();
        let versions: Vec<usize> = (0..self.takes.len()).filter(|&t| self.takes[t].stream() == stream).collect();
        let at = versions.iter().position(|&t| t == self.take).unwrap_or(0) as i64;
        versions[(at + delta).clamp(0, versions.len() as i64 - 1) as usize]
    }

    /// The newest version of the stream `delta` streams from the shown take's
    /// (positive: downstream) among those of its kind, in playlist order. The
    /// shown take when there is no stream that far.
    pub fn department_step(&self, delta: i64) -> usize {
        let shown = self.takes[self.take].stream();
        let mut streams: Vec<(Kind, &str, Option<&str>)> = Vec::new();
        for take in self.takes.iter().filter(|t| t.kind == shown.0) {
            if !streams.contains(&take.stream()) {
                streams.push(take.stream());
            }
        }
        let at = streams.iter().position(|&s| s == shown).unwrap_or(0) as i64;
        match usize::try_from(at + delta).ok().and_then(|t| streams.get(t)) {
            Some(&target) if target != shown => {
                (0..self.takes.len()).rev().find(|&t| self.takes[t].stream() == target).unwrap_or(self.take)
            }
            _ => self.take,
        }
    }

    /// The take a switch to `kind` shows: the last of that kind, the newest
    /// version of its most downstream stream. None when there is none.
    pub fn last_of_kind(&self, kind: Kind) -> Option<usize> {
        (0..self.takes.len()).rev().find(|&t| self.takes[t].kind == kind)
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Playlist {
    pub name: String,
    pub ocio: Option<String>,
    pub shots: Vec<Shot>,
}

/// A media URL as a local path: `file://` URLs decoded, plain paths as they are.
pub fn url_to_path(url: &str) -> String {
    let Some(rest) = url.strip_prefix("file://") else { return url.to_owned() };
    // file:///P:/x -> P:/x (a Windows drive), file:///mnt/x -> /mnt/x.
    let rest = match rest.strip_prefix('/') {
        Some(after) if after.as_bytes().get(1) == Some(&b':') => after,
        _ => rest,
    };
    let bytes = rest.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = |b: u8| (b as char).to_digit(16);
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                out.push((h * 16 + l) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn rate_of(rate: f64) -> Option<(i32, i32)> {
    let rate = otio::FrameRate::from_f64(rate)?;
    Some((i32::try_from(rate.num()).ok()?, i32::try_from(rate.den()).ok()?))
}

/// The take a media reference is, if it points at media.
fn reference_take(name: &str, reference: &MediaReference, rate: Option<(i32, i32)>) -> Option<Take> {
    let path = match reference {
        MediaReference::External(r) => url_to_path(&r.target_url),
        MediaReference::ImageSequence(r) => {
            let start = r.start_frame;
            let pad = usize::try_from(r.frame_zero_padding).unwrap_or(0);
            let base = url_to_path(&r.target_url_base);
            let file = format!("{}{start:0pad$}{}", r.name_prefix, r.name_suffix);
            Path::new(&base).join(file).to_string_lossy().into_owned()
        }
        MediaReference::Missing(_) | MediaReference::Generator(_) => return None,
    };
    let rate = match reference {
        MediaReference::ImageSequence(r) if r.rate > 0.0 => rate_of(r.rate).or(rate),
        _ => rate,
    };
    Some(Take::of_media(name.to_owned(), path, rate))
}

/// The sequence of the shot `entity` names (`entity:/shots/<sequence>/<shot>`,
/// the sequence maybe nested); None for any other entity.
fn sequence_of(entity: &str) -> Option<String> {
    let (sequence, shot) = entity.strip_prefix("entity:/shots/")?.rsplit_once('/')?;
    (!sequence.is_empty() && !shot.is_empty()).then(|| sequence.to_owned())
}

fn metadata_takes(meta: &Value, rate: Option<(i32, i32)>) -> Option<(Vec<Take>, usize)> {
    // The shot's own rate, for its EXR takes; else the clip's range's.
    let rate = meta.get("fps").and_then(Value::as_f64).filter(|&f| f > 0.0).and_then(rate_of).or(rate);
    let takes: Vec<Take> = meta
        .get("takes")?
        .as_array()?
        .iter()
        .filter_map(|t| {
            let path = t.get("path")?.as_str()?;
            let text = |key: &str| t.get(key).and_then(Value::as_str);
            let mut take = Take::of_media(text("label").unwrap_or(path).to_owned(), url_to_path(path), rate);
            match text("kind") {
                Some("movie") => take.kind = Kind::Movie,
                Some("exr") => take.kind = Kind::Exr,
                _ => {}
            }
            take.department = text("department").unwrap_or_default().to_owned();
            take.layer = text("layer").map(str::to_owned);
            take.version = text("version").unwrap_or_default().to_owned();
            Some(take)
        })
        .collect();
    if takes.is_empty() {
        return None;
    }
    let take = meta.get("take").and_then(Value::as_u64).map_or(0, |t| t as usize).min(takes.len() - 1);
    Some((takes, take))
}

impl Playlist {
    pub fn from_otio(timeline: &Timeline) -> Self {
        let ocio = timeline
            .metadata
            .get("tumblepipe")
            .and_then(|m| m.get("ocio"))
            .and_then(Value::as_str)
            .map(url_to_path);
        let mut shots = Vec::new();
        let track = timeline.tracks().find(|t| t.kind == otio::TrackKind::Video);
        for child in track.map(|t| t.children.as_slice()).unwrap_or_default() {
            let Composable::Clip(clip) = child else { continue };
            if !clip.enabled {
                continue;
            }
            let rate = clip.trimmed_range().and_then(|r| rate_of(r.start_time.rate));
            let from_meta = clip.metadata.get("tumblepipe").and_then(|m| metadata_takes(m, rate));
            let (takes, take) = match from_meta {
                Some(found) => found,
                None => match clip.media_reference.as_ref().and_then(|r| reference_take(&clip.name, r, rate)) {
                    Some(take) => (vec![take], 0),
                    None => continue,
                },
            };
            let sequence = clip.metadata.get("tumblepipe").and_then(|m| m.get("entity")).and_then(Value::as_str).and_then(sequence_of);
            shots.push(Shot { name: clip.name.clone(), sequence, takes, take });
        }
        Self { name: timeline.name.clone(), ocio, shots }
    }

    pub fn read(path: &str) -> Result<Self, String> {
        let timeline = otio::read(path, &otio::ReadOptions::default()).map_err(|e| format!("{path}: {e}"))?;
        Ok(Self::from_otio(&timeline))
    }

    /// A playlist of media paths, one shot each, named after the file.
    pub fn of_paths(paths: &[String]) -> Self {
        let shots = paths
            .iter()
            .map(|p| {
                let name = Path::new(p).file_name().map_or_else(|| p.clone(), |n| n.to_string_lossy().into_owned());
                Shot { name: name.clone(), sequence: None, takes: vec![Take::of_media(name, p.clone(), None)], take: 0 }
            })
            .collect();
        Self { name: String::new(), ocio: None, shots }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn fixture() -> Playlist {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/playlist.otio");
        Playlist::read(path).unwrap()
    }

    #[test]
    fn reads_the_tumblepipe_schema() {
        let playlist = fixture();
        assert_eq!(playlist.name, "HideAndReek review");
        assert_eq!(playlist.ocio.as_deref(), Some("P:/HideAndReek/_config/ocio/tumblehead.ocio"));
        let names: Vec<&str> = playlist.shots.iter().map(|s| s.name.as_str()).collect();
        // The gap and the disabled clip are skipped.
        assert_eq!(names, ["010", "020", "plain"]);
        let s010 = &playlist.shots[0];
        assert_eq!(s010.take, 1);
        assert_eq!(s010.takes.len(), 3);
        assert_eq!(s010.takes[1].label, "animation v0015");
        assert_eq!(s010.takes[1].path, "P:/HideAndReek_render/playblast/shots/HideAndReek/010/animation/v0015.mp4");
        assert_eq!(s010.takes[2].path, "P:/HideAndReek_render/render/shots/HideAndReek/010/denoise/default/v0030/beauty/HideAndReek_010_default_beauty_v0030.1001.exr");
        assert_eq!(s010.takes[0].rate, Some((24, 1)));
        assert_eq!(s010.sequence.as_deref(), Some("HideAndReek"));
        // An out-of-range take index is clamped; the metadata's fps is the rate.
        assert_eq!(playlist.shots[1].take, 0);
        assert_eq!(playlist.shots[1].takes[0].rate, Some((25, 1)));
        // A clip without TumblePipe metadata is its media reference.
        let plain = &playlist.shots[2];
        assert_eq!(plain.takes, vec![Take::of_media("plain".into(), "C:/review/plain v2.mov".into(), Some((25, 1)))]);
        assert_eq!(plain.takes[0].kind, Kind::Movie);
        // The takes say what they are.
        assert_eq!((s010.takes[1].kind, s010.takes[1].department.as_str(), s010.takes[1].version.as_str()), (Kind::Movie, "animation", "v0015"));
        assert_eq!(s010.takes[2].kind, Kind::Exr);
    }

    #[test]
    fn image_sequence_references_name_their_first_frame() {
        let reference: MediaReference = serde_json::from_value(serde_json::json!({
            "OTIO_SCHEMA": "ImageSequenceReference.1",
            "target_url_base": "file:///P:/renders/beauty/",
            "name_prefix": "shot.",
            "name_suffix": ".exr",
            "start_frame": 1001,
            "frame_step": 1,
            "rate": 24.0,
            "frame_zero_padding": 4,
            "missing_frame_policy": "error"
        }))
        .unwrap();
        let take = reference_take("s", &reference, None).unwrap();
        assert_eq!(Path::new(&take.path), Path::new("P:/renders/beauty/shot.1001.exr"));
        assert_eq!(take.rate, Some((24, 1)));
    }

    /// A shot of takes of a few kinds, departments, layers and versions, in
    /// any order, showing any of them.
    fn shots() -> impl Strategy<Value = Shot> {
        let take = (any::<bool>(), 0..3usize, prop::option::of(0..2usize), 1..20u32).prop_map(|(exr, dept, layer, code)| {
            let kind = if exr { Kind::Exr } else { Kind::Movie };
            let mut take = Take::of_media(String::new(), String::new(), None);
            take.kind = kind;
            take.department = ["layout", "animation", "light"][dept].to_owned();
            take.layer = if kind == Kind::Exr { layer.map(|l| ["default", "slapcomp"][l].to_owned()) } else { None };
            take.version = format!("v{code:04}");
            take
        });
        prop::collection::vec(take, 1..12).prop_flat_map(|takes| {
            let n = takes.len();
            (Just(takes), 0..n).prop_map(|(takes, take)| Shot { name: "s".into(), sequence: None, takes, take })
        })
    }

    fn showing(shot: &Shot, take: usize) -> Shot {
        Shot { take, ..shot.clone() }
    }

    proptest! {
        /// Stepping versions stays in the shown take's stream, moves the way
        /// it is asked, and stepping newer visits each newer version once
        /// before it stops at the newest.
        #[test]
        fn version_steps_walk_the_stream(shot in shots(), delta in -3i64..4) {
            let stream = shot.takes[shot.take].stream();
            let to = shot.version_step(delta);
            prop_assert_eq!(shot.takes[to].stream(), stream);
            let moved_as_asked = match delta.signum() {
                1 => to >= shot.take,
                -1 => to <= shot.take,
                _ => to == shot.take,
            };
            prop_assert!(moved_as_asked);
            let newer: Vec<usize> = (shot.take + 1..shot.takes.len()).filter(|&t| shot.takes[t].stream() == stream).collect();
            let mut walked = Vec::new();
            let mut at = shot.clone();
            loop {
                let next = at.version_step(1);
                if next == at.take {
                    break;
                }
                walked.push(next);
                at = showing(&at, next);
            }
            prop_assert_eq!(walked, newer);
        }

        /// A department step keeps the kind and lands on the newest version
        /// of another stream, or stays; stepping back returns to the stream
        /// it left.
        #[test]
        fn department_steps_land_on_newest_versions(shot in shots(), delta in -2i64..3) {
            let shown = shot.takes[shot.take].stream();
            let to = shot.department_step(delta);
            prop_assert_eq!(shot.takes[to].kind, shot.takes[shot.take].kind);
            if to == shot.take {
                return Ok(());
            }
            prop_assert!(delta != 0);
            prop_assert_ne!(shot.takes[to].stream(), shown);
            let stream = shot.takes[to].stream();
            prop_assert!(shot.takes[to + 1..].iter().all(|t| t.stream() != stream), "not the newest version");
            let back = showing(&shot, to).department_step(-delta);
            prop_assert_eq!(shot.takes[back].stream(), shown);
        }

        /// A switch of kind shows the last take of that kind, if it has one.
        #[test]
        fn a_kind_switch_shows_the_last_take_of_the_kind(shot in shots(), exr in any::<bool>()) {
            let kind = if exr { Kind::Exr } else { Kind::Movie };
            match shot.last_of_kind(kind) {
                Some(t) => {
                    prop_assert_eq!(shot.takes[t].kind, kind);
                    prop_assert!(shot.takes[t + 1..].iter().all(|x| x.kind != kind));
                }
                None => prop_assert!(shot.takes.iter().all(|x| x.kind != kind)),
            }
        }

        /// A file URL decodes to the path it was made from.
        #[test]
        fn file_urls_round_trip(drive in "[A-Z]", segments in prop::collection::vec("[a-zA-Z0-9 _.%#-]{1,8}", 1..4)) {
            let path = format!("{drive}:/{}", segments.join("/"));
            let encoded: String = path
                .bytes()
                .map(|b| if b.is_ascii_alphanumeric() || b"/:._-".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
                .collect();
            prop_assert_eq!(url_to_path(&format!("file:///{encoded}")), path.clone());
            prop_assert_eq!(url_to_path(&path), path);
        }

        /// A shot entity names its sequence, nested or not; another entity
        /// names none.
        #[test]
        fn a_shot_entity_names_its_sequence(sequence in prop::collection::vec("[A-Za-z0-9]{1,8}", 1..3), shot in "[A-Za-z0-9]{1,8}") {
            let sequence = sequence.join("/");
            prop_assert_eq!(sequence_of(&format!("entity:/shots/{sequence}/{shot}")), Some(sequence.clone()));
            prop_assert_eq!(sequence_of(&format!("entity:/assets/{sequence}/{shot}")), None);
            prop_assert_eq!(sequence_of(&format!("entity:/shots/{shot}")), None);
        }
    }
}
