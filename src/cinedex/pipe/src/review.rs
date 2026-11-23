//! Review marks: what reviewers draw on a take's frames and write about
//! them (Cinedex's review brush and notes), kept on the project share so
//! that everyone with access to the project sees them, adds to them and
//! changes them, from any machine.
//!
//! A take's marks are a folder of its own under the render root, beside
//! the review media they are about ([`take_dir`], [`media_dir`]):
//!
//! - a shot's playblast: `render:/review/playblast/<entity>/<department>/<version>/`
//! - a shot's render: `render:/review/render/<entity>/<department>/<version>/`
//! - a media library item: `render:/review/media/<item>/<version>/`
//!
//! A render's marks are its version's, whichever of its layers is shown.
//! A live playblast has none: its frames are not kept.
//!
//! Every mark is a file of its own, `<frame>.<id>.json` ([`file_name`]):
//! a stroke of the brush, or a note. So two people drawing on one frame at
//! once never write the same file, and anyone may erase or change anyone's
//! mark: erasing removes its file, changing a note writes it again whole.
//! The frame is a source frame (a shot's are its own numbers, 1001 on), so
//! a mark stays on its picture however the clips showing it are cut. The
//! id is the milliseconds it was made at and a random part
//! ([`new_id`]), so a folder's names in order are its marks in the order
//! they were made, the order they are drawn in. A file is written under a
//! temporary name (`.<name>.tmp`, which readers pass over) and renamed into
//! place, so a reader never sees half of one.
//!
//! A file holds one JSON object ([`encode`]):
//!
//! ```json
//! {"kind": "stroke", "frame": 1043, "author": "soren-n", "created": 1763894400123,
//!  "color": "#ff3b30", "size": 0.004, "points": [0.51, 0.25, 0.8, 0.52, 0.26, 0.9]}
//! {"kind": "note", "frame": 1043, "author": "soren-n", "created": 1763894400123,
//!  "text": "The hand pops here", "edited": 1763894460000, "edited_by": "remy"}
//! ```
//!
//! A stroke's `points` are `x, y, pressure` triples: `x` across the
//! source picture from its left edge (0) to its right (1), `y` down it
//! from the top (0) to the bottom (1), and the pen's pressure from 0 to 1
//! (1 for a mouse, or with pressure off). `size` is the line's width at
//! full pressure, as a share of the picture's height; [`width_at`] is the
//! width at a pressure. `author`, `created` and `edited_by` are
//! TumblePipe's `TH_USER` and Unix milliseconds; `edited` and `edited_by`
//! are there once a note was changed.
//!
//! Reading is lenient, so marks written by a later Cinedex still read: a
//! field it does not know is passed over, and a file of a kind it does
//! not know (or that is not a mark at all) is left out ([`decode`]).

use std::collections::HashMap;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};
use th_playlist::{Kind, Take};

use crate::{Project, Uri};

/// The folder under the render root that every take's marks are under.
pub const ROOT: &str = "review";
/// The thinnest and thickest a stroke may be, as shares of the picture's
/// height.
pub const MIN_SIZE: f32 = 0.0005;
pub const MAX_SIZE: f32 = 0.25;
/// How thin a stroke is at no pressure, of its full width.
pub const LEAST_WIDTH: f32 = 0.1;
/// The decimals a point's place and pressure are kept to: a ten-thousandth
/// of the picture is under half a pixel of a 4K frame.
const PLACE_SCALE: f64 = 10_000.0;
const PRESSURE_SCALE: f64 = 1_000.0;

/// The folder of a shot's take's marks: `render:/review/<playblast|render|
/// check>/<entity>/<department>/<version>` (a check render's versions are
/// numbered apart from the renders'). None for a live playblast, which
/// keeps no frames to review.
pub fn take_dir(project: &Project, shot: &str, take: &Take) -> Result<Option<PathBuf>, String> {
    let kind = match take.kind() {
        Kind::Movie => "playblast",
        Kind::Exr if take.check => "check",
        Kind::Exr => "render",
        Kind::Live => return Ok(None),
    };
    let entity = Uri::parse_address(shot).map_err(|e| e.to_string())?;
    let uri = Uri::root("render").join(ROOT).join(kind).join_all(&entity.segments).join(&take.department).join(&take.version);
    project.storage.resolve(&uri).map(Some).map_err(|e| e.to_string())
}

