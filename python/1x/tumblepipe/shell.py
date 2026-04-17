from pathlib import Path
import logging
import sys
import os

# Setup logging
logging.basicConfig(
    level = logging.DEBUG,
    format = '%(message)s',
    stream = sys.stdout
)

# Ensure python packages are loaded
pipeline_path = Path(os.environ['TH_PIPELINE_PATH'])

# Add the shared python packages location to sys.path
packages_path = pipeline_path / 'python' / '1x'
sys.path.insert(0, str(packages_path))

# Set project environment variables
os.environ['OCIO'] = str(pipeline_path / 'ocio' / 'tumblehead.ocio')

# Load the tumblepipe api
from tumblepipe.api import default_client
api = default_client()
