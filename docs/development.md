# Contributing and development

TumblePipe is developed privately and mirrored to the public GitHub
repository at [tumblehead/TumblePipe](https://github.com/tumblehead/TumblePipe).
We welcome issue reports and questions, but cannot guarantee response times
or feature requests.

## Reporting issues

File issues on
[GitHub Issues](https://github.com/tumblehead/TumblePipe/issues). When
reporting a bug, include:

- Houdini version.
- TumblePipe version (`hpm show tumblepipe`, or check `hpm.toml`).
- Operating system.
- A minimal reproduction — a small .hip file or a short script — if
  possible.

## Building the documentation locally

The docs are written in [MyST Markdown](https://myst-parser.readthedocs.io)
and built with [Sphinx](https://www.sphinx-doc.org). From the package root:

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate   # Windows: .venv-docs\Scripts\activate
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html` in a browser.

## Documentation hosting

The rendered docs live at
[tumblepipe.readthedocs.io](https://tumblepipe.readthedocs.io). They are
rebuilt automatically on every push to `main` in the public mirror repo.
The RTD build configuration is `.readthedocs.yaml` at the repository root.

## License

TumblePipe is released under the [MIT License](https://github.com/tumblehead/TumblePipe/blob/main/LICENSE).
