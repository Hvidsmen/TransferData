/* Shared interactions: persisted list layout, safe copy, and progressive job setup. */
(() => {
  'use strict';
  const storage = {
    get(key) { try { return localStorage.getItem('etl-dev:' + key); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem('etl-dev:' + key, value); } catch (_) {} }
  };
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-error]');
    if (!button) return;
    const panel = button.closest('details');
    try {
      await navigator.clipboard.writeText(panel.querySelector('.error-copy-source').textContent);
      panel.querySelector('.copy-status').textContent = 'Скопировано';
    } catch (_) {
      panel.querySelector('.copy-status').textContent = 'Выделите текст ошибки и скопируйте его вручную.';
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const layout = document.querySelector('[data-job-layout]');
    if (layout) {
      const toolbar = document.querySelector('[data-layout-toolbar]');
      toolbar.hidden = false;
      const apply = choice => {
        const checked = new Set([...layout.querySelectorAll('input[name=selected]:checked:not(:disabled)')].map(box => box.value));
        layout.querySelectorAll('.jobs-cards, .jobs-table').forEach(node => {
          const visible = node.classList.contains(choice === 'table' ? 'jobs-table' : 'jobs-cards');
          node.hidden = !visible;
          node.querySelectorAll('input[name=selected]').forEach(box => { box.disabled = !visible; box.checked = checked.has(box.value); });
        });
        toolbar.querySelectorAll('[data-layout]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.layout === choice)));
        storage.set('layout:' + layout.dataset.layoutKey, choice);
        document.dispatchEvent(new Event('etl:layout'));
      };
      toolbar.addEventListener('click', event => { const button = event.target.closest('[data-layout]'); if (button) apply(button.dataset.layout); });
      apply(storage.get('layout:' + layout.dataset.layoutKey) === 'table' ? 'table' : 'cards');
    }
    const form = document.querySelector('[data-job-wizard]');
    if (!form) return;
    const rows = [...form.querySelectorAll('.editor-field')];
    const fields = [['name', 'group', 'job_type', 'handler', 'source_type', 'connection', 'source_connection'],
      ['source_schema', 'source_table', 'one_c_object', 'sql_query', 'procedure_schema', 'procedure_name', 'procedure_parameters', 'script', 'parameters', 'columns'],
      ['target_connection', 'target_schema', 'target_table'],
      ['enabled', 'cron', 'timezone', 'schedule_enabled', 'schedule_cron', 'is_active']];
    const titles = ['Источник', 'Данные', 'Назначение', 'Расписание', 'Проверка'];
    const sections = titles.map((title, index) => {
      const section = document.createElement('section'); section.className = 'wizard-step'; section.dataset.step = index;
      const heading = document.createElement('h2'); heading.textContent = title; heading.tabIndex = -1; section.append(heading);
      return section;
    });
    const advanced = document.createElement('details'); advanced.className = 'wizard-advanced';
    const summary = document.createElement('summary'); summary.textContent = 'Дополнительные параметры'; advanced.append(summary); sections[1].append(advanced);
    rows.forEach(row => { const index = fields.findIndex(names => names.includes(row.dataset.field)); (index < 0 ? advanced : sections[index]).append(row); });
    if (!sections[2].querySelector('.editor-field')) sections[2].remove();
    const used = sections.filter(section => section !== sections[2] || section.querySelector('.editor-field'));
    const footer = form.querySelector('.form-footer');
    const submit = footer.querySelector('button:not([type=button])');
    const nav = document.createElement('nav'); nav.className = 'wizard-nav'; nav.setAttribute('aria-label', 'Шаги настройки задания');
    const error = document.createElement('p'); error.className = 'wizard-error'; error.setAttribute('role', 'alert'); error.hidden = true;
    const review = document.createElement('dl'); review.className = 'wizard-review'; sections[4].append(review);
    const hint = document.createElement('p'); hint.className = 'field-help'; hint.textContent = 'Проверьте настройки перед сохранением. Доступность источника и назначения проверяется обработчиком; эти шаги не выполняют перенос данных.'; sections[4].append(hint);
    used.forEach((section, index) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = (index + 1) + '. ' + section.querySelector('h2').textContent;
      button.addEventListener('click', () => go(index)); nav.append(button); form.insertBefore(section, footer);
    });
    form.prepend(nav, error);
    const back = document.createElement('button'); back.type = 'button'; back.className = 'btn secondary'; back.textContent = 'Назад';
    const next = document.createElement('button'); next.type = 'button'; next.className = 'btn primary'; next.textContent = 'Далее';
    footer.insertBefore(back, submit); footer.insertBefore(next, submit);
    let current = 0;
    const relevant = input => !input.disabled && !input.closest('.editor-field').hidden;
    function validate(section) {
      const invalid = [...section.querySelectorAll('input,select,textarea')].find(input => relevant(input) && !input.checkValidity());
      if (!invalid) { error.hidden = true; return true; }
      const extra = invalid.closest('details'); if (extra) extra.open = true;
      error.textContent = 'Проверьте поле «' + (invalid.closest('.editor-field').querySelector('label')?.textContent || invalid.name) + '».'; error.hidden = false;
      // Smart selects hide the native select. Focus their visible control instead.
      const control = invalid.getClientRects().length ? invalid : invalid.closest('.editor-field').querySelector('button, input:not([type=hidden]), .CodeMirror');
      if (control) control.focus();
      return false;
    }
    const applicable = section => section === sections[4] || [...section.querySelectorAll('.editor-field')].some(row => !row.hidden && [...row.querySelectorAll('input,select,textarea')].some(input => !input.disabled));
    function render() {
      let number = 0;
      used.forEach((section, index) => { nav.children[index].hidden = !applicable(section); if (applicable(section)) nav.children[index].textContent = (++number) + '. ' + section.querySelector('h2').textContent; });
      used.forEach((section, index) => { section.hidden = index !== current; nav.children[index].setAttribute('aria-current', index === current ? 'step' : 'false'); });
      back.hidden = current === 0; next.hidden = current === used.length - 1; submit.hidden = current !== used.length - 1;
      if (current === used.length - 1) {
        review.replaceChildren();
        rows.filter(row => !row.hidden).forEach(row => {
          const input = row.querySelector('select,input,textarea'); if (!input || input.disabled) return;
          const pair = document.createElement('div'), key = document.createElement('dt'), value = document.createElement('dd');
          key.textContent = row.querySelector('label')?.textContent;
          const text = input.tagName === 'SELECT' ? input.selectedOptions[0]?.textContent : input.type === 'checkbox' ? (input.checked ? 'Да' : 'Нет') : input.value;
          value.textContent = (text || 'Не задано').slice(0, 300); pair.append(key, value); review.append(pair);
        });
      }
      form.querySelectorAll('.CodeMirror').forEach(node => node.CodeMirror?.refresh());
      form.dispatchEvent(new Event('etl:wizard-step', {bubbles: true}));
    }
    function go(index) {
      const direction = index > current ? 1 : -1;
      while (index >= 0 && index < used.length && !applicable(used[index])) index += direction;
      if (index < 0 || index >= used.length) return;
      if (index > current) {
        for (let i = current; i < index; i++) {
          current = i; render(); if (!validate(used[i])) return;
        }
      }
      current = index; render(); used[current].querySelector('h2').focus();
    }
    form.addEventListener('change', render);
    back.addEventListener('click', () => go(current - 1)); next.addEventListener('click', () => go(current + 1));
    // Native validation cannot focus fields on other steps. Route to their step first.
    form.noValidate = true;
    form.addEventListener('submit', event => {
      if (current !== used.length - 1 && event.submitter?.name !== 'inspect_source') { event.preventDefault(); go(current + 1); return; }
      for (let index = 0; index < used.length - 1; index++) {
        current = index; render(); if (!validate(used[index])) { event.preventDefault(); return; }
      }
      current = used.length - 1; render();
    });
    // Server validation errors open their step, including advanced settings.
    const errored = used.findIndex(section => section.querySelector('.errorlist'));
    current = errored < 0 ? 0 : errored;
    if (errored >= 0) { const extra = used[errored].querySelector('.wizard-advanced'); if (extra?.querySelector('.errorlist')) extra.open = true; }
    render();
  });
})();
