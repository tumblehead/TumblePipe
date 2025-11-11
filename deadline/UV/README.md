# UV Deadline Plugin

A high-performance Deadline plugin that uses [Astral UV](https://github.com/astral-sh/uv) for Python environment and package management, replacing the micromamba-based Shell plugin.

## Overview

This plugin provides significant performance improvements over the micromamba-based Shell plugin:

- **80x faster** virtual environment creation
- **7x+ faster** package installation
- **Built-in Python version management** - no need to pre-install Python
- **Better package caching** - reduces redundant downloads across workers
- **Simpler architecture** - temporary venvs instead of named environments

## Architecture

### Environment Lifecycle

1. **Pre-Render**: Create temporary venv in `/tmp/uv-venvs/{random_hex}`
2. **Install Base Packages**: Install `python-dotenv` required by Runner.py
3. **Install Job Requirements**: Install packages from requirements.txt (if provided)
4. **Execute Task**: Run script using venv's Python interpreter
5. **Cleanup**: Delete temporary venv directory

### Key Differences from Shell Plugin

| Feature | Shell (micromamba) | UV |
|---------|-------------------|-----|
| Environment Type | Named environments | Temporary venvs |
| Environment Location | `~/.conda/envs/{name}` | `/tmp/uv-venvs/{hex}` |
| Creation Command | `micromamba create -n {name}` | `uv venv {path}` |
| Package Install | `micromamba run -n {name} pip install` | `uv pip install --python {venv}/bin/python` |
| Execution | `micromamba run -n {name} python` | `{venv}/bin/python` |
| Cleanup | `micromamba env remove -n {name}` | `rm -rf {venv}` |
| Python Management | Must be pre-installed | Auto-downloads as needed |

## Installation

### Worker Requirements

1. **Install UV** on all farm workers:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Verify Installation**:
   ```bash
   uv --version
   ```

3. **Pre-install Python versions** (optional, UV will auto-download):
   ```bash
   uv python install 3.11
   uv python install 3.12
   ```

### Deadline Repository Setup

1. Copy the `deadline/UV` directory to your Deadline repository's custom plugins folder:
   ```
   <DeadlineRepository>/custom/plugins/UV/
   ```

2. Restart Deadline workers to load the new plugin

## Plugin Configuration

### Job Submission

The plugin accepts the same parameters as the Shell plugin:

```python
from tumblehead.apps.deadline import Job

job = Job(
    script_path=Path('/path/to/script.py'),
    requirements_path=Path('/path/to/requirements.txt'),  # Optional
    'arg1', 'arg2'  # Script arguments
)

# Set job properties
job.name = 'My Render Job'
job.pool = 'general'
job.group = 'karma'
job.priority = 50

# Add environment variables
job.env.update({
    'MY_VAR': 'value'
})

# Submit (plugin will be set to 'UV' instead of 'Shell')
```

### Plugin Info Options

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `PythonVersion` | string | No | Python version (e.g., '3.11', '3.12'). Defaults to '3.11' |
| `ScriptFile` | path | Yes | Path to the Python script to execute |
| `EnvironmentFile` | path | No | Path to .env file with environment variables |
| `RequirementsFile` | path | No | Path to requirements.txt for package installation |
| `Arguments` | string | No | Command-line arguments to pass to the script |
| `StartupDirectory` | path | Yes | Working directory for script execution |
| `CacheDirectory` | path | No | UV package cache directory. Defaults to `/tmp/uv-cache` |
| `SingleFramesOnly` | boolean | No | Render one frame at a time. Defaults to True |

## Migration Guide

### Step 1: Update Job Submission Code

Update the `tumblehead.apps.deadline.Job.job_info()` method to support plugin selection:

```python
# In your job builder, change plugin name:
job_info = {
    'Plugin': 'UV',  # Changed from 'Shell'
    # ... rest of job_info
}
```

Or update the `Job` class to accept a plugin parameter:

```python
class Job:
    def __init__(self, script_path, requirements_path, *args, plugin='UV'):
        # ...
        self._plugin = plugin

    def job_info(self):
        return {
            'Plugin': self._plugin,
            # ...
        }
```

### Step 2: Test with Non-Critical Jobs

Start by testing UV with development or test renders:

```python
# Test render job with UV
test_job = Job(
    test_script_path,
    requirements_path,
    plugin='UV'  # Explicitly use UV
)
```

### Step 3: Gradual Migration

Migrate task types one at a time:

1. **Start**: Non-critical tasks (notifications, cleanup)
2. **Then**: Lightweight tasks (composite, denoise)
3. **Then**: Render tasks (partial renders first)
4. **Finally**: Full production renders

### Step 4: Monitor Performance

Compare metrics between Shell and UV plugins:

- Environment creation time
- Package installation time
- Total task overhead
- Worker disk usage

### Step 5: Update Default Plugin

Once confident, update the default plugin globally:

```python
# In tumblehead/apps/deadline.py
class Job:
    def job_info(self):
        return {
            'Plugin': 'UV',  # Changed default from 'Shell'
            # ...
        }
```

## Performance Tuning

### Cache Directory

The UV cache directory stores downloaded packages for reuse across jobs. Consider:

- **Shared Network Storage**: Mount a shared cache directory across workers
- **Local SSD**: Use local fast storage for cache if network latency is high
- **Cache Cleanup**: Periodically clean old cache entries

```python
# Set custom cache directory in plugin info
plugin_info = {
    'CacheDirectory': '/mnt/shared/uv-cache'  # Shared across workers
}
```

### Python Version Pre-installation

Pre-install Python versions on workers to avoid download overhead:

```bash
# On each worker
uv python install 3.11
uv python install 3.12
```

## Troubleshooting

### UV Not Found

**Symptom**: `Failed to create python environment`

**Solution**: Ensure UV is installed and in PATH:
```bash
which uv
# Should output: /home/user/.local/bin/uv
```

### Python Version Download Fails

**Symptom**: `Failed to create python environment` during venv creation

**Solution**: Pre-install Python version or check network connectivity:
```bash
uv python install 3.11
```

### Permission Errors

**Symptom**: `Failed to remove python environment`

**Solution**: Check permissions on `/tmp/uv-venvs/` directory:
```bash
chmod 1777 /tmp/uv-venvs/
```

### Package Installation Slow

**Symptom**: Long package installation times

**Solution**:
1. Use a shared cache directory across workers
2. Pre-populate cache with common packages
3. Consider using locked requirements for reproducibility:

```bash
# Create locked requirements
uv pip compile requirements.in -o requirements.txt --universal

# Use in jobs
job.requirements_path = locked_requirements_path
```

## Advanced Usage

### Using uv.lock Files

For even better reproducibility, consider using UV's project management:

```python
# Instead of requirements.txt, use uv.lock
# This requires modifying the plugin to support:
# uv sync --python {venv}/bin/python
```

### Inline Dependencies

For simple scripts, UV supports inline dependency declarations:

```python
# script.py
# /// script
# dependencies = [
#   "requests>=2.31.0",
#   "pillow>=10.0.0",
# ]
# ///

import requests
# ... script code
```

This could be used with `uv run` instead of creating a venv.

## Comparison: Micromamba vs UV

### Micromamba Advantages
- Mature ecosystem
- Conda package support
- Cross-platform consistency

### UV Advantages
- **10-100x faster** operations
- Simpler architecture (no conda)
- Better Python version management
- Built-in lockfile support
- Lower resource overhead
- Active development by Astral

## Future Enhancements

Potential improvements for the UV plugin:

1. **Support for uv.lock files** - Better reproducibility
2. **Persistent venv caching** - Reuse venvs across similar jobs
3. **uv run integration** - Skip venv creation for simple scripts
4. **Parallel package installation** - Even faster setup
5. **Health monitoring** - Report cache statistics and performance

## Contributing

When making changes to the UV plugin:

1. Test on a development worker first
2. Verify with multiple Python versions (3.11, 3.12)
3. Check both Linux and WSL environments
4. Update this README with any new features or gotchas

## References

- [UV Documentation](https://docs.astral.sh/uv/)
- [UV GitHub](https://github.com/astral-sh/uv)
- [Deadline Plugin Development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
