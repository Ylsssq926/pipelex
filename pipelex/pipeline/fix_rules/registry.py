"""Fix rule registry.

Rules are ordered by execution priority — earlier rules may unlock later ones.
"""

from pipelex.pipeline.fix_rules.base import FixRule, FixRuleCategory
from pipelex.pipeline.fix_rules.fix_list_notation import FixListNotationRule
from pipelex.pipeline.fix_rules.match_sequence_output import MatchSequenceOutputRule
from pipelex.pipeline.fix_rules.prune_unreachable import PruneUnreachableRule
from pipelex.pipeline.fix_rules.prune_unused_concepts import PruneUnusedConceptsRule
from pipelex.pipeline.fix_rules.strip_namespace import StripNamespaceRule
from pipelex.pipeline.fix_rules.strip_native_concept_redecl import StripNativeConceptRedeclRule
from pipelex.pipeline.fix_rules.sync_controller_inputs import SyncControllerInputsRule

# Ordered by execution priority
ALL_RULES: list[FixRule] = [
    StripNamespaceRule(),
    StripNativeConceptRedeclRule(),
    SyncControllerInputsRule(),
    MatchSequenceOutputRule(),
    FixListNotationRule(),
    PruneUnreachableRule(),
    PruneUnusedConceptsRule(),
]

DEFAULT_RULES: list[FixRule] = [rule for rule in ALL_RULES if rule.category == FixRuleCategory.CORRECTION]

PRUNING_RULES: list[FixRule] = [rule for rule in ALL_RULES if rule.category == FixRuleCategory.PRUNING]

ALL_RULE_CODES: set[str] = {rule.code for rule in ALL_RULES}


def get_rule_by_code(code: str) -> FixRule | None:
    """Look up a fix rule by its code string."""
    for rule in ALL_RULES:
        if rule.code == code:
            return rule
    return None
