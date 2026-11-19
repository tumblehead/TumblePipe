//! The bins' thumbnails: a small picture of each shot and media item.
//!
//! A shot's is the first frame of its best available take (what a
//! double-click puts in the cut), a media item's the first frame of its
//! newest version, scaled by `th_media::thumbnail` as the timeline's clips'
//! are. Finding a shot's takes and reading a frame are round trips to the
//! file server, so:
//!
//! - nothing is read on the UI thread: each thumbnail is a blocking job of
//!   its own ([`load`]), its answer a message;
//! - only what is on screen is read: the bin's list builds only the rows in
//!   view, and each row it builds says so ([`Seen`]); a row scrolled past
//!   and gone before its turn is never read;
//! - a few at a time ([`LIMIT`]), top row first ([`to_start`]), so a
//!   project of hundreds of shots on a slow share never has more than that
//!   many reads in flight, and the list scrolls freely meanwhile;
//! - once read it is kept for the session ([`Thumbs`]): scrolling back
//!   costs nothing. A shot is read again when the journal says its media
//!   changed, and everything on View > Refresh.
//!
//! A shot with nothing to play has no picture; its row says why
//! (`missing`), the same words its clip would.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use primer::ImageHandle;
use th_edit::document::{Rule, VersionRule};
use th_media::{Frame, Media, MovieDecoder, Proxy};
use th_pipe::edits::EditSources;

use crate::display::open_media;
use crate::editor::ProjectPaths;

/// Thumbnails being read at once: each is a listing of the shot's takes
/// and one frame off the file server.
pub const LIMIT: usize = 3;
/// The most pixels a thumbnail has each way: a grid tile at twice its
/// usual width, for a sharp picture on a scaled display.
pub const SIZE: (u32, u32) = (256, 144);
/// How long a row counts as on screen after the list last built it. The
/// list builds its visible rows whenever it draws; a row not built for this
/// long has been scrolled away.
const ON_SCREEN: Duration = Duration::from_secs(2);

/// A scene-linear colour space's way to the display (the OCIO display
/// transform, colour by colour), or why it has none.
pub type Shows = Result<Box<dyn Fn([f32; 3]) -> [f32; 3]>, String>;

/// What a thumbnail is of.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum ThumbKey {
    /// A shot, by its URI.
    Shot(String),
    /// A media item's version.
    Item { item: String, version: String },
}

/// The keys of `visible` (the rows on screen, top first) to start reading
/// now: those not `known` (read, being read, or failed), in that order, as
/// many as there is room for beside the `loading` already in flight under
/// `limit`.
pub fn to_start<K: Ord + Clone>(visible: &[K], known: impl Fn(&K) -> bool, loading: usize, limit: usize) -> Vec<K> {
    let mut out: Vec<K> = Vec::new();
    for key in visible {
        if out.len() + loading >= limit {
            break;
        }
        if !known(key) && !out.contains(key) {
            out.push(key.clone());
        }
    }
    out
}

/// The rows the bin's list has built lately: its row builder marks each
/// row it builds (the list builds only those in view), and the editor asks
/// which are on screen. Shared with the view, which cannot send messages.
#[derive(Debug, Clone, Default)]
pub struct Seen(Arc<Mutex<BTreeMap<ThumbKey, Instant>>>);

impl Seen {
    /// The list built `key`'s row at `now`.
    pub fn mark(&self, key: &ThumbKey, now: Instant) {
        if let Ok(mut seen) = self.0.lock() {
            match seen.get_mut(key) {
                Some(at) => *at = now,
                None => {
                    seen.insert(key.clone(), now);
                }
            }
        }
    }

    /// The keys among `order` (the bin's rows, top first) built within
    /// [`ON_SCREEN`] of `now`, in that order; rows not built since are
    /// forgotten.
    pub fn on_screen(&self, order: &[ThumbKey], now: Instant) -> Vec<ThumbKey> {
        let Ok(mut seen) = self.0.lock() else { return Vec::new() };
        seen.retain(|_, at| now.saturating_duration_since(*at) <= ON_SCREEN);
        order.iter().filter(|key| seen.contains_key(key)).cloned().collect()
    }
}

