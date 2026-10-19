"""The Pipeline catalog's sidebar tree.

One folder per registered project (or its sections at the top level when
there is only one): Assets with its categories, Shots with its sequences —
each led by a Multis row when the project has Multis on that side — Roots,
and Recipes; the package's Nodes beside them. Built on the GUI thread, so
every count comes from the discovery cache or a uri listing, never from
building cards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.types import Collection

from . import recipes as recipes
from .types import cascade_counts

if TYPE_CHECKING:
    from .projects import PipelineProjectRegistry
    from .containers import ContainerManager
    from .discovery import EntityIndex, ProjectBuckets
    from .nodes import PipelineNodes
    from .recipes import RecipeManager


class Sidebar:
    """Builds the collections tree."""

    def __init__(
        self,
        *,
        registry: "PipelineProjectRegistry",
        buckets: "ProjectBuckets",
        index: "EntityIndex",
        containers: "ContainerManager",
        recipes: "RecipeManager",
        nodes: "PipelineNodes",
    ) -> None:
        self._registry = registry
        self._buckets = buckets
        self._index = index
        self._containers = containers
        self._recipes = recipes
        self._nodes = nodes

    def collections(self) -> list[Collection]:
        """Build the collections sidebar tree.

        Each registered project gets a top-level folder with its own
        ``Assets`` / ``Shots`` / ``Groups`` / ``Scenes`` sub-sections.
        When there is only one project the per-project wrapper is
        dropped and those four sections live at the top level for a
        flatter navigation.
        """
        if not self._registry:
            return []
        projects = list(self._registry.all())
        flatten = len(projects) == 1

        result: list[Collection] = []
        for proj in projects:
            sections = self._project_sections(proj)
            if not sections:
                continue
            if flatten:
                result.extend(sections)
            else:
                project_tag = f"project:{proj.name}"
                result.append(Collection(
                    id=f"project_section:{proj.name}",
                    label=proj.name,
                    tag=project_tag,
                    icon="folder",
                    children=tuple(sections),
                ))
        # Nodes ship with the package, so they sit beside the project
        # sections rather than inside each one.
        node_section = self._nodes.collection()
        if node_section is not None:
            result.append(node_section)
        return [cascade_counts(c) for c in result]

    def _project_sections(self, proj) -> list[Collection]:
        """Return [Assets, Shots, Roots, Recipes] sections for one
        project, omitting any that are empty. (Tasks is intentionally
        omitted — see below; the feature isn't implemented yet.) Multis
        are nested inside
        Assets / Shots based on the Multi's own context (they can't
        mix) — a single ``Multis`` subheader on each side filters the
        grid to that context's Multi cards. A side with no Multi has no
        subheader.
        """
        project_tag = f"project:{proj.name}"
        sections: list[Collection] = []

        # Build once, then inject under Assets / Shots below.
        asset_multis, shot_multis = self._containers.multis_sections(proj)

        # Multis go at the top of each side — artists favour multi-shot
        # / multi-asset workflows when one exists. A side with no Multi
        # gets no subheader: an empty "Multis" row is noise in a project
        # that doesn't use them, and "New Multi..." is already on the
        # Assets / Shots headers' own menus.
        cats = self._buckets.categories(proj.name)
        cat_children = [asset_multis] if asset_multis.children else []
        for cat in cats:
            count = self._index.count(proj.name, "assets", cat)
            cat_children.append(Collection(
                id=f"{proj.name}:category:{cat}",
                label=cat,
                count=count,
                tag=f"{project_tag}+category:{cat}",
                kind="category",
            ))
        sections.append(Collection(
            id=f"{proj.name}:assets_section",
            label="Assets",
            tag=f"{project_tag}+type:asset",
            icon="box",
            children=tuple(cat_children),
        ))

        seqs = self._buckets.sequences(proj.name)
        seq_children = [shot_multis] if shot_multis.children else []
        for seq in seqs:
            count = self._index.count(proj.name, "shots", seq)
            # default_view="list": shots browse better as list rows —
            # their department deck items expand inline in pipeline
            # (catalog) order, one row per department.
            seq_children.append(Collection(
                id=f"{proj.name}:sequence:{seq}",
                label=seq,
                count=count,
                tag=f"{project_tag}+sequence:{seq}",
                kind="sequence",
                default_view="list",
            ))
        sections.append(Collection(
            id=f"{proj.name}:shots_section",
            label="Shots",
            tag=f"{project_tag}+type:shot",
            icon="clapperboard",
            children=tuple(seq_children),
            default_view="list",
        ))

        scene_children = self._containers.root_collections(proj)
        sections.append(Collection(
            id=f"{proj.name}:scenes_section",
            label="Roots",
            icon="layers",
            tag=f"{project_tag}+type:scene",
            children=tuple(scene_children),
            # A header even with no Root yet, so right-click → New Root…
            # stays reachable.
            section=True,
        ))

        # Recipes: one row per Houdini context the project has recipes
        # in. Counts come from the recipe cache — this runs on the GUI
        # thread and must not walk the share (see recipes.py). The
        # section is always listed so its header menu offers New
        # Recipe… even before the first one is saved.
        recipe_children = [
            Collection(
                id=f"{proj.name}:recipe_context:{ctx}",
                label=recipes.context_label(ctx),
                count=n,
                tag=f"{project_tag}+{recipes.TYPE_TAG}+context:{ctx}",
                kind="recipe_context",
            )
            for ctx, n in sorted(
                self._recipes.context_counts(proj.name).items()
            )
        ]
        sections.append(Collection(
            id=f"{proj.name}:recipes_section",
            label="Recipes",
            icon="puzzle",
            tag=f"{project_tag}+{recipes.TYPE_TAG}",
            children=tuple(recipe_children),
        ))

        return sections
