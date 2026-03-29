"""Fix rule: remove concepts not referenced by any reachable pipe."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.core.concepts.concept_blueprint import ConceptBlueprint, ConceptStructureBlueprint
from pipelex.core.concepts.native.concept_native import NativeConceptCode
from pipelex.core.pipes.variable_multiplicity import parse_concept_with_multiplicity
from pipelex.pipe_controllers.parallel.pipe_parallel_blueprint import PipeParallelBlueprint
from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory
from pipelex.pipeline.fix_rules.prune_unreachable import compute_reachable_pipes

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


def _extract_bare_concept_code(concept_ref_with_multiplicity: str) -> str:
    """Extract the bare concept code from a ref that may include domain prefix and multiplicity."""
    parsed = parse_concept_with_multiplicity(concept_ref_with_multiplicity)
    ref = parsed.concept_ref_or_code
    # Strip domain prefix if present (e.g., "native.Text" -> "Text", "my_domain.Foo" -> "Foo")
    if "." in ref:
        return ref.rsplit(".", 1)[1]
    return ref


class PruneUnusedConceptsRule(FixRule):
    code = "prune-unused-concepts"
    category = FixRuleCategory.PRUNING
    description = "Remove concepts not referenced by any pipe"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        if not blueprint.concept or not blueprint.pipe:
            return []

        # Step 1: Find reachable pipes
        reachable_pipe_codes = compute_reachable_pipes(blueprint)

        # Step 2: Collect concept refs from reachable pipes
        used_concepts: set[str] = set()
        for pipe_code in reachable_pipe_codes:
            pipe_bp = blueprint.pipe.get(pipe_code)
            if pipe_bp is None:
                continue

            # Output concept
            used_concepts.add(_extract_bare_concept_code(pipe_bp.output))

            # Input concepts
            if pipe_bp.inputs:
                used_concepts.update(_extract_bare_concept_code(input_ref) for input_ref in pipe_bp.inputs.values())

            # Combined output for PipeParallel
            if isinstance(pipe_bp, PipeParallelBlueprint) and pipe_bp.combined_output:
                used_concepts.add(_extract_bare_concept_code(pipe_bp.combined_output))

        # Step 3: Transitively walk concept definitions
        # Concepts may reference other concepts via refines and structure fields
        changed = True
        while changed:
            changed = False
            for concept_code, concept_bp in blueprint.concept.items():
                if concept_code not in used_concepts:
                    continue
                if not isinstance(concept_bp, ConceptBlueprint):
                    continue

                # Check refines
                if concept_bp.refines:
                    refined_code = _extract_bare_concept_code(concept_bp.refines)
                    if refined_code not in used_concepts:
                        used_concepts.add(refined_code)
                        changed = True

                # Check structure fields
                if isinstance(concept_bp.structure, dict):
                    for field_bp in concept_bp.structure.values():
                        if isinstance(field_bp, ConceptStructureBlueprint):
                            if field_bp.concept_ref:
                                ref_code = _extract_bare_concept_code(field_bp.concept_ref)
                                if ref_code not in used_concepts:
                                    used_concepts.add(ref_code)
                                    changed = True
                            if field_bp.item_concept_ref:
                                ref_code = _extract_bare_concept_code(field_bp.item_concept_ref)
                                if ref_code not in used_concepts:
                                    used_concepts.add(ref_code)
                                    changed = True

        # Step 4: Remove unused concept definitions (skip native concepts — they're not local)
        native_codes = {native_code.value for native_code in NativeConceptCode.values_list()}
        concept_table = toml_doc.get("concept")
        if concept_table is None:
            return []

        fixes: list[FixResult] = []
        unused_codes = set(blueprint.concept.keys()) - used_concepts - native_codes

        for code in sorted(unused_codes):
            if code in concept_table:
                del concept_table[code]
                fixes.append(
                    FixResult(
                        fix_code=self.code,
                        concept_code=code,
                        message=f"Removed unused concept '{code}'",
                    )
                )

        # If the concept table is now empty, remove it entirely
        if concept_table is not None and not concept_table:
            del toml_doc["concept"]

        return fixes
