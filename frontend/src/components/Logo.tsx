import { cn } from "@/lib/utils";

/** The TibaDirect mark: a medical cross whose upper-right corner carries the teal "confirmed" dot. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8", className)} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--tiba-blue-600)" />
      <path d="M16 8v16M8 16h16" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
      <circle cx="25" cy="7" r="4.5" fill="var(--uzima-600)" stroke="#fff" strokeWidth="1.5" />
    </svg>
  );
}

/** Mark plus wordmark. "Tiba" is Swahili for treatment. */
export function Logo({ className, onDark = false }: { className?: string; onDark?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-heading text-xl font-extrabold tracking-tight", className)}>
      <LogoMark />
      <span className={onDark ? "text-white" : "text-tiba-900"}>
        Tiba<span className={onDark ? "text-sky-500" : "text-primary"}>Direct</span>
      </span>
    </span>
  );
}
