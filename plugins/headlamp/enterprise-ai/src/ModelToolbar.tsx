// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { MenuItem, Stack, TextField } from '@mui/material';
import { RowFilter, State, STATES } from './data';

// Search and two filters above the model cards.
export function ModelToolbar(props: {
  filter: RowFilter;
  categories: string[];
  onFilter: (f: RowFilter) => void;
}) {
  const { filter, onFilter } = props;
  return (
    <Stack
      direction="row"
      spacing={1.5}
      useFlexGap
      flexWrap="wrap"
      alignItems="center"
      sx={{ mb: 2 }}
    >
      <TextField
        size="small"
        label="Search models"
        value={filter.query ?? ''}
        onChange={e => onFilter({ ...filter, query: e.target.value })}
        sx={{ minWidth: 220 }}
      />
      <TextField
        select
        size="small"
        label="Status"
        value={filter.state ?? ''}
        onChange={e => onFilter({ ...filter, state: e.target.value as State | '' })}
        sx={{ minWidth: 140 }}
      >
        <MenuItem value="">All</MenuItem>
        {STATES.map(s => (
          <MenuItem key={s} value={s}>
            {s}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        select
        size="small"
        label="Type"
        value={filter.category ?? ''}
        onChange={e => onFilter({ ...filter, category: e.target.value })}
        sx={{ minWidth: 120 }}
      >
        <MenuItem value="">All</MenuItem>
        {props.categories.map(c => (
          <MenuItem key={c} value={c}>
            {c}
          </MenuItem>
        ))}
      </TextField>
    </Stack>
  );
}
