# Getting Started with the Intel® Agent Toolkit

[← Docs Index](../README.md)

Deploy the Agent Toolkit on a single localhost machine — Kubernetes, platform services, inference with the LiteLLM gateway, and the agent layer — with one installer.

---

## Prerequisites

- Ubuntu 22.04 or 24.04 on Intel® Xeon®, sized for the default `qwen2-5-coder-14b` model — see [Prerequisites](prerequisites.md)
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

> [!Important]
> Use a separate `enterprise-ai-solutions` checkout for agent-toolkit environments. A checkout holds one inference revision under `ext/`, and the toolkit's differs from the one the `erag` and `inference` manifests pin.

> [!Note]
> `init agent-toolkit` seeds `auth_provider: "litellm"` into a new `env/<name>/global_config.yaml`.
> With `auth_provider: "keycloak"` (JWT access to the AI gateway, no LiteLLM), these features are not available: LiteLLM virtual keys and budgets, the content-safety guardrail, the `hybrid-router` and `vllm-sr/auto` models, Langfuse tracing, and the OpenShell GenAI Gateway provider. Everything else is deployed as in LiteLLM mode.
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
curl -sk --noproxy '*' --resolve "litellm.$DOMAIN:443:$GATEWAY_IP" \
  https://litellm.$DOMAIN/v1/models -H "Authorization: Bearer $MK"
```

---

## What Gets Installed

| Component | Default | Switch |
|---|---|---|
| LiteLLM gateway with virtual keys, content-safety guardrail, hybrid router, Langfuse | on | `auth_provider` in `global_config.yaml` (seeded `"litellm"` by `init agent-toolkit`; `"keycloak"` for Keycloak JWT access instead) |
| Content-safety guardrail (Qwen3Guard) | on in LiteLLM mode | `guardrails_enabled: false` in `config.agent-toolkit.yaml` turns it off |
| Default model `qwen2-5-coder-14b` | on | `llm_services_autodeploy_models` in `config.agent-toolkit.yaml` |
| Redis Stack, pgvector | on | `redis_stack_enabled`, `pgvector_enabled` |
| Agent Sandbox (warm pools, sandbox router) | on | `agent_sandbox_enabled` |
| Kata microVM sandboxes | off | `kata_containers_enabled`, `kata_runtime` in `global_config.yaml` |
| OpenShell | off | `openshell_enabled` |
| KubeRay | off | `kuberay_enabled` |
| vLLM Semantic Router | off | `vllm_sr_enabled` in `config.inference.yaml` |

Switches without a file named live in `env/<name>/config.agent-toolkit.yaml`.

Turn Kata on before the first install. Enabling it on a running stack restarts containerd and the CPU-pinning plugin, which moves running models to other CPUs; they fail their next request until restarted. In that case, restart them after the install: `kubectl delete pod -n llm-inference --all`.

The toolkit's services run in the Istio ambient mesh with strict mTLS. To call LiteLLM, Redis, pgvector or Ray from a namespace of your own, label it `istio.io/dataplane-mode=ambient`, and for LiteLLM also add it to `litellm_allowed_namespaces`. Kata sandbox pods stay outside the mesh (a microVM cannot join it); only the sandbox router reaches them. Component details are in the [Intel® Agent Toolkit repository](https://github.com/intel/enterprise-agent-toolkit).

---

## Teardown

```bash
# Remove the agent layer only (keeps cluster, platform and inference intact)
./es_auto_installer.sh teardown agent-toolkit

# Remove the cluster (and everything on it)
./es_auto_installer.sh teardown infrastructure
```
