# vmctl-ng

Minimal CLI for controlling Proxmox VMs via SSH from a jumpserver.

## Install

```bash
python -m pip install -e .
```

## Usage

```bash
vmctl --config /path/to/config.yaml start jumpingrook
vmctl stop jumpingrook
vmctl status jumpingrook
vmctl node qm-list rook
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
    host: 172.16.1.150
    user: saar
    port: 53433
    vms:
      jumpingrook: 110
      portcullis: 120
```

VM names must be unique across all nodes. The `port` field is optional and defaults to 22.

## SSH + sudo behavior

Commands run as:

```text
ssh <user>@<host> "sudo -n qm <action> <vmid>"
```

If `sudo -n` fails because a password is required, vmctl-ng exits with a clear error. Configure passwordless sudo for the `qm` command.

## Manual test examples

```bash
vmctl vm list
vmctl status jumpingrook
```
