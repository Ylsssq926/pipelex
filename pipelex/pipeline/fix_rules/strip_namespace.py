"""Fix rule: strip same-domain namespace prefixes from pipe codes."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from pipelex.pipeline.fix_rules.base import FixResult, FixRule, FixRuleCategory

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


def _should_strip(ref: str, domain: str, local_pipe_codes: set[str]) -> bool:
    """Check whether a dotted pipe reference should be stripped.

    A reference should be stripped when:
    - It contains a dot
    - The prefix matches the bundle's domain
    - The bare code (after stripping) exists in the bundle's pipe definitions
    """
    if "." not in ref:
        return False
    prefix, bare = ref.rsplit(".", 1)
    return prefix == domain and bare in local_pipe_codes


def _strip_ref(ref: str, domain: str) -> str:
    """Remove the domain prefix from a pipe reference."""
    prefix = f"{domain}."
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref


class StripNamespaceRule(FixRule):
    code = "strip-namespace"
    category = FixRuleCategory.CORRECTION
    description = "Remove same-domain pipe code prefixes"

    @override
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        domain = blueprint.domain
        if not blueprint.pipe:
            return []

        local_pipe_codes = set(blueprint.pipe.keys())
        # Also collect bare codes from dotted keys (the stripped version)
        for code in list(local_pipe_codes):
            if "." in code:
                _, bare = code.rsplit(".", 1)
                local_pipe_codes.add(bare)

        fixes: list[FixResult] = []
        pipe_table = toml_doc.get("pipe")
        if pipe_table is None:
            return []

        # Phase 1: Rename pipe definition keys
        keys_to_rename: list[tuple[str, str]] = []
        for key in list(pipe_table.keys()):
            if _should_strip(key, domain, local_pipe_codes):
                bare_code = _strip_ref(key, domain)
                keys_to_rename.append((key, bare_code))

        for old_key, new_key in keys_to_rename:
            value = pipe_table[old_key]
            # Preserve the table by removing old key and adding with new key
            del pipe_table[old_key]
            pipe_table[new_key] = value
            fixes.append(
                FixResult(
                    fix_code=self.code,
                    pipe_code=new_key,
                    message=f"Stripped same-domain prefix '{domain}.' from pipe definition '{old_key}'",
                )
            )

        # Update local_pipe_codes with renamed keys
        for old_key, new_key in keys_to_rename:
            local_pipe_codes.discard(old_key)
            local_pipe_codes.add(new_key)

        # Phase 2: Update main_pipe
        main_pipe_val = toml_doc.get("main_pipe")
        if main_pipe_val and isinstance(main_pipe_val, str) and _should_strip(main_pipe_val, domain, local_pipe_codes):
            new_main = _strip_ref(main_pipe_val, domain)
            toml_doc["main_pipe"] = new_main
            fixes.append(
                FixResult(
                    fix_code=self.code,
                    message=f"Stripped same-domain prefix from main_pipe: '{main_pipe_val}' -> '{new_main}'",
                )
            )

        # Phase 3: Update internal references within pipe definitions
        for pipe_code in list(pipe_table.keys()):
            pipe_def = pipe_table[pipe_code]
            if not isinstance(pipe_def, dict):
                continue

            pipe_type = pipe_def.get("type")

            # PipeSequence: steps[*].pipe
            if pipe_type == "PipeSequence":
                steps = pipe_def.get("steps")
                if steps:
                    for step in steps:
                        step_pipe = step.get("pipe")
                        if step_pipe and _should_strip(step_pipe, domain, local_pipe_codes):
                            new_ref = _strip_ref(step_pipe, domain)
                            step["pipe"] = new_ref
                            fixes.append(
                                FixResult(
                                    fix_code=self.code,
                                    pipe_code=pipe_code,
                                    message=f"Stripped prefix from step reference: '{step_pipe}' -> '{new_ref}'",
                                )
                            )

            # PipeParallel: branches[*].pipe
            elif pipe_type == "PipeParallel":
                branches = pipe_def.get("branches")
                if branches:
                    for branch in branches:
                        branch_pipe = branch.get("pipe")
                        if branch_pipe and _should_strip(branch_pipe, domain, local_pipe_codes):
                            new_ref = _strip_ref(branch_pipe, domain)
                            branch["pipe"] = new_ref
                            fixes.append(
                                FixResult(
                                    fix_code=self.code,
                                    pipe_code=pipe_code,
                                    message=f"Stripped prefix from branch reference: '{branch_pipe}' -> '{new_ref}'",
                                )
                            )

            # PipeBatch: branch_pipe_code
            elif pipe_type == "PipeBatch":
                bpc = pipe_def.get("branch_pipe_code")
                if bpc and _should_strip(bpc, domain, local_pipe_codes):
                    new_ref = _strip_ref(bpc, domain)
                    pipe_def["branch_pipe_code"] = new_ref
                    fixes.append(
                        FixResult(
                            fix_code=self.code,
                            pipe_code=pipe_code,
                            message=f"Stripped prefix from branch_pipe_code: '{bpc}' -> '{new_ref}'",
                        )
                    )

            # PipeCondition: outcomes values + default_outcome
            elif pipe_type == "PipeCondition":
                outcomes = pipe_def.get("outcomes")
                if outcomes:
                    for outcome_key in list(outcomes.keys()):
                        outcome_pipe = outcomes[outcome_key]
                        if isinstance(outcome_pipe, str) and _should_strip(outcome_pipe, domain, local_pipe_codes):
                            new_ref = _strip_ref(outcome_pipe, domain)
                            outcomes[outcome_key] = new_ref
                            fixes.append(
                                FixResult(
                                    fix_code=self.code,
                                    pipe_code=pipe_code,
                                    message=f"Stripped prefix from outcome '{outcome_key}': '{outcome_pipe}' -> '{new_ref}'",
                                )
                            )

                default_outcome = pipe_def.get("default_outcome")
                if default_outcome and isinstance(default_outcome, str) and _should_strip(default_outcome, domain, local_pipe_codes):
                    new_ref = _strip_ref(default_outcome, domain)
                    pipe_def["default_outcome"] = new_ref
                    fixes.append(
                        FixResult(
                            fix_code=self.code,
                            pipe_code=pipe_code,
                            message=f"Stripped prefix from default_outcome: '{default_outcome}' -> '{new_ref}'",
                        )
                    )

        return fixes
