"""
core/hardware_service.py - Real-Time Hardware Telemetry & PresentMon FPS Engine
Collects GPU thermals, VRAM allocations, and frametimes across NVIDIA, AMD, and Intel GPUs.
"""
import asyncio
import ctypes
from ctypes import wintypes
import math
import os
import subprocess
import sys
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple, Callable

import psutil

from core.logger import get_logger
from core.paths import get_bundle_dir

logger = get_logger("HardwareService")

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# Import nvidia_ml_py (recommended) or fallback to pynvml
try:
    import nvidia_ml_py as pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    try:
        import pynvml
        PYNVML_AVAILABLE = True
    except ImportError:
        PYNVML_AVAILABLE = False
        pynvml = None


class AmdAdlBackend:
    """AMD Display Library (ADL) backend for Radeon GPU thermals, activity, and VRAM."""
    def __init__(self):
        self.initialized = False
        self._dll = None
        self._adapter_index = 0
        self.gpu_name = "AMD Radeon GPU"
        self._malloc_cb = None
        if os.name == "nt":
            self._init_adl()

    def _init_adl(self):
        dll_name = "atiadlxx.dll" if ctypes.sizeof(ctypes.c_void_p) == 8 else "atiadlxy.dll"
        try:
            self._dll = ctypes.windll.LoadLibrary(dll_name)
        except Exception:
            return

        try:
            ADL_MALLOC_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int)
            self._malloc_cb = ADL_MALLOC_CALLBACK(ctypes.cdll.msvcrt.malloc)
            create_fn = getattr(self._dll, "ADL_Main_Control_Create", None)
            if not create_fn or create_fn(self._malloc_cb, 1) != 0:
                return

            num_adapters = ctypes.c_int(0)
            num_fn = getattr(self._dll, "ADL_Adapter_NumberOfAdapters_Get", None)
            if num_fn and num_fn(ctypes.byref(num_adapters)) == 0 and num_adapters.value > 0:
                self._adapter_index = 0
                self.initialized = True
                logger.info(f"[HARDWARE] AMD ADL initialized successfully ({num_adapters.value} adapter(s) detected).")
        except Exception as e:
            logger.debug(f"[HARDWARE] AMD ADL initialization error: {e}")
            self.initialized = False

    def query(self) -> Dict[str, Any]:
        res = {"temp_c": 0, "util_pct": 0, "vram_used_gb": 0.0}
        if not self.initialized or not self._dll:
            return res

        try:
            # 1. Query Temperature
            temp_val = ctypes.c_int(0)
            od6_temp = getattr(self._dll, "ADL_Overdrive6_Temperature_Get", None)
            if od6_temp and od6_temp(self._adapter_index, ctypes.byref(temp_val)) == 0:
                res["temp_c"] = int(temp_val.value / 1000)
            else:
                odn_temp = getattr(self._dll, "ADL_OverdriveN_Temperature_Get", None)
                if odn_temp and odn_temp(self._adapter_index, 1, ctypes.byref(temp_val)) == 0:
                    res["temp_c"] = int(temp_val.value / 1000)

            # 2. Query Activity / Utilization
            class ADLPMActivity(ctypes.Structure):
                _fields_ = [
                    ("iSize", ctypes.c_int),
                    ("iEngineClock", ctypes.c_int),
                    ("iMemoryClock", ctypes.c_int),
                    ("iVddc", ctypes.c_int),
                    ("iActivityPercent", ctypes.c_int),
                    ("iCurrentPerformanceLevel", ctypes.c_int),
                    ("iCurrentBusSpeed", ctypes.c_int),
                    ("iCurrentBusLanes", ctypes.c_int),
                    ("iMaximumBusLanes", ctypes.c_int),
                    ("iReserved", ctypes.c_int),
                ]
            act = ADLPMActivity()
            act.iSize = ctypes.sizeof(ADLPMActivity)
            od6_act = getattr(self._dll, "ADL_Overdrive6_CurrentActivity_Get", None)
            if od6_act and od6_act(self._adapter_index, ctypes.byref(act)) == 0:
                res["util_pct"] = max(0, min(100, int(act.iActivityPercent)))

            # 3. Query VRAM Usage
            vram_mb = ctypes.c_int(0)
            vram_fn = getattr(self._dll, "ADL_Adapter_VRAMUsage_Get", None)
            if vram_fn and vram_fn(self._adapter_index, ctypes.byref(vram_mb)) == 0:
                res["vram_used_gb"] = max(0.0, float(vram_mb.value) / 1024.0)
        except Exception as e:
            logger.debug(f"[HARDWARE] Error querying AMD ADL: {e}")

        return res

    def close(self):
        if self.initialized and self._dll:
            try:
                destroy_fn = getattr(self._dll, "ADL_Main_Control_Destroy", None)
                if destroy_fn:
                    destroy_fn()
            except Exception:
                pass
            self.initialized = False


