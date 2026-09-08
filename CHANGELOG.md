# Changelog

All notable changes to TumblePipe, one section per release.

**This file is generated — do not edit it by hand.** It is derived from the
conventional-commit subjects between release tags. To change an entry, the
commit subject is the source of truth. Regenerate with:

```bash
uv run --no-project python scripts/generate_changelog.py
```

CI and build commits are omitted deliberately.

## v1.45.1 — 2026-09-07

### Fixes
- fix(scripts): import the submit-jobs dialog as the package module it is (`1afc676`)
- fix(catalog): claim Multi and Root collections so the browser can act on them (`c8f923e`)

### Documentation
- docs: document Multis, retire the asset_browser_catalogs references (`f98e9e4`)

### Tests
- test(catalog): run the catalog against the shipped SDK, pin the Multi lifecycle (`fa8c1ff`)

## v1.45.0 — 2026-09-07

### Features
- feat(catalog): declare the pipeline catalog to TumbleTrove (`b4ada48`)

### Fixes
- fix(startup): drop the package.install() call (`6f19121`)
- fix(lops): resolve from_context in output_modified_prims (`988e333`)
- fix(hda): create_asset_lookdev published no materials out of the box (`95d7519`)
- fix(import_layer): stop reporting "Imported" over an empty stage (`38c1505`)
- fix(hda): re-anchor absolute import paths instead of escaping the geo scope (`64daa45`)
- fix(hda): asset_payload composed under the asset and shared one sidecar (`96fc5db`)
- fix(release): range the notes from the last RELEASED version, not the last tag (`99c664a`)

### Refactors
- refactor(catalog): move the catalog into the package it belongs to (`27dcda8`)
- refactor(catalog): declare a settings page instead of a settings widget (`699ccfa`)

### Documentation
- docs: correct composition.md where this session proved it wrong (`db5fb30`)
- docs: cover the superseded list and what a release's notes actually span (`65b21fe`)
- docs: regenerate CHANGELOG.md for v1.44.2 (`a906321`)

## v1.44.2 — 2026-09-03

### Documentation
- docs: regenerate CHANGELOG.md for v1.44.1 (`7d9eb20`)

## v1.44.1 — 2026-09-03

### Fixes
- fix(ci): resolve the windows build tools through $HOME, not $USERPROFILE (`5a23ecd`)

### Documentation
- docs: regenerate CHANGELOG.md through v1.44.0 (`7b4b2c6`)

## v1.44.0 — 2026-09-02

### Breaking Changes
- chore(toolbar)!: delete the unshipped shelves and the helpers only they reached (`5ace9b8`)
- fix(config)!: store own properties verbatim instead of diffing against inherited (`7454025`)
- refactor!: drop the unused Qt widget kit (`e215aa5`)
- refactor!: drop the unreachable DaVinci Resolve integration (`1cabd87`)
- refactor!: drop the vestigial Result type (`0ff729e`)
- refactor!: drop the RenderMan denoiser wrapper (`daa9644`)
- refactor!: drop the dead data/ value objects (`5fc945d`)

### Features
- feat(submit-jobs): submit the batch through ProcessDialog (`9cfdf16`)
- feat(submit-jobs): tri-state form fields and a pre-flight table (`c4bf4a9`)
- feat(submit-jobs): pure per-entity settings resolver (`7481212`)

### Fixes
- fix(changelog): never announce a commit superseded before it shipped (`75cc773`)
- fix(catalog): refuse to seed the edit dialog with a guessed frame range (`76706d9`)
- fix(catalog): write only the fields the artist actually changed (`23fc007`)
- fix(render): find the RenderSettings prim on the stage instead of assuming it (`0bd7a6e`)
- fix(usd): fail the submission when a staged build's layer is missing (`35aa32b`)
- fix(context-repair): never delete a reservation claim that is still in flight (`d5618ea`)
- fix(submit): apply pre_roll/post_roll to farm renders (`69899e5`)
- fix(render): reject an empty output_paths instead of publishing nothing (`ea83c44`)
- fix(validators): an unknown validator name must fail, not be skipped (`43c5b21`)
- fix(composite): honour step_size in the worker instead of compositing every frame (`84b8dda`)
- fix(validators): stop reporting "no issues found" while holding warnings (`96049c8`)
- fix(farm): reject bool where a farm config expects an int (`2b97a9e`)
- fix(scaffold): stamp new projects at the latest migration version (`ff60580`)
- fix(quality-gates): ask git, not the filesystem, for committed binary HDAs (`85ff41b`)
- fix(farm): require a frame range for render instead of guessing 1001-1100 (`9c138f1`)

### Refactors
- refactor(catalogs): read both channel spellings in the browser catalogs (`eeeef5a`)
- refactor(farm): read both channel spellings in job configs too (`0b05478`)
- refactor(channels): read both channel spellings instead of refusing one (`6d87c59`)
- refactor(build): rename the in-memory resolver keys, and pin what is not renameable (`1713519`)
- refactor(composite): read the channel property key from its one definition (`e98989c`)
- refactor(scene): rename scene.py's private DEFAULT_VARIANT, and pin the wire key (`752e947`)

### Documentation
- docs: record the caller obligation the Edit dialog bug exposed (`3386f53`)
- docs: say what writers emit, not what is "frozen" (`8530a6d`)
- docs: record the channel-rename prerequisites and the release ordering (`36d1069`)
- docs: reconcile the render and context-chain docs with the fail-loudly changes (`11a8d08`)
- docs: reconcile the config-store docs with the verbatim-own-properties change (`cab6873`)
- docs(submit-jobs): correct the dialog's own submission docstring (`23d2238`)
- docs: cover the tri-state form, pre-flight and per-entity resolution (`0e7943c`)

### Chores
- chore(deps): drop two orphaned python dependencies (`96e3e00`)
- chore: pin line endings with .gitattributes (`0eb3b21`)
- chore(submit-jobs): tighten the batch zips and the docs left behind (`60870f1`)

## v1.43.0 — 2026-08-27

### Breaking Changes
- refactor(config)!: retire the Python migrator for the shared core (`b56d0d0`)

### Features
- feat(config): repoint temp:/ off the project drive (v8) (`72b4250`)
- feat(config): drop the retired kits entries from storage_convention (v6) (`026e8ae`)
- feat(wizard): give tt_prepare a headless --migrate mode (`d5cbed3`)
- feat(hpm): ship tt_prepare and register it as a launch hook (`4ec9ccc`)
- feat(wizard): add tt_prepare, the launch-time _config migrator (`86535fe`)
- feat(wizard): apply the _config migrations from core (`bab10c6`)
- feat(wizard): add the migration registry and a preflight to core (`55572bd`)
- feat(templates): department templates own their own layout (`64764cc`)

### Fixes
- fix(storage): resolve temp:/ to machine-local scratch (`7443ba3`)
- fix(config): point the convention modules at the renamed package (v7) (`334a3a5`)
- fix(hda): rebuild asset_thumbnail from clean source, add Type parm (`0272a67`)

### Refactors
- refactor(wizard): split the crate into a workspace with a shared core (`312cd21`)

### Documentation
- docs: document TH_TEMP and the v8 config migration (`f246e5f`)
- docs: cover the hook apps and drop references to what was removed (`466e2bc`)

### Chores
- chore(wizard): stop creating the kits/ directory (`dde13ef`)

## v1.42.0 — 2026-08-26

### Breaking Changes
- refactor!: rename the publish "variant" to a "channel" (`82822e1`)

### Features
- feat(scripts): audit that menu scripts reach a real wrapper method (`cdceb23`)
- feat(config): relabel the browser variants column as Channels (`0e25978`)
- feat(submit): pick render variants from a checkable menu (`2bff20f`)
- feat(submit): default the render department to the open workfile's (`5dc9245`)

### Fixes
- fix(validators): drop the nonexistent Rest LOP hint (`984a1df`)
- fix(hda): stop create_model prefixing absolute paths too (`2f07920`)
- fix(hda): honour incoming path attributes in create_asset_model (`4bc2fc3`)
- fix(browser): stop reporting partial failures as success (`a6aad14`)
- fix(browser): report failed artist-initiated actions instead of only logging (`cc984be`)

### Documentation
- docs: describe channels, and the wire boundary a rename must not cross (`b54adea`)
- docs: describe where model geometry lands under the asset (`ddb3f09`)
- docs: describe the Submit Jobs variant menu (`97d2515`)
- docs: document the workfile-seeded render department (`1db09e7`)
- docs: record that an action the artist asked for must fail visibly (`89858ff`)
- docs: regenerate CHANGELOG.md through v1.41.0 (`567dda5`)

### Chores
- chore(toolbar): drop the dead export_variant and import_variant tools (`925de82`)

## v1.41.0 — 2026-08-03

### Features
- feat(asset-browser): show workfile version notes in a Note column (`1b9f9b2`)
- feat(workfiles): record an artist note on each saved version (`1acfdf4`)

### Fixes
- fix(setup): treat a cancelled wizard as a decline, not a failure (`e80f263`)

### Documentation
- docs(setup): correct the wizard's documented cancel exit code (`fc1c6be`)
- docs: regenerate CHANGELOG.md through v1.40.1 (`658a634`)

## v1.40.1 — 2026-08-02

### Fixes
- fix(setup): ship the project-setup wizard with its executable bit (`c232f65`)

### Documentation
- docs(ci): correct the stale hpm pin reference (`2df48c1`)
- docs: regenerate CHANGELOG.md through v1.40.0 (`601a897`)

## v1.40.0 — 2026-07-31

### Breaking Changes
- fix(hpm)!: declare TH_PROJECT_PATH as a required env var (`4c0b6da`)

### Documentation
- docs: document the TH_PROJECT_PATH requirement (`0dffa7a`)

## v1.39.2 — 2026-07-24

### Fixes
- fix(scripts): spawn tt_setup via $HPM_PACKAGE_ROOT (`1a15324`)

## v1.39.1 — 2026-07-24

### Fixes
- fix(build): run the Windows wizard cargo build under the MSVC env (`34e4582`)

## v1.39.0 — 2026-07-24

### Features
- feat(catalog): implement project_for_ref for Favourites project-scoping (`086af72`)
- feat(wizard): native Rust tt_setup wizard (wizard-src) (`639288e`)

### Refactors
- refactor: move native sources under src/ (src/resolver, src/wizard) (`efbe3ec`)

### Documentation
- docs(hpm): list build-wizard in the [stage] prepack comment (`66ef73a`)
- docs(wizard): document native tt_setup wizard; add real-template parity test (`e898843`)

