//! A frozen edit as an OpenTimelineIO timeline, for Resolve
//! (`designs/edit-entity.md` §5, one way).
//!
//! Tracks map to OTIO tracks (Resolve stacks video tracks top wins too),
//! gaps between clips to OTIO gaps. An overlap that is a plain dissolve (an
//! upper clip fading in over the end of the clip below it, with nothing
//! between them) is written as both clips on the lower clip's track with an
//! `SMPTE_Dissolve` between them: the outgoing clip runs on under the
//! transition from its handle. Any other fade is not in the timeline: the
//! clips stay top wins, and a marker says so. Every clip carries what it is
//! in its `tumblepipe` metadata. Every frame of the timeline shows the clip
//! the edit shows, outside fades.

use serde_json::{Value, json};
use th_playlist::Media;

use crate::document::{Clip, Frame, FrozenEdit, FrozenSource, VideoClip, timecode_frames};

/// The OTIO `Timeline.1` of `edit`, named `name`.
pub fn timeline(edit: &FrozenEdit, name: &str) -> Value {
    let fps = edit.fps;
    let mut notes: Vec<(Frame, Frame, String)> = Vec::new();
    let tracks = video_tracks(edit, &mut notes);
    let mut children: Vec<Value> = tracks
        .iter()
        .enumerate()
        .map(|(i, items)| {
            track(&edit.video[i].name, "Video", items.iter().map(|p| (p.clip.at, p.clip.duration, p)), fps, |p| video_item(p, fps))
        })
        .collect();
    for t in &edit.audio {
        children.push(track(&t.name, "Audio", t.clips.iter().map(|c| (c.at, c.duration, c)), fps, |c| {
            vec![clip_json(
                &c.source,
                c.source_in,
                c.duration,
                fps,
                json!({"gain_db": c.gain_db, "fade_in": c.fade_in, "fade_out": c.fade_out}),
            )]
        }));
    }
    let mut markers: Vec<Value> = edit.markers.iter().map(|m| marker(&m.note, m.at, 1, "RED", fps)).collect();
    markers.extend(notes.iter().map(|(at, duration, note)| marker(note, *at, *duration, "YELLOW", fps)));
    let start = timecode_frames(&edit.start_timecode, fps).expect("a checked edit's timecode reads");
    json!({
        "OTIO_SCHEMA": "Timeline.1",
        "name": name,
        "global_start_time": time(start, fps),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "source_range": null,
            "effects": [],
            "markers": markers,
            "enabled": true,
            "metadata": {},
            "children": children,
        },
        "metadata": {"tumblepipe": {"resolution": edit.resolution}},
    })
}

/// A video clip as placed in the timeline: maybe moved down to the clip it
/// dissolves from, with the dissolve's length.
#[derive(Debug, Clone)]
struct Placed<'a> {
    clip: VideoClip<&'a FrozenSource>,
    /// Frames of dissolve from the clip before it on its track.
    dissolve: Frame,
}

