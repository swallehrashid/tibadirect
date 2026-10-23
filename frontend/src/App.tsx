import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, unwrap } from "@/api";

export function Home() {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => unwrap(await api.GET("/api/health")),
  });
  const message = health.isPending ? t("status.checking") : health.isError ? t("status.down") : t("status.ok");
  return (
    <main style={{ padding: "2rem" }}>
      <h1>{t("app.name")}</h1>
      <p>{t("app.tagline")}</p>
      <p role="status">{message}</p>
    </main>
  );
}
