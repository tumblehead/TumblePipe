//! Migration v10's file edits: drop the separate `alpha` AOV and render the
//! beauty RGBA.
//!
//! The `alpha` RenderVar the project scaffold shipped was sourced from Karma's
//! `ray:hit`, a utility AOV that holdouts do not affect, so in a render layer
//! with the set held out it still counted the set as solid. The beauty's own
//! alpha honours holdouts. `root_default_prims.usda` is every shot's weakest
//! sublayer and every RenderVar in it renders, so the alpha keeps rendering
//! (and an untouched beauty stays RGB) until the project's own file changes.
//!
//! The file is hand-authored per project, so this is a careful text edit, not
//! a USD round trip: everything it does not mean to touch comes through byte
//! for byte. It understands USDA's lexical structure (strings, comments,
//! `( … )` metadata, `{ … }` bodies, `[ … ]` lists) rather than matching
//! lines, and anything it does not recognise is **refused** -- nothing is
//! written and the reason is reported -- instead of guessed at:
//!
//! - the alpha is removed only when the beauty is RGBA afterwards (converted
//!   here or already). A project that would lose its alpha with no RGBA beauty
//!   to replace it would comp every channel opaque;
//! - a beauty that is not a `def RenderVar "beauty"` with one string
//!   `dataType` of `color3f`/`color3h`/`color4f`/`color4h` is refused;
//! - an `orderedVars` target naming the alpha other than as an entry of a list
//!   that keeps at least one other var is refused.

use serde_json::Value;

/// The RenderVar v10 removes.
pub const ALPHA_VAR: &str = "alpha";
/// The RenderVar v10 makes RGBA.
pub const BEAUTY_VAR: &str = "beauty";

/// What v10 does to one file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Edit {
    /// Nothing to do: already through v10, or nothing of the kind in it.
    Unchanged,
    /// The edited text.
    Changed(String),
    /// A shape v10 does not recognise. Nothing may be written; the message
    /// says what was found, in terms a user can act on.
    Refused(String),
}

// ── lexical scanning (byte indexes; every index lands on an ASCII byte) ──

/// Index just past the string literal opening at `i`: `"…"`, `'…'`, the
/// triple-quoted forms, or an `@asset path@`. None when unterminated.
fn skip_string(b: &[u8], i: usize) -> Option<usize> {
    let q = b[i];
    let triple = b.len() >= i + 3 && b[i + 1] == q && b[i + 2] == q;
    let mut j = if triple { i + 3 } else { i + 1 };
    while j < b.len() {
        let c = b[j];
        if c == b'\\' && q != b'@' {
            j += 2;
            continue;
        }
        if triple {
            if b.len() >= j + 3 && b[j] == q && b[j + 1] == q && b[j + 2] == q {
                return Some(j + 3);
            }
        } else if c == q {
            return Some(j + 1);
        } else if c == b'\n' {
            return None;
        }
        j += 1;
    }
    None
}

fn is_quote(c: u8) -> bool {
    c == b'"' || c == b'\'' || c == b'@'
}

fn skip_comment(b: &[u8], mut i: usize) -> usize {
    while i < b.len() && b[i] != b'\n' {
        i += 1;
    }
    i
}

/// The next index at or after `i` that is neither whitespace nor comment.
fn skip_trivia(b: &[u8], mut i: usize) -> usize {
    loop {
        while i < b.len() && b[i].is_ascii_whitespace() {
            i += 1;
        }
        if i < b.len() && b[i] == b'#' {
            i = skip_comment(b, i);
            continue;
        }
        return i;
    }
}

