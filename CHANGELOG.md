# Changelog

All notable changes to TumblePipe, one section per release.

**This file is generated — do not edit it by hand.** It is derived from the
conventional-commit subjects between release tags. To change an entry, the
commit subject is the source of truth. Regenerate with:

```bash
uv run --no-project python scripts/generate_changelog.py
```

CI and build commits are omitted deliberately.

## v1.69.1 — 2026-10-10

### Fixes
- fix(farm): denoise on the CPU so denoised AOVs keep their precision (`9ff5021a`)

### Documentation
- docs: the denoise runs OIDN on the CPU (`4296c85c`)
- docs: regenerate CHANGELOG.md for v1.69.0 (`584fcf4e`)

## v1.69.0 — 2026-10-10

### Features
- feat(submit): cancel an in-progress farm submission (`ed2f964b`)
- feat(submit): show the render camera in the Farm Submit dialog (`74bf2ba1`)

### Fixes
- fix(hda): th::render_vars emission AOV renders instead of stopping Karma (`78aa110c`)
- fix(submit): samples, motion blur and DOF override the scene only when set (`5a99183c`)

### Documentation
- docs: cancelling a farm submission from the Farm submission window (`f702f9ea`)
- docs: submit overrides apply only when set; the dialog shows the render camera (`2042c2e3`)
- docs: regenerate CHANGELOG.md for v1.68.2 (`0b07ae8f`)

## v1.68.2 — 2026-10-10

### Fixes
- fix(submit): each entity renders only the checked channels it defines (`791409eb`)
- fix(farm): run the publish chain's builds and collapse at publish priority (`e7a04522`)
- fix(farm): refuse a render channel the entity does not define (`e3f08bc1`)
- fix(build): fail a channel build when no department exported the channel (`83d00342`)

### Documentation
- docs: render channels are narrowed per entity; the farm refuses the rest (`0c50c4e7`)
- docs: regenerate CHANGELOG.md for v1.68.1 (`cfbf9a42`)

## v1.68.1 — 2026-10-09

### Fixes
- fix(farm): the slapcomp reads each channel at its own version (`a16201f0`)
- fix(slapcomp): pin default to the bottom of the channel stack (`9c45fc74`)

### Refactors
- refactor(slapcomp): stack in the channel list's order, no default pin (`eff4013b`)

### Documentation
- docs: render channels keep their own versions (`758c0703`)
- docs: add render-layer channels bottom to top (`da41229f`)
- docs: regenerate CHANGELOG.md for v1.68.0 (`9dac1904`)

## v1.68.0 — 2026-10-09

### Breaking Changes
- feat(render)!: the beauty is RGBA; drop the separate alpha AOV (`a4cba6b7`)

### Features
- feat(farm,comp): take the alpha from the RGBA beauty, keep old renders working (`5c65c4e4`)

### Fixes
- fix(comp): Update re-points planes and the alpha; slapcomp finds A by name (`86e8af3c`)
- fix(config): migration v10 reads USDA structure and refuses what it doesn't know (`3aa62455`)

### Documentation
- docs: Resolve can honour the RGBA beauty's alpha (`4997a156`)
- docs: alpha lives in the RGBA beauty (`19719230`)
- docs: regenerate CHANGELOG.md for v1.67.0 (`04867ad0`)

## v1.67.0 — 2026-10-09

### Breaking Changes
- feat!: remove th::render_layer_setup and the render layer matte recipe (`1fcc474e`)

### Fixes
- fix(publish): the Channel column names every channel a row covers (`ebde8282`)

### Documentation
- docs: publish dialog Channel column; link config to render layers (`c0f1287c`)
- docs: regenerate CHANGELOG.md for v1.66.0 (`0fe31e7c`)

## v1.66.0 — 2026-10-09

### Features
- feat(hda): th::mattes, single-channel mattes and distance ramps for grading (`5503038d`)

### Fixes
- fix(sync): copy ramp_* distance ramps to the edit (`b6266714`)
- fix(hpm): index th::mattes and th::material; test the operator index (`39df5846`)
- fix(denoise,comp): pass mattes and ramps through; single-channel objid_ masks (`a92153be`)

### Documentation
- docs: regenerate CHANGELOG.md for v1.65.0 (`73d5ac7b`)

### Tests
- test(mattes): name rule, multiparm rows, downstream contract, radial nodes (`5bee94c2`)

## v1.65.0 — 2026-10-08

### Features
- feat(hda): per-AOV Compression menus on th::render_vars and th::puzzlemattes (`e1b9e4bc`)
- feat(submit): Assets and Shots tabs in the Farm Submit dialog (`16a64f47`)

### Fixes
- fix(render): the farm stops overriding per-AOV compression (`94023ae8`)

### Documentation
- docs: compression is set on the nodes; HDA editing notes (`65fc1d86`)
- docs: regenerate CHANGELOG.md for v1.64.0 (`26286442`)

## v1.64.0 — 2026-10-08

### Features
- feat(hda): material_assigner exposes Strength per assignment (`0bc1d1d5`)
- feat(hda): create_asset_model ships a starter box and imports subset groups (`2346bd18`)

### Fixes
- fix(render): write data passes with lossless ZIP compression (`c65d1927`)
- fix(farm): survive the mkdir race when tasks share an output folder (`63820c15`)
- fix(render): the partial notify looks for frames under the render channel (`0d934dfa`)

### Documentation
- docs: data-pass compression and today's farm failures (`7147506d`)
- docs: regenerate CHANGELOG.md for v1.63.2 (`9f71a50b`)

## v1.63.2 — 2026-10-08

### Fixes
- fix(publish): skip a department with nothing to publish instead of failing (`a953d7da`)
- fix(farm): bundle each workfile with its own context.json (`0ceb5c8f`)

### Documentation
- docs: regenerate CHANGELOG.md for v1.63.1 (`c419437e`)

## v1.63.1 — 2026-10-07

### Fixes
- fix(catalog): display_name strips only the lowercase 'th ' prefix (`3de62b65`)
- fix(template): drop the /scene prim from the root default layer (`fb37e8ae`)
- fix(staging): a set's shot-sourced entries no longer hide a shot's own asset refs (`3e99f21c`)

### Documentation
- docs: render validators find the settings prim on the stage (`cdc9c1e6`)
- docs: list which render settings the submit dialog can override (`b729fd38`)
- docs: regenerate CHANGELOG.md for v1.63.0 (`05fa3f87`)

### Tests
- test: upgrade the suite to minigun 6.1.0 (`1b37685e`)

## v1.63.0 — 2026-10-07

### Features
- feat(browser): Duplicate an asset under a new name (`902d0511`)

### Fixes
- fix(staging): never track a shot's assets as part of an asset (`266cd6c0`)
- fix(entities): archive a deleted entity's folders; refuse renaming one with files (`f177698d`)
- fix(export): bring library files and their dependencies into the publish (`683e27b3`)
- fix(build_comp): refuse to build an empty comp, and say where renders go (`de6848f7`)
- fix(farm): say "Deadline is not set up" instead of "%DEADLINE_PATH%" (`16677793`)
- fix(import): bake Inline imports into the export instead of an absolute arc (`a34b1829`)

### Documentation
- docs: asset exports and builds leave out assets seen through a shot layer (`b42f9c62`)
- docs: troubleshooting and node pages for the library-publish, delete/duplicate, Deadline and build_comp changes (`70419eee`)
- docs: Inline import mode flattens the import; _staged paths in the escaping-arc error (`e9c6c914`)
- docs(import_shot): say where Preview Procedurals lives and what it needs (`311138b9`)
- docs: regenerate CHANGELOG.md for v1.62.1 (`8763fceb`)

## v1.62.1 — 2026-10-07

### Fixes
- fix(csv_shot_import): count a repeated shot once in a dry run (`59bc8d1f`)
- fix(farm): close validator holes for bools, empty outputs and channel lists (`4af00217`)
- fix(asset_browser): refuse category/sequence name clashes; fix "0mo ago" (`fbf97c14`)
- fix(config_editor): flag added list items and empty containers as changes (`f2d09597`)
- fix(paths): find newer complete layers by stored version name (`5d23661c`)
- fix(graph): terminate recursive queries on cycles; keep invalidate pure (`0cffa4e1`)
- fix(config): stop callers aliasing the cached config tree (`cc141e4b`)
- fix(discord): match channel and user names ignoring case (`9ffe3309`)
- fix(wizard): write JSON escapes the way Python's json.dump does (`c091d792`)

### Documentation
- docs: document the new property modules, cargo, and the fixed behaviours (`ddb8d872`)
- docs: refer to issues under the repos' new owners (`77064ad8`)
- docs: regenerate CHANGELOG.md for v1.62.0 (`6bbf54cc`)

### Tests
- test: make existing properties reach their checks (`2c26e244`)
- test(stubs): give the headless hou applicationVersionString (`5bf5878c`)
- test(resolver): proptest properties and a Python writer parity check (`ce05e393`)

## v1.62.0 — 2026-10-06

### Features
- feat(import_shot): Exclude "Nothing" mode loads every department (`a2e8fb58`)

### Documentation
- docs: regenerate CHANGELOG.md for v1.61.0 (`88e42c2a`)

## v1.61.0 — 2026-10-05

### Features
- feat(settings): split the Projects page by who a change reaches; pool for admins (`a16c5772`)

### Documentation
- docs: regenerate CHANGELOG.md for v1.60.0 (`685ca210`)

## v1.60.0 — 2026-10-05

### Features
- feat(import-assets): per-row USD Variant menu (`87dd1bb7`)
- feat(import-shot): Preview Procedurals toggle runs houdinipreviewprocedurals (`12c9382f`)

### Fixes
- fix(farm): rename copy-to-edit task to sync; composites honour copy_to_edit (`b2e188c5`)

### Documentation
- docs(design): note the import_assets Variant menu against §4.2 (`8d67f832`)
- docs(otls): updateFromNode on an installed source dir rewrites the repo (`bbc67da2`)
- docs(context): stock radials sit outside the package layer, now that contributions are declared (`977de214`)
- docs: release CI runs on version tags only; keep master linear (`5da3fb3f`)
- docs: regenerate CHANGELOG.md for v1.59.0 (`5be67d9b`)

### Tests
- test(import-assets): verify harness for the USD Variant menu (`6d471fef`)

## v1.59.0 — 2026-10-02

### Features
- feat(context): tell TumbleTrove the workfile's department, ship stock department radials (`2bb4ac79`)

### Documentation
- docs: a worker whose SideFX login lapsed fails every job (`9615e530`)
- docs: regenerate CHANGELOG.md for v1.58.0 (`2bd66618`)

### Tests
- test: one scratch root per run, deleted at exit (`20f227a5`)

### Other Changes
- Merge branch 'feat/workfile-context-provider' (`f524dade`)

## v1.58.0 — 2026-09-30

### Features
- feat(asset_browser): Nodes section lists every pipeline HDA as a card (`beba24fc`)

### Fixes
- fix(playblast): submit farm playblasts to the karma group (`370c3738`)
- fix(playblast): render farm playblasts with Karma XPU only (`28cea78a`)
- fix(asset_browser): Root Open Location URI (`12c7e50a`)
- fix(asset_browser): Multi version dropdown lists every version (`3164e570`)
- fix(import_layer): stop authoring /_METADATA; shot validator ignores HoudiniLayerInfo (`6128bae7`)
- fix(import_assets): placements made with the edit state now publish (`f46c9d9c`)

### Documentation
- docs: finding and creating nodes from the Asset Browser (`ef71859d`)
- docs: farm playblasts render with Karma XPU, Storm removed (`52b89d8c`)
- docs: Multi version dropdown and Open Location troubleshooting (`84107dff`)
- docs: shot_root_prims legacy /_METADATA warning and HoudiniLayerInfo (`61c4d856`)
- docs: import_assets placements publish; moving a node inside an HDA (`ff9929e9`)
- docs: regenerate CHANGELOG.md for v1.57.0 (`ebf1136b`)

### Tests
- test: Nodes section properties (`ce9b1d33`)
- test: Multi version open and Multi/Root Open Location properties (`8d3833e1`)

## v1.57.0 — 2026-09-29

### Features
- feat(asset-browser): noted versions stand out in Open Version (`bb54d280`)
- feat(import_shot): an Exclude menu, so upstream departments can see downstream (`e6c74a46`)
- feat(farm): playblasts can render with Karma XPU (`5d6a044f`)

### Fixes
- fix(import_shot): resolve Department from_context inside a Multi (`310e5579`)
- fix(farm): farm playblasts take a Karma licence, not a Houdini seat (`b91372cd`)

### Documentation
- docs: the comment icon on noted versions (`af1703d9`)
- docs: playblast licences, the XPU engine, and the Storm hang (`8741b887`)
- docs: regenerate CHANGELOG.md for v1.56.1 (`ae06f6e2`)

## v1.56.1 — 2026-09-28

### Fixes
- fix(farm): the submission window's progress bar carries the summary; Hide becomes Close (`f1642615`)

### Documentation
- docs: the submission window's summary bar and Close button (`994af948`)
- docs: regenerate CHANGELOG.md for v1.56.0 (`d78197d5`)

## v1.56.0 — 2026-09-28

### Features
- feat(farm): a Farm Submit confirmation that says what it sends (`90670e12`)

### Fixes
- fix(farm): a farm publish no longer saves a new workfile version (`6bdb77a7`)
- fix(farm): bundle only a workfile version whose save has finished (`fae902d7`)
- fix(houdini): network thumbnails do nothing without a UI instead of raising (`d54a0237`)
- fix(export): an uncookable input is a failure that quotes the cook error (`b8f5a58e`)
- fix(farm): farm jobs install the project's packages, not TumblePipe alone (`0399ba78`)
- fix(farm): the submission window shows Retry failed only after failures (`a89915ce`)

### Documentation
- docs: farm publishes don't save a workfile; bundles skip half-saved versions (`0000e4dd`)
- docs: farm jobs carry the project's packages; the uncookable-input error (`cf4d0425`)
- docs: the Farm Submit confirmation and the submission window's layout (`fa7640a4`)
- docs: regenerate CHANGELOG.md for v1.55.0 (`d88c8ce6`)

