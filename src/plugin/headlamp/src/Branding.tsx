// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { Typography } from '@mui/material';

export const BRAND = 'Enterprise AI';

// Top-bar logo: the product name instead of Headlamp's mark.
export function BrandLogo(props: { logoType?: 'small' | 'large' }) {
  return (
    <Typography
      component="span"
      variant="h6"
      noWrap
      sx={{ fontWeight: 600, color: 'inherit', letterSpacing: 0.2 }}
    >
      {props.logoType === 'small' ? 'EAI' : BRAND}
    </Typography>
  );
}
