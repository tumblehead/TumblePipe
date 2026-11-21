//! Which shots have work on the farm: Deadline's jobs and this machine's
//! local farm queue, read as the Farm Manager reads them
//! (`tumblepipe.farm.manager.rows`), and what each shot's clip shows.
//!
//! Every job TumblePipe submits is stamped with its project and entity
//! (`tumblepipe.farm.stamp`: `TumblePipeProject` / `TumblePipeEntity`, a
//! Deadline job's `ExtraInfoKeyValue` pairs, read back as `Props.ExDic`; a
//! local job's `batch.json` entry's `extra_info`). A job from before the
//! stamp falls back on its batch's name, `<project> <kinds...> <entity uri>
//! <timestamp>` (`pipelines._batch.batch_title`), as the Farm Manager does
//! (`rows.row_project`, `rows.row_entity`).
//!
//! What a shot shows ([`ShotJobs::of`]): of the jobs of this project
//! stamped with the shot, the newest batch (the latest submission) decides.
//! Its most urgent job is the shot's: failed, then rendering, suspended,
//! queued, waiting on another job (pending). A batch that has ended
//! (completed or cancelled) shows nothing, so a shot whose newest
//! submission finished shows nothing even when an older one failed.
//!
//! Deadline is asked through `deadlinecommand -Json GetJobs` (the Farm
//! Manager's `deadline_jobs.fetch_jobs`), found through `$DEADLINE_PATH`
//! as `tumblepipe.apps.deadline` finds it ([`deadline_command`], which
//! Clean Up Project's job folders also use). Nothing here changes the farm.
//! A farm that cannot be asked answers nothing: no Deadline on this
//! machine, a `deadlinecommand` that fails or takes longer than its
//! timeout, or a queue folder that is not there all mean no jobs.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::previews::{BATCH_NAME, BATCH_VERSION, STATE_NAME};

/// The stamp's keys (`farm.stamp.PROJECT`, `farm.stamp.ENTITY`).
pub const STAMP_PROJECT: &str = "TumblePipeProject";
pub const STAMP_ENTITY: &str = "TumblePipeEntity";
/// A local job's id: `local:<batch id>/<job index>` (`farm.local.backend`).
pub const LOCAL_PREFIX: &str = "local:";
/// How long the editor waits for `deadlinecommand` before giving up on
/// one read: `GetJobs` takes about a second for a few hundred jobs.
pub const DEADLINE_TIMEOUT: Duration = Duration::from_secs(30);

/// Where a job is queued.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Source {
    Deadline,
    Local,
}

/// A job's state, in the Farm Manager's vocabulary (`rows.STATES`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Status {
    Rendering,
    Queued,
    /// Waiting on a job it depends on.
    Pending,
    Suspended,
    Failed,
    Completed,
    Cancelled,
    Unknown,
}

impl Status {
    /// Every state, for tests.
    pub const ALL: [Status; 8] = [
        Status::Rendering,
        Status::Queued,
        Status::Pending,
        Status::Suspended,
        Status::Failed,
        Status::Completed,
        Status::Cancelled,
        Status::Unknown,
    ];

    /// Whether a clip shows it: work still to come (rendering, queued,
    /// waiting, suspended) or a failure to look at.
    pub fn shown(self) -> bool {
        self.urgency() > 0
    }

    /// Which of a batch's jobs speaks for it: the higher, the more a person
    /// needs to know. 0 for what is not shown.
    pub fn urgency(self) -> u8 {
        match self {
            Status::Failed => 5,
            Status::Rendering => 4,
            Status::Suspended => 3,
            Status::Queued => 2,
            Status::Pending => 1,
            Status::Completed | Status::Cancelled | Status::Unknown => 0,
        }
    }

    /// The word a clip's badge and menu say.
    pub fn word(self) -> &'static str {
        match self {
            Status::Rendering => "Rendering",
            Status::Queued => "Queued",
            Status::Pending => "Waiting",
            Status::Suspended => "Suspended",
            Status::Failed => "Failed",
            Status::Completed => "Completed",
            Status::Cancelled => "Cancelled",
            Status::Unknown => "Unknown",
        }
    }

    /// A local job's state from its tasks' (`queue.job_status`).
    pub fn of_local_tasks(tasks: &[&str], deps_completed: bool) -> Status {
        let has = |s: &str| tasks.contains(&s);
        if has("rendering") {
            Status::Rendering
        } else if has("failed") {
            Status::Failed
        } else if has("queued") {
            if deps_completed { Status::Queued } else { Status::Pending }
        } else if has("suspended") {
            Status::Suspended
        } else if has("cancelled") {
            Status::Cancelled
        } else {
            Status::Completed
        }
    }

    /// A Deadline job's `Stat` code (`rows.deadline_job_status`): active
    /// is rendering while a task renders, else queued.
    pub fn of_deadline(stat: i64, rendering_chunks: i64) -> Status {
        match stat {
            1 if rendering_chunks > 0 => Status::Rendering,
            1 => Status::Queued,
            2 => Status::Suspended,
            3 => Status::Completed,
            4 => Status::Failed,
            6 => Status::Pending,
            _ => Status::Unknown,
        }
    }
}

