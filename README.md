# mo2-flatten

> **English.** Run a large Mod Organizer 2 setup (Skyrim SE 1.5.97, *Requiem for a Dream*, ~400 mods)
> on macOS through CrossOver/Wine **without usvfs**. The MO2 profile is materialised into a real `Data`
> folder with hardlinks (no extra disk space, ~35 s for 73k files), the game is started through
> `skse64_loader.exe`, and everything is put back after the game exits. Measured on an M1 Pro: the game
> reaches the main menu in **61 s instead of 106 s** under MO2/usvfs. The README also documents every
> crash we hit on the way and its fix (.NET Script Framework under Wine Mono, the 64 MB save size
> limit, …). The rest of this page is in Russian.

Запуск большой MO2-сборки (Skyrim SE, RfaD) на Mac под CrossOver **без usvfs**. Профиль MO2
превращается в настоящую папку `Data` из hardlink'ов: место на диске не занимает, моды и MO2 не
меняются. Игра стартует через SKSE напрямую, после выхода всё возвращается как было.

Кому пригодится: тем, кто играет на Mac (или Linux/Wine) в сборки на MO2 и упирается в зависания,
вылеты или медленный старт. Ниже ещё и разбор всех проблем, которые встретились по пути, с причинами
и исправлениями. Большая часть из них не зависит от скрипта.

## Результаты замеров

Один и тот же сейв, 6 запусков с чередованием режимов (MO2 → скрипт → MO2 → …), чтобы дисковый кэш
не играл в пользу одного из них. Время снималось автоматически по логам SKSE-плагинов
(`bench_load.py`, данные — в `bench_results.csv`).

| | MO2 + usvfs | mo2-flatten |
|---|---|---|
| Старт `SkyrimSE.exe` → главное меню (медиана, мин–макс) | 106,1 с (105,3–112,9) | **61,1 с** (60,6–62,3) |
| Загрузка сейва | 32,6 с (32,2–33,6) | 32,3 с (32,3–33,2) |
| Подготовка `Data` перед запуском | — | 34–36 с |
| Возврат `Data` после выхода | — | 18–19 с |

Что из этого следует, без приукрашивания:

- **Сама игра стартует на 45 с (43 %) быстрее.** Разница намного больше разброса между запусками.
  На старте движок читает 400+ плагинов и архивов, и под MO2 каждое обращение к файлу проходит
  через перехват usvfs.
- **От двойного клика до меню выигрыш меньше, около 10 с** (≈ 96 с против ≈ 106 с), потому что
  скрипт сначала собирает `Data`. Время запуска самого MO2 в замер не входило.
- **Загрузка сейва не ускоряется.** Разница в пределах шума: там время уходит на разбор самого
  сейва и скриптов, а не на чтение файлов.
- **Отклик мыши — наблюдение, не замер.** Под MO2 чувствуется инпутлаг, через скрипт его нет.
  Объяснение то же: usvfs перехватывает чтение файлов, а Skyrim подгружает текстуры и меши прямо
  во время игры. Отдельные кадры задерживаются, и мышь, привязанная к кадрам, ощущается вязкой.
  Цифр нет: на macOS нет инструмента, который пишет время кадра в файл.

Где мерили:

| | |
|---|---|
| Машина | MacBook Pro 16" (2021), Apple M1 Pro (8P+2E CPU, 16 GPU), 32 ГБ |
| ОС | macOS 15.7.7 |
| CrossOver | 26.3, бутылка Windows 11 (64-bit), графика DXVK |
| Экран | внешний 3440×1440 @ 165 Гц |
| Игра | Skyrim SE 1.5.97, RfaD SE 6.2, MO2 2.5.2, usvfs 0.5.7.2 |
| Графика в игре | 3440×1440, окно без рамки (SSE Display Tweaks), VSync, лимит 60 FPS, ReShade |

## Быстрый старт

