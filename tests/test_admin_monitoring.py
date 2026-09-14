from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import auth, routes


@pytest.mark.asyncio
async def test_monitoring_denies_non_admin_before_reading_database(monkeypatch):
    monkeypatch.setattr(auth, 'ADMIN_IDS', set())
    read = AsyncMock()
    monkeypatch.setattr(routes.db, 'fetch_one', read)
    with pytest.raises(HTTPException) as exc:
        await routes.admin_monitoring({'tg_id': 1, 'is_admin': 0})
    assert exc.value.status_code == 403
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitoring_counts_all_channels(store, channel, monkeypatch):
    monkeypatch.setattr(routes.gemini, 'cooldown_left', AsyncMock(return_value=0))
    for status in ['new', 'pending', 'approved', 'digest', 'publishing', 'uncertain', 'partial', 'published']:
        await store.execute(
            'INSERT INTO posts (channel_id, status, created_at) VALUES (?, ?, now())',
            (channel['id'], status),
        )
    result = await routes.admin_monitoring({'tg_id': 1, 'is_admin': 1})
    assert result['queue'] == {'new': 1, 'pending': 1, 'ready': 2, 'publishing': 1, 'attention': 2}
    assert result['capacity']['users'] == 1
    assert result['capacity']['channels'] == 1
    assert result['capacity']['active_channels'] == 1
    assert result['capacity']['database_bytes'] > 0
    assert result['system']['disk_total'] > 0
    assert result['system']['process_mb'] > 0
