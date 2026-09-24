"""Message handlers and commands for the patched vacuums.

Two library behaviours are corrected here, both seen from Home Assistant on a
T90 (firmware 1.103.0):

* ``OnWorkState`` publishes no robot state for ``robotState idle`` with
  ``stationState idle``. A robot stopped away from its dock reports exactly
  that, so the vacuum entity stayed ``cleaning`` until the robot docked.
* ``SetAutoEmpty`` sends only the fields it is given. The firmware answers
  ``{"frequency": "smart"}`` alone with ``code 20004 "get act fail"`` and
  changes nothing; the app always sends ``enable``, ``frequency`` and
  ``intensity`` together, and the library has no ``intensity`` at all.

``MESSAGES`` is global, so the handlers registered by ``apply()`` are reached
for every JSON device on the account, and by core's ``ecovacs`` too when it
runs in the same process. They behave exactly like the library's for any bus
not registered here: registration is the marker, as ``state_precedence`` is
for the mowers.

Keyed by ``EventBus`` for the reason given in ``state_precedence``: handlers
are classmethods that receive nothing else, and a weak key dies with its
device.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet

from deebot_client.commands.json.auto_empty import GetAutoEmpty
from deebot_client.commands.json.common import ExecuteCommand
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.events import StateEvent
from deebot_client.events.auto_empty import AutoEmptyEvent, Frequency
from deebot_client.message import HandlingResult, HandlingState
from deebot_client.messages.json.auto_empty import OnAutoEmpty
from deebot_client.messages.json.work_state import OnWorkState
from deebot_client.models import State
from deebot_client.util import get_enum

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator
    from deebot_client.event_bus import EventBus
    from deebot_client.models import ApiDeviceInfo

_LOGGER = logging.getLogger(__name__)

_VACUUM_BUSES: WeakSet[EventBus] = WeakSet()
_AUTO_EMPTY_INTENSITY: WeakKeyDictionary[EventBus, int] = WeakKeyDictionary()


def register_vacuum_bus(event_bus: EventBus) -> None:
    """Mark *event_bus* as a patched vacuum's.

    Before ``Device.initialize()``, for the reason ``register_mower_bus`` is:
    that call starts the MQTT subscription, and a push arriving before the
    registration would be handled as an unpatched device's.
    """
    _VACUUM_BUSES.add(event_bus)


def is_vacuum_bus(event_bus: EventBus) -> bool:
    """Whether *event_bus* belongs to a patched vacuum."""
    return event_bus in _VACUUM_BUSES


def auto_empty_intensity_for(event_bus: EventBus) -> int | None:
    """The dust collection power last reported on *event_bus*, if any."""
    return _AUTO_EMPTY_INTENSITY.get(event_bus)


def reset() -> None:
    """Forget every registration and record. Tests only."""
    _VACUUM_BUSES.clear()
    _AUTO_EMPTY_INTENSITY.clear()


class OnWorkStateVacuum(OnWorkState):
    """``onWorkState`` that reports a robot idle away from its dock.

    The library's table maps ``(idle, idle)`` to no robot state, because on
    the dock the charge state is the one that says ``DOCKED``. Away from the
    dock nothing else ever arrives, so ``IDLE`` is published here. It cannot
    undo a docking: ``EventBus.notify`` turns an ``IDLE`` that follows
    ``DOCKED`` into ``DOCKED`` by itself.

    A paused station action on the dock is ``(idle, idle)`` with ``paused 1``;
    the library already publishes ``PAUSED`` for it (gap S2 in the T90
    research notes) and this handler leaves that alone.
    """

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        """Handle message->body->data and notify the correct event subscribers."""
        result = super()._handle_body_data_dict(event_bus, data)
        if (
            is_vacuum_bus(event_bus)
            and data.get("paused") != 1
            and (data.get("robotState") or {}).get("state") == "idle"
            and (data.get("stationState") or {}).get("state") == "idle"
        ):
            event_bus.notify(StateEvent(State.IDLE))
        return result


class GetWorkStateVacuum(OnWorkStateVacuum, GetWorkState):
    """``getWorkState`` answered by ``OnWorkStateVacuum``'s rule."""

    # Spelled out: OnWorkStateVacuum comes first in the MRO, and a NAME added
    # to it later would silently rename this command to "onWorkState".
    NAME = "getWorkState"


class OnAutoEmptyVacuum(OnAutoEmpty):
    """``onAutoEmpty`` that also remembers the dust collection power.

    ``AutoEmptyEvent`` has no field for ``intensity``, and the setter below
    cannot send a complete request without it.
    """

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        """Handle message->body->data and notify the correct event subscribers."""
        # Recorded before the library notifies, so a set that follows the
        # event can already read it.
        intensity = data.get("intensity")
        if is_vacuum_bus(event_bus) and isinstance(intensity, int):
            _AUTO_EMPTY_INTENSITY[event_bus] = intensity
        return super()._handle_body_data_dict(event_bus, data)


class GetAutoEmptyVacuum(OnAutoEmptyVacuum, GetAutoEmpty):
    """``getAutoEmpty`` answered by ``OnAutoEmptyVacuum``."""

    NAME = "getAutoEmpty"


class SetAutoEmptyVacuum(ExecuteCommand):
    """``setAutoEmpty`` with all three fields, as the app sends it.

    Takes the library's arguments, so ``CapabilitySetTypes.set(None, option)``
    keeps working. Whatever the caller leaves out is filled in at execution
    time from what the robot last reported: ``enable`` and ``frequency`` from
    the bus's last ``AutoEmptyEvent``, ``intensity`` from ``OnAutoEmptyVacuum``.
    If any of the three is still unknown the request is not sent at all: a
    guessed value would change a setting the user did not touch.
    """

    NAME = "setAutoEmpty"

    def __init__(
        self,
        enable: bool | None = None,
        frequency: Frequency | str | None = None,
        intensity: int | None = None,
    ) -> None:
        if frequency is not None and not isinstance(frequency, Frequency):
            frequency = get_enum(Frequency, frequency)
        self._enable = enable
        self._frequency = frequency
        self._intensity = intensity
        super().__init__(self._params(enable, frequency, intensity))

    @staticmethod
    def _params(
        enable: bool | None, frequency: Frequency | None, intensity: int | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if enable is not None:
            params["enable"] = int(enable)
        if frequency is not None:
            params["frequency"] = frequency.value
        if intensity is not None:
            params["intensity"] = intensity
        return params

    async def _execute(
        self,
        authenticator: Authenticator,
        device_info: ApiDeviceInfo,
        event_bus: EventBus,
    ) -> tuple[HandlingResult, dict[str, Any]]:
        """Complete the request from the last report, then send it."""
        last = event_bus.get_last_event(AutoEmptyEvent)
        enable = self._enable
        if enable is None and last is not None:
            enable = last.enabled
        frequency = self._frequency
        if frequency is None and last is not None:
            frequency = last.frequency
        intensity = self._intensity
        if intensity is None:
            intensity = auto_empty_intensity_for(event_bus)

        if enable is None or frequency is None or intensity is None:
            _LOGGER.warning(
                "Not sending setAutoEmpty to %s: the current auto-empty "
                "settings have not been reported yet (enable=%s, "
                "frequency=%s, intensity=%s)",
                device_info["class"],
                enable,
                frequency,
                intensity,
            )
            return HandlingResult(HandlingState.FAILED), {}

        self._args = self._params(enable, frequency, intensity)
        return await super()._execute(authenticator, device_info, event_bus)
