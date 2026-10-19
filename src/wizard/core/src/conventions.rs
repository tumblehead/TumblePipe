//! v11 — a project's convention modules become data.
//!
//! Until v11 every project carried three Python modules in `_config/` that
//! `tumblepipe.api.Client` imported and executed at construction:
//!
//! - `naming_convention.py`, which only ever had one implementation (the
//!   package hardcodes `vNNNN` version names elsewhere anyway, and so does
//!   the resolver);
//! - `config_convention.py`, which after v1 is a shim returning the
//!   package's `JsonConfigStore`;
//! - `storage_convention.py`, which maps each URI purpose to a directory.
//!
//! Only the last carries anything that legitimately differs per project: the
//! *paths*. Several live projects keep their edit sources on another drive or
//! their scratch in a WSL-era home, and a step that replaced the file would
//! silently repoint where a show's work resolves. So v11 reads those paths out
//! of the module and writes them as `_config/storage.json`, a purpose ->
//! path-template table, and the package resolves URIs from that table.
//!
//! The module is *read*, never executed: this crate has no Python, and running
//! project code to migrate it would be the very thing this step retires. The
//! reader understands every shape the scaffold has shipped and every shape the
//! live projects carry (literal and WSL `/mnt/...` roots, `Path.home()`,
//! locals such as `_primary_path`, f-strings on `project_name`), and refuses
//! anything else with the line it could not read, rather than guessing.
//!
//! It also has to read the module as it is *before* v6-v8 ran: preflight
//! checks every pending step against the files as they are, so a project
//! coming from v5 is checked while it still has its `kits` lines, its
//! `tumblehead.*` imports and its project-drive temp. Those forms are
//! accepted, and each reads to what its step turns it into.

use serde_json::{Map, Value};

/// Where a project's storage table lives, beside `version.json`.
pub const STORAGE_FILE: &str = "storage.json";

/// The purposes `storage.json` maps, in the order the file lists them.
///
/// `project:`, `pipeline:` and `config:` are not among them: they are the
/// directories TumblePipe is launched with (`TH_PROJECT_PATH`,
/// `TH_PIPELINE_PATH`, `TH_CONFIG_PATH`), so no table can move them.
pub const PURPOSES: [&str; 11] = [
    "assets", "shots", "groups", "export", "cache", "render", "turntable", "temp", "proxy",
    "preset", "edit",
];

/// The placeholders a path template may use, each filled in by the package
/// when it loads the table. Mirrored by `tumblepipe.storage.VARIABLES`.
pub const VARIABLES: [&str; 7] = [
    "project_path",
    "project_parent",
    "project_name",
    "pipeline_path",
    "config_path",
    "home",
    "local_temp",
];

/// The purposes the launch environment fixes, with the convention attribute
/// that held each and the only value that attribute may have had.
const FIXED: [(&str, &str, &str); 3] = [
    ("project", "project_path", "{project_path}"),
    ("pipeline", "pipeline_path", "{pipeline_path}"),
    ("config", "config_path", "{config_path}"),
];

/// Purposes a convention may still route that nothing resolves any more.
/// `kits` is v6's; a project coming from before v6 still has it.
const RETIRED: [&str; 1] = ["kits"];

/// What `tumblepipe.storage.default_temp_path()` resolved to (v8 pointed
/// `temp` at it), spelled as a template.
const LOCAL_SCRATCH: &str = "{local_temp}/th_temp/{project_name}";

/// Strip CR so the reader sees one newline style.
fn normalize(text: &str) -> String {
    text.replace("\r\n", "\n")
}

/// `line` without a trailing `# comment`, honouring quotes.
fn strip_comment(line: &str) -> &str {
    let mut quote: Option<char> = None;
    for (index, c) in line.char_indices() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == '\'' || c == '"' => quote = Some(c),
            None if c == '#' => return &line[..index],
            None => {}
        }
    }
    line
}

fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Split `expr` on the `/` operators outside quotes and parentheses.
fn split_path_operators(expr: &str) -> Result<Vec<String>, String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut depth = 0usize;
    for c in expr.chars() {
        match quote {
            Some(q) => {
                if c == q {
                    quote = None;
                }
                current.push(c);
            }
            None => match c {
                '\'' | '"' => {
                    quote = Some(c);
                    current.push(c);
                }
                '(' => {
                    depth += 1;
                    current.push(c);
                }
                ')' => {
                    depth = depth
                        .checked_sub(1)
                        .ok_or_else(|| format!("unbalanced `)` in `{expr}`"))?;
                    current.push(c);
                }
                '/' if depth == 0 => {
                    parts.push(current.trim().to_string());
                    current.clear();
                }
                _ => current.push(c),
            },
        }
    }
    if quote.is_some() || depth != 0 {
        return Err(format!("unterminated string or call in `{expr}`"));
    }
    parts.push(current.trim().to_string());
    if parts.iter().any(|p| p.is_empty()) {
        return Err(format!("`{expr}` has an empty path component"));
    }
    Ok(parts)
}

/// The text of a one-line string literal: `'x'`, `"x"`, and with `f` an
/// f-string, whose only placeholder may be `{project_name}`.
fn string_literal(term: &str) -> Option<Result<String, String>> {
    let (is_f, body) = match term.strip_prefix('f') {
        Some(rest) => (true, rest),
        None => (false, term),
    };
    let quote = body.chars().next()?;
    if quote != '\'' && quote != '"' {
        return None;
    }
    if body.len() < 2 || !body.ends_with(quote) {
        return None;
    }
    let inner = &body[1..body.len() - 1];
    if inner.contains(quote) {
        return None;
    }
    if inner.contains('\\') {
        return Some(Err(format!(
            "`{term}` contains a backslash escape; write the path with forward slashes"
        )));
    }
    let text = if is_f {
        inner.replace("{project_name}", "\u{0}")
    } else {
        inner.to_string()
    };
    if text.contains('{') || text.contains('}') {
        return Some(Err(format!(
            "`{term}` uses a placeholder other than {{project_name}}"
        )));
    }
    Some(Ok(text.replace('\u{0}', "{project_name}")))
}

/// What a value names once the module's locals are substituted.
struct Scope {
    /// `project_name` and the `_underscore` locals.
    locals: Vec<(String, String)>,
    /// `self.<name>` attributes.
    attrs: Vec<(String, String)>,
}

fn lookup<'a>(table: &'a [(String, String)], name: &str) -> Option<&'a str> {
    table
        .iter()
        .rev()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.as_str())
}

/// The parent of a template, as `Path.parent` would give it.
fn parent_of(template: &str) -> Result<String, String> {
    if template == "{project_path}" {
        return Ok("{project_parent}".to_string());
    }
    match template.rsplit_once('/') {
        Some((head, _)) if !head.is_empty() => Ok(head.to_string()),
        _ => Err(format!("cannot take the parent of `{template}`")),
    }
}

fn eval_term(term: &str, scope: &Scope) -> Result<String, String> {
    match term {
        "get_project_name()" => return Ok("{project_name}".to_string()),
        "get_project_path()" => return Ok("{project_path}".to_string()),
        "get_pipeline_path()" => return Ok("{pipeline_path}".to_string()),
        "get_config_path()" => return Ok("{config_path}".to_string()),
        "default_temp_path()" => return Ok(LOCAL_SCRATCH.to_string()),
        "Path.home()" => return Ok("{home}".to_string()),
        _ => {}
    }
    if let Some(literal) = string_literal(term) {
        return literal;
    }
    if let Some(inner) = term.strip_prefix("Path(").and_then(|t| t.strip_suffix(')')) {
        return match string_literal(inner.trim()) {
            Some(literal) => literal,
            None => Err(format!("`{term}` is not a Path of a string literal")),
        };
    }
    if let Some(attr) = term.strip_prefix("self.") {
        let (name, parent) = match attr.strip_suffix(".parent") {
            Some(name) => (name, true),
            None => (attr, false),
        };
        let value = lookup(&scope.attrs, name)
            .ok_or_else(|| format!("`self.{name}` is used before it is assigned"))?;
        return if parent {
            parent_of(value)
        } else {
            Ok(value.to_string())
        };
    }
    if is_identifier(term) {
        return lookup(&scope.locals, term)
            .map(str::to_string)
            .ok_or_else(|| format!("`{term}` is not a name this reader knows"));
    }
    Err(format!("`{term}` is not a path expression this reader knows"))
}

