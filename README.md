# vmctl-ng

Minimal CLI for controlling Proxmox VMs via SSH from a jumpserver, with guest listing across VMs and LXCs.

## Install

```bash
python -m pip install -e .
```

## Usage

```bash
vmctl --config /path/to/config.yaml start webserver1
vmctl stop webserver1
vmctl status webserver1
vmctl list
vmctl vm list
```

Config search order (if `--config` is not set):

1) `./vmctl.yaml`
2) `~/.config/vmctl-ng/config.yaml`

## Config

Example (`config.example.yaml`):

```yaml
nodes:
  rook:
    host: 172.16.1.100
    user: admin
    port: 2222
    vms:
      webserver1: 101
      fileserver1: 102
    lxcs:
      vaultkeeper: 103
```

VM names must be unique across all nodes. The `port` field is optional and defaults to 22. The `vms` and `lxcs` sections are optional.

## SSH + sudo behavior

Commands run as:

```text
ssh <user>@<host> "sudo -n qm <action> <vmid>"
```

Listing guests runs:

```text
ssh <user>@<host> "sudo -n qm list"
ssh <user>@<host> "sudo -n pct list"
```

If `sudo -n` fails because a password is required, vmctl-ng prompts once for the sudo password by default and retries with `sudo -S`. Use `--no-askpass` to keep strict behavior and exit with an error instead.

## Manual test examples

```bash
vmctl vm list
vmctl status webserver1
vmctl list --node rook --running
```
