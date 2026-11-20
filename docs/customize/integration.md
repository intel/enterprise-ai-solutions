# Integrating Your Application

[← Docs Index](../README.md)

How to connect **your tool, framework, or application** to the Toolkit Manager inference stack, without changing the stack itself.

> **TL;DR:** point any OpenAI-compatible client at the inference endpoint and send a Bearer token. The `"model"` field in the request picks the model.
> - `auth_type: jwt` (default): `https://inference.<domain>/v1` with a Keycloak JWT.
> - `auth_type: litellm_vkey`: `https://litellm.<domain>/v1` with a LiteLLM key.
>
> With `routing_mode: path` these are `https://<domain>/inference/v1` and `https://<domain>/litellm/v1`.

---

## Why integration is easy

The stack is built on open standards. Anything that speaks them works:

| Contract | Provided by | What you get |
|---|---|---|
| **OpenAI REST API** (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) | vLLM / OpenVINO™ Model Server behind KServe, reached through the AI gateway (or LiteLLM) | Works with the whole OpenAI client ecosystem |
| **Open Inference Protocol** (KServe v2, `/v2/models/<m>/infer`) | KServe predictors (sklearn, XGBoost, PyTorch, TensorFlow, ONNX, Triton, custom) | Serve **traditional ML** models on the same stack |
| **Bearer token auth** | Keycloak OIDC JWT (`auth_type: jwt`) or LiteLLM keys (`auth_type: litellm_vkey`) | A standard `Authorization: Bearer <token>` header works with any HTTP client |
| **Gateway API + Kubernetes CRDs** | Envoy Gateway, KServe, cert-manager, Istio ambient | Add models, routes and policies declaratively, with no stack edits |

`auth_type` is set in `env/<env>/config.inference.yaml`. See [Accessing Models](../../ext/enterprise.ai-inference/docs/deploy/accessing_models.md) in the inference repo for both modes in full.

---

## The 30-second integration

Run these from the Toolkit Manager checkout, on a machine that can reach the cluster.

### 1. Get the gateway address and a token

First point `kubectl` at the cluster and find the gateway:

```bash
ENV=local                                   # the --env you installed with
export KUBECONFIG=$(pwd)/env/$ENV/kubeconfig.yaml
CA=$(pwd)/env/$ENV/logs/ai-solutions-ca.crt # the installer's CA (selfsigned TLS)

# Gateway LoadBalancer IP (from MetalLB)
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
DOMAIN=$(yq '.base_domain_name' env/$ENV/global_config.yaml)
```

#### With `auth_type=litellm_vkey`

```bash
GATEWAY_DOMAIN="litellm.${DOMAIN}"          # path mode: GATEWAY_DOMAIN=$DOMAIN
GATEWAY_URL="https://${GATEWAY_DOMAIN}"     # path mode: https://$DOMAIN/litellm

# The master key has full admin rights: use it to try things out, then give apps a virtual key
TOKEN=$(kubectl get secret litellm-master-key -n litellm \
  -o jsonpath='{.data.master_key}' | base64 -d)
```

To create a scoped virtual key (its own models, budget and expiry), see [Virtual keys](../../ext/enterprise.ai-inference/docs/deploy/accessing_models.md#virtual-keys).

#### With `auth_type=jwt` (default)

The inference repo ships a helper that finds the gateway, reads the Keycloak client credentials from the cluster, and fetches a token:

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Sets: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN, GATEWAY_URL (already right for subdomain or path mode)
```

Tokens last 15 minutes by default. Run the `source` line again for a new one, or add `--lifespan 3600` for one hour.

<details>
<summary>What the helper does (subdomain mode), if you want to do it by hand</summary>

```bash
KEYCLOAK_DOMAIN="keycloak.${DOMAIN}"
GATEWAY_DOMAIN="inference.${DOMAIN}"
GATEWAY_URL="https://${GATEWAY_DOMAIN}"

# OIDC client credentials, created by the inference layer's keycloak_config role
CLIENT_ID=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-id}' | base64 -d)
CLIENT_SECRET=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-secret}' | base64 -d)

