# Bare-Metal-Only Intel® TDX Attestation with an External Trustee

Intel® Trust Domain Extensions (Intel® TDX) uses hardware-backed memory encryption to isolate a virtual machine's memory from the host and hypervisor, helping protect data while it is in use. This guide covers remote attestation of an Intel® TDX guest using Confidential Containers Trustee, which runs on a separate, trusted Docker host reachable by the Kubernetes cluster over the network.

> **Deployment note:** The upstream Docker Compose setup is intended for evaluation and proof-of-concept use. For production, use a hardened Trustee deployment, enable HTTPS, restrict network access, pin image versions, and protect the generated keys and reference values.

## Prepare the Intel® TDX Guest VM

Prepare the host and guest OS for Intel® TDX by following Intel's [Host OS Setup](https://cc-enabling.trustedservices.intel.com/intel-tdx-enabling-guide/05/host_os_setup/) and [Guest OS Setup](https://cc-enabling.trustedservices.intel.com/intel-tdx-enabling-guide/06/guest_os_setup/) guides.

## Prepare Reference Values

Trustee's Attestation Service (AS) evaluates Intel® TDX evidence using its attestation policy, and its Reference Value Provider Service (RVPS) supplies expected measurements to that policy. The [`tdx-measure`](https://github.com/virtee/tdx-measure) tool calculates expected Intel® TDX measurements for a configured guest boot. Store its `RTMR1` and `RTMR2` output as RVPS reference-value files and have Rego query those values; do not hardcode the measurements in the policy. The tool does not configure Trustee or validate a live guest by itself.

### Build `tdx-measure`

On a trusted Linux administration host, install the Rust prerequisites described in the upstream README, then build the CLI:

```bash
git clone https://github.com/virtee/tdx-measure.git
cd tdx-measure
cargo build --release
```

