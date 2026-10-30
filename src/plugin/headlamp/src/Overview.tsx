// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { Icon } from '@iconify/react';
import { K8s, Router } from '@kinvolk/headlamp-plugin/lib';
import { Box, Button, GlobalStyles, Paper, Stack, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { ABOUT } from './about';
import { BRAND } from './Branding';
import { CATALOG, catalogModels, PORTAL, portalEntries } from './data';
import { LLMInferenceService, readyCondition } from './resources';
import { CardGrid, PlatformCard, ServiceCards } from './ServicesList';

// Headlamp lays overview items out in one row of 300px cells. Ours take full-width
// cells, so each sits on its own line: About first, the service cards after the tiles.
const ROW = 'data-enterprise-ai-row';
const fullWidthCell = {
  [`.MuiGrid-item:has(> [${ROW}])`]: { maxWidth: '100% !important', flexBasis: '100% !important' },
};

export function About() {
  return (
    <Paper variant="outlined" sx={{ p: 2.5 }} {...{ [ROW]: '' }}>
      <GlobalStyles styles={fullWidthCell} />
      <Typography variant="h5" component="h2">
        {BRAND}
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
        {ABOUT.summary}
      </Typography>
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
        }}
      >
        {ABOUT.features.map(f => (
          <Stack key={f.title} direction="row" spacing={1.5} alignItems="flex-start">
            <Box sx={{ color: 'primary.main', pt: 0.25 }}>
              <Icon icon={f.icon} width={24} />
            </Box>
            <Box>
              <Typography variant="subtitle2" component="h3">
                {f.title}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {f.text}
              </Typography>
            </Box>
          </Stack>
        ))}
      </Box>
    </Paper>
  );
}

function ModelsCard() {
  const [catalog] = K8s.ResourceClasses.ConfigMap.useGet(CATALOG.name, CATALOG.namespace);
  const [services] = LLMInferenceService.useList({ namespace: CATALOG.namespace });
  const deployed = services ?? [];
  const ready = deployed.filter(s => readyCondition(s)?.status === 'True').length;
  const starting = deployed.length - ready;
  return (
    <PlatformCard
      title="Models"
      description={`${ready} of ${catalogModels(catalog?.data).length} ready${
        starting ? `, ${starting} starting` : ''
      }`}
      action={
        // In-app page, so same tab, unlike the external services.
        <Button
          variant="contained"
          size="small"
          component={RouterLink}
          to={Router.createRouteURL('ea-models')}
          aria-label="Open Models"
        >
          Open
        </Button>
      }
    />
  );
}

function PlatformRow() {
  const [portal, error] = K8s.ResourceClasses.ConfigMap.useGet(PORTAL.name, PORTAL.namespace);
  const entries = portalEntries(portal?.data);
  return (
    <Box {...{ [ROW]: '' }}>
      <GlobalStyles styles={fullWidthCell} />
      <Typography variant="h6" component="h2" sx={{ mb: 1.5 }}>
        Platform services
      </Typography>
      {error && entries.length === 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Cannot read the service list.
        </Typography>
      )}
      <CardGrid>
        <ServiceCards entries={entries} />
        <ModelsCard />
      </CardGrid>
    </Box>
  );
}

export const aboutTile = { id: 'enterprise-ai-about', component: () => <About /> };
export const platformTile = { id: 'enterprise-ai-platform', component: () => <PlatformRow /> };
