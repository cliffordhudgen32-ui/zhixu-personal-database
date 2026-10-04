# 数据库 Schema v1.1

UTC ISO 8601 时间；业务 date 为本地日期。UUID 是外部永久身份。

当前迁移：`20261004_capture`，在 `20261003_workflow` 之后新增独立文字采集草稿表。

`capture_drafts` 与 `entries` 分开：`open` 可编辑，`discarded` 是可恢复的收起状态，`submitted` 已明确保存到收集箱。`revision` 用于并发版本校验，首次暂存为 1；正文可以为空暂存，提交时必须非空。`project_ids` 保存项目 UUID 数组，暂存与提交时验证项目存在。确认提交在同一事务创建同 UUID 的 `entries` 原始记录并写入 `submitted_entry_uuid`；重复提交返回已有记录当前值，不覆盖后续编辑。原始记录彻底删除后外键置空，不通过草稿重建。

所有状态的文字草稿会保存在完整 SQLite ZIP 与全库 JSON 中；未提交草稿不参与普通资料搜索、记录导出或 AI 整理。网页尚未上传的附件仅在页面内存中；桌面粘贴截图临时文件位于数据库旁的 `capture-temp/`，不属于登记附件，不在完整 ZIP 内。普通记录导入器不接收全库 JSON，完整恢复使用 ZIP。

人工从 AI 草稿计划创建的待办仍存入 `tasks`，来源保存在 `metadata_json.ai_followup`：包含草稿 UUID/版本、计划原句及候选标识、创建时草稿状态、原始来源与可选手动关联记录。普通编辑保留来源，同一草稿同一候选重复提交不覆盖已有任务。来源与关联项目隐私上调会传递到这类待办；创建待办不等于确认 AI 归档或完成事项。

## 关系
entries ↔ tags/projects/people：多对多；entries → attachments/links：一对多；
tasks/events → projects/people：可空外键；删除项目/人物时 SET NULL，删除记录时附件/链接元数据 CASCADE。
entries.deleted_at 实现回收站；FTS5 trigram 由三条触发器同步。

## ai_configuration

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| id | INTEGER | False | 主键 |
| values | JSON | False |  |
| updated_at | VARCHAR(40) | False |  |

索引：

## ai_jobs

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| kind | VARCHAR(50) | False |  |
| status | VARCHAR(30) | False |  |
| payload | JSON | False |  |
| progress | JSON | False |  |
| message | TEXT | False |  |
| error | TEXT | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_ai_jobs_origin(origin); ix_ai_jobs_kind(kind); ix_ai_jobs_uuid(uuid); ix_ai_jobs_status(status); ix_ai_jobs_created_at(created_at)

## audit_logs

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| id | INTEGER | False | 主键 |
| created_at | VARCHAR(40) | False |  |
| action | VARCHAR(60) | False |  |
| entity | VARCHAR(60) | False |  |
| object_uuid | VARCHAR(100) | False |  |
| description | VARCHAR(500) | False |  |

索引：ix_audit_logs_created_at(created_at)

## custom_fields

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(100) | False | 唯一 |
| label | VARCHAR(200) | False |  |
| field_type | VARCHAR(30) | False |  |
| entity_type | VARCHAR(40) | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_custom_fields_uuid(uuid); ix_custom_fields_created_at(created_at); ix_custom_fields_origin(origin)

## domains

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(200) | False |  |
| description | TEXT | False |  |
| parent_id | INTEGER | True | domains.id ON DELETE RESTRICT |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_domains_uuid(uuid); ix_domains_parent_id(parent_id); ix_domains_origin(origin); ix_domains_created_at(created_at); ix_domains_name(name)

