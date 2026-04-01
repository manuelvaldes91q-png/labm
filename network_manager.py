#!/usr/bin/env python3
"""
Network Manager for MikroTik CHR L2 connectivity.
Creates veth pairs, moves them to container namespaces, renames them,
and sets up WAN bridge with NAT for internet access.
"""

import subprocess
import docker
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

WAN_BRIDGE = "chr-wan"
WAN_SUBNET_BASE = "172.30.0"
WINBOX_PORT_BASE = 8291


class ContainerNotFoundError(Exception):
    pass


class ContainerNotRunningError(Exception):
    pass


class VethCommandError(Exception):
    pass


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise VethCommandError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def get_container_pid(container_name: str) -> int:
    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        raise ContainerNotFoundError(f"Container '{container_name}' not found")
    pid = container.attrs["State"]["Pid"]
    if pid == 0:
        raise ContainerNotRunningError(f"Container '{container_name}' is not running")
    return pid


def veth_exists(veth_name: str) -> bool:
    result = run(["ip", "link", "show", veth_name], check=False)
    return result.returncode == 0


def create_veth_pair(host_veth: str, container_veth: str) -> None:
    if veth_exists(host_veth):
        logger.warning("veth '%s' already exists, skipping creation", host_veth)
        return
    run(["ip", "link", "add", host_veth, "type", "veth", "peer", "name", container_veth])
    logger.info("Created veth pair %s <-> %s", host_veth, container_veth)


def move_veth_to_namespace(veth_name: str, container_pid: int, new_name: str) -> None:
    run(["ip", "link", "set", veth_name, "netns", str(container_pid)])
    run(["nsenter", "-t", str(container_pid), "-n", "ip", "link", "set", veth_name, "name", new_name])
    run(["nsenter", "-t", str(container_pid), "-n", "ip", "link", "set", new_name, "up"])
    run(["nsenter", "-t", str(container_pid), "-n", "ip", "link", "set", "lo", "up"])
    logger.info("Moved %s -> container PID %d as '%s' (UP)", veth_name, container_pid, new_name)


def assign_ip_in_namespace(container_pid: int, iface: str, ip_cidr: str) -> None:
    run(["nsenter", "-t", str(container_pid), "-n",
         "ip", "addr", "add", ip_cidr, "dev", iface], check=False)
    logger.info("Assigned %s to %s in PID %d", ip_cidr, iface, container_pid)


def set_host_veth_up(host_veth: str) -> None:
    run(["ip", "link", "set", host_veth, "up"])
    logger.info("Set host-side '%s' UP", host_veth)


def bridge_add(bridge: str, host_veth: str) -> None:
    if not veth_exists(bridge):
        run(["ip", "link", "add", bridge, "type", "bridge"])
        run(["ip", "link", "set", bridge, "up"])
        logger.info("Created bridge '%s'", bridge)
    run(["ip", "link", "set", host_veth, "master", bridge])
    logger.info("Attached '%s' to bridge '%s'", host_veth, bridge)


def cleanup_veth_pair(host_veth: str) -> None:
    if veth_exists(host_veth):
        run(["ip", "link", "del", host_veth], check=False)
        logger.info("Deleted veth pair via host end '%s'", host_veth)


# ── WAN Bridge ───────────────────────────────────────────────────────


def get_host_default_iface() -> str:
    result = run(["ip", "route", "show", "default"])
    parts = result.stdout.split()
    for i, p in enumerate(parts):
        if p == "dev" and i + 1 < len(parts):
            return parts[i + 1]
    return "eth0"


def _get_next_wan_ip() -> str:
    existing = set()
    client = docker.from_env()
    containers = client.containers.list(filters={"label": "chr_wan_ip"})
    for c in containers:
        ip_label = c.labels.get("chr_wan_ip", "")
        if ip_label:
            parts = ip_label.split(".")
            if len(parts) == 4:
                try:
                    existing.add(int(parts[3]))
                except ValueError:
                    pass
    for last_octet in range(10, 254):
        if last_octet not in existing:
            return f"{WAN_SUBNET_BASE}.{last_octet}"
    raise VethCommandError("No available WAN IPs in subnet")