/// The video tracks as written: each a list of clips in order, the plain
/// dissolves moved onto the track below and the outgoing clip cut where
/// the dissolve starts. Every fade left over adds a note.
fn video_tracks<'a>(edit: &'a FrozenEdit, notes: &mut Vec<(Frame, Frame, String)>) -> Vec<Vec<Placed<'a>>> {
    let mut tracks: Vec<Vec<Placed>> = edit
        .video
        .iter()
        .map(|t| {
            t.clips
                .iter()
                .map(|c| Placed {
                    clip: VideoClip {
                        at: c.at,
                        duration: c.duration,
                        source_in: c.source_in,
                        source: &c.source,
                        fade_in: c.fade_in,
                        fade_out: c.fade_out,
                    },
                    dissolve: 0,
                })
                .collect()
        })
        .collect();
    // Upper clips by start, so a clip moved down is where a later one looks.
    let mut uppers: Vec<(Frame, usize, usize)> = Vec::new();
    for (t, track) in tracks.iter().enumerate() {
        for (i, p) in track.iter().enumerate() {
            if p.clip.fade_in > 0 {
                uppers.push((p.clip.at, t, i));
            }
        }
    }
    uppers.sort();
    let mut moved: Vec<(usize, Frame)> = Vec::new();
    for (at, tu, _) in uppers {
        let Some(iu) = tracks[tu].iter().position(|p| p.clip.at == at) else { continue };
        let upper = tracks[tu][iu].clip.clone();
        let (start, end, n) = (upper.at, upper.end(), upper.fade_in);
        // The track right below with anything over the upper clip's span
        // must hold the outgoing clip, ending where the fade ends.
        let Some(tl) = (0..tu).rev().find(|&t| tracks[t].iter().any(|p| p.clip.at < end && start < p.clip.end())) else {
            continue;
        };
        let lower = &tracks[tl];
        let Some(il) = lower.iter().position(|p| p.clip.at < start && p.clip.end() == start + n) else { continue };
        if lower[il].clip.fade_out > 0 || lower.iter().any(|p| p.clip.at >= start + n && p.clip.at < end) {
            continue;
        }
        let mut incoming = tracks[tu].remove(iu);
        incoming.clip.fade_in = 0;
        incoming.dissolve = n;
        let outgoing = &mut tracks[tl][il];
        outgoing.clip.duration = start - outgoing.clip.at;
        tracks[tl].insert(il + 1, incoming);
        moved.push((tl, start));
    }
    for track in &tracks {
        for p in track {
            let c = &p.clip;
            if c.fade_in > 0 {
                notes.push((c.at, c.fade_in, format!("fade in of {} frames over the tracks below: not in this timeline", c.fade_in)));
            }
            if c.fade_out > 0 {
                notes.push((
                    c.end() - c.fade_out,
                    c.fade_out,
                    format!("fade out of {} frames over the tracks below: not in this timeline", c.fade_out),
                ));
            }
        }
    }
    notes.sort_by_key(|n| n.0);
    tracks
}

fn video_item(p: &Placed, fps: u32) -> Vec<Value> {
    let c = &p.clip;
    let clip = clip_json(c.source, c.source_in, c.duration, fps, json!({"fade_in": c.fade_in, "fade_out": c.fade_out}));
    if p.dissolve == 0 {
        return vec![clip];
    }
    let transition = json!({
        "OTIO_SCHEMA": "Transition.1",
        "name": "",
        "transition_type": "SMPTE_Dissolve",
        "in_offset": time(0, fps),
        "out_offset": time(p.dissolve, fps),
        "metadata": {},
    });
    vec![transition, clip]
}

/// An OTIO track of `items` (`(at, duration, item)`, in order), with gaps
/// between them; `write` gives an item's children (a clip, after a
/// transition into it).
fn track<'a, T: 'a>(
    name: &str,
    kind: &str,
    items: impl Iterator<Item = (Frame, Frame, T)>,
    fps: u32,
    write: impl Fn(T) -> Vec<Value>,
) -> Value {
    let mut children = Vec::new();
    let mut cursor = 0;
    for (at, duration, item) in items {
        if at > cursor {
            children.push(gap(at - cursor, fps));
        }
        children.extend(write(item));
        cursor = at + duration;
    }
    json!({
        "OTIO_SCHEMA": "Track.1",
        "name": name,
        "kind": kind,
        "source_range": null,
        "effects": [],
        "markers": [],
        "enabled": true,
        "metadata": {},
        "children": children,
    })
}

