# Upgrade a Layer

[← Docs Index](../README.md)

There is no `upgrade` command. Running `install <layer>` from a newer release over a layer that is
already deployed **is** the upgrade. Before anything changes, the installer reads what the cluster
records about each layer, compares it with the release you are installing, runs the upgrade checks,
and either proceeds or refuses with the command that fixes the problem.

Upgrade one layer at a time, bottom up — there is no single command that upgrades the whole stack:

```bash
./toolkit_manager.sh install platform  --env prod
./toolkit_manager.sh install inference --env prod
./toolkit_manager.sh install erag      --env prod
```

---

## What the installer decides before it starts

### The run class

Each planned layer is put into one class, printed at the start of every install:

| Class | When | What happens |
|---|---|---|
| `fresh` | no record of the layer in the cluster | plain install |
| `reinstall` | recorded version = this release, last run `complete` | plain re-run, idempotent |
| `resume` | last run `in-progress` or `halted` towards this release's version | re-runs the whole layer to finish it |
| `upgrade` | this release is newer than the recorded version | every check runs; one report is written |
| `downgrade` | this release is older than the recorded version | every check runs; the version check is blocking |
| `conflict` | last run `in-progress` or `halted` towards a **different** version, or a teardown that did not finish | the version check is blocking |
| `unversioned` | the layer declares no version | no version-based check runs |

`infrastructure` and `platform` take their version from the installer's `VERSION` file. Every other
layer takes it from the `version.yaml` beside its `components.yaml`. An install from a release
tarball and from a git checkout behave the same.

### The checks

| Check | Runs for | Blocking when |
|---|---|---|
| `cluster-probe` | every class, when the kubeconfig exists | the cluster did not answer the read of the deployment records (every layer would otherwise read as `fresh`) |
| `version-change` | every class | `downgrade`, `conflict` |
| `upgrade-path` | `upgrade`, `conflict` (a `downgrade` is skipped: `version-change` judges it) | the recorded version is outside the release's `upgrade_base_version`, or an unfinished run's recorded version does not parse |
| `backup-freshness` | `upgrade`, `downgrade` of a solution layer | no Completed backup of the **installed** version, at most `upgrade_backup_max_age_hours` (6) old |

Each check prints one line per layer with its verdict: `pass`, `advisory`, `blocking` or `skipped`
(with the reason).

### Advisory or gating

Whether a `blocking` verdict stops the run depends on the layer, not on the check:

- A layer that declares `supports.upgrade: true` on its `layers` entry (today: Intel® Chatbot
  Toolkit, layer `erag`) is **refused** before anything changes. The line reads
  `blocking (gating)`.
- Every other layer — including `infrastructure`, `platform` and `inference` — only **warns**: the
  line reads `blocking (advisory)` and the install proceeds.

A refusal lists every gating check with its reason and the fix:

```text
Upgrade checks refuse this install; nothing was changed.
  - erag upgrade-path: erag 3.0.0 is outside upgrade_base_version >=3.1.0,<3.2.0. Resolve: install erag from a release whose upgrade_base_version covers 3.0.0, then this one (--env prod)
To run anyway, add --force; the run is then recorded as forced.
```

### `--force` and `--only`

- `install <layer> --force` runs past every refusal. All checks still run; the deployment record and
  the upgrade report then carry `forced: true` and `forcedChecks: [<layer>/<check>, …]`.
- `--only` narrows what is installed, never what is checked: `install erag --only` refuses the same
  way `install erag` does.
- With no Velero deployed there is no backup to find. `-e upgrade_without_velero=true` lets the
  upgrade run without a restore point; the line reads `blocking (gating, bypassed)` and the run is
  recorded as forced, as with `--force`. A cluster that does not answer while the backups are looked
  up (including a `velero` namespace left without the Velero CRDs) stops the run instead, for an
  advisory layer too.
- `-e upgrade_backup_max_age_hours=<n>` changes the freshness window.

---

## What the installer leaves behind

### The deployment record

One ConfigMap per layer, `default/<layer>-deployment-manifest`, key `manifest.yaml`, written when the
layer starts and when it ends:

```yaml
version: 3.1.0                  # installed version (null until a run completes)
layer: erag
targetVersion: null             # set while a run is in progress
status: complete                # in-progress | complete | halted | deleting
startedAt: '2026-10-28T17:46:10Z'
completedAt: '2026-10-28T17:58:02Z'
failedComponent: null           # the component that failed, on halted
haltReason: null                # on halted: component-failed
forced: false
forcedChecks: []
dependencies:                   # versions of the layers below, when this run started
  platform: 1.1.0
  inference: 1.1.0
installedBy:
  installerVersion: 1.1.0
  installerCommit: <sha>        # empty when installed from a release tarball
```

