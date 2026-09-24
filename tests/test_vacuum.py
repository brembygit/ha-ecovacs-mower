"""The vacuum entity exists for the verified vacuums only, never for a mower."""

import pytest

from tests import requires_ha

pytestmark = requires_ha

T90 = "twunby"


def _strings() -> dict:
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    return json.loads((root / "strings.json").read_text(encoding="utf-8"))


def _icons() -> dict:
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    return json.loads((root / "icons.json").read_text(encoding="utf-8"))


@pytest.fixture
async def t90_capabilities():
    """The T90's capabilities with the patch applied, cache left clean."""
    from deebot_client.hardware import _DEVICES, get_static_device_info

    from custom_components.ecovacs_mower.deebot_patch.vacuum import (
        patch_vacuum_device_info,
    )

    _DEVICES.pop(T90, None)
    await patch_vacuum_device_info(T90)
    yield (await get_static_device_info(T90)).capabilities
    _DEVICES.pop(T90, None)


def _device(capabilities, did: str = "did-t90"):
    from unittest.mock import MagicMock

    device = MagicMock()
    device.device_info = {"did": did, "class": T90}
    device.capabilities = capabilities
    return device


def _vacuum(capabilities):
    from unittest.mock import AsyncMock

    from custom_components.ecovacs_mower.vacuum import EcovacsVacuum

    entity = EcovacsVacuum(_device(capabilities))
    entity._execute_command = AsyncMock()
    return entity


def test_the_vacuum_platform_is_loaded() -> None:
    """A platform file that is not in PLATFORMS is never loaded at all."""
    from homeassistant.const import Platform

    from custom_components.ecovacs_mower import PLATFORMS

    assert Platform.VACUUM in PLATFORMS


def test_the_vacuum_has_its_translation_and_icon_and_nothing_else() -> None:
    from custom_components.ecovacs_mower.vacuum import EcovacsVacuum

    key = EcovacsVacuum.entity_description.translation_key
    assert set(_strings()["entity"]["vacuum"]) == {key}
    assert set(_icons()["entity"]["vacuum"]) == {key}


def test_the_vacuum_is_named_after_its_device() -> None:
    """A translated name would beat name=None: "<device> Vacuum", not "<device>"."""
    from custom_components.ecovacs_mower.vacuum import EcovacsVacuum

    key = EcovacsVacuum.entity_description.translation_key
    assert EcovacsVacuum.entity_description.name is None
    assert "name" not in _strings()["entity"]["vacuum"][key]


async def test_every_t90_fan_speed_has_a_translation(t90_capabilities) -> None:
    from custom_components.ecovacs_mower.vacuum import EcovacsVacuum

    key = EcovacsVacuum.entity_description.translation_key
    states = _strings()["entity"]["vacuum"][key]["state_attributes"]["fan_speed"][
        "state"
    ]
    entity = _vacuum(t90_capabilities)

    assert entity.fan_speed_list
    assert set(entity.fan_speed_list) <= set(states)


def test_every_library_state_is_mapped() -> None:
    """An unmapped state would log a warning and leave the entity stale."""
    from deebot_client.models import State

    from custom_components.ecovacs_mower.vacuum import _STATE_TO_VACUUM_STATE

    assert set(_STATE_TO_VACUUM_STATE) == set(State)


async def test_vacuums_are_built_for_the_verified_vacuums_only(
    t90_capabilities,
) -> None:
    """The platform reads controller.vacuums, not controller.devices."""
    from unittest.mock import MagicMock

    from custom_components.ecovacs_mower.vacuum import async_setup_entry

    verified = _device(t90_capabilities, "did-verified")
    unverified = _device(t90_capabilities, "did-unverified")
    config_entry = MagicMock()
    config_entry.runtime_data.devices = [verified, unverified]
    config_entry.runtime_data.vacuums = [verified]
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), config_entry, add_entities)

    (entities,) = add_entities.call_args.args
    assert [e._device.device_info["did"] for e in entities] == ["did-verified"]


@pytest.mark.parametrize(
    ("method", "action"),
    [
        ("async_start", "START"),
        ("async_pause", "PAUSE"),
        ("async_stop", "STOP"),
    ],
)
async def test_clean_commands_go_out_as_clean_v2(
    t90_capabilities, method: str, action: str
) -> None:
    from deebot_client.commands.json.clean import CleanV2
    from deebot_client.models import CleanAction

    entity = _vacuum(t90_capabilities)

    await getattr(entity, method)()

    entity._execute_command.assert_awaited_once_with(CleanV2(CleanAction[action]))


async def test_return_to_base_sends_charge(t90_capabilities) -> None:
    from deebot_client.commands.json.charge import Charge

    entity = _vacuum(t90_capabilities)

    await entity.async_return_to_base()

    entity._execute_command.assert_awaited_once_with(Charge())


async def test_locate_plays_the_sound(t90_capabilities) -> None:
    from deebot_client.commands.json.play_sound import PlaySound

    entity = _vacuum(t90_capabilities)

    await entity.async_locate()

    entity._execute_command.assert_awaited_once_with(PlaySound())


async def test_fan_speed_is_set_by_its_key(t90_capabilities) -> None:
    from deebot_client.commands.json.fan_speed import SetFanSpeed

    entity = _vacuum(t90_capabilities)

    await entity.async_set_fan_speed("max_plus")

    entity._execute_command.assert_awaited_once_with(SetFanSpeed("max_plus"))



