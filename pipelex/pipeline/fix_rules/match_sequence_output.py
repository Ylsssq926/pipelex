"""Fix rule: fix sequence output to match last step's output."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.core.pipes.exceptions import PipeValidationErrorType
from pipelex.pipe_controllers.sequence.pipe_sequence import PipeSequence
from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


class MatchSequenceOutputRule(FixRule):
    code = "match-sequence-output"
    category = FixRuleCategory.CORRECTION
    description = "Fix sequence output to match last step"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        if not validation_error or not pipes:
            return []

        # Collect PipeSequence codes with output errors
        fixable_types = {PipeValidationErrorType.INADEQUATE_OUTPUT_CONCEPT, PipeValidationErrorType.INADEQUATE_OUTPUT_MULTIPLICITY}
        affected_pipe_codes: set[str] = set()
        for error_data in validation_error.pipe_validation_errors:
            if error_data.error_type in fixable_types and error_data.pipe_code:
                affected_pipe_codes.add(error_data.pipe_code)

        if not affected_pipe_codes:
            return []

        pipe_by_code: dict[str, PipeAbstract] = {pipe.code: pipe for pipe in pipes}

        pipe_table = toml_doc.get("pipe")
        if pipe_table is None:
            return []

        fixes: list[FixResult] = []

        for pipe_code in affected_pipe_codes:
            the_pipe = pipe_by_code.get(pipe_code)
            if not isinstance(the_pipe, PipeSequence):
                continue

            if not the_pipe.sequential_sub_pipes:
                continue

            # Get the last step's pipe to determine correct output
            last_sub_pipe = the_pipe.sequential_sub_pipes[-1]
            last_pipe = pipe_by_code.get(last_sub_pipe.pipe_code)
            if last_pipe is None:
                continue

            # The correct output is the last step's output with its multiplicity
            correct_output = last_pipe.output.to_bundle_representation()

            # Check if the last sub-pipe has a multiplicity override
            if last_sub_pipe.output_multiplicity is not None:
                # Use the concept from last pipe but multiplicity from sub-pipe override
                concept_ref = last_pipe.output.concept.concept_ref
                mult = last_sub_pipe.output_multiplicity
                if isinstance(mult, bool):
                    correct_output = f"{concept_ref}[]"
                else:
                    correct_output = f"{concept_ref}[{mult}]"

            pipe_def = pipe_table.get(pipe_code)
            if pipe_def is None:
                continue

            old_output = pipe_def.get("output", "")
            if old_output == correct_output:
                continue

            pipe_def["output"] = correct_output
            fixes.append(
                FixResult(
                    fix_code=self.code,
                    pipe_code=pipe_code,
                    message=f"Set output to match last step: '{old_output}' -> '{correct_output}'",
                )
            )

        return fixes
