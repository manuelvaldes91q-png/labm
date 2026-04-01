#!/usr/bin/env python3
"""
FastAPI server for MikroTik CHR network simulation.
Manages Docker containers, L2 veth connectivity, WAN/NAT, and Winbox access.
"""

import socket

import docker
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import network_manager as nm

app = FastAPI(title="MikroTik CHR Network Manager", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

docker_client = docker.from_env()

CHR_IMAGE = "evilfreelancer/docker-routeros:latest"
PC_IMAGE = "alpine:latest"

# Track next available Winbox port per host
_winbox_counter = {"next": nm.WINBOX_PORT_BASE}


# ── Schemas ──────────────────────────────────────────────────────────


class CreateNodeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    node_type: str = Field(..., pattern=r"^(router|switch|pc)$")


class ConnectRequest(BaseModel):
    node_a: str
    node_b: str
    index_a: int = Field(..., ge=1, le=32)
    index_b: int = Field(..., ge=1, le=32)


class NodeResponse(BaseModel):
    name: str
    node_type: str
    image: str
    status: str
    wan_ip: str | None = None
    winbox_port: int | None = None


class ConnectionResponse(BaseModel):
    node_a: str
    interface_a: str
    node_b: str
    interface_b: str


class WanStatusResponse(BaseModel):
    bridge: str
    status: str
    host_interface: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


def _get_container_status(name: str) -> str | None:
    try:
        c = docker_client.containers.get(name)
        return c.status
    except docker.errors.NotFound:
        return None


def _raise_not_found(name: str) -> None:
    raise HTTPException(status_code=404, detail=f"Container '{name}' not found")


def _raise_not_running(name: str) -> None:
    raise HTTPException(status_code=409, detail=f"Container '{name}' is not running")


def _get_free_port(start: int) -> int:
    for port in range(start, start + 1000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port available")


def _node_response(container: docker.models.containers.Container) -> NodeResponse:
    labels = container.labels or {}
    return NodeResponse(
        name=container.name,
        node_type=labels.get("chr_node_type", "unknown"),
        image=container.image.tags[0] if container.image.tags else container.image.short_id,
        status=container.status,
        wan_ip=labels.get("chr_wan_ip"),
        winbox_port=int(p) if (p := labels.get("chr_winbox_port")) else None,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@app.post("/wan/setup", response_model=WanStatusResponse)
def setup_wan():
    """Create the WAN bridge with NAT for internet access."""
    try:
        result = nm.setup_wan_bridge()
    except nm.VethCommandError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return WanStatusResponse(
        bridge=result["bridge"],
        status=result["status"],
        host_interface=result.get("host_iface"),
    )


@app.get("/wan/status", response_model=WanStatusResponse)
def wan_status():
    """Check if WAN bridge exists."""
    exists = nm.veth_exists(nm.WAN_BRIDGE)
    return WanStatusResponse(
        bridge=nm.WAN_BRIDGE,
        status="active" if exists else "not_setup",
        host_interface=nm.get_host_default_iface() if exists else None,
    )


@app.post("/nodes", response_model=NodeResponse, status_code=201)
def create_node(req: CreateNodeRequest):
    """Create a network node. Routers/switches get Winbox access, all get WAN."""
    if _get_container_status(req.name) is not None:
        raise HTTPException(status_code=409, detail=f"Container '{req.name}' already exists")

    if req.node_type in ("router", "switch"):
        image = CHR_IMAGE
        cmd = None
        winbox_port = _get_free_port(_winbox_counter["next"])
        _winbox_counter["next"] = winbox_port + 1
        ports = {"8291/tcp": winbox_port}
    else:
        image = PC_IMAGE
        cmd = ["sh", "-c", "apk add --no-cache iputils curl iproute2 >/dev/null 2>&1; sleep infinity"]
        winbox_port = None
        ports = {}

    try:
        docker_client.images.get(image)
    except docker.errors.ImageNotFound:
        docker_client.images.pull(image)

    labels = {"chr_node_type": req.node_type}
    if winbox_port:
        labels["chr_winbox_port"] = str(winbox_port)

    container = docker_client.containers.run(
        image,
        command=cmd,
        name=req.name,
        detach=True,
        privileged=True,
        network_mode="none",
        ports=ports,
        labels=labels,
    )

    try:
        nm.setup_wan_bridge()
    except nm.VethCommandError:
        pass

    wan_ip = None
    try:
        wan_result = nm.connect_wan(req.name)
        wan_ip = wan_result["ip"]
    except (nm.VethCommandError, nm.ContainerNotFoundError, nm.ContainerNotRunningError):
        pass

    container.reload()

    return NodeResponse(
        name=req.name,
        node_type=req.node_type,
        image=image,
        status=container.status,
        wan_ip=wan_ip,
        winbox_port=winbox_port,
    )


@app.get("/nodes", response_model=list[NodeResponse])
def list_nodes():
    """List all managed network nodes."""
    containers = docker_client.containers.list(
        all=True,
        filters={"label": "chr_node_type"},
    )
    return [_node_response(c) for c in containers]


@app.get("/nodes/{name}", response_model=NodeResponse)
def get_node(name: str):
    """Get details for a specific node."""
    try:
        container = docker_client.containers.get(name)
    except docker.errors.NotFound:
        _raise_not_found(name)
    return _node_response(container)


@app.post("/connections", response_model=ConnectionResponse, status_code=201)
def connect_nodes(req: ConnectRequest):
    """Create a direct L2 veth link between two running nodes."""
    for name in (req.node_a, req.node_b):
        status = _get_container_status(name)
        if status is None:
            _raise_not_found(name)
        if status != "running":
            _raise_not_running(name)

    try:
        result = nm.connect_pair(req.node_a, req.index_a, req.node_b, req.index_b)
    except nm.ContainerNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except nm.ContainerNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except nm.VethCommandError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ConnectionResponse(
        node_a=result["container_a"],
        interface_a=result["interface_a"],
        node_b=result["container_b"],
        interface_b=result["interface_b"],
    )


@app.get("/connections")
def list_connections():
    """List active veth pairs by scanning the host network namespace."""
    result = nm.run(["ip", "-o", "link", "show", "type", "veth"], check=False)
    if result.returncode != 0:
        return []
    connections = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(": ")
        if len(parts) >= 2:
            iface_name = parts[1].split("@")[0]
            if iface_name.startswith("veth-"):
                connections.append({"host_interface": iface_name})
    return connections


@app.delete("/connections/{host_veth}")
def delete_connection(host_veth: str):
    """Delete a veth pair by its host-side name."""
    nm.cleanup_veth_pair(host_veth)
    return {"deleted": host_veth}


@app.delete("/nodes/{name}")
def delete_node(name: str):
    """Stop and remove a network node container."""
    try:
        container = docker_client.containers.get(name)
    except docker.errors.NotFound:
        _raise_not_found(name)

    result = nm.run(["ip", "-o", "link", "show", "type", "veth"], check=False)
    if result.returncode == 0:
        for line in result.stdout.strip().splitlines():
            parts = line.split(": ")
            if len(parts) >= 2:
                iface = parts[1].split("@")[0]
                if iface.startswith(f"veth-{name}-"):
                    nm.cleanup_veth_pair(iface)

    container.remove(force=True)
    return {"deleted": name}
