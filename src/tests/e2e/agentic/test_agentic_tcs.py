#!/usr/bin/env python
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import os
import ssl
import urllib.error
import urllib.request

import allure
import kr8s
import pytest
import yaml

from agentic_support import configure_kubeconfig, run_cli, run_kubectl


AGENTIC_TARGET = os.environ.get("AGENTIC_INSTALLER_TARGET", "agent-toolkit")


def _run_installer(installer, env_name, *args):
    installer_args = list(args)
    if installer_args[0] == "init":
        if "--env" not in installer_args:
            installer_args.extend(("--env", env_name))
    elif "--env" not in installer_args:
        installer_args.extend(("--env", env_name))
    result = run_cli(installer, *installer_args, extra_env={"ES_ENV": env_name})
    assert result.returncode == 0, result.stdout
    return result.stdout


def _run_model_manager(model_manager, agentic_env, *args):
    result = run_cli(
        model_manager,
        *args,
        extra_env={"ES_ENV": agentic_env["name"]},
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


def _require_enabled(request, option, reason):
    if not request.config.getoption(option):
        pytest.skip(f"Pass {option} to enable this state-changing test: {reason}")


def _assert_ready(resource, description):
    ready = getattr(resource.status, "readyReplicas", None)
    if ready is None:
        ready = getattr(resource.status, "numberReady", 0)
    assert int(ready or 0) > 0, f"{description} has no ready replicas"


@allure.title("TC001 Decommission the agentic environment")
def test_tc001_decommission_agentic_environment(
    request, installer, agentic_env, agentic_target_available
):
    _require_enabled(
        request,
        "--run-agentic-lifecycle",
        "this tears down the agentic layer and its dependants",
    )
    _run_installer(
        installer,
        agentic_env["name"],
        "teardown",
        AGENTIC_TARGET,
        "--force",
        "--",
        "-e",
        "kuberay_enabled=true",
        "-e",
        "agentic_flowise_enabled=true",
    )


@allure.title("TC002 Install the agentic stack on a single node")
def test_tc002_install_agentic_stack_single_node(
    request, installer, agentic_env_name
):
    _require_enabled(
        request,
        "--run-agentic-lifecycle",
        "this installs the agentic layer and its dependencies",
    )
    _run_installer(installer, agentic_env_name, "init", AGENTIC_TARGET, "--env", agentic_env_name)
    _run_installer(installer, agentic_env_name, "install", AGENTIC_TARGET, "--force")
    configure_kubeconfig(agentic_env_name)
    nodes = list(kr8s.get("nodes"))
    expected_nodes = int(os.environ.get("AGENTIC_EXPECTED_NODE_COUNT", "1"))
    assert len(nodes) == expected_nodes, (
        f"Expected {expected_nodes} node(s) after setup, got {len(nodes)}"
    )


@allure.title("TC003 Verify inference endpoint access")
def test_tc003_inference_endpoint():
    endpoint = os.environ.get("AGENTIC_ENDPOINT_URL")
    assert endpoint, "Set AGENTIC_ENDPOINT_URL to the unified OpenAI-compatible endpoint"
    token = os.environ.get("AGENTIC_API_TOKEN", "")
    base_url = endpoint.rstrip("/")
    request = urllib.request.Request(f"{base_url}/v1/models")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    ca_bundle = os.environ.get("AGENTIC_CA_BUNDLE")
    tls_context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
    if os.environ.get("AGENTIC_INSECURE_TLS", "false").lower() == "true":
        tls_context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=30, context=tls_context) as response:
            assert response.status == 200
            models = json.load(response).get("data", [])
        assert models, "The unified inference endpoint returned no models"
        chat_request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": models[0]["id"],
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 8,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if token:
            chat_request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(chat_request, timeout=120, context=tls_context) as response:
            assert response.status == 200
            completion = json.load(response)
    except urllib.error.URLError as error:
        pytest.fail(f"Inference endpoint request failed: {error}")
    assert completion.get("choices"), completion


@allure.title("TC004 Add a catalog model with model-manager")
def test_tc004_add_model_command(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this deploys a model and consumes cluster resources",
    )
    alias = os.environ.get("AGENTIC_MODEL_ALIAS", "qwen3-0-6b")
    output = _run_model_manager(model_manager, agentic_env, "deploy", alias, "--wait")
    assert "ready" in output.lower() or "endpoint" in output.lower(), output


@allure.title("TC005 Remove a catalog model with model-manager")
def test_tc005_remove_model_command(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this removes a model deployment",
    )
    alias = os.environ.get("AGENTIC_MODEL_ALIAS", "qwen3-0-6b")
    _run_model_manager(model_manager, agentic_env, "undeploy", alias)


