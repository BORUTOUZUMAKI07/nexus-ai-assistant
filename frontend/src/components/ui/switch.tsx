"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const switchVariants = cva(
  "inline-flex items-center cursor-pointer rounded-full border-[var(--border-subtle)] bg-transparent text-[var(--text-secondary)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      checked: {
        true: "bg-[var(--accent)] text-[var(--accent-foreground)]",
        false: "",
      },
    },
    defaultVariants: {
      checked: false,
    },
  }
);

export interface SwitchProps
  extends React.HTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof switchVariants> {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ className, checked, onCheckedChange, ...props }, ref) => {
    const handleClick = () => {
      onCheckedChange?.(!checked);
    };
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={handleClick}
        ref={ref}
        className={cn(switchVariants({ checked }), className)}
        {...props}
      >
        <span className="shrink-0 w-6 h-6 rounded-full bg-[var(--bg-main)] shadow-sm transition-transform">
          {checked ? "translate-x-6" : "translate-x-1"}
        </span>
      </button>
    );
  }
);
Switch.displayName = "Switch";

export { Switch, switchVariants, cn };
