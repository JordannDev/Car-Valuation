-- This schema is for review, generated from docs/database.dbml. It has not been applied.
-- DBML cannot describe ON UPDATE, so the updated_at clauses are added below.
-- Use UTC sessions and strict SQL mode when applying or using this schema.
-- Keep these defaults, comments, and clauses when creating API migrations.

-- SQL dump generated using DBML (dbml.dbdiagram.io)
-- Database: MySQL
-- Generated at: 2026-10-27T10:31:55.057Z

CREATE TABLE `makes` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `name` varchar(120) NOT NULL,
  `normalized_name` varchar(120) UNIQUE NOT NULL COMMENT 'Name with whitespace trimmed and case normalized, matched using binary collation.',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `models` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `make_id` bigint NOT NULL,
  `name` varchar(160) NOT NULL,
  `normalized_name` varchar(160) NOT NULL COMMENT 'Match this name within its make, using binary collation.',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `model_years` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `model_id` bigint NOT NULL,
  `year` smallint NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `trims` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `model_year_id` bigint NOT NULL,
  `name` varchar(200) NOT NULL,
  `normalized_name` varchar(200) NOT NULL COMMENT 'Match this name within its model year, using binary collation.',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `vehicles` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `vin` char(17) UNIQUE NOT NULL COMMENT 'Use uppercase ASCII and binary comparison. Validate the VIN before importing it.',
  `model_year_id` bigint NOT NULL,
  `trim_id` bigint,
  `drivetrain_code` varchar(32),
  `body_type_code` varchar(32),
  `fuel_type_code` varchar(32),
  `powertrain_type_code` varchar(32),
  `engine_displacement_liters` decimal(5,2),
  `engine_cylinders` smallint,
  `exterior_color_code` varchar(32),
  `interior_color_code` varchar(32),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.',
  CONSTRAINT `ck_vehicles_engine_displacement_nonnegative` CHECK (engine_displacement_liters >= 0),
  CONSTRAINT `ck_vehicles_engine_cylinders_nonnegative` CHECK (engine_cylinders >= 0)
);

CREATE TABLE `dealers` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `identity_key` char(64) UNIQUE NOT NULL COMMENT 'Hash of the cleaned-up dealer name and full address, matched using binary comparison.',
  `website` varchar(2048),
  `street` varchar(255),
  `city` varchar(120),
  `state_or_province` varchar(80),
  `postal_code` varchar(20) COMMENT 'Store as text to keep leading zeros and Canadian postal codes intact.',
  `country_code` char(2),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `listings` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `vehicle_id` bigint NOT NULL,
  `dealer_id` bigint NOT NULL,
  `source_name` varchar(80) NOT NULL,
  `source_listing_id` varchar(255),
  `fallback_identity_key` char(64) COMMENT 'Key built from the source, VIN, dealer, and a reliable first-seen date, matched using binary comparison.',
  `first_seen_date` date,
  `last_seen_date` date,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `listing_observations` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `listing_id` bigint NOT NULL,
  `import_batch_id` bigint NOT NULL,
  `source_record_number` bigint NOT NULL COMMENT 'Original CSV record number, counting the header as 1 and the first data record as 2.',
  `observed_at` datetime(6) COMMENT 'When the source snapshot was taken, in UTC, if known. Do not replace a missing date with the import time.',
  `listing_price` decimal(18,2) COMMENT 'The listed asking price. Leave missing prices null rather than setting them to zero.',
  `currency_code` char(3) COMMENT 'Confirm the currency before using this snapshot for valuation.',
  `listing_mileage` bigint,
  `mileage_unit` varchar(2) COMMENT 'MI or KM. Confirm the unit before using this snapshot for valuation.',
  `used` boolean,
  `certified` boolean,
  `listing_status` ENUM ('in_transit') COMMENT 'in_transit means the provider reports the vehicle is in transit. NULL means unknown or not supplied, not sold.',
  `first_seen_date` date COMMENT 'Date reported in this source snapshot.',
  `last_seen_date` date COMMENT 'Date reported in this source snapshot.',
  `dealer_vdp_last_seen_date` date,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.',
  CONSTRAINT `ck_observations_source_record` CHECK (source_record_number >= 2),
  CONSTRAINT `ck_observations_price_nonnegative` CHECK (listing_price >= 0),
  CONSTRAINT `ck_observations_mileage_nonnegative` CHECK (listing_mileage >= 0),
  CONSTRAINT `ck_observations_mileage_unit` CHECK (mileage_unit IN ('MI', 'KM')),
  CONSTRAINT `ck_observations_used_boolean` CHECK (used IN (0, 1)),
  CONSTRAINT `ck_observations_certified_boolean` CHECK (certified IN (0, 1))
);

