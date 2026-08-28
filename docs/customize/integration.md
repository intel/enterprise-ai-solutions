# Integrating Applications with Intel® AI for Enterprise Solutions

[← Docs Index](../README.md)

How to integrate **your tool, framework, or application** with the Intel® AI for Enterprise Solutions inference stack — without modifying the stack itself.

> **TL;DR** — point any OpenAI-compatible client at the inference endpoint and pass a Bearer token. With `auth_provider=litellm`: use `https://${LITELLM_DOMAIN}/v1/chat/completions` with a LiteLLM virtual key. With `auth_provider=keycloak`: use `https://${GATEWAY_DOMAIN}/llm-inference/<model>/v1/chat/completions` with a Keycloak JWT.

---

## Why integration is easy

The stack is built on open standards. Anything that speaks them works:

| Contract | Provided by | What you get |
|---|---|---|
| **OpenAI REST API** (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) | vLLM behind KServe + LiteLLM proxy (or Envoy AI Gateway) | Drop-in compatibility with the entire OpenAI ecosystem |
| **Open Inference Protocol** / KFServing v2 (`/v2/models/<m>/infer`) | KServe predictors (sklearn, XGBoost, PyTorch, TF, ONNX, Triton, custom) | Serve **traditional ML** models on the same stack |
| **Bearer token auth** | LiteLLM virtual keys (`auth_provider=litellm`) or Keycloak OIDC JWT (`auth_provider=keycloak`) | Standard `Authorization: Bearer <token>` works with any HTTP client |
| **Gateway API + Kubernetes CRDs** | Envoy Gateway, KServe, cert-manager, Istio ambient | Add models / routes / policies declaratively — no stack edits |

---

## Quick start: get a token and call a model

### 1. Get the gateway address and a token

> The exact discovery commands below have been validated end-to-end on this stack.

```bash
# Gateway LoadBalancer IP (provisioned by MetalLB)
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
```

#### With `auth_provider=litellm`

```bash
# LiteLLM hostname
LITELLM_DOMAIN="litellm.$(grep base_domain_name env/local/global_config.yaml | awk '{print $2}' | tr -d '\"')"

# Get the master key (or use a virtual key created via LiteLLM UI/API)
TOKEN=$(kubectl get secret litellm-master-key -n litellm \
  -o jsonpath='{.data.master_key}' | base64 -d)
```

#### With `auth_provider=keycloak` (default)

```bash
# Keycloak hostname (from the Keycloak CR)
KEYCLOAK_DOMAIN=$(kubectl get keycloaks -n keycloak \
  -o jsonpath='{.items[0].spec.hostname.hostname}')

# Inference gateway hostname: keycloak.<base> → inference.<base>
GATEWAY_DOMAIN="inference.${KEYCLOAK_DOMAIN#keycloak.}"

# OIDC client credentials (created by the keycloak_config role)
REALM="inference"
CLIENT_ID=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-id}' | base64 -d)
CLIENT_SECRET=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-secret}' | base64 -d)

# Get a JWT (client-credentials flow — service-to-service)
TOKEN=$(curl -sk --noproxy '*' --resolve ${KEYCLOAK_DOMAIN}:443:${GATEWAY_IP} \
  https://${KEYCLOAK_DOMAIN}/realms/${REALM}/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
```

> **Behind a corporate proxy?** `--noproxy '*'` bypasses `$http_proxy` / `$https_proxy` for cluster-internal traffic. Without it, curl routes the request through the proxy and you get the proxy's HTML error page back.

### 2. Call the model

#### With `auth_provider=litellm`

```bash
curl -sk --noproxy '*' --resolve ${LITELLM_DOMAIN}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  https://${LITELLM_DOMAIN}/v1/chat/completions \
  -d '{
    "model": "llama-3-2-1b",
    "messages": [{"role":"user","content":"Hello!"}],
    "max_tokens": 50
  }'
```

LiteLLM handles model routing — all models share the same base URL (`/v1/...`). Specify the model name in the request body.

#### With `auth_provider=keycloak` (default)

