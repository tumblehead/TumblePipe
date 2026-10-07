//! Property tests for the resolver core: the URI parser, "latest" discovery
//! and the four resolve flavors, over generated input rather than the
//! hand-picked fixtures in `resolve_integration.rs` and the unit tests.
//!
//! Two things make these worth more than they look:
//!
//! - The release profile sets `panic = "abort"`, so `ffi_guard`'s
//!   `catch_unwind` cannot catch anything in the plugin Houdini actually
//!   loads. A panic anywhere under `th_resolver_resolve` takes the whole DCC
//!   down with it. "Never panics, on any input" is therefore a contract, not
//!   a nicety, and it is pinned here for the parser and for `resolve_uri`.
//! - Every published layer is found again only through this crate, so the
//!   parser has to read back exactly what the writers put in the query, in
//!   any key order and in either channel spelling.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::sync::Mutex;

use proptest::prelude::*;

use th_resolver_core::env::{EXPORT_PATH_VAR, LATEST_MODE_VAR};
use th_resolver_core::uri::DEFAULT_VARIANT;
use th_resolver_core::version::find_latest;
use th_resolver_core::{resolve_uri, EntityUri};

// Env vars are process-wide and the test harness runs tests on threads;
// serialize every test that sets them.
static ENV_LOCK: Mutex<()> = Mutex::new(());

fn with_env<T>(export: &Path, latest: bool, body: impl FnOnce() -> T) -> T {
    let _lock = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    std::env::set_var(EXPORT_PATH_VAR, export);
    if latest {
        std::env::set_var(LATEST_MODE_VAR, "1");
    } else {
        std::env::remove_var(LATEST_MODE_VAR);
    }
    let out = body();
    std::env::remove_var(EXPORT_PATH_VAR);
    std::env::remove_var(LATEST_MODE_VAR);
    out
}

/// A path segment / query value the writers can produce: entity, channel and
/// department names are identifiers.
fn token() -> impl Strategy<Value = String> {
    "[A-Za-z0-9][A-Za-z0-9_-]{0,7}"
}

/// A version name the way the Python naming convention writes it.
fn version_name() -> impl Strategy<Value = String> {
    (1u32..20000).prop_map(|n| format!("v{n:04}"))
}

/// Entity path segments under a non-scene context, at least two deep.
fn entity_segments() -> impl Strategy<Value = Vec<String>> {
    (
        prop::sample::select(vec!["assets", "shots"]),
        prop::collection::vec(token(), 1..4),
    )
        .prop_map(|(context, rest)| {
            let mut segments = vec![context.to_owned()];
            segments.extend(rest);
            segments
        })
}

fn build_uri(segments: &[String], query: &[(String, String)], slash: bool) -> String {
    let sep = if slash { "/" } else { "" };
    let mut uri = format!("entity:{sep}{}", segments.join("/"));
    if !query.is_empty() {
        let pairs: Vec<String> = query.iter().map(|(k, v)| format!("{k}={v}")).collect();
        uri.push('?');
        uri.push_str(&pairs.join("&"));
    }
    uri
}

/// The query a writer would emit, as (key, value) pairs in writer order.
fn query_pairs(
    dept: &Option<String>,
    variant: &Option<String>,
    version: &Option<String>,
) -> Vec<(String, String)> {
    let mut query = Vec::new();
    if let Some(d) = dept {
        query.push(("dept".to_owned(), d.clone()));
    }
    if let Some(v) = variant {
        query.push(("variant".to_owned(), v.clone()));
    }
    if let Some(v) = version {
        query.push(("version".to_owned(), v.clone()));
    }
    query
}

