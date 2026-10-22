// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import { ABOUT } from './about';

describe('ABOUT', () => {
  it('has a summary and features with an icon, title and text each', () => {
    expect(ABOUT.summary.length).toBeGreaterThan(20);
    expect(ABOUT.features.length).toBeGreaterThan(0);
    for (const f of ABOUT.features) {
      expect(f.icon).toMatch(/^mdi:/);
      expect(f.title && f.text).toBeTruthy();
    }
  });
});
