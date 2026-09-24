"""The selects exist for the verified vacuums only, never for a mower."""

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


def test_the_select_platform_is_loaded() -> None:
    """A platform file that is not in PLATFORMS is never loaded at all."""
    from homeassistant.const import Platform

    from custom_components.ecovacs_mower import PLATFORMS

    assert Platform.SELECT in PLATFORMS


def test_expected_select_keys() -> None:
    """Locks the set. If it changes, that must be a decision, not an accident."""
    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    assert {d.key for d in ENTITY_DESCRIPTIONS} == {"work_mode", "auto_empty"}


def test_every_select_has_a_translation_and_an_icon() -> None:
    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    keys = {d.translation_key for d in ENTITY_DESCRIPTIONS}
    assert keys <= set(_strings()["entity"]["select"])
    assert keys <= set(_icons()["entity"]["select"])


def test_no_stale_select_translations_or_icons() -> None:
    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    keys = {d.translation_key for d in ENTITY_DESCRIPTIONS}
    assert set(_strings()["entity"]["select"]) <= keys
    assert set(_icons()["entity"]["select"]) <= keys


def test_both_selects_are_settings() -> None:
    from homeassistant.const import EntityCategory

    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    assert all(d.entity_category is EntityCategory.CONFIG for d in ENTITY_DESCRIPTIONS)


async def test_every_t90_option_has_a_translation(t90_capabilities) -> None:
    """An option without a state string shows as a raw key such as vacuum_and_mop."""
    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    strings = _strings()["entity"]["select"]
    for description in ENTITY_DESCRIPTIONS:
        capability = description.capability_fn(t90_capabilities)
        assert capability is not None, description.key
        options = description.options_fn(capability)
        assert set(options) <= set(strings[description.translation_key]["state"])


async def test_work_mode_offers_only_what_the_app_offers(t90_capabilities) -> None:
    """Mop only (2) is in the library's list but never offered by the app."""
    from custom_components.ecovacs_mower.select import ENTITY_DESCRIPTIONS

    description = next(d for d in ENTITY_DESCRIPTIONS if d.key == "work_mode")
    options = description.options_fn(description.capability_fn(t90_capabilities))

    assert set(options) == {"vacuum_and_mop", "vacuum", "mop_after_vacuum"}


async def test_selects_are_built_for_the_verified_vacuums_only(
    t90_capabilities,
) -> None:
    """The platform reads controller.vacuums, not controller.devices.

    The second T90 stands for one whose capability patch did not take: it is on
    the account, so it is in devices, but not in vacuums.
    """
    from unittest.mock import MagicMock

    from custom_components.ecovacs_mower.select import async_setup_entry

    verified = _device(t90_capabilities, "did-verified")
    unverified = _device(t90_capabilities, "did-unverified")
    config_entry = MagicMock()
    config_entry.runtime_data.devices = [verified, unverified]
    config_entry.runtime_data.vacuums = [verified]
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), config_entry, add_entities)

    (entities,) = add_entities.call_args.args
    built = {
        (entity._device.device_info["did"], entity.entity_description.key)
        for entity in entities
    }
    assert built == {
        ("did-verified", "work_mode"),
        ("did-verified", "auto_empty"),
    }


async def test_no_vacuum_means_no_select() -> None:
    from unittest.mock import MagicMock

    from custom_components.ecovacs_mower.select import async_setup_entry

    config_entry = MagicMock()
    config_entry.runtime_data.vacuums = []
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), config_entry, add_entities)

    add_entities.assert_not_called()


async def test_auto_empty_goes_through_the_three_field_setter(
    t90_capabilities,
) -> None:
    """Only the frequency is chosen here; the command fills in the rest.

    The firmware refuses a frequency on its own (code 20004), so the patched
    setter completes enable and intensity from the robot's last report when
    it executes — see test_vacuum_messages.py.
    """
    from unittest.mock import AsyncMock

    from deebot_client.events.auto_empty import Frequency

    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        SetAutoEmptyVacuum,
    )
    from custom_components.ecovacs_mower.select import (
        ENTITY_DESCRIPTIONS,
        EcovacsSelect,
    )

    description = next(d for d in ENTITY_DESCRIPTIONS if d.key == "auto_empty")
    capability = description.capability_fn(t90_capabilities)
    entity = EcovacsSelect(_device(t90_capabilities), capability, description)
    entity._execute_command = AsyncMock()

    await entity.async_select_option("smart")

    (command,) = entity._execute_command.await_args.args
    assert type(command) is SetAutoEmptyVacuum
    assert command._frequency is Frequency.SMART
    assert command._enable is None
    assert command._intensity is None
