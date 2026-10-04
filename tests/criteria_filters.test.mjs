import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../src/components/criteria_table.js', import.meta.url));
const {matchesFilters, filterOptions} = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const rows = [
  {id:'A', category:'배송', name:'속도'},
  {id:'B', category:'배송', name:'안내'},
  {id:'C', category:'고객 서비스', name:'안내'},
  {id:'D', category:'상품', name:'가격'},
  {id:'E', category:' ', name:''},
];

test('복수 값은 OR, 열 사이는 AND이며 원래 행과 순서를 보존한다', () => {
  const before = structuredClone(rows);
  const filters = {category:['배송','고객 서비스'], name:['안내']};
  assert.deepEqual(rows.filter(row => matchesFilters(row, filters)).map(row => row.id), ['B','C']);
  assert.deepEqual(rows, before);
});

test('필터 해제·전체 선택·빈 값·선택 없음의 표시 범위를 구분한다', () => {
  assert.equal(rows.filter(row => matchesFilters(row, {category:null,name:null})).length, 5);
  assert.equal(rows.filter(row => matchesFilters(row, {category:[],name:null})).length, 0);
  assert.deepEqual(rows.filter(row => matchesFilters(row, {category:[''],name:['']})).map(row => row.id), ['E']);
  assert.equal(rows.filter(row => matchesFilters(row, {category:['상품'],name:['안내']})).length, 0);
});

test('선택 목록은 다른 열의 필터를 반영하고 자기 열의 선택은 제외한다', () => {
  assert.deepEqual(filterOptions(rows, {category:['배송'],name:['속도']}, 'name'), ['속도','안내']);
  assert.deepEqual(filterOptions(rows, {category:['배송'],name:['안내']}, 'category'), ['고객 서비스','배송']);
});

test('중복 이름과 긴 이름을 보존하고 HTML 같은 값도 문자열로 처리한다', () => {
  const longName = '가'.repeat(80);
  assert.deepEqual(filterOptions([...rows,{category:'상품',name:longName},{category:'상품',name:'<img onerror=alert(1)>'}], {category:['상품'],name:null}, 'name'), ['<img onerror=alert(1)>',longName,'가격'].sort((a,b)=>a.localeCompare(b,'ko',{numeric:true})));
  assert.equal(filterOptions(rows,{category:null,name:null},'name').filter(value=>value==='안내').length,1);
});
