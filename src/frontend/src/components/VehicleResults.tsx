import { Button } from '@astryxdesign/core/Button';
import { HStack } from '@astryxdesign/core/HStack';
import { Icon } from '@astryxdesign/core/Icon';
import { IconButton } from '@astryxdesign/core/IconButton';
import { Tooltip } from '@astryxdesign/core/Tooltip';
import { Section } from '@astryxdesign/core/Section';
import { Text } from '@astryxdesign/core/Text';
import { Table, TableBody, TableRow, TableHeaderCell, TableCell } from '@astryxdesign/core/Table';
import { formatMileage, fromMiles, toMiles, type MileageUnit } from '../utils/mileage';
import type { ModelAValuation } from '../services/model-a';
import { ComparableListings } from './ComparableListings';
import { PriceMileageChart } from './PriceMileageChart';

const confidenceIndicators = {
  high: { label: 'High Confidence', icon: 'success', color: 'success' },
  medium: { label: 'Medium Confidence', icon: 'info', color: 'secondary' },
  low: { label: 'Low Confidence', icon: 'warning', color: 'warning' },
} as const;

/** Show the estimate and the listing dates that explain how current it is. */
export function VehicleResults({
  valuation,
  unit,
  onClear,
}: {
  valuation: ModelAValuation;
  unit: MileageUnit;
  onClear: () => void;
}) {
  const money = (value: number) =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: valuation.currency,
      maximumFractionDigits: 0,
    }).format(value);

  // The API reports kilometres; convert once to the user's selected display unit.
  const mileage =
    valuation.referenceMileageKm === null
      ? null
      : fromMiles(toMiles(valuation.referenceMileageKm, 'kilometers'), unit);

  // Choose labels from the API's stated valuation basis rather than inferring freshness from
  // a date.
  const isHistorical = valuation.valuationBasis === 'historical';
  const isProjected = valuation.valuationBasis === 'projected';
  const confidence = confidenceIndicators[valuation.confidence.level];
  const summary = valuation.listingSummary;
  const latestListingDate = summary.latestListingDate;

  // Source dates are calendar dates, so format them in UTC to avoid shifting to the previous
  // day.
  const dateFormat = new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
  const priceLabel = isProjected
    ? 'Projected asking price today'
    : isHistorical
      ? 'Estimated Price'
      : 'Estimated asking price';

  return (
    <section className="results-grid result-reveal" aria-label="Valuation results">
      <Section className="summary-panel" padding={4}>
        <Text type="supporting" as="p" display="block" className="eyebrow">
          {priceLabel}
        </Text>
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Text display="block" type="display-2" className="estimate-value">
            {money(valuation.estimatedAskingPrice)}
          </Text>
          <Tooltip
            content={confidence.label}
            touchTrigger="tap"
            focusTrigger="always"
            hasHoverIndication={false}
          >
            <IconButton
              label={confidence.label}
              variant="ghost"
              size="lg"
              icon={<Icon icon={confidence.icon} color={confidence.color} size="lg" />}
            />
          </Tooltip>
        </HStack>
        <Text type="supporting" as="p" display="block">
          Based on {valuation.confidence.independentVehicles} comparable vehicles
        </Text>
        {valuation.locationSelection.region && (
          <Text as="p" display="block" type="supporting" role="note">
            {valuation.locationSelection.fallback
              ? `Not enough comparable listings in ${valuation.locationSelection.region}. Using ${valuation.locationSelection.country ? `${valuation.locationSelection.country}-wide` : 'broader market'} listings.`
              : `Based on listings in ${valuation.locationSelection.region}${valuation.locationSelection.country ? ` (${valuation.locationSelection.country})` : ''}.`}
          </Text>
        )}
        {valuation.mileageSource === 'rounded_down' &&
          mileage !== null &&
          valuation.requestedMileageKm !== null && (
            <Text as="p" display="block" type="body" role="note">
              Using {formatMileage(mileage)} {unit}, the nearest available mileage below your
              requested{' '}
              {formatMileage(fromMiles(toMiles(valuation.requestedMileageKm, 'kilometers'), unit))}{' '}
              {unit}.
            </Text>
          )}
        {valuation.mileageSource === 'unavailable' && (
          <Text as="p" display="block" type="body" role="note">
            No mileage adjustment was applied. The estimate uses recorded asking prices.
          </Text>
        )}
        <ul
          className="summary-stats"
          aria-label="Indicative asking-price range"
          style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}
        >
          <li>
            <strong>{money(valuation.marketRange.low)}</strong>
            <Text type="supporting">Lower range</Text>
          </li>
          <li>
            <strong>{money(valuation.marketRange.high)}</strong>
            <Text type="supporting">Upper range</Text>
          </li>
        </ul>
        <Table
          aria-label={`Recorded listing summary (${valuation.currency})`}
          density="compact"
          dividers="rows"
        >
          <TableBody>
            <TableRow>
              <TableHeaderCell scope="row">Last listing</TableHeaderCell>
              <TableCell>
                {latestListingDate ? (
                  <time dateTime={latestListingDate}>
                    {dateFormat.format(new Date(`${latestListingDate}T00:00:00Z`))}
                  </time>
                ) : (
                  'Unavailable'
                )}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableHeaderCell scope="row">Total listings</TableHeaderCell>
              <TableCell>
                {summary.totalListings.toLocaleString('en-US')}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableHeaderCell scope="row">Lowest recorded price</TableHeaderCell>
              <TableCell>
                {summary.lowestRecordedPrice != null
                  ? money(summary.lowestRecordedPrice)
                  : 'Unavailable'}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableHeaderCell scope="row">Highest recorded price</TableHeaderCell>
              <TableCell>
                {summary.highestRecordedPrice != null
                  ? money(summary.highestRecordedPrice)
                  : 'Unavailable'}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <Button
          className="change-vehicle-button"
          label="Change vehicle"
          variant="ghost"
          size="sm"
          onClick={onClear}
        />
        <Text as="p" display="block" type="supporting">
          This estimate is based on listings for the same year, make and model, accounting for
          mileage and location where available.
        </Text>
      </Section>
      <Section className="mileage-panel" padding={4}>
        <PriceMileageChart valuation={valuation} unit={unit} />
        <ComparableListings valuation={valuation} />
      </Section>
    </section>
  );
}
