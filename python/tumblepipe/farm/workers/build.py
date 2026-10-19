"""Write an entity's staged USD on the farm (``pipe.staged_build``)."""

from pathlib import Path
import json
import sys

from tumblepipe.config.timeline import BlockRange
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.specs import build as build_spec
from tumblepipe.farm.worker import headline
from tumblepipe.pipe.staged_build import build_staged
from tumblepipe.util.uri import Uri


def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Build as the job's context.json describes: its range, not the
    Deadline task's (one task spans it)."""
    print_env()
    headline('Config')
    print(json.dumps(config, indent=4))
    render_range = (
        BlockRange(config['first_frame'], config['last_frame'])
        if 'first_frame' in config else None
    )
    build_staged(
        Uri.parse(config['entity_uri']),
        Path(config['output_path']),
        render_range,
        config['channel_name']
    )
    return 0


if __name__ == '__main__':
    sys.exit(worker.run(build_spec.is_valid_context, from_config))
