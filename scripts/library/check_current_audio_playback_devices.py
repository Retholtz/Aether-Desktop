import ctypes
from ctypes import (
    HRESULT,
    POINTER,
    Structure,
    byref,
    c_ubyte,
    c_uint,
    c_ulong,
    c_ushort,
    c_void_p,
    cast,
)
from ctypes.wintypes import DWORD, LPWSTR
from typing import Any, Dict, List, Optional

ole32 = ctypes.oledll.ole32

# Core Audio / COM Constants
CLSCTX_INPROC_SERVER = 0x1
STGM_READ = 0x00000000
VT_LPWSTR = 31

# EDataFlow
DATA_FLOW_RENDER = 0
DATA_FLOW_CAPTURE = 1
DATA_FLOW_ALL = 2

# ERole
ROLE_CONSOLE = 0
ROLE_MULTIMEDIA = 1
ROLE_COMMUNICATIONS = 2

# Device State Masks
DEVICE_STATE_ACTIVE = 0x00000001
DEVICE_STATE_DISABLED = 0x00000002
DEVICE_STATE_NOTPRESENT = 0x00000004
DEVICE_STATE_UNPLUGGED = 0x00000008
DEVICE_STATEMASK_ALL = 0x0000000F

STATE_MAP = {
    DEVICE_STATE_ACTIVE: "Active",
    DEVICE_STATE_DISABLED: "Disabled",
    DEVICE_STATE_NOTPRESENT: "NotPresent",
    DEVICE_STATE_UNPLUGGED: "Unplugged",
}


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]


class PROPERTYKEY(Structure):
    _fields_ = [
        ("fmtid", GUID),
        ("pid", DWORD),
    ]


class PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("pwszVal", LPWSTR),
        ("padding", c_ubyte * 8),
    ]


def _parse_guid(guid_str: str) -> GUID:
    clean = guid_str.strip("{}").replace("-", "")
    d1 = int(clean[0:8], 16)
    d2 = int(clean[8:12], 16)
    d3 = int(clean[12:16], 16)
    d4 = (c_ubyte * 8)(*(int(clean[i : i + 2], 16) for i in range(16, 32, 2)))
    return GUID(d1, d2, d3, d4)


CLSID_MMDeviceEnumerator = _parse_guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _parse_guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
PKEY_Device_FriendlyName = PROPERTYKEY(
    _parse_guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14
)

ole32.CoCreateInstance.argtypes = [
    POINTER(GUID),
    c_void_p,
    DWORD,
    POINTER(GUID),
    POINTER(c_void_p),
]
ole32.CoCreateInstance.restype = HRESULT


def _com_call(interface_ptr: c_void_p, index: int, restype: Any, argtypes: list, *args) -> Any:
    vtable = cast(interface_ptr, POINTER(POINTER(c_void_p))).contents
    func = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])
    return func(interface_ptr, *args)


def _com_release(interface_ptr: Optional[c_void_p]) -> None:
    if interface_ptr:
        _com_call(interface_ptr, 2, c_ulong, [])


def _get_device_properties(p_device: c_void_p) -> Dict[str, Any]:
    dev_id: str = ""
    name: str = "Unknown"
    state_str: str = "Unknown"

    p_id = LPWSTR()
    if _com_call(p_device, 5, HRESULT, [POINTER(LPWSTR)], byref(p_id)) == 0:
        try:
            dev_id = p_id.value or ""
        finally:
            ole32.CoTaskMemFree(p_id)

    raw_state = DWORD()
    if _com_call(p_device, 6, HRESULT, [POINTER(DWORD)], byref(raw_state)) == 0:
        state_str = STATE_MAP.get(raw_state.value, f"0x{raw_state.value:X}")

    p_props = c_void_p()
    if _com_call(p_device, 4, HRESULT, [DWORD, POINTER(c_void_p)], STGM_READ, byref(p_props)) == 0:
        try:
            pv = PROPVARIANT()
            if (
                _com_call(
                    p_props,
                    5,
                    HRESULT,
                    [POINTER(PROPERTYKEY), POINTER(PROPVARIANT)],
                    byref(PKEY_Device_FriendlyName),
                    byref(pv),
                )
                == 0
            ):
                try:
                    if pv.vt == VT_LPWSTR and pv.pwszVal:
                        name = pv.pwszVal
                finally:
                    ole32.PropVariantClear(byref(pv))
        finally:
            _com_release(p_props)

    return {"name": name, "device_id": dev_id, "status": state_str}


def get_audio_devices(
    data_flow: int = DATA_FLOW_RENDER,
    state_mask: int = DEVICE_STATE_ACTIVE,
    role: int = ROLE_CONSOLE,
) -> Dict[str, Any]:
    """
    Deterministically enumerates audio endpoint devices and resolves the default output.
    """
    devices: List[Dict[str, Any]] = []
    default_device_id: Optional[str] = None

    hr = ole32.CoInitialize(None)
    co_initialized = hr in (0, 1)  # S_OK or S_FALSE

    try:
        p_enumerator = c_void_p()
        if (
            ole32.CoCreateInstance(
                byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_INPROC_SERVER,
                byref(IID_IMMDeviceEnumerator),
                byref(p_enumerator),
            )
            != 0
        ):
            raise RuntimeError("Failed to create IMMDeviceEnumerator instance.")

        try:
            p_default = c_void_p()
            if (
                _com_call(
                    p_enumerator,
                    4,
                    HRESULT,
                    [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                    data_flow,
                    role,
                    byref(p_default),
                )
                == 0
            ):
                try:
                    default_props = _get_device_properties(p_default)
                    default_device_id = default_props.get("device_id")
                finally:
                    _com_release(p_default)

            p_collection = c_void_p()
            if (
                _com_call(
                    p_enumerator,
                    3,
                    HRESULT,
                    [ctypes.c_int, DWORD, POINTER(c_void_p)],
                    data_flow,
                    state_mask,
                    byref(p_collection),
                )
                == 0
            ):
                try:
                    count = c_uint()
                    if _com_call(p_collection, 3, HRESULT, [POINTER(c_uint)], byref(count)) == 0:
                        for i in range(count.value):
                            p_device = c_void_p()
                            if (
                                _com_call(
                                    p_collection,
                                    4,
                                    HRESULT,
                                    [c_uint, POINTER(c_void_p)],
                                    i,
                                    byref(p_device),
                                )
                                == 0
                            ):
                                try:
                                    dev_info = _get_device_properties(p_device)
                                    dev_info["is_default"] = (
                                        dev_info["device_id"] == default_device_id
                                    )
                                    devices.append(dev_info)
                                finally:
                                    _com_release(p_device)
                finally:
                    _com_release(p_collection)
        finally:
            _com_release(p_enumerator)

    finally:
        if co_initialized:
            ole32.CoUninitialize()

    return {
        "default_device_id": default_device_id,
        "devices": devices,
    }


if __name__ == "__main__":
    audio_info = get_audio_devices(data_flow=DATA_FLOW_RENDER, state_mask=DEVICE_STATE_ACTIVE)

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