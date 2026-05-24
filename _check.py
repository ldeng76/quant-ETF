import urllib.request
pages = ['/pages/overview','/pages/portfolio','/pages/strategy','/pages/monitor','/pages/alerts','/pages/settings']
for p in pages:
    try:
        r = urllib.request.urlopen('http://127.0.0.1:8080'+p, timeout=5)
        print(f'{p}: Status={r.status} Len={len(r.read())}')
    except Exception as e:
        print(f'{p}: ERROR {e}')
