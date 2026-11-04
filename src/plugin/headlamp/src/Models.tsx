// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { K8s } from '@kinvolk/headlamp-plugin/lib';
import {
  AuthVisible,
  Link,
  Loader,
  SectionBox,
} from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import { Alert, Box, MenuItem, Typography } from '@mui/material';
import { useState } from 'react';
import { AccessDialog } from './AccessDialog';
import {
  Action,
  buildJob,
  CATALOG,
  catalogAccess,
  CatalogModel,
  catalogModels,
  categories,
  DeployOptions,
  filterRows,
  LatestJob,
  latestJobs,
  liveFacts,
  MANAGED_BY,
  MODEL_MANAGER,
  modelPods,
  parseJobTemplate,
  Row,
  rowActions,
  RowFilter,
} from './data';
import { DeployDialog, UndeployDialog } from './DeployDialog';
import { ModelActionButton, ModelMoreMenu } from './ModelActionMenu';
import { ModelCards } from './ModelCards';
import { ModelToolbar } from './ModelToolbar';
import { InferenceService, LLMInferenceService, readyCondition, ServingObject } from './resources';

function stateOf(svc: ServingObject | undefined, job: LatestJob | undefined) {
  if (job?.state === 'Running')
    return {
      state: job.action === 'deploy' ? 'Deploying' : 'Undeploying',
      reason: 'model-manager is running',
    } as const;
  if (job?.state === 'Failed')
    return { state: 'Failed', reason: `Last ${job.action} failed; see its log` } as const;
  if (!svc) return { state: 'Available', reason: 'In the catalogue, not deployed' } as const;
  const ready = readyCondition(svc);
  return ready?.status === 'True'
    ? ({ state: 'Ready', reason: '' } as const)
    : ({ state: 'Deploying', reason: ready?.reason ?? '' } as const);
}

