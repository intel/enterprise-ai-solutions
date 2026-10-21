// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { Dashboards } from './Dashboards';
import { Models } from './Models';

const pages = [
  {
    name: 'ea-dashboards',
    label: 'Dashboards',
    url: '/enterprise-ai/dashboards',
    icon: 'mdi:view-dashboard-outline',
    component: Dashboards,
  },
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
