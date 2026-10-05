import logging
from dataclasses import dataclass, field
from typing import Optional

from app.ai import gemini, prompts, safety
from app.core.filters import AD_THRESHOLD, ad_score, strip_source_artifacts

log = logging.getLogger("pipeline")


@dataclass
class Result:
    ok: bool
    text: Optional[str] = None
    reason: Optional[str] = None
    fact_check: Optional[dict] = None
    ai_requests: int = 0
    warnings: list[str] = field(default_factory=list)
    retryable: bool = False
    needs_review: bool = False
    duplicate_of: Optional[int] = None
    is_ad: bool = False
    ad_score: int = 0
    ad_reasons: list[str] = field(default_factory=list)


async def process(
    raw_text: str,
    *,
    quality: str,
    instructions: str,
    lang: str,
    api_key: Optional[str],
    voice_sample: str = "",
    recent_posts: Optional[list[dict]] = None,
) -> Result:
    if any(safety.input_risk(value) for value in (raw_text, instructions, voice_sample)):
        return Result(False, reason=safety.BLOCK_REASON)
    # Stored examples are another untrusted source. Omit poisoned history instead
    # of allowing one old entry to prevent every future legitimate publication.
    recent_posts = [p for p in (recent_posts or []) if not safety.input_risk(p.get('text_out') or '')]
    text = strip_source_artifacts(raw_text)
    if len(text) < 40:
        return Result(False, reason="слишком короткий текст")

    score, ad_reasons = ad_score(raw_text)
    calls = 0
    needs_review = False
    confirmed = None
    if score >= AD_THRESHOLD:
        confirmed = await _confirm_ad(raw_text, api_key)
        calls += 1
        if confirmed and confirmed['decision'] == 'ad':
            return Result(False, reason='реклама: ' + confirmed['reason'], is_ad=True,
                          ad_score=score, ad_reasons=[confirmed['reason'], confirmed['evidence']], ai_requests=calls)
        needs_review = confirmed is None or confirmed['decision'] == 'uncertain'

    if quality in ("balanced", "super"):
        try:
            triage = await gemini.generate_json(
                prompts.triage_prompt(text, instructions, recent_posts),
                api_key=api_key,
                system=prompts.TRIAGE_SYSTEM,
                temperature=0.2,
            )
            calls += 1
            if any(type(triage.get(key)) is not bool for key in ("is_ad", "is_offtopic", "is_newsworthy")):
                raise gemini.AIError("некорректная структура триажа")
            duplicate_id = triage.get('duplicate_of')
            if duplicate_id is not None:
                if type(duplicate_id) is not int or duplicate_id not in {p['id'] for p in (recent_posts or [])}:
                    raise gemini.AIError('некорректная ссылка на повтор новости')
                return Result(False, reason=f'повтор новости #{duplicate_id}',
                              duplicate_of=duplicate_id, ai_requests=calls)
            if triage.get("is_ad"):
                if confirmed is None:
                    confirmed = await _confirm_ad(raw_text, api_key)
                    calls += 1
                if confirmed and confirmed['decision'] == 'ad':
                    return Result(False, reason='реклама (проверено): ' + confirmed['reason'],
                                  ai_requests=calls, is_ad=True, ad_score=max(score, AD_THRESHOLD),
                                  ad_reasons=[confirmed['reason'], confirmed['evidence']])
                needs_review = confirmed is None or confirmed['decision'] == 'uncertain'
            if triage.get("is_offtopic"):
                return Result(False, reason=f"оффтоп: {triage.get('reason', '')}", ai_requests=calls)
            if triage.get("is_newsworthy") is False:
                return Result(False, reason="нет информационного повода", ai_requests=calls)
        except (gemini.NoKeyError, gemini.BusyError):
            raise
        except gemini.AIError as exc:
            return Result(False, reason="триаж недоступен — ожидает повторной проверки", retryable=True, ai_requests=calls)

    deep = quality == "super"
    rewritten = await gemini.generate(
        prompts.rewrite_prompt(
            text, instructions=instructions, lang=lang, voice_sample=voice_sample, deep=deep
        ),
        api_key=api_key,
        system=prompts.REWRITER_SYSTEM,
        temperature=0.85 if deep else 0.7,
    )
    calls += 1
    rewritten = rewritten.strip()
    if safety.output_risk(rewritten):
        return Result(False, reason=safety.BLOCK_REASON, ai_requests=calls)
    if len(rewritten) < 40:
        return Result(False, reason="рерайт пустой или не содержит полноценного поста", retryable=True, ai_requests=calls)

    if quality != "super":
        return Result(True, text=rewritten, ai_requests=calls, needs_review=needs_review)

    check = await _factcheck(text, rewritten, api_key)
    calls += 1
    if check is None:
        return Result(False, reason="фактчек недоступен — ожидает повторной проверки", retryable=True, ai_requests=calls, warnings=["фактчек недоступен"])

    if check["ok"] is not True:
        retry = await gemini.generate(
            prompts.rewrite_prompt(
                text,
                instructions=f"{instructions}\nСтрого держись оригинала, ничего не додумывай.",
                lang=lang,
                voice_sample=voice_sample,
                deep=True,
            ),
            api_key=api_key,
            system=prompts.REWRITER_SYSTEM,
            temperature=0.3,
        )
        calls += 1
        if safety.output_risk(retry):
            return Result(False, reason=safety.BLOCK_REASON, ai_requests=calls)
        if len(retry.strip()) < 40:
            return Result(False, reason="повторный рерайт пустой или неполный", retryable=True, ai_requests=calls)
        recheck = await _factcheck(text, retry.strip(), api_key)
        calls += 1
        if recheck is not None and recheck.get("ok"):
            return Result(True, text=retry.strip(), fact_check=recheck, ai_requests=calls, needs_review=needs_review)
        if recheck is None:
            return Result(False, reason="повторный фактчек недоступен — ожидает повторной проверки", retryable=True, ai_requests=calls)
        check = recheck
        issues = (check.get("hallucinations") or []) + (check.get("distortions") or [])
        return Result(
            False,
            reason=f"фактчек не пройден: {'; '.join(issues[:2]) or check.get('verdict', '')}",
            fact_check=check,
            ai_requests=calls,
        )

    return Result(True, text=rewritten, fact_check=check, ai_requests=calls, needs_review=needs_review)