/// Evaluate a `/`-joined path expression into a template.
fn eval_expr(expr: &str, scope: &Scope) -> Result<String, String> {
    let mut terms = split_path_operators(expr)?.into_iter();
    let first = terms.next().expect("split always yields a term");
    let mut path = eval_term(&first, scope)?;
    for term in terms {
        let part = eval_term(&term, scope)?;
        // `Path('a') / '/b'` is `/b` in Python: a later absolute part
        // replaces everything before it. No real convention does that, and
        // honouring it silently would hide whatever was meant.
        let absolute = part.starts_with('/')
            || part.as_bytes().get(1) == Some(&b':')
            || part.starts_with('{') && !part.starts_with("{project_name}");
        if absolute {
            return Err(format!(
                "`{expr}` joins an absolute path (`{term}`) onto another"
            ));
        }
        path = format!("{}/{}", path.trim_end_matches('/'), part);
    }
    Ok(path)
}

/// Lines a stock storage convention carries that say nothing about paths.
fn is_scaffolding(line: &str) -> bool {
    const EXACT: [&str; 13] = [
        "def __init__(self):",
        "def resolve(self, uri: Uri):",
        "def resolve(self, path):",
        "def create():",
        "normalized = self._normalize_input(uri)",
        "if normalized is None:",
        "return None",
        "purpose, parts = normalized",
        "match purpose:",
        "if not self.is_valid_path(path): return None",
        "purpose, parts = self.parse_path(path)",
        "case _: return None",
        "case _: assert False, f'Unknown path purpose \"{purpose}\"'",
    ];
    if EXACT.contains(&line) {
        return true;
    }
    if let Some(rest) = line.strip_prefix("class ") {
        return rest.ends_with("(StorageConvention):")
            || rest.ends_with("(storage.StorageConvention):");
    }
    if let Some(call) = line.strip_prefix("return ") {
        return call
            .strip_suffix("()")
            .is_some_and(is_identifier);
    }
    false
}

/// `case '<purpose>': return self.<attr> / Path(*parts) [if parts else self.<attr>]`
/// as `(purpose, attr)`.
fn case_line(line: &str) -> Option<Result<(String, String), String>> {
    let rest = line.strip_prefix("case ")?;
    let quote = rest.chars().next()?;
    if quote != '\'' && quote != '"' {
        return None;
    }
    let rest = &rest[1..];
    let end = rest.find(quote)?;
    let purpose = rest[..end].to_string();
    let rest = rest[end + 1..].trim_start();
    let parsed = (|| {
        let body = rest.strip_prefix(':')?.trim_start().strip_prefix("return self.")?;
        let attr_end = body
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(body.len());
        let attr = &body[..attr_end];
        let tail = body[attr_end..].trim();
        let joined = "/ Path(*parts)";
        let guarded = format!("/ Path(*parts) if parts else self.{attr}");
        if tail == joined || tail == guarded {
            Some(attr.to_string())
        } else {
            None
        }
    })();
    Some(match parsed {
        Some(attr) => Ok((purpose, attr)),
        None => Err(format!("`{line}` is not a plain purpose -> path case")),
    })
}