Нужны Python 3 (проверено на 3.14 из Homebrew, без сторонних пакетов) и MO2-сборка внутри
бутылки CrossOver. В `rfad-play.command` укажите путь к своей папке MO2 (по умолчанию бутылка `Games`,
`C:\Games\RfaD\MO2`).

```bash
git clone https://github.com/vratskyi/mo2-flatten.git
cd mo2-flatten
./rfad-play.command          # или двойной клик по файлу в Finder
```

`./` обязателен: zsh не ищет команды в текущей папке (`zsh: command not found: rfad-play.command`).
MO2 перед запуском закройте, иначе скрипт откажется работать.

`play` делает всё сам: собирает `Data`, запускает SKSE, ждёт выхода из игры и возвращает всё как было.
MO2 сплющенную `Data` никогда не видит. Каждый запуск собирается по актуальному modlist, так что
моды можно настраивать в MO2 как обычно. Окно терминала держите открытым до выхода из игры. Если
закрыть его раньше, `Data` останется сплющенной, и до открытия MO2 нужно выполнить `restore`.

## Проблемы и решения (RfaD на macOS)

### 1. Главное меню без кнопок, «Загрузка анимаций 0/612», «Новая игра» висит

**Причина:** .NET Script Framework (NSF) не работает на **Wine Mono**, встроенной замене .NET в
CrossOver. Главный поток игры навсегда встаёт внутри Mono. На NSF держатся Custom Skills Framework
(деревья навыков RfaD), SSE Gameplay Tweaks, No Grass In Objects и крашлоги NSF.

Как установили: серия запусков, плагины исключались по одному через `excludes.txt`, стек снимался
`winedbg --command "bt all"` с зависшего процесса (с живого так нельзя: отладчик замораживает игру).

| Что загружено | Результат |
|---|---|
| NSF + любой из его плагинов (4 запуска) | меню не появляется, главный поток стоит в `libmono` |
| NSF без плагинов (2 запуска) | меню есть, «Новая игра» висит |
| без NSF | меню, анимации OAR, создание персонажа работают |

**Исправление:** поставить в бутылку настоящий .NET Framework 4.8 от Microsoft. После этого RfaD
работает целиком, со всеми плагинами NSF, и через скрипт, и через MO2.

**⚠️ Сначала сделайте копию бутылки** (CrossOver → бутылка → Duplicate): шаги меняют её необратимо.

1. **Удалить Wine Mono.** CrossOver → бутылка → Run Command → `uninstaller` → «Wine Mono Windows
   Support» → Remove. Wine Mono прописывает в реестре версию 4.8.1, и установщик .NET отказывается
   ставиться («already installed»).
