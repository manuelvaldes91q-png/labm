#!/usr/bin/env python3
"""
RouterOS REST API client for configuring MikroTik CHR containers.
Communicates with RouterOS v7+ REST API on port 80.
"""

import time
import logging
import httpx

logger = logging.getLogger(__name__)

ROS_USER = "admin"
ROS_PASS = ""
ROS_API_PORT = 80
BOOT_TIMEOUT = 120
BOOT_POLL_INTERVAL = 5


class RouterOSConnectionError(Exception):
    pass


class RouterOSConfigError(Exception):
    pass


def _api_url(host_ip: str) -> str:
    return f"http://{host_ip}:{ROS_API_PORT}/rest"


def wait_for_boot(host_ip: str, timeout: int = BOOT_TIMEOUT) -> bool:
    url = _api_url(host_ip)
    client = httpx.Client(auth=(ROS_USER, ROS_PASS), timeout=5.0)
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = client.get(f"{url}/system/resource")
            if resp.status_code == 200:
                elapsed = round(time.time() - start, 1)
                logger.info("RouterOS booted at %s (%.1fs)", host_ip, elapsed)
                client.close()
                return True
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
            pass
        time.sleep(BOOT_POLL_INTERVAL)
    client.close()
    raise RouterOSConnectionError(
        f"RouterOS at {host_ip} did not boot within {timeout}s"
    )


def ros_get(host_ip: str, endpoint: str) -> list[dict]:
    url = f"{_api_url(host_ip)}/{endpoint}"
    resp = httpx.get(url, auth=(ROS_USER, ROS_PASS), timeout=15.0)
    if resp.status_code == 401:
        raise RouterOSConfigError("Authentication failed")
    resp.raise_for_status()
    return resp.json()


def ros_post(host_ip: str, endpoint: str, data: dict) -> dict:
    url = f"{_api_url(host_ip)}/{endpoint}"
    resp = httpx.post(url, auth=(ROS_USER, ROS_PASS), json=data, timeout=15.0)
    if resp.status_code == 400:
        detail = resp.json()
        if "message" in detail and "already have" in detail.get("message", "").lower():
            logger.info("Resource already exists: %s", detail["message"])
            return detail
    resp.raise_for_status()
    return resp.json()