/// What [`load`] read: the picture, scaled, when there is one; the colour
/// space a scene-linear one is in; else why there is none.
#[derive(Debug, Clone)]
pub struct Loaded {
    pub proxy: Option<Proxy>,
    pub colour_space: Option<String>,
    pub note: Option<String>,
}

/// A thumbnail as far as it has got.
#[derive(Debug, Clone)]
enum Thumb {
    Loading,
    /// `linear` keeps a scene-linear picture and its colour space, to show
    /// it again when the display changes.
    Ready {
        handle: ImageHandle,
        linear: Option<(Proxy, String)>,
    },
    /// Nothing to show, and why (in the row's words).
    Nothing(String),
}

/// What a bin row shows for its thumbnail.
#[derive(Debug, Clone, PartialEq)]
pub enum Shown<'a> {
    /// Not asked for yet, or being read.
    Pending,
    Picture(&'a ImageHandle),
    /// No picture: why.
    Nothing(&'a str),
}

/// The thumbnails read this session, by what they are of.
#[derive(Debug, Default)]
pub struct Thumbs {
    thumbs: BTreeMap<ThumbKey, Thumb>,
    pub seen: Seen,
}

impl Thumbs {
    pub fn shown(&self, key: &ThumbKey) -> Shown<'_> {
        match self.thumbs.get(key) {
            None | Some(Thumb::Loading) => Shown::Pending,
            Some(Thumb::Ready { handle, .. }) => Shown::Picture(handle),
            Some(Thumb::Nothing(why)) => Shown::Nothing(why),
        }
    }

    fn loading(&self) -> usize {
        self.thumbs.values().filter(|t| matches!(t, Thumb::Loading)).count()
    }

    /// The thumbnails to start reading for the rows on screen among
    /// `order` (the bin's rows, top first), marked as being read.
    pub fn start(&mut self, order: &[ThumbKey], now: Instant) -> Vec<ThumbKey> {
        let visible = self.seen.on_screen(order, now);
        let start = to_start(&visible, |key| self.thumbs.contains_key(key), self.loading(), LIMIT);
        for key in &start {
            self.thumbs.insert(key.clone(), Thumb::Loading);
        }
        start
    }

    /// `key`'s read came back. `display` shows a scene-linear picture of a
    /// colour space (the OCIO display transform), or says why it cannot.
    /// An answer for a key forgotten meanwhile is dropped: it is read
    /// again when it is next on screen.
    pub fn arrived(&mut self, key: &ThumbKey, result: Result<Loaded, String>, display: impl FnMut(&str) -> Shows) {
        if !matches!(self.thumbs.get(key), Some(Thumb::Loading)) {
            return;
        }
        let thumb = match result {
            Err(e) => Thumb::Nothing(format!("No thumbnail: {e}")),
            Ok(Loaded { proxy: None, note, .. }) => Thumb::Nothing(note.unwrap_or_default()),
            Ok(Loaded { proxy: Some(proxy), colour_space, .. }) => picture(proxy, colour_space, display),
        };
        self.thumbs.insert(key.clone(), thumb);
    }

    /// The display or view changed: show every scene-linear picture
    /// through it again (nothing is read again).
    pub fn redisplay(&mut self, mut display: impl FnMut(&str) -> Shows) {
        for thumb in self.thumbs.values_mut() {
            if let Thumb::Ready { linear: Some((proxy, colour_space)), .. } = thumb {
                *thumb = picture(proxy.clone(), Some(colour_space.clone()), &mut display);
            }
        }
    }

    /// `shot`'s media changed: read its thumbnail again when it is next on
    /// screen. The URI is compared as the edit writes it.
    pub fn forget_shot(&mut self, shot: &str) {
        let shot = shot_key(shot);
        self.thumbs.retain(|key, _| !matches!(key, ThumbKey::Shot(uri) if shot_key(uri) == shot));
    }

    /// Read everything again as it comes on screen.
    pub fn clear(&mut self) {
        self.thumbs.clear();
    }
}

