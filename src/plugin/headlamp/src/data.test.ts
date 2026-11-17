// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import {
  accessExample,
  aroundModels,
  buildJob,
  catalogAccess,
  catalogModels,
  categories,
  clusterGpuResources,
  filterRows,
  jobArgs,
  latestJobs,
  liveFacts,
  modelAccelerators,
  modelDevice,
  modelPods,
  offeredModels,
  parseJobTemplate,
  portalEntries,
  Row,
  rowActions,
} from './data';

const entry = (v: unknown) => JSON.stringify(v);

describe('portalEntries', () => {
  it('returns nothing without data', () => {
    expect(portalEntries(undefined)).toEqual([]);
    expect(portalEntries({})).toEqual([]);
  });

  it('keeps valid entries in the overview order, unlisted ones last by title', () => {
    const svc = (title: string) => entry({ title, url: 'https://x.example.com' });
    const got = portalEntries({
      zeta: svc('Zeta'),
      langfuse: svc('Langfuse'),
      'litellm-ui': svc('LiteLLM'),
      alpha: svc('Alpha'),
      grafana: entry({ title: 'Grafana', url: 'https://grafana.example.com' }),
      keycloak: entry({ title: 'Keycloak', description: 'IAM', url: 'https://kc.example.com/' }),
    });
    expect(got.map(e => e.key)).toEqual([
      'keycloak',
      'grafana',
      'litellm-ui',
      'langfuse',
      'alpha',
      'zeta',
    ]);
    expect(got[1]).toEqual({
      key: 'grafana',
      title: 'Grafana',
      description: '',
      url: 'https://grafana.example.com/',
    });
  });

  it('drops links that are not absolute https URLs', () => {
    const got = portalEntries({
      js: entry({ title: 'X', url: 'javascript:alert(1)' }),
      http: entry({ title: 'Y', url: 'http://plain.example.com' }),
      rel: entry({ title: 'Z', url: '/relative' }),
    });
    expect(got).toEqual([]);
  });

  it('drops malformed entries', () => {
    const got = portalEntries({
      json: '{not json',
      untitled: entry({ url: 'https://a.example.com' }),
      ok: entry({ title: 'Ok', url: 'https://ok.example.com' }),
    });
    expect(got.map(e => e.key)).toEqual(['ok']);
  });
});

describe('aroundModels', () => {
  it('puts Keycloak before the Models card and the rest after it', () => {
    const svc = (title: string) => entry({ title, url: 'https://x.example.com' });
    const [before, after] = aroundModels(
      portalEntries({
        langfuse: svc('Langfuse'),
        grafana: svc('Grafana'),
        keycloak: svc('Keycloak'),
        x: svc('X'),
      })
    );
    expect(before.map(e => e.key)).toEqual(['keycloak']);
    expect(after.map(e => e.key)).toEqual(['grafana', 'langfuse', 'x']);
  });

  it('shows Models first when Keycloak is not published', () => {
    const [before, after] = aroundModels(
      portalEntries({ grafana: entry({ title: 'Grafana', url: 'https://g.example.com' }) })
    );
    expect(before).toEqual([]);
    expect(after.map(e => e.key)).toEqual(['grafana']);
  });
});

describe('modelAccelerators', () => {
  it('maps the engine vendor to its GPU resource', () => {
    expect(modelAccelerators({ name: 'a', runtime: 'vllm-cuda' })).toEqual(['nvidia.com/gpu']);
    expect(modelAccelerators({ name: 'b', default_server: 'vllm-xpu', runtime: 'vllm' })).toEqual([
      'gpu.intel.com/xe',
      'gpu.intel.com/i915',
    ]);
    expect(modelAccelerators({ name: 'c', runtime: 'vllm-rocm' })).toEqual(['amd.com/gpu']);
  });

  it('prefers an explicit accelerator, and device: cpu means none', () => {
    expect(
      modelAccelerators({
        name: 'a',
        runtime: 'vllm-cuda',
        accelerator_resource: 'nvidia.com/mig-1g.10gb',
      })
    ).toEqual(['nvidia.com/mig-1g.10gb']);
    expect(modelAccelerators({ name: 'b', device: 'cpu', runtime: 'vllm-cuda' })).toEqual([]);
  });

  it('accepts any GPU for device: gpu or a gpus count without a vendor', () => {
    expect(modelAccelerators({ name: 'a', device: 'gpu' })).toContain('gpu.intel.com/xe');
    expect(modelAccelerators({ name: 'b', gpus: 2 })).toContain('nvidia.com/gpu');
  });

  it('is empty for CPU engines', () => {
    expect(modelAccelerators({ name: 'a', runtime: 'vllm' })).toEqual([]);
    expect(modelAccelerators({ name: 'b', runtime: 'openvino' })).toEqual([]);
    expect(modelDevice({ name: 'c', runtime: 'vllm' })).toBe('cpu');
    expect(modelDevice({ name: 'd', runtime: 'vllm-cuda' })).toBe('gpu');
  });
});

