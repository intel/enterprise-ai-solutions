# Deployment Guide - Install On-Premises

[← Docs Index](../README.md)

This guide installs Toolkit Manager on your own machines, step by step: from plain
servers to a running, self-hosted AI stack. You do not need Kubernetes experience.

---

## What Gets Installed

The installer sets up these layers, each on top of the one before:

| Layer | What it contains |
|-------|------------------|
| 1. Infrastructure | Kubernetes (built with Kubespray) and storage (`local-path`, NFS, Ceph or NetApp) |
| 2. Platform | cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, observability (Grafana, Prometheus, Loki, Tempo), NRI CPU Balloons, Node Feature Discovery, metrics-server. Optional: Headlamp, KEDA, Velero |
| 3. Intel® Inference Toolkit | From the inference repository: KServe, the Envoy AI Gateway and the model services. LiteLLM and Langfuse too in LiteLLM mode |
| 4. Intel® Chatbot Toolkit (optional) | Vector database, document ingestion, chat UI. Needs `init erag` and `install erag` |

You run **one command** and it installs every layer the target needs.
`./toolkit_manager.sh show` lists every layer and component.

---

## Before You Start

Check the [Prerequisites](../quickstart/prerequisites.md): operating system, hardware,
network and access. In short:

- **Operating system:** `configure` sets up Ubuntu 22.04 or 24.04, and RHEL-family
  systems (RHEL, Rocky). It refuses other Ubuntu versions.
