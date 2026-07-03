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