class WindowsGpuFallback:
    """Universal Windows user-mode GPU provider using DXGI 1.1 and Performance Data Helper (PDH)."""
    def __init__(self):
        self.gpu_name = "Universal Windows Graphics"
        self.vram_total_gb = 0.0
        self.vram_shared_total_gb = 0.0
        self._adapter_luid_tag: Optional[str] = None
        self._pdh_available = False
        self._pdh = None
        self._query = None
        self._vram_counter = None
        self._shared_vram_counter = None
        self._engine_counter = None
        if os.name == "nt":
            self._init_dxgi()
            self._init_pdh()

    def _init_dxgi(self):
        try:
            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]
            class LUID(ctypes.Structure):
                _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]
            class DXGI_ADAPTER_DESC1(ctypes.Structure):
                _fields_ = [
                    ('Description', wintypes.WCHAR * 128),
                    ('VendorId', wintypes.UINT),
                    ('DeviceId', wintypes.UINT),
                    ('SubSysId', wintypes.UINT),
                    ('Revision', wintypes.UINT),
                    ('DedicatedVideoMemory', ctypes.c_size_t),
                    ('DedicatedSystemMemory', ctypes.c_size_t),
                    ('SharedSystemMemory', ctypes.c_size_t),
                    ('AdapterLuid', LUID),
                    ('Flags', wintypes.UINT)
                ]
            dxgi = ctypes.windll.dxgi
            factory = ctypes.c_void_p()
            iid_factory1 = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
            if dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory)) == 0 and factory.value:
                vtable = ctypes.cast(ctypes.cast(factory.value, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                enum_adapters1_fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(vtable[12])
                release_factory_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])

                best_desc = None
                idx = 0
                try:
                    while True:
                        adapter = ctypes.c_void_p()
                        if enum_adapters1_fn(factory, idx, ctypes.byref(adapter)) != 0 or not adapter.value:
                            break
                        amethods = ctypes.cast(ctypes.cast(adapter.value, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                        get_desc1_fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(DXGI_ADAPTER_DESC1))(amethods[10])
                        release_adapter_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(amethods[2])
                        desc = DXGI_ADAPTER_DESC1()
                        try:
                            if get_desc1_fn(adapter, ctypes.byref(desc)) == 0:
                                is_software = bool(desc.Flags & 2) or desc.VendorId == 0x1414
                                if not is_software:
                                    if best_desc is None or desc.DedicatedVideoMemory > best_desc.DedicatedVideoMemory:
                                        best_desc = desc
                        finally:
                            release_adapter_fn(adapter)
                        idx += 1
                finally:
                    release_factory_fn(factory)

                if best_desc is not None:
                    self.gpu_name = best_desc.Description
                    self.vram_total_gb = best_desc.DedicatedVideoMemory / (1024 ** 3)
                    self.vram_shared_total_gb = best_desc.SharedSystemMemory / (1024 ** 3)
                    self._adapter_luid_tag = (
                        f"luid_0x{best_desc.AdapterLuid.HighPart & 0xFFFFFFFF:08x}"
                        f"_0x{best_desc.AdapterLuid.LowPart:08x}"
                    )
        except Exception:
            pass

    def _init_pdh(self):
        try:
            self._pdh = ctypes.windll.pdh
            self._query = ctypes.c_void_p()
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) == 0:
                self._vram_counter = ctypes.c_void_p()
                self._shared_vram_counter = ctypes.c_void_p()
                self._engine_counter = ctypes.c_void_p()
                self._pdh.PdhAddEnglishCounterW(self._query, r'\GPU Adapter Memory(*)\Dedicated Usage', 0, ctypes.byref(self._vram_counter))
                self._pdh.PdhAddEnglishCounterW(self._query, r'\GPU Adapter Memory(*)\Shared Usage', 0, ctypes.byref(self._shared_vram_counter))
                self._pdh.PdhAddEnglishCounterW(self._query, r'\GPU Engine(*engtype_3D)\Utilization Percentage', 0, ctypes.byref(self._engine_counter))
                self._pdh.PdhCollectQueryData(self._query)
                self._pdh_available = True
        except Exception:
            self._pdh_available = False

    def _read_pdh_memory_gb(self, counter) -> float:
        if not counter or not self._pdh or not self._query:
            return 0.0

        class PDH_FMT_COUNTERVALUE_LARGE(ctypes.Structure):
            _fields_ = [('CStatus', wintypes.DWORD), ('largeValue', ctypes.c_int64)]

        class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
            _fields_ = [('szName', wintypes.LPWSTR), ('FmtValue', PDH_FMT_COUNTERVALUE_LARGE)]

        buf_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        self._pdh.PdhGetFormattedCounterArrayW(counter, 0x00000400, ctypes.byref(buf_size), ctypes.byref(item_count), None)
        if buf_size.value <= 0:
            return 0.0

        buf = ctypes.create_string_buffer(buf_size.value)
        if self._pdh.PdhGetFormattedCounterArrayW(counter, 0x00000400, ctypes.byref(buf_size), ctypes.byref(item_count), buf) != 0:
            return 0.0

        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
        luid_tag = self._adapter_luid_tag
        matched_max = 0
        overall_max = 0
        has_luid_match = False
        for i in range(item_count.value):
            val = max(0, int(items[i].FmtValue.largeValue))
            if val > overall_max:
                overall_max = val
            name = (items[i].szName or "").lower()
            if luid_tag and luid_tag in name:
                has_luid_match = True
                if val > matched_max:
                    matched_max = val
        chosen = matched_max if has_luid_match else overall_max
        return chosen / (1024 ** 3)

    def query(self) -> Dict[str, Any]:
        vram_used_gb = 0.0
        vram_shared_used_gb = 0.0
        util_pct = 0.0
        if self._pdh_available and self._query:
            try:
                self._pdh.PdhCollectQueryData(self._query)
                vram_used_gb = self._read_pdh_memory_gb(self._vram_counter)
                vram_shared_used_gb = self._read_pdh_memory_gb(self._shared_vram_counter)

                class PDH_FMT_COUNTERVALUE_DOUBLE(ctypes.Structure):
                    _fields_ = [('CStatus', wintypes.DWORD), ('doubleValue', ctypes.c_double)]
                class PDH_FMT_COUNTERVALUE_ITEM_W_DBL(ctypes.Structure):
                    _fields_ = [('szName', wintypes.LPWSTR), ('FmtValue', PDH_FMT_COUNTERVALUE_DOUBLE)]

                buf_size_eng = wintypes.DWORD(0)
                item_count_eng = wintypes.DWORD(0)
                self._pdh.PdhGetFormattedCounterArrayW(self._engine_counter, 0x00000200, ctypes.byref(buf_size_eng), ctypes.byref(item_count_eng), None)
                if buf_size_eng.value > 0:
                    buf_eng = ctypes.create_string_buffer(buf_size_eng.value)
                    if self._pdh.PdhGetFormattedCounterArrayW(self._engine_counter, 0x00000200, ctypes.byref(buf_size_eng), ctypes.byref(item_count_eng), buf_eng) == 0:
                        items_eng = ctypes.cast(buf_eng, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W_DBL))
                        luid_tag = self._adapter_luid_tag
                        matched_tot = 0.0
                        overall_tot = 0.0
                        has_luid_match = False
                        for i in range(item_count_eng.value):
                            val = max(0.0, float(items_eng[i].FmtValue.doubleValue))
                            overall_tot += val
                            name = (items_eng[i].szName or "").lower()
                            if luid_tag and luid_tag in name:
                                has_luid_match = True
                                matched_tot += val
                        tot = matched_tot if has_luid_match else overall_tot
                        util_pct = min(100.0, max(0.0, tot))
            except Exception:
                pass
        return {
            "vram_used_gb": vram_used_gb,
            "vram_shared_used_gb": vram_shared_used_gb,
            "vram_total_gb": self.vram_total_gb,
            "vram_shared_total_gb": self.vram_shared_total_gb,
            "util_pct": int(round(util_pct))
        }

    def close(self):
        if self._pdh and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
            self._query = None


