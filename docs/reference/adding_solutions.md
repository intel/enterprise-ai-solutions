# Adding a Solution

A **solution** is an external repository that contributes one layer of components to the
stack. The ai-solutions repo owns `infrastructure`, `platform` and `inference`; anything
above that lives in its own repo and is cloned into `ext/` on demand.

This is the complete reference for wiring one up - the layer manifest, the component
registry, roles, config, flavours, and how to verify it. It is the source of truth for
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
`ssh` shell-outs - use `kubernetes.core.*`; pin collection and Python versions. Variables
are flat (`my_api_replicas`), never nested dicts - config files are merged by Ansible
extra-vars precedence, so a nested dict from one file replaces the other's wholesale rather
than merging.

Roles receive `component_action` (`install` | `teardown` | `validate`) plus `env_dir`,
`env_name` and `kubernetes_kubeconfig`.

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

---

## 7. Running it

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
you which `init` to run. Install resolves **downward** (a target needs what it depends on),
teardown resolves **upward** (a target's dependants go first) and reverses the order.

Ansible flags go after `--`: `install mylayer -- --check -vvv`. Set `ES_LOG_LEVEL=debug` for
verbose output plus `ansible -vvv`, or `trace` to add bash xtrace. Per-run logs land in
`env/<env>/logs/`.

---

## 8. Verify before opening the PR

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

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| `Unknown layer: 'mylayer'` | No `configs/repos/repos.mylayer.yaml`, and the registry doesn't declare the layer either. |
| `Unknown target 'mylayer'` from the resolver | The repo is cloned but its `components.yaml` doesn't declare the layer - check `layers:` isn't empty. |
| `Layer 'mylayer' needs ext/... but it is not cloned` | Run `init mylayer` first; `install` never clones. |
| Your layer/components missing from `show` | `components.yaml` isn't where `deployment_subdir` says, or it's malformed - `show` warns on a bad merge. |
| `'x' is undefined` during preflight | An `enabled:` expression lacks `| default(...)`. |
| Role not found | Component name doesn't match the role directory, or `roles/` isn't under `deployment_subdir`. |
| A component runs too early/late | Its `depends_on` is missing, or it names a component in another layer - put the edge on the layer instead. |
| A component silently never runs | Its `enabled:` rendered to something falsey. `show` and the "Skipped (disabled)" preflight line list what was dropped. |

Resolution and ordering live in
[../../filter_plugins/resolve_components.py](../../filter_plugins/resolve_components.py), called from
[../../playbooks/includes/preflight.yaml](../../playbooks/includes/preflight.yaml).
