//! A clip's right-click popup where two features of 2026-11-22 meet:
//! Convert to Shot's rows (`convert_shot`) and the clip menu's
//! (`clip_menu`), merged by hand into one popup. Wired: a right-click on any
//! clip of a cut of slugs, shots, converted clips and clips swapped back,
//! then every row the popup lists sent to the editor.

use th_edit::document::Rule;

use super::*;
use crate::convert_shot;
use crate::editor::file_dialogs::Dialog;
use crate::editor::range_shot::Place;

/// A planned shot (the project template has no sequence `ZZ`).
fn planned(n: usize) -> Source {
    Source::Shot { shot: format!("entity:/shots/ZZ/SH{n:03}0"), take: Rule::best() }
}

/// [`cut`] with each video clip left a slug, made a shot's, converted to
/// a shot (keeping the slug), or converted and swapped back (the slug
/// keeping the shot).
fn mixed_cut() -> impl Strategy<Value = WorkingEdit> {
    (cut(), prop::collection::vec(0u8..4, 16)).prop_map(|(mut edit, kinds)| {
        let clips = edit.video.iter_mut().flat_map(|t| t.clips.iter_mut());
        for (n, (clip, kind)) in clips.zip(kinds.into_iter().cycle()).enumerate() {
            let shot = VideoClip::new(clip.at, clip.duration, 1001, planned(n));
            *clip = match kind {
                0 => continue,
                1 => shot,
                2 => convert_shot::converted(clip, shot),
                _ => convert_shot::swapped(&convert_shot::converted(clip, shot)).unwrap(),
            };
        }
        edit
    })
}

/// What a row's message does to the cut.
fn changes(message: &Message) -> bool {
    matches!(message, Message::Edit(_) | Message::Lift | Message::RippleDelete | Message::CloseGap(_) | Message::SwapOriginal(_))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// A right-click on any clip of any cut lists the clip menu's rows and
    /// Convert to Shot's in one popup: no label twice; Convert to Shot…
    /// exactly on a video clip that is not a shot's, the swap exactly on
    /// one that keeps the clip it replaced, both only on a cut that can
    /// change; on a published cut no row changes it. Every row is one the
    /// editor does: a change is taken (never "Not done") and is one undo
    /// step, Convert to Shot… opens New shot over the clip's own frames,
    /// and the rest leave the cut as it is.
    #[test]
    fn the_popup_lists_both_features_rows_and_each_is_done(
        edit in mixed_cut(), read_only in any::<bool>(), stage in hand(), pick in any::<Index>(), within in any::<Index>(),
        playhead in any::<Index>(),
    ) {
        let mut bench = Bench::new(&edit, read_only);
        let all: Vec<TrackRef> = edit.tracks().collect();
        let shown = clip_refs(&edit, &all);
        prop_assume!(!shown.is_empty());
        let clip = *pick.get(&shown);
        let (at, end) = edit.spans(clip.track)[clip.index];
        let frame = (at + within.index((end - at) as usize) as EditFrame) as usize;
        prop_assume!(frame < bench.editor.tracks_program().total);
        let playhead = playhead.index(edit.length() as usize + 1);
        bench.send(Message::Seek(playhead));
        let mut hand = Hand::new(stage);
        let rows = bench.editor.rows();
        let row = rows.iter().position(|r| !r.spare && r.track == clip.track).expect("every track has a row");
        let point = hand.over(frame as EditFrame, Hand::row_y(&bench.editor, row, 0.5));
        let said = hand.right_press(&mut bench.editor, point);
        prop_assert_eq!(said, Some(format!("{:?}", Message::ClipMenu(clip, frame))));
        let state = bench.editor.clip_menu_state(clip, frame).expect("the clip is there");
        let popup = clip_menu::clip_rows(bench.edit(), &state);

        let mut labels: Vec<&str> = popup.iter().map(|(l, _)| l.as_str()).collect();
        labels.sort_unstable();
        let count = labels.len();
        labels.dedup();
        prop_assert_eq!(labels.len(), count, "a label twice: {:?}", popup);
        let video = convert_shot::video_clip(&edit, clip);
        let convert = !read_only && video.is_some_and(convert_shot::convertible);
        let swap = !read_only && video.is_some_and(|c| c.original.is_some());
        prop_assert_eq!(popup.iter().any(|(_, m)| matches!(m, Message::ConvertToShot(c) if *c == clip)), convert);
        prop_assert_eq!(popup.iter().any(|(_, m)| matches!(m, Message::SwapOriginal(c) if *c == clip)), swap);
        prop_assert_eq!(popup.iter().filter(|(_, m)| matches!(m, Message::ConvertToShot(_) | Message::SwapOriginal(_))).count(), usize::from(convert) + usize::from(swap));
        if read_only {
            for (label, message) in &popup {
                prop_assert!(matches!(message, Message::Seek(_)), "{} ({:?}) on a published cut", label, message);
            }
        }

        for (label, message) in popup {
            // As it was when the popup opened: the clip picked alone, the
            // playhead where it was.
            bench.send(Message::Seek(playhead));
            bench.send(Message::ClipMenu(clip, frame));
            bench.editor.selection = BTreeSet::from([clip]);
            let before = bench.edit().clone();
            bench.send(message.clone());
            if changes(&message) {
                prop_assert_eq!(bench.refusal(), None, "{} was refused", label);
                prop_assert!(bench.edit() != &before, "{} did nothing", label);
                prop_assert_eq!(bench.edit().problem(), None);
                bench.send(Message::Undo);
                prop_assert_eq!(bench.edit(), &before, "{} was not one undo step", label);
            } else {
                prop_assert_eq!(bench.edit(), &before, "{} changed the cut", label);
            }
            match message {
                Message::ConvertToShot(_) => {
                    let Some(Dialog::NewShot { place, frames, now, .. }) = bench.editor.dialog.clone() else {
                        return Err(TestCaseError::fail(format!("{label} opened no New shot")));
                    };
                    prop_assert_eq!(place, Some(Place { track: clip.track.index, at, frames: end - at, replaces: true }));
                    prop_assert_eq!(frames, (end - at).to_string());
                    prop_assert!(now);
                    bench.send(Message::Dismiss);
                }
                Message::Seek(to) => prop_assert_eq!(bench.editor.position, to),
                _ => prop_assert!(bench.editor.dialog.is_none(), "{} opened a dialog", label),
            }
        }
    }
}
