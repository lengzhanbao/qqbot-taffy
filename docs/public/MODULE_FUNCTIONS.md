# QQBot 模块函数说明

> 自动从当前代码提取，不继续拆分。

## bridge_qq.py

### FunctionDef: _safe_max_tokens

（无 docstring）

### FunctionDef: _dynamic_final_max_tokens

（无 docstring）

### FunctionDef: _provider_blocked

（无 docstring）

### FunctionDef: _mark_provider_down

（无 docstring）

### FunctionDef: _mark_provider_ok

（无 docstring）

### FunctionDef: _mark_provider_weak

（无 docstring）

### FunctionDef: _sort_models

（无 docstring）

### FunctionDef: _is_song_recommendation_query

（无 docstring）

### FunctionDef: _song_recommendation_source

（无 docstring）

### FunctionDef: _song_recommendation_fallback_reply

（无 docstring）

### FunctionDef: _book_recommendation_fallback_reply

（无 docstring）

### FunctionDef: _book_recommendation_broken

（无 docstring）

### FunctionDef: _song_recommendation_broken

（无 docstring）

### FunctionDef: _strip_history_prompt_noise

去掉旧版本误存进会话里的 system/style/tone 等提示词残留。

### FunctionDef: _sanitize_history_content

旧会话里如果存过残缺歌单，读取时替换成完整兜底，避免污染新回答。

### FunctionDef: _clean_date_concat

把来源里粘在一起的日期清理成完整日期，不删除名字/队伍信息。

### FunctionDef: _refine_polluted_history

保留人物/作品信息，只移除重复片段和粘连日期。

### FunctionDef: _recommendation_category

（无 docstring）

### FunctionDef: _generic_recommendation_items

（无 docstring）

### FunctionDef: _recommendation_fallback_reply

（无 docstring）

### FunctionDef: _recommendation_should_fallback

（无 docstring）

### FunctionDef: _recommendation_search_plans

推荐类搜索计划：保留原查询，并叠加榜单/作品搜索。

### FunctionDef: _skip_recommendation_suggestions

（无 docstring）

### FunctionDef: _skip_person_suggestions

（无 docstring）

### FunctionDef: _person_bio_key

（无 docstring）

### FunctionDef: _extract_esports_id

（无 docstring）

### FunctionDef: _person_bio_broken

（无 docstring）

### FunctionDef: _person_bio_fallback_reply

（无 docstring）

### FunctionDef: _is_lyrics_query

（无 docstring）

### FunctionDef: _classify_dialog_mode

（无 docstring）

### FunctionDef: _dialog_mode_prompt

（无 docstring）

### FunctionDef: _pick_abstract_variant

（无 docstring）

### FunctionDef: _classify_style_control

（无 docstring）

### FunctionDef: _style_control_prompt

（无 docstring）

### FunctionDef: _build_style_fewshot

（无 docstring）

### FunctionDef: _build_factual_fewshot

事实/攻略问题加入少量正确范例，重点教完整实体和“没查证不乱编”。

### FunctionDef: _current_date_text

（无 docstring）

### FunctionDef: _glm_web_search_tool

智谱 Web Search in Chat 专用工具：让免费 GLM 在 API 层直接联网检索。

### FunctionDef: log

（无 docstring）

### FunctionDef: _log_phase

（无 docstring）

### FunctionDef: write_health

增量更新健康状态；使用临时文件 + rename 保证原子写入。

### FunctionDef: read_health

读取健康状态；缺失或损坏时返回 None。

### FunctionDef: _admin_text

去掉 @ 前缀后识别管理员指令，兼容 `@bot !status`。

### FunctionDef: is_admin

（无 docstring）

### FunctionDef: _cmd_status

（无 docstring）

### FunctionDef: _cmd_style

（无 docstring）

### FunctionDef: _cmd_taffy

（无 docstring）

### FunctionDef: _cmd_model

（无 docstring）

### FunctionDef: _run_backup_hidden

（无 docstring）

### FunctionDef: _clear_memory_md

（无 docstring）

### FunctionDef: _reset_memory_md

（无 docstring）

### FunctionDef: _cmd_clear_memory

（无 docstring）

### FunctionDef: _cmd_forget

（无 docstring）

### FunctionDef: handle_admin_command

（无 docstring）

### FunctionDef: _cleanup_log

启动时检查日志大小，超过上限截断保留最后 2000 行

### FunctionDef: _cleanup_sessions

启动时检查对话存储大小；只有超过 50GB 才把最旧会话移入归档文件。

### FunctionDef: _keywords

