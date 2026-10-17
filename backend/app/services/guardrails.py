"""Medical-AI safety guardrails (BACKEND_PLAN Phase E; spec phase-a §7 EMG-01).

Region: Canada, English first. Emergency guidance uses ``settings.emergency_number`` (911),
``settings.health_line_number`` (811 nurse advice line: Health Link / Health811 / Info-Santé, in
most provinces and territories) and the 9-8-8 Suicide Crisis Helpline (call or text). The
Japanese branch is kept for structure (users may still write in Japanese) but uses the same
Canadian numbers.

- :func:`detect_red_flags` — deterministic EN + JA keyword/regex detector for emergency
  symptoms. The chat calls it BEFORE any LLM call and short-circuits to
  :func:`emergency_response`.
- :func:`output_guard` — post-processes LLM answers: rewrites a few diagnostic /
  treatment-instruction phrasings into advocacy phrasing and appends the disclaimer.

Known limits (deliberately simple, documented for review):
- Keyword matching: misses paraphrases ("an elephant sitting on my chest"), typos and
  symptoms spread over several messages; may fire on reported speech ("my father fainted").
  False positives are preferred over false negatives for this path.
- Negation is a short-window heuristic: EN looks ≤ 3 words back for no/not/never/without/
  denies (and is cancelled by "but"); JA accepts only a negative ending attached to the match (痛くない, 失神はしていない).
  Suicidal-ideation patterns are never negation-suppressed.
- ``confusion`` and ``loss_of_balance`` (BE-FAST) fire on sudden onset only ("suddenly confused", "sudden
  dizziness", "spinning and I fell"); plain "I'm confused about the app" / "a bit dizzy" do not fire.
- ``output_guard`` rewrites only the listed sentence patterns; it cannot detect implied
  diagnoses. The system prompt is the primary control, this is a backstop.
"""

from __future__ import annotations

import re

from app.core.config import settings

__all__ = [
    "RED_FLAG_LABELS", "DISCLAIMER_JA", "DISCLAIMER_EN", "disclaimer_for", "detect_red_flags",
    "emergency_response", "output_guard",
]

#: ``shortness_of_breath`` / ``confusion`` follow app/ml/rules.py BP_SYMPTOM_LABELS; ``loss_of_balance`` is the
#: BE-FAST "Balance" sign (Balance, Eyes, Face, Arms, Speech, Time). Sudden-onset only for confusion / balance.
RED_FLAG_LABELS = (
    "chest_pain", "chest_pressure", "shortness_of_breath", "speech_difficulty", "vision_disturbance", "fainting",
    "face_droop", "arm_weakness", "loss_of_balance", "sudden_confusion", "severe_sudden_headache", "suicidal_ideation",
)

#: 9-8-8 Suicide Crisis Helpline (Canada, call or text, 24/7): ``settings.crisis_line_number``.

DISCLAIMER_EN = ("This is not a diagnosis. I'm a health advocate, not a doctor — please discuss these findings with a "
                 f"healthcare professional. For sudden or severe symptoms, call {settings.emergency_number}.")
DISCLAIMER_JA = ("※これは診断ではありません。私は医師ではなく、あなたの健康の代弁者(アドボケート)として情報を整理しています。"
                 f"気になる点は医師にご相談ください。急な・強い症状があるときは{settings.emergency_number}番へ。")


def _lang(language: str | None) -> str:
    """English unless Japanese is explicitly requested (product default: English, Canada)."""
    return "ja" if (language or "").lower().startswith("ja") else "en"


def disclaimer_for(language: str | None) -> str:
    return DISCLAIMER_JA if _lang(language) == "ja" else DISCLAIMER_EN


# ---------------------------------------------------------------- red-flag detection