/// The folder of a media library item's version's marks:
/// `render:/review/media/<item>/<version>`.
pub fn media_dir(project: &Project, item: &str, version: &str) -> Result<PathBuf, String> {
    let uri = Uri::root("render").join(ROOT).join("media").join(item).join(version);
    project.storage.resolve(&uri).map_err(|e| e.to_string())
}

/// Who makes a mark: TumblePipe's `TH_USER` (never the OS account), else
/// blank.
pub fn author() -> String {
    crate::edit_files::user_name()
}

/// Now, in Unix milliseconds.
pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)
}

/// A new mark's id: the milliseconds `now`, then sixteen random hex digits,
/// so ids made one after another sort in the order they were made.
pub fn new_id(now: u64) -> String {
    format!("{now:013}-{}", &crate::shared_journal::new_id()[..16])
}

/// The file a mark of `frame` with `id` is kept in.
pub fn file_name(frame: i64, id: &str) -> String {
    format!("{frame}.{id}.json")
}

/// The frame and id a mark's file name holds; None for any other name (a
/// temporary file, anything else in the folder).
pub fn parse_name(name: &str) -> Option<(i64, &str)> {
    let stem = name.strip_suffix(".json")?;
    let (frame, id) = stem.split_once('.')?;
    let id_ok = !id.is_empty() && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    let frame_ok = !frame.is_empty() && frame.trim_start_matches('-').bytes().all(|b| b.is_ascii_digit());
    if id_ok && frame_ok { Some((frame.parse().ok()?, id)) } else { None }
}

/// An RGB colour, written `#rrggbb`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Rgb(pub [u8; 3]);

impl Rgb {
    pub fn hex(self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.0[0], self.0[1], self.0[2])
    }

    /// `#rrggbb` (either case) as a colour; None for anything else.
    pub fn parse(text: &str) -> Option<Rgb> {
        let digits = text.strip_prefix('#')?;
        if digits.len() != 6 || !digits.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let byte = |i: usize| u8::from_str_radix(&digits[i..i + 2], 16).ok();
        Some(Rgb([byte(0)?, byte(2)?, byte(4)?]))
    }
}

/// A point of a stroke: across and down the source picture (0 to 1), and
/// the pen's pressure (0 to 1).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Point {
    pub x: f32,
    pub y: f32,
    pub pressure: f32,
}

/// A line drawn with the brush.
#[derive(Debug, Clone, PartialEq)]
pub struct Stroke {
    pub color: Rgb,
    /// Its width at full pressure, as a share of the picture's height.
    pub size: f32,
    /// At least one.
    pub points: Vec<Point>,
}

/// A stroke's width at `pressure`, as a share of the picture's height: its
/// size at full pressure, down to [`LEAST_WIDTH`] of it at none.
pub fn width_at(size: f32, pressure: f32) -> f32 {
    size * (LEAST_WIDTH + (1.0 - LEAST_WIDTH) * pressure.clamp(0.0, 1.0))
}

/// A note written on a frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Note {
    pub text: String,
    /// When it was last changed (Unix ms) and by whom; None until it is.
    pub edited: Option<(u64, String)>,
}

/// What a mark is.
#[derive(Debug, Clone, PartialEq)]
pub enum Body {
    Stroke(Stroke),
    Note(Note),
}

/// A mark on a take's frame, as its file holds it.
#[derive(Debug, Clone, PartialEq)]
pub struct Mark {
    pub id: String,
    pub frame: i64,
    pub author: String,
    /// When it was made, Unix ms.
    pub created: u64,
    pub body: Body,
}

impl Mark {
    /// The file it is kept in.
    pub fn file_name(&self) -> String {
        file_name(self.frame, &self.id)
    }

    pub fn note(&self) -> Option<&Note> {
        match &self.body {
            Body::Note(note) => Some(note),
            Body::Stroke(_) => None,
        }
    }

    pub fn stroke(&self) -> Option<&Stroke> {
        match &self.body {
            Body::Stroke(stroke) => Some(stroke),
            Body::Note(_) => None,
        }
    }
}

fn rounded(value: f32, scale: f64) -> Value {
    json!((f64::from(value) * scale).round() / scale)
}

