"""Check the VRAM Inspector's ``hou`` collectors against a live scene graph.

* COP scan: a cooked Copernicus node's layer is itemised with the right
  size; an uncooked one is skipped — and *stays* uncooked, because an
  inspector that cooks an 8K chain to measure it is worse than none.
* LOP attribution: a prim made inside a ``sopcreate`` is blamed on the
  ``sopcreate`` the artist placed, not the node buried inside it; a prim
  from a plain LOP is blamed on that LOP.
* ``gpumem`` is callable and parses (empty here: hython has no viewport).

Run under hython::

    hython scripts/verify_vram_collect.py

Exit 0 = every check passed.
"""
# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def report(what: str) -> int:
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print(f"All {what} checks passed.")
    return 0


import hou

from tumblepipe.vram import collect, cop_scan
from tumblepipe.vram.usd_scan import ScanOptions, StageScan


def main() -> int:
    net = hou.node("/obj").createNode("copnet", "vram_verify_cops")
    cooked = net.createNode("bubblenoise", "cooked")
    idle = net.createNode("bubblenoise", "idle")
    cooked.cook(force=True)
    check("the idle node starts uncooked", idle.needsToCook())

    result = cop_scan.scan()
    mine = [layer for layer in result.layers if layer.node.startswith(net.path())]
    check("exactly the cooked node is itemised",
          [layer.node for layer in mine] == [cooked.path()], str(mine))
    if mine:
        layer = mine[0]
        w, h = layer.width, layer.height
        check("its size is resolution × channels × storage",
              layer.data_bytes == w * h * layer.channels * 4
              and layer.storage == "Float32", str(layer))
    check("the uncooked node was counted as skipped", result.skipped >= 1)
    check("…and the scan did not cook it", idle.needsToCook())

    stage_net = hou.node("/stage")
    made = stage_net.createNode("sopcreate", "verify_grid")
    # A sopcreate starts with an empty SOP network; give it something to make.
    grid = made.node("sopnet/create").createNode("grid")
    grid.setDisplayFlag(True)
    grid.setRenderFlag(True)
    sphere = stage_net.createNode("sphere", "verify_sphere")
    merge = stage_net.createNode("merge", "verify_merge")
    merge.setInput(0, made)
    merge.setInput(1, sphere)
    stage = merge.stage()
    owner_of = collect.owner_resolver(stage_net)
    scan = StageScan(stage, ScanOptions(), owner_of=owner_of).run()
    owners = {p.path: p.owner for p in scan.prims}
    print("owners:", owners)
    grid_owners = {o for p, o in owners.items() if p.startswith("/verify_grid")}
    check("prims made inside a sopcreate are blamed on the sopcreate",
          grid_owners == {made.path()}, str(grid_owners))
    check("a plain LOP's prim is blamed on that LOP",
          owners.get("/verify_sphere") == sphere.path(), str(owners.get("/verify_sphere")))

    clients = collect.measure()
    check("gpumem is callable and parses", isinstance(clients, list), str(clients))
    return report("VRAM collect")


if __name__ == "__main__":
    raise SystemExit(main())