/// A shot URI however it is written (a trailing slash, or none).
fn shot_key(uri: &str) -> String {
    th_pipe::Uri::parse_address(uri).map_or_else(|_| uri.trim_end_matches('/').to_owned(), |u| u.to_string())
}

fn picture(proxy: Proxy, colour_space: Option<String>, mut display: impl FnMut(&str) -> Shows) -> Thumb {
    let handle = |shown: th_media::Thumbnail| ImageHandle::from_rgba(shown.width, shown.height, shown.rgba);
    if !proxy.linear {
        return Thumb::Ready { handle: handle(proxy.shown(|rgb| rgb)), linear: None };
    }
    let Some(colour_space) = colour_space else { return Thumb::Nothing("No thumbnail: its frames do not say their colour space".into()) };
    match display(&colour_space) {
        Ok(transform) => Thumb::Ready { handle: handle(proxy.shown(transform)), linear: Some((proxy, colour_space)) },
        Err(e) => Thumb::Nothing(format!("No thumbnail: {e}")),
    }
}

/// The first frame of `media`: a stack's is its bottom layer's.
fn first_frame(media: &Media) -> Result<Frame, String> {
    match media {
        Media::Movie(index) => {
            let mut decoder = MovieDecoder::open(Arc::clone(index), 1).map_err(|e| e.to_string())?;
            decoder.decode(0, |_, _| {}, || true).map_err(|e| e.to_string())
        }
        Media::Exr { sequence, .. } => sequence.decode(0, &media.default_view()).map_err(|e| e.to_string()),
        Media::Jpg { sequence, .. } => sequence.decode(0).map_err(|e| e.to_string()),
        Media::Stack(layers) => first_frame(&layers.first().ok_or("a stack of no layers")?.1),
    }
}

fn pictured(media: &th_playlist::Media, fps: u32) -> Result<Loaded, String> {
    let opened = open_media(media, (fps as i32, 1)).map_err(|e| crate::missing::open_failure(media, e))?;
    let frame = first_frame(&opened)?;
    let proxy = th_media::proxy(&frame, SIZE.0, SIZE.1);
    Ok(Loaded { proxy: Some(proxy), colour_space: opened.colour_space().map(str::to_owned), note: None })
}

