"""Fix rule: remove redeclared native concepts."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.core.concepts.native.concept_native import NativeConceptCode
from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


class StripNativeConceptRedeclRule(FixRule):
    code = "strip-native-concept-redecl"
    category = FixRuleCategory.CORRECTION
    description = "Remove redeclared native concepts"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        concept_table = toml_doc.get("concept")
        if concept_table is None:
            return []

        native_codes = {native_code.value for native_code in NativeConceptCode.values_list()}
        fixes: list[FixResult] = []

        keys_to_remove: list[str] = []
        for key in list(concept_table.keys()):
            if key in native_codes:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del concept_table[key]
            fixes.append(
                FixResult(
                    fix_code=self.code,
                    concept_code=key,
                    message=f"Removed redeclared native concept '{key}'",
                )
            )

        # If the concept table is now empty, remove it entirely
        if not concept_table:
            del toml_doc["concept"]

        return fixes