_EN: dict[str, list[str]] = {
    "chest_pain": [r"chest\s+(pain|pains|hurts?|hurting|ache|aching)", r"pain\s+in\s+(my|the)\s+chest",
                   r"crushing\s+(pain|feeling)", r"heart\s+attack",
                   r"pain\s+(is\s+)?(spreading|radiating|going|moving)\s+(down\s+|up\s+)?(in)?to\s+(my\s+)?(left\s+)?(arm|jaw|shoulder)"],
    "chest_pressure": [r"chest\s+(pressure|tightness|tight|heaviness|heavy|squeez\w*|discomfort)",
                       r"(pressure|tightness|heaviness|squeezing)\s+(in|on|across)\s+(my|the)\s+chest"],
    "shortness_of_breath": [r"can'?t\s+(breathe?|catch\s+(my|a)\s+breath|get\s+(enough\s+|any\s+|my\s+)?(air|breath))",
                            r"(unable|struggling|fighting|gasping)\s+(to|for)\s+(breathe?|air|breath)",
                            r"(trouble|difficulty|difficult|hard|problems?)\s+(to\s+)?breath(e|ing)",
                            r"short(ness)?\s+of\s+breath", r"breathing\s+(is\s+)?(hard|difficult|laboured|labored)",
                            r"\bdyspn(oe|e)a\b"],
    "face_droop": [r"face\s+(is\s+)?(droop\w*|numb|uneven|lopsided)", r"droop\w*\s+(face|mouth|smile|eyelid)",
                   r"(one|left|right)\s+side\s+of\s+(my|his|her|the)\s+face"],
    "arm_weakness": [r"(arm|leg|hand)\s+(is\s+|feels\s+|went\s+|suddenly\s+)?(weak|numb|limp|heavy|dead)",
                     r"can'?t\s+(lift|raise|move)\s+(my|his|her)\s+(arm|leg|hand)",
                     r"sudden\s+(weakness|numbness)", r"(weakness|numbness)\s+(on|in)\s+one\s+side"],
    "speech_difficulty": [r"slurr(ed|ing)", r"(trouble|difficulty|problem)s?\s+(speaking|talking|getting\s+words)",
                          r"can'?t\s+(speak|talk|get\s+(the\s+)?words\s+out)", r"speech\s+(is\s+)?(slurred|garbled)",
                          r"words\s+(are\s+)?coming\s+out\s+wrong"],
    "vision_disturbance": [r"sudden(ly)?\s+(loss\s+of|lost\s+(my\s+)?|blurr?ed|double)\s*(my\s+)?(vision|sight)",
                           r"sudden(ly)?\s+(\w+\s+){0,2}can'?t\s+see\b", r"(lost|losing)\s+(my\s+)?(vision|sight)", r"can'?t\s+see\s+(out\s+of|in|with)\s+(one|my)",
                           r"blind\s+in\s+one\s+eye", r"vision\s+(suddenly\s+)?(went|is\s+going|has\s+gone)\s+(black|dark|blurry)"],
    "loss_of_balance": [r"sudden(ly)?\s+(\w+\s+){0,2}(dizz\w+|vertigo|light-?headed\w*|unsteady|off\s+balance)",
                        r"(loss\s+of|lost|losing|lose)\s+(my\s+)?balance", r"can'?t\s+(keep\s+my\s+balance|stay\s+upright|walk\s+straight|stand\s+(up\s+)?straight)",
                        r"(room|everything|world|head)\s+(is\s+|was\s+|started\s+|keeps\s+)?spinning", r"spinning\s+(\w+\s+){0,3}(fell|fall|falling)",
                        r"(dizzy|dizziness|vertigo)\s+(\w+\s+){0,4}(fell|fall|falling|collapsed)"],
    "sudden_confusion": [r"sudden(ly)?\s+(\w+\s+){0,2}(confus\w+|disorient\w+)", r"(confus\w+|disorient\w+)\s+(\w+\s+){0,3}(all\s+of\s+a\s+sudden|suddenly|out\s+of\s+nowhere)",
                  r"all\s+of\s+a\s+sudden\s+(\w+\s+){0,3}(confus\w+|disorient\w+)"],
    "fainting": [r"\bfaint(ed|ing)\b", r"passed\s+out", r"pass(ing)?\s+out", r"blacked\s+out",
                 r"lost\s+consciousness", r"\bsyncope\b", r"\bcollapsed\b", r"\bkeeled\s+over\b",
                 r"(went|was|became)\s+unconscious"],
    "severe_sudden_headache": [r"(worst|severe|sudden|thunderclap|explosive|excruciating)\s+(\w+\s+){0,3}headache",
                               r"headache\s+(\w+\s+){0,4}(worst\s+of\s+my\s+life|came\s+on\s+suddenly)"],
    "suicidal_ideation": [r"suicid\w*", r"kill(ing)?\s+myself", r"end(ing|ed)?\s+(my\s+own|my|it\s+all)\s+life", r"end(ing)?\s+it\s+all",
                          r"want\s+to\s+die", r"don'?t\s+want\s+to\s+(live|be\s+alive|be\s+here|wake\s+up)",
                          r"(wish|rather)\s+(i\s+)?(was|were|be)\s+dead", r"better\s+off\s+dead", r"tak(e|ing)\s+my\s+(own\s+)?life",
                          r"(hurt|harm)(ing)?\s+myself", r"no\s+(reason|point)\s+(in\s+)?(to\s+)?(living|live|going\s+on|go\s+on)",
                          r"\boverdos\w*", r"\bod'?(ed|ing)?\s+on\b",
                          r"(took|taken|taking|take|swallowed|swallow)\s+(too\s+many|a\s+(bunch|handful|bottle|lot)\s+of|all\s+(of\s+)?(my|the|her|his))\s+(\w+\s+){0,2}(pills?|tablets?|meds|medications?|medicines?)"],
}

