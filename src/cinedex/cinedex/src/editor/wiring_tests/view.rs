//! Scrolling and zooming the timeline through the wheel, the keys and the
//! strip under the tracks, on a timeline 1000 pixels wide.

use super::*;
use crate::tracks::overview;

/// The tracks' width: the names' column and 1000 pixels of time.
const WIDE: f32 = HEADER + 1000.0;

/// Something done to the timeline's view.
#[derive(Debug, Clone)]
enum Turn {
    /// The wheel, `notches` away from the user, with Ctrl (Cmd) held and
    /// the pointer this far across the tracks' time.
    Zoom(f32, f32),
    /// The wheel with Shift held.
    ShiftWheel(f32),
    /// A sideways wheel, or a trackpad swiped sideways.
    Sideways(f32),
    /// The wheel alone.
    Wheel(f32),
    Fit,
    FitClips,
    /// The strip's thumb dragged this many pixels.
    Thumb(f32),
}

fn turn() -> impl Strategy<Value = Turn> {
    prop_oneof![
        4 => (-4f32..4.0, 0.0f32..1.0).prop_map(|(notches, across)| Turn::Zoom(notches, across)),
        2 => (-6f32..6.0).prop_map(Turn::ShiftWheel),
        2 => (-6f32..6.0).prop_map(Turn::Sideways),
        1 => (-6f32..6.0).prop_map(Turn::Wheel),
        1 => Just(Turn::Fit),
        1 => Just(Turn::FitClips),
        2 => (-400f32..400.0).prop_map(Turn::Thumb),
    ]
}

/// The wheel turned by `delta` pixels over the tracks at `x` with `held`
/// down: what the tracks sent, as it prints.
fn wheel(bench: &mut Bench, pointer: &mut Pointer, x: f32, delta: Vector, held: Modifiers) -> Option<String> {
    let program = bench.editor.tracks_program();
    let bounds = Rectangle { x: 0.0, y: 0.0, width: WIDE, height: program.height() };
    let at = Cursor::Available(Point::new(x, RULER + 10.0));
    program.update(pointer, &Event::Keyboard(keyboard::Event::ModifiersChanged(held)), bounds, at, Instant::now());
    let (status, message) = program.update(pointer, &Event::Mouse(mouse::Event::WheelScrolled { delta }), bounds, at, Instant::now());
    let said = message.as_ref().map(|m| format!("{m:?}"));
    assert_eq!(status == primer::widget::custom::Status::Captured, message.is_some(), "the wheel is taken exactly when it does something");
    if let Some(message) = message {
        bench.send(message);
    }
    said
}

