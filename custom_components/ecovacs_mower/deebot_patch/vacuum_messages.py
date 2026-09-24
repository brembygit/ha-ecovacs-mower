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

Three things the library does not give a T90 at all are added here as well:

* the raw work and charge states, as ``VacuumWorkStateEvent`` and
  ``VacuumChargeEvent``, for the station-state and charge-state sensors. The
  library folds both into ``StateEvent`` / ``StationEvent`` and loses what the
  sensors need (``spinDrying``, gap S1; the ``isCharging`` value itself);
* the rooms of a map (``GetMapSetV2Rooms``). The library's parser expects 10
  or 11 fields per room, the T90 sends 12, and it then asks for subsets the
  T90 does not have; ``twunby`` has no map capability in 18.5.1 either;
* a room clean in the app's shape (``CleanV2Rooms``), which the library's
  ``CleanAreaV2`` does not produce.

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

from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet

from deebot_client.commands.json.auto_empty import GetAutoEmpty
from deebot_client.commands.json.charge_state import GetChargeState
from deebot_client.commands.json.common import (
    ExecuteCommand,
    JsonCommandWithMessageHandling,
)
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.events import StateEvent
from deebot_client.events.auto_empty import AutoEmptyEvent, Frequency
from deebot_client.events.base import Event
from deebot_client.message import HandlingResult, HandlingState, MessageBodyDataDict
from deebot_client.messages.json.auto_empty import OnAutoEmpty
from deebot_client.messages.json.work_state import OnWorkState
from deebot_client.models import State
from deebot_client.rs.util import decompress_base64_data
from deebot_client.util import get_enum
import orjson

from .messages import OnChargeState

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator
    from deebot_client.event_bus import EventBus
    from deebot_client.models import ApiDeviceInfo

_LOGGER = logging.getLogger(__name__)

_VACUUM_BUSES: WeakSet[EventBus] = WeakSet()
_AUTO_EMPTY_INTENSITY: WeakKeyDictionary[EventBus, int] = WeakKeyDictionary()
_ROOMS: WeakKeyDictionary[EventBus, dict[str, tuple[VacuumRoom, ...]]] = (
    WeakKeyDictionary()
)


@dataclass(frozen=True)
class VacuumWorkStateEvent(Event):
    """The robot and station states exactly as ``onWorkState`` reports them."""

    robot: str | None
    station: str | None
    paused: bool


@dataclass(frozen=True)
class VacuumChargeEvent(Event):
    """``isCharging`` exactly as ``onChargeState`` reports it.

    0 off the charger, 1 charging; 2 is reported for about a second as the
    robot reaches the dock, before 1.
    """

    is_charging: int


@dataclass(frozen=True)
class VacuumRoom:
    """One room of a map: the id the robot cleans by, and its name."""

    id: int
    name: str


@dataclass(frozen=True)
class VacuumRoomsEvent(Event):
    """The rooms of one map, in the order the robot lists them."""

    map_id: str
    rooms: tuple[VacuumRoom, ...]


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


def rooms_for(event_bus: EventBus) -> dict[str, tuple[VacuumRoom, ...]]:
    """The rooms last reported on *event_bus*, by map id."""
    return dict(_ROOMS.get(event_bus, {}))


def reset() -> None:
    """Forget every registration and record. Tests only."""
    _VACUUM_BUSES.clear()
    _AUTO_EMPTY_INTENSITY.clear()
    _ROOMS.clear()


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
        if not is_vacuum_bus(event_bus):
            return result
        robot = (data.get("robotState") or {}).get("state")
        station = (data.get("stationState") or {}).get("state")
        paused = data.get("paused") == 1
        if not paused and robot == "idle" and station == "idle":
            event_bus.notify(StateEvent(State.IDLE))
        # Published whatever the library made of it, spinDrying included: the
        # station-state sensor reads this, not StationEvent.
        event_bus.notify(VacuumWorkStateEvent(robot, station, paused))
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


class CleanV2AppStart(ExecuteCommand):
    """An auto clean start in the shape the app's home-screen card sends it.

    The app has two start buttons. The one on the robot's own page sends its
    requests with ``channel "android"``; the "Start" button on the robot's
    card in the app's home screen reaches the robot as a ``clean_V2`` whose
    header says ``channel "ROP"`` and whose data carries ``noVoiceResp``.
    Seen on a T90 (firmware 1.103.0) on 2026-09-24::

        header {"ver": 0.1, "priority": 1, "ts": <ms>, "channel": "ROP"}
        data   {"act": "start", "content": {"type": "auto"}, "noVoiceResp": 0}

    Only that card's start was seen to complete the app's daily "automatic
    clean" reward task, at the press. This command reproduces what the robot
    receives, header included, so that whether the robot-side shape alone is
    enough can be tested from Home Assistant. It does not reproduce the app's
    own HTTP call behind the card, which Home Assistant cannot see.

    Not a CleanV2: that class turns a start into a resume when the robot is
    paused, and a resume is not what the card sends.
    """

    NAME = "clean_V2"

    def __init__(self) -> None:
        super().__init__(
            {"act": "start", "content": {"type": "auto"}, "noVoiceResp": 0}
        )

    def _get_payload(self) -> dict[str, Any]:
        # The library's header is {"pri": "1", "ts": <s>, "tzm": 480,
        # "ver": "0.0.50"}; this one copies the captured request field for
        # field, down to the numeric types and the millisecond timestamp.
        return {
            "header": {
                "ver": 0.1,
                "priority": 1,
                "ts": int(time.time() * 1000),
                "channel": "ROP",
            },
            "body": {"data": self._args},
        }


