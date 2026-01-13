from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


DEFAULT_CONFIG_PATHS = [
    Path("vmctl.yaml"),
    Path("~/.config/vmctl-ng/config.yaml").expanduser(),
]


@dataclass(frozen=True)
class NodeConfig:
    name: str
    host: str
    user: str
    port: int = 22
    vms: dict[str, int]


@dataclass(frozen=True)
class Config:
    nodes: dict[str, NodeConfig]
    vm_index: dict[str, tuple[str, int]]


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _require_vms(value: Any, label: str) -> dict[str, int]:
    vms = _require_mapping(value, label)
    normalized: dict[str, int] = {}
    for name, vmid in vms.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{label} keys must be non-empty strings")
        if not isinstance(vmid, int):
            raise ConfigError(f"VM id for '{name}' must be an integer")
        normalized[name] = vmid
    return normalized


def _require_port(value: Any, label: str) -> int:
    if value is None:
        return 22
    if not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer")
    return value


def find_config_path(override: str | None) -> Path:
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return path

    for path in DEFAULT_CONFIG_PATHS:
        if path.is_file():
            return path
    paths = ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
    raise ConfigError(f"No config file found. Tried: {paths}")


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError(f"Failed to read config: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc}") from exc

    if raw is None:
        raise ConfigError("Config is empty")

    root = _require_mapping(raw, "root")
    nodes_raw = _require_mapping(root.get("nodes"), "nodes")

    nodes: dict[str, NodeConfig] = {}
    vm_index: dict[str, tuple[str, int]] = {}

    for node_name, node_data in nodes_raw.items():
        if not isinstance(node_name, str) or not node_name.strip():
            raise ConfigError("Node names must be non-empty strings")
        node_map = _require_mapping(node_data, f"nodes.{node_name}")
        host = _require_str(node_map.get("host"), f"nodes.{node_name}.host")
        user = _require_str(node_map.get("user"), f"nodes.{node_name}.user")
        port = _require_port(node_map.get("port"), f"nodes.{node_name}.port")
        vms = _require_vms(node_map.get("vms"), f"nodes.{node_name}.vms")

        node_cfg = NodeConfig(name=node_name, host=host, user=user, port=port, vms=vms)
        nodes[node_name] = node_cfg

        for vm_name, vmid in vms.items():
            if vm_name in vm_index:
                prev_node = vm_index[vm_name][0]
                raise ConfigError(
                    f"VM name '{vm_name}' is duplicated in nodes '{prev_node}' and '{node_name}'"
                )
            vm_index[vm_name] = (node_name, vmid)

    if not nodes:
        raise ConfigError("No nodes configured")

    return Config(nodes=nodes, vm_index=vm_index)