## v1.55.0 — 2026-09-28

### Features
- feat(farm): Farm Submit opens anywhere, ticks the workfile, shows only live settings (`a9be7527`)

### Fixes
- fix(farm): the Farm Submit legend names the dim ○ (`83163e14`)
- fix(farm): a closed Farm Submit dialog is deleted (`bc4bdaf3`)
- fix(farm): Farm Submit from an empty Multi's workfile opens instead of erroring (`d1acc482`)
- fix(farm): the Desktop launcher blanks package variables nothing sets (`3877bf95`)
- fix(farm): Select stale skips members a Multi's workfile never exported (`16e988f4`)
- fix(farm): the Farm Submit column header checkboxes respond to clicks (`f48cf5b4`)
- fix(hpm): migrate-config script resolves via $HPM_PACKAGE_ROOT (`3748edb9`)

### Documentation
- docs: the dim ○ of a Multi that never exported a member (`ae10c5b8`)
- docs: Farm Submit opens without a workfile, ticks it with one, and hides idle settings (`51acfe02`)

## v1.54.2 — 2026-09-25

### Documentation
- docs: Farm Submit shipped in 1.54.1; the macOS worker builds arm64 natively (`ad22d7c6`)
- docs: regenerate CHANGELOG.md for v1.54.1 (`df007b08`)

## v1.54.1 — 2026-09-24

### Documentation
- docs: regenerate CHANGELOG.md for v1.54.0 (`9260d3db`)

## v1.54.0 — 2026-09-24

### Features
- feat(farm): call it Farm Submit, and drop the title before the filter (`8af0f47b`)
- feat(farm): open the Farm grid from TumbleTrove Desktop, without Houdini (`fc0beced`)
- feat(farm): the Farm grid replaces Submit Jobs, submitting in the background (`1b9f1188`)

### Fixes
- fix(farm): the Farm dialog's scrollbars are visible (`f31dee9d`)
- fix(farm): scrolling the settings panel no longer edits and pins fields (`04f0835e`)
- fix(farm): an unconfigured preview cut composes the whole shot (`9f468711`)
- fix(farm): the Farm dialog fits its headers and settings (`a1d2b3e5`)
- fix(farm): previews after a publish show what the submission published (`281c493e`)

### Documentation
- docs: Farm Submit's live test, the wheel guard and the harness's check 11 (`e97a8c29`)
- docs: the Farm dialog, farm-side preview snapshots and the Desktop button (`b14da3f3`)
- docs(design): farm pipeline grid for publish, playblast and render (`c1f40e4f`)
- docs: regenerate CHANGELOG.md for v1.53.0 (`a03689db`)

## v1.53.0 — 2026-09-23

### Features
- feat(asset-browser): Open Version submenu on department deck items (`9fa9d4cb`)

### Documentation
- docs: the TumbleTrove menu gives each package a submenu (`0de94555`)
- docs: Open Version on the department right-click menu (`0c61c29f`)
- docs: regenerate CHANGELOG.md for v1.52.3 (`b82e9701`)

## v1.52.3 — 2026-09-23

### Fixes
- fix(playblast): pick the shot with an Entity parm, as import_shot does (`dcfafc34`)
- fix(apps): in-session tools use the Houdini that is running (`13f4da8e`)

### Documentation
- docs: playblast Entity parm, in-session encode failure, camera lookup (`031be189`)

## v1.52.2 — 2026-09-22

### Features
- feat(playblast): husk's own decisions reach the farm log (`542775ea`)

### Fixes
- fix(notify): a project with no discord configuration skips, not fails (`9e397132`)
- fix(playblast): find cameras wherever the project keeps them (`9701d90a`)
- fix(validators): the render validators ask the stage where its settings live (`8b9a7c30`)
- fix(playblast): render through settings husk can find and Storm can fill (`9567fea1`)
- fix(submit): give the entity tree the dialog's left column (`21e87479`)
- fix(nodes): turn a group URI away wherever a workfile resolves an entity (`9cd8895f`)
- fix(playblast): a Multi's workfile resolves no shot, not a traceback (`7c839683`)

### Documentation
- docs: what a farm notify does when the project has no discord config (`850d6560`)
- docs: husk finds render settings under /Render, or on the root layer (`4e94077a`)
- docs: from_context resolves nothing in a Multi's workfile (`f8b5bb6d`)
- docs: regenerate CHANGELOG.md for v1.52.1 (`59089676`)

### Tests
- test: pin the stage a farm playblast renders (`a41910fc`)

### Other Changes
- tools: report which projects can actually post a farm notify (`4cc35bd0`)
- tools: report what a farm playblast will render, without submitting one (`a663c777`)

## v1.52.1 — 2026-09-17

### Fixes
- fix(workfiles): record the Multi, not the member, in a Multi folder's context.json (`25ce1a78`)
- fix(import_shot): restore excluding asset departments, as layer mutes (`69ec5772`)

### Documentation
- docs: a Multi workfile whose context.json names one member (`a30dfabf`)
- docs: import_shot asset department exclusion (`d68d9f4c`)
- docs: regenerate CHANGELOG.md for v1.52.0 (`e672712b`)

## v1.52.0 — 2026-09-16

### Features
- feat(submit-jobs): forms on the left, entity tree on the right (`1f85a060`)

### Fixes
- fix(publish): collect export_rig nodes in an asset Multi's rig workfile (`5de2a406`)
- fix(submit-jobs): a Multi submits its member shots, not itself (`f9c255b9`)
- fix(browser): a recipe saves one network's nodes; document node-drop saves (`b3524458`)
- fix(browser): Refresh no longer empties the Recipes sidebar section (`01810826`)

### Documentation
- docs: rig exports from an asset Multi (`35a8f377`)
- docs: regenerate CHANGELOG.md for v1.51.0 (`2524589a`)

## v1.51.0 — 2026-09-16

### Features
- feat(config): refresh department templates again at migration v9 (`5edacb33`)
- feat(templates): enumerate points after promote_name in the rig template (`af0a552b`)
- feat(templates): promote the model's name attribute to points in the rig template (`deea8014`)
- feat(browser): recipes are a project entity type beside assets and shots (`86ffc25b`)

### Fixes
- fix(templates): animation scene invoke outputs unpacked geometry (`df071b33`)
- fix(browser): New from Current moves the scene's nodes to the new context (`68250f1c`)
- fix(farm): publish forces standalone import_asset and import_rig to latest too (`f67b9b75`)
- fix(browser): refresh import nodes on every scene load, not only Browser opens (`5659efdd`)
- fix(import nodes): un-bypass import_assets, import_rigs and import_shot on success (`7b91b879`)
- fix(import_layer): load the pinned version even when latest mode was left on (`1a96c898`)
- fix(process-dialog): paint the Execute button and end a clean run on a green Done (`ef0bbc65`)
- fix(otls): clear project-specific paths baked into HDA sources (`2934f6f4`)

### Documentation
- docs: th::animate scene invoke settings and the empty Tab-menu dive (`83c215b3`)
- docs: New from Current moves pinned nodes to the new context (`5fd0b4ab`)
- docs: radial-menus points at project recipes, not the old network catalog (`38d10db6`)
- docs: process dialog footer states (green Done, Close plus retry) (`1d8a0680`)
- docs: HDAs remember the project they were saved in; hdapaths stage (`a899f2a3`)
- docs: regenerate CHANGELOG.md for v1.50.0 (`2f85b41c`)

## v1.50.0 — 2026-09-15

### Features
- feat(browser): empty department card leads with New from Template and creates it on double-click (`728d2a3d`)

### Documentation
- docs: department menus read New from Template / New from Current / Open Folder (`a5f4ac96`)
- docs: regenerate CHANGELOG.md for v1.49.0 (`3ba63a92`)

## v1.49.0 — 2026-09-14

### Features
- feat(desktop): open the TumblePipe desktop with its bars expanded (`d4e5e18d`)

### Fixes
- fix(desktop): show the viewer's color correction toolbar by default (`d785784e`)
- fix(hdas): U from the MODEL and LOOKDEV dive targets leaves the node (`cd564f61`)
- fix(browser): hide the Multis subheader until its context has a Multi (`d3992dc3`)
- fix(browser): New: Template opens the network editor on /stage, not /obj (`5fe335d4`)
- fix(startup): bind the COP and VOP radial menus with Radial 0.4.0's bind() (`e83297e4`)
- fix(browser): LOP drops stop duplicating nodes and get their thumbnail back (`572d1300`)

### Documentation
- docs: the TumblePipe viewer opens with its color correction toolbar (`724176a8`)
- docs: the TumblePipe desktop opens with its bars expanded (`50d26ec9`)
- docs: MODEL and LOOKDEV dive targets are their only editable networks (`20fb6450`)
- docs: the Multis subheader shows only once a context has a Multi (`b4fcd354`)
- docs: New: Template lands the network editor on /stage (`36b14caf`)
- docs: COP/VOP radial menus follow Radial's key; catalogue the new tests (`ef9c4f03`)
- docs(browser): LOP drops set no render flag; document drop thumbnails (`f34e7227`)
- docs: regenerate CHANGELOG.md for v1.48.0 (`68d393e2`)

## v1.48.0 — 2026-09-14

### Features
- feat(otls): add th::material LOP HDA (`c40178a2`)

### Fixes
- fix(browser): creation options follow the scope, not only type tags (`0b846649`)
- fix(browser): refuse to create or edit an entity the config does not hold (`93b47595`)
- fix(publish): say why an export found no tasks; Multis get downstream departments (`6f9a5ff4`)

### Documentation
- docs(browser): one action list per object across views and sidebar (`3c97130a`)
- docs: why an export finds no tasks, and the entity-not-registered refusals (`feb5c847`)
- docs: regenerate CHANGELOG.md for v1.47.1 (`bd258132`)

### Tests
- test: headless qtpy stub, so pipe/houdini/ui is importable at all (`0e064517`)

## v1.47.1 — 2026-09-09

### Fixes
- fix(asset-browser): dropping a department deck item into a LOP works again (`9f5d04af`)
- fix(asset-browser): the Submit Jobs dialog opens again (`c86aa841`)
- fix(catalog): the right pane follows the grid selection again (`2eec5dbe`)

### Refactors
- refactor(asset-browser): delete an override of a hook nobody calls (`a05de749`)

### Documentation
- docs: the host-contract section, rewritten around what it actually cost (`ab97d91c`)
- docs(development): the base Catalog is not the whole host contract (`fb1e478c`)
- docs: name the package, not the retired asset_browser_catalogs/ dir (`bd87fefb`)
- docs: the right pane is the detail panel, in all four places that said otherwise (`21097b6c`)
- docs: regenerate CHANGELOG.md for v1.47.0 (`04195d14`)

### Tests
- test(catalog): scan the whole SDK package, not a list of host directories (`baf52783`)
- test(catalog): scan core/ for catalog reach-ins too, not just ui/ (`2afe151c`)
- test(catalog): every attribute the browser reaches on a catalog exists (`d264ab06`)
- test(catalog): no module in the package may be loaded by file path (`bc746e32`)
- test(catalog): pin the open scene, and the pane that has to show it (`938b0a93`)

## v1.47.0 — 2026-09-08

### Fixes
- fix(hda): export_layer hides the inner USD ROP's folder (the "Save to Disk" that did nothing) (`75b58d44`)
- fix(hda): image_plane_painter ships its Krita template instead of reading W:/_pipeline (`3ee464e9`)
- fix(hda): karmafogbox shows its Emission parms (`b51b3e7f`)
- fix(hda): create_model strips its Import Path Prefix, not the reference prim path (`5d6de09c`)
- fix(hda): material_assigner "Update Materials Paths" no longer NameErrors on a missing material (`c44ba59f`)
- fix(logging): warnings and above always reach the process console (`e77ffd14`)
- fix(asset-browser): disk-touching handlers resolve the entity from a READY client (`eb28c6c2`)
- fix(context): a stale extension hint no longer hides the hip on disk; repair corrects it (`0e511fad`)
- fix(workfiles): record the hip extension Houdini actually wrote; Education saves .hipnc (`f75874fe`)

### Documentation
- docs: console echo in "Where the logs are"; test_logging in the tests README (`6adf393a`)
- docs: regenerate CHANGELOG.md for v1.46.0 (`9291efb5`)

### Tests
- test(context): pin the licence-driven extension rewrite, stale-hint reading, and repair (`6fe1fc6d`)

## v1.46.0 — 2026-09-08

### Features
- feat(docs): ship the user documentation offline through TumbleTrove (requires TumbleTrove 0.27.0) (`874bf3b0`)
- feat(catalog): a Multi is an entity with department rows, not a folder of shots (`22c7d0bc`)

### Fixes
- fix(workfiles): creating a Multi workfile from a department row raised AttributeError (`1a530ee6`)
- fix(catalog): a container card's detail panel offers only the actions that work on it (`bf1a529d`)
- fix(catalog): every host surface on a Multi or Root id answers, and is pinned (`1b70b894`)
- fix(catalog): fail visibly when the launch project is not configured (`949d2b10`)
- fix(tt_prepare): find the project from its manifest, and say "not configured" instead of "out of date" (`bccb3d6f`)
- fix(catalog): route delete_entity on a Multi or Root card id to delete_collection (`05272d34`)

### Documentation
- docs: how a Multi workfile is created, and the wiring gate (`10a22e70`)
- docs(containers): describe the container card's click model as it is now (`794ce31a`)
- docs: hook README on where tt_prepare finds the project, tests README on the two new catalog modules (`7858e1a5`)
- docs(configuration): the not-configured notice, and how tt_prepare finds the project (`9dd6d396`)
- docs: regenerate CHANGELOG.md for v1.45.1 (`360e5f11`)

### Tests
- test(docs): render once per process and clean up; describe test_docs_tree (`303b43cb`)

## v1.45.1 — 2026-09-07

### Fixes
- fix(scripts): import the submit-jobs dialog as the package module it is (`1afc6765`)
- fix(catalog): claim Multi and Root collections so the browser can act on them (`c8f923e2`)

### Documentation
- docs: document Multis, retire the asset_browser_catalogs references (`f98e9e44`)

