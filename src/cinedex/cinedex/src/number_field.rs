//! What is typed into a number field, read as a number: maths worked out
//! (`100/2` is 50, `+=10` is ten more than the field holds) by
//! `th_pipe::number_expr`, the one evaluator every number field of the
//! editor reads through, the same rules TumblePipe's Qt fields have. A
//! field then holds the number to its own limits as it always did; text
//! that is no number is refused and the field keeps its value.

use th_pipe::number_expr::{self, Problem};

/// The number `text` stands for. A unit the field shows (`%`, `°`) may
/// end it. `current` is what the field holds, which `+=` and its like
/// start from.
pub fn number(text: &str, units: &[char], current: Option<f64>) -> Result<f64, Problem> {
    let text = text.trim();
    let bare = text.strip_suffix(units).unwrap_or(text);
    number_expr::evaluate(bare, current)
}

/// As [`number`], for a field of whole numbers: the result rounded to the
/// nearest, a half away from zero.
pub fn whole(text: &str, current: Option<i64>) -> Result<i64, Problem> {
    let value = number(text, &[], current.map(|c| c as f64))?;
    number_expr::whole(value).ok_or(Problem::NotFinite)
}

/// Why `text` was refused, for the status line: what the field takes
/// (`takes`, "a fade is a whole number of frames, 0 or more"), or what
/// went wrong in the sum when it was one (a division by zero).
pub fn refusal(text: &str, problem: Option<Problem>, takes: &str) -> String {
    match problem {
        Some(why @ (Problem::DivideByZero | Problem::NotFinite | Problem::NoValue | Problem::TooDeep | Problem::TooLong)) => {
            format!("{text:?}: {why}")
        }
        _ => format!("{text:?}: {takes}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// A field's number is what its text works out to with or without
        /// the field's unit and spaces around it, and a unit that is not
        /// the field's is refused.
        #[test]
        fn a_unit_may_end_the_text(a in -5000i32..5000, b in 1i32..500, op in prop::sample::select(vec!['+', '-', '*', '/']), pad in prop::sample::select(vec!["", " "])) {
            let (x, y) = (f64::from(a) / 4.0, f64::from(b) / 4.0);
            let want = match op { '+' => x.abs() + y, '-' => x.abs() - y, '*' => x.abs() * y, _ => x.abs() / y };
            let body = format!("{}{pad}{op}{pad}{}", x.abs(), y);
            for unit in ["", "%", "\u{b0}"] {
                let text = format!("{pad}{body}{pad}{unit}{pad}");
                prop_assert_eq!(number(&text, &['%', '\u{b0}'], None), Ok(if want == 0.0 { 0.0 } else { want }), "{}", text);
                let own = number(&text, &['%'], None);
                prop_assert_eq!(own.is_ok(), unit != "\u{b0}", "{}", text);
                prop_assert_eq!(number(&text, &[], None).is_ok(), unit.is_empty());
            }
            // Relative entry starts from the field's value.
            prop_assert_eq!(number(&format!("+={y}%"), &['%'], Some(x)), Ok(if x + y == 0.0 { 0.0 } else { x + y }));
            prop_assert_eq!(number(&format!("+={y}"), &[], None), Err(Problem::NoValue));
        }

        /// A whole-number field rounds what its text works out to, a half
        /// away from zero, and starts `+=` from its own whole value.
        #[test]
        fn a_whole_field_rounds(a in -100_000i64..100_000, b in 1i64..1000, current in -1000i64..1000) {
            let exact = |n: i64, d: i64| { let (q, r) = (n.div_euclid(d), n.rem_euclid(d)); if 2 * r > d || (2 * r == d && n >= 0) { q + 1 } else { q } };
            let got = whole(&format!("{}/{b}", a.abs()), None).unwrap();
            // The float quotient is nowhere near a half unless it is one.
            prop_assert_eq!(got, exact(a.abs(), b));
            prop_assert_eq!(whole(&format!("-={}", a.abs()), Some(current)), Ok(current - a.abs()));
            prop_assert_eq!(whole(&format!("{a}"), None), Ok(a));
            prop_assert_eq!(whole("1/0", None), Err(Problem::DivideByZero));
        }
    }

    /// A refusal says what went wrong in a sum, else what the field takes.
    #[test]
    fn a_refusal_says_which() {
        assert_eq!(refusal("1/0", Some(Problem::DivideByZero), "a fade is frames"), "\"1/0\": it divides by zero");
        assert_eq!(refusal("x", Some(Problem::Syntax), "a fade is frames"), "\"x\": a fade is frames");
        assert_eq!(refusal("-3", None, "a fade is frames"), "\"-3\": a fade is frames");
        assert_eq!(refusal("+=1", Some(Problem::NoValue), "x"), "\"+=1\": there is no value here to change");
    }
}
