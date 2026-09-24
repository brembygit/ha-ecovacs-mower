"""The vacuum entity for Ecovacs DEEBOT vacuums.

This platform is deliberately separate from ``lawn_mower``.  The controller
owns one authenticated MQTT connection for every device on the account, and
only the vacuums whose patched capabilities it verified become entities here —
see ``EcovacsController.vacuums`` and ``deebot_patch/vacuum.py``.

Cleaning by area (``VacuumEntityFeature.CLEAN_AREA``): the segments are the
rooms of every built map, grouped by the map's name, so each floor's rooms
stay together in the area mapping dialog. They come from the robot, not the
library: ``getCachedMapInfo`` for the maps, ``GetMapSetV2Rooms`` for each
map's rooms. A segment id is ``<map id>_<room id>``, because room ids repeat
across maps. A clean sends the rooms of one map in the app's ``freeClean``
shape (``CleanV2Rooms``), and only for the map the robot is on: switching
floors is left to the user, since a map switch from here could strand the
robot on the wrong floor.

``ecovacs_mower.clean_rooms`` (``async_clean_rooms``) cleans the same rooms by
their names in the Ecovacs app, for anyone who does not want to map them to
Home Assistant areas first. It ends in the same ``async_clean_segments``.
"""

from __future__ import annotations

import logging
from typing import Any, override

from deebot_client.capabilities import Capabilities
from deebot_client.commands.json.map import GetCachedMapInfo
from deebot_client.device import Device
from deebot_client.events import FanSpeedEvent, StateEvent
from deebot_client.events.map import CachedMapInfoEvent, Map
from deebot_client.models import CleanAction, State

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    StateVacuumEntityDescription,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsMowerConfigEntry
from .deebot_patch.vacuum_messages import (
    CleanV2Rooms,
    GetMapSetV2Rooms,
    VacuumRoomsEvent,
    rooms_for,
)
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


def segment_id(map_id: str, room_id: int) -> str:
    """The segment id of a room: room ids repeat across maps, map ids do not."""
    return f"{map_id}_{room_id}"


