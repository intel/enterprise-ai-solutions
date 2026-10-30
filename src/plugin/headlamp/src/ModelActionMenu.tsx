// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { Icon } from '@iconify/react';
import { Button, IconButton, Menu } from '@mui/material';
import { ReactNode, useState } from 'react';
import { Action, Row } from './data';

// Deploy or Undeploy on the card itself; both open the yes/no dialog.
export function ModelActionButton(props: {
  row: Row;
  actions: Action[];
  onSelect: (a: Action, r: Row) => void;
}) {
  if (props.actions.includes('deploy'))
    return (
      <Button size="small" variant="contained" onClick={() => props.onSelect('deploy', props.row)}>
        Deploy
      </Button>
    );
  if (props.actions.includes('undeploy'))
    return (
      <Button
        size="small"
        variant="outlined"
        color="error"
        onClick={() => props.onSelect('undeploy', props.row)}
      >
        Undeploy
      </Button>
    );
  return null;
}

// ⋮ menu for secondary items (logs); hidden when it would be empty.
export function ModelMoreMenu(props: { name: string; items: ReactNode[] }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  if (props.items.length === 0) return null;
  return (
    <>
      <IconButton
        size="small"
        aria-label={`More for ${props.name}`}
        onClick={e => setAnchor(e.currentTarget)}
      >
        <Icon icon="mdi:dots-vertical" />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        onClick={() => setAnchor(null)}
      >
        {props.items}
      </Menu>
    </>
  );
}
