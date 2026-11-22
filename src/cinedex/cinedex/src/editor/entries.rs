//! Each source of the cut resolved: the takes it has, the one its rule
//! picks, opened for the engine; and kept so as the project changes.

use std::sync::Arc;

use primer::prelude::*;
use serde_json::json;
use th_edit::document::{Frame as EditFrame, Source, TakeKind};
use th_edit::ops::{Available, Sources as _};
use th_media::sound::Sound;
use th_media::{ExrView, Media};
use th_pipe::edits::EditSources;
use th_playlist::Take;

use crate::colour::Lut;
use crate::display::{open_live_over, open_media};
use crate::editing::Bound;
use crate::engine::{Content, Timeline};
use crate::live::LiveWatch;
use crate::{editing, take_menu};

use super::{Editor, Message, ProjectPaths, source_name};

/// A source resolved: the takes it has, the one its rule picks, opened.
#[derive(Debug, Clone)]
pub struct Resolved {
    pub(super) takes: Vec<Take>,
    pub(super) take: Option<usize>,
    /// What plays; None for sound alone, or nothing (see `nothing`).
    pub(crate) media: Option<Arc<Media>>,
    pub(crate) sound: Option<Arc<Sound>>,
    /// The frame number of the media's index 0.
    pub(crate) first: EditFrame,
    /// Why nothing plays, when nothing does (a slug, a planned shot, no
    /// take: `missing`), in the few words a clip has room for.
    pub(super) nothing: Option<String>,
    /// The same in a sentence, with what to do about it, when there is
    /// more to say than `nothing` does.
    pub(super) explained: Option<String>,
    /// The shot's live playblasts on this machine: when one is going it
    /// plays over the take its rule picks (never published). Boxed: most
    /// sources have none, and an entry is moved about.
    pub(super) live: Box<LiveWatch>,
    /// The take that shows where the live playblast has not rendered yet
    /// (its label), when one does.
    pub(super) under: Option<String>,
    /// The kind View > Show asked for (#57) when the shot has no take of
    /// it, so the clip shows its cut's take: its name says so.
    pub(super) lacks: Option<TakeKind>,
    /// The frames the source has, as the document judges an op by
    /// (`th_edit::ops::Sources`): how far a clip of it trims out
    /// (`tracks::room`).
    pub(super) available: Option<Available>,
}

impl Resolved {
    /// What a burn-in says of it: the take it shows, or why nothing plays
    /// (`Entry::detail` of a source with no live playblast over it).
    pub(crate) fn take_label(&self) -> String {
        match (&self.nothing, self.take) {
            (Some(why), _) => why.clone(),
            (None, Some(t)) => self.takes[t].label.clone(),
            (None, None) => String::new(),
        }
    }

    pub(super) fn nothing(why: impl Into<String>) -> Self {
        Resolved {
            takes: Vec::new(),
            take: None,
            media: None,
            sound: None,
            first: 0,
            nothing: Some(why.into()),
            explained: None,
            live: Box::default(),
            under: None,
            lacks: None,
            // A slug and a planned shot have any frame.
            available: Some(Available::Any),
        }
    }
}

/// What View > Show plays, as the agent and the events say it (#57).
pub(crate) fn show_word(show: Option<TakeKind>) -> &'static str {
    match show {
        None => "cut",
        Some(TakeKind::Render) => "renders",
        Some(TakeKind::Playblast) => "playblasts",
    }
}

/// What a clip's take says after it when View > Show asked for a kind the
/// shot has none of (#57).
fn lacks_note(lacks: Option<TakeKind>) -> &'static str {
    match lacks {
        None => "",
        Some(TakeKind::Render) => "  (no render)",
        Some(TakeKind::Playblast) => "  (no playblast)",
    }
}

