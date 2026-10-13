import builtins

from tumblepipe.vram import collect

# Record what the hook measured for the variant set last time (per prim,
# MiB; the request was made by the previous step), then switch variants and
# request a new walk. Run once per bridge call, a few seconds apart.
snap = collect.hook_snapshot()
reading = {}
for vp in (snap.viewports if snap else ()):
    if vp.lops:
        reading.update({path: round(size / 2**20, 3)
                        for path, size in vp.prim_bytes().items()})
cur = getattr(builtins, "_vram_current", None)
if cur:
    builtins._vram_clean[cur] = reading
queue = builtins._vram_queue
if queue:
    nxt = queue.pop(0)
    builtins._vram_set(nxt)
    builtins._vram_current = nxt
    collect.hook_request()
    collect.redraw_viewers()
else:
    builtins._vram_current = None
print(cur, reading, "next:", builtins._vram_current)
