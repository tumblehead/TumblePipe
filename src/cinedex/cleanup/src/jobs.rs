//! Farm job folders (`tumblepipe.prune`, as `tumblepipe.cleanup.jobs`).
//!
//! Every farm submission writes `export/other/jobs/<uuid>/`. A folder goes
//! only when nothing in Deadline can run from it again: every Deadline job
//! naming it is Completed, or none names it any more and it is older than
//! `job_days`. A folder written for the local farm is kept while a batch in
//! this machine's local queue runs from it, and otherwise until it is older
//! than `local_job_days`. When Deadline cannot be asked nothing goes.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;

use crate::core::{
    Category, CategoryPlan, Entry, Farm, Outcome, Payload, Settings, Steer, Watch, each_item, log_line, open_log, stamp_now, user,
};
use crate::fsx;
use crate::py;
use crate::roots::ProjectRoots;

pub const CATEGORY: Category = Category {
    key: "jobs",
    title: "Farm job folders",
    about: "the copies of workfiles made for farm jobs, once neither Deadline nor the local farm can run them again",
    purposes: &[],
};

/// Where farm submissions write their job folders, under the export root.
pub const JOBS_DIR: [&str; 2] = ["other", "jobs"];
/// The one Deadline status from which a job cannot run again by itself.
pub const DONE: &str = "Completed";
/// How long `deadlinecommand` may take to answer.
pub const DEADLINE_TIMEOUT: Duration = Duration::from_secs(600);

pub const PRUNE_COMPLETED: &str = "every job that used it completed";
pub const PRUNE_ORPHANED: &str = "no Deadline job uses it any more";
pub fn reason_keep_active(status: &str) -> String {
    format!("a job that uses it is {status} in Deadline and may be requeued")
}
pub const KEEP_RECENT: &str = "no Deadline job uses it, but it is too recent to be sure";
pub const KEEP_UNKNOWN: &str = "Deadline could not be asked";
pub const KEEP_LOCAL: &str = "a batch in this machine's local farm queue uses it";
pub const KEEP_LOCAL_RECENT: &str = "a local farm job folder: another machine's queue may still use it";

#[derive(Debug, Clone, PartialEq)]
pub struct JobFolder {
    pub name: String,
    pub path: PathBuf,
    pub size: u64,
    pub mtime: f64,
    /// Written for the local farm (its env file says so).
    pub local: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Verdict {
    pub folder: JobFolder,
    pub prune: bool,
    pub reason: String,
}

/// `_UUID.fullmatch(name.lower())`.
pub fn is_uuid(name: &str) -> bool {
    let bytes = name.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(at, b)| match at {
            8 | 13 | 18 | 23 => *b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(b),
        })
}

/// `_JOB_FOLDER_REF.finditer`, case-blind: the uuids of every
/// `[\/]other[\/]jobs[\/]<uuid>[\/]` in `info`, lower-cased.
fn job_folder_refs(info: &str) -> Vec<String> {
    let chars: Vec<char> = info.chars().collect();
    let slash = |c: char| c == '/' || c == '\\';
    // `s` also matches the long s (U+017F) case-blind.
    let letter = |c: char, l: char| c.to_lowercase().eq(std::iter::once(l)) || (l == 's' && c == '\u{17F}');
    let word = |at: usize, text: &str| -> Option<usize> {
        let mut at = at;
        for l in text.chars() {
            if !letter(*chars.get(at)?, l) {
                return None;
            }
            at += 1;
        }
        Some(at)
    };
    let mut out = Vec::new();
    let mut at = 0;
    while at < chars.len() {
        let hit = (|| {
            if !slash(chars[at]) {
                return None;
            }
            let after = word(at + 1, "other")?;
            if !slash(*chars.get(after)?) {
                return None;
            }
            let after = word(after + 1, "jobs")?;
            if !slash(*chars.get(after)?) {
                return None;
            }
            let start = after + 1;
            // The hex digits match either case.
            let uuid: String = chars.get(start..start + 36)?.iter().collect::<String>().to_ascii_lowercase();
            if !is_uuid(&uuid) || !slash(*chars.get(start + 36)?) {
                return None;
            }
            Some((uuid, start + 37))
        })();
        match hit {
            Some((uuid, end)) => {
                out.push(uuid);
                at = end;
            }
            None => at += 1,
        }
    }
    out
}

