# Shipped recipes

Network-catalog entries bundled with the TumblePipe package. TumbleTrove's
asset browser (> 0.9.1) discovers this directory through the
`ASSET_BROWSER_NETWORK_PATH` env var that `hpm.toml` prepends at launch, and
shows every entry alongside the user's personal recipe library — flagged
**read-only** (no edit/delete; users can't save into this root).

## Layout

Same layout as a personal recipe root:

```
recipes/
  <context>/            # sop, lop, obj, ...
    <kind>/             # recipe (other kinds may follow)
      <slug>/
        entry.json      # {"kind": "recipe", "meta": {...}}
        recipe.cpio     # payload — loaded via loadItemsFromFile
        thumbnail.png   # optional
```

## Authoring a shipped recipe

1. In Houdini, select the nodes and use the asset browser's **Save Recipe…**
   (saves into your personal recipe root, by default
   `<user config>/asset_browser/recipes/`).
2. Copy the saved `<context>/recipe/<slug>/` directory into this tree.
3. Commit it — the whole `recipes/` tree ships in the package archive
   (`hpm.toml [stage].include` + `.ci/_tumblepipe_build.py` INCLUDE_PATTERNS).

Keep thumbnails small (the saver already scales to 400×300) and note that
`recipe.cpio` is Houdini-version-tolerant but not guaranteed across majors —
re-save recipes that misbehave on a new Houdini major.
