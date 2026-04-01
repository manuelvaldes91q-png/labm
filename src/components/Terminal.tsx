"use client";

import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface TerminalProps {
  nodeName?: string;
  className?: string;
}

export default function Terminal({ nodeName, className }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new XTerm({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "var(--font-geist-mono), monospace",
      theme: {
        background: "#0a0a0a",
        foreground: "#d4d4d4",
        cursor: "#22c55e",
        selectionBackground: "#264f78",
        black: "#1a1a1a",
        red: "#ef4444",
        green: "#22c55e",
        yellow: "#eab308",
        blue: "#3b82f6",
        magenta: "#a855f7",
        cyan: "#06b6d4",
        white: "#d4d4d4",
      },
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);

    term.open(containerRef.current);
    fitAddon.fit();

    xtermRef.current = term;
    fitRef.current = fitAddon;

    term.writeln("\x1b[1;32m┌─────────────────────────────────┐\x1b[0m");
    term.writeln("\x1b[1;32m│   MikroTik CHR Terminal         │\x1b[0m");
    term.writeln("\x1b[1;32m└─────────────────────────────────┘\x1b[0m");
    term.writeln("");

    if (nodeName) {
      term.writeln(`\x1b[36mConnected to:\x1b[0m ${nodeName}`);
    } else {
      term.writeln(
        "\x1b[33mSelect a node to open a terminal session.\x1b[0m",
      );
    }
    term.writeln("");

    term.write("\x1b[32m[admin@MikroTik]\x1b[0m > ");

    let inputBuffer = "";
    const onData = term.onData((data) => {
      for (const ch of data) {
        if (ch === "\r" || ch === "\n") {
          term.writeln("");
          if (inputBuffer.trim()) {
            term.writeln(`\x1b[33mCommand:\x1b[0m ${inputBuffer}`);
          }
          inputBuffer = "";
          term.write("\x1b[32m[admin@MikroTik]\x1b[0m > ");
        } else if (ch === "\x7f") {
          if (inputBuffer.length > 0) {
            inputBuffer = inputBuffer.slice(0, -1);
            term.write("\b \b");
          }
        } else if (ch >= " ") {
          inputBuffer += ch;
          term.write(ch);
        }
      }
    });

    const onResize = () => fitAddon.fit();
    window.addEventListener("resize", onResize);

    return () => {
      onData.dispose();
      window.removeEventListener("resize", onResize);
      term.dispose();
    };
  }, [nodeName]);

  useEffect(() => {
    fitRef.current?.fit();
  }, [nodeName]);

  return (
    <div className={`flex flex-col ${className ?? ""}`}>
      <div className="flex items-center justify-between px-3 py-1.5 border-t border-neutral-800 bg-neutral-900">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500" />
          <span className="text-xs font-mono text-neutral-400">
            {nodeName ?? "No node selected"}
          </span>
        </div>
        <span className="text-[10px] text-neutral-600">xterm.js</span>
      </div>
      <div
        ref={containerRef}
        className="flex-1 min-h-[200px] bg-[#0a0a0a]"
      />
    </div>
  );
}
