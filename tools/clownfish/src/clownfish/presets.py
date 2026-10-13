# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hardware and guest configuration presets for Clownfish fake emulators."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DevicePreset:
    """Immutable hardware and guest configuration preset for a simulated device."""

    name: str
    avd_name: str
    avd_id: str
    display_name: str
    model_id: str
    device_type: str = "default"
    api_level: int = 35
    cpu_arch: str = "x86_64"
    cpu_cores: int = 4
    ram_mb: int = 4096
    storage_bytes: int = 6 * 1024 * 1024 * 1024
    supports_play_store: bool = True
    manufacturer: str = "Google"
    brand: str = "google"
    emulator_version: str = "35.4.9.0"
    emulator_build: str = "12345678"
    server_version: str = "2.0.0"
    server_implementation: str = "clownfish-grpc-v2"

    # Primary display geometry
    width: int = 1080
    height: int = 2400
    dpi: int = 420
    circular: bool = False
    refresh_rate_hz: float = 60.0
    corner_radius_normalized: float = 0.05
    user_configurable_displays: int = 3
    max_displays: int = 11

    # Foldable / Rollable configuration
    is_foldable: bool = False
    folded_width: int = 1080
    folded_height: int = 2092
    folded_x_offset: int = 0
    folded_y_offset: int = 0
    fold_axis: str = "vertical"  # "vertical" or "horizontal"
    num_hinges: int = 0
    num_rollables: int = 0
    initial_hinge_angles: tuple[float, ...] = field(default_factory=tuple)
    hinge_ranges: str = ""
    hinge_defaults: str = ""
    posture_list: str = ""
    hinge_angles_posture_definitions: str = ""

    # Resizable / XR / Wear features
    resizable_config: str = ""
    is_xr: bool = False
    has_rotary_input: bool = False
    has_telephony: bool = True
    has_heart_rate: bool = False
    has_wrist_tilt: bool = False


DEVICE_PRESETS: dict[str, DevicePreset] = {
    "phone": DevicePreset(
        name="phone",
        avd_name="Pixel_8_Pro_API_35",
        avd_id="pixel_8_pro",
        display_name="Pixel 8 Pro API 35",
        model_id="Pixel 8 Pro",
        width=1080,
        height=2400,
        dpi=420,
    ),
    "foldable": DevicePreset(
        name="foldable",
        avd_name="Pixel_Fold_API_35",
        avd_id="pixel_fold",
        display_name="Pixel Fold API 35",
        model_id="Pixel Fold",
        width=2208,
        height=1840,
        dpi=420,
        is_foldable=True,
        folded_width=1080,
        folded_height=2092,
        folded_x_offset=0,
        folded_y_offset=0,
        fold_axis="vertical",
        num_hinges=1,
        initial_hinge_angles=(180.0,),
        hinge_ranges="0-180",
        hinge_defaults="180",
        posture_list="1, 2, 3",
        hinge_angles_posture_definitions="0-30, 30-150, 150-180",
    ),
    "tablet": DevicePreset(
        name="tablet",
        avd_name="Pixel_Tablet_API_35",
        avd_id="pixel_tablet",
        display_name="Pixel Tablet API 35",
        model_id="Pixel Tablet",
        width=2560,
        height=1600,
        dpi=320,
        has_telephony=False,
        resizable_config=(
            "phone-0-1080-2400-420,"
            "foldable-1-2208-1840-420,"
            "tablet-2-2560-1600-320,"
            "desktop-3-1920-1080-160"
        ),
    ),
    "xr": DevicePreset(
        name="xr",
        avd_name="Android_XR_API_35",
        avd_id="xr_headset",
        display_name="Android XR Headset API 35",
        model_id="Android XR",
        device_type="xr-headset",
        width=1920,
        height=1080,
        dpi=320,
        is_xr=True,
        has_telephony=False,
    ),
    "wear": DevicePreset(
        name="wear",
        avd_name="Pixel_Watch_API_35",
        avd_id="wear_round",
        display_name="Pixel Watch API 35",
        model_id="Pixel Watch",
        device_type="android-wear",
        width=454,
        height=454,
        dpi=320,
        circular=True,
        corner_radius_normalized=0.5,
        has_rotary_input=True,
        has_heart_rate=True,
        has_wrist_tilt=True,
    ),
}


def get_preset(name_or_preset: str | DevicePreset = "phone") -> DevicePreset:
    """Returns a DevicePreset by name or passes through an existing DevicePreset."""
    if isinstance(name_or_preset, DevicePreset):
        return name_or_preset
    key = name_or_preset.strip().lower()
    if key not in DEVICE_PRESETS:
        valid = ", ".join(sorted(DEVICE_PRESETS))
        raise ValueError(f"Unknown preset '{name_or_preset}'. Expected one of: {valid}")
    return DEVICE_PRESETS[key]
