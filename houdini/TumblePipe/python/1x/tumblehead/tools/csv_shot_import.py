"""
CSV Shot Import Tool

Imports shot data from a Google Sheets CSV export into the project database.
Only imports shots with frame ranges - no scenes or assets.

Usage:
    python -m tumblehead.tools.csv_shot_import "path/to/shots.csv" --dry-run
    python -m tumblehead.tools.csv_shot_import "path/to/shots.csv"
"""

import argparse
import csv
from pathlib import Path

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

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


def shot_exists(api, uri: Uri) -> bool:
    """Check if a shot entity already exists."""
    return api.config.get_properties(uri) is not None


def ensure_sequence(api, sequence: str, dry_run: bool = False) -> bool:
    """Ensure sequence exists with proper schema. Returns True if created."""
    seq_uri = Uri.parse_unsafe(f'entity:/shots/{sequence}')
    seq_schema = Uri.parse_unsafe('schemas:/entity/shots/sequence')

    if api.config.get_properties(seq_uri) is not None:
        return False

    if dry_run:
        print(f"  [ADD SEQ] {seq_uri}")
    else:
        api.config.add_entity(seq_uri, {}, seq_schema)
        print(f"  [ADD SEQ] {seq_uri}")
    return True


def sync_from_csv(csv_path: Path, dry_run: bool = False) -> dict:
    """Import shots from CSV."""
    print("Connecting to API...")
    api = default_client()
    print("Connected.")

    stats = {
        'shots_added': 0,
        'shots_skipped': 0,
        'shots_errors': 0,
        'rows_processed': 0,
        'rows_skipped': 0,
        'sequences_added': 0,
    }

    print(f"Reading CSV: {csv_path}")
    rows = parse_csv(csv_path)
    print(f"Found {len(rows)} rows")

    shot_schema = Uri.parse_unsafe('schemas:/entity/shots/sequence/shot')
    created_sequences = set()

    for row in rows:
        if not is_valid_shot_row(row):
            stats['rows_skipped'] += 1
            continue

        stats['rows_processed'] += 1

        sequence = row[COL_SEQUENCE].strip()
        shot_name = row[COL_SHOT_NAME].strip()
        frame_start = int(row[COL_START])
        frame_end = int(row[COL_END])

        shot_uri = Uri.parse_unsafe(f'entity:/shots/{sequence}/{shot_name}')

        # Ensure sequence exists with proper schema
        if sequence not in created_sequences:
            if ensure_sequence(api, sequence, dry_run):
                stats['sequences_added'] += 1
            created_sequences.add(sequence)

        if shot_exists(api, shot_uri):
            stats['shots_skipped'] += 1
            continue

        shot_properties = {
            'frame_start': frame_start,
            'frame_end': frame_end,
            'roll_start': 0,
            'roll_end': 0,
        }

        if dry_run:
            print(f"  [ADD] {shot_uri} (frames {frame_start}-{frame_end})")
            stats['shots_added'] += 1
        else:
            try:
                api.config.add_entity(shot_uri, shot_properties, shot_schema)
                print(f"  [ADD] {shot_uri}")
                stats['shots_added'] += 1
            except Exception as e:
                print(f"  [ERROR] {shot_uri}: {e}")
                stats['shots_errors'] += 1

    return stats


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
