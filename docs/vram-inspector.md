# VRAM Inspector

**TumbleTrove ▸ TumblePipe ▸ VRAM Inspector** shows what in your session is
using the graphics card's memory, down to the node, the prim, the primvar and
the texture file.

Houdini measures its GPU memory per *client*, meaning the texture cache,
each viewer pane's geometry, Copernicus's buffers and the OpenCL pool (the
`gpumem -U` command), but never per node. The inspector puts that measurement
next to an itemised reading of the scene, so you can see which node to cut.

## The summary line

```
Houdini process: 1.68 GB dedicated · gpumem: 159 MB across 4 clients · not reported by Houdini: 1.52 GB
```

- **Houdini process**: Windows' figure for the whole Houdini process, the
  same as Task Manager's "Dedicated GPU memory" column. Windows only.
- **gpumem**: what Houdini's own clients add up to, including how much of it
  is cached (held but not in use, so it can be freed).
- **not reported by Houdini**: the difference. This covers window surfaces
  and render targets, the driver, and anything that doesn't register with
  `gpumem` (plug-ins, other renderers). The inspector can't break it down.

## The tree

One top-level row per `gpumem` client, in bold, with its measured size. Open
one to see where that memory comes from:

| Client | Opens into |
|---|---|
| Vulkan Geometry (*desktop.pane.solaris*) | the LOP nodes that made the drawn prims ▸ prims ▸ topology, points, normals, each primvar, the viewport's own buffers |
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

### Three columns, from estimate to fact

- **VRAM**: the row's share of its client's measured total. The bold client
  rows are measured. Below them the measured total is split by **Upload**, so
  it always adds up to what `gpumem` reports. The bar under the figure shows
  the row's share of its parent.
- **Upload**: what the viewport puts on the GPU for the row, worked out
  from the scene with the viewport's own rules (see below).
- **Data**: what the scene itself holds. This is exact.

**Data** and **Upload** can differ a lot. An 8K texture holds 192 MB of
pixels, but the viewport caps it at 2048² and uploads about 21 MB. A primvar
no material reads uploads nothing at all.

### Coverage

A client row's details say how much of the measurement the scan accounts
for:

- **scan explains 100%**: the itemised rows add up to the measurement.
- **scan explains 40%**: the client holds something the scan cannot see, such
  as a viewer showing an OBJ network, or geometry still being uploaded.
- **scene asks for 3.0× this, evicted or reduced**: the materials want more
  textures than the cache holds, so Houdini is evicting textures and swapping
  them back in. Raise the texture cache size, or shrink the textures that
  head the list.

## How the Upload column is worked out

It follows the viewport's own rules, calibrated against `gpumem` in Houdini
22.0's Solaris viewport:

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

These rules were measured on one machine and one Houdini build. That's why
the VRAM column distributes the *measured* total rather than trusting the
model, and why the coverage figure tells you when the two disagree.

### Known gaps

The rules above were calibrated on simple grids. On a production lighting
shot the scan explained only about 40% of the measured viewer geometry. Two
kinds of geometry cost much more than the model says:

- **meshes with faceVarying normals**, likely because the viewport splits
  their points per face-vertex;
- **curves** (BasisCurves such as grooms), likely because the viewport builds
  ribbons from them.

Such prims get **too small a share** of the VRAM column. When a geometry
client's coverage is low, check the prims with the most face-vertices or
curve points first, whatever their share says.

## Buttons

- **Rescan**: read the viewers' stages and the Copernicus nodes again. The
  scan runs in short slices, so Houdini stays usable while a large stage is
  read. When the stage changes after a scan, the summary says so; it doesn't
  rescan by itself.
- **Free cached**: asks every client to release memory it holds but isn't
  using (`gpumem -f`). Houdini reloads it when it's needed again.

The measured numbers are read when the window opens and on **Rescan**, never
in the background: Houdini's own `gpumem` command can crash Houdini if it
runs while the viewport is updating.

Clicking a node row selects the node, and double-clicking frames it in the
network editor. Clicking a prim, primvar or topology row selects the prim in
the viewer. Right-click a row to copy its path, or to show a texture in its
folder.

## What it does not do

- **Cook anything.** A Copernicus node that has not cooked is left out (the
  status line counts these). Measuring it would mean cooking it.
- **Itemise OBJ/SOP viewers.** Their client is measured, but its rows aren't
  broken down yet.
- **Size implicit prims** (Sphere, Cube, …). The viewport tessellates them
  itself; they show as *implicit*.
- **See Karma XPU's memory by object.** XPU reports its own totals in the
  viewport's render stats.
