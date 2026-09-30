# Rack-Scale Deployment

**[Overview](#overview)** | **[Quick Start](#quick-start)** | **[rack-profile.yaml](#writing-rack-profileyaml)** | **[Star](#star-topology)** | **[Clos](#clos-topology)** | **[GPU](#gpu-nodes)** | **[Validation](#validation)** | **[Troubleshooting](#troubleshooting)**

---

Deploy ai-solutions on multi-node rack-scale hardware with high-speed fabric
networking, Ceph storage on fabric, and optional GPU acceleration.

## Overview

Rack-scale mode adds 100G fabric networking to the infrastructure layer. It is
**disabled by default** (`rack_scale_enabled: false`) — when off, all fabric
components are skipped and the stack behaves identically to a standard
deployment. When enabled, the installer reads `rack-profile.yaml` and
automatically configures:

- **Fabric NICs** — persistent IP addressing via netplan on every node
- **Hub forwarding** (star) or **ECMP + BGP** (Clos) — topology-appropriate routing
- **Ceph on fabric** — OSD traffic binds to the high-speed network
- **GPU operator** — auto-installs when `accelerator:` is declared (no separate flag)
- **Node labels** — silicon family, GPU vendor/model/form applied to K8s nodes

Two topologies are supported from the same `rack-profile.yaml` schema:

| Topology | Use Case | Switching | Calico Mode |
|----------|----------|-----------|-------------|
| **Star** | Pilots, POC, switchless (e.g. HPE B300 HGX) | None — direct cables | VXLAN |
| **Clos** | Production, full HA, max throughput | Leaf/spine ToR switches | BGP |

## Prerequisites

- All [standard ai-solutions prerequisites](GETTING_STARTED_RAG_DEV.md#prerequisites)
- **Hardware:** 100G NICs on all nodes (check with `ip link show`)
  - Star: direct cables between hub and each node
  - Clos: cables to ToR leaf switches
- **Network plan:** fabric IP addresses, subnet layout, cable map
- **Storage (optional):** raw NVMe devices for Ceph OSDs (not the OS disk)
- **GPU (optional):** nodes with NVIDIA or Intel GPUs

---

## Quick Start

```bash
git clone https://github.com/intel-innersource/applications.ai.enterprise.ai-solutions.git
cd applications.ai.enterprise.ai-solutions

./es_auto_installer.sh configure

# Create environment and enable rack-scale
./es_auto_installer.sh init inference --env rack
vim env/rack/global_config.yaml           # set rack_scale_enabled: true, proxy if needed

# Write your hardware profile
cp configs/defaults/rack-profile.yaml.example env/rack/rack-profile.yaml
vim env/rack/rack-profile.yaml            # edit for your hardware — see below

# Deploy
./es_auto_installer.sh install inference --env rack

# Validate
./es_auto_installer.sh validate inference --env rack
```

> [!Note]
> `rack-profile.yaml` replaces `nodes.yaml` for rack-scale. Leave `nodes.yaml`
> empty (or don't create it) — the fabric_inventory role generates the Kubespray
> inventory from your rack profile.

---

## Writing rack-profile.yaml

Copy the example and edit for your hardware:

```bash
cp configs/defaults/rack-profile.yaml.example env/<env>/rack-profile.yaml
```

### Site

```yaml
site:
  name: my-datacenter       # identifier for logs and reports
```

### Network

```yaml
network:
  topology: star             # star | clos
  calico_mode: vxlan         # vxlan (star) | bgp (clos)
  pod_cidr: 10.244.0.0/16
  service_cidr: 10.233.0.0/18
  mtu: 9000                  # jumbo frames — must match switch and NIC config
  fabric_prefix: 30          # /30 for star point-to-point, /16 for clos
  netplan_renderer: networkd # networkd | NetworkManager
  control_plane_on_fabric: true  # bind kubelet + etcd to fabric IP (recommended)
```

### SSH Access

```yaml
access:
  ansible_user: ubuntu
  ansible_ssh_private_key_file: ~/.ssh/id_ed25519
```

Per-node overrides are supported — set `ansible_user` or
`ansible_ssh_private_key_file` directly on any node.

### Node Profiles (optional)

Define silicon once, reference from nodes via `profile:`:

```yaml
node_profiles:
  cpu-gnr-ap-6980P:
    node_family: cpu-gnr-ap
    sku: 6980P
  gpu-b300-hgx:
    node_family: cpu-gnr-sp
    sku: 6776P
    accelerator: nvidia-b300-hgx    # format: vendor-model[-form]
    gpu_count: 8
```

### Nodes

Nodes are grouped under racks. Each node needs:

```yaml
racks:
  rack-01:
    nodes:
      master1:
        hostname: node-cp1            # short hostname
        mgmt_ip: 10.0.0.10           # management network IP (SSH)
        local: true                   # set on ONE node — the Ansible bastion
        roles: [kube_control_plane, etcd, storage]
        fabric_nics:
          - nic: ens801f0             # NIC name from `ip link show`
            fabric_ip: 10.201.1.1    # fabric IP address
            prefix: 30               # on-link prefix (star: /30, clos: /16)
            peer: worker1            # star: node at the other end of this cable
            # leaf: leaf-a           # clos: which ToR switch this NIC connects to
```

| Field | Required | Notes |
|-------|----------|-------|
| `hostname` | yes | Used for netplan file names and K8s node name |
| `mgmt_ip` | yes | SSH reachable from bastion. Not needed for `local: true` node |
| `roles` | yes | `kube_control_plane`, `etcd`, `kube_node`, `storage` |
| `local` | no | Exactly one node — the bastion running the installer |
| `fabric_nics` | yes | At least one 100G NIC per node |
| `fabric_router` | no | **Star only.** Exactly one node. The hub that forwards between spokes |
| `profile` | no | Reference to a `node_profiles` entry |

### Storage

```yaml
storage:
  ceph_fabric_nic: ens801f0              # NIC Ceph binds to
  ceph_public_network: 10.201.1.0/28    # client → OSD traffic
  ceph_cluster_network: 10.201.1.0/28   # OSD → OSD replication

  nodes:
    - name: master1                      # must match a node name above
      devices: [nvme1n1]                 # raw block devices (NEVER the OS disk)
    - name: master2
      devices: [nvme1n1]
```

---

## Star Topology

Switchless hub-and-spoke. One node (the hub) forwards traffic between all
others via point-to-point /30 links.

**When to use:** pilots, POC, switchless hardware (HPE B300 HGX), 3–8 nodes.

```
master1 ──/30── worker1 (hub) ──/30── worker2
master2 ──/30──┘     │
master3 ──/30────────┘
```

**How it works:**
- Each cable is its own /30 subnet — two usable IPs per link
- The hub sets `fabric_router: true` → gets `ip_forward=1` and iptables FORWARD
- Spokes route to the fabric supernet via the hub's cable-local IP
- Calico runs in VXLAN mode (node-to-node mesh enabled)

**Key fields:**

```yaml
network:
  topology: star
  calico_mode: vxlan
  fabric_prefix: 30

# On the hub node:
worker1:
  fabric_router: true
  fabric_nics:
    - nic: ens801f0
      fabric_ip: 10.201.1.2
      prefix: 30
      peer: master1             # the node at the other end

# On a spoke:
master1:
  fabric_nics:
    - nic: ens801f0
      fabric_ip: 10.201.1.1
      prefix: 30
      peer: worker1             # points to the hub
```

> [!Important]
> Both ends of a cable **must** share the same /30 subnet. The parser validates
> this — a mismatch is a hard error.

---

## Clos Topology

Leaf/spine switch fabric with native BGP peering. Nodes connect to ToR leaf
switches; Calico peers directly with each leaf.

**When to use:** production, full HA, maximum throughput, 10+ nodes, scale-out.

**How it works:**
- Nodes have 1–2 NICs, each attached to a leaf switch
- Per-node BGPPeer CRDs tell Calico to peer with the leaf's SVI IP
- Dual-homed nodes get ECMP (multipath hash across both uplinks)
- Calico runs in BGP mode (bird backend, node-to-node mesh OFF)

**Key fields:**

```yaml
network:
  topology: clos
  calico_mode: bgp
  fabric_prefix: 16
  leafs:
    - name: leaf-a
      asn: 65001
      peer_ip: 10.201.0.1
      fabric_subnet: 10.201.0.0/16
    - name: leaf-b
      asn: 65002
      peer_ip: 10.202.0.1
      fabric_subnet: 10.202.0.0/16
  bgp:
    cluster_asn: 65100

# Dual-homed worker:
worker1:
  fabric_nics:
    - nic: ens801f0
      fabric_ip: 10.201.0.10
      leaf: leaf-a
    - nic: ens801f1
      fabric_ip: 10.202.0.10
      leaf: leaf-b              # ECMP across both uplinks
```

---

## Upgrading Star → Clos

This is a config-only change — no application modifications required.

1. Install ToR leaf switches and cable nodes to them
2. Edit `rack-profile.yaml`:
   - Change `topology: star` → `topology: clos`
   - Change `calico_mode: vxlan` → `calico_mode: bgp`
   - Change `fabric_prefix: 30` → `fabric_prefix: 16`
   - Remove `fabric_router: true` and `peer:` from nodes
   - Add `leafs:` definitions and `leaf:` on each fabric NIC
   - Add `bgp: cluster_asn:` 
3. Re-run `install` — the stack reconfigures automatically

---

## GPU Nodes

Declare GPU hardware in `node_profiles` and reference from nodes:

```yaml
node_profiles:
  gpu-b300-hgx:
    node_family: cpu-gnr-sp
    sku: 6776P
    accelerator: nvidia-b300-hgx    # vendor-model[-form]
    gpu_count: 8

racks:
  rack-01:
    nodes:
      gpu-worker:
        hostname: hpe690
        mgmt_ip: 10.0.0.30
        roles: [kube_node]
        profile: gpu-b300-hgx        # references the profile above
        fabric_nics:
          - nic: ens801f0
            fabric_ip: 10.201.1.14
            prefix: 30
            peer: master1
```

**What happens automatically:**
- Parser detects `accelerator:` → sets `rack_scale_gpu_vendor=nvidia`
- GPU operator role auto-enables (NVIDIA GPU operator or Intel GPU plugin)
- Node labels applied: `gpu-vendor=nvidia`, `gpu-model=b300`, `gpu-form=hgx`, `gpu-count=8`
- Inference scheduling and the GPU operator use these labels

No separate flag is needed — describe the hardware, the stack figures out what
to install.

---

## Validation

```bash
./es_auto_installer.sh validate inference --env rack
```

The `fabric_validate` role checks:

| Check | Star | Clos | Failure means |
|-------|------|------|---------------|
| Fabric IP reachability | ✓ | ✓ | NIC not configured or cable disconnected |
| Jumbo MTU (8950-byte ping) | ✓ | ✓ | Switch or NIC MTU < 9000 |
| NIC MTU = 9000 | ✓ | ✓ | Netplan not applied or NIC misconfigured |
| Spoke-to-spoke ping | ✓ | ✓ | Routing broken — check hub or BGP |
| Hub ip_forward = 1 | ✓ | | Hub not forwarding — re-run fabric_sysctl |
| Hub rp_filter = 2 | ✓ | | Reverse-path filtering blocking fabric traffic |
| Hub iptables FORWARD ACCEPT | ✓ | | FORWARD chain dropping fabric packets |
| BIRD daemon active | | ✓ | Calico bird backend not running |
| BGP sessions Established | | ✓ | Leaf switch config mismatch — check ASN/peer_ip |
| ECMP hash policy = 1 | | ✓ | Multipath hashing not enabled |
| Node labels applied | ✓ | ✓ | Labels missing — GPU operator can't select nodes |

Quorum members (etcd/control-plane) fail hard if unreachable. Worker nodes warn
and continue.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `rack_scale_enabled is true but rack-profile.yaml not found` | Missing profile | Copy `configs/defaults/rack-profile.yaml.example` to `env/<env>/rack-profile.yaml` |
| Kubespray can't SSH to workers | Star hub not forwarding | Check hub: `sysctl net.ipv4.ip_forward` (must be 1), `iptables -S FORWARD` (must have ACCEPT rules) |
| `star cable faults: both ends must share...` | /30 subnet mismatch | Both ends of a cable need IPs in the same /30 block (e.g. `.1/30` and `.2/30`) |
| `duplicate fabric_ip` | Two nodes share an IP | Check `rack-profile.yaml` for collisions |
| Ceph OSDs not starting | Wrong NIC name | Verify `ceph_fabric_nic` exists on storage nodes: `ip link show` on each |
| Kubespray hangs behind proxy | Fabric CIDRs not in no_proxy | The installer injects them automatically; verify with `echo $no_proxy` |
| `unknown field 'X' — typo?` | Schema validation | Fix the field name and re-run — the parser catches typos as hard errors |
| `GPU operator not installing` | Missing `accelerator:` | Add `accelerator: nvidia-b300-hgx` (or similar) to the node or its profile |
| BGP sessions not Established (Clos) | Leaf switch misconfigured | Verify `peer_ip`, `asn`, and `fabric_subnet` match the switch config |

---

## Reference

### Execution Order

**Star (rack_scale_enabled=true, no BGP):**
```
fabric_inventory → fabric_netplan → fabric_sysctl → kubernetes → fabric_validate → storage
```

**Clos (rack_scale_enabled=true, BGP on):**
```
fabric_inventory → fabric_netplan → fabric_sysctl → kubernetes → fabric_bgp → fabric_validate → storage
```

**Default (rack_scale_enabled=false):**
```
kubernetes → storage
```

### Parser Output Artifacts

| File | Purpose |
|------|---------|
| `inventory/hosts.yaml` | Kubespray inventory with fabric NICs, silicon metadata, GPU info |
| `inventory/metadata/k8s-net-calico.yml` | Calico CNI config (BGP or VXLAN) |
| `inventory/metadata/k8s-net-fabric.yml` | Rack metadata (site, topology, CIDRs) |
| `inventory/netplan/<hostname>.yaml` | Per-node fabric NIC config |
| `inventory/metadata/bgp-peers/<name>.yaml` | Per-node BGPPeer CRDs (Clos only) |
| `inventory/metadata/vars/ceph-storage.yml` | Ceph fabric network + OSD devices |
| `inventory/metadata/rack-scale-flags.env` | Shell-sourceable derived flags |

### Config Variables

| Variable | Default | Location | Purpose |
|----------|---------|----------|---------|
| `rack_scale_enabled` | `false` | `global_config.yaml` | Master gate for all fabric components |
| `storage_backend` | `local-path` | `global_config.yaml` | Set to `ceph` for fabric-attached storage |
| `rack-profile.yaml` | (none) | `env/<env>/` | Hardware profile — see above |