_JA: dict[str, list[str]] = {
    "chest_pain": [r"胸(が|の|に|は|も)?(すごく|とても|急に|強く)?(痛|いた)", r"胸痛", r"心臓(が|の)?(痛|いた)", r"胸[^。！？\n]{1,12}?(痛|いた)"],
    "chest_pressure": [r"胸(が|の|に|は|も)?(圧迫|締め付け|しめつけ|締めつけ|苦し|くるし|重苦し|押さえつけ)", r"胸部(の)?圧迫"],
    "shortness_of_breath": [r"息(が|も)?(できな|出来な|苦し|くるし|詰ま|つま|吸えな)", r"呼吸(が)?(できな|出来な|苦し|くるし|困難|しづら|しにく)",
                            r"呼吸困難", r"息ができません"],
    "face_droop": [r"顔(の)?(片側|半分|左|右)?(が|の)?(ゆが|歪|垂れ|下が|しびれ|痺れ|動かな)", r"口(角|元)?(が)?(下が|ゆが|歪)",
                   r"ろれつ.*顔|顔.*ゆがん"],
    "arm_weakness": [r"(腕|手|足|脚|片側|半身|片方)(が|に|の)?(急に|突然)?(力が入らな|ちからが入らな|力がはいらな|しびれ|痺れ|動かな|上がらな|麻痺)",
                     r"片麻痺"],
    "speech_difficulty": [r"(ろれつ|呂律)(が)?(回|まわ)らな", r"(ろれつ|呂律)が?(おかし|あやし)", r"言葉(が|を)?(出に|出な|でな|でに|うまく出)", r"(しゃべ|喋|話)(り|し)にく",
                          r"うまく(話せ|しゃべれ|喋れ)"],
    "vision_disturbance": [r"(目|視界|視野)(が|の)?(急に|突然)?(見えな|みえな|かすむ|欠け|暗く|真っ暗)", r"(物|もの)が二重",
                           r"片目(が)?見えな"],
    "loss_of_balance": [r"(急に|突然|いきなり)[^。！？\n]{0,6}(めまい|目まい|眩暈|ふらつ|ふらふら|バランス)",
                        r"(めまい|目まい|眩暈|ふらつ)[^。！？\n]{0,10}(倒れ|転ん|転倒|こけ)", r"バランス(が|を)?(崩|くず|とれな|取れな|保てな)",
                        r"まっすぐ(歩|立)けな", r"(目|視界|部屋|天井)が(ぐるぐる|グルグル)(回|まわ)"],
    "sudden_confusion": [r"(急に|突然|いきなり)[^。！？\n]{0,6}(混乱|錯乱|もうろう|朦朧|様子がおかし|話がかみ合わな|話が通じな|わけがわからな)"],
    "fainting": [r"失神", r"気(を)?失", r"意識(を|が)?(失|なくな|無くな|遠のい|とんだ|飛んだ)", r"倒れ(た|ました|て)"],
    "severe_sudden_headache": [r"(突然|急に|激しい|激しく|割れるよう|今までで(一番|最悪)|人生で(一番|最悪))[^。]{0,10}頭(が)?痛",
                               r"頭(が)?(割れ|激しく痛|ガンガン)", r"(激しい|突然の|急な)頭痛"],
    "suicidal_ideation": [r"死にたい", r"自殺", r"消えたい", r"生きて(い)?たくない", r"生きている意味がな", r"命を絶", r"自分を傷つけ",
                          r"死んだ(ほう|方)がまし", r"死(のう|にたく|んでしまいたい)", r"人生を終わらせ",
                          r"薬を(飲みすぎ|飲み過ぎ|大量に飲|全部飲|まとめて飲)", r"(大量|過量)(に)?服薬", r"オーバードーズ",
                          r"(?<![A-Za-z])OD(?![A-Za-z])"],
}

