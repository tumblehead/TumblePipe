"""
CSV Shot Import Tool

Imports shot data from a Google Sheets CSV export into the project database.
Only imports shots with frame ranges - no scenes or assets.

The header row is the first row naming the Sequence, ShotName, Start and End
columns, wherever the sheet's preamble puts it (#32: a fixed row 30 silently
imported nothing from a sheet laid out differently). The browser's Shots
section offers the same import as **Import Shots from CSV…**, showing this
plan before it writes.

Usage:
    python -m tumblepipe.tools.csv_shot_import "path/to/shots.csv" --dry-run
    python -m tumblepipe.tools.csv_shot_import "path/to/shots.csv"
"""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.api import default_client
from tumblepipe.config import shot_batch

# Column names from CSV
COL_SEQUENCE = 'Sequence'
COL_SHOT_NAME = 'ShotName'
COL_START = 'Start'
COL_END = 'End'
#: The columns a row must name to be the header row.
REQUIRED_COLUMNS = (COL_SEQUENCE, COL_SHOT_NAME, COL_START, COL_END)


class NoHeaderRow(ValueError):
    """The sheet has no row naming every required column."""


def header_index(all_rows: list[list[str]]) -> int | None:
    """The first row holding every required column name (cells stripped)."""
    for index, row in enumerate(all_rows):
        if set(REQUIRED_COLUMNS) <= {cell.strip() for cell in row}:
            return index
    return None


def parse_csv(csv_path: Path) -> list[dict]:
    """The rows after the header row, each keyed by the header's names.

    Raises :class:`NoHeaderRow` when no row names Sequence, ShotName, Start
    and End: a sheet laid out some other way must say so, not import
    nothing.
    """
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        all_rows = list(csv.reader(f))

    header_row = header_index(all_rows)
    if header_row is None:
        raise NoHeaderRow(
            f'{Path(csv_path).name} has no header row naming the columns '
            + ', '.join(REQUIRED_COLUMNS)
        )
    headers = [cell.strip() for cell in all_rows[header_row]]

    rows = []
    for row in all_rows[header_row + 1:]:
        if len(row) < len(headers):
            row = row + [''] * (len(headers) - len(row))
        rows.append({headers[i]: row[i] for i in range(len(headers))})
    return rows


def is_valid_shot_row(row: dict) -> bool:
    """Check if row is a valid shot (not a sequence header or empty row)."""
    sequence = row.get(COL_SEQUENCE, '').strip()
    shot_name = row.get(COL_SHOT_NAME, '').strip()
    start = row.get(COL_START, '').strip()
    end = row.get(COL_END, '').strip()

    if not sequence or not shot_name:
        return False
    if ' - ' in sequence:  # Skip sequence headers like "sq010 - Introduction"
        return False
    try:
        int(start)
        int(end)
    except (ValueError, TypeError):
        return False
    return True


@dataclass(frozen=True)
class SheetPlan:
    """What importing a sheet would do: ``plan`` (``shot_batch.plan``) and
    how many rows were read and how many of them were shot rows."""
    plan: shot_batch.Plan
    rows_read: int
    shot_rows: int


def plan_sheet(csv_path: Path, config) -> SheetPlan:
    """Read the sheet and plan it against ``config``; writes nothing."""
    rows = parse_csv(csv_path)
    shot_rows = [
        shot_batch.ShotRow(
            row[COL_SEQUENCE].strip(), row[COL_SHOT_NAME].strip(),
            int(row[COL_START]), int(row[COL_END]),
        )
        for row in rows if is_valid_shot_row(row)
    ]
    plan = shot_batch.plan(shot_rows, shot_batch.snapshot(config))
    return SheetPlan(plan, len(rows), len(shot_rows))


def plan_lines(plan: shot_batch.Plan) -> list[str]:
    """The plan as ``[ADD SEQ]``, ``[ADD]`` and ``[ERROR]`` lines; existing
    shots are not listed (:func:`summary` counts them)."""
    lines = [f"[ADD SEQ] entity:/shots/{sequence}" for sequence in plan.sequences_to_create]
    for planned in plan.shots:
        # Text, not a Uri: a refused row's name ("SH 050") is not a valid
        # Uri segment, and building one aborted the whole import.
        uri = f"entity:/shots/{planned.row.sequence}/{planned.row.shot}"
        if planned.status is shot_batch.Status.NEW:
            lines.append(f"[ADD] {uri} (frames {planned.row.frame_start}-{planned.row.frame_end})")
        elif planned.status is shot_batch.Status.INVALID:
            lines.append(f"[ERROR] {uri}: {planned.reason}")
    return lines


def stats(sheet: SheetPlan) -> dict:
    plan = sheet.plan
    return {
        'shots_added': len(plan.new),
        'shots_skipped': len(plan.skipped),
        'shots_errors': len(plan.invalid),
        'rows_processed': sheet.shot_rows,
        'rows_skipped': sheet.rows_read - sheet.shot_rows,
        'sequences_added': len(plan.sequences_to_create),
    }


def write_plan(config, plan: shot_batch.Plan) -> list:
    """Create the plan's new sequences and shots; the shots made."""
    created = shot_batch.create(config, plan)
    # A shot made in a sequence a Multi lists joins that Multi.
    from tumblepipe.config.groups import place_new_members
    place_new_members(created)
    return created


def sync_from_csv(csv_path: Path, dry_run: bool = False) -> dict:
    """Import shots from CSV.

    The rows go through ``config.shot_batch``, whose name rules the Entity
    Editor shares: a shot that exists or that an earlier row lists is
    skipped, a row that cannot be created (a name that is not letters and
    digits, an end before the start, a case-only clash) is counted as an
    error, and only the new shots and the sequences they need are written.
    A dry run prints the same plan and writes nothing.
    """
    print("Connecting to API...")
    api = default_client()
    print("Connected.")

    print(f"Reading CSV: {csv_path}")
    sheet = plan_sheet(csv_path, api.config)
    print(f"Found {sheet.rows_read} rows")
    for line in plan_lines(sheet.plan):
        print(f"  {line}")

    if not dry_run:
        write_plan(api.config, sheet.plan)

    return stats(sheet)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Import shots from CSV')
    parser.add_argument('csv_path', type=Path, help='Path to CSV file')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')

    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: CSV file not found: {args.csv_path}")
        return 1

    if args.dry_run:
        print("=== DRY RUN MODE ===\n")

    try:
        stats = sync_from_csv(args.csv_path, dry_run=args.dry_run)
    except NoHeaderRow as error:
        print(f"Error: {error}")
        return 1

    print("\n=== Summary ===")
    print(f"Rows processed: {stats['rows_processed']}")
    print(f"Rows skipped: {stats['rows_skipped']}")
    print(f"Sequences added: {stats['sequences_added']}")
    print(f"Shots added: {stats['shots_added']}")
    print(f"Shots skipped (existing): {stats['shots_skipped']}")
    print(f"Shots errors: {stats['shots_errors']}")

    return 0


if __name__ == '__main__':
    exit(main())
