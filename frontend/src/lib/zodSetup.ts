import { z } from "zod";

// Zod can compile its validators with new Function(), which the page's Content-Security-Policy
// rightly forbids (no 'unsafe-eval'). Without it validation is the same, and no violation is reported.
z.config({ jitless: true });