def setup_wan_bridge() -> dict:
    if veth_exists(WAN_BRIDGE):
        logger.info("WAN bridge '%s' already exists", WAN_BRIDGE)
        return {"bridge": WAN_BRIDGE, "status": "exists"}

    host_iface = get_host_default_iface()
    logger.info("Host default interface: %s", host_iface)

    run(["ip", "link", "add", WAN_BRIDGE, "type", "bridge"])
    run(["ip", "link", "set", WAN_BRIDGE, "up"])

    bridge_ip = f"{WAN_SUBNET_BASE}.1/24"
    run(["ip", "addr", "add", bridge_ip, "dev", WAN_BRIDGE], check=False)

    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    run(["iptables", "-t", "nat", "-C", "POSTROUTING",
         "-s", f"{WAN_SUBNET_BASE}.0/24",
         "-o", host_iface,
         "-j", "MASQUERADE"], check=False)
    if run(["iptables", "-t", "nat", "-C", "POSTROUTING",
            "-s", f"{WAN_SUBNET_BASE}.0/24",
            "-o", host_iface,
            "-j", "MASQUERADE"], check=False).returncode != 0:
        run(["iptables", "-t", "nat", "-A", "POSTROUTING",
             "-s", f"{WAN_SUBNET_BASE}.0/24",
             "-o", host_iface,
             "-j", "MASQUERADE"])

    run(["iptables", "-A", "FORWARD",
         "-i", WAN_BRIDGE, "-o", host_iface, "-j", "ACCEPT"], check=False)
    run(["iptables", "-A", "FORWARD",
         "-i", host_iface, "-o", WAN_BRIDGE,
         "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"], check=False)

    logger.info("WAN bridge '%s' setup complete with NAT via %s", WAN_BRIDGE, host_iface)
    return {"bridge": WAN_BRIDGE, "status": "created", "host_iface": host_iface}


def connect_wan(container_name: str) -> dict:
    pid = get_container_pid(container_name)
    wan_ip = _get_next_wan_ip()
    host_veth = f"veth-{container_name}-wan"
    container_veth = f"vethc-{container_name}-wan"

    create_veth_pair(host_veth, container_veth)
    set_host_veth_up(host_veth)
    bridge_add(WAN_BRIDGE, host_veth)
    move_veth_to_namespace(container_veth, pid, "wan")
    assign_ip_in_namespace(pid, "wan", f"{wan_ip}/24")

    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
        container.reload()
        labels = dict(container.labels or {})
        labels["chr_wan_ip"] = wan_ip
    except docker.errors.NotFound:
        pass

    logger.info("Connected %s to WAN as %s", container_name, wan_ip)
    return {
        "container": container_name,
        "interface": "wan",
        "ip": wan_ip,
        "bridge": WAN_BRIDGE,
    }


def get_container_wan_ip(container_name: str) -> str | None:
    client = docker.from_env()
    try:
        c = client.containers.get(container_name)
        return c.labels.get("chr_wan_ip")
    except docker.errors.NotFound:
        return None


# ── Container connection ─────────────────────────────────────────────


def connect_container(
    container_name: str,
    iface_index: int,
    bridge: str | None = None,
) -> dict:
    pid = get_container_pid(container_name)
    ether_name = f"ether{iface_index}"
    host_veth = f"veth-{container_name}-{ether_name}"
    container_veth = f"vethc-{container_name}-{ether_name}"

    create_veth_pair(host_veth, container_veth)
    set_host_veth_up(host_veth)
    move_veth_to_namespace(container_veth, pid, ether_name)

    if bridge:
        bridge_add(bridge, host_veth)

    return {
        "container": container_name,
        "interface": ether_name,
        "host_veth": host_veth,
        "container_pid": pid,
        "bridge": bridge,
    }


def connect_pair(
    container_a: str,
    index_a: int,
    container_b: str,
    index_b: int,
) -> dict:
    pid_a = get_container_pid(container_a)
    pid_b = get_container_pid(container_b)

    ether_a = f"ether{index_a}"
    ether_b = f"ether{index_b}"
    veth_a = f"veth-{container_a}-{ether_a}"
    veth_b = f"veth-{container_b}-{ether_b}"

    create_veth_pair(veth_a, veth_b)
    set_host_veth_up(veth_a)
    move_veth_to_namespace(veth_b, pid_b, ether_b)
    move_veth_to_namespace(veth_a, pid_a, ether_a)

    return {
        "container_a": container_a,
        "interface_a": ether_a,
        "container_b": container_b,
        "interface_b": ether_b,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command> [args...]")
        print()
        print("Commands:")
        print("  connect <container> <iface_index> [bridge]")
        print("  pair <container_a> <index_a> <container_b> <index_b>")
        print("  cleanup <host_veth_name>")
        print("  wan-setup")
        print("  wan-connect <container>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "connect":
        container = sys.argv[2]
        index = int(sys.argv[3])
        bridge = sys.argv[4] if len(sys.argv) > 4 else None
        result = connect_container(container, index, bridge)
        logger.info("Done: %s", result)

    elif cmd == "pair":
        ca, ia, cb, ib = sys.argv[2], int(sys.argv[3]), sys.argv[4], int(sys.argv[5])
        result = connect_pair(ca, ia, cb, ib)
        logger.info("Done: %s", result)

    elif cmd == "cleanup":
        cleanup_veth_pair(sys.argv[2])

    elif cmd == "wan-setup":
        result = setup_wan_bridge()
        logger.info("Done: %s", result)

    elif cmd == "wan-connect":
        result = connect_wan(sys.argv[2])
        logger.info("Done: %s", result)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
