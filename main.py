#!/usr/bin/env python3
"""
FastAPI server for MikroTik CHR network simulation.
Manages Docker containers, L2 veth connectivity, WAN/NAT, Winbox access,
and full RouterOS configuration (VLANs, OSPF, BGP, DHCP, firewall).
"""

import socket
import asyncio
import logging

import docker
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import network_manager as nm
import routeros_config as ros

logger = logging.getLogger(__name__)

app = FastAPI(title="MikroTik CHR Network Manager", version="2.0.0")

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

_winbox_counter = {"next": nm.WINBOX_PORT_BASE}
_ros_boot_tasks: dict[str, asyncio.Task] = {}


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
    ros_booted: bool = False


class ConnectionResponse(BaseModel):
    node_a: str
    interface_a: str
    node_b: str
    interface_b: str


class WanStatusResponse(BaseModel):
    bridge: str
    status: str
    host_interface: str | None = None


# Bridge / VLAN
class BridgeRequest(BaseModel):
    node: str
    name: str
    vlan_filtering: bool = False


class BridgePortRequest(BaseModel):
    node: str
    bridge: str
    interface: str
    pvid: int | None = None


class VlanRequest(BaseModel):
    node: str
    interface: str
    vlan_id: int = Field(..., ge=1, le=4094)
    name: str | None = None


class BridgeVlanRequest(BaseModel):
    node: str
    bridge: str
    vlan_ids: str
    tagged: str | None = None
    untagged: str | None = None


# IP
class IpAddressRequest(BaseModel):
    node: str
    address: str
    interface: str


class RouteRequest(BaseModel):
    node: str
    dst: str
    gateway: str
    distance: int = 1


class DhcpServerRequest(BaseModel):
    node: str
    interface: str
    pool_name: str
    network: str
    gateway: str
    dns: str = "8.8.8.8"


# OSPF
class OspfNetwork(BaseModel):
    network: str
    area: str = "backbone"


class OspfRequest(BaseModel):
    node: str
    router_id: str
    networks: list[OspfNetwork]


# BGP
class BgpNeighbor(BaseModel):
    name: str
    remote_address: str
    remote_as: int


class BgpRequest(BaseModel):
    node: str
    as_number: int
    router_id: str
    neighbors: list[BgpNeighbor] = []
    networks: list[str] = []


# Firewall
class FirewallRuleRequest(BaseModel):
    node: str
    chain: str
    action: str = "accept"
    params: dict = {}


class NatRuleRequest(BaseModel):
    node: str
    chain: str = "srcnat"
    action: str = "masquerade"
    params: dict = {}


# System
class IdentityRequest(BaseModel):
    node: str
    name: str


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


def _get_ros_ip(node_name: str) -> str | None:
    container = docker_client.containers.get(node_name)
    labels = container.labels or {}
    return labels.get("chr_wan_ip")


def _require_ros_ip(node_name: str) -> str:
    ip = _get_ros_ip(node_name)
    if not ip:
        raise HTTPException(
            status_code=400,
            detail=f"Node '{node_name}' has no WAN IP — RouterOS not reachable",
        )
    return ip


def _node_response(container: docker.models.containers.Container) -> NodeResponse:
    labels = container.labels or {}
    wan_ip = labels.get("chr_wan_ip")
    ros_booted = labels.get("chr_ros_booted") == "true"
    return NodeResponse(
        name=container.name,
        node_type=labels.get("chr_node_type", "unknown"),
        image=container.image.tags[0] if container.image.tags else container.image.short_id,
        status=container.status,
        wan_ip=wan_ip,
        winbox_port=int(p) if (p := labels.get("chr_winbox_port")) else None,
        ros_booted=ros_booted,
    )


async def _background_ros_boot(node_name: str, wan_ip: str) -> None:
    try:
        result = await asyncio.to_thread(ros.auto_configure, wan_ip)
        logger.info("Auto-configured %s: %s", node_name, result)
        try:
            container = docker_client.containers.get(node_name)
            labels = dict(container.labels or {})
            labels["chr_ros_booted"] = "true"
        except docker.errors.NotFound:
            pass
    except ros.RouterOSConnectionError as e:
        logger.error("RouterOS boot failed for %s: %s", node_name, e)
    except Exception as e:
        logger.error("Unexpected error during ROS boot for %s: %s", node_name, e)


# ── WAN ──────────────────────────────────────────────────────────────


@app.post("/wan/setup", response_model=WanStatusResponse)
def setup_wan():
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
    exists = nm.veth_exists(nm.WAN_BRIDGE)
    return WanStatusResponse(
        bridge=nm.WAN_BRIDGE,
        status="active" if exists else "not_setup",
        host_interface=nm.get_host_default_iface() if exists else None,
    )


# ── Nodes ────────────────────────────────────────────────────────────