### Tests
- test(catalog): run the catalog against the shipped SDK, pin the Multi lifecycle (`fa8c1ff9`)

## v1.45.0 — 2026-09-07

### Features
- feat(catalog): declare the pipeline catalog to TumbleTrove (`b4ada480`)

### Fixes
- fix(startup): drop the package.install() call (`6f191218`)
- fix(lops): resolve from_context in output_modified_prims (`988e3334`)
- fix(hda): create_asset_lookdev published no materials out of the box (`95d7519a`)
- fix(import_layer): stop reporting "Imported" over an empty stage (`38c1505a`)
- fix(hda): re-anchor absolute import paths instead of escaping the geo scope (`64daa45a`)
- fix(hda): asset_payload composed under the asset and shared one sidecar (`96fc5dbc`)
- fix(release): range the notes from the last RELEASED version, not the last tag (`99c664a6`)

### Refactors
- refactor(catalog): move the catalog into the package it belongs to (`27dcda8b`)
- refactor(catalog): declare a settings page instead of a settings widget (`699ccfa1`)

### Documentation
- docs: correct composition.md where this session proved it wrong (`db5fb30d`)
- docs: cover the superseded list and what a release's notes actually span (`65b21fe1`)
- docs: regenerate CHANGELOG.md for v1.44.2 (`a9063210`)

## v1.44.2 — 2026-09-03

### Documentation
- docs: regenerate CHANGELOG.md for v1.44.1 (`7d9eb208`)

## v1.44.1 — 2026-09-03

### Fixes
- fix(ci): resolve the windows build tools through $HOME, not $USERPROFILE (`5a23ecd4`)

### Documentation
- docs: regenerate CHANGELOG.md through v1.44.0 (`7b4b2c60`)

## v1.44.0 — 2026-09-02

### Breaking Changes
- chore(toolbar)!: delete the unshipped shelves and the helpers only they reached (`5ace9b85`)
- fix(config)!: store own properties verbatim instead of diffing against inherited (`74540253`)
- refactor!: drop the unused Qt widget kit (`e215aa59`)
- refactor!: drop the unreachable DaVinci Resolve integration (`1cabd874`)
- refactor!: drop the vestigial Result type (`0ff729e5`)
- refactor!: drop the RenderMan denoiser wrapper (`daa96448`)
- refactor!: drop the dead data/ value objects (`5fc945d6`)

### Features
- feat(submit-jobs): submit the batch through ProcessDialog (`9cfdf164`)
- feat(submit-jobs): tri-state form fields and a pre-flight table (`c4bf4a96`)
- feat(submit-jobs): pure per-entity settings resolver (`74812127`)

### Fixes
- fix(changelog): never announce a commit superseded before it shipped (`75cc7738`)
- fix(catalog): refuse to seed the edit dialog with a guessed frame range (`76706d92`)
- fix(catalog): write only the fields the artist actually changed (`23fc007b`)
- fix(render): find the RenderSettings prim on the stage instead of assuming it (`0bd7a6e9`)
- fix(usd): fail the submission when a staged build's layer is missing (`35aa32b0`)
- fix(context-repair): never delete a reservation claim that is still in flight (`d5618ead`)
- fix(submit): apply pre_roll/post_roll to farm renders (`69899e59`)
- fix(render): reject an empty output_paths instead of publishing nothing (`ea83c444`)
- fix(validators): an unknown validator name must fail, not be skipped (`43c5b21c`)
- fix(composite): honour step_size in the worker instead of compositing every frame (`84b8dda1`)
- fix(validators): stop reporting "no issues found" while holding warnings (`96049c8c`)
- fix(farm): reject bool where a farm config expects an int (`2b97a9e8`)
- fix(scaffold): stamp new projects at the latest migration version (`ff605804`)
- fix(quality-gates): ask git, not the filesystem, for committed binary HDAs (`85ff41bb`)
- fix(farm): require a frame range for render instead of guessing 1001-1100 (`9c138f17`)

### Refactors
- refactor(catalogs): read both channel spellings in the browser catalogs (`eeeef5ab`)
- refactor(farm): read both channel spellings in job configs too (`0b054788`)
- refactor(channels): read both channel spellings instead of refusing one (`6d87c597`)
- refactor(build): rename the in-memory resolver keys, and pin what is not renameable (`1713519b`)
- refactor(composite): read the channel property key from its one definition (`e98989c8`)
- refactor(scene): rename scene.py's private DEFAULT_VARIANT, and pin the wire key (`752e947d`)

### Documentation
- docs: record the caller obligation the Edit dialog bug exposed (`3386f538`)
- docs: say what writers emit, not what is "frozen" (`8530a6db`)
- docs: record the channel-rename prerequisites and the release ordering (`36d10690`)
- docs: reconcile the render and context-chain docs with the fail-loudly changes (`11a8d08a`)
- docs: reconcile the config-store docs with the verbatim-own-properties change (`cab68737`)
- docs(submit-jobs): correct the dialog's own submission docstring (`23d22383`)
- docs: cover the tri-state form, pre-flight and per-entity resolution (`0e7943c4`)

### Chores
- chore(deps): drop two orphaned python dependencies (`96e3e008`)
- chore: pin line endings with .gitattributes (`0eb3b218`)
- chore(submit-jobs): tighten the batch zips and the docs left behind (`60870f13`)

## v1.43.0 — 2026-08-27

### Breaking Changes
- refactor(config)!: retire the Python migrator for the shared core (`b56d0d0b`)

### Features
- feat(config): repoint temp:/ off the project drive (v8) (`72b4250d`)
- feat(config): drop the retired kits entries from storage_convention (v6) (`026e8aec`)
- feat(wizard): give tt_prepare a headless --migrate mode (`d5cbed33`)
- feat(hpm): ship tt_prepare and register it as a launch hook (`4ec9ccca`)
- feat(wizard): add tt_prepare, the launch-time _config migrator (`86535fe4`)
- feat(wizard): apply the _config migrations from core (`bab10c69`)
- feat(wizard): add the migration registry and a preflight to core (`55572bd7`)
- feat(templates): department templates own their own layout (`64764cca`)

### Fixes
- fix(storage): resolve temp:/ to machine-local scratch (`7443ba3b`)
- fix(config): point the convention modules at the renamed package (v7) (`334a3a5b`)
- fix(hda): rebuild asset_thumbnail from clean source, add Type parm (`0272a67e`)

### Refactors
- refactor(wizard): split the crate into a workspace with a shared core (`312cd21d`)

### Documentation
- docs: document TH_TEMP and the v8 config migration (`f246e5f8`)
- docs: cover the hook apps and drop references to what was removed (`466e2bcb`)

### Chores
- chore(wizard): stop creating the kits/ directory (`dde13ef7`)

## v1.42.0 — 2026-08-26

### Breaking Changes
- refactor!: rename the publish "variant" to a "channel" (`82822e14`)

### Features
- feat(scripts): audit that menu scripts reach a real wrapper method (`cdceb23d`)
- feat(config): relabel the browser variants column as Channels (`0e259789`)
- feat(submit): pick render variants from a checkable menu (`2bff20f0`)
- feat(submit): default the render department to the open workfile's (`5dc9245b`)

### Fixes
- fix(validators): drop the nonexistent Rest LOP hint (`984a1df7`)
- fix(hda): stop create_model prefixing absolute paths too (`2f079200`)
- fix(hda): honour incoming path attributes in create_asset_model (`4bc2fc3c`)
- fix(browser): stop reporting partial failures as success (`a6aad142`)
- fix(browser): report failed artist-initiated actions instead of only logging (`cc984bea`)

### Documentation
- docs: describe channels, and the wire boundary a rename must not cross (`b54adeaa`)
- docs: describe where model geometry lands under the asset (`ddb3f09d`)
- docs: describe the Submit Jobs variant menu (`97d25154`)
- docs: document the workfile-seeded render department (`1db09e76`)
- docs: record that an action the artist asked for must fail visibly (`89858ffe`)
- docs: regenerate CHANGELOG.md through v1.41.0 (`567dda50`)

### Chores
- chore(toolbar): drop the dead export_variant and import_variant tools (`925de824`)

## v1.41.0 — 2026-08-03

### Features
- feat(asset-browser): show workfile version notes in a Note column (`1b9f9b22`)
- feat(workfiles): record an artist note on each saved version (`1acfdf4f`)

### Fixes
- fix(setup): treat a cancelled wizard as a decline, not a failure (`e80f263c`)

### Documentation
- docs(setup): correct the wizard's documented cancel exit code (`fc1c6bec`)
- docs: regenerate CHANGELOG.md through v1.40.1 (`658a6346`)

## v1.40.1 — 2026-08-02

### Fixes
- fix(setup): ship the project-setup wizard with its executable bit (`c232f65c`)

### Documentation
- docs(ci): correct the stale hpm pin reference (`2df48c10`)
- docs: regenerate CHANGELOG.md through v1.40.0 (`601a897a`)

## v1.40.0 — 2026-07-31

### Breaking Changes
- fix(hpm)!: declare TH_PROJECT_PATH as a required env var (`4c0b6da3`)

### Documentation
- docs: document the TH_PROJECT_PATH requirement (`0dffa7a3`)

## v1.39.2 — 2026-07-24

### Fixes
- fix(scripts): spawn tt_setup via $HPM_PACKAGE_ROOT (`1a153246`)

## v1.39.1 — 2026-07-24

### Fixes
- fix(build): run the Windows wizard cargo build under the MSVC env (`34e45822`)

## v1.39.0 — 2026-07-24

### Features
- feat(catalog): implement project_for_ref for Favourites project-scoping (`086af72d`)
- feat(wizard): native Rust tt_setup wizard (wizard-src) (`639288e3`)

### Refactors
- refactor: move native sources under src/ (src/resolver, src/wizard) (`efbe3ec4`)

### Documentation
- docs(hpm): list build-wizard in the [stage] prepack comment (`66ef73a9`)
- docs(wizard): document native tt_setup wizard; add real-template parity test (`e898843d`)

## v1.38.8 — 2026-07-23

### Fixes
- fix(import_assets): mark duplicates instanceable only when not animatable (`cd3d88e9`)

### Documentation
- docs(composition): document instanceable copies and the animatable flag (`327abe26`)
- docs(changelog): regenerate through v1.38.7 (`09de7b43`)

## v1.38.7 — 2026-07-23

### Fixes
- fix(exr): stamp ACEScg chromaticities so RV/Nuke read renders correctly (`045f63bb`)
- fix(farm): flatten batched export chunks like the interactive path (`d42c8889`)
- fix(usd): stitch keeps non-usd sidecars and skips only the top-level main (`045b704b`)
- fix(export): publish stage caches on 1s, not the render step (`e6225a2b`)

### Refactors
- refactor(usd): single home for chunk math and sidecar flattening (`613042ed`)

### Documentation
- docs(compositing): document ACEScg colour space stamping (`88c5043f`)
- docs(tests): update harness README for the minigun 3.x API (`f0e5b608`)
- docs(changelog): regenerate through v1.38.6 (`16f41858`)

### Tests
- test(usd): cover frame chunking for batched export (`fdba611d`)
- test: migrate the suite to minigun 3.0.1 (`e5841227`)

## v1.38.6 — 2026-07-22

### Fixes
- fix(publish): refresh session after local publish so own exports appear without restart (`6d7de407`)

### Documentation
- docs(changelog): regenerate through v1.38.5 (`e9d90c15`)

## v1.38.5 — 2026-07-21

### Fixes
- fix(farm/publish): force all imports to latest consistently on publish (`3c261843`)
- fix(import_rigs): honor per-row version selection instead of forcing latest (`52ddc875`)

### Documentation
- docs(development): drop reference to deleted update usd_rop trackprimexistence (`a046abb8`)
- docs(changelog): regenerate through v1.38.4 (`73bb8a27`)

### Chores
- chore(farm): remove dead update/publish.py (`4c145e1e`)
- chore(farm): remove dead update/export.py + export_houdini.py (`0bec62ad`)

## v1.38.4 — 2026-07-21

### Features
- feat(anim): output Cd/Alpha from th animate as displayColor/displayOpacity (`f6759480`)

### Documentation
- docs(changelog): regenerate through v1.38.3 (`4d1c012d`)

## v1.38.3 — 2026-07-21

### Features
- feat(desktop): show network controls on the Scene View by default (`7dc129b6`)

### Fixes
- fix(desktop): stop desktops overriding viewport clip/homing defaults (`d33323cc`)

### Documentation
- docs: retire references to the removed tag-time ci checks (`7d76a719`)
- docs(changelog): regenerate through v1.38.2 (`a09d601f`)

## v1.38.2 — 2026-07-21

### Fixes
- fix(farm): encode full contiguous frame span in farm mp4s (`04f91ce7`)

### Documentation
- docs(changelog): regenerate through v1.38.1 (`34236e29`)

### Chores
- chore(scripts): add playblast path convention auditor (`8042494d`)

## v1.38.1 — 2026-07-20

### Fixes
- fix(farm): keep the root layer out of the department cut (`d2f5a2a4`)

### Documentation
- docs(usd): point the cut's pool guard at RESERVED_NAMES (`7e012df2`)
- docs(composition): say the cut only slices pool departments (`8263ab3c`)
- docs(changelog): regenerate through v1.38.0 (`4fcd4dda`)

## v1.38.0 — 2026-07-20

### Features
- feat(submit-jobs): say what the department dropdowns actually do (`0b06ecac`)
- feat(render_stage): cut the department stack at the submitted department (`1bd2f794`)
- feat(import_shot): add exclude_downstream_of for a render's department cut (`1511dcce`)
- feat(usd): drop staged layers past a department cut before flattening (`6c371746`)
- feat(config): add department_names_up_to, the inclusive pool slice (`2d19566f`)

### Fixes
- fix(farm): report a disconnected export node instead of crashing on it (`c1816251`)
- fix(export): skip a disconnected export node instead of failing its department (`6a8ae6f5`)
- fix(farm): honour the render and playblast department selections (`fc91eeb1`)
- fix(resolver): re-check layer handle after UpdateAssetInfo (`4f55af19`)

