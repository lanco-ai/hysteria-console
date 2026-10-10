const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The 行程 day model is plain TypeScript with type-only imports, so it runs as is once types are stripped.
async function loadModel() {
  const sourcePath = path.resolve(__dirname, '../frontend/src/features/plans/scheduleModel.ts');
  const { transformWithOxc } = await import('vite');
  const { code } = await transformWithOxc(fs.readFileSync(sourcePath, 'utf8'), sourcePath, { lang: 'ts' });
  // The main context, so arrays and objects compare equal to the test's own literals.
  const module = new vm.SourceTextModule(code, { identifier: sourcePath });
  await module.link(async specifier => { throw new Error(`unexpected dependency: ${specifier}`); });
  await module.evaluate();
  return module.namespace;
}

const FRIDAY = '2026-10-09';
const SATURDAY = '2026-10-10';
const emptyDay = () => ({ blocks: [], routine_status: {}, note: '' });
const block = (changes = {}) => ({ id: 'late', title: '夜聚', category: 'life', start: '23:00', end: '07:00', weekdays: [4], ...changes });

test('an overnight block that runs only on Friday fills Friday evening and Saturday morning', async () => {
  const model = await loadModel();
  const routine = { blocks: [block()], targets: {} };

  const friday = model.dayEntries(FRIDAY, routine, emptyDay());
  assert.deepEqual(model.placeSegments(friday).map(piece => [piece.from, piece.to, piece.continued]), [[1380, 1440, false]]);
  assert.equal(model.allocation(friday).life, 60);

  const saturday = model.dayEntries(SATURDAY, routine, emptyDay());
  assert.equal(saturday.length, 1);
  assert.equal(saturday[0].carried, true);
  assert.deepEqual(model.placeSegments(saturday).map(piece => [piece.from, piece.to, piece.continued]), [[0, 420, true]]);
  assert.equal(model.allocation(saturday).life, 420);
  // At 03:00 on Saturday the Friday night block is the one running.
  assert.equal(model.containsMinute(saturday[0], 180), true);
  assert.equal(model.containsMinute(friday[0], 180), false);

  // Sunday has neither part.
  assert.deepEqual(model.dayEntries('2026-10-11', routine, emptyDay()), []);
});

test('a daily overnight routine still adds up to its full length each day', async () => {
  const model = await loadModel();
  const routine = { blocks: [block({ id: 'sleep', title: '睡觉', category: 'rest', weekdays: [0, 1, 2, 3, 4, 5, 6] })], targets: {} };
  const entries = model.dayEntries(SATURDAY, routine, emptyDay());
  assert.deepEqual(entries.map(entry => [entry.carried, model.segmentOf(entry).from, model.segmentOf(entry).to]), [[true, 0, 420], [false, 1380, 1440]]);
  assert.equal(model.allocation(entries).rest, 480);
});

test('the previous day carries its one-off overnight blocks and its marks on routine blocks', async () => {
  const model = await loadModel();
  const routine = { blocks: [block({ id: 'sleep', title: '睡觉', category: 'rest', weekdays: [4] })], targets: {} };
  const previous = {
    blocks: [{ id: 'party', title: '聚会', category: 'life', start: '22:00', end: '02:00', notes: '', status: 'done' }],
    routine_status: { sleep: 'skipped' },
  };
  const saturday = model.dayEntries(SATURDAY, routine, emptyDay(), [], previous);
  const party = saturday.find(entry => entry.id === 'party');
  assert.equal(party.carried, true);
  assert.equal(party.status, 'done');
  // Friday's sleep was skipped, so its morning part is not drawn or counted on Saturday.
  assert.equal(saturday.find(entry => entry.id === 'sleep').status, 'skipped');
  assert.deepEqual(model.placeSegments(saturday).map(piece => piece.entry.id), ['party']);
  assert.equal(model.allocation(saturday).rest, 0);
  assert.equal(model.allocation(saturday).life, 120);
});

test('a block ending at midnight stays on its own day', async () => {
  const model = await loadModel();
  const routine = { blocks: [block({ start: '22:00', end: '00:00' })], targets: {} };
  assert.equal(model.crossesMidnight(routine.blocks[0]), false);
  assert.deepEqual(model.dayEntries(SATURDAY, routine, emptyDay()), []);
  assert.equal(model.allocation(model.dayEntries(FRIDAY, routine, emptyDay())).life, 120);
});

test('overlap checks use the part of each block that falls on the day', async () => {
  const model = await loadModel();
  const routine = { blocks: [block()], targets: {} };
  const saturday = model.dayEntries(SATURDAY, routine, emptyDay());
  // A Saturday 06:00 breakfast meets the end of Friday night, a Saturday 23:30 call does not.
  assert.deepEqual(model.overlapsWith({ start: '06:00', end: '06:30' }, saturday).map(entry => entry.id), ['late']);
  assert.deepEqual(model.overlapsWith({ start: '23:30', end: '23:45' }, saturday), []);
  const friday = model.dayEntries(FRIDAY, routine, emptyDay());
  assert.deepEqual(model.overlapsWith({ start: '06:00', end: '06:30' }, friday), []);
  // A repeating overnight candidate that also ran the day before reaches into this morning.
  const breakfast = [{ ...friday[0], key: 'day:breakfast', id: 'breakfast', kind: 'day', start: '06:30', end: '07:30', carried: false }];
  assert.deepEqual(model.overlapsWith({ start: '23:00', end: '07:00' }, breakfast, '', { today: true, carried: false }), []);
  assert.deepEqual(model.overlapsWith({ start: '23:00', end: '07:00' }, breakfast, '', { today: false, carried: true }).map(entry => entry.id), ['breakfast']);
});
