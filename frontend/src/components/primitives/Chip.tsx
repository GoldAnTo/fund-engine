import type { ReactNode } from "react";

type Tone =
  | "neutral"
  | "moss"
  | "ochre"
  | "clay"
  | "mineral"
  | "iris"
  | "amber";

interface Props {
  tone?: Tone;
  children: ReactNode;
  bordered?: boolean;
  outlined?: boolean;
  size?: "xs" | "sm";
  title?: string;
}

const TONE_CLASS: Record<Tone, string> = {
  neutral: "chip--neutral",
  moss: "chip--moss",
  ochre: "chip--ochre",
  clay: "chip--clay",
  mineral: "chip--mineral",
  iris: "chip--iris",
  amber: "chip--amber",
};

export function Chip({
  tone = "neutral",
  bordered = false,
  outlined = false,
  size = "xs",
  children,
  title,
}: Props) {
  return (
    <span
      className={`chip chip--${size} ${TONE_CLASS[tone]}${
        bordered ? " chip--bordered" : ""
      }${outlined ? " chip--outlined" : ""}`}
      title={title}
    >
      {children}
    </span>
  );
}