## v1.38.8 — 2026-07-23

### Fixes
- fix(import_assets): mark duplicates instanceable only when not animatable (`cd3d88e`)

### Documentation
- docs(composition): document instanceable copies and the animatable flag (`327abe2`)
- docs(changelog): regenerate through v1.38.7 (`09de7b4`)

## v1.38.7 — 2026-07-23

### Fixes
- fix(exr): stamp ACEScg chromaticities so RV/Nuke read renders correctly (`045f63b`)
- fix(farm): flatten batched export chunks like the interactive path (`d42c888`)
- fix(usd): stitch keeps non-usd sidecars and skips only the top-level main (`045b704`)
- fix(export): publish stage caches on 1s, not the render step (`e6225a2`)

### Refactors
- refactor(usd): single home for chunk math and sidecar flattening (`613042e`)

### Documentation
- docs(compositing): document ACEScg colour space stamping (`88c5043`)
- docs(tests): update harness README for the minigun 3.x API (`f0e5b60`)
- docs(changelog): regenerate through v1.38.6 (`16f4185`)

### Tests
- test(usd): cover frame chunking for batched export (`fdba611`)
- test: migrate the suite to minigun 3.0.1 (`e584122`)

## v1.38.6 — 2026-07-22

### Fixes
- fix(publish): refresh session after local publish so own exports appear without restart (`6d7de40`)

### Documentation
- docs(changelog): regenerate through v1.38.5 (`e9d90c1`)

## v1.38.5 — 2026-07-21

### Fixes
- fix(farm/publish): force all imports to latest consistently on publish (`3c26184`)
- fix(import_rigs): honor per-row version selection instead of forcing latest (`52ddc87`)

### Documentation
- docs(development): drop reference to deleted update usd_rop trackprimexistence (`a046abb`)
- docs(changelog): regenerate through v1.38.4 (`73bb8a2`)

### Chores
- chore(farm): remove dead update/publish.py (`4c145e1`)
- chore(farm): remove dead update/export.py + export_houdini.py (`0bec62a`)

## v1.38.4 — 2026-07-21

### Features
- feat(anim): output Cd/Alpha from th animate as displayColor/displayOpacity (`f675948`)

### Documentation
- docs(changelog): regenerate through v1.38.3 (`4d1c012`)

## v1.38.3 — 2026-07-21

### Features
- feat(desktop): show network controls on the Scene View by default (`7dc129b`)

### Fixes
- fix(desktop): stop desktops overriding viewport clip/homing defaults (`d33323c`)

### Documentation
- docs: retire references to the removed tag-time ci checks (`7d76a71`)
- docs(changelog): regenerate through v1.38.2 (`a09d601`)

## v1.38.2 — 2026-07-21

### Fixes
- fix(farm): encode full contiguous frame span in farm mp4s (`04f91ce`)

### Documentation
- docs(changelog): regenerate through v1.38.1 (`34236e2`)

### Chores
- chore(scripts): add playblast path convention auditor (`8042494`)

## v1.38.1 — 2026-07-20

### Fixes
- fix(farm): keep the root layer out of the department cut (`d2f5a2a`)

### Documentation
- docs(usd): point the cut's pool guard at RESERVED_NAMES (`7e012df`)
- docs(composition): say the cut only slices pool departments (`8263ab3`)
- docs(changelog): regenerate through v1.38.0 (`4fcd4dd`)

## v1.38.0 — 2026-07-20

### Features
- feat(submit-jobs): say what the department dropdowns actually do (`0b06eca`)
- feat(render_stage): cut the department stack at the submitted department (`1bd2f79`)
- feat(import_shot): add exclude_downstream_of for a render's department cut (`1511dcc`)
- feat(usd): drop staged layers past a department cut before flattening (`6c37174`)
- feat(config): add department_names_up_to, the inclusive pool slice (`2d19566`)

### Fixes
- fix(farm): report a disconnected export node instead of crashing on it (`c181625`)
- fix(export): skip a disconnected export node instead of failing its department (`6a8ae6f`)
- fix(farm): honour the render and playblast department selections (`fc91eeb`)
- fix(resolver): re-check layer handle after UpdateAssetInfo (`4f55af1`)

### Documentation
- docs(development): describe the skip-vs-fail task contract (`72d5910`)
- docs: describe the render department cut, correct the scoping boundary (`a998ee7`)

### Tests
- test(process_dialog): pin the skip-vs-fail split for grouped tasks (`326e5a1`)

## v1.37.2 — 2026-07-18

### Fixes
- fix(lpe_tags): surface tags that match no lights, and find mesh lights (`1710dae`)
- fix(lpe_tags): build render vars for mesh-light LPE tags (`32e800d`)
- fix(asset-browser): give Reload a glyph distinct from Refresh (`637b2b3`)
- fix(import_layer): clear the node bypass when an import succeeds (`900067c`)
- fix(import_model): resolve the variant set from the Department parm (`b3ff690`)
- fix(import_model): keep the variant menu alive when the entity can't resolve (`6292a14`)

### Documentation
- docs(changelog): regenerate through v1.37.1 (`2801830`)

### Tests
- test(lpe_tags): pin the tag-to-render-var contract, document HDA edits (`63c05a9`)
- test(import_model): add a verify script for the variant dropdown (`68f8b10`)

## v1.37.1 — 2026-07-17

### Fixes
- fix(export): exempt any workfile-directory cache from the escaping-arc guard (`5dff8ce`)

### Documentation
- docs(changelog): regenerate through v1.37.0 (`bdcad27`)

## v1.37.0 — 2026-07-17

### Features
- feat(catalog): replace the session panel's department list with a workspace/export detail view (`03fefd9`)

### Fixes
- fix(export): exempt cross-entity SOP th::cache bgeo from the escaping-arc guard (`299d7fa`)

### Documentation
- docs(changelog): regenerate through v1.36.1 (`548b955`)

## v1.36.1 — 2026-07-17

### Fixes
- fix(paths): export get_workspace_relpath so it survives the F,E9 lint (`adb4146`)

## v1.36.0 — 2026-07-17

### Features
- feat(cache): add Department parm so th::cache can load other workfiles' caches (`67dc2be`)
- feat(asset-browser): session pane shows Current Workspace + Latest Export (`d953006`)

### Fixes
- fix(ci): emit the canonical registry install line in release notes (`56ba3b1`)

### Documentation
- docs(asset-browser): finish the badge -> licence terminology sweep (`38085fc`)
- docs(changelog): regenerate through v1.35.0 (`93072a9`)

## v1.35.0 — 2026-07-17

### Features
- feat(catalog): give a Multi's department coverage a way back in (`d5ec7bd`)
- feat(catalog): fill the session panel, and don't blank it on a Multi (`f996e1b`)
- feat(scripts): audit that every HDA callback reaches a real function (`6231bba`)
- feat(scripts): audit asset nesting for cycles across projects (`92314a5`)
- feat(lops): open an asset's department workfile from the layer inspector (`9acf990`)
- feat(lops): inspect the department layers inside an asset's Layer Stack row (`2a6ca9e`)
- feat(usd): read an asset's department layers back out of its staged file (`4d8e7b1`)

### Fixes
- fix(catalog): lose the badge on an old tumbletrove, not the deck (`6a9b796`)
- fix(startup): follow the radial out of tumbletrove, and stop hiding it (`f9b2c98`)
- fix(cache): forward select() so th::cache's Entity button works (`7da9ec0`)
- fix(lops): stop import_assets raising on its own department pool (`034cb25`)
- fix(lops): show which departments are excluded in import_asset's menu (`9720afd`)
- fix(catalog): stop shipping two shapes under metadata["departments"] (`0895086`)
- fix(lops): report the loaded version in the Layer Stack, not the stripped pin (`e33b81b`)

### Performance
- perf(catalog): one read per dept row, and the licence badge it needs (`1c8eae2`)
- perf(lops): don't flatten a staged asset for an exclusion that drops nothing (`a433d11`)

### Refactors
- refactor(paths): one version_name_from_path, not two hand-rolled copies (`c44a96b`)
- refactor(catalog): drop the hover grid's now-dead shape normalization (`761cfd5`)

### Documentation
- docs(development): document the asset-payload verification (`ed48f79`)
- docs: the right pane is the session panel in the Pipeline scope (`f65335e`)
- docs(structure): the radial is its own package now (`77146dc`)
- docs(otls): a callback takes three files, not two (`f423e53`)
- docs: nesting is not containment, and two stale notes (`38ce84c`)
- docs(design): mark the non-renderable-exclusion shortcut as fixed (`ff40e29`)
- docs(design): the header-metadata claim is false; the flatten is lossless (`cd2d858`)
- docs(composition): department exclusion does not survive the session (`1224ccd`)
- docs(design): the nested-asset workflow, and why "X in Y" is a lie (`3a24ab1`)
- docs(composition): document the layer inspector, and that only renderable departments compose (`8f051f4`)
- docs(design): record the inspector as built, and what is still unverified (`61a9e18`)
- docs(catalog): correct the departments schema in PipelineAssetMetadata (`2a400ca`)
- docs(design): plan the asset layer inspector (`9508d23`)
- docs(changelog): regenerate through v1.34.0 (`8e0427c`)

### Tests
- test(lops): pin the asset inspector button in the layer stack verify (`146f200`)

## v1.34.0 — 2026-07-16

### Features
- feat(denoise): drive idenoise instead of hython (`86a7fe0`)
- feat(apps): wrap idenoise and add the exr plumbing it needs (`56838f4`)

### Fixes
- fix(usd): name flatten instances from the asset URI, not the scraped tag (`76856f7`)
- fix(usd): never re-derive an xformOpOrder that already composed (`3d90252`)
- fix(denoise): probe channel counts on a real frame, not a frame pattern (`f071918`)
- fix(import-shot): author the prototype transform in the duplicates subnet (`0825c93`)
- fix(usd): author the prototype transform in the static flatten too (`fe8628a`)
- fix(import): author the transform on re-established sub-asset prototypes (`d81df91`)
- fix(asset-browser): don't build the DatabaseWindow on a worker thread (`ed33d51`)

### Documentation
- docs(composition): document the direct-render flatten; correct the order rule (`3e7f031`)
- docs(deadline): record which farm tasks need a Houdini license (`a5395dc`)
- docs(designs): record what the idenoise port actually verified (`b768b48`)
- docs(composition): record that the prototype is re-established too (`d72e24d`)
- docs(designs): plan denoising the farm renders without a Houdini license (`0ef8ed9`)
- docs(designs): reconcile the Qt thread-safety plan with what shipped (`1b15828`)
- docs(designs): plan for killing the asset-browser Qt/thread crash class (`50397ef`)

