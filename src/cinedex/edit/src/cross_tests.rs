//! Properties across the edit's features: a clip's transform and opacity
//! under every op and undo, the document with and without a `transform`,
//! the limits of a transform to the number, and the OTIO of an edit whose
//! clips are moved, faded and see-through at once.
//!
//! The edits here are *dressed*: every clip has a source of its own, and
//! every video clip a transform and an opacity of its own. No op makes up
//! a source, so the source a clip shows says which clip it came from, and
//! with it the transform and opacity it must still carry.

use proptest::prelude::*;
use serde_json::Value;

use crate::document::tests::transforms;
use crate::document::{ClipRef, Edit, Frame, Source, TrackKind, TrackRef, Transform, Version, VersionRule, VideoClip};
use crate::freeze::{freeze, tests::Fixed};
use crate::history::History;
use crate::ops::tests::{Lengths, Try, any_op, choose, edits, pick};
use crate::ops::{Edge, NewClip, Op, apply};
use crate::timeline_export::{Kind, Movie, Probe, otio_summary};

/// A source no other clip has: media with frames 0 to 1000, by its number.
fn source_of(n: usize) -> Source {
    Source::Media { item: "m100".into(), version: VersionRule::Pinned(Version::try_from(format!("v{:04}", 1000 + n)).unwrap()) }
}

/// What dresses a video clip: a transform, an opacity, and its fades as
/// parts of its length.
type Dress = (Transform, f64, (u8, u8));

fn dresses() -> impl Strategy<Value = Vec<Dress>> {
    let transform = prop_oneof![1 => Just(Transform::IDENTITY), 4 => transforms()];
    let opacity = prop::sample::select(vec![1.0f64, 1.0, 0.75, 0.5, 0.125]);
    prop::collection::vec((transform, opacity, (0u8..4, 0u8..4)), 24)
}

/// `edit` with every clip showing a source of its own, and every video
/// clip dressed: clip `n` in `dress[n]`, its transform's `x` made its own
/// when it has one (so no two clips sit alike by chance). `fades` gives
/// the clips fades too (never more than a clip is long).
fn dress(mut edit: Edit<Source>, dress: &[Dress], fades: bool) -> Edit<Source> {
    let mut n = 0;
    for track in &mut edit.video {
        for c in &mut track.clips {
            let (transform, opacity, (fade_in, fade_out)) = dress[n % dress.len()];
            c.source = source_of(n);
            c.transform = if transform.is_identity() { transform } else { Transform { x: (n as f64 + 1.0) / 64.0, ..transform } };
            c.opacity = opacity;
            if fades {
                c.fade_in = c.duration * Frame::from(fade_in) / 8;
                c.fade_out = c.duration * Frame::from(fade_out) / 8;
            }
            n += 1;
        }
    }
    for track in &mut edit.audio {
        for c in &mut track.clips {
            c.source = source_of(n);
            n += 1;
        }
    }
    assert_eq!(edit.problem(), None);
    edit
}

/// `edit` with every video clip as fitted.
fn undressed(edit: &Edit<Source>) -> Edit<Source> {
    let mut plain = edit.clone();
    for c in plain.video.iter_mut().flat_map(|t| &mut t.clips) {
        c.transform = Transform::IDENTITY;
    }
    plain
}

fn video_clip(edit: &Edit<Source>, clip: ClipRef) -> Option<&VideoClip<Source>> {
    (clip.track.kind == TrackKind::Video).then(|| edit.video.get(clip.track.index)?.clips.get(clip.index)).flatten()
}

/// How many video clips of `edit` show `source`.
fn showing(edit: &Edit<Source>, source: &Source) -> usize {
    edit.video.iter().flat_map(|t| &t.clips).filter(|c| &c.source == source).count()
}

/// The transform and opacity each source's clips carry.
type Looks = Vec<(Source, Transform, f64)>;

