# Снятие слепка R4S design system

`fileKey` ДС — `<DS_FILE_KEY>`. Макеты: «вайб» `<MOCKUP_FILE_KEY>`,
«Заявки» `<MOCKUP_FILE_KEY_2>`.

## 1. Компоненты и матрицы вариантов — `get_metadata`

Самый ценный источник: варианты компонент-сета лежат узлами с именем
`Size=Medium, Type=Primary, State=Hover`, то есть матрица читается машинно.

```
get_metadata(fileKey="<DS_FILE_KEY>", nodeId="2476:1183")
```

Ответ сохранить в `snapshots/raw/<страница>.xml` и прогнать:

```bash
python3 bin/parse_ds_metadata.py snapshots/raw/buttons.xml
```

Скрипт выдаёт `props` (исчерпывающие значения каждого свойства), `drawnVariants`,
`matrixSize`, `missingVariants` (незакрытые ячейки матрицы) и `caseClashes`
(одно значение записано в разных регистрах). Результат кладётся в `components[]`
файла `snapshots/ds-latest.json`.

**Ограничение.** `get_metadata` без `nodeId` вернул только страницу `📕 Change Log` —
остальные страницы так не перечисляются. Node-id нужных страниц брать из URL,
который дал заказчик, или обходом через `use_figma`.

## 2. Описания, дата обновления, DEPRECATED — `search_design_system`

```
search_design_system(query="Button", fileKey="<DS_FILE_KEY>",
                     includeVariables=false, includeStyles=false)
```

Даёт `componentKey`, `updatedAt` и `description`. В R4S описания содержательные:
там прописаны размеры, правило `@media (hover:hover)`, требование Focus по WCAG 2.4.7,
пометка `⚠️ DEPRECATED` и путь миграции. Всё это идёт в `notes` и `replacedBy`.

**Один запрос — одно намерение.** Инструмент не понимает ИЛИ; на каждый компонент
или семейство отдельный вызов.

`updatedAt` компонента, более свежий, чем дата последней правки прототипа, — сигнал,
что ДС ушла вперёд. Это аналог «release detection» оригинала.

## 3. Переменные — Plugin API, а не `get_variable_defs`

`get_variable_defs` даёт только то, что применено на конкретном узле, вперемешку с чужими
коллекциями. Полный слепок снимается через `use_figma`:

```js
const cols = await figma.variables.getLocalVariableCollectionsAsync();
```

### Устройство режимов — читать внимательно, тут легко ошибиться

| Коллекция | Переменных | Режимы |
|---|---:|---|
| `Tokens` | 87 | **Green accent / Blue accent / Purple accent** |
| `Primitives` | 113 | **Light / Dark** |

Светлая и тёмная тема живут **не в Tokens**. Семантический токен вроде
`Color/Text/Default/Primary` лежит в Tokens (режимы — акценты) и алиасом ссылается
в Primitives, где уже есть Light и Dark. Значит итоговое значение зависит от **пары**
(акцент, тема), и разрешать его надо по цепочке:

```js
async function res(id, primMode, accentMode, d) {
  if (d > 8) return null;
  const v = await figma.variables.getVariableByIdAsync(id);
  const mode = (v.variableCollectionId === PRIM_ID) ? primMode : accentMode;
  let x = v.valuesByMode[mode];
  if (x && x.type === 'VARIABLE_ALIAS') return res(x.id, primMode, accentMode, d + 1);
  return x;
}
```

**16 токенов различаются по акценту** — семейства Brand, Link и Button. Значение из
чужого акцента в коде — это не дрейф, а не тот режим, и сверка обязана говорить именно
это: иначе разработчик пойдёт «чинить» правильное значение. Слепок хранит их отдельно
в `accentVariants`, прототип объявляет свой акцент в `config.json`.

Из известных мин, подтверждённых прямо из API: `Size/Size-10` = **10 в Light и 8 в Dark**,
из-за чего плавают `Layout/Gap-size/Gap-M` и `Layout/Font-size/Text-XS`.

