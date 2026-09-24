"""The patched vacuums' work-state rule and three-field auto-empty setter."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from deebot_client.commands.json.auto_empty import GetAutoEmpty
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.event_bus import EventBus
from deebot_client.events import StateEvent
from deebot_client.events.auto_empty import AutoEmptyEvent, Frequency
from deebot_client.events.station import State as StationState, StationEvent
from deebot_client.message import HandlingState
from deebot_client.messages.json import MESSAGES
from deebot_client.models import State

from custom_components.ecovacs_mower.deebot_patch import apply
from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
    CleanV2AppStart,
    CleanV2Rooms,
    GetChargeStateVacuum,
    GetMapSetV2Rooms,
    OnChargeStateVacuum,
    VacuumChargeEvent,
    VacuumRoom,
    VacuumRoomsEvent,
    VacuumWorkStateEvent,
    rooms_for,
    GetAutoEmptyVacuum,
    GetWorkStateVacuum,
    OnAutoEmptyVacuum,
    OnWorkStateVacuum,
    SetAutoEmptyVacuum,
    auto_empty_intensity_for,
    is_vacuum_bus,
    register_vacuum_bus,
    reset,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset()
    yield
    reset()


def _bus() -> EventBus:
    return EventBus(AsyncMock(), Mock(get_refresh_commands=lambda _event: []))


def _vacuum_bus() -> EventBus:
    bus = _bus()
    register_vacuum_bus(bus)
    return bus


def _work_state(robot: str, station: str, paused: int = 0) -> dict:
    """A work state body in the shape a T90 on firmware 1.103.0 sends."""
    return {
        "body": {
            "data": {
                "paused": paused,
                "robotState": {"state": robot, "trigger": "app"},
                "stationState": {"state": station, "trigger": "app"},
            }
        }
    }


def _auto_empty(enable: int = 1, frequency: str = "auto", intensity: int = 0) -> dict:
    return {
        "body": {
            "data": {
                "enable": enable,
                "frequency": frequency,
                "intensity": intensity,
                "status": 0,
            }
        }
    }


# --- registration ---------------------------------------------------------


def test_registration_marks_only_that_bus() -> None:
    marked, other = _vacuum_bus(), _bus()
    assert is_vacuum_bus(marked)
    assert not is_vacuum_bus(other)


def test_apply_registers_both_handlers() -> None:
    apply()
    assert MESSAGES["onWorkState"] is OnWorkStateVacuum
    assert MESSAGES["onAutoEmpty"] is OnAutoEmptyVacuum


def test_the_commands_keep_the_librarys_names() -> None:
    assert GetWorkStateVacuum.NAME == GetWorkState.NAME == "getWorkState"
    assert GetAutoEmptyVacuum.NAME == GetAutoEmpty.NAME == "getAutoEmpty"
    assert SetAutoEmptyVacuum.NAME == "setAutoEmpty"
    assert issubclass(GetWorkStateVacuum, GetWorkState)
    assert issubclass(GetAutoEmptyVacuum, GetAutoEmpty)


# --- work state (S4) --------------------------------------------------------


def test_library_publishes_no_robot_state_for_idle_idle() -> None:
    # Documents the gap: a robot stopped away from the dock reports this and
    # the library leaves the vacuum at its previous state.
    bus = _bus()
    bus.notify(StateEvent(State.CLEANING))
    OnWorkStateVacuum.handle(bus, _work_state("idle", "idle"))
    assert bus.get_last_event(StateEvent) == StateEvent(State.CLEANING)


def test_a_stop_away_from_the_dock_becomes_idle() -> None:
    # The sequence seen from HA on 2026-09-24: cleaning, then a stop.
    bus = _vacuum_bus()
    OnWorkStateVacuum.handle(bus, _work_state("cleaning", "idle"))
    assert bus.get_last_event(StateEvent) == StateEvent(State.CLEANING)

    OnWorkStateVacuum.handle(bus, _work_state("idle", "idle"))

    assert bus.get_last_event(StateEvent) == StateEvent(State.IDLE)
    assert bus.get_last_event(StationEvent) == StationEvent(StationState.IDLE)


def test_idle_idle_on_the_dock_stays_docked() -> None:
    # Docking as seen from HA: the charge state says DOCKED, then onWorkState
    # reports (idle, idle) a second later. The bus keeps DOCKED on its own.
    bus = _vacuum_bus()
    bus.notify(StateEvent(State.DOCKED))

    OnWorkStateVacuum.handle(bus, _work_state("idle", "idle"))

    assert bus.get_last_event(StateEvent) == StateEvent(State.DOCKED)


def test_a_paused_station_action_is_left_to_the_library() -> None:
    bus = _vacuum_bus()
    OnWorkStateVacuum.handle(bus, _work_state("idle", "idle", paused=1))
    assert bus.get_last_event(StateEvent) == StateEvent(State.PAUSED)


@pytest.mark.parametrize(
    ("robot", "station", "expected"),
    [
        ("cleaning", "idle", State.CLEANING),
        ("idle", "goCharging", State.RETURNING),
        ("idle", "emptying", State.DOCKED),
        ("cleaning", "washing", State.DOCKED),
    ],
)
def test_other_pairs_are_the_librarys(
    robot: str, station: str, expected: State
) -> None:
    bus = _vacuum_bus()
    OnWorkStateVacuum.handle(bus, _work_state(robot, station))
    assert bus.get_last_event(StateEvent) == StateEvent(expected)


def test_an_unregistered_bus_gets_the_library_behaviour() -> None:
    # Any other JSON device on the account, or core's ecovacs in the same
    # process: MESSAGES is global.
    bus = _bus()
    OnWorkStateVacuum.handle(bus, _work_state("idle", "idle"))
    assert bus.get_last_event(StateEvent) is None


def test_the_get_command_applies_the_same_rule() -> None:
    bus = _vacuum_bus()
    bus.notify(StateEvent(State.CLEANING))

    result = GetWorkStateVacuum.handle(bus, _work_state("idle", "idle"))

    assert result.state is HandlingState.SUCCESS
    assert bus.get_last_event(StateEvent) == StateEvent(State.IDLE)


# --- auto-empty (G7) --------------------------------------------------------


def test_the_intensity_is_recorded_and_the_event_still_published() -> None:
    bus = _vacuum_bus()

    OnAutoEmptyVacuum.handle(bus, _auto_empty(intensity=1))

    assert auto_empty_intensity_for(bus) == 1
    assert bus.get_last_event(AutoEmptyEvent) == AutoEmptyEvent(True, Frequency.AUTO)


def test_the_get_answer_records_the_intensity_too() -> None:
    bus = _vacuum_bus()
    GetAutoEmptyVacuum.handle(bus, _auto_empty(intensity=0))
    assert auto_empty_intensity_for(bus) == 0


def test_an_unregistered_bus_records_nothing() -> None:
    bus = _bus()
    OnAutoEmptyVacuum.handle(bus, _auto_empty(intensity=1))
    assert auto_empty_intensity_for(bus) is None
    assert bus.get_last_event(AutoEmptyEvent) == AutoEmptyEvent(True, Frequency.AUTO)


def _device_info() -> dict:
    return {"class": "twunby", "did": "did-t90", "resource": "res"}


async def _execute(command: SetAutoEmptyVacuum, bus: EventBus) -> tuple:
    """Run _execute with the REST call replaced; return (result, sent args)."""
    sent: list[dict] = []

    async def fake_request(self, _authenticator, _device_info):
        sent.append(dict(self._args))
        return {"ret": "ok", "resp": {"body": {"code": 0, "msg": "ok"}}}

    with patch.object(SetAutoEmptyVacuum, "_execute_api_request", fake_request):
        result, _ = await command._execute(Mock(), _device_info(), bus)
    return result, sent


async def test_a_frequency_change_sends_all_three_fields() -> None:
    # What the select does, and what the firmware refused alone on 2026-09-24.
    # Intensity 1 (standard), not the robot's current 0: a value the setter
    # could not produce by accident.
    bus = _vacuum_bus()
    OnAutoEmptyVacuum.handle(bus, _auto_empty(enable=1, frequency="auto", intensity=1))

    result, sent = await _execute(SetAutoEmptyVacuum(None, "smart"), bus)

    assert result.state is HandlingState.SUCCESS
    assert sent == [{"enable": 1, "frequency": "smart", "intensity": 1}]


async def test_explicit_arguments_win_over_the_report() -> None:
    bus = _vacuum_bus()
    OnAutoEmptyVacuum.handle(bus, _auto_empty(enable=1, frequency="auto", intensity=0))

    _, sent = await _execute(SetAutoEmptyVacuum(False, "smart", 1), bus)

    assert sent == [{"enable": 0, "frequency": "smart", "intensity": 1}]


async def test_nothing_is_sent_before_the_settings_are_known() -> None:
    bus = _vacuum_bus()

    result, sent = await _execute(SetAutoEmptyVacuum(None, "smart"), bus)

    assert result.state is HandlingState.FAILED
    assert sent == []


async def test_nothing_is_sent_without_the_intensity() -> None:
    # An AutoEmptyEvent alone (for instance from an unregistered handler)
    # carries enable and frequency but not the intensity.
    bus = _vacuum_bus()
    bus.notify(AutoEmptyEvent(True, Frequency.AUTO))

    result, sent = await _execute(SetAutoEmptyVacuum(None, "smart"), bus)

    assert result.state is HandlingState.FAILED
    assert sent == []


async def test_a_refusal_is_reported_as_a_failure() -> None:
    bus = _vacuum_bus()
    OnAutoEmptyVacuum.handle(bus, _auto_empty())

    async def refused(self, _authenticator, _device_info):
        return {"ret": "ok", "resp": {"body": {"code": 20004, "msg": "get act fail"}}}

    with patch.object(SetAutoEmptyVacuum, "_execute_api_request", refused):
        result, _ = await SetAutoEmptyVacuum(None, "smart")._execute(
            Mock(), _device_info(), bus
        )

    assert result.state is HandlingState.FAILED


def test_the_app_start_copies_the_home_card_request() -> None:
    # The clean_V2 the app's home-screen "Start" sent on 2026-09-24, field for
    # field: the header's numeric types and millisecond timestamp included.
    payload = CleanV2AppStart()._get_payload()

    header = payload["header"]
    assert set(header) == {"ver", "priority", "ts", "channel"}
    assert header["ver"] == 0.1
    assert header["priority"] == 1
    assert header["channel"] == "ROP"
    assert isinstance(header["ts"], int)
    assert header["ts"] > 10**12  # milliseconds, not the library's seconds
    assert payload["body"] == {
        "data": {"act": "start", "content": {"type": "auto"}, "noVoiceResp": 0}
    }
    assert CleanV2AppStart.NAME == "clean_V2"


async def test_the_app_start_is_sent_as_a_start_even_when_paused() -> None:
    # CleanV2 would turn a start on a paused robot into a resume; the card's
    # request is always a start.
    bus = _vacuum_bus()
    bus.notify(StateEvent(State.PAUSED))
    sent: list[dict] = []

    async def fake_request(self, _authenticator, _device_info):
        sent.append(self._get_payload()["body"]["data"])
        return {"ret": "ok", "resp": {"body": {"code": 0, "msg": "ok"}}}

    with patch.object(CleanV2AppStart, "_execute_api_request", fake_request):
        result, _ = await CleanV2AppStart()._execute(Mock(), _device_info(), bus)

    assert result.state is HandlingState.SUCCESS
    assert [data["act"] for data in sent] == ["start"]



# --- raw work and charge states --------------------------------------------


def _last(bus: EventBus, event_type):
    return bus.get_last_event(event_type)


def test_the_raw_work_state_is_published_for_a_vacuum() -> None:
    bus = _vacuum_bus()

    OnWorkStateVacuum.handle(bus, _work_state("cleaning", "washing"))

    assert _last(bus, VacuumWorkStateEvent) == VacuumWorkStateEvent(
        "cleaning", "washing", False
    )


def test_spin_drying_is_published_though_the_library_drops_it() -> None:
    # Gap S1: the library's table has no spinDrying and publishes nothing.
    bus = _vacuum_bus()

    OnWorkStateVacuum.handle(bus, _work_state("idle", "spinDrying"))

    assert _last(bus, VacuumWorkStateEvent) == VacuumWorkStateEvent(
        "idle", "spinDrying", False
    )


def test_a_paused_station_action_is_published_as_paused() -> None:
    bus = _vacuum_bus()

    OnWorkStateVacuum.handle(bus, _work_state("idle", "washing", paused=1))

    assert _last(bus, VacuumWorkStateEvent).paused is True


def test_no_raw_work_state_for_an_unregistered_bus() -> None:
    bus = _bus()

    OnWorkStateVacuum.handle(bus, _work_state("cleaning", "idle"))

    assert _last(bus, VacuumWorkStateEvent) is None


def _charge(is_charging: int) -> dict:
    return {"body": {"data": {"chargeRate": 0, "isCharging": is_charging, "mode": "autoEmpty"}}}


@pytest.mark.parametrize("value", [0, 1, 2])
def test_the_raw_charge_state_is_published_for_a_vacuum(value: int) -> None:
    bus = _vacuum_bus()

    OnChargeStateVacuum.handle(bus, _charge(value))

    assert _last(bus, VacuumChargeEvent) == VacuumChargeEvent(value)


def test_the_charge_push_still_docks_the_robot() -> None:
    # What the mowers' OnChargeState did before, for every bus.
    bus = _vacuum_bus()

    OnChargeStateVacuum.handle(bus, _charge(1))

    assert _last(bus, StateEvent) == StateEvent(State.DOCKED)


def test_no_raw_charge_state_for_an_unregistered_bus() -> None:
    bus = _bus()

    OnChargeStateVacuum.handle(bus, _charge(1))

    assert _last(bus, VacuumChargeEvent) is None
    assert _last(bus, StateEvent) == StateEvent(State.DOCKED)


def test_the_charge_answer_publishes_the_raw_state() -> None:
    bus = _vacuum_bus()

    GetChargeStateVacuum.handle(bus, {"body": {"code": 0, "data": {"isCharging": 0}}})

    assert _last(bus, VacuumChargeEvent) == VacuumChargeEvent(0)


def test_the_vacuum_charge_handler_is_the_registered_one() -> None:
    apply()

    assert MESSAGES["onChargeState"] is OnChargeStateVacuum


# --- rooms -----------------------------------------------------------------


def _rooms_answer(rows: list, map_id: str = "map-a", type_: str = "ar") -> dict:
    """A getMapSet_V2 answer: base64 zstd JSON rows, as a T90 sends them."""
    import base64
    import json
    from compression import zstd

    subsets = base64.b64encode(zstd.compress(json.dumps(rows).encode())).decode()
    return {
        "body": {
            "code": 0,
            "data": {"mid": map_id, "msid": "1", "type": type_, "subsets": subsets},
        }
    }


# Invented rooms in the T90's 12-field row shape.
_ROWS = [
    ["1", "Hall", 0, "3-4", 0, 100, 200, "1-0-2", " ", "2-0", 0, 0],
    ["3", "Kitchen", 0, "1", 0, 300, 400, "1-0-2", " ", "0", 0, 0],
    ["4", "", 0, "1", 0, 500, 600, "1-0-2", " ", "0", 0, 0],
]


def test_the_rooms_request_is_the_apps() -> None:
    assert GetMapSetV2Rooms("map-a")._args == {
        "mid": "map-a",
        "type": "ar",
        "count": 15,
        "start": 0,
    }
    assert GetMapSetV2Rooms.NAME == "getMapSet_V2"


def test_the_rooms_of_a_map_are_decoded_and_recorded() -> None:
    bus = _vacuum_bus()

    result = GetMapSetV2Rooms.handle(bus, _rooms_answer(_ROWS))

    assert result.state is HandlingState.SUCCESS
    expected = (
        VacuumRoom(1, "Hall"),
        VacuumRoom(3, "Kitchen"),
        VacuumRoom(4, "Room 4"),  # an unnamed room gets its id
    )
    assert rooms_for(bus) == {"map-a": expected}
    assert _last(bus, VacuumRoomsEvent) == VacuumRoomsEvent("map-a", expected)


def test_rooms_are_kept_per_map() -> None:
    bus = _vacuum_bus()

    GetMapSetV2Rooms.handle(bus, _rooms_answer(_ROWS[:1], "map-a"))
    GetMapSetV2Rooms.handle(bus, _rooms_answer(_ROWS[1:2], "map-b"))

    assert set(rooms_for(bus)) == {"map-a", "map-b"}


def test_another_map_set_type_is_not_taken_for_rooms() -> None:
    bus = _vacuum_bus()

    result = GetMapSetV2Rooms.handle(bus, _rooms_answer([], type_="vw"))

    assert result.state is HandlingState.ANALYSE_LOGGED
    assert rooms_for(bus) == {}


def test_an_undecodable_answer_is_not_taken_for_rooms() -> None:
    bus = _vacuum_bus()
    answer = {"body": {"code": 0, "data": {"mid": "m", "type": "ar", "subsets": "!!"}}}

    result = GetMapSetV2Rooms.handle(bus, answer)

    assert result.state is HandlingState.ANALYSE_LOGGED
    assert rooms_for(bus) == {}


# --- room clean ------------------------------------------------------------


def test_a_room_clean_is_the_apps_free_clean() -> None:
    # PROTOCOL.md: rooms 4, 3, 1 tapped in that order.
    assert CleanV2Rooms([4, 3, 1])._args == {
        "act": "start",
        "content": {"type": "freeClean", "value": "1,4;1,3;1,1"},
    }
    assert CleanV2Rooms.NAME == "clean_V2"


def test_a_room_clean_needs_a_room() -> None:
    with pytest.raises(ValueError):
        CleanV2Rooms([])
