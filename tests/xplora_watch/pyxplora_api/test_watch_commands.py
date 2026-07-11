"""Unit tests for the WatchCommand mechanism (ADR 0010).

These cover the structural prevention of the response-field-typo class:
- the query text and operation name are *generated* from the server-owned GraphQL field, and
  match the battle-tested oracle queries in ``WATCH_M`` (field + argument types);
- ``GQLHandler.run_command`` reads the response positionally (the single value of ``data``),
  so no response-field string exists to mistype.

No network: the generation tests are pure, and the read-contract tests mock the handler's
authenticated chokepoint (``runAuthorizedGqlQuery_a``).
"""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import Error, XploraProtocolError
from custom_components.xplora_watch.pyxplora_api.gql_handler_async import GQLHandler
from custom_components.xplora_watch.pyxplora_api.watch_commands import WatchCommand

from ..fixtures.command_oracle import ORACLE_BY_FIELD, create_selection, parse_command_query


def _bare_handler() -> GQLHandler:
    """A GQLHandler constructed without any network/login (like the PyXploraApi unit tests)."""
    return GQLHandler("", "", "", "", "")


def _dummy_variables(command: WatchCommand) -> dict[str, object]:
    """A value of the right kind for every declared argument (the mock ignores values)."""
    return {name: 1 if gql_type.startswith("Int") else "x" for name, gql_type in command.args.items()}


# --------------------------------------------------------------------------- generation


@pytest.mark.parametrize("command", list(WatchCommand))
def test_generated_query_matches_oracle(command: WatchCommand) -> None:
    """Each generated query selects the same field with the same argument types AND the same
    argument-to-variable wiring as the proven oracle query. Order-independent (GraphQL ignores
    argument order); the response sub-selection is pinned separately (see below)."""
    parsed = parse_command_query(command.query)
    assert parsed.field == command.field
    assert command.field in ORACLE_BY_FIELD, f"no oracle query retained for field {command.field!r}"
    assert parsed == ORACLE_BY_FIELD[command.field]


@pytest.mark.parametrize("command", [c for c in WatchCommand if c.returns_id], ids=lambda c: c.name)
def test_create_command_selects_only_id(command: WatchCommand) -> None:
    """A create's response sub-selection is exactly ``id`` -- the last hand-written literal in the
    query path (``watch_commands`` builds ``{ id }`` by hand). The oracle equivalence cannot cover it
    (the oracle selects a full fragment) and the payload-driven mock ignores it, so a typo here (``Id``
    / ``ids``) would pass every other test yet be rejected by the real server, reintroducing the exact
    bogus-``watch_offline`` class ADR 0010 closes. Pinned explicitly."""
    assert create_selection(command.query) == ["id"]


@pytest.mark.parametrize("command", [c for c in WatchCommand if not c.returns_id], ids=lambda c: c.name)
def test_non_create_command_selects_bare_scalar(command: WatchCommand) -> None:
    """Every non-create command selects the bare scalar with no sub-selection; a stray ``{ ... }``
    would change what the server returns and break the single-value read contract."""
    assert create_selection(command.query) == []


def test_parser_captures_arg_wiring_and_list_types() -> None:
    """The oracle parser sees everything the server validates: the argument-to-variable wiring (not
    just the variable declarations) and a list-typed variable captured whole. Without this, a command
    whose server argument name differs from our variable name, or a list-typed argument, would diverge
    from the server contract while comparing equal to the oracle -- a blind spot ADR 0010 must not
    have. This bites: the pre-hardening parser dropped the list type and never captured the wiring."""
    parsed = parse_command_query("mutation Demo($ids: [String!]!, $flag: Boolean) {\n  demo(serverArg: $ids, flag: $flag)\n}")
    assert parsed.field == "demo"
    assert parsed.var_types == {"ids": "[String!]!", "flag": "Boolean"}
    assert parsed.arg_bindings == {"serverArg": "ids", "flag": "flag"}


def test_parser_fails_loud_on_unparsed_variable_type() -> None:
    """A declared variable whose type does not parse must raise, not silently drop -- a dropped
    variable would let a real divergence compare equal to the oracle."""
    with pytest.raises(ValueError):
        parse_command_query("mutation X($a: ) {\n  x(a: $a)\n}")