/// `re.split(r'\r?\n\s*\r?\n', text)`.
fn split_blocks(text: &str) -> Vec<&str> {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut blocks = Vec::new();
    let mut start = 0;
    let mut at = 0;
    while at < chars.len() {
        let first = if chars[at].1 == '\r' && chars.get(at + 1).map(|c| c.1) == Some('\n') {
            at + 1
        } else if chars[at].1 == '\n' {
            at
        } else {
            at += 1;
            continue;
        };
        let run_end = chars[first + 1..].iter().take_while(|(_, c)| py::is_space(*c)).count() + first + 1;
        match (first + 1..run_end).rev().find(|i| chars[*i].1 == '\n') {
            Some(last) => {
                blocks.push(&text[start..chars[at].0]);
                start = chars.get(last + 1).map_or(text.len(), |c| c.0);
                at = last + 1;
            }
            None => at += 1,
        }
    }
    blocks.push(&text[start..]);
    blocks
}

/// `deadlinecommand -GetJobsFilter` output -> {folder uuid: [statuses]}
/// (`prune.parse_jobs`).
pub fn parse_jobs(text: &str) -> HashMap<String, Vec<String>> {
    let mut folders: HashMap<String, Vec<String>> = HashMap::new();
    for block in split_blocks(text) {
        let mut fields: HashMap<String, String> = HashMap::new();
        for line in py::splitlines(block) {
            let Some((key, value)) = line.split_once('=') else { continue };
            // Python checks the key as written but stores it stripped.
            if !key.is_empty() && !fields.contains_key(key) {
                fields.insert(py::strip(key).to_string(), value.to_string());
            }
        }
        let status = fields.get("JobStatus").filter(|s| !s.is_empty()).or_else(|| fields.get("Status"));
        let info = fields.get("PluginInfoDictionary").map(String::as_str).unwrap_or("");
        let Some(status) = status.filter(|s| !s.is_empty()) else { continue };
        if info.is_empty() {
            continue;
        }
        let mut seen = HashSet::new();
        for uuid in job_folder_refs(info) {
            if seen.insert(uuid.clone()) {
                folders.entry(uuid).or_default().push(py::strip(status).to_string());
            }
        }
    }
    folders
}

/// This machine's `deadlinecommand`, found as `tumblepipe.apps.deadline`
/// finds it: in `$DEADLINE_PATH`, which must exist. Shared with the
/// editor's farm badges (`th_pipe::farm_jobs`).
pub use th_pipe::farm_jobs::deadline_command;

/// Every TumblePipe (HPM plugin) job Deadline holds, by job folder
/// (`prune.query_deadline`). The error says why there is none.
pub fn query_deadline(command: &Path, timeout: Duration) -> Result<HashMap<String, Vec<String>>, String> {
    let th_pipe::farm_jobs::Output { status, stdout, stderr } =
        th_pipe::farm_jobs::run(command, &["-GetJobsFilter", "PluginName=HPM"], timeout)?;
    if !status.success() {
        let code = status.code().map_or_else(|| "by a signal".to_string(), |c| c.to_string());
        let detail: String = py::strip(&stderr).chars().take(300).collect();
        return Err(format!("deadlinecommand exited {code}: {detail}"));
    }
    let jobs = parse_jobs(&stdout);
    if jobs.is_empty() {
        return Err("Deadline reported no TumblePipe jobs at all".into());
    }
    Ok(jobs)
}

/// The job folders (uuids) the batches in the local farm queue at `root`
/// run from (`prune.local_queue_folders`). A batch that cannot be read
/// names none.
pub fn local_queue_folders(root: &Path) -> Result<HashSet<String>, String> {
    let mut names = HashSet::new();
    let folder = root.join("batches");
    if !folder.is_dir() {
        return Ok(names);
    }
    let listing = std::fs::read_dir(&folder).map_err(|e| e.to_string())?;
    for entry in listing {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let batch_file = path.join(th_pipe::previews::BATCH_NAME);
        if !path.is_dir() || !batch_file.is_file() {
            continue;
        }
        let Ok(bytes) = std::fs::read(&batch_file) else { continue };
        let Ok(Value::Object(batch)) = serde_json::from_slice::<Value>(&bytes) else { continue };
        if batch.get("version").and_then(Value::as_i64) != Some(th_pipe::previews::BATCH_VERSION)
            || batch.get("version").is_some_and(Value::is_f64)
        {
            continue;
        }
        if let Some(job_dir) = batch.get("job_dir").filter(|v| py::truthy(v)) {
            let text = py::str_of(job_dir).replace('\\', "/");
            let text = text.trim_end_matches('/');
            names.insert(text.rsplit('/').next().unwrap_or("").to_lowercase());
        }
    }
    Ok(names)
}

/// The farm as this machine sees it: Deadline through `deadlinecommand`,
/// the local farm through its queue folder.
pub struct LiveFarm;

