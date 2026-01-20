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
    identity_file: str | None = None,
    identities_only: bool = False,
    ssh_options: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    remote_cmd = f"sudo {sudo_flags} {command}"
    ssh_cmd = ["ssh", "-p", str(port)]
    if identity_file:
        ssh_cmd.extend(["-i", identity_file])
    if identities_only:
        ssh_cmd.extend(["-o", "IdentitiesOnly=yes"])
    if ssh_options:
        for opt in ssh_options:
            if opt.startswith("-"):
                ssh_cmd.append(opt)
            else:
                ssh_cmd.extend(["-o", opt])
    ssh_cmd.append(f"{user}@{host}")
    ssh_cmd.append(remote_cmd)
    return subprocess.run(
        ssh_cmd,
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
    emit_errors: bool = True,
) -> tuple[int, str, str]:
    for attempt in range(1, 4):
        password = getpass(f"Password for sudo on node '{node_name}' ({node.host}): ")
        retry = _run_ssh_sudo_command(
            node.host,
            node.user,
            node.port,
            command,
            sudo_flags="-S -p ''",
            input_text=f"{password}\n",
            identity_file=node.identity_file,
            identities_only=node.identities_only,
            ssh_options=node.ssh_options,
        )
        password = ""
        retry_output = (retry.stdout or "") + (retry.stderr or "")
        if retry.returncode == 0:
            return 0, retry.stdout or "", ""
        if _is_sudo_auth_failed(retry_output):
            if attempt < 3:
                continue
            message = (
                f"sudo authentication failed after 3 attempts on node '{node_name}' ({node.host})"
            )
            if emit_errors:
                _print_error(message)
            return EXIT_SUDO, "", message
        message = retry_output.strip() or "Remote command failed"
        if emit_errors:
            _print_error(message)
        return EXIT_REMOTE, "", message
    message = _sudo_required_message(command_label, node_name, node.host)
    if emit_errors:
        _print_error(message)
    return EXIT_SUDO, "", message


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
        identity_file=node.identity_file,
        identities_only=node.identities_only,
        ssh_options=node.ssh_options,
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
        retry_code, retry_stdout, _retry_error = _run_sudo_with_password_retry(
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


def _run_remote_command_with_askpass(
    args: argparse.Namespace,
    node_name: str,
    node,
    command: str,
    command_label: str,
    emit_errors: bool = True,
) -> tuple[int, str, str]:
    result = _run_ssh_sudo_command(
        node.host,
        node.user,
        node.port,
        command,
        identity_file=node.identity_file,
        identities_only=node.identities_only,
        ssh_options=node.ssh_options,
    )
    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        return 0, result.stdout or "", ""

    if _is_sudo_password_required(combined):
        if not args.askpass:
            message = _sudo_required_message(command_label, node_name, node.host)
            if emit_errors:
                _print_error(message)
            return EXIT_SUDO, "", message
        return _run_sudo_with_password_retry(
            args,
            node_name,
            node,
            command,
            command_label,
            emit_errors=emit_errors,
        )

    message = combined.strip() or "Remote command failed"
    if emit_errors:
        _print_error(message)
    return EXIT_REMOTE, "", message


def _run_remote_qm_list(
    args: argparse.Namespace,
    node_name: str,
    node,
    emit_errors: bool = True,
) -> tuple[int, str, str]:
    return _run_remote_command_with_askpass(
        args,
        node_name,
        node,
        "/usr/sbin/qm list",
        "qm",
        emit_errors=emit_errors,
    )


def _run_remote_pct_list(
    args: argparse.Namespace,
    node_name: str,
    node,
    emit_errors: bool = True,
) -> tuple[int, str, str]:
    return _run_remote_command_with_askpass(
        args,
        node_name,
        node,
        "/usr/sbin/pct list",
        "pct",
        emit_errors=emit_errors,
    )


def _run_remote_pct_status(
    args: argparse.Namespace,
    node_name: str,
    node,
    ctid: int,
    emit_errors: bool = True,
) -> tuple[int, str, str]:
    return _run_remote_command_with_askpass(
        args,
        node_name,
        node,
        f"/usr/sbin/pct status {ctid}",
        "pct",
        emit_errors=emit_errors,
    )


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
    failures: list[tuple[str, str, int, str]] = []
    for node_name in sorted(nodes):
        node = nodes[node_name]
        qm_code, qm_output, qm_error = _run_remote_qm_list(
            args,
            node_name,
            node,
            emit_errors=args.strict,
        )
        if qm_code != 0:
            if args.strict:
                return qm_code
            failures.append((node_name, node.host, node.port, qm_error or "Remote command failed"))
            continue
        pct_code, pct_output, pct_error = _run_remote_pct_list(
            args,
            node_name,
            node,
            emit_errors=args.strict,
        )
        if pct_code != 0:
            if args.strict:
                return pct_code
            failures.append((node_name, node.host, node.port, pct_error or "Remote command failed"))
            continue
        vm_statuses = _parse_status_map(qm_output)
        for name, vmid in node.vms.items():
            status = vm_statuses.get(vmid, "unknown")
            guests.append((node_name, vmid, name, status, "VM"))

        lxc_statuses = _parse_status_map(pct_output)
        for name, vmid in node.lxcs.items():
            status = lxc_statuses.get(vmid, "unknown")
            if status.lower() == "unknown":
                status_code, status_output, status_error = _run_remote_pct_status(
                    args,
                    node_name,
                    node,
                    vmid,
                    emit_errors=args.strict,
                )
                if status_code != 0:
                    if args.strict:
                        return status_code
                    failures.append(
                        (node_name, node.host, node.port, status_error or "Remote command failed")
                    )
                    continue
                fallback_status = _parse_pct_status(status_output)
                if fallback_status:
                    status = fallback_status
            guests.append((node_name, vmid, name, status, "LXC"))

    if args.running:
        guests = [guest for guest in guests if guest[3].lower() == "running"]
    elif args.stopped:
        guests = [guest for guest in guests if guest[3].lower() == "stopped"]

    guests.sort(key=lambda guest: (guest[0], guest[1]))

    node_order: list[str] = []
    rows_by_node: dict[str, list[tuple[int, str, str, str]]] = {}
    for node_name, vmid, name, status, guest_type in guests:
        if node_name not in rows_by_node:
            rows_by_node[node_name] = []
            node_order.append(node_name)
        rows_by_node[node_name].append((vmid, name, status, guest_type))

    for idx, node_name in enumerate(node_order):
        rows = rows_by_node[node_name]
        id_width = max([len("ID")] + [len(str(row[0])) for row in rows])
        name_width = max([len("NAME")] + [len(row[1]) for row in rows])
        status_width = max([len("STATUS")] + [len(row[2]) for row in rows])
        type_width = max([len("TYPE")] + [len(row[3]) for row in rows])

        print(f"NODE: {node_name}")
        header = (
            f"{'ID'.ljust(id_width)} "
            f"{'NAME'.ljust(name_width)} "
            f"{'STATUS'.ljust(status_width)} "
            f"{'TYPE'.ljust(type_width)}"
        )
        print(f"  {header}")
        for vmid, name, status, guest_type in rows:
            line = (
                f"{str(vmid).ljust(id_width)} "
                f"{name.ljust(name_width)} "
                f"{status.ljust(status_width)} "
                f"{guest_type.ljust(type_width)}"
            )
            print(f"  {line}")
        if idx != len(node_order) - 1:
            print()

    if failures:
        if node_order:
            print()
        print("FAILED NODES")
        for node_name, host, port, message in failures:
            print(f"  {node_name} ({host}:{port}): {message}")
        if not node_order:
            print("No nodes reachable.")
        return 1
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
        default=False,
        help="Prompt for sudo password if needed (default: disabled)",
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
    list_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast if any node is unreachable",
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
