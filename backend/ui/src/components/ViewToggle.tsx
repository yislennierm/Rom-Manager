import React from 'react'

type ViewMode = 'list' | 'cards'

type Props = {
  value: ViewMode
  onChange: (value: ViewMode) => void
}

const options: Array<{ label: string; value: ViewMode }> = [
  { label: 'List', value: 'list' },
  { label: 'Cards', value: 'cards' },
]

const ViewToggle: React.FC<Props> = ({ value, onChange }) => (
  <div className="view-toggle" role="radiogroup" aria-label="ROM view mode">
    {options.map((option) => (
      <button
        key={option.value}
        type="button"
        role="radio"
        aria-checked={value === option.value}
        className={`view-toggle-button${value === option.value ? ' is-active' : ''}`}
        onClick={() => onChange(option.value)}
      >
        {option.label}
      </button>
    ))}
  </div>
)

export default ViewToggle
