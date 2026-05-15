"use client";

import { useEffect } from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastType = "success" | "error" | "info";

interface ToastProps {
  message: string;
  type: ToastType;
  onClose: () => void;
}

const config = {
  success: { bg: "bg-emerald-600", Icon: CheckCircle2 },
  error:   { bg: "bg-rose-600",    Icon: AlertCircle },
  info:    { bg: "bg-blue-600",    Icon: Info },
};

export function Toast({ message, type, onClose }: ToastProps) {
  const { bg, Icon } = config[type];

  useEffect(() => {
    const t = setTimeout(onClose, 5000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div
      className={cn(
        "fixed top-4 right-4 z-50 flex items-center gap-3 rounded-xl px-5 py-3 shadow-lg",
        "text-white text-sm font-medium animate-slide-in",
        bg
      )}
    >
      <Icon size={16} className="shrink-0" />
      <span>{message}</span>
      <button onClick={onClose} className="text-white/70 hover:text-white ml-1">
        <X size={14} />
      </button>
    </div>
  );
}