/// The table a `storage_convention.py` amounts to, as `purpose -> template`
/// in [`PURPOSES`] order.
///
/// A purpose the module never routes resolved to nothing, so the project
/// had no working location for it; it gets the scaffold's (`scaffold`, the
/// packaged `storage.json`). Every other value is the module's own.
pub fn storage_table(text: &str, scaffold: &Map<String, Value>) -> Result<Map<String, Value>, String> {
    let text = normalize(text);
    let mut scope = Scope {
        locals: Vec::new(),
        attrs: Vec::new(),
    };
    let mut cases: Vec<(String, String)> = Vec::new();
    let mut in_import = false;
    for (index, raw) in text.lines().enumerate() {
        let number = index + 1;
        let line = strip_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        let refuse = |why: String| format!("line {number} (`{}`): {why}", raw.trim());
        if in_import {
            if line == ")" {
                in_import = false;
            } else if !line
                .split(',')
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .all(is_identifier)
            {
                return Err(refuse("not an imported name".to_string()));
            }
            continue;
        }
        if line.starts_with("from ") || line.starts_with("import ") {
            in_import = line.ends_with('(');
            continue;
        }
        if is_scaffolding(line) {
            continue;
        }
        if let Some(case) = case_line(line) {
            let (purpose, attr) = case.map_err(refuse)?;
            cases.push((purpose, attr));
            continue;
        }
        let Some((lhs, rhs)) = line.split_once(" = ") else {
            return Err(refuse(
                "not part of a stock storage convention, so this step cannot tell what it \
                 does"
                    .to_string(),
            ));
        };
        let value = eval_expr(rhs.trim(), &scope).map_err(refuse)?;
        let lhs = lhs.trim();
        if let Some(attr) = lhs.strip_prefix("self.") {
            if !is_identifier(attr) {
                return Err(refuse(format!("`{lhs}` is not an attribute")));
            }
            // The launch environment fixes these; a convention that pointed
            // them anywhere else cannot be carried into a table without them.
            if let Some((purpose, _, fixed)) = FIXED.iter().find(|(_, a, _)| *a == attr) {
                if value != *fixed {
                    return Err(refuse(format!(
                        "{purpose}:/ is always the launch environment's {fixed} and can no \
                         longer be moved"
                    )));
                }
            }
            scope.attrs.push((attr.to_string(), value));
        } else if lhs == "project_name" || (lhs.starts_with('_') && is_identifier(lhs)) {
            scope.locals.push((lhs.to_string(), value));
        } else {
            return Err(refuse(format!("`{lhs}` is not a name this reader knows")));
        }
    }

    for (purpose, attr, fixed) in FIXED {
        if let Some((_, routed)) = cases.iter().find(|(p, _)| p == purpose) {
            if routed != attr {
                return Err(format!(
                    "{purpose}:/ is routed to `self.{routed}`, but it is always the launch \
                     environment's {fixed} and can no longer be moved"
                ));
            }
        }
    }

    let mut table = Map::new();
    for purpose in PURPOSES {
        let template = match cases.iter().rev().find(|(p, _)| p == purpose) {
            Some((_, attr)) => lookup(&scope.attrs, attr)
                .ok_or_else(|| format!("{purpose}:/ is routed to `self.{attr}`, which is never assigned"))?
                .to_string(),
            None => scaffold
                .get(purpose)
                .and_then(Value::as_str)
                .ok_or_else(|| format!("the packaged {STORAGE_FILE} has no `{purpose}`"))?
                .to_string(),
        };
        table.insert(purpose.to_string(), Value::String(template));
    }
    for (purpose, _) in &cases {
        let known = PURPOSES.contains(&purpose.as_str())
            || FIXED.iter().any(|(fixed, _, _)| fixed == purpose)
            || RETIRED.contains(&purpose.as_str());
        if !known {
            return Err(format!(
                "it routes a `{purpose}:` purpose of its own; TumblePipe resolves only {} \
                 (plus project, pipeline and config), so nothing could use it",
                PURPOSES.join(", ")
            ));
        }
    }
    validate_table(&table)?;
    Ok(table)
}

/// Check a storage table: exactly [`PURPOSES`], each a string whose
/// placeholders are all [`VARIABLES`].
pub fn validate_table(table: &Map<String, Value>) -> Result<(), String> {
    for purpose in PURPOSES {
        if !table.contains_key(purpose) {
            return Err(format!("no `{purpose}` entry"));
        }
    }
    for (purpose, template) in table {
        if !PURPOSES.contains(&purpose.as_str()) {
            return Err(format!("`{purpose}` is not a storage purpose"));
        }
        let template = template
            .as_str()
            .ok_or_else(|| format!("`{purpose}` is not a string"))?;
        let mut rest = template;
        while let Some(open) = rest.find('{') {
            let after = &rest[open + 1..];
            let close = after
                .find('}')
                .ok_or_else(|| format!("`{purpose}` has an unclosed `{{`"))?;
            let name = &after[..close];
            if !VARIABLES.contains(&name) {
                return Err(format!("`{purpose}` uses an unknown placeholder `{{{name}}}`"));
            }
            rest = &after[close + 1..];
        }
        if rest.contains('}') {
            return Err(format!("`{purpose}` has a stray `}}`"));
        }
    }
    Ok(())
}

