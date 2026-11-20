const ui={busy:false};const output=document.querySelector('#message');document.querySelector('#print').addEventListener('click',()=>window.print());document.querySelector('#execute').addEventListener('click',async()=>{if(ui.busy)return;ui.busy=true;output.textContent='核验中…';try{const dataset=document.querySelector('#sample').value;const response=await fetch(dataset===document.body.dataset.source?'/api/runs/'+document.body.dataset.runId+'/rerun':'/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset})});const result=await response.json();if(!response.ok)throw Error(result.detail);const url=new URL(location.href);url.searchParams.set('dataset',dataset);url.searchParams.set('run_id',result.manifest.run_id);location.href=url;}catch(e){output.textContent=e.message;}finally{ui.busy=false;}});document.querySelector('#query').addEventListener('input',event=>document.querySelectorAll('.data-panel tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(event.target.value.toLowerCase())));document.querySelector('#sample').value=new URL(location.href).searchParams.get('dataset')||document.querySelector('#sample').value;
const filePicker=document.querySelector('#csv-file');
if(filePicker){
 const editor=document.querySelector('#csv-text');
 filePicker.addEventListener('change',async()=>{const file=filePicker.files[0];if(!file)return;if(file.size>2000000){editor.value='';alert('文件超过2MB，请缩小后导入');return;}const bytes=await file.arrayBuffer();try{editor.value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(e){editor.value=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}});
 document.querySelector('#import-csv').addEventListener('click',async event=>{event.target.disabled=true;try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv_text:editor.value})});const data=await response.json();if(!response.ok)throw Error(data.detail);location.href=location.pathname+'?run_id='+data.manifest.run_id;}catch(e){editor.setCustomValidity(e.message);editor.reportValidity();}finally{event.target.disabled=false;}});
 editor.addEventListener('input',()=>editor.setCustomValidity(''));
}
const genBtn=document.querySelector('#gen-receipt');
if(genBtn){
 const runId=document.body.dataset.runId;
 const msg=document.querySelector('#rcp-message');
 const parseBoxes=id=>document.querySelector(id).value.split(',').map(s=>s.trim()).filter(Boolean);
 genBtn.addEventListener('click',async()=>{
  genBtn.disabled=true;msg.textContent='生成中…';msg.style.color='';
  let seals;
  try{seals=JSON.parse(document.querySelector('#term-seals').value);}
  catch(e){msg.textContent='封签 JSON 格式错误';genBtn.disabled=false;return;}
  const payload={source_containers:parseBoxes('#src-boxes'),
   terminal:{containers:parseBoxes('#term-boxes'),seals},
   receipt:{receipt_no:document.querySelector('#rcp-no').value.trim(),
    container:document.querySelector('#rcp-box').value.trim(),
    pieces:document.querySelector('#rcp-pieces').value,
    net_kg:document.querySelector('#rcp-net').value,
    gross_kg:document.querySelector('#rcp-gross').value}};
  try{
   const resp=await fetch('/api/runs/'+runId+'/receipts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
   const data=await resp.json();
   if(!resp.ok)throw Error(data.detail||('请求失败 '+resp.status));
   msg.style.color=data.block_codes&&data.block_codes.length?'var(--bad)':'var(--accent)';
   msg.textContent=(data.block_codes&&data.block_codes.length?'已写明阻断：'+data.block_codes.join(','):'回单已确认：'+data.receipt_id)+'（幂等重试不新增第二套）';
   setTimeout(()=>location.reload(),900);
  }catch(e){msg.textContent=e.message;}
  finally{genBtn.disabled=false;}
 });
 const rollbackBtn=document.querySelector('#rollback-btn');
 if(rollbackBtn){
  rollbackBtn.addEventListener('click',async()=>{
   const mergeKey=document.querySelector('.table-scroll tbody tr td:nth-child(2)');
   if(!mergeKey){rollbackBtn.disabled=true;return;}
   rollbackBtn.disabled=true;
   try{
    const resp=await fetch('/api/runs/'+runId+'/receipts/rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({merge_key:mergeKey.textContent.trim()})});
    if(!resp.ok){const d=await resp.json();throw Error(d.detail);}
    location.reload();
   }catch(e){msg.textContent=e.message;rollbackBtn.disabled=false;}
  });
 }
}
