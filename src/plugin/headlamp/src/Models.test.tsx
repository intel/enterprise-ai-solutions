// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Row } from './data';
import { ModelCards } from './ModelCards';

const row = (r: Partial<Row>): Row => ({
  name: 'm',
  state: 'Available',
  reason: '',
  deployed: false,
  inCatalogue: true,
  ...r,
});

describe('ModelCards', () => {
  it('renders one card per model with its facts, status and action', () => {
    render(
      <ModelCards
        rows={[
          row({
            name: 'qwen3-0-6b',
            model_id: 'Qwen/Qwen3-0.6B',
            category: 'llm',
            cpu: 8,
            memory: '16Gi',
            state: 'Ready',
          }),
          row({ name: 'custom', state: 'Deploying', inCatalogue: false }),
        ]}
        empty="none"
        lastAction={r => (r.name === 'qwen3-0-6b' ? 'Last deploy: Succeeded' : null)}
        action={r => <button>{`act ${r.name}`}</button>}
      />
    );
    expect(screen.getByRole('heading', { name: 'qwen3-0-6b' })).toBeTruthy();
    expect(screen.getByText('Qwen/Qwen3-0.6B')).toBeTruthy();
    expect(screen.getByText('8 CPU')).toBeTruthy();
    expect(screen.getByText('Ready')).toBeTruthy();
    expect(screen.getByText('Last deploy: Succeeded')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'act qwen3-0-6b' })).toBeTruthy();
    expect(screen.getByText('outside the catalogue')).toBeTruthy();
  });

  it('shows the empty message when there are no models', () => {
    render(
      <ModelCards
        rows={[]}
        empty="No models in the catalogue."
        lastAction={() => null}
        action={() => null}
      />
    );
    expect(screen.getByText('No models in the catalogue.')).toBeTruthy();
  });
});