fn clip_json(source: &FrozenSource, source_in: Frame, duration: Frame, fps: u32, extra: Value) -> Value {
    let (name, reference, mut metadata) = match source {
        FrozenSource::Shot { shot, take: None } => {
            let name = format!("{} (no take)", shot.rsplit('/').next().unwrap_or(shot));
            let reference = json!({
                "OTIO_SCHEMA": "MissingReference.1",
                "name": name,
                "available_range": null,
                "metadata": {},
            });
            (name, reference, json!({"shot": shot, "take": null}))
        }
        FrozenSource::Shot { shot, take: Some(take) } => {
            let name = format!("{} {} {}", shot.rsplit('/').next().unwrap_or(shot), take.department, take.version.as_str());
            let metadata = json!({
                "shot": shot, "department": take.department, "kind": take.kind,
                "layer": take.layer, "version": take.version, "media": take.media,
            });
            (name, reference(&take.media, fps), metadata)
        }
        FrozenSource::Media { item, version, media } => {
            let metadata = json!({"item": item, "version": version, "media": media});
            (format!("{item} {}", version.as_str()), reference(media, fps), metadata)
        }
        FrozenSource::Slug { text } => {
            let reference = json!({
                "OTIO_SCHEMA": "GeneratorReference.1",
                "name": text,
                "generator_kind": "SolidColor",
                "parameters": {"color": "black", "text": text},
                "available_range": null,
                "metadata": {},
            });
            (text.clone(), reference, json!({"slug": text}))
        }
    };
    if let (Value::Object(m), Value::Object(e)) = (&mut metadata, extra) {
        m.extend(e);
    }
    json!({
        "OTIO_SCHEMA": "Clip.2",
        "name": name,
        "source_range": range(source_in, duration, fps),
        "media_references": {"DEFAULT_MEDIA": reference},
        "active_media_reference_key": "DEFAULT_MEDIA",
        "effects": [],
        "markers": [],
        "enabled": true,
        "metadata": {"tumblepipe": metadata},
    })
}

/// The OTIO media reference of `media`: a movie's file, an EXR sequence,
/// or a stack's bottom layer (the rest are in the clip's metadata).
fn reference(media: &Media, fps: u32) -> Value {
    match media {
        Media::Movie { path } => json!({
            "OTIO_SCHEMA": "ExternalReference.1",
            "name": "",
            "target_url": file_url(path),
            "available_range": null,
            "metadata": {},
        }),
        Media::Exr { frames, .. } => json!({
            "OTIO_SCHEMA": "ImageSequenceReference.1",
            "name": "",
            "target_url_base": format!("{}/", file_url(&frames.directory)),
            "name_prefix": frames.prefix,
            "name_suffix": frames.suffix,
            "start_frame": frames.first,
            "frame_step": frames.step,
            "rate": f64::from(fps),
            "frame_zero_padding": frames.padding,
            "missing_frame_policy": "error",
            "available_range": range(frames.first, (frames.last - frames.first) / frames.step + 1, fps),
            "metadata": {},
        }),
        Media::Stack { layers } => match layers.first() {
            Some(layer) => reference(&layer.media, fps),
            None => json!({"OTIO_SCHEMA": "MissingReference.1", "name": "", "available_range": null, "metadata": {}}),
        },
    }
}

/// `path` as a `file:` URL: forward slashes, a drive letter after
/// `file:///`, spaces and `%` escaped.
pub fn file_url(path: &str) -> String {
    let slashed = path.replace('\\', "/");
    let escaped = slashed.replace('%', "%25").replace(' ', "%20").replace('#', "%23");
    if escaped.starts_with('/') { format!("file://{escaped}") } else { format!("file:///{escaped}") }
}