# Exchange them for a JWT (client-credentials flow, for service-to-service calls)
TOKEN=$(curl -s --noproxy '*' --cacert "$CA" --resolve ${KEYCLOAK_DOMAIN}:443:${GATEWAY_IP} \
  https://${KEYCLOAK_DOMAIN}/realms/inference/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d "client_id=${CLIENT_ID}" -d "client_secret=${CLIENT_SECRET}" \
  | jq -r .access_token)
```

</details>

> **Behind a corporate proxy?** `--noproxy '*'` makes curl ignore `$http_proxy` / `$https_proxy` for these calls. Without it, curl sends the request to the proxy and you get the proxy's error page back.

### 2. Call the model

#### With `auth_type=litellm_vkey`

```bash
curl -s --noproxy '*' --cacert "$CA" --resolve ${GATEWAY_DOMAIN}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  ${GATEWAY_URL}/v1/chat/completions \
  -d '{
    "model": "qwen3-0-6b",
    "messages": [{"role":"user","content":"Hello!"}],
    "max_tokens": 50
  }'
```

LiteLLM routes by model name: all models share the same base URL (`/v1/...`), and the `"model"` field in the body picks one.

#### With `auth_type=jwt` (default)

The command is the same, because `GATEWAY_DOMAIN` and `GATEWAY_URL` already point at `inference.<domain>` (path mode: `<domain>/inference`):

```bash
curl -s --noproxy '*' --cacert "$CA" --resolve ${GATEWAY_DOMAIN}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  ${GATEWAY_URL}/v1/chat/completions \
  -d '{
    "model": "qwen3-0-6b",
    "messages": [{"role":"user","content":"Hello!"}],
    "max_tokens": 50
  }'
```

URL pattern: `https://inference.<domain>/v1/...`. The AI gateway picks the model from the `"model"` field, so every model shares one base URL.

- **Success:** a JSON answer with `choices[0].message.content`.
- `"model"` is the name you deployed with `./model-manager deploy <name>`. To list them: `curl ... ${GATEWAY_URL}/v1/models`.
- `--resolve` sends the request to the gateway IP without a DNS entry. Once DNS points at the gateway ([External access](../deploy/networking.md#external-access-configuration)), drop it.

That same endpoint + token is what every framework below uses (`${GATEWAY_URL}/v1`).

---

## Python: OpenAI SDK

```python
import os
import httpx
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["GATEWAY_URL"] + "/v1",
    api_key=os.environ["TOKEN"],                         # Keycloak JWT or LiteLLM key
    http_client=httpx.Client(verify=os.environ["CA"]),   # trust the installer's CA
)

resp = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

Export the shell variables first (`export GATEWAY_URL TOKEN CA`). If the client runs where the gateway's hostname doesn't resolve, add a DNS record or an `/etc/hosts` entry for it.

Streaming, tool calling and embeddings work through the standard OpenAI SDK calls, as long as the model and its server support them.

---

## Integration recipes

Pick your tool. Each one needs only a **base URL and an API key**:

- **Base URL:** `${GATEWAY_URL}/v1`, for example `https://inference.inteltoolkits.ai/v1` (`jwt`) or `https://litellm.inteltoolkits.ai/v1` (`litellm_vkey`).
- **API key:** `${TOKEN}`.

The examples read them from `BASE_URL` and `TOKEN` environment variables (`export BASE_URL=${GATEWAY_URL}/v1`). Tools that verify TLS also need the CA file, or a certificate from a CA they already trust (`gateway_tls_mode: custom`).

### RAG — LangChain

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
    model="qwen3-0-6b",
)
# Use any LangChain chain / retriever / vectorstore on top.
```

### RAG — LlamaIndex

```python
import os
from llama_index.llms.openai_like import OpenAILike

llm = OpenAILike(
    api_base=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
    model="qwen3-0-6b",
    is_chat_model=True,
)
```

### Agents — LangGraph

```python
import os
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
    model="qwen3-0-6b",
)
agent = create_react_agent(llm, tools=[...])
```

### Agents — CrewAI

```python
import os
from crewai import LLM, Agent

