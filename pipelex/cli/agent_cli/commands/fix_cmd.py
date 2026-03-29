"""Agent CLI fix command - auto-fix deterministic issues in .mthds bundles."""

from __future__ import annotations

import asyncio
import difflib
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from pipelex.cli.agent_cli.commands.agent_cli_factory import make_pipelex_for_agent_cli
from pipelex.cli.agent_cli.commands.agent_output import agent_error, agent_success
from pipelex.core.interpreter.helpers import is_pipelex_file
from pipelex.pipelex import Pipelex
from pipelex.pipeline.fix_bundle import FixBundleResult, fix_bundle
from pipelex.types import StrEnum


class FixOutputFormat(StrEnum):
    TOML = "toml"
    JSON = "json"


def _print_diff(original: str, fixed: str, file_path: str) -> None:
    """Print a unified diff to stderr."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    sys.stderr.writelines(diff)


def _result_to_json(result: FixBundleResult) -> dict[str, Any]:
    """Convert a FixBundleResult to a JSON-serializable dict."""
    output: dict[str, Any] = {
        "success": True,
        "bundle_path": result.bundle_path,
        "fixed": result.fixed,
        "fixes_applied": [fix.model_dump(exclude_none=True) for fix in result.fixes_applied],
    }
    if result.remaining_errors:
        output["remaining_errors"] = result.remaining_errors
    return output


def _print_diagnostics_to_stderr(result: FixBundleResult) -> None:
    """Print JSON diagnostics summary to stderr."""
    from pipelex.tools.misc.json_utils import clean_json_dumps  # noqa: PLC0415

    diag: dict[str, Any] = {
        "fixed": result.fixed,
        "fixes_applied": [fix.model_dump(exclude_none=True) for fix in result.fixes_applied],
    }
    if result.remaining_errors:
        diag["remaining_errors"] = result.remaining_errors
    print(clean_json_dumps(diag, indent=2), file=sys.stderr)


def fix_cmd(
    ctx: typer.Context,
    paths: Annotated[
        list[str],
        typer.Argument(help="Path(s) to .mthds bundle file(s) to fix"),
    ],
    output_format: Annotated[
        FixOutputFormat,
        typer.Option("--format", "-f", help="Output format: toml (default, fixed content to stdout) or json (structured result)"),
    ] = FixOutputFormat.TOML,
    in_place: Annotated[
        bool,
        typer.Option("--in-place", "-w", help="Write fixed content back to the file instead of stdout"),
    ] = False,
    select: Annotated[
        str | None,
        typer.Option("--select", help="Comma-separated list of fix rules to apply (mutually exclusive with --ignore)"),
    ] = None,
    ignore: Annotated[
        str | None,
        typer.Option("--ignore", help="Comma-separated list of fix rules to skip (mutually exclusive with --select)"),
    ] = None,
    prune: Annotated[
        bool,
        typer.Option("--prune", help="Also run pruning rules (prune-unreachable, prune-unused-concepts)"),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress diff and diagnostics on stderr"),
    ] = False,
    library_dir: Annotated[
        list[str] | None,
        typer.Option("--library-dir", "-L", help="Directory to search for pipe definitions (.mthds files)"),
    ] = None,
) -> None:
    """Auto-fix deterministic issues in .mthds bundles.

    Default: outputs fixed TOML content to stdout (for agents to capture).
    Use --format json for structured JSON output (for VS Code extension / LSP).
    Use --in-place to write fixes back to the file.

    Diff and diagnostics go to stderr unless --quiet is set.

    Examples:
        pipelex-agent fix my_bundle.mthds                            # fixed TOML to stdout
        pipelex-agent fix my_bundle.mthds --format json              # JSON result to stdout
        pipelex-agent fix my_bundle.mthds --in-place                 # write fixes to file, diagnostics to stderr
        pipelex-agent fix my_bundle.mthds --select strip-namespace,sync-controller-inputs
        pipelex-agent fix my_bundle.mthds --ignore match-sequence-output
        pipelex-agent fix my_bundle.mthds --prune
    """
    if not paths:
        agent_error("At least one .mthds file path is required", "ArgumentError")

    match output_format:
        case FixOutputFormat.TOML:
            if len(paths) > 1:
                agent_error("TOML output (default) supports only a single file. Use --format json for multiple files.", "ArgumentError")
        case FixOutputFormat.JSON:
            pass

    # Validate all paths are .mthds files
    for file_path in paths:
        target = Path(file_path)
        if not target.is_file():
            agent_error(f"File not found: '{file_path}'", "FileNotFoundError")
        if not is_pipelex_file(target):
            agent_error(f"Not a .mthds file: '{file_path}'", "ArgumentError")

    selected_rules = select.split(",") if select else None
    ignored_rules = ignore.split(",") if ignore else None
    library_dirs = [Path(lib_dir) for lib_dir in library_dir] if library_dir else None

    make_pipelex_for_agent_cli(library_dirs=library_dirs, log_level=ctx.obj["log_level"], needs_inference=False, needs_model_specs=True)

    try:
        if len(paths) == 1:
            result = asyncio.run(
                fix_bundle(
                    mthds_file_path=Path(paths[0]),
                    selected_rules=selected_rules,
                    ignored_rules=ignored_rules,
                    prune=prune,
                    library_dirs=library_dirs,
                )
            )

            if result.fixed and not quiet:
                _print_diff(result.original_text, result.fixed_text, paths[0])

            if in_place and result.fixed:
                Path(paths[0]).write_text(result.fixed_text, encoding="utf-8")

            match output_format:
                case FixOutputFormat.TOML:
                    # Output fixed content (or original if no fixes) to stdout
                    print(result.fixed_text, end="")
                    if not quiet:
                        _print_diagnostics_to_stderr(result)
                case FixOutputFormat.JSON:
                    agent_success(_result_to_json(result))

        else:
            # Multiple files — only JSON format
            bundle_results: list[dict[str, Any]] = []
            for file_path in paths:
                result = asyncio.run(
                    fix_bundle(
                        mthds_file_path=Path(file_path),
                        selected_rules=selected_rules,
                        ignored_rules=ignored_rules,
                        prune=prune,
                        library_dirs=library_dirs,
                    )
                )

                if result.fixed:
                    if not quiet:
                        _print_diff(result.original_text, result.fixed_text, file_path)
                    if in_place:
                        Path(file_path).write_text(result.fixed_text, encoding="utf-8")

                bundle_results.append(_result_to_json(result))

            agent_success(
                {
                    "success": True,
                    "bundles": bundle_results,
                }
            )

    except ValueError as exc:
        agent_error(str(exc), "ArgumentError")

    except typer.Exit:
        raise

    except Exception as exc:
        agent_error(str(exc), type(exc).__name__, cause=exc)

    finally:
        Pipelex.teardown_if_needed()