/// A name of its own for every movie `edit` plays, by its path:
/// `<sequence>_<shot>_<department>_<kind>[_<layer>]_<version>.<ext>` for a
/// shot's take, `<item>_<version>.<ext>` for a media item, `_2`, `_3`… on
/// a clash. Resolve's OTIO import takes movies with the same file name and
/// start timecode for one movie, and every playblast is `vNNNN.mp4`; the
/// publish links each movie under its name (see `relink`). EXR sequences
/// are not renamed.
pub fn movie_links(edit: &FrozenEdit) -> std::collections::BTreeMap<String, String> {
    let sources = edit
        .video
        .iter()
        .flat_map(|t| t.clips.iter().map(|c| &c.source))
        .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)));
    let mut out = std::collections::BTreeMap::new();
    let mut taken = std::collections::BTreeSet::new();
    for source in sources {
        let (stem, media) = match source {
            FrozenSource::Shot { shot, take: Some(take) } => {
                let entity = shot.trim_start_matches("entity:/shots/").replace('/', "_");
                let kind = match take.kind {
                    crate::document::TakeKind::Playblast => "playblast",
                    crate::document::TakeKind::Render => "render",
                };
                let layer = take.layer.as_ref().map(|l| format!("_{l}")).unwrap_or_default();
                (format!("{entity}_{}_{kind}{layer}_{}", take.department, take.version.as_str()), &take.media)
            }
            FrozenSource::Media { item, version, media } => {
                (format!("{}_{}", item.rsplit(['/', ':']).next().unwrap_or(item), version.as_str()), media)
            }
            _ => continue,
        };
        let Some(path) = movie_path(media) else { continue };
        if out.contains_key(path) {
            continue;
        }
        let ext = std::path::Path::new(path).extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
        let stem: String = stem.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' }).collect();
        let mut name = format!("{stem}{ext}");
        let mut n = 1;
        while !taken.insert(name.to_lowercase()) {
            n += 1;
            name = format!("{stem}_{n}{ext}");
        }
        out.insert(path.to_string(), name);
    }
    out
}

/// The movie file an OTIO clip of `media` refers to (`reference`).
fn movie_path(media: &Media) -> Option<&str> {
    match media {
        Media::Movie { path } => Some(path),
        Media::Stack { layers } => layers.first().and_then(|l| movie_path(&l.media)),
        Media::Exr { .. } => None,
    }
}

/// Point `otio`'s movie references at other files: `links` maps a movie's
/// path, as the edit has it, to the path to refer to instead.
pub fn relink(otio: &mut Value, links: &std::collections::BTreeMap<String, String>) {
    let urls: std::collections::BTreeMap<String, String> = links.iter().map(|(from, to)| (file_url(from), file_url(to))).collect();
    fn walk(v: &mut Value, urls: &std::collections::BTreeMap<String, String>) {
        match v {
            Value::Object(m) => {
                if m.get("OTIO_SCHEMA").and_then(Value::as_str) == Some("ExternalReference.1")
                    && let Some(to) = m.get("target_url").and_then(Value::as_str).and_then(|u| urls.get(u))
                {
                    m.insert("target_url".into(), Value::String(to.clone()));
                }
                m.values_mut().for_each(|c| walk(c, urls));
            }
            Value::Array(a) => a.iter_mut().for_each(|c| walk(c, urls)),
            _ => {}
        }
    }
    walk(otio, &urls);
}

fn gap(duration: Frame, fps: u32) -> Value {
    json!({
        "OTIO_SCHEMA": "Gap.1",
        "name": "",
        "source_range": range(0, duration, fps),
        "effects": [],
        "markers": [],
        "enabled": true,
        "metadata": {},
    })
}

fn marker(note: &str, at: Frame, duration: Frame, color: &str, fps: u32) -> Value {
    json!({
        "OTIO_SCHEMA": "Marker.2",
        "name": note,
        "marked_range": range(at, duration, fps),
        "color": color,
        "comment": "",
        "metadata": {},
    })
}

fn time(value: Frame, fps: u32) -> Value {
    json!({"OTIO_SCHEMA": "RationalTime.1", "rate": f64::from(fps), "value": value as f64})
}