/// The JSON a mark's file holds: one object and a newline. Places are
/// kept to four decimals and pressures to three; the id and frame are the
/// file's name, not written in it.
pub fn encode(mark: &Mark) -> Vec<u8> {
    let mut object = Map::new();
    let kind = match &mark.body {
        Body::Stroke(_) => "stroke",
        Body::Note(_) => "note",
    };
    object.insert("kind".into(), json!(kind));
    object.insert("frame".into(), json!(mark.frame));
    object.insert("author".into(), json!(mark.author));
    object.insert("created".into(), json!(mark.created));
    match &mark.body {
        Body::Stroke(stroke) => {
            object.insert("color".into(), json!(stroke.color.hex()));
            object.insert("size".into(), rounded(stroke.size, 1_000_000.0));
            let points: Vec<Value> = stroke
                .points
                .iter()
                .flat_map(|p| [rounded(p.x, PLACE_SCALE), rounded(p.y, PLACE_SCALE), rounded(p.pressure, PRESSURE_SCALE)])
                .collect();
            object.insert("points".into(), Value::Array(points));
        }
        Body::Note(note) => {
            object.insert("text".into(), json!(note.text));
            if let Some((when, who)) = &note.edited {
                object.insert("edited".into(), json!(when));
                object.insert("edited_by".into(), json!(who));
            }
        }
    }
    let mut bytes = serde_json::to_vec(&Value::Object(object)).expect("a mark is JSON");
    bytes.push(b'\n');
    bytes
}

/// The mark the file named for `frame` and `id` holds; None for anything
/// that is not one: not JSON, a kind this Cinedex does not know, a stroke
/// with no points or points that are not numbers, a note with no text.
/// Fields it does not know are passed over. The frame and id are the
/// name's: a file renamed by hand is the mark its name says.
pub fn decode(frame: i64, id: &str, bytes: &[u8]) -> Option<Mark> {
    let value: Value = serde_json::from_slice(bytes).ok()?;
    let text = |key: &str| value.get(key).and_then(Value::as_str).map(str::to_owned);
    let body = match value.get("kind")?.as_str()? {
        "stroke" => {
            let numbers: Vec<f32> = value.get("points")?.as_array()?.iter().map(|v| v.as_f64().map(|n| n as f32)).collect::<Option<_>>()?;
            if numbers.is_empty() || !numbers.len().is_multiple_of(3) || numbers.iter().any(|n| !n.is_finite()) {
                return None;
            }
            let points = numbers.as_chunks::<3>().0.iter().map(|&[x, y, p]| Point { x, y, pressure: p.clamp(0.0, 1.0) }).collect();
            let size = value.get("size")?.as_f64()? as f32;
            if !size.is_finite() {
                return None;
            }
            Body::Stroke(Stroke { color: Rgb::parse(&text("color")?)?, size: size.clamp(MIN_SIZE, MAX_SIZE), points })
        }
        "note" => {
            let edited = value.get("edited").and_then(Value::as_u64).map(|when| (when, text("edited_by").unwrap_or_default()));
            Body::Note(Note { text: text("text")?, edited })
        }
        _ => return None,
    };
    Some(Mark {
        id: id.to_owned(),
        frame,
        author: text("author").unwrap_or_default(),
        created: value.get("created").and_then(Value::as_u64).unwrap_or(0),
        body,
    })
}

/// How many times a rename or delete Windows refuses is tried, a moment
/// apart: it refuses one of a file another reviewer is reading or has
/// just replaced (pending its delete) until that is over.
const BUSY_TRIES: u32 = 20;
const BUSY_WAIT: std::time::Duration = std::time::Duration::from_millis(25);

/// Whether `e` is Windows refusing a file another program has open (access
/// denied, or a sharing violation), which passes once it is closed.
fn busy(e: &std::io::Error) -> bool {
    matches!(e.kind(), std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::ResourceBusy)
        || (cfg!(windows) && e.raw_os_error() == Some(32))
}

/// `act` again while it is refused for a file in use, at most
/// [`BUSY_TRIES`] times.
fn when_free<T>(mut act: impl FnMut() -> std::io::Result<T>) -> std::io::Result<T> {
    let mut tries = 1;
    loop {
        match act() {
            Err(e) if busy(&e) && tries < BUSY_TRIES => {
                tries += 1;
                std::thread::sleep(BUSY_WAIT);
            }
            done => return done,
        }
    }
}

