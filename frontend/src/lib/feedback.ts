/**
 * "Give feedback" opens the person's own mail program with a short form already started. Nothing is
 * sent from the page and nothing about the person is added: only the address of the page they were
 * on (a path such as /c/ab12, never a name or an email), so the team can find what they mean.
 */
export const FEEDBACK_ADDRESS = "feedback@tibadirect.example";

export function feedbackHref(path: string, labels: { subject: string; body: string }): string {
  const page = path.split("?")[0] || "/";
  const body = `${labels.body}\n\n---\n/${page.replace(/^\/+/, "")}`;
  return `mailto:${FEEDBACK_ADDRESS}?subject=${encodeURIComponent(labels.subject)}&body=${encodeURIComponent(body)}`;
}
