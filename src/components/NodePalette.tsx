"use client";

import { type DragEvent } from "react";
import {
  MIKROTIK_MODELS,
  PC_MODELS,
  WAN_MODEL,
  type DeviceModel,
} from "@/lib/mikrotik-models";

function DraggableModel({ model }: { model: DeviceModel }) {
  const onDragStart = (e: DragEvent) => {
    e.dataTransfer.setData("application/reactflow", JSON.stringify(model));
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="cursor-grab active:cursor-grabbing rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-2 transition-colors hover:border-neutral-500 hover:bg-neutral-750"
    >
      <div className="flex items-center gap-2">
        <div
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: model.color }}
        />
        <span className="text-sm font-medium text-neutral-200">
          {model.name}
        </span>
      </div>
      <div className="text-[11px] text-neutral-500 mt-0.5 ml-[18px]">
        {model.series}
        {model.ports > 0 && <> · {model.ports} ports</>}
      </div>
    </div>
  );
}

export default function NodePalette() {
  const routers = MIKROTIK_MODELS.filter((m) => m.node_type === "router");
  const switches = MIKROTIK_MODELS.filter((m) => m.node_type === "switch");

  return (
    <aside className="w-64 border-r border-neutral-800 bg-neutral-900 p-4 flex flex-col gap-4 overflow-y-auto">
      <div>
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-2">
          Routers
        </h2>
        <div className="flex flex-col gap-1.5">
          {routers.map((m) => (
            <DraggableModel key={m.id} model={m} />
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-2">
          Switches
        </h2>
        <div className="flex flex-col gap-1.5">
          {switches.map((m) => (
            <DraggableModel key={m.id} model={m} />
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-2">
          PCs
        </h2>
        <div className="flex flex-col gap-1.5">
          {PC_MODELS.map((m) => (
            <DraggableModel key={m.id} model={m} />
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-2">
          WAN
        </h2>
        <DraggableModel model={WAN_MODEL} />
        <p className="text-[10px] text-neutral-600 mt-1 ml-0.5">
          Each node auto-connects to WAN via NAT on creation.
        </p>
      </div>

      <div className="mt-auto pt-4 border-t border-neutral-800 space-y-2">
        <div>
          <p className="text-[11px] text-neutral-400 font-medium">
            Winbox Access
          </p>
          <p className="text-[10px] text-neutral-600 leading-relaxed">
            Routers and switches get a unique Winbox port. Connect with{" "}
            <code className="text-indigo-400">{"<host-ip>:<port>"}</code>.
          </p>
        </div>
        <p className="text-[10px] text-neutral-600 leading-relaxed">
          Drag a device onto the canvas. Connect two handles to create a veth
          link. PCs can verify configs with ping/curl via WAN.
        </p>
      </div>
    </aside>
  );
}
