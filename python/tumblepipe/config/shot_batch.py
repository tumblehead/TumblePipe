"""Planning and creating many shots at once.

The CSV importer (``tools/csv_shot_import``) turns a list of rows
(sequence, shot, frame range) into shot entities with :func:`plan` and
:func:`create`; the Entity Editor (``config/entity_sheet``) shares the rest, so
the two cannot disagree about a name or a pasted line:

- :func:`expand_names` and :func:`guess_pattern` build shot names from a
  pattern such as ``SH###`` (``SH010``, ``SH020``, …), continuing a
  sequence's own numbering (:func:`next_number`).
- :func:`parse_table` reads rows pasted from a spreadsheet.
- :func:`name_problem` and :func:`same_but_case` are the name rules.
- :func:`plan` says, for every row, whether it creates a shot, is skipped
  (the shot already exists, or the list names it twice) or cannot be
  created, and why. It is a pure function of the rows and a
  :class:`ProjectShots` snapshot, so a dry run is exactly what
  :func:`create` then does.
- :func:`create` writes the new rows: missing sequences first, then each
  shot with only its frame range, and its Root when one is given. Every
  other property is left to inherit, the same as **New Shot...**, so a
  later change to the sequence or project still reaches these shots.

Names follow :func:`tumblepipe.naming.is_valid_entity_name` (ASCII letters
and digits), and two names that differ only by case are refused: the
config store rejects them, since case variants split exports and sidecars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from tumblepipe.naming import is_valid_entity_name
from tumblepipe.util.uri import Uri

SHOTS_URI = Uri.parse('entity:/shots')
ASSETS_URI = Uri.parse('entity:/assets')

_PATTERN_RUN = re.compile(r'#+')


@dataclass(frozen=True)
class ShotRow:
    """One shot to create."""
    sequence: str
    shot: str
    frame_start: int | None  # None: a pasted bare name, left to the sequence
    frame_end: int | None


class Status(Enum):
    NEW = 'new'              # will be created
    EXISTS = 'exists'        # already in the project: skipped
    DUPLICATE = 'duplicate'  # an earlier row names it: skipped
    INVALID = 'invalid'      # cannot be created as written


@dataclass(frozen=True)
class PlannedShot:
    row: ShotRow
    status: Status
    reason: str = ''          # why it is skipped or invalid
    new_sequence: bool = False


@dataclass(frozen=True)
class Plan:
    shots: tuple[PlannedShot, ...]

    def _with(self, status: Status) -> list[PlannedShot]:
        return [p for p in self.shots if p.status is status]

    @property
    def new(self) -> list[ShotRow]:
        return [p.row for p in self._with(Status.NEW)]

    @property
    def skipped(self) -> list[PlannedShot]:
        return self._with(Status.EXISTS) + self._with(Status.DUPLICATE)

    @property
    def invalid(self) -> list[PlannedShot]:
        return self._with(Status.INVALID)

    @property
    def sequences_to_create(self) -> list[str]:
        """New sequences, in first-use order."""
        seen: list[str] = []
        for p in self._with(Status.NEW):
            if p.new_sequence and p.row.sequence not in seen:
                seen.append(p.row.sequence)
        return seen


@dataclass(frozen=True)
class ProjectShots:
    """What :func:`plan` checks rows against."""
    shots: dict[str, frozenset[str]] = field(default_factory=dict)
    categories: frozenset[str] = frozenset()

    @property
    def sequences(self) -> frozenset[str]:
        return frozenset(self.shots)


def snapshot(config) -> ProjectShots:
    """The project's sequences, their shots, and its asset categories."""
    shots: dict[str, set[str]] = {}
    categories: set[str] = set()
    with config.coherent():
        for uri in config.list_entity_uris(SHOTS_URI, closure=True):
            segments = uri.segments[1:]
            if len(segments) >= 1:
                shots.setdefault(segments[0], set())
            if len(segments) == 2:
                shots[segments[0]].add(segments[1])
        for uri in config.list_entity_uris(ASSETS_URI, closure=True):
            if len(uri.segments) >= 2:
                categories.add(uri.segments[1])
    return ProjectShots(
        shots={seq: frozenset(names) for seq, names in shots.items()},
        categories=frozenset(categories),
    )


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _split_pattern(pattern: str) -> tuple[str, int, str]:
    runs = list(_PATTERN_RUN.finditer(pattern))
    if len(runs) != 1:
        raise ValueError(
            f"'{pattern}' needs exactly one run of # for the shot number, "
            "e.g. SH###",
        )
    run = runs[0]
    return pattern[:run.start()], len(run.group()), pattern[run.end():]