### Tests
- test(usd): pin the prototype's transform in the static instance defs (`cfc1307`)

## v1.33.1 — 2026-07-16

### Fixes
- fix(export): scope the dropped-asset guard to the asset export tree (`65a3c6a`)
- fix(farm): derive resolver major from the running Houdini; harden the contract (`726b21a`)
- fix(farm): run jobs against the submitting Houdini's major, not hardcoded 21 (`48b9a94`)

## v1.33.0 — 2026-07-15

### Features
- feat(ocio): add legacy W:/_pipeline OCIO retire audit script (`18e8ae3`)
- feat(context): add verify_context_chain diagnose/repair tool (`e843e39`)

### Fixes
- fix(export): allow artist-added geometry beside tracked assets (`80abf5d`)
- fix(context): harden workfile version chain against races and v0000 re-anchor (`47674db`)
- fix(import-asset): merge the input in after metadata instead of feeding the sublayer (`657666c`)
- fix(ci): drop stale ocio dir check from validate-structure (`ab68672`)

### Documentation
- docs: document the dropped-metadata guard and its arc-aware harness (`91b524d`)
- docs: document the legacy OCIO retire audit, correct the concat claim (`b74dd09`)
- docs: document the context-chain audit and test (`84920b2`)

### Tests
- test(context): pin the workfile context-chain hardening (`9f5ccc6`)

## v1.32.1 — 2026-07-15

### Fixes
- fix(publish): default the validation task off pending convention work (`af65de1`)
- fix(farm): load only the current package houdini dir in hython env (`8c8b3af`)
- fix(ocio): own the color config per project, not in the package (`4149d35`)
- fix(resolver): skip expired layer handles in refresh_context (`90fbcff`)

## v1.32.0 — 2026-07-15

### Features
- feat(browser): Playblast section in the Submit Jobs dialog (`f6566fb`)
- feat(farm): submit playblast jobs from batch_submit (`322ccec`)
- feat(farm): playblast job family (`421e712`)
- feat(farm): playblast task family (headless GL via Hydra Storm) (`58789c8`)
- feat(browser): per-entity department assignment UI (`83c96ef`)
- feat(browser): department pool editor (`775e137`)
- feat(browser,hdas): scope department menus and rows to the entity (`3d728d0`)
- feat(config): per-entity department assignment (`9eb1c62`)
- feat(config): department pool ordering, insert-at-index, reserved-name guard (`454e5c7`)
- feat(migration): refresh _config/templates from the packaged scaffold (`7aecaee`)
- feat(cache): add an Entity parm and a database frame range to th::cache (`93e4dc1`)
- feat(otls): give th::create_asset an Entity parm (`5da56a8`)

### Fixes
- fix(catalog): make the department-pool fallback loud, not silent (`96da129`)
- fix(config): don't KeyError on un-migrated department nodes (`b966340`)
- fix(export): preserve animated switch/blend on USD export (track prim existence) (`bfabe3d`)
- fix(farm): pass department to playblast/daily path helpers (`43480a5`)
- fix(hdas,browser): scope the department menus the first pass missed (`d3ef089`)
- fix(otls): restore import_lop_camera's editable embedded import node (`0cab6cc`)
- fix(otls): rebuild th::import_lop_camera on th::import_shot (`18978cc`)
- fix(templates): leave single-entity graphs on from_context (`2faf19a`)
- fix(hdas): keep entity parms on the from_context sentinel (`b98fbe8`)

### Documentation
- docs(config): document the v3 departments migration + un-migrated resilience (`c62ad35`)
- docs: pin the animated-switch export trackprimexistence contract in development.md (`ee1397a`)
- docs(composition): say where "pipeline order" comes from, and that scoping never changes a render (`49a6ce8`)
- docs(design): editable department pool + per-entity department assignment (`c554d89`)
- docs: document the from_context contract and template migration (`e42ccf2`)

### Tests
- test(hdas): audit the entity from_context contract (`855b6f4`)

### Other Changes
- docs+tooling: farm playblast job harness and docs (`97ebdc4`)
- docs+tooling: department pool audit script and configuration docs (`4cc809e`)

## v1.31.0 — 2026-07-14

### Features
- feat(export): report the exported version in the process dialog (`248c0d1`)
- feat(changelog): generate CHANGELOG.md from the tag history (`489aace`)
- feat(submit-jobs): checkable entity tree for multi-entity submission (`d6d44fd`)
- feat(identity): stamp the TumbleTrove account as the pipeline user (`ebb1bfd`)
- feat(catalog): show category path on asset rows in mixed-category views (`c882e70`)
- feat(export): publish versioned th::cache files by reference (`473b99a`)

### Fixes
- fix(changelog): drop bump commits, parse breaking-change markers (`eb5fc5f`)
- fix(catalog): refresh the created/saved entity's own card row (`b71d1a8`)

### Refactors
- refactor(process-dialog): delete the unused ProcessTaskTableModel (`8c4eef6`)
- refactor(ci): extract the changelog renderer into a shared module (`7423c64`)

### Documentation
- docs(design): native USD variants for assets, Render Layer rename for shots (`9362c1f`)
- docs: versioned-cache exemption in composition.md, catalog its harness (`533c6c6`)

## v1.30.0 — 2026-07-13

### Features
- feat(catalog): surface the project name via get_sidebar_subtitle (`d848317`)
- feat(submit-jobs): entity selector defaulting to the open entity (`fe4585d`)

### Documentation
- docs: catalog the Submit Jobs entity-selector harness in development.md (`8359664`)

## v1.29.0 — 2026-07-13

### Features
- feat(submit-jobs): first/middle/last range mode, render on by default (`f0e3606`)
- feat(catalog): Render quick action next to Publish (`0073ccf`)
- feat(catalog): surface publish author in detail rows + dept hover (`dbe70c6`)
- feat(catalog): per-scene user/edited in list-view deck rows (`8048d4e`)

### Fixes
- fix(import-shot): anchor Layer Stack insert on parms, not folder names (`33b34a8`)
- fix(catalog): Multi-covered dept tooltip says double-click to open (`c3b67d5`)

### Refactors
- refactor(catalog): drop dead user_mtime_label helper (`d6594fd`)

### Documentation
- docs: pin the HDA spare-parm anchoring contract (harness + development.md) (`e73917d`)

## v1.28.0 — 2026-07-13

### Features
- feat(browser): add Update quick action — refresh imports without reloading (`d43ff74`)

### Fixes
- fix(resolver): make refresh_context() reload stale entity layers (`02a8869`)
- fix(browser): migrate pref files that froze auto_refresh_on_open=false (`3fadbb1`)
- fix(browser): route Reload through prepare_scene_swap (`708193d`)
- fix(import): request resolver refresh from import_assets and import_layer (`d2dc25c`)
- fix(resolver): implement _RefreshContext so refresh_context() actually invalidates (`2f1c584`)

### Documentation
- docs: pin the resolver-refresh contract (harness script + caveats) (`cff244b`)
- docs: document mid-session version pickup (Update action + layer reload) (`19c6774`)

## v1.27.1 — 2026-07-11

### Fixes
- fix(paths): resolve render layers from 'variants', unbreaking comp AOV discovery (`6859ec6`)
- fix(export): don't flag USD search-path arcs as dangling (`acf24ed`)

### Documentation
- docs: document the compositing workflow (build_comp, farm chain, MP4s) (`a345bb8`)
- docs: document the dangling-arc guard and its search-path exception (`ba540db`)

## v1.27.0 — 2026-07-11

### Features
- feat(hpm): root catalog downloads under the active project (`ff00db7`)
- feat(config): add is_animatable entity predicate (`795009b`)

### Fixes
- fix(staging): fall back to default-variant exports in variant shot builds (`d68f311`)
- fix(asset-browser): keep asset frame range across workfile re-open (`fd3ae52`)

### Documentation
- docs: document default-variant fallback in variant staged builds (`13148b2`)

## v1.26.0 — 2026-07-10

### Features
- feat(asset-browser): add User + Edited columns to the pipeline list view (`d181045`)

## v1.25.3 — 2026-07-10

### Fixes
- fix(asset-browser): default Auto-import latest on workfile open to on (`1587e5f`)
- fix(asset-browser): omit the unimplemented Tasks sidebar section (`43dab2b`)

## v1.25.2 — 2026-07-09

### Fixes
- fix(import-shot): only reset the playhead when it is outside the shot range (`c9df63e`)

## v1.25.1 — 2026-07-08

### Fixes
- fix(scripts): satisfy the F841 gate in the process-dialog harness (`4c7baed`)

## v1.25.0 — 2026-07-08

### Features
- feat(process-dialog): warn when cancelling leaves enabled steps unrun (`2726b9a`)
- feat(process-dialog): surface export progress breadcrumbs (`4b4cf29`)

### Fixes
- fix(export-layer): refuse to export under an unlisted variant name (`1229c0e`)
- fix(process-dialog): show the running child task in the status label (`3363728`)
- fix(config-editor): commit URI-tree renames once, when the editor closes (`e8e1b63`)
- fix(config-editor): commit edits on click-away and make key renames stick (`5da3ffc`)

### Refactors
- refactor(otls): rename th_a_b_slider operator type to a_b_slider (`53b5e2f`)
- refactor(otls): rename th_cop_material_library into the th namespace (`a192b25`)

### Documentation
- docs: cover the Qt widget harnesses and correct a model docstring (`1b19e46`)
- docs(asset-browser): note env-path validation in the catalog docstring (`a292b83`)

### Tests
- test(process-dialog): add a QTest harness for the execution UX (`8a4fad4`)
- test(config-editor): add a QTest harness for the editor's commit UX (`d20eb07`)

## v1.24.2 — 2026-07-07

### Fixes
- fix(ci): adopt the HPM_HOUDINI_MAJORS build-env contract for the resolver build (`85b1845`)

### Documentation
- docs: document local package builds and the HPM_HOUDINI_MAJORS knob (`7905458`)

## v1.24.1 — 2026-07-07

### Fixes
- fix(ci): carry hpm's operator index through to version registration (`d9182a9`)

## v1.24.0 — 2026-07-07

### Features
- feat(scripts): sweep workfiles for H22-enabled layer save paths (`10dc290`)

