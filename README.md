# mo2-flatten

Запуск большой MO2-сборки (Skyrim SE, RfaD) под CrossOver/Wine **без usvfs**. Профиль MO2
материализуется в настоящую папку `Data` hardlink'ами: места не занимает, 74 тыс. файлов
собираются примерно за 25 секунд. Игра стартует через `skse64_loader.exe` напрямую.

Зачем это нужно: usvfs под Wine нестабилен на больших сборках. Он отдаёт плагинам битые данные
(краш `refrain_nextgen.dll` при чтении своего ini) или виснет на файловой операции (OAR
«Loading animations 0/612»).

## Что на самом деле вешало RfaD (разбор 2026-09-26)

**Зависание вызывал не usvfs, а .NET Script Framework (NSF) под Wine Mono.** Проверено серией
запусков: flatten без usvfs, MSync вкл/выкл, исключение плагинов по одному через `excludes.txt`:

| Что загружено | Результат |
|---|---|
| NSF + любой из его плагинов (4 запуска, разные наборы) | главное меню не появляется; главный поток стоит в `libmono` |
| NSF без плагинов (2 запуска) | меню есть, «Новая игра» висит |
| без NSF (`DLLPlugins/NetScriptFramework.Runtime.dll` исключён) | меню, загрузка анимаций OAR, создание персонажа — работают |

Стек брался через `winedbg --command "bt all"` на зависшем процессе. На живом процессе так
делать нельзя: отладчик не отцепляется и замораживает игру.

Без NSF не работают Custom Skills Framework (деревья навыков RfaD), SSE Gameplay Tweaks,
No Grass In Objects и крашлоги NSF.

## Исправление: настоящий .NET Framework 4.8 вместо Wine Mono (проверено 2026-09-26)

С ним RfaD запускается полностью, со всеми 4 плагинами NSF: меню, новая игра, пролог.
CrossOver 26.3, бутылка Windows 11 (64-bit). **Перед началом сделайте копию бутылки.**

1. **Удалить Wine Mono.** CrossOver → Run Command → `uninstaller` → «Wine Mono Windows Support» → Remove.
   Wine Mono пишет в реестр `NDP\v4\Full Release=533320` (4.8.1), и установщик .NET отказывается
   ставиться («already installed»).
2. **Временно переключить бутылку на Windows 7:** `wine --bottle <имя> winecfg -v win7`.
   На Windows 10/11 .NET 4.8 считается частью системы, и установщик блокируется.
3. **Поставить .NET 4.8** (`ndp48-x86-x64-allos-enu.exe`, офлайн-установщик Microsoft).
   Предупреждения про Windows Update — Continue.
4. **Поставить настоящий `mscoree.dll`.** Установщик 4.8 его не кладёт, а встроенная заглушка
   Wine умеет запускать только Mono. Берётся из установщика .NET 4.0
   (`dotNetFx40_Full_x86_x64.exe`, SHA-256 `65e06425…c38659f` — то же, что в winetricks):
   ```bash
   7z x dotNetFx40_Full_x86_x64.exe -ox
   cabextract -F '_003_mscoree_dll_*' -F '_017_mscorees_dll_*' -d out x/netfx_Core.mzz
   # amd64 -> windows/system32, x86 -> windows/syswow64
   cp out/_003_mscoree_dll_amd64  drive_c/windows/system32/mscoree.dll
   cp out/_017_mscorees_dll_amd64 drive_c/windows/system32/mscorees.dll
   cp out/_003_mscoree_dll_x86    drive_c/windows/syswow64/mscoree.dll
   cp out/_017_mscorees_dll_x86   drive_c/windows/syswow64/mscorees.dll
   ```
