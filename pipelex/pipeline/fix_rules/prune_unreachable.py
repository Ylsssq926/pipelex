"""Fix rule: remove pipes not reachable from main_pipe."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


def compute_reachable_pipes(blueprint: PipelexBundleBlueprint) -> set[str]:
    """BFS from main_pipe to find all reachable pipe codes."""
    if not blueprint.main_pipe or not blueprint.pipe:
        return set()

    reachable: set[str] = set()
    queue: deque[str] = deque([blueprint.main_pipe])

    while queue:
        code = queue.popleft()
        if code in reachable:
            continue
        reachable.add(code)

        pipe_bp = blueprint.pipe.get(code)
        if pipe_bp is None:
            continue

        for dep_code in pipe_bp.pipe_dependencies:
            if dep_code not in reachable:
                queue.append(dep_code)

    return reachable


class PruneUnreachableRule(FixRule):
    code = "prune-unreachable"
    category = FixRuleCategory.PRUNING
    description = "Remove pipes not reachable from main_pipe"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        if not blueprint.main_pipe or not blueprint.pipe:
            return []

        reachable = compute_reachable_pipes(blueprint)
        all_codes = set(blueprint.pipe.keys())
        unreachable = all_codes - reachable

        if not unreachable:
            return []

        pipe_table = toml_doc.get("pipe")
        if pipe_table is None:
            return []

        fixes: list[FixResult] = []
        for code in sorted(unreachable):
            if code in pipe_table:
                del pipe_table[code]
                fixes.append(
                    FixResult(
                        fix_code=self.code,
                        pipe_code=code,
                        message=f"Removed unreachable pipe '{code}'",
                    )
                )

        return fixes
