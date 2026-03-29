"""Base classes and models for bundle fix rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel

from pipelex.types import StrEnum

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

    from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
    from pipelex.core.pipes.pipe_abstract import PipeAbstract
    from pipelex.pipeline.validate_bundle import ValidateBundleError


class FixRuleCategory(StrEnum):
    CORRECTION = "correction"
    PRUNING = "pruning"


class FixResult(BaseModel):
    """Result of applying a single fix."""

    fix_code: str
    pipe_code: str | None = None
    concept_code: str | None = None
    message: str
    line: int | None = None


class FixRule(ABC):
    """Abstract base class for bundle fix rules.

    Each rule operates on a tomlkit TOMLDocument (mutable, preserves formatting),
    optionally using the parsed blueprint, validation errors, and instantiated pipes
    to determine what to fix.
    """

    code: str
    category: FixRuleCategory
    description: str

    @abstractmethod
    def apply(
        self,
        toml_doc: TOMLDocument,
        blueprint: PipelexBundleBlueprint,
        validation_error: ValidateBundleError | None,
        pipes: list[PipeAbstract],
    ) -> list[FixResult]:
        """Mutate toml_doc in place. Return list of fixes applied."""
