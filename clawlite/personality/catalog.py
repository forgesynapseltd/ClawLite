"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

personality/catalog.py — Catálogo multilingüe de mensajes deterministas (victoria #6, fase 1;
cobertura ampliada 11 jul)

DATOS revisados por humano, no lógica (Regla 2): aquí viven los mensajes que el
código devuelve SIN pasar por el modelo (mensajes de borde de research,
confirmaciones de calendario, comando no reconocido), en 8 idiomas
(es/en/de/fr/zh/ru/ja/tl).

El idioma NUNCA lo juzga el modelo (Regla 3): lo fija la red determinista
existente (personality/language.py — py3langid + cruce con planner) vía
ContextVar del turno, o un estado persistido (p.ej. event_draft["lang"]) cuando
la respuesta ocurre fuera del turno que detectó el idioma (botones).

Reserva: INGLÉS (decisión de producto 6 jul, adenda §6 — Nivel 1 viral/global).
Añadir un idioma = añadir entradas de datos; cero cambios de lógica.
Los strings de es/en/de/fr/zh fueron revisados y aprobados por Fernando (6 jul)
— no se editan sin nueva revisión humana. ru/ja/tl añadidos 11 jul.
"""

from clawlite.personality.language import get_target_language

_FALLBACK_LANG = "en"

_CATALOG: dict[str, dict[str, str]] = {
    # ── Mensajes de borde de research (esquivan la síntesis por diseño) ──────
    "research_no_sources": {
        "es": "No encontré fuentes relevantes para esta consulta.",
        "en": "I couldn't find relevant sources for this query.",
        "de": "Ich habe keine relevanten Quellen zu dieser Anfrage gefunden.",
        "fr": "Je n'ai pas trouvé de sources pertinentes pour cette requête.",
        "zh": "我没有找到与此查询相关的可靠来源。",
        "ru": "Мне не удалось найти релевантные источники по этому запросу.",
        "ja": "このクエリに関連する情報源が見つかりませんでした。",
        "tl": "Hindi ako nakahanap ng mga kaugnay na sanggunian para sa query na ito.",
    },
    "research_sources_inaccessible": {
        "es": "Encontré fuentes pero no pude acceder a su contenido.",
        "en": "I found sources but couldn't access their content.",
        "de": "Ich habe Quellen gefunden, konnte aber nicht auf ihren Inhalt zugreifen.",
        "fr": "J'ai trouvé des sources mais je n'ai pas pu accéder à leur contenu.",
        "zh": "我找到了一些来源，但无法访问其内容。",
        "ru": "Я нашёл источники, но не смог получить доступ к их содержимому.",
        "ja": "情報源は見つかりましたが、内容にアクセスできませんでした。",
        "tl": "Nakahanap ako ng mga sanggunian ngunit hindi ako nakapag-access sa kanilang nilalaman.",
    },
    "research_insufficient_verification": {
        "es": (
            "No encontré información suficientemente confiable para responder "
            "esto: las fuentes disponibles no se corroboran entre sí. Prefiero "
            "no darte una respuesta que podría ser inexacta."
        ),
        "en": (
            "I couldn't find sufficiently reliable information to answer this: "
            "the available sources don't corroborate each other. I'd rather not "
            "give you an answer that could be inaccurate."
        ),
        "de": (
            "Ich habe keine ausreichend verlässlichen Informationen gefunden: "
            "die verfügbaren Quellen bestätigen einander nicht. Lieber gebe ich "
            "dir keine Antwort, die ungenau sein könnte."
        ),
        "fr": (
            "Je n'ai pas trouvé d'informations suffisamment fiables : les "
            "sources disponibles ne se corroborent pas entre elles. Je préfère "
            "ne pas te donner une réponse qui pourrait être inexacte."
        ),
        "zh": "我没有找到足够可靠的信息来回答这个问题：现有来源之间无法相互印证。我宁可不给出可能不准确的回答。",
        "ru": (
            "Мне не удалось найти достаточно надёжную информацию для ответа: "
            "доступные источники не подтверждают друг друга. Я предпочитаю не "
            "давать ответ, который может быть неточным."
        ),
        "ja": (
            "これに答えるための十分に信頼できる情報が見つかりませんでした。利用可能な情報源が"
            "互いに裏付けられていません。不正確な可能性がある回答はしたくありません。"
        ),
        "tl": (
            "Hindi ako nakahanap ng sapat na mapagkakatiwalaang impormasyon para sagutin ito: "
            "ang mga available na sanggunian ay hindi nagkakatugma sa isa't isa. Mas gugustuhin "
            "kong hindi magbigay ng sagot na maaaring hindi tumpak."
        ),
    },
    "research_synthesis_failed": {
        "es": (
            "Ocurrió un error local al intentar procesar la información. No "
            "pude sintetizar una respuesta verificada."
        ),
        "en": (
            "A local error occurred while processing the information. I "
            "couldn't synthesize a verified answer."
        ),
        "de": (
            "Beim Verarbeiten der Informationen ist ein lokaler Fehler "
            "aufgetreten. Ich konnte keine verifizierte Antwort erstellen."
        ),
        "fr": (
            "Une erreur locale s'est produite lors du traitement des "
            "informations. Je n'ai pas pu synthétiser une réponse vérifiée."
        ),
        "zh": "处理信息时发生了本地错误。我无法生成经过验证的回答。",
        "ru": (
            "Произошла локальная ошибка при обработке информации. Мне не "
            "удалось сформировать проверенный ответ."
        ),
        "ja": "情報の処理中にローカルエラーが発生しました。検証済みの回答を作成できませんでした。",
        "tl": (
            "May naganap na lokal na error habang pinoproseso ang impormasyon. "
            "Hindi ako nakagawa ng beripikadong sagot."
        ),
    },
    # ── Confirmaciones de calendario (idioma persistido en event_draft) ──────
    "event_cancelled": {
        "es": "❌ Evento cancelado.",
        "en": "❌ Event cancelled.",
        "de": "❌ Termin abgesagt.",
        "fr": "❌ Événement annulé.",
        "zh": "❌ 已取消该日程。",
        "ru": "❌ Событие отменено.",
        "ja": "❌ 予定をキャンセルしました。",
        "tl": "❌ Nakansela ang kaganapan.",
    },
    "event_blocked": {
        "es": "🚫 No creé el evento: la política de seguridad lo bloqueó ({reason}).",
        "en": "🚫 I didn't create the event: the security policy blocked it ({reason}).",
        "de": "🚫 Ich habe den Termin nicht erstellt: die Sicherheitsrichtlinie hat ihn blockiert ({reason}).",
        "fr": "🚫 Je n'ai pas créé l'événement : la politique de sécurité l'a bloqué ({reason}).",
        "zh": "🚫 我没有创建该日程：安全策略阻止了此操作（{reason}）。",
        "ru": "🚫 Я не создал событие: политика безопасности заблокировала это ({reason}).",
        "ja": "🚫 予定を作成しませんでした：セキュリティポリシーによりブロックされました（{reason}）。",
        "tl": "🚫 Hindi ko nagawa ang kaganapan: hinarang ito ng patakaran sa seguridad ({reason}).",
    },
    "event_create_failed": {
        "es": "❌ No pude crear el evento.",
        "en": "❌ I couldn't create the event.",
        "de": "❌ Ich konnte den Termin nicht erstellen.",
        "fr": "❌ Je n'ai pas pu créer l'événement.",
        "zh": "❌ 我无法创建该日程。",
        "ru": "❌ Не удалось создать событие.",
        "ja": "❌ 予定を作成できませんでした。",
        "tl": "❌ Hindi ko nagawa ang kaganapan.",
    },
    "toast_confirmed": {
        "es": "Confirmado ✅",
        "en": "Confirmed ✅",
        "de": "Bestätigt ✅",
        "fr": "Confirmé ✅",
        "zh": "已确认 ✅",
        "ru": "Подтверждено ✅",
        "ja": "確認しました ✅",
        "tl": "Nakumpirma ✅",
    },
    "toast_cancelled": {
        "es": "Cancelado ❌",
        "en": "Cancelled ❌",
        "de": "Abgebrochen ❌",
        "fr": "Annulé ❌",
        "zh": "已取消 ❌",
        "ru": "Отменено ❌",
        "ja": "キャンセルしました ❌",
        "tl": "Nakansela ❌",
    },
    # ── Mensajes de /job, /jobs, /job_status, /cancel, /job_files (fase 2a,
    #    8 jul). Idioma: fuente ③ (last_turn_language de sesión) — los slash
    #    commands nunca pasan por el planner. Los códigos de estado (`queued`,
    #    `completed`…) son DATOS del store y no se traducen. ─────────────────
    "jobs_not_initialized": {
        "es": "❌ Sistema de jobs no inicializado.",
        "en": "❌ Job system not initialized.",
        "de": "❌ Job-System nicht initialisiert.",
        "fr": "❌ Système de tâches non initialisé.",
        "zh": "❌ 任务系统尚未初始化。",
        "ru": "❌ Система задач не инициализирована.",
        "ja": "❌ ジョブシステムが初期化されていません。",
        "tl": "❌ Hindi pa na-initialize ang sistema ng job.",
    },
    "job_id_not_numeric": {
        "es": "El id debe ser un número.",
        "en": "The id must be a number.",
        "de": "Die ID muss eine Zahl sein.",
        "fr": "L'identifiant doit être un nombre.",
        "zh": "ID 必须是数字。",
        "ru": "Идентификатор должен быть числом.",
        "ja": "IDは数字でなければなりません。",
        "tl": "Dapat ay numero ang id.",
    },
    # ── Notificaciones del JobRunner (jobs/runner.py) — corren en background,
    #    fuera de cualquier turno; el idioma se resuelve vía _display_lang
    #    (misma cadena de idioma persistente que el resto del proyecto,
    #    hallazgo "notificaciones async en español fijo", 10 jul). ──────────
    "job_unknown_type": {
        "es": "Tipo de job desconocido: '{job_type}'",
        "en": "Unknown job type: '{job_type}'",
        "de": "Unbekannter Job-Typ: '{job_type}'",
        "fr": "Type de tâche inconnu : '{job_type}'",
        "zh": "未知的任务类型：'{job_type}'",
        "ru": "Неизвестный тип задачи: '{job_type}'",
        "ja": "不明なジョブタイプです: '{job_type}'",
        "tl": "Hindi kilalang uri ng job: '{job_type}'",
    },
    "job_timeout": {
        "es": "El job superó el tiempo límite de {seconds}s",
        "en": "The job exceeded the {seconds}s time limit",
        "de": "Der Job hat das Zeitlimit von {seconds}s überschritten",
        "fr": "La tâche a dépassé la limite de {seconds}s",
        "zh": "任务超过了 {seconds} 秒的时间限制",
        "ru": "Задача превысила лимит времени {seconds}с",
        "ja": "ジョブが制限時間の{seconds}秒を超えました",
        "tl": "Nalagpasan ng job ang time limit na {seconds}s",
    },
    "job_not_found": {
        "es": "No encontré el job #{job_id}.",
        "en": "I couldn't find job #{job_id}.",
        "de": "Ich habe Job #{job_id} nicht gefunden.",
        "fr": "Je n'ai pas trouvé la tâche #{job_id}.",
        "zh": "我没有找到任务 #{job_id}。",
        "ru": "Не удалось найти задачу #{job_id}.",
        "ja": "ジョブ #{job_id} が見つかりませんでした。",
        "tl": "Hindi ko mahanap ang job #{job_id}.",
    },
    "job_usage": {
        "es": (
            "Uso: `/job <descripción de la tarea>`\n\n"
            "Ejemplos:\n"
            "• `/job investiga el mercado de drones de carga en LATAM`\n"
            "• `/job construye un dashboard de ventas en Python`\n"
            "• `/job genera el calendario de contenido del mes`"
        ),
        "en": (
            "Usage: `/job <task description>`\n\n"
            "Examples:\n"
            "• `/job research the cargo drone market in LATAM`\n"
            "• `/job build a sales dashboard in Python`\n"
            "• `/job generate this month's content calendar`"
        ),
        "de": (
            "Verwendung: `/job <Aufgabenbeschreibung>`\n\n"
            "Beispiele:\n"
            "• `/job recherchiere den Frachtdrohnen-Markt in LATAM`\n"
            "• `/job baue ein Verkaufs-Dashboard in Python`\n"
            "• `/job erstelle den Content-Kalender des Monats`"
        ),
        "fr": (
            "Utilisation : `/job <description de la tâche>`\n\n"
            "Exemples :\n"
            "• `/job étudie le marché des drones cargo en LATAM`\n"
            "• `/job construis un tableau de bord des ventes en Python`\n"
            "• `/job génère le calendrier de contenu du mois`"
        ),
        "zh": (
            "用法：`/job <任务描述>`\n\n"
            "示例：\n"
            "• `/job 调查拉美货运无人机市场`\n"
            "• `/job 用 Python 构建销售仪表盘`\n"
            "• `/job 生成本月内容日历`"
        ),
        "ru": (
            "Использование: `/job <описание задачи>`\n\n"
            "Примеры:\n"
            "• `/job исследуй рынок грузовых дронов в Латинской Америке`\n"
            "• `/job создай дашборд продаж на Python`\n"
            "• `/job сгенерируй контент-календарь на месяц`"
        ),
        "ja": (
            "使い方：`/job <タスクの説明>`\n\n"
            "例：\n"
            "• `/job ラテンアメリカの貨物ドローン市場を調査して`\n"
            "• `/job Pythonで売上ダッシュボードを作成して`\n"
            "• `/job 今月のコンテンツカレンダーを生成して`"
        ),
        "tl": (
            "Paggamit: `/job <paglalarawan ng gawain>`\n\n"
            "Mga halimbawa:\n"
            "• `/job saliksikin ang merkado ng cargo drone sa LATAM`\n"
            "• `/job gumawa ng sales dashboard sa Python`\n"
            "• `/job bumuo ng content calendar ng buwan`"
        ),
    },
    "job_status_usage": {
        "es": "Uso: `/job_status <id>`",
        "en": "Usage: `/job_status <id>`",
        "de": "Verwendung: `/job_status <id>`",
        "fr": "Utilisation : `/job_status <id>`",
        "zh": "用法：`/job_status <id>`",
        "ru": "Использование: `/job_status <id>`",
        "ja": "使い方：`/job_status <id>`",
        "tl": "Paggamit: `/job_status <id>`",
    },
    "job_cancel_usage": {
        "es": "Uso: `/cancel <id>`",
        "en": "Usage: `/cancel <id>`",
        "de": "Verwendung: `/cancel <id>`",
        "fr": "Utilisation : `/cancel <id>`",
        "zh": "用法：`/cancel <id>`",
        "ru": "Использование: `/cancel <id>`",
        "ja": "使い方：`/cancel <id>`",
        "tl": "Paggamit: `/cancel <id>`",
    },
    "job_files_usage": {
        "es": "Uso: `/job_files <id>`",
        "en": "Usage: `/job_files <id>`",
        "de": "Verwendung: `/job_files <id>`",
        "fr": "Utilisation : `/job_files <id>`",
        "zh": "用法：`/job_files <id>`",
        "ru": "Использование: `/job_files <id>`",
        "ja": "使い方：`/job_files <id>`",
        "tl": "Paggamit: `/job_files <id>`",
    },
    "jobs_empty": {
        "es": "No tienes jobs todavía.\n\nCrea uno con `/job <descripción>`.",
        "en": "You don't have any jobs yet.\n\nCreate one with `/job <description>`.",
        "de": "Du hast noch keine Jobs.\n\nErstelle einen mit `/job <Beschreibung>`.",
        "fr": "Tu n'as pas encore de tâches.\n\nCrée-en une avec `/job <description>`.",
        "zh": "你还没有任务。\n\n用 `/job <描述>` 创建一个。",
        "ru": "У тебя пока нет задач.\n\nСоздай одну с помощью `/job <описание>`.",
        "ja": "まだジョブがありません。\n\n`/job <説明>` で作成してください。",
        "tl": "Wala ka pang mga job.\n\nGumawa ng isa gamit ang `/job <paglalarawan>`.",
    },
    "job_cancel_not_queued": {
        "es": (
            "No puedo cancelar el job #{job_id}: está en estado `{status}`.\n"
            "Solo se cancelan los jobs que aún no han empezado (`queued`)."
        ),
        "en": (
            "I can't cancel job #{job_id}: it's in `{status}` state.\n"
            "Only jobs that haven't started yet (`queued`) can be cancelled."
        ),
        "de": (
            "Ich kann Job #{job_id} nicht abbrechen: er ist im Zustand `{status}`.\n"
            "Nur Jobs, die noch nicht gestartet sind (`queued`), lassen sich abbrechen."
        ),
        "fr": (
            "Je ne peux pas annuler la tâche #{job_id} : elle est à l'état `{status}`.\n"
            "Seules les tâches pas encore démarrées (`queued`) peuvent être annulées."
        ),
        "zh": "无法取消任务 #{job_id}：其状态为 `{status}`。\n只能取消尚未开始（`queued`）的任务。",
        "ru": (
            "Не могу отменить задачу #{job_id}: она в состоянии `{status}`.\n"
            "Отменить можно только задачи, которые ещё не начались (`queued`)."
        ),
        "ja": (
            "ジョブ #{job_id} をキャンセルできません：状態は `{status}` です。\n"
            "まだ開始していないジョブ（`queued`）のみキャンセルできます。"
        ),
        "tl": (
            "Hindi ko makansela ang job #{job_id}: nasa estadong `{status}` ito.\n"
            "Maaari lang ikansela ang mga job na hindi pa nagsisimula (`queued`)."
        ),
    },
    "job_cancelled": {
        "es": "🚫 Job #{job_id} cancelado.",
        "en": "🚫 Job #{job_id} cancelled.",
        "de": "🚫 Job #{job_id} abgebrochen.",
        "fr": "🚫 Tâche #{job_id} annulée.",
        "zh": "🚫 任务 #{job_id} 已取消。",
        "ru": "🚫 Задача #{job_id} отменена.",
        "ja": "🚫 ジョブ #{job_id} をキャンセルしました。",
        "tl": "🚫 Nakansela ang job #{job_id}.",
    },
    "sovereignty_disclaimer_prompt": {
        "es": "🌐 Esta comparación entre varias opciones razona mejor con un modelo en la nube — tu configuración actual es 100% local.\n\n¿Quieres que use la nube para las comparaciones de esta sesión?",
        "en": "🌐 This comparison between several options reasons better with a cloud model — your current setup is 100% local.\n\nDo you want me to use the cloud for comparisons in this session?",
        "de": "🌐 Dieser Vergleich zwischen mehreren Optionen funktioniert besser mit einem Cloud-Modell — deine aktuelle Einstellung ist 100% lokal.\n\nSoll ich die Cloud für Vergleiche in dieser Sitzung verwenden?",
        "fr": "🌐 Cette comparaison entre plusieurs options raisonne mieux avec un modèle en ligne — ta configuration actuelle est 100% locale.\n\nVeux-tu que j'utilise le cloud pour les comparaisons de cette session ?",
        "zh": "🌐 在多个选项之间进行比较时，云端模型的推理效果更好——你目前的配置是100%本地运行。\n\n你想让我在本次会话的比较中使用云端吗？",
        "ru": "🌐 Это сравнение между несколькими вариантами лучше работает с облачной моделью — твоя текущая настройка на 100% локальная.\n\nХочешь, чтобы я использовал облако для сравнений в этой сессии?",
        "ja": "🌐 複数の選択肢を比較する場合、クラウドモデルの方がより良く推論できます——現在の設定は100%ローカルです。\n\nこのセッションの比較にクラウドを使用しますか？",
        "tl": "🌐 Ang paghahambing na ito sa pagitan ng ilang opsyon ay mas mahusay gamit ang cloud model — 100% lokal ang kasalukuyan mong setup.\n\nGusto mo bang gamitin ko ang cloud para sa mga paghahambing sa session na ito?",
    },
    "cloud_override_reminder": {
        "es": "☁️ _Recordatorio: sigues usando el modelo en la nube para comparaciones en esta sesión._",
        "en": "☁️ _Reminder: you're still using the cloud model for comparisons in this session._",
        "de": "☁️ _Erinnerung: Du verwendest für Vergleiche in dieser Sitzung weiterhin das Cloud-Modell._",
        "fr": "☁️ _Rappel : tu utilises toujours le modèle en ligne pour les comparaisons de cette session._",
        "zh": "☁️ _提醒：本次会话中你仍在使用云端模型进行比较。_",
        "ru": "☁️ _Напоминание: ты всё ещё используешь облачную модель для сравнений в этой сессии._",
        "ja": "☁️ _リマインダー：このセッションの比較には引き続きクラウドモデルを使用しています。_",
        "tl": "☁️ _Paalala: ginagamit mo pa rin ang cloud model para sa mga paghahambing sa session na ito._",
    },
    "cloud_yes_button": {
        "es": "☁️ Sí, usar nube esta sesión", "en": "☁️ Yes, use the cloud this session",
        "de": "☁️ Ja, für diese Sitzung die Cloud nutzen", "fr": "☁️ Oui, utiliser le cloud pour cette session",
        "zh": "☁️ 是的，本次会话使用云端",
        "ru": "☁️ Да, использовать облако в этой сессии", "ja": "☁️ はい、このセッションはクラウドを使用",
        "tl": "☁️ Oo, gamitin ang cloud sa session na ito",
    },
    "cloud_no_button": {
        "es": "🏠 No, sigo en local", "en": "🏠 No, staying local",
        "de": "🏠 Nein, ich bleibe lokal", "fr": "🏠 Non, je reste en local",
        "zh": "🏠 不，继续使用本地",
        "ru": "🏠 Нет, остаюсь локально", "ja": "🏠 いいえ、ローカルのまま",
        "tl": "🏠 Hindi, mananatili sa lokal",
    },
    "job_confirm_short": {
        "es": "⚠️ Esta petición es muy corta: \"{request}\" (se clasificaría como *{job_type}*). ¿Seguro que quieres crear este job?",
        "en": "⚠️ This request is very short: \"{request}\" (would be classified as *{job_type}*). Are you sure you want to create this job?",
        "de": "⚠️ Diese Anfrage ist sehr kurz: \"{request}\" (würde als *{job_type}* eingestuft). Bist du sicher, dass du diesen Job erstellen willst?",
        "fr": "⚠️ Cette demande est très courte : « {request} » (serait classée comme *{job_type}*). Es-tu sûr de vouloir créer cette tâche ?",
        "zh": "⚠️ 这个请求很短：\"{request}\"（会被分类为 *{job_type}*）。你确定要创建这个任务吗？",
        "ru": "⚠️ Этот запрос очень короткий: \"{request}\" (будет классифицирован как *{job_type}*). Ты уверен, что хочешь создать эту задачу?",
        "ja": "⚠️ このリクエストはとても短いです：「{request}」（*{job_type}* として分類されます）。本当にこのジョブを作成しますか？",
        "tl": "⚠️ Napakaikli ng kahilingang ito: \"{request}\" (ikaklasipika bilang *{job_type}*). Sigurado ka bang gusto mong gawin ang job na ito?",
    },
    "job_draft_cancelled": {
        "es": "❌ No creé el job.",
        "en": "❌ I didn't create the job.",
        "de": "❌ Ich habe den Job nicht erstellt.",
        "fr": "❌ Je n'ai pas créé la tâche.",
        "zh": "❌ 我没有创建该任务。",
        "ru": "❌ Я не создал задачу.",
        "ja": "❌ ジョブを作成しませんでした。",
        "tl": "❌ Hindi ko nagawa ang job.",
    },
    "job_cancel_failed": {
        "es": "No pude cancelar el job #{job_id} (puede que ya haya empezado).",
        "en": "I couldn't cancel job #{job_id} (it may have already started).",
        "de": "Ich konnte Job #{job_id} nicht abbrechen (er ist womöglich schon gestartet).",
        "fr": "Je n'ai pas pu annuler la tâche #{job_id} (elle a peut-être déjà commencé).",
        "zh": "我无法取消任务 #{job_id}（它可能已经开始）。",
        "ru": "Не удалось отменить задачу #{job_id} (возможно, она уже началась).",
        "ja": "ジョブ #{job_id} をキャンセルできませんでした（すでに開始している可能性があります）。",
        "tl": "Hindi ko makansela ang job #{job_id} (maaaring nagsimula na ito).",
    },
    "job_files_not_completed": {
        "es": "El job #{job_id} está en estado `{status}`. Solo puedo enviar archivos de jobs completados.",
        "en": "Job #{job_id} is in `{status}` state. I can only send files from completed jobs.",
        "de": "Job #{job_id} ist im Zustand `{status}`. Ich kann nur Dateien abgeschlossener Jobs senden.",
        "fr": "La tâche #{job_id} est à l'état `{status}`. Je ne peux envoyer que les fichiers des tâches terminées.",
        "zh": "任务 #{job_id} 的状态为 `{status}`。我只能发送已完成任务的文件。",
        "ru": "Задача #{job_id} в состоянии `{status}`. Я могу отправлять файлы только из завершённых задач.",
        "ja": "ジョブ #{job_id} は `{status}` 状態です。完了したジョブのファイルのみ送信できます。",
        "tl": "Nasa estadong `{status}` ang job #{job_id}. Mga file lang mula sa natapos na mga job ang maipapadala ko.",
    },
    "job_files_wrong_type": {
        "es": (
            "El job #{job_id} es de tipo `{job_type}`, no tiene archivos descargables.\n"
            "Usa `/job_status {job_id}` para ver el resultado."
        ),
        "en": (
            "Job #{job_id} is of type `{job_type}` and has no downloadable files.\n"
            "Use `/job_status {job_id}` to see the result."
        ),
        "de": (
            "Job #{job_id} ist vom Typ `{job_type}` und hat keine herunterladbaren Dateien.\n"
            "Nutze `/job_status {job_id}`, um das Ergebnis zu sehen."
        ),
        "fr": (
            "La tâche #{job_id} est de type `{job_type}` et n'a pas de fichiers téléchargeables.\n"
            "Utilise `/job_status {job_id}` pour voir le résultat."
        ),
        "zh": "任务 #{job_id} 的类型是 `{job_type}`，没有可下载的文件。\n用 `/job_status {job_id}` 查看结果。",
        "ru": (
            "Задача #{job_id} имеет тип `{job_type}`, у неё нет файлов для скачивания.\n"
            "Используй `/job_status {job_id}`, чтобы увидеть результат."
        ),
        "ja": (
            "ジョブ #{job_id} はタイプ `{job_type}` で、ダウンロード可能なファイルはありません。\n"
            "`/job_status {job_id}` で結果を確認してください。"
        ),
        "tl": (
            "Ang job #{job_id} ay uri ng `{job_type}`, walang madadownload na file.\n"
            "Gamitin ang `/job_status {job_id}` para makita ang resulta."
        ),
    },
    "job_files_none": {
        "es": (
            "El job #{job_id} no tiene archivos asociados (puede que se haya "
            "completado antes de implementar este comando)."
        ),
        "en": "Job #{job_id} has no associated files (it may have completed before this command existed).",
        "de": (
            "Job #{job_id} hat keine zugehörigen Dateien (er wurde womöglich "
            "abgeschlossen, bevor es diesen Befehl gab)."
        ),
        "fr": (
            "La tâche #{job_id} n'a pas de fichiers associés (elle s'est "
            "peut-être terminée avant l'existence de cette commande)."
        ),
        "zh": "任务 #{job_id} 没有关联文件（可能在此命令实现之前就已完成）。",
        "ru": (
            "У задачи #{job_id} нет связанных файлов (возможно, она завершилась "
            "до появления этой команды)."
        ),
        "ja": "ジョブ #{job_id} に関連するファイルはありません（このコマンドが存在する前に完了した可能性があります）。",
        "tl": "Walang kaugnay na file ang job #{job_id} (posibleng natapos ito bago magkaroon ng command na ito).",
    },
    # ── Mensajes de _handle_email_flow (fase 2 catálogo, 9 jul). Idioma:
    #    fuente ③ (last_turn_language de sesión) — este flujo corre en
    #    _state_interceptors, ANTES de que el planner clasifique el turno
    #    actual, así que no hay ContextVar de idioma disponible aquí. ───────
    "email_send_blocked": {
        "es": "🚫 No envié el correo: la política de seguridad lo bloqueó ({reason}).",
        "en": "🚫 I didn't send the email: the security policy blocked it ({reason}).",
        "de": "🚫 Ich habe die E-Mail nicht gesendet: die Sicherheitsrichtlinie hat sie blockiert ({reason}).",
        "fr": "🚫 Je n'ai pas envoyé l'e-mail : la politique de sécurité l'a bloqué ({reason}).",
        "zh": "🚫 我没有发送邮件：安全策略阻止了此操作（{reason}）。",
        "ru": "🚫 Я не отправил письмо: политика безопасности заблокировала это ({reason}).",
        "ja": "🚫 メールを送信しませんでした：セキュリティポリシーによりブロックされました（{reason}）。",
        "tl": "🚫 Hindi ko naipadala ang email: hinarang ito ng patakaran sa seguridad ({reason}).",
    },
    "email_sent": {
        "es": "✅ Correo enviado.", "en": "✅ Email sent.",
        "de": "✅ E-Mail gesendet.", "fr": "✅ E-mail envoyé.",
        "zh": "✅ 邮件已发送。",
        "ru": "✅ Письмо отправлено.", "ja": "✅ メールを送信しました。",
        "tl": "✅ Naipadala ang email.",
    },
    "email_send_failed": {
        "es": "❌ No pude enviar el correo. Intenta de nuevo.",
        "en": "❌ I couldn't send the email. Try again.",
        "de": "❌ Ich konnte die E-Mail nicht senden. Versuch es noch einmal.",
        "fr": "❌ Je n'ai pas pu envoyer l'e-mail. Réessaie.",
        "zh": "❌ 邮件发送失败，请重试。",
        "ru": "❌ Не удалось отправить письмо. Попробуй ещё раз.",
        "ja": "❌ メールを送信できませんでした。もう一度お試しください。",
        "tl": "❌ Hindi ko naipadala ang email. Subukan ulit.",
    },
    "email_draft_discarded": {
        "es": "❌ Borrador descartado.", "en": "❌ Draft discarded.",
        "de": "❌ Entwurf verworfen.", "fr": "❌ Brouillon annulé.",
        "zh": "❌ 草稿已丢弃。",
        "ru": "❌ Черновик удалён.", "ja": "❌ 下書きを破棄しました。",
        "tl": "❌ Na-discard ang draft.",
    },
    "email_draft_updated": {
        "es": "📝 Borrador actualizado:\n\n{draft}\n\n¿Lo envío? Responde *sí* para confirmar o *no* para cancelar.",
        "en": "📝 Draft updated:\n\n{draft}\n\nShall I send it? Reply *yes* to confirm or *no* to cancel.",
        "de": "📝 Entwurf aktualisiert:\n\n{draft}\n\nSoll ich sie senden? Antworte mit *ja* zum Bestätigen oder *nein* zum Abbrechen.",
        "fr": "📝 Brouillon mis à jour :\n\n{draft}\n\nJe l'envoie ? Réponds *oui* pour confirmer ou *non* pour annuler.",
        "zh": "📝 草稿已更新：\n\n{draft}\n\n要发送吗？回复 *是* 确认，或 *否* 取消。",
        "ru": "📝 Черновик обновлён:\n\n{draft}\n\nОтправить? Ответь *да* для подтверждения или *нет* для отмены.",
        "ja": "📝 下書きを更新しました：\n\n{draft}\n\n送信しますか？*はい*で確認、*いいえ*でキャンセルしてください。",
        "tl": "📝 Na-update ang draft:\n\n{draft}\n\nIpapadala ko ba ito? Sagutin ang *oo* para kumpirmahin o *hindi* para kanselahin.",
    },
    "email_use_inbox_first": {
        "es": "Primero usa /gmail inbox para ver tus correos.",
        "en": "First use /gmail inbox to see your emails.",
        "de": "Verwende zuerst /gmail inbox, um deine E-Mails zu sehen.",
        "fr": "Utilise d'abord /gmail inbox pour voir tes e-mails.",
        "zh": "请先使用 /gmail inbox 查看你的邮件。",
        "ru": "Сначала используй /gmail inbox, чтобы увидеть свои письма.",
        "ja": "まず /gmail inbox でメールを確認してください。",
        "tl": "Gamitin muna ang /gmail inbox para makita ang iyong mga email.",
    },
    "email_inbox_empty_cache": {
        "es": "No hay correos en caché. Usa /gmail inbox primero.",
        "en": "No emails cached yet. Use /gmail inbox first.",
        "de": "Keine E-Mails zwischengespeichert. Verwende zuerst /gmail inbox.",
        "fr": "Aucun e-mail en cache. Utilise d'abord /gmail inbox.",
        "zh": "还没有缓存的邮件。请先使用 /gmail inbox。",
        "ru": "Пока нет писем в кэше. Сначала используй /gmail inbox.",
        "ja": "まだキャッシュされたメールがありません。先に /gmail inbox を使用してください。",
        "tl": "Wala pang naka-cache na email. Gamitin muna ang /gmail inbox.",
    },
    "email_number_missing": {
        "es": "Indica el número del correo que quieres responder. Ejemplo: *responde al correo 2*",
        "en": "Tell me the number of the email you want to reply to. Example: *reply to email 2*",
        "de": "Gib die Nummer der E-Mail an, die du beantworten willst. Beispiel: *antworte auf E-Mail 2*",
        "fr": "Indique le numéro de l'e-mail auquel tu veux répondre. Exemple : *réponds au mail 2*",
        "zh": "请告诉我你要回复的邮件编号。例如：*回复第2封邮件*",
        "ru": "Скажи номер письма, на которое хочешь ответить. Пример: *ответь на письмо 2*",
        "ja": "返信したいメールの番号を教えてください。例：*メール2に返信*",
        "tl": "Sabihin mo ang numero ng email na gusto mong sagutin. Halimbawa: *sagutin ang email 2*",
    },
    "email_number_not_found": {
        "es": "No existe el correo {number}. Usa /gmail inbox para ver los disponibles.",
        "en": "There's no email #{number}. Use /gmail inbox to see the available ones.",
        "de": "Es gibt keine E-Mail Nr. {number}. Verwende /gmail inbox, um die verfügbaren zu sehen.",
        "fr": "Il n'y a pas d'e-mail n°{number}. Utilise /gmail inbox pour voir ceux disponibles.",
        "zh": "没有第 {number} 封邮件。请使用 /gmail inbox 查看可用的邮件。",
        "ru": "Письма #{number} не существует. Используй /gmail inbox, чтобы увидеть доступные.",
        "ja": "メール #{number} は存在しません。/gmail inbox で利用可能なメールを確認してください。",
        "tl": "Walang email #{number}. Gamitin ang /gmail inbox para makita ang mga available.",
    },
    "email_injection_warning": {
        "es": "⚠️ *Aviso de seguridad.* No pude verificar que el correo de {sender} sea seguro de procesar automáticamente (puede contener instrucciones dirigidas a un asistente). Por seguridad NO genero un borrador automático.\n\nSi quieres responder, dime con tus palabras qué contestar y lo redacto yo, o respóndelo tú directamente desde Gmail.",
        "en": "⚠️ *Security notice.* I couldn't verify that the email from {sender} is safe to process automatically (it may contain instructions aimed at an assistant). For safety, I'm NOT generating an automatic draft.\n\nIf you want to reply, tell me in your own words what to say and I'll draft it, or reply directly from Gmail yourself.",
        "de": "⚠️ *Sicherheitshinweis.* Ich konnte nicht verifizieren, dass die E-Mail von {sender} sicher automatisch verarbeitet werden kann (sie könnte Anweisungen für einen Assistenten enthalten). Aus Sicherheitsgründen erstelle ich KEINEN automatischen Entwurf.\n\nWenn du antworten möchtest, sag mir in deinen eigenen Worten, was ich schreiben soll, oder antworte direkt in Gmail.",
        "fr": "⚠️ *Avis de sécurité.* Je n'ai pas pu vérifier que l'e-mail de {sender} est sûr à traiter automatiquement (il pourrait contenir des instructions destinées à un assistant). Par sécurité, je NE génère PAS de brouillon automatique.\n\nSi tu veux répondre, dis-moi avec tes propres mots quoi dire et je le rédigerai, ou réponds directement depuis Gmail.",
        "zh": "⚠️ *安全提示。* 我无法确认来自 {sender} 的邮件可以安全地自动处理（它可能包含针对助手的指令）。出于安全考虑，我不会自动生成草稿。\n\n如果你想回复，请用你自己的话告诉我要说什么，由我来起草，或者你直接在 Gmail 中回复。",
        "ru": "⚠️ *Уведомление о безопасности.* Я не смог убедиться, что письмо от {sender} безопасно обрабатывать автоматически (оно может содержать инструкции, адресованные ассистенту). В целях безопасности я НЕ создаю автоматический черновик.\n\nЕсли хочешь ответить, скажи своими словами, что написать, и я составлю черновик, либо ответь напрямую из Gmail.",
        "ja": "⚠️ *セキュリティ通知。* {sender} からのメールを自動的に処理しても安全か確認できませんでした（アシスタント宛ての指示が含まれている可能性があります）。安全のため、自動で下書きを生成しません。\n\n返信したい場合は、何を伝えたいかご自身の言葉で教えていただければ下書きを作成します。または、Gmailから直接返信してください。",
        "tl": "⚠️ *Paalala sa seguridad.* Hindi ko na-verify na ligtas iprocess nang awtomatiko ang email mula kay {sender} (maaaring may mga tagubiling nakatutok sa isang assistant). Para sa kaligtasan, HINDI ako bubuo ng awtomatikong draft.\n\nKung gusto mong sumagot, sabihin mo sa sarili mong mga salita kung ano ang isusulat at gagawin ko ang draft, o sagutin mo mismo mula sa Gmail.",
    },
    "email_draft_created": {
        "es": "📝 Borrador para responder a {sender}:\n\n{draft}\n\n¿Lo envío? Responde *sí* para confirmar, *no* para cancelar, o escribe una versión diferente.",
        "en": "📝 Draft reply to {sender}:\n\n{draft}\n\nShall I send it? Reply *yes* to confirm, *no* to cancel, or write a different version.",
        "de": "📝 Entwurf für die Antwort an {sender}:\n\n{draft}\n\nSoll ich sie senden? Antworte mit *ja* zum Bestätigen, *nein* zum Abbrechen, oder schreib eine andere Version.",
        "fr": "📝 Brouillon de réponse à {sender} :\n\n{draft}\n\nJe l'envoie ? Réponds *oui* pour confirmer, *non* pour annuler, ou écris une version différente.",
        "zh": "📝 回复 {sender} 的草稿：\n\n{draft}\n\n要发送吗？回复 *是* 确认，*否* 取消，或写一个不同的版本。",
        "ru": "📝 Черновик ответа для {sender}:\n\n{draft}\n\nОтправить? Ответь *да* для подтверждения, *нет* для отмены, либо напиши другой вариант.",
        "ja": "📝 {sender} への返信の下書き：\n\n{draft}\n\n送信しますか？*はい*で確認、*いいえ*でキャンセル、または別のバージョンを書いてください。",
        "tl": "📝 Draft ng sagot kay {sender}:\n\n{draft}\n\nIpapadala ko ba ito? Sagutin ang *oo* para kumpirmahin, *hindi* para kanselahin, o sumulat ng ibang bersyon.",
    },
    # ── Evento con intención de crear confirmada pero datos insuficientes
    #    (expediente contaminación temporal, 13 jul). La clave representa el
    #    ESTADO ("faltan datos para crear el evento"), no la frase concreta —
    #    condición del auditor: el texto puede evolucionar a preguntas
    #    específicas sin cambiar el contrato semántico. ─────────────────────
    "event_needs_missing_details": {
        "es": "Claro — ¿para qué día y a qué hora?",
        "en": "Sure — what day and time?",
        "de": "Gerne — an welchem Tag und um wie viel Uhr?",
        "fr": "Bien sûr — quel jour et à quelle heure ?",
        "zh": "好的——哪一天、几点呢？",
        "ru": "Конечно — на какой день и во сколько?",
        "ja": "はい — 何日の何時にしますか？",
        "tl": "Sige — anong araw at anong oras?",
    },
    # ── Comando no reconocido (caso débil documentado: "/foobar" no tiene
    #    idioma detectable → reserva) ─────────────────────────────────────────
    "unknown_command": {
        "es": "❓ No reconozco ese comando.\nEscribe /help para ver los comandos disponibles.",
        "en": "❓ I don't recognize that command.\nType /help to see the available commands.",
        "de": "❓ Diesen Befehl kenne ich nicht.\nSchreib /help, um die verfügbaren Befehle zu sehen.",
        "fr": "❓ Je ne reconnais pas cette commande.\nÉcris /help pour voir les commandes disponibles.",
        "zh": "❓ 我不认识这个命令。\n输入 /help 查看可用命令。",
        "ru": "❓ Я не знаю такой команды.\nНапиши /help, чтобы увидеть доступные команды.",
        "ja": "❓ そのコマンドは認識できません。\n/help と入力して利用可能なコマンドを確認してください。",
        "tl": "❓ Hindi ko nakikilala ang command na iyan.\nI-type ang /help para makita ang mga available na command.",
    },
}


def msg(key: str, lang: str | None = None, **fmt) -> str:
    """Resuelve un mensaje del catálogo en el idioma pedido.

    lang=None → idioma del turno (ContextVar de la red determinista) → reserva EN.
    Idioma no catalogado → reserva EN. Clave inexistente → KeyError (error de
    programación: debe reventar en tests, nunca degradar en silencio).

    NOTA para consumidores: importar con alias ("from ... import msg as
    catalog_msg") — 'msg' es un nombre local frecuente en el código y la
    asignación local convertiría la referencia en UnboundLocalError.
    """
    table = _CATALOG[key]
    code = (lang or get_target_language() or _FALLBACK_LANG).lower().split("-")[0]
    text = table.get(code) or table[_FALLBACK_LANG]
    return text.format(**fmt) if fmt else text


# ── Nombres de día de la semana (mitigación B6: hace visible el weekday real
# antes/después de crear un evento, para que la aprobación humana pueda cazar
# un día equivocado — NO corrige la alucinación del campo weekday del modelo,
# solo reduce el costo de que pase desapercibida). DATOS revisados por humano,
# mismo patrón que _CATALOG; índice = datetime.weekday() (lunes=0..domingo=6),
# igual que _WEEKDAY_ENUM en core.py.
_WEEKDAY_NAMES: dict[int, dict[str, str]] = {
    0: {"es": "lunes", "en": "Monday", "de": "Montag", "fr": "lundi", "zh": "星期一",
        "ru": "Понедельник", "ja": "月曜日", "tl": "Lunes"},
    1: {"es": "martes", "en": "Tuesday", "de": "Dienstag", "fr": "mardi", "zh": "星期二",
        "ru": "Вторник", "ja": "火曜日", "tl": "Martes"},
    2: {"es": "miércoles", "en": "Wednesday", "de": "Mittwoch", "fr": "mercredi", "zh": "星期三",
        "ru": "Среда", "ja": "水曜日", "tl": "Miyerkules"},
    3: {"es": "jueves", "en": "Thursday", "de": "Donnerstag", "fr": "jeudi", "zh": "星期四",
        "ru": "Четверг", "ja": "木曜日", "tl": "Huwebes"},
    4: {"es": "viernes", "en": "Friday", "de": "Freitag", "fr": "vendredi", "zh": "星期五",
        "ru": "Пятница", "ja": "金曜日", "tl": "Biyernes"},
    5: {"es": "sábado", "en": "Saturday", "de": "Samstag", "fr": "samedi", "zh": "星期六",
        "ru": "Суббота", "ja": "土曜日", "tl": "Sabado"},
    6: {"es": "domingo", "en": "Sunday", "de": "Sonntag", "fr": "dimanche", "zh": "星期日",
        "ru": "Воскресенье", "ja": "日曜日", "tl": "Linggo"},
}


def weekday_name(index: int, lang: str | None = None) -> str:
    """Nombre del día de la semana (0=lunes..6=domingo) en el idioma pedido.
    Misma resolución de idioma que msg(): lang explícito → turno → reserva EN."""
    table = _WEEKDAY_NAMES[index]
    code = (lang or get_target_language() or _FALLBACK_LANG).lower().split("-")[0]
    return table.get(code) or table[_FALLBACK_LANG]
