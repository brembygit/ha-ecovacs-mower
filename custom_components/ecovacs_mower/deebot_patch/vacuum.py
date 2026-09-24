"""Seeds deebot-client's device cache with corrected vacuum capabilities.

Deliberately separate from ``hardware.py``: the mower patch and this one share
the mechanism — let the library build its own definition, swap the broken part,
put the result back — but nothing else, so a vacuum class can never pick up a
mower command or the other way around.
"""

from __future__ import annotations

from dataclasses import replace
import logging

from deebot_client.capabilities import Capabilities, DeviceType
from deebot_client.commands.json.auto_empty import GetAutoEmpty
from deebot_client.commands.json.clean import CleanV2
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.events import WorkMode
from deebot_client.hardware import _DEVICES, get_static_device_info

from . import PatchContractError
from .vacuum_messages import (
    GetAutoEmptyVacuum,
    GetWorkStateVacuum,
    SetAutoEmptyVacuum,
    register_vacuum_bus,
)

__all__ = [
    "APP_WORK_MODES",
    "SUPPORTED_VACUUM_CLASSES",
    "patch_vacuum_device_info",
    "register_vacuum_bus",
    "verify_vacuum_capabilities",
]

_LOGGER = logging.getLogger(__name__)

# Vacuum classes this integration patches, and how each one was confirmed. This
# tuple is the only allowlist: the vacuum and select platforms build entities
# from the controller's verified vacuums, and a class reaches those only by
# being listed here and passing verify_vacuum_capabilities().
#   twunby — DEEBOT T90 PRO OMNI, firmware 1.103.0. The library's definition
#            starts a clean with the legacy clean command; the Ecovacs app
#            only ever sends clean_V2, and nobody has seen this class answer
#            clean. The app's start matches CleanV2 exactly. Its pause, resume
#            and stop carry no "content", where CleanV2 adds one; the firmware
#            answers CleanV2's shape too (code 0, state pushed), seen live.
SUPPORTED_VACUUM_CLASSES = ("twunby",)

# The work modes the Ecovacs app offers, per class. The library lists every
# mode its enum knows; a mode the app never offers is one nobody has seen the
# robot accept, so it is not offered here either. A class without an entry
# keeps the library's list.
#   twunby — the app offers 0 vacuum and mop, 1 vacuum only and 3 mop after
#            vacuum, each confirmed by read-back. 2 (mop only) is not offered.
APP_WORK_MODES: dict[str, frozenset[WorkMode]] = {
    "twunby": frozenset(
        {WorkMode.VACUUM_AND_MOP, WorkMode.VACUUM, WorkMode.MOP_AFTER_VACUUM}
    ),
}


def _with_vacuum_work_state[CapabilityT](capability: CapabilityT) -> CapabilityT:
    """*capability* with each GetWorkState swapped for GetWorkStateVacuum.

    Returned as is when there is nothing to swap, so the caller can tell by
    identity whether anything changed. Exact type comparison, not
    isinstance: GetWorkStateVacuum is a GetWorkState, and isinstance would
    swap the already-patched command again on every call.
    """
    if capability is None or not any(
        type(command) is GetWorkState for command in capability.get
    ):
        return capability
    return replace(
        capability,
        get=[
            GetWorkStateVacuum() if type(command) is GetWorkState else command
            for command in capability.get
        ],
    )


async def patch_vacuum_device_info(class_: str) -> None:
    """Swap in the vacuum's corrected commands. Idempotent.

    CleanV2 for the clean action, the app's work modes, the work-state reads
    that report a robot idle away from its dock, and the three-field
    auto-empty setter — see vacuum_messages.py for the last two.

    Never raises for a library whose capabilities no longer look as expected:
    the cache is then left as it was, and verify_vacuum_capabilities() — which
    checks the object the device actually got — is what reports it.
    """
    if class_ not in SUPPORTED_VACUUM_CLASSES:
        return

    base = await get_static_device_info(class_)
    if base is None:
        return

    try:
        capabilities = base.capabilities
        clean = capabilities.clean
        action = clean.action
        if action.command is not CleanV2:
            action = replace(action, command=CleanV2)

        work_mode = clean.work_mode
        if work_mode is not None and (modes := APP_WORK_MODES.get(class_)):
            # Filtered rather than rebuilt, so the library's order is kept.
            types = tuple(mode for mode in work_mode.types if mode in modes)
            if types != work_mode.types:
                work_mode = replace(work_mode, types=types)

        state = _with_vacuum_work_state(capabilities.state)

        station = capabilities.station
        if station is not None:
            station_state = _with_vacuum_work_state(station.state)
            auto_empty = station.auto_empty
            if auto_empty is not None and auto_empty.set is not SetAutoEmptyVacuum:
                auto_empty = replace(
                    auto_empty,
                    get=[
                        GetAutoEmptyVacuum()
                        if type(command) is GetAutoEmpty
                        else command
                        for command in auto_empty.get
                    ],
                    set=SetAutoEmptyVacuum,
                )
            if (
                station_state is not station.state
                or auto_empty is not station.auto_empty
            ):
                station = replace(
                    station, state=station_state, auto_empty=auto_empty
                )

        if (
            action is clean.action
            and work_mode is clean.work_mode
            and state is capabilities.state
            and station is capabilities.station
        ):
            return
        patched = replace(
            base,
            capabilities=replace(
                capabilities,
                clean=replace(clean, action=action, work_mode=work_mode),
                state=state,
                station=station,
            ),
        )
    except (AttributeError, TypeError):
        _LOGGER.debug("Could not patch vacuum %s", class_, exc_info=True)
        return

    _DEVICES[class_] = patched
    _LOGGER.debug("Patched vacuum capabilities for %s", class_)


def verify_vacuum_capabilities(capabilities: Capabilities, class_: str) -> None:
    """Confirm that a vacuum got the patched capabilities.

    Raises PatchContractError like verify_capabilities() does for a mower, but
    the controller treats it differently: a vacuum that fails this check loses
    its vacuum entities, it does not take the config entry down with it.
    """
    if capabilities.device_type is not DeviceType.VACUUM:
        raise PatchContractError(
            f"vacuum {class_} was built as {capabilities.device_type}, "
            "not as a vacuum"
        )
    if capabilities.clean.action.command is not CleanV2:
        raise PatchContractError(
            f"vacuum {class_} was built with "
            f"{capabilities.clean.action.command.__name__} instead of CleanV2 — "
            "the patch ran too late or deebot-client changed shape"
        )
    work_mode = capabilities.clean.work_mode
    if (
        work_mode is not None
        and (modes := APP_WORK_MODES.get(class_))
        and not set(work_mode.types) <= modes
    ):
        raise PatchContractError(
            f"vacuum {class_} offers work modes the app does not: "
            f"{sorted(m.name for m in set(work_mode.types) - modes)}"
        )
    station = capabilities.station
    for where, capability in (
        ("state", capabilities.state),
        ("station state", station.state if station else None),
    ):
        if capability is not None and any(
            type(command) is GetWorkState for command in capability.get
        ):
            raise PatchContractError(
                f"vacuum {class_} reads its {where} with the library's "
                "GetWorkState, which never reports a robot idle away from "
                "its dock"
            )
    if (
        station is not None
        and station.auto_empty is not None
        and station.auto_empty.set is not SetAutoEmptyVacuum
    ):
        raise PatchContractError(
            f"vacuum {class_} sets auto-empty with "
            f"{getattr(station.auto_empty.set, '__name__', station.auto_empty.set)}"
            " instead of SetAutoEmptyVacuum, which the firmware refuses"
        )
