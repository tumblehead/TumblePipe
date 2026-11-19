//! Files on their way into the media library: the ones picked in the
//! system's file dialog (Import…) or dropped on the window.
//!
//! [`plan`] sorts them: each movie and sound is an import under a name
//! made from the file's (`editing::item_name_of`), unique among the files
//! given; a frame of a numbered image sequence stands for the whole
//! sequence, once; a folder, or a file of no kind the player reads, is
//! unsupported. [`import_all`] imports the plan's files one by one through
//! whatever imports a file (the editor gives it the library's own
//! `th_pipe::media::import`, as the old Import dialog did), and the
//! [`Report`] names everything that did not go in, with why: nothing is
//! passed over without a word.
//!
//! The library holds one file per item version (`th_pipe::media`), so an
//! image sequence has nowhere to go yet: it is recognised and named in the
//! report as not imported, with what to do instead.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

/// Extensions of what the library takes, lower case: movies and sound.
pub const MEDIA: &[&str] =
    &["mov", "mp4", "m4v", "mkv", "avi", "webm", "mxf", "wav", "mp3", "aac", "flac", "ogg", "m4a", "aif", "aiff", "opus"];
/// Extensions of single pictures, which come as numbered sequences.
pub const STILLS: &[&str] = &["exr", "jpg", "jpeg", "png", "tif", "tiff", "dpx", "tga", "bmp"];

fn extension(path: &Path) -> String {
    path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default()
}

fn file_name(path: &Path) -> String {
    path.file_name().map_or_else(|| path.to_string_lossy().into_owned(), |n| n.to_string_lossy().into_owned())
}

/// A file name that ends in a number before its extension, as a frame of a
/// sequence does: `plate.0101.exr` is `plate.`, 101, `exr`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Numbered {
    pub prefix: String,
    pub number: u64,
    pub extension: String,
}

/// `name` as a numbered file; None when its stem does not end in digits
/// (or in more of them than a number holds).
pub fn numbered(name: &str) -> Option<Numbered> {
    let (stem, extension) = name.rsplit_once('.')?;
    let digits = stem.chars().rev().take_while(char::is_ascii_digit).count();
    if digits == 0 || extension.is_empty() {
        return None;
    }
    let (prefix, number) = stem.split_at(stem.len() - digits);
    Some(Numbered { prefix: prefix.to_owned(), number: number.parse().ok()?, extension: extension.to_lowercase() })
}

/// An image sequence found on disk: where, what its frames are called,
/// how many there are and from which number to which.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frames {
    pub directory: PathBuf,
    pub prefix: String,
    pub extension: String,
    pub count: usize,
    pub first: u64,
    pub last: u64,
}

impl Frames {
    /// The sequence in words: `plate.####.exr (48 frames, 1001-1048)`.
    pub fn words(&self) -> String {
        format!("{}####.{} ({} frames, {}-{})", self.prefix, self.extension, self.count, self.first, self.last)
    }
}

/// What to do with the files given.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Plan {
    /// Each file to import, and the item it becomes.
    pub files: Vec<(PathBuf, String)>,
    /// The image sequences among them, each once.
    pub sequences: Vec<Frames>,
    /// What is none of those: its name, and what it is.
    pub unsupported: Vec<(String, &'static str)>,
}

pub const A_FOLDER: &str = "a folder";
pub const NOT_MEDIA: &str = "not a movie or a sound";

/// A name for a new item made from `file`'s name, not one of `taken`: the
/// file's letters and digits (`media` when it has none, or they spell a
/// name Windows reserves), with the first number from 2 that makes it new.
fn item_name(file: &Path, taken: &BTreeSet<String>) -> String {
    let mut base = crate::editing::item_name_of(&file.to_string_lossy());
    if th_pipe::naming::entity_name_problem(&base).is_some() {
        base = "media".to_owned();
    }
    if !taken.contains(&base) {
        return base;
    }
    (2..).map(|n| format!("{base}{n}")).find(|name| !taken.contains(name)).expect("a free name")
}