- **Internet access:** a proxy is fine (see [Step 2](#step-2-edit-your-settings)).
- **Several machines:** keep their clocks in sync (NTP, `chrony` or
  `systemd-timesyncd`) on every node. Clock skew shows up later as hard-to-explain
  certificate and etcd errors.

`./toolkit_manager.sh configure` installs these tools (it needs `sudo` and changes the
system), and skips any that are already present:

| Tool | Version |
|------|---------|
| Python | 3.11 or newer, with its `venv` package |
| yq | v4.53.2 |
| kubectl | v1.35.4 |
| helm | v3.20.2 |

`git` must already be installed.

---

## Step 1: Get the Code and Create an Environment

1. Get the code:

   ```bash
   git clone https://github.com/intel/toolkit-manager.git
   cd toolkit-manager
   ```

2. Prepare the machine (once per machine). It lists the changes it will make and asks
   before going ahead:

   ```bash
   ./toolkit_manager.sh configure
   ```

3. Create an **environment**: a folder under `env/` that holds the settings for one
   cluster. This creates `env/local/` for the inference layer:

   ```bash
   ./toolkit_manager.sh init inference
   ```

   Add `--env <name>` to give it another name, for example `--env prod`. `init` also
   downloads the inference repository into `ext/`.

`init` creates these files in `env/local/`:

| File | What it is |
|------|------------|
| `global_config.yaml` | Your settings for the whole platform (domain, TLS, proxy, storage) |
| `nodes.yaml` | Which machines form the cluster. Left as is, the cluster is this machine |
| `inventory/hosts.yaml` | The machine list in Kubespray format. Generated from `nodes.yaml`, or edited by hand |
| `config.inference.yaml` | Settings for the inference layer, such as `auth_type` |
| `models.yaml` | The model catalog `model-manager` deploys from |
| `.solutions.yaml` | The record of which layers this environment was set up for. Do not edit |
| `logs/` | Installer logs |

`kubeconfig.yaml` (the key to your cluster) appears in `env/local/` during the install.

---

## Step 2: Edit Your Settings

Open the environment's settings:

```bash
nano env/local/global_config.yaml
```

**Behind a corporate proxy?** In section 2, *Networking and proxy*, uncomment and fill in:

```yaml
http_proxy: "http://your-proxy:8080"
https_proxy: "http://your-proxy:8080"
no_proxy: "localhost,127.0.0.1,.svc,.cluster.local,.monitoring,10.233.0.0/18,10.233.64.0/18,<node-subnet>"
```

`no_proxy` must keep the two cluster address ranges shown and add your machines'
subnet (shown by `ip route`), or traffic inside the cluster goes to the proxy.

**Your own domain and TLS certificate?** In section 3, *Domain and access*:

```yaml
base_domain_name: "your-domain.com"
gateway_tls_mode: "custom"
gateway_tls_cert_file: "/path/to/your/cert.pem"
gateway_tls_key_file: "/path/to/your/key.pem"
```

Otherwise keep the defaults: the domain `inteltoolkits.ai` and a self-signed
certificate that the installer creates.

**Everything else can stay as it is.** Save and close the file. Every setting is
described in the [Configuration Reference](../customize/configuration.md).

---

## Step 3: Choose Where to Install

Pick one option, depending on your machines.

---

### Option A: Single Machine (Simplest)

**Best for:** getting started, development and test, a single Intel® Xeon® server.

Everything runs on the machine you are on. No SSH setup is needed, and nothing needs
configuring: the files `init` created already describe this machine.

1. Install:

   ```bash
   ./toolkit_manager.sh install inference --env local
   ```

2. Wait. A first install usually takes 30 to 60 minutes. It ends with an
   **INSTALLATION COMPLETE** box that names the log file.

3. Check the cluster:

   ```bash
   export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
   kubectl get nodes
   ```

   One node is listed, with `STATUS` `Ready` and `ROLES` `control-plane`.

Continue to [Step 4](#step-4-verify-everything-works).

---

### Option B: Multiple Machines

**Best for:** production, with separate control-plane and worker machines.

**What you need:**
- 2 or more machines that reach each other over the network
- A machine to run the installer from, usually the first control-plane machine
- Shared storage for model weights: set `storage_backend` to `nfs` or `ceph` in
  `global_config.yaml`. The default, `local-path`, is for a single machine.

#### B.1 — Set up SSH access

1. On the machine that runs the installer, create an SSH key (press Enter at every
   prompt):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/cluster_key -N ""
   ```

2. Copy it to every cluster machine (use your user and IPs):

   ```bash
   ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.10
   ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.20
   ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.21
   ```

3. Test it. Each command prints the machine's name:

   ```bash
   ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.10 hostname
   ```

4. On every cluster machine, let that user run commands as root without a password:

   ```bash
   echo "ubuntu ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ubuntu
   ```

#### B.2 — List your machines in `nodes.yaml`

Open `env/local/nodes.yaml`, uncomment the multi-node example, and fill in your
machines:

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1

nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2

nodes_ssh_user: "ubuntu"
nodes_ssh_key: "~/.ssh/cluster_key"
```

- `nodes_control_plane`: the machines that manage the cluster (they also run etcd, the
  cluster's database). Use 1, or 3 for high availability.
- `nodes_workers`: the machines that run the workloads. Leave it out to run workloads on
  the control-plane machines.
- `hostname`: the node's name in Kubernetes, in lower case.
- A machine can override the SSH settings with its own `ssh_user` and `ssh_key`.

The installer writes `inventory/hosts.yaml` from this file on every run, so do not
edit `hosts.yaml` while `nodes.yaml` lists machines. More layouts (HA, a hand-written
inventory) are in [Multi-Node & BYO Cluster](topologies.md).

#### B.3 — Install

```bash
./toolkit_manager.sh install inference --env local
```

When it finishes, check that every machine is listed as `Ready`:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

Continue to [Step 4](#step-4-verify-everything-works).

---

### Option C: Bastion (Jump Host)

**Best for:** cluster machines in a private network that you reach only through a
gateway machine, the *bastion*.

It is Option B, run from the bastion. The bastion runs the installer and does **not**
become part of the cluster.

```
 You → SSH → [Bastion] → SSH → [cp1, worker1, worker2, ...]
              (runs installer)    (become the cluster)
```

#### C.1 — On the bastion: get the code and set up SSH

1. Do [Step 1](#step-1-get-the-code-and-create-an-environment) on the bastion.
2. Do [B.1](#b1--set-up-ssh-access) from the bastion to every cluster machine.

#### C.2 — Configure

List the machines in `env/local/nodes.yaml`, as in [B.2](#b2--list-your-machines-in-nodesyaml).

**Is there a second jump host between the bastion and the machines?** `nodes.yaml`
cannot express that. Leave `nodes_control_plane` empty in `nodes.yaml` and write
`env/local/inventory/hosts.yaml` yourself (Kubespray format, see
[Multi-Node & BYO Cluster](topologies.md)), with the extra hop in its `vars`:

```yaml
  vars:
    ansible_user: ubuntu
    ansible_ssh_private_key_file: ~/.ssh/cluster_key
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5'
```

With a double hop, the bastion must still reach the Kubernetes API at
`https://<control-plane-ip>:6443`. After Kubespray, the installer checks the cluster and
labels the nodes from the bastion. If only the extra jump host reaches the machines,
Kubespray succeeds over SSH but that check times out, and a re-run sees a live cluster
and skips the rest. In that case:

1. Forward port 6443 through the jump host.
2. Point `env/local/kubeconfig.yaml` at the forwarded port.
3. Run the remaining phase on its own:

   ```bash
   ./toolkit_manager.sh install --env local kubernetes --only -- -e _kubespray_skip_cluster_yml=true
   ```

#### C.3 — Install

```bash
./toolkit_manager.sh install inference --env local
```

#### C.4 — Use kubectl on the bastion

The install writes the kubeconfig to `env/local/kubeconfig.yaml`:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

Continue to Step 4.

---

## Step 4: Verify Everything Works

1. Check the nodes and pods:

   ```bash
   export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml

   kubectl get nodes      # every node: Ready
   kubectl get pods -A    # pods: Running or Completed
   ```

2. Run the built-in checks. Each component checks itself and reports what is wrong:

   ```bash
   ./toolkit_manager.sh validate inference --env local
   ```

3. See what is installed, with each namespace's health and the service URLs:

   ```bash
   ./toolkit_manager.sh status --env local
   ```

   `status --health` prints only a pass/fail verdict.

The web UIs live under your domain, for example `https://grafana.inteltoolkits.ai`. The
domain is usually not in your DNS: point the host names at the gateway IP in your DNS
or `/etc/hosts`. How to find the gateway IP and trust the self-signed certificate is in
[Deploy a Model](deploy_models.md#1-common-setup).

**Next:** [deploy a model](deploy_models.md).

---

## Scaling: Adding More Machines Later

To add or remove worker machines in a running cluster, edit `nodes.yaml` and run
`add-node` or `remove-node`, not `install`:

1. Set up SSH to the new machine, as in [B.1](#b1--set-up-ssh-access).
2. Add it under `nodes_workers` in `env/local/nodes.yaml`.
3. Add it to the cluster:

   ```bash
   ./toolkit_manager.sh add-node --env local --dry-run   # shows what would change
   ./toolkit_manager.sh add-node --env local
   ```

Only worker machines can be added or removed this way, and the cluster must have been
installed from `nodes.yaml`. All the rules are in [Node Scaling](../customize/node_scaling.md).

---

## Teardown: Removing Things

`teardown <target>` removes the target **and everything installed on top of it**. It
asks before it starts (`--force` skips the question).

```bash
# Remove everything: the inference and platform layers, then the Kubernetes cluster itself
./toolkit_manager.sh teardown infrastructure --env local

# Remove only the inference layer and keep the cluster and platform
./toolkit_manager.sh teardown inference --env local

# Remove one component (and whatever depends on it)
./toolkit_manager.sh teardown llm_services --env local
```

---

## Troubleshooting

### "Connection refused" or SSH errors

Test SSH by hand first:

```bash
ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.10 hostname
```

If this does not work, the installer cannot work either. Fix SSH access first.

### Install fails partway through

1. Read the end of the newest log. The **INSTALLATION COMPLETE** box, or the error,
   names the file:

   ```bash
   ls -t env/local/logs/ | head -3
   tail -50 env/local/logs/install-inference-<timestamp>.log
   ```

2. Fix the cause, then run the same command again. Re-running is safe: what is already
   installed is left as it is.

   ```bash
   ./toolkit_manager.sh install inference --env local
   ```

### Pods stuck in "ImagePullBackOff"

The node cannot download images. This is usually the proxy: check `http_proxy`,
`https_proxy` and `no_proxy` in `env/local/global_config.yaml`.

### More detail in the output

```bash
TKM_LOG_LEVEL=debug ./toolkit_manager.sh install inference --env local
```

`debug` adds verbose logs and Ansible's `-vvv`; `trace` adds even more. You can also pass
Ansible flags after `--`, for example `-- -vvv`.

### Install one component without its dependencies (expert)

```bash
./toolkit_manager.sh install metallb --env local --only
```

`--only` runs just the named target and skips the layers and components it depends on.

---

## Common Issues

### Wrong kubeconfig

Make sure `kubectl` uses the environment's kubeconfig:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

### Several environments mixed up

Each environment is separate. Always pass `--env <name>`, and use that environment's
kubeconfig:

```bash
# For environment "prod"
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
./toolkit_manager.sh install inference --env prod
```

---

## Logs

Each run writes one log under `env/<name>/logs/`, named after the action, the target
and the time:

```
env/<name>/logs/install-inference-20260817-143022.log
env/<name>/logs/teardown-infrastructure-20260817-150100.log
env/<name>/logs/validate-inference-20260817-151500.log
env/<name>/logs/status-cluster-20260817-152000.log
```

---

## Quick Reference

```bash
# Set up
./toolkit_manager.sh configure                          # prepare this machine (once)
./toolkit_manager.sh init <layer> --env <name>          # create an environment (inference, erag, agent-toolkit)
./toolkit_manager.sh show                               # list layers and components

# Install
./toolkit_manager.sh install inference --env <name>           # infrastructure + platform + inference
./toolkit_manager.sh install kubernetes --env <name>          # just Kubernetes
./toolkit_manager.sh install metallb --env <name> --only      # one component, no dependencies
./toolkit_manager.sh install inference --env <name> -- -vvv   # pass Ansible flags

# Operate
./toolkit_manager.sh validate inference --env <name>      # check health
./toolkit_manager.sh status --env <name>                  # what is installed
./toolkit_manager.sh teardown infrastructure --env <name> # remove everything

# kubectl
export KUBECONFIG=$(pwd)/env/<name>/kubeconfig.yaml
kubectl get nodes
```

Every command and flag: [CLI Reference](../customize/cli.md).

---

## Deploying and Accessing Models

Deploying a model, getting a credential (Keycloak token or LiteLLM key), and calling the
model with `curl` or Python are covered in [Deploy a Model](deploy_models.md):

- [Deploy a model from the catalog](deploy_models.md#deploy-a-model-from-the-catalog)
- [Access the model](deploy_models.md#access-the-model): Keycloak mode
  (`auth_type: jwt`, the default) and LiteLLM mode (`auth_type: litellm_vkey`), including
  LiteLLM virtual keys

---

## Deploying Intel® Chatbot Toolkit

Intel® Chatbot Toolkit adds document ingestion, vector search, RAG pipelines and a web
UI on top of the inference layer. It is optional and has its own `init` and `install`:

```bash
./toolkit_manager.sh init erag --env <name>
./toolkit_manager.sh install erag --env <name>
```

- `init erag` downloads the inference and Chatbot Toolkit repositories, creates
  `env/<name>/config.erag.yaml` from a pipeline preset (default `chatqna`; pick another
  with `--flavour`), and records the layer in `env/<name>/.solutions.yaml`.
- `install erag` first installs whatever it needs that is missing (infrastructure,
  platform, inference), then the Chatbot Toolkit.

The full guide: [Getting Started with Intel® Chatbot Toolkit](../quickstart/getting_started_rag.md).

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Deploy across multiple machines or onto a cluster you already run | [Multi-Node & BYO Cluster](topologies.md) |
| Change TLS, storage, auth type, or any other setting used above | [Configuration Reference](../customize/configuration.md) |
| Deploy your first model once the platform is up | [Deploy a Model](deploy_models.md) |
| Understand exactly how a request reaches a model after install | [Network Architecture](networking.md) |
