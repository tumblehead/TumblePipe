//! How the editor shows its tracks' rows: how tall each is.
//!
//! This is the view's, never the edit's: nothing here is in the edit
//! document, its undo history or what plays. The heights are this user's,
//! kept between sessions beside the panel sizes (`layout_prefs`) in
//! `tracks.json`: by track, V1, V2, ... and A1, A2, ..., in any edit, as the
//! timeline's height is.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use th_edit::document::{Edit, TrackKind, TrackRef};
use th_edit::ops::Op;

use crate::tracks;

/// How many tracks of a kind a height is remembered for.
const REMEMBERED: usize = 64;

/// The rows' heights as the user left them, in logical pixels.
#[derive(Debug, Clone, PartialEq, Default)]
pub(crate) struct TrackLayout {
    /// By track index; a track past the end is [`tracks::ROW`] tall.
    video: Vec<f32>,
    audio: Vec<f32>,
    /// The audio tracks have no rows (View > Hide Audio Tracks).
    pub(crate) audio_hidden: bool,
}

/// Which tracks the timeline shows a row for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(crate) struct Focus {
    /// The one track shown (its name was double-clicked); None: all.
    pub solo: Option<TrackRef>,
    /// The audio tracks are left out.
    pub audio_hidden: bool,
}

/// The solo after a double-click on `track`'s name: that track, or, when it
/// was the one shown already, none (every track shows again).
pub(crate) fn toggle_solo(solo: Option<TrackRef>, track: TrackRef) -> Option<TrackRef> {
    (solo != Some(track)).then_some(track)
}

/// `solo` while the edit has `video` and `audio` tracks: None once its
/// track is gone (an undo took it), so a track made later is not soloed.
pub(crate) fn kept_solo(solo: Option<TrackRef>, video: usize, audio: usize) -> Option<TrackRef> {
    solo.filter(|t| {
        t.index
            < match t.kind {
                TrackKind::Video => video,
                TrackKind::Audio => audio,
            }
    })
}

/// The tracks the timeline shows a row for, top first, of an edit with
/// `video` and `audio` tracks: the soloed one alone, else the video tracks
/// (top track first) and, unless hidden, the audio tracks.
///
/// Only the rows: a track without a row still plays, is still saved, and
/// still moves with a ripple.
pub(crate) fn shown(video: usize, audio: usize, focus: Focus) -> Vec<TrackRef> {
    if let Some(solo) = kept_solo(focus.solo, video, audio) {
        return vec![solo];
    }
    let audio = if focus.audio_hidden { 0 } else { audio };
    tracks::row_order(video, audio)
}

/// The spare layer: the video track that is not there yet, one above the
/// top one, which the timeline shows an empty row for whenever the top
/// video track holds a clip (`top_used`) or there is no video track. A clip
/// dragged onto it makes the track ([`landing`]), and the next spare shows
/// above that, so there is always a layer to drag up to (as in Resolve).
///
/// It is a row only, never a track of the edit: nothing is saved for it.
/// None on an edit that cannot change, and while a track is soloed.
pub(crate) fn spare(video: usize, top_used: bool, focus: Focus, writable: bool, audio: usize) -> Option<TrackRef> {
    let free = writable && kept_solo(focus.solo, video, audio).is_none();
    (free && (video == 0 || top_used)).then(|| TrackRef::video(video))
}

/// Whether `edit`'s top video track holds a clip.
pub(crate) fn top_used<S>(edit: &Edit<S>) -> bool {
    edit.video.last().is_some_and(|t| !t.clips.is_empty())
}

/// A name for a new track that none of `taken` (the names of the tracks of
/// its kind) has: `letter` and the next number, as V3 on top of V1 and V2,
/// or the first number after it that is free (tracks renamed, moved or
/// deleted leave numbers taken out of order, and an edit refuses a second
/// track of a name).
pub(crate) fn fresh_name<N: AsRef<str>>(letter: char, taken: &[N]) -> String {
    let mut number = taken.len() + 1;
    while taken.iter().any(|name| name.as_ref() == format!("{letter}{number}")) {
        number += 1;
    }
    format!("{letter}{number}")
}

/// The ops a drag's `op` is done with: a clip moved onto the spare layer
/// (the video track one past the top) first makes that track, in the same
/// undo step; anything else is the op alone.
pub(crate) fn landing<S>(edit: &Edit<S>, op: Op<S>) -> Vec<Op<S>> {
    match &op {
        Op::Move { clip, to, .. } if clip.track.kind == TrackKind::Video && to.kind == TrackKind::Video && to.index == edit.video.len() => {
            let taken: Vec<&str> = edit.video.iter().map(|t| t.name.as_str()).collect();
            vec![Op::AddTrack { kind: TrackKind::Video, name: fresh_name('V', &taken) }, op]
        }
        _ => vec![op],
    }
}

