"""SQL structure is application-owned; values always remain asyncpg parameters."""

# Independent of request validation: a future API field must not silently become
# an SQL identifier. Never accept a table, column or RETURNING clause from JSON.
_UPDATES = {
    'channels': frozenset({
        'business_mode', 'business_auto', 'business_profile', 'autopost', 'paused',
        'channel_voice', 'hits_only', 'media_only', 'digest_enabled', 'watermark',
        'instructions', 'stopwords', 'signature_text', 'signature_url', 'gemini_key',
        'delay_mode', 'watermark_position', 'pace', 'tz', 'lang', 'quality',
        'digest_time', 'window_start', 'window_end', 'voice_sample',
    }),
    'users': frozenset({'plan', 'daily_limit', 'max_channels', 'access_until', 'blocked'}),
}
_KEYS = {'channels': 'id', 'users': 'tg_id'}


def settings_update(table: str, fields: dict, identity: int) -> tuple[str, tuple]:
    allowed = _UPDATES.get(table)
    if not allowed or not fields or not set(fields) <= allowed:
        raise ValueError('Unsupported SQL update fields')
    assignments = ', '.join(f'"{column}"=${i}' for i, column in enumerate(fields, 1))
    sql = f'UPDATE "{table}" SET {assignments} WHERE "{_KEYS[table]}"=${len(fields)+1} RETURNING *'
    return sql, (*fields.values(), identity)