def _maps_bus(using: str = "map-a"):
    """A real EventBus holding two built maps and one unbuilt one."""
    from unittest.mock import AsyncMock, Mock

    from deebot_client.event_bus import EventBus
    from deebot_client.events.map import CachedMapInfoEvent, Map
    from deebot_client.rs.map import RotationAngle

    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        register_vacuum_bus,
    )

    bus = EventBus(AsyncMock(), Mock(get_refresh_commands=lambda _event: []))
    register_vacuum_bus(bus)
    angle = RotationAngle.from_int(0)
    bus.notify(
        CachedMapInfoEvent(
            maps={
                Map("map-a", "Ground floor", using == "map-a", True, angle),
                Map("map-b", "First floor", using == "map-b", True, angle),
                Map("map-c", "", False, False, angle),
            }
        )
    )
    return bus


def _record_rooms(bus, map_id: str, rooms: list[tuple[int, str]]) -> None:
    from custom_components.ecovacs_mower.deebot_patch import vacuum_messages

    vacuum_messages._ROOMS.setdefault(bus, {})[map_id] = tuple(
        vacuum_messages.VacuumRoom(room_id, name) for room_id, name in rooms
    )


def _vacuum_with_maps(capabilities, using: str = "map-a"):
    entity = _vacuum(capabilities)
    entity._device.events = _maps_bus(using)
    _record_rooms(entity._device.events, "map-a", [(1, "Hall"), (3, "Kitchen")])
    _record_rooms(entity._device.events, "map-b", [(1, "Study")])
    return entity


def test_the_vacuum_can_clean_by_area(t90_capabilities) -> None:
    from homeassistant.components.vacuum import VacuumEntityFeature

    entity = _vacuum(t90_capabilities)

    assert VacuumEntityFeature.CLEAN_AREA in entity.supported_features


async def test_segments_are_grouped_by_map_the_robot_is_on_first(
    t90_capabilities,
) -> None:
    from homeassistant.components.vacuum import Segment

    entity = _vacuum_with_maps(t90_capabilities, using="map-b")

    segments = await entity.async_get_segments()

    assert segments == [
        Segment(id="map-b_1", name="Study", group="First floor"),
        Segment(id="map-a_1", name="Hall", group="Ground floor"),
        Segment(id="map-a_3", name="Kitchen", group="Ground floor"),
    ]
    entity._execute_command.assert_not_awaited()


async def test_missing_rooms_are_asked_for_before_answering(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        GetMapSetV2Rooms,
    )

    entity = _vacuum(t90_capabilities)
    entity._device.events = _maps_bus()

    # The fake command records nothing, so the rooms stay unknown.
    with pytest.raises(HomeAssistantError):
        await entity.async_get_segments()

    sent = [call.args[0] for call in entity._execute_command.await_args_list]
    rooms_requests = [c for c in sent if isinstance(c, GetMapSetV2Rooms)]
    assert sorted(c._args["mid"] for c in rooms_requests) == ["map-a", "map-b"]


async def test_rooms_are_cleaned_in_the_apps_shape_in_the_given_order(
    t90_capabilities,
) -> None:
    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        CleanV2Rooms,
    )

    entity = _vacuum_with_maps(t90_capabilities)

    await entity.async_clean_segments(["map-a_3", "map-a_1"])

    entity._execute_command.assert_awaited_once_with(CleanV2Rooms([3, 1]))


async def test_rooms_on_another_floor_are_refused(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _vacuum_with_maps(t90_capabilities, using="map-a")

    with pytest.raises(HomeAssistantError, match="First floor"):
        await entity.async_clean_segments(["map-b_1"])
    entity._execute_command.assert_not_awaited()


async def test_rooms_on_two_floors_are_refused(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _vacuum_with_maps(t90_capabilities)

    with pytest.raises(HomeAssistantError, match="more than one floor"):
        await entity.async_clean_segments(["map-a_1", "map-b_1"])
    entity._execute_command.assert_not_awaited()


async def test_a_segment_of_a_vanished_map_is_refused(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _vacuum_with_maps(t90_capabilities)

    with pytest.raises(HomeAssistantError, match="map the"):
        await entity.async_clean_segments(["map-gone_1"])


def test_segment_ids_round_trip() -> None:
    from custom_components.ecovacs_mower.vacuum import parse_segment_id, segment_id

    assert parse_segment_id(segment_id("floor_2", 7)) == ("floor_2", 7)
    for bad in ("nounderscore", "_3", "map_x"):
        with pytest.raises(ValueError):
            parse_segment_id(bad)



async def test_rooms_are_cleaned_by_their_app_names_in_order(t90_capabilities) -> None:
    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        CleanV2Rooms,
    )

    entity = _vacuum_with_maps(t90_capabilities)

    await entity.async_clean_rooms(["kitchen", "Hall", "Kitchen"])

    entity._execute_command.assert_awaited_once_with(CleanV2Rooms([3, 1]))


async def test_a_room_name_on_the_map_in_use_wins(t90_capabilities) -> None:
    from custom_components.ecovacs_mower.deebot_patch import vacuum_messages
    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        CleanV2Rooms,
    )

    entity = _vacuum_with_maps(t90_capabilities)
    vacuum_messages._ROOMS[entity._device.events]["map-b"] += (
        vacuum_messages.VacuumRoom(9, "Hall"),
    )

    await entity.async_clean_rooms(["Hall"])

    entity._execute_command.assert_awaited_once_with(CleanV2Rooms([1]))


async def test_a_room_name_only_on_another_floor_is_refused(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _vacuum_with_maps(t90_capabilities)

    with pytest.raises(HomeAssistantError, match="First floor"):
        await entity.async_clean_rooms(["Study"])
    entity._execute_command.assert_not_awaited()


async def test_an_unknown_room_name_lists_the_rooms(t90_capabilities) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _vacuum_with_maps(t90_capabilities)

    with pytest.raises(HomeAssistantError, match="Kitchen"):
        await entity.async_clean_rooms(["Garage"])
    entity._execute_command.assert_not_awaited()