llm = LLM(
    model="openai/qwen3-0-6b",
    base_url=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
)
agent = Agent(role="Researcher", llm=llm, ...)
```

### Agents — OpenAI Agents SDK

```python
import os
from openai import AsyncOpenAI
from agents import Agent, OpenAIChatCompletionsModel

client = AsyncOpenAI(
    base_url=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
)
agent = Agent(
    name="MyAgent",
    model=OpenAIChatCompletionsModel("qwen3-0-6b", openai_client=client),
)
```

### MCP (Model Context Protocol)

Any app (an MCP host such as Claude Desktop, Cursor or Continue, among others) that lets you set a custom OpenAI-compatible endpoint can use the stack:

```
Base URL: https://inference.inteltoolkits.ai/v1   (auth_type: jwt)
      or: https://litellm.inteltoolkits.ai/v1     (auth_type: litellm_vkey)
API Key:  <Keycloak JWT or LiteLLM key>
```

A Keycloak JWT expires (15 minutes by default), so long-running apps are easier with a LiteLLM virtual key.

### Vector DBs (RAG storage)

These run **alongside** the stack; your app talks to them directly. For example:

| Vector DB | Notes |
|---|---|
| **pgvector** | A PostgreSQL extension: create a database with `CREATE EXTENSION vector` on a PostgreSQL that ships it. The platform's PostgreSQL (CloudNativePG) is set up for the platform services, not for app data. |
| **Milvus** | `helm install milvus milvus/milvus -n milvus --create-namespace` |
| **Qdrant** | `helm install qdrant qdrant/qdrant -n qdrant --create-namespace` |
| **Weaviate** | `helm install weaviate weaviate/weaviate -n weaviate --create-namespace` |
| **Chroma** | `helm install chromadb chromadb/chromadb` |

The stack doesn't need to know about them: only your application code uses them. For a ready-made RAG stack, see [Getting Started with Intel® Chatbot Toolkit](../quickstart/getting_started_rag.md).

### Eval & Observability

| Tool | How it integrates |
|---|---|
| **Langfuse** | Installed with the stack whenever LiteLLM is on (`auth_type: litellm_vkey`, or `litellm_enabled: true`), unless `langfuse_enabled: false`. LiteLLM sends traces to it, with no client-side code. Open `https://langfuse.<base_domain_name>`. |
| **Phoenix / Arize** | Use the OpenInference instrumentor for OpenAI; spans go to your Phoenix collector. |
| **Ragas / DeepEval** | Pass the same `ChatOpenAI` / `OpenAILike` LLM into the eval harness. |
| **Promptfoo** | Use provider `openai:chat:qwen3-0-6b` with `apiBaseUrl` and `apiKey` in `promptfoo.yaml`. |
| **OpenLLMetry** | OpenTelemetry SDK for LLM calls; export to your OTEL collector. |

### Chat UIs

| UI | Setup |
|---|---|
| **Open WebUI** | Settings → Connections → OpenAI API → set the base URL and API key |
| **LibreChat** | `librechat.yaml`: add a custom endpoint with `baseURL` and `apiKey` |
| **AnythingLLM** | LLM Provider = "Generic OpenAI" → base URL and API key |
| **Dify** | Model Provider = OpenAI-API-compatible → base URL and API key |

### Coding assistants

| Tool | Setup |
|---|---|
| **Continue (VS Code/JetBrains)** | `~/.continue/config.json`: `{"provider":"openai","apiBase":"...","apiKey":"...","model":"qwen3-0-6b"}` |
| **Cline** | API Provider = "OpenAI Compatible" → URL and key |
| **Aider** | `aider --openai-api-base ... --openai-api-key ... --model openai/qwen3-0-6b` |
| **Cursor** | Settings → Models → Override OpenAI Base URL and API key |

### Workflow tools

| Tool | How |
|---|---|
| **n8n** | OpenAI node → custom base URL and credentials |
| **Temporal / Airflow / Dagster** | An HTTP or Python step that calls the OpenAI SDK with `BASE_URL` and `TOKEN` |

---

## Authentication options

Pick the option that fits your client:

| Option | Status | When to use | How |
|---|---|---|---|
| **Client-credentials JWT** (default) | Set up by the installer | Service-to-service, batch jobs, agents | `grant_type=client_credentials` against the Keycloak token endpoint ([Step 2](#1-get-the-gateway-address-and-a-token)) |
| **Password JWT** | Keycloak supports it; you configure it | Scripts that sign in as a user | `grant_type=password`. Turn on Direct Access Grants on the client first. |
| **Authorization Code flow** | Keycloak supports it; you configure it | Web apps with single sign-on | Standard OIDC redirect flow against Keycloak; set the client's redirect URIs |
| **LiteLLM virtual key** | Set up by the installer in `litellm_vkey` mode | Many teams or apps, each with its own budget and limits | Set `auth_type: litellm_vkey` in `env/<env>/config.inference.yaml`. Each key has its own model list, budget, rate limit and expiry. Langfuse tracing is on. |

**How the gateway enforces it:**

The gateway's `SecurityPolicy` (`gateway-auth`) requires a Keycloak JWT on every route, in both modes, unless the route has its own policy. Routes that sign people in or check their own credential (Grafana, Langfuse and Keycloak logins, the LiteLLM API) attach their own.

- **`auth_type: litellm_vkey`:** clients call `litellm.<base_domain_name>`. LiteLLM checks the key, applies its budget and rate limit, and forwards to the model. `inference.<base_domain_name>` is not published (requests there get `404`).
- **`auth_type: jwt`:** the gateway checks the Keycloak JWT:
  - **Issuer:** `https://keycloak.<base_domain_name>/realms/inference` (path mode: `https://<base_domain_name>/keycloak/realms/inference`)
  - **Audience:** `inference-client`
  - **Signing keys (JWKS):** fetched inside the cluster from `http://keycloak-service.keycloak.svc.cluster.local:8080/realms/inference/protocol/openid-connect/certs`

  The policy is a standard Envoy Gateway JWT provider. You could point it at another identity provider (Auth0, Okta, Entra ID, ...) that issues tokens with a matching issuer and audience, but the installer only sets up Keycloak.

---

## Adding a new model

You don't have to write Ansible. Use the model-manager:

```bash
./model-manager deploy <model-name>
```

It creates the KServe resources for the model and registers it with the AI gateway (and with LiteLLM, when LiteLLM is on). The model is then reachable at the same endpoint, `${GATEWAY_URL}/v1/...`, with the same token: only the `"model"` field changes. See [Deploy a Model](../deploy/deploy_models.md).

To deploy models at install time instead, mark them `autodeploy: true` in `env/<env>/models.yaml` (with `llm_services_deploy_models: true` in `env/<env>/config.inference.yaml`), or list them in `llm_services_autodeploy_models`. Then run:

```bash
./toolkit_manager.sh install llm_services --env <name>
```

---

## Traditional ML models

Scikit-learn, XGBoost, PyTorch, TensorFlow, ONNX, Triton, and custom servers.

KServe is a **general-purpose model server**: LLMs are just one kind of workload. You can run classic ML models on the same cluster, behind the same gateway and the same auth.

### What's supported out of the box

The inference layer installs KServe's standard serving runtimes (`kserve-runtime-configs`). They speak the **Open Inference Protocol** (KServe v2) at `/v2/models/<model>/infer`. To see which runtimes your cluster has:

```bash
kubectl get clusterservingruntimes
```

| Framework | Predictor | Model artifact |
|---|---|---|
| **scikit-learn** | `sklearn` | `model.joblib` / `model.pkl` |
| **XGBoost** | `xgboost` | `model.bst` / `model.json` |
| **LightGBM** | `lightgbm` | `model.txt` |
| **PyTorch** | `pytorch` (TorchServe) | `.mar` archive |
| **TensorFlow** | `tensorflow` | SavedModel directory |
| **ONNX** | `onnx` (Triton) | `model.onnx` |
| **Triton** (multi-framework) | `triton` | Triton model repository |
| **Hugging Face transformers** (non-LLM) | `huggingface` | HF repo or local directory |
| **PMML** | `pmml` | `model.pmml` |
| **MLflow** | `mlflow` | MLflow model directory |

The installer's own tests cover LLMs served through `model-manager`. These runtimes are KServe's, used as KServe documents them.

### Deploy a traditional ML model

1. Write an `InferenceService` that points at your model file (S3, GCS, PVC, HTTP or OCI):

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
         storageUri: "pvc://<your-pvc>/iris/"   # or s3://, gs://, https://, oci://
         resources:
           requests:
             cpu: "200m"
             memory: "512Mi"
   ```

2. Apply it and wait until `READY` is `True`:

   ```bash
   kubectl apply -f my-sklearn-model.yaml
   kubectl get inferenceservice -n llm-inference iris-classifier -w
   ```

KServe downloads the model, starts the matching predictor, and creates an `HTTPRoute` on the platform gateway. Its hostname is `<name>-<namespace>.<base_domain_name>`, here `iris-classifier-llm-inference.inteltoolkits.ai`.

### Call a traditional ML model

Use the Open Inference Protocol path on the model's own hostname. The gateway still requires a Keycloak JWT:

```bash
ML_HOST=iris-classifier-llm-inference.${DOMAIN}
curl -s --noproxy '*' --cacert "$CA" --resolve ${ML_HOST}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  https://${ML_HOST}/v2/models/iris-classifier/infer \
  -d '{
    "inputs": [{
      "name": "input-0",
      "shape": [1, 4],
      "datatype": "FP32",
      "data": [5.1, 3.5, 1.4, 0.2]
    }]
  }'
```

Only the hostname, path and payload format differ from the OpenAI-style LLM endpoint. This works in subdomain mode; in path mode there is no apex path for these routes.

### Python client example

```python
import os, requests

URL = (f"https://iris-classifier-llm-inference.{os.environ['DOMAIN']}"
       "/v2/models/iris-classifier/infer")

resp = requests.post(
    URL,
    headers={"Authorization": f"Bearer {os.environ['TOKEN']}"},
    json={
        "inputs": [{
            "name": "input-0",
            "shape": [1, 4],
            "datatype": "FP32",
            "data": [5.1, 3.5, 1.4, 0.2],
        }]
    },
    verify=os.environ["CA"],   # the installer's CA (selfsigned TLS)
)
print(resp.json())
```

For richer clients, the [`kserve` Python SDK](https://kserve.github.io/website/) and [Triton client libraries](https://github.com/triton-inference-server/client) speak the same protocol.

### Custom model servers (any framework)

If KServe has no predictor for your framework, or you have your own inference container, use a **custom predictor**:

```yaml
spec:
  predictor:
    containers:
      - name: kserve-container
        image: myregistry/my-custom-server:latest
        ports:
          - containerPort: 8080
            protocol: TCP
```

As long as the container serves HTTP on that port, KServe creates the `HTTPRoute` and the model is reachable through the gateway, with auth.

### Why this matters

The same stack serves:
- **LLMs** (vLLM / OpenVINO Model Server → OpenAI API)
- **Classical ML** (sklearn, XGBoost, ... → Open Inference Protocol)
- **Computer vision** (PyTorch, TensorFlow, ONNX, Triton)
- **Custom predictors** (any container)

…with **one auth flow, one gateway, one observability stack and one way to deploy**.

---

## Routing patterns (host + path)

The stack uses the **Kubernetes Gateway API** (implemented by Envoy Gateway). Every `HTTPRoute` can match on **host**, **path**, **headers**, **method** and **query parameters** at the same time.

### Gateway listener (already deployed)

`roles/gateway/templates/gateway.yaml.j2` creates the `eg-gateway` listeners your routes attach to:

```yaml
listeners:
  - name: https                       # any subdomain, for routes not on a ListenerSet
    protocol: HTTPS
    port: 443
    hostname: "*.<base_domain_name>"
  - name: https-apex                  # the bare domain; every service in path mode
    protocol: HTTPS
    port: 443
    hostname: "<base_domain_name>"
```

Each `HTTPRoute` then claims the exact hostnames and paths it serves. The `*.<base_domain_name>` listener speaks HTTP/1.1 only. Platform services publish their own hosts with ListenerSets on Envoy Gateway 1.9+ ([How services attach](../deploy/networking.md#gateway-resource)).

### What's running today

| Workload | `routing_mode: subdomain` | `routing_mode: path` |
|---|---|---|
| Keycloak | `keycloak.<base>` (whole host) | `<base>/keycloak` |
| Inference API (`jwt`) | `inference.<base>`, path `/v1/...` | `<base>/inference/v1/...` |
| LiteLLM API (`litellm_vkey`) | `litellm.<base>`, path `/v1/...` | `<base>/litellm/v1/...` |

Two rules apply to every route you add to `eg-gateway`:

1. **It needs a Keycloak JWT by default.** The gateway-wide `gateway-auth` policy covers your route too. To use another credential, or none, attach your own `SecurityPolicy` to the route.
2. **Some hostnames are reserved.** A namespace's `gateway.enterprise-ai/hostnames` annotation (comma-separated) grants it hostnames, and an admission policy then rejects any route from another namespace that claims them. So pick a hostname no other namespace has been granted (the platform ones, like `inference.<base>`, are taken). In path mode, only namespaces labelled `gateway.enterprise-ai/allow-apex-routes: "true"` may attach to the apex host, under paths granted in the `gateway-path-grants` ConfigMap. Grants are set by cluster operators.

> [!IMPORTANT]
> The Gateway API decides between two routes for the same hostname by how specific their match is, not by namespace. The hostname grants are what stop a route in one namespace from taking over another's traffic with a more specific match.

### Patterns you can add with `kubectl apply` — no stack changes

#### 1. Subdomain per tenant

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: tenant-a
  namespace: tenant-a
spec:
  parentRefs:
    - name: eg-gateway
      namespace: envoy-gateway-system
  hostnames:
    - tenant-a.<base>
  rules:
    - backendRefs:
        - name: tenant-a-svc
          port: 8080
```

#### 2. Path per tenant on a shared host

```yaml
spec:
  hostnames: [api.<base>]
  rules:
    - matches:
        - path: { type: PathPrefix, value: /tenant-a }
      backendRefs:
        - { name: tenant-a-svc, port: 8080 }
    - matches:
        - path: { type: PathPrefix, value: /tenant-b }
      backendRefs:
        - { name: tenant-b-svc, port: 8080 }
```

#### 3. Version routing (v1 → old, v2 → new)

```yaml
rules:
  - matches: [{ path: { type: PathPrefix, value: /v1 } }]
    backendRefs: [{ name: api-v1, port: 8080 }]
  - matches: [{ path: { type: PathPrefix, value: /v2 } }]
    backendRefs: [{ name: api-v2, port: 8080 }]
```

#### 4. Canary / weighted A-B

```yaml
rules:
  - matches: [{ path: { type: PathPrefix, value: /v1 } }]
    backendRefs:
      - { name: api-stable, port: 8080, weight: 90 }
      - { name: api-canary, port: 8080, weight: 10 }
```

#### 5. Header-based routing (e.g., feature flag)

```yaml
rules:
  - matches:
      - path: { type: PathPrefix, value: /v1 }
        headers:
          - { name: x-experiment, value: variant-b }
    backendRefs: [{ name: api-variant-b, port: 8080 }]
  - matches: [{ path: { type: PathPrefix, value: /v1 } }]
    backendRefs: [{ name: api-stable, port: 8080 }]
```

#### 6. Method-based split (read vs write)

```yaml
rules:
  - matches: [{ path: { type: PathPrefix, value: /api }, method: GET }]
    backendRefs: [{ name: api-readonly-cache, port: 8080 }]
  - matches: [{ path: { type: PathPrefix, value: /api }, method: POST }]
    backendRefs: [{ name: api-write, port: 8080 }]
```

#### 7. Path types reference

| `type` | Matches |
|---|---|
| `PathPrefix` | `/api` matches `/api`, `/api/v1`, `/api/x/y` |
| `Exact` | `/api` matches only `/api` |
| `RegularExpression` | `^/v[12]/.*` (Envoy RE2 syntax) |

#### 8. Combine everything

One `matches` entry can require host, path, headers, method and query parameters **all at once**. Conditions inside one entry are ANDed; separate entries are ORed.

### Cross-namespace routing

If the route and the Service it sends to are in different namespaces, the Service's namespace must allow it once with a `ReferenceGrant`:

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-from-gateway
  namespace: my-app-ns            # where the Service is
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: envoy-gateway-system   # where the route is
  to:
    - group: ""
      kind: Service
```

Keeping the route in the same namespace as its Service avoids this.

### Rate limiting per route

Apply an Envoy Gateway `BackendTrafficPolicy` in the route's namespace:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: tenant-a-ratelimit
  namespace: tenant-a
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: tenant-a
  rateLimit:
    type: Local
    local:
      rules:
        - limit: { requests: 100, unit: Minute }
```

---

## Adding a new tenant / app

Give a new app its own identity and limits, without touching the stack code:

1. **Create a Keycloak client.** Sign in to the Keycloak admin console (`https://keycloak.<base_domain_name>`), open the `inference` realm, and add a client (for example `my-app`) with service accounts on. Add an audience mapper so its tokens carry the audience `inference-client`: that is the audience the gateway checks.
2. **Rate-limit it (optional).** Apply a `BackendTrafficPolicy` to the route(s) it uses (see above).
3. **Give it its own rules (optional).** To require a different audience, attach a separate `SecurityPolicy` to that app's own `HTTPRoute`.

The app then fetches tokens with its own `client_id` / `client_secret`: same endpoint, different identity.

> Per-key budgets and quotas come with LiteLLM virtual keys (`auth_type: litellm_vkey`).

---

## When you DO need to touch the stack

The rare cases:

| Need | Where to change |
|---|---|
| **CPU tuning for other Intel® Xeon® generations** | NRI balloon sizing and `kubernetes_cpu_policy` ([NRI CPU Balloons](nri_cpu_balloons.md)) |
| **A non-HTTP inference protocol** (raw gRPC, Triton without a predictor) | A custom route and backend pointing at the service |
| **mTLS-only client auth** | Replace the JWT `SecurityPolicy` with a client-certificate policy, and issue certificates with cert-manager |
| **A new service the installer deploys** | Write a role under `roles/<name>/` and register it in `configs/components.yaml`, or ship it as a solution layer ([Adding Solution Layers](../reference/adding_solutions.md)) |

---

## Troubleshooting integration

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Missing or expired JWT, or wrong issuer or audience | Fetch a new token. Check its `iss` matches the Keycloak URL above and `aud` contains `inference-client`. |
| `404 Not Found` | Wrong hostname for the mode (e.g. `inference.<base>` in `litellm_vkey` mode), or no route for that host | Use the endpoint for your `auth_type` and `routing_mode`; `kubectl get httproute -A` |
| Error that the model does not exist | Model not deployed, or a different name | `./model-manager list`, then `./model-manager deploy <name>` |
| `503` / `upstream connect error` | Model pod not ready | `kubectl get pods -n llm-inference`; check the model container's logs |
| TLS verification failure | Selfsigned certificate not trusted | Pass `--cacert env/<env>/logs/ai-solutions-ca.crt` (or `verify=<that file>`); or use `gateway_tls_mode: custom` with your own certificate |
| HTML error page instead of JSON | The request went to your corporate proxy | Add `--noproxy '*'`, or put the domain in `no_proxy` |
| Connection hangs from outside the cluster | The hostname does not resolve to the gateway IP | Add a DNS record (or `--resolve` for curl) → `${GATEWAY_IP}` |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See where the gateway and auth boundary you just integrated with fit in the full stack | [Architecture & Design Document](../reference/architecture.md) |
| Trace exactly how a request flows through MetalLB, the gateway, and ingress | [Network Architecture](../deploy/networking.md) |
| Deploy, list, or undeploy the model you're pointing your app at | [Deploy a Model](../deploy/deploy_models.md) |
| Dig into the inference repo (`model-manager`, vLLM/OVMS configs) | [Intel® Inference Toolkit on GitHub](https://github.com/intel/inference-toolkit) |

Files behind the auth flow above:
- Envoy Gateway `SecurityPolicy`: `roles/envoy_gateway/templates/security-policy.yaml.j2`
- Keycloak realm and client: `ext/enterprise.ai-inference/roles/keycloak_config/`
