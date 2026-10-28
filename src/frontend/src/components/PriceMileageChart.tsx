import { useId, useLayoutEffect, useRef, useState } from 'react';
import { Heading } from '@astryxdesign/core/Heading';
import { HStack } from '@astryxdesign/core/HStack';
import { Section } from '@astryxdesign/core/Section';
import { Text } from '@astryxdesign/core/Text';
import { fromMiles, toMiles, type MileageUnit } from '../utils/mileage';
import type { ModelAValuation } from '../services/model-a';
import {
  createAxisScale,
  getPriceMileageBands,
  getPriceMileagePoints,
  type PriceMileageBand,
} from '../utils/price-mileage';

/** Use horizontal Bézier handles so the curve stays between the prices at each end. */
function createCurvePath(
  coordinates: readonly (readonly [number, number])[],
  start: 'M' | 'L' = 'M',
): string {
  return coordinates
    .map(([x, y], index) => {
      if (index === 0) {
        return `${start} ${x} ${y}`;
      }

      const [previousX, previousY] = coordinates[index - 1]!;

      // Both control points share the midpoint X and keep their endpoint Y values.
      // This smooths each transition without overshooting either price.
      const midpointX = (previousX + x) / 2;

      return `C ${midpointX} ${previousY} ${midpointX} ${y} ${x} ${y}`;
    })
    .join(' ');
}

/** Find enough decimal places to keep neighboring tick labels distinct. */
function precisionForStep(step: number): number {
  for (let digits = 0; digits <= 6; digits += 1) {
    // Ignore floating-point noise when checking whether this precision preserves the step.
    if (Math.abs(step - Number(step.toFixed(digits))) < 1e-9) {
      return digits;
    }
  }

  return 6;
}

