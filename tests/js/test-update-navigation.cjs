/* Execute the actual polling script against controlled HTTP responses; no browser dependencies. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/transferdata/web/panel/static/panel/live.js', 'utf8');
async function scenario({url='http://localhost/python/jobs/', redirected=false, status=200, update=null}) {
  const timers=[], destinations=[], links=[];
  const indicator={hidden:true,textContent:'',append:link=>links.push(link)};
  const current=new URL('http://localhost/python/jobs/');
  const window={location:{href:current.href,origin:current.origin,pathname:current.pathname,search:'',assign:to=>destinations.push(to)},addEventListener(){}};
  const document={hidden:false,querySelector:()=>({}),getElementById:()=>indicator,addEventListener(){},createElement:()=>({})};
  vm.runInNewContext(source,{document,window,URL,AbortController,console,
    setTimeout:cb=>{timers.push(cb);return timers.length;},clearTimeout(){},
    fetch:async()=>({url,redirected,status,ok:status>=200&&status<300,headers:{get:()=>update}})});
  await timers[0]();
  return {destinations,links,message:indicator.textContent};
}
(async()=>{
  let result=await scenario({update:'/administration/update/',status:503});
  assert.deepEqual(result.destinations,['http://localhost/administration/update/']);
  result=await scenario({url:'http://localhost/administration/update/?run=abc',redirected:true});
  assert.deepEqual(result.destinations,['http://localhost/administration/update/?run=abc']);
  result=await scenario({url:'http://localhost/python/jobs/?state=active',redirected:true});
  assert.equal(result.destinations.length,1);
  assert(!result.message.includes('Сессия завершена'));
  result=await scenario({url:'http://localhost/login/?next=/python/jobs/',redirected:true});
  assert.equal(result.destinations.length,0);
  assert.equal(result.links.length,1);
  assert(result.links[0].href.includes('/login/?next='));
  result=await scenario({status:403});
  assert(result.message.includes('Нет прав'));
  result=await scenario({status:503,update:'https://external.invalid/'});
  assert.equal(result.destinations.length,0);
  assert(result.message.includes('Нет связи'));
  console.log('Update navigation, benign redirects, login, permissions and same-origin guards passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
