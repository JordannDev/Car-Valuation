import { Section } from '@astryxdesign/core/Section';
import { Skeleton } from '@astryxdesign/core/Skeleton';

/** Keep space for the estimate and explanation while the request is loading. */
export function ValuationSkeleton() {
  return (
    <section className="valuation-skeleton" aria-hidden="true">
      <section className="results-grid">
        <Section className="summary-panel skeleton-panel" padding={4}>
          <Skeleton width="35%" height="1rem" />
          <Skeleton width="65%" height="3.5rem" />
          <Skeleton width="80%" height="1rem" />
          <Skeleton height="4rem" />
        </Section>
        <Section className="mileage-panel skeleton-panel" padding={4}>
          <Skeleton width="50%" height="1.25rem" />
          <Skeleton height="12rem" />
        </Section>
      </section>
    </section>
  );
}