@allure.title("TC006 Add a model selected in the unified model catalog")
def test_tc006_add_model_from_catalog(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this deploys a model and consumes cluster resources",
    )
    catalog = agentic_env["dir"] / "models.yaml"
    assert catalog.is_file(), f"Unified model catalog not found: {catalog}"
    alias = os.environ.get("AGENTIC_MODEL_ALIAS", "qwen3-0-6b")
    config = yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}
    aliases = {model.get("name") for model in config.get("models", [])}
    assert alias in aliases, f"Model alias {alias!r} is not in {catalog}"
    _run_model_manager(model_manager, agentic_env, "deploy", alias, "--wait")


@allure.title("TC007 Remove a model selected in the unified model catalog")
def test_tc007_remove_model_from_catalog(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this removes a model deployment",
    )
    alias = os.environ.get("AGENTIC_MODEL_ALIAS", "qwen3-0-6b")
    _run_model_manager(model_manager, agentic_env, "undeploy", alias)


@allure.title("TC008 Deploy a model by Hugging Face repository ID")
def test_tc008_add_model_by_huggingface_id(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this downloads and deploys a Hugging Face model",
    )
    model_id = os.environ.get("AGENTIC_HF_MODEL_ID", "Qwen/Qwen3-0.6B")
    deployment_alias = os.environ.get("AGENTIC_HF_MODEL_ALIAS", "qwen3-0-6b-hf")
    output = _run_model_manager(
        model_manager,
        agentic_env,
        "deploy",
        "--id",
        model_id,
        "--name",
        deployment_alias,
        "--wait",
    )
    assert "ready" in output.lower() or "endpoint" in output.lower(), output


@allure.title("TC009 Remove an ad-hoc Hugging Face model")
def test_tc009_remove_huggingface_model(request, model_manager, agentic_env):
    _require_enabled(
        request,
        "--run-agentic-model-operations",
        "this removes an ad-hoc model deployment",
    )
    alias = os.environ.get("AGENTIC_HF_MODEL_ALIAS")
    assert alias, "Set AGENTIC_HF_MODEL_ALIAS to the deployed model name to remove"
    _run_model_manager(model_manager, agentic_env, "undeploy", alias)


@allure.title("TC010 List installed models")
def test_tc010_list_installed_models(agentic_env):
    catalog = agentic_env["dir"] / "models.yaml"
    assert catalog.is_file(), f"Unified model catalog not found: {catalog}"
    config = yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}
    catalog_names = {model.get("name") for model in config.get("models", [])}
    assert catalog_names, "The unified model catalog contains no model names"
    namespace = config.get("defaults", {}).get("namespace", "llm-inference")
    api_resources = run_kubectl(
        agentic_env,
        "api-resources",
        "--api-group=serving.kserve.io",
        "-o",
        "name",
    )
    resource_kinds = [line.strip() for line in api_resources.splitlines() if line.strip()]
    if not resource_kinds:
        pytest.skip("No KServe serving APIs are installed in this cluster yet.")

    listed_resources = 0
    for resource_kind in resource_kinds:
        result = json.loads(
            run_kubectl(
                agentic_env,
                "get",
                resource_kind,
                "-n",
                namespace,
                "-o",
                "json",
            )
        )
        assert isinstance(result.get("items"), list), (
            f"Kubernetes returned no item list for {resource_kind}: {result}"
        )
        listed_resources += len(result["items"])
    allure.attach(
        f"Catalog models: {sorted(catalog_names)}\n"
        f"Serving resource kinds: {resource_kinds}\n"
        f"Installed serving resources: {listed_resources}",
        name="Model listing",
        attachment_type=allure.attachment_type.TEXT,
    )


@allure.title("TC011 Preview model installation without changing the cluster")
def test_tc011_cancel_agentic_installation(model_manager, agentic_env):
    alias = os.environ.get("AGENTIC_MODEL_ALIAS", "qwen3-0-6b")
    output = _run_model_manager(model_manager, agentic_env, "deploy", alias, "--dry-run")
    assert "apiVersion:" in output or "apiVersion:" in output.replace("\\n", "\n"), output


@allure.title("TC012 Documented cancellation behavior for model removal")
def test_tc012_cancel_agentic_removal():
    pytest.skip(
        "The unified model-manager does not support undeploy --dry-run or an interactive "
        "cancel action; removal must remain skipped until a non-mutating preview is added."
    )


