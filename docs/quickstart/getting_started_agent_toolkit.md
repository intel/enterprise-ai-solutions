# Getting Started with the Intel® Agent Toolkit

[← Docs Index](../README.md)

Deploy the Agent Toolkit on a single localhost machine — Kubernetes, platform services, inference with the LiteLLM gateway, and the agent layer — with one installer.

---

## Prerequisites

- Ubuntu 22.04 or 24.04 on Intel® Xeon®, with the rest of [Prerequisites](prerequisites.md) met
- Enough memory for the two models the install deploys: the default chat model `qwen2-5-coder-14b` and the guardrail model `qwen3guard`
- `sudo` rights (passwordless is easiest; otherwise Ansible asks for your password at the start of each run)
- Behind a proxy: the proxy settings in `env/<name>/global_config.yaml` (the file `init` creates)

---

## Quick Start

```bash
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

./toolkit_manager.sh configure                # 1. one-time machine prep; answer y
./toolkit_manager.sh init agent-toolkit       # 2. create env/local/ for the agent toolkit
vim env/local/global_config.yaml              # 3. optional: base_domain_name, proxy

./toolkit_manager.sh install agent-toolkit    # 4. install everything up to the agent layer
```

1. `configure` installs Python 3.11+, `yq`, `kubectl` and `helm` with `sudo`, after you confirm.
2. `init agent-toolkit` clones the inference and Agent Toolkit repositories into `ext/` and creates the settings files under `env/local/`, including `config.agent-toolkit.yaml`.
3. The defaults work as they are; the domain is `inteltoolkits.ai` unless you change it.
4. `install agent-toolkit` checks that the nodes are ready for agent workloads (`node_preflight_enabled`), builds the cluster and installs infrastructure, platform, inference and the agent layer. It ends with an `INSTALLATION COMPLETE` banner.

> [!Note]
> `init agent-toolkit` clones every repo in `configs/repos/repos.agent-toolkit.yaml` (inference first, then the agent toolkit) and seeds a `config.<layer>.yaml` per layer.
> `install agent-toolkit` auto-pulls its dependencies (infrastructure → platform → inference); `--only` runs the target alone.

After install, check that the LiteLLM gateway answers and lists the models:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
echo "$GATEWAY_IP  litellm.$DOMAIN keycloak.$DOMAIN langfuse.$DOMAIN"   # line for /etc/hosts on client machines

MK=$(kubectl get secret litellm-master-key -n litellm -o jsonpath='{.data.master_key}' | base64 -d)
curl -s --cacert env/local/logs/ai-solutions-ca.crt \
  --resolve "litellm.$DOMAIN:443:$GATEWAY_IP" \
  https://litellm.$DOMAIN/v1/models -H "Authorization: Bearer $MK"
```

The reply is a JSON list of models that includes `qwen2-5-coder-14b` and `hybrid-router`. The master key is LiteLLM's admin key: use it to create virtual keys, and give applications and agents a virtual key instead (see [Access the model](../deploy/deploy_models.md#access-the-model)).

---

## What Gets Installed

| Component | Default | Switch |
|---|---|---|
| LiteLLM gateway with virtual keys, hybrid router, Langfuse | on | `auth_type: "litellm_vkey"` in `config.agent-toolkit.yaml` (`"jwt"` for Keycloak JWT access instead, without LiteLLM) |
| Content-safety guardrail (guard model `qwen3guard`) | on with LiteLLM | `guardrails_enabled` |
| Default model `qwen2-5-coder-14b` | on | `llm_services_autodeploy_models` in `config.agent-toolkit.yaml` |
| Redis Stack, pgvector | on | `redis_stack_enabled`, `pgvector_enabled` |
| Agent Sandbox (warm pools, sandbox router) | on | `agent_sandbox_enabled` |
| Keycloak realm `agent-toolkit` | on | `keycloak_agent_config_enabled` |
| Kata microVM sandboxes | off | `kata_containers_enabled`, `kata_runtime` in `global_config.yaml` |
| OpenShell | off | `openshell_enabled` |
| KubeRay | off | `kuberay_enabled` |
| vLLM Semantic Router | off | `vllm_sr_enabled` in `config.inference.yaml` |

Switches without a file named live in `env/<name>/config.agent-toolkit.yaml`. Component details are in the [Intel® Agent Toolkit repository](https://github.com/intel/agent-toolkit). Re-run `./toolkit_manager.sh install agent-toolkit` after changing a switch.

---

## Teardown

```bash
# Remove the agent layer only (keeps cluster, platform and inference intact)
./toolkit_manager.sh teardown agent-toolkit

# Remove the cluster (and everything on it)
./toolkit_manager.sh teardown infrastructure
```