/// Sort `paths` (as picked or dropped, in that order). `is_dir` says
/// whether a path is a folder, and `listing` names the files of a folder:
/// one frame of a numbered sequence brings the whole sequence, so a
/// picture is looked for among its neighbours.
pub fn plan(paths: &[PathBuf], is_dir: impl Fn(&Path) -> bool, listing: impl Fn(&Path) -> Vec<String>) -> Plan {
    let mut plan = Plan::default();
    let mut seen: BTreeSet<&Path> = BTreeSet::new();
    let mut names: BTreeSet<String> = BTreeSet::new();
    let mut sequences: BTreeSet<(PathBuf, String, String)> = BTreeSet::new();
    for path in paths {
        if !seen.insert(path.as_path()) {
            continue;
        }
        let extension = extension(path);
        if is_dir(path) {
            plan.unsupported.push((file_name(path), A_FOLDER));
            continue;
        }
        let mut import = |plan: &mut Plan| {
            let name = item_name(path, &names);
            names.insert(name.clone());
            plan.files.push((path.clone(), name));
        };
        if MEDIA.contains(&extension.as_str()) {
            import(&mut plan);
        } else if STILLS.contains(&extension.as_str()) {
            let directory = path.parent().map(Path::to_path_buf).unwrap_or_default();
            let found = numbered(&file_name(path)).and_then(|frame| {
                let numbers: BTreeSet<u64> = listing(&directory)
                    .iter()
                    .filter_map(|name| numbered(name))
                    .filter(|other| other.prefix == frame.prefix && other.extension == frame.extension)
                    .map(|other| other.number)
                    .chain([frame.number])
                    .collect();
                (numbers.len() > 1).then(|| Frames {
                    directory: directory.clone(),
                    prefix: frame.prefix.clone(),
                    extension: frame.extension.clone(),
                    count: numbers.len(),
                    first: *numbers.first().expect("the frame given"),
                    last: *numbers.last().expect("the frame given"),
                })
            });
            match found {
                // Each sequence once, however many of its frames came.
                Some(frames) => {
                    if sequences.insert((frames.directory.clone(), frames.prefix.clone(), frames.extension.clone())) {
                        plan.sequences.push(frames);
                    }
                }
                // A picture on its own: the importer says what it makes of it.
                None => import(&mut plan),
            }
        } else {
            plan.unsupported.push((file_name(path), NOT_MEDIA));
        }
    }
    plan
}

/// [`plan`] of files on this machine.
pub fn plan_on_disk(paths: &[PathBuf]) -> Plan {
    plan(paths, Path::is_dir, |directory| {
        std::fs::read_dir(directory)
            .map(|entries| entries.filter_map(Result::ok).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default()
    })
}

/// What became of the files given.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Report {
    /// The items made, each with its new version, in the order given.
    pub imported: Vec<(String, String)>,
    /// What did not go in: its name, and why.
    pub not: Vec<(String, String)>,
}

impl Report {
    /// The items made, by name.
    #[cfg(test)]
    pub fn items(&self) -> Vec<String> {
        self.imported.iter().map(|i| i.0.clone()).collect()
    }
}

/// Import `plan`'s files with `import` (a file and its item's name: the
/// new version, or why not), each on its own: one that fails does not stop
/// the rest. Everything else in the plan is in the report as not imported.
pub fn import_all(plan: &Plan, mut import: impl FnMut(&Path, &str) -> Result<String, String>) -> Report {
    let mut report = Report::default();
    for (file, item) in &plan.files {
        match import(file, item) {
            Ok(version) => report.imported.push((item.clone(), version)),
            Err(why) => report.not.push((file_name(file), why)),
        }
    }
    for frames in &plan.sequences {
        report.not.push((
            frames.words(),
            "an image sequence, which the media library cannot hold yet: make a movie of it and import that".to_owned(),
        ));
    }
    for (name, what) in &plan.unsupported {
        report.not.push((name.clone(), (*what).to_owned()));
    }
    report
}

