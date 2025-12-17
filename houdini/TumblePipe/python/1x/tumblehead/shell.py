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
packages_path = pipeline_path / 'python' / '11'
sys.path.insert(0, str(packages_path))

# Add the additional python packages location to sys.path
external_path = pipeline_path / 'houdini' / 'Tumblehead' / 'python3.11libs' / 'external'
sys.path.insert(0, str(external_path))

# Set project environment variables
os.environ['OCIO'] = str(pipeline_path / 'ocio' / 'tumblehead.ocio')

# Load the tumblehead api
from tumblehead.api import default_client
api = default_client()