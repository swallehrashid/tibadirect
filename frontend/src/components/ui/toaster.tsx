import { Toaster as Sonner } from "sonner";

export { toast } from "sonner";

/** Toasts for success and error messages; announced politely to screen readers by sonner. */
export function Toaster() {
  return (
    <Sonner
      position="top-center"
      closeButton
      toastOptions={{
        classNames: {
          toast: "!rounded-lg !border !border-border !bg-card !text-foreground !shadow-card !font-sans",
          description: "!text-muted-foreground",
        },
      }}
    />
  );
}
