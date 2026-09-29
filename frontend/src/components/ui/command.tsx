"use client";

import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { Dialog, DialogOverlay } from "@/components/ui/dialog";

const Command = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive>
>(({ className, ...props }, ref) => (
  <CommandPrimitive
    ref={ref}
    className={cn("flex h-full w-full flex-col overflow-hidden text-[var(--text-primary)]", className)}
    {...props}
  />
));
Command.displayName = "Command";

interface CommandDialogProps extends React.ComponentProps<typeof Dialog> {
  label?: string;
}

const CommandDialog = ({ children, label = "Command palette", ...props }: CommandDialogProps) => (
  <Dialog {...props}>
    <DialogPrimitive.Portal>
      <DialogOverlay />
      <DialogPrimitive.Content
        aria-label={label}
        className="nx-palette fixed left-1/2 top-[15vh] z-50 w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] shadow-float focus:outline-none"
      >
        <DialogPrimitive.Title className="sr-only">{label}</DialogPrimitive.Title>
        <DialogPrimitive.Description className="sr-only">
          Search conversations, jump to a section, switch model or change appearance.
        </DialogPrimitive.Description>
        <Command
          loop
          className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[var(--text-muted)]"
        >
          {children}
        </Command>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </Dialog>
);

const CommandInput = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Input>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Input>
>(({ className, ...props }, ref) => (
  <div className="flex items-center gap-2.5 border-b border-[var(--border-subtle)] px-4">
    <Search className="h-4 w-4 shrink-0 text-[var(--text-muted)]" aria-hidden="true" />
    <CommandPrimitive.Input
      ref={ref}
      className={cn(
        "h-12 w-full bg-transparent text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-faint)]",
        className,
      )}
      {...props}
    />
    <kbd className="rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-faint)]">
      esc
    </kbd>
  </div>
));
CommandInput.displayName = "CommandInput";

const CommandList = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.List>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.List
    ref={ref}
    className={cn("max-h-[min(24rem,60vh)] overflow-y-auto overflow-x-hidden p-1.5", className)}
    {...props}
  />
));
CommandList.displayName = "CommandList";

const CommandEmpty = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Empty>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>
>((props, ref) => (
  <CommandPrimitive.Empty
    ref={ref}
    className="px-3 py-8 text-center text-sm text-[var(--text-muted)]"
    {...props}
  />
));
CommandEmpty.displayName = "CommandEmpty";

const CommandGroup = CommandPrimitive.Group;

const CommandItem = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Item>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Item
    ref={ref}
    className={cn(
      "flex cursor-pointer select-none items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-[var(--text-secondary)] outline-none",
      "data-[selected=true]:bg-[var(--accent-soft)] data-[selected=true]:text-[var(--text-primary)]",
      "data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50",
      className,
    )}
    {...props}
  />
));
CommandItem.displayName = "CommandItem";

const CommandShortcut = ({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) => (
  <span
    className={cn("ml-auto font-mono text-[10px] text-[var(--text-faint)]", className)}
    {...props}
  />
);

export {
  Command,
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandShortcut,
};