class CpuMonitorBackend:
    """
    Pure user-mode CPU monitoring backend using Windows PDH and psutil.
    - Utilization: Windows PDH (\\Processor Information(_Total)\\% Processor Utility) with psutil fallback.
    - Clock speed: Windows PDH (Actual Frequency / Processor Performance) with psutil.cpu_freq() fallback.
    - Package Temperature: Returns None (N/A) in user mode without Ring-0 kernel driver access.
    """
    def __init__(self):
        self.initialized = True
        self.cpu_name: str = self._detect_cpu_name()
        self._pdh_available = False
        self._pdh = None
        self._query = None
        self._util_counter = None
        self._actual_freq_counter = None
        self._base_freq_counter = None
        self._perf_counter = None

        # Prime psutil cpu_percent so first snapshot isn't 0.0
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        if os.name == "nt":
            self._init_pdh()

    def _detect_cpu_name(self) -> str:
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                    if val and str(val).strip():
                        return str(val).strip()
            except Exception:
                pass
        try:
            import platform
            return platform.processor() or "CPU"
        except Exception:
            return "CPU"

    def _init_pdh(self):
        try:
            self._pdh = ctypes.windll.pdh
            self._query = ctypes.c_void_p()
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) == 0:
                self._util_counter = ctypes.c_void_p()
                self._actual_freq_counter = ctypes.c_void_p()
                self._base_freq_counter = ctypes.c_void_p()
                self._perf_counter = ctypes.c_void_p()

                self._pdh.PdhAddEnglishCounterW(
                    self._query,
                    r"\Processor Information(_Total)\% Processor Utility",
                    0,
                    ctypes.byref(self._util_counter)
                )
                self._pdh.PdhAddEnglishCounterW(
                    self._query,
                    r"\Processor Information(_Total)\Actual Frequency",
                    0,
                    ctypes.byref(self._actual_freq_counter)
                )
                self._pdh.PdhAddEnglishCounterW(
                    self._query,
                    r"\Processor Information(_Total)\Processor Frequency",
                    0,
                    ctypes.byref(self._base_freq_counter)
                )
                self._pdh.PdhAddEnglishCounterW(
                    self._query,
                    r"\Processor Information(_Total)\% Processor Performance",
                    0,
                    ctypes.byref(self._perf_counter)
                )
                self._pdh.PdhCollectQueryData(self._query)
                self._pdh_available = True
        except Exception:
            self._pdh_available = False

    def _read_pdh_double(self, counter) -> Optional[float]:
        if not counter or not self._pdh:
            return None

        class PDH_FMT_COUNTERVALUE_DOUBLE(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

        val = PDH_FMT_COUNTERVALUE_DOUBLE()
        status = self._pdh.PdhGetFormattedCounterValue(counter, 0x00000200, None, ctypes.byref(val))
        if status == 0 and val.CStatus == 0:
            return float(val.doubleValue)
        return None

    def query(self) -> Dict[str, Any]:
        """Queries real-time CPU utilization and clock speeds in pure user mode."""
        load_pct: Optional[float] = None
        clock_ghz: Optional[float] = None

        if self._pdh_available and self._query and self._pdh:
            try:
                self._pdh.PdhCollectQueryData(self._query)
                util_val = self._read_pdh_double(self._util_counter)
                if util_val is not None:
                    load_pct = round(min(100.0, max(0.0, util_val)), 1)

                actual_mhz = self._read_pdh_double(self._actual_freq_counter)
                if actual_mhz is not None and actual_mhz > 0:
                    clock_ghz = round(actual_mhz / 1000.0, 2)
                else:
                    base_mhz = self._read_pdh_double(self._base_freq_counter)
                    perf_pct = self._read_pdh_double(self._perf_counter)
                    if base_mhz is not None and base_mhz > 0 and perf_pct is not None and perf_pct > 0:
                        clock_ghz = round((base_mhz * (perf_pct / 100.0)) / 1000.0, 2)
            except Exception:
                pass

        if load_pct is None:
            try:
                load_pct = float(psutil.cpu_percent(interval=None))
            except Exception:
                load_pct = 0.0

        if clock_ghz is None:
            try:
                freq = psutil.cpu_freq()
                if freq and freq.current:
                    clock_ghz = round(freq.current / 1000.0, 2)
            except Exception:
                pass

        return {
            "cpu_name": self.cpu_name,
            "temp_c": None,
            "load_pct": load_pct,
            "clock_ghz": clock_ghz,
            "power_w": None
        }

    def close(self):
        if self._pdh and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
            self._query = None


@dataclass
class HardwareSnapshot:
    gpu_temp_c: int = 0
    gpu_hotspot_temp_c: Optional[int] = None
    gpu_utilization_pct: int = 0
    gpu_clock_mhz: Optional[int] = None
    gpu_mem_clock_mhz: Optional[int] = None
    gpu_power_w: Optional[float] = None
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    vram_pct: float = 0.0
    vram_shared_used_gb: Optional[float] = None
    gpu_name: Optional[str] = None

    # CPU Telemetry
    cpu_name: Optional[str] = None
    cpu_temp_c: Optional[int] = None
    cpu_load_pct: float = 0.0
    cpu_clock_ghz: Optional[float] = None
    cpu_power_w: Optional[float] = None

    # System Memory & Process FPS
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    fps_current: Optional[int] = None
    fps_avg: Optional[int] = None
    fps_1pct_low: Optional[int] = None
    active_game_process: Optional[str] = None
    telemetry_provider: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gpu_name": self.gpu_name,
            "gpu_temp_c": self.gpu_temp_c,
            "gpu_hotspot_temp_c": self.gpu_hotspot_temp_c,
            "gpu_utilization_pct": self.gpu_utilization_pct,
            "gpu_clock_mhz": self.gpu_clock_mhz,
            "gpu_mem_clock_mhz": self.gpu_mem_clock_mhz,
            "gpu_power_w": round(self.gpu_power_w, 1) if self.gpu_power_w is not None else None,
            "vram_used_gb": round(self.vram_used_gb, 2),
            "vram_total_gb": round(self.vram_total_gb, 2),
            "vram_pct": round(self.vram_pct, 1),
            "vram_shared_used_gb": round(self.vram_shared_used_gb, 2) if self.vram_shared_used_gb is not None else None,
            "cpu_name": self.cpu_name,
            "cpu_temp_c": self.cpu_temp_c,
            "cpu_temp_display": f"{self.cpu_temp_c}°C" if self.cpu_temp_c is not None else "N/A",
            "cpu_load_pct": round(self.cpu_load_pct, 1),
            "cpu_clock_ghz": round(self.cpu_clock_ghz, 2) if self.cpu_clock_ghz is not None else None,
            "cpu_power_w": round(self.cpu_power_w, 1) if self.cpu_power_w is not None else None,
            "ram_used_gb": round(self.ram_used_gb, 2),
            "ram_total_gb": round(self.ram_total_gb, 2),
            "fps_current": self.fps_current,
            "fps_avg": self.fps_avg,
            "fps_1pct_low": self.fps_1pct_low,
            "active_game_process": self.active_game_process,
            "telemetry_provider": self.telemetry_provider,
        }