简单分词：英文/数字整词 + 中文二元组，避免'谁是图赫尔'匹配不到'图赫尔'

### FunctionDef: _query_terms

先生成实体词，再去掉百科/新闻等泛词，避免无关结果被泛词放行。

### FunctionDef: _is_relevant_result

只要求命中实体/强词，不做逐条组合相关性判断。

### FunctionDef: _memory_blocks

（无 docstring）

### FunctionDef: _memory_scope_allowed

（无 docstring）

### FunctionDef: retrieve_knowledge_scored

返回 (命中的记忆文本, 最高命中分数)；低于门控阈值时调用方不注入。

### FunctionDef: retrieve_knowledge

兼容旧调用的文本检索入口。

### FunctionDef: _needs_search

只有明显需要实时/事实检索的问题才联网，避免每条消息都等搜索

### FunctionDef: _should_search

ALWAYS_SEARCH=1 时全部搜索；默认 0，只按事实/链接/直播等触发词搜索。

### FunctionDef: _generic_fallback_query

生成不依赖手写规则兜底的通用搜索词。

### FunctionDef: _should_attempt_generic_search

（无 docstring）

### FunctionDef: _is_factual

温度判定：真正需要准确的事实/知识问题才用低温。

### FunctionDef: _is_generic_year_noise

过滤“2026年百科/日历/放假”这种泛年份页，避免污染赛事结果。

### FunctionDef: _is_followup_query

只有明确包含代词或承接连词才认为是追问，避免短句被强行联系上文。

### FunctionDef: _clean_entities

过滤模型误把属性/年份当实体的情况，只保留真正的实体名。

### FunctionDef: _split_search_terms

把搜索词拆成独立关键词，供分别搜索后合并结果。

### FunctionDef: _classify_intent

web-search-chatbot 意图决策：规则优先，默认事实型。

### FunctionDef: _is_game_query

（无 docstring）

### FunctionDef: _is_guide_query

（无 docstring）

### FunctionDef: _should_serial_final

事实/攻略类不用竞速抢答，按稳定模型优先级串行调用。

### FunctionDef: _is_weak_reply

（无 docstring）

### FunctionDef: _split_sub_queries

按实体优先拆子问题；实体不足时按连词/标点拆，最多 3 个。

### FunctionDef: _extract_events_rule

LLM 事件识别失败时，用简单句式兜底提取事件。

### FunctionDef: _build_search_query

从问题里提取搜索关键词，去掉疑问词和聊天词，提升搜索命中率。

### FunctionDef: _extract_recommendation_entity

从“推荐几本东野圭吾的书”里拆出实体，但保留原查询不动。

### FunctionDef: _build_search_variants

生成多个完整搜索式：原始问题、清洗关键词、实体百科、LLM 补充式。

### FunctionDef: _search_cache_key

（无 docstring）

### FunctionDef: _search_cache_get

（无 docstring）

### FunctionDef: _search_cache_set

（无 docstring）

### FunctionDef: _is_engine_degraded

连续失败达到阈值后在 5 分钟内跳过该引擎。

### FunctionDef: _record_engine_fail

（无 docstring）

### FunctionDef: search_with_resilience

搜索统一入口：缓存 + 引擎失败统计 + 自动降级。

### FunctionDef: _answer_cache_key

（无 docstring）

### FunctionDef: _answer_cache_get

（无 docstring）

### FunctionDef: _answer_cache_set

（无 docstring）

### FunctionDef: _fetch_html

多套抓取兜底：curl -> urllib -> requests，避免单一网络库被反爬卡住。

### FunctionDef: _fetch_json

（无 docstring）

### FunctionDef: _find_drission_browser

（无 docstring）

### FunctionDef: _drission_search_html

真实浏览器搜索兜底，主要用来绕过百度/搜狗/360 的纯 HTTP 反爬。

### FunctionDef: _search_wikipedia

中文维基 JSON API，结构化返回，比网页搜索更少被反爬拦截。

### FunctionDef: _search_arxiv

arXiv API：结构化返回论文标题和摘要。

### FunctionDef: _search_pubmed

PubMed E-utilities：先查 ID，再拉标题摘要。

### FunctionDef: _search_baike_openapi

百度百科 OpenAPI：国内结构化实体兜底，反爬比网页版少。

### FunctionDef: _search_suggestions

百度/B站建议接口：无登录、无 key，用来补充相关搜索方向。

### FunctionDef: _search_douban_movies

豆瓣热门电影 JSON 接口，免登录、低反爬，用于电影推荐。

### FunctionDef: _extract_bing_results