### Documentation
- docs(development): describe the skip-vs-fail task contract (`72d59106`)
- docs: describe the render department cut, correct the scoping boundary (`a998ee72`)

### Tests
- test(process_dialog): pin the skip-vs-fail split for grouped tasks (`326e5a11`)

## v1.37.2 — 2026-07-18

### Fixes
- fix(lpe_tags): surface tags that match no lights, and find mesh lights (`1710daeb`)
- fix(lpe_tags): build render vars for mesh-light LPE tags (`32e800d8`)
- fix(asset-browser): give Reload a glyph distinct from Refresh (`637b2b39`)
- fix(import_layer): clear the node bypass when an import succeeds (`900067c4`)
- fix(import_model): resolve the variant set from the Department parm (`b3ff6908`)
- fix(import_model): keep the variant menu alive when the entity can't resolve (`6292a142`)

### Documentation
- docs(changelog): regenerate through v1.37.1 (`28018304`)

### Tests
- test(lpe_tags): pin the tag-to-render-var contract, document HDA edits (`63c05a96`)
- test(import_model): add a verify script for the variant dropdown (`68f8b104`)

## v1.37.1 — 2026-07-17

### Fixes
- fix(export): exempt any workfile-directory cache from the escaping-arc guard (`5dff8ced`)

### Documentation
- docs(changelog): regenerate through v1.37.0 (`bdcad273`)

## v1.37.0 — 2026-07-17

### Features
- feat(catalog): replace the session panel's department list with a workspace/export detail view (`03fefd92`)

### Fixes
- fix(export): exempt cross-entity SOP th::cache bgeo from the escaping-arc guard (`299d7fa7`)

### Documentation
- docs(changelog): regenerate through v1.36.1 (`548b9559`)

## v1.36.1 — 2026-07-17

### Fixes
- fix(paths): export get_workspace_relpath so it survives the F,E9 lint (`adb4146e`)

## v1.36.0 — 2026-07-17

### Features
- feat(cache): add Department parm so th::cache can load other workfiles' caches (`67dc2bee`)
- feat(asset-browser): session pane shows Current Workspace + Latest Export (`d9530061`)

### Fixes
- fix(ci): emit the canonical registry install line in release notes (`56ba3b12`)

### Documentation
- docs(asset-browser): finish the badge -> licence terminology sweep (`38085fc4`)
- docs(changelog): regenerate through v1.35.0 (`93072a9e`)

## v1.35.0 — 2026-07-17

### Features
- feat(catalog): give a Multi's department coverage a way back in (`d5ec7bd8`)
- feat(catalog): fill the session panel, and don't blank it on a Multi (`f996e1ba`)
- feat(scripts): audit that every HDA callback reaches a real function (`6231bbaf`)
- feat(scripts): audit asset nesting for cycles across projects (`92314a55`)
- feat(lops): open an asset's department workfile from the layer inspector (`9acf9907`)
- feat(lops): inspect the department layers inside an asset's Layer Stack row (`2a6ca9e8`)
- feat(usd): read an asset's department layers back out of its staged file (`4d8e7b15`)

### Fixes
- fix(catalog): lose the badge on an old tumbletrove, not the deck (`6a9b796a`)
- fix(startup): follow the radial out of tumbletrove, and stop hiding it (`f9b2c98b`)
- fix(cache): forward select() so th::cache's Entity button works (`7da9ec04`)
- fix(lops): stop import_assets raising on its own department pool (`034cb256`)
- fix(lops): show which departments are excluded in import_asset's menu (`9720afd8`)
- fix(catalog): stop shipping two shapes under metadata["departments"] (`08950863`)
- fix(lops): report the loaded version in the Layer Stack, not the stripped pin (`e33b81ba`)

### Performance
- perf(catalog): one read per dept row, and the licence badge it needs (`1c8eae2b`)
- perf(lops): don't flatten a staged asset for an exclusion that drops nothing (`a433d11b`)

### Refactors
- refactor(paths): one version_name_from_path, not two hand-rolled copies (`c44a96b6`)
- refactor(catalog): drop the hover grid's now-dead shape normalization (`761cfd5b`)

### Documentation
- docs(development): document the asset-payload verification (`ed48f790`)
- docs: the right pane is the session panel in the Pipeline scope (`f65335e6`)
- docs(structure): the radial is its own package now (`77146dc5`)
- docs(otls): a callback takes three files, not two (`f423e537`)
- docs: nesting is not containment, and two stale notes (`38ce84c4`)
- docs(design): mark the non-renderable-exclusion shortcut as fixed (`ff40e290`)
- docs(design): the header-metadata claim is false; the flatten is lossless (`cd2d8585`)
- docs(composition): department exclusion does not survive the session (`1224ccde`)
- docs(design): the nested-asset workflow, and why "X in Y" is a lie (`3a24ab14`)
- docs(composition): document the layer inspector, and that only renderable departments compose (`8f051f40`)
- docs(design): record the inspector as built, and what is still unverified (`61a9e187`)
- docs(catalog): correct the departments schema in PipelineAssetMetadata (`2a400cab`)
- docs(design): plan the asset layer inspector (`9508d237`)
- docs(changelog): regenerate through v1.34.0 (`8e0427ce`)

### Tests
- test(lops): pin the asset inspector button in the layer stack verify (`146f2007`)

## v1.34.0 — 2026-07-16

### Features
- feat(denoise): drive idenoise instead of hython (`86a7fe0c`)
- feat(apps): wrap idenoise and add the exr plumbing it needs (`56838f4f`)

### Fixes
- fix(usd): name flatten instances from the asset URI, not the scraped tag (`76856f74`)
- fix(usd): never re-derive an xformOpOrder that already composed (`3d90252b`)
- fix(denoise): probe channel counts on a real frame, not a frame pattern (`f071918d`)
- fix(import-shot): author the prototype transform in the duplicates subnet (`0825c931`)
- fix(usd): author the prototype transform in the static flatten too (`fe8628af`)
- fix(import): author the transform on re-established sub-asset prototypes (`d81df918`)
- fix(asset-browser): don't build the DatabaseWindow on a worker thread (`ed33d515`)

### Documentation
- docs(composition): document the direct-render flatten; correct the order rule (`3e7f0311`)
- docs(deadline): record which farm tasks need a Houdini license (`a5395dc9`)
- docs(designs): record what the idenoise port actually verified (`b768b488`)
- docs(composition): record that the prototype is re-established too (`d72e24d8`)
- docs(designs): plan denoising the farm renders without a Houdini license (`0ef8ed9e`)
- docs(designs): reconcile the Qt thread-safety plan with what shipped (`1b158281`)
- docs(designs): plan for killing the asset-browser Qt/thread crash class (`50397ef8`)

### Tests
- test(usd): pin the prototype's transform in the static instance defs (`cfc13070`)

## v1.33.1 — 2026-07-16

### Fixes
- fix(export): scope the dropped-asset guard to the asset export tree (`65a3c6a9`)
- fix(farm): derive resolver major from the running Houdini; harden the contract (`726b21a3`)
- fix(farm): run jobs against the submitting Houdini's major, not hardcoded 21 (`48b9a940`)

## v1.33.0 — 2026-07-15

### Features
- feat(ocio): add legacy W:/_pipeline OCIO retire audit script (`18e8ae38`)
- feat(context): add verify_context_chain diagnose/repair tool (`e843e39a`)

### Fixes
- fix(export): allow artist-added geometry beside tracked assets (`80abf5d0`)
- fix(context): harden workfile version chain against races and v0000 re-anchor (`47674dbc`)
- fix(import-asset): merge the input in after metadata instead of feeding the sublayer (`657666c0`)
- fix(ci): drop stale ocio dir check from validate-structure (`ab686721`)

### Documentation
- docs: document the dropped-metadata guard and its arc-aware harness (`91b524dd`)
- docs: document the legacy OCIO retire audit, correct the concat claim (`b74dd091`)
- docs: document the context-chain audit and test (`84920b24`)

### Tests
- test(context): pin the workfile context-chain hardening (`9f5ccc67`)

## v1.32.1 — 2026-07-15

### Fixes
- fix(publish): default the validation task off pending convention work (`af65de1e`)
- fix(farm): load only the current package houdini dir in hython env (`8c8b3afe`)
- fix(ocio): own the color config per project, not in the package (`4149d35d`)
- fix(resolver): skip expired layer handles in refresh_context (`90fbcfff`)

## v1.32.0 — 2026-07-15

### Features
- feat(browser): Playblast section in the Submit Jobs dialog (`f6566fb6`)
- feat(farm): submit playblast jobs from batch_submit (`322ccec1`)
- feat(farm): playblast job family (`421e712f`)
- feat(farm): playblast task family (headless GL via Hydra Storm) (`58789c84`)
- feat(browser): per-entity department assignment UI (`83c96ef7`)
- feat(browser): department pool editor (`775e137b`)
- feat(browser,hdas): scope department menus and rows to the entity (`3d728d09`)
- feat(config): per-entity department assignment (`9eb1c621`)
- feat(config): department pool ordering, insert-at-index, reserved-name guard (`454e5c76`)
- feat(migration): refresh _config/templates from the packaged scaffold (`7aecaee0`)
- feat(cache): add an Entity parm and a database frame range to th::cache (`93e4dc15`)
- feat(otls): give th::create_asset an Entity parm (`5da56a8e`)

### Fixes
- fix(catalog): make the department-pool fallback loud, not silent (`96da129f`)
- fix(config): don't KeyError on un-migrated department nodes (`b966340a`)
- fix(export): preserve animated switch/blend on USD export (track prim existence) (`bfabe3dc`)
- fix(farm): pass department to playblast/daily path helpers (`43480a54`)
- fix(hdas,browser): scope the department menus the first pass missed (`d3ef0894`)
- fix(otls): restore import_lop_camera's editable embedded import node (`0cab6cc1`)
- fix(otls): rebuild th::import_lop_camera on th::import_shot (`18978cc3`)
- fix(templates): leave single-entity graphs on from_context (`2faf19a8`)
- fix(hdas): keep entity parms on the from_context sentinel (`b98fbe80`)

### Documentation
- docs(config): document the v3 departments migration + un-migrated resilience (`c62ad358`)
- docs: pin the animated-switch export trackprimexistence contract in development.md (`ee1397a2`)
- docs(composition): say where "pipeline order" comes from, and that scoping never changes a render (`49a6ce8b`)
- docs(design): editable department pool + per-entity department assignment (`c554d896`)
- docs: document the from_context contract and template migration (`e42ccf2c`)

### Tests
- test(hdas): audit the entity from_context contract (`855b6f4d`)

### Other Changes
- docs+tooling: farm playblast job harness and docs (`97ebdc49`)
- docs+tooling: department pool audit script and configuration docs (`4cc809e5`)

## v1.31.0 — 2026-07-14

### Features
- feat(export): report the exported version in the process dialog (`248c0d14`)
- feat(changelog): generate CHANGELOG.md from the tag history (`489aacea`)
- feat(submit-jobs): checkable entity tree for multi-entity submission (`d6d44fdc`)
- feat(identity): stamp the TumbleTrove account as the pipeline user (`ebb1bfd5`)
- feat(catalog): show category path on asset rows in mixed-category views (`c882e708`)
- feat(export): publish versioned th::cache files by reference (`473b99a1`)

### Fixes
- fix(changelog): drop bump commits, parse breaking-change markers (`eb5fc5ff`)
- fix(catalog): refresh the created/saved entity's own card row (`b71d1a83`)

### Refactors
- refactor(process-dialog): delete the unused ProcessTaskTableModel (`8c4eef60`)
- refactor(ci): extract the changelog renderer into a shared module (`7423c643`)

### Documentation
- docs(design): native USD variants for assets, Render Layer rename for shots (`9362c1f5`)
- docs: versioned-cache exemption in composition.md, catalog its harness (`533c6c62`)

## v1.30.0 — 2026-07-13

### Features
- feat(catalog): surface the project name via get_sidebar_subtitle (`d848317b`)
- feat(submit-jobs): entity selector defaulting to the open entity (`fe4585de`)

### Documentation
- docs: catalog the Submit Jobs entity-selector harness in development.md (`83596644`)

## v1.29.0 — 2026-07-13

### Features
- feat(submit-jobs): first/middle/last range mode, render on by default (`f0e36062`)
- feat(catalog): Render quick action next to Publish (`0073ccf6`)
- feat(catalog): surface publish author in detail rows + dept hover (`dbe70c6a`)
- feat(catalog): per-scene user/edited in list-view deck rows (`8048d4ed`)

### Fixes
- fix(import-shot): anchor Layer Stack insert on parms, not folder names (`33b34a8f`)
- fix(catalog): Multi-covered dept tooltip says double-click to open (`c3b67d5b`)

### Refactors
- refactor(catalog): drop dead user_mtime_label helper (`d6594fde`)

### Documentation
- docs: pin the HDA spare-parm anchoring contract (harness + development.md) (`e73917d9`)

## v1.28.0 — 2026-07-13

### Features
- feat(browser): add Update quick action — refresh imports without reloading (`d43ff74c`)

### Fixes
- fix(resolver): make refresh_context() reload stale entity layers (`02a8869d`)
- fix(browser): migrate pref files that froze auto_refresh_on_open=false (`3fadbb1a`)
- fix(browser): route Reload through prepare_scene_swap (`708193db`)
- fix(import): request resolver refresh from import_assets and import_layer (`d2dc25c4`)
- fix(resolver): implement _RefreshContext so refresh_context() actually invalidates (`2f1c5848`)

### Documentation
- docs: pin the resolver-refresh contract (harness script + caveats) (`cff244b2`)
- docs: document mid-session version pickup (Update action + layer reload) (`19c67740`)

## v1.27.1 — 2026-07-11

### Fixes
- fix(paths): resolve render layers from 'variants', unbreaking comp AOV discovery (`6859ec6b`)
- fix(export): don't flag USD search-path arcs as dangling (`acf24ed0`)

### Documentation
- docs: document the compositing workflow (build_comp, farm chain, MP4s) (`a345bb86`)
- docs: document the dangling-arc guard and its search-path exception (`ba540dbd`)

