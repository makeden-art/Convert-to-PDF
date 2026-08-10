import urllib.request, json
req = urllib.request.Request('https://hub.docker.com/v2/repositories/makeden/convert-to-pdf/tags/latest')
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print('Updated:', data['last_updated'])
        print('Digest:', data['digest'])
        for img in data['images']:
            print(f"OS: {img['os']} Arch: {img['architecture']} Digest: {img['digest']}")
except Exception as e:
    print('Error:', e)
