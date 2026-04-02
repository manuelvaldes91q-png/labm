#!/usr/bin/env python3
"""
RouterOS v6 legacy API client for configuring MikroTik CHR containers.
Communicates with RouterOS v6 API on port 8728 using librouteros.
"""

import time
import logging
import socket
from librouteros import connect
from librouteros.exceptions import TrapError, FatalError, ConnectionError

logger = logging.getLogger(__name__)

ROS_USER = "admin"
ROS_PASS = ""
ROS_API_PORT = 8728
BOOT_TIMEOUT = 300
BOOT_POLL_INTERVAL = 5


class RouterOSConnectionError(Exception):
    pass


class RouterOSConfigError(Exception):
    pass


def _get_api(host_ip: str):
    return connect(
        username=ROS_USER,
        password=ROS_PASS,
        host=host_ip,
        port=ROS_API_PORT,
    )


def wait_for_boot(host_ip: str, timeout: int = BOOT_TIMEOUT) -> bool:
    start = time.time()
    last_error = None
    while time.time() - start < timeout:
        try:
            api = _get_api(host_ip)
            list(api.path("system", "resource"))
            elapsed = round(time.time() - start, 1)
            logger.info("RouterOS v6 booted at %s (%.1fs)", host_ip, elapsed)
            api.close()
            return True
        except Exception as e:
            last_error = e
            elapsed = round(time.time() - start, 1)
            logger.debug("Waiting for RouterOS at %s (%.1fs): %s", host_ip, elapsed, e)
        time.sleep(BOOT_POLL_INTERVAL)
    error_detail = f" Last error: {last_error}" if last_error else ""
    raise RouterOSConnectionError(
        f"RouterOS at {host_ip} did not boot within {timeout}s.{error_detail}"
    )


def _ros_cmd(host_ip: str, path_parts: tuple, **kwargs):
    try:
        with _get_api(host_ip) as api:
            return list(api.path(*path_parts).add(**kwargs))
    except TrapError as e:
        if "already have" in str(e).lower() or "failure" in str(e).lower():
            logger.info("Resource already exists or minor error: %s", e)
            return []
        raise RouterOSConfigError(str(e)) from e


def _ros_set(host_ip: str, path_parts: tuple, item_id, **kwargs):
    try:
        with _get_api(host_ip) as api:
            return list(api.path(*path_parts).set(id=item_id, **kwargs))
    except TrapError as e:
        raise RouterOSConfigError(str(e)) from e


def _ros_get(host_ip: str, path_parts: tuple):
    try:
        with _get_api(host_ip) as api:
            return list(api.path(*path_parts))
    except TrapError as e:
        raise RouterOSConfigError(str(e)) from e


# ── Auto-Configuration ───────────────────────────────────────────────


def auto_configure(host_ip: str) -> dict:
    wait_for_boot(host_ip)
    results = {}

    try:
        identity = _ros_get(host_ip, ("system", "identity"))
        results["identity"] = identity[0].get("name", "MikroTik") if identity else "MikroTik"
    except Exception:
        results["identity"] = "unknown"

    try:
        dns_entries = _ros_get(host_ip, ("ip", "dns"))
        if dns_entries:
            _ros_set(host_ip, ("ip", "dns"), dns_entries[0].get("id"),
                     servers="8.8.8.8,8.8.4.4", **{"allow-remote-requests": "true"})
        results["dns"] = "configured"
    except Exception as e:
        results["dns"] = f"error: {e}"

    try:
        interfaces = _ros_get(host_ip, ("interface",))
        wan_iface = None
        for iface in interfaces:
            if iface.get("name") == "wan":
                wan_iface = iface
                break
        if wan_iface:
            _ros_cmd(host_ip, ("ip", "dhcp-client"),
                     interface="wan", **{"add-default-route": "yes",
                                         "use-peer-dns": "no", "use-peer-ntp": "no"})
            results["wan_dhcp"] = "configured"
        else:
            results["wan_dhcp"] = "no WAN interface found"
    except Exception as e:
        results["wan_dhcp"] = f"error: {e}"

    try:
        _ros_cmd(host_ip, ("ip", "firewall", "nat"),
                 chain="srcnat", action="masquerade", **{"out-interface": "wan"})
        results["nat"] = "configured"
    except Exception as e:
        results["nat"] = f"error: {e}"

    try:
        _ros_cmd(host_ip, ("ip", "firewall", "filter"),
                 chain="input", action="accept", protocol="tcp",
                 **{"dst-port": "8291", "comment": "Allow WinBox"})
        _ros_cmd(host_ip, ("ip", "firewall", "filter"),
                 chain="input", action="accept", protocol="tcp",
                 **{"dst-port": "8728", "comment": "Allow API"})
        _ros_cmd(host_ip, ("ip", "firewall", "filter"),
                 chain="input", action="accept", protocol="tcp",
                 **{"dst-port": "8729", "comment": "Allow API-SSL"})
        results["firewall"] = "configured"
    except Exception as e:
        results["firewall"] = f"error: {e}"

    return results


# ── Bridge / VLAN ────────────────────────────────────────────────────


def create_bridge(host_ip: str, name: str, vlan_filtering: bool = False) -> dict:
    result = _ros_cmd(host_ip, ("interface", "bridge"),
                      name=name, **{"vlan-filtering": str(vlan_filtering).lower()})
    return {"bridge": name, "result": result}


def add_bridge_port(host_ip: str, bridge: str, interface: str, pvid: int | None = None) -> dict:
    data = {"bridge": bridge, "interface": interface}
    if pvid is not None:
        data["pvid"] = str(pvid)
    result = _ros_cmd(host_ip, ("interface", "bridge", "port"), **data)
    return {"result": result}


