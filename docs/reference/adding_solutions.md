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
| 7 | your repo | `<subdir>/version.yaml` | optional; the layer's version, upgrade path and `requires` ([Upgrades](#upgrades-optional)) |
| 8 | your repo | `<subdir>/config_tracking.yaml` | optional; configuration tracking for upgrades |

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
    ├── version.yaml                ← optional, the layer's version (see Upgrades)
    ├── upgrade_checks/             ← optional, the layer's own pre-run checks (see Upgrades)
    ├── config_tracking.yaml        ← optional, configuration compared on an upgrade
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

`config_tracking.yaml` lists the files under `env/<env>/` the layer owns and the changes an
operator must know about before an upgrade applies them:

```yaml
files:
  - config.mylayer.yaml
rules:
  - id: VECTOR_DIMS_CHANGED           # unique; shown in the check's verdict and the report
    key: vector_dims                  # <path> or <file>:<path>, e.g. models[role=embedding].name
    verdict: blocking                 # advisory (default) | blocking
    when: changed                     # changed (default) | set | cleared
    message: "Vector dimension changed from {old} to {new}"
    sets:                             # optional facts for the layer's roles when the rule fires;
                                      # a fact name is lower case, does not start with _, is not
                                      # a variable the run already has (env_dir, a config key),
                                      # and one rule of the layer sets it; two layers setting it
                                      # fail a run in which both rules fire; the value is a
                                      # quoted string, so a when: reads it with | bool; a fact
                                      # is set only when its rule fires, so give it a role default
      my_reindex_required: "true"
```

