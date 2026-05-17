import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: ReactNode;
  variant?: "primary" | "secondary" | "icon";
}

export default function Button({
  children,
  className = "",
  icon,
  type = "button",
  variant = "secondary",
  ...props
}: ButtonProps) {
  const variantClass = variant === "primary"
    ? "primary-action"
    : variant === "icon"
      ? "icon-action"
      : "secondary-action";

  return (
    <button className={`${variantClass} ${className}`.trim()} type={type} {...props}>
      {icon}
      {children}
    </button>
  );
}
