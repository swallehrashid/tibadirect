import type { CaseStatus, TrustLevel, UserRole } from "./enums";
import type { components } from "./schema";

type Schemas = components["schemas"];

type DeepStrict<T> = T extends (infer U)[] ? DeepStrict<U>[] : T extends object ? { [K in keyof T]-?: DeepStrict<T[K]> } : T;

/**
 * A response object as the browser really receives it. The generated types mark every field
 * optional, because the API's schemas do not list required fields, and type computed fields as
 * `unknown`. Responses always carry every field they declare, so this removes the optional marks,
 * and `Over` supplies the concrete type of the computed fields. `Over` may only name fields that
 * exist in the schema, so a renamed or removed field becomes a compile error here.
 */
export type Model<S, Over extends { [K in keyof Over]: K extends keyof S ? unknown : never } = object> = Omit<DeepStrict<S>, keyof Over> & Over;

export type PublicConfig = Model<Schemas["PublicConfig"]>;

export type HospitalSummary = Model<Schemas["HospitalSummary"], { staff_role: "billing_clerk" | "billing_supervisor" }>;
export type User = Model<Schemas["User"], { role: UserRole; hospital: HospitalSummary | null; preferred_language: string | null }>;

export type PublicStats = Model<Schemas["PublicStats"]>;
export type PublicFx = Model<Schemas["PublicFx"]>;
export type HospitalListItem = Model<Schemas["HospitalListItem"]>;

export type PublicCase = Model<
  Schemas["PublicCase"],
  {
    hospital_name: string;
    trust_level: TrustLevel;
    status: CaseStatus;
    photo_url: string | null;
    /** Amounts as decimal strings, "1250.00"; null when the hospital has not confirmed a bill yet. */
    target_kes: string | null;
    pledged_kes: string;
    settled_kes: string;
    usd_goal: string | null;
  }
>;

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}