describe('clusterGpuResources', () => {
  const node = (allocatable: Record<string, string>) => ({ status: { allocatable } });

  it('collects extended resources above zero, from every node', () => {
    const got = clusterGpuResources([
      node({ cpu: '172', memory: '503Gi', 'intel.com/qat': '2' }),
      node({ 'nvidia.com/gpu': '8', 'gpu.intel.com/xe': '0' }),
    ]);
    expect([...got].sort()).toEqual(['intel.com/qat', 'nvidia.com/gpu']);
  });

  it('is empty without nodes, while loading or without access', () => {
    expect(clusterGpuResources([]).size).toBe(0);
    expect(clusterGpuResources(undefined).size).toBe(0);
    expect(clusterGpuResources(null).size).toBe(0);
  });
});

describe('offeredModels', () => {
  const models = [
    { name: 'cpu-model', runtime: 'vllm' },
    { name: 'nvidia-model', runtime: 'vllm-cuda' },
    { name: 'intel-model', runtime: 'vllm-xpu' },
  ];
  const names = (gpus: string[], deployed: string[] = []) =>
    offeredModels(models, new Set(gpus), new Set(deployed)).map(m => m.name);

  it('hides GPU models on a cluster without a GPU', () => {
    expect(names([])).toEqual(['cpu-model']);
    expect(names(['intel.com/qat'])).toEqual(['cpu-model']);
  });

  it("offers a GPU model only where its vendor's GPU is", () => {
    expect(names(['nvidia.com/gpu'])).toEqual(['cpu-model', 'nvidia-model']);
    expect(names(['gpu.intel.com/i915'])).toEqual(['cpu-model', 'intel-model']);
    expect(names(['nvidia.com/gpu', 'gpu.intel.com/xe'])).toEqual([
      'cpu-model',
      'nvidia-model',
      'intel-model',
    ]);
  });

  it('keeps a deployed GPU model visible, so it can still be undeployed', () => {
    expect(names([], ['nvidia-model'])).toEqual(['cpu-model', 'nvidia-model']);
  });
});

describe('catalogModels', () => {
  it('returns nothing for missing or invalid data', () => {
    expect(catalogModels(undefined)).toEqual([]);
    expect(catalogModels({})).toEqual([]);
    expect(catalogModels({ 'models.json': '{bad' })).toEqual([]);
    expect(catalogModels({ 'models.json': entry({ name: 'not-a-list' }) })).toEqual([]);
  });

  it('keeps only models with a name', () => {
    const got = catalogModels({
      'models.json': entry([
        { name: 'qwen3-0-6b', category: 'llm' },
        { category: 'nameless' },
        null,
      ]),
    });
    expect(got).toEqual([{ name: 'qwen3-0-6b', category: 'llm' }]);
  });
});

const template = {
  apiVersion: 'batch/v1',
  kind: 'Job',
  metadata: { namespace: 'model-manager', labels: { 'app.kubernetes.io/name': 'model-manager' } },
  spec: {
    template: {
      metadata: { labels: { 'app.kubernetes.io/name': 'model-manager' } },
      spec: { containers: [{ name: 'model-manager', image: 'img@sha256:x', args: [] }] },
    },
  },
};

describe('jobArgs', () => {
  it('deploys with --wait and only non-default scale flags', () => {
    expect(jobArgs('deploy', 'qwen3-0-6b')).toEqual(['deploy', 'qwen3-0-6b', '--wait']);
    expect(jobArgs('deploy', 'qwen3-0-6b', { replicas: 2 })).toEqual([
      'deploy',
      'qwen3-0-6b',
      '--wait',
      '--replicas',
      '2',
    ]);
    expect(jobArgs('undeploy', 'qwen3-0-6b', { replicas: 3 })).toEqual([
      'undeploy',
      'qwen3-0-6b',
      '--wait',
    ]);
  });

  it('rejects names and values the admission policy would refuse', () => {
    expect(() => jobArgs('deploy', 'Bad Name')).toThrow();
    expect(() => jobArgs('deploy', 'm; rm -rf /')).toThrow();
    expect(() => jobArgs('deploy', 'm', { replicas: 9 })).toThrow();
    expect(() => jobArgs('deploy', 'm', { replicas: 0 })).toThrow();
  });
});