## entries

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| item_type | VARCHAR(50) | False |  |
| origin | VARCHAR(30) | False |  |
| content_nature | VARCHAR(30) | False |  |
| original_content | TEXT | False |  |
| normalized_content | TEXT | False |  |
| ai_summary | TEXT | False |  |
| generated_by_ai | BOOLEAN | False |  |
| ai_provider | VARCHAR(100) | False |  |
| ai_model | VARCHAR(100) | False |  |
| generated_at | VARCHAR(40) | True |  |
| date | DATE | False |  |
| title | VARCHAR(500) | False |  |
| content | TEXT | False |  |
| summary | TEXT | False |  |
| category | VARCHAR(100) | False |  |
| sub_category | VARCHAR(100) | False |  |
| importance | INTEGER | False |  |
| status | VARCHAR(50) | False |  |
| source | TEXT | False |  |
| location_text | VARCHAR(500) | False |  |
| notes | TEXT | False |  |
| privacy_level | INTEGER | False |  |
| is_favorite | BOOLEAN | False |  |
| is_archived | BOOLEAN | False |  |
| deleted_at | VARCHAR(40) | True |  |
| content_hash | VARCHAR(64) | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |

索引：ix_entries_status(status); ix_entries_deleted_at(deleted_at); ix_entries_uuid(uuid); ix_entries_origin(origin); ix_entries_content_nature(content_nature); ix_entries_privacy_level(privacy_level); ix_entries_is_archived(is_archived); ix_entries_content_hash(content_hash); ix_entries_created_at(created_at); ix_entries_active_date(deleted_at, date, id); ix_entries_category(category); ix_entries_is_favorite(is_favorite); ix_entries_item_type(item_type); ix_entries_date(date); ix_entries_importance(importance)

## people

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(200) | False |  |
| nickname | VARCHAR(200) | False |  |
| organization | VARCHAR(300) | False |  |
| position | VARCHAR(300) | False |  |
| contact_note | TEXT | False |  |
| notes | TEXT | False |  |
| privacy_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_people_created_at(created_at); ix_people_origin(origin); ix_people_name(name); ix_people_uuid(uuid)

## projects

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(300) | False |  |
| description | TEXT | False |  |
| status | VARCHAR(50) | False |  |
| priority | INTEGER | False |  |
| start_date | DATE | True |  |
| end_date | DATE | True |  |
| summary | TEXT | False |  |
| privacy_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_projects_name(name); ix_projects_created_at(created_at); ix_projects_uuid(uuid); ix_projects_origin(origin); ix_projects_status(status)

## tags

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(100) | False | 唯一 |
| color | VARCHAR(30) | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_tags_uuid(uuid); ix_tags_origin(origin); ix_tags_created_at(created_at)

## ai_conversations

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| entry_uuid | VARCHAR(36) | True | entries.uuid ON DELETE CASCADE |
| platform | VARCHAR(100) | False |  |
| conversation_title | VARCHAR(500) | False |  |
| conversation_date | DATE | False |  |
| user_message | TEXT | False |  |
| assistant_message | TEXT | False |  |
| summary | TEXT | False |  |
| tags | JSON | False |  |
| project_id | INTEGER | True | projects.id ON DELETE SET NULL |
| source_file | TEXT | False |  |
| privacy_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_ai_conversations_created_at(created_at); ix_ai_conversations_project_id(project_id); ix_ai_conversations_entry_uuid(entry_uuid); ix_ai_conversations_origin(origin); ix_ai_conversations_uuid(uuid)

## ai_drafts

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| date | DATE | False |  |
| scope_key | VARCHAR(80) | False |  |
| request_key | VARCHAR(64) | False | 唯一 |
| source_hash | VARCHAR(64) | False |  |
| target_hash | VARCHAR(64) | False |  |
| status | VARCHAR(30) | False |  |
| revision | INTEGER | False |  |
| title | VARCHAR(500) | False |  |
| content | TEXT | False |  |
| summary | TEXT | False |  |
| category | VARCHAR(100) | False |  |
| tags | JSON | False |  |
| questions | JSON | False |  |
| warnings | JSON | False |  |
| evidence | JSON | False |  |
| source_records | JSON | False |  |
| answers | JSON | False |  |
| provider | VARCHAR(100) | False |  |
| model | VARCHAR(100) | False |  |
| error | TEXT | False |  |
| archived_uuid | VARCHAR(36) | True | entries.uuid ON DELETE SET NULL |
| approved_at | VARCHAR(40) | True |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_ai_drafts_uuid(uuid); ix_ai_drafts_date(date); ix_ai_drafts_created_at(created_at); ix_ai_drafts_origin(origin); ix_ai_drafts_status(status); ix_ai_drafts_scope_key(scope_key)

