// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import { catalogModels, portalEntries } from './data';

const entry = (v: unknown) => JSON.stringify(v);

describe('portalEntries', () => {
  it('returns nothing without data', () => {
    expect(portalEntries(undefined)).toEqual([]);
    expect(portalEntries({})).toEqual([]);
  });

  it('keeps valid entries, sorted by title', () => {
    const got = portalEntries({
      keycloak: entry({ title: 'Keycloak', description: 'IAM', url: 'https://kc.example.com/' }),
      grafana: entry({ title: 'Grafana', url: 'https://grafana.example.com' }),
    });
    expect(got.map(e => e.key)).toEqual(['grafana', 'keycloak']);
    expect(got[0]).toEqual({
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

describe('catalogModels', () => {
  it('returns nothing for missing or invalid data', () => {
    expect(catalogModels(undefined)).toEqual([]);
    expect(catalogModels({})).toEqual([]);
    expect(catalogModels({ 'models.json': '{bad' })).toEqual([]);
    expect(catalogModels({ 'models.json': entry({ name: 'not-a-list' }) })).toEqual([]);
  });

  it('keeps only models with a name', () => {
    const got = catalogModels({
      'models.json': entry([{ name: 'qwen3-0-6b', category: 'llm' }, { category: 'nameless' }, null]),
    });
    expect(got).toEqual([{ name: 'qwen3-0-6b', category: 'llm' }]);
  });
});
