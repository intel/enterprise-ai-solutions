# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — resolves install/teardown target into ordered component list.

Registered as Jinja filters via FilterModule (bottom). Auto-loaded from
filter_plugins/ dir configured in ansible.cfg.

Usage in preflight.yaml:
    {{ components | resolve_components(layers, target=target, action=..., ...) }}

IMPORTANT: `enabled` values must be rendered before reaching this plugin.
preflight.yaml uses lookup('template', ...) (not 'file') for ext manifests so
Jinja expressions resolve to "True"/"False" strings; _enabled() coerces those.
"""

import warnings
from collections import deque

VALID_ACTIONS = ("install", "teardown", "validate")


def _enabled(component):
    val = component.get("enabled", True)
    if isinstance(val, bool):
        return val
    if val is None:
        return True
    return str(val).strip().lower() in ("true", "yes", "on", "1")


def _detect_cycles(graph):
    """Return list of cycles found in the adjacency graph, or [] if acyclic."""
    visited = set()
    path = set()
    cycles = []

    def _dfs(node, trail):
        if node in path:
            cycle = trail[trail.index(node):]
            cycles.append(cycle + [node])
            return
        if node in visited:
            return
        visited.add(node)
        path.add(node)
        trail.append(node)
        for neighbour in graph.get(node, []):
            _dfs(neighbour, trail)
        trail.pop()
        path.discard(node)

    for start in graph:
        if start not in visited:
            _dfs(start, [])
    return cycles


def _resolve_layers(layers, seed_layers, action, include_deps):
    """BFS from seed layers. Install walks deps downward, teardown walks rdeps upward."""
    if not include_deps:
        return list(seed_layers)

    deps = {layer["name"]: list(layer.get("depends_on") or []) for layer in layers}
    rdeps = {}
    for layer in layers:
        for parent in layer.get("depends_on") or []:
            rdeps.setdefault(parent, []).append(layer["name"])

    graph = rdeps if action == "teardown" else deps

    cycles = _detect_cycles(graph)
    if cycles:
        warnings.warn(
            "Cycle detected in layer dependencies: %s" %
            " | ".join(" -> ".join(c) for c in cycles)
        )

    seen = list(seed_layers)
    queue = deque(seed_layers)
    while queue:
        node = queue.popleft()
        for neighbour in graph.get(node, []):
            if neighbour not in seen:
                seen.append(neighbour)
                queue.append(neighbour)

    if action == "teardown":
        return seen
    # Install: drop disabled layers unless explicitly targeted.
    disabled = {layer["name"] for layer in layers if layer.get("enabled") is False}
    return [n for n in seen if n not in disabled or n in seed_layers]


def _toposort(keys, deps):
    """Lexicographically smallest topological order over keys given in rank order.

    Scans in rank order and emits the first key whose deps are all satisfied, so the
    result deviates from declared order only where a dependency forces it.
    Returns (ordered, unresolved); unresolved is non-empty only on a cycle.
    """
    keys = list(keys)
    deps = {k: set(deps.get(k) or ()) for k in keys}
    remaining, ordered, done = list(keys), [], set()
    while remaining:
        for i, key in enumerate(remaining):
            if deps[key] <= done:
                ordered.append(key)
                done.add(key)
                remaining.pop(i)
                break
        else:
            return ordered, remaining
    return ordered, []


def _layer_order(layers, resolved):
    """Order resolved layer names dependencies-first, declaration order breaking ties.

    Neither input carries execution order: _resolve_layers returns BFS order from the
    target, and the component pool arrives in manifest merge order (ext directory-name
    order). Ordering here is what decouples execution from where a layer is declared.
    """
    in_set = set(resolved)
    declared = []
    for layer in layers:
        if layer["name"] in in_set and layer["name"] not in declared:
            declared.append(layer["name"])
    # A component may name a layer nobody declared; keep it, ordered last.
    keys = declared + [n for n in resolved if n not in declared]

    deps = {layer["name"]: [d for d in (layer.get("depends_on") or []) if d in in_set]
            for layer in layers if layer["name"] in in_set}
    ordered, unresolved = _toposort(keys, deps)
    return ordered + unresolved  # cycle: _resolve_layers already warned


def _component_order(comps):
    """Order one layer's components dependencies-first, registry order breaking ties.

    Only same-layer depends_on constrains order — cross-layer deps are already
    satisfied by layer ordering. Keyed on position rather than name so duplicate
    names from overlapping manifests both survive.
    """
    positions = {}
    for i, comp in enumerate(comps):
        positions.setdefault(comp["name"], []).append(i)
    deps = {i: {p for d in (comp.get("depends_on") or [])
                for p in positions.get(d, ()) if p != i}
            for i, comp in enumerate(comps)}

    ordered, unresolved = _toposort(range(len(comps)), deps)
    if unresolved:
        warnings.warn(
            "Cycle detected in component dependencies within layer '%s': %s"
            % (comps[unresolved[0]].get("layer"),
               ", ".join(comps[i]["name"] for i in unresolved))
        )
    return [comps[i] for i in ordered + unresolved]


def resolve_components(components, layers, target, action="install",
                       include_deps=True, skip=""):
    """Resolve target → ordered list of component dicts to execute."""
    if action not in VALID_ACTIONS:
        raise ValueError(
            "Invalid action '%s'. Valid: %s" % (action, sorted(VALID_ACTIONS))
        )

    layer_names = {layer["name"] for layer in layers}
    by_name = {c["name"]: c for c in components}

    # Classify target: each part is either a known layer or a component name.
    parts = [t.strip() for t in target.split(",") if t.strip()]
    targeted_layers = [p for p in parts if p in layer_names]
    targeted_components = [p for p in parts if p not in layer_names]

    # Validate: a non-layer target must be a known component.
    for cname in targeted_components:
        if cname not in by_name:
            raise ValueError(
                "Unknown target '%s' — not a layer (%s) or component (%s)."
                % (cname, sorted(layer_names), sorted(by_name.keys()))
            )

    # Determine seed layers
    if targeted_layers:
        seed_layers = targeted_layers
    else:
        seed_layers = []
        for cname in targeted_components:
            comp = by_name[cname]
            if comp.get("layer") not in seed_layers:
                seed_layers.append(comp["layer"])

    resolved_layers = _resolve_layers(layers, seed_layers, action, include_deps)

    # Execution order is derived, not inherited: layers in dependency order, then
    # components in dependency order within each layer. Manifest merge order (ext
    # directory-name order) must not leak into it.
    pool = []
    for layer_name in _layer_order(layers, resolved_layers):
        pool.extend(_component_order(
            [c for c in components if c.get("layer") == layer_name]))

    # Component-level target: narrow pool to relevant components.
    # Install: target + forward deps (things it needs).
    # Teardown: target + reverse deps (things that depend on it).
    if targeted_components:
        wanted = set(targeted_components)
        if include_deps:
            queue = deque(targeted_components)
            while queue:
                cname = queue.popleft()
                if action == "teardown":
                    nxt = [c["name"] for c in components
                           if cname in (c.get("depends_on") or [])]
                else:
                    nxt = by_name.get(cname, {}).get("depends_on") or []
                for dep in nxt:
                    if dep not in wanted:
                        wanted.add(dep)
                        queue.append(dep)
        pool = [c for c in pool if c["name"] in wanted]

    # Skip filter
    # A skip entry may name a component or a whole layer: "skip erag" is the useful
    # request, and expanding it to erag's component list is the caller's job otherwise.
    skip_names = {s.strip() for s in (skip or "").split(",") if s.strip()}
    if skip_names:
        pool = [c for c in pool
                if c["name"] not in skip_names and c.get("layer") not in skip_names]

    # Enabled filter
    pool = [c for c in pool if _enabled(c)]

    if action == "teardown":
        pool = list(reversed(pool))
    return pool


def disabled_components(components, layers, target, action="install",
                        include_deps=True, skip=""):
    """Names that would run but are disabled — drives the 'Skipped' debug line."""
    kept = {c["name"] for c in resolve_components(
        components, layers, target, action, include_deps, skip)}
    forced = [dict(c, enabled=True) for c in components]
    candidates = resolve_components(forced, layers, target, action, include_deps, skip)
    return [c["name"] for c in candidates if c["name"] not in kept]


class FilterModule(object):
    def filters(self):
        return {
            "resolve_components": resolve_components,
            "disabled_components": disabled_components,
        }
