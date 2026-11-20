# Frequently Asked Questions

[← Docs Index](README.md)

Short answers with links to the full detail. New here? Start with [Meet Toolkit Manager](meet/meet.md).

---

## Getting started

### What does Toolkit Manager actually install?

A complete, self-hosted AI platform: Kubernetes, storage, service mesh, ingress, identity, observability, model serving, and an AI gateway that exposes OpenAI-compatible endpoints. Optionally, the Intel® Chatbot Toolkit (a retrieval-augmented generation application) or the Intel® Agent Toolkit on top. One script, `toolkit_manager.sh`, drives all of it.

### What do I need before running it?

Ubuntu 22.04/24.04 or RHEL/Rocky Linux, `git`, `sudo` rights (passwordless is easiest), and internet access. Full hardware and software requirements are in [Prerequisites](quickstart/prerequisites.md).

### Do I need Kubernetes experience?

No for the default path. The installer provisions the cluster with Kubespray and configures every add-on itself. You need `kubectl` only to look at what it built, and the guides give you the commands.

### Do I need an accelerator or a GPU?

No. Inference runs on Intel® Xeon® CPUs, with NUMA-aware CPU pinning and vLLM or OpenVINO™ Model Server as the runtime.

### What is the difference between `configure`, `init`, and `install`?

1. `configure` prepares the machine once: Python, `yq`, `kubectl`, `helm`, and the installer's Python environment.
2. `init <layer>` creates an environment under `env/<name>/`, copies in the settings files, and clones the external repositories that layer needs into `ext/`.
3. `install <target>` deploys.

See [CLI Reference](customize/cli.md).

### Which targets can I pass to `init`?

Any layer: `infrastructure`, `platform`, `inference`, `erag`, or `agent-toolkit`. `inference`, `erag`, and `agent-toolkit` have a repository manifest (`configs/repos/repos.<layer>.yaml`), so `init` also clones their repositories and seeds their `config.<layer>.yaml`; `init platform` only creates the environment. Single components are valid targets for `install`, `teardown`, and `validate`, but not for `init`.

### How long does a full install take?

Allow up to an hour for infrastructure, platform, and inference on a first install; most of that is downloading container images, so a fast network shortens it. Model downloads come on top. Intel® Chatbot Toolkit adds roughly another 30 to 40 minutes.

---

## Environments and configuration

### What is an environment?

A directory under `env/<name>/` holding one deployment's configuration, inventory, kubeconfig, and logs. `--env` selects it and defaults to `local`. Environments are independent, so you can keep `local` and `prod` side by side.

### Which file do I edit to change settings?

`env/<name>/global_config.yaml` for platform-wide settings, and `env/<name>/config.<layer>.yaml` for a solution layer's own settings. Never edit the templates in `configs/defaults/`; those seed new environments. See [Configuration Reference](customize/configuration.md).

### Do I have to configure anything before the first deploy?

