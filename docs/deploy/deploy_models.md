# Deploy an LLM on Intel® Silicon with model-manager

[← Docs Index](../README.md)

How to deploy, access, and manage self-hosted LLM inference on Intel® AI for Enterprise Solutions using the `model-manager` CLI.

---

## The model-manager

`./model-manager` is the CLI for LLM lifecycle management. It downloads weights, calculates serving parameters, creates KServe resources, and registers the model with the API proxy.

```
./model-manager <command> [options] [--env <name>]
```

| Command | What it does |
|---|---|
| `list` | Show all models in the catalog |
| `deploy <name>` | Download weights and start serving |
| `undeploy <name>` | Stop serving (weights stay on the PVC) |
| `undeploy all` | Stop all models |
| `status` | Show running models and their endpoints |

---

## Deploy from the catalog

The model catalog lives at `env/<name>/models.yaml`. It is seeded by `init` and is yours to edit.

```bash
# List what's available
./model-manager list

# Deploy a model (no HF token needed for open models)
./model-manager deploy qwen3-0-6b --wait

# Deploy a gated model (Llama, Mistral, etc.)
export HF_TOKEN="hf_your_token_here"
./model-manager deploy llama3-8b-awq --wait
```

`--wait` blocks until the model is ready. When it returns it prints the inference endpoint and a ready-to-run example.

> [!NOTE]
> With `auth_provider: litellm`, the host that `model-manager` prints
> (`inference.<base_domain_name>`) is **not** the one that serves requests — that
> host returns `404` in this mode. Use `litellm.<base_domain_name>` instead, as
> shown under [Access the model](#access-the-model).

---

## Deploy any Hugging Face model (ad-hoc)

No catalog entry needed:

```bash
./model-manager deploy --id Qwen/Qwen3-0.6B --cpu 4 --memory 8Gi --wait
./model-manager deploy --id meta-llama/Llama-3.2-1B-Instruct --cpu 16 --memory 24Gi --wait
```

Key flags:

| Flag | Default | Description |
|---|---|---|
| `--id` | — | Hugging Face repo ID |
| `--cpu` | from catalog | CPU cores for the serving pod |
| `--memory` | from catalog | Memory limit (e.g. `24Gi`) |
| `--replicas` | `1` | Number of serving replicas |
| `--tp` | `1` | Tensor parallelism — splits across NUMA nodes |
| `--runtime` | `vllm` | Serving runtime: `vllm` \| `openvino` |
| `--wait` | — | Wait for model to be ready |
| `--dry-run` | — | Print the manifest without applying |

---

## Model catalog format (`env/<name>/models.yaml`)

```yaml
defaults:
  namespace: llm-inference
  runtime: vllm
  replicas: 1
storage:
  pvc_name: model-store

models:
- name: llama3-8b-awq
  model_id: casperhansen/llama-3-8b-instruct-awq
  category: llm
  cpu: 16
  memory: 32Gi
  replicas: 2

- name: qwen3-0-6b
  model_id: Qwen/Qwen3-0.6B
  category: llm
  cpu: 8
  memory: 16Gi
```

| Field | Description |
|---|---|
| `name` | Alias used with model-manager commands |
| `model_id` | Hugging Face repo to download |
| `runtime` | `vllm` (LLM) or `openvino` (OVMS) |
| `cpu` | CPU core request for the serving pod |
| `memory` | Memory limit |
| `replicas` | Number of pods to run |
| `node` | Pin to a specific node name (optional) |
| `tp` | Tensor parallelism width (optional) |

---

## Access the model

After a successful deploy, the model-manager prints the exact commands for your `auth_provider`. Refer to those directly, or use the patterns below.

### With `auth_provider: litellm`

LiteLLM acts as the unified OpenAI-compatible proxy. All models share the same base URL; the `"model"` field in the request body selects which one.

```bash
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
TOKEN=$(kubectl get secret litellm-master-key -n litellm \
  -o jsonpath='{.data.master_key}' | base64 -d)

curl -sk --noproxy '*' \
  --resolve "litellm.${DOMAIN}:443:${GATEWAY_IP}" \
  "https://litellm.${DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

Create a scoped virtual key via the LiteLLM UI (`https://litellm.${DOMAIN}/ui/` — the
trailing slash is required) or API for per-key budgets and rate limits. See
[Deploying and Accessing Models](install_platform.md#deploying-and-accessing-models)
for minting, listing, and revoking keys.

### With `auth_provider: keycloak`

Uses path-based routing through the inference gateway.

```bash
# Get the client secret
CLIENT_SECRET=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-secret}' | base64 -d)

# Request a JWT via client-credentials flow
TOKEN=$(curl -sk --noproxy '*' \
  --resolve "keycloak.${DOMAIN}:443:${GATEWAY_IP}" \
  "https://keycloak.${DOMAIN}/realms/inference/protocol/openid-connect/token" \
  -d "grant_type=client_credentials" \
  -d "client_id=inference-client" \
  -d "client_secret=${CLIENT_SECRET}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# Call the model (path includes namespace and model name)
curl -sk --noproxy '*' \
  --resolve "inference.${DOMAIN}:443:${GATEWAY_IP}" \
  "https://inference.${DOMAIN}/llm-inference/qwen3-0-6b/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

Keycloak tokens expire after 15 minutes. Re-request using the same client credentials.

### Python (OpenAI SDK)

```python
from openai import OpenAI
import httpx

# litellm mode
client = OpenAI(
    base_url="https://litellm.solutions.ai/v1",
    api_key="<TOKEN>",
    http_client=httpx.Client(verify=False),   # for self-signed TLS
)

# keycloak mode
# client = OpenAI(
#     base_url="https://inference.solutions.ai/llm-inference/qwen3-0-6b/v1",
#     api_key="<JWT_TOKEN>",
#     http_client=httpx.Client(verify=False),
# )

response = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=64,
)
print(response.choices[0].message.content)
```

For LangChain, LlamaIndex, CrewAI, Cursor, n8n, and others → see [Integration Guide](../customize/integration.md).

---

## Status, scaling, and logs

```bash
# List all deployed models
kubectl get llminferenceservices,inferenceservices -n llm-inference

