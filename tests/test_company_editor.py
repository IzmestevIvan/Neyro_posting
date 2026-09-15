import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai import gemini
from app.core import business

PROFILE = {'name':'DOBRA','services':'Мультимедиа','website':'https://example.org','facts':'Частный текст досье'}
TEXT = 'Компания занимается мультимедиа для переговорных и учебных пространств.'


@pytest.mark.asyncio
async def test_search_uses_public_identity_and_caches(store,channel,monkeypatch):
    search = AsyncMock(return_value=json.dumps({'text':'Найдено','sources':[{'uri':'https://example.org'}]}))
    monkeypatch.setattr(gemini,'generate',search)
    a = await business.research_company(channel,PROFILE)
    b = await business.research_company(channel,PROFILE)
    assert a==b and search.await_count==1
    assert 'Частный текст' not in search.call_args.args[0]
    assert search.call_args.kwargs['search'] is True


@pytest.mark.asyncio
async def test_search_failure_is_explicit_and_cached(store,channel,monkeypatch):
    search = AsyncMock(side_effect=gemini.AIError('quota'))
    monkeypatch.setattr(gemini,'generate',search)
    result = await business.research_company(channel,PROFILE)
    assert 'warning' in result and 'sources' not in result
    await business.research_company(channel,PROFILE)
    assert search.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('policy,brief,allowed', [('company_only','',False),('company_then_topic','',True),('company_then_topic','Наш новый проект',False)])
async def test_topic_requires_opt_in_and_no_explicit_company_request(store,channel,monkeypatch,policy,brief,allowed):
    profile = dict(PROFILE, website='',content_policy=policy)
    channel['business_profile']=json.dumps(profile)
    monkeypatch.setattr(business,'research_company',AsyncMock(return_value={}))
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={'text':TEXT,'scope':'topic','basis':'Тематический резерв','review':''}))
    if allowed:
        assert 'Тематический резерв' in (await business.draft_text(channel,brief))[1]
    else:
        with pytest.raises(ValueError):
            await business.draft_text(channel,brief)


@pytest.mark.asyncio
async def test_search_transport_requires_grounding_metadata():
    class Client:
        async def post(self,*args,**kwargs):
            assert kwargs['json']['tools']==[{'google_search':{}}]
            assert 'responseMimeType' not in kwargs['json']['generationConfig']
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'Ungrounded answer'}]}}]})
    with pytest.raises(gemini.AIError,match='подтверждающих'):
        await gemini._call(Client(),'model','secret','prompt',None,0.1,False,search=True)


@pytest.mark.asyncio
async def test_grounded_metadata_is_preserved():
    class Client:
        async def post(self,*args,**kwargs):
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'Company facts'}]},'groundingMetadata':{
                'groundingChunks':[{'web':{'uri':'https://example.org','title':'Company'}}],
                'groundingSupports':[{'segment':{'text':'Company facts'},'groundingChunkIndices':[0]}],
                'searchEntryPoint':{'renderedContent':'<div>Google</div>'}}}]})
    result = json.loads(await gemini._call(Client(),'model','secret','prompt',None,0.1,False,search=True))
    assert result['sources'][0]['uri']=='https://example.org' and result['search_entry']=='<div>Google</div>'