```bash
curl -sk --noproxy '*' --resolve ${GATEWAY_DOMAIN}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  https://${GATEWAY_DOMAIN}/llm-inference/llama-3-2-1b/v1/chat/completions \
  -d '{
    "model": "llama-3-2-1b",
    "messages": [{"role":"user","content":"Hello!"}],
    "max_tokens": 50
  }'
```

URL pattern: `https://<gateway-domain>/<namespace>/<model-name>/v1/...` where `<namespace>` defaults to `llm-inference`.

That same endpoint + token is what every framework below uses.

---

## Python: OpenAI SDK

```python
import os
from openai import OpenAI

# auth_provider=litellm:
client = OpenAI(
    base_url=f"https://{os.environ['LITELLM_DOMAIN']}/v1",
    api_key=os.environ["TOKEN"],   # LiteLLM virtual key or master key
    http_client=__import__("httpx").Client(verify=False),  # for self-signed certs
)

# auth_provider=keycloak:
# client = OpenAI(
#     base_url=f"https://{os.environ['GATEWAY_DOMAIN']}/llm-inference/llama-3-2-1b/v1",
#     api_key=os.environ["TOKEN"],   # Keycloak JWT
#     http_client=__import__("httpx").Client(verify=False),
# )

resp = client.chat.completions.create(
    model="llama-3-2-1b",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

> Streaming, function-calling, and embeddings work via the standard OpenAI SDK calls — vLLM implements them. Note: TLS verification may need `verify=False` (or trusting the cert-manager CA) when using the default `selfsigned` TLS mode.

---

## Integration recipes

Pick your tool. Each one needs only **base URL + API key** — both already provided above.

> **Note on base URL:** All examples below use `BASE_URL` which is:
> - With `auth_provider=litellm`: `https://${LITELLM_DOMAIN}/v1` (LiteLLM routes to the model)
> - With `auth_provider=keycloak`: `https://${GATEWAY_DOMAIN}/llm-inference/<model>/v1` (direct KServe path)

### RAG — LangChain

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ['BASE_URL'],
    api_key=os.environ['TOKEN'],
    model="llama-3-2-1b",
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
    model="llama-3-2-1b",
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
    model="llama-3-2-1b",
)
agent = create_react_agent(llm, tools=[...])
```

### Agents — CrewAI

```python
import os
from crewai import LLM, Agent

llm = LLM(
    model="openai/llama-3-2-1b",
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
    model=OpenAIChatCompletionsModel("llama-3-2-1b", openai_client=client),
)
```

### MCP (Model Context Protocol)

Any MCP host (Claude Desktop, Cursor, Continue, etc.) that lets you set a custom OpenAI-compatible endpoint can target the gateway. Configure:

```
Base URL: https://litellm.inference-example.com/v1  (litellm mode)
      or: https://inference.inference-example.com/llm-inference/<model>/v1  (keycloak mode)
API Key:  <LiteLLM virtual key or Keycloak JWT>
```

### Vector DBs (RAG storage)

These run **alongside** the stack — your app talks to them directly. Recommended deployments:

| Vector DB | Notes |
|---|---|
| **pgvector** | Reuse the in-cluster PostgreSQL (CNPG). Create a DB, enable extension, done. |
| **Milvus** | `helm install milvus milvus/milvus -n milvus --create-namespace` |
| **Qdrant** | `helm install qdrant qdrant/qdrant -n qdrant --create-namespace` |
| **Weaviate** | `helm install weaviate weaviate/weaviate -n weaviate --create-namespace` |
| **Chroma** | `helm install chromadb chromadb/chromadb` |

The stack does **not** need to know about them — they're consumed by your application code.

### Eval & Observability

| Tool | How it integrates |
|---|---|
| **Langfuse** | ✅ Bundled with the stack when `auth_provider=litellm`. LiteLLM auto-sends traces to the in-cluster Langfuse instance — no client-side instrumentation needed. Access at `https://langfuse.<base_domain_name>`. |
| **Phoenix / Arize** | Use the OpenInference instrumentor for OpenAI; spans are emitted to your Phoenix collector. |
| **Ragas / DeepEval** | Pass the same `ChatOpenAI` / `OpenAILike` LLM into the eval harness. |
| **Promptfoo** | Use provider `openai:chat:llama-3-2-1b` with `apiBaseUrl` + `apiKey` in `promptfoo.yaml`. |
| **OpenLLMetry** | Drop-in OpenTelemetry SDK; export to your OTEL collector. |