## attachments

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| original_filename | VARCHAR(500) | False |  |
| stored_filename | VARCHAR(100) | False |  |
| file_path | VARCHAR(500) | False | 唯一 |
| file_type | VARCHAR(200) | False |  |
| file_size | INTEGER | False |  |
| sha256 | VARCHAR(64) | False |  |
| entry_id | INTEGER | False | entries.id ON DELETE CASCADE |
| description | TEXT | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_attachments_created_at(created_at); ix_attachments_entry_id(entry_id); ix_attachments_origin(origin); ix_attachments_uuid(uuid); ix_attachments_sha256(sha256)

## capture_drafts

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| date | DATE | False |  |
| title | VARCHAR(500) | False |  |
| content | TEXT | False |  |
| template_key | VARCHAR(50) | False |  |
| privacy_level | INTEGER | False |  |
| project_ids | JSON | False |  |
| revision | INTEGER | False |  |
| status | VARCHAR(20) | False |  |
| discarded_at | VARCHAR(40) | True |  |
| submitted_at | VARCHAR(40) | True |  |
| submitted_entry_uuid | VARCHAR(36) | True | entries.uuid ON DELETE SET NULL |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_capture_drafts_submitted_entry_uuid(submitted_entry_uuid); ix_capture_drafts_status_updated(status, updated_at, id); ix_capture_drafts_date(date); ix_capture_drafts_uuid(uuid); ix_capture_drafts_created_at(created_at); ix_capture_drafts_status(status); ix_capture_drafts_origin(origin)

## entry_people

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| entry_id | INTEGER | False | entries.id ON DELETE CASCADE |
| person_id | INTEGER | False | people.id ON DELETE CASCADE |

索引：ix_entry_people_reverse(person_id, entry_id)

## entry_projects

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| entry_id | INTEGER | False | entries.id ON DELETE CASCADE |
| project_id | INTEGER | False | projects.id ON DELETE CASCADE |

索引：ix_entry_projects_reverse(project_id, entry_id)

## entry_tags

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| entry_id | INTEGER | False | entries.id ON DELETE CASCADE |
| tag_id | INTEGER | False | tags.id ON DELETE CASCADE |

索引：ix_entry_tags_reverse(tag_id, entry_id)

## events

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| title | VARCHAR(500) | False |  |
| description | TEXT | False |  |
| date | DATE | False |  |
| project_id | INTEGER | True | projects.id ON DELETE SET NULL |
| person_id | INTEGER | True | people.id ON DELETE SET NULL |
| privacy_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_events_person_id(person_id); ix_events_project_id(project_id); ix_events_uuid(uuid); ix_events_date(date); ix_events_origin(origin); ix_events_created_at(created_at)

## knowledge_relations

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| from_uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| to_uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| relation_type | VARCHAR(60) | False |  |
| description | TEXT | False |  |
| judgment | TEXT | False |  |
| evidence | TEXT | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_knowledge_relations_created_at(created_at); ix_knowledge_relations_origin(origin); ix_knowledge_relations_to_uuid(to_uuid); ix_knowledge_relations_relation_type(relation_type); ix_knowledge_relations_from_uuid(from_uuid); ix_knowledge_relations_uuid(uuid)

## links

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| title | VARCHAR(500) | False |  |
| url | TEXT | False |  |
| description | TEXT | False |  |
| entry_id | INTEGER | False | entries.id ON DELETE CASCADE |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_links_origin(origin); ix_links_entry_id(entry_id); ix_links_created_at(created_at); ix_links_uuid(uuid)

## reuse_logs

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| item_uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| project_id | INTEGER | True | projects.id ON DELETE SET NULL |
| used_at | VARCHAR(40) | False |  |
| result | TEXT | False |  |
| notes | TEXT | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_reuse_logs_item_uuid(item_uuid); ix_reuse_logs_uuid(uuid); ix_reuse_logs_project_id(project_id); ix_reuse_logs_used_at(used_at); ix_reuse_logs_created_at(created_at); ix_reuse_logs_origin(origin)