_EN_RE = {k: [re.compile(p, re.I) for p in v] for k, v in _EN.items()}
_JA_RE = {k: [re.compile(p) for p in v] for k, v in _JA.items()}
_EN_NEG = re.compile(r"\b(no|not|never|without|denies|deny|nor|none)\b(\W+\w+){0,3}\W*$|n't(\W+\w+){0,3}\W*$", re.I)
_JA_NEG = re.compile(r"^(?:く|くは|くも|みは|みも|みが|は|も|では|じゃ|して|はして|はしてい|してい|してはい|って|ってい|っては|ってはい)?"
                     r"(?:ない|無い|ありません|ません|なし|無し|なかった)")
_NO_NEGATION = {"suicidal_ideation"}


def _en_negated(text: str, start: int) -> bool:
    before = text[max(0, start - 40):start]
    before = re.split(r"[.;!?]|\bbut\b", before, flags=re.I)[-1]
    return bool(_EN_NEG.search(before))


def _ja_negated(text: str, end: int) -> bool:
    return bool(_JA_NEG.match(text[end:end + 12]))


def detect_red_flags(text: str, language: str | None = None) -> list[str]:
    """Return red-flag labels (subset of :data:`RED_FLAG_LABELS`, stable order) found in ``text``.

    Both JA and EN lists are always applied (users mix languages); ``language`` is kept for
    the API and future language-specific tuning.
    """
    if not text:
        return []
    found: set[str] = set()
    for label, pats in _EN_RE.items():
        for p in pats:
            for m in p.finditer(text):
                # "don't want to live" is itself the signal → never negation-suppressed
                if label in _NO_NEGATION or not _en_negated(text, m.start()):
                    found.add(label)
                    break
            if label in found:
                break
    for label, pats in _JA_RE.items():
        if label in found:
            continue
        for p in pats:
            if any(label in _NO_NEGATION or not _ja_negated(text, m.end()) for m in p.finditer(text)):
                found.add(label)
                break
    return [lbl for lbl in RED_FLAG_LABELS if lbl in found]


