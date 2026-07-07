"""Regression guards: no deprecated TrackerEntity property overrides (HA 2027.7 removal).

HA 2026.7 warns -- and 2027.7 breaks -- when a device-tracker entity class overrides the
deprecated ``battery_level`` (BaseTrackerEntity) or ``location_name`` (TrackerEntity) property.
The warning fires from ``__init_subclass__`` at class-definition (import) time, so a per-test
``caplog`` can never capture it once the module sits in ``sys.modules``; assert on the exact
condition HA's check reads instead: the property being present in the class ``__dict__``.
"""

from __future__ import annotations

import pytest

from custom_components.xplora_watch.device_tracker import XploraDeviceTracker, XploraSafezoneTracker


@pytest.mark.parametrize("tracker_class", [XploraDeviceTracker, XploraSafezoneTracker])
def test_tracker_class_does_not_override_deprecated_battery_level(tracker_class: type) -> None:
    assert "battery_level" not in tracker_class.__dict__


@pytest.mark.parametrize("tracker_class", [XploraDeviceTracker, XploraSafezoneTracker])
def test_tracker_class_does_not_override_deprecated_location_name(tracker_class: type) -> None:
    assert "location_name" not in tracker_class.__dict__
