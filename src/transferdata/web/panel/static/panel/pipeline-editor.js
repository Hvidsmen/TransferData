document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.pipeline-editor').forEach(editor => {
    const text = editor.querySelector('textarea'), rows = editor.querySelector('.pipeline-rows'), error = editor.querySelector('.pipeline-error');
    let steps = [];
    const save = () => { text.value = JSON.stringify(steps, null, 2); };
    function button(label, action) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'btn secondary'; b.textContent = label; b.addEventListener('click', action); return b;
    }
    function render() {
      rows.replaceChildren();
      steps.forEach((step, index) => {
        const card = document.createElement('section'); card.className = 'pipeline-card';
        const bar = document.createElement('div'); bar.className = 'pipeline-toolbar';
        const title = document.createElement('strong'); title.textContent = `Шаг ${index + 1}`; bar.append(title);
        const enabledLabel = document.createElement('label'); const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = step.enabled !== false;
        enabled.addEventListener('change', () => { step.enabled = enabled.checked; save(); }); enabledLabel.append(enabled, ' Выполнять'); bar.append(enabledLabel);
        [-1, 1].forEach(delta => { const b = button(delta < 0 ? '↑' : '↓', () => { [steps[index], steps[index + delta]] = [steps[index + delta], steps[index]]; render(); save(); }); b.disabled = index + delta < 0 || index + delta >= steps.length; b.setAttribute('aria-label', delta < 0 ? 'Переместить шаг вверх' : 'Переместить шаг вниз'); bar.append(b); });
        bar.append(button('Удалить', () => { if (!steps.some(s => (s.depends_on || []).includes(step.id)) || confirm('От этого шага зависят другие. После удаления потребуется исправить зависимости.')) { steps.splice(index, 1); render(); save(); } }));
        card.append(bar);
        const fields = document.createElement('div'); fields.className = 'pipeline-fields';
        [['name', 'Название', ''], ['database', 'База (пусто — из подключения)', ''], ['schema', 'Схема', 'dbo'], ['procedure', 'Процедура', ''], ['timeout', 'Таймаут шага, секунд', 300]].forEach(([key, label, fallback]) => {
          const l = document.createElement('label'), input = document.createElement('input'); input.value = step[key] ?? fallback; input.type = key === 'timeout' ? 'number' : 'text';
          input.setAttribute('aria-label', label); if (key === 'timeout') { input.min = 1; input.max = 86400; }
          input.addEventListener('input', () => { step[key] = key === 'timeout' ? Number(input.value) : input.value; save(); if (key === 'name') rows.querySelectorAll('option').forEach(option => { const j = steps.findIndex(s => s.id === option.value); if (j >= 0) option.textContent = `${j+1}. ${steps[j].name || steps[j].id}`; }); }); l.append(label, input); fields.append(l);
        });
        const l = document.createElement('label'); l.append('Обязательные предыдущие шаги'); const select = document.createElement('select'); select.multiple = true;
        steps.forEach((other, j) => { if (j === index) return; const o = document.createElement('option'); o.value = other.id; o.textContent = `${j+1}. ${other.name || other.id}`; o.selected = (step.depends_on || []).includes(other.id); select.append(o); });
        select.addEventListener('change', () => { step.depends_on = [...select.selectedOptions].map(o => o.value); save(); }); l.append(select); fields.append(l); card.append(fields); rows.append(card);
      });
    }
    function load() { try { const value = JSON.parse(text.value || '[]'); if (!Array.isArray(value) || value.some(s => !s || typeof s !== 'object' || Array.isArray(s))) throw Error(); steps = value; render(); error.textContent = ''; } catch { error.textContent = 'Проверьте JSON: нужен массив объектов шагов.'; } }
    editor.querySelector('.pipeline-add').addEventListener('click', () => { steps.push({id: 'step_' + (globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2)), name: '', database: '', schema: 'dbo', procedure: '', enabled: true, depends_on: [], timeout: 300, parameters: {}}); render(); save(); });
    editor.querySelector('.pipeline-load').addEventListener('click', load); load();
  });
});