### Chat UIs

| UI | Setup |
|---|---|
| **Open WebUI** | Settings → Connections → OpenAI API → set `base_url` + `api_key` |
| **LibreChat** | `librechat.yaml`: add a custom endpoint with `baseURL` + `apiKey` |
| **AnythingLLM** | LLM Provider = "Generic OpenAI" → `base_url` + `api_key` |
| **Dify** | Model Provider = OpenAI-API-compatible → `base_url` + `api_key` |

### Coding assistants

| Tool | Setup |
|---|---|
| **Continue (VS Code/JetBrains)** | `~/.continue/config.json`: `{"provider":"openai","apiBase":"...","apiKey":"...","model":"llama-3-2-1b"}` |
| **Cline** | API Provider = "OpenAI Compatible" → URL + Key |
| **Aider** | `aider --openai-api-base ... --openai-api-key ... --model openai/llama-3-2-1b` |
| **Cursor** | Settings → Models → Override OpenAI Base URL + API Key |

### Workflow tools

| Tool | How |
|---|---|
| **n8n** | OpenAI node → custom Base URL + Credentials |
| **Temporal / Airflow / Dagster** | An HTTP/Python step that calls the OpenAI SDK with your `BASE` + `TOKEN` |

---

## Authentication options

Pick the model that fits your client:

| Option | Status | When to use | How |
|---|---|---|---|
| **Client-credentials JWT** (default) | ✅ shipped | Service-to-service, batch jobs, agents | `grant_type=client_credentials` against Keycloak token endpoint (see top of guide) |
| **Password JWT** | ✅ supported by Keycloak | User-facing apps with login | `grant_type=password` — issue per-user tokens (enable Direct Access Grants on the client first) |
| **Authorization Code Flow** | ✅ supported by Keycloak | Web apps with full SSO | Standard OIDC redirect flow against Keycloak (configure `redirectUris`) |
| **LiteLLM virtual key** | ✅ shipped | Multi-tenant SaaS, per-key budgets/limits | Set `auth_provider: litellm` in `global_config.yaml`. LiteLLM issues virtual keys with per-key budgets, rate limits, and model access controls. Langfuse observability auto-enabled. |
| **No auth** | ✅ shipped | Local dev only | `auth_provider: "none"` in `global_config.yaml` |

**Gateway auth enforcement:**

- **`auth_provider=litellm`**: Envoy Gateway sends every request to LiteLLM's `/key/verify` as extAuth. LiteLLM validates the virtual key, enforces per-key budgets/rate-limits, and forwards to the model backend. No OIDC configuration needed.
- **`auth_provider=keycloak`**: Envoy Gateway `SecurityPolicy` validates JWTs:
  - **Issuer**: `https://${KEYCLOAK_HOSTNAME}/realms/inference`
  - **Audience**: `inference-client`
  - **JWKS URI**: `http://keycloak-service.keycloak.svc.cluster.local:8080/realms/inference/protocol/openid-connect/certs`
  - Match those claims and any IdP works — the policy can be reconfigured to point at Auth0, Okta, Azure AD, etc.

---

## Adding a new model

You don't have to write Ansible — use the Model Manager:

```bash
./model-manager deploy <model-name>
```

Behind the scenes it creates a KServe `InferenceService` (or `LLMInferenceService`) and KServe auto-creates the `HTTPRoute` so the new model is reachable at `/llm-inference/<model-name>/v1/...` — same gateway, same JWT.

To declare models in Git instead, drop CRDs into your role/values and run:

```bash
./es_auto_installer.sh install inference_services --env <name>
```

---

## Traditional ML models (sklearn, XGBoost, PyTorch, TensorFlow, ONNX, …)

KServe is a **general-purpose model server** — LLMs are just one workload. You can deploy classic ML models on the same stack with the same auth, gateway, and routing.

### What's supported out of the box

KServe ships predictor runtimes for every mainstream ML framework. They all expose the **Open Inference Protocol** (a.k.a. KFServing v2) at `/v2/models/<model>/infer`.

