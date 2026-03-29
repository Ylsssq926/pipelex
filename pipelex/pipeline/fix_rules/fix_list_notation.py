"""Fix rule: add [] for multiplicity mismatches in pipe inputs."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.core.pipes.variable_multiplicity import parse_concept_with_multiplicity
from pipelex.pipe_controllers.pipe_controller import PipeController
from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


class FixListNotationRule(FixRule):
    code = "fix-list-notation"
    category = FixRuleCategory.CORRECTION
    description = "Add [] for multiplicity mismatches"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        if not pipes:
            return []

        pipe_table = toml_doc.get("pipe")
        if pipe_table is None:
            return []

        fixes: list[FixResult] = []

        for the_pipe in pipes:
            # Only fix controller pipes where inputs are derivable
            if not isinstance(the_pipe, PipeController):
                continue

            pipe_def = pipe_table.get(the_pipe.code)
            if pipe_def is None:
                continue

            toml_inputs = pipe_def.get("inputs")
            if not toml_inputs:
                continue

            needed = the_pipe.needed_inputs()

            for var_name in list(toml_inputs.keys()):
                toml_value = toml_inputs[var_name]
                if not isinstance(toml_value, str):
                    continue

                # Parse what the TOML declares
                declared = parse_concept_with_multiplicity(toml_value)

                # Check what needed_inputs says
                needed_spec = needed.root.get(var_name)
                if needed_spec is None:
                    continue

                # If needed says multiple but declared says singular, add []
                if needed_spec.multiplicity is not None and declared.multiplicity is None:
                    new_value: str
                    if isinstance(needed_spec.multiplicity, bool):
                        new_value = f"{toml_value}[]"
                    else:
                        new_value = f"{toml_value}[{needed_spec.multiplicity}]"

                    toml_inputs[var_name] = new_value
                    fixes.append(
                        FixResult(
                            fix_code=self.code,
                            pipe_code=the_pipe.code,
                            message=f"Added multiplicity to input '{var_name}': '{toml_value}' -> '{new_value}'",
                        )
                    )

        return fixes
