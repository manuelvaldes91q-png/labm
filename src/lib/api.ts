const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ───────────────────────────────────────────────────────────

interface CreateNodePayload {
  name: string;
  node_type: "router" | "switch" | "pc";
}

interface ConnectPayload {
  node_a: string;
  node_b: string;
  index_a: number;
  index_b: number;
}

interface NodeResponse {
  name: string;
  node_type: string;
  image: string;
  status: string;
  wan_ip: string | null;
  winbox_port: number | null;
  ros_booted: boolean;
}

interface ConnectionResponse {
  node_a: string;
  interface_a: string;
  node_b: string;
  interface_b: string;
}

interface WanStatusResponse {
  bridge: string;
  status: string;
  host_interface: string | null;
}

// Bridge / VLAN
interface BridgePayload {
  node: string;
  name: string;
  vlan_filtering?: boolean;
}

interface BridgePortPayload {
  node: string;
  bridge: string;
  interface: string;
  pvid?: number;
}

interface VlanPayload {
  node: string;
  interface: string;
  vlan_id: number;
  name?: string;
}

interface BridgeVlanPayload {
  node: string;
  bridge: string;
  vlan_ids: string;
  tagged?: string;
  untagged?: string;
}

// IP
interface IpAddressPayload {
  node: string;
  address: string;
  interface: string;
}

interface RoutePayload {
  node: string;
  dst: string;
  gateway: string;
  distance?: number;
}

interface DhcpServerPayload {
  node: string;
  interface: string;
  pool_name: string;
  network: string;
  gateway: string;
  dns?: string;
}

// OSPF
interface OspfNetwork {
  network: string;
  area?: string;
}

interface OspfPayload {
  node: string;
  router_id: string;
  networks: OspfNetwork[];
}

// BGP
interface BgpNeighbor {
  name: string;
  remote_address: string;
  remote_as: number;
}

interface BgpPayload {
  node: string;
  as_number: number;
  router_id: string;
  neighbors?: BgpNeighbor[];
  networks?: string[];
}

// Firewall
interface FirewallRulePayload {
  node: string;
  chain: string;
  action?: string;
  params?: Record<string, string>;
}

interface NatRulePayload {
  node: string;
  chain?: string;
  action?: string;
  params?: Record<string, string>;
}

interface IdentityPayload {
  node: string;
  name: string;
}

// ── HTTP Client ─────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ── Nodes ───────────────────────────────────────────────────────────

export function createNode(payload: CreateNodePayload) {
  return request<NodeResponse>("/nodes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listNodes() {
  return request<NodeResponse[]>("/nodes");
}

export function getNode(name: string) {
  return request<NodeResponse>(`/nodes/${name}`);
}

export function deleteNode(name: string) {
  return request<{ deleted: string }>(`/nodes/${name}`, {
    method: "DELETE",
  });
}

// ── Connections ─────────────────────────────────────────────────────

export function connectNodes(payload: ConnectPayload) {
  return request<ConnectionResponse>("/connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listConnections() {
  return request<{ host_interface: string }[]>("/connections");
}

export function deleteConnection(hostVeth: string) {
  return request<{ deleted: string }>(`/connections/${hostVeth}`, {
    method: "DELETE",
  });
}

// ── WAN ─────────────────────────────────────────────────────────────

export function setupWan() {
  return request<WanStatusResponse>("/wan/setup", { method: "POST" });
}

export function getWanStatus() {
  return request<WanStatusResponse>("/wan/status");
}

interface RosInterface {
  name: string;
  type: string;
  running: boolean;
  disabled: boolean;
  [key: string]: unknown;
}

interface RosIpAddress {
  address: string;
  interface: string;
  [key: string]: unknown;
}

interface RosRoute {
  dst: string;
  gateway: string;
  active: boolean;
  [key: string]: unknown;
}

interface RosFirewallRule {
  chain: string;
  action: string;
  [key: string]: unknown;
}

interface RosSystemInfo {
  identity: string;
  resource: Record<string, unknown>;
}

export function getRosSystem(node: string) {
  return request<RosSystemInfo>(`/ros/${node}/system`);
}

export function getRosInterfaces(node: string) {
  return request<RosInterface[]>(`/ros/${node}/interfaces`);
}

export function getRosIpAddresses(node: string) {
  return request<RosIpAddress[]>(`/ros/${node}/ip/addresses`);
}

export function getRosRoutes(node: string) {
  return request<RosRoute[]>(`/ros/${node}/ip/routes`);
}

export function getRosFirewall(node: string) {
  return request<RosFirewallRule[]>(`/ros/${node}/firewall`);
}

export function setRosIdentity(payload: IdentityPayload) {
  return request(`/ros/${payload.node}/identity`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ── RouterOS: Bridge & VLAN ─────────────────────────────────────────

export function createRosBridge(payload: BridgePayload) {
  return request(`/ros/${payload.node}/bridge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addRosBridgePort(payload: BridgePortPayload) {
  return request(`/ros/${payload.node}/bridge/port`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createRosVlan(payload: VlanPayload) {
  return request(`/ros/${payload.node}/vlan`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addRosBridgeVlan(payload: BridgeVlanPayload) {
  return request(`/ros/${payload.node}/bridge/vlan`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── RouterOS: IP ────────────────────────────────────────────────────

export function addRosIpAddress(payload: IpAddressPayload) {
  return request(`/ros/${payload.node}/ip/address`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addRosRoute(payload: RoutePayload) {
  return request(`/ros/${payload.node}/ip/route`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addRosDhcpServer(payload: DhcpServerPayload) {
  return request(`/ros/${payload.node}/dhcp-server`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── RouterOS: OSPF ──────────────────────────────────────────────────

export function configureRosOspf(payload: OspfPayload) {
  return request(`/ros/${payload.node}/ospf`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── RouterOS: BGP ───────────────────────────────────────────────────

export function configureRosBgp(payload: BgpPayload) {
  return request(`/ros/${payload.node}/bgp`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── RouterOS: Firewall ──────────────────────────────────────────────

export function addRosFirewallRule(payload: FirewallRulePayload) {
  return request(`/ros/${payload.node}/firewall/filter`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addRosNatRule(payload: NatRulePayload) {
  return request(`/ros/${payload.node}/firewall/nat`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
