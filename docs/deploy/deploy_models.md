# Deploy an LLM on Intel® Silicon with model-manager

[← Docs Index](../README.md)

This guide shows how to start serving a model, send it requests, and stop it again,
using the `model-manager` command. It assumes the platform is already installed
(see the [Deployment Guide](install_platform.md)).

---

## The model-manager

`./model-manager` is the command that manages models on the cluster. For each model it:

1. downloads the weights from [Hugging Face](https://huggingface.co) into shared
   storage (the `model-store` volume), once;
2. sizes the serving pod (CPU, memory, CPU pinning) for your nodes;
3. creates the KServe resources that run the model server (vLLM or OpenVINO);
4. connects the model to the gateway, so it answers at one OpenAI-compatible URL.

[KServe](../glossary.md) is the Kubernetes add-on that runs model servers. You do not
need to write any KServe YAML yourself.

```bash
./model-manager <command> [options]
```

| Command | What it does |
|---|---|
| `list` | The catalog models and whether each is deployed, plus any other deployed models. `--catalog` shows only the catalog (no cluster needed); `--deployed` only what runs |
| `deploy <name>` | Download the weights (if needed) and start serving a catalog model |
| `deploy --hfid <org/repo>` | Deploy any Hugging Face model without a catalog entry |
| `undeploy <name>` | Stop serving a model. The weights stay in storage, so a later deploy is fast |
| `undeploy all` | Stop every deployed model |
| `cpu-collisions-check` | Report CPU pinning and NUMA layout, and spot cores shared by two models |

Every flag is listed in the [CLI Reference](../customize/cli.md).

### Which environment it uses

Run `./model-manager` from the repository root. It works on one environment (`env/<name>/`):

- With only one environment, it uses that one.
- With several, it asks which one, or set it yourself:
  `TKM_ENV=prod ./model-manager list`.

It then uses that environment's kubeconfig (`env/<name>/kubeconfig.yaml`), settings
(`global_config.yaml`, `config.inference.yaml`) and model catalog (`models.yaml`).

> [!NOTE]
> `--env` on `model-manager` does **not** pick the environment. It sets an
> environment variable inside the model container (`--env KEY=VALUE`).

---

## Deploy a model from the catalog

The catalog is `env/<name>/models.yaml`. `init inference` copies it from the inference
repository, and it is yours to edit.

1. See what is available:

   ```bash
   ./model-manager list
   ```

2. Deploy a model. Open models such as `qwen3-0-6b` need no account:

   ```bash
   ./model-manager deploy qwen3-0-6b --wait
   ```

   Gated models (Llama, some Mistral models) need a Hugging Face token from an account
   that accepted the model's licence:

   ```bash
   export HF_TOKEN="hf_your_token_here"
   ./model-manager deploy llama3-8b-awq --wait
   ```

3. Wait. `--wait` blocks until the model is ready, or until `--wait-timeout`
   (default 900 seconds) runs out. The first deploy of a model also downloads its
   weights, so it takes longer than later ones.

4. Read the result. When the model is ready, `model-manager` prints a box with the
   model's endpoint (URL) and the two commands to call it: one that gets a credential,
   and a `curl` request. Those commands match your settings (sign-in mode and routing
   mode), so they are the quickest way to a first answer. The same steps are explained
   in [Access the model](#access-the-model).

---

## Deploy any Hugging Face model (no catalog entry)

```bash
./model-manager deploy --hfid Qwen/Qwen3-0.6B --cpu 4 --memory 8Gi --wait
./model-manager deploy --hfid meta-llama/Llama-3.2-1B-Instruct --cpu 16 --memory 24Gi --wait
```

The model's name is made from the repository ID; `--name <name>` picks one yourself.

The flags used most often:

| Flag | Default | What it does |
|---|---|---|
| `--hfid <org/repo>` | — | Hugging Face repository to deploy (`--id` still works but is deprecated) |
| `--name <name>` | from the repository ID | Name of the deployed model (with `--hfid`) |
| `--server <name>` | `vllm` | Model server: `vllm`, `openvino` or `vllm-cuda` (NVIDIA GPUs). `--runtime` is the same flag |
| `--cpu <N>` | from the catalog or category | CPU cores for the model pod |
| `--memory <size>` | from the catalog or category | Memory for the model pod, for example `24Gi` |
| `--replicas <N>` | `1` | Fixed number of copies of the model (turns autoscaling off) |
| `--min-replicas` / `--max-replicas` | — | Autoscale between these counts. Needs `keda_enabled: true` and a KServe with KEDA scaling (installed with `envoy_gateway_upgrade: true`) |
| `--tp <N>` | `1` | Split the model across N NUMA nodes (tensor parallelism) |
| `--node <name>` | — | Run the model on this node only |
| `--wait` | off | Wait until the model is ready |
| `--dry-run` | off | Print the resources it would create, and change nothing |

---

## Model catalog format (`env/<name>/models.yaml`)

```yaml
defaults:
  namespace: llm-inference       # where model pods run
  runtime: vllm                  # model server when an entry names none
  replicas: 1
  require_amx: true              # only schedule on nodes with Intel AMX

storage:
  pvc_name: model-store          # shared volume that holds downloaded weights

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

Only `name` and `model_id` are required. The fields used most:

| Field | What it means |
|---|---|
| `name` | The name you use with `model-manager`, and the `"model"` value in API requests |
| `model_id` | Hugging Face repository to download |
| `category` | `llm`, `embed`, `rerank` or `vlm`. Picks default server arguments and sizes |
| `runtime` + `server_version` | Model server (`vllm`, `openvino`, `vllm-cuda`) and its version |
| `servers` + `default_server` | Instead of the two above: the servers and versions this model may run on |
| `cpu` / `memory` | Resources for the model pod. `cpu: auto` sizes it to the free cores of one NUMA node |
| `gpus` | GPUs per pod, for a GPU server such as `vllm-cuda` |
| `replicas` | Number of copies. Or `autoscaling: {min_replicas, max_replicas, scale_target}` with KEDA |
| `node` | Run on this node only |
| `autodeploy` | `true`: deploy this model during `install` |
| `args` / `env` | Extra model-server arguments and environment variables. Tensor parallelism goes here as `--tensor-parallel-size`, or use `--tp` when deploying |

The full field list, with comments, is at the top of the catalog file itself.

---

## Access the model

Every model answers at one OpenAI-compatible URL; the `"model"` field in the request
body picks the model. How a program proves who it is depends on `auth_type`
(in `env/<name>/config.inference.yaml`, or forced for every layer in `global_config.yaml`).

| | **Keycloak** (`auth_type: jwt`, the default) | **LiteLLM** (`auth_type: litellm_vkey`) |
|---|---|---|
| **Credential** | A short-lived token (JWT) from Keycloak | An API key from LiteLLM (the master key, or a virtual key you create) |
| **Endpoint** (subdomain mode) | `https://inference.<base_domain_name>/v1/...` | `https://litellm.<base_domain_name>/v1/...` |
| **Endpoint** (path mode) | `https://<base_domain_name>/inference/v1/...` | `https://<base_domain_name>/litellm/v1/...` |
| **Good for** | Single sign-on and per-user access rules | Per-key budgets, rate limits and spend tracking |

Keycloak is installed in both modes; it also signs people in to the web UIs.
The examples below use subdomain mode, the default.

### 1. Common setup

1. Point `kubectl` at your cluster (replace `<env>` with your environment name, for
   example `local`):

   ```bash
   export KUBECONFIG=$(pwd)/env/<env>/kubeconfig.yaml
   ```

2. Find the gateway's IP address and your domain:

   ```bash
   GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
     -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
     -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

   DOMAIN=$(yq '.base_domain_name' env/<env>/global_config.yaml)
   ```

   The domain (default `inteltoolkits.ai`) is usually not in your DNS. That is why the
   `curl` commands below use `--resolve <host>:443:<gateway IP>`: it sends the request to
   the gateway while keeping the right host name. For a browser, add the host names to
   your DNS or to `/etc/hosts`, pointing at the gateway IP.

3. Trust the certificate. With the default self-signed TLS, the installer saves its CA
   certificate to `env/<env>/logs/ai-solutions-ca.crt`. The `curl` commands below pass it
   with `--cacert`; set `SSL_CERT_FILE` to it for Python clients, or import it into your
   browser or OS:

   ```bash
   CA=$(pwd)/env/<env>/logs/ai-solutions-ca.crt
   ```

### 2a. Keycloak mode (`auth_type: jwt`)

Programs use the **client credentials** flow: a client ID and secret are exchanged for a
token, with no user password.

1. Get a token. The helper script finds the gateway, the Keycloak host and the client
   credentials for you:

   ```bash
   source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
   # sets TOKEN, GATEWAY_IP, GATEWAY_DOMAIN and GATEWAY_URL
   ```

   The token is valid for 15 minutes. Run the script again for a new one, or ask for a
   longer validity with `--lifespan 3600` (seconds; this changes the Keycloak client's
   setting).

   <details>
   <summary>The same without the script</summary>

   ```bash
   KEYCLOAK_DOMAIN=$(kubectl get keycloaks -n keycloak \
     -o jsonpath='{.items[0].spec.hostname.hostname}')
   CLIENT_ID=$(kubectl get secret keycloak-client-secret -n keycloak \
     -o jsonpath='{.data.client-id}' | base64 -d)
   CLIENT_SECRET=$(kubectl get secret keycloak-client-secret -n keycloak \
     -o jsonpath='{.data.client-secret}' | base64 -d)

   TOKEN=$(curl -s --cacert "$CA" --noproxy '*' \
     --resolve "${KEYCLOAK_DOMAIN}:443:${GATEWAY_IP}" \
     "https://${KEYCLOAK_DOMAIN}/realms/inference/protocol/openid-connect/token" \
     -d "grant_type=client_credentials" \
     -d "client_id=${CLIENT_ID}" \
     -d "client_secret=${CLIENT_SECRET}" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
   ```
   </details>

2. Call the model:

   ```bash
   curl -s --cacert "$CA" --noproxy '*' \
     --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
     "https://${GATEWAY_DOMAIN}/v1/chat/completions" \
     -H "Authorization: Bearer ${TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
   ```

   A JSON answer with `"choices"` means it works. A missing, wrong or expired token gets
   `401` from the gateway before anything reaches the model.

3. List the models the gateway serves:

   ```bash
   curl -s --cacert "$CA" --noproxy '*' --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
     "https://${GATEWAY_DOMAIN}/v1/models" -H "Authorization: Bearer ${TOKEN}"
   ```

### 2b. LiteLLM mode (`auth_type: litellm_vkey`)

[LiteLLM](../glossary.md) is a proxy in front of the models that checks API keys and
tracks spend. `model-manager deploy` registers every model with it.

1. Get the master key (full admin rights; keep it secret):

   ```bash
   MASTER_KEY=$(kubectl get secret litellm-master-key -n litellm \
     -o jsonpath='{.data.master_key}' | base64 -d)
   LITELLM_DOMAIN="litellm.${DOMAIN}"
   ```

2. Call the model:

   ```bash
   curl -s --cacert "$CA" --noproxy '*' \
     --resolve "${LITELLM_DOMAIN}:443:${GATEWAY_IP}" \
     "https://${LITELLM_DOMAIN}/v1/chat/completions" \
     -H "Authorization: Bearer ${MASTER_KEY}" \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
   ```

   Add `"stream": true` to the body for a streamed answer. A missing or revoked key gets
   `401` from LiteLLM. `GET /v1/models` lists every registered model.

   The public `litellm.<domain>` address only answers `/v1/chat/completions`,
   `/v1/completions`, `/v1/embeddings`, `/v1/models` and `/health/liveliness`.

3. Give applications their own **virtual key** instead of the master key. A virtual key
   can be limited to some models, a budget and an expiry. The key API is not on the
   public address, so reach LiteLLM through a port-forward:

   ```bash
   kubectl port-forward -n litellm svc/litellm 4000:4000 &

   # Create: returns {"key": "sk-..."}
   VIRTUAL_KEY=$(curl -s http://localhost:4000/key/generate \
     -H "Authorization: Bearer ${MASTER_KEY}" \
     -H "Content-Type: application/json" \
     -d '{"models":["qwen3-0-6b"],"key_alias":"team-a","max_budget":5,"duration":"24h"}' \
     | jq -r '.key')

   # List keys, with alias, allowed models, budget and spend
   curl -s "http://localhost:4000/key/list?return_full_object=true" \
     -H "Authorization: Bearer ${MASTER_KEY}"

   # Change a key (for example, raise its budget)
   curl -s http://localhost:4000/key/update -H "Authorization: Bearer ${MASTER_KEY}" \
     -H "Content-Type: application/json" -d '{"key":"sk-...","max_budget":20}'

   # Revoke a key
   curl -s http://localhost:4000/key/delete -H "Authorization: Bearer ${MASTER_KEY}" \
     -H "Content-Type: application/json" -d '{"keys":["sk-..."]}'
   ```

   Use the virtual key exactly like the master key in step 2.

4. Or manage keys in the LiteLLM admin UI, under **Virtual Keys**:
   `https://litellm-ui.<base_domain_name>/ui/` (path mode:
   `https://<base_domain_name>/litellm/ui/`). The trailing slash is required. You sign
   in with Keycloak; only `litellm_ui_groups` get in (default: the `platform-admins`
   group). Request traces appear in Langfuse at `https://langfuse.<base_domain_name>`.

<p align="center">
  <img src="../assets/screenshots/litellm-langfuse.png" alt="LiteLLM virtual keys and Langfuse traces" />
</p>

More views of both UIs are in [docs/assets/screenshots/](../assets/screenshots/).

### 3. Python (OpenAI SDK)

```python
import os
from openai import OpenAI

# Keycloak mode: after `source .../get-keycloak-token.sh`
client = OpenAI(
    base_url=os.environ["GATEWAY_URL"] + "/v1",   # https://inference.<domain>/v1
    api_key=os.environ["TOKEN"],
)

# LiteLLM mode
# client = OpenAI(base_url="https://litellm.inteltoolkits.ai/v1", api_key="<virtual key>")

response = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=64,
)
print(response.choices[0].message.content)
```

Run it with `SSL_CERT_FILE=env/<env>/logs/ai-solutions-ca.crt` so Python trusts the
self-signed certificate. Your machine must also resolve the host name to the gateway
(DNS or `/etc/hosts`); Python has no `--resolve`.

For LangChain, LlamaIndex, CrewAI, Cursor, n8n and others, see the
[Integration Guide](../customize/integration.md).

### Credential lifetime

| `auth_type` | Lifetime | Renew |
|---|---|---|
| `jwt` | 15 minutes (change with `--lifespan`) | Source the helper script again |
| `litellm_vkey` | A virtual key lasts until its `duration` runs out (none set: no expiry) | Create a new virtual key |

---

## Status, scaling and logs

```bash
# What is deployed
./model-manager list --deployed
kubectl get llminferenceservices,inferenceservices -n llm-inference

# Fixed replica count: scale a model to 2 copies
kubectl patch llminferenceservice qwen3-0-6b -n llm-inference \
  -p '{"spec":{"replicas":2}}' --type=merge

# Autoscaled models (deployed with --max-replicas)
kubectl get scaledobject,hpa -n llm-inference

# Follow the model server's logs
kubectl logs -n llm-inference -l app.kubernetes.io/name=qwen3-0-6b -f
```

---

## Traditional ML models (sklearn, XGBoost, PyTorch, TF, ONNX, Triton)

KServe also serves classic ML models next to the LLMs. `model-manager` does not deploy
these; you write the KServe `InferenceService` yourself:

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

They speak the Open Inference Protocol (`/v2/models/<name>/infer`). How to reach and
call them is in the [Integration Guide](../customize/integration.md#traditional-ml-models).

---

## Undeploy

```bash
# Stop one model (its weights stay in storage)
./model-manager undeploy qwen3-0-6b

# Wait until its pods are gone and its CPUs are free
./model-manager undeploy qwen3-0-6b --wait

# Stop every model
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