### Fixes
- fix(ci): point the resolver build's missing-install error at HOUDINI_MAJORS (`52032d1`)
- fix(image_plane_painter): OnDeleted no longer errors without a thumbnail (`fd2bef1`)
- fix(export): refuse to publish layers whose composition arcs escape the folder (`ebe02a1`)
- fix(houdini): ship OnCreated scripts turning off H22's default layer save path (`112c55b`)
- fix(templates): disable H22-default layer save paths on template nodes (`51674c5`)
- fix(slapcomp): select channels positionally in constant-alpha branch (`61ac996`)

### Documentation
- docs: document the export portability contract and layer-save-path guard (`6156027`)
- docs(exr): document the per-AOV channel-naming contract at the split site (`ddd7fbe`)

## v1.23.3 — 2026-07-06

### Fixes
- fix(batch-submit): collapsed stages derive instance op order from composition (`df7e85e`)
- fix(import-shot): apply placement op order after the duplicates subnet (`a2eb6eb`)

### Refactors
- refactor(import): share placement-op-order authoring via util helper (`8956c6e`)

### Documentation
- docs: one shared placement-op-order rule across all three instance paths (`d6d7ea8`)

## v1.23.2 — 2026-07-06

### Fixes
- fix(import-asset): author xformOpOrder for composed placement ops (`16d0507`)
- fix(import-asset): re-established duplicates author dup op + xformOpOrder (`2ed7fcd`)
- fix(hda): import_asset transform node uses XformCommonAPI (`6681fd9`)

### Documentation
- docs: re-establishment authors xformOpOrder; drop stale no-transform claim (`f91f853`)

### Other Changes
- Revert "fix(import): duplicate nodes author matrix ops, not XformCommonAPI" (`bc1f26f`)

## v1.23.1 — 2026-07-06

### Fixes
- fix(import): duplicate nodes author matrix ops, not XformCommonAPI (`304861f`)

## v1.23.0 — 2026-07-06

### Features
- feat(config): reject entity names differing only by case from a sibling (`b04c357`)
- feat(scripts): sweep staged tracked-asset counts vs department contexts (`0cf4f84`)

### Fixes
- fix(import-asset): deactivate stale re-established duplicates (`063bae5`)
- fix(staging): no direct ref for transitively-reachable tracked assets (`0c298cb`)

### Refactors
- refactor(staging): drop unused per-layer asset dict from shot_layers (`73316e4`)

### Documentation
- docs(development): local lint advice matches the CI gate (E9,F) (`59738ac`)
- docs: correct the export sidecar story; document dedup + stale cleanup (`db095d2`)

## v1.22.0 — 2026-07-06

### Fixes
- fix(staging): newest-export-wins for shot-flow asset counts too (`4aacf6b`)

### Documentation
- docs: newest-export-wins applies to shot-flow asset counts too (`8b83ffd`)

## v1.21.1 — 2026-07-06

### Fixes
- fix(staging): newest-export-wins for tracked-asset counts, not max() (`039bc05`)

### Documentation
- docs: document newest-export-wins for tracked-asset merges in staged builds (`b5689b3`)

## v1.21.0 — 2026-07-06

### Breaking Changes
- refactor(pipe)!: unify entity-URI accessor naming on get/set_entity_uri (`d74e69d`)
- refactor(pipe)!: evict the config-editor app from pipe/ to tumblepipe.config_editor (`8392e96`)
- refactor!: delete the unused rpc package (~5k lines) (`481a0f7`)
- refactor(radial)!: purge native radialmenu system, migrate to tumbletrove radial (`ca83d9b`)
- refactor(asset-browser)!: adopt tumbletrove's deck vocabulary (`1d47622`)

### Features
- feat(asset-browser): Version column after Name in pipeline list view (`b4417f3`)

### Fixes
- fix(import): variant-aware staged version menus; tag 0-based duplicates (`f4fb3f1`)
- fix(import-asset): re-establish tracked sub-assets on staged import (`d0d610f`)
- fix(import-layer): pass keywords to the widened _metadata_script (`a7132cd`)
- fix(staging): pin tracked-asset variant and version in staged builds (`e5210bf`)
- fix(cloud-stage): nest the entity dict in the configs stage.py emits (`3c49f5a`)
- fix(farm): stage one pinned USD per variant instead of one floating stack (`84b59d7`)
- fix(render-debug): add the variant parm the node code expects (`151060d`)
- fix(startup): skip sparse radial menus instead of writing invalid specs (`789a1e6`)
- fix(asset-browser): import DEPT_SHORT_NAMES in the Multi work-scenes section (`0512019`)
- fix(asset-browser): repair hover-widget import and todos detail section (`b96c114`)
- fix: harden publish path and replace silent fallbacks with hard failures (`ca27f0a`)

### Refactors
- refactor(cloud-stage): build the render stage via the shared builder (`44e4756`)
- refactor(render): shared render-stage graph builder; render_debug uses it (`497eca2`)
- refactor(pipe): hoist identical wrapper boilerplate into EntityNode base (`f112698`)
- refactor(farm): de-fork render and cloud_render job builders (`f7bdf1f`)
- refactor(pipe): split build resolution out of graph.py (`63a607a`)
- refactor(farm): dedupe publish-job builder between update and batch_submit (`7269ebd`)
- refactor: delete dead modules, UV plugin branch, and unused config API (`d59743b`)

### Documentation
- docs: document render staging semantics and the shared graph builder (`5ae3ead`)
- docs: update project structure for the refactor pass (`d36d46e`)

### Chores
- chore: gitignore the startup-generated radial menus (`86da62a`)
- chore(asset-browser): clear _pipeline_detail lint debt (F821/F401) (`9ccbc9e`)

## v1.20.0 — 2026-07-03

### Features
- feat: ship read-only recipes via ASSET_BROWSER_NETWORK_PATH (`88740fe`)

### Fixes
- fix(sop-import): prime embedded labels on create (`2b68e20`)
- fix(import_assets): read-only menus, instance-relative opmenu paths (`5c6511c`)
- fix(sop-import): rebuild th::Sop/import_asset around the live LOP interface (`e97c7b8`)

### Performance
- perf(asset_browser): one enumeration + one scandir per card, GUI-thread guard (`1c8bc2e`)
- perf(config): list_entity_uris for uri-only listings + coherent() batching (`9117975`)
- perf(config): stamp once per read + memoize results — fix v1.16.5 stat storm (`fca1617`)

### Documentation
- docs: document the shipped recipes/ directory (`c021184`)
- docs: read-semantics of the config store, HDA menu-script rules (`d342aa0`)

## v1.19.0 — 2026-07-03

### Fixes
- fix(import): apply department exclusion through nested asset staging (`e68529b`)
- fix(build): compose tracked assets into an asset's staged file (`2a4b2e5`)
- fix(util): scrape tagged 'over' prims - dept-layer imports lost tracking (`bf887c8`)
- fix(import/export): see assets inside instances; tag import_layer roots (`f5ddf50`)

### Refactors
- refactor(otls): purge the th::duplicate HDA (`cb5c1ad`)
- refactor(catalog): adopt the tumbletrove 0.8 hook renames (`ea94203`)
- refactor: legibility pass - flatten nesting, dedupe farm/config validators (`836a1e6`)

### Documentation
- docs: document asset composition, staging, and nested assets (`400e103`)
- docs(otls): document expanded-HDA editing rules (.chn whitespace trap, .orig files) (`843c3b9`)

## v1.18.2 — 2026-07-03

### Fixes
- fix(hda): spaceless layerbreak activation expr - scenes failed to load (`e582fe5`)
- fix(scripts): verify_entity_casing skips export stage/ intermediates (`b20b7e4`)

## v1.18.1 — 2026-07-02

### Features
- feat(scripts): add case-duplicate category fix + entity casing verify CLIs (`370afad`)

### Fixes
- fix(scripts): verify_entity_casing - gate crate scan, tighten filename check (`be0b65a`)
- fix(validators): accept Xform asset categories/roots in shot checks (`6c14a6c`)

### Refactors
- refactor(catalogue): drop dead get_primary_filters (pill row removed upstream) (`e48f8a1`)

### Documentation
- docs(configuration): document the entity casing audit + fix CLIs (`2fe669a`)
- docs(catalogue): stop calling the project: filter a pill (`c8aad07`)

## v1.18.0 — 2026-07-02

### Features
- feat(import): add Import Mode (Reference/Inline) to LOP import nodes (`307acee`)
- feat(export): recognize deliberately inlined assets in the publish guards (`32f97a3`)

### Fixes
- fix(catalogue): preserve case in category/sequence tags — pipeline naming is case-sensitive (`e48b825`)

### Refactors
- refactor(catalogue): drop unused Qt import and ctx_seg local left by the case-preservation fix (`de1be61`)

## v1.17.0 — 2026-07-02

### Features
- feat(browser): default shot collections to list view (`1327ff9`)

### Fixes
- fix(export): block publish when an import node's asset is missing from the metadata scrape (`da80707`)
- fix(import): make 'Exclude departments' actually work on import_asset (`897c6d5`)

### Refactors
- refactor(import): drop dead get_department_names from import wrappers (`03f55e1`)

## v1.16.6 — 2026-07-01

### Fixes
- fix(browser): restore the Publish export window (one dialog, not per-node) (`5eaa5d2`)

## v1.16.5 — 2026-07-01

### Features
- feat(hpm): declare the HDA operator index; drop third-party hpaint (`e48a284`)
- feat(migration): expose migrate-config as a desktop project script (`ad65593`)
- feat(migration): back up original config_convention before clobbering (`4129e46`)

### Fixes
- fix(browser): stop Publish popping one window per export node (`3e2d12a`)
- fix(import/export): stop assets silently dropping when import metadata is missing (`ef0bab6`)
- fix(migration): label migrate-config so the launcher lists it (`b06d275`)