/// What a source's rule picks, opened; with `live`, a shot's live playblast
/// on this machine in its place while there is one. `show` is View > Show
/// (#57): a shot's take of that kind in place of its rule's pick (and no
/// live playblast over a render); None, the cut. Runs off the UI thread:
/// listing takes and opening media take seconds on a file server.
pub(crate) fn resolve(paths: &ProjectPaths, source: &Source, fps: u32, live: bool, show: Option<TakeKind>) -> Result<Resolved, String> {
    match source {
        Source::Slug { text } => Ok(Resolved::nothing(text.clone())),
        Source::Shot { shot, take: rule } => {
            let project = paths.open()?;
            let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
            if !project.config.exists(&uri).map_err(|e| e.to_string())? {
                return Ok(Resolved {
                    explained: Some(
                        "A planned shot: it is made, with this length, when the master edit is published. Until then it has nothing to play."
                            .into(),
                    ),
                    ..Resolved::nothing("Planned: made when the edit is published")
                });
            }
            let available = Some(EditSources::new(&project, fps).available(source));
            let takes = EditSources::new(&project, fps).takes(shot)?;
            let (picked, none) = th_edit::takes::pick_shown(&takes, rule, show);
            let lacks = show.filter(|_| none);
            let (watch, live_take) = if live && show != Some(TakeKind::Render) {
                crate::live::watch(&project, &uri, &th_pipe::live::this_live_root())?
            } else {
                (LiveWatch::default(), None)
            };
            let Some(chosen) = live_take.as_ref().or(picked.map(|t| &takes[t])) else {
                // Say which of the few reasons it is (`missing`); a render
                // on its way is looked for only now, for a shot with nothing.
                let unfinished = crate::missing::unfinished_renders(&project, &uri);
                let (label, explained) = match crate::missing::why(&takes, rule) {
                    Some(reason) => (reason.label(unfinished), Some(reason.explain(unfinished))),
                    None => ("Nothing to play".into(), None),
                };
                return Ok(Resolved { takes, live: Box::new(watch), explained, available, ..Resolved::nothing(label) });
            };
            let open = |media: &th_playlist::Media| open_media(media, rate_of(fps)).map_err(|e| crate::missing::open_failure(media, e));
            let first = th_pipe::timeline::shot_first_frame(&project.config, &uri)?;
            let rate = (fps as i32, 1);
            // A live playblast shows the department's latest playblast where
            // it has not rendered yet, and plays that take's sound.
            let under = live_take.as_ref().and_then(|l| th_edit::takes::under_live(&takes, &l.department, picked));
            let (opened, under) = match (&live_take, under) {
                (Some(l), Some(u)) => match open_live_over(&l.media, &takes[u].media, rate, first) {
                    Ok(opened) => (opened, Some(u)),
                    Err(e) => {
                        eprintln!("cinedex: the take under the live playblast: {e}");
                        (open(&chosen.media)?, None)
                    }
                },
                _ => (open(&chosen.media)?, None),
            };
            let heard = under.map_or(&chosen.media, |u| &takes[u].media);
            let sound = match heard {
                th_playlist::Media::Movie { path } => Sound::read(path).unwrap_or_else(|e| {
                    eprintln!("cinedex: {e}");
                    None
                }),
                _ => None,
            };
            let first = opened.frame_number(0).unwrap_or(first);
            let under = under.map(|u| takes[u].label.clone());
            Ok(Resolved {
                takes,
                take: picked,
                media: Some(opened),
                sound: sound.map(Arc::new),
                first,
                nothing: None,
                explained: None,
                live: Box::new(watch),
                under,
                lacks,
                available,
            })
        }
        Source::Media { item, version } => {
            let project = paths.open()?;
            let found = EditSources::new(&project, fps).item(item, version)?;
            let available = Some(EditSources::new(&project, fps).available(source));
            let path = th_pipe::storage::slashed(&found.path);
            let take = Take {
                label: format!("{item} {}", found.version),
                department: "media".into(),
                version: found.version.clone(),
                media: th_playlist::Media::Movie { path: path.clone() },
                multi_range: false,
                check: false,
            };
            let opened = match found.measure.frames {
                Some(_) => Some(Media::movie(&path).map(Arc::new).map_err(|e| e.to_string())?),
                None => None,
            };
            let sound = if found.measure.sound { Sound::read(&path).map_err(|e| e.to_string())?.map(Arc::new) } else { None };
            Ok(Resolved {
                takes: vec![take],
                take: Some(0),
                media: opened,
                sound,
                first: 0,
                nothing: None,
                explained: None,
                live: Box::default(),
                under: None,
                lacks: None,
                available,
            })
        }
    }
}

/// A project rate as media take it.
fn rate_of(fps: u32) -> (i32, i32) {
    (fps as i32, 1)
}

/// A source of the edit, as far as the editor has it.
pub(super) struct Entry {
    pub(super) source: Source,
    pub(super) state: EntryState,
    /// Being resolved again in the background (its live playblast changed),
    /// while it plays on as it was.
    pub(super) refreshing: bool,
    /// Its media changed while it was being resolved (a journal line came
    /// meanwhile): resolve it again once that resolve is in.
    pub(super) again: bool,
}

// Nearly every entry is Ready, and there is one a source of the cut: boxing
// it would save nothing.
#[allow(clippy::large_enum_variant)]
pub(super) enum EntryState {
    Resolving,
    /// `view` and `hidden` are the viewer's AOV and stack layer choices
    /// (#58, `layer_view`): view only, never saved.
    Ready {
        resolved: Resolved,
        view: ExrView,
        hidden: u64,
        lut: Result<Option<Arc<Lut>>, String>,
    },
    Failed(String),
}

