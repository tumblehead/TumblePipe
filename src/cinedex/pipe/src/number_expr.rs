//! Maths typed into a number field: `100/2` sets 50, `(1001+48)*2` sets
//! 2098, and `+=10` adds ten to what the field holds. One small evaluator,
//! never the language's own `eval`. Cinedex's number fields read their text
//! through it, and TumblePipe's Qt fields through the same rules in Python
//! (`tumblepipe.util.number_expr`); `tests/test_number_expr_parity.py`
//! holds the two to the same answer for the same text, to the bit.
//!
//! The grammar, whole:
//!
//! ```text
//! field    := relative? sum
//! relative := "+=" | "-=" | "*=" | "/="
//! sum      := product (("+" | "-") product)*
//! product  := signed (("*" | "/") signed)*
//! signed   := ("+" | "-")? primary
//! primary  := number | "(" sum ")"
//! number   := digit+ ("." digit*)? | "." digit+
//! ```
//!
//! - Spaces and tabs may stand between any two tokens, nowhere else.
//! - Digits are ASCII; there is no exponent, no thousands separator, no
//!   name (`nan`, `inf`, `pi`) and no decimal comma.
//! - A sign belongs to the number or bracket right after it, once: `2--3`
//!   is 5 and `-(-2)` is 2, but `--2` is refused (a slip of the finger far
//!   more often than a double negative).
//! - **`+10` is ten, and `-10` is minus ten.** A leading sign is never
//!   "relative to what is there": a field that takes negative numbers could
//!   not tell `-10` the value from `-10` the step. Relative entry is spelt
//!   out instead, `+=10`, `-=10`, `*=2`, `/=2`, which means the field's
//!   value with that done to it and is refused where there is no value.
//! - The arithmetic is IEEE double, left to right. Dividing by zero is
//!   refused, as is any step whose result is not a finite number.
//! - At most [`MAX_LENGTH`] characters and [`MAX_DEPTH`] brackets deep.
//!
//! A whole-number field takes the result through [`whole`]: rounded to the
//! nearest whole number, a half away from zero (`7/2` is 4, `-7/2` is -4).

/// The longest text read, in bytes, after trimming.
pub const MAX_LENGTH: usize = 200;
/// How deep brackets may nest.
pub const MAX_DEPTH: usize = 32;
/// The largest whole number [`whole`] gives (and the smallest, negated):
/// every whole number up to it is exact in a double.
pub const MAX_WHOLE: f64 = 9_007_199_254_740_992.0;

/// Why a text is no number.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Problem {
    /// Nothing typed.
    Empty,
    /// Longer than [`MAX_LENGTH`].
    TooLong,
    /// Brackets nested deeper than [`MAX_DEPTH`].
    TooDeep,
    /// Not in the grammar.
    Syntax,
    /// A division by zero.
    DivideByZero,
    /// A step whose result is past what a double holds.
    NotFinite,
    /// `+=` and its like where the field has no value to start from.
    NoValue,
}

impl Problem {
    /// A short name, the same in the Python evaluator (`ExprError.code`).
    pub fn code(self) -> &'static str {
        match self {
            Problem::Empty => "empty",
            Problem::TooLong => "long",
            Problem::TooDeep => "deep",
            Problem::Syntax => "syntax",
            Problem::DivideByZero => "divide",
            Problem::NotFinite => "range",
            Problem::NoValue => "novalue",
        }
    }
}

impl std::fmt::Display for Problem {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Problem::Empty => "nothing typed",
            Problem::TooLong => "too long to be a number",
            Problem::TooDeep => "too many brackets inside each other",
            Problem::Syntax => "not a number or a sum of numbers (+ - * / and brackets)",
            Problem::DivideByZero => "it divides by zero",
            Problem::NotFinite => "the result is too large",
            Problem::NoValue => "there is no value here to change",
        })
    }
}

struct Parser<'a> {
    text: &'a [u8],
    at: usize,
}

