const filterFields = ['category', 'name'];
const filterValue = (row, field) => (row[field] || '').trim();

export function matchesFilters(row, filters, except = null) {
  return filterFields.every(field => field === except || filters[field] == null || filters[field].includes(filterValue(row, field)));
}

export function filterOptions(rows, filters, field) {
  return [...new Set(rows.filter(row => matchesFilters(row, filters, field)).map(row => filterValue(row, field)))].sort((a, b) => a.localeCompare(b, 'ko', {numeric: true}));
}

export default function(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const root = parentElement.querySelector('.criteria-table');
  if (root.dataset.bookId !== data.book_id) {
    root.dataset.bookId = data.book_id;
    root.filters = data.filters || {category: null, name: null};
    root.keptRows = new Set(data.kept_rows || []);
    delete root.dataset.revision;
  }
  const fitCell = input => {
    input.style.height = 'auto';
    input.style.height = `${Math.max(parseFloat(getComputedStyle(input).minHeight), input.scrollHeight + 2)}px`;
  };
  root.resizeObserver?.disconnect();
  let lastWidth = null;
  const resizeObserver = new ResizeObserver(entries => {
    const width = entries[0].contentRect.width;
    if (width === lastWidth) return;
    lastWidth = width;
    root.querySelectorAll('textarea').forEach(fitCell);
  });
  root.resizeObserver = resizeObserver;
  resizeObserver.observe(root);
  // Capture the final cell value together with Save, including a still-focused cell.
  // Streamlit's native button otherwise races the cell's blur update.
  if (root.saveHandler) document.removeEventListener('click', root.saveHandler, true);
  const saveHandler = event => {
    const control = event.target.closest('button');
    if (!root.isConnected || !control || root.contains(control) || control.textContent.trim() !== '분류 기준표 저장') return;
    event.preventDefault(); event.stopImmediatePropagation();
    // Hidden rows remain in the snapshot so filtering never deletes saved codes.
    const snapshot = Array.from(root.querySelectorAll('tbody tr[data-row-id]')).map(tr => {
      const row = {id: tr.dataset.rowId};
      for (const field of ['category', 'name', 'definition']) row[field] = tr.querySelector(`[data-field="${field}"]`).value;
      return row;
    });
    setTriggerValue('action', {kind: 'save', rows: snapshot, filters: root.filters, kept_rows: [...root.keptRows], revision: data.revision, nonce: crypto.randomUUID()});
  };
  root.saveHandler = saveHandler;
  document.addEventListener('click', saveHandler, true);
  if (root.outsideHandler) document.removeEventListener('pointerdown', root.outsideHandler, true);
  const outsideHandler = event => { if (!root.contains(event.target)) root.closeMenu?.(); };
  root.outsideHandler = outsideHandler;
  document.addEventListener('pointerdown', outsideHandler, true);
  const cleanup = () => { document.removeEventListener('click', saveHandler, true); document.removeEventListener('pointerdown', outsideHandler, true); resizeObserver.disconnect(); };
  if (root.dataset.revision === String(data.revision)) return cleanup;
  root.closeMenu?.();
  root.keptRows = new Set(data.kept_rows || []);
  root.dataset.revision = String(data.revision);
  root.replaceChildren();
  let menu = null;
  let menuTrigger = null;
  let drag = null;
  let pending = false;
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const closeMenu = (restoreFocus = false) => {
    if (menu) menu.remove();
    menu = null;
    if (restoreFocus) menuTrigger?.focus();
    menuTrigger?.setAttribute('aria-expanded', 'false');
    menuTrigger = null;
  };
  root.closeMenu = closeMenu;
  const rows = () => Array.from(root.querySelectorAll('tbody tr[data-row-id]')).map(tr => {
    const row = {id: tr.dataset.rowId};
    for (const field of ['category', 'name', 'definition']) row[field] = tr.querySelector(`[data-field="${field}"]`).value;
    return row;
  });
  const send = (kind, extra = {}) => {
    if (pending) return;
    pending = true;
    closeMenu();
    status.textContent = '변경 중…';
    setTriggerValue('action', {kind, ...extra, rows: rows(), filters: root.filters, kept_rows: [...root.keptRows], revision: data.revision, nonce: crypto.randomUUID()});
  };
  const publish = () => {
    if (!pending) setStateValue('edits', {revision: data.revision, rows: rows(), filters: root.filters, kept_rows: [...root.keptRows]});
  };
  const button = (label, action, className) => {
    const result = make('button', className, label);
    result.type = 'button';
    result.onclick = action;
    return result;
  };
  const positionMenu = (x, y) => {
    const bounds = root.getBoundingClientRect();
    const width = menu.getBoundingClientRect().width;
    menu.style.left = `${Math.max(0, Math.min(x - bounds.left, bounds.width - width))}px`;
    menu.style.top = `${Math.max(0, y - bounds.top)}px`;
    const bottom = menu.getBoundingClientRect().bottom;
    if (bottom > window.innerHeight - 8) menu.style.top = `${Math.max(0, y - bounds.top - bottom + window.innerHeight - 8)}px`;
  };
  const activeFilters = () => filterFields.some(field => root.filters[field] !== null);
  const filterButtons = {};
  const refreshFilters = () => {
    let shown = 0;
    for (const tr of body.querySelectorAll('tr[data-row-id]')) {
      const row = {id: tr.dataset.rowId, category: tr.querySelector('[data-field="category"]').value, name: tr.querySelector('[data-field="name"]').value};
      tr.hidden = !matchesFilters(row, root.filters) && !root.keptRows.has(row.id);
      if (!tr.hidden) { shown += 1; tr.querySelectorAll('textarea').forEach(fitCell); }
    }
    emptyRow.hidden = shown > 0;
    emptyText.textContent = activeFilters() ? '조건에 맞는 분류가 없습니다.' : '분류를 추가해주세요.';
    emptyClear.hidden = !activeFilters();
    for (const field of filterFields) {
      const active = root.filters[field] !== null;
      filterButtons[field].classList.toggle('criteria-filter-active', active);
      filterButtons[field].title = active ? '필터 적용 중' : '필터';
      filterButtons[field].querySelector('path').setAttribute('d', active ? 'M3 4h18l-7 8v7l-4 2v-9z' : 'm6 9 6 6 6-6');
      filterButtons[field].querySelector('.criteria-filter-state').textContent = active ? '적용 중' : '';
    }
    filterClear.hidden = !activeFilters();
    filterCount.textContent = activeFilters() ? `${data.rows.length}개 중 ${shown}개 표시` : '';
  };
  const clearFilters = () => {
    root.filters = {category: null, name: null}; root.keptRows.clear();
    closeMenu(true); refreshFilters(); publish();
  };
  const openFilter = (field, trigger) => {
    if (menuTrigger === trigger) { closeMenu(true); return; }
    closeMenu();
    menuTrigger = trigger;
    trigger.setAttribute('aria-expanded', 'true');
    const label = field === 'category' ? '대분류' : '세부분류';
    const options = filterOptions(rows(), root.filters, field);
    let selected = new Set(root.filters[field] === null ? options : root.filters[field]);
    menu = make('div', 'criteria-filter-menu');
    menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', `${label} 필터 선택`);
    menu.append(make('div', 'criteria-filter-title', `${label} 필터`));
    const search = make('input', 'criteria-filter-search'); search.type = 'search'; search.placeholder = '검색';
    search.setAttribute('aria-label', `${label} 필터 검색`); menu.append(search);
    const selectAll = make('label', 'criteria-filter-option criteria-filter-all');
    const allBox = make('input'); allBox.type = 'checkbox';
    const allText = make('span', '', '전체 선택'); selectAll.append(allBox, allText); menu.append(selectAll);
    const choices = make('div', 'criteria-filter-options'); menu.append(choices);
    const matching = () => options.filter(value => value.toLocaleLowerCase('ko').includes(search.value.trim().toLocaleLowerCase('ko')));
    const syncAll = () => {
      const values = matching(); const count = values.filter(value => selected.has(value)).length;
      allBox.checked = values.length > 0 && count === values.length;
      allBox.indeterminate = count > 0 && count < values.length;
      allBox.disabled = !values.length;
      allText.textContent = search.value.trim() ? '검색 결과 전체 선택' : '전체 선택';
    };
    const drawChoices = () => {
      choices.replaceChildren();
      const values = matching();
      if (!values.length) choices.append(make('p', 'criteria-filter-empty', '검색 결과가 없습니다.'));
      for (const value of values) {
        const option = make('label', 'criteria-filter-option');
        const checkbox = make('input'); checkbox.type = 'checkbox'; checkbox.checked = selected.has(value);
        checkbox.onchange = () => { if (checkbox.checked) selected.add(value); else selected.delete(value); syncAll(); };
        option.append(checkbox, make('span', '', value || '(빈 값)')); choices.append(option);
      }
      syncAll();
    };
    allBox.onchange = () => { for (const value of matching()) { if (allBox.checked) selected.add(value); else selected.delete(value); } drawChoices(); };
    search.oninput = () => { selected = new Set(matching()); drawChoices(); };
    const controls = make('div', 'criteria-filter-actions');
    controls.append(button('이 열 필터 해제', () => { root.filters[field] = null; root.keptRows.clear(); closeMenu(true); refreshFilters(); publish(); }));
    controls.append(button('취소', () => closeMenu(true)));
    controls.append(button('적용', () => {
      root.filters[field] = options.length && options.every(value => selected.has(value)) ? null : [...selected];
      root.keptRows.clear(); closeMenu(true); refreshFilters(); publish();
    }, 'criteria-filter-apply'));
    menu.append(controls); root.append(menu); drawChoices();
    const point = trigger.getBoundingClientRect(); positionMenu(point.left, point.bottom);
    search.focus();
  };
  const openMenu = (id, x, y, targets = false) => {
    closeMenu();
    const bounds = root.getBoundingClientRect();
    menu = make('div', 'criteria-menu');
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', targets ? '합칠 분류 선택' : '분류 행 메뉴');
    menu.style.left = `${Math.max(0, Math.min(x - bounds.left, bounds.width - 220))}px`;
    menu.style.top = `${Math.max(0, y - bounds.top)}px`;
    const item = (label, action) => {
      const control = button(label, action);
      control.setAttribute('role', 'menuitem');
      menu.append(control);
    };
    if (targets) {
      const visibleIds = new Set(Array.from(root.querySelectorAll('tbody tr[data-row-id]')).filter(tr => !tr.hidden).map(tr => tr.dataset.rowId));
      for (const row of rows().filter(row => row.id !== id && visibleIds.has(row.id))) item(`${row.category} · ${row.name}`, () => send('merge', {source_id: id, target_id: row.id}));
      if (!menu.childElementCount) menu.append(make('p', 'criteria-filter-empty', '합칠 다른 행이 없습니다.'));
    } else {
      item('분리하기', () => send('split', {source_id: id}));
      item('다른 행과 합치기', () => openMenu(id, x, y, true));
      item('행 삭제', () => send('delete', {source_id: id}));
    }
    root.append(menu);
    const menuBounds = menu.getBoundingClientRect();
    if (menuBounds.bottom > window.innerHeight - 8) {
      menu.style.top = `${Math.max(0, parseFloat(menu.style.top) - (menuBounds.bottom - window.innerHeight + 8))}px`;
    }
    menu.querySelector('button')?.focus();
  };
  const scroll = make('div', 'criteria-scroll');
  const table = make('table');
  table.setAttribute('aria-label', '분류 기준표 편집');
  const head = make('thead');
  const headings = make('tr');
  for (const [index, title] of ['', '대분류', '세부분류', '분류 기준', ''].entries()) {
    const field = index === 1 ? 'category' : index === 2 ? 'name' : null;
    const th = make('th'); th.scope = 'col';
    if (field) {
      const heading = make('div', 'criteria-filter-heading'); heading.append(make('span', '', title));
      const control = button('', () => openFilter(field, control), 'criteria-filter-button');
      control.setAttribute('aria-label', `${title} 필터`); control.setAttribute('aria-haspopup', 'dialog'); control.setAttribute('aria-expanded', 'false');
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); svg.append(path); control.append(svg);
      control.append(make('span', 'criteria-filter-state')); filterButtons[field] = control;
      heading.append(control); th.append(heading);
    } else th.textContent = title;
    headings.append(th);
  }
  head.append(headings); table.append(head);
  const body = make('tbody');
  for (const row of data.rows) {
    const tr = make('tr'); tr.dataset.rowId = row.id;
    tr.oncontextmenu = event => { event.preventDefault(); openMenu(row.id, event.clientX, event.clientY); };
    const gripCell = make('td');
    const grip = button('⠿', () => {}, 'criteria-grip');
    grip.setAttribute('aria-label', `행 끌기: ${row.name || '새 분류'}`);
    grip.title = '다른 행에 끌어 놓아 합치기';
    grip.onpointerdown = event => {
      if (event.button !== 0 || pending) return;
      closeMenu();
      drag = {id: row.id, x: event.clientX, y: event.clientY, active: false, target: null};
      grip.setPointerCapture(event.pointerId);
    };
    grip.onpointermove = event => {
      if (!drag) return;
      if (!drag.active && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 8) return;
      drag.active = true; tr.classList.add('criteria-dragging');
      root.querySelectorAll('.criteria-drop').forEach(item => item.classList.remove('criteria-drop'));
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('tr[data-row-id]');
      drag.target = target && root.contains(target) && target.dataset.rowId !== drag.id ? target.dataset.rowId : null;
      if (drag.target) target.classList.add('criteria-drop');
      status.textContent = drag.target ? '놓으면 두 분류가 합쳐집니다.' : '합칠 행 위에 놓으세요.';
      if (event.clientY > window.innerHeight - 40) window.scrollBy(0, 12);
      if (event.clientY < 40) window.scrollBy(0, -12);
    };
    const endDrag = cancel => {
      if (!drag) return;
      const current = drag; drag = null;
      tr.classList.remove('criteria-dragging');
      root.querySelectorAll('.criteria-drop').forEach(item => item.classList.remove('criteria-drop'));
      status.textContent = '';
      if (!cancel && current.active && current.target) send('merge', {source_id: current.id, target_id: current.target});
    };
    grip.onpointerup = () => endDrag(false);
    grip.onpointercancel = () => endDrag(true);
    gripCell.append(grip); tr.append(gripCell);
    for (const field of ['category', 'name', 'definition']) {
      const td = make('td');
      const input = make('textarea');
      input.rows = 1;
      input.dataset.field = field; input.value = row[field];
      input.maxLength = field === 'definition' ? 1500 : 80;
      input.setAttribute('aria-label', `${row.name || '새 분류'} · ${field === 'category' ? '대분류' : field === 'name' ? '세부분류' : '분류 기준'}`);
      input.placeholder = field === 'category' ? '대분류' : field === 'name' ? '새 세부분류' : '분류 기준 (비우면 저장 시 보완)';
      input.oninput = () => {
        if (field !== 'definition') input.value = input.value.replace(/[\r\n]+/g, ' ');
        // Keep the edited row visible until the user applies another filter.
        if (field !== 'definition' && activeFilters()) root.keptRows.add(row.id);
        fitCell(input);
      };
      if (field !== 'definition') input.onkeydown = event => { if (event.key === 'Enter') event.preventDefault(); };
      input.onchange = publish;
      td.append(input); tr.append(td);
    }
    const menuCell = make('td');
    const more = button('⋮', event => {
      const point = more.getBoundingClientRect(); openMenu(row.id, point.left, point.bottom);
    }, 'criteria-menu-button');
    more.setAttribute('aria-label', `${row.name || '새 분류'} 행 메뉴`);
    more.setAttribute('aria-haspopup', 'menu');
    more.onkeydown = event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        event.preventDefault(); const point = more.getBoundingClientRect(); openMenu(row.id, point.left, point.bottom);
      }
    };
    menuCell.append(more); tr.append(menuCell); body.append(tr);
  }
  const emptyRow = make('tr', 'criteria-empty-row');
  const emptyCell = make('td'); emptyCell.colSpan = 5;
  const emptyText = make('span'); const emptyClear = button('필터 해제', clearFilters);
  emptyCell.append(emptyText, emptyClear);
  emptyRow.append(emptyCell); body.append(emptyRow);
  table.append(body); scroll.append(table); root.append(scroll);
  const toolbar = make('div', 'criteria-toolbar');
  toolbar.append(button('행 추가', () => send('add')));
  const undo = button('되돌리기', () => send('undo')); undo.disabled = !data.can_undo; toolbar.append(undo);
  const filterClear = button('필터 해제', clearFilters, 'criteria-clear-filters'); toolbar.append(filterClear);
  const filterCount = make('span', 'criteria-filter-count'); filterCount.setAttribute('role', 'status'); filterCount.setAttribute('aria-live', 'polite'); toolbar.append(filterCount);
  root.append(toolbar);
  const status = make('p', 'criteria-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); root.append(status);
  root.onkeydown = event => {
    if (event.key === 'Escape') { closeMenu(true); drag = null; root.querySelectorAll('.criteria-drop, .criteria-dragging').forEach(el => el.classList.remove('criteria-drop', 'criteria-dragging')); status.textContent = ''; }
    if (menu?.getAttribute('role') === 'menu' && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); const items = Array.from(menu.querySelectorAll('button')); const current = items.indexOf(document.activeElement);
      items[(current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  root.onfocusout = event => { if (menu && event.relatedTarget && !menu.contains(event.relatedTarget) && !menuTrigger?.contains(event.relatedTarget)) closeMenu(); };
  root.onpointerdown = event => { if (menu && !menu.contains(event.target) && !menuTrigger?.contains(event.target)) closeMenu(); };
  if (data.focus) root.keptRows.add(data.focus);
  refreshFilters();
  root.querySelectorAll('textarea').forEach(fitCell);
  if (data.focus) {
    const focusRow = Array.from(body.children).find(tr => tr.dataset.rowId === data.focus);
    focusRow?.querySelector('[data-field="name"]')?.focus();
  }
  return cleanup;
}
