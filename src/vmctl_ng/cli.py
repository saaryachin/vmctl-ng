from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import ConfigError, find_config_path, load_config


EXIT_CONFIG = 2
EXIT_NOT_FOUND = 3
EXIT_REMOTE = 4
EXIT_SUDO = 5


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _sudo_required_message(node_name: str, host: str) -> str:
    return (
        "sudo password is required for qm commands. "
        f"Configure passwordless sudo for qm on node '{node_name}' ({host})."
    )


def _is_sudo_password_required(output: str) -> bool:
    lower = output.lower()
    return "sudo" in lower and "password" in lower and "required" in lower


def _run_ssh_qm(host: str, user: str, qm_args: str) -> subprocess.CompletedProcess[str]:
    remote_cmd = f"sudo -n qm {qm_args}"
    return subprocess.run(
        ["ssh", f"{user}@{host}", remote_cmd],
        capture_output=True,
        text=True,
    )


def _load_config_from_args(args: argparse.Namespace):
    try:
        config_path = find_config_path(args.config)
        return load_config(config_path)
    except ConfigError as exc:
        _print_error(str(exc))
        sys.exit(EXIT_CONFIG)


def _handle_vm_action(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)
    vm = config.vm_index.get(args.vmname)
    if not vm:
        _print_error(f"Unknown VM name: {args.vmname}")
        return EXIT_NOT_FOUND

    node_name, vmid = vm
    node = config.nodes[node_name]
    result = _run_ssh_qm(node.host, node.user, f"{args.action} {vmid}")
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        return 0

    if _is_sudo_password_required(combined):
        _print_error(_sudo_required_message(node_name, node.host))
        return EXIT_SUDO

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE


def _handle_node_qm_list(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)
    node = config.nodes.get(args.node)
    if not node:
        _print_error(f"Unknown node: {args.node}")
        return EXIT_NOT_FOUND

    result = _run_ssh_qm(node.host, node.user, "list")
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        return 0

    if _is_sudo_password_required(combined):
        _print_error(_sudo_required_message(args.node, node.host))
        return EXIT_SUDO

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE


def _handle_vm_list(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)
    print("NAME\tVMID\tNODE")
    for name in sorted(config.vm_index):
        node_name, vmid = config.vm_index[name]
        print(f"{name}\t{vmid}\t{node_name}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vmctl", description="Control Proxmox VMs via SSH")
    parser.add_argument(
        "--config",
        help="Path to config file (default: ./vmctl.yaml or ~/.config/vmctl-ng/config.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for action in ("start", "stop", "status"):
        sub = subparsers.add_parser(action, help=f"qm {action} <vmid>")
        sub.add_argument("vmname", help="VM name from config")
        sub.set_defaults(func=_handle_vm_action, action=action)

    node_parser = subparsers.add_parser("node", help="Node-scoped actions")
    node_sub = node_parser.add_subparsers(dest="node_command", required=True)

    node_qm = node_sub.add_parser("qm-list", help="Run qm list on a node")
    node_qm.add_argument("node", help="Node name from config")
    node_qm.set_defaults(func=_handle_node_qm_list)

    vm_parser = subparsers.add_parser("vm", help="VM-related actions")
    vm_sub = vm_parser.add_subparsers(dest="vm_command", required=True)

    vm_list = vm_sub.add_parser("list", help="List VMs from config")
    vm_list.set_defaults(func=_handle_vm_list)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    exit_code = args.func(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