### Refactors
- refactor(hda): hide include_layerbreak toggle on import nodes (`5394d6e`)
- refactor: narrow bug-hiding bare excepts so real errors propagate (`b867ccc`)
- refactor(farm/tasks): dedup hython/OCIO child-process env into env.py helpers (`461faa6`)
- refactor(houdini): drop dev-time submit() reloads and dead commented code (`4c9d4f4`)
- refactor(api): collapse fix_path into local_path; drop dead WSL short-path code (`02e5dea`)
- refactor(ui/database): split json_editor/widget into items + view (`a61cae4`)
- refactor(cops): build_comp.create() adopts ns.create_node (`323f208`)
- refactor(sops): adopt ns.create_node/set_node_style (consistency with lops) (`ae2d36f`)
- refactor(ui/database): split the 2886-line json_editor into a package (`0f9d2aa`)
- refactor(lops): centralize node create/style into ns; fix bugs surfaced en route (`8173ffa`)
- refactor(farm/jobs): make _common Deadline-free; sweep sibling _error helpers (`f925f94`)
- refactor(farm/jobs): migrate propagate/update/composite/cloud_render/render onto _common (batch 2/2) (`82822cd`)
- refactor(farm/jobs): extract shared scaffolding into houdini/_common.py (batch 1/2) (`cffbd09`)
- refactor(pipe/paths): dedupe the 5x-copied workspace resolution in workspace.py (`8c7e6c3`)
- refactor(pipe): split the 1719-line paths.py god module into a paths/ package (`c3aa78c`)
- refactor(houdini/ui): split the 1446-line process_executor god module (`acae9a9`)
- refactor(rpc): centralize the try: import hou guard into util.houdini (`66d165b`)
- refactor(config): merge scene+scenes, push USD build down into pipe (`e37937c`)
- refactor(catalog): drop now-dead per-module api patching on project switch (`e162635`)
- refactor: retire eager api=default_client() across the package (`79f17f0`)
- refactor(config): lazy client proxy, retire eager api bindings + RLock (`e73a347`)
- refactor(config): coherent-read DB store, package engine, versioned migrations (`d8a1544`)

### Documentation
- docs: fix stale import namespace (tumblehead -> tumblepipe) (`9a9cac4`)

### Chores
- chore(rpc,farm): drop pre-existing unused imports in touched modules (`d3a25d2`)
- chore: clean up after hpaint removal; refresh migration docs (`6600a08`)

## v1.16.4 — 2026-06-30

### Fixes
- fix(workfiles): apply config timeline on scene reload (`74f6a3a`)
- fix(timeline): pin frame range across fps change on shot open (`9bee12f`)

### Refactors
- refactor(timeline): set fps before frame range at every call site (`794fb89`)

## v1.16.3 — 2026-06-29

### Refactors
- refactor(desktop): drop rig_tree panel from layout (`dbdac88`)

## v1.16.2 — 2026-06-29

### Fixes
- fix(export): point frame-range error at an option the node actually has (`ffef195`)
- fix(export): refresh config cache before resolving frame range (`6e830f1`)

### Refactors
- refactor(export): drop dead frame-range source cases (`5d97d20`)
- refactor(export): centralize config-cache refresh at execute choke points (`df59c17`)

## v1.16.1 — 2026-06-24

### Fixes
- fix(catalogue): drop stale schema arg from add_entity — silent shot/asset create failure (`f5320ec`)

### Refactors
- refactor(catalogue): remove dead schema_* URI helpers (`37abde2`)

### Documentation
- docs(tests): document catalog lifecycle test, hou stub, and Windows UTF-8 note (`a7a7fc2`)

### Tests
- test(catalog): cover create/edit/delete orchestration against real config (`9c2f6db`)

## v1.16.0 — 2026-06-22

### Features
- feat(rebuild): preserve export_rig nodes on in-place rebuild (`06c44a8`)

### Fixes
- fix(templates): use entity-uri setters in rig/composite group scaffolding (`e755e97`)
- fix(render_debug): remove dead render_layer parm (`778b27e`)
- fix(build_comp): repair shot/render-dept menus and port proxy AOV TOP to URI API (`adebfb6`)
- fix(catalogue): publish export_rig via get_entity_uri and surface publish failures (`15c2fdb`)

## v1.15.2 — 2026-06-18

### Fixes
- fix(farm): thread per-task requirements.txt into HPM job manifest (`ae4826d`)

## v1.15.1 — 2026-06-18

### Fixes
- fix(build_comp): static frame/roll defaults to stop launch hang (`d2abc3a`)

## v1.15.0 — 2026-06-18

### Fixes
- fix(build_comp): repair frame-range defaults after submit_render removal (`b6d9ee3`)
- fix(validators): blendshape department checks 'blshp' not 'geo' (`69c724c`)

### Refactors
- refactor(otls): rename karmafogbox_copy operator type to karmafogbox (`14900ad`)
- refactor(otls): group HDA tab-menu categories under _TumblePipe (`9e39d9f`)

### Chores
- chore(resources): drop orphaned Submit.png icon (`69bb50d`)
- chore(otls): purge superseded image_plane_painter 1.0 (`6a63019`)
- chore: remove stale th_model_validator references (`aead219`)
- chore: remove orphaned submit_render and model_validator lop modules (`765963a`)
- chore(otls): remove dead model_validator and submit_render HDAs (`f74de60`)

## v1.14.0 — 2026-06-17

### Features
- feat(asset-browser): emergency off-thread Save via quick-action right-click (`eb1095a`)

### Fixes
- fix(farm/publish): resolve bundled workfile against the data dir (`4a2cc60`)
- fix(farm/render): resolve bundled collapsed-USD input against the data dir (`e31fe0d`)

### Refactors
- refactor(asset-browser): drop dead detail-panel actions section (`b38f195`)

### Documentation
- docs(deadline): hpm pin v0.22.1 -> v0.22.2 (Windows arg-quoting fix) (`0d1e366`)

## v1.13.0 — 2026-06-16

### Features
- feat(farm): drop WSL2 — use Houdini's native hoiiotool/hffmpeg (`2add15c`)
- feat(farm): run tasks in native Windows python via hpm package-env (`7e252be`)

### Documentation
- docs: state farm prerequisites positively (drop "no WSL2/uv needed") (`7f66ac6`)
- docs(readme): farm needs Houdini, not WSL2/uv/image-tools (`9637452`)

### Chores
- chore(apps): drop the now-functionless WSLENV env patching (`a0c1099`)
- chore(farm): remove obsolete WSL worker-maintenance job (`03b2d0a`)

## v1.12.6 — 2026-06-15

### Fixes
- fix(asset-browser): use license-correct nc_type for all workfile saves (`6b87180`)
- fix(asset-browser): save the scene on the main thread (data loss) (`6666a20`)

### Documentation
- docs(deadline): hpm pin default v0.18.0 -> v0.21.0 (`6e62042`)

## v1.12.5 — 2026-06-15

### Documentation
- docs(deadline): HPM workers need no per-node setup (`298e5af`)

## v1.12.4 — 2026-06-15

### Fixes
- fix(farm): declare the registry in the job manifest; build it with tomli-w (`7a4f465`)

## v1.12.3 — 2026-06-15

### Fixes
- fix(farm): use full creator/slug in the HPM job manifest (`8ae03bf`)

### Refactors
- refactor(farm): move HPM manifest generation to the job creators (`7040369`)

### Documentation
- docs(deadline): use farm Task factory in the submit example (`7b11bf1`)

## v1.12.2 — 2026-06-15

_No user-facing changes._

## v1.12.1 — 2026-06-15

### Features
- feat(farm): bundle the HPM manifest in the job dir + release v1.12.1 (`df02887`)

### Chores
- chore: un-ignore the two intentionally-tracked otls HDAs (`99ba169`)
- chore: gitignore .venv/ virtualenvs globally (`5acb66f`)
- chore(resolver-src): gitignore CMake build dirs (`bf165b4`)
- chore(otls): remove stray ViewerStateName.orig merge leftovers (`d9c36a5`)

## v1.12.0 — 2026-06-15

### Features
- feat(farm): submit jobs to the HPM Deadline plugin (`911c28c`)

## v1.11.0 — 2026-06-12

### Features
- feat(import_model): expose Frame Mode + Import Frame, static by default (`d0a713b`)
- feat(database-editor): restore a global launcher + stop silent failures (`428263c`)
- feat(template): give asset entities a 1001-1200 timeline default (`628fa4c`)

### Fixes
- fix(export_rig): foreground cache save to stop intermittent export hang (`e63cd05`)

### Chores
- chore(ci): drop deleted project_browser.pypanel from build allowlist (`656fcd0`)

## v1.10.0 — 2026-06-12

### Fixes
- fix(hpm): stop staging pypanels entirely (`5944e17`)
- fix(hpm): stage icon_browser.pypanel instead of the deleted browser panel (`40888ae`)
- fix(catalog): wrap Reload Scene's hip load in Manual update mode (`321f92b`)
- fix(shot_sequencer): skip OnInputChanged work while a hip is loading (`032d12a`)

### Performance
- perf(resolver): batch RefreshContext across import-node auto-refresh (`99fb7f1`)

### Refactors
- refactor(ui): retire the legacy Project Browser (`f310d35`)

### Chores
- chore: clean up dead code and stale docs after Project Browser purge (`21a62dc`)

## v1.9.1 — 2026-06-11

### Fixes
- fix(catalog): scope auto-refresh-on-open to import nodes only (`13bd7b0`)
- fix(catalog): version up instead of overwriting on save-before-swap (`dc66065`)
- fix(catalog): suppress full-graph cook on workfile open (`3653c2c`)

### Documentation
- docs(catalog): correct auto-import tooltip after import-only scoping (`b0f521a`)

## v1.9.0 — 2026-06-11

### Features
- feat(asset-browser): dismiss deck popup when opening a workfile (`53a4c7c`)

## v1.8.0 — 2026-06-09

### Features
- feat(asset_browser): rich hover popup on dept icons + clamp grid to 3-4 cols (`1ce35cc`)
- feat(import_asset): add transform handle targeting imported asset (`91b404c`)

### Fixes
- fix(catalog): read TodoItem fields instead of dict keys in detail panel (`4b05dd1`)
- fix(catalog): move framework asset flags from metadata to typed Asset fields (`6be6487`)
- fix(catalog): realign with tumbletrove SubCard/ProjectRegistry rename + gui_dispatch move (`1c8c734`)
- fix(playblast): guard missing-playblast path; drop debug prints + dead code (`dd37ccd`)
- fix(import_assets): finish multi-asset edit state (sidefx_lop_edit) (`1b058b9`)

### Refactors
- refactor: migrate to tumbletrove.* namespace for asset_browser/radial SDK (`380a45e`)

## v1.7.0 — 2026-06-08

### Features
- feat(import_model): department-gated Pack SOP for blendshapes (`bba76a3`)

### Fixes
- fix(import_rig): exclude empty categories from asset listings (`aa5094e`)

### Refactors
- refactor(config): extract is_terminal_entity, fix scene asset listing (`abadcb7`)

## v1.6.0 — 2026-06-08

### Features
- feat(asset_browser): entity lifecycle — bucket create/delete + asset delete (`ab37570`)
- feat(asset_browser): rewrite asset hover popup as a widget tree with dept icons (`d587bab`)
- feat(asset_browser): auto-import latest on workfile open (`1a8ed8e`)

