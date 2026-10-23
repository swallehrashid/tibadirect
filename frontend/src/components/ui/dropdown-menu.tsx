import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils";

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;

export const DropdownMenuContent = forwardRef<ElementRef<typeof MenuPrimitive.Content>, ComponentPropsWithoutRef<typeof MenuPrimitive.Content>>(
  ({ className, sideOffset = 6, ...props }, ref) => (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        className={cn("z-50 min-w-44 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-card", className)}
        {...props}
      />
    </MenuPrimitive.Portal>
  ),
);
DropdownMenuContent.displayName = "DropdownMenuContent";

export const DropdownMenuItem = forwardRef<ElementRef<typeof MenuPrimitive.Item>, ComponentPropsWithoutRef<typeof MenuPrimitive.Item>>(
  ({ className, ...props }, ref) => (
    <MenuPrimitive.Item
      ref={ref}
      className={cn("flex min-h-10 cursor-default select-none items-center gap-2 rounded-sm px-3 text-sm outline-none data-[disabled]:opacity-50 data-[highlighted]:bg-tiba-100", className)}
      {...props}
    />
  ),
);
DropdownMenuItem.displayName = "DropdownMenuItem";

export const DropdownMenuLabel = forwardRef<ElementRef<typeof MenuPrimitive.Label>, ComponentPropsWithoutRef<typeof MenuPrimitive.Label>>(
  ({ className, ...props }, ref) => (
    <MenuPrimitive.Label ref={ref} className={cn("px-3 py-2 text-xs font-semibold text-muted-foreground", className)} {...props} />
  ),
);
DropdownMenuLabel.displayName = "DropdownMenuLabel";

export const DropdownMenuSeparator = forwardRef<ElementRef<typeof MenuPrimitive.Separator>, ComponentPropsWithoutRef<typeof MenuPrimitive.Separator>>(
  ({ className, ...props }, ref) => <MenuPrimitive.Separator ref={ref} className={cn("-mx-1 my-1 h-px bg-border", className)} {...props} />,
);
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";
