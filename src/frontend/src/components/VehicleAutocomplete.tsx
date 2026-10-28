import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import { Text } from '@astryxdesign/core/Text';
import type { CatalogMake, CatalogMakeDetail, CatalogSelection } from '../models/catalog';
import { AsyncRequestSequence, catalogClient } from '../services/catalog';
import { makeForQuery, suggestionsFor, type VehicleSuggestion } from '../utils/vehicle-selection';

/** Guide a year/make/model search using catalog records and keyboard-accessible suggestions. */
export function VehicleAutocomplete({
  value,
  onValueChange,
  onSelectionChange,
  onCatalogReady,
}: {
  value: string;
  onValueChange: (value: string) => void;
  onSelectionChange: (selection: CatalogSelection | undefined) => void;
  onCatalogReady: (ready: boolean) => void;
}) {
  const [makes, setMakes] = useState<CatalogMake[]>([]);
  const [makesState, setMakesState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [detail, setDetail] = useState<CatalogMakeDetail>();
  const [detailState, setDetailState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [isOpen, setIsOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);

  // Keep the highlighted option separate from selection; only choosing a model completes the
  // vehicle.
  const inputRef = useRef<HTMLInputElement>(null);
  const detailSequence = useRef(new AsyncRequestSequence());
  const listId = 'vehicle-suggestions';
  const resolvedMake = makesState === 'ready' ? makeForQuery(value, makes) : undefined;

  // Load the shared catalog first and keep submission disabled until it is available.
  /** Load available makes and report whether the catalog is ready for a search. */
  async function loadMakes() {
    setMakesState('loading');
    onCatalogReady(false);

    try {
      setMakes(await catalogClient.getMakes());
      setMakesState('ready');
      onCatalogReady(true);
    } catch {
      setMakesState('error');
      onCatalogReady(false);
    }
  }

  useEffect(() => {
    void loadMakes();
  }, []);

  useEffect(() => {
    // Invalidate the previous make lookup whenever the resolved make or retry attempt
    // changes.
    const requestSequence = detailSequence.current.begin();
    if (!resolvedMake) {
      setDetail(undefined);
      setDetailState('idle');

      return;
    }

    setDetail(undefined);
    setDetailState('loading');
    catalogClient
      .getMakeDetail(resolvedMake.id)
      .then((result) => {
        if (detailSequence.current.isCurrent(requestSequence)) {
          setDetail(result);
          setDetailState('ready');
        }
      })
      .catch(() => {
        if (detailSequence.current.isCurrent(requestSequence)) setDetailState('error');
      });

    // Mark this lookup stale on cleanup so a response for the previous make cannot update the
    // menu.
    return () => {
      detailSequence.current.begin();
    };
  }, [resolvedMake?.id, detailAttempt]);

  // Build suggestions from the current text and matching make details, retaining catalog IDs.
  const suggestions = useMemo(() => suggestionsFor(value, makes, detail), [value, makes, detail]);
  const showRetry = makesState === 'error' || detailState === 'error';
  const activeIndex = highlighted >= 0 && highlighted < suggestions.length ? highlighted : -1;

  useEffect(() => {
    if (highlighted >= suggestions.length) setHighlighted(-1);
  }, [highlighted, suggestions.length]);

  /** Commit the chosen label and its catalog selection, then close the suggestions. */
  function select(suggestion: VehicleSuggestion) {
    onValueChange(suggestion.label);
    onSelectionChange(suggestion.selection);
    setIsOpen(false);
    setHighlighted(-1);
  }

  /** Treat typed text as an incomplete selection until a full model is chosen. */
  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    onValueChange(event.target.value);

    // Typing invalidates the prior vehicle selection until a complete suggestion is chosen.
    onSelectionChange(undefined);
    setIsOpen(true);
    setHighlighted(-1);
  }

  /** Move through suggestions with arrow keys, choose with Enter, and close with Escape. */
  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Keep arrow-key navigation inside the menu and let Enter commit the highlighted
    // suggestion.
    switch (event.key) {
      case 'ArrowDown':
        if (suggestions.length === 0) return;

        event.preventDefault();
        setIsOpen(true);
        setHighlighted((index) => (index + 1) % suggestions.length);
        return;

      case 'ArrowUp':
        if (suggestions.length === 0) return;

        event.preventDefault();
        setIsOpen(true);
        setHighlighted((index) => (index <= 0 ? suggestions.length - 1 : index - 1));
        return;

      case 'Enter': {
        if (!isOpen || highlighted < 0) return;

        const suggestion = suggestions[highlighted];
        if (!suggestion) return;

        event.preventDefault();
        select(suggestion);
        return;
      }

      case 'Escape':
        setIsOpen(false);
        setHighlighted(-1);
        return;
    }
  }

  return (
    <section className="vehicle-autocomplete">
      <label className="vehicle-field-label" htmlFor="vehicle-query">
        Year, make, and model{' '}
        <Text as="span" type="supporting">
          Required
        </Text>
      </label>
      <input
        ref={inputRef}
        id="vehicle-query"
        className="vehicle-query-input"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={isOpen}
        aria-controls={listId}
        aria-activedescendant={isOpen && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        aria-required="true"
        aria-describedby="vehicle-guidance"
        autoComplete="off"
        placeholder="2015 Toyota Camry"
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onFocus={() => setIsOpen(true)}
        onBlur={() => window.setTimeout(() => setIsOpen(false), 100)}
      />
      <Text as="span" id="vehicle-guidance" className="sr-only">
        Type a year, then choose a make, then choose a model. For example, 2015 Toyota Camry.
      </Text>
      {isOpen && (
        <ul
          className="vehicle-suggestions"
          id={listId}
          role="listbox"
          aria-label="Vehicle suggestions"
        >
          {suggestions.map((suggestion, index) => (
            <li
              key={suggestion.label}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={activeIndex === index}
              className={activeIndex === index ? 'is-highlighted' : undefined}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(suggestion)}
            >
              {suggestion.label}
            </li>
          ))}
          {showRetry && (
            <li className="catalog-status" role="status">
              Catalog unavailable.{' '}
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  if (makesState === 'error') void loadMakes();
                  else setDetailAttempt((attempt) => attempt + 1);
                }}
              >
                Retry
              </button>
            </li>
          )}
          {!showRetry && makesState === 'loading' && (
            <li className="catalog-status" role="status">
              Loading vehicle catalog…
            </li>
          )}
          {!showRetry && makesState === 'ready' && !value.trim() && (
            <li className="catalog-status" role="status">
              Type a 4-digit year to start.
            </li>
          )}
          {!showRetry && makesState === 'ready' && resolvedMake && detailState === 'loading' && (
            <li className="catalog-status" role="status">
              Loading models for {resolvedMake.name}…
            </li>
          )}
          {!showRetry && detailState === 'ready' && suggestions.length === 0 && (
            <li className="catalog-status" role="status">
              No matching vehicles for that year and make.
            </li>
          )}
          {!showRetry &&
            makesState === 'ready' &&
            /^\d{4}/.test(value) &&
            !resolvedMake &&
            suggestions.length === 0 && (
              <li className="catalog-status" role="status">
                No matching makes. Try another name.
              </li>
            )}
        </ul>
      )}
      {showRetry && !isOpen && (
        <p className="catalog-inline-error" role="alert">
          Vehicle catalog unavailable.{' '}
          <button
            type="button"
            onClick={() => {
              if (makesState === 'error') void loadMakes();
              else setDetailAttempt((attempt) => attempt + 1);

              inputRef.current?.focus();
            }}
          >
            Retry
          </button>
        </p>
      )}
    </section>
  );
}
