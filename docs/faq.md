# Frequently Asked Questions

[← Docs Index](README.md)

Short answers with links to the full detail. New here? Start with [Meet Intel® AI for Enterprise Solutions](meet/meet.md).

---

## Getting started

### What does Intel® AI for Enterprise Solutions actually install?

A complete, self-hosted AI platform: Kubernetes, storage, service mesh, ingress, identity, observability, model serving, and an AI gateway that exposes OpenAI-compatible endpoints. Optionally, the retrieval-augmented generation application on top. One script, `es_auto_installer.sh`, drives all of it.

### What do I need before running it?

Ubuntu 22.04/24.04 or RHEL/Rocky x86_64, passwordless sudo, and internet access. Full hardware and software requirements are in [Prerequisites](quickstart/prerequisites.md).

### Do I need Kubernetes experience?

No for the default path. The installer provisions the cluster with Kubespray and configures every add-on itself. You need `kubectl` only to look at what it built, and the guides give you the commands.

### Do I need an accelerator or a GPU?

No. Inference runs on Intel® Xeon® CPUs, with NUMA-aware CPU pinning and vLLM or OpenVINO™ Model Server as the runtime.

### What is the difference between `configure`, `init`, and `install`?

`configure` prepares the machine once (Python, `yq`, `kubectl`, `helm`). `init <layer>` creates an environment under `env/<name>/` and clones the external repositories that layer needs. `install <target>` deploys. See [CLI Reference](customize/cli.md).

### Which targets can I pass to `init`?

`inference` and `erag`. Those are the layers with a repository manifest. Other layers and single components are valid targets for `install`, `teardown`, and `validate`, but not for `init`.

### How long does a full install take?

Roughly 15 to 20 minutes for infrastructure, platform, and inference on a single node, plus model download time. The RAG layer adds another 10 to 20 minutes.

---

## Environments and configuration

### What is an environment?

A directory under `env/<name>/` holding one deployment's configuration, inventory, kubeconfig, credentials, and logs. `--env` selects it and defaults to `local`. Environments are independent, so you can keep `local` and `prod` side by side.

### Which file do I edit to change settings?

`env/<name>/global_config.yaml` for platform-wide settings, and `env/<name>/config.<layer>.yaml` for a solution layer's own settings. Never edit the templates in `configs/defaults/`; those seed new environments. See [Configuration Reference](customize/configuration.md).

### Do I have to configure anything before the first deploy?

