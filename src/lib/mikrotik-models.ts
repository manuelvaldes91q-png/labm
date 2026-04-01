export interface MikroTikModel {
  id: string;
  name: string;
  series: string;
  ports: number;
  description: string;
  color: string;
  node_type: "router" | "switch";
}

export const MIKROTIK_MODELS: MikroTikModel[] = [
  {
    id: "rb4011",
    name: "RB4011iGS+",
    series: "RB4000",
    ports: 10,
    description: "10x Gigabit Ethernet, SFP+, dual-band WiFi",
    color: "#2563eb",
    node_type: "router",
  },
  {
    id: "rb5009",
    name: "RB5009UG+S+",
    series: "RB5000",
    ports: 7,
    description: "7x Gigabit Ethernet, SFP+, USB 3.0",
    color: "#7c3aed",
    node_type: "router",
  },
  {
    id: "ccr2116",
    name: "CCR2116-12G-4S+",
    series: "CCR2000",
    ports: 12,
    description: "16-core ARM, 12x Gigabit, 4x SFP+",
    color: "#dc2626",
    node_type: "router",
  },
  {
    id: "ccr2004",
    name: "CCR2004-1G-12S+2XS",
    series: "CCR2000",
    ports: 12,
    description: "4-core ARM, 12x SFP+, 2x QSFP28",
    color: "#ea580c",
    node_type: "router",
  },
  {
    id: "crs326",
    name: "CRS326-24G-2S+",
    series: "CRS300",
    ports: 24,
    description: "24x Gigabit Ethernet, 2x SFP+, L3 switch",
    color: "#0891b2",
    node_type: "switch",
  },
  {
    id: "crs354",
    name: "CRS354-48G-4S+2Q+",
    series: "CRS300",
    ports: 48,
    description: "48x Gigabit, 4x SFP+, 2x QSFP+",
    color: "#059669",
    node_type: "switch",
  },
  {
    id: "css610",
    name: "CSS610-8G-2S+",
    series: "CSS600",
    ports: 8,
    description: "8x Gigabit Ethernet, 2x SFP+",
    color: "#ca8a04",
    node_type: "switch",
  },
];

export function getModelById(id: string): MikroTikModel | undefined {
  return MIKROTIK_MODELS.find((m) => m.id === id);
}