describe('buildJob', () => {
  it('sets only name, labels and args, leaving the template intact', () => {
    const job = buildJob(template, 'deploy', 'qwen3-0-6b', { replicas: 2 });
    expect(job.metadata.generateName).toBe('deploy-qwen3-0-6b-');
    expect(job.metadata.labels).toMatchObject({
      'app.kubernetes.io/name': 'model-manager',
      'ai.intel.com/model': 'qwen3-0-6b',
      'ai.intel.com/action': 'deploy',
    });
    expect(job.spec.template.metadata.labels['ai.intel.com/model']).toBe('qwen3-0-6b');
    expect(job.spec.template.spec.containers[0]).toMatchObject({
      image: 'img@sha256:x',
      args: ['deploy', 'qwen3-0-6b', '--wait', '--replicas', '2'],
    });
    expect(template.spec.template.spec.containers[0].args).toEqual([]);
  });

  it('keeps generateName within the name length limit', () => {
    const job = buildJob(template, 'undeploy', 'a'.repeat(60));
    expect(job.metadata.generateName.length).toBeLessThanOrEqual(58);
  });
});

describe('parseJobTemplate', () => {
  it('accepts a Job template and refuses anything else', () => {
    expect(parseJobTemplate({ 'job.json': JSON.stringify(template) })?.kind).toBe('Job');
    expect(parseJobTemplate({ 'job.json': '{"kind":"Pod"}' })).toBeNull();
    expect(parseJobTemplate({ 'job.json': 'not json' })).toBeNull();
    expect(parseJobTemplate(undefined)).toBeNull();
  });
});

describe('latestJobs', () => {
  const job = (name: string, model: string, action: string, at: string, status = {}) => ({
    metadata: {
      name,
      creationTimestamp: at,
      labels: { 'ai.intel.com/model': model, 'ai.intel.com/action': action },
    },
    status,
  });

  it('keeps the newest Job per model with its state', () => {
    const latest = latestJobs([
      job('d1', 'm', 'deploy', '2026-10-19T10:00:00Z', { succeeded: 1 }),
      job('u1', 'm', 'undeploy', '2026-10-19T11:00:00Z', { active: 1 }),
      job('d2', 'n', 'deploy', '2026-10-19T09:00:00Z', { failed: 1 }),
      job('x', 'o', 'exec', '2026-10-19T12:00:00Z'),
    ]);
    expect(latest.get('m')).toEqual({ name: 'u1', action: 'undeploy', state: 'Running' });
    expect(latest.get('n')).toEqual({ name: 'd2', action: 'deploy', state: 'Failed' });
    expect(latest.has('o')).toBe(false);
  });
});

const vrow = (r: Partial<Row>): Row => ({
  name: 'm',
  state: 'Available',
  reason: '',
  deployed: false,
  inCatalogue: true,
  ...r,
});

describe('filterRows and categories', () => {
  const rows = [
    vrow({ name: 'qwen3-0-6b', model_id: 'Qwen/Qwen3-0.6B', category: 'llm', state: 'Ready' }),
    vrow({ name: 'bge-base-en', model_id: 'BAAI/bge-base-en-v1.5', category: 'embed' }),
    vrow({ name: 'llama3-8b-awq', category: 'llm', state: 'Failed' }),
  ];
  it('searches name and model ID, and matches status and type exactly', () => {
    expect(filterRows(rows, { query: 'baai' }).map(r => r.name)).toEqual(['bge-base-en']);
    expect(filterRows(rows, { query: ' QWEN ' }).map(r => r.name)).toEqual(['qwen3-0-6b']);
    expect(filterRows(rows, { category: 'llm', state: 'Failed' }).map(r => r.name)).toEqual([
      'llama3-8b-awq',
    ]);
    expect(filterRows(rows, {})).toHaveLength(3);
  });
  it('lists each type once, sorted', () => {
    expect(categories(rows)).toEqual(['embed', 'llm']);
  });
});

