//! A scan as the window's tree table shows it: one row per category,
//! opened to why (the reasons), what goes biggest first, its notes and what
//! could not be read.
//!
//! The rows are `th_cleanup::overview`'s [`Row`]s, built here rather than
//! by `overview::rows`, because the table follows the convention's reading
//! rules where the Python window's text cells do not: every column means the
//! same at every depth, a count is a bare number (the noun is in the
//! tooltip), the size is in one unit down the column, chosen from the
//! largest value and named in the header, with two decimals, and an empty
//! cell is an em dash.

use th_cleanup::overview::{CategoryOverview, DETAIL, ERROR_DETAIL, Reason, Row, Tone, biggest, plural};
use th_cleanup::py::{human, thousands};

/// The number columns' headers (the size column's names its [`Unit`]) and
/// widths.
pub const FOUND: (&str, f32) = ("Found", 56.0);
pub const TO_PURGE: (&str, f32) = ("To purge", 66.0);
pub const FREES_WIDTH: f32 = 82.0;

/// What an empty cell shows.
pub const EMPTY: &str = "\u{2014}";

/// The size column's unit: the largest of B, KB, MB, GB and TB in which the
/// largest value is at least 1, so a small project reads "4 B", not
/// "< 0.01" GB.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unit {
    pub name: &'static str,
    pub bytes: u64,
}

const UNITS: [Unit; 5] = [
    Unit { name: "B", bytes: 1 },
    Unit { name: "KB", bytes: 1 << 10 },
    Unit { name: "MB", bytes: 1 << 20 },
    Unit { name: "GB", bytes: 1 << 30 },
    Unit { name: "TB", bytes: 1 << 40 },
];

impl Unit {
    /// The unit for a column whose largest value is `largest` bytes.
    pub fn for_largest(largest: u64) -> Unit {
        UNITS.iter().rev().copied().find(|u| largest >= u.bytes).unwrap_or(UNITS[0])
    }

    /// The size column's header.
    pub fn header(self) -> String {
        format!("Frees ({})", self.name)
    }

    /// `bytes` in this unit: whole bytes, or two decimals; a size too small
    /// to show is "< 0.01", never a misleading 0.00.
    pub fn format(self, bytes: u64) -> String {
        if self.bytes == 1 {
            return thousands(bytes as usize);
        }
        let value = bytes as f64 / self.bytes as f64;
        if bytes > 0 && value < 0.005 { "< 0.01".into() } else { format!("{value:.2}") }
    }
}

fn row(cells: [&str; 4], tone: Tone, tip: &str) -> Row {
    Row { cells: cells.map(String::from), tone, tip: tip.into(), children: Vec::new(), expanded: false }
}

fn reason_row(category: &CategoryOverview, reason: &Reason, unit: Unit) -> Row {
    let count = thousands(reason.count);
    if reason.prune {
        return row(
            [&reason.reason, &count, &count, &unit.format(reason.size)],
            Tone::Normal,
            &format!("{} go: {}", plural(reason.count, &category.noun), reason.reason),
        );
    }
    row(
        [&reason.reason, &count, EMPTY, EMPTY],
        Tone::Dim,
        &format!("{} stay ({}): {}", plural(reason.count, &category.noun), human(reason.size), reason.reason),
    )
}

