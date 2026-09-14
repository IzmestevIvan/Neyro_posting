"""Read-only production smoke check. No Telegram publications or AI requests."""
import asyncio
import hashlib
import hmac
import json
import time
from importlib.metadata import version
from urllib.parse import urlencode

import httpx
from aiogram import Bot

from app import db
from app.config import BOT_TOKEN, PUBLIC_URL, DEV_AUTH
from app.api.auth import is_admin


def auth_header(user_id):
    fields = {'auth_date':str(int(time.time())), 'user':json.dumps({'id':user_id})}
    secret = hmac.new(b'WebAppData',BOT_TOKEN.encode(),hashlib.sha256).digest()
    payload = '\n'.join(f'{k}={fields[k]}' for k in sorted(fields))
    fields['hash'] = hmac.new(secret,payload.encode(),hashlib.sha256).hexdigest()
    return {'x-init-data':urlencode(fields)}


async def main():
    if DEV_AUTH:
        raise RuntimeError('DEV_AUTH enabled in production')
    await db.connect()
    bot = Bot(BOT_TOKEN)
    try:
        me = await bot.get_me()
        print(f'BOT: @{me.username}, OK')
        channels = await db.fetch_all('SELECT * FROM channels ORDER BY id')
        async with httpx.AsyncClient(timeout=30) as client:
            for base in ('http://127.0.0.1:8080', PUBLIC_URL):
                response = await client.get(base+'/healthz')
                response.raise_for_status()
                assert response.json()=={'ok':True}
                page = await client.get(base+'/')
                page.raise_for_status()
                assert '/static/app.js?v=' in page.text
                assert 'id="logoUpload"' in page.text and 'id="watermarkPosition"' in page.text
                denied = await client.get(base+'/api/bootstrap')
                assert denied.status_code==401
                print(f'HTTP: {base}, health/page/auth OK')
            for user in await db.fetch_all('SELECT tg_id, is_admin FROM users'):
                monitor = await client.get('http://127.0.0.1:8080/api/admin/monitoring',
                                           headers=auth_header(user['tg_id']))
                keys = await client.get('http://127.0.0.1:8080/api/admin/api-keys', headers=auth_header(user['tg_id']))
                if is_admin(user):
                    keys.raise_for_status()
                    assert all('secret' not in key for key in keys.json()['keys'])
                    monitor.raise_for_status()
                    assert monitor.json()['system']['disk_total'] > 0
                    assert monitor.json()['capacity']['database_bytes'] > 0
                    assert isinstance(monitor.json()['events'], list)
                else:
                    assert keys.status_code == 403
                    assert monitor.status_code == 403
            print('ADMIN: monitoring metrics and role isolation OK')
            if channels:
                headers = auth_header(channels[0]['owner_id'])
                boot = await client.get('http://127.0.0.1:8080/api/bootstrap',headers=headers)
                boot.raise_for_status()
                assert all(not {'gemini_key','logo_path','voice_sample'} & c.keys() for c in boot.json()['channels'])
                assert all(c['watermark_position'] in ('top-left','top-right','center','bottom-left','bottom-right') for c in boot.json()['channels'])
                started = time.monotonic()
                replies = await asyncio.gather(*(client.get(
                    f"http://127.0.0.1:8080/api/channels/{channels[0]['id']}/stats",headers=headers) for _ in range(30)))
                assert all(r.status_code==200 for r in replies), [r.status_code for r in replies]
                assert all('waiting' in r.json() and 'last_rejection' in r.json() for r in replies)
                print(f'API: 30 parallel stats requests OK in {time.monotonic()-started:.2f}s; keys hidden')
            for channel in channels:
                member = await bot.get_chat_member(channel['chat_id'],me.id)
                print(f"CHANNEL {channel['id']}: bot role={member.status}, can_post={getattr(member,'can_post_messages',None)}")
        rows=await db.fetch_all('SELECT status,count(*) AS n FROM posts GROUP BY status ORDER BY status')
        print('POSTS:',json.dumps(rows))
        print('PRUNED_ON:',await db.get_kv('pruned_on'))
        print('VERSIONS:',json.dumps({p:version(p) for p in ('aiogram','aiohttp','fastapi','starlette','Pillow','python-dotenv')}))
    finally:
        await bot.session.close()
        await db.close()


if __name__=='__main__':
    asyncio.run(main())
