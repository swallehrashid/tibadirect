import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useHospitals } from "@/api";
import { familyApi, useCaseMutation } from "@/api/family";
import { ErrorState } from "@/components/ErrorState";
import { HospitalPicker } from "@/components/HospitalPicker";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { firstFieldError, serverFieldMessage } from "@/lib/fieldErrors";
import type { StepProps } from "./types";

const REGION = /^[\p{L}][\p{L} '’.-]*$/u;
const PATIENT_REF = /^[A-Za-z0-9][A-Za-z0-9 /._#-]*$/;

export function HospitalStep({ draft, onNext }: StepProps) {
  const { t } = useTranslation();
  const hospitals = useHospitals();

  const schema = z.object({
    hospital_id: z.string().min(1, t("wizard.errors.hospital")),
    alias: z.string().trim().min(2, t("wizard.errors.aliasLength")).max(40, t("wizard.errors.aliasLength")),
    region: z.string().trim().min(2, t("wizard.errors.region")).max(40, t("wizard.errors.region")).regex(REGION, t("wizard.errors.region")),
    patient_reference: z.string().trim().min(3, t("wizard.errors.patientReference")).max(60, t("wizard.errors.patientReference")).regex(PATIENT_REF, t("wizard.errors.patientReference")),
    is_minor: z.boolean(),
  });
  type Values = z.infer<typeof schema>;

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { hospital_id: "", alias: "", region: "", patient_reference: "", is_minor: false },
  });

  // Fill the form once when an existing draft arrives.
  useEffect(() => {
    if (draft) form.reset({ hospital_id: draft.hospital.id, alias: draft.alias, region: draft.region, patient_reference: draft.patient_reference, is_minor: draft.is_minor });
  }, [draft, form]);

  const save = useCaseMutation(async (values: Values & { caseId: string }) => {
    // The hospital cannot be changed once the draft exists.
    return familyApi.update(values.caseId, { alias: values.alias, region: values.region, patient_reference: values.patient_reference, is_minor: values.is_minor });
  });
  const create = useMutation({ mutationFn: (values: Values) => familyApi.create(values) });
  const busy = create.isPending || save.isPending;
  const error = create.error ?? save.error;

  async function submit(values: Values) {
    create.reset();
    save.reset();
    try {
      if (draft) {
        await save.mutateAsync({ ...values, caseId: draft.id });
        onNext(draft.id);
      } else {
        const created = await create.mutateAsync(values);
        onNext(created.id);
      }
    } catch (failure) {
      const field = firstFieldError(failure);
      const known = ["hospital_id", "alias", "region", "patient_reference", "is_minor"] as const;
      if (field && (known as readonly string[]).includes(field.field)) {
        form.setError(field.field as (typeof known)[number], { message: serverFieldMessage(failure, t) ?? field.message });
      }
    }
  }

  const errors = form.formState.errors;
  const alias = useWatch({ control: form.control, name: "alias" });
  const region = useWatch({ control: form.control, name: "region" });

  if (hospitals.isPending) return <Skeleton className="h-64" />;
  if (hospitals.isError) return <ErrorState error={hospitals.error} onRetry={() => void hospitals.refetch()} />;

  return (
    <form onSubmit={form.handleSubmit(submit)} noValidate className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-heading text-xl font-bold">{t("wizard.hospital.title")}</h2>
        <p className="text-muted-foreground">{t("wizard.hospital.intro")}</p>
      </div>

      <div className="space-y-2">
        <Controller
          control={form.control}
          name="hospital_id"
          render={({ field }) => (
            <HospitalPicker hospitals={hospitals.data} value={field.value} onChange={field.onChange} disabled={Boolean(draft) || busy} invalid={Boolean(errors.hospital_id)} describedBy={errors.hospital_id ? "hospital-error" : undefined} />
          )}
        />
        {draft ? <p className="text-sm text-muted-foreground">{t("wizard.hospital.locked")}</p> : null}
        {errors.hospital_id ? (
          <p id="hospital-error" role="alert" className="text-sm font-semibold text-danger-700">
            {errors.hospital_id.message}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor="alias">{t("wizard.hospital.alias")}</Label>
        <Input id="alias" maxLength={40} autoComplete="off" aria-invalid={errors.alias ? true : undefined} aria-describedby="alias-hint" {...form.register("alias")} />
        <p id="alias-hint" className="text-sm text-muted-foreground">
          {alias.trim().length >= 2 && region.trim().length >= 2 ? t("wizard.hospital.aliasPreview", { alias: alias.trim(), region: region.trim() }) : t("wizard.hospital.aliasHint")}
        </p>
        {errors.alias ? (
          <p role="alert" className="text-sm font-semibold text-danger-700">
            {errors.alias.message}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor="region">{t("wizard.hospital.region")}</Label>
        <Input id="region" maxLength={40} autoComplete="off" aria-invalid={errors.region ? true : undefined} {...form.register("region")} />
        {errors.region ? (
          <p role="alert" className="text-sm font-semibold text-danger-700">
            {errors.region.message}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor="patient_reference">{t("wizard.hospital.patientReference")}</Label>
        <Input id="patient_reference" maxLength={60} autoComplete="off" aria-invalid={errors.patient_reference ? true : undefined} aria-describedby="patient-ref-hint" {...form.register("patient_reference")} />
        <p id="patient-ref-hint" className="text-sm text-muted-foreground">
          {t("wizard.hospital.patientReferenceHint")}
        </p>
        {errors.patient_reference ? (
          <p role="alert" className="text-sm font-semibold text-danger-700">
            {errors.patient_reference.message}
          </p>
        ) : null}
      </div>

      <div className="flex items-start gap-3">
        <Controller control={form.control} name="is_minor" render={({ field }) => <Checkbox id="is_minor" checked={field.value} onCheckedChange={(value) => field.onChange(value === true)} />} />
        <Label htmlFor="is_minor" className="space-y-0.5 pt-0.5 font-normal leading-snug">
          <span className="block font-semibold">{t("wizard.hospital.minor")}</span>
          <span className="block text-sm text-muted-foreground">{t("wizard.hospital.minorHint")}</span>
        </Label>
      </div>

      {error && !firstFieldError(error) ? (
        <Alert tone="danger">
          <AlertDescription>{t("error.body")}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex justify-end">
        <Button type="submit" size="lg" disabled={busy}>
          {busy ? t("common.loading") : t("common.next")}
        </Button>
      </div>
    </form>
  );
}
