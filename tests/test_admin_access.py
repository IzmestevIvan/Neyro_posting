from datetime import timedelta
import pytest
from fastapi import HTTPException
from app.core import promo
from app.api.routes import admin_update_user, create_promo

ADMIN = {'tg_id':999,'is_admin':1}

@pytest.mark.asyncio
async def test_admin_plan_limits_and_extension(store, owner):
    before=await store.fetch_one('SELECT * FROM users WHERE tg_id=?',(owner,))
    result=await admin_update_user(owner,{'plan':'pro','extend_days':10},ADMIN)
    assert result['plan']=='pro' and result['max_channels']==3
    assert result['access_until']==before['access_until']+timedelta(days=10)
    result=await admin_update_user(owner,{'max_channels':8},ADMIN)
    assert result['plan']=='custom' and result['max_channels']==8

@pytest.mark.asyncio
async def test_admin_access_validation_and_permissions(store,owner):
    for payload in ({'max_channels':0},{'extend_days':-1},{'plan':[]},{'daily_limit':True}):
        with pytest.raises(HTTPException) as e:
            await admin_update_user(owner,payload,ADMIN)
        assert e.value.status_code==422
    with pytest.raises(HTTPException) as e:
        await admin_update_user(owner,{'plan':'unlim'},{'tg_id':owner,'is_admin':0})
    assert e.value.status_code==403
    with pytest.raises(HTTPException) as e:
        await admin_update_user(888,{'plan':'pro'},ADMIN)
    assert e.value.status_code==404

@pytest.mark.asyncio
async def test_custom_code_recipient_expiration_and_replay(store,owner):
    result=await create_promo({'count':1,'plan':'custom','overrides':{'daily_limit':42,'max_channels':7,'days':9},'activation_days':2,'assigned_to':owner},ADMIN)
    code=result['codes'][0]
    await store.execute('INSERT INTO users(tg_id,created_at) VALUES(2,now())')
    with pytest.raises(promo.PromoError,match='другому'):
        await promo.redeem(code,2)
    first=await promo.redeem(code,owner)
    assert first['daily_limit']==42 and first['max_channels']==7
    await store.execute("UPDATE promo_codes SET expires_at=now()-interval '1 day' WHERE code=?",(code,))
    assert (await promo.redeem(code,owner))['already_redeemed']
    other,=await promo.create_codes(1,activation_days=1)
    await store.execute("UPDATE promo_codes SET expires_at=now()-interval '1 day' WHERE code=?",(other,))
    with pytest.raises(promo.PromoError,match='истёк'):
        await promo.redeem(other,owner)

@pytest.mark.asyncio
async def test_invalid_code_settings_create_nothing(store):
    for kwargs in ({'count':0},{'count':1,'overrides':{'days':0}},{'count':1,'assigned_to':True},{'count':1,'activation_days':0}):
        with pytest.raises(promo.PromoError):
            await promo.create_codes(**kwargs)
    assert not await store.fetch_all('SELECT * FROM promo_codes')
