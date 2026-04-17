from pathlib import Path
import logging
import sys
import shutil
import time

# Add tumblehead python packages path
tumblehead_packages_path = Path('/mnt/y/_pipeline/python')
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

def _headline(msg):
    logging.info(f' {msg} '.center(80, '='))

def main():

    # Clean up old UV temporary venvs (older than 1 day)
    _headline('Cleaning up old UV venvs')
    uv_venvs_path = Path('/tmp/uv-venvs')
    if uv_venvs_path.exists():
        current_time = time.time()
        one_day_ago = current_time - (24 * 60 * 60)

        for venv_dir in uv_venvs_path.iterdir():
            if not venv_dir.is_dir(): continue

            # Check if older than 1 day
            mtime = venv_dir.stat().st_mtime
            if mtime < one_day_ago:
                logging.info(f'Removing old venv: {venv_dir.name}')
                try:
                    shutil.rmtree(venv_dir)
                except Exception as e:
                    logging.warning(f'Failed to remove {venv_dir.name}: {e}')

    # Clean up old UV cache (optional - keeps cache under control)
    _headline('Cleaning up old UV cache entries')
    uv_cache_path = Path('/tmp/uv-cache')
    if uv_cache_path.exists():
        current_time = time.time()
        one_week_ago = current_time - (7 * 24 * 60 * 60)

        removed_count = 0
        for cache_file in uv_cache_path.rglob('*'):
            if not cache_file.is_file(): continue

            # Check if older than 1 week
            mtime = cache_file.stat().st_mtime
            if mtime < one_week_ago:
                try:
                    cache_file.unlink()
                    removed_count += 1
                except Exception as e:
                    logging.warning(f'Failed to remove cache file: {e}')

        logging.info(f'Removed {removed_count} old cache files')

    # Done
    _headline('Done')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.parse_args()
    return main()

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.INFO,
        format = '[%(levelname)s] %(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())