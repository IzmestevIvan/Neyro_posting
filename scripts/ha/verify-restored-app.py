#!/usr/bin/env python3
"""Run inside the saved image with only the isolated restore DB reachable."""
import asyncio
import re
import asyncpg
import httpx
from app import db
from app.api.server import create_app


async def main():
    # Inject a read-only pool: do not run migrations, polling or any workers.
    db._pool = await asyncpg.create_pool('postgresql://postgres@127.0.0.1/restorecheck',
        min_size=1, max_size=2, server_settings={'default_transaction_read_only': 'on'})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(None)), base_url='http://restore.test') as client:
            response = await client.get('/healthz')
            assert response.status_code == 200 and response.json() == {'ok': True}
            page = await client.get('/')
            assert page.status_code == 200
            assets = set(re.findall(r'(?:src|href)="(/static/[^" ]+)"', page.text))
            assert assets
            for asset in assets:
                assert (await client.get(asset)).status_code == 200
            assert (await client.get('/api/bootstrap')).status_code == 401
            assert (await client.get('/api/admin/monitoring')).status_code == 401
            assert (await client.get('/api/billing/orders')).status_code == 401
            assert (await client.get('/account')).status_code == 200
        print('Restored image: database health, HTML/assets and auth boundaries verified offline')
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
