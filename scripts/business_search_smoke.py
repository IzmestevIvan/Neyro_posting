"""One public-company search; no user dossier, draft insert or Telegram send."""
import asyncio
import json
from app import db
from app.ai import gemini


async def main():
    try:
        async with asyncio.timeout(65):
            raw = await gemini.generate('Найди официальный сайт DOBRA Group https://www.dobragroup.ru/ и кратко перечисли направления деятельности. Не смешивай с одноимёнными компаниями.', search=True, temperature=0.1)
        result = json.loads(raw)
        assert result['sources'] and result['text']
        print('SEARCH OK; grounded sources:',len(result['sources']))
        print('PUBLIC SUMMARY:',result['text'][:1200])
    finally:
        await db.close()


asyncio.run(main())
