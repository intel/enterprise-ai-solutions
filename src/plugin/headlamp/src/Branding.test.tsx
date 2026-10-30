// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BRAND, BrandLogo } from './Branding';

describe('branding', () => {
  it('shows the product name, short on small screens', () => {
    const { rerender } = render(<BrandLogo />);
    expect(screen.getByText(BRAND)).toBeTruthy();
    rerender(<BrandLogo logoType="small" />);
    expect(screen.getByText('EAI')).toBeTruthy();
  });
});
