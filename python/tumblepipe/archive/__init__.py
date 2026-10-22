"""Archive a project into self-describing zip files, and restore one.

The design is ``designs/project-archive.md``. In short:

- ``roots``     — where a project's files live (``storage.json``), and the
                  prefix each root gets inside an archive;
- ``inventory`` — walk those roots and classify every file (workfile,
                  export version, cache, backup, client material, ...);
- ``plan``      — decide per profile (proof / latest / full) what goes in,
                  with a reason for everything left out;
- ``pack``      — write the plan as zip parts plus a manifest, verify an
                  archive against its manifest, and restore one.

Nothing here touches the live project: archiving only reads it.

Pure Python, no ``hou``: it runs from the TumbleTrove Desktop Scripts panel,
a shell, or the farm.
"""
