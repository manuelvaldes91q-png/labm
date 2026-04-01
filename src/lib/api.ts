const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
}

interface ConnectionResponse {
  node_a: string;
  interface_a: string;
  node_b: string;
  interface_b: string;
}

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

export function createNode(payload: CreateNodePayload) {
  return request<NodeResponse>("/nodes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listNodes() {
  return request<NodeResponse[]>("/nodes");
}

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

export function deleteNode(name: string) {
  return request<{ deleted: string }>(`/nodes/${name}`, {
    method: "DELETE",
  });
}