## v1.27.0 — 2026-07-11

### Features
- feat(hpm): root catalog downloads under the active project (`ff00db78`)
- feat(config): add is_animatable entity predicate (`795009bc`)

### Fixes
- fix(staging): fall back to default-variant exports in variant shot builds (`d68f311d`)
- fix(asset-browser): keep asset frame range across workfile re-open (`fd3ae52d`)

### Documentation
- docs: document default-variant fallback in variant staged builds (`13148b21`)

## v1.26.0 — 2026-07-10

### Features
- feat(asset-browser): add User + Edited columns to the pipeline list view (`d1810457`)

## v1.25.3 — 2026-07-10

### Fixes
- fix(asset-browser): default Auto-import latest on workfile open to on (`1587e5fe`)
- fix(asset-browser): omit the unimplemented Tasks sidebar section (`43dab2bd`)

## v1.25.2 — 2026-07-09

### Fixes
- fix(import-shot): only reset the playhead when it is outside the shot range (`c9df63e7`)

## v1.25.1 — 2026-07-08

### Fixes
- fix(scripts): satisfy the F841 gate in the process-dialog harness (`4c7baed1`)

## v1.25.0 — 2026-07-08

### Features
- feat(process-dialog): warn when cancelling leaves enabled steps unrun (`2726b9a8`)
- feat(process-dialog): surface export progress breadcrumbs (`4b4cf295`)

### Fixes
- fix(export-layer): refuse to export under an unlisted variant name (`1229c0e3`)
- fix(process-dialog): show the running child task in the status label (`3363728f`)
- fix(config-editor): commit URI-tree renames once, when the editor closes (`e8e1b636`)
- fix(config-editor): commit edits on click-away and make key renames stick (`5da3ffc0`)

### Refactors
- refactor(otls): rename th_a_b_slider operator type to a_b_slider (`53b5e2fe`)
- refactor(otls): rename th_cop_material_library into the th namespace (`a192b258`)

### Documentation
- docs: cover the Qt widget harnesses and correct a model docstring (`1b19e46a`)
- docs(asset-browser): note env-path validation in the catalog docstring (`a292b839`)

### Tests
- test(process-dialog): add a QTest harness for the execution UX (`8a4fad40`)
- test(config-editor): add a QTest harness for the editor's commit UX (`d20eb07d`)

## v1.24.2 — 2026-07-07

### Fixes
- fix(ci): adopt the HPM_HOUDINI_MAJORS build-env contract for the resolver build (`85b18450`)

### Documentation
- docs: document local package builds and the HPM_HOUDINI_MAJORS knob (`7905458c`)

## v1.24.1 — 2026-07-07

### Fixes
- fix(ci): carry hpm's operator index through to version registration (`d9182a9a`)

## v1.24.0 — 2026-07-07

### Features
- feat(scripts): sweep workfiles for H22-enabled layer save paths (`10dc290e`)

### Fixes
- fix(ci): point the resolver build's missing-install error at HOUDINI_MAJORS (`52032d15`)
- fix(image_plane_painter): OnDeleted no longer errors without a thumbnail (`fd2bef16`)
- fix(export): refuse to publish layers whose composition arcs escape the folder (`ebe02a15`)
- fix(houdini): ship OnCreated scripts turning off H22's default layer save path (`112c55bb`)
- fix(templates): disable H22-default layer save paths on template nodes (`51674c54`)
- fix(slapcomp): select channels positionally in constant-alpha branch (`61ac9961`)

### Documentation
- docs: document the export portability contract and layer-save-path guard (`61560276`)
- docs(exr): document the per-AOV channel-naming contract at the split site (`ddd7fbe2`)

## v1.23.3 — 2026-07-06

### Fixes
- fix(batch-submit): collapsed stages derive instance op order from composition (`df7e85e7`)
- fix(import-shot): apply placement op order after the duplicates subnet (`a2eb6eb4`)

### Refactors
- refactor(import): share placement-op-order authoring via util helper (`8956c6eb`)

### Documentation
- docs: one shared placement-op-order rule across all three instance paths (`d6d7ea8b`)

## v1.23.2 — 2026-07-06

### Fixes
- fix(import-asset): author xformOpOrder for composed placement ops (`16d0507f`)
- fix(import-asset): re-established duplicates author dup op + xformOpOrder (`2ed7fcd9`)
- fix(hda): import_asset transform node uses XformCommonAPI (`6681fd99`)

### Documentation
- docs: re-establishment authors xformOpOrder; drop stale no-transform claim (`f91f8533`)

### Other Changes
- Revert "fix(import): duplicate nodes author matrix ops, not XformCommonAPI" (`bc1f26fd`)

## v1.23.1 — 2026-07-06

### Fixes
- fix(import): duplicate nodes author matrix ops, not XformCommonAPI (`304861f2`)

## v1.23.0 — 2026-07-06

### Features
- feat(config): reject entity names differing only by case from a sibling (`b04c3576`)
- feat(scripts): sweep staged tracked-asset counts vs department contexts (`0cf4f84d`)

### Fixes
- fix(import-asset): deactivate stale re-established duplicates (`063bae55`)
- fix(staging): no direct ref for transitively-reachable tracked assets (`0c298cbb`)

### Refactors
- refactor(staging): drop unused per-layer asset dict from shot_layers (`73316e4a`)

### Documentation
- docs(development): local lint advice matches the CI gate (E9,F) (`59738acd`)
- docs: correct the export sidecar story; document dedup + stale cleanup (`db095d27`)

## v1.22.0 — 2026-07-06

### Fixes
- fix(staging): newest-export-wins for shot-flow asset counts too (`4aacf6bf`)

### Documentation
- docs: newest-export-wins applies to shot-flow asset counts too (`8b83ffda`)

## v1.21.1 — 2026-07-06

### Fixes
- fix(staging): newest-export-wins for tracked-asset counts, not max() (`039bc053`)

### Documentation
- docs: document newest-export-wins for tracked-asset merges in staged builds (`b5689b3a`)

## v1.21.0 — 2026-07-06

### Breaking Changes
- refactor(pipe)!: unify entity-URI accessor naming on get/set_entity_uri (`d74e69dd`)
- refactor(pipe)!: evict the config-editor app from pipe/ to tumblepipe.config_editor (`8392e961`)
- refactor!: delete the unused rpc package (~5k lines) (`481a0f77`)
- refactor(radial)!: purge native radialmenu system, migrate to tumbletrove radial (`ca83d9b3`)
- refactor(asset-browser)!: adopt tumbletrove's deck vocabulary (`1d476220`)

### Features
- feat(asset-browser): Version column after Name in pipeline list view (`b4417f35`)

### Fixes
- fix(import): variant-aware staged version menus; tag 0-based duplicates (`f4fb3f13`)
- fix(import-asset): re-establish tracked sub-assets on staged import (`d0d610fa`)
- fix(import-layer): pass keywords to the widened _metadata_script (`a7132cd5`)
- fix(staging): pin tracked-asset variant and version in staged builds (`e5210bf0`)
- fix(cloud-stage): nest the entity dict in the configs stage.py emits (`3c49f5ac`)
- fix(farm): stage one pinned USD per variant instead of one floating stack (`84b59d72`)
- fix(render-debug): add the variant parm the node code expects (`151060d1`)
- fix(startup): skip sparse radial menus instead of writing invalid specs (`789a1e68`)
- fix(asset-browser): import DEPT_SHORT_NAMES in the Multi work-scenes section (`05120195`)
- fix(asset-browser): repair hover-widget import and todos detail section (`b96c1149`)
- fix: harden publish path and replace silent fallbacks with hard failures (`ca27f0ab`)

### Refactors
- refactor(cloud-stage): build the render stage via the shared builder (`44e47568`)
- refactor(render): shared render-stage graph builder; render_debug uses it (`497eca26`)
- refactor(pipe): hoist identical wrapper boilerplate into EntityNode base (`f1126984`)
- refactor(farm): de-fork render and cloud_render job builders (`f7bdf1f7`)
- refactor(pipe): split build resolution out of graph.py (`63a607ac`)
- refactor(farm): dedupe publish-job builder between update and batch_submit (`7269ebd7`)
- refactor: delete dead modules, UV plugin branch, and unused config API (`d59743b1`)

### Documentation
- docs: document render staging semantics and the shared graph builder (`5ae3ead6`)
- docs: update project structure for the refactor pass (`d36d46eb`)

### Chores
- chore: gitignore the startup-generated radial menus (`86da62a0`)
- chore(asset-browser): clear _pipeline_detail lint debt (F821/F401) (`9ccbc9e6`)

## v1.20.0 — 2026-07-03

### Features
- feat: ship read-only recipes via ASSET_BROWSER_NETWORK_PATH (`88740fe3`)

### Fixes
- fix(sop-import): prime embedded labels on create (`2b68e203`)
- fix(import_assets): read-only menus, instance-relative opmenu paths (`5c6511cc`)
- fix(sop-import): rebuild th::Sop/import_asset around the live LOP interface (`e97c7b87`)

### Performance
- perf(asset_browser): one enumeration + one scandir per card, GUI-thread guard (`1c8bc2e0`)
- perf(config): list_entity_uris for uri-only listings + coherent() batching (`91179757`)
- perf(config): stamp once per read + memoize results — fix v1.16.5 stat storm (`fca16179`)

### Documentation
- docs: document the shipped recipes/ directory (`c0211841`)
- docs: read-semantics of the config store, HDA menu-script rules (`d342aa06`)

## v1.19.0 — 2026-07-03

### Fixes
- fix(import): apply department exclusion through nested asset staging (`e68529b2`)
- fix(build): compose tracked assets into an asset's staged file (`2a4b2e54`)
- fix(util): scrape tagged 'over' prims - dept-layer imports lost tracking (`bf887c86`)
- fix(import/export): see assets inside instances; tag import_layer roots (`f5ddf508`)

### Refactors
- refactor(otls): purge the th::duplicate HDA (`cb5c1ad5`)
- refactor(catalog): adopt the tumbletrove 0.8 hook renames (`ea94203b`)
- refactor: legibility pass - flatten nesting, dedupe farm/config validators (`836a1e60`)

### Documentation
- docs: document asset composition, staging, and nested assets (`400e1032`)
- docs(otls): document expanded-HDA editing rules (.chn whitespace trap, .orig files) (`843c3b9a`)

## v1.18.2 — 2026-07-03

### Fixes
- fix(hda): spaceless layerbreak activation expr - scenes failed to load (`e582fe52`)
- fix(scripts): verify_entity_casing skips export stage/ intermediates (`b20b7e4b`)

## v1.18.1 — 2026-07-02

### Features
- feat(scripts): add case-duplicate category fix + entity casing verify CLIs (`370afadf`)

### Fixes
- fix(scripts): verify_entity_casing - gate crate scan, tighten filename check (`be0b65a8`)
- fix(validators): accept Xform asset categories/roots in shot checks (`6c14a6cb`)

### Refactors
- refactor(catalogue): drop dead get_primary_filters (pill row removed upstream) (`e48f8a15`)

### Documentation
- docs(configuration): document the entity casing audit + fix CLIs (`2fe669a9`)
- docs(catalogue): stop calling the project: filter a pill (`c8aad075`)

## v1.18.0 — 2026-07-02

### Features
- feat(import): add Import Mode (Reference/Inline) to LOP import nodes (`307aceea`)
- feat(export): recognize deliberately inlined assets in the publish guards (`32f97a3d`)

### Fixes
- fix(catalogue): preserve case in category/sequence tags — pipeline naming is case-sensitive (`e48b8253`)

### Refactors
- refactor(catalogue): drop unused Qt import and ctx_seg local left by the case-preservation fix (`de1be61c`)

## v1.17.0 — 2026-07-02

### Features
- feat(browser): default shot collections to list view (`1327ff9c`)

### Fixes
- fix(export): block publish when an import node's asset is missing from the metadata scrape (`da807075`)
- fix(import): make 'Exclude departments' actually work on import_asset (`897c6d51`)

### Refactors
- refactor(import): drop dead get_department_names from import wrappers (`03f55e11`)

## v1.16.6 — 2026-07-01

### Fixes
- fix(browser): restore the Publish export window (one dialog, not per-node) (`5eaa5d2b`)

## v1.16.5 — 2026-07-01

### Features
- feat(hpm): declare the HDA operator index; drop third-party hpaint (`e48a2843`)
- feat(migration): expose migrate-config as a desktop project script (`ad655939`)
- feat(migration): back up original config_convention before clobbering (`4129e462`)

### Fixes
- fix(browser): stop Publish popping one window per export node (`3e2d12ac`)
- fix(import/export): stop assets silently dropping when import metadata is missing (`ef0bab67`)
- fix(migration): label migrate-config so the launcher lists it (`b06d275a`)

### Refactors
- refactor(hda): hide include_layerbreak toggle on import nodes (`5394d6eb`)
- refactor: narrow bug-hiding bare excepts so real errors propagate (`b867ccc0`)
- refactor(farm/tasks): dedup hython/OCIO child-process env into env.py helpers (`461faa61`)
- refactor(houdini): drop dev-time submit() reloads and dead commented code (`4c9d4f42`)
- refactor(api): collapse fix_path into local_path; drop dead WSL short-path code (`02e5dea7`)
- refactor(ui/database): split json_editor/widget into items + view (`a61cae4e`)
- refactor(cops): build_comp.create() adopts ns.create_node (`323f2080`)
- refactor(sops): adopt ns.create_node/set_node_style (consistency with lops) (`ae2d36fe`)
- refactor(ui/database): split the 2886-line json_editor into a package (`0f9d2aaf`)
- refactor(lops): centralize node create/style into ns; fix bugs surfaced en route (`8173ffa2`)
- refactor(farm/jobs): make _common Deadline-free; sweep sibling _error helpers (`f925f947`)
- refactor(farm/jobs): migrate propagate/update/composite/cloud_render/render onto _common (batch 2/2) (`82822cd1`)
- refactor(farm/jobs): extract shared scaffolding into houdini/_common.py (batch 1/2) (`cffbd096`)
- refactor(pipe/paths): dedupe the 5x-copied workspace resolution in workspace.py (`8c7e6c32`)
- refactor(pipe): split the 1719-line paths.py god module into a paths/ package (`c3aa78ca`)
- refactor(houdini/ui): split the 1446-line process_executor god module (`acae9a99`)
- refactor(rpc): centralize the try: import hou guard into util.houdini (`66d165bb`)
- refactor(config): merge scene+scenes, push USD build down into pipe (`e37937c4`)
- refactor(catalog): drop now-dead per-module api patching on project switch (`e162635c`)
- refactor: retire eager api=default_client() across the package (`79f17f07`)
- refactor(config): lazy client proxy, retire eager api bindings + RLock (`e73a3471`)
- refactor(config): coherent-read DB store, package engine, versioned migrations (`d8a15447`)

