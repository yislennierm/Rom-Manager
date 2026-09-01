import React from 'react'

type Props = {
  value: string
  placeholder: string
  className?: string
  ariaLabel?: string
  onChange: (value: string) => void
  onSubmit?: (value: string) => void
}

const SearchField: React.FC<Props> = ({ value, placeholder, className, ariaLabel, onChange, onSubmit }) => (
  <form
    className={`search-field${className ? ` ${className}` : ''}`}
    role="search"
    onSubmit={(event) => {
      event.preventDefault()
      onSubmit?.(value)
    }}
  >
    <input
      type="search"
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel || placeholder}
      className="search-field-input"
      onChange={(event) => onChange(event.target.value)}
    />
    <button type="submit" className="search-field-button" aria-label="Search">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="m21 21-4.35-4.35m2.35-5.15a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z" />
      </svg>
    </button>
  </form>
)

export default SearchField
