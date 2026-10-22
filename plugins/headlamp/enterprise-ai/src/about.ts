// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

// Landing-page text about the stack. Edit freely; no code depends on the wording.
export const ABOUT = {
  summary:
    'An on-premises AI platform on Kubernetes: serve open models on Intel CPUs, expose them through one secure gateway, and run the platform from a single console.',
  features: [
    {
      icon: 'mdi:brain',
      title: 'Model serving',
      text: 'vLLM and OpenVINO on KServe, CPU-pinned with NRI balloons; deploy from the catalogue in one click.',
    },
    {
      icon: 'mdi:api',
      title: 'OpenAI-compatible API',
      text: 'LiteLLM in front of every model, with virtual keys, rate limits and spend tracking.',
    },
    {
      icon: 'mdi:shield-lock-outline',
      title: 'Single sign-on',
      text: 'Keycloak signs people in once for Headlamp, Grafana, Langfuse and LiteLLM, with group-based roles.',
    },
    {
      icon: 'mdi:lan',
      title: 'Secure gateway',
      text: 'Envoy Gateway with TLS on every host, path or subdomain routing, and Istio ambient mTLS inside.',
    },
    {
      icon: 'mdi:chart-line',
      title: 'Observability',
      text: 'Prometheus, Grafana, Loki and Tempo for metrics, logs and traces; Langfuse for LLM tracing.',
    },
    {
      icon: 'mdi:console',
      title: 'One console',
      text: 'This Headlamp console: cluster resources with per-person RBAC, platform services and models.',
    },
  ],
};
