"""Tests that two concurrent fan-outs never interleave over the shared per-watch state.

The in-flight coalescer (`test_inflight_coalescing.py`) merges concurrent calls that share a
request *signature*. Calls with different signatures deliberately stay independent -- a scheduled
poll (`targets=None`) racing a `see` for one watch, or two cards rendering different watches. Those
used to run as overlapping tasks, and the fetch helpers keep their per-watch intermediates on the
coordinator object (`self.device`, `self.lat/lng`, `self.imei`, `self.battery`, `self.unread_msg`,
`self.alarm`, `self.silent`, `self._step_day`, `self._xcoin`) while awaiting the network between
writing and reading them. The second fan-out then overwrote the first watch's values before
`get_data()` had copied them out, and a watch showed another watch's steps, XCoins, silent times,
unread count and IMEI.

Observed in the wild: two watches on one account, both reporting an identical step count, with the
recorder showing one watch's value flipping to the other's and back three times inside one second.
"""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator

WUID_A = "watch-id-aaa"
WUID_B = "watch-id-bbb"
STEPS = {WUID_A: 1302, WUID_B: 2581}
IMEIS = {WUID_A: "111111111111111", WUID_B: "222222222222222"}


async def test_concurrent_different_signature_fetches_do_not_mix_watch_values(
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """A second fan-out must not overwrite the first one's per-watch values mid-flight."""
    coord = coordinator_with_data
    a_inside = asyncio.Event()
    release_a = asyncio.Event()
    observed: dict[str, tuple[int | None, str | None]] = {}

    async def fake_init_and_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        """Mimic the real loop's shape: stash on `self`, await the network, read back."""
        wuid = (targets or [WUID_A])[0]
        coord._step_day = STEPS[wuid]
        coord.imei = IMEIS[wuid]

        if wuid == WUID_A:
            a_inside.set()
            await release_a.wait()
        else:
            await asyncio.sleep(0)

        # What `get_data()` does: build the watch's entry from the values parked on `self`.
        observed[wuid] = (coord._step_day, coord.imei)
        return {wuid: {"step_day": coord._step_day, "imei": coord.imei}}

    coord._init_and_fetch = fake_init_and_fetch  # type: ignore[method-assign]

    # A different `targets` is a different signature, so this is NOT coalesced -- the two calls
    # are exactly the independent fan-outs that used to interleave.
    task_a = asyncio.create_task(coord.async_update_xplora_data([WUID_A]))
    await a_inside.wait()
    task_b = asyncio.create_task(coord.async_update_xplora_data([WUID_B]))

    # Give B every chance to run while A is parked. Serialized, it cannot start yet; unserialized,
    # it runs here and clobbers A's values.
    for _ in range(10):
        await asyncio.sleep(0)

    release_a.set()
    await asyncio.gather(task_a, task_b)

    assert observed[WUID_A] == (STEPS[WUID_A], IMEIS[WUID_A]), (
        f"watch A read back {observed[WUID_A]}, i.e. watch B's values -- the fan-outs interleaved"
    )
    assert observed[WUID_B] == (STEPS[WUID_B], IMEIS[WUID_B])
    assert coord.data[WUID_A]["step_day"] == STEPS[WUID_A]
    assert coord.data[WUID_B]["step_day"] == STEPS[WUID_B]


async def test_second_fetch_waits_for_the_first(coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """Explicitly: at most one fan-out body runs at a time, whatever the signatures are."""
    coord = coordinator_with_data
    concurrent = 0
    peak = 0
    first_inside = asyncio.Event()
    release = asyncio.Event()

    async def fake_init_and_fetch(targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        try:
            if not first_inside.is_set():
                first_inside.set()
                await release.wait()
            else:
                await asyncio.sleep(0)
            return {}
        finally:
            concurrent -= 1

    coord._init_and_fetch = fake_init_and_fetch  # type: ignore[method-assign]

    task_a = asyncio.create_task(coord.async_update_xplora_data([WUID_A]))
    await first_inside.wait()
    task_b = asyncio.create_task(coord.async_update_xplora_data([WUID_B], force_functions=True))
    for _ in range(10):
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(task_a, task_b)

    assert peak == 1, f"{peak} fan-outs ran at once; they share per-watch state and must not overlap"
