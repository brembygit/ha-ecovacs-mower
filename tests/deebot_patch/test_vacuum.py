"""The seeding of the vacuum capabilities, kept apart from the mowers'."""

from dataclasses import replace

import pytest
from deebot_client.capabilities import DeviceType
from deebot_client.commands.json.auto_empty import SetAutoEmpty
from deebot_client.commands.json.clean import Clean, CleanV2
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.events import WorkMode
from deebot_client.hardware import _DEVICES, get_static_device_info
from deebot_client.models import CleanAction

from custom_components.ecovacs_mower.deebot_patch import PatchContractError
from custom_components.ecovacs_mower.deebot_patch.commands import CleanMower
from custom_components.ecovacs_mower.deebot_patch.hardware import (
    SUPPORTED_CLASSES,
    patch_device_info,
)
from custom_components.ecovacs_mower.deebot_patch.vacuum import (
    APP_WORK_MODES,
    SUPPORTED_VACUUM_CLASSES,
    patch_vacuum_device_info,
    verify_vacuum_capabilities,
)
from custom_components.ecovacs_mower.deebot_patch.vacuum_messages import (
    GetAutoEmptyVacuum,
    GetWorkStateVacuum,
    SetAutoEmptyVacuum,
)

T90 = "twunby"
# The T5PRO vacuum: a valid class in the library, outside the allowlist.
T5PRO = "npwtuz"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Empty the library's cache between tests."""
    classes = (*SUPPORTED_VACUUM_CLASSES, *SUPPORTED_CLASSES, T5PRO)
    for class_ in classes:
        _DEVICES.pop(class_, None)
    yield
    for class_ in classes:
        _DEVICES.pop(class_, None)


def test_the_t90_is_the_only_patched_vacuum() -> None:
    """Locks the set. Widening it must be a decision, not an accident."""
    assert set(SUPPORTED_VACUUM_CLASSES) == {T90}


def test_no_class_is_both_a_mower_and_a_vacuum() -> None:
    assert not set(SUPPORTED_VACUUM_CLASSES) & set(SUPPORTED_CLASSES)


async def test_unpatched_library_uses_the_legacy_clean() -> None:
    # Documents what the patch is for. If this starts failing, upstream has
    # switched the T90 to clean_V2 and the patch can go.
    info = await get_static_device_info(T90)
    assert info.capabilities.device_type is DeviceType.VACUUM
    assert info.capabilities.clean.action.command is Clean


async def test_patch_swaps_in_clean_v2() -> None:
    await patch_vacuum_device_info(T90)
    info = await get_static_device_info(T90)
    assert info.capabilities.clean.action.command is CleanV2


async def test_patch_starts_a_clean_the_way_the_app_does() -> None:
    # The shape the Ecovacs app sent a T90, acknowledged with code 0.
    await patch_vacuum_device_info(T90)
    info = await get_static_device_info(T90)
    command = info.capabilities.clean.action.command(CleanAction.START)
    assert command.NAME == "clean_V2"
    assert command._args == {"act": "start", "content": {"type": "auto"}}


def test_every_work_mode_override_belongs_to_a_patched_class() -> None:
    assert set(APP_WORK_MODES) <= set(SUPPORTED_VACUUM_CLASSES)


async def test_unpatched_library_offers_mop_only() -> None:
    # Documents what the second half of the patch is for.
    info = await get_static_device_info(T90)
    assert WorkMode.MOP in info.capabilities.clean.work_mode.types


async def test_patch_offers_only_the_apps_work_modes() -> None:
    unpatched = (await get_static_device_info(T90)).capabilities.clean.work_mode
    _DEVICES.pop(T90, None)
    await patch_vacuum_device_info(T90)
    patched = (await get_static_device_info(T90)).capabilities.clean.work_mode

    assert set(patched.types) == {
        WorkMode.VACUUM_AND_MOP,
        WorkMode.VACUUM,
        WorkMode.MOP_AFTER_VACUUM,
    }
    # The library's order is kept, and only the list changes.
    assert list(patched.types) == [m for m in unpatched.types if m in patched.types]
    assert patched.event is unpatched.event
    assert patched.set is unpatched.set
    assert patched.get == unpatched.get


async def test_verify_raises_when_mop_only_is_offered() -> None:
    await patch_vacuum_device_info(T90)
    capabilities = (await get_static_device_info(T90)).capabilities
    work_mode = replace(
        capabilities.clean.work_mode,
        types=(*capabilities.clean.work_mode.types, WorkMode.MOP),
    )
    widened = replace(
        capabilities, clean=replace(capabilities.clean, work_mode=work_mode)
    )

    with pytest.raises(PatchContractError, match="MOP"):
        verify_vacuum_capabilities(widened, T90)


