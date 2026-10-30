// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DeployDialog, footprint, UndeployDialog } from './DeployDialog';

const model = { name: 'qwen3-0-6b', model_id: 'Qwen/Qwen3-0.6B', cpu: 8, memory: '16Gi' };

describe('footprint', () => {
  it('scales CPU and memory with replicas', () => {
    expect(footprint(model, 1)).toBe('8 CPU and 16Gi memory');
    expect(footprint(model, 2)).toBe('16 CPU and 32Gi memory');
    expect(footprint({ name: 'x' }, 2)).toBe('');
  });
});

describe('DeployDialog', () => {
  it('asks before deploying and deploys only on yes', () => {
    const onDeploy = vi.fn();
    const onClose = vi.fn();
    render(<DeployDialog model={model} onDeploy={onDeploy} onClose={onClose} />);
    expect(screen.getByText('Deploy qwen3-0-6b?')).toBeTruthy();
    expect(screen.getByText(/using 8 CPU and 16Gi memory/)).toBeTruthy();
    expect(screen.queryByText('Tensor parallel')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onDeploy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Yes, deploy' }));
    expect(onDeploy).toHaveBeenCalledWith({ replicas: 1 });
  });
});

describe('UndeployDialog', () => {
  it('asks before undeploying and undeploys only on yes', () => {
    const onUndeploy = vi.fn();
    const onClose = vi.fn();
    render(<UndeployDialog model={model} onUndeploy={onUndeploy} onClose={onClose} />);
    expect(screen.getByText('Undeploy qwen3-0-6b?')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onUndeploy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Yes, undeploy' }));
    expect(onUndeploy).toHaveBeenCalledOnce();
  });
});
