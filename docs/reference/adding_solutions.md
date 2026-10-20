# Adding a Solution

A **solution** is an external repository that contributes one layer of components to the
stack. The ai-solutions repo owns `infrastructure`, `platform` and `inference`; anything
above that lives in its own repo and is cloned into `ext/` on demand.

This is the complete reference for wiring one up - the layer manifest, the component
registry, roles, config, flavours, how to use the platform's gateway, Keycloak and models
from your layer, and how to verify it. It is the source of truth for
these file formats; the YAML files themselves are kept comment-light and point back here.

---

## What you have to create

| # | Where | File | Purpose |
|---|---|---|---|
| 1 | **ai-solutions** | `configs/repos/repos.<layer>.yaml` | which repos the layer needs, and at which rev |
| 2 | your repo | `<subdir>/components.yaml` | declares the layer + its components |
| 3 | your repo | `<subdir>/roles/<component>/` | one role per component |
| 4 | your repo | `<subdir>/config.yaml` | operator-editable defaults, seeded on `init` |
| 5 | your repo | `<subdir>/<config_dir>/<flavour>/config.yaml` | optional presets |
| 6 | your repo | any path | optional `models.yaml` catalog |

`<subdir>` is whatever you set as `deployment_subdir` - `""` when the Ansible tree sits at
the repo root, `"deployment"` when it's nested.

Exactly one change lands in ai-solutions: file 1. Everything else lives in your repo.

---

## 1. The layer manifest (in ai-solutions)

`configs/repos/repos.<layer>.yaml`. **The filename carries the layer name** - `init mylayer`
and `install mylayer` resolve to this file.

```yaml
---
repos:
  # Dependencies first: repos are cloned and configs seeded in file order.
  - layer: "inference"
    url: "https://github.com/<org>/<inference-repo>"
    dest: "enterprise.ai-inference"
    rev: "main"
    deployment_subdir: ""
    config_dir: ""
    default_flavour: ""
    model_catalog: "model_manager/models.yaml"

  - layer: "mylayer"                 # primary layer - matches the filename
    url: "https://github.com/<org>/<my-repo>"
    dest: "my-solution"              # → ext/my-solution/
    rev: "main"
    deployment_subdir: "deployment"
    config_dir: "pipelines"
    default_flavour: "chatqna"
```

| Field | Required | Meaning |
|---|---|---|
| `layer` | yes | Layer this repo provides. Names the seeded `config.<layer>.yaml`. The entry matching the filename is the **primary** layer - the one `--flavour` applies to. |
| `url` | yes | Git remote to clone. |
| `dest` | yes | Directory created under `ext/`. |
| `rev` | no (`main`) | Branch, tag, commit SHA, or a full ref such as `refs/pull/N/head`. A branch is checked out as a branch; anything else detaches. |
| `deployment_subdir` | no (`""`) | Path to the dir holding `roles/`, `components.yaml`, `config.yaml`. |
| `config_dir` | no (`""`) | Relative to `deployment_subdir`; holds one subdir per flavour. Empty → seed from `config.yaml` directly. |
| `default_flavour` | no (`""`) | Flavour used when `--flavour` is omitted. |
| `model_catalog` | no | Path **from the repo root** (not from `deployment_subdir`) to a `models.yaml` seeded into the env. |

**A manifest is self-contained.** List every repo the layer needs, including its
dependencies, each pinned separately. That keeps versions correlated per layer: a layer is
installed from exactly one manifest, so `rev:` there is that repo's version *for that
layer*. Listing the same repo in two manifests at different revs is supported and
deliberate.

**Order matters** - dependencies before the layer that needs them. Clone order, config-seed
order, and config precedence all follow file order.

**A layer participates only in the envs that were inited for it.** `init` records the layer in
`env/<env>/.solutions.yaml`, and that record - not the presence of a clone in `ext/` - decides
which solutions an env includes. `ext/` is per checkout and shared by every env, so a plan that
reaches a layer the record does not list is refused rather than silently narrowed: either
`init <layer> --env <env>`, or move the repo out of `ext/`.

**ai-solutions' own layers need no manifest.** `init platform` works because `platform` is
declared in [../../configs/components.yaml](../../configs/components.yaml) and every role ships in
this repo. A layer is nameable when *either* a manifest or the merged registry declares it.

---

## 2. Repo layout

