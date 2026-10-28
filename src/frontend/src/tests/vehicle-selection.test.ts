import assert from 'node:assert/strict';
import test from 'node:test';
import type {CatalogMake, CatalogMakeDetail} from '../models/catalog.ts';
import {makeForQuery, suggestionsFor} from '../utils/vehicle-selection.ts';

const makes: CatalogMake[] = [
  {id: 1, name: 'Land Rover'},
  {id: 2, name: 'Land Rover SV'},
  {id: 3, name: 'Mercedes-Benz'},
  {id: 4, name: 'Toyota'},
];

const landRover: CatalogMakeDetail = {
  ...makes[0]!,
  models: [
    {id: 11, name: 'Range Rover', years: [{id: 111, year: 2020, trims: []}, {id: 112, year: 2021, trims: []}]},
    {id: 12, name: 'Range Rover Sport', years: [{id: 121, year: 2021, trims: []}]},
    {id: 13, name: 'Discovery Sport', years: [{id: 131, year: 2021, trims: []}]},
  ],
};

test('year and partial multiword make suggest matching makes without selecting prematurely', () => {
  assert.deepEqual(suggestionsFor('2021 land ro', makes, undefined).map(({label, selection}) => [label, selection]), [
    ['2021 Land Rover', undefined],
    ['2021 Land Rover SV', undefined],
  ]);
  assert.deepEqual(suggestionsFor('2021 mercedes-b', makes, undefined).map(({label}) => label), ['2021 Mercedes-Benz']);
});

test('full make transitions to year-filtered model suggestions with retained catalog IDs', () => {
  assert.equal(makeForQuery('2021 Land Rover Range Rover Sport', makes)?.id, 1);
  const suggestions = suggestionsFor('2021 Land Rover Range Rover S', makes, landRover);
  assert.equal(suggestions.length, 1);
  assert.equal(suggestions[0]?.label, '2021 Land Rover Range Rover Sport');
  assert.deepEqual(
    [suggestions[0]?.selection?.make.id, suggestions[0]?.selection?.model.id, suggestions[0]?.selection?.modelYear.id],
    [1, 12, 121],
  );
});

test('longest make prefix wins and partial model names handle punctuation', () => {
  assert.equal(makeForQuery('2021 Land Rover SV', makes)?.id, 2);
  assert.deepEqual(suggestionsFor('2021 Mercedes-Benz C', makes, {
    ...makes[2]!,
    models: [{id: 31, name: 'C-Class', years: [{id: 311, year: 2021, trims: []}]}],
  }).map(({label}) => label), ['2021 Mercedes-Benz C-Class']);
});

test('invalid query combinations and models unavailable in the requested year produce no selection', () => {
  assert.deepEqual(suggestionsFor('2021 Unknown', makes, undefined), []);
  assert.deepEqual(suggestionsFor('20 Land Rover', makes, landRover), []);
  assert.deepEqual(suggestionsFor('2020 Land Rover Range Rover Sport', makes, landRover), []);
  assert.deepEqual(suggestionsFor('2021 Land Rover', makes, {...landRover, id: 99}), []);
});