只解析必应结果块 li.b_algo，避免把页面其他 h2/h3 当结果。

### FunctionDef: _extract_search_results

（无 docstring）

### FunctionDef: _search_engine

（无 docstring）

### FunctionDef: _extract_page_text

提取网页正文，优先正文容器，再去掉导航/脚本类噪音。

### FunctionDef: _extract_search_links

从必应/通用搜索结果页提取 URL，供歌词类深读使用。

### FunctionDef: _looks_like_lyrics_page

（无 docstring）

### FunctionDef: _extract_js_redirect

（无 docstring）

### AsyncFunctionDef: _fetch_page_text_async

（无 docstring）

### FunctionDef: _lyric_search_url

（无 docstring）

### FunctionDef: _extract_lyric_song_title

从歌词问题里提取歌名，去掉歌手/专辑/“歌词”这类搜索噪音。

### FunctionDef: _jayfc_search_links

周杰伦歌迷联盟无反爬直连，专门兜底周杰伦歌曲。

### AsyncFunctionDef: _fetch_lyric_links_from_engine

（无 docstring）

### AsyncFunctionDef: _fetch_lyrics_body

搜索歌词类 query，打开结果页并提取正文；没有正文时返回空串。

### FunctionDef: _lyric_subject_terms

提取歌词问题里的歌名关键词，用于回答校验，不做记忆写入。

### FunctionDef: _lyric_reply_suspect

（无 docstring）

### FunctionDef: _extract_lyrics_block

从深读正文里切出歌词行，避免模型重写后丢字/改字。

### FunctionDef: _extract_memory_lyrics

从 bot_memory.md 的手动歌词条目里抽取完整可靠歌词。

### FunctionDef: _lyric_key_fragments

选最长的几行可靠歌词做逐字校验，抓模型漏字/改字。

### FunctionDef: _build_lyrics_fallback_answer

（无 docstring）

### FunctionDef: search_web

多源搜索：统一入口带缓存、引擎失败统计和自动降级。

### AsyncFunctionDef: search_web_async

并发搜索国内源：必应中国/百度桌面/搜狗桌面为主，整体 5 秒内返回。

### FunctionDef: _merge_search_results

合并多个搜索源的结果并按标题去重。

### FunctionDef: _rule_plan_for_query

高频问题类型的规则兜底搜索计划，模型失效时也能拆。

### FunctionDef: _normalize_plan_queries

校验 plan_search 查询；空计划不再用规则兜底，避免乱搜。

### FunctionDef: _split_planned_query

长 query 兜底拆分：按常见连接词拆成短查询。

### AsyncFunctionDef: _search_planned_queries

执行 plan_search 返回的多个短 query，并行搜索后合并。

### FunctionDef: _source_rank

（无 docstring）

### FunctionDef: _append_age_fact

从搜索结果提取出生日期，按今天本地计算年龄，避免模型算错。

### FunctionDef: _distill_search_results

本地轻量凝练：只按实体词过滤，不做组合打分排序。

### FunctionDef: _web_search_to_text

把智谱原生 web_search 返回结果转成本地搜索结果格式。

### FunctionDef: _fix_known_phrase_errors

修已知固定短语的轻微缩字，例如玄武口音里的“玄武神兽”。

### FunctionDef: _strip_suggestion_lines

去掉建议词/提示词，避免把搜索建议当成可引用事实。

### FunctionDef: _is_important_number

短数字不带单位时不算关键事实，避免“20”被当成缺失字段。

### FunctionDef: _ensure_numeric_facts_preserved

把来源里的数字事实逐字保真，补回模型漏掉的前置数字。

### FunctionDef: _split_source_entities

从来源里拆出短专名候选，用于补齐模型压缩掉的后缀。

### FunctionDef: _ensure_entity_facts_preserved

来源里有完整专名、回答只写前缀时，补回后缀。

### FunctionDef: _collapsed_source_variants

生成“中间被删掉一段”的压缩写法，用于匹配模型截断后的专名。

### FunctionDef: _ensure_source_phrases_preserved

把来源里的完整长短语找回来，修复中间被压掉的片段。

### FunctionDef: _looks_like_tool_call

识别模型把工具调用写成文本、没进入最终答案的情况。

### FunctionDef: _extract_tool_calls_from_text

从模型误写进正文的工具调用 JSON 中恢复 tool_calls。

### FunctionDef: _looks_garbled_reply

（无 docstring）

### FunctionDef: _has_repeated_long_phrase

（无 docstring）

### FunctionDef: _needs_self_check

（无 docstring）

### FunctionDef: _repetition_score

