//! The Clean Up Project window's overview of a listing, as plain data
//! (`tumblepipe.cleanup.overview`).
//!
//! The window shows a [`Plan`] as a tree: a row per category, opened to
//! why (the reasons, grouped), what goes biggest first, and what could not
//! be read. A note that does not start "Read " warns (something is *not*
//! protected, or could not be asked) and is shown above the tree as well.

use std::collections::HashMap;

use crate::core::{CategoryPlan, Outcome, Plan};
use crate::py::{human, thousands};

/// A note that only says what was read starts so.
pub const INFO_PREFIX: &str = "Read ";
/// Groups listed under "Biggest first" before the rest are summed up.
pub const DETAIL: usize = 200;
/// Errors listed under a category before the rest are counted.
pub const ERROR_DETAIL: usize = 50;
pub const COLUMNS: [&str; 4] = ["", "Found", "To delete", "Frees"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    Normal,
    /// A category's own row.
    Heading,
    /// What stays, and what was read.
    Dim,
    Warning,
    Error,
}

impl Tone {
    pub fn name(self) -> &'static str {
        match self {
            Tone::Normal => "normal",
            Tone::Heading => "heading",
            Tone::Dim => "dim",
            Tone::Warning => "warning",
            Tone::Error => "error",
        }
    }
}

/// Whether a category's note warns, rather than says what was read.
pub fn is_warning(note: &str) -> bool {
    !note.starts_with(INFO_PREFIX)
}

