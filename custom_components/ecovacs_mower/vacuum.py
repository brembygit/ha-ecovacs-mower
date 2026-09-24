"""The vacuum entity for Ecovacs DEEBOT vacuums.

This platform is deliberately separate from ``lawn_mower``.  The controller
owns one authenticated MQTT connection for every device on the account, and
only the vacuums whose patched capabilities it verified become entities here —
see ``EcovacsController.vacuums`` and ``deebot_patch/vacuum.py``.
"""

from __future__ import annotations

import logging
from typing import Any, override

from deebot_client.capabilities import Capabilities
from deebot_client.device import Device
from deebot_client.events import FanSpeedEvent, StateEvent
from deebot_client.models import CleanAction, State

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    StateVacuumEntityDescription,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsMowerConfigEntry
from .entity import EcovacsEntity
from .util import get_name_key

_LOGGER = logging.getLogger(__name__)

_STATE_TO_VACUUM_STATE = {
    State.IDLE: VacuumActivity.IDLE,
    State.CLEANING: VacuumActivity.CLEANING,
    State.RETURNING: VacuumActivity.RETURNING,
    State.DOCKED: VacuumActivity.DOCKED,
    State.ERROR: VacuumActivity.ERROR,
    State.PAUSED: VacuumActivity.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsMowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add vacuum entities for the verified vacuums only."""
    vacuums = [EcovacsVacuum(device) for device in config_entry.runtime_data.vacuums]
    _LOGGER.debug("Adding Ecovacs vacuums: %s", vacuums)
    async_add_entities(vacuums)


class EcovacsVacuum(EcovacsEntity[Capabilities], StateVacuumEntity):
    """An Ecovacs DEEBOT vacuum."""

    # name=None makes this the device's main entity, named after the device. It
    # only holds while strings.json gives the key no "name": a translated name
    # takes precedence over name=None and would turn "DEEBOT T90 PRO OMNI" into
    # "DEEBOT T90 PRO OMNI Vacuum". The key stays for the fan speed states.
    entity_description = StateVacuumEntityDescription(
        key="vacuum", translation_key="vacuum", name=None
    )
    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.START
    )

    def __init__(self, device: Device) -> None:
        """Initialize the vacuum."""
        super().__init__(device, device.capabilities)
        if fan_speed := self._capability.fan_speed:
            self._attr_supported_features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = [
                get_name_key(level) for level in fan_speed.types
            ]

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to vacuum state events."""
        await super().async_added_to_hass()

        async def on_status(event: StateEvent) -> None:
            activity = _STATE_TO_VACUUM_STATE.get(event.state)
            if activity is None:
                _LOGGER.warning("Unhandled vacuum state from device: %s", event.state)
                return
            self._attr_activity = activity
            self.async_write_ha_state()

        self._subscribe(self._capability.state.event, on_status)

        if self._capability.fan_speed:

            async def on_fan_speed(event: FanSpeedEvent) -> None:
                self._attr_fan_speed = get_name_key(event.speed)
                self.async_write_ha_state()

            self._subscribe(self._capability.fan_speed.event, on_fan_speed)

    async def _clean_command(self, action: CleanAction) -> None:
        """Send a capability-provided vacuum cleaning command."""
        await self._execute_command(self._capability.clean.action.command(action))

    @override
    async def async_start(self) -> None:
        """Start cleaning."""
        await self._clean_command(CleanAction.START)

    @override
    async def async_pause(self) -> None:
        """Pause cleaning."""
        await self._clean_command(CleanAction.PAUSE)

    @override
    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        await self._clean_command(CleanAction.STOP)

    @override
    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return to the dock."""
        await self._execute_command(self._capability.charge.execute())

    @override
    async def async_locate(self, **kwargs: Any) -> None:
        """Play the device locating sound."""
        await self._execute_command(self._capability.play_sound.execute())

    @override
    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the vacuum fan speed."""
        if self._capability.fan_speed:
            await self._execute_command(self._capability.fan_speed.set(fan_speed))
