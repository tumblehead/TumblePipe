# Upgrading

What changes for scripts, tools and project templates that reach into
TumblePipe beyond `tumblepipe.api` and the names the
shipped HDAs call (those keep working). Each entry also leads its release's
notes under **Breaking Changes** in the [changelog](changelog.md).

## v1.124.0

### Every light's diffuse_ and specular_ AOV is an LPE split

`comp.is_lpe_aov` (and so `aov_cable`, `aov_signature`, `is_colour_aov`)
takes any `beauty_<x>`, `diffuse_<x>` or `specular_<x>`, for any non-empty
suffix and in any case (`comp.LPE_PREFIXES`), where it took only the
light groups and the eight names of `comp.LPE_AOV_NAMES`; that tuple
stays, as examples. `diffuse_fill`, `specular_rimLight`, ... now ride
th::import_render's **lpe** cable (RGB, in the scene's colour space)
instead of **extra**. Plain `diffuse` and `specular` stay extras;
`light_groups` (a light mix's rows) is unchanged.

### A File COP asks for the plane as the EXR spells it

`exr.cop_plane_name(channel_names, aov_name)` returns the prefix of the
`<plane>.<channel>` channels that is `aov_name` in any case, else their one
prefix, still `C` for bare channels and `aov_name` when the channels are
unreadable or name several other planes. It returned `aov_name` for any
prefixed file, and a light's `diffuse_Fill.r` in folder `diffuse_fill`
read black. The farm's split (`exr.split_subimages`) now renames a part's
channels unless their plane is its lowercased name case for case (new
`exr.channel_needs_rename`), so it writes `diffuse_fill.r`. A channel's
plane is everything before its last dot (new `exr.channel_plane`), for the
split and `cop_plane_name` alike: a channel that only starts with the
part's name (`color.R` in part `c`, `beauty_fill_rim.r` in `beauty_fill`)
is renamed too, and `exr._rename_channel` keeps the suffix after the last
dot (`a.b.z` becomes `<part>.z`, no longer `<part>.b.z`).

### th::import_render 2.0: Import Geometry and Import Camera

New parms `import_geometry` and `import_camera` (on by default), with
`import_render.IMPORT_GEOMETRY_PARM` / `IMPORT_CAMERA_PARM`,
`ImportRender.imports_geometry()`, `imports_camera()`, `scene_wanted()`,
`wired_scene_outputs()` and the pure `import_render.scene_state(geometry,
camera, camera_problems, version_label, wired=())`. With both off
`ImportRender.import_scene()` and `execute(scene=True)` import nothing and
close both outputs; with geometry off `import_scene()` sets the embedded
`th::import_shot`'s `loadpayloads` to 0. A 1.0 node has neither parm and
imports both. `import_render.dive_outputs_missing(dive)` is new: a dive
short of `DIVE_OUTPUTS` (saved before v1.121.0) is rebuilt, and OnLoaded
adds its missing outputs after the load.

## v1.123.0

### A comp of a check render goes on any frames inside its range, the frames it lacks held

`comp.check_render_refusal(reads, frames)` now refuses only a comp whose
range (`first` to `last`) reaches outside a check render's recorded range,
or a check render whose range is not known (Soren, 2026-11-23: a Step 1
comp of a Step 2 render). The message names the range to comp inside
(`comps …, reaching outside check render …: comp inside 1001-1122, or
render the channel in full (Step 1), or pick a published version`), no
longer a Step. Without `frames` it still refuses every check render. A
frame inside the range the check render lacks is held from the nearest
frame it has, as th::import_render's Missing Frames holds it. New in
`comp`: `frames_in_range`, `frames_held(frames, checks)`,
`held_frames(reads, frames)`, `held_text(frames, checks)`,
`held_note(reads, frames)` (`every frame of 1001-1122 from a render every
2nd frame: 61 frames held`, empty when none is held) and
`slap_frame_token(frame_range)`. `check_comp_note` takes an optional
fourth argument, `held` (a `held_text`), which leads the note when given.
`held_text` and `held_note` take an optional `shows` (`comp.HELD`, the
default; `comp.BLACK`; `comp.HELD_UNLESS_EMPTY`), and
`comp.missing_note(reads, frames)` takes each import's `(Missing Frames,
read)`: the frames an import holds are `held`, those an import on Empty
leaves black `black (Missing Frames: Empty)`, as its File COPs read them.
Submit Composite's check comp list and the re-comp worker's log use it;
Farm Submit's Warnings line, which cannot know the imports, says `held
(black on an import set to Missing Frames: Empty)`.
`frames_inside`, `frames_that_fit` and `fit_text` are removed.

`farm.specs.submission.stepped_comp_problems` is removed: Farm Submit takes
a Comp beside a stepped render on any frames inside the render's, and one
reaching past it is refused by `comp_frame_problems` as before.
`stepped_comp_frames(settings)` returns `(comp, render)` frames, each
`(first, last, step)`, for a Comp that reads the stepped render beside it,
and the Warnings column (`farm.submit.settings.comp_step_warnings`) names
the frames it holds. `export_comp.ShotSubmit` gained `held` (its
`comp.held_note`), which `check_comps` lists. The re-comp worker
(`composite_houdini.resolve_imports`) logs a `WARNING:` line naming the
frames it holds, and a scene comp (#80) of a stepped render reads each
frame through `comp.slap_frame_token`, holding the nearest rendered frame.
Every comp that reads a check render is still a check comp, never
published.

## v1.122.0

### A comp of a check render goes on the frames it has; stepped comps are not published

`comp.check_render_refusal(reads)` used to refuse any read of a check
render. It now takes the comp's frames, `check_render_refusal(reads,
frames=(first, last, step))`, and refuses only when a check render read
lacks one of them (Soren, 2026-11-22), naming the Step that would go.
Called without `frames` it refuses every check render, as before.
`Resolved` gained `check_range` (a check render's recorded first and last
frame; `comp.check_frames(read)` gives its frames), and `comp` gained
`frames_inside`, `frames_that_fit`, `is_check_comp` and `check_comp_note`.
`ImportRender.import_states(frames=None)` passes them on; the new
`resolve_states()` returns the reads with the states, without the check.

A shot comp on a Step above 1, or one that reads a check render, is now a
*check comp*: its frame stacks record `"published": false` in their
`context.json`, no daily is written and Copy to edit is skipped. Before,
a stepped comp published like any other. The composite pipeline config
takes an optional `settings.reads_check_render` (bool; Submit Composite
sets it), and each composite job's config, and its worker's context, carries
`comp_frames = [first, last, step]`. In
`farm.workers.composite_houdini`, `pin_imports` now returns
`(problem, reads_check_render)` and `resolve_imports(entity_uri, frames)`
returns `(plan, reads_check_render, problem)`; `record_reads` takes a third
argument, `check_render`.

Farm Submit takes a Comp beside a stepped render when every comp frame is
one the render makes. `farm.specs.submission.problems` no longer says
`Comp follows a published render, and stepped renders are not published`;
`stepped_comp_problems(settings)` says `Comp beside a stepped render comps
only frames it renders: …` for one that does not. Such a comp reads that
render: `composite.chained_config(..., follows_check_render=True)` sets
`settings.reads_check_render` and, for a re-comp, a top-level `follows`
(`[{channel, department, version}]`, also on each composite job and its
worker context), which the worker pins the imports to (`comp.follow_pins`;
`resolve_imports(entity_uri, frames, follows)`).

### A comp's daily and Copy to edit read its `published` flag when they run

A comp that learns on the farm that it reads a check render (a Farm Submit
Comp alone resolving its imports, an import pinned to a check render) is
marked `"published": false` by its worker, after its daily and sync were
queued (Soren, 2026-11-23). The mp4 job config and worker context take two
optional keys, `daily_paths` (the outputs that are dailies, each one of
`output_paths`) and `publish_contexts` (the `context.json` paths of the
versions the dailies publish); the sync job config and context take
`publish_contexts`. When one of those records `"published": false`, the mp4
worker writes the other outputs and no daily, the sync worker copies
nothing, and both print a `WARNING:` line and exit 0. The composite pipeline
sets them; the render pipeline's configs are unchanged. A missing
`context.json` or `published` key is published. New helpers:
`comp.unpublished_reason(contexts)`, `comp.read_unpublished_reason(paths)`,
`farm.checks.valid_publish_contexts`, `farm.workers.mp4.outputs_to_write`;
`post_render.layer_mp4_job`, `slapcomp_mp4_job` and `sync_job_for` take an
optional `publish_contexts`.

### `cinedex --shot-audio` takes a working version, and a place wins over a name

`--shot-audio entity:/edits/<name>@vNNNN_working` now mixes that working
version; `@vNNNN` is still a published version and a bare name the newest
working one. Load Audio from Edit always names the version it picked the
tracks in, and `pipe.shot_audio.export_shot_audio` raises (writing no WAV
and no sidecar) when Cinedex reports another version. `--tracks A2` now
means the second audio track even when another track is named `A2`; before,
both were mixed. A tool of your own that picks tracks by a name looking like
a place (`A1`, `A2`...) passes the place instead. Each mix now has a sidecar,
`<wav>.json` (edit, version, places, track names), which the refresh on
workfile open reads; `pipe.houdini.shot_audio.load` and `export_shot_audio`
take `places`.

### Failed config writes leave nothing behind

`config.store` drops a purpose's cached tree when writing it fails, so the
session reads disk again instead of a change that never landed.
`config.groups.drop_member_everywhere_in` and `rename_member_everywhere_in`
are all or nothing: when a Multi write fails they put back the Multis they
already wrote before raising. `entity_files.rename_entity` renames the entity
back when updating its Multis fails, and `entity_ops.delete_bucket` takes a
deleted category or sequence out of the Multis that list it.
`config.flat_entities.removable_buckets` / `removable_buckets_in` name the
empty buckets the flat toggle offers to remove.

## v1.121.0

### `comp.resolve_render` can return a check render for `latest`

`resolve_render(..., version="latest")` used to raise `NotRendered` when a
department had only check renders (Farm Submit's **Step** above 1). It now
falls back to the newest check render that has all of its stepped frames,
when no department asked has a complete version (Antoine, 2026-11-22). A
complete render still wins over a newer check render; `Resolved.newer_check`
names the one skipped. A tool of your own that treats any `Resolved` as the
shot's final render checks `check_render_refusal` first, as Submit Composite
does. `NotRendered`'s message now ends in "(nor a check render)".

### th::import_render 2.0 has a tenth output, `extra`

The new `extra` cable comes after `camera`, so existing wires keep their
place. It carries every AOV no other output takes (transmission, sss,
emission, shadow, plain diffuse and specular, motion vectors, custom names).
Depth now reads as Mono. The diffuse/specular key, dome, direct and
indirect splits ride `lpe`. `beauty_mse` is no longer treated as a light
group; it is skipped with the other `*_mse` variance passes. `dive_plan`
gained `extras=True`; pass `extras=False` for a 1.0-shaped plan. 1.0 nodes
get no `extra` output and read no extras, but their depth is Mono too.

### Edits with converted clips need Cinedex 1.121.0

A clip made by **Convert to Shot** stores its animatic in the clip's new
`original` field. Older Cinedex versions refuse to open a working edit
that has one (the document format rejects unknown fields). Edits without
converted clips save byte-for-byte as before, and a publish drops
`original`, so published versions open anywhere. **Join with Next Clip** is
refused when only one of the two clips has an original, or when their
originals don't continue each other.

### A shot's root layer is rewritten when its range or fps changes

The shot root layer always carried the shot's frame range (with rolls) and
fps, but was never rewritten after the first time. It is now regenerated
when they differ from the config: at Entity Editor Apply, Database Editor
Save, workfile creation and staged builds. Expect a new root layer version
on shots whose range was edited since they were created. Edits made
elsewhere (for example the tumblepipe MCP's `set_shot_frame_range`) catch up
at the next build or editor sync.

### Flat asset and shot lists

`config:/project` may now hold `flat_assets` and/or `flat_shots`, each the
name of a hidden category or sequence. Entity URIs keep their depth
(`entity:/shots/<bucket>/<shot>`), so code that reads URIs is unaffected.
Code that creates entities in a flat project puts them in that bucket and
refuses a name any bucket of the kind already holds (by casefold); see
`tumblepipe.config.flat_entities`. The tumblepipe MCP's `create_asset` and
`create_shot` don't know the setting yet: pass the bucket as the category
or sequence. In every project, the Asset Browser's list view of shots now
shows a **Sequence** column where it showed an always-empty Category one.

## v1.120.1

### Farm Submit's `department` no longer pins the Render cut

`open_submit_jobs_dialog(..., department=...)` and `SubmitJobsDialog(...,
department=...)` now pin only the Playblast **Up to** to that department; the
Render **Up to** stays each shot's configured cut, else its latest department
turned on (Remy, 2026-11-20). A tool of your own that opens Farm Submit to
render up to a given department passes `render_department=` as well, as
th::import_render's **Render** button now does.

### A farm render through a camera that is not on the stage is refused

The collapse job now fails a render whose RenderSettings `camera` names a
prim the composed stage does not have (typically `/cameras/camera1`, the
default of Houdini's Karma and Render Settings LOPs, left as created in a
department workfile), naming the cameras the stage has. Such renders used to
come back from no camera. Set the Camera on that department's render settings
node and publish, or pick the camera in Farm Submit's new **Camera** field.
A settings prim with no `camera` target at all is left to husk, as before.

## v1.119.0

### Python names removed or moved

Nothing in TumblePipe called these. Code of your own that does needs the
change on the right:

| Gone | Use instead |
| --- | --- |
| `tumblepipe.pipe.houdini.rebuild` | nothing; it served the removed legacy Project Browser |
| `tumblepipe.util.nested` | nothing; its users moved into the comp builder |
| `config.renderer.RangeSetting`, `RendererDefaults`, `DEFAULT_RENDERER`, `get_renderer_defaults`, `get_tile_count_range`, `get_batch_size_range`, `get_timeout_range`, `get_denoise_default`, `set_renderer_setting` | nothing reads `config:/renderer/settings`; a project that holds it keeps it, unused |
| `apps.exr.dwab_encode`, `apps.houdini.IConvert` | nothing; farm tasks never run iconvert |
| `config.store.stamp_ttl` | `config.store.set_stamp_ttl` still sets it |
| `config.multi_timeline.place`, `pack`, `place_members` | the same functions in `tumblepipe.config.groups` |
| `ImportAssets.get_asset_imports`, `ImportRigs.get_rig_imports` | `get_row_imports()` on either (`pipe/houdini/asset_rows.py`) |
| `ImportAssets`' entity helpers (`get_department_name`, `set_department_name`, `require_department_name`, `pick_entity`, `list_asset_uris`, `list_shot_uris`, `get_entity_type`) | none; `ImportAssets` imports rows of assets and no longer has an entity of its own |

### `python -m tumblepipe.prune_app` removed

The old Prune Old Exports command is gone. Run `python -m tumblepipe.cleanup`
instead, or use **Clean Up Project** in TumbleTrove Desktop, which covers old
exports along with the other kinds of clean-up.

### Project files written with LF and UTF-8 on Windows

On Windows, `_config/db/*.json` files and local farm queue files are now
written with LF line endings and a final newline, as `tt_prepare` already
wrote them. Text files written by `util.io.store_text` are now UTF-8
rather than the system code page. Every reader in TumblePipe ignores the
change, so no migration is needed. A file a tool of your own compares byte for
byte shows every line changed the first time TumblePipe rewrites it.

### Generated radial menus written to your own folder

The Alt+R recipes and Alt+F favourites menus (`tumblepipe_recipes.json`,
`tumblepipe_asset_favorites.json`) are now generated into a per-user folder
(see [Radial menus](radial-menus.md)), never into the package's
`radial_menus/`, and only in a graphical session. Copies an older TumblePipe
wrote into the package's `radial_menus/` are ignored and can be deleted.

### `config.scene.set_scene` takes a list only

`set_scene` and `set_scene_in` take the scene's assets as a
`list[AssetEntry]`. The older `dict[Uri, AssetEntry]` form is no longer
accepted; pass `list(assets.values())`.
