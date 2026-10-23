import type { UserRole } from "./enums";
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