fn range(start: Frame, duration: Frame, fps: u32) -> Value {
    json!({"OTIO_SCHEMA": "TimeRange.1", "start_time": time(start, fps), "duration": time(duration, fps)})
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::{Source, Track, VersionRule};
    use crate::freeze::{freeze, tests::Fixed};
    use crate::ops::tests::{edits, vclip};
    use crate::picture::{Playable, layers_at};
    use proptest::prelude::*;

    struct All;

    impl<S> Playable<S> for All {
        fn playable(&self, _: &S, _: Frame) -> bool {
            true
        }
    }

    /// What the timeline shows at frame `at`, top wins, transitions
    /// ignored: the clip's `tumblepipe` metadata and source frame.
    fn shows(timeline: &Value, at: Frame) -> Option<(Value, Frame)> {
        let tracks = timeline["tracks"]["children"].as_array().unwrap();
        for track in tracks.iter().rev().filter(|t| t["kind"] == "Video") {
            let mut cursor = 0;
            for item in track["children"].as_array().unwrap() {
                if item["OTIO_SCHEMA"] == "Transition.1" {
                    continue;
                }
                let r = &item["source_range"];
                let (start, duration) =
                    (r["start_time"]["value"].as_f64().unwrap() as Frame, r["duration"]["value"].as_f64().unwrap() as Frame);
                if cursor <= at && at < cursor + duration {
                    if item["OTIO_SCHEMA"] == "Clip.2" {
                        let mut meta = item["metadata"]["tumblepipe"].clone();
                        meta.as_object_mut().unwrap().retain(|k, _| k != "fade_in" && k != "fade_out");
                        return Some((meta, start + (at - cursor)));
                    }
                    break;
                }
                cursor += duration;
            }
        }
        None
    }

    fn mediate(n: usize) -> Source {
        Source::Media { item: format!("m{n}"), version: VersionRule::Latest }
    }

    #[test]
    fn a_plain_dissolve_becomes_a_transition() {
        let mut edit = crate::WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(Track { name: "V1".into(), clips: vec![vclip(0, 20, 0, mediate(1))] });
        let mut upper = vclip(16, 10, 100, mediate(2));
        upper.fade_in = 4;
        edit.video.push(Track { name: "V2".into(), clips: vec![upper] });
        let otio = timeline(&freeze(&edit, &Fixed).unwrap(), "cut");
        let v1 = otio["tracks"]["children"][0]["children"].as_array().unwrap();
        let kinds: Vec<&str> = v1.iter().map(|i| i["OTIO_SCHEMA"].as_str().unwrap()).collect();
        assert_eq!(kinds, ["Clip.2", "Transition.1", "Clip.2"]);
        assert_eq!(v1[0]["source_range"]["duration"]["value"], 16.0);
        assert_eq!(v1[1]["out_offset"]["value"], 4.0);
        assert!(otio["tracks"]["children"][1]["children"].as_array().unwrap().is_empty());
        assert!(otio["tracks"]["markers"].as_array().unwrap().is_empty(), "nothing lost, nothing noted");
        assert_eq!(otio["global_start_time"]["value"], 86400.0);
    }

    #[test]
    fn file_urls() {
        assert_eq!(file_url("P:/Show/a b.mov"), "file:///P:/Show/a%20b.mov");
        assert_eq!(file_url("/mnt/show/x.mov"), "file:///mnt/show/x.mov");
        assert_eq!(file_url(r"C:\a\b.mov"), "file:///C:/a/b.mov");
    }

    /// Movies that share a file name get names of their own (Resolve takes
    /// same-named movies for one), a movie played twice one name, and the
    /// OTIO refers to every movie by its link.
    #[test]
    fn every_movie_is_linked_under_a_name_of_its_own() {
        use crate::document::{FrozenTake, TakeKind, Version};
        let version = |v: &str| Version::try_from(v.to_string()).unwrap();
        let shot = |shot: &str, dept: &str, v: &str, path: &str| FrozenSource::Shot {
            shot: shot.into(),
            take: Some(FrozenTake {
                department: dept.into(),
                kind: TakeKind::Playblast,
                layer: None,
                version: version(v),
                media: Media::Movie { path: path.into() },
            }),
        };
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let clip = |at, source| VideoClip { at, duration: 10, source_in: 1001, source, fade_in: 0, fade_out: 0 };
        edit.video.push(Track {
            name: "V1".into(),
            clips: vec![
                clip(0, shot("entity:/shots/S/010", "layout", "v0001", "P:/r/S/010/layout/v0001.mp4")),
                clip(10, shot("entity:/shots/S/020", "layout", "v0001", "P:/r/S/020/layout/v0001.mp4")),
                clip(20, shot("entity:/shots/S/010", "layout", "v0001", "P:/r/S/010/layout/v0001.mp4")),
                clip(
                    30,
                    FrozenSource::Media {
                        item: "media:/ref/Look".into(),
                        version: version("v0002"),
                        media: Media::Movie { path: "P:/m/v0002.mov".into() },
                    },
                ),
                clip(40, FrozenSource::Slug { text: "end".into() }),
            ],
        });
        let links = movie_links(&edit);
        let names: Vec<&str> = links.values().map(String::as_str).collect();
        assert_eq!(links.len(), 3, "{links:?}");
        assert_eq!(links["P:/r/S/010/layout/v0001.mp4"], "S_010_layout_playblast_v0001.mp4");
        assert_eq!(links["P:/r/S/020/layout/v0001.mp4"], "S_020_layout_playblast_v0001.mp4");
        assert_eq!(links["P:/m/v0002.mov"], "Look_v0002.mov");
        assert!(names.iter().all(|n| !n.contains(['/', ':', ' '])));

        let mut otio = timeline(&edit, "cut");
        let to: std::collections::BTreeMap<String, String> = links.iter().map(|(p, n)| (p.clone(), format!("P:/x/media/{n}"))).collect();
        relink(&mut otio, &to);
        let text = otio.to_string();
        assert!(!text.contains("file:///P:/r/"), "every movie relinked");
        assert_eq!(text.matches("file:///P:/x/media/S_010_layout_playblast_v0001.mp4").count(), 2);
        assert_eq!(text.matches("file:///P:/x/media/S_020_layout_playblast_v0001.mp4").count(), 1);
    }

    /// Two movies whose names would read the same get told apart.
    #[test]
    fn names_that_clash_are_numbered() {
        use crate::document::Version;
        let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
        let item = |at, path: &str| VideoClip {
            at,
            duration: 10,
            source_in: 0,
            source: FrozenSource::Media {
                item: "a b".into(),
                version: Version::try_from("v0001".to_string()).unwrap(),
                media: Media::Movie { path: path.into() },
            },
            fade_in: 0,
            fade_out: 0,
        };
        edit.video.push(Track { name: "V1".into(), clips: vec![item(0, "P:/1/x.mp4"), item(10, "P:/2/x.mp4")] });
        let links = movie_links(&edit);
        assert_eq!(links["P:/1/x.mp4"], "a_b_v0001.mp4");
        assert_eq!(links["P:/2/x.mp4"], "a_b_v0001_2.mp4");
    }

    /// A movie from small pools, so paths repeat and file names clash: the
    /// same `vNNNN.mp4` in many folders, case variants, extensions in
    /// either case, odd characters in a folder.
    fn movie_path() -> impl Strategy<Value = String> {
        let folder = prop::sample::select(vec!["P:/r/S/010/anim", "P:/r/S/020/anim", "P:/r/s/010/ANIM", "P:/a b/#1", "/mnt/r/S/030/lay"]);
        let file = prop::sample::select(vec!["v0001.mp4", "v0001.MP4", "v0002.mov", "V0001.mp4", "movie", "a.b.c.mov"]);
        (folder, file).prop_map(|(d, f)| format!("{d}/{f}"))
    }

    /// Any media a clip plays: movies, EXRs, stacks of either.
    fn any_media() -> impl Strategy<Value = Media> {
        let exr = (0i64..3).prop_map(|n| Media::Exr {
            layer: "default".into(),
            frames: th_playlist::Frames {
                directory: format!("P:/r/beauty{n}"),
                prefix: "beauty.".into(),
                suffix: ".exr".into(),
                padding: 4,
                first: 1001,
                last: 1010,
                step: 1,
            },
        });
        let movie = movie_path().prop_map(|path| Media::Movie { path });
        let leaf = prop_oneof![3 => movie, 1 => exr];
        prop_oneof![
            4 => leaf.clone(),
            1 => prop::collection::vec(leaf, 1..3).prop_map(|layers| Media::Stack {
                layers: layers.into_iter().enumerate().map(|(i, media)| th_playlist::StackLayer { name: format!("l{i}"), media }).collect(),
            }),
        ]
    }

    /// Any frozen source, from small pools of shots, departments, items and
    /// versions, so the names links get clash too.
    fn any_source() -> impl Strategy<Value = FrozenSource> {
        use crate::document::{FrozenTake, TakeKind, Version};
        let version = prop::sample::select(vec!["v0001", "v0002", "v10000"]).prop_map(|v| Version::try_from(v.to_string()).unwrap());
        let shot = (
            prop::sample::select(vec!["entity:/shots/S/010", "entity:/shots/S/020", "entity:/shots/s/010"]),
            prop::sample::select(vec!["anim", "layout", "Anim"]),
            prop::bool::ANY,
            prop::option::of(prop::sample::select(vec!["layers", "fg", "bg"])),
            version.clone(),
            any_media(),
        )
            .prop_map(|(shot, department, render, layer, version, media)| FrozenSource::Shot {
                shot: shot.into(),
                take: Some(FrozenTake {
                    department: department.into(),
                    kind: if render { TakeKind::Render } else { TakeKind::Playblast },
                    layer: layer.map(String::from),
                    version,
                    media,
                }),
            });
        let item = (prop::sample::select(vec!["media:/ref/Look", "Look", "a b", "Blå:x/y", ""]), version, any_media())
            .prop_map(|(item, version, media)| FrozenSource::Media { item: item.into(), version, media });
        prop_oneof![
            4 => shot,
            2 => item,
            1 => Just(FrozenSource::Shot { shot: "entity:/shots/S/090".into(), take: None }),
            1 => Just(FrozenSource::Slug { text: "slug".into() }),
        ]
    }

    /// A frozen edit of those sources on two video tracks and an audio one.
    fn movie_edit() -> impl Strategy<Value = FrozenEdit> {
        prop::collection::vec((0usize..3, any_source()), 0..14).prop_map(|clips| {
            let mut edit = FrozenEdit::new(24, [1920, 1080], "01:00:00:00");
            edit.video.push(Track { name: "V1".into(), clips: Vec::new() });
            edit.video.push(Track { name: "V2".into(), clips: Vec::new() });
            edit.audio.push(Track { name: "A1".into(), clips: Vec::new() });
            for (i, (track, source)) in clips.into_iter().enumerate() {
                let at = i as Frame * 10;
                if track == 2 {
                    edit.audio[0].clips.push(crate::document::AudioClip {
                        at,
                        duration: 10,
                        source_in: 1001,
                        source,
                        gain_db: 0.0,
                        fade_in: 0,
                        fade_out: 0,
                    });
                } else {
                    edit.video[track].clips.push(VideoClip { at, duration: 10, source_in: 1001, source, fade_in: 0, fade_out: 0 });
                }
            }
            edit
        })
    }

    /// Every movie file `edit`'s clips refer to (a stack by its bottom
    /// layer, as the OTIO does).
    fn movies_of(edit: &FrozenEdit) -> std::collections::BTreeSet<String> {
        let sources = edit
            .video
            .iter()
            .flat_map(|t| t.clips.iter().map(|c| &c.source))
            .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)));
        sources
            .filter_map(|s| match s {
                FrozenSource::Shot { take: Some(t), .. } => movie_path_of(&t.media),
                FrozenSource::Media { media, .. } => movie_path_of(media),
                _ => None,
            })
            .collect()
    }

    fn movie_path_of(media: &Media) -> Option<String> {
        super::movie_path(media).map(String::from)
    }

    proptest! {
        /// Every movie the edit plays gets one link, and nothing else does;
        /// no two links' names are the same in any case (Windows and macOS
        /// do not tell case apart), each is plain characters with its
        /// movie's extension, and the same edit always gets the same names.
        #[test]
        fn every_movie_gets_a_name_no_other_has(edit in movie_edit()) {
            let links = movie_links(&edit);
            let movies = movies_of(&edit);
            prop_assert_eq!(links.keys().cloned().collect::<std::collections::BTreeSet<_>>(), movies);
            let mut seen = std::collections::BTreeSet::new();
            for (path, name) in &links {
                prop_assert!(seen.insert(name.to_lowercase()), "{} named twice: {:?}", name, links);
                let (stem, ext) = match std::path::Path::new(path).extension() {
                    Some(e) => (name.strip_suffix(&format!(".{}", e.to_string_lossy())), true),
                    None => (Some(name.as_str()), false),
                };
                prop_assert!(stem.is_some(), "{} lost {}'s extension", name, path);
                let stem = stem.unwrap();
                prop_assert!(!stem.is_empty() && stem.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'), "{:?} for {} ({})", stem, path, ext);
            }
            prop_assert_eq!(movie_links(&edit), links);
        }

        /// Relinked, the OTIO refers to every movie by its link and to
        /// nothing else differently: take the links back out and it is the
        /// OTIO as written.
        #[test]
        fn relinking_changes_only_movie_references(edit in movie_edit()) {
            let written = timeline(&edit, "cut");
            let links = movie_links(&edit);
            let to: std::collections::BTreeMap<String, String> = links.iter().map(|(p, n)| (p.clone(), format!("Q:/pub/media/{n}"))).collect();
            let mut relinked = written.clone();
            relink(&mut relinked, &to);
            let text = relinked.to_string();
            for path in links.keys() {
                prop_assert!(!text.contains(&format!("\"{}\"", file_url(path))), "{} still referred to", path);
            }
            let back: std::collections::BTreeMap<String, String> = to.iter().map(|(p, l)| (l.clone(), p.clone())).collect();
            relink(&mut relinked, &back);
            prop_assert_eq!(relinked, written);
        }
    }

    proptest! {
        /// Outside fades, every frame of the timeline shows the clip (and
        /// source frame) the edit shows; every fade not written as a
        /// dissolve is noted.
        #[test]
        fn the_timeline_shows_what_the_edit_shows(edit in edits(), fades in prop::collection::vec((0i64..8, 0i64..4), 0..12), at in 0i64..200) {
            let mut edit = edit;
            let mut i = 0;
            for t in &mut edit.video {
                for c in &mut t.clips {
                    if let Some(&(a, b)) = fades.get(i)
                        && a + b <= c.duration {
                            c.fade_in = a;
                            c.fade_out = b;
                        }
                    i += 1;
                }
            }
            let frozen = freeze(&edit, &Fixed).unwrap();
            let otio = timeline(&frozen, "cut");
            let layers = layers_at(&frozen, at, &All);
            let in_fade = layers.iter().any(|l| l.opacity < 1.0);
            if !in_fade {
                let want = layers.last().map(|l| {
                    let c = &frozen.video[l.track].clips[l.clip];
                    let mut meta = clip_json(&c.source, c.source_in, c.duration, frozen.fps, json!({}))["metadata"]["tumblepipe"].clone();
                    meta.as_object_mut().unwrap().retain(|k, _| k != "fade_in" && k != "fade_out");
                    (meta, l.frame)
                });
                prop_assert_eq!(shows(&otio, at), want);
            }
            let fades = frozen.video.iter().flat_map(|t| &t.clips).map(|c| (c.fade_in > 0) as usize + (c.fade_out > 0) as usize).sum::<usize>();
            let transitions = otio["tracks"]["children"].as_array().unwrap().iter()
                .flat_map(|t| t["children"].as_array().unwrap()).filter(|i| i["OTIO_SCHEMA"] == "Transition.1").count();
            prop_assert_eq!(otio["tracks"]["markers"].as_array().unwrap().len(), frozen.markers.len() + fades - transitions);
        }
    }
}
