# Backend protocol v1

Windows pipe: `\\.\pipe\amazing-law-overlay-v2`. QLocalServer uses
`UserAccessOption`; no listening TCP port. On non-Windows Qt uses a local socket.
For isolated tests, override `AMAZING_OVERLAY_PIPE` for both client and backend.
`AMAZING_OVERLAY_DATA` overrides the user data directory.

UTF-8 JSON, one newline-terminated request and response per connection, 16 MiB maximum.

```json
{"v":1,"id":"request-1","method":"search","params":{"query":"задержание"}}
```

```json
{"v":1,"id":"request-1","result":[]}
```

Errors replace `result` with `error: {code, message}`. Clients validate protocol
version and matching request ID. Unknown methods/versions return errors.
There is no arbitrary Python execution endpoint.

| Method | Parameters | Result |
| --- | --- | --- |
| health | — | version, app_version, documents, data_dir, browsers_dir, import_warnings, shutdown, stopping |
| shutdown | — | stopping:true; cancel jobs, finish in-flight RPC/file writes and exit normally |
| documents | — | document metadata array |
| document | id | document tree and sanitized HTML |
| search | query, optional doc_ids, limit | ranked section hits including snippet/post_id/heading |
| sources | — | source metadata array |
| source_add | url | id; URL normalized by thread ID |
| source_update | id, optional enabled/delete | true |
| sources_import | text | per-line IDs/errors; imports links only |
| preview | url, visible=false | job_id; does not publish |
| preview_save | job_id, optional replaces | publish completed preview, optionally replace old source link |
| update | ids=null, visible=false | job_id; null selects all enabled sources |
| export | directory | job_id; writes explicit TXT/JSON/CSV export |
| versions | id | digest and fetched-time history |
| settings | — | effective settings, has_api_key, env_overrides; no key |
| settings_save | settings subset and optional ai_api_key | effective public settings |
| state_get | key, default | client preference value |
| state_set | key, value | true |
| ai | question, history=[], doc_ids=null | job_id |
| ai_test | необязательные поля настроек API (без сохранения) | job_id |
| browser_check | optional visible=false | job_id; launches Chromium on a local test page, no forum request |
| job_status | id | state, kind, progress, result/error |
| job_cancel | id | cooperative cancellation request |

Jobs transition `running → done/error/cancelled`. Poll at about 350 ms.
Progress is the latest snapshot, not an append-only event stream. AI progress
includes the accumulated answer and source metadata. No API keys are returned.
The backend retains a bounded number of completed jobs and at most 8 active jobs.
Cancellation is cooperative; a current network read is bounded by its timeout.

`health.shutdown: true` advertises graceful stop support (2.3+). `shutdown` is
available only through the same current-user local pipe. After acknowledgment,
new operations are rejected; `health` remains reachable with `stopping:true`
until accepted requests and cancelled jobs finish. The launcher waits for the
pipe to close, without terminating processes. Closing the launcher does not stop
the backend. Older services must be updated before using this command.

Document tree v1:

```json
{
  "schema_version":1,"id":"1034845","url":"https://forum.amazing-online.com/threads/trudovoj-kodeks.1034845/",
  "title":"Трудовой кодекс","structured":true,"fetched_at":"2026-10-30T12:00:00+00:00","pages":1,
  "posts":[{"post_id":"123","author":"author","blocks":[
    {"tag":"p","children":[{"tag":"text","text":"Статья 1. Текст"}]},
    {"tag":"table","children":[{"tag":"tr","children":[
      {"tag":"th","rowspan":1,"colspan":2,"children":[{"tag":"text","text":"Заголовок"}]}
    ]}]}
  ]}]
}
```

Allowed markup is defined in `documents.py`. Scripts, event attributes and unsafe
URL schemes are removed. Native rendering consumes the tree, external rendering
consumes the generated HTML; neither executes forum scripts.


## TXT-блокнот (2.2, протокол v1)

`health.notes: true` означает поддержку блокнота. `settings`/`settings_save`
дополнены `notes_dir` (пусто — стандартная Notes, иначе абсолютный путь) и
`notes_hotkey` (Ctrl+N). Смена папки не перемещает файлы.

| Метод | Параметры | Результат |
|---|---|---|
| `notes_info` | — | `root`, `edit_limit` |
| `notes_list`, `notes_search` | `query?` | `root`, `entries[{path,folder,bytes?}]` |
| `notes_read` | `path` | `path,text,revision,encoding,newline,readonly,bytes` |
| `notes_create` | `path,folder?,text?` | Созданный файл или папка; существующее не заменяется |
| `notes_save` | `path,text,revision,root?` | `conflict:false` и прочитанная сохранённая версия; либо `conflict:true,current` |
| `notes_move` | `path,destination,root?` | `path,previous` |
| `notes_delete` | `path,root?` | `trash_id` |
| `notes_trash` | — | `[{id,path}]` |
| `notes_restore` | `id,destination?,root?` | `path` |

Пути относительные, разделитель `/`, только файлы TXT. `root` — полученная от
сервера папка; клиенты передают её при записи и изменениях, чтобы буфер от прежней
папки не сохранился в новую. Сервер отклоняет несовпадение. Ревизия — SHA256 байтов
файла; конфликт не изменяет файл. Ошибки передаются обычным error протокола v1.
Содержимое заметок не входит в базу законов, diagnostics или автоматический AI-контекст.
