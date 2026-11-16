import json, uuid
from pathlib import Path

out=Path(__file__).parent/'n8n_workflow.json'

def node(name,typ,pos,params,type_version=1,**extra):
    o={'id':str(uuid.uuid4()),'name':name,'type':typ,'typeVersion':type_version,'position':pos,'parameters':params}
    o.update(extra)
    return o

nodes=[]
nodes.append(node('Moi 5 phut - kiem tra dau vao','n8n-nodes-base.scheduleTrigger',[-900,-280],{'rule':{'interval':[{'field':'minutes','minutesInterval':5}]}},1.2))
nodes.append(node('Chay thu thu cong','n8n-nodes-base.manualTrigger',[-900,-80],{},1))
nodes.append(node('Nhap ZIP tu inbox','n8n-nodes-base.httpRequest',[-780,-220],{'method':'POST','url':'http://renderer:8000/jobs/import-zips','options':{'timeout':120000}},4.2))
nodes.append(node('Lay job moi','n8n-nodes-base.httpRequest',[-650,-220],{'url':'http://renderer:8000/jobs/next','options':{'timeout':15000}},4.2))
nodes.append(node('Bo qua neu khong co job','n8n-nodes-base.code',[-400,-220],{'mode':'runOnceForAllItems','jsCode':'const job = $input.first()?.json ?? {};\nreturn job.has_job === true ? [{json: job}] : [];'},2))
nodes.append(node('Dung video va gui duyet Telegram','n8n-nodes-base.httpRequest',[-130,-220],{
    'method':'POST','url':'http://renderer:8000/jobs/render','sendBody':True,'specifyBody':'json',
    'jsonBody':'={{ JSON.stringify({ job_id: $json.job_id }) }}',
    'options':{'timeout':600000}},4.2))
nodes.append(node('Moi 1 phut - tim video da duyet','n8n-nodes-base.scheduleTrigger',[-900,260],{'rule':{'interval':[{'field':'minutes','minutesInterval':1}]}},1.2))
nodes.append(node('Lay video da duyet','n8n-nodes-base.httpRequest',[-650,260],{'url':'http://renderer:8000/jobs/approved-next','options':{'timeout':15000}},4.2))
nodes.append(node('Chi tiep tuc khi duoc duyet','n8n-nodes-base.code',[-400,260],{'mode':'runOnceForAllItems','jsCode':'const job = $input.first()?.json ?? {};\nreturn job.has_job === true && !!job.video_file ? [{json: job}] : [];'},2))
nodes.append(node('Doc MP4 da duyet','n8n-nodes-base.readWriteFile',[-150,260],{'fileSelector':'={{ $json.video_file }}','options':{'dataPropertyName':'data'}},1.1))
nodes.append(node('Upload YouTube - PRIVATE khi thu','n8n-nodes-base.youTube',[100,260],{
    'resource':'video','operation':'upload',
    'title':"={{ $('Chi tiep tuc khi duoc duyet').first().json.title }}",
    'regionCode':'US','categoryId':'27','binaryProperty':'data',
    'options':{'description':"={{ $('Chi tiep tuc khi duoc duyet').first().json.description }}",'privacyStatus':'private'}
},1))
nodes.append(node('Danh dau da upload','n8n-nodes-base.httpRequest',[360,260],{
    'method':'POST','url':'http://renderer:8000/jobs/finalize','sendBody':True,
    'specifyBody':'json',
    'jsonBody':"={{ JSON.stringify({ job_id: $('Chi tiep tuc khi duoc duyet').first().json.job_id, video_id: ($json.uploadId ?? $json.id) }) }}",
    'options':{'timeout':15000}
},4.2))
nodes.append(node('HUONG DAN - doc truoc khi kich hoat','n8n-nodes-base.stickyNote',[-940,-640],{
    'content':'# English Shorts / ChatGPT Plus input / free local rendering\n\n**Ngôn ngữ nội dung:** ENGLISH (en-US). Kokoro `af_heart`. `manifest.json` phải có `language: en-US`.\n\n**Nhánh A**: Mỗi 5 phút tìm thư mục `data/jobs/<job_id>/` có `manifest.json`, ảnh và file `READY`; render bằng Kokoro/FFmpeg rồi gửi video kèm nút duyệt trên Telegram.\n\n**Nhánh B**: Mỗi phút tìm video mà Telegram đã xác nhận **đúng Telegram user ID**. Chỉ video đó được upload lên YouTube.\n\n**BẢO VỆ**: YouTube để `private` lúc thử. Sau khi kiểm thử, đổi `privacyStatus` thành `public` để duyệt = công khai.\n\nKết nối credential YouTube OAuth2 tại node YouTube. Bot token/chỉ định người duyệt đặt ở file `.env` của renderer. Không cần OpenAI API, ElevenLabs, Creatomate, webhook công khai hay n8n Execute Command.',
    'width':1670,'height':265,'color':4
},1))
nodes[-1]['parameters']['content'] += '\n\n**ZIP input:** Drop a complete ZIP containing manifest.json and scene images into data/inbox/. The ZIP intake node imports it automatically; no manual extraction or READY file is required. See processed/ or failed/ for results.'
connections={}
def edge(a,b):
    connections.setdefault(a,{'main':[[]]})['main'][0].append({'node':b,'type':'main','index':0})
edge('Moi 5 phut - kiem tra dau vao','Nhap ZIP tu inbox')
edge('Chay thu thu cong','Nhap ZIP tu inbox')
edge('Nhap ZIP tu inbox','Lay job moi')
edge('Lay job moi','Bo qua neu khong co job')
edge('Bo qua neu khong co job','Dung video va gui duyet Telegram')
edge('Moi 1 phut - tim video da duyet','Lay video da duyet')
edge('Lay video da duyet','Chi tiep tuc khi duoc duyet')
edge('Chi tiep tuc khi duoc duyet','Doc MP4 da duyet')
edge('Doc MP4 da duyet','Upload YouTube - PRIVATE khi thu')
edge('Upload YouTube - PRIVATE khi thu','Danh dau da upload')
workflow={
    'name':'EN-US AI Faceless Shorts - ChatGPT Plus input - FREE local Kokoro/FFmpeg - Telegram approval - YouTube',
    'nodes':nodes,'connections':connections,'settings':{'executionOrder':'v1','timezone':'Asia/Ho_Chi_Minh'},
    'active':False,'pinData':{},'meta':{'templateCredsSetupCompleted':False},'tags':[]
}
from combine_mp4_workflow import integrate
workflow = integrate(workflow)
out.write_text(json.dumps(workflow,ensure_ascii=False,indent=2),encoding='utf-8')
print('Created',out,'nodes:',len(nodes))
