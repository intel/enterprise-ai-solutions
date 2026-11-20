# Observability: Grafana, Prometheus, Loki and Tempo

The `observability` component (on by default, `observability_enabled`) installs
Prometheus (metrics), Loki (logs), Tempo (traces) and Grafana, with the three
already added to Grafana as data sources. They run in the `monitoring` namespace.
This page covers reaching Grafana and signing in.

## Open Grafana

| Routing mode | URL |
|--------------|-----|
| `subdomain` (default) | `https://grafana.<base_domain_name>/` |
| `path` | `https://<base_domain_name>/grafana/` |

Grafana opens on its own login form. Nothing sends you to Keycloak first. If
the host name does not resolve, see [Without the gateway](#without-the-gateway).

## Sign in

### Local admin

Enter `admin` and its password on the login form. The password is generated
at the first install and kept in a Secret:

```bash
kubectl -n monitoring get secret grafana-admin-credentials -o jsonpath='{.data.password}' | base64 -d; echo
```

To choose it yourself, see [Admin passwords](../customize/configuration.md#admin-passwords).

### Sign in with Keycloak

1. Click **Sign in with Keycloak** on the login form.
2. Sign in with a `platform` realm account. The first one is `platform-admin`;
   its temporary password, which you must change at first login, is in a Secret:

   ```bash
   kubectl -n keycloak get secret platform-bootstrap-admin -o jsonpath='{.data.value}' | base64 -d; echo
   ```

Members of `platform-admins` are Grafana Admins; every other user is a Viewer.
The role is set again at each login, so leaving the group removes Admin.
Signing out of Grafana ends only Grafana's session, not the Keycloak one.

| Setting | Default | Effect |
|---------|---------|--------|
| `observability_grafana_sso` | `keycloak_enabled` | `false` removes the Keycloak button; only the local admin signs in |
| `observability_grafana_allowed_groups` | `[]` | Keycloak groups Grafana admits; empty admits every `platform` realm user |

On Kubernetes (the Helm stack) Grafana takes these settings on a fresh install
of `observability` only; `install` prints a notice while the running Grafana is
out of date. To apply them:

1. `./toolkit_manager.sh teardown observability --env <name>`
2. `./toolkit_manager.sh install observability --env <name>`

On OpenShift every `install` applies them.

## Without the gateway

When the gateway or DNS is down, reach Grafana through a port-forward and sign
in as the local admin:

```bash
# Kubernetes
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
# OpenShift
kubectl -n monitoring port-forward svc/grafana-platform-service 3000:3000
```

Then open `http://localhost:3000/`.

## Check it

`./toolkit_manager.sh validate observability --env <name>` checks that:

- `/login` shows the login form through the gateway, without a redirect;
- **Sign in with Keycloak** starts at the `platform` realm;
- Grafana's own pod reaches Keycloak with its client credentials. When this
  fails, the button ends in "Login failed: Failed to get token from provider".
  The check names the cause: rejected credentials, or no route to Keycloak
  (DNS, or a `no_proxy` that does not cover `.svc` and `.cluster.local`).