- Only a run that covers the whole layer writes it; `install <component> --only` leaves it alone.
- A layer teardown marks it `deleting`, then deletes it. A teardown that fails leaves `deleting`
  behind, so the next install of that layer is a `conflict` until the teardown is run again.
- A record still written by a solution itself (Intel® Chatbot Toolkit 3.0.0, nested
  `deployment:` block) is read as it is; when the solution's newer release declares
  `supports.upgrade`, the installer replaces it with this shape on its first write.

```bash
kubectl -n default get configmap erag-deployment-manifest -o jsonpath='{.data.manifest\.yaml}'
```

### The upgrade report

Every `upgrade` or `downgrade` run writes one report per layer,
`env/<env>/reports/<layer>-upgrade-report-<UTC>.yaml` (a downgrade report carries the same name):
source and target version, class, outcome (`complete` or `halted`), whether the run was forced, and every
check with its verdict and whether it gated. Other runs print the check lines and write no report.

---

## Interrupted and failed runs

| Situation | Record | What to do |
|---|---|---|
| A component failed | `halted`, `failedComponent: <name>` | fix the cause, run the same `install` again: class `resume` |
| The run was killed | stays `in-progress` towards its target | run the same `install` again: class `resume` |
| You run another release over an unfinished run | `conflict`, refused for a gating layer | finish it first: `install` from the release it was heading to |
| A layer teardown failed | `deleting` | run the teardown again |

`resume` re-runs every component of the layer; it relies on each component's install being
idempotent, not on knowing which ones finished. A resumed run keeps the `forced` state of the run it
finishes: its `forcedChecks` are those of the interrupted run plus any its own `--force` bypassed.

---

## Backup and restore

Backup and restore are not installer actions: `run_backup.sh` runs them, for one layer at a time,
never a component. They need Velero (`velero_enabled: true`, see
[Configuration](../customize/configuration.md)) and the layer's backup profile, `backup_profile.yaml`
beside its `components.yaml` (Intel® Chatbot Toolkit: `deployment/backup_profile.yaml`). Core's
own layers have none.

```bash
./run_backup.sh backup  erag --env prod
./run_backup.sh list    erag --env prod      # one line per backup; marks the one restore picks
./run_backup.sh restore erag --env prod      # asks first; --force where there is no terminal
./run_backup.sh restore erag --env prod --from <backup-name>
```

`run_backup.sh` passes the env's configs and kubeconfig the way `toolkit_manager.sh` does, logs to
`env/<env>/logs/backup-<layer>-<timestamp>.log`, and passes anything after `--` to ansible-playbook.
It runs `playbooks/backup.yaml`, which can also be called directly:

```bash
.venv/bin/ansible-playbook -i env/prod/inventory/hosts.yaml \
  -e env_dir=$(pwd)/env/prod -e @env/prod/global_config.yaml \
  playbooks/backup.yaml -e target=erag -e backup_action=backup|restore|list \
  [-e backup_confirm=yes] [-e velero_restore_from=<backup-name>] [-e backup_force=true]
```

A direct call loads only the files it is given: add `-e @env/prod/config.<layer>.yaml` for each
solution layer the env holds, as `run_backup.sh` does. `restore` refuses without `backup_confirm=yes`.

- **Back up before you move the checkout.** The profile is read from the checkout, so a backup is
  refused when the checkout's version differs from the installed one. Take the backup first, then
  `init <layer> --upgrade`. `backup --force` takes it anyway, labelled as forced; such a backup
  never satisfies `backup-freshness`, and `restore` uses it only when named with `--from`.
- The backup is labelled with the installed version; `backup-freshness` and `restore` look for that
  label.
- `restore` takes the newest Completed backup labelled with the layer's **recorded** version, skipping
  forced backups. With no deployment record, or no such backup of that version, it refuses and lists
  the backups it found; when only forced backups match, it names them with the command that restores
  one. `--from <backup-name>` names any backup explicitly, forced or not. `list` marks the backup
  `restore` would pick.
- `restore` first **deletes** the Helm releases and PersistentVolumeClaims the profile lists, then
  replays the backup with Velero's `existingResourcePolicy: update` (`backup_existing_resource_policy`).
  Objects labelled as belonging to another layer (`toolkit-manager.intel.com/layer`) are left out, so other
  layers' records are not touched. **Unlabelled objects in the profile's namespaces are replaced** by
  their backed-up copy, including objects you created there yourself; a profile can leave known kinds
  out with `exclude_resources`.