@allure.title("TC013 Resolve the unified platform deployment plan")
def test_tc013_minimal_platform_plan(installer, agentic_env):
    output = _run_installer(installer, agentic_env["name"], "install", "platform", "--", "--check")
    assert "platform" in output.lower()


@allure.title("TC014 Validate a multi-node agentic environment")
def test_tc014_agentic_environment_multinode(
    installer, agentic_env_name, agentic_target_available
):
    configure_kubeconfig(agentic_env_name)
    try:
        nodes = list(kr8s.get("nodes"))
    except Exception as error:
        pytest.skip(f"TC014 requires an accessible multi-node Kubernetes environment: {error}")
    if len(nodes) < 2:
        pytest.skip("TC014 requires a separately provisioned multi-node environment.")
    output = _run_installer(installer, agentic_env_name, "validate", AGENTIC_TARGET)
    assert "complete" in output.lower() or "validate" in output.lower(), output


@allure.title("TC015 Verify Redis memory backend")
def test_tc015_redis_backend(agentic_env):
    redis = list(kr8s.get("statefulsets", namespace="redis"))
    assert redis, "Redis StatefulSet not found in namespace redis"
    _assert_ready(redis[0], "Redis StatefulSet")
    secret_data = run_kubectl(
        agentic_env,
        "get",
        "secret",
        "redis-stack-server-credentials",
        "-n",
        "redis",
        "-o",
        "jsonpath={.data}",
    )
    assert "REDIS_PASSWORD" in secret_data and "REDIS_URL" in secret_data
    pong = run_kubectl(
        agentic_env,
        "exec",
        "-n",
        "redis",
        "redis-stack-server-0",
        "--",
        "sh",
        "-c",
        'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping',
    )
    assert pong == "PONG", f"Redis ping returned {pong!r}"


@allure.title("TC016 Verify PostgreSQL pgvector backend")
def test_tc016_pgvector_backend(agentic_env):
    pgvector = list(kr8s.get("statefulsets", namespace="pgvector"))
    assert pgvector, "pgvector StatefulSet not found in namespace pgvector"
    _assert_ready(pgvector[0], "pgvector StatefulSet")
    secrets = list(kr8s.get("secrets", namespace="pgvector"))
    assert any(secret.name == "pgvector-credentials" for secret in secrets)
    extension = run_kubectl(
        agentic_env,
        "exec",
        "-n",
        "pgvector",
        "pgvector-0",
        "--",
        "psql",
        "-U",
        "agentuser",
        "-d",
        "agentdb",
        "-tAc",
        "SELECT extname FROM pg_extension WHERE extname = 'vector'",
    )
    assert extension == "vector", f"Expected pgvector extension; query returned {extension!r}"


@allure.title("TC017 Verify Agent Sandbox controller and CRDs")
def test_tc017_agent_sandbox(agentic_env):
    namespace = agentic_env["agentic_config"].get(
        "agent_sandbox_namespace", "agent-sandbox-system"
    )
    deployments = {}
    for name in ("agent-sandbox-controller", "sandbox-router-deployment"):
        deployments[name] = json.loads(
            run_kubectl(
                agentic_env,
                "get",
                "deployment.apps",
                name,
                "-n",
                namespace,
                "-o",
                "json",
            )
        )
        ready = int(deployments[name].get("status", {}).get("readyReplicas", 0))
        assert ready > 0, f"Deployment {name} has no ready replicas in {namespace}"

    router_service = json.loads(
        run_kubectl(
            agentic_env,
            "get",
            "service",
            "sandbox-router-svc",
            "-n",
            namespace,
            "-o",
            "json",
        )
    )
    service_ports = {item["port"] for item in router_service.get("spec", {}).get("ports", [])}
    assert 8080 in service_ports, f"sandbox-router-svc exposes unexpected ports: {service_ports}"
    crds = run_kubectl(agentic_env, "get", "crd")
    assert "sandboxes.agents.x-k8s.io" in crds, "Sandbox CRD is not registered"
    assert "sandboxtemplates.extensions.agents.x-k8s.io" in crds, (
        "SandboxTemplate CRD is not registered"
    )
    template = json.loads(
        run_kubectl(
            agentic_env,
            "get",
            "sandboxtemplate",
            "python-sandbox-template",
            "-n",
            namespace,
            "-o",
            "json",
        )
    )
    assert template["metadata"]["name"] == "python-sandbox-template"
    health = run_kubectl(
        agentic_env,
        "get",
        "--raw",
        f"/api/v1/namespaces/{namespace}/services/sandbox-router-svc:8080/proxy/healthz",
    )
    assert '"status":"ok"' in health.replace(" ", "").lower(), health