The executable is created at `target/release/tdx-measure`. Generating ACPI tables for direct-boot metadata additionally requires Docker with Buildx; see the upstream [tdx-measure README](https://github.com/virtee/tdx-measure#readme) for prerequisites.

### Describe the target guest

Create a `metadata.json` that describes the **same guest boot configuration** that Trustee will attest. The metadata includes the platform settings (such as vCPU count, memory, and firmware) and boot-specific inputs:

- **Direct boot:** kernel, initrd, and kernel command line. ACPI tables can be generated with `--create-acpi-tables` for the distributions supported by the tool, or supplied from the target guest.
- **Indirect boot:** the guest QCOW2 image, kernel command line, and Secure Boot inputs such as MOK and SBAT variables. Firmware and boot-variable files should come from a guest configured identically to the target.

See the upstream [tdx-measure README](https://github.com/virtee/tdx-measure#readme) for the exact schema and input-file sources. Differences in firmware, boot chain, kernel/initrd, command line, or platform configuration can produce different register measurements.

Calculate and save the expected measurements:

```bash
target/release/tdx-measure --json --json-file measurements.json metadata.json
```

The JSON reports expected `MRTD` and `RTMR0`–`RTMR2` values. Confirm that the selected boot method and tool version support your guest before relying on the output; the project README describes its current validation scope.

To calculate only the runtime registers, use `--runtime-only`. It computes `RTMR1` and `RTMR2`; the other registers are omitted from the JSON:

```bash
target/release/tdx-measure --runtime-only --json \
  --json-file runtime-measurements.json metadata.json
```

The output has this shape (values below are placeholders; use the values generated for your own guest):

```json
{
  "rtmr1": "<96 hexadecimal characters>",
  "rtmr2": "<96 hexadecimal characters>"
}
```

The upstream tool warns that `--runtime-only` assumes a VM memory size greater than 2.75 GB. Review the [tdx-measure README](https://github.com/virtee/tdx-measure#readme) and confirm that this mode's assumptions match the target before using the measurements in the Trustee policy.

## Install Cluster External Trustee

### 1. Prepare the Trustee host

Use a trusted Linux host with Docker Engine and the Docker Compose plugin installed. It must have a stable IP address or DNS name reachable from the Kubernetes nodes and Pods.

Clone the official Trustee repository, customize its default policy, then build and start its Compose services:

```bash
git clone https://github.com/confidential-containers/trustee.git
cd trustee
```

To give the KBS container outbound proxy access, edit the existing `kbs.environment` list in `docker-compose.yml` and add the proxy variables. Use the proxy address reachable from the Trustee host, and keep the existing environment entries:

```yaml
services:
  kbs:
    environment:
      - RUST_LOG=debug
      - HTTP_PROXY=http://<proxy-host>:<proxy-port>
      - HTTPS_PROXY=http://<proxy-host>:<proxy-port>
      - NO_PROXY=localhost,127.0.0.1,as,rvps
```

Keep the Compose service names used for internal Trustee traffic in `NO_PROXY` so calls to the Attestation Service and RVPS bypass the outbound proxy. If the stack is already running, recreate KBS to apply the environment change with `docker compose up -d kbs`; rebuilding the image is not needed for proxy-variable changes.

Before building Trustee, patch its existing CPU attestation policy at `attestation-service/src/ear_token/ear_default_policy_cpu.rego`. The commands below write the patch to a file, check that it applies, and then apply it. It removes the `mr_td` and `xfam` RVPS lookups from the standard bare-metal Intel® TDX policy branch while leaving the existing `RTMR1` and `RTMR2` lookups and all other policy checks unchanged. Trustee's verifier still validates quote authenticity.

Before starting Compose, create the two reference-value files under `kbs/data/reference-values/reference_value/`. The Compose configuration mounts `kbs/data/reference-values` into the RVPS container's reference-value storage. This layout was verified on the `demo_gpu` host at `/var/tmp/trustee/kbs/data/reference-values/reference_value/`. Each file uses the Trustee JSON format: `version`, `name`, `expiration` (a future UTC RFC 3339 timestamp), and `value` (an array containing the 96-character hexadecimal measurement from `runtime-measurements.json`).

Create the directory from the Trustee checkout:

```bash
mkdir -p kbs/data/reference-values/reference_value
```

Create `kbs/data/reference-values/reference_value/rtmr_1`:

```json
{
  "version": "0.1.0",
  "name": "rtmr_1",
  "expiration": "<future UTC timestamp, for example 2027-12-31T23:59:59Z>",
  "value": ["<96-character rtmr1 value from runtime-measurements.json>"]
}
```

Create `kbs/data/reference-values/reference_value/rtmr_2`:

```json
{
  "version": "0.1.0",
  "name": "rtmr_2",
  "expiration": "<future UTC timestamp, for example 2027-12-31T23:59:59Z>",
  "value": ["<96-character rtmr2 value from runtime-measurements.json>"]
}
```

Keep each `name` identical to its filename and renew the references before they expire.

```bash
cat > /tmp/tdx-policy.patch <<'PATCH'
diff --git a/attestation-service/src/ear_token/ear_default_policy_cpu.rego b/attestation-service/src/ear_token/ear_default_policy_cpu.rego
--- a/attestation-service/src/ear_token/ear_default_policy_cpu.rego
+++ b/attestation-service/src/ear_token/ear_default_policy_cpu.rego
@@ -180,4 +180,3 @@
 	# Check OVMF code hash
-	input.tdx.quote.body.mr_td in query_reference_value("mr_td")
 
 	# Check TCB status (covers quote.body.tcb_svn claim check)
@@ -219,3 +218,2 @@ configuration := 2 if {
 	input.tdx.td_attributes.debug == false
-	input.tdx.quote.body.xfam in query_reference_value("xfam")
 }
PATCH

git apply --check /tmp/tdx-policy.patch && git apply /tmp/tdx-policy.patch
```

This patch preserves the existing Intel® TDX runtime-measurement checks and other policy behavior. Build and start Trustee after applying it:

```bash
docker compose up -d --build
```

Building is required for this policy change: the AS binary embeds `ear_default_policy_cpu.rego` at compile time. The command above builds and starts the full Compose stack for the initial deployment. For a later policy-only update, rebuild and recreate just AS with `docker compose up -d --build as`.

The Compose file builds and starts the KBS, Attestation Service (AS), Reference Value Provider Service (RVPS), and setup service from the repository's Dockerfiles. Check startup and logs:

```bash
docker compose ps
docker compose logs --tail=100 kbs as rvps
```

The default Compose configuration publishes KBS on TCP `8080`, AS on `50004`, and RVPS on `50003`. Only the KBS endpoint needs to be reachable from the cluster. Restrict TCP `8080` to cluster/node networks, and bind the AS and RVPS host ports to loopback or remove their host port mappings unless external access is required. Docker-published ports may bypass host firewall rules, so verify the effective network policy. The Compose setup generates an admin keypair under `kbs/config/`; protect that directory and do not commit its private key.

To stop the services, run `docker compose down` from the Trustee checkout. Persistent configuration and data are stored in the mounted `kbs/config/` and `kbs/data/` directories; back them up and restrict access.

### 2. Configure this deployment

Set `kbs_address` in the target environment's `global_config.yaml` to the Trustee host's address, not `localhost`:

```yaml
kbs_address: "http://<trustee-host-or-ip>:8080"
```

For example, if the Trustee host is `192.168.10.1`:

```yaml
kbs_address: "http://192.168.10.1:8080"
```

With HTTPS enabled on Trustee, use its `https://` URL and configure the attestation workload to trust the issuing CA; do not disable certificate verification in production.

Then deploy the environment with the normal installer command, for example:

```bash
./es_auto_installer.sh install --all --env <environment>
```

Ensure `tdx_enabled: true` is set for that environment. The Intel® TDX attestation component reads `kbs_address` and contacts Trustee from its Kubernetes Pod.

### 3. Check connectivity

From a machine with access to the Kubernetes cluster, check that the KBS host returns an HTTP response:

```bash
kubectl run kbs-connectivity --rm -i --restart=Never \
  --image=curlimages/curl -- \
  curl --silent --show-error --max-time 5 \
    --output /dev/null --write-out 'HTTP %{http_code}\n' \
    http://<trustee-host-or-ip>:8080/
```

An HTTP response (including `404` for an unhandled root path) confirms network reachability; it does **not** prove that attestation succeeded. Verify attestation by running the Intel® TDX installation and checking its Ansible result and the attestation Pod logs.

## Security considerations

- Trustee is part of the trusted computing base for deciding whether to accept a guest. Run it on a trusted, patched host and protect its KBS, AS, and RVPS configuration and data.
- The upstream [Docker installation guide](https://confidentialcontainers.org/docs/attestation/installation/docker/) recommends Docker Compose for quick evaluation. Use the upstream [installation overview](https://confidentialcontainers.org/docs/attestation/installation/) to choose an appropriate production deployment method.
- The CoCo [guest attestation API](https://confidentialcontainers.org/docs/features/get-attestation/) has separate security considerations. This guide configures the Trustee KBS endpoint used by this deployment; it does not replace Trustee verification with a direct evidence fetch.
