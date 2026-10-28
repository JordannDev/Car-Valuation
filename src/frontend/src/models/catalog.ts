/**
 * A vehicle manufacturer, such as Toyota or Ford.
 */
export interface CatalogMake {
  id: number;
  name: string;
}

/**
 * A trim available for a model year, such as XL or XLT.
 */
export interface CatalogTrim {
  id: number;
  name: string;
}

/**
 * A model year and the trims available for it.
 */
export interface CatalogYear {
  id: number;
  year: number;
  trims: CatalogTrim[];
}

/**
 * A vehicle model and the years it is available in.
 */
export interface CatalogModel {
  id: number;
  name: string;
  years: CatalogYear[];
}

export interface CatalogMakeDetail extends CatalogMake {
  models: CatalogModel[];
}

export interface CatalogSelection {
  make: CatalogMake;
  model: CatalogModel;
  modelYear: CatalogYear;
}