### Documentation
- docs: fix stale import namespace (tumblehead -> tumblepipe) (`9a9cac44`)

### Chores
- chore(rpc,farm): drop pre-existing unused imports in touched modules (`d3a25d24`)
- chore: clean up after hpaint removal; refresh migration docs (`6600a08e`)

## v1.16.4 — 2026-06-30

### Fixes
- fix(workfiles): apply config timeline on scene reload (`74f6a3a4`)
- fix(timeline): pin frame range across fps change on shot open (`9bee12f9`)

### Refactors
- refactor(timeline): set fps before frame range at every call site (`794fb890`)

## v1.16.3 — 2026-06-29

### Refactors
- refactor(desktop): drop rig_tree panel from layout (`dbdac88a`)

## v1.16.2 — 2026-06-29

### Fixes
- fix(export): point frame-range error at an option the node actually has (`ffef1957`)
- fix(export): refresh config cache before resolving frame range (`6e830f1b`)

### Refactors
- refactor(export): drop dead frame-range source cases (`5d97d207`)
- refactor(export): centralize config-cache refresh at execute choke points (`df59c172`)

## v1.16.1 — 2026-06-24

### Fixes
- fix(catalogue): drop stale schema arg from add_entity — silent shot/asset create failure (`f5320ec9`)

### Refactors
- refactor(catalogue): remove dead schema_* URI helpers (`37abde21`)

### Documentation
- docs(tests): document catalog lifecycle test, hou stub, and Windows UTF-8 note (`a7a7fc25`)

### Tests
- test(catalog): cover create/edit/delete orchestration against real config (`9c2f6db7`)

## v1.16.0 — 2026-06-22

### Features
- feat(rebuild): preserve export_rig nodes on in-place rebuild (`06c44a89`)

### Fixes
- fix(templates): use entity-uri setters in rig/composite group scaffolding (`e755e97b`)
- fix(render_debug): remove dead render_layer parm (`778b27e8`)
- fix(build_comp): repair shot/render-dept menus and port proxy AOV TOP to URI API (`adebfb65`)
- fix(catalogue): publish export_rig via get_entity_uri and surface publish failures (`15c2fdb2`)

## v1.15.2 — 2026-06-18

### Fixes
- fix(farm): thread per-task requirements.txt into HPM job manifest (`ae4826dd`)

## v1.15.1 — 2026-06-18

### Fixes
- fix(build_comp): static frame/roll defaults to stop launch hang (`d2abc3a4`)

## v1.15.0 — 2026-06-18

### Fixes
- fix(build_comp): repair frame-range defaults after submit_render removal (`b6d9ee3f`)
- fix(validators): blendshape department checks 'blshp' not 'geo' (`69c724c9`)

### Refactors
- refactor(otls): rename karmafogbox_copy operator type to karmafogbox (`14900ada`)
- refactor(otls): group HDA tab-menu categories under _TumblePipe (`9e39d9f5`)

### Chores
- chore(resources): drop orphaned Submit.png icon (`69bb50de`)
- chore(otls): purge superseded image_plane_painter 1.0 (`6a630194`)
- chore: remove stale th_model_validator references (`aead2199`)
- chore: remove orphaned submit_render and model_validator lop modules (`765963ab`)
- chore(otls): remove dead model_validator and submit_render HDAs (`f74de60e`)

## v1.14.0 — 2026-06-17

### Features
- feat(asset-browser): emergency off-thread Save via quick-action right-click (`eb1095af`)

### Fixes
- fix(farm/publish): resolve bundled workfile against the data dir (`4a2cc60c`)
- fix(farm/render): resolve bundled collapsed-USD input against the data dir (`e31fe0d7`)

### Refactors
- refactor(asset-browser): drop dead detail-panel actions section (`b38f195b`)

### Documentation
- docs(deadline): hpm pin v0.22.1 -> v0.22.2 (Windows arg-quoting fix) (`0d1e366d`)

## v1.13.0 — 2026-06-16

### Features
- feat(farm): drop WSL2 — use Houdini's native hoiiotool/hffmpeg (`2add15cd`)
- feat(farm): run tasks in native Windows python via hpm package-env (`7e252be7`)

### Documentation
- docs: state farm prerequisites positively (drop "no WSL2/uv needed") (`7f66ac66`)
- docs(readme): farm needs Houdini, not WSL2/uv/image-tools (`96374522`)

### Chores
- chore(apps): drop the now-functionless WSLENV env patching (`a0c10997`)
- chore(farm): remove obsolete WSL worker-maintenance job (`03b2d0ac`)

## v1.12.6 — 2026-06-15

### Fixes
- fix(asset-browser): use license-correct nc_type for all workfile saves (`6b87180d`)
- fix(asset-browser): save the scene on the main thread (data loss) (`6666a206`)

### Documentation
- docs(deadline): hpm pin default v0.18.0 -> v0.21.0 (`6e620423`)

## v1.12.5 — 2026-06-15

### Documentation
- docs(deadline): HPM workers need no per-node setup (`298e5af3`)

## v1.12.4 — 2026-06-15

### Fixes
- fix(farm): declare the registry in the job manifest; build it with tomli-w (`7a4f465d`)

## v1.12.3 — 2026-06-15

### Fixes
- fix(farm): use full creator/slug in the HPM job manifest (`8ae03bf7`)

### Refactors
- refactor(farm): move HPM manifest generation to the job creators (`70403693`)

### Documentation
- docs(deadline): use farm Task factory in the submit example (`7b11bf18`)

## v1.12.2 — 2026-06-15

_No user-facing changes._

## v1.12.1 — 2026-06-15

### Features
- feat(farm): bundle the HPM manifest in the job dir + release v1.12.1 (`df028879`)

### Chores
- chore: un-ignore the two intentionally-tracked otls HDAs (`99ba169d`)
- chore: gitignore .venv/ virtualenvs globally (`5acb66fb`)
- chore(resolver-src): gitignore CMake build dirs (`bf165b46`)
- chore(otls): remove stray ViewerStateName.orig merge leftovers (`d9c36a54`)

## v1.12.0 — 2026-06-15

### Features
- feat(farm): submit jobs to the HPM Deadline plugin (`911c28c3`)

## v1.11.0 — 2026-06-12

### Features
- feat(import_model): expose Frame Mode + Import Frame, static by default (`d0a713bb`)
- feat(database-editor): restore a global launcher + stop silent failures (`428263c0`)
- feat(template): give asset entities a 1001-1200 timeline default (`628fa4cc`)

### Fixes
- fix(export_rig): foreground cache save to stop intermittent export hang (`e63cd05c`)

### Chores
- chore(ci): drop deleted project_browser.pypanel from build allowlist (`656fcd04`)

## v1.10.0 — 2026-06-12

### Fixes
- fix(hpm): stop staging pypanels entirely (`5944e178`)
- fix(hpm): stage icon_browser.pypanel instead of the deleted browser panel (`40888ae6`)
- fix(catalog): wrap Reload Scene's hip load in Manual update mode (`321f92b9`)
- fix(shot_sequencer): skip OnInputChanged work while a hip is loading (`032d12ac`)

### Performance
- perf(resolver): batch RefreshContext across import-node auto-refresh (`99fb7f18`)

### Refactors
- refactor(ui): retire the legacy Project Browser (`f310d35f`)

### Chores
- chore: clean up dead code and stale docs after Project Browser purge (`21a62dca`)

## v1.9.1 — 2026-06-11

### Fixes
- fix(catalog): scope auto-refresh-on-open to import nodes only (`13bd7b01`)
- fix(catalog): version up instead of overwriting on save-before-swap (`dc660650`)
- fix(catalog): suppress full-graph cook on workfile open (`3653c2cb`)

### Documentation
- docs(catalog): correct auto-import tooltip after import-only scoping (`b0f521a0`)

## v1.9.0 — 2026-06-11

### Features
- feat(asset-browser): dismiss deck popup when opening a workfile (`53a4c7c3`)

## v1.8.0 — 2026-06-09

### Features
- feat(asset_browser): rich hover popup on dept icons + clamp grid to 3-4 cols (`1ce35cc5`)
- feat(import_asset): add transform handle targeting imported asset (`91b404cd`)

### Fixes
- fix(catalog): read TodoItem fields instead of dict keys in detail panel (`4b05dd1b`)
- fix(catalog): move framework asset flags from metadata to typed Asset fields (`6be6487f`)
- fix(catalog): realign with tumbletrove SubCard/ProjectRegistry rename + gui_dispatch move (`1c8c7347`)
- fix(playblast): guard missing-playblast path; drop debug prints + dead code (`dd37ccdf`)
- fix(import_assets): finish multi-asset edit state (sidefx_lop_edit) (`1b058b9f`)

### Refactors
- refactor: migrate to tumbletrove.* namespace for asset_browser/radial SDK (`380a45e8`)

## v1.7.0 — 2026-06-08

### Features
- feat(import_model): department-gated Pack SOP for blendshapes (`bba76a3a`)

### Fixes
- fix(import_rig): exclude empty categories from asset listings (`aa5094e7`)

### Refactors
- refactor(config): extract is_terminal_entity, fix scene asset listing (`abadcb78`)

## v1.6.0 — 2026-06-08

### Features
- feat(asset_browser): entity lifecycle — bucket create/delete + asset delete (`ab375704`)
- feat(asset_browser): rewrite asset hover popup as a widget tree with dept icons (`d587bab9`)
- feat(asset_browser): auto-import latest on workfile open (`1a8ed8ed`)

### Fixes
- fix(import_rig): refresh entity cache so new categories appear (`ba639b7c`)
- fix(validators): expect 'mtl' material scope, not 'mat' (`cbed5d58`)
- fix(asset_browser): restore mix_hex + import QSize in dept section (`6580ac8a`)
- fix(export): localize payload sidecars into the version folder (`f5429545`)
- fix(asset_payload): stop primpath duplication when adding payload (`ef563f20`)
- fix(hpm): migrate manifest to 2.0 schema and admit Houdini 22 (`a94072f2`)

### Refactors
- refactor(create_model): author model materials under /mtl, not /materials (`a1f8efc2`)

### Tests
- test(export): add in-app verification harness for asset_payload fixes (`2688dcc6`)

### Chores
- chore(release): bump version to 1.6.0 (`1b63df20`)
- chore: clean up stale references after the Manifest 2.0 / build->pack work (`1ee967f3`)

### Other Changes
- Merge remote-tracking branch 'origin/master' into feat/port-wip-features (`85d6cfe0`)
- hpm: bump max_version to allow Houdini 22 (`83065f86`)

## v1.5.0 — 2026-06-04

### Fixes
- fix(import): guard metadata update against empty-composed assets (`f1f47816`)
- fix(catalog): entity_uri_for requires exactly three id segments (`61b86612`)
- fix(catalog): one group-context classifier; drop bogus shot category key (`cdf3cf02`)
- fix(catalog): use numeric latest_version at the remaining version sorts (`104b1046`)
- fix(catalog): AssetResolver.split rejects non-three-segment ids (`9836a6f1`)
- fix(catalog): sort workfile_versions numerically, not lexically (`292eab3f`)
- fix(uri): reject empty path segments (`53593b5b`)
- fix(naming): is_valid_entity_name is ASCII-only (`0d552a97`)
- fix(paths): get_workfile_context degrades on partial context.json (`f7659c02`)
- fix(scene): read direct scene refs via own-properties, not merged (`0ffb4549`)
- fix(scene): match the list-shaped Scene.assets contract (`5e91b22d`)
- fix(naming): version validation rejects v10000 and accepts Unicode digits (`36ba5f5e`)
- fix(renderer): read settings from the store the writer writes to (`b5948bce`)
- fix(timeline): correct BlockRange.__len__ off-by-one for step > 1 (`b5b05a54`)
- fix(uri): preserve query on join and make hash agree with equality (`36f17607`)
- fix(export): drop stale "assets have no frame range" messaging (`32ff74b1`)
- fix(config): resolve entity schema by position when none is bound (`25fb406c`)

### Refactors
- refactor(catalog): dispatch container ops to ContainerManager (`b2949fe8`)
- refactor(catalog): move container operations into ContainerManager (`e48046fe`)
- refactor(catalog): add ContainerManager skeleton + wire into catalog (`20fe6085`)
- refactor(config): stop storing the per-node schema; derive everywhere (`232a3dd9`)
- refactor(validation): broken validators.py fails loudly (`814f9a86`)
- refactor(renderer): one source of truth for defaults (`8af754bf`)
- refactor(config): resolve entity schema by position only (single source of truth) (`88d9c0c6`)
- refactor(uri): flat dataclass; delete wildcard node hierarchy, db.py, parse() (`1d4b58fe`)
- refactor(timeline): validate FrameRange roll underflow at construction (`fd2b5241`)

### Documentation
- docs(catalog): record ContainerManager as the home of container behaviour (`d64aab74`)
- docs(tests): index test_validation and update schema-derivation wording (`db7eaf04`)
- docs(tests): document the asset_browser stub and test_catalog surface (`1de1bea3`)
- docs(tests): index the audit test surfaces in the harness README (`e3924cb4`)
- docs(tests): index the frame-range inheritance surface in the harness README (`08e9d0a7`)

### Tests
- test(department): guard default-flag resolution from sparse storage (`5108fef0`)
- test(catalog): cover catalog addressing + fix parse_entity_ref segment glue (`79fc6561`)
- test: add round-trip guards for config convention, variants, io, cache (`60dfc627`)