async def test_patch_preserves_untouched_capabilities() -> None:
    unpatched = await get_static_device_info(T90)
    _DEVICES.pop(T90, None)
    await patch_vacuum_device_info(T90)
    patched = await get_static_device_info(T90)

    before, after = unpatched.capabilities, patched.capabilities
    assert after.clean.action.area is before.clean.action.area
    assert after.fan_speed == before.fan_speed
    assert after.station.action == before.station.action
    assert after.station.auto_empty.types == before.station.auto_empty.types
    assert after.station.auto_empty.event is before.station.auto_empty.event
    # The state reads keep their order; only GetWorkState's class changes.
    assert [c.NAME for c in after.state.get] == [c.NAME for c in before.state.get]


async def test_patch_swaps_in_the_work_state_reads() -> None:
    await patch_vacuum_device_info(T90)
    capabilities = (await get_static_device_info(T90)).capabilities

    for capability in (capabilities.state, capabilities.station.state):
        types = [type(command) for command in capability.get]
        assert GetWorkStateVacuum in types
        assert GetWorkState not in types


async def test_patch_swaps_in_the_three_field_auto_empty() -> None:
    await patch_vacuum_device_info(T90)
    auto_empty = (await get_static_device_info(T90)).capabilities.station.auto_empty

    assert auto_empty.set is SetAutoEmptyVacuum
    assert [type(command) for command in auto_empty.get] == [GetAutoEmptyVacuum]


async def test_verify_raises_on_the_librarys_work_state_read() -> None:
    await patch_vacuum_device_info(T90)
    capabilities = (await get_static_device_info(T90)).capabilities
    unpatched_state = replace(capabilities.state, get=[GetWorkState()])

    with pytest.raises(PatchContractError, match="GetWorkState"):
        verify_vacuum_capabilities(
            replace(capabilities, state=unpatched_state), T90
        )


async def test_verify_raises_on_the_librarys_auto_empty_setter() -> None:
    await patch_vacuum_device_info(T90)
    capabilities = (await get_static_device_info(T90)).capabilities
    station = replace(
        capabilities.station,
        auto_empty=replace(capabilities.station.auto_empty, set=SetAutoEmpty),
    )

    with pytest.raises(PatchContractError, match="SetAutoEmpty"):
        verify_vacuum_capabilities(replace(capabilities, station=station), T90)


async def test_patch_is_idempotent() -> None:
    await patch_vacuum_device_info(T90)
    first = await get_static_device_info(T90)
    await patch_vacuum_device_info(T90)
    assert await get_static_device_info(T90) is first


async def test_unlisted_vacuum_is_not_patched() -> None:
    await patch_vacuum_device_info(T5PRO)
    assert T5PRO not in _DEVICES


async def test_a_mower_class_is_not_touched() -> None:
    await patch_device_info("2i0fns")
    await patch_vacuum_device_info("2i0fns")
    info = await get_static_device_info("2i0fns")
    assert info.capabilities.clean.action.command is CleanMower


async def test_patch_leaves_a_misshapen_definition_alone() -> None:
    """A library whose capabilities changed shape must not crash setup.

    The cache is left as it was; verification is what reports it.
    """
    info = await get_static_device_info(T90)
    misshapen = replace(info, capabilities=object())
    _DEVICES[T90] = misshapen

    await patch_vacuum_device_info(T90)

    assert _DEVICES[T90] is misshapen


async def test_verify_passes_after_patching() -> None:
    await patch_vacuum_device_info(T90)
    info = await get_static_device_info(T90)
    verify_vacuum_capabilities(info.capabilities, T90)


async def test_verify_raises_on_unpatched_object() -> None:
    info = await get_static_device_info(T90)
    with pytest.raises(PatchContractError, match="CleanV2"):
        verify_vacuum_capabilities(info.capabilities, T90)


async def test_verify_raises_for_a_device_that_is_not_a_vacuum() -> None:
    await patch_vacuum_device_info(T90)
    info = await get_static_device_info(T90)
    mower_shaped = replace(info.capabilities, device_type=DeviceType.MOWER)
    with pytest.raises(PatchContractError, match="not as a vacuum"):
        verify_vacuum_capabilities(mower_shaped, T90)


async def test_get_devices_path_produces_patched_capabilities() -> None:
    """The device built by get_devices() carries CleanV2, not just the cache."""
    from unittest.mock import AsyncMock

    from deebot_client.api_client import ApiClient

    authenticator = AsyncMock()
    authenticator.post_authenticated.return_value = {
        "devices": [{"did": "abc123", "class": T90, "company": "eco-ng"}]
    }

    await patch_vacuum_device_info(T90)  # before get_devices, as in the controller
    devices = await ApiClient(authenticator).get_devices()

    assert len(devices.mqtt) == 1
    verify_vacuum_capabilities(devices.mqtt[0].static.capabilities, T90)


async def test_patch_must_run_before_get_devices() -> None:
    from deebot_client.models import DeviceInfo

    stale = await get_static_device_info(T90)
    device_info = DeviceInfo({"class": T90, "did": "x"}, stale)
    await patch_vacuum_device_info(T90)  # too late for device_info

    with pytest.raises(PatchContractError):
        verify_vacuum_capabilities(device_info.static.capabilities, T90)
