import { useMemo, useState } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { Heading } from '@astryxdesign/core/Heading';
import { Skeleton } from '@astryxdesign/core/Skeleton';
import { Table, type TableColumn } from '@astryxdesign/core/Table';
import { Text } from '@astryxdesign/core/Text';
import type { ModelAValuation } from '../services/model-a';
import { formatMileage } from '../utils/mileage';

type ComparableListing = ModelAValuation['comparables'][number];
const PAGE_SIZE = 10;
const MAX_COMPARABLE_LISTINGS = 100;

/** Show up to 100 positive-weight listings used for the estimate, one page at a time. */
export function ComparableListings({ valuation }: { valuation: ModelAValuation }) {
  const [page, setPage] = useState(0);

  // Only show listings that contributed to the estimate, with the newest observations first.
  const listings = useMemo(
    () =>
      valuation.comparables
        .filter((listing) => listing.weight > 0)
        .sort(
          (a, b) => b.observedOn.localeCompare(a.observedOn) || b.observationId - a.observationId,
        )
        .slice(0, MAX_COMPARABLE_LISTINGS),
    [valuation.comparables],
  );

  const pageCount = Math.ceil(listings.length / PAGE_SIZE);

  // Clamp the page when a new result contains fewer listings so the table never lands on an
  // empty page.
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));

  const offset = currentPage * PAGE_SIZE;

  const money = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: valuation.currency,
    maximumFractionDigits: 0,
  });

  const columns: TableColumn<ComparableListing>[] = [
    {
      key: 'photo',
      header: 'Photo',
      renderCell: () => (
        <Skeleton
          width="3.5rem"
          height="2.5rem"
          radius={2}
          role="img"
          aria-label="Vehicle photo unavailable"
          aria-hidden={false}
          style={{ animation: 'none', opacity: 1 }}
        />
      ),
    },
    {
      key: 'mileageKm',
      header: 'Mileage (km)',
      renderCell: (listing) =>
        listing.mileageKm === null ? 'Unavailable' : `${formatMileage(listing.mileageKm)} km`,
    },
    {
      key: 'listingPrice',
      header: `Asking price (${valuation.currency})`,
      renderCell: (listing) => (
        <Text type="body" weight="semibold">
          {money.format(listing.listingPrice)}
        </Text>
      ),
    },
    {
      key: 'location',
      header: 'Location',
      renderCell: (listing) =>
        [listing.location.city, listing.location.region, listing.location.country]
          .filter((part) => part?.trim())
          .join(', ') || 'Location unavailable',
    },
  ];

  return (
    <>
      <header className="panel-heading">
        <Heading level={3}>Comparable listings ({listings.length.toLocaleString('en-US')})</Heading>
      </header>

      <Text as="p" display="block" type="supporting">
        Original asking prices, newest observations first. Showing up to 100 listings used for the
        estimate. Photos are unavailable.
      </Text>

      <Table
        aria-label="Comparable vehicle listings"
        data={listings.slice(offset, offset + PAGE_SIZE)}
        columns={columns}
        idKey="observationId"
        density="compact"
        dividers="rows"
        textOverflow="wrap"
        rowIndexStart={offset + 1}
        rowCount={listings.length}
      />
      {listings.length > 0 && (
        <footer className="table-footer">
          <Text type="supporting" role="status">
            Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, listings.length)} of{' '}
            {listings.length.toLocaleString('en-US')}
          </Text>
          {pageCount > 1 && (
            <nav aria-label="Listing pages" style={{ display: 'flex', gap: 'var(--spacing-2)' }}>
              <Button
                label="Previous"
                variant="ghost"
                size="sm"
                isDisabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
              />

              <Button
                label="Next"
                variant="secondary"
                size="sm"
                isDisabled={currentPage + 1 >= pageCount}
                onClick={() => setPage(currentPage + 1)}
              />
            </nav>
          )}
        </footer>
      )}
    </>
  );
}
