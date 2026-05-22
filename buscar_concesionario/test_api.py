import requests, json

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0',
    'Accept': 'application/json, text/html, */*',
    'Referer': 'https://rpc.ift.org.mx/vrpc/',
}

session = requests.Session()
session.headers.update(HEADERS)
session.get('https://rpc.ift.org.mx/vrpc/', timeout=10)

for query in ['A', 'TEL', 'TELMEX', 'CARLOS']:
    try:
        r = session.get('https://rpc.ift.org.mx/vrpc//RpcServicesController/searchBP', params={'query': query}, timeout=10)
        print(f'Query={query!r}: status={r.status_code}')
        if r.status_code == 200:
            data = r.json()
            tipo = type(data).__name__
            largo = len(data) if isinstance(data, list) else 'dict'
            print(f'  Tipo: {tipo}, longitud: {largo}')
            if isinstance(data, list) and len(data) > 0:
                print(f'  Primer elemento: {data[0]}')
                print(f'  Ultimo elemento: {data[-1]}')
    except Exception as e:
        print(f'Query={query!r}: ERROR - {e}')
    print()
