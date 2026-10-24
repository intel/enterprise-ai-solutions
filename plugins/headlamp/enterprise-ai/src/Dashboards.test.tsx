// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DashboardCards } from './Dashboards';

describe('DashboardCards', () => {
  it('renders one card per entry with an Open button to its URL', () => {
    render(
      <DashboardCards
        entries={[
          { key: 'grafana', title: 'Grafana', description: 'Dashboards', url: 'https://grafana.example.com/' },
          { key: 'keycloak', title: 'Keycloak', description: 'IAM', url: 'https://kc.example.com/admin/' },
        ]}
      />
    );
    const open = screen.getByRole('link', { name: 'Open Grafana' });
    expect(open.getAttribute('href')).toBe('https://grafana.example.com/');
    expect(open.getAttribute('target')).toBe('_blank');
    expect(open.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByRole('link', { name: 'Open Keycloak' })).toBeTruthy();
  });

  it('shows the error, or an empty message, when there are no entries', () => {
    const { rerender } = render(<DashboardCards entries={[]} error="Cannot read portal" />);
    expect(screen.getByText('Cannot read portal')).toBeTruthy();
    rerender(<DashboardCards entries={[]} />);
    expect(screen.getByText('No dashboards published yet.')).toBeTruthy();
  });
});
