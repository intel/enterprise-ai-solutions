# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — resolves a service's gateway routing from the shared
service_routes registry, the cluster's routing_mode, and its base_domain_name,
and builds the gateway objects that publish it.

Registered as Jinja filters via FilterModule (bottom). Auto-loaded from the
filter_plugins/ dir configured in ansible.cfg.

This is the single place the subdomain-vs-path (apex) derivation and the route
hardening live, so every role publishes consistently and adding a service is a
one-line registry entry.

Usage in a role's defaults:
    my_route: >-
      {{ 'keycloak' | service_route(routing_mode | default('subdomain'),
                                    base_domain_name | default('inference-example.com'),
                                    service_routes | default({}),
                                    service_route_overrides | default({})) }}

service_route returns a dict:
    host           host the HTTPRoute matches on (apex in path mode, else subdomain)
    relative_path  path the service must serve under ('/keycloak' | '/')
    frontend_url   absolute URL the service advertises (issuer/redirects/assets)
    section_name   gateway listener to attach to ('https-apex' for the apex host,
                   else 'https' — the wildcard '*.base' listener does NOT match apex)
    mode           effective mode after per-service override ('path' | 'subdomain')
    prefix         how the path prefix reaches the backend (passthrough|strip|none)
    published      False when the service cannot be served in this mode
    cookies        cookie names the service owns ('name*' = prefix match)
    path           logical path from the registry (kept for reference)
    backend/port/namespace  backend Service wiring, passed through from the registry

gateway_httproute builds the HTTPRoute for a resolved route (see its docstring).

"""

import copy
import re

DEFAULT_MODE = "subdomain"
VALID_MODES = ("subdomain", "path")
# passthrough: the app serves under its prefix (UIs; the gateway cannot rewrite
#              bodies, redirects or cookie paths). strip: the gateway removes the
#              prefix (APIs serving at '/'). none: no sub-path support.
VALID_PREFIX = ("passthrough", "strip", "none")
DEFAULT_PREFIX = "passthrough"

_COOKIE_RE = re.compile(r"^[A-Za-z0-9_.-]+\*?$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")

# OWASP secure-headers baseline, set on every published route. Framing and CSP
# stay with each app, which knows what it embeds.
HSTS = "max-age=31536000; includeSubDomains"
SECURITY_HEADERS = (
    ("Strict-Transport-Security", HSTS),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)


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

    prefix = entry.get("prefix") or DEFAULT_PREFIX
    if prefix not in VALID_PREFIX:
        raise ValueError(
            "Invalid prefix '%s' for service '%s'. Valid: %s."
            % (prefix, service, list(VALID_PREFIX))
        )

    cookies = list(entry.get("cookies") or [])
    bad = [c for c in cookies if not _COOKIE_RE.match(str(c))]
    if bad:
        raise ValueError("Invalid cookie name(s) %s for service '%s'." % (bad, service))

    subdomain = entry.get("subdomain") or service
    path = _norm_path(entry.get("path") or ("/" + service))
    if not _PATH_RE.match(path):
        raise ValueError("Invalid path '%s' for service '%s'." % (path, service))

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
        "prefix": prefix,
        # A prefix-less app cannot live under /<path>; it stays internal there.
        "published": not (prefix == "none" and relative_path != "/"),
        "cookies": cookies,
        "backend": entry.get("service") or service,
        "port": entry.get("port"),
        "namespace": entry.get("namespace"),
    }


def _join(prefix, path):
    """'/svc' + '/v1/x' -> '/svc/v1/x'; '/svc' + '/' -> '/svc'; '' root -> path."""
    if prefix in ("", "/"):
        return path
    return prefix if path == "/" else prefix + path


def gateway_httproute(route, name, rules=None, gateway_name="eg-gateway",
                      gateway_namespace="envoy-gateway-system", headers=None,
                      backend_namespace=None):
    """Build a hardened HTTPRoute for a resolved service_route.

    rules: list of {matches: [{type: Exact|PathPrefix, value: <app path>}],
                    filters: [...], backend: false}; default one PathPrefix '/'.
      Shorthand: exact: [paths] / prefix: [paths] in place of matches.
      Paths are as the APP serves them. In path mode they are published under
      the route's prefix; with prefix 'strip' each match is rewritten back to
      the app path (one rule per match, as each needs its own rewrite).
      backend: false = no backendRefs (filter-only rules, e.g. a 404).
    headers: extra response headers to set, merged over the baseline.
    backend_namespace: Service namespace when it differs from the route's
      (needs a ReferenceGrant there).

    Always attaches to the HTTPS listener only (sectionName) so nothing is served
    on plaintext :80, and sets the security-header baseline on every rule.
    """
    if not route.get("published", True):
        raise ValueError("Service '%s' is not published in %s mode."
                         % (route.get("service"), route.get("mode")))
    prefix = "" if route["relative_path"] == "/" else route["relative_path"]
    strip = route.get("prefix") == "strip" and prefix
    hdrs = dict(SECURITY_HEADERS)
    hdrs.update(headers or {})
    header_filter = {
        "type": "ResponseHeaderModifier",
        "responseHeaderModifier": {
            "set": [{"name": k, "value": v} for k, v in hdrs.items()]},
    }
    backend = [{"name": route["backend"], "port": int(route["port"])}]
    if backend_namespace and backend_namespace != route["namespace"]:
        backend[0]["namespace"] = backend_namespace

    out = []
    for rule in rules or [{"matches": [{"type": "PathPrefix", "value": "/"}]}]:
        to_backend = rule.get("backend", True)
        extra = list(rule.get("filters") or [])
        matches = (list(rule.get("matches") or [])
                   + [{"type": "Exact", "value": v} for v in rule.get("exact") or []]
                   + [{"type": "PathPrefix", "value": v} for v in rule.get("prefix") or []])
        if not matches:
            raise ValueError("HTTPRoute '%s': rule without matches." % name)
        groups = [[m] for m in matches] if strip and to_backend else [matches]
        for group in groups:
            filters = []
            if strip and to_backend:
                m = group[0]
                if m["type"] == "Exact":
                    path = {"type": "ReplaceFullPath", "replaceFullPath": m["value"]}
                else:
                    path = {"type": "ReplacePrefixMatch",
                            "replacePrefixMatch": m["value"]}
                filters.append({"type": "URLRewrite", "urlRewrite": {"path": path}})
            filters += extra
            if to_backend:
                filters.append(copy.deepcopy(header_filter))
            r = {
                "matches": [{"path": {"type": m["type"],
                                      "value": _join(prefix, m["value"])}}
                            for m in group],
                "filters": filters,
            }
            if to_backend:
                r["backendRefs"] = copy.deepcopy(backend)
            out.append(r)

    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {"name": name, "namespace": route["namespace"]},
        "spec": {
            "parentRefs": [{"name": gateway_name, "namespace": gateway_namespace,
                            "sectionName": route["section_name"]}],
            "hostnames": [route["host"]],
            "rules": out,
        },
    }


class FilterModule(object):
    def filters(self):
        return {
            "service_route": service_route,
            "gateway_httproute": gateway_httproute,
        }