fn look<'a>(looks: &'a mut Looks, source: &Source) -> Option<&'a mut (Source, Transform, f64)> {
    looks.iter_mut().find(|l| &l.0 == source)
}

/// `document` (an edit's JSON) with every `transform` taken out of the
/// text: the document an editor from before transforms wrote.
fn without_transforms(document: &str) -> String {
    let mut out = document.to_owned();
    while let Some(key) = out.find("\"transform\": {") {
        let comma = out[..key].rfind(',').expect("a transform comes after another field");
        let close = key + out[key..].find('}').expect("a transform closes");
        out.replace_range(comma..=close, "");
    }
    out
}

/// A disk with everything on it.
struct Whole;

impl Probe for Whole {
    fn movie(&self, _: &str) -> Option<Movie> {
        None
    }
    fn exists(&self, _: &str) -> bool {
        true
    }
    fn shot_first(&self, _: &str) -> Result<Frame, String> {
        Ok(crate::document::DEFAULT_START_FRAME)
    }
}

proptest! {
    /// Whatever is done to an edit, every clip keeps the transform and the
    /// opacity of the clip it came from: both halves of a split, a clip
    /// trimmed, slipped, rolled, moved, cut in two by an insert or a ripple
    /// delete on another track, given another take, faded. Only Set
    /// Transform and Set Opacity change them, and only on their clip. Each
    /// result is an edit (no problem), is what the same op makes of the
    /// edit with every clip as fitted (a transform changes nothing else),
    /// and reads back from its JSON to the byte. Undo then gives back
    /// every earlier document byte for byte, and redo every later one.
    #[test]
    fn every_op_keeps_each_clips_transform_and_undo_gives_the_bytes_back(
        edit in edits(),
        dresses in dresses(),
        tries in prop::collection::vec((any_op(), 0u8..6, transforms()), 1..24),
    ) {
        let mut edit = dress(edit, &dresses, false);
        let mut looks: Looks = edit.video.iter().flat_map(|t| &t.clips).map(|c| (c.source.clone(), c.transform, c.opacity)).collect();
        let mut fresh = 500;
        let mut history = History::new();
        let mut seen = vec![edit.to_json()];
        for (attempt, turn, transform) in &tries {
            let Some(mut op) = choose(&edit, attempt) else { continue };
            // A clip added is dressed too, with a source of its own; and
            // some ops are made another take or a transform of their clip.
            if let Op::Place { clip: NewClip::Video(c), .. } | Op::Insert { clip: NewClip::Video(c), .. } = &mut op {
                fresh += 1;
                c.source = source_of(fresh);
                c.transform = *transform;
                c.opacity = 0.25;
                looks.push((c.source.clone(), c.transform, c.opacity));
            }
            let turned = match (attempt, &op) {
                (Try::OnClip { .. }, Op::Slip { clip, .. } | Op::SetFades { clip, .. } | Op::Lift { clip }) => Some(*clip),
                _ => None,
            };
            match (turned, turn) {
                (Some(clip), 0) => {
                    fresh += 1;
                    op = Op::SetSource { clip, source: source_of(fresh) };
                }
                (Some(clip), 1) => op = Op::SetTransform { clip, transform: *transform },
                _ => {}
            }
            // What the op says of looks, before it is applied.
            let mut expected = looks.clone();
            match &op {
                Op::SetTransform { clip, transform } => {
                    let Some(c) = video_clip(&edit, *clip) else { continue };
                    // Its other half (after a split) would no longer sit
                    // as it does: the source would not say which is which.
                    if showing(&edit, &c.source) != 1 {
                        continue;
                    }
                    look(&mut expected, &c.source).expect("a dressed clip").1 = *transform;
                }
                Op::SetOpacity { clip, opacity } => {
                    let Some(c) = video_clip(&edit, *clip) else { continue };
                    if showing(&edit, &c.source) != 1 {
                        continue;
                    }
                    look(&mut expected, &c.source).expect("a dressed clip").2 = *opacity;
                }
                Op::SetSource { clip, source } => {
                    if let Some(c) = video_clip(&edit, *clip) {
                        let (_, transform, opacity) = *look(&mut expected, &c.source).expect("a dressed clip");
                        expected.push((source.clone(), transform, opacity));
                    }
                }
                _ => {}
            }
            let plain = apply(&undressed(&edit), &op_undressed(&op), &Lengths);
            let before = edit.to_json();
            match history.apply(&mut edit, &op, &Lengths) {
                Err(_) => {
                    prop_assert_eq!(edit.to_json(), before, "a refused {:?} changed the edit", op);
                    // Only a join looks at how clips sit, and only Set
                    // Transform at a transform.
                    if !matches!(op, Op::Join { .. } | Op::SetTransform { .. }) {
                        prop_assert!(plain.is_err(), "{:?} is refused only for the transforms", op);
                    }
                }
                Ok(()) => {
                    looks = expected;
                    prop_assert_eq!(edit.problem(), None);
                    for c in edit.video.iter().flat_map(|t| &t.clips) {
                        let (_, transform, opacity) = looks.iter().find(|l| l.0 == c.source).expect("no op makes up a source");
                        prop_assert_eq!(&c.transform, transform, "after {:?}, the clip of {:?}", op, c.source);
                        prop_assert_eq!(c.opacity, *opacity, "after {:?}, the clip of {:?}", op, c.source);
                    }
                    prop_assert_eq!(&undressed(&edit), &plain.expect("the same op on the edit as fitted"), "{:?}", op);
                    let json = edit.to_json();
                    let back = Edit::<Source>::parse(&json).unwrap();
                    prop_assert_eq!(&back, &edit);
                    prop_assert_eq!(back.to_json(), json.clone());
                    seen.push(json);
                }
            }
        }
        for want in seen.iter().rev().skip(1) {
            prop_assert!(history.undo(&mut edit));
            prop_assert_eq!(&edit.to_json(), want);
        }
        prop_assert!(!history.undo(&mut edit));
        for want in seen.iter().skip(1) {
            prop_assert!(history.redo(&mut edit));
            prop_assert_eq!(&edit.to_json(), want);
        }
    }

    /// An edit with transforms reads back from its JSON as itself, and
    /// writes the same bytes again. Taking every `transform` out of the
    /// text leaves, byte for byte, the document of the same edit with
    /// every clip as fitted: what an editor from before transforms wrote,
    /// and it reads as that edit. A clip as fitted has only the fields it
    /// always had.
    #[test]
    fn a_document_without_its_transforms_is_the_document_from_before_them(edit in edits(), dresses in dresses()) {
        let edit = dress(edit, &dresses, true);
        let json = edit.to_json();
        let back = Edit::<Source>::parse(&json).unwrap();
        prop_assert_eq!(&back, &edit);
        prop_assert_eq!(back.to_json(), json.clone());
        let plain = undressed(&edit);
        let old = without_transforms(&json);
        prop_assert!(!old.contains("transform"));
        prop_assert_eq!(&old, &plain.to_json());
        prop_assert_eq!(&Edit::<Source>::parse(&old).unwrap(), &plain);
        let transformed = edit.video.iter().flat_map(|t| &t.clips).filter(|c| !c.transform.is_identity()).count();
        prop_assert_eq!(json.matches("\"transform\": {").count(), transformed);
        let value: Value = serde_json::from_str(&old).unwrap();
        for clip in value["video"].as_array().unwrap().iter().flat_map(|t| t["clips"].as_array().unwrap()) {
            let mut keys: Vec<&str> = clip.as_object().unwrap().keys().map(String::as_str).collect();
            keys.retain(|k| *k != "opacity");
            keys.sort_unstable();
            prop_assert_eq!(keys, ["at", "duration", "fade_in", "fade_out", "source", "source_in"]);
        }
    }

    /// A transform's limits, to the number: a clip moves up to 10 picture
    /// heights either way, is scaled from 0.01 to 100, and turns up to
    /// 3600 degrees either way. At a limit it is a transform (the document
    /// holds it, Set Transform takes it, and it reads back); the next
    /// number past it is none, to all three.
    #[test]
    fn a_transform_is_within_its_limits_to_the_number(
        good in transforms(),
        field in 0usize..4,
        high in any::<bool>(),
        inside in 0.0f64..=1.0,
    ) {
        let (low, top) = [(-10.0, 10.0), (-10.0, 10.0), (0.01, 100.0), (-3600.0, 3600.0)][field];
        let (limit, past) = if high { (top, f64::next_up(top)) } else { (low, f64::next_down(low)) };
        let with = |value: f64| {
            let mut t = good;
            match field {
                0 => t.x = value,
                1 => t.y = value,
                2 => t.scale = value,
                _ => t.rotation = value,
            }
            t
        };
        let mut edit = Edit::<Source>::new(24, [1920, 1080], "01:00:00:00");
        edit.video.push(crate::Track { name: "V1".into(), clips: vec![VideoClip::new(0, 10, 0, source_of(0))], color: None });
        let clip = ClipRef { track: TrackRef::video(0), index: 0 };
        for (value, fine) in [(limit, true), ((low + (top - low) * inside).clamp(low, top), true), (past, false)] {
            let transform = with(value);
            prop_assert_eq!(transform.problem().is_none(), fine, "{:?}", transform);
            let set = apply(&edit, &Op::SetTransform { clip, transform }, &Lengths);
            prop_assert_eq!(set.is_ok(), fine, "set transform {:?}", transform);
            let mut held = edit.clone();
            held.video[0].clips[0].transform = transform;
            prop_assert_eq!(held.problem().is_none(), fine);
            let read = Edit::<Source>::parse(&held.to_json());
            prop_assert_eq!(read.is_ok(), fine, "{:?} read back", transform);
            if fine {
                prop_assert_eq!(&read.unwrap(), &held);
                prop_assert_eq!(&set.unwrap(), &held);
            }
        }
    }

    /// The OTIO of an edit whose clips are moved, faded and see-through at
    /// once: every video clip is written once, wherever a dissolve put it,
    /// with its own transform and opacity in its metadata as the edit has
    /// them (none for a clip as fitted or whole); a yellow marker spans
    /// each transformed clip and each see-through one as it is written
    /// (from its first frame, up to the dissolve that cuts it); and the summary
    /// counts what is there: the clips, each fade as a transition or a
    /// note, each opacity and each transform as a note.
    #[test]
    fn the_otio_keeps_each_clips_transform_and_the_summary_counts_it(edit in edits(), dresses in dresses()) {
        let edit = dress(edit, &dresses, true);
        let frozen = freeze(&edit, &Fixed).unwrap();
        let otio = crate::otio::timeline(&frozen, "cut");
        let clips: Vec<&VideoClip<Source>> = edit.video.iter().flat_map(|t| &t.clips).collect();
        let mut written = 0;
        let mut lengths = std::collections::BTreeMap::new();
        let tracks = otio["tracks"]["children"].as_array().unwrap();
        for item in tracks.iter().filter(|t| t["kind"] == "Video").flat_map(|t| t["children"].as_array().unwrap()) {
            if item["OTIO_SCHEMA"] != "Clip.2" {
                continue;
            }
            written += 1;
            let meta = &item["metadata"]["tumblepipe"];
            let version = meta["version"].as_str().unwrap();
            let clip = clips
                .iter()
                .find(|c| matches!(&c.source, Source::Media { version: VersionRule::Pinned(v), .. } if v.as_str() == version))
                .expect("a written clip is one of the edit's");
            match meta.get("transform") {
                None => prop_assert!(clip.transform.is_identity(), "{:?} lost its transform", clip),
                Some(kept) => {
                    prop_assert!(!clip.transform.is_identity(), "a clip as fitted carries a transform");
                    prop_assert_eq!(serde_json::from_value::<Transform>(kept.clone()).unwrap(), clip.transform);
                }
            }
            prop_assert_eq!(meta.get("opacity").map(|o| o.as_f64().unwrap()), (clip.opacity < 1.0).then_some(clip.opacity));
            let length = item["source_range"]["duration"]["value"].as_f64().unwrap() as Frame;
            prop_assert!((1..=clip.duration).contains(&length), "{} frames written of {:?}", length, clip);
            lengths.insert(version.to_owned(), length);
        }
        prop_assert_eq!(written, clips.len());
        let noted = |what: &str| {
            let mut spans: Vec<(Frame, Frame)> = otio["tracks"]["markers"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|m| m["color"] == "YELLOW" && m["name"].as_str().unwrap().starts_with(what))
                .map(|m| {
                    let range = &m["marked_range"];
                    (range["start_time"]["value"].as_f64().unwrap() as Frame, range["duration"]["value"].as_f64().unwrap() as Frame)
                })
                .collect();
            spans.sort_unstable();
            spans
        };
        let spans = |of: &dyn Fn(&VideoClip<Source>) -> bool| {
            let written = |c: &VideoClip<Source>| match &c.source {
                Source::Media { version: VersionRule::Pinned(v), .. } => lengths[v.as_str()],
                _ => unreachable!("every clip here is a pinned media item"),
            };
            let mut spans: Vec<(Frame, Frame)> = clips.iter().filter(|c| of(c)).map(|c| (c.at, written(c))).collect();
            spans.sort_unstable();
            spans
        };
        let (moved, faint) = (spans(&|c| !c.transform.is_identity()), spans(&|c| c.opacity < 1.0));
        prop_assert_eq!(&noted("transform"), &moved);
        prop_assert_eq!(&noted("opacity"), &faint);
        let summary = otio_summary(&frozen, &otio, &Whole);
        prop_assert_eq!(summary.clips, clips.len());
        prop_assert_eq!(summary.audio_clips, edit.audio.iter().map(|t| t.clips.len()).sum::<usize>());
        prop_assert_eq!((summary.video_tracks, summary.audio_tracks, summary.markers), (edit.video.len(), edit.audio.len(), edit.markers.len()));
        prop_assert_eq!(summary.count(Kind::Transform), moved.len());
        prop_assert_eq!(summary.count(Kind::Opacity), faint.len());
        let fades: usize = clips.iter().map(|c| usize::from(c.fade_in > 0) + usize::from(c.fade_out > 0)).sum();
        prop_assert_eq!(summary.transitions + summary.count(Kind::Fade), fades, "a fade is a transition or a note");
        // The same edit as fitted and whole notes nothing of either.
        let mut plain = undressed(&edit);
        for c in plain.video.iter_mut().flat_map(|t| &mut t.clips) {
            c.opacity = 1.0;
        }
        let plain = freeze(&plain, &Fixed).unwrap();
        let summary = otio_summary(&plain, &crate::otio::timeline(&plain, "cut"), &Whole);
        prop_assert_eq!((summary.count(Kind::Transform), summary.count(Kind::Opacity)), (0, 0));
    }

    /// A trim (an edge dragged) and a shift (a clip pushed with what
    /// follows it) are of one clip's track: on every other track each
    /// clip is still there after, whole, with its source frames, fades,
    /// transform and opacity, in the order they were; all a trim may do
    /// to it is move it along by the frames the trim put in or took out,
    /// and a shift not even that. (Up to 1.112.0 a trim inwards took its
    /// frames out of every track: a clip of another track lying in them
    /// was deleted without a word, and one across them cut in two.) The
    /// result is an edit, and one undo gives back the bytes.
    #[test]
    fn a_trim_or_a_shift_keeps_every_other_tracks_clips_whole(
        edit in edits(),
        dresses in dresses(),
        t in any::<prop::sample::Index>(),
        c in any::<prop::sample::Index>(),
        delta in -60i64..60,
        which in 0u8..3,
    ) {
        let mut edit = dress(edit, &dresses, true);
        let Some(clip) = pick(&edit, t, c) else { return Ok(()) };
        let op = match which {
            0 => Op::Trim { clip, edge: Edge::Head, delta },
            1 => Op::Trim { clip, edge: Edge::Tail, delta },
            _ => Op::Shift { clip, delta },
        };
        let before = edit.clone();
        let bytes = before.to_json();
        let mut history = History::new();
        if history.apply(&mut edit, &op, &Lengths).is_err() {
            prop_assert_eq!(edit.to_json(), bytes);
            return Ok(());
        }
        prop_assert_eq!(edit.problem(), None);
        let by = match which {
            0 => -delta,
            1 => delta,
            _ => 0,
        };
        for (index, (was, now)) in before.video.iter().zip(&edit.video).enumerate() {
            if clip.track == TrackRef::video(index) {
                continue;
            }
            prop_assert_eq!(was.clips.len(), now.clips.len(), "V{} lost or gained a clip to {:?}", index + 1, op);
            for (a, b) in was.clips.iter().zip(&now.clips) {
                prop_assert!(b.at == a.at || b.at == a.at + by, "{:?} moved by {} under {:?}", a, b.at - a.at, op);
                prop_assert_eq!(&VideoClip { at: a.at, ..b.clone() }, a, "{:?} changed a clip of another track", op);
            }
        }
        for (index, (was, now)) in before.audio.iter().zip(&edit.audio).enumerate() {
            if clip.track == TrackRef::audio(index) {
                continue;
            }
            prop_assert_eq!(was.clips.len(), now.clips.len(), "A{} lost or gained a clip to {:?}", index + 1, op);
            for (a, b) in was.clips.iter().zip(&now.clips) {
                prop_assert!(b.at == a.at || b.at == a.at + by, "{:?} moved by {} under {:?}", a, b.at - a.at, op);
                prop_assert_eq!(&crate::document::AudioClip { at: a.at, ..b.clone() }, a, "{:?} changed a clip of another track", op);
            }
        }
        // Its own track keeps its clips too, in order: only the trimmed
        // one's length, source frames and fades may differ.
        let sources = |e: &Edit<Source>| -> Vec<Source> {
            match clip.track.kind {
                TrackKind::Video => e.video[clip.track.index].clips.iter().map(|c| c.source.clone()).collect(),
                TrackKind::Audio => e.audio[clip.track.index].clips.iter().map(|c| c.source.clone()).collect(),
            }
        };
        prop_assert_eq!(sources(&edit), sources(&before));
        prop_assert!(history.undo(&mut edit));
        prop_assert_eq!(edit.to_json(), bytes);
    }

    /// A track moved, renamed, coloured, removed or added, one after
    /// another, against a model that knows only names, colours and each
    /// track's clips as written: a move puts the track at its new place
    /// with the others in their order around it (refused to where it is,
    /// or past the top); a rename changes one name (refused when empty or
    /// another track's of its kind); a colour changes one colour (refused
    /// when no colour name); a remove takes one track, clips and all; an
    /// add puts an empty one on top (refused under a name taken). Every
    /// clip stays as it was written, transform, opacity and fades too, on
    /// the track it was on; the markers stay. A refused op changes
    /// nothing. Each result is an edit, reads back from its JSON, and
    /// writes a `color` for exactly the tracks that have one. Undo gives
    /// back every earlier document byte for byte, and redo every later.
    #[test]
    fn track_ops_keep_every_clip_and_undo_gives_the_bytes_back(
        edit in edits(),
        dresses in dresses(),
        tries in prop::collection::vec((any::<prop::sample::Index>(), any::<prop::sample::Index>(), 0u8..8), 1..16),
    ) {
        let mut edit = dress(edit, &dresses, true);
        let markers = edit.markers.clone();
        let mut model = model_of(&edit);
        let mut history = History::new();
        let mut bytes = vec![edit.to_json()];
        for &(t, to, which) in &tries {
            let Some(op) = choose(&edit, &Try::Track { t, to, which }) else { continue };
            let expected = modelled(&model, &op);
            let before = edit.to_json();
            match (history.apply(&mut edit, &op, &Lengths), expected) {
                (Ok(()), Some(next)) => model = next,
                (Err(_), None) => {
                    prop_assert_eq!(edit.to_json(), before, "{:?} was refused and changed the edit", op);
                    continue;
                }
                (Ok(()), None) => prop_assert!(false, "{:?} should have been refused", op),
                (Err(why), Some(_)) => prop_assert!(false, "{:?} was refused: {}", op, why),
            }
            prop_assert_eq!(&model_of(&edit), &model, "after {:?}", op);
            prop_assert_eq!(edit.problem(), None);
            prop_assert_eq!(&edit.markers, &markers);
            let json = edit.to_json();
            prop_assert_eq!(&Edit::<Source>::parse(&json).unwrap(), &edit);
            let document: Value = serde_json::from_str(&json).unwrap();
            for (kind, tracks) in [("video", &model.0), ("audio", &model.1)] {
                for (written, (_, color, _)) in document[kind].as_array().unwrap().iter().zip(tracks) {
                    prop_assert_eq!(written.get("color").cloned(), color.clone().map(Value::String), "{}", json);
                }
            }
            bytes.push(json);
        }
        for earlier in bytes.iter().rev().skip(1) {
            prop_assert!(history.undo(&mut edit));
            prop_assert_eq!(&edit.to_json(), earlier);
        }
        prop_assert!(!history.undo(&mut edit));
        for later in bytes.iter().skip(1) {
            prop_assert!(history.redo(&mut edit));
            prop_assert_eq!(&edit.to_json(), later);
        }
    }

    /// An edit whose tracks have no colour is, byte for byte, the document
    /// from before tracks had colours: no track says `color`. Colouring
    /// tracks and clearing the colours again gives those bytes back, and a
    /// document that names a colour reads with it.
    #[test]
    fn an_edit_without_track_colours_is_the_document_from_before_them(edit in edits(), dresses in dresses(), picks in prop::collection::vec(0usize..3, 8)) {
        let plain = dress(edit, &dresses, true);
        let bytes = plain.to_json();
        prop_assert!(!bytes.contains("\"color\""));
        let mut coloured = plain.clone();
        let tracks: Vec<TrackRef> = plain.tracks().collect();
        for (track, pick) in tracks.iter().zip(&picks) {
            let color = Some(["blue", "teal", "brick"][*pick].to_owned());
            coloured = apply(&coloured, &Op::SetTrackColor { track: *track, color }, &Lengths).unwrap();
        }
        let written = coloured.to_json();
        prop_assert_eq!(written.matches("\"color\"").count(), tracks.len().min(picks.len()));
        prop_assert_eq!(&Edit::<Source>::parse(&written).unwrap(), &coloured);
        // Published and exported, each coloured track is noted, and an
        // edit without colours has no such note.
        let n = tracks.len().min(picks.len());
        for (edit, noted) in [(&coloured, n), (&plain, 0)] {
            let frozen = freeze(edit, &Fixed).unwrap();
            let summary = otio_summary(&frozen, &crate::otio::timeline(&frozen, "cut"), &Whole);
            prop_assert_eq!(summary.count(Kind::TrackColor), noted);
            prop_assert_eq!(summary.lines().iter().filter(|l| l.contains("track colour")).count(), usize::from(noted > 0));
        }
        for track in &tracks {
            coloured = apply(&coloured, &Op::SetTrackColor { track: *track, color: None }, &Lengths).unwrap();
        }
        prop_assert_eq!(coloured.to_json(), bytes);
    }
}

