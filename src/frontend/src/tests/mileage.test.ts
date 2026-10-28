import assert from 'node:assert/strict';
import test from 'node:test';
import {
  defaultMileageUnit,
  formatMileage,
  formatMileageEdit,
  formatMileageInput,
  getAdjacentDigitSelection,
  fromMiles,
  parseMileage,
  toMiles,
} from '../utils/mileage.ts';

test('formats valid mileage input while preserving invalid edits for validation', () => {
  assert.equal(formatMileageInput(''), '');
  assert.equal(formatMileageInput('150000'), '150,000');
  assert.equal(formatMileageInput('1,500,000'), '1,500,000');
  assert.equal(formatMileageInput('-150000'), '-150000');
  assert.equal(formatMileageInput('150000.5'), '150000.5');
  assert.equal(formatMileageInput('1e6'), '1e6');
  assert.equal(formatMileageInput('150000mi'), '150000mi');
  assert.equal(formatMileageInput('9007199254740992'), '9007199254740992');
  assert.equal(formatMileageInput('9,007,199,254,740,992'), '9,007,199,254,740,992');
  assert.equal(formatMileageInput('15,00'), '1,500');
  assert.equal(formatMileageInput('1,,500'), '1,500');
});

test('sequentially typing digits keeps grouping and allows values beyond four digits', () => {
  for (const [digits, expected] of [['15000', '15,000'], ['150000', '150,000']]) {
    let value = '';

    for (const digit of digits) {
      const rawEdit = value + digit;
      const edit = formatMileageEdit(rawEdit, rawEdit.length, rawEdit.length);
      value = edit.value;
    }

    assert.equal(value, expected);
  }
});

test('parseMileage retains blank, valid, and invalid results', () => {
  assert.equal(parseMileage(''), undefined);
  assert.equal(parseMileage('  '), undefined);
  assert.equal(parseMileage('150000'), 150_000);
  assert.equal(parseMileage('150,000'), 150_000);
  assert.equal(parseMileage('-1'), null);
  assert.equal(parseMileage('1.5'), null);
  assert.equal(parseMileage('1e3'), null);
  assert.equal(parseMileage('9,007,199,254,740,992'), null);
});

test('formats display values as rounded en-US whole numbers', () => {
  assert.equal(formatMileage(149_999.6), '150,000');
  assert.equal(formatMileage(1_000_000), '1,000,000');
});

test('uses server country before explicit browser language regions', () => {
  assert.equal(defaultMileageUnit(['en-US'], 'CA'), 'kilometers');
  assert.equal(defaultMileageUnit(['en-CA'], 'us'), 'miles');
  assert.equal(defaultMileageUnit(['en-GB', 'fr-CA']), 'kilometers');
  assert.equal(defaultMileageUnit(['en-CA', 'en-US']), 'kilometers');
  assert.equal(defaultMileageUnit(['not_a_locale', 'en-US']), 'miles');
  assert.equal(defaultMileageUnit(['en-GB']), 'miles');
});

test('converts using the exact kilometer to mile ratio', () => {
  assert.equal(fromMiles(1, 'kilometers'), 1.609344);
  assert.equal(toMiles(1.609344, 'kilometers'), 1);
  assert.equal(fromMiles(123, 'miles'), 123);
  assert.equal(toMiles(123, 'miles'), 123);
});

test('maps selection positions through inserted or removed grouping commas', () => {
  assert.deepEqual(formatMileageEdit('150000', 3, 3), {
    value: '150,000',
    selectionStart: 3,
    selectionEnd: 3,
  });
  assert.deepEqual(formatMileageEdit('15000', 2, 4), {
    value: '15,000',
    selectionStart: 2,
    selectionEnd: 5,
  });
  assert.deepEqual(formatMileageEdit('1000', 1, 1), {
    value: '1,000',
    selectionStart: 1,
    selectionEnd: 1,
  });
  assert.deepEqual(formatMileageEdit('-15000', 3, 3), {
    value: '-15000',
    selectionStart: 3,
    selectionEnd: 3,
  });
});

test('selects the neighboring digit when deleting beside a grouping comma', () => {
  assert.deepEqual(getAdjacentDigitSelection('15,000', 3, 3, 'Backspace'), {start: 1, end: 2});
  assert.deepEqual(getAdjacentDigitSelection('15,000', 2, 2, 'Delete'), {start: 3, end: 4});
  assert.equal(getAdjacentDigitSelection('15,000', 4, 4, 'Backspace'), null);
  assert.equal(getAdjacentDigitSelection('15,000', 2, 3, 'Backspace'), null);
});

test('mileage accepts blank, plain or grouped nonnegative safe integers only', () => {
  assert.equal(parseMileage(''), undefined);
  assert.equal(parseMileage('150000'), 150_000);
  assert.equal(parseMileage('150,000'), 150_000);
  assert.equal(parseMileage('9,007,199,254,740,991'), Number.MAX_SAFE_INTEGER);
  for (const invalid of ['-1', '1.5', '1,50', '1,000,00', '9007199254740992', '1e3']) {
    assert.equal(parseMileage(invalid), null, `${invalid} should be rejected`);
  }
});
