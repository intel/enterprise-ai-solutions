// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { Icon } from '@iconify/react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { Access, accessExample } from './data';

const CREDENTIAL = {
  jwt: { label: 'Keycloak JWT', variable: 'TOKEN' },
  litellm_vkey: { label: 'LiteLLM virtual key', variable: 'LITELLM_VIRTUAL_KEY' },
} as const;

function Copy(props: { text: string; what: string }) {
  return (
    <Tooltip title={`Copy ${props.what}`}>
      <IconButton
        size="small"
        aria-label={`Copy ${props.what}`}
        onClick={() => navigator.clipboard?.writeText(props.text)}
      >
        <Icon icon="mdi:content-copy" width={16} />
      </IconButton>
    </Tooltip>
  );
}

function Code(props: { text: string; what: string }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={0.5} sx={{ mt: 0.5 }}>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1,
          flexGrow: 1,
          overflowX: 'auto',
          fontSize: '0.8rem',
          bgcolor: 'action.hover',
          borderRadius: 1,
        }}
      >
        {props.text}
      </Box>
      <Copy text={props.text} what={props.what} />
    </Stack>
  );
}

function Field(props: { label: string; value: string }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography variant="body2" sx={{ minWidth: 72, fontWeight: 600 }}>
        {props.label}
      </Typography>
      <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
        {props.value}
      </Typography>
      <Copy text={props.value} what={props.label.toLowerCase()} />
    </Stack>
  );
}

// How to call one model: never a credential, only how to get one.
export function AccessDialog(props: {
  model: { name: string; category?: string };
  access: Access;
  ready: boolean;
  onClose: () => void;
}) {
  const { endpoint, body } = accessExample(props.access, props.model.name, props.model.category);
  const cred = CREDENTIAL[props.access.authType];
  const curl = [
    `curl -H "Authorization: Bearer $${cred.variable}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '${body}' \\`,
    `  ${endpoint}`,
  ].join('\n');
  return (
    <Dialog open onClose={props.onClose} aria-labelledby="access-title" maxWidth="md" fullWidth>
      <DialogTitle id="access-title">Access {props.model.name}</DialogTitle>
      <DialogContent>
        {!props.ready && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {props.model.name} is not ready yet; requests fail until it is.
          </Alert>
        )}
        <Stack spacing={0.5} sx={{ mb: 2 }}>
          <Field label="Base URL" value={props.access.baseURL} />
          <Field label="Model" value={props.model.name} />
          <Stack direction="row" spacing={1}>
            <Typography variant="body2" sx={{ minWidth: 72, fontWeight: 600 }}>
              Auth
            </Typography>
            <Typography variant="body2">
              {cred.label} (Authorization: Bearer &lt;{cred.variable.toLowerCase()}&gt;)
            </Typography>
          </Stack>
        </Stack>
        {props.access.authType === 'jwt' ? (
          <>
            <Typography variant="subtitle2">
              1. Get a token (valid 15 minutes), in the solutions checkout:
            </Typography>
            <Code
              text="source ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh"
              what="token command"
            />
          </>
        ) : (
          <>
            <Typography variant="subtitle2">
              1. Use a LiteLLM virtual key from your administrator:
            </Typography>
            <Code text="export LITELLM_VIRTUAL_KEY=<your key>" what="key command" />
          </>
        )}
        <Typography variant="subtitle2" sx={{ mt: 2 }}>
          2. Call the model:
        </Typography>
        <Code text={curl} what="request" />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          The gateway certificate is issued by the platform CA: add --cacert with that CA, or -k
          for a quick test.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
