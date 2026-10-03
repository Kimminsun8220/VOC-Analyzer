export default function(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const root = parentElement.querySelector('.criteria-table');
  // Capture the final cell value together with Save, including a still-focused cell.
  // Streamlit's native button otherwise races the cell's blur update.
  if (root.saveHandler) document.removeEventListener('click', root.saveHandler, true);
  const saveHandler = event => {
    const control = event.target.closest('button');
    if (!root.isConnected || !control || root.contains(control) || control.textContent.trim() !== '변경 내용 저장') return;
    event.preventDefault(); event.stopImmediatePropagation();
    const snapshot = Array.from(root.querySelectorAll('tbody tr')).map(tr => {
      const row = {id: tr.dataset.rowId};
      for (const field of ['category', 'name', 'definition']) row[field] = tr.querySelector(`[data-field="${field}"]`).value;
      return row;
    });
    setTriggerValue('action', {kind: 'save', rows: snapshot, revision: data.revision, nonce: crypto.randomUUID()});
  };
  root.saveHandler = saveHandler;
  document.addEventListener('click', saveHandler, true);
  const cleanup = () => document.removeEventListener('click', saveHandler, true);
  if (root.dataset.revision === String(data.revision)) return cleanup;
  root.dataset.revision = String(data.revision);
  root.replaceChildren();
  let menu = null;
  let drag = null;
  let pending = false;
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const closeMenu = () => { if (menu) menu.remove(); menu = null; };
  const rows = () => Array.from(root.querySelectorAll('tbody tr')).map(tr => {
    const row = {id: tr.dataset.rowId};
    for (const field of ['category', 'name', 'definition']) row[field] = tr.querySelector(`[data-field="${field}"]`).value;
    return row;
  });
  const send = (kind, extra = {}) => {
    if (pending) return;
    pending = true;
    closeMenu();
    status.textContent = '변경 중…';
    setTriggerValue('action', {kind, ...extra, rows: rows(), revision: data.revision, nonce: crypto.randomUUID()});
  };
  const publish = () => {
    if (!pending) setStateValue('edits', {revision: data.revision, rows: rows()});
  };
  const button = (label, action, className) => {
    const result = make('button', className, label);
    result.type = 'button';
    result.onclick = action;
    return result;
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
      for (const row of rows().filter(row => row.id !== id)) item(`${row.category} · ${row.name}`, () => send('merge', {source_id: id, target_id: row.id}));
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
  for (const title of ['', '대분류', '세부분류', '분류 기준', '']) {
    const th = make('th', '', title); th.scope = 'col'; headings.append(th);
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
      const input = make(field === 'definition' ? 'textarea' : 'input');
      if (field === 'definition') input.rows = 2;
      input.dataset.field = field; input.value = row[field];
      input.maxLength = field === 'definition' ? 1500 : 80;
      input.setAttribute('aria-label', `${row.name || '새 분류'} · ${field === 'category' ? '대분류' : field === 'name' ? '세부분류' : '분류 기준'}`);
      input.placeholder = field === 'category' ? '대분류' : field === 'name' ? '새 세부분류' : '분류 기준 (비우면 저장 시 보완)';
      if (field === 'definition') input.oninput = () => { input.style.height = 'auto'; input.style.height = `${Math.max(64, input.scrollHeight + 2)}px`; };
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
  table.append(body); scroll.append(table); root.append(scroll);
  const toolbar = make('div', 'criteria-toolbar');
  toolbar.append(button('행 추가', () => send('add')));
  const undo = button('되돌리기', () => send('undo')); undo.disabled = !data.can_undo; toolbar.append(undo);
  root.append(toolbar);
  const status = make('p', 'criteria-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); root.append(status);
  root.onkeydown = event => {
    if (event.key === 'Escape') { closeMenu(); drag = null; root.querySelectorAll('.criteria-drop, .criteria-dragging').forEach(el => el.classList.remove('criteria-drop', 'criteria-dragging')); status.textContent = ''; }
    if (menu && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); const items = Array.from(menu.querySelectorAll('button')); const current = items.indexOf(document.activeElement);
      items[(current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  root.onfocusout = event => { if (menu && event.relatedTarget && !root.contains(event.relatedTarget)) closeMenu(); };
  root.onpointerdown = event => { if (menu && !menu.contains(event.target)) closeMenu(); };
  root.querySelectorAll('textarea').forEach(input => { input.style.height = 'auto'; input.style.height = `${Math.max(64, input.scrollHeight + 2)}px`; });
  if (data.focus) {
    const focusRow = Array.from(body.children).find(tr => tr.dataset.rowId === data.focus);
    focusRow?.querySelector('[data-field="name"]')?.focus();
  }
  return cleanup;
}
