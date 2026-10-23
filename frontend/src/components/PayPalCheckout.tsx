import { PayPalOneTimePaymentButton, PayPalProvider } from "@paypal/react-paypal-js/sdk-v6";
import { captureOrder, createOrder, type NewDonation } from "@/api/donations";

interface Props {
  clientId: string;
  environment: "sandbox" | "production";
  disabled: boolean;
  /** The donation as the form stands right now, or null when it is not valid. */
  getDonation: () => NewDonation | null;
  onCaptured: (receiptToken: string) => void;
  onProblem: (error: unknown) => void;
  onCancel: () => void;
}

/**
 * PayPal's own button (JS SDK v6). We create the order on our server so the amount and case
 * cannot be changed in the browser, and capture it on our server after the donor approves.
 * Loaded only when the app runs against the PayPal sandbox.
 */
export default function PayPalCheckout({ clientId, environment, disabled, getDonation, onCaptured, onProblem, onCancel }: Props) {
  return (
    <PayPalProvider clientId={clientId} environment={environment} components={["paypal-payments"]} pageType="checkout">
      <PayPalOneTimePaymentButton
        type="donate"
        disabled={disabled}
        createOrder={async () => {
          const donation = getDonation();
          if (!donation) throw new Error("The donation form is not complete.");
          try {
            const order = await createOrder(donation);
            return { orderId: order.order_id };
          } catch (error) {
            onProblem(error);
            throw error;
          }
        }}
        onApprove={async ({ orderId }) => {
          try {
            const result = await captureOrder(orderId);
            onCaptured(result.receipt_token);
          } catch (error) {
            onProblem(error);
          }
        }}
        onCancel={onCancel}
        onError={onProblem}
      />
    </PayPalProvider>
  );
}
