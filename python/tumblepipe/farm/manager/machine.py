"""This machine's load while the local farm renders: CPU, memory, GPU, and the task's own processes.

What the Farm Manager shows beside a rendering local task, so nobody has to
guess whether a render is using the GPU. Qt-free; :meth:`Sampler.sample`
starts ``nvidia-smi`` and walks a process tree, a few hundred milliseconds,
so the window calls it on a worker thread (*Qt and threads* in
docs/development.md).

Each source degrades on its own: without ``psutil`` (Houdini's Python has
it) CPU and memory come from the OS directly on Windows and Linux, and the
task's processes are not measured; without ``nvidia-smi`` (no NVIDIA GPU,
macOS) there is no GPU line. A source that fails once is not asked again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

GPU_QUERY = [
    '--query-gpu=name,utilization.gpu,memory.used,memory.total',
    '--format=csv,noheader,nounits',
]


@dataclass(frozen=True)
class GpuStat:
    name: str
    utilization: int        # percent
    memory_used: int        # MiB
    memory_total: int       # MiB


@dataclass(frozen=True)
class MachineStats:
    cpu: float | None = None            # percent of the whole machine
    ram_used: int | None = None         # bytes
    ram_total: int | None = None        # bytes
    gpus: tuple[GpuStat, ...] = ()
    task_cpu: float | None = None       # the task's processes, percent of the machine
    task_ram: int | None = None         # the task's processes, bytes resident
    task_processes: int = 0
    task_names: tuple[str, ...] = field(default=(), compare=False)


def parse_nvidia_smi(text: str) -> list[GpuStat]:
    """``nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits``.

    One GPU a line; the last three fields are numbers, the name is the rest
    (a name may hold a comma). A line with ``[N/A]`` or anything else
    unreadable is skipped.
    """
    result = []
    for line in (text or '').splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 4:
            continue
        try:
            utilization, used, total = (int(float(p)) for p in parts[-3:])
        except ValueError:
            continue
        name = ', '.join(parts[:-3]).strip()
        if utilization < 0 or used < 0 or total <= 0:
            continue
        result.append(GpuStat(name, min(100, utilization), used, total))
    return result


def bytes_text(value: int | None) -> str:
    """``21.3 GB``, ``512 MB``: decimal-free below a gigabyte."""
    if value is None or value < 0:
        return ''
    gb = value / 1024 ** 3
    if gb >= 1:
        return f'{gb:.1f} GB'
    return f'{value / 1024 ** 2:.0f} MB'


def summary_text(stats: MachineStats | None) -> str:
    """One line for the footer: ``CPU 34% · RAM 21.3/63.9 GB · GPU 87% · VRAM 6.1/8.0 GB``."""
    if stats is None:
        return ''
    parts = []
    if stats.cpu is not None:
        parts.append(f'CPU {stats.cpu:.0f}%')
    if stats.ram_used is not None and stats.ram_total:
        parts.append(f'RAM {stats.ram_used / 1024 ** 3:.1f}/{stats.ram_total / 1024 ** 3:.1f} GB')
    for gpu in stats.gpus:
        parts.append(f'GPU {gpu.utilization}%')
        parts.append(f'VRAM {gpu.memory_used / 1024:.1f}/{gpu.memory_total / 1024:.1f} GB')
    return ' · '.join(parts)


def stat_rows(stats: MachineStats | None) -> list[tuple[str, str, str]]:
    """``(key, value, tip)`` rows for the details pane's *This machine* section."""
    if stats is None:
        return []
    rows = []
    if stats.task_processes:
        task = []
        if stats.task_cpu is not None:
            task.append(f'CPU {stats.task_cpu:.0f}%')
        if stats.task_ram is not None:
            task.append(f'RAM {bytes_text(stats.task_ram)}')
        task.append(f"{stats.task_processes} process{'' if stats.task_processes == 1 else 'es'}")
        rows.append((
            'This task', ' · '.join(task),
            'The task\'s own processes (hpm, hython, husk...), CPU as a share of the '
            'whole machine' + (f":\n{', '.join(stats.task_names)}" if stats.task_names else ''),
        ))
    if stats.cpu is not None:
        rows.append(('CPU', f'{stats.cpu:.0f}%', 'Every process on this machine'))
    if stats.ram_used is not None and stats.ram_total:
        rows.append(('RAM', f'{bytes_text(stats.ram_used)} of {bytes_text(stats.ram_total)}', ''))
    many = len(stats.gpus) > 1
    for index, gpu in enumerate(stats.gpus):
        label = f'GPU {index}' if many else 'GPU'
        rows.append((label, f'{gpu.utilization}%', gpu.name))
        rows.append((
            f'VRAM {index}' if many else 'VRAM',
            f'{gpu.memory_used / 1024:.1f} of {gpu.memory_total / 1024:.1f} GB', gpu.name,
        ))
    return rows


