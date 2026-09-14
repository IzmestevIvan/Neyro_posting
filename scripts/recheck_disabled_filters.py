"""Repair a specified owner's stale filter decisions without changing settings or publishing."""
import argparse
import asyncio
import json

from app import db
from app.core.scheduler import reconsider_filtered


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', type=int, required=True)
    parser.add_argument('--channel', type=int, required=True)
    args = parser.parse_args()
    await db.connect()
    try:
        channel = await db.fetch_one('SELECT id FROM channels WHERE id=? AND owner_id=?', (args.channel, args.owner))
        if not channel:
            raise SystemExit('Owner/channel mismatch')
        count = await reconsider_filtered(channel['id'])
        print(json.dumps({'channel': channel['id'], 'returned_for_review': count}))
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