/// One job, from either farm.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Job {
    pub source: Source,
    /// Deadline's job id, or `local:<batch>/<index>`.
    pub id: String,
    /// Groups the jobs of one submission: `deadline:<batch name>` or
    /// `local:<batch id>`.
    pub batch_key: String,
    /// The batch's name (`<project> <kinds...> <entity> <timestamp>`).
    pub batch: String,
    pub name: String,
    /// The stamp's project and entity; '' where the job has none.
    pub project: String,
    pub entity: String,
    pub status: Status,
    /// When it was submitted, seconds since 1970 (0 when not known).
    pub submitted: i64,
}

impl Job {
    /// The project it is for: its stamp, else its batch name's first word
    /// (`rows.row_project`).
    pub fn project(&self) -> &str {
        if self.project.is_empty() { self.batch.split(' ').next().unwrap_or("") } else { &self.project }
    }

    /// The entity URI it is for: its stamp, else the one its batch's name
    /// carries (`rows.row_entity`); '' when neither says.
    pub fn entity(&self) -> &str {
        if self.entity.is_empty() { name_entity(&self.batch) } else { &self.entity }
    }
}

/// The entity URI a batch's name carries (`rows.name_entity`, the regex
/// `\bentity:/(?:shots|assets|groups|edits)/\S+`); '' when none.
pub fn name_entity(name: &str) -> &str {
    let word = |c: char| c.is_alphanumeric() || c == '_';
    for (at, _) in name.match_indices("entity:/") {
        if name[..at].chars().next_back().is_some_and(word) {
            continue;
        }
        let rest = &name[at + "entity:/".len()..];
        let Some(kind) = ["shots/", "assets/", "groups/", "edits/"].into_iter().find(|k| rest.starts_with(k)) else { continue };
        let tail = &rest[kind.len()..];
        let more = tail.find(char::is_whitespace).unwrap_or(tail.len());
        if more > 0 {
            return &name[at..at + "entity:/".len() + kind.len() + more];
        }
    }
    ""
}

/// The shot a URI names, as `entity:/shots/<sequence>/<shot>`, so a job's
/// entity and a clip's shot compare; None for anything but a shot.
pub fn shot_key(uri: &str) -> Option<String> {
    let uri = crate::Uri::parse(uri.trim().trim_end_matches('/')).ok()?;
    (uri.purpose == "entity" && uri.segments.len() == 3 && uri.segments[0] == "shots" && !uri.is_wild())
        .then(|| format!("entity:/{}", uri.segments.join("/")))
}

/// What a shot's clip shows: the job that speaks for its newest batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShotJob {
    pub source: Source,
    pub id: String,
    pub name: String,
    pub status: Status,
}

/// The shots of one project with work on the farm.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ShotJobs {
    by_shot: BTreeMap<String, ShotJob>,
}

impl ShotJobs {
    /// What each shot of `project` shows, of `jobs` (see the module's
    /// rules). Jobs of another project, or of anything but a shot, are
    /// left out; so is every job when `project` is ''.
    pub fn of(project: &str, jobs: &[Job]) -> Self {
        let mut by_shot: BTreeMap<String, BTreeMap<&str, Vec<&Job>>> = BTreeMap::new();
        for job in jobs.iter().filter(|j| !project.is_empty() && j.project() == project) {
            let Some(shot) = shot_key(job.entity()) else { continue };
            by_shot.entry(shot).or_default().entry(job.batch_key.as_str()).or_default().push(job);
        }
        let by_shot = by_shot
            .into_iter()
            .filter_map(|(shot, batches)| {
                // The newest batch: its latest job's submission, then its key.
                let (_, newest) = batches.into_iter().max_by_key(|(key, jobs)| (jobs.iter().map(|j| j.submitted).max(), *key))?;
                let speaker = newest.into_iter().max_by_key(|j| (j.status.urgency(), j.submitted, j.id.as_str()))?;
                speaker.status.shown().then(|| {
                    (shot, ShotJob { source: speaker.source, id: speaker.id.clone(), name: speaker.name.clone(), status: speaker.status })
                })
            })
            .collect();
        ShotJobs { by_shot }
    }

    /// What the farm says now for `project`: Deadline's jobs and the local
    /// queue's together. A farm that cannot be asked adds nothing.
    pub fn ask(farm: &dyn FarmJobs, project: &str) -> Self {
        let mut jobs = farm.deadline_jobs().unwrap_or_default();
        jobs.extend(farm.local_jobs().unwrap_or_default());
        ShotJobs::of(project, &jobs)
    }

    /// The job the clip of `shot` shows, if any.
    pub fn get(&self, shot: &str) -> Option<&ShotJob> {
        self.by_shot.get(&shot_key(shot)?)
    }

