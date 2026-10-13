# VRAM Inspector

**TumbleTrove ▸ TumblePipe ▸ VRAM Inspector** shows what in your session is
using the graphics card's memory, down to the node, the prim, the primvar and
the texture file.

It measures with its own viewport hook, a small plug-in that ships with
TumblePipe (Houdini 22). While each viewer redraws, the hook records the GPU
memory of every prim it draws, so **viewer geometry is measured per USD prim**,
not estimated. Everything else (the texture cache, Copernicus's buffers, the
OpenCL pool, Karma XPU) comes from Houdini's own GPU memory registry. The
inspector puts those measurements next to an itemised reading of the scene,
so you can see which node to cut.

The inspector doesn't use Houdini's `gpumem` command. Its viewer-geometry
figure counts a prim drawn in several parts more than once (on one lighting
shot it reported 1.5 GB for 430 MB of geometry), and it can crash Houdini
right after a prim is hidden.

## The summary line

```
Houdini process: 1.67 GB dedicated · Measured: 473 MB across 3 clients · not reported by Houdini: 1.21 GB
```

- **Houdini process**: Windows' figure for the whole Houdini process, the
  same as Task Manager's "Dedicated GPU memory" column. Windows only.
- **Measured**: what Houdini's clients add up to, including how much of it is
  cached (held but not in use, so it can be freed).
- **not reported by Houdini**: the difference. This covers window surfaces
  and render targets, the driver, and anything that doesn't register with
  Houdini's memory registry (plug-ins, other renderers). The inspector can't
  break it down.

## The tree

One top-level row per client, in bold, with its measured size. Open one to
see where that memory comes from:

| Client | Opens into |
|---|---|
| Vulkan Geometry (*desktop.pane.solaris*) | the LOP nodes that made the drawn prims ▸ prims (each **measured**) ▸ topology, points, normals, each primvar, the viewport's own buffers |
| Vulkan Texture Cache | every texture file the drawn materials read: resolution, channels, bit depth, UDIM tiles, how many materials use it |
| COP OpenCL Buffer Cache | Copernicus nodes ▸ the layers each one holds |
| OpenCL Buffer Pool | simulations and OpenCL SOPs (measured only) |

A prim is listed under the node you placed. A prim made inside a SOP Create
or a digital asset is listed under the SOP Create or the asset, not under
the node buried inside it.

Prims loaded from published USD files (a TumblePipe import, a reference, a
sublayer) carry no record of the node that loaded them in this session. They
are listed under their top-level prim path instead, such as `/SET` or
`/CHAR`.

Prims the viewer drew but the scan didn't itemise are grouped under
**(drawn, not itemised)**. Usually these are `/__consolidated…__` prims, where
the viewport has merged many small meshes into one; their memory is measured
like any other prim's.

### Three columns, from fact to estimate

- **VRAM**: measured on every client row and on every viewer prim. Below a
  prim (its topology and primvars), and under textures and Copernicus, it is
  the parent's measured figure split by **Upload**, so it always adds up.
  The bar under the figure shows the row's share of its parent.
- **Upload**: what the viewport puts on the GPU for the row, worked out
  from the scene with the viewport's own rules (see below).
- **Data**: what the scene itself holds. This is exact.

**Data** and **Upload** can differ a lot. An 8K texture holds 192 MB of
pixels, but the viewport caps it at 2048² and uploads about 21 MB. A primvar
no material reads uploads nothing at all.

### Coverage

A client row's details compare the measurement with the scan:

- **measured per prim · the size model predicts 118%**: viewer geometry. Every
  prim's VRAM is measured; the percentage only grades the Upload model.
- **scan explains 100%**: the itemised rows add up to the measurement.
- **scan explains 40%**: the client holds something the scan cannot see.
- **scene asks for 3.0× this, evicted or reduced**: the materials want more
  textures than the cache holds, so Houdini is evicting textures and swapping
  them back in. Raise the texture cache size, or shrink the textures that
  head the list.

## How the Upload column is worked out

It follows the viewport's own rules, calibrated in Houdini 22.0's Solaris
viewport:

- **Textures**: capped to the viewport's 2D texture resolution limit (2048² by
  default) with the aspect ratio kept, RGB stored as RGBA, at the file's own
  bit depth, plus a third for mipmaps. A material's textures are the ones its
  drawn network reads: the MaterialX or Karma output when the material has
  one, otherwise its universal output.
- **Meshes**:
  - the triangle index buffer (12 bytes per triangle);
  - the wireframe (8 bytes per face-vertex);
  - points and normals at 12 bytes each per point. Normals the viewport
    generates count too, as **normals (generated)**;
  - **viewport buffers**: about 5 bytes per point and 9 per triangle of IDs
    and selection data.
- **Primvars**: uploaded only if the viewport draws with them (`points`,
  `normals`, `displayColor`, `displayOpacity`, `st`) or a primvar reader in
  the bound material reads them. Others show their data size and upload
  nothing. Doubles go up as floats. A faceVarying primvar costs one extra byte
  per value.
- **Instances**: a prototype is uploaded once, plus 64 bytes per extra
  instance for its transform.

The model only shapes how a prim's measured memory is split across its
topology and primvars, and how texture and Copernicus totals are split
across files and layers. It was calibrated on simple grids, and on a
production lighting shot it over-predicted geometry by about a third (the
park's big tree: 511 MB modelled, 381 MB measured). The VRAM column doesn't
depend on it for prims.

## Buttons

- **Rescan**: measure again and re-read the viewers' stages and the
  Copernicus nodes. Each viewer measures itself on its next redraw, and the
  window waits up to a few seconds for all of them. A viewer that isn't
  redrawing (a hidden pane) keeps its last figures, and the status line says
  so. The stage scan runs in short slices, so Houdini stays usable while a
  large stage is read. When the stage changes after a scan, the summary says
  so; it doesn't rescan by itself.
- **Free cached**: asks the texture cache, the OpenCL pools and Copernicus to
  release memory they hold but aren't using. Houdini reloads it when it's
  needed again.

Clicking a node row selects the node, and double-clicking frames it in the
network editor. Clicking a prim, primvar or topology row selects the prim in
the viewer. Right-click a row to copy its path, or to show a texture in its
folder.

## What it does not do

- **Run in Houdini 21.** The viewport hook is built for Houdini 22 only. In
  Houdini 21 the window says the hook isn't loaded and measures nothing
  (see [Troubleshooting](troubleshooting.md#the-vram-inspector-says-the-vram-hook-is-not-loaded)).
- **Cook anything.** A Copernicus node that has not cooked is left out (the
  status line counts these). Measuring it would mean cooking it.
- **Itemise OBJ/SOP viewers.** Their geometry is measured, but its rows
  aren't broken down yet.
- **Size implicit prims** (Sphere, Cube, …) in the Upload column. The
  viewport tessellates them itself; they show as *implicit*, with their
  measured VRAM.
- **See Karma XPU's memory by object.** XPU reports its own totals in the
  viewport's render stats.