fn key(bench: &mut Bench, key: Key, held: Modifiers) {
    let message = menu_bar::key_message(&bench.editor.menu_state(), key, held).expect("a key of the editor");
    bench.send(message);
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// Whatever the wheel, the keys and the strip's thumb do, one after
    /// another: Ctrl+wheel zooms and keeps the frame under the pointer
    /// where it is (unless an end of the timeline is reached); Shift+wheel
    /// and a sideways wheel scroll by the pixels turned, as far as the
    /// ends; the wheel alone is left to the tracks' up-and-down scroll; F
    /// shows the whole timeline and Shift+F the first clip to the last;
    /// the thumb scrolls by what it is dragged. What is shown is always
    /// inside the timeline, the cut never changes, and a press on a clip
    /// where it is drawn selects that clip.
    #[test]
    fn the_wheel_the_keys_and_the_strip_move_the_view(edit in cut(), turns in prop::collection::vec(turn(), 1..12), pick in any::<Index>()) {
        let mut bench = Bench::new(&edit, false);
        let mut pointer = Pointer::default();
        let span = edit.length().max(0) as usize + 24;
        for turn in &turns {
            let before = bench.editor.tracks_program();
            let (first, total) = (before.first, before.total as f64);
            let frames_a_pixel = total / 1000.0;
            let last = span as f64 - total;
            match *turn {
                Turn::Zoom(notches, across) => {
                    let x = HEADER + across * 1000.0;
                    let about = first + f64::from(across) * total;
                    let said = wheel(&mut bench, &mut pointer, x, Vector::new(0.0, notches * 16.0), Modifiers::COMMAND);
                    prop_assert!(said.is_some_and(|m| m.starts_with("TimelineView(Zoom")));
                    let after = bench.editor.tracks_program();
                    if notches > 0.0 {
                        prop_assert!(after.total <= before.total && (after.total < before.total || before.total <= 8.min(span)), "zooming in shows fewer frames");
                    } else if notches < 0.0 {
                        prop_assert!(after.total >= before.total && (after.total > before.total || before.total == span), "zooming out shows more");
                    }
                    let wanted = (total / 1.25f64.powf(f64::from(notches))).round();
                    prop_assert!((after.total as f64 - wanted.clamp(8.0f64.min(span as f64), span as f64)).abs() <= 1.0, "a notch is a quarter");
                    let across_now = (about - after.first) / after.total as f64;
                    let at_an_end = after.first == 0.0 || (after.first - (span - after.total) as f64).abs() < 1e-9;
                    prop_assert!((across_now - f64::from(across)).abs() < 1e-6 || at_an_end, "the frame under the pointer moved");
                }
                Turn::ShiftWheel(notches) | Turn::Sideways(notches) => {
                    let (delta, held) = match turn {
                        Turn::ShiftWheel(_) => (Vector::new(0.0, notches * 16.0), Modifiers { shift: true, ..Modifiers::NONE }),
                        _ => (Vector::new(notches * 16.0, notches * 3.0), Modifiers::NONE),
                    };
                    let said = wheel(&mut bench, &mut pointer, 300.0, delta, held);
                    prop_assert_eq!(said.is_some(), notches != 0.0);
                    let after = bench.editor.tracks_program();
                    prop_assert_eq!(after.total, before.total, "a scroll changed the scale");
                    let expected = (first - f64::from(notches * 16.0) * frames_a_pixel).clamp(0.0, last);
                    prop_assert!((after.first - expected).abs() < 1e-6, "scrolled to {} not {}", after.first, expected);
                }
                Turn::Wheel(notches) => {
                    let said = wheel(&mut bench, &mut pointer, 300.0, Vector::new(notches * 3.0, notches * 16.0), Modifiers::NONE);
                    prop_assert_eq!(said, None, "the wheel alone is the tracks' up and down");
                    let after = bench.editor.tracks_program();
                    prop_assert_eq!((after.first, after.total), (before.first, before.total));
                }
                Turn::Fit => {
                    key(&mut bench, Key::Character('f'), Modifiers::NONE);
                    let after = bench.editor.tracks_program();
                    prop_assert_eq!((after.first, after.total), (0.0, span));
                }
                Turn::FitClips => {
                    key(&mut bench, Key::Character('F'), Modifiers { shift: true, ..Modifiers::NONE });
                    let after = bench.editor.tracks_program();
                    let all = clips(&edit);
                    match (all.iter().map(|c| c.2).min(), all.iter().map(|c| c.2 + c.3).max()) {
                        (Some(from), Some(to)) => {
                            // At least 8 frames, which near the end start earlier.
                            let shown = ((to - from) as usize).max(8);
                            prop_assert_eq!((after.first, after.total), ((from as f64).min((span - shown) as f64), shown));
                        }
                        _ => prop_assert_eq!((after.first, after.total), (before.first, before.total)),
                    }
                }
                Turn::Thumb(dx) => {
                    let strip = bench.editor.overview();
                    prop_assert_eq!((strip.span, strip.first, strip.shown), (span, first, before.total));
                    let bounds = Rectangle { x: 0.0, y: 0.0, width: WIDE, height: overview::HEIGHT };
                    let (x0, x1) = overview::thumb(strip.span, strip.first, strip.shown, WIDE);
                    let mut held = overview::Held::default();
                    let from = Point::new((x0 + x1) / 2.0, 6.0);
                    let to = Point::new(from.x + dx, 6.0);
                    let events = [
                        (Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), from),
                        (Event::Mouse(mouse::Event::CursorMoved { position: to }), to),
                        (Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)), to),
                    ];
                    for (event, at) in events {
                        // The strip is made again after each message, as the window does.
                        let (_, message) = bench.editor.overview().update(&mut held, &event, bounds, Cursor::Available(at));
                        if let Some(message) = message {
                            bench.send(message);
                        }
                    }
                    let after = bench.editor.tracks_program();
                    prop_assert_eq!(after.total, before.total);
                    // The thumb is at least 12 pixels: narrower than that it
                    // no longer starts exactly where the view does.
                    if x1 - x0 > 12.5 {
                        let expected = (first + f64::from(dx) * span as f64 / 1000.0).clamp(0.0, last);
                        prop_assert!((after.first - expected).abs() <= span as f64 / 1000.0 * 0.01 + 1e-3, "the thumb scrolled to {} not {}", after.first, expected);
                    }
                }
            }
            let after = bench.editor.tracks_program();
            prop_assert!(after.first >= 0.0 && after.first + after.total as f64 <= span as f64 + 1e-9, "the view left the timeline");
            prop_assert!(after.total >= 1);
            prop_assert_eq!(bench.edit(), &edit, "the view changed the cut");
        }
        // A clip pressed where it is drawn is the clip selected.
        let program = bench.editor.tracks_program();
        let shown = clip_refs(&edit, &View::default().rows(&edit));
        let seen: Vec<ClipRef> = shown
            .into_iter()
            .filter(|c| {
                let (at, end) = edit.spans(c.track)[c.index];
                let middle = (at + end) as f64 / 2.0;
                program.first + 0.02 * program.total as f64 <= middle && middle <= program.first + 0.98 * program.total as f64
            })
            .collect();
        if !seen.is_empty() {
            let clip = *pick.get(&seen);
            let (at, end) = edit.spans(clip.track)[clip.index];
            let x = HEADER + (((at + end) as f64 / 2.0 - program.first) / program.total as f64 * 1000.0) as f32;
            let row = bench.editor.rows().iter().position(|r| !r.spare && r.track == clip.track).unwrap();
            let point = Point::new(x, Hand::row_y(&bench.editor, row, 0.5));
            let bounds = Rectangle { x: 0.0, y: 0.0, width: WIDE, height: program.height() };
            let mut pointer = Pointer::default();
            let (_, message) = program.update(&mut pointer, &Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)), bounds, Cursor::Available(point), Instant::now());
            bench.send(message.expect("a press on a clip selects it"));
            prop_assert_eq!(&bench.editor.selection, &BTreeSet::from([clip]));
        }
    }

    /// Both fits are in the View menu and in the timeline's right-click
    /// menu, and say what they do there.
    #[test]
    fn both_fits_are_in_the_menus(edit in cut()) {
        let bench = Bench::new(&edit, false);
        let entries = timeline_menu::entries(&bench.editor.timeline_menu_state(Some(0), None));
        let has = |label: &str, message: &Message| entries.iter().any(|(l, m)| l == label && format!("{m:?}") == format!("{message:?}"));
        prop_assert!(has("Fit Timeline", &Message::FitTimeline) && has("Fit to Clips", &Message::FitClips));
        let state = bench.editor.menu_state();
        let view: Vec<(String, String, bool)> = menu_bar::headings(&state)
            .into_iter()
            .filter(|heading| heading.label.contains("View"))
            .flat_map(|heading| heading.entries)
            .filter_map(|entry| match entry {
                menu_bar::Entry::Item { label, message, enabled, .. } => Some((label.to_owned(), format!("{message:?}"), enabled)),
                menu_bar::Entry::Separator => None,
            })
            .collect();
        prop_assert!(view.contains(&("&Fit Timeline".to_owned(), "FitTimeline".to_owned(), true)), "{:?}", view);
        prop_assert!(view.contains(&("Fit to C&lips".to_owned(), "FitClips".to_owned(), true)), "{:?}", view);
    }
}
