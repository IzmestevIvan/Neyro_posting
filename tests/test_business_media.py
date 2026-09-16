import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from fastapi import HTTPException

from app.core import business_media, publisher, business
from app.api.routes import post_action
from app.sources import web
from tests.test_business import enable, TEXT
from tests.test_pipeline_db import make_post, FakeBot


def photo_bytes():
    out = io.BytesIO()
    Image.new('RGB', (640, 480), 'green').save(out, 'PNG')
    return out.getvalue()


def test_photo_is_bounded_jpeg():
    result = business_media.normalize(photo_bytes())
    data = base64.b64decode(result['data'])
    assert len(data) <= 256 * 1024
    assert Image.open(io.BytesIO(data)).format == 'JPEG'
    for content in (b'not an image', b'x' * (business_media.MAX_UPLOAD + 1)):
        with pytest.raises(ValueError):
            business_media.normalize(content)


@pytest.mark.parametrize('url,allowed', [('https://www.example.org/projects/1',True),
    ('https://example.org/',False), ('https://evil.org/projects/1',False),
    ('http://127.0.0.1/project',False), ('https://example.org.evil.org/project',False)])
def test_company_boundary(url, allowed):
    assert business_media.company_page(url, 'https://example.org') is allowed


@pytest.mark.asyncio
async def test_inertia_projects_excludes_partner_images(monkeypatch):
    import html
    props = {'projects':[{'title':'Our school', 'description':'Our equipment project for a school with a conference room.',
                          'images':[{'src':'/school.webp'}]}], 'partners':[{'title':'Other', 'images':[{'src':'/logo.png'}]}]}
    page = '<div data-page="' + html.escape(json.dumps({'props':props}), quote=True) + '"></div>'
    monkeypatch.setattr(web, 'fetch', AsyncMock(return_value=httpx.Response(200, text=page, request=httpx.Request('GET','https://example.org/projects'))))
    result = await web.fetch_article(None, 'https://example.org/projects')
    assert 'Our school' in result.text and 'Other' not in result.text
    assert result.media == [{'type':'photo','url':'https://example.org/school.webp','project_title':'Our school'}]


@pytest.mark.asyncio
async def test_upload_preview_revision_and_publication(store, channel):
    current = await enable(store, channel)
    post = await make_post(store, channel, status='pending', text=TEXT)
    await store.execute('UPDATE posts SET business_draft=1,is_manual=1 WHERE id=?', (post['id'],))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    bot = FakeBot()
    async def stream():
        yield photo_bytes()
    old_revision = business_media.revision(post['media'])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)),
        headers={'X-Media-Revision':old_revision}, stream=stream)
    await post_action(post['id'], 'photo', request, user)
    fresh = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
    assert json.loads(fresh['media'])[0]['data']
    with pytest.raises(HTTPException) as error:
        await post_action(post['id'], 'remove_photo', request, user)
    assert error.value.status_code == 409
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, fresh, current, approved_by_user=True, approved_text=TEXT, approved_media=old_revision)
    assert not bot.sent
    prepared = await publisher._prepare_media(json.loads(fresh['media']), current)
    assert prepared[0]['file'].data.startswith(b'\xff\xd8')
    await publisher.publish_post(bot, fresh, current, approved_by_user=True, approved_text=TEXT,
                                 approved_media=business_media.revision(fresh['media']))
    assert bot.sent
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (post['id'],)))['status'] == 'published'


@pytest.mark.asyncio
async def test_project_photo_not_first_unrelated_picture(store,channel,monkeypatch):
    current = await enable(store,channel)
    current['business_profile'] = business.clean_profile({'name':'Company','services':'Equipment','website':'https://example.org'})
    monkeypatch.setattr(business,'research_company',AsyncMock(return_value={}))
    article = SimpleNamespace(text=TEXT, media=[{'url':'https://example.org/a.jpg','project_title':'Project A'}, {'url':'https://example.org/b.jpg','project_title':'Project B'}])
    monkeypatch.setattr(web,'fetch_article',AsyncMock(return_value=article))
    monkeypatch.setattr(business.gemini,'generate_json',AsyncMock(return_value={'text':TEXT,'review':'Review','scope':'company','basis':'Project B','media_project':'Project B'}))
    download = AsyncMock(return_value=business_media.normalize(photo_bytes()))
    monkeypatch.setattr(business_media,'download',download)
    evidence = {}
    await business.draft_text(current,'Project B','https://example.org/projects',research_out=evidence)
    download.assert_awaited_once_with('https://example.org/b.jpg','https://example.org/projects')
    assert evidence['photo']['project_title'] == 'Project B'