@pytest.mark.parametrize("command", list(WatchCommand))
def test_operation_name_is_typo_free(command: WatchCommand) -> None:
    """The operation name is derived from the field, so the historical ``Slient`` typo cannot
    recur, and it is a valid capitalised identifier the transport mock / server can resolve."""
    assert "Slient" not in command.operation_name
    assert command.operation_name[0].isupper()
    # Load-bearing: pins the exact derivation. A regression to e.g. `.capitalize()` (which lowercases
    # the tail: shutDown -> Shutdown) or `.upper()` would fail here; the old `startswith(field[:4])`
    # check could never fail since a string always starts with its own prefix.
    assert command.operation_name == command.field[0].upper() + command.field[1:]


# --------------------------------------------------------------------------- read contract


async def test_run_command_returns_single_value() -> None:
    """A single-key ``data`` yields that key's value verbatim (the bool, or the create's object)."""
    handler = _bare_handler()

    async def _fake(query, variables=None, operation_name=None):  # noqa: ANN001, ANN202
        return {"data": {"setEnableSilentTime": True}}

    handler.runAuthorizedGqlQuery_a = _fake  # type: ignore[method-assign]
    assert await handler.run_command(WatchCommand.SET_ENABLE_SILENT_TIME, {"silentId": "s1"}) is True


async def test_run_command_returns_create_object() -> None:
    handler = _bare_handler()

    async def _fake(query, variables=None, operation_name=None):  # noqa: ANN001, ANN202
        return {"data": {"addAlarm": {"id": "a1"}}}

    handler.runAuthorizedGqlQuery_a = _fake  # type: ignore[method-assign]
    assert await handler.run_command(WatchCommand.ADD_ALARM, {"uid": "w1"}) == {"id": "a1"}


@pytest.mark.parametrize("envelope", [{}, {"data": {}}, {"data": None}])
async def test_run_command_empty_data_is_non_success(envelope: dict) -> None:
    """Empty/null ``data`` returns None -> falsy -> refused -> watch_offline (unchanged)."""
    handler = _bare_handler()

    async def _fake(query, variables=None, operation_name=None):  # noqa: ANN001, ANN202
        return envelope

    handler.runAuthorizedGqlQuery_a = _fake  # type: ignore[method-assign]
    assert await handler.run_command(WatchCommand.REBOOT, {"uid": "w1"}) is None


async def test_run_command_multi_key_raises_protocol_error() -> None:
    """The impossible >1-key shape (schema drift / code bug) raises loudly rather than defaulting."""
    handler = _bare_handler()

    async def _fake(query, variables=None, operation_name=None):  # noqa: ANN001, ANN202
        return {"data": {"reboot": True, "shutDown": True}}

    handler.runAuthorizedGqlQuery_a = _fake  # type: ignore[method-assign]
    with pytest.raises(XploraProtocolError):
        await handler.run_command(WatchCommand.REBOOT, {"uid": "w1"})


def test_protocol_error_does_not_subclass_error() -> None:
    """It must escape the package's ``except Error`` retry loops so it is never mapped to a
    silent refusal / watch_offline (mirrors AuthError / RateLimitError)."""
    assert not issubclass(XploraProtocolError, Error)
    assert issubclass(XploraProtocolError, Exception)


# --------------------------------------------------------------------- end-to-end (all 9 commands)


@pytest.mark.parametrize("command", list(WatchCommand), ids=lambda c: c.name)
async def test_command_round_trips_through_transport(
    command: WatchCommand,
    coordinator: XploraDataUpdateCoordinator,
    mock_graphql: aioresponses,
) -> None:
    """Every command drives run_command -> real handler -> the hardened transport mock.

    Proves all nine commands round-trip end to end (not just the four previously wired), and the
    mock's oracle assertion enforces query equivalence on the way out. A create returns its
    ``{ id }`` object; every other command returns the server's ``Boolean`` (default fixture: True).
    """
    handler = coordinator.controller._gql_handler
    result = await handler.run_command(command, _dummy_variables(command))

    if command.returns_id:
        assert isinstance(result, dict) and result.get("id")
    else:
        assert result is True
