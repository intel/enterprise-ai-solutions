// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

// Written by the installer (gateway role / llm_services role).
export const PORTAL = { name: 'platform-portal', namespace: 'envoy-gateway-system' };
export const CATALOG = { name: 'model-catalog', namespace: 'llm-inference' };

export interface PortalEntry {
  key: string;
  title: string;
  description: string;
  url: string;
}

export interface CatalogModel {
  name: string;
  model_id?: string;
  category?: string;
  default_server?: string;
  runtime?: string;
  cpu?: number | string;
  memory?: string;
}

function parseJSON(raw: string | undefined): unknown {
  if (!raw) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

// Only absolute https links: a tampered entry must not inject javascript: URLs.
function safeHttpsURL(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

export function portalEntries(data: Record<string, string> | undefined): PortalEntry[] {
  return Object.entries(data ?? {})
    .map(([key, raw]) => {
      const v = parseJSON(raw) as Partial<PortalEntry> | undefined;
      const url = safeHttpsURL(v?.url);
      if (!v || !url || typeof v.title !== 'string') return null;
      return {
        key,
        title: v.title,
        description: typeof v.description === 'string' ? v.description : '',
        url,
      };
    })
    .filter((e): e is PortalEntry => e !== null)
    .sort((a, b) => a.title.localeCompare(b.title));
}

export function catalogModels(data: Record<string, string> | undefined): CatalogModel[] {
  const list = parseJSON(data?.['models.json']);
  if (!Array.isArray(list)) return [];
  return list.filter((m): m is CatalogModel => typeof m?.name === 'string');
}

// ── How to call a model: model-catalog access.json (llm_services role) ─────
export type AuthType = 'jwt' | 'litellm_vkey';

export interface Access {
  baseURL: string;
  authType: AuthType;
}

// null when absent (an older inference release) or not a plain https URL.
export function catalogAccess(data: Record<string, string> | undefined): Access | null {
  const v = parseJSON(data?.['access.json']) as { base_url?: unknown; auth_type?: unknown };
  const url = safeHttpsURL(v?.base_url);
  if (!url || (v.auth_type !== 'jwt' && v.auth_type !== 'litellm_vkey')) return null;
  return { baseURL: url.replace(/\/+$/, ''), authType: v.auth_type };
}

export interface AccessExample {
  endpoint: string;
  body: string;
}

// The request model-manager prints after a deploy: the API by category, the model in the body.
export function accessExample(access: Access, model: string, category?: string): AccessExample {
  const api = category === 'embed' ? 'embeddings' : category === 'rerank' ? 'score' : 'chat/completions';
  const body =
    category === 'embed'
      ? { model, input: ['Hello world'] }
      : category === 'rerank'
      ? { model, text_1: 'query', text_2: ['doc1', 'doc2'] }
      : { model, messages: [{ role: 'user', content: 'Hello' }], max_tokens: 16 };
  return { endpoint: `${access.baseURL}/${api}`, body: JSON.stringify(body) };
}

// ── Deploy from the Models page: model-manager Jobs ─────────────────────────
// The template is published by the installer (llm_services role); an admission
// policy admits only pods built from it, with deploy|undeploy <catalogue name>.
export const MODEL_MANAGER = { namespace: 'model-manager', template: 'model-manager-job' };
export const LABEL_MODEL = 'ai.intel.com/model';
export const LABEL_ACTION = 'ai.intel.com/action';
export const MAX_SCALE = 8;

export type Action = 'deploy' | 'undeploy';
export type JobState = 'Running' | 'Succeeded' | 'Failed';

export interface DeployOptions {
  replicas?: number;
}

export interface JobLike {
  metadata: {
    name: string;
    creationTimestamp?: string;
    labels?: Record<string, string>;
  };
  status?: {
    active?: number;
    succeeded?: number;
    failed?: number;
    conditions?: { type: string; status: string }[];
  };
}

export interface LatestJob {
  name: string;
  action: Action;
  state: JobState;
}

// Same checks as the admission policy, so a bad value fails here first.
const DNS_LABEL = /^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$/;
const inRange = (n: number | undefined) =>
  n === undefined || (Number.isInteger(n) && n >= 1 && n <= MAX_SCALE);

export function jobArgs(action: Action, model: string, opts: DeployOptions = {}): string[] {
  if (!DNS_LABEL.test(model)) throw new Error(`Invalid model name: ${model}`);
  if (!inRange(opts.replicas)) throw new Error(`Replicas must be 1-${MAX_SCALE}`);
  const args: string[] = [action, model, '--wait'];
  if (action === 'deploy' && opts.replicas && opts.replicas > 1)
    args.push('--replicas', String(opts.replicas));
  return args;
}

export function parseJobTemplate(
  data: Record<string, string> | undefined
): Record<string, any> | null {
  const t = parseJSON(data?.['job.json']) as Record<string, any> | undefined;
  return t?.kind === 'Job' && Array.isArray(t?.spec?.template?.spec?.containers) ? t : null;
}

// The template with only names, labels and args set: anything else the
// admission policy would reject.
export function buildJob(
  template: Record<string, any>,
  action: Action,
  model: string,
  opts: DeployOptions = {}
): Record<string, any> {
  const job = JSON.parse(JSON.stringify(template));
  const labels = { [LABEL_MODEL]: model, [LABEL_ACTION]: action };
  job.metadata = {
    ...job.metadata,
    generateName: `${action}-${model}`.slice(0, 57) + '-',
    labels: { ...job.metadata?.labels, ...labels },
  };
  const pod = job.spec.template;
  pod.metadata = { ...pod.metadata, labels: { ...pod.metadata?.labels, ...labels } };
  pod.spec.containers[0].args = jobArgs(action, model, opts);
  return job;
}

function jobState(job: JobLike): JobState {
  const s = job.status ?? {};
  if (s.succeeded) return 'Succeeded';
  if (s.failed || s.conditions?.some(c => c.type === 'Failed' && c.status === 'True'))
    return 'Failed';
  return 'Running';
}

// Newest Job per model.
export function latestJobs(jobs: JobLike[]): Map<string, LatestJob> {
  const out = new Map<string, LatestJob>();
  const newest = new Map<string, string>();
  for (const j of jobs) {
    const model = j.metadata.labels?.[LABEL_MODEL];
    const action = j.metadata.labels?.[LABEL_ACTION];
    if (!model || (action !== 'deploy' && action !== 'undeploy')) continue;
    const at = j.metadata.creationTimestamp ?? '';
    if (out.has(model) && at <= (newest.get(model) ?? '')) continue;
    newest.set(model, at);
    out.set(model, { name: j.metadata.name, action, state: jobState(j) });
  }
  return out;
}

// ── Models page view model ───────────────────────────────────────────────────

export type State = 'Ready' | 'Deploying' | 'Undeploying' | 'Failed' | 'Available';
export const STATES: State[] = ['Ready', 'Deploying', 'Undeploying', 'Failed', 'Available'];

export interface Row extends CatalogModel {
  state: State;
  reason: string;
  deployed: boolean;
  inCatalogue: boolean;
  job?: LatestJob;
}

export const statusColour: Record<State, 'success' | 'warning' | 'error' | ''> = {
  Ready: 'success',
  Deploying: 'warning',
  Undeploying: 'warning',
  Failed: 'error',
  Available: '',
};

export interface RowFilter {
  query?: string;
  state?: State | '';
  category?: string;
}

// Search over name and model ID; state and category must match exactly.
export function filterRows(rows: Row[], f: RowFilter): Row[] {
  const q = (f.query ?? '').trim().toLowerCase();
  return rows.filter(
    r =>
      (!q || r.name.toLowerCase().includes(q) || (r.model_id ?? '').toLowerCase().includes(q)) &&
      (!f.state || r.state === f.state) &&
      (!f.category || r.category === f.category)
  );
}

export function categories(rows: Row[]): string[] {
  return [...new Set(rows.map(r => r.category).filter((c): c is string => !!c))].sort();
}

// Every serving resource model-manager creates carries this label.
export const MANAGED_BY = 'app.kubernetes.io/managed-by=model-manager';

export interface ServingLike {
  spec?: {
    template?: { containers?: ContainerLike[] };
    predictor?: { model?: ContainerLike & { runtime?: string } };
  };
}

interface ContainerLike {
  image?: string;
  resources?: { requests?: { cpu?: string | number; memory?: string } };
}

// Facts a deployed resource carries, for models with no catalogue entry: the
// Hugging Face ID is not recorded on it, the image and its size are.
export function liveFacts(s: ServingLike): Pick<CatalogModel, 'runtime' | 'cpu' | 'memory'> {
  const llm = s.spec?.template?.containers?.[0];
  const model = s.spec?.predictor?.model;
  const c = llm ?? model ?? {};
  const requests = c.resources?.requests ?? {};
  return {
    runtime: c.image?.split('/').pop() || model?.runtime || undefined,
    cpu: requests.cpu,
    memory: requests.memory,
  };
}

// What a person may start on a row: catalogue models only, never while a run is going.
export function rowActions(r: Row, canManage: boolean): Action[] {
  if (!canManage || !r.inCatalogue || r.job?.state === 'Running') return [];
  return [r.deployed ? 'undeploy' : 'deploy'];
}

export interface PodLike {
  metadata: { name: string; labels?: Record<string, string> };
  status?: { phase?: string };
}

export interface ModelPods {
  server?: string;
  router?: string;
}

const COMPONENT = 'app.kubernetes.io/component';

// A model's serving pod and, for LLMInferenceService, its router-scheduler;
// a Running pod wins over others of the same role.
export function modelPods(pods: PodLike[], model: string): ModelPods {
  const out: ModelPods = {};
  const running: ModelPods = {};
  for (const p of pods) {
    const l = p.metadata.labels ?? {};
    const isServer =
      (l['app.kubernetes.io/name'] === model && l[COMPONENT] === 'llminferenceservice-workload') ||
      (l['serving.kserve.io/inferenceservice'] === model && l.component === 'predictor');
    const isRouter =
      l['app.kubernetes.io/name'] === model &&
      l[COMPONENT] === 'llminferenceservice-router-scheduler';
    const role = isServer ? 'server' : isRouter ? 'router' : null;
    if (!role) continue;
    out[role] ??= p.metadata.name;
    if (p.status?.phase === 'Running') running[role] ??= p.metadata.name;
  }
  return { ...out, ...running };
}
