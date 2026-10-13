"""How much dedicated VRAM the whole Houdini process holds, per Windows.

Houdini's GPU memory registry only knows the clients that register with
it; drivers, Qt's own
GL/Vulkan surfaces, Karma XPU in another process and third-party plug-ins
don't. Windows' ``GPU Process Memory`` performance counter is what Task
Manager's "Dedicated GPU memory" column shows, and it is the only per-process
figure available on consumer Windows — NVML reports ``N/A`` for per-process
usage under WDDM. The gap between this and the measured clients' sum is
shown as "not reported by Houdini".

Instance names look like ``pid_23460_luid_0x00000000_0x0000D1B3_phys_0``: one
per process per adapter, so a process on two GPUs has two.

:func:`dedicated_bytes` is Windows-only and returns ``None`` elsewhere or on
any failure; :func:`sum_for_pid` is the pure part the property suite tests.
"""

from __future__ import annotations

import re
import sys

_PID_RE = re.compile(r"^pid_(?P<pid>\d+)_")

_COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"
_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2


def instance_pid(name: str) -> int | None:
    """The process id an instance name belongs to, or ``None``."""
    m = _PID_RE.match(name)
    return int(m.group("pid")) if m else None


def sum_for_pid(items: list[tuple[str, int]], pid: int) -> int:
    """Total of every counter instance belonging to ``pid`` (all adapters).

    Negative readings — the counter's way of saying "no data" — count as 0.
    """
    return sum(max(0, value) for name, value in items if instance_pid(name) == pid)


def dedicated_bytes(pid: int) -> int | None:
    """Dedicated VRAM held by process ``pid``, or ``None`` if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        items = _read_counter()
    except Exception:
        return None
    if items is None:
        return None
    return sum_for_pid(items, pid)


def _read_counter() -> list[tuple[str, int]] | None:
    import ctypes
    from ctypes import wintypes

    class _Value(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]

    class _Item(ctypes.Structure):
        _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]

    pdh = ctypes.WinDLL("pdh")
    query = wintypes.HANDLE()
    counter = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, _COUNTER, None, ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
        if (status & 0xFFFFFFFF) != _PDH_MORE_DATA or size.value == 0:
            return [] if status == 0 else None
        buf = (ctypes.c_byte * size.value)()
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf)
        if status != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        return [(items[i].szName or "", int(items[i].FmtValue.largeValue))
                for i in range(count.value)]
    finally:
        pdh.PdhCloseQuery(query)