粗略检测两句是否复读：按字符二元组重叠度。

### FunctionDef: _required_entity_terms

（无 docstring）

### FunctionDef: _reply_missing_entities

（无 docstring）

### FunctionDef: _answer_field_hints

（无 docstring）

### FunctionDef: _source_required_numbers

来源里带单位的数字/较长数字必须逐项出现在回答中。

### FunctionDef: _source_required_dates

（无 docstring）

### FunctionDef: _unsupported_numeric_phrases

回答里的数字若不在来源中，视为模型硬凑，触发重答。

### FunctionDef: _answer_contract_missing

（无 docstring）

### FunctionDef: _complete_missing_fields

契约仍缺失时，把来源里能确认的实体/数字/日期补到回答尾部。

### FunctionDef: _filter_silent_memory_text

（无 docstring）

### FunctionDef: _extract_style_fragments_v2

采集一条群消息的多维风格特征。

### FunctionDef: _discover_ngrams

从最近消息中发现高频候选新梗。

### ClassDef: GroupStyleStore

多维群说话风格学习器，兼容原接口。

### FunctionDef: build_style_system_prompt

（无 docstring）

### FunctionDef: _is_question_or_quote

（无 docstring）

### FunctionDef: _should_inject_group_style

群风格只作为记忆背景，不再长期注入实时 prompt。

### FunctionDef: _try_self_improve

检测纠正和新的时效事实，自动追加到 bot_memory.md

### FunctionDef: acquire_single_instance

创建命名互斥锁。已存在=已有桥接在跑，返回 False。

### ClassDef: SessionStore

（无 docstring）

### ClassDef: Bridge

（无 docstring）

### FunctionDef: _onebot_http_probe

启动时探测 OneBot HTTP API 是否可用；测试模式跳过真实网络请求。

### FunctionDef: run_startup_checks

每次启动前离线自检，失败则拒绝启动，避免带着坏规则上线。

### FunctionDef: main

（无 docstring）

### FunctionDef: add

（无 docstring）

### FunctionDef: add_call

（无 docstring）

### FunctionDef: __init__

（无 docstring）

### FunctionDef: _load

（无 docstring）

### FunctionDef: save

（无 docstring）

### FunctionDef: _atexit_save

（无 docstring）

### FunctionDef: learn

（无 docstring）

### FunctionDef: _update_counter

（无 docstring）

### FunctionDef: _extract_common_slang

（无 docstring）

### FunctionDef: style_prompt

（无 docstring）

### FunctionDef: _stats_to_prompt

（无 docstring）

### AsyncFunctionDef: generate_style_description

（无 docstring）

### FunctionDef: discover_new_slang

（无 docstring）

### FunctionDef: get_group_profile

（无 docstring）

### FunctionDef: get_analogy_methodology

（无 docstring）

### FunctionDef: set_analogy_methodology

（无 docstring）

### FunctionDef: __init__

（无 docstring）

### FunctionDef: _load

（无 docstring）

### FunctionDef: save

（无 docstring）

### FunctionDef: get

（无 docstring）

### FunctionDef: put

存储 session 数据（方法名避免与内置 set 冲突）

### FunctionDef: update

线程安全地读取-修改-写回一个 session。

### FunctionDef: __init__

（无 docstring）

### FunctionDef: _load_api_key

从环境变量或 hermes .env 读取 OPENCODE_GO_API_KEY

### FunctionDef: worker_for

每个群/私聊一个串行队列 worker

### AsyncFunctionDef: _worker_loop

（无 docstring）

### AsyncFunctionDef: _health_heartbeat

（无 docstring）

### FunctionDef: _parse_card_payload

（无 docstring）

### FunctionDef: _card_text

（无 docstring）

### FunctionDef: extract_text

OneBot array 消息 -> (纯文本, 是否@机器人)

### AsyncFunctionDef: _fetch_reply_context

通过 OneBot get_msg 拉取被回复消息，补上卡片/链接等上下文。

### FunctionDef: _api_call

流式同步调用：连接/响应头超时后按 network 失败并切换。

### FunctionDef: _parse_stream_response

解析 OpenAI 兼容流式/JSON 响应。

### AsyncFunctionDef: _complete

遍历模型列表完成一次调用，返回 (回复, 使用的模型)。

### AsyncFunctionDef: _complete_race

模型竞速：前两个可用模型同时请求，谁先返回有效结果用谁。

### AsyncFunctionDef: _complete_with_tools

第一轮：带工具列表调用模型，返回 (tool_calls, 直接回复, 模型, 原生联网结果)。

