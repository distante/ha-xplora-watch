"""Tests for the coordinator's in-flight request coalescer (`_inflight_updates`).

Concurrent `async_update_xplora_data` calls that share a request signature (`targets` +
`force_functions`) must run a SINGLE network fan-out and hand its result to every caller -- so two
cards rendering at once, or a button press racing a service call / scheduled poll for the same
watch(es), don't each hit the API. Calls with different signatures stay independent, and the
local-only `new_data` injection path is never coalesced.
"""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


async def _wait_until(predicate, *, iterations: int = 1000) -> None:
    """Yield to the loop until ``predicate()`` is true (bounded, so a bug fails fast)."""
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition not met in time")  # pragma: no cover - only if coalescing breaks


async def test_concurrent_same_signature_runs_one_fetch(
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Two concurrent identical calls share ONE underlying fetch and both get its result."""
    coord = coordinator_with_data
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"sentinel": True}

    coord._fetch_and_store_xplora_data = fake_fetch  # type: ignore[method-assign]

    # First call: register and start the in-flight fetch, then a second identical call joins it.
    first = asyncio.create_task(coord.async_update_xplora_data([DEFAULT_WUID]))
    await started.wait()
    second = asyncio.create_task(coord.async_update_xplora_data([DEFAULT_WUID]))
    await asyncio.sleep(0)  # let the second call reach the coalesce branch

    release.set()
    r1, r2 = await asyncio.gather(first, second)

    assert calls == 1  # the second call was coalesced onto the first
    assert r1 == {"sentinel": True}
    assert r2 == {"sentinel": True}
    # The in-flight entry is cleaned up once the request settles.
    assert coord._inflight_updates == {}


async def test_different_force_functions_not_coalesced(
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """A `force_functions` refresh is a distinct signature -- it never merges with a plain one."""
    coord = coordinator_with_data
    seen: list[bool] = []
    release = asyncio.Event()

    async def fake_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        seen.append(force_functions)
        await release.wait()
        return {"force_functions": force_functions}

    coord._fetch_and_store_xplora_data = fake_fetch  # type: ignore[method-assign]

    plain = asyncio.create_task(coord.async_update_xplora_data([DEFAULT_WUID], force_functions=False))
    forced = asyncio.create_task(coord.async_update_xplora_data([DEFAULT_WUID], force_functions=True))
    await _wait_until(lambda: len(seen) == 2)  # both fetches genuinely started

    release.set()
    await asyncio.gather(plain, forced)

    assert sorted(seen) == [False, True]  # two independent fetches ran
    assert coord._inflight_updates == {}


async def test_different_targets_not_coalesced(
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Calls for different watch sets are independent requests (not coalesced)."""
    coord = coordinator_with_data
    seen: list[tuple[str, ...] | None] = []
    release = asyncio.Event()

    async def fake_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        seen.append(tuple(targets) if targets else None)
        await release.wait()
        return {}

    coord._fetch_and_store_xplora_data = fake_fetch  # type: ignore[method-assign]

    a = asyncio.create_task(coord.async_update_xplora_data([DEFAULT_WUID]))
    b = asyncio.create_task(coord.async_update_xplora_data(["other-watch-id"]))
    await _wait_until(lambda: len(seen) == 2)

    release.set()
    await asyncio.gather(a, b)

    assert (DEFAULT_WUID,) in seen
    assert ("other-watch-id",) in seen
    assert coord._inflight_updates == {}


async def test_new_data_path_bypasses_coalescer(
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """The local-only `new_data` injection never touches the network fetch or the in-flight map."""
    coord = coordinator_with_data
    called = False

    async def fake_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:  # pragma: no cover
        nonlocal called
        called = True
        return {}

    coord._fetch_and_store_xplora_data = fake_fetch  # type: ignore[method-assign]

    await coord.async_update_xplora_data(new_data={DEFAULT_WUID: {"injected": True}})

    assert called is False
    assert coord._inflight_updates == {}
    assert coord.data[DEFAULT_WUID]["injected"] is True