### Fixes
- fix(import_rig): refresh entity cache so new categories appear (`ba639b7`)
- fix(validators): expect 'mtl' material scope, not 'mat' (`cbed5d5`)
- fix(asset_browser): restore mix_hex + import QSize in dept section (`6580ac8`)
- fix(export): localize payload sidecars into the version folder (`f542954`)
- fix(asset_payload): stop primpath duplication when adding payload (`ef563f2`)
- fix(hpm): migrate manifest to 2.0 schema and admit Houdini 22 (`a94072f`)

### Refactors
- refactor(create_model): author model materials under /mtl, not /materials (`a1f8efc`)

### Tests
- test(export): add in-app verification harness for asset_payload fixes (`2688dcc`)

### Chores
- chore(release): bump version to 1.6.0 (`1b63df2`)
- chore: clean up stale references after the Manifest 2.0 / build->pack work (`1ee967f`)

### Other Changes
- Merge remote-tracking branch 'origin/master' into feat/port-wip-features (`85d6cfe`)
- hpm: bump max_version to allow Houdini 22 (`83065f8`)

## v1.5.0 — 2026-06-04

### Fixes
- fix(import): guard metadata update against empty-composed assets (`f1f4781`)
- fix(catalog): entity_uri_for requires exactly three id segments (`61b8661`)
- fix(catalog): one group-context classifier; drop bogus shot category key (`cdf3cf0`)
- fix(catalog): use numeric latest_version at the remaining version sorts (`104b104`)
- fix(catalog): AssetResolver.split rejects non-three-segment ids (`9836a6f`)
- fix(catalog): sort workfile_versions numerically, not lexically (`292eab3`)
- fix(uri): reject empty path segments (`53593b5`)
- fix(naming): is_valid_entity_name is ASCII-only (`0d552a9`)
- fix(paths): get_workfile_context degrades on partial context.json (`f7659c0`)
- fix(scene): read direct scene refs via own-properties, not merged (`0ffb454`)
- fix(scene): match the list-shaped Scene.assets contract (`5e91b22`)
- fix(naming): version validation rejects v10000 and accepts Unicode digits (`36ba5f5`)
- fix(renderer): read settings from the store the writer writes to (`b5948bc`)
- fix(timeline): correct BlockRange.__len__ off-by-one for step > 1 (`b5b05a5`)
- fix(uri): preserve query on join and make hash agree with equality (`36f1760`)
- fix(export): drop stale "assets have no frame range" messaging (`32ff74b`)
- fix(config): resolve entity schema by position when none is bound (`25fb406`)

### Refactors
- refactor(catalog): dispatch container ops to ContainerManager (`b2949fe`)
- refactor(catalog): move container operations into ContainerManager (`e48046f`)
- refactor(catalog): add ContainerManager skeleton + wire into catalog (`20fe608`)
- refactor(config): stop storing the per-node schema; derive everywhere (`232a3dd`)
- refactor(validation): broken validators.py fails loudly (`814f9a8`)
- refactor(renderer): one source of truth for defaults (`8af754b`)
- refactor(config): resolve entity schema by position only (single source of truth) (`88d9c0c`)
- refactor(uri): flat dataclass; delete wildcard node hierarchy, db.py, parse() (`1d4b58f`)
- refactor(timeline): validate FrameRange roll underflow at construction (`fd2b524`)

### Documentation
- docs(catalog): record ContainerManager as the home of container behaviour (`d64aab7`)
- docs(tests): index test_validation and update schema-derivation wording (`db7eaf0`)
- docs(tests): document the asset_browser stub and test_catalog surface (`1de1bea`)
- docs(tests): index the audit test surfaces in the harness README (`e3924cb`)
- docs(tests): index the frame-range inheritance surface in the harness README (`08e9d0a`)

### Tests
- test(department): guard default-flag resolution from sparse storage (`5108fef`)
- test(catalog): cover catalog addressing + fix parse_entity_ref segment glue (`79fc656`)
- test: add round-trip guards for config convention, variants, io, cache (`60dfc62`)

### Chores
- chore: remove dead scene_description_dialog (`4138801`)

## v1.4.7 — 2026-06-01

### Fixes
- fix(export): abort instead of publishing a layer with a dangling payload (`f3836ab`)
- fix(asset-catalog): reload config snapshot from disk on refresh (`c642eb2`)

### Documentation
- docs(tests): index the export-guard path policy test in the harness README (`910b629`)
- docs(tests): note the cache-coherency test category in the harness README (`af4a070`)

### Tests
- test(config): pin external-write reload of the entity cache (`d288b1a`)

## v1.4.6 — 2026-06-01

### Fixes
- fix(hpm): migrate manifest to 2.0 schema and admit Houdini 22 (`1bd9e0b`)

### Chores
- chore: clean up stale references after the Manifest 2.0 / build->pack work (`98131ee`)

## v1.4.5 — 2026-06-01

### Fixes
- fix(asset_browser): apply FPS and frame range when opening workfile (`4a7cfbe`)

### Refactors
- refactor(asset_browser): extract SceneManager to _pipeline_scene (`af5f7fc`)
- refactor(asset_browser): extract DetailSectionBuilder to _pipeline_detail (`75fa802`)
- refactor(asset_browser): extract WorkfileManager to _pipeline_workfiles (`7404644`)
- refactor(asset_browser): move PipelineCatalog to _pipeline_catalog (`3a09693`)
- refactor(asset_browser): extract ThumbnailManager to _pipeline_thumbnails (`e6eb1be`)
- refactor(asset_browser): extract DropRouter to _pipeline_drops (`e74d4c6`)
- refactor(asset_browser): hoist DeptNameLabel / DeptMetaLabel to _pipeline_widgets (`01ad20f`)
- refactor(asset_browser): polymorphic GroupContainer / SceneContainer (`57d8606`)
- refactor(asset_browser): extract AssetResolver to _pipeline_resolver (`158a49a`)
- refactor(asset_browser): extract ClientPool to _pipeline_clients (`85fd754`)
- refactor(asset_browser): route URI construction through _pipeline_uris (`4722e68`)
- refactor(asset_browser): extract Houdini bridge + ProjectActivator (`7e5c84c`)
- refactor(asset_browser): unify asset and shot discovery into one pass (`4dcd8ba`)
- refactor(asset_browser): add AssetRef/ShotRef sum type + helpers (`49a5780`)
- refactor(asset_browser): extract DeptVersionStore from PipelineCatalog state (`e00d808`)
- refactor(asset_browser): drop defensive except blocks around no-op ops (`7182536`)
- refactor(asset_browser): extract workfile globbing helpers (`6b54d34`)
- refactor(asset_browser): raise from collection ops instead of swallowing (`a30a2e6`)
- refactor(asset_browser): consolidate dept short-name maps in one module (`1146ee3`)
- refactor(asset_browser): drop bool return on _write_description (`805e991`)

### Documentation
- docs(asset_browser): point header at the registry constraint (`0f2edc3`)

### Chores
- chore(asset_browser): retag the now-tiny "Detail panel layout" section (`ab24f9d`)
- chore(asset_browser): drop _mix_hex residue + extend docs for the new managers (`ce52079`)
- chore(asset_browser): post-sweep cleanup + flesh out the docs (`a6da1d2`)
- chore(asset_browser): defensive sweep — flatten _publish_current_scene_impl (`6762a84`)
- chore(asset_browser): first pass of the defensive try/except sweep (`3345a9d`)
- chore(asset_browser): post-refactor cleanup + document the catalog directory (`0832c0c`)
- chore(asset_browser): tidy post-refactor stragglers (`21dfb38`)
- chore(asset_browser): clean up post-refactor stragglers (`9653166`)

## v1.4.4 — 2026-05-26

### Features
- feat(config): let set_properties attach a schema to schema-less leaves (`4d8df8d`)

### Fixes
- fix(api): switch default_client lock to RLock to close re-entry deadlock (`943242a`)
- fix(project_template): correct tumblehead.* imports to tumblepipe.* (`0bef314`)
- fix(config_convention): don't treat root entity's own props as inherited (`c3a34a0`)
- fix(config/department): write to departments:/ to match the read side (`0ceab2d`)
- fix(config/farm): point pool/priority writers at entity:/ root (`f37bddb`)
- fix(asset_browser): apply FPS and frame range when opening workfile (`ad2b367`)

### Refactors
- refactor(asset_browser): unify asset and shot discovery into one pass (`caa9849`)
- refactor(asset_browser): extract DeptVersionStore from PipelineCatalog state (`1d25573`)
- refactor(asset_browser): cut ~40 defensive except blocks that hid bugs (`040dc1c`)
- refactor(asset_browser): raise from collection ops instead of swallowing (`483164d`)
- refactor(asset_browser): consolidate dept short-name maps in one module (`dd353c5`)
- refactor(asset_browser): drop bool return on _write_description (`0f3f893`)
- refactor(asset_browser): extract workfile globbing helpers (`7eeff29`)
- refactor(asset_browser): replace AssetId with AssetRef/ShotRef sum type (`9804e10`)

### Documentation
- docs: README + dev guide reflect the three-convention layout and tests/ (`ff09e45`)
- docs(configuration): drop stale render_convention.py bullet (`732c20c`)
- docs(asset_browser): document why pipeline.py can't split into a package (`603d3b2`)

### Tests
- test: property-based tests for the config layer (uv + minigun-soren-n) (`596c0f6`)

### Chores
- chore(tests): drop the tumblehead→tumblepipe convention patch shim (`d438205`)
- chore(otls): drop stale .OPdummydefs / .OPfallbacks from create_asset_model (`062286d`)
- chore(asset_browser): clean up post-refactor stragglers (`80ad0e0`)

### Other Changes
- merge: take upstream asset_browser_catalogs/pipeline.py + _pipeline_types.py (`a567837`)
- merge: absorb upstream feature work outside asset_browser/pipeline.py (`c98323b`)

## v1.4.2 — 2026-05-20

### Features
- feat(asset_browser): PipelineCatalog.get_asset_hover_html (`3ab9548`)
- feat(asset_browser): collection-scoped pill counts in PipelineCatalog (`ccff887`)
- feat(otls): import_model mirrors inner import_layer's bypass state (`2be0125`)
- feat(asset_browser): drop assets into SOP networks as th::import_model (`12cb40c`)

### Fixes
- fix(otls): import_model — swap Variant parm for Department picker (`6d7738c`)

## v1.4.1 — 2026-05-19

### Features
- feat(asset_browser): autosave-on-scene-change toggle + quick-action hover content (`94cf8cf`)

### Fixes
- fix(otls): default entity to 'from_context' + zero-index Active Variant (`3054b0c`)

### Chores
- chore(asset_browser): drop stderr debug prints from pipeline catalog (`3d4a464`)

