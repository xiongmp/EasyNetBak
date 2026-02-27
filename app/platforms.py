from __future__ import annotations

import importlib


def _labelize(platform_id: str) -> str:
    return (platform_id or "").strip()


def _load_netmiko_platforms() -> list[str]:
    try:
        sd = importlib.import_module("netmiko.ssh_dispatcher")
        platforms = getattr(sd, "platforms", None)
        if isinstance(platforms, (list, tuple, set)):
            out = [str(x) for x in platforms if str(x).strip()]
            out.sort()
            return out
    except Exception:
        return []
    return []


def _load_telnet_device_type_map() -> dict[str, str]:
    try:
        sd = importlib.import_module("netmiko.ssh_dispatcher")
        class_mapper = getattr(sd, "CLASS_MAPPER", None)
        if not isinstance(class_mapper, dict):
            return {}
        out: dict[str, str] = {}
        for key in class_mapper:
            key_str = str(key).strip()
            if not key_str.endswith("_telnet"):
                continue
            base = key_str[: -len("_telnet")]
            if not base:
                continue
            out[base] = key_str
        return out
    except Exception:
        return {}


_EXCLUDE_SUFFIXES: tuple[str, ...] = ("_ssh", "_telnet", "_serial")


def _is_kept(platform_id: str) -> bool:
    pid = (platform_id or "").strip().lower()
    if not pid:
        return False
    if any(pid.endswith(sfx) for sfx in _EXCLUDE_SUFFIXES):
        return False
    return True


_BASE_PLATFORMS: list[str] = [
    "cisco_ios",
    "cisco_nxos",
    "cisco_xe",
    "cisco_xr",
    "cisco_asa",
    "huawei_vrp",
    "huawei_vrpv8",
    "hp_comware",
    "h3c_comware",
    "juniper_junos",
    "juniper_screenos",
    "ruijie_os",
]
_NETMIKO_PLATFORMS = [p for p in _load_netmiko_platforms() if _is_kept(p)]
_ALL_PLATFORM_IDS = sorted({*filter(_is_kept, _BASE_PLATFORMS), *_NETMIKO_PLATFORMS})

_TELNET_MAP_RAW = _load_telnet_device_type_map()
TELNET_DEVICE_TYPE_MAP: dict[str, str] = {
    base: telnet
    for base, telnet in _TELNET_MAP_RAW.items()
    if base in _ALL_PLATFORM_IDS
}
TELNET_PLATFORM_BASE_IDS: list[str] = sorted(TELNET_DEVICE_TYPE_MAP.keys())
TELNET_PLATFORM_IDS: list[str] = sorted({*TELNET_DEVICE_TYPE_MAP.values()})

PLATFORMS: list[dict[str, str]] = [{"id": pid, "label": _labelize(pid)} for pid in _ALL_PLATFORM_IDS]

TELNET_PLATFORMS: list[dict[str, str]] = [
    {"id": TELNET_DEVICE_TYPE_MAP[base], "label": f"{_labelize(base)} Telnet"} for base in TELNET_PLATFORM_BASE_IDS
]


def normalize_platform_id(platform_id: str) -> str:
    pid = (platform_id or "").strip()
    for sfx in _EXCLUDE_SUFFIXES:
        if pid.endswith(sfx):
            return pid[: -len(sfx)]
    return pid


def to_netmiko_device_type(platform_id: str, login_method: str) -> str:
    pid = normalize_platform_id(platform_id)
    lm = (login_method or "").strip().lower()
    if lm == "telnet":
        return TELNET_DEVICE_TYPE_MAP.get(pid, pid + "_telnet")
    return pid


def platforms_compatible(a: str, b: str) -> bool:
    return normalize_platform_id(a) == normalize_platform_id(b)


def _default_command_for(pid: str) -> str:
    p = pid.lower()
    if "cisco" in p and "wlc" in p:
        return "show run-config commands"
    if any(k in p for k in ("juniper", "junos")) and "screenos" not in p:
        return "show configuration | display set"
    if "screenos" in p:
        return "get config"
    if any(k in p for k in ("huawei", "vrp")):
        return "display current-configuration"
    if any(k in p for k in ("comware", "h3c", "hp_comware")):
        return "display current-configuration"
    if any(k in p for k in ("vyos", "vyatta")):
        return "show configuration commands"
    if any(k in p for k in ("mikrotik", "routeros", "ros")):
        return "export"
    if any(k in p for k in ("f5", "ltm", "bigip", "tmsh")):
        return "tmsh list /"
    if any(k in p for k in ("fortinet", "fortios", "fortigate")):
        return "show full-configuration"
    if any(k in p for k in ("paloalto", "panos", "pan")):
        return "show config running"
    if any(k in p for k in ("checkpoint", "gaia")):
        return "show configuration"
    if any(k in p for k in ("extreme", "exos")):
        return "show configuration"
    if any(k in p for k in ("dell", "os6", "os9", "os10", "force10", "powerconnect")):
        return "show running-configuration"
    if any(k in p for k in ("arista", "eos", "ruijie", "brocade_fastiron", "icx", "netiron", "lenovo_cnos", "cnos", "quanta", "qnos", "hpe_procurve", "procurve", "aruba", "hp_procurve")):
        return "show running-config"
    if any(k in p for k in ("extremexos", "aos", "omniswitch")):
        return "show configuration"
    return "show running-config"


DEFAULT_COMMANDS: dict[str, str] = {pid: _default_command_for(pid) for pid in _ALL_PLATFORM_IDS}