def create_vlan(host_ip: str, interface: str, vlan_id: int, name: str | None = None) -> dict:
    vlan_name = name or f"vlan{vlan_id}"
    result = _ros_cmd(host_ip, ("interface", "vlan"),
                      interface=interface, **{"vlan-id": str(vlan_id)}, name=vlan_name)
    return {"vlan": vlan_name, "result": result}


def add_bridge_vlan(host_ip: str, bridge: str, vlan_ids: str, tagged: str | None = None, untagged: str | None = None) -> dict:
    data = {"bridge": bridge, **{"vlan-ids": vlan_ids}}
    if tagged:
        data["tagged"] = tagged
    if untagged:
        data["untagged"] = untagged
    result = _ros_cmd(host_ip, ("interface", "bridge", "vlan"), **data)
    return {"result": result}


def set_port_pvid(host_ip: str, bridge: str, interface: str, pvid: int) -> dict:
    ports = _ros_get(host_ip, ("interface", "bridge", "port"))
    for port in ports:
        if port.get("interface") == interface and port.get("bridge") == bridge:
            _ros_set(host_ip, ("interface", "bridge", "port"), port[".id"], pvid=str(pvid))
            return {"updated": True}
    return add_bridge_port(host_ip, bridge, interface, pvid)


# ── IP Addressing ────────────────────────────────────────────────────


def add_ip_address(host_ip: str, address: str, interface: str) -> dict:
    result = _ros_cmd(host_ip, ("ip", "address"), address=address, interface=interface)
    return {"result": result}


def add_route(host_ip: str, dst: str, gateway: str, distance: int = 1) -> dict:
    result = _ros_cmd(host_ip, ("ip", "route"),
                      **{"dst-address": dst, "gateway": gateway, "distance": str(distance)})
    return {"result": result}


def add_dhcp_server(host_ip: str, interface: str, pool_name: str, network: str, gateway: str, dns: str = "8.8.8.8") -> dict:
    _ros_cmd(host_ip, ("ip", "pool"), name=pool_name, ranges=network)
    _ros_cmd(host_ip, ("ip", "dhcp-server", "network"),
             address=network, gateway=gateway, **{"dns-server": dns})
    result = _ros_cmd(host_ip, ("ip", "dhcp-server"),
                      name=f"dhcp-{interface}", interface=interface, **{"address-pool": pool_name})
    return {"result": result}


# ── OSPF ─────────────────────────────────────────────────────────────


def configure_ospf(host_ip: str, router_id: str, networks: list[dict], areas: list[dict] | None = None) -> dict:
    if areas:
        for area in areas:
            try:
                _ros_cmd(host_ip, ("routing", "ospf", "area"), **area)
            except Exception:
                pass

    try:
        _ros_cmd(host_ip, ("routing", "ospf", "instance"),
                 **{"router-id": router_id, "version": "2"})
    except Exception:
        pass

    results = {"router_id": router_id}
    for net in networks:
        try:
            _ros_cmd(host_ip, ("routing", "ospf", "interface-template"), **net)
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
    results = {"as": as_number, "router_id": router_id}

    if neighbors:
        for nb in neighbors:
            try:
                peer_data = {
                    "name": nb.get("name", f"peer-{nb.get('remote-address', 'unknown')}"),
                    "remote-address": nb["remote-address"],
                    "remote-as": str(nb["remote-as"]),
                    "as": str(as_number),
                    "router-id": router_id,
                }
                _ros_cmd(host_ip, ("routing", "bgp", "peer"), **peer_data)
                results[f"peer_{nb['remote-address']}"] = "added"
            except Exception as e:
                results[f"peer_{nb.get('remote-address', 'unknown')}"] = str(e)

    if networks:
        for net in networks:
            try:
                _ros_cmd(host_ip, ("routing", "bgp", "network"), address=net)
                results[f"network_{net}"] = "added"
            except Exception as e:
                results[f"network_{net}"] = str(e)

    return results


# ── Firewall ─────────────────────────────────────────────────────────


def add_firewall_rule(host_ip: str, chain: str, action: str = "accept", **kwargs) -> dict:
    result = _ros_cmd(host_ip, ("ip", "firewall", "filter"), chain=chain, action=action, **kwargs)
    return {"result": result}


def add_nat_rule(host_ip: str, chain: str = "srcnat", action: str = "masquerade", **kwargs) -> dict:
    result = _ros_cmd(host_ip, ("ip", "firewall", "nat"), chain=chain, action=action, **kwargs)
    return {"result": result}


# ── System ───────────────────────────────────────────────────────────


def get_system_resource(host_ip: str) -> dict:
    result = _ros_get(host_ip, ("system", "resource"))
    return result[0] if result else {}


def get_system_identity(host_ip: str) -> str:
    result = _ros_get(host_ip, ("system", "identity"))
    return result[0].get("name", "MikroTik") if result else "MikroTik"


def set_system_identity(host_ip: str, name: str) -> dict:
    identity = _ros_get(host_ip, ("system", "identity"))
    if identity:
        _ros_set(host_ip, ("system", "identity"), identity[0].get("id"), name=name)
    return {"name": name}


def get_interfaces(host_ip: str) -> list[dict]:
    return _ros_get(host_ip, ("interface",))


def get_ip_addresses(host_ip: str) -> list[dict]:
    return _ros_get(host_ip, ("ip", "address"))


def get_routes(host_ip: str) -> list[dict]:
    return _ros_get(host_ip, ("ip", "route"))


def get_firewall_rules(host_ip: str) -> list[dict]:
    return _ros_get(host_ip, ("ip", "firewall", "filter"))
