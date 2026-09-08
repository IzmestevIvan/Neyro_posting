import logging
from dataclasses import dataclass, field
from typing import Optional

from app.ai import gemini, prompts
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
) -> Result:
    text = strip_source_artifacts(raw_text)
    if len(text) < 40:
        return Result(False, reason="слишком короткий текст")

    score, ad_reasons = ad_score(raw_text)
    if score >= AD_THRESHOLD:
        return Result(
            False,
            reason=f"реклама: {', '.join(ad_reasons[:2])}",
            is_ad=True,
            ad_score=score,
            ad_reasons=ad_reasons,
        )

    calls = 0

    if quality in ("balanced", "super"):
        try:
            triage = await gemini.generate_json(
                prompts.triage_prompt(text, instructions),
                api_key=api_key,
                system=prompts.TRIAGE_SYSTEM,
                temperature=0.2,
            )
            calls += 1
            if triage.get("is_ad"):
                return Result(
                    False,
                    reason=f"реклама (ИИ): {triage.get('reason', '')}",
                    ai_requests=calls,
                    is_ad=True,
                    ad_score=max(score, AD_THRESHOLD),
                    ad_reasons=ad_reasons + [str(triage.get("reason", "определено нейросетью"))],
                )
            if triage.get("is_offtopic"):
                return Result(False, reason=f"оффтоп: {triage.get('reason', '')}", ai_requests=calls)
            if triage.get("is_newsworthy") is False:
                return Result(False, reason="нет информационного повода", ai_requests=calls)
        except gemini.NoKeyError:
            raise
        except gemini.AIError as exc:
            log.warning("triage failed, continuing: %s", exc)

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

    if quality != "super":
        return Result(True, text=rewritten, ai_requests=calls)

    check = await _factcheck(text, rewritten, api_key)
    calls += 1
    if check is None:
        return Result(True, text=rewritten, ai_requests=calls, warnings=["фактчек недоступен"])

    if not check.get("ok", True):
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
        recheck = await _factcheck(text, retry.strip(), api_key)
        calls += 1
        if recheck is not None and recheck.get("ok"):
            return Result(True, text=retry.strip(), fact_check=recheck, ai_requests=calls)
        issues = (check.get("hallucinations") or []) + (check.get("distortions") or [])
        return Result(
            False,
            reason=f"фактчек не пройден: {'; '.join(issues[:2]) or check.get('verdict', '')}",
            fact_check=check,
            ai_requests=calls,
        )

    return Result(True, text=rewritten, fact_check=check, ai_requests=calls)


async def _factcheck(original: str, rewritten: str, api_key: Optional[str]) -> Optional[dict]:
    try:
        return await gemini.generate_json(
            prompts.factcheck_prompt(original, rewritten),
            api_key=api_key,
            system=prompts.FACTCHECK_SYSTEM,
            model=gemini.verify_model(),
            temperature=0.1,
            allow_fallback=False,
        )
    except gemini.AIError as exc:
        log.warning("factcheck failed: %s", exc)
        return None


async def rewrite_with_instruction(
    current: str, instruction: str, lang: str, api_key: Optional[str]
) -> str:
    return (
        await gemini.generate(
            prompts.edit_prompt(current, instruction, lang),
            api_key=api_key,
            system=prompts.REWRITER_SYSTEM,
            temperature=0.6,
        )
    ).strip()


async def make_digest(
    texts: list[str], instructions: str, lang: str, api_key: Optional[str]
) -> str:
    return (
        await gemini.generate(
            prompts.digest_prompt(texts, instructions, lang),
            api_key=api_key,
            system=prompts.REWRITER_SYSTEM,
            temperature=0.5,
        )
    ).strip()