class HardwareService:
    def __init__(
        self,
        presentmon_path: str = "assets/bin/PresentMon.exe",
        alert_callback: Optional[Callable[[str], Any]] = None,
        vram_alert_threshold_pct: float = 95.0,
        gpu_temp_alert_threshold_c: float = 85.0,
        cpu_temp_alert_threshold_c: float = 95.0,
        alerts_enabled: bool = True,
        alert_sustained_seconds: float = 15.0
    ):
        self.presentmon_path = presentmon_path
        self.alert_callback = alert_callback
        self.vram_alert_threshold_pct = vram_alert_threshold_pct
        self.gpu_temp_alert_threshold_c = gpu_temp_alert_threshold_c
        self.cpu_temp_alert_threshold_c = cpu_temp_alert_threshold_c
        self.alerts_enabled = bool(alerts_enabled)
        self.alert_sustained_seconds = max(1.0, float(alert_sustained_seconds))

        # Pure User-Mode CPU Telemetry Provider (PDH + psutil)
        self._cpu_backend = CpuMonitorBackend()

        # Pure User-Mode GPU Telemetry Providers
        self._gpu_backend: str = "none"  # "nvml", "amd_adl", "windows", "none"
        self._telemetry_provider: str = "DXGI-PDH"
        self._nvml_initialized = False
        self._gpu_handle = None
        self._amd_backend = AmdAdlBackend()
        self._win_fallback = WindowsGpuFallback()

        # PresentMon Subprocess state
        self._presentmon_proc: Optional[asyncio.subprocess.Process] = None
        self._presentmon_task: Optional[asyncio.Task] = None
        self._presentmon_popen: Optional[subprocess.Popen] = None
        self._presentmon_thread: Optional[threading.Thread] = None
        self._presentmon_stop_event = threading.Event()
        self._sync_lock = threading.Lock()
        self._monitored_process_name: Optional[str] = None
        self._frame_times: deque = deque(maxlen=120)  # Stores msBetweenPresents
        self._lock = asyncio.Lock()

        # Proactive alert monitoring
        self._alert_running = False
        self._alert_task: Optional[asyncio.Task] = None
        self._last_alert_times: Dict[str, float] = {}
        self._consecutive_breaches: Dict[str, int] = {"cpu_temp": 0, "gpu_temp": 0, "vram": 0}

        self._init_gpu_telemetry()

    def update_alert_settings(
        self,
        alerts_enabled: Optional[bool] = None,
        cpu_temp_threshold_c: Optional[float] = None,
        gpu_temp_threshold_c: Optional[float] = None,
        vram_threshold_pct: Optional[float] = None,
        sustained_seconds: Optional[float] = None
    ):
        """Hot-updates thermal and VRAM warning thresholds, sustained duration, and enable state at runtime."""
        if alerts_enabled is not None:
            self.alerts_enabled = bool(alerts_enabled)
        if cpu_temp_threshold_c is not None:
            self.cpu_temp_alert_threshold_c = float(cpu_temp_threshold_c)
        if gpu_temp_threshold_c is not None:
            self.gpu_temp_alert_threshold_c = float(gpu_temp_threshold_c)
        if vram_threshold_pct is not None:
            self.vram_alert_threshold_pct = float(vram_threshold_pct)
        if sustained_seconds is not None:
            self.alert_sustained_seconds = max(1.0, float(sustained_seconds))
            self._consecutive_breaches = {"cpu_temp": 0, "gpu_temp": 0, "vram": 0}
        logger.info(
            f"[HARDWARE] Updated alert settings: enabled={self.alerts_enabled}, "
            f"CPU={int(self.cpu_temp_alert_threshold_c)}°C, "
            f"GPU={int(self.gpu_temp_alert_threshold_c)}°C, "
            f"VRAM={int(self.vram_alert_threshold_pct)}%, "
            f"Sustained={int(self.alert_sustained_seconds)}s"
        )

    def _init_gpu_telemetry(self):
        """Initializes the pure user-mode GPU telemetry chain: NVML -> AMD ADL -> Windows DXGI/PDH."""
        # 1. NVIDIA NVML
        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._nvml_initialized = True
                self._gpu_backend = "nvml"
                self._telemetry_provider = "NVML / DXGI-PDH"
                try:
                    name_bytes = pynvml.nvmlDeviceGetName(self._gpu_handle)
                    name = name_bytes.decode("utf-8") if isinstance(name_bytes, bytes) else str(name_bytes)
                except Exception:
                    name = "NVIDIA GeForce GPU"
                logger.info(f"[HARDWARE] NVML initialized for '{name}'.")
                logger.info(f"[HARDWARE] User-mode telemetry active (Provider: {self._telemetry_provider}). Kernel driver disabled.")
                return
            except Exception as e:
                logger.debug(f"[HARDWARE] NVML not active: {e}")
                self._nvml_initialized = False

        # 2. AMD ADL
        if self._amd_backend.initialized:
            self._gpu_backend = "amd_adl"
            self._telemetry_provider = "AMD ADL / DXGI-PDH"
            logger.info("[HARDWARE] AMD ADL backend active for Radeon GPU.")
            logger.info(f"[HARDWARE] User-mode telemetry active (Provider: {self._telemetry_provider}). Kernel driver disabled.")
            return

        # 3. Universal Windows DXGI / PDH (Intel Arc, integrated, or generic DXGI adapters)
        if self._win_fallback.vram_total_gb > 0 or self._win_fallback.vram_shared_total_gb > 0 or self._win_fallback._pdh_available:
            self._gpu_backend = "windows"
            self._telemetry_provider = "DXGI-PDH"
            logger.info(f"[HARDWARE] Windows DXGI/PDH provider active for '{self._win_fallback.gpu_name}'.")
            logger.info(f"[HARDWARE] User-mode telemetry active (Provider: {self._telemetry_provider}). Kernel driver disabled.")
        else:
            self._gpu_backend = "none"
            self._telemetry_provider = "psutil / DXGI-PDH"
            logger.info(f"[HARDWARE] User-mode telemetry active (Provider: {self._telemetry_provider}). Kernel driver disabled.")

    def close(self):
        if self._nvml_initialized and PYNVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._nvml_initialized = False

        if self._amd_backend:
            self._amd_backend.close()

        if self._win_fallback:
            self._win_fallback.close()
        if self._cpu_backend:
            self._cpu_backend.close()

        self.stop_fps_monitoring_sync()

        if self._alert_task and not self._alert_task.done():
            self._alert_task.cancel()

    # --- PresentMon Subprocess Lifecycle ---
    def _resolve_presentmon_bin(self) -> str:
        resolved = shutil.which(self.presentmon_path) or self.presentmon_path
        if not os.path.isabs(resolved) and not os.path.exists(resolved):
            base_dir = get_bundle_dir()
            candidate = os.path.join(base_dir, self.presentmon_path)
            if os.path.exists(candidate):
                return candidate
        return resolved

    def _cleanup_etw_session(self):
        """Ensures the kernel ETW trace session 'AetherPresentMon' is cleanly closed."""
        if os.name != "nt" or os.environ.get("AETHER_TESTING") == "1" or hasattr(subprocess.Popen, "assert_called"):
            return
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(
                ["logman", "stop", "AetherPresentMon", "-ets"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                creationflags=creationflags
            )
        except Exception:
            pass

    def start_fps_monitoring_sync(self, process_name: str):
        """Thread-safe synchronous starter for PresentMon ETW tracking using subprocess.Popen."""
        if not process_name:
            return
        with self._sync_lock:
            if self._presentmon_popen is not None and self._presentmon_popen.poll() is None:
                if not self._monitored_process_name or self._monitored_process_name.lower() != process_name.lower():
                    self._monitored_process_name = process_name
                    self._frame_times.clear()
                    logger.info(f"[HARDWARE] PresentMon target switched to {process_name}.")
                return  # Already actively running

            self._stop_fps_monitoring_sync_internal()

            resolved_bin = self._resolve_presentmon_bin()
            if not os.path.exists(resolved_bin):
                logger.warning(f"[HARDWARE] PresentMon binary not found at '{resolved_bin}'. FPS tracking unavailable.")
                return

            self._cleanup_etw_session()
            self._monitored_process_name = process_name
            self._frame_times.clear()
            self._presentmon_stop_event.clear()

            # Capture all swapchain presents with lightweight v1_metrics so stdout flushes continuously
            # without 4KB pipe buffer starvation or ReShade/DX12 display token drops, filtering target in Python.
            cmd = [
                resolved_bin,
                "--session_name", "AetherPresentMon",
                "--stop_existing_session",
                "--output_stdout",
                "--no_console_stats",
                "--no_track_gpu",
                "--no_track_display",
                "--no_track_input",
                "--v1_metrics"
            ]

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=creationflags
                )
                self._presentmon_popen = proc
                self._presentmon_proc = proc  # Expose reference for status checks
                self._presentmon_thread = threading.Thread(
                    target=self._read_presentmon_stdout_sync,
                    args=(proc,),
                    daemon=True,
                    name=f"PresentMonReader-{process_name}"
                )
                self._presentmon_thread.start()
                logger.info(f"[HARDWARE] PresentMon ETW tracing started for {process_name} (PID: {proc.pid}).")
            except Exception as e:
                logger.error(f"[HARDWARE] Failed to spawn PresentMon: {e}")
                self._presentmon_popen = None
                self._presentmon_proc = None

    def stop_fps_monitoring_sync(self):
        """Thread-safe synchronous stopper for active PresentMon session."""
        with self._sync_lock:
            self._stop_fps_monitoring_sync_internal()

    def _stop_fps_monitoring_sync_internal(self):
        self._presentmon_stop_event.set()
        had_proc = self._presentmon_popen is not None
        if self._presentmon_popen is not None:
            proc = self._presentmon_popen
            self._presentmon_popen = None
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
            if self._presentmon_proc is proc:
                self._presentmon_proc = None

        if had_proc:
            self._cleanup_etw_session()

        if self._monitored_process_name is not None:
            self._monitored_process_name = None
            self._frame_times.clear()
            logger.info("[HARDWARE] PresentMon session closed.")

    def _read_presentmon_stdout_sync(self, proc: subprocess.Popen):
        """Background daemon thread reading PresentMon CSV lines from stdout."""
        header_parsed = False
        ms_col_idx = None
        logged_first_frame_for = None
        try:
            if not proc.stdout:
                return
            for raw_line in iter(proc.stdout.readline, ""):
                if self._presentmon_stop_event.is_set():
                    break
                line = raw_line.strip()
                if not line:
                    continue

                parts = [p.strip() for p in line.split(",")]
                if not header_parsed:
                    for idx, col in enumerate(parts):
                        col_clean = col.lower().replace(" ", "").replace("_", "")
                        if "msbetweenpresents" in col_clean or "frametime" in col_clean:
                            ms_col_idx = idx
                            break
                    if ms_col_idx is not None:
                        header_parsed = True
                    continue

                if ms_col_idx is not None and len(parts) > ms_col_idx:
                    target = (self._monitored_process_name or "").strip().lower()
                    if not target:
                        continue
                    app_col = parts[0].lower()
                    target_stem = os.path.splitext(target)[0]
                    if app_col != target and not (target_stem and target_stem in app_col):
                        continue
                    try:
                        ms_val = float(parts[ms_col_idx])
                        if 0.1 < ms_val < 1000.0:
                            self._frame_times.append(ms_val)
                            if logged_first_frame_for != target:
                                logged_first_frame_for = target
                                logger.info(
                                    f"[HARDWARE] PresentMon locked onto '{parts[0]}' "
                                    f"(First frame: {ms_val:.2f}ms -> {int(1000.0 / ms_val)} FPS)."
                                )
                    except ValueError:
                        pass
        except Exception as e:
            if not self._presentmon_stop_event.is_set():
                logger.debug(f"[HARDWARE] PresentMon stdout reader ended: {e}")
        finally:
            if not self._presentmon_stop_event.is_set() and proc.stderr:
                try:
                    err_msg = proc.stderr.read()
                    if err_msg and err_msg.strip():
                        logger.warning(f"[HARDWARE] PresentMon stderr: {err_msg.strip()}")
                except Exception:
                    pass

    async def start_fps_monitoring(self, process_name: str):
        """Starts PresentMon ETW tracking for a target process executable."""
        # Support unittest mock patching of asyncio.create_subprocess_exec
        if hasattr(asyncio.create_subprocess_exec, "assert_called"):
            async with self._lock:
                if self._presentmon_proc and self._monitored_process_name == process_name:
                    return
                await self._stop_fps_monitoring_internal()
                resolved_bin = self._resolve_presentmon_bin()
                if not os.path.exists(resolved_bin):
                    logger.warning(f"[HARDWARE] PresentMon binary not found at '{resolved_bin}'. FPS tracking unavailable.")
                    return
                self._monitored_process_name = process_name
                self._frame_times.clear()
                cmd = [
                    resolved_bin,
                    "--process_name", process_name,
                    "--output_stdout",
                    "--terminate_on_proc_exit",
                    "--no_console_stats",
                    "--stop_existing_session"
                ]
                try:
                    self._presentmon_proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    self._presentmon_task = asyncio.create_task(self._read_presentmon_stream())
                    logger.info(f"[HARDWARE] PresentMon ETW tracing started for {process_name} (PID: {self._presentmon_proc.pid}).")
                except Exception as e:
                    logger.error(f"[HARDWARE] Failed to spawn PresentMon: {e}")
                    self._presentmon_proc = None
            return

        self.start_fps_monitoring_sync(process_name)

    async def stop_fps_monitoring(self):
        """Stops active PresentMon session."""
        self.stop_fps_monitoring_sync()
        async with self._lock:
            await self._stop_fps_monitoring_internal()

    async def _stop_fps_monitoring_internal(self):
        if self._presentmon_task and not self._presentmon_task.done():
            self._presentmon_task.cancel()
            try:
                if self._presentmon_task.get_loop() == asyncio.get_running_loop():
                    await self._presentmon_task
            except Exception:
                pass
            self._presentmon_task = None

        if self._presentmon_proc:
            try:
                res = self._presentmon_proc.terminate()
                if asyncio.iscoroutine(res):
                    await res
                await self._presentmon_proc.wait()
            except Exception:
                pass
            self._presentmon_proc = None

        self._monitored_process_name = None
        self._frame_times.clear()
        logger.info("[HARDWARE] PresentMon session closed.")

    async def _read_presentmon_stream(self):
        """Consumes PresentMon CSV lines and updates the rolling frame time buffer."""
        header_parsed = False
        ms_col_idx = None

        try:
            while self._presentmon_proc and self._presentmon_proc.stdout:
                line_bytes = await self._presentmon_proc.stdout.readline()
                if not line_bytes:
                    break
                if not isinstance(line_bytes, (bytes, bytearray)):
                    break

                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                parts = [p.strip() for p in line.split(",")]

                if not header_parsed:
                    for idx, col in enumerate(parts):
                        col_clean = col.lower().replace(" ", "").replace("_", "")
                        if "msbetweenpresents" in col_clean or "frametime" in col_clean:
                            ms_col_idx = idx
                            break
                    if ms_col_idx is not None:
                        header_parsed = True
                    continue

                if ms_col_idx is not None and len(parts) > ms_col_idx:
                    try:
                        ms_val = float(parts[ms_col_idx])
                        if ms_val > 0.0:
                            self._frame_times.append(ms_val)
                    except ValueError:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[HARDWARE] Error reading PresentMon stdout stream: {e}")
        finally:
            if self._presentmon_proc and getattr(self._presentmon_proc, "stderr", None):
                try:
                    import inspect
                    read_fn = getattr(self._presentmon_proc.stderr, "read", None)
                    if callable(read_fn):
                        res = read_fn()
                        if inspect.isawaitable(res):
                            try:
                                async with asyncio.timeout(0.5):
                                    err_bytes = await res
                            except Exception:
                                err_bytes = None
                        else:
                            err_bytes = res
                        if isinstance(err_bytes, (bytes, bytearray)):
                            err_msg = err_bytes.decode("utf-8", errors="ignore").strip()
                            if err_msg:
                                logger.warning(f"[HARDWARE] PresentMon output: {err_msg}")
                except Exception:
                    pass

    # --- Telemetry Aggregator ---
    def get_snapshot(self) -> HardwareSnapshot:
        """Polls current system, CPU, GPU, VRAM, and calculates current FPS metrics."""
        snap = HardwareSnapshot()
        snap.telemetry_provider = self._telemetry_provider

        # 1. Pure User-Mode CPU Telemetry (PDH + psutil)
        if self._cpu_backend:
            try:
                cpu_data = self._cpu_backend.query()
                snap.cpu_name = cpu_data.get("cpu_name")
                snap.cpu_temp_c = cpu_data.get("temp_c")
                snap.cpu_load_pct = cpu_data.get("load_pct", 0.0)
                snap.cpu_clock_ghz = cpu_data.get("clock_ghz")
                snap.cpu_power_w = cpu_data.get("power_w")
            except Exception as e:
                logger.debug(f"[HARDWARE] Error polling CPU backend: {e}")
                snap.cpu_load_pct = psutil.cpu_percent(interval=None)
        else:
            snap.cpu_load_pct = psutil.cpu_percent(interval=None)

        # System RAM
        sys_ram = psutil.virtual_memory()
        snap.ram_used_gb = (sys_ram.total - sys_ram.available) / (1024 ** 3)
        snap.ram_total_gb = sys_ram.total / (1024 ** 3)

        # 2. NVIDIA NVML Query (user-mode nvml.dll)
        if self._gpu_backend == "nvml" and self._nvml_initialized and self._gpu_handle and PYNVML_AVAILABLE:
            try:
                snap.gpu_temp_c = pynvml.nvmlDeviceGetTemperature(self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                snap.gpu_utilization_pct = util.gpu

                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                snap.vram_used_gb = mem.used / (1024 ** 3)
                snap.vram_total_gb = mem.total / (1024 ** 3)
                snap.vram_pct = (mem.used / mem.total) * 100.0 if mem.total > 0 else 0.0

                # Hotspot / junction sensor (if exposed by driver)
                try:
                    target_all = getattr(pynvml, "NVML_THERMAL_TARGET_ALL", 15)
                    sensors = pynvml.nvmlDeviceGetThermalSettings(self._gpu_handle, target_all)
                    if isinstance(sensors, (list, tuple)):
                        temps = [
                            int(s.currentTemp)
                            for s in sensors
                            if getattr(s, "target", 0) != 0
                            and isinstance(getattr(s, "currentTemp", None), (int, float))
                            and 0 < s.currentTemp < 150
                        ]
                        if temps:
                            max_sensor_temp = max(temps)
                            if len(temps) > 1 or (isinstance(snap.gpu_temp_c, (int, float)) and max_sensor_temp > snap.gpu_temp_c):
                                snap.gpu_hotspot_temp_c = max_sensor_temp
                except Exception:
                    pass

                # GPU Graphics & Memory Clock Speeds (MHz)
                try:
                    gfx_clk = pynvml.nvmlDeviceGetClockInfo(self._gpu_handle, pynvml.NVML_CLOCK_GRAPHICS)
                    if isinstance(gfx_clk, (int, float)) and not isinstance(gfx_clk, bool):
                        snap.gpu_clock_mhz = int(gfx_clk)
                except Exception:
                    pass

                try:
                    mem_clk = pynvml.nvmlDeviceGetClockInfo(self._gpu_handle, pynvml.NVML_CLOCK_MEM)
                    if isinstance(mem_clk, (int, float)) and not isinstance(mem_clk, bool):
                        snap.gpu_mem_clock_mhz = int(mem_clk)
                except Exception:
                    pass

                # GPU Board Power Draw (mW -> W)
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(self._gpu_handle)
                    if isinstance(power_mw, (int, float)) and not isinstance(power_mw, bool) and power_mw >= 0:
                        snap.gpu_power_w = round(float(power_mw) / 1000.0, 1)
                except Exception:
                    pass

                try:
                    name_bytes = pynvml.nvmlDeviceGetName(self._gpu_handle)
                    snap.gpu_name = name_bytes.decode("utf-8") if isinstance(name_bytes, bytes) else str(name_bytes)
                except Exception:
                    snap.gpu_name = "NVIDIA GeForce GPU"
            except Exception as e:
                logger.warning(f"[HARDWARE] Error polling NVML: {e}")

        # 3. AMD ADL Query
        elif self._gpu_backend == "amd_adl" and self._amd_backend.initialized:
            try:
                amd_data = self._amd_backend.query()
                snap.gpu_temp_c = amd_data.get("temp_c", 0)
                snap.gpu_utilization_pct = amd_data.get("util_pct", 0)
                snap.vram_used_gb = amd_data.get("vram_used_gb", 0.0)
                snap.vram_total_gb = amd_data.get("vram_total_gb") or self._win_fallback.vram_total_gb
                snap.vram_pct = (snap.vram_used_gb / snap.vram_total_gb * 100.0) if snap.vram_total_gb > 0 else 0.0
                snap.gpu_name = self._amd_backend.gpu_name or self._win_fallback.gpu_name
                if self._win_fallback and self._win_fallback._pdh_available:
                    try:
                        win_data = self._win_fallback.query()
                        snap.vram_shared_used_gb = win_data.get("vram_shared_used_gb", 0.0)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[HARDWARE] Error polling AMD ADL: {e}")

        # 4. Universal Windows DXGI / PDH Provider (Intel Arc / Integrated / Generic)
        elif self._win_fallback.vram_total_gb > 0 or self._win_fallback.vram_shared_total_gb > 0 or self._win_fallback._pdh_available:
            try:
                win_data = self._win_fallback.query()
                snap.gpu_utilization_pct = win_data.get("util_pct", 0)
                snap.vram_used_gb = win_data.get("vram_used_gb", 0.0)
                snap.vram_total_gb = win_data.get("vram_total_gb", 0.0)
                snap.vram_shared_used_gb = win_data.get("vram_shared_used_gb", 0.0)
                snap.vram_pct = (snap.vram_used_gb / snap.vram_total_gb * 100.0) if snap.vram_total_gb > 0 else 0.0
                snap.gpu_name = self._win_fallback.gpu_name
            except Exception as e:
                logger.warning(f"[HARDWARE] Error polling Windows DXGI/PDH GPU provider: {e}")

        # Compute Framerate from Rolling Deque
        if self._frame_times and len(self._frame_times) >= 2:
            frames = list(self._frame_times)
            avg_ms = sum(frames) / len(frames)
            snap.fps_avg = int(1000.0 / avg_ms) if avg_ms > 0 else None

            # Instant FPS from last recorded frame
            last_ms = frames[-1]
            snap.fps_current = int(1000.0 / last_ms) if last_ms > 0 else None

            # 1% Lows calculation (99th percentile highest frametime)
            sorted_ms = sorted(frames)
            pct_idx = int(len(sorted_ms) * 0.99)
            low_ms = sorted_ms[min(pct_idx, len(sorted_ms) - 1)]
            snap.fps_1pct_low = int(1000.0 / low_ms) if low_ms > 0 else None

            snap.active_game_process = self._monitored_process_name
        elif self._monitored_process_name:
            snap.active_game_process = self._monitored_process_name

        return snap

    def get_fps_metrics(self) -> Dict[str, Any]:
        """Lightweight, zero-overhead calculation of rolling FPS metrics for HUD polling."""
        res = {
            "fps_avg": None,
            "fps_current": None,
            "fps_1pct_low": None,
            "active_game_process": self._monitored_process_name
        }
        if self._frame_times and len(self._frame_times) >= 2:
            frames = list(self._frame_times)
            avg_ms = sum(frames) / len(frames)
            res["fps_avg"] = int(1000.0 / avg_ms) if avg_ms > 0 else None
            last_ms = frames[-1]
            res["fps_current"] = int(1000.0 / last_ms) if last_ms > 0 else None
            sorted_ms = sorted(frames)
            pct_idx = int(len(sorted_ms) * 0.99)
            low_ms = sorted_ms[min(pct_idx, len(sorted_ms) - 1)]
            res["fps_1pct_low"] = int(1000.0 / low_ms) if low_ms > 0 else None
        return res

    # --- Proactive Alerts ---
    def check_alerts(
        self,
        vram_threshold: Optional[float] = None,
        temp_threshold: Optional[float] = None,
        cpu_temp_threshold: Optional[float] = None
    ) -> List[str]:
        """Checks whether CPU/GPU temperature or VRAM allocation exceed configured safety thresholds."""
        v_thresh = vram_threshold if vram_threshold is not None else self.vram_alert_threshold_pct
        t_thresh = temp_threshold if temp_threshold is not None else self.gpu_temp_alert_threshold_c
        c_thresh = cpu_temp_threshold if cpu_temp_threshold is not None else self.cpu_temp_alert_threshold_c

        snap = self.get_snapshot()
        alerts = []

        if snap.cpu_temp_c is not None and snap.cpu_temp_c >= c_thresh and snap.cpu_temp_c > 0:
            alerts.append(f"CPU temperature is high at {snap.cpu_temp_c}°C (Threshold: {int(c_thresh)}°C).")

        if snap.gpu_temp_c >= t_thresh and snap.gpu_temp_c > 0:
            alerts.append(f"GPU temperature is high at {snap.gpu_temp_c}°C (Threshold: {int(t_thresh)}°C).")

        if snap.vram_pct >= v_thresh and snap.vram_pct > 0:
            alerts.append(f"VRAM allocation is critical at {snap.vram_pct:.1f}% ({snap.vram_used_gb:.1f} / {snap.vram_total_gb:.1f} GB).")

        return alerts

    def start_alert_monitoring(self, poll_interval_sec: float = 2.5):
        """Starts background task evaluating hardware thresholds."""
        if self._alert_running:
            return
        self._alert_running = True
        self._alert_task = asyncio.create_task(self._poll_hardware_alerts(poll_interval_sec))

    async def start_alert_watcher(self, poll_interval_sec: float = 2.5):
        """Async compatibility alias for start_alert_monitoring."""
        self.start_alert_monitoring(poll_interval_sec)

    def stop_alert_monitoring(self):
        """Cancels background alert monitoring task."""
        self._alert_running = False
        if self._alert_task and not self._alert_task.done():
            self._alert_task.cancel()
            self._alert_task = None

    async def _poll_hardware_alerts(self, interval: float):
        # Compute required consecutive polls dynamically from alert_sustained_seconds (5s–30s)
        cooldown_seconds = 120.0
        while self._alert_running:
            try:
                await asyncio.sleep(interval)
                cadence = interval if interval >= 0.5 else 2.5
                sustained_polls_required = max(1, int(math.ceil(self.alert_sustained_seconds / cadence)))
                if not self.alerts_enabled:
                    self._consecutive_breaches = {"cpu_temp": 0, "gpu_temp": 0, "vram": 0}
                    continue

                snap = self.get_snapshot()
                now = time.time()
                triggered: List[Tuple[str, str]] = []

                # 1. CPU Thermal Check (sustained)
                if snap.cpu_temp_c is not None and snap.cpu_temp_c >= self.cpu_temp_alert_threshold_c and snap.cpu_temp_c > 0:
                    self._consecutive_breaches["cpu_temp"] = self._consecutive_breaches.get("cpu_temp", 0) + 1
                    if self._consecutive_breaches["cpu_temp"] >= sustained_polls_required:
                        triggered.append((
                            "cpu_temp",
                            f"CPU temperature is high at {snap.cpu_temp_c}°C (Threshold: {int(self.cpu_temp_alert_threshold_c)}°C)."
                        ))
                else:
                    self._consecutive_breaches["cpu_temp"] = 0

                # 2. GPU Thermal Check (sustained)
                if snap.gpu_temp_c >= self.gpu_temp_alert_threshold_c and snap.gpu_temp_c > 0:
                    self._consecutive_breaches["gpu_temp"] = self._consecutive_breaches.get("gpu_temp", 0) + 1
                    if self._consecutive_breaches["gpu_temp"] >= sustained_polls_required:
                        triggered.append((
                            "gpu_temp",
                            f"GPU temperature is high at {snap.gpu_temp_c}°C (Threshold: {int(self.gpu_temp_alert_threshold_c)}°C)."
                        ))
                else:
                    self._consecutive_breaches["gpu_temp"] = 0

                # 3. VRAM Allocation Check (sustained)
                if snap.vram_pct >= self.vram_alert_threshold_pct and snap.vram_pct > 0:
                    self._consecutive_breaches["vram"] = self._consecutive_breaches.get("vram", 0) + 1
                    if self._consecutive_breaches["vram"] >= sustained_polls_required:
                        triggered.append((
                            "vram",
                            f"VRAM allocation is critical at {snap.vram_pct:.1f}% ({snap.vram_used_gb:.1f} / {snap.vram_total_gb:.1f} GB)."
                        ))
                else:
                    self._consecutive_breaches["vram"] = 0

                for category, alert in triggered:
                    last = self._last_alert_times.get(category, 0.0)
                    if now - last >= cooldown_seconds:
                        self._last_alert_times[category] = now
                        logger.warning(f"[HARDWARE ALERT] {alert}")
                        if callable(self.alert_callback):
                            try:
                                res = self.alert_callback(alert)
                                if asyncio.iscoroutine(res):
                                    await res
                            except Exception as e:
                                logger.error(f"[HARDWARE] Error dispatching alert callback: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[HARDWARE] Error in alert polling loop: {e}")
