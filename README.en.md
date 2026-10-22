[![HACS Default][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Installations][installations_shield]][releases]


[hacs_shield]: https://img.shields.io/static/v1.svg?label=HACS&message=Default&style=popout&color=green&labelColor=41bdf5&logo=HomeAssistantCommunityStore&logoColor=white
[hacs]: https://hacs.xyz/docs/default_repositories

[latest_release]: https://github.com/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus/releases/latest
[releases_shield]: https://img.shields.io/github/release/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus.svg?style=popout

[releases]: https://github.com/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus/releases
[downloads_total_shield]: https://img.shields.io/github/downloads/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus/total

[installations_shield]: https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.tauron_amiplus.total&style=popout&color=41bdf5&label=analytics


# Tauron Amiplus+

[Wersja polska](README.pl.md)

This integration retrieves energy consumption and generation data from the unofficial [TAURON eLicznik](https://elicznik.tauron-dystrybucja.pl) API.

## Fork and acknowledgements

This repository is a community fork of the original project by [Piotr Machowski](https://github.com/PiotrMachowski).

Kudos to the original author for creating and maintaining the integration, reverse-engineering the eLicznik API, and providing the initial Home Assistant implementation. This fork was created because the original project was no longer receiving updates required for current Home Assistant versions and changes in the TAURON service.

The fork keeps the original project’s credit and license. It is not affiliated with or endorsed by TAURON.

## How it works

The integration logs in to eLicznik and periodically reads the current meter data. Depending on the selected options, it also reads daily, monthly, yearly, and hourly data.

Statistics are handled separately from the current-value sensors:

- Home Assistant Recorder is the primary source for already stored long-term statistics.
- The integration queries the TAURON API only for the missing or recent range needed to extend the statistics.
- Daily, monthly and yearly values (with per-zone split) are calculated from Recorder statistics. Every refresh the TAURON API is asked only for the tariff, the meter readings (the counter state, which cannot be derived from history) and the hours that are not yet in the Recorder.
- Recorder statistics are exposed under the `tauron_importer` source and can be used by the Home Assistant Energy dashboard.
- On the first run (no statistics stored for the meter yet) the integration downloads the history automatically: **12 months, from the 1st day of the month 12 months back up to today** (e.g. on 22.10.2026 from 01.10.2025). This needs one request per day and series, so it takes several minutes; it runs in the background and the sensors are not blocked. If it is interrupted it continues from the last stored hour. Later synchronizations only add the missing hours.
- Besides the `tauron_importer` statistics, the integration keeps four raw hourly series up to date in the Recorder (`tauron_amiplus:<meter>_consum`, `_oze`, `_netto`, `_netto_oze`). They are the data source of the energy storage and the rolling 12-month sensors. They are filled by the same automatic mechanism; the former import from `tauron_raw_data.json` and the `tauron_amiplus.import_history` service were removed.
- The Recorder is the only store of historical data. The integration keeps no history of its own (no files, no `.storage` copies of readings or of the energy storage result); it asks the TAURON API only for hours that are not in the Recorder yet, and the energy storage and 12-month sensors are recalculated from Recorder statistics.
- To download a longer period or refill a gap use the `tauron_amiplus.download_statistics` service with a start date.

## Tariff detection and multi-zone tariffs (G11, G12, G12W)

The tariff is read from eLicznik on every refresh, so no configuration is needed when it changes (e.g. G11 -> G12W).

- The detected tariff is shown as the device software version (`Tariff G12W`), in the attributes of the energy storage sensor and in the log (`Tariff changed from ... to ...`).
- Hourly data keeps the zone of every hour. If the tariff changes inside the fetched range, zones of the previous tariff are stored separately (e.g. `G11_1` = `Cała doba (T1) [G11]`), so the whole-day G11 zone is never mixed with the peak zone of G12W.
- Monthly and yearly zone totals are rebuilt from the per-day rows returned by eLicznik for the same reason (the API itself sums zone numbers across tariffs).
- Hourly statistics get per-zone variants (`..._consumption_zone_1`, `..._consumption_zone_2`) for multi-zone tariffs. Hours recorded under another tariff count as `0` in those per-zone statistics; the total statistic always contains everything.
- The energy storage ("magazyn") does not depend on the tariff: it always sums total energy.

### Zone sensors (multi-zone tariffs only)

Created automatically as soon as the detected tariff has more than one zone; not created for G11:

| Sensor | Description |
|---|---|
| `monthly energy consumption zone <n>` | Consumption in the current month in zone `<n>` of the current tariff (kWh). The zone name is in the `zone` attribute. Only days of the current tariff are counted. |
| `monthly off-peak consumption share` | Share (%) of the current-tariff consumption in the cheaper zone (the zone with the highest number, e.g. `T2` in G12W). Attributes: `cheaper_zone`, `total_in_tariff`. |

These sensors reset every month and are meant for cards and automations. For the Energy dashboard use the hourly `tauron_importer` statistics instead (add either the total `..._consumption`, or the per-zone ones, but not both).

## Configuration

The integration can be configured through the Home Assistant UI or, for legacy installations, through YAML. The UI config flow is recommended.

### Config flow (recommended)

To configure this integration go to: _Settings_ -> _Devices & services_ -> _Add integration_ -> _Tauron Amiplus+_.

The setup consists of:

1. TAURON eLicznik username and password.
2. Meter selection from the meters available in the account.
3. Optional sensors and statistics settings.

The main options are:

| Option | Description |
|---|---|
| Generation sensors | Adds sensors for energy produced by the installation. |
| Last 12 months sensors | Adds rolling 12-month consumption, generation, and balance sensors. |
| Balanced sensors | Adds balance sensors comparing consumption with generation. |
| Balanced yearly sensors | Adds yearly balance data. |
| Configurable sensors | Adds a sensor for a user-selected historical start date. This option can require more API requests. |
| Store statistics | Stores hourly data in Home Assistant Recorder statistics under `tauron_importer`. Enable this when using the Energy dashboard or long-term history. |
| Settlement factor | Factor used by the virtual energy storage and balancing calculations. The value must be between `0` and `1`; the default is `0.8`. |
| Energy storage reset date | Optional date from which the virtual energy storage calculation starts. |

You can also use following [My Home Assistant](http://my.home-assistant.io/) link:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tauron_amiplus)

### Manual - yaml


<details>
<summary>Warning: yaml configuration is no longer recommended</summary>


**Warning:** Not all features are available when using yaml configuration

| Key                   | Type     | Required | Default          | Description                                                                    |
|-----------------------|----------|----------|------------------|--------------------------------------------------------------------------------|
| `name`                | `string` | `False`  | `Tauron Amiplus+` | Name of sensor                                                                 |
| `username`            | `string` | `True`   | -                | Username used to login at [*eLicznik*](https://elicznik.tauron-dystrybucja.pl) |
| `password`            | `string` | `True`   | -                | Password used to login at [*eLicznik*](https://elicznik.tauron-dystrybucja.pl) |
| `energy_meter_id`     | `string` | `True`   | -                | ID of energy meter                                                             |
| `monitored_variables` | `list`   | `True`   | -                | List of variables to monitor                                                   |

#### Possible monitored conditions

| Key                          | Description                                      |
|------------------------------|--------------------------------------------------| 
| `consumption_reading`        | Current consumption reading of a meter           |
| `consumption_daily`          | Daily energy consumption **(for previous day!)** |
| `consumption_monthly`        | Monthly energy consumption                       |
| `consumption_yearly`         | Yearly energy consumption                        |
| `consumption_last_12_months` | Total energy consumption for last 12 months      |
| `generation_reading`         | Current generation reading of a meter            |
| `generation_daily`           | Daily energy generation **(for previous day!)**  |
| `generation_monthly`         | Monthly energy generation                        |
| `generation_yearly`          | Yearly energy generation                         |
| `generation_last_12_months`  | Total energy generation for last 12 months       |
| `balanced_daily`             | Daily balance **(for previous day!)**            |
| `balanced_monthly`           | Monthly balance                                  |
| `balanced_yearly`            | Yearly balance                                   |
| `balanced_last_12_months`    | Balance for last 12 months                       |
| `balanced_configurable`      | Balance from the configured historical start date |
| `energy_storage`             | Virtual home energy storage calculated from consumption and generation |
| `amount`                     | Current amount/payment information when available |

#### Example usage

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

## Sensors

All energy sensors use `kWh` and are created with a name containing the configured meter name. Exact entity IDs depend on the meter ID and the selected options.

### Consumption and generation

- **Current reading**: the latest cumulative meter reading returned by TAURON.
- **Daily energy**: energy for the previous completed day. The previous-day behavior is intentional because the current day may still be incomplete.
- **Monthly energy**: energy accumulated in the current calendar month.
- **Yearly energy**: energy accumulated in the current calendar year.
- **Last 12 months energy**: rolling total covering the previous 12 months.

The same groups are available for consumption and, when generation is enabled, for energy generation.

### Balance sensors

Balance sensors compare consumption and generation for the selected period. Positive and negative values indicate the direction of the balance according to the data returned by TAURON. Daily, monthly, yearly, rolling 12-month, and configurable-period variants are available depending on the enabled options.

### Virtual energy storage

The virtual energy storage sensor estimates available stored energy using generation credits and consumption. It uses Recorder statistics rather than re-downloading the complete history on every update. The settlement factor and reset date affect this calculation.

### Recorder statistics

Hourly statistics (always on, the **Store statistics** option no longer switches them off since the sensors are built from them) are written to the `tauron_importer` source. The integration checks the latest stored Recorder point and requests only the recent or missing range from TAURON. The Home Assistant Energy dashboard should use the `tauron_importer` statistics rather than entity state sensors for hourly charts.

## Installation




### Using [HACS](https://hacs.xyz/) (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=elgatho&repository=Home-Assistant-custom-components-Tauron-AMIplusplus&category=integration)

* In _Integrations_ section add repository "Tauron Amiplus+" (this fork)
* Install added repository
 
### Manual

Download [*tauron_amiplus.zip*](https://github.com/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus/releases/latest/download/tauron_amiplus.zip) and extract its contents to `config/custom_components/tauron_amiplus` directory:
```bash
mkdir -p custom_components/tauron_amiplus
cd custom_components/tauron_amiplus
wget https://github.com/elgatho/Home-Assistant-custom-components-Tauron-AMIplusplus/releases/latest/download/tauron_amiplus.zip
unzip tauron_amiplus.zip
rm tauron_amiplus.zip
```

Then restart Home Assistant before applying configuration file changes.

## FAQ

* **How to display hourly data in Energy dashboard?**

  To show hourly data in Energy dashboard you have to use `tauron_importer` statistics instead of entities.
  With a multi-zone tariff (G12, G12W) add either the total `..._consumption` statistic, or `..._consumption_zone_1` and `..._consumption_zone_2` separately (e.g. with different prices). Do not add both, the energy would be counted twice.

* **Why there are missing days in statistics/Energy dashboard?**

  Such gaps appear when there are missing values in hourly readings for this day. The integration does not invent missing measurements; it waits until the values are available in eLicznik and then fills the missing range.
  You can confirm it using [eLicznik website](https://elicznik.tauron-dystrybucja.pl).
  
* **How to fix missing data in statistics/Energy dashboard?**

  Once the data appears on [eLicznik website](https://elicznik.tauron-dystrybucja.pl) can fill such gaps using `tauron_amiplus.download_statistics` service.

* **When does this integration update data?**

  This integration logs in and downloads data from eLicznik website every 8.5h.
  This timer is restarted after: HA restart, integration reload, configuration change.
  Additionally, a new pack of data is freshly downloaded when user retrieves diagnostics data.

* **How to calculate available energy as a prosument?**

  To calculate available energy you can use following config:
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