def _note_charge(event_bus: EventBus, body: dict[str, Any]) -> None:
    """Publish ``isCharging`` for a patched vacuum, if the body carries one."""
    data = body.get("data")
    if (
        is_vacuum_bus(event_bus)
        and isinstance(data, dict)
        and isinstance(is_charging := data.get("isCharging"), int)
    ):
        event_bus.notify(VacuumChargeEvent(is_charging))


class OnChargeStateVacuum(OnChargeState):
    """``onChargeState`` that also publishes the raw ``isCharging``.

    Replaces the mowers' ``OnChargeState`` in ``MESSAGES`` and runs it first,
    so a mower's push is handled exactly as before.
    """

    @classmethod
    def _handle_body(cls, event_bus: EventBus, body: dict[str, Any]) -> HandlingResult:
        """Handle message->body."""
        result = super()._handle_body(event_bus, body)
        _note_charge(event_bus, body)
        return result


class GetChargeStateVacuum(GetChargeState):
    """``getChargeState`` that also publishes the raw ``isCharging``.

    Sent by the charge-state sensor when it is added: the push only arrives
    when the value changes.
    """

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        """Handle message->body->data."""
        result = super()._handle_body_data_dict(event_bus, data)
        _note_charge(event_bus, {"data": data})
        return result


class GetMapSetV2Rooms(JsonCommandWithMessageHandling, MessageBodyDataDict):
    """The rooms of one map, as the app asks for them.

    The app sends ``{"mid": <map id>, "type": "ar", "count": 15, "start": 0}``.
    The answer's ``subsets`` is base64 zstd JSON, one row per room; on a T90
    (firmware 1.103.0) a row has 12 fields and starts ``[id, name, …]``. Room
    ids are per map and not dense.

    The rooms are recorded before they are published, so a caller that awaited
    this command can read them with ``rooms_for()`` at once.
    """

    NAME = "getMapSet_V2"

    def __init__(self, map_id: str) -> None:
        super().__init__({"mid": map_id, "type": "ar", "count": 15, "start": 0})

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        """Handle message->body->data."""
        map_id = data.get("mid")
        if data.get("type") != "ar" or not isinstance(map_id, str):
            return HandlingResult.analyse()
        try:
            rows = orjson.loads(decompress_base64_data(data["subsets"]).decode())
        except (KeyError, TypeError, ValueError, orjson.JSONDecodeError):
            _LOGGER.debug("Could not decode the rooms of map %s", map_id, exc_info=True)
            return HandlingResult.analyse()

        rooms: list[VacuumRoom] = []
        for row in rows:
            try:
                room_id = int(row[0])
            except (IndexError, TypeError, ValueError):
                continue
            name = str(row[1]).strip() if len(row) > 1 else ""
            rooms.append(VacuumRoom(room_id, name or f"Room {room_id}"))
        if len(rooms) >= 15:
            _LOGGER.debug(
                "Map %s answered 15 rooms, the most one request asks for", map_id
            )

        _ROOMS.setdefault(event_bus, {})[map_id] = tuple(rooms)
        event_bus.notify(VacuumRoomsEvent(map_id, tuple(rooms)))
        return HandlingResult.success()


class CleanV2Rooms(ExecuteCommand):
    """A clean of the given rooms, in the app's shape.

    ``{"act": "start", "content": {"type": "freeClean", "value": "1,4;1,3"}}``:
    one ``1,<room id>`` pair per room, joined by ``;``, in the order given
    (the app keeps the order the rooms were tapped in). The library's
    ``CleanAreaV2`` joins the ids with commas behind a single count instead.

    Not a CleanV2: that class turns a start on a paused robot into a resume.
    """

    NAME = "clean_V2"

    def __init__(self, room_ids: list[int]) -> None:
        if not room_ids:
            raise ValueError("at least one room is needed")
        value = ";".join(f"1,{room_id}" for room_id in room_ids)
        super().__init__(
            {"act": "start", "content": {"type": "freeClean", "value": value}}
        )