- `restore` does not run `validate`; run `./toolkit_manager.sh validate <layer>` afterwards.
- **Shared Keycloak (Intel® Chatbot Toolkit).** Its backup covers the `keycloak` and `postgresql`
  namespaces, which the platform layer shares: the platform realm is kept in the same Keycloak
  database. A restore puts that database back as it was when the backup was taken, so restoring a
  backup taken before the platform realm was imported removes that realm. After such a restore, run
  `./toolkit_manager.sh install platform --env <env>`. If it fails with a 404 on `realms/platform`,
  delete `KeycloakRealmImport platform-realm`, Secret `keycloak-platform-realm` and ConfigMap
  `platform-realm-imported` (namespace `keycloak`), then run it again.

---

## Rolling back a failed upgrade

There is no automatic rollback. The procedure has three steps, in this order:

1. **Clean (mandatory):** `./toolkit_manager.sh teardown <layer> --env <env> --force`. This also
   deletes the layer's deployment record. Do not restore over the failed
   upgrade: the backup does not hold everything the newer release created.
2. **Reinstall the previous release:** move the checkout back to it (`repos.<layer>.yaml` pin, then
   `init <layer> --upgrade`) and run `install <layer>`. With no record left, this is a `fresh` run,
   and it records the previous version again.
3. **Restore the data:** run the restore above. It picks the newest backup of the version step 2
   recorded, which is the pre-upgrade one; name another with `--from <backup-name>`.

Not reversed: cluster-scoped objects, anything outside the profile's namespaces, model weights (pulled
again), and other databases sharing a restored volume. Check every layer's pods afterwards: after a
PostgreSQL volume is replayed, a service holding a database connection (for example Keycloak) may need
its pod restarted.

---

## Worked example: Intel® Chatbot Toolkit 3.0.0 → 3.1.0

Starting point: platform and inference at 1.1.0, `erag` 3.0.0 installed with documents, a chat history
and users; its 3.0.0 record is in the solution's own shape.

```bash
# 1. Back up with the installed (3.0.0) checkout
./run_backup.sh backup erag --env prod

# 2. Move the checkout to the new release
./toolkit_manager.sh init erag --env prod --upgrade

# 3. Upgrade
./toolkit_manager.sh install erag --env prod
```

Step 3 prints, before anything changes:

```text
infrastructure: reinstall (1.1.0 -> 1.1.0)
  version-change: pass — reinstall
platform: reinstall (1.1.0 -> 1.1.0)
  version-change: pass — reinstall
inference: fresh (none -> 1.1.0)
  version-change: pass — fresh
erag: upgrade (3.0.0 -> 3.1.0)
  version-change: pass — upgrade
  upgrade-path: pass — 3.0.0 is in >=3.0.0,<3.1.0
  backup-freshness: pass — backup backup-erag-20261028t183459, completed 2026-10-28T17:36:43Z
```

(`inference` is `fresh` because its earlier release recorded no version.) The run ends with
`erag-deployment-manifest` at `version: 3.1.0, status: complete` in the shape above and one report,
`env/prod/reports/erag-upgrade-report-<UTC>.yaml`.

What refuses an `erag` upgrade, before any change and with the records left as they were. Each case
was shown with a release made to trip it, so the versions and ranges in these lines are that release's,
not 3.1.0's (whose `version.yaml` passes, as above):

| Cause | Release installed | Line |
|---|---|---|
| a release skipped | 3.2.0, upgrading from `>=3.1.0,<3.2.0`, over 3.0.0 | `upgrade-path: blocking (gating) — erag 3.0.0 is outside upgrade_base_version >=3.1.0,<3.2.0` |
| no backup, or too old | 3.1.0 | `backup-freshness: blocking (gating) — no Completed backup of erag 3.0.0 in the last 6 h` |
| older release | 3.0.9 over 3.1.0 | `version-change: blocking (gating) — downgrade 3.1.0 -> 3.0.9` |
| unfinished run towards another version | 3.1.0, while a run towards 3.1.1 was interrupted | `version-change: blocking (gating) — a run towards 3.1.1 is in-progress; this release is 3.1.0` |

An `inference` upgrade from 1.1.0 to 1.2.0 runs with advisory lines only, since `inference` does not declare `supports.upgrade`; it still writes its report.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See every action and flag | [CLI Reference](../customize/cli.md) |
| Make your own layer upgradeable | [Adding Solution Layers](../reference/adding_solutions.md) |
| Turn on Velero | [Configuration Reference](../customize/configuration.md) |
