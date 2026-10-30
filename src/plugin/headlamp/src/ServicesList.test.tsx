// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CardGrid, ServiceCards } from './ServicesList';

describe('ServiceCards', () => {
  it('renders one card per service with an Open link to its URL in a new tab', () => {
    render(
      <CardGrid>
        <ServiceCards
          entries={[
            {
              key: 'grafana',
              title: 'Grafana',
              description: 'Dashboards',
              url: 'https://grafana.example.com/',
            },
            {
              key: 'keycloak',
              title: 'Keycloak',
              description: 'IAM',
              url: 'https://kc.example.com/admin/',
            },
          ]}
        />
      </CardGrid>
    );
    const open = screen.getByRole('link', { name: 'Open Grafana' });
    expect(open.getAttribute('href')).toBe('https://grafana.example.com/');
    expect(open.getAttribute('target')).toBe('_blank');
    expect(open.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByRole('heading', { name: 'Keycloak' })).toBeTruthy();
    expect(screen.getByText('Dashboards')).toBeTruthy();
  });
});
