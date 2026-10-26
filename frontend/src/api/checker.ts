import { api, unwrap } from "./client";
import type { AppealResult } from "./models";

export const APPEAL_MIN = 20;
export const APPEAL_MAX = 5000;

/** The API answers "no match" as null or as an empty object; the page only wants a real match or null. */
function tidy(raw: AppealResult): AppealResult {
  const match = raw.matched_case as unknown as Record<string, unknown> | null;
  return { ...raw, matched_case: match && typeof match.slug === "string" ? raw.matched_case : null };
}

/** Scores a forwarded message. The text is hashed on the server and never stored. */
export async function checkAppealText(text: string): Promise<AppealResult> {
  return tidy(unwrap(await api.POST("/api/public/checker/appeal", { body: { text } })) as unknown as AppealResult);
}

/** Scores a screenshot of a message: the server reads the text out of the picture and scores that. */
export async function checkAppealImage(image: File): Promise<AppealResult> {
  const result = await api.POST("/api/public/checker/appeal", {
    // The endpoint takes either JSON or a picture; the generated type only knows the JSON form.
    body: { text: "" },
    bodySerializer: () => {
      const form = new FormData();
      form.append("image", image);
      return form;
    },
  });
  return tidy(unwrap(result) as unknown as AppealResult);
}
