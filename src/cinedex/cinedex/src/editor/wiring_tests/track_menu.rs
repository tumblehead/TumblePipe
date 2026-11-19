//! The track rows of the timeline's menu, through a right press on a
//! track's name: Move Up and Down, Rename, the colours, Delete, and Add
//! once names are out of order, with undo and redo between them. The cut
//! is compared with a model that knows a track only as its name, its
//! colour and its clips, and the rows with a [`View`] whose heights and
//! solo are moved by the model when tracks change places.

use super::*;

/// A track as the model has it: its name, its colour's name, and each
/// clip's name, start and length.
#[derive(Debug, Clone, PartialEq)]
struct Tr {
    name: String,
    color: Option<String>,
    clips: Vec<(String, EditFrame, EditFrame)>,
}

/// The tracks of a cut, bottom first.
#[derive(Debug, Clone, PartialEq)]
struct Cut {
    video: Vec<Tr>,
    audio: Vec<Tr>,
}

impl Cut {
    fn of(edit: &WorkingEdit) -> Self {
        let all = clips(edit);
        let track = |track: TrackRef| Tr {
            name: edit.track_name(track).to_owned(),
            color: edit.track_color(track).map(str::to_owned),
            clips: all.iter().filter(|c| c.1 == track).map(|c| (c.0.clone(), c.2, c.3)).collect(),
        };
        Cut {
            video: (0..edit.video.len()).map(|i| track(TrackRef::video(i))).collect(),
            audio: (0..edit.audio.len()).map(|i| track(TrackRef::audio(i))).collect(),
        }
    }

    fn kind(&mut self, kind: TrackKind) -> &mut Vec<Tr> {
        match kind {
            TrackKind::Video => &mut self.video,
            TrackKind::Audio => &mut self.audio,
        }
    }
}

/// The cut, what undo and redo hold, and the view.
struct Model {
    cut: Cut,
    past: Vec<Cut>,
    future: Vec<Cut>,
    view: View,
}

impl Model {
    /// A change of the cut: one step of undo.
    fn step(&mut self, next: Cut) {
        let was = std::mem::replace(&mut self.cut, next);
        self.past.push(was.clone());
        self.future.clear();
        self.follow(&was);
    }

    /// The view after the cut changed from `was`: when a kind's tracks
    /// are the same tracks (told by their names, which no two share) in
    /// another order, each track's height and the solo go with it. A solo
    /// on a track that is no more is dropped.
    fn follow(&mut self, was: &Cut) {
        for (video, before, after) in [(true, &was.video, &self.cut.video), (false, &was.audio, &self.cut.audio)] {
            let to: Vec<Option<usize>> = before.iter().map(|t| after.iter().position(|a| a == t)).collect();
            if before.len() != after.len() || to.iter().any(Option::is_none) || before == after {
                continue;
            }
            let old = self.view.heights.clone();
            let height = |index: usize| old.get(&(video, index)).copied().unwrap_or(46.0);
            for (from, now) in to.iter().enumerate() {
                self.view.heights.insert((video, now.unwrap()), height(from));
            }
            if let Some(solo) = self.view.solo.filter(|t| (t.kind == TrackKind::Video) == video) {
                self.view.solo = Some(TrackRef { index: to[solo.index].unwrap(), ..solo });
            }
        }
        let (video, audio) = (self.cut.video.len(), self.cut.audio.len());
        self.view.solo = self.view.solo.filter(|t| t.index < if t.kind == TrackKind::Video { video } else { audio });
    }
}

#[derive(Debug, Clone)]
enum Step {
    Up(Index),
    Down(Index),
    /// The name typed, of [`NAMES`].
    Rename(Index, usize),
    Colour(Index, Index),
    Delete(Index),
    Add(Index, bool),
    Undo,
    Redo,
    Height(Index, f32),
    Solo(Index),
}

/// Names to type: free ones, ones tracks have, none, and ones in spaces.
const NAMES: [&str; 9] = ["Plates", "BG", "V1", "V2", "V3", "A1", "", " Hero ", "  "];

fn step() -> impl Strategy<Value = Step> {
    let i = any::<Index>;
    prop_oneof![
        3 => i().prop_map(Step::Up),
        3 => i().prop_map(Step::Down),
        3 => (i(), 0..NAMES.len()).prop_map(|(t, n)| Step::Rename(t, n)),
        3 => (i(), i()).prop_map(|(t, c)| Step::Colour(t, c)),
        2 => i().prop_map(Step::Delete),
        1 => (i(), any::<bool>()).prop_map(|(t, video)| Step::Add(t, video)),
        3 => Just(Step::Undo),
        2 => Just(Step::Redo),
        2 => (i(), 24f32..200.0).prop_map(|(t, h)| Step::Height(t, h)),
        1 => i().prop_map(Step::Solo),
    ]
}