### Chores
- chore: remove dead scene_description_dialog (`4138801a`)

## v1.4.7 — 2026-06-01

### Fixes
- fix(export): abort instead of publishing a layer with a dangling payload (`f3836ab9`)
- fix(asset-catalog): reload config snapshot from disk on refresh (`c642eb2a`)

### Documentation
- docs(tests): index the export-guard path policy test in the harness README (`910b6297`)
- docs(tests): note the cache-coherency test category in the harness README (`af4a0707`)

### Tests
- test(config): pin external-write reload of the entity cache (`d288b1a4`)

## v1.4.6 — 2026-06-01

### Fixes
- fix(hpm): migrate manifest to 2.0 schema and admit Houdini 22 (`1bd9e0be`)

### Chores
- chore: clean up stale references after the Manifest 2.0 / build->pack work (`98131eee`)

## v1.4.5 — 2026-06-01

### Fixes
- fix(asset_browser): apply FPS and frame range when opening workfile (`4a7cfbe7`)

### Refactors
- refactor(asset_browser): extract SceneManager to _pipeline_scene (`af5f7fce`)
- refactor(asset_browser): extract DetailSectionBuilder to _pipeline_detail (`75fa802c`)
- refactor(asset_browser): extract WorkfileManager to _pipeline_workfiles (`74046448`)
- refactor(asset_browser): move PipelineCatalog to _pipeline_catalog (`3a09693f`)
- refactor(asset_browser): extract ThumbnailManager to _pipeline_thumbnails (`e6eb1be5`)
- refactor(asset_browser): extract DropRouter to _pipeline_drops (`e74d4c6e`)
- refactor(asset_browser): hoist DeptNameLabel / DeptMetaLabel to _pipeline_widgets (`01ad20fc`)
- refactor(asset_browser): polymorphic GroupContainer / SceneContainer (`57d86068`)
- refactor(asset_browser): extract AssetResolver to _pipeline_resolver (`158a49ae`)
- refactor(asset_browser): extract ClientPool to _pipeline_clients (`85fd754b`)
- refactor(asset_browser): route URI construction through _pipeline_uris (`4722e682`)
- refactor(asset_browser): extract Houdini bridge + ProjectActivator (`7e5c84cd`)
- refactor(asset_browser): unify asset and shot discovery into one pass (`4dcd8ba7`)
- refactor(asset_browser): add AssetRef/ShotRef sum type + helpers (`49a5780b`)
- refactor(asset_browser): extract DeptVersionStore from PipelineCatalog state (`e00d808f`)
- refactor(asset_browser): drop defensive except blocks around no-op ops (`71825362`)
- refactor(asset_browser): extract workfile globbing helpers (`6b54d342`)
- refactor(asset_browser): raise from collection ops instead of swallowing (`a30a2e6f`)
- refactor(asset_browser): consolidate dept short-name maps in one module (`1146ee3f`)
- refactor(asset_browser): drop bool return on _write_description (`805e9919`)

### Documentation
- docs(asset_browser): point header at the registry constraint (`0f2edc31`)

### Chores
- chore(asset_browser): retag the now-tiny "Detail panel layout" section (`ab24f9d0`)
- chore(asset_browser): drop _mix_hex residue + extend docs for the new managers (`ce520791`)
- chore(asset_browser): post-sweep cleanup + flesh out the docs (`a6da1d24`)
- chore(asset_browser): defensive sweep — flatten _publish_current_scene_impl (`6762a843`)
- chore(asset_browser): first pass of the defensive try/except sweep (`3345a9d5`)
- chore(asset_browser): post-refactor cleanup + document the catalog directory (`0832c0c7`)
- chore(asset_browser): tidy post-refactor stragglers (`21dfb382`)
- chore(asset_browser): clean up post-refactor stragglers (`96531669`)

## v1.4.4 — 2026-05-26

### Features
- feat(config): let set_properties attach a schema to schema-less leaves (`4d8df8d3`)

### Fixes
- fix(api): switch default_client lock to RLock to close re-entry deadlock (`943242a3`)
- fix(project_template): correct tumblehead.* imports to tumblepipe.* (`0bef314f`)
- fix(config_convention): don't treat root entity's own props as inherited (`c3a34a0c`)
- fix(config/department): write to departments:/ to match the read side (`0ceab2d2`)
- fix(config/farm): point pool/priority writers at entity:/ root (`f37bddb7`)
- fix(asset_browser): apply FPS and frame range when opening workfile (`ad2b3677`)

### Refactors
- refactor(asset_browser): unify asset and shot discovery into one pass (`caa98493`)
- refactor(asset_browser): extract DeptVersionStore from PipelineCatalog state (`1d25573c`)
- refactor(asset_browser): cut ~40 defensive except blocks that hid bugs (`040dc1c8`)
- refactor(asset_browser): raise from collection ops instead of swallowing (`483164dc`)
- refactor(asset_browser): consolidate dept short-name maps in one module (`dd353c5c`)
- refactor(asset_browser): drop bool return on _write_description (`0f3f893d`)
- refactor(asset_browser): extract workfile globbing helpers (`7eeff298`)
- refactor(asset_browser): replace AssetId with AssetRef/ShotRef sum type (`9804e105`)

### Documentation
- docs: README + dev guide reflect the three-convention layout and tests/ (`ff09e458`)
- docs(configuration): drop stale render_convention.py bullet (`732c20cf`)
- docs(asset_browser): document why pipeline.py can't split into a package (`603d3b2d`)

### Tests
- test: property-based tests for the config layer (uv + minigun-soren-n) (`596c0f63`)

### Chores
- chore(tests): drop the tumblehead→tumblepipe convention patch shim (`d438205d`)
- chore(otls): drop stale .OPdummydefs / .OPfallbacks from create_asset_model (`062286d6`)
- chore(asset_browser): clean up post-refactor stragglers (`80ad0e09`)

### Other Changes
- merge: take upstream asset_browser_catalogs/pipeline.py + _pipeline_types.py (`a567837d`)
- merge: absorb upstream feature work outside asset_browser/pipeline.py (`c98323b9`)

## v1.4.2 — 2026-05-20

### Features
- feat(asset_browser): PipelineCatalog.get_asset_hover_html (`3ab9548f`)
- feat(asset_browser): collection-scoped pill counts in PipelineCatalog (`ccff887e`)
- feat(otls): import_model mirrors inner import_layer's bypass state (`2be01257`)
- feat(asset_browser): drop assets into SOP networks as th::import_model (`12cb40c3`)

### Fixes
- fix(otls): import_model — swap Variant parm for Department picker (`6d7738cc`)

## v1.4.1 — 2026-05-19

### Features
- feat(asset_browser): autosave-on-scene-change toggle + quick-action hover content (`94cf8cfe`)

### Fixes
- fix(otls): default entity to 'from_context' + zero-index Active Variant (`3054b0c9`)

### Chores
- chore(asset_browser): drop stderr debug prints from pipeline catalog (`3d4a4644`)

## v1.4.0 — 2026-05-19

### Features
- feat(validators): department-aware export validation with grouped issues + suggestions (`b68003c8`)
- feat(asset_browser): Multi + Root container support (`0524de55`)
- feat(otls): wire entity-derived prim path into import/layer output modifiedprims (`c9970e27`)
- feat(otls): entity/path toggle + jump button on create_asset_{model,lookdev} (`cdd571c0`)

### Fixes
- fix(otls): refresh entity/department labels in setters (`9a6466f8`)
- fix(asset_browser): network thumbnail on asset drop (`279cb1aa`)

### Performance
- perf(asset_browser): fast-path _activate_project + rename invalidate_cache (`d4a53744`)

### Refactors
- refactor(asset_browser): make attach_network_thumbnail public (`516d63c1`)

### Other Changes
- scaffold: bundle _config/templates/ for assets + shots departments (`ebd98e04`)

## v1.3.2 — 2026-05-15

_No user-facing changes._

## v1.3.1 — 2026-05-15

_No user-facing changes._

## v1.3.0 — 2026-05-15

### Features
- feat(otls): import-asset/shot lifecycle callbacks for network thumbnails (`8bcd69e3`)
- feat(radial): bundle TumblePipe radial menus + register at startup (`845df812`)
- feat(asset_browser): submit-jobs dialog + drop status column from list view (`8be35e2b`)

### Fixes
- fix(asset_browser): always invoke quick-action refresh_cb in finally (`f1a16169`)

### Chores
- chore(tools): local dev build script (`cb056101`)

## v1.2.3 — 2026-05-15

_No user-facing changes._

## v1.2.2 — 2026-05-15

### Fixes
- fix(tt_setup): make project-setup wizard legible on dark themes (`bd83950f`)

### Refactors
- refactor(tt_setup): consolidate status-label updates into helpers (`6118d97a`)

## v1.2.1 — 2026-05-06

### Fixes
- fix(ci): discover Houdini install path on linux + defensive checks (`2d745544`)

## v1.2.0 — 2026-05-06

### Features
- feat(catalog): typed PipelineAssetMetadata schema (P6c partial) (`17848427`)

### Fixes
- fix(catalog): pipeline.py absolute import (was: relative, broke discovery) (`1ec3175d`)

### Refactors
- refactor(catalog): extract types & constants to _pipeline_types (P4 partial) (`0e38f098`)
- refactor(catalog): typed AssetId for 3-segment ids (P6b) (`9784cd57`)
- refactor(catalog): drop start_frame/first_frame version-skew fallbacks (P9) (`4530d199`)
- refactor(catalog): import value types from api.types (P6d) (`01e6949e`)
- refactor(pipeline): adopt Catalog lifecycle hooks (P8 partial) (`1f1ba5d1`)
- refactor(catalog): typed errors + ClientSlot state machine (P0b + P7) (`c69101c9`)
- refactor(catalog): purge disabled render/playblast subsystem (P3) (`72680a59`)

### Documentation
- docs(catalog): module docstring reflects env-wins-on-bootstrap (`6a3e948e`)
- docs(catalog): update bootstrap_from_env call-site comment (`6a364546`)
- docs: refresh stale _ensure_client reference in get_available_tags (`7acbac81`)

## v1.1.27 — 2026-05-06

_No user-facing changes._

## v1.1.26 — 2026-05-06

### Features
- feat: tt_setup project wizard (`c68f377b`)

### Other Changes
- project_template: drop spurious +x bits (`e06e4b81`)
- config: default TH_CONFIG_PATH and TH_EXPORT_PATH to project subpaths (`107d6ad1`)

## v1.1.25 — 2026-05-06

### Other Changes
- asset_browser_catalogs: disable render + playblast disk discovery (`1e8441cc`)

## v1.1.24 — 2026-05-06

### Features
- feat(asset_browser): pipeline detail tabs + drop upgrades (`d119ac8f`)

### Other Changes
- asset_browser_catalogs: don't block Houdini load on catalog initialize (`4780c4ff`)

## v1.1.23 — 2026-05-05

### Chores
- chore: bump to 1.1.23 — CI fix for new TumbleTrove API shape (`5348e4c3`)

## v1.1.22 — 2026-05-05

### Chores
- chore: bump to 1.1.22 — Houdini 22 support (`62552fc2`)

### Other Changes
- houdini_majors: link 'add a new major' to hpm.toml max_version bump (`af89b693`)
- hpm: cap max_version at the highest Houdini major we build for (`12d9c189`)
- python: drop unused 1x version segment from package path (`a76d7318`)
- python3.13libs: drop sys.path-walking dev-session fallback (`54836934`)
- python3.11libs: drop sys.path-walking dev-session fallback (`63e215ee`)
- python3.13libs: bring pythonrc.py in sync with python3.11libs (`06e7a2c5`)
- resolver(cmake): add python313 to bundled-python search list (`6bce3333`)
- hpm: route resolver env per Houdini major via $HOUDINI_MAJOR_RELEASE (`3a2d783a`)

## v1.1.21 — 2026-04-30

### Features
- feat(asset_browser): playblast preview cards + department short labels (`365761c8`)
- feat(asset_browser): add Render asset type — browse on-disk renders + dailies (`b8f1d0bf`)
- feat(asset_browser): consolidated Info tab + Tasks rename (`4509a0e1`)
- feat(asset_browser): ship TumblePipe brand logo as catalog icon (`d7d04b7c`)

### Fixes
- fix(asset_browser): shot drop fails for numeric-only shot names (`a45f29a2`)
- fix(asset_browser): always handle drops, never fall back to Place dialog (`73c4a11e`)

### Documentation
- docs(resolver): document imperative factory registration and file-based debug log (`e02ef02d`)

### Chores
- chore: bump to 1.1.21 — shot drop fix (`1f9deb06`)

## v1.1.20 — 2026-04-29

### Fixes
- fix(resolver): register Ar_ResolverFactory via standard CRT init, not AR_DEFINE_RESOLVER (`fbbdae21`)

### Chores
- chore: bump to 1.1.20 (`0ecf3b9e`)

## v1.1.19 — 2026-04-29

### Fixes
- fix(resolver): force-link C++ shim so AR_DEFINE_RESOLVER static init survives MSVC /OPT:REF (`f6c557b4`)

### Chores
- chore: bump to 1.1.19 (`5b7f71f5`)

## v1.1.18 — 2026-04-29

### Chores
- chore: bump to 1.1.18 (`0907a1f8`)

### Other Changes
- debug(resolver): add TH_RESOLVER_DEBUG stderr trace at every ArResolver override (`e5b82230`)

## v1.1.17 — 2026-04-29

### Fixes
- fix(hdas): resolve entity URIs to filesystem paths before writing sublayer parms (`ed2bafc1`)

### Refactors
- refactor(scene): route get_root_layer_path through get_root_layer_file_name (`1c3d2580`)
- refactor(paths): centralize root-layer filename, drop unused export-file wrappers (`8cf707eb`)

### Chores
- chore: bump to 1.1.17 (`1910b30f`)

## v1.1.16 — 2026-04-28

### Fixes
- fix(asset-browser): treat TH_PIPELINE_PATH as hpm-owned, not per-project (`6a2dd608`)