Every successful install of the whole layer saves the listed files to
`Secret/default/<layer>-config-tracking`; keys whose name contains `password`, `passwd`,
`secret`, `token` or `credential`, ends in `key` (`api_key`, `apiKey`), or is `pwd` or `auth`, in
any case, are left out, together with everything under them. That also leaves out keys such as
`max_tokens` or `primary_key`, so a rule whose key names such a key is refused when the file is
loaded; name tracked keys clear of these patterns. The match is on key name only, so a secret held under a non-matching key name
(for example a list of name/value pairs) is still saved, in the Secret; keep secrets under a key
name that matches. An install that upgrades,
downgrades or resumes the layer compares the files with that state first: no saved state is
`baseline`, no rule fired is `pass`, otherwise the strongest verdict of the fired rules. A
`blocking` verdict refuses an upgrade or downgrade when the layer declares `supports.upgrade`,
unless `--force` is given; on a resume it only warns, so the resume still sets the fired rules'
facts.
A key without a file is read from the first listed file that holds it; a `[field=value]`
segment picks one entry of a list. Renamed keys are not detected. A layer teardown deletes the
saved state; a layer without the file is reported `skipped`.

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
| `supports.upgrade` | no (`false`) | `true` makes the upgrade checks refuse a blocking verdict for this layer instead of warning. See [Upgrades](#upgrades-optional). |

### Component fields

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Must equal `roles/<name>/`. Unique across the merged registry. |
| `layer` | yes | Your layer, or an existing one you're extending. |
| `depends_on` | yes (may be `[]`) | Component names **in the same layer only**. |
| `enabled` | no (default `true`) | `true`/`false`, or a Jinja expression rendering to one. |
| `used_by` | no | Component names, any layer, for a component that exists only to serve them. It is installed only while one of them is enabled, and tearing them down takes it along, unless one outside that teardown is still enabled (preflight prints `Kept: …`). Names not in the registry are ignored. |

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

One role per component, at `<subdir>/roles/<component-name>/`:

```text
roles/<name>/
├── defaults/main.yaml           public settings only; no platform logic
├── vars/                        optional, both files or neither
│   ├── kubernetes.yaml          private _<name>_* literal values
│   └── openshift.yaml
├── templates/                   no cluster_distribution
└── tasks/
    ├── main.yaml                the dispatcher below, the same in every role but for the name
    ├── install.yaml             required; the same on both platforms
    ├── validate.yaml            required
    ├── teardown.yaml            required
    └── <helpers>.yaml           called from the files above
```

```yaml
# roles/<name>/tasks/main.yaml - the entire dispatcher
---
- name: "<name> | dispatch | Assert supported action"
  ansible.builtin.assert:
    that: component_action | default('') in ['install', 'teardown', 'validate']
    fail_msg: "<name>: unsupported component_action='{{ component_action | default('') }}'"
    quiet: true

# Only in a role with vars/kubernetes.yaml and vars/openshift.yaml:
- name: "<name> | dispatch | Load {{ cluster_distribution }} values"
  ansible.builtin.include_vars: "{{ role_path }}/vars/{{ cluster_distribution }}.yaml"
  when: (role_path ~ '/vars/' ~ cluster_distribution ~ '.yaml') is file

- name: "<name> | dispatch | Include {{ component_action }}"
  ansible.builtin.include_tasks:
    file: "{{ component_action }}.yaml"
```

| File | Required | Contract |
|---|---|---|
| `tasks/install.yaml` | yes | Idempotent - a second run with no config change reports zero `changed`. |
| `tasks/teardown.yaml` | yes | Removes what `install` created; succeeds when nothing is installed. |
| `tasks/validate.yaml` | yes | Read-only; never mutates the cluster. |
| `vars/kubernetes.yaml`, `vars/openshift.yaml` | no | Both or neither, with the same keys. Literal values, named `_<name>_*`: `include_vars` keeps them for the rest of the play. A role without them has no `Load` step in its dispatcher. |
| `defaults/main.yaml` | yes | Every variable the role reads, flat-named, with a working default. |
| `meta/main.yaml` | no | Do **not** use `dependencies:` - ordering comes from `components.yaml`. |

**Platform differences.** `cluster_distribution` (`kubernetes` | `openshift`) is detected once,
in preflight, and only the dispatcher and `components.yaml` read it. The tasks are the same on
both platforms: whatever differs is a value in `vars/<platform>.yaml`. A role that uses the
cluster's operator on OpenShift instead of installing one (cert-manager, CNPG, Keycloak, NFD) has a
boolean `_<name>_from_olm` there: `true` checks the marketplace operator and skips its install,
and teardown leaves it and its CRDs. Where the two stacks differ throughout (`observability`:
Helm charts, or instances of the OperatorHub operators) the shared steps stay in `install.yaml`
and the flag picks a helper, `install_helm.yaml` or `install_olm.yaml`. A component not used on OpenShift (`istio`, `metallb`,
`velero`) is gated off in `components.yaml` and its role has no OpenShift steps. What every role needs
is published once: `gateway` `publish.yaml` creates the OpenShift Routes.
Library roles, entered with `tasks_from`, have no dispatcher: their caller passes
`vars_from: "{{ cluster_distribution }}.yaml"`. The rack-scale `fabric_*` roles are not moved
to this layout yet.

**Infrastructure does not depend on the platform.** The infrastructure layer (`kubernetes`,
`storage` and its backends `custom_storage`, `nfs_storage`, `rook_ceph_storage`,
`netapp_trident_csi`) has no `vars/<platform>.yaml`, and `components.yaml` does not gate it on
`cluster_distribution`. A BYO cluster (`existing_kubernetes`) decides what it does: `kubernetes`
skips provisioning. OpenShift is always BYO: preflight stops on OpenShift without
`existing_kubernetes`. `storage/tasks/resolve.yaml` maps the storage selector on OpenShift to the
cluster's default StorageClass (`custom`): the CSI driver behind it comes from the marketplace.

Conventions: FQCN always (`kubernetes.core.k8s`, not `k8s`); no raw `kubectl` / `helm` /
`ssh` shell-outs - use `kubernetes.core.*`; pin collection and Python versions. The one
sanctioned shell-out is the `model-manager` CLI (see
[Using the platform](#7-using-the-platform-from-your-layer)). Variables
are flat (`my_api_replicas`), never nested dicts - config files are merged by Ansible
extra-vars precedence, so a nested dict from one file replaces the other's wholesale rather
than merging.

Roles receive `component_action` (`install` | `teardown` | `validate`) plus `env_dir`,
`env_name` and `kubernetes_kubeconfig`. Generated credentials belong in `log_dir`
(`env/<env>/logs`).

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
layer), yours is skipped. To deploy a model from your layer, see
[Using the platform](#7-using-the-platform-from-your-layer).

---

## 7. Using the platform from your layer

Reuse what the platform runs instead of rebuilding it. The points that bite:

- **Publish hosts through the core `gateway` role** (`publish.yaml`, `unpublish.yaml`), not your
  own listeners. The gateway is **deny-by-default**: a route without its own policy needs a
  Keycloak JWT, so a UI with its own login gets `401` until you exempt its route with
  `route_auth_exempt.yaml`. Example: `roles/langfuse` in enterprise-inference.
- **Call models in-cluster** at `http://ai-gateway.envoy-gateway-system.svc/v1` (OpenAI-compatible,
  no token).
- **Deploy a model you need** with `{{ playbook_dir }}/../model-manager deploy <model> --label
  <owner>`, setting `ES_ENV: "{{ env_dir | basename }}"` (the wrapper otherwise prompts, and there
  is no tty). Skip it when the model is already served, and in teardown undeploy only what
  carries your label. Example: `deployment/roles/app_inference_models` in enterprise-rag.

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

### Upgrades (optional)

Installing a newer release of your layer over a deployed one upgrades it. What your layer provides
for it:

**`version.yaml`**, beside `components.yaml`. Without it the layer is `unversioned` and no
version-based check runs.

```yaml
schema: 1
layer: mylayer
layer_version: 1.2.0                     # or solution_version; core reads either
upgrade_base_version: ">=1.1.0,<1.2.0"   # versions this release upgrades from
requires:                                # what this release needs underneath, with an upper bound
  platform: ">=1.1.0,<1.2.0"
  inference: ">=1.1.0,<1.2.0"
  installer: ">=1.1.0,<2.0.0"
```

- A recorded version outside `upgrade_base_version` (or the literal `unsupported`) makes
  `upgrade-path` blocking; without the key the check is `skipped`. A bare version means that
  version or later.
- `requires` names layers (and `installer`, the installer's `VERSION`). A layer below the range, or
  one in `requires` or `depends_on` with no version, is blocking; above the range is advisory. Your
  range is also checked when a layer underneath is installed later, advisory there.
- `infrastructure` and `platform` are versioned by the installer's `VERSION`.

**`supports.upgrade: true`** on your `layers` entry makes every blocking verdict refuse an `upgrade`
or `downgrade` of your layer before anything changes (`--force` overrides, recorded as forced). Leave
it out and the checks only warn. Such a layer needs a `version.yaml`, a version on every layer in its
`depends_on` and a `config_tracking.yaml`: without the file `config-tracking` refuses each upgrade
(`--force` overrides, recorded as forced). On a layer without `supports.upgrade` the missing file
makes that check `skipped`.

**`config_tracking.yaml`**: see [Repo layout](#2-repo-layout).

**`upgrade_checks/*.yaml`**, beside `components.yaml`: your layer's own pre-run checks, Ansible task
files run before anything changes, after the core checks, one include per file in name order, on every
install that plans your layer. Each file sees `upgrade_check_layer` (your layer), `_layer_class`,
`_layer_release`, `_layer_records`, `_layer_dirs` and your layer's configuration, and appends one
verdict per check to `_upgrade_verdicts` (here `_issues` is a list of problems earlier tasks in
the file collected):

```yaml
- name: "upgrade_checks | mylayer configuration"
  ansible.builtin.set_fact:
    _upgrade_verdicts: >-
      {{ _upgrade_verdicts + [{'layer': upgrade_check_layer, 'check': 'configuration',
                               'verdict': 'blocking' if _issues else 'pass',
                               'reason': _issues | join('; ') or 'no issues',
                               'resolve': 'fix the settings above in config.mylayer.yaml' if _issues else none}] }}
```

With `supports.upgrade: true`, a `blocking` verdict from these files refuses **every** run class,
`fresh` included; `--force` overrides it, recorded as forced. Without it the verdict only warns. Keep
the files free of cluster and network access: they run before the cluster is touched.

Migrations of stored data belong in your charts; the installer never migrates data.

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
| `401` on your app's host from the gateway | The gateway is deny-by-default - exempt the route (see [Using the platform](#7-using-the-platform-from-your-layer)). |
| `init` fails with `could not read Username for 'https://github.com'` | The repo is private or internal and the host has no git credentials. |
| `install` on a new env starts provisioning Kubernetes | The env has no `kubeconfig.yaml` - see the warning in [Verify](#9-verify-before-opening-the-pr). |
| A component silently never runs | Its `enabled:` rendered to something falsey. `show` and the "Skipped (disabled)" preflight line list what was dropped. |

Resolution and ordering live in
[../../filter_plugins/resolve_components.py](../../filter_plugins/resolve_components.py), called from
[../../playbooks/includes/preflight.yaml](../../playbooks/includes/preflight.yaml).
