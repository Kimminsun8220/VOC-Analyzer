export default function(component) {
  const {data, parentElement, setTriggerValue} = component;
  const root = parentElement.querySelector('.result-bars');
  if (root.dataset.signature === data.signature) return;
  root.dataset.signature = data.signature;
  root.removeAttribute('aria-busy');
  root.replaceChildren();
  let pending = false, menu = null, drag = null, menuOwner = null;
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const closeMenu = (restore = false) => {
    if (menu) menu.remove();
    menu = null;
    menuOwner?.setAttribute('aria-expanded', 'false');
    if (restore) menuOwner?.focus();
  };
  const send = (kind, source_id, target_id) => {
    if (pending) return;
    pending = true;
    closeMenu();
    root.setAttribute('aria-busy', 'true');
    status.textContent = kind === 'merge' ? '합치는 중…' : '원문 여는 중…';
    setTriggerValue('action', {kind, source_id, target_id, signature: data.signature, nonce: crypto.randomUUID()});
  };
  const button = (label, className, action) => {
    const result = make('button', className);
    result.type = 'button'; result.setAttribute('aria-label', label); result.onclick = action;
    return result;
  };
  const icon = (path) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const line = document.createElementNS(svg.namespaceURI, 'path');
    line.setAttribute('d', path); line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'currentColor');
    line.setAttribute('stroke-width', '2'); line.setAttribute('stroke-linecap', 'round');
    svg.append(line); return svg;
  };
  const openMenu = (row, owner, host) => {
    closeMenu(); menuOwner = owner; owner.setAttribute('aria-expanded', 'true');
    menu = make('div', 'result-bars-menu'); menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '합칠 분류 선택');
    for (const target of data.rows.filter(item => item.id !== row.id)) {
      const item = button(`${target.label}에 합치기`, '', () => send('merge', row.id, target.id));
      item.setAttribute('role', 'menuitem'); item.textContent = `${target.label}에 합치기`;
      menu.append(item);
    }
    host.append(menu);
    const bounds = menu.getBoundingClientRect();
    if (bounds.bottom > window.innerHeight - 8) { menu.style.top = 'auto'; menu.style.bottom = '100%'; }
    menu.querySelector('button')?.focus();
  };
  const list = make('div', 'result-bars-list');
  list.setAttribute('role', 'list'); list.setAttribute('aria-label', `${data.title} 분포`);
  for (const row of data.rows) {
    const item = make('div', 'result-bars-row');
    item.setAttribute('role', 'listitem'); item.dataset.rowId = row.id;
    const grip = button(`분류 끌기: ${row.label}`, 'result-bars-grip', () => {});
    grip.title = '다른 분류에 끌어 놓아 합치기';
    grip.append(icon('M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01'));
    grip.disabled = data.rows.length < 2;
    grip.onpointerdown = event => {
      if (event.button !== 0 || pending || grip.disabled) return;
      closeMenu();
      drag = {id: row.id, x: event.clientX, y: event.clientY, active: false, target: null};
      grip.setPointerCapture(event.pointerId);
    };
    grip.onpointermove = event => {
      if (!drag || pending) return;
      if (!drag.active && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 8) return;
      drag.active = true; item.classList.add('result-bars-dragging');
      root.querySelectorAll('.result-bars-drop').forEach(element => element.classList.remove('result-bars-drop'));
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('.result-bars-row');
      drag.target = target && root.contains(target) && target.dataset.rowId !== drag.id ? target.dataset.rowId : null;
      if (drag.target) target.classList.add('result-bars-drop');
      status.textContent = drag.target ? '놓으면 두 분류를 합쳐 봅니다.' : '합칠 분류 위에 놓으세요.';
      if (event.clientY > window.innerHeight - 40) window.scrollBy(0, 12);
      if (event.clientY < 40) window.scrollBy(0, -12);
    };
    const endDrag = cancel => {
      if (!drag) return;
      const current = drag; drag = null;
      root.querySelectorAll('.result-bars-drop, .result-bars-dragging').forEach(element => element.classList.remove('result-bars-drop', 'result-bars-dragging'));
      status.textContent = '';
      if (!cancel && current.active && current.target) send('merge', current.id, current.target);
    };
    grip.onpointerup = () => endDrag(false);
    grip.onpointercancel = () => endDrag(true);
    const control = button(`${row.label} · ${row.count}건 · ${row.percent.toFixed(1)}% 원문 보기`, 'result-bars-open', () => send('open', row.id));
    control.title = `${row.label}\n응답 ${row.count}건\n전체 응답 대비 ${row.percent.toFixed(1)}% (분모 ${data.denominator}건)`;
    const label = make('span', 'result-bars-label', row.label);
    const track = make('span', 'result-bars-track'); track.setAttribute('aria-hidden', 'true');
    const bar = make('span', 'result-bars-bar'); bar.style.width = `${data.maximum ? row.value / data.maximum * 100 : 0}%`;
    track.append(bar);
    control.append(label, track, make('span', 'result-bars-value', `${row.count}건 · ${row.percent.toFixed(1)}%`));
    const more = button(`합칠 분류 선택: ${row.label}`, 'result-bars-more', () => openMenu(row, more, item));
    more.append(icon('M12 5h.01M12 12h.01M12 19h.01'));
    more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', 'false');
    more.disabled = data.rows.length < 2;
    item.append(grip, control, more); list.append(item);
  }
  root.append(list);
  const status = make('p', 'result-bars-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  root.append(status);
  root.onkeydown = event => {
    if (event.key === 'Escape') {
      closeMenu(true); drag = null; status.textContent = '';
      root.querySelectorAll('.result-bars-drop, .result-bars-dragging').forEach(element => element.classList.remove('result-bars-drop', 'result-bars-dragging'));
    }
    if (menu && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); const items = Array.from(menu.querySelectorAll('button'));
      const current = items.indexOf(document.activeElement);
      items[(current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  root.onfocusout = event => {if (menu && event.relatedTarget && !root.contains(event.relatedTarget)) closeMenu();};
  root.onpointerdown = event => {if (menu && !menu.contains(event.target) && event.target !== menuOwner) closeMenu();};
}
