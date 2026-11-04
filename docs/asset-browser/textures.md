# Texture sets

A **texture set** is a versioned group of images: base colour, roughness,
metalness, normal, height, AO and opacity, any of them. A set is published
like a cache, with its own versions and not as a department. It belongs to
an **asset** (the chair's wood) or to the **project library** (a concrete
every asset can use). Each version also carries a ready MaterialX material
that reads its images, so importing a set is one node.

## Where they are

```
export/assets/<category>/<asset>/_textures/<set>/vNNNN/   # an asset's sets
export/textures/<set>/vNNNN/                             # the project library
    <set>_diffuse.exr        # one file per map, named by role
    <set>_rough.exr          #   (<set>_<role>.<UDIM>.exr for a tiled map)
    <set>.usda               # the MaterialX material, reading ./<files>
    context.json             # written last: the version is finished
```

A version counts once its `context.json` is there, so a publish that failed
halfway is never read. A version whose `context.json` cannot be read (cut
short, hand-edited, a sync tool's conflict copy) is skipped the same way. The
newest *finished* version is the latest. Set names
follow the entity name rule: letters and digits only. Two sets of one owner
cannot differ only by case (`rock` and `Rock` would be one folder on Windows
and macOS): publishing the second is refused, and a library drop publishes
into the existing spelling.

`context.json` records each map's role, file, colour space and source, who
published it, and from which workfile and node.

## Colour spaces

The colour space comes from the map's role, not its file extension:

| Map | Colour space |
|---|---|
| Base colour | `sRGB - Texture`, or `ACEScg` for an EXR (linear data) |
| Roughness, metalness, normal, height, AO, opacity | `Raw` |

The space is written into the material's image readers and into
`context.json`. The names are the project OCIO config's
(`_config/ocio/tumblehead.ocio`). [`th::export_texture`](../nodes/assets.md#thexport_texture-cop)
writes the files to match: only the base colour is converted, and every data
map is written raw, so a roughness of 0.25 stays 0.25 in the file.

## Publishing

- **From Copernicus:** wire layers into a [`th::export_texture`](../nodes/assets.md#thexport_texture-cop)
  node (one input per role), set **Texture Set**, and press **Publish**. Every
  connected input is written and published as the next version.
- **From the library:** in an asset's workfile, **Shift**-drag a Poly Haven or
  ambientCG texture set and pick **Asset (*name*): publish**. The set is
  published as a version of one of the asset's sets, named after the library
  set (`wood_floor_deck` becomes `woodFloorDeck`). The nodes the drop builds
  read that version. Dropping the same files again reuses the version and
  publishes nothing new. Once picked, Asset is the default for plain drops,
  as every destination choice is. This needs TT Asset Browser 0.54.0 or
  newer.

## Finding and dropping them

Every set with a finished version is a card in the Pipeline catalog,
tagged `type:texture`, with its base colour as the thumbnail when it is a PNG,
JPEG or TIFF. Cards show up in the **All** view and in search. A card's
metadata names its owner, its newest version and its maps.

Dropping a card reads that version where it is. It is never copied, and the
Shift-drop destination question is not asked:

| Drop into | You get |
|---|---|
| LOPs | A [`th::import_texture`](../nodes/assets.md#thimport_texture-lop) pinned to the newest version, wired to the selected node |
| `/mat`, a material network or a Material Library's inside | A MaterialX material with every map wired into the surface |
| Inside a material | The maps, wired into its standard surface |
| Copernicus | A File COP on the base colour |

Paths are written `$TH_EXPORT_PATH/...`, so a scene works on the farm and on
other machines.

## Versions

[`th::import_texture`](../nodes/assets.md#thimport_texture-lop) pins the
version it took. A newer publish changes nothing in the scene: the node's
**Status** says `v0003 is newer, press Update`, and **Update** takes it. A
drop into a shading network writes the version into the file paths, so it
stays on that version until the maps are dropped again.

## Not yet

- Publishing from Copernicus runs in the session. There is no farm job for it
  yet.
- Texture sets have no sidebar section and no per-version detail view. The
  card shows the newest version.
- No `.rat` or `.tx` mipmaps are made at publish.
