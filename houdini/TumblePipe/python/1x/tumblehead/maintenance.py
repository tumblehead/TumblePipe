import logging
import sys

from .apps.deadline import Deadline

def main():
    deadline = Deadline()
    deadline.maintenance()
    return 0

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '[%(levelname)s] %(message)s',
        stream = sys.stdout
    )
    sys.exit(main())