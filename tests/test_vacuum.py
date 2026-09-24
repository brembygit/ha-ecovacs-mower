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
