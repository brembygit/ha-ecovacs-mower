"""Buttons: lifespan reset and sound signal."""

from tests import requires_ha

pytestmark = requires_ha


def test_four_lifespan_reset_buttons() -> None:
    from custom_components.ecovacs_mower.button import LIFESPAN_ENTITY_DESCRIPTIONS

    assert {d.key for d in LIFESPAN_ENTITY_DESCRIPTIONS} == {
        "reset_lifespan_blade",
        "reset_lifespan_lens_brush",
        "reset_lifespan_trimmer_brush",
        "reset_lifespan_weed_rope",
    }


def test_play_sound_button_exists() -> None:
    """The capability exists on 2i0fns but core does not expose it."""
    from custom_components.ecovacs_mower.button import ENTITY_DESCRIPTIONS

    assert {d.key for d in ENTITY_DESCRIPTIONS} == {"play_sound"}


def test_no_station_buttons() -> None:
    from custom_components.ecovacs_mower import button

    assert not hasattr(button, "STATION_ENTITY_DESCRIPTIONS")
    assert not hasattr(button, "EcovacsStationActionButtonEntity")


def test_every_description_has_a_translation() -> None:
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import (
        ENTITY_DESCRIPTIONS,
        LIFESPAN_ENTITY_DESCRIPTIONS,
    )

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    names = strings["entity"]["button"]

    for description in (*ENTITY_DESCRIPTIONS, *LIFESPAN_ENTITY_DESCRIPTIONS):
        assert description.translation_key in names, description.key


def test_every_button_has_an_icon() -> None:
    """A button without its own icon gets HA's generic icon — easy to miss."""
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import (
        ENTITY_DESCRIPTIONS,
        LIFESPAN_ENTITY_DESCRIPTIONS,
    )

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    icons = json.loads((root / "icons.json").read_text(encoding="utf-8"))
    names = icons["entity"]["button"]

    for description in (*ENTITY_DESCRIPTIONS, *LIFESPAN_ENTITY_DESCRIPTIONS):
        assert description.translation_key in names, description.key


def test_no_stale_button_translations_or_icons() -> None:
    """Every key in strings.json/icons.json must belong to a real button.

    The converse of the tests above: they check description -> string/icon, not
    the other way around. Without this, a leftover key for a removed button would
    go unnoticed.
    """
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import (
        ENTITY_DESCRIPTIONS,
        LIFESPAN_ENTITY_DESCRIPTIONS,
        MOWER_COMMAND_DESCRIPTIONS,
        VACUUM_COMMAND_DESCRIPTIONS,
        EcovacsClearFaultButtonEntity,
    )

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    icons = json.loads((root / "icons.json").read_text(encoding="utf-8"))

    keys = {
        d.translation_key
        for d in (
            *ENTITY_DESCRIPTIONS,
            *LIFESPAN_ENTITY_DESCRIPTIONS,
            *MOWER_COMMAND_DESCRIPTIONS,
            *VACUUM_COMMAND_DESCRIPTIONS,
        )
    } | {EcovacsClearFaultButtonEntity.entity_description.translation_key}
    assert set(strings["entity"]["button"]) <= keys
    assert set(icons["entity"]["button"]) <= keys


def test_the_clear_fault_button_has_a_translation_and_an_icon() -> None:
    """Not in either description tuple, so the loops above never reach it."""
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import EcovacsClearFaultButtonEntity

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    key = EcovacsClearFaultButtonEntity.entity_description.translation_key
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    icons = json.loads((root / "icons.json").read_text(encoding="utf-8"))

    assert key in strings["entity"]["button"]
    assert key in icons["entity"]["button"]


async def test_the_clear_fault_button_releases_the_latch() -> None:
    """Issue #53. The one clear that needs nothing from the mower.

    Deliberately sends no command: the latch is ours, the device has no notion
    of an acknowledged fault, and this must therefore work while the mower is
    unreachable — which is when a stale fault is most likely to be the thing
    bothering someone.
    """
    from unittest.mock import AsyncMock, Mock

    from custom_components.ecovacs_mower.button import EcovacsClearFaultButtonEntity

    latch = Mock()
    device = Mock(execute_command=AsyncMock())
    device.device_info = {"did": "test-did"}
    entity = EcovacsClearFaultButtonEntity(device, latch)

    await entity.async_press()

    latch.clear_by_request.assert_called_once_with()
    device.execute_command.assert_not_called()


def test_the_clear_fault_button_stays_available_when_the_mower_is_not() -> None:
    """A latch is local state; releasing it cannot depend on reaching the mower."""
    from custom_components.ecovacs_mower.button import EcovacsClearFaultButtonEntity

    assert EcovacsClearFaultButtonEntity._always_available is True


def test_the_mower_command_buttons_are_border_and_end_task() -> None:
    from custom_components.ecovacs_mower.button import MOWER_COMMAND_DESCRIPTIONS

    assert {d.key for d in MOWER_COMMAND_DESCRIPTIONS} == {"mow_border", "end_task"}


def test_the_border_button_is_limited_to_the_classes_with_a_capture() -> None:
    from custom_components.ecovacs_mower.button import MOWER_COMMAND_DESCRIPTIONS
    from custom_components.ecovacs_mower.deebot_patch.hardware import BORDER_CLASSES

    by_key = {d.key: d for d in MOWER_COMMAND_DESCRIPTIONS}
    assert by_key["mow_border"].classes == BORDER_CLASSES
    assert by_key["mow_border"].starts_job is True
    # Every supported mower: the V2 stop is captured, the non-V2 one is the
    # shape pause already uses, and the #51 reporter has non-V2 hardware.
    assert by_key["end_task"].classes is None
    assert by_key["end_task"].starts_job is False


