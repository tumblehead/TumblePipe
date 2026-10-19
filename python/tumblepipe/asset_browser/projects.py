"""Multi-project registry for the Pipeline catalog.

A small JSON-on-disk persistence layer that lets the asset browser browse
multiple Tumblehead projects (e.g. ``RND``, ``growth``) at once.

Lived in the TumbleTrove SDK (``asset_browser.core.projects``) until the
SDK stopped knowing about TumblePipe: nothing but this catalog ever used it,
and its ``TH_*`` environment is TumblePipe's.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from tumbletrove.common.persistence import read_json, write_json

log = logging.getLogger(__name__)

_VERSION = 1


@dataclass
class ProjectConfig:
    """Paths for a single Tumblehead project.

    ``name`` doubles as the project namespace prefix on asset IDs
    (``"<project>/<category>/<asset>"``) so it must be filesystem- and
    URI-safe (alphanum + ``_``-``-``).
    """

    name: str
    project_path: str   # → TH_PROJECT_PATH
    pipeline_path: str  # → TH_PIPELINE_PATH
    config_path: str    # → TH_CONFIG_PATH


class PipelineProjectRegistry:
    """Manages a list of registered Tumblehead projects with JSON
    persistence at ``~/.config/asset_browser/projects.json``.

    Distinct from :class:`asset_browser.api.registry.CatalogRegistry`:
    that one discovers *catalogs* (data sources); this one tracks the
    *projects* a single Pipeline catalog spans. Named with the
    ``Pipeline`` prefix to keep the two from being confused.
    """

    def __init__(self, config_path: Path) -> None:
        self._path = config_path
        # Insertion-order dict so the gear-icon list and the sidebar's
        # project sections render in the order the user added them.
        self._projects: dict[str, ProjectConfig] = {}

    # ── Persistence ────────────────────────────────────

    def load(self) -> None:
        data = read_json(self._path)
        if not isinstance(data, dict):
            return
        self._projects.clear()
        for entry in data.get("projects", []):
            try:
                proj = ProjectConfig(
                    name=str(entry["name"]),
                    project_path=str(entry["project_path"]),
                    pipeline_path=str(entry.get("pipeline_path", "")),
                    config_path=str(entry.get("config_path", "")),
                )
                self._projects[proj.name] = proj
            except (KeyError, TypeError, ValueError):
                log.exception("Skipping malformed project entry: %r", entry)

    def save(self) -> None:
        write_json(self._path, {
            "version": _VERSION,
            "projects": [asdict(p) for p in self._projects.values()],
        })

    # ── Read API ───────────────────────────────────────

    def all(self) -> list[ProjectConfig]:
        return list(self._projects.values())

    @property
    def names(self) -> list[str]:
        return list(self._projects.keys())

    def get(self, name: str) -> ProjectConfig | None:
        return self._projects.get(name)

    def __len__(self) -> int:
        return len(self._projects)

    def __bool__(self) -> bool:
        return bool(self._projects)

    # ── Mutation ───────────────────────────────────────

    def add(self, project: ProjectConfig, save: bool = True) -> None:
        self._projects[project.name] = project
        if save:
            self.save()

    def remove(self, name: str, save: bool = True) -> bool:
        if name not in self._projects:
            return False
        del self._projects[name]
        if save:
            self.save()
        return True

    def replace(self, name: str, new: ProjectConfig, save: bool = True) -> None:
        """Update an existing entry, possibly renaming it.

        If ``new.name`` differs from ``name``, the old key is removed
        and the new key is inserted in the same position.
        """
        if name not in self._projects:
            self.add(new, save=save)
            return
        # Preserve order: rebuild the dict with the new entry in place.
        rebuilt: dict[str, ProjectConfig] = {}
        for k, v in self._projects.items():
            if k == name:
                rebuilt[new.name] = new
            else:
                rebuilt[k] = v
        self._projects = rebuilt
        if save:
            self.save()

    # ── Bootstrap ──────────────────────────────────────

    @staticmethod
    def _paths_exist(name: str, project_path: str, config_path: str) -> bool:
        """True iff the env-derived project and config dirs exist on disk.

        Gate for :meth:`bootstrap_from_env` only — entries added through
        the settings UI go through their own form validation. Deliberately
        does not validate ``pipeline_path``: that one is hpm-owned
        (``TH_PIPELINE_PATH``) and re-resolved at Client-build time anyway.
        """
        for label, p in (("project", project_path), ("config", config_path)):
            # Explicit empty check: Path("") stringifies to "." which
            # would pass an is_dir() probe against the cwd.
            if not p or not Path(p).is_dir():
                log.warning(
                    "PipelineProjectRegistry: not registering/updating "
                    "project %r from environment — %s dir %r does not exist "
                    "(mistyped TH_PROJECT_PATH/TH_CONFIG_PATH, or the share "
                    "is unreachable right now)",
                    name, label, p,
                )
                return False
        return True

    def bootstrap_from_env(self) -> bool:
        """Ensure the env-driven project is in the registry AND up to date.

        When ``TH_PROJECT_PATH`` is set:

        - If the project name doesn't exist in the registry, add it
          from the current ``TH_*`` env vars.
        - If the project name does exist but the env-derived paths
          differ from the stored values, UPDATE the stored entry. The
          env vars are authoritative for the launch session — the
          on-disk ``projects.json`` is just a cache so non-env-launched
          sessions can still browse the same projects.

        Empty env values never clobber stored paths: launching Houdini
        without ``TH_*`` set leaves the existing registry untouched.

        Env paths are validated before they touch the registry: the
        project dir and its config dir must exist on disk, else the
        add/update is skipped (with a warning). A mistyped
        ``TH_PROJECT_PATH`` would otherwise be persisted forever and
        fail Client init with an "Invalid config path" warning on every
        subsequent launch. The flip side — an unreachable network share
        at launch time — just skips this session's refresh; the stored
        entry (if any) is left untouched.

        The project ``name`` is the leaf folder of ``TH_PROJECT_PATH``
        (e.g. ``"growth"`` for ``"P:/growth"``).

        Returns True iff the registry was modified (new entry added or
        existing entry updated).
        """
        proj_path = os.environ.get("TH_PROJECT_PATH", "").strip()
        if not proj_path:
            return False
        name = Path(proj_path).name or "default"

        env_pipeline_path = os.environ.get("TH_PIPELINE_PATH", "").strip()
        env_config_path = os.environ.get("TH_CONFIG_PATH", "").strip()

        existing = self._projects.get(name)
        if existing is None:
            # Resolve the config dir the same way the pipeline does when
            # TH_CONFIG_PATH is unset, so the persisted entry is complete.
            config_path = env_config_path or str(Path(proj_path) / "_config")
            if not self._paths_exist(name, proj_path, config_path):
                return False
            self.add(ProjectConfig(
                name=name,
                project_path=proj_path,
                pipeline_path=env_pipeline_path,
                config_path=config_path,
            ))
            log.info(
                "PipelineProjectRegistry: bootstrapped %s from environment", name,
            )
            return True

        # Existing entry — refresh paths whose env value disagrees.
        # Empty env values fall back to the stored value so launching
        # without TH_* doesn't blank out a registered project.
        new_project_path = proj_path or existing.project_path
        new_pipeline_path = env_pipeline_path or existing.pipeline_path
        new_config_path = env_config_path or existing.config_path

        if (
            new_project_path == existing.project_path
            and new_pipeline_path == existing.pipeline_path
            and new_config_path == existing.config_path
        ):
            return False

        # Validate AFTER the no-change check so an offline share doesn't
        # warn on every ordinary launch — only an actual refresh attempt
        # with unreachable paths is skipped (keeping the stored entry).
        if not self._paths_exist(name, new_project_path, new_config_path):
            return False

        # Log what changed so the user can trace stale-cache cases like
        # the asset-browser-shows-no-assets bug from issue #5: the
        # config dir was renamed _config → _config2, the env var
        # tracked it, but the stored entry didn't until now.
        if new_project_path != existing.project_path:
            log.info(
                "PipelineProjectRegistry: %s project_path env-updated %r → %r",
                name, existing.project_path, new_project_path,
            )
        if new_pipeline_path != existing.pipeline_path:
            log.info(
                "PipelineProjectRegistry: %s pipeline_path env-updated %r → %r",
                name, existing.pipeline_path, new_pipeline_path,
            )
        if new_config_path != existing.config_path:
            log.info(
                "PipelineProjectRegistry: %s config_path env-updated %r → %r",
                name, existing.config_path, new_config_path,
            )

        self._projects[name] = ProjectConfig(
            name=name,
            project_path=new_project_path,
            pipeline_path=new_pipeline_path,
            config_path=new_config_path,
        )
        self.save()
        return True
