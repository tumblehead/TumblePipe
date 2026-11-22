# Upgrading

What changes for scripts, tools and project templates that reach into
TumblePipe beyond `tumblepipe.api` and the names the
shipped HDAs call (those keep working). Each entry also leads its release's
notes under **Breaking Changes** in the [changelog](changelog.md).

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
