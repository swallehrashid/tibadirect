import { minorToDecimal } from "@/lib/money";
import { api, unwrap } from "./client";
import type { ExcessChoice } from "./enums";
import type { OrderCreated, Receipt } from "./models";

export interface NewDonation {
  caseSlug: string;
  amountUsdMinor: number;
  excessChoice: ExcessChoice;
  donorDisplayName: string | null;
  isAnonymous: boolean;
}

/** Starts a donation: the API creates the PayPal order the donor then approves in PayPal's window. */
export async function createOrder(donation: NewDonation): Promise<OrderCreated> {
  const result = await api.POST("/api/donations/orders", {
    body: {
      case_slug: donation.caseSlug,
      // The generated type says number, but the API takes a decimal string and keeps it exact.
      amount_usd: minorToDecimal(donation.amountUsdMinor) as unknown as number,
      excess_choice: donation.excessChoice,
      donor_display_name: donation.donorDisplayName,
      is_anonymous: donation.isAnonymous,
    },
  });
  return unwrap(result) as OrderCreated;
}

/** Takes the money once the donor has approved. Repeating it returns the same donation. */
export async function captureOrder(orderId: string): Promise<{ receipt_token: string; donation: Receipt }> {
  const result = await api.POST("/api/donations/orders/{order_id}/capture", { params: { path: { order_id: orderId } } });
  return unwrap(result) as unknown as { receipt_token: string; donation: Receipt };
}