def ros_put(host_ip: str, endpoint: str, data: dict) -> dict:
    url = f"{_api_url(host_ip)}/{endpoint}"
    resp = httpx.put(url, auth=(ROS_USER, ROS_PASS), json=data, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def ros_delete(host_ip: str, endpoint: str) -> dict:
    url = f"{_api_url(host_ip)}/{endpoint}"
    resp = httpx.delete(url, auth=(ROS_USER, ROS_PASS), timeout=15.0)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def ros_patch(host_ip: str, endpoint: str, data: dict) -> dict:
    url = f"{_api_url(host_ip)}/{endpoint}"
    resp = httpx.patch(url, auth=(ROS_USER, ROS_PASS), json=data, timeout=15.0)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ── Auto-Configuration ───────────────────────────────────────────────


def auto_configure(host_ip: str) -> dict:
    wait_for_boot(host_ip)
    results = {}

    try:
        identity = ros_get(host_ip, "system/identity")
        results["identity"] = identity[0].get("name", "MikroTik") if identity else "MikroTik"
    except Exception:
        results["identity"] = "unknown"

    try:
        ros_put(host_ip, "ip/dns/set", {"servers": "8.8.8.8,8.8.4.4", "allow-remote-requests": "true"})
        results["dns"] = "configured"
    except Exception as e:
        results["dns"] = f"error: {e}"

    try:
        interfaces = ros_get(host_ip, "interface")
        wan_iface = None
        for iface in interfaces:
            if iface.get("name") == "wan":
                wan_iface = iface
                break
        if wan_iface:
            ros_post(host_ip, "ip/dhcp-client/add", {
                "interface": "wan",
                "add-default-route": "yes",
                "use-peer-dns": "no",
                "use-peer-ntp": "no",
            })
            results["wan_dhcp"] = "configured"
        else:
            results["wan_dhcp"] = "no WAN interface found"
    except Exception as e:
        results["wan_dhcp"] = f"error: {e}"

    try:
        ros_post(host_ip, "ip/firewall/nat/add", {
            "chain": "srcnat",
            "action": "masquerade",
            "out-interface": "wan",
        })
        results["nat"] = "configured"
    except Exception as e:
        results["nat"] = f"error: {e}"

    return results


# ── Bridge / VLAN ────────────────────────────────────────────────────


def create_bridge(host_ip: str, name: str, vlan_filtering: bool = False) -> dict:
    return ros_post(host_ip, "interface/bridge/add", {
        "name": name,
        "vlan-filtering": str(vlan_filtering).lower(),
    })


def add_bridge_port(host_ip: str, bridge: str, interface: str, pvid: int | None = None) -> dict:
    data = {"bridge": bridge, "interface": interface}
    if pvid is not None:
        data["pvid"] = str(pvid)
    return ros_post(host_ip, "interface/bridge/port/add", data)


def create_vlan(host_ip: str, interface: str, vlan_id: int, name: str | None = None) -> dict:
    vlan_name = name or f"vlan{vlan_id}"
    return ros_post(host_ip, "interface/vlan/add", {
        "interface": interface,
        "vlan-id": vlan_id,
        "name": vlan_name,
    })


def add_bridge_vlan(host_ip: str, bridge: str, vlan_ids: str, tagged: str | None = None, untagged: str | None = None) -> dict:
    data = {"bridge": bridge, "vlan-ids": vlan_ids}
    if tagged:
        data["tagged"] = tagged
    if untagged:
        data["untagged"] = untagged
    return ros_post(host_ip, "interface/bridge/vlan/add", data)


def set_port_pvid(host_ip: str, bridge: str, interface: str, pvid: int) -> dict:
    ports = ros_get(host_ip, "interface/bridge/port")
    for port in ports:
        if port.get("interface") == interface and port.get("bridge") == bridge:
            return ros_patch(
                host_ip,
                f"interface/bridge/port/{port['.id']}",
                {"pvid": str(pvid)},
            )
    return add_bridge_port(host_ip, bridge, interface, pvid)


# ── IP Addressing ────────────────────────────────────────────────────


def add_ip_address(host_ip: str, address: str, interface: str) -> dict:
    return ros_post(host_ip, "ip/address/add", {
        "address": address,
        "interface": interface,
    })


def add_route(host_ip: str, dst: str, gateway: str, distance: int = 1) -> dict:
    return ros_post(host_ip, "ip/route/add", {
        "dst-address": dst,
        "gateway": gateway,
        "distance": distance,
    })


def add_dhcp_server(host_ip: str, interface: str, pool_name: str, network: str, gateway: str, dns: str = "8.8.8.8") -> dict:
    ros_post(host_ip, "ip/pool/add", {
        "name": pool_name,
        "ranges": network,
    })
    ros_post(host_ip, "ip/dhcp-server/network/add", {
        "address": network,
        "gateway": gateway,
        "dns-server": dns,
    })
    return ros_post(host_ip, "ip/dhcp-server/add", {
        "name": f"dhcp-{interface}",
        "interface": interface,
        "address-pool": pool_name,
    })


# ── OSPF ─────────────────────────────────────────────────────────────


def configure_ospf(host_ip: str, router_id: str, networks: list[dict], areas: list[dict] | None = None) -> dict:
    if areas:
        for area in areas:
            try:
                ros_post(host_ip, "routing/ospf/area/add", area)
            except Exception:
                pass

    router_data = {"router-id": router_id, "version": "2"}
    try:
        ros_post(host_ip, "routing/ospf/instance/add", router_data)
    except Exception:
        pass

    results = {"router_id": router_id}
    for net in networks:
        try:
            ros_post(host_ip, "routing/ospf/interface-template/add", net)
            results[f"net_{net.get('network', 'unknown')}"] = "added"
        except Exception as e:
            results[f"net_{net.get('network', 'unknown')}"] = str(e)

    return results


# ── BGP ──────────────────────────────────────────────────────────────


def configure_bgp(
    host_ip: str,
    as_number: int,
    router_id: str,
    neighbors: list[dict] | None = None,
    networks: list[str] | None = None,
) -> dict:
    ros_post(host_ip, "routing/bgp/connection/add", {
        "name": f"as{as_number}",
        "as": str(as_number),
        "router-id": router_id,
        "listen": "yes",
    })

    results = {"as": as_number, "router_id": router_id}

    if neighbors:
        for nb in neighbors:
            try:
                ros_post(host_ip, "routing/bgp/connection/add", nb)
                results[f"peer_{nb.get('remote.address', 'unknown')}"] = "added"
            except Exception as e:
                results[f"peer_{nb.get('remote.address', 'unknown')}"] = str(e)

    if networks:
        for net in networks:
            try:
                ros_post(host_ip, "routing/bgp/network/add", {"address": net})
                results[f"network_{net}"] = "added"
            except Exception as e:
                results[f"network_{net}"] = str(e)

    return results


# ── Firewall ─────────────────────────────────────────────────────────


def add_firewall_rule(
    host_ip: str,
    chain: str,
    action: str = "accept",
    **kwargs,
) -> dict:
    data = {"chain": chain, "action": action}
    data.update(kwargs)
    return ros_post(host_ip, "ip/firewall/filter/add", data)


def add_nat_rule(
    host_ip: str,
    chain: str = "srcnat",
    action: str = "masquerade",
    **kwargs,
) -> dict:
    data = {"chain": chain, "action": action}
    data.update(kwargs)
    return ros_post(host_ip, "ip/firewall/nat/add", data)


# ── System ───────────────────────────────────────────────────────────


def get_system_resource(host_ip: str) -> dict:
    result = ros_get(host_ip, "system/resource")
    return result[0] if result else {}


def get_system_identity(host_ip: str) -> str:
    result = ros_get(host_ip, "system/identity")
    return result[0].get("name", "MikroTik") if result else "MikroTik"


def set_system_identity(host_ip: str, name: str) -> dict:
    return ros_put(host_ip, "system/identity/set", {"name": name})


def get_interfaces(host_ip: str) -> list[dict]:
    return ros_get(host_ip, "interface")


def get_ip_addresses(host_ip: str) -> list[dict]:
    return ros_get(host_ip, "ip/address")


def get_routes(host_ip: str) -> list[dict]:
    return ros_get(host_ip, "ip/route")


def get_firewall_rules(host_ip: str) -> list[dict]:
    return ros_get(host_ip, "ip/firewall/filter")