/// A module's code, for comparing against a stock text: no blank lines, no
/// comment-only lines, no trailing whitespace, and the pre-rename
/// `tumblehead.` package prefix read as `tumblepipe.` (v7's rewrite).
fn code_lines(text: &str) -> Vec<String> {
    normalize(text)
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.trim().is_empty() && !line.trim_start().starts_with('#'))
        .map(|line| {
            line.replace("from tumblehead.", "from tumblepipe.")
                .replace("import tumblehead.", "import tumblepipe.")
        })
        .collect()
}

/// Every `naming_convention.py` the scaffold has shipped, as [`code_lines`]:
/// the original, the v10000 fix (36ba5f5) and the ASCII-entity fix (0d552a9).
/// The package's `tumblepipe.naming` is the newest of them, and each older
/// one accepts a subset of what it does, so dropping any of them loses
/// nothing.
const STOCK_NAMING: [&str; 3] = [
    concat!(
        "from tumblepipe.naming import NamingConvention\n",
        "class ProjectNamingConvention(NamingConvention):\n",
        "    def is_valid_entity_name(self, entity_name: str) -> bool:\n",
        "        return entity_name.isalnum()\n",
        "    def is_valid_version_name(self, version_name: str) -> bool:\n",
        "        if not len(version_name) == 5: return False\n",
        "        if not version_name.startswith('v'): return False\n",
        "        if not version_name[1:].isdigit(): return False\n",
        "        return True\n",
        "    def get_version_name(self, version_code: int) -> str:\n",
        "        return f'v{str(version_code).zfill(4)}'\n",
        "    def get_version_code(self, version_name: str) -> int:\n",
        "        return int(version_name[1:])\n",
        "    def get_instance_name(self, asset_name: str, instance_index: int) -> str:\n",
        "        if instance_index == 0: return asset_name\n",
        "        return f'{asset_name}{instance_index}'\n",
        "def create():\n",
        "    return ProjectNamingConvention()",
    ),
    concat!(
        "from tumblepipe.naming import NamingConvention\n",
        "class ProjectNamingConvention(NamingConvention):\n",
        "    def is_valid_entity_name(self, entity_name: str) -> bool:\n",
        "        return entity_name.isalnum()\n",
        "    def is_valid_version_name(self, version_name: str) -> bool:\n",
        "        if len(version_name) < 5: return False\n",
        "        if not version_name.startswith('v'): return False\n",
        "        digits = version_name[1:]\n",
        "        if not (digits.isascii() and digits.isdigit()): return False\n",
        "        return True\n",
        "    def get_version_name(self, version_code: int) -> str:\n",
        "        return f'v{str(version_code).zfill(4)}'\n",
        "    def get_version_code(self, version_name: str) -> int:\n",
        "        return int(version_name[1:])\n",
        "    def get_instance_name(self, asset_name: str, instance_index: int) -> str:\n",
        "        if instance_index == 0: return asset_name\n",
        "        return f'{asset_name}{instance_index}'\n",
        "def create():\n",
        "    return ProjectNamingConvention()",
    ),
    concat!(
        "from tumblepipe.naming import NamingConvention\n",
        "class ProjectNamingConvention(NamingConvention):\n",
        "    def is_valid_entity_name(self, entity_name: str) -> bool:\n",
        "        return entity_name.isascii() and entity_name.isalnum()\n",
        "    def is_valid_version_name(self, version_name: str) -> bool:\n",
        "        if len(version_name) < 5: return False\n",
        "        if not version_name.startswith('v'): return False\n",
        "        digits = version_name[1:]\n",
        "        if not (digits.isascii() and digits.isdigit()): return False\n",
        "        return True\n",
        "    def get_version_name(self, version_code: int) -> str:\n",
        "        return f'v{str(version_code).zfill(4)}'\n",
        "    def get_version_code(self, version_name: str) -> int:\n",
        "        return int(version_name[1:])\n",
        "    def get_instance_name(self, asset_name: str, instance_index: int) -> str:\n",
        "        if instance_index == 0: return asset_name\n",
        "        return f'{asset_name}{instance_index}'\n",
        "def create():\n",
        "    return ProjectNamingConvention()",
    ),
];