## reviews

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| date | DATE | False |  |
| period | VARCHAR(20) | False |  |
| summary | TEXT | False |  |
| learning | TEXT | False |  |
| problems | TEXT | False |  |
| plan | TEXT | False |  |
| review_type | VARCHAR(50) | False |  |
| target_type | VARCHAR(50) | False |  |
| target_uuid | VARCHAR(36) | True | entries.uuid ON DELETE SET NULL |
| project_uuid | VARCHAR(36) | True | projects.uuid ON DELETE SET NULL |
| what_went_well | TEXT | False |  |
| what_went_wrong | TEXT | False |  |
| lessons | TEXT | False |  |
| next_action | TEXT | False |  |
| reasoning | TEXT | False |  |
| expectations | TEXT | False |  |
| chance_factors | TEXT | False |  |
| reconsideration | TEXT | False |  |
| keep_methods | TEXT | False |  |
| avoid_methods | TEXT | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_reviews_date(date); ix_reviews_origin(origin); ix_reviews_target_uuid(target_uuid); ix_reviews_uuid(uuid); ix_reviews_project_uuid(project_uuid); ix_reviews_created_at(created_at)

## revisions

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| item_uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| reason | TEXT | False |  |
| old_version | JSON | False |  |
| new_version | JSON | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_revisions_created_at(created_at); ix_revisions_origin(origin); ix_revisions_item_uuid(item_uuid); ix_revisions_uuid(uuid)

## tasks

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| title | VARCHAR(500) | False |  |
| description | TEXT | False |  |
| status | VARCHAR(50) | False |  |
| priority | INTEGER | False |  |
| due_date | DATE | True |  |
| completed_at | VARCHAR(40) | True |  |
| project_id | INTEGER | True | projects.id ON DELETE SET NULL |
| person_id | INTEGER | True | people.id ON DELETE SET NULL |
| privacy_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_tasks_project_id(project_id); ix_tasks_uuid(uuid); ix_tasks_origin(origin); ix_tasks_status(status); ix_tasks_due_date(due_date); ix_tasks_person_id(person_id); ix_tasks_completed_at(completed_at); ix_tasks_created_at(created_at)

## topics

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| name | VARCHAR(200) | False |  |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| parent_id | INTEGER | True | topics.id ON DELETE RESTRICT |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_topics_created_at(created_at); ix_topics_origin(origin); ix_topics_uuid(uuid); ix_topics_name(name); ix_topics_parent_id(parent_id); ix_topics_domain_id(domain_id)

## ai_archives

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| scope_key | VARCHAR(80) | False | 主键 |
| entry_uuid | VARCHAR(36) | True | entries.uuid ON DELETE SET NULL |
| last_source_hash | VARCHAR(64) | False |  |
| last_draft_uuid | VARCHAR(36) | True | ai_drafts.uuid ON DELETE SET NULL |
| updated_at | VARCHAR(40) | False |  |

索引：

## ai_review_revisions

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| draft_uuid | VARCHAR(36) | False | ai_drafts.uuid ON DELETE CASCADE |
| revision | INTEGER | False |  |
| action | VARCHAR(30) | False |  |
| snapshot | JSON | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | 唯一 |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |
| metadata_json | JSON | False |  |
| origin | VARCHAR(30) | False |  |

索引：ix_ai_review_revisions_draft_uuid(draft_uuid); ix_ai_review_revisions_created_at(created_at); ix_ai_review_revisions_uuid(uuid); ix_ai_review_revisions_origin(origin)

## attachment_extractions

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| attachment_uuid | VARCHAR(36) | False | attachments.uuid ON DELETE CASCADE |
| source_sha256 | VARCHAR(64) | False |  |
| status | VARCHAR(20) | False |  |
| text | TEXT | False |  |
| pages | JSON | False |  |
| tool | VARCHAR(100) | False |  |
| error | TEXT | False |  |
| truncated | BOOLEAN | False |  |
| parser_version | VARCHAR(30) | False |  |
| job_token | VARCHAR(36) | False |  |
| created_at | VARCHAR(40) | False |  |
| updated_at | VARCHAR(40) | False |  |

