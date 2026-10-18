import http from 'k6/http';
import { check, sleep } from 'k6';
export const options = {scenarios:{questions:{executor:'constant-vus',vus:Number(__ENV.USERS||5),duration:__ENV.DURATION||'5m'}},thresholds:{http_req_failed:['rate<0.01'],http_req_duration:['p(95)<60000'],checks:['rate>0.99']}};
export default function(){
 const response=http.post(`${__ENV.BASE_URL}/api/chat`,JSON.stringify({question:'Berapa plafon hotel perjalanan dinas domestik?',document_ids:[__ENV.DOCUMENT_ID],mode:'naive'}),{headers:{Authorization:`Bearer ${__ENV.EVAL_TOKEN}`,'X-Tenant-ID':__ENV.TENANT_ID,'Content-Type':'application/json'},timeout:'10m'});
 check(response,{'successful response':r=>r.status===200,'grounded response':r=>r.status===200&&!r.json('abstained')&&r.json('citations').length>0}); sleep(1);
}