/// Read `key`'s thumbnail. Blocking, off the UI thread: on a file server
/// it takes seconds.
pub fn load(paths: &ProjectPaths, key: &ThumbKey, fps: u32) -> Result<Loaded, String> {
    let project = paths.open()?;
    let sources = EditSources::new(&project, fps);
    match key {
        ThumbKey::Shot(shot) => {
            let takes = sources.takes(shot)?;
            let rule = Rule::best();
            match th_edit::takes::pick(&takes, &rule) {
                Some(take) => pictured(&takes[take].media, fps),
                None => {
                    let uri = th_pipe::Uri::parse_address(shot).map_err(|e| e.to_string())?;
                    let unfinished = crate::missing::unfinished_renders(&project, &uri);
                    let note = crate::missing::why(&takes, &rule).map(|reason| reason.label(unfinished));
                    Ok(Loaded { proxy: None, colour_space: None, note })
                }
            }
        }
        ThumbKey::Item { item, version } => {
            let pinned = VersionRule::Pinned(th_edit::document::Version::try_from(version.clone())?);
            let found = sources.item(item, &pinned)?;
            if found.measure.frames.is_none() {
                return Ok(Loaded { proxy: None, colour_space: None, note: None });
            }
            pictured(&th_playlist::Media::Movie { path: th_pipe::storage::slashed(&found.path) }, fps)
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::*;
    use proptest::prelude::*;

    fn key(n: usize) -> ThumbKey {
        ThumbKey::Shot(format!("entity:/shots/A/{n:03}"))
    }

    fn loaded() -> Loaded {
        let proxy = Proxy { width: 2, height: 1, rgb: vec![[0.5; 3]; 2], linear: false };
        Loaded { proxy: Some(proxy), colour_space: None, note: None }
    }

    fn no_display(_: &str) -> Shows {
        Err("no display".into())
    }

    /// One step of a session at a bin of `rows` rows.
    #[derive(Debug, Clone)]
    enum Step {
        /// The list draws rows `from .. from + count` (scrolled there).
        Draw { from: usize, count: usize },
        /// The editor's tick: start what there is room for.
        Tick,
        /// A read comes back (the `nth` of those in flight), failed or not.
        Arrive { nth: usize, failed: bool },
        /// Time passes.
        Wait(u64),
        /// The journal says a shot changed.
        Forget(usize),
    }

    fn step(rows: usize) -> impl Strategy<Value = Step> {
        prop_oneof![
            3 => (0..rows, 1usize..8).prop_map(|(from, count)| Step::Draw { from, count }),
            4 => Just(Step::Tick),
            4 => (0usize..LIMIT, any::<bool>()).prop_map(|(nth, failed)| Step::Arrive { nth, failed }),
            1 => (100u64..3000).prop_map(Step::Wait),
            1 => (0..rows).prop_map(Step::Forget),
        ]
    }

    proptest! {
        /// Whatever a session does (scrolling, ticks, reads coming back in
        /// any order, shots changing): never more than LIMIT reads in
        /// flight; a read starts only for a row the list drew within the
        /// last two seconds, top row first, and never for one already read
        /// or being read; and a row never drawn is never read, so a bin of
        /// hundreds of shots costs only what was looked at.
        #[test]
        fn only_what_is_on_screen_is_read_a_few_at_a_time(rows in 1usize..400, steps in prop::collection::vec(step(400), 0..80)) {
            let order: Vec<ThumbKey> = (0..rows).map(key).collect();
            let mut thumbs = Thumbs::default();
            let start = Instant::now();
            let mut now = start;
            let mut drawn: BTreeMap<usize, Instant> = BTreeMap::new();
            let mut flying: Vec<ThumbKey> = Vec::new();
            let mut done: BTreeSet<ThumbKey> = BTreeSet::new();
            let mut ever_drawn: BTreeSet<usize> = BTreeSet::new();
            for step in steps {
                match step {
                    Step::Draw { from, count } => {
                        for row in (from..from + count).filter(|r| *r < rows) {
                            thumbs.seen.mark(&order[row], now);
                            drawn.insert(row, now);
                            ever_drawn.insert(row);
                        }
                    }
                    Step::Tick => {
                        let started = thumbs.start(&order, now);
                        prop_assert!(flying.len() + started.len() <= LIMIT, "{} in flight", flying.len() + started.len());
                        let fresh: Vec<usize> = drawn.iter().filter(|(_, at)| now.duration_since(**at) <= ON_SCREEN).map(|(r, _)| *r).collect();
                        let wanted: Vec<&ThumbKey> = fresh.iter().map(|&r| &order[r]).filter(|k| !done.contains(*k) && !flying.contains(*k)).collect();
                        // The topmost wanted rows, as many as there is room for.
                        let room = LIMIT - flying.len();
                        let expected: Vec<ThumbKey> = wanted.into_iter().take(room).cloned().collect();
                        prop_assert_eq!(&started, &expected);
                        flying.extend(started);
                    }
                    Step::Arrive { nth, failed } => {
                        if nth < flying.len() {
                            let key = flying.remove(nth);
                            let result = if failed { Err("the share is away".to_owned()) } else { Ok(loaded()) };
                            let was_loading = matches!(thumbs.shown(&key), Shown::Pending) && thumbs.thumbs.contains_key(&key);
                            thumbs.arrived(&key, result, no_display);
                            if was_loading {
                                prop_assert_eq!(matches!(thumbs.shown(&key), Shown::Picture(_)), !failed);
                                prop_assert_eq!(matches!(thumbs.shown(&key), Shown::Nothing(_)), failed);
                                done.insert(key);
                            } else {
                                // Forgotten while it was read: dropped.
                                prop_assert_eq!(thumbs.shown(&key), Shown::Pending);
                                prop_assert!(!thumbs.thumbs.contains_key(&key));
                            }
                        }
                    }
                    Step::Wait(ms) => now += Duration::from_millis(ms),
                    Step::Forget(row) => {
                        if row < rows {
                            thumbs.forget_shot(&format!("entity:/shots/A/{row:03}/"));
                            done.remove(&order[row]);
                            // Its read in flight is dropped when it lands;
                            // it no longer counts against the limit.
                            if flying.contains(&order[row]) {
                                flying.retain(|k| k != &order[row]);
                            }
                        }
                    }
                }
                prop_assert!(thumbs.loading() <= LIMIT);
            }
            for (row, key) in order.iter().enumerate() {
                if !ever_drawn.contains(&row) {
                    prop_assert!(!thumbs.thumbs.contains_key(key), "row {} was never on screen", row);
                }
            }
        }

        /// A screenful left alone is read to the end: each tick starts the
        /// next rows as reads come back, until every row on screen has its
        /// thumbnail, and nothing more.
        #[test]
        fn a_screenful_is_read_to_the_end(rows in 1usize..300, from in 0usize..300, count in 1usize..30) {
            let order: Vec<ThumbKey> = (0..rows).map(key).collect();
            let visible: Vec<usize> = (from..from + count).filter(|r| *r < rows).collect();
            let mut thumbs = Thumbs::default();
            let now = Instant::now();
            for &row in &visible {
                thumbs.seen.mark(&order[row], now);
            }
            let mut read = Vec::new();
            for _ in 0..=visible.len() {
                let started = thumbs.start(&order, now);
                for key in &started {
                    thumbs.arrived(key, Ok(loaded()), no_display);
                }
                read.extend(started);
            }
            let expected: Vec<ThumbKey> = visible.iter().map(|&r| order[r].clone()).collect();
            prop_assert_eq!(read, expected, "top row first, each once");
            prop_assert!(thumbs.start(&order, now).is_empty());
        }

        /// to_start never goes over the limit, never repeats a key, keeps
        /// the order, and skips what is known.
        #[test]
        fn to_start_respects_the_limit(visible in prop::collection::vec(0u8..20, 0..30), known in prop::collection::btree_set(0u8..20, 0..10), loading in 0usize..6, limit in 0usize..6) {
            let started = to_start(&visible, |k| known.contains(k), loading, limit);
            prop_assert!(started.len() <= limit.saturating_sub(loading));
            let mut wanted: Vec<u8> = Vec::new();
            for k in &visible {
                if !known.contains(k) && !wanted.contains(k) {
                    wanted.push(*k);
                }
            }
            wanted.truncate(limit.saturating_sub(loading));
            prop_assert_eq!(started, wanted);
        }
    }

    /// A scene-linear picture goes through the display, again when the
    /// display changes, without being read again; a display-referred one is
    /// shown as it is.
    #[test]
    fn a_linear_picture_follows_the_display() {
        let mut thumbs = Thumbs::default();
        let now = Instant::now();
        let order = [key(0), key(1)];
        for k in &order {
            thumbs.seen.mark(k, now);
        }
        assert_eq!(thumbs.start(&order, now).len(), 2);
        let linear = Loaded {
            proxy: Some(Proxy { width: 1, height: 1, rgb: vec![[0.25; 3]], linear: true }),
            colour_space: Some("ACEScg".into()),
            note: None,
        };
        let gain = |g: f32| {
            move |space: &str| -> Shows {
                assert_eq!(space, "ACEScg");
                Ok(Box::new(move |rgb: [f32; 3]| rgb.map(|c| c * g)))
            }
        };
        thumbs.arrived(&order[0], Ok(linear), gain(2.0));
        thumbs.arrived(&order[1], Ok(loaded()), no_display);
        let pixel = |thumbs: &Thumbs, k: &ThumbKey| match thumbs.shown(k) {
            Shown::Picture(handle) => handle.image().pixels[0],
            other => panic!("{other:?}"),
        };
        assert_eq!(pixel(&thumbs, &order[0]), 128, "0.25 through a gain of 2");
        assert_eq!(pixel(&thumbs, &order[1]), 128, "0.5 as it is");
        thumbs.redisplay(gain(4.0));
        assert_eq!(pixel(&thumbs, &order[0]), 255);
        assert_eq!(pixel(&thumbs, &order[1]), 128);
        assert!(thumbs.start(&order, now).is_empty(), "nothing is read again");
    }

    #[test]
    fn a_shot_with_nothing_says_why_and_refresh_reads_again() {
        let mut thumbs = Thumbs::default();
        let now = Instant::now();
        let order = [key(0)];
        thumbs.seen.mark(&order[0], now);
        thumbs.start(&order, now);
        let nothing = Loaded { proxy: None, colour_space: None, note: Some("No playblast or render yet".into()) };
        thumbs.arrived(&order[0], Ok(nothing), no_display);
        assert_eq!(thumbs.shown(&order[0]), Shown::Nothing("No playblast or render yet"));
        assert!(thumbs.start(&order, now).is_empty());
        thumbs.clear();
        assert_eq!(thumbs.start(&order, now), order);
    }

    /// What came back for a row, as a test tells it.
    #[derive(Debug, Clone)]
    enum Back {
        Picture,
        Reason(String),
        /// Read, and there is no picture to have: media that is sound only.
        SoundOnly,
        Failed(String),
    }

    fn back() -> impl Strategy<Value = Back> {
        prop_oneof![Just(Back::Picture), "[a-z]{1,12}".prop_map(Back::Reason), Just(Back::SoundOnly), "[a-z]{1,12}".prop_map(Back::Failed),]
    }

    proptest! {
        /// Once a row's read is back it shows one thing: its picture, or
        /// the words for why there is none, never both and never still
        /// waiting. The words are the reason given (a shot with no take),
        /// or the failure (a file that would not open). Sound-only media
        /// is the one row with neither: it has no picture to miss, so it
        /// shows no picture and no words here (the row's own line says
        /// "sound, N frames long").
        #[test]
        fn a_row_read_shows_its_picture_or_why_not(backs in prop::collection::vec(back(), 1..9)) {
            let mut thumbs = Thumbs::default();
            let now = Instant::now();
            let order: Vec<ThumbKey> = (0..backs.len()).map(key).collect();
            for k in &order {
                thumbs.seen.mark(k, now);
            }
            let mut left = backs.len();
            while left > 0 {
                let started = thumbs.start(&order, now);
                prop_assert!(!started.is_empty(), "rows on screen are never left unread");
                for k in started {
                    let at = order.iter().position(|o| *o == k).unwrap();
                    let result = match &backs[at] {
                        Back::Picture => Ok(loaded()),
                        Back::Reason(why) => Ok(Loaded { proxy: None, colour_space: None, note: Some(why.clone()) }),
                        Back::SoundOnly => Ok(Loaded { proxy: None, colour_space: None, note: None }),
                        Back::Failed(why) => Err(why.clone()),
                    };
                    thumbs.arrived(&k, result, no_display);
                    left -= 1;
                }
            }
            for (k, back) in order.iter().zip(&backs) {
                match (thumbs.shown(k), back) {
                    (Shown::Picture(_), Back::Picture) => {}
                    (Shown::Nothing(said), Back::Reason(why)) => prop_assert_eq!(said, why),
                    (Shown::Nothing(said), Back::SoundOnly) => prop_assert_eq!(said, ""),
                    (Shown::Nothing(said), Back::Failed(why)) => prop_assert!(said.contains(why.as_str()) && said.starts_with("No thumbnail"), "{}", said),
                    (shown, back) => prop_assert!(false, "{:?} shows {:?}", back, shown),
                }
            }
        }
    }

    /// A shot with no playblast and no render is read as the reason, in
    /// words, not as an empty tile: read from the project template, where
    /// no shot has a take.
    #[test]
    fn a_shot_with_no_take_is_read_as_its_reason() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../scripts/project_template");
        let paths = ProjectPaths { root: root.clone(), pipeline: root.clone(), config: root.join("_config") };
        let read = load(&paths, &ThumbKey::Shot("entity:/shots/A/010".into()), 24).expect("a shot with no take is no failure");
        assert!(read.proxy.is_none());
        assert!(read.note.is_some_and(|why| !why.trim().is_empty()), "no reason was given");
    }
}