索引：ix_attachment_extractions_status(status); ix_attachment_extractions_source_sha256(source_sha256)

## cases

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| case_type | VARCHAR(100) | False |  |
| background | TEXT | False |  |
| problem | TEXT | False |  |
| goal | TEXT | False |  |
| constraints | TEXT | False |  |
| analysis | TEXT | False |  |
| solution | TEXT | False |  |
| execution | TEXT | False |  |
| result | TEXT | False |  |
| outcome | VARCHAR(30) | False |  |
| lessons | TEXT | False |  |
| mistakes | TEXT | False |  |
| success_factors | TEXT | False |  |
| failure_factors | TEXT | False |  |
| reusable_method | TEXT | False |  |
| applicable_conditions | TEXT | False |  |
| not_applicable_conditions | TEXT | False |  |
| future_improvements | TEXT | False |  |
| start_date | DATE | True |  |
| end_date | DATE | True |  |
| is_reviewed | BOOLEAN | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_cases_times_used(times_used); ix_cases_case_type(case_type); ix_cases_outcome(outcome); ix_cases_domain_id(domain_id); ix_cases_start_date(start_date); ix_cases_is_reviewed(is_reviewed); ix_cases_topic_id(topic_id); ix_cases_uuid(uuid)

## embedding_chunks

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| id | INTEGER | False | 主键 |
| entry_uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| model | VARCHAR(200) | False |  |
| model_version | VARCHAR(200) | False |  |
| source_hash | VARCHAR(64) | False |  |
| entry_updated_at | VARCHAR(40) | False |  |
| chunk_index | INTEGER | False |  |
| field | VARCHAR(100) | False |  |
| attachment_uuid | VARCHAR(36) | True | attachments.uuid ON DELETE CASCADE |
| attachment_sha256 | VARCHAR(64) | True |  |
| page | INTEGER | True |  |
| source_label | VARCHAR(500) | False |  |
| start_offset | INTEGER | False |  |
| end_offset | INTEGER | False |  |
| evidence | TEXT | False |  |
| dimension | INTEGER | False |  |
| vector | BLOB | False |  |
| created_at | VARCHAR(40) | False |  |

索引：ix_embedding_chunks_entry_uuid(entry_uuid); ix_embedding_chunks_attachment_uuid(attachment_uuid); ix_embedding_chunks_source_hash(source_hash); ix_embedding_chunk_model_entry(model_version, entry_uuid); ix_embedding_chunks_model_version(model_version)

## knowledge

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| knowledge_type | VARCHAR(100) | False |  |
| difficulty | INTEGER | False |  |
| source_type | VARCHAR(100) | False |  |
| source_url | TEXT | False |  |
| source_title | VARCHAR(500) | False |  |
| author | VARCHAR(300) | False |  |
| published_date | DATE | True |  |
| learned_date | DATE | True |  |
| confidence | VARCHAR(30) | False |  |
| maturity_level | INTEGER | False |  |
| last_reviewed_at | VARCHAR(40) | True |  |
| next_review_date | DATE | True |  |
| review_count | INTEGER | False |  |
| needs_verification | BOOLEAN | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_knowledge_confidence(confidence); ix_knowledge_times_used(times_used); ix_knowledge_domain_id(domain_id); ix_knowledge_uuid(uuid); ix_knowledge_maturity_level(maturity_level); ix_knowledge_next_review_date(next_review_date); ix_knowledge_topic_id(topic_id); ix_knowledge_knowledge_type(knowledge_type)

## learning_records

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| learning_type | VARCHAR(100) | False |  |
| source | TEXT | False |  |
| start_date | DATE | True |  |
| finish_date | DATE | True |  |
| progress | INTEGER | False |  |
| notes | TEXT | False |  |
| key_points | TEXT | False |  |
| questions | TEXT | False |  |
| application | TEXT | False |  |
| review_date | DATE | True |  |
| maturity_level | INTEGER | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_learning_records_domain_id(domain_id); ix_learning_records_review_date(review_date); ix_learning_records_uuid(uuid); ix_learning_records_maturity_level(maturity_level); ix_learning_records_topic_id(topic_id); ix_learning_records_times_used(times_used); ix_learning_records_learning_type(learning_type)

