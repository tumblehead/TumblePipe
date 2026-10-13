# VRAM Inspector calibration

How the constants in `python/tumblepipe/vram/sizes.py` were measured, and the
scripts to measure them again (for example for faceVarying normals and
BasisCurves, which the model under-counts — see *Known gaps* in
`docs/vram-inspector.md`). Dev-only: `src/` doesn't ship.

Original measurements: Houdini 22.0.368, RTX 3070, Windows 11, 2026-10.

## Scripts

Run them inside a dev Houdini session (TumbleTrove Desktop's bridge
`sessions_exec_python`), not hython — they need a viewport.

| Script | What it does |
|---|---|
| `vram_scene.py` | Builds the test scene in `/stage` (below). Regenerates `$TEMP/vram_inspector_test/albedo_4k.exr` with OIIO + numpy. |
| `measure_step.py` | One step of the geometry protocol: records the current `gpumem` geometry reading under the variant set last step, then switches to the next queued variant. Needs `builtins._vram_queue` (variant names), `builtins._vram_set(name)` (sets that variant's display flag) and `builtins._vram_clean = {}` set up first. |
| `tex_step.py` | One step of the texture protocol: generates the six test images, swaps `/stage/vramtest_mtl/albedo`'s file, runs `glcache -c`. |
| `reload_vram.py` | Hot-reloads `tumblepipe.vram` from this checkout into a running session. Dev sessions run a *staged* copy and the bridge blocks `importlib`, so it execs each module's source into `sys.modules[name].__dict__`. |
| `strings_grep.py` | Printable-string grep over DLLs/PDBs (`python strings_grep.py <regex> <files…>`); how the debug-validation findings in `../vkprobe/README.md` were made. |

## Protocol

`gpumem` readings lag a viewport redraw. A reading taken in the same bridge
call that changed the scene, or right after it, is the **previous** state's.

1. Set one variant's display flag (`measure_step.py` does this).
2. **Return** from the bridge call, so Houdini redraws.
3. Wait at least 5 s outside Houdini.
4. Read the `gpumem -U` geometry client for the Solaris pane (the next
   `measure_step.py` call does this, then sets the next variant).

Never read `gpumem` on a timer while toggling things: Houdini's `gpumem`
segfaults if it runs while the viewport tears down a geometry render. That
is the crash TumbleTrove 0.46.0's "Live totals" caused, and why
`tests/test_vram_no_polling.py` exists.

## Test scene

`sopcreate` `vramtest_grid` with a 1000×1000 grid (rows/cols 1000, size 10)
inside `sopnet/create`, `uvproject` → faceVarying `st`; `materiallibrary`
with `mtlxstandard_surface` + `mtlximage` (signature color3) reading a
generated EXR; `assignmaterial` `/vramtest_grid/**`; a sphere; a merge
(display). COP: an `/obj` copnet with a `fractalnoise` (1024² mono float32 =
4.0 MiB; `gpumem` "COP OpenCL Buffer Cache" read 4.125 MiB).

## Geometry results (MiB)

| Variant | Points | Triangles | Face-vertices | Measured |
|---|---|---|---|---|
| grid | 1,000,000 | 1,996,002 | 3,992,004 | 99.057 |
| triangulated (`divide`) | 1,000,000 | 1,996,002 | 5,988,006 | 114.285 |
| unique points (`facet` unique=1) | 3,992,004 | 1,996,002 | 3,992,004 | 182.752 |
| 500×500 grid | | | | 24.739 (model 24.73) |

- `+` faceVarying `st` (texCoord2f): +34.26 on both topologies (9 B per
  face-vertex).
- Authored point normals cost the same as generated ones.
- Solve: **8.0 B per face-vertex** (wireframe), **21.34 B per triangle**,
  **29.33 B per point** (P 12 + N 12 + 5.33).
- The `add` SOP with remove=1 did **not** delete prims, so the points-only
  variant was invalid; use a different SOP for a points-only test.

## Texture results (MiB)

Run `glcache -c` between reads, or old textures linger (the first 85 MiB
reading was two cached copies).

| Image | Measured |
|---|---|
| 4K half RGB EXR | 42.712 |
| 4K u8 RGB PNG | 21.379 |
| 4K f32 RGBA EXR | 85.378 |
| 2K half RGB EXR | 42.712 |
| 4K u8 mono PNG | 5.381 |
| 8K u8 RGB PNG | 21.379 |

So: `min(res, 2048²) × channels (3 → 4, 1 stays 1) × file depth × 4/3`;
HDR16 does not cap float32.

Viewport settings at the time (`SceneViewer.curViewport().settings()`):
`textureMaxRes2D` (2048, 2048), `textureResLimit2D` True, `textureScale2D`
1.0, `textureBitDepthLimit2D` HDR16, `textureMipmapping` True,
`textureCacheSize` 512 MB.

## Measuring a production gap

Hide one prim at a time and take the delta, in the session layer only:

```python
with hou.node("/stage").viewportOverrides(lop) as ov:
    ov.setVisible(hou.LopSelectionRule("/SET/Park/geo/big_tree"), False)
# undo: ov.setVisible(..., True) or ov.clear()
```

Wait before reading, as above. Open candidates from the HideAndReek lighting
check (`P:/HideAndReek/groups/shots/Lighting/light/Lighting_light_v0126.hip`
— load read-only with `hdefereval.executeDeferred(lambda:
hou.hipFile.load(path, suppress_save_prompt=True, ignore_load_warnings=True))`,
~11 s, first draw ~60 s; never save it):

- `/SET/Park/geo/big_tree` — Mesh, 2,848,318 pts, 2,853,392 faces,
  11,402,706 face-vertices, faceVarying normals and `st`, vertex
  `displayColor`; the model says 511 MB.
- `/SET/Studio/groom/carpet_fuzz/curve_0` — BasisCurves, 2,497,945 pts,
  499,589 curves; the model says 66.7 MB.
