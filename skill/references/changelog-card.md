# Карточка обновления в change-log R4S design system

Страница `📕 Change Log`, узел-сетка `12929:4`.

## Устройство шаблона

```
12929:4  FRAME
└ ряд по годам: "2023" / "2024" / "2025" / "2026"   HORIZONTAL, gap 250, align MIN
  └ карточка, имя = "ДД.ММ.ГГ"                      HORIZONTAL, gap 190, padding 100, radius 100
    └ "Text"                                        VERTICAL, gap 60
      ├ "Heading"  TEXT  RF Dewi Expanded Bold 117  «Обновление⁠<U+2028>от ДД.ММ.ГГ»
      └ тело       TEXT  Manrope 30, lineHeight 177, width 1130, autoResize HEIGHT
```

Тело состоит из повторяющихся блоков:

| Часть | Стиль | Список |
|---|---|---|
| `Рубрика:\n` | Manrope **Bold** 30 | нет |
| пустая строка | Manrope Medium 30 | нет |
| пункты через `\n` | Manrope Medium 30 | UNORDERED, indentation 1 |

Рубрики: **Добавлено**, **Изменено**, **Исправлено**, **Удалено**, **В разработке**.

Эталонная карточка для клонирования — `13380:5783` (последняя в ряду 2026).
Имя карточки в шаблоне бывает несвежим — реальная дата живёт в тексте заголовка.

## Шрифты: заголовок отредактировать нельзя

`RF Dewi Expanded` (заголовки карточек) и `GT Eesti Pro Text` в сессии MCP **недоступны** —
`listAvailableFontsAsync` их не отдаёт. Из нужного есть только `Manrope` и `Onest`.
Поменять текст заголовка, не тронув шрифт, невозможно: Figma требует загрузить
текущий шрифт узла перед записью `characters`.

Значит порядок такой: загрузить `Onest Bold`, присвоить `head.fontName`, вернуть
исходный `fontSize` (117 — присвоение семейства его не трогает, но проверить стоит),
и только потом писать `characters`. В отчёте человеку прямо сказать, что заголовок
набран не тем шрифтом и его надо вернуть на `RF Dewi Expanded Bold` — это один выбор
в панели, у дизайнера шрифт установлен.

**Перенос в заголовке — `U+2028`, а не пробел и не `\n`.** Подставите пробел —
заголовок уйдёт в одну строку и вылезет за карточку. В коде писать
`String.fromCharCode(0x2028)`: литеральный `U+2028` внутри регулярного выражения
роняет скрипт с `SyntaxError: unexpected line terminator in regexp`.

## Как писать

Текст берётся из `reports/changelog-card.json`, который собрал `bin/review.py`:
`heading`, `body`, `ranges` (диапазоны `label` / `item` для разметки стилей).

**Клонировать эталон, а не строить с нуля** — так сохраняются заливка, радиус,
паддинги, шрифты и привязки. Менять только два текста.

Перед записью:
1. показать человеку текст карточки и дождаться согласия — это общий файл ДС;
2. запомнить id клона, чтобы можно было удалить его одним действием, если что-то не так;
3. библиотеку **не публиковать** — это всегда руками.

```js
// 1) контекст
const page = figma.root.children.find(p => p.name.includes('Change Log'));
await figma.setCurrentPageAsync(page);
const grid = await figma.getNodeByIdAsync('12929:4');
const year = grid.children.find(c => c.name === YEAR);       // напр. '2026'
const tpl  = await figma.getNodeByIdAsync('13380:5783');

// 2) шрифты — до любой правки текста
for (const f of [{family:'RF Dewi Expanded',style:'Bold'},
                 {family:'Manrope',style:'Bold'},
                 {family:'Manrope',style:'Medium'}]) await figma.loadFontAsync(f);

// 3) клон в конец ряда
const card = tpl.clone();
card.name = DATE;                                             // 'ДД.ММ.ГГ'
year.appendChild(card);

// 4) тексты
const box = card.children[0];
box.children[0].characters = HEADING;                         // с U+2028 внутри
const body = box.children[1];
body.characters = BODY;
for (const r of RANGES) {
  body.setRangeFontName(r.start, r.end,
    {family:'Manrope', style: r.style === 'label' ? 'Bold' : 'Medium'});
  body.setRangeListOptions(r.start, r.end,
    {type: r.style === 'label' ? 'NONE' : 'UNORDERED'});
  body.setRangeIndentation(r.start, r.end, r.style === 'label' ? 0 : 1);
}

return { createdNodeIds: [card.id] };
```

Проверить результат `get_screenshot` по id клона, и только потом отчитываться.
Если вышло криво — удалить клон по возвращённому id, а не чинить поверх.