CREATE TABLE `import_batches` (
  `id` bigint PRIMARY KEY NOT NULL AUTO_INCREMENT,
  `source_name` varchar(80) NOT NULL,
  `source_file_name` varchar(255) NOT NULL,
  `source_file_hash` char(64) NOT NULL COMMENT 'SHA-256 hash of the original file.',
  `source_file_uri` varchar(2048) NOT NULL COMMENT 'Location of the original file, saved outside the database.',
  `normalization_version` varchar(40) NOT NULL,
  `rules_hash` char(64) NOT NULL COMMENT 'Hash of the settings and rule code used to prepare this file.',
  `manifest_uri` varchar(2048) NOT NULL COMMENT 'Manifest saved for this run. Keep it unchanged and retain earlier manifests outside the database when reprocessing.',
  `status` ENUM ('pending', 'running', 'completed', 'failed') NOT NULL DEFAULT 'pending' COMMENT 'pending: waiting to start; running: importing; completed: available to query; failed: can be resumed.',
  `started_at` datetime(6) COMMENT 'UTC.',
  `completed_at` datetime(6) COMMENT 'UTC.',
  `total_rows` bigint NOT NULL DEFAULT 0,
  `accepted_rows` bigint NOT NULL DEFAULT 0,
  `rejected_rows` bigint NOT NULL DEFAULT 0,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.',
  CONSTRAINT `ck_import_batches_counts_nonnegative` CHECK (total_rows >= 0 AND accepted_rows >= 0 AND rejected_rows >= 0),
  CONSTRAINT `ck_import_batches_counts_total` CHECK (accepted_rows + rejected_rows <= total_rows),
  CONSTRAINT `ck_import_batches_completed_counts` CHECK (status <> 'completed' OR accepted_rows + rejected_rows = total_rows)
);

