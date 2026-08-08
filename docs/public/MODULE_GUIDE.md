# QQBot 模块职责导览

> 完整函数清单见 [MODULE_FUNCTIONS.md](MODULE_FUNCTIONS.md)  
> 这里按模块和功能组解释每个函数大致做什么。

---

## `qqbot_core/config.py`

负责全局配置和路径。

- `_runtime_tmp_path(name)`：返回 `<项目根>\.tmp\name`，并确保目录存在。
- `_read_memory_file_cached()`：带缓存读取 `bot_memory.md`。
- `_load_env_file()`：加载项目根目录 `.env`。

常量包括：

- 路径：`BASE_DIR`、`MEMORY_FILE`、`SESSIONS_FILE`、`LOG_FILE`、`HEALTH_FILE`。
- 连接：`WS_URL`、`ONEBOT_HTTP_URL`、`TRIGGER`。
- 模型：`PROVIDERS`、`MODELS`、`MODELS_FACTUAL`、`MODELS_CASUAL`。
- 采样：`FACTUAL_TEMPERATURE`、`FACTUAL_TOP_P`、`CASUAL_TEMPERATURE`、`CASUAL_TOP_P`。
- 搜索：`ALWAYS_SEARCH`、`MAX_SEARCH_CHARS`、`TOOL_CALL_TIMEOUT`。

---

## `qqbot_core/style.py`

负责永雏塔菲风格模型。

- `_is_taffy_styled(reply)`：判断回答是否具备“塔菲身份 + 情绪标记”。
- `_append_search_source_marker(reply, search_returned, search_attempted)`：追加联网/未联网来源标注。

常量：

- `SYSTEM_PROMPT`：主角色提示词。
- `TAFFY_FLAVOR_PROMPT`：塔菲角色叠加提示词。
- `CURRENT_DATE_PROMPT`：当前日期提示词。
- `SEARCH_STRATEGY_SYSTEM`：通用搜索策略。

---

## `qqbot_core/prompts.py`

只放提示词常量，不做逻辑。

- `REFLECTION_EVAL_SYSTEM`：反思评估。
- `REFLECTION_IMPROVE_SYSTEM`：反思改进。
- `HISTORY_SUMMARY_SYSTEM`：历史摘要。
- `GROUP_MEMORY_LEARN_SYSTEM`：群记忆学习。
- `NO_REPEAT_SYSTEM`：防复读重写。
- `TOOL_FINAL_SYSTEM`：工具结果转最终回答。
- `LYRIC_RETRY_SYSTEM`：歌词重答。
- `CONTRACT_RETRY_SYSTEM`：契约缺失重答。
- `SELF_CHECK_SYSTEM`：回答自检。
- `GROUNDING_CHECK_SYSTEM`：事实核查。
- `GROUNDING_IMPROVE_SYSTEM`：事实核查后重写。
- `QUERY_COMPRESS_SYSTEM`：搜索关键词优化。

---

## `qqbot_core/text.py`

纯文本工具，不依赖业务逻辑。

- `_clean_question(text)`：清理引用/@/昵称，并归一常见指代。
- `_clean_search_query(text)`：清洗搜索词，避免引用污染。
- `_normalize_entity_spacing(text)`：去掉中文实体中间空格。
- `_entity_norm(text)`：实体归一，用于匹配。
- `_clean_reply(reply)`：删除 `<think>` 推理标签。
- `_repair_utf8(text)`：修复 UTF-8/Latin-1 双重编码乱码。
- `_sanitize_reply(reply)`：清理 Markdown、重复 emoji、格式残留。
- `_truncate_at_sentence(text, limit)`：只在句子边界截断。

---

## `qqbot_core/stats.py`

负责运行统计。

- `_load_stats()`：启动时读取已有 `stats.json`，跨重启累计。
- `inc_stat(key, n)`：原子递增统计。
- `record_reply_time(ms)`：记录回复耗时。
- `get_avg_reply_time()`：返回平均回复耗时。
- `_flush_stats()`：把统计写入 `data/stats.json`。

---

## `qqbot_core/safety.py`

负责安全护栏。

- `_check_safety(text)`：检查 self_harm/violence/illegal/adult/sensitive 五类内容。

常量：

- `SAFETY_PATTERNS`：敏感词规则。
- `REFUSAL_TEMPLATES`：拒绝模板。

---

## `qqbot_core/learning.py`

负责群聊长期学习。

- `record_group_learning(scope, user, text, reply)`：记录用户纠正、新梗、约定到 `data/learnings.md`。

---

## `bridge_qq.py`

主桥接文件，仍然负责大量业务编排。

### 模型工具

- `_safe_max_tokens(provider, model_name, max_tokens)`：限制部分 GLM 模型的 max_tokens。
- `_dynamic_final_max_tokens(is_factual, guide_query)`：按问题类型动态调整最终输出 token。

### 模型熔断

- `_provider_blocked(model_ref)`：判断模型是否处于熔断期。
- `_mark_provider_down(model_ref, seconds)`：标记模型异常并熔断。
- `_mark_provider_ok(model_ref)`：标记模型恢复。
- `_mark_provider_weak(model_ref)`：标记弱回复。
- `_sort_models(model_refs)`：按健康分和基础顺序排序。

### 推荐与历史清洗

- `_is_song_recommendation_query(text)`：判断是否歌曲推荐。
- `_song_recommendation_source()`：返回完整歌单来源。
- `_song_recommendation_fallback_reply()`：歌曲推荐兜底。
- `_book_recommendation_fallback_reply(entity)`：书籍推荐兜底。
- `_strip_history_prompt_noise(content)`：剥离旧 system 提示词残留。
- `_sanitize_history_content(content, role)`：读取历史时清理污染。
- `_clean_date_concat(text)`：修复粘连日期。
- `_refine_polluted_history(content)`：保留信息、删除乱码。

