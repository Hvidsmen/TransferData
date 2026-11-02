document.addEventListener('DOMContentLoaded', () => {
  const handler = document.getElementById('id_handler');
  if (!handler) return;
  const update = () => {
    const script = handler.value === 'script';
    document.querySelector('[data-field="script"]').hidden = !script;
    document.querySelector('[data-field="connection"]').hidden = script;
    ['steps', 'transaction_mode'].forEach(key => { const row = document.querySelector(`[data-field="${key}"]`); if (row) row.hidden = handler.value !== 'sql_pipeline'; });
    document.querySelector('[data-field="parameters"]').hidden = handler.value === 'sql_pipeline';
  };
  handler.addEventListener('change', update);
  update();
});
