//! What the window's left side holds, apart from how it is drawn: the
//! settings each category shows, their text, and the [`Settings`] they make
//! (the Python window's `_FIELDS`, `section_summary` and `sentence`).

use th_cleanup::Settings;

/// One setting a category's row shows: its [`Settings`] field, the label
/// before its field, the unit after it, what it means (its tooltip), and the
/// range it takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Field {
    pub name: &'static str,
    pub label: &'static str,
    pub unit: &'static str,
    pub tip: &'static str,
    pub low: i64,
    pub high: i64,
}

const fn field(name: &'static str, label: &'static str, unit: &'static str, tip: &'static str, low: i64, high: i64) -> Field {
    Field { name, label, unit, tip, low, high }
}

/// Per category, in the order listed, the settings its row shows. Labels
/// are short so they sit right-aligned in one column at the narrowest the
/// window goes; the tooltip says the whole rule.
pub const FIELDS: [(&str, &[Field]); 6] = [
    ("exports", &[field("keep_exports", "Keep", "newest finished", "Keep the newest finished versions of each export.", 1, 50)]),
    (
        "renders",
        &[
            field("keep_renders", "Keep", "newest complete", "Keep the newest complete versions of each render.", 1, 50),
            field("check_render_days", "Checks older than", "days", "Check renders older than this many days go.", 0, 3650),
        ],
    ),
    ("caches", &[field("keep_caches", "Keep", "newest finished", "Keep the newest finished versions of each th::cache.", 1, 50)]),
    (
        "backups",
        &[
            field("keep_backups", "Keep", "newest", "Keep the newest backups of each workfile's latest version.", 0, 100),
            field("backup_days", "Keep newer than", "days", "No backup newer than this many days goes.", 0, 3650),
        ],
    ),
    (
        "jobs",
        &[
            field("job_days", "Off Deadline for", "days", "A job folder Deadline has not named for this many days goes.", 0, 3650),
            field("local_job_days", "Local jobs after", "days", "A local farm job's folder goes after this many days.", 0, 3650),
        ],
    ),
    (
        "deleted",
        &[field("deleted_days", "Deleted over", "days ago", "Entities deleted more than this many days ago go for good.", 0, 3650)],
    ),
];

/// Every field, in the order the rows show them.
pub fn all_fields() -> impl Iterator<Item = &'static Field> {
    FIELDS.iter().flat_map(|(_, fields)| fields.iter())
}

/// The fields of category `key`.
pub fn fields_of(key: &str) -> &'static [Field] {
    FIELDS.iter().find(|(k, _)| *k == key).map_or(&[], |(_, fields)| fields)
}

/// A setting's value in `settings`, as the window's whole number.
pub fn value(settings: &Settings, name: &str) -> i64 {
    match name {
        "keep_exports" => settings.keep_exports,
        "keep_renders" => settings.keep_renders,
        "check_render_days" => settings.check_render_days as i64,
        "keep_caches" => settings.keep_caches,
        "keep_backups" => settings.keep_backups,
        "backup_days" => settings.backup_days as i64,
        "job_days" => settings.job_days as i64,
        "local_job_days" => settings.local_job_days as i64,
        "deleted_days" => settings.deleted_days as i64,
        _ => 0,
    }
}

/// `settings` with the setting `name` at `value`.
pub fn set(settings: &mut Settings, name: &str, value: i64) {
    match name {
        "keep_exports" => settings.keep_exports = value,
        "keep_renders" => settings.keep_renders = value,
        "check_render_days" => settings.check_render_days = value as f64,
        "keep_caches" => settings.keep_caches = value,
        "keep_backups" => settings.keep_backups = value,
        "backup_days" => settings.backup_days = value as f64,
        "job_days" => settings.job_days = value as f64,
        "local_job_days" => settings.local_job_days = value as f64,
        "deleted_days" => settings.deleted_days = value as f64,
        _ => {}
    }
}

/// A category's `about` as a sentence (its name's tooltip); a node name (`th::cache`) keeps its
/// case.
pub fn sentence(about: &str) -> String {
    if about.starts_with("th::") {
        return format!("{about}.");
    }
    let mut chars = about.chars();
    let first = chars.next().map(|c| c.to_uppercase().collect::<String>()).unwrap_or_default();
    format!("{first}{}.", chars.as_str())
}

/// What is typed into a setting's field, as its value: a whole number (or
/// a sum, `+=1`, from `current`) held to the field's range. None for text
/// that is no number.
pub fn read_field(field: &Field, text: &str, current: i64) -> Option<i64> {
    let number = th_pipe::number_expr::evaluate(text.trim(), Some(current as f64)).ok()?;
    let whole = th_pipe::number_expr::whole(number)?;
    Some(whole.clamp(field.low, field.high))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// Every field is a setting, its default is in its range, and setting
    /// it reads back.
    #[test]
    fn every_field_is_a_setting() {
        let defaults = Settings::default();
        for (key, fields) in FIELDS {
            assert!(th_cleanup::core::CATEGORY_KEYS.contains(&key), "{key}");
            for f in fields {
                let value = value(&defaults, f.name);
                assert!((f.low..=f.high).contains(&value), "{} {value}", f.name);
                let mut settings = Settings::default();
                set(&mut settings, f.name, f.high);
                assert_eq!(super::value(&settings, f.name), f.high, "{}", f.name);
            }
        }
        assert_eq!(FIELDS.map(|(k, _)| k), th_cleanup::core::CATEGORY_KEYS);
    }

    #[test]
    fn about_reads_as_a_sentence() {
        assert_eq!(sentence("th::cache versions"), "th::cache versions.");
        assert_eq!(sentence("export versions"), "Export versions.");
        assert_eq!(sentence(""), ".");
    }

    proptest! {
        /// A field takes a whole number or a sum, held to its range, and
        /// refuses anything else.
        #[test]
        fn fields_hold_to_their_range(index in 0usize..9, n in -10_000i64..10_000, step in 0i64..100, current in 0i64..60) {
            let field = all_fields().nth(index).expect("nine fields");
            prop_assert_eq!(read_field(field, &n.to_string(), current), Some(n.clamp(field.low, field.high)));
            prop_assert_eq!(read_field(field, &format!(" +={step} "), current), Some((current + step).clamp(field.low, field.high)));
            prop_assert_eq!(read_field(field, &format!("{n}x"), current), None);
        }
    }
}