```
my-solution/
└── deployment/                     ← deployment_subdir
    ├── components.yaml             ← declares the layer + components
    ├── config.yaml                 ← seeded to env/<env>/config.mylayer.yaml
    ├── pipelines/                  ← config_dir (optional)
    │   ├── chatqna/config.yaml
    │   ├── docsum/config.yaml
    │   ├── _shared/                  skipped (leading underscore)
    │   └── examples/                 skipped
    └── roles/
        ├── my_bootstrap/
        └── my_api/
```

The installer appends `ext/<dest>/<deployment_subdir>/roles` to `ANSIBLE_ROLES_PATH` at
runtime, so roles are found by name with no path coupling - moving a role between repos is
just a `mv`.

---

## 3. components.yaml - the layer and its components

Preflight discovers every `ext/**/components.yaml` and merges it into the ai-solutions
registry.

```yaml
---
layers:
  - name: mylayer
    enabled: true
    depends_on: [platform, inference]

components:
  - name: my_bootstrap
    layer: mylayer
    enabled: true
    depends_on: []

  - name: my_api
    layer: mylayer
    enabled: "{{ my_api_enabled | default(true) | bool }}"
    depends_on: [my_bootstrap]
```

### Layer fields

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Declare your layer here, not in ai-solutions. Unique across the merged registry. |
| `depends_on` | yes (may be `[]`) | Layer names. Everything in those layers runs before anything in yours. |
| `enabled` | no | Kept for compatibility; a declared layer is part of the stack. |

### Component fields

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Must equal `roles/<name>/`. Unique across the merged registry. |
| `layer` | yes | Your layer, or an existing one you're extending. |
| `depends_on` | yes (may be `[]`) | Component names **in the same layer only**. |
| `enabled` | no (default `true`) | `true`/`false`, or a Jinja expression rendering to one. |

### Rules that actually bite

**Order comes from `depends_on`, never from file position.** Layers run in dependency order,
then components within each layer. Where nothing forces an order, declaration order is
preserved - treat that as a tiebreaker, not a guarantee. If order matters, declare it.

**Component `depends_on` is same-layer only.** Cross-layer ordering is the layer graph's job.
A cross-layer component dependency is either redundant or silently ineffective:

```yaml
# WRONG - reaching across layers from a component
- name: my_api
  layer: mylayer
  depends_on: [keycloak]        # keycloak is in `platform`

# RIGHT - the layer carries the edge
layers:
  - name: mylayer
    depends_on: [platform]
```

**`enabled:` is rendered at preflight**, before any role's `defaults/main.yaml` is loaded, so
only `global_config.yaml` and the seeded `config.<layer>.yaml` variables exist at that
moment. Always supply `| default(...)` - an undefined variable aborts the whole run - and
end with `| bool`. Rendering yields the *string* `"True"`/`"False"`, which the resolver
coerces; recognised true values are `true`, `yes`, `on`, `1` (case-insensitive), and missing
or `null` means enabled. Anything unrecognised is false.

**Extending someone else's layer** is fine: set `layer:` to theirs and do *not* add a
`layers:` entry for it. Your components merge into that layer and order among its existing
ones by `depends_on`.

**Prefix your component names** with a short solution tag. Names share one namespace with
layers and with every other solution's components.

**A dependency cycle warns and does not fail** - the plan comes out in an arbitrary but
deterministic order. Don't rely on it; fix the cycle.

---

## 4. Roles

One role per component, at `<subdir>/roles/<component-name>/`.

```yaml
# roles/<name>/tasks/main.yaml - the entire dispatcher
---
- name: "<name> | Dispatch action: {{ component_action }}"
  ansible.builtin.include_tasks: "{{ component_action }}.yaml"
```

| File | Required | Contract |
|---|---|---|
| `tasks/install.yaml` | yes | Idempotent - a second run with no config change reports zero `changed`. |
| `tasks/teardown.yaml` | yes | Removes what `install` created; succeeds when nothing is installed. |
| `tasks/validate.yaml` | yes | Read-only; never mutates the cluster. |
| `defaults/main.yaml` | yes | Every variable the role reads, flat-named, with a working default. |
| `meta/main.yaml` | no | Do **not** use `dependencies:` - ordering comes from `components.yaml`. |

