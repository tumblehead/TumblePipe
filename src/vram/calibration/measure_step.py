import builtins
import hou
from tumblepipe.vram import gpumem

# Record what is on screen now under the variant set last time, then switch.
reading = {c.name: round(c.used_bytes / 2**20, 3)
           for c in gpumem.parse_usage(hou.hscript("gpumem -U")[0])
           if c.kind == "geometry"}
cur = getattr(builtins, "_vram_current", None)
if cur:
    builtins._vram_clean[cur] = reading
queue = builtins._vram_queue
if queue:
    nxt = queue.pop(0)
    builtins._vram_set(nxt)
    builtins._vram_current = nxt
    hou.ui.triggerUpdate()
else:
    builtins._vram_current = None
print(cur, reading, "next:", builtins._vram_current)
