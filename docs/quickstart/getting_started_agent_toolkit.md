# Getting Started with the Intel® Agent Toolkit

[← Docs Index](../README.md)

Deploy the Agent Toolkit on a single localhost machine — Kubernetes, platform services, inference with the LiteLLM gateway, and the agent layer — with one installer.

---

## Prerequisites

- Ubuntu 22.04 or 24.04 on Intel® Xeon®
- Sudo access (passwordless is most convenient)
- Proxy configured in `env/<name>/global_config.yaml` (written by `init`) if behind one

---

## Quick Start

```bash
git clone https://github.com/intel/enterprise-ai-solutions.git
cd enterprise-ai-solutions

./es_auto_installer.sh configure
./es_auto_installer.sh init agent-toolkit
vim env/local/global_config.yaml          # set base_domain_name, proxy if needed

./es_auto_installer.sh install agent-toolkit
```

> [!Note]
> `init agent-toolkit` clones every repo in `configs/repos/repos.agent-toolkit.yaml` (inference first, then the agent toolkit) and seeds a `config.<layer>.yaml` per layer.
> `install agent-toolkit` auto-pulls its dependencies (infrastructure → platform → inference); `--only` runs the target alone.

After install, add the gateway IP to `/etc/hosts` and call the LiteLLM gateway:

```bash
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
echo "$GATEWAY_IP  litellm.$DOMAIN keycloak.$DOMAIN"

MK=$(kubectl get secret litellm-master-key -n litellm -o jsonpath='{.data.master_key}' | base64 -d)
curl -sk https://litellm.$DOMAIN/v1/models -H "Authorization: Bearer $MK"
```

---

## What Gets Installed

| Component | Default | Switch |
|---|---|---|
| LiteLLM gateway with virtual keys, content-safety guardrail, hybrid router, Langfuse | on | `auth_type` in `config.agent-toolkit.yaml` (`"jwt"` for Keycloak JWT access instead) |
| Default model `qwen2-5-coder-14b` | on | `llm_services_autodeploy_models` in `config.agent-toolkit.yaml` |
| Redis Stack, pgvector | on | `redis_stack_enabled`, `pgvector_enabled` |
| Agent Sandbox (warm pools, sandbox router) | on | `agent_sandbox_enabled` |
| Kata microVM sandboxes | off | `kata_containers_enabled`, `kata_runtime` in `global_config.yaml` |
| OpenShell | off | `openshell_enabled` |
| KubeRay | off | `kuberay_enabled` |
| vLLM Semantic Router | off | `vllm_sr_enabled` in `config.inference.yaml` |

Switches without a file named live in `env/<name>/config.agent-toolkit.yaml`. Component details are in the [Intel® Agent Toolkit repository](https://github.com/intel/enterprise-agent-toolkit).

---

## Teardown

```bash
# Remove the agent layer only (keeps cluster, platform and inference intact)
./es_auto_installer.sh teardown agent-toolkit

# Remove the cluster (and everything on it)
./es_auto_installer.sh teardown infrastructure
```
