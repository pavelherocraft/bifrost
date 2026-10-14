# Отчёт аудита: {{app_name}} {{version}} ({{build_number}}) — {{audit_date}}
## 1. Краткое резюме
- Вердикт: {{verdict}}
- Топ-3 находки (по одной строке каждая):
{{top3}}
- Сравнение с предыдущим билдом (diff-сводка): {{diff_prev}}
## 2. Информация о сборке
- IPA SHA-256: {{ipa_sha256}}, размер: {{ipa_size}}, cryptid: {{cryptid}}, arch: {{arch}}, Xcode/экспорт-происхождение (PROVENANCE): {{provenance}}
- Версии инструментов: {{tools}}
- Hybrid-тип (native / RN / Flutter / Capacitor / Unity IL2CPP): {{hybrid}}
## 3. Найденные проблемы
| ID | Серьёзность | Уверенность | Категория | Паттерн | Доказательства (файл:фаза) | Задекларировано? | Достижимо? | Управляется | Вердикт | План устранения |
|---|---|---|---|---|---|---|---|---|---|---|
{{findings_table}}
## 4. Матрица расхождений «binary vs declarations»
{{mismatch_matrix}}
## 5. Доказательства
{{evidence_appendix}}
## 6. Ложные срабатывания (рассмотрены и отклонены)
{{false_positives}}
## 6b. Инвентаризация аналитических и рекламных SDK (ред. 2, §4.17) — обязательная секция даже при 0 violations
| SDK | Маркеры найдены | Purchase-трекинг? | Задекларировано в App Description? | Решение (задокументировать / отключить события / удалить) |
|---|---|---|---|---|
{{analytics_inventory}}
## 7. План устранения
{{remediation}}
## 8. Результат повторного аудита (после remediation)
- Placeholder: заполняется при повторном аудите (diff по finding-ID, подтверждения из evidence нового билда).
## 9. Контрольный список подтверждения перед сабмитом
- [ ] 0 confirmed Critical/High
- [ ] Все `analytics_ad`-находки закрыты вердиктом (нет needs_review)
- [ ] Все deprecated-SDK (TinkLink/MobilePay AppSwitch/CP* legacy/WalletConnectV2) удалены
- [ ] Nordic URL-схемы удалены из Info.plist (или задекларированы + доступны ревьюеру из США)
- [ ] Матрица гейтинга пуста (нет region/date/test-account/A/B-условий на платёжных экранах)
- [ ] Resolution Center ответ готов
- [ ] App Description / Review Notes обновлены (если Вариант B / A3-вариант 1)
- [ ] Секреты ротированы (если были в бинаре)
- [ ] CI-гейт зелёный на финальном экспорте
