//! What is picked on the tracks, through the pointer and the keys: the
//! selection (click, Ctrl+click, Shift+click, a box) and how it follows
//! its clips; a gap, and closing it.

use super::*;

/// The name of clip `clip` of `edit`.
fn name_of(edit: &WorkingEdit, clip: ClipRef) -> String {
    clip_source(edit, clip).map(named).expect("a clip")
}

/// The names of the clips selected.
fn selected_names(bench: &Bench) -> BTreeSet<String> {
    bench.editor.selection.iter().map(|clip| name_of(bench.edit(), *clip)).collect()
}

/// Every frame of the cut's tracks that is on no clip but has a clip of
/// its track after it: the track, the frame, and the index of that clip.
fn gap_frames(edit: &WorkingEdit) -> Vec<(TrackRef, EditFrame, usize)> {
    let mut out = Vec::new();
    for track in edit.tracks() {
        let spans = edit.spans(track);
        let last = spans.last().map_or(0, |s| s.0);
        for frame in 0..last {
            if !spans.iter().any(|&(at, end)| at <= frame && frame < end) {
                out.push((track, frame, spans.iter().position(|&(at, _)| at > frame).unwrap()));
            }
        }
    }
    out
}

/// Click the middle of `clip` with `held` down.
fn click(bench: &mut Bench, hand: &mut Hand, clip: ClipRef, held: Modifiers) {
    hand.hold(&mut bench.editor, held);
    let at = hand.grip_point(&bench.editor, clip, Grip::Body);
    let before = bench.edit().clone();
    hand.press(&mut bench.editor, at);
    hand.release(&mut bench.editor, at);
    hand.hold(&mut bench.editor, Modifiers::NONE);
    // A click is no drag: nothing snaps, nothing moves.
    assert_eq!(bench.edit(), &before, "a click on {clip:?} changed the cut");
}