/// Right-press the name of the `pick`ed row that is a track; the track,
/// and the menu's rows and colours for it.
#[allow(clippy::type_complexity)]
fn menu_on(
    bench: &mut Bench,
    hand: &mut Hand,
    pick: Index,
) -> Result<(TrackRef, Vec<(String, Message)>, Option<(String, Vec<(String, Message)>)>), TestCaseError> {
    let rows = bench.editor.rows();
    let tracks: Vec<usize> = rows.iter().enumerate().filter(|(_, r)| !r.spare).map(|(i, _)| i).collect();
    let row = *pick.get(&tracks);
    let track = rows[row].track;
    let point = hand.at(0.4 * HEADER, Hand::row_y(&bench.editor, row, 0.4));
    let said = hand.right_press(&mut bench.editor, point);
    prop_assert_eq!(said, Some(format!("{:?}", Message::TimelineMenu(None, Some(track)))));
    let (clicked, on) = bench.editor.menu_empty.expect("the menu is for that press");
    let state = bench.editor.timeline_menu_state(clicked, on);
    Ok((track, timeline_menu::entries(&state), timeline_menu::colours(&state)))
}

fn row_labelled(entries: &[(String, Message)], label: &str) -> Option<Message> {
    entries.iter().find(|(l, _)| l == label).map(|(_, m)| m.clone())
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(CASES))]

    /// Whatever is done to the tracks through their menu, with undo and
    /// redo between: Move Up and Move Down are offered exactly when a
    /// track of its kind is drawn that side, and the two change places;
    /// Rename takes a name that is free (the spaces around it dropped) and
    /// keeps its dialog open on one that is not; a colour row gives the
    /// track the colour it names; the row that takes a track away says
    /// how many clips go with it, is never offered for the last track of
    /// its kind, and takes that track alone; Add puts an empty track on
    /// top under a free name, whatever names are taken. Each is one step
    /// of undo. No clip of another track changes, and none changes its
    /// time. The rows are the cut's, each with its track's colour, and a
    /// row's height and the solo stay with their track when tracks change
    /// places, through undo and redo too.
    #[test]
    fn the_track_rows_do_what_they_say(edit in cut(), steps in prop::collection::vec(step(), 1..14), stage in hand()) {
        let mut bench = Bench::new(&edit, false);
        let mut hand = Hand::new(stage);
        let mut model = Model { cut: Cut::of(&edit), past: Vec::new(), future: Vec::new(), view: View::default() };
        for step in &steps {
            let before = model.cut.clone();
            match step {
                Step::Up(pick) | Step::Down(pick) => {
                    let up = matches!(step, Step::Up(_));
                    let (track, entries, _) = menu_on(&mut bench, &mut hand, *pick)?;
                    let count = model.cut.kind(track.kind).len();
                    // Video rows are drawn top track first, audio rows first track first.
                    let higher = up == (track.kind == TrackKind::Video);
                    let beside = if higher { Some(track.index + 1).filter(|&i| i < count) } else { track.index.checked_sub(1) };
                    let label = format!("Move Track {} {}", before.clone().kind(track.kind)[track.index].name, if up { "Up" } else { "Down" });
                    let message = row_labelled(&entries, &label);
                    prop_assert_eq!(message.is_some(), beside.is_some(), "{} in {:?}", label, entries);
                    if let (Some(message), Some(beside)) = (message, beside) {
                        bench.send(message);
                        let mut next = before.clone();
                        next.kind(track.kind).swap(track.index, beside);
                        model.step(next);
                    }
                }
                Step::Rename(pick, typed) => {
                    let (track, entries, _) = menu_on(&mut bench, &mut hand, *pick)?;
                    let name = before.clone().kind(track.kind)[track.index].name.clone();
                    let message = row_labelled(&entries, &format!("Rename Track {name}\u{2026}")).expect("Rename is on every track's row");
                    bench.send(message.clone());
                    let shown = match &bench.editor.dialog {
                        Some(Dialog::RenameTrack { name, .. }) => Some(name.clone()),
                        _ => None,
                    };
                    prop_assert_eq!(shown, Some(name.clone()), "the dialog opens on the name it has");
                    // Confirmed as it opened, it closes and is no step:
                    // nothing new to undo, and what could be redone still can.
                    let steps = (bench.editor.doc.can_undo(), bench.editor.doc.can_redo());
                    bench.send(Message::Confirm);
                    prop_assert!(bench.editor.dialog.is_none());
                    prop_assert_eq!((bench.editor.doc.can_undo(), bench.editor.doc.can_redo()), steps, "the same name is no step");
                    bench.send(message.clone());
                    bench.send(Message::Text(Field::Name, NAMES[*typed].to_owned()));
                    bench.send(Message::Confirm);
                    let bare: String = NAMES[*typed].chars().filter(|c| *c != ' ').collect();
                    let taken = before.clone().kind(track.kind).iter().enumerate().any(|(i, t)| i != track.index && t.name == bare);
                    if bare.is_empty() || taken {
                        prop_assert!(matches!(bench.editor.dialog, Some(Dialog::RenameTrack { tried: true, .. })), "a name that cannot be closed the dialog");
                        bench.send(Message::Dismiss);
                    } else if bare != name {
                        let mut next = before.clone();
                        next.kind(track.kind)[track.index].name = bare;
                        model.step(next);
                    }
                    prop_assert!(bench.editor.dialog.is_none());
                }
                Step::Colour(pick, choice) => {
                    let (track, _, colours) = menu_on(&mut bench, &mut hand, *pick)?;
                    let (title, rows) = colours.expect("a track's row has its colours");
                    let has = before.clone().kind(track.kind)[track.index].color.clone();
                    prop_assert!(title.to_lowercase().ends_with(&format!("({})", has.clone().unwrap_or_else(|| "none".to_owned()))), "{}", title);
                    let (label, message) = choice.get(&rows).clone();
                    let color = (label != "None").then(|| label.to_lowercase());
                    prop_assert!(color != has, "{} is offered, and is the colour it has", label);
                    bench.send(message);
                    let mut next = before.clone();
                    next.kind(track.kind)[track.index].color = color;
                    model.step(next);
                }
                Step::Delete(pick) => {
                    let (track, entries, _) = menu_on(&mut bench, &mut hand, *pick)?;
                    let gone = before.clone().kind(track.kind)[track.index].clone();
                    let label = match gone.clips.len() {
                        0 => format!("Remove Track {}", gone.name),
                        1 => format!("Delete Track {} and 1 Clip", gone.name),
                        n => format!("Delete Track {} and {n} Clips", gone.name),
                    };
                    let message = row_labelled(&entries, &label);
                    let last = before.clone().kind(track.kind).len() == 1;
                    prop_assert_eq!(message.is_some(), !last, "{} in {:?}", label, entries);
                    prop_assert_eq!(entries.iter().filter(|(l, _)| l.starts_with("Remove Track") || l.starts_with("Delete Track")).count(), usize::from(!last));
                    if let Some(message) = message {
                        bench.send(message);
                        let mut next = before.clone();
                        next.kind(track.kind).remove(track.index);
                        model.step(next);
                    }
                }
                Step::Add(pick, video) => {
                    let (_, entries, _) = menu_on(&mut bench, &mut hand, *pick)?;
                    let kind = if *video { TrackKind::Video } else { TrackKind::Audio };
                    let label = if *video { "Add Video Track" } else { "Add Audio Track" };
                    bench.send(row_labelled(&entries, label).expect("Add is always there"));
                    prop_assert_eq!(bench.refusal(), None, "{} was refused", label);
                    let now = Cut::of(bench.edit());
                    let mut next = before.clone();
                    let added = now.clone().kind(kind).last().cloned().expect("a track was added");
                    prop_assert!(added.clips.is_empty() && added.color.is_none());
                    prop_assert!(added.name.starts_with(if *video { 'V' } else { 'A' }) && added.name[1..].parse::<usize>().is_ok(), "{}", added.name);
                    prop_assert!(!before.clone().kind(kind).iter().any(|t| t.name == added.name), "{} is taken", added.name);
                    next.kind(kind).push(added);
                    model.step(next);
                }
                Step::Undo => {
                    bench.send(Message::Undo);
                    if let Some(earlier) = model.past.pop() {
                        let was = std::mem::replace(&mut model.cut, earlier);
                        model.future.push(was.clone());
                        model.follow(&was);
                    }
                }
                Step::Redo => {
                    bench.send(Message::Redo);
                    if let Some(later) = model.future.pop() {
                        let was = std::mem::replace(&mut model.cut, later);
                        model.past.push(was.clone());
                        model.follow(&was);
                    }
                }
                Step::Height(pick, height) => {
                    let shown = model.view.rows(bench.edit());
                    let track = *pick.get(&shown);
                    bench.send(Message::RowHeight(track, *height));
                    model.view.heights.insert((track.kind == TrackKind::Video, track.index), height.round().clamp(24.0, 240.0));
                }
                Step::Solo(pick) => {
                    let all: Vec<TrackRef> = bench.edit().tracks().collect();
                    let track = *pick.get(&all);
                    bench.send(Message::Solo(track));
                    model.view.solo = if model.view.solo == Some(track) { None } else { Some(track) };
                }
            }
            prop_assert_eq!(bench.refusal(), None, "after {:?}", step);
            prop_assert_eq!(&Cut::of(bench.edit()), &model.cut, "after {:?}", step);
            prop_assert_eq!(bench.edit().problem(), None);
            check_rows(&bench, &model.view, true)?;
            for row in bench.editor.rows().iter().filter(|r| !r.spare) {
                let name = model.cut.clone().kind(row.track.kind)[row.track.index].color.clone();
                prop_assert_eq!(row.color, name.as_deref().and_then(crate::look::track), "{:?}'s colour after {:?}", row.track, step);
            }
            // No clip changed its time or its length, whatever its track did.
            let timed = |cut: &Cut| -> BTreeSet<(String, EditFrame, EditFrame)> {
                cut.video.iter().chain(&cut.audio).flat_map(|t| t.clips.iter().cloned()).collect()
            };
            let (was, now) = (timed(&before), timed(&model.cut));
            prop_assert!(now.is_subset(&was) || was.is_subset(&now), "a clip changed after {:?}", step);
        }
    }
}
