// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import {
  registerAppLogo,
  registerOverviewChartsProcessor,
  registerRoute,
  registerSidebarEntry,
} from '@kinvolk/headlamp-plugin/lib';
import { BrandLogo } from './Branding';
import { Models } from './Models';
import { aboutTile, platformTile } from './Overview';

const pages = [
  {
    name: 'ea-models',
    label: 'Models',
    url: '/enterprise-ai/models',
    icon: 'mdi:brain',
    component: Models,
  },
];

for (const page of pages) {
  registerSidebarEntry({
    parent: null,
    name: page.name,
    label: page.label,
    url: page.url,
    icon: page.icon,
  });
  registerRoute({
    path: page.url,
    sidebar: page.name,
    name: page.name,
    exact: true,
    component: page.component,
  });
}

registerAppLogo(BrandLogo);

// Landing page (cluster overview): About on top, Headlamp's tiles, then services and models.
registerOverviewChartsProcessor({
  id: 'enterprise-ai',
  processor: charts => [aboutTile, ...charts, platformTile],
});
