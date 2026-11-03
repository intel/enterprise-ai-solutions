// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AccessDialog } from './AccessDialog';

describe('AccessDialog', () => {
  it('shows the endpoint, model and Keycloak steps, never a credential', () => {
    const onClose = vi.fn();
    render(
      <AccessDialog
        model={{ name: 'qwen3-0-6b', category: 'llm' }}
        access={{ baseURL: 'https://inference.solutions.ai/v1', authType: 'jwt' }}
        ready
        onClose={onClose}
      />
    );
    expect(screen.getByText('Access qwen3-0-6b')).toBeTruthy();
    expect(screen.getByText('https://inference.solutions.ai/v1')).toBeTruthy();
    expect(screen.getByText(/Keycloak JWT/)).toBeTruthy();
    expect(screen.getByText(/get-keycloak-token\.sh/)).toBeTruthy();
    expect(screen.getByText(/\$TOKEN/)).toBeTruthy();
    expect(screen.getByText(/v1\/chat\/completions/)).toBeTruthy();
    expect(screen.queryByText(/not ready yet/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('shows LiteLLM key steps and warns while the model is not ready', () => {
    render(
      <AccessDialog
        model={{ name: 'qwen-alias' }}
        access={{ baseURL: 'https://litellm.solutions.ai/v1', authType: 'litellm_vkey' }}
        ready={false}
        onClose={() => undefined}
      />
    );
    expect(screen.getByText(/LiteLLM virtual key \(/)).toBeTruthy();
    expect(screen.getByText(/LITELLM_VIRTUAL_KEY=<your key>/)).toBeTruthy();
    expect(screen.getByText(/not ready yet/)).toBeTruthy();
    expect(screen.queryByText(/get-keycloak-token/)).toBeNull();
  });
});