Only `base_domain_name`, and proxy settings if you are behind one. Everything else has a working default. See [Do I need to change global_config.yaml](customize/configuration.md#do-i-need-to-change-global_configyaml-before-my-first-deploy).

### Why did my nested YAML override get ignored?

The override surface is flat. Solution config files are passed with `-e @file`, which merges shallowly, so a nested dictionary replaces the role default rather than merging into it. Keep keys at the top level.

### Can I override one value without editing a file?

Yes: `./es_auto_installer.sh install <target> -- -e key=value`. Everything after `--` goes to `ansible-playbook`, which also lets you pass `--check` for a dry run or `-vvv` for verbose output.

### How do I see what would change before it runs?

`./es_auto_installer.sh install <target> -- --check` performs an Ansible dry run. `show` lists layers and components, and `status` shows what is currently deployed.

---

## Deployment choices

### Can I deploy onto a cluster I already run?

Yes. Set `existing_kubernetes: "/path/to/kubeconfig"` in `global_config.yaml` and the installer skips Kubespray entirely, deploying the platform and everything above it onto your cluster. See [Multi-Node & BYO Cluster](deploy/topologies.md).

### What changes for a multi-node deployment?

You define the nodes in the environment inventory, and you need shared storage: `storage_backend: nfs`, `ceph`, or `netapp-trident`. `local-path` cannot serve model weights to more than one node. See [Multi-Node & BYO Cluster](deploy/topologies.md).

### Which storage backends are supported?

`local-path` (default, single node), `nfs` (auto-provisioned on the first control-plane node), `ceph` (Rook-Ceph, replicated block storage), and `netapp-trident` with ONTAP. See [Storage](customize/configuration.md#storage) and [NetApp ONTAP and Trident](deploy/netapp_ontap.md).

### Which authentication provider should I choose?

`keycloak`, the default, gives OIDC single sign-on, role-based access, and per-user JWTs, and is the production choice. `litellm` issues virtual API keys with per-key budgets and deploys no Keycloak, which suits multi-tenant API access and simpler setups. See [Auth provider](customize/configuration.md#auth-provider).

### Can I use my own TLS certificates?

Yes. `gateway_tls_mode: custom` with a certificate and key covering `*.<base_domain_name>`. With the default `selfsigned`, cert-manager creates an internal CA and exports it to `env/<name>/logs/ai-solutions-ca.crt` so you can trust it once instead of clicking through warnings.

### Does it work behind a corporate proxy?

Yes. Set `http_proxy`, `https_proxy`, and `no_proxy` in `global_config.yaml`. `no_proxy` must include the Kubernetes service and pod CIDRs and your node subnets, or in-cluster traffic will be sent to the proxy and fail.

### Can I install only part of the stack?

Yes. Pass a layer or a single component as the target. `--only` runs it without pulling dependencies, and `--skip <names>` leaves parts out of the plan.

---

## Models and requests

### How do I deploy a model?

`./model-manager deploy <name> --wait` for a model in the catalog, or `./model-manager deploy --id <hf-repo-id> --cpu <n> --memory <n>Gi --wait` for anything on Hugging Face. It downloads the weights, sizes the server, and creates the pods. See [Deploy a Model](deploy/deploy_models.md).

### Do gated models need anything special?

Yes, a Hugging Face token exported as `HF_TOKEN` before deploying. Llama, Mistral, and Gemma are gated.

### How do I call the model once it is running?

Any OpenAI-compatible client, with a Bearer token. With `keycloak`, source the token helper script to get a JWT and the gateway address; with `litellm`, use a virtual key. See [Integration Guide](customize/integration.md).

### Can I serve more than one model at a time?

Yes, subject to CPU and memory. The AI gateway routes by the `model` field in the request, and `model-manager` reports what is deployed and can scale replicas.

### Can I serve non-LLM models?

Yes. KServe also serves scikit-learn, XGBoost, PyTorch, TensorFlow, ONNX, and Triton models. See [Traditional ML models](customize/integration.md#traditional-ml-models).

---

## Operating it

### Where are the credentials?

Written to `env/<name>/logs/` at install time, along with the generated CA certificate. Change them after first login.

### How do I check the deployment is healthy?

`./es_auto_installer.sh status --env <name>` for namespaces, pods, and endpoints, and `validate <target>` to run the health checks for a layer or component.

### Where are the logs?

Under `env/<name>/logs/`, one file per run. Set `ES_LOG_LEVEL=debug` for verbose output, or `trace` to also enable bash tracing. Cluster-side logs are in Loki and browsable from Grafana.

### An install failed halfway. Is it safe to re-run?

Yes. Roles are idempotent, so re-running continues from where things stand rather than duplicating work. Read the log first and fix the cause.

### How do I remove things?

`teardown <target>` removes in reverse dependency order. `teardown infrastructure` destroys the cluster and everything on it. Teardown is environment-scoped, so pass the same `--env` you installed with.

### How do I move to a newer release?

`init <layer> --upgrade` moves the cloned `ext/` repositories onto the revisions the manifests now pin, then re-run `install`. It refuses if those checkouts have local changes and never rewrites your configuration.

### Can I add my own layer or component?

Yes, that is the intended extension point: register it in the component registry and add a role. See [Adding Solution Layers](reference/adding_solutions.md).

### Something is broken and this list does not cover it.

Check the run log in `env/<name>/logs/`, then `status` and `validate`. The [Deployment Guide](deploy/install_platform.md) and [Network Architecture](deploy/networking.md) cover the failure modes that come up most, such as ingress and DNS.