/// What the rows leave out, for the line under them; None when every track
/// has its row.
pub(crate) fn note(video: usize, audio: usize, focus: Focus) -> Option<String> {
    let plural = |n: usize, what: &str| if n == 1 { format!("1 {what}") } else { format!("{n} {what}s") };
    if kept_solo(focus.solo, video, audio).is_some() {
        let others = video + audio - 1;
        return (others > 0).then(|| format!("{} hidden \u{b7} double-click the track's name to show all", plural(others, "other track")));
    }
    (focus.audio_hidden && audio > 0).then(|| format!("{} hidden \u{b7} View > Show Audio Tracks", plural(audio, "audio track")))
}

impl TrackLayout {
    fn heights(&self, kind: TrackKind) -> &Vec<f32> {
        match kind {
            TrackKind::Video => &self.video,
            TrackKind::Audio => &self.audio,
        }
    }

    /// How tall `track`'s row is.
    pub(crate) fn height(&self, track: TrackRef) -> f32 {
        self.heights(track.kind).get(track.index).copied().unwrap_or(tracks::ROW)
    }

    /// `track`'s row is `height` tall now (within the rows' limits,
    /// [`tracks::row_height`]); every other row as it was.
    pub(crate) fn set_height(&mut self, track: TrackRef, height: f32) {
        if track.index >= REMEMBERED {
            return;
        }
        let heights = match track.kind {
            TrackKind::Video => &mut self.video,
            TrackKind::Audio => &mut self.audio,
        };
        if heights.len() <= track.index {
            heights.resize(track.index + 1, tracks::ROW);
        }
        heights[track.index] = tracks::row_height(height);
    }

    /// The layout a file's JSON says, whatever it does not say (or says
    /// nonsense for) at its default.
    pub(crate) fn from_json(value: &Value) -> Self {
        let heights = |key: &str| -> Vec<f32> {
            let list = value.get(key).and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
            list.iter().take(REMEMBERED).map(|h| h.as_f64().map_or(tracks::ROW, |h| tracks::row_height(h as f32))).collect()
        };
        let audio_hidden = value.get("audio_hidden").and_then(Value::as_bool).unwrap_or(false);
        Self { video: heights("video"), audio: heights("audio"), audio_hidden }
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({"video": self.video, "audio": self.audio, "audio_hidden": self.audio_hidden})
    }
}

/// The file the rows' layout is kept in, beside the panel sizes' at
/// `layout` (`layout_prefs::path`).
pub(crate) fn path(layout: &Path) -> PathBuf {
    layout.with_file_name("tracks.json")
}

/// The layout saved at `path`; the default when there is none, or it does
/// not read.
pub(crate) fn load(path: &Path) -> TrackLayout {
    th_pipe::io::load_or_default(path, TrackLayout::from_json)
}

/// Save `layout` at `path`, whole: written beside it and renamed over it,
/// as the panel sizes are.
pub(crate) fn save(path: &Path, layout: &TrackLayout) -> std::io::Result<()> {
    th_pipe::io::store_pretty_json(path, &layout.to_json())
}

#[cfg(test)]
pub(crate) mod tests {
    // Heights are whole pixels, compared as written.
    #![allow(clippy::float_cmp)]

    use super::*;
    use proptest::prelude::*;
    use th_edit::document::{ClipRef, Frame, Source, Track, VideoClip, WorkingEdit};
    use th_edit::ops::{Available, Sources, apply};

    proptest! {
        #[test]
        fn the_track_layout_is_saved_whole(layout in layout()) {
            crate::layout_prefs::tests::saved_whole(&|path| save(path, &layout), &layout.to_json())?;
        }
    }

    #[test]
    fn nothing_is_left_when_the_track_layout_is_blocked() {
        crate::layout_prefs::tests::nothing_left_when_blocked(&|path| save(path, &TrackLayout::default()));
    }

    pub(crate) fn track() -> impl Strategy<Value = TrackRef> {
        (any::<bool>(), 0usize..12).prop_map(|(video, index)| if video { TrackRef::video(index) } else { TrackRef::audio(index) })
    }

