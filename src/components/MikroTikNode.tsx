"use client";

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { DeviceModel } from "@/lib/mikrotik-models";

export type MikroTikNodeData = Record<string, unknown> & DeviceModel & {
  containerName?: string;
  containerStatus?: string;
  wanIp?: string;
  winboxPort?: number;
  rosBooted?: boolean;
};

function MikroTikNode({ data, selected }: NodeProps) {
  const d = data as unknown as MikroTikNodeData;
  const borderColor = selected ? "#ffffff" : d.color;
  const glowStyle = selected
    ? { boxShadow: `0 0 16px ${d.color}80` }
    : undefined;

  const isPc = d.node_type === "pc";

  return (
    <div
      className="relative rounded-lg border-2 bg-neutral-900 px-4 py-3 min-w-[200px] transition-shadow"
      style={{ borderColor, ...glowStyle }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !bg-neutral-400 !border-2 !border-neutral-900 hover:!bg-white"
      />

      <div className="flex items-center gap-2 mb-1">
        <div
          className="w-3 h-3 rounded-full shrink-0"
          style={{ backgroundColor: d.color }}
        />
        <span className="text-xs font-mono uppercase tracking-wider text-neutral-400">
          {d.series}
        </span>
        {d.node_type && (
          <span
            className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded"
            style={{
              backgroundColor: `${d.color}20`,
              color: d.color,
            }}
          >
            {d.node_type}
          </span>
        )}
      </div>

      <div className="text-sm font-semibold text-white leading-tight">
        {d.name}
      </div>

      <div className="text-[11px] text-neutral-500 mt-1">{d.description}</div>

      <div className="flex items-center gap-3 mt-2">
        {!isPc && (
          <span className="text-[10px] text-neutral-500">
            {d.ports} ports
          </span>
        )}
        {d.containerStatus && (
          <span
            className={`text-[10px] font-medium ${
              d.containerStatus === "running"
                ? "text-emerald-400"
                : d.containerStatus === "provisioning"
                  ? "text-yellow-400"
                  : "text-red-400"
            }`}
          >
            ● {d.containerStatus}
          </span>
        )}
      </div>

      {d.containerName && (
        <div className="text-[10px] text-neutral-600 mt-1 font-mono">
          {d.containerName}
        </div>
      )}

      {d.wanIp && (
        <div className="flex items-center gap-1 mt-1">
          <span className="text-[9px] text-emerald-600 font-mono">
            WAN: {d.wanIp}
          </span>
        </div>
      )}

      {d.winboxPort && (
        <div className="flex items-center gap-1 mt-0.5">
          <span className="text-[9px] text-indigo-400 font-mono">
            Winbox: :{d.winboxPort}
          </span>
        </div>
      )}

      {!isPc && d.containerStatus === "running" && (
        <div className="flex items-center gap-1 mt-0.5">
          <span className={`text-[9px] font-mono ${d.rosBooted ? "text-emerald-400" : "text-yellow-500"}`}>
            {d.rosBooted ? "RouterOS ready" : "Booting RouterOS..."}
          </span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-neutral-400 !border-2 !border-neutral-900 hover:!bg-white"
      />
    </div>
  );
}

export default memo(MikroTikNode);
