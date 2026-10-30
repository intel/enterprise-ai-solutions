// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { StatusLabel } from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import { Box, Card, CardActions, CardContent, Chip, Stack, Typography } from '@mui/material';
import { ReactNode } from 'react';
import { Row, statusColour } from './data';

// One card per model: catalogue facts, live status, last action and its button.
export function ModelCards(props: {
  rows: Row[];
  empty: string;
  lastAction: (r: Row) => ReactNode;
  action: (r: Row) => ReactNode;
}) {
  if (props.rows.length === 0) return <Typography color="text.secondary">{props.empty}</Typography>;
  return (
    <Box
      sx={{ display: 'grid', gap: 2, gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}
    >
      {props.rows.map(r => {
        const facts = [
          r.category,
          r.default_server ?? r.runtime,
          r.cpu && `${r.cpu} CPU`,
          r.memory,
        ].filter(Boolean) as string[];
        return (
          <Card key={r.name} variant="outlined" sx={{ display: 'flex', flexDirection: 'column' }}>
            <CardContent sx={{ flexGrow: 1 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="flex-start"
                spacing={1}
              >
                <Typography variant="h6" component="h2" sx={{ wordBreak: 'break-word' }}>
                  {r.name}
                </Typography>
                <StatusLabel status={statusColour[r.state]} title={r.reason}>
                  {r.state}
                </StatusLabel>
              </Stack>
              {r.model_id && (
                <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all' }}>
                  {r.model_id}
                </Typography>
              )}
              <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" sx={{ mt: 1 }}>
                {facts.map(f => (
                  <Chip key={f} label={f} size="small" variant="outlined" />
                ))}
                {!r.inCatalogue && <Chip label="outside the catalogue" size="small" />}
              </Stack>
              <Typography variant="body2" sx={{ mt: 1 }} component="div">
                {props.lastAction(r)}
              </Typography>
            </CardContent>
            <CardActions>{props.action(r)}</CardActions>
          </Card>
        );
      })}
    </Box>
  );
}