/// Index of the bracket closing the one at `open`, skipping strings and
/// comments. None when unbalanced.
fn find_close(b: &[u8], open: usize) -> Option<usize> {
    let (o, c) = match b[open] {
        b'(' => (b'(', b')'),
        b'{' => (b'{', b'}'),
        b'[' => (b'[', b']'),
        _ => return None,
    };
    let mut depth = 0i32;
    let mut i = open;
    while i < b.len() {
        let x = b[i];
        if is_quote(x) {
            i = skip_string(b, i)?;
            continue;
        }
        if x == b'#' {
            i = skip_comment(b, i);
            continue;
        }
        if x == o {
            depth += 1;
        } else if x == c {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

fn is_ident_byte(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_' || c == b':'
}

fn read_ident(b: &[u8], i: usize) -> usize {
    let mut j = i;
    while j < b.len() && is_ident_byte(b[j]) {
        j += 1;
    }
    j
}

/// Every identifier in `b[from..to]` outside strings and comments, as
/// `(start, end)`.
fn identifiers(b: &[u8], from: usize, to: usize) -> Result<Vec<(usize, usize)>, String> {
    let mut out = Vec::new();
    let mut i = from;
    while i < to {
        let x = b[i];
        if is_quote(x) {
            i = skip_string(b, i).ok_or("an unterminated string")?;
            continue;
        }
        if x == b'#' {
            i = skip_comment(b, i);
            continue;
        }
        if is_ident_byte(x) && (i == 0 || !is_ident_byte(b[i - 1])) {
            let end = read_ident(b, i);
            out.push((i, end));
            i = end;
            continue;
        }
        i += 1;
    }
    Ok(out)
}

fn text(b: &[u8], from: usize, to: usize) -> &str {
    std::str::from_utf8(&b[from..to]).unwrap_or("")
}

/// One prim spec: `<specifier> [Type] "name" [( metadata )] { body }`.
#[derive(Debug, Clone)]
struct Prim {
    specifier: String,
    type_name: Option<String>,
    name: String,
    /// Index of the specifier keyword.
    start: usize,
    body_open: usize,
    body_close: usize,
    /// The prim's path, built from the enclosing prims.
    path: String,
}

/// Every prim spec in a USDA file, nested ones included.
fn scan_prims(b: &[u8]) -> Result<Vec<Prim>, String> {
    let mut prims: Vec<Prim> = Vec::new();
    for (start, end) in identifiers(b, 0, b.len())? {
        let word = text(b, start, end);
        if !matches!(word, "def" | "over" | "class") {
            continue;
        }
        if end >= b.len() || !b[end].is_ascii_whitespace() {
            continue;
        }
        let mut j = skip_trivia(b, end);
        let mut type_name = None;
        if j < b.len() && !is_quote(b[j]) {
            let type_end = read_ident(b, j);
            if type_end == j {
                return Err(format!("a `{word}` at byte {start} is not followed by a prim name"));
            }
            type_name = Some(text(b, j, type_end).to_string());
            j = skip_trivia(b, type_end);
        }
        if j >= b.len() || b[j] != b'"' {
            return Err(format!("a `{word}` at byte {start} has no quoted prim name"));
        }
        let name_end = skip_string(b, j).ok_or("an unterminated prim name")?;
        let name = text(b, j + 1, name_end - 1).to_string();
        j = skip_trivia(b, name_end);
        if j < b.len() && b[j] == b'(' {
            let close = find_close(b, j)
                .ok_or_else(|| format!("prim \"{name}\" has unbalanced ( … ) metadata"))?;
            j = skip_trivia(b, close + 1);
        }
        if j >= b.len() || b[j] != b'{' {
            return Err(format!("prim \"{name}\" has no {{ … }} body where one was expected"));
        }
        let body_close =
            find_close(b, j).ok_or_else(|| format!("prim \"{name}\" has an unbalanced body"))?;
        let parent = prims
            .iter()
            .rev()
            .find(|p| p.body_open < start && start < p.body_close)
            .map(|p| p.path.clone())
            .unwrap_or_default();
        prims.push(Prim {
            specifier: word.to_string(),
            type_name,
            path: format!("{parent}/{name}"),
            name,
            start,
            body_open: j,
            body_close,
        });
    }
    Ok(prims)
}

/// `(literal start, literal end, value)` of each string-valued declaration
/// of attribute `attr` directly in `prim`'s body. Any other form (no value,
/// time samples, a connection, a non-string value) is an error.
fn string_attribute(
    b: &[u8],
    prim: &Prim,
    attr: &str,
) -> Result<Vec<(usize, usize, String)>, String> {
    let mut out = Vec::new();
    for (start, end) in identifiers(b, prim.body_open + 1, prim.body_close)? {
        if text(b, start, end) != attr {
            continue;
        }
        let k = skip_trivia(b, end);
        if k >= b.len() || b[k] != b'=' {
            return Err(format!(
                "`{attr}` on \"{}\" is not a plain `= \"value\"` (no value, time samples \
                 or a connection)",
                prim.name
            ));
        }
        let v = skip_trivia(b, k + 1);
        if v >= b.len() || b[v] != b'"' {
            return Err(format!("`{attr}` on \"{}\" is not a quoted value", prim.name));
        }
        let v_end = skip_string(b, v).ok_or("an unterminated attribute value")?;
        out.push((v, v_end, text(b, v + 1, v_end - 1).to_string()));
    }
    Ok(out)
}

/// The colour-4 spelling of a beauty data type, or None for one v10 does
/// not know.
fn rgba_type(value: &str) -> Option<&'static str> {
    match value {
        "color3f" | "color4f" => Some("color4f"),
        "color3h" | "color4h" => Some("color4h"),
        _ => None,
    }
}

/// Start of the line holding `i`.
fn line_start(b: &[u8], i: usize) -> usize {
    b[..i].iter().rposition(|&c| c == b'\n').map_or(0, |n| n + 1)
}

/// Index of the `\n` ending the line holding `i`, or the text's end.
fn line_end(b: &[u8], i: usize) -> usize {
    b[i..].iter().position(|&c| c == b'\n').map_or(b.len(), |n| i + n)
}

fn blank(b: &[u8], from: usize, to: usize) -> bool {
    b[from..to].iter().all(|c| c.is_ascii_whitespace())
}

/// The span to delete to remove list element `b[start..end]` with its
/// separator, or None when it is the list's only element.
fn element_deletion(b: &[u8], list_open: usize, list_close: usize, start: usize, end: usize) -> Option<(usize, usize)> {
    // The comma after the element, if any (skipping spaces and comments)
    let after = skip_trivia(b, end);
    let comma_after = (after < list_close && b[after] == b',').then_some(after);
    // The comma before it, if any
    let mut k = start;
    while k > list_open + 1 && b[k - 1].is_ascii_whitespace() {
        k -= 1;
    }
    let comma_before = (k > list_open + 1 && b[k - 1] == b',').then(|| k - 1);
    if comma_after.is_none() && comma_before.is_none() {
        return None;
    }
    let ls = line_start(b, start);
    let rest_from = comma_after.map_or(end, |c| c + 1);
    let le = line_end(b, rest_from.min(list_close));
    let own_line = ls > list_open
        && blank(b, ls, start)
        && le < list_close
        && {
            let tail = skip_trivia(b, rest_from);
            tail >= le
        };
    if own_line {
        // Drop the whole line; a last element also takes the comma before it
        let delete_to = (le + 1).min(b.len());
        return Some(match (comma_after, comma_before) {
            (None, Some(c)) => (c, delete_to - 1),
            _ => (ls, delete_to),
        });
    }
    Some(match comma_after {
        Some(c) => {
            let mut to = c + 1;
            while to < list_close && b[to] == b' ' {
                to += 1;
            }
            (start, to)
        }
        None => (comma_before.unwrap(), end),
    })
}

fn apply_deletions(original: &str, mut spans: Vec<(usize, usize, String)>) -> String {
    spans.sort_by(|a, b| b.0.cmp(&a.0));
    let mut out = original.to_string();
    for (from, to, with) in spans {
        out.replace_range(from..to, &with);
    }
    out
}

/// v10 on a `root_default_prims.usda`, as LF text.
pub fn root_defaults_edit(original: &str) -> Edit {
    match root_defaults_edit_inner(original) {
        Ok(None) => Edit::Unchanged,
        Ok(Some(edited)) => match check_migrated(&edited) {
            Ok(()) => Edit::Changed(edited),
            Err(why) => Edit::Refused(format!("the edit did not come out clean ({why})")),
        },
        Err(why) => Edit::Refused(why),
    }
}

fn root_defaults_edit_inner(original: &str) -> Result<Option<String>, String> {
    let b = original.as_bytes();
    let prims = scan_prims(b)?;
    let alphas: Vec<&Prim> = prims.iter().filter(|p| p.name == ALPHA_VAR).collect();
    let beauties: Vec<&Prim> = prims.iter().filter(|p| p.name == BEAUTY_VAR).collect();
    let is_render_var_def =
        |p: &Prim| p.specifier == "def" && p.type_name.as_deref() == Some("RenderVar");

    if alphas.len() > 1 || alphas.iter().any(|p| !is_render_var_def(p)) {
        return Err(format!(
            "it has {} prim spec(s) named \"{ALPHA_VAR}\" and v10 only knows a single \
             `def RenderVar \"{ALPHA_VAR}\"`",
            alphas.len()
        ));
    }
    if beauties.is_empty() {
        if alphas.is_empty() {
            return Ok(None); // no render vars of ours here at all
        }
        return Err(format!(
            "it has an `{ALPHA_VAR}` RenderVar but no `def RenderVar \"{BEAUTY_VAR}\"` to carry \
             the alpha instead; removing the alpha would make every comp opaque"
        ));
    }
    if beauties.len() > 1 || !is_render_var_def(beauties[0]) {
        return Err(format!(
            "its \"{BEAUTY_VAR}\" is not a single `def RenderVar \"{BEAUTY_VAR}\"`"
        ));
    }
    let beauty = beauties[0];

    let mut edits: Vec<(usize, usize, String)> = Vec::new();

    // The beauty goes colour-4, or the whole edit is off
    let data_types = string_attribute(b, beauty, "dataType")?;
    if data_types.len() != 1 {
        return Err(format!(
            "the {BEAUTY_VAR} RenderVar has {} `dataType` declarations, not one (an unauthored \
             dataType means color3f by default)",
            data_types.len()
        ));
    }
    for attr in ["dataType", "driver:parameters:aov:format"] {
        let decls = string_attribute(b, beauty, attr)?;
        if decls.len() > 1 {
            return Err(format!("the {BEAUTY_VAR} RenderVar declares `{attr}` more than once"));
        }
        for (from, to, value) in decls {
            let rgba = rgba_type(&value).ok_or_else(|| {
                format!("the {BEAUTY_VAR} RenderVar's `{attr}` is \"{value}\", not a colour v10 knows")
            })?;
            if rgba != value {
                edits.push((from, to, format!("\"{rgba}\"")));
            }
        }
    }

    if let Some(alpha) = alphas.first() {
        // The prim, on lines of its own, with the blank line before it
        let ls = line_start(b, alpha.start);
        if !blank(b, ls, alpha.start) {
            return Err(format!("the {ALPHA_VAR} RenderVar does not start on a line of its own"));
        }
        let le = line_end(b, alpha.body_close);
        if !blank(b, alpha.body_close + 1, le) {
            return Err(format!("the {ALPHA_VAR} RenderVar does not end on a line of its own"));
        }
        let mut from = ls;
        if ls >= 1 {
            let prev = line_start(b, ls - 1);
            if prev < ls && blank(b, prev, ls) {
                from = prev;
            }
        }
        edits.push((from, (le + 1).min(b.len()), String::new()));

        // Its orderedVars targets
        for (start, end) in identifiers(b, 0, b.len())? {
            if text(b, start, end) != "orderedVars" {
                continue;
            }
            let k = skip_trivia(b, end);
            if k >= b.len() || b[k] != b'=' {
                continue; // a declaration without targets
            }
            let v = skip_trivia(b, k + 1);
            if v < b.len() && b[v] == b'<' {
                let close = b[v..].iter().position(|&c| c == b'>').map(|n| v + n)
                    .ok_or("an unterminated orderedVars target")?;
                if target_is_alpha(text(b, v + 1, close), &alpha.path)? {
                    return Err(format!(
                        "orderedVars is the single target <{}>; with the alpha removed the \
                         product would render nothing",
                        alpha.path
                    ));
                }
                continue;
            }
            if v >= b.len() || b[v] != b'[' {
                continue; // `= None` and the like name no target
            }
            let close = find_close(b, v).ok_or("an unbalanced orderedVars list")?;
            let mut i = v + 1;
            loop {
                i = skip_trivia(b, i);
                if i >= close {
                    break;
                }
                if b[i] == b',' {
                    i += 1;
                    continue;
                }
                if b[i] != b'<' {
                    return Err("an orderedVars list holds something other than <targets>".into());
                }
                let t_end = b[i..close].iter().position(|&c| c == b'>').map(|n| i + n)
                    .ok_or("an unterminated orderedVars target")?;
                if target_is_alpha(text(b, i + 1, t_end), &alpha.path)? {
                    let span = element_deletion(b, v, close, i, t_end + 1).ok_or_else(|| {
                        format!(
                            "orderedVars lists only <{}>; with the alpha removed the product \
                             would render nothing",
                            alpha.path
                        )
                    })?;
                    edits.push((span.0, span.1, String::new()));
                }
                i = t_end + 1;
            }
        }
    }

    if edits.is_empty() {
        return Ok(None);
    }
    // No two edits may overlap (the alpha prim never holds the beauty's
    // attributes or a product's targets)
    let mut sorted = edits.clone();
    sorted.sort();
    if sorted.windows(2).any(|w| w[0].1 > w[1].0) {
        return Err("its edits overlap, which v10 does not expect".into());
    }
    Ok(Some(apply_deletions(original, edits)))
}

/// Whether an orderedVars target names the alpha var. A relative target, or
/// an absolute one ending in `/alpha` that is not the alpha var's path, is a
/// shape v10 does not resolve.
fn target_is_alpha(target: &str, alpha_path: &str) -> Result<bool, String> {
    let target = target.trim();
    let last = target.rsplit('/').next().unwrap_or(target);
    if last != ALPHA_VAR {
        return Ok(false);
    }
    if target == alpha_path {
        return Ok(true);
    }
    Err(format!(
        "orderedVars names <{target}>, which v10 cannot match to the alpha RenderVar at \
         {alpha_path}"
    ))
}

/// The invariant a migrated file holds: parses, no alpha prim, an RGBA beauty
/// (when there is one), no orderedVars target ending in the alpha's name.
fn check_migrated(text_: &str) -> Result<(), String> {
    let b = text_.as_bytes();
    let prims = scan_prims(b)?;
    if prims.iter().any(|p| p.name == ALPHA_VAR) {
        return Err("an alpha prim is still there".into());
    }
    if let Some(beauty) = prims.iter().find(|p| p.name == BEAUTY_VAR) {
        for (_, _, value) in string_attribute(b, beauty, "dataType")? {
            if !value.starts_with("color4") {
                return Err("the beauty is not colour-4".into());
            }
        }
    }
    for (start, end) in identifiers(b, 0, b.len())? {
        if text(b, start, end) != "orderedVars" {
            continue;
        }
        let k = skip_trivia(b, end);
        if k < b.len() && b[k] == b'=' {
            let v = skip_trivia(b, k + 1);
            let stop = if v < b.len() && b[v] == b'[' {
                find_close(b, v).ok_or("an unbalanced orderedVars list")?
            } else {
                line_end(b, v)
            };
            if text(b, v, stop).contains(&format!("/{ALPHA_VAR}>")) {
                return Err("an orderedVars target still names the alpha".into());
            }
        }
    }
    Ok(())
}

/// Drop `alpha` from every `aov_names` list; whether anything changed.
pub fn drop_alpha_aov_names(node: &mut Value) -> bool {
    let mut changed = false;
    match node {
        Value::Array(items) => {
            for item in items {
                changed |= drop_alpha_aov_names(item);
            }
        }
        Value::Object(map) => {
            for (key, value) in map.iter_mut() {
                if key == "aov_names" {
                    if let Value::Array(names) = value {
                        let before = names.len();
                        names.retain(|name| name.as_str() != Some(ALPHA_VAR));
                        changed |= names.len() != before;
                    }
                } else {
                    changed |= drop_alpha_aov_names(value);
                }
            }
        }
        _ => {}
    }
    changed
}

/// v10 on a `context.json`: drop `alpha` from its `aov_names` lists as a text
/// edit, so the file keeps its own formatting. The result is checked against
/// the same edit made on the parsed JSON, and refused if they disagree.
pub fn context_json_edit(original: &str) -> Edit {
    let Ok(parsed) = serde_json::from_str::<Value>(original) else {
        return Edit::Refused("it is not valid JSON".into());
    };
    let mut expected = parsed;
    if !drop_alpha_aov_names(&mut expected) {
        return Edit::Unchanged;
    }
    let b = original.as_bytes();
    let mut edits = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'"' {
            i += 1;
            continue;
        }
        let Some(end) = skip_string(b, i) else {
            return Edit::Refused("it has an unterminated string".into());
        };
        let key = text(b, i + 1, end - 1);
        i = end;
        if key != "aov_names" {
            continue;
        }
        let colon = skip_trivia(b, end);
        if colon >= b.len() || b[colon] != b':' {
            continue; // a value, not a key
        }
        let open = skip_trivia(b, colon + 1);
        if open >= b.len() || b[open] != b'[' {
            continue;
        }
        let Some(close) = find_close(b, open) else {
            return Edit::Refused("an aov_names list is unbalanced".into());
        };
        let mut j = open + 1;
        while j < close {
            if b[j] != b'"' {
                j += 1;
                continue;
            }
            let Some(e) = skip_string(b, j) else {
                return Edit::Refused("it has an unterminated string".into());
            };
            if text(b, j + 1, e - 1) == ALPHA_VAR {
                let span = element_deletion(b, open, close, j, e).unwrap_or((j, e));
                edits.push((span.0, span.1, String::new()));
            }
            j = e;
        }
        i = close + 1;
    }
    let edited = apply_deletions(original, edits);
    match serde_json::from_str::<Value>(&edited) {
        Ok(value) if value == expected => Edit::Changed(edited),
        _ => Edit::Refused(
            "removing \"alpha\" from its aov_names as a text edit did not give the expected \
             JSON"
                .into(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn changed(edit: Edit) -> String {
        match edit {
            Edit::Changed(text) => text,
            other => panic!("expected a change, got {other:?}"),
        }
    }

    fn refused(edit: Edit) -> String {
        match edit {
            Edit::Refused(why) => why,
            other => panic!("expected a refusal, got {other:?}"),
        }
    }

    /// The render vars of a project's root defaults as the scaffold shipped
    /// them before v10, with a `{}` inside a string to trip naive brace counts.
    const BEFORE: &str = concat!(
        "#usda 1.0\n",
        "(\n",
        "    renderSettingsPrimPath = \"/scene/Render/rendersettings\"\n",
        ")\n",
        "\n",
        "def Scope \"scene\"\n",
        "{\n",
        "    def Scope \"Render\"\n",
        "    {\n",
        "        def Scope \"Products\"\n",
        "        {\n",
        "            def Scope \"Vars\"\n",
        "            {\n",
        "                def RenderVar \"beauty\" (\n",
        "                    apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "                )\n",
        "                {\n",
        "                    uniform token dataType = \"color3f\"\n",
        "                    custom token driver:parameters:aov:format = \"color3f\"\n",
        "                    string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                    uniform string sourceName = \"C.*[LO]\"\n",
        "                }\n",
        "\n",
        "                def RenderVar \"alpha\" (\n",
        "                    apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "                )\n",
        "                {\n",
        "                    uniform token dataType = \"float\"\n",
        "                    string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                    uniform string sourceName = \"ray:noholdouts;hit\"\n",
        "                }\n",
        "\n",
        "                def RenderVar \"albedo\"\n",
        "                {\n",
        "                    uniform token dataType = \"color3f\"\n",
        "                    custom token driver:parameters:aov:format = \"color3h\"\n",
        "                }\n",
        "            }\n",
        "\n",
        "            def RenderProduct \"renderproduct\"\n",
        "            {\n",
        "                rel orderedVars = [\n",
        "                    </scene/Render/Products/Vars/beauty>,\n",
        "                    </scene/Render/Products/Vars/alpha>,\n",
        "                    </scene/Render/Products/Vars/albedo>,\n",
        "                ]\n",
        "            }\n",
        "        }\n",
        "    }\n",
        "}\n",
    );

    const AFTER: &str = concat!(
        "#usda 1.0\n",
        "(\n",
        "    renderSettingsPrimPath = \"/scene/Render/rendersettings\"\n",
        ")\n",
        "\n",
        "def Scope \"scene\"\n",
        "{\n",
        "    def Scope \"Render\"\n",
        "    {\n",
        "        def Scope \"Products\"\n",
        "        {\n",
        "            def Scope \"Vars\"\n",
        "            {\n",
        "                def RenderVar \"beauty\" (\n",
        "                    apiSchemas = [\"KarmaRenderVarAPI\", \"HuskRenderVarAPI\"]\n",
        "                )\n",
        "                {\n",
        "                    uniform token dataType = \"color4f\"\n",
        "                    custom token driver:parameters:aov:format = \"color4f\"\n",
        "                    string driver:parameters:aov:karma:filter = '[\"ubox\",{}]'\n",
        "                    uniform string sourceName = \"C.*[LO]\"\n",
        "                }\n",
        "\n",
        "                def RenderVar \"albedo\"\n",
        "                {\n",
        "                    uniform token dataType = \"color3f\"\n",
        "                    custom token driver:parameters:aov:format = \"color3h\"\n",
        "                }\n",
        "            }\n",
        "\n",
        "            def RenderProduct \"renderproduct\"\n",
        "            {\n",
        "                rel orderedVars = [\n",
        "                    </scene/Render/Products/Vars/beauty>,\n",
        "                    </scene/Render/Products/Vars/albedo>,\n",
        "                ]\n",
        "            }\n",
        "        }\n",
        "    }\n",
        "}\n",
    );

    #[test]
    fn the_scaffold_shape_migrates_and_is_then_left_alone() {
        assert_eq!(changed(root_defaults_edit(BEFORE)), AFTER);
        assert_eq!(root_defaults_edit(AFTER), Edit::Unchanged);
    }

    #[test]
    fn alpha_metadata_with_a_dictionary_is_removed_whole() {
        let before = BEFORE.replace(
            "                def RenderVar \"alpha\" (\n",
            "                def RenderVar \"alpha\" (\n                    customData = {\n                        string note = \"x}\"\n                        int n = 1\n                    }\n",
        );
        assert_eq!(changed(root_defaults_edit(&before)), AFTER);
    }

    /// Brackets inside strings and comments are text, not structure.
    #[test]
    fn brackets_in_strings_and_comments_are_not_structure() {
        let before = BEFORE.replace(
            "                    uniform string sourceName = \"ray:noholdouts;hit\"\n",
            "                    uniform string sourceName = \"ray:noholdouts;hit\"\n                    string note = \"{ ( [\" # } ) ]\n",
        )
        .replace(
            "                def RenderVar \"alpha\" (\n",
            "                def RenderVar \"alpha\" (\n                    doc = \"\"\"a ) and a }\"\"\"\n",
        );
        assert_eq!(changed(root_defaults_edit(&before)), AFTER);
    }

    #[test]
    fn a_one_line_alpha_is_removed_and_its_neighbour_kept() {
        let start = BEFORE.find("                def RenderVar \"alpha\" (").unwrap();
        let end = BEFORE.find("\n\n                def RenderVar \"albedo\"").unwrap();
        let before = format!(
            "{}                def RenderVar \"alpha\" {{ uniform token dataType = \"float\" }}{}",
            &BEFORE[..start],
            &BEFORE[end..]
        );
        assert_eq!(changed(root_defaults_edit(&before)), AFTER);
    }

    #[test]
    fn beauty_dictionary_metadata_still_converts() {
        let before = BEFORE.replace(
            "                def RenderVar \"beauty\" (\n",
            "                def RenderVar \"beauty\" (\n                    customData = { string a = \"b\" }\n",
        );
        let after = AFTER.replace(
            "                def RenderVar \"beauty\" (\n",
            "                def RenderVar \"beauty\" (\n                    customData = { string a = \"b\" }\n",
        );
        assert_eq!(changed(root_defaults_edit(&before)), after);
    }

    #[test]
    fn no_beauty_named_beauty_refuses_and_keeps_the_alpha() {
        let before = BEFORE.replace("RenderVar \"beauty\"", "RenderVar \"C\"");
        let why = refused(root_defaults_edit(&before));
        assert!(why.contains("beauty"), "{why}");
    }

    #[test]
    fn an_unknown_beauty_type_refuses() {
        let before = BEFORE.replacen("dataType = \"color3f\"", "dataType = \"float3\"", 1);
        refused(root_defaults_edit(&before));
        let unauthored = BEFORE.replacen("                    uniform token dataType = \"color3f\"\n", "", 1);
        refused(root_defaults_edit(&unauthored));
    }

    #[test]
    fn a_single_alpha_target_refuses() {
        let before = BEFORE.replace(
            "                rel orderedVars = [\n                    </scene/Render/Products/Vars/beauty>,\n                    </scene/Render/Products/Vars/alpha>,\n                    </scene/Render/Products/Vars/albedo>,\n                ]\n",
            "                rel orderedVars = </scene/Render/Products/Vars/alpha>\n",
        );
        let why = refused(root_defaults_edit(&before));
        assert!(why.contains("single target"), "{why}");
        let only = BEFORE.replace(
            "                    </scene/Render/Products/Vars/beauty>,\n                    </scene/Render/Products/Vars/alpha>,\n                    </scene/Render/Products/Vars/albedo>,\n",
            "                    </scene/Render/Products/Vars/alpha>\n",
        );
        refused(root_defaults_edit(&only));
    }

    #[test]
    fn an_alpha_outside_a_vars_scope_is_matched_by_its_path() {
        // The vars sit straight under the product's parent, no Vars scope
        let before = BEFORE
            .replace("Products/Vars/", "Products/")
            .replace("            def Scope \"Vars\"\n            {\n", "            def Scope \"Flat\"\n            {\n")
            .replace("Products/beauty", "Products/Flat/beauty")
            .replace("Products/alpha", "Products/Flat/alpha")
            .replace("Products/albedo", "Products/Flat/albedo");
        let after = changed(root_defaults_edit(&before));
        assert!(!after.contains("alpha"), "{after}");
        assert!(after.contains("</scene/Render/Products/Flat/albedo>"));
    }

    #[test]
    fn a_target_that_cannot_be_matched_refuses() {
        let before = BEFORE.replace(
            "</scene/Render/Products/Vars/alpha>",
            "</Render/Products/Vars/alpha>",
        );
        refused(root_defaults_edit(&before));
    }

    #[test]
    fn inline_lists_lose_only_the_alpha() {
        let list = "                rel orderedVars = [\n                    </scene/Render/Products/Vars/beauty>,\n                    </scene/Render/Products/Vars/alpha>,\n                    </scene/Render/Products/Vars/albedo>,\n                ]\n";
        for (inline, want) in [
            (
                "[</scene/Render/Products/Vars/beauty>, </scene/Render/Products/Vars/alpha>, </scene/Render/Products/Vars/albedo>]",
                "[</scene/Render/Products/Vars/beauty>, </scene/Render/Products/Vars/albedo>]",
            ),
            (
                "[</scene/Render/Products/Vars/beauty>, </scene/Render/Products/Vars/alpha>]",
                "[</scene/Render/Products/Vars/beauty>]",
            ),
            (
                "[</scene/Render/Products/Vars/alpha>, </scene/Render/Products/Vars/beauty>]",
                "[</scene/Render/Products/Vars/beauty>]",
            ),
        ] {
            let before = BEFORE.replace(list, &format!("                rel orderedVars = {inline}\n"));
            let after = changed(root_defaults_edit(&before));
            assert!(after.contains(&format!("rel orderedVars = {want}\n")), "{after}");
        }
    }

    #[test]
    fn a_last_alpha_on_its_own_line_takes_the_comma_before_it() {
        let before = BEFORE.replace(
            "                    </scene/Render/Products/Vars/alpha>,\n                    </scene/Render/Products/Vars/albedo>,\n",
            "                    </scene/Render/Products/Vars/albedo>,\n                    </scene/Render/Products/Vars/alpha>\n",
        );
        let after = changed(root_defaults_edit(&before));
        assert!(after.contains("Vars/albedo>\n                ]"), "{after}");
    }

    #[test]
    fn an_alpha_override_refuses() {
        let before = format!("{BEFORE}\nover \"scene\"\n{{\n    over \"alpha\"\n    {{\n    }}\n}}\n");
        refused(root_defaults_edit(&before));
    }

    #[test]
    fn unbalanced_text_refuses() {
        refused(root_defaults_edit(&BEFORE[..BEFORE.len() - 2]));
    }

    #[test]
    fn a_beauty_only_file_still_goes_rgba() {
        let start = BEFORE.find("\n                def RenderVar \"alpha\"").unwrap();
        let end = BEFORE.find("\n\n                def RenderVar \"albedo\"").unwrap() + 1;
        let before = format!("{}{}", &BEFORE[..start], &BEFORE[end..])
            .replace("                    </scene/Render/Products/Vars/alpha>,\n", "");
        assert_eq!(changed(root_defaults_edit(&before)), AFTER);
    }

    #[test]
    fn a_file_without_render_vars_is_unchanged() {
        assert_eq!(root_defaults_edit("#usda 1.0\ndef Xform \"world\"\n{\n}\n"), Edit::Unchanged);
    }

    #[test]
    fn context_json_keeps_its_formatting() {
        let before = "{\n    \"outputs\": [{\n        \"uri\": \"config:/usd/root_default_prims\",\n        \"parameters\": {\n            \"aov_names\": [\"beauty\", \"alpha\", \"albedo\", \"normal\"]\n        }\n    }]\n}\n";
        let after = changed(context_json_edit(before));
        assert_eq!(after, before.replace("\"alpha\", ", ""));
        assert_eq!(context_json_edit(&after), Edit::Unchanged);

        let multi = "{\n  \"aov_names\": [\n    \"beauty\",\n    \"alpha\"\n  ]\n}\n";
        assert_eq!(
            changed(context_json_edit(multi)),
            "{\n  \"aov_names\": [\n    \"beauty\"\n  ]\n}\n"
        );
        let only = "{\"aov_names\": [\"alpha\"]}";
        assert_eq!(changed(context_json_edit(only)), "{\"aov_names\": []}");
        refused(context_json_edit("{ not json"));
    }
}