/// A category's row and what opens under it, sizes in `unit`.
pub fn category_row(category: &CategoryOverview, unit: Unit) -> Row {
    let mut children: Vec<Row> = category.warnings.iter().map(|text| row([text, "", "", ""], Tone::Warning, text)).collect();
    if !category.reasons.is_empty() {
        let mut why = row(["Why", "", "", ""], Tone::Dim, "What goes and what stays, by reason");
        why.children = category.reasons.iter().map(|r| reason_row(category, r, unit)).collect();
        why.expanded = true;
        children.push(why);
    }
    if !category.groups.is_empty() {
        let (shown, rest) = biggest(&category.groups, DETAIL);
        let mut rows: Vec<Row> =
            shown.iter().map(|g| row([&g.name, "", &thousands(g.count), &unit.format(g.size)], Tone::Normal, &g.name)).collect();
        if let Some(rest) = rest {
            rows.push(row([&rest.name, "", &thousands(rest.count), &unit.format(rest.size)], Tone::Dim, ""));
        }
        let mut biggest_row = row(["To purge, biggest first", "", "", ""], Tone::Dim, "What goes, by entity or folder, the biggest first");
        biggest_row.children = rows;
        children.push(biggest_row);
    }
    children.extend(category.notes.iter().map(|text| row([text, "", "", ""], Tone::Dim, text)));
    if !category.errors.is_empty() {
        let mut shown: Vec<Row> = category.errors.iter().take(ERROR_DETAIL).map(|e| row([e, "", "", ""], Tone::Error, e)).collect();
        if category.errors.len() > ERROR_DETAIL {
            shown.push(row([&format!("({} more)", thousands(category.errors.len() - ERROR_DETAIL)), "", "", ""], Tone::Error, ""));
        }
        let mut errors = row(
            [&format!("Could not be read: {}", thousands(category.errors.len())), "", "", ""],
            Tone::Error,
            "What could not be read is kept",
        );
        errors.children = shown;
        children.push(errors);
    }
    let frees = if category.to_delete > 0 { unit.format(category.size) } else { EMPTY.into() };
    let tone = if category.warnings.is_empty() { Tone::Heading } else { Tone::Warning };
    let tip = format!(
        "{}: {} to purge ({}), {} kept ({})",
        plural(category.found, &category.noun),
        thousands(category.to_delete),
        human(category.size),
        thousands(category.kept()),
        human(category.kept_size)
    );
    let mut line = row([&category.title, &thousands(category.found), &thousands(category.to_delete), &frees], tone, &tip);
    line.children = children;
    // A category whose scan warns opens, so the warning is in view.
    line.expanded = !category.warnings.is_empty();
    line
}

/// A warning as a notice says it: its first sentence (the notice's bold
/// title, after the category) and the rest.
pub fn split_sentence(text: &str) -> (String, String) {
    match text.find(". ") {
        Some(at) => (text[..=at].to_string(), text[at + 2..].to_string()),
        None => {
            let mut first = text.trim_end().to_string();
            if !first.ends_with('.') {
                first.push('.');
            }
            (first, String::new())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn the_unit_comes_from_the_largest_value() {
        assert_eq!(Unit::for_largest(0).name, "B");
        assert_eq!(Unit::for_largest(4).name, "B");
        assert_eq!(Unit::for_largest(1023).name, "B");
        assert_eq!(Unit::for_largest(1024).name, "KB");
        assert_eq!(Unit::for_largest(5 << 20).name, "MB");
        assert_eq!(Unit::for_largest(9 << 30).name, "GB");
        assert_eq!(Unit::for_largest(3 << 40).name, "TB");
        assert_eq!(Unit::for_largest(4).format(4), "4");
        assert_eq!(Unit::for_largest(4).header(), "Frees (B)");
        let gb = Unit::for_largest(1 << 30);
        assert_eq!(gb.format(1 << 30), "1.00");
        assert_eq!(gb.format(5 * (1 << 30) / 2), "2.50");
        assert_eq!(gb.format(4), "< 0.01");
        assert_eq!(gb.format(0), "0.00");
        assert_eq!(split_sentence("Deadline could not be asked"), ("Deadline could not be asked.".into(), String::new()));
        assert_eq!(split_sentence("One. Two three."), ("One.".into(), "Two three.".into()));
    }

    proptest! {
        /// The largest value reads as at least 1 and under 1024 of its unit
        /// (below a TB), and every size in a scaled unit has two decimals.
        #[test]
        fn the_largest_value_reads_in_its_unit(largest in 1u64..(1u64 << 50), other in any::<u64>()) {
            let unit = Unit::for_largest(largest);
            let value = largest as f64 / unit.bytes as f64;
            prop_assert!(value >= 1.0);
            prop_assert!(value < 1024.0 || unit.name == "TB");
            if unit.bytes > 1 {
                let shown = unit.format(other);
                prop_assert!(shown == "< 0.01" || shown.split('.').nth(1).is_some_and(|d| d.len() == 2), "{}", shown);
            }
        }
    }
}
