export default function({data, parentElement, setTriggerValue}) {
  const root = parentElement.querySelector('.response-table');
  if (root.dataset.signature === data.signature) {
    root.install?.();
    return root.cleanListeners;
  }
  root.dispose?.();
  root.dataset.signature = data.signature;
  root.replaceChildren();
  let menu = null;
  let opener = null;
  let pending = false;
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (label, handler, className = '') => {
    const node = make('button', className, label);
    node.type = 'button'; node.onclick = handler; return node;
  };
  const closeMenu = (restore = false) => {
    menu?.remove(); menu = null;
    opener?.setAttribute('aria-expanded', 'false');
    opener?.classList.remove('is-editing');
    if (restore) opener?.focus({preventScroll: true});
  };
  const send = (kind, extra = {}) => {
    if (pending) return;
    pending = true; closeMenu();
    root.setAttribute('aria-busy', 'true');
    setTriggerValue('action', {kind, ...extra, signature: data.signature, nonce: crypto.randomUUID()});
  };
  root.removeAttribute('aria-busy');
  const placeMenu = focus => {
    root.append(menu);
    const rootRect = root.getBoundingClientRect(); const anchor = opener.getBoundingClientRect();
    const dialogRect = root.closest('[role="dialog"]')?.getBoundingClientRect();
    const lower = Math.min(window.innerHeight, dialogRect?.bottom || window.innerHeight) - 16;
    const upper = Math.max(12, (dialogRect?.top || 0) + 16);
    menu.style.maxHeight = `${lower - upper}px`;
    const rect = menu.getBoundingClientRect();
    const left = Math.max(rootRect.left, Math.min(anchor.left, rootRect.right - rect.width));
    const top = Math.max(upper, Math.min(anchor.bottom + 4, lower - rect.height));
    menu.style.left = `${left - rootRect.left}px`; menu.style.top = `${top - rootRect.top}px`;
    focus.focus({preventScroll: true});
    menu.addEventListener('keydown', event => {
      if (event.key === 'Tab') {
        const controls = [...menu.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)')];
        if (event.shiftKey && event.target === controls[0]) { event.preventDefault(); controls.at(-1).focus(); }
        if (!event.shiftKey && event.target === controls.at(-1)) { event.preventDefault(); controls[0].focus(); }
      }
    });
  };
  const openOpinionEditor = (row, cell, field) => {
    if (pending) return;
    closeMenu(); opener = cell; opener.setAttribute('aria-expanded', 'true');
    opener.classList.add('is-editing');
    menu = make('div', 'response-filter response-inline-editor');
    menu.style.left = '0px'; menu.style.top = '0px';
    const sentiment = field === 'sentiment'; const title = sentiment ? '감성' : '분류';
    const options = sentiment ? data.sentiment_options : data.classification_options;
    menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', `${title} 수정`);
    menu.append(make('strong', '', `${title} 수정`));
    const draft = row.opinions.map(item => item[field]);
    const save = button('저장', () => send(sentiment ? 'sentiment' : 'classify',
      {id: row.id, [sentiment ? 'sentiments' : 'code_ids']: draft}), 'response-filter-apply');
    save.disabled = true;
    const selects = [];
    const indices = sentiment ? row.sentiment_indices : row.opinions.map((_, index) => index);
    indices.forEach((index, position) => {
      const item = row.opinions[index];
      const label = make('label', 'response-opinion-label');
      const classification = data.classification_options.find(option => option.value === item.code_id)?.label || '';
      label.append(make('span', 'response-opinion-evidence', sentiment
        ? `${classification} · “${item.evidence_text}”` : `“${item.evidence_text}” · ${item.sentiment}`));
      const select = make('select', 'response-classification-choice');
      select.setAttribute('aria-label', `의견 ${position + 1} ${title}: ${item.evidence_text}`);
      for (const option of options) {
        const node = make('option', '', option.label); node.value = option.value; select.append(node);
      }
      select.value = item[field];
      select.onchange = () => {
        draft[index] = select.value;
        save.disabled = draft.every((value, index) => value === row.opinions[index][field]);
      };
      selects.push(select); label.append(select); menu.append(label);
    });
    const footer = make('div', 'response-filter-actions');
    footer.append(button('취소', () => closeMenu(true)), save); menu.append(footer);
    placeMenu(selects[0]);
  };
  const active = column => column.filter.values !== undefined || Boolean(column.filter.search);
  const openFilter = (column, control) => {
    if (opener === control && menu) { closeMenu(true); return; }
    closeMenu(); opener = control; opener.setAttribute('aria-expanded', 'true');
    menu = make('div', 'response-filter');
    menu.style.left = '0px'; menu.style.top = '0px';
    menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', `${column.label} 필터`);
    menu.append(make('strong', '', `${column.label} 필터`));
    let query = null;
    if (column.key === 'VOC 원문') {
      const label = make('label', 'response-query-label', '원문 포함');
      query = make('input', 'response-filter-search'); query.type = 'search';
      query.placeholder = '검색어 입력'; query.value = column.filter.search || '';
      query.maxLength = 5000; query.setAttribute('aria-label', '원문 포함 검색');
      label.append(query); menu.append(label);
    }
    const valueSearch = make('input', 'response-filter-search'); valueSearch.type = 'search';
    valueSearch.placeholder = '값 찾기'; valueSearch.setAttribute('aria-label', `${column.label} 값 찾기`);
    menu.append(valueSearch);
    const draft = new Set(column.filter.values ?? column.options.map(option => option.value));
    const allLabel = make('label', 'response-filter-option response-filter-all');
    const all = make('input'); all.type = 'checkbox'; all.setAttribute('aria-label', `${column.label} 전체 선택`);
    allLabel.append(all, make('span', '', '전체 선택')); menu.append(allLabel);
    const list = make('div', 'response-filter-values'); menu.append(list);
    let visible = column.options;
    const updateAll = () => {
      all.checked = visible.length > 0 && visible.every(option => draft.has(option.value));
      all.indeterminate = !all.checked && visible.some(option => draft.has(option.value));
      all.disabled = visible.length === 0;
    };
    const drawValues = () => {
      list.replaceChildren();
      visible = column.options.filter(option => option.label.toLocaleLowerCase().includes(valueSearch.value.toLocaleLowerCase()));
      for (const option of visible) {
        const label = make('label', 'response-filter-option'); label.title = option.label;
        const check = make('input'); check.type = 'checkbox'; check.checked = draft.has(option.value);
        check.setAttribute('aria-label', option.label);
        check.onchange = () => { if (check.checked) draft.add(option.value); else draft.delete(option.value); updateAll(); };
        label.append(check, make('span', '', option.label)); list.append(label);
      }
      if (!visible.length) list.append(make('p', 'response-filter-empty', '일치하는 값이 없습니다.'));
      updateAll();
    };
    valueSearch.oninput = drawValues;
    all.onchange = () => {
      for (const option of visible) { if (all.checked) draft.add(option.value); else draft.delete(option.value); }
      drawValues();
    };
    const footer = make('div', 'response-filter-actions');
    footer.append(button('해제', () => send('clear', {column: column.key})),
      button('적용', () => send('filter', {column: column.key,
        values: column.options.every(option => draft.has(option.value)) ? null : [...draft], search: query?.value || ''}), 'response-filter-apply'));
    menu.append(footer); drawValues(); placeMenu(query || valueSearch);
    menu.onkeydown = event => {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT' && event.target.type === 'search') {
        event.preventDefault(); footer.querySelector('.response-filter-apply').click();
      }
    };
  };
  const scroll = make('div', 'response-table-scroll'); scroll.tabIndex = 0;
  scroll.setAttribute('aria-label', '고객 원문 표 스크롤');
  const table = make('table'); table.setAttribute('aria-label', '고객 원문 목록');
  const colgroup = make('colgroup');
  for (const name of ['selection', 'text', 'class', 'sentiment', 'state']) colgroup.append(make('col', `response-col-${name}`));
  table.append(colgroup);
  const head = make('thead'); const headers = make('tr');
  const selection = make('th'); selection.scope = 'col'; selection.append(make('span', 'response-visually-hidden', '응답 선택')); headers.append(selection);
  for (const column of data.columns) {
    const th = make('th'); th.scope = 'col';
    const control = button('', () => openFilter(column, control), `response-column-filter${active(column) ? ' is-active' : ''}`);
    control.append(make('span', 'response-column-title', column.label));
    const icon = make('span', 'response-filter-icon', active(column) ? '⏷' : '▾'); icon.setAttribute('aria-hidden', 'true'); control.append(icon);
    control.setAttribute('aria-label', `${column.label} 필터${active(column) ? ' 적용 중' : ''}`);
    control.title = `${column.label} 필터${active(column) ? ' · 적용 중' : ''}`;
    control.setAttribute('aria-haspopup', 'dialog'); control.setAttribute('aria-expanded', 'false');
    th.append(control); headers.append(th);
  }
  head.append(headers); table.append(head);
  const body = make('tbody');
  for (const row of data.rows) {
    const tr = make('tr', row.id === data.selected ? 'is-selected' : ''); tr.dataset.rowId = row.id;
    const select = () => send('select', {id: row.id === data.selected ? null : row.id});
    tr.onclick = select;
    const selectCell = make('td');
    const control = button('', event => { event.stopPropagation(); select(); }, 'response-select');
    control.setAttribute('aria-label', `응답 선택: ${row['VOC 원문']}`);
    control.setAttribute('aria-pressed', String(row.id === data.selected));
    control.append(make('span', 'response-selection-mark', row.id === data.selected ? '✓' : ''));
    selectCell.append(control); tr.append(selectCell);
    for (const column of data.columns) {
      const cell = make('td');
      const text = column.key === '응답 상태' ? data.status_labels[row[column.key]] || row[column.key] : row[column.key];
      const content = make('span', column.key === '응답 상태' ? 'response-state' : 'response-cell-text', text);
      content.title = row[column.key]; cell.append(content); tr.append(cell);
      if (['분류', '감성'].includes(column.key) && row.opinions?.length
          && (column.key !== '감성' || row.sentiment_indices?.length)) {
        const field = column.key === '감성' ? 'sentiment' : 'code_id';
        cell.classList.add('response-editable-cell'); cell.tabIndex = 0;
        cell.setAttribute('aria-label', `${column.key} 수정: ${row['VOC 원문']}`);
        cell.setAttribute('aria-haspopup', 'dialog'); cell.setAttribute('aria-expanded', 'false');
        cell.title = `더블클릭 또는 Enter로 ${column.key} 수정`;
        // 첫 클릭에서 행 선택·재실행하면 두 번째 클릭이 다른 DOM에 도착한다.
        cell.onclick = event => event.stopPropagation();
        cell.ondblclick = event => { event.stopPropagation(); openOpinionEditor(row, cell, field); };
        cell.onkeydown = event => {
          if (event.key === 'Enter' || event.key === 'F2') {
            event.preventDefault(); event.stopPropagation(); openOpinionEditor(row, cell, field);
          }
        };
        cell.onpointerup = event => {
          if (event.pointerType === 'touch') { event.stopPropagation(); openOpinionEditor(row, cell, field); }
        };
      }
    }
    body.append(tr);
  }
  if (!data.rows.length) {
    const tr = make('tr'); const cell = make('td', 'response-empty', '조건에 맞는 응답이 없습니다. 열 필터를 해제하거나 전체 보기를 눌러주세요.');
    cell.colSpan = 5; tr.append(cell); body.append(tr);
  }
  table.append(body); scroll.append(table); root.append(scroll);
  const outside = event => { if (menu && !menu.contains(event.target) && !opener.contains(event.target)) closeMenu(); };
  root.install = () => document.addEventListener('pointerdown', outside, true);
  root.cleanListeners = () => document.removeEventListener('pointerdown', outside, true);
  root.install();
  root.onkeydown = event => {
    if (event.key === 'Escape' && menu) { event.preventDefault(); event.stopPropagation(); closeMenu(true); }
  };
  root.dispose = () => { root.cleanListeners(); closeMenu(); };
  return root.cleanListeners;
}
