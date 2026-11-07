"""CLI: write the start frame into playblasts made before they recorded one.

    python scripts/fix_playblast_start_frames.py <project_path> [--shot SEQ/SHOT ...] [--apply]

The editor addresses a shot's movie by shot frame, which a playblast records
in itself since TumblePipe started writing a ``start_frame`` tag and a
timecode track (``tumblepipe.apps.mp4.start_frame_args``). For every movie in
the shots' playblast folders this says one of:

- ``has``: it records its start frame; left alone;
- ``fix``: its frame count is its shot's full range (frames plus roll), so
  it starts at that range's first frame; ``--apply`` remuxes it with that
  (no re-encode; the copy is checked before it replaces the movie);
- ``cannot place``: anything else, with the reason. Those need a new
  playblast.

Lists by default; nothing is written without ``--apply``. Run it with a
Python that can import tumblepipe and find Houdini's hffmpeg/hffprobe
(Houdini's own ``python313``, no licence needed). A movie another program
holds open (Resolve) is retried for a few seconds, then reported.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _activate(project: Path) -> None:
    """Point tumblepipe at ``project``. Set before tumblepipe is imported:
    a stale ``TH_PROJECT_PATH`` (a bare hython sets one) must not win."""
    project = project.resolve()
    if project.name == '_config':
        project = project.parent
    if not (project / '_config').is_dir():
        raise SystemExit(f'{project} is not a TumblePipe project (no _config)')
    os.environ['TH_PROJECT_PATH'] = str(project)
    os.environ['TH_CONFIG_PATH'] = str(project / '_config')
    os.environ.setdefault('TH_PIPELINE_PATH', str(REPO))
    sys.path.insert(0, str(REPO / 'python'))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description = __doc__.split('\n\n')[0])
    parser.add_argument('project', type = Path, help = 'the project root (or its _config)')
    parser.add_argument('--shot', action = 'append', default = [],
        help = 'only this shot, as SEQUENCE/SHOT (repeatable); default every shot')
    parser.add_argument('--houdini', default = None,
        help = 'the Houdini whose hffmpeg/hffprobe to use (e.g. 22.0.429); '
            'default $HOUDINI_VERSION, as under hython')
    parser.add_argument('--apply', action = 'store_true', help = 'write the start frames')
    args = parser.parse_args(argv)
    _activate(args.project)

    from tumblepipe.apps.houdini import FFmpeg, FFProbe
    from tumblepipe.pipe import playblast_start_frames as psf
    from tumblepipe.util.uri import Uri

    shots = (
        [Uri.parse(f'entity:/shots/{shot}') for shot in args.shot]
        if args.shot else psf.shot_uris()
    )
    ffprobe = FFProbe(args.houdini)
    ffmpeg = FFmpeg(args.houdini) if args.apply else None
    counts: Counter[str] = Counter()
    failed = 0
    for verdict in psf.survey(shots, ffprobe):
        # A movie that fails to rewrite counts as failed, not as fixed.
        if verdict.action != 'fix' or not args.apply:
            counts[verdict.action] += 1
        if verdict.action == 'has':
            continue
        if verdict.action == 'unplaceable':
            print(f'cannot place  {verdict.path}: {verdict.reason}')
            continue
        if not args.apply:
            print(f'would fix     {verdict.path}: starts at {verdict.start_frame}')
            continue
        try:
            psf.rewrite(verdict, ffmpeg, ffprobe, psf.framerate_of(verdict.shot))
        except Exception as error:
            failed += 1
            print(f'FAILED        {verdict.path}: {error}')
            continue
        counts['fix'] += 1
        print(f'fixed         {verdict.path}: starts at {verdict.start_frame}')
    print(
        f'\n{counts["has"]} record a start frame, {counts["fix"]} '
        f'{"fixed" if args.apply else "to fix"}, {counts["unplaceable"]} cannot be placed'
        + (f', {failed} failed' if failed else '')
        + ('' if args.apply or not counts['fix'] else '. Run again with --apply to write.')
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
