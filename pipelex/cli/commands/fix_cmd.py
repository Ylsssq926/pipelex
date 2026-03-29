"""Regular CLI fix command - auto-fix deterministic issues in .mthds bundles."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Annotated

import typer

from pipelex.cli.cli_factory import make_pipelex_for_cli
from pipelex.cli.error_handlers import ErrorContext
from pipelex.core.interpreter.helpers import is_pipelex_file
from pipelex.pipelex import Pipelex
from pipelex.pipeline.fix_bundle import fix_bundle


def fix_cmd(
    path: Annotated[
        str,
        typer.Argument(help="Path to a .mthds bundle file to fix"),
    ],
    stdout: Annotated[
        bool,
        typer.Option("--stdout", help="Output fixed file content to stdout instead of writing in-place"),
    ] = False,
    prune: Annotated[
        bool,
        typer.Option("--prune", help="Also run pruning rules (prune-unreachable, prune-unused-concepts)"),
    ] = False,
    library_dir: Annotated[
        list[str] | None,
        typer.Option(
            "--library-dir",
            "-L",
            help="Directory to search for pipe definitions (.mthds files). Can be specified multiple times.",
        ),
    ] = None,
) -> None:
    """Auto-fix deterministic issues in a .mthds bundle.

    Applies fix rules to a bundle file and writes the fixed content in-place.
    Shows a unified diff when fixes are applied.

    Examples:
        pipelex fix my_bundle.mthds
        pipelex fix my_bundle.mthds --stdout
        pipelex fix my_bundle.mthds --prune
    """
    target = Path(path)
    if not target.is_file():
        typer.secho(f"File not found: '{path}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not is_pipelex_file(target):
        typer.secho(f"Not a .mthds file: '{path}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    library_dirs = [Path(lib_dir) for lib_dir in library_dir] if library_dir else None

    make_pipelex_for_cli(
        context=ErrorContext.VALIDATION,
        library_dirs=library_dirs,
        needs_inference=False,
        needs_model_specs=True,
    )

    try:
        result = asyncio.run(
            fix_bundle(
                mthds_file_path=target,
                prune=prune,
                library_dirs=library_dirs,
            )
        )

        if not result.fixed:
            typer.secho("No fixes needed.", fg=typer.colors.GREEN)
            return

        # Show diff
        diff_lines = list(
            difflib.unified_diff(
                result.original_text.splitlines(keepends=True),
                result.fixed_text.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        if diff_lines:
            for line in diff_lines:
                if line.startswith("+") and not line.startswith("+++"):
                    typer.secho(line, fg=typer.colors.GREEN, nl=False)
                elif line.startswith("-") and not line.startswith("---"):
                    typer.secho(line, fg=typer.colors.RED, nl=False)
                elif line.startswith("@@"):
                    typer.secho(line, fg=typer.colors.CYAN, nl=False)
                else:
                    typer.echo(line, nl=False)

        if stdout:
            print(result.fixed_text, end="")
        else:
            target.write_text(result.fixed_text, encoding="utf-8")
            typer.secho(
                f"\n{len(result.fixes_applied)} fix(es) applied to {path}",
                fg=typer.colors.GREEN,
            )

        if result.remaining_errors:
            typer.secho(
                f"Note: {len(result.remaining_errors)} validation error(s) remain after fixes.",
                fg=typer.colors.YELLOW,
            )

    except ValueError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    except Exception as exc:
        typer.secho(f"Failed to fix bundle: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    finally:
        Pipelex.teardown_if_needed()