## v1.4.0 — 2026-05-19

### Features
- feat(validators): department-aware export validation with grouped issues + suggestions (`b68003c`)
- feat(asset_browser): Multi + Root container support (`0524de5`)
- feat(otls): wire entity-derived prim path into import/layer output modifiedprims (`c9970e2`)
- feat(otls): entity/path toggle + jump button on create_asset_{model,lookdev} (`cdd571c`)

### Fixes
- fix(otls): refresh entity/department labels in setters (`9a6466f`)
- fix(asset_browser): network thumbnail on asset drop (`279cb1a`)

### Performance
- perf(asset_browser): fast-path _activate_project + rename invalidate_cache (`d4a5374`)

### Refactors
- refactor(asset_browser): make attach_network_thumbnail public (`516d63c`)

### Other Changes
- scaffold: bundle _config/templates/ for assets + shots departments (`ebd98e0`)

## v1.3.2 — 2026-05-15

_No user-facing changes._

## v1.3.1 — 2026-05-15

_No user-facing changes._

## v1.3.0 — 2026-05-15

### Features
- feat(otls): import-asset/shot lifecycle callbacks for network thumbnails (`8bcd69e`)
- feat(radial): bundle TumblePipe radial menus + register at startup (`845df81`)
- feat(asset_browser): submit-jobs dialog + drop status column from list view (`8be35e2`)

### Fixes
- fix(asset_browser): always invoke quick-action refresh_cb in finally (`f1a1616`)

### Chores
- chore(tools): local dev build script (`cb05610`)

## v1.2.3 — 2026-05-15

_No user-facing changes._

## v1.2.2 — 2026-05-15

### Fixes
- fix(tt_setup): make project-setup wizard legible on dark themes (`bd83950`)

### Refactors
- refactor(tt_setup): consolidate status-label updates into helpers (`6118d97`)

## v1.2.1 — 2026-05-06

### Fixes
- fix(ci): discover Houdini install path on linux + defensive checks (`2d74554`)

## v1.2.0 — 2026-05-06

### Features
- feat(catalog): typed PipelineAssetMetadata schema (P6c partial) (`1784842`)

### Fixes
- fix(catalog): pipeline.py absolute import (was: relative, broke discovery) (`1ec3175`)

### Refactors
- refactor(catalog): extract types & constants to _pipeline_types (P4 partial) (`0e38f09`)
- refactor(catalog): typed AssetId for 3-segment ids (P6b) (`9784cd5`)
- refactor(catalog): drop start_frame/first_frame version-skew fallbacks (P9) (`4530d19`)
- refactor(catalog): import value types from api.types (P6d) (`01e6949`)
- refactor(pipeline): adopt Catalog lifecycle hooks (P8 partial) (`1f1ba5d`)
- refactor(catalog): typed errors + ClientSlot state machine (P0b + P7) (`c69101c`)
- refactor(catalog): purge disabled render/playblast subsystem (P3) (`72680a5`)

### Documentation
- docs(catalog): module docstring reflects env-wins-on-bootstrap (`6a3e948`)
- docs(catalog): update bootstrap_from_env call-site comment (`6a36454`)
- docs: refresh stale _ensure_client reference in get_available_tags (`7acbac8`)

## v1.1.27 — 2026-05-06

_No user-facing changes._

## v1.1.26 — 2026-05-06

### Features
- feat: tt_setup project wizard (`c68f377`)

### Other Changes
- project_template: drop spurious +x bits (`e06e4b8`)
- config: default TH_CONFIG_PATH and TH_EXPORT_PATH to project subpaths (`107d6ad`)

## v1.1.25 — 2026-05-06

### Other Changes
- asset_browser_catalogs: disable render + playblast disk discovery (`1e8441c`)

## v1.1.24 — 2026-05-06

### Features
- feat(asset_browser): pipeline detail tabs + drop upgrades (`d119ac8`)

### Other Changes
- asset_browser_catalogs: don't block Houdini load on catalog initialize (`4780c4f`)

## v1.1.23 — 2026-05-05

### Chores
- chore: bump to 1.1.23 — CI fix for new TumbleTrove API shape (`5348e4c`)

## v1.1.22 — 2026-05-05

### Chores
- chore: bump to 1.1.22 — Houdini 22 support (`62552fc`)

### Other Changes
- houdini_majors: link 'add a new major' to hpm.toml max_version bump (`af89b69`)
- hpm: cap max_version at the highest Houdini major we build for (`12d9c18`)
- python: drop unused 1x version segment from package path (`a76d731`)
- python3.13libs: drop sys.path-walking dev-session fallback (`5483693`)
- python3.11libs: drop sys.path-walking dev-session fallback (`63e215e`)
- python3.13libs: bring pythonrc.py in sync with python3.11libs (`06e7a2c`)
- resolver(cmake): add python313 to bundled-python search list (`6bce333`)
- hpm: route resolver env per Houdini major via $HOUDINI_MAJOR_RELEASE (`3a2d783`)

## v1.1.21 — 2026-04-30

### Features
- feat(asset_browser): playblast preview cards + department short labels (`365761c`)
- feat(asset_browser): add Render asset type — browse on-disk renders + dailies (`b8f1d0b`)
- feat(asset_browser): consolidated Info tab + Tasks rename (`4509a0e`)
- feat(asset_browser): ship TumblePipe brand logo as catalog icon (`d7d04b7`)

### Fixes
- fix(asset_browser): shot drop fails for numeric-only shot names (`a45f29a`)
- fix(asset_browser): always handle drops, never fall back to Place dialog (`73c4a11`)

### Documentation
- docs(resolver): document imperative factory registration and file-based debug log (`e02ef02`)

### Chores
- chore: bump to 1.1.21 — shot drop fix (`1f9deb0`)

## v1.1.20 — 2026-04-29

### Fixes
- fix(resolver): register Ar_ResolverFactory via standard CRT init, not AR_DEFINE_RESOLVER (`fbbdae2`)

### Chores
- chore: bump to 1.1.20 (`0ecf3b9`)

## v1.1.19 — 2026-04-29

### Fixes
- fix(resolver): force-link C++ shim so AR_DEFINE_RESOLVER static init survives MSVC /OPT:REF (`f6c557b`)

### Chores
- chore: bump to 1.1.19 (`5b7f71f`)

## v1.1.18 — 2026-04-29

### Chores
- chore: bump to 1.1.18 (`0907a1f`)

### Other Changes
- debug(resolver): add TH_RESOLVER_DEBUG stderr trace at every ArResolver override (`e5b8223`)

## v1.1.17 — 2026-04-29

### Fixes
- fix(hdas): resolve entity URIs to filesystem paths before writing sublayer parms (`ed2bafc`)

### Refactors
- refactor(scene): route get_root_layer_path through get_root_layer_file_name (`1c3d258`)
- refactor(paths): centralize root-layer filename, drop unused export-file wrappers (`8cf707e`)

### Chores
- chore: bump to 1.1.17 (`1910b30`)

## v1.1.16 — 2026-04-28

### Fixes
- fix(asset-browser): treat TH_PIPELINE_PATH as hpm-owned, not per-project (`6a2dd60`)

### Chores
- chore: bump to 1.1.16 (`25a4a5d`)
- chore: drop dead /target/ ignore (handled by resolver-src/.gitignore) (`92054b6`)
- chore: ignore .claude/ (per-developer Claude Code harness state) (`c8c3696`)

## v1.1.15 — 2026-04-28

### Fixes
- fix(ci): pass PEM directly to hpm pack --key (v0.9.1 contract) (`f5a4d0a`)

### Chores
- chore: ignore .mcp.json (per-developer MCP config) (`e131898`)

## v1.1.14 — 2026-04-28

### Fixes
- fix(release): ship resolver/ in archive via hpm v0.9.1 native semantics (`b413395`)

## v1.1.12 — 2026-04-28

### Fixes
- fix(resolver): register via hpm.toml env, flatten install layout (`e2b9e7d`)

### Refactors
- refactor(hdas): import_asset/import_layer set sublayer parms to entity URIs (`9666672`)

### Chores
- chore: anchor /resolver/ ignore and add /target/ (`9ca63d3`)
- chore: bump to 1.1.12 to supersede broken 1.1.11 release (`b78b285`)

## v1.1.11 — 2026-04-27

### Fixes
- fix(hda): fix active_variant off-by-one in create_asset_model (`0c6b2aa`)

### Chores
- chore: bump to 1.1.11 — add asset_browser_catalogs to INCLUDE_PATTERNS (`f032134`)

## v1.1.10 — 2026-04-23

### Fixes
- fix(resolver): defer pxr.Ar import so pythonrc can register TumbleResolver (`de9bae2`)

### Chores
- chore: bump to 1.1.10 for resolver registration fix (`7639f37`)

## v1.1.9 — 2026-04-23

### Features
- feat(asset_browser): ship TumblePipe catalog as external catalog (`75f8c8e`)
- feat(desktop): add TumblePipe Solaris desktop layout (`a76afd4`)
- feat(hda): update create_asset_lookdev and export_asset (`200dfcb`)
- feat(hda): update create_asset_model, create_asset_lookdev, and export_asset (`7edaa9a`)
- feat(hda): import asset_thumbnail and export_asset HDAs from RND (`8a429c3`)
- feat(hda): update create_asset_lookdev active_variant and add sublayer parm (`866413e`)
- feat(hda): refactor create_asset_model variant fetch to use context variable (`ee19c8e`)

### Fixes
- fix(ci): use GET /v1/storage list to resolve storage public URL (`522d8c9`)
- fix(ci): update release script to use creator storage API (`38acfd7`)
- fix(ci): update storage presign URL to new /v1/storage/{id}/upload route (`8681b46`)
- fix(ci): remove binary HDAs from otls — only decompiled dirs should be tracked (`4e22907`)
- fix(hda): remove accidentally saved subnet contents from create_asset_lookdev (`0e8f3e0`)
- fix(hda): remove auto-layout on variant add in create_asset_lookdev (`151b144`)

### Chores
- chore: ignore compiled .hda files and backup dir in otls/ (`cc6544a`)
- chore: bump version to 1.1.9 (`4b0faf0`)

## v1.1.8 — 2026-04-22

### Fixes
- fix: normalize HDA icon paths to $TH_PIPELINE_PATH/resources (`5e16d3c`)

### Documentation
- docs: trim README to point at readthedocs for deep content (`2e16b70`)
- docs: add Sphinx docs scaffold and Read the Docs config (`b1f343c`)

### Chores
- chore: point hpm.toml documentation at readthedocs (`1b37388`)

