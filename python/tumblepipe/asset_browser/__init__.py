"""TumblePipe's asset-browser catalog.

The pipeline catalog TumblePipe contributes to TumbleTrove's asset browser,
declared from :func:`tumblepipe.startup.register_package`.

This was a top-level ``asset_browser_catalogs/`` directory until TumbleTrove
replaced catalog discovery with declaration. Its shape was dictated entirely
by the scanner: the entry had to be a single non-underscored ``pipeline.py``
because the globber skipped ``_``-prefixed files, and every companion module
carried a ``_pipeline_`` prefix to stay out of its way. They imported each
other absolutely off a ``sys.path`` entry the entry module injected, because
the path loader never attached them to a package.

None of that is true now, so the prefixes are gone, the ``sys.path`` mutation
is gone, and these are ordinary modules importing each other relatively.

Nothing is imported here: the catalog is built by :func:`factory.create_catalog`
when the browser panel first opens, and importing it eagerly would pull Qt and
the pipeline clients onto the Houdini launch path.
"""