class Sampler:
    """Samples this machine, one call at a time, from one worker thread.

    Keeps what a percentage needs between calls (the last CPU times, the
    task's ``psutil.Process`` objects), so the first sample of each has no
    CPU figure.
    """

    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            psutil = None
        self._psutil = psutil
        self._processes: dict[int, object] = {}
        self._cpu_times = None
        self._nvidia_smi = shutil.which('nvidia-smi')
        self._primed = False

    def sample(self, pid: int = 0) -> MachineStats:
        cpu, ram_used, ram_total = self._machine()
        task = self._task(pid) if pid else (None, None, 0, ())
        return MachineStats(
            cpu=cpu, ram_used=ram_used, ram_total=ram_total, gpus=tuple(self._gpus()),
            task_cpu=task[0], task_ram=task[1], task_processes=task[2], task_names=task[3],
        )

    # ── the machine ──

    def _machine(self) -> tuple[float | None, int | None, int | None]:
        try:
            if self._psutil is not None:
                cpu = self._psutil.cpu_percent(interval=None)
                memory = self._psutil.virtual_memory()
                primed, self._primed = self._primed, True
                return (cpu if primed else None), memory.total - memory.available, memory.total
            return self._cpu_fallback(), *self._ram_fallback()
        except Exception:
            return None, None, None

    def _cpu_fallback(self) -> float | None:
        times = _cpu_times()
        if times is None:
            return None
        last, self._cpu_times = self._cpu_times, times
        if last is None:
            return None
        idle, total = times[0] - last[0], times[1] - last[1]
        return max(0.0, min(100.0, 100.0 * (1 - idle / total))) if total > 0 else None

    @staticmethod
    def _ram_fallback() -> tuple[int | None, int | None]:
        if sys.platform == 'win32':
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None, None
            return status.ullTotalPhys - status.ullAvailPhys, status.ullTotalPhys
        if os.path.exists('/proc/meminfo'):
            values = {}
            with open('/proc/meminfo') as handle:
                for line in handle:
                    key, _, rest = line.partition(':')
                    values[key] = int(rest.split()[0]) * 1024
            total = values.get('MemTotal')
            available = values.get('MemAvailable')
            if total and available is not None:
                return total - available, total
        return None, None

    # ── the task's processes ──

    def _task(self, pid: int) -> tuple[float | None, int | None, int, tuple[str, ...]]:
        psutil = self._psutil
        if psutil is None:
            return None, None, 0, ()
        try:
            root = self._processes.get(pid) or psutil.Process(pid)
            family = [root, *root.children(recursive=True)]
        except Exception:
            self._processes.clear()
            return None, None, 0, ()
        kept: dict[int, object] = {}
        cpu = 0.0
        ram = 0
        measured = False
        names = []
        for process in family:
            # The same object as last time, or its percentage starts over.
            process = self._processes.get(process.pid, process)
            try:
                if process.pid not in self._processes:
                    process.cpu_percent(interval=None)
                else:
                    cpu += process.cpu_percent(interval=None)
                    measured = True
                ram += process.memory_info().rss
                names.append(process.name())
            except Exception:
                continue
            kept[process.pid] = process
        self._processes = kept
        cores = psutil.cpu_count() or 1
        share = min(100.0, cpu / cores) if measured else None
        return share, ram, len(kept), tuple(dict.fromkeys(names))

    # ── the GPU ──

    def _gpus(self) -> list[GpuStat]:
        if not self._nvidia_smi:
            return []
        try:
            result = subprocess.run(
                [self._nvidia_smi, *GPU_QUERY],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
        except (OSError, subprocess.SubprocessError):
            self._nvidia_smi = None
            return []
        if result.returncode != 0:
            self._nvidia_smi = None
            return []
        return parse_nvidia_smi(result.stdout.decode('utf-8', errors='replace'))


def _cpu_times() -> tuple[float, float] | None:
    """``(idle, total)`` CPU time so far, on Windows and Linux; None elsewhere."""
    try:
        if sys.platform == 'win32':
            import ctypes

            class FileTime(ctypes.Structure):
                _fields_ = [('low', ctypes.c_ulong), ('high', ctypes.c_ulong)]

            idle, kernel, user = FileTime(), FileTime(), FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None

            def value(t: FileTime) -> int:
                return (t.high << 32) | t.low

            # Kernel time includes idle time.
            return float(value(idle)), float(value(kernel) + value(user))
        if os.path.exists('/proc/stat'):
            with open('/proc/stat') as handle:
                fields = [float(v) for v in handle.readline().split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)
            return idle, sum(fields)
    except Exception:
        return None
    return None
