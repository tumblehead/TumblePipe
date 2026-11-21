//! What Python's `str` and `re` do, where the cleanup's rules lean on it.
//!
//! The Python package is the specification, and its patterns are Unicode
//! patterns: `\d` is any decimal digit (category Nd, not only ASCII), and
//! `int()` reads every one of them; `\s` and `str.strip()` take Python's
//! whitespace, which has the four information separators U+001C..U+001F
//! that Rust's `char::is_whitespace` has not; `str.splitlines()` breaks on
//! more than `\n`. Each helper here is the Python one, so a name or a line
//! reads the same in both (`tests/test_cleanup_parity.py`).

/// The runs of Unicode decimal digits (category Nd) as Python 3.12 knows
/// them (Unicode 15.0). Every run starts at a zero and holds whole tens.
const DIGIT_RUNS: [(u32, u32); 64] = [
    (0x30, 0x39),
    (0x660, 0x669),
    (0x6F0, 0x6F9),
    (0x7C0, 0x7C9),
    (0x966, 0x96F),
    (0x9E6, 0x9EF),
    (0xA66, 0xA6F),
    (0xAE6, 0xAEF),
    (0xB66, 0xB6F),
    (0xBE6, 0xBEF),
    (0xC66, 0xC6F),
    (0xCE6, 0xCEF),
    (0xD66, 0xD6F),
    (0xDE6, 0xDEF),
    (0xE50, 0xE59),
    (0xED0, 0xED9),
    (0xF20, 0xF29),
    (0x1040, 0x1049),
    (0x1090, 0x1099),
    (0x17E0, 0x17E9),
    (0x1810, 0x1819),
    (0x1946, 0x194F),
    (0x19D0, 0x19D9),
    (0x1A80, 0x1A89),
    (0x1A90, 0x1A99),
    (0x1B50, 0x1B59),
    (0x1BB0, 0x1BB9),
    (0x1C40, 0x1C49),
    (0x1C50, 0x1C59),
    (0xA620, 0xA629),
    (0xA8D0, 0xA8D9),
    (0xA900, 0xA909),
    (0xA9D0, 0xA9D9),
    (0xA9F0, 0xA9F9),
    (0xAA50, 0xAA59),
    (0xABF0, 0xABF9),
    (0xFF10, 0xFF19),
    (0x104A0, 0x104A9),
    (0x10D30, 0x10D39),
    (0x11066, 0x1106F),
    (0x110F0, 0x110F9),
    (0x11136, 0x1113F),
    (0x111D0, 0x111D9),
    (0x112F0, 0x112F9),
    (0x11450, 0x11459),
    (0x114D0, 0x114D9),
    (0x11650, 0x11659),
    (0x116C0, 0x116C9),
    (0x11730, 0x11739),
    (0x118E0, 0x118E9),
    (0x11950, 0x11959),
    (0x11C50, 0x11C59),
    (0x11D50, 0x11D59),
    (0x11DA0, 0x11DA9),
    (0x11F50, 0x11F59),
    (0x16A60, 0x16A69),
    (0x16AC0, 0x16AC9),
    (0x16B50, 0x16B59),
    (0x1D7CE, 0x1D7FF),
    (0x1E140, 0x1E149),
    (0x1E2F0, 0x1E2F9),
    (0x1E4F0, 0x1E4F9),
    (0x1E950, 0x1E959),
    (0x1FBF0, 0x1FBF9),
];

/// The value of `c` when it is a decimal digit (`re`'s `\d`, what `int()`
/// reads), else None.
pub fn digit(c: char) -> Option<u32> {
    let code = c as u32;
    DIGIT_RUNS.iter().find(|(first, last)| (*first..=*last).contains(&code)).map(|(first, _)| (code - first) % 10)
}

/// `re`'s `\d`.
pub fn is_digit(c: char) -> bool {
    digit(c).is_some()
}

/// `str.isspace()` and `re`'s `\s`.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.strip(chars)`.
pub fn strip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_matches(|c| chars.contains(c))
}

