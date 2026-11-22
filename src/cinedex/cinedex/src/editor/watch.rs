//! What the editor keeps watching: live playblasts, the change journal,
//! the bins and board, Houdini sessions; and opening a shot's workfile.

use std::collections::BTreeSet;

use primer::prelude::*;
use th_edit::document::Frame as EditFrame;
use th_media::FrameKey;
use th_pipe::{master, media};

use crate::editing::BinShot;
use crate::live::Change as LiveChange;
use crate::{board, editing};

use super::entries::{EntryState, resolve};
use super::{Editor, LIVE_HUSK_POLLS, Message, NO_DESKTOP, ProjectPaths};

/// The project's shots and media library, for the bins.
#[derive(Debug, Clone, Default)]
pub struct BinData {
    pub(super) shots: Vec<BinShot>,
    pub(super) media: Vec<editing::BinItem>,
    /// The project's master edit.
    pub(super) master: Option<String>,
    /// The frame a new shot starts on, by sequence
    /// ([`master::new_shot_start`]); `""` for a sequence not made yet.
    shot_starts: std::collections::BTreeMap<String, EditFrame>,
    /// The project lists its shots flat (`th_pipe::flat_entities`): its
    /// implicit sequence, where New shot puts every new shot. The Shots bin
    /// shows no sequence names, its shots by name
    /// ([`bins::bin_shot_rows`](crate::bins::bin_shot_rows)).
    pub(super) flat_shots: Option<String>,
    /// Every shot entity's URI segments (`["shots", seq, name]`), with a
    /// frame range or not: what a flat project's new shot may not be named
    /// (`flat_entities::name_taken`).
    pub(super) shot_paths: Vec<Vec<String>>,
}

impl BinData {
    /// The frame a new shot of `sequence` starts on.
    pub(super) fn shot_start(&self, sequence: &str) -> EditFrame {
        self.shot_starts.get(sequence).or_else(|| self.shot_starts.get("")).copied().unwrap_or(th_edit::document::DEFAULT_START_FRAME)
    }
}

pub(super) fn load_bins(paths: &ProjectPaths, fps: u32) -> Result<BinData, String> {
    let project = paths.open()?;
    let mut shots = editing::project_shots(&project)?;
    let flat_shots = th_pipe::flat_entities::flat_bucket_in(&project.config, "shots")?;
    if flat_shots.is_some() {
        crate::bins::flat_order(&mut shots);
    }
    let all = th_pipe::Uri::parse("entity:/shots").map_err(|e| e.to_string())?;
    let shot_paths = project.config.list_entity_uris(&all, true).map_err(|e| e.to_string())?.into_iter().map(|u| u.segments).collect();
    let mut items = Vec::new();
    for item in media::list_items(&project)? {
        if let Some(last) = media::item_versions(&project, &item)?.pop() {
            items.push(editing::BinItem::of(&item, &last, fps));
        }
    }
    let mut shot_starts = std::collections::BTreeMap::new();
    shot_starts.insert(String::new(), master::new_shot_start(&project, "")?);
    for sequence in shots.iter().map(|s| s.sequence.clone()).chain(flat_shots.clone()).collect::<BTreeSet<_>>() {
        let start = master::new_shot_start(&project, &sequence)?;
        shot_starts.insert(sequence, start);
    }
    Ok(BinData { shots, media: items, master: master::master_edit(&project)?, shot_starts, flat_shots, shot_paths })
}

impl Editor {
    /// Follow the shots' live playblasts: frames a render wrote since are
    /// read again, and a shot whose live render changed otherwise (a new
    /// run, another department's, gone, or newly there) is resolved again,
    /// playing on as it was meanwhile.
    pub(super) fn poll_live(&mut self) -> Command<Message> {
        self.live_polls += 1;
        let husk = self.live_polls.is_multiple_of(LIVE_HUSK_POLLS);
        let shown: Vec<FrameKey> = self.engine.layers(self.position).iter().map(|l| l.key).collect();
        let (fps, live, show) = (self.fps(), self.show_live, self.show);
        let mut commands = Vec::new();
        for index in 0..self.entries.len() {
            let entry = &mut self.entries[index];
            let EntryState::Ready { resolved, .. } = &mut entry.state else { continue };
            if entry.refreshing {
                continue;
            }
            match resolved.live.poll(husk) {
                LiveChange::None | LiveChange::Label => {}
                LiveChange::Frames(indices) => {
                    let source = index + 1;
                    self.reshow |= shown.iter().any(|k| k.source == source && indices.contains(&k.index));
                    self.engine.forget_frames(source, &indices);
                }
                LiveChange::Resolve => {
                    entry.refreshing = true;
                    let (paths, source) = (self.paths.clone(), entry.source.clone());
                    commands.push(Command::perform_blocking(
                        move || resolve(&paths, &source, fps, live, show),
                        move |r| Message::Resolved(index, r),
                    ));
                }
            }
        }
        Command::batch(commands)
    }

