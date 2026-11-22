import sys

from tumblepipe.cleanup.cli import main

if __name__ == '__main__':
    from tumblepipe.util.logging import setup_logging
    setup_logging()
    sys.exit(main())
