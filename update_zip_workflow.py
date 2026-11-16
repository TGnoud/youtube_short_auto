"""Add ZIP intake to source or exported workflows without replacing credentials."""
import json
import sys
import uuid
from pathlib import Path


def update(workflow):
    name = 'Nhap ZIP tu inbox'
    nodes = workflow['nodes']
    if not any(n['name'] == name for n in nodes):
        next_node = next(n for n in nodes if n['name'] == 'Lay job moi')
        x, y = next_node['position']
        nodes.append({
            'id': str(uuid.uuid4()), 'name': name,
            'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
            'position': [x - 210, y],
            'parameters': {'method': 'POST', 'url': 'http://renderer:8000/jobs/import-zips',
                           'options': {'timeout': 120000}},
        })
    for trigger in ('Moi 5 phut - kiem tra dau vao', 'Chay thu thu cong'):
        edges = workflow['connections'][trigger]['main'][0]
        for edge in edges:
            if edge['node'] == 'Lay job moi':
                edge['node'] = name
    workflow['connections'][name] = {'main': [[{'node': 'Lay job moi', 'type': 'main', 'index': 0}]]}
    for node in nodes:
        if node['type'] == 'n8n-nodes-base.stickyNote':
            note = '\n\n**ZIP input:** Drop the complete ZIP into `data/inbox/`, then execute. The ZIP intake node validates and imports it automatically. Check `processed/` or `failed/` for results. No manual extraction or READY file is required. Images remain static with transitions; narration uses Kokoro.'
            if '**ZIP input:**' not in node['parameters']['content']:
                node['parameters']['content'] += note
    return workflow


if __name__ == '__main__':
    for filename in sys.argv[1:] or ['n8n_workflow.json']:
        path = Path(filename)
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, list):
            value = [update(w) for w in value]
        else:
            value = update(value)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Updated:', path)
