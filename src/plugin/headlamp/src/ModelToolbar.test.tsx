// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ModelToolbar } from './ModelToolbar';

describe('ModelToolbar', () => {
  it('reports search text', () => {
    const onFilter = vi.fn();
    render(<ModelToolbar filter={{}} categories={['llm']} onFilter={onFilter} />);
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } });
    expect(onFilter).toHaveBeenCalledWith({ query: 'qwen' });
  });
});
