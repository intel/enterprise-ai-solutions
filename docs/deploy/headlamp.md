# Headlamp and Platform Sign-in

Headlamp is the cluster web UI and the platform's landing page. People sign in
with their Keycloak account (realm `platform`), the same sign-in as Grafana,
Langfuse and the LiteLLM admin UI. This page covers turning it on, who gets in,
and the extra step on a cluster you bring yourself (BYO).

## Turn it on

In `env/<name>/global_config.yaml`:

```yaml
headlamp_enabled: true
```

Then install it on its own, or with the rest of the platform:

```bash
./es_auto_installer.sh install headlamp --env <name>
```

Headlamp is at `https://headlamp.<base_domain_name>/` (subdomain mode) or
`https://<base_domain_name>/headlamp/` (path mode, where the bare domain also
lands on it).

## How sign-in works

1. The gateway signs people in through Keycloak before any request reaches an
   app, and admits only the groups the app allows.
2. The app's own Keycloak sign-in then runs on the same Keycloak session, with
   no second password prompt.
3. Signing out of Headlamp, Grafana or the LiteLLM admin UI ends the gateway
   session and the Keycloak session. Signing out of Langfuse ends only its own
   session.

The first administrator is `platform-admin` (group `platform-admins`). Its
temporary password, which must be changed at first login, is in a Secret:

```bash
kubectl -n keycloak get secret platform-bootstrap-admin -o jsonpath='{.data.value}' | base64 -d
```

To make it enrol a one-time password (OTP) at first login too, set
`keycloak_platform_bootstrap_admin_mfa: true` before the first install.

## Who gets in

Two Keycloak groups come with the `platform` realm:

| Group | Headlamp (`apiserver` mode) | Grafana | LiteLLM admin UI |
|-------|-----------------------------|---------|------------------|
| `platform-admins` | `cluster-admin` | Admin | `proxy_admin` |
| `platform-viewers` | `view`: read-only, no Secrets | Viewer | not admitted |

Add people to a group in the Keycloak admin console (realm `platform`, Groups).
A signed-in user in neither group gets 403 at Headlamp and the LiteLLM admin
UI, and is a Viewer in Grafana and Langfuse.

| Setting | Default | Effect |
|---------|---------|--------|
| `headlamp_allowed_groups` | `[platform-admins, platform-viewers]` | Keycloak groups the gateway admits to Headlamp; everyone else gets 403 |
| `headlamp_auth_mode` | `apiserver` | `apiserver`: Headlamp calls Kubernetes as each person. `gateway`: one shared account for everyone |
| `headlamp_gateway_cluster_role` | `view` | `gateway` mode: what the shared account may do |

In `apiserver` mode Kubernetes sees each person as `oidc:<username>` in groups
`oidc:<group>`. To admit another group, bind it to a role first, then add it to
`headlamp_allowed_groups`. For example, a `team-a` group that may edit one
namespace:

```bash
kubectl -n team-a create rolebinding headlamp-team-a \
  --clusterrole=edit --group=oidc:team-a
```

## Bring your own Kubernetes (BYO)

With `existing_kubernetes` set, the installer does not change the API server. In
`apiserver` mode the API server must trust the `platform` realm, or sign-in
works but every Kubernetes call in Headlamp returns 401. So the Headlamp install
stops early, before creating anything, until you choose one:

**Option A: configure the API server (per-person access and audit).**

1. Install Headlamp. It stops with the issuer to trust and writes a ready-made
   configuration to `env/<name>/logs/headlamp-authentication-config.yaml`.
2. Copy that file to every control-plane node (for example to
   `/etc/kubernetes/authn/authentication-config.yaml`, mode `0600`) and start
   kube-apiserver with `--authentication-config=<that path>`. Mount the
   directory, not the file, so later changes reach the API server. This flag
   cannot be combined with the `--oidc-*` flags.
3. The file uses `apiserver.config.k8s.io/v1` (Kubernetes 1.34 and later). On
   1.30 to 1.33, change it to `apiserver.config.k8s.io/v1beta1`.
4. The API server fetches the realm's keys from the issuer URL, so the
   control-plane nodes must resolve the Keycloak host (`keycloak.<base>`, or
   `<base>` in path mode) to the gateway. The file already carries the gateway
   CA when the certificate is not publicly trusted.
5. Set `headlamp_byo_apiserver_trusted: true` in `global_config.yaml` and
   install Headlamp again.
6. Check: `./es_auto_installer.sh validate platform --env <name>` confirms the
   API server accepts a realm token.

On a managed cluster (EKS, AKS, GKE and others), use the provider's OIDC
identity-provider setting with the same values: issuer from the installer's
stop message, client ID `headlamp`, username claim `preferred_username`, groups claim
`groups`, and prefix `oidc:` for both.

**Option B: no API server change.** Set `headlamp_auth_mode: gateway`. Only
`headlamp_allowed_groups` get in, and everyone shares one ServiceAccount
(`headlamp_gateway_cluster_role`), so there is no per-person access or audit.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| 403 right after the Keycloak login | The account is not in `headlamp_allowed_groups` |
| Signed in, but every page shows 401 | The API server does not trust the realm (BYO: see above) |
| Signed in, but resources are forbidden | The person's groups have no RoleBinding (`apiserver` mode) |
