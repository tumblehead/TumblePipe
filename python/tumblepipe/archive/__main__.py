import sys

from tumblepipe.archive.cli import main
from tumblepipe.util.logging import setup_logging

setup_logging()
sys.exit(main())
