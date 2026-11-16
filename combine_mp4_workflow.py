"""Merge MP4 preparation/review into the existing shared upload workflow."""
import copy
import json
import sys
import uuid
from pathlib import Path

MP4_INSTRUCTIONS = (
    '# Branch C: MP4 + continuous ChatGPT script -> Kokoro -> Telegram -> YouTube\n\n'
    'Give ChatGPT Plus your video and PROMPT_CHATGPT_VIDEO.txt, with YOUR chosen topic. '
    'Save its script.json containing mode=continuous and one full narration. '
    'No contact sheet, frames, metadata file or timed speaking slots are required.\n\n'
    'Put input.mp4 and script.json together at data/video_jobs/<job_id>/, '
    'then run Chay MP4 Voiceover. Kokoro reads the full narration together; tempo is fitted '
    'globally to the real video duration. Original audio is replaced and animated word-highlight '
    'captions are burned into the MP4 using the same thin-outline style as the image branch. '
    'Scene order, playback speed and display dimensions are preserved. '
    'Processing runs in the background and finished output automatically goes to Telegram review.\n\n'
    'Approve on Telegram before upload. MP4 and image jobs share the same YouTube credentials, '
    'privacy setting and upload/finalize nodes. Publish for automatic schedules; otherwise run '
    'the MP4 branch again after approval. Large files use a smaller Telegram preview.'
)


def integrate(workflow):
    template = json.loads((Path(__file__).parent / 'n8n_mp4_voiceover_workflow.json').read_text(encoding='utf-8'))
    nodes = workflow['nodes']
    by_name = {n['name']: n for n in nodes}
    upload_trigger = by_name['Moi 1 phut - tim video da duyet']
    x = upload_trigger['position'][0]
    y = max(n['position'][1] for n in nodes if n['type'] != 'n8n-nodes-base.stickyNote'
            and n['name'] not in {v['name'] for v in template['nodes']}
            and n['name'] != 'Gui MP4 sang duyet Telegram') + 420
    positions = {'Chay MP4 Voiceover': [x, y], 'Moi 1 phut - cap nhat MP4': [x, y+190],
                 'Nhan MP4 va tao giong nen': [x+260, y],
                 'Xem trang thai va duong dan ket qua': [x+830, y],
                 'HUONG DAN MP4 Voiceover': [x, y+390]}
    for candidate in template['nodes']:
        if candidate['name'] in by_name:
            if candidate['type'] == 'n8n-nodes-base.stickyNote':
                by_name[candidate['name']]['parameters']['content'] = MP4_INSTRUCTIONS
            continue
        node = copy.deepcopy(candidate)
        node['position'] = positions[node['name']]
        if node['type'] == 'n8n-nodes-base.stickyNote':
            node['parameters']['content'] = MP4_INSTRUCTIONS
            node['parameters']['height'] = 350
        nodes.append(node)
    review = 'Gui MP4 sang duyet Telegram'
    if review not in by_name:
        nodes.append({'id': str(uuid.uuid4()), 'name': review, 'type': 'n8n-nodes-base.httpRequest',
                      'typeVersion': 4.2, 'position': [x+550, y],
                      'parameters': {'method': 'POST', 'url': 'http://renderer:8000/video-jobs/queue-reviews',
                                     'options': {'timeout': 15000}}})
    connections = workflow['connections']

    def edge(a, b):
        connections[a] = {'main': [[{'node': b, 'type': 'main', 'index': 0}]]}

    edge('Chay MP4 Voiceover', 'Nhan MP4 va tao giong nen')
    edge('Moi 1 phut - cap nhat MP4', 'Nhan MP4 va tao giong nen')
    edge('Nhan MP4 va tao giong nen', review)
    edge(review, 'Xem trang thai va duong dan ket qua')
    edge('Xem trang thai va duong dan ket qua', 'Lay video da duyet')
    return workflow


if __name__ == '__main__':
    for filename in sys.argv[1:] or ['n8n_workflow.json']:
        path = Path(filename)
        value = json.loads(path.read_text(encoding='utf-8'))
        value = [integrate(w) for w in value] if isinstance(value, list) else integrate(value)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Integrated MP4 branch:', path)
