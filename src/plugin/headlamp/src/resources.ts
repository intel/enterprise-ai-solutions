// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { KubeObject, KubeObjectInterface } from '@kinvolk/headlamp-plugin/lib/k8s/cluster';
import { ServingLike } from './data';

interface Condition {
  type: string;
  status: string;
  reason?: string;
}

interface LLMInferenceServiceSpec extends KubeObjectInterface {
  spec?: ServingLike['spec'];
  status?: { conditions?: Condition[] };
}

export class LLMInferenceService extends KubeObject<LLMInferenceServiceSpec> {
  static apiVersion = ['serving.kserve.io/v1alpha2', 'serving.kserve.io/v1alpha1'];
  static kind = 'LLMInferenceService';
  static apiName = 'llminferenceservices';
  static isNamespaced = true;
}

// What model-manager deploys for runtimes that are not LLMInferenceService (OpenVINO).
export class InferenceService extends KubeObject<LLMInferenceServiceSpec> {
  static apiVersion = 'serving.kserve.io/v1beta1';
  static kind = 'InferenceService';
  static apiName = 'inferenceservices';
  static isNamespaced = true;
}

export type ServingObject = LLMInferenceService | InferenceService;

export function readyCondition(svc: ServingObject) {
  return svc.jsonData.status?.conditions?.find(c => c.type === 'Ready');
}