impl Entry {
    pub(super) fn resolved(&self) -> Option<&Resolved> {
        match &self.state {
            EntryState::Ready { resolved, .. } => Some(resolved),
            _ => None,
        }
    }

    /// What its clips say under their name: the take, or why nothing plays.
    pub(super) fn detail(&self) -> String {
        match &self.state {
            EntryState::Resolving => "Opening…".into(),
            EntryState::Failed(e) => format!("Cannot play: {e}"),
            EntryState::Ready { resolved, .. } => match (&resolved.nothing, resolved.take) {
                _ if resolved.nothing.is_none() && resolved.live.playing().is_some() => {
                    resolved.live.playing().map(|p| p.label_over(resolved.under.as_deref())).unwrap_or_default()
                }
                (Some(why), _) => why.clone(),
                (None, Some(t)) => format!("{}{}", resolved.takes[t].label, lacks_note(resolved.lacks)),
                (None, None) => String::new(),
            },
        }
    }

    /// [`Self::detail`] where there is room for a sentence (the viewer's
    /// card): why nothing plays and what to do about it, when it knows.
    pub(super) fn explain(&self) -> String {
        match self.resolved().filter(|r| r.live.playing().is_none()).and_then(|r| r.explained.clone()) {
            Some(explained) => explained,
            None => self.detail(),
        }
    }

    /// What its clips' names say after the shot (#58): the render layer
    /// shown and how many others there are (`take_menu::layer_note`); None
    /// while a live playblast plays over the take.
    pub(super) fn layer_note(&self) -> Option<String> {
        let (Source::Shot { take: rule, .. }, Some(resolved)) = (&self.source, self.resolved()) else { return None };
        if resolved.live.playing().is_some() {
            return None;
        }
        take_menu::layer_note(&resolved.takes, resolved.take?, rule)
    }

    /// The department of the take it shows, if it shows a shot's take.
    pub(super) fn department(&self) -> Option<String> {
        let resolved = self.resolved()?;
        matches!(self.source, Source::Shot { .. }).then(|| resolved.takes.get(resolved.take?).map(|t| t.department.clone()))?
    }

    pub(super) fn missing(&self) -> bool {
        match &self.state {
            EntryState::Failed(_) => true,
            EntryState::Ready { resolved, .. } => resolved.nothing.is_some() && !matches!(self.source, Source::Slug { .. }),
            EntryState::Resolving => false,
        }
    }
}

impl Editor {
    pub(super) fn entry_of(&self, source: &Source) -> Option<usize> {
        self.entries.iter().position(|e| &e.source == source)
    }

    /// The entry an engine source plays (engine source 0 plays none).
    pub(super) fn entry_of_engine(&self, source: usize) -> Option<&Entry> {
        source.checked_sub(1).and_then(|i| self.entries.get(i))
    }

    fn bind(&self, source: &Source) -> Option<Bound> {
        let i = self.entry_of(source)?;
        match &self.entries[i].state {
            EntryState::Ready { resolved, .. } if resolved.media.is_some() => Some(Bound {
                source: i + 1,
                first: resolved.first,
                ranges: resolved.media.as_deref().and_then(|m| m.frame_ranges()).cloned(),
            }),
            _ => None,
        }
    }

    pub(super) fn engine_timeline(&self) -> Timeline {
        Timeline::new(editing::engine_clips(&self.doc.edit, |s| self.bind(s)))
    }

    /// An entry for every source the edit names that has none, each
    /// resolving off the UI thread.
    pub(super) fn resolve_new(&mut self) -> Command<Message> {
        let mut sources: Vec<Source> = Vec::new();
        let edit = &self.doc.edit;
        for s in edit
            .video
            .iter()
            .flat_map(|t| t.clips.iter().map(|c| &c.source))
            .chain(edit.audio.iter().flat_map(|t| t.clips.iter().map(|c| &c.source)))
        {
            if self.entry_of(s).is_none() && !sources.contains(s) {
                sources.push(s.clone());
            }
        }
        let fps = self.fps();
        let mut commands = Vec::new();
        for source in sources {
            let index = self.entries.len();
            self.entries.push(Entry { source: source.clone(), state: EntryState::Resolving, refreshing: false, again: false });
            let timeline = Timeline::new(self.timeline.clips.clone());
            self.timeline = self.engine.replace_source(index + 1, Content::none(), timeline);
            let (paths, live, show) = (self.paths.clone(), self.show_live, self.show);
            commands
                .push(Command::perform_blocking(move || resolve(&paths, &source, fps, live, show), move |r| Message::Resolved(index, r)));
        }
        Command::batch(commands)
    }

