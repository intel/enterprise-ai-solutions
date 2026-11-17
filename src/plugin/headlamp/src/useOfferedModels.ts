// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { K8s } from '@kinvolk/headlamp-plugin/lib';
import {
  CatalogModel,
  clusterExtendedResources,
  MODEL_MANAGER,
  offeredModels,
  parseJobTemplate,
  templateAccelerator,
} from './data';

// offered is null until nodes and the Job template are read; no access to either counts as read.
export function useOfferedModels(
  models: CatalogModel[],
  deployed: ReadonlySet<string>
): { offered: CatalogModel[] | null; template: Record<string, any> | null } {
  const [nodes, nodesError] = K8s.ResourceClasses.Node.useList();
  const [templateMap, templateError] = K8s.ResourceClasses.ConfigMap.useGet(
    MODEL_MANAGER.template,
    MODEL_MANAGER.namespace
  );
  const template = parseJobTemplate(templateMap?.data);
  if ((!nodes && !nodesError) || (!templateMap && !templateError))
    return { offered: null, template };
  const offered = offeredModels(
    models,
    clusterExtendedResources(nodes?.map(n => n.jsonData)),
    deployed,
    templateAccelerator(template)
  );
  return { offered, template };
}
