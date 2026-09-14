import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from fastapi import HTTPException

from app.core import watermark
from app.core import publisher
from app.api import routes, auth
from app.api.server import create_app


def png(size=(100, 60), color=(255, 0, 0, 128)):
    out = io.BytesIO()
    Image.new('RGBA', size, color).save(out, 'PNG')
    return out.getvalue()


@pytest.mark.parametrize('position', sorted(watermark.POSITIONS))
def test_watermark_position_and_alpha(tmp_path, position):
    path = tmp_path / 'logo.png'
    watermark.save_logo(png(), path)
    assert Image.open(path).getpixel((0, 0))[3] == 128
    result = Image.open(io.BytesIO(watermark._overlay(png((400, 300), (255,255,255,255)), path, position)))
    points = {'top-left': (30,30), 'top-right': (350,30), 'bottom-left': (30,260),
              'bottom-right': (350,260), 'center': (200,150)}
    r,g,b = result.getpixel(points[position])
    assert r > 240 and 130 < g < 160 and 130 < b < 160
    for other, point in points.items():
        if other != position:
            assert min(result.getpixel(point)) > 245


def test_invalid_logo_preserves_existing(tmp_path):
    path = tmp_path / 'logo.png'
    watermark.save_logo(png(), path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        watermark.save_logo(png(color=(0,0,0,0)), path)
    assert path.read_bytes() == before
    with pytest.raises(HTTPException):
        routes._clean_settings({'watermark_position': 'arbitrary'})


@pytest.mark.asyncio
async def test_logo_upload_and_position(store, channel, monkeypatch, tmp_path):
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=?', (channel['owner_id'],))
    app = create_app(None)
    app.dependency_overrides[auth.active_user] = lambda: user
    monkeypatch.setattr(routes, 'LOGO_DIR', tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post(f"/api/channels/{channel['id']}/logo", content=png(), headers={'Content-Type':'image/png'})
        assert result.status_code == 200, result.text
        assert result.json()['logo_configured'] and 'logo_path' not in result.json()
        for position in watermark.POSITIONS:
            fields = routes._clean_settings({'watermark_position': position})
            assert fields['watermark_position'] == position
        denied = await client.post('/api/channels/999/logo', content=png(), headers={'Content-Type':'image/png'})
        assert denied.status_code in (403,404)
        rejected = await client.post(f"/api/channels/{channel['id']}/logo", content=b'not PNG', headers={'Content-Type':'image/png'})
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_telegram_file_photo_gets_watermark(tmp_path):
    path = tmp_path / 'logo.png'
    watermark.save_logo(png(), path)
    bot = AsyncMock()
    bot.get_file.return_value = SimpleNamespace(file_size=2000, file_path='photo')
    async def download(file_path, destination):
        destination.write(png((400,300), (255,255,255,255)))
    bot.download_file.side_effect = download
    result = await publisher._prepare_media([{'type':'photo', 'file_id':'telegram-file'}],
        {'id':1, 'watermark':1, 'logo_path':str(path), 'watermark_position':'top-left'}, bot)
    assert 'file' in result[0] and 'file_id' not in result[0]
    image = Image.open(io.BytesIO(result[0]['file'].data))
    assert image.getpixel((30,30))[1] < 170
