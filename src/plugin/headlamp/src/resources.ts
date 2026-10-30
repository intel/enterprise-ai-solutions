// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { KubeObject, KubeObjectInterface } from '@kinvolk/headlamp-plugin/lib/k8s/cluster';

interface Condition {
  type: string;
  status: string;
  reason?: string;
}

interface LLMInferenceServiceSpec extends KubeObjectInterface {
  status?: { conditions?: Condition[] };
}

export class LLMInferenceService extends KubeObject<LLMInferenceServiceSpec> {
  static apiVersion = ['serving.kserve.io/v1alpha2', 'serving.kserve.io/v1alpha1'];
  static kind = 'LLMInferenceService';
  static apiName = 'llminferenceservices';
  static isNamespaced = true;
}

export function readyCondition(svc: LLMInferenceService) {
  return svc.jsonData.status?.conditions?.find(c => c.type === 'Ready');
}