    /// Follow the change journal: a playblast, render or live run of a shot
    /// resolves that shot again, playing on as it was meanwhile; a config
    /// write reads the bins (the master edit box, the shots) and board
    /// again, and of the shots' purpose resolves every shot again; a line
    /// that may have been lost does all of it (`journal::plan`).
    pub(super) fn poll_journal(&mut self) -> Command<Message> {
        let changes = self.shared_journal.local(self.journal.poll());
        let shared = match self.shared_journal.due() {
            Some(poll) => Command::perform_blocking(poll, Message::SharedJournal),
            None => Command::none(),
        };
        Command::batch([shared, self.apply_journal(changes)])
    }

    /// Do what `changes` of either journal ask (`journal::plan`).
    pub(super) fn apply_journal(&mut self, changes: Option<Vec<th_pipe::events::Change>>) -> Command<Message> {
        if changes.as_ref().is_some_and(Vec::is_empty) {
            return Command::none();
        }
        self.bin_thumbs_changed(changes.as_deref());
        let shots = self.shot_entries();
        let plan = crate::journal::plan(changes.as_deref(), &self.journal_key, std::process::id(), &shots, self.show_live);
        let indices: Vec<usize> = if plan.all { shots.iter().map(|(i, _)| *i).collect() } else { plan.entries.into_iter().collect() };
        let mut commands = vec![self.refresh_entries(indices)];
        if plan.bins {
            commands.push(self.refresh_bins());
        } else if plan.board && self.mode == 1 {
            commands.push(self.refresh_board());
        }
        Command::batch(commands)
    }

    pub(super) fn refresh_bins(&self) -> Command<Message> {
        let paths = self.paths.clone();
        let fps = self.fps();
        Command::perform_blocking(move || load_bins(&paths, fps), Message::Bins)
    }

    /// Read the board again (every shot's workfiles), unless a read is
    /// under way.
    pub(super) fn refresh_board(&mut self) -> Command<Message> {
        if self.board_loading {
            return Command::none();
        }
        self.board_loading = true;
        let paths = self.paths.clone();
        let shots = self.bins.shots.clone();
        Command::perform_blocking(move || board::load(&paths.open()?, &shots), Message::Board)
    }

    /// Ask Desktop for the project's Houdini sessions, unless it is being
    /// asked already or there is no link to it.
    pub(super) fn poll_sessions(&mut self) -> Command<Message> {
        let Some(desktop) = self.desktop.clone() else { return Command::none() };
        if self.sessions_polling {
            return Command::none();
        }
        self.sessions_polling = true;
        Command::perform_blocking(move || desktop.sessions(), Message::Sessions)
    }

    /// Open `shot`'s `department` workfile in Houdini: Desktop raises the
    /// session that has it open, else the Houdini that started this editor
    /// loads it, else Desktop launches one on it
    /// (`th_pipe::houdini_link::open_workfile`).
    pub(super) fn open_workfile(&mut self, shot: &str, department: &str) -> Command<Message> {
        let Some(workfile) = self.board.as_ref().and_then(|b| b.workfile(shot, department)) else {
            self.status = Some((format!("{department} has no workfile yet: it is made in Houdini, from the Asset Browser"), true));
            return Command::none();
        };
        if self.desktop.is_none() && self.houdini.is_none() {
            self.status = Some((NO_DESKTOP.into(), true));
            return Command::none();
        }
        let (desktop, houdini) = (self.desktop.clone(), self.houdini.clone());
        let (shot, department) = (shot.to_string(), department.to_string());
        let path = workfile.path.clone();
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        self.status = Some((format!("Opening {name}…"), false));
        Command::perform_blocking(
            move || {
                th_pipe::houdini_link::open_workfile(desktop.as_ref(), houdini.as_ref(), &path, &shot, &department)
                    .map(|opened| (name, opened))
            },
            Message::Opened,
        )
    }
}