def parse_segment_id(value: str) -> tuple[str, int]:
    """Split a segment id back into map id and room id; ValueError if it is not one."""
    map_id, separator, room_id = value.rpartition("_")
    if not separator or not map_id:
        raise ValueError(value)
    return map_id, int(room_id)


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
        | VacuumEntityFeature.CLEAN_AREA
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

        async def on_maps(_event: CachedMapInfoEvent) -> None:
            # Every time the map list changes (EventBus drops an unchanged
            # one): a renamed or re-split map changes its rooms too.
            await self._async_refresh_rooms()

        async def on_rooms(_event: VacuumRoomsEvent) -> None:
            self._async_check_segments()

        self._subscribe(CachedMapInfoEvent, on_maps)
        self._subscribe(VacuumRoomsEvent, on_rooms)
        # twunby has no map capability, so subscribing requests nothing: ask
        # once here. The answer's CachedMapInfoEvent then fetches the rooms.
        self.hass.async_create_task(self._execute_command(GetCachedMapInfo()))

    def _built_maps(self) -> list[Map]:
        """The robot's built maps, the one it is on first."""
        event = self._device.events.get_last_event(CachedMapInfoEvent)
        if event is None:
            return []
        return sorted(
            (map_ for map_ in event.maps if map_.built),
            key=lambda map_: (not map_.using, map_.name, map_.id),
        )

    async def _async_refresh_rooms(self) -> None:
        """Ask for the rooms of every built map."""
        for map_ in self._built_maps():
            await self._execute_command(GetMapSetV2Rooms(map_.id))

    def _current_segments(self) -> dict[str, Segment] | None:
        """The segments, or None while a built map's rooms are still unknown."""
        maps = self._built_maps()
        rooms = rooms_for(self._device.events)
        if not maps or any(map_.id not in rooms for map_ in maps):
            return None
        return {
            segment.id: segment
            for map_ in maps
            for segment in (
                Segment(
                    id=segment_id(map_.id, room.id),
                    name=room.name,
                    group=map_.name or None,
                )
                for room in rooms[map_.id]
            )
        }

    @callback
    def _async_check_segments(self) -> None:
        """Raise HA's repair issue when the rooms differ from the mapped ones."""
        if (
            self.registry_entry is not None
            and (last_seen := self.last_seen_segments) is not None
            and (current := self._current_segments())
            and current != {segment.id: segment for segment in last_seen}
        ):
            _LOGGER.debug(
                "Vacuum rooms changed: last seen %s, now %s", last_seen, current
            )
            self.async_create_segments_issue()

    @override
    async def async_get_segments(self) -> list[Segment]:
        """The rooms of every built map, grouped by map."""
        if (segments := self._current_segments()) is None:
            await self._execute_command(GetCachedMapInfo())
            await self._async_refresh_rooms()
            segments = self._current_segments()
        if segments is None:
            raise HomeAssistantError(
                "The robot has not reported its maps and rooms yet; try again in a "
                "moment."
            )
        return list(segments.values())

    @override
    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean the given rooms, all on the map the robot is on."""
        try:
            parsed = [parse_segment_id(value) for value in segment_ids]
        except ValueError as ex:
            raise ServiceValidationError(f"Not a room of this vacuum: {ex}") from ex
        if not parsed:
            raise ServiceValidationError("No room to clean.")

        maps = {map_.id: map_ for map_ in self._built_maps()}
        map_ids = {map_id for map_id, _ in parsed}
        if len(map_ids) > 1:
            names = ", ".join(
                maps[map_id].name if map_id in maps else map_id for map_id in map_ids
            )
            raise ServiceValidationError(
                f"The rooms are on more than one floor ({names}); clean one floor "
                "at a time."
            )
        (map_id,) = map_ids
        if (target := maps.get(map_id)) is None:
            raise ServiceValidationError(
                "These rooms belong to a map the robot no longer has; map the "
                "vacuum's rooms to areas again."
            )
        if not target.using:
            current = next((map_ for map_ in maps.values() if map_.using), None)
            raise ServiceValidationError(
                f"These rooms are on {target.name or 'another map'}, but the robot "
                f"is on {current.name if current else 'another map'}. Move it there "
                "and switch the map in the Ecovacs app first."
            )

        await self._execute_command(CleanV2Rooms([room_id for _, room_id in parsed]))

    async def async_clean_rooms(self, rooms: list[str]) -> None:
        """Clean rooms given by their names in the Ecovacs app, in that order.

        The ``ecovacs_mower.clean_rooms`` action: the app's room names, no
        Home Assistant areas in between. Names match without regard to case,
        on the map the robot is on; a name found only on another map gets the
        same refusal as a room clean there.
        """
        segments = await self.async_get_segments()
        maps = self._built_maps()
        using = next((map_.id for map_ in maps if map_.using), None)

        def lookup(map_id: str | None) -> dict[str, list[Segment]]:
            found: dict[str, list[Segment]] = {}
            for segment in segments:
                if parse_segment_id(segment.id)[0] == map_id:
                    found.setdefault(segment.name.casefold(), []).append(segment)
            return found

        on_this_map = lookup(using)
        segment_ids: list[str] = []
        for name in rooms:
            key = name.strip().casefold()
            matches = on_this_map.get(key)
            if matches is None:
                elsewhere = [s for s in segments if s.name.casefold() == key]
                if elsewhere:
                    # Refused by async_clean_segments, with the floor's name.
                    matches = elsewhere[:1]
                else:
                    known = ", ".join(sorted({s.name for s in segments}))
                    raise ServiceValidationError(
                        f"No room named {name!r} on the robot. Rooms: {known}."
                    )
            if len(matches) > 1:
                raise ServiceValidationError(
                    f"More than one room is named {name!r}; rename one in the app."
                )
            if matches[0].id not in segment_ids:
                segment_ids.append(matches[0].id)

        await self.async_clean_segments(segment_ids)

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