2. Остальное — команды для Терминала. Поменяйте `BOTTLE` на имя своей бутылки. Нужны установщики
   Microsoft: [.NET 4.8 офлайн](https://dotnet.microsoft.com/download/dotnet-framework/net48)
   (`ndp48-x86-x64-allos-enu.exe`) и
   [.NET 4.0](https://download.microsoft.com/download/9/5/A/95A9616B-7A37-4AF6-BC36-D6EA96C8DAAE/dotNetFx40_Full_x86_x64.exe)
   (`dotNetFx40_Full_x86_x64.exe`, SHA-256 `65e06425…c38659f`, тот же, что берёт winetricks).
   Оба положите в «Загрузки». Ещё нужен 7z: `brew install p7zip`.

```bash
BOTTLE="Games"
WINE="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"
CAB="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cabextract"
WIN="$HOME/Library/Application Support/CrossOver/Bottles/$BOTTLE/drive_c/windows"
w() { "$WINE" --bottle "$BOTTLE" --cx-app "$@"; }

# Временно Windows 7: на Win10/11 установщик .NET 4.8 блокируется
w winecfg -v win7

# .NET 4.8. Предупреждения про Windows Update — Continue, дождаться "Installation Is Complete".
# CrossOver запускает exe только по Windows-пути, поэтому сначала копируем в C:\
cp "$HOME/Downloads/ndp48-x86-x64-allos-enu.exe" "$WIN/../ndp48.exe"
w 'C:\ndp48.exe'

# Настоящий mscoree.dll из пакета .NET 4.0: установщик 4.8 его не кладёт,
# а заглушка Wine умеет запускать только Mono. cabextract учитывает только одну маску -F.
cd /tmp && rm -rf net40 && mkdir net40 && cd net40
7z x -y "$HOME/Downloads/dotNetFx40_Full_x86_x64.exe" -ox > /dev/null
"$CAB" -q -F '_0??_mscoree*' -d out x/netfx_Core.mzz
cp out/_003_mscoree_dll_amd64  "$WIN/system32/mscoree.dll"
cp out/_017_mscorees_dll_amd64 "$WIN/system32/mscorees.dll"
cp out/_003_mscoree_dll_x86    "$WIN/syswow64/mscoree.dll"
cp out/_017_mscorees_dll_x86   "$WIN/syswow64/mscorees.dll"

# Реестр
w reg add 'HKCU\Software\Wine\DllOverrides' /v mscoree /t REG_SZ /d native /f
w reg add 'HKLM\Software\Microsoft\.NETFramework' /v InstallRoot /t REG_SZ /d 'C:\windows\Microsoft.NET\Framework64\' /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework' /v InstallRoot /t REG_SZ /d 'C:\windows\Microsoft.NET\Framework\' /f
w reg add 'HKLM\Software\Microsoft\.NETFramework\Policy\v4.0' /v 30319 /t REG_SZ /d 30319-30319 /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework\Policy\v4.0' /v 30319 /t REG_SZ /d 30319-30319 /f
w reg add 'HKLM\Software\Microsoft\.NETFramework' /v OnlyUseLatestCLR /t REG_DWORD /d 1 /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework' /v OnlyUseLatestCLR /t REG_DWORD /d 1 /f

# Вернуть Windows 11 (или какая была у бутылки)
w winecfg -v win11

# Проверка: должно напечатать "Microsoft .NET Framework ... version 4.8.x"
w 'C:\windows\Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe' '/?'
```

Окно «A fatal error occurred… mscorees.dll could not be loaded» на проверке значит, что пропущен
шаг с `mscoree.dll` или с реестром. На свежей бутылке всё это, вероятно, заменяет
`winetricks dotnet48`, но через CrossOver это не проверялось.

Если .NET 4.8 ставить не хочется, RfaD запустится без NSF: раскомментируйте строку в `excludes.txt`.
Тогда не будет деревьев навыков и трёх модов.

### 2. Вылет в `refrain_nextgen.dll` при старте, OAR висит на «Loading animations»

**Причина:** usvfs под Wine на сборке такого размера ведёт себя нестабильно: отдаёт плагинам битые
данные (краш `refrain_nextgen.dll` при чтении своего ini) или виснет на файловой операции.

**Исправление:** играть через mo2-flatten, там usvfs нет вообще. Если нужен именно MO2, замените
в его папке `usvfs_proxy_x64.exe`, `usvfs_proxy_x86.exe`, `usvfs_x64.dll`, `usvfs_x86.dll` на
[версию 0.5.7.2](https://github.com/ModOrganizer2/usvfs/releases/tag/v0.5.7.2): с usvfs из комплекта
MO2 2.5.2 SKSE-плагины грузились с ошибками.

**Плюс: анимации — после сейва, а не в главном меню.** По умолчанию Open Animation Replacer грузит
поведения и анимации ещё в главном меню (та самая плашка «Loading animations 0/612»), а под Wine
на ней игра и зависала. В `SKSE/Plugins/OpenAnimationReplacer.ini` (MO2 кладёт его
в `overwrite`; то же самое меняется в меню OAR в игре, Shift+O):

```ini
[General]
bLoadDefaultBehaviorsInMainMenu = false
```

Теперь главное меню доступно сразу, а анимации подгружаются вместе с сейвом. По логу OAR:
меню в 23:31:38, сборка анимаций персонажа и существ — в 23:32:10–11, уже во время загрузки
сейва. Отдельно выигрыш от этой настройки не мерили; в замерах выше она стояла одинаково в обоих
режимах, так что на сравнение MO2 и скрипта не влияет.

### 3. После долгой игры любой новый сейв вылетает при загрузке

**Симптомы:** старые сейвы грузятся, все сейвы начиная с какого-то момента вылетают на загрузке.
В крашлоге NSF (`Data/NetScriptFramework/Crash/`, после `restore` — `MO2/overwrite/...`) одно и то же
место: `SkyrimSE.exe+128731A`, `LoadStorageWrapper` → `BSScript::...VirtualMachine`. Совпадает с
каким-то событием в игре (у нас — выход из подземелья с новыми ботинками), но событие ни при чём.

**Причина:** в Skyrim SE 1.5.97 распакованный сейв не может быть больше **64 МиБ**. Сейв растёт
вместе с прохождением; дойдя до потолка, игра молча записывает обрезанный файл. Разбор наших
сейвов: 423–446 росли с 61,1 до 63,7 МиБ и целые, все последующие ровно 64,000 МиБ, у них обрезан
хвост, а в таблице смещений стоит 67108864 (ровно 64 МиБ) вместо настоящего адреса.

**Исправление:** в Engine Fixes этот лимит поднимается до 128 МиБ, но опция выключена по умолчанию.
`SKSE/Plugins/EngineFixes.toml` в моде SSE Engine Fixes:

```toml
[Experimental]
SaveGameMaxSize = true
```

Проверено: первый сейв после правки записался целым, 64,03 МиБ, дальше сейвы растут без проблем.
Уже обрезанные сейвы не восстановить: данные потеряны при записи. Нужно вернуться на последний целый.

**Какие сейвы целые** — покажет `check_saves.py` (только стандартный Python, читает лишь заголовок,
сотни сейвов за доли секунды; работает с сейвами любой сборки Skyrim SE, в том числе на Windows):

```bash
python3 check_saves.py "путь/к/папке/saves"
```

```
Save446_…  Malor  63.704 MiB  ok, close to 64 MiB: turn on SaveGameMaxSize in EngineFixes.toml
Save447_…  Malor  64.000 MiB  BROKEN: cut off at 64 MiB
Save465_…  Malor  64.031 MiB  ok (over 64 MiB: SaveGameMaxSize is on)
```

Признак обрезки однозначный: заголовок плюс распакованное тело сейва занимают ровно 64 МиБ (или
128 МиБ с Engine Fixes) — буфер движка заполнен до последнего байта. На наших 543 сейвах это
совпало с полным разбором структуры: 16 обрезанных, ни одного ложного срабатывания.

### 4. Редкий вылет в `BSWin32KeyboardDevice::Process`

Игра упала при нажатии клавиши: Wine отдал DirectInput-событие с мусорным скан-кодом (`0x6D266573`
вместо 0–255), движок использовал его как индекс в таблице клавиш. Один случай на 12 крашлогов,
не воспроизводится. Не связан ни со сборкой, ни с сейвом, ни с тем, что в этот момент делалось в
игре. Если повторяется — проверьте, не совпадает ли с переключением раскладки или Cmd+Tab.

### 5. `PermissionError: ... Skyrim.ini` при запуске скрипта

Было в старых версиях скрипта: если ini в профиле MO2 сделаны «только для чтения», твики из
`initweaks.ini` не могли записаться. Исправлено: скрипт временно даёт запись и возвращает режим
обратно, так что защита ini от перезаписи игрой сохраняется.

## Команды

```bash
S="python3 mo2_flatten.py --mo2 '$HOME/Library/Application Support/CrossOver/Bottles/Games/drive_c/Games/RfaD/MO2'"
eval $S plan      # что и откуда будет слито, конфликты
eval $S flatten   # сплющить (исходная Data переименовывается в Data.vanilla)
eval $S run       # запустить skse64_loader.exe в бутылке
eval $S restore   # вернуть обычный режим MO2
eval $S status    # текущее состояние; предупредит, если modlist менялся после flatten
eval $S verify    # каждый файл в Data — победитель по правилам MO2
eval $S which 'SKSE/Plugins/refrain_nextgen.dll'   # кто поставляет путь и кто побеждает
```

`excludes.txt` рядом со скриптом — пути внутри `Data`, которые не попадут в игру (маска, без учёта
регистра). Удобно для поиска виновника вылета: исключили плагин, запустили, вернули. MO2 и моды при
этом не меняются.

## Как это устроено

| MO2 делает виртуально | Скрипт делает физически |
|---|---|
| моды поверх `Data` (верх `modlist.txt` = наивысший приоритет, `+` включён, `_separator` пропускается) | hardlink победителя по каждому пути, без учёта регистра |
| `overwrite` поверх всего | линкуется последним |
| скрывает `*.mohidden`, `meta.ini` в корне мода | пропускаются |
| `LocalSettings`: ini из профиля | профильные `Skyrim.ini` / `SkyrimPrefs.ini` / `SkyrimCustom.ini` (+ `initweaks.ini`) кладутся в `My Games`, оригиналы уходят в бэкап |
| `plugins.txt` / `loadorder.txt` профиля | копируются в `AppData/Local/Skyrim Special Edition` |
| `LocalSaves`: сейвы в `profiles/<p>/saves` | копируются в `My Games/.../__MO_Saves` (путь из `sLocalSavePath`), после игры новые переносятся обратно |
| новые файлы от игры → `overwrite` | при `restore` всё, что игра создала или подменила в `Data`, переносится в `overwrite` |

Порядок приоритетов в `modlist.txt` проверен замером: среди общих файлов `6_2_Base` и `6_0_Base`
41 из 150 различаются, и во всех 41 случае новее версия из `6_2_Base`, которая стоит выше в файле.

Инварианты безопасности:
- `restore` удаляет из `Data` только ссылки, у которых есть второй экземпляр (`nlink > 1`). Файл без
  второй ссылки не удаляется, а переносится в `overwrite/_flatten_orphans`.
- Если MO2 или игра запущены, скрипт откажется работать.
- Повторный `flatten` без `restore` запрещён.
- Настройки, изменённые в игре, возвращаются в профиль. Сравнение идёт с тем, что положил скрипт,
  поэтому `initweaks` не утекают в профиль как «изменения».

Состояние и манифест лежат в `RfaD/.mo2flatten/`.

## Ограничения

- `Data`, `mods` и `overwrite` должны быть на одном томе (hardlink). Скрипт это проверяет.
- Если игра правит существующий файл мода «на месте», правка попадает в сам мод — так же, как под usvfs.
- Кастомные `mod_directory` / `profiles_directory` в `ModOrganizer.ini` не поддерживаются: скрипт
  откажется работать, а не сломает что-то молча.
- BSA не распаковываются и не нужны: движок грузит их сам, одноимённые архивы решаются тем же
  правилом «побеждает старший».
- Если `flatten` упадёт посередине, скрипт попросит выполнить `restore`: он вернёт и `Data`, и
  подменённые ini и `plugins.txt` (список подменённого пишется по ходу работы, а не в конце).

## Как повторить замеры

```bash
python3 bench_load.py            # в отдельном окне; потом запускайте игру любым способом
python3 bench_load.py --summary  # медиана / мин / макс по режимам
```

Режим определяется сам (есть состояние flatten — значит скрипт, иначе MO2). Маркеры берутся из логов
плагинов этой сборки: `QuickLootIE.log` (`kDataLoaded end`, со своим временем) и `refrain_nextgen.log`
(`log_pre_load_game` → `kPostLoadGame`). В другой сборке их придётся заменить в начале `bench_load.py`.
