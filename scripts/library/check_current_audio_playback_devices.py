"""
Skill: check_current_audio_playback_devices
Inspects audio output devices and default system output using standard sounddevice API (zero ctypes).
"""

from typing import Any, Dict, List, Optional
import sounddevice as sd


def get_audio_devices() -> Dict[str, Any]:
    """Retrieves all active output audio playback devices and system default."""
    all_devices = sd.query_devices()
    default_indices = sd.default.device
    default_out_idx = default_indices[1] if default_indices and len(default_indices) > 1 else None

    devices: List[Dict[str, Any]] = []
    default_device_id = None

    for idx, dev in enumerate(all_devices):
        if dev.get("max_output_channels", 0) > 0:
            is_default = (idx == default_out_idx)
            dev_id = f"device_{idx}"
            if is_default:
                default_device_id = dev_id
            devices.append({
                "name": dev.get("name", f"Audio Output {idx}"),
                "status": "Active",
                "is_default": is_default,
                "device_id": dev_id,
                "channels": dev.get("max_output_channels", 2),
                "samplerate": dev.get("default_samplerate", 44100.0)
            })

    return {
        "default_device_id": default_device_id,
        "devices": devices,
    }


def main():
    audio_info = get_audio_devices()

    col_widths = {"Name": 40, "Status": 12, "Default": 9, "DeviceID": 45}
    header = f"{'Name':<{col_widths['Name']}} {'Status':<{col_widths['Status']}} {'Default':<{col_widths['Default']}} {'DeviceID'}"
    print(header)
    print("-" * len(header))

    for dev in audio_info["devices"]:
        is_def_str = "Yes" if dev["is_default"] else "No"
        name = (dev["name"][:37] + "...") if len(dev["name"]) > 40 else dev["name"]
        print(
            f"{name:<{col_widths['Name']}} {dev['status']:<{col_widths['Status']}} {is_def_str:<{col_widths['Default']}} {dev['device_id']}"
        )


if __name__ == "__main__":
    main()