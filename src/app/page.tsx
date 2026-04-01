"use client";

import dynamic from "next/dynamic";
import NodePalette from "@/components/NodePalette";

const TopologyCanvas = dynamic(
  () => import("@/components/TopologyCanvas"),
  { ssr: false },
);

export default function Home() {
  return (
    <div className="flex h-screen bg-neutral-950 text-white overflow-hidden">
      <NodePalette />
      <TopologyCanvas />
    </div>
  );
}