pub fn plural(count: usize, noun: &str) -> String {
    format!("{} {noun}{}", thousands(count), if count == 1 { "" } else { "s" })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Reason {
    pub prune: bool,
    pub reason: String,
    pub count: usize,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Group {
    pub name: String,
    pub count: usize,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CategoryOverview {
    pub key: String,
    pub title: String,
    pub noun: String,
    pub found: usize,
    pub to_delete: usize,
    /// Bytes the deletions free.
    pub size: u64,
    /// Bytes of what stays.
    pub kept_size: u64,
    /// Deleting reasons first, then keeping ones; each biggest first.
    pub reasons: Vec<Reason>,
    /// What goes, by group, biggest first.
    pub groups: Vec<Group>,
    pub notes: Vec<String>,
    pub warnings: Vec<String>,
    pub errors: Vec<String>,
}

impl CategoryOverview {
    pub fn kept(&self) -> usize {
        self.found - self.to_delete
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Overview {
    pub categories: Vec<CategoryOverview>,
}

impl Overview {
    pub fn found(&self) -> usize {
        self.categories.iter().map(|c| c.found).sum()
    }
    pub fn to_delete(&self) -> usize {
        self.categories.iter().map(|c| c.to_delete).sum()
    }
    pub fn size(&self) -> u64 {
        self.categories.iter().map(|c| c.size).sum()
    }
}

pub fn category_overview(category: &CategoryPlan) -> CategoryOverview {
    let mut by_reason: HashMap<(bool, String), (usize, u64)> = HashMap::new();
    let mut by_group: HashMap<String, (usize, u64)> = HashMap::new();
    for entry in &category.entries {
        let tally = by_reason.entry((entry.prune, entry.reason.clone())).or_default();
        tally.0 += 1;
        tally.1 += entry.size;
        if entry.prune {
            let tally = by_group.entry(entry.group.clone()).or_default();
            tally.0 += 1;
            tally.1 += entry.size;
        }
    }
    let mut reasons: Vec<Reason> =
        by_reason.into_iter().map(|((prune, reason), (count, size))| Reason { prune, reason, count, size }).collect();
    reasons.sort_by(|a, b| {
        (!a.prune, std::cmp::Reverse(a.size), std::cmp::Reverse(a.count), &a.reason).cmp(&(
            !b.prune,
            std::cmp::Reverse(b.size),
            std::cmp::Reverse(b.count),
            &b.reason,
        ))
    });
    let mut groups: Vec<Group> = by_group.into_iter().map(|(name, (count, size))| Group { name, count, size }).collect();
    groups.sort_by(|a, b| {
        (std::cmp::Reverse(a.size), std::cmp::Reverse(a.count), &a.name).cmp(&(
            std::cmp::Reverse(b.size),
            std::cmp::Reverse(b.count),
            &b.name,
        ))
    });
    let size = category.prunable_size();
    CategoryOverview {
        key: category.key.clone(),
        title: category.title.clone(),
        noun: category.noun.clone(),
        found: category.entries.len(),
        to_delete: category.prunable_count(),
        size,
        kept_size: category.entries.iter().map(|e| e.size).sum::<u64>() - size,
        reasons,
        groups,
        notes: category.notes.iter().filter(|n| !is_warning(n)).cloned().collect(),
        warnings: category.notes.iter().filter(|n| is_warning(n)).cloned().collect(),
        errors: category.errors.clone(),
    }
}

pub fn overview(plan: &Plan) -> Overview {
    Overview { categories: plan.categories.iter().map(category_overview).collect() }
}

pub fn headline(view: &Overview) -> String {
    if view.to_delete() > 0 { format!("{} to free", human(view.size())) } else { "Nothing to delete".into() }
}

pub fn subline(view: &Overview) -> String {
    let count = view.categories.len();
    let looked = format!("{} looked at in {count} {}", thousands(view.found()), if count == 1 { "category" } else { "categories" });
    if view.to_delete() == 0 {
        return format!("{looked}: everything stays.");
    }
    format!("{} to delete, of {looked}. Nothing is deleted until you confirm.", plural(view.to_delete(), "item"))
}

/// The Delete button's text.
pub fn delete_label(view: Option<&Overview>) -> String {
    match view {
        Some(view) if view.to_delete() > 0 => format!("Delete {} item(s), {}…", view.to_delete(), human(view.size())),
        _ => "Delete…".into(),
    }
}

/// Every warning, `(category title, text)`, in the order listed.
pub fn warnings(view: &Overview) -> Vec<(String, String)> {
    view.categories.iter().flat_map(|c| c.warnings.iter().map(|w| (c.title.clone(), w.clone()))).collect()
}

/// What the confirmation lists: one line per category that deletes.
pub fn confirm_lines(view: &Overview) -> Vec<String> {
    view.categories
        .iter()
        .filter(|c| c.to_delete > 0)
        .map(|c| format!("{}: {} {}(s), {}", c.title, c.to_delete, c.noun, human(c.size)))
        .collect()
}

/// The categories a workfile makes again: an export, render or cache by
/// exporting, rendering or caching again, a farm job folder by the next
/// submit. What the cleanup removes is a risk to no one, and the words say
/// so plainly: no "dangerous", no "can't be undone" (`overview.REMAKEABLE`).
pub const REMAKEABLE: [&str; 4] = ["exports", "renders", "caches", "jobs"];

/// How the confirmation names a category no workfile makes again.
fn not_remakeable_noun(category: &CategoryOverview) -> &str {
    match category.key.as_str() {
        "backups" => "Houdini backups",
        "deleted" => "deleted entities",
        _ => &category.title,
    }
}

/// The confirmation's sentence for the categories about to be purged: how
/// many items go, and which of them a workfile can make again
/// (`overview.confirm_note`).
pub fn confirm_note<'a>(categories: impl IntoIterator<Item = &'a CategoryOverview>) -> String {
    let deleting: Vec<&CategoryOverview> = categories.into_iter().filter(|c| c.to_delete > 0).collect();
    let count: usize = deleting.iter().map(|c| c.to_delete).sum();
    let head = format!("{} old item{} will be removed from disk.", thousands(count), if count == 1 { "" } else { "s" });
    let others: Vec<&CategoryOverview> = deleting.iter().copied().filter(|c| !REMAKEABLE.contains(&c.key.as_str())).collect();
    if deleting.is_empty() || others.len() == deleting.len() {
        return head;
    }
    if others.is_empty() {
        return format!("{head} Each can be made again from its workfile.");
    }
    let names: Vec<&str> = others.iter().map(|c| not_remakeable_noun(c)).collect();
    format!("{head} All but the {} can be made again from their workfiles.", names.join(" and "))
}

/// One row of the tree: its cells ([`COLUMNS`]), how it is drawn, a
/// tooltip, its children and whether it starts open.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub cells: [String; 4],
    pub tone: Tone,
    pub tip: String,
    pub children: Vec<Row>,
    pub expanded: bool,
}

impl Row {
    fn new(cells: [&str; 4], tone: Tone, tip: &str) -> Self {
        Row { cells: cells.map(String::from), tone, tip: tip.into(), children: Vec::new(), expanded: false }
    }
}

fn reason_row(category: &CategoryOverview, reason: &Reason) -> Row {
    let count = thousands(reason.count);
    if reason.prune {
        return Row::new(
            [&reason.reason, &count, &count, &human(reason.size)],
            Tone::Normal,
            &format!("{} go: {}", plural(reason.count, &category.noun), reason.reason),
        );
    }
    Row::new(
        [&reason.reason, &count, "kept", ""],
        Tone::Dim,
        &format!("{} stay ({}): {}", plural(reason.count, &category.noun), human(reason.size), reason.reason),
    )
}

/// The first `detail` groups, and the rest summed up (None when none are
/// left over); the rest's name says how many groups it holds.
pub fn biggest(groups: &[Group], detail: usize) -> (&[Group], Option<Group>) {
    if groups.len() <= detail {
        return (groups, None);
    }
    let rest = &groups[detail..];
    let summed = Group {
        name: format!("({} more)", thousands(rest.len())),
        count: rest.iter().map(|g| g.count).sum(),
        size: rest.iter().map(|g| g.size).sum(),
    };
    (&groups[..detail], Some(summed))
}

pub fn category_row(category: &CategoryOverview, detail: usize) -> Row {
    let mut children: Vec<Row> = category.warnings.iter().map(|text| Row::new([text, "", "", ""], Tone::Warning, text)).collect();
    if !category.reasons.is_empty() {
        let mut why = Row::new(["Why", "", "", ""], Tone::Dim, "What goes and what stays, by reason");
        why.children = category.reasons.iter().map(|r| reason_row(category, r)).collect();
        why.expanded = true;
        children.push(why);
    }
    if !category.groups.is_empty() {
        let (shown, rest) = biggest(&category.groups, detail);
        let mut rows: Vec<Row> =
            shown.iter().map(|g| Row::new([&g.name, "", &thousands(g.count), &human(g.size)], Tone::Normal, &g.name)).collect();
        if let Some(rest) = rest {
            rows.push(Row::new([&rest.name, "", &thousands(rest.count), &human(rest.size)], Tone::Dim, ""));
        }
        let mut biggest_row =
            Row::new(["To delete, biggest first", "", "", ""], Tone::Dim, "What goes, by entity or folder, the biggest first");
        biggest_row.children = rows;
        children.push(biggest_row);
    }
    children.extend(category.notes.iter().map(|text| Row::new([text, "", "", ""], Tone::Dim, text)));
    if !category.errors.is_empty() {
        let mut shown: Vec<Row> = category.errors.iter().take(ERROR_DETAIL).map(|e| Row::new([e, "", "", ""], Tone::Error, e)).collect();
        if category.errors.len() > ERROR_DETAIL {
            shown.push(Row::new([&format!("({} more)", thousands(category.errors.len() - ERROR_DETAIL)), "", "", ""], Tone::Error, ""));
        }
        let mut errors = Row::new(
            [&format!("Could not be read: {}", thousands(category.errors.len())), "", "", ""],
            Tone::Error,
            "What could not be read is kept",
        );
        errors.children = shown;
        children.push(errors);
    }
    let found = plural(category.found, &category.noun);
    let frees = if category.to_delete > 0 { human(category.size) } else { "—".into() };
    let tone = if category.warnings.is_empty() { Tone::Heading } else { Tone::Warning };
    let tip = format!(
        "{found}: {} to delete ({}), {} kept ({})",
        thousands(category.to_delete),
        human(category.size),
        thousands(category.kept()),
        human(category.kept_size)
    );
    let mut row = Row::new([&category.title, &found, &thousands(category.to_delete), &frees], tone, &tip);
    row.children = children;
    // A category whose listing warns opens, so the warning is in view.
    row.expanded = !category.warnings.is_empty();
    row
}

/// The tree: one row per category, in the order listed.
pub fn rows(view: &Overview, detail: usize) -> Vec<Row> {
    view.categories.iter().map(|c| category_row(c, detail)).collect()
}

/// What a delete did, one row per category it touched; its errors under it.
pub fn deleted_rows(plan: &Plan, results: &[(String, Outcome)]) -> Vec<Row> {
    let mut out = Vec::new();
    for category in &plan.categories {
        let (removed, removed_bytes, errors) =
            results.iter().find(|(k, _)| *k == category.key).map(|(_, o)| o.clone()).unwrap_or((0, 0, Vec::new()));
        if removed == 0 && errors.is_empty() {
            continue;
        }
        let tone = if errors.is_empty() { Tone::Heading } else { Tone::Error };
        let mut row = Row::new([&category.title, "", &format!("{} deleted", thousands(removed)), &human(removed_bytes)], tone, "");
        row.children = errors.iter().map(|e| Row::new([e, "", "", ""], Tone::Error, e)).collect();
        row.expanded = !errors.is_empty();
        out.push(row);
    }
    out
}
