# HDA sources

Each directory here is an **expanded** Houdini Digital Asset (the
`hotl -t` layout): a `DialogScript`, `Contents.dir/Contents.mime`
(a MIME archive of the HDA's internal node data), and per-definition
metadata files. The release build compiles them into installable
`.hda` libraries (compile-hdas); nothing in a packaged build reads
these directories directly, so **an edit here only takes effect after
a build**.

Editing rules learned the hard way:

- **Never put whitespace in a `.chn` channel expression.** The channel
  segment parser inside `Contents.mime` tokenizes on whitespace, so
  `expr = ch(\"../a\") * (ch(\"../b\") == 0)` fails to parse
  ("Missing token: =") — every scene containing the node then errors
  on load and the parameter silently falls back to its default value.
  This shipped broken in v1.18.0/1.18.1 (the import layerbreak
  disabled itself). Write the arithmetically equivalent spaceless
  form instead: `expr = ch(\"../a\")*(1-ch(\"../b\"))`.
- **Never delete `*.orig` files** (e.g. `ViewerStateName.orig`) — they
  are load-bearing for compile-hdas.
- DialogScript menu/toggle parms follow the existing idioms in each
  file (`hou.phm().execute()` callbacks, python `menu {}` blocks);
  copy a neighbouring parm rather than inventing a new shape.
- **Menu scripts are read-only.** Houdini evaluates them on every
  parameter-pane redraw, so a `parm.set()` inside a `menu {}` block
  dirties the node mid-draw and can re-trigger evaluation (import_assets
  2.0 did this per multiparm row). Menus list; explicit actions
  (callbacks, `execute()`) write. They must also stay cheap — a menu
  script that enumerates config wants `list_entity_uris`, never
  `list_entities` (which resolves properties per entity).
- **No absolute node paths in `opmenu` references.** DialogScript
  `opmenu -l -a <path> <parm>` resolves relative to the HDA instance;
  an absolute path like `/stage/import_assets1/dive/layout_assets` only
  works for an instance with that exact name and location — every other
  instance's menu silently errors. Use the instance-relative form
  (`dive/layout_assets`).
- CI's `validate-hdas` step does NOT parse channel expressions — a
  syntactically broken `.chn` sails through the build. The only real
  gate is loading a scene that contains the node in a live Houdini.
- **`EditableNodes` must have no trailing newline.** The section is a
  bare list of node paths (`lopnet/import_asset`). A trailing `\n` makes
  Houdini fail to match the path, and the node is silently *not* editable
  inside the locked HDA — no error, it just quietly stops working. This
  matters whenever an HDA embeds a pipeline node that rewrites its own
  contents at runtime (`th::import_shot`, `th::import_layer`): their
  `execute()` builds a layer stack and spare parms, which a locked HDA
  blocks. Check with `node.isEditableInsideLockedHDA()`, and compare
  against a known-good HDA rather than trusting that the section exists.
- **Rebuilding an HDA from Python?** `createDigitalAsset()` will not give
  you a shippable asset on its own — it drops things silently:
  - it promotes the parms onto the *node* but leaves the **definition's**
    interface empty. Call `definition.setParmTemplateGroup(ptg)`
    explicitly or the HDA has no parms at all.
  - it emits **no `TypePropertiesOptions`** section, so the asset falls
    back to Houdini's defaults — including unlocked contents, which makes
    every scene containing the node save its whole internal network into
    the `.hip`. Set them via `definition.setOptions()`.
  Verify by instantiating the *compiled* `.hda` in a fresh hython and
  driving it, not by reading the source you just wrote.
- **A stale `otls/<name>.hda` shadows your edit.** The compiled `.hda`
  files are gitignored build artifacts that sit *next to* the source
  directories, and Houdini scans them. Until you re-run compile-hdas
  (`hython .ci/compile_hdas.py`) a dev-override launch — and even
  `hou.hda.installFile()` inside a verification script — keeps loading
  the **old** definition, so your new parm appears to not exist. In a
  hython harness, force yours current with
  `defn.setIsPreferred(True)`.
- **An entity-addressing parm defaults to `from_context`.** Any HDA that
  binds to a pipeline entity uses one idiom: an invisible `entity` (or
  `shot`/`asset`) string parm defaulting to the literal `from_context`,
  beside an `entity_select` button and a visible `entity_label`. The
  sentinel resolves against the workfile the node lives in, every
  evaluation. Two things that look harmless but are not:
  - **Never default it to `""`.** `EntityNode.get_entity_uri()` resolves
    an unset parm to the *first entity in the project* — the node then
    silently addresses an arbitrary asset while looking, in the UI,
    exactly like a deliberate choice.
  - **Never write a URI into it from `OnCreated`.** That pins the node to
    whichever entity it was born in and defeats the sentinel the parm
    just defaulted to. Only a *group* template pins an entity, because a
    group workfile holds several at once and `from_context` cannot
    resolve to one of them.

  `scripts/verify_entity_from_context.py` audits all three rules; run it
  after touching an entity-aware HDA (see `docs/development.md`).