describe('rowActions', () => {
  it('offers deploy or undeploy only to people who may, never during a run', () => {
    expect(rowActions(vrow({}), true)).toEqual(['deploy']);
    expect(rowActions(vrow({ deployed: true }), true)).toEqual(['undeploy']);
    expect(rowActions(vrow({}), false)).toEqual([]);
    expect(rowActions(vrow({ inCatalogue: false, deployed: true }), true)).toEqual([]);
    expect(
      rowActions(vrow({ job: { name: 'j', action: 'deploy', state: 'Running' } }), true)
    ).toEqual([]);
  });
});

describe('modelPods', () => {
  const pod = (name: string, labels: Record<string, string>, phase = 'Running') => ({
    metadata: { name, labels },
    status: { phase },
  });
  it('finds the server and router pods of a model, preferring Running ones', () => {
    const pods = [
      pod(
        'q-old',
        {
          'app.kubernetes.io/name': 'q',
          'app.kubernetes.io/component': 'llminferenceservice-workload',
        },
        'Failed'
      ),
      pod('q-new', {
        'app.kubernetes.io/name': 'q',
        'app.kubernetes.io/component': 'llminferenceservice-workload',
      }),
      pod('q-rs', {
        'app.kubernetes.io/name': 'q',
        'app.kubernetes.io/component': 'llminferenceservice-router-scheduler',
      }),
      pod('download-q', { 'app.kubernetes.io/name': 'q', 'job-name': 'download-q' }),
      pod('e-pred', { 'serving.kserve.io/inferenceservice': 'e', component: 'predictor' }),
    ];
    expect(modelPods(pods, 'q')).toEqual({ server: 'q-new', router: 'q-rs' });
    expect(modelPods(pods, 'e')).toEqual({ server: 'e-pred' });
    expect(modelPods(pods, 'none')).toEqual({});
  });
});

describe('liveFacts', () => {
  it('reads image and size from an LLMInferenceService', () => {
    const spec = {
      template: {
        containers: [
          {
            image: 'docker.io/vllm/vllm-openai-cpu:v0.24.0',
            resources: { requests: { cpu: '4', memory: '16Gi' } },
          },
        ],
      },
    };
    expect(liveFacts({ spec })).toEqual({
      runtime: 'vllm-openai-cpu:v0.24.0',
      cpu: '4',
      memory: '16Gi',
    });
  });

  it('falls back to the runtime name of an InferenceService', () => {
    const spec = {
      predictor: {
        model: { runtime: 'openvino-runtime', resources: { requests: { cpu: 8, memory: '8Gi' } } },
      },
    };
    expect(liveFacts({ spec })).toEqual({ runtime: 'openvino-runtime', cpu: 8, memory: '8Gi' });
  });

  it('returns no facts for an empty object', () => {
    expect(liveFacts({})).toEqual({ runtime: undefined, cpu: undefined, memory: undefined });
  });
});

describe('catalogAccess', () => {
  const data = (v: unknown) => ({ 'access.json': JSON.stringify(v) });

  it('reads the base URL and credential type', () => {
    expect(catalogAccess(data({ base_url: 'https://solutions.ai/inference/v1/', auth_type: 'jwt' }))).toEqual({
      baseURL: 'https://solutions.ai/inference/v1',
      authType: 'jwt',
    });
  });

  it('refuses missing data, non-https URLs and unknown credential types', () => {
    expect(catalogAccess(undefined)).toBeNull();
    expect(catalogAccess({ 'access.json': 'not json' })).toBeNull();
    expect(catalogAccess(data({ base_url: 'javascript:alert(1)', auth_type: 'jwt' }))).toBeNull();
    expect(catalogAccess(data({ base_url: 'http://x/v1', auth_type: 'jwt' }))).toBeNull();
    expect(catalogAccess(data({ base_url: 'https://x/v1', auth_type: 'basic' }))).toBeNull();
  });
});

describe('accessExample', () => {
  const access = { baseURL: 'https://inference.solutions.ai/v1', authType: 'jwt' as const };

  it('picks the API and body by category, the model in the body', () => {
    expect(accessExample(access, 'q').endpoint).toBe('https://inference.solutions.ai/v1/chat/completions');
    expect(JSON.parse(accessExample(access, 'q').body).model).toBe('q');
    expect(accessExample(access, 'e', 'embed').endpoint).toMatch(/\/v1\/embeddings$/);
    expect(JSON.parse(accessExample(access, 'e', 'embed').body).input).toEqual(['Hello world']);
    expect(accessExample(access, 'r', 'rerank').endpoint).toMatch(/\/v1\/score$/);
  });
});
