from __future__ import annotations

import importlib


_LABEL_OVERRIDES: dict[str, str] = {
    "cisco_ios": "Cisco IOS",
    "cisco_xe": "Cisco IOS-XE",
    "cisco_xr": "Cisco IOS-XR",
    "cisco_nxos": "Cisco NX-OS",
    "cisco_asa": "Cisco ASA",
    "huawei_vrp": "Huawei VRP",
    "hp_comware": "H3C Comware",
    "arista_eos": "Arista EOS",
    "juniper_junos": "Juniper Junos",
}


def _labelize(platform_id: str) -> str:
    if platform_id in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[platform_id]
    return platform_id.replace("_", " ")


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


_KEEP_KEYWORDS: tuple[str, ...] = ("cisco", "huawei", "juniper", "ruijie", "comware", "h3c")
_EXCLUDE_SUFFIXES: tuple[str, ...] = ("_ssh", "_telnet", "_serial")


def _is_kept(platform_id: str) -> bool:
    pid = (platform_id or "").strip().lower()
    if not pid:
        return False
    if not any(k in pid for k in _KEEP_KEYWORDS):
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


DEFAULT_COMMANDS: dict[str, str] = {
    "cisco_apic": "show running-config",
    "cisco_ios": "show running-config",
    "cisco_asa": "show running-config",
    "cisco_ftd": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_s200": "show running-config",
    "cisco_s300": "show running-config",
    "cisco_tp": "show running-config",
    "cisco_viptela": "show running-config",
    "cisco_wlc": "show run-config commands",
    "cisco_xe": "show running-config",
    "cisco_xr": "show running-config",
    "arista_eos": "show running-config",
    "huawei": "display current-configuration",
    "huawei_olt": "display current-configuration",
    "huawei_smartax": "display current-configuration",
    "huawei_smartaxmmi": "display current-configuration",
    "juniper_junos": "show configuration | display set",
    "juniper": "show configuration | display set",
    "juniper_screenos": "get config",
    "huawei_vrp": "display current-configuration",
    "huawei_vrpv8": "display current-configuration",
    "h3c_comware": "display current-configuration",
    "hp_comware": "display current-configuration",
    "ruijie_os": "show running-config",
}