/// Whether `text` is a `naming_convention.py` the scaffold shipped.
pub fn naming_is_stock(text: &str) -> bool {
    let code = code_lines(text).join("\n");
    STOCK_NAMING.contains(&code.as_str())
}

/// The code of v1's config shim, docstring aside.
const SHIM_CODE: &str = concat!(
    "from tumblepipe.config.store import JsonConfigStore\n",
    "def create() -> JsonConfigStore:\n",
    "    return JsonConfigStore()",
);

/// Whether `text` is a `config_convention.py` the package can stand in for:
/// v1's shim (whatever its docstring says), or the stock engine v1 turns
/// into the shim — v1's own preflight refuses anything else before this
/// step is reached.
pub fn config_is_stock(text: &str) -> bool {
    if text.contains("class ProjectConfigConvention") {
        return true;
    }
    let mut lines = code_lines(text).into_iter().peekable();
    // A leading module docstring.
    if let Some(first) = lines.peek() {
        let opener = first.trim_start();
        if let Some(rest) = opener.strip_prefix("\"\"\"") {
            let closes_here = rest.contains("\"\"\"");
            lines.next();
            if !closes_here {
                for line in lines.by_ref() {
                    if line.contains("\"\"\"") {
                        break;
                    }
                }
            }
        }
    }
    lines.collect::<Vec<_>>().join("\n") == SHIM_CODE
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scaffold() -> Map<String, Value> {
        let text = include_str!("../../../../scripts/project_template/_config/storage.json");
        match serde_json::from_str::<Value>(text).unwrap() {
            Value::Object(map) => map,
            _ => panic!("storage.json is not an object"),
        }
    }

    fn table(text: &str) -> Map<String, Value> {
        storage_table(text, &scaffold()).unwrap_or_else(|why| panic!("{why}"))
    }

    /// The convention every scaffold before v11 shipped (after v8).
    const STOCK: &str = include_str!("../tests/fixtures/storage_convention_v8.py");

    #[test]
    fn the_packaged_table_is_valid_and_complete() {
        let scaffold = scaffold();
        validate_table(&scaffold).unwrap();
        let keys: Vec<&str> = scaffold.keys().map(String::as_str).collect();
        assert_eq!(keys, PURPOSES, "the scaffold lists the purposes in PURPOSES order");
    }

    /// A project that never touched its convention migrates to exactly the
    /// table a new project is created with.
    #[test]
    fn the_stock_convention_reads_as_the_packaged_table() {
        assert_eq!(table(STOCK), scaffold());
    }

    /// The same module before v6/v7/v8: `kits`, `tumblehead.*` imports and a
    /// project-drive temp. Preflight reads it in this state.
    #[test]
    fn the_first_scaffold_reads_too() {
        let first = include_str!("../tests/fixtures/storage_convention_initial.py");
        let read = table(first);
        assert_eq!(read["temp"], "{project_parent}/{project_name}_temp");
        let mut rest = read.clone();
        rest.remove("temp");
        let mut expected = scaffold();
        expected.remove("temp");
        assert_eq!(rest, expected);
    }

    #[test]
    fn wsl_roots_and_locals_survive() {
        let text = STOCK
            .replace(
                "        project_name = get_project_name()\n",
                concat!(
                    "        project_name = get_project_name()\n",
                    "        _primary_path = Path('/mnt/c')\n",
                    "        _secondary_path = Path('/mnt/e')\n",
                    "        _home = _primary_path / 'users' / 'tumblehead'\n",
                ),
            )
            .replace(
                "self.temp_path = default_temp_path()",
                "self.temp_path = _home / 'th_temp' / project_name",
            )
            .replace(
                "self.proxy_path = self.project_path.parent / f'{project_name}_proxy'",
                "self.proxy_path = Path.home() / 'th_proxy' / project_name",
            )
            .replace(
                "self.edit_path = self.project_path / 'edit'",
                "self.edit_path = Path('/mnt/z/') / project_name  # cut room",
            );
        let read = table(&text);
        assert_eq!(read["temp"], "/mnt/c/users/tumblehead/th_temp/{project_name}");
        assert_eq!(read["proxy"], "{home}/th_proxy/{project_name}");
        assert_eq!(read["edit"], "/mnt/z/{project_name}");
        assert_eq!(read["assets"], "{project_path}/assets");
    }

    #[test]
    fn crlf_reads_the_same() {
        assert_eq!(table(&STOCK.replace('\n', "\r\n")), table(STOCK));
    }

    #[test]
    fn a_purpose_it_never_routes_gets_the_scaffold_location() {
        let text: String = STOCK
            .lines()
            .filter(|line| !line.contains("'groups'"))
            .map(|line| format!("{line}\n"))
            .collect();
        assert_eq!(table(&text)["groups"], "{project_path}/groups");
    }

    #[test]
    fn a_rerouted_purpose_is_honoured() {
        let text = STOCK.replace(
            "case 'render': return self.render_path / Path(*parts) if parts else self.render_path",
            "case 'render': return self.cache_path / Path(*parts) if parts else self.cache_path",
        );
        assert_eq!(table(&text)["render"], "{project_parent}/{project_name}_cache");
    }

    fn refused(text: &str) -> String {
        match storage_table(text, &scaffold()) {
            Ok(table) => panic!("should have been refused, read {table:?}"),
            Err(why) => why,
        }
    }

    #[test]
    fn logic_it_cannot_read_is_refused_with_the_line() {
        let text = STOCK.replace(
            "        self.edit_path = self.project_path / 'edit'\n",
            concat!(
                "        self.edit_path = self.project_path / 'edit'\n",
                "        if os.environ.get('STUDIO'):\n",
                "            self.edit_path = Path('/studio/edit')\n",
            ),
        );
        let why = refused(&text);
        assert!(why.contains("if os.environ.get('STUDIO'):"), "{why}");
    }

    #[test]
    fn an_unknown_call_is_refused() {
        let why = refused(&STOCK.replace(
            "self.cache_path = self.project_path.parent / f'{project_name}_cache'",
            "self.cache_path = studio_cache_root(project_name)",
        ));
        assert!(why.contains("studio_cache_root"), "{why}");
    }

    #[test]
    fn a_moved_project_root_is_refused() {
        let why = refused(&STOCK.replace(
            "self.project_path = get_project_path()",
            "self.project_path = Path('/elsewhere')",
        ));
        assert!(why.contains("project:/"), "{why}");
    }

    #[test]
    fn a_custom_purpose_is_refused() {
        let why = refused(&STOCK.replace(
            "            case _: return None",
            concat!(
                "            case 'library': return self.assets_path / Path(*parts) if parts else self.assets_path\n",
                "            case _: return None",
            ),
        ));
        assert!(why.contains("library"), "{why}");
    }

    #[test]
    fn placeholders_other_than_project_name_are_refused() {
        let why = refused(&STOCK.replace(
            "f'{project_name}_cache'",
            "f'{project_name}_{user}_cache'",
        ));
        assert!(why.contains("placeholder"), "{why}");
    }

    #[test]
    fn every_stock_naming_convention_is_recognised() {
        let current = include_str!("../tests/fixtures/naming_convention_current.py");
        assert!(naming_is_stock(current));
        assert!(naming_is_stock(&current.replace('\n', "\r\n")));
        assert!(naming_is_stock(
            &current.replace("from tumblepipe.naming", "from tumblehead.naming")
        ));
        for stock in STOCK_NAMING {
            assert!(naming_is_stock(stock));
        }
        assert!(!naming_is_stock(&current.replace("zfill(4)", "zfill(3)")));
        assert!(!naming_is_stock("def create():\n    return Mine()\n"));
    }

    #[test]
    fn the_config_shim_and_the_stock_engine_are_recognised() {
        let shim = include_str!("../tests/fixtures/config_convention_shim.py");
        assert!(config_is_stock(shim));
        assert!(config_is_stock(&shim.replace('\n', "\r\n")));
        assert!(config_is_stock("class ProjectConfigConvention:\n    pass\n"));
        let subclassed = shim.replace(
            "    return JsonConfigStore()",
            "    return MyStore()\n\nclass MyStore(JsonConfigStore):\n    pass",
        );
        assert!(!config_is_stock(&subclassed));
    }
}