export function Models() {
  // Scoped to the inference and model-manager namespaces, and watched.
  const [catalog, catalogError] = K8s.ResourceClasses.ConfigMap.useGet(
    CATALOG.name,
    CATALOG.namespace
  );
  // Both kinds model-manager deploys (OpenVINO models are InferenceServices), its own only.
  const scope = { namespace: CATALOG.namespace, labelSelector: MANAGED_BY };
  const [llmServices, servicesError] = LLMInferenceService.useList(scope);
  const [inferenceServices, isvcError] = InferenceService.useList(scope);
  const [templateMap] = K8s.ResourceClasses.ConfigMap.useGet(
    MODEL_MANAGER.template,
    MODEL_MANAGER.namespace
  );
  const [pods] = K8s.ResourceClasses.Pod.useList({ namespace: CATALOG.namespace });
  const [jobs] = K8s.ResourceClasses.Job.useList({
    namespace: MODEL_MANAGER.namespace,
    labelSelector: 'app.kubernetes.io/name=model-manager',
  });
  const [filter, setFilter] = useState<RowFilter>({});
  // Actions are offered only to people allowed to start model-manager Jobs.
  const [canManage, setCanManage] = useState(false);
  const [deploying, setDeploying] = useState<Row | null>(null);
  const [undeploying, setUndeploying] = useState<Row | null>(null);
  const [accessing, setAccessing] = useState<Row | null>(null);
  const [error, setError] = useState('');

  if (
    (!catalog && !catalogError) ||
    (!llmServices && !servicesError) ||
    (!inferenceServices && !isvcError)
  )
    return <Loader title="Loading models" />;

  // An InferenceService list that fails (no KServe v1beta1, no access) means none, not an error.
  const services: ServingObject[] = [...(llmServices ?? []), ...(inferenceServices ?? [])];

  const template = parseJobTemplate(templateMap?.data);
  const jobByModel = latestJobs((jobs ?? []).map(j => j.jsonData));
  const jobObject = new Map((jobs ?? []).map(j => [j.metadata.name, j]));
  const byName = new Map(services.map(s => [s.metadata.name, s]));
  const known = catalogModels(catalog?.data);
  // Absent before the inference release that publishes it: no "Access details" then.
  const access = catalogAccess(catalog?.data);
  const row = (m: CatalogModel, inCatalogue: boolean): Row => {
    const job = jobByModel.get(m.name);
    return {
      ...m,
      ...stateOf(byName.get(m.name), job),
      deployed: byName.has(m.name),
      inCatalogue,
      job,
    };
  };
  const rows: Row[] = [
    ...known.map(m => row(m, true)),
    // Deployed outside the catalogue (model-manager deploy --as / --hfid): shown, not managed here.
    ...[...byName.entries()]
      .filter(([n]) => !known.some(m => m.name === n))
      .map(([n, s]) => row({ name: n, ...liveFacts(s.jsonData) }, false)),
  ];
  const shown = filterRows(rows, filter);
  const empty = catalogError
    ? `Cannot read ${CATALOG.namespace}/${CATALOG.name}: ${catalogError.message}`
    : rows.length
    ? 'No models match the filters.'
    : 'No models in the catalogue.';

  async function start(action: Action, model: string, opts?: DeployOptions) {
    setError('');
    try {
      await K8s.ResourceClasses.Job.apiEndpoint.post(buildJob(template!, action, model, opts));
    } catch (e) {
      setError(`Could not start ${action} of ${model}: ${(e as Error).message}`);
    }
  }

  const actions = (r: Row) => rowActions(r, canManage && !!template);
  const select = (a: Action, r: Row) => (a === 'deploy' ? setDeploying(r) : setUndeploying(r));
  const lastAction = (r: Row) =>
    r.job && jobObject.get(r.job.name) ? (
      <Link kubeObject={jobObject.get(r.job.name)} tooltip="Job and its log">
        {`${r.job.action}: ${r.job.state}`}
      </Link>
    ) : null;

  // Links to Headlamp's own pages: a pod page streams its logs, a Job page shows the run.
  const podList = (pods ?? []).map(p => p.jsonData);
  const moreItems = (r: Row) => {
    const { server, router } = modelPods(podList, r.name);
    const job = r.job && jobObject.get(r.job.name);
    const pod = (name: string, label: string) => (
      <MenuItem key={label}>
        <Link routeName="pod" params={{ namespace: CATALOG.namespace, name }}>
          {label}
        </Link>
      </MenuItem>
    );
    return [
      ...(access && r.deployed
        ? [
            <MenuItem key="access" onClick={() => setAccessing(r)}>
              Access details
            </MenuItem>,
          ]
        : []),
      ...(server ? [pod(server, 'Server logs')] : []),
      ...(router ? [pod(router, 'Router logs')] : []),
      ...(job
        ? [
            <MenuItem key="run">
              <Link kubeObject={job}>{`Last ${r.job!.action} log`}</Link>
            </MenuItem>,
          ]
        : []),
    ];
  };

  return (
    <SectionBox title="AI Models">
      <AuthVisible
        item={K8s.ResourceClasses.Job}
        authVerb="create"
        namespace={MODEL_MANAGER.namespace}
        onAuthResult={r => setCanManage(r.allowed)}
      />
      {error && (
        <Alert severity="error" onClose={() => setError('')} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      <ModelToolbar filter={filter} categories={categories(rows)} onFilter={setFilter} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {shown.length === rows.length
          ? `${rows.length} models`
          : `${shown.length} of ${rows.length} models`}
      </Typography>
      <ModelCards
        rows={shown}
        empty={empty}
        lastAction={lastAction}
        action={r => (
          <>
            <ModelActionButton row={r} actions={actions(r)} onSelect={select} />
            <Box sx={{ flexGrow: 1 }} />
            <ModelMoreMenu name={r.name} items={moreItems(r)} />
          </>
        )}
      />
      {deploying && (
        <DeployDialog
          model={deploying}
          onClose={() => setDeploying(null)}
          onDeploy={opts => {
            setDeploying(null);
            start('deploy', deploying.name, opts);
          }}
        />
      )}
      {accessing && access && (
        <AccessDialog
          model={accessing}
          access={access}
          ready={accessing.state === 'Ready'}
          onClose={() => setAccessing(null)}
        />
      )}
      {undeploying && (
        <UndeployDialog
          model={undeploying}
          onClose={() => setUndeploying(null)}
          onUndeploy={() => {
            setUndeploying(null);
            start('undeploy', undeploying.name);
          }}
        />
      )}
    </SectionBox>
  );
}