async def _factcheck(original: str, rewritten: str, api_key: Optional[str]) -> Optional[dict]:
    if safety.input_risk(original) or safety.output_risk(rewritten):
        return None
    try:
        check = await gemini.generate_json(
            prompts.factcheck_prompt(original, rewritten),
            api_key=api_key,
            system=prompts.FACTCHECK_SYSTEM,
            model=gemini.verify_model(),
            temperature=0.1,
            allow_fallback=True,
        )
        if (type(check.get("ok")) is not bool
                or not isinstance(check.get("hallucinations"), list)
                or not isinstance(check.get("distortions"), list)
                or not all(isinstance(x, str) for x in check["hallucinations"] + check["distortions"])
                or not isinstance(check.get("verdict"), str)):
            raise gemini.AIError("некорректная структура фактчека")
        if check["ok"] and (check["hallucinations"] or check["distortions"]):
            raise gemini.AIError("противоречивый результат фактчека")
        return check
    except gemini.BusyError:
        raise
    except gemini.AIError as exc:
        log.warning("factcheck failed: %s", exc)
        return None


async def rewrite_with_instruction(
    current: str, instruction: str, lang: str, api_key: Optional[str]
) -> str:
    if safety.input_risk(current) or safety.input_risk(instruction):
        raise gemini.AIError(safety.BLOCK_REASON)
    result = (
        await gemini.generate(
            prompts.edit_prompt(current, instruction, lang),
            api_key=api_key,
            system=prompts.REWRITER_SYSTEM,
            temperature=0.6,
        )
    ).strip()
    if safety.output_risk(result) or len(result) < 40:
        raise gemini.AIError(safety.BLOCK_REASON)
    return result


async def make_digest(
    texts: list[str], instructions: str, lang: str, api_key: Optional[str]
) -> str:
    if any(safety.input_risk(text) for text in [*texts, instructions]):
        raise gemini.AIError(safety.BLOCK_REASON)
    result = (
        await gemini.generate(
            prompts.digest_prompt(texts, instructions, lang),
            api_key=api_key,
            system=prompts.REWRITER_SYSTEM,
            temperature=0.5,
        )
    ).strip()
    if safety.output_risk(result) or len(result) < 40:
        raise gemini.AIError(safety.BLOCK_REASON)
    return result


async def _confirm_ad(raw_text: str, api_key: Optional[str]) -> Optional[dict]:
    try:
        verdict = await gemini.generate_json(prompts.ad_confirmation_prompt(raw_text), api_key=api_key,
                                             system=prompts.TRIAGE_SYSTEM, temperature=0.1)
        if (verdict.get('decision') not in ('ad', 'not_ad', 'uncertain')
                or not isinstance(verdict.get('reason'), str) or not verdict['reason'].strip()):
            return None
        if verdict['decision'] == 'ad':
            evidence = verdict.get('evidence')
            if not isinstance(evidence, str) or len(evidence.strip()) < 8 or evidence not in raw_text[:8000]:
                return None
        return verdict
    except (gemini.NoKeyError, gemini.BusyError):
        raise
    except gemini.AIError:
        return None