def test_mower_command_buttons_have_translations_and_icons() -> None:
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import MOWER_COMMAND_DESCRIPTIONS

    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    icons = json.loads((root / "icons.json").read_text(encoding="utf-8"))

    for description in MOWER_COMMAND_DESCRIPTIONS:
        assert description.translation_key in strings["entity"]["button"]
        assert description.translation_key in icons["entity"]["button"]


async def test_the_border_button_sends_the_recorded_map_id_and_restarts_polling() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ecovacs_mower.button import (
        MOWER_COMMAND_DESCRIPTIONS,
        EcovacsMowerCommandButtonEntity,
    )
    from custom_components.ecovacs_mower.deebot_patch.border import MowBorder
    from custom_components.ecovacs_mower.deebot_patch.state_precedence import register

    description = next(d for d in MOWER_COMMAND_DESCRIPTIONS if d.key == "mow_border")
    device = MagicMock()
    device.device_info = {"did": "test-did", "class": "77atlz"}
    register(device.events).note_map("2049987783")
    controller = MagicMock()
    entity = EcovacsMowerCommandButtonEntity(device, controller, description)
    entity._execute_command = AsyncMock()

    await entity.async_press()

    controller.start_polling.assert_called_once_with(device)
    entity._execute_command.assert_awaited_once_with(MowBorder("2049987783"))


async def test_the_border_button_asks_for_the_map_when_it_has_none() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ecovacs_mower.button import (
        MOWER_COMMAND_DESCRIPTIONS,
        EcovacsMowerCommandButtonEntity,
    )
    from custom_components.ecovacs_mower.deebot_patch.map_messages import (
        MowerMapInfoEvent,
    )
    from custom_components.ecovacs_mower.deebot_patch.state_precedence import register

    description = next(d for d in MOWER_COMMAND_DESCRIPTIONS if d.key == "mow_border")
    device = MagicMock()
    device.device_info = {"did": "test-did", "class": "77atlz"}
    register(device.events)  # registered, but no map message has arrived
    controller = MagicMock()
    entity = EcovacsMowerCommandButtonEntity(device, controller, description)
    entity._execute_command = AsyncMock()

    with pytest.raises(HomeAssistantError, match="has not reported its map"):
        await entity.async_press()

    # Self-healing: the refresh is the getMapInfo_V2 that teaches the id.
    device.events.request_refresh.assert_called_once_with(MowerMapInfoEvent)
    entity._execute_command.assert_not_awaited()
    controller.start_polling.assert_not_called()


async def test_the_end_task_button_sends_stop_and_leaves_polling_alone() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from deebot_client.models import CleanAction

    from custom_components.ecovacs_mower.button import (
        MOWER_COMMAND_DESCRIPTIONS,
        EcovacsMowerCommandButtonEntity,
    )
    from custom_components.ecovacs_mower.deebot_patch.commands import CleanMower

    description = next(d for d in MOWER_COMMAND_DESCRIPTIONS if d.key == "end_task")
    device = MagicMock()
    device.device_info = {"did": "test-did", "class": "2px96q"}
    controller = MagicMock()
    entity = EcovacsMowerCommandButtonEntity(device, controller, description)
    entity._execute_command = AsyncMock()

    await entity.async_press()

    entity._execute_command.assert_awaited_once_with(CleanMower(CleanAction.STOP))
    # Ending a job is not a leaving-the-dock command, same as pause.
    controller.start_polling.assert_not_called()


def test_mower_command_buttons_are_built_per_class() -> None:
    from unittest.mock import MagicMock

    from deebot_client.capabilities import DeviceType

    from custom_components.ecovacs_mower.button import _mower_command_entities

    def mower(class_: str) -> MagicMock:
        device = MagicMock()
        device.device_info = {"did": f"did-{class_}", "class": class_}
        device.capabilities.device_type = DeviceType.MOWER
        return device

    vacuum = MagicMock()
    vacuum.device_info = {"did": "did-vac", "class": "yna5xi"}
    vacuum.capabilities.device_type = DeviceType.VACUUM

    controller = MagicMock()
    controller.devices = [mower("77atlz"), mower("2px96q"), vacuum]

    built = {
        (e._device.device_info["class"], e.entity_description.key)
        for e in _mower_command_entities(controller)
    }
    assert built == {
        ("77atlz", "mow_border"),
        ("77atlz", "end_task"),
        ("2px96q", "end_task"),
    }


def test_the_vacuum_command_button_is_app_start() -> None:
    import json
    from pathlib import Path

    from custom_components.ecovacs_mower.button import VACUUM_COMMAND_DESCRIPTIONS

    assert [d.key for d in VACUUM_COMMAND_DESCRIPTIONS] == ["app_start"]
    root = Path(__file__).parent.parent / "custom_components" / "ecovacs_mower"
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    icons = json.loads((root / "icons.json").read_text(encoding="utf-8"))
    for description in VACUUM_COMMAND_DESCRIPTIONS:
        assert description.translation_key in strings["entity"]["button"]
        assert description.translation_key in icons["entity"]["button"]


async def test_the_app_start_button_sends_the_home_card_start() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ecovacs_mower.button import (
        VACUUM_COMMAND_DESCRIPTIONS,
        EcovacsMowerCommandButtonEntity,
    )
    from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
        CleanV2AppStart,
    )

    device = MagicMock()
    device.device_info = {"did": "test-did", "class": "twunby"}
    controller = MagicMock()
    entity = EcovacsMowerCommandButtonEntity(
        device, controller, VACUUM_COMMAND_DESCRIPTIONS[0]
    )
    entity._execute_command = AsyncMock()

    await entity.async_press()

    (command,), _ = entity._execute_command.call_args
    assert isinstance(command, CleanV2AppStart)
    controller.start_polling.assert_not_called()
