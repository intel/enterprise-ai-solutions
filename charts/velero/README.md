# velero

Wrapper chart for the backup component. It vendors two upstream charts and adds
nothing of its own beyond the values that wire them together:

| Dependency | Version | Role |
|---|---|---|
| `velero` | 12.1.0 (appVersion 1.18.1) | The Velero server, its plugins and its CRDs |
| `seaweedfs` | 4.0.407 | The in-cluster object store, deployed only when `seaweedfs.enabled` |

The chart is not installed directly. `roles/velero` renders its values, resolves
the store's credentials and applies the CRDs, and the component is driven from
the installer:

```bash
./es_auto_installer.sh install velero --env <env> --only
./es_auto_installer.sh validate velero --env <env>
```

Every variable the component takes is declared in `roles/velero/defaults/main.yaml`.
The profile contract the backup engine consumes is documented in `configs/backup.yaml`;
taking and restoring a backup is driven by the solution that owns a profile, and
documented there.