## v1.1.7 — 2026-04-19

### Fixes
- fix(ci): scan Rust staticlib (not DLL) for FFI symbols on Windows (`a07fc81`)

## v1.1.6 — 2026-04-17

### Fixes
- fix: inject MSVC /WHOLEARCHIVE via LINK_FLAGS so MSBuild passes it through (`a24e6e6`)
- fix: cross-compile Rust to match C++ target arch, use tested whole-archive form (`e74e4be`)
- fix: preserve TumbleResolver plugin registration across linker strip (`c66d541`)
- fix: set default TumblePipe desktop from uiready.py, not 123.py (`496e483`)
- fix: publish_to_tumblepipe also skips HDA compile + guard for mirror (`d7ac389`)

### Documentation
- docs: update resolver build notes for per-platform CI and symbol-preservation fix (`e79739c`)
- docs: add TumblePipe Deadline job submission example (`166e0b4`)
- docs: recommend TumbleTrove Desktop as primary install method (`86ed713`)
- docs: add TumbleTrove Desktop as an install option (`2b5e649`)
- docs: link HPM to hpm.readthedocs.io (`010eba8`)
- docs: update README for current HPM package structure (`1884985`)
- docs: mirror text-based HDA sources to github, not compiled binaries (`36e9bd0`)
- docs: restore original README.md from v1.0.2 (`9246c02`)

### Chores
- chore: remove DJV render viewer and opentimelineio dependency (`c4d881b`)

## v1.1.5 — 2026-04-17

### Fixes
- fix(build): feed hpm the 32-byte raw Ed25519 seed, not the PEM (`f256e55`)

### Other Changes
- Add Apache-2.0 LICENSE + include it in staged package (`6ae959c`)

## v1.1.4 — 2026-04-16

_No user-facing changes._

## v1.1.3 — 2026-04-16

### Fixes
- fix(build): find hotl under C:\Houdini* (non-default install path) (`3405c70`)

## v1.1.2 — 2026-04-15

_No user-facing changes._

## v1.1.1 — 2026-04-15

_No user-facing changes._

## v1.1.0 — 2026-04-15

### Features
- feat: move asset creation HDAs from Tumblehead into TumblePipe (`8adddb9`)
- feat: cutover to Rust-based tumbleResolver (`d343c78`)
- feat: C++ ArResolver shim + CMake build for tumbleResolver (`8f7572e`)
- feat: Rust core for entity:// USD asset resolver (`9419d7b`)
- feat: declare macos-arm64 as a supported native platform (`08eecb3`)
- feat: set TumblePipe desktop as default on Houdini load (`13424bc`)
- feat: initial TumblePipe Houdini package (`52df04b`)

### Fixes
- fix(release): curl PUT — disable Expect/chunked/default CT, log URL (`98e31ac`)
- fix(release): only send Content-Type header if it is signed (`f64f17b`)
- fix(release): upload archive via curl, not urllib (`11ecbf2`)
- fix(release): resolve creator/slug to package id via /v1/creator/packages (`69c8c74`)
- fix(release): send package path with literal slash, not %2F (`7dd72d3`)
- fix(release): parse hpm pack JSON out of mixed stdout (`3d110e8`)
- fix(release): rename macos slot macos-universal, add missing README (`61f8337`)
- fix(release): surface hpm pack stderr on failure (`bdd3372`)
- fix(release): accept PKCS#8 Ed25519 keys with public-key attribute (`4fe8fcf`)
- fix(resolver): skip strip on macos; link Houdini python on windows (`25c2b58`)
- fix(resolver): link USD libs explicitly on windows, defer on macos (`23f1a17`)
- fix(resolver): stub _OpenAssetForWrite; widen windows tool discovery (`d18a0ce`)
- fix(resolver): use Houdini CMake package instead of bare pxr (`98f7cee`)

### Refactors
- refactor: migrate metadata from /_METADATA prims to customData on scene prims (`e05164f`)
- refactor: rename Tumblehead desktops to TumblePipe (`3cd6a87`)

### Chores
- chore: add .gitignore and remove tracked __pycache__ files (`f024536`)

## v1.0.2 — 2026-03-29

### Chores
- chore: bump version to 1.0.2 (`b888242`)
- chore: bump version to 1.0.1 (`bc23246`)
- chore: bump version to 1.0.1 (`d1d594e`)
- chore: bump version to 1.0.1 (`dfefad7`)
- chore: bump version to 1.0.1 (`6f90d9d`)
- chore: bump version to 1.0.1 (`1f235c6`)
- chore: bump version to 1.0.1 (`fb2a37d`)

### Other Changes
- Update TumblePipe framework from 42282b7f (`1a9822d`)
- Update TumblePipe framework from 42282b7f (`0fafd9a`)
- Update TumblePipe framework from 42282b7f (`725d4e6`)
- Update TumblePipe framework from 42282b7f (`b4c305c`)
- Update TumblePipe framework from 1b09f532 (`73c563d`)
- Update TumblePipe framework from 1b09f532 (`bbf8b8b`)
- Update TumblePipe framework from 1b09f532 (`85d9225`)
- Update TumblePipe framework from 1b09f532 (`de77384`)
- Update TumblePipe framework from 1b09f532 (`0a2691d`)
- Update TumblePipe framework from 1b09f532 (`d9ac61e`)
- Update TumblePipe framework from 1b09f532 (`8ddcf40`)
- Update TumblePipe framework from 1b09f532 (`e3cc8e3`)
- Update TumblePipe framework from 1b09f532 (`d2b56ca`)
- Update TumblePipe framework from 1b09f532 (`fa9b46c`)
- Update TumblePipe framework from 1b09f532 (`16dfc93`)
- Update TumblePipe framework from 1b09f532 (`2fb5a53`)
- Update TumblePipe framework from 1b09f532 (`46d6431`)
- Update TumblePipe framework from 1b09f532 (`3e9de18`)
- Update TumblePipe framework from 1b09f532 (`f65ae4a`)
- Update TumblePipe framework from 1b09f532 (`ad1ad1a`)
- Update TumblePipe framework from 1b09f532 (`d844eca`)
- Update TumblePipe framework from 1b09f532 (`63273fd`)
- Update TumblePipe framework from 3359f890 (`8f00758`)
- Update TumblePipe framework from 3359f890 (`5197f44`)
- Update TumblePipe framework from d4f73dc9 (`46d30b3`)
- Update TumblePipe framework from d4f73dc9 (`dc821aa`)
- Update TumblePipe framework from d4f73dc9 (`07974a3`)
- Update TumblePipe framework from d4f73dc9 (`1dc4d98`)
- Update TumblePipe framework from d4f73dc9 (`6653d71`)
- Update TumblePipe framework from d4f73dc9 (`99424e1`)
- Update TumblePipe framework from d4f73dc9 (`5dc3e2c`)

## v1.0.1 — 2026-01-25

### Features
- feat: add weekly automated release workflow and HPM manifest (`c80c2f8`)

### Chores
- chore: bump version to 1.0.1 (`b9d7595`)
- chore: bump version to 1.1.1 (`bb00c67`)
- chore: bump version to 1.1.0 (`7706f47`)

### Other Changes
- Update TumblePipe framework (`4b7c972`)
- Update TumblePipe framework (`9d89deb`)
- Update TumblePipe framework (`76c06bc`)
- Update TumblePipe framework (`fc267a8`)
- Update TumblePipe framework (`4310c82`)
- Update TumblePipe framework (`ffa4f51`)
- Update TumblePipe framework (`1629134`)
- Update TumblePipe framework (`1cd7ad9`)
- Update TumblePipe framework (`5fbd801`)
- Update TumblePipe framework (`80ae90f`)
- Update TumblePipe framework (`2198508`)
- Update TumblePipe framework (`5d02864`)
- Update TumblePipe framework (`f7bceec`)
- Update TumblePipe framework (`f667ff6`)

## v0.2.0 — 2025-11-11

### Other Changes
- Update example config to match Growth project patterns (`7240901`)
- Expand README with Prerequisites and Render Farm sections (`cbe5cc0`)
- Restore examples directory (`34ce63c`)
- Update TumblePipe framework (`320e403`)
- Update TumblePipe framework (`4b61a79`)
- Update Windows launcher for Houdini 21.0 (`79baf9d`)
- Merge pull request #6 from tumblehead/prepare_release (`6661a33`)
- Merge pull request #5 from tumblehead/prepare_release (`e72a7c3`)
- Merge pull request #4 from tumblehead/prepare_release (`62bf503`)
- Merge pull request #3 from tumblehead:prepare_release (`ebe4ce9`)

## v0.1.3 — 2025-05-20

### Other Changes
- fix various bugs (`2033138`)
- fix sync override (`8b83442`)

## v0.1.2 — 2025-05-15

### Fixes
- fix: update archive naming to use OS instead of platform (`778039f`)
- fix: update archive naming to use OS instead of platform (`9e8f3b0`)

### Other Changes
- Merge pull request #4 from tumblehead/prepare_release (`62bf503`)
- prepare v.0.1.2 (`996cb24`)
- Merge pull request #3 from tumblehead:prepare_release (`ebe4ce9`)
- Merge branch 'prepare_release' of https://github.com/tumblehead/TumblePipe into prepare_release (`ed5950f`)

## v0.1.1 — 2025-05-02

### Fixes
- fix: update download archives step to use merge-multiple option (`cde58ea`)
- fix: add merge option for downloading archives in release workflow (`0435141`)
- fix: remove .distignore and add .gitattributes for export-ignore configuration (`a08080e`)
- fix: update download archives step to use merge-multiple option (`82f8186`)
- fix: add merge option for downloading archives in release workflow (`58f15a9`)
- fix: remove .distignore and add .gitattributes for export-ignore configuration (`e88c42c`)

### Other Changes
- Merge pull request #2 from tumblehead/prepare_release (`21e88e4`)
- Merge branch 'prepare_release' of https://github.com/tumblehead/TumblePipe into prepare_release (`02e4eb9`)

## v0.1.0 — 2025-05-02

### Fixes
- fix: update step names for clarity in build workflow (`5a572db`)
- fix: replace zip build action with git archive command (`f3e5608`)
- fix: update action version for building zip artifact (`8a37f93`)
- fix: update setup-uv action reference in build workflow (`272474a`)

### Other Changes
- Merge pull request #1 from tumblehead/prepare_release (`bcee49e`)
- add prepare_release branch to build trigger (`05967f6`)
- update the readme (`d64baee`)
- add all the files (`071f05d`)
- Initial commit (`0420265`)
