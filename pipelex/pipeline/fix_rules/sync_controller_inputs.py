"""Fix rule: align controller pipe inputs with their needed_inputs()."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from typing_extensions import override

from pipelex.core.pipes.exceptions import PipeValidationErrorType
from pipelex.pipe_controllers.pipe_controller import PipeController
from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


class SyncControllerInputsRule(FixRule):
    code = "sync-controller-inputs"
    category = FixRuleCategory.CORRECTION
    description = "Align controller pipe inputs with their requirements"

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

        # Collect pipe codes that have MISSING_INPUT_VARIABLE or EXTRANEOUS_INPUT_VARIABLE errors
        fixable_types = {PipeValidationErrorType.MISSING_INPUT_VARIABLE, PipeValidationErrorType.EXTRANEOUS_INPUT_VARIABLE}
        affected_pipe_codes: set[str] = set()
        for error_data in validation_error.pipe_validation_errors:
            if error_data.error_type in fixable_types and error_data.pipe_code:
                affected_pipe_codes.add(error_data.pipe_code)

        if not affected_pipe_codes:
            return []

        # Build pipe lookup by code
        pipe_by_code: dict[str, PipeAbstract] = {pipe.code: pipe for pipe in pipes}

        pipe_table = toml_doc.get("pipe")
        if pipe_table is None:
            return []

        fixes: list[FixResult] = []

        for pipe_code in affected_pipe_codes:
            the_pipe = pipe_by_code.get(pipe_code)
            if the_pipe is None:
                continue

            # Only fix controller pipes — their inputs are fully derivable
            if not isinstance(the_pipe, PipeController):
                continue

            needed = the_pipe.needed_inputs()
            pipe_def = pipe_table.get(pipe_code)
            if pipe_def is None:
                continue

            # Build the new inputs inline table
            old_inputs = dict(pipe_def.get("inputs", {})) if pipe_def.get("inputs") else {}
            new_inputs = tomlkit.inline_table()
            for var_name, stuff_spec in sorted(needed.root.items()):  # pyright: ignore[reportCallIssue]
                new_inputs[var_name] = stuff_spec.to_bundle_representation()

            new_inputs_dict = dict(new_inputs)
            if new_inputs_dict == old_inputs:
                continue

            # Compute diff for the message
            added = set(new_inputs_dict.keys()) - set(old_inputs.keys())
            removed = set(old_inputs.keys()) - set(new_inputs_dict.keys())

            parts: list[str] = []
            if added:
                parts.append(f"added {', '.join(sorted(added))}")
            if removed:
                parts.append(f"removed {', '.join(sorted(removed))}")
            # Check for changed types
            for var_name in set(new_inputs_dict.keys()) & set(old_inputs.keys()):
                if new_inputs_dict[var_name] != old_inputs[var_name]:
                    parts.append(f"changed '{var_name}' from '{old_inputs[var_name]}' to '{new_inputs_dict[var_name]}'")

            if new_inputs_dict:
                pipe_def["inputs"] = new_inputs
            elif "inputs" in pipe_def:
                del pipe_def["inputs"]

            diff_desc = "; ".join(parts) if parts else "rebuilt inputs"
            fixes.append(
                FixResult(
                    fix_code=self.code,
                    pipe_code=pipe_code,
                    message=f"Synced inputs with needed_inputs(): {diff_desc}",
                )
            )

        return fixes