def expand_names(pattern: str, start: int, step: int, count: int) -> list[str]:
    """``count`` names from ``pattern``, numbering from ``start`` by ``step``.

    The ``#`` run is the number, zero-padded to the run's width; a number
    wider than the run is written in full (``SH##`` at 100 is ``SH100``).
    """
    head, width, tail = _split_pattern(pattern)
    if count < 0:
        raise ValueError('count cannot be negative')
    if step < 1:
        raise ValueError('step must be at least 1')
    if start < 0:
        raise ValueError('the first number cannot be negative')
    return [
        f'{head}{str(start + i * step).zfill(width)}{tail}'
        for i in range(count)
    ]


def _pattern_regex(pattern: str) -> re.Pattern:
    head, _width, tail = _split_pattern(pattern)
    return re.compile(
        f'{re.escape(head)}([0-9]+){re.escape(tail)}', re.IGNORECASE,
    )


def next_number(pattern: str, existing: Iterable[str], step: int) -> int | None:
    """The number after the highest existing name ``pattern`` matches,
    rounded up to the next multiple of ``step``; None when none match."""
    regex = _pattern_regex(pattern)
    numbers = [
        int(m.group(1)) for name in existing
        if (m := regex.fullmatch(name)) is not None
    ]
    if not numbers:
        return None
    step = max(step, 1)
    return (max(numbers) // step + 1) * step


_GUESS = re.compile(r'([A-Za-z]*)([0-9]+)([A-Za-z]*)')


def guess_pattern(existing: Iterable[str]) -> str | None:
    """The pattern most of ``existing`` follow (``SH010`` → ``SH###``),
    or None when no name is letters, digits, letters."""
    counts: dict[str, int] = {}
    for name in existing:
        m = _GUESS.fullmatch(name)
        if m is None:
            continue
        pattern = f'{m.group(1)}{"#" * len(m.group(2))}{m.group(3)}'
        counts[pattern] = counts.get(pattern, 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=counts.__getitem__)


# ---------------------------------------------------------------------------
# Pasted rows
# ---------------------------------------------------------------------------


def _int(text: str) -> int | None:
    text = text.strip()
    return int(text) if re.fullmatch(r'-?[0-9]+', text) else None


#: Column titles a pasted two-cell header row is made of.
_TITLES = frozenset({'sequence', 'shot', 'category', 'asset', 'name'})


def parse_table(
    text: str, default_sequence: str,
    frame_start: int | None, frame_end: int | None,
) -> tuple[list[ShotRow], list[str]]:
    """Rows pasted from a spreadsheet, and the lines that were not rows.

    Cells are split on tabs (a spreadsheet copy), else on commas. A line
    holds one of:

    - ``shot``: in ``default_sequence``, with the given frame range (None
      to leave it to the sequence)
    - ``sequence, shot``: likewise, in ``sequence``
    - ``shot, start, end``
    - ``sequence, shot, start, end``

    (The Entity Editor reads assets the same way: category for sequence,
    asset for shot.) A line whose frames are not whole numbers, or a
    two-cell line of column titles (``Sequence, Shot``), is returned as
    rejected rather than guessed at; blank lines are dropped.
    """
    rows: list[ShotRow] = []
    rejected: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split('\t' if '\t' in line else ',')]
        while cells and not cells[-1]:
            cells.pop()
        if len(cells) == 1:
            rows.append(ShotRow(
                default_sequence, cells[0], frame_start, frame_end,
            ))
            continue
        if len(cells) == 2:
            if {c.casefold() for c in cells} <= _TITLES:
                rejected.append(line)
            else:
                rows.append(ShotRow(cells[0], cells[1], frame_start, frame_end))
            continue
        if len(cells) == 3:
            sequence, (shot, start, end) = default_sequence, cells
        elif len(cells) == 4:
            sequence, shot, start, end = cells
        else:
            rejected.append(line)
            continue
        start_n, end_n = _int(start), _int(end)
        if start_n is None or end_n is None:
            rejected.append(line)
            continue
        rows.append(ShotRow(sequence, shot, start_n, end_n))
    return rows, rejected