fn touch_versions(dir: &Path, versions: &BTreeSet<u32>, padding: &[usize]) -> Vec<String> {
    fs::create_dir_all(dir).unwrap();
    versions
        .iter()
        .zip(padding.iter().cycle())
        .map(|(n, width)| {
            let name = format!("v{n:0width$}", width = *width);
            fs::create_dir(dir.join(&name)).unwrap();
            name
        })
        .collect()
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(256))]

    /// The C ABI hands the parser whatever string USD asked about. Any input
    /// at all must come back as Ok or Err, never as an unwind.
    #[test]
    fn parse_never_panics(raw in any::<String>()) {
        let _ = EntityUri::parse(&raw);
    }

    /// Same, for inputs that get past the scheme check and exercise the
    /// query and segment handling.
    #[test]
    fn parse_never_panics_on_entity_shaped_input(body in "[a-z/?&=_.:%-]{0,40}") {
        let _ = EntityUri::parse(&format!("entity:{body}"));
        let _ = EntityUri::parse(&format!("entity:/{body}"));
    }

    /// End to end: nothing a URI can say makes `resolve_uri` panic, with or
    /// without latest mode and whether or not versions exist on disk. The
    /// input is built from the real keys and edge-case values (empty,
    /// `latest`, `root`, `_shared`, a `scenes` context, any depth) so that
    /// most cases parse and reach every resolve flavor, rather than random
    /// text that the parser rejects before resolution starts.
    #[test]
    fn resolve_never_panics(
        context in prop::sample::select(vec!["assets", "shots", "scenes", "x"]),
        rest in prop::collection::vec("[A-Za-z0-9_.-]{0,6}", 0..6),
        query in prop::collection::vec(
            (
                prop::sample::select(vec!["dept", "variant", "channel", "version"]),
                "|latest|root|_shared|v[0-9]{1,5}|[A-Za-z0-9_.-]{1,6}",
            ),
            0..4,
        ),
        latest in any::<bool>(),
        seed_versions in prop::collection::btree_set(1u32..100, 0..3),
    ) {
        let td = tempfile::tempdir().unwrap();
        let mut segments = vec![context.to_owned()];
        segments.extend(rest);
        // Give "latest" discovery something to find under some flavors.
        // Best effort: generated segments include names the filesystem
        // refuses (`..`, trailing dots, Windows device names), and failing
        // to seed is not what this property is about.
        let mut dir = td.path().join(segments.join("/"));
        dir.push("_staged");
        dir.push(DEFAULT_VARIANT);
        if fs::create_dir_all(&dir).is_ok() {
            for n in &seed_versions {
                let _ = fs::create_dir(dir.join(format!("v{n:04}")));
            }
        }
        let query: Vec<(String, String)> =
            query.into_iter().map(|(k, v)| (k.to_owned(), v)).collect();
        with_env(td.path(), latest, || {
            let _ = resolve_uri(&build_uri(&segments, &query, true));
        });
    }

    /// What a writer puts in the URI is what the parser reads back.
    #[test]
    fn parse_reads_back_written_fields(
        segments in entity_segments(),
        dept in prop::option::of(token()),
        variant in prop::option::of(token()),
        version in prop::option::of(version_name()),
        slash in any::<bool>(),
    ) {
        let query = query_pairs(&dept, &variant, &version);
        let parsed = EntityUri::parse(&build_uri(&segments, &query, slash)).unwrap();
        prop_assert_eq!(parsed.segments, segments);
        prop_assert_eq!(parsed.department, dept);
        prop_assert_eq!(parsed.variant, variant.unwrap_or_else(|| DEFAULT_VARIANT.to_owned()));
        prop_assert_eq!(parsed.version, version);
    }

    /// Query order carries no meaning: any permutation of the same pairs
    /// parses to the same URI.
    #[test]
    fn parse_ignores_query_order(
        segments in entity_segments(),
        dept in token(),
        variant in token(),
        version in version_name(),
        order in Just(vec![0usize, 1, 2]).prop_shuffle(),
    ) {
        let query = query_pairs(&Some(dept), &Some(variant), &Some(version));
        let shuffled: Vec<_> = order.iter().map(|&i| query[i].clone()).collect();
        let a = EntityUri::parse(&build_uri(&segments, &query, true)).unwrap();
        let b = EntityUri::parse(&build_uri(&segments, &shuffled, true)).unwrap();
        prop_assert_eq!(a, b);
    }

    /// `channel` is the other spelling of `variant`: same value, same parse,
    /// alone or alongside an agreeing `variant`.
    #[test]
    fn channel_spelling_parses_like_variant(
        segments in entity_segments(),
        value in token(),
        dept in prop::option::of(token()),
    ) {
        let mut base = Vec::new();
        if let Some(d) = &dept {
            base.push(("dept".to_owned(), d.clone()));
        }
        let with = |key: &str| {
            let mut q = base.clone();
            q.push((key.to_owned(), value.clone()));
            EntityUri::parse(&build_uri(&segments, &q, true)).unwrap()
        };
        let as_variant = with("variant");
        prop_assert_eq!(&with("channel"), &as_variant);
        let mut both = base.clone();
        both.push(("variant".to_owned(), value.clone()));
        both.push(("channel".to_owned(), value.clone()));
        prop_assert_eq!(EntityUri::parse(&build_uri(&segments, &both, true)).unwrap(), as_variant);
    }

    /// Two spellings naming different channels is unresolvable, in either
    /// order.
    #[test]
    fn disagreeing_spellings_are_refused(
        segments in entity_segments(),
        a in token(),
        b in token(),
        variant_first in any::<bool>(),
    ) {
        prop_assume!(a != b);
        let mut query = vec![("variant".to_owned(), a), ("channel".to_owned(), b)];
        if !variant_first {
            query.reverse();
        }
        prop_assert!(EntityUri::parse(&build_uri(&segments, &query, true)).is_err());
    }

    /// Any key that is not one of the four written ones is refused rather
    /// than ignored, wherever it sits in the query.
    #[test]
    fn unknown_query_keys_are_refused(
        segments in entity_segments(),
        key in "[a-z]{1,8}",
        value in token(),
        dept in token(),
        first in any::<bool>(),
    ) {
        prop_assume!(!["dept", "variant", "channel", "version"].contains(&key.as_str()));
        let mut query = vec![("dept".to_owned(), dept), (key, value)];
        if first {
            query.reverse();
        }
        prop_assert!(EntityUri::parse(&build_uri(&segments, &query, true)).is_err());
    }

    /// "latest" is the newest version by number, not by spelling: zero
    /// padding varies, and `v10000` outranks `v9999`.
    #[test]
    fn find_latest_is_the_numeric_maximum(
        versions in prop::collection::btree_set(1u32..200_000, 1..12),
        padding in prop::collection::vec(1usize..7, 1..4),
    ) {
        let td = tempfile::tempdir().unwrap();
        let names = touch_versions(td.path(), &versions, &padding);
        let max = *versions.iter().max().unwrap();
        let expected = names
            .iter()
            .find(|name| name[1..].parse::<u32>().unwrap() == max)
            .unwrap();
        prop_assert_eq!(find_latest(td.path()), Some(expected.clone()));
    }

    /// Entries that are not version directories never win: files named like
    /// versions, and directories that are not `v` + digits.
    #[test]
    fn find_latest_ignores_non_version_entries(
        versions in prop::collection::btree_set(1u32..1000, 1..6),
        decoys in prop::collection::vec("[a-uw-z][a-z0-9]{0,5}|v[0-9]+[a-z]", 0..4),
    ) {
        let td = tempfile::tempdir().unwrap();
        touch_versions(td.path(), &versions, &[4]);
        let max = *versions.iter().max().unwrap();
        // A bigger number, but as a file it must not count.
        fs::write(td.path().join(format!("v{:04}", max + 1)), b"").unwrap();
        for decoy in &decoys {
            let _ = fs::create_dir(td.path().join(decoy));
        }
        let expected = format!("v{max:04}");
        prop_assert_eq!(find_latest(td.path()), Some(expected));
    }

    /// A department URI with an explicit version resolves inside the export
    /// root to `<entity>/<channel>/<dept>/<version>/` and a file named for
    /// exactly those parts, whether or not anything exists on disk.
    #[test]
    fn department_resolves_to_its_own_layer(
        segments in entity_segments(),
        variant in token(),
        dept in token(),
        version in version_name(),
    ) {
        prop_assume!(dept != "root");
        let td = tempfile::tempdir().unwrap();
        let query = query_pairs(&Some(dept.clone()), &Some(variant.clone()), &Some(version.clone()));
        let resolved = with_env(td.path(), false, || {
            resolve_uri(&build_uri(&segments, &query, true))
        }).unwrap();
        let entity = segments.join("_");
        let expected_tail = format!(
            "{}/{variant}/{dept}/{version}/{entity}_{variant}_{dept}_{version}.usd",
            segments.join("/"),
        );
        let base = td.path().to_string_lossy().replace('\\', "/");
        prop_assert!(resolved.starts_with(&base), "{} outside {}", resolved, base);
        prop_assert!(resolved.ends_with(&expected_tail), "{} !~ {}", resolved, expected_tail);
    }

    /// Latest mode overrides whatever version the URI pins, for every
    /// flavor: the newest version on disk is what resolves.
    #[test]
    fn latest_mode_picks_the_newest_on_disk(
        segments in entity_segments(),
        dept in prop::option::of(token()),
        versions in prop::collection::btree_set(1u32..20000, 1..6),
        pinned in version_name(),
    ) {
        let td = tempfile::tempdir().unwrap();
        let mut dir = td.path().to_path_buf();
        for seg in &segments {
            dir.push(seg);
        }
        match dept.as_deref() {
            Some("root") => dir.push("_root"),
            Some(d) => {
                dir.push(DEFAULT_VARIANT);
                dir.push(d);
            }
            None => {
                dir.push("_staged");
                dir.push(DEFAULT_VARIANT);
            }
        }
        touch_versions(&dir, &versions, &[4]);
        let newest = format!("v{:04}", versions.iter().max().unwrap());
        let query = query_pairs(&dept, &None, &Some(pinned));
        let resolved = with_env(td.path(), true, || {
            resolve_uri(&build_uri(&segments, &query, true))
        }).unwrap();
        let parent = Path::new(&resolved).parent().unwrap().file_name().unwrap();
        prop_assert_eq!(parent.to_string_lossy(), newest.as_str());
    }
}