    pub(super) fn resolved(&mut self, index: usize, result: Result<Resolved, String>) {
        let state = match result {
            Ok(resolved) => {
                let (view, hidden) = self.kept_choices(index, resolved.media.as_deref());
                let lut = match &resolved.media {
                    Some(m) => self.display.lut(m),
                    None => Ok(None),
                };
                EntryState::Ready { resolved, view, hidden, lut }
            }
            Err(e) => EntryState::Failed(e),
        };
        self.entries[index].state = state;
        self.entries[index].refreshing = false;
        let entry = &self.entries[index];
        self.events.push(
            "take_changed",
            json!({"name": source_name(&entry.source), "source": entry.source, "shows": entry.detail(), "missing": entry.missing()}),
        );
        let content = self.content_of(index);
        self.proxies.remove(&(index + 1));
        let timeline = self.engine_timeline();
        self.timeline = self.engine.replace_source(index + 1, content, timeline);
        self.engine.set_playhead(self.position);
        self.place_sounds();
        self.shown = None;
        self.refresh_shown();
    }

    /// The shot entries: index and shot URI as the edit writes it.
    pub(super) fn shot_entries(&self) -> Vec<(usize, String)> {
        self.entries
            .iter()
            .enumerate()
            .filter_map(|(i, e)| match &e.source {
                Source::Shot { shot, .. } => Some((i, shot.clone())),
                _ => None,
            })
            .collect()
    }

    /// Resolve `indices` again in the background, playing on meanwhile; one
    /// being resolved already is resolved again once that is in.
    pub(super) fn refresh_entries(&mut self, indices: Vec<usize>) -> Command<Message> {
        let mut commands = Vec::new();
        for index in indices {
            let entry = &mut self.entries[index];
            if entry.refreshing || matches!(entry.state, EntryState::Resolving) {
                entry.again = true;
            } else {
                commands.push(self.refresh_entry(index));
            }
        }
        Command::batch(commands)
    }

    /// Resolve every shot source again (their takes and ranges may have
    /// changed under them).
    pub(super) fn re_resolve_shots(&mut self) -> Command<Message> {
        let fps = self.fps();
        let mut commands = Vec::new();
        for (index, entry) in self.entries.iter_mut().enumerate() {
            if matches!(entry.source, Source::Shot { .. }) {
                entry.state = EntryState::Resolving;
                let (paths, source, live, show) = (self.paths.clone(), entry.source.clone(), self.show_live, self.show);
                commands.push(Command::perform_blocking(
                    move || resolve(&paths, &source, fps, live, show),
                    move |r| Message::Resolved(index, r),
                ));
            }
        }
        Command::batch(commands)
    }

    /// Keep the live playblast entry `index` plays as its shot's next
    /// playblast version and daily (encoded with the Houdini that rendered
    /// it; refused unless the render is done).
    pub(super) fn keep_live(&mut self, index: usize) -> Command<Message> {
        let Some(folder) = self.entries.get(index).and_then(Entry::resolved).and_then(|r| r.live.playing_folder()) else {
            self.status = Some(("This shot plays no live playblast".into(), true));
            return Command::none();
        };
        self.status = Some(("Keeping the live playblast…".into(), false));
        let paths = self.paths.clone();
        Command::perform_blocking(
            move || paths.open().and_then(|project| th_pipe::live::keep(&project, &folder)),
            move |r| Message::Kept(index, r),
        )
    }

    /// View > Show (#57): every shot entry resolved again to show `show`,
    /// playing on as it was meanwhile (one being resolved is resolved again
    /// once that is in, so none stays on the old choice).
    pub(super) fn set_show(&mut self, show: Option<TakeKind>) -> Command<Message> {
        if show == self.show {
            return Command::none();
        }
        self.show = show;
        self.events.push("show", json!({"show": show_word(show)}));
        let shots = self.shot_entries().into_iter().map(|(i, _)| i).collect();
        self.refresh_entries(shots)
    }

    /// Resolve entry `index` again in the background, playing on meanwhile.
    pub(super) fn refresh_entry(&mut self, index: usize) -> Command<Message> {
        let Some(entry) = self.entries.get_mut(index) else { return Command::none() };
        if entry.refreshing || matches!(entry.state, EntryState::Resolving) {
            return Command::none();
        }
        entry.refreshing = true;
        let (paths, source, fps, live, show) = (self.paths.clone(), entry.source.clone(), self.fps(), self.show_live, self.show);
        Command::perform_blocking(move || resolve(&paths, &source, fps, live, show), move |r| Message::Resolved(index, r))
    }
}
