"""The server-proven control-action query oracle, for equivalence checks (ADR 0010).

``gql_mutations.WATCH_M`` retains the battle-tested control-action queries the real Xplora server
has accepted for years. This module parses them into the parts the server validates on every real
call -- the selected field, the variable declarations (name -> GraphQL type), and the
argument-to-variable wiring -- so tests can assert every *generated* :class:`WatchCommand` query
still matches its proven oracle, and so the shared transport mock can reject any outgoing command
query that diverges.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from custom_components.xplora_watch.pyxplora_api import gql_mutations as gm
from custom_components.xplora_watch.pyxplora_api.watch_commands import WatchCommand


class ParsedCommandQuery(NamedTuple):
    """Everything the real server validates on the way in that a generation typo could get wrong.

    ``field`` -- the selected GraphQL field. ``var_types`` -- variable declarations (name -> full
    GraphQL type, nullability and list wrappers included). ``arg_bindings`` -- the argument-to-
    variable wiring (GraphQL *argument* name -> the ``$variable`` passed to it). The generator
    assumes the argument name equals the variable name (``field(x: $x)``); comparing the wiring
    catches a future command where the server's argument name differs from ours.

    The response sub-selection is deliberately excluded: a generated create selects ``{ id }`` while
    its oracle selects a full response fragment, so the two cannot be compared for equality here. The
    create sub-selection is pinned separately by :func:`create_selection` (the last hand-written
    literal in the query path -- see ``test_watch_commands``).
    """

    field: str
    var_types: dict[str, str]
    arg_bindings: dict[str, str]


def parse_command_query(query: str) -> ParsedCommandQuery:
    """Extract the server-contract-bearing shape of a mutation string.

    Variable declarations (``$name: Type``) live in the operation header before the body's opening
    ``{``; the field call and its ``arg: $var`` bindings live in the body. The type pattern captures
    the *whole* type token including list wrappers and ``!`` (so ``[String!]!`` is compared, not
    silently dropped) and digits (so ``FooV2`` is not truncated to ``FooV``). If a declared variable's
    type does not parse at all, we raise rather than let a dropped variable compare equal -- the exact
    blind spot ADR 0010's equivalence guarantee must not have.
    """
    header, _, body = query.partition("{")
    var_types = dict(re.findall(r"\$(\w+)\s*:\s*(\[?[A-Za-z_]\w*!?\]?!?)", header))
    declared = set(re.findall(r"\$(\w+)\s*:", header))
    if declared != set(var_types):
        raise ValueError(f"unparsed variable type(s) in query header: {sorted(declared - set(var_types))}")
    match = re.search(r"(\w+)", body)
    field = match.group(1) if match else ""
    arg_bindings = dict(re.findall(r"(\w+)\s*:\s*\$(\w+)", body))
    return ParsedCommandQuery(field, var_types, arg_bindings)


def create_selection(query: str) -> list[str]:
    """The response sub-selection of the top-level field, e.g. ``["id"]`` for a create.

    Only the ``add*`` creates carry a sub-selection (every other command selects the bare scalar and
    returns ``[]``). Lets a test assert a generated create still selects exactly ``id`` -- a typo in
    that hand-written literal (``Id`` / ``ids``) is rejected by the real server and would otherwise
    reproduce the very bogus-``watch_offline`` symptom ADR 0010 closes, since neither the oracle
    equivalence (the oracle selects a full fragment) nor the payload-driven mock can see it.
    """
    # Scan the operation body only, so the operation header's own `) {` (the variable-list close) is
    # not mistaken for a field sub-selection. Within the body, the field's `)` closes its argument
    # list; a following `{ ... }` is the response sub-selection.
    _, _, body = query.partition("{")
    match = re.search(r"\)\s*\{([^}]*)\}", body)
    return re.findall(r"\w+", match.group(1)) if match else []


_COMMAND_FIELDS = {command.field for command in WatchCommand}

#: Oracle keyed by selected field, restricted to the in-scope control-action commands. The other
#: WATCH_M mutations (chat, contact, ...) are not generated and so are not enforced here.
ORACLE_BY_FIELD: dict[str, ParsedCommandQuery] = {
    parse_command_query(query).field: parse_command_query(query)
    for query in gm.WATCH_M.values()
    if parse_command_query(query).field in _COMMAND_FIELDS
}
