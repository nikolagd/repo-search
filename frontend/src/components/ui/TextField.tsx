import type { ChangeEvent, HTMLInputTypeAttribute } from "react";

interface TextFieldProps {
  autoComplete?: string;
  className?: string;
  id: string;
  label: string;
  max?: number;
  min?: number;
  multiline?: boolean;
  onValueChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  type?: HTMLInputTypeAttribute;
  value: string | number;
}

export default function TextField({
  autoComplete,
  className = "",
  id,
  label,
  max,
  min,
  multiline = false,
  onValueChange,
  placeholder,
  rows,
  type = "text",
  value,
}: TextFieldProps) {
  function handleChange(event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) {
    onValueChange(event.target.value);
  }

  return (
    <div className={`field ${className}`.trim()}>
      <label htmlFor={id}>{label}</label>
      {multiline ? (
        <textarea
          id={id}
          onChange={handleChange}
          placeholder={placeholder}
          rows={rows}
          value={value}
        />
      ) : (
        <input
          autoComplete={autoComplete}
          id={id}
          max={max}
          min={min}
          onChange={handleChange}
          placeholder={placeholder}
          type={type}
          value={value}
        />
      )}
    </div>
  );
}