5. **Реестр** (через `wine --bottle <имя> reg add …`):
   ```
   HKCU\Software\Wine\DllOverrides                          mscoree = native
   HKLM\Software\Microsoft\.NETFramework                    InstallRoot = C:\windows\Microsoft.NET\Framework64\
   HKLM\Software\Wow6432Node\Microsoft\.NETFramework        InstallRoot = C:\windows\Microsoft.NET\Framework\
   HKLM\Software\Microsoft\.NETFramework\Policy\v4.0        30319 = 30319-30319
   HKLM\Software\Wow6432Node\Microsoft\.NETFramework\Policy\v4.0   30319 = 30319-30319
   HKLM\Software\Microsoft\.NETFramework                    OnlyUseLatestCLR = dword:1
   HKLM\Software\Wow6432Node\Microsoft\.NETFramework        OnlyUseLatestCLR = dword:1
   ```
   Без `mscorees.dll` и `InstallRoot`/`Policy` окно ошибки говорит: «A fatal error occurred…
   mscorees.dll could not be loaded».
6. **Вернуть Windows 11:** `winecfg -v win11`.
7. **Проверка:** `wine --bottle <имя> 'C:\windows\Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe' '/?'`
   должен напечатать «Microsoft .NET Framework … version 4.8.x». Окно с ошибкой — значит, шаг пропущен.

Шаги 4–5 — ручное повторение того, что делает `winetricks dotnet40` (он ставится перед dotnet48).
На свежей копии бутылки, скорее всего, хватит `winetricks dotnet48`, но это не проверялось.

Не проверено: нужен ли после этого flatten, или RfaD пойдёт и через обычный MO2 с usvfs 0.5.7.2.

## Как играть

```bash
./rfad-play.command          # или двойной клик по файлу в Finder
```

`play` делает всё сам: сплющивает `Data`, запускает SKSE, ждёт выхода из игры и возвращает
всё как было. MO2 никогда не видит сплющенную `Data`. Каждый запуск собирается по актуальному
modlist, так что пересобирать после изменений в MO2 руками не нужно.

Окно терминала на время игры держите открытым. Если закрыть его раньше, `Data` останется
сплющенной — до открытия MO2 выполните `restore`.

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

## Что делается и почему

| MO2 делает виртуально | Скрипт делает физически |
|---|---|
| моды поверх `Data` (верх `modlist.txt` = наивысший приоритет, `+` включён, `_separator` пропускается) | hardlink победителя по каждому пути, без учёта регистра |
| `overwrite` поверх всего | линкуется последним |
| скрывает `*.mohidden`, `meta.ini` в корне мода | пропускаются |
| `LocalSettings`: ini из профиля | профильные `Skyrim.ini` / `SkyrimPrefs.ini` / `SkyrimCustom.ini` (+ `initweaks.ini`) кладутся в `My Games`, оригиналы уходят в бэкап |
| `plugins.txt` / `loadorder.txt` профиля | копируются в `AppData/Local/Skyrim Special Edition` |
| `LocalSaves`: сейвы в `profiles/<p>/saves` | копируются в `My Games/.../__MO_Saves` (путь из `sLocalSavePath`), после игры новые переносятся обратно |
| новые файлы от игры → `overwrite` | при `restore` всё, что игра создала или подменила в `Data`, переносится в `overwrite` |

Порядок приоритетов в `modlist.txt` проверен замером. Среди общих файлов `6_2_Base` и `6_0_Base`
41 из 150 различаются, и во всех 41 случае новее версия из `6_2_Base`, которая стоит выше в файле.

Инварианты безопасности:
- `restore` удаляет из `Data` только те ссылки, у которых есть второй экземпляр (`nlink > 1`).
  Файл без второй ссылки не удаляется, а переносится в `overwrite/_flatten_orphans`.
- Если MO2 или игра запущены, скрипт откажется работать.
- Повторный `flatten` без `restore` запрещён.
- Настройки, изменённые в игре, возвращаются в профиль. Сравнение идёт с тем, что положил скрипт,
  поэтому `initweaks` не утекают в профиль как «изменения».

Состояние и манифест лежат в `RfaD/.mo2flatten/`.

## Ограничения

- `Data`, `mods` и `overwrite` должны быть на одном томе (hardlink). Скрипт проверяет это.
- Если игра правит существующий файл мода «на месте», правка попадает в сам мод — ровно так же,
  как под usvfs.
- Кастомные `mod_directory` / `profiles_directory` в `ModOrganizer.ini` пока не поддерживаются:
  скрипт откажется работать, молча ничего не сломает.
- BSA не распаковываются и не нужны: движок грузит их сам, одноимённые архивы решаются тем же
  правилом «побеждает старший».
