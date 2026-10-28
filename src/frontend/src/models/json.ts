/** Check JSON values before using them as catalog or valuation data. */
export type JsonValue = string | number | boolean | null | JsonValue[] | JsonObject;
/** Valid JSON can still leave out a field that the app expects. */
export type JsonField = JsonValue | undefined;
export interface JsonObject {
  [key: string]: JsonField;
}
