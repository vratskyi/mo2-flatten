**RfaD на macOS (CrossOver) — починил зависание на главном меню / «Загрузка анимаций 0/612»**

**Симптомы:** игра стартует, SKSE грузит плагины, но дальше фон главного меню без кнопок,
или висит плашка «Loading animations 0/612», или «Новая игра» не грузится. Иногда вылет
в `refrain_nextgen.dll`.

**Причина:** .NET Script Framework (на нём Custom Skills — деревья навыков, SSE Gameplay Tweaks,
No Grass In Objects) не работает на **Wine Mono**, встроенной замене .NET в CrossOver. Главный
поток игры навсегда встаёт внутри Mono. Лечится настоящим **.NET Framework 4.8** от Microsoft.
После этого RfaD работает полностью, через обычный MO2, деревья навыков на месте.

Проверено: MacBook M1 Pro, CrossOver 26.3, RfaD SE 6.2, Skyrim 1.5.97, MO2 2.5.2.

**⚠️ Сначала сделайте копию бутылки** (CrossOver → бутылка → Duplicate). Шаги меняют её необратимо.

**0. usvfs.** Замените в папке MO2 файлы `usvfs_proxy_x64.exe`, `usvfs_proxy_x86.exe`,
`usvfs_x64.dll`, `usvfs_x86.dll` на версию 0.5.7.2:
https://github.com/ModOrganizer2/usvfs/releases/tag/v0.5.7.2
(На usvfs из комплекта MO2 2.5.2 SKSE-плагины грузились с ошибками.)

**1. Удалите Wine Mono:** CrossOver → бутылка → Run Command → `uninstaller` →
«Wine Mono Windows Support» → Remove.

**2–7.** Команды для Терминала macOS. Поменяйте `BOTTLE` на имя своей бутылки.
Нужны установщики Microsoft: [.NET 4.8 офлайн](https://dotnet.microsoft.com/download/dotnet-framework/net48)
(`ndp48-x86-x64-allos-enu.exe`) и [.NET 4.0](https://download.microsoft.com/download/9/5/A/95A9616B-7A37-4AF6-BC36-D6EA96C8DAAE/dotNetFx40_Full_x86_x64.exe)
(`dotNetFx40_Full_x86_x64.exe`) — положите оба в «Загрузки». Ещё нужен 7z: `brew install p7zip`.

```bash
BOTTLE="Games"
WINE="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"
CAB="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cabextract"
WIN="$HOME/Library/Application Support/CrossOver/Bottles/$BOTTLE/drive_c/windows"
w() { "$WINE" --bottle "$BOTTLE" --cx-app "$@"; }

# 2. Временно Windows 7: на Win10/11 установщик .NET 4.8 блокируется ("already installed")
w winecfg -v win7

# 3. Ставим .NET 4.8. Предупреждения про Windows Update — Continue, дождаться "Installation Is Complete"
#    (CrossOver запускает exe только по Windows-пути, поэтому сначала копируем в C:\)
cp "$HOME/Downloads/ndp48-x86-x64-allos-enu.exe" "$WIN/../ndp48.exe"
w 'C:\ndp48.exe'

# 4. Настоящий mscoree.dll из пакета .NET 4.0 (установщик 4.8 его не кладёт,
#    а заглушка Wine умеет запускать только Mono)
cd /tmp && rm -rf net40 && mkdir net40 && cd net40
7z x -y "$HOME/Downloads/dotNetFx40_Full_x86_x64.exe" -ox > /dev/null
"$CAB" -q -F '_0??_mscoree*' -d out x/netfx_Core.mzz
cp out/_003_mscoree_dll_amd64  "$WIN/system32/mscoree.dll"
cp out/_017_mscorees_dll_amd64 "$WIN/system32/mscorees.dll"
cp out/_003_mscoree_dll_x86    "$WIN/syswow64/mscoree.dll"
cp out/_017_mscorees_dll_x86   "$WIN/syswow64/mscorees.dll"

# 5. Реестр
w reg add 'HKCU\Software\Wine\DllOverrides' /v mscoree /t REG_SZ /d native /f
w reg add 'HKLM\Software\Microsoft\.NETFramework' /v InstallRoot /t REG_SZ /d 'C:\windows\Microsoft.NET\Framework64\' /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework' /v InstallRoot /t REG_SZ /d 'C:\windows\Microsoft.NET\Framework\' /f
w reg add 'HKLM\Software\Microsoft\.NETFramework\Policy\v4.0' /v 30319 /t REG_SZ /d 30319-30319 /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework\Policy\v4.0' /v 30319 /t REG_SZ /d 30319-30319 /f
w reg add 'HKLM\Software\Microsoft\.NETFramework' /v OnlyUseLatestCLR /t REG_DWORD /d 1 /f
w reg add 'HKLM\Software\Wow6432Node\Microsoft\.NETFramework' /v OnlyUseLatestCLR /t REG_DWORD /d 1 /f

# 6. Возвращаем Windows 11 (или какая была у бутылки)
w winecfg -v win11

# 7. Проверка: должно напечатать "Microsoft .NET Framework ... version 4.8.x"
w 'C:\windows\Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe' '/?'
```

Если на шаге 7 выскочило окно «A fatal error occurred… mscorees.dll could not be loaded»,
значит, пропущен шаг 4 или 5.

После этого запускайте RfaD как обычно, через MO2 → SKSE.

*Не проверял: на свежей бутылке шаги 1–6, вероятно, заменяет `winetricks dotnet48`
(он делает то же самое), но через CrossOver я его не гонял.*
