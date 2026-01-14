from __future__ import annotations

import argparse
import subprocess
import sys
from getpass import getpass

from .config import ConfigError, find_config_path, load_config


EXIT_CONFIG = 2
EXIT_NOT_FOUND = 3
EXIT_REMOTE = 4
EXIT_SUDO = 5


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _sudo_required_message(command_label: str, node_name: str, host: str) -> str:
    return (
        f"sudo password is required for {command_label} commands. "
        f"Configure passwordless sudo for {command_label} on node '{node_name}' ({host})."
    )


def _is_sudo_password_required(output: str) -> bool:
    lower = output.lower()
    return "sudo" in lower and "password" in lower and "required" in lower


def _is_sudo_auth_failed(output: str) -> bool:
    lower = output.lower()
    return (
        "sorry, try again" in lower
        or "incorrect password" in lower
        or "authentication failure" in lower
    )


def _run_ssh_sudo_command(
    host: str,
    user: str,
    port: int,
    command: str,
    sudo_flags: str = "-n",
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    remote_cmd = f"sudo {sudo_flags} {command}"
    return subprocess.run(
        ["ssh", "-p", str(port), f"{user}@{host}", remote_cmd],
        capture_output=True,
        text=True,
        input=input_text,
    )


def _run_sudo_with_password_retry(
    args: argparse.Namespace,
    node_name: str,
    node,
    command: str,
    command_label: str,
) -> tuple[int, str]:
    for attempt in range(1, 4):
        password = getpass(f"Password for sudo on node '{node_name}' ({node.host}): ")
        retry = _run_ssh_sudo_command(
            node.host,
            node.user,
            node.port,
            command,
            sudo_flags="-S -p ''",
            input_text=f"{password}\n",
        )
        password = ""
        retry_output = (retry.stdout or "") + (retry.stderr or "")
        if retry.returncode == 0:
            return 0, retry.stdout or ""
        if _is_sudo_auth_failed(retry_output):
            if attempt < 3:
                continue
            _print_error(
                f"sudo authentication failed after 3 attempts on node '{node_name}' ({node.host})"
            )
            return EXIT_SUDO, ""
        _print_error(retry_output.strip() or "Remote command failed")
        return EXIT_REMOTE, ""
    _print_error(_sudo_required_message(command_label, node_name, node.host))
    return EXIT_SUDO, ""


def _load_config_from_args(args: argparse.Namespace):
    try:
        config_path = find_config_path(args.config)
        return load_config(config_path)
    except ConfigError as exc:
        _print_error(str(exc))
        sys.exit(EXIT_CONFIG)


def _resolve_guest_target(
    config,
    target: str,
) -> tuple[str, str, int]:
    if target.isdigit():
        target_id = int(target)
        matches: list[tuple[str, str, int]] = []
        for node_name, node in config.nodes.items():
            for name, vmid in node.vms.items():
                if vmid == target_id:
                    matches.append((node_name, "VM", vmid))
            for name, ctid in node.lxcs.items():
                if ctid == target_id:
                    matches.append((node_name, "LXC", ctid))
        if not matches:
            _print_error(f"Unknown guest ID: {target}")
            raise SystemExit(EXIT_NOT_FOUND)
        if len(matches) > 1:
            _print_error(f"Guest ID is not unique: {target}")
            raise SystemExit(EXIT_NOT_FOUND)
        return matches[0]

    matches_by_name: list[tuple[str, str, int]] = []
    for node_name, node in config.nodes.items():
        if target in node.vms:
            matches_by_name.append((node_name, "VM", node.vms[target]))
        if target in node.lxcs:
            matches_by_name.append((node_name, "LXC", node.lxcs[target]))
    if not matches_by_name:
        _print_error(f"Unknown guest name: {target}")
        raise SystemExit(EXIT_NOT_FOUND)
    if len(matches_by_name) > 1:
        _print_error(f"Guest name is not unique: {target}")
        raise SystemExit(EXIT_NOT_FOUND)
    return matches_by_name[0]


def _handle_vm_action(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)

    node_name, guest_type, guest_id = _resolve_guest_target(config, args.vmname)
    node = config.nodes[node_name]
    command = "qm" if guest_type == "VM" else "pct"
    result = _run_ssh_sudo_command(
        node.host,
        node.user,
        node.port,
        f"{command} {args.action} {guest_id}",
    )
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        if result.stdout:
            print(result.stdout, end="")
        return 0

    if _is_sudo_password_required(combined):
        if not args.askpass:
            _print_error(_sudo_required_message(command, node_name, node.host))
            return EXIT_SUDO
        retry_code, retry_stdout = _run_sudo_with_password_retry(
            args,
            node_name,
            node,
            f"{command} {args.action} {guest_id}",
            command,
        )
        if retry_code == 0:
            if retry_stdout:
                print(retry_stdout, end="")
        return retry_code

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE


def _run_remote_list_command(
    args: argparse.Namespace,
    node_name: str,
    node,
    command: str,
    command_label: str,
) -> tuple[int, str]:
    result = _run_ssh_sudo_command(node.host, node.user, node.port, command)
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        return 0, result.stdout or ""

    if _is_sudo_password_required(combined):
        if not args.askpass:
            _print_error(_sudo_required_message(command_label, node_name, node.host))
            return EXIT_SUDO, ""
        return _run_sudo_with_password_retry(
            args,
            node_name,
            node,
            command,
            command_label,
        )

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE, ""


def _parse_guest_table(output: str) -> list[tuple[int, str, str]]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return []
    header_tokens = [token.upper() for token in lines[0].split()]
    header_map = {name: idx for idx, name in enumerate(header_tokens)}

    def _find_index(candidates: tuple[str, ...]) -> int | None:
        for candidate in candidates:
            if candidate in header_map:
                return header_map[candidate]
        return None

    id_idx = _find_index(("VMID", "CTID", "ID"))
    name_idx = _find_index(("NAME",))
    status_idx = _find_index(("STATUS", "STATE"))
    if id_idx is None or name_idx is None or status_idx is None:
        return []

    rows: list[tuple[int, str, str]] = []
    max_idx = max(id_idx, name_idx, status_idx)
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max_idx:
            continue
        try:
            vmid = int(parts[id_idx])
        except ValueError:
            continue
        name = parts[name_idx]
        status = parts[status_idx]
        rows.append((vmid, name, status))
    return rows


def _parse_status_map(output: str) -> dict[int, str]:
    statuses: dict[int, str] = {}
    for vmid, _name, status in _parse_guest_table(output):
        statuses[vmid] = status
    return statuses


def _parse_pct_status(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("status:"):
            return stripped.split(":", 1)[1].strip()
    return None


def _build_list_script(lxc_ids: list[int]) -> str:
    lines = [
        'qm_list=$(qm list)',
        'printf "__VMCTL_QM__\\n%s\\n" "$qm_list"',
        'pct_list=$(pct list)',
        'printf "__VMCTL_PCT__\\n%s\\n" "$pct_list"',
    ]
    if lxc_ids:
        joined_ids = " ".join(str(ctid) for ctid in lxc_ids)
        lines.extend(
            [
                f"for id in {joined_ids}; do",
                '  if ! printf "%s\\n" "$pct_list" | awk "NR>1 {print \\$1}" | grep -qx "$id"; then',
                '    echo "__VMCTL_PCT_STATUS__${id}"',
                '    pct status "$id"',
                "  fi",
                "done",
            ]
        )
    script = "\n".join(lines)
    return f"sh -c '{script}'"


def _run_remote_list_bundle(
    args: argparse.Namespace,
    node_name: str,
    node,
    lxc_ids: list[int],
) -> tuple[int, str]:
    command = _build_list_script(lxc_ids)
    result = _run_ssh_sudo_command(node.host, node.user, node.port, command)
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        return 0, result.stdout or ""

    if _is_sudo_password_required(combined):
        if not args.askpass:
            _print_error(_sudo_required_message("qm", node_name, node.host))
            return EXIT_SUDO, ""
        return _run_sudo_with_password_retry(
            args,
            node_name,
            node,
            command,
            "qm",
        )

    _print_error(combined.strip() or "Remote command failed")
    return EXIT_REMOTE, ""


def _split_list_bundle(output: str) -> tuple[str, str, dict[int, str]]:
    qm_lines: list[str] = []
    pct_lines: list[str] = []
    pct_status: dict[int, list[str]] = {}
    current: str | None = None
    current_id: int | None = None

    for line in output.splitlines():
        if line == "__VMCTL_QM__":
            current = "qm"
            current_id = None
            continue
        if line == "__VMCTL_PCT__":
            current = "pct"
            current_id = None
            continue
        if line.startswith("__VMCTL_PCT_STATUS__"):
            current = "pct_status"
            current_id = int(line.split("__VMCTL_PCT_STATUS__", 1)[1])
            pct_status[current_id] = []
            continue
        if current == "qm":
            qm_lines.append(line)
        elif current == "pct":
            pct_lines.append(line)
        elif current == "pct_status" and current_id is not None:
            pct_status[current_id].append(line)

    pct_status_text = {ctid: "\n".join(lines) for ctid, lines in pct_status.items()}
    return "\n".join(qm_lines), "\n".join(pct_lines), pct_status_text


def _handle_list(args: argparse.Namespace) -> int:
    config = _load_config_from_args(args)
    if args.node:
        node = config.nodes.get(args.node)
        if not node:
            _print_error(f"Unknown node: {args.node}")
            return EXIT_NOT_FOUND
        nodes = {args.node: node}
    else:
        nodes = config.nodes

    guests: list[tuple[str, int, str, str, str]] = []
    for node_name in sorted(nodes):
        node = nodes[node_name]
        exit_code, combined_output = _run_remote_list_bundle(
            args,
            node_name,
            node,
            list(node.lxcs.values()),
        )
        if exit_code != 0:
            return exit_code
        qm_output, pct_output, pct_status_outputs = _split_list_bundle(combined_output)
        vm_statuses = _parse_status_map(qm_output)
        for name, vmid in node.vms.items():
            status = vm_statuses.get(vmid, "unknown")
            guests.append((node_name, vmid, name, status, "VM"))

        lxc_statuses = _parse_status_map(pct_output)
        for name, vmid in node.lxcs.items():
            status = lxc_statuses.get(vmid, "unknown")
            if status.lower() == "unknown":
                fallback_status = _parse_pct_status(pct_status_outputs.get(vmid, ""))
                if fallback_status:
                    status = fallback_status
            guests.append((node_name, vmid, name, status, "LXC"))

    if args.running:
        guests = [guest for guest in guests if guest[3].lower() == "running"]
    elif args.stopped:
        guests = [guest for guest in guests if guest[3].lower() == "stopped"]

    guests.sort(key=lambda guest: (guest[0], guest[1]))

    print("NODE\tID\tNAME\tSTATUS\tTYPE")
    for node_name, vmid, name, status, guest_type in guests:
        print(f"{node_name}\t{vmid}\t{name}\t{status}\t{guest_type}")
    return 0


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
        sub.add_argument("vmname", help="Guest name or numeric ID")
        sub.set_defaults(func=_handle_vm_action, action=action)

    list_parser = subparsers.add_parser("list", help="List VMs and LXCs across nodes")
    list_parser.add_argument(
        "-n",
        "--node",
        help="Restrict listing to a single node",
    )
    list_filter = list_parser.add_mutually_exclusive_group()
    list_filter.add_argument(
        "--running",
        action="store_true",
        help="Show only running guests",
    )
    list_filter.add_argument(
        "--stopped",
        action="store_true",
        help="Show only stopped guests",
    )
    list_parser.set_defaults(func=_handle_list)

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