/// Write `mark` into `dir` (made if it is not there), under a temporary
/// name and then renamed over its file: a reader sees the old mark or the
/// new, never half of one.
pub fn write(dir: &Path, mark: &Mark) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    let name = mark.file_name();
    let temp = dir.join(format!(".{name}.{}.tmp", &crate::shared_journal::new_id()[..8]));
    let written = (|| {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temp)?;
        file.write_all(&encode(mark))?;
        drop(file);
        when_free(|| std::fs::rename(&temp, dir.join(&name)))
    })();
    if written.is_err() {
        let _ = std::fs::remove_file(&temp);
    }
    written
}

/// Take the mark named `name` out of `dir`. One already gone (another
/// reviewer erased it) is no error.
pub fn remove(dir: &Path, name: &str) -> std::io::Result<()> {
    match when_free(|| std::fs::remove_file(dir.join(name))) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
        _ => Ok(()),
    }
}

/// What a mark's file was when it was read: its modified time and size. A
/// file whose stamp is the same has not changed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stamp {
    pub modified: Option<SystemTime>,
    pub len: u64,
}

/// The mark files of `dir` and their stamps, by name. A folder that is not
/// there has none (nothing was marked yet).
pub fn listing(dir: &Path) -> std::io::Result<HashMap<String, Stamp>> {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(HashMap::new()),
        Err(e) => return Err(e),
    };
    let mut found = HashMap::new();
    for entry in entries {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if parse_name(&name).is_none() {
            continue;
        }
        // On Windows the listing carries each file's metadata: no round
        // trip to the share per file.
        let Ok(meta) = entry.metadata() else { continue };
        if meta.is_file() {
            found.insert(name, Stamp { modified: meta.modified().ok(), len: meta.len() });
        }
    }
    Ok(found)
}

/// What changed in a folder since it was last read ([`sync`]).
#[derive(Debug, Default)]
pub struct Changes {
    /// Marks new or changed since, by file name, with their stamps.
    pub changed: Vec<(String, Stamp, Mark)>,
    /// Names no longer there: erased, here or elsewhere.
    pub gone: Vec<String>,
    /// Names there that do not read as a mark yet (a later kind, a file
    /// mid-copy), with their stamps: kept, so they are read again only
    /// once they change.
    pub unread: Vec<(String, Stamp)>,
}

