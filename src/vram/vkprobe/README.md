# vkprobe — Vulkan layer spike for the VRAM Inspector

A pass-through Vulkan layer (`VK_LAYER_TT_vramprobe`) that logs every buffer,
image and memory allocation a process makes, and every debug name it assigns.
Built to answer one question for the VRAM Inspector's second phase:

> Does Houdini's Vulkan viewport name its GPU objects (`VK_EXT_debug_utils`)?
> If it did, a layer could attribute exact bytes to the prims and textures
> they belong to, which no HDK API exposes for the Solaris viewport.

This is a research tool. It isn't part of the shipped package: the release
staging allowlist (`hpm.toml` `[stage].include`) doesn't include `src/`. It
moved here from TumbleTrove's `native/vkprobe/` with the VRAM Inspector
(`python/tumblepipe/vram/`).

## Answer (2026-10-13, Houdini 22.0.368, RTX 3070, Windows 11)

**No.** In both a normal launch and one with `HOUDINI_OGL_DEBUG=1`:

- the instance enables only `VK_KHR_external_*_capabilities`, `VK_KHR_surface`
  and `VK_KHR_win32_surface`, and **not** `VK_EXT_debug_utils`;
- the device never gets a `vkSetDebugUtilsObjectNameEXT` (`set_name=0` in the
  log), and **no object is ever named**.

`libRV.dll` exports `RV_Instance::usingDebugValidation()`, and `libVE.dll` knows
`VK_LAYER_KHRONOS_validation`, `VK_LAYER_RENDERDOC_Capture` and
`vkSetDebugUtilsObjectNameEXT`. No environment variable in Houdini's table
switches this on, though, and the only gate visible in `libRV` is
`IsDebuggerPresent`. Naming looks reserved for a debugger-attached or
internal session. Forcing the extension on from the layer wouldn't help
either: Houdini only names objects when it has decided to validate.

**So exact per-prim VRAM isn't reachable from a layer in shipping Houdini 22.**
The VRAM Inspector's itemised rows stay what they are: the measured `gpumem`
total, split by an upload model calibrated against `gpumem`.

## What a layer *does* see that `gpumem` does not

Exact live buffers and images by kind. On a bare session at a 1652×1042
viewport, the viewer's render targets alone came to **≈105 MiB**: 12 colour
images (RGBA16F, 13.1 MiB each) and 7 depth images (D32, 6.6 MiB each).
`gpumem` lists none of this. It's part of what the inspector reports as "not
reported by Houdini". A layer that exported a summary function (Python can
`ctypes`-load the same DLL in-process) could break that gap down into render
targets vs other. It's worth doing if the gap turns out to matter. The cost
is that Houdini has to be launched with the layer enabled (env vars below),
which TumbleTrove Desktop could set per project.

## Build

From a shell with MSVC (`vcvars64.bat`), using the Vulkan headers the HDK
ships:

```bat
cl /nologo /LD /O2 /EHsc /I"%HFS%\toolkit\include" vkprobe.cpp /Fe:tt_vramprobe.dll
```

Put `tt_vramprobe.dll` next to `VK_LAYER_TT_vramprobe.json`.

## Run

```powershell
$env:VK_ADD_LAYER_PATH       = "<folder with the .json and .dll>"
$env:VK_INSTANCE_LAYERS      = "VK_LAYER_TT_vramprobe"   # older loaders
$env:VK_LOADER_LAYERS_ENABLE = "VK_LAYER_TT_vramprobe"   # newer loaders
$env:TT_VKPROBE_LOG          = "C:\path\to\run.log"
& "$env:HFS\bin\houdini.exe" scene.hip
```

Then summarise the log:

```
python analyze.py run.log
```

Gotchas from the spike:

- **A fresh, empty `HOUDINI_USER_PREF_DIR` makes Houdini 22 throw on startup**
  (exit `0xE06D7363`, an unhandled C++ exception, about 13 s in), with or
  without the layer. Seed a sandbox with copies of the real `*.pref`,
  `houdini.env` and `config/`, and point at it as `…\houdini__HVER__`.
- **Houdini expands `~` in `HOUDINI_PATH`**, so an 8.3 short path like
  `C:\Users\TUMBLE~1\…` silently stops the path from loading. Use the long
  form.