| Framework | Predictor | Model artifact |
|---|---|---|
| **scikit-learn** | `sklearn` | `model.joblib` / `model.pkl` |
| **XGBoost** | `xgboost` | `model.bst` / `model.json` |
| **LightGBM** | `lightgbm` | `model.txt` |
| **PyTorch** | `pytorch` (TorchServe) | `.mar` archive |
| **TensorFlow** | `tensorflow` | SavedModel directory |
| **ONNX** | `onnx` (Triton) | `model.onnx` |
| **Triton** (multi-framework) | `triton` | Triton model repository |
| **HuggingFace transformers** (non-LLM) | `huggingface` | HF repo or local dir |
| **PMML** | `pmml` | `model.pmml` |
| **MLflow** | `mlflow` | MLflow model dir |

### Deploy a traditional ML model

Apply an `InferenceService` CRD pointing at your model artifact (S3, GCS, PVC, HTTP, OCI, or hostPath):

```yaml
# my-sklearn-model.yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: iris-classifier
  namespace: llm-inference         # or any KServe-enabled namespace
spec:
  predictor:
    sklearn:
      storageUri: "pvc://shared-models/iris/"   # or s3://, gs://, http://, oci://
      resources:
        requests:
          cpu: "200m"
          memory: "512Mi"
```

```bash
kubectl apply -f my-sklearn-model.yaml
kubectl get inferenceservice -n llm-inference iris-classifier -w
```

KServe will:
1. Pull the model artifact via its storage initializer.
2. Start the appropriate predictor container (sklearn server, XGBoost server, etc.).
3. Auto-create the `HTTPRoute` on the same Envoy gateway.

### Call a traditional ML model

The endpoint follows the **same pattern** as LLMs but uses the **Open Inference Protocol** path:

```bash
curl -sk --noproxy '*' --resolve ${GATEWAY_DOMAIN}:443:${GATEWAY_IP} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  https://${GATEWAY_DOMAIN}/llm-inference/iris-classifier/v2/models/iris-classifier/infer \
  -d '{
    "inputs": [{
      "name": "input-0",
      "shape": [1, 4],
      "datatype": "FP32",
      "data": [5.1, 3.5, 1.4, 0.2]
    }]
  }'
```

Same gateway, same JWT, same `HTTPRoute` mechanism — only the **path** and **payload format** differ from the OpenAI-style LLM endpoints.

### Python client example

```python
import os, requests

URL = (f"https://{os.environ['GATEWAY_DOMAIN']}"
       "/llm-inference/iris-classifier/v2/models/iris-classifier/infer")

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
    verify=False,   # selfsigned TLS in default install
)
print(resp.json())
```

