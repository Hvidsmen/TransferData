document.addEventListener('DOMContentLoaded', () => {
  const handler = document.getElementById('id_handler');
  if (!handler) return;
  const update = () => {
    const script = handler.value === 'script';
    document.querySelector('[data-field="script"]').hidden = !script;
    document.querySelector('[data-field="connection"]').hidden = script;
  };
  handler.addEventListener('change', update);
  update();
});