const SHIFT: Modifiers = Modifiers { shift: true, ..Modifiers::NONE };

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// What is selected stays selected through a drag, its undo and its
    /// redo: the same clips by name, wherever the drag left them, and
    /// never a clip that was not selected. (A press on a clip that is not
    /// selected selects it alone first, as a click does.)
    #[test]
    fn the_selection_follows_its_clips_through_a_drag_and_its_undo(
        edit in cut(), stage in hand(), picks in prop::collection::vec(any::<Index>(), 0..4),
        pick in any::<Index>(), grip in grip(), dx in -70i64..70,
    ) {
        let all = clip_refs(&edit, &edit.tracks().collect::<Vec<_>>());
        prop_assume!(!all.is_empty());
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        // Ctrl (Cmd) clicks: each adds its clip or takes it out.
        let mut chosen: BTreeSet<ClipRef> = BTreeSet::new();
        for p in &picks {
            let clip = *p.get(&all);
            click(&mut bench, &mut hand, clip, Modifiers::COMMAND);
            if !chosen.remove(&clip) {
                chosen.insert(clip);
            }
        }
        prop_assert_eq!(&bench.editor.selection, &chosen, "Ctrl+click toggles one clip");
        let clip = *pick.get(&all);
        let expected: BTreeSet<String> =
            if chosen.contains(&clip) { chosen.iter().map(|c| name_of(&edit, *c)).collect() } else { BTreeSet::from([name_of(&edit, clip)]) };
        hand.drag(&mut bench.editor, clip, grip, dx, None);
        prop_assert_eq!(&selected_names(&bench), &expected, "after the drag");
        if bench.editor.doc.can_undo() {
            bench.send(Message::Undo);
            prop_assert_eq!(bench.edit(), &edit);
            prop_assert_eq!(&selected_names(&bench), &expected, "after the undo");
            bench.send(Message::Redo);
            prop_assert_eq!(&selected_names(&bench), &expected, "after the redo");
        }
    }

    /// Several clips selected and one of them dragged along its track:
    /// every selected clip moves by the same frames, each on its own
    /// track, none onto a clip that stays; nothing else moves; it is one
    /// undo step and it is never refused for want of room.
    #[test]
    fn selected_clips_dragged_together_keep_their_distances(
        edit in cut(), stage in hand(), picks in prop::collection::vec(any::<Index>(), 2..5), dx in -70i64..70,
    ) {
        let all = clip_refs(&edit, &edit.tracks().collect::<Vec<_>>());
        prop_assume!(!all.is_empty());
        let chosen: BTreeSet<ClipRef> = picks.iter().map(|p| *p.get(&all)).collect();
        prop_assume!(chosen.len() > 1);
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        for clip in &chosen {
            click(&mut bench, &mut hand, *clip, Modifiers::COMMAND);
        }
        let names: BTreeSet<String> = chosen.iter().map(|c| name_of(&edit, *c)).collect();
        let dragged = *picks[0].get(&all);
        hand.drag(&mut bench.editor, dragged, Grip::Body, dx, None);
        prop_assert_eq!(bench.refusal(), None, "a move of several clips was refused");
        let (before, after) = (clips(&edit), clips(bench.edit()));
        let place = |all: &[Known], name: &str| all.iter().find(|c| c.0 == name).cloned().unwrap();
        let moved = place(&after, &name_of(&edit, dragged)).2 - place(&before, &name_of(&edit, dragged)).2;
        for was in &before {
            let now = place(&after, &was.0);
            let by = if names.contains(&was.0) { moved } else { 0 };
            prop_assert_eq!(&now, &(was.0.clone(), was.1, was.2 + by, was.3), "{} moved wrongly", &was.0);
        }
        prop_assert_eq!(bench.edit().problem(), None);
        prop_assert_eq!(&selected_names(&bench), &names, "the clips moved stay selected");
        if moved != 0 {
            bench.send(Message::Undo);
            prop_assert_eq!(bench.edit(), &edit, "a move of several clips is one undo step");
        }
    }

    /// A click selects one clip; Shift+click then selects from it to the
    /// clip clicked, in place of what was selected: on one track those two
    /// and the clips between; across tracks every clip, on the rows from
    /// one to the other, with a frame between the start of the earlier and
    /// the end of the later. Ctrl+click then toggles one clip and is where
    /// the next Shift+click ranges from.
    #[test]
    fn a_shift_click_selects_the_range_from_the_last_click(
        edit in cut(), stage in hand(), a in any::<Index>(), b in any::<Index>(), c in any::<Index>(), d in any::<Index>(),
    ) {
        let all = clip_refs(&edit, &edit.tracks().collect::<Vec<_>>());
        prop_assume!(!all.is_empty());
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        let rows = View::default().rows(&edit);
        let range = |from: ClipRef, to: ClipRef| -> BTreeSet<ClipRef> {
            let row = |clip: ClipRef| rows.iter().position(|t| *t == clip.track).unwrap();
            let (one, other) = (edit.spans(from.track)[from.index], edit.spans(to.track)[to.index]);
            all.iter()
                .copied()
                .filter(|clip| {
                    if from.track == to.track {
                        return clip.track == from.track && (from.index.min(to.index)..=from.index.max(to.index)).contains(&clip.index);
                    }
                    let (at, end) = edit.spans(clip.track)[clip.index];
                    (row(from).min(row(to))..=row(from).max(row(to))).contains(&row(*clip))
                        && (at..end).any(|f| one.0.min(other.0) <= f && f < one.1.max(other.1))
                })
                .collect()
        };
        let (a, b, c, d) = (*a.get(&all), *b.get(&all), *c.get(&all), *d.get(&all));
        click(&mut bench, &mut hand, a, Modifiers::NONE);
        prop_assert_eq!(&bench.editor.selection, &BTreeSet::from([a]));
        click(&mut bench, &mut hand, b, SHIFT);
        prop_assert_eq!(&bench.editor.selection, &range(a, b), "Shift+click from {:?} to {:?}", a, b);
        // Again from the same clip: the range is replaced, not grown.
        click(&mut bench, &mut hand, d, SHIFT);
        prop_assert_eq!(&bench.editor.selection, &range(a, d), "a second Shift+click ranges from the same clip");
        let mut expected = range(a, d);
        click(&mut bench, &mut hand, c, Modifiers::COMMAND);
        if !expected.remove(&c) {
            expected.insert(c);
        }
        prop_assert_eq!(&bench.editor.selection, &expected, "Ctrl+click toggles {:?}", c);
        click(&mut bench, &mut hand, b, SHIFT);
        prop_assert_eq!(&bench.editor.selection, &range(c, b), "Shift+click after a Ctrl+click ranges from it");
        // With no clip clicked before, Shift+click selects the clip alone.
        let mut fresh = Bench::new(&edit, false);
        click(&mut fresh, &mut hand, b, SHIFT);
        prop_assert_eq!(&fresh.editor.selection, &BTreeSet::from([b]));
    }

    /// A press on the empty frames before a clip selects that gap (and no
    /// clip) and parks the playhead there; Delete then closes it: that
    /// clip and every clip after it on its track move earlier by the gap,
    /// nothing else moves, on any track, and it is one undo step. The
    /// right-click menu's Close Gap does the same. A press after a
    /// track's last clip selects no gap, and Delete then does nothing.
    #[test]
    fn a_gap_is_selected_by_a_click_and_closed_by_delete(
        edit in cut(), stage in hand(), pick in any::<Index>(), by_menu in any::<bool>(), shift in any::<bool>(),
    ) {
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        let delete = |bench: &mut Bench| {
            let held = Modifiers { shift, ..Modifiers::NONE };
            let message = menu_bar::key_message(&bench.editor.menu_state(), Key::Named(Named::Delete), held).expect("Delete is a key");
            bench.send(message);
        };
        let row_of = |bench: &Bench, track: TrackRef| bench.editor.rows().iter().position(|r| !r.spare && r.track == track).unwrap();
        // After the last clip of the top video track: no gap.
        let top = TrackRef::video(edit.video.len() - 1);
        let past = edit.spans(top).last().map_or(3, |s| s.1 + 2);
        let at = hand.over(past, Hand::row_y(&bench.editor, row_of(&bench, top), 0.5));
        hand.press(&mut bench.editor, at);
        hand.release(&mut bench.editor, at);
        prop_assert_eq!(bench.editor.gap, None);
        delete(&mut bench);
        prop_assert_eq!(bench.edit(), &edit, "Delete with nothing selected changed the cut");

        let gaps = gap_frames(&edit);
        prop_assume!(!gaps.is_empty());
        let (track, frame, index) = *pick.get(&gaps);
        let clip = ClipRef { track, index };
        let at = hand.over(frame, Hand::row_y(&bench.editor, row_of(&bench, track), 0.5));
        if by_menu {
            hand.right_press(&mut bench.editor, at);
            let (clicked, on) = bench.editor.menu_empty.expect("the timeline's menu opened");
            let entries = timeline_menu::entries(&bench.editor.timeline_menu_state(clicked, on));
            let close: Vec<&(String, Message)> = entries.iter().filter(|(label, _)| label == "Close Gap").collect();
            prop_assert_eq!(close.len(), 1, "Close Gap is in the menu on a gap");
            prop_assert_eq!(format!("{:?}", close[0].1), format!("{:?}", Message::CloseGap(clip)));
            bench.send(Message::CloseGap(clip));
        } else {
            hand.press(&mut bench.editor, at);
            hand.release(&mut bench.editor, at);
            prop_assert_eq!(bench.editor.gap, Some(clip), "the gap under the press is selected");
            prop_assert!(bench.editor.selection.is_empty());
            prop_assert_eq!(bench.editor.position as EditFrame, frame.min(edit.length() - 1));
            prop_assert_eq!(bench.editor.tracks_program().gap, Some(clip), "the gap is drawn selected");
            delete(&mut bench);
        }
        prop_assert_eq!(bench.refusal(), None);
        prop_assert_eq!(bench.editor.gap, None, "the gap is closed, so not selected");
        let spans = edit.spans(track);
        let gap = spans[index].0 - index.checked_sub(1).map_or(0, |i| spans[i].1);
        prop_assert!(gap > 0);
        let expected: Vec<Known> =
            clips(&edit).into_iter().map(|c| if c.1 == track && c.2 >= spans[index].0 { (c.0, c.1, c.2 - gap, c.3) } else { c }).collect();
        prop_assert_eq!(clips(bench.edit()), expected);
        prop_assert_eq!(&bench.edit().markers, &edit.markers);
        bench.send(Message::Undo);
        prop_assert_eq!(bench.edit(), &edit, "closing a gap is one undo step");
        prop_assert!(!bench.editor.doc.can_undo());
    }

    /// A drag from empty track draws a box and selects, as it goes, the
    /// clips it touches: those of the rows from the press to the pointer
    /// with a frame in the frames between them. Letting go keeps them, and
    /// the cut is as it was.
    #[test]
    fn a_box_dragged_from_empty_track_selects_what_it_touches(
        edit in cut(), stage in hand(), from_row in any::<Index>(), to_row in any::<Index>(), frame in 0i64..400, to_frame in 0i64..400,
    ) {
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        let total = bench.editor.tracks_program().total as EditFrame;
        let (frame, to_frame) = (frame % total, to_frame % total);
        let rows = bench.editor.rows();
        let (r, t) = (from_row.index(rows.len()), to_row.index(rows.len()));
        prop_assume!(!rows[r].clips.iter().any(|c| c.at <= frame && frame < c.at + c.duration));
        prop_assume!(r != t || (frame - to_frame).abs() >= 2);
        let from = hand.over(frame, Hand::row_y(&bench.editor, r, 0.5));
        hand.press(&mut bench.editor, from);
        let to = hand.over(to_frame, Hand::row_y(&bench.editor, t, 0.5));
        hand.move_to(&mut bench.editor, to);
        let mut expected = BTreeSet::new();
        for row in &rows[r.min(t)..=r.max(t)] {
            for (index, c) in row.clips.iter().enumerate() {
                if (c.at..c.at + c.duration).any(|f| frame.min(to_frame) <= f && f <= frame.max(to_frame)) {
                    expected.insert(ClipRef { track: row.track, index });
                }
            }
        }
        prop_assert_eq!(&bench.editor.selection, &expected);
        hand.release(&mut bench.editor, to);
        prop_assert_eq!(&bench.editor.selection, &expected, "letting go keeps what the box selected");
        prop_assert_eq!(bench.edit(), &edit, "a box changed the cut");
    }
}