### Chores
- chore: bump to 1.1.16 (`25a4a5d7`)
- chore: drop dead /target/ ignore (handled by resolver-src/.gitignore) (`92054b69`)
- chore: ignore .claude/ (per-developer Claude Code harness state) (`c8c36964`)

## v1.1.15 — 2026-04-28

### Fixes
- fix(ci): pass PEM directly to hpm pack --key (v0.9.1 contract) (`f5a4d0a5`)

### Chores
- chore: ignore .mcp.json (per-developer MCP config) (`e131898f`)

## v1.1.14 — 2026-04-28

### Fixes
- fix(release): ship resolver/ in archive via hpm v0.9.1 native semantics (`b4133951`)

## v1.1.12 — 2026-04-28

### Fixes
- fix(resolver): register via hpm.toml env, flatten install layout (`e2b9e7dc`)

### Refactors
- refactor(hdas): import_asset/import_layer set sublayer parms to entity URIs (`96666724`)

### Chores
- chore: anchor /resolver/ ignore and add /target/ (`9ca63d35`)
- chore: bump to 1.1.12 to supersede broken 1.1.11 release (`b78b2854`)

## v1.1.11 — 2026-04-27

### Chores
- chore: bump to 1.1.11 — add asset_browser_catalogs to INCLUDE_PATTERNS (`f032134c`)

## v1.1.10 — 2026-04-27

### Fixes
- fix(hda): fix active_variant off-by-one in create_asset_model (`0c6b2aa2`)
- fix(resolver): defer pxr.Ar import so pythonrc can register TumbleResolver (`de9bae21`)

### Chores
- chore: bump to 1.1.10 for resolver registration fix (`7639f37f`)

## v1.1.9 — 2026-04-23

### Features
- feat(asset_browser): ship TumblePipe catalog as external catalog (`75f8c8e9`)
- feat(desktop): add TumblePipe Solaris desktop layout (`a76afd45`)
- feat(hda): update create_asset_lookdev and export_asset (`200dfcbb`)
- feat(hda): update create_asset_model, create_asset_lookdev, and export_asset (`7edaa9aa`)
- feat(hda): import asset_thumbnail and export_asset HDAs from RND (`8a429c32`)
- feat(hda): update create_asset_lookdev active_variant and add sublayer parm (`866413e2`)
- feat(hda): refactor create_asset_model variant fetch to use context variable (`ee19c8e8`)

### Fixes
- fix(ci): use GET /v1/storage list to resolve storage public URL (`522d8c94`)
- fix(ci): update release script to use creator storage API (`38acfd72`)
- fix(ci): update storage presign URL to new /v1/storage/{id}/upload route (`8681b46f`)
- fix(ci): remove binary HDAs from otls — only decompiled dirs should be tracked (`4e22907a`)
- fix(hda): remove accidentally saved subnet contents from create_asset_lookdev (`0e8f3e0c`)
- fix(hda): remove auto-layout on variant add in create_asset_lookdev (`151b1444`)

### Chores
- chore: ignore compiled .hda files and backup dir in otls/ (`cc6544ad`)
- chore: bump version to 1.1.9 (`4b0faf0c`)

## v1.1.8 — 2026-04-22

### Fixes
- fix: normalize HDA icon paths to $TH_PIPELINE_PATH/resources (`5e16d3c4`)

### Documentation
- docs: trim README to point at readthedocs for deep content (`2e16b702`)
- docs: add Sphinx docs scaffold and Read the Docs config (`b1f343ca`)

### Chores
- chore: point hpm.toml documentation at readthedocs (`1b373881`)

## v1.1.7 — 2026-04-19

### Fixes
- fix(ci): scan Rust staticlib (not DLL) for FFI symbols on Windows (`a07fc81e`)

## v1.1.6 — 2026-04-17

### Fixes
- fix: inject MSVC /WHOLEARCHIVE via LINK_FLAGS so MSBuild passes it through (`a24e6e66`)
- fix: cross-compile Rust to match C++ target arch, use tested whole-archive form (`e74e4beb`)
- fix: preserve TumbleResolver plugin registration across linker strip (`c66d5419`)
- fix: set default TumblePipe desktop from uiready.py, not 123.py (`496e4832`)
- fix: publish_to_tumblepipe also skips HDA compile + guard for mirror (`d7ac389d`)

### Documentation
- docs: update resolver build notes for per-platform CI and symbol-preservation fix (`e79739ca`)
- docs: add TumblePipe Deadline job submission example (`166e0b4e`)
- docs: recommend TumbleTrove Desktop as primary install method (`86ed713e`)
- docs: add TumbleTrove Desktop as an install option (`2b5e649e`)
- docs: link HPM to hpm.readthedocs.io (`010eba87`)
- docs: update README for current HPM package structure (`18849853`)
- docs: mirror text-based HDA sources to github, not compiled binaries (`36e9bd00`)
- docs: restore original README.md from v1.0.2 (`9246c02b`)

### Chores
- chore: remove DJV render viewer and opentimelineio dependency (`c4d881bd`)

## v1.1.5 — 2026-04-17

### Fixes
- fix(build): feed hpm the 32-byte raw Ed25519 seed, not the PEM (`f256e554`)

### Other Changes
- Add Apache-2.0 LICENSE + include it in staged package (`6ae959ca`)

## v1.1.4 — 2026-04-16

_No user-facing changes._

## v1.1.3 — 2026-04-16

### Fixes
- fix(build): find hotl under C:\Houdini* (non-default install path) (`3405c70e`)

## v1.1.2 — 2026-04-15

_No user-facing changes._

## v1.1.1 — 2026-04-15

_No user-facing changes._

## v1.1.0 — 2026-04-15

### Features
- feat: move asset creation HDAs from Tumblehead into TumblePipe (`8adddb93`)
- feat: cutover to Rust-based tumbleResolver (`d343c784`)
- feat: C++ ArResolver shim + CMake build for tumbleResolver (`8f7572eb`)
- feat: Rust core for entity:// USD asset resolver (`9419d7be`)
- feat: declare macos-arm64 as a supported native platform (`08eecb31`)
- feat: set TumblePipe desktop as default on Houdini load (`13424bc9`)
- feat: initial TumblePipe Houdini package (`52df04b9`)

### Fixes
- fix(release): curl PUT — disable Expect/chunked/default CT, log URL (`98e31acc`)
- fix(release): only send Content-Type header if it is signed (`f64f17b2`)
- fix(release): upload archive via curl, not urllib (`11ecbf2c`)
- fix(release): resolve creator/slug to package id via /v1/creator/packages (`69c8c74b`)
- fix(release): send package path with literal slash, not %2F (`7dd72d37`)
- fix(release): parse hpm pack JSON out of mixed stdout (`3d110e82`)
- fix(release): rename macos slot macos-universal, add missing README (`61f83375`)
- fix(release): surface hpm pack stderr on failure (`bdd3372e`)
- fix(release): accept PKCS#8 Ed25519 keys with public-key attribute (`4fe8fcf8`)
- fix(resolver): skip strip on macos; link Houdini python on windows (`25c2b58d`)
- fix(resolver): link USD libs explicitly on windows, defer on macos (`23f1a176`)
- fix(resolver): stub _OpenAssetForWrite; widen windows tool discovery (`d18a0cee`)
- fix(resolver): use Houdini CMake package instead of bare pxr (`98f7ceea`)

### Refactors
- refactor: migrate metadata from /_METADATA prims to customData on scene prims (`e05164f4`)
- refactor: rename Tumblehead desktops to TumblePipe (`3cd6a876`)

### Chores
- chore: add .gitignore and remove tracked __pycache__ files (`f024536e`)

## v1.0.2 — 2026-03-29

### Chores
- chore: bump version to 1.0.2 (`b888242a`)
- chore: bump version to 1.0.1 (`bc23246e`)
- chore: bump version to 1.0.1 (`d1d594e5`)
- chore: bump version to 1.0.1 (`dfefad7a`)
- chore: bump version to 1.0.1 (`6f90d9df`)
- chore: bump version to 1.0.1 (`1f235c68`)
- chore: bump version to 1.0.1 (`fb2a37d5`)

### Other Changes
- Update TumblePipe framework from 42282b7f (`1a9822de`)
- Update TumblePipe framework from 42282b7f (`0fafd9a2`)
- Update TumblePipe framework from 42282b7f (`725d4e6b`)
- Update TumblePipe framework from 42282b7f (`b4c305c4`)
- Update TumblePipe framework from 1b09f532 (`73c563df`)
- Update TumblePipe framework from 1b09f532 (`bbf8b8b5`)
- Update TumblePipe framework from 1b09f532 (`85d9225c`)
- Update TumblePipe framework from 1b09f532 (`de773845`)
- Update TumblePipe framework from 1b09f532 (`0a2691d3`)
- Update TumblePipe framework from 1b09f532 (`d9ac61e9`)
- Update TumblePipe framework from 1b09f532 (`8ddcf40c`)
- Update TumblePipe framework from 1b09f532 (`e3cc8e30`)
- Update TumblePipe framework from 1b09f532 (`d2b56ca5`)
- Update TumblePipe framework from 1b09f532 (`fa9b46c9`)
- Update TumblePipe framework from 1b09f532 (`16dfc93e`)
- Update TumblePipe framework from 1b09f532 (`2fb5a535`)
- Update TumblePipe framework from 1b09f532 (`46d64318`)
- Update TumblePipe framework from 1b09f532 (`3e9de18a`)
- Update TumblePipe framework from 1b09f532 (`f65ae4a9`)
- Update TumblePipe framework from 1b09f532 (`ad1ad1a0`)
- Update TumblePipe framework from 1b09f532 (`d844eca5`)
- Update TumblePipe framework from 1b09f532 (`63273fd2`)
- Update TumblePipe framework from 3359f890 (`8f007588`)
- Update TumblePipe framework from 3359f890 (`5197f44e`)
- Update TumblePipe framework from d4f73dc9 (`46d30b3f`)
- Update TumblePipe framework from d4f73dc9 (`dc821aa6`)
- Update TumblePipe framework from d4f73dc9 (`07974a3a`)
- Update TumblePipe framework from d4f73dc9 (`1dc4d985`)
- Update TumblePipe framework from d4f73dc9 (`6653d71d`)
- Update TumblePipe framework from d4f73dc9 (`99424e17`)
- Update TumblePipe framework from d4f73dc9 (`5dc3e2c3`)

## v1.0.1 — 2026-01-25

### Features
- feat: add weekly automated release workflow and HPM manifest (`c80c2f84`)

### Chores
- chore: bump version to 1.0.1 (`b9d75959`)
- chore: bump version to 1.1.1 (`bb00c67c`)
- chore: bump version to 1.1.0 (`7706f479`)

### Other Changes
- Update TumblePipe framework (`4b7c9725`)
- Update TumblePipe framework (`9d89deb8`)
- Update TumblePipe framework (`76c06bc0`)
- Update TumblePipe framework (`fc267a86`)
- Update TumblePipe framework (`4310c820`)
- Update TumblePipe framework (`ffa4f517`)
- Update TumblePipe framework (`1629134a`)
- Update TumblePipe framework (`1cd7ad99`)
- Update TumblePipe framework (`5fbd8013`)
- Update TumblePipe framework (`80ae90f9`)
- Update TumblePipe framework (`21985089`)
- Update TumblePipe framework (`5d028646`)
- Update TumblePipe framework (`f7bceec5`)
- Update TumblePipe framework (`f667ff6c`)

## v0.2.0 — 2025-11-11

### Other Changes
- Update example config to match Growth project patterns (`72409019`)
- Expand README with Prerequisites and Render Farm sections (`cbe5cc0e`)
- Restore examples directory (`34ce63cc`)
- Update TumblePipe framework (`320e403a`)
- Update TumblePipe framework (`4b61a794`)
- Update Windows launcher for Houdini 21.0 (`79baf9d1`)
- Merge pull request #6 from tumblehead/prepare_release (`6661a33e`)
- Merge pull request #5 from tumblehead/prepare_release (`e72a7c35`)
- Merge pull request #4 from tumblehead/prepare_release (`62bf5031`)
- Merge pull request #3 from tumblehead:prepare_release (`ebe4ce99`)

## v0.1.3 — 2025-05-20

### Other Changes
- fix various bugs (`2033138b`)
- fix sync override (`8b834426`)

## v0.1.2 — 2025-05-15

### Fixes
- fix: update archive naming to use OS instead of platform (`778039fa`)
- fix: update archive naming to use OS instead of platform (`9e8f3b01`)

### Other Changes
- Merge pull request #4 from tumblehead/prepare_release (`62bf5031`)
- prepare v.0.1.2 (`996cb24e`)
- Merge pull request #3 from tumblehead:prepare_release (`ebe4ce99`)
- Merge branch 'prepare_release' of https://github.com/tumblehead/TumblePipe into prepare_release (`ed5950fc`)

## v0.1.1 — 2025-05-02

### Fixes
- fix: update download archives step to use merge-multiple option (`cde58eae`)
- fix: add merge option for downloading archives in release workflow (`04351411`)
- fix: remove .distignore and add .gitattributes for export-ignore configuration (`a08080ec`)
- fix: update download archives step to use merge-multiple option (`82f81865`)
- fix: add merge option for downloading archives in release workflow (`58f15a98`)
- fix: remove .distignore and add .gitattributes for export-ignore configuration (`e88c42cf`)

### Other Changes
- Merge pull request #2 from tumblehead/prepare_release (`21e88e4d`)
- Merge branch 'prepare_release' of https://github.com/tumblehead/TumblePipe into prepare_release (`02e4eb9e`)

## v0.1.0 — 2025-05-02

### Fixes
- fix: update step names for clarity in build workflow (`5a572dbb`)
- fix: replace zip build action with git archive command (`f3e56089`)
- fix: update action version for building zip artifact (`8a37f931`)
- fix: update setup-uv action reference in build workflow (`272474a5`)

### Other Changes
- Merge pull request #1 from tumblehead/prepare_release (`bcee49e0`)
- add prepare_release branch to build trigger (`05967f6b`)
- update the readme (`d64baee6`)
- add all the files (`071f05de`)
- Initial commit (`04202658`)
