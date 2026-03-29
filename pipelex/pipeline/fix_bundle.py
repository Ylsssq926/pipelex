"""Orchestrator for auto-fixing deterministic issues in .mthds bundles."""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import tomlkit
from pydantic import BaseModel, ConfigDict, ValidationError

from pipelex import log
from pipelex.core.interpreter.exceptions import MthdsDecodeError, PipelexInterpreterError
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.core.pipes.exceptions import PipeFactoryError, PipeValidationError
from pipelex.core.pipes.handle_pipe_errors import (
    categorize_pipe_factory_error,
    categorize_pipe_validation_error,
    categorize_pipe_validation_with_libraries_error,
)
from pipelex.core.validation import report_validation_error
from pipelex.hub import get_library_manager, resolve_library_dirs, set_current_library
from pipelex.pipe_run.dry_run import DryRunError, dry_run_pipes
from pipelex.pipe_run.exceptions import PipeRunError
from pipelex.pipeline.fix_rules.base import FixResult  # noqa: TC001 - used by Pydantic model at runtime
from pipelex.pipeline.validate_bundle import ValidateBundleError, validate_bundle

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.fix_rules.base import FixRule


class FixBundleResult(BaseModel):
    """Result of fixing a single bundle."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bundle_path: str
    fixed: bool
    fixes_applied: list[FixResult]
    original_text: str
    fixed_text: str
    remaining_errors: list[dict[str, Any]] | None = None


def _resolve_rules(
    selected_rules: list[str] | None,
    ignored_rules: list[str] | None,
    prune: bool,
) -> list[FixRule]:
    """Determine which fix rules to run based on CLI flags."""
    from pipelex.pipeline.fix_rules.registry import ALL_RULE_CODES, ALL_RULES, DEFAULT_RULES, PRUNING_RULES  # noqa: PLC0415

    if selected_rules and ignored_rules:
        msg = "--select and --ignore are mutually exclusive"
        raise ValueError(msg)

    if selected_rules:
        unknown = set(selected_rules) - ALL_RULE_CODES
        if unknown:
            msg = f"Unknown fix rule(s): {', '.join(sorted(unknown))}"
            raise ValueError(msg)
        return [rule for rule in ALL_RULES if rule.code in selected_rules]

    active: list[FixRule] = list(DEFAULT_RULES)
    if prune:
        active.extend(PRUNING_RULES)
    if ignored_rules:
        unknown = set(ignored_rules) - ALL_RULE_CODES
        if unknown:
            msg = f"Unknown fix rule(s): {', '.join(sorted(unknown))}"
            raise ValueError(msg)
        active = [rule for rule in active if rule.code not in ignored_rules]
    return active


async def _load_blueprint_and_pipes(
    mthds_content: str,
    library_dirs: Sequence[Path] | None,
) -> tuple[PipelexBundleBlueprint | None, list[PipeAbstract], ValidateBundleError | None]:
    """Load a bundle, collecting as much data as possible even on partial failure.

    Returns:
        (blueprint, pipes, error) — blueprint and pipes may be populated even when error is not None.
    """
    library_manager = get_library_manager()
    library_id, library = library_manager.open_library()
    set_current_library(library_id=library_id)

    effective_dirs, _ = resolve_library_dirs(library_dirs)
    if effective_dirs:
        library_manager.load_libraries(library_id=library_id, library_dirs=effective_dirs)

    # Phase 1: Parse blueprint
    blueprint: PipelexBundleBlueprint | None = None
    try:
        blueprint = PipelexInterpreter.make_pipelex_bundle_blueprint(mthds_content=mthds_content)
    except PipelexInterpreterError as interpreter_error:
        error = ValidateBundleError(
            message=interpreter_error.message,
            pipelex_bundle_blueprint_validation_errors=interpreter_error.validation_errors,
        )
        error.__cause__ = interpreter_error
        return None, [], error
    except MthdsDecodeError as decode_error:
        msg = f"TOML syntax error at line {decode_error.lineno}, column {decode_error.colno}: {decode_error.message}"
        error = ValidateBundleError(message=msg)
        error.__cause__ = decode_error
        return None, [], error
    except ValidationError as validation_error:
        pipe_validation_errors = categorize_pipe_validation_error(validation_error=validation_error)
        validation_error_msg = report_validation_error(category="mthds", validation_error=validation_error)
        msg = f"Could not load blueprints because of: {validation_error_msg}"
        error = ValidateBundleError(message=msg, pipe_validation_errors=pipe_validation_errors)
        error.__cause__ = validation_error
        return None, [], error

    # Phase 2: Load pipes into library (may raise, but pipes are created before validation)
    loaded_pipes: list[PipeAbstract] = []
    try:
        loaded_pipes = library_manager.load_from_blueprints(library_id=library_id, blueprints=[blueprint])
    except PipeFactoryError as factory_error:
        factory_error_data = categorize_pipe_factory_error(factory_error=factory_error)
        error = ValidateBundleError(
            message=f"Pipe factory error: {factory_error}",
            pipe_factory_errors=[factory_error_data],
        )
        error.__cause__ = factory_error
        # Pipes that were created before the error are in the library
        loaded_pipes = library.pipe_library.get_pipes()
        return blueprint, loaded_pipes, error
    except PipeValidationError as pipe_error:
        pipe_error_data = categorize_pipe_validation_with_libraries_error(pipe_error=pipe_error)
        error = ValidateBundleError(
            message=f"Pipe validation failed: {pipe_error}",
            pipe_validation_errors=[pipe_error_data],
        )
        error.__cause__ = pipe_error
        # Pipes were added to library before validate_library raised
        loaded_pipes = library.pipe_library.get_pipes()
        return blueprint, loaded_pipes, error
    except ValidationError as validation_error:
        pipe_validation_errors = categorize_pipe_validation_error(validation_error=validation_error)
        validation_error_msg = report_validation_error(category="mthds", validation_error=validation_error)
        msg = f"Could not load blueprints because of: {validation_error_msg}"
        error = ValidateBundleError(message=msg, pipe_validation_errors=pipe_validation_errors)
        error.__cause__ = validation_error
        loaded_pipes = library.pipe_library.get_pipes()
        return blueprint, loaded_pipes, error

    # Phase 3: Dry-run (optional failure)
    try:
        await dry_run_pipes(pipes=loaded_pipes, raise_on_failure=True)
    except (PipeRunError, DryRunError) as dry_run_error:
        error = ValidateBundleError(
            message=dry_run_error.message,
            dry_run_error_message=dry_run_error.message,
        )
        error.__cause__ = dry_run_error
        return blueprint, loaded_pipes, error

    # Full success — no errors
    return blueprint, loaded_pipes, None


async def _validate_fixed_text(
    fixed_text: str,
    library_dirs: Sequence[Path] | None,
) -> ValidateBundleError | None:
    """Re-validate after applying fixes. Returns error if validation fails, None on success."""
    try:
        await validate_bundle(mthds_contents=[fixed_text], library_dirs=library_dirs)
    except ValidateBundleError as exc:
        return exc
    return None


async def fix_bundle(
    mthds_file_path: Path,
    selected_rules: list[str] | None = None,
    ignored_rules: list[str] | None = None,
    prune: bool = False,
    library_dirs: Sequence[Path] | None = None,
) -> FixBundleResult:
    """Auto-fix deterministic issues in a .mthds bundle.

    Args:
        mthds_file_path: Path to the .mthds file.
        selected_rules: If set, only run these rules (mutually exclusive with ignored_rules).
        ignored_rules: If set, skip these rules (mutually exclusive with selected_rules).
        prune: If True, also run pruning rules.
        library_dirs: Optional library directories for dependency resolution.

    Returns:
        FixBundleResult with fix details and fixed text.
    """
    raw_text = mthds_file_path.read_text(encoding="utf-8")
    toml_doc: TOMLDocument = tomlkit.parse(raw_text)

    # Phase 1: Load and validate to get errors and pipes
    blueprint, loaded_pipes, validation_error = await _load_blueprint_and_pipes(raw_text, library_dirs)

    if blueprint is None:
        # Cannot even parse the blueprint — nothing we can fix at TOML level
        log.warning("Cannot parse bundle blueprint, no fixes can be applied")
        return FixBundleResult(
            bundle_path=str(mthds_file_path),
            fixed=False,
            fixes_applied=[],
            original_text=raw_text,
            fixed_text=raw_text,
        )

    # Phase 2: Resolve which rules to run
    rules = _resolve_rules(selected_rules, ignored_rules, prune)

    # Phase 3: Apply fix rules in order
    all_fixes: list[FixResult] = []
    for rule in rules:
        fixes = rule.apply(toml_doc, blueprint, validation_error, loaded_pipes)
        all_fixes.extend(fixes)

    if not all_fixes:
        return FixBundleResult(
            bundle_path=str(mthds_file_path),
            fixed=False,
            fixes_applied=[],
            original_text=raw_text,
            fixed_text=raw_text,
        )

    fixed_text = tomlkit.dumps(toml_doc)

    # Phase 4: Re-validate to confirm fixes worked
    remaining_error = await _validate_fixed_text(fixed_text, library_dirs)
    remaining_errors: list[dict[str, Any]] | None = None
    if remaining_error is not None:
        from pipelex.cli.agent_cli.commands.agent_output import extract_validation_errors  # noqa: PLC0415

        remaining_errors = extract_validation_errors(remaining_error)

    return FixBundleResult(
        bundle_path=str(mthds_file_path),
        fixed=True,
        fixes_applied=all_fixes,
        original_text=raw_text,
        fixed_text=fixed_text,
        remaining_errors=remaining_errors,
    )