    pub(crate) fn layout() -> impl Strategy<Value = TrackLayout> {
        (prop::collection::vec((track(), -100f32..600.0), 0..12), any::<bool>()).prop_map(|(sets, audio_hidden)| {
            let mut layout = TrackLayout { audio_hidden, ..TrackLayout::default() };
            for (track, height) in sets {
                layout.set_height(track, height);
            }
            layout
        })
    }

    /// Any JSON value, a few levels deep.
    fn any_json() -> impl Strategy<Value = Value> {
        let leaf = prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::from),
            (-1e9f64..1e9).prop_map(Value::from),
            "[a-z]{0,6}".prop_map(Value::from),
        ];
        leaf.prop_recursive(3, 24, 6, |inner| {
            prop_oneof![
                prop::collection::vec(inner.clone(), 0..6).prop_map(Value::from),
                prop::collection::btree_map(prop_oneof![Just("video".to_owned()), Just("audio".to_owned()), "[a-z]{1,5}"], inner, 0..5)
                    .prop_map(|m| Value::Object(m.into_iter().collect())),
            ]
        })
    }

    proptest! {
        /// A new track's name is its letter and a number, no track has it,
        /// and no free number between the count of tracks and it was
        /// passed over: V3 on top of V1 and V2, as it always was. So a
        /// track can be added, and a clip dropped on the spare layer,
        /// whatever the tracks were renamed to or whichever were deleted.
        #[test]
        fn a_new_tracks_name_is_free(numbers in prop::collection::btree_set(1usize..12, 0..8), words in prop::collection::btree_set("[A-Z][a-z]{1,4}", 0..3)) {
            let taken: Vec<String> = numbers.iter().map(|n| format!("V{n}")).chain(words.iter().cloned()).collect();
            let name = fresh_name('V', &taken);
            prop_assert!(!taken.contains(&name));
            let number: usize = name.strip_prefix('V').and_then(|n| n.parse().ok()).expect("V and a number");
            prop_assert!(number > taken.len());
            for skipped in taken.len() + 1..number {
                prop_assert!(numbers.contains(&skipped), "V{} was free", skipped);
            }
            // A drop on the spare layer makes its track under that name.
            let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
            for (i, name) in taken.iter().enumerate() {
                let clips = if i + 1 == taken.len() { vec![VideoClip::new(0, 10, 0, Source::Slug { text: "x".into() })] } else { Vec::new() };
                edit.video.push(Track { name: name.clone(), clips, color: None });
            }
            if let Some(top) = taken.len().checked_sub(1) {
                let clip = ClipRef { track: TrackRef::video(top), index: 0 };
                let ops = landing(&edit, Op::Move { clip, to: TrackRef::video(top + 1), at: 0 });
                let after = apply_all(&edit, &ops).expect("the drop is taken");
                prop_assert_eq!(&after.video[top + 1].name, &name);
                prop_assert_eq!(after.video[top + 1].clips.len(), 1);
            }
        }

        /// A track's row is as tall as it was last dragged (within the
        /// rows' limits), every other track's row as tall as it was, and a
        /// track never dragged the default.
        #[test]
        fn a_row_keeps_the_height_it_was_given(layout in layout(), track in track(), height in -100f32..600.0, other in track()) {
            let mut after = layout.clone();
            after.set_height(track, height);
            prop_assert_eq!(after.height(track), tracks::row_height(height));
            if other != track {
                prop_assert_eq!(after.height(other), layout.height(other));
            }
            prop_assert_eq!(TrackLayout::default().height(other), tracks::ROW);
        }

        /// What is saved reads back as it was, through the file too.
        #[test]
        fn a_layout_reads_back_as_saved(layout in layout()) {
            prop_assert_eq!(&TrackLayout::from_json(&layout.to_json()), &layout);
            let dir = tempfile::tempdir().unwrap();
            let file = path(&dir.path().join("layout.json"));
            save(&file, &layout).unwrap();
            prop_assert_eq!(load(&file), layout);
        }

        /// Whatever a file holds, it loads: every row at a height a row may
        /// have.
        #[test]
        fn any_json_loads_as_heights_within_reason(value in any_json(), track in track()) {
            let layout = TrackLayout::from_json(&value);
            let height = layout.height(track);
            prop_assert!((tracks::ROW_MIN..=tracks::ROW_MAX).contains(&height));
        }
    }

    pub(crate) fn focus() -> impl Strategy<Value = Focus> {
        (proptest::option::of(track()), any::<bool>()).prop_map(|(solo, audio_hidden)| Focus { solo, audio_hidden })
    }

    proptest! {
        /// The rows are tracks of the edit, each once, in the order they
        /// are drawn without a focus; a soloed track is the only row; with
        /// the audio hidden every video track has its row and no audio
        /// track has; with neither, every track has its row.
        #[test]
        fn the_rows_are_what_the_focus_leaves(video in 0usize..6, audio in 0usize..6, focus in focus()) {
            let all = tracks::row_order(video, audio);
            let rows = shown(video, audio, focus);
            let mut at = 0;
            for row in &rows {
                let found = all[at..].iter().position(|t| t == row);
                prop_assert!(found.is_some(), "{row:?} is no track of the edit, or is out of order");
                at += found.unwrap() + 1;
            }
            let solo = focus.solo.filter(|t| all.contains(t));
            match solo {
                Some(track) => prop_assert_eq!(&rows, &vec![track]),
                None if focus.audio_hidden => {
                    prop_assert_eq!(&rows, &tracks::row_order(video, 0));
                }
                None => prop_assert_eq!(&rows, &all),
            }
            // The line under the rows is there exactly when a track has
            // no row.
            prop_assert_eq!(note(video, audio, focus).is_some(), rows.len() < all.len());
        }

        /// A double-click on a track's name shows it alone; a second on the
        /// same name shows every track again; one on another name solos
        /// that one instead.
        #[test]
        fn a_double_click_solos_and_again_restores(video in 1usize..6, audio in 1usize..6, a in track(), b in track(), audio_hidden in any::<bool>()) {
            let all = tracks::row_order(video, audio);
            let (a, b) = (all[a.index % all.len()], all[(a.index + 1 + b.index) % all.len()]);
            let before = shown(video, audio, Focus { solo: None, audio_hidden });
            let solo = toggle_solo(None, a);
            prop_assert_eq!(shown(video, audio, Focus { solo, audio_hidden }), vec![a]);
            prop_assert_eq!(shown(video, audio, Focus { solo: toggle_solo(solo, a), audio_hidden }), before);
            if a != b {
                prop_assert_eq!(shown(video, audio, Focus { solo: toggle_solo(solo, b), audio_hidden }), vec![b]);
            }
        }

        /// A solo does not outlive its track: once the edit has no such
        /// track (an undo), every track shows, and a track made later at
        /// that place is not soloed.
        #[test]
        fn a_solo_goes_with_its_track(video in 0usize..6, audio in 0usize..6, solo in track()) {
            let exists = tracks::row_order(video, audio).contains(&solo);
            prop_assert_eq!(kept_solo(Some(solo), video, audio), exists.then_some(solo));
            prop_assert_eq!(kept_solo(None, video, audio), None);
        }
    }

    struct AnyFrame;
    impl Sources<Source> for AnyFrame {
        fn available(&self, _: &Source) -> Available {
            Available::Any
        }
    }

    /// An edit of video tracks (each track's clips as gap before, length)
    /// and one empty audio track.
    fn edit_of(video: &[Vec<(Frame, Frame)>]) -> WorkingEdit {
        let mut edit = WorkingEdit::new(24, [1920, 1080], "01:00:00:00");
        for (index, spans) in video.iter().enumerate() {
            let mut at = 0;
            let clips = spans
                .iter()
                .map(|&(gap, duration)| {
                    at += gap;
                    let clip = VideoClip {
                        at,
                        duration,
                        source_in: 0,
                        source: Source::Slug { text: format!("{index}.{at}") },
                        fade_in: 0,
                        fade_out: 0,
                        opacity: 1.0,
                        transform: Default::default(),
                    };
                    at += duration;
                    clip
                })
                .collect();
            edit.video.push(Track { name: format!("V{}", index + 1), clips, color: None });
        }
        edit.audio.push(Track { name: "A1".into(), clips: Vec::new(), color: None });
        edit
    }

    fn video_tracks() -> impl Strategy<Value = Vec<Vec<(Frame, Frame)>>> {
        prop::collection::vec(prop::collection::vec((0i64..40, 1i64..60), 0..4), 0..4)
    }

    fn apply_all(edit: &WorkingEdit, ops: &[Op<Source>]) -> Result<WorkingEdit, th_edit::ops::Refused> {
        ops.iter().try_fold(edit.clone(), |edit, op| apply(&edit, op, &AnyFrame))
    }

    proptest! {
        /// There is always one empty layer over the top used one: the rows
        /// start with the spare exactly when the top video track holds a
        /// clip (or there is none), it is the track one past the top, so no
        /// track of the edit, and a top track still empty is the empty
        /// layer itself. None while a track is soloed, or on an edit that
        /// cannot change.
        #[test]
        fn one_empty_layer_tops_the_used_ones(video in video_tracks(), focus in focus(), writable in any::<bool>()) {
            let edit = edit_of(&video);
            let soloed = kept_solo(focus.solo, edit.video.len(), edit.audio.len()).is_some();
            let top_empty = edit.video.last().is_some_and(|t| t.clips.is_empty());
            let spare = spare(edit.video.len(), top_used(&edit), focus, writable, edit.audio.len());
            prop_assert_eq!(spare.is_some(), writable && !soloed && !top_empty);
            if let Some(track) = spare {
                prop_assert_eq!(track, TrackRef::video(edit.video.len()));
                prop_assert!(!edit.tracks().any(|t| t == track), "the spare is a track of the edit");
            }
        }

        /// A clip dragged onto the spare layer lands on a new top track,
        /// as one step, and a new spare shows above it. The track is only
        /// made for a clip that lands on it: the edit never gets an empty
        /// track from the spare. (Before, a move there was refused: there
        /// was no such track, and no row to drop on.)
        #[test]
        fn a_clip_dropped_on_the_spare_makes_the_layer(video in video_tracks(), pick in any::<prop::sample::Index>(), at in 0i64..400) {
            let edit = edit_of(&video);
            let clips: Vec<ClipRef> =
                edit.tracks().filter(|t| t.kind == TrackKind::Video).flat_map(|track| (0..edit.spans(track).len()).map(move |index| ClipRef { track, index })).collect();
            prop_assume!(!clips.is_empty());
            let clip = *pick.get(&clips);
            let moved = edit.video[clip.track.index].clips[clip.index].clone();
            let top = edit.video.len();
            let op = Op::Move { clip, to: TrackRef::video(top), at };
            prop_assert!(apply(&edit, &op, &AnyFrame).is_err(), "the move alone has no track to land on");

            let ops = landing(&edit, op);
            let after = apply_all(&edit, &ops).expect("the clip lands on the spare layer");
            prop_assert_eq!(after.video.len(), top + 1);
            prop_assert_eq!(&after.video[top].name, &format!("V{}", top + 1));
            prop_assert_eq!(&after.video[top].clips, &vec![VideoClip { at, ..moved }]);
            // Every other track is as it was, less the clip.
            for (index, track) in edit.video.iter().enumerate() {
                let mut clips = track.clips.clone();
                if index == clip.track.index {
                    clips.remove(clip.index);
                }
                prop_assert_eq!(&after.video[index].clips, &clips);
            }
            // And the next spare is one above the new layer.
            prop_assert_eq!(spare(after.video.len(), top_used(&after), Focus::default(), true, 1), Some(TrackRef::video(top + 1)));
        }

        /// A drag that is not onto the spare layer is its op alone: no
        /// track is made for it.
        #[test]
        fn any_other_drag_makes_no_track(video in video_tracks().prop_filter("a track", |v| !v.is_empty()), to in 0usize..4, at in 0i64..400, delta in -30i64..30) {
            let edit = edit_of(&video);
            let clip = ClipRef { track: TrackRef::video(0), index: 0 };
            let to = TrackRef::video(to % edit.video.len());
            for op in [
                Op::Move { clip, to, at },
                Op::Move { clip: ClipRef { track: TrackRef::audio(0), index: 0 }, to: TrackRef::audio(1), at },
                Op::Trim { clip, edge: th_edit::ops::Edge::Tail, delta },
                Op::Slip { clip, delta },
            ] {
                prop_assert_eq!(landing(&edit, op.clone()), vec![op]);
            }
        }
    }

    #[test]
    fn the_note_says_what_is_hidden() {
        let hidden = Focus { solo: None, audio_hidden: true };
        assert_eq!(note(2, 2, hidden).unwrap(), "2 audio tracks hidden \u{b7} View > Show Audio Tracks");
        assert_eq!(note(2, 0, hidden), None);
        let solo = Focus { solo: Some(TrackRef::video(1)), audio_hidden: false };
        assert_eq!(note(2, 0, solo).unwrap(), "1 other track hidden \u{b7} double-click the track's name to show all");
        assert_eq!(note(2, 1, Focus::default()), None);
    }

    #[test]
    fn a_missing_file_is_the_default() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(load(&dir.path().join("none").join("tracks.json")), TrackLayout::default());
        assert_eq!(path(Path::new("/prefs/cinedex/layout.json")), Path::new("/prefs/cinedex/tracks.json"));
    }
}