/** Show the median price and middle 50% of listing prices for each mileage band. */
export function PriceMileageChart({
  valuation,
  unit,
}: {
  valuation: ModelAValuation;
  unit: MileageUnit;
}) {
  // Use one usable listing per vehicle, then group prices into mileage bands.
  const points = getPriceMileagePoints(valuation.comparables, unit);
  const bands = getPriceMileageBands(points);
  const hasBands = bands.length > 0;

  const svgRef = useRef<SVGSVGElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [size, setSize] = useState({ width: 720, height: 320, fontSize: 12 });

  // Match SVG units to the panel size so widening the chart does not stretch the labels.
  // Measure again when bands appear because the SVG is absent in the empty state.
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) {
      return;
    }

    const measure = () => {
      const { width, height } = svg.getBoundingClientRect();
      if (width <= 0 || height <= 0) {
        return;
      }

      // Only update measurements when they change so the resize observer does not cause extra
      // renders.
      const fontSize = Number.parseFloat(getComputedStyle(svg).fontSize);
      setSize((previous) =>
        previous.width === width && previous.height === height && previous.fontSize === fontSize
          ? previous
          : { width, height, fontSize },
      );
    };

    measure();

    // Keep the SVG aligned with its rendered size and stop observing on unmount.
    const observer = new ResizeObserver(measure);
    observer.observe(svg);

    return () => observer.disconnect();
  }, [hasBands]);

  if (!hasBands) {
    return null;
  }

  const { width, height, fontSize } = size;

  // Reserve room for axis labels, scaling the margins with the rendered font size.
  const plot = {
    left: fontSize * 3.5,
    right: fontSize * 2,
    top: fontSize * 1.5,
    bottom: fontSize * 3.5,
  };

  // Allow more mileage labels on wider charts, requesting between 3 and 6 ticks.
  const mileageTickCount = Math.max(3, Math.min(6, Math.floor(width / (fontSize * 9))));
  const mileageScale = createAxisScale(
    bands.flatMap((band) => [band.start, band.end]),
    mileageTickCount,
    0,
    0,
  );

  // Only draw the selected mileage marker inside a populated band; leave gaps in the evidence
  // visible.
  const referenceMileage =
    valuation.referenceMileageKm === null
      ? null
      : fromMiles(toMiles(valuation.referenceMileageKm, 'kilometers'), unit);
  const markerMileage =
    referenceMileage !== null &&
    bands.some((band) => referenceMileage >= band.start && referenceMileage <= band.end)
      ? referenceMileage
      : null;

  // The chart shows recorded prices, so projected estimates use their historical reference
  // here.
  const markerPrice =
    valuation.valuationBasis === 'projected'
      ? valuation.historicalReference.estimatedAskingPrice
      : valuation.estimatedAskingPrice;

  // Include a visible estimate in the price range so its marker is not clipped.
  const priceScale = createAxisScale(
    [
      ...bands.flatMap((band) => [band.lower, band.upper]),
      ...(markerMileage === null ? [] : [markerPrice]),
    ],
    5,
    0,
  );

  // Convert mileage and price values into SVG coordinates inside the space reserved for the
  // plot.
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;

  const mileageToX = (value: number) =>
    plot.left +
    ((value - mileageScale.minimum) / (mileageScale.maximum - mileageScale.minimum)) * plotWidth;

  // SVG Y increases downward, so higher prices must map to smaller Y coordinates.
  const priceToY = (value: number) =>
    plot.top +
    ((priceScale.maximum - value) / (priceScale.maximum - priceScale.minimum)) * plotHeight;

  // Match decimal precision to tick spacing so distinct labels do not round to the same
  // value.
  const priceDigits = precisionForStep(priceScale.step);
  const currencyFormatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: valuation.currency,
    minimumFractionDigits: priceDigits,
    maximumFractionDigits: priceDigits,
  });

  const mileageFormatter = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: precisionForStep(mileageScale.step),
  });

  // Compact labels keep the axes readable; tooltips and the callout use full values.
  const compactMileage = new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  });
  const compactPrice = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: valuation.currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  });

  const sourceContext =
    valuation.valuationBasis === 'current'
      ? 'Recorded asking prices'
      : 'Historical recorded asking prices';

  // Split the curve at empty mileage bands instead of drawing prices through missing
  // evidence.
  const segments: PriceMileageBand[][] = [];

  for (const band of bands) {
    const previousSegment = segments.at(-1);

    if (previousSegment && previousSegment.at(-1)!.index + 1 === band.index) {
      previousSegment.push(band);
    } else {
      segments.push([band]);
    }
  }

  // Anchor each curve at band centers and extend it to the outer edges of the segment.
  // This gives even a single populated band its full width.
  const getCoordinates = (
    segment: readonly PriceMileageBand[],
    price: 'median' | 'lower' | 'upper',
  ) => {
    const firstBand = segment[0]!;
    const lastBand = segment.at(-1)!;

    return [
      [mileageToX(firstBand.start), priceToY(firstBand[price])] as const,
      ...segment.map(
        (band) => [mileageToX((band.start + band.end) / 2), priceToY(band[price])] as const,
      ),
      [mileageToX(lastBand.end), priceToY(lastBand[price])] as const,
    ];
  };

  const markerX = markerMileage === null ? null : mileageToX(markerMileage);
  const markerY = priceToY(markerPrice);
  const calloutWidth = fontSize * 10;
  const calloutHeight = fontSize * 4.5;

  // Keep the estimate label inside the plot, placing it below the marker if there is no room
  // above.
  const calloutX =
    markerX === null
      ? 0
      : Math.max(
          plot.left,
          Math.min(width - plot.right - calloutWidth, markerX - calloutWidth / 2),
        );
  const calloutY =
    markerY - calloutHeight - fontSize >= plot.top
      ? markerY - calloutHeight - fontSize
      : Math.min(height - plot.bottom - calloutHeight, markerY + fontSize);

  return (
    <section className="price-mileage-chart" aria-label="Price versus mileage chart">
      <header className="price-mileage-chart__heading">
        <Heading level={3}>Typical Price Range by Mileage</Heading>
        <HStack gap={3} wrap="wrap" className="price-mileage-chart__legend">
          <Text type="supporting" className="price-mileage-chart__legend-price">
            Typical price
          </Text>
          <Text type="supporting" className="price-mileage-chart__legend-range">
            Typical range
          </Text>
        </HStack>
      </header>

      <Section className="price-mileage-chart__viewport" variant="transparent" padding={0}>
        <svg
          ref={svgRef}
          className="price-mileage-chart__plot"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-labelledby={`${titleId} ${descriptionId}`}
        >
          <title id={titleId}>Typical Price Range by Mileage</title>
          <desc id={descriptionId}>
            A blue line connects median recorded asking prices in {valuation.currency} by mileage in{' '}
            {unit}. The shaded range shows the middle 50% of recorded prices. Based on{' '}
            {points.length} distinct vehicles. Empty mileage bands are left as gaps. Prices are not
            adjusted or projected.
          </desc>

          {/* Draw the grid first so the price curves and marker appear above it. */}
          {priceScale.ticks.map((tick) => (
            <g className="price-mileage-chart__grid" key={`price-${tick}`}>
              <line x1={plot.left} x2={width - plot.right} y1={priceToY(tick)} y2={priceToY(tick)} />
              <text x={plot.left - 9} y={priceToY(tick)} textAnchor="end" dominantBaseline="middle">
                {priceScale.maximum >= 1_000
                  ? compactPrice.format(tick)
                  : currencyFormatter.format(tick)}
              </text>
            </g>
          ))}

          {mileageScale.ticks.map((tick) => (
            <g className="price-mileage-chart__grid" key={`mileage-${tick}`}>
              <line
                x1={mileageToX(tick)}
                x2={mileageToX(tick)}
                y1={plot.top}
                y2={height - plot.bottom}
              />
              <text x={mileageToX(tick)} y={height - plot.bottom + fontSize * 1.6} textAnchor="middle">
                {mileageScale.step >= 1_000
                  ? compactMileage.format(tick)
                  : mileageFormatter.format(tick)}
              </text>
            </g>
          ))}

          <line
            className="price-mileage-chart__axis"
            x1={plot.left}
            x2={width - plot.right}
            y1={height - plot.bottom}
            y2={height - plot.bottom}
          />

          <line
            className="price-mileage-chart__axis"
            x1={plot.left}
            x2={plot.left}
            y1={plot.top}
            y2={height - plot.bottom}
          />
          <text
            className="price-mileage-chart__axis-label"
            x={width - plot.right}
            y={height - fontSize * 0.3}
            textAnchor="end"
          >
            {unit}
          </text>

          {/* Render each populated segment separately to preserve gaps in the data. */}
          {segments.map((segment) => {
            const upper = getCoordinates(segment, 'upper');

            // Trace the upper edge forward and the lower edge backward to close the shaded area.
            const lower = [...getCoordinates(segment, 'lower')].reverse();

            return (
              <g key={segment[0]!.index}>
                <path
                  className="price-mileage-chart__range"
                  d={`${createCurvePath(upper)} ${createCurvePath(lower, 'L')} Z`}
                />
                <path
                  className="price-mileage-chart__line"
                  d={createCurvePath(getCoordinates(segment, 'median'))}
                />
              </g>
            );
          })}

          {/* Invisible full-height targets make each band easy to hover for exact values. */}
          {bands.map((band) => (
            <rect
              key={band.index}
              className="price-mileage-chart__hit-area"
              x={mileageToX(band.start)}
              y={plot.top}
              width={mileageToX(band.end) - mileageToX(band.start)}
              height={plotHeight}
            >
              <title>{`${mileageFormatter.format(band.start)}–${mileageFormatter.format(band.end)} ${unit}: median ${currencyFormatter.format(band.median)} (${band.count} vehicles)`}</title>
            </rect>
          ))}

          {/* Show the estimate only where a populated band supports the selected mileage. */}
          {markerX !== null && (
            <g className="price-mileage-chart__selection">
              <line
                className="price-mileage-chart__guide"
                x1={markerX}
                x2={markerX}
                y1={markerY}
                y2={height - plot.bottom}
              />
              <circle className="price-mileage-chart__halo" cx={markerX} cy={markerY} r="9" />
              <circle className="price-mileage-chart__marker" cx={markerX} cy={markerY} r="5" />
              <rect
                className="price-mileage-chart__callout"
                x={calloutX}
                y={calloutY}
                width={calloutWidth}
                height={calloutHeight}
                rx="7"
              />
              <text
                className="price-mileage-chart__callout-mileage"
                x={calloutX + calloutWidth / 2}
                y={calloutY + fontSize * 1.4}
                textAnchor="middle"
              >
                {mileageFormatter.format(markerMileage!)} {unit}
              </text>
              <text
                className="price-mileage-chart__callout-price"
                x={calloutX + calloutWidth / 2}
                y={calloutY + fontSize * 2.8}
                textAnchor="middle"
              >
                {currencyFormatter.format(markerPrice)}
              </text>
              <text
                className="price-mileage-chart__callout-label"
                x={calloutX + calloutWidth / 2}
                y={calloutY + fontSize * 3.9}
                textAnchor="middle"
              >
                {valuation.valuationBasis === 'current' ? 'Your estimate' : 'Historical estimate'}
              </text>
            </g>
          )}
        </svg>
      </Section>

      <Text as="p" display="block" type="supporting">
        {sourceContext} ({valuation.currency}). Line: median. Shading: middle 50% of prices.
      </Text>
    </section>
  );
}
