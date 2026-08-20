"""Lightweight, post-run metrics for the simulation dashboard.

Everything in this module is computed after OASIS finishes. It never changes an
agent prompt, agent memory, or the simulation decision loop.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any

from .network_service import build_interaction_graph


ACTION_TYPES = (
    "CREATE_POST",
    "CREATE_COMMENT",
    "QUOTE_POST",
    "REPOST",
    "LIKE_POST",
)
ACTION_LABELS = {
    "CREATE_POST": "发帖",
    "CREATE_COMMENT": "评论",
    "QUOTE_POST": "引用",
    "REPOST": "转发",
    "LIKE_POST": "点赞",
}
GROUP_LABELS = {
    "regulator": "监管机构",
    "futures_company": "期货机构",
    "netizen_or_account": "公众账号",
    "named_person": "具名人物",
    "named_organization": "具名机构",
}
POSITIVE_TERMS = ("支持", "积极", "利好", "期待", "认同", "赞同", "便利", "完善", "提升", "推进")
NEGATIVE_TERMS = ("反对", "质疑", "担忧", "风险", "警惕", "压力", "困难", "冲击", "不满", "谨慎")
TOPIC_STOP_FRAGMENTS = (
    "我们", "表示", "认为", "支持", "推进", "工作", "相关", "同时", "可以", "非常",
    "这一", "当前", "需要", "进行", "已经", "以及", "一个", "将会", "更加", "进一步",
)
TOPIC_SUFFIXES = (
    "政策", "试点", "风险", "市场", "企业", "期货", "汇率", "投资", "监管", "机制",
    "工具", "发展", "创新", "国际化", "准备", "设计", "交易", "融资", "利率", "定价",
    "渠道", "体系", "基础", "规则", "制度", "技术",
)
TOPIC_PREFIXES = ("夯实", "加快", "推动", "促进", "做好", "构建", "加强", "提升")
KEYWORD_TOP_N = 24
KEYWORD_STOP = {
    "我们", "你们", "他们", "大家", "自己", "这个", "那个", "这些", "那些", "一种",
    "一个", "一些", "这种", "那种", "什么", "怎么", "如何", "为何", "不是", "只是",
    "还是", "就是", "都是", "也是", "而是", "可以", "能够", "应该", "不会", "不要",
    "不能", "没有", "已经", "以及", "如果", "因为", "所以", "但是", "同时", "目前",
    "当前", "相关", "非常", "更加", "进一步", "需要", "进行", "通过", "对于", "作为",
    "表示", "认为", "支持", "推进", "工作", "关注", "看到", "希望", "持续", "方面",
    "问题", "情况", "时候", "之后", "之前", "来说", "而言", "以上", "以下", "其中",
    "包括", "还有", "还要", "另外", "此外", "因此", "于是", "然后", "接着", "开始",
    "成为", "出现", "发生", "存在", "具有", "带来", "产生", "形成", "造成", "指出",
    "提到", "强调", "建议", "提醒", "回应", "补充", "同意", "认同", "理解", "觉得",
    "知道", "告诉", "来说", "其实", "确实", "当然", "也许", "可能", "一定", "肯定",
    "比较", "较为", "十分", "很多", "不少", "大量", "部分", "整体", "整个", "所有",
    "其他", "其它", "各种", "不同", "同样", "类似", "重要", "主要", "真正", "完全",
    "直接", "间接", "明显", "充分", "有效", "合理", "积极", "理性", "审慎", "谨慎",
    "值得", "引发", "引起", "围绕", "面对", "基于", "根据", "按照", "关于", "针对",
    "随着", "处于", "位于", "来自", "回到", "进入", "参与", "看看", "说说", "想想",
    "咱们", "各位", "兄弟", "朋友", "本人", "个人", "机构", "公司", "企业", "市场",
    "投资者", "股民", "资本", "行业", "领域", "赛道", "案例", "话题", "讨论", "观点",
    "第一", "确实", "零星", "主流",
}
KEYWORD_LATIN = re.compile(r"(?:[A-Za-z]{2,12}|A股|AI|IPO|PE)")
KEYWORD_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
KEYWORD_BOUNDARIES = set("的是了么着过地得这那")
KEYWORD_FRAGMENT_STOP = {
    "资者", "业化", "业链", "形机", "器人", "人赛", "人产", "具身智", "值投", "人形",
    "树科", "树科技", "盘交", "易确", "的是", "更关", "行市", "盘交易确实",
}


def _text(action: dict[str, Any]) -> str:
    return str(action.get("content") or (action.get("action_args") or {}).get("content") or "").strip()


def _expression_score(text: str) -> int:
    positive = sum(text.count(term) for term in POSITIVE_TERMS)
    negative = sum(text.count(term) for term in NEGATIVE_TERMS)
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _round_actions(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for round_item in run.get("rounds") or []:
        round_number = int(round_item.get("round") or 0)
        for action in round_item.get("actions") or []:
            if action.get("validated", True):
                rows.append({**action, "round": round_number})
    return rows


def _topic_candidates(documents: list[tuple[int, int, str]]) -> list[str]:
    document_hits: dict[str, set[int]] = defaultdict(set)
    occurrences: Counter[str] = Counter()
    for doc_index, (_, _, text) in enumerate(documents):
        seen_here: set[str] = set()
        for segment in re.findall(r"[0-9\u4e00-\u9fff]{2,}", text):
            for length in range(2, min(10, len(segment)) + 1):
                for start in range(0, len(segment) - length + 1):
                    phrase = segment[start:start + length]
                    if any(fragment in phrase for fragment in TOPIC_STOP_FRAGMENTS):
                        continue
                    occurrences[phrase] += 1
                    seen_here.add(phrase)
        for phrase in seen_here:
            document_hits[phrase].add(doc_index)

    ranked = [
        phrase
        for phrase in occurrences
        if len(phrase) >= 4 and len(document_hits[phrase]) >= 2 and occurrences[phrase] >= 2
        and phrase.endswith(TOPIC_SUFFIXES)
    ]
    ranked.sort(
        key=lambda phrase: (
            len(document_hits[phrase]) * len(phrase) ** 1.35,
            occurrences[phrase],
            len(phrase),
        ),
        reverse=True,
    )
    selected: list[str] = []
    for phrase in ranked:
        phrase_pairs = {phrase[index:index + 2] for index in range(len(phrase) - 1)}
        overlaps_existing = False
        for existing in selected:
            existing_pairs = {existing[index:index + 2] for index in range(len(existing) - 1)}
            overlap = len(phrase_pairs & existing_pairs) / max(1, min(len(phrase_pairs), len(existing_pairs)))
            if phrase in existing or existing in phrase or overlap >= 0.5:
                overlaps_existing = True
                break
        if overlaps_existing:
            continue
        selected.append(phrase)
        if len(selected) >= 5:
            break
    return selected


def _is_boundary_char(char: str | None) -> bool:
    if not char:
        return True
    if char in KEYWORD_BOUNDARIES:
        return True
    return not bool(re.match(r"[\u4e00-\u9fffA-Za-z0-9]", char))


def _keyword_phrases(text: str) -> set[str]:
    phrases: set[str] = set()
    stop_lower = {item.lower() for item in KEYWORD_STOP}
    for match in KEYWORD_LATIN.finditer(text):
        token = match.group(0)
        normalized = "IPO" if token.upper() == "IPO" else token.upper() if token.upper() in {"AI", "PE"} else token
        if normalized.lower() in stop_lower:
            continue
        phrases.add(normalized)
    for segment in KEYWORD_CJK.findall(text):
        if 2 <= len(segment) <= 8:
            if segment not in KEYWORD_STOP and segment[0] not in KEYWORD_BOUNDARIES and segment[-1] not in KEYWORD_BOUNDARIES:
                phrases.add(segment)
        for length in range(2, min(5, len(segment)) + 1):
            for start in range(0, len(segment) - length + 1):
                phrase = segment[start:start + length]
                if phrase in KEYWORD_STOP or phrase in KEYWORD_FRAGMENT_STOP:
                    continue
                if phrase[0] in KEYWORD_BOUNDARIES or phrase[-1] in KEYWORD_BOUNDARIES:
                    continue
                if any(fragment in phrase for fragment in TOPIC_STOP_FRAGMENTS):
                    continue
                phrases.add(phrase)
    return phrases


def _count_phrase(text: str, phrase: str) -> int:
    if phrase.isascii():
        return len(re.findall(rf"\b{re.escape(phrase)}\b", text, flags=re.IGNORECASE)) or text.lower().count(phrase.lower())
    return text.count(phrase)


def _phrase_contexts(text: str, phrase: str) -> tuple[list[str], list[str]]:
    lefts: list[str] = []
    rights: list[str] = []
    start = 0
    needle = phrase.lower() if phrase.isascii() else phrase
    haystack = text.lower() if phrase.isascii() else text
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            break
        lefts.append(text[index - 1] if index > 0 else "")
        end = index + len(phrase)
        rights.append(text[end] if end < len(text) else "")
        start = index + max(1, len(phrase))
    return lefts, rights


def _select_keywords(documents: list[tuple[int, int, str]], *, top_n: int = KEYWORD_TOP_N) -> list[dict[str, Any]]:
    if not documents:
        return []
    occurrences: Counter[str] = Counter()
    agents: dict[str, set[int]] = defaultdict(set)
    docs: dict[str, set[int]] = defaultdict(set)
    left_ctx: dict[str, Counter[str]] = defaultdict(Counter)
    right_ctx: dict[str, Counter[str]] = defaultdict(Counter)
    for index, (_round_number, agent_id, text) in enumerate(documents):
        for phrase in _keyword_phrases(text):
            count = _count_phrase(text, phrase)
            if count <= 0:
                continue
            occurrences[phrase] += count
            agents[phrase].add(agent_id)
            docs[phrase].add(index)
            lefts, rights = _phrase_contexts(text, phrase)
            left_ctx[phrase].update(lefts)
            right_ctx[phrase].update(rights)

    def is_fragment(phrase: str) -> bool:
        if phrase.isascii() or len(phrase) < 3:
            return False
        total = occurrences[phrase]
        if total < 4:
            return False
        left_items = left_ctx[phrase]
        right_items = right_ctx[phrase]
        left_top, left_count = left_items.most_common(1)[0] if left_items else ("", 0)
        right_top, right_count = right_items.most_common(1)[0] if right_items else ("", 0)
        stuck_left = (not _is_boundary_char(left_top)) and left_count / total >= 0.8
        stuck_right = (not _is_boundary_char(right_top)) and right_count / total >= 0.8
        return stuck_left or stuck_right

    min_docs = 2 if len(documents) >= 4 else 1
    ranked = [
        phrase
        for phrase, count in occurrences.items()
        if count >= 2 and len(docs[phrase]) >= min_docs and not is_fragment(phrase)
    ]

    def score(phrase: str) -> float:
        length_bonus = 1 + 0.28 * max(0, min(len(phrase), 6) - 2)
        agent_bonus = 1 + 0.12 * max(0, len(agents[phrase]) - 1)
        return occurrences[phrase] * length_bonus * agent_bonus

    ranked.sort(key=lambda phrase: (score(phrase), len(phrase), occurrences[phrase]), reverse=True)
    selected: list[str] = []
    for phrase in ranked:
        overlapped = False
        for index, existing in enumerate(selected):
            if phrase not in existing and existing not in phrase:
                continue
            shorter, longer = (phrase, existing) if len(phrase) < len(existing) else (existing, phrase)
            if occurrences[longer] >= occurrences[shorter] * 0.45:
                if existing == shorter:
                    if longer in selected:
                        selected.pop(index)
                    else:
                        selected[index] = longer
                overlapped = True
                break
            overlapped = True
            break
        if overlapped:
            continue
        selected.append(phrase)
        if len(selected) >= top_n:
            break
    selected.sort(key=lambda phrase: (-occurrences[phrase], -len(phrase), phrase))
    return [
        {
            "keyword": phrase,
            "count": int(occurrences[phrase]),
            "agent_count": len(agents[phrase]),
        }
        for phrase in selected
    ]


def _keyword_hotspots(
    text_documents: list[tuple[int, int, str]],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    round_rows = []
    for round_item in rounds:
        round_number = int(round_item["round"])
        round_docs = [item for item in text_documents if item[0] == round_number]
        round_rows.append({
            "round": round_number,
            "label": round_item["label"],
            "keywords": _select_keywords(round_docs),
        })
    return {
        "default_scope": "last_round",
        "all": _select_keywords(text_documents),
        "rounds": round_rows,
        "metric_source": "日志文本统计",
    }


def build_visualization_analysis(
    run: dict[str, Any], personas: dict[str, Any], database_path
) -> dict[str, Any]:
    approved = personas.get("personas") or []
    persona_by_id = {index: item for index, item in enumerate(approved)}
    actions = _round_actions(run)
    graph = build_interaction_graph(run, personas, database_path)

    rounds = []
    for round_item in run.get("rounds") or []:
        counts = Counter(
            str(action.get("action_type") or "")
            for action in round_item.get("actions") or []
            if action.get("validated", True)
        )
        rounds.append({
            "round": int(round_item.get("round") or 0),
            "label": "初始事件" if int(round_item.get("round") or 0) == 0 else f"第 {round_item.get('round')} 轮",
            "active_agents": len(set(round_item.get("active_agent_ids") or [])),
            "total_actions": sum(counts.values()),
            "actions": {
                action_type: counts.get(action_type, 0)
                for action_type in ACTION_TYPES
            },
        })

    agent_expression: dict[int, list[int]] = defaultdict(list)
    agent_activity: Counter[int] = Counter()
    group_activity: Counter[str] = Counter()
    text_documents: list[tuple[int, int, str]] = []
    for action in actions:
        agent_id = int(action.get("agent_id", -1))
        if agent_id < 0:
            continue
        persona = persona_by_id.get(agent_id) or {}
        role = str(action.get("role_group") or persona.get("role_group") or persona.get("role_type") or "unknown")
        agent_activity[agent_id] += 1
        group_activity[role] += 1
        text = _text(action)
        if text:
            agent_expression[agent_id].append(_expression_score(text))
            text_documents.append((int(action["round"]), agent_id, text))

    group_agents: dict[str, list[float]] = defaultdict(list)
    for agent_id, persona in persona_by_id.items():
        role = str(persona.get("role_group") or persona.get("role_type") or "unknown")
        scores = agent_expression.get(agent_id) or []
        if scores:
            group_agents[role].append(sum(scores) / len(scores))
    roles = sorted(
        {str(item.get("role_group") or item.get("role_type") or "unknown") for item in approved},
        key=lambda role: (-group_activity[role], GROUP_LABELS.get(role, role)),
    )
    group_metrics = []
    for role in roles:
        members = [
            index for index, item in persona_by_id.items()
            if str(item.get("role_group") or item.get("role_type") or "unknown") == role
        ]
        values = group_agents.get(role) or []
        group_metrics.append({
            "group": role,
            "label": GROUP_LABELS.get(role, "其他角色"),
            "agent_count": len(members),
            "active_agent_count": sum(agent_activity[agent_id] > 0 for agent_id in members),
            "action_count": group_activity[role],
            "expression_mean": round(sum(values) / len(values), 3) if values else None,
            "individual_values": [round(value, 3) for value in values],
            "metric_source": "文本规则推断",
        })

    topics = []
    for topic in _topic_candidates(text_documents):
        topic_label = topic
        for prefix in TOPIC_PREFIXES:
            if topic_label.startswith(prefix) and len(topic_label) - len(prefix) >= 4:
                topic_label = topic_label[len(prefix):]
                break
        by_round = []
        total_mentions = 0
        for round_item in rounds:
            round_number = round_item["round"]
            round_docs = [(agent_id, text) for item_round, agent_id, text in text_documents if item_round == round_number]
            mentions = sum(text.count(topic) for _, text in round_docs)
            active_text_agents = {agent_id for agent_id, _ in round_docs}
            attention_agents = {agent_id for agent_id, text in round_docs if topic in text}
            total_mentions += mentions
            by_round.append({
                "round": round_number,
                "mentions": mentions,
                "attention": round(len(attention_agents) / len(active_text_agents), 3) if active_text_agents else 0,
            })
        topics.append({
            "topic": topic_label,
            "total_mentions": total_mentions,
            "rounds": by_round,
            "metric_source": "日志文本统计",
        })
    topics.sort(key=lambda item: (-item["total_mentions"], item["topic"]))
    keyword_hotspots = _keyword_hotspots(text_documents, rounds)

    action_counts = Counter(str(action.get("action_type") or "") for action in actions)
    most_common_action, most_common_count = action_counts.most_common(1)[0] if action_counts else (None, 0)
    most_active_id, most_active_count = agent_activity.most_common(1)[0] if agent_activity else (-1, 0)
    most_active_name = (
        persona_by_id.get(most_active_id, {}).get("display_name")
        or f"主体 {most_active_id}"
    )
    lead_topic = topics[0]["topic"] if topics else "暂无稳定议题"
    finding = (
        f"本次模拟共记录 {len(actions)} 个行为，其中"
        f"{ACTION_LABELS.get(most_common_action, '互动')}最多（{most_common_count} 次）。"
        f"“{lead_topic}”是文本中最集中的议题；{most_active_name}最活跃（{most_active_count} 个行为）。"
    )

    return {
        "analysis_version": "1.0",
        "case_id": run.get("case_id"),
        "simulation_id": run.get("simulation_id"),
        "origin": "simulation",
        "disclaimer": "以下指标只描述本次模拟结果，不代表现实世界情况。",
        "summary": {
            "agent_count": len(approved),
            "round_count": max(0, len(rounds) - 1),
            "action_count": len(actions),
            "interaction_count": sum(int(edge.get("count") or 0) for edge in graph.get("edges") or []),
            "topic_count": len(topics),
            "finding": finding,
        },
        "action_labels": ACTION_LABELS,
        "round_metrics": rounds,
        "group_metrics": group_metrics,
        "topic_evolution": topics,
        "keyword_hotspots": keyword_hotspots,
        "network": graph,
        "metric_sources": {
            "actions": "模拟日志直接统计",
            "network": "模拟日志直接统计",
            "topics": "日志文本统计",
            "keywords": "日志文本统计",
            "group_expression": "文本规则推断",
        },
    }