_LABEL_TEXT = {
    "ja": {"chest_pain": "胸の痛み", "chest_pressure": "胸の圧迫感", "shortness_of_breath": "息ができない・息苦しい",
           "speech_difficulty": "言葉が出にくい・ろれつが回らない",
           "vision_disturbance": "急な見え方の異常", "fainting": "失神・意識を失った", "face_droop": "顔のゆがみ",
           "arm_weakness": "腕や脚の力が入らない・しびれ", "loss_of_balance": "急なめまい・バランスが取れない",
           "sudden_confusion": "急な混乱", "severe_sudden_headache": "突然の激しい頭痛",
           "suicidal_ideation": "死にたい気持ち"},
    "en": {"chest_pain": "chest pain", "chest_pressure": "chest pressure", "shortness_of_breath": "trouble breathing",
           "speech_difficulty": "difficulty speaking",
           "vision_disturbance": "sudden vision problems", "fainting": "fainting / loss of consciousness",
           "face_droop": "face drooping", "arm_weakness": "arm or leg weakness / numbness",
           "loss_of_balance": "sudden dizziness / loss of balance", "sudden_confusion": "sudden confusion",
           "severe_sudden_headache": "a sudden severe headache", "suicidal_ideation": "thoughts of ending your life"},
}
_STROKE_LABELS = ("face_droop", "arm_weakness", "speech_difficulty", "vision_disturbance", "loss_of_balance", "sudden_confusion")


def emergency_response(language: str | None, labels: list[str], number: str | None = None) -> str:
    """Emergency guidance text (spec §7), Canada: call ``number`` (default ``settings.emergency_number``,
    911); if unsure, the ``settings.health_line_number`` (811) nurse line; for suicidal ideation the
    9-8-8 Suicide Crisis Helpline (call or text). English unless ``language`` is Japanese. Never
    mentions booking an appointment for this."""
    lang = _lang(language)
    num = number or settings.emergency_number
    health = settings.health_line_number
    names = "、".join(_LABEL_TEXT["ja"][lb] for lb in labels if lb in _LABEL_TEXT["ja"]) if lang == "ja" else \
        ", ".join(_LABEL_TEXT["en"][lb] for lb in labels if lb in _LABEL_TEXT["en"])
    physical = [lb for lb in labels if lb != "suicidal_ideation"]
    stroke = any(lb in physical for lb in _STROKE_LABELS)
    if lang == "ja":
        parts = [f"【緊急】「{names}」についてのメッセージを受け取りました。"]
        if physical:
            parts.append(f"これは急いで対応が必要なサインの可能性があります。今すぐ{num}番に電話して救急車を呼んでください。"
                         "症状が始まった時刻をメモし、一人の場合は家族や近くの人に知らせてください。自分で運転しないでください。"
                         f"緊急かどうか迷う場合は{health}（看護師による電話健康相談、多くの州・準州で利用可）に相談できます。")
            if stroke:
                parts.append("急なバランスの崩れ・見え方の異常・顔のゆがみ・腕の力が入らない・言葉の異常・急な混乱は"
                             "脳卒中のサイン（BE-FAST）のことがあり、時間が重要です。")
        if "suicidal_ideation" in labels:
            parts.append(f"つらい気持ちを話してくださりありがとうございます。あなたは一人ではありません。"
                         f"差し迫った危険がある場合は{num}番へ。{settings.crisis_line_number}（自殺危機ヘルプライン、電話またはテキスト、24時間）"
                         "でも相談できます。信頼できる人にも今の気持ちを伝えてください。")
        parts.append("このアプリは診断を行わず、予約の手配もしません。まず緊急の対応を優先してください。緊急連絡先が登録されていれば通知します。")
        return "\n".join(parts)
    parts = [f"EMERGENCY: you mentioned {names}."]
    if physical:
        parts.append(f"This can be a sign that needs urgent care. Please call {num} now. Note the time the symptoms "
                     "started, and if you are alone, tell someone nearby. Do not drive yourself.")
        if stroke:
            parts.append(f"Sudden loss of balance, vision changes, face drooping, arm weakness, speech difficulty or "
                         f"sudden confusion can be stroke signs (BE-FAST: Balance, Eyes, Face, Arms, Speech, "
                         f"Time to call {num}) — time matters.")
        parts.append(f"If you are not sure whether this is an emergency, you can call {health} to speak with a "
                     "registered nurse (Health Link / Health811, available in most provinces and territories). "
                     f"If symptoms are severe or getting worse, call {num} without waiting.")
    if "suicidal_ideation" in labels:
        parts.append(f"Thank you for telling me. You are not alone. If you might act on these thoughts or are in "
                     f"danger, call {num} now. You can also call or text {settings.crisis_line_number} (Suicide Crisis Helpline) "
                     "any time, day or night, anywhere in Canada. Please reach out to someone you trust right now.")
    parts.append("This app does not diagnose and will not book an appointment for this — please get emergency help "
                 "first. Your emergency contact will be notified if you have consented.")
    return "\n".join(parts)