@allure.title("TC018 Verify unified Prometheus, Grafana, Loki, Tempo and OTel")
def test_tc018_observability():
    namespaces = {resource.name for resource in kr8s.get("namespaces")}
    if "monitoring" in namespaces:
        namespace = "monitoring"
        deployments = {
            resource.name: resource
            for resource in kr8s.get("deployments", namespace=namespace)
        }
        for name in ("kube-prometheus-stack-operator", "kube-prometheus-stack-grafana"):
            assert name in deployments, f"Deployment {name} not found in {namespace}"
            _assert_ready(deployments[name], name)

        statefulsets = {
            resource.name: resource
            for resource in kr8s.get("statefulsets", namespace=namespace)
        }
        for name in ("loki", "tempo"):
            assert name in statefulsets, f"StatefulSet {name} not found in {namespace}"
            _assert_ready(statefulsets[name], name)

        daemonsets = {
            resource.name: resource
            for resource in kr8s.get("daemonsets", namespace=namespace)
        }
        for name in ("kube-prometheus-stack-prometheus-node-exporter", "opentelemetry-collector-agent"):
            assert name in daemonsets, f"DaemonSet {name} not found in {namespace}"
            ready = daemonsets[name].status.numberReady or 0
            desired = daemonsets[name].status.desiredNumberScheduled or 0
            assert desired > 0 and ready == desired, f"{name}: {ready}/{desired} pods ready"
        return

    namespace = "observability"
    assert namespace in namespaces, "Neither unified 'monitoring' nor Toolkit 'observability' namespace exists"
    pods = list(kr8s.get("pods", namespace=namespace))
    assert pods, f"No observability pods found in {namespace}"
    pod_names = [pod.name.lower() for pod in pods]
    for component in (
        "prometheus",
        "grafana",
        "alertmanager",
        "kube-state-metrics",
        "node-exporter",
        "loki",
        "otelcol",
    ):
        assert any(component in name for name in pod_names), (
            f"No {component} pod found in {namespace}: {pod_names}"
        )
    not_ready = [
        pod.name
        for pod in pods
        if getattr(pod.status, "phase", "") != "Running"
        or not all(
            getattr(container, "ready", False)
            for container in (getattr(pod.status, "containerStatuses", None) or [])
        )
    ]
    assert not not_ready, f"Observability pods are not Ready: {not_ready}"


@allure.title("TC019 Verify KubeRay operator and Ray cluster")
def test_tc019_kuberay(agentic_env):
    operators = list(kr8s.get("deployments", namespace="ray-system"))
    assert any("operator" in deployment.name for deployment in operators), (
        "KubeRay operator deployment not found in namespace ray-system"
    )
    operator = next(deployment for deployment in operators if "operator" in deployment.name)
    _assert_ready(operator, operator.name)
    clusters = list(kr8s.get("rayclusters.ray.io", namespace="ray-system"))
    assert clusters, "RayCluster not found in namespace ray-system"
    assert any(
        (cluster.status.state or "").lower() == "ready" for cluster in clusters
    ), f"No RayCluster is Ready: {[cluster.name for cluster in clusters]}"
    services = {service.name: service for service in kr8s.get("services", namespace="ray-system")}
    head_service = next(
        (service for name, service in services.items() if name.endswith("-head-svc")),
        None,
    )
    assert head_service is not None, "Ray head service is missing"
    ports = {port.port for port in head_service.spec.ports}
    assert {10001, 8265}.issubset(ports), f"Ray service ports are {ports}"
    head_pod = run_kubectl(
        agentic_env,
        "get",
        "pods",
        "-n",
        "ray-system",
        "-l",
        "ray.io/node-type=head",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    ray_status = run_kubectl(
        agentic_env,
        "exec",
        "-n",
        "ray-system",
        head_pod,
        "--",
        "ray",
        "status",
    )
    assert "Active:" in ray_status and "CPU" in ray_status, ray_status


@allure.title("TC020 Verify Flowise plugin")
def test_tc020_flowise_plugin():
    pytest.skip(
        "Flowise was removed from the unified Agent Toolkit migration; there is no Flowise component to validate."
    )


@allure.title("TC021 Documented worker-node add/remove coverage")
def test_tc021_worker_node_lifecycle():
    pytest.skip(
        "The unified installer does not expose the Toolkit's worker add/remove workflow; "
        "this lifecycle case needs a supported unified operation before it can run."
    )