Usually nothing: `base_domain_name` defaults to `inteltoolkits.ai`. Change it if you want your own domain, and set the proxy settings if you are behind one. Everything else has a working default. See [Do I need to change global_config.yaml](customize/configuration.md#do-i-need-to-change-global_configyaml-before-my-first-deploy).

### Why did my nested YAML override get ignored?

The override surface is flat. Solution config files are passed with `-e @file`, which merges shallowly, so a nested dictionary replaces the role default rather than merging into it. Keep keys at the top level.

### Can I override one value without editing a file?

Yes: `./toolkit_manager.sh install <target> -- -e key=value`. Everything after `--` goes to `ansible-playbook`, so you can also pass `-vvv` for verbose output. A value given this way wins over every config file.

### How do I see what would change before it runs?

There is no full dry run for `install`: not every role supports Ansible check mode, so `-- --check` is not a reliable preview. Instead, `./toolkit_manager.sh show` lists layers and components, `./toolkit_manager.sh status` shows what is currently deployed, and `install` itself prints its plan (the components it will run, and each layer's upgrade-check verdict) before it changes anything. For node changes, `add-node --dry-run` and `remove-node --dry-run` print the plan without running it.

---

## Deployment choices

### Can I deploy onto a cluster I already run?

Yes. Set `existing_kubernetes: "/path/to/kubeconfig"` in `global_config.yaml` and the installer skips Kubespray entirely, deploying the platform and everything above it onto your cluster. See [Multi-Node & BYO Cluster](deploy/topologies.md).

### What changes for a multi-node deployment?

You list the nodes in `env/<name>/nodes.yaml` (the installer generates the inventory from it), set up passwordless SSH to them, and choose shared storage: `storage_backend: nfs`, `ceph`, or `netapp-trident`. `local-path` cannot serve model weights to more than one node. See [Multi-Node & BYO Cluster](deploy/topologies.md).

### Which storage backends are supported?

`local-path` (default, single node), `nfs` (auto-provisioned on the first control-plane node), `ceph` (Rook-Ceph, replicated block storage), `netapp-trident` with ONTAP, and `custom` (your own CSI driver's default StorageClass). See [Storage](customize/configuration.md#storage) and [NetApp ONTAP and Trident](deploy/netapp_ontap.md).

### Which API authentication type should I choose?

Keycloak is always deployed and handles sign-in in both modes; `auth_type` only selects what API clients send. `jwt`, the default, uses Keycloak JWTs (client credentials) on the inference endpoint and is the production choice. `litellm_vkey` uses LiteLLM virtual API keys with per-key budgets on `litellm.<domain>`, which suits multi-tenant API access. See [API authentication](customize/configuration.md#api-authentication).

### Can I use my own TLS certificates?

Yes. `gateway_tls_mode: custom` with a certificate and key covering `*.<base_domain_name>`. With the default `selfsigned`, cert-manager creates an internal CA and exports it to `env/<name>/logs/ai-solutions-ca.crt` so you can trust it once instead of clicking through warnings.

### Does it work behind a corporate proxy?

Yes. Set `http_proxy`, `https_proxy`, and `no_proxy` in `env/<name>/global_config.yaml` before installing. `no_proxy` must include the Kubernetes service and pod CIDRs and your node subnets, or in-cluster traffic will be sent to the proxy and fail.

### Can I install only part of the stack?

Yes. Pass a layer or a single component as the target. `--only` runs it without pulling dependencies, and `--skip <names>` leaves parts out of the plan.

---

## Models and requests

### How do I deploy a model?

`./model-manager deploy <name> --wait` for a model in the catalog, or `./model-manager deploy --id <hf-repo-id> --cpu <n> --memory <n>Gi --wait` for anything on Hugging Face. It downloads the weights, sizes the server, and creates the pods. See [Deploy a Model](deploy/deploy_models.md).

### Do gated models need anything special?

Yes, a Hugging Face token exported as `HF_TOKEN` before deploying (`export HF_TOKEN=hf_...`). Llama, Mistral, and Gemma models are gated, and you must also accept each model's licence on its Hugging Face page.

### How do I call the model once it is running?

Any OpenAI-compatible client, with a Bearer token. With `auth_type: jwt`, source the token helper script to get a JWT and the gateway address; with `litellm_vkey`, use a virtual key. See [Integration Guide](customize/integration.md).

### I installed agent-toolkit. Which model does it serve, and where is LiteLLM?

The Intel® Agent Toolkit deploys one default model on install, `qwen2-5-coder-14b`, set by `llm_services_autodeploy_models` in `env/<name>/config.agent-toolkit.yaml`; set it to `[]` to skip it, and deploy others with `./model-manager deploy <name>`. The same file sets `auth_type: "litellm_vkey"`, so the agent toolkit installs LiteLLM: call models on `https://litellm.<domain>/v1` with a LiteLLM key. Set `auth_type: "jwt"` there instead for Keycloak tokens on the inference endpoint and no LiteLLM. See [Getting Started with the Intel® Agent Toolkit](quickstart/getting_started_agent_toolkit.md) and [Access the model](deploy/deploy_models.md#access-the-model).

### Can I serve more than one model at a time?

Yes, subject to CPU and memory. The AI gateway routes by the `model` field in the request, and `model-manager` reports what is deployed and can scale replicas.

### Can I serve non-LLM models?

Yes. KServe also serves scikit-learn, XGBoost, PyTorch, TensorFlow, ONNX, and Triton models. See [Traditional ML models](customize/integration.md#traditional-ml-models).

---

## Operating it

### Where are the credentials?

The Keycloak and Grafana admin passwords are generated on the first install and kept in Kubernetes Secrets; [Admin passwords](customize/configuration.md#admin-passwords) shows the `kubectl` commands that read them. The generated CA certificate is written to `env/<name>/logs/ai-solutions-ca.crt`. The Intel® Chatbot Toolkit also writes its user passwords to `env/<name>/logs/rag/default_credentials.txt`.

### How do I check the deployment is healthy?

`./toolkit_manager.sh status --env <name>` for namespaces, pods, and endpoints, and `validate <target>` to run the health checks for a layer or component.

### Where are the logs?

Under `env/<name>/logs/`, one file per run, named `<action>-<target>-<date>-<time>.log`. Set `TKM_LOG_LEVEL=debug` for verbose output, or `trace` to also enable bash tracing (`ES_LOG_LEVEL`, the former name, still works). Cluster-side logs are in Loki and browsable from Grafana.

### An install failed halfway. Is it safe to re-run?

Yes. Roles are idempotent, so re-running continues from where things stand rather than duplicating work. Read the log first and fix the cause.

### How do I remove things?

`teardown <target>` removes the target and the layers that depend on it, in reverse order, after asking you to confirm (`--force` skips the question). `teardown infrastructure` destroys the cluster and everything on it; your files under `env/<name>/` are kept. Teardown is environment-scoped, so pass the same `--env` you installed with.

### How do I move to a newer release?

1. Update this repository (`git pull`).
2. Run `init <layer> --upgrade`. It moves the cloned `ext/` repositories onto the revisions the manifests now pin, refuses if those checkouts have local changes, and never rewrites your configuration; it only reports what changed upstream.
3. Re-run `install <layer>`. It runs the upgrade checks first. See [Upgrade a Layer](deploy/upgrade.md).

### Can I add my own layer or component?

Yes, that is the intended extension point: register it in the component registry and add a role. See [Adding Solution Layers](reference/adding_solutions.md).

### Something is broken and this list does not cover it.

Check the run log in `env/<name>/logs/`, then `status` and `validate`. The [Deployment Guide](deploy/install_platform.md) and [Network Architecture](deploy/networking.md) cover the failure modes that come up most, such as ingress and DNS.