/// Read what changed in `dir` since the marks held were read, `held` being
/// each one's name and stamp then: the files new or with another stamp are
/// read, and the names no longer listed are gone. A file that cannot be
/// opened just now (Windows refuses one that another reviewer is
/// replacing) is left out, so the next read tries it again.
pub fn sync(dir: &Path, held: &HashMap<String, Stamp>) -> std::io::Result<Changes> {
    let listed = listing(dir)?;
    let mut changes = Changes { gone: held.keys().filter(|name| !listed.contains_key(*name)).cloned().collect(), ..Changes::default() };
    for (name, stamp) in listed {
        if held.get(&name) == Some(&stamp) {
            continue;
        }
        let (frame, id) = parse_name(&name).expect("listed names parse");
        let mark = match std::fs::read(dir.join(&name)) {
            Ok(bytes) => decode(frame, id, &bytes),
            // Erased between the listing and the read.
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if held.contains_key(&name) {
                    changes.gone.push(name);
                }
                continue;
            }
            Err(_) => continue,
        };
        match mark {
            Some(mark) => changes.changed.push((name, stamp, mark)),
            None => changes.unread.push((name, stamp)),
        }
    }
    changes.gone.sort();
    changes.changed.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(changes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::collections::BTreeMap;

    fn point() -> impl Strategy<Value = Point> {
        (-0.5f32..1.5, -0.5f32..1.5, 0f32..=1.0).prop_map(|(x, y, pressure)| Point { x, y, pressure })
    }

    fn body() -> impl Strategy<Value = Body> {
        let stroke = (any::<[u8; 3]>(), MIN_SIZE..MAX_SIZE, prop::collection::vec(point(), 1..40))
            .prop_map(|(c, size, points)| Body::Stroke(Stroke { color: Rgb(c), size, points }));
        let note = ("\\PC{0,80}", proptest::option::of((0u64..u64::from(u32::MAX) * 1000, "[a-z\\-]{0,12}")))
            .prop_map(|(text, edited)| Body::Note(Note { text, edited }));
        prop_oneof![stroke, note]
    }

    /// What happens to a folder between two reads.
    #[derive(Debug, Clone)]
    enum FileOp {
        Write(Mark),
        Rewrite(prop::sample::Index, Body),
        Remove(prop::sample::Index),
        /// A file named as a mark that is not one (a later kind, a broken copy).
        Junk(i64, Vec<u8>),
        /// Anything else in the folder.
        Stray(String),
        Read,
    }

    fn file_op() -> impl Strategy<Value = FileOp> {
        prop_oneof![
            3 => mark().prop_map(FileOp::Write),
            2 => (any::<prop::sample::Index>(), body()).prop_map(|(i, b)| FileOp::Rewrite(i, b)),
            2 => any::<prop::sample::Index>().prop_map(FileOp::Remove),
            1 => (1000i64..1010, prop::collection::vec(any::<u8>(), 0..64)).prop_map(|(f, b)| FileOp::Junk(f, b)),
            1 => r"[a-z]{1,8}\.(json|tmp|txt)".prop_map(FileOp::Stray),
            3 => Just(FileOp::Read),
        ]
    }

    /// Any JSON value, small.
    fn json() -> impl Strategy<Value = Value> {
        let leaf = prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::Bool),
            any::<f64>().prop_map(|n| serde_json::Number::from_f64(n).map_or(Value::Null, Value::Number)),
            any::<i64>().prop_map(|n| Value::Number(n.into())),
            r"\PC{0,12}".prop_map(Value::String),
            Just(Value::String("#ff00aa".into())),
        ];
        leaf.prop_recursive(2, 16, 8, |inner| {
            prop_oneof![
                prop::collection::vec(inner.clone(), 0..10).prop_map(Value::Array),
                prop::collection::btree_map("[a-z]{1,6}", inner, 0..5).prop_map(|m| Value::Object(m.into_iter().collect())),
            ]
        })
    }

    /// A mark's file as JSON: its fields there or not, each of the right
    /// type or any other, and fields no Cinedex knows.
    fn json_mark() -> impl Strategy<Value = Value> {
        let kind = prop_oneof![3 => Just(Value::from("stroke")), 2 => Just(Value::from("note")), 1 => json()];
        let color = prop_oneof![3 => any::<[u8; 3]>().prop_map(|c| Value::from(Rgb(c).hex())), 1 => json()];
        let points = prop_oneof![
            4 => prop::collection::vec(prop_oneof![4 => -2f64..3.0, 1 => any::<f64>()], 0..4).prop_map(|v| v.repeat(3)).prop_map(|v| Value::Array(
                v.into_iter().map(|n| serde_json::Number::from_f64(n).map_or(Value::Null, Value::Number)).collect()
            )),
            1 => json(),
        ];
        let size = prop_oneof![3 => (-1f64..1.0).prop_map(Value::from), 1 => json()];
        let field = |v: BoxedStrategy<Value>| proptest::option::of(v);
        (
            field(kind.boxed()),
            field(points.boxed()),
            field(size.boxed()),
            field(color.boxed()),
            field(json().boxed()),
            field(json().boxed()),
            prop::collection::btree_map("[a-z]{1,6}", json(), 0..3),
        )
            .prop_map(|(kind, points, size, color, text, edited, extra)| {
                let mut out: serde_json::Map<String, Value> = extra.into_iter().collect();
                for (key, value) in
                    [("kind", kind), ("points", points), ("size", size), ("color", color), ("text", text), ("edited", edited)]
                {
                    if let Some(value) = value {
                        out.insert(key.into(), value);
                    }
                }
                Value::Object(out)
            })
    }

    fn mark() -> impl Strategy<Value = Mark> {
        (-100i64..100_000, 0u64..u64::from(u32::MAX) * 1000, "[a-z\\-]{0,12}", body()).prop_map(|(frame, created, author, body)| Mark {
            id: new_id(created),
            frame,
            author,
            created,
            body,
        })
    }

    /// A mark as kept: its places to four decimals, pressures to three,
    /// its size to six.
    fn as_kept(mark: &Mark) -> Mark {
        let mut kept = mark.clone();
        if let Body::Stroke(stroke) = &mut kept.body {
            let round = |v: f32, scale: f64| ((f64::from(v) * scale).round() / scale) as f32;
            stroke.size = round(stroke.size, 1_000_000.0).clamp(MIN_SIZE, MAX_SIZE);
            for p in &mut stroke.points {
                (p.x, p.y, p.pressure) = (round(p.x, PLACE_SCALE), round(p.y, PLACE_SCALE), round(p.pressure, PRESSURE_SCALE));
            }
        }
        kept
    }

    proptest! {
        /// Reading only what changed, however files are written, rewritten,
        /// erased or broken between reads, always ends where a fresh read of
        /// the folder does: the same marks, the same unreadable names.
        #[test]
        fn reading_changes_only_ends_as_a_fresh_read(ops in prop::collection::vec(file_op(), 1..40)) {
            let dir = tempfile::tempdir().unwrap();
            let dir = dir.path().join("v0001");
            let mut held: HashMap<String, Stamp> = HashMap::new();
            let mut seen: BTreeMap<String, Option<Vec<u8>>> = BTreeMap::new();
            let mut written: Vec<Mark> = Vec::new();
            let read = |held: &mut HashMap<String, Stamp>, seen: &mut BTreeMap<String, Option<Vec<u8>>>| {
                let changes = sync(&dir, held).unwrap();
                for name in changes.gone {
                    held.remove(&name);
                    seen.remove(&name);
                }
                for (name, stamp, mark) in changes.changed {
                    held.insert(name.clone(), stamp);
                    seen.insert(name, Some(encode(&mark)));
                }
                for (name, stamp) in changes.unread {
                    held.insert(name.clone(), stamp);
                    seen.insert(name, None);
                }
            };
            for (n, op) in ops.into_iter().enumerate() {
                match op {
                    FileOp::Write(mut mark) => {
                        mark.id = new_id(mark.created.wrapping_add(n as u64));
                        write(&dir, &mark).unwrap();
                        written.push(mark);
                    }
                    FileOp::Rewrite(pick, body) if !written.is_empty() => {
                        let at = pick.index(written.len());
                        written[at].body = body;
                        write(&dir, &written[at]).unwrap();
                    }
                    FileOp::Remove(pick) if !written.is_empty() => {
                        let gone = written.remove(pick.index(written.len()));
                        remove(&dir, &gone.file_name()).unwrap();
                    }
                    FileOp::Junk(frame, bytes) => {
                        std::fs::create_dir_all(&dir).unwrap();
                        std::fs::write(dir.join(file_name(frame, &new_id(n as u64))), bytes).unwrap();
                    }
                    FileOp::Stray(name) => {
                        std::fs::create_dir_all(&dir).unwrap();
                        std::fs::write(dir.join(name), b"{}").unwrap();
                    }
                    FileOp::Read => read(&mut held, &mut seen),
                    _ => {}
                }
            }
            read(&mut held, &mut seen);
            let mut fresh_held = HashMap::new();
            let mut fresh = BTreeMap::new();
            let changes = sync(&dir, &fresh_held).unwrap();
            prop_assert!(changes.gone.is_empty());
            for (name, stamp, mark) in changes.changed {
                fresh_held.insert(name.clone(), stamp);
                fresh.insert(name, Some(encode(&mark)));
            }
            for (name, _) in changes.unread {
                fresh.insert(name, None);
            }
            prop_assert_eq!(seen, fresh);
        }

        /// Whatever is in a mark's file (any bytes; any JSON, its fields of
        /// any type, a later Cinedex's fields among them), reading it never
        /// fails hard, and what it reads as is a mark Cinedex can draw: a
        /// stroke with points, every number finite, pressure 0 to 1, the
        /// size in range.
        #[test]
        fn any_file_reads_as_a_drawable_mark_or_none(bytes in prop_oneof![
            prop::collection::vec(any::<u8>(), 0..200),
            json_mark().prop_map(|v| serde_json::to_vec(&v).unwrap()),
        ]) {
            if let Some(mark) = decode(1001, "1-a", &bytes) {
                prop_assert_eq!(mark.frame, 1001);
                if let Body::Stroke(stroke) = &mark.body {
                    prop_assert!(!stroke.points.is_empty());
                    prop_assert!((MIN_SIZE..=MAX_SIZE).contains(&stroke.size));
                    for p in &stroke.points {
                        prop_assert!(p.x.is_finite() && p.y.is_finite());
                        prop_assert!((0.0..=1.0).contains(&p.pressure));
                    }
                }
            }
        }

        /// A mark written reads back as itself, its numbers as kept.
        #[test]
        fn a_mark_reads_back(mark in mark()) {
            let bytes = encode(&mark);
            prop_assert_eq!(bytes.last(), Some(&b'\n'));
            prop_assert_eq!(decode(mark.frame, &mark.id, &bytes), Some(as_kept(&mark)));
        }

        /// A file name holds its frame and id, and nothing else parses:
        /// not a temporary file, not a name without a frame.
        #[test]
        fn names_hold_frame_and_id(frame in -100_000i64..100_000, now in 0u64..u64::from(u32::MAX) * 1000) {
            let id = new_id(now);
            let name = file_name(frame, &id);
            prop_assert_eq!(parse_name(&name), Some((frame, id.as_str())));
            let (temp, other) = (format!(".{name}.0a1b2c3d.tmp"), format!("x{name}"));
            prop_assert_eq!(parse_name(&temp), None);
            prop_assert_eq!(parse_name(&other), None);
            prop_assert_eq!(parse_name(&id), None);
        }

        /// Ids made later sort after ids made earlier, so a folder's names
        /// in order are its marks in the order they were made.
        #[test]
        fn later_ids_sort_after(a in 0u64..u64::from(u32::MAX) * 1000, b in 0u64..u64::from(u32::MAX) * 1000) {
            prop_assume!(a != b);
            prop_assert_eq!(new_id(a) < new_id(b), a < b);
        }

        /// A colour reads back from its hex, either case.
        #[test]
        fn colours_round_trip(c in any::<[u8; 3]>()) {
            prop_assert_eq!(Rgb::parse(&Rgb(c).hex()), Some(Rgb(c)));
            prop_assert_eq!(Rgb::parse(&Rgb(c).hex().to_uppercase()), Some(Rgb(c)));
        }

        /// A stroke is never thinner than its least width nor thicker than
        /// its size, and grows with pressure.
        #[test]
        fn width_follows_pressure(size in MIN_SIZE..MAX_SIZE, a in 0f32..=1.0, b in 0f32..=1.0) {
            let (wa, wb) = (width_at(size, a), width_at(size, b));
            prop_assert!(wa >= size * LEAST_WIDTH * 0.999 && wa <= size * 1.001);
            if a < b { prop_assert!(wa <= wb); }
        }
    }

    /// What a later Cinedex might write still reads: unknown fields are
    /// passed over; an unknown kind or a broken mark reads as none.
    #[test]
    fn reading_is_lenient() {
        let note = br#"{"kind":"note","frame":5,"author":"a","created":1,"text":"hi","mood":"glad"}"#;
        assert_eq!(decode(5, "1-a", note).and_then(|m| m.note().map(|n| n.text.clone())), Some("hi".into()));
        for broken in [
            &br#"{"kind":"arrow","frame":5}"#[..],
            br##"{"kind":"stroke","color":"#ff0000","size":0.01,"points":[]}"##,
            br##"{"kind":"stroke","color":"#ff0000","size":0.01,"points":[0.1,0.2]}"##,
            br#"{"kind":"stroke","color":"red","size":0.01,"points":[0.1,0.2,1]}"#,
            br#"{"kind":"note"}"#,
            b"not json",
        ] {
            assert_eq!(decode(5, "1-a", broken), None, "{}", String::from_utf8_lossy(broken));
        }
    }

    fn stroke(frame: i64, created: u64) -> Mark {
        Mark {
            id: new_id(created),
            frame,
            author: "a".into(),
            created,
            body: Body::Stroke(Stroke { color: Rgb([255, 0, 0]), size: 0.01, points: vec![Point { x: 0.5, y: 0.5, pressure: 1.0 }] }),
        }
    }

    /// A sync reads what is new or changed and names what went, and a
    /// second sync with what the first read finds nothing.
    #[test]
    fn sync_reads_changes_only() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("v0001");
        assert!(sync(&folder, &HashMap::new()).unwrap().changed.is_empty(), "no folder is no marks");
        let (a, b) = (stroke(1001, 10), stroke(1002, 20));
        write(&folder, &a).unwrap();
        write(&folder, &b).unwrap();
        std::fs::write(folder.join(".1003.x.tmp"), b"half").unwrap();
        std::fs::write(folder.join("1004.9-z.json"), br#"{"kind":"arrow"}"#).unwrap();
        let first = sync(&folder, &HashMap::new()).unwrap();
        let names: Vec<&str> = first.changed.iter().map(|c| c.0.as_str()).collect();
        assert_eq!(names, [a.file_name(), b.file_name()]);
        assert_eq!(first.unread.len(), 1);
        let mut held: HashMap<String, Stamp> = first.changed.iter().map(|c| (c.0.clone(), c.1)).collect();
        held.extend(first.unread.iter().cloned());
        let again = sync(&folder, &held).unwrap();
        assert!(again.changed.is_empty() && again.gone.is_empty() && again.unread.is_empty());
        remove(&folder, &a.file_name()).unwrap();
        remove(&folder, &a.file_name()).unwrap();
        let after = sync(&folder, &held).unwrap();
        assert_eq!(after.gone, [a.file_name()]);
    }

    /// A take's marks are its version's under `render:/review/<kind>`, a
    /// media item's under `render:/review/media`, and a live playblast has
    /// no folder.
    /// A mark file another program holds open (Windows refuses to replace
    /// or read it then) is left out of a read until it is let go, and a
    /// change to it is written once it is.
    #[cfg(windows)]
    #[test]
    fn a_file_in_use_is_waited_for() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tempfile::tempdir().unwrap();
        let mut mark = Mark {
            id: new_id(1),
            frame: 7,
            author: "remy".into(),
            created: 1,
            body: Body::Note(Note { text: "first".into(), edited: None }),
        };
        write(dir.path(), &mark).unwrap();
        let path = dir.path().join(mark.file_name());
        // Opened sharing nothing: no other open, rename or delete of it.
        let held = std::fs::OpenOptions::new().read(true).share_mode(0).open(&path).unwrap();
        let changes = sync(dir.path(), &HashMap::new()).unwrap();
        assert!(changes.changed.is_empty() && changes.unread.is_empty(), "{changes:?}");
        let letting_go = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(100));
            drop(held);
        });
        mark.body = Body::Note(Note { text: "second".into(), edited: Some((2, "soren".into())) });
        write(dir.path(), &mark).unwrap();
        letting_go.join().unwrap();
        let changes = sync(dir.path(), &HashMap::new()).unwrap();
        assert_eq!(changes.changed.len(), 1);
        assert_eq!(changes.changed[0].2.note().map(|n| n.text.as_str()), Some("second"));
    }

    #[test]
    fn folders_follow_the_take() {
        let (_dir, root) = crate::testing::make_project();
        let project = Project::open(&root, &root).unwrap();
        let take = |media: th_playlist::Media| Take {
            label: String::new(),
            department: "animation".into(),
            version: "v0012".into(),
            media,
            multi_range: false,
            check: false,
        };
        let render = project.storage.resolve(&Uri::root("render")).unwrap();
        let movie = take_dir(&project, "entity:/shots/SQ010/SH020", &take(th_playlist::Media::Movie { path: "x.mp4".into() })).unwrap();
        assert_eq!(
            movie,
            Some(render.join("review").join("playblast").join("shots").join("SQ010").join("SH020").join("animation").join("v0012"))
        );
        let live = take_dir(
            &project,
            "entity:/shots/SQ010/SH020",
            &take(th_playlist::Media::Live {
                frames: th_playlist::Frames {
                    directory: "x".into(),
                    prefix: "f.".into(),
                    suffix: ".jpg".into(),
                    padding: 4,
                    first: 1,
                    last: 2,
                    step: 1,
                },
            }),
        );
        assert_eq!(live, Ok(None));
        assert_eq!(media_dir(&project, "music", "v0003").unwrap(), render.join("review").join("media").join("music").join("v0003"));
    }
}
