import logging
import sys

from .apps.deadline import Deadline

def _error(msg):
    logging.error(msg)
    return 1

def main():
    try: deadline = Deadline()
    except: return _error('Could not connect to Deadline')
    deadline.maintenance()
    return 0

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '[%(levelname)s] %(message)s',
        stream = sys.stdout
    )
    sys.exit(main())