impl Farm for LiveFarm {
    fn deadline_jobs(&self) -> Result<HashMap<String, Vec<String>>, String> {
        let Some(command) = deadline_command() else {
            return Err("Deadline is not installed on this machine: no job folder can be deleted safely.".into());
        };
        query_deadline(&command, DEADLINE_TIMEOUT)
            .map_err(|error| format!("Deadline could not be asked ({error}): no job folder will be deleted."))
    }

    fn local_folders(&self) -> Result<HashSet<String>, String> {
        local_queue_folders(&th_pipe::previews::this_farm_root())
    }
}

/// Whether `folder` was written for the local farm: one of its `*.env`
/// files carries `TH_FARM_BACKEND=local` (`prune.is_local_job_folder`).
pub fn is_local_job_folder(folder: &Path) -> bool {
    let Ok(listing) = std::fs::read_dir(folder) else { return false };
    for entry in listing.filter_map(Result::ok) {
        let name = fsx::name_of(&entry.file_name());
        // `Path.glob('*.env')`: case-blind on Windows.
        let matches = if cfg!(windows) { name.to_lowercase().ends_with(".env") } else { name.ends_with(".env") };
        if !matches {
            continue;
        }
        let Ok(bytes) = std::fs::read(entry.path()) else { continue };
        let text = String::from_utf8_lossy(&bytes);
        for line in py::splitlines(&text) {
            if let Some((key, value)) = line.split_once('=')
                && py::strip(key) == "TH_FARM_BACKEND"
                && py::strip(value).to_lowercase() == "local"
            {
                return true;
            }
        }
    }
    false
}