# Scale a model to 2 replicas
kubectl patch llminferenceservice qwen3-0-6b -n llm-inference \
  -p '{"spec":{"replicas":2}}' --type=merge

# Tail the serving logs
kubectl logs -n llm-inference -l serving.kserve.io/inferenceservice=qwen3-0-6b -f
```

---

## Traditional ML models (sklearn, XGBoost, PyTorch, TF, ONNX, Triton)

KServe is a general-purpose model server — LLMs are just one workload. Deploy classical ML models on the same stack with the same auth and gateway.

```yaml
# my-sklearn-model.yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: iris-classifier
  namespace: llm-inference
spec:
  predictor:
    sklearn:
      storageUri: "pvc://model-store/iris/"
      resources:
        requests:
          cpu: "200m"
          memory: "512Mi"
```

```bash
kubectl apply -f my-sklearn-model.yaml
```

These models are served via the Open Inference Protocol at `/v2/models/<name>/infer` — same gateway, same JWT. See [Integration Guide](../customize/integration.md#traditional-ml-models-sklearn-xgboost-pytorch-tensorflow-onnx) for details.

---

## Undeploy

```bash
# Stop one model (weights remain on the PVC)
./model-manager undeploy qwen3-0-6b

# Stop all models
./model-manager undeploy all
```

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Connect LangChain, LlamaIndex, agents, or a chat UI to a deployed model | [Integration Guide](../customize/integration.md) |
| Look up every `model-manager` command and flag | [CLI Reference](../customize/cli.md) |
| Tune per-node CPU pinning for the model pods you just deployed | [NRI CPU Balloons](../customize/nri_cpu_balloons.md) |
| Change auth mode, storage backend, or other platform-wide settings | [Configuration Reference](../customize/configuration.md) |
