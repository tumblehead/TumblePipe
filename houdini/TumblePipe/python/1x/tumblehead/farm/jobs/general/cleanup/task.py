from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path('/mnt/y/_pipeline/python')
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.apps.micromamba import Micromamba

def _headline(msg):
    logging.info(f' {msg} '.center(80, '='))

def main():

    # Prepare
    m = Micromamba()

    # Remove all environments except 'base' and 'general'
    _headline('Removing stale environments')
    for env_name in m.list_envs():
        if env_name == 'base': continue
        if env_name == 'general': continue
        logging.info(f'Removing environment: {env_name}')
        m.remove_env(env_name)

    # Done
    _headline('Done')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    return main()

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.INFO,
        format = '[%(levelname)s] %(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())