impl Parser<'_> {
    fn skip_space(&mut self) {
        while matches!(self.text.get(self.at), Some(b' ' | b'\t')) {
            self.at += 1;
        }
    }

    /// The next token's first byte, past any space.
    fn peek(&mut self) -> Option<u8> {
        self.skip_space();
        self.text.get(self.at).copied()
    }

    fn sum(&mut self, depth: usize) -> Result<f64, Problem> {
        let mut value = self.product(depth)?;
        while let Some(op @ (b'+' | b'-')) = self.peek() {
            self.at += 1;
            let right = self.product(depth)?;
            value = finite(if op == b'+' { value + right } else { value - right })?;
        }
        Ok(value)
    }

    fn product(&mut self, depth: usize) -> Result<f64, Problem> {
        let mut value = self.signed(depth)?;
        while let Some(op @ (b'*' | b'/')) = self.peek() {
            self.at += 1;
            let right = self.signed(depth)?;
            value = apply(op, value, right)?;
        }
        Ok(value)
    }

    fn signed(&mut self, depth: usize) -> Result<f64, Problem> {
        match self.peek() {
            Some(b'-') => {
                self.at += 1;
                Ok(-self.primary(depth)?)
            }
            Some(b'+') => {
                self.at += 1;
                self.primary(depth)
            }
            _ => self.primary(depth),
        }
    }

    fn primary(&mut self, depth: usize) -> Result<f64, Problem> {
        match self.peek() {
            Some(b'(') => {
                if depth >= MAX_DEPTH {
                    return Err(Problem::TooDeep);
                }
                self.at += 1;
                let value = self.sum(depth + 1)?;
                if self.peek() != Some(b')') {
                    return Err(Problem::Syntax);
                }
                self.at += 1;
                Ok(value)
            }
            Some(b'0'..=b'9' | b'.') => self.number(),
            _ => Err(Problem::Syntax),
        }
    }

    fn number(&mut self) -> Result<f64, Problem> {
        let start = self.at;
        let digits = |p: &mut Self| {
            let from = p.at;
            while matches!(p.text.get(p.at), Some(b'0'..=b'9')) {
                p.at += 1;
            }
            p.at - from
        };
        let whole = digits(self);
        let mut fraction = 0;
        if self.text.get(self.at) == Some(&b'.') {
            self.at += 1;
            fraction = digits(self);
        }
        if whole + fraction == 0 {
            return Err(Problem::Syntax);
        }
        // ASCII digits and at most one point: always a float literal.
        let literal = std::str::from_utf8(&self.text[start..self.at]).map_err(|_| Problem::Syntax)?;
        finite(literal.parse::<f64>().map_err(|_| Problem::Syntax)?)
    }
}

fn finite(value: f64) -> Result<f64, Problem> {
    if value.is_finite() { Ok(value) } else { Err(Problem::NotFinite) }
}

/// `left op right` for `*` and `/` (and `+`, `-` of a relative entry).
fn apply(op: u8, left: f64, right: f64) -> Result<f64, Problem> {
    finite(match op {
        b'+' => left + right,
        b'-' => left - right,
        b'*' => left * right,
        _ => {
            if right == 0.0 {
                return Err(Problem::DivideByZero);
            }
            left / right
        }
    })
}

/// The number `text` stands for. `current` is what the field holds now,
/// which `+=`, `-=`, `*=` and `/=` start from.
pub fn evaluate(text: &str, current: Option<f64>) -> Result<f64, Problem> {
    let text = text.trim_matches([' ', '\t']);
    if text.is_empty() {
        return Err(Problem::Empty);
    }
    if text.len() > MAX_LENGTH {
        return Err(Problem::TooLong);
    }
    let bytes = text.as_bytes();
    let relative = match bytes {
        [op @ (b'+' | b'-' | b'*' | b'/'), b'=', ..] => Some(*op),
        _ => None,
    };
    let mut parser = Parser { text: bytes, at: if relative.is_some() { 2 } else { 0 } };
    let value = parser.sum(0)?;
    if parser.peek().is_some() {
        return Err(Problem::Syntax);
    }
    let value = match relative {
        None => value,
        Some(op) => apply(op, current.filter(|c| c.is_finite()).ok_or(Problem::NoValue)?, value)?,
    };
    // No minus zero: a field never shows "-0".
    Ok(if value == 0.0 { 0.0 } else { value })
}