# ---------------------------------------------------------------- output guard

_COND = (r"(?:disease|disorder|dementia|alzheimer'?s?|parkinson'?s?|atrial\s+fibrillation|afib|a-?fib|"
         r"hypertension|high\s+blood\s+pressure|stroke|cancer|diabetes|syndrome|arrhythmia|heart\s+failure|"
         r"cognitive\s+impairment|mci|depression|infection)")
_EN_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\byou\s+(?:definitely\s+|probably\s+|likely\s+|clearly\s+|most\s+likely\s+)?(?:have|has|are\s+suffering\s+from|suffer\s+from|are\s+diagnosed\s+with)\s+(?P<cond>[^.,;!?\n]{{0,40}}?\b{_COND}\b)",
                re.I),
     r"it may be worth asking your doctor about \g<cond>"),
    (re.compile(rf"\b(?:this|these\s+results?|your\s+data|it)\s+(?:confirms?|proves?|means?)\s+(?:that\s+)?you\s+have\s+(?P<cond>[^.,;!?\n]{{0,40}}?\b{_COND}\b)",
                re.I),
     r"these results may be worth discussing with your doctor in relation to \g<cond>"),
    (re.compile(r"\byou\s+(?:should|must|need\s+to)\s+(?P<verb>start|stop|increase|decrease|double|halve|reduce)\s+(?:taking\s+)?(?P<x>[^.;!?\n]{1,60})",
                re.I),
     r"you could ask your doctor whether to \g<verb> \g<x>"),
    (re.compile(r"\b(?:take|start\s+taking)\s+(?P<dose>\d+(?:\.\d+)?\s?(?:mg|mcg|µg|g|ml|tablets?|pills?))\b", re.I),
     r"ask your doctor about the right dose (\g<dose> was mentioned)"),
]
_JA_COND = r"(?:病|症|障害|細動|高血圧|認知症|パーキンソン|脳卒中|不整脈|心不全|がん|糖尿病)"
_JA_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:あなたは)?(?P<cond>[^\s。、]{{0,15}}{_JA_COND})(?:です|でしょう|と診断されます|に間違いありません|にかかっています)"),
     r"\g<cond>の可能性について医師に相談する価値があります"),
    (re.compile(r"(?P<x>[^\s。、]{1,20})(?:を|の)(?:服用を)?(?:やめて|中止して|増やして|減らして)ください"),
     r"\g<x>の服用については医師に相談してください"),
]


def output_guard(answer: str, language: str | None = None) -> str:
    """Rewrite diagnostic / treatment-instruction phrasing (simple regex list) and append the
    disclaimer in ``language`` if no disclaimer is already present."""
    text = (answer or "").strip()
    for pat, repl in _EN_REWRITES + _JA_REWRITES:
        text = pat.sub(repl, text)
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    disc = disclaimer_for(language)
    if DISCLAIMER_EN not in text and DISCLAIMER_JA not in text:
        text = f"{text}\n\n{disc}" if text else disc
    return text
