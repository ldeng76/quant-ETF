import httpx

r = httpx.post('http://127.0.0.1:8080/api/strategy/run', json={'strategies': ['short']}, timeout=30)
print('run response:', r.status_code, r.text[:500])
