# Upgrading

What changes for scripts, tools and project templates that reach into
TumblePipe beyond `tumblepipe.api` and the names the
shipped HDAs call (those keep working). Each entry also leads its release's
notes under **Breaking Changes** in the [changelog](changelog.md).

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
