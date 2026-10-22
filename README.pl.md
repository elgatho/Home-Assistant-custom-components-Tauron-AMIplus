[![HACS Default][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Installations][installations_shield]][releases]


[hacs_shield]: https://img.shields.io/static/v1.svg?label=HACS&message=Default&style=popout&color=green&labelColor=41bdf5&logo=HomeAssistantCommunityStore&logoColor=white
[hacs]: https://hacs.xyz/docs/default_repositories

[latest_release]: https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/releases/latest
[releases_shield]: https://img.shields.io/github/release/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus.svg?style=popout

[releases]: https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/releases
[downloads_total_shield]: https://img.shields.io/github/downloads/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/total

[installations_shield]: https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.tauron_amiplus.total&style=popout&color=41bdf5&label=analytics


# Tauron Amiplus+

[English version](README.md)

Integracja pobiera dane o zużyciu i produkcji energii z nieoficjalnego API [TAURON eLicznik](https://elicznik.tauron-dystrybucja.pl).

## Fork i podziękowania

To społecznościowy fork oryginalnego projektu [Piotra Machowskiego](https://github.com/PiotrMachowski).

Podziękowania dla autora oryginału za stworzenie i utrzymywanie integracji, rozpracowanie API eLicznika oraz pierwszą implementację dla Home Assistanta. Fork powstał, ponieważ oryginalny projekt przestał otrzymywać aktualizacje wymagane przez obecne wersje Home Assistanta i zmiany w serwisie TAURON.

Fork zachowuje informacje o autorstwie i licencję oryginału. Nie jest powiązany z TAURON ani przez niego rekomendowany.

## Jak to działa

Integracja loguje się do eLicznika i okresowo odczytuje bieżące dane licznika. W zależności od wybranych opcji pobiera też dane dzienne, miesięczne, roczne i godzinowe.

Statystyki są obsługiwane osobno niż sensory bieżących wartości:

- Rejestrator (Recorder) Home Assistanta jest głównym źródłem już zapisanych statystyk długoterminowych.
- Integracja odpytuje API TAURON tylko o brakujący lub najnowszy zakres potrzebny do uzupełnienia statystyk.
- Wartości dzienne, miesięczne i roczne (z podziałem na strefy) są liczone ze statystyk Recordera. Przy każdym odświeżeniu API TAURON jest pytane wyłącznie o taryfę, odczyty licznika (stan licznika, którego nie da się wyliczyć z historii) oraz godziny, których nie ma jeszcze w Recorderze.
- Statystyki w Recorderze są dostępne ze źródłem `tauron_importer` i można ich używać w panelu Energia Home Assistanta.
- Przy pierwszym uruchomieniu (brak statystyk dla licznika) integracja pobiera historię automatycznie: **12 miesięcy, od 1. dnia miesiąca sprzed 12 miesięcy do dziś** (np. 22.10.2026 od 01.10.2025). Wymaga to jednego zapytania na dzień i serię, więc trwa kilka minut; odbywa się w tle i nie blokuje sensorów. Przerwane pobieranie jest kontynuowane od ostatniego zapisanego punktu.
- Oprócz statystyk `tauron_importer` integracja utrzymuje w Recorderze dwie surowe serie godzinowe (`tauron_amiplus:<licznik>_netto`, `_netto_oze`). Są źródłem danych dla magazynu energii i sensorów z ostatnich 12 miesięcy. Wypełnia je ten sam automatyczny mechanizm; dawny import z pliku `tauron_raw_data.json` i usługa `tauron_amiplus.import_history` zostały usunięte.
- Recorder jest jedynym magazynem danych historycznych. Integracja nie przechowuje własnej historii (żadnych plików ani kopii odczytów czy wyniku magazynu energii w `.storage`); pyta API TAURON tylko o godziny, których nie ma jeszcze w Recorderze, a magazyn energii i sensory 12-miesięczne są przeliczane ze statystyk Recordera.
- Aby pobrać dłuższy okres lub uzupełnić lukę, użyj usługi `tauron_amiplus.download_statistics` z datą początkową.

## Wykrywanie taryfy i taryfy wielostrefowe (G11, G12, G12W)

Taryfa jest odczytywana z eLicznika przy każdym odświeżeniu, więc zmiana (np. G11 -> G12W) nie wymaga żadnej konfiguracji.

- Wykryta taryfa jest widoczna jako wersja oprogramowania urządzenia (`Tariff G12W`), w atrybutach sensora magazynu energii oraz w logu (`Tariff changed from ... to ...`).
- Dane godzinowe zachowują strefę każdej godziny. Jeśli taryfa zmienia się w pobieranym zakresie, strefy poprzedniej taryfy są przechowywane osobno (np. `G11_1` = `Cała doba (T1) [G11]`), dzięki czemu całodobowa strefa G11 nigdy nie miesza się ze strefą szczytową G12W.
- Miesięczne i roczne sumy stref są przeliczane z wierszy dziennych zwracanych przez eLicznik z tego samego powodu (samo API sumuje numery stref między taryfami).
- Statystyki godzinowe mają warianty per strefa (`..._consumption_zone_1`, `..._consumption_zone_2`) dla taryf wielostrefowych. Godziny zapisane w innej taryfie liczą się w nich jako `0`; statystyka całkowita zawsze zawiera wszystko.
- Magazyn energii nie zależy od taryfy: zawsze sumuje całą energię.

### Sensory stref (tylko taryfy wielostrefowe)

Tworzone automatycznie, gdy wykryta taryfa ma więcej niż jedną strefę; dla G11 nie są tworzone:

| Sensor | Opis |
|---|---|
| `monthly energy consumption zone <n>` | Zużycie w bieżącym miesiącu w strefie `<n>` bieżącej taryfy (kWh). Nazwa strefy jest w atrybucie `zone`. Liczone są tylko dni bieżącej taryfy. |
| `monthly off-peak consumption share` | Udział (%) zużycia w tańszej strefie w zużyciu bieżącej taryfy (strefa o najwyższym numerze, np. `T2` w G12W). Atrybuty: `cheaper_zone`, `total_in_tariff`. |

Sensory te zerują się co miesiąc i służą do kart oraz automatyzacji. W panelu Energia używaj godzinowych statystyk `tauron_importer` (dodaj albo statystykę całkowitą `..._consumption`, albo statystyki stref, ale nie obie naraz).

## Konfiguracja

Integrację można skonfigurować przez interfejs Home Assistanta lub, w starszych instalacjach, przez YAML. Zalecany jest interfejs (config flow).

### Config flow (zalecane)

Przejdź do: _Ustawienia_ -> _Urządzenia i usługi_ -> _Dodaj integrację_ -> _Tauron Amiplus+_.

Konfiguracja obejmuje:

1. Nazwę użytkownika i hasło do TAURON eLicznik.
2. Wybór licznika spośród liczników dostępnych na koncie.
3. Opcjonalne ustawienia sensorów i statystyk.

Główne opcje:

| Opcja | Opis |
|---|---|
| Generation sensors | Dodaje sensory energii wyprodukowanej przez instalację. |
| Last 12 months sensors | Dodaje sensory zużycia, produkcji i bilansu z ostatnich 12 miesięcy. |
| Balanced sensors | Dodaje sensory bilansu porównujące zużycie z produkcją. |
| Balanced yearly sensors | Dodaje dane bilansu rocznego. |
| Configurable sensors | Dodaje sensor liczony od wybranej daty historycznej. Opcja może wymagać więcej zapytań do API. |
| Store statistics | Zapisuje dane godzinowe w statystykach Recordera Home Assistanta pod źródłem `tauron_importer`. Statystyki są obecnie zawsze włączone, bo sensory są z nich budowane. |
| Settlement factor | Współczynnik używany przez wirtualny magazyn energii i obliczenia bilansu. Wartość od `0` do `1`; domyślnie `0.8`. |
| Energy storage reset date | Opcjonalna data, od której zaczyna się obliczanie wirtualnego magazynu energii. |

Możesz też użyć linku [My Home Assistant](http://my.home-assistant.io/):

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tauron_amiplus)

### Ręcznie - yaml

<details>
<summary>Uwaga: konfiguracja przez yaml nie jest już zalecana</summary>

**Uwaga:** Nie wszystkie funkcje są dostępne przy konfiguracji przez yaml

| Klucz                 | Typ      | Wymagany | Domyślnie        | Opis                                                                           |
|-----------------------|----------|----------|------------------|--------------------------------------------------------------------------------|
| `name`                | `string` | `False`  | `Tauron Amiplus+` | Nazwa sensora                                                                 |
| `username`            | `string` | `True`   | -                | Login do [*eLicznika*](https://elicznik.tauron-dystrybucja.pl)                 |
| `password`            | `string` | `True`   | -                | Hasło do [*eLicznika*](https://elicznik.tauron-dystrybucja.pl)                 |
| `energy_meter_id`     | `string` | `True`   | -                | ID licznika energii                                                            |
| `monitored_variables` | `list`   | `True`   | -                | Lista monitorowanych zmiennych                                                 |

#### Dostępne monitorowane zmienne

| Klucz                        | Opis                                                          |
|------------------------------|---------------------------------------------------------------|
| `consumption_reading`        | Aktualny odczyt licznika (pobór)                              |
| `consumption_daily`          | Dzienne zużycie energii **(za poprzedni dzień!)**             |
| `consumption_monthly`        | Miesięczne zużycie energii                                    |
| `consumption_yearly`         | Roczne zużycie energii                                        |
| `consumption_last_12_months` | Łączne zużycie energii z ostatnich 12 miesięcy                |
| `generation_reading`         | Aktualny odczyt licznika (produkcja)                          |
| `generation_daily`           | Dzienna produkcja energii **(za poprzedni dzień!)**           |
| `generation_monthly`         | Miesięczna produkcja energii                                  |
| `generation_yearly`          | Roczna produkcja energii                                      |
| `generation_last_12_months`  | Łączna produkcja energii z ostatnich 12 miesięcy              |
| `balanced_daily`             | Bilans dzienny **(za poprzedni dzień!)**                      |
| `balanced_monthly`           | Bilans miesięczny                                             |
| `balanced_yearly`            | Bilans roczny                                                 |
| `balanced_last_12_months`    | Bilans z ostatnich 12 miesięcy                                |
| `balanced_configurable`      | Bilans od skonfigurowanej daty historycznej                   |
| `energy_storage`             | Wirtualny domowy magazyn energii liczony ze zużycia i produkcji |
| `amount`                     | Aktualna kwota/informacje o płatności, jeśli dostępne         |

#### Przykład

```
sensor:
  - platform: tauron_amiplus
    name: Tauron Amiplus+
    username: !secret tauron_amiplus.username
    password: !secret tauron_amiplus.password
    energy_meter_id: !secret tauron_amiplus.energy_meter_id
    monitored_variables:
      - consumption_reading
      - consumption_daily
      - consumption_monthly
      - consumption_yearly
      - consumption_last_12_months
      - generation_reading
      - generation_daily
      - generation_monthly
      - generation_yearly
      - generation_last_12_months
      - balanced_daily
      - balanced_monthly
      - balanced_yearly
      - balanced_last_12_months
```

</details>

## Sensory

Wszystkie sensory energii używają jednostki `kWh`, a ich nazwy zawierają skonfigurowaną nazwę licznika. Dokładne identyfikatory encji zależą od ID licznika i wybranych opcji.

### Zużycie i produkcja

- **Aktualny odczyt**: ostatni skumulowany odczyt licznika zwrócony przez TAURON.
- **Energia dzienna**: energia za poprzedni zakończony dzień. Dane za poprzedni dzień są zamierzone, bo bieżący dzień może być niepełny.
- **Energia miesięczna**: energia zgromadzona w bieżącym miesiącu kalendarzowym.
- **Energia roczna**: energia zgromadzona w bieżącym roku kalendarzowym.
- **Energia z ostatnich 12 miesięcy**: kroczące sumowanie poprzednich 12 miesięcy.

Te same grupy są dostępne dla zużycia oraz, po włączeniu produkcji, dla wytwarzania energii.

### Sensory bilansu

Sensory bilansu porównują zużycie i produkcję w wybranym okresie. Wartości dodatnie i ujemne wskazują kierunek bilansu zgodnie z danymi zwróconymi przez TAURON. W zależności od włączonych opcji dostępne są warianty dzienne, miesięczne, roczne, kroczące 12-miesięczne i z konfigurowalnym okresem.

### Wirtualny magazyn energii

Sensor wirtualnego magazynu energii szacuje dostępną zmagazynowaną energię na podstawie kredytów z produkcji i zużycia. Korzysta ze statystyk Recordera, zamiast pobierać całą historię przy każdej aktualizacji. Na wynik wpływają współczynnik rozliczenia i data resetu.

### Statystyki w Recorderze

Statystyki godzinowe (zawsze włączone; opcja **Store statistics** już ich nie wyłącza, ponieważ sensory są z nich budowane) są zapisywane ze źródłem `tauron_importer`. Integracja sprawdza ostatni zapisany punkt w Recorderze i pobiera z TAURON tylko ostatni lub brakujący zakres. W panelu Energia Home Assistanta do wykresów godzinowych używaj statystyk `tauron_importer`, a nie sensorów.

## Instalacja

<table><tr><th>Od wersji v2.3.0 integracja wymaga Home Assistanta w wersji 2022.12 lub nowszej</th></tr></table>
<table><tr><th>Od wersji v2.4.8 integracja może powodować wolniejszy start Home Assistanta</th></tr></table>

### Przez [HACS](https://hacs.xyz/) (zalecane)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=PiotrMachowski&repository=Home-Assistant-custom-components-Tauron-AMIplus&category=integration)

* W sekcji _Integracje_ dodaj repozytorium „Tauron Amiplus+” (ten fork)
* Zainstaluj dodane repozytorium

### Ręcznie

Pobierz [*tauron_amiplus.zip*](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/releases/latest/download/tauron_amiplus.zip) i rozpakuj do katalogu `config/custom_components/tauron_amiplus`:
```bash
mkdir -p custom_components/tauron_amiplus
cd custom_components/tauron_amiplus
wget https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/releases/latest/download/tauron_amiplus.zip
unzip tauron_amiplus.zip
rm tauron_amiplus.zip
```

Następnie zrestartuj Home Assistanta przed zastosowaniem zmian w pliku konfiguracyjnym.

## FAQ

* **Jak wyświetlić dane godzinowe w panelu Energia?**

  Aby pokazać dane godzinowe w panelu Energia, użyj statystyk `tauron_importer` zamiast encji.
  Przy taryfie wielostrefowej (G12, G12W) dodaj albo statystykę całkowitą `..._consumption`, albo osobno `..._consumption_zone_1` i `..._consumption_zone_2` (np. z różnymi cenami). Nie dodawaj obu naraz, bo energia zostanie policzona dwukrotnie.

* **Dlaczego w statystykach/panelu Energia brakuje dni?**

  Luki pojawiają się, gdy w odczytach godzinowych z danego dnia brakuje wartości. Integracja nie zmyśla brakujących pomiarów; czeka, aż wartości pojawią się w eLicznik, a potem uzupełnia brakujący zakres.
  Możesz to sprawdzić na [stronie eLicznika](https://elicznik.tauron-dystrybucja.pl).

* **Jak naprawić brakujące dane w statystykach/panelu Energia?**

  Gdy dane pojawią się na [stronie eLicznika](https://elicznik.tauron-dystrybucja.pl), możesz uzupełnić luki usługą `tauron_amiplus.download_statistics`.

* **Kiedy integracja aktualizuje dane?**

  Integracja loguje się i pobiera dane z eLicznika co 8,5 h.
  Licznik jest restartowany po: restarcie HA, przeładowaniu integracji i zmianie konfiguracji.
  Dodatkowo nowa paczka danych jest pobierana, gdy użytkownik pobiera dane diagnostyczne.

* **Jak znaleźć ID licznika energii?**

  Wartość `energy_meter_id` znajdziesz, postępując według [tych kroków](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/issues/105#issuecomment-1413675239).

* **Jak obliczyć dostępną energię jako prosument?**

  Do obliczenia dostępnej energii możesz użyć następującej konfiguracji:
  ```yaml
  input_number:
    initial_energy_bank:
      min: 0
      max: 100000000
      step: 1
      mode: box
  template:
    - sensor:
        - name: Tauron energy bank
          state_class: total
          device_class: energy
          unique_id: tauron_energy_bank
          icon: mdi:home-battery-outline
          state: "{{ (states('input_number.initial_energy_bank') | float(0) + states('sensor.tauron_amiplus_123_yearly_energy_generation') | float(0) * 0.8 - states('sensor.tauron_amiplus_123_yearly_energy_consumption') | float(0)) | round(3) }}"
          unit_of_measurement: "kWh"
          availability: "{{ states('sensor.tauron_amiplus_123_yearly_energy_generation') | is_number and states('sensor.tauron_amiplus_123_yearly_energy_consumption') | is_number }}"
  ```
