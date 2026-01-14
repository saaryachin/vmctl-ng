# vmctl-ng

`vmctl-ng` is a small, opinionated CLI for controlling **Proxmox VMs and LXCs** over SSH from a **jumpserver**.

It’s built for homelabs where:

- Proxmox nodes live behind a firewall  
- all access goes through a single jump host  
- you want **human names**, not VMIDs  
- and you don’t want to wire up the Proxmox API just to start or stop a machine  

It does one thing well: **list, start, stop, and check the status of guests across multiple Proxmox nodes**, safely and predictably.

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
```

You can also target guests by numeric ID:

```bash
vmctl status 101
```

List all guests across all nodes:

```bash
vmctl list
```

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
nodes:
  prxmx1:
    host: 192.168.1.100
    user: admin
    port: 2222
    vms:
      webserver1: 101
      fileserver1: 102
    lxcs:
      jumpbox1: 103
```

### Notes

- **Guest names must be unique across all nodes**
- Guest **IDs should be unique** across VMs and LXCs if you want to target guests by numeric ID
- `port` is optional (defaults to `22`)
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
- If a password is required, it prompts interactively and retries
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

## Design philosophy

- **Config-driven** — no hardcoded lab assumptions  
- **Name-first** — VMIDs are an implementation detail  
- **Safe by default** — no hidden sudo tricks, no silent failures  
- **Boring on purpose** — predictable behavior beats cleverness  

Sometimes SSH + sudo + good defaults are exactly what you want.

---

## Author

Created by **Saar Yachin**.

Built for my own Proxmox homelab and shared publicly in case it’s useful to others.
