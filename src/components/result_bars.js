export default function(component) {
  const {data, parentElement, setTriggerValue} = component;
  const root = parentElement.querySelector('.result-bars');
  if (root.dataset.signature === data.signature) return;
  root.dataset.signature = data.signature;
  root.dataset.level = data.level || data.variant || '';
  root.removeAttribute('aria-busy');
  root.classList.toggle('result-bars-sentiment', data.variant === 'sentiment');
  root.classList.toggle('result-category-context', data.variant === 'category_context');
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
    status.textContent = ['filter', 'clear'].includes(kind) ? '분류 그래프 갱신 중…' : kind === 'merge' ? '합치는 중…' : '원문 여는 중…';
    setTriggerValue('action', {kind, source_id, target_id, signature: data.signature, nonce: crypto.randomUUID()});
  };
  const button = (label, className, action) => {
    const result = make('button', className);
    result.type = 'button'; result.setAttribute('aria-label', label); result.onclick = action;
    return result;
  };
  const status = make('p', 'result-bars-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  if (data.variant === 'category_context') {
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', `${data.title} 감성 요약`);
    if (data.condition) {
      const condition = make('div', 'result-context-condition', data.condition);
      if (data.selected) {
        const clear = button('대분류 감성 필터 해제', 'result-context-clear', () => send('clear', data.selected));
        clear.textContent = '해제'; condition.append(clear);
      }
      root.append(condition, status);
      return;
    }
    const summary = make('div', 'result-context-summary');
    const count = make('span', 'result-context-count', `응답 ${data.denominator}건`);
    count.title = '선택한 대분류의 고유 응답 수'; summary.append(count);
    for (const row of data.rows) {
      const item = button(`${data.title} 내 ${row.label} ${row.count}건 · ${row.percent.toFixed(1)}%로 좁혀보기`,
        'result-context-sentiment', () => send('filter', row.id));
      item.disabled = !row.count; item.setAttribute('aria-pressed', 'false');
      item.title = `${row.label} ${row.count}건 · 해당 대분류 ${data.denominator}건의 ${row.percent.toFixed(1)}%` +
        (row.label === '기타' ? '\n무응답·중립·미검토' : '');
      const swatch = make('span', 'result-context-swatch'); swatch.style.backgroundColor = row.color;
      swatch.setAttribute('aria-hidden', 'true');
      item.append(swatch, make('span', '', `${row.label} ${row.percent.toFixed(1)}%`)); summary.append(item);
    }
    root.append(summary, status);
    return;
  }
  const metric = row => {
    const value = make('span', 'result-bars-value');
    value.append(make('span', 'result-percent', `${row.percent.toFixed(1)}%`),
      make('span', 'result-count', ` (${row.count}건)`));
    return value;
  };
  if (data.variant === 'sentiment') {
    const heading = make('div', 'result-mention-heading');
    const title = make('span', '', '긍정·부정 언급률');
    const help = button('언급률 집계 기준', 'result-mention-help', () => {});
    const basisLabel = data.basis === '유효 기준' ? '무응답 제외' : '전체';
    help.textContent = 'ⓘ'; help.title = `${basisLabel} 응답 기준입니다. 혼합 응답은 긍정과 부정에 각각 포함되므로 합계는 100%를 넘을 수 있습니다. 무응답은 중립과 구분합니다.`;
    heading.append(title, help, make('span', 'result-mention-denominator', `${basisLabel} 응답 ${data.denominator}건 기준`));
    root.append(heading);
    for (const row of data.rows) {
      const neutral = !['긍정', '부정'].includes(row.id);
      const selected = data.selected === row.id;
      const label = neutral ? row.label : `${row.label} 포함`;
      const item = button(`${label} ${row.percent.toFixed(1)}% (${row.count}건) 필터 ${selected ? '해제' : '적용'}`,
        neutral ? 'result-mention-neutral' : 'result-mention-row', () => send('filter', row.id));
      item.setAttribute('aria-pressed', String(selected)); item.disabled = !row.count;
      item.append(make('span', 'result-mention-label', label));
      if (!neutral) {
        const track = make('span', 'result-mention-track'); track.setAttribute('aria-hidden', 'true');
        const fill = make('span', 'result-mention-fill');
        fill.style.width = `${row.percent}%`; fill.style.backgroundColor = row.color;
        track.append(fill); item.append(track);
      }
      item.append(metric(row)); root.append(item);
    }
    root.append(status); return;
  }
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
    const view = button(`${row.label} 원문 보기`, '', () => send('open', row.id));
    view.setAttribute('role', 'menuitem'); view.textContent = '원문 보기'; menu.append(view);
    for (const target of data.rows.filter(item => item.id !== row.id && row.can_merge !== false && item.can_merge !== false)) {
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
    item.classList.toggle('is-selected', Boolean(row.selected));
    item.classList.toggle('is-muted', data.level === 'category' && data.filtered && !row.selected);
    const grip = button(`분류 끌기: ${row.label}`, 'result-bars-grip', () => {});
    grip.title = '다른 분류에 끌어 놓아 합치기';
    grip.append(icon('M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01'));
    grip.disabled = data.rows.length < 2 || row.can_merge === false;
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
      drag.target = target && root.contains(target) && target.dataset.rowId !== drag.id && data.rows.find(row => row.id === target.dataset.rowId)?.can_merge !== false ? target.dataset.rowId : null;
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
    const drill = data.level === 'category';
    const control = button(`${row.label} · ${row.count}건 · ${row.percent.toFixed(1)}% ${drill ? '세부분류 보기' : '원문 보기'}`, 'result-bars-open', () => send(drill ? 'filter' : 'open', row.id));
    if (drill) control.setAttribute('aria-pressed', String(Boolean(row.selected)));
    control.title = `${row.label}\n응답 ${row.count}건\n${data.scope || '전체'} 응답 대비 ${row.percent.toFixed(1)}% (분모 ${data.denominator}건)`;
    const label = make('span', 'result-bars-label', row.label);
    const track = make('span', 'result-bars-track'); track.setAttribute('aria-hidden', 'true');
    const bar = make('span', 'result-bars-bar'); bar.style.width = `${data.maximum ? row.value / data.maximum * 100 : 0}%`;
    track.append(bar);
    control.append(label, metric(row), track);
    const more = button(`합칠 분류 선택: ${row.label}`, 'result-bars-more', () => openMenu(row, more, item));
    more.append(icon('M12 5h.01M12 12h.01M12 19h.01'));
    more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', 'false');
    more.disabled = false;
    item.append(grip, control, more); list.append(item);
  }
  root.append(list);
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
