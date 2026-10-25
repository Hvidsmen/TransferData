(() => {
 'use strict';
 const panel = document.getElementById('portal-update-progress'); if (!panel) return;
 const phases = {queued:'Запрос принят', launch_failed:'Не удалось запустить задачу обновления', launch_uncertain:'Ожидаем подтверждение планировщика',
 check:'Проверка настроек и служб', fetch:'Получение версии из main', pause:'Приостановка новых операций', wait:'Ожидание завершения активных операций',
 backup:'Остановка служб и резервное копирование', install:'Обновление кода и зависимостей', migrate:'Обновление базы панели', static:'Обновление интерфейса',
 validate:'Проверка приложения', helper:'Обновление помощника 1С', restart:'Запуск служб', complete:'Обновление завершено'};
 const connection = document.getElementById('update-connection');
 function show(data) {
  document.getElementById('update-phase').textContent = phases[data.phase] || 'Системный этап';
  document.getElementById('update-progress').value = Math.max(0, Math.min(11, Number(data.step)||0));
  document.getElementById('update-step-count').textContent = `Завершено этапов: ${Number(data.step)||0} из 11. Длительность этапов может различаться.`;
  const journal=document.getElementById('update-journal'); journal.replaceChildren();
  for (const event of data.events || []) {
   const row=document.createElement('li');
   const at=new Date(event.at); row.textContent = (Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow'})+' · ') + (phases[event.phase] || 'Системный этап'); journal.append(row);
  }
  const outcome=document.getElementById('update-outcome');
  if (data.state==='success') {
   outcome.textContent='Портал обновлён. Ранее работавшие службы запущены.'; outcome.className='notice success'; const reload=document.createElement('a'); reload.className='btn secondary small'; reload.href=window.location.href; reload.textContent='Открыть обновлённую страницу'; outcome.append(document.createElement('br'),reload); return true;
  }
  if (data.state==='failed') {
   outcome.textContent=data.recovery_required ? 'Обновление прервано после изменений. Режим обслуживания сохранён; требуется восстановление на сервере. Подробный журнал и резервная копия находятся на сервере.' : 'Обновление не завершено. Проверьте этап выше и журнал задачи на сервере. Если изменения ещё не начинались, прежнее состояние служб восстанавливается скриптом.';
   outcome.className='notice error'; return true;
  }
  return false;
 }
 try { show(JSON.parse(document.getElementById('initial-update-state').textContent)); } catch (_) {}
 async function poll() {
  let finished=false;
  const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),15000);
  try {
   const response=await fetch(panel.dataset.statusUrl,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
   if(response.redirected || response.status===401 || response.status===403){connection.textContent='Сессия завершена. После запуска портала войдите снова и откройте эту страницу.';return;}
   if(!response.ok) throw Error();
   finished=show(await response.json()); connection.textContent='Состояние получено с сервера.';
  } catch (_) {
   connection.textContent='Связь с порталом временно недоступна. Обновление выполняется отдельной задачей Windows; переподключаемся. По отсутствию связи нельзя определить, завершилось ли оно.';
  }
  finally { clearTimeout(timeout); }
  if(!finished) setTimeout(poll,3000);
 }
 poll();
})();