Осиротевшие коллекции (`Spacing`, `Primitives/`, `Styles`) в
`getLocalVariableCollectionsAsync()` **не возвращаются** — это подтверждает DS-AUDIT §4.1:
поправить их через UI нельзя, а привязки на них живые.

## 3b. Точечно — `get_variable_defs`

Нужен узел, где переменные реально применены:

```
get_variable_defs(fileKey="<MOCKUP_FILE_KEY_2>", nodeId="4080:6116")   # форма заявки
get_variable_defs(fileKey="<MOCKUP_FILE_KEY_2>", nodeId="4080:5137")   # список заявок
```

Возвращает `{"Color/Text/Default/Primary": "#04141f", ...}` — вперемешку с переменными
чужих коллекций (`Core/24`, `var(--sds-*)`, `Typography/Body/L/Font size`). В слепок брать
только имена ДС; остальное — из посторонних китов, см. `foreignCollections`.

**Значения зависят от режима.** Узлы светлых макетов дают Light. Тёмные прототипы
по этим значениям сверять нельзя — в `ds-latest.json` значения хранятся как
`{"light": ..., "dark": ...}`, а `{"any": ...}` только для действительно
режимонезависимых. Исключение уже известно: `Size/Size-10` = 10 в Light и 8 в Dark,
из-за чего плавают `Layout/Gap-size/Gap-M` и `Layout/Font-size/Text-XS`.

## 3c. Компоненты — `variantGroupProperties`

Матрицу вариантов удобнее брать не разбором XML, а прямо у компонент-сета:

```js
const page = await figma.getNodeByIdAsync(PAGE_ID);
await figma.setCurrentPageAsync(page);
const sets = page.findAllWithCriteria({ types: ['COMPONENT_SET'] });
// s.variantGroupProperties → { Size: {values:[...]}, State: {values:[...]} }
// s.children.length        → сколько вариантов реально нарисовано
```

Страницы компонентов ДС: `2476:1183` Buttons, `2508:1666` Inputs, `2574:2520` Tabs/Controls,
`3076:4062` Components, `5332:2636` Modals. Разносить по одному вызову на страницу
и слать параллельно — `setCurrentPageAsync` разрешено звать один раз за скрипт.

**Описание компонента может расходиться с его матрицей.** У `Chip` в описании заявлены
`Type` и `Selected`, а в `variantGroupProperties` — `State` и `Color`. Истина — матрица,
описание вторично.

**Легаси помечается по-разному.** У кнопок — словом `DEPRECATED` в описании,
у `❌Segmented control❌Legacy` и `❌Modal_status❌` — крестиками прямо в имени.
Проверять оба признака.

## 4. Что на этом тарифе не работает

- `list_file_components_for_code_connect` — требует Dev/Full seat на Organization
  или Enterprise. У команды R4S тариф pro, инструмент отвечает отказом.
  Поэтому инвентарь компонентов собирается через `get_metadata` + `search_design_system`.

## 5. Грабли транспорта MCP

**Текст с `U+2028` рвёт ответ.** В карточках change-log переносы сделаны символом
LINE SEPARATOR. Если вернуть такой текст из `use_figma` как есть, приходит
`Failed to parse SSE message: EOF while parsing a string` — это не размер ответа,
а незакрытая строка. Возвращать текст только экранированным:

```js
const esc = s => Array.from(String(s)).map(ch => {
  const c = ch.codePointAt(0);
  return (c >= 32 && c < 127) || (c >= 0x410 && c <= 0x44f) || c === 0x401 || c === 0x451
    ? ch : '{' + c.toString(16) + '}';
}).join('');
```

**Имена текстовых узлов в Figma равны их содержимому.** Дерево с текстами разносит
ответ на десятки килобайт. Обрезать имена (`String(n.name).slice(0, 34)`) и держать
глубину обхода в пределах 3.

**`get_metadata` по большой странице тоже не влезает.** Спускаться на конкретный
фрейм, а не запрашивать страницу целиком.
