"""
CSV Shot Import Tool

Imports shot data from a Google Sheets CSV export into the project database.
Only imports shots with frame ranges - no scenes or assets.

Usage:
    python -m tumblepipe.tools.csv_shot_import "path/to/shots.csv" --dry-run
    python -m tumblepipe.tools.csv_shot_import "path/to/shots.csv"
"""

import argparse
import csv
from pathlib import Path

from tumblepipe.api import default_client
from tumblepipe.config import shot_batch

# CSV structure constants
HEADER_ROW = 29  # 0-indexed, row containing "Cut #, Thumbnail, Sequence, ..."
DATA_START_ROW = 31  # 0-indexed, first actual shot data (skips sequence header row)

# Column names from CSV
COL_SEQUENCE = 'Sequence'
COL_SHOT_NAME = 'ShotName'
COL_START = 'Start'
COL_END = 'End'


def parse_csv(csv_path: Path) -> list[dict]:
    """Parse CSV file, returning list of row dicts."""
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    if len(all_rows) <= HEADER_ROW:
        return []

    headers = all_rows[HEADER_ROW]

    for row in all_rows[DATA_START_ROW:]:
        if len(row) < len(headers):
            row.extend([''] * (len(headers) - len(row)))
        row_dict = {headers[i]: row[i] for i in range(len(headers))}
        rows.append(row_dict)

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
    rows = parse_csv(csv_path)
    print(f"Found {len(rows)} rows")

    shot_rows = [
        shot_batch.ShotRow(
            row[COL_SEQUENCE].strip(), row[COL_SHOT_NAME].strip(),
            int(row[COL_START]), int(row[COL_END]),
        )
        for row in rows if is_valid_shot_row(row)
    ]
    plan = shot_batch.plan(shot_rows, shot_batch.snapshot(api.config))

    for sequence in plan.sequences_to_create:
        print(f"  [ADD SEQ] entity:/shots/{sequence}")
    for planned in plan.shots:
        uri = shot_batch.shot_uri(planned.row.sequence, planned.row.shot)
        if planned.status is shot_batch.Status.NEW:
            print(f"  [ADD] {uri} (frames {planned.row.frame_start}-{planned.row.frame_end})")
        elif planned.status is shot_batch.Status.INVALID:
            print(f"  [ERROR] {uri}: {planned.reason}")

    if not dry_run:
        shot_batch.create(api.config, plan)

    return {
        'shots_added': len(plan.new),
        'shots_skipped': len(plan.skipped),
        'shots_errors': len(plan.invalid),
        'rows_processed': len(shot_rows),
        'rows_skipped': len(rows) - len(shot_rows),
        'sequences_added': len(plan.sequences_to_create),
    }


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

    stats = sync_from_csv(args.csv_path, dry_run=args.dry_run)

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