For richer clients, the [`kserve` Python SDK](https://kserve.github.io/website/) and [Triton client libraries](https://github.com/triton-inference-server/client) speak the same protocol natively.

### Custom model servers (any framework)

If KServe doesn't ship a predictor for your framework — or you have a custom inference container — use a **custom predictor**:

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

As long as the container exposes HTTP on the agreed port, KServe creates the `HTTPRoute` and your model is reachable through the gateway with auth.

### Why this matters

The same stack serves:
- **LLMs** (vLLM / `LLMInferenceService` → OpenAI API)
- **Classical ML** (sklearn/XGBoost/etc. → Open Inference Protocol)
- **Computer vision** (PyTorch/TensorFlow/ONNX/Triton)
- **Custom predictors** (any container)

…with **one auth flow, one gateway, one observability surface, one deployment model**. There is no separate stack for "traditional ML" vs "GenAI".

---

## Routing patterns (host + path)

The stack uses the **Kubernetes Gateway API** (Envoy Gateway implementation), which supports **host routing**, **path routing**, and **header / method / query matching** simultaneously on every `HTTPRoute`.

### Gateway listener (already deployed)

`roles/envoy_gateway/templates/gateway.yaml.j2` provisions a wildcard-host HTTPS listener:

```yaml
listeners:
  - name: https
    protocol: HTTPS
    port: 443
    hostname: "*.<base_domain_name>"   # accepts any subdomain
```

Each `HTTPRoute` then claims the exact hostnames + paths it owns.

### What's running today

| Workload | Hostname | Path |
|---|---|---|
| Keycloak | `keycloak.<base>` | `/` (entire host) |
| LLMs (KServe-managed) | `inference.<base>` | `/llm-inference/<model>/v1/...` (auto-created by KServe) |

So the stack is already exercising **pure host routing** (Keycloak) and **host + path routing combined** (inference).

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

A single rule can require host + path + headers + method + query params **all at once** — match semantics are AND within a `matches` entry, OR between entries.

### Cross-namespace routing

If your service lives in a different namespace from the gateway, add a `ReferenceGrant` once:

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-from-gateway
  namespace: my-app-ns
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: envoy-gateway-system
  to:
    - group: ""
      kind: Service
```

### Rate limiting per route

Apply an Envoy Gateway `BackendTrafficPolicy` targeting any `HTTPRoute`:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: tenant-a-ratelimit
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

Create a Keycloak client + a routing policy without touching the stack code:

1. **New Keycloak client** — log into `https://${KEYCLOAK_DOMAIN}` and add `clientId: my-app` (service-accounts enabled, audience mapper for `my-app`). Or declare it via a `KeycloakRealmImport` CR (see `ext/enterprise.ai-inference/roles/keycloak_config/tasks/install.yaml` for a template).
2. **Per-app rate limit (optional)** — apply an Envoy Gateway `BackendTrafficPolicy` targeting the route(s) the new client should hit.
3. **Per-client SecurityPolicy (optional)** — to require a different audience claim, add a second `SecurityPolicy` targeting only the new client's `HTTPRoute`.

Your app then uses its own `client_id` / `client_secret` to fetch tokens — same endpoint, different identity.

> Per-key budgets and quotas are available via LiteLLM virtual keys when `auth_provider: litellm` is configured.

---

## When you DO need to touch the stack

These are the rare cases:

| Need | Where to change |
|---|---|
| **Alternative Intel® Xeon® optimizations** | Tune NRI balloon sizing, adjust CPU policy, or update `kubernetes_accelerator` for new Xeon generations |
| **Non-OpenAI inference protocol** (raw gRPC, Triton v2 without predictor) | Custom `HTTPRoute` + `Backend` resource pointing to the service |
| **mTLS-only auth** | Replace the JWT `SecurityPolicy` with a client-cert policy and provision certs via cert-manager |
| **Cross-namespace gateway exposure** | Add `ReferenceGrant` from your namespace to `envoy-gateway-system` |
| **Add a new persistent service to the installer** | Write a role under `roles/<name>/`, register it in `playbooks/site.yaml` and `es_auto_installer.sh` |

---

## Troubleshooting integration

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Missing/expired JWT, wrong audience | Re-fetch token; verify `aud` claim matches `inference-client` |
| `403 Forbidden` | Token valid but issuer mismatch | Confirm Keycloak hostname in JWT `iss` matches `SecurityPolicy` |
| `404 Not Found` on `/llm-inference/<model>` | Model not deployed | `kubectl get inferenceservice -A` — deploy via Model Manager |
| `503` / `upstream connect error` | Model pod not ready | `kubectl get pods -n llm-inference` — check vLLM container logs |
| TLS verification failure | Self-signed cert | Use `--insecure` / `verify=False` in dev, or trust the CA from cert-manager |
| Connection hangs from outside cluster | Hostname not resolving to LoadBalancer IP | Add a DNS record (or `--resolve` for curl) → `${GATEWAY_IP}` |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See where the gateway and auth boundary you just integrated with fit in the full stack | [Architecture & Design Document](../reference/architecture.md) |
| Trace exactly how a request flows through MetalLB, the gateway, and ingress | [Network Architecture](../deploy/networking.md) |
| Deploy, list, or undeploy the model you're pointing your app at | [Deploy an LLM](../deploy/deploy_models.md) |
| Dig into the underlying inference-serving repo (`model-manager`, vLLM/OVMS configs) | [Model Manager (Enterprise Inference) on GitHub](https://github.com/intel/enterprise-inference) |

Config file references for the auth flow above:
- Envoy Gateway `SecurityPolicy`: `roles/envoy_gateway/templates/security-policy.yaml.j2`
- Keycloak realm/client config: `ext/enterprise.ai-inference/roles/keycloak_config/`