/// Whether `c` ends a line for `str.splitlines()`.
fn is_line_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// `str.splitlines()`: lines without their ends, `\r\n` one end.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((at, c)) = chars.next() {
        if !is_line_break(c) {
            continue;
        }
        lines.push(&text[start..at]);
        let mut end = at + c.len_utf8();
        if c == '\r'
            && let Some(&(next, '\n')) = chars.peek()
        {
            chars.next();
            end = next + 1;
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `int(text)` for a run of decimal digits (any script), None when it is
/// not one. Saturates past `i128`, which no version or frame reaches.
pub fn int_digits(text: &str) -> Option<i128> {
    if text.is_empty() {
        return None;
    }
    let mut value: i128 = 0;
    for c in text.chars() {
        value = value.saturating_mul(10).saturating_add(i128::from(digit(c)?));
    }
    Some(value)
}

/// `int(text)` for a string, as Python reads one: surrounding whitespace,
/// a sign, digits of any script with single underscores between them.
pub fn int_str(text: &str) -> Option<i128> {
    let body = strip(text);
    let (negative, body) = match body.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, body.strip_prefix('+').unwrap_or(body)),
    };
    if body.is_empty() || body.starts_with('_') || body.ends_with('_') || body.contains("__") {
        return None;
    }
    let value = int_digits(&body.replace('_', ""))?;
    Some(if negative { -value } else { value })
}

/// `{value:g}` (six significant digits, no trailing zeros, an exponent
/// below 1e-4 or from 1e6).
pub fn format_g(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    let precision = 6usize;
    // The exponent after rounding to `precision` significant digits.
    let scientific = format!("{:.*e}", precision - 1, value);
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    if exponent < -4 || exponent >= precision as i32 {
        let mantissa = trim_zeros(mantissa);
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    } else {
        let decimals = (precision as i32 - 1 - exponent).max(0) as usize;
        trim_zeros(&format!("{value:.decimals$}")).to_string()
    }
}

fn trim_zeros(text: &str) -> &str {
    if text.contains('.') { text.trim_end_matches('0').trim_end_matches('.') } else { text }
}

/// `f'{count:,}'`.
pub fn thousands(count: usize) -> String {
    let digits = count.to_string();
    let mut out = String::new();
    for (index, c) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// `cleanup.core.human`: a byte count as `812 B`, `4.8 MB`.
pub fn human(count: u64) -> String {
    let mut size = count as f64;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if size < 1024.0 || unit == "TB" {
            return if unit == "B" { format!("{} B", size as u64) } else { format!("{size:.1} {unit}") };
        }
        size /= 1024.0;
    }
    format!("{count} B")
}

/// Text padded on the right to `width` characters (`f'{text:<width}'`).
pub fn left(text: &str, width: usize) -> String {
    let count = text.chars().count();
    if count >= width { text.to_string() } else { format!("{text}{}", " ".repeat(width - count)) }
}

/// Text padded on the left to `width` characters (`f'{text:>width}'`).
pub fn right(text: &str, width: usize) -> String {
    let count = text.chars().count();
    if count >= width { text.to_string() } else { format!("{}{text}", " ".repeat(width - count)) }
}

/// Python's truth of a JSON value: empty containers, `""`, `0`, `false`
/// and `null` are false.
pub fn truthy(value: &serde_json::Value) -> bool {
    use serde_json::Value;
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python's `str()` of a JSON value read with `json.load`.
pub fn str_of(value: &serde_json::Value) -> String {
    use serde_json::Value;
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        Value::Number(n) => {
            if n.is_f64() {
                let f = n.as_f64().unwrap_or(0.0);
                if f.fract() == 0.0 && f.abs() < 1e16 { format!("{f:.1}") } else { format!("{f}") }
            } else {
                n.to_string()
            }
        }
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digits_of_every_script() {
        assert_eq!(digit('7'), Some(7));
        assert_eq!(digit('\u{663}'), Some(3));
        assert_eq!(digit('\u{1D7D8}'), Some(0));
        assert_eq!(digit('\u{1D7D9}'), Some(1));
        assert_eq!(digit('²'), None);
        assert_eq!(int_digits("0\u{663}"), Some(3));
        assert_eq!(int_str(" -1_000 "), Some(-1000));
        assert_eq!(int_str("1__0"), None);
        assert_eq!(int_str(""), None);
    }

    #[test]
    fn lines_split_as_python() {
        assert_eq!(splitlines("a\r\nb\rc\nd\u{1c}e\u{85}"), vec!["a", "b", "c", "d", "e"]);
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("\n"), vec![""]);
        assert_eq!(splitlines("x\n\ny"), vec!["x", "", "y"]);
    }

    #[test]
    fn g_and_sizes_format_as_python() {
        for (value, text) in [
            (14.0, "14"),
            (2.0, "2"),
            (0.5, "0.5"),
            (1e-5, "1e-05"),
            (1234567.0, "1.23457e+06"),
            (100000.0, "100000"),
            (0.0001, "0.0001"),
            (123456.5, "123456"),
            (0.30000000000000004, "0.3"),
        ] {
            assert_eq!(format_g(value), text, "{value}");
        }
        assert_eq!(human(812), "812 B");
        assert_eq!(human(1280), "1.2 KB");
        assert_eq!(thousands(1234567), "1,234,567");
        assert_eq!(thousands(12), "12");
    }
}
