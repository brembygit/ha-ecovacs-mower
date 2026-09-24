"""Select entities for Ecovacs DEEBOT vacuums."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilitySetTypes
from deebot_client.device import Device
from deebot_client.events import WorkModeEvent, auto_empty
from deebot_client.events.base import Event

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsMowerConfigEntry
from .entity import EcovacsCapabilityEntityDescription, EcovacsDescriptionEntity
from .util import get_name_key


@dataclass(kw_only=True, frozen=True)
class EcovacsSelectEntityDescription[EventT: Event](
    SelectEntityDescription,
    EcovacsCapabilityEntityDescription,
):
    """Describe a capability-backed DEEBOT select."""

    current_option_fn: Callable[[EventT], str | None]
    options_fn: Callable[[CapabilitySetTypes], list[str]]
    set_option_fn: Callable[[CapabilitySetTypes, str], Any] = (
        lambda capability, option: capability.set(option)
    )


ENTITY_DESCRIPTIONS: tuple[EcovacsSelectEntityDescription, ...] = (
    EcovacsSelectEntityDescription[WorkModeEvent](
        capability_fn=lambda caps: caps.clean.work_mode,
        current_option_fn=lambda event: get_name_key(event.mode),
        options_fn=lambda capability: [
            get_name_key(mode) for mode in capability.types
        ],
        key="work_mode",
        translation_key="work_mode",
        entity_category=EntityCategory.CONFIG,
    ),
    EcovacsSelectEntityDescription[auto_empty.AutoEmptyEvent](
        capability_fn=lambda caps: caps.station.auto_empty if caps.station else None,
        current_option_fn=lambda event: (
            get_name_key(event.frequency) if event.frequency else None
        ),
        options_fn=lambda capability: [
            get_name_key(frequency) for frequency in capability.types
        ],
        set_option_fn=lambda capability, option: capability.set(None, option),
        key="auto_empty",
        translation_key="auto_empty",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsMowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add selects for the verified vacuums only."""
    entities = [
        EcovacsSelect(device, capability, description)
        for device in config_entry.runtime_data.vacuums
        for description in ENTITY_DESCRIPTIONS
        if (capability := description.capability_fn(device.capabilities))
    ]
    if entities:
        async_add_entities(entities)


class EcovacsSelect[EventT: Event](
    EcovacsDescriptionEntity[CapabilitySetTypes[EventT, [str], str]],
    SelectEntity,
):
    """A capability-backed DEEBOT select."""

    _attr_current_option: str | None = None
    entity_description: EcovacsSelectEntityDescription

    def __init__(
        self,
        device: Device,
        capability: CapabilitySetTypes[EventT, [str], str],
        entity_description: EcovacsSelectEntityDescription,
        **kwargs: Any,
    ) -> None:
        """Initialize the select."""
        super().__init__(device, capability, entity_description, **kwargs)
        self._attr_options = entity_description.options_fn(capability)

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to setting updates."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            if (option := self.entity_description.current_option_fn(event)) is not None:
                self._attr_current_option = option
                self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the setting."""
        await self._execute_command(
            self.entity_description.set_option_fn(self._capability, option)
        )
