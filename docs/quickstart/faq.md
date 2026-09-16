# FAQ

[← Docs index](../README.md)

Answers about deploying and operating the installer and the stack it deploys.
Each answer explains the default, then points at the guide that owns the full
detail.

**On this page**

- [What is Intel® AI for Enterprise Solutions?](#what-is)
- [Does it require a GPU?](#gpu)
- [Can I run it on-premises or air-gapped?](#air-gapped)
- [What OS and hardware do I need?](#hardware)
- [Do I need Kubernetes experience?](#kubernetes)
- [Single node, multi-node, or BYO cluster: which should I pick?](#which-path)
- [Do I need to edit `global_config.yaml` before the first install?](#config)
- [How do I skip components I already run?](#skip)
- [What's the difference between Keycloak and LiteLLM auth?](#auth)
- [Do I need a Hugging Face token?](#hf-token)
- [How do I deploy a model?](#deploy-model)
- [How do I call the API or connect my app?](#api)
- [How do I open Grafana, Keycloak, or the other UIs?](#uis)
- [Can I manage more than one environment from one machine?](#envs)
- [How do I tear it down?](#teardown)
- [Where are the CLI commands and config options?](#lookup)

<a id="what-is"></a>

## What is Intel® AI for Enterprise Solutions?

It is the installer for Intel® enterprise AI toolkits. It is not a
toolkit of its own. One command stands up a shared Kubernetes foundation
(cluster, storage, TLS, identity, gateway, observability) and deploys those
toolkits onto it. The stack runs on Intel® Xeon® processors, on your own
infrastructure.

You do not install each toolkit by hand, and you do not rebuild the foundation
for every product. See [Meet Intel® AI for Enterprise Solutions](../meet/meet.md).

<a id="gpu"></a>

## Does it require a GPU?

No. Inference is designed to run on Intel® Xeon® CPUs, including NUMA-aware
pinning and AMX where the processor supports it. You do not need a discrete GPU
to install the stack or to serve the catalog models.

<a id="air-gapped"></a>

## Can I run it on-premises or air-gapped?

On-premises and private cloud are the primary targets. After the stack is up,
inference stays on your cluster. Request data does not have to leave your
infrastructure.

Install itself is different. The installer needs internet access to pull
packages, container images, and model weights. Once those are on the nodes,
serving can stay fully local. Hardware, proxy, and network details are in
[Prerequisites](prerequisites.md).

<a id="hardware"></a>

## What OS and hardware do I need?

The installer supports Ubuntu 22.04/24.04 and RHEL/Rocky 8 (x86_64). The machine
that runs the installer needs passwordless sudo.

For a first trial of the foundation and observability only, plan on 16 cores,
32 GB RAM, and 200 GB disk. Serving an 8B-parameter model needs more: 64 cores,
128 GB RAM, and 400 GB disk. Intel® Xeon® processors are recommended for
CPU inference. The full table is in [Prerequisites](prerequisites.md).

<a id="kubernetes"></a>

## Do I need Kubernetes experience?

No. You do not need to write manifests or install Kubernetes yourself. The
[Deployment Guide](../deploy/install_platform.md) walks through a first install
without assuming cluster experience.

After install you will use `kubectl` to check that nodes are Ready and pods are
Running. The installer writes the kubeconfig to `env/<name>/kubeconfig.yaml`, so
you do not have to create that file.

<a id="which-path"></a>

## Single node, multi-node, or BYO cluster: which should I pick?

| Path | Pick it when |
|------|----------------|
| **Single node** | Fastest trial, on the machine you are sitting on. Kubernetes is installed on localhost. |
| **Multi-node** | Production, or you need more CPU and memory than one box. The installer provisions Kubernetes over SSH. |
| **Bring your own Kubernetes** | You already have a cluster. Kubespray is skipped. The installer deploys the platform and toolkits onto that cluster. |

Start with [Getting Started](quickstart.md) to pick a path. Multi-node and BYO
steps (SSH, shared storage, `existing_kubernetes`) are in
[Multi-Node & BYO Cluster](../deploy/topologies.md).

<a id="config"></a>

## Do I need to edit `global_config.yaml` before the first install?

Not for a single-node trial. `init` writes the file with defaults that deploy a
working stack on localhost: self-signed TLS, Keycloak auth, and `local-path`
storage.

You must edit the file before install when any of these is true: you have more
than one node (`storage_backend` cannot stay `local-path`, or the installer
aborts), you already run Kubernetes (`existing_kubernetes`), or you are behind
a corporate proxy (`http_proxy` / `https_proxy` / `no_proxy`). See
[Configuration Reference](../customize/configuration.md#do-i-need-to-change-global_configyaml-before-my-first-deploy).

<a id="skip"></a>

## How do I skip components I already run?

Each built-in component has an `*_enabled` flag in
`env/<name>/global_config.yaml`. Set it to `false` and the installer does not
deploy that component.

Typical skips on a cluster you already manage: `cert_manager_enabled`,
`metallb_enabled`, and `istio_enabled`. See
[Getting Started, Option C](quickstart.md#option-c--bring-your-own-kubernetes)
and [Configuration Reference](../customize/configuration.md).

<a id="auth"></a>

## What's the difference between Keycloak and LiteLLM auth?

The stack has two auth modes. You pick one with `auth_provider` in
`global_config.yaml`. You should not mix them on the same environment.

| `auth_provider` | What you get | How API calls are authorized |
|-----------------|--------------|------------------------------|
| `keycloak` (default) | Full OIDC: SSO, RBAC, and identity management | Bearer JWT from Keycloak |
| `litellm` | Virtual keys. Keycloak is not deployed. | LiteLLM virtual key (or master key) |

How you fetch a token and call a model is in
[Deploy a Model](../deploy/deploy_models.md) and the
[Integration Guide](../customize/integration.md).

<a id="hf-token"></a>

## Do I need a Hugging Face token?

Only for gated models (Llama, Mistral, Gemma, and similar). Hugging Face will
not download those weights until you accept the publisher's license and pass a
Read token. Open models such as Qwen do not need one.

Create a free token at
[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and
export `HF_TOKEN=hf_...` before you deploy a gated model.

<a id="deploy-model"></a>

## How do I deploy a model?

After the foundation and inference toolkit are up, deploy from the catalog:

```bash
./model-manager deploy qwen3-0-6b --wait
```

That command downloads weights, sizes the serving pod, waits until it is ready,
and prints an endpoint. For gated models, ad-hoc Hugging Face ids, scaling, and
undeploy, use [Deploy a Model](../deploy/deploy_models.md).

<a id="api"></a>

## How do I call the API or connect my app?

The inference toolkit speaks the OpenAI-compatible API. Point any OpenAI SDK,
LangChain, LlamaIndex, or similar client at the inference endpoint and pass a
Bearer token. You do not need a vendor-specific protocol.

- First `curl` after install: [project README, Step 3](../../README.md#step-3--send-your-first-request)
- Python, LangChain, and other tools: [Integration Guide](../customize/integration.md)

<a id="uis"></a>

## How do I open Grafana, Keycloak, or the other UIs?

Service hostnames are derived from `base_domain_name` in `global_config.yaml`
(default `solutions.ai`). Grafana is `https://grafana.<domain>`, Keycloak is
`https://keycloak.<domain>`, and so on.

On the client machine, add the gateway LoadBalancer IP to `/etc/hosts` for those
names. The default TLS is a self-signed CA. Import
`env/<name>/logs/ai-solutions-ca.crt` into the browser or OS trust store so the
warning goes away. Steps: [Getting Started, Access the services](quickstart.md#access-the-services).

<a id="envs"></a>

## Can I manage more than one environment from one machine?

Yes. Each `./es_auto_installer.sh init <name>` creates an isolated directory
under `env/<name>/` with its own config, inventory, kubeconfig, and logs. Pass
`--env <name>` on every later command so the installer acts on the right one.
`--env` defaults to `local`.

Install and teardown are environment-scoped. If you installed with `--env prod`,
you must teardown with `--env prod`. Running teardown against `local` will not
touch `prod`. See
[Getting Started, Multiple environments](quickstart.md#multiple-environments).

<a id="teardown"></a>

## How do I tear it down?

```bash
./es_auto_installer.sh teardown --all --env local
```

That removes the stack from the cluster for that environment. Files under
`env/<name>/` are kept, so you can edit config and install again. Use the same
`--env` you used at install. Flags and other teardown targets:
[CLI Reference, teardown](../customize/cli.md#teardown).

<a id="lookup"></a>

## Where are the CLI commands and config options?

- Every installer and `model-manager` command: [CLI Reference](../customize/cli.md)
- Every `global_config.yaml` setting: [Configuration Reference](../customize/configuration.md)
- How the layers and repos fit together: [Architecture & Design](../meet/architecture.md)
