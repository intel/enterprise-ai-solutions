// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { K8s } from '@kinvolk/headlamp-plugin/lib';
import {
  Loader,
  SectionBox,
  SimpleTable,
  StatusLabel,
} from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import { KubeObject, KubeObjectInterface } from '@kinvolk/headlamp-plugin/lib/k8s/cluster';
import { CATALOG, CatalogModel, catalogModels } from './data';

interface Condition {
  type: string;
  status: string;
  reason?: string;
}

interface LLMInferenceServiceSpec extends KubeObjectInterface {
  status?: { conditions?: Condition[] };
}

class LLMInferenceService extends KubeObject<LLMInferenceServiceSpec> {
  static apiVersion = ['serving.kserve.io/v1alpha2', 'serving.kserve.io/v1alpha1'];
  static kind = 'LLMInferenceService';
  static apiName = 'llminferenceservices';
  static isNamespaced = true;
}

type State = 'Ready' | 'Deploying' | 'Available';

interface Row extends CatalogModel {
  state: State;
  reason: string;
}

function stateOf(svc: LLMInferenceService | undefined): Pick<Row, 'state' | 'reason'> {
  if (!svc) return { state: 'Available', reason: 'In the catalogue, not deployed' };
  const ready = svc.jsonData.status?.conditions?.find(c => c.type === 'Ready');
  return ready?.status === 'True'
    ? { state: 'Ready', reason: '' }
    : { state: 'Deploying', reason: ready?.reason ?? '' };
}

const statusColour: Record<State, 'success' | 'warning' | ''> = {
  Ready: 'success',
  Deploying: 'warning',
  Available: '',
};

export function Models() {
  // Both scoped to the inference namespace and watched; nothing cluster-wide.
  const [catalog, catalogError] = K8s.ResourceClasses.ConfigMap.useGet(
    CATALOG.name,
    CATALOG.namespace
  );
  const [services, servicesError] = LLMInferenceService.useList({ namespace: CATALOG.namespace });

  if ((!catalog && !catalogError) || (!services && !servicesError))
    return <Loader title="Loading models" />;

  const byName = new Map((services ?? []).map(s => [s.metadata.name, s]));
  const known = catalogModels(catalog?.data);
  const rows: Row[] = [
    ...known.map(m => ({ ...m, ...stateOf(byName.get(m.name)) })),
    // Deployed outside the catalogue (e.g. model-manager deploy --id).
    ...[...byName.keys()]
      .filter(n => !known.some(m => m.name === n))
      .map(n => ({ name: n, ...stateOf(byName.get(n)) })),
  ];

  return (
    <SectionBox title="Models">
      <SimpleTable
        data={rows}
        emptyMessage={
          catalogError
            ? `Cannot read ${CATALOG.namespace}/${CATALOG.name}: ${catalogError.message}`
            : 'No models in the catalogue.'
        }
        columns={[
          { label: 'Name', getter: (r: Row) => r.name },
          { label: 'Model', getter: (r: Row) => r.model_id ?? '' },
          { label: 'Category', getter: (r: Row) => r.category ?? '' },
          { label: 'Server', getter: (r: Row) => r.default_server ?? r.runtime ?? '' },
          {
            label: 'Resources',
            getter: (r: Row) => [r.cpu && `${r.cpu} CPU`, r.memory].filter(Boolean).join(', '),
          },
          {
            label: 'Status',
            getter: (r: Row) => (
              <StatusLabel status={statusColour[r.state]} title={r.reason}>
                {r.state}
              </StatusLabel>
            ),
          },
        ]}
      />
    </SectionBox>
  );
}
