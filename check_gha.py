import urllib.request, json
req = urllib.request.Request('https://api.github.com/repos/makeden-art/Convert-to-PDF/actions/runs?per_page=3')
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    for run in data.get('workflow_runs', []):
        print(f"{run['name']} - {run['status']} - {run['conclusion']} - {run['head_branch']} - {run['created_at']}")
