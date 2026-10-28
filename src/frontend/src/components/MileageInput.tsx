import { useLayoutEffect, useRef } from 'react';
import { Text } from '@astryxdesign/core/Text';
import { formatMileageEdit, getAdjacentDigitSelection, type MileageUnit } from '../utils/mileage';

export interface MileageInputProps {
  value: string;
  unit: MileageUnit;
  onChange: (value: string) => void;
  onUnitChange: (unit: MileageUnit) => void;
}

/** Format mileage as it is typed while keeping the caret beside the same digits. */
export default function MileageInput({ value, unit, onChange, onUnitChange }: MileageInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pendingSelection = useRef<{ start: number; end: number } | null>(null);

  // Restore the caret after React applies the formatted value, before the browser paints.
  useLayoutEffect(() => {
    const selection = pendingSelection.current;
    if (!selection || !inputRef.current) return;

    inputRef.current.setSelectionRange(selection.start, selection.end);
    pendingSelection.current = null;
  }, [value]);

  /** Apply formatting immediately and restore the selection after the controlled update. */
  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;

    // Format the edit and keep both selection endpoints beside the same digits.
    const edit = formatMileageEdit(input.value, start, end);

    // Update the native input immediately, then save the selection for the controlled
    // rerender.
    input.value = edit.value;
    input.setSelectionRange(edit.selectionStart, edit.selectionEnd);
    pendingSelection.current =
      edit.value === value ? null : { start: edit.selectionStart, end: edit.selectionEnd };
    onChange(edit.value);
  }

  /** Let Backspace and Delete remove a neighboring digit when the caret meets a comma. */
  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Backspace' && event.key !== 'Delete') return;

    const input = event.currentTarget;

    // Select the digit beside a comma so the browser deletes that digit instead of just the
    // separator.
    const selection = getAdjacentDigitSelection(
      input.value,
      input.selectionStart ?? input.value.length,
      input.selectionEnd ?? input.value.length,
      event.key,
    );
    if (selection) input.setSelectionRange(selection.start, selection.end);
  }

  return (
    <section className="mileage-input">
      <label className="mileage-label" htmlFor="mileage-input">
        <Text as="span">Mileage</Text>{' '}
        <Text as="span" type="supporting">
          Optional
        </Text>
      </label>
      <input
        ref={inputRef}
        id="mileage-input"
        aria-label={`Mileage in ${unit}, optional`}
        className="mileage-value-input"
        inputMode="numeric"
        placeholder="150,000"
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
      <select
        aria-label="Mileage unit"
        className="mileage-unit-select"
        value={unit}
        onChange={(event) => onUnitChange(event.currentTarget.value as MileageUnit)}
      >
        <option value="miles">miles</option>
        <option value="kilometers">kilometers</option>
      </select>
    </section>
  );
}