# ---------------------------------------------------------------------------
# Planning and creating
# ---------------------------------------------------------------------------


def same_but_case(name: str, names: Iterable[str]) -> str | None:
    """The first of ``names`` that differs from ``name`` only by case."""
    folded = name.casefold()
    for other in names:
        if other != name and other.casefold() == folded:
            return other
    return None


def name_problem(kind: str, name: str) -> str:
    """Why ``name`` cannot name a ``kind`` (sequence, shot), or ''."""
    if not name:
        return f'No {kind} name'
    if not is_valid_entity_name(name):
        return f"{kind.capitalize()} name '{name}' may only use letters and digits"
    return ''


def plan(rows: Iterable[ShotRow], project: ProjectShots) -> Plan:
    """What :func:`create` does with each of ``rows``, in order."""
    planned: list[PlannedShot] = []
    # Spellings this list introduces: a new sequence's first spelling, and
    # each sequence's shots (existing and earlier rows), by casefold.
    new_sequences: dict[str, str] = {}
    listed: dict[str, dict[str, int]] = {}
    for index, row in enumerate(rows):
        def invalid(reason: str) -> None:
            planned.append(PlannedShot(row, Status.INVALID, reason))

        reason = name_problem('sequence', row.sequence) \
            or name_problem('shot', row.shot)
        if reason:
            invalid(reason)
            continue
        if None not in (row.frame_start, row.frame_end)                 and row.frame_end < row.frame_start:
            invalid(f'Ends ({row.frame_end}) before it starts ({row.frame_start})')
            continue
        category = next(
            (c for c in project.categories
             if c.casefold() == row.sequence.casefold()),
            None,
        )
        if category is not None:
            invalid(f"'{category}' is an asset category; a sequence cannot share its name")
            continue
        sequence_exists = row.sequence in project.sequences
        if not sequence_exists:
            other = same_but_case(row.sequence, project.sequences)
            if other is not None:
                invalid(f"Sequence '{row.sequence}' differs only by case from '{other}'")
                continue
            first = new_sequences.setdefault(row.sequence.casefold(), row.sequence)
            if first != row.sequence:
                invalid(f"Sequence '{row.sequence}' differs only by case from '{first}' above")
                continue
        existing = project.shots.get(row.sequence, frozenset())
        if row.shot in existing:
            planned.append(PlannedShot(
                row, Status.EXISTS, f'{row.sequence}/{row.shot} already exists',
            ))
            continue
        other = same_but_case(row.shot, existing)
        if other is not None:
            invalid(f"Shot '{row.shot}' differs only by case from '{other}'")
            continue
        seen = listed.setdefault(row.sequence, {})
        earlier = seen.get(row.shot.casefold())
        if earlier is not None:
            planned.append(PlannedShot(
                row, Status.DUPLICATE, f'Row {earlier + 1} already lists it',
            ))
            continue
        seen[row.shot.casefold()] = index
        planned.append(PlannedShot(
            row, Status.NEW, new_sequence=not sequence_exists,
        ))
    return Plan(tuple(planned))


class CreateError(Exception):
    """A write failed part-way; ``created`` holds what was written."""

    def __init__(self, message: str, created: list[Uri]):
        super().__init__(message)
        self.created = created


def shot_uri(sequence: str, shot: str) -> Uri:
    return SHOTS_URI / sequence / shot


def create(config, the_plan: Plan, scene: Uri | None = None) -> list[Uri]:
    """Write the plan's new sequences and shots; the shots' URIs.

    ``scene`` is a Root (``scenes:/…``) set on every new shot. Rows the
    plan skips or refuses are not written. A failed write raises
    :class:`CreateError` naming what was already created.
    """
    created: list[Uri] = []
    try:
        for sequence in the_plan.sequences_to_create:
            config.add_entity(SHOTS_URI / sequence, {})
        for row in the_plan.new:
            properties: dict = {
                'frame_start': row.frame_start,
                'frame_end': row.frame_end,
            }
            if scene is not None:
                properties['scene'] = str(scene)
            uri = shot_uri(row.sequence, row.shot)
            config.add_entity(uri, properties)
            created.append(uri)
    except Exception as exc:
        raise CreateError(str(exc), created) from exc
    return created
