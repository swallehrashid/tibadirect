import { useEffect } from "react";

/** Sets the browser tab title for the page; the shell announces route changes to screen readers. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · TibaDirect` : "TibaDirect";
  }, [title]);
}