## problems

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| occurred_date | DATE | True |  |
| impact | TEXT | False |  |
| possible_causes | TEXT | False |  |
| root_cause | TEXT | False |  |
| final_solution | TEXT | False |  |
| execution | TEXT | False |  |
| result | TEXT | False |  |
| is_resolved | BOOLEAN | False |  |
| recurrence_count | INTEGER | False |  |
| last_recurred_date | DATE | True |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_problems_occurred_date(occurred_date); ix_problems_times_used(times_used); ix_problems_domain_id(domain_id); ix_problems_is_resolved(is_resolved); ix_problems_uuid(uuid); ix_problems_topic_id(topic_id)

## solutions

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| problem_type | VARCHAR(200) | False |  |
| description | TEXT | False |  |
| steps | TEXT | False |  |
| requirements | TEXT | False |  |
| advantages | TEXT | False |  |
| disadvantages | TEXT | False |  |
| risks | TEXT | False |  |
| cost | VARCHAR(300) | False |  |
| difficulty | INTEGER | False |  |
| success_rate_note | TEXT | False |  |
| applicable_conditions | TEXT | False |  |
| not_applicable_conditions | TEXT | False |  |
| verification_status | VARCHAR(50) | False |  |
| confidence | VARCHAR(30) | False |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_solutions_confidence(confidence); ix_solutions_times_used(times_used); ix_solutions_domain_id(domain_id); ix_solutions_problem_type(problem_type); ix_solutions_verification_status(verification_status); ix_solutions_uuid(uuid); ix_solutions_topic_id(topic_id)

## sources

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| source_type | VARCHAR(100) | False |  |
| author | VARCHAR(300) | False |  |
| organization | VARCHAR(300) | False |  |
| url | TEXT | False |  |
| publication_date | DATE | True |  |
| access_date | DATE | True |  |
| description | TEXT | False |  |
| file_id | INTEGER | True | attachments.id ON DELETE SET NULL |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_sources_file_id(file_id); ix_sources_times_used(times_used); ix_sources_domain_id(domain_id); ix_sources_source_type(source_type); ix_sources_uuid(uuid); ix_sources_topic_id(topic_id)

## case_events

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| case_uuid | VARCHAR(36) | False | cases.uuid ON DELETE CASCADE |
| event_uuid | VARCHAR(36) | False | events.uuid ON DELETE CASCADE |

索引：ix_case_events_reverse(event_uuid, case_uuid)

## case_tasks

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| case_uuid | VARCHAR(36) | False | cases.uuid ON DELETE CASCADE |
| task_uuid | VARCHAR(36) | False | tasks.uuid ON DELETE CASCADE |

索引：ix_case_tasks_reverse(task_uuid, case_uuid)

## experiences

| 字段 | 类型 | 可空 | 关系 / 约束 |
|---|---|---|---|
| context | TEXT | False |  |
| source_case_uuid | VARCHAR(36) | True | cases.uuid ON DELETE SET NULL |
| confidence | VARCHAR(30) | False |  |
| times_verified | INTEGER | False |  |
| last_verified_at | VARCHAR(40) | True |  |
| id | INTEGER | False | 主键 |
| uuid | VARCHAR(36) | False | entries.uuid ON DELETE CASCADE |
| domain_id | INTEGER | True | domains.id ON DELETE SET NULL |
| topic_id | INTEGER | True | topics.id ON DELETE SET NULL |
| times_used | INTEGER | False |  |
| last_used_at | VARCHAR(40) | True |  |
| reuse_score | FLOAT | False |  |

索引：ix_experiences_uuid(uuid); ix_experiences_source_case_uuid(source_case_uuid); ix_experiences_confidence(confidence); ix_experiences_times_used(times_used); ix_experiences_domain_id(domain_id); ix_experiences_topic_id(topic_id)