@app.post("/nodes", response_model=NodeResponse, status_code=201)
def create_node(req: CreateNodeRequest, background_tasks: BackgroundTasks):
    if _get_container_status(req.name) is not None:
        raise HTTPException(status_code=409, detail=f"Container '{req.name}' already exists")

    if req.node_type in ("router", "switch"):
        image = CHR_IMAGE
        cmd = None
        winbox_port = _get_free_port(_winbox_counter["next"])
        _winbox_counter["next"] = winbox_port + 1
        ports = {"8291/tcp": winbox_port, "80/tcp": None}
    else:
        image = PC_IMAGE
        cmd = ["sh", "-c", "apk add --no-cache iputils curl iproute2 >/dev/null 2>&1; sleep infinity"]
        winbox_port = None
        ports = {}

    try:
        docker_client.images.get(image)
    except docker.errors.ImageNotFound:
        docker_client.images.pull(image)

    labels = {"chr_node_type": req.node_type, "chr_ros_booted": "false"}
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

    if wan_ip and req.node_type in ("router", "switch"):
        background_tasks.add_task(_background_ros_boot, req.name, wan_ip)

    container.reload()
    return _node_response(container)


@app.get("/nodes", response_model=list[NodeResponse])
def list_nodes():
    containers = docker_client.containers.list(
        all=True,
        filters={"label": "chr_node_type"},
    )
    return [_node_response(c) for c in containers]


@app.get("/nodes/{name}", response_model=NodeResponse)
def get_node(name: str):
    try:
        container = docker_client.containers.get(name)
    except docker.errors.NotFound:
        _raise_not_found(name)
    return _node_response(container)


# ── Connections ──────────────────────────────────────────────────────


@app.post("/connections", response_model=ConnectionResponse, status_code=201)
def connect_nodes(req: ConnectRequest):
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
    nm.cleanup_veth_pair(host_veth)
    return {"deleted": host_veth}


@app.delete("/nodes/{name}")
def delete_node(name: str):
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


# ── RouterOS: System ─────────────────────────────────────────────────


@app.get("/ros/{node}/system")
def ros_system_info(node: str):
    ip = _require_ros_ip(node)
    try:
        resource = ros.get_system_resource(ip)
        identity = ros.get_system_identity(ip)
        return {"identity": identity, "resource": resource}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RouterOS API error: {e}")


@app.get("/ros/{node}/interfaces")
def ros_interfaces(node: str):
    ip = _require_ros_ip(node)
    try:
        return ros.get_interfaces(ip)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/ros/{node}/ip/addresses")
def ros_ip_addresses(node: str):
    ip = _require_ros_ip(node)
    try:
        return ros.get_ip_addresses(ip)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/ros/{node}/ip/routes")
def ros_routes(node: str):
    ip = _require_ros_ip(node)
    try:
        return ros.get_routes(ip)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/ros/{node}/firewall")
def ros_firewall(node: str):
    ip = _require_ros_ip(node)
    try:
        return ros.get_firewall_rules(ip)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.put("/ros/{node}/identity")
def ros_set_identity(node: str, req: IdentityRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.set_system_identity(ip, req.name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── RouterOS: Bridge & VLAN ─────────────────────────────────────────


@app.post("/ros/{node}/bridge")
def ros_create_bridge(node: str, req: BridgeRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.create_bridge(ip, req.name, req.vlan_filtering)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/bridge/port")
def ros_add_bridge_port(node: str, req: BridgePortRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_bridge_port(ip, req.bridge, req.interface, req.pvid)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/vlan")
def ros_create_vlan(node: str, req: VlanRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.create_vlan(ip, req.interface, req.vlan_id, req.name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/bridge/vlan")
def ros_add_bridge_vlan(node: str, req: BridgeVlanRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_bridge_vlan(ip, req.bridge, req.vlan_ids, req.tagged, req.untagged)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── RouterOS: IP ─────────────────────────────────────────────────────


@app.post("/ros/{node}/ip/address")
def ros_add_ip(node: str, req: IpAddressRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_ip_address(ip, req.address, req.interface)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/ip/route")
def ros_add_route(node: str, req: RouteRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_route(ip, req.dst, req.gateway, req.distance)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/dhcp-server")
def ros_add_dhcp_server(node: str, req: DhcpServerRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_dhcp_server(ip, req.interface, req.pool_name, req.network, req.gateway, req.dns)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── RouterOS: OSPF ───────────────────────────────────────────────────


@app.post("/ros/{node}/ospf")
def ros_configure_ospf(node: str, req: OspfRequest):
    ip = _require_ros_ip(node)
    try:
        networks = [n.model_dump() for n in req.networks]
        return ros.configure_ospf(ip, req.router_id, networks)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── RouterOS: BGP ────────────────────────────────────────────────────


@app.post("/ros/{node}/bgp")
def ros_configure_bgp(node: str, req: BgpRequest):
    ip = _require_ros_ip(node)
    try:
        neighbors = [n.model_dump(by_alias=True) for n in req.neighbors]
        return ros.configure_bgp(ip, req.as_number, req.router_id, neighbors, req.networks)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── RouterOS: Firewall ───────────────────────────────────────────────


@app.post("/ros/{node}/firewall/filter")
def ros_add_firewall_rule(node: str, req: FirewallRuleRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_firewall_rule(ip, req.chain, req.action, **req.params)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/ros/{node}/firewall/nat")
def ros_add_nat_rule(node: str, req: NatRuleRequest):
    ip = _require_ros_ip(node)
    try:
        return ros.add_nat_rule(ip, req.chain, req.action, **req.params)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