    pub fn is_empty(&self) -> bool {
        self.by_shot.is_empty()
    }

    pub fn len(&self) -> usize {
        self.by_shot.len()
    }
}

/// The farm, as the editor asks it; tests answer for it.
pub trait FarmJobs: Sync {
    /// Every job Deadline holds, or why it could not be asked.
    fn deadline_jobs(&self) -> Result<Vec<Job>, String>;
    /// Every job in this machine's local farm queue, or why it could not
    /// be read.
    fn local_jobs(&self) -> Result<Vec<Job>, String>;
}

/// The farm as this machine sees it: Deadline through `deadlinecommand`,
/// the local farm through its queue folder.
pub struct LiveFarmJobs {
    /// The local farm's folder (`previews::this_farm_root`).
    pub local_root: PathBuf,
    pub timeout: Duration,
}

impl LiveFarmJobs {
    pub fn here() -> Self {
        LiveFarmJobs { local_root: crate::previews::this_farm_root(), timeout: DEADLINE_TIMEOUT }
    }
}

impl FarmJobs for LiveFarmJobs {
    fn deadline_jobs(&self) -> Result<Vec<Job>, String> {
        let command = deadline_command().ok_or("Deadline is not installed on this machine")?;
        let output = run(&command, &["-Json", "GetJobs"], self.timeout)?;
        // A refused -Json read still prints its envelope, which says why.
        if !output.status.success() && output.stdout.trim().is_empty() {
            return Err(format!("deadlinecommand failed: {}", output.stderr.trim().chars().take(300).collect::<String>()));
        }
        parse_deadline_jobs(&output.stdout)
    }

    fn local_jobs(&self) -> Result<Vec<Job>, String> {
        local_jobs(&self.local_root)
    }
}

/// Python's `str.strip()` whitespace.
fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// This machine's `deadlinecommand`, found as `tumblepipe.apps.deadline`
/// finds it: in `$DEADLINE_PATH`, which must exist.
pub fn deadline_command() -> Option<PathBuf> {
    let raw = std::env::var("DEADLINE_PATH").ok()?;
    let raw = raw.trim_matches(is_space).trim_matches('"');
    if raw.is_empty() {
        return None;
    }
    let bin = PathBuf::from(raw.replace('\\', "/"));
    if !bin.exists() {
        return None;
    }
    let command = bin.join(if cfg!(windows) { "deadlinecommand.exe" } else { "deadlinecommand" });
    command.is_file().then_some(command)
}

/// What a program printed, as Python's `text=True` reads it: UTF-8 with
/// replacements, line ends made `\n`.
#[derive(Debug)]
pub struct Output {
    pub status: ExitStatus,
    pub stdout: String,
    pub stderr: String,
}

fn universal(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).replace("\r\n", "\n").replace('\r', "\n")
}

/// Run `command` with `args`, no console window and nothing on its stdin;
/// killed, and an error, when it takes longer than `timeout`.
pub fn run(command: &Path, args: &[&str], timeout: Duration) -> Result<Output, String> {
    let mut process = Command::new(command);
    process.args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        process.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = process.spawn().map_err(|e| e.to_string())?;
    let mut stdout = child.stdout.take().expect("piped");
    let mut stderr = child.stderr.take().expect("piped");
    let out_reader = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let _ = stdout.read_to_end(&mut buffer);
        buffer
    });
    let err_reader = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let _ = stderr.read_to_end(&mut buffer);
        buffer
    });
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if started.elapsed() > timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("it did not answer in {} seconds", timeout.as_secs()));
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let stdout = universal(&out_reader.join().unwrap_or_default());
    let stderr = universal(&err_reader.join().unwrap_or_default());
    Ok(Output { status, stdout, stderr })
}