/// `value` as a whole-number field takes it: the nearest whole number, a
/// half away from zero. None past [`MAX_WHOLE`] either way, or for no
/// number.
pub fn whole(value: f64) -> Option<i64> {
    let rounded = value.round();
    (rounded.is_finite() && rounded.abs() <= MAX_WHOLE).then_some(rounded as i64)
}

/// Whether `text` is one plain number, as a field shows a value: digits
/// with at most one point and at most one leading minus, no maths.
pub fn is_plain(text: &str) -> bool {
    let text = text.trim_matches([' ', '\t']);
    let digits = text.strip_prefix('-').unwrap_or(text);
    let (whole, fraction) = digits.split_once('.').unwrap_or((digits, ""));
    !(whole.is_empty() && fraction.is_empty()) && whole.bytes().chain(fraction.bytes()).all(|b| b.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// An expression as generated: the tree the text is written from, so
    /// the reference answer never goes through the parser.
    #[derive(Debug, Clone)]
    enum Tree {
        Number(String),
        Negated(Box<Tree>),
        Plus(Box<Tree>),
        Op(Box<Tree>, char, Box<Tree>),
    }

    fn precedence(op: char) -> u8 {
        if matches!(op, '+' | '-') { 1 } else { 2 }
    }

    /// The tree's value, by plain recursion; None where the evaluator must
    /// refuse (a division by zero, a result not finite).
    fn reference(tree: &Tree) -> Option<f64> {
        let value = match tree {
            Tree::Number(text) => text.parse::<f64>().unwrap(),
            Tree::Negated(inner) => -reference(inner)?,
            Tree::Plus(inner) => reference(inner)?,
            Tree::Op(left, op, right) => {
                let (l, r) = (reference(left)?, reference(right)?);
                match op {
                    '+' => l + r,
                    '-' => l - r,
                    '*' => l * r,
                    _ if r == 0.0 => return None,
                    _ => l / r,
                }
            }
        };
        value.is_finite().then_some(value)
    }

    /// The tree as text: brackets only where the grammar needs them (and
    /// where `extra` asks), `pad` between tokens.
    fn written(tree: &Tree, pad: &str, extra: bool) -> String {
        fn operand(tree: &Tree, least: u8, pad: &str, extra: bool) -> String {
            let text = written(tree, pad, extra);
            let binds = match tree {
                Tree::Op(_, op, _) => precedence(*op),
                _ => 3,
            };
            if binds < least || extra { format!("({pad}{text}{pad})") } else { text }
        }
        match tree {
            Tree::Number(text) => text.clone(),
            // A sign takes a primary: anything else goes in brackets.
            Tree::Negated(inner) | Tree::Plus(inner) => {
                let sign = if matches!(tree, Tree::Negated(_)) { '-' } else { '+' };
                let inner_text = written(inner, pad, extra);
                if matches!(**inner, Tree::Number(_)) && !extra {
                    format!("{sign}{inner_text}")
                } else {
                    format!("{sign}({pad}{inner_text}{pad})")
                }
            }
            Tree::Op(left, op, right) => {
                let p = precedence(*op);
                // Left to right: a right operand of the same strength is
                // bracketed. A signed operand needs none: the sign is its own.
                let l = operand(left, p, pad, extra);
                let r = operand(right, p + 1, pad, extra);
                format!("{l}{pad}{op}{pad}{r}")
            }
        }
    }

    fn numbers() -> impl Strategy<Value = String> {
        prop_oneof!["[0-9]{1,6}", "[0-9]{1,4}\\.[0-9]{1,4}", "\\.[0-9]{1,3}", "[0-9]{1,3}\\.", Just("0".to_owned()), Just("0.0".to_owned()),]
    }

    fn trees() -> impl Strategy<Value = Tree> {
        numbers().prop_map(Tree::Number).prop_recursive(5, 24, 2, |inner| {
            prop_oneof![
                inner.clone().prop_map(|t| Tree::Negated(Box::new(t))),
                inner.clone().prop_map(|t| Tree::Plus(Box::new(t))),
                (inner.clone(), prop::sample::select(vec!['+', '-', '*', '/']), inner).prop_map(|(l, op, r)| Tree::Op(
                    Box::new(l),
                    op,
                    Box::new(r)
                )),
            ]
        })
    }

    fn pads() -> impl Strategy<Value = &'static str> {
        prop::sample::select(vec!["", " ", "\t", "  "])
    }

    fn same(got: f64, want: f64) -> bool {
        got.to_bits() == (if want == 0.0 { 0.0f64 } else { want }).to_bits()
    }

    proptest! {
        /// Any expression of the grammar is the number its tree works out
        /// to, to the bit: `*` and `/` before `+` and `-`, left to right,
        /// brackets first, a sign on what follows it. One that divides by
        /// zero or leaves what a double holds is refused, and says which.
        #[test]
        fn an_expression_is_its_trees_value(tree in trees(), pad in pads(), extra in any::<bool>(), outer in pads()) {
            let text = format!("{outer}{}{outer}", written(&tree, pad, extra));
            prop_assume!(text.trim().len() <= MAX_LENGTH);
            let got = evaluate(&text, None);
            match reference(&tree) {
                Some(want) => {
                    let got = got.map_err(|e| TestCaseError::fail(format!("{text:?} refused: {e}")))?;
                    prop_assert!(same(got, want), "{:?} gave {} for {}", text, got, want);
                }
                None => prop_assert!(matches!(got, Err(Problem::DivideByZero | Problem::NotFinite)), "{:?} gave {:?}", text, got),
            }
        }

        /// A plain number reads as the number it is, with or without a
        /// sign and spaces around it: what every field held before maths.
        #[test]
        fn a_plain_number_is_itself(whole in 0u32..1_000_000, tenths in 0u32..10, sign in prop::sample::select(vec!["", "-", "+"]), pad in pads()) {
            let digits = format!("{whole}.{tenths}");
            let want: f64 = format!("{}{digits}", if sign == "-" { "-" } else { "" }).parse().unwrap();
            let got = evaluate(&format!("{pad}{sign}{digits}{pad}"), None).unwrap();
            prop_assert!(same(got, want));
            prop_assert_eq!(is_plain(&format!("{pad}{sign}{digits}{pad}")), sign != "+");
        }

        /// `+=`, `-=`, `*=` and `/=` are the field's value with the
        /// expression done to it; without a value they are refused, and a
        /// leading sign alone is never relative.
        #[test]
        fn relative_entry_starts_from_the_fields_value(
            current in -10_000i32..10_000, tree in trees(), op in prop::sample::select(vec!['+', '-', '*', '/']), pad in pads(),
        ) {
            let current = f64::from(current) / 8.0;
            let body = written(&tree, pad, false);
            let text = format!("{op}={pad}{body}");
            prop_assume!(text.len() <= MAX_LENGTH);
            let got = evaluate(&text, Some(current));
            let want = reference(&tree).and_then(|right| {
                let v = match op {
                    '+' => current + right,
                    '-' => current - right,
                    '*' => current * right,
                    _ if right == 0.0 => return None,
                    _ => current / right,
                };
                v.is_finite().then_some(v)
            });
            match want {
                Some(want) => prop_assert!(same(got.unwrap(), want), "{:?} from {}", text, current),
                None => prop_assert!(got.is_err()),
            }
            if reference(&tree).is_some() {
                prop_assert_eq!(evaluate(&text, None), Err(Problem::NoValue));
                prop_assert_eq!(evaluate(&text, Some(f64::NAN)), Err(Problem::NoValue));
            }
            // "+5" and "-5" are five and minus five whatever the field holds.
            prop_assert_eq!(evaluate("+5", Some(current)), Ok(5.0));
            prop_assert_eq!(evaluate("-5", Some(current)), Ok(-5.0));
        }

        /// Text that is not in the grammar is refused, never read in part:
        /// a valid expression with one piece that does not belong put
        /// anywhere in it.
        #[test]
        fn anything_else_is_refused(
            tree in trees(), pad in pads(), at in 0f64..1.0,
            junk in prop::sample::select(vec!["x", ",", "e", "%", "nan", "inf", "_", "²", "٣", "**", "//", "\n", "'", "$", "0x", "#", "!"]),
        ) {
            let text = written(&tree, pad, false);
            let cut = (at * text.len() as f64) as usize;
            let broken = format!("{}{junk}{}", &text[..cut], &text[cut..]);
            prop_assume!(broken.len() <= MAX_LENGTH);
            let got = evaluate(&broken, Some(1.0));
            prop_assert!(got.is_err(), "{:?} read as {:?}", broken, got);
        }

        /// A whole-number field's value is the nearest whole number, a half
        /// away from zero, and nothing past what a double counts exactly.
        #[test]
        fn a_whole_number_is_the_nearest(halves in -2_000_000i64..2_000_000, nudge in -3i32..4) {
            let value = halves as f64 / 2.0 + f64::from(nudge) * 1e-7;
            let got = whole(value).unwrap();
            // The nearest, worked out in whole numbers of 1e-7.
            let scaled = halves * 5_000_000 + i64::from(nudge);
            let (q, r) = (scaled.div_euclid(10_000_000), scaled.rem_euclid(10_000_000));
            let want = if r > 5_000_000 || (r == 5_000_000 && q >= 0) { q + 1 } else { q };
            prop_assert_eq!(got, want, "{}", value);
        }
    }

    #[test]
    fn the_edges_are_as_documented() {
        assert_eq!(evaluate("100/2", None), Ok(50.0));
        assert_eq!(evaluate("(1001+48)*2", None), Ok(2098.0));
        assert_eq!(evaluate("2--3", None), Ok(5.0));
        assert_eq!(evaluate("-(-2)", None), Ok(2.0));
        assert_eq!(evaluate("2*-3", None), Ok(-6.0));
        assert_eq!(evaluate("1-1", None).map(f64::to_bits), Ok(0f64.to_bits()));
        assert_eq!(evaluate("-0", None).map(f64::to_bits), Ok(0f64.to_bits()));
        assert_eq!(evaluate("+=10", Some(5.0)), Ok(15.0));
        assert_eq!(evaluate("/=0", Some(5.0)), Err(Problem::DivideByZero));
        for refused in ["--2", "2---3", "+-2", "2+", "*2", "()", "(1", "1)", "1 2", "1.2.3", ".", "1e3", "+ =1", "=1", "2(3)"] {
            assert_eq!(evaluate(refused, Some(1.0)), Err(Problem::Syntax), "{refused}");
        }
        assert_eq!(evaluate("", None), Err(Problem::Empty));
        assert_eq!(evaluate(" \t ", None), Err(Problem::Empty));
        assert_eq!(evaluate("1/0", None), Err(Problem::DivideByZero));
        assert_eq!(evaluate("1/(2-2)", None), Err(Problem::DivideByZero));
        assert_eq!(evaluate(&"9".repeat(MAX_LENGTH + 1), None), Err(Problem::TooLong));
        let deep = |n: usize| format!("{}1{}", "(".repeat(n), ")".repeat(n));
        assert_eq!(evaluate(&deep(MAX_DEPTH), None), Ok(1.0));
        assert_eq!(evaluate(&deep(MAX_DEPTH + 1), None), Err(Problem::TooDeep));
        assert_eq!(evaluate("*=10", Some(1e308)), Err(Problem::NotFinite));
        assert_eq!(evaluate("+=1", None), Err(Problem::NoValue));
        assert_eq!(evaluate("+=5", Some(-5.0)).map(f64::to_bits), Ok(0f64.to_bits()));
        assert_eq!(whole(3.5), Some(4));
        assert_eq!(whole(-3.5), Some(-4));
        assert_eq!(whole(0.49999999999999994), Some(0));
        assert_eq!(whole(MAX_WHOLE * 2.0), None);
        assert_eq!(whole(f64::NAN), None);
        assert!(is_plain("12") && is_plain("-1.5") && is_plain(" .5 ") && is_plain("3."));
        assert!(!is_plain("") && !is_plain("-") && !is_plain(".") && !is_plain("1+1") && !is_plain("--1") && !is_plain("1.2.3"));
    }
}
