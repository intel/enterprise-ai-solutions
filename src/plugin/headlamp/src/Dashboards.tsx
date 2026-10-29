// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { K8s } from '@kinvolk/headlamp-plugin/lib';
import { Loader, SectionBox } from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import { Box, Button, Card, CardActions, CardContent, Typography } from '@mui/material';
import { PORTAL, portalEntries, PortalEntry } from './data';

// One card per published UI; links come from portalEntries (https only).
export function DashboardCards({ entries, error }: { entries: PortalEntry[]; error?: string }) {
  if (entries.length === 0) {
    return <Typography color="text.secondary">{error ?? 'No dashboards published yet.'}</Typography>;
  }
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
      }}
    >
      {entries.map(e => (
        <Card key={e.key} variant="outlined" sx={{ display: 'flex', flexDirection: 'column' }}>
          <CardContent sx={{ flexGrow: 1 }}>
            <Typography variant="h6" component="h2">
              {e.title}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {e.description}
            </Typography>
          </CardContent>
          <CardActions>
            <Button
              variant="contained"
              size="small"
              href={e.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open ${e.title}`}
            >
              Open
            </Button>
          </CardActions>
        </Card>
      ))}
    </Box>
  );
}

export function Dashboards() {
  // One watched object: entries appear as services publish, with no polling.
  const [portal, error] = K8s.ResourceClasses.ConfigMap.useGet(PORTAL.name, PORTAL.namespace);

  if (!portal && !error) return <Loader title="Loading dashboards" />;

  return (
    <SectionBox title="Dashboards">
      <DashboardCards
        entries={portalEntries(portal?.data)}
        error={error ? `Cannot read ${PORTAL.namespace}/${PORTAL.name}: ${error.message}` : undefined}
      />
    </SectionBox>
  );
}