fn text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn int(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// The stamp's `(project, entity)` from a job's extra info; '' for what
/// it does not carry (`stamp.read`).
fn stamp(extra: Option<&Value>) -> (String, String) {
    let Some(Value::Object(extra)) = extra else { return (String::new(), String::new()) };
    (text(extra.get(STAMP_PROJECT)), text(extra.get(STAMP_ENTITY)))
}

/// A Deadline document time (`2026-11-20T10:11:12.345Z`) as seconds since
/// 1970; 0 for unset (year 1) or unreadable.
pub fn deadline_time(value: &str) -> i64 {
    chrono::DateTime::parse_from_rfc3339(value.trim()).map(|t| t.timestamp()).ok().filter(|t| *t > 0).unwrap_or(0)
}

/// A local batch's `submitted` (`queue.now()`: local time, no zone) as
/// seconds since 1970; 0 when unreadable.
pub fn local_time(value: &str) -> i64 {
    use chrono::TimeZone;
    let Ok(naive) = chrono::NaiveDateTime::parse_from_str(value.trim(), "%Y-%m-%dT%H:%M:%S") else { return 0 };
    chrono::Local.from_local_datetime(&naive).earliest().map_or(0, |t| t.timestamp())
}

/// One of `GetJobs`' documents as a job (`rows.farm_job_row`).
pub fn deadline_job(doc: &Value) -> Job {
    let props = doc.get("Props");
    let id = text(doc.get("_id"));
    let batch = text(props.and_then(|p| p.get("Batch")));
    let name = text(props.and_then(|p| p.get("Name")));
    let (project, entity) = stamp(props.and_then(|p| p.get("ExDic")));
    Job {
        source: Source::Deadline,
        batch_key: if batch.is_empty() { format!("deadline:{id}") } else { format!("deadline:{batch}") },
        batch: if batch.is_empty() { name.clone() } else { batch },
        id,
        name,
        project,
        entity,
        status: Status::of_deadline(int(doc.get("Stat")), int(doc.get("RenderingChunks"))),
        submitted: deadline_time(&text(doc.get("Date"))),
    }
}

/// `deadlinecommand -Json GetJobs`' stdout as jobs: its envelope,
/// `{"ok": true, "result": [...]}`; an error for a refusal or anything
/// that is not one (`deadline_jobs.parse_envelope`).
pub fn parse_deadline_jobs(stdout: &str) -> Result<Vec<Job>, String> {
    let text = stdout.trim().trim_start_matches('\u{feff}');
    let data: Value = serde_json::from_str(text)
        .map_err(|_| format!("deadlinecommand printed no jobs: {}", text.chars().take(200).collect::<String>()))?;
    if data.get("ok") != Some(&Value::Bool(true)) {
        return Err(format!("Deadline refused: {}", self::text(data.get("result"))));
    }
    match data.get("result") {
        Some(Value::Array(docs)) => Ok(docs.iter().filter(|d| d.is_object()).map(deadline_job).collect()),
        Some(Value::Null) | None => Ok(Vec::new()),
        Some(_) => Err("Deadline's job list is not a list".into()),
    }
}

/// A local batch's jobs (`rows.local_job_rows`), from its `batch.json`
/// and `state.json` (every task queued while it has none). An error for a
/// batch that cannot be read, as `queue.read_batch` raises: no file, not
/// version 1, a dependency cycle or a job without its state.
pub fn batch_jobs(batch: &Value, state: Option<&Value>) -> Result<Vec<Job>, String> {
    let version = batch.get("version");
    if version.and_then(Value::as_i64) != Some(BATCH_VERSION) || version.is_some_and(Value::is_f64) {
        return Err("not a version 1 batch".into());
    }
    let id = text(batch.get("id"));
    let name = text(batch.get("name"));
    let submitted = local_time(&text(batch.get("submitted")));
    let jobs: &[Value] = batch.get("jobs").and_then(Value::as_array).map_or(&[], Vec::as_slice);
    let tasks_of = |index: i64| -> Result<Vec<String>, String> {
        let Some(state) = state else {
            // `queue.initial_state`: every task queued.
            let job = jobs.iter().find(|j| int(j.get("index")) == index);
            let count = job.and_then(|j| j.get("tasks")).and_then(Value::as_array).map_or(0, Vec::len);
            return Ok(vec!["queued".to_owned(); count]);
        };
        let tasks = state
            .get("jobs")
            .and_then(|j| j.get(index.to_string()))
            .and_then(|j| j.get("tasks"))
            .and_then(Value::as_array)
            .ok_or_else(|| format!("no state for job {index}"))?;
        Ok(tasks.iter().map(|t| text(t.get("status"))).collect())
    };
    // `queue.job_statuses`: resolved to a fixpoint, dependencies first.
    let mut statuses: BTreeMap<i64, Status> = BTreeMap::new();
    let mut pending: Vec<&Value> = jobs.iter().collect();
    while !pending.is_empty() {
        let before = pending.len();
        let mut left = Vec::new();
        for job in pending {
            let deps: Vec<i64> =
                job.get("deps").and_then(Value::as_array).map_or_else(Vec::new, |d| d.iter().map(|v| int(Some(v))).collect());
            if !deps.iter().all(|d| statuses.contains_key(d)) {
                left.push(job);
                continue;
            }
            let completed = deps.iter().all(|d| statuses[d] == Status::Completed);
            let index = int(job.get("index"));
            let tasks = tasks_of(index)?;
            let tasks: Vec<&str> = tasks.iter().map(String::as_str).collect();
            statuses.insert(index, Status::of_local_tasks(&tasks, completed));
        }
        if left.len() == before {
            return Err(format!("batch {id} has a dependency cycle or a missing job"));
        }
        pending = left;
    }
    Ok(jobs
        .iter()
        .map(|job| {
            let index = int(job.get("index"));
            let (project, entity) = stamp(job.get("extra_info"));
            Job {
                source: Source::Local,
                id: format!("{LOCAL_PREFIX}{id}/{index}"),
                batch_key: format!("{LOCAL_PREFIX}{id}"),
                batch: name.clone(),
                name: text(job.get("name")),
                project,
                entity,
                status: statuses.get(&index).copied().unwrap_or(Status::Unknown),
                submitted,
            }
        })
        .collect())
}

/// Every job in the local farm queue at `root` (`rows.local_rows`); a
/// batch that cannot be read is passed over, no queue is no jobs.
pub fn local_jobs(root: &Path) -> Result<Vec<Job>, String> {
    let Ok(listing) = std::fs::read_dir(root.join("batches")) else { return Ok(Vec::new()) };
    let mut folders: Vec<PathBuf> = listing.filter_map(Result::ok).map(|e| e.path()).filter(|p| p.join(BATCH_NAME).is_file()).collect();
    folders.sort();
    let read = |path: PathBuf| std::fs::read(path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok());
    Ok(folders
        .into_iter()
        .filter_map(|folder| {
            let batch = read(folder.join(BATCH_NAME))?;
            batch_jobs(&batch, read(folder.join(STATE_NAME)).as_ref()).ok()
        })
        .flatten()
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    const PROJECT: &str = "HideAndReek";

    fn job(id: &str, batch: &str, entity: &str, status: Status, submitted: i64) -> Job {
        Job {
            source: Source::Deadline,
            id: id.into(),
            batch_key: format!("deadline:{batch}"),
            batch: format!("{PROJECT} render {entity} 2026/11/20 10:00:00"),
            name: format!("{id} render"),
            project: PROJECT.into(),
            entity: entity.into(),
            status,
            submitted,
        }
    }

    fn status() -> impl Strategy<Value = Status> {
        proptest::sample::select(Status::ALL.to_vec())
    }

    fn shot() -> impl Strategy<Value = String> {
        (0u8..3, 0u8..4).prop_map(|(s, h)| format!("entity:/shots/sq{s}/sh{h}"))
    }

    fn jobs() -> impl Strategy<Value = Vec<Job>> {
        proptest::collection::vec(
            (shot(), status(), 0u8..6, 0i64..50, prop_oneof![Just(PROJECT.to_owned()), Just("Other".to_owned())], any::<bool>()),
            0..24,
        )
        .prop_map(|rows| {
            rows.into_iter()
                .enumerate()
                .map(|(n, (shot, status, batch, at, project, local))| Job {
                    source: if local { Source::Local } else { Source::Deadline },
                    project,
                    ..job(&format!("j{n:02}"), &format!("b{batch}"), &shot, status, at)
                })
                .collect()
        })
    }

    /// The model the rules say, written plainly: per shot, the batch with
    /// the latest job, and of it the most urgent job.
    fn expected(jobs: &[Job], shot: &str) -> Option<Status> {
        let mine: Vec<&Job> = jobs.iter().filter(|j| j.project == PROJECT && j.entity == shot).collect();
        let batch_time = |key: &str| mine.iter().filter(|j| j.batch_key == key).map(|j| j.submitted).max();
        let key = mine.iter().map(|j| j.batch_key.clone()).max_by_key(|k| (batch_time(k), k.clone()))?;
        let top = mine.iter().filter(|j| j.batch_key == key).map(|j| j.status.urgency()).max()?;
        let status = mine.iter().find(|j| j.batch_key == key && j.status.urgency() == top)?.status;
        status.shown().then_some(status)
    }

    proptest! {
        /// Every shot shows exactly the state its newest batch's most
        /// urgent job is in, and that job's own id; nothing when that batch
        /// has ended.
        #[test]
        fn a_shot_shows_its_newest_batchs_most_urgent_job(jobs in jobs()) {
            let shots = ShotJobs::of(PROJECT, &jobs);
            for s in 0..3 {
                for h in 0..4 {
                    let uri = format!("entity:/shots/sq{s}/sh{h}");
                    let shown = shots.get(&uri);
                    prop_assert_eq!(shown.map(|j| j.status), expected(&jobs, &uri), "{}", uri);
                    if let Some(shown) = shown {
                        let job = jobs.iter().find(|j| j.id == shown.id).expect("a job of the list");
                        prop_assert_eq!(&job.entity, &uri);
                        prop_assert_eq!(job.project.as_str(), PROJECT);
                        prop_assert_eq!(job.status, shown.status);
                        prop_assert!(shown.status.shown());
                    }
                }
            }
        }

        /// Jobs of another project, of an asset or an edit, never show; nor
        /// does anything when the project is not known.
        #[test]
        fn other_projects_and_entities_never_show(jobs in jobs(), entity in prop_oneof![
            Just("entity:/assets/char/bob"), Just("entity:/edits/main"), Just("entity:/shots/sq0"), Just("")
        ]) {
            let others: Vec<Job> = jobs.iter().map(|j| Job { project: "Other".into(), ..j.clone() }).collect();
            prop_assert!(ShotJobs::of(PROJECT, &others).is_empty());
            prop_assert!(ShotJobs::of("", &jobs).is_empty());
            let not_shots: Vec<Job> = jobs.iter().map(|j| Job { entity: entity.into(), batch: "x".into(), ..j.clone() }).collect();
            prop_assert!(ShotJobs::of(PROJECT, &not_shots).is_empty());
        }

        /// A newer submission of the shot decides, whatever an older one is in.
        #[test]
        fn the_newest_submission_wins(old in status(), new in status(), gap in 1i64..1000) {
            let jobs = [job("a", "old", "entity:/shots/sq0/sh0", old, 100), job("b", "new", "entity:/shots/sq0/sh0", new, 100 + gap)];
            let shown = ShotJobs::of(PROJECT, &jobs);
            let got = shown.get("entity:/shots/sq0/sh0");
            prop_assert_eq!(got.map(|j| j.id.as_str()), new.shown().then_some("b"));
        }

        /// A farm that cannot be asked shows nothing and never errors; a
        /// half-reachable one shows what the other half says.
        #[test]
        fn an_unreachable_farm_shows_nothing(jobs in jobs(), deadline_up in any::<bool>(), local_up in any::<bool>()) {
            struct Fake { jobs: Vec<Job>, deadline_up: bool, local_up: bool }
            impl FarmJobs for Fake {
                fn deadline_jobs(&self) -> Result<Vec<Job>, String> {
                    if self.deadline_up { Ok(self.jobs.iter().filter(|j| j.source == Source::Deadline).cloned().collect()) } else { Err("no Deadline".into()) }
                }
                fn local_jobs(&self) -> Result<Vec<Job>, String> {
                    if self.local_up { Ok(self.jobs.iter().filter(|j| j.source == Source::Local).cloned().collect()) } else { Err("no queue".into()) }
                }
            }
            let fake = Fake { jobs: jobs.clone(), deadline_up, local_up };
            let reachable: Vec<Job> = jobs.iter().filter(|j| match j.source { Source::Deadline => deadline_up, Source::Local => local_up }).cloned().collect();
            prop_assert_eq!(ShotJobs::ask(&fake, PROJECT), ShotJobs::of(PROJECT, &reachable));
            if !deadline_up && !local_up {
                prop_assert!(ShotJobs::ask(&fake, PROJECT).is_empty());
            }
        }

        /// A shot's URI matches whatever way it is written: a trailing
        /// slash or a query (a channel) does not hide its job.
        #[test]
        fn a_shot_matches_however_written(s in 0u8..3, h in 0u8..4) {
            let jobs = [job("a", "b", &format!("entity:/shots/sq{s}/sh{h}?channel=main"), Status::Queued, 1)];
            let shown = ShotJobs::of(PROJECT, &jobs);
            let (plain, slashed, other) =
                (format!("entity:/shots/sq{s}/sh{h}"), format!("entity:/shots/sq{s}/sh{h}/"), format!("entity:/shots/sq{s}/sh{}", h + 9));
            prop_assert!(shown.get(&plain).is_some());
            prop_assert!(shown.get(&slashed).is_some());
            prop_assert!(shown.get(&other).is_none());
        }

        /// A local job's state is its tasks' as the runner gives it.
        #[test]
        fn local_states_follow_the_runner(tasks in proptest::collection::vec(proptest::sample::select(vec!["queued", "rendering", "completed", "failed", "suspended", "cancelled"]), 0..6), deps in any::<bool>()) {
            let status = Status::of_local_tasks(&tasks, deps);
            let has = |s| tasks.contains(&s);
            let want = if has("rendering") { Status::Rendering } else if has("failed") { Status::Failed }
                else if has("queued") { if deps { Status::Queued } else { Status::Pending } }
                else if has("suspended") { Status::Suspended } else if has("cancelled") { Status::Cancelled } else { Status::Completed };
            prop_assert_eq!(status, want);
        }
    }

    /// An instant from 2001 to 2099, whole seconds.
    fn instant() -> impl Strategy<Value = i64> {
        1_000_000_000i64..4_000_000_000
    }

    proptest! {
        /// One instant read from a Deadline date (UTC) and from a local
        /// batch's `submitted` (this machine's time, no zone) is the same
        /// second, so a local batch and a Deadline batch of one shot are
        /// ordered by when they were really submitted. (A local time the
        /// clock passes twice is passed over: it names two instants.)
        #[test]
        fn deadline_and_local_times_name_the_same_instant(at in instant(), millis in 0u32..1000) {
            use chrono::{Local, TimeZone, Utc};
            let utc = Utc.timestamp_opt(at, millis * 1_000_000).unwrap();
            prop_assert_eq!(deadline_time(&utc.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()), at);
            prop_assert_eq!(deadline_time(&utc.format("%Y-%m-%dT%H:%M:%SZ").to_string()), at);
            let naive = utc.with_timezone(&Local).naive_local();
            prop_assume!(matches!(Local.from_local_datetime(&naive), chrono::LocalResult::Single(_)));
            prop_assert_eq!(local_time(&naive.format("%Y-%m-%dT%H:%M:%S").to_string()), at);
        }

        /// Of two Deadline dates the later reads later; an unreadable or
        /// unset one reads 0, older than any real submission.
        #[test]
        fn later_deadline_dates_read_later(a in instant(), b in instant(), junk in "[^0-9]{0,12}") {
            use chrono::{TimeZone, Utc};
            let read = |t: i64| deadline_time(&Utc.timestamp_opt(t, 0).unwrap().to_rfc3339());
            prop_assert_eq!(read(a).cmp(&read(b)), a.cmp(&b));
            prop_assert_eq!(deadline_time(&junk), 0);
            prop_assert_eq!(local_time(&junk), 0);
        }

        /// A drawn `GetJobs` document reads back as the job it describes:
        /// its id, its stamp (else the batch name's), its state as
        /// `of_deadline` says, its date; a job with no batch is a batch
        /// of its own, never merged with another unbatched job.
        #[test]
        fn deadline_documents_read_back(
            docs in proptest::collection::vec((
                "[0-9a-f]{24}", 0usize..3, shot(), any::<bool>(), 0i64..8, 0i64..3, instant()), 1..8),
            envelope_bom in any::<bool>(),
        ) {
            use chrono::{TimeZone, Utc};
            let docs: Vec<_> = docs.into_iter().enumerate().map(|(n, (id, batch, shot, stamped, stat, chunks, at))| {
                let id = format!("{id}{n}");
                let batch = if batch == 0 { String::new() } else { format!("{PROJECT} render {shot} b{batch}") };
                let ex = if stamped { json!({"TumblePipeProject": PROJECT, "TumblePipeEntity": shot}) } else { json!({}) };
                (id, batch, shot, stat, chunks, at, ex)
            }).collect();
            let result: Vec<Value> = docs.iter().map(|(id, batch, _, stat, chunks, at, ex)| json!({
                "_id": id, "Stat": stat, "RenderingChunks": chunks,
                "Date": Utc.timestamp_opt(*at, 0).unwrap().format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string(),
                "Props": {"Name": format!("job {id}"), "Batch": batch, "ExDic": ex},
            })).collect();
            let out = json!({"ok": true, "result": result}).to_string();
            let out = if envelope_bom { format!("\u{feff}{out}\r\n") } else { out };
            let jobs = parse_deadline_jobs(&out).unwrap();
            prop_assert_eq!(jobs.len(), docs.len());
            for (job, (id, batch, shot, stat, chunks, at, ex)) in jobs.iter().zip(&docs) {
                prop_assert_eq!(&job.id, id);
                prop_assert_eq!(job.submitted, *at);
                prop_assert_eq!(job.status, Status::of_deadline(*stat, *chunks));
                if batch.is_empty() {
                    prop_assert_eq!(&job.batch_key, &format!("deadline:{id}"));
                    prop_assert_eq!(job.entity(), if ex.as_object().unwrap().is_empty() { "" } else { shot.as_str() });
                } else {
                    prop_assert_eq!(&job.batch_key, &format!("deadline:{batch}"));
                    prop_assert_eq!((job.project(), job.entity()), (PROJECT, shot.as_str()));
                }
            }
            let unbatched: Vec<&str> = jobs.iter().filter(|j| j.batch_key == format!("deadline:{}", j.id)).map(|j| j.id.as_str()).collect();
            let keys: std::collections::BTreeSet<&str> = jobs.iter().filter(|j| unbatched.contains(&j.id.as_str())).map(|j| j.batch_key.as_str()).collect();
            prop_assert_eq!(keys.len(), unbatched.len());
        }

        /// Whatever `deadlinecommand` prints, reading it answers jobs or an
        /// error, never a panic.
        #[test]
        fn any_output_reads_without_panicking(out in ".{0,200}", doc in "\\{\"ok\": ?(true|false|1), ?\"result\": ?(null|\\[\\]|\"x\"|\\{\\}|\\[1, ?\"a\", ?\\{\\}\\])\\}") {
            let _ = parse_deadline_jobs(&out);
            let read = parse_deadline_jobs(&doc);
            prop_assert_eq!(read.is_ok(), doc.contains("true") && !doc.contains("\"x\"") && !doc.contains("{}}"));
        }
    }

    #[test]
    fn deadline_documents_read_as_the_farm_manager_reads_them() {
        let out = json!({"ok": true, "result": [
            {"_id": "6720aa", "Stat": 1, "RenderingChunks": 2, "Date": "2026-11-20T10:00:00.000Z",
             "Props": {"Name": "render lighting", "Batch": "HideAndReek render entity:/shots/sq0/sh0 2026/11/20 10:00:00",
                       "ExDic": {"TumblePipeProject": "HideAndReek", "TumblePipeEntity": "entity:/shots/sq0/sh0"}}},
            {"_id": "6720ab", "Stat": 1, "RenderingChunks": 0, "Date": "0001-01-01T00:00:00Z",
             "Props": {"Name": "old", "Batch": "HideAndReek playblast entity:/shots/sq0/sh1 2026/01/01 10:00:00"}},
            {"_id": "6720ac", "Stat": 6, "Props": {}},
            "not a job"
        ]});
        let jobs = parse_deadline_jobs(&format!("\u{feff}{out}\n")).unwrap();
        assert_eq!(jobs.len(), 3);
        assert_eq!(jobs[0].status, Status::Rendering);
        assert_eq!(jobs[0].submitted, 1_795_168_800);
        assert_eq!(jobs[1].status, Status::Queued);
        assert_eq!(jobs[1].submitted, 0, "year 1 is unset");
        assert_eq!((jobs[1].project(), jobs[1].entity()), ("HideAndReek", "entity:/shots/sq0/sh1"), "the batch name, without a stamp");
        assert_eq!(jobs[2].status, Status::Pending);
        assert_eq!(jobs[2].batch_key, "deadline:6720ac");
        assert!(parse_deadline_jobs(r#"{"ok": false, "result": "no repository"}"#).unwrap_err().contains("no repository"));
        assert!(parse_deadline_jobs("Error: could not connect").is_err());
        assert_eq!(parse_deadline_jobs(r#"{"ok": true, "result": null}"#).unwrap(), Vec::new());
    }

    #[test]
    fn batch_names_carry_their_entity() {
        assert_eq!(name_entity("P render entity:/shots/a/b 2026/11/20"), "entity:/shots/a/b");
        assert_eq!(name_entity("P xentity:/shots/a/b"), "");
        assert_eq!(name_entity("P entity:/shots/ x"), "");
        assert_eq!(name_entity("P entity:/props/a entity:/assets/c/d"), "entity:/assets/c/d");
        assert_eq!(name_entity("P (entity:/edits/main)"), "entity:/edits/main)");
    }

    #[test]
    fn local_batches_read_with_their_dependencies() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("batches").join("20261120_100000_abcdef");
        std::fs::create_dir_all(&folder).unwrap();
        let batch = json!({"version": 1, "id": "20261120_100000_abcdef", "name": "HideAndReek render entity:/shots/sq0/sh0 x",
            "submitted": "2026-11-20T10:00:00", "jobs": [
                {"index": 0, "name": "render", "deps": [], "tasks": [[1, 5], [6, 10]],
                 "extra_info": {"TumblePipeProject": "HideAndReek", "TumblePipeEntity": "entity:/shots/sq0/sh0"}},
                {"index": 1, "name": "notify", "deps": [0], "tasks": [[1, 1]]}]});
        std::fs::write(folder.join(BATCH_NAME), batch.to_string()).unwrap();
        let jobs = local_jobs(dir.path()).unwrap();
        assert_eq!(jobs.iter().map(|j| j.status).collect::<Vec<_>>(), [Status::Queued, Status::Pending], "no state yet: every task queued");
        assert_eq!(jobs[0].id, "local:20261120_100000_abcdef/0");
        assert_eq!(jobs[1].entity(), "entity:/shots/sq0/sh0", "from the batch name");
        let state =
            json!({"jobs": {"0": {"tasks": [{"status": "completed"}, {"status": "rendering"}]}, "1": {"tasks": [{"status": "queued"}]}}});
        std::fs::write(folder.join(STATE_NAME), state.to_string()).unwrap();
        let jobs = local_jobs(dir.path()).unwrap();
        assert_eq!(jobs.iter().map(|j| j.status).collect::<Vec<_>>(), [Status::Rendering, Status::Pending]);
        let shots = ShotJobs::of("HideAndReek", &jobs);
        assert_eq!(shots.get("entity:/shots/sq0/sh0").map(|j| j.id.as_str()), Some("local:20261120_100000_abcdef/0"));
        std::fs::write(folder.join(BATCH_NAME), json!({"version": 1.0, "jobs": []}).to_string()).unwrap();
        assert!(local_jobs(dir.path()).unwrap().is_empty(), "a batch it cannot read is passed over");
        assert!(local_jobs(&dir.path().join("nowhere")).unwrap().is_empty(), "no queue, no jobs");
        let cycle =
            json!({"version": 1, "id": "c", "jobs": [{"index": 0, "deps": [1], "tasks": []}, {"index": 1, "deps": [0], "tasks": []}]});
        assert!(batch_jobs(&cycle, None).is_err());
    }

    #[test]
    fn no_deadline_is_no_jobs_not_a_hang() {
        let farm = LiveFarmJobs { local_root: PathBuf::from("/nowhere/at/all"), timeout: Duration::from_secs(1) };
        // Whatever this machine has, asking never panics and the local
        // queue that is not there answers nothing.
        assert!(farm.local_jobs().unwrap().is_empty());
        if std::env::var_os("DEADLINE_PATH").is_none() {
            assert!(farm.deadline_jobs().is_err());
        }
    }
}
