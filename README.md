# vmctl-ng

`vmctl-ng` is a small, opinionated CLI for controlling Proxmox VMs and LXCs over SSH from a jumpserver.

It’s built for homelabs where:

- Proxmox nodes live behind a firewall  
- all access goes through a single jump host  

And is useful if:
- you don't want to use the Proxmox API just to start/stop a machine
- you prefer using your terminal rather then the GUI, or the GUI is unavailable because of firewall settings.

It does one thing well: list, start, stop, and check the status of guests across multiple Proxmox nodes, safely and predictably, using either Proxmox ID numbers or names.

---

## Installation

Clone the repository and install in editable mode:

```bash
python -m pip install -e .
```

This installs the `vmctl` command and runs it directly from the source tree.

---

## Usage

Basic guest commands (VMs and LXCs are handled automatically):

```bash
vmctl start webserver1
vmctl stop webserver1
vmctl status webserver1
vmctl shutdown webserver1
vmctl reboot webserver1
```

You can also target guests by numeric ID:

```bash
vmctl status 101
```

Node operations (explicit confirmation required):

```bash
vmctl node-shutdown prxmx1
vmctl node-reboot prxmx1
```

List all guests across all nodes:

```bash
vmctl list
```

`vmctl list` is best-effort across nodes by default; use `--strict` to fail fast on the first unreachable node. In strict mode, vmctl returns the underlying error code (e.g. SSH failure, sudo failure).

Filter the list:

```bash
vmctl list --node prxmx1
vmctl list --running
vmctl list --stopped
```

---

## Configuration

Example configuration (`config.example.yaml`):

```yaml
defaults:
  port: 22
  user:
    name: vmctl
    identity_file: ~/.ssh/vmctl_ed25519
    identities_only: true
  ssh_options: []
nodes:
  prxmx1:
    host: 192.168.1.100
    vms:
      webserver1: 101
      fileserver1: 102
    lxcs:
      jumpbox1: 103
  prxmx2:
    host: 192.168.1.101
    user:
      name: admin
      identity_file: ~/.ssh/admin_ed25519
      identities_only: true
```

### Notes

- **Guest names must be unique across all nodes**
- Guest **IDs should be unique** across VMs and LXCs if you want to target guests by numeric ID
- `defaults.user` is required and contains `name`, `identity_file`, and `identities_only`
- `defaults.port` is optional (defaults to `22` if omitted)
- `defaults.ssh_options` is optional
- `vms` and `lxcs` sections are optional — a node may have only one or the other

---

## Config file lookup order

If `--config` is not provided, vmctl-ng looks for a config file in this order:

1. `./vmctl.yaml`  
2. `~/.config/vmctl-ng/config.yaml`  

---

## SSH and sudo behavior

All commands are executed remotely over SSH.

Guest actions run as:

```
ssh <user>@<host> "sudo -n qm <action> <vmid>"
ssh <user>@<host> "sudo -n pct <action> <ctid>"
```

Guest listing runs `qm list` and `pct list` together in a **single SSH session per node**, so sudo is requested **once per node**, not per command.

### Sudo authentication

- By default, vmctl-ng tries `sudo -n` (non-interactive)
- If a password is required, it prompts interactively and retries (disable with `--no-askpass`)
- You get **up to 3 attempts per node**
- Authentication is cached per node for the duration of the command

To disable prompting and fail immediately:

```bash
vmctl --no-askpass list
```

---

## Output format

Guest listing is grouped by node and formatted for readability:

```
NODE: prxmx1
  ID   NAME         STATUS   TYPE
  101  webserver1   running  VM
  102  fileserver1  stopped  VM
  103  jumpbox1     running  LXC
```

The output is intentionally human-oriented and stable, not designed for machine parsing.

---

## Manual test examples

```bash
vmctl status webserver1
vmctl status 102
vmctl status jumpbox1
vmctl list --node prxmx1 --running
```

---

## Author

Created by **Saar Yachin**.

Built for my own Proxmox homelab and shared publicly in case it’s useful to others.
