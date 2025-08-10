"""Support for ThinQ device selects."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LGEDevice
from .const import DOMAIN, LGE_DEVICES, LGE_DISCOVERY_NEW
from .wideq import WM_DEVICE_TYPES, DeviceType, MicroWaveFeatures
from .wideq.devices.ac import ACAutoDryMode 

_LOGGER = logging.getLogger(__name__)


@dataclass
class ThinQSelectRequiredKeysMixin:
    """Mixin for required keys."""

    options_fn: Callable[[Any], list[str]]
    select_option_fn: Callable[[Any], Awaitable[None]]


@dataclass
class ThinQSelectEntityDescription(
    SelectEntityDescription, ThinQSelectRequiredKeysMixin
):
    """A class that describes ThinQ select entities."""

    available_fn: Callable[[Any], bool] | None = None
    value_fn: Callable[[Any], str] | None = None


WASH_DEV_SELECT: tuple[ThinQSelectEntityDescription, ...] = (
    ThinQSelectEntityDescription(
        key="course_selection",
        name="Course selection",
        icon="mdi:tune-vertical-variant",
        options_fn=lambda x: x.device.course_list,
        select_option_fn=lambda x, option: x.device.select_start_course(option),
        available_fn=lambda x: x.device.select_course_enabled,
        value_fn=lambda x: x.device.selected_course,
    ),
)
MICROWAVE_SELECT: tuple[ThinQSelectEntityDescription, ...] = (
    ThinQSelectEntityDescription(
        key=MicroWaveFeatures.DISPLAY_SCROLL_SPEED,
        name="Display scroll speed",
        icon="mdi:format-pilcrow-arrow-right",
        entity_category=EntityCategory.CONFIG,
        options_fn=lambda x: x.device.display_scroll_speeds,
        select_option_fn=lambda x, option: x.device.set_display_scroll_speed(option),
    ),
    ThinQSelectEntityDescription(
        key=MicroWaveFeatures.WEIGHT_UNIT,
        name="Weight unit",
        icon="mdi:weight",
        entity_category=EntityCategory.CONFIG,
        options_fn=lambda x: x.device.defrost_weight_units,
        select_option_fn=lambda x, option: x.device.set_defrost_weight_unit(option),
    ),
)

# === AC vertical wind step (1..6) ===
AC_VSTEP_SELECT: tuple[ThinQSelectEntityDescription, ...] = (
    ThinQSelectEntityDescription(
        key="ac_vertical_wind_step",                  # 임의 키 (state.device_features 안 써도 됨)
        name="Vertical Wind Step",
        icon="mdi:unfold-more-vertical",
        # 옵션은 기기에서 노출한 vertical_step_modes(IntEnum 리스트)를 문자열로 변환
        options_fn=lambda x: [str(int(m)) for m in (x.device.vertical_step_modes or [])],
        # 선택 시 1..6 정수로 변환해서 장치 API 호출 (async 함수여야 함)
        select_option_fn=lambda x, option: x.device.set_vertical_step_mode(int(option)),
        # 현재 선택 값: 상태에서 정수(1..6)를 문자열로
        value_fn=lambda x: (str(v) if isinstance((v := x.device.vertical_step_mode), int) and 1 <= v <= 6 else None),
        # 장치가 vStep을 노출할 때만 엔티티 생성
        available_fn=lambda x: bool(x.device.vertical_step_modes),
    ),
)


AUTO_DRY_LABEL = {
    ACAutoDryMode.OFF: "꺼짐",
    ACAutoDryMode.MIN_10: "10분",
    ACAutoDryMode.MIN_30: "30분",
    ACAutoDryMode.MIN_60: "60분",
    ACAutoDryMode.AI: "AI건조",
}
AUTO_DRY_FROM_LABEL = {v: k for k, v in AUTO_DRY_LABEL.items()}  # 역맵

AC_AUTODRY_SELECT: tuple[ThinQSelectEntityDescription, ...] = (
    ThinQSelectEntityDescription(
        key="ac_autodry_mode",  # 임의 키
        name="Auto Dry",
        icon="mdi:hair-dryer",
        # 옵션: 디바이스가 지원하는 enum 목록을 한글 라벨로 변환
        options_fn=lambda x: [AUTO_DRY_LABEL[m] for m in (x.device.auto_dry_modes or [])],
        # 선택: 라벨 -> enum 으로 변환해서 장치로 전달 (awaitable)
        select_option_fn=lambda x, option: x.device.set_auto_dry_mode(AUTO_DRY_FROM_LABEL[option]),
        # 현재 선택: state.auto_dry_mode가 'AI' 또는 '@AIAUTODRY' 등 무엇을 주더라도 라벨로 환산
        value_fn=lambda x: (
            AUTO_DRY_LABEL.get(x.device.auto_dry_mode)
            if x.device.auto_dry_mode in AUTO_DRY_LABEL
            else None
        ),
        # 지원 시에만 엔티티 생성
        available_fn=lambda x: bool(x.device.auto_dry_modes),
    ),
)

SELECT_ENTITIES = {
    DeviceType.MICROWAVE: MICROWAVE_SELECT,
    DeviceType.AC: AC_VSTEP_SELECT,  # ← 추가
    **{dev_type: WASH_DEV_SELECT for dev_type in WM_DEVICE_TYPES},
}


def _select_exist(
    lge_device: LGEDevice, select_desc: ThinQSelectEntityDescription
) -> bool:
    """Check if a select exist for device."""
    if select_desc.value_fn is not None:
        return True

    feature = select_desc.key
    if feature in lge_device.available_features:
        return True

    return False


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the LGE selects."""
    entry_config = hass.data[DOMAIN]
    lge_cfg_devices = entry_config.get(LGE_DEVICES)

    _LOGGER.debug("Starting LGE ThinQ select setup...")

    @callback
    def _async_discover_device(lge_devices: dict) -> None:
        """Add entities for a discovered ThinQ device."""

        if not lge_devices:
            return

        lge_select = [
            LGESelect(lge_device, select_desc)
            for dev_type, select_descs in SELECT_ENTITIES.items()
            for select_desc in select_descs
            for lge_device in lge_devices.get(dev_type, [])
            if _select_exist(lge_device, select_desc)
        ]

        async_add_entities(lge_select)

    _async_discover_device(lge_cfg_devices)

    entry.async_on_unload(
        async_dispatcher_connect(hass, LGE_DISCOVERY_NEW, _async_discover_device)
    )


class LGESelect(CoordinatorEntity, SelectEntity):
    """Class to control selects for LGE device"""

    entity_description: ThinQSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        api: LGEDevice,
        description: ThinQSelectEntityDescription,
    ):
        """Initialize the select."""
        super().__init__(api.coordinator)
        self._api = api
        self.entity_description = description
        self._attr_unique_id = f"{api.unique_id}-{description.key}-select"
        self._attr_device_info = api.device_info
        self._attr_options = self.entity_description.options_fn(self._api)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.entity_description.select_option_fn(self._api, option)
        self._api.async_set_updated()

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(self._api)

        if self._api.state:
            feature = self.entity_description.key
            return self._api.state.device_features.get(feature)

        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        is_avail = True
        if self.entity_description.available_fn is not None:
            is_avail = self.entity_description.available_fn(self._api)
        return self._api.available and is_avail
