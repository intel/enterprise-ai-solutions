# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — resolves a service's gateway routing from the shared
service_routes registry, the cluster's routing_mode, and its base_domain_name.

Registered as a Jinja filter via FilterModule (bottom). Auto-loaded from the
filter_plugins/ dir configured in ansible.cfg.

This is the single place the subdomain-vs-path (apex) derivation lives, so every
role publishes consistently and adding a service is a one-line registry entry.

Usage in a role's defaults:
    my_route: >-
      {{ 'keycloak' | service_route(routing_mode | default('subdomain'),
                                    base_domain_name | default('inference-example.com'),
                                    service_routes | default({}),
                                    service_route_overrides | default({})) }}

Returns a dict:
    host           host the HTTPRoute matches on (apex in path mode, else subdomain)
    relative_path  path the service must serve under ('/keycloak' | '/')
    frontend_url   absolute URL the service advertises (issuer/redirects/assets)
    section_name   gateway listener to attach to ('https-apex' for the apex host,
                   else 'https' — the wildcard '*.base' listener does NOT match apex)
    mode           effective mode after per-service override ('path' | 'subdomain')
    path           logical path from the registry (kept for reference)
    backend/port/namespace  backend Service wiring, passed through from the registry
"""

DEFAULT_MODE = "subdomain"
VALID_MODES = ("subdomain", "path")


def _norm_path(path):
    """Normalize to a leading-slash, no-trailing-slash path; '' -> '/'."""
    return "/" + (path or "").strip("/")


def _merge(base, override):
    """Overlay override onto base, ignoring keys whose override value is None."""
    out = dict(base or {})
    out.update({k: v for k, v in (override or {}).items() if v is not None})
    return out


def service_route(service, routing_mode, base_domain, registry, overrides=None):
    """Resolve one service's routing into a dict (see module docstring)."""
    if not registry or service not in registry:
        raise ValueError(
            "Unknown service '%s' — not in the service_routes registry (%s). "
            "Add it to configs/service_routes.yaml."
            % (service, sorted((registry or {}).keys()))
        )

    entry = _merge(registry[service], (overrides or {}).get(service))

    # A per-service override may pin its own mode (e.g. keep one service on a
    # subdomain while the cluster is otherwise path-based); else the cluster default.
    mode = entry.get("mode") or routing_mode or DEFAULT_MODE
    if mode not in VALID_MODES:
        raise ValueError(
            "Invalid routing mode '%s' for service '%s'. Valid: %s."
            % (mode, service, list(VALID_MODES))
        )

    subdomain = entry.get("subdomain") or service
    path = _norm_path(entry.get("path") or ("/" + service))

    if mode == "path":
        host = base_domain
        relative_path = path
        frontend_url = "https://%s%s" % (
            base_domain, "" if relative_path == "/" else relative_path)
    else:  # subdomain
        host = "%s.%s" % (subdomain, base_domain)
        relative_path = "/"
        frontend_url = "https://%s" % host

    # The apex listener serves the bare base domain; the wildcard '*.base'
    # listener serves subdomains and does not match the apex. Choose by host.
    section_name = "https-apex" if host == base_domain else "https"

    return {
        "service": service,
        "mode": mode,
        "host": host,
        "path": path,
        "relative_path": relative_path,
        "frontend_url": frontend_url,
        "section_name": section_name,
        "backend": entry.get("service") or service,
        "port": entry.get("port"),
        "namespace": entry.get("namespace"),
    }


class FilterModule(object):
    def filters(self):
        return {
            "service_route": service_route,
        }
