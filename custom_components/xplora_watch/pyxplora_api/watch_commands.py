"""Watch control-action commands, generated from the server-owned GraphQL field (ADR 0010).

A control action's identity used to be spread across three separately hand-written strings that
all had to agree -- the operations-registry key, the GraphQL operation name, and the response
field read back. Only the response field has an external source of truth (Xplora's schema); a
typo in it silently returned ``False`` and surfaced as a bogus ``watch_offline``.

Here each control action is a single :class:`WatchCommand` keyed on that server field. The query
text and operation name are *generated* from the field, and the response is read positionally by
:meth:`GQLHandler.run_command` -- so no response-field string exists to mistype, and the operation
name cannot drift from the query. "Command" is only the code-level name for this mechanism; the
domain / user-facing term for a watch-mutating action stays **control action** (ADR 0001).

The argument spec (variable name -> GraphQL type) and ``returns_id`` flag are taken verbatim from
the battle-tested queries retained as an oracle in ``gql_mutations.WATCH_M``; a parametrised test
asserts each generated query still matches its oracle (selected field + argument types).
"""

from __future__ import annotations

from enum import Enum

# Control-action operation names whose wire spelling the field-derived PascalCase can't reproduce
# -- kept here as verbatim overrides so requests stay byte-identical for traffic fidelity
# (ref:XW-013; ADR 0010, ADR 0011). A GraphQL operation name is client-chosen and, for a
# single-operation document, the server dispatches on the selected field not the label, so an
# override has no functional effect; it is preserved purely for traffic fidelity. Keyed by the
# server field.
_OPERATION_NAME_OVERRIDES: dict[str, str] = {"setEnableSilentTime": "SetEnableSlientTime"}


class WatchCommand(Enum):
    """A watch control-action mutation, identified by its server GraphQL field.

    Value tuple: ``(field, args, returns_id)`` where ``args`` maps a GraphQL variable name to its
    type (e.g. ``"String!"``) and ``returns_id`` marks the ``add*`` creates whose response is an
    object the callers read ``{ id }`` from (every other command returns a scalar ``Boolean``).
    """

    REBOOT = ("reboot", {"uid": "String!"}, False)
    SHUTDOWN = ("shutDown", {"uid": "String!"}, False)
    SET_ENABLE_SILENT_TIME = ("setEnableSilentTime", {"silentId": "String!", "status": "NormalStatus!"}, False)
    MODIFY_SILENT_TIME = (
        "modifySilentTime",
        {"silentId": "String!", "start": "Int", "end": "Int", "weekRepeat": "String", "description": "String", "extra": "JSON"},
        False,
    )
    REMOVE_SILENT_TIME = ("removeSilentTime", {"silentId": "String!"}, False)
    ADD_SILENT_TIME = (
        "addSilentTime",
        {"uid": "String!", "start": "Int!", "end": "Int!", "weekRepeat": "String!", "description": "String", "extra": "JSON"},
        True,
    )
    MODIFY_ALARM = (
        "modifyAlarm",
        {
            "alarmId": "String!",
            "name": "String",
            "occurMin": "Int",
            "start": "Int",
            "end": "Int",
            "weekRepeat": "String",
            "description": "String",
            "status": "NormalStatus",
            "extra": "JSON",
            "timeZone": "String",
        },
        False,
    )
    REMOVE_ALARM = ("removeAlarm", {"alarmId": "String!"}, False)
    ADD_ALARM = (
        "addAlarm",
        {
            "uid": "String!",
            "name": "String",
            "occurMin": "Int!",
            "start": "Int!",
            "end": "Int",
            "weekRepeat": "String!",
            "description": "String",
            "extra": "JSON",
            "timeZone": "String",
        },
        True,
    )

    def __init__(self, field: str, args: dict[str, str], returns_id: bool) -> None:
        self.field = field
        self.args = args
        self.returns_id = returns_id

    @property
    def operation_name(self) -> str:
        """The GraphQL operation name: the field with its first letter upper-cased, unless a
        non-derivable wire label is defined for it (see ``_OPERATION_NAME_OVERRIDES``).

        For all but the overridden ops this is a pure function of the field, so it can't carry a
        typo the field does not; the one override reproduces a misspelling kept verbatim for wire
        fidelity (ref:XW-013). Either way it is a valid named operation the transport mock / real
        server can resolve."""
        override = _OPERATION_NAME_OVERRIDES.get(self.field)
        return override if override is not None else self.field[0].upper() + self.field[1:]

    @property
    def query(self) -> str:
        """The mutation text, generated from the field and argument spec.

        Declares every variable in the spec and passes each to the field (GraphQL ignores
        argument order). ``add*`` creates select ``{ id }`` -- all their callers read; every other
        command selects the bare scalar."""
        declarations = ", ".join(f"${name}: {gql_type}" for name, gql_type in self.args.items())
        arguments = ", ".join(f"{name}: ${name}" for name in self.args)
        selection = " {\n    id\n  }" if self.returns_id else ""
        return f"mutation {self.operation_name}({declarations}) {{\n  {self.field}({arguments}){selection}\n}}"