/// An edit as the track ops' model sees it: per kind, bottom first, each
/// track's name, colour and clips as they are written.
type TrackModel = (Vec<(String, Option<String>, Value)>, Vec<(String, Option<String>, Value)>);

fn model_of(edit: &Edit<Source>) -> TrackModel {
    (
        edit.video.iter().map(|t| (t.name.clone(), t.color.clone(), serde_json::to_value(&t.clips).unwrap())).collect(),
        edit.audio.iter().map(|t| (t.name.clone(), t.color.clone(), serde_json::to_value(&t.clips).unwrap())).collect(),
    )
}

/// What a track op makes of the model, or None when it is to be refused.
fn modelled(model: &TrackModel, op: &Op<Source>) -> Option<TrackModel> {
    let mut next = model.clone();
    let of = |next: &mut TrackModel, kind: TrackKind| match kind {
        TrackKind::Video => std::mem::take(&mut next.0),
        TrackKind::Audio => std::mem::take(&mut next.1),
    };
    let (kind, tracks) = match op {
        Op::MoveTrack { track, to } => {
            let tracks = of(&mut next, track.kind);
            if *to >= tracks.len() || *to == track.index {
                return None;
            }
            let mut others: Vec<_> = tracks.iter().enumerate().filter(|(i, _)| *i != track.index).map(|(_, t)| t.clone()).collect();
            let above = others.split_off(*to);
            others.push(tracks[track.index].clone());
            others.extend(above);
            (track.kind, others)
        }
        Op::RenameTrack { track, name } => {
            let mut tracks = of(&mut next, track.kind);
            if name.is_empty() || tracks.iter().enumerate().any(|(i, t)| i != track.index && t.0 == *name) {
                return None;
            }
            tracks[track.index].0 = name.clone();
            (track.kind, tracks)
        }
        Op::SetTrackColor { track, color } => {
            let mut tracks = of(&mut next, track.kind);
            if let Some(name) = color
                && (name.is_empty()
                    || name.chars().count() > 32
                    || !name.chars().all(|c| "abcdefghijklmnopqrstuvwxyz0123456789-".contains(c)))
            {
                return None;
            }
            tracks[track.index].1 = color.clone();
            (track.kind, tracks)
        }
        Op::RemoveTrack { track } => {
            let mut tracks = of(&mut next, track.kind);
            tracks.remove(track.index);
            (track.kind, tracks)
        }
        Op::AddTrack { kind, name } => {
            let mut tracks = of(&mut next, *kind);
            if tracks.iter().any(|t| t.0 == *name) {
                return None;
            }
            tracks.push((name.clone(), None, Value::Array(Vec::new())));
            (*kind, tracks)
        }
        other => panic!("{other:?} is no track op"),
    };
    match kind {
        TrackKind::Video => next.0 = tracks,
        TrackKind::Audio => next.1 = tracks,
    }
    Some(next)
}

/// `op` with a clip it adds as fitted, and a transform it sets none: what
/// it is to an edit whose clips are all as fitted.
fn op_undressed(op: &Op<Source>) -> Op<Source> {
    let plain = |clip: &NewClip<Source>| match clip {
        NewClip::Video(c) => NewClip::Video(VideoClip { transform: Transform::IDENTITY, ..c.clone() }),
        audio => audio.clone(),
    };
    match op {
        Op::Place { track, clip } => Op::Place { track: *track, clip: plain(clip) },
        Op::Insert { track, clip } => Op::Insert { track: *track, clip: plain(clip) },
        Op::SetTransform { clip, .. } => Op::SetTransform { clip: *clip, transform: Transform::IDENTITY },
        other => other.clone(),
    }
}