CREATE TABLE `drivetrains` (
  `code` varchar(32) PRIMARY KEY NOT NULL,
  `display_name` varchar(100) NOT NULL,
  `is_specific` boolean NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `body_types` (
  `code` varchar(32) PRIMARY KEY NOT NULL,
  `display_name` varchar(100) NOT NULL,
  `is_specific` boolean NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `fuel_types` (
  `code` varchar(32) PRIMARY KEY NOT NULL,
  `display_name` varchar(100) NOT NULL,
  `is_specific` boolean NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `powertrain_types` (
  `code` varchar(32) PRIMARY KEY NOT NULL,
  `display_name` varchar(100) NOT NULL,
  `is_specific` boolean NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE TABLE `colors` (
  `code` varchar(32) PRIMARY KEY NOT NULL,
  `display_name` varchar(100) NOT NULL,
  `is_specific` boolean NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'When the row was created. Keep this unchanged and use UTC for database connections.',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'When the row last changed, in UTC. The MySQL schema updates this with ON UPDATE CURRENT_TIMESTAMP(6); keep that behavior in API migrations.'
);

CREATE UNIQUE INDEX `uq_models_make_name` ON `models` (`make_id`, `normalized_name`);

CREATE UNIQUE INDEX `uq_model_years_model_year` ON `model_years` (`model_id`, `year`);

CREATE UNIQUE INDEX `uq_trims_model_year_name` ON `trims` (`model_year_id`, `normalized_name`);

CREATE UNIQUE INDEX `uq_trims_id_model_year` ON `trims` (`id`, `model_year_id`);

CREATE INDEX `ix_vehicles_model_year_trim` ON `vehicles` (`model_year_id`, `trim_id`);

CREATE UNIQUE INDEX `uq_listings_source_id` ON `listings` (`source_name`, `source_listing_id`);

CREATE UNIQUE INDEX `uq_listings_source_fallback` ON `listings` (`source_name`, `fallback_identity_key`);

CREATE INDEX `ix_listings_vehicle_dealer` ON `listings` (`vehicle_id`, `dealer_id`);

CREATE UNIQUE INDEX `uq_observations_batch_record` ON `listing_observations` (`import_batch_id`, `source_record_number`);

CREATE INDEX `ix_observations_listing_observed` ON `listing_observations` (`listing_id`, `observed_at`);

CREATE UNIQUE INDEX `uq_import_batches_source_file` ON `import_batches` (`source_name`, `source_file_hash`);

ALTER TABLE `trims` COMMENT = 'Vehicles with the same trim can have different engines and drivetrains, so store those details on the vehicle.';

ALTER TABLE `vehicles` COMMENT = 'Keep one physical vehicle per VIN and leave uncertain attributes null.
The composite foreign key checks that the selected trim belongs to model_year_id.
Report conflicting source details rather than overwriting values already saved.
Do not save uncertain roof, accent, or two-tone color matches as the vehicle color.
';

ALTER TABLE `dealers` COMMENT = 'One dealer location, matched by its name and full address. If those details are incomplete, report an import issue rather than matching by name or website alone.';

ALTER TABLE `listings` COMMENT = 'One sale listing. The same VIN may be listed by different dealers or relisted later.
Use the provider listing ID when available. Otherwise require enough details to identify the listing;
if those details are missing, report the row as unmatched rather than guessing a merge.
Use binary comparison for source keys. Unique keys may contain null for more than one row.
';

ALTER TABLE `listing_observations` COMMENT = 'One source row in one file. New files add snapshots; retries reuse the same batch and row keys.
Keep price and mileage history. Comparable queries count each VIN once and choose a usable
snapshot with known currency, units, and a suitable source date. Repeated imports must not give a vehicle extra weight.
';

ALTER TABLE `import_batches` COMMENT = 'Keep one batch per file, including retries. Resume failed imports with the same batch and row keys.
Reprocessing updates calculated results for existing records. It must not create new market
snapshots, and it must retire calculated records that are now rejected. Keep earlier manifests outside the database.
Keep incomplete imports out of valuation queries and detailed row issues outside the database.
';

ALTER TABLE `drivetrains` COMMENT = 'Keep FWD, RWD, AWD, and 4WD separate. Broader categories such as 2WD, AWD_OR_4WD, and OTHER have is_specific=false.';

ALTER TABLE `fuel_types` COMMENT = 'Includes GASOLINE, DIESEL, BIODIESEL, ELECTRIC, and FLEX_FUEL. Store HYBRID under powertrain_types without guessing which fuel it burns.';

ALTER TABLE `powertrain_types` COMMENT = 'HYBRID is supported. Only use a more specific plug-in or battery-electric category when the source states it.';

ALTER TABLE `colors` COMMENT = 'Use these colors for both interior and exterior. OTHER is a broad category; leave uncertain matches null.';

ALTER TABLE `models` ADD CONSTRAINT `fk_models_make` FOREIGN KEY (`make_id`) REFERENCES `makes` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `model_years` ADD CONSTRAINT `fk_model_years_model` FOREIGN KEY (`model_id`) REFERENCES `models` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `trims` ADD CONSTRAINT `fk_trims_model_year` FOREIGN KEY (`model_year_id`) REFERENCES `model_years` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_model_year` FOREIGN KEY (`model_year_id`) REFERENCES `model_years` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_trim_year` FOREIGN KEY (`trim_id`, `model_year_id`) REFERENCES `trims` (`id`, `model_year_id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_drivetrain` FOREIGN KEY (`drivetrain_code`) REFERENCES `drivetrains` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_body_type` FOREIGN KEY (`body_type_code`) REFERENCES `body_types` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_fuel_type` FOREIGN KEY (`fuel_type_code`) REFERENCES `fuel_types` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_powertrain_type` FOREIGN KEY (`powertrain_type_code`) REFERENCES `powertrain_types` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_exterior_color` FOREIGN KEY (`exterior_color_code`) REFERENCES `colors` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `vehicles` ADD CONSTRAINT `fk_vehicles_interior_color` FOREIGN KEY (`interior_color_code`) REFERENCES `colors` (`code`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `listings` ADD CONSTRAINT `fk_listings_vehicle` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `listings` ADD CONSTRAINT `fk_listings_dealer` FOREIGN KEY (`dealer_id`) REFERENCES `dealers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `listing_observations` ADD CONSTRAINT `fk_observations_listing` FOREIGN KEY (`listing_id`) REFERENCES `listings` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE `listing_observations` ADD CONSTRAINT `fk_observations_batch` FOREIGN KEY (`import_batch_id`) REFERENCES `import_batches` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT;
