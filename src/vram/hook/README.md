# tt_vramhook — the VRAM Inspector's measurements

A DSO that registers a `DM_SceneHook` and, during each viewport's own redraw,
walks the viewport's looks → `GUI_GeoRender`s → `GR_Primitive`s →
`GR_GeoRender`s and records each one's GPU memory; on the same walk it reads
Houdini's GPU memory registry (`UT_MemoryResource` / `UT_MemoryClient`) for
every non-geometry client. Python asks for a walk and reads the last
snapshot (JSON); it never walks live renderers itself. It is the inspector's
only source: `tumblepipe.vram` never runs Houdini's `gpumem`, and has no
fallback when the hook isn't loaded.

Shipped for Houdini 22 in `dso/<major>.<minor>/`, built by
`.ci/build_vramhook.py` (the `build-vramhook` prepack script). Spike run
2026-10-13 on Houdini 22.0.368, RTX 3070,
`P:/HideAndReek/groups/shots/Lighting/light/Lighting_light_v0126.hip`
(read-only).

## What the spike found

**(a) The hook sees the Solaris viewer.** One `USDStage` look (class
`GUI_DetailLook`) holds the stage's geometry. Nearly all of it is one
`GUI_GeoRender`, `USDStage[0]:-1`, with one `GR_PrimPacked` per drawn USD
prim (42 on the lighting shot). A walk takes ~0.2 ms.

**(b) Every primitive is named by its USD prim path.** `GR_Primitive::
getCacheName()` is the prim path (`/SET/Park/geo/big_tree`,
`/CHAR/Ryan/geo/Hair`, …; consolidated small meshes appear as
`/__consolidated10__`). A primitive's memory is the sum of its
`getGeoRenders()` parts. That is exact per-prim VRAM, no model:
hiding `big_tree` dropped the total by exactly its row (380.69 MiB) plus its
child `tree_rock` (0.80 MiB).

**(c) The sum matches `gpumem` only by reproducing a Houdini bug.**
`gpumem -U` reported `Vulkan Geometry … 1576.4MB`; the true per-part sum is
**430.45 MiB**. Disassembling `GUI_GeoRender::gpuMemoryUsage` (libGUI.dll,
RVA 0x432B0) shows it zeroes its per-render outputs once per *primitive*,
while each `GR_GeoRender::gpuMemoryUsage` *adds* into them — so a primitive
with parts a, b, c is counted a + (a+b) + (a+b+c). Replaying that arithmetic
in the spike build of the hook gave 1576.37 MiB, gpumem's number to the
hundredth. The whole
Vulkan device allocation at the time was ~760 MiB, so 1576 MiB of geometry
was never possible. **gpumem's geometry figure overstates multi-part prims;
the inspector's calibration and "explained %" were measured against it.**

**The crash is a null entry, not a race.** The second spike session crashed
inside Houdini's own `GUI_GeoRender::gpuMemoryUsage`, called from the hook
mid-redraw, right after hiding a prim. At +0x53 it loads an entry of its
`myPrimitives` array and dereferences it without a null check; hiding a prim
leaves null entries (2 after hiding `big_tree`) until the next update compacts
the array. That is the fault behind gpumem's crash, too: reading "at a safe
time" does not avoid it. `getPrimitive(i)` *does* null-check the entry, so
the hook sums per primitive through it and never calls the render-level
function. Hide → walk → unhide → walk ran clean (48.97 MiB with 2 null
entries skipped; back to 430.45 MiB with 0).

**Settled after the spike:**

- Every other client comes from the registry the hook walks — it is what
  `gpumem` prints, and it agrees with it (texture cache 42.71 MiB, OpenCL pool
  0.34 MiB, COP OpenCL Buffer Cache 4.125 MiB). The registry's geometry
  clients are listed by name only (their `memoryUse` is the crashing
  function); their pane names let Python match viewers.
- Textures: `RV_TextureCache::currentSizeBytes()` and the registry's texture
  client agree at the same moment. The one mismatch (42.7 vs 407 MB) was a
  stale snapshot read against a later gpumem, while Houdini loaded textures
  in the background.
- One request is answered by **every** viewport, each on its own next
  redraw: requests are generation numbers, and each viewport's snapshot
  echoes the one it answered. A 22.0.368 build loads in 22.0.429 and the
  reverse, so one build per major.minor.
- `hou.ui.triggerUpdate()` does not redraw an idle viewport;
  `SceneViewer.curViewport().draw()` does (on the next event-loop pass, so
  read the snapshot in a later call).
- First spike session crashed in the hook's own code:
  `DM_VPortAgent::getDetailLookByIndex` has no count and does not return null
  past the end. Only the counted `getNum*Objects()` / `get*Object(i)` lists are
  walked.

**Ruled out — a Vulkan layer.** A pass-through Vulkan layer that logged
every buffer, image and allocation (a 2026-10 spike, since deleted) showed
Houdini 22 never names its Vulkan objects (`vkSetDebugUtilsObjectNameEXT` is
never called, with or without `HOUDINI_OGL_DEBUG`), so a layer cannot
attribute memory to prims. What it does see that the memory registry does
not: the viewer's render targets — about 105 MiB of RGBA16F colour and D32
depth images for one 1652×1042 viewport — part of "not reported by Houdini".

**Open:** `RV_VKMemAllocator` totals (`vulkan.device_vk`) are recorded but not
yet broken down (render targets, the "not reported by Houdini" gap). Houdini
21 is not built.

## Interface

hscript (works from the TumbleTrove session bridge, which blocks `ctypes`):

- `ttvramhook_request` — every viewport walks on its next redraw; prints the
  request number.
- `ttvramhook_snapshot` — prints every viewport's last walk and the registry
  as JSON.
- `ttvramhook_free` — asks every non-geometry client to release the memory
  it reports as cached (what `gpumem -f` did); prints the bytes freed.

C (for in-process `ctypes`): `long long tt_vramhook_request()`,
`const char *tt_vramhook_snapshot()` (valid until the next call).

Snapshot: `request`, `walks`, `clients[]` (registry: `device`, `name`,
`geometry`, `used`, `cached` — geometry clients carry no figure), and
`viewports[]`, each with `request` (the one it answered), `viewport_id`,
`display_lop`, `walk_ms`, `textures` (cache bytes, count, max), `vulkan`
(device name, allocator totals), and `viewport.looks[].renders[]`, each with
`used`/`cached`/`pinned` (the null-safe sum), `null_prims`, and `prims[]` — `cache` (USD
prim path), `class`, `parts[]` (bytes per `GR_GeoRender`), `used`, `cached`,
`pinned`. `tumblepipe.vram.hook` parses it.

## Build and run

`hpm build` (and every dev launch, which runs the prepack scripts) builds it
with CMake and the HDK's `toolkit/cmake` into `dso/<major>.<minor>/`; by hand:

```sh
python .ci/build_vramhook.py --platform windows-x86_64   # or linux-x86_64 / macos-aarch64
```

`hpm.toml` `[runtime]` puts `dso/$HOUDINI_MAJOR_RELEASE.$HOUDINI_MINOR_RELEASE`
on `HOUDINI_DSO_PATH`; `.ci/build_platform.py` fails a Houdini 22 archive
that lacks it. Each dev launch stages a copy of the package, so a rebuilt DSO
needs a relaunch.

For a quick debug build, `hcustom -g -i <dir> vramhook.cpp` works too — always
pass `-i`: without it hcustom installs into the user's own `houdini22.0/dso`,
which every session loads.

A DSO bug takes Houdini down like gpumem did. Read only inside the hook,
null-check every pointer, keep no HDK pointer past the callback, and skip
retired looks.