### AsyncFunctionDef: _compress_history

后台提炼旧历史为摘要，不清空原文，只记录已压缩位置。

### AsyncFunctionDef: _search_with_terms

搜多个完整搜索式，再做实体百科和兜底补充，最后合并去重。

### AsyncFunctionDef: _enrich_lyrics_search

歌词类问题补一层正文抓取：摘要不足时打开结果页，喂真实正文。

### AsyncFunctionDef: _parallel_search_subqueries

按 skill 并行搜索多个子问题，单个子问题失败不影响其他。

### AsyncFunctionDef: _optimize_search_query

用 LLM 生成多个完整搜索式和核心实体；失败或超时退回规则版。

### AsyncFunctionDef: _grounding_check

事实核查：用 LLM 结构化判断，不只靠关键词重叠。

### AsyncFunctionDef: _reflect

任务级反思：评估并最多改进一轮。

### AsyncFunctionDef: _restyle_as_taffy

（无 docstring）

### AsyncFunctionDef: _llm_answer_from_search

（无 docstring）

### AsyncFunctionDef: ask_model

直连模型 API：历史 + 记忆门控 + 快速工具检索 + 可选反思。

### AsyncFunctionDef: _process_group

（无 docstring）

### AsyncFunctionDef: _process_private

（无 docstring）

### AsyncFunctionDef: send_group_text

（无 docstring）

### AsyncFunctionDef: send_group_msg

（无 docstring）

### AsyncFunctionDef: send_private_msg

（无 docstring）

### AsyncFunctionDef: call_api

通过 OneBot 11 HTTP API 调用（默认 http://127.0.0.1:6099）。

### AsyncFunctionDef: _deep_compress_if_needed

对话数据达到 45GB 时，后台压缩各会话旧历史，保留重要内容。

### AsyncFunctionDef: _learn_group_history

从群历史中学习值得长期记住的信息，写入 bot_memory.md。

### AsyncFunctionDef: _learn_analogy_methodology

（无 docstring）

### AsyncFunctionDef: _periodic_style_learning

（无 docstring）

### AsyncFunctionDef: run

（无 docstring）

### FunctionDef: merge

（无 docstring）

### FunctionDef: add

（无 docstring）

### AsyncFunctionDef: call_one

（无 docstring）

### FunctionDef: update

（无 docstring）

### AsyncFunctionDef: generic_fallback_search

（无 docstring）

### FunctionDef: _do

（无 docstring）

## qqbot_core\__init__.py

## qqbot_core\config.py

### FunctionDef: _runtime_tmp_path

（无 docstring）

### FunctionDef: _read_memory_file_cached

（无 docstring）

### FunctionDef: _load_env_file

加载项目根目录 .env 文件中的环境变量（不覆盖已有值）。

## qqbot_core\learning.py

### FunctionDef: record_group_learning

记录群聊中的纠正、梗和值得长期保留的信息。

## qqbot_core\prompts.py

## qqbot_core\safety.py

### FunctionDef: _check_safety

检查输入是否安全；self_harm 优先级最高。

## qqbot_core\stats.py

### FunctionDef: _load_stats

（无 docstring）

### FunctionDef: inc_stat

原子递增统计并落盘。

### FunctionDef: record_reply_time

记录一次回复耗时。

### FunctionDef: get_avg_reply_time

（无 docstring）

### FunctionDef: _flush_stats

（无 docstring）

## qqbot_core\style.py

### FunctionDef: _is_taffy_styled

（无 docstring）

### FunctionDef: _append_search_source_marker

回答末尾标注本次是否来自网络搜索。

## qqbot_core\text.py

### FunctionDef: _clean_question

去掉引用/表情等标记，并做群内常见指代归一，搜索才更准。

### FunctionDef: _clean_search_query

清洗搜索词：去掉引用原文、昵称前缀，避免把上一句回答搜进去。

### FunctionDef: _normalize_entity_spacing

把搜索引擎在中文实体里插入的空格去掉，避免“洛克 王国”被拆碎。

### FunctionDef: _entity_norm

实体匹配用：去掉空格、书名号、冒号，避免“洛克王国：世界”对不上。

### FunctionDef: _clean_reply

清理模型返回中的 think 标签，避免推理过程发到群里。

### FunctionDef: _repair_utf8

修复 opencode 等接口可能返回的 UTF-8/Latin-1 双重编码乱码。

### FunctionDef: _sanitize_reply

只做格式清理，不替换模型已经表达清楚的句意。

### FunctionDef: _truncate_at_sentence

长度截断只允许在完整句子边界处发生，避免切到词中间。