/// `report` for the status line, and whether it is an error (something
/// did not go in). Everything is named.
pub fn words(report: &Report) -> (String, bool) {
    let imported = report.imported.iter().map(|(item, version)| format!("{item} {version}")).collect::<Vec<_>>().join(", ");
    let not = report.not.iter().map(|(name, why)| format!("{name} ({why})")).collect::<Vec<_>>().join("; ");
    match (report.imported.is_empty(), report.not.is_empty()) {
        (true, true) => ("Nothing to import".to_owned(), false),
        (false, true) => (format!("Imported {imported}"), false),
        (true, false) => (format!("Not imported: {not}"), true),
        (false, false) => (format!("Imported {imported}. Not imported: {not}"), true),
    }
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use super::*;

    /// A file given: (which kind, a number for its name).
    #[derive(Debug, Clone)]
    enum Given {
        Movie(String),
        Sound(String),
        Other(String),
        Folder(String),
        /// A frame of sequence `0..3`, by its index among that sequence's.
        Frame(usize, prop::sample::Index),
        /// A picture with no neighbours.
        Lone(String),
    }

    fn stem() -> impl Strategy<Value = String> {
        prop_oneof!["[a-zA-Z][a-zA-Z0-9]{0,5}", "[a-z]{1,3}[ _.-][a-z0-9]{1,3}", Just("---".to_owned()), Just("nul".to_owned())]
    }

    fn given() -> impl Strategy<Value = Given> {
        prop_oneof![
            stem().prop_map(Given::Movie),
            stem().prop_map(Given::Sound),
            stem().prop_map(Given::Other),
            stem().prop_map(Given::Folder),
            (0usize..3, any::<prop::sample::Index>()).prop_map(|(s, i)| Given::Frame(s, i)),
            "[a-z]{1,4}".prop_map(Given::Lone),
        ]
    }

    proptest! {
        /// Every file given is accounted for, once: imported under a valid
        /// name no other file of the batch has, part of a sequence named
        /// once whole (however many of its frames were given, and however
        /// many it has on disk), or named as unsupported with what it is.
        #[test]
        fn every_file_given_is_accounted_for(
            given in prop::collection::vec(given(), 0..10), sizes in prop::collection::vec(2usize..6, 3), first in 1u64..2000, repeat in any::<bool>(),
        ) {
            // The disk, in the test's own words: three sequences in /seq.
            let frame_name = |s: usize, n: u64| format!("plate{}.{:04}.exr", ["A", "B", "C"][s], n);
            let on_disk: Vec<String> = (0..3).flat_map(|s| (0..sizes[s] as u64).map(move |i| (s, i))).map(|(s, i)| frame_name(s, first + i)).collect();
            let mut paths: Vec<PathBuf> = Vec::new();
            let (mut media, mut other, mut folders, mut frames_of) = (Vec::new(), Vec::new(), BTreeSet::new(), BTreeSet::new());
            for (at, g) in given.iter().enumerate() {
                let path = match g {
                    Given::Movie(stem) => PathBuf::from(format!("/in/{at}/{stem}.MOV")),
                    Given::Sound(stem) => PathBuf::from(format!("/in/{at}/{stem}.wav")),
                    Given::Other(stem) => PathBuf::from(format!("/in/{at}/{stem}.txt")),
                    Given::Folder(stem) => PathBuf::from(format!("/in/{at}/{stem}.d")),
                    Given::Frame(s, i) => PathBuf::from(format!("/seq/{}", frame_name(*s, first + i.index(sizes[*s]) as u64))),
                    Given::Lone(stem) => PathBuf::from(format!("/in/{at}/{stem}.0001.png")),
                };
                match g {
                    Given::Movie(_) | Given::Sound(_) | Given::Lone(_) => media.push(path.clone()),
                    Given::Other(_) => other.push(path.clone()),
                    Given::Folder(_) => { folders.insert(path.clone()); }
                    Given::Frame(s, _) => { frames_of.insert(*s); }
                }
                paths.push(path);
            }
            if repeat {
                paths.extend(paths.clone());
            }
            let is_dir = |p: &Path| folders.contains(p);
            let listing = |d: &Path| if d == Path::new("/seq") { on_disk.clone() } else { vec!["0001.png".to_owned(), "notes.txt".to_owned()] };
            let plan = plan(&paths, is_dir, listing);
            prop_assert_eq!(plan.files.iter().map(|f| f.0.clone()).collect::<Vec<_>>(), media, "the movies, sounds and lone pictures, in the order given");
            let names: Vec<&String> = plan.files.iter().map(|f| &f.1).collect();
            prop_assert_eq!(names.iter().collect::<BTreeSet<_>>().len(), names.len(), "two files with one name: {:?}", names);
            for name in names {
                prop_assert_eq!(th_pipe::naming::entity_name_problem(name), None, "{}", name);
            }
            prop_assert_eq!(plan.sequences.len(), frames_of.len(), "each sequence once");
            for frames in &plan.sequences {
                let s = ["plateA.", "plateB.", "plateC."].iter().position(|p| *p == frames.prefix).expect("a sequence on disk");
                prop_assert!(frames_of.contains(&s));
                prop_assert_eq!((frames.count, frames.first, frames.last), (sizes[s], first, first + sizes[s] as u64 - 1), "the whole sequence");
                prop_assert_eq!(&frames.directory, Path::new("/seq"));
                let count = format!("{} frames", sizes[s]);
                prop_assert!(frames.words().contains(&count) && frames.words().starts_with(&frames.prefix));
            }
            let mut unsupported: Vec<(String, &str)> = other.iter().map(|p| (file_name(p), NOT_MEDIA)).chain(folders.iter().map(|p| (file_name(p), A_FOLDER))).collect();
            let mut got = plan.unsupported.clone();
            unsupported.sort();
            got.sort();
            prop_assert_eq!(got, unsupported);

            // Imported one by one: a file that fails is named with why, the
            // rest still go in, and the words name everything.
            let fails = |file: &Path| file.to_string_lossy().len().is_multiple_of(3);
            let report = import_all(&plan, |file, item| if fails(file) { Err(format!("cannot read {item}")) } else { Ok("v0001".to_owned()) });
            let went: Vec<&(PathBuf, String)> = plan.files.iter().filter(|f| !fails(&f.0)).collect();
            prop_assert_eq!(report.items(), went.iter().map(|f| f.1.clone()).collect::<Vec<_>>());
            prop_assert_eq!(report.not.len(), plan.files.len() - went.len() + plan.sequences.len() + plan.unsupported.len());
            let (said, error) = words(&report);
            prop_assert_eq!(error, !report.not.is_empty());
            for (item, version) in &report.imported {
                let went = format!("{item} {version}");
                prop_assert!(said.contains(&went), "{} is not in {:?}", item, said);
            }
            for (name, why) in &report.not {
                let stayed = format!("{name} ({why})");
                prop_assert!(said.contains(&stayed), "{} is not in {:?}", name, said);
            }
            for file in plan.files.iter().filter(|f| fails(&f.0)) {
                prop_assert!(said.contains(&file_name(&file.0)), "{:?}", said);
            }
            for frames in &plan.sequences {
                prop_assert!(said.contains(&frames.words()) && said.contains("image sequence"), "{:?}", said);
            }
        }

        /// A numbered name reads back as it was written, whatever its
        /// padding; a name that ends in no number is not numbered.
        #[test]
        fn a_numbered_name_reads_back(prefix in "[a-z]{0,5}[._-]?", number in 0u64..100_000, pad in 1usize..7, extension in "[a-z]{2,4}") {
            let name = format!("{prefix}{number:0pad$}.{extension}");
            prop_assert_eq!(numbered(&name), Some(Numbered { prefix: prefix.clone(), number, extension: extension.clone() }));
            prop_assert_eq!(numbered(&format!("{prefix}x.{extension}")), None);
            prop_assert_eq!(numbered(&format!("{prefix}{number}")), None, "no extension");
        }
    }

    #[test]
    fn names_come_from_the_file_and_are_new() {
        let taken: BTreeSet<String> = ["cut3".to_owned(), "cut32".to_owned()].into();
        assert_eq!(item_name(Path::new("C:/in/cut 3.mov"), &BTreeSet::new()), "cut3");
        assert_eq!(item_name(Path::new("C:/in/cut 3.mov"), &taken), "cut33");
        assert_eq!(item_name(Path::new("/in/---.wav"), &BTreeSet::new()), "media");
        assert_eq!(item_name(Path::new("/in/nul.wav"), &BTreeSet::new()), "media");
        assert_eq!(words(&Report::default()), ("Nothing to import".to_owned(), false));
    }
}
