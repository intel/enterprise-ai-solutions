// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  TextField,
} from '@mui/material';
import { useState } from 'react';
import { CatalogModel, DeployOptions, MAX_SCALE } from './data';

// "8 CPU, 16Gi" for one replica, scaled to the chosen replica count.
export function footprint(m: CatalogModel, replicas: number): string {
  const cpu = Number(m.cpu);
  const mem = /^(\d+(?:\.\d+)?)(\D*)$/.exec(m.memory ?? '');
  return [
    Number.isFinite(cpu) && cpu > 0 ? `${cpu * replicas} CPU` : '',
    mem ? `${Number(mem[1]) * replicas}${mem[2]} memory` : '',
  ]
    .filter(Boolean)
    .join(' and ');
}

// Nothing starts until the person answers yes.
export function DeployDialog(props: {
  model: CatalogModel;
  onDeploy: (opts: DeployOptions) => void;
  onClose: () => void;
}) {
  const [replicas, setReplicas] = useState(1);
  const choices = Array.from({ length: MAX_SCALE }, (_, i) => i + 1);
  const uses = footprint(props.model, replicas);
  return (
    <Dialog open onClose={props.onClose} aria-labelledby="deploy-title">
      <DialogTitle id="deploy-title">Deploy {props.model.name}?</DialogTitle>
      <DialogContent>
        <DialogContentText>
          This downloads {props.model.model_id ?? 'the model'} if needed and starts serving it
          {uses ? `, using ${uses}` : ''}. It is published on the gateway once ready.
        </DialogContentText>
        <TextField
          select
          label="Replicas"
          value={replicas}
          onChange={e => setReplicas(Number(e.target.value))}
          sx={{ mt: 2, minWidth: 120 }}
        >
          {choices.map(n => (
            <MenuItem key={n} value={n}>
              {n}
            </MenuItem>
          ))}
        </TextField>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>No</Button>
        <Button variant="contained" onClick={() => props.onDeploy({ replicas })}>
          Yes, deploy
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// Same shape as the deploy question; nothing is removed until the answer is yes.
export function UndeployDialog(props: {
  model: CatalogModel;
  onUndeploy: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog open onClose={props.onClose} aria-labelledby="undeploy-title">
      <DialogTitle id="undeploy-title">Undeploy {props.model.name}?</DialogTitle>
      <DialogContent>
        <DialogContentText>
          {props.model.name} stops serving and is removed from the gateway, so requests to it will
          fail. Its downloaded weights stay, so deploying it again is quicker.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>No</Button>
        <Button variant="contained" color="error" onClick={props.onUndeploy}>
          Yes, undeploy
        </Button>
      </DialogActions>
    </Dialog>
  );
}
