// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { Box, Button, Card, CardActions, CardContent, Typography } from '@mui/material';
import { ReactNode } from 'react';
import { PortalEntry } from './data';

// Card grid that fills the row and wraps as the window narrows.
export function CardGrid(props: { children: ReactNode }) {
  return (
    <Box
      sx={{ display: 'grid', gap: 2, gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
    >
      {props.children}
    </Box>
  );
}

export function PlatformCard(props: { title: string; description: ReactNode; action: ReactNode }) {
  return (
    <Card variant="outlined" sx={{ display: 'flex', flexDirection: 'column' }}>
      <CardContent sx={{ flexGrow: 1, pb: 1 }}>
        <Typography variant="h6" component="h3">
          {props.title}
        </Typography>
        <Typography variant="body2" color="text.secondary" component="div">
          {props.description}
        </Typography>
      </CardContent>
      <CardActions sx={{ px: 2, pb: 2 }}>{props.action}</CardActions>
    </Card>
  );
}

// One card per published UI; links come from portalEntries (https only).
export function ServiceCards({ entries }: { entries: PortalEntry[] }) {
  return (
    <>
      {entries.map(e => (
        <PlatformCard
          key={e.key}
          title={e.title}
          description={e.description}
          action={
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
          }
        />
      ))}
    </>
  );
}
