# Deploy an LLM on Intel® Silicon with model-manager

[← Docs Index](../README.md)

How to deploy, access, and manage self-hosted LLM inference on Toolkit Manager using the `model-manager` CLI. It assumes the platform is already installed (see the [Deployment Guide](install_platform.md)).

---

## The model-manager

`./model-manager` is the CLI for LLM lifecycle management. For each model it downloads the weights from [Hugging Face](https://huggingface.co) into shared storage (the `model-store` volume) once, sizes the serving pod (CPU, memory, CPU pinning) for your nodes, creates the [KServe](../glossary.md) resources that run the model server (vLLM or OpenVINO), and connects the model to the gateway (and to LiteLLM in LiteLLM mode). You do not write any KServe YAML yourself.

```
./model-manager <command> [options]
```

| Command | What it does |
|---|---|
| `list` | Show the catalog models and whether each is deployed, plus any other deployed models. `--catalog` shows only the catalog (no cluster needed); `--deployed` only what runs |
| `deploy <name>` | Download weights (if needed) and start serving a catalog model |
| `deploy --hfid <org/repo>` | Deploy any Hugging Face model without a catalog entry |
| `undeploy <name>` | Stop serving (weights stay on the PVC, so a later deploy is fast) |
| `undeploy all` | Stop all models |
| `cpu-collisions-check` | Report CPU pinning and NUMA layout, and spot cores shared by two models |

There is no `status` command: `list` shows each model's status. Every flag is in the [CLI Reference](../customize/cli.md).

Run `./model-manager` from the repository root. It works on one environment (`env/<name>/`): with only one environment it uses that one; with several it asks which one, or you set it with `TKM_ENV=prod ./model-manager list`. It then uses that environment's kubeconfig, settings (`global_config.yaml`, `config.inference.yaml`) and catalog (`models.yaml`).

> [!NOTE]
> `--env` on `model-manager` does **not** pick the environment. It sets an
> environment variable inside the model container (`--env KEY=VALUE`).

---

## Deploy from the catalog

The model catalog lives at `env/<name>/models.yaml`. It is seeded by `init` and is yours to edit.

```bash
# List what's available
./model-manager list

# Deploy a model (no HF token needed for open models)
./model-manager deploy qwen3-0-6b --wait

# Deploy a gated model (Llama, some Mistral models): needs a Hugging Face token
# from an account that accepted the model's licence
export HF_TOKEN="hf_your_token_here"
./model-manager deploy llama3-8b-awq --wait
```

`--wait` blocks until the model is ready, or until `--wait-timeout` (default 900 seconds) runs out; the first deploy of a model also downloads its weights. When it returns it prints a box with the model's endpoint and two ready-to-run commands: one that gets a credential and a `curl` request. They match your settings (`auth_type` and routing mode), so they are the quickest way to a first answer: `inference.<base_domain_name>` with `auth_type: jwt`, `litellm.<base_domain_name>` with `auth_type: litellm_vkey`. The same steps are explained under [Access the model](#access-the-model).

---

## Deploy any Hugging Face model (ad-hoc)

No catalog entry needed:

```bash
./model-manager deploy --hfid Qwen/Qwen3-0.6B --cpu 4 --memory 8Gi --wait
./model-manager deploy --hfid meta-llama/Llama-3.2-1B-Instruct --cpu 16 --memory 24Gi --wait
```

The model's name is made from the repository ID; `--name <name>` picks one yourself.

Key flags:

| Flag | Default | Description |
|---|---|---|
| `--hfid` | — | Hugging Face repo ID (`--id` still works but is deprecated) |
| `--name` | from the repo ID | Name of the deployed model (with `--hfid`) |
| `--cpu` | from catalog or category | CPU cores for the serving pod |
| `--memory` | from catalog or category | Memory for the serving pod (e.g. `24Gi`) |
| `--replicas` | `1` | Fixed number of serving replicas (turns autoscaling off) |
| `--min-replicas` / `--max-replicas` | — | Autoscale between these counts. Needs `keda_enabled: true` and a KServe with KEDA scaling (installed with `envoy_gateway_upgrade: true`) |
| `--tp` | `1` | Tensor parallelism — splits the model across NUMA nodes |
| `--server` | `vllm` | Model server: `vllm` \| `openvino` \| `vllm-cuda` (NVIDIA GPUs). `--runtime` is the same flag |
| `--node` | — | Run the model on this node only (the pod stays `Pending` if it cannot) |
| `--wait` | — | Wait for model to be ready |
| `--dry-run` | — | Print the manifest without applying |

---

## Model catalog format (`env/<name>/models.yaml`)

```yaml
defaults:
  namespace: llm-inference
  runtime: vllm
  replicas: 1
  require_amx: true          # only schedule model pods on nodes with Intel AMX
storage:
  pvc_name: model-store

models:
- name: qwen3-0-6b
  model_id: Qwen/Qwen3-0.6B
  category: llm
  servers:
    vllm: { versions: ["0.24.0", "0.19.1"], default: "0.24.0" }
  default_server: vllm
  cpu: 8
  memory: 16Gi

- name: llama3-8b-awq
  model_id: casperhansen/llama-3-8b-instruct-awq
  category: llm
  runtime: vllm
  server_version: "0.24.0"
  cpu: 16
  memory: 32Gi
```

Only `name` and `model_id` are required; the full field list, with comments, is at the top of the catalog file itself.

| Field | Description |
|---|---|
| `name` | Alias used with model-manager commands, and the `"model"` value in API requests |
| `model_id` | Hugging Face repo to download |
| `category` | `llm`, `embed`, `rerank` or `vlm`; picks default server arguments and sizes |
| `runtime` + `server_version` | Model server (`vllm`, `openvino`, `vllm-cuda`) and its version |
| `servers` + `default_server` | Instead of the two above: the servers and versions this model may run on |
| `cpu` | CPU core request for the serving pod; `cpu: auto` sizes it to the free cores of one NUMA node |
| `memory` | Memory for the serving pod |
| `gpus` | GPUs per pod, for a GPU server such as `vllm-cuda` |
| `replicas` | Number of pods to run; or `autoscaling: {min_replicas, max_replicas, scale_target}` with KEDA |
| `node` | Pin to a specific node name (optional) |
| `autodeploy` | `true`: deploy this model during `install` |
| `args` / `env` | Extra model-server arguments and environment variables. Tensor parallelism goes in `args` as `--tensor-parallel-size`, or use `--tp` when deploying |

---

## Access the model

After a successful deploy, the model-manager prints the exact commands for your `auth_type`. Refer to those directly, or use the patterns below. Every model answers at one OpenAI-compatible URL; the `"model"` field in the request body selects the model. The examples use subdomain mode (the default); in path mode the URLs are `https://<domain>/litellm/v1/...` and `https://<domain>/inference/v1/...`.

First, find the gateway IP, your domain and the installer's CA certificate (default self-signed TLS), so `curl` can check the certificate instead of skipping it with `-k`:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml

GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
CA=$(pwd)/env/local/logs/ai-solutions-ca.crt
```

The domain (default `inteltoolkits.ai`) is usually not in your DNS, so the commands use `--resolve <host>:443:<gateway IP>` to send the request to the gateway while keeping the right host name.

### With `auth_type: litellm_vkey`

LiteLLM acts as the unified OpenAI-compatible proxy. All models share the same base URL; the `"model"` field in the request body selects which one.

```bash
TOKEN=$(kubectl get secret litellm-master-key -n litellm \
  -o jsonpath='{.data.master_key}' | base64 -d)

curl -s --cacert "$CA" --noproxy '*' \
  --resolve "litellm.${DOMAIN}:443:${GATEWAY_IP}" \
  "https://litellm.${DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

The master key has full admin rights. Give applications a scoped virtual key instead, for per-key model lists, budgets and expiry. Create one in the LiteLLM admin UI (`https://litellm-ui.${DOMAIN}/ui/`, trailing slash required; sign-in with Keycloak, `platform-admins` group by default) or through the key API. The key API is not on the public `litellm.<domain>` address (it only answers the `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` and `/v1/models` paths, plus `/health/liveliness`), so it is used through `kubectl port-forward`. See
[Deploying and Accessing Models](install_platform.md#option-b--litellm-virtual-keys-auth_type-litellm_vkey)
for minting, listing, and revoking keys.

### With `auth_type: jwt`

Uses the inference gateway: one host, `inference.<domain>`, for every model. The helper script gets a token (client credentials flow) and finds the gateway for you:

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Exports: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN, GATEWAY_URL

# Call the model (the "model" field picks the model)
curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
  "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

Keycloak tokens expire after 15 minutes. Source the script again for a new one, or pass `--lifespan 3600` for one hour. The same steps without the script are in [Deploying and Accessing Models](install_platform.md#option-a--keycloak-jwt-auth_type-jwt-default).

### Python (OpenAI SDK)

```python
import os
from openai import OpenAI

# litellm mode
client = OpenAI(
    base_url="https://litellm.inteltoolkits.ai/v1",
    api_key="<TOKEN>",
)

# keycloak mode: after `source .../get-keycloak-token.sh`
# client = OpenAI(
#     base_url=os.environ["GATEWAY_URL"] + "/v1",   # https://inference.inteltoolkits.ai/v1
#     api_key=os.environ["TOKEN"],
# )

response = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=64,
)
print(response.choices[0].message.content)
```

Run it with `SSL_CERT_FILE=env/local/logs/ai-solutions-ca.crt` so Python trusts the self-signed certificate, and make the host name resolve to the gateway IP (DNS or `/etc/hosts`).

For LangChain, LlamaIndex, CrewAI, Cursor, n8n, and others → see [Integration Guide](../customize/integration.md).

---

## Status, scaling, and logs

```bash
# List all deployed models
./model-manager list --deployed
kubectl get llminferenceservices,inferenceservices -n llm-inference

# Scale a model to 2 replicas (fixed replica count)
kubectl patch llminferenceservice qwen3-0-6b -n llm-inference \
  -p '{"spec":{"replicas":2}}' --type=merge

# Autoscaled models (deployed with --max-replicas)
kubectl get scaledobject,hpa -n llm-inference

# Tail the serving logs
kubectl logs -n llm-inference -l app.kubernetes.io/name=qwen3-0-6b -f
```

---

## Traditional ML models (sklearn, XGBoost, PyTorch, TF, ONNX, Triton)

KServe is a general-purpose model server — LLMs are just one workload. It also serves classical ML models on the same stack; `model-manager` does not deploy these, so you write the KServe `InferenceService` yourself:

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

These models are served via the Open Inference Protocol at `/v2/models/<name>/infer`. How to reach and call them is in the [Integration Guide](../customize/integration.md#traditional-ml-models).

---

## Undeploy

```bash
# Stop one model (weights remain on the PVC)
./model-manager undeploy qwen3-0-6b

# Wait until its pods are gone and its CPUs are free
./model-manager undeploy qwen3-0-6b --wait

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
| See how a request travels from your client to the model | [Network Architecture](networking.md) |
