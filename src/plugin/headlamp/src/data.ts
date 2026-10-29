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