Conventions: FQCN always (`kubernetes.core.k8s`, not `k8s`); no raw `kubectl` / `helm` /
`ssh` shell-outs - use `kubernetes.core.*`; pin collection and Python versions. The one
sanctioned shell-out is the `model-manager` CLI (see
[Deploying a model](#deploying-a-model)). Variables
are flat (`my_api_replicas`), never nested dicts - config files are merged by Ansible
extra-vars precedence, so a nested dict from one file replaces the other's wholesale rather
than merging.

Roles receive `component_action` (`install` | `teardown` | `validate`) plus `env_dir`,
`env_name` and `kubernetes_kubeconfig`. `log_dir` (`env/<env>/logs`) is where generated
credentials belong - write them there with mode `0600`.

---

## 5. Config and flavours

`<subdir>/config.yaml` is copied to `env/<env>/config.<layer>.yaml` on `init`. That copy is
what operators edit; the file in your repo is only the seed. `init` never overwrites an
existing env file - it reports `exists, skipping`.

For presets, add a `config_dir` with one subdir per flavour, each holding its own
`config.yaml`. Flavours are auto-discovered from the directory listing; `examples` and any
name starting with `_` or `.` are ignored (use `_shared/` for common material). `--flavour`
applies only to the primary layer; passing it to a layer with no `config_dir`, or to an
ai-solutions layer with no manifest, is an error rather than a silent no-op.

### Variable precedence at install

Later `-e` wins in Ansible. The installer builds them in this order:

1. Base facts - `component_action`, `target`, `env_name`, `env_dir`, `_include_deps`
2. `config.<layer>.yaml` for each layer in the manifest, **in manifest order** - dependencies
   first, so your layer's config wins over the configs of layers you build on
3. `env/<env>/global_config.yaml`
4. `env/<env>/nodes.yaml` (only when it has real content, not just comments)
5. `kubernetes_kubeconfig`

So **`global_config.yaml` overrides whatever your `config.<layer>.yaml` ships** - the
operator's environment-wide settings win over your defaults. Put solution-specific defaults
in your `config.yaml`, and expect anything an operator sets globally to take precedence.

---

## 6. Model catalog (optional)

Set `model_catalog:` to a path from your repo root and `init` seeds it to
`env/<env>/models.yaml` - again only when absent. Only one repo per manifest should declare
one. Everything else your repo ships (image lists, compatibility matrices, version files) is
private to it; ai-solutions reads nothing else.

Seeding is first-writer-wins: when an env already has a `models.yaml` (seeded by another
layer), yours is skipped. A catalog only describes models; to actually deploy one from your
layer, see [Deploying a model](#deploying-a-model).

---

## 7. Using the platform from your layer

The point of a layer is to not rebuild what the platform already runs. These are the
integration points a solution typically needs.

### Publishing a host

Publish through the core `gateway` role instead of writing Gateway listeners yourself; it
handles subdomain and path routing modes, the shared TLS certificate and listener
admission. `publish.yaml` sets `gateway_publish.parent_ref` for your HTTPRoute:

```yaml
- name: "my_ui | Publish the host"
  ansible.builtin.include_role:
    name: gateway
    tasks_from: publish.yaml
  vars:
    gateway_publish_name: my-ui
    gateway_publish_namespace: "{{ my_ui_namespace }}"
    gateway_publish_host: "{{ my_ui_hostname }}"   # e.g. my-ui.{{ base_domain_name }}
    gateway_publish_mode: subdomain                # omit to follow routing_mode; set a
                                                   # gateway_publish_path for path mode

- name: "my_ui | Create HTTPRoute"
  kubernetes.core.k8s:
    definition:
      apiVersion: gateway.networking.k8s.io/v1
      kind: HTTPRoute
      metadata: {name: my-ui, namespace: "{{ my_ui_namespace }}"}
      spec:
        parentRefs: ["{{ gateway_publish.parent_ref }}"]
        hostnames: ["{{ my_ui_hostname }}"]
        rules: [{backendRefs: [{name: my-ui, port: 80}]}]
```

Teardown mirrors it with `tasks_from: unpublish.yaml` and the same variables.

**The gateway is deny-by-default.** A Gateway-wide policy requires a Keycloak JWT on every
route that has no policy of its own. An API your clients call with a token needs nothing
more. A browser UI that runs its own login gets `401` on every request until you exempt its
route:

```yaml
- name: "my_ui | Exempt the route from gateway auth (it has its own login)"
  ansible.builtin.include_role:
    name: gateway
    tasks_from: route_auth_exempt.yaml
  vars:
    gateway_route_name: my-ui
    gateway_route_namespace: "{{ my_ui_namespace }}"
    gateway_route_policy_name: my-ui-no-auth
    # gateway_route_state: absent   # in teardown.yaml
```

Reference: `roles/langfuse` in enterprise-inference (publish, route, exemption, teardown).

### Single sign-on

Keycloak runs in the platform; there is no shared "create my client" role. Give your
solution its own realm (or its own client in an existing realm), created by a component
that runs before the app - the platform keeps the admin credentials in the
`keycloak-admin-secret` Secret in the `keycloak` namespace. Store the generated client
secret in a Kubernetes Secret, reuse it on reruns so credentials do not rotate, and make the
Secret authoritative over Keycloak: a `KeycloakRealmImport` is a no-op on a realm that
already exists. Browsers are sent to Keycloak's public URL; in-cluster token and userinfo
calls go to `http://keycloak-service.keycloak.svc:8080` (plus `/keycloak` in path mode).

References: `roles/keycloak_config` in enterprise-inference (client-credentials client),
`deployment/roles/app_keycloak_config` in enterprise-rag (browser login, users, Grafana SSO).

### Calling a model

In-cluster, the inference layer serves an OpenAI-compatible endpoint that needs no token:

| Model kind | Base URL |
|---|---|
| Chat / completion | `http://ai-gateway.envoy-gateway-system.svc/v1` - routes on the request's `model` field |
| Embedding, reranking | `http://ai-gateway.envoy-gateway-system.svc/llm-inference/<model>/v1` |

`GET /v1/models` on the first URL lists chat models only. Clients outside the cluster go
through the platform gateway with a Keycloak JWT - see
[../customize/integration.md](../customize/integration.md).

### Deploying a model

If your layer needs a model, deploy it through the `model-manager` wrapper, labelled so you
can find what you own. Skip the deploy when a serving resource of that name already exists -
another layer deployed it, and it stays theirs:

```yaml
- name: "my_app | Look up the model"
  kubernetes.core.k8s_info:
    api_version: "{{ item.api_version }}"
    kind: "{{ item.kind }}"
    name: "{{ my_app_model }}"
    namespace: llm-inference
  loop:
    - {api_version: serving.kserve.io/v1alpha2, kind: LLMInferenceService}   # vllm
    - {api_version: serving.kserve.io/v1beta1, kind: InferenceService}       # openvino
  register: _my_model

- name: "my_app | Deploy the model"
  ansible.builtin.command:
    argv: ["{{ playbook_dir }}/../model-manager", deploy, "{{ my_app_model }}",
           --label, "my-solution.example.com/managed-by=my_app", --wait]
  changed_when: true
  environment:
    KUBECONFIG: "{{ lookup('env', 'KUBECONFIG') }}"
    ES_ENV: "{{ env_dir | basename }}"   # the wrapper prompts for an env otherwise, and has no tty
  when: _my_model.results | map(attribute='resources') | flatten | length == 0
```

The model name must exist in the env's catalog (`env/<env>/models.yaml`, read by default).
A layer that needs models the inference catalog does not ship can seed its own catalog
under a different name and pass it as `MM_CONFIG`. In teardown, query both kinds with
`label_selectors` and `model-manager undeploy` only what carries your label - never a
model you reused.

Reference: `deployment/roles/app_inference_models` in enterprise-rag (own catalog, ownership
label, reconcile on config change).

### Namespaces and pods

- Label your namespaces `istio.io/dataplane-mode: ambient` when `istio_enabled`, and apply the
  Pod Security labels when `enforce_pss` - the core roles do both; follow them.
- Charts that ship their own NetworkPolicies can block ambient's HBONE port (15008) and the
  route to the model endpoint. Disable them or allow both.
- Pods that reach the internet (package installs, model downloads) need `http_proxy` /
  `https_proxy` / `no_proxy` from `global_config.yaml`; keep `.svc` and `.cluster.local` in
  `no_proxy` so in-cluster calls skip the proxy.

---

## 8. Running it

```bash
./es_auto_installer.sh init mylayer                     # clone + seed env/local/
./es_auto_installer.sh init mylayer --flavour docsum     # with a preset
./es_auto_installer.sh init mylayer --env prod           # a different environment
./es_auto_installer.sh show                             # merged layers + components
./es_auto_installer.sh install mylayer                  # layer + every layer below it
./es_auto_installer.sh install my_api                   # component + its same-layer deps
./es_auto_installer.sh install my_api --only            # exactly that component
./es_auto_installer.sh validate mylayer                 # read-only checks
./es_auto_installer.sh teardown mylayer                 # layer + everything above it
./es_auto_installer.sh status                           # what is installed
```

There is no `all` target - name the topmost layer you want and its closure follows.

`init` is the only command that clones; `install` treats a missing repo as fatal and tells
you which `init` to run. It clones with plain `git`, so a private or internal repo needs git
credentials on the host first (for example `gh auth setup-git`). Running `init` for your
layer in an env already inited for another layer adds yours alongside it; nothing already
seeded is overwritten. Install resolves **downward** (a target needs what it depends on),
teardown resolves **upward** (a target's dependants go first) and reverses the order.
`install mylayer` re-runs every layer below yours - idempotent, but slow; while iterating on
a running cluster, `install my_api --only` runs just that component.

Ansible flags go after `--`: `install mylayer -- --check -vvv`. Set `ES_LOG_LEVEL=debug` for
verbose output plus `ansible -vvv`, or `trace` to add bash xtrace. Per-run logs land in
`env/<env>/logs/`.

---

## 9. Verify before opening the PR

> [!WARNING]
> A new env has no `kubeconfig.yaml`, and `install` without `--only` treats that as "no
> cluster": it runs Kubespray `cluster.yml` against the env's inventory. On a host that
> already runs a cluster, point the new env at it first - set
> `existing_kubernetes: /path/to/kubeconfig` in `env/dev/global_config.yaml`, or copy the
> kubeconfig to `env/dev/kubeconfig.yaml`.

```bash
./es_auto_installer.sh init mylayer --env dev
./es_auto_installer.sh show                          # your layer + components appear?
./es_auto_installer.sh install mylayer -- --check    # resolves and orders, applies nothing
./es_auto_installer.sh install mylayer
./es_auto_installer.sh validate mylayer
./es_auto_installer.sh install mylayer               # second run: zero changed
./es_auto_installer.sh teardown mylayer
./es_auto_installer.sh teardown mylayer              # second run: still succeeds
```

The `--check` run executes preflight, so it confirms the merge, the resolution and the
execution order before anything touches the cluster. Read the printed plan: it is the exact
order your components will run in.

---

## 10. Troubleshooting

| Symptom | Cause |
|---|---|
| `Unknown layer: 'mylayer'` | No `configs/repos/repos.mylayer.yaml`, and the registry doesn't declare the layer either. |
| `Unknown target 'mylayer'` from the resolver | The repo is cloned but its `components.yaml` doesn't declare the layer - check `layers:` isn't empty. |
| `Layer 'mylayer' needs ext/... but it is not cloned` | Run `init mylayer` first; `install` never clones. |
| Your layer/components missing from `show` | `components.yaml` isn't where `deployment_subdir` says, or it's malformed - `show` warns on a bad merge. |
| `'x' is undefined` during preflight | An `enabled:` expression lacks `| default(...)`. |
| Role not found | Component name doesn't match the role directory, or `roles/` isn't under `deployment_subdir`. |
| A component runs too early/late | Its `depends_on` is missing, or it names a component in another layer - put the edge on the layer instead. |
| `401` on your app's host from the gateway | The route has no policy of its own, so the deny-by-default gateway policy demands a JWT - exempt it (see [Publishing a host](#publishing-a-host)). |
| `init` fails with `could not read Username for 'https://github.com'` | The repo is private or internal and the host has no git credentials. |
| `install` on a new env starts provisioning Kubernetes | The env has no `kubeconfig.yaml` - see the warning in [Verify](#9-verify-before-opening-the-pr). |
| Pods cannot reach the model endpoint | A chart NetworkPolicy blocks it, or `no_proxy` lacks `.svc`. |
| A component silently never runs | Its `enabled:` rendered to something falsey. `show` and the "Skipped (disabled)" preflight line list what was dropped. |

Resolution and ordering live in
[../../filter_plugins/resolve_components.py](../../filter_plugins/resolve_components.py), called from
[../../playbooks/includes/preflight.yaml](../../playbooks/includes/preflight.yaml).