/// The submission folders: uuid-named folders, nothing else
/// (`prune.list_job_folders`).
/// `goes` says whether a measured folder is to go, for the found tally.
pub fn list_job_folders(jobs_path: &Path, watch: &Watch<'_>, goes: &(dyn Fn(&JobFolder) -> bool + Sync)) -> Result<Vec<JobFolder>, String> {
    let Ok(listing) = std::fs::read_dir(jobs_path) else { return Ok(Vec::new()) };
    let mut paths: Vec<PathBuf> = listing.filter_map(Result::ok).map(|e| e.path()).collect();
    fsx::sort_paths(&mut paths);
    // Each folder measured on its own: `watch.threads` at a time.
    let meter = watch.meter(Some(CATEGORY.key), "Measuring job folders", Some(paths.len()));
    let folders = fsx::par_map(&paths, watch.threads, |path| {
        let name = path.file_name().map(fsx::name_of).unwrap_or_default();
        let link = std::fs::symlink_metadata(path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if watch.cancelled() || !path.is_dir() || link || !is_uuid(&name.to_lowercase()) {
            meter.add(1, 0);
            return None;
        }
        let size = fsx::tree_size(path);
        let folder = JobFolder { size, mtime: fsx::mtime(path).unwrap_or(0.0), local: is_local_job_folder(path), name, path: path.clone() };
        if goes(&folder) {
            meter.found(size);
        }
        meter.add(1, size);
        Some(folder)
    });
    meter.finish();
    watch.check()?;
    Ok(folders.into_iter().flatten().collect())
}

/// `prune.decide`.
pub fn decide(
    folder: &JobFolder,
    statuses: Option<&Vec<String>>,
    now: f64,
    settings: &Settings,
    deadline_known: bool,
    local_folders: &HashSet<String>,
) -> (bool, String) {
    if !deadline_known {
        return (false, KEEP_UNKNOWN.into());
    }
    if let Some(statuses) = statuses.filter(|s| !s.is_empty()) {
        if let Some(live) = statuses.iter().find(|s| *s != DONE) {
            return (false, reason_keep_active(live));
        }
        return (true, PRUNE_COMPLETED.into());
    }
    if local_folders.contains(&folder.name.to_lowercase()) {
        return (false, KEEP_LOCAL.into());
    }
    if folder.local && now - folder.mtime < settings.job_days.max(settings.local_job_days) * 86400.0 {
        return (false, KEEP_LOCAL_RECENT.into());
    }
    if now - folder.mtime < settings.job_days * 86400.0 {
        return (false, KEEP_RECENT.into());
    }
    (true, PRUNE_ORPHANED.into())
}

/// The category's plan (`cleanup.jobs.plan`).
pub fn plan(roots: &ProjectRoots, settings: &Settings, farm: &dyn Farm, now: f64, watch: &Watch<'_>) -> Result<CategoryPlan, String> {
    let mut jobs_path = roots.export_path();
    jobs_path.extend(JOBS_DIR);
    let mut out = CategoryPlan::new(CATEGORY.key, CATEGORY.title, "folder");
    out.payload = Payload::Jobs { jobs_path: jobs_path.clone(), verdicts: Vec::new() };
    if !jobs_path.is_dir() {
        return Ok(out);
    }
    watch.meter(Some(CATEGORY.key), "Asking the farm", None);
    let jobs = match farm.deadline_jobs() {
        Ok(jobs) => Some(jobs),
        Err(problem) => {
            out.notes.push(problem);
            None
        }
    };
    let local = farm.local_folders().unwrap_or_else(|error| {
        out.notes.push(format!("This machine's local farm queue could not be read ({error})."));
        HashSet::new()
    });
    let verdict_of = |folder: &JobFolder| {
        let statuses = jobs.as_ref().and_then(|jobs| jobs.get(&folder.name.to_lowercase()));
        decide(folder, statuses, now, settings, jobs.is_some(), &local)
    };
    let verdicts: Vec<Verdict> = list_job_folders(&jobs_path, watch, &|folder| verdict_of(folder).0)?
        .into_iter()
        .map(|folder| {
            let (prune, reason) = verdict_of(&folder);
            Verdict { folder, prune, reason }
        })
        .collect();
    for verdict in &verdicts {
        let reason =
            if verdict.reason.starts_with("a job that uses it is") { reason_keep_active("not completed") } else { verdict.reason.clone() };
        out.entries.push(Entry {
            path: verdict.folder.path.clone(),
            label: format!("other/jobs/{}", verdict.folder.name),
            group: "other/jobs".into(),
            size: verdict.folder.size,
            prune: verdict.prune,
            reason,
        });
    }
    out.payload = Payload::Jobs { jobs_path, verdicts };
    Ok(out)
}

/// Delete the folders marked to go, each only a uuid folder directly in the
/// jobs folder (`prune.apply`).
pub fn apply(category: &CategoryPlan, log_path: &Path, steer: &Steer<'_>) -> Result<Outcome, String> {
    let Payload::Jobs { jobs_path, verdicts } = &category.payload else { return Ok((0, 0, Vec::new())) };
    let mut log = open_log(log_path)?;
    log_line(&mut log, &format!("# prune {} by {}", stamp_now(), user()))?;
    let jobs_path = fsx::resolve(jobs_path);
    each_item(steer, &category.key, verdicts.iter().filter(|v| v.prune), |verdict| {
        let path = &verdict.folder.path;
        let parent = path.parent().unwrap_or(Path::new(""));
        let name = path.file_name().map(fsx::name_of).unwrap_or_default();
        if !fsx::same_path(&fsx::resolve(parent), &jobs_path) || !is_uuid(&name.to_lowercase()) {
            return Ok(Err(format!("{}: not a job folder, left alone", path.display())));
        }
        if let Err(error) = std::fs::remove_dir_all(path) {
            return Ok(Err(format!("{}: {error}", path.display())));
        }
        log_line(&mut log, &format!("{}\t{}\t{}", verdict.folder.name, verdict.folder.size, verdict.reason))?;
        Ok(Ok(verdict.folder.size))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "0123abcd-0000-4000-8000-00000000000a";
    const B: &str = "0123abcd-0000-4000-8000-00000000000b";

    #[test]
    fn jobs_are_read_by_folder() {
        let text = format!(
            "JobStatus=Completed\nPluginInfoDictionary=StartupDirectory=P:/x/other/jobs/{A}/;Manifest=Q:\\x\\OTHER\\JOBS\\{}\\m\n\
             \n  \n\
             Status= Suspended \nPluginInfoDictionary=x/other/jobs/{A}/ y/other/jobs/{B}\\\n\
             \n\
             JobStatus=Queued\n",
            A.to_uppercase()
        );
        let jobs = parse_jobs(&text);
        assert_eq!(jobs[A], vec!["Completed", "Suspended"]);
        assert_eq!(jobs[B], vec!["Suspended"]);
        assert_eq!(jobs.len(), 2);
    }

    #[test]
    fn blocks_split_as_python() {
        assert_eq!(split_blocks("a\n\nb"), vec!["a", "b"]);
        assert_eq!(split_blocks("a\r\n \r\n\n b\nc"), vec!["a", " b\nc"]);
        assert_eq!(split_blocks("a\nb"), vec!["a\nb"]);
        assert_eq!(split_blocks("\n\n"), vec!["", ""]);
    }

    #[test]
    fn uuids() {
        assert!(is_uuid(A));
        assert!(!is_uuid(&A.to_uppercase()));
        assert!(!is_uuid("0123abcd-0000-4000-8000-00000000000"));
    }
}
