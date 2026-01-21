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
    user: "UserConfig"
    vms: dict[str, int]
    lxcs: dict[str, int]
    ssh_options: list[str]
    port: int = 22


@dataclass(frozen=True)
class Config:
    nodes: dict[str, NodeConfig]
    vm_index: dict[str, tuple[str, int]]
    defaults: "DefaultsConfig"


@dataclass(frozen=True)
class UserConfig:
    name: str
    identity_file: str
    identities_only: bool


@dataclass(frozen=True)
class DefaultsConfig:
    port: int
    user: UserConfig
    ssh_options: list[str]


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _require_vms(value: Any, label: str) -> dict[str, int]:
    if value is None:
        return {}
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


def _require_bool(value: Any, label: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _require_ssh_options(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be a list")
    options: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{label} entries must be non-empty strings")
        options.append(item)
    return options


def _require_user(value: Any, label: str) -> UserConfig:
    user_map = _require_mapping(value, label)
    name = _require_str(user_map.get("name"), f"{label}.name")
    identity_file = _require_str(user_map.get("identity_file"), f"{label}.identity_file")
    identities_only = _require_bool(user_map.get("identities_only"), f"{label}.identities_only")
    return UserConfig(
        name=name,
        identity_file=str(Path(identity_file).expanduser()),
        identities_only=identities_only,
    )


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
    defaults_raw = _require_mapping(root.get("defaults"), "defaults")
    if "identity_file" in defaults_raw or "identities_only" in defaults_raw:
        raise ConfigError(
            "defaults.identity_file and defaults.identities_only are not supported in v2; "
            "use defaults.user"
        )
    defaults_user_raw = defaults_raw.get("user")
    if defaults_user_raw is None:
        raise ConfigError("defaults.user is required")
    if not isinstance(defaults_user_raw, dict):
        raise ConfigError("defaults.user must be a mapping with name/identity_file/identities_only")
    defaults_user = _require_user(defaults_user_raw, "defaults.user")
    defaults_port = _require_port(defaults_raw.get("port"), "defaults.port")
    defaults_ssh_options = _require_ssh_options(defaults_raw.get("ssh_options"), "defaults.ssh_options")
    defaults = DefaultsConfig(
        port=defaults_port,
        user=defaults_user,
        ssh_options=defaults_ssh_options,
    )

    nodes: dict[str, NodeConfig] = {}
    vm_index: dict[str, tuple[str, int]] = {}

    for node_name, node_data in nodes_raw.items():
        if not isinstance(node_name, str) or not node_name.strip():
            raise ConfigError("Node names must be non-empty strings")
        node_map = _require_mapping(node_data, f"nodes.{node_name}")
        if "identity_file" in node_map or "identities_only" in node_map:
            raise ConfigError(
                f"nodes.{node_name} uses deprecated keys identity_file/identities_only; "
                "use user.name/user.identity_file/user.identities_only"
            )
        host = _require_str(node_map.get("host"), f"nodes.{node_name}.host")
        user_block = node_map.get("user", defaults.user)
        if isinstance(user_block, UserConfig):
            user = user_block
        else:
            if not isinstance(user_block, dict):
                raise ConfigError(
                    f"nodes.{node_name}.user must be a mapping with name/identity_file/identities_only"
                )
            user = _require_user(user_block, f"nodes.{node_name}.user")
        port = _require_port(node_map.get("port", defaults.port), f"nodes.{node_name}.port")
        ssh_options = _require_ssh_options(
            node_map.get("ssh_options", defaults.ssh_options),
            f"nodes.{node_name}.ssh_options",
        )
        vms = _require_vms(node_map.get("vms"), f"nodes.{node_name}.vms")
        lxcs = _require_vms(node_map.get("lxcs"), f"nodes.{node_name}.lxcs")

        node_cfg = NodeConfig(
            name=node_name,
            host=host,
            user=user,
            port=port,
            vms=vms,
            lxcs=lxcs,
            ssh_options=ssh_options,
        )
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

    return Config(nodes=nodes, vm_index=vm_index, defaults=defaults)
