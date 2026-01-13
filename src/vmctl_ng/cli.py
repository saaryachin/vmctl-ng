from __future__ import annotations

import argparse
import subprocess
import sys
from getpass import getpass
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


def _run_ssh_qm(
    host: str,
    user: str,
    port: int,
    qm_args: str,
    sudo_flags: str = "-n",
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    remote_cmd = f"sudo {sudo_flags} qm {qm_args}"
    return subprocess.run(
        ["ssh", "-p", str(port), f"{user}@{host}", remote_cmd],
        capture_output=True,
        text=True,
        input=input_text,
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
    result = _run_ssh_qm(node.host, node.user, node.port, f"{args.action} {vmid}")
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        return 0

    if _is_sudo_password_required(combined):
        if not args.askpass:
            _print_error(_sudo_required_message(node_name, node.host))
            return EXIT_SUDO
        password = getpass(f"Password for sudo on node '{node_name}' ({node.host}): ")
        retry = _run_ssh_qm(
            node.host,
            node.user,
            node.port,
            f"{args.action} {vmid}",
            sudo_flags="-S -p ''",
            input_text=f"{password}\n",
        )
        password = ""
        retry_output = (retry.stdout or "") + (retry.stderr or "")
        if retry.returncode == 0:
            if retry.stdout:
                print(retry.stdout, end="")
            return 0
        _print_error(retry_output.strip() or "Remote command failed")
        return EXIT_REMOTE

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE


def _handle_node_qm_list(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)
    node = config.nodes.get(args.node)
    if not node:
        _print_error(f"Unknown node: {args.node}")
        return EXIT_NOT_FOUND

    result = _run_ssh_qm(node.host, node.user, node.port, "list")
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        return 0

    if _is_sudo_password_required(combined):
        if not args.askpass:
            _print_error(_sudo_required_message(args.node, node.host))
            return EXIT_SUDO
        password = getpass(f"Password for sudo on node '{args.node}' ({node.host}): ")
        retry = _run_ssh_qm(
            node.host,
            node.user,
            node.port,
            "list",
            sudo_flags="-S -p ''",
            input_text=f"{password}\n",
        )
        password = ""
        retry_output = (retry.stdout or "") + (retry.stderr or "")
        if retry.returncode == 0:
            if retry.stdout:
                print(retry.stdout, end="")
            return 0
        _print_error(retry_output.strip() or "Remote command failed")
        return EXIT_REMOTE

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
    parser.add_argument(
        "--askpass",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prompt for sudo password if needed (default: enabled)",
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