### 对话模式与抽象

- `_classify_dialog_mode(text)`：分类闲聊/任务/创意/安慰/歌词。
- `_dialog_mode_prompt(mode)`：生成对应模式提示词。
- `_pick_abstract_variant(query, key, history_len)`：轮换抽象角度。
- `_classify_style_control(query)`：判断抽象等级和敏感话题。
- `_style_control_prompt(level, sensitive)`：生成抽象控制提示。
- `_build_style_fewshot(query, level, key)`：生成抽象参考示例。
- `_build_factual_fewshot(query)`：生成事实回答参考示例。

### 日志与健康

- `log(msg)`：写控制台和日志文件。
- `_log_phase(start, label)`：记录阶段耗时。
- `write_health(**kwargs)`：原子写入健康状态。
- `read_health()`：读取健康状态。

### 管理员命令

- `_admin_text(text)`：去掉 @ 前缀。
- `is_admin(user_id)`：判断是否管理员。
- `_cmd_status(bridge)`：返回 PID/WS/会话状态。
- `_cmd_style(level_str)`：设置抽象等级。
- `_cmd_taffy(on_off)`：开关塔菲。
- `_cmd_model()`：显示模型顺序。
- `_run_backup_hidden(tag)`：隐藏调用备份脚本。
- `_clear_memory_md(scope_key)`：清理长期记忆块。
- `_reset_memory_md()`：重置长期记忆。
- `_cmd_clear_memory(args, user_id, bridge)`：全局清记忆。
- `_cmd_forget(context, bridge)`：清当前会话记忆。
- `handle_admin_command(text, user_id, context, bridge)`：管理员命令分发。

### 知识库

- `_keywords(text)`：提取关键词。
- `_query_terms(text)`：提取搜索相关词。
- `_is_relevant_result(combined, qterms)`：判断结果是否相关。
- `_memory_blocks(memory_text)`：按 `##` 分块。
- `retrieve_knowledge_scored(...)`：按关键词检索记忆。
- `retrieve_knowledge(...)`：兼容旧接口。

### 搜索

- `_needs_search(text)`：判断是否需要搜索。
- `_should_search(text)`：判断是否开放搜索工具。
- `_generic_fallback_query(query)`：生成通用兜底搜索词。
- `_is_factual(text)`：判断是否事实问题。
- `_clean_entities(entities)`：清理实体。
- `_classify_intent(text)`：判断意图。
- `_is_guide_query(text)`：判断是否攻略类。
- `_split_sub_queries(text, entities)`：拆子问题。
- `_build_search_query(text)`：生成搜索关键词。
- `_build_search_variants(...)`：生成多个搜索变体。
- `_search_cache_key/get/set`：搜索缓存。
- `_is_engine_degraded(engine)`：判断引擎降级。
- `_record_engine_fail(engine)`：记录引擎失败。
- `search_with_resilience(query, max_results)`：搜索统一入口。
- `_fetch_html(url, timeout, extra_headers)`：网页抓取。
- `_search_engine(query, engine, max_results)`：单引擎搜索。
- `_distill_search_results(...)`：搜索结果凝练。
- `search_web(query, max_results)`：同步搜索入口。
- `search_web_async(...)`：异步并发搜索。

### 回答清理与补全

- `_looks_like_tool_call(reply)`：判断工具调用是否被写成文本。
- `_extract_tool_calls_from_text(text)`：恢复正文里的工具调用 JSON。
- `_looks_garbled_reply(reply)`：判断乱码回答。
- `_has_repeated_long_phrase(text)`：判断重复长片段。
- `_ensure_numeric_facts_preserved(...)`：补齐来源数字。
- `_ensure_entity_facts_preserved(...)`：补齐完整专名。
- `_ensure_source_phrases_preserved(...)`：补齐来源短语。
- `_fix_known_phrase_errors(reply, query)`：修已知错字。

### 群风格学习

- `_extract_style_fragments_v2(text)`：提取群风格特征。
- `_discover_ngrams(...)`：发现新梗。
- `GroupStyleStore`：群风格存储。
- `build_style_system_prompt(style_store, gid)`：生成群风格提示词。

### 自我改进

- `_is_question_or_quote(text)`：判断是否问题或引用。
- `_should_inject_group_style(key, query)`：是否注入群风格。
- `_try_self_improve(...)`：检测纠正并尝试学习。

### 会话与桥接

- `SessionStore`：会话持久化。
- `Bridge`：主桥接类。
  - `extract_text`：解析 OneBot 消息。
  - `_parse_card_payload`：解析卡片。
  - `_card_text`：卡片转文本。
  - `_fetch_reply_context`：获取引用消息。
  - `_complete`：模型串行调用。
  - `_complete_race`：模型竞速。
  - `_complete_with_tools`：带工具的第一轮调用。
  - `ask_model`：核心回答链路。
  - `_process_group`：群消息处理。
  - `_process_private`：私聊消息处理。
  - `send_group_msg` / `send_private_msg`：发送消息。
  - `call_api`：OneBot HTTP API。
  - `run`：WebSocket 主循环。

### 启动

- `acquire_single_instance()`：单实例锁。
- `_onebot_http_probe()`：探测 OneBot HTTP。
- `run_startup_checks()`：启动自检。
- `main()`：程序入口。
