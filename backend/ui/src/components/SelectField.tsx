import React from 'react'

type Option = {
  label: string
  value: string
}

type Props = {
  value: string
  options: Option[]
  ariaLabel: string
  className?: string
  onChange: (value: string) => void
}

const SelectField: React.FC<Props> = ({ value, options, ariaLabel, className, onChange }) => (
  <label className={`select-field${className ? ` ${className}` : ''}`}>
    <span className="sr-only">{ariaLabel}</span>
    <select value={value} onChange={(event) => onChange(event.target.value)} className="select-field-control">
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="select-field-icon">
      <path d="m7 10 5 5 5-5" />
    </svg>
  </label>
)

export default SelectField
