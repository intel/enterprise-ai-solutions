# Agentic End-to-End Tests

This suite ports the Enterprise Agent Toolkit TC001-TC021 scenarios to the
unified repository's pytest e2e layout. It uses `es_auto_installer.sh`, the
environment under `env/<name>/`, `model-manager`, and Kubernetes API assertions;
it does not source the legacy `agentic-config.cfg` or drive an interactive menu.

## Run

From the unified repository root, install the e2e dependencies and collect the
cases:

```bash
python3 -m pip install -r src/tests/e2e/agentic/requirements.txt
python3 -m pytest --collect-only -q src/tests/e2e/agentic
```

First merge the Toolkit deployment package to the revision configured in
`configs/repos/repos.agentic.yaml`, then initialize the environment:

```bash
./es_auto_installer.sh init agent-toolkit --env local
```

Set the required credentials in `env/local/config.agent-toolkit.yaml`. The tests use
`global_config.yaml`, `config.agent-toolkit.yaml`, and `models.yaml` from the selected
environment.

```bash
python3 -m pytest -q src/tests/e2e/agentic --agentic-env local
```

TC001 and TC002 perform layer teardown/install and require
`--run-agentic-lifecycle`. Model deploy/undeploy cases require
`--run-agentic-model-operations`. These operations are opt-in.

```bash
python3 -m pytest -q src/tests/e2e/agentic \
  --agentic-env local \
  --run-agentic-lifecycle \
  --run-agentic-model-operations
```

## Case Mapping

| Case | Unified-repo scenario |
| --- | --- |
| TC001 | Tear down the agentic layer with the unified installer |
| TC002 | Initialize/install the agentic layer and check single-node readiness |
| TC003 | Check the unified OpenAI-compatible `/v1/models` endpoint |
| TC004-TC005 | Deploy/undeploy a catalog model with `model-manager` |
| TC006-TC007 | Deploy/undeploy a model selected from `env/<name>/models.yaml` |
| TC008-TC009 | Deploy/remove a Hugging Face model by ID and configured alias |
| TC010 | List catalog models and deployed inference services |
| TC011 | Exercise `model-manager deploy --dry-run` without changing the cluster |
| TC012 | Explicitly skipped: model-manager has no undeploy preview/cancel operation |
| TC013 | Resolve the platform install plan with Ansible check mode |
| TC014 | Check multi-node readiness and validate the agentic layer; needs a multi-node environment |
| TC015-TC018 | Check Redis, pgvector, Agent Sandbox and unified observability |
| TC019 | Verify the existing KubeRay operator, RayCluster and services without installing |
| TC020 | Explicitly skipped: Flowise was removed from the unified Toolkit migration |
| TC021 | Explicitly skipped: unified installer has no worker add/remove operation |

Endpoint and model settings are supplied through `AGENTIC_ENDPOINT_URL`,
`AGENTIC_API_TOKEN`, `AGENTIC_CA_BUNDLE`, `AGENTIC_MODEL_ALIAS`,
`AGENTIC_HF_MODEL_ID`, and `AGENTIC_HF_MODEL_ALIAS`. Set
`AGENTIC_INSECURE_TLS=true` only for environments intentionally using an
untrusted/self-signed certificate. `AGENTIC_INSTALLER_TARGET` overrides the
default layer name (`agent-toolkit`). TC003 verifies both `/v1/models` and a chat
completion.

The Toolkit deployment package and unified layer manifest must be available in
the checked-out branches before a clean `init agent-toolkit` can clone the layer.
TC014 requires a separately provisioned multi-node environment.
TC012 and TC021 remain explicitly skipped because unified model-manager has no
undeploy preview and the installer has no worker add/remove operation. TC013
resolves the unified platform plan; it is not the legacy minimal
Kubernetes-plus-Ingress install scenario. Flowise's public route is opt-in and
requires its initial administrator account to be created before registration
locking is enabled. The migrated suite defaults to the `agent-toolkit` layer
and `config.agent-toolkit.yaml`.