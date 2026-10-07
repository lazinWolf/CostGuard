/* The URL is the resumable workspace state; the server owns execution and evidence. */
const config = JSON.parse(document.getElementById('workbench-data').textContent);
const el = id => document.getElementById(id);
let baseline = null, candidate = null, source = null, candidatePrices = null;
let waiting = false;

async function api(path, body, method) {
  const response = await fetch('/api/v1/' + path, {method: method || (body ? 'POST' : 'GET'),
    headers: {'Content-Type':'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function notice(text) { el('message').hidden = false; el('message').textContent = text; }
async function guarded(action) {
  if (waiting) return;
  waiting = true; document.querySelectorAll('button').forEach(button => button.disabled = true);
  try { await action(); } catch (error) { notice(error.message); }
  finally { waiting = false; document.querySelectorAll('button').forEach(button => button.disabled = false); }
}
function state(values) {
  const url = new URL(location.href);
  Object.entries(values).forEach(([key,value]) => value ? url.searchParams.set(key,value) : url.searchParams.delete(key));
  history.replaceState(null, '', url);
}
function route(value) {
  const index = value.indexOf('/');
  if (index < 1 || index === value.length - 1) throw Error('Use provider/model for the gateway route');
  return {provider:value.slice(0,index), model:value.slice(index+1)};
}
function settings(prefix) {
  const model = route(el(prefix+'-model').value.trim());
  return {...model, name:el(prefix+'-name').value, system_prompt:el(prefix+'-prompt').value,
    temperature: el(prefix+'-temperature').value === '' ? null : el(prefix+'-temperature').value,
    limits: {...(source?.limits || config.example.spec.limits), max_attempts:1,
      max_output_tokens:Number(el(prefix+'-tokens').value), stop_after_usd:el(prefix+'-spend').value},
    pricing:{version:el(prefix+'-price-version').value, entries:[{...model,
      input_per_million_usd:el(prefix+'-input-price').value, output_per_million_usd:el(prefix+'-output-price').value}]}};
}
function summary() {
  try { const cases=JSON.parse(el('cases').value); el('suite-summary').textContent =
    `${cases.length} cases · ${cases.filter(c=>c.partition==='validation').length} validation · identical inputs/expectations required for comparison`; }
  catch { el('suite-summary').textContent='Case list needs valid JSON'; }
}
function enableComparison() { el('compare-form').hidden = !(baseline && candidate); }
async function chooseBaseline(id) {
  baseline = await api('artifacts/'+id);
  source = (await api('artifacts/'+id+'/source')).spec;
  candidate = null; state({baseline_id:id,candidate_id:null}); enableComparison();
  el('baseline-form').hidden = true; el('baseline-summary').hidden = false;
  el('baseline-status').textContent = `${baseline.name} · ${baseline.cases.length} executed cases · ${baseline.suite_digest ? 'manifest recorded' : 'legacy, identity unverified'}`;
  el('baseline-evidence').href = '/artifacts/'+id;
  const usable = source && baseline.suite_digest && baseline.cases.length===baseline.suite_cases.length && baseline.cases.every(c=>c.status==='ok');
  el('candidate-section').hidden = false; el('candidate-form').hidden = !usable;
  el('identity').textContent = usable ? `Case suite is locked to baseline ${baseline.suite_digest.slice(0,12)}. Only workflow/model settings change.` :
    source ? 'This baseline is incomplete or has legacy evidence. Run a new complete baseline before cloning a candidate.' :
    'Imported application runs are not executed by CostGuard. Run your application candidate separately and import it below.';
  el('variant').disabled = baseline.suite_id !== config.example.spec.suite_id;
  candidatePrices = baseline.pricing;
  if (source) {
    el('candidate-model').value = source.provider+'/'+source.model;
    el('candidate-prompt').value = source.system_prompt;
    el('candidate-tokens').value = source.limits.max_output_tokens;
    el('candidate-spend').value = source.limits.stop_after_usd || '0.10';
    el('candidate-temperature').value = source.temperature ?? '';
    const price=source.pricing.entries.find(price=>price.provider===source.provider && price.model===source.model);
    el('candidate-input-price').value=price.input_per_million_usd;
    el('candidate-output-price').value=price.output_per_million_usd;
    el('candidate-price-version').value=source.pricing.version;
  }
}
async function chooseCandidate(id) {
  candidate = await api('artifacts/'+id); candidatePrices=candidate.pricing;
  state({candidate_id:id}); el('candidate-status').textContent=`${candidate.name} · ${candidate.cases.length} executed cases`;
  enableComparison();
}
async function waitJob(id, role) {
  state({job_id:id,role}); notice('Run queued. Calls and progress are saved; you can reload this page.');
  for (;;) {
    const job = await api('jobs/'+id);
    notice(`${role}: ${job.state} · ${job.progress}/${job.total} cases. Job ${id.slice(0,8)}.`);
    if (!['queued','running'].includes(job.state)) {
      if (job.artifact_id) await (role==='baseline' ? chooseBaseline(job.artifact_id) : chooseCandidate(job.artifact_id));
      state({job_id:null,role:null});
      if (job.state!=='completed') notice(`${job.error || job.state}. Partial evidence is retained; inspect the job at /jobs/${id}.`);
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
}
el('baseline-form').addEventListener('submit', event => { event.preventDefault(); guarded(async()=>{
  const values=settings('baseline');
  if (values.provider==='costguard-mock') {
    if (!config.demoEnabled) throw Error('Mock provider requires COSTGUARD_DEMO=1 and --profile demo. Configure a real model or enable the demo.');
    await api('demo/setup',{});
  }
  const spec={...config.example.spec,...values,suite_id:el('suite-id').value,cases:JSON.parse(el('cases').value),
    mode:values.provider==='costguard-mock'?'fixture':'live'};
  await waitJob((await api('jobs',spec)).job_id,'baseline');
});});
el('candidate-form').addEventListener('submit',event=>{event.preventDefault();guarded(async()=>{
  await waitJob((await api('candidates',{baseline_id:baseline.artifact_id,...settings('candidate')})).job_id,'candidate');
});});
el('compare-form').addEventListener('submit',event=>{event.preventDefault();guarded(async()=>{
  const entries=new Map(baseline.pricing.entries.map(price=>[price.provider+'/'+price.model,price]));
  candidatePrices.entries.forEach(price=>entries.set(price.provider+'/'+price.model,price));
  const policy={max_cost_increase_percent:el('regression').value};
  [['quality','min_candidate_quality_score'],['latency','max_candidate_mean_latency_ms'],['monthly-budget','max_monthly_cost_usd']]
    .forEach(([id,key])=>{if(el(id).value!=='')policy[key]=el(id).value;});
  const saved=await api('comparisons',{baseline_id:baseline.artifact_id,candidate_id:candidate.artifact_id,
    pricing:{version:candidatePrices.version,entries:[...entries.values()]},policy,monthly_requests:Number(el('monthly').value)});
  location.href='/reports/'+saved.id;
});});
el('saved-baseline').addEventListener('change',()=>{if(el('saved-baseline').value)guarded(()=>chooseBaseline(el('saved-baseline').value));});
el('promote').addEventListener('click',()=>guarded(async()=>{
  await api('baselines/'+encodeURIComponent(baseline.suite_id),{artifact_id:baseline.artifact_id},'PUT');
  notice('Baseline designation saved.');
}));
['baseline','candidate'].forEach(role=>{
  el(role+'-file').addEventListener('change',()=>guarded(async()=>{
    const file=el(role+'-file').files[0]; if(!file)return;
    const artifact=JSON.parse(await file.text()); const saved=await api('artifacts',artifact);
    await (role==='baseline'?chooseBaseline(saved.artifact_id):chooseCandidate(saved.artifact_id));
  }));
  el(role+'-model').addEventListener('change',()=>{
    el(role+'-input-price').value=''; el(role+'-output-price').value=''; el(role+'-price-version').value='';
    notice('Supply explicit prices for the newly selected model before running.');
  });
});
el('suite-file').addEventListener('change',()=>guarded(async()=>{
  const file=el('suite-file').files[0]; if(!file)return;
  const data=JSON.parse(await file.text()); el('cases').value=JSON.stringify(Array.isArray(data)?data:data.cases,null,2);
  el('suite-id').value=Array.isArray(data)?file.name.replace(/\.json$/,''):data.suite_id;
  summary();
}));
el('cases').addEventListener('input',summary);
el('variant').addEventListener('change',()=>{if(config.example.variants[el('variant').value])el('candidate-prompt').value=config.example.variants[el('variant').value];});
summary();
guarded(async()=>{
  const params=new URLSearchParams(location.search), baselineId=params.get('baseline_id'), candidateId=params.get('candidate_id');
  const job=params.get('job_id'), role=params.get('role');
  if(baselineId)await chooseBaseline(baselineId);
  if(candidateId)await chooseCandidate(candidateId);
  if(job && ['baseline','candidate'].includes(role))await waitJob(job,role);
});
