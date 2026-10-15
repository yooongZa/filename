'use strict';
const $=id=>document.getElementById(id);
const token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('filename-token');
if(token)sessionStorage.setItem('filename-token',token);
history.replaceState(null,'',location.pathname);
let lastRows='',state={},folderLocation='',initialized=false,quitting=false,analysisId='';
const edits=new Map(), selected=new Set(), previews=new Map();
async function api(path,body){
 const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-Filename-Token':token||'','Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'요청 실패');return result;
}
function showError(error){$('error').hidden=!error;$('error').textContent=error||'';}
function modeHint(){
 $('modeHint').textContent=$('review').checked?'검토 후 선택한 파일만 변경합니다.':$('automatic').checked?'분석에 성공한 파일을 바로 변경합니다.':'분석 결과를 저장하고 수동 변경을 기다립니다.';
}
function newName(row,tags){
 const name=row.name,index=name.lastIndexOf('.'),stem=index>0?name.slice(0,index):name,extension=index>0?name.slice(index):'';
 return stem+'__'+tags.map(t=>t.trim()).join('_')+extension;
}
function refreshButton(){
 const eligible=(state.items||[]).filter(r=>['ready','review'].includes(r.status));
 $('selectAll').checked=eligible.length>0&&eligible.every(r=>selected.has(r.id));
 $('selectAll').indeterminate=selected.size>0&&!$('selectAll').checked;
 $('rename').disabled=state.busy||state.mock||selected.size===0;
 $('rename').firstChild.textContent=$('review').checked?'검토 완료 · '+selected.size+'개 변경 ':'선택한 '+selected.size+'개 변경 ';
}
function renderRows(rows){
 const signature=JSON.stringify([rows,state.busy]);if(signature===lastRows)return;lastRows=signature;
 $('rows').replaceChildren();$('empty').hidden=rows.length>0;$('count').textContent=rows.length;
 rows.forEach(row=>{
  const wrap=document.createElement('article');wrap.className='row';
  const check=document.createElement('input');check.type='checkbox';check.setAttribute('aria-label',row.name+' 선택');
  const selectable=['ready','review'].includes(row.status);check.disabled=!selectable||state.busy;
  if(row.status==='ready'&&!edits.has(row.id))selected.add(row.id);
  if(!selectable)selected.delete(row.id);
  check.checked=selected.has(row.id);check.onchange=()=>{check.checked?selected.add(row.id):selected.delete(row.id);refreshButton();};
  const img=document.createElement('img');img.className='thumb';img.alt='';
  if(previews.has(row.id))img.src=previews.get(row.id);
  else fetch('/api/preview?id='+encodeURIComponent(row.id),{headers:{'X-Filename-Token':token}}).then(r=>r.ok?r.blob():null).then(b=>{if(b){let url=URL.createObjectURL(b);previews.set(row.id,url);img.src=url;}}).catch(()=>{});
  const content=document.createElement('div'),head=document.createElement('div');head.className='rowhead';
  const name=document.createElement('span');name.className='original';name.textContent=row.original;
  const badge=document.createElement('span');badge.className='badge';badge.textContent=({ready:'검토 대기',review:'수정 필요',done:'변경 완료',error:'분석 오류'})[row.status];
  head.append(name,badge);content.append(head);
  const tags=edits.get(row.id)||[...row.tags];while(tags.length<5)tags.push('');edits.set(row.id,tags.slice(0,5));
  const tagBox=document.createElement('div');tagBox.className='tag-inputs';const proposed=document.createElement('div');proposed.className='newname';
  edits.get(row.id).forEach((value,i)=>{const input=document.createElement('input');input.value=value;input.placeholder='태그 '+(i+1);input.setAttribute('aria-label',row.name+' 태그 '+(i+1));input.disabled=!selectable||state.busy;
   input.oninput=()=>{edits.get(row.id)[i]=input.value;proposed.textContent=newName(row,edits.get(row.id));};tagBox.append(input);});
  proposed.textContent=row.status==='done'?row.proposed:newName(row,edits.get(row.id));
  const reason=document.createElement('div');reason.className='reason';reason.textContent=row.reason;
  content.append(tagBox,proposed,reason);
  if(row.error){const error=document.createElement('div');error.className='rowerror';error.textContent=row.error;content.append(error);}
  wrap.append(check,img,content);$('rows').append(wrap);
 });refreshButton();
}
async function poll(){
 try{
  state=await api('/api/state');
  if(state.analysis_id&&state.analysis_id!==analysisId){
   analysisId=state.analysis_id;edits.clear();selected.clear();previews.forEach(URL.revokeObjectURL);previews.clear();lastRows='';
   $('folder').value=state.folder;showError('');
  }
  if(!initialized){$('review').checked=state.review_before_rename;$('automatic').checked=state.auto_rename;initialized=true;modeHint();}$('message').textContent=state.message;$('mock').hidden=!state.mock;
  $('counts').textContent=state.total?`${state.processed} / ${state.total}개 · 완료 파일 건너뜀 ${state.skipped}개`:'Gemma 4 12B QAT · 태그 5개';
  $('configPath').textContent='설정 · '+state.config_path;
  $('statusDot').classList.toggle('busy',state.busy);$('cancel').hidden=!state.busy;
  for(const id of ['analyze','browse','folder','review','automatic','undo'])$(id).disabled=state.busy;
  if(state.error)showError(state.error);
  renderRows(state.items);refreshButton();
  const stage=state.busy?1:state.items.some(r=>r.status==='done')?3:state.items.length?2:0;
  document.querySelectorAll('nav span').forEach((s,i)=>s.classList.toggle('active',i===stage));

 }catch(error){if(!quitting)showError(error.message);}finally{if(!quitting)setTimeout(poll,900);}
}
$('analyze').onclick=async()=>{try{
 showError('');edits.clear();selected.clear();previews.forEach(URL.revokeObjectURL);previews.clear();lastRows='';
 await api('/api/analyze',{folder:$('folder').value,options:{review_before_rename:$('review').checked,auto_rename:$('automatic').checked}});
}catch(error){showError(error.message);}};
$('rename').onclick=async()=>{try{showError('');await api('/api/rename',{items:[...selected].map(id=>({id,tags:edits.get(id)}))});}catch(error){showError(error.message);}};
$('undo').onclick=async()=>{if(!confirm('이 폴더에서 filename이 변경한 파일명을 원래 이름으로 되돌릴까요?'))return;try{showError('');await api('/api/undo',{});edits.clear();selected.clear();}catch(error){showError(error.message);}};
$('cancel').onclick=()=>api('/api/cancel',{}).catch(e=>showError(e.message));
$('quit').onclick=async()=>{if(state.busy&&!confirm('현재 작업 후 프로그램을 종료할까요?'))return;quitting=true;showError('');try{await api('/api/quit',{});$('message').textContent='프로그램을 종료합니다. 이 창을 닫아도 됩니다.';}catch(e){quitting=false;showError(e.message);poll();}};
$('settings').onclick=()=>api('/api/config',{}).catch(e=>showError(e.message));
$('selectAll').onchange=()=>{selected.clear();if($('selectAll').checked)state.items.filter(r=>['ready','review'].includes(r.status)).forEach(r=>selected.add(r.id));lastRows='';renderRows(state.items);};
$('review').onchange=()=>{modeHint();refreshButton();};$('automatic').onchange=modeHint;
async function browse(path){try{
 const result=await api('/api/folders?path='+encodeURIComponent(path||''));folderLocation=result.path;$('pickerPath').value=result.path;
 $('folderList').replaceChildren();$('drives').replaceChildren();
 result.folders.forEach(f=>{const b=document.createElement('button');b.textContent='▱  '+f.name;b.onclick=()=>browse(f.path);$('folderList').append(b);});
 result.drives.forEach(p=>{const b=document.createElement('button');b.textContent=p;b.onclick=()=>browse(p);$('drives').append(b);});
 $('upFolder').onclick=()=>browse(result.parent);
}catch(e){showError(e.message);}}
$('browse').onclick=async()=>{await browse($('folder').value||'~');$('picker').showModal();};
$('goFolder').onclick=()=>browse($('pickerPath').value);
$('chooseFolder').onclick=()=>{$('folder').value=folderLocation;$('picker').close();};
$('closePicker').onclick=()=>$('picker').close();
modeHint();poll();
