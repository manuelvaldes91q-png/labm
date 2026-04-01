#!/usr/bin/env python3
"""
FastAPI server for MikroTik CHR network simulation.
Manages Docker containers and L2 veth connectivity.
"""

import docker
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import network_manager as nm

app = FastAPI(title="MikroTik CHR Network Manager", version="1.0.0")

docker_client = docker.from_env()

CHR_IMAGE = "evilfreelancer/docker-routeros:latest"

NODE_IMAGES = {
    "router": CHR_IMAGE,
    "switch": CHR_IMAGE,
    "pc": "alpine:latest",
}

NODE_CMD = {
    "router": None,
    "switch": None,
    "pc": ["sleep", "infinity"],
}


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


class ConnectionResponse(BaseModel):
    node_a: str
    interface_a: str
    node_b: str
    interface_b: str


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


# ── Endpoints ────────────────────────────────────────────────────────


@app.post("/nodes", response_model=NodeResponse, status_code=201)
def create_node(req: CreateNodeRequest):
    """Create a new network node (router, switch, or pc) as a Docker container."""
    if _get_container_status(req.name) is not None:
        raise HTTPException(status_code=409, detail=f"Container '{req.name}' already exists")

    image = NODE_IMAGES[req.node_type]
    cmd = NODE_CMD[req.node_type]

    # Pull image if not present locally
    try:
        docker_client.images.get(image)
    except docker.errors.ImageNotFound:
        docker_client.images.pull(image)

    container = docker_client.containers.run(
        image,
        command=cmd,
        name=req.name,
        detach=True,
        privileged=True,
        network_mode="none",
        labels={"chr_node_type": req.node_type},
    )

    return NodeResponse(
        name=req.name,
        node_type=req.node_type,
        image=image,
        status=container.status,
    )


@app.get("/nodes", response_model=list[NodeResponse])
def list_nodes():
    """List all managed network nodes (containers with chr_node_type label)."""
    containers = docker_client.containers.list(
        all=True,
        filters={"label": "chr_node_type"},
    )
    return [
        NodeResponse(
            name=c.name,
            node_type=c.labels.get("chr_node_type", "unknown"),
            image=c.image.tags[0] if c.image.tags else c.image.short_id,
            status=c.status,
        )
        for c in containers
    ]


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

    # Clean up any veth pairs associated with this node
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
