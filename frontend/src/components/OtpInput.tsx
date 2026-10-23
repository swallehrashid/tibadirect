import { useEffect, useId, useRef, type ClipboardEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Called once when every digit is filled in. */
  onComplete?: (value: string) => void;
  length?: number;
  disabled?: boolean;
  invalid?: boolean;
  autoFocus?: boolean;
  /** Id of text that describes the field (for example an error message). */
  describedBy?: string;
  label?: string;
}

const digitsOnly = (text: string) => text.replace(/\D/g, "");

/**
 * One box per digit. Typing moves on, backspace moves back, arrows move around, and pasting a
 * whole code fills every box. The first box carries autocomplete="one-time-code" so phones can
 * offer the code from a message.
 */
export function OtpInput({ value, onChange, onComplete, length = 6, disabled = false, invalid = false, autoFocus = false, describedBy, label }: Props) {
  const { t } = useTranslation();
  const groupId = useId();
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const completed = useRef<string | null>(null);

  useEffect(() => {
    if (value.length === length && completed.current !== value) {
      completed.current = value;
      onComplete?.(value);
    }
    if (value.length < length) completed.current = null;
  }, [value, length, onComplete]);

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus();
  }, [autoFocus]);

  const focusBox = (index: number) => refs.current[Math.max(0, Math.min(length - 1, index))]?.focus();

  function setDigits(start: number, text: string) {
    const digits = digitsOnly(text);
    if (!digits) return;
    const next = (value.slice(0, start) + digits + value.slice(start + digits.length)).slice(0, length);
    onChange(next);
    focusBox(Math.min(start + digits.length, length - 1));
  }

  function onKeyDown(index: number, event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace") {
      event.preventDefault();
      if (value[index]) {
        onChange(value.slice(0, index) + value.slice(index + 1));
      } else if (index > 0) {
        onChange(value.slice(0, index - 1) + value.slice(index));
        focusBox(index - 1);
      }
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      focusBox(index - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      focusBox(index + 1);
    }
  }

  function onPaste(index: number, event: ClipboardEvent<HTMLInputElement>) {
    event.preventDefault();
    setDigits(index, event.clipboardData.getData("text"));
  }

  return (
    <div role="group" aria-labelledby={`${groupId}-label`} aria-describedby={describedBy} className="space-y-2">
      <span id={`${groupId}-label`} className="block text-sm font-semibold">
        {label ?? t("otp.label")}
      </span>
      <div className="flex gap-2">
        {Array.from({ length }, (_, index) => (
          <input
            key={index}
            ref={(element) => {
              refs.current[index] = element;
            }}
            value={value[index] ?? ""}
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete={index === 0 ? "one-time-code" : "off"}
            maxLength={length}
            disabled={disabled}
            aria-label={t("otp.digit", { n: index + 1 })}
            aria-invalid={invalid || undefined}
            onFocus={(event) => event.target.select()}
            onChange={(event) => setDigits(index, event.target.value)}
            onKeyDown={(event) => onKeyDown(index, event)}
            onPaste={(event) => onPaste(index, event)}
            className={cn(
              "h-12 w-full min-w-0 max-w-12 rounded-md border bg-card text-center font-heading text-xl font-bold disabled:opacity-60",
              invalid ? "border-danger-600" : "border-input",
            )}
          />
        ))}
      </div>
      <p className="sr-only" aria-live="polite">
        {value.length === length ? t("otp.entered") : t("otp.incomplete", { count: value.length })}
      </p>
    </div>
  );
}
