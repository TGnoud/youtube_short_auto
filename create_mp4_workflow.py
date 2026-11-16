"""Generate the inactive MP4 node template used by the combined workflow."""
import json
import uuid
from pathlib import Path
from combine_mp4_workflow import MP4_INSTRUCTIONS


def node(name, kind, position, parameters, version=1):
    return {'id': str(uuid.uuid4()), 'name': name, 'type': 'n8n-nodes-base.' + kind,
            'typeVersion': version, 'position': position, 'parameters': parameters}


nodes = [
    node('Chay MP4 Voiceover', 'manualTrigger', [-640, 0], {}),
    node('Moi 1 phut - cap nhat MP4', 'scheduleTrigger', [-640, 210],
         {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 1}]}}, 1.2),
    node('Nhan MP4 va tao giong nen', 'httpRequest', [-350, 50],
         {'method': 'POST', 'url': 'http://renderer:8000/video-jobs/scan',
          'options': {'timeout': 15000}}, 4.2),
    node('Xem trang thai va duong dan ket qua', 'httpRequest', [-60, 50],
         {'url': 'http://renderer:8000/video-jobs', 'options': {'timeout': 15000}}, 4.2),
    node('HUONG DAN MP4 Voiceover', 'stickyNote', [-710, -440], {
        'content': MP4_INSTRUCTIONS,
        'width': 1150, 'height': 400, 'color': 5,
    }),
]
connections = {
    'Chay MP4 Voiceover': {'main': [[{'node': 'Nhan MP4 va tao giong nen', 'type': 'main', 'index': 0}]]},
    'Moi 1 phut - cap nhat MP4': {'main': [[{'node': 'Nhan MP4 va tao giong nen', 'type': 'main', 'index': 0}]]},
    'Nhan MP4 va tao giong nen': {'main': [[{'node': 'Xem trang thai va duong dan ket qua', 'type': 'main', 'index': 0}]]},
}
workflow = {'id': 'b6c91a840f2d473e', 'name': 'DA GOP - MP4 Voiceover (use the main workflow)', 'nodes': nodes,
            'connections': connections, 'active': False,
            'settings': {'executionOrder': 'v1', 'timezone': 'Asia/Ho_Chi_Minh'},
            'pinData': {}, 'tags': []}
output = Path(__file__).parent / 'n8n_mp4_voiceover_workflow.json'
output.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding='utf-8')
print('Created:', output)
