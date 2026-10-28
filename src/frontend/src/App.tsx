import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { Heading } from '@astryxdesign/core/Heading';
import { Icon } from '@astryxdesign/core/Icon';
import { Section } from '@astryxdesign/core/Section';
import { Text } from '@astryxdesign/core/Text';
import { VehicleAutocomplete } from './components/VehicleAutocomplete';
import type { CatalogSelection } from './models/catalog';
import { useHeroTransition } from './hooks/useHeroTransition';
import { HeroHeadline } from './components/HeroHeadline';
import { ValuationSkeleton } from './components/ValuationSkeleton';
import {
  defaultMileageUnit,
  fromMiles,
  parseMileage,
  toMiles,
  formatMileage,
  type MileageUnit,
} from './utils/mileage';
import MileageInput from './components/MileageInput';
import { getSavedMileageUnit, saveMileageUnit } from './services/mileage-preference';
import { fetchModelA, type ModelAResult, type ModelAValuation } from './services/model-a';
import { VehicleResults } from './components/VehicleResults';
import './styles/styles.css';

/** Coordinate the search inputs, request state, and the last submitted valuation. */
export default function App() {
  // Keep editable inputs separate from the submitted vehicle and mileage shown in the
  // results.
  const [vehicleQuery, setVehicleQuery] = useState('');
  const [mileageInput, setMileageInput] = useState('');
  const [mileageUnit, setMileageUnit] = useState<MileageUnit>(
    () =>
      getSavedMileageUnit() ??
      defaultMileageUnit(typeof navigator === 'undefined' ? [] : navigator.languages),
  );
  const [enteredMileageMiles, setEnteredMileageMiles] = useState<number | undefined | null>();
  const mileageInteraction = useRef(false);
  const unitWasSelected = useRef(getSavedMileageUnit() !== undefined);
  const [catalogSelection, setCatalogSelection] = useState<CatalogSelection>();
  const [submittedSelection, setSubmittedSelection] = useState<CatalogSelection>();
  const [activeMileage, setActiveMileage] = useState<number>();
  const [catalogReady, setCatalogReady] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [valuation, setValuation] = useState<ModelAValuation>();
  const [insufficient, setInsufficient] =
    useState<Extract<ModelAResult, { status: 'insufficient' }>>();

  // Track both cancellation and request order so a late response cannot replace newer
  // results.
  const pendingRequest = useRef<AbortController | undefined>(undefined);
  const [docked, setDocked] = useState(false);
  const [queryState, setQueryState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const requestSequence = useRef(0);

  // Invalidate requests on unmount before aborting the active fetch.
  useEffect(
    () => () => {
      requestSequence.current += 1;
      pendingRequest.current?.abort();
    },
    [],
  );
  const heroMotion = useHeroTransition(docked);
  const busy = queryState === 'loading' || heroMotion.moving;

  // Only use the country hint before the user starts entering mileage or chooses a unit.
  useEffect(() => {
    if (unitWasSelected.current || mileageInteraction.current) return;

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1800);

    void fetch('/api/location', { signal: controller.signal })
      .then((response) =>
        response.ok ? (response.json() as Promise<{ countryCode?: string | null }>) : undefined,
      )
      .then((location) => {
        if (!location || unitWasSelected.current || mileageInteraction.current) return;

        const unit = defaultMileageUnit(
          typeof navigator === 'undefined' ? [] : navigator.languages,
          location.countryCode,
        );
        setMileageUnit(unit);
      })
      .catch(() => undefined)
      .finally(() => window.clearTimeout(timeout));

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  /** Parse edited mileage and cancel any estimate for the previous input. */
  function handleMileageChange(value: string) {
    mileageInteraction.current = true;
    setMileageInput(value);

    // Keep the entered distance in miles so switching display units does not compound
    // rounding errors.
    const parsed = parseMileage(value);
    setEnteredMileageMiles(
      parsed === undefined || parsed === null ? parsed : toMiles(parsed, mileageUnit),
    );
    setError(undefined);
    setQueryState(valuation || insufficient ? 'success' : 'idle');

    // Editing the input makes any pending result stale, even if cancellation arrives too
    // late.
    requestSequence.current += 1;
    pendingRequest.current?.abort();
  }

  /** Change the display unit while preserving the underlying entered distance. */
  function handleMileageUnitChange(unit: MileageUnit) {
    mileageInteraction.current = true;
    unitWasSelected.current = true;
    saveMileageUnit(unit);
    setError(undefined);

    // Convert from the stored distance rather than repeatedly converting the displayed,
    // rounded text.
    if (typeof enteredMileageMiles === 'number') {
      setMileageInput(formatMileage(fromMiles(enteredMileageMiles, unit)));
    }

    setMileageUnit(unit);
  }

  /** Clear the catalog selection and invalidate requests when the vehicle text changes. */
  function handleVehicleText(value: string) {
    requestSequence.current += 1;
    pendingRequest.current?.abort();
    setQueryState(valuation || insufficient ? 'success' : 'idle');
    setVehicleQuery(value);
    setCatalogSelection(undefined);
    setError(undefined);
  }

  /** Validate the selected vehicle and optional mileage before requesting an estimate. */
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // Require a catalog selection that still matches the visible text before sending a
    // valuation request.
    const mileage = enteredMileageMiles;
    if (
      !catalogSelection ||
      vehicleQuery !==
        `${catalogSelection.modelYear.year} ${catalogSelection.make.name} ${catalogSelection.model.name}`
    ) {
      setError('Choose a year, make, and model from the vehicle suggestions.');

      return;
    }

    if (mileage === null) {
      setError(`Enter a whole number of ${mileageUnit}, such as 150,000, or leave it blank.`);

      return;
    }

    if (mileage !== undefined && mileage > 10_000_000) {
      setError('Mileage is too large to value. Check the amount and selected unit.');

      return;
    }

    void loadValuation(catalogSelection, mileage);
  }

  /** Load one estimate and ignore results that arrive after a newer request or edit. */
  async function loadValuation(selection: CatalogSelection, mileage: number | undefined) {
    // Replace the previous request and give this one its own timeout and sequence number.
    const sequence = ++requestSequence.current;
    pendingRequest.current?.abort();
    const controller = new AbortController();
    pendingRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 20_000);

    // Capture the starting layout before docking the search, then clear results while
    // loading.
    if (!docked) heroMotion.capture();

    setDocked(true);
    setSubmittedSelection(selection);
    setActiveMileage(mileage);
    setValuation(undefined);
    setInsufficient(undefined);
    setQueryState('loading');
    setError(undefined);

    try {
      const result = await fetchModelA(
        {
          year: selection.modelYear.year,
          make: selection.make.name,
          model: selection.model.name,
          mileageMiles: mileage,
        },
        controller.signal,
      );

      // Ignore responses for inputs that have changed since this request started.
      if (sequence !== requestSequence.current) return;

      if (result.status === 'success') setValuation(result.valuation);

      setInsufficient(result.status === 'insufficient' ? result : undefined);
      setQueryState('success');
    } catch {
      if (sequence !== requestSequence.current) return;

      setQueryState('error');
    } finally {
      // Always release this request's timer, without clearing a newer request's controller.
      window.clearTimeout(timeout);
      if (pendingRequest.current === controller) pendingRequest.current = undefined;
    }
  }

  /** Return to the entry layout and clear the previous search and result. */
  function clearVehicle() {
    // Capture the current layout before returning to the empty search, then clear the
    // previous result.
    heroMotion.capture();
    setDocked(false);
    handleVehicleText('');
    setMileageInput('');
    setEnteredMileageMiles(undefined);
    setCatalogSelection(undefined);
    setSubmittedSelection(undefined);
    setActiveMileage(undefined);
    setValuation(undefined);
    setInsufficient(undefined);
    setError(undefined);
    heroMotion.ref.current?.querySelector('input')?.focus();
  }

  /** Announce the current search state, prioritizing loading and errors over prior results. */
  function getStatusAnnouncement(): string {
    if (!docked) return '';

    if (busy) return 'Calculating your estimated asking price.';

    if (queryState === 'error') return 'The estimated price could not be loaded.';

    if (!submittedSelection) return 'Choose a vehicle to continue.';

    if (!valuation) return 'Insufficient listing evidence for an estimate.';

    let priceLabel: string;
    switch (valuation.valuationBasis) {
      case 'projected':
        priceLabel = 'Projected asking price today';
        break;
      case 'historical':
        priceLabel = 'Estimated Price';
        break;
      case 'current':
        priceLabel = 'Estimated asking price';
        break;
    }

    return `${priceLabel}: ${valuation.estimatedAskingPrice} ${valuation.currency}.`;
  }

  const title = submittedSelection
    ? `${submittedSelection.modelYear.year} ${submittedSelection.make.name} ${submittedSelection.model.name}`
    : 'Valuation details';

  // Keep previous results visible after an edit, but tell the user they need to submit again.
  const queryEdited =
    submittedSelection !== undefined &&
    (vehicleQuery !== title || enteredMileageMiles !== activeMileage);

  return (
    <main className={`valuation-app ${docked ? 'is-docked' : 'is-entry'}`}>
      <section className="hero" aria-labelledby="page-title">
        <img
          className="hero-image"
          src="/valuation-hero.png"
          alt="White Toyota Camry on a mountain road at sunrise."
          fetchPriority="high"
        />
        <section
          className="hero-content"
          ref={heroMotion.ref}
          data-moving={heroMotion.moving || undefined}
          onAnimationEnd={heroMotion.finish}
        >
          <HeroHeadline />
          <Text type="supporting" as="p" className="hero-subtitle">
            Find the value of your vehicle through our database of over 4 million listings.
          </Text>
          <form className="valuation-form" onSubmit={handleSubmit} noValidate>
            <VehicleAutocomplete
              value={vehicleQuery}
              onValueChange={handleVehicleText}
              onSelectionChange={setCatalogSelection}
              onCatalogReady={setCatalogReady}
            />
            <MileageInput
              value={mileageInput}
              unit={mileageUnit}
              onChange={handleMileageChange}
              onUnitChange={handleMileageUnitChange}
            />
            <Button
              label={busy ? 'Loading…' : 'Get value'}
              type="submit"
              variant="primary"
              size="lg"
              isDisabled={!catalogReady || busy}
              endContent={<Icon icon="chevronRight" />}
            />
          </form>
          <Text type="supporting" as="p" className="example-copy">
            Start with a year, choose a make, then choose a model. Example: 2015 Toyota Camry.
          </Text>
          {error && (
            <Text type="body" as="p" className="form-error" role="alert">
              {error}
            </Text>
          )}
        </section>
      </section>

      <Text as="p" className="sr-only" role="status">
        {getStatusAnnouncement()}
      </Text>
      {docked && (
        <section
          className="dashboard-content"
          aria-label="Vehicle valuation dashboard"
          aria-busy={busy}
        >
          <header className="dashboard-meta">
            <Heading level={2}>{title}</Heading>
            {!busy && valuation && (
              <Text type="supporting">
                {valuation.valuationBasis === 'projected'
                  ? 'Projected asking price today · low confidence'
                  : valuation.valuationBasis === 'historical'
                    ? 'Estimated Price · low confidence'
                    : 'Market asking-price estimate'}
              </Text>
            )}
          </header>
          {!busy && queryEdited && (valuation || insufficient) && (
            <Text as="p" type="supporting" role="status">
              Showing previous results. Select Get value to update.
            </Text>
          )}
          {busy ? (
            <ValuationSkeleton />
          ) : queryState === 'error' ? (
            <Section className="empty-state result-reveal" padding={6}>
              <Heading level={2}>Unable to estimate right now</Heading>
              <Text type="body" as="p">
                The valuation service is temporarily unavailable. Your vehicle and mileage are still
                here.
              </Text>
              <Button
                label="Try again"
                variant="primary"
                onClick={() => {
                  if (submittedSelection) void loadValuation(submittedSelection, activeMileage);
                }}
              />
            </Section>
          ) : valuation ? (
            <VehicleResults valuation={valuation} unit={mileageUnit} onClear={clearVehicle} />
          ) : insufficient ? (
            <Section className="empty-state result-reveal" padding={6}>
              <Heading level={2}>{insufficient.heading}</Heading>
              <Text type="body" as="p">
                {insufficient.reason}
              </Text>
              <Button label="Choose another vehicle" variant="secondary" onClick={clearVehicle} />
            </Section>
          ) : (
            <Section className="empty-state result-reveal" padding={6}>
              <Heading level={2}>Select a vehicle to begin</Heading>
              <Text type="body" as="p">
                Choose a vehicle and select Get value to calculate an estimate. Mileage is optional.
              </Text>
            </Section>
          )}
        </section>
      )}
    </main>
  